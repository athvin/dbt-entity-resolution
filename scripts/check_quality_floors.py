#!/usr/bin/env python3
"""The quality floors must BLOCK, not report (3.85, §1.8/DR-22, G14, M12).

§1.8 is unambiguous about what these numbers are for:

    The floors are committed numbers, per fixture, and Stage 0.4 cannot complete
    without them. ... otherwise this stage reports and the product ships at 0.72.

They were committed -- `er_blocking_recall_floor`, `er_f1_floor`,
`er_max_cluster_size` in `dbt_project.yml`, with a `CODEOWNERS` entry so nobody
lowers one quietly. The measurement was written -- `scripts/measure_quality.py`.
**And nothing connected them.** `measure_quality.py`'s `main()` returned 0
unconditionally and appeared in no Makefile target, no pre-commit hook, no CI
step and no injection. G14 names this exact shape:

    a Makefile target reports; it does not stop a build.

So the floors existed, the measurement existed, and the product could have
shipped at any quality whatsoever with every gate green. This script is the
connection.

**Why parity gates cannot do this job.** Every §6.4 gate compares two engines;
none compares either engine to the truth. A reimplementation can be bit-identical
to Splink and still be a bad entity resolver -- §1.8 measures a +0.26 F1
improvement that is *invisible to every parity gate in the project*. These floors
are the only thing in the repository that looks at whether the output is good.

**The recall band is TWO-SIDED, and that is not symmetry for its own sake**
(M12 rec 2). Falling below is a regression in the code. Rising above means the
fixture or the oracle moved -- and since neither is supposed to move without a
deliberate re-mint, that is equally a finding. A one-sided floor would let a
fixture change sail through as an improvement.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import measure_quality
from _er_paths import ROOT

PROJECT = "dbt_project.yml"
FIXTURE = "fake_1000"

# Below this the check has stopped checking rather than the project having
# become simple -- §6.1's vacuous pass, the floor 3.83 and 3.84 also carry.
MIN_ASSERTIONS = 5


def _floors(root: Path) -> dict[str, Any]:
    """Read the committed numbers from where CODEOWNERS protects them."""
    loaded = yaml.safe_load((root / PROJECT).read_text(encoding="utf-8")) or {}
    return loaded.get("vars") or {}


def _recall_errors(band: dict[str, float] | None, recall: float) -> tuple[list[str], int]:
    """Judge the two-sided band (M12 rec 2). Both directions are findings."""
    if not band:
        return (
            [
                (
                    f"er_blocking_recall_floor has no entry for `{FIXTURE}`. A fixture "
                    f"with no committed floor is measured and never judged."
                )
            ],
            0,
        )
    if recall < band["min"]:
        return (
            [
                (
                    f"blocking recall {recall:.4f} is BELOW the committed floor "
                    f"{band['min']} for `{FIXTURE}`. Pairs the blocking rules never "
                    f"generate cannot be recovered by any later stage -- this is the "
                    f"ceiling on end-to-end recall (§1.8)."
                )
            ],
            1,
        )
    if recall > band["max"]:
        return (
            [
                (
                    f"blocking recall {recall:.4f} is ABOVE the committed ceiling "
                    f"{band['max']} for `{FIXTURE}`. The band is two-sided on purpose "
                    f"(M12 rec 2): rising means the fixture or the oracle moved, not "
                    f"that the code improved, and neither should move without a "
                    f"deliberate re-mint. Confirm which, then move the band."
                )
            ],
            1,
        )
    return [], 1


def _f1_errors(floors: dict[str, float], by_threshold: dict[str, Any]) -> tuple[list[str], int]:
    """Per threshold, because F1 varies ~0.17 across the three built together."""
    if not floors:
        return [f"er_f1_floor has no entry for `{FIXTURE}`."], 0
    errors: list[str] = []
    asserted = 0
    for threshold, floor in sorted(floors.items()):
        stats = by_threshold.get(str(threshold))
        if stats is None:
            errors.append(
                f"er_f1_floor names threshold {threshold} for `{FIXTURE}`, but the "
                f"measurement produced no such threshold. A floor over a threshold "
                f"nobody measures is asserted against nothing."
            )
            continue
        asserted += 1
        if stats["f1"] < floor:
            errors.append(
                f"F1 {stats['f1']:.4f} at threshold {threshold} is below the "
                f"committed floor {floor} for `{FIXTURE}` (precision "
                f"{stats['precision']:.4f}, recall {stats['recall']:.4f})."
            )
    return errors, asserted


def _cluster_errors(limit: int | None, sizes: dict[str, int]) -> tuple[list[str], int]:
    """Apply the HARD max-size test (M12 rec 3); cluster-level error amplifies.

    Measured edge precision 0.9764 against CLUSTER precision 0.7495 -- 14.8x.
    The committed value is the fixture's TRUE maximum, so any over-merge exceeds
    it immediately, which is the point and why this is not a warning.
    """
    if limit is None:
        return [f"er_max_cluster_size has no entry for `{FIXTURE}`."], 0
    errors: list[str] = []
    asserted = 0
    for threshold, size in sorted(sizes.items()):
        asserted += 1
        if size > limit:
            errors.append(
                f"max cluster size {size} at threshold {threshold} exceeds the "
                f"committed {limit} for `{FIXTURE}`. The committed value is the "
                f"fixture's TRUE maximum, so exceeding it means an over-merge has "
                f"already happened (M12 rec 3)."
            )
    return errors, asserted


def check(root: Path = ROOT, measured: dict[str, Any] | None = None) -> list[str]:
    """Return every floor the shipped model fails to clear."""
    floors = _floors(root)
    if measured is None:
        measured = measure_quality.measure()
        measured["max_cluster_size"] = measure_quality.max_cluster_size()

    errors: list[str] = []
    asserted = 0

    for found, count in (
        _recall_errors(
            (floors.get("er_blocking_recall_floor") or {}).get(FIXTURE),
            measured["blocking_recall"],
        ),
        _f1_errors(
            (floors.get("er_f1_floor") or {}).get(FIXTURE) or {},
            measured.get("by_threshold") or {},
        ),
        _cluster_errors(
            (floors.get("er_max_cluster_size") or {}).get(FIXTURE),
            measured.get("max_cluster_size") or {},
        ),
    ):
        errors.extend(found)
        asserted += count

    if asserted < MIN_ASSERTIONS:
        errors.append(
            f"only {asserted} floor assertion(s) were made, expected at least "
            f"{MIN_ASSERTIONS}. The floors were not read, or the measurement "
            f"produced nothing to judge -- either way this check passed without "
            f"checking (§6.1)."
        )

    sys.stdout.write(f"3.85: {asserted} quality floor assertion(s) checked.\n")
    return errors


def main() -> int:
    """Return 0 when the shipped model clears every committed floor."""
    errors = check()
    for err in errors:
        sys.stderr.write(f"ERROR: {err}\n")
    if errors:
        sys.stderr.write(
            f"\n{len(errors)} quality floor violation(s) (3.85).\n"
            f"Lowering a floor to make this pass is the §21 failure mode, and "
            f"CODEOWNERS covers the file it lives in.\n"
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
