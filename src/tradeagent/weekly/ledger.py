"""Deterministic virtual cash/lot ledger; each mutation is one atomic journal event."""

from datetime import date

from .protocol import CONFIG, dec, instant, next_session, quantity


def mature(state, day):
    keep = []
    for item in state["pending"]:
        if item.get("settles") and item["settles"] <= str(day):
            state["settled"] = str(dec(state["settled"]) + dec(item["amount"]))
        else:
            keep.append(item)
    state["pending"] = keep
    return state


def cash(state, *, settled_only=False):
    return dec(state["settled"]) + (
        0
        if settled_only
        else sum(
            (
                dec(x["amount"])
                for x in state["pending"]
                if x.get("settles") and x.get("fundable", True)
            ),
            dec(0),
        )
    )


def total_cash(state):
    return dec(state["settled"]) + sum((dec(x["amount"]) for x in state["pending"]), dec(0))


def shares(state, symbol):
    return sum((dec(lot["qty"]) for lot in state["positions"].get(symbol, [])), dec(0))


def wac(state, symbol):
    lots = state["positions"].get(symbol, [])
    qty = shares(state, symbol)
    return sum((dec(x["qty"]) * dec(x["unit_cost"]) for x in lots), dec(0)) / qty if qty else dec(0)


def nav(state, prices):
    if any(s not in prices or prices[s] is None for s in state["positions"]):
        return None
    return total_cash(state) + sum(
        (shares(state, s) * dec(prices[s]) for s in state["positions"]), dec(0)
    )


def record(store, key, book, state, **detail):
    store.add(key, "ledger", {"book": book, "after": state, **detail})


def buy(store, key, book, symbol, requested, quote, day, *, at):
    if store.get(key):
        return True
    state = mature(store.state(book), day)
    price = dec(quote["ask"]) * (1 + dec(CONFIG["slippage_per_side"]))
    funds = cash(state, settled_only=book.startswith("settled:"))
    qty = quantity(min(dec(requested), funds / price))
    if qty <= 0:
        return False
    amount, remaining, unlock = qty * price, qty * price, str(day)
    used = min(dec(state["settled"]), remaining)
    state["settled"] = str(dec(state["settled"]) - used)
    remaining -= used
    for item in sorted(state["pending"], key=lambda x: x.get("settles") or "9999"):
        if not remaining:
            break
        if not item.get("settles") or not item.get("fundable", True) or book.startswith("settled:"):
            continue
        used = min(dec(item["amount"]), remaining)
        item["amount"] = str(dec(item["amount"]) - used)
        remaining -= used
        if used:
            unlock = max(unlock, item["settles"])
    if remaining:
        raise ValueError("ledger purchase funding conservation failed")
    state["pending"] = [x for x in state["pending"] if dec(x["amount"])]
    state["positions"].setdefault(symbol, []).append(
        {"qty": str(qty), "unit_cost": str(price), "opened_at": at, "unlock": unlock}
    )
    average = wac(state, symbol)
    for lot in state["positions"][symbol]:
        lot["unit_cost"] = str(average)
    mid = (dec(quote["bid"]) + dec(quote["ask"])) / 2
    cost = qty * (price - mid)
    state["costs"] = str(dec(state["costs"]) + cost)
    state["turnover"] = str(dec(state["turnover"]) + amount)
    record(
        store,
        key,
        book,
        state,
        symbol=symbol,
        side="buy",
        qty=str(qty),
        price=str(price),
        notional=str(amount),
        modeled_cost=str(cost),
        quote=quote,
        date=str(day),
        funding_unlock=unlock,
    )
    return True


def sell(store, key, book, symbol, requested, quote, day, *, at, reason):
    if store.get(key):
        return True
    state = mature(store.state(book), day)
    lots = state["positions"].get(symbol, [])
    if not lots:
        return True
    if any(x["unlock"] > str(day) for x in lots):
        deferred = key + ":deferred:" + str(day)
        if not store.get(deferred):
            store.add(
                deferred,
                "delay",
                {
                    "book": book,
                    "symbol": symbol,
                    "reason": "funding not settled; no GFV",
                    "first_allowed_date": max(x["unlock"] for x in lots),
                    "date": str(day),
                    "phase": reason,
                },
            )
        return False
    qty = quantity(min(dec(requested), shares(state, symbol)))
    if qty <= 0:
        return True
    price = dec(quote["bid"]) * (1 - dec(CONFIG["slippage_per_side"]))
    remaining, kept = qty, []
    for lot in lots:
        used = min(dec(lot["qty"]), remaining)
        remaining -= used
        if used:
            state["closed_lots"].append(
                {
                    "symbol": symbol,
                    "qty": str(used),
                    "seconds": at - lot["opened_at"],
                    "pnl": str(used * (price - dec(lot["unit_cost"]))),
                }
            )
        if dec(lot["qty"]) > used:
            kept.append({**lot, "qty": str(dec(lot["qty"]) - used)})
    if kept:
        state["positions"][symbol] = kept
    else:
        state["positions"].pop(symbol, None)
    state["pending"].append(
        {"amount": str(qty * price), "settles": str(next_session(day)), "source": key}
    )
    mid = (dec(quote["bid"]) + dec(quote["ask"])) / 2
    cost = qty * (mid - price)
    state["costs"] = str(dec(state["costs"]) + cost)
    state["turnover"] = str(dec(state["turnover"]) + qty * price)
    record(
        store,
        key,
        book,
        state,
        symbol=symbol,
        side="sell",
        qty=str(qty),
        price=str(price),
        notional=str(qty * price),
        modeled_cost=str(cost),
        quote=quote,
        date=str(day),
        reason=reason,
    )
    return True


def corporate_action(store, book, action):
    key = f"corporate:{book}:{action['type']}:{action['symbol']}:{action['date']}"
    if store.get(key):
        return
    day = date.fromisoformat(action["date"])
    state = mature(store.state(book), day)
    symbol = action["symbol"]
    if action["type"] == "split":
        for lot in state["positions"].get(symbol, []):
            lot["qty"] = str(dec(lot["qty"]) * dec(action["ratio"]))
            lot["unit_cost"] = str(dec(lot["unit_cost"]) / dec(action["ratio"]))
    elif action["type"] == "dividend":
        previous = store.state(book, before=instant(day, "00:00"))
        entitled = shares(previous, symbol)
        if entitled:
            state["pending"].append(
                {
                    "amount": str(entitled * dec(action["amount"])),
                    "settles": action.get("pay_date"),
                    "source": key,
                    "fundable": False,
                    "status": "PAY_DATE_PENDING" if not action.get("pay_date") else "CONFIRMED",
                }
            )
    else:
        raise ValueError("unknown corporate action")
    record(store, key, book, state, corporate_action=action, date=str(day))
