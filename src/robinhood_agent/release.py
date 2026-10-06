"""Deterministic autonomous broker gate; legacy unbound callers remain closed."""

from dataclasses import asdict

from .model import Halt, digest

MILESTONE = "SIGNED_CANARY_ONLY"


def require_real_release(guard=None, intent=None, snapshot=None, baseline=None):
    from .calendar import regular_session
    from .policy import PolicyGuard, require_autonomous_release
    from .risk import check_order

    if type(guard) is not PolicyGuard or guard.simulation is not False:
        raise Halt("real broker execution capability disabled: signed production guard required")
    policy = guard.validate()
    require_autonomous_release(policy["phase"])
    if intent is None or snapshot is None or baseline is None:
        raise Halt("real release requires current order/account/risk context")
    if (
        digest(asdict(guard.config)) != guard.context.config_hash
        or digest(asdict(guard.risk)) != guard.context.risk_hash
    ):
        raise Halt("real release config/risk drift")
    if not regular_session(guard.clock()):
        raise Halt("real execution outside current XNYS regular session")
    guard.check(intent, snapshot, baseline)
    check_order(intent, snapshot, guard.config, guard.risk, guard.clock(), baseline)
