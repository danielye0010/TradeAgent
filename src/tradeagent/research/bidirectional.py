"""Signed daily SHADOW selection and clearly separated instrument comparisons."""

import random

from ..model import Halt
from .domain import identity
from .put_shadow import PUT_RULE, put_outcome

PROTOCOL = "daily-bidirectional-v3"
ARMS = ("quant_only", "codex_price", "codex_news", "quant_codex", "spy", "cash", "seeded_random")
CAPITAL = 1000
MIN_NET_EDGE = 0.001


def select(eligible, price, news, price_status, news_status, seed, now):
    """Use the three existing families; signed, cost-screened point forecasts only."""
    from .opportunity_workflow import research_support

    def signal(candidate, direction=None):
        costs = candidate["outcome_costs"]
        possibilities = [
            p
            for p in candidate["predictions"]
            if p["direction"] in (-1, 1)
            and p["expected_return"] * p["direction"] > 2 * costs["base_side_cost"] + MIN_NET_EDGE
            and (direction is None or direction == p["direction"])
        ]
        return max(
            possibilities, key=lambda p: (abs(p["expected_return"]), p["strategy_id"]), default=None
        )

    def choose(assessments=None, *, quant=False):
        pool = []
        for c in eligible:
            a = (assessments or {}).get(c["symbol"])
            direction = {"long": 1, "bearish": -1}.get((a or {}).get("stance"))
            if assessments is not None:
                support = research_support(c["scanner"], a, now, allow_bearish=True)
                if not direction or not support["supported"]:
                    continue
            p = signal(c, direction)
            if p:
                pool.append((c, p, a))
        if quant or assessments is None:
            return max(
                pool, key=lambda v: (abs(v[1]["expected_return"]), v[0]["symbol"]), default=None
            )
        return min(
            pool,
            key=lambda v: (v[2]["rank"], -abs(v[1]["expected_return"]), v[0]["symbol"]),
            default=None,
        )

    selections = {
        "quant_only": (choose(), "VALID"),
        "codex_price": (choose(price), price_status),
        "codex_news": (choose(news), news_status),
        "quant_codex": (choose(news, quant=True), news_status),
        "seeded_random": (
            random.Random(seed).choice(
                [None, *[(c, signal(c), None) for c in eligible if signal(c)]]
            ),
            "VALID",
        ),
    }
    arms = {}
    for name, (chosen, status) in selections.items():
        if status != "VALID":
            chosen = None
        direction = chosen[1]["direction"] if chosen else 0
        arms[name] = {
            "symbol": chosen[0]["symbol"] if chosen else None,
            "shadow_direction": direction,
            "predicted_direction": direction,
            "prediction_id": chosen[1]["prediction_id"] if chosen else None,
            "expected_return": chosen[1]["expected_return"] if chosen else None,
            "decision": {1: "LONG", -1: "BEARISH", 0: "CASH"}[direction],
            "instrument": {1: "LONG_EQUITY", -1: "LONG_PUT", 0: "CASH"}[direction],
            "status": "VALID" if status == "VALID" else "UNAVAILABLE",
            "plan": None,
            "research_only": True,
            "capital_dollars": CAPITAL,
            "reason": "signed uncalibrated forecast exceeds estimated round-trip costs plus 10bp; no proven profitability",
        }
    arms["spy"] = {
        "symbol": "SPY",
        "shadow_direction": 1,
        "predicted_direction": 1,
        "decision": "LONG",
        "instrument": "LONG_EQUITY",
        "status": "VALID",
        "plan": None,
        "research_only": True,
        "capital_dollars": CAPITAL,
    }
    arms["cash"] = {
        "symbol": None,
        "shadow_direction": 0,
        "predicted_direction": 0,
        "decision": "CASH",
        "instrument": "CASH",
        "status": "VALID",
        "plan": None,
        "research_only": True,
        "capital_dollars": CAPITAL,
    }
    return {name: arms[name] for name in ARMS}


def directional(outcome, direction, costs):
    if not direction:
        return {
            "status": "AVAILABLE",
            "direction": 0,
            "instrument": "CASH",
            "gross": 0,
            "net_return": 0,
            "pnl_dollars": 0,
            "capital_dollars": CAPITAL,
            "entry_price": None,
            "exit_price": None,
        }
    result = {
        **outcome,
        "status": "AVAILABLE",
        "direction": direction,
        "capital_dollars": CAPITAL,
        "instrument": "LONG_EQUITY" if direction > 0 else "THEORETICAL_BEARISH_UNDERLYING",
        "gross": direction * outcome["gross"],
        "underlying_gross": outcome["gross"],
        "predicted_direction": direction,
    }
    for name in ("base", "stress"):
        side = costs[f"{name}_side_cost"]
        net = (
            (1 + outcome["gross"]) * (1 - side) / (1 + side) - 1
            if direction > 0
            else -outcome["gross"] - side * (2 + outcome["gross"])
        )
        result[f"{name}_net"] = net
    result["net_return"] = result["base_net"]
    result["pnl_dollars"] = CAPITAL * result["net_return"]
    if direction < 0:
        result["price_model"] = (
            "theoretical negative underlying exposure on $1000 reference notional; estimated costs; no borrow/margin/fills; not an executable short or Put return"
        )
    return result


def resolve_arms(decision, observed, option_observations):
    costs = {c["symbol"]: c["outcome_costs"] for c in decision["quantitative_observations"]}
    quotes = {
        (q["symbol"], q["phase"]): q.get("quote")
        for q in option_observations
        if q["decision_id"] == decision["decision_id"]
    }
    arms = {}
    for name, arm in decision["comparisons"].items():
        if arm["status"] == "UNAVAILABLE":
            arms[name] = None
            continue
        symbol, direction = arm["symbol"], arm["shadow_direction"]
        benchmark = directional(observed.get(symbol, {}), direction, costs.get(symbol, {}))
        actual = (
            put_outcome(
                decision.get("put_selections", {}).get(symbol),
                quotes.get((symbol, "entry")),
                quotes.get((symbol, "exit")),
                decision["outcome_entry_time"],
                decision["outcome_exit_time"],
            )
            if direction < 0
            else benchmark
        )
        arms[name] = {
            "symbol": symbol,
            "direction": direction,
            "decision": arm["decision"],
            "directional_benchmark": benchmark,
            "instrument_simulation": actual,
        }
    return arms


def observe_options(store, reader, now):
    """Called by manual pipeline ticks. Missed endpoints stay unavailable, never backfill."""
    saved = {
        (q["decision_id"], q["symbol"], q["phase"]) for q in store.records("option_observations")
    }
    for d in store.records("decisions"):
        if d.get("research_protocol") != PROTOCOL:
            continue
        for symbol, selection in d.get("put_selections", {}).items():
            if not selection:
                continue
            for phase in ("entry", "exit"):
                key = (d["decision_id"], symbol, phase)
                target = d[f"outcome_{phase}_time"]
                if key in saved or now < target:
                    continue
                quote, reason = None, "missed actual quote observation window"
                if now < target + 60 and reader:
                    try:
                        options = reader.quotes([selection["contract"]])
                        quote = options[0] if len(options) == 1 else None
                        reason = "actual quote collected" if quote else "option quote missing"
                    except (Halt, ValueError, OSError, KeyError, TypeError):
                        reason = "option quote read unavailable"
                item = {
                    "decision_id": d["decision_id"],
                    "symbol": symbol,
                    "phase": phase,
                    "target_time": target,
                    "recorded_at": max(now, quote["book"]["available_at"]) if quote else now,
                    "quote": quote,
                    "status": "OBSERVED" if quote else "UNAVAILABLE",
                    "reason": reason,
                }
                store.save("option_observations", item, identity(key), item["recorded_at"])
                saved.add(key)


def compare(store):
    decisions = {
        d["decision_id"]: d
        for d in store.records("decisions")
        if d.get("research_protocol") == PROTOCOL
    }
    outcomes = [o for o in store.records("outcomes") if o["decision_id"] in decisions]
    pools = {}
    for pool in sorted({d["evidence_kind"] for d in decisions.values()}):
        rows = [o for o in outcomes if o["evidence_kind"] == pool]
        sleeves = {}
        for sleeve in ("instrument_simulation", "directional_benchmark"):
            # Every reported difference uses the same matched sessions, including SPY/CASH.
            paired_rows = [
                o
                for o in rows
                if all(
                    o["comparisons"].get(n) and o["comparisons"][n][sleeve]["status"] == "AVAILABLE"
                    for n in ARMS
                    if n != "seeded_random"
                )
            ]
            stats = {}
            for name in ARMS:
                values = [
                    o["comparisons"][name][sleeve]
                    for o in paired_rows
                    if o["comparisons"].get(name)
                    and o["comparisons"][name][sleeve]["status"] == "AVAILABLE"
                ]
                observed = [
                    o["comparisons"][name][sleeve]
                    for o in rows
                    if o["comparisons"].get(name)
                    and o["comparisons"][name][sleeve]["status"] == "AVAILABLE"
                ]
                stats[name] = {
                    "observed_days": len(observed),
                    "observed_mean_net_return": sum(v["net_return"] for v in observed)
                    / len(observed)
                    if observed
                    else None,
                    "observed_mean_pnl_dollars": sum(v["pnl_dollars"] for v in observed)
                    / len(observed)
                    if observed
                    else None,
                    "paired_days": len(values),
                    "mean_net_return": sum(v["net_return"] for v in values) / len(values)
                    if values
                    else None,
                    "mean_pnl_dollars": sum(v["pnl_dollars"] for v in values) / len(values)
                    if values
                    else None,
                    "bullish_days": sum(
                        o["comparisons"].get(name, {}).get("direction") == 1
                        for o in paired_rows
                        if o["comparisons"].get(name)
                    ),
                    "bearish_days": sum(
                        o["comparisons"].get(name, {}).get("direction") == -1
                        for o in paired_rows
                        if o["comparisons"].get(name)
                    ),
                    "unavailable_days": sum(
                        not o["comparisons"].get(name)
                        or o["comparisons"][name][sleeve]["status"] != "AVAILABLE"
                        for o in rows
                    ),
                }
            differences = {
                name: {
                    baseline: {
                        "paired_days": len(paired_rows),
                        "mean_net_return_difference": sum(
                            o["comparisons"][name][sleeve]["net_return"]
                            - o["comparisons"][baseline][sleeve]["net_return"]
                            for o in paired_rows
                        )
                        / len(paired_rows)
                        if paired_rows
                        else None,
                        "mean_pnl_difference_dollars": sum(
                            o["comparisons"][name][sleeve]["pnl_dollars"]
                            - o["comparisons"][baseline][sleeve]["pnl_dollars"]
                            for o in paired_rows
                        )
                        / len(paired_rows)
                        if paired_rows
                        else None,
                    }
                    for baseline in ("quant_only", "spy", "cash")
                }
                for name in ARMS
                if name != "seeded_random"
            }
            sleeves[sleeve] = {
                "paired_differences": differences,
                "arms": {n: stats[n] for n in ARMS if n != "seeded_random"},
                "secondary_random": stats["seeded_random"],
            }
        pools[pool] = {
            "resolved_sessions": len(rows),
            "comparisons": sleeves,
            "selections_and_prices": [o["comparisons"] for o in rows],
        }
    return {
        "protocol": PROTOCOL,
        "capital_dollars": CAPITAL,
        "put_rule": PUT_RULE,
        "capital_basis": "instrument sleeve: $1000 cash budget, whole Puts with idle cash or fractional unleveraged equity; returns/P&L divide by $1000, not premium",
        "directional_basis": "separate $1000 underlying reference notional; bearish exposure is theoretical with unbounded short risk, never mixed into instrument sleeve",
        "evidence_pools": pools,
        "orders_submitted": 0,
    }


def pnl_report(store):
    """Readable derivative of frozen decisions/outcomes; no substitutes for missing prices."""
    decisions = [d for d in store.records("decisions") if d.get("research_protocol") == PROTOCOL]
    lines = [
        "# Bidirectional SHADOW P&L",
        "",
        "$1,000 capital per strategy, including unused option cash. Modeled prices/P&L, not broker fills.",
        "",
    ]
    if not decisions:
        return "\n".join(lines + ["PENDING: no prospective decision recorded.", ""])
    decision = decisions[-1]
    from .domain import iso

    lines += [
        f"Decision: {iso(decision['decision_time'])}",
        f"Entry window: {iso(decision['outcome_entry_time'])}; exit window: {iso(decision['outcome_exit_time'])} (60 seconds each).",
        "",
    ]
    outcome = next(
        (o for o in store.records("outcomes") if o["decision_id"] == decision["decision_id"]), None
    )
    labels = {
        "quant_only": "Quant Only",
        "codex_price": "Codex Price",
        "codex_news": "Codex News",
        "quant_codex": "Quant + Codex",
        "spy": "SPY",
        "cash": "CASH",
        "seeded_random": "Random (secondary)",
    }

    def number(value, percent=False):
        return (
            "—"
            if value is None
            else f"{value * 100 if percent else value:.2f}" + ("%" if percent else "")
        )

    for sleeve, title in (
        ("instrument_simulation", "Funded equity/Put/cash simulations"),
        (
            "directional_benchmark",
            "Theoretical underlying benchmark — bearish exposure is non-executable, not Put P&L",
        ),
    ):
        lines += [
            f"## {title}",
            "",
            "| Strategy | Symbol | Direction | Instrument | Entry | Exit | Put contracts | Premium $ | Idle cash $ | P&L $ | Return | Status |",
            "| --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
        ]
        notes = []
        for name in ARMS:
            arm = decision["comparisons"][name]
            resolved = (outcome or {}).get("comparisons", {}).get(name)
            price = resolved[sleeve] if resolved else {}
            status = price.get(
                "status", "UNAVAILABLE" if arm["status"] == "UNAVAILABLE" else "PENDING"
            )
            instrument = price.get(
                "instrument",
                arm["instrument"]
                if sleeve == "instrument_simulation"
                else "THEORETICAL_BEARISH_UNDERLYING"
                if arm["shadow_direction"] < 0
                else arm["instrument"],
            )
            lines.append(
                "| "
                + " | ".join(
                    [
                        labels[name],
                        arm["symbol"] or "—",
                        arm["decision"],
                        instrument,
                        number(price.get("entry_price")),
                        number(price.get("exit_price")),
                        str(price.get("contracts", "—")),
                        number(price.get("premium_required")),
                        number(price.get("idle_cash")),
                        number(price.get("pnl_dollars")),
                        number(price.get("net_return"), True),
                        status,
                    ]
                )
                + " |"
            )
            if price.get("reason"):
                notes.append(f"{name}: {price['reason']}")
        lines += ["", *notes, ""]
    lines += [
        "Equity uses minute prices and frozen estimated costs. Put uses observed entry ask / exit bid, multiplier 100; unknown option fees remain unobserved. Missing prices/P&L are not zero.",
        "",
    ]
    return "\n".join(lines)
