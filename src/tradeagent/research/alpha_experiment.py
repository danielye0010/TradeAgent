"""Small chronological research harness. Run with python -m; never touches live state."""

import argparse
import csv
import json
import math
import statistics
from collections import defaultdict
from dataclasses import asdict, replace
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import exchange_calendars

from ..strategy import DEFAULT_PARAMS, BaselineStrategy
from .alpha_data import ResearchBars, download_public
from .alpha_signals import FAMILIES, AlphaStrategy, prior_beta
from .domain import MarketSnapshot, Payload, canonical, identity
from .tradeplan import COSTS, build_plan, economic_evidence

NY = ZoneInfo("America/New_York")
CONTROLS = ("opening_momentum", "opening_reversal", "same_window")
ADAPTIVE = ("adaptive_pooled", "adaptive_regime", "rule_selector")


def phase(day, protocol):
    if day < protocol["train_end"]:
        return "train"
    if day < protocol["validation_end"]:
        return "validation"
    if day < protocol["test_end"]:
        return "test"
    return "supplemental"


def sessions(history, end):
    first = min(b.start for bars in history.bars.values() for b in bars)
    start = datetime.fromtimestamp(first, NY).date()
    stop = date.fromisoformat(end)
    cal = exchange_calendars.get_calendar("XNYS", start=start - timedelta(days=10), end=stop)
    result = []
    for day in cal.sessions_in_range(start, stop - timedelta(days=1)):
        previous = cal.previous_session(day)
        result.append(
            {
                "date": str(day.date()),
                "open": cal.session_open(day).timestamp(),
                "close": cal.session_close(day).timestamp(),
                "previous_close": cal.session_close(previous).timestamp(),
            }
        )
    return result


def snapshot_at(history, session, symbol, minute):
    now = session["open"] + minute * 60
    bars = history.exact(symbol, session["open"], now, now)
    benchmark = history.exact("SPY", session["open"], now, now)
    previous = history.exact(
        symbol, session["previous_close"] - history.interval, session["previous_close"], now
    )
    return MarketSnapshot(
        symbol,
        now,
        history.metadata["source"],
        "replay",
        "SPY",
        bars,
        benchmark,
        bars[-1].close,
        bars[-1].close,
        now,
        now,
        bars[0].open,
        previous[-1].close,
        (),
        session["open"],
        session["previous_close"],
    )


def net_metrics(rows, dates):
    result = {
        "sessions": len(dates),
        "trades": sum(r["active"] for r in rows),
        "active_days": len({r["date"] for r in rows if r["active"]}),
    }
    by_day = defaultdict(list)
    for row in rows:
        by_day[row["date"]].append(row)
    for scenario in ("gross", *COSTS):
        daily = [
            sum(
                (r["gross"] if scenario == "gross" else COSTS[scenario].net(r["gross"])) / 2
                for r in by_day[day]
                if r["active"]
            )
            for day in dates
        ]
        capital = peak = 1.0
        dd = 0.0
        for value in daily:
            capital *= 1 + value
            peak = max(peak, capital)
            dd = min(dd, capital / peak - 1)
        result[scenario] = {
            "total_return": capital - 1,
            "mean_daily": statistics.fmean(daily) if daily else None,
            "max_drawdown": dd,
        }
    active = [r for r in rows if r["active"]]
    result["mean_trade_gross_bps"] = (
        statistics.fmean(r["gross"] for r in active) * 10000 if active else None
    )
    result["mean_trade_net_bps"] = (
        statistics.fmean(COSTS["base"].net(r["gross"]) for r in active) * 10000 if active else None
    )
    result["worst_adverse"] = min((r["adverse"] for r in active), default=None)
    result["mean_beta_residual_bps"] = (
        statistics.fmean(r["residual_gross"] for r in active) * 10000 if active else None
    )
    market_daily = [
        sum(COSTS["base"].net(r.get("market_gross", 0)) / 2 for r in by_day[day] if r["active"])
        for day in dates
    ]
    result["matched_spy_net"] = math.prod(1 + r for r in market_daily) - 1
    result["excess_vs_matched_spy_net"] = result["base"]["total_return"] - result["matched_spy_net"]
    result["diagnostics"] = {
        "adverse_direction": sum(r["gross"] <= 0 for r in active),
        "cost_erased_positive_move": sum(
            r["gross"] > 0 and COSTS["base"].net(r["gross"]) <= 0 for r in active
        ),
        "profitable_after_base_cost": sum(COSTS["base"].net(r["gross"]) > 0 for r in active),
    }
    result["win_fraction_net"] = (
        statistics.fmean(COSTS["base"].net(r["gross"]) > 0 for r in active) if active else None
    )
    return result


def choose_validation(records, protocol):
    validation = [r for r in records if phase(r["date"], protocol) == "validation"]
    dates = sorted({r["date"] for r in validation})
    choices = []
    for key in sorted({r["configuration"] for r in validation if r["strategy"] in FAMILIES}):
        stats = net_metrics([r for r in validation if r["configuration"] == key], dates)
        if stats["active_days"] >= 5 and stats["base"]["mean_daily"] > 0:
            choices.append((stats["base"]["mean_daily"], key))
    return max(choices, key=lambda x: (x[0], x[1]))[1] if choices else "cash"


def adaptive_choice(predictions, previous, regime_specific=False):
    choices = []
    details = {}
    for p in predictions:
        if p.direction <= 0:
            continue
        e = economic_evidence(
            p, previous, regime=p.context.value["regime"] if regime_specific else None
        )
        # Daily shrinkage logic follows the existing learner; score actual unleveraged returns.
        score = (
            COSTS["base"].net(e["mean_gross"] * e["days"] / (e["days"] + 10)) - e["standard_error"]
        )
        details[p.strategy_id] = {**e, "score": score}
        if e["days"] >= 10 and score > 0:
            choices.append((score, p.strategy_id))
    return (max(choices)[1] if choices else None), details


def evaluate(history, protocol):
    records, skipped, plans, choices = [], [], [], []
    beta_pairs = defaultdict(list)
    previous = []
    weekly_history = []
    week = None
    action_days = {
        datetime.fromtimestamp(event["date"], NY).date().isoformat()
        for source in history.metadata.get("sources", [])
        for kind in ("dividends", "splits")
        for event in source.get("events", {}).get(kind, {}).values()
    }
    for session in sessions(history, protocol["supplemental_end"]):
        if session["date"] in action_days:
            skipped.append(
                {
                    "date": session["date"],
                    "reason": "corporate-action day excluded; gap reference ambiguous",
                }
            )
            continue
        day_records = []
        current_week = datetime.fromtimestamp(session["open"], NY).isocalendar()[:2]
        if current_week != week:
            # Freeze evidence once weekly. None of today's outcomes can influence selection.
            weekly_history = list(previous)
            week = current_week
        for minute in protocol["decision_minutes_after_open"]:
            try:
                snapshots = {
                    s: snapshot_at(history, session, s, minute) for s in protocol["symbols"]
                }
                predictions = {}
                for symbol, snap in snapshots.items():
                    beta = prior_beta(beta_pairs[symbol, minute])
                    for horizon in protocol["holding_minutes"]:
                        for family in (*FAMILIES, *CONTROLS[:2]):
                            strategy = (
                                AlphaStrategy(
                                    family, (horizon + protocol["entry_delay_minutes"]) * 60, beta
                                )
                                if family in FAMILIES
                                else BaselineStrategy(
                                    family,
                                    "control-v1",
                                    family,
                                    {
                                        **DEFAULT_PARAMS,
                                        "horizon": (horizon + protocol["entry_delay_minutes"]) * 60,
                                    },
                                )
                            )
                            p = strategy.predict(snap, snap.decision_time)
                            p = replace(
                                p,
                                features=Payload.of(
                                    {
                                        **p.features.plain(),
                                        "decision_offset": minute,
                                        "entry_delay_seconds": protocol["entry_delay_minutes"] * 60,
                                    }
                                ),
                            )
                            predictions[symbol, horizon, family] = p
                # A common complete-case sample across all horizons and both targets.
                future = {}
                for symbol in (*protocol["symbols"], "SPY"):
                    for horizon in protocol["holding_minutes"]:
                        entry = session["open"] + (minute + protocol["entry_delay_minutes"]) * 60
                        exit_time = entry + horizon * 60
                        if exit_time + history.interval > session["close"]:
                            raise ValueError("horizon exceeds session")
                        path = history.exact(
                            symbol,
                            entry,
                            exit_time + history.interval,
                            exit_time + history.interval,
                        )
                        future[symbol, horizon] = {
                            "gross": path[-1].open / path[0].open - 1,
                            "entry": path[0].open,
                            "exit": path[-1].open,
                            "adverse": min(b.low for b in path[:-1]) / path[0].open - 1,
                            "entry_time": entry,
                            "exit_time": exit_time,
                            "resolved_at": exit_time + history.interval,
                        }
            except ValueError as exc:
                skipped.append({"date": session["date"], "minute": minute, "reason": str(exc)})
                continue
            for symbol, snap in snapshots.items():
                market = snap.benchmark_bars[-1].close / snap.benchmark_bars[0].open - 1
                beta = prior_beta(beta_pairs[symbol, minute])
                for horizon in protocol["holding_minutes"]:
                    for family in (*FAMILIES, *CONTROLS):
                        p = predictions.get((symbol, horizon, family))
                        obs = future[symbol, horizon]
                        record = {
                            "date": session["date"],
                            "symbol": symbol,
                            "strategy": family,
                            "version": p.strategy_version if p else "control-v1",
                            "configuration": f"{family}:{minute}:{horizon}",
                            "horizon": (horizon + protocol["entry_delay_minutes"]) * 60,
                            "decision_offset": minute,
                            "decision_time": snap.decision_time,
                            "prediction_id": p.prediction_id
                            if p
                            else identity([family, symbol, snap.decision_time, horizon]),
                            "regime": p.context.value["regime"] if p else "control",
                            "evidence_kind": "replay",
                            "active": p.direction > 0 if p else True,
                            "direction": p.direction if p else 1,
                            "expected": p.expected_return if p else None,
                            **obs,
                            "beta": beta,
                            "market_gross": future["SPY", horizon]["gross"],
                            "residual_gross": obs["gross"] - beta * future["SPY", horizon]["gross"],
                        }
                        day_records.append(record)
                        if minute == 30 and horizon == 60 and family in FAMILIES:
                            plans.append(asdict(build_plan(p, snap, previous)))
                if minute == 30:
                    candidates = [predictions[symbol, 60, f] for f in FAMILIES]
                    for selector in ADAPTIVE:
                        detail = {}
                        if selector == "rule_selector":
                            regime = candidates[0].context.value["regime"]
                            selected = (
                                "opening_continuation"
                                if regime == "trend"
                                else "stabilized_reversal"
                            )
                            if (
                                next(p for p in candidates if p.strategy_id == selected).direction
                                <= 0
                            ):
                                selected = None
                        else:
                            selected, detail = adaptive_choice(
                                candidates, weekly_history, selector == "adaptive_regime"
                            )
                        template = next(
                            r
                            for r in day_records
                            if r["symbol"] == symbol
                            and r["configuration"] == f"{selected or FAMILIES[0]}:30:60"
                        )
                        day_records.append(
                            {
                                **template,
                                "strategy": selector,
                                "configuration": f"{selector}:30:60",
                                "active": selected is not None,
                                "selected_family": selected,
                            }
                        )
                        choices.append(
                            {
                                "date": session["date"],
                                "symbol": symbol,
                                "selector": selector,
                                "selected": selected,
                                "evidence": detail,
                            }
                        )
                beta_pairs[symbol, minute].append(
                    (market, snap.bars[-1].close / snap.bars[0].open - 1)
                )
        records.extend(day_records)
        previous.extend(r for r in day_records if r["strategy"] in FAMILIES)
    return records, skipped, plans, choices


def summarize(records, protocol, selected):
    result = {
        "selected_before_test": selected,
        "periods": {},
        "by_symbol_test": {},
        "by_regime_test": {},
    }
    for split in ("train", "validation", "test", "supplemental"):
        subset = [r for r in records if phase(r["date"], protocol) == split]
        result["periods"][split] = {}
        for key in sorted({r["configuration"] for r in subset}):
            rows = [r for r in subset if r["configuration"] == key]
            dates = sorted({r["date"] for r in rows})
            result["periods"][split][key] = net_metrics(rows, dates)
    test = [r for r in records if phase(r["date"], protocol) == "test"]
    for key in sorted({r["configuration"] for r in test}):
        subset = [r for r in test if r["configuration"] == key]
        for symbol in protocol["symbols"]:
            rows = [r for r in subset if r["symbol"] == symbol]
            # Keep the same total-capital denominator: each instrument owns half.
            result["by_symbol_test"][f"{key}:{symbol}"] = net_metrics(
                rows, sorted({r["date"] for r in rows})
            )
        for regime in sorted({r["regime"] for r in subset}):
            rows = [r for r in subset if r["regime"] == regime]
            result["by_regime_test"][f"{key}:{regime}"] = net_metrics(
                rows, sorted({r["date"] for r in rows})
            )
    result["conclusion"] = (
        "No validated net edge: short revised-price sample, modeled costs and multiple comparisons."
    )
    result["options"] = (
        "Unavailable: no historical synchronized entry/exit bid-ask, IV, theta or liquidity records."
    )
    return result


def run(dataset, protocol, output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    history = ResearchBars(dataset)
    if not (
        protocol["train_end"]
        < protocol["validation_end"]
        < protocol["test_end"]
        <= protocol["supplemental_end"]
        <= datetime.now(NY).date().isoformat()
    ):
        raise ValueError("invalid chronological boundaries")
    if protocol["symbols"] != ["QQQ", "IWM"] or protocol["benchmark"] != "SPY":
        raise ValueError("fixed equal-sleeve research universe required")
    if (
        protocol["decision_minutes_after_open"] != [30, 90]
        or protocol["holding_minutes"] != [30, 60, 120]
        or protocol["entry_delay_minutes"] != 5
        or protocol["costs_per_side_bps"] != {"low": 1, "base": 2.1, "stress": 5.2}
    ):
        raise ValueError("this frozen experiment supports only its declared grid/costs")
    metadata = {
        "protocol": protocol,
        "dataset_digest": identity(dataset),
        "data_metadata": history.metadata,
        "source_hashes": {
            p.name: identity(p.read_text())
            for p in (
                Path(__file__),
                Path(__file__).with_name("alpha_signals.py"),
                Path(__file__).with_name("alpha_data.py"),
                Path(__file__).with_name("tradeplan.py"),
                Path(__file__).with_name("market.py"),
                Path(__file__).with_name("domain.py"),
                Path(__file__).parent.parent / "strategy.py",
            )
        },
    }
    (output / "protocol.json").write_text(canonical(metadata) + "\n")
    records, skipped, plans, choices = evaluate(history, protocol)
    selected = choose_validation(records, protocol)
    (output / "selection.json").write_text(
        canonical({"selected": selected, "rule": protocol["selection"]}) + "\n"
    )
    result = summarize(records, protocol, selected)
    result["coverage"] = {
        "skipped": skipped,
        "complete_sessions": len({r["date"] for r in records}),
        "first": min((r["date"] for r in records), default=None),
        "last": max((r["date"] for r in records), default=None),
    }
    result["data_digest"] = metadata["dataset_digest"]
    result["plan_decisions"] = {
        kind: sum(p["decision"]["kind"] == kind for p in plans)
        for kind in ("NO_TRADE", "UNDERLYING")
    }
    result["all_plans_research_only"] = all(p["research_only"] for p in plans)
    (output / "results.json").write_text(json.dumps(result, indent=2) + "\n")
    (output / "records.json").write_text(canonical(records) + "\n")
    (output / "plans.json").write_text(canonical(plans) + "\n")
    (output / "selector.json").write_text(canonical(choices) + "\n")
    if records:
        keys = sorted(set().union(*(r.keys() for r in records)))
        with (output / "trades.csv").open("w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=keys)
            writer.writeheader()
            writer.writerows(records)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--download", type=Path, help="fresh public data directory; explicitly enables network"
    )
    parser.add_argument("--input", type=Path)
    parser.add_argument("--protocol", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    if args.download:
        if args.input or args.output or args.protocol:
            parser.error("--download is a separate collection operation")
        dataset = download_public(args.download)
        print(json.dumps({"bars": len(dataset["bars"]), "path": str(args.download / "bars.json")}))
        return
    if not all((args.input, args.protocol, args.output)):
        parser.error("--input, --protocol and --output are required")
    result = run(
        json.loads(args.input.read_text()), json.loads(args.protocol.read_text()), args.output
    )
    print(
        json.dumps(
            {
                "selected": result["selected_before_test"],
                "coverage": result["coverage"],
                "plans": result["plan_decisions"],
                "conclusion": result["conclusion"],
            }
        )
    )


if __name__ == "__main__":
    main()
