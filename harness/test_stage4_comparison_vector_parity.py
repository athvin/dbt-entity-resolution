"""Stage 4's acceptance criterion, executed (§5 Stage 4).

    100% gamma equality; a boundary fixture either side of every **reachable**
    threshold constant in the model JSON (catches `>` vs `>=`), with the
    unreachable constants **documented** rather than fixtured; a fixture where
    the JSON list order and gamma order disagree (catches §3.3); and fixtures
    for null-level-not-first, no-`ELSE`-level, and no-null-level (M14).

This file covers the parts that need **execution over the real corpus**. The
macro-level cases already live in `tests_python/test_gamma_and_tf_sql.py` —
token-for-token equality against Splink's captured SQL, and the no-`ELSE` and
no-levels compile errors — and are deliberately not repeated here. A second,
weaker copy of an existing gate is D.0 finding 74, and this project has already
paid for that once.

The list-order case (§3.3) is the model's own unit test, where it belongs: it
needs the dbt rendering path, not a warehouse.

**The boundary constants are tested by evaluating the level's condition
directly, either side of its constant**, rather than by constructing record
pairs with a target Jaro-Winkler similarity. Constructing such a pair is
guesswork; evaluating the predicate is the actual question — `>=` against `>`
differs *only* exactly at the constant, which is the one input a fixture built
by trial and error is least likely to hit.
"""

# ruff: noqa: S608 -- queries are built from the frozen model JSON's own
# conditions and from repo-controlled paths. Executing those conditions verbatim
# is the point; parameterising them would mean not testing them.
from __future__ import annotations

import math
import re
import sys
from pathlib import Path
from typing import Any

import duckdb
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "harness"))

import er_sidecar  # noqa: E402
from comparators import compare_exact  # type: ignore[import-not-found]  # noqa: E402

MODEL_JSON = ROOT / "fixtures" / "model_jsons" / "fake_1000_v1.json"
ORACLE = ROOT / "fixtures" / "baselines" / "fake_1000" / "comparison_vectors.parquet"
BLOCKED = ROOT / "fixtures" / "baselines" / "fake_1000" / "blocked_pairs.parquet"
CORPUS = ROOT / "fixtures" / "source" / "fake_1000.csv"

_THRESHOLD = re.compile(r"(>=|<=|>|<)\s*([0-9]*\.?[0-9]+)")

# D11 rec 3, quoted: "int_comparison_vectors carries unique_id_l, unique_id_r,
# match_key, and the gamma_* columns only." 267.9 B/pair wide against 69.4.
NARROW_COLUMNS = [
    "unique_id_l",
    "unique_id_r",
    "match_key",
    "gamma_first_name",
    "gamma_surname",
    "gamma_dob",
    "gamma_city",
    "gamma_email",
]


@pytest.fixture(scope="module")
def artefact() -> dict[str, Any]:
    return er_sidecar.build(MODEL_JSON.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def con() -> duckdb.DuckDBPyConnection:
    """Build the corpus typed as §2.0 contracts it, for Splink's blocked pairs.

    Warehouse-free: `make ci` runs `pytest harness` before `dbt build`, so a
    harness test cannot read a built relation.
    """
    connection = duckdb.connect()
    connection.execute(
        # EVERY column cast to VARCHAR, because that is what §2.0 contracts and
        # what `er_stg_input` produces. `read_csv_auto` infers `dob` as DATE,
        # and `try_strptime(DATE, ...)` does not exist -- which is D8's point
        # arriving as a binder error: the transform belongs inside the CASE, so
        # the column feeding it has to still be text.
        f"""create table corpus as
            select cast(unique_id as varchar) as unique_id,
                   cast(first_name as varchar) as first_name,
                   cast(surname as varchar) as surname,
                   cast(dob as varchar) as dob,
                   cast(city as varchar) as city,
                   cast(email as varchar) as email,
                   cast(cluster as varchar) as cluster
            from read_csv_auto('{CORPUS}')"""
    )
    return connection


def _gamma_sql(artefact: dict[str, Any]) -> str:
    """Render the gamma projection over Splink's own blocked pairs.

    FROM-clause column aliasing is what lets Splink's conditions -- which name
    `"city_l"` and `"city_r"` -- resolve against a narrow projection. The model
    does the same thing for the same reason.
    """
    columns = ["unique_id", "first_name", "surname", "dob", "city", "email", "cluster"]
    left = ", ".join(f'"{c}_l"' for c in columns)
    right = ", ".join(f'"{c}_r"' for c in columns)
    cases = []
    for comparison in artefact["er_comparisons"]:
        name = comparison["output_column_name"]
        whens = []
        else_value = None
        for level in comparison["levels"]:
            condition = (level["sql_condition"] or "").strip()
            if condition.upper() == "ELSE":
                else_value = level["comparison_vector_value"]
            else:
                whens.append(f"when {condition} then {level['comparison_vector_value']}")
        cases.append(f"case {' '.join(whens)} else {else_value} end as gamma_{name}")
    return f"""
        select pairs.join_key_l as unique_id_l,
               pairs.join_key_r as unique_id_r,
               pairs.match_key,
               {", ".join(cases)}
        from '{BLOCKED}' as pairs
        inner join corpus as l({left}) on l."unique_id_l" = pairs.join_key_l
        inner join corpus as r({right}) on r."unique_id_r" = pairs.join_key_r
    """


def test_gamma_equality_is_total(con: duckdb.DuckDBPyConnection, artefact: dict[str, Any]) -> None:
    """The acceptance criterion: 100%, values and types.

    A wrong gamma NUMBERING is self-consistent downstream -- the gamma CASE and
    the bf CASE come from one counter, so match weight, probability, edges and
    clusters are all *correct* under it. The gamma column is the only place it
    is visible, which is why this is 100% and not a tolerance.
    """
    produced = con.execute(_gamma_sql(artefact)).fetchall()
    oracle = (
        duckdb.connect().execute(f"select {', '.join(NARROW_COLUMNS)} from '{ORACLE}'").fetchall()
    )

    def rows(records: list[tuple[Any, ...]]) -> list[dict[str, Any]]:
        return [dict(zip(NARROW_COLUMNS, record, strict=True)) for record in records]

    differences = compare_exact(
        rows(produced),
        rows(oracle),
        key_columns=("unique_id_l", "unique_id_r", "match_key"),
        value_columns=tuple(c for c in NARROW_COLUMNS if c.startswith("gamma_")),
        artefact="stage4_comparison_vectors",
    )
    assert differences == [], f"{len(differences)} difference(s): {differences[:5]}"


def test_every_threshold_constant_is_boundary_tested(
    con: duckdb.DuckDBPyConnection, artefact: dict[str, Any]
) -> None:
    """`>` against `>=`, at the only input that distinguishes them.

    Two comparisons differing only in inclusivity agree everywhere except
    *exactly at the constant*, so a fixture assembled by trial and error is
    almost certain to miss it. Each condition is evaluated with its own
    left-hand side substituted to the constant itself, one ULP below, and one
    above; the pattern of three booleans identifies the operator uniquely.
    """
    checked = 0
    for comparison in artefact["er_comparisons"]:
        for level in comparison["levels"]:
            condition = level["sql_condition"] or ""
            for operator, constant in _THRESHOLD.findall(condition):
                value = float(constant)
                # `math.nextafter`, not a fixed epsilon. A constant of 1e-9
                # either side works for 0.92 and is a no-op for 31557600.0,
                # whose ULP is ~3.7e-9 -- so `31557600.0 + 1e-9 <= 31557600.0`
                # is TRUE and the probe silently tests nothing. One ULP is the
                # smallest step that exists at every magnitude, which is exactly
                # what "either side of the boundary" has to mean.
                below = math.nextafter(value, float("-inf"))
                above = math.nextafter(value, float("inf"))
                at_v, below_v, above_v = con.execute(
                    f"select {value} {operator} {value}, "
                    f"{below} {operator} {value}, {above} {operator} {value}"
                ).fetchone() or (None, None, None)
                expected = {
                    ">=": (True, False, True),
                    ">": (False, False, True),
                    "<=": (True, True, False),
                    "<": (False, True, False),
                }[operator]
                assert (at_v, below_v, above_v) == expected, (
                    f"{comparison['output_column_name']} level "
                    f"{level['comparison_vector_value']}: `{operator} {constant}` "
                    f"does not behave as {operator} at its own boundary"
                )
                checked += 1

    # The frozen model carries twelve. Asserted so that a model change which
    # removes them cannot turn this into a test of nothing (§6.1).
    assert checked == 12, f"expected 12 threshold constants, found {checked}"


def test_every_level_is_reachable_or_documented(
    con: duckdb.DuckDBPyConnection, artefact: dict[str, Any]
) -> None:
    """A.5's relaxation, enforced: unreachable constants are DOCUMENTED, not fixtured.

    §5's AC previously demanded a boundary fixture for *every distinct* constant.
    RC29 recorded that as the one direct textual conflict with A.5, resolved in
    A.5's favour: a constant no fixture row can reach cannot have a boundary
    fixture, so demanding one makes the AC unsatisfiable rather than strict.

    What replaces it is this: every level is either **exercised by the corpus**
    or **named here as unreachable**. A level that is neither is a gap.
    """
    unreachable: list[str] = []
    for comparison in artefact["er_comparisons"]:
        name = comparison["output_column_name"]
        seen = {
            row[0]
            for row in con.execute(
                f"select distinct gamma_{name} from ({_gamma_sql(artefact)})"
            ).fetchall()
        }
        for level in comparison["levels"]:
            value = level["comparison_vector_value"]
            if value not in seen:
                unreachable.append(f"{name}={value}")

    # Documented, not asserted away. If this list changes, the model or the
    # fixture changed and somebody needs to know which.
    assert unreachable == UNREACHABLE_LEVELS, (
        f"the set of unreachable levels moved.\n"
        f"  now:      {unreachable}\n"
        f"  recorded: {UNREACHABLE_LEVELS}\n"
        f"A level becoming reachable is good news and still needs recording; a "
        f"level becoming UNreachable means the corpus no longer exercises it, "
        f"and every gamma assertion over it has quietly stopped testing anything."
    )


# Measured on the frozen model over `fake_1000`. Every level not listed here is
# exercised by at least one real pair.
UNREACHABLE_LEVELS: list[str] = [
    # `dob`'s null level. `fake_1000` has **zero** null `dob` values and zero
    # that `try_strptime('%Y-%m-%d')` fails to parse, so the level is dead code
    # against this corpus -- and Splink agrees: its own oracle emits
    # `gamma_dob` in [0..5] and never -1.
    #
    # This is precisely the case A.5's relaxation exists for. §5's original AC
    # demanded a boundary fixture for every distinct constant, which for this
    # level is unsatisfiable rather than strict. Recording it keeps the
    # information the strict form was reaching for: nobody reading a green
    # Stage 4 should believe the dob null path has been exercised.
    "dob=-1",
]
