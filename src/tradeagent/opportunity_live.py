"""Explicit manual LIVE orchestration; production engine owns every broker action."""

import json
import os
import sqlite3
import sys
import time
from dataclasses import asdict
from uuid import uuid4

from .execution_policy import active_settings, check_run_state, load_live_config
from .model import Halt, dec
from .oneshot import atomic_json, new_live_run, run_live
from .research.domain import identity
from .research.opportunity_workflow import selected_execution
from .research.tradeplan import EVIDENCE_GATED, EXPERIMENTAL, plan_dict, validate_execution_plan


def display(kind, payload):
    print(
        json.dumps({"stage": kind, **payload}, indent=2, default=str, allow_nan=False),
        file=sys.stderr,
        flush=True,
    )


def execution_result(report):
    """Describe only broker/engine observations, never infer fills from acceptance."""
    orders = report.get("orders", [])
    status = report.get("status", "HALTED")
    closed = (
        status in {"COMPLETED", "CLOSED_PARTIAL"}
        and report.get("flat_bot_position") is True
        and report.get("cash_reconciled") is True
        and report.get("reconciliation_status") == "RECONCILED"
        and report.get("submission_status") == "BROKER_CONFIRMED"
        and not report.get("outstanding_incident")
        and not report.get("reconciliation_blocker")
    )
    pnl_confirmed = (
        report.get("submission_status") == "BROKER_CONFIRMED"
        and report.get("cash_reconciled") is True
        and report.get("flat_bot_position") is True
        and report.get("reconciliation_status") == "RECONCILED"
        and report.get("realized_pnl") is not None
    )
    uncertain = (
        report.get("submission_status") == "SUBMISSION_UNKNOWN"
        or report.get("reconciliation_blocker")
        or report.get("outstanding_incident") is True
    )
    stage = (
        "RECOVERY_REQUIRED"
        if uncertain
        else "CLOSED"
        if closed
        else "HALTED"
        if status == "HALTED"
        else "NO_TRADE"
        if status == "NO_TRADE"
        else "PARTIALLY_FILLED"
        if any(o.get("state") == "partially_filled" for o in orders)
        else "POSITION_OPEN"
        if dec(report.get("bot_owned_residual", 0)) > 0
        else "ORDER_SUBMITTED"
        if orders
        else "HALTED"
    )
    bought, sold = report.get("bought"), report.get("sold")
    return {
        "status": stage,
        "engine_status": status,
        "submission_status": report.get("submission_status", "SUBMISSION_UNKNOWN"),
        "broker_order_count": report.get("broker_order_count"),
        "orders": [
            {
                "broker_id": o["id"],
                "ref_id": o.get("ref_id"),
                "symbol": o["symbol"],
                "side": o["side"],
                "broker_status": o["state"],
                "filled_quantity": o.get("cumulative_quantity"),
                "fills": o.get("executions", []),
                "fees": o.get("fees"),
            }
            for o in orders
        ],
        "position": {
            "final_positions": report.get("final_positions"),
            "bot_owned_residual": report.get("bot_owned_residual"),
            "flat_bot_position": report.get("flat_bot_position"),
            "bought": bought,
            "sold": sold,
            "exit_due": report.get("exit_due"),
            "exit_reason": report.get("exit_reason"),
        },
        "pnl": {
            "realized_pnl": report.get("realized_pnl") if pnl_confirmed else None,
            "status": "CONFIRMED" if pnl_confirmed else "PENDING",
            "entry_average_price": str(dec(report["entry_executed_notional"]) / dec(bought))
            if bought is not None and dec(bought) > 0
            else None,
            "exit_average_price": str(dec(report["exit_executed_notional"]) / dec(sold))
            if sold is not None and dec(sold) > 0
            else None,
            "known_fees": report.get("known_fees"),
        },
        "reconciliation_status": report.get("reconciliation_status"),
        "reason": report.get("reason") or report.get("reconciliation_blocker"),
    }


def purchase_plan(decision, plan, settings, *, mode="quant_codex", validated=None):
    candidate = next(
        c
        for c in decision.get("quantitative_observations", decision["ranked_candidates"])
        if c["symbol"] == plan.decision.instrument
    )
    economics = plan.economics.plain()
    entry = settings.options.get("entry", {})
    sizing = {k: entry[k] for k in ("dollar_amount", "quantity") if k in entry}
    if not sizing:
        raise Halt("LIVE plan requires existing owner-configured dollar or share sizing")
    value = {
        "decision_id": decision["decision_id"],
        "plan_id": plan.plan_id,
        "research_arm": mode,
        "symbol": plan.decision.instrument,
        "direction": "BUY_LONG",
        "instrument_type": "ETF"
        if plan.decision.instrument
        in {"SPY", "QQQ", "IWM", "DIA", "XLF", "XLK", "XLE", "XLV", "TLT", "GLD"}
        else "US_EQUITY_OR_ETF",
        "selection_reason": (
            decision["experimental"] if mode == "experimental" else decision["comparisons"][mode]
        )["reason"],
        "execution_policy": plan.execution_policy,
        "profitability_established": False if mode == "experimental" else None,
        "quantitative_evidence": {
            k: economics.get(k)
            for k in (
                "grouping_policy",
                "days",
                "target_days",
                "mean_net",
                "lower_net_estimate",
                "standard_error",
                "target_standard_error",
                "between_symbol_discount",
                "critical_value",
                "uncertainty",
                "pool",
                "asof",
            )
        },
        "codex_conclusion": candidate["research"],
        "sizing": sizing,
        "order_type": entry.get("order_type", "limit"),
        "estimated_execution_price": str(plan.decision.entry_limit),
        "maximum_entry_price": str(
            min(
                dec(plan.decision.entry_limit),
                dec(entry.get("limit_price", plan.decision.entry_limit)),
            )
        )
        if entry.get("order_type", "limit") == "limit"
        else str(plan.decision.entry_limit),
        "price_limit_semantics": "entry quote cap; market fill price is not guaranteed"
        if entry.get("order_type") == "market"
        else "broker limit price cap",
        "entry_condition": candidate["entry_condition"],
        "entry_after": plan.entry_after,
        "entry_deadline": plan.entry_deadline,
        "planned_exit_at": plan.exit_at,
        "invalidation": candidate["invalidation"],
        "exit_logic": candidate["exit_logic"],
        "account_selection": settings.config.account_selector
        or settings.account_sha256
        or "existing owner account selector; broker identity verified by engine",
        "risk_limits": asdict(settings.risk),
        "owner_max_notional": settings.options["max_notional"],
        "account_risk_validated": validated is not None,
    }
    if validated is not None:
        value.update(
            estimated_execution_price=validated["ask"],
            validated_order=validated["intent"],
            execution_quote_asof=validated["quote_asof"],
            execution_quote_observed_at=validated["validated_at"],
            account_key=validated["account_key"],
            account_constraints=validated["account_constraints"],
        )
    return value


def reserve_experimental_day(settings, decision, plan, now):
    """One conservative new-entry attempt per owner/session, shared across research dirs.

    Exclusive creation is the cross-process claim. Unknown/failed attempts retain it;
    only the existing active-lifecycle recovery can proceed without a new reservation.
    """
    from datetime import datetime
    from zoneinfo import ZoneInfo

    entry = settings.options["entry"]
    if "quantity" in entry or dec(entry.get("dollar_amount", "0")) != dec("5"):
        raise Halt("Experimental LIVE requires the existing owner $5 dollar sizing")
    day = datetime.fromtimestamp(now, ZoneInfo("America/New_York")).date().isoformat()
    if (
        day
        != datetime.fromtimestamp(plan.entry_after, ZoneInfo("America/New_York")).date().isoformat()
    ):
        raise Halt("experimental entry belongs to a different trading day")
    # new_live_run archives the WHOLE lifecycle directory. Keep the daily claim
    # beside it so a closed lifecycle or a different research dir cannot reset n.
    directory = settings.directory.with_name(settings.directory.name + ".experimental-days")
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = directory / (day + ".json")
    value = {
        "execution_policy": EXPERIMENTAL,
        "session_date": day,
        "decision_id": decision["decision_id"],
        "prediction_id": plan.decision.prediction_id,
        "reserved_at": now,
        "status": "NEW_ENTRY_ATTEMPT_RESERVED",
    }
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as error:
        raise Halt(
            "experimental daily new-entry allowance already reserved; recovery only"
        ) from error
    with os.fdopen(fd, "w") as stream:
        json.dump(value, stream, allow_nan=False)
        stream.flush()
        os.fsync(stream.fileno())
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    return str(path)


def execute_opportunity(
    store, decision_id, config_path, *, live=False, mode="quant_codex", emit=display
):
    """One invocation, no selector fallback, no broker implementation or timers."""
    if live is not True:
        raise Halt("explicit owner LIVE invocation required")
    if mode not in {"quant_codex", "quant_only", "experimental"}:
        raise Halt("Codex Only cannot authorize LIVE execution")
    decision = store.decision(decision_id)
    decision_id = decision["decision_id"]
    policy = decision.get("execution_policy", EVIDENCE_GATED)
    if (mode == "experimental") != (policy == EXPERIMENTAL):
        raise Halt("explicit execution mode must match frozen decision policy")
    root = store.experience.directory / "live" / decision_id / str(uuid4())
    root.mkdir(parents=True, mode=0o700)
    result = {
        "decision_id": decision_id,
        "mode": "LIVE",
        "research_arm": mode,
        "execution_policy": policy,
        "status": "NO_TRADE",
        "submission_status": "NOT_SUBMITTED",
        "broker_order_count": 0,
        "position": None,
        "pnl": {"realized_pnl": None, "status": "PENDING"},
        "result_path": str(root / "result.json"),
    }
    settings, report, called, previous = None, None, False, None
    warnings = []
    linkage_failed = False

    def event(stage, payload, *, critical=False):
        try:
            with (root / "events.jsonl").open("a") as stream:
                stream.write(
                    json.dumps(
                        {"stage": stage, "observed_at": time.time(), **payload},
                        default=str,
                        allow_nan=False,
                    )
                    + "\n"
                )
                stream.flush()
                os.fsync(stream.fileno())
            emit(stage, payload)
        except Exception as error:
            if critical:
                raise
            # A display/log failure after submission must not stop the production exit.
            warnings.append(str(error))

    def observer(kind, payload):
        nonlocal report
        if kind == "one_shot_final":
            report = dict(payload)
            return
        if kind == "validated_plan_entry":
            if payload["provenance"]["prediction_id"] != plan.decision.prediction_id:
                raise Halt("engine purchase plan differs from frozen decision")
            value = purchase_plan(decision, plan, settings, mode=mode, validated=payload)
            path = root / "purchase-plan-validated.json"
            atomic_json(path, value)
            result["purchase_plan_path"] = str(path)
            event("PLAN_CREATED", {"purchase_plan": value, "path": str(path)}, critical=True)
        elif kind == "placement_send_started":
            event("SUBMISSION_UNKNOWN", {"ref_id": payload["ref_id"]})
        elif kind == "broker_acknowledgment":
            event(
                "ORDER_SUBMITTED",
                {
                    "broker_id": payload["id"],
                    "side": payload["side"],
                    "broker_status": payload["state"],
                    "fill_confirmed": False,
                },
            )
        elif kind == "fill_reconciliation" and dec(payload["filled"]) > 0:
            stage = (
                "PARTIALLY_FILLED"
                if payload["state"] in {"partially_filled", "partially_filled_rest_cancelled"}
                else "POSITION_OPEN"
                if payload["order"]["side"] == "buy"
                else "EXIT_FILL_CONFIRMED"
            )
            event(
                stage,
                {
                    "side": payload["order"]["side"],
                    "filled_quantity": payload["filled"],
                    "broker_status": payload["state"],
                    "positions": payload["positions"],
                },
            )
        elif kind == "exit_due":
            event("POSITION_OPEN", {"planned_exit_at": payload["due"]})

    try:
        arm = decision["experimental"] if mode == "experimental" else decision["comparisons"][mode]
        if not arm["plan"]:
            result["reason"] = f"{mode} selected NO_TRADE; no selector fallback"
            return result
        plan, prediction = selected_execution(decision, mode)
        if plan.research_only or plan.decision.kind != "UNDERLYING":
            result["reason"] = "only eligible prospective long equity/ETF plans can execute"
            return result
        # Static eligibility at the original opener is not acceptance of future quotes.
        validate_execution_plan(plan, plan.entry_after)
        settings = load_live_config(config_path)
        settings.validate()
        previous = check_run_state(settings)
        resume = False
        if previous:
            with sqlite3.connect(
                f"file:{settings.directory / 'agent/state.sqlite3'}?mode=ro", uri=True
            ) as db:
                row = db.execute(
                    "SELECT payload FROM one_shot_meta WHERE key='decision'"
                ).fetchone()
                saved = json.loads(row[0]) if row else {}
            provenance = saved.get("provenance") or {}
            resume = provenance.get("prediction_id") == prediction.prediction_id
            if resume and provenance.get("economic_plan") != plan_dict(plan):
                raise Halt("active execution plan differs from immutable research decision")
        if not resume:
            # Never replay an old decision after its lifecycle has been archived.
            if any(r["decision_id"] == decision_id for r in store.records("executions")):
                raise Halt("decision already attempted; inspect its saved execution feedback")
            if time.time() < prediction.decision_time:
                raise Halt("future-dated decision cannot execute")
            if time.time() >= plan.entry_deadline:
                result.update(status="EXPIRED", reason="original entry window expired")
                return result
        effective = active_settings(settings) if resume else settings
        value = purchase_plan(decision, plan, effective, mode=mode)
        path = root / "purchase-plan-created.json"
        atomic_json(path, value)
        result["purchase_plan_path"] = str(path)
        event("PLAN_CREATED", {"purchase_plan": value, "path": str(path)}, critical=True)
        if mode == "experimental" and not resume:
            result["experimental_day_receipt"] = reserve_experimental_day(
                settings, decision, plan, time.time()
            )
        if previous and not resume:
            # The established routine proves closure using fresh broker evidence and
            # archives the complete old journal/receipt. It cannot replace open exposure.
            archive = new_live_run(settings)
            result["previous_lifecycle"] = archive
        if not resume:
            last = None
            while time.time() < plan.entry_after:
                now = time.time()
                settings.validate()
                if last is None or now - last >= 30:
                    event(
                        "AWAITING_ENTRY",
                        {"entry_after": plan.entry_after, "entry_deadline": plan.entry_deadline},
                        critical=True,
                    )
                    last = now
                time.sleep(min(5, max(0, plan.entry_after - now)))
            if time.time() >= plan.entry_deadline:
                result.update(
                    status="EXPIRED", reason="original entry window expired while waiting"
                )
                return result
            validate_execution_plan(plan, time.time())
        called = True
        # Existing engine refreshes and validates quotes/account/risk, then invokes
        # the purchase-plan observer before review/place. It owns all entry/exit work.
        report = run_live(
            settings, recover=resume, plan=plan, prediction=prediction, observer=observer
        )
        linked = (report.get("decision") or {}).get("provenance") or {}
        if (
            report.get("submission_status") == "BROKER_CONFIRMED"
            and linked.get("prediction_id") != prediction.prediction_id
        ):
            linkage_failed = True
            raise Halt("broker report is not linked to this immutable prediction")
        result.update(execution_result(report))
        return result
    except (
        Halt,
        OSError,
        ValueError,
        KeyError,
        TypeError,
        sqlite3.Error,
        KeyboardInterrupt,
        SystemExit,
    ) as error:
        result.update(
            status="RECOVERY_REQUIRED" if called or (settings and previous) else "HALTED",
            reason=str(error),
        )
        if called:
            if report is not None and not linkage_failed:
                result.update(execution_result(report))
                result["reason"] = str(error)
            else:
                result.update(submission_status="SUBMISSION_UNKNOWN", broker_order_count=None)
            # The engine journal/report, rather than a local exception, decides whether
            # placement happened. Preserve a diagnostic link, never report zero orders.
            result["engine_report_path"] = str(settings.directory / "report.json")
        return result
    finally:
        if report is not None:
            result["execution_report_path"] = str(root / "execution-report.json")
            result["engine_report_path"] = str(settings.directory / "report.json")
            try:
                atomic_json(root / "execution-report.json", report)
                record = {
                    "decision_id": decision_id,
                    "research_arm": mode,
                    "execution_policy": policy,
                    "recorded_at": time.time(),
                    "report": report,
                    "verification": "existing production engine observations; reconciliation status retained",
                    "purchase_plan_path": result.get("purchase_plan_path"),
                    "execution_report_path": result["execution_report_path"],
                }
                if linkage_failed:
                    raise Halt(
                        "unlinked report retained for diagnosis, excluded from strategy feedback"
                    )
                result["execution_record"] = store.save(
                    "executions", record, identity([decision_id, mode, report])
                )
            except Exception as error:
                warnings.append("feedback persistence failed: " + str(error))
        if result["status"] in {"RECOVERY_REQUIRED", "HALTED"}:
            result["recovery"] = (
                "Inspect tradeagent status --config CONFIG; if exposure is unresolved use tradeagent recover --config CONFIG (exit-only). Never retry a new entry."
            )
        if warnings:
            result["feedback_warnings"] = warnings
        try:
            atomic_json(root / "result.json", result)
        except OSError as error:
            warnings.append("result persistence failed: " + str(error))
            result["feedback_warnings"] = warnings
        event(result["status"], {"decision_id": decision_id, "result_path": result["result_path"]})
