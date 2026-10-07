"""Transparent ranking, with raw-score control and strictly prior learner state."""

import json
import math


def rank(store, predictions, snapshot, max_selected=3):
    if type(max_selected) is not int or not 0 <= max_selected <= 3:
        raise ValueError("selection is bounded to 0-3 candidates")
    ranked = []
    directional = [p.direction for p in predictions if p.direction]
    for p in predictions:
        baseline = abs(p.expected_return) * p.confidence
        prior = store.db.execute(
            "SELECT sc.*,l.asof FROM strategy_scores sc JOIN learning_runs l USING(run_id) "
            "WHERE sc.strategy_id=? AND sc.version=? AND l.asof<? "
            "AND json_extract(l.configuration,'$.evidence_kind')=? ORDER BY l.asof DESC LIMIT 1",
            (p.strategy_id, p.strategy_version, p.decision_time, snapshot.evidence_kind),
        ).fetchone()
        regime = store.db.execute(
            "SELECT rs.* FROM regime_scores rs JOIN learning_runs l USING(run_id) "
            "WHERE rs.strategy_id=? AND rs.version=? AND rs.regime=? AND l.asof<? "
            "AND json_extract(l.configuration,'$.evidence_kind')=? ORDER BY l.asof DESC LIMIT 1",
            (
                p.strategy_id,
                p.strategy_version,
                json.loads(p.context.encoded)["regime"],
                p.decision_time,
                snapshot.evidence_kind,
            ),
        ).fetchone()
        weight = prior["weight"] if prior else 1.0
        calibration = prior["calibration"] if prior else 1.0
        compatibility = regime["compatibility"] if regime else 1.0
        agreement = (
            sum(d == p.direction for d in directional) / len(directional) if directional else 0.0
        )
        score = (
            baseline * weight * calibration * math.sqrt(compatibility) * (0.75 + 0.5 * agreement)
        )
        v = store.db.execute(
            "SELECT s.champion_version,v.role FROM strategies s JOIN strategy_versions v USING(strategy_id) "
            "WHERE v.strategy_id=? AND v.version=?",
            (p.strategy_id, p.strategy_version),
        ).fetchone()
        eligible = bool(
            p.direction and v["champion_version"] == p.strategy_version and v["role"] != "control"
        )
        ranked.append(
            {
                "prediction_id": p.prediction_id,
                "score": score,
                "baseline_score": baseline,
                "eligible": eligible,
                "state_asof": prior["asof"] if prior else None,
                "rationale": {
                    "weight": weight,
                    "calibration": calibration,
                    "regime_compatibility": compatibility,
                    "agreement": agreement,
                    "baseline": "raw absolute expected return times confidence",
                },
            }
        )
    ranked.sort(key=lambda item: (-item["score"], item["prediction_id"]))
    raw_ranking = sorted(ranked, key=lambda item: (-item["baseline_score"], item["prediction_id"]))
    raw_selected = 0
    for index, row in enumerate(raw_ranking, 1):
        row["baseline_rank"] = index
        row["baseline_selected"] = bool(
            row["eligible"] and raw_selected < max_selected and row["baseline_score"] > 0
        )
        raw_selected += row["baseline_selected"]
    selected = 0
    for index, row in enumerate(ranked, 1):
        row["rank"] = index
        row["selected"] = bool(row.pop("eligible") and selected < max_selected and row["score"] > 0)
        selected += row["selected"]
    return ranked
