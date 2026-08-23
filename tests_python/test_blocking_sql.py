"""D2's blocking SQL, rendered by our macro and compared against Splink's own.

Stage 1's acceptance criterion is *"rendered SQL for the fixture model matches
reviewed snapshots"*. The snapshot at `fixtures/snapshots/blocking_fake_1000_v1.sql`
is **Splink 4.0.16's actual output**, captured from
`splink.internals.blocking.block_using_rules_sqls` — not a transcription of the
document. So this is a parity test, not a formatting test.

**v1 described this wrong**, which is why it is worth pinning: v1 said "rule 2
`AND NOT` (rule 1)", one exclusion per preceding rule. The real generator emits
a **single** `AND NOT ( ... OR ... )` over all preceding rules.

Every test below names a way the SQL could be *silently* wrong — producing
plausible pairs, in the right shape, in the wrong quantity.
"""

from __future__ import annotations

import re
from pathlib import Path

import jinja2
import pytest

ROOT = Path(__file__).resolve().parents[1]
MACRO = ROOT / "macros" / "sql_gen" / "er_blocking_sql.sql"
SNAPSHOT = ROOT / "fixtures" / "snapshots" / "blocking_fake_1000_v1.sql"

# The frozen model's four rules, in order.
FROZEN_RULES = (
    'l."first_name" = r."first_name"',
    'l."surname" = r."surname"',
    'l."dob" = r."dob"',
    'l."email" = r."email"',
)


def _render(rules: tuple[str, ...] | list[str], unique_id: str = "unique_id") -> str:
    """Render the macro outside dbt, so the test needs no warehouse."""
    source = MACRO.read_text(encoding="utf-8")
    body = source[source.index("{% macro er_blocking_sql") :]
    env = jinja2.Environment(loader=jinja2.DictLoader({"m": body}), autoescape=False)  # noqa: S701

    def _raise(message: str) -> None:
        raise RuntimeError(message)

    env.globals["exceptions"] = type("E", (), {"raise_compiler_error": staticmethod(_raise)})
    module = env.get_template("m").make_module()
    return str(
        module.er_blocking_sql(  # type: ignore[attr-defined]
            list(rules), "__splink__df_concat", "__splink__df_concat", unique_id
        )
    )


def _normalise(sql: str) -> str:
    """Collapse whitespace and case. Layout is not the property under test."""
    return re.sub(r"\s+", " ", sql).strip().lower()


def test_the_rendered_sql_matches_splinks_own_output() -> None:
    """The parity result: token-for-token equivalent to the oracle's SQL."""
    assert _normalise(_render(FROZEN_RULES)) == _normalise(SNAPSHOT.read_text(encoding="utf-8"))


def test_the_exclusion_is_one_combined_not_or_not_a_chain() -> None:
    """D2's correction of v1, asserted directly.

    v1's shape would emit `AND NOT (rule0) AND NOT (rule1)` for the third rule.
    The real one emits `AND NOT (coalesce(rule0) OR coalesce(rule1))`.
    """
    rendered = _normalise(_render(FROZEN_RULES))
    assert "and not (coalesce" in rendered
    # Four rules produce three exclusion clauses, never six.
    assert rendered.count("and not (") == 3


def test_every_excluded_rule_is_coalesce_wrapped() -> None:
    """Without `coalesce(..., false)` a NULL in a preceding rule DELETES pairs.

    Not "fails to exclude" -- deletes. Splink's own comment says so, and the
    symptom is a rule quietly finding fewer pairs than it should.
    """
    rendered = _normalise(_render(FROZEN_RULES))
    # 3 exclusion clauses over 1 + 2 + 3 = 6 preceding rules.
    assert rendered.count("coalesce((") == 6
    assert rendered.count(",false)") == 6


def test_match_key_is_a_string_literal_not_an_integer() -> None:
    """§12.7 carries a mutant for exactly this.

    `match_key` is VARCHAR in Splink (`blocking.py:203-206`). Emitting it as an
    integer would make a dtype-coercing comparator normalise a real divergence
    away, and both sides would agree while disagreeing.
    """
    rendered = _render(FROZEN_RULES)
    for index in range(len(FROZEN_RULES)):
        assert f"'{index}' as match_key" in rendered
        assert f" {index} as match_key" not in rendered


def test_pairs_are_combined_with_union_all_never_union() -> None:
    """`UNION` would dedupe, which is a different pair set and a silent one."""
    rendered = _normalise(_render(FROZEN_RULES))
    assert rendered.count("union all") == len(FROZEN_RULES) - 1
    assert not re.search(r"\bunion(?!\s+all)\b", rendered)
    assert "distinct" not in rendered


def test_the_id_comparison_is_strict() -> None:
    """D3's ordering, and G9's cost.

    `<` not `<=`: two records sharing a `unique_id` never pair with each other,
    so the match most likely to matter is silently suppressed. §2.0 makes
    `unique_id` UNIQUE and fixtures/degenerate/shared_unique_id.csv pins it.
    """
    rendered = _normalise(_render(FROZEN_RULES))
    assert rendered.count('where l."unique_id" < r."unique_id"') == len(FROZEN_RULES)
    assert "<=" not in rendered


def test_the_first_rule_has_no_exclusion() -> None:
    """Nothing precedes it, so an exclusion clause would be both wrong and empty."""
    first_block = _normalise(_render(FROZEN_RULES)).split("union all")[0]
    assert "and not" not in first_block


def test_a_single_rule_renders_without_any_exclusion() -> None:
    rendered = _normalise(_render((FROZEN_RULES[0],)))
    assert "union all" not in rendered
    assert "and not" not in rendered


def test_no_blocking_rules_is_a_compile_error() -> None:
    """A blocking stage that generates nothing is §12.7's zero-to-zero comparison."""
    with pytest.raises(RuntimeError, match="ER-050"):
        _render(())


def test_the_key_columns_are_join_key_not_unique_id() -> None:
    """Matching Splink: they become unique_id_l/r only after Stage 4 joins back."""
    rendered = _normalise(_render(FROZEN_RULES))
    assert "as join_key_l" in rendered
    assert "as unique_id_l" not in rendered


def test_the_unique_id_column_is_configurable() -> None:
    """§2.0 lets a consumer name it; the SQL must follow rather than assume."""
    rendered = _render(FROZEN_RULES, unique_id="record_id")
    assert 'l."record_id" < r."record_id"' in rendered
