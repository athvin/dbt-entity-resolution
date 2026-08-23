#!/usr/bin/env python3
"""The floors must measure the PACKAGE once the package produces scores (3.86).

`harness/test_quality_floors.py` makes the quality floors block. This makes them
keep meaning what they say.

Today `scripts/measure_quality.py` reads a frozen baseline directory, which holds
**Splink's** output. That is correct right now and it is the only honest option
for the scored metrics: Stage 5 does not exist, so the package computes no
`match_probability` of its own, and §1.8's floors are satisfied by the oracle
because for those numbers the oracle is all there is.

The danger is not that arrangement. It is that the arrangement **survives**.

The day Stage 5 lands, `make ci` stays green, the floors stay green, and three
committed numbers quietly go on asserting *"Splink is a good entity resolver"* --
a proposition nobody doubts and nothing here is for. Nothing about that day
announces itself: no test fails, no artefact changes, and the defect is invisible
precisely because every gate passes. It is Appendix D.0's recurring shape
(20, 64, 69, 71, 73) with a delay fuse.

**Both halves of the predicate are asked semantically, and that took two goes.**
The question is *"does the package declare a score AND do the floors still read
a frozen artefact?"* -- and each early version answered one half by
pattern-matching a string:

* v1 grepped `measure_quality.py` for the literal `"fixtures/baselines"`, which
  that file spells as separate path components. It never matched, so the check
  could not fire under any input (D.0 finding 76).
* v2 fixed that half by resolving the module's constant, then keyed the ORACLE
  half on the literal directory name `"baselines"` -- the exact mistake the
  docstring had just rejected for the model half. Renaming the directory to
  `fixtures/frozen/` silently disarmed the tripwire (D.0 finding 79).

The oracle half is now *"does the measurement read a committed file in this
repository rather than a table the package built?"*, answered from the resolved
path, which no rename can break. And the model half walks dbt's real column
shapes -- `versions:` included, which a scored model is exactly the kind of thing
to acquire -- rather than the one shape that occurred to me first.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import yaml

from _er_paths import ROOT, rel

# Splink's own name for the scored column, and the one B2 makes every threshold
# comparison read. A contract-enforced model cannot emit it without declaring it.
SCORE_COLUMN = "match_probability"

MEASUREMENT = "scripts/measure_quality.py"

# Both dbt properties extensions, and both project roots. v2 scanned `*.yml`
# only, so the same file saved as `.yaml` -- which dbt accepts -- was invisible.
SEARCH_ROOTS = ("models", "integration_tests/models")
SUFFIXES = ("*.yml", "*.yaml")


def _columns_of(entry: dict[str, Any]) -> list[Any]:
    """Return every column a model entry declares, `versions:` included.

    dbt lets a versioned model carry its columns inside `versions:` rather than
    at the entry's top level (`dbt/contracts/graph/unparsed.py`,
    `UnparsedVersion`). A scored model is precisely the kind that acquires a v2,
    so reading only the top level misses the realistic case, not an exotic one.
    """
    columns = list(entry.get("columns") or [])
    for version in entry.get("versions") or []:
        if isinstance(version, dict):
            columns.extend(version.get("columns") or [])
    return columns


def _declaring_models(root: Path) -> tuple[list[str], list[str]]:
    """Return `(models declaring a score, files that could not be read)`.

    Unreadable files are REPORTED rather than skipped. v2 swallowed `YAMLError`
    on the reasoning that malformed YAML is yamllint's job -- true, but it makes
    a file this check cannot parse indistinguishable from one declaring nothing,
    and that is the answer which keeps the tripwire quiet.
    """
    found: list[str] = []
    unreadable: list[str] = []
    for base in SEARCH_ROOTS:
        directory = root / base
        if not directory.is_dir():
            continue
        for suffix in SUFFIXES:
            for path in sorted(directory.rglob(suffix)):
                try:
                    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
                except (yaml.YAMLError, OSError):
                    unreadable.append(rel(path, root))
                    continue
                # A top level that is not a mapping is not a dbt properties
                # file -- and v2 raised AttributeError on exactly that.
                if not isinstance(loaded, dict):
                    continue
                found.extend(
                    f"{rel(path, root)} ({entry.get('name')})"
                    for entry in loaded.get("models") or []
                    if isinstance(entry, dict)
                    if any(
                        isinstance(column, dict) and column.get("name") == SCORE_COLUMN
                        for column in _columns_of(entry)
                    )
                )
    return found, unreadable


def _measures_a_frozen_artefact(root: Path) -> bool:
    """Report whether the measurement reads a committed file, not the warehouse.

    Asked from the resolved path's relationship to the repository, so renaming
    the baseline directory cannot disarm the tripwire (D.0 finding 79). What
    matters is not what the directory is called -- it is that the numbers come
    from a file in the repo rather than from a table the package built.
    """
    import measure_quality  # noqa: PLC0415 -- imported for its resolved constant

    source = Path(measure_quality.BASELINES).resolve()
    try:
        source.relative_to(root.resolve())
    except ValueError:
        return False
    return source.is_dir()


def check(root: Path = ROOT) -> list[str]:
    """Return an error once the package scores its own pairs but floors do not."""
    declaring, unreadable = _declaring_models(root)

    errors: list[str] = []
    if unreadable:
        errors.append(
            f"could not parse {', '.join(unreadable)}, so this check cannot tell "
            f"whether the package declares a `{SCORE_COLUMN}` column. An "
            f"unreadable properties file must not read as 'declares nothing' -- "
            f"that is the answer which keeps a tripwire quiet."
        )

    if declaring and _measures_a_frozen_artefact(root):
        errors.append(
            f"ER-086: {', '.join(declaring)} declares a `{SCORE_COLUMN}` column, "
            f"so this package now produces its own scores -- but the quality "
            f"floors still measure a frozen artefact ({MEASUREMENT}).\n"
            f"  Until they are re-pointed at the package's own output, "
            f"`er_blocking_recall_floor`, `er_f1_floor` and `er_max_cluster_size` "
            f"are asserting that SPLINK is a good entity resolver -- which nothing "
            f"in this repository is for (§1.8, M12).\n"
            f"  Nothing else will tell you: every gate stays green, because a "
            f"floor measured against the oracle passes exactly as well as it did "
            f"yesterday."
        )

    if not errors:
        state = (
            "the package scores its own pairs and the floors measure it"
            if declaring
            else "no model declares a score yet; the floors measure the oracle by design"
        )
        sys.stdout.write(f"3.86: {len(declaring)} scored-model declaration(s) -- {state}.\n")
    return errors


def main() -> int:
    """Return 0 while the floors still measure the right thing."""
    errors = check()
    for err in errors:
        sys.stderr.write(f"ERROR: {err}\n")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
