"""Declared-empty subjects for checks that ship before their artefacts (section 23).

A check whose subject does not exist walks an empty set, exits 0, and looks
exactly like a check that passed. `pending_subjects.yml` is where a check
declares that it is currently running against nothing, and this module enforces
the half that makes the declaration worth having: **an entry whose path now
exists is an error**, so the subject cannot appear without the check waking up.

The leading underscore keeps it out of dbt-bouncer's and pre-commit's script
globs: it is a helper, not a check.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import yaml

from _er_paths import ROOT

if TYPE_CHECKING:
    from pathlib import Path

REGISTRY_NAME = "pending_subjects.yml"


class Pending:
    """The `pending_subjects.yml` entries belonging to one check."""

    def __init__(self, check: str, root: Path = ROOT) -> None:
        self.check = check
        self.root = root
        self.path = root / "scripts" / REGISTRY_NAME
        self._entries, self._load_errors = self._load()

    def _load(self) -> tuple[list[dict[str, Any]], list[str]]:
        if not self.path.is_file():
            return [], [
                f"{REGISTRY_NAME} does not exist; every check that consults it is unguarded."
            ]
        loaded = yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}
        raw = loaded.get("pending")
        if not isinstance(raw, list):
            return [], [f"{REGISTRY_NAME}: `pending:` is missing or is not a list."]

        entries: list[dict[str, Any]] = []
        errors: list[str] = []
        for item in raw:
            if not isinstance(item, dict):
                errors.append(f"{REGISTRY_NAME}: entry is not a mapping: {item!r}")
                continue
            if not item.get("path") and not item.get("standard"):
                errors.append(
                    f"{REGISTRY_NAME}: entry {item!r} names neither a `path` nor a `standard`."
                )
                continue
            missing = [k for k in ("check", "created_by", "since") if not item.get(k)]
            if missing:
                errors.append(
                    f"{REGISTRY_NAME}: entry {item.get('path', '?')!r} is missing {missing}. "
                    f"An entry without `created_by` is a waiver nobody can retire."
                )
                continue
            if item["check"] == self.check:
                entries.append(item)
        return entries, errors

    def is_pending(self, subject: str) -> bool:
        """Report whether `subject` -- a path or a standard id -- is declared empty."""
        return any(e.get("path") == subject or e.get("standard") == subject for e in self._entries)

    def notice(self, subject: str) -> str:
        """Return the line a check prints when it skips a declared-empty subject."""
        entry = next(
            e for e in self._entries if e.get("path") == subject or e.get("standard") == subject
        )
        return (
            f"PENDING: {subject} does not exist yet -- declared in {REGISTRY_NAME} "
            f"since {entry['since']}, created by {entry['created_by'].strip()}"
        )

    def errors(self) -> list[str]:
        """Registry defects, and every entry whose path now exists.

        The second is the point of the file. An entry that outlives its subject
        silences a real check, and does it at exactly the moment the check first
        has something to say.
        """
        errors = list(self._load_errors)
        errors.extend(
            f"{REGISTRY_NAME} still lists `{entry['path']}` as pending for {self.check}, "
            f"but it now EXISTS. Remove the entry -- until you do, this check is "
            f"skipping a subject that is really there."
            for entry in self._entries
            if entry.get("path") and (self.root / entry["path"]).exists()
        )
        return errors
