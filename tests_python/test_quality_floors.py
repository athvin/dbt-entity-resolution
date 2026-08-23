"""3.57 for 3.85 and 3.86: the floor gates must be shown to fail.

These two are the only things in the repository that ask whether the output is
**good**. Every §6.4 parity gate compares two engines; none compares either
engine to the truth. §1.8 measures a +0.26 F1 improvement that is invisible to
all of them.

So a floor gate that cannot fail is worse than none — it is the thing that makes
"quality is enforced" look true. The cases below cover both directions of the
two-sided recall band (M12 rec 2), the per-threshold F1 floor, the hard
max-cluster-size test (M12 rec 3), and — the one that would let this rot —
asserting nothing at all.
"""

from __future__ import annotations

import copy
import pathlib
import shutil
import sys
from typing import Any

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import check_floor_subject  # noqa: E402
import check_quality_floors  # noqa: E402
import measure_quality  # noqa: E402


@pytest.fixture(scope="module")
def measured() -> dict[str, Any]:
    """Compute the real measurement once — it reads several parquet files."""
    result = measure_quality.measure()
    result["max_cluster_size"] = measure_quality.max_cluster_size()
    return result


def _with(measured: dict[str, Any], **patch: object) -> dict[str, Any]:
    out = copy.deepcopy(measured)
    out.update(patch)
    return out


def test_the_shipped_model_clears_every_committed_floor(measured: dict[str, Any]) -> None:
    """The baseline claim. Without it the failing cases below prove nothing."""
    assert check_quality_floors.check(ROOT, measured=measured) == []


def test_recall_below_the_band_is_caught(measured: dict[str, Any]) -> None:
    """The regression direction: pairs blocking never generates are unrecoverable."""
    errors = check_quality_floors.check(ROOT, measured=_with(measured, blocking_recall=0.70))
    assert len(errors) == 1
    assert "BELOW" in errors[0]


def test_recall_above_the_band_is_also_caught(measured: dict[str, Any]) -> None:
    """M12 rec 2, and the half a one-sided floor would miss.

    Rising recall means the fixture or the oracle moved, not that the code
    improved — and neither is supposed to move without a deliberate re-mint. A
    one-sided floor would wave that through as good news.
    """
    errors = check_quality_floors.check(ROOT, measured=_with(measured, blocking_recall=0.95))
    assert len(errors) == 1
    assert "ABOVE" in errors[0]


def test_an_f1_collapse_is_caught_per_threshold(measured: dict[str, Any]) -> None:
    """Per threshold, because F1 varies by ~0.17 across the three that build together.

    A single floor would be vacuous at t=0.5 or unmeetable at t=0.99.
    """
    broken = copy.deepcopy(measured)
    broken["by_threshold"]["0.9"]["f1"] = 0.10
    errors = check_quality_floors.check(ROOT, measured=broken)
    assert len(errors) == 1
    assert "0.9" in errors[0]


def test_an_over_merge_is_caught(measured: dict[str, Any]) -> None:
    """M12 rec 3: cluster-level error amplifies (edge precision 0.9764 vs cluster 0.7495).

    The committed value is the fixture's TRUE maximum cluster size, so any
    over-merge exceeds it immediately. That is why it is a hard test.
    """
    broken = copy.deepcopy(measured)
    broken["max_cluster_size"]["0.5"] = 400
    errors = check_quality_floors.check(ROOT, measured=broken)
    assert len(errors) == 1
    assert "over-merge" in errors[0]


def test_asserting_nothing_is_not_a_pass(measured: dict[str, Any]) -> None:
    """§6.1's vacuous pass — how a floor gate rots without anyone noticing."""
    errors = check_quality_floors.check(ROOT, measured=_with(measured, by_threshold={}))
    assert any("without checking" in e for e in errors)


def test_the_floor_subject_tripwire_is_quiet_today() -> None:
    """No model declares a score yet, so the floors measure the oracle by design."""
    assert check_floor_subject.check(ROOT) == []


def test_the_tripwire_fires_when_a_scored_column_appears(tmp_path: pathlib.Path) -> None:
    """The day Stage 5 lands, this is the only thing that says so.

    Every other gate stays green: a floor measured against the oracle passes
    exactly as well as it did the day before. Note the tripwire is keyed on the
    COLUMN NAME — the first version grepped for a literal path string that the
    measurement spells as separate path components, so it could never fire
    (D.0 finding 75).
    """
    root = tmp_path / "repo"
    shutil.copytree(ROOT / "models", root / "models")
    shutil.copytree(
        ROOT / "scripts", root / "scripts", ignore=shutil.ignore_patterns("__pycache__")
    )
    (root / "integration_tests" / "models").mkdir(parents=True, exist_ok=True)

    target = root / "models" / "intermediate" / "er_tf_all.yml"
    target.write_text(
        target.read_text(encoding="utf-8").replace(
            "      - name: tf\n        data_type: double",
            "      - name: match_probability\n        data_type: double\n"
            "      - name: tf\n        data_type: double",
        ),
        encoding="utf-8",
    )

    errors = check_floor_subject.check(root)
    assert len(errors) == 1
    assert "ER-086" in errors[0]
    assert "er_tf_all" in errors[0]
