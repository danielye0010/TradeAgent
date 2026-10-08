"""Cost-aware plans wrapping the existing TradePlan; no persistence or broker ownership."""

import math
import statistics
from collections import defaultdict
from dataclasses import dataclass
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

    @property
    def plan_id(self):
        from dataclasses import asdict

        return identity(asdict(self))


def build_plan(prediction, snapshot, rows, *, selected=True):
    if prediction.snapshot_id != snapshot_identity(snapshot):
        raise ValueError("prediction and snapshot mismatch")
    evidence = economic_evidence(prediction, rows)
    spread = (snapshot.ask - snapshot.bid) / ((snapshot.ask + snapshot.bid) / 2) * 10000
    base = Costs(max(1.0, spread / 2), 1, 0.1)
    stress = Costs(max(2.0, spread / 2), 3, 0.2)
    mean, se = evidence["mean_gross"], evidence["standard_error"]
    lower = stress.net(max(-0.999, mean - 1.96 * se))
    delay = prediction.features.value.get("entry_delay_seconds", 0)
    if not 0 <= finite(delay) < prediction.horizon:
        raise ValueError("invalid entry delay")
    reasons = []
    deadline = min(prediction.decision_time + 120, snapshot.quote_time + 120)
    if prediction.decision_time + delay >= deadline:
        reasons.append("entry requires a fresh quote after the planned delay")
    if not selected:
        reasons.append("selector rejection")
    if prediction.direction <= 0:
        reasons.append("abstention or unsupported short equity expression")
    if evidence["days"] < 20:
        reasons.append("fewer than 20 prior active daily clusters")
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
                "uncertainty": "daily-cluster standard error; selection and serial dependence unadjusted",
            }
        ),
        prediction.decision_time + delay,
        deadline,
        prediction.decision_time + prediction.horizon,
        snapshot.evidence_kind != "prospective",
        tuple(reasons),
    )


def to_execution_intent(plan, quantity, now):
    """Pure whole-share limit-intent adapter; caller must still run existing risk checks.

    No account lookup, sizing, execution approval, order submission or exit scheduling.
    """
    finite(now)
    if (
        plan.research_only
        or plan.decision.kind != "UNDERLYING"
        or plan.rejection_reasons
        or not plan.entry_after <= now < plan.entry_deadline
        or now >= plan.exit_at
        or plan.economics.value["days"] < 20
        or plan.economics.value["lower_net_estimate"] <= 0
        or plan.economics.value["pool"] != "prospective"
    ):
        raise Halt("plan is rejected, expired or research-only")
    q = dec(quantity)
    if q <= 0 or q != q.to_integral_value():
        raise Halt("adapter requires externally sized positive whole shares")
    price = dec(plan.decision.entry_limit).quantize(dec("0.01"), rounding=ROUND_DOWN)
    intent = Intent(plan.decision.instrument, "buy", q, price)
    intent.validate()
    return intent
