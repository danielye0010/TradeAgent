"""Research-only option comparison using actual entry/exit sides, never simulated marks."""

from .domain import finite


def long_premium(entry, exit_quote, fees_per_contract=0.65):
    if finite(fees_per_contract) < 0:
        raise ValueError("negative fee")
    keys = ("contract_id", "underlying", "kind", "expiration", "strike", "multiplier")
    if any(getattr(entry, k) != getattr(exit_quote, k) for k in keys):
        raise ValueError("contract mismatch")
    if exit_quote.asof <= entry.asof or exit_quote.available_at < entry.available_at:
        raise ValueError("exit must follow entry")
    debit = entry.ask * entry.multiplier
    pnl = (exit_quote.bid - entry.ask) * entry.multiplier - 2 * fees_per_contract
    return {
        "pnl": pnl,
        "entry_debit": debit,
        "max_loss": debit + 2 * fees_per_contract,
        "return_on_debit": pnl / debit,
        "fill_model": "entry ask, later bid; fees per contract",
        "research_only": True,
    }


def debit_spread(long_entry, short_entry, long_exit, short_exit, fees_per_contract=0.65):
    if (
        long_entry.underlying != short_entry.underlying
        or long_entry.kind != short_entry.kind
        or long_entry.expiration != short_entry.expiration
        or long_entry.asof != short_entry.asof
        or long_exit.asof != short_exit.asof
    ):
        raise ValueError("spread requires synchronized same-expiry/same-type books")
    if (long_entry.kind == "call" and long_entry.strike >= short_entry.strike) or (
        long_entry.kind == "put" and long_entry.strike <= short_entry.strike
    ):
        raise ValueError("not a directional debit spread")
    long_premium(long_entry, long_exit, fees_per_contract)
    long_premium(short_entry, short_exit, fees_per_contract)
    debit = (long_entry.ask - short_entry.bid) * 100
    width = abs(long_entry.strike - short_entry.strike) * 100
    if not 0 < debit < width:
        raise ValueError("invalid executable debit")
    credit = (long_exit.bid - short_exit.ask) * 100
    pnl = credit - debit - 4 * fees_per_contract
    return {
        "entry_debit": debit,
        "pnl": pnl,
        "return_on_debit": pnl / debit,
        "max_loss": debit + 4 * fees_per_contract,
        "max_expiry_profit": width - debit - 4 * fees_per_contract,
        "research_only": True,
        "fill_model": "buy ask/sell bid per leg; no midpoint fills",
    }
