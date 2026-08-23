#!/usr/bin/env python3
"""Assert the two projects share one strictness policy (3.22, section 5.1).

``flags:`` are read **only from the invoked project**. CI invokes dbt from
``integration_tests/``, so the package's block does not apply to the build that
actually runs the tests -- which means the project under test could be strictly
*less* strict than the one that ships, and nothing would say so.

Both blocks must therefore be identical. This compares the parsed mappings rather
than the text, so a reordering or a comment is not a failure, but any difference
in a value is.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "dbt_project.yml"
INTEGRATION = ROOT / "integration_tests" / "dbt_project.yml"


def _flags(path: Path) -> dict[str, Any]:
    if not path.is_file():
        msg = f"{path.relative_to(ROOT)} does not exist"
        raise FileNotFoundError(msg)
    loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    flags = loaded.get("flags")
    if flags is None:
        msg = (
            f"{path.relative_to(ROOT)} has no `flags:` block. Both projects must "
            f"declare one; an absent block is not the same as an identical one."
        )
        raise ValueError(msg)
    if not isinstance(flags, dict):
        msg = f"{path.relative_to(ROOT)}: `flags:` is not a mapping"
        raise TypeError(msg)
    return flags


def check() -> list[str]:
    """Return every difference between the two ``flags:`` blocks."""
    try:
        pkg = _flags(PACKAGE)
        itg = _flags(INTEGRATION)
    except (FileNotFoundError, ValueError, TypeError) as err:
        return [str(err)]

    errors: list[str] = []
    for key in sorted(set(pkg) | set(itg)):
        in_pkg, in_itg = key in pkg, key in itg
        if not in_itg:
            errors.append(f"flag `{key}` is set in the package but not in integration_tests")
        elif not in_pkg:
            errors.append(f"flag `{key}` is set in integration_tests but not in the package")
        elif pkg[key] != itg[key]:
            errors.append(
                f"flag `{key}` differs: package={pkg[key]!r} integration_tests={itg[key]!r}"
            )
    return errors


def main() -> int:
    """Return 0 when both projects declare identical flags."""
    errors = check()
    for err in errors:
        sys.stderr.write(f"ERROR: {err}\n")
    if errors:
        sys.stderr.write(
            f"\n{len(errors)} flags-parity violation(s). `flags:` are read only from "
            f"the INVOKED project, so a difference means CI tests a project that is "
            f"not the one that ships.\n"
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
