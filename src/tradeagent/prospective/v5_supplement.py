"""Read-only supplementary matching; frozen v5 events/config/primary stay untouched."""

import argparse
import json
import sqlite3
import time
from pathlib import Path

from ..research.domain import canonical, iso
from .continuous import PROTOCOL, cluster, summarize


class SavedV5:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.db = sqlite3.connect(f"file:{self.directory / 'experience.sqlite3'}?mode=ro", uri=True)
        self.db.execute("PRAGMA query_only=ON")

    def records(self, kind):
        return [
            json.loads(r[0])
            for r in self.db.execute(
                "SELECT payload FROM continuous_events WHERE kind=? ORDER BY at,key", (kind,)
            )
        ]


def supplement(directory, now=None):
    now = time.time() if now is None else now
    reader = SavedV5(directory)
    try:
        original = summarize(reader, now)
        opportunities = {o["id"]: o for o in reader.records("opportunity")}
        endpoints = {(e["opportunity_id"], e["delay"]): e for e in reader.records("endpoint")}
    finally:
        reader.db.close()
    rows = original["outcomes"]
    paired = {}
    baselines = {}
    for direction, label in ((1, "long"), (-1, "theoretical_bearish")):
        selected = [r for r in rows if r["direction"] == direction and r["horizon_eligible"]]
        indexed = {(r["id"], r["delay_minutes"]): r for r in selected}
        paired[label] = {}
        for delay in (3, 5, 10):
            matches = [
                (a, indexed[(a["id"], delay)])
                for a in selected
                if a["delay_minutes"] == 1
                and a["selection"]
                and indexed.get((a["id"], delay), {}).get("selection")
            ]
            paired[label][f"1_vs_{delay}"] = {
                "raw_later_minus_1": cluster(
                    [
                        (
                            a["session"],
                            b["selection"]["net_modeled_return"]
                            - a["selection"]["net_modeled_return"],
                        )
                        for a, b in matches
                    ]
                ),
                "policy_later_minus_1": cluster(
                    [
                        (a["session"], b["trade_or_cash_net"] - a["trade_or_cash_net"])
                        for a, b in matches
                    ]
                ),
            }
        baselines[label] = {}
        for delay in (1, 3, 5, 10):
            baselines[label][str(delay)] = {
                name: cluster(
                    [
                        (r["session"], r["selection"]["net_modeled_return"] - r[field])
                        for r in selected
                        if r["delay_minutes"] == delay and r["selection"] and r[field] is not None
                    ]
                )
                for name, field in (("spy", "spy_net"), ("frozen_basket", "basket_net"))
            }
    completeness = {}
    for period in ("morning", "midday", "afternoon"):
        completeness[period] = {}
        for delay in (1, 3, 5, 10):
            candidates = [r for r in rows if r["period"] == period and r["delay_minutes"] == delay]
            value = {
                "frozen_opportunities": len(candidates),
                "horizon_eligible": sum(r["horizon_eligible"] for r in candidates),
                "entry_observed": sum(r["entry_observed"] for r in candidates),
                "own_quote_outcome_complete": sum(r["selection"] is not None for r in candidates),
                "original_full_comparison_complete": sum(
                    r["status"] == "COMPLETE" for r in candidates
                ),
                "entry_missing": 0,
                "exit_missing": 0,
                "pending": 0,
            }
            for row in candidates:
                if not row["horizon_eligible"]:
                    continue
                o = opportunities[row["id"]]
                entry = endpoints.get((o["id"], delay * 60))
                exit_obs = endpoints.get((o["id"], 3600))
                value["entry_missing"] += bool(
                    now >= o["detected_at"] + delay * 60 + 60
                    and (not entry or o["symbol"] not in entry.get("quotes", {}))
                )
                value["exit_missing"] += bool(
                    now >= o["exit_at"] + 60
                    and (not exit_obs or o["symbol"] not in exit_obs.get("quotes", {}))
                )
                value["pending"] += bool(not row["selection"] and now < o["exit_at"] + 60)
            completeness[period][str(delay)] = value
    return {
        "report": "v5-supplementary-matching-v1",
        "source_protocol": PROTOCOL,
        "frozen_source_version": original["version"],
        "generated_at": now,
        "original_primary_matched_n": original["matched_opportunities"],
        "pairwise": paired,
        "per_delay_baselines": baselines,
        "completeness": completeness,
        "limitations": "supplementary pairwise cohorts differ; session clusters, explicit n, no rewriting primary or backfill",
    }


def render(r):
    lines = [
        "# v5 supplementary saved-evidence report",
        f"Generated: {iso(r['generated_at'])}",
        f"Frozen v5 version: {r['frozen_source_version']}; original four-delay matched n={r['original_primary_matched_n']}",
        "Original primary comparison is unchanged. Each supplementary metric has its own matched cohort.",
        "Differences are later delay minus 1 minute; uncertainty uses session clusters. Bearish remains theoretical.",
        "",
        "|Direction|Pair|Matched opportunities|Sessions|Mean later-minus-1 net|Session SE|",
        "|---|---|---:|---:|---:|---:|",
    ]
    for label, pairs in r["pairwise"].items():
        for name, view in pairs.items():
            s = view["raw_later_minus_1"]
            lines.append(
                f"|{label}|{name}|{s['opportunities']}|{s['sessions']}|{s['session_mean']}|{s['session_standard_error']}|"
            )
    lines += [
        "",
        "|Direction|Delay|Benchmark|Matched n|Sessions|Mean excess net|",
        "|---|---:|---|---:|---:|---:|",
    ]
    for direction, delays in r["per_delay_baselines"].items():
        for delay, benchmarks in delays.items():
            for benchmark, s in benchmarks.items():
                lines.append(
                    f"|{direction}|{delay}|{benchmark}|{s['opportunities']}|{s['sessions']}|{s['session_mean']}|"
                )
    lines += [
        "",
        "|Period|Delay|Frozen|Horizon eligible|Entry observed|Own outcome|Entry missing|Exit missing|Pending|",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for period, delays in r["completeness"].items():
        for delay, s in delays.items():
            lines.append(
                f"|{period}|{delay}|{s['frozen_opportunities']}|{s['horizon_eligible']}|{s['entry_observed']}|{s['own_quote_outcome_complete']}|{s['entry_missing']}|{s['exit_missing']}|{s['pending']}|"
            )
    return "\n".join(lines) + "\n"


def write_report(directory, result):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    for suffix, text in (("json", canonical(result)), ("md", render(result))):
        path = directory / ("v5-supplementary." + suffix)
        temp = path.with_suffix(path.suffix + ".tmp")
        temp.write_text(text)
        temp.replace(path)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--state-dir", type=Path, required=True)
    p.add_argument("--output-dir", type=Path, required=True)
    args = p.parse_args()
    result = supplement(args.state_dir)
    write_report(args.output_dir, result)
    print(
        canonical(
            {
                k: result[k]
                for k in ("report", "frozen_source_version", "original_primary_matched_n")
            }
        )
    )


if __name__ == "__main__":
    main()
