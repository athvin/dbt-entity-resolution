#!/usr/bin/env python3
"""Divergence log <-> pinning test, both directions (3.49); PARITY.md stage coverage (3.50).

**3.49 -- both directions, and the second one is the one that matters.**

Forward: every entry in `docs/divergence-log.md` names a pinning test, and that
test exists and cites the entry. This is the direction a reviewer checks anyway.

Reverse: every `DIV-nn` cited anywhere in the test suite has an entry in the log.
Without it, a divergence can be pinned by a test and never written down -- so the
behaviour is frozen, deliberately, with no record of *why*. Section 19.4 is
explicit that 3.49 is what stopped this being "a checkbox".

A divergence from the oracle that nobody logged is indistinguishable, six months
on, from a bug that nobody noticed. That is the state this check prevents.

**3.50** asserts `PARITY.md` names every stage the DAG contains -- comparing
`parity_stage_N` tags in the manifest against the sections in the file. A stage
that is built but unpublished is a stage whose parity nobody claimed.
"""

from __future__ import annotations

import json
import re
import sys
from typing import TYPE_CHECKING

from _er_paths import ROOT, rel
from _er_pending import Pending

if TYPE_CHECKING:
    from pathlib import Path

LOG_PATH = "docs/divergence-log.md"
PARITY_PATH = "PARITY.md"

# Where a pinning test may live. `tests/` is dbt's test-paths; `harness/` holds
# the pytest comparators. Never `tests_python/`, which is the enforcement
# scripts' own suite (3.57) and has nothing to do with parity.
_TEST_ROOTS = ("tests", "harness", "integration_tests/tests")

_ENTRY = re.compile(r"^#{2,3}\s+(DIV-\d+)\b(.*)$", re.MULTILINE)
_PINNING = re.compile(r"^\s*[-*]?\s*\*{0,2}Pinning test:\*{0,2}\s*`([^`]+)`", re.MULTILINE)
_DIV_ID = re.compile(r"\bDIV-\d+\b")
_STAGE_TAG = re.compile(r"^parity_stage_(\d+[a-z]?)$")
_PARITY_STAGE = re.compile(r"^#{2,3}\s+.*?\bStage\s+(\d+[a-z]?)\b", re.MULTILINE | re.IGNORECASE)


def _entries(text: str) -> dict[str, str]:
    """Return `{DIV id: body}` for every section in the divergence log."""
    matches = list(_ENTRY.finditer(text))
    entries: dict[str, str] = {}
    for i, match in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        entries[match.group(1)] = text[match.end() : end]
    return entries


def _cited_in_tests(root: Path) -> dict[str, list[str]]:
    """Return `{DIV id: [paths citing it]}` across every test root."""
    cited: dict[str, list[str]] = {}
    for test_root in _TEST_ROOTS:
        base = root / test_root
        if not base.is_dir():
            continue
        for path in base.rglob("*"):
            if not path.is_file() or path.suffix not in {".sql", ".py", ".yml", ".yaml", ".md"}:
                continue
            try:
                body = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            for ident in set(_DIV_ID.findall(body)):
                cited.setdefault(ident, []).append(rel(path, root))
    return cited


def _manifest_stages(root: Path) -> set[str] | None:
    """Stage numbers carried by `parity_stage_N` tags, or None with no manifest."""
    manifest = root / "target" / "manifest.json"
    if not manifest.is_file():
        return None
    data = json.loads(manifest.read_text(encoding="utf-8"))
    stages: set[str] = set()
    for node in data.get("nodes", {}).values():
        for tag in node.get("tags", []) or []:
            match = _STAGE_TAG.match(str(tag))
            if match:
                stages.add(match.group(1))
    return stages


def check(root: Path = ROOT) -> list[str]:
    """Return every broken divergence/test correspondence and parity gap."""
    pending = Pending("check_divergence_log.py", root=root)
    errors: list[str] = list(pending.errors())

    log = root / LOG_PATH
    cited = _cited_in_tests(root)

    if not log.is_file():
        if not pending.is_pending(LOG_PATH):
            errors.append(
                f"{LOG_PATH} does not exist and is not declared pending. 3.49 cannot "
                f"check a log that is absent, and passing silently is how an "
                f"unlogged divergence ships."
            )
        else:
            sys.stdout.write(pending.notice(LOG_PATH) + "\n")
        # The reverse direction still applies: a pinning test may exist before
        # the log does, and that is exactly the case worth catching.
        errors.extend(
            f"{ident} is cited by {', '.join(sorted(paths))} but {LOG_PATH} does not "
            f"exist. The divergence is pinned and unrecorded."
            for ident, paths in sorted(cited.items())
        )
    else:
        text = log.read_text(encoding="utf-8")
        entries = _entries(text)
        if not entries:
            errors.append(
                f"{LOG_PATH} exists but declares no `## DIV-nn` entries. Either it is "
                f"empty -- in which case remove it -- or the heading format changed and "
                f"this check is walking an empty set."
            )

        for ident, body in sorted(entries.items()):
            pin = _PINNING.search(body)
            if not pin:
                errors.append(
                    f"{ident}: no `Pinning test:` line. A divergence with no test is a "
                    f"decision nothing holds in place (3.49)."
                )
                continue
            test_path = root / pin.group(1)
            if not test_path.is_file():
                errors.append(f"{ident}: pinning test `{pin.group(1)}` does not exist.")
            elif ident not in test_path.read_text(encoding="utf-8"):
                errors.append(
                    f"{ident}: pinning test `{pin.group(1)}` exists but does not cite "
                    f"{ident}. The link is one-way, so renaming either side breaks it "
                    f"silently."
                )

        errors.extend(
            f"{ident} is cited by {', '.join(sorted(paths))} but has no entry in "
            f"{LOG_PATH}. A pinned divergence with no log entry freezes the behaviour "
            f"without recording why (3.49, reverse direction)."
            for ident, paths in sorted(cited.items())
            if ident not in entries
        )

    errors.extend(_check_parity(root, pending))
    return errors


def _check_parity(root: Path, pending: Pending) -> list[str]:
    """3.50: `PARITY.md` names every stage the DAG contains."""
    parity = root / PARITY_PATH
    stages = _manifest_stages(root)

    if not parity.is_file():
        if pending.is_pending(PARITY_PATH):
            sys.stdout.write(pending.notice(PARITY_PATH) + "\n")
            return []
        return [
            f"{PARITY_PATH} does not exist and is not declared pending (3.50, DesignDoc DoD 5)."
        ]

    if stages is None:
        return [
            (
                f"{PARITY_PATH} exists but target/manifest.json does not -- 3.50 cannot "
                f"compare it against the DAG. Run `dbt parse` before this check."
            )
        ]

    published = set(_PARITY_STAGE.findall(parity.read_text(encoding="utf-8")))
    missing = sorted(stages - published, key=_stage_sort)
    return [
        f"{PARITY_PATH} does not name Stage {stage}, which the DAG contains "
        f"(tagged parity_stage_{stage}). A built stage with no published parity "
        f"claim is an unstated gap (3.50)."
        for stage in missing
    ]


def _stage_sort(stage: str) -> tuple[int, str]:
    digits = "".join(c for c in stage if c.isdigit())
    return (int(digits or 0), stage)


def main() -> int:
    """Return 0 when divergences and parity claims are complete in both directions."""
    errors = check()
    for err in errors:
        sys.stderr.write(f"ERROR: {err}\n")
    if errors:
        sys.stderr.write(f"\n{len(errors)} divergence/parity gap(s) (3.49, 3.50).\n")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
