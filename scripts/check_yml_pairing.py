#!/usr/bin/env python3
"""Enforce the strict 1:1 colocated properties-file convention (3.1, 3.2).

Section 6 explains why this exists: no released tool enforces it. dbt does not
care where a properties file lives, so a folder-level ``schema.yml`` is legal and
turns "which file documents this model?" into a search.

This is the v1 script with section 6.1's two blind spots CLOSED:

1. **The seed clause had no subject.** ``seeds/`` lives in ``integration_tests/``,
   which the original ``SKIP_PARTS`` excluded -- so the seed half of 3.1 walked an
   empty set and passed. A check whose subject has disappeared is the worst kind,
   because it reports success.
2. **``SKIP_PARTS`` hid ``integration_tests/`` entirely**, while 3.1 says the rule
   "covers **both** project roots". The original walked one.

Both are fixed by walking an explicit list of roots and asserting that the roots
themselves exist -- so the script fails loudly when it has nothing to check,
rather than quietly.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator

ROOT = Path(__file__).resolve().parents[1]

# 3.1: "Covers BOTH project roots." The package and the runnable project.
PROJECT_ROOTS = (ROOT, ROOT / "integration_tests")

SQL_DIRS = ("models", "macros", "tests")
SEED_DIRS = ("seeds",)
# `integration_tests` is deliberately NOT here any more -- it is a root, not a
# thing to skip. What remains is generated output, which has no properties files
# and should never be linted.
SKIP_PARTS = {"target", "dbt_packages", "logs", ".venv", "__pycache__"}
NON_RESOURCE_PREFIX = "_"


def _walk(base: Path, suffix: str) -> Iterator[Path]:
    """Yield files under ``base`` with ``suffix``, skipping generated trees."""
    for path in sorted(base.rglob(f"*{suffix}")):
        if SKIP_PARTS & set(path.parts):
            continue
        yield path


def _rel(path: Path) -> str:
    """Render ``path`` relative to the repository root, for an actionable message."""
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def _check_dir(base: Path, partner: str) -> list[str]:
    """Return every pairing violation inside one resource directory."""
    errors: list[str] = []

    errors.extend(
        f"missing properties file: {_rel(src.with_suffix('.yml'))} (required by {_rel(src)})"
        for src in _walk(base, partner)
        if not src.with_suffix(".yml").exists()
    )
    errors.extend(
        f"orphan properties file: {_rel(yml)} (no sibling {partner}). Rename it with "
        f"a leading '_' if it defines sources, groups, exposures, unit tests or "
        f"singular-test properties."
        for yml in _walk(base, ".yml")
        if not yml.name.startswith(NON_RESOURCE_PREFIX) and not yml.with_suffix(partner).exists()
    )
    errors.extend(f"use .yml, not .yaml: {_rel(bad)}" for bad in _walk(base, ".yaml"))
    return errors


def check(roots: tuple[Path, ...] = PROJECT_ROOTS) -> list[str]:
    """Return every pairing violation found under ``roots``.

    Returns an empty list when the convention holds everywhere.
    """
    errors: list[str] = []
    checked_any = False

    for project in roots:
        if not project.is_dir():
            # A root named in the contract but absent is itself a finding: the
            # alternative is walking nothing and reporting success.
            errors.append(f"project root does not exist: {_rel(project)}")
            continue

        for dirname in SQL_DIRS + SEED_DIRS:
            base = project / dirname
            if not base.is_dir():
                continue
            checked_any = True
            errors.extend(_check_dir(base, ".csv" if dirname in SEED_DIRS else ".sql"))

    if not checked_any:
        errors.append(
            "no resource directory was found under any project root -- this check "
            "had nothing to check. Section 6.1 calls a check whose subject has "
            "disappeared the worst kind, because it reports success."
        )

    return errors


def main() -> int:
    """Return 0 when every resource is correctly paired, 1 otherwise."""
    errors = check()
    for err in errors:
        sys.stderr.write(f"ERROR: {err}\n")
    if errors:
        sys.stderr.write(f"\n{len(errors)} YAML-pairing violation(s).\n")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
