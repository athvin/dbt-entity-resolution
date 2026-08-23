"""Stage 0.6: bytes-per-pair is measured, and the cap is derived from it.

The measurements are **recorded and range-checked**, never pinned to an exact
byte count. A hard equality on a compression ratio would fail on any DuckDB
patch release for no reason anyone cares about, and §21 puts the cost of a
flaky gate high. What is asserted is the part the cap depends on: memory costs
several times disk, entropy costs more than tidiness, and wide costs more than
narrow.
"""

from __future__ import annotations

import sys

import duckdb
import pytest

from harness.capacity import (
    BYTES_PER_PAIR_NARROW,
    MEASURED_ON_DUCKDB,
    PAIR_BUDGET_FRACTION,
    max_pairs,
    measure,
    summarise,
)

# Enough rows that per-row overhead is amortised, few enough to stay quick.
#
# 50,000 is NOT enough, measured: B/pair falls from 280.3 at 25k rows to 182.1
# at 50k, 148.7 at 100k and 140.8 at 800k, converging around 140-150. Below
# ~100k the fixed per-block overhead dominates and the figure is misleadingly
# HIGH -- so a constant validated at 50k would look conservative and a cap
# derived from it would be far too small.
#
# A B/pair number therefore needs three qualifiers, and the inherited 946
# carried none of them: units (memory, not disk), entropy, and ROW COUNT.
ROWS = 50_000

# Where the per-row cost has converged. Used wherever the assertion is about
# the production-scale figure rather than about the shape of the curve.
CONVERGED_ROWS = 200_000

GIB = 1024**3


def test_the_measured_duckdb_version_is_the_pinned_one() -> None:
    assert duckdb.__version__ == MEASURED_ON_DUCKDB, (
        f"B/pair was measured on DuckDB {MEASURED_ON_DUCKDB}, not {duckdb.__version__}. "
        f"Storage layout and compression are version-specific -- re-measure and "
        f"re-derive er_max_pairs before trusting the cap."
    )


def test_memory_costs_several_times_disk() -> None:
    """The finding that makes the units matter.

    Measuring the obvious way -- the size of the database file -- reports ~6x
    less than the relation costs in memory, and memory is what OOMs.
    """
    narrow = measure(ROWS, wide=False, high_entropy=True)
    assert narrow.memory_bytes_per_pair > 2 * narrow.disk_bytes_per_pair, (
        f"memory {narrow.memory_bytes_per_pair:.1f} B/pair is not meaningfully "
        f"above disk {narrow.disk_bytes_per_pair:.1f} B/pair. If these have "
        f"converged, section 5's published figures need re-interpreting."
    )


def test_entropy_costs_as_much_as_shape_does() -> None:
    """Why the conservative cell is the one that sets `er_bytes_per_pair`.

    DuckDB's dictionary and RLE compression works on `i % 97` and does not work
    on real names and emails, so a capacity figure measured on tidy synthetic
    data is optimistic about production -- the wrong direction for a guardrail
    whose failure mode is an OOM.
    """
    tidy = measure(ROWS, wide=False, high_entropy=False)
    real = measure(ROWS, wide=False, high_entropy=True)
    assert real.memory_bytes_per_pair > tidy.memory_bytes_per_pair


def test_wide_costs_more_than_narrow() -> None:
    """The premise of the inherited 42,000,000, checked rather than assumed."""
    narrow = measure(ROWS, wide=False, high_entropy=True)
    wide = measure(ROWS, wide=True, high_entropy=True)
    assert wide.memory_bytes_per_pair > 2 * narrow.memory_bytes_per_pair


def test_the_conservative_constant_is_not_optimistic() -> None:
    """`er_bytes_per_pair` must not be below what the narrow shape really costs.

    This is the assertion that keeps the cap honest. If a DuckDB change makes
    pairs more expensive, the constant becomes optimistic and the cap admits a
    build that will not fit -- so it fails here instead.
    """
    narrow = measure(CONVERGED_ROWS, wide=False, high_entropy=True)
    assert narrow.memory_bytes_per_pair * 0.9 <= BYTES_PER_PAIR_NARROW, (
        f"er_bytes_per_pair = {BYTES_PER_PAIR_NARROW} is optimistic against a "
        f"measured {narrow.memory_bytes_per_pair:.1f} B/pair. Re-derive the cap: "
        f"an optimistic per-pair cost admits builds that do not fit, and nothing "
        f"interrupts them partway (G13, D4a)."
    )


def test_max_pairs_is_derived_from_the_budget_not_a_constant() -> None:
    assert max_pairs(12 * GIB) == int(12 * GIB * PAIR_BUDGET_FRACTION / BYTES_PER_PAIR_NARROW)
    # Halving the budget halves the cap. A constant would not move.
    assert max_pairs(6 * GIB) == max_pairs(12 * GIB) // 2


def test_the_shipped_cap_matches_its_stated_derivation() -> None:
    """`dbt_project.yml`'s number must be reproducible from the stated inputs.

    A cap nobody can re-derive is the same defect as a `[VERIFIED]` marker
    nobody can re-earn: it looks like a decision and is actually a leftover.
    """
    assert max_pairs(12 * GIB) == 42_384_545


@pytest.mark.parametrize("budget", [0, -1])
def test_a_nonsensical_budget_is_rejected(budget: int) -> None:
    with pytest.raises(ValueError, match="positive"):
        max_pairs(budget)


def test_bytes_per_pair_falls_as_rows_amortise_fixed_overhead() -> None:
    """The third qualifier: a B/pair figure is meaningless without a row count.

    Measured 280.3 B/pair at 25k rows and 140.8 at 800k. A guardrail calibrated
    on a small sample is calibrated on per-block overhead, not on the data.
    """
    small = measure(25_000, wide=False, high_entropy=True)
    large = measure(CONVERGED_ROWS, wide=False, high_entropy=True)
    assert small.memory_bytes_per_pair > large.memory_bytes_per_pair * 1.3, (
        f"per-pair cost did not fall with scale ({small.memory_bytes_per_pair:.1f} "
        f"-> {large.memory_bytes_per_pair:.1f}). If overhead no longer amortises, "
        f"the row count at which er_bytes_per_pair was measured needs revisiting."
    )


def test_record_the_capacity_table() -> None:
    """Publish the measurement, which is what Stage 0.6 asks for."""
    measurements = [
        measure(ROWS, wide=wide, high_entropy=entropy)
        for entropy in (False, True)
        for wide in (False, True)
    ]
    sys.stdout.write("\n" + summarise(measurements) + "\n")
    assert len(measurements) == 4
