import time
from dataclasses import asdict
from decimal import Decimal as D

import pytest

from tradeagent import state as state_module
from tradeagent.execution import Execution
from tradeagent.model import Config, Halt, Intent, Risk, Snapshot, digest
from tradeagent.state import State


@pytest.mark.parametrize("receipt", ["timeout", "ack_only", "filled"])
def test_approved_fake_submission_requires_reconciliation(tmp_path, monkeypatch, receipt):
    # Pure local contract test. The real broker adapter rejects these operations.
    now = time.time()
    monkeypatch.setattr(state_module.uuid, "uuid4", lambda: "fake-ref-only")
    config = Config(mode="SUPERVISED", supervised_enabled=True, allowed_symbols=["SPY"])
    risk = Risk()
    snapshot = Snapshot(
        "fake-account-key",
        now,
        D(10000),
        D(10000),
        D(10000),
        {},
        {},
        {"SPY": D(100)},
        {"SPY": now},
        {"SPY": D("100.01")},
        {"SPY": D("99.99")},
        [],
        D(0),
        tradable={"SPY": True},
        bid_times={"SPY": now},
        ask_times={"SPY": now},
        regular_session=True,
        liquidity={"SPY": {"asof": now, "bid_size": 10000, "ask_size": 10000}},
        agentic_eligible=True,
        account_policy="agentic-unleveraged-2026-10-05-v1",
    )
    intent = Intent("SPY", "buy", D(5), D("100.01"))
    review = {"payload": intent.payload(), "checks_passed": True, "asof": now}
    key = digest([snapshot.account_key, config.strategy_version, intent.symbol, intent.side, "bar"])
    proposal = {
        "key": key,
        "ref_id": "fake-ref-only",
        "payload": intent.payload(),
        "mode": "SUPERVISED",
    }
    approval = digest([proposal, review])
    state = State(tmp_path)
    run_id = state.start(config, risk)

    class Fake:
        def __init__(self):
            self.submitted = False

        def review(self, _):
            return review

        def snapshot(self):
            if self.submitted and receipt == "filled":
                return Snapshot(
                    **{
                        **asdict(snapshot),
                        "orders": [
                            {
                                "id": "fake-order",
                                "ref_id": "fake-ref-only",
                                "symbol": "SPY",
                                "side": "buy",
                                "quantity": "5.00000000",
                                "price": "100.0100",
                                "state": "filled",
                            }
                        ],
                    }
                )
            return snapshot

        def reconcile(self, _):
            return snapshot

        def submit(self, _, ref_id):
            assert ref_id == "fake-ref-only"
            # Persistence precedes the call even if the transport loses its response.
            assert state.db.execute("SELECT status FROM intents").fetchone()[0] == "submitting"
            self.submitted = True
            if receipt == "timeout":
                raise TimeoutError("fake acknowledgement loss")
            return {"id": "fake-order"}

    executor = Execution(state, Fake(), config, risk, run_id, lambda: None)
    if receipt == "filled":
        assert executor.process(intent, snapshot, 10000, "bar", approval)["status"] == "filled"
    else:
        with pytest.raises(Halt, match="uncertain|not terminal"):
            executor.process(intent, snapshot, 10000, "bar", approval)
        assert state.db.execute("SELECT status FROM intents").fetchone()[0] == (
            "unknown" if receipt == "timeout" else "pending"
        )
    state.close()
