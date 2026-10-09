"""Fixed, outcome-blind comparability policy for daily opportunity research."""

import math
import statistics
from collections import defaultdict

from .domain import finite, identity, iso

POLICY = "opportunity-cohorts-v1"
MIN_DAYS = 20
MIN_TARGET_DAYS = 5
LOOKBACK_SECONDS = 180 * 86400
# t(4) 97.5% critical value: at least as conservative as 1.96 for every
# supported sample size (target >=5 days, cohort >=20). This remains a screen,
# not calibrated inference under market dependence or adaptive model search.
CRITICAL = 2.7764451051977987

BROAD_ETFS = {"SPY", "QQQ", "IWM", "DIA"}
SECTOR_ETFS = {"XLF", "XLK", "XLE", "XLV"}
STOCK_SECTORS = {
    **dict.fromkeys(("AAPL", "MSFT", "NVDA", "AVGO", "AMD", "ORCL", "CRM"), "technology"),
    **dict.fromkeys(("META", "GOOGL", "NFLX"), "communication"),
    **dict.fromkeys(("AMZN", "TSLA"), "consumer_discretionary"),
    **dict.fromkeys(("JPM", "GS"), "financials"),
    **dict.fromkeys(("LLY", "UNH"), "healthcare"),
    **dict.fromkeys(("XOM", "CVX"), "energy"),
    **dict.fromkeys(("COST", "WMT"), "consumer_staples"),
}


def comparison_context(features, economics=None):
    """Only frozen, decision-time features; never realized outcome volatility."""
    return {
        "opening_rv": features.get("opening_rv"),
        "observed_spread_bps": features.get(
            "observed_spread_bps", (economics or {}).get("observed_spread_bps")
        ),
    }


def cohort_key(symbol, minute, regime, context):
    """Predeclared asset/risk/liquidity/time strata, not learned from returns."""
    if minute is None or regime not in {"trend", "range"}:
        return None
    rv, spread = context.get("opening_rv"), context.get("observed_spread_bps")
    if rv is None or spread is None:
        return None
    minute, rv, spread = finite(minute), finite(rv), finite(spread)
    if not 0 <= minute < 390 or rv < 0 or spread < 0:
        return None
    group = (
        "broad_equity_etf"
        if symbol in BROAD_ETFS
        else "sector_equity_etf"
        if symbol in SECTOR_ETFS
        else "stock_sector:" + STOCK_SECTORS[symbol]
        if symbol in STOCK_SECTORS
        else "single_symbol:" + symbol
    )
    return {
        "asset_group": group,
        "decision_half_hour": int(minute // 30),
        "regime": regime,
        "opening_rv_band": "low" if rv < 0.003 else "medium" if rv < 0.01 else "high",
        "spread_band": "tight" if spread < 10 else "normal" if spread < 25 else "wide",
    }


def daily_summary(values):
    """One vote per day; max ordinary/HAC SE prevents negative-AC precision gains."""
    n = len(values)
    mean = statistics.fmean(values) if n else 0.0
    if n < 2:
        return {"days": n, "mean": mean, "se": 1.0}
    ordinary = statistics.stdev(values) / math.sqrt(n)
    residuals = [x - mean for x in values]
    long_variance = sum(x * x for x in residuals) / n
    lags = min(3, n - 1)
    for lag in range(1, lags + 1):
        covariance = sum(residuals[i] * residuals[i - lag] for i in range(lag, n)) / n
        long_variance += 2 * (1 - lag / (lags + 1)) * covariance
    hac = math.sqrt(max(0, long_variance) / (n - 1))
    return {"days": n, "mean": mean, "se": max(ordinary, hac)}


def cohort_evidence(prediction, rows):
    """Raw long returns: comparable peers plus an independent target-symbol gate.

    Same version/source/holding/delay/pool. Coarsen decision minute ONLY inside
    fixed strata. Multiple intraday signals first average per symbol-day; symbols
    then share one daily vote. Retain losses and discount between-symbol dispersion.
    """
    f, context = prediction.features.plain(), prediction.context.plain()
    key = cohort_key(prediction.symbol, f.get("decision_offset"), context.get("regime"), f)
    delay = f.get("entry_delay_seconds")
    matched, ids = [], set()
    if key is not None and delay is not None:
        for row in rows:
            if (
                not prediction.decision_time - LOOKBACK_SECONDS
                <= row["decision_time"]
                < prediction.decision_time
                or row["resolved_at"] >= prediction.decision_time
                or row["strategy"] != prediction.strategy_id
                or row["version"] != prediction.strategy_version
                or row.get("source") != context["source"]
                or row["evidence_kind"] != context["evidence_kind"]
                or row["horizon"] != prediction.horizon
                or row.get("entry_delay_seconds") != delay
                or not row["active"]
                or cohort_key(
                    row["symbol"],
                    row.get("decision_offset"),
                    row.get("regime"),
                    row.get("comparison_context", {}),
                )
                != key
            ):
                continue
            if row["prediction_id"] in ids:
                raise ValueError("duplicate economic observation")
            if row["resolved_at"] < row["decision_time"] + row["horizon"]:
                raise ValueError("outcome resolved before horizon")
            if finite(row["gross"]) <= -1:
                raise ValueError("invalid return")
            ids.add(row["prediction_id"])
            matched.append(row)
    days = defaultdict(lambda: defaultdict(list))
    for row in matched:
        days[iso(row["decision_time"])[:10]][row["symbol"]].append(row["gross"])
    symbol_days = defaultdict(list)
    pooled, target = [], []
    for _, symbols in sorted(days.items()):
        means = {s: statistics.fmean(values) for s, values in symbols.items()}
        pooled.append(statistics.fmean(means.values()))
        for symbol, mean in means.items():
            symbol_days[symbol].append(mean)
        if prediction.symbol in means:
            target.append(means[prediction.symbol])
    cohort, local = daily_summary(pooled), daily_summary(target)
    symbol_means = [statistics.fmean(values) for values in symbol_days.values()]
    dispersion = statistics.pstdev(symbol_means) if len(symbol_means) > 1 else 0.0
    lower = min(
        cohort["mean"] - CRITICAL * cohort["se"] - dispersion,
        local["mean"] - CRITICAL * local["se"],
    )
    reasons = []
    if key is None or delay is None:
        reasons.append("decision-time comparability context unavailable")
    if cohort["days"] < MIN_DAYS:
        reasons.append("fewer than 20 comparable prior active daily clusters")
    if local["days"] < MIN_TARGET_DAYS:
        reasons.append("fewer than 5 target-symbol prior active daily clusters")
    # Both target and cohort evidence must be recently represented. Missing
    # target support cannot be disguised by recent observations of other symbols.
    local_asof = max(
        (r["resolved_at"] for r in matched if r["symbol"] == prediction.symbol), default=None
    )
    cohort_asof = max((r["resolved_at"] for r in matched), default=None)
    return {
        "grouping_policy": POLICY,
        "cohort": key,
        "days": cohort["days"],
        "target_days": local["days"],
        "observations": len(matched),
        "symbols": sorted(symbol_days),
        "mean_gross": cohort["mean"],
        "standard_error": cohort["se"],
        "target_mean_gross": local["mean"],
        "target_standard_error": local["se"],
        "between_symbol_discount": dispersion,
        "critical_value": CRITICAL,
        "lower_gross_estimate": lower,
        "sufficient": not reasons,
        "insufficient_reasons": reasons,
        "downside_daily": sorted(pooled)[max(0, int(0.05 * len(pooled)) - 1)] if pooled else -1.0,
        "evidence_digest": identity([POLICY, key, sorted(ids)]),
        "pool": context["evidence_kind"],
        "asof": min(cohort_asof, local_asof) if local_asof is not None else None,
        "lookback_days": LOOKBACK_SECONDS // 86400,
    }
