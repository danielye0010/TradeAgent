"""Versioned long Call/Put SHADOW lifecycle, using only official observed quotes."""

import json
import math
import sqlite3
import subprocess
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from ..model import Halt
from . import bidirectional
from .domain import OptionBook, identity
from .opportunities import research_snapshot
from .put_shadow import PUT_RULE, OptionAccessDenied, PutReader, liquid

PROTOCOL = "daily-call-put-v4"
ARMS = bidirectional.ARMS
CAPITAL = 1000
FEE = 0.65
RULE = {
    **PUT_RULE,
    "kinds": ["call", "put"],
    "fees_per_contract_per_side": FEE,
    "fees": "modeled $0.65 per contract per side; not a Robinhood fee observation",
    "allocation": "entry ask and size; reserve both modeled fees; never resize at exit",
}
NY = ZoneInfo("America/New_York")


class OptionReader(PutReader):
    def select(self, symbol, spot, *, kind, deadline=None):
        return super().select(
            symbol, spot, deadline=deadline, kind=kind, strict_quotes=True, fees_per_contract=FEE
        )


def access_status(directory):
    path = Path(directory) / "option-access.json"
    return json.loads(path.read_text()) if path.exists() else {"status": "UNVERIFIED"}


def block_access(directory, now):
    from ..opportunity_cli import write

    value = {
        "status": "BLOCKED",
        "observed_at": now,
        "http_status": 403,
        "reason": "Authenticated official option quote access denied (HTTP 403); no automatic retry",
        "required_action": "Robinhood/account owner must restore read-only MCP option quote access; reverify before clearing this blocker",
    }
    write(Path(directory) / "option-access.json", value)
    return value


def codex_slot_available():
    try:
        result = subprocess.run(
            [
                "systemctl",
                "--user",
                "is-active",
                "--quiet",
                "tradeagent-daily-research.service",
                "tradeagent-daily-news.service",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    # systemctl returns 3 only when both known services are inactive; errors fail closed.
    return result.returncode == 3


def discover(pipeline, report, data, deadline):
    selections = {
        f"{c['symbol']}:{kind}": {
            "status": "UNAVAILABLE",
            "selection": None,
            "reason": "option discovery did not complete before its deadline",
        }
        for c in report["candidates"]
        for kind in ("call", "put")
    }
    if access_status(pipeline.directory)["status"] == "BLOCKED":
        for row in selections.values():
            row["reason"] = "persisted HTTP 403 option-access blocker; no request made"
        return selections
    try:
        with pipeline.put_reader_factory(
            Path(__file__).resolve().parents[3], pipeline.helper
        ) as bridge:
            reader = OptionReader(bridge, pipeline.clock)
            for candidate in report["candidates"]:
                for kind in ("call", "put"):
                    if pipeline.clock() >= deadline:
                        return selections
                    key = f"{candidate['symbol']}:{kind}"
                    try:
                        snapshot = research_snapshot(data, candidate["symbol"], pipeline.clock())
                        chosen = reader.select(
                            candidate["symbol"],
                            (snapshot.bid + snapshot.ask) / 2,
                            kind=kind,
                            deadline=deadline,
                        )
                        selections[key] = {
                            "status": "SELECTED" if chosen else "NO_ELIGIBLE_CONTRACT",
                            "selection": chosen,
                            "reason": None
                            if chosen
                            else "no genuine contract satisfies DTE/liquidity/whole-contract fee-inclusive capital rule",
                        }
                    except OptionAccessDenied:
                        block_access(pipeline.directory, pipeline.clock())
                        for row in selections.values():
                            if row["status"] == "UNAVAILABLE":
                                row["reason"] = "option quote HTTP 403; remaining reads suppressed"
                        return selections
                    except (Halt, ValueError, OSError, KeyError, TypeError):
                        selections[key]["reason"] = "genuine contract/quote data unavailable"
    except OptionAccessDenied:
        block_access(pipeline.directory, pipeline.clock())
    except (Halt, ValueError, OSError, KeyError, TypeError):
        pass
    return selections


def select(eligible, price, news, price_status, news_status, seed, now, selections):
    arms = bidirectional.select(eligible, price, news, price_status, news_status, seed, now)
    for name, arm in arms.items():
        if name in {"spy", "cash"} or arm["status"] != "VALID" or not arm["shadow_direction"]:
            continue
        direction, symbol = arm["shadow_direction"], arm["symbol"]
        kind = "call" if direction > 0 else "put"
        key = f"{symbol}:{kind}"
        discovered = selections.get(
            key, {"status": "UNAVAILABLE", "reason": "no saved option discovery"}
        )
        arm.update(
            instrument="LONG_" + kind.upper(),
            option_key=key,
            option_status=discovered["status"],
            forecast_symbol=symbol,
            forecast_direction=direction,
        )
        if discovered["status"] == "NO_ELIGIBLE_CONTRACT":
            arm.update(
                symbol=None,
                shadow_direction=0,
                predicted_direction=0,
                decision="CASH",
                instrument="CASH",
                reason=discovered["reason"],
            )
        else:
            arm["reason"] += "; underlying directional screen is not positive option expected value"
    return arms


def matching_quote(selection, quote, target):
    if not selection or not quote:
        return False
    book = OptionBook.from_dict(quote["book"])
    return (
        all(
            getattr(book, k) == selection["book"][k]
            for k in ("contract_id", "underlying", "kind", "expiration", "strike", "multiplier")
        )
        and target <= book.asof <= book.available_at < target + 60
        and selection["book"]["available_at"] <= target
    )


def allocate(selection, quote, target, fee=FEE):
    if not math.isfinite(fee) or fee < 0 or not matching_quote(selection, quote, target):
        return None
    if (
        type(quote["ask_size"]) is not int
        or type(quote["bid_size"]) is not int
        or not liquid(quote, quote["book"]["available_at"])
    ):
        return None
    ask = quote["book"]["ask"]
    quantity = min(int(CAPITAL // (ask * 100 + 2 * fee)), quote["ask_size"])
    if quantity < 1:
        return None
    premium = quantity * ask * 100
    return {
        "contracts": quantity,
        "premium_required": premium,
        "entry_fee": quantity * fee,
        "exit_fee_reserve": quantity * fee,
        "idle_cash": CAPITAL - premium - quantity * fee,
        "uncommitted_cash": CAPITAL - premium - 2 * quantity * fee,
    }


def observe(pipeline):
    """One append per frozen contract/endpoint; no late quote backfill or 403 replay."""
    store, clock = pipeline.store, pipeline.clock
    saved = {
        (r["decision_id"], r["selection_key"], r["phase"])
        for r in store.records("option_observations")
        if "selection_key" in r
    }
    due = []
    now = clock()
    for d in store.records("decisions"):
        if d.get("research_protocol") != PROTOCOL:
            continue
        keys = {
            a["option_key"]
            for a in d["comparisons"].values()
            if a.get("option_status") == "SELECTED" and a["shadow_direction"]
        }
        for key in sorted(keys):
            for phase in ("entry", "exit"):
                target = d[f"outcome_{phase}_time"]
                if (d["decision_id"], key, phase) not in saved and now >= target:
                    due.append((d, key, phase, target))
    if not due:
        return
    reader, context, entered = None, None, False
    try:
        if (
            any(clock() < target + 60 for _, _, _, target in due)
            and access_status(pipeline.directory)["status"] != "BLOCKED"
        ):
            try:
                context = pipeline.put_reader_factory(
                    Path(__file__).resolve().parents[3], pipeline.helper
                )
                bridge = context.__enter__()
                entered = True
                reader = OptionReader(bridge, clock)
            except OptionAccessDenied:
                block_access(pipeline.directory, clock())
            except (Halt, ValueError, OSError, KeyError, TypeError):
                reader = None
        for d, key, phase, target in due:
            selection = d["option_selections"][key]["selection"]
            quote, allocation, valid = None, None, False
            reason = "missed quote observation window; no historical replacement"
            if clock() < target + 60:
                reason = "option quote unavailable or persisted access blocker"
                if reader:
                    try:
                        reader.deadline = target + 60
                        rows = reader.quotes([selection["contract"]])
                        quote = rows[0] if len(rows) == 1 else None
                        valid = matching_quote(selection, quote, target)
                        if phase == "entry":
                            allocation = allocate(
                                selection,
                                quote,
                                target,
                                d["option_rule"]["fees_per_contract_per_side"],
                            )
                            valid = allocation is not None
                        reason = (
                            "genuine observed quote"
                            if valid
                            else "quote identity, timing, liquidity or affordability invalid"
                        )
                    except OptionAccessDenied:
                        block_access(pipeline.directory, clock())
                        reader = None
                        reason = "option quote HTTP 403; further requests suppressed"
                    except (Halt, ValueError, OSError, KeyError, TypeError):
                        reason = "option quote read unavailable"
            recorded = clock()
            item = {
                "decision_id": d["decision_id"],
                "selection_key": key,
                "symbol": selection["book"]["underlying"],
                "kind": selection["book"]["kind"],
                "phase": phase,
                "target_time": target,
                "recorded_at": recorded,
                "quote": quote,
                "allocation": allocation,
                "status": "OBSERVED" if valid else "UNAVAILABLE",
                "reason": reason,
            }
            store.save(
                "option_observations",
                item,
                identity([PROTOCOL, d["decision_id"], key, phase]),
                recorded,
            )
    finally:
        if entered:
            context.__exit__(None, None, None)


def option_outcome(selection, entry, exit_quote, entry_time, exit_time, fee=FEE, kind="put"):
    result = {
        "status": "UNAVAILABLE",
        "instrument": "LONG_" + kind.upper(),
        "direction": 1 if kind == "call" else -1,
        "capital_dollars": CAPITAL,
        "net_return": None,
        "pnl_dollars": None,
        "entry_price": None,
        "exit_price": None,
        "reason": "missing usable prospective entry/exit quote or allocation",
    }
    if (
        not selection
        or not entry
        or not exit_quote
        or entry["status"] != "OBSERVED"
        or exit_quote["status"] != "OBSERVED"
    ):
        return result
    try:
        start, end = entry["quote"], exit_quote["quote"]
        allocation = allocate(selection, start, entry_time, fee)
        if (
            allocation is None
            or allocation != entry.get("allocation")
            or not matching_quote(selection, end, exit_time)
            or not matching_quote(selection, start, entry_time)
            or end["book"]["asof"] <= start["book"]["asof"]
            or start["book"]["kind"] != kind
        ):
            return result
        count = allocation["contracts"]
        if end["bid_size"] < count:
            return {
                **result,
                "reason": "exit bid size cannot cover frozen entry quantity; no resizing",
            }
        pnl = count * ((end["book"]["bid"] - start["book"]["ask"]) * 100 - 2 * fee)
        return {
            **result,
            **allocation,
            "status": "AVAILABLE",
            "reason": None,
            "contract_id": start["book"]["contract_id"],
            "symbol": start["book"]["underlying"],
            "expiration": start["book"]["expiration"],
            "strike": start["book"]["strike"],
            "multiplier": 100,
            "entry_price": start["book"]["ask"],
            "exit_price": end["book"]["bid"],
            "entry_time": start["book"]["asof"],
            "exit_time": end["book"]["asof"],
            "entry_observed_at": start["book"]["available_at"],
            "exit_observed_at": end["book"]["available_at"],
            "known_modeled_fees": 2 * fee * count,
            "fee_status": "MODELED_ASSUMPTION",
            "pnl_dollars": pnl,
            "net_return": pnl / CAPITAL,
            "return_on_premium": pnl / allocation["premium_required"],
            "price_model": "genuine entry ask / later exit bid; whole contracts; quoted-size SHADOW, not actual fills",
        }
    except (ValueError, KeyError, TypeError, OverflowError):
        return result


def resolve_arms(decision, observed, observations):
    quotes = {
        (q["selection_key"], q["phase"]): q
        for q in observations
        if q["decision_id"] == decision["decision_id"] and "selection_key" in q
    }
    costs = {c["symbol"]: c["outcome_costs"] for c in decision["quantitative_observations"]}
    result = {}
    for name, arm in decision["comparisons"].items():
        if arm["status"] != "VALID":
            result[name] = None
            continue
        symbol, direction = arm["symbol"], arm["shadow_direction"]
        reference = arm.get("forecast_symbol", symbol)
        forecast_direction = arm.get("forecast_direction", direction)
        directional = bidirectional.directional(
            observed.get(reference, {}), forecast_direction, costs.get(reference, {})
        )
        equity = bidirectional.directional(
            observed.get(reference, {}), 1 if reference else 0, costs.get(reference, {})
        )
        if not direction:
            actual = bidirectional.directional({}, 0, {})
        elif name == "spy":
            actual = equity
        else:
            key = arm["option_key"]
            actual = option_outcome(
                decision["option_selections"].get(key, {}).get("selection"),
                quotes.get((key, "entry")),
                quotes.get((key, "exit")),
                decision["outcome_entry_time"],
                decision["outcome_exit_time"],
                decision["option_rule"]["fees_per_contract_per_side"],
                "call" if direction > 0 else "put",
            )
        result[name] = {
            "symbol": symbol,
            "direction": direction,
            "decision": arm["decision"],
            "instrument_simulation": actual,
            "equity_benchmark": equity,
            "directional_benchmark": directional,
            "benchmark_symbol": reference,
            "direction_correct": forecast_direction * observed[reference]["gross"] > 0
            if forecast_direction
            else None,
        }
    return result


def compare(store):
    result = bidirectional.compare(
        store,
        protocol=PROTOCOL,
        sleeve_names=("instrument_simulation", "equity_benchmark", "directional_benchmark"),
    )
    result.pop("put_rule", None)
    result["option_rule"] = RULE
    result["capital_basis"] = (
        "$1000 cash budget; whole long Calls/Puts, idle cash and explicit modeled fees; SPY/equity and CASH remain separate controls"
    )
    ids = {
        d["decision_id"]
        for d in store.records("decisions")
        if d.get("research_protocol") == PROTOCOL
    }
    for pool, data in result["evidence_pools"].items():
        rows = [
            o
            for o in store.records("outcomes")
            if o["decision_id"] in ids and o["evidence_kind"] == pool
        ]
        data["direction_accuracy"] = {}
        for name in ARMS:
            flags = [
                o["comparisons"][name]["direction_correct"]
                for o in rows
                if o["comparisons"].get(name)
                and o["comparisons"][name]["direction_correct"] is not None
            ]
            data["direction_accuracy"][name] = {
                "days": len(flags),
                "correct": sum(flags),
                "fraction": sum(flags) / len(flags) if flags else None,
            }
    return result


def activation_ready(directory):
    """Require successful genuine prospective Call AND Put cycles before timer enablement."""
    directory = Path(directory)
    if (
        access_status(directory)["status"] == "BLOCKED"
        or not (directory / "experience.sqlite3").is_file()
    ):
        return False
    db = sqlite3.connect(
        (directory / "experience.sqlite3").resolve().as_uri() + "?mode=ro", uri=True
    )
    try:
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")
        decisions = {
            d["decision_id"]: d
            for (p,) in db.execute("SELECT payload FROM opportunity_decisions")
            if (d := json.loads(p)).get("research_protocol") == PROTOCOL
            and d["evidence_kind"] == "prospective"
            and d["source"] == "robinhood-opportunity-minute-v1"
        }
        observations = [
            json.loads(p)
            for (p,) in db.execute("SELECT payload FROM opportunity_option_observations")
        ]
        kinds = set()
        for (p,) in db.execute("SELECT payload FROM opportunity_outcomes"):
            o = json.loads(p)
            if o["decision_id"] not in decisions or o["evidence_kind"] != "prospective":
                continue
            d = decisions[o["decision_id"]]
            for resolved in resolve_arms(d, o["candidate_outcomes"], observations).values():
                if resolved and resolved["instrument_simulation"]["status"] == "AVAILABLE":
                    kinds.add(resolved["instrument_simulation"]["instrument"])
        return {"LONG_CALL", "LONG_PUT"} <= kinds
    except (sqlite3.Error, ValueError, KeyError, TypeError):
        return False
    finally:
        db.close()


def report(store, now, access):
    def number(value, scale=1):
        return "—" if value is None else f"{value * scale:.4f}"

    def stamp(value):
        return datetime.fromtimestamp(value, NY).isoformat()

    day = datetime.fromtimestamp(now, NY).date().isoformat()
    lines = [
        f"# Call/Put SHADOW — {day}",
        "",
        f"Protocol: {PROTOCOL}; generated {stamp(now)}.",
        f"Option access: {access['status']}. {access.get('reason', '')}",
        "$1,000 per arm. Observed quote simulation, no orders or actual fills. Missing prices/P&L are never zero.",
        "",
    ]
    decisions = [d for d in store.records("decisions") if d.get("research_protocol") == PROTOCOL]
    today = [
        d
        for d in decisions
        if datetime.fromtimestamp(d["decision_time"], NY).date().isoformat() == day
    ]
    if not today:
        lines += [
            "PENDING: no frozen prospective decision for today; no prior-day substitution.",
            "",
        ]
    else:
        d = today[-1]
        o = next(
            (o for o in store.records("outcomes") if o["decision_id"] == d["decision_id"]), None
        )
        lines += [
            f"Decision {stamp(d['decision_time'])}; entry {stamp(d['outcome_entry_time'])}; exit {stamp(d['outcome_exit_time'])}; 60-second quote windows.",
            f"Resolution: {'RESOLVED' if o else 'PENDING'}. Bid/ask timestamps are retained in option_observations.",
            "",
            "| Arm | Symbol | Instrument | Entry ask | Exit bid | Contracts | Premium $ | Idle cash $ | Modeled fees $ | P&L $ | Net % | Status |",
            "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
        ]
        notes = []
        for name in ARMS:
            a = d["comparisons"][name]
            r = (o or {}).get("comparisons", {}).get(name)
            p = r["instrument_simulation"] if r else {}
            status = p.get(
                "status",
                "UNAVAILABLE"
                if a["status"] != "VALID" or a.get("option_status") == "UNAVAILABLE"
                else "PENDING",
            )
            values = [
                name,
                a["symbol"] or "CASH" if a["status"] == "VALID" else "UNAVAILABLE",
                a["instrument"],
                number(p.get("entry_price")),
                number(p.get("exit_price")),
                str(p.get("contracts", "—")),
                number(p.get("premium_required")),
                number(p.get("idle_cash")),
                number(p.get("known_modeled_fees")),
                number(p.get("pnl_dollars")),
                number(p.get("net_return"), 100),
                status,
            ]
            lines.append("| " + " | ".join(values) + " |")
            if p.get("reason"):
                notes.append(name + ": " + p["reason"])
        lines += [
            "",
            *notes,
            "",
            "| Arm | Direction correct | Long equity P&L $ | Directional reference P&L $ |",
            "| --- | --- | ---: | ---: |",
        ]
        for name in ARMS:
            r = (o or {}).get("comparisons", {}).get(name) or {}
            lines.append(
                f"| {name} | {r.get('direction_correct', 'UNAVAILABLE')} | {number(r.get('equity_benchmark', {}).get('pnl_dollars'))} | {number(r.get('directional_benchmark', {}).get('pnl_dollars'))} |"
            )
        lines += ["", "Contract selections (frozen before entry):", ""]
        for key, value in d["option_selections"].items():
            s = value.get("selection")
            lines.append(
                f"- {key}: {value['status']}; "
                + (
                    f"{s['book']['contract_id']} / {s['book']['expiration']} / strike {s['book']['strike']}"
                    if s
                    else value.get("reason", "")
                )
            )
    lines += [
        "",
        "Modeled fee: $0.65 per contract per side; not an observed Robinhood fee schedule. Idle cash is cash remaining after premium and entry fees; exit fees are reserved within it.",
        "LONG EQUITY and SPY use the established frozen equity cost model. The bearish directional reference is theoretical underlying exposure, never option P&L or positive option EV.",
        "",
        "## Cumulative saved results",
        "",
    ]
    for pool, data in compare(store)["evidence_pools"].items():
        lines.append(
            f"{pool}: {data['resolved_sessions']} resolved sessions. Whole sessions, not contracts, are independent comparison units."
        )
        for name, stats in data["comparisons"]["instrument_simulation"]["arms"].items():
            lines.append(
                f"- {name}: {stats['observed_days']} observed days, mean net {number(stats['observed_mean_net_return'], 100)}%; unavailable {stats['unavailable_days']} days; direction accuracy {data['direction_accuracy'][name]}."
            )
    return "\n".join(lines) + "\n"
