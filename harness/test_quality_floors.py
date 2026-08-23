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
