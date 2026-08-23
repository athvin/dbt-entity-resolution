"""The generator is deterministic, calibrated, and passes the repository's own gates.

Stage 11's nightly loop runs on generated data, so two properties matter more
than the data's realism: **the same seed reproduces exactly**, because a nightly
that cannot reproduce its own failing input is not debuggable; and **the output
passes 3.55**, because a generator that trips the PII scan makes the scan
something people switch off.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from harness.generate import COLUMNS, generate, to_csv

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import check_pii_heuristics

# Measured on the vendored fake_1000; the generator is calibrated to these.
FAKE_1000_MISSING_RATES = {
    "first_name": 0.169,
    "surname": 0.181,
    "city": 0.187,
    "email": 0.211,
}
TOLERANCE = 0.06


def test_the_same_seed_reproduces_exactly() -> None:
    """The property Stage 11's failure bundles depend on."""
    assert generate(seed=7) == generate(seed=7)


def test_a_different_seed_produces_different_data() -> None:
    """Otherwise `seed` is decoration and every nightly runs the same data."""
    assert generate(seed=7) != generate(seed=8)


def test_every_record_has_every_column() -> None:
    for record in generate(clusters=40):
        assert set(record) == set(COLUMNS)


def test_ground_truth_is_internally_consistent() -> None:
    """Every record belongs to exactly one cluster, and clusters are non-empty."""
    records = generate(clusters=60)
    clusters: dict[str, int] = {}
    for record in records:
        clusters[record["cluster"]] = clusters.get(record["cluster"], 0) + 1
    assert len(clusters) == 60
    assert all(size >= 1 for size in clusters.values())
    assert sum(clusters.values()) == len(records)


def test_unique_ids_are_unique() -> None:
    """§2.0's input contract requires it, and G9 is what happens when it fails."""
    records = generate(clusters=120)
    ids = [r["unique_id"] for r in records]
    assert len(set(ids)) == len(ids)


def test_dob_is_never_missing() -> None:
    """Calibrated, not smoothed.

    `dob` has zero nulls in `fake_1000`, and that is load-bearing rather than
    incidental: it is the one attribute always available to block on, which is
    why `block_on(dob)` lifted blocking recall from 0.5057 to 0.8124 in Stage
    0.4. A generator that nulled it would quietly make that rule look weak.
    """
    assert all(record["dob"] is not None for record in generate(clusters=200))


@pytest.mark.parametrize("column", sorted(FAKE_1000_MISSING_RATES))
def test_missingness_matches_the_real_fixture(column: str) -> None:
    records = generate(clusters=400)
    rate = sum(1 for r in records if r[column] is None) / len(records)
    expected = FAKE_1000_MISSING_RATES[column]
    assert abs(rate - expected) < TOLERANCE, (
        f"{column} is missing at {rate:.3f}; fake_1000 measures {expected}. "
        f"A generator whose missingness does not match the reference fixture "
        f"exercises different comparison levels."
    )


def test_cluster_sizes_span_the_configured_range() -> None:
    records = generate(clusters=300, max_cluster_size=7)
    sizes: dict[str, int] = {}
    for record in records:
        sizes[record["cluster"]] = sizes.get(record["cluster"], 0) + 1
    observed = set(sizes.values())
    assert observed <= set(range(1, 8))
    assert {1, 7} <= observed, f"sizes {sorted(observed)} do not span 1..7"


def test_it_scales() -> None:
    assert len(generate(clusters=400)) > len(generate(clusters=100))


@pytest.mark.parametrize("bad", [0, -1])
def test_a_nonsensical_size_is_rejected(bad: int) -> None:
    with pytest.raises(ValueError, match="at least 1"):
        generate(clusters=bad)


def test_generated_data_passes_the_repositorys_own_pii_scan(tmp_path: Path) -> None:
    """The real check, not a copy of its rules.

    A generator that trips 3.55 makes the scan something people switch off, so
    this runs `check_pii_heuristics.check` itself over a tree containing only
    generated output. If the detectors change, this test changes with them.
    """
    fixtures = tmp_path / "fixtures" / "generated"
    fixtures.mkdir(parents=True)
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "pending_subjects.yml").write_text("---\npending: []\n")

    for index in range(6):
        csv_path = fixtures / f"generated_{index}.csv"
        csv_path.write_text(to_csv(generate(clusters=60, seed=index)), encoding="utf-8")
        csv_path.with_suffix(".csv.manifest.yml").write_text(
            "---\nkind: synthetic\nsynthetic: true\nauthored: 2026-08-23\n"
            "generator: harness/generate.py\nshape: 60 clusters\n"
            "probes: >-\n  seeded synthetic person records\n",
            encoding="utf-8",
        )

    assert check_pii_heuristics.check(tmp_path) == []


def test_csv_rendering_round_trips_nulls_as_empty_fields() -> None:
    """A `None` must become an empty field, not the string "None"."""
    rendered = to_csv(generate(clusters=80, seed=3))
    assert "None" not in rendered
    assert rendered.startswith(",".join(COLUMNS))
