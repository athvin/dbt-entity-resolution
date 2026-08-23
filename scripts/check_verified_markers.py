#!/usr/bin/env python3
"""Demote `[VERIFIED]` markers that no longer describe the installed toolchain (3.44).

Section 0's rule: a `[VERIFIED]` marker means *observed to pass on a stated
toolchain*, and **it expires when a pin moves**. Appendix D records the failure
this prevents -- markers describing a scaffold that had been deleted stayed
`[VERIFIED]` through an entire revision, and "3.44 mechanises the demotion by
comparing section 4's pins against `uv.lock`".

Three surfaces must agree, and this compares all three:

  1. **Section 4's pin table** -- what the document says is pinned.
  2. **`uv.lock`** -- what is actually resolved and installed.
  3. **The verified-against block** -- the toolchain the markers were earned on.

A `[VERIFIED]` row whose tool is absent from the verified-against block is the
subtle case and the most common one: the marker names no toolchain at all, so
nothing can ever expire it. That is a marker with no scope, and section 0 makes
it `[UNVERIFIED]` by construction.
"""

from __future__ import annotations

import re
import sys
import tomllib
from typing import TYPE_CHECKING

import yaml

from _er_paths import ROOT, rel

if TYPE_CHECKING:
    from pathlib import Path

# Tools whose "version" is the repository itself; they have no lock entry and
# their marker is scoped by the commit, not by a pin.
_SELF_HOSTED = frozenset({"In-repo enforcement scripts", "GitHub Actions workflows"})

# Section 4 writes display names; uv.lock writes distribution names.
_LOCK_NAME = {
    "dbt-core": "dbt-core",
    "dbt-duckdb": "dbt-duckdb",
    "duckdb": "duckdb",
    "splink": "splink",
    "sqlglot": "sqlglot",
    "SQLFluff + `sqlfluff-templater-dbt`": "sqlfluff",
    "dbt-bouncer": "dbt-bouncer",
    "yamllint": "yamllint",
    "ruff": "ruff",
    "mypy": "mypy",
}

# Display name -> dbt package name in `package-lock.yml`.
_DBT_PACKAGE = {
    "dbt_utils": "dbt_utils",
    "dbt_project_evaluator": "dbt_project_evaluator",
}

# The status cell is backticked in the document: | B | `[VERIFIED]` |
_ROW = re.compile(r"^\|\s*([^|]+?)\s*\|\s*([^|]*?)\s*\|.*\|\s*`?(\[(?:UN)?VERIFIED\][^|]*)\|\s*$")
_EXACT_PIN = re.compile(r"`==([0-9][^`]*)`")
_VERIFIED = "[VERIFIED]"

# "**The scope of the verified markers.** Everything above was executed on
#  dbt-core 1.12.2 · dbt-duckdb 1.11.0 · DuckDB 1.5.5 · ..."
#
# Anchored on the block's own heading, not on "executed on": the phrase
# "re-executed on the §4 pins" appears three lines earlier and matched first,
# yielding an empty toolchain that read as "no block found".
_SCOPE_BLOCK = re.compile(
    r"\*\*The scope of the verified markers\.\*\*\s*(.+?)(?:\n\n|Per §0)", re.DOTALL
)
_SCOPE_ENTRY = re.compile(r"([A-Za-z][A-Za-z0-9_.+-]*)\s+([0-9]+(?:\.[0-9]+)+)")


def _section4_rows(text: str) -> list[tuple[str, str, str]]:
    """Return `(tool, pin_cell, status_cell)` for section 4's pin table."""
    start = text.find("\n## 4. Tool stack and pins")
    end = text.find("\n## 5.", start + 1) if start != -1 else -1
    if start == -1 or end == -1:
        return []
    rows: list[tuple[str, str, str]] = []
    for line in text[start:end].splitlines():
        stripped = line.strip()
        if not stripped.startswith("|") or stripped.startswith("|---") or ">" in stripped[:2]:
            continue
        match = _ROW.match(stripped)
        if not match:
            continue
        tool = match.group(1).strip()
        if tool in {"Tool", ""}:
            continue
        rows.append((tool, match.group(2).strip(), match.group(3).strip()))
    return rows


def _verified_against(text: str) -> dict[str, str]:
    """Parse the toolchain the `[VERIFIED]` markers were earned on."""
    match = _SCOPE_BLOCK.search(text)
    if not match:
        return {}
    return {name.lower(): version for name, version in _SCOPE_ENTRY.findall(match.group(1))}


def _lock_versions(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    return {
        pkg["name"]: pkg["version"]
        for pkg in data.get("package", [])
        if "name" in pkg and "version" in pkg
    }


def _dbt_package_versions(root: Path) -> dict[str, str]:
    """Return resolved dbt package versions from every `package-lock.yml`.

    dbt packages are not Python distributions and never appear in `uv.lock`.
    Without this, `dbt_utils` could move from 1.4.1 to 1.9.0 and its
    `[VERIFIED]` marker would survive -- the precise failure 3.44 exists to
    prevent, one packaging system to the left.
    """
    versions: dict[str, str] = {}
    for lock in root.rglob("package-lock.yml"):
        if "dbt_packages" in lock.parts:
            continue
        loaded = yaml.safe_load(lock.read_text(encoding="utf-8")) or {}
        for pkg in loaded.get("packages", []):
            if isinstance(pkg, dict) and pkg.get("name") and pkg.get("version"):
                versions[str(pkg["name"])] = str(pkg["version"])
    return versions


def _check_pin(
    tool: str, dist: str | None, pin_cell: str, installed: dict[str, str]
) -> tuple[list[str], int]:
    """Surface 1 vs 2: what section 4 says is pinned vs what is locked."""
    exact = _EXACT_PIN.search(pin_cell)
    if not dist or not exact:
        return [], 0
    resolved = installed.get(dist)
    if resolved is None:
        return (
            [f"{tool}: section 4 pins `=={exact.group(1)}` but {dist} is not in uv.lock."],
            0,
        )
    if resolved != exact.group(1):
        return (
            [
                (
                    f"{tool}: section 4 pins `=={exact.group(1)}` but uv.lock resolves "
                    f"{dist} {resolved}. The pin and the lock disagree."
                )
            ],
            0,
        )
    return [], 1


def _check_marker(
    tool: str, dist: str | None, scope: dict[str, str], installed: dict[str, str]
) -> tuple[list[str], int]:
    """Surface 3: the toolchain a `[VERIFIED]` marker was earned on."""
    earned = scope.get(tool.lower()) or (scope.get(dist.lower()) if dist else None)
    if earned is None:
        return (
            [
                (
                    f"{tool}: marked {_VERIFIED} but the verified-against block does not "
                    f"name it. A marker with no stated toolchain has no scope and can "
                    f"never expire (section 0). Demote it, or add it to the block with "
                    f"the version it was executed on."
                )
            ],
            0,
        )
    resolved = installed.get(dist) if dist else None
    if resolved is not None and resolved != earned:
        return (
            [
                (
                    f"{tool}: marked {_VERIFIED} against {earned}, but uv.lock now resolves "
                    f"{resolved}. The pin moved; per section 0 and 3.44 this marker is "
                    f"DEMOTED to [UNVERIFIED] until re-executed."
                )
            ],
            0,
        )
    return [], 1


def check(root: Path = ROOT) -> list[str]:
    """Return every `[VERIFIED]` marker that no longer holds."""
    doc = root / "docs" / "DbtBestPractices.md"
    lock = root / "uv.lock"
    if not doc.is_file():
        return [f"{rel(doc, root)} does not exist."]
    if not lock.is_file():
        return [f"{rel(lock, root)} does not exist -- 3.44 has nothing to compare pins against."]

    text = doc.read_text(encoding="utf-8")
    rows = _section4_rows(text)
    scope = _verified_against(text)
    installed = _lock_versions(lock)
    installed.update(_dbt_package_versions(root))

    if not rows:
        return ["parsed no rows from section 4's pin table -- the parser has broken."]
    if not scope:
        return [
            (
                "found no verified-against block. Section 0 scopes every `[VERIFIED]` "
                "marker to a stated toolchain; without the block, no marker has a scope "
                "and none can expire."
            )
        ]

    errors: list[str] = []
    checked = 0

    for tool, pin_cell, status in rows:
        dist = _LOCK_NAME.get(tool) or _DBT_PACKAGE.get(tool)
        pin_errors, pin_ok = _check_pin(tool, dist, pin_cell, installed)
        errors.extend(pin_errors)
        checked += pin_ok

        if not status.startswith(_VERIFIED) or tool in _SELF_HOSTED:
            continue
        marker_errors, marker_ok = _check_marker(tool, dist, scope, installed)
        errors.extend(marker_errors)
        checked += marker_ok

    sys.stdout.write(
        f"3.44: {len(rows)} section 4 row(s), {len(scope)} tool(s) in the "
        f"verified-against block, {checked} marker/pin agreement(s).\n"
    )
    return errors


def main() -> int:
    """Return 0 when every `[VERIFIED]` marker still describes the toolchain."""
    errors = check()
    for err in errors:
        sys.stderr.write(f"ERROR: {err}\n")
    if errors:
        sys.stderr.write(f"\n{len(errors)} marker(s) to demote or reconcile (3.44).\n")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
