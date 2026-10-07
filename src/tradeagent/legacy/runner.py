"""One-shot, journaled cycle; no timers, scheduling or LLM sizing."""

import copy
import time
from dataclasses import asdict
from datetime import datetime
from zoneinfo import ZoneInfo

from ..account_policy import POLICY
from ..accounting import Accounting
from ..execution import Execution, apply_shadow
from ..model import Halt, dec
from ..risk import check_state
from .strategy import TestTrendStrategy


def cycle(broker, state, config, risk, strategy=None):
    config.validate()
    risk.validate()
    # CLI is deliberately shadow-only even if a config is mistakenly changed.
    if config.mode != "SHADOW":
        raise Halt(
            "one-shot runner supports SHADOW only; supervised capability requires a separate release"
        )
    strategy = strategy or TestTrendStrategy()
    if set(strategy.universe(config)) != set(config.allowed_symbols):
        raise Halt("strategy universe must exactly match the deterministic allowed universe")
    with state.lock(config.lease_seconds) as fence:
        run_id = state.start(config, risk)
        result = {
            "run_id": run_id,
            "mode": config.mode,
            "proposals": [],
            "risk": [],
            "reconciliation": "not_completed",
        }
        status, initial = "failed", None
        try:
            initial = broker.snapshot()
            state.event(run_id, "account_scope", broker.account_scope)
            state.event(run_id, "portfolio_snapshot", asdict(initial))
            state.recover(run_id, initial)
            state.export_log()
            histories = broker.histories()
            state.event(run_id, "historical_snapshot", histories)
            normalized = strategy.signals(histories, config)
            signals = {symbol: asdict(sig.validate()) for symbol, sig in normalized.items()}
            state.event(run_id, "signal_snapshot", signals)
            result["signals"] = signals
            day = datetime.now(ZoneInfo("America/New_York")).date().isoformat()
            baseline = Accounting(state).observe(run_id, initial, day)
            state.event(
                run_id,
                "daily_baseline",
                {
                    "day": day,
                    "nav": baseline,
                    "meaning": "first observed positive NAV, flow adjusted; unknown movements latch halt",
                },
            )
            result["portfolio_gate_checks"] = {
                "state_valid": initial.valid,
                "startup_reconciled": initial.reconciled,
                "eligible_unleveraged_account": initial.account_type == "cash"
                or (
                    initial.account_type == "limited_margin"
                    and initial.agentic_eligible
                    and initial.account_policy == POLICY
                ),
                "positive_nav": initial.nav > 0,
                "nonnegative_cash": initial.cash >= 0,
                "nonnegative_unleveraged_buying_power": initial.buying_power >= 0,
                "regular_session": initial.regular_session,
            }
            state.event(run_id, "portfolio_gate_checks", result["portfolio_gate_checks"])
            try:
                check_state(initial, risk, time.time())
            except Halt as exc:
                result["risk"].append(
                    {"scope": "portfolio", "decision": "halt", "reason": str(exc)}
                )
            if not result["risk"]:
                working = copy.deepcopy(initial)
                exposure, turnover = dec(0), dec(0)
                executor = Execution(state, broker, config, risk, run_id, fence)
                for symbol in config.allowed_symbols:
                    sig = signals[symbol]
                    intent = strategy.proposal(normalized[symbol], working, config)
                    if intent is None:
                        result["risk"].append(
                            {
                                "symbol": symbol,
                                "decision": "no_intent",
                                "reason": "signal/position/whole-share sizing",
                            }
                        )
                        continue
                    state.event(
                        run_id,
                        "strategy_candidate",
                        {"symbol": symbol, "payload": intent.payload()},
                    )
                    try:
                        decision = executor.process(
                            intent,
                            working,
                            baseline,
                            sig["bar_time"],
                            cycle_exposure=exposure,
                            cycle_turnover=turnover,
                        )
                        result["proposals"].append(decision)
                        result["risk"].append({"symbol": symbol, "decision": decision["status"]})
                        if decision["status"] == "shadow_recorded":
                            notional = intent.quantity * intent.limit_price
                            turnover += notional
                            if intent.side == "buy":
                                exposure += notional
                            apply_shadow(working, intent)
                    except Halt as exc:
                        result["risk"].append(
                            {"symbol": symbol, "decision": "rejected", "reason": str(exc)}
                        )
            state.event(run_id, "risk_decisions", result["risk"])
            fence()
            final = broker.reconcile(initial)
            state.event(
                run_id, "final_reconciliation", {"decision": "matched", "snapshot": asdict(final)}
            )
            result["reconciliation"] = "matched"
            result["broker_calls"] = broker.bridge.calls if hasattr(broker, "bridge") else []
            status = (
                "halted"
                if any(x["decision"] in {"halt", "rejected"} for x in result["risk"])
                else "completed"
            )
        except Halt as exc:
            status = "halted"
            result["error"] = str(exc)
            state.event(run_id, "halt", {"reason": str(exc)})
        except Exception as exc:
            # Store class only; upstream exception text may contain private identifiers.
            result["error"] = (
                f"unexpected {type(exc).__name__}; inspect implementation before retry"
            )
            state.event(run_id, "unexpected_error", {"class": type(exc).__name__})
        finally:
            result["status"] = status
            state.event(run_id, "cycle_result", result)
            state.finish(run_id, status)
            state.export_log()
        return result
