"""§3.2 and §3.3, executed against Splink's own SQL rather than compared as text.

Both sections correct a v1 error, and both corrections are the kind that produce
**systematic** divergence rather than floating-point noise — a wrong `LEAST` for
`GREATEST` flips the adjustment on every pair where the two sides differ.

The TF numerator is the piece the A.2 sidecar exists for: it is **not in the
model JSON**, and Splink resolves it by CNF analysis of sibling levels.
"""

from __future__ import annotations

import json
import pathlib
import random
import struct
import sys
from typing import Any

import duckdb
import jinja2
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import er_sidecar  # noqa: E402

SEED = 20260823

# Splink's own bf_tf_adj projection for `city`, captured from its predict SQL.
SPLINK_TF = """CASE WHEN  gamma_city = -1 then cast(1 as float8) WHEN  gamma_city = 1 then
    (CASE WHEN coalesce("tf_city_l", "tf_city_r") is not null
    THEN POW(cast(0.0551475711801453 as float8) /
    (CASE WHEN coalesce("tf_city_l", "tf_city_r") >= coalesce("tf_city_r", "tf_city_l")
        THEN coalesce("tf_city_l", "tf_city_r") ELSE coalesce("tf_city_r", "tf_city_l") END),
        cast(1.0 as float8))
    ELSE cast(1 as float8) END) WHEN  gamma_city = 0 then cast(1 as float8) END as bf_tf_adj_city"""

# Splink's own gamma CASE for `city` (`Comparison._case_statement`).
SPLINK_GAMMA = (
    'CASE WHEN "city_l" IS NULL OR "city_r" IS NULL THEN -1 '
    'WHEN "city_l" = "city_r" THEN 1 ELSE 0 END as gamma_city'
)


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


def _city_levels() -> list[dict[str, object]]:
    """Raw JSON level fields merged with the sidecar's resolution."""
    path = ROOT / "fixtures" / "model_jsons" / "fake_1000_v1.json"
    raw_model = json.loads(path.read_text(encoding="utf-8"))
    resolved = er_sidecar.build(path.read_text(encoding="utf-8"))["resolved"]
    raw = next(c for c in raw_model["comparisons"] if c["output_column_name"] == "city")[
        "comparison_levels"
    ]
    res = next(c for c in resolved["comparisons"] if c["output_column_name"] == "city")["levels"]
    return [{**a, **b} for a, b in zip(raw, res, strict=True)]


def _rows() -> list[tuple[int, float | None, float | None]]:
    rng = random.Random(SEED)  # noqa: S311
    rows: list[tuple[int, float | None, float | None]] = [
        (
            rng.choice([-1, 0, 1]),
            rng.choice([None, rng.uniform(1e-6, 0.5)]),
            rng.choice([None, rng.uniform(1e-6, 0.5)]),
        )
        for _ in range(1500)
    ]
    # Every NULL combination, and both orientations of a differing pair.
    rows += [
        (1, None, None),
        (1, 0.01, None),
        (1, None, 0.03),
        (1, 0.01, 0.03),
        (1, 0.03, 0.01),
        (-1, 0.01, 0.02),
        (0, 0.01, 0.02),
    ]
    return rows


def _bits(value: object) -> str:
    return struct.pack(">d", value).hex() if isinstance(value, float) else repr(value)


def _evaluate(expression: str) -> list[object]:
    con = duckdb.connect()
    try:
        con.execute('create table cv (gamma_city int, "tf_city_l" double, "tf_city_r" double)')
        con.executemany("insert into cv values (?,?,?)", _rows())
        return [r[0] for r in con.execute(f"select {expression} from cv").fetchall()]
    finally:
        con.close()


def test_the_tf_adjustment_is_bit_identical_to_splinks() -> None:
    """1,507 rows including every NULL combination."""
    tf = _macros().er_tf_adjustment_sql("city", _city_levels())
    assert [_bits(v) for v in _evaluate(tf)] == [_bits(v) for v in _evaluate(SPLINK_TF)]


def test_the_gamma_case_matches_splinks_token_for_token() -> None:
    """§3.3: exact JSON list order, no reordering, no null-hoisting."""
    gamma = _macros().er_gamma_case_sql("city", _city_levels())
    assert " ".join(gamma.split()).lower() == " ".join(SPLINK_GAMMA.split()).lower()


def test_the_numerator_is_the_exact_match_levels_u_not_the_levels_own() -> None:
    """§3.2 correction 1, and the reason the A.2 sidecar exists.

    The value is **not in the model JSON**. Splink resolves it by CNF analysis
    of sibling levels, and a naive string match on `"<col>_l" = "<col>_r"`
    diverges on conditions like `a_l = a_r AND b_l = b_r`.
    """
    levels = _city_levels()
    adjusted = next(lv for lv in levels if lv.get("tf_u_exact_match") is not None)
    exact = next(lv for lv in levels if lv["is_exact_match"])
    assert adjusted["tf_u_exact_match"] == exact["u_probability"]
    assert str(exact["u_probability"]) in _macros().er_tf_adjustment_sql("city", levels)


def test_the_divisor_is_greatest_not_least() -> None:
    """§3.2 correction 2: a systematic divergence, not a floating-point one.

    Splink deliberately uses the MORE COMMON term's frequency, giving the
    SMALLER boost. `LEAST` flips the direction on every pair where l and r
    differ.
    """
    tf = _macros().er_tf_adjustment_sql("city", _city_levels())
    values = _evaluate(tf)
    rows = _rows()
    low_high = values[rows.index((1, 0.01, 0.03))]
    high_low = values[rows.index((1, 0.03, 0.01))]
    # Orientation-invariant (M1's counter-check) AND using the larger tf.
    assert _bits(low_high) == _bits(high_low)
    assert isinstance(low_high, float)
    assert low_high == pytest.approx(0.0551475711801453 / 0.03, rel=1e-12)


def test_both_null_falls_back_to_one_but_one_null_does_not() -> None:
    """§3.2's NULL handling, which `COALESCE(tf, 0)` would turn into +inf."""
    tf = _macros().er_tf_adjustment_sql("city", _city_levels())
    values = _evaluate(tf)
    rows = _rows()
    assert values[rows.index((1, None, None))] == 1.0
    assert values[rows.index((1, 0.01, None))] != 1.0
    assert values[rows.index((1, None, 0.03))] != 1.0


def test_non_adjusted_levels_emit_the_constant_one() -> None:
    """The null level and the ELSE level, detected by sentinel not by gamma == 0."""
    tf = _macros().er_tf_adjustment_sql("city", _city_levels())
    values = _evaluate(tf)
    rows = _rows()
    assert values[rows.index((-1, 0.01, 0.02))] == 1.0
    assert values[rows.index((0, 0.01, 0.02))] == 1.0


def test_a_comparison_with_no_levels_is_a_compile_error() -> None:
    with pytest.raises(RuntimeError, match="ER-052"):
        _macros().er_gamma_case_sql("city", [])


def test_a_comparison_with_no_else_level_is_a_compile_error() -> None:
    """An unmatched pair would yield NULL gamma, which propagates silently."""
    levels = [lv for lv in _city_levels() if str(lv["sql_condition"]).strip().upper() != "ELSE"]
    with pytest.raises(RuntimeError, match="ER-053"):
        _macros().er_gamma_case_sql("city", levels)
