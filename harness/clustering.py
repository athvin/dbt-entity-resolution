"""D4's connected-components query, and an independent oracle for it (Stage 0.5).

D4 calls the `WITH RECURSIVE … USING KEY` formulation the site of **"the most
dangerous error in v1"**, and the error was not a typo — v1 had the recursion
semantics **inverted**, which an independent sweep of 250 random graphs caught as
**214/250 failures**. DR-05 adds that D4a measures this 3.4-18.5x slower than
Splink and that it **OOMs rather than degrading**.

So this stage exists to answer one question before Stage 6 is built on it: does
the query compute connected components, on graph shapes chosen to break it?

**The oracle is union-find in Python**, deliberately implemented from a different
algorithm than the SQL. A second min-label fixpoint written the same way would
reproduce the same misunderstanding and agree with it.

**Label equality, not partition equality.** A.4 row 5: D4 proves the labels are
identical, so partition equality — which passes on any consistent relabelling —
*hides real drift* (M6). The min-label fixpoint is unique, which is what makes
the stronger gate available at all.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    import duckdb

# D4's formulation, as executed, with DR-12's column name.
#
# Every line of the recursive term is load-bearing, and D4 records what happens
# without each one:
#   * `cc as c`            -- the DELTA. Driving from `recurring.` re-derives
#                             rows forever; a v1-shaped query did not terminate.
#   * `recurring.cc as cur`-- the FULL accumulated state, used as a LOOKUP.
#   * `group by`           -- MANDATORY.
#   * `having … < …`       -- MANDATORY. `USING KEY` REPLACES a key's row, so an
#                             unguarded `min()` can RAISE a label and oscillate.
CC_QUERY = """
with recursive
  bidir as (
      select unique_id_l as src, unique_id_r as dst from int_edges
    union all
      select unique_id_r as src, unique_id_l as dst from int_edges
  ),
  cc(unique_id, component_label) using key (unique_id) as (
      select unique_id, unique_id as component_label from stg_input
    union all
      select b.dst as unique_id, min(c.component_label) as component_label
      from cc as c
      join bidir as b          on b.src = c.unique_id
      join recurring.cc as cur on cur.unique_id = b.dst
      group by b.dst, cur.component_label
      having min(c.component_label) < cur.component_label
  )
select unique_id, component_label from cc order by unique_id
"""

# The same query with the monotone guard removed. D4: "the labels oscillate and
# never converge ... hung past 100s on 5,000 nodes and past 90s on 300 nodes."
CC_QUERY_NO_GUARD = CC_QUERY.replace(
    "      having min(c.component_label) < cur.component_label\n", ""
)

# NOTE: v1's inverted-driver shape is NOT reconstructed here. D4 describes it in
# prose -- "propagates the smaller component label across edges via the
# `recurring.` pseudo-schema" -- and its decisive counter-recursion test has two
# substitution points whose full text the document does not give. A
# reconstruction from that description terminated in 0.5s, which says something
# about the reconstruction and nothing about v1. The claim stays where D4 left
# it: measured by someone who had the query.


def union_find(nodes: Iterable[str], edges: Iterable[tuple[str, str]]) -> dict[str, str]:
    """Return `{node: min label of its component}`, computed by union-find.

    Independent of the SQL by construction: disjoint-set forest with path
    compression, then a min-per-root pass. If both sides shared an algorithm they
    would share its mistakes.
    """
    parent: dict[str, str] = {node: node for node in nodes}

    def find(node: str) -> str:
        root = node
        while parent[root] != root:
            root = parent[root]
        while parent[node] != root:  # path compression
            parent[node], node = root, parent[node]
        return root

    for left, right in edges:
        # D4: an edge whose endpoint is absent from the nodes table is dropped,
        # as is an edge with a NULL endpoint. The oracle must agree.
        if left not in parent or right not in parent:
            continue
        root_l, root_r = find(left), find(right)
        if root_l != root_r:
            parent[max(root_l, root_r)] = min(root_l, root_r)

    smallest: dict[str, str] = {}
    for node in parent:
        root = find(node)
        smallest[root] = min(smallest.get(root, node), node)
    return {node: smallest[find(node)] for node in parent}


def load_graph(
    con: duckdb.DuckDBPyConnection,
    nodes: Sequence[str],
    edges: Sequence[tuple[str | None, str | None]],
) -> None:
    """Create `stg_input` and `int_edges` for one graph."""
    con.execute("drop table if exists stg_input")
    con.execute("drop table if exists int_edges")
    con.execute("create table stg_input (unique_id varchar)")
    con.execute("create table int_edges (unique_id_l varchar, unique_id_r varchar)")
    if nodes:
        con.executemany("insert into stg_input values (?)", [(n,) for n in nodes])
    if edges:
        con.executemany("insert into int_edges values (?, ?)", list(edges))


def components(con: duckdb.DuckDBPyConnection) -> dict[str, str]:
    """Run D4's query and return `{unique_id: component_label}`."""
    return dict(con.execute(CC_QUERY).fetchall())
