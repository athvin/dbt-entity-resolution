"""Stage 3's acceptance criterion, executed (A.4 row 1, §5 Stage 3).

    Exact `(unique_id_l, unique_id_r, match_key)` set equality, with `match_key`
    compared as VARCHAR.

`tests_python/test_blocking_sql.py` already checks the *rendered SQL* against
Splink's captured output. This checks the **pair set that SQL produces**, which
is a different claim: SQL can render identically and still select differently if
the data types underneath it differ, and D.0 finding 82 is exactly that — a
BIGINT `unique_id` against a VARCHAR contract moved 148 of 3,989 pairs while
every SQL-level test stayed green.

**Why the comparison is type-strict and a SQL `=` would not do.** DuckDB casts
`'0' = 0` to true, so a warehouse-side equality test cannot see a `match_key`
type divergence at all. `compare_exact` compares types as well as values,
because in Splink they differ.

**Why this runs warehouse-free rather than reading the built model.**
`Makefile`'s `ci` target orders `pytest harness` *before* `dbt build`, so a
harness test cannot read a built relation. It executes the same generated SQL
over the same fixture instead — which also means a failure here is unambiguous:
it is the SQL or the data, never the materialisation.

The oracle is `blocked_pairs.parquet`, captured from Splink's own
`block_using_rules_sqls` — blocking and nothing else, so a Stage 3 failure
cannot be masked or manufactured by a Stage 4/5 change.
"""

# ruff: noqa: S608 -- every query below is built from repo-controlled constants
# (RULES, the fixture path, the frozen oracle path) and from `er_blocking_sql`'s
# own rendered output. There is no user input anywhere in this module, and the
# whole point of the file is to execute the generator's SQL verbatim: rewriting
# these as parameterised queries would mean NOT testing the thing under test.
from __future__ import annotations

import itertools
import sys
from pathlib import Path
from typing import Any

import duckdb
import jinja2
import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "harness"))

from comparators import compare_exact  # type: ignore[import-not-found]  # noqa: E402

FIXTURE = ROOT / "fixtures" / "source" / "fake_1000.csv"
ORACLE = ROOT / "fixtures" / "baselines" / "fake_1000" / "blocked_pairs.parquet"

# The rules the frozen model carries, as the sidecar publishes them from the
# VALIDATED model JSON. Stated here rather than re-derived so this test fails if
# the frozen model's rules change without the oracle being re-minted.
RULES = [
    'l."first_name" = r."first_name"',
    'l."surname" = r."surname"',
    'l."dob" = r."dob"',
    'l."email" = r."email"',
]

# Measured on the reference corpus. Per-rule counts are the second AC bullet and
# they localise a failure: a single wrong number names the rule that moved.
EXPECTED_PER_RULE = {"0": 1998, "1": 1351, "2": 466, "3": 174}


def _render(rules: list[str], relation: str) -> str:
    """Render `er_blocking_sql` exactly as the model does."""
    text = (ROOT / "macros" / "sql_gen" / "er_blocking_sql.sql").read_text(encoding="utf-8")
    body = text[text.index("{% macro") :]
    env = jinja2.Environment(loader=jinja2.DictLoader({"m": body}), autoescape=False)  # noqa: S701

    def _raise(message: str) -> None:
        raise RuntimeError(message)

    env.globals["exceptions"] = type("E", (), {"raise_compiler_error": staticmethod(_raise)})
    module = env.get_template("m").make_module()
    return str(
        module.er_blocking_sql(  # type: ignore[attr-defined]
            rules, relation, relation, "unique_id"
        )
    )


@pytest.fixture(scope="module")
def con() -> duckdb.DuckDBPyConnection:
    """Build a corpus typed the way §2.0 contracts it: `unique_id` VARCHAR."""
    connection = duckdb.connect()
    connection.execute(
        f"""create table corpus as
            select cast(unique_id as varchar) as unique_id,
                   first_name, surname, dob, city, email, cluster
            from read_csv_auto('{FIXTURE}')"""
    )
    return connection


def _rows(records: list[tuple[Any, ...]]) -> list[dict[str, Any]]:
    return [
        {"match_key": mk, "join_key_l": left, "join_key_r": right} for mk, left, right in records
    ]


def test_the_pair_set_equals_splinks_exactly(con: duckdb.DuckDBPyConnection) -> None:
    """The acceptance criterion itself, values AND types."""
    produced = con.execute(
        f"select match_key, join_key_l, join_key_r from ({_render(RULES, 'corpus')})"
    ).fetchall()
    oracle = (
        duckdb.connect()
        .execute(f"select match_key, join_key_l, join_key_r from '{ORACLE}'")
        .fetchall()
    )

    differences = compare_exact(
        _rows(produced),
        _rows(oracle),
        key_columns=("match_key", "join_key_l", "join_key_r"),
        value_columns=(),
        artefact="stage3_blocked_pairs",
    )
    assert differences == [], f"{len(differences)} difference(s): {differences[:5]}"


def test_match_key_and_join_keys_are_text_on_both_sides(con: duckdb.DuckDBPyConnection) -> None:
    """A.4 requires `match_key` compared as VARCHAR, and DuckDB hides that.

    `'0' = 0` is true in DuckDB, so a warehouse-side equality test is blind to a
    type divergence here. This asserts the types directly, on both sides.
    """
    produced = con.execute(
        f"select match_key, join_key_l, join_key_r from ({_render(RULES, 'corpus')}) limit 1"
    ).fetchall()[0]
    oracle = (
        duckdb.connect()
        .execute(f"select match_key, join_key_l, join_key_r from '{ORACLE}' limit 1")
        .fetchall()[0]
    )
    assert [type(v).__name__ for v in produced] == ["str", "str", "str"]
    assert [type(v).__name__ for v in oracle] == ["str", "str", "str"]


def test_per_rule_counts_match(con: duckdb.DuckDBPyConnection) -> None:
    """The second AC bullet. A single wrong number names the rule that moved."""
    counts = dict(
        con.execute(
            f"select match_key, count(*) from ({_render(RULES, 'corpus')}) group by 1"
        ).fetchall()
    )
    assert counts == EXPECTED_PER_RULE


def test_stripping_the_coalesce_is_caught(con: duckdb.DuckDBPyConnection) -> None:
    """The 23.2% failure, pinned.

    The exclusion clause is `AND NOT (coalesce(<prev>, false) OR ...)`. Without
    the `coalesce`, a NULL comparison makes the whole `NOT (...)` NULL and the
    row is dropped — silently, with the right output shape. Measured: per-rule
    1998/1351/466/174 becomes 1998/853/152/60, losing **926 of 3,989 pairs**.

    Asserted as a *measured magnitude* rather than "it differs", because the
    number is what makes the risk legible.
    """
    stripped = _render(RULES, "corpus").replace("coalesce((", "((").replace("),false)", "))")
    counts = dict(
        con.execute(f"select match_key, count(*) from ({stripped}) group by 1").fetchall()
    )
    total_stripped = sum(counts.values())
    assert total_stripped < sum(EXPECTED_PER_RULE.values())
    assert sum(EXPECTED_PER_RULE.values()) - total_stripped == 926


def test_empty_string_keys_still_block(con: duckdb.DuckDBPyConnection) -> None:
    """D2. Splink does NOT special-case the empty string, and neither may we.

    The natural defensive filter — `where key is not null and key <> ''` — is
    the divergence: on this fixture it deletes 3 of the 4 pairs that an empty
    `city` legitimately generates. An empty string is a value; only NULL is not.
    """
    con.execute("""create or replace table empties as
        select * from (values
            ('e1', 'Ann', 'Shah', '1990-01-01', '', 'a@x.invalid', 'c1'),
            ('e2', 'Bo',  'Lee',  '1991-01-01', '', 'b@x.invalid', 'c1'),
            ('e3', 'Cy',  'Ng',   '1992-01-01', '', 'c@x.invalid', 'c2'),
            ('e4', 'Di',  'Fox',  '1993-01-01', '', 'd@x.invalid', 'c2')
        ) as t(unique_id, first_name, surname, dob, city, email, cluster)""")
    rule = ['l."city" = r."city"']

    kept = con.execute(f"select count(*) from ({_render(rule, 'empties')})").fetchall()[0][0]
    assert kept == 6, "four records sharing an empty city are six unordered pairs"

    filtered = _render(rule, "empties").replace(
        'on\n    (l."city" = r."city")',
        'on\n    (l."city" = r."city" and l."city" is not null and l."city" <> \'\')',
    )
    assert con.execute(f"select count(*) from ({filtered})").fetchall()[0][0] == 0, (
        "the defensive filter deletes every empty-string pair -- which is the "
        "divergence D2 exists to name"
    )


def test_blocking_recall_from_the_packages_own_pairs_clears_its_floor(
    con: duckdb.DuckDBPyConnection,
) -> None:
    """§5's fourth AC bullet, measured against what THIS package produces.

    `harness/test_quality_floors.py` also checks this floor, but from Splink's
    frozen `predictions.parquet` — the oracle. That was the only option while
    the package produced no pairs of its own. It does now, so this measures the
    package, and the two agreeing is a result rather than an assumption.

    The band is TWO-SIDED (M12 rec 2). Below is a regression in the code. Above
    means the fixture or the frozen model moved, which is equally a finding and
    would otherwise read as an improvement.

    Recall lost here is unrecoverable: no later stage can score a pair blocking
    never generated.
    """
    band = yaml.safe_load((ROOT / "dbt_project.yml").read_text(encoding="utf-8"))["vars"][
        "er_blocking_recall_floor"
    ]["fake_1000"]

    truth: set[tuple[str, str]] = set()
    groups: dict[str, list[str]] = {}
    for uid, cluster in con.execute(
        f"select cast(unique_id as varchar), cast(cluster as varchar) "
        f"from read_csv_auto('{FIXTURE}')"
    ).fetchall():
        groups.setdefault(cluster, []).append(uid)
    for members in groups.values():
        truth.update(itertools.combinations(sorted(members), 2))

    generated = {
        tuple(sorted((left, right)))
        for left, right in con.execute(
            f"select join_key_l, join_key_r from ({_render(RULES, 'corpus')})"
        ).fetchall()
    }
    recall = len(generated & truth) / len(truth)

    assert len(truth) == 2031, "the recall denominator moved; every figure below is incomparable"
    assert band["min"] <= recall <= band["max"], (
        f"blocking recall {recall:.4f} is outside the committed "
        f"[{band['min']}, {band['max']}]. Below: a regression, and unrecoverable "
        f"downstream. Above: the fixture or the frozen model moved, not the code."
    )
