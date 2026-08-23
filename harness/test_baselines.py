"""Stage 0.3: the baselines carry what later stages need, and reproduce.

These are the files every parity claim will be compared against, so the tests
here are about the **format** rather than the numbers. §5 is explicit that
retrofitting the format after Stage 0.4 freezes it is the expensive path, and
this is the last point at which it is cheap.

Three properties, and the third is the one that decides whether a baseline
generated on a laptop can be trusted by CI at all.
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import pytest
import yaml

BASELINES = Path(__file__).resolve().parents[1] / "fixtures" / "baselines" / "fake_1000"
PREDICTIONS = BASELINES / "predictions.parquet"

# M14: neither retain flag is a Splink default, and the baseline is unusable
# without both. These are the column families that go missing.
RETAIN_MATCHING_COLUMNS = ("first_name_l", "first_name_r", "surname_l", "surname_r")
RETAIN_INTERMEDIATE_COLUMNS = ("bf_first_name", "tf_city_l", "bf_tf_adj_city")

# The FIXED model (Stage 0.4): four blocking rules, trained. Two rules alone
# generated 3,349 -- the extra `dob` and `email` rules add 640 candidate pairs
# and lift blocking recall 0.5057 -> 0.8124.
EXPECTED_PAIRS = 3989


@pytest.fixture(scope="module")
def columns() -> dict[str, str]:
    """`{column: duckdb type}` for the predictions baseline."""
    con = duckdb.connect()
    try:
        rows = con.execute(
            "select column_name, column_type from (describe select * from read_parquet(?))",
            [str(PREDICTIONS)],
        ).fetchall()
    finally:
        con.close()
    return dict(rows)


def test_the_baselines_exist() -> None:
    assert PREDICTIONS.is_file(), (
        "run `make baseline` -- every parity gate compares against these files."
    )


def test_retain_matching_columns_was_on(columns: dict[str, str]) -> None:
    """Without it there are no `*_l`/`*_r` columns to compare (M14)."""
    missing = [c for c in RETAIN_MATCHING_COLUMNS if c not in columns]
    assert not missing, (
        f"{missing} absent: retain_matching_columns was off. A baseline without "
        f"the source values cannot show WHY two engines disagree, only that they do."
    )


def test_retain_intermediate_calculation_columns_was_on(columns: dict[str, str]) -> None:
    """Without it gamma equality is the sole gate over a self-consistent wrong numbering.

    M14's exact warning, and the reason A.5 makes both flags normative rather
    than recommended.
    """
    missing = [c for c in RETAIN_INTERMEDIATE_COLUMNS if c not in columns]
    assert not missing, (
        f"{missing} absent: retain_intermediate_calculation_columns was off. "
        f"Stage 4's baseline would then contain no bayes factors, and gamma "
        f"equality becomes the only gate (M14)."
    )


def test_match_key_is_varchar_not_an_integer(columns: dict[str, str]) -> None:
    """§12.7's measured fact, asserted on the artefact rather than on Splink's source.

    `match_key` is VARCHAR in Splink (`blocking.py:203-206`). A baseline that
    stored it as an integer would make the comparator's dtype check vacuous --
    both sides would agree after coercion, and a real divergence would vanish.
    """
    assert columns["match_key"].upper() in {"VARCHAR", "STRING", "TEXT"}, (
        f"match_key is {columns['match_key']}, not VARCHAR. The comparator's "
        f"coerce_match_key_to_int mutant defends against exactly this, and it "
        f"cannot defend a baseline that already lost the type."
    )


def test_the_ground_truth_label_survived() -> None:
    """M12: without labels, §1.8's F1 and recall floors are unmeasurable."""
    clusters = BASELINES / "clusters_at_0_9.parquet"
    con = duckdb.connect()
    try:
        cluster_columns = [
            row[0]
            for row in con.execute(
                "select column_name from (describe select * from read_parquet(?))",
                [str(clusters)],
            ).fetchall()
        ]
    finally:
        con.close()
    assert "cluster" in cluster_columns, (
        "the ground-truth `cluster` column is missing, so precision, recall and "
        "F1 cannot be computed and DR-22's floors have nothing to measure."
    )


def test_the_pair_count_is_what_the_frozen_model_produces() -> None:
    """A canary. If this moves, the model or the fixture moved with it."""
    con = duckdb.connect()
    try:
        count = con.execute("select count(*) from read_parquet(?)", [str(PREDICTIONS)]).fetchone()
    finally:
        con.close()
    assert count is not None
    assert count[0] == EXPECTED_PAIRS


@pytest.mark.parametrize(
    "field",
    [
        "splink_version",
        "model_json_sha256",
        "sqlglot_version",
        "platform",
        "producing_commit",
        "source_fixture_sha256",
        "not_exercised_by_this_fixture",
    ],
)
def test_the_manifest_records_what_makes_a_baseline_reproducible(field: str) -> None:
    """§20.1's fields, plus the two this project added.

    `source_fixture_sha256` because the fixture is vendored and could drift, and
    `not_exercised_by_this_fixture` because a green baseline is otherwise read
    as "everything is covered".
    """
    manifest = yaml.safe_load(
        (PREDICTIONS.parent / (PREDICTIONS.name + ".manifest.yml")).read_text(encoding="utf-8")
    )
    assert manifest.get(field), f"the baseline manifest has no `{field}`"


def test_the_manifest_states_what_this_fixture_does_not_cover() -> None:
    """The honest half of provenance.

    `[RUN]` established that `cluster_id` has ZERO nulls over this fixture:
    Splink's NULL-node rows on dangling edges cannot arise in a `dedupe_only`
    run over one table. So "predictions baseline green" must not be read as
    "NULL handling verified" -- the degenerate corpora cover that, and the
    manifest says so rather than leaving a reader to assume.
    """
    manifest = yaml.safe_load(
        (PREDICTIONS.parent / (PREDICTIONS.name + ".manifest.yml")).read_text(encoding="utf-8")
    )
    gaps = " ".join(manifest["not_exercised_by_this_fixture"])
    assert "dangling edge" in gaps
    assert "unique_id" in gaps
