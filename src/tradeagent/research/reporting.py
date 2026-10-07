"""Prospective version/generation and selector-control comparisons from frozen evidence."""

import json
import statistics
from collections import defaultdict

from .domain import iso
from .learning import metric


def performance(store):
    rows = store.db.execute(
        "SELECT p.*,o.residual_return,s.evidence_kind,v.generation,c.selected,c.baseline_selected "
        "FROM predictions p JOIN outcomes o USING(prediction_id) JOIN market_snapshots s USING(snapshot_id) "
        "JOIN strategy_versions v ON p.strategy_id=v.strategy_id AND p.strategy_version=v.version "
        "JOIN selections c USING(prediction_id) WHERE o.residual_return IS NOT NULL"
    ).fetchall()
    grouped, selector = defaultdict(list), defaultdict(list)
    for row in rows:
        regime = json.loads(row["context"])["regime"]
        grouped[
            (
                row["evidence_kind"],
                row["strategy_id"],
                row["strategy_version"],
                row["generation"],
                regime,
            )
        ].append(row)
        for mode, chosen in (
            ("learned", row["selected"]),
            ("raw_baseline", row["baseline_selected"]),
        ):
            if chosen:
                selector[(row["evidence_kind"], mode, iso(row["decision_time"])[:10])].append(
                    metric(row)
                )
    versions = []
    for (kind, strategy, version, generation, regime), observations in grouped.items():
        by_day = defaultdict(list)
        for row in observations:
            by_day[iso(row["decision_time"])[:10]].append(metric(row))
        days = [statistics.fmean(v) for v in by_day.values()]
        versions.append(
            {
                "evidence_kind": kind,
                "strategy_id": strategy,
                "version": version,
                "generation": generation,
                "regime": regime,
                "predictions": len(observations),
                "daily_clusters": len(days),
                "mean_daily_metric": statistics.fmean(days),
                "daily_metric_std": statistics.pstdev(days),
                "metric_is_executable_pnl": False,
            }
        )
    modes = defaultdict(list)
    for (kind, mode, day), values in selector.items():
        modes[(kind, mode)].append((day, statistics.fmean(values)))
    return {
        "version_regime_performance": versions,
        "selector_comparison": [
            {
                "evidence_kind": kind,
                "mode": mode,
                "daily_clusters": len(days),
                "mean_daily_selected_metric": statistics.fmean(v for _, v in days),
                "days": days,
            }
            for (kind, mode), days in modes.items()
        ],
    }
