"""Import externally reconciled fills; no broker I/O or alpha-training side effects."""

from .domain import canonical, finite, identity


def import_feedback(store, dataset, now):
    if dataset.get("reconciled") is not True:
        raise ValueError("execution feedback requires external reconciliation")
    imported = 0
    with store.db:
        for record in dataset["records"]:
            row = dict(record)
            for key in ("observed_at", "expected_entry", "quantity"):
                finite(row[key])
            for key in ("actual_entry", "actual_exit", "fees"):
                if row.get(key) is not None:
                    finite(row[key])
            if (
                not 0 < row["expected_entry"]
                or row["quantity"] <= 0
                or row["observed_at"] > now
                or not row["execution_key"]
                or row["status"] not in {"filled", "rejected", "failed", "pending"}
                or any(
                    row.get(k) is not None and row[k] < 0
                    for k in ("actual_entry", "actual_exit", "fees")
                )
            ):
                raise ValueError("invalid reconciled execution feedback")
            if row["status"] == "filled" and (
                row.get("actual_entry") is None or row.get("fees") is None
            ):
                raise ValueError("filled feedback needs actual entry and fees")
            prediction = store.db.execute(
                "SELECT decision_time FROM predictions WHERE prediction_id=?",
                (row["prediction_id"],),
            ).fetchone()
            if prediction is None or row["observed_at"] < prediction[0]:
                raise ValueError("execution feedback must follow a known prediction")
            row.setdefault(
                "live_id",
                identity([row["prediction_id"], row["execution_key"], row["observed_at"]]),
            )
            old = store.db.execute(
                "SELECT * FROM live_expressions WHERE live_id=?", (row["live_id"],)
            ).fetchone()
            if old:
                if any(old[k] != v for k, v in row.items()):
                    raise ValueError("execution observation cannot be revised")
            else:
                store.insert("live_expressions", row)
                imported += 1
        attribute_fills(store, now)
    return {"imported": imported, "real_broker_calls": 0}


def attribute_fills(store, now):
    rows = store.db.execute(
        "SELECT l.* FROM live_expressions l LEFT JOIN live_attributions a USING(live_id) WHERE a.live_id IS NULL"
    ).fetchall()
    for row in rows:
        slip = (
            row["actual_entry"] / row["expected_entry"] - 1
            if row["actual_entry"] is not None
            else None
        )
        label = (
            "fill_error"
            if row["status"] in {"failed", "rejected"} or (slip is not None and slip > 0.001)
            else (
                "pending_unresolved"
                if row["status"] == "pending"
                else "observed_without_fill_error_evidence"
            )
        )
        pnl = (
            (row["actual_exit"] - row["actual_entry"]) * row["quantity"] - row["fees"]
            if all(row[k] is not None for k in ("actual_entry", "actual_exit", "fees"))
            else None
        )
        store.insert(
            "live_attributions",
            {
                "live_id": row["live_id"],
                "classification": label,
                "slippage_fraction": slip,
                "realized_pnl": pnl,
                "observed_at": now,
                "evidence": canonical(
                    {
                        "execution_key": row["execution_key"],
                        "fees": row["fees"],
                        "reconciliation": "external_import_attestation",
                        "pnl_unit": "long unit; quantity must include contract multiplier",
                    }
                ),
            },
        )
