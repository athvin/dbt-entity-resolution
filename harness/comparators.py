"""The parity comparators, written to `DesignDoc` A.4's corrected tolerance table.

**A.4, not §6.1.** §6.1 omits the relative term, the `mw > 54` vacuity rule and
the float-aggregate row, and its clusters row contradicts Stage 6's own
acceptance criterion (RC13). Where the two disagree, A.4 is in force.

| artefact | gate |
|---|---|
| pair sets, `match_key` (VARCHAR), gammas, TF | exact after canonical ordering |
| `match_weight` | exact bit equality expected; `1e-9 + 1e-12*abs(mw)`
  permitted **with a divergence-log entry** |
| `match_probability` | derived, never asserted independently |
| edge-set membership | **the binding gate** -- boolean agreement per threshold |
| clusters | **label** equality primary; partition equality diagnostic only |
| float aggregates | **not a gate** -- advisory only |

**Rows are plain dicts, deliberately.** A DataFrame layer would apply dtype
inference between the baseline and the comparison, and two of the eight mutants
in §12.7's catalogue exist precisely because that coercion hides real
divergence: `match_key` is **VARCHAR** in Splink (`blocking.py:203-206`), so a
comparator that normalises it to INT reports equality where Splink would report
a difference. Making coercion impossible to do accidentally is worth more here
than the convenience.

**Every comparator reports *what* differs and *where*, never a bare boolean.**
§12.7: "a comparator that fails for the wrong reason is still broken, and it is
the harder defect to notice later because the gate looks like it is working."
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

Row = dict[str, Any]
Table = list[Row]

# A.4: `1e-9 + 1e-12*abs(mw)`. Exact bit equality is the expected result; this is
# what a divergence-log entry may buy, not what the gate defaults to.
MW_ABS_TOL = 1e-9
MW_REL_TOL = 1e-12

# A.4 addition 1: `2**54/(1+2**54) == 1.0` exactly in float64, so *any* absolute
# probability tolerance passes for free above this -- in exactly the region where
# Splink's clamp and its 'Infinity' sentinel diverge most violently.
MW_PROBABILITY_VACUOUS_ABOVE = 54.0

# The derived probability bound's floating-point slack: 4 * 2**-53.
P_EPSILON = 4 * 2.0**-53

_LN2 = math.log(2.0)


@dataclass(frozen=True)
class Difference:
    """One located disagreement. `kind` is the string a mutant asserts on."""

    artefact: str
    kind: str
    key: str
    detail: str

    def __str__(self) -> str:
        """Render the difference as one greppable line."""
        return f"[{self.artefact}] {self.kind} at {self.key}: {self.detail}"


def _key_of(row: Row, key_columns: Sequence[str]) -> tuple[Any, ...]:
    """Return the join key, with **no coercion** and NULLs preserved.

    Splink's clustering emits spurious NULL-node rows on dangling edges
    (`connected_components.py:89-100`). A comparator that drops NULL keys before
    diffing also drops real rows, so NULL is a value here, not an absence.
    """
    return tuple(row.get(column) for column in key_columns)


def _fmt(key: tuple[Any, ...]) -> str:
    return "(" + ", ".join(repr(part) for part in key) + ")"


def _index(
    table: Table, key_columns: Sequence[str], artefact: str
) -> tuple[dict[tuple[Any, ...], Row], list[Difference]]:
    """Index rows by key, reporting duplicates rather than silently keeping one."""
    indexed: dict[tuple[Any, ...], Row] = {}
    problems: list[Difference] = []
    for row in table:
        key = _key_of(row, key_columns)
        if key in indexed:
            problems.append(
                Difference(
                    artefact,
                    "duplicate_key",
                    _fmt(key),
                    "appears more than once; a comparator that indexes by key would "
                    "silently keep one of them",
                )
            )
            continue
        indexed[key] = row
    return indexed, problems


def _guard_empty(actual: Table, expected: Table, artefact: str) -> list[Difference]:
    """Report the failure §12.7 opens with: zero rows compared to zero rows.

    "A comparator with a wrong join key returns '0 differences' by comparing zero
    rows to zero rows. Every stage is green, PARITY.md ships claiming verified
    equivalence, and the first real divergence surfaces in production as
    mis-merged entities."

    So an empty comparison is a defect in the comparison, never a pass.
    """
    if not actual and not expected:
        return [
            Difference(
                artefact,
                "empty_comparison",
                "-",
                "both sides are empty. This is indistinguishable from a wrong join "
                "key or a var typo, and it is not evidence of equality.",
            )
        ]
    return []


def compare_exact(
    actual: Table,
    expected: Table,
    *,
    key_columns: Sequence[str],
    value_columns: Sequence[str],
    artefact: str,
) -> list[Difference]:
    """Exact equality after canonical ordering (A.4 row 1).

    Covers pair sets, `match_key` as VARCHAR, gammas and TF tables. Types are
    compared as well as values: `'1'` and `1` are a difference, because in
    Splink they are.
    """
    differences = _guard_empty(actual, expected, artefact)

    actual_idx, dup_a = _index(actual, key_columns, artefact)
    expected_idx, dup_e = _index(expected, key_columns, artefact)
    differences.extend(dup_a)
    differences.extend(dup_e)

    differences.extend(
        Difference(artefact, "extra_row", _fmt(key), "present in actual, absent from the baseline")
        for key in sorted(actual_idx.keys() - expected_idx.keys(), key=repr)
    )
    differences.extend(
        Difference(
            artefact, "missing_row", _fmt(key), "present in the baseline, absent from actual"
        )
        for key in sorted(expected_idx.keys() - actual_idx.keys(), key=repr)
    )

    for key in sorted(actual_idx.keys() & expected_idx.keys(), key=repr):
        left, right = actual_idx[key], expected_idx[key]
        for column in value_columns:
            got, want = left.get(column), right.get(column)
            if got == want and type(got) is type(want):
                continue
            differences.append(
                Difference(
                    artefact,
                    f"value_differs:{column}",
                    _fmt(key),
                    f"actual {got!r} ({type(got).__name__}) != baseline {want!r} "
                    f"({type(want).__name__})",
                )
            )
    return differences


def compare_match_weight(
    actual: Table,
    expected: Table,
    *,
    key_columns: Sequence[str],
    column: str = "match_weight",
    artefact: str = "match_weight",
    logged_divergences: Iterable[str] = (),
) -> list[Difference]:
    """Exact bit equality expected; tolerance only with a divergence-log entry (A.4 row 2).

    A.4's standing note is why the default is exactness, not tolerance: *"both
    engines run float8 on the same DuckDB; where the expression tree is identical
    the result is identical."* A.3 addition 3 measured the real headroom at
    `2.842e-14` across all 2,880 gamma vectors of a production model, while the
    smallest **semantic** bug in the document's finding set is `0.1665` bits --
    about `1.7e13` times the gate. Tolerance is not where the risk lives.
    """
    differences = _guard_empty(actual, expected, artefact)
    permitted = set(logged_divergences)

    actual_idx, dup_a = _index(actual, key_columns, artefact)
    expected_idx, dup_e = _index(expected, key_columns, artefact)
    differences.extend(dup_a)
    differences.extend(dup_e)

    for key in sorted(actual_idx.keys() & expected_idx.keys(), key=repr):
        got = actual_idx[key].get(column)
        want = expected_idx[key].get(column)
        if got is None or want is None:
            differences.append(
                Difference(artefact, "null_weight", _fmt(key), f"actual {got!r}, baseline {want!r}")
            )
            continue
        if got == want:
            continue

        delta = abs(got - want)
        budget = MW_ABS_TOL + MW_REL_TOL * abs(want)
        if delta > budget:
            differences.append(
                Difference(
                    artefact,
                    "weight_outside_tolerance",
                    _fmt(key),
                    f"|{got!r} - {want!r}| = {delta:.6g} exceeds {budget:.6g}",
                )
            )
        elif _fmt(key) not in permitted:
            differences.append(
                Difference(
                    artefact,
                    "weight_within_tolerance_but_unlogged",
                    _fmt(key),
                    f"differs by {delta:.6g} (within {budget:.6g}), but A.4 permits a "
                    f"tolerance only WITH a divergence-log entry, and there is none",
                )
            )
    return differences


def compare_match_probability(
    actual: Table,
    expected: Table,
    *,
    key_columns: Sequence[str],
    weight_column: str = "match_weight",
    probability_column: str = "match_probability",
    artefact: str = "match_probability",
) -> list[Difference]:
    """Compare probabilities against a bound DERIVED from the weight difference (A.4 row 3).

    `dp/dmw = ln2 * p(1-p)`, so the admissible probability difference follows from
    the admissible weight difference. Above `abs(mw) > 54` the derived bound is
    vacuous -- `2**54/(1+2**54)` is exactly `1.0` in float64 -- so A.4 requires
    `p == 1.0` exactly there and records that probability parity carries no
    information in that region.
    """
    differences = _guard_empty(actual, expected, artefact)

    actual_idx, _ = _index(actual, key_columns, artefact)
    expected_idx, _ = _index(expected, key_columns, artefact)

    for key in sorted(actual_idx.keys() & expected_idx.keys(), key=repr):
        left, right = actual_idx[key], expected_idx[key]
        got, want = left.get(probability_column), right.get(probability_column)
        if got is None or want is None:
            differences.append(
                Difference(artefact, "null_probability", _fmt(key), f"{got!r} vs {want!r}")
            )
            continue

        baseline_mw = right.get(weight_column)
        if baseline_mw is not None and abs(baseline_mw) > MW_PROBABILITY_VACUOUS_ABOVE:
            expected_exact = 1.0 if baseline_mw > 0 else 0.0
            if got != expected_exact:
                differences.append(
                    Difference(
                        artefact,
                        "probability_not_exact_in_vacuous_region",
                        _fmt(key),
                        f"abs(mw)={abs(baseline_mw)} > {MW_PROBABILITY_VACUOUS_ABOVE}, where A.4 "
                        f"requires p == {expected_exact} exactly; got {got!r}",
                    )
                )
            continue

        delta_mw = abs((left.get(weight_column) or 0.0) - (baseline_mw or 0.0))
        bound = _LN2 * want * (1.0 - want) * delta_mw + P_EPSILON
        if abs(got - want) > bound:
            differences.append(
                Difference(
                    artefact,
                    "probability_outside_derived_bound",
                    _fmt(key),
                    f"|{got!r} - {want!r}| = {abs(got - want):.6g} exceeds the bound "
                    f"{bound:.6g} derived from dmw={delta_mw:.6g}",
                )
            )
    return differences


def compare_edge_membership(
    actual: Table,
    expected: Table,
    *,
    key_columns: Sequence[str],
    thresholds: Sequence[float],
    probability_column: str = "match_probability",
    artefact: str = "edge_membership",
) -> list[Difference]:
    """Assert the binding gate (A.4 row 4, new in A.4 and absent from §6.1).

    A.4 addition 2: *"neither tolerance protects the only property that changes
    downstream: whether a pair lands on the same side of the threshold."* Two
    weights can agree to `1e-14` and still put a pair on opposite sides of `t`.
    This is the row that decides what the pipeline actually emits.
    """
    differences = _guard_empty(actual, expected, artefact)
    if not thresholds:
        return [
            Difference(
                artefact,
                "no_thresholds",
                "-",
                "no thresholds supplied, so this gate asserted nothing. The binding "
                "gate cannot be satisfied by an empty threshold list.",
            )
        ]

    actual_idx, _ = _index(actual, key_columns, artefact)
    expected_idx, _ = _index(expected, key_columns, artefact)

    for threshold in thresholds:
        for key in sorted(actual_idx.keys() & expected_idx.keys(), key=repr):
            got = actual_idx[key].get(probability_column)
            want = expected_idx[key].get(probability_column)
            if got is None or want is None:
                continue
            if (got >= threshold) != (want >= threshold):
                differences.append(
                    Difference(
                        artefact,
                        "edge_membership_differs",
                        f"{_fmt(key)} @ t={threshold}",
                        f"actual p={got!r} -> {got >= threshold}, baseline p={want!r} "
                        f"-> {want >= threshold}",
                    )
                )
    return differences


def compare_clusters(
    actual: Table,
    expected: Table,
    *,
    node_column: str = "unique_id",
    label_column: str = "component_label",
    artefact: str = "clusters",
) -> list[Difference]:
    """Label equality is the gate; partition equality is a diagnostic (A.4 row 5).

    D4 proves the labels are identical, so partition equality -- which passes on
    any consistent relabelling -- **hides real drift** (M6). The weaker check is
    still computed, because when labels differ it says whether the partition also
    changed, which is the difference between a rename and a merge.
    """
    differences = _guard_empty(actual, expected, artefact)

    actual_idx, dup_a = _index(actual, [node_column], artefact)
    expected_idx, dup_e = _index(expected, [node_column], artefact)
    differences.extend(dup_a)
    differences.extend(dup_e)

    differences.extend(
        Difference(artefact, "extra_node", _fmt(key), "clustered in actual, absent from baseline")
        for key in sorted(actual_idx.keys() - expected_idx.keys(), key=repr)
    )
    differences.extend(
        Difference(artefact, "missing_node", _fmt(key), "clustered in baseline, absent from actual")
        for key in sorted(expected_idx.keys() - actual_idx.keys(), key=repr)
    )

    shared = sorted(actual_idx.keys() & expected_idx.keys(), key=repr)
    relabelled = [
        key
        for key in shared
        if actual_idx[key].get(label_column) != expected_idx[key].get(label_column)
    ]
    for key in relabelled:
        differences.append(
            Difference(
                artefact,
                "cluster_label_differs",
                _fmt(key),
                f"actual {actual_idx[key].get(label_column)!r} != baseline "
                f"{expected_idx[key].get(label_column)!r}",
            )
        )

    if relabelled:
        differences.append(
            Difference(
                artefact,
                "partition_diagnostic",
                "-",
                "partition is OTHERWISE IDENTICAL -- a pure relabelling"
                if _same_partition(actual_idx, expected_idx, label_column)
                else "partition ALSO differs -- membership changed, not just labels",
            )
        )
    return differences


def _same_partition(
    actual_idx: dict[tuple[Any, ...], Row],
    expected_idx: dict[tuple[Any, ...], Row],
    label_column: str,
) -> bool:
    """Report whether the groupings match under a consistent renaming (fallback only)."""

    def grouping(index: dict[tuple[Any, ...], Row]) -> set[frozenset[tuple[Any, ...]]]:
        groups: dict[Any, set[tuple[Any, ...]]] = {}
        for key, row in index.items():
            groups.setdefault(row.get(label_column), set()).add(key)
        return {frozenset(members) for members in groups.values()}

    return grouping(actual_idx) == grouping(expected_idx)
