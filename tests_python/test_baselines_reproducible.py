"""3.57 applied to 3.84: the reproducibility gate must be shown to fail.

D.0 finding 71 is what this guards, and its shape is worth keeping in view. The
baselines were **not** reproducible for three independent reasons at once — row
order, column order, and a random salt column — and every gate over them passed
anyway. Nothing was computing a wrong answer; the artefacts simply could not be
re-derived, so no gate was in a position to notice.

A reproducibility check has a matching failure mode, and it is the reason these
tests exist rather than a single happy path: **a check that regenerates nothing
agrees with everything.** `test_no_baselines_is_not_a_pass` is the one that
would catch this file rotting.

The tree is copied rather than symlinked because `gen_baseline.py` resolves its
own root from `__file__` (via `_er_paths`), so a symlinked script would reach
back into the real repository and the isolation would be imaginary.
"""

from __future__ import annotations

import pathlib
import shutil
import sys

import duckdb
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import check_baselines_reproducible as gate  # noqa: E402

BASELINES = "fixtures/baselines/fake_1000"


@pytest.fixture(scope="module")
def tree(tmp_path_factory: pytest.TempPathFactory) -> pathlib.Path:
    """Build a self-contained copy of everything the generator reads."""
    root = tmp_path_factory.mktemp("repo")
    shutil.copytree(
        ROOT / "scripts", root / "scripts", ignore=shutil.ignore_patterns("__pycache__")
    )
    for sub in ("source", "model_jsons", "baselines"):
        shutil.copytree(ROOT / "fixtures" / sub, root / "fixtures" / sub)
    return root


def _fresh(tree: pathlib.Path, tmp_path: pathlib.Path) -> pathlib.Path:
    """Copy the tree per test, so one test's corruption cannot leak into another."""
    root = tmp_path / "repo"
    shutil.copytree(tree, root)
    return root


def test_the_committed_baselines_regenerate(tree: pathlib.Path, tmp_path: pathlib.Path) -> None:
    """The baseline claim. Without this, every failing case below proves nothing."""
    assert gate.check(_fresh(tree, tmp_path)) == []


def test_a_corrupted_baseline_is_caught(tree: pathlib.Path, tmp_path: pathlib.Path) -> None:
    """The direct case: committed bytes that no longer match what is generated."""
    root = _fresh(tree, tmp_path)
    target = root / BASELINES / "predictions.parquet"
    target.write_bytes(target.read_bytes() + b"\x00")

    errors = gate.check(root)
    assert len(errors) == 1
    assert "predictions.parquet" in errors[0]
    # Both hashes must be named, or the reader cannot tell which side moved.
    assert "committed:" in errors[0]
    assert "regenerated:" in errors[0]


def test_an_orphan_baseline_is_caught(tree: pathlib.Path, tmp_path: pathlib.Path) -> None:
    """A committed artefact the generator no longer produces.

    This is how a baseline outlives its own generator: the file stays, the code
    that made it goes, and every comparison against it keeps passing because
    nothing regenerates it to disagree with.
    """
    root = _fresh(tree, tmp_path)
    shutil.copy(root / BASELINES / "predictions.parquet", root / BASELINES / "retired.parquet")

    errors = gate.check(root)
    assert len(errors) == 1
    assert "retired.parquet" in errors[0]
    assert "orphan" in errors[0]


def test_no_baselines_is_not_a_pass(tree: pathlib.Path, tmp_path: pathlib.Path) -> None:
    """§6.1's vacuous pass — the failure mode that would retire this gate quietly.

    Delete the baselines and a naive implementation reports success, having
    compared nothing at all.
    """
    root = _fresh(tree, tmp_path)
    for path in (root / BASELINES).glob("*.parquet"):
        path.unlink()

    errors = gate.check(root)
    assert any("passes unconditionally" in e for e in errors)


def test_a_broken_generator_fails_loudly(tree: pathlib.Path, tmp_path: pathlib.Path) -> None:
    """If regeneration cannot run, that is a failure and never a pass.

    The tempting bug is to treat a non-zero exit as "could not check" and return
    no errors — which converts a broken generator into a green gate.
    """
    root = _fresh(tree, tmp_path)
    generator = root / "scripts" / "gen_baseline.py"
    generator.write_text("raise SystemExit('deliberately broken')\n", encoding="utf-8")

    errors = gate.check(root)
    assert len(errors) == 1
    assert "reproducibility cannot be established" in errors[0]


def test_the_salt_column_is_excluded_and_says_so(
    tree: pathlib.Path, tmp_path: pathlib.Path
) -> None:
    """S1, asserted rather than described (D.0 finding 70).

    `__splink_salt` holds 1,000 unseeded random floats redrawn every run, so a
    baseline retaining it could never be byte-stable. Its absence is checkable;
    that it was *deliberately* dropped is only checkable because the manifest
    records it.
    """
    root = _fresh(tree, tmp_path)
    concat = root / BASELINES / "concat.parquet"
    columns = [
        r[0] for r in duckdb.connect().execute(f"describe select * from '{concat}'").fetchall()
    ]
    assert "__splink_salt" not in columns
    assert "unique_id" in columns

    manifest = (root / BASELINES / "concat.parquet.manifest.yml").read_text(encoding="utf-8")
    assert "__splink_salt" in manifest, "the exclusion must be recorded, not merely performed"
