"""Cost-aware plans wrapping the existing TradePlan; no persistence or broker ownership."""

import math
import statistics
from collections import defaultdict
from dataclasses import dataclass, replace
from decimal import ROUND_DOWN

from ..model import Halt, Intent, dec
from .domain import Payload, finite, identity
from .expressions import TradePlan
from .market import snapshot_identity


@dataclass(frozen=True)
class Costs:
    half_spread_bps: float = 1.0
    slippage_bps: float = 1.0
    fee_bps: float = 0.1

    def __post_init__(self):
        if any(
            not 0 <= finite(x) < 100
            for x in (self.half_spread_bps, self.slippage_bps, self.fee_bps)
        ):
            raise ValueError("invalid per-side cost")

    @property
    def side(self):
        return (self.half_spread_bps + self.slippage_bps + self.fee_bps) / 10000

    def net(self, gross):
        if finite(gross) <= -1:
            raise ValueError("invalid gross return")
        return (1 + gross) * (1 - self.side) / (1 + self.side) - 1


EVIDENCE_GATED = "evidence-gated-v1"
EXPERIMENTAL = "experimental-live-v1"

COSTS = {"low": Costs(0.5, 0.5, 0), "base": Costs(), "stress": Costs(2, 3, 0.2)}


def economic_evidence(prediction, rows, *, regime=None):
    """Estimate actual long-return economics, not the learner's clipped residual score.

    One mean per UTC daily cluster; exact strategy/version/symbol/horizon and pool.
    All observations must be resolved strictly before the forecast decision.
    """
    pool = prediction.context.value["evidence_kind"]
    groups, ids = defaultdict(list), []
    from .domain import iso

    for r in rows:
        if (
            r["resolved_at"] >= prediction.decision_time
            or r["decision_time"] >= prediction.decision_time
            or r["strategy"] != prediction.strategy_id
            or r["version"] != prediction.strategy_version
            or r["symbol"] != prediction.symbol
            or r["horizon"] != prediction.horizon
            or r["evidence_kind"] != pool
            or r.get("decision_offset") != prediction.features.value.get("decision_offset")
            or (regime is not None and r["regime"] != regime)
            or (
                "entry_delay_seconds" in prediction.features.value
                and r.get("entry_delay_seconds") != prediction.features.value["entry_delay_seconds"]
            )
            or (
                "entry_delay_seconds" in prediction.features.value
                and r.get("source") != prediction.context.value["source"]
            )
            or not r["active"]
        ):
            continue
        if r["prediction_id"] in ids:
            raise ValueError("duplicate economic observation")
        if r["resolved_at"] < r["decision_time"] + r["horizon"]:
            raise ValueError("outcome resolved before horizon")
        if finite(r["gross"]) <= -1:
            raise ValueError("invalid return")
        groups[iso(r["decision_time"])[:10]].append(finite(r["gross"]))
        ids.append(r["prediction_id"])
    values = [statistics.fmean(v) for _, v in sorted(groups.items())]
    n = len(values)
    mean = statistics.fmean(values) if n else 0.0
    se = statistics.stdev(values) / math.sqrt(n) if n > 1 else 1.0
    # Descriptive daily-cluster SE, not a multiple-search-adjusted confidence guarantee.
    return {
        "days": n,
        "mean_gross": mean,
        "standard_error": se,
        "downside_daily": sorted(values)[max(0, int(0.05 * n) - 1)] if n else -1.0,
        "evidence_digest": identity(sorted(ids)),
        "pool": pool,
        "asof": max((r["resolved_at"] for r in rows if r["prediction_id"] in ids), default=None),
    }


@dataclass(frozen=True)
class EconomicPlan:
    decision: TradePlan
    forecast: Payload
    economics: Payload
    entry_after: float
    entry_deadline: float
    exit_at: float
    research_only: bool
    rejection_reasons: tuple[str, ...]
    quote_time: float | None = None
    quote_max_age_seconds: int = 120
    schema_version: int = 2
    execution_policy: str = EVIDENCE_GATED

    @property
    def plan_id(self):
        from dataclasses import asdict

        value = asdict(self)
        if self.execution_policy == EVIDENCE_GATED:
            value.pop("execution_policy")
        return identity(value)


def build_plan(prediction, snapshot, rows, *, selected=True, evidence_policy="exact-v1"):
    if prediction.snapshot_id != snapshot_identity(snapshot):
        raise ValueError("prediction and snapshot mismatch")
    if evidence_policy == "exact-v1":
        evidence = economic_evidence(prediction, rows)
    elif evidence_policy == "opportunity-cohorts-v1":
        from .evidence_cohorts import cohort_evidence

        evidence = cohort_evidence(prediction, rows)
    else:
        raise ValueError("unknown economic evidence policy")
    spread = (snapshot.ask - snapshot.bid) / ((snapshot.ask + snapshot.bid) / 2) * 10000
    base = Costs(max(1.0, spread / 2), 1, 0.1)
    stress = Costs(max(2.0, spread / 2), 3, 0.2)
    mean, se = evidence["mean_gross"], evidence["standard_error"]
    lower = stress.net(max(-0.999, evidence.get("lower_gross_estimate", mean - 1.96 * se)))
    delay = prediction.features.value.get("entry_delay_seconds", 0)
    if not 0 <= finite(delay) < prediction.horizon:
        raise ValueError("invalid entry delay")
    reasons = []
    # Signal/window validity is independent of the initial quote lifetime.
    deadline = min(
        prediction.decision_time + delay + 120, prediction.decision_time + prediction.horizon
    )
    if not selected:
        reasons.append("selector rejection")
    if prediction.direction <= 0:
        reasons.append("abstention or unsupported short equity expression")
    if evidence["days"] < 20:
        reasons.append("fewer than 20 prior active daily clusters")
    reasons.extend(evidence.get("insufficient_reasons", []))
    if lower <= 0:
        reasons.append("stress-cost lower estimate is nonpositive")
    if evidence["asof"] is None or prediction.decision_time - evidence["asof"] > 30 * 86400:
        reasons.append("missing or stale evidence")
    kind = "NO_TRADE" if reasons else "UNDERLYING"
    decision = TradePlan(
        prediction.prediction_id,
        kind,
        snapshot.symbol if not reasons else None,
        snapshot.ask if not reasons else None,
        "; ".join(reasons) if reasons else "positive prior cost-adjusted equity edge",
    )
    return EconomicPlan(
        decision,
        Payload.of(
            {
                "strategy": prediction.strategy_id,
                "version": prediction.strategy_version,
                "symbol": prediction.symbol,
                "decision_time": prediction.decision_time,
                "horizon": prediction.horizon,
                "opportunity": prediction.features.plain(),
                "raw_expected_return": prediction.expected_return,
            }
        ),
        Payload.of(
            {
                **evidence,
                "mean_net": base.net(mean),
                "stress_mean_net": stress.net(mean),
                "lower_net_estimate": lower,
                "observed_spread_bps": spread,
                "base_side_cost": base.side,
                "stress_side_cost": stress.side,
                "max_loss_fraction": 1.0,
                "options": "research_only_missing_quote_outcomes",
                "uncertainty": (
                    "fixed comparable strata, max ordinary/HAC daily SE, t(4) critical, target gate and between-symbol discount; descriptive screen, not calibrated coverage"
                    if evidence_policy == "opportunity-cohorts-v1"
                    else "daily-cluster standard error; selection and serial dependence unadjusted"
                ),
            }
        ),
        prediction.decision_time + delay,
        deadline,
        prediction.decision_time + prediction.horizon,
        snapshot.evidence_kind != "prospective",
        tuple(reasons),
        snapshot.quote_time,
    )


def build_experimental_plan(prediction, snapshot, rows, *, session_open, session_close):
    """A predefined signal, not a proven edge; execution risk gates remain shared."""
    from .alpha_signals import FAMILIES

    plan = build_plan(prediction, snapshot, rows, evidence_policy="opportunity-cohorts-v1")
    reasons = []
    if prediction.strategy_id not in FAMILIES or prediction.direction != 1:
        reasons.append("no predefined long quantitative signal")
    if prediction.expected_return <= 0:
        reasons.append("nonpositive predefined quantitative signal")
    if (
        not session_open
        <= prediction.decision_time
        <= plan.entry_after
        < plan.entry_deadline
        < plan.exit_at
        <= session_close
    ):
        reasons.append("forecast horizon exceeds regular session or invalid entry window")
    if math.ceil(plan.exit_at / 60) * 60 > session_close:
        reasons.append("modeled endpoint exceeds regular session")
    decision = TradePlan(
        prediction.prediction_id,
        "NO_TRADE" if reasons else "UNDERLYING",
        None if reasons else snapshot.symbol,
        None if reasons else snapshot.ask,
        "; ".join(reasons)
        if reasons
        else "experimental predefined signal; profitability unestablished",
    )
    return replace(
        plan,
        decision=decision,
        rejection_reasons=tuple(reasons),
        execution_policy=EXPERIMENTAL,
        forecast=Payload.of(
            {
                **plan.forecast.plain(),
                "direction": prediction.direction,
                "session_open": session_open,
                "session_close": session_close,
            }
        ),
        economics=Payload.of({**plan.economics.plain(), "profitability_established": False}),
    )


def validate_execution_plan(plan, now):
    """Signal and entry window only. Transaction-time quotes remain independently fresh."""
    finite(now)
    decision_time = finite(plan.forecast.value["decision_time"])
    horizon = finite(plan.forecast.value["horizon"])
    if (
        plan.schema_version != 2
        or not decision_time <= plan.entry_after < plan.entry_deadline <= plan.exit_at
        or plan.exit_at != decision_time + horizon
        or plan.quote_max_age_seconds != 120
        or horizon <= 0
    ):
        raise Halt("plan timing/provenance is inconsistent")
    if plan.execution_policy not in {EVIDENCE_GATED, EXPERIMENTAL}:
        raise Halt("unknown execution policy")
    if plan.execution_policy == EXPERIMENTAL:
        from .alpha_signals import ALPHA_VERSION, FAMILIES, semantic_version

        forecast = plan.forecast.value
        from datetime import datetime
        from zoneinfo import ZoneInfo

        from ..calendar import session_bounds

        bounds = session_bounds(
            datetime.fromtimestamp(decision_time, ZoneInfo("America/New_York")).date()
        )
        if bounds != (forecast.get("session_open"), forecast.get("session_close")):
            raise Halt("experimental plan session differs from XNYS calendar")
        if (
            forecast.get("strategy") not in FAMILIES
            or semantic_version(forecast.get("version")) != ALPHA_VERSION
            or horizon not in (1800, 2100, 3600, 3900, 7200, 7500)
            or forecast.get("direction") != 1
            or forecast.get("raw_expected_return", 0) <= 0
            or not forecast.get("session_open", math.inf) <= decision_time <= plan.entry_after
            or not plan.entry_deadline < plan.exit_at <= forecast.get("session_close", -math.inf)
            or math.ceil(plan.exit_at / 60) * 60 > forecast.get("session_close", -math.inf)
            or plan.economics.value.get("profitability_established") is not False
        ):
            raise Halt("experimental plan lacks a frozen signal or realizable session window")
    if (
        plan.research_only
        or plan.decision.kind != "UNDERLYING"
        or plan.rejection_reasons
        or not plan.entry_after <= now < plan.entry_deadline
        or now >= plan.exit_at
        or (
            plan.execution_policy == EVIDENCE_GATED
            and (
                plan.economics.value["days"] < 20 or plan.economics.value["lower_net_estimate"] <= 0
            )
        )
        or plan.economics.value["pool"] != "prospective"
    ):
        raise Halt("plan is rejected, expired, awaiting its entry window or research-only")


def to_execution_intent(plan, quantity, now, *, order_type="limit", dollar_amount=None, quote=None):
    """Pure adapter. Fresh quote evidence and externally resolved sizing; no broker calls."""
    validate_execution_plan(plan, now)
    if quote is None:
        quote = {
            "asof": plan.quote_time,
            "observed_at": plan.quote_time,
            "ask": plan.decision.entry_limit,
        }
    if (
        quote.get("asof") is None
        or not quote["asof"] <= quote["observed_at"] <= now
        or now - quote["asof"] > plan.quote_max_age_seconds
    ):
        raise Halt("execution requires an actually fresh quote; initial quote cannot be renewed")
    if dec(quote["ask"]) > dec(plan.decision.entry_limit):
        raise Halt("entry price condition is no longer met")
    q = dec(quantity) if quantity is not None else None
    price = (
        dec(plan.decision.entry_limit).quantize(dec("0.01"), rounding=ROUND_DOWN)
        if order_type == "limit"
        else None
    )
    intent = Intent(
        plan.decision.instrument,
        "buy",
        q,
        price,
        order_type=order_type,
        dollar_amount=dec(dollar_amount) if dollar_amount is not None else None,
    )
    intent.validate()
    return intent


def plan_dict(plan):
    from dataclasses import asdict

    value = asdict(plan)
    # Preserve serialization of existing frozen evidence-gated plans for recovery.
    if plan.execution_policy == EVIDENCE_GATED:
        value.pop("execution_policy")
    return {
        **value,
        "rejection_reasons": list(plan.rejection_reasons),
        "forecast": plan.forecast.plain(),
        "economics": plan.economics.plain(),
        "signal_valid_until": plan.entry_deadline,
        "quote_freshness_seconds": plan.quote_max_age_seconds,
        "holding_seconds": plan.exit_at - plan.entry_after,
    }


def plan_from_dict(value):
    value = dict(value)
    for key in ("signal_valid_until", "quote_freshness_seconds", "holding_seconds"):
        value.pop(key, None)
    value["decision"] = TradePlan(**value["decision"])
    value["forecast"] = Payload.of(value["forecast"])
    value["economics"] = Payload.of(value["economics"])
    value["rejection_reasons"] = tuple(value["rejection_reasons"])
    return EconomicPlan(**value)


def execution_handoff(plan, prediction, snapshot, config, risk, now, limit, entry):
    """The production validated_plan_entry boundary owns all account/risk sizing checks."""
    from ..oneshot import validated_plan_entry

    validate_execution_plan(plan, now)
    intent, provenance = validated_plan_entry(
        plan, prediction, snapshot, config, risk, now, limit, entry
    )
    return {
        "intent": intent.payload(),
        "provenance": provenance,
        "exit_at": plan.exit_at,
        "orders_submitted": 0,
    }
