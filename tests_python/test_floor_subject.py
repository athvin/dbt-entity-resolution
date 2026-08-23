"""3.57 for 3.86: the tripwire must be shown to fail.

3.86 answers one question — *does the package declare a score while the quality
floors still measure a frozen artefact?* — and it is the only thing that will
notice the day Stage 5 lands. Every other gate stays green that day, because a
floor measured against the oracle passes exactly as well as it did before.

That makes a tripwire which cannot fire strictly worse than no tripwire, and
this file exists because two versions of it could not. An adversarial review
found eight evasions (D.0 findings 76, 79); each is a case below.

The floors THEMSELVES are enforced by `harness/test_quality_floors.py`, which
predates this work and is run by `pytest harness` in both `make ci` and CI. This
file deliberately does not re-test them — a second, weaker copy of one gate is
finding 69's defect, and the first draft of 3.86 shipped exactly that.
"""

from __future__ import annotations

import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import check_floor_subject  # noqa: E402
import measure_quality  # noqa: E402

SCORED = "      - name: match_probability\n        data_type: double\n"


def _repo(root: pathlib.Path, name: str, body: str) -> pathlib.Path:
    """Build a scratch repo with one properties file and a frozen baseline dir."""
    (root / "models" / "intermediate").mkdir(parents=True, exist_ok=True)
    (root / "models" / "intermediate" / name).write_text(body, encoding="utf-8")
    return root


def test_the_tripwire_is_quiet_on_the_real_repository() -> None:
    """No model declares a score yet, so the floors measure the oracle by design.

    The baseline claim: without it, every failing case below proves nothing.
    """
    assert check_floor_subject.check(ROOT) == []


@pytest.mark.parametrize(
    ("label", "filename", "body", "should_fire"),
    [
        (
            "plain columns",
            "x.yml",
            "version: 2\nmodels:\n  - name: er_scored\n    columns:\n" + SCORED,
            True,
        ),
        (
            # A scored model is exactly the kind of thing that acquires a v2,
            # and dbt lets a versioned model declare columns inside `versions:`.
            "columns under versions:",
            "x.yml",
            (
                "version: 2\nmodels:\n  - name: er_scored\n    latest_version: 2\n"
                "    versions:\n      - v: 2\n        columns:\n"
                "          - name: match_probability\n            data_type: double\n"
            ),
            True,
        ),
        (
            # dbt accepts both extensions; v2 globbed only *.yml.
            "yaml extension",
            "x.yaml",
            "version: 2\nmodels:\n  - name: er_scored\n    columns:\n" + SCORED,
            True,
        ),
        (
            # v2 swallowed YAMLError, which makes "cannot parse" and "declares
            # nothing" the same answer -- and the second one keeps it quiet.
            "unparseable yaml",
            "x.yml",
            "models: {{ unclosed ]]\n",
            True,
        ),
        (
            # v2 raised AttributeError here rather than erroring cleanly.
            "list at top level",
            "x.yml",
            "- a\n- b\n",
            False,
        ),
        ("nothing declared", "x.yml", "version: 2\nmodels: []\n", False),
    ],
)
def test_the_tripwire_survives_every_shape_of_stage_five(  # noqa: PLR0913
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    label: str,
    filename: str,
    body: str,
    *,
    should_fire: bool,
) -> None:
    """Recognise every legal shape, not just the one its author imagined.

    A tripwire that matches only the author's mental model is a tripwire for
    that mental model.
    """
    root = _repo(tmp_path / label.replace(" ", "_").replace(":", ""), filename, body)
    baselines = root / "fixtures" / "baselines" / "fake_1000"
    baselines.mkdir(parents=True)
    monkeypatch.setattr(measure_quality, "BASELINES", baselines)

    assert bool(check_floor_subject.check(root)) is should_fire


def test_renaming_the_baseline_directory_does_not_disarm_it(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D.0 finding 79 — the worst of the eight, because it did not go quiet.

    v2 keyed the ORACLE half on the literal directory name `"baselines"`, one
    function below a docstring congratulating itself for having replaced exactly
    that pattern on the MODEL half. `git mv fixtures/baselines fixtures/frozen`
    left 3.62 green and printed `1 scored-model declaration(s) -- the package
    scores its own pairs and the floors measure it`, asserting something the
    check had never established.
    """
    root = _repo(
        tmp_path / "renamed",
        "x.yml",
        "version: 2\nmodels:\n  - name: er_scored\n    columns:\n" + SCORED,
    )
    frozen = root / "fixtures" / "frozen" / "fake_1000"
    frozen.mkdir(parents=True)
    monkeypatch.setattr(measure_quality, "BASELINES", frozen)

    errors = check_floor_subject.check(root)
    assert len(errors) == 1
    assert "ER-086" in errors[0]


def test_a_missing_models_directory_is_not_a_pass(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Tolerate an absent search root without crashing or claiming coverage.

    `integration_tests/models/` does not exist, so one SEARCH_ROOT has always
    walked an empty set. That is tolerable only because the other root is real. What must not happen
    is a crash, and what must not be claimed is coverage the walk did not have.
    """
    root = tmp_path / "empty"
    baselines = root / "fixtures" / "baselines" / "fake_1000"
    baselines.mkdir(parents=True)
    monkeypatch.setattr(measure_quality, "BASELINES", baselines)

    assert check_floor_subject.check(root) == []


def test_the_error_names_the_model_and_the_consequence(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Localisation: the message has to say which model and why it matters.

    "Something is wrong" on the one gate that fires once, years into a project,
    is a message nobody can act on.
    """
    root = _repo(
        tmp_path / "named",
        "x.yml",
        "version: 2\nmodels:\n  - name: er_int_scored_pairs\n    columns:\n" + SCORED,
    )
    baselines = root / "fixtures" / "baselines" / "fake_1000"
    baselines.mkdir(parents=True)
    monkeypatch.setattr(measure_quality, "BASELINES", baselines)

    errors = check_floor_subject.check(root)
    assert len(errors) == 1
    assert "er_int_scored_pairs" in errors[0]
    assert "SPLINK is a good entity resolver" in errors[0]
