"""Stage 0.4: the frozen model meets the floors, and the floors came from it.

§A.6 Q5's argument in one line: **parity is not quality.** Every §6.4 gate
compares two engines; none compares either engine to the truth. DR-22's floors
close that, and they are only worth having if they are (a) met by what ships and
(b) derived from measuring it rather than copied from a document.

Both halves are asserted here. The second matters because a floor set from
someone else's numbers is either unmeetable or vacuous, and there is no way to
tell which without measuring.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

# Same convention as `tests_python`: put `scripts/` on the path and import by
# module name, which is how these are invoked in anger. Importing them as
# `scripts.x` makes mypy resolve one file under two module names.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from measure_quality import max_cluster_size, measure

PROJECT = Path(__file__).resolve().parents[1] / "dbt_project.yml"
FIXTURE = "fake_1000"

# Recorded from `scripts/measure_quality.py` on the frozen baselines. A canary:
# if the model, the fixture or the toolchain moves, this moves with it.
MEASURED_TRUE_PAIRS = 2031
MEASURED_BLOCKING_RECALL = 0.8124


@pytest.fixture(scope="module")
def floors() -> dict[str, Any]:
    loaded = yaml.safe_load(PROJECT.read_text(encoding="utf-8"))["vars"]
    return dict(loaded)


def test_the_true_pair_count_is_what_the_fixture_contains() -> None:
    """Plain arithmetic over the ground-truth column, and the denominator of every recall.

    1,000 records in 251 clusters of size 1-7 give 2,031 within-cluster pairs.
    Asserted because **a recall figure whose denominator is not stated cannot be
    compared with anyone else's** -- which is exactly the difficulty §A.6 Q5's
    published figures presented.
    """
    assert measure()["true_pairs"] == MEASURED_TRUE_PAIRS


def test_blocking_recall_is_inside_its_two_sided_band(floors: dict[str, Any]) -> None:
    """M12 rec 2: failing ABOVE is a finding too.

    Below the band is a regression in the code. Above it means the fixture or the
    oracle moved, which is equally worth knowing and would otherwise look like
    an improvement.
    """
    band = floors["er_blocking_recall_floor"][FIXTURE]
    measured = measure()["blocking_recall"]
    assert band["min"] <= measured <= band["max"], (
        f"blocking recall {measured} is outside [{band['min']}, {band['max']}]. "
        f"Below: a regression. Above: the fixture or the oracle moved, not the code."
    )


@pytest.mark.parametrize("threshold", ["0.5", "0.9", "0.99"])
def test_f1_clears_its_floor(floors: dict[str, Any], threshold: str) -> None:
    floor = floors["er_f1_floor"][FIXTURE][threshold]
    measured = measure()["by_threshold"][threshold]["f1"]
    assert measured >= floor, (
        f"F1 at t={threshold} is {measured}, below the committed floor {floor}. "
        f"Parity gates cannot see this: two engines can agree exactly and both be wrong."
    )


@pytest.mark.parametrize("threshold", ["0.5", "0.9", "0.99"])
def test_no_cluster_exceeds_the_hard_cap(floors: dict[str, Any], threshold: str) -> None:
    """M12 rec 3: cluster-level error amplifies, so this is a hard test.

    Measured edge precision 0.9764 against cluster precision 0.7495 -- 14.8x. A
    single spurious edge merges two entities, and the F1 floor above, being
    pair-level, would barely notice.
    """
    cap = floors["er_max_cluster_size"][FIXTURE]
    largest = max_cluster_size()[threshold]
    assert largest <= cap, (
        f"largest cluster at t={threshold} is {largest}, over the cap of {cap}. "
        f"That is an over-merge, and it is the most damaging error an ER system "
        f"makes because downstream systems key on master records."
    )


def test_precision_is_saturated_at_every_threshold() -> None:
    """Corroborates §A.6 Q5 exactly, and explains why recall is the lever.

    Precision is 1.0000 at all three thresholds, so raising the threshold buys
    **no** precision and costs real recall. That is the measured basis for DR-22
    removing the default threshold rather than a preference.
    """
    for threshold in ("0.5", "0.9", "0.99"):
        assert measure()["by_threshold"][threshold]["precision"] == 1.0


def test_f1_falls_as_the_threshold_rises() -> None:
    """DR-22's finding, reproduced in direction on the vendored fixture.

    Measured 0.8267 -> 0.7717 -> 0.6532 across t = 0.5, 0.9, 0.99. §A.6 Q5
    reports the same shape at different magnitudes (0.9809 -> 0.9219). **The
    direction is what the decision rests on**: a default threshold of 0.9 was
    silently choosing worse output for no precision benefit.
    """
    by_threshold = measure()["by_threshold"]
    assert by_threshold["0.5"]["f1"] > by_threshold["0.9"]["f1"] > by_threshold["0.99"]["f1"]


def test_the_floors_are_below_measurement_but_not_vacuously_so(
    floors: dict[str, Any],
) -> None:
    """A floor far below what ships is decoration.

    Each F1 floor sits within 0.05 of the measured value: close enough that a
    real regression trips it, far enough that a patch release does not.
    """
    for threshold in ("0.5", "0.9", "0.99"):
        floor = floors["er_f1_floor"][FIXTURE][threshold]
        measured = measure()["by_threshold"][threshold]["f1"]
        assert 0 < measured - floor <= 0.05, (
            f"the F1 floor at t={threshold} is {floor} against a measured "
            f"{measured}: a gap of {measured - floor:.4f}. Too wide and it never "
            f"fires; at or above and it fires on nothing."
        )


# ---------------------------------------------------------------------------
# Added 2026-08-23 after an adversarial review of PR #42 found three ways to
# make these floors vacuous that the tests above do not reach (D.0 finding 78).
# They live HERE, beside the gate that already owns this job, rather than in a
# second script -- two copies of one logic is finding 69, and a duplicate that
# is strictly weaker than the original is worse than no duplicate at all.
# ---------------------------------------------------------------------------

# A two-sided band is made vacuous by WIDENING, not by lowering, and membership
# alone cannot see that: [0.0, 1.0] contains every possible measurement while
# still looking like a committed band.
MAX_BAND_WIDTH = 0.12

# Cluster size is an integer count and the committed value is supposed to BE the
# fixture's true maximum, so slack here is slack against an over-merge.
MAX_SIZE_SLACK = 1


def test_the_recall_band_is_narrow_enough_to_fail(floors: dict[str, Any]) -> None:
    """Membership is not enough: `[0.0, 1.0]` passes it and asserts nothing."""
    band = floors["er_blocking_recall_floor"][FIXTURE]
    width = band["max"] - band["min"]
    assert width <= MAX_BAND_WIDTH, (
        f"the blocking-recall band spans {width:.4f} "
        f"({band['min']}-{band['max']}), wider than {MAX_BAND_WIDTH}. A band that "
        f"wide admits everything -- it is a decoration, not a gate."
    )


def test_the_cluster_cap_is_not_slack_against_an_over_merge(
    floors: dict[str, Any],
) -> None:
    """M12 rec 3: the committed value IS the fixture's true maximum.

    Raising it is the cheapest possible way to let an over-merge through, and
    cluster-level error amplifies -- measured edge precision 0.9764 against
    CLUSTER precision 0.7495, 14.8x.
    """
    limit = floors["er_max_cluster_size"][FIXTURE]
    measured = max(max_cluster_size().values())
    assert limit <= measured + MAX_SIZE_SLACK, (
        f"er_max_cluster_size is {limit} against a measured maximum of "
        f"{measured}. Every unit of slack is a merge this gate will not catch."
    )


def test_every_measured_threshold_has_a_committed_floor(
    floors: dict[str, Any],
) -> None:
    """Coverage must be SYMMETRIC, and the tests above only check one direction.

    They read a hard-coded ("0.5", "0.9", "0.99"). A fourth threshold added to
    the measurement would be reported and never judged -- and deleting a floor
    is the cheapest way to stop a gate failing, because it leaves the
    measurement in place so the output still looks complete.
    """
    committed = {str(k) for k in floors["er_f1_floor"][FIXTURE]}
    measured = {str(k) for k in measure()["by_threshold"]}
    assert measured <= committed, (
        f"threshold(s) {sorted(measured - committed)} are measured but have no "
        f"committed F1 floor. Measured and never judged is indistinguishable "
        f"from passing."
    )
