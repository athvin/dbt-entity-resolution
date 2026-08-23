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
    conditions = list(_sql_conditions(model))
    for where, condition in conditions:
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
    }


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


def main() -> int:
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
        "--check",
        action="store_true",
        help="regenerate and compare byte-for-byte against --out, without writing",
    )
    args = parser.parse_args()

    raw = args.model_json.read_text(encoding="utf-8")
    try:
        artefact = build(raw) if (args.out or args.check) else validate(raw)
    except ValidationError as err:
        for finding in err.findings:
            sys.stderr.write(f"ERROR: {finding}\n")
        sys.stderr.write(
            f"\n{len(err.findings)} failure(s). This model JSON has no er_model_sha "
            f"and will not build (§1.5 rule 5).\n"
        )
        return 1

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
