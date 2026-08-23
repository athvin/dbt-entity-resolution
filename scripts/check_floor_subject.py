#!/usr/bin/env python3
"""The floors must measure the PACKAGE once the package produces scores (3.86).

3.85 makes the quality floors block. This makes them keep meaning what they say.

Today `scripts/measure_quality.py` reads `fixtures/baselines/fake_1000/`, which
is **Splink's** output. That is correct right now and it is the only honest
option: Stages 3, 4 and 5 do not exist, so the package produces no scores of its
own, and there is nothing else to measure. §1.8's floors are satisfied by the
oracle because the oracle is all there is.

The danger is not that arrangement. It is that the arrangement **survives**.

The day Stage 5 lands, `make ci` stays green, the floors stay green, and three
committed numbers quietly go on asserting *"Splink is a good entity resolver"* --
a proposition nobody doubts and nothing in this repository is for. Nothing about
that day announces itself: no test fails, no artefact changes, and the defect is
invisible precisely because every gate is passing. It is Appendix D.0's recurring
shape (findings 20, 64, 69, 71, 73) with a delay fuse.

**The tripwire is keyed on a COLUMN NAME, not a file path.** An earlier design
keyed it on the eventual model's path, which fails in both directions: it fires
early if the guess is wrong, and never fires if Stage 5 names its model something
else, splits it in two, or lands under a different directory. `match_probability`
is fixed by Splink, is the column every threshold comparison uses (B2), and a
contract-enforced model cannot emit it without declaring it. So the question this
check asks -- *does any model in this package declare a `match_probability`
column?* -- is immune to renaming, moving, splitting and reordering.

When the answer is **no**, this exits 0 having asserted nothing about quality.
That is a vacuous pass by construction, which is exactly why 3.86 ships with a
`verify_gates.py` injection that creates the declaration in a scratch copy and
asserts the red: per §0, *observed to pass* is never *observed to fail when
violated*.
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

from _er_paths import ROOT, rel

# Splink's own name for the scored column, and the one B2 makes every threshold
# comparison read. A model that produces scores must declare it.
SCORE_COLUMN = "match_probability"

# Where the floors currently take their numbers from, asked SEMANTICALLY.
#
# The first version of this check grepped `measure_quality.py` for the literal
# string "fixtures/baselines" -- which that file spells `ROOT / "fixtures" /
# "baselines"`, as separate path components. The substring never matched, so the
# check could not fire under any circumstances. It was the exact defect it
# exists to prevent, inside the gate meant to prevent it, and only the failing
# case caught it (D.0 finding 75).
#
# Resolving the module's own constant cannot drift from what the module does,
# because it IS what the module does.
ORACLE_DIR = "baselines"
MEASUREMENT = "scripts/measure_quality.py"

SEARCH_ROOTS = ("models", "integration_tests/models")


def _declaring_models(root: Path) -> list[str]:
    """Every properties file declaring a column named `match_probability`."""
    found: list[str] = []
    for base in SEARCH_ROOTS:
        for path in sorted((root / base).rglob("*.yml")):
            try:
                loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            except yaml.YAMLError:
                # Malformed YAML is yamllint's job (§11.4), not this check's --
                # and swallowing it here would let a parse error read as
                # "no scored model exists".
                continue
            found.extend(
                f"{rel(path, root)} ({entry.get('name')})"
                for entry in loaded.get("models") or []
                if isinstance(entry, dict)
                for column in entry.get("columns") or []
                if isinstance(column, dict) and column.get("name") == SCORE_COLUMN
            )
    return found


def check(root: Path = ROOT) -> list[str]:
    """Return an error once the package scores its own pairs but floors do not."""
    declaring = _declaring_models(root)

    import measure_quality  # noqa: PLC0415 -- imported for its resolved constant

    measures_oracle = ORACLE_DIR in Path(measure_quality.BASELINES).parts

    if declaring and measures_oracle:
        return [
            (
                f"ER-086: {', '.join(declaring)} declares a `{SCORE_COLUMN}` column, so "
                f"this package now produces its own scores -- but the quality floors "
                f"still measure the ORACLE ({MEASUREMENT} reads "
                f"{Path(measure_quality.BASELINES).name}/ under fixtures/{ORACLE_DIR}/).\n"
                f"  Until they are re-pointed at the package's own output, "
                f"`er_blocking_recall_floor`, `er_f1_floor` and `er_max_cluster_size` "
                f"are asserting that SPLINK is a good entity resolver -- which nothing "
                f"in this repository is for (§1.8, M12).\n"
                f"  Nothing else will tell you: every gate stays green, because a "
                f"floor measured against the oracle passes exactly as well as it did "
                f"yesterday."
            )
        ]

    state = (
        "the package scores its own pairs and the floors measure it"
        if declaring
        else "no model declares a score yet; the floors measure the oracle by design"
    )
    sys.stdout.write(f"3.86: {len(declaring)} scored-model declaration(s) -- {state}.\n")
    return []


def main() -> int:
    """Return 0 while the floors still measure the right thing."""
    errors = check()
    for err in errors:
        sys.stderr.write(f"ERROR: {err}\n")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
