"""Economic evaluation of frozen baselines, isolated from prospective state."""

import argparse
import csv
import json
import math
import statistics
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import exchange_calendars

from ..strategy import DEFAULT_FAMILIES, DEFAULT_PARAMS, BaselineStrategy, implementation_hash
from .domain import canonical, identity
from .expressions import plan
from .historical_data import HistoricalInputs
from .replay import decision_snapshot, exact_path

FAMILIES = DEFAULT_FAMILIES[:5]
# Basis points PER SIDE. Quotes are not observed; these are sensitivity assumptions.
COSTS = {
    "low": {"half_spread_bps": 0.5, "slippage_bps": 0.5, "fee_bps": 0.0},
    "base": {"half_spread_bps": 1.0, "slippage_bps": 1.0, "fee_bps": 0.1},
    "stress": {"half_spread_bps": 2.0, "slippage_bps": 3.0, "fee_bps": 0.2},
}
NY = ZoneInfo("America/New_York")


def calendar(start, end):
    start, end = date.fromisoformat(start), date.fromisoformat(end)
    if start >= end or end > datetime.now(NY).date():
        raise ValueError("require start < end <= today; end is exclusive")
    cal = exchange_calendars.get_calendar("XNYS", start=start - timedelta(days=14), end=end)
    sessions = cal.sessions_in_range(start, end - timedelta(days=1))
    rows = []
    for session in sessions:
        previous = cal.previous_session(session)
        rows.append(
            {
                "date": str(session.date()),
                "open": cal.session_open(session).timestamp(),
                "close": cal.session_close(session).timestamp(),
                "decision": datetime.combine(session.date(), time(9, 33), NY).timestamp(),
                "previous_close": cal.session_close(previous).timestamp(),
            }
        )
    return rows


def net_return(entry, exit_price, cost):
    side = sum(cost.values()) / 10000
    if not 0 <= side < 1 or min(entry, exit_price) <= 0:
        raise ValueError("invalid prices or costs")
    return exit_price * (1 - side) / (entry * (1 + side)) - 1


def metrics(returns):
    if not returns:
        return {"sessions": 0, "total_return": None, "max_drawdown": None, "sharpe": None}
    equity = peak = 1.0
    drawdown = 0.0
    for value in returns:
        equity *= 1 + value
        peak = max(peak, equity)
        drawdown = min(drawdown, equity / peak - 1)
    sigma = statistics.pstdev(returns)
    annual = equity ** (252 / len(returns)) - 1 if len(returns) >= 252 else None
    return {
        "sessions": len(returns),
        "total_return": equity - 1,
        "annualized_return": annual,
        "max_drawdown": drawdown,
        "annualized_volatility": sigma * math.sqrt(252),
        "sharpe": statistics.fmean(returns) / sigma * math.sqrt(252) if sigma else None,
        "mean_daily_return": statistics.fmean(returns),
    }


def observations(history, session, symbols, benchmark):
    """Require common complete opportunity set; missing days stay unavailable."""
    now = session["decision"]
    entry_time, exit_time = now + 60, now + 3660
    result = {}
    for symbol in [*symbols, benchmark]:
        path = history.window(symbol, entry_time, exit_time + 60, exit_time + 60)
        previous = history.window(
            symbol, session["previous_close"] - 60, session["previous_close"], now
        )
        close = history.window(symbol, session["close"] - 60, session["close"], session["close"])
        if not exact_path(path, entry_time, exit_time + 60) or not previous or not close:
            raise ValueError(f"{symbol}: missing contiguous entry/exit path or passive reference")
        result[symbol] = {
            "entry": path[0].open,
            "exit": path[-1].open,
            "gross": path[-1].open / path[0].open - 1,
            "passive": close[-1].close / previous[-1].close - 1,
            "worst_adverse": min(b.low for b in path[:-1]) / path[0].open - 1,
        }
    return result


def evaluate_sessions(inputs, sessions, symbols, benchmark):
    daily, trades, skipped = [], [], []
    strategies = {
        f: BaselineStrategy(f, "historical-baseline-v1", f, DEFAULT_PARAMS) for f in FAMILIES
    }
    for history, chunk in inputs.chunks(sessions):
        for session in chunk:
            try:
                snapshots = {
                    s: decision_snapshot(history, session, s, benchmark, session["decision"])
                    for s in symbols
                }
                # Generate predictions before requesting any future prices.
                predictions = {
                    (f, s): strategy.predict(snapshots[s], session["decision"])
                    for f, strategy in strategies.items()
                    for s in symbols
                }
                future = observations(history, session, symbols, benchmark)
            except ValueError as exc:
                skipped.append({"date": session["date"], "reason": str(exc)})
                continue
            record = {
                "date": session["date"],
                "session_minutes": (session["close"] - session["open"]) / 60,
                "passive_targets": statistics.fmean(future[s]["passive"] for s in symbols),
                "passive_market": future[benchmark]["passive"],
                "passive_by_symbol": {s: future[s]["passive"] for s in symbols},
                "window_targets": statistics.fmean(future[s]["gross"] for s in symbols),
                "window_market": future[benchmark]["gross"],
                "strategies": {},
            }
            for family in FAMILIES:
                active, gross, costs = 0, 0.0, {c: 0.0 for c in COSTS}
                directions = {"bullish": 0, "bearish_no_trade": 0, "abstain": 0}
                for symbol in symbols:
                    prediction = predictions[family, symbol]
                    expression = plan(prediction, snapshots[symbol], selected=True)
                    directions[
                        "bullish"
                        if prediction.direction > 0
                        else "bearish_no_trade"
                        if prediction.direction < 0
                        else "abstain"
                    ] += 1
                    if expression.kind != "UNDERLYING":
                        continue
                    obs = future[symbol]
                    active += 1
                    gross += obs["gross"] / len(symbols)
                    trade = {
                        "date": session["date"],
                        "family": family,
                        "symbol": symbol,
                        "prediction_id": prediction.prediction_id,
                        "decision_time": session["decision"],
                        "signal_end": snapshots[symbol].bars[-1].end,
                        "entry_time": session["decision"] + 60,
                        "exit_time": session["decision"] + 3660,
                        "capital_fraction": 1 / len(symbols),
                        "entry_open": obs["entry"],
                        "exit_open": obs["exit"],
                        "gross_return": obs["gross"],
                        "worst_adverse": obs["worst_adverse"],
                    }
                    for name, cost in COSTS.items():
                        trade[f"net_{name}"] = net_return(obs["entry"], obs["exit"], cost)
                        costs[name] += trade[f"net_{name}"] / len(symbols)
                    trades.append(trade)
                record["strategies"][family] = {
                    "gross": gross,
                    **costs,
                    "trades": active,
                    "directions": directions,
                    "capital_utilization": active / len(symbols),
                    "market_time_exposure": active / len(symbols) * 60 / record["session_minutes"],
                }
            daily.append(record)
    return daily, trades, skipped


def benchmark_scales(training):
    scales = {}
    passive = [d["passive_targets"] for d in training]
    sigma = statistics.pstdev(passive) if passive else 0
    for family in FAMILIES:
        member = [d["strategies"][family] for d in training]
        scales[family] = {
            "capital": statistics.fmean(r["capital_utilization"] for r in member) if member else 0,
            "time": statistics.fmean(r["market_time_exposure"] for r in member) if member else 0,
            "volatility": min(1.0, statistics.pstdev([r["base"] for r in member]) / sigma)
            if sigma
            else 0,
        }
    return scales


def summarize(days, trades, scales, requested):
    result = {
        "requested_sessions": requested,
        "complete_sessions": len(days),
        "coverage": len(days) / requested if requested else 0,
        "benchmarks": {"cash": metrics([0.0] * len(days))},
        "strategies": {},
    }
    for key in ("passive_targets", "passive_market", "window_targets", "window_market"):
        returns = [d[key] for d in days]
        if key == "passive_targets":
            returns = benchmark_returns(days)
        elif returns:
            # Passive incurs a single entry/exit pair per segment; window incurs daily pair.
            side = sum(COSTS["base"].values()) / 10000
            if key.startswith("passive"):
                returns[0] = (1 + returns[0]) / (1 + side) - 1
                returns[-1] = (1 + returns[-1]) * (1 - side) - 1
            else:
                returns = [(1 + r) * (1 - side) / (1 + side) - 1 for r in returns]
        result["benchmarks"][key] = metrics(returns)
    for family in FAMILIES:
        rows = [d["strategies"][family] for d in days]
        family_trades = [t for t in trades if t["family"] == family]
        stats = {c: metrics([r[c] for r in rows]) for c in ("gross", *COSTS)}
        stats.update(
            {
                "trades": len(family_trades),
                "trades_per_session": len(family_trades) / len(days) if days else None,
                "mean_capital_utilization": statistics.fmean(r["capital_utilization"] for r in rows)
                if rows
                else None,
                "mean_market_time_exposure": statistics.fmean(
                    r["market_time_exposure"] for r in rows
                )
                if rows
                else None,
                "time_out_of_market": 1 - statistics.fmean(r["market_time_exposure"] for r in rows)
                if rows
                else None,
                "win_fraction_base": statistics.fmean(t["net_base"] > 0 for t in family_trades)
                if family_trades
                else None,
                "mean_net_trade_return_base": statistics.fmean(t["net_base"] for t in family_trades)
                if family_trades
                else None,
                "worst_trade_adverse": min(
                    (t["worst_adverse"] for t in family_trades), default=None
                ),
                "direction_counts": {
                    k: sum(r["directions"][k] for r in rows)
                    for k in ("bullish", "bearish_no_trade", "abstain")
                },
                "benchmark_scales_from_train": scales[family],
            }
        )
        # Fixed training exposure, not a hindsight mask of each strategy's test trades.
        capital = scales[family]["capital"]
        matched = [
            capital
            * (
                (1 + d["window_targets"])
                * (1 - sum(COSTS["base"].values()) / 10000)
                / (1 + sum(COSTS["base"].values()) / 10000)
                - 1
            )
            for d in days
        ]
        stats["exposure_matched_window"] = metrics(matched)
        stats["paired_mean_daily_excess"] = (
            statistics.fmean(r["base"] - b for r, b in zip(rows, matched, strict=True))
            if rows
            else None
        )
        for name in ("time", "volatility"):
            scale = scales[family][name]
            stats[f"{name}_matched_passive"] = metrics([scale * r for r in benchmark_returns(days)])
        stats["by_year"] = {
            year: metrics(
                [d["strategies"][family]["base"] for d in days if d["date"].startswith(year)]
            )
            for year in sorted({d["date"][:4] for d in days})
        }
        result["strategies"][family] = stats
    return result


def benchmark_returns(days):
    # Equal INITIAL weights, thereafter drifting buy-and-hold weights; no daily
    # rebalancing to create a free synthetic passive portfolio.
    wealth = {s: 1.0 for s in days[0]["passive_by_symbol"]} if days else {}
    returns, previous = [], 1.0
    for day in days:
        for symbol in wealth:
            wealth[symbol] *= 1 + day["passive_by_symbol"][symbol]
        current = statistics.fmean(wealth.values())
        returns.append(current / previous - 1)
        previous = current
    if returns:
        side = sum(COSTS["base"].values()) / 10000
        returns[0] = (1 + returns[0]) / (1 + side) - 1
        returns[-1] = (1 + returns[-1]) * (1 - side) - 1
    return returns


def choose_validation(summary):
    if not summary["complete_sessions"]:
        return "cash"
    ranking = sorted(FAMILIES, key=lambda f: (-summary["strategies"][f]["base"]["total_return"], f))
    best = ranking[0]
    return best if summary["strategies"][best]["base"]["total_return"] > 0 else "cash"


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")


def report(result):
    lines = [
        "# Historical alpha research",
        "",
        result["assessment"],
        "",
        f"Evidence: **{result['evidence_kind']}**. Selected before test: **{result.get('selected', 'unavailable')}**.",
        "",
        "All returns are fractions of total capital; bearish forecasts receive NO_TRADE.",
        "OHLCV entry/exit and execution costs are modeled, never observed fills.",
        "",
    ]

    for record in result.get("data", []):
        metadata = record["metadata"]
        lines.extend(
            [
                "",
                f"Data: {metadata['source']}; symbols {metadata.get('symbols', [])}; "
                f"{record['bars']} bars; {record['first_start']} to {record['last_end']} UTC epoch; "
                f"adjustment {metadata['adjustment']}; digest {record['digest']}.",
            ]
        )
    lines.extend(
        [
            "",
            f"Local search attempt {result['attempt']}; cumulative registered family hypotheses "
            f"{result['cumulative_hypotheses']} (includes data-unavailable attempts).",
            "",
        ]
    )

    def pct(value):
        return "n/a" if value is None else f"{value:.3%}"

    for period, summary in result.get("periods", {}).items():
        lines.append(
            f"\n{period}: {summary['complete_sessions']}/{summary['requested_sessions']} complete sessions."
        )
        lines.extend(
            [
                "",
                "| Family | Sessions | Trades | Gross | Low net | Base net | Stress net | Base DD | Exposure | Excess/day |",
                "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
            ]
        )
        for family, s in summary["strategies"].items():
            lines.append(
                f"| {family} | {summary['complete_sessions']} | {s['trades']} | "
                + " | ".join(pct(s[c]["total_return"]) for c in ("gross", *COSTS))
                + f" | {pct(s['base']['max_drawdown'])} | {pct(s['mean_market_time_exposure'])} | {pct(s['paired_mean_daily_excess'])} |"
            )
        lines.append("")
        lines.append(
            f"{period} benchmarks (base costs): "
            + "; ".join(
                f"{name} {pct(m['total_return'])}" for name, m in summary["benchmarks"].items()
            )
            + "."
        )
    lines += [
        "",
        "## Interpretation and limits",
        "",
        *[f"- {s}" for s in result["limitations"]],
        "",
        f"Next experiment: {result['next_experiment']}",
        "",
        "See results.json, protocol.json, selection.json, daily.json and trades.csv for reproducible inputs, decisions and calculations.",
    ]
    return "\n".join(lines) + "\n"


def run(
    output,
    start,
    validation_start,
    test_start,
    end,
    symbols=None,
    benchmark="SPY",
    source="alpaca",
    input_path=None,
    registry=None,
):
    symbols = symbols or ["QQQ", "IWM"]
    if len(set([*symbols, benchmark])) != len(symbols) + 1 or not symbols:
        raise ValueError("distinct target symbols and separate benchmark required")
    if not all(s.isalpha() and len(s) <= 5 and s.isupper() for s in [*symbols, benchmark]):
        raise ValueError("invalid equity symbol")
    if not start < validation_start < test_start < end:
        raise ValueError("require chronological start < validation-start < test-start < end")
    sessions = calendar(start, end)
    groups = {
        "train": [s for s in sessions if s["date"] < validation_start],
        "validation": [s for s in sessions if validation_start <= s["date"] < test_start],
        "test": [s for s in sessions if s["date"] >= test_start],
    }
    if not all(groups.values()):
        raise ValueError("each chronological partition needs at least one XNYS session")
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    if any(output.iterdir()):
        raise ValueError("use a fresh historical output directory")
    protocol = {
        "schema": "historical-alpha-v1",
        "start": start,
        "validation_start": validation_start,
        "test_start": test_start,
        "end_exclusive": end,
        "symbols": symbols,
        "benchmark": benchmark,
        "families": list(FAMILIES),
        "params": DEFAULT_PARAMS,
        "costs_per_side_bps": COSTS,
        "strategy_implementation": implementation_hash(),
        "research_implementation": identity(
            [Path(__file__).read_text(), Path(__file__).with_name("historical_data.py").read_text()]
        ),
        "source": source if not input_path else "input",
        "evidence_pool": "historical_only",
        "selection": "maximum validation base-cost total portfolio return; cash if <=0; alphabetical tie",
        "capital": "equal fixed sleeves; idle sleeves stay cash; no leverage or shorts; no interest",
        "timing": "09:33 NY decision; entry 09:34 open; exit 10:34 open; 60 minutes",
        "test_policy": "all five frozen comparisons descriptive; winner frozen before test; no tuning",
        "hypotheses": 5,
        "parameter_configurations_per_family": 1,
        "cost_scenarios": 3,
    }
    write_json(output / "protocol.json", protocol)
    registry = Path(registry) if registry else Path("work/historical-alpha/experiments.jsonl")
    registry.parent.mkdir(parents=True, exist_ok=True)
    # One append per invocation, including failed data access. Keep the same registry
    # across runs; it is a transparent local search history, not a global guarantee.
    prior = registry.read_text().splitlines() if registry.exists() else []
    attempt = len(prior) + 1
    with registry.open("a") as stream:
        stream.write(
            canonical(
                {
                    "attempt": attempt,
                    "protocol_digest": identity(protocol),
                    "protocol": protocol,
                    "output": str(output),
                }
            )
            + "\n"
        )
    limitations = [
        "Retrospective revised OHLCV: availability at bar end assumed; one-minute entry latency; no observed spread, quotes or fills.",
        "Fixed fractional capital sleeves; excludes whole-share rounding, impact, taxes, cash interest and dividends. Passive benchmarks are price returns.",
        "Daily drawdown ignores intraday portfolio peaks/troughs; worst per-trade adverse excursion is reported separately. Sharpe uses daily returns and zero cash yield.",
        "Repeated searching is counted only within the supplied local registry; prior external searches are unknown. The final test is consumed by this run and cannot be reused as untouched evidence.",
        "Missing any required symbol/path excludes the whole session, never invents prices or counts it as zero return; missing-day selection can bias results.",
        "Five frozen families and three declared costs; validation chooses one family without changing parameters. Historical results cannot promote a champion.",
    ]
    result = {
        "protocol": protocol,
        "attempt": attempt,
        "registry": str(registry),
        "cumulative_hypotheses": sum(json.loads(line)["protocol"]["hypotheses"] for line in prior)
        + 5,
        "evidence_kind": "unavailable",
        "data": [],
        "periods": {},
        "skipped": {},
        "limitations": limitations,
        "assessment": "No credible net trading edge established.",
        "next_experiment": "Obtain multi-year QQQ/IWM/SPY minute history; keep these five baselines fixed and reserve a new final year. Validate costs with prospective quotes before considering candidates.",
    }
    all_days, all_trades = [], []
    try:
        inputs = HistoricalInputs(output, sorted([*symbols, benchmark]), source, input_path)
        training, train_trades, skipped = evaluate_sessions(
            inputs, groups["train"], symbols, benchmark
        )
        scales = benchmark_scales(training)
        result["periods"]["train"] = summarize(training, train_trades, scales, len(groups["train"]))
        result["skipped"]["train"] = skipped
        all_days.extend({**d, "period": "train"} for d in training)
        all_trades.extend({**t, "period": "train"} for t in train_trades)
        for period in ("validation", "test"):
            if period == "test":
                result["selected"] = choose_validation(result["periods"]["validation"])
                write_json(
                    output / "selection.json",
                    {
                        "family": result["selected"],
                        "protocol_digest": identity(protocol),
                        "validation_digest": identity(result["periods"]["validation"]),
                        "benchmark_scales_from_train": scales,
                        "test_evaluated": False,
                    },
                )
            days, trades, skipped = evaluate_sessions(inputs, groups[period], symbols, benchmark)
            result["periods"][period] = summarize(days, trades, scales, len(groups[period]))
            result["skipped"][period] = skipped
            all_days.extend({**d, "period": period} for d in days)
            all_trades.extend({**t, "period": period} for t in trades)
        result["data"] = inputs.records
        metadata = [r["metadata"] for r in inputs.records]
        is_fixture = any(
            m.get("availability") == "synthetic_fixture"
            or m.get("source", "").startswith("fixture")
            or m.get("evidence_kind") == "synthetic"
            for m in metadata
        )
        result["evidence_kind"] = (
            "synthetic_fixture_implementation_only" if is_fixture else "historical_market"
        )
        selected = result["selected"]
        result["selected_test"] = (
            result["periods"]["test"]["benchmarks"]["cash"]
            if selected == "cash"
            else result["periods"]["test"]["strategies"][selected]["base"]
        )
        result["further_investigation"] = []
        if not is_fixture:
            for family in FAMILIES:
                stats = result["periods"]["test"]["strategies"][family]
                if (
                    stats["stress"]["total_return"] is not None
                    and stats["stress"]["total_return"] > 0
                    and (stats["paired_mean_daily_excess"] or 0) > 0
                    and result["periods"]["test"]["complete_sessions"] >= 126
                ):
                    result["further_investigation"].append(family)
        if result["further_investigation"]:
            result["assessment"] += (
                " Descriptive leads for further independent testing: "
                + ", ".join(result["further_investigation"])
                + "."
            )
        if is_fixture:
            result["assessment"] = (
                "Synthetic fixture: implementation validation only. Real-market profitability remains unavailable."
            )
        if len(all_days) < 252:
            limitations.insert(
                0,
                f"Only {len(all_days)} complete market sessions across all partitions; insufficient period/regime diversity to establish trading edge.",
            )
    except (ValueError, OSError) as exc:
        result["failure"] = str(exc)
        result["assessment"] = "Real-market profitability results unavailable: " + str(exc)
        write_json(output / "results.json", result)
        (output / "REPORT.md").write_text(report(result))
        raise
    write_json(output / "daily.json", all_days)
    with (output / "trades.csv").open("w", newline="") as stream:
        fields = [
            "period",
            "date",
            "family",
            "symbol",
            "prediction_id",
            "decision_time",
            "signal_end",
            "entry_time",
            "exit_time",
            "capital_fraction",
            "entry_open",
            "exit_open",
            "gross_return",
            "worst_adverse",
            "net_low",
            "net_base",
            "net_stress",
        ]
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(all_trades)
    write_json(output / "results.json", result)
    (output / "REPORT.md").write_text(report(result))
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("start", "validation-start", "test-start", "end"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--input", type=Path)
    parser.add_argument("--source", choices=["alpaca", "firstrate-sample"], default="alpaca")
    parser.add_argument("--symbols", default="QQQ,IWM")
    parser.add_argument("--benchmark", default="SPY")
    parser.add_argument("--registry", type=Path)
    args = parser.parse_args(argv)
    try:
        result = run(
            args.output,
            args.start,
            args.validation_start,
            args.test_start,
            args.end,
            args.symbols.split(","),
            args.benchmark,
            args.source,
            args.input,
            args.registry,
        )
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(json.dumps({"status": "unavailable", "reason": str(exc)}))
        return 2
    print(
        json.dumps(
            {
                "report": str(args.output / "REPORT.md"),
                "selected": result.get("selected"),
                "assessment": result["assessment"],
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
