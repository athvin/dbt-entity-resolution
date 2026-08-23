"""Pin the DuckDB expression semantics B.8's resolution depends on (Stage 0.8).

B.8 asked one question, and D11 rec 4 is why it mattered: Stage 5 must compute
`least(greatest(<product>, 1e-300), 1e300)` **once** and derive both
`match_weight` and `match_probability` from it, because *"dbt's ephemeral wrapper
hard-codes `{name} as (…)` with no `MATERIALIZED` (compilation.py:616-625), so
single-evaluation must be structural, not a hint."*

The three available constructs were a subquery (banned by ST05), a CTE (banned by
§7.3), or a separate pair-grain model (~100 B/pair to hold one float). The
document recommended relaxing ST05 — *"(a), after testing (c)"* — where (c) is a
**lateral column alias**, which would need no relaxation at all.

**Measured on DuckDB 1.5.5: (c) evaluates once per row.** So B.8 resolves to (c)
and ST05 keeps its default scope.

**This file exists because that answer is an optimizer behaviour, not a
guarantee.** §0's rule applies to engine behaviour exactly as it applies to
markers: it is scoped to the pin that produced it. A DuckDB bump that stopped
folding the alias would silently reintroduce the cost D11 rejected, on the
project's hottest relation, with nothing failing. These tests are what fail
instead.
"""

from __future__ import annotations

import duckdb
import pytest

# The pin these measurements were taken on. A bump must re-run them, not assume.
MEASURED_ON = "1.5.5"

ROWS = 1000


@pytest.fixture
def counting_connection() -> tuple[duckdb.DuckDBPyConnection, dict[str, int]]:
    """Return a connection whose UDF counts its own invocations.

    A UDF counter answers "how many times was this evaluated" **directly**.
    Timing comparisons only support an inference, and would make the test flaky
    on a shared runner.
    """
    con = duckdb.connect()
    calls = {"n": 0}

    def clamp_counted(value: float) -> float:
        calls["n"] += 1
        return min(max(value, 1e-300), 1e300)

    con.create_function("clamp_counted", clamp_counted, ["DOUBLE"], "DOUBLE")
    # S608 does not apply: ROWS is a module-level int constant, not input.
    con.execute(
        "create table pairs as select range::double + 1 as bf_product from range(?)", [ROWS]
    )
    return con, calls


def _evaluations(
    fixture: tuple[duckdb.DuckDBPyConnection, dict[str, int]], sql: str
) -> tuple[int, list[tuple[float, ...]]]:
    con, calls = fixture
    calls["n"] = 0
    rows = con.execute(sql).fetchall()
    return calls["n"], rows


# Stage 5's actual shape: clamp once, derive weight and probability from it.
_LATERAL_ALIAS = """
    select
        clamp_counted(bf_product)       as bf_clamped,
        log2(bf_clamped)                as match_weight,
        bf_clamped / (1.0 + bf_clamped) as match_probability
    from pairs
"""

_SUBQUERY = """
    select
        bf_clamped,
        log2(bf_clamped)                as match_weight,
        bf_clamped / (1.0 + bf_clamped) as match_probability
    from (select clamp_counted(bf_product) as bf_clamped from pairs) s
"""

_REPEATED = """
    select
        clamp_counted(bf_product)                                     as bf_clamped,
        log2(clamp_counted(bf_product))                               as match_weight,
        clamp_counted(bf_product) / (1.0 + clamp_counted(bf_product)) as match_probability
    from pairs
"""


def test_the_measured_duckdb_version_is_the_pinned_one() -> None:
    """A result measured on another build is not this result."""
    assert duckdb.__version__ == MEASURED_ON, (
        f"These semantics were measured on DuckDB {MEASURED_ON}, not "
        f"{duckdb.__version__}. Re-run Stage 0.8's spike and re-record B.8 before "
        f"trusting the lateral-alias form (section 0: a result is scoped to its pin)."
    )


def test_a_lateral_column_alias_evaluates_once_per_row(
    counting_connection: tuple[duckdb.DuckDBPyConnection, dict[str, int]],
) -> None:
    """B.8 option (c). This is the assertion the resolution rests on."""
    evaluations, _ = _evaluations(counting_connection, _LATERAL_ALIAS)
    assert evaluations == ROWS, (
        f"A lateral column alias evaluated the expression {evaluations} times over "
        f"{ROWS} rows. B.8 resolves to option (c) ONLY while this is {ROWS} -- "
        f"D11 rec 4 requires single evaluation. If this has changed, B.8 reopens "
        f"and option (a)'s ST05 relaxation is back on the table."
    )


def test_the_counter_can_distinguish_two_evaluations_from_one(
    counting_connection: tuple[duckdb.DuckDBPyConnection, dict[str, int]],
) -> None:
    """Guard against a counter that reads `ROWS` for the wrong reason.

    Without this, a UDF that DuckDB decided to evaluate once per *distinct value*
    -- or a counter that silently stopped incrementing -- would make every other
    test in this file pass vacuously.
    """
    evaluations, _ = _evaluations(
        counting_connection,
        "select clamp_counted(bf_product) as a, clamp_counted(bf_product + 0) as b from pairs",
    )
    assert evaluations == 2 * ROWS, (
        f"two DIFFERENT expressions evaluated {evaluations} times, expected "
        f"{2 * ROWS}. The counter is not measuring what these tests assume."
    )


def test_all_three_constructs_are_bit_identical(
    counting_connection: tuple[duckdb.DuckDBPyConnection, dict[str, int]],
) -> None:
    """Float parity does not distinguish them; only cost did, and cost is settled."""
    _, lateral = _evaluations(counting_connection, _LATERAL_ALIAS)
    _, subquery = _evaluations(counting_connection, _SUBQUERY)
    _, repeated = _evaluations(counting_connection, _REPEATED)
    assert lateral == subquery == repeated


def test_duckdb_eliminates_a_repeated_identical_subexpression(
    counting_connection: tuple[duckdb.DuckDBPyConnection, dict[str, int]],
) -> None:
    """The unexpected half of the spike, recorded so the reasoning stays honest.

    B.8 states *"repeating the expression is not an option — D11 rejects it on
    parity grounds"*, and the cost argument behind that is **unfounded on DuckDB
    1.5.5**: three identical calls cost one evaluation per row, because the
    planner splits the projection and computes the common subexpression once.

    This does not change the resolution — (c) is still preferred, because it
    states the intent in the SQL rather than depending on an optimiser pass — but
    an argument that is wrong should be recorded as wrong rather than left
    standing because its conclusion survived.
    """
    evaluations, _ = _evaluations(counting_connection, _REPEATED)
    assert evaluations == ROWS, (
        f"the thrice-repeated expression evaluated {evaluations} times over "
        f"{ROWS} rows; common-subexpression elimination is no longer happening. "
        f"B.8's cost argument against repetition becomes valid again."
    )
