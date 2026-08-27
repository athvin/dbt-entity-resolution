#!/usr/bin/env python3
"""Generate Splink baselines as parquet, each with a provenance manifest (Stage 0.3).

Every parity claim this project makes is a comparison against these files, so the
format has to carry, **from day one**, everything a later stage will need. §5
Stage 0.3 is explicit that retrofitting after 0.4 freezes the format is the
expensive path:

* **`retain_matching_columns=True` and `retain_intermediate_calculation_columns=True`.**
  Neither is Splink's default, and `[RUN]` on 4.0.16 confirms what M14 warns:
  without the first there are no `*_l`/`*_r` columns, without the second no
  `bf_`/`tf_` columns. A baseline taken at defaults makes gamma equality the sole
  gate over a self-consistent wrong numbering.
* **Ground-truth labels** (M12), which is what makes §1.8's F1 and recall floors
  measurable at all rather than aspirational.
* **The model JSON is saved and reloaded before generating** (§3.4, normative).
  Generating from the in-memory settings object would not exercise the
  round-trip, and the round-trip is what a consumer actually does.

**Determinism is asserted, not assumed.** The run is seeded, `u` training is
seeded, and the script re-runs prediction twice and compares -- a baseline that
is not reproducible in the process that made it will not be reproducible in CI.

Manifests are written for 3.62, which verifies them on every run.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import platform
import subprocess
import sys
import textwrap
import warnings
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    # pandas ships no stubs and `pandas-stubs` is not in the section 4 pin
    # table. This module only ever passes DataFrames through to Splink and
    # DuckDB, so the annotation is documentation rather than a checked contract.
    import pandas as pd  # type: ignore[import-untyped]

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from _er_paths import ROOT, rel

FIXTURES = ROOT / "fixtures"
BASELINE_DIR = FIXTURES / "baselines"
MODEL_JSON_DIR = FIXTURES / "model_jsons"

# 3.58 pins these for determinism; the generator states its own seed too.
SEED = 20260823

# yamllint caps lines at 100 (section 11.4); leave room for indentation.
_MAX_LINE = 96
_WRAP_WIDTH = 84

# The thresholds Stage 6's acceptance criterion names simultaneously (DR-08).
THRESHOLDS = (0.5, 0.9, 0.99)

# S1's column. Splink adds it to the concat table as an unseeded random float
# used for salting blocking rules; it is excluded from comparison and, because
# it is redrawn every run, from the baseline itself. See `_concat_frame`.
SALT_COLUMN = "__splink_salt"

# M12's ground truth, carried by `fake_1000` and by every fixture that wants to
# be measurable rather than merely comparable. §1.8: parity gates compare two
# engines; none of them compares either engine to the truth.
GROUND_TRUTH_COLUMN = "cluster"


def _git(*args: str) -> str:
    try:
        return subprocess.run(  # noqa: S603
            ["git", *args],  # noqa: S607
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (subprocess.CalledProcessError, OSError):
        return "unknown"


def _platform_triple(duckdb_version: str) -> dict[str, str]:
    """§20.1's triple. G5 closed the question; the field records the answer."""
    machine = platform.machine().lower()
    return {
        "os": platform.system().lower(),
        "architecture": {"x86_64": "amd64", "aarch64": "arm64"}.get(machine, machine),
        "duckdb_build": duckdb_version,
    }


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_settings() -> Any:
    """Return the frozen model's shape -- the FIXED one (Stage 0.4).

    §5 Stage 0.4 is explicit: *"fix the frozen model rather than freezing a bad
    one"*. Two things were wrong with the obvious model, and only one of them is
    the one the document names.

    **1. Missing blocking rules.** `block_on(first_name)` and `block_on(surname)`
    alone leave blocking recall at 0.5057 -- reproducing §5's "blocking recall =
    0.51" almost exactly. Adding `dob` and `email` lifts it to 0.8124.

    **2. The model was untrained**, which the document does not call out and
    which matters more. `[RUN]`: training roughly doubles recall on the SAME
    blocking rules, 0.2354 -> 0.4362. A baseline generated from an untrained
    model is a baseline of Splink's defaults, not of a model.
    """
    import splink.comparison_library as cl  # noqa: PLC0415
    from splink import SettingsCreator, block_on  # noqa: PLC0415

    return SettingsCreator(
        link_type="dedupe_only",
        comparisons=[
            cl.NameComparison("first_name"),
            cl.NameComparison("surname"),
            cl.DateOfBirthComparison("dob", input_is_string=True),
            cl.ExactMatch("city").configure(term_frequency_adjustments=True),
            cl.EmailComparison("email"),
        ],
        blocking_rules_to_generate_predictions=[
            block_on("first_name"),
            block_on("surname"),
            # The two rules §A.6 Q5 / M12 identify as the fix. Measured effect
            # on blocking recall: 0.5057 -> 0.8124.
            block_on("dob"),
            block_on("email"),
        ],
        # NEITHER IS A DEFAULT. See the module docstring and M14.
        retain_matching_columns=True,
        retain_intermediate_calculation_columns=True,
    )


def _write_parquet(frame: pd.DataFrame, path: Path) -> None:
    """Write parquet through DuckDB rather than pandas.

    pandas needs `pyarrow` or `fastparquet` for parquet, and neither is in the
    section 4 pin table. DuckDB writes it natively and is already the engine
    both sides of every comparison run on -- so this adds no dependency, and
    keeps the baseline written by the same library that reads it back.

    **Rows are sorted before writing, and that is not a tidiness measure.**
    `[RUN]` 2026-08-23: three regenerations from the *same frozen model* produced
    three different `predictions.parquet` files -- 3,989 rows each, **zero**
    set-difference, different bytes. Splink returns rows in engine order, which
    varies with scheduling, so every baseline was byte-unstable while being
    semantically identical (D.0 finding 71).

    That made three things quietly untrue at once: §20.1's regeneration target
    could not be run without producing a diff, 3.62's manifest recorded a
    `sha256` that changed for no semantic reason, and A.4's own wording -- pair
    sets are **"exact after canonical ordering"** -- described an ordering step
    that nothing in this harness performed.

    Sorting happens HERE, in the one function every baseline passes through,
    rather than at each call site, because a per-artefact sort key is a list
    that can be added to incompletely -- and the artefact whose key was
    forgotten is exactly the one that would drift silently.
    """
    import duckdb  # noqa: PLC0415

    # COLUMNS first, then rows. Both vary: Splink builds the concat table's
    # `tf_*` columns in an order that changed between processes, so sorting rows
    # alone left the parquet schema -- and therefore the bytes -- unstable.
    # Canonicalising both is what makes `sha256` mean "same data".
    columns = sorted(frame.columns)
    ordered = frame[columns].sort_values(by=columns, kind="mergesort").reset_index(drop=True)

    con = duckdb.connect()
    try:
        con.register("frame", ordered)
        con.execute("copy frame to ? (format parquet)", [str(path)])
    finally:
        con.close()


def _train(linker: Any) -> Any:
    """Train m and u, seeded (Stage 0.4).

    Everything here is seeded or deterministic by construction, because an
    unseeded `u` estimate makes the frozen model -- and therefore every baseline
    generated from it -- irreproducible. `estimate_u_using_random_sampling` takes
    a seed for exactly this reason.
    """
    from splink import block_on  # noqa: PLC0415

    linker.training.estimate_probability_two_random_records_match(
        [block_on("first_name", "surname"), block_on("email")], recall=0.7
    )
    linker.training.estimate_u_using_random_sampling(max_pairs=1_000_000, seed=SEED)
    linker.training.estimate_parameters_using_expectation_maximisation(
        block_on("first_name", "surname")
    )
    linker.training.estimate_parameters_using_expectation_maximisation(block_on("dob"))
    return linker


# Minting runs SINGLE-THREADED, and this is load-bearing rather than cautious.
#
# `[RUN]` 2026-08-23, five processes, same seed, same code, same machine:
# EM-trained `m_probability` vectors hash identically across three separate
# processes at `threads=1`, and DIVERGE in the last ULP at `threads=8`. DuckDB
# combines partial aggregates in completion order and float addition is not
# associative, so thread scheduling reaches the trained parameters.
#
# The consequence is not small. Without this, regenerating the "frozen" model
# produces a different `model_json_sha256` every time -- so §20.1's regeneration
# target emits a diff on every run, 3.62's manifest records a sha nobody can
# re-derive, and every parity claim in the project is measured against an
# artefact that cannot be reproduced. That is the fully-developed form of the
# defect this repository keeps meeting: a gate that looks like it works.
#
# **This does not weaken §13.2.** That rule requires the DETERMINISM GATE to run
# at `threads=8`, because the claim under test there is that *this package's SQL*
# is thread-stable. The claim here is different and narrower: the ORACLE must be
# mintable twice. Splink's EM is not thread-stable, and that is a property of
# Splink, not of our SQL -- pinning it at the point of minting is what keeps the
# two claims from being confused for one another.
MINT_THREADS = 1


def _linker(frame: pd.DataFrame, settings: Any) -> Any:
    from splink import DuckDBAPI, Linker  # noqa: PLC0415

    db_api = DuckDBAPI()
    db_api._con.execute(f"SET threads TO {MINT_THREADS}")  # noqa: SLF001 -- no public setter
    return Linker(frame, settings, db_api=db_api, set_up_basic_logging=False)


def _concat_frame(linker: Any) -> pd.DataFrame:
    """Splink's `__splink__df_concat_with_tf`, as it exists in the engine.

    Emitted with the wide `tf_*` columns intact -- the oracle should be what
    Splink actually produced, and a baseline pre-filtered to match the
    expectation cannot then falsify it.

    **`__splink_salt` is the one exception, and measuring it sharpened S1.**
    `[RUN]`: the column holds 1,000 distinct unseeded random floats, one per
    row, redrawn on every run. So a concat baseline containing it can never be
    byte-stable -- three regenerations produced three different files with
    identical data. S1 says to exclude the salt from comparison; the stronger
    statement is that a baseline *retaining* it is unusable as an oracle at all.

    Dropping it silently would hide that, so `SALT_COLUMN` is recorded in the
    manifest instead: the exclusion becomes a stated fact a test can assert
    against, rather than a filter nobody can see.
    """
    con = linker._db_api._con  # noqa: SLF001 -- no public accessor for intermediates
    name = next(
        r[0]
        for r in con.execute("select table_name from information_schema.tables").fetchall()
        if r[0].startswith("__splink__df_concat_with_tf")
    )
    frame = con.execute(f'select * from "{name}"').fetchdf()  # noqa: S608 -- name from catalog
    if SALT_COLUMN not in frame.columns:
        msg = (
            f"Splink's concat table has no `{SALT_COLUMN}` column. S1 is written "
            f"around its presence, and this harness drops it deliberately -- if "
            f"it is gone, S1 needs rechecking rather than silently passing."
        )
        raise RuntimeError(msg)
    return frame.drop(columns=[SALT_COLUMN])


def _tf_columns(concat: pd.DataFrame) -> list[str]:
    """Which columns carry a TF adjustment -- asked of SPLINK, never hard-coded.

    Splink emits one `tf_<col>` column into the concat table per TF-adjusted
    comparison, so the concat table *is* the answer. A literal list here would
    be a second copy of a fact the model JSON already decides, and M2's finding
    is precisely that such copies drift silently -- add a TF adjustment to the
    model and the baseline would keep freezing the old set without erroring.
    """
    return sorted(c[len("tf_") :] for c in concat.columns if c.startswith("tf_"))


def _tf_long_frame(linker: Any, concat: pd.DataFrame) -> pd.DataFrame:
    """D7's long-format term frequency, from Splink's OWN `compute_tf_table`.

    D7a freezes `tf_all` by snapshot, and this is where the snapshot is minted.
    Taking it from `compute_tf_table` rather than from this package's own
    `er_term_frequency_sql` is deliberate: it makes the frozen values **Splink's
    output**, so the macro is verified against the oracle instead of against
    itself. D7a's warning that the incumbent *"never calls `compute_tf_table`"*
    is about the production path -- minting a snapshot is precisely the opt-in
    `er_tf_mode='refresh'` operation step 3 carves out for it.

    Long format, one row per `(column_name, value)`, because the wide form makes
    the grain a function of the model JSON: add a TF-adjusted comparison and
    every downstream contract changes shape.
    """
    import pandas as pd  # noqa: PLC0415

    columns = _tf_columns(concat)
    if not columns:
        msg = (
            "the concat table carries no `tf_*` column, so no term-frequency "
            "snapshot can be minted. Either the model JSON has no TF-adjusted "
            "comparison, or Splink's concat schema moved -- an empty snapshot "
            "would freeze silently and every TF adjustment would vanish."
        )
        raise RuntimeError(msg)

    parts: list[pd.DataFrame] = []
    for column in columns:
        frame = linker.table_management.compute_tf_table(column).as_pandas_dataframe()
        parts.append(
            pd.DataFrame(
                {
                    "column_name": column,
                    "value": frame[column].astype(str),
                    "tf": frame[f"tf_{column}"],
                }
            )
        )
    combined = pd.concat(parts, ignore_index=True)
    return combined.sort_values(["column_name", "value"]).reset_index(drop=True)


def _blocked_pairs_frame(linker: Any) -> pd.DataFrame:
    """Splink's own blocked pair set -- Stage 3's oracle, and nothing else.

    **Not derived from `predictions.parquet`.** The two agree on this fixture
    (3,989 rows, zero difference either way), but that agreement is *contingent*
    on `predict()` defaulting `threshold_match_probability=None` rather than
    structural: any future threshold, filter or scoring change would silently
    move the set Stage 3 is asserting on. An oracle for blocking must contain
    blocking and nothing else, so a Stage 3 failure cannot be manufactured or
    masked by a Stage 4/5 change. That is D.0 finding 70's lesson, one stage on.

    Generated by executing **Splink's own** `block_using_rules_sqls` against
    Splink's own concat table, so this is a capture rather than a
    reimplementation -- the same discipline `resolve()` follows for A.2.

    The output is `(match_key, join_key_l, join_key_r)`, all three VARCHAR, and
    the column names are Splink's: the package's `er_blocking_sql` emits
    `join_key_l`/`join_key_r` because *this* is what it is matching. The rename
    to `unique_id_l`/`unique_id_r` happens in Stage 4, which is where Splink
    itself does it.

    These are private Splink APIs. That is a real cost and the alternative is
    worse -- reimplementing the blocking SQL here would make the oracle a second
    copy of the thing under test, which is the one property an oracle must not
    have. 3.44's pin-move rule is what catches it if they move.
    """
    from splink.internals.blocking import block_using_rules_sqls  # noqa: PLC0415
    from splink.internals.pipeline import CTEPipeline  # noqa: PLC0415
    from splink.internals.vertically_concatenate import (  # noqa: PLC0415
        compute_df_concat_with_tf,
    )

    settings = linker._settings_obj  # noqa: SLF001 -- no public accessor
    concat = compute_df_concat_with_tf(linker, CTEPipeline())
    pipeline = CTEPipeline([concat])
    pipeline.enqueue_list_of_sqls(
        block_using_rules_sqls(
            input_tablename_l=concat.physical_name,
            input_tablename_r=concat.physical_name,
            blocking_rules=settings._blocking_rules_to_generate_predictions,  # noqa: SLF001
            link_type=settings._link_type,  # noqa: SLF001
            source_dataset_input_column=settings.column_info_settings.source_dataset_input_column,
            unique_id_input_column=settings.column_info_settings.unique_id_input_column,
        )
    )
    frame = linker._db_api.sql_pipeline_to_splink_dataframe(  # noqa: SLF001
        pipeline
    ).as_pandas_dataframe()
    if frame.empty:
        msg = (
            "Splink produced no blocked pairs, so Stage 3 has no oracle. An empty "
            "pair set makes every set-equality assertion over it vacuously true "
            "(section 6.1) -- and blocking that generates nothing is section "
            "12.7's zero-rows-to-zero-rows comparison."
        )
        raise RuntimeError(msg)
    return frame


def _comparison_vectors_frame(linker: Any) -> pd.DataFrame:
    """Splink's own comparison-vector table -- Stage 4's oracle.

    Captured from `compute_comparison_vector_values_from_id_pairs_sqls` for the
    same reason `_blocked_pairs_frame` is captured rather than derived: it
    contains gammas and the columns feeding them, and nothing from scoring. A
    Stage 4 failure therefore cannot be masked or manufactured by a Stage 5
    change, and `predictions.parquet` -- which happens to carry the same gammas
    today -- would tie the two together for no reason beyond convenience.

    **This is where Splink renames `join_key_l`/`join_key_r` to
    `unique_id_l`/`unique_id_r`**, and the package follows it here rather than
    at Stage 3, so the two agree column-for-column at every stage boundary.
    """
    from splink.internals.blocking import block_using_rules_sqls  # noqa: PLC0415
    from splink.internals.comparison_vector_values import (  # noqa: PLC0415
        compute_comparison_vector_values_from_id_pairs_sqls,
    )
    from splink.internals.pipeline import CTEPipeline  # noqa: PLC0415
    from splink.internals.vertically_concatenate import (  # noqa: PLC0415
        compute_df_concat_with_tf,
    )

    settings = linker._settings_obj  # noqa: SLF001
    concat = compute_df_concat_with_tf(linker, CTEPipeline())
    pipeline = CTEPipeline([concat])
    pipeline.enqueue_list_of_sqls(
        block_using_rules_sqls(
            input_tablename_l=concat.physical_name,
            input_tablename_r=concat.physical_name,
            blocking_rules=settings._blocking_rules_to_generate_predictions,  # noqa: SLF001
            link_type=settings._link_type,  # noqa: SLF001
            source_dataset_input_column=settings.column_info_settings.source_dataset_input_column,
            unique_id_input_column=settings.column_info_settings.unique_id_input_column,
        )
    )
    pipeline.enqueue_list_of_sqls(
        compute_comparison_vector_values_from_id_pairs_sqls(
            settings._columns_to_select_for_blocking,  # noqa: SLF001
            settings._columns_to_select_for_comparison_vector_values,  # noqa: SLF001
            input_tablename_l=concat.physical_name,
            input_tablename_r=concat.physical_name,
            source_dataset_input_column=settings.column_info_settings.source_dataset_input_column,
            unique_id_input_column=settings.column_info_settings.unique_id_input_column,
        )
    )
    frame = linker._db_api.sql_pipeline_to_splink_dataframe(  # noqa: SLF001
        pipeline
    ).as_pandas_dataframe()
    if frame.empty:
        msg = (
            "Splink produced no comparison vectors, so Stage 4 has no oracle. "
            "An empty table makes every gamma assertion vacuously true (§6.1)."
        )
        raise RuntimeError(msg)
    return frame


def _accuracy_frame(linker: Any) -> pd.DataFrame:
    """Splink's own `accuracy_analysis_from_labels_column`, swept and whole.

    This is Stage 10's acceptance criterion in artefact form: *"confusion-matrix
    parity against `accuracy_analysis_from_labels_table` on a labelled fixture"*.
    The `_column` variant is used because `fake_1000` carries ground truth as a
    `cluster` COLUMN (M12), which is the labelling this project actually has.

    `positives_not_captured_by_blocking_rules_scored_as_zero` is left at its
    default `True`, and that default is the reason the table is usable as a
    blocking-recall oracle at all: pairs no blocking rule generated are scored
    zero rather than dropped, so `recall` at the lowest threshold IS blocking
    recall. `[RUN]`: 0.812408 here against 0.8124 from `measure_quality.py`,
    with `tp + fn = 1650 + 381 = 2031` -- the fixture's true pair count, reached
    independently.

    Every optional metric is requested. They cost nothing to compute alongside
    the ones we need, and a metric absent from a frozen baseline is a metric no
    later stage can gate on without re-minting every artefact.

    **`match_weight_round_to_nearest=None` is the load-bearing argument.** Its
    default is `0.1`, which QUANTISES the sweep -- and a baseline frozen at the
    default is a charting artefact, not an oracle. Measured, at the three
    thresholds this project builds simultaneously:

        round=0.1     t>=0.5  tp=1433  fp=3  f1=0.826651
        round=None    t>=0.5  tp=1431  fp=0  f1=0.826690   <- matches ours exactly

    At `0.1` the first row at or above probability `t` is the matrix at the
    nearest rounded MATCH WEIGHT, which can sit below `t` and admit pairs the
    threshold excludes -- hence `fp=3` where the exact computation has none. Our
    own `measure_quality.py` reports tp = 1431 / 1276 / 985 and f1 = 0.826690 /
    0.771696 / 0.653183, and unrounded Splink reproduces every one of those
    exactly.

    Freezing the default would therefore have manufactured a parity failure of
    ~2 pairs and ~0.0014 F1 for Stage 10's acceptance criterion to trip over --
    and the natural response to a small unexplained divergence is to widen a
    tolerance, which is §21's failure mode reached by way of the oracle's
    plotting default.
    """
    frame = linker.evaluation.accuracy_analysis_from_labels_column(
        GROUND_TRUTH_COLUMN,
        output_type="table",
        match_weight_round_to_nearest=None,
        add_metrics=["specificity", "npv", "accuracy", "f1", "f2", "f0_5", "p4", "phi"],
    ).as_pandas_dataframe()
    if frame.empty:
        msg = (
            "Splink's accuracy analysis returned no rows, so Stage 10 has no "
            "oracle. An empty confusion-matrix baseline would make every parity "
            "assertion over it vacuously true (section 6.1)."
        )
        raise RuntimeError(msg)
    return frame


def generate(  # noqa: PLR0915 -- one linear block per frozen artefact. Splitting
    # it would scatter the ORDER these are minted in, which matters: the concat
    # table is computed once and reused by three of them.
    fixture: Path,
    out_dir: Path,
    model_json: Path,
    *,
    refreeze: bool = False,
) -> list[Path]:
    """Generate every baseline for one fixture. Returns the files written.

    **Training is OPT-IN, and that is the whole point.** Until 2026-08-23 this
    function retrained on every invocation and overwrote the frozen model, which
    meant §20.1's regeneration target could never be run without silently moving
    the artefact every parity claim is measured against (D.0 finding 71). The
    default path now *reads* the frozen model and regenerates the baselines from
    it -- which is both what a consumer does and what CI can safely repeat.

    `refreeze=True` retrains and re-mints. It is a deliberate, reviewable act.
    """
    import duckdb  # noqa: PLC0415
    import pandas as pd  # noqa: PLC0415
    import splink  # noqa: PLC0415
    import sqlglot  # noqa: PLC0415

    # `unique_id` is read as TEXT, and that is a parity decision rather than a
    # tidiness one. §2.0 contracts it VARCHAR, `integration_tests/seeds/
    # er_fake_1000.yml` declares it VARCHAR, and DesignDoc §1 is explicit that
    # "a BIGINT id and a VARCHAR id are different products".
    #
    # `pd.read_csv` infers int64, so the baselines were minted as the OTHER
    # product. D3's blocking predicate is `l.<uid> < r.<uid>`, and that
    # comparison is type-dependent: `'507' < '64'` is true as text and false as
    # integers. Measured on the frozen fixture: **148 of 3,989 pairs order
    # differently**, so Stage 3's acceptance criterion -- exact
    # `(unique_id_l, unique_id_r, match_key)` set equality -- would have failed
    # on all 148 against a package that honours its own contract (D.0 finding 82).
    #
    # The tempting repair is to canonicalise orientation in the comparator. That
    # would hide exactly the id-type mistake this catches, and Stage 6 depends on
    # the same property (S2).
    #
    # `[RUN]` before changing it: the trained model is unaffected -- `u` is
    # bit-identical between the two readings and `m` differs by 5e-16, inside
    # A.4's trained-parameter tolerance. So this re-mints the pair sets without
    # re-freezing the model.
    frame = pd.read_csv(fixture, dtype={"unique_id": str})

    # 1. Save the model JSON, then RELOAD it. Section 3.4 is normative that
    #    baselines are generated from a saved-and-reloaded artefact, because
    #    that is the path a consumer takes and the only one that exercises
    #    serialisation.
    model_json.parent.mkdir(parents=True, exist_ok=True)
    if refreeze:
        trained = _train(_linker(frame, build_settings()))
        trained.misc.save_model_to_json(str(model_json), overwrite=True)
    elif not model_json.is_file():
        msg = (
            f"{rel(model_json, ROOT)} does not exist, so there is no frozen model "
            f"to generate baselines from. Pass --refreeze to mint one; that is a "
            f"deliberate act because it moves the artefact every parity claim in "
            f"this project is measured against."
        )
        raise RuntimeError(msg)
    # Splink writes the JSON without a trailing newline, which `end-of-file-fixer`
    # then adds -- so every regeneration left a dirty tree AND changed the sha
    # the manifest had just recorded. Normalise here, before the sha is taken.
    model_json.write_text(
        model_json.read_text(encoding="utf-8").rstrip("\n") + "\n", encoding="utf-8"
    )
    reloaded = json.loads(model_json.read_text(encoding="utf-8"))

    linker = _linker(frame, reloaded)
    predictions = linker.inference.predict()
    pairs = predictions.as_pandas_dataframe()

    # 2. Determinism, asserted rather than assumed.
    again = _linker(frame, reloaded).inference.predict().as_pandas_dataframe()
    if not pairs.equals(again):
        msg = (
            "two prediction runs over identical input disagree. The baseline is "
            "not reproducible in the process that generated it, so it will not be "
            "reproducible in CI -- do not freeze it."
        )
        raise RuntimeError(msg)

    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    pairs_path = out_dir / "predictions.parquet"
    _write_parquet(pairs, pairs_path)
    written.append(pairs_path)

    # 2a. The two INTERMEDIATES Stage 2 needs as oracles. Until now this harness
    #     emitted only end-products -- predictions and clusters -- so Stage 2's
    #     acceptance criteria had nothing to compare against and `stg_input`'s
    #     "equals Splink's concat excluding `__splink_salt`" was a claim in a
    #     model description rather than an assertion. A description that states
    #     parity without a test is exactly the inert-config defect this project
    #     keeps finding (D.0 finding 70).
    concat = _concat_frame(linker)
    concat_path = out_dir / "concat.parquet"
    _write_parquet(concat, concat_path)
    written.append(concat_path)

    # 2b. Stage 10's oracle. Frozen NOW, before any measurement model exists,
    #     because Stage 0.3's whole lesson is that a baseline format retrofitted
    #     after the fact is the expensive path -- and this one carries two
    #     details that would each have been discovered late:
    #
    #     * `truth_threshold` is a MATCH WEIGHT (log-odds), not a probability,
    #       and the table is a SWEEP over rounded weights -- 404 rows here, not
    #       one row per configured threshold.
    #     * `tn` counts the FULL comparison space (495,130), not the generated
    #       pairs. A confusion matrix built over blocked pairs only would agree
    #       on tp/fp/fn and be wrong on every rate that uses tn.
    blocked_path = out_dir / "blocked_pairs.parquet"
    _write_parquet(_blocked_pairs_frame(linker), blocked_path)
    written.append(blocked_path)

    vectors_path = out_dir / "comparison_vectors.parquet"
    _write_parquet(_comparison_vectors_frame(linker), vectors_path)
    written.append(vectors_path)

    accuracy_path = out_dir / "accuracy_analysis.parquet"
    _write_parquet(_accuracy_frame(linker), accuracy_path)
    written.append(accuracy_path)

    tf_path = out_dir / "tf_all.parquet"
    _write_parquet(_tf_long_frame(linker, concat), tf_path)
    written.append(tf_path)

    for threshold in THRESHOLDS:
        clusters = linker.clustering.cluster_pairwise_predictions_at_threshold(
            predictions, threshold_match_probability=threshold
        ).as_pandas_dataframe()
        path = out_dir / f"clusters_at_{str(threshold).replace('.', '_')}.parquet"
        _write_parquet(clusters, path)
        written.append(path)

    manifest = {
        "kind": "baseline",
        # 3.55 / section 20.4: the source fixture is synthetic and says so.
        "synthetic": True,
        "splink_version": splink.__version__,
        "model_json_sha256": _sha256(model_json),
        "seed": SEED,
        # Recorded because it is the difference between an artefact that can be
        # re-derived and one that cannot -- see MINT_THREADS. A manifest that
        # omits it cannot explain why a regeneration disagreed.
        "mint_threads": MINT_THREADS,
        "duckdb_version": duckdb.__version__,
        # RC54: sqlglot, not Splink, decides which levels get a TF adjustment,
        # and it arrives transitively -- a baseline under a different sqlglot is
        # a different baseline with no visible difference.
        # sqlglot ships no __version__ export; read it the way it is published.
        "sqlglot_version": getattr(sqlglot, "__version__", "unknown"),
        "platform": _platform_triple(duckdb.__version__),
        "date": _git("log", "-1", "--format=%cs") or "unknown",
        # Recorded rather than merely done: S1's exclusion is now a fact a test
        # can assert, instead of a filter invisible in the artefact.
        "excluded_columns": [SALT_COLUMN],
        "producing_commit": _git("rev-parse", "--short", "HEAD"),
        "source_fixture": rel(fixture, ROOT),
        "source_fixture_sha256": _sha256(fixture),
        "thresholds": list(THRESHOLDS),
        "ground_truth_column": "cluster",
        # Stated so nobody reads "baseline green" as "everything is covered".
        "not_exercised_by_this_fixture": [
            (
                "Splink's NULL-node rows on dangling edges "
                "(connected_components.py:89-100): a dedupe_only run over one table "
                "cannot produce a dangling edge. fixtures/degenerate/ covers it."
            ),
            (
                "Two records sharing a unique_id (G9): fake_1000 has none. "
                "fixtures/degenerate/shared_unique_id.csv covers it."
            ),
        ],
    }

    # The model JSON is a generated artefact too, and 3.62 is right to ask for
    # its provenance: it is the input every baseline here was produced from, so
    # a baseline whose model JSON cannot be traced is not reproducible either.
    written.append(model_json)

    # Iterate a COPY: the loop appends to `written`, and iterating the live
    # list made each sidecar the subject of the next one.
    for path in list(written):
        sidecar = path.with_suffix(path.suffix + ".manifest.yml")
        sidecar.write_text(
            "---\n# Generated by scripts/gen_baseline.py (Stage 0.3). Do not hand-edit:\n"
            "# section 20.1 requires regeneration through the target that writes this file.\n"
            + _to_yaml(manifest),
            encoding="utf-8",
        )
        written.append(sidecar)

    return written


def _to_yaml(data: dict[str, Any], indent: int = 0) -> str:
    """Minimal YAML writer, so the generator needs no serialiser dependency."""
    pad = "  " * indent
    lines: list[str] = []
    for key, value in data.items():
        if isinstance(value, dict):
            lines.append(f"{pad}{key}:")
            lines.append(_to_yaml(value, indent + 1).rstrip("\n"))
        elif isinstance(value, list):
            lines.append(f"{pad}{key}:")
            for item in value:
                rendered = json.dumps(item)
                if isinstance(item, str) and len(rendered) + len(pad) > _MAX_LINE:
                    # Folded scalar, so yamllint's line-length rule holds. Safe
                    # here because these are prose; D.0 records that a folded
                    # scalar BREAKS a JSON literal containing a space, so this
                    # is deliberately not used for structured values.
                    lines.append(f"{pad}  - >-")
                    lines.extend(
                        f"{pad}      {chunk}" for chunk in textwrap.wrap(item, _WRAP_WIDTH)
                    )
                else:
                    lines.append(f"{pad}  - {rendered}")
        elif isinstance(value, bool):
            lines.append(f"{pad}{key}: {str(value).lower()}")
        elif isinstance(value, (int, float)):
            lines.append(f"{pad}{key}: {value}")
        else:
            lines.append(f"{pad}{key}: {json.dumps(str(value))}")
    return "\n".join(lines) + "\n"


def main() -> int:
    """Generate baselines for the named fixture."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--fixture",
        type=Path,
        default=FIXTURES / "source" / "fake_1000.csv",
        help="source CSV to generate baselines from",
    )
    parser.add_argument("--out", type=Path, default=BASELINE_DIR / "fake_1000")
    parser.add_argument("--model-json", type=Path, default=MODEL_JSON_DIR / "fake_1000_v1.json")
    parser.add_argument(
        "--refreeze",
        action="store_true",
        help=(
            "retrain and OVERWRITE the frozen model. Without this the frozen "
            "model is read, not rewritten -- see D.0 finding 71."
        ),
    )
    args = parser.parse_args()

    logging.disable(logging.CRITICAL)
    warnings.filterwarnings("ignore")

    written = generate(args.fixture, args.out, args.model_json, refreeze=args.refreeze)
    for path in written:
        sys.stdout.write(f"  wrote {rel(path, ROOT)}\n")
    sys.stdout.write(f"{len(written)} file(s) written.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
