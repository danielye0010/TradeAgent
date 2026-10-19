"""daily-news-claude-v1: Claude answers the daily-news-v2 question; never execution.

A read-only companion of ``daily-news-v2``. Each session it takes the shortlist that v2
froze, asks Claude (Claude Code CLI on a Claude subscription) the same question the v2
Codex assessors answer, with the same prompt, schema, deadline and search switch, and
scores Claude's picks over v2's own entry and exit window and modeled costs. The v2
research, its decisions and anything that follows them are never written or changed.

Fairness rules, frozen in the deployment config:

- Claude starts from the scan v2 saved, at most ``max_start_lag_seconds`` after it, and
  has the same per-call deadline as Codex.
- An answer frozen after the v2 decision's ``entry_after`` cannot be scored (it could
  have seen later news) and is recorded as UNAVAILABLE, like any missing answer.
- Missing evidence stays missing: there is no rerun, no later session and no backfill.
"""

import argparse
import json
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from ..opportunity_cli import write
from ..research.domain import identity
from ..research.opportunity_workflow import DailyResearch, research_support
from . import claude_assessor
from .daily import NEWS_CONFIG, NEWS_PROTOCOL, cited, paired, residual, window

NY = ZoneInfo("America/New_York")
PROTOCOL = "daily-news-claude-v1"
CLAUDE_ARMS = ("claude_news", "claude_price", "quant_claude_news")
CODEX_ARMS = ("codex_news", "codex_price", "quant_codex_news")
SHARED_ARMS = ("quant_only", "seeded_random")
ARMS = CLAUDE_ARMS + CODEX_ARMS + SHARED_ARMS
# Each Claude arm is scored against the Codex arm that answered the same question.
COUNTERPART = dict(zip(CLAUDE_ARMS, CODEX_ARMS, strict=True))
BASELINES = ("quant_only", "seeded_random")
START_LAG_SECONDS = 60
POLL_SECONDS = 2
# v2 freezes before its window ends; allow it a moment to commit.
DECISION_GRACE_SECONDS = 60
CONFIG = {
    "protocol": PROTOCOL,
    "follows": NEWS_PROTOCOL,
    "decision_window_ny": NEWS_CONFIG["decision_window_ny"],
    "holding_seconds": NEWS_CONFIG["holding_seconds"],
    "candidates": NEWS_CONFIG["candidates"],
    "shortlist": "the scan daily-news-v2 froze for the session; same candidates and evidence",
    "assessor": "the daily-news-v2 prompt and schema; only the model and CLI differ",
    "assessor_timeout_seconds": NEWS_CONFIG["assessor_timeout_seconds"],
    "assessors_run": "concurrently",
    "max_start_lag_seconds": START_LAG_SECONDS,
    "claude_model": claude_assessor.MODEL,
    "claude_effort": claude_assessor.EFFORT,
    "claude_tools": {"news": claude_assessor.SEARCH_TOOL, "price": "none"},
    "claude_login": "Claude subscription (claude.ai login or setup-token); never an API key",
    "codex_model": NEWS_CONFIG["codex_model"],
    "scoring": "the v2 decision's entry/exit endpoints and modeled candidate outcomes",
    "late_answer": "frozen after the v2 entry_after: UNAVAILABLE",
    "arms": list(ARMS),
    "mode": "SHADOW",
}


class Follow:
    """Read-only view of the daily-news-v2 state directory."""

    def __init__(self, directory):
        self.directory = Path(directory).resolve()
        deployment = self.directory / "daily-deployment.json"
        try:
            config = json.loads(deployment.read_text())["config"]
        except (OSError, ValueError, KeyError):
            raise ValueError(f"no daily-news-v2 research deployment in {self.directory}") from None
        if config.get("protocol") != NEWS_PROTOCOL:
            raise ValueError(f"{self.directory} is not a daily-news-v2 state directory")
        self.config = config

    def query(self, sql, values=()):
        path = self.directory / "experience.sqlite3"
        if not path.is_file():
            return []
        try:
            with closing(sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True, timeout=5)) as db:
                db.execute("PRAGMA query_only=ON")
                return [json.loads(row[0]) for row in db.execute(sql, values)]
        except (sqlite3.Error, ValueError):
            return []  # Busy or mid-write: the caller polls again or leaves it pending.

    def scan(self, start, end):
        rows = self.query(
            "SELECT payload FROM opportunity_scans WHERE recorded_at >= ? AND recorded_at < ? "
            "ORDER BY recorded_at, id",
            (start, end),
        )
        return rows[-1] if rows else None

    def decision(self, scan_id, since):
        rows = self.query(
            "SELECT payload FROM opportunity_decisions WHERE recorded_at >= ? ORDER BY recorded_at",
            (since,),
        )
        return next(
            (
                d
                for d in rows
                if d.get("scan_id") == scan_id and d.get("research_protocol") == NEWS_PROTOCOL
            ),
            None,
        )

    def record(self, kind, key):
        rows = self.query(f"SELECT payload FROM opportunity_{kind} WHERE id=?", (key,))
        return rows[0] if rows else None


def selections(decision, assessments, statuses):
    """Claude's arms, chosen exactly as v2 chooses its Codex arms from the same candidates."""
    eligible = [
        c for c in decision["ranked_candidates"] if c["predictions"] and not c["limitations"]
    ]
    if sorted(c["symbol"] for c in eligible) != sorted(decision["comparison_universe"]):
        raise ValueError("v2 comparison universe does not match its frozen candidates")
    signals = {
        c["symbol"]: max(
            (p["expected_return"] for p in c["predictions"] if p["direction"] == 1),
            default=0.0,
        )
        for c in eligible
    }
    now = decision["decision_time"]
    arms = {}
    for tag, name, combined in (
        ("price", "claude_price", None),
        ("news", "claude_news", "quant_claude_news"),
    ):
        own = assessments[tag]
        support = {
            c["symbol"]: research_support(c["scanner"], own.get(c["symbol"]), now)["supported"]
            for c in eligible
        }
        chosen = min(
            (c for c in eligible if support[c["symbol"]]),
            key=lambda c: (own[c["symbol"]]["rank"], c["symbol"]),
            default=None,
        )
        both = min(
            (c for c in eligible if support[c["symbol"]] and signals[c["symbol"]] > 0),
            key=lambda c: (own[c["symbol"]]["rank"], -signals[c["symbol"]], c["symbol"]),
            default=None,
        )
        for arm, pick in ((name, chosen), (combined, both)):
            if arm is None:
                continue
            valid = statuses[tag] == "VALID"
            arms[arm] = {
                "symbol": pick["symbol"] if pick and valid else None,
                "decision": "SHADOW_LONG" if pick and valid else "NO_TRADE",
                "plan": None,
                "research_only": True,
                "shadow_direction": int(bool(pick and valid)),
                "status": "VALID" if valid else "UNAVAILABLE",
                "reason": "frozen research selection; no proven profitability"
                if valid
                else "bounded assessment unavailable",
            }
    for name in CODEX_ARMS + SHARED_ARMS:
        arms[name] = {**decision["comparisons"][name], "source": NEWS_PROTOCOL}
    return {name: arms[name] for name in ARMS}


def _mean(values):
    return sum(values) / len(values) if values else None


def _difference(rows, arm, other):
    """Paired excess difference over the sessions where both arms have an outcome."""
    both = [
        o
        for o in rows
        if o["comparisons"].get(arm) is not None and o["comparisons"].get(other) is not None
    ]
    return paired(
        [residual(o["comparisons"][arm]) - residual(o["comparisons"][other]) for o in both]
    )


def compare(store):
    """Paired daily comparison; one session is one observation, cash counts as zero."""
    decisions = {
        d["decision_id"]: d
        for d in store.records("decisions")
        if d.get("research_protocol") == PROTOCOL
    }
    terminal = {r["decision_id"] for r in store.records("resolution_failures")}
    outcomes = {
        o["decision_id"]: o
        for o in store.records("outcomes")
        if o["decision_id"] in decisions and o["decision_id"] not in terminal
    }
    pools = {}
    for pool in sorted({d["evidence_kind"] for d in decisions.values()}):
        ds = [d for d in decisions.values() if d["evidence_kind"] == pool]
        rows = [outcomes[d["decision_id"]] for d in ds if d["decision_id"] in outcomes]
        complete = [o for o in rows if all(o["comparisons"].get(n) is not None for n in ARMS)]

        stats = {
            n: {
                "observed_days": sum(o["comparisons"].get(n) is not None for o in rows),
                "selected_days": sum(bool(d["comparisons"][n]["shadow_direction"]) for d in ds),
                "unavailable_days": sum(d["comparisons"][n]["status"] == "UNAVAILABLE" for d in ds),
                "paired_mean_base_net": _mean([o["comparisons"][n]["base_net"] for o in complete]),
                "paired_mean_stress_net": _mean(
                    [o["comparisons"][n]["stress_net"] for o in complete]
                ),
                "paired_excess_over_benchmark": paired(
                    [residual(o["comparisons"][n]) for o in complete]
                ),
            }
            for n in ARMS
        }
        differences = {
            n: {
                b: _difference(rows, n, b)
                for b in ((COUNTERPART[n],) if n in COUNTERPART else ()) + BASELINES
                if b != n
            }
            for n in ARMS
        }
        agreement = {}
        for arm, other in COUNTERPART.items():
            both = [
                d
                for d in ds
                if d["comparisons"][arm]["status"] == "VALID"
                and d["comparisons"][other]["status"] == "VALID"
            ]
            agreement[arm] = {
                "valid_days": len(both),
                "same_pick_days": sum(
                    d["comparisons"][arm]["symbol"] == d["comparisons"][other]["symbol"]
                    for d in both
                ),
            }
        pools[pool] = {
            "frozen_sessions": len(ds),
            "resolved_sessions": len(rows),
            "valid_paired_days": len(complete),
            "arms": stats,
            "paired_excess_differences": differences,
            "same_pick_as_codex": agreement,
        }
    return {
        "protocol": PROTOCOL,
        "evidence_pools": pools,
        "orders_submitted": 0,
        "interpretation": "modeled returns over the v2 window with v2's frozen costs; cash is "
        "zero; excess = arm net return minus the benchmark; a Claude-minus-Codex difference "
        "uses the sessions where both answered; no actual fills or established uplift",
    }


class Companion:
    def __init__(
        self,
        directory,
        follow_dir,
        *,
        assessor=claude_assessor.assess_news,
        clock=time.time,
        sleep=time.sleep,
    ):
        self.directory = Path(directory)
        self.follow = Follow(follow_dir)
        self.store = DailyResearch(self.directory)
        self.assessor, self.clock, self.sleep = assessor, clock, sleep

    def close(self):
        self.store.close()

    def event(self, day, phase, **values):
        item = {
            "session_date": day.isoformat(),
            "phase": phase,
            "recorded_at": self.clock(),
            "protocol": PROTOCOL,
            "orders_submitted": 0,
            **values,
        }
        key = identity([PROTOCOL, day.isoformat(), phase])
        self.store.save("sessions", item, key, item["recorded_at"])
        write(self.directory / "sessions" / (key + ".json"), item)
        return item

    def freeze(self, report, call, tag):
        """Freeze one bounded Claude answer, or record it unavailable; never partial."""
        try:
            # Each answer keeps the time its own call returned, not when it is stored.
            (document, provenance), frozen = call.result()
            with self.store.db:
                for item in document["assessments"]:
                    self.store.assessment(
                        {
                            **item,
                            "scan_id": report["scan_id"],
                            "observed_at": frozen,
                            "sources": cited(item.get("sources", []), frozen),
                            "hypothesis_type": item.get("hypothesis_type", "price_action"),
                            "model": provenance["model"],
                            "assessor": tag,
                        },
                        frozen,
                    )
            return {
                "status": "VALID",
                "error": None,
                "frozen_at": frozen,
                "provenance": provenance,
                "assessments": {i["symbol"]: i for i in document["assessments"]},
            }
        except Exception as error:
            return {
                "status": "UNAVAILABLE",
                "error": type(error).__name__,
                # Only this package's own fixed messages; never model output or CLI text.
                "reason": str(error)[:200] if type(error) is ValueError else None,
                "frozen_at": None,
                "provenance": {},
                "assessments": {},
            }

    def await_value(self, read, deadline):
        while True:
            value = read()
            if value is not None or self.clock() >= deadline:
                return value
            self.sleep(POLL_SECONDS)

    def session(self, day, target):
        report = self.await_value(lambda: self.follow.scan(*target), target[1])
        if report is None:
            return self.event(
                day,
                "FINAL",
                status="INCOMPLETE",
                decision_id=None,
                failure_kind="V2_SCAN_UNAVAILABLE",
                reason="daily-news-v2 saved no scan in its decision window",
            )
        started = self.clock()
        lag = started - report["decision_time"]
        if lag > START_LAG_SECONDS:
            return self.event(
                day,
                "FINAL",
                status="INCOMPLETE",
                decision_id=None,
                failure_kind="LATE_START",
                reason=f"started {lag:.0f}s after the v2 scan; the limit is {START_LAG_SECONDS}s",
            )
        self.store.save("scans", report, report["scan_id"], report["decision_time"])

        def timed(search):
            answer = self.assessor(report, now=started, search=search)
            return answer, self.clock()

        with ThreadPoolExecutor(max_workers=2) as pool:
            calls = {tag: pool.submit(timed, tag == "news") for tag in ("price", "news")}
            answers = {tag: self.freeze(report, call, tag) for tag, call in calls.items()}
        decision = self.await_value(
            lambda: self.follow.decision(report["scan_id"], report["decision_time"]),
            target[1] + DECISION_GRACE_SECONDS,
        )
        claude = {
            tag: {k: v for k, v in a.items() if k != "assessments"} | {"started_at": started}
            for tag, a in answers.items()
        }
        if decision is None:
            return self.event(
                day,
                "FINAL",
                status="INCOMPLETE",
                decision_id=None,
                failure_kind="V2_DECISION_UNAVAILABLE",
                reason="daily-news-v2 froze no decision for its scan",
                claude=claude,
            )
        statuses = {}
        for tag, answer in answers.items():
            statuses[tag] = answer["status"]
            limit = decision.get("entry_after")
            if answer["status"] == "VALID" and (limit is None or answer["frozen_at"] > limit):
                statuses[tag] = "UNAVAILABLE"
                claude[tag]["late"] = True
        arms = selections(decision, {t: a["assessments"] for t, a in answers.items()}, statuses)
        result = {
            "schema_version": 1,
            "research_protocol": PROTOCOL,
            "follows": {
                "protocol": NEWS_PROTOCOL,
                "decision_id": decision["decision_id"],
                "directory": str(self.follow.directory),
            },
            "scan_id": report["scan_id"],
            "skill_version": decision.get("skill_version"),
            "session_open": decision["session_open"],
            "session_close": decision["session_close"],
            "decision_time": decision["decision_time"],
            "freeze_time": self.clock(),
            "source": decision["source"],
            "evidence_kind": decision["evidence_kind"],
            "holding_seconds": decision["holding_seconds"],
            "research_window": decision["research_window"],
            "entry_after": decision["entry_after"],
            "entry_deadline": decision["entry_deadline"],
            "exit_at": decision["exit_at"],
            "outcome_entry_time": decision["outcome_entry_time"],
            "outcome_exit_time": decision["outcome_exit_time"],
            "comparison_universe": decision["comparison_universe"],
            "ranked_candidates": [
                {"symbol": c["symbol"], "scanner": c["scanner"]}
                for c in decision["ranked_candidates"]
            ],
            "claude": claude,
            "assessment_status": statuses["price"],
            "news_assessment_status": statuses["news"],
            "comparisons": arms,
            "final_decision": "NO_TRADE",
            "selected_plan": None,
            "decision_status": "SHADOW_ONLY",
            "orders_submitted": 0,
        }
        result["decision_id"] = identity(result)
        self.store.save("decisions", result, result["decision_id"], result["freeze_time"])
        write(self.directory / "decisions" / (result["decision_id"] + ".json"), result)
        complete = all(s == "VALID" for s in statuses.values())
        return self.event(
            day,
            "FINAL",
            status="COMPLETED" if complete else "INCOMPLETE",
            failure_kind=None if complete else "CLAUDE_ASSESSMENT_UNAVAILABLE",
            decision_id=result["decision_id"],
            claude=claude,
        )

    def resolve(self):
        """Score frozen decisions from v2's own outcomes; never estimate a missing one."""
        done = {o["decision_id"] for o in self.store.records("outcomes")}
        done |= {r["decision_id"] for r in self.store.records("resolution_failures")}
        resolved = []
        for d in self.store.records("decisions"):
            if d.get("research_protocol") != PROTOCOL or d["decision_id"] in done:
                continue
            source = d["follows"]["decision_id"]
            failure = self.follow.record("resolution_failures", source)
            if failure is not None:
                item = {**failure, "decision_id": d["decision_id"], "follows": source}
                self.store.save("resolution_failures", item, d["decision_id"], self.clock())
                continue
            outcome = self.follow.record("outcomes", source)
            if outcome is None:
                continue
            observed = outcome["candidate_outcomes"]
            arms = {}
            for name, arm in d["comparisons"].items():
                if name not in CLAUDE_ARMS:
                    arms[name] = outcome["comparisons"].get(name)
                elif arm["status"] == "UNAVAILABLE":
                    arms[name] = None
                elif arm["shadow_direction"]:
                    arms[name] = observed.get(arm["symbol"])
                else:
                    arms[name] = {"gross": 0, "base_net": 0, "stress_net": 0}
            picked = {a["symbol"] for a in d["comparisons"].values() if a["shadow_direction"]}
            if any(
                d["comparisons"][n]["shadow_direction"] and arms[n] is None
                for n in CLAUDE_ARMS
                if d["comparisons"][n]["status"] == "VALID"
            ):
                continue  # The v2 outcome lacks this symbol; leave it pending, never guess.
            item = {
                "decision_id": d["decision_id"],
                "resolved_at": self.clock(),
                "follows": source,
                "candidate_outcomes": {s: observed[s] for s in sorted(picked) if s in observed},
                "comparisons": arms,
                "evidence_kind": d["evidence_kind"],
                "source": d["source"],
            }
            self.store.save("outcomes", item, d["decision_id"], item["resolved_at"])
            resolved.append(d["decision_id"])
        return resolved

    def tick(self):
        now = self.clock()
        day = datetime.fromtimestamp(now, NY).date()
        self.directory.mkdir(parents=True, exist_ok=True)
        with self.store.experience.lock():
            config = {
                **CONFIG,
                "follow_dir": str(self.follow.directory),
                "provider": self.follow.config.get("provider"),
                "evidence_kind": self.follow.config.get("evidence_kind"),
            }
            deployment = self.directory / "daily-deployment.json"
            if deployment.exists():
                enrolled = json.loads(deployment.read_text())
                if enrolled["config"] != config:
                    raise ValueError("frozen Claude comparison deployment differs")
            else:
                enrolled = {"config": config, "first_session_date": day.isoformat()}
                write(deployment, enrolled)
            records = self.store.records("sessions")
            finals = {r["session_date"] for r in records if r.get("phase") == "FINAL"}
            past = date.fromisoformat(enrolled["first_session_date"])
            while past < day:
                if window(past, NEWS_PROTOCOL) and past.isoformat() not in finals:
                    self.event(
                        past,
                        "FINAL",
                        status="INCOMPLETE",
                        failure_kind="MISSED_DECISION_WINDOW",
                        decision_id=None,
                    )
                past += timedelta(days=1)
            today = [r for r in records if r.get("session_date") == day.isoformat()]
            started = any(r.get("phase") == "STARTED" for r in today)
            final = next((r for r in today if r.get("phase") == "FINAL"), None)
            target = window(day, NEWS_PROTOCOL)
            result = final or {"status": "IDLE", "session_date": day.isoformat()}
            if started and not final:
                # Crash recovery never reruns inference.
                saved = [
                    d
                    for d in self.store.records("decisions")
                    if datetime.fromtimestamp(d["session_open"], NY).date() == day
                ]
                result = self.event(
                    day,
                    "FINAL",
                    status="COMPLETED" if saved else "INCOMPLETE",
                    failure_kind=None if saved else "INTERRUPTED",
                    decision_id=saved[0]["decision_id"] if saved else None,
                )
            elif not started and not final and target and now >= target[0]:
                if now >= target[1]:
                    result = self.event(
                        day,
                        "FINAL",
                        status="INCOMPLETE",
                        failure_kind="MISSED_DECISION_WINDOW",
                        decision_id=None,
                    )
                else:
                    self.event(day, "STARTED", status="STARTED")
                    result = self.session(day, target)
            resolved = self.resolve()
            comparison = compare(self.store)
            # An outcome or a terminal failure settles a session; neither is pending.
            settled = {o["decision_id"] for o in self.store.records("outcomes")} | {
                r["decision_id"] for r in self.store.records("resolution_failures")
            }
            status = {
                "mode": "SHADOW",
                "protocol": PROTOCOL,
                "follows": NEWS_PROTOCOL,
                "claude_model": claude_assessor.MODEL,
                "codex_model": NEWS_CONFIG["codex_model"],
                "run": result,
                "resolved": resolved,
                "comparison": comparison,
                "pending_sessions": [
                    d["decision_id"]
                    for d in self.store.records("decisions")
                    if d["decision_id"] not in settled
                ],
                "orders_submitted": 0,
                "tick_completed_at": self.clock(),
            }
            write(self.directory / "daily-status.json", status)
            (self.directory / "daily-report.md").write_text(report(comparison), encoding="utf-8")
            return status


NAMES = {
    "claude_news": "Claude + news",
    "claude_price": "Claude, price only",
    "quant_claude_news": "Quant + Claude + news",
    "codex_news": "Codex + news",
    "codex_price": "Codex, price only",
    "quant_codex_news": "Quant + Codex + news",
    "quant_only": "Quant",
    "seeded_random": "Random",
}


def _pct(value):
    return "n/a" if value is None else f"{value * 100:+.2f}%"


def report(comparison):
    lines = [
        "# Claude vs Codex (daily-news-claude-v1)",
        "",
        "Shadow research only: no orders. Same daily-news-v2 shortlist, prompt, window and",
        "costs; only the model differs. Returns are modeled, not fills.",
        "",
    ]
    for pool, value in comparison["evidence_pools"].items():
        lines += [
            f"## {pool}: {value['valid_paired_days']} complete paired sessions "
            f"({value['frozen_sessions']} frozen, {value['resolved_sessions']} resolved)",
            "",
            "| Arm | Picks | Mean net | Excess vs benchmark | vs Codex counterpart |",
            "| --- | ---: | ---: | ---: | ---: |",
        ]
        for name in ARMS:
            arm = value["arms"][name]
            excess = arm["paired_excess_over_benchmark"]
            versus = value["paired_excess_differences"][name].get(COUNTERPART.get(name, ""), {})
            lines.append(
                f"| {NAMES[name]} | {arm['selected_days']} | {_pct(arm['paired_mean_base_net'])} "
                f"| {_pct(excess['mean'])} | "
                + (f"{_pct(versus['mean'])} over {versus['days']} days" if versus else "")
                + " |"
            )
        lines.append("")
        for arm, same in value["same_pick_as_codex"].items():
            lines.append(
                f"- {NAMES[arm]} picked the same as {NAMES[COUNTERPART[arm]]} on "
                f"{same['same_pick_days']} of {same['valid_days']} sessions where both answered."
            )
        lines.append("")
    lines.append(
        "A difference needs many sessions before it means anything; treat early numbers as noise."
    )
    return "\n".join(lines) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument(
        "--follow-dir", type=Path, required=True, help="the daily-news-v2 --state-dir"
    )
    args = parser.parse_args(argv)
    companion = Companion(args.state_dir, args.follow_dir)
    try:
        print(json.dumps(companion.tick(), allow_nan=False))
    finally:
        companion.close()


if __name__ == "__main__":
    main()
