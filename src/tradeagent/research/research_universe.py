"""Frozen broad discovery pool; adoption requires a measured collection profile."""

from .opportunities import UNIVERSE

RESEARCH_UNIVERSE = tuple(
    UNIVERSE
    + """
BAC C WFC MS SCHW V MA AXP BLK PGR CB
JNJ MRK ABBV PFE AMGN GILD TMO ABT ISRG MDT BMY CVS
HD LOW TGT NKE SBUX MCD KO PEP PM MO
ADBE INTU NOW IBM CSCO QCOM INTC MU TXN AMAT LRCX KLAC PANW CRWD PLTR
GE CAT DE HON RTX LMT BA UPS UNP FDX WM
DIS CMCSA T VZ TMUS BKNG ABNB PYPL COIN
SLB COP OXY XLU XLI XLP XLY
""".split()
)

# A fixed prefix permits reproducible fallback, with SPY/QQQ always present.
COLLECTION_BUDGET_SECONDS = 45
MIN_DOLLAR_VOLUME_PER_MINUTE = 1_000_000
MAX_EQUITY_SPREAD = 0.005


def feasible_universe(profile):
    """Choose the largest measured prefix, never extrapolate from per-symbol latency."""
    if profile.get("provider") != "robinhood":
        raise ValueError("universe profile must use the official Robinhood interface")
    valid = [
        r
        for r in profile["measurements"]
        if r["symbols"] == list(RESEARCH_UNIVERSE[: len(r["symbols"])])
        and r["complete"]
        and 0 < r["elapsed_seconds"] <= COLLECTION_BUDGET_SECONDS
        and r["interval"] == "minute"
        and r["bounds"] == "regular"
    ]
    if not valid:
        raise ValueError("no complete measured universe fits the collection budget")
    return tuple(max(valid, key=lambda r: len(r["symbols"]))["symbols"])


def liquid_candidates(report, dataset):
    """Use observed spread and completed volume, not assumed liquidity from a ticker."""
    from .opportunities import grouped

    bars = grouped(dataset, report["decision_time"])
    for candidate in report["candidate_pool"]:
        symbol = candidate["symbol"]
        q = dataset.get("quotes", {}).get(symbol, {})
        path = bars.get(symbol, [])[-30:]
        volume = sum(b.close * b.volume for b in path) / len(path) if path else 0
        bid, ask = q.get("bid", 0), q.get("ask", 0)
        liquid = (
            0 < bid <= ask
            and (ask - bid) / ((ask + bid) / 2) <= MAX_EQUITY_SPREAD
            and volume >= MIN_DOLLAR_VOLUME_PER_MINUTE
        )
        candidate["liquidity"] = {"eligible": liquid, "mean_dollar_volume_per_minute": volume}
        if not liquid:
            candidate["status"] = "REJECTED"
    report["candidates"] = sorted(
        (c for c in report["candidate_pool"] if c["status"] == "CANDIDATE"),
        key=lambda c: (-c["ranking_score"], c["symbol"]),
    )[:3]
    from .domain import identity

    report["scan_id"] = identity({k: v for k, v in report.items() if k != "scan_id"})
    return report
