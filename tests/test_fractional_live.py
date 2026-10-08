"""Exact installed LIVE path, controlled HTTP replies; never real broker writes."""

import json
from copy import deepcopy
from uuid import uuid4

import pytest
from test_production_oneshot import owner_mock_runtime

from tradeagent.execution_policy import load_live_config
from tradeagent.model import Halt, Intent, dec
from tradeagent.oneshot import new_live_run
from tradeagent.simulator import schema_blank


def fractional_runtime(
    tmp_path, monkeypatch, amount="5", mode="filled", exit_mode="filled", fees="0"
):
    invoke, sim, calls, target, path = owner_mock_runtime(tmp_path, monkeypatch)
    text = path.read_text().replace('["IWM", "QQQ"]', '["AAPL"]')
    text = text.replace(
        "[exit]",
        f'[entry]\norder_type = "market"\ndollar_amount = "{amount}"\n[exit]\norder_type = "market"',
    )
    path.write_text(text)
    s = sim.initial
    s.prices, s.asks, s.bids = {"AAPL": dec(500)}, {"AAPL": dec("500.01")}, {"AAPL": dec("499.99")}
    for field in ("quote_times", "bid_times", "ask_times"):
        setattr(s, field, {"AAPL": sim.clock()})
    s.tradable, s.fractional_tradable, s.countries = {"AAPL": True}, {"AAPL": True}, {"AAPL": "US"}
    s.liquidity = {"AAPL": {"asof": sim.clock(), "bid_size": 10000, "ask_size": 10000}}
    original_invoke, original_snapshot = sim.invoke, sim.snapshot
    requested = []

    def controlled(name, args):
        if name == "review_equity_order":
            reply = {
                "data": {
                    k: v
                    for k, v in args.items()
                    if k not in {"account_number", "time_in_force", "market_hours"}
                },
                "guide": "CONTROLLED TEST",
            }
            reply["data"].update(
                order_checks={}, customer_approval_required=False, quote_data=sim._quote("AAPL")
            )
            sim.contracts.validate(name, reply, "outputSchema")
            return reply
        if name == "place_equity_order":
            requested.append(deepcopy(args))
            side = args["side"]
            scenario = mode if side == "buy" else exit_mode
            quantity = dec(amount) / 500 if side == "buy" else dec(args["quantity"])
            if scenario in {"partial", "partial_pending"}:
                quantity /= 2
            if scenario in {"rejected", "unknown"}:
                quantity = dec(0)
            order = schema_blank(
                sim.contracts.tools[name]["outputSchema"]["properties"]["data"]["properties"][
                    "order"
                ]
            )
            order.update(
                id=str(uuid4()),
                ref_id=args["ref_id"],
                symbol="AAPL",
                side=side,
                type="market",
                state="partially_filled"
                if scenario == "partial_pending"
                else "partially_filled_rest_cancelled"
                if scenario == "partial"
                else "rejected"
                if scenario == "rejected"
                else "filled",
                quantity=None if side == "buy" else args["quantity"],
                cumulative_quantity=str(quantity),
                price=None,
                dollar_based_amount={"amount": amount, "currency_code": "USD"}
                if side == "buy"
                else None,
                average_price="500" if quantity else None,
                fees=fees if quantity else "0",
                time_in_force="gfd",
                market_hours="regular_hours",
                created_at=sim.clock.iso(),
                executions=[
                    {
                        "id": str(uuid4()),
                        "quantity": str(quantity / 2),
                        "price": "499",
                        "timestamp": sim.clock.iso(),
                        "fees": "0",
                    },
                    {
                        "id": str(uuid4()),
                        "quantity": str(quantity / 2),
                        "price": "501",
                        "timestamp": sim.clock.iso(),
                        "fees": fees,
                    },
                ]
                if quantity
                else [],
            )
            reply = {"data": {"order": order}, "guide": "CONTROLLED TEST"}
            sim.contracts.validate(name, reply, "outputSchema")
            if scenario != "unknown":
                with sim.db:
                    sim.db.execute(
                        "INSERT INTO simulated_orders VALUES(?,?,?,?,?)",
                        (
                            args["ref_id"],
                            order["id"],
                            "equity",
                            json.dumps(args),
                            json.dumps(reply),
                        ),
                    )
            if scenario in {"lost_ack", "unknown"}:
                raise TimeoutError("CONTROLLED lost acknowledgment")
            if scenario == "crash":
                raise SystemExit("CONTROLLED interruption after broker persistence")
            return reply
        return original_invoke(name, args)

    def snapshot():
        result = original_snapshot()
        execution_fees = {f["id"]: dec(f["fees"]) for o in result.orders for f in o["executions"]}
        total = sum(execution_fees.values(), dec(0))
        result.cash -= total
        result.buying_power = min(result.buying_power, result.cash)
        result.nav -= total
        for fill in result.fills:
            fill["cash_delta"] = str(dec(fill["cash_delta"]) - execution_fees[fill["id"]])
        return result

    monkeypatch.setattr(sim, "invoke", controlled)
    monkeypatch.setattr(sim, "snapshot", snapshot)
    return invoke, sim, requested, target, path


@pytest.mark.parametrize("amount", ["5", "10"])
def test_dollar_entry_exact_fractional_exit_and_fees(tmp_path, monkeypatch, amount):
    invoke, sim, requested, target, path = fractional_runtime(
        tmp_path, monkeypatch, amount, fees="0.01"
    )
    try:
        result = invoke()
        assert result["status"] == "COMPLETED", result
        assert requested[0]["dollar_amount"] == amount and "quantity" not in requested[0]
        assert requested[0]["type"] == "market" and "limit_price" not in requested[0]
        assert dec(requested[1]["quantity"]) == dec(amount) / 500
        assert "dollar_amount" not in requested[1] and "limit_price" not in requested[1]
        assert result["orders"][0]["quantity"] is None
        assert len(result["orders"][0]["executions"]) == 2
        assert dec(result["orders"][0]["execution_average_price"]) == 500
        assert result["final_positions"] == {} and dec(result["bot_owned_residual"]) == 0
        assert dec(result["known_fees"]) == dec(".02")
        assert dec(result["realized_pnl"]) == dec("-.02")
        assert dec(result["final_cash"]) == dec("999.98")
        assert invoke()["duplicate_suppressed"] and len(requested) == 2
        assert (target / "report.json").exists()
    finally:
        sim.close()


@pytest.mark.parametrize("mode", ["partial", "partial_pending", "lost_ack", "crash"])
def test_fractional_entry_recovery_uses_actual_fills_once(tmp_path, monkeypatch, mode):
    invoke, sim, requested, target, path = fractional_runtime(tmp_path, monkeypatch, mode=mode)
    try:
        if mode == "crash":
            with pytest.raises(SystemExit):
                invoke()
        result = invoke()
        assert result["status"] == (
            "CLOSED_PARTIAL" if mode in {"partial", "partial_pending"} else "COMPLETED"
        ), result
        assert dec(requested[1]["quantity"]) == (
            dec(".005") if mode in {"partial", "partial_pending"} else dec(".01")
        )
        assert len(requested) == 2 and result["flat_bot_position"]
    finally:
        sim.close()


@pytest.mark.parametrize("mode", ["unknown", "rejected"])
def test_fractional_no_blind_retry_and_no_exit_without_fills(tmp_path, monkeypatch, mode):
    invoke, sim, requested, target, path = fractional_runtime(tmp_path, monkeypatch, mode=mode)
    try:
        result = invoke()
        assert result["status"] == ("HALTED" if mode == "unknown" else "NO_TRADE")
        assert invoke()["status"] == result["status"] and len(requested) == 1
    finally:
        sim.close()


@pytest.mark.parametrize("exit_mode", ["partial", "lost_ack", "crash", "unknown"])
def test_fractional_exit_recovery_and_exact_residual(tmp_path, monkeypatch, exit_mode):
    invoke, sim, requested, target, path = fractional_runtime(
        tmp_path, monkeypatch, exit_mode=exit_mode
    )
    try:
        if exit_mode == "crash":
            with pytest.raises(SystemExit):
                invoke()
        result = invoke()
        assert result["status"] == (
            "HALTED" if exit_mode in {"partial", "unknown"} else "COMPLETED"
        ), result
        if exit_mode == "partial":
            assert dec(result["bot_owned_residual"]) == dec(".005")
            assert result["realized_pnl"] is None
        assert invoke()["status"] == result["status"] and len(requested) == 2
    finally:
        sim.close()


def test_fractional_ineligible_blocks_entry(tmp_path, monkeypatch):
    invoke, sim, requested, target, path = fractional_runtime(tmp_path, monkeypatch)
    sim.initial.fractional_tradable["AAPL"] = False
    try:
        result = invoke()
        assert result["status"] == "NO_TRADE" and not requested
    finally:
        sim.close()


@pytest.mark.parametrize(
    "intent",
    [
        Intent("AAPL", "buy", dec(".01"), dec(500)),
        Intent("AAPL", "buy", dec(".0000001"), None, order_type="market"),
        Intent("AAPL", "buy", None, None, order_type="market", dollar_amount=dec(".99")),
        Intent("AAPL", "buy", dec(1), None, order_type="market", dollar_amount=dec(5)),
    ],
)
def test_broker_constraints_remain_enforced(intent):
    with pytest.raises(Halt):
        intent.payload()


def test_owner_new_run_refuses_unresolved_exposure(tmp_path, monkeypatch):
    invoke, sim, requested, target, path = fractional_runtime(
        tmp_path, monkeypatch, exit_mode="partial"
    )
    try:
        assert invoke()["status"] == "HALTED"
        with pytest.raises(Halt, match="unfinished"):
            new_live_run(load_live_config(path))
        assert len(requested) == 2 and target.exists()
    finally:
        sim.close()


def test_owner_new_run_archives_and_preserves_previous_evidence(tmp_path, monkeypatch):
    invoke, sim, requested, target, path = fractional_runtime(tmp_path, monkeypatch)
    try:
        assert invoke()["status"] == "COMPLETED"
        before = (target / "report.json").read_bytes()
        archived = new_live_run(load_live_config(path))
        from pathlib import Path

        archive = Path(archived["archived_run"])
        assert (archive / "report.json").read_bytes() == before
        assert (archive / "agent/state.sqlite3").is_file()
        assert not target.exists() and not load_live_config(path).receipt.exists()
        assert len(requested) == 2  # archive itself is strictly read-only at the broker
        assert invoke()["status"] == "COMPLETED"
        assert len(requested) == 4
        assert len({r["ref_id"] for r in requested}) == 4
    finally:
        sim.close()


@pytest.mark.parametrize(
    "quantity,kind,price",
    [("0.01", "market", None), ("1", "market", None), ("1", "limit", "500.01")],
)
def test_quantity_entries_remain_available(tmp_path, monkeypatch, quantity, kind, price):
    invoke, sim, requested, target, path = fractional_runtime(tmp_path, monkeypatch, amount="10")
    text = path.read_text().replace('max_notional = "25"', 'max_notional = "1000"')
    text = text.replace('dollar_amount = "10"', f'quantity = "{quantity}"')
    if kind == "limit":
        text = text.replace(
            '[entry]\norder_type = "market"',
            f'[entry]\norder_type = "limit"\nlimit_price = "{price}"',
        )
    path.write_text(text)
    # Exercise selector/risk/wire payload for quantity modes against fresh controlled state.
    from tradeagent.oneshot import choose_entry
    from tradeagent.risk import check_order

    sim.initial.nav = sim.initial.cash = sim.initial.buying_power = dec(10000)
    try:
        settings = load_live_config(path)
        s = sim.snapshot()
        intent, reasons = choose_entry(
            s,
            settings.config,
            settings.risk,
            sim.clock(),
            settings.options["max_notional"],
            settings.options["entry"],
        )
        assert intent is not None, reasons
        check_order(intent, s, settings.config, settings.risk, sim.clock(), s.nav)
        assert intent.payload()["quantity"] == quantity and intent.order_type == kind
    finally:
        sim.close()


def test_actual_broker_read_normalizes_dollar_fills_and_metadata():
    from test_broker_contract import NOW, RawReadFake

    from tradeagent.broker import Broker
    from tradeagent.model import Config, Risk

    raw = RawReadFake()
    raw.payloads["get_equity_tradability"]["results"][0].update(
        country="US", fractional_tradability="tradable"
    )
    raw.payloads["get_equity_orders"]["orders"] = [
        {
            "id": "CONTROLLED_READ_ORDER",
            "ref_id": "CONTROLLED_REF",
            "symbol": "SPY",
            "side": "buy",
            "type": "market",
            "state": "filled",
            "quantity": None,
            "price": None,
            "dollar_based_amount": {"amount": "5", "currency_code": "USD"},
            "cumulative_quantity": "0.01",
            "average_price": "500",
            "fees": "0.01",
            "executions": [
                {
                    "id": "READ_FILL_1",
                    "quantity": "0.004",
                    "price": "499",
                    "timestamp": NOW.isoformat(),
                    "fees": "0",
                },
                {
                    "id": "READ_FILL_2",
                    "quantity": "0.006",
                    "price": "500.666666666666666667",
                    "timestamp": NOW.isoformat(),
                    "fees": "0.01",
                },
            ],
        }
    ]
    snapshot = Broker(raw, Config(allowed_symbols=["SPY"]), Risk()).snapshot(NOW.timestamp())
    order = snapshot.orders[0]
    assert order["quantity"] is None and order["price"] is None
    assert abs(dec(order["executed_notional"]) - 5) < dec("1e-12")
    assert sum(dec(f["cash_delta"]) for f in snapshot.fills) == -dec(
        order["executed_notional"]
    ) - dec(".01")
    assert snapshot.countries == {"SPY": "US"} and snapshot.fractional_tradable == {"SPY": True}


def test_fractional_reserved_shares_prevent_exit(tmp_path, monkeypatch):
    invoke, sim, requested, target, path = fractional_runtime(tmp_path, monkeypatch)
    original = sim.snapshot

    def reserved():
        s = original()
        if s.positions:
            s.available["AAPL"] = dec(0)
        return s

    monkeypatch.setattr(sim, "snapshot", reserved)
    try:
        result = invoke()
        assert result["status"] == "HALTED" and len(requested) == 1
        assert "reserved" in result["reason"]
        assert dec(result["bot_owned_residual"]) == dec(".01")
    finally:
        sim.close()
