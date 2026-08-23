"""§3.5's term frequency: the denominator is the NON-NULL count.

Executed against the real fixture, because the failure mode is **silent**. With
`count(*)` every frequency comes out proportionally too small, the ordering is
unchanged, and the values look entirely reasonable — the term-frequency
adjustment is just systematically wrong.

§3.5's own test catches it directly: **`sum(tf) = 1.0` per column.**
"""

from __future__ import annotations

import pathlib
import sys
from typing import Any

import duckdb
import jinja2
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

FIXTURE = ROOT / "fixtures" / "source" / "fake_1000.csv"
TF_COLUMNS = ("first_name", "surname", "city", "email")


def _macros() -> Any:  # noqa: ANN401 -- a Jinja module has no useful static type
    combined = "\n".join(
        (text[text.index("{% macro") :] if "{% macro" in text else text)
        for text in (
            p.read_text(encoding="utf-8")
            for p in sorted((ROOT / "macros" / "sql_gen").glob("*.sql"))
        )
    )
    env = jinja2.Environment(loader=jinja2.DictLoader({"m": combined}), autoescape=False)  # noqa: S701

    def _raise(message: str) -> None:
        raise RuntimeError(message)

    env.globals["exceptions"] = type("E", (), {"raise_compiler_error": staticmethod(_raise)})
    return env.get_template("m").make_module()


def _connection() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    con.execute("create table corpus as select * from read_csv(?)", [str(FIXTURE)])
    return con


@pytest.mark.parametrize("column", TF_COLUMNS)
def test_frequencies_sum_to_exactly_one(column: str) -> None:
    """§3.5's direct test for the denominator bug.

    Both sides of the division cover the same population -- the WHERE clause
    excludes NULLs from the numerator and `count(<col>)` excludes them from the
    denominator -- so the frequencies are a genuine distribution.
    """
    con = _connection()
    try:
        sql = _macros().er_term_frequency_sql(column, "corpus")
        total = con.execute(f"select sum(tf_{column}) from ({sql})").fetchone()
    finally:
        con.close()
    assert total is not None
    assert total[0] == pytest.approx(1.0, abs=1e-12)


@pytest.mark.parametrize("column", TF_COLUMNS)
def test_count_star_as_the_denominator_would_be_visibly_wrong(column: str) -> None:
    """The natural mistake, and how far off it lands on real data.

    `fake_1000` carries 17-21% NULLs per column, so `count(*)` puts `sum(tf)`
    near 0.8 rather than 1.0. Asserted so the test suite documents the size of
    the error rather than merely forbidding it.
    """
    con = _connection()
    try:
        wrong = (
            f'select "{column}", cast(count(*) as float8) / '
            f"(select count(*) from corpus) as tf "
            f'from corpus where "{column}" is not null group by "{column}"'
        )
        total = con.execute(f"select sum(tf) from ({wrong})").fetchone()
    finally:
        con.close()
    assert total is not None
    assert 0.75 < total[0] < 0.85


@pytest.mark.parametrize("column", TF_COLUMNS)
def test_the_macro_matches_splinks_own_sql(column: str) -> None:
    """Splink's generator, `term_frequencies.py:33-48`, value for value."""
    con = _connection()
    try:
        splink_sql = (
            f'select "{column}", cast(count(*) as float8) / '
            f'(select count("{column}") as total from corpus) as tf '
            f'from corpus where "{column}" is not null group by "{column}"'
        )
        theirs = con.execute(f"select * from ({splink_sql}) order by 1").fetchall()
        ours_sql = _macros().er_term_frequency_sql(column, "corpus")
        ours = con.execute(f"select * from ({ours_sql}) order by 1").fetchall()
    finally:
        con.close()
    assert ours == theirs


def test_nulls_are_excluded_from_the_numerator_too() -> None:
    """A NULL is not a term, so it must not appear as one.

    Splink's `WHERE <col> IS NOT NULL` does this; without it a NULL group would
    carry a frequency and the adjustment would treat "missing" as a value.
    """
    con = _connection()
    try:
        sql = _macros().er_term_frequency_sql("city", "corpus")
        rows = con.execute(f"select * from ({sql}) where city is null").fetchall()
    finally:
        con.close()
    assert rows == []


def test_a_missing_column_name_is_a_compile_error() -> None:
    with pytest.raises(RuntimeError, match="ER-054"):
        _macros().er_term_frequency_sql("", "corpus")
