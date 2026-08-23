"""§12.7's mutant catalogue, adopted as written (3.46, `DesignDoc` Stage 0.7).

Each mutant takes a **known-good** output and introduces one defect that a
comparator must detect. The suite fails if any mutant survives.

The point is not that the comparator returns *something*. §12.7: *"each mutant
asserts not just failure but the expected localisation string. A comparator that
fails for the wrong reason is still broken, and it is the harder defect to notice
later because the gate looks like it is working."* So every entry carries the
`Difference.kind` it must provoke.

Two of the eight exist because of measured Splink behaviour rather than
speculation: `match_key` is **VARCHAR** (`blocking.py:203-206`), and clustering
emits spurious **NULL-node rows** on dangling edges
(`connected_components.py:89-100`).
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

    from harness.comparators import Table


@dataclass(frozen=True)
class Mutant:
    """One defect, and the localisation string the comparator must produce."""

    name: str
    catches: str
    target: str  # "pairs" or "clusters"
    apply: Callable[[Table], Table]
    expect_kind: str


def _copy(table: Table) -> Table:
    return copy.deepcopy(table)


# --- 1. Drop a pair / add a pair -------------------------------------------


def _drop_a_pair(table: Table) -> Table:
    rows = _copy(table)
    rows.pop(0)
    return rows


def _add_a_pair(table: Table) -> Table:
    rows = _copy(table)
    extra = copy.deepcopy(rows[0])
    extra["unique_id_l"] = "zzz-inserted"
    rows.append(extra)
    return rows


# --- 2/3. match_key: flip, and coerce to INT --------------------------------


def _flip_match_key(table: Table) -> Table:
    rows = _copy(table)
    rows[0]["match_key"] = "2" if rows[0]["match_key"] == "1" else "1"
    return rows


def _coerce_match_key_to_int(table: Table) -> Table:
    """`match_key` is VARCHAR in Splink; an INT that compares equal is the trap."""
    rows = _copy(table)
    for row in rows:
        row["match_key"] = int(row["match_key"])
    return rows


# --- 4. Change one gamma by +/- 1 -------------------------------------------


def _shift_one_gamma(table: Table) -> Table:
    rows = _copy(table)
    rows[0]["gamma_surname"] = rows[0]["gamma_surname"] + 1
    return rows


# --- 5. Shift one match_weight by 2x tolerance ------------------------------


def _shift_one_weight(table: Table) -> Table:
    """Twice the A.4 budget, so it must land outside tolerance, not inside it."""
    from harness.comparators import MW_ABS_TOL, MW_REL_TOL  # noqa: PLC0415

    rows = _copy(table)
    weight = rows[0]["match_weight"]
    rows[0]["match_weight"] = weight + 2 * (MW_ABS_TOL + MW_REL_TOL * abs(weight))
    return rows


# --- 6. Swap unique_id_l / unique_id_r on one pair ---------------------------


def _swap_pair_order(table: Table) -> Table:
    """D3's `l.uid < r.uid` makes ordering canonical; assuming it is not checking it."""
    rows = _copy(table)
    rows[0]["unique_id_l"], rows[0]["unique_id_r"] = (
        rows[0]["unique_id_r"],
        rows[0]["unique_id_l"],
    )
    return rows


# --- 7. Merge two clusters / split one / relabel one -------------------------


def _merge_two_clusters(table: Table) -> Table:
    rows = _copy(table)
    labels = sorted({row["component_label"] for row in rows})
    for row in rows:
        if row["component_label"] == labels[1]:
            row["component_label"] = labels[0]
    return rows


def _split_one_cluster(table: Table) -> Table:
    rows = _copy(table)
    target = min({row["component_label"] for row in rows})
    members = [row for row in rows if row["component_label"] == target]
    members[-1]["component_label"] = "split-off"
    return rows


def _relabel_one_component(table: Table) -> Table:
    """Rename a whole component -- the mutant that distinguishes the gate from the fallback.

    A.4 makes **label** equality the gate and partition equality a diagnostic,
    because D4 proves the labels are identical -- so a pure relabelling is a real
    failure that partition equality alone would pass. This mutant is what proves
    the comparator uses the primary gate and not the weaker one.
    """
    rows = _copy(table)
    target = min({row["component_label"] for row in rows})
    for row in rows:
        if row["component_label"] == target:
            row["component_label"] = f"renamed-{target}"
    return rows


# --- 8. Inject one NULL key --------------------------------------------------


def _inject_null_key(table: Table) -> Table:
    """Add a NULL-keyed row.

    Splink emits spurious NULL-node rows on dangling edges; a comparator that
    drops them before diffing also drops real rows.
    """
    rows = _copy(table)
    extra = copy.deepcopy(rows[0])
    extra["unique_id"] = None
    extra["component_label"] = None
    rows.append(extra)
    return rows


CATALOGUE: tuple[Mutant, ...] = (
    Mutant(
        "drop_a_pair",
        "set comparison collapsed to a count",
        "pairs",
        _drop_a_pair,
        "missing_row",
    ),
    Mutant(
        "add_a_pair",
        "set comparison collapsed to a count",
        "pairs",
        _add_a_pair,
        "extra_row",
    ),
    Mutant(
        "flip_match_key",
        "key ignored, or compared after coercion",
        "pairs",
        _flip_match_key,
        "value_differs:match_key",
    ),
    Mutant(
        "coerce_match_key_to_int",
        "the VARCHAR-normalisation failure (blocking.py:203-206)",
        "pairs",
        _coerce_match_key_to_int,
        "value_differs:match_key",
    ),
    Mutant(
        "shift_one_gamma",
        "column-wise comparison replaced by row-count",
        "pairs",
        _shift_one_gamma,
        "value_differs:gamma_surname",
    ),
    Mutant(
        "shift_one_match_weight",
        "tolerance applied in the wrong space, or not at all",
        "pairs",
        _shift_one_weight,
        "weight_outside_tolerance",
    ),
    Mutant(
        "swap_pair_order",
        "canonical ordering assumed rather than checked",
        "pairs",
        _swap_pair_order,
        "extra_row",
    ),
    Mutant(
        "merge_two_clusters",
        "partition equality degraded to cluster-count equality",
        "clusters",
        _merge_two_clusters,
        "cluster_label_differs",
    ),
    Mutant(
        "split_one_cluster",
        "partition equality degraded to cluster-count equality",
        "clusters",
        _split_one_cluster,
        "cluster_label_differs",
    ),
    Mutant(
        "relabel_one_component",
        "label equality degraded to partition equality (M6)",
        "clusters",
        _relabel_one_component,
        "cluster_label_differs",
    ),
    Mutant(
        "inject_null_key",
        "NULL rows silently dropped before diffing (connected_components.py:89-100)",
        "clusters",
        _inject_null_key,
        "extra_node",
    ),
)
