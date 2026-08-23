"""§3.1's scoring arithmetic, executed and compared bit-for-bit against Splink's form.

This is the arithmetic every parity claim rests on, so the test **runs the SQL**
rather than comparing text. A.4 makes `match_weight` an exact-bit-equality gate;
anything less here would be asserting a weaker property than the gate it feeds.

**[v1 ERROR]** v1 specified *"sum of per-comparison log2 Bayes factors"*. Splink
multiplies in **linear space**, clamps, and applies `log2` exactly once. §3.1
measured the naive log-space sum diverging by **9.97** on underflow and
**56.47** on overflow, in match-weight units, against A.4's `1e-9` tolerance.

The comparison form is Splink's own: the clamped product written out three
times. Ours uses a **lateral column alias** (B.8 / DR-23), measured in Stage 0.8
to evaluate once per row. The two must agree bit-for-bit, and PC-3 already
showed the constructs do.
"""

from __future__ import annotations

import random
import struct
from pathlib import Path

import duckdb
import jinja2
import pytest

ROOT = Path(__file__).resolve().parents[1]
MACRO = ROOT / "macros" / "sql_gen" / "er_match_weight_sql.sql"

PRIOR = "0.002497283380020013"
BF_COLUMNS = ("bf_a", "bf_tf_adj_a", "bf_b", "bf_c")
SEED = 20260823


def _render(prior: str = PRIOR, columns: tuple[str, ...] = BF_COLUMNS) -> str:
    source = MACRO.read_text(encoding="utf-8")
    body = source[source.index("{% macro er_match_weight_sql") :]
    env = jinja2.Environment(loader=jinja2.DictLoader({"m": body}), autoescape=False)  # noqa: S701

    def _raise(message: str) -> None:
        raise RuntimeError(message)

    env.globals["exceptions"] = type("E", (), {"raise_compiler_error": staticmethod(_raise)})
    module = env.get_template("m").make_module()
    return str(module.er_match_weight_sql(prior, list(columns)))  # type: ignore[attr-defined]


def _splink_form() -> str:
    """Splink's own projection: the clamped product written out three times."""
    product = f"cast({PRIOR} as float8)" + "".join(f" * {c}" for c in BF_COLUMNS)
    clamped = f"least(greatest({product}, 1e-300), 1e300)"
    infinity = " OR ".join(f"{c} = cast('infinity' as float8)" for c in BF_COLUMNS)
    return (
        f"log2({clamped}) as match_weight, "
        f"CASE WHEN {infinity} THEN 1.0 "
        f"ELSE ({clamped})/(1+({clamped})) END as match_probability"
    )


def _rows() -> list[tuple[float, float, float, float]]:
    rng = random.Random(SEED)  # noqa: S311 - fixtures, not cryptography
    rows = [
        (
            rng.choice([1e-30, 0.5, 1.0, 3.7, 1e30, 88.87]),
            rng.uniform(0.5, 2),
            rng.uniform(1e-8, 1e8),
            rng.uniform(0.1, 10),
        )
        for _ in range(2000)
    ]
    # §3.1's two measured cases, the clamp boundaries, and an infinity.
    rows += [
        (1e-25,) * 4,
        (1e40,) * 4,
        (1e-300, 1.0, 1.0, 1.0),
        (1e300, 1.0, 1.0, 1.0),
        (float("inf"), 1.0, 1.0, 1.0),
    ]
    return rows


@pytest.fixture(scope="module")
def scored() -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """`(ours, splink's)` as bit patterns, over the same rows."""
    con = duckdb.connect()
    try:
        con.execute("create table cv (bf_a double, bf_tf_adj_a double, bf_b double, bf_c double)")
        con.executemany("insert into cv values (?,?,?,?)", _rows())
        ours = con.execute(f"select {_render()} from cv").fetchall()
        theirs = con.execute(f"select {_splink_form()} from cv").fetchall()
    finally:
        con.close()

    def bits(value: object) -> str:
        return struct.pack(">d", value).hex() if isinstance(value, float) else repr(value)

    # Ours emits bf_clamped first; Splink's emits only the two outputs.
    return (
        [(bits(r[1]), bits(r[2])) for r in ours],
        [(bits(r[0]), bits(r[1])) for r in theirs],
    )


def test_the_scores_are_bit_identical_to_splinks_form(
    scored: tuple[list[tuple[str, str]], list[tuple[str, str]]],
) -> None:
    """A.4 gates `match_weight` on exact bit equality, so the test must too."""
    ours, theirs = scored
    assert len(ours) == 2005
    assert ours == theirs


def test_the_product_actually_contains_every_bayes_factor() -> None:
    """The bug this file caught, pinned so it cannot come back.

    Jinja's `{% set %}` inside a `{% for %}` does **not** escape the loop, so an
    accumulator built that way silently drops every factor and leaves the prior
    alone. The SQL stays valid, runs, and scores every pair identically -- the
    "looks like a working model and is not one" failure, arriving through the
    macro rather than through its arguments.
    """
    rendered = _render()
    for column in BF_COLUMNS:
        assert f"* {column}" in rendered, (
            f"{column} is missing from the product. Every pair would score on the "
            f"prior alone, and the SQL would still run."
        )


def test_the_clamp_is_applied_before_the_log() -> None:
    """§3.1: a naive log-space sum diverges by 9.97 and 56.47 in weight units."""
    rendered = _render()
    assert "least(greatest(" in rendered
    assert "1e-300" in rendered
    assert "1e300" in rendered


def test_log2_is_applied_exactly_once() -> None:
    """DR-06 / D6: multiply in linear space, then ONE log2.

    G5 measured `product(bf)` and `exp(sum(ln(bf)))` differing by 3 ULP even
    with no clamping in play, so this is not a stylistic preference.
    """
    assert _render().count("log2(") == 1


def test_the_prior_is_a_factor_never_a_summand() -> None:
    """§3.1: "converted to odds and multiplied as the first factor"."""
    rendered = _render()
    assert f"cast({PRIOR} as float8) *" in rendered
    assert f"log2(cast({PRIOR}" not in rendered


def test_the_clamped_product_is_named_once_and_reused() -> None:
    """B.8 / DR-23: a lateral column alias, measured to evaluate once per row.

    Splink's own projection repeats the clamped product three times. D11 rec 4
    requires computing it once; Stage 0.8 measured the alias doing so, and PC-3
    showed the forms are bit-identical -- so this costs no parity.
    """
    rendered = _render()
    assert rendered.count("least(greatest(") == 1
    assert "as bf_clamped" in rendered
    assert rendered.count("bf_clamped") >= 3


def test_the_infinity_guard_covers_every_bayes_factor() -> None:
    """One unguarded column and an infinite BF yields NULL instead of 1.0."""
    rendered = _render()
    for column in BF_COLUMNS:
        assert f"{column} = cast('infinity' as float8)" in rendered


def test_no_bayes_factor_columns_is_a_compile_error() -> None:
    with pytest.raises(RuntimeError, match="ER-051"):
        _render(columns=())


def test_an_underflowing_product_lands_on_section_3_1s_clamp_floor() -> None:
    """§3.1's -996.5784284662087 is `log2(1e-300)` -- the floor, not a coincidence.

    §3.1 reached it with prior 0.001 and twelve BFs of `1e-25`; the inputs here
    are four factors chosen to underflow the same clamp. **What is asserted is
    the property, not the transcribed number**: any product below `1e-300`
    scores at the floor, which is exactly what a naive log-space sum fails to do
    -- §3.1 measured it continuing to `-1006.54`, a 9.97 divergence against
    A.4's `1e-9` tolerance.
    """
    con = duckdb.connect()
    try:
        con.execute("create table cv (bf_a double, bf_tf_adj_a double, bf_b double, bf_c double)")
        # 0.001 * (1e-80)^4 = 1e-323, well below the 1e-300 clamp.
        con.execute("insert into cv values (1e-80, 1e-80, 1e-80, 1e-80)")
        weight = con.execute(f"select {_render(prior='0.001')} from cv").fetchone()
    finally:
        con.close()
    assert weight is not None
    assert weight[1] == pytest.approx(-996.5784284662087, abs=1e-9)


def test_an_overflowing_product_lands_on_the_symmetric_ceiling() -> None:
    """The other half: `log2(1e300)` = +996.5784284662087.

    §3.1 measured a log-space sum continuing to `1053.05` here -- a **56.47**
    divergence, and the reason the clamp is described as "not cosmetic".
    """
    con = duckdb.connect()
    try:
        con.execute("create table cv (bf_a double, bf_tf_adj_a double, bf_b double, bf_c double)")
        con.execute("insert into cv values (1e80, 1e80, 1e80, 1e80)")
        weight = con.execute(f"select {_render(prior='0.001')} from cv").fetchone()
    finally:
        con.close()
    assert weight is not None
    assert weight[1] == pytest.approx(996.5784284662087, abs=1e-9)
