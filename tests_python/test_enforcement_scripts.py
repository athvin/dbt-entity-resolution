"""Failing-case tests for the enforcement scripts (3.57).

3.57 requires every enforcement script to ship a test "positively and
negatively", and section 0 says why the negative half is the one that matters:
``[VERIFIED]`` means *observed to pass*, never *observed to fail when violated*,
and only the second proves a gate is enforcing anything.

So every test here does two things: assert the script passes on a correct tree,
and assert it **fails on a specific injected violation, with a message that names
what is wrong**. A check that fails for the wrong reason is still broken, and it
is the harder defect to notice later -- which is the same argument 3.38 makes for
``verify_gates.py`` at the whole-matrix level.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import check_flags_parity  # noqa: E402
import check_no_nondeterminism  # noqa: E402
import check_root_packages_minimal  # noqa: E402
import check_workflow_hardening  # noqa: E402
import check_yml_pairing  # noqa: E402


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """Build a minimal two-root project tree that satisfies every check."""
    for root in (tmp_path, tmp_path / "integration_tests"):
        (root / "models" / "intermediate").mkdir(parents=True)
        (root / "models" / "intermediate" / "er_x.sql").write_text("select 1 as a\n")
        (root / "models" / "intermediate" / "er_x.yml").write_text("---\nversion: 2\n")
    return tmp_path


# --------------------------------------------------------------------------
# 3.1 / 3.2 -- the 1:1 pairing rule
# --------------------------------------------------------------------------


def test_pairing_passes_on_a_correct_tree(project: Path) -> None:
    assert check_yml_pairing.check((project, project / "integration_tests")) == []


def test_pairing_fails_on_a_missing_properties_file(project: Path) -> None:
    (project / "models" / "intermediate" / "er_x.yml").unlink()
    errors = check_yml_pairing.check((project, project / "integration_tests"))
    assert errors, "a .sql with no sibling .yml must be a violation"
    assert any("missing properties file" in e and "er_x.yml" in e for e in errors)


def test_pairing_fails_on_an_orphan_properties_file(project: Path) -> None:
    (project / "models" / "intermediate" / "er_orphan.yml").write_text("---\n")
    errors = check_yml_pairing.check((project, project / "integration_tests"))
    assert any("orphan properties file" in e and "er_orphan.yml" in e for e in errors)


def test_pairing_allows_underscore_prefixed_non_resource_yaml(project: Path) -> None:
    (project / "models" / "_er_groups.yml").write_text("---\n")
    assert check_yml_pairing.check((project, project / "integration_tests")) == []


def test_pairing_rejects_yaml_extension(project: Path) -> None:
    (project / "models" / "intermediate" / "er_y.sql").write_text("select 1 as a\n")
    (project / "models" / "intermediate" / "er_y.yaml").write_text("---\n")
    errors = check_yml_pairing.check((project, project / "integration_tests"))
    assert any("use .yml, not .yaml" in e for e in errors)


def test_pairing_covers_the_second_project_root(project: Path) -> None:
    """Section 6.1's blind spot 2: the v1 script skipped integration_tests entirely."""
    (project / "integration_tests" / "models" / "intermediate" / "er_x.yml").unlink()
    errors = check_yml_pairing.check((project, project / "integration_tests"))
    assert any("integration_tests" in e for e in errors), (
        "3.1 covers BOTH project roots; a violation in the second must be found"
    )


def test_pairing_fails_loudly_when_it_has_nothing_to_check(tmp_path: Path) -> None:
    """Section 6.1's blind spot 1: a check whose subject disappeared must not pass."""
    empty = tmp_path / "empty"
    empty.mkdir()
    errors = check_yml_pairing.check((empty,))
    assert errors, "walking an empty tree must be a finding, not a pass"
    assert any("nothing to check" in e for e in errors)


# --------------------------------------------------------------------------
# 3.16 -- the non-determinism lint
# --------------------------------------------------------------------------


def test_nondeterminism_passes_on_clean_sql(project: Path) -> None:
    assert check_no_nondeterminism.check((project,)) == []


def test_nondeterminism_fails_on_tier_1_set(project: Path) -> None:
    (project / "models" / "intermediate" / "er_x.sql").write_text(
        "{% set cols = set(['a', 'b']) %}\nselect 1 as a\n"
    )
    errors = check_no_nondeterminism.check((project,))
    assert any("[tier 1]" in e and "set()" in e for e in errors)


def test_nondeterminism_fails_on_tier_2_invocation_id(project: Path) -> None:
    (project / "models" / "intermediate" / "er_x.sql").write_text(
        "select '{{ invocation_id }}' as run\n"
    )
    errors = check_no_nondeterminism.check((project,))
    assert any("[tier 2]" in e and "invocation_id" in e for e in errors)


def test_nondeterminism_exempts_only_listed_files_from_tier_2(project: Path) -> None:
    exempt = project / "macros" / "model_json"
    exempt.mkdir(parents=True)
    (exempt / "er_load_model_json.sql").write_text(
        "{% macro er_load_model_json() %}{{ env_var('DBT_ER_MODEL_JSON') }}{% endmacro %}\n"
    )
    (exempt / "er_load_model_json.yml").write_text("---\n")
    assert check_no_nondeterminism.check((project,)) == []


def test_nondeterminism_tier_1_has_no_exemption(project: Path) -> None:
    """The two tiers exist so a tier-2 exemption cannot smuggle tier 1 through."""
    exempt = project / "macros" / "model_json"
    exempt.mkdir(parents=True)
    (exempt / "er_load_model_json.sql").write_text("{% set x = set(['a']) %}\n")
    (exempt / "er_load_model_json.yml").write_text("---\n")
    errors = check_no_nondeterminism.check((project,))
    assert any("[tier 1]" in e for e in errors), "tier 1 is banned everywhere, no exemptions"


def test_nondeterminism_ignores_prose_in_jinja_comments(project: Path) -> None:
    """Section 11.3: prose explaining the ban must not trip it."""
    (project / "models" / "intermediate" / "er_x.sql").write_text(
        "{#- never use set() here, it is randomised -#}\nselect 1 as a\n"
    )
    assert check_no_nondeterminism.check((project,)) == []


# --------------------------------------------------------------------------
# 3.22 -- flags parity, and 3.29 -- the shipped surface
# --------------------------------------------------------------------------


def test_flags_parity_detects_a_differing_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pkg = tmp_path / "dbt_project.yml"
    itg_dir = tmp_path / "integration_tests"
    itg_dir.mkdir()
    itg = itg_dir / "dbt_project.yml"
    pkg.write_text("---\nflags:\n  validate_macro_args: true\n")
    itg.write_text("---\nflags:\n  validate_macro_args: false\n")
    monkeypatch.setattr(check_flags_parity, "ROOT", tmp_path)
    monkeypatch.setattr(check_flags_parity, "PACKAGE", pkg)
    monkeypatch.setattr(check_flags_parity, "INTEGRATION", itg)
    errors = check_flags_parity.check()
    assert any("validate_macro_args" in e and "differs" in e for e in errors)


def test_flags_parity_detects_a_missing_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pkg = tmp_path / "dbt_project.yml"
    itg_dir = tmp_path / "integration_tests"
    itg_dir.mkdir()
    itg = itg_dir / "dbt_project.yml"
    pkg.write_text("---\nflags:\n  a: true\n  b: true\n")
    itg.write_text("---\nflags:\n  a: true\n")
    monkeypatch.setattr(check_flags_parity, "ROOT", tmp_path)
    monkeypatch.setattr(check_flags_parity, "PACKAGE", pkg)
    monkeypatch.setattr(check_flags_parity, "INTEGRATION", itg)
    errors = check_flags_parity.check()
    assert any("`b`" in e for e in errors)


def test_root_packages_rejects_an_extra_package(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pkgs = tmp_path / "packages.yml"
    pkgs.write_text(
        "---\npackages:\n"
        "  - package: dbt-labs/dbt_utils\n    version: ['>=1.4.1', '<2.0.0']\n"
        "  - package: dbt-labs/codegen\n    version: 0.13.1\n"
    )
    monkeypatch.setattr(check_root_packages_minimal, "PACKAGES", pkgs)
    errors = check_root_packages_minimal.check()
    assert any("codegen" in e for e in errors)


def test_root_packages_rejects_a_local_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A local path cannot resolve in a consumer's project."""
    pkgs = tmp_path / "packages.yml"
    pkgs.write_text("---\npackages:\n  - local: ../\n")
    monkeypatch.setattr(check_root_packages_minimal, "PACKAGES", pkgs)
    errors = check_root_packages_minimal.check()
    assert any("local" in e for e in errors)


def test_root_packages_accepts_the_shipped_surface(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pkgs = tmp_path / "packages.yml"
    shutil.copyfile(ROOT / "packages.yml", pkgs)
    monkeypatch.setattr(check_root_packages_minimal, "PACKAGES", pkgs)
    assert check_root_packages_minimal.check() == []


# --------------------------------------------------------------------------
# 3.56 -- workflow hardening
# --------------------------------------------------------------------------


@pytest.fixture
def workflows(tmp_path: Path) -> Path:
    """Build a workflow directory that satisfies every hardening rule."""
    wf = tmp_path / "workflows"
    wf.mkdir()
    (wf / "ci.yml").write_text(
        "---\n"
        "on:\n  pull_request: {}\n"
        "permissions:\n  contents: read\n"
        "jobs:\n"
        "  lint:\n"
        "    permissions:\n      contents: read\n"
        "    steps:\n"
        "      - uses: actions/checkout@" + ("a" * 40) + "\n"
        "        with:\n          persist-credentials: false\n"
    )
    return wf


def test_workflow_hardening_passes_on_a_clean_workflow(workflows: Path) -> None:
    assert check_workflow_hardening.check(workflows) == []


def test_workflow_hardening_rejects_a_mutable_tag(workflows: Path) -> None:
    p = workflows / "ci.yml"
    p.write_text(p.read_text().replace("a" * 40, "v5"))
    errors = check_workflow_hardening.check(workflows)
    assert any("40-character commit SHA" in e for e in errors)


def test_workflow_hardening_rejects_persisted_credentials(workflows: Path) -> None:
    p = workflows / "ci.yml"
    p.write_text(p.read_text().replace("persist-credentials: false", "fetch-depth: 0"))
    errors = check_workflow_hardening.check(workflows)
    assert any("persist-credentials" in e for e in errors)


def test_workflow_hardening_rejects_a_job_without_permissions(workflows: Path) -> None:
    p = workflows / "ci.yml"
    p.write_text(p.read_text().replace("    permissions:\n      contents: read\n", "", 1))
    errors = check_workflow_hardening.check(workflows)
    assert any("declares no `permissions:`" in e for e in errors)


def test_workflow_hardening_rejects_pull_request_target(workflows: Path) -> None:
    """Section 15's stated position is never. C.7's GITHUB_ENV heredoc is why."""
    p = workflows / "ci.yml"
    p.write_text(p.read_text().replace("  pull_request: {}", "  pull_request_target: {}"))
    errors = check_workflow_hardening.check(workflows)
    assert any("pull_request_target" in e for e in errors)


def test_workflow_hardening_fails_when_there_are_no_workflows(tmp_path: Path) -> None:
    """A check whose subject has disappeared must not report success."""
    empty = tmp_path / "none"
    empty.mkdir()
    errors = check_workflow_hardening.check(empty)
    assert any("nothing to check" in e for e in errors)
