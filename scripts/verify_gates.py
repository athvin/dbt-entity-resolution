#!/usr/bin/env python3
"""Prove every standard FAILS when violated (3.38).

Appendix D calls this "the most valuable missing piece" and records that it was
designed but never written. Section 0 says why it matters: ``[VERIFIED]`` means
*observed to pass*, and **never** *observed to fail when violated*. Those are
different claims, and only the second establishes that a gate enforces anything.

Method, per 3.38: copy the repository to a scratch directory, inject each
violation from the section 3 matrix, and assert **a non-zero exit and the
expected error string**. The string assertion is the part v1's phrasing omitted
-- a check that fails for the wrong reason is still broken, and it is the harder
defect to notice later, because the gate looks like it is working.

This script also reports which standards have **no injection registered**, so
the gap between "the matrix" and "the matrix we have shown to fire" is a number
rather than an impression. Section 23 requires every new standard to ship its
injection in the same PR; Waiver B-1 (Appendix D.1) covers the bootstrap
interval, and this report is what makes that interval visible.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Trees that must not be copied: build output, virtualenvs, and git history.
#
# `dbt_packages` is deliberately KEPT. Excluding it meant every scratch copy
# needed its own `dbt deps`, and dbt refuses to run without it -- so each
# injection would have failed on a missing dependency rather than on the
# violation, which is precisely the "fails for the wrong reason" defect this
# script exists to catch.
_EXCLUDE = {
    ".git",
    ".venv",
    "target",
    "logs",
    "exports",
    ".duckdb_tmp",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
}

MODEL_SQL = Path("models/intermediate/er_thresholds.sql")
MODEL_YML = Path("models/intermediate/er_thresholds.yml")
DOC = Path("docs/DbtBestPractices.md")

# A lint threshold, not a production value. DR-22 removed the default because
# the one that existed measurably cost ~330 true pairs for zero precision gain.
THRESHOLDS = '[{"auto_merge":0.9}]'


@dataclass(frozen=True)
class Injection:
    """One standard, one violation, and the string its gate must emit."""

    standard: str
    what: str
    mutate: str
    command: tuple[str, ...]
    expect: str
    cwd: str = "."
    edits: tuple[tuple[str, str, str], ...] = field(default=())


# The compile gate runs from `on-run-start`, and `dbt parse` does NOT execute
# hooks -- so `dbt parse` cannot be used to provoke it. C.7 carries an explicit
# `dbt run-operation er_assert_project_standards` step for exactly this reason,
# and that is what these injections invoke.
GATE = ("dbt", "run-operation", "er_assert_project_standards", "--project-dir", "integration_tests")


INJECTIONS: tuple[Injection, ...] = (
    Injection(
        standard="3.1",
        what="delete a model's colocated properties file",
        mutate="unlink:" + str(MODEL_YML),
        command=("python", "scripts/check_yml_pairing.py"),
        expect="missing properties file",
    ),
    Injection(
        standard="3.2",
        what="add a properties file with no sibling .sql",
        mutate="write:models/intermediate/er_orphan.yml:---\nversion: 2\n",
        command=("python", "scripts/check_yml_pairing.py"),
        expect="orphan properties file",
    ),
    Injection(
        standard="3.3",
        what="set contract.enforced false on a model",
        mutate="noop",
        command=GATE,
        expect="contract.enforced is false",
        edits=(
            (
                str(MODEL_YML),
                "      contract:\n        enforced: true",
                "      contract:\n        enforced: false",
            ),
        ),
    ),
    Injection(
        standard="3.4",
        what="blank a column description",
        mutate="noop",
        command=GATE,
        expect="column description missing or too short",
        edits=(
            (
                str(MODEL_YML),
                "        description: >\n          The lower edge of the gray band.",
                (
                    '        description: "x"\n        _unused: >\n'
                    "          The lower edge of the gray band."
                ),
            ),
        ),
    ),
    Injection(
        standard="3.5",
        what="remove a required description section heading",
        mutate="noop",
        command=GATE,
        expect="description is missing the **Caveats** section",
        edits=((str(MODEL_YML), "**Caveats**", "Caveats"),),
    ),
    Injection(
        standard="3.6",
        what="remove the primary_key constraint",
        mutate="noop",
        command=GATE,
        expect="no primary_key constraint",
        # The anchor moved when the key moved from column level to model level
        # (section 8.3 -- a column-level primary_key is invisible to
        # check_model_has_constraints). The registry breaking loudly on that is
        # the behaviour wanted: an injection whose anchor has drifted is an
        # injection that is no longer testing what it claims.
        edits=(
            (
                str(MODEL_YML),
                "    constraints:\n      - type: primary_key\n        columns: [thr_auto_merge]\n",
                "",
            ),
        ),
    ),
    Injection(
        standard="3.11",
        what="set an unpermitted materialization (incremental, not view)",
        mutate="noop",
        command=GATE,
        # Isolating this gate took three attempts, and what it took is the
        # finding. dbt-core 1.12.2 rejects every materialization our policy
        # forbids BEFORE our gate sees it, for a model that is contracted and
        # carries constraints -- which every model here is (3.3, 3.6):
        #
        #   view        -> "Constraint types are not supported for view
        #                   materializations", a WARNING that error: all turns
        #                   into an error. That is 3.13's point exactly, and it
        #                   proves 3.21 rather than 3.11.
        #   incremental -> "must set on_schema_change to 'append_new_columns'
        #                   or 'fail'".
        #
        # So `on_schema_change: fail` is set here purely to get past dbt's own
        # validation and reach ours. 3.11 is therefore SHADOWED in practice on
        # this project: dbt catches the same class of mistake first, and our
        # gate is the backstop for the case dbt does not -- an uncontracted or
        # constraint-free model. That is worth knowing rather than assuming the
        # gate is the first line of defence.
        expect="but policy allows only table",
        edits=(
            (
                str(MODEL_YML),
                "    config:\n      contract:",
                (
                    "    config:\n      materialized: incremental\n      "
                    "on_schema_change: fail\n      contract:"
                ),
            ),
        ),
    ),
    Injection(
        standard="3.16",
        what="use set() in model Jinja",
        mutate="append:" + str(MODEL_SQL) + ":\n{% set x = set(['a']) %}\n",
        command=("python", "scripts/check_no_nondeterminism.py"),
        expect="[tier 1]",
    ),
    Injection(
        standard="3.20",
        what="remove a model's unit tests",
        mutate="truncate_after:" + str(MODEL_YML) + ":unit_tests:",
        command=GATE,
        expect="no unit test",
    ),
    Injection(
        standard="3.22",
        what="make the two flags: blocks disagree",
        mutate="noop",
        command=("python", "scripts/check_flags_parity.py"),
        expect="differs",
        edits=(
            (
                "integration_tests/dbt_project.yml",
                "  validate_macro_args: true",
                "  validate_macro_args: false",
            ),
        ),
    ),
    Injection(
        standard="3.29",
        what="add a package to the shipped surface",
        mutate="append:packages.yml:  - package: dbt-labs/codegen\n    version: 0.13.1\n",
        command=("python", "scripts/check_root_packages_minimal.py"),
        expect="is not in the allowed shipped surface",
    ),
    Injection(
        standard="3.33",
        what="name a model without the er_ prefix",
        mutate="rename_model:er_thresholds:thresholds",
        command=GATE,
        expect="model names carry the `er_` prefix",
    ),
    Injection(
        standard="3.42",
        what="apply a tag outside the governed vocabulary",
        mutate="noop",
        command=GATE,
        expect="is outside er_allowed_tags",
        edits=(
            (
                "dbt_project.yml",
                '      +tags: ["parity"]',
                '      +tags: ["parity", "not_a_governed_tag"]',
            ),
        ),
    ),
    Injection(
        standard="3.56",
        what="unpin a CI action to a mutable tag",
        mutate="noop",
        command=("python", "scripts/check_workflow_hardening.py"),
        expect="40-character commit SHA",
        edits=(
            (
                ".github/workflows/ci.yml",
                ("actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09  # v5"),
                "actions/checkout@v5",
            ),
        ),
    ),
    Injection(
        standard="3.72",
        what="nest a unit_tests: block under a models: entry",
        mutate="nest_unit_tests:" + str(MODEL_YML),
        command=GATE,
        expect="no unit test",
    ),
    Injection(
        standard="3.72",
        what="nest a unit_tests: block -- 3.72's own mechanism, not the consequence",
        mutate="nest_unit_tests:" + str(MODEL_YML),
        command=("python", "scripts/check_unit_test_fixtures.py"),
        expect="nested `unit_tests:` key",
    ),
    Injection(
        standard="3.46",
        what="coerce match_key before comparing -- the comparator can no longer fail",
        mutate="noop",
        command=("pytest", "harness", "-q", "--no-header", "-x"),
        expect="MUTANT SURVIVED",
        edits=(
            (
                "harness/comparators.py",
                "            if got == want and type(got) is type(want):",
                "            if str(got) == str(want):",
            ),
        ),
    ),
    Injection(
        standard="3.73",
        what="restore a second copy of a live config file into the document",
        mutate="noop",
        command=("python", "scripts/check_canonical_homes.py"),
        expect="which EXISTS in the repository",
        edits=(
            (
                str(DOC),
                "**Canonical: [`packages.yml`](../packages.yml).**",
                (
                    "```yaml\npackages:\n  - package: dbt-labs/dbt_utils\n```\n\n"
                    "**Canonical: [`packages.yml`](../packages.yml).**"
                ),
            ),
        ),
    ),
    Injection(
        standard="3.39",
        what="delete a script the section 3 matrix names as a mechanism",
        mutate="unlink:scripts/check_verified_markers.py",
        command=("python", "scripts/check_standards_matrix.py"),
        expect="which does not exist at",
    ),
    Injection(
        standard="3.44",
        what="move a pin away from the toolchain the markers were earned on",
        mutate="noop",
        command=("python", "scripts/check_verified_markers.py"),
        expect="DEMOTED to [UNVERIFIED]",
        edits=((str(DOC), "yamllint 1.38.0 ·", "yamllint 1.37.0 ·"),),
    ),
    Injection(
        standard="3.49",
        what="pin a divergence with a test and never log it",
        mutate=(
            "write:tests/divergence/test_div_99_unlogged.sql:"
            "-- DIV-99: a divergence pinned by a test and recorded nowhere.\n"
            "select 1 as never_logged\n"
        ),
        command=("python", "scripts/check_divergence_log.py"),
        expect="pinned and unrecorded",
    ),
    Injection(
        standard="3.50",
        what="stop declaring PARITY.md pending while it still does not exist",
        mutate="noop",
        command=("python", "scripts/check_divergence_log.py"),
        expect="not declared pending (3.50",
        edits=(
            (
                "scripts/pending_subjects.yml",
                "  - path: docs/PARITY.md\n    check: check_divergence_log.py",
                "  - path: docs/PARITY.md.disabled\n    check: check_divergence_log.py",
            ),
        ),
    ),
    Injection(
        standard="3.74",
        what="project more pairs than the derived budget allows",
        mutate="noop",
        command=(
            "dbt",
            "run-operation",
            "er_assert_pair_budget",
            "--args",
            "{projected_pairs: 999999999999}",
            "--project-dir",
            "integration_tests",
        ),
        expect="ER-021",
    ),
    Injection(
        standard="3.59",
        what="change one bit of the pinned float reference",
        mutate="noop",
        command=("pytest", "harness/test_float_parity.py", "-q", "--no-header"),
        expect="does not hold across platforms",
        edits=(
            (
                "harness/float_probe.py",
                '"match_weight": "40345d48400a308f"',
                '"match_weight": "40345d48400a308e"',
            ),
        ),
    ),
    Injection(
        standard="3.55",
        what="put a consumer email provider in a fixture",
        mutate="append:fixtures/degenerate/single_row.csv:"
        "a-9,Real,Person,1980-01-01,Leeds,real.person@gmail.com\n",
        command=("python", "scripts/check_pii_heuristics.py"),
        expect="consumer provider",
    ),
    Injection(
        standard="3.62",
        what="add a baseline with no provenance sidecar",
        mutate="write:fixtures/fake_1000/baseline_edges.parquet:not-really-parquet",
        command=("python", "scripts/check_baseline_manifests.py"),
        expect="no sidecar at",
    ),
    Injection(
        standard="3.62",
        what="tamper with a vendored fixture, leaving its recorded sha256 in place",
        mutate="append:fixtures/source/fake_1000.csv:tampered,row,here,,,,\n",
        command=("python", "scripts/check_baseline_manifests.py"),
        expect="unattributable",
    ),
    Injection(
        standard="3.69",
        what="declare a unit-test fixture `format: dict`",
        mutate="noop",
        command=("python", "scripts/check_unit_test_fixtures.py"),
        expect="Only `format: sql` is permitted",
        edits=((str(MODEL_YML), "      format: sql", "      format: dict"),),
    ),
)


def _copy_repo(dest: Path) -> None:
    """Copy the repository, and re-point the package symlink at the copy.

    `dbt deps` installs a `local:` package as a SYMLINK to the absolute path of
    the source repository -- so a plain copy leaves
    `integration_tests/dbt_packages/dbt_er` pointing back at the LIVE repository.
    dbt would then read the unmutated package, every injection would have no
    effect, and this script would report that no gate fires while actually having
    tested nothing.

    That failure is silent and self-consistent, which makes it exactly the class
    of defect 3.38 exists to expose -- here, in the tool that exists to expose it.
    """

    def ignore(_src: str, names: list[str]) -> set[str]:
        return {n for n in names if n in _EXCLUDE}

    shutil.copytree(ROOT, dest, ignore=ignore, symlinks=True)

    for link in (dest / "integration_tests" / "dbt_packages").glob("*"):
        if link.is_symlink() and link.readlink() == ROOT:
            link.unlink()
            link.symlink_to(dest, target_is_directory=True)


def _apply(scratch: Path, inj: Injection) -> None:
    """Apply one injection's mutation to the scratch copy."""
    for rel, old, new in inj.edits:
        path = scratch / rel
        text = path.read_text(encoding="utf-8")
        if old not in text:
            msg = f"{inj.standard}: injection anchor not found in {rel}: {old!r}"
            raise LookupError(msg)
        path.write_text(text.replace(old, new, 1), encoding="utf-8")

    op, _, rest = inj.mutate.partition(":")
    if op == "noop":
        return
    _mutate(scratch, op, rest)


def _mutate(scratch: Path, op: str, rest: str) -> None:
    """Apply one non-edit mutation op to the scratch copy."""
    if op == "unlink":
        (scratch / rest).unlink()
    elif op == "rename":
        src, _, dst = rest.partition(":")
        (scratch / src).rename(scratch / dst)
    elif op == "write":
        rel, _, body = rest.partition(":")
        # Parents are created: several injections write into a directory the
        # repository does not have yet (`tests/`, `fixtures/`), which is the
        # whole point of the checks that guard them.
        (scratch / rel).parent.mkdir(parents=True, exist_ok=True)
        (scratch / rel).write_text(body, encoding="utf-8")
    elif op == "append":
        rel, _, body = rest.partition(":")
        with (scratch / rel).open("a", encoding="utf-8") as fh:
            fh.write(body)
    elif op == "truncate_after":
        rel, _, marker = rest.partition(":")
        path = scratch / rel
        text = path.read_text(encoding="utf-8")
        path.write_text(text[: text.index(marker)], encoding="utf-8")
    elif op == "rename_model":
        old_name, _, new_name = rest.partition(":")
        base = scratch / "models" / "intermediate"
        for suffix in (".sql", ".yml"):
            (base / f"{old_name}{suffix}").rename(base / f"{new_name}{suffix}")
        yml = base / f"{new_name}.yml"
        yml.write_text(
            yml.read_text(encoding="utf-8").replace(old_name, new_name), encoding="utf-8"
        )
    elif op == "nest_unit_tests":
        path = scratch / rest
        text = path.read_text(encoding="utf-8")
        head, marker, tail = text.partition("unit_tests:")
        nested = "\n".join(f"    {ln}" if ln.strip() else ln for ln in (marker + tail).split("\n"))
        path.write_text(head.rstrip("\n") + "\n" + nested, encoding="utf-8")
    else:  # pragma: no cover - a typo in the registry, not a runtime path
        msg = f"unknown mutation op: {op}"
        raise ValueError(msg)


def _run(scratch: Path, inj: Injection) -> tuple[int, str]:
    env = dict(os.environ)
    env["DBT_PROFILES_DIR"] = str(scratch / "profiles")
    env["DBT_ER_THRESHOLDS"] = THRESHOLDS
    # DuckDB will not create a database in a directory that does not exist, and
    # `target/` is excluded from the copy on purpose -- a stale build artefact is
    # the likeliest source of a false green (section 15).
    (scratch / "target").mkdir(exist_ok=True)
    env["DBT_ER_DB_PATH"] = str(scratch / "target" / "verify.duckdb")
    # Resolve executables from THIS interpreter's environment. Prefixing with
    # `uv run` would make uv discover the scratch copy's pyproject.toml and
    # build a fresh virtualenv per injection -- minutes of work per gate, to
    # reproduce an environment that is already active.
    cmd = list(inj.command)
    bindir = Path(sys.executable).parent
    if (bindir / cmd[0]).exists():
        cmd[0] = str(bindir / cmd[0])
    proc = subprocess.run(  # noqa: S603
        cmd, cwd=scratch / inj.cwd, env=env, capture_output=True, text=True, check=False
    )
    return proc.returncode, proc.stdout + proc.stderr


def verify(only: str | None = None) -> tuple[int, int, list[str]]:
    """Run every registered injection. Returns (passed, failed, messages)."""
    passed = failed = 0
    messages: list[str] = []

    for inj in INJECTIONS:
        if only and inj.standard != only:
            continue
        with tempfile.TemporaryDirectory(prefix="er-verify-") as tmp:
            scratch = Path(tmp) / "repo"
            _copy_repo(scratch)
            try:
                _apply(scratch, inj)
            except LookupError as err:
                failed += 1
                messages.append(f"FAIL {inj.standard}: {err}")
                continue
            code, output = _run(scratch, inj)

        if code == 0:
            failed += 1
            messages.append(
                f"FAIL {inj.standard} ({inj.what}): the gate EXITED 0. "
                f"A standard that does not fail when violated is not enforced."
            )
        elif inj.expect and inj.expect not in output:
            failed += 1
            messages.append(
                f"FAIL {inj.standard} ({inj.what}): failed, but not for the stated "
                f"reason. Expected {inj.expect!r} in the output. A check that fails "
                f"for the wrong reason is still broken."
            )
        else:
            passed += 1
            messages.append(f"ok   {inj.standard}: {inj.what}")

    return passed, failed, messages


def _report(passed: int, failed: int, messages: list[str], registered: list[str]) -> None:
    """Print the human-readable summary."""
    for m in messages:
        sys.stdout.write(m + "\n")
    sys.stdout.write(f"\n{passed} gate(s) shown to fire, {failed} failure(s).\n")
    sys.stdout.write(
        f"{len(registered)} standard(s) have a registered injection: {', '.join(registered)}.\n"
    )
    sys.stdout.write(
        "Section 23 requires every standard to ship its injection in the same PR. "
        "Waiver B-1 (Appendix D.1) covers the remainder of the section 3 matrix "
        "until each mechanism exists, and expires when the matrix is COVERED -- "
        "not when this script exists.\n"
    )


def main() -> int:
    """Run the registered injections and report the unregistered remainder."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--standard", help="verify one standard only, e.g. 3.20")
    parser.add_argument("--json", action="store_true", help="machine-readable summary")
    args = parser.parse_args()

    passed, failed, messages = verify(args.standard)
    registered = sorted({i.standard for i in INJECTIONS})

    if args.json:
        payload = {"passed": passed, "failed": failed, "registered": registered}
        sys.stdout.write(json.dumps(payload) + "\n")
    else:
        _report(passed, failed, messages, registered)

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
