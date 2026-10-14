"""One scheduled opportunity experiment per XNYS session; never execution."""

import argparse
import json
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from ..calendar import session_bounds
from ..model import Halt
from ..opportunity_cli import write
from ..research import bidirectional
from ..research.alpha_signals import ALPHA_VERSION
from ..research.domain import identity
from ..research.opportunities import UNIVERSE, capture, research_snapshot, scan
from ..research.opportunity_workflow import DailyResearch
from ..research.put_shadow import PUT_RULE, PutReader, open_put_reader
from ..research.research_universe import (
    COLLECTION_BUDGET_SECONDS,
    feasible_universe,
    liquid_candidates,
)
from ..research.tradeplan import COSTS
from .assessor import MODEL, NEWS_WEB_SEARCH, assess, assess_news

NY = ZoneInfo("America/New_York")
PROTOCOL = "daily-four-arm-v1"
ARMS = ("quant_only", "codex_only", "quant_codex", "seeded_random")
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


NEWS_PROTOCOL = "daily-news-v2"
NEWS_ARMS = ("quant_only", "codex_price", "codex_news", "quant_codex_news", "seeded_random")
# Separate experiment: same candidates and window for a price-only and a news-reading
# Codex, so their difference isolates what reading the news adds.
NEWS_CONFIG = {
    "protocol": NEWS_PROTOCOL,
    "decision_window_ny": "10:30:00-10:37:00",
    "universe": UNIVERSE,
    "candidates": 3,
    "holding_seconds": 3600,
    "delay_seconds": 300,
    "assessor": "one shared prompt, schema, model and deadline; only web search differs",
    "assessor_timeout_seconds": 300,
    "assessors_run": "concurrently",
    "web_search": NEWS_WEB_SEARCH,
    "random_seed": "random-seed-2026-10-09",
    "mode": "SHADOW",
    "alpha_version": ALPHA_VERSION,
    "codex_model": MODEL,
    "costs": {name: asdict(cost) for name, cost in COSTS.items()},
}
BIDIRECTIONAL_CONFIG = {
    **NEWS_CONFIG,
    "protocol": bidirectional.PROTOCOL,
    "universe": "measured prefix from --universe-profile",
    "capital_dollars": bidirectional.CAPITAL,
    "minimum_point_net_edge": bidirectional.MIN_NET_EDGE,
    "put_rule": PUT_RULE,
    "collection_budget_seconds": COLLECTION_BUDGET_SECONDS,
}
PROTOCOLS = {
    PROTOCOL: {
        "config": CONFIG,
        "arms": ARMS,
        "baselines": ("quant_only", "seeded_random"),
        "start": (10, 0),
        "seconds": 180,
    },
    NEWS_PROTOCOL: {
        "config": NEWS_CONFIG,
        "arms": NEWS_ARMS,
        "baselines": ("quant_only", "codex_price", "seeded_random"),
        "start": (10, 30),
        "seconds": 420,
    },
}
PROTOCOLS[bidirectional.PROTOCOL] = {
    "config": BIDIRECTIONAL_CONFIG,
    "arms": bidirectional.ARMS,
    "baselines": ("quant_only", "spy", "cash"),
    "start": (10, 30),
    "seconds": 420,
}


def window(day, protocol=PROTOCOL):
    bounds = session_bounds(day)
    if not bounds:
        return None
    spec = PROTOCOLS[protocol]
    hour, minute = spec["start"]
    start = datetime(day.year, day.month, day.day, hour, minute, tzinfo=NY).timestamp()
    end = start + spec["seconds"]
    # Early closes use the unchanged horizon, not a shortened forecast.
    return (start, end) if end + 3900 + 60 <= bounds[1] else None


def residual(outcome):
    """Net return in excess of holding the benchmark over the same window; cash is zero."""
    if "benchmark_gross" not in outcome:
        return 0.0
    return outcome["base_net"] - outcome["benchmark_gross"]


def paired(values):
    """Mean and standard error of daily paired values; one session is one observation."""
    n = len(values)
    if not n:
        return {"days": 0, "mean": None, "standard_error": None}
    mean = sum(values) / n
    se = (sum((v - mean) ** 2 for v in values) / (n - 1)) ** 0.5 / n**0.5 if n > 1 else None
    return {"days": n, "mean": mean, "standard_error": se}


def compare(store, protocol=PROTOCOL, names=ARMS, baselines=("quant_only", "seeded_random")):
    decisions = {
        d["decision_id"]: d
        for d in store.records("decisions")
        if d.get("research_protocol") == protocol
    }
    terminal = {r["decision_id"] for r in store.records("resolution_failures")}
    pools = {}
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
        paired_days = [
            o for o in outcomes if all(o["comparisons"].get(n) is not None for n in names)
        ]
        days = {decisions[o["decision_id"]]["session_open"] for o in paired_days}
        if len(days) != len(paired_days):
            raise ValueError("duplicate scheduled session in paired research")

        def mean(values):
            return sum(values) / len(values) if values else None

        stats = {
            n: {
                "observed_days": sum(o["comparisons"].get(n) is not None for o in outcomes),
                "selected_days": sum(bool(d["comparisons"][n]["shadow_direction"]) for d in ds),
                "unavailable_days": sum(d["comparisons"][n]["status"] == "UNAVAILABLE" for d in ds),
                "long_days": sum("benchmark_gross" in o["comparisons"][n] for o in paired_days),
                "paired_mean_base_net": mean(
                    [o["comparisons"][n]["base_net"] for o in paired_days]
                ),
                "paired_mean_stress_net": mean(
                    [o["comparisons"][n]["stress_net"] for o in paired_days]
                ),
                "paired_excess_over_benchmark": paired(
                    [residual(o["comparisons"][n]) for o in paired_days]
                ),
            }
            for n in names
        }
        differences = {
            n: {
                baseline: mean(
                    [
                        o["comparisons"][n]["base_net"] - o["comparisons"][baseline]["base_net"]
                        for o in paired_days
                    ]
                )
                for baseline in baselines
            }
            for n in names
        }
        # Market-adjusted: an arm that is simply long more often than its baseline
        # does not win when the market rises during the window.
        excess_differences = {
            n: {
                baseline: paired(
                    [
                        residual(o["comparisons"][n]) - residual(o["comparisons"][baseline])
                        for o in paired_days
                    ]
                )
                for baseline in baselines
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
            "paired_excess_differences": excess_differences,
            "resolved_symbol_counterfactuals": len(full),
            "eligible_symbol_forecasts": sum(
                bool(c["predictions"]) for d in ds for c in d["quantitative_observations"]
            ),
        }
    return {
        "protocol": protocol,
        "evidence_pools": pools,
        "orders_submitted": 0,
        "interpretation": "modeled returns with frozen spread/slippage; cash abstentions retained; "
        "excess = arm net return minus the benchmark over the same window (cash = 0); "
        "no actual fills or established uplift",
    }


def cited(sources, observed):
    """Keep only well-formed HTTPS citations; unknown or future dates become null."""
    from urllib.parse import urlparse

    from ..research.domain import timestamp

    kept = []
    for source in sources:
        if urlparse(source.get("url", "")).scheme != "https" or not source.get("claim"):
            continue
        try:
            published = timestamp(source["published_at"]) if source["published_at"] else None
        except (TypeError, ValueError, OverflowError):
            published = None
        kept.append(
            {
                "url": source["url"],
                "title": source.get("title") or source["url"],
                "published_at": published
                if published is not None and published <= observed
                else None,
                "observed_at": observed,
                "claim": source["claim"],
                "role": source["role"],
            }
        )
    return kept


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
        protocol=PROTOCOL,
        news_assessor=assess_news,
        universe_profile=None,
        put_reader_factory=open_put_reader,
    ):
        if protocol not in PROTOCOLS:
            raise ValueError("unknown scheduled research protocol")
        self.protocol, self.news_assessor = protocol, news_assessor
        self.bidirectional = protocol == bidirectional.PROTOCOL
        self.universe = UNIVERSE
        self.profile = None
        self.put_reader_factory = put_reader_factory
        if self.bidirectional:
            if provider != "robinhood" or universe_profile is None:
                raise ValueError(
                    "bidirectional research requires a measured Robinhood universe profile"
                )
            self.profile = json.loads(Path(universe_profile).read_text())
            self.universe = list(feasible_universe(self.profile))
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
            "protocol": self.protocol,
            "orders_submitted": 0,
            **values,
        }
        key = identity([self.protocol, day.isoformat(), phase])
        self.store.save("sessions", item, key, item["recorded_at"])
        write(self.directory / "sessions" / (key + ".json"), item)
        return item

    def freeze(self, report, call, tag):
        """Run one bounded assessor call and freeze its output, or record it unavailable."""
        try:
            document, provenance = call() if callable(call) else call.result()
            frozen = self.clock()
            with self.store.db:
                for item in document["assessments"]:
                    record = {
                        **item,
                        "scan_id": report["scan_id"],
                        "observed_at": frozen,
                        "sources": cited(item.get("sources", []), frozen),
                        "hypothesis_type": item.get("hypothesis_type", "price_action"),
                        "model": provenance["model"],
                    }
                    if tag:
                        record["assessor"] = tag
                    self.store.assessment(record, frozen)
            return "VALID", None, provenance
        except Exception as error:
            # No partial assessment reaches a decision. Bounded errors, not
            # model output or process diagnostics, are persisted.
            return "UNAVAILABLE", type(error).__name__, {}

    def collect(self, symbols, *, session=None):
        if not self.bidirectional:
            return self.collector(
                symbols, self.provider, self.helper, **({"session": session} if session else {})
            )
        return self.collector(
            symbols,
            self.provider,
            self.helper,
            session=session,
            deadline=self.clock() + COLLECTION_BUDGET_SECONDS,
        )

    def select_puts(self, report, data, deadline):
        selections = {}
        try:
            with self.put_reader_factory(
                Path(__file__).resolve().parents[3], self.helper
            ) as bridge:
                reader = PutReader(bridge, self.clock)
                for c in report["candidates"]:
                    if self.clock() >= deadline:
                        break
                    try:
                        snapshot = research_snapshot(data, c["symbol"], self.clock())
                        # Any of the same three candidates can be independently bearish
                        # for Codex, even when every Quant family disagrees.
                        selections[c["symbol"]] = reader.select(
                            c["symbol"], (snapshot.ask + snapshot.bid) / 2, deadline=deadline
                        )
                    except (Halt, ValueError, OSError, KeyError, TypeError):
                        selections[c["symbol"]] = None
        except (Halt, ValueError, OSError, KeyError, TypeError):
            pass  # Missing options are explicit UNAVAILABLE outcomes, never cash or underlying-derived Put returns.
        return selections

    def observe_puts(self):
        now = self.clock()
        due = any(
            d.get("research_protocol") == bidirectional.PROTOCOL
            and any(d.get("put_selections", {}).values())
            and any(
                d[f"outcome_{p}_time"] <= now < d[f"outcome_{p}_time"] + 60
                for p in ("entry", "exit")
            )
            for d in self.store.records("decisions")
        )
        if due:
            try:
                with self.put_reader_factory(
                    Path(__file__).resolve().parents[3], self.helper
                ) as bridge:
                    bidirectional.observe_options(
                        self.store, PutReader(bridge, self.clock), self.clock()
                    )
                    return
            except (Halt, ValueError, OSError, KeyError, TypeError):
                pass
        bidirectional.observe_options(self.store, None, self.clock())

    def resolve(self):
        results = []
        for request in self.store.pending_sessions(self.clock(), evidence_kind=self.evidence_kind):
            if request["source"] != self.provider + "-opportunity-minute-v1":
                continue
            try:
                data = self.collect(
                    request["symbols"], session=date.fromisoformat(request["session_date"])
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
            config = {
                **PROTOCOLS[self.protocol]["config"],
                "provider": self.provider,
                "evidence_kind": self.evidence_kind,
            }
            if self.bidirectional:
                config.update(universe=self.universe, universe_profile_hash=identity(self.profile))
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
                if window(past, self.protocol) and past.isoformat() not in old_days:
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
            target = window(day, self.protocol)
            result = next(
                (r for r in records if r.get("phase") == "FINAL"),
                {"status": "IDLE", "session_date": day.isoformat(), "orders_submitted": 0},
            )
            if started and not finished:
                # Crash recovery never reruns inference or substitutes a later universe.
                saved = [
                    d
                    for d in self.store.records("decisions")
                    if d.get("research_protocol") == self.protocol
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
                        data = self.collect(self.universe)
                        captured = self.clock()
                        if (
                            not target[0] <= captured < target[1]
                            or data["evidence_kind"] != self.evidence_kind
                        ):
                            raise ValueError("capture outside prospective decision window")
                        if (data["session_open"], data["session_close"]) != session_bounds(day):
                            raise ValueError("provider session differs from XNYS calendar")
                        if set(data["symbols"]) != set(self.universe):
                            raise ValueError(
                                "provider capture differs from frozen research universe"
                            )
                        report = scan(data, now=captured, candidate_count=3)
                        if self.bidirectional:
                            report["research_protocol"] = self.protocol
                            report = liquid_candidates(report, data)
                        write(self.directory / "captures" / (identity(data) + ".json"), data)
                        self.store.save("scans", report, report["scan_id"], captured)
                        news = self.protocol in {NEWS_PROTOCOL, bidirectional.PROTOCOL}
                        if news:
                            # Both arms: same prompt, schema, model and deadline; only web
                            # search differs. Run together so both fit the decision window.
                            started = self.clock()
                            with ThreadPoolExecutor(max_workers=2) as pool:
                                price_call = pool.submit(
                                    self.news_assessor, report, now=started, search=False
                                )
                                news_call = pool.submit(
                                    self.news_assessor, report, now=started, search=True
                                )
                                agent_status, agent_error, provenance = self.freeze(
                                    report, price_call, "price"
                                )
                                news_status, news_error, news_provenance = self.freeze(
                                    report, news_call, "news"
                                )
                        else:
                            agent_status, agent_error, provenance = self.freeze(
                                report, lambda: self.assessor(report), None
                            )
                            news_status, news_error, news_provenance = "UNAVAILABLE", None, {}
                        if news:
                            # Keep the assessed shortlist frozen; refresh once for every
                            # arm after both Codex calls, which can outlive quote freshness.
                            data = self.collect(self.universe)
                            if (data["session_open"], data["session_close"]) != session_bounds(day):
                                raise ValueError("provider session differs from XNYS calendar")
                            write(self.directory / "captures" / (identity(data) + ".json"), data)
                        puts = (
                            self.select_puts(report, data, min(target[1], self.clock() + 20))
                            if self.bidirectional
                            else None
                        )
                        frozen = self.clock()
                        if not target[0] <= frozen < target[1]:
                            raise ValueError("assessment finished after fixed decision window")
                        # All arms share the same decision data and freshness checks.
                        for candidate in report["candidates"]:
                            research_snapshot(data, candidate["symbol"], frozen)
                        decision = self.store.decide(
                            report,
                            data,
                            now=frozen,
                            shadow_protocol=self.protocol,
                            assessment_status=agent_status,
                            news_assessment_status=news_status,
                            **({"put_selections": puts} if self.bidirectional else {}),
                        )
                        write(
                            self.directory / "decisions" / (decision["decision_id"] + ".json"),
                            decision,
                        )
                        complete = agent_status == "VALID" and (not news or news_status == "VALID")
                        extra = (
                            {"news_assessment_error": news_error, "codex_news": news_provenance}
                            if news
                            else {}
                        )
                        result = self.event(
                            day,
                            "FINAL",
                            status="COMPLETED" if complete else "INCOMPLETE",
                            failure_kind=None if complete else "CODEX_ASSESSMENT_UNAVAILABLE",
                            assessment_error=agent_error,
                            codex=provenance,
                            decision_id=decision["decision_id"],
                            **extra,
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
            if self.bidirectional:
                self.observe_puts()
            resolution = self.resolve()
            status = {
                "mode": "SHADOW",
                "alpha_version": ALPHA_VERSION,
                "codex_model": MODEL,
                "costs": {name: asdict(cost) for name, cost in COSTS.items()},
                "run": result,
                "resolution": resolution,
                "protocol": self.protocol,
                "comparison": bidirectional.compare(self.store)
                if self.bidirectional
                else compare(
                    self.store,
                    self.protocol,
                    PROTOCOLS[self.protocol]["arms"],
                    PROTOCOLS[self.protocol]["baselines"],
                ),
                "pending_sessions": self.store.pending_sessions(
                    self.clock(), evidence_kind=self.evidence_kind
                ),
                "terminal_unresolvable": self.store.terminal_windows(self.clock()),
                "orders_submitted": 0,
                "tick_completed_at": self.clock(),
            }
            if not self.bidirectional:
                from .daily_reporting import SavedCaptures, evaluate, render, write_report

                evaluation = evaluate(
                    self.store,
                    self.protocol,
                    self.clock(),
                    SavedCaptures(self.directory, self.clock()),
                )
                status["exploratory_selection"] = evaluation
                write_report(self.directory, render(evaluation, status))
            write(self.directory / "daily-status.json", status)
            if self.bidirectional:
                (self.directory / "daily-pnl.md").write_text(
                    bidirectional.pnl_report(self.store), encoding="utf-8"
                )
            return status


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--oauth-helper", type=Path)
    parser.add_argument("--protocol", choices=sorted(PROTOCOLS), default=PROTOCOL)
    parser.add_argument("--universe-profile", type=Path)
    parser.add_argument(
        "--report-only",
        action="store_true",
        help="regenerate daily-report.md from saved records only; no inference, collection or decision tick",
    )
    args = parser.parse_args(argv)
    if args.report_only:
        from .daily_reporting import regenerate

        result = regenerate(args.state_dir, args.protocol)
        print(json.dumps({k: v for k, v in result.items() if k != "today"}, allow_nan=False))
        return
    pipeline = Pipeline(
        args.state_dir,
        oauth_helper=args.oauth_helper,
        protocol=args.protocol,
        universe_profile=args.universe_profile,
    )
    try:
        print(json.dumps(pipeline.tick(), allow_nan=False))
    finally:
        pipeline.store.close()


if __name__ == "__main__":
    main()
