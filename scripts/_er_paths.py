"""Shared path helper for the enforcement scripts.

Extracted on its third occurrence. Every check formats paths relative to the
repository root so its message is something a human can act on -- but a scanned
tree is not always inside the root: 3.57's failing-case tests build one in a
temp directory, and `verify_gates.py` builds one in a scratch copy. `relative_to`
raises there, and the bug surfaces as a test failure in the check rather than a
finding about the code being checked.

The leading underscore keeps it out of dbt-bouncer's and pre-commit's script
globs: it is a helper, not a check.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def rel(path: Path, root: Path = ROOT) -> str:
    """Render ``path`` relative to ``root``, or absolute when it is outside."""
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)
