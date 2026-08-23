#!/usr/bin/env python3
"""Assert every mechanism named in the section 3 matrix exists and is wired up (3.39).

Section 23 explains why this rule exists: "mechanisms nothing verified still
existed ... is how section 7 stayed stale for a revision". A matrix row is a
promise that something enforces the standard. Nothing checked that the something
was real, so a rule could name a script that was never written, a bouncer check
that was never configured, or a SQLFluff code that was excluded three files away.

The matrix cites four kinds of thing that can be resolved mechanically:

  * a repository path (`scripts/*.py`, `dbt-bouncer.yml`, `.sqlfluff`)  -- must exist
  * a dbt-bouncer check name (`check_model_has_constraints`)            -- must be configured
  * a SQLFluff rule code (`ST05`, `LT02`)                               -- must not be disabled
  * a pre-commit hook id (`er-yml-pairing`)                             -- must be configured

Everything else in a mechanism cell is prose and is left alone.

**Two non-vacuity guards, because this check is itself the class of thing it
polices.** A parser that silently matches nothing exits 0 -- the failure Appendix
D.0 finding 20 records for dbt-bouncer, where `SUCCESS=0` was a green run. So the
row count and the resolved-citation count both have floors, and a row whose
mechanism cell resolves to nothing at all must say `unenforced` explicitly.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

from _er_paths import ROOT, rel
from _er_pending import REGISTRY_NAME, Pending

if TYPE_CHECKING:
    from collections.abc import Iterable

DOC = ROOT / "docs" / "DbtBestPractices.md"

# The matrix has 71 rows today. A floor well below that catches a parser that
# stopped matching after a formatting change, without tripping on every addition.
MIN_ROWS = 60

# Likewise: if fewer than this many citations resolve, the extractor has broken
# rather than the repository having become clean.
MIN_CITATIONS = 40

# Rows that name no mechanism because none exists. Section 3 labels these
# "convention (unenforced)"; they are legitimate, and capped so that marking a
# row unenforced cannot become the way to dodge this check.
MAX_UNENFORCED = 6

_ID = re.compile(r"^\*{0,2}(3\.\d+)\*{0,2}$")
# | # | Standard | Mechanism | Gate | On violation |
_MECHANISM_COL = 2
_MIN_COLS = 5
_TICKED = re.compile(r"`([^`]+)`")
_SQLFLUFF_CODE = re.compile(r"^[A-Z]{2}\d{2}$")
_BARE_CONFIGS = frozenset({".sqlfluff", ".sqlfluffignore"})
_UNENFORCED = re.compile(r"convention \(unenforced\)|unenforced", re.IGNORECASE)
# "Same script", "Same check", "3.11 + 3.12 together" -- a row may cite the row
# above rather than repeat it. That is a real mechanism, named by reference.
_CROSS_REF = re.compile(r"^same\b|^\d\.\d+\s*\+|^see\b", re.IGNORECASE)

# Dashed lowercase tokens that are NOT pre-commit hook ids. Written out and
# capped rather than widening the pattern, so the exceptions stay auditable --
# the same discipline section 18 applies to waivers.
_NOT_HOOK_IDS = frozenset(
    {
        "dbt-core",
        "dbt-duckdb",
        "dbt-bouncer",
        "dbt-utils",
        "dbt-osmosis",
        "sqlfluff-templater-dbt",
        "pre-commit",
        "ubuntu-24.04",
        "linux-amd64",
        "run-operation",
        "full-refresh",
        "store-failures",
        "check-name",
        "entity-grain",
        "pair-grain",
        # dbt concepts, not hooks.
        "on-run-end",
        "on-run-start",
    }
)

# A pre-commit hook id: lowercase words joined by dashes, nothing else.
_HOOK_SHAPED = re.compile(r"^[a-z][a-z0-9]*(-[a-z0-9.]+)+$")

# yamllint's own rule names, so a cited-but-unconfigured rule is caught rather
# than mistaken for a missing pre-commit hook.
_YAMLLINT_KNOWN = re.compile(
    r"^(empty-values|key-duplicates|line-length|truthy|comments|comments-indentation"
    r"|quoted-strings|braces|brackets|colons|commas|document-start|document-end"
    r"|empty-lines|hyphens|indentation|new-line-at-end-of-file|octal-values"
    r"|trailing-spaces|new-lines|key-ordering|float-values|anchors)$"
)

# Cited by the matrix but owned elsewhere: dbt's own features, not our wiring.
_NOT_OUR_ARTEFACTS = frozenset(
    {
        "contract: {enforced: true}",
        "constraints",
        "unit_tests:",
        "data_tests:",
        "severity",
        "store_failures_as",
        "patch_path",
        "node.constraints",
        "warn_error_options",
        "error: all",
    }
)


def _installed_bouncer_checks() -> set[str]:
    """Every check function dbt-bouncer 3.8.0 actually ships.

    Knowing the real set is what separates "you typed the name wrong" from "the
    check is real and you never configured it". 3.19 cited
    `check_model_has_tests_by_type` -- a real check, absent from the
    reconstructed C.5 -- and without this distinction that reads as a typo.
    """
    try:
        import dbt_bouncer  # type: ignore[import-untyped]  # noqa: PLC0415
    except ImportError:
        return set()
    pkg = Path(dbt_bouncer.__file__).parent / "checks"
    if not pkg.is_dir():
        return set()
    names: set[str] = set()
    for module in pkg.rglob("*.py"):
        names.update(
            re.findall(r"^def (check_[a-z0-9_]+)", module.read_text(encoding="utf-8"), re.MULTILINE)
        )
    return names


def _injected_standards(root: Path) -> set[str]:
    """Return the standard ids `verify_gates.py` registers an injection for.

    Read as text rather than imported: this check must work against a mirrored
    tree in `tmp_path`, where importing would load *this* repository's module
    and silently check the wrong thing.
    """
    gates = root / "scripts" / "verify_gates.py"
    if not gates.is_file():
        return set()
    return set(re.findall(r'standard="(\d+\.\d+)"', gates.read_text(encoding="utf-8")))


def _matrix_rows(text: str) -> list[tuple[str, str, str]]:
    """Return `(standard_id, mechanism_cell, full_row)` for every matrix row."""
    start = text.find("\n## 3.")
    end = text.find("\n## 4.")
    if start == -1 or end == -1:
        return []
    rows: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    for line in text[start:end].splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        # Split rather than group: a greedy `(.*)` between pipes swallows the
        # Standard column and hands back Gate as the mechanism.
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if len(cells) < _MIN_COLS:
            continue
        match = _ID.match(cells[0])
        if not match:
            continue
        ident, mechanism = match.group(1), cells[_MECHANISM_COL]
        # The matrix is quoted in review notes and in Appendix D's injection
        # tables; the first occurrence is the normative row.
        if ident in seen:
            continue
        seen.add(ident)
        # `_UNENFORCED` lives in the "On violation" column, not the mechanism
        # cell, so classification needs the whole row.
        rows.append((ident, mechanism.strip(), stripped))
    return rows


def _bouncer_check_names(path: Path) -> set[str]:
    if not path.is_file():
        return set()
    loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    names: set[str] = set()
    for value in loaded.values():
        if not isinstance(value, list):
            continue
        for item in value:
            if isinstance(item, dict) and isinstance(item.get("name"), str):
                names.add(item["name"])
    return names


def _precommit_hook_ids(path: Path) -> set[str]:
    if not path.is_file():
        return set()
    loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return {
        hook["id"]
        for repo in loaded.get("repos", [])
        if isinstance(repo, dict)
        for hook in repo.get("hooks", [])
        if isinstance(hook, dict) and isinstance(hook.get("id"), str)
    }


def _yamllint_rules(path: Path) -> tuple[set[str], set[str]]:
    """Return `(configured, disabled)` yamllint rule names."""
    if not path.is_file():
        return set(), set()
    loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    rules = loaded.get("rules") or {}
    if not isinstance(rules, dict):
        return set(), set()
    disabled = {name for name, value in rules.items() if value == "disable"}
    return set(rules), disabled


def _sqlfluff_disabled(path: Path) -> set[str]:
    """Rule codes switched off in `.sqlfluff` -- `exclude_rules` and friends."""
    if not path.is_file():
        return set()
    disabled: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        key, sep, value = line.partition("=")
        if sep and key.strip() == "exclude_rules":
            disabled.update(part.strip() for part in value.split(",") if part.strip())
    return disabled


_MIN_HOOK_DASHES = 2


@dataclass(frozen=True)
class _Wiring:
    """Everything a citation can be resolved against."""

    root: Path
    bouncer: set[str]
    installed: set[str]
    hooks: set[str]
    disabled: set[str]
    yamllint_rules: set[str]
    yamllint_disabled: set[str]

    @classmethod
    def load(cls, root: Path) -> _Wiring:
        return cls(
            root=root,
            bouncer=_bouncer_check_names(root / "dbt-bouncer.yml"),
            installed=_installed_bouncer_checks(),
            hooks=_precommit_hook_ids(root / ".pre-commit-config.yaml"),
            disabled=_sqlfluff_disabled(root / ".sqlfluff"),
            yamllint_rules=_yamllint_rules(root / ".yamllint.yml")[0],
            yamllint_disabled=_yamllint_rules(root / ".yamllint.yml")[1],
        )

    def classify(self, token: str) -> str | None:  # noqa: PLR0911
        """Return an error for `token`, or None when it resolves or is prose.

        One branch per citation kind, and the count IS the coverage: paths,
        SQLFluff codes, dbt-bouncer checks, yamllint rules, pre-commit hooks.
        Collapsing them into a table would hide which kinds are checked at all,
        which is the property 3.39 exists to make visible.
        """
        if token in _NOT_OUR_ARTEFACTS or token.startswith(("+", "{{", "--")):
            return None
        if token.endswith((".py", ".yml", ".yaml", ".toml")) or token in _BARE_CONFIGS:
            return self._as_path(token)
        if _SQLFLUFF_CODE.match(token):
            return self._as_sqlfluff_rule(token)
        if token.startswith("check_"):
            return self._as_bouncer_check(token)
        if token in self.yamllint_rules or _YAMLLINT_KNOWN.match(token):
            return self._as_yamllint_rule(token)
        if _HOOK_SHAPED.match(token) and token not in _NOT_HOOK_IDS:
            return self._as_hook(token)
        return None

    def _as_path(self, token: str) -> str | None:
        candidates = (
            self.root / token,
            self.root / "scripts" / token,
            self.root / "dbt_bouncer_checks" / token,
        )
        if any(c.exists() for c in candidates):
            return None
        # A bare basename may live anywhere in the tree.
        if "/" not in token and any(self.root.rglob(token)):
            return None
        return f"cites `{token}`, which does not exist at {rel(self.root / token, self.root)}"

    def _as_yamllint_rule(self, token: str) -> str | None:
        """Resolve a yamllint rule, which must be ENABLED and not merely mentioned.

        §11.4 says two rules carry the weight -- `empty-values` and
        `key-duplicates` -- and §10.4 calls the first "the gate contracts cannot
        provide". Both were invisible to 3.39 before this: they are dashed
        lowercase tokens, so they read as pre-commit hook ids that were not
        configured, and before *that* they read as prose.
        """
        if token in self.yamllint_disabled:
            return (
                f"cites yamllint rule `{token}`, which `.yamllint.yml` sets to "
                f"`disable`. A standard whose mechanism is switched off is not "
                f"enforced"
            )
        if token not in self.yamllint_rules:
            return (
                f"cites yamllint rule `{token}`, which `.yamllint.yml` does not "
                f"configure. It inherits from `extends: default`, so state it "
                f"explicitly if a standard depends on it"
            )
        return None

    def _as_sqlfluff_rule(self, token: str) -> str | None:
        if token in self.disabled:
            return f"cites SQLFluff rule `{token}`, which `.sqlfluff` puts in `exclude_rules`"
        return None

    def _as_bouncer_check(self, token: str) -> str | None:
        if token in self.bouncer:
            return None
        # Custom checks live as files, not config entries.
        if any(self.root.rglob(f"{token}.py")):
            return None
        if token in self.installed:
            return (
                f"cites dbt-bouncer check `{token}`, which EXISTS in dbt-bouncer "
                f"but is not configured in dbt-bouncer.yml -- the standard names a "
                f"mechanism that is switched off"
            )
        return (
            f"cites `{token}`, which is not a dbt-bouncer check, not configured, "
            f"and has no custom-check file. Check the spelling against "
            f"`dbt-bouncer --list`"
        )

    def _as_hook(self, token: str) -> str | None:
        """Resolve a pre-commit hook id.

        Previously this was reached only for `er-` prefixed tokens, which meant
        every UPSTREAM hook the matrix cites -- `detect-private-key`,
        `check-added-large-files`, `no-commit-to-branch` -- fell through as
        prose and resolved by default. That is how 3.55 could name a mechanism
        that was only half-present and still satisfy 3.39.
        """
        if token in self.hooks:
            return None
        return (
            f"cites pre-commit hook `{token}`, which is not configured in "
            f".pre-commit-config.yaml. If it is not a hook id, add it to "
            f"_NOT_HOOK_IDS with a reason"
        )


def _citations(mechanism: str) -> Iterable[str]:
    return (t.strip() for t in _TICKED.findall(mechanism))


def _classify_bare_row(
    row: tuple[str, str, str],
    pending: Pending,
    buckets: tuple[list[str], list[str], list[str]],
) -> None:
    """Sort a row whose mechanism cell cites nothing into one of three buckets."""
    ident, mechanism, full_row = row
    errors, unenforced, deferred = buckets
    if _UNENFORCED.search(full_row):
        unenforced.append(ident)
    elif pending.is_pending(ident):
        deferred.append(ident)
    else:
        errors.append(
            f"{ident}: mechanism cell names nothing checkable, is not labelled "
            f"unenforced, and is not declared pending in {REGISTRY_NAME}: "
            f"{mechanism[:70]!r}"
        )


def check(root: Path = ROOT) -> list[str]:
    """Return every orphaned citation in the section 3 matrix."""
    doc = root / "docs" / "DbtBestPractices.md"
    if not doc.is_file():
        return [f"{rel(doc, root)} does not exist -- 3.39 has no matrix to parse."]

    rows = _matrix_rows(doc.read_text(encoding="utf-8"))
    if len(rows) < MIN_ROWS:
        return [
            (
                f"parsed only {len(rows)} matrix row(s), expected at least {MIN_ROWS}. "
                f"The extractor has broken, not the repository -- a matrix parser that "
                f"matches nothing exits 0 and reads as a pass."
            )
        ]

    wiring = _Wiring.load(root)
    pending = Pending("check_standards_matrix.py", root=root)

    errors: list[str] = list(pending.errors())
    resolved = 0
    unenforced: list[str] = []
    deferred: list[str] = []
    previous_tokens: list[str] = []

    for ident, mechanism, full_row in rows:
        tokens = list(_citations(mechanism))
        if not tokens and _CROSS_REF.match(mechanism):
            # "Same script" / "3.11 + 3.12 together": the mechanism is the one
            # above, named by reference rather than repeated.
            tokens = previous_tokens
        if not tokens:
            _classify_bare_row(
                (ident, mechanism, full_row), pending, (errors, unenforced, deferred)
            )
            continue
        previous_tokens = tokens
        row_problems = [
            problem for token in tokens if (problem := wiring.classify(token)) is not None
        ]
        if row_problems and pending.is_pending(ident):
            deferred.append(ident)
            continue
        if not row_problems and pending.is_pending(ident):
            errors.append(
                f"{REGISTRY_NAME} still lists {ident} as pending, but every mechanism "
                f"it names now resolves. Remove the entry."
            )
        errors.extend(f"{ident}: {problem}" for problem in row_problems)
        resolved += len(tokens) - len(row_problems)

    # The reverse direction. 3.39 as written asks "does every row name a real
    # mechanism?" -- which cannot see a standard that is *enforced* and has no
    # row at all. 3.72 was exactly that for one step: a registered injection, a
    # compile gate that fired, and nothing in the matrix. Same both-directions
    # argument as 3.49, one level up.
    declared = {ident for ident, _, _ in rows}
    errors.extend(
        f"{standard}: verify_gates.py registers an injection for it, but the "
        f"section 3 matrix has no row. A standard that is enforced and "
        f"undocumented cannot be reviewed, amended or retired (section 23)."
        for standard in sorted(_injected_standards(root) - declared)
    )

    if len(unenforced) > MAX_UNENFORCED:
        errors.append(
            f"{len(unenforced)} row(s) are labelled unenforced ({', '.join(unenforced)}), "
            f"cap is {MAX_UNENFORCED}. Marking a row unenforced must not become the "
            f"way to satisfy 3.39."
        )
    if resolved < MIN_CITATIONS and not errors:
        errors.append(
            f"only {resolved} citation(s) resolved, expected at least {MIN_CITATIONS}. "
            f"A check that matches nothing is not a check that passed."
        )

    sys.stdout.write(
        f"3.39: {len(rows)} matrix row(s), {resolved} citation(s) resolved, "
        f"{len(unenforced)} labelled unenforced.\n"
    )
    return errors


def main() -> int:
    """Return 0 when every mechanism in the section 3 matrix is real."""
    errors = check()
    for err in errors:
        sys.stderr.write(f"ERROR: {err}\n")
    if errors:
        sys.stderr.write(f"\n{len(errors)} orphaned mechanism(s) in the section 3 matrix (3.39).\n")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
