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

import json
import shutil
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import check_baseline_manifests  # noqa: E402
import check_bouncer_ran  # noqa: E402
import check_canonical_homes  # noqa: E402
import check_divergence_log  # noqa: E402
import check_flags_parity  # noqa: E402
import check_no_nondeterminism  # noqa: E402
import check_root_packages_minimal  # noqa: E402
import check_standards_matrix  # noqa: E402
import check_unit_test_fixtures  # noqa: E402
import check_verified_markers  # noqa: E402
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


# --------------------------------------------------------------------------
# 3.40 -- dbt-bouncer actually ran its checks
# --------------------------------------------------------------------------


def _bouncer_results(tmp_path: Path, outcome: str = "success", n: int = 25) -> Path:
    runs = [
        {
            "check_run_id": f"check_one_yml_per_sql:{i}:er_x",
            "outcome": outcome,
            "severity": "error",
        }
        for i in range(n)
    ]
    p = tmp_path / "bouncer.json"
    p.write_text(json.dumps(runs))
    return p


def test_bouncer_ran_passes_on_a_healthy_run(tmp_path: Path) -> None:
    assert check_bouncer_ran.check(_bouncer_results(tmp_path)) == []


def test_bouncer_ran_rejects_a_run_that_matched_nothing(tmp_path: Path) -> None:
    """The measured case: SUCCESS=0 WARN=2 ERROR=0 exits 0 (D.0 finding 20)."""
    p = tmp_path / "bouncer.json"
    p.write_text(json.dumps([]))
    errors = check_bouncer_ran.check(p)
    assert any("matches nothing" in e for e in errors)


def test_bouncer_ran_rejects_warned_checks(tmp_path: Path) -> None:
    """A check that RAISES is downgraded to a warning and the run stays green."""
    errors = check_bouncer_ran.check(_bouncer_results(tmp_path, outcome="warning"))
    assert any("'warning'" in e for e in errors)


def test_bouncer_ran_rejects_a_missing_custom_check(tmp_path: Path) -> None:
    """Section 6.2: an import failure is a WARNING that leaves the run green."""
    runs = [
        {"check_run_id": f"check_model_names:{i}:er_x", "outcome": "success"} for i in range(25)
    ]
    p = tmp_path / "bouncer.json"
    p.write_text(json.dumps(runs))
    errors = check_bouncer_ran.check(p)
    assert any("check_one_yml_per_sql" in e for e in errors)


def test_bouncer_ran_rejects_a_missing_results_file(tmp_path: Path) -> None:
    errors = check_bouncer_ran.check(tmp_path / "nope.json")
    assert any("does not exist" in e for e in errors)


# ---------------------------------------------------------------------------
# Helpers for the whole-repository checks.
#
# These four read the real tree rather than a synthetic one: 3.39 parses the
# section 3 matrix, 3.44 the section 4 pin table, and a fabricated stand-in
# would test the fabrication. So each test mirrors the repository into tmp_path
# and injects one defect -- the same shape as `verify_gates.py`, one layer down.
# ---------------------------------------------------------------------------

# Every path the whole-tree checks resolve against. `packages.yml`,
# `dbt_project.yml` and the rest are here because 3.73 fires only when the file
# a heading names actually EXISTS -- a mirror missing them makes its tests pass
# vacuously, which is the failure `pending_subjects.yml` guards one layer down.
_MIRROR = (
    "docs",
    "scripts",
    "models",
    "macros",
    "profiles",
    ".github",
    "dbt-bouncer.yml",
    "uv.lock",
    ".pre-commit-config.yaml",
    ".sqlfluff",
    ".sqlfluffignore",
    "packages.yml",
    "dbt_project.yml",
    "package-lock.yml",
    "integration_tests",
)


def _mirror(tmp_path: Path) -> Path:
    """Copy the parts of the repository the whole-tree checks read."""
    scratch = tmp_path / "repo"
    scratch.mkdir()
    for name in _MIRROR:
        src = ROOT / name
        if not src.exists():
            continue
        if src.is_dir():
            shutil.copytree(
                src,
                scratch / name,
                ignore=shutil.ignore_patterns(
                    "target", "dbt_packages", "__pycache__", "*.duckdb", "logs"
                ),
            )
        else:
            shutil.copy2(src, scratch / name)
    return scratch


def _write_log(scratch: Path, body: str) -> None:
    log = scratch / "docs" / "divergence-log.md"
    log.write_text(body, encoding="utf-8")
    _drop_pending(scratch, "docs/divergence-log.md")


def _drop_pending(scratch: Path, path: str) -> None:
    """Remove a pending entry, since the subject now exists."""
    reg = scratch / "scripts" / "pending_subjects.yml"
    lines = reg.read_text(encoding="utf-8").splitlines(keepends=True)
    out, skip = [], False
    for line in lines:
        if line.startswith(f"  - path: {path}"):
            skip = True
            continue
        if skip and (line.startswith("  - ") or not line.strip()):
            skip = False
        if not skip:
            out.append(line)
    reg.write_text("".join(out), encoding="utf-8")


def _complete_manifest() -> dict[str, Any]:
    return {
        "splink_version": "4.0.16",
        "model_json_sha256": "a" * 64,
        "seed": 42,
        "duckdb_version": "1.5.5",
        "sqlglot_version": "30.17.0",
        "platform": {"os": "linux", "architecture": "amd64", "duckdb_build": "v1.5.5"},
        "date": "2026-08-23",
        "producing_commit": "e3a9eb6",
    }


def _write_baseline(scratch: Path, manifest: dict[str, Any]) -> None:
    baseline = scratch / "fixtures" / "fake_1000" / "edges.parquet"
    baseline.parent.mkdir(parents=True, exist_ok=True)
    baseline.write_text("not-really-parquet")
    sidecar = baseline.with_suffix(baseline.suffix + ".manifest.yml")
    sidecar.write_text(yaml.safe_dump(manifest), encoding="utf-8")
    _drop_pending(scratch, "fixtures")


# ---------------------------------------------------------------------------
# 3.39 -- every mechanism the section 3 matrix names is real and wired up.
# ---------------------------------------------------------------------------


def test_standards_matrix_passes_on_the_real_repository() -> None:
    assert check_standards_matrix.check(ROOT) == []


def test_standards_matrix_rejects_a_script_that_does_not_exist(tmp_path: Path) -> None:
    """The orphan-rule case: a row naming a mechanism nobody wrote."""
    scratch = _mirror(tmp_path)
    (scratch / "scripts" / "check_verified_markers.py").unlink()
    errors = check_standards_matrix.check(scratch)
    assert any("check_verified_markers.py" in e and "does not exist" in e for e in errors)


def test_standards_matrix_rejects_an_unconfigured_bouncer_check(tmp_path: Path) -> None:
    """A real dbt-bouncer check that the config never switches on."""
    scratch = _mirror(tmp_path)
    cfg = scratch / "dbt-bouncer.yml"
    cfg.write_text(
        cfg.read_text().replace(
            "  - name: check_model_has_tests_by_type\n    min_number_of_schema_tests: 1\n", ""
        )
    )
    errors = check_standards_matrix.check(scratch)
    assert any("check_model_has_tests_by_type" in e and "EXISTS" in e for e in errors)


def test_standards_matrix_refuses_to_pass_when_the_parser_breaks(tmp_path: Path) -> None:
    """A parser matching nothing exits 0 and reads as a pass -- the D.0 finding 20 shape."""
    scratch = _mirror(tmp_path)
    doc = scratch / "docs" / "DbtBestPractices.md"
    doc.write_text(doc.read_text().replace("| 3.", "| x3."))
    errors = check_standards_matrix.check(scratch)
    assert any("extractor has broken" in e for e in errors)


def test_standards_matrix_rejects_a_stale_pending_entry(tmp_path: Path) -> None:
    """Rule 2: an entry must not outlive the gap it records."""
    scratch = _mirror(tmp_path)
    reg = scratch / "scripts" / "pending_subjects.yml"
    reg.write_text(reg.read_text().replace('- standard: "3.60"', '- standard: "3.1"'))
    errors = check_standards_matrix.check(scratch)
    assert any("3.1" in e and "now resolves" in e for e in errors)


# ---------------------------------------------------------------------------
# 3.44 -- a [VERIFIED] marker is scoped to a toolchain and expires with it.
# ---------------------------------------------------------------------------


def test_verified_markers_pass_on_the_real_repository() -> None:
    assert check_verified_markers.check(ROOT) == []


def test_verified_markers_demotes_on_a_moved_pin(tmp_path: Path) -> None:
    scratch = _mirror(tmp_path)
    doc = scratch / "docs" / "DbtBestPractices.md"
    doc.write_text(doc.read_text().replace("yamllint 1.38.0 ·", "yamllint 1.37.0 ·"))
    errors = check_verified_markers.check(scratch)
    assert any("DEMOTED" in e and "yamllint" in e for e in errors)


def test_verified_markers_rejects_a_marker_with_no_scope(tmp_path: Path) -> None:
    """The subtle case: a marker naming no toolchain can never expire."""
    scratch = _mirror(tmp_path)
    doc = scratch / "docs" / "DbtBestPractices.md"
    doc.write_text(doc.read_text().replace(" · ruff 0.16.4", ""))
    errors = check_verified_markers.check(scratch)
    assert any("ruff" in e and "does not name it" in e for e in errors)


def test_verified_markers_rejects_a_pin_the_lock_disagrees_with(tmp_path: Path) -> None:
    scratch = _mirror(tmp_path)
    doc = scratch / "docs" / "DbtBestPractices.md"
    doc.write_text(doc.read_text().replace("| duckdb | `==1.5.5`", "| duckdb | `==1.5.4`"))
    errors = check_verified_markers.check(scratch)
    assert any("pin and the lock disagree" in e for e in errors)


# ---------------------------------------------------------------------------
# 3.49 / 3.50 -- divergences and parity claims, in both directions.
# ---------------------------------------------------------------------------


def test_divergence_log_passes_on_the_real_repository() -> None:
    assert check_divergence_log.check(ROOT) == []


def test_divergence_log_rejects_a_pinned_but_unlogged_divergence(tmp_path: Path) -> None:
    """The reverse direction -- behaviour frozen with no record of why."""
    scratch = _mirror(tmp_path)
    test = scratch / "tests" / "divergence" / "test_div_99.sql"
    test.parent.mkdir(parents=True)
    test.write_text("-- DIV-99\nselect 1\n")
    errors = check_divergence_log.check(scratch)
    assert any("DIV-99" in e and "pinned and unrecorded" in e for e in errors)


def test_divergence_log_rejects_an_entry_with_no_pinning_test(tmp_path: Path) -> None:
    scratch = _mirror(tmp_path)
    _write_log(scratch, "## DIV-01 - a divergence\n\nSome prose and no test.\n")
    errors = check_divergence_log.check(scratch)
    assert any("DIV-01" in e and "no `Pinning test:` line" in e for e in errors)


def test_divergence_log_rejects_a_one_way_link(tmp_path: Path) -> None:
    """The test exists but never cites the entry, so renaming breaks it silently."""
    scratch = _mirror(tmp_path)
    test = scratch / "tests" / "divergence" / "test_div_01.sql"
    test.parent.mkdir(parents=True)
    test.write_text("select 1 as no_citation_here\n")
    _write_log(
        scratch,
        "## DIV-01 - a divergence\n\nPinning test: `tests/divergence/test_div_01.sql`\n",
    )
    errors = check_divergence_log.check(scratch)
    assert any("does not cite" in e for e in errors)


def test_divergence_log_accepts_a_complete_pair(tmp_path: Path) -> None:
    scratch = _mirror(tmp_path)
    test = scratch / "tests" / "divergence" / "test_div_01.sql"
    test.parent.mkdir(parents=True)
    test.write_text("-- DIV-01: pinned here.\nselect 1\n")
    _write_log(
        scratch,
        "## DIV-01 - a divergence\n\nPinning test: `tests/divergence/test_div_01.sql`\n",
    )
    assert check_divergence_log.check(scratch) == []


def test_divergence_log_rejects_an_undeclared_missing_parity_file(tmp_path: Path) -> None:
    """3.50: absence must be declared, never silent."""
    scratch = _mirror(tmp_path)
    reg = scratch / "scripts" / "pending_subjects.yml"
    reg.write_text(
        reg.read_text().replace("  - path: docs/PARITY.md\n", "  - path: docs/PARITY.disabled\n")
    )
    errors = check_divergence_log.check(scratch)
    assert any("not declared pending (3.50" in e for e in errors)


# ---------------------------------------------------------------------------
# 3.62 -- a baseline without provenance is a number nobody can reproduce.
# ---------------------------------------------------------------------------


def test_baseline_manifests_pass_on_the_real_repository() -> None:
    assert check_baseline_manifests.check(ROOT) == []


def test_baseline_manifests_rejects_a_baseline_with_no_sidecar(tmp_path: Path) -> None:
    scratch = _mirror(tmp_path)
    baseline = scratch / "fixtures" / "fake_1000" / "edges.parquet"
    baseline.parent.mkdir(parents=True)
    baseline.write_text("not-really-parquet")
    errors = check_baseline_manifests.check(scratch)
    assert any("no sidecar at" in e for e in errors)


@pytest.mark.parametrize("field", ["sqlglot_version", "platform", "producing_commit", "seed"])
def test_baseline_manifests_rejects_each_missing_field(tmp_path: Path, field: str) -> None:
    """Section 20.1's fields, each absent in turn."""
    scratch = _mirror(tmp_path)
    manifest = _complete_manifest()
    del manifest[field]
    _write_baseline(scratch, manifest)
    errors = check_baseline_manifests.check(scratch)
    assert any(field in e for e in errors)


def test_baseline_manifests_rejects_an_incomplete_platform_triple(tmp_path: Path) -> None:
    """G5: a manifest that cannot say which platform produced a baseline."""
    scratch = _mirror(tmp_path)
    manifest = _complete_manifest()
    del manifest["platform"]["architecture"]
    _write_baseline(scratch, manifest)
    errors = check_baseline_manifests.check(scratch)
    assert any("architecture" in e for e in errors)


def test_baseline_manifests_rejects_a_truncated_hash(tmp_path: Path) -> None:
    scratch = _mirror(tmp_path)
    manifest = _complete_manifest()
    manifest["model_json_sha256"] = "abc123"
    _write_baseline(scratch, manifest)
    errors = check_baseline_manifests.check(scratch)
    assert any("truncated hash" in e for e in errors)


def test_baseline_manifests_accepts_a_complete_sidecar(tmp_path: Path) -> None:
    scratch = _mirror(tmp_path)
    _write_baseline(scratch, _complete_manifest())
    assert check_baseline_manifests.check(scratch) == []


def test_baseline_manifests_rejects_an_empty_fixtures_directory(tmp_path: Path) -> None:
    """An empty walk exits 0 and reads as a pass."""
    scratch = _mirror(tmp_path)
    (scratch / "fixtures").mkdir()
    errors = check_baseline_manifests.check(scratch)
    assert any("contains no baselines" in e for e in errors)


# ---------------------------------------------------------------------------
# 3.69 -- fixture types are declared, never inferred.
# ---------------------------------------------------------------------------


def test_unit_test_fixtures_pass_on_the_real_repository() -> None:
    assert check_unit_test_fixtures.check(ROOT) == []


def test_unit_test_fixtures_rejects_format_dict(tmp_path: Path) -> None:
    """Section 12.2's measured case: agate read DATE from "not-a-date"."""
    scratch = _mirror(tmp_path)
    yml = scratch / "models" / "intermediate" / "er_thresholds.yml"
    yml.write_text(yml.read_text().replace("      format: sql", "      format: dict", 1))
    errors = check_unit_test_fixtures.check(scratch)
    assert any("Only `format: sql` is permitted" in e for e in errors)


def test_unit_test_fixtures_rejects_an_uncast_column(tmp_path: Path) -> None:
    scratch = _mirror(tmp_path)
    yml = scratch / "models" / "intermediate" / "er_thresholds.yml"
    yml.write_text(
        yml.read_text().replace("cast(0.9 as double) as thr_auto_merge", "0.9 as thr_auto_merge", 1)
    )
    errors = check_unit_test_fixtures.check(scratch)
    assert any("thr_auto_merge" in e and "without an explicit" in e for e in errors)


def test_unit_test_fixtures_rejects_a_missing_format(tmp_path: Path) -> None:
    scratch = _mirror(tmp_path)
    yml = scratch / "models" / "intermediate" / "er_thresholds.yml"
    yml.write_text(yml.read_text().replace("      format: sql\n", "", 1))
    errors = check_unit_test_fixtures.check(scratch)
    assert any("declares no `format`" in e for e in errors)


def test_unit_test_fixtures_rejects_select_star(tmp_path: Path) -> None:
    scratch = _mirror(tmp_path)
    yml = scratch / "models" / "intermediate" / "er_thresholds.yml"
    body = yml.read_text().replace(
        "        select\n            cast(0.9 as double) as thr_auto_merge,\n"
        "            cast(0.9 as double) as thr_review_low",
        "        select * from somewhere",
        1,
    )
    yml.write_text(body)
    errors = check_unit_test_fixtures.check(scratch)
    assert any("select *" in e for e in errors)


def test_unit_test_fixtures_rejects_a_nested_unit_tests_block(tmp_path: Path) -> None:
    """3.72 / D.0 finding 4: dbt ignores a nested block silently -- exit 0, no warning."""
    scratch = _mirror(tmp_path)
    yml = scratch / "models" / "intermediate" / "er_thresholds.yml"
    text = yml.read_text()
    head, marker, tail = text.partition("unit_tests:")
    nested = "\n".join(f"    {ln}" if ln.strip() else ln for ln in (marker + tail).split("\n"))
    yml.write_text(head.rstrip("\n") + "\n" + nested)
    errors = check_unit_test_fixtures.check(scratch)
    assert any("nested `unit_tests:` key" in e for e in errors)


def test_standards_matrix_rejects_an_injection_with_no_matrix_row(tmp_path: Path) -> None:
    """The reverse direction: enforced, and documented nowhere."""
    scratch = _mirror(tmp_path)
    doc = scratch / "docs" / "DbtBestPractices.md"
    doc.write_text(doc.read_text().replace("| 3.72 |", "| 3.972 |", 1))
    errors = check_standards_matrix.check(scratch)
    assert any("3.72" in e and "no row" in e for e in errors)


# ---------------------------------------------------------------------------
# 3.73 -- one canonical home per configuration artifact.
# ---------------------------------------------------------------------------


def test_canonical_homes_pass_on_the_real_repository() -> None:
    assert check_canonical_homes.check(ROOT) == []


def test_canonical_homes_rejects_a_second_copy_of_a_live_file(tmp_path: Path) -> None:
    """The failure the rule exists for: a copy that cannot be run and never expires."""
    scratch = _mirror(tmp_path)
    doc = scratch / "docs" / "DbtBestPractices.md"
    doc.write_text(
        doc.read_text().replace(
            "**Canonical: [`packages.yml`](../packages.yml).**",
            "```yaml\npackages:\n  - package: dbt-labs/dbt_utils\n```\n\n"
            "**Canonical: [`packages.yml`](../packages.yml).**",
            1,
        )
    )
    errors = check_canonical_homes.check(scratch)
    assert any("packages.yml" in e and "EXISTS in the repository" in e for e in errors)


def test_canonical_homes_allows_a_marked_excerpt(tmp_path: Path) -> None:
    scratch = _mirror(tmp_path)
    doc = scratch / "docs" / "DbtBestPractices.md"
    doc.write_text(
        doc.read_text().replace(
            "**Canonical: [`packages.yml`](../packages.yml).**",
            "<!-- excerpt: the one shipped entry -->\n```yaml\npackages: []\n```\n\n"
            "**Canonical: [`packages.yml`](../packages.yml).**",
            1,
        )
    )
    assert check_canonical_homes.check(scratch) == []


def test_canonical_homes_rejects_an_excerpt_that_is_really_a_copy(tmp_path: Path) -> None:
    """An excerpt marker is not a way to keep a whole file."""
    scratch = _mirror(tmp_path)
    doc = scratch / "docs" / "DbtBestPractices.md"
    body = "\n".join(f"  line_{i}: value" for i in range(20))
    doc.write_text(
        doc.read_text().replace(
            "**Canonical: [`packages.yml`](../packages.yml).**",
            f"<!-- excerpt: too much -->\n```yaml\n{body}\n```\n\n"
            "**Canonical: [`packages.yml`](../packages.yml).**",
            1,
        )
    )
    errors = check_canonical_homes.check(scratch)
    assert any("is a copy with a label on it" in e for e in errors)


def test_canonical_homes_ignores_a_block_under_a_heading_naming_no_live_file(
    tmp_path: Path,
) -> None:
    """Design content in fenced blocks is the point of the document, not a violation."""
    scratch = _mirror(tmp_path)
    doc = scratch / "docs" / "DbtBestPractices.md"
    doc.write_text(doc.read_text() + "\n\n### An illustration\n\n```sql\nselect 1\n```\n")
    assert check_canonical_homes.check(scratch) == []
