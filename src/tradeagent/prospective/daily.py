"""One scheduled opportunity experiment per XNYS session; never execution."""

import argparse
import json
import time
from dataclasses import asdict
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from ..calendar import session_bounds
from ..model import Halt
from ..opportunity_cli import write
from ..research.alpha_signals import ALPHA_VERSION
from ..research.domain import identity
from ..research.opportunities import UNIVERSE, capture, research_snapshot, scan
from ..research.opportunity_workflow import DailyResearch
from ..research.tradeplan import COSTS
from .assessor import MODEL, assess

NY = ZoneInfo("America/New_York")
PROTOCOL = "daily-four-arm-v1"
# Frozen before outcomes. No fitting, economic gate changes or automatic promotion.
CONFIG = {
    "protocol": PROTOCOL,
    "decision_window_ny": "10:00:00-10:03:00",
    "universe": UNIVERSE,
    "candidates": 3,
    "holding_seconds": 3600,
    "delay_seconds": 300,
    "codex_timeout_seconds": 60,
    "random_seed": "random-seed-2026-10-09",
    "mode": "SHADOW",
    "alpha_version": ALPHA_VERSION,
    "codex_model": MODEL,
    "costs": {name: asdict(cost) for name, cost in COSTS.items()},
}


def window(day):
    bounds = session_bounds(day)
    if not bounds:
        return None
    start = datetime(day.year, day.month, day.day, 10, tzinfo=NY).timestamp()
    # Early closes use the unchanged horizon, not a shortened forecast.
    return (start, start + 180) if start + 180 + 3900 + 60 <= bounds[1] else None


def compare(store):
    decisions = {
        d["decision_id"]: d
        for d in store.records("decisions")
        if d.get("research_protocol") == PROTOCOL
    }
    terminal = {r["decision_id"] for r in store.records("resolution_failures")}
    pools = {}
    names = ("quant_only", "codex_only", "quant_codex", "seeded_random")
    for pool in sorted({d["evidence_kind"] for d in decisions.values()}):
        ds = [d for d in decisions.values() if d["evidence_kind"] == pool]
        outcomes = [
            o
            for o in store.records("outcomes")
            if o["decision_id"] in decisions
            and o["evidence_kind"] == pool
            and o["decision_id"] not in terminal
        ]
        # A single daily session, not a trade/symbol vote. Missing Agent evidence
        # is not cash and is excluded from the paired comparison only.
        paired = [o for o in outcomes if all(o["comparisons"].get(n) is not None for n in names)]
        days = {decisions[o["decision_id"]]["session_open"] for o in paired}
        if len(days) != len(paired):
            raise ValueError("duplicate scheduled session in paired research")

        def mean(values):
            return sum(values) / len(values) if values else None

        stats = {
            n: {
                "observed_days": sum(o["comparisons"].get(n) is not None for o in outcomes),
                "selected_days": sum(bool(d["comparisons"][n]["shadow_direction"]) for d in ds),
                "unavailable_days": sum(d["comparisons"][n]["status"] == "UNAVAILABLE" for d in ds),
                "paired_mean_base_net": mean([o["comparisons"][n]["base_net"] for o in paired]),
                "paired_mean_stress_net": mean([o["comparisons"][n]["stress_net"] for o in paired]),
            }
            for n in names
        }
        differences = {
            n: {
                baseline: mean(
                    [
                        o["comparisons"][n]["base_net"] - o["comparisons"][baseline]["base_net"]
                        for o in paired
                    ]
                )
                for baseline in ("quant_only", "seeded_random")
            }
            for n in names
        }
        full = [
            r
            for r in store.records("symbol_outcomes")
            if r["decision_id"] in {d["decision_id"] for d in ds}
        ]
        pools[pool] = {
            "frozen_sessions": len(ds),
            "resolved_sessions": len(outcomes),
            "valid_paired_days": len(days),
            "arms": stats,
            "paired_base_net_differences": differences,
            "resolved_symbol_counterfactuals": len(full),
            "eligible_symbol_forecasts": sum(
                bool(c["predictions"]) for d in ds for c in d["quantitative_observations"]
            ),
        }
    return {
        "protocol": PROTOCOL,
        "evidence_pools": pools,
        "orders_submitted": 0,
        "interpretation": "modeled returns with frozen spread/slippage; cash abstentions retained; no actual fills or established uplift",
    }


class Pipeline:
    def __init__(
        self,
        directory,
        *,
        provider="robinhood",
        oauth_helper=None,
        collector=capture,
        assessor=assess,
        clock=time.time,
        evidence_kind="prospective",
    ):
        self.directory = Path(directory)
        self.store = DailyResearch(self.directory)
        self.provider, self.helper = provider, oauth_helper
        self.collector, self.assessor, self.clock = collector, assessor, clock
        if evidence_kind not in {"prospective", "synthetic"}:
            raise ValueError("unknown scheduled evidence pool")
        self.evidence_kind = evidence_kind

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

    def resolve(self):
        results = []
        for request in self.store.pending_sessions(self.clock(), evidence_kind=self.evidence_kind):
            if request["source"] != self.provider + "-opportunity-minute-v1":
                continue
            try:
                data = self.collector(
                    request["symbols"],
                    self.provider,
                    self.helper,
                    session=date.fromisoformat(request["session_date"]),
                )
                write(self.directory / "captures" / (identity(data) + ".json"), data)
                results.append(self.store.resolve(data, self.clock()))
            except (Halt, ValueError, OSError, KeyError, TypeError) as error:
                # Original forecasts remain pending, not terminal session failures.
                item = {
                    "failure_kind": "MISSING_MARKET_DATA",
                    "request": request,
                    "recorded_at": self.clock(),
                    "reason": str(error),
                    "orders_submitted": 0,
                }
                self.store.save("sessions", item)
                results.append(item)
        return results

    def tick(self):
        now = self.clock()
        day = datetime.fromtimestamp(now, NY).date()
        self.directory.mkdir(parents=True, exist_ok=True)
        with self.store.experience.lock():
            deployment = self.directory / "daily-deployment.json"
            config = {**CONFIG, "provider": self.provider, "evidence_kind": self.evidence_kind}
            if deployment.exists():
                enrolled = json.loads(deployment.read_text())
                if enrolled["config"] != config:
                    raise ValueError("frozen scheduled research deployment differs")
            else:
                enrolled = {"config": config, "first_session_date": day.isoformat()}
                write(deployment, enrolled)
            # Persistent timers cannot recreate a missed observation. Preserve each
            # eligible missed session across multi-day host/network downtime.
            old_days = {
                r.get("session_date")
                for r in self.store.records("sessions")
                if r.get("phase") == "FINAL"
            }
            past = date.fromisoformat(enrolled["first_session_date"])
            while past < day:
                if window(past) and past.isoformat() not in old_days:
                    self.event(
                        past,
                        "FINAL",
                        status="INCOMPLETE",
                        failure_kind="MISSED_DECISION_WINDOW",
                        decision_id=None,
                    )
                past += timedelta(days=1)
            records = [
                r
                for r in self.store.records("sessions")
                if r.get("session_date") == day.isoformat()
            ]
            started = any(r.get("phase") == "STARTED" for r in records)
            finished = any(r.get("phase") == "FINAL" for r in records)
            target = window(day)
            result = next(
                (r for r in records if r.get("phase") == "FINAL"),
                {"status": "IDLE", "session_date": day.isoformat(), "orders_submitted": 0},
            )
            if started and not finished:
                # Crash recovery never reruns inference or substitutes a later universe.
                saved = [
                    d
                    for d in self.store.records("decisions")
                    if d.get("research_protocol") == PROTOCOL
                    and datetime.fromtimestamp(d["session_open"], NY).date() == day
                ]
                result = self.event(
                    day,
                    "FINAL",
                    status="COMPLETED" if saved else "INCOMPLETE",
                    failure_kind=None if saved else "INTERRUPTED",
                    decision_id=saved[0]["decision_id"] if saved else None,
                )
            elif not started and not finished and target and now >= target[0]:
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
                    try:
                        data = self.collector(UNIVERSE, self.provider, self.helper)
                        captured = self.clock()
                        if (
                            not target[0] <= captured < target[1]
                            or data["evidence_kind"] != self.evidence_kind
                        ):
                            raise ValueError("capture outside prospective decision window")
                        if (data["session_open"], data["session_close"]) != session_bounds(day):
                            raise ValueError("provider session differs from XNYS calendar")
                        if set(data["symbols"]) != set(UNIVERSE):
                            raise ValueError(
                                "provider capture differs from frozen research universe"
                            )
                        report = scan(data, now=captured, candidate_count=3)
                        write(self.directory / "captures" / (identity(data) + ".json"), data)
                        self.store.save("scans", report, report["scan_id"], captured)
                        agent_status, agent_error, provenance = "VALID", None, {}
                        try:
                            document, provenance = self.assessor(report)
                            frozen = self.clock()
                            with self.store.db:
                                for item in document["assessments"]:
                                    self.store.assessment(
                                        {
                                            **item,
                                            "scan_id": report["scan_id"],
                                            "observed_at": frozen,
                                            "sources": [],
                                            "hypothesis_type": "price_action",
                                            "model": provenance["model"],
                                        },
                                        frozen,
                                    )
                        except Exception as error:
                            # No partial assessment reaches a decision. Bounded errors, not
                            # model output or process diagnostics, are persisted.
                            agent_status, agent_error = "UNAVAILABLE", type(error).__name__
                        frozen = self.clock()
                        if not target[0] <= frozen < target[1]:
                            raise ValueError("assessment finished after fixed decision window")
                        # Freshness/completion validation applies to the SAME evidence
                        # for every arm; no extra market look given to Quant after Codex.
                        for candidate in report["candidates"]:
                            research_snapshot(data, candidate["symbol"], frozen)
                        decision = self.store.decide(
                            report,
                            data,
                            now=frozen,
                            shadow_protocol=PROTOCOL,
                            assessment_status=agent_status,
                        )
                        write(
                            self.directory / "decisions" / (decision["decision_id"] + ".json"),
                            decision,
                        )
                        result = self.event(
                            day,
                            "FINAL",
                            status="COMPLETED" if agent_status == "VALID" else "INCOMPLETE",
                            failure_kind=None
                            if agent_status == "VALID"
                            else "CODEX_ASSESSMENT_UNAVAILABLE",
                            assessment_error=agent_error,
                            codex=provenance,
                            decision_id=decision["decision_id"],
                        )
                    except (Halt, ValueError, OSError, KeyError, TypeError) as error:
                        result = self.event(
                            day,
                            "FINAL",
                            status="INCOMPLETE",
                            decision_id=None,
                            failure_kind="MARKET_EVIDENCE_UNAVAILABLE",
                            reason=str(error),
                        )
            resolution = self.resolve()
            status = {
                "mode": "SHADOW",
                "alpha_version": ALPHA_VERSION,
                "codex_model": MODEL,
                "costs": {name: asdict(cost) for name, cost in COSTS.items()},
                "run": result,
                "resolution": resolution,
                "comparison": compare(self.store),
                "pending_sessions": self.store.pending_sessions(
                    self.clock(), evidence_kind=self.evidence_kind
                ),
                "terminal_unresolvable": self.store.terminal_windows(self.clock()),
                "orders_submitted": 0,
            }
            write(self.directory / "daily-status.json", status)
            return status


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--oauth-helper", type=Path)
    args = parser.parse_args(argv)
    pipeline = Pipeline(args.state_dir, oauth_helper=args.oauth_helper)
    try:
        print(json.dumps(pipeline.tick(), allow_nan=False))
    finally:
        pipeline.store.close()


if __name__ == "__main__":
    main()
