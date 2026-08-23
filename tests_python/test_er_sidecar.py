"""§1.5's five rules, each shown to reject (Stage 1, DR-17).

§1.5 names five negative tests as Stage 1 acceptance criteria. The positive case
matters too, and it is the one that found the real defects: **the allow-list as
published rejected Splink's own comparison library.**

Every test here asserts the *specific* error code, not merely that validation
failed. A validator that rejects for the wrong reason is still broken, and it is
harder to notice later — the same argument 3.38 makes for `verify_gates.py` and
§12.7 makes for comparators.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import er_sidecar  # noqa: E402

FROZEN = ROOT / "fixtures" / "model_jsons" / "fake_1000_v1.json"


def _model(condition: str) -> str:
    """Build a minimal model JSON carrying one comparison level."""
    return json.dumps(
        {
            "comparisons": [
                {
                    "output_column_name": "first_name",
                    "comparison_levels": [
                        {"sql_condition": condition, "label_for_charts": "test"},
                    ],
                }
            ]
        }
    )


def _codes(raw: str, bounds: dict[str, int] | None = None) -> list[str]:
    with pytest.raises(er_sidecar.ValidationError) as caught:
        er_sidecar.validate(raw, bounds)
    return [finding.split(":")[0] for finding in caught.value.findings]


# --- The positive case, which is where the real findings came from -----------


def test_the_frozen_model_json_validates() -> None:
    """A stock Splink model must pass, or the trust boundary is unusable.

    This failed on first run and produced two findings about D6's allow-list:
    it was written in DuckDB's vocabulary while §1.5 matches sqlglot's parsed
    tree, and it omitted `abs`, which a stock DateOfBirthComparison emits.
    """
    artefact = er_sidecar.validate(FROZEN.read_text(encoding="utf-8"))
    assert artefact["comparisons"] == 5
    assert artefact["levels_validated"] == 28
    assert len(artefact["er_model_sha"]) == 64


def test_splinks_else_sentinel_is_not_parsed_as_sql() -> None:
    """Splink writes the final level's condition as the literal `ELSE`.

    Five of the frozen model's levels are exactly this. Parsing it as SQL fails
    with "No expression was parsed from 'ELSE'", which reads like a malformed
    model rather than a validator that does not know the format.
    """
    assert er_sidecar.validate(_model("ELSE"))["levels_validated"] == 1


def test_boolean_operators_are_not_treated_as_function_calls() -> None:
    """`exp.Or` is a `Func` subclass in sqlglot and reports `sql_names() == ["OR"]`.

    Without excluding `exp.Connector`, an ordinary `a = b OR c IS NULL` is
    rejected as calling an unlisted function named `or`.
    """
    assert er_sidecar.validate(_model('"a_l" = "a_r" OR "a_l" IS NULL'))


@pytest.mark.parametrize(
    ("canonical", "duckdb_spelling"),
    [("time_to_unix", "epoch"), ("jarowinkler_similarity", "jaro_winkler_similarity")],
)
def test_sqlglot_canonical_names_map_back_to_the_d6_spelling(
    canonical: str, duckdb_spelling: str
) -> None:
    """D6's list is in DuckDB's vocabulary; the tree is in sqlglot's.

    Both of these are functions D6 explicitly allows, and both were rejected
    before the mapping existed.
    """
    assert er_sidecar.SQLGLOT_CANONICAL_NAMES[canonical] == duckdb_spelling
    assert duckdb_spelling in er_sidecar.ALLOWED_FUNCTIONS


# --- Rule 1: the closed allow-list ------------------------------------------


def test_an_unlisted_function_is_rejected_by_name() -> None:
    """§1.5: "naming the function", not "invalid condition"."""
    with pytest.raises(er_sidecar.ValidationError) as caught:
        er_sidecar.validate(_model('md5("a_l") = md5("a_r")'))
    assert any("ER-037" in f and "md5" in f for f in caught.value.findings)


# --- Rule 2: non-determinism, listed or not ---------------------------------


@pytest.mark.parametrize(
    "condition",
    [
        "try_strptime(\"dob_l\", '%Y-%m-%d') < current_date",
        '"a_l" = "a_r" AND random() > 0.5',
        "now() > try_strptime(\"dob_l\", '%Y-%m-%d')",
    ],
)
def test_non_deterministic_functions_are_rejected(condition: str) -> None:
    """The rule that keeps §6.3's determinism claim true rather than aspirational.

    §1.5's benign case: an analyst adds an age-band level containing
    `current_date` -- the natural way to write one -- gamma becomes a function
    of the wall clock, and every parity gate silently stops being true while CI
    merely looks flaky.
    """
    assert "ER-036" in _codes(_model(condition))


def test_non_determinism_beats_the_allow_list() -> None:
    """Rejected "whether or not they appear above".

    Belt and braces: even if someone added `now` to the allow-list, rule 2 still
    rejects it -- the two checks are ordered so non-determinism wins.
    """
    assert "ER-036" in _codes(_model("now() IS NOT NULL"))


# --- Rule 3: structural rejection -------------------------------------------


def test_a_statement_terminator_is_rejected_before_parsing() -> None:
    """The classic bypass: sqlglot parses `a = b; drop table t` as TWO statements.

    Validating only the first would allow-list the harmless half and pass the
    rest through verbatim into compiled SQL.
    """
    assert "ER-033" in _codes(_model('"a_l" = "a_r"; drop table users'))


@pytest.mark.parametrize(
    "condition",
    [
        '"a_l" IN (SELECT x FROM secrets)',
        '"a_l" = (SELECT max(x) FROM t)',
    ],
)
def test_subqueries_are_rejected(condition: str) -> None:
    assert "ER-035" in _codes(_model(condition))


def test_unparseable_sql_is_rejected_as_such() -> None:
    assert "ER-034" in _codes(_model("this is not sql ((("))


# --- Rule 4: bounds ----------------------------------------------------------


def test_too_many_comparisons_is_a_named_error() -> None:
    """Bound the comparison count: enormous becomes a diagnosis, not a symptom."""
    raw = json.dumps(
        {
            "comparisons": [
                {
                    "output_column_name": f"c{i}",
                    "comparison_levels": [{"sql_condition": "ELSE"}],
                }
                for i in range(60)
            ]
        }
    )
    assert "ER-030" in _codes(raw, {"er_max_comparisons": 50})


def test_too_many_levels_is_a_named_error() -> None:
    raw = json.dumps(
        {
            "comparisons": [
                {
                    "output_column_name": "wide",
                    "comparison_levels": [{"sql_condition": "ELSE"} for _ in range(40)],
                }
            ]
        }
    )
    assert "ER-031" in _codes(raw, {"er_max_levels_per_comparison": 30})


def test_an_oversized_json_is_a_named_error() -> None:
    assert "ER-032" in _codes(_model("ELSE"), {"er_max_model_json_bytes": 10})


# --- Rule 5: the sha is of the VALIDATED artefact ----------------------------


def test_the_sha_is_stable_across_formatting() -> None:
    """Canonical serialisation, so whitespace in the environment cannot move it.

    D1 delivers the JSON through an environment variable, where re-indentation
    is routine and must not invalidate every downstream baseline.
    """
    compact = json.dumps(json.loads(_model("ELSE")), separators=(",", ":"))
    spaced = json.dumps(json.loads(_model("ELSE")), indent=4)
    assert (
        er_sidecar.validate(compact)["er_model_sha"] == er_sidecar.validate(spaced)["er_model_sha"]
    )


def test_the_sha_changes_when_the_model_changes() -> None:
    """Otherwise the sha is decoration and rule 5 protects nothing."""
    assert (
        er_sidecar.validate(_model("ELSE"))["er_model_sha"]
        != er_sidecar.validate(_model('"a_l" = "a_r"'))["er_model_sha"]
    )


def test_an_invalid_model_produces_no_sha_at_all() -> None:
    """Rule 5 is what makes rules 1-4 unskippable.

    An unvalidated JSON has no sha, and a model with no sha does not build --
    so there is no path that reaches a build having skipped validation.
    """
    with pytest.raises(er_sidecar.ValidationError) as caught:
        er_sidecar.validate(_model("current_date IS NOT NULL"))
    assert not hasattr(caught.value, "er_model_sha")


# --- Anti-vacuity -------------------------------------------------------------


def test_a_model_with_no_conditions_is_rejected() -> None:
    """A validator that inspects nothing passes everything.

    The same empty-subject failure §6.1 diagnoses, at the trust boundary.
    """
    assert "ER-039" in _codes(json.dumps({"comparisons": []}))


def test_every_finding_is_reported_not_just_the_first() -> None:
    """Three bad levels should report three, not require three build attempts."""
    raw = json.dumps(
        {
            "comparisons": [
                {
                    "output_column_name": "c",
                    "comparison_levels": [
                        {"sql_condition": "current_date IS NOT NULL"},
                        {"sql_condition": 'md5("a_l") = md5("a_r")'},
                        {"sql_condition": '"a_l" IN (SELECT x FROM t)'},
                    ],
                }
            ]
        }
    )
    codes = _codes(raw)
    assert {"ER-036", "ER-037", "ER-035"} <= set(codes)


def test_malformed_json_is_rejected() -> None:
    assert "ER-038" in _codes("{not json")


def test_the_allow_list_is_a_frozenset_so_it_cannot_be_mutated() -> None:
    """Enforce "normative and closed" with the type, not with convention."""
    assert isinstance(er_sidecar.ALLOWED_FUNCTIONS, frozenset)
    assert isinstance(er_sidecar.NON_DETERMINISTIC, frozenset)


def test_no_function_is_both_allowed_and_non_deterministic() -> None:
    """The two lists must not disagree, or precedence decides correctness by accident."""
    overlap = er_sidecar.ALLOWED_FUNCTIONS & er_sidecar.NON_DETERMINISTIC
    assert not overlap, f"{sorted(overlap)} appear in both lists"


# ---------------------------------------------------------------------------
# A.2 C2 -- TF exact-match resolution, which string-matching gets wrong in BOTH
# directions. These four cases are the ones A.2 measured; they pin Splink's
# behaviour so an internal moving under us is a test failure, not a parity bug.
# ---------------------------------------------------------------------------


def _is_exact(condition: str) -> bool:
    from splink.internals.comparison import Comparison  # noqa: PLC0415

    built = Comparison(
        [{"sql_condition": condition, "label_for_charts": "x"}, {"sql_condition": "ELSE"}],
        sqlglot_dialect="duckdb",
        output_column_name="name",
    )
    return bool(built.comparison_levels[0]._is_exact_match)  # noqa: SLF001


@pytest.mark.parametrize(
    ("condition", "expected"),
    [
        # Reversed operand order still counts -- a string match on "l = r" misses it.
        ('"name_r" = "name_l"', True),
        # A negated inequality normalises to equality under CNF.
        ('NOT ("name_l" <> "name_r")', True),
        # An extra conjunct disqualifies, however harmless it looks.
        ('"name_l" = "name_r" AND 1=1', False),
        # A function application disqualifies.
        ("lower(name_l) = lower(name_r)", False),
    ],
)
def test_a2_c2_exact_match_resolution(condition: str, expected: bool) -> None:
    """A.2 C2's measured cases, pinned.

    Two of these a naive string match would call False and two it would call
    True -- wrong in both directions, which is why §A.2 requires the sidecar to
    resolve this rather than approximate it.
    """
    assert _is_exact(condition) is expected


def test_the_resolution_is_splinks_own_not_a_reimplementation() -> None:
    """Resolving with our own CNF code would be the Jinja mistake, one language along.

    Asserted structurally: the resolver must reach Splink's `Comparison`, so a
    future "simplification" that reimplements the analysis fails here.
    """
    source = (ROOT / "scripts" / "er_sidecar.py").read_text(encoding="utf-8")
    assert "from splink.internals.comparison import Comparison" in source


def test_resolution_records_null_levels_rather_than_failing() -> None:
    """Splink RAISES for m/u on a null level; that absence is a valid state."""
    artefact = er_sidecar.build(FROZEN.read_text(encoding="utf-8"))
    first = artefact["resolved"]["comparisons"][0]["levels"][0]
    assert first["is_null_level"] is True
    assert first["comparison_vector_value"] == -1
    assert first["m_probability"] is None


def test_tf_u_is_resolved_only_where_a_tf_adjustment_exists() -> None:
    """`city` carries term_frequency_adjustments; `dob` does not."""
    resolved = er_sidecar.build(FROZEN.read_text(encoding="utf-8"))["resolved"]
    by_name = {c["output_column_name"]: c for c in resolved["comparisons"]}
    city_exact = next(lv for lv in by_name["city"]["levels"] if lv["is_exact_match"])
    dob_exact = next(lv for lv in by_name["dob"]["levels"] if lv["is_exact_match"])
    assert city_exact["tf_u_exact_match"] == city_exact["u_probability"]
    assert dob_exact["tf_u_exact_match"] is None


def test_c3_the_json_key_is_not_evidence_of_a_source_dataset() -> None:
    """A.2 C3's measured trap.

    `source_dataset_column_name` is present in the JSON even for `dedupe_only`,
    while the runtime `source_dataset_input_column` is None. Reading the key as
    a boolean would report a source dataset that does not exist.
    """
    resolved = er_sidecar.build(FROZEN.read_text(encoding="utf-8"))["resolved"]
    assert resolved["er_backend_link_type"] == "dedupe_only"
    assert resolved["er_has_source_dataset"] is False


def test_c4_left_table_is_null_because_it_is_not_in_the_json() -> None:
    """`min(templated_name)` over input-table aliases -- a runtime fact.

    Null rather than guessed: Open Question 3 governs refusing the
    configuration, and inventing a value here would pre-empt that decision.
    """
    resolved = er_sidecar.build(FROZEN.read_text(encoding="utf-8"))["resolved"]
    assert resolved["er_left_table"] is None


def test_the_committed_sidecar_regenerates_byte_identically() -> None:
    """Guard the generated artefact with A.2's byte-equality regeneration test.

    A generated file that has drifted from its generator is worse than no
    generated file, because every downstream consumer trusts it.
    """
    committed = ROOT / "fixtures" / "sidecar" / "fake_1000_v1.sidecar.json"
    artefact = er_sidecar.build(FROZEN.read_text(encoding="utf-8"))
    assert committed.read_text(encoding="utf-8") == (
        json.dumps(artefact, indent=2, sort_keys=True) + "\n"
    )


def test_nothing_is_resolved_before_it_is_validated() -> None:
    """Order matters: resolving first would import an unvalidated JSON into Splink.

    That is the trust boundary §1.5 exists to hold, so `build` must raise on a
    hostile model rather than resolving it.
    """
    with pytest.raises(er_sidecar.ValidationError):
        er_sidecar.build(_model("random() > 0.5"))


# ---------------------------------------------------------------------------
# The Stage 1 lints: M1 (asymmetry), M2 (name collision), M13 (m/u).
# ---------------------------------------------------------------------------


def _levels(*levels: dict[str, object]) -> str:
    return json.dumps(
        {"comparisons": [{"output_column_name": "name", "comparison_levels": list(levels)}]}
    )


def test_m1_detects_a_columns_reversed_level() -> None:
    """M1's measured example: ColumnsReversedLevel('forename','surname').

    Renders `"forename_l" = "surname_r"`, and `symmetrical=False` is the DEFAULT.
    On one pair the Splink orientation gives false and the flipped orientation
    true -- same pair, opposite gamma -- which is what kills S2's "match the set,
    not the orientation" escape.
    """
    artefact = er_sidecar.build(
        _levels({"sql_condition": '"forename_l" = "surname_r"'}, {"sql_condition": "ELSE"})
    )
    assert any("ER-044" in f and "ASYMMETRIC" in f for f in artefact["asymmetric_levels"])


def test_m1_detects_a_one_sided_literal_level() -> None:
    """`LiteralMatchLevel(side_of_comparison='left')` renders `"city_l" = 'London'`."""
    artefact = er_sidecar.build(
        _levels({"sql_condition": "\"city_l\" = 'london'"}, {"sql_condition": "ELSE"})
    )
    assert artefact["asymmetric_levels"]


def test_m1_does_not_fire_on_a_symmetric_level() -> None:
    """Precision matters: an ordinary equality must not be reported."""
    artefact = er_sidecar.build(
        _levels({"sql_condition": '"name_l" = "name_r"'}, {"sql_condition": "ELSE"})
    )
    assert artefact["asymmetric_levels"] == []


def test_m1_is_silent_on_the_frozen_dedupe_only_model() -> None:
    """M1 is "dormant on dedupe-only configs with symmetric levels" -- confirmed."""
    assert er_sidecar.build(FROZEN.read_text(encoding="utf-8"))["asymmetric_levels"] == []


def test_m2_rejects_names_that_collide_after_normalisation() -> None:
    """`first name` and `first_name` both emit `gamma_first_name`."""
    raw = json.dumps(
        {
            "comparisons": [
                {
                    "output_column_name": "first name",
                    "comparison_levels": [{"sql_condition": "ELSE"}],
                },
                {
                    "output_column_name": "first_name",
                    "comparison_levels": [{"sql_condition": "ELSE"}],
                },
            ]
        }
    )
    assert "ER-040" in _codes(raw)


def test_m13_a_present_zero_is_a_hard_error() -> None:
    """A zero bayes factor makes the pair unscoreable and log2(0) is undefined."""
    raw = _levels({"sql_condition": '"a_l" = "a_r"', "m_probability": 0}, {"sql_condition": "ELSE"})
    assert "ER-042" in _codes(raw)


def test_m13_an_absent_m_probability_is_valid_input() -> None:
    """The distinction that IS the finding.

    Splink's save guard `if self._m_probability and self._m_is_trained` drops
    0.0 and not-observed alike, so a routine trained model legitimately omits
    m_probability -- M13 measured 3 of 14 non-null levels missing it. Rejecting
    absence would red the nightly job on an ordinary Splink artefact, and the
    likely "fix" is weakening the validator.
    """
    assert er_sidecar.validate(
        _levels({"sql_condition": '"a_l" = "a_r"'}, {"sql_condition": "ELSE"})
    )


def test_m13_one_point_zero_exactly_is_valid() -> None:
    """A naive "probabilities in (0,1)" open-interval check rejects it.

    M13 measured three levels at exactly 1.0 in a real production model.
    """
    raw = _levels(
        {"sql_condition": '"a_l" = "a_r"', "m_probability": 1.0, "u_probability": 1.0},
        {"sql_condition": "ELSE"},
    )
    assert er_sidecar.validate(raw)


def test_m13_rejects_a_probability_outside_the_unit_interval() -> None:
    raw = _levels(
        {"sql_condition": '"a_l" = "a_r"', "u_probability": 1.5}, {"sql_condition": "ELSE"}
    )
    assert "ER-043" in _codes(raw)


# ---------------------------------------------------------------------------
# D.0 finding 81 -- the model JSON carries SQL in TWO places and §1.5 policed
# one. Every payload below is rejected in a comparison level and was ACCEPTED
# as a blocking rule until 2026-08-23.
# ---------------------------------------------------------------------------

HOSTILE_RULES = [
    ("subquery", 'l."x" = (select max("y") from other_table)'),
    ("statement terminator", 'l."x" = r."x"; drop table er_stg_input'),
    ("non-deterministic", 'l."x" = r."x" and random() < 0.5'),
    ("outside the D6 allow-list", 'l."x" = r."x" and read_csv_auto(\'/etc/passwd\') is not null'),
]


def _model_with_rule(rule: object) -> str:
    return json.dumps(
        {
            "blocking_rules_to_generate_predictions": [{"blocking_rule": rule}],
            "comparisons": [
                {
                    "output_column_name": "c",
                    "comparison_levels": [
                        {"sql_condition": '"a_l" = "a_r"', "m_probability": 0.9},
                        {"sql_condition": "ELSE"},
                    ],
                }
            ],
        }
    )


@pytest.mark.parametrize(
    "payload", [payload for _, payload in HOSTILE_RULES], ids=[label for label, _ in HOSTILE_RULES]
)
def test_a_hostile_blocking_rule_is_rejected(payload: str) -> None:
    """`blocking_rule` is interpolated into `er_blocking_sql` as `{{ rule }}`.

    It is executed with the consumer's credentials -- exactly the threat DR-17
    and G3 describe -- so it must clear the same boundary a comparison level
    does. The hole was unreachable only because Stage 3 did not exist yet.
    """
    with pytest.raises(er_sidecar.ValidationError):
        er_sidecar.validate(_model_with_rule(payload))


def test_a_bare_string_blocking_rule_is_still_validated() -> None:
    """Splink accepts a bare string as well as the `{blocking_rule, ...}` mapping.

    Reading only the mapping shape would leave the other one unpoliced, which is
    the same defect one level down.
    """
    raw = json.loads(_model_with_rule("placeholder"))
    raw["blocking_rules_to_generate_predictions"] = ['l."x" = r."x"; drop table t']
    with pytest.raises(er_sidecar.ValidationError):
        er_sidecar.validate(json.dumps(raw))


def test_an_unrecognised_blocking_rule_shape_is_not_silently_skipped() -> None:
    """A shape the reader does not understand must not yield nothing.

    Yielding nothing is how this field became unpoliced in the first place: the
    walk simply never produced it, and a validator that inspects nothing passes
    everything (§6.1).
    """
    for shape in (None, 42, {"not_blocking_rule": "x"}):
        with pytest.raises(er_sidecar.ValidationError):
            er_sidecar.validate(_model_with_rule(shape))


def test_the_real_model_reports_both_counts() -> None:
    """Coverage is reported, so "validated nothing" cannot look like "validated"."""
    artefact = er_sidecar.validate(FROZEN.read_text(encoding="utf-8"))
    assert artefact["levels_validated"] == 28
    assert artefact["blocking_rules_validated"] == 4
