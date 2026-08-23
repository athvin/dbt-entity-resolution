#!/usr/bin/env python3
"""Every unit-test fixture is `format: sql` with an explicit cast per column (3.69).

Section 12.2 records the measurement this rule comes from. A `format: dict`
fixture is typed by agate's inference over the literal values: it inferred
**DATE** for a `dob` column from the strings `"not-a-date"` and `"1990-13-45"`,
so dbt emitted `cast('not-a-date' as DATE)` and the test died.

The document's own verdict: *"the error was the lucky outcome. Had the sample
values all looked like dates, the fixture would have silently become a DATE
column -- and a test whose entire purpose is proving the model does not transform
values would have been running against pre-parsed input."*

dbt compares actual against expected in Python over sorted rows with **exact**
equality -- there is no float tolerance in a unit test -- so a fixture whose type
is inferred rather than declared is a test that passes for a reason nobody chose.

Checked over both project roots, because a rule that covers one is a rule with a
hole in it (3.1's lesson).
"""

from __future__ import annotations

import re
import sys
from typing import TYPE_CHECKING

import yaml

from _er_paths import ROOT, rel

if TYPE_CHECKING:
    from pathlib import Path

_PROJECT_ROOTS = (".", "integration_tests")
_MODEL_DIRS = ("models", "tests", "seeds", "snapshots")

_SQL = "sql"
# A projected column: the text between `select`/`,` and the next comma at depth 0.
_CAST = re.compile(r"\bcast\s*\(", re.IGNORECASE)
_SELECT_STAR = re.compile(r"^\s*select\s+\*", re.IGNORECASE)


def _iter_yaml(root: Path) -> list[Path]:
    paths: list[Path] = []
    for project in _PROJECT_ROOTS:
        for models in _MODEL_DIRS:
            base = root / project / models
            if not base.is_dir():
                continue
            paths.extend(
                p
                for p in base.rglob("*.yml")
                if "dbt_packages" not in p.parts and "target" not in p.parts
            )
    return sorted(set(paths))


def _split_projections(select_body: str) -> list[str]:
    """Split a select list on commas that are not inside parentheses."""
    parts: list[str] = []
    depth = 0
    current: list[str] = []
    for char in select_body:
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        if char == "," and depth == 0:
            parts.append("".join(current))
            current = []
        else:
            current.append(char)
    parts.append("".join(current))
    return [p.strip() for p in parts if p.strip()]


def _uncast_columns(rows_sql: str) -> list[str]:
    """Return every projected column lacking a `cast(... as ...)`."""
    uncast: list[str] = []
    # Each `select ... ` clause, up to `union`/`from`/end.
    for clause in re.split(r"\bunion\s+all\b|\bunion\b", rows_sql, flags=re.IGNORECASE):
        stripped = clause.strip()
        if not stripped:
            continue
        if _SELECT_STAR.match(stripped):
            uncast.append("select * -- every column is untyped")
            continue
        match = re.match(r"\s*select\b(.*?)(?:\bfrom\b|$)", stripped, re.IGNORECASE | re.DOTALL)
        if not match:
            continue
        uncast.extend(
            _column_name(projection)
            for projection in _split_projections(match.group(1))
            if not _CAST.search(projection)
        )
    return uncast


def _column_name(projection: str) -> str:
    """Return the alias if there is one, else the expression's leading token.

    3.69 requires the message to name **the fixture and the column**. An alias
    is the name the model actually produces, so `0.9 as thr_auto_merge` is
    reported as `thr_auto_merge` -- the string a reader can find in the model.
    """
    alias = re.search(r"\bas\s+([A-Za-z_][A-Za-z0-9_]*)\s*$", projection, re.IGNORECASE)
    if alias:
        return alias.group(1)
    tokens = projection.split()
    return tokens[0] if tokens else projection


def _fixture_errors(where: str, label: str, fixture: object) -> list[str]:
    """Check one `given` entry or an `expect` block."""
    if not isinstance(fixture, dict):
        return [f"{where}: {label} is not a mapping."]

    fmt = fixture.get("format")
    if fmt is None:
        return [
            (
                f"{where}: {label} declares no `format`. dbt defaults to `dict`, whose "
                f"types come from agate's inference over the literal values (section 12.2) "
                f"-- state `format: sql` explicitly."
            )
        ]
    if str(fmt).lower() != _SQL:
        return [
            (
                f"{where}: {label} uses `format: {fmt}`. Only `format: sql` is permitted "
                f'(3.69): agate inferred DATE for a `dob` column from "not-a-date", and '
                f"a silently-typed fixture tests the inference, not the model."
            )
        ]

    rows = fixture.get("rows")
    if rows is None or not str(rows).strip():
        # A legitimately empty fixture -- e.g. `rows: ""` for a no-rows case.
        return []

    return [
        f"{where}: {label} projects `{column}` without an explicit "
        f"`cast(... as ...)`. Unit tests compare with exact equality and no float "
        f"tolerance, so every column's type must be declared, not inferred (3.69)."
        for column in _uncast_columns(str(rows))
    ]


def check(root: Path = ROOT) -> list[str]:
    """Return every unit-test fixture that is implicitly typed."""
    errors: list[str] = []
    fixtures_seen = 0

    for path in _iter_yaml(root):
        try:
            loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as err:
            errors.append(f"{rel(path, root)}: unparseable YAML: {err}")
            continue
        if not isinstance(loaded, dict):
            continue
        # 3.72. A `unit_tests:` block nested under a `models:` entry is SILENTLY
        # ignored by dbt-core 1.12.2 -- clean parse, exit 0, no warning (D.0
        # finding 4). The compile gate does catch the consequence, but it says
        # "no unit test" about a file that visibly contains three of them.
        errors.extend(
            f"{rel(path, root)}: model `{model.get('name', '<unnamed>')}` has a "
            f"nested `unit_tests:` key. dbt ignores it silently -- the tests "
            f"never run and nothing says so. Move it to the top level (3.72)."
            for model in loaded.get("models") or []
            if isinstance(model, dict) and "unit_tests" in model
        )

        for test in loaded.get("unit_tests") or []:
            if not isinstance(test, dict):
                continue
            name = test.get("name", "<unnamed>")
            where = f"{rel(path, root)}::{name}"
            for i, given in enumerate(test.get("given") or []):
                fixtures_seen += 1
                errors.extend(_fixture_errors(where, f"given[{i}]", given))
            if "expect" in test:
                fixtures_seen += 1
                errors.extend(_fixture_errors(where, "expect", test["expect"]))

    sys.stdout.write(f"3.69: {fixtures_seen} unit-test fixture(s) checked.\n")
    return errors


def main() -> int:
    """Return 0 when every unit-test fixture declares its own types."""
    errors = check()
    for err in errors:
        sys.stderr.write(f"ERROR: {err}\n")
    if errors:
        sys.stderr.write(f"\n{len(errors)} implicitly-typed unit-test fixture(s) (3.69).\n")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
