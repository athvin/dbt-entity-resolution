#!/usr/bin/env python3
"""The CI workflow and the Makefile must agree (3.83, §17, D.0 finding 69).

§17's promise is *"every Make target is also a CI step"*, and C.7 writes those
steps out explicitly rather than invoking the targets. Two copies of one logic,
and **they drifted the moment one changed**: fixing `make lint` to parse the
runnable project left the workflow parsing the package root, so `make ci` was
exit 0 locally while CI went red on exactly the thing just fixed.

That is worse than either copy being wrong on its own. A green local run is
*evidence* under §17's promise — so when the promise fails, the local result is
not merely incomplete, it is **misleading**.

**What this checks, and what it deliberately does not.** Full equivalence
between a Makefile and a workflow is neither achievable nor useful: CI has
setup steps, caching and matrix concerns a Makefile should not carry. What is
checked is narrower and is where the drift actually happened -- the handful of
**drift-prone commands** that appear in both, which must agree on the arguments
that decide *what they look at*:

  * which project directory they run against
  * which paths they are pointed at

Those are the two things that were wrong, and both failed silently rather than
erroring.
"""

from __future__ import annotations

import re
import sys
from typing import TYPE_CHECKING

from _er_paths import ROOT, rel

if TYPE_CHECKING:
    from pathlib import Path

WORKFLOW = ".github/workflows/ci.yml"
MAKEFILE = "Makefile"

# Commands whose *target* is the thing that drifts. Each is a (label, pattern)
# where the pattern captures everything after the verb.
# `(label, pattern, takes_a_path)`. The last field matters: `dbt parse` and
# `run-operation` take no path argument, so looking for one absorbs whatever
# path happens to appear on a later line -- which is exactly the false positive
# the first version of this check produced against itself.
DRIFT_PRONE = (
    ("dbt parse", re.compile(r"\bdbt parse\b(.*)"), False),
    ("sqlfluff lint", re.compile(r"\bsqlfluff lint\b(.*)"), True),
    (
        "run-operation er_assert_project_standards",
        re.compile(r"\brun-operation er_assert_project_standards\b(.*)"),
        False,
    ),
)

# EVERY command in `DRIFT_PRONE` must be found in BOTH files. The list is
# curated: a command earns a place on it by actually appearing in both, so one
# going missing means the extractor broke or a step was silently dropped --
# never that the repository got simpler.
#
# A weaker floor was tried first and its own test caught it. With
# `MIN_COMMANDS_COMPARED = 2`, renaming one of three commands still "passed":
# the guard against comparing NOTHING did not guard against comparing LESS.
# That is §6.1's vacuous pass wearing a threshold, and a threshold set below
# full coverage is indistinguishable from no threshold on the day it matters.
MIN_COMMANDS_COMPARED = len(DRIFT_PRONE)

# The ENVIRONMENT is the other half, and it took a third divergence to add it.
#
# 3.83 shipped comparing the drift-prone COMMANDS. Stage 4 then introduced
# `DBT_ER_COMPARISONS`, exported by the Makefile and absent from the workflow --
# so `make ci` was exit 0 and CI failed three jobs on `ER-071`, which is finding
# 69's shape exactly, in a channel this check was not built to look at (D.0
# finding 89). A parity check scoped to one kind of drift finds one kind of
# drift.
#
# Every `DBT_ER_*` the Makefile exports must also reach the workflow. The
# converse is deliberately NOT required: CI legitimately sets variables a local
# run has no use for.
_MAKE_EXPORT = re.compile(r"^export\s+(DBT_ER_\w+)\s*\??=", re.MULTILINE)
_WORKFLOW_ENV = re.compile(r"^\s{2,}(DBT_ER_\w+)\s*:", re.MULTILINE)

# `Makefile` writes these as variables; the workflow writes them out.
MAKE_VARIABLES = {
    "$(DBT)": "uv run dbt",
    "$(IT)": "--project-dir integration_tests",
    "$$paths": "integration_tests/dbt_packages/dbt_er/models",
}


# What is compared, and NOTHING else. CI legitimately adds flags a Makefile
# should not carry -- `--format github-annotation-native` exists so failures
# surface as GitHub annotations, and demanding the two match on that would make
# this check noise. What must agree is WHERE each command looks:
#
#   * `--project-dir <x>` -- which project it runs against
#   * the first bare path  -- what it is pointed at
#
# Both were wrong in finding 69, and both failed silently rather than erroring.
_PROJECT_DIR = re.compile(r"--project-dir\s+(\S+)")
_PATH = re.compile(r"(?<![\w-])((?:[\w.]+/)+[\w./]+)")


def _normalise(text: str) -> str:
    """Strip comments and fold continuations, so matching sees one line per command.

    Both were needed, and both produced FALSE POSITIVES before they were:

    * the Makefile's own comment *"section 17 writes this as `sqlfluff lint
      models tests`"* matched as if it were an invocation;
    * the workflow's `>-` folded block puts `--project-dir integration_tests` on
      a continuation line, so a newline-bounded match saw no arguments at all
      and reported the compile gate as running at the package root.

    A parity check that reports divergences which are not there is worse than
    none: it trains people to skip the output.
    """
    lines: list[str] = []
    for raw in text.splitlines():
        line = raw
        # A `#` inside quotes is not a comment, but neither file has one and
        # guessing would add more failure modes than it removes.
        if "#" in line:
            line = line[: line.index("#")]
        lines.append(line.rstrip("\\").strip())
    return " ".join(part for part in lines if part)


def _expand(text: str) -> str:
    for name, value in MAKE_VARIABLES.items():
        text = text.replace(name, value)
    return _normalise(text)


def _targets(text: str, pattern: re.Pattern[str], *, takes_path: bool) -> set[tuple[str, str]]:
    """`{(project_dir, path)}` for every occurrence -- the two facets that drift."""
    found: set[tuple[str, str]] = set()
    for match in pattern.finditer(_expand(text)):
        # Bound the tail so the NEXT command's arguments are not absorbed.
        tail = match.group(1).split(" uv run ")[0][:200]
        project = _PROJECT_DIR.search(tail)
        path = _PATH.search(tail)
        found.add(
            (
                project.group(1) if project else "<package root>",
                (path.group(1) if path else "<none>") if takes_path else "<n/a>",
            )
        )
    return found


def check(root: Path = ROOT) -> list[str]:
    """Return every drift-prone command whose arguments disagree."""
    workflow = root / WORKFLOW
    makefile = root / MAKEFILE
    for path in (workflow, makefile):
        if not path.is_file():
            return [f"{rel(path, root)} does not exist -- 3.83 has nothing to compare."]

    workflow_text = workflow.read_text(encoding="utf-8")
    makefile_text = makefile.read_text(encoding="utf-8")

    errors: list[str] = []
    compared = 0
    missing: list[str] = []

    for label, pattern, takes_path in DRIFT_PRONE:
        in_workflow = _targets(workflow_text, pattern, takes_path=takes_path)
        in_makefile = _targets(makefile_text, pattern, takes_path=takes_path)
        if not in_workflow or not in_makefile:
            absent = WORKFLOW if not in_workflow else MAKEFILE
            missing.append(f"`{label}` (absent from {absent})")
            continue
        compared += 1
        if in_workflow != in_makefile:
            errors.append(
                f"`{label}` disagrees between {WORKFLOW} and {MAKEFILE}:\n"
                f"    workflow: {sorted(in_workflow)}\n"
                f"    Makefile: {sorted(in_makefile)}\n"
                f"  §17 promises every Make target is also a CI step. When they "
                f"differ, a green `make ci` is misleading rather than merely "
                f"incomplete (D.0 finding 69)."
            )

    exported = set(_MAKE_EXPORT.findall(makefile_text))
    in_workflow_env = set(_WORKFLOW_ENV.findall(workflow_text))
    errors.extend(
        f"`{name}` is exported by {MAKEFILE} and never set in {WORKFLOW}.\n"
        f"  Every job that needs it will see the package default instead, which "
        f"for these vars is an empty value that raises rather than builds -- so "
        f"`make ci` passes locally and CI fails somewhere that does not name the "
        f"missing variable (D.0 finding 89)."
        for name in sorted(exported - in_workflow_env)
    )

    if compared < MIN_COMMANDS_COMPARED:
        errors.append(
            f"only {compared} of {MIN_COMMANDS_COMPARED} drift-prone commands were "
            f"compared: {', '.join(missing)}.\n"
            f"  The extractor has broken, or a step was dropped from one file. "
            f"Either way 3.83 is no longer checking what it claims to -- a parity "
            f"check that compares less passes more (§6.1's vacuous pass).\n"
            f"  If the command was retired deliberately, remove it from "
            f"DRIFT_PRONE in the same PR, so the narrowing is reviewed."
        )

    sys.stdout.write(
        f"3.83: {compared} shared command(s) and {len(exported)} exported variable(s) compared.\n"
    )
    return errors


def main() -> int:
    """Return 0 when the workflow and the Makefile agree."""
    errors = check()
    for err in errors:
        sys.stderr.write(f"ERROR: {err}\n")
    if errors:
        sys.stderr.write(f"\n{len(errors)} CI/Makefile divergence(s) (3.83).\n")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
