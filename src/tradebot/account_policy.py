"""Explicit caller-scoped account policy, verified against official 2026-10-05 metadata."""

from .model import Halt, dec

POLICY = "agentic-unleveraged-2026-10-05-v1"
SOURCE = "https://robinhood.com/us/en/support/articles/setting-up-an-agent/"


def eligible(account):
    if (
        account.get("agentic_allowed") is not True
        or account.get("state") != "active"
        or account.get("deactivated") is not False
        or account.get("permanently_deactivated") is not False
        or account.get("brokerage_account_type") != "individual"
        or account.get("management_type") not in (None, "", "self_directed")
    ):
        raise Halt(
            "account is not an active dedicated caller-accessible individual Agentic account"
        )
    if account.get("type") not in {"cash", "limited_margin"}:
        raise Halt("ordinary margin or unknown account type is ineligible")
    # Dedicated Agentic limited margin provides settlement access, never borrowing.
    return POLICY


def capital(portfolio):
    bp = portfolio.get("buying_power")
    if (
        not isinstance(bp, dict)
        or portfolio.get("currency") != "USD"
        or bp.get("display_currency") != "USD"
    ):
        raise Halt("missing authoritative USD buying power")
    cash, pending, generic, unleveraged = (
        dec(v)
        for v in (
            portfolio.get("cash"),
            portfolio.get("pending_deposits"),
            bp.get("buying_power"),
            bp.get("unleveraged_buying_power"),
        )
    )
    if min(cash, pending, generic, unleveraged) < 0:
        raise Halt("cash or buying power deficit")
    # Cash includes early pending deposits; exclude these. Generic BP can only
    # reduce this ceiling, never increase it. unsettled_funds is lagging information,
    # explicitly NOT authoritative for gating in the official schema.
    return min(max(dec(0), cash - pending), unleveraged, generic)
