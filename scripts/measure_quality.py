#!/usr/bin/env python3
"""Measure the frozen model's quality, so DR-22's floors come from what ships (Stage 0.4).

§A.6 Q5's argument, which DR-22 turns into gates: **parity is not quality.** A
reimplementation can match Splink bit-for-bit and still be a bad entity
resolver, because every §6.4 gate compares two engines and none of them compares
either engine to the truth. §1.8's floors are what close that, and they are only
meaningful if they come from measuring the model that actually ships.

Everything here is computed from the ground-truth `cluster` column that
`fake_1000` carries (M12), against the baselines Stage 0.3 froze.

**Precision, recall and F1 are pair-level.** Cluster-level error amplifies --
M12 rec 3 measured edge precision 0.9764 against cluster precision 0.7495, a
14.8x difference -- which is why `er_max_cluster_size` is a separate hard test
rather than something the F1 floor is expected to catch.
"""

from __future__ import annotations

import itertools
import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from _er_paths import ROOT

if TYPE_CHECKING:
    from collections.abc import Iterable

FIXTURE = ROOT / "fixtures" / "source" / "fake_1000.csv"
BASELINES = ROOT / "fixtures" / "baselines" / "fake_1000"

GROUND_TRUTH_COLUMN = "cluster"
THRESHOLDS = (0.5, 0.9, 0.99)


def true_pairs(rows: Iterable[dict[str, Any]]) -> set[tuple[str, str]]:
    """Every within-cluster pair, from the ground-truth column.

    This is plain arithmetic over the fixture: 1,000 records in 251 clusters of
    size 1-7 give **2,031** pairs. Recorded because it is the denominator of
    every recall number below, and a recall figure whose denominator is not
    stated cannot be compared with anyone else's.
    """
    groups: dict[Any, list[str]] = {}
    for row in rows:
        groups.setdefault(row[GROUND_TRUTH_COLUMN], []).append(str(row["unique_id"]))
    pairs: set[tuple[str, str]] = set()
    for members in groups.values():
        pairs.update(itertools.combinations(sorted(members), 2))
    return pairs


def measure() -> dict[str, Any]:
    """Return every quality number the floors are set from."""
    import duckdb  # noqa: PLC0415

    con = duckdb.connect()
    try:
        rows = [
            dict(zip([d[0] for d in con.description or []], record, strict=True))
            for record in con.execute(
                "select unique_id, cluster from read_csv(?)", [str(FIXTURE)]
            ).fetchall()
        ]
        truth = true_pairs(rows)

        predictions = con.execute(
            "select unique_id_l, unique_id_r, match_probability from read_parquet(?)",
            [str(BASELINES / "predictions.parquet")],
        ).fetchall()
    finally:
        con.close()

    generated = {tuple(sorted((str(left), str(right)))) for left, right, _ in predictions}
    blocking_recall = len(generated & truth) / len(truth)

    per_threshold: dict[str, dict[str, float]] = {}
    for threshold in THRESHOLDS:
        matched = {
            tuple(sorted((str(left), str(right))))
            for left, right, probability in predictions
            if probability >= threshold
        }
        hits = len(matched & truth)
        precision = hits / len(matched) if matched else 0.0
        recall = hits / len(truth)
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        per_threshold[str(threshold)] = {
            "predicted_pairs": len(matched),
            "true_positives": hits,
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "f1": round(f1, 4),
        }

    return {
        "fixture": "fake_1000",
        "records": len(rows),
        "true_pairs": len(truth),
        "generated_pairs": len(generated),
        "blocking_recall": round(blocking_recall, 4),
        "by_threshold": per_threshold,
    }


def max_cluster_size() -> dict[str, int]:
    """Largest cluster at each threshold. M12 rec 3's hard test."""
    import duckdb  # noqa: PLC0415

    con = duckdb.connect()
    sizes: dict[str, int] = {}
    try:
        for threshold in THRESHOLDS:
            path = BASELINES / f"clusters_at_{str(threshold).replace('.', '_')}.parquet"
            row = con.execute(
                "select max(n) from ("
                "select count(*) as n from read_parquet(?) group by cluster_id)",
                [str(path)],
            ).fetchone()
            sizes[str(threshold)] = int(row[0]) if row and row[0] is not None else 0
    finally:
        con.close()
    return sizes


def main() -> int:
    """Print the quality measurement as JSON."""
    result = measure()
    result["max_cluster_size"] = max_cluster_size()
    sys.stdout.write(json.dumps(result, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
