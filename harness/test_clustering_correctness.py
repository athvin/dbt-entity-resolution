"""Stage 0.5: D4's clustering reproduces a union-find partition, and its traps are real.

Two halves, and the second is the one that keeps the rule from becoming folklore:

1. **Correctness** — D4's query agrees with an independent union-find oracle on
   random, chain and star graphs, plus the degenerate shapes D4 names.
2. **The monotone guard** is shown to *actually* be load-bearing on this DuckDB,
   rather than believed because the document says so.

**What is deliberately NOT re-verified here.** D4's other trap — v1's inverted
driver, "the most dangerous error in v1" — is described in prose rather than as
SQL, and its decisive counter-recursion test has two substitution points whose
full query text the document does not record. A reconstruction from that
description terminated in 0.5s, which is a fact about the reconstruction and not
evidence about v1's query. Asserting on a guess would be worse than leaving the
claim where D4 left it: measured once, by someone who had the query.

**On running queries that do not terminate.** G13 measured that DuckDB 1.5.5
exposes **no statement timeout** — `duckdb_settings()` returns zero rows for
`%timeout%` — so a hanging query cannot be bounded in SQL, and running one
in-process would hang this suite with no way out. The non-terminating forms
therefore run in a **subprocess with a hard kill**. One measured finding shaping
another piece of work.

Runtimes are *recorded*, never asserted: D4a's honest performance statement is
what Stage 6 needs, and a timing threshold on a shared runner is a flake
generator (§21's base rate).
"""

from __future__ import annotations

import random
import subprocess
import sys
import time
from typing import TYPE_CHECKING

import duckdb
import pytest

from harness.clustering import components, load_graph, union_find

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence

# 3.58 pins PYTHONHASHSEED/TZ/LC_ALL; the graph seed is pinned here for the same
# reason -- a fixture nobody can regenerate is not a regression test.
SEED = 20260823

# Small enough to stay fast, large enough that a wrong answer is not luck.
RANDOM_NODES = 200
RANDOM_EDGES = 150

# The correct query completes this shape in ~0.5s (measured), so 10s is ~19x
# its runtime: long enough that a merely slow runner does not read as a hang,
# short enough not to stall CI. D4 measured the unguarded form running past 90s
# on this shape, so the gap is not marginal.
HANG_BUDGET_SECONDS = 10


@pytest.fixture
def con() -> Iterator[duckdb.DuckDBPyConnection]:
    connection = duckdb.connect()
    try:
        yield connection
    finally:
        connection.close()


def _random_graph(seed: int) -> tuple[list[str], list[tuple[str, str]]]:
    rng = random.Random(seed)  # noqa: S311 -- graph shapes, not cryptography
    nodes = [f"n{i:04d}" for i in range(RANDOM_NODES)]
    edges = [(rng.choice(nodes), rng.choice(nodes)) for _ in range(RANDOM_EDGES)]
    return nodes, edges


def _chain(length: int) -> tuple[list[str], list[tuple[str, str]]]:
    """Build a chain -- the shape that maximises iteration count (diameter = length)."""
    nodes = [f"c{i:04d}" for i in range(length)]
    return nodes, [(nodes[i], nodes[i + 1]) for i in range(length - 1)]


def _star(points: int) -> tuple[list[str], list[tuple[str, str]]]:
    """Build a star -- the shape that maximises fan-out from one node."""
    nodes = [f"s{i:04d}" for i in range(points + 1)]
    return nodes, [(nodes[0], nodes[i]) for i in range(1, points + 1)]


def _assert_matches_oracle(
    con: duckdb.DuckDBPyConnection,
    nodes: Sequence[str],
    edges: Sequence[tuple[str, str]],
    label: str,
) -> float:
    load_graph(con, nodes, edges)
    started = time.perf_counter()
    got = components(con)
    elapsed = time.perf_counter() - started
    want = union_find(nodes, edges)
    assert got == want, (
        f"{label}: D4's query disagrees with the union-find oracle. "
        f"{sum(1 for k in want if got.get(k) != want[k])} of {len(want)} labels differ."
    )
    return elapsed


# --- 1. Correctness against an independent oracle ---------------------------


def test_random_graph_matches_union_find(con: duckdb.DuckDBPyConnection) -> None:
    nodes, edges = _random_graph(SEED)
    _assert_matches_oracle(con, nodes, edges, "random")


@pytest.mark.parametrize("seed_offset", range(10))
def test_a_sweep_of_random_graphs_matches(con: duckdb.DuckDBPyConnection, seed_offset: int) -> None:
    """D4's own evidence came from a sweep; a single graph can pass by luck.

    The v1-shaped query failed 214 of 250 random graphs -- meaning it also
    *passed* 36, which is exactly why one graph proves nothing.
    """
    nodes, edges = _random_graph(SEED + seed_offset)
    _assert_matches_oracle(con, nodes, edges, f"random+{seed_offset}")


def test_chain_matches_union_find(con: duckdb.DuckDBPyConnection) -> None:
    nodes, edges = _chain(120)
    _assert_matches_oracle(con, nodes, edges, "chain")


def test_star_matches_union_find(con: duckdb.DuckDBPyConnection) -> None:
    nodes, edges = _star(120)
    _assert_matches_oracle(con, nodes, edges, "star")


def test_singletons_appear_because_the_seed_is_the_node_table(
    con: duckdb.DuckDBPyConnection,
) -> None:
    """Seeding from edges would silently drop every unmatched record."""
    load_graph(con, ["a", "b", "lonely"], [("a", "b")])
    assert components(con) == {"a": "a", "b": "a", "lonely": "lonely"}


def test_an_edge_to_an_absent_node_is_dropped(con: duckdb.DuckDBPyConnection) -> None:
    """D4 (a): an endpoint absent from the nodes table."""
    load_graph(con, ["a", "b"], [("a", "b"), ("b", "ghost")])
    assert components(con) == {"a": "a", "b": "a"}


def test_an_edge_with_a_null_endpoint_is_dropped(con: duckdb.DuckDBPyConnection) -> None:
    """D4 (b): Splink emits these on dangling edges (connected_components.py:89-100)."""
    load_graph(con, ["a", "b"], [("a", "b"), ("a", None)])
    assert components(con) == {"a": "a", "b": "a"}


def test_an_empty_graph_yields_nothing_rather_than_failing(
    con: duckdb.DuckDBPyConnection,
) -> None:
    load_graph(con, [], [])
    assert components(con) == {}


# --- 2. The traps are real on this DuckDB -----------------------------------


def _run_isolated(sql_name: str) -> tuple[bool, float]:
    """Run a trap query in a subprocess. Returns `(completed, seconds)`.

    A subprocess because G13 measured that DuckDB 1.5.5 has no statement
    timeout, so this cannot be bounded from inside SQL.
    """
    program = (
        "import duckdb;"
        "from harness.clustering import load_graph, CC_QUERY, CC_QUERY_NO_GUARD;"
        "con = duckdb.connect();"
        "nodes = [f'n{i:04d}' for i in range(300)];"
        "edges = [(nodes[i], nodes[i+1]) for i in range(299)];"
        "load_graph(con, nodes, edges);"
        f"con.execute({sql_name}).fetchall();"
        "print('COMPLETED')"
    )
    started = time.perf_counter()
    try:
        proc = subprocess.run(  # noqa: S603
            [sys.executable, "-c", program],
            capture_output=True,
            text=True,
            timeout=HANG_BUDGET_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return False, time.perf_counter() - started
    return "COMPLETED" in proc.stdout, time.perf_counter() - started


def test_the_guarded_query_completes_in_isolation() -> None:
    """Control. Without this, the trap test below could pass for the wrong reason."""
    completed, elapsed = _run_isolated("CC_QUERY")
    assert completed, f"the CORRECT query failed to complete in {elapsed:.1f}s"


def test_without_the_monotone_guard_the_query_does_not_converge() -> None:
    """D4's second trap, verified rather than believed.

    `USING KEY` REPLACES the row for a key, so an unguarded `min()` can RAISE a
    node's label when a neighbour in the delta holds a higher one. D4 measured
    the unguarded form hanging past 90s on 300 nodes; this asserts it does not
    finish inside a much smaller budget on the same shape.
    """
    completed, elapsed = _run_isolated("CC_QUERY_NO_GUARD")
    assert not completed, (
        f"the query WITHOUT the monotone guard completed in {elapsed:.1f}s. D4 "
        f"records it hanging past 90s on this shape -- if it now converges, the "
        f"`having` clause may no longer be load-bearing and D4 needs re-deriving."
    )


# --- 3. D4a's honest performance statement ----------------------------------


def test_record_runtimes(con: duckdb.DuckDBPyConnection) -> None:
    """Record, never assert. A timing threshold on a shared runner is a flake."""
    shapes = {
        "random(200n/150e)": _random_graph(SEED),
        "chain(120)": _chain(120),
        "star(120)": _star(120),
    }
    for label, (nodes, edges) in shapes.items():
        elapsed = _assert_matches_oracle(con, nodes, edges, label)
        sys.stdout.write(f"  clustering {label}: {elapsed * 1000:.1f} ms\n")
