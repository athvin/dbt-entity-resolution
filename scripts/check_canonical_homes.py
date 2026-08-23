#!/usr/bin/env python3
"""One canonical home per configuration artifact (3.73, section 23).

Section 23's rule, stated there as prose and mechanised here:

> **Every configuration artifact has exactly one canonical home, and once the
> repository file exists, it is the canonical one.** This document thereafter
> cites it **by path**, keeping the provenance marker, the delta ledger and the
> rationale — never a second copy of the content.

Section 23 also explains why the rule needs a mechanism rather than a sentence:
"mechanisms nothing verified still existed ... is how section 7 stayed stale for
a revision while the decision replacing it sat three pages away". A second copy
of a live file is that failure in its purest form -- the copy cannot be run, so
nothing tells you when it stops being true, and a reader cannot tell which of
the two is current.

**What counts as a violation:** a heading names a path in backticks, that path
exists in the repository, and a fenced block follows before the next heading.
Reducing a section to the pointer form removes the block, so it passes.

**The escape hatch, capped.** A short illustrative excerpt is sometimes worth
quoting. Mark it `<!-- excerpt: why -->` immediately before the fence. The
marker is counted and capped, because an uncapped waiver is not a rule.
"""

from __future__ import annotations

import re
import sys
from typing import TYPE_CHECKING

from _er_paths import ROOT, rel

if TYPE_CHECKING:
    from pathlib import Path

DOCS_DIR = "docs"

# An excerpt is an excerpt; a copy is a copy. Anything longer is a copy.
MAX_EXCERPT_LINES = 12

# Section 18's argument applied to documentation: a waiver nobody counts is a
# hole nobody sees. Raise it deliberately, never to make a run green.
MAX_EXCERPTS = 4

_HEADING = re.compile(r"^#{2,4}\s+(.*)$")
_TICKED = re.compile(r"`([^`]+)`")
_FENCE = re.compile(r"^\s*```")
_EXCERPT = re.compile(r"<!--\s*excerpt:", re.IGNORECASE)

# Paths that are directories, globs or prose rather than artifacts.
_NOT_A_PATH = re.compile(r"[*?\s]|/$")


def _candidate_paths(heading: str, root: Path) -> list[str]:
    """Backticked tokens in a heading that name a file present in the repo."""
    found: list[str] = []
    for token in _TICKED.findall(heading):
        candidate = token.strip()
        if not candidate or _NOT_A_PATH.search(candidate):
            continue
        if (root / candidate).is_file():
            found.append(candidate)
    return found


def _scan(path: Path, root: Path) -> tuple[list[str], int]:
    """Return violations in one document, and its excerpt-marker count."""
    lines = path.read_text(encoding="utf-8").splitlines()
    violations: list[str] = []
    excerpts = 0

    heading = ""
    live_paths: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        head = _HEADING.match(line)
        if head:
            heading = head.group(1).strip()
            live_paths = _candidate_paths(heading, root)
            i += 1
            continue

        if _FENCE.match(line):
            marked = any(_EXCERPT.search(prev) for prev in lines[max(0, i - 2) : i])
            start = i
            i += 1
            while i < len(lines) and not _FENCE.match(lines[i]):
                i += 1
            length = i - start - 1
            if marked:
                excerpts += 1
                if length > MAX_EXCERPT_LINES:
                    violations.append(
                        f"{rel(path, root)}:{start + 1}: excerpt under "
                        f"'{heading[:60]}' is {length} lines (cap {MAX_EXCERPT_LINES}). "
                        f"An excerpt that long is a copy with a label on it."
                    )
            elif live_paths:
                violations.append(
                    f"{rel(path, root)}:{start + 1}: '{heading[:60]}' names "
                    f"`{live_paths[0]}`, which EXISTS in the repository, and carries a "
                    f"{length}-line fenced block. Per section 23 the file is canonical: "
                    f"cite it by path and keep the provenance, delta ledger and "
                    f"rationale -- never a second copy of the content."
                )
        i += 1

    return violations, excerpts


def check(root: Path = ROOT) -> list[str]:
    """Return every document block that duplicates a live repository file."""
    docs = root / DOCS_DIR
    if not docs.is_dir():
        return [f"{DOCS_DIR}/ does not exist -- 3.73 has nothing to scan."]

    markdown = sorted(docs.rglob("*.md"))
    if not markdown:
        return [f"{DOCS_DIR}/ contains no markdown -- this check has no subject."]

    errors: list[str] = []
    excerpts = 0
    for path in markdown:
        found, marked = _scan(path, root)
        errors.extend(found)
        excerpts += marked

    if excerpts > MAX_EXCERPTS:
        errors.append(
            f"{excerpts} excerpt marker(s) across {DOCS_DIR}/, cap is {MAX_EXCERPTS}. "
            f"The marker is an escape hatch for illustration, not a way to keep "
            f"copies of live files."
        )

    sys.stdout.write(f"3.73: {len(markdown)} document(s) scanned, {excerpts} marked excerpt(s).\n")
    return errors


def main() -> int:
    """Return 0 when no document duplicates a live configuration file."""
    errors = check()
    for err in errors:
        sys.stderr.write(f"ERROR: {err}\n")
    if errors:
        sys.stderr.write(f"\n{len(errors)} duplicated configuration artifact(s) (3.73).\n")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
