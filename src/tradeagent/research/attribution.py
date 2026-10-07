"""Numerical fact labels first. Unknown regime/execution facts stay unknown."""

import json

from .domain import canonical


def attribute(store, now):
    count = 0
    with store.db:
        rows = store.db.execute(
            "SELECT p.*,o.raw_return,o.residual_return,o.mfe,o.mae FROM predictions p "
            "JOIN outcomes o USING(prediction_id) LEFT JOIN attributions a USING(prediction_id) "
            "WHERE a.prediction_id IS NULL"
        ).fetchall()
        for p in rows:
            raw, direction = p["raw_return"], p["direction"]
            noise = max(0.001, (json.loads(p["features"])["volatility"] or 0) * 0.25)
            alpha = (
                ("correct_abstention" if abs(raw) <= noise else "missed_move")
                if direction == 0
                else (
                    "insufficient_edge_noise"
                    if abs(raw) <= noise
                    else "direction_correct"
                    if raw * direction > 0
                    else "direction_error"
                )
            )
            error = abs(raw - p["expected_return"])
            calibration = (
                "magnitude_error"
                if error > max(noise, 2 * abs(p["expected_return"]))
                else "within_coarse_tolerance"
            )
            timing = (
                "path_reversal"
                if direction and raw * direction < 0 and p["mfe"] > noise
                else "no_path_reversal_evidence"
            )
            cfs = [
                dict(r)
                for r in store.db.execute(
                    "SELECT * FROM counterfactuals WHERE prediction_id=?", (p["prediction_id"],)
                )
            ]
            bad_options = [
                r["expression_id"]
                for r in cfs
                if r["kind"] in {"LONG_CALL", "LONG_PUT"}
                and r["status"] == "available"
                and r["pnl"] < 0
                and (
                    (r["kind"] == "LONG_CALL" and direction > 0)
                    or (r["kind"] == "LONG_PUT" and direction < 0)
                )
            ]
            expression = (
                "correct_thesis_poor_option_expression"
                if alpha == "direction_correct" and bad_options
                else "no_expression_error_evidence"
            )
            if all(
                r["status"] != "available" for r in cfs if r["kind"] in {"LONG_CALL", "LONG_PUT"}
            ):
                expression = "option_evaluation_unavailable"
            live = [
                dict(r)
                for r in store.db.execute(
                    "SELECT * FROM live_expressions WHERE prediction_id=?", (p["prediction_id"],)
                )
            ]
            execution = "unobserved_shadow_only"
            if live:
                execution = (
                    "fill_error"
                    if any(
                        r["status"] in {"rejected", "failed"}
                        or (
                            r["actual_entry"] is not None
                            and r["actual_entry"] > r["expected_entry"] * 1.001
                        )
                        for r in live
                    )
                    else "observed_without_fill_error_evidence"
                )
            context = json.loads(p["context"])
            regime = "insufficient_prior_regime_evidence"
            prior = store.db.execute(
                "SELECT rs.* FROM regime_scores rs JOIN learning_runs l USING(run_id) "
                "WHERE strategy_id=? AND version=? AND regime=? AND l.asof<? "
                "AND json_extract(l.configuration,'$.evidence_kind')=? ORDER BY l.asof DESC LIMIT 1",
                (
                    p["strategy_id"],
                    p["strategy_version"],
                    context["regime"],
                    p["decision_time"],
                    context["evidence_kind"],
                ),
            ).fetchone()
            if prior and prior["n"] >= 10:
                regime = (
                    "regime_mismatch_evidence"
                    if prior["mean_return"] < 0 and alpha == "direction_error"
                    else "prior_regime_compatible"
                )
            evidence = {
                "raw_return": raw,
                "expected_return": p["expected_return"],
                "absolute_error": error,
                "noise_tolerance": noise,
                "mfe": p["mfe"],
                "mae": p["mae"],
                "bad_option_counterfactuals": bad_options,
                "live_observation_ids": [r["live_id"] for r in live],
                "labels_are_diagnostics_not_causal_proof": True,
            }
            store.insert(
                "attributions",
                {
                    "prediction_id": p["prediction_id"],
                    "alpha": alpha,
                    "calibration": calibration,
                    "timing": timing,
                    "regime": regime,
                    "expression": expression,
                    "execution": execution,
                    "evidence": canonical(evidence),
                    "created_at": now,
                },
            )
            count += 1
    return count
