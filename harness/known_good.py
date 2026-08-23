"""Known-good outputs for the comparator sensitivity suite (`DesignDoc` Stage 0.7).

These are **synthetic and deliberately so.** 0.7 is built *before* 0.4 — §12.7 is
explicit that "a comparator written first and mutation-tested afterwards is a
comparator whose earlier green results nobody can trust retrospectively" — so at
this point there are no stage outputs to use. The subject under test is the
comparator, not the data, and a hand-built frame exercises it exactly as well
while keeping the suite runnable before Stage 1 exists.

Real stage outputs plug into the same comparators as they arrive; the mutant
catalogue does not change.

The shapes follow A.4's artefact classes, and two details are load-bearing:
`match_key` is a **string**, because it is VARCHAR in Splink, and the weights
span the `abs(mw) > 54` region where A.4 says probability parity carries no
information.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from harness.comparators import Table

# A.4 addition 1: above abs(mw) = 54 the probability is exactly 1.0 in float64,
# so the last row exercises the region where a probability gate proves nothing.
_PAIRS: Table = [
    {
        "unique_id_l": "a-001",
        "unique_id_r": "a-002",
        "match_key": "0",
        "gamma_first_name": 2,
        "gamma_surname": 2,
        "match_weight": 12.5,
        "match_probability": 0.99982772881,
    },
    {
        "unique_id_l": "a-001",
        "unique_id_r": "a-003",
        "match_key": "1",
        "gamma_first_name": 1,
        "gamma_surname": 0,
        "match_weight": -3.25,
        "match_probability": 0.09503257476,
    },
    {
        "unique_id_l": "b-010",
        "unique_id_r": "b-011",
        "match_key": "1",
        "gamma_first_name": 2,
        "gamma_surname": 1,
        "match_weight": 0.0,
        "match_probability": 0.5,
    },
    {
        "unique_id_l": "c-100",
        "unique_id_r": "c-101",
        "match_key": "2",
        "gamma_first_name": 2,
        "gamma_surname": 2,
        "match_weight": 60.0,
        "match_probability": 1.0,
    },
]

_CLUSTERS: Table = [
    {"unique_id": "a-001", "component_label": "a-001"},
    {"unique_id": "a-002", "component_label": "a-001"},
    {"unique_id": "a-003", "component_label": "a-001"},
    {"unique_id": "b-010", "component_label": "b-010"},
    {"unique_id": "b-011", "component_label": "b-010"},
    {"unique_id": "c-100", "component_label": "c-100"},
    {"unique_id": "c-101", "component_label": "c-100"},
]

PAIR_KEYS = ("unique_id_l", "unique_id_r")
PAIR_VALUES = ("match_key", "gamma_first_name", "gamma_surname")
CLUSTER_NODE = "unique_id"
CLUSTER_LABEL = "component_label"

# Stage 6's acceptance criterion names these three simultaneously (DR-08).
THRESHOLDS = (0.5, 0.9, 0.99)


def pairs() -> Table:
    """Return a fresh copy of the known-good pair output."""
    return [dict(row) for row in _PAIRS]


def clusters() -> Table:
    """Return a fresh copy of the known-good cluster output."""
    return [dict(row) for row in _CLUSTERS]
