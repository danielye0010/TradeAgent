"""Durable NAV/cash-flow attribution. Unknown cash or asset movements latch a halt."""

import json

from .model import Halt, dec
from .state import dumps


class Accounting:
    def __init__(self, state):
        self.state = state
        state.db.execute("""CREATE TABLE IF NOT EXISTS accounting(
            account_key TEXT PRIMARY KEY, checkpoint TEXT NOT NULL, blocked INTEGER NOT NULL)""")
        state.db.commit()

    def observe(self, run, snapshot, day):
        s, db = snapshot, self.state.db
        row = db.execute(
            "SELECT * FROM accounting WHERE account_key=?", (s.account_key,)
        ).fetchone()
        prior = json.loads(row["checkpoint"]) if row else None
        if row and row["blocked"]:
            raise Halt("cash-flow uncertainty is latched; human reconciliation/rebaseline required")
        if s.nav <= 0:
            return str(s.nav)
        fills = {f["id"]: f for f in s.fills}
        if len(fills) != len(s.fills):
            raise Halt("duplicate accounting execution")
        flows = {f["id"]: f for f in s.cashflows} if s.cashflows is not None else None
        if flows is not None and len(flows) != len(s.cashflows):
            raise Halt("duplicate cash-flow record")
        external, traded, movements = dec(0), dec(0), {}
        classifications = []
        if prior:
            old_fills = prior["fills"]
            if any(k not in fills or fills[k] != v for k, v in old_fills.items()):
                return self._block(run, s, prior, "fill history changed/incomplete")
            for fid, fill in fills.items():
                if fid in old_fills:
                    continue
                if fill.get("fees_known") is False:
                    return self._block(
                        run,
                        s,
                        prior,
                        "option execution fees unavailable; cash attribution incomplete",
                    )
                traded += dec(fill["cash_delta"])
                sign = 1 if fill["side"] == "buy" else -1
                symbol = fill["symbol"]
                movements[symbol] = movements.get(symbol, dec(0)) + sign * dec(fill["quantity"])
                classifications.append(
                    {
                        "kind": "trading_cash_change",
                        "execution_id": fid,
                        "cash_delta": fill["cash_delta"],
                    }
                )
            if flows is not None:
                old_flows = prior.get("flows") or {}
                if any(k not in flows or flows[k] != v for k, v in old_flows.items()):
                    return self._block(run, s, prior, "cash-flow history changed/incomplete")
                for fid, flow in flows.items():
                    if fid not in old_flows:
                        if flow.get("kind") not in {"deposit", "withdrawal", "transfer"}:
                            return self._block(run, s, prior, "unknown cash-flow classification")
                        external += dec(flow["amount"])
                        classifications.append(flow)
            if abs(s.cash - dec(prior["cash"]) - traded - external) > dec(".01"):
                return self._block(
                    run, s, prior, "unexplained cash movement; transfer data unavailable"
                )
            holdings = {**s.positions, **s.options}
            previous = prior["holdings"]
            for symbol in set(holdings) | set(previous) | set(movements):
                if dec(holdings.get(symbol, 0)) - dec(previous.get(symbol, 0)) != movements.get(
                    symbol, dec(0)
                ):
                    return self._block(run, s, prior, "unexplained asset transfer/corporate action")
        baseline = dec(prior["baseline"]) + external if prior and prior["day"] == day else s.nav
        high = max(s.nav, dec(prior["high"]) + external) if prior else s.nav
        if baseline <= 0 or high <= 0:
            return self._block(run, s, prior or {}, "invalid flow-adjusted baseline")
        s.high_water_nav = high
        record = {
            "day": day,
            "baseline": str(baseline),
            "high": str(high),
            "nav": str(s.nav),
            "cash": str(s.cash),
            "holdings": {**s.positions, **s.options},
            "fills": fills,
            "flows": flows,
        }
        # Convert Decimals once so history comparisons on restart use identical shapes.
        record = json.loads(dumps(record))
        with db:
            db.execute(
                "INSERT OR REPLACE INTO accounting VALUES(?,?,0)", (s.account_key, dumps(record))
            )
        self.state.event(
            run,
            "accounting",
            {
                "baseline": str(baseline),
                "high_water": str(high),
                "external_flows": str(external),
                "trading_cash_change": str(traded),
                "market_pnl_change": str(s.nav - dec(prior["nav"]) - external) if prior else None,
                "attribution": classifications,
                "cashflows_available": flows is not None,
                "meaning": "first observed positive NAV, flow-adjusted; realized P&L needs cost basis",
            },
        )
        return str(baseline)

    def _block(self, run, snapshot, prior, reason):
        with self.state.db:
            self.state.db.execute(
                "INSERT OR REPLACE INTO accounting VALUES(?,?,1)",
                (snapshot.account_key, dumps(prior)),
            )
        snapshot.accounting_verified = False
        self.state.event(
            run, "accounting_halt", {"reason": reason, "rebaseline": "requires human investigation"}
        )
        raise Halt(reason)
