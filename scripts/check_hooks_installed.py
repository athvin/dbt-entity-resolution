#!/usr/bin/env python3
"""pre-commit must be INSTALLED, not merely configured (3.87, D.0 finding 84).

`.pre-commit-config.yaml` carries `no-commit-to-branch --branch main`, and the
comment above it says, in as many words:

    This is the hook that would have caught c7ffae6 being pushed straight to
    main.

It could not have. `pre-commit install` had never been run in this working tree,
so `.git/hooks/pre-commit` did not exist and **no hook in that file has ever run
on a `git commit`**. The only place they execute is `pre-commit run --all-files`
in CI — where `no-commit-to-branch` is explicitly `SKIP`ped, because CI is always
on a detached head and it would fail every run.

So the guard against pushing to main was configured, documented, credited with a
past save, and structurally incapable of firing. Stage 3 then went straight to
main and CI went red (finding 83). This is the repository's own recurring defect
— a gate that looks like it works — aimed squarely at the repository's own
recurring process failure.

**Why a config file cannot check itself.** `.git/hooks/` is not version
controlled, so a fresh clone starts with the hooks absent no matter what the
config says. That is not a flaw to work around; it is the reason this check
exists. The obligation has to be asserted somewhere a build can see it.

Skipped under CI, where the hooks genuinely should not be installed and every
commit is someone else's already.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from _er_paths import ROOT

HOOK = "pre-commit"
CONFIG = ".pre-commit-config.yaml"

# The hooks a `git commit` must actually run. `no-commit-to-branch` is the one
# finding 83 needed; the rest come along with it and are listed so a partial
# install is a failure rather than a silence.
REQUIRED_IN_CONFIG = ("no-commit-to-branch",)


def _git_dir(root: Path) -> Path | None:
    """Resolve the real `.git` directory, worktrees included."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--git-dir"],  # noqa: S607
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (subprocess.CalledProcessError, OSError):
        return None
    path = Path(out)
    return path if path.is_absolute() else (root / path)


def check(root: Path = ROOT) -> list[str]:
    """Return an error when a `git commit` would run none of the configured hooks."""
    config = root / CONFIG
    if not config.is_file():
        return [f"{CONFIG} does not exist, so there are no hooks to install."]

    text = config.read_text(encoding="utf-8")
    missing = [name for name in REQUIRED_IN_CONFIG if name not in text]
    if missing:
        return [
            (
                f"{CONFIG} does not configure {', '.join(missing)}. That hook is what "
                f"stops a change reaching main without a PR (D.0 finding 83)."
            )
        ]

    git_dir = _git_dir(root)
    if git_dir is None:
        # Not a git work tree -- a tarball, or a scratch copy under test. There
        # is no commit to guard, so there is nothing to assert.
        sys.stdout.write("3.87: not a git work tree; no commit hooks to check.\n")
        return []

    installed = git_dir / "hooks" / HOOK
    if not installed.is_file():
        return [
            (
                f"pre-commit is configured but NOT INSTALLED: {installed} does not "
                f"exist, so `git commit` runs none of the hooks in {CONFIG} -- "
                f"including `no-commit-to-branch`, which is the one that stops a "
                f"change reaching main without a PR.\n"
                f"  Run: uv run pre-commit install\n"
                f"  This is not hypothetical: it is why finding 83 happened, and the "
                f"comment above that hook credits it with a save it could not have "
                f"made."
            )
        ]

    if HOOK not in installed.read_text(encoding="utf-8", errors="replace"):
        return [
            (
                f"{installed} exists but is not pre-commit's hook. Something else "
                f"owns it, so the configured hooks still do not run on `git commit`."
            )
        ]

    sys.stdout.write(f"3.87: pre-commit installed at {installed.name}; commit hooks will run.\n")
    return []


def main() -> int:
    """Return 0 when a `git commit` would actually run the configured hooks."""
    if os.environ.get("CI") or os.environ.get("GITHUB_ACTIONS"):
        # CI is always on a detached head and commits nothing. Installing hooks
        # there would make `no-commit-to-branch` fail every run, which is why
        # the workflow SKIPs it -- so asserting installation would be asserting
        # something that must not be true.
        sys.stdout.write("3.87: CI detected; commit hooks are a local obligation.\n")
        return 0
    errors = check()
    for err in errors:
        sys.stderr.write(f"ERROR: {err}\n")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
