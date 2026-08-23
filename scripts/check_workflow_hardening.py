#!/usr/bin/env python3
"""Assert the CI workflows are SHA-pinned and least-privilege (3.56).

Section 15 pins the runner image by exact version and then argues that the same
reasoning applies *with more force* to third-party actions: a floating tag is
executable code someone else can change under you, which a base image is not.

Three properties, each a separate failure mode:

1. **Every `uses:` carries a 40-character commit SHA.** `actions/checkout@v5` is
   a moving target; `@fbc6f39…` is not.
2. **`persist-credentials: false` on every checkout.** The default leaves a
   usable token in `.git/config` for every subsequent step, including
   third-party actions.
3. **Every job declares `permissions:`.** The workflow-level default is right
   for today's jobs and will not be for the nightly one; granting per job is
   what keeps that a decision rather than a drift.

A fourth is asserted because §15 states a position on it: **`pull_request_target`
is never used.** C.7 injects the model JSON into `GITHUB_ENV` through a heredoc,
which is safe only while that JSON is repository-controlled -- and
`pull_request_target` is precisely the trigger that stops being true.
"""

from __future__ import annotations

import re
import sys
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

import yaml

from _er_paths import ROOT
from _er_paths import rel as _rel

WORKFLOWS = ROOT / ".github" / "workflows"

_SHA_RE = re.compile(r"@[0-9a-f]{40}$")


def _jobs(doc: dict[str, Any]) -> dict[str, Any]:
    jobs = doc.get("jobs") or {}
    return jobs if isinstance(jobs, dict) else {}


def check(workflows: Path = WORKFLOWS) -> list[str]:
    """Return every workflow-hardening violation."""
    errors: list[str] = []
    files = sorted(workflows.glob("*.yml")) + sorted(workflows.glob("*.yaml"))

    if not files:
        return [
            (
                f"no workflows found under {workflows}. This check had nothing "
                f"to check, which section 6.1 calls the worst kind of pass."
            )
        ]

    for path in files:
        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        rel = _rel(path)

        # `on:` parses as the boolean True in YAML 1.1, which is why section 11.4
        # disables yamllint's truthy check for keys rather than quoting it here.
        triggers = doc.get("on", doc.get(True)) or {}
        if isinstance(triggers, dict) and "pull_request_target" in triggers:
            errors.append(
                f"{rel}: uses `pull_request_target`. Section 15's stated position "
                f"is never: it is the standard way this class of workflow is "
                f"compromised, and C.7's GITHUB_ENV heredoc is safe only while "
                f"the model JSON is repository-controlled."
            )

        for job_name, job in _jobs(doc).items():
            if not isinstance(job, dict):
                continue
            if "permissions" not in job:
                errors.append(
                    f"{rel}: job `{job_name}` declares no `permissions:`. "
                    f"Grant per job (3.56) rather than relying on the workflow "
                    f"default, which will be wrong for the first job that needs more."
                )
            errors.extend(_check_steps(rel, job_name, job.get("steps") or []))
    return errors


def _check_steps(rel: str, job_name: str, steps: list[Any]) -> list[str]:
    """Return every hardening violation among one job's steps."""
    errors: list[str] = []
    for step in steps:
        if not isinstance(step, dict):
            continue
        uses = step.get("uses")
        if not uses:
            continue
        if not _SHA_RE.search(str(uses)):
            errors.append(
                f"{rel}: job `{job_name}` uses `{uses}`, which is not pinned to a "
                f"40-character commit SHA. A mutable tag is executable "
                f"third-party code that can change under you."
            )
        if "actions/checkout@" in str(uses):
            with_ = step.get("with") or {}
            if with_.get("persist-credentials") is not False:
                errors.append(
                    f"{rel}: job `{job_name}` checks out without "
                    f"`persist-credentials: false`. The default leaves a usable "
                    f"token in .git/config for every later step."
                )
    return errors


def main() -> int:
    """Return 0 when every workflow is SHA-pinned and least-privilege."""
    errors = check()
    for err in errors:
        sys.stderr.write(f"ERROR: {err}\n")
    if errors:
        sys.stderr.write(f"\n{len(errors)} workflow-hardening violation(s).\n")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
