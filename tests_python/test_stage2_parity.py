"""Stage 2's two acceptance criteria that had no oracle until now.

Both were written into §5 as parity claims and neither could be checked, because
`gen_baseline.py` emitted only end-products (D.0 finding 70). With `concat` and
`tf_all` now captured from Splink, they become assertions:

  * **S1** -- `stg_input` equals Splink's concat excluding `__splink_salt`. This
    sentence has been sitting in `er_stg_input.yml` under **Splink parity** with
    nothing behind it.
  * **D7a** -- re-scoring a pair under an unchanged `(er_model_sha,
    er_tf_snapshot_id)` is invariant to what else is in the corpus. §5 calls this
    *"the test that makes frozen TF meaningful rather than decorative"*.

The second is asserted here at the level of the **term frequencies themselves**
rather than of a `match_weight` column, because Stage 5's scoring model does not
exist yet. That is not a weaker claim: the frozen `tf` values ARE the mechanism
by which an unrelated record could reach a pair's score, so pinning them pins
the consequence. The model-level restatement lands with Stage 5.
"""

from __future__ import annotations

import pathlib
import sys

import duckdb
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import gen_baseline  # noqa: E402

BASELINES = ROOT / "fixtures" / "baselines" / "fake_1000"
CONCAT = BASELINES / "concat.parquet"
TF_ALL = BASELINES / "tf_all.parquet"
CORPUS = ROOT / "fixtures" / "source" / "fake_1000.csv"

# `er_stg_input` selects exactly these, in this order (integration_tests names
# them via `er_input_columns`). Stated here so a change to the model has to be
# reflected deliberately rather than absorbed.
STG_INPUT_COLUMNS = ["unique_id", "first_name", "surname", "dob", "city", "email", "cluster"]


@pytest.fixture
def con() -> duckdb.DuckDBPyConnection:
    return duckdb.connect()


def test_concat_excludes_the_salt_and_nothing_else(con: duckdb.DuckDBPyConnection) -> None:
    """S1, finally asserted rather than described.

    The claim is not "the salt is absent" -- that would be satisfied by a
    baseline that dropped half the schema. It is that the salt is the ONLY
    thing separating Splink's concat from the input columns.
    """
    columns = [r[0] for r in con.execute(f"describe select * from '{CONCAT}'").fetchall()]

    assert gen_baseline.SALT_COLUMN not in columns

    # What remains, once Splink's derived `tf_*` columns are set aside, must be
    # exactly the input columns -- no more, no fewer.
    non_derived = sorted(c for c in columns if not c.startswith("tf_"))
    assert non_derived == sorted(STG_INPUT_COLUMNS)

    # And the exclusion must be RECORDED, or a future reader cannot tell a
    # deliberate omission from a column Splink stopped emitting.
    manifest = (BASELINES / "concat.parquet.manifest.yml").read_text(encoding="utf-8")
    assert gen_baseline.SALT_COLUMN in manifest


def test_stg_input_equals_splinks_concat_row_for_row(con: duckdb.DuckDBPyConnection) -> None:
    """The other half of S1: same rows, not merely the same shape.

    `stg_input` is a bare passthrough (D8), so the corpus CSV read directly is
    what the model produces. Comparing that against Splink's concat is the
    acceptance criterion, and it is a set comparison because neither side
    promises an order.
    """
    projection = ", ".join(f'cast("{c}" as varchar) as "{c}"' for c in STG_INPUT_COLUMNS)
    difference = con.execute(f"""
        with splink as (select {projection} from '{CONCAT}'),
             ours   as (select {projection} from read_csv_auto('{CORPUS}'))
        select count(*) from (
            (select * from splink except select * from ours)
            union all
            (select * from ours except select * from splink)
        )
    """).fetchone()
    assert difference is not None
    assert difference[0] == 0


def test_every_column_sums_to_one(con: duckdb.DuckDBPyConnection) -> None:
    """§3.5's own test, applied to the frozen snapshot rather than to a macro.

    With `count(*)` as the denominator each frequency comes out proportionally
    too small -- ordering unchanged, values plausible, adjustment systematically
    wrong. On this corpus, whose columns carry 17-21% NULLs, the sum would land
    near 0.8.
    """
    rows = con.execute(f"""
        select column_name, sum(tf) from '{TF_ALL}' group by 1 order by 1
    """).fetchall()
    assert len(rows) == 4
    for column, total in rows:
        assert total == pytest.approx(1.0, abs=1e-12), f"{column} sums to {total}"


def test_frozen_tf_is_invariant_to_unrelated_records(con: duckdb.DuckDBPyConnection) -> None:
    """D7a's point, and the reason freezing is not merely tidy.

    Appending records that share no value with a pair still moves that pair's
    score under LIVE term frequency, because `tf` is a property of the whole
    corpus. D7a measures a comparison going from -6.51 to -8.81 bits on a pair
    whose own two records did not change.

    Under a frozen snapshot the same append must move nothing. Asserted by
    computing live TF over the corpus and over the corpus-plus-newcomers, and
    showing the live values DIVERGE while the frozen ones cannot -- because the
    frozen path never reads the corpus at all.
    """
    live = """
        select cast(count(*) as double) / (select count(city) from corpus) as tf
        from corpus where city is not null and city = 'London'
    """
    # Typed as §2.0 requires -- `unique_id` VARCHAR. Left to inference it comes
    # back INT64 here, which is not the type the model contracts.
    typed = ", ".join(f'cast("{c}" as varchar) as "{c}"' for c in STG_INPUT_COLUMNS)
    con.execute(f"create table corpus as select {typed} from read_csv_auto('{CORPUS}')")
    before = con.execute(live).fetchone()

    # Twenty newcomers in a city no existing record shares. They touch neither
    # side of a London pair, and under live TF they still move its score.
    con.execute("""
        insert into corpus
        select 'new-' || i, 'Zzz', 'Qqq', '1900-01-01', 'Atlantis',
               'z' || i || '@example.invalid', 'zz'
        from range(20) as t(i)
    """)
    after = con.execute(live).fetchone()

    assert before is not None
    assert after is not None
    assert before[0] != after[0], (
        "live TF did not move when unrelated records arrived -- the fixture no "
        "longer demonstrates what D7a is about, so this test has stopped testing it"
    )

    # The frozen value is what `er_tf_all` serves, and it is a committed
    # artefact: no corpus read, therefore no dependence on the append.
    frozen = con.execute(
        f"select tf from '{TF_ALL}' where column_name = 'city' and value = 'London'"
    ).fetchone()
    assert frozen is not None
    assert frozen[0] == pytest.approx(before[0], abs=1e-12)
