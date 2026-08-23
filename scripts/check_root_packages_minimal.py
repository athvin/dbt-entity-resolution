#!/usr/bin/env python3
"""Assert the shipped dependency surface stays minimal (3.29, C.2).

Everything in the root ``packages.yml`` is **force-installed into every consumer
project**, because dbt resolves package dependencies transitively. A development
convenience added here becomes a dependency of every downstream build, and the
person who pays for it never sees the commit.

The allowed set is deliberately one entry. Development packages belong in
``integration_tests/packages.yml``, which no consumer installs.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
PACKAGES = ROOT / "packages.yml"

# C.2: dbt_utils ONLY. Section 4 calls it "the only shipped dependency".
ALLOWED: frozenset[str] = frozenset({"dbt-labs/dbt_utils"})


def _entries(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        msg = f"{path.name} does not exist"
        raise FileNotFoundError(msg)
    loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    packages = loaded.get("packages") or []
    if not isinstance(packages, list):
        msg = f"{path.name}: `packages:` is not a list"
        raise TypeError(msg)
    return [p for p in packages if isinstance(p, dict)]


def check() -> list[str]:
    """Return every violation of the minimal shipped surface."""
    try:
        entries = _entries(PACKAGES)
    except (FileNotFoundError, TypeError) as err:
        return [str(err)]

    errors: list[str] = []
    for entry in entries:
        if "local" in entry:
            errors.append(
                f"`local: {entry['local']}` in the ROOT packages.yml. A local path "
                f"cannot resolve in a consumer's project; it belongs in "
                f"integration_tests/packages.yml."
            )
            continue
        if "git" in entry:
            errors.append(
                f"`git: {entry['git']}` in the ROOT packages.yml. A git dependency is "
                f"force-installed into every consumer; it belongs in "
                f"integration_tests/packages.yml unless it is a shipped dependency."
            )
            continue
        name = entry.get("package")
        if name is None:
            errors.append(f"packages.yml entry has no `package:` key: {entry!r}")
        elif name not in ALLOWED:
            errors.append(
                f"`{name}` is not in the allowed shipped surface {sorted(ALLOWED)}. "
                f"Everything here is force-installed into every consumer project "
                f"(3.29, C.2). Development packages belong in "
                f"integration_tests/packages.yml."
            )
    return errors


def main() -> int:
    """Return 0 when the shipped dependency surface is minimal."""
    errors = check()
    for err in errors:
        sys.stderr.write(f"ERROR: {err}\n")
    if errors:
        sys.stderr.write(f"\n{len(errors)} shipped-dependency violation(s).\n")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
