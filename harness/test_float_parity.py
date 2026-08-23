"""G5: the same bits on darwin/arm64 and linux/amd64, asserted every run.

The tests below are identical on both platforms **on purpose**. That is the
whole mechanism: this file runs locally on darwin/arm64 and in CI on
`ubuntu-24.04` (native linux/amd64), and both must reproduce the same committed
reference. Two platforms asserting the same bit patterns continuously is a
stronger claim than either measuring it once.

Without this, A.4's *"exact bit equality is the right default"* is a
same-platform result generalised to a cross-platform gate, and the failure would
surface as a baseline that stops reproducing on a runner — long after the
baseline was frozen, with the tolerance table already built on it.
"""

from __future__ import annotations

import duckdb
import pytest

from harness.float_probe import (
    MEASURED_ON_DUCKDB,
    PROBE_COLUMNS,
    REFERENCE,
    bits,
    platform_tag,
    probe,
)


def test_the_probe_covers_every_column_the_reference_pins() -> None:
    """Deleting a probe must be a failure, not a quietly smaller reference."""
    assert set(REFERENCE) == set(PROBE_COLUMNS)


def test_the_duckdb_version_is_the_one_the_reference_was_measured_on() -> None:
    """A bit pattern is scoped to the build that produced it (§0)."""
    assert duckdb.__version__ == MEASURED_ON_DUCKDB, (
        f"the reference was measured on DuckDB {MEASURED_ON_DUCKDB}, not "
        f"{duckdb.__version__}. Re-measure on BOTH platforms and re-record before "
        f"trusting it -- a bump is exactly when G5 would reopen."
    )


@pytest.mark.parametrize("column", PROBE_COLUMNS)
def test_this_platform_reproduces_the_reference_bits(column: str) -> None:
    """G5's gate. Same assertion on darwin/arm64 and on linux/amd64."""
    measured = probe()
    assert measured[column] == REFERENCE[column], (
        f"{column} on {platform_tag()} is {measured[column]}, reference says "
        f"{REFERENCE[column]}. A.4's 'exact bit equality' default does not hold "
        f"across platforms for this expression -- the tolerance table needs "
        f"amending, and no baseline should be frozen until it is (G5, §22.1)."
    )


def test_the_probe_actually_distinguishes_different_values() -> None:
    """Guard: a `bits()` that returned a constant would make every test above pass."""
    assert bits(1.0) != bits(1.0000000000000002)
    assert bits(0.0) != bits(-0.0)


def test_linear_space_and_log_space_disagree_by_a_measurable_amount() -> None:
    """Why D6 / DR-06 mandates linear space then ONE log2 (A.4 addition 3).

    Multiplying bayes factors then taking a single `log2` is NOT the same
    computation as summing their logs: measured here at **3 ULP** on the probe's
    seven-factor product, which is the same effect A.4 addition 3 bounds at
    max |dmw| = 2.842e-14 across all 2,880 gamma vectors of a production model.

    It is pinned because a later "optimisation" to log space would look tidier,
    change every weight in the last few bits, and break the exact-equality gate
    that every parity claim rests on.
    """
    measured = probe()
    assert measured["via_product"] != measured["via_logs"], (
        "linear-space and log-space products agree bit-for-bit, which contradicts "
        "the measurement D6/DR-06 rests on. Re-derive the rule before relying on it."
    )
    delta_ulp = abs(int(measured["via_product"], 16) - int(measured["via_logs"], 16))
    assert delta_ulp == 3, (
        f"the linear-vs-log gap is {delta_ulp} ULP, not the 3 measured on "
        f"DuckDB {MEASURED_ON_DUCKDB}. The arithmetic changed; re-check D6."
    )
