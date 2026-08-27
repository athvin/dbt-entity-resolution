"""3.57's own requirement, applied to 3.83: the check must be shown to fail.

D.0 finding 69 is the defect this guards, and it is worth restating why it is
not an ordinary bug. `make ci` was **exit 0** while CI was red on precisely the
thing that had just been fixed. §17's promise -- *"every Make target is also a
CI step"* -- is what makes a local green run count as evidence; when the two
copies drift, the local result stops being incomplete and becomes **misleading**.

The tests below therefore cover both directions, because a parity check has two
ways to be useless and only one of them looks like failure:

  * it does not fire on real drift (`test_catches_*`) -- the obvious one
  * it fires on differences that are legitimate (`test_tolerates_*`) -- the one
    that gets a check deleted, because output nobody trusts is output nobody
    reads

and one that is neither: comparing **nothing at all** and reporting success,
which is §6.1's vacuous pass and the reason `MIN_COMMANDS_COMPARED` exists.
"""

from __future__ import annotations

import pathlib
import re
import sys
from collections.abc import Callable

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import check_ci_makefile_parity as parity  # noqa: E402

Mutate = Callable[[str], str]


@pytest.fixture
def repo(tmp_path: pathlib.Path) -> Callable[[Mutate, Mutate], list[str]]:
    """Copy the real workflow and Makefile, mutate them, and run the check."""

    def _run(on_workflow: Mutate, on_makefile: Mutate) -> list[str]:
        workflow = tmp_path / parity.WORKFLOW
        makefile = tmp_path / parity.MAKEFILE
        workflow.parent.mkdir(parents=True, exist_ok=True)
        workflow.write_text(
            on_workflow((ROOT / parity.WORKFLOW).read_text(encoding="utf-8")), encoding="utf-8"
        )
        makefile.write_text(
            on_makefile((ROOT / parity.MAKEFILE).read_text(encoding="utf-8")), encoding="utf-8"
        )
        return parity.check(tmp_path)

    return _run


def _same(text: str) -> str:
    return text


def test_the_repository_as_it_stands_passes(repo: Callable[..., list[str]]) -> None:
    """The baseline. Without this the failing cases prove nothing."""
    assert repo(_same, _same) == []


def test_catches_the_actual_finding_69_drift(repo: Callable[..., list[str]]) -> None:
    """The regression that produced the finding, reproduced exactly.

    Fixing `make lint` to parse the runnable project left the workflow parsing
    the package root. Not a hypothetical mutation -- this is the diff that
    happened.
    """
    errors = repo(
        lambda t: t.replace("uv run dbt parse --project-dir integration_tests", "uv run dbt parse"),
        _same,
    )
    assert len(errors) == 1
    assert "dbt parse" in errors[0]
    # The message must localise: naming BOTH sides is what makes it actionable.
    assert "<package root>" in errors[0]
    assert "integration_tests" in errors[0]


def test_catches_drift_in_the_other_direction(repo: Callable[..., list[str]]) -> None:
    """Whichever copy moves, the check fires. Asymmetry here would be a hole."""
    errors = repo(
        _same,
        lambda t: t.replace("$(DBT) parse $(IT)", "$(DBT) parse"),
    )
    assert len(errors) == 1
    assert "dbt parse" in errors[0]


def test_catches_a_lint_pointed_at_the_wrong_path(repo: Callable[..., list[str]]) -> None:
    """The second half of finding 69: `sqlfluff lint` pointed elsewhere.

    §11's `.sqlfluffignore` and the ≤2 exemption cap are both scoped to a path;
    linting a different tree silently applies neither.
    """
    errors = repo(
        lambda t: t.replace(
            "integration_tests/dbt_packages/dbt_er/models", "integration_tests/models"
        ),
        _same,
    )
    assert len(errors) == 1
    assert "sqlfluff lint" in errors[0]


def test_tolerates_ci_only_output_formatting(repo: Callable[..., list[str]]) -> None:
    """CI annotates; a Makefile should not. That difference is CORRECT.

    `--format github-annotation-native` exists so failures surface on the diff.
    A check that demanded byte equality would flag it, and the fix a reviewer
    would reach for is to delete the check.
    """
    assert (
        repo(
            lambda t: t.replace(
                "--format github-annotation-native",
                "--format github-annotation-native --nofail",
            ),
            _same,
        )
        == []
    )


def test_tolerates_a_step_that_exists_in_only_one_place(repo: Callable[..., list[str]]) -> None:
    """CI has setup, caching and matrix steps no Makefile carries.

    Only a command present in BOTH can have drifted; a command in one place is
    a design choice, not a defect.
    """
    assert repo(lambda t: t + "\n      - run: uv run dbt deps --project-dir other\n", _same) == []


def test_a_makefile_comment_is_not_an_invocation(repo: Callable[..., list[str]]) -> None:
    """The first version of this check failed here, against the real repository.

    The Makefile's own comment -- "section 17 writes this as `sqlfluff lint
    models tests`" -- matched as though it were a command.
    """
    assert repo(_same, lambda t: t + "\n# sqlfluff lint some/other/path\n") == []


def test_comparing_nothing_is_not_a_pass(repo: Callable[..., list[str]]) -> None:
    """§6.1's vacuous pass: an extractor that matches nothing agrees with everything.

    This is the failure mode that would let 3.83 rot silently -- rename a
    command, and a check that compares zero commands still exits 0.
    """
    errors = repo(lambda t: t.replace("dbt parse", "dbt xparse"), _same)
    assert any("extractor has broken" in e for e in errors)


def test_a_variable_exported_only_by_the_makefile_is_caught(
    repo: Callable[..., list[str]],
) -> None:
    """D.0 finding 89 — the third Makefile/CI divergence, in a new channel.

    3.83 shipped comparing drift-prone COMMANDS. Stage 4 introduced
    `DBT_ER_COMPARISONS`, exported by the Makefile and absent from the workflow,
    and `make ci` was exit 0 while CI failed three jobs on `ER-071`. A parity
    check scoped to one kind of drift finds one kind of drift.

    Exercised here on `DBT_ER_THRESHOLDS`, because the variable that prompted it
    no longer exists: the eventual fix removed the channel rather than
    duplicating it. The arm it added is what stays.
    """
    errors = repo(
        lambda t: re.sub(r"  DBT_ER_THRESHOLDS: [^\n]*\n", "", t),
        _same,
    )
    assert len(errors) == 1
    assert "DBT_ER_THRESHOLDS" in errors[0]


def test_a_variable_only_ci_sets_is_not_a_divergence(
    repo: Callable[..., list[str]],
) -> None:
    """The converse is deliberately allowed.

    CI legitimately sets variables a local run has no use for -- tokens, runner
    hints, annotation formats. Requiring symmetry there would be the noise that
    gets a parity check skipped, which is 3.83's own founding lesson.
    """
    assert (
        repo(
            lambda t: t.replace(
                "  DBT_ER_THRESHOLDS:", "  DBT_ER_CI_ONLY: 'x'\n  DBT_ER_THRESHOLDS:"
            ),
            _same,
        )
        == []
    )
