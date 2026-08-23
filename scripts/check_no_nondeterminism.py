#!/usr/bin/env python3
"""Ban non-deterministic Jinja from model and macro code (3.16, section 11.3).

The one check nothing else does. SQLFluff sees *rendered* SQL, so it cannot see
that the Jinja which produced it iterates a ``set()``; dbt sees the manifest, not
the source text. This reads the source.

Two tiers, and the split is the point:

**Tier 1 -- banned everywhere, no exemptions.** Randomised iteration order makes
output order a function of the interpreter rather than of the data. Section A.1
M15 measures the consequence: ``set()`` iteration order randomised per process
would fail Stage 1's rendered-SQL snapshots roughly 80% of the time.

**Tier 2 -- run-context values.** Legitimate, and necessary, inside a hook macro
whose whole job is recording what a run did. Fatal in anything that becomes a
model's compiled SQL, because they change the SQL *text* between two runs of
identical input -- which is what section 6.3's content-stability claim rests on.

A single blunt ban would have forced the observability macros to be exempted
wholesale, taking the ``set()`` ban with them. Hence two tiers, and hence
``EXEMPT_RUN_CONTEXT`` being a Tier 2 escape only.

Per section 11.3 and RC42, THIS FILE IS THE AUTHORITATIVE COPY of these
constants; the document keeps the rationale and one illustrative entry per tier.
A stale allowlist quoted in a document is worse than none, because it tells a
reviewer an exemption exists with the confidence of a verified block.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator

ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOTS = (ROOT, ROOT / "integration_tests")
SCAN_DIRS = ("models", "macros", "tests", "analyses")
SKIP_PARTS = {"target", "dbt_packages", "logs", ".venv", "__pycache__"}

# Tier 1: banned EVERYWHERE, no exemptions.
ALWAYS_BANNED: dict[str, str] = {
    r"\bset_strict\s*\(": (
        "set_strict() iteration order is randomised per process; output order "
        "becomes a function of the interpreter, not the data."
    ),
    r"(?<![\w.])set\s*\(": (
        "set() iteration order is randomised per process. Use a sorted list, or "
        "dbt_utils' ordered helpers; M15 measures an ~80% snapshot failure rate."
    ),
}

# Tier 2: run-context values. Fine in a hook macro, fatal in compiled SQL.
RUN_CONTEXT_BANNED: dict[str, str] = {
    r"\binvocation_id\b": "invocation_id changes every run and must never reach compiled SQL.",
    r"\brun_started_at\b": "run_started_at is a wall clock and must never reach compiled SQL.",
    r"\bthread_id\b": "thread_id is scheduling-dependent, so it is not even stable within a run.",
    r"\benv_var\s*\(": (
        "env_var() in model code makes compiled SQL host-dependent. The model JSON "
        "is the one exception and it is loaded through the exempt macro below."
    ),
}

# Files permitted to read run-context values: macros that run ONLY from a hook,
# plus the model-JSON loader. Adding to this set is a reviewable act -- it is the
# only way to defeat Tier 2.
#
# Entries are listed relative to a project root. None of these macros exists yet;
# they arrive with the observability layer and Stage 1. An entry naming a file
# that does not exist is reported (see `stale exemption` below) rather than left
# to rot, because a stale exemption is indistinguishable from a live one.
EXEMPT_RUN_CONTEXT: frozenset[str] = frozenset(
    {
        "macros/model_json/er_load_model_json.sql",
        "macros/observability/er_obs_enabled.sql",
        "macros/observability/er_obs_relation.sql",
        "macros/observability/er_obs_bootstrap.sql",
        "macros/observability/er_obs_on_run_start.sql",
        "macros/observability/er_obs_capture_query_metrics.sql",
        "macros/observability/er_obs_log_run_results.sql",
    }
)

_COMMENT_RE = re.compile(r"\{#.*?#\}", re.DOTALL)


def _rel(path: Path) -> str:
    """Render ``path`` relative to the repository root, or absolute if outside it.

    A scanned tree is not always inside ROOT -- the failing-case tests build one
    in a temp directory, which is the point of 3.57. `relative_to` raises there,
    so a message-formatting helper must not assume containment.
    """
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def _strip_comments(text: str) -> str:
    """Blank out Jinja comments, preserving line numbers.

    Prose explaining the ban must not trip it -- section 11.3 -- but the lines
    still have to line up, so comments are replaced with newlines rather than
    removed.
    """

    def _blank(match: re.Match[str]) -> str:
        return "\n" * match.group(0).count("\n")

    return _COMMENT_RE.sub(_blank, text)


def _walk(root: Path) -> Iterator[Path]:
    for dirname in SCAN_DIRS:
        base = root / dirname
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*.sql")):
            if SKIP_PARTS & set(path.parts):
                continue
            yield path


def _scan(path: Path, *, exempt_from_tier_2: bool) -> list[str]:
    """Return every banned construct found in one file."""
    found: list[str] = []
    body = _strip_comments(path.read_text(encoding="utf-8"))
    for lineno, line in enumerate(body.splitlines(), start=1):
        found.extend(
            f"{_rel(path)}:{lineno}: [tier 1] {why}"
            for pattern, why in ALWAYS_BANNED.items()
            if re.search(pattern, line)
        )
        if exempt_from_tier_2:
            continue
        found.extend(
            f"{_rel(path)}:{lineno}: [tier 2] {why}"
            for pattern, why in RUN_CONTEXT_BANNED.items()
            if re.search(pattern, line)
        )
    return found


def check(roots: tuple[Path, ...] = PROJECT_ROOTS) -> list[str]:
    """Return every non-determinism violation, plus any stale exemption."""
    errors: list[str] = []
    seen_exempt: set[str] = set()

    for project in roots:
        if not project.is_dir():
            continue
        for path in _walk(project):
            rel_to_project = path.relative_to(project).as_posix()
            is_exempt = rel_to_project in EXEMPT_RUN_CONTEXT
            if is_exempt:
                seen_exempt.add(rel_to_project)
            errors.extend(_scan(path, exempt_from_tier_2=is_exempt))

    # A stale exemption is indistinguishable from a live one by reading, which is
    # the whole failure mode 3.39 exists to catch one level up. Reported, not
    # fatal: these macros are scheduled, not missing.
    stale = sorted(EXEMPT_RUN_CONTEXT - seen_exempt)
    if stale:
        joined = ", ".join(stale)
        sys.stderr.write(
            f"note: {len(stale)} tier-2 exemption(s) name files that do not exist yet: {joined}\n"
        )

    return errors


def main() -> int:
    """Return 0 when no banned construct reaches model or macro code."""
    errors = check()
    for err in errors:
        sys.stderr.write(f"ERROR: {err}\n")
    if errors:
        sys.stderr.write(f"\n{len(errors)} non-determinism violation(s).\n")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
