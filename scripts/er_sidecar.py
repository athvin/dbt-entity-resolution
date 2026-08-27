#!/usr/bin/env python3
"""The compile-time sidecar: the model JSON's trust boundary (Stage 1, §1.5, DR-17).

**The model JSON is an input, not a contract, until it has been validated.**

The reason is D6: comparison-level SQL is passed through **verbatim** into
compiled SQL, and D1 delivers the JSON through an environment variable — so it
never passes review as code. §1.5 gives both readings:

> In a consumer's project `DBT_ER_MODEL_JSON` may be set from a pipeline
> variable rather than a reviewed file, at which point this package executes
> arbitrary SQL with that consumer's warehouse credentials. That is the hostile
> reading. The benign one is likelier and nearly as damaging: an analyst adds an
> age-band level containing `current_date` — the natural way to write one —
> gamma becomes a function of the wall clock, and every parity and determinism
> gate in §6 silently stops being true while CI merely looks flaky.

**Validation is against the PARSED TREE, never the raw string.** §1.5 is explicit
that string-matching is what makes an allow-list bypassable, and A.2 C2 records
the same mistake for TF resolution: *"Jinja can only string-match."* sqlglot is
already a pinned dependency because it decides TF adjustment, so the tree is
already available.

Five rules, all compile-time, all failing the build rather than warning:

  1. every function appears in D6's closed allow-list, named on violation
  2. non-deterministic functions rejected outright, listed or not
  3. structural rejection — no subquery, terminator, set op, CTE, or side effect
  4. the input is bounded, so "the JSON is enormous" is a diagnosis not a symptom
  5. `er_model_sha` hashes the VALIDATED artefact, so an unvalidated JSON has no
     sha and a model with no sha does not build

Rule 5 is what makes 1-4 unskippable.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from _er_paths import ROOT, rel

if TYPE_CHECKING:
    from collections.abc import Iterator

# D6's allow-list, normative and CLOSED. Adding to it is a reviewed act with the
# determinism argument written down -- not a convenience.
ALLOWED_FUNCTIONS: frozenset[str] = frozenset(
    {
        "levenshtein",
        "damerau_levenshtein",
        "jaro_similarity",
        "jaro_winkler_similarity",
        "jaccard",
        "array_cosine_similarity",
        "list_intersect",
        "array_intersect",
        "array_length",
        "list_max",
        "list_min",
        "list_transform",
        "flatten",
        "try_strptime",
        "epoch",
        "radians",
        "acos",
        "sin",
        "cos",
        "least",
        "greatest",
        "regexp_extract",
        "nullif",
        "substring",
        "lower",
        "date_trunc",
        "split_part",
        # Added 2026-08-23 with the determinism argument §D6 requires: `abs` is a
        # pure numeric function that reads no session or wall-clock state, and a
        # STOCK `DateOfBirthComparison` emits it, in an absolute-difference of
        # epoch-seconds condition -- so the list as published rejected the very
        # comparison library D6 says it surveyed.
        "abs",
    }
)

# D6's list is written in DUCKDB's vocabulary; §1.5 requires matching against
# sqlglot's PARSED TREE, and sqlglot canonicalises some builtins to different
# names. The rule and the list were in different languages.
#
# Measured on the frozen model JSON: `EPOCH(...)` parses to `time_to_unix` and
# `jaro_winkler_similarity(...)` to `jarowinkler_similarity`. Both are functions
# D6 explicitly allows, and both were being rejected.
#
# The mapping is canonical-name -> the D6 spelling a reader will look for.
SQLGLOT_CANONICAL_NAMES = {
    "time_to_unix": "epoch",
    "jarowinkler_similarity": "jaro_winkler_similarity",
}

# §1.5 rule 2. Rejected whether or not they appear above, because this is the
# rule that keeps §6.3's determinism claim true rather than aspirational.
NON_DETERMINISTIC: frozenset[str] = frozenset(
    {
        "current_date",
        "current_timestamp",
        "current_time",
        "current_localtimestamp",
        "localtime",
        "localtimestamp",
        "now",
        "today",
        "random",
        "rand",
        "nextval",
        "uuid",
        "uuidv4",
        "uuidv7",
        "gen_random_uuid",
        "current_setting",
        "getvariable",
        "version",
        # sqlglot canonicalises `version()` to CURRENT_VERSION; both spellings
        # are listed so neither vocabulary can slip through.
        "current_version",
        "current_schema",
        "current_database",
        "current_user",
        "txid_current",
    }
)

# §1.5 rule 3. Anything that is not a scalar boolean expression over columns of
# the current row pair, however it is spelled.
FORBIDDEN_STATEMENTS = (
    "Select",
    "Union",
    "Intersect",
    "Except",
    "With",
    "CTE",
    "Insert",
    "Update",
    "Delete",
    "Create",
    "Drop",
    "Alter",
    "Copy",
    "Attach",
    "Detach",
    "Command",
    "Use",
    "Pragma",
    "Set",
    "Merge",
)

# §1.5 rule 4. M2 measures 397 B/level, so generous defaults are cheap.
DEFAULT_BOUNDS = {
    "er_max_comparisons": 50,
    "er_max_levels_per_comparison": 30,
    "er_max_model_json_bytes": 4_194_304,
}


# Splink writes the final comparison level's `sql_condition` as this literal.
ELSE_SENTINEL = "ELSE"


class ValidationError(Exception):
    """A model JSON that must not build. Carries every finding, not just the first."""

    def __init__(self, findings: list[str]) -> None:
        """Collect every finding, so three bad levels report three."""
        self.findings = findings
        super().__init__(f"{len(findings)} model-JSON validation failure(s)")


def _sql_conditions(model: dict[str, Any]) -> Iterator[tuple[str, str]]:
    """Yield `(where, sql_condition)` for every comparison level."""
    for c_index, comparison in enumerate(model.get("comparisons") or []):
        name = comparison.get("output_column_name", f"comparison[{c_index}]")
        for l_index, level in enumerate(comparison.get("comparison_levels") or []):
            condition = level.get("sql_condition")
            if isinstance(condition, str) and condition.strip():
                yield f"{name}.level[{l_index}]", condition


def _blocking_rules(model: dict[str, Any]) -> Iterator[tuple[str, str | None]]:
    """Yield `(where, blocking_rule)` for every blocking rule.

    **The model JSON carries SQL in TWO places, and §1.5 policed one of them.**
    `blocking_rules_to_generate_predictions[].blocking_rule` is interpolated
    into `er_blocking_sql` as `{{ rule }}` and executed with the consumer's
    credentials -- the exact threat DR-17 and G3 describe -- while
    `_sql_conditions()` walked only comparison levels.

    Measured before the fix, on payloads the boundary rejects in a level:

        subquery / exfiltration shape   level=rejected   blocking_rule=ACCEPTED
        statement terminator            level=rejected   blocking_rule=ACCEPTED
        non-deterministic function      level=rejected   blocking_rule=ACCEPTED
        function outside the D6 list    level=rejected   blocking_rule=ACCEPTED

    The hole was unreachable only because Stage 3 did not exist yet -- nothing
    in the package called `er_blocking_sql` with rules from a model JSON. It
    becomes reachable the moment that stage ships, which is why it is closed
    first (D.0 finding 81).

    Splink also accepts a bare string here rather than the `{blocking_rule,
    sql_dialect}` mapping, so both shapes are read.

    A shape this function does not recognise yields `None`, which the caller
    turns into a finding. The first fix yielded `repr(rule)` and let
    `_check_condition` judge it -- which is worse than useless: `repr(None)` is
    `"None"` and `repr(42)` is `"42"`, both of which sqlglot parses happily as
    an identifier and a literal, so a malformed rule became *innocuous SQL* and
    passed. Turning input the validator does not understand into input it
    approves of is the failure this whole boundary exists to prevent.
    """
    for index, rule in enumerate(model.get("blocking_rules_to_generate_predictions") or []):
        where = f"blocking_rules_to_generate_predictions[{index}]"
        if isinstance(rule, str):
            yield where, (rule if rule.strip() else None)
        elif isinstance(rule, dict):
            condition = rule.get("blocking_rule")
            yield where, (condition if isinstance(condition, str) and condition.strip() else None)
        else:
            yield where, None


def _check_bounds(model: dict[str, Any], raw_bytes: int, bounds: dict[str, int]) -> list[str]:
    """Rule 4. A named error rather than a pathological build."""
    findings: list[str] = []
    comparisons = model.get("comparisons") or []
    if len(comparisons) > bounds["er_max_comparisons"]:
        findings.append(
            f"ER-030: {len(comparisons)} comparisons exceeds er_max_comparisons="
            f"{bounds['er_max_comparisons']}."
        )
    for index, comparison in enumerate(comparisons):
        levels = comparison.get("comparison_levels") or []
        if len(levels) > bounds["er_max_levels_per_comparison"]:
            name = comparison.get("output_column_name", f"comparison[{index}]")
            findings.append(
                f"ER-031: `{name}` has {len(levels)} levels, over "
                f"er_max_levels_per_comparison={bounds['er_max_levels_per_comparison']}."
            )
    if raw_bytes > bounds["er_max_model_json_bytes"]:
        findings.append(
            f"ER-032: the model JSON is {raw_bytes} bytes, over "
            f"er_max_model_json_bytes={bounds['er_max_model_json_bytes']}."
        )
    return findings


def _check_condition(where: str, condition: str) -> list[str]:
    """Rules 1-3, against the PARSED TREE."""
    import sqlglot  # noqa: PLC0415
    from sqlglot import exp  # noqa: PLC0415

    findings: list[str] = []

    # Splink's else-level carries the literal sentinel "ELSE" rather than SQL.
    # It is not a condition and must not be parsed as one -- 5 of the frozen
    # model JSON's levels are exactly this.
    if condition.strip().upper() == ELSE_SENTINEL:
        return []

    # A statement terminator is rejected before parsing: sqlglot happily parses
    # `a = b; drop table t` as TWO statements and would otherwise validate only
    # the first, which is the classic injection shape.
    try:
        statements = sqlglot.parse(condition, read="duckdb")
    except Exception as err:  # noqa: BLE001 - sqlglot raises several types
        return [f"ER-034: {where} does not parse as DuckDB SQL: {err}"]
    if len(statements) > 1:
        return [
            (
                f"ER-033: {where} contains {len(statements)} statements. A "
                f"sql_condition is ONE scalar boolean expression; a terminator is "
                f"how an allow-list gets bypassed."
            )
        ]

    try:
        tree = sqlglot.parse_one(condition, read="duckdb")
    except Exception as err:  # noqa: BLE001 - sqlglot raises several types
        return [f"ER-034: {where} does not parse as DuckDB SQL: {err}"]

    if tree is None:
        return [f"ER-034: {where} parsed to nothing."]

    for node in tree.walk():
        findings.extend(_check_node(node, exp, where))
    return findings


def _check_node(node: Any, exp: Any, where: str) -> list[str]:
    """Rules 1-3 for one node of the parsed tree."""
    kind = type(node).__name__
    if kind in FORBIDDEN_STATEMENTS:
        return [
            (
                f"ER-035: {where} contains a `{kind}` node. A sql_condition is a "
                f"scalar boolean expression over columns of the current row pair -- "
                f"no subquery, set operation, CTE or statement (§1.5 rule 3)."
            )
        ]

    names = _function_names(node, exp)
    if not names:
        return []
    # Map any alias back to the D6 spelling a reader will look for.
    names |= {SQLGLOT_CANONICAL_NAMES[n] for n in names if n in SQLGLOT_CANONICAL_NAMES}
    shown = min(names)

    if names & NON_DETERMINISTIC:
        return [
            (
                f"ER-036: {where} calls `{shown}`, which is non-deterministic. "
                f"Rejected whether or not it is allow-listed: gamma would become a "
                f"function of the wall clock and every §6 parity and determinism "
                f"gate would silently stop being true (§1.5 rule 2)."
            )
        ]
    if not (names & ALLOWED_FUNCTIONS):
        return [
            (
                f"ER-037: {where} calls `{shown}`, which is not in D6's allow-list. "
                f"The list is normative and closed; adding to it is a reviewed act "
                f"with the determinism argument written down (§1.5 rule 1)."
            )
        ]
    return []


def _function_names(node: Any, exp: Any) -> set[str]:
    """Return EVERY name sqlglot knows this call by, lowercased.

    **All aliases, not just the canonical one, and the reason is a false accept.**
    sqlglot reports `random()` as `exp.Rand` whose `sql_names()` is
    `['RAND', 'RANDOM']`. Taking only the first yields `rand`, which is not in
    the non-determinism list -- so `random()` was **silently accepted**, and
    §6.3's determinism claim would have been quietly untrue. `version()` ->
    `CURRENT_VERSION` was the same.

    On the allow-list a vocabulary mismatch causes a noisy false REJECT. On the
    non-determinism list it causes a silent false ACCEPT. Checking every alias
    is robust to canonicalisation in both directions, and needs no hand-kept
    name mapping.

    Two further subtleties, both found by running this against a real model JSON:

    **Boolean operators are `Func` subclasses.** `exp.Or` satisfies
    `isinstance(node, exp.Func)` and reports `sql_names() == ["OR"]`, so a naive
    check rejects `a = b OR c IS NULL` as calling an unlisted function `or`.
    `exp.Connector` is what separates an operator from a call.

    **Most builtins are not `Anonymous`.** sqlglot models them as their own
    classes, so `sql_names()` is what maps a node back to a name -- checking only
    `Anonymous` would miss `lower`, `least`, `substring` and most of the list.
    """
    if isinstance(node, exp.Connector):
        return set()
    if isinstance(node, exp.Anonymous):
        return {str(node.this).lower()}
    if isinstance(node, exp.Func):
        return {str(name).lower() for name in type(node).sql_names()}
    return set()


def validate(raw: str, bounds: dict[str, int] | None = None) -> dict[str, Any]:
    """Validate a model JSON. Returns the validated artefact, or raises.

    Every finding is collected rather than stopping at the first, because a
    model JSON with three bad levels should report three, not require three
    build attempts.
    """
    effective = {**DEFAULT_BOUNDS, **(bounds or {})}
    raw_bytes = len(raw.encode("utf-8"))

    try:
        model = json.loads(raw)
    except json.JSONDecodeError as err:
        raise ValidationError([f"ER-038: the model JSON does not parse: {err}"]) from err
    if not isinstance(model, dict):
        raise ValidationError(
            [f"ER-038: the model JSON is a {type(model).__name__}, not an object."]
        )

    findings = _check_bounds(model, raw_bytes, effective)
    findings.extend(_check_output_column_names(model))
    findings.extend(_check_probabilities(model))
    conditions = list(_sql_conditions(model))
    rules = list(_blocking_rules(model))
    findings.extend(
        f"ER-037: {where} is not a blocking rule this validator understands. A "
        f"shape it cannot read must not be treated as absent -- that is how "
        f"`blocking_rule` went unpoliced in the first place (§1.5, D.0 81)."
        for where, condition in rules
        if condition is None
    )
    # Both SQL-bearing fields, through the SAME checks. Two code paths with two
    # policies is how one of them ends up with no policy at all.
    for where, condition in [*conditions, *rules]:
        if condition is not None:
            findings.extend(_check_condition(where, condition))

    if not conditions:
        findings.append(
            "ER-039: the model JSON declares no comparison levels with a "
            "sql_condition. A validator that inspects nothing passes everything."
        )

    if findings:
        raise ValidationError(findings)

    # Rule 5: the sha is of the VALIDATED artefact, serialised canonically so
    # that whitespace or key order in the environment cannot change it.
    canonical = json.dumps(model, sort_keys=True, separators=(",", ":"))
    return {
        "er_model_sha": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        "levels_validated": len(conditions),
        "blocking_rules_validated": len(rules),
        "comparisons": len(model.get("comparisons") or []),
        "model": model,
    }


def build(raw: str, bounds: dict[str, int] | None = None) -> dict[str, Any]:
    """Validate, then resolve. The sidecar artefact in full.

    Order matters: **nothing is resolved until it has been validated.** Resolving
    first would mean importing an unvalidated model JSON into Splink, which is
    the trust boundary §1.5 exists to hold.
    """
    validated = validate(raw, bounds)
    return {
        "er_model_sha": validated["er_model_sha"],
        "levels_validated": validated["levels_validated"],
        "comparisons": validated["comparisons"],
        "resolved": resolve(validated["model"]),
        # M1 is reported rather than rejected: an asymmetric level is legitimate
        # in a link job whose orientation is settled. What is not legitimate is
        # not knowing, so it travels with the artefact.
        "asymmetric_levels": _check_symmetry(validated["model"]),
        # Stage 3 consumes these as `er_blocking_rules`. Published from the
        # VALIDATED model and never from the raw one -- these strings are
        # interpolated into `er_blocking_sql` and executed with the consumer's
        # credentials, so the only version that may leave this module is the one
        # that cleared §1.5 (D.0 finding 81).
        # Stage 4 consumes this. The gamma CASE needs BOTH halves and they live
        # in different places: `sql_condition` is the raw model's, and
        # `comparison_vector_value` is SPLINK's -- A.2 C2 is explicit that it
        # cannot be inferred from list position. Merging them here, once, is
        # what stops every consumer doing it in Jinja and getting it subtly
        # different (M2's drift, applied to structure rather than to columns).
        "er_comparisons": [
            {
                "output_column_name": str(comparison["output_column_name"]).replace(" ", "_"),
                "levels": [
                    {
                        "sql_condition": raw_level.get("sql_condition"),
                        "comparison_vector_value": level["comparison_vector_value"],
                    }
                    for raw_level, level in zip(
                        raw_comparison.get("comparison_levels") or [],
                        comparison["levels"],
                        strict=True,
                    )
                ],
            }
            for raw_comparison, comparison in zip(
                validated["model"].get("comparisons") or [],
                resolve(validated["model"])["comparisons"],
                strict=True,
            )
        ],
        "er_blocking_rules": [
            condition for _, condition in _blocking_rules(validated["model"]) if condition
        ],
        # M2: published as vars so `schema.yml` can contract the two models
        # whose column set is data. RC57's drift guard covers these against the
        # rendered SQL and the unit-test fixtures -- three artefacts, one JSON.
        **column_lists(resolve(validated["model"])),
    }


def _check_output_column_names(model: dict[str, Any]) -> list[str]:
    """M2: uniqueness AFTER `.replace(" ", "_")` normalisation.

    `int_comparison_vectors` emits one `gamma_<name>` per comparison and
    `int_scored_pairs` one `bf_<name>`, so two comparisons whose names normalise
    to the same identifier silently collapse two columns into one -- and because
    the column set is data (M2), no contract or unit test would catch it.
    """
    seen: dict[str, str] = {}
    findings: list[str] = []
    for index, comparison in enumerate(model.get("comparisons") or []):
        raw = comparison.get("output_column_name") or f"comparison[{index}]"
        normalised = str(raw).replace(" ", "_")
        if normalised in seen and seen[normalised] != raw:
            findings.append(
                f"ER-040: `{raw}` and `{seen[normalised]}` both normalise to "
                f"`{normalised}`. They would emit the same gamma_ and bf_ columns, "
                f"collapsing two comparisons into one (M2)."
            )
        elif normalised in seen:
            findings.append(f"ER-040: `{raw}` is declared more than once (M2).")
        seen[normalised] = raw
    return findings


def _check_probabilities(model: dict[str, Any]) -> list[str]:
    """M13: a PRESENT zero is a hard error; an ABSENT value is valid input.

    The distinction is the whole finding. Splink's save guard is
    `if self._m_probability and self._m_is_trained` (`comparison_level.py:654-658`),
    whose truthiness drops `0.0` **and** not-observed alike -- so a routine
    trained model legitimately omits `m_probability` on some levels, and §3.4
    documents that reload substitutes `_default_m_values`. M13 measured 3 of 14
    non-null levels missing `m_probability` in an ordinary 400-row training.

    Rejecting absence would red the nightly model-varying job on a perfectly
    ordinary Splink artefact -- and M13's failure scenario is someone then
    "fixing" it by weakening the validator, losing the guard on genuinely
    malformed input.

    **`1.0` exactly is valid.** The same production model carries three levels at
    `m_probability == 1.0`, which a naive "probabilities in (0,1)" open-interval
    check rejects.
    """
    findings: list[str] = []
    for c_index, comparison in enumerate(model.get("comparisons") or []):
        name = comparison.get("output_column_name", f"comparison[{c_index}]")
        for l_index, level in enumerate(comparison.get("comparison_levels") or []):
            where = f"{name}.level[{l_index}]"
            for field in ("m_probability", "u_probability"):
                value = level.get(field)
                if value is None:
                    continue  # ABSENT is valid input -- see the docstring.
                if not isinstance(value, (int, float)) or isinstance(value, bool):
                    findings.append(f"ER-041: {where} has a non-numeric `{field}`: {value!r}.")
                elif value == 0:
                    findings.append(
                        f"ER-042: {where} has `{field} = 0`, which is a HARD error: "
                        f"a zero bayes factor makes the pair unscoreable and log2(0) "
                        f"is undefined. Note that an ABSENT {field} is valid input "
                        f"and renders _default_m_values (M13)."
                    )
                elif not 0 <= value <= 1:
                    findings.append(f"ER-043: {where} has `{field} = {value}`, outside [0, 1].")
    return findings


_SIDE_SUFFIXES = ("_l", "_r")


def _check_symmetry(model: dict[str, Any]) -> list[str]:
    """M1: asymmetric levels, which make gamma orientation-dependent.

    `[RECON]` `ColumnsReversedLevel('forename','surname')` renders
    `"forename_l" = "surname_r"` -- and **`symmetrical: bool = False` is the
    default** (`comparison_level_library.py:363-397`). Executed in DuckDB on one
    pair, the Splink orientation gives `false` and the flipped orientation
    `true`: same pair, opposite gamma.

    That kills S2's *"match the set, not the orientation"* escape, because a
    canonicalised set comparison at Stage 3 **hides** a real Stage-4 divergence
    rather than deferring it. Dormant on dedupe-only configs with symmetric
    levels -- i.e. until the first link job or the first `ColumnsReversed` level.

    Reported, not rejected: an asymmetric level is legitimate in a link job that
    has settled its orientation (A.2 C4, Open Question 3). What is not legitimate
    is not knowing.
    """
    import sqlglot  # noqa: PLC0415
    from sqlglot import exp  # noqa: PLC0415

    findings: list[str] = []
    for c_index, comparison in enumerate(model.get("comparisons") or []):
        name = comparison.get("output_column_name", f"comparison[{c_index}]")
        for l_index, level in enumerate(comparison.get("comparison_levels") or []):
            condition = level.get("sql_condition")
            if not isinstance(condition, str) or condition.strip().upper() == ELSE_SENTINEL:
                continue
            try:
                tree = sqlglot.parse_one(condition.lower(), read="duckdb")
            except Exception:  # noqa: BLE001, S112 - validation reports parse errors
                continue
            if tree is None:
                continue

            bases: dict[str, set[str]] = {"_l": set(), "_r": set()}
            for column in tree.find_all(exp.Column):
                output = str(column.output_name).lower()
                for suffix in _SIDE_SUFFIXES:
                    if output.endswith(suffix):
                        bases[suffix].add(output[: -len(suffix)])
            if bases["_l"] != bases["_r"]:
                findings.append(
                    f"ER-044: {name}.level[{l_index}] is ASYMMETRIC -- left columns "
                    f"{sorted(bases['_l'])} against right {sorted(bases['_r'])}. Gamma "
                    f"is not orientation-invariant here, so canonicalising the pair "
                    f"set at Stage 3 would HIDE a Stage-4 divergence rather than "
                    f"defer it (M1). Legitimate in a link job with a settled "
                    f"orientation; record it in PARITY.md."
                )
    return findings


def resolve(model: dict[str, Any]) -> dict[str, Any]:
    """Resolve what Jinja provably cannot (A.2 C2, C3, C4).

    **Splink's own code does the resolving.** A.2 sanctions it -- the sidecar is
    *"produced by a Python preprocessing step that imports Splink"* -- and it is
    the only way to be exact rather than approximate. A.2 C2 measured what
    string-matching gets wrong **in both directions**:

    | condition | exact match? |
    |---|---|
    | `"name_r" = "name_l"` | **True** (reversed order still counts) |
    | `NOT ("name_l" <> "name_r")` | **True** (negated inequality counts) |
    | `"name_l" = "name_r" AND 1=1` | **False** (an extra conjunct disqualifies) |
    | `lower(a_l) = lower(a_r)` | **False** (a function application disqualifies) |

    Splink reaches those answers via `simplify(normalize(tree))` to CNF, then
    compares **tree signatures**. Reimplementing that in Python would be the same
    mistake as approximating it in Jinja, one language along -- so this calls it.
    The four cases above are pinned as tests, which is what catches a Splink
    internal moving under us.
    """
    from splink.internals.comparison import Comparison  # noqa: PLC0415

    resolved_comparisons: list[dict[str, Any]] = []
    for comparison in model.get("comparisons") or []:
        levels = comparison.get("comparison_levels") or []
        name = comparison.get("output_column_name")
        built = Comparison(levels, sqlglot_dialect="duckdb", output_column_name=name)

        resolved_levels: list[dict[str, Any]] = []
        for level in built.comparison_levels:
            # Null levels have no m or u -- Splink raises rather than returning
            # None, so the absence is a property to record, not an error.
            is_null = bool(level.is_null_level)
            resolved_levels.append(
                {
                    "comparison_vector_value": level.comparison_vector_value,
                    "is_null_level": is_null,
                    "is_exact_match": _safely(
                        lambda lv=level: bool(lv._is_exact_match)  # noqa: SLF001
                    ),
                    "m_probability": None
                    if is_null
                    else _safely(lambda lv=level: lv.m_probability),
                    "u_probability": None
                    if is_null
                    else _safely(lambda lv=level: lv.u_probability),
                    # Resolved only where the level carries a TF adjustment;
                    # Splink raises otherwise, and None is the right answer.
                    "tf_u_exact_match": _safely(
                        # `levels` is bound too: B023 -- without it the lambda
                        # would close over the loop variable, which is a real
                        # bug waiting for the day this stops being eager.
                        lambda lv=level, levels=built.comparison_levels: (
                            lv._u_probability_corresponding_to_exact_match(levels)  # noqa: SLF001
                        )
                    ),
                }
            )
        resolved_comparisons.append({"output_column_name": name, "levels": resolved_levels})

    return {
        "comparisons": resolved_comparisons,
        # A.2 C3. `inference.py:227-246` rewrites link_only -> two_dataset_link_only
        # when there are exactly two input tables -- a RUNTIME fact, not a JSON one.
        "er_backend_link_type": model.get("link_type", "dedupe_only"),
        # A.2 C3's measured trap: `source_dataset_column_name` is present in the
        # JSON even for dedupe_only, while the runtime input column is None. The
        # JSON's key is therefore NOT evidence that a source dataset exists.
        "er_has_source_dataset": bool(model.get("source_dataset_column_name"))
        and model.get("link_type") != "dedupe_only",
        # A.2 C4. `min(templated_name)` over input-table aliases, which is not in
        # the JSON at all -- so it stays null until a two-table configuration
        # supplies it, and Open Question 3 governs refusing the configuration.
        "er_left_table": None,
    }


def column_lists(resolved: dict[str, Any]) -> dict[str, list[Any]]:
    """M2's `er_gamma_columns` and `er_bf_columns`, as dbt `columns:` entries.

    Also `er_tf_columns` (D7a), which is a plain list of names rather than
    `columns:` entries -- it selects rows in `er_tf_all`, it does not contract
    columns. The looser return type is that difference, not sloppiness.

    **dbt parses `schema.yml` as YAML BEFORE rendering Jinja**, so a `{% for %}`
    cannot emit YAML structure -- which silently removes contracts, per-column
    tests, docs and unit-test fixtures from exactly the two models Stages 4 and
    5 are about. `[SRC] dbt/parser/schemas.py:128-155`.

    The mechanism that does work is **native** Jinja on a string leaf
    (`renderer.py:43-48`): `columns: "{{ var('er_gamma_columns') }}"` renders to
    a genuine list of dicts. And `SchemaYamlContext` adds only `var` and
    `env_var` -- **no project macros** -- so the derivation must live where the
    model JSON is emitted, which is here, not in a macro.

    That constraint is why this is a Stage-1 decision: M2's failure scenario is
    it being noticed mid-Stage-4, "a Stage-1 decision being made in Stage 4".
    """
    gamma: list[dict[str, str]] = []
    bayes: list[dict[str, str]] = []
    # D7a: which columns `tf_all` must carry a distribution for. Derived from the
    # SAME predicate that decides `bf_tf_adj_`, deliberately -- a TF adjustment
    # the snapshot has no values for is a run-time failure, and two independently
    # maintained lists is exactly M2's drift.
    tf_columns: list[str] = []
    for comparison in resolved["comparisons"]:
        name = str(comparison["output_column_name"]).replace(" ", "_")
        # The gamma CASE emits integer comparison_vector_values.
        gamma.append({"name": f"gamma_{name}", "data_type": "INTEGER"})
        bayes.append({"name": f"bf_{name}", "data_type": "DOUBLE"})
        # `bf_tf_adj_` exists only where a level carries a TF adjustment --
        # emitting it unconditionally would contract a column the SQL never
        # produces, and dbt raises on the mismatch at run time.
        if any(level["tf_u_exact_match"] is not None for level in comparison["levels"]):
            bayes.append({"name": f"bf_tf_adj_{name}", "data_type": "DOUBLE"})
            tf_columns.append(str(comparison["output_column_name"]))
    return {
        "er_gamma_columns": gamma,
        "er_bf_columns": bayes,
        "er_tf_columns": tf_columns,
    }


def _safely(call: Any) -> Any:
    """Return `call()`, or None where Splink raises because the value is absent.

    Splink signals "this level has no m-probability" and "this level has no
    exact-match level to draw a TF u from" by raising. Both are legitimate
    states of a valid model, so the absence is recorded rather than propagated.
    """
    try:
        return call()
    except Exception:  # noqa: BLE001 - absence is signalled by several types
        return None


def main() -> int:  # noqa: PLR0911 -- one return per CLI mode; a dispatch
    # table would hide which mode produced which exit code, and this script's
    # exit codes are its contract (§1.5 rule 5).
    """Validate a model JSON and emit the sidecar artefact."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("model_json", type=Path)
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="write the resolved sidecar artefact here (A.2's committed, hashed file)",
    )
    parser.add_argument(
        "--emit",
        metavar="KEY",
        default=None,
        help=(
            "print one top-level key of the artefact as compact JSON, for a "
            "Makefile or workflow to export into the environment. §9's route: "
            "sqlfluff has no --vars and dbt renders a Jinja-bearing vars: value "
            "to a string, so the environment is the only channel both can see."
        ),
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="regenerate and compare byte-for-byte against --out, without writing",
    )
    args = parser.parse_args()

    raw = args.model_json.read_text(encoding="utf-8")
    try:
        artefact = build(raw) if (args.out or args.check or args.emit) else validate(raw)
    except ValidationError as err:
        for finding in err.findings:
            sys.stderr.write(f"ERROR: {finding}\n")
        sys.stderr.write(
            f"\n{len(err.findings)} failure(s). This model JSON has no er_model_sha "
            f"and will not build (§1.5 rule 5).\n"
        )
        return 1

    if args.emit:
        # Nothing else is printed: the caller is a `$(shell ...)` and anything
        # on stdout becomes part of the exported value.
        if args.emit not in artefact:
            sys.stderr.write(
                f"ERROR: --emit {args.emit!r} is not a key of the sidecar artefact. "
                f"Available: {', '.join(sorted(artefact))}.\n"
            )
            return 1
        sys.stdout.write(json.dumps(artefact[args.emit], separators=(",", ":")))
        return 0

    sys.stdout.write(
        f"validated {rel(args.model_json, ROOT)}: "
        f"{artefact['comparisons']} comparison(s), "
        f"{artefact['levels_validated']} level(s), "
        f"er_model_sha={artefact['er_model_sha'][:12]}...\n"
    )
    if not args.out:
        return 0

    rendered = json.dumps(artefact, indent=2, sort_keys=True) + "\n"

    # A.2: "Guard it with a byte-equality regeneration test." A generated file
    # that has drifted from its generator is worse than no generated file,
    # because every downstream consumer trusts it.
    if args.check:
        if not args.out.is_file():
            sys.stderr.write(f"ERROR: {rel(args.out, ROOT)} does not exist; run without --check.\n")
            return 1
        if args.out.read_text(encoding="utf-8") != rendered:
            sys.stderr.write(
                f"ERROR: {rel(args.out, ROOT)} is not what this model JSON generates. "
                f"Regenerate it -- a sidecar that has drifted from its model JSON is "
                f"a contract nobody is honouring (A.2).\n"
            )
            return 1
        sys.stdout.write(f"  {rel(args.out, ROOT)} regenerates byte-identically.\n")
        return 0

    args.out.write_text(rendered, encoding="utf-8")
    sys.stdout.write(f"  wrote {rel(args.out, ROOT)}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
