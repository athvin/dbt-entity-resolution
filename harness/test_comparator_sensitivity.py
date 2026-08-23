"""No mutant may survive (3.46, §12.7, `DesignDoc` Stage 0.7).

**Every parity claim this project will ever make rests on comparator code that
nothing proves can fail.** M10 calls this the single most valuable missing test
in the repository and sizes it at one day; §12.7 calls it "the cheapest
credibility available".

Two properties are asserted, and the second is the one that matters:

1. the comparator finds **no** differences between a known-good output and itself;
2. for every mutant in the catalogue, it finds a difference **with the expected
   localisation string** — not merely *a* difference.

A comparator that fails for the wrong reason is still broken, and it is harder to
notice later because the gate looks like it is working.
"""

from __future__ import annotations

import pytest

from harness import known_good
from harness.comparators import (
    Difference,
    Table,
    compare_clusters,
    compare_edge_membership,
    compare_exact,
    compare_match_probability,
    compare_match_weight,
)
from harness.mutants import CATALOGUE, Mutant


def _compare_pairs(actual: Table, expected: Table) -> list[Difference]:
    """Every pair-grain gate A.4 defines, run together."""
    return [
        *compare_exact(
            actual,
            expected,
            key_columns=known_good.PAIR_KEYS,
            value_columns=known_good.PAIR_VALUES,
            artefact="pairs",
        ),
        *compare_match_weight(actual, expected, key_columns=known_good.PAIR_KEYS),
        *compare_match_probability(actual, expected, key_columns=known_good.PAIR_KEYS),
        *compare_edge_membership(
            actual,
            expected,
            key_columns=known_good.PAIR_KEYS,
            thresholds=known_good.THRESHOLDS,
        ),
    ]


def _compare_clusters(actual: Table, expected: Table) -> list[Difference]:
    return compare_clusters(
        actual,
        expected,
        node_column=known_good.CLUSTER_NODE,
        label_column=known_good.CLUSTER_LABEL,
    )


def _run(mutant: Mutant) -> list[Difference]:
    if mutant.target == "pairs":
        return _compare_pairs(mutant.apply(known_good.pairs()), known_good.pairs())
    return _compare_clusters(mutant.apply(known_good.clusters()), known_good.clusters())


# --- Property 1: the unmutated output compares clean -------------------------


def test_known_good_pairs_compare_clean() -> None:
    assert _compare_pairs(known_good.pairs(), known_good.pairs()) == []


def test_known_good_clusters_compare_clean() -> None:
    assert _compare_clusters(known_good.clusters(), known_good.clusters()) == []


# --- Property 2: no mutant survives, and each is localised --------------------


@pytest.mark.parametrize("mutant", CATALOGUE, ids=lambda m: m.name)
def test_no_mutant_survives(mutant: Mutant) -> None:
    """Fails if the mutant survives, OR if it is caught for the wrong reason."""
    differences = _run(mutant)
    assert differences, (
        f"MUTANT SURVIVED: {mutant.name} ({mutant.catches}). The comparator "
        f"reported no difference, so every parity claim it backs is unfounded."
    )
    kinds = {d.kind for d in differences}
    assert mutant.expect_kind in kinds, (
        f"{mutant.name} was caught, but for the wrong reason. Expected kind "
        f"{mutant.expect_kind!r}, got {sorted(kinds)}. A comparator that fails for "
        f"the wrong reason is still broken."
    )


def test_the_catalogue_covers_every_mutant_section_12_7_names() -> None:
    """§12.7's table has eight rows; three of them are three cases each.

    Asserted by name so that deleting a mutant is a test failure rather than a
    quietly smaller suite -- the decay 3.38 exists to prevent, one level down.
    """
    required = {
        "drop_a_pair",
        "add_a_pair",
        "flip_match_key",
        "coerce_match_key_to_int",
        "shift_one_gamma",
        "shift_one_match_weight",
        "swap_pair_order",
        "merge_two_clusters",
        "split_one_cluster",
        "relabel_one_component",
        "inject_null_key",
    }
    assert {m.name for m in CATALOGUE} == required


# --- The failures the comparator must not have ------------------------------


def test_an_empty_comparison_is_a_failure_not_a_pass() -> None:
    """§12.7's opening failure: zero rows compared to zero rows reads as equality."""
    differences = _compare_pairs([], [])
    assert any(d.kind == "empty_comparison" for d in differences)


def test_edge_membership_with_no_thresholds_is_a_failure() -> None:
    """The binding gate cannot be satisfied by supplying nothing to check."""
    differences = compare_edge_membership(
        known_good.pairs(),
        known_good.pairs(),
        key_columns=known_good.PAIR_KEYS,
        thresholds=(),
    )
    assert any(d.kind == "no_thresholds" for d in differences)


def test_a_weight_inside_tolerance_still_fails_without_a_divergence_entry() -> None:
    """A.4 permits tolerance only WITH a divergence-log entry."""
    from harness.comparators import MW_ABS_TOL  # noqa: PLC0415

    mutated = known_good.pairs()
    mutated[0]["match_weight"] += MW_ABS_TOL / 2
    differences = compare_match_weight(
        mutated, known_good.pairs(), key_columns=known_good.PAIR_KEYS
    )
    assert any(d.kind == "weight_within_tolerance_but_unlogged" for d in differences)


def test_a_logged_divergence_permits_the_same_difference() -> None:
    from harness.comparators import MW_ABS_TOL  # noqa: PLC0415

    mutated = known_good.pairs()
    mutated[0]["match_weight"] += MW_ABS_TOL / 2
    differences = compare_match_weight(
        mutated,
        known_good.pairs(),
        key_columns=known_good.PAIR_KEYS,
        logged_divergences=["('a-001', 'a-002')"],
    )
    assert differences == []


def test_probability_is_not_asserted_independently_of_the_weight() -> None:
    """A.4 row 3: the admissible probability difference is DERIVED from dmw.

    A probability that moves without its weight moving is outside the derived
    bound, however small it looks in absolute terms.
    """
    mutated = known_good.pairs()
    mutated[2]["match_probability"] = 0.5 + 1e-9
    differences = compare_match_probability(
        mutated, known_good.pairs(), key_columns=known_good.PAIR_KEYS
    )
    assert any(d.kind == "probability_outside_derived_bound" for d in differences)


def test_probability_parity_is_vacuous_above_mw_54_and_requires_exactness() -> None:
    """A.4 addition 1: above abs(mw)=54, assert p == 1.0 exactly or assert nothing."""
    mutated = known_good.pairs()
    mutated[3]["match_probability"] = 0.9999999999999
    differences = compare_match_probability(
        mutated, known_good.pairs(), key_columns=known_good.PAIR_KEYS
    )
    assert any(d.kind == "probability_not_exact_in_vacuous_region" for d in differences)


def test_edge_membership_catches_what_the_weight_tolerance_permits() -> None:
    """A.4 addition 2, demonstrated: agreement to 1e-14 can still flip the decision.

    This is why edge membership is the binding gate and not a convenience. The
    weight gate passes here -- the difference is inside tolerance and logged --
    and the pair still lands on opposite sides of t = 0.9.
    """
    baseline = known_good.pairs()
    baseline[0]["match_probability"] = 0.9
    mutated = known_good.pairs()
    mutated[0]["match_probability"] = 0.9 - 1e-15

    assert compare_match_weight(mutated, baseline, key_columns=known_good.PAIR_KEYS) == []
    differences = compare_edge_membership(
        mutated, baseline, key_columns=known_good.PAIR_KEYS, thresholds=(0.9,)
    )
    assert any(d.kind == "edge_membership_differs" for d in differences)


def test_a_pure_relabelling_is_reported_as_such() -> None:
    """The diagnostic that separates a rename from a merge."""
    from harness.mutants import _relabel_one_component  # noqa: PLC0415

    differences = _compare_clusters(
        _relabel_one_component(known_good.clusters()), known_good.clusters()
    )
    diagnostics = [d for d in differences if d.kind == "partition_diagnostic"]
    assert diagnostics
    assert "pure relabelling" in diagnostics[0].detail


def test_a_merge_is_reported_as_a_partition_change() -> None:
    from harness.mutants import _merge_two_clusters  # noqa: PLC0415

    differences = _compare_clusters(
        _merge_two_clusters(known_good.clusters()), known_good.clusters()
    )
    diagnostics = [d for d in differences if d.kind == "partition_diagnostic"]
    assert diagnostics
    assert "ALSO differs" in diagnostics[0].detail


def test_duplicate_keys_are_reported_not_silently_deduplicated() -> None:
    mutated = known_good.pairs()
    mutated.append(dict(mutated[0]))
    differences = _compare_pairs(mutated, known_good.pairs())
    assert any(d.kind == "duplicate_key" for d in differences)
