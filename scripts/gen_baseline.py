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
    """
    import duckdb  # noqa: PLC0415

    con = duckdb.connect()
    try:
        con.register("frame", frame)
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


def _linker(frame: pd.DataFrame, settings: Any) -> Any:
    from splink import DuckDBAPI, Linker  # noqa: PLC0415

    return Linker(frame, settings, db_api=DuckDBAPI(), set_up_basic_logging=False)


def generate(fixture: Path, out_dir: Path, model_json: Path) -> list[Path]:
    """Generate every baseline for one fixture. Returns the files written."""
    import duckdb  # noqa: PLC0415
    import pandas as pd  # noqa: PLC0415
    import splink  # noqa: PLC0415
    import sqlglot  # noqa: PLC0415

    frame = pd.read_csv(fixture)

    # 1. Save the model JSON, then RELOAD it. Section 3.4 is normative that
    #    baselines are generated from a saved-and-reloaded artefact, because
    #    that is the path a consumer takes and the only one that exercises
    #    serialisation.
    model_json.parent.mkdir(parents=True, exist_ok=True)
    trained = _train(_linker(frame, build_settings()))
    trained.misc.save_model_to_json(str(model_json), overwrite=True)
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
        "duckdb_version": duckdb.__version__,
        # RC54: sqlglot, not Splink, decides which levels get a TF adjustment,
        # and it arrives transitively -- a baseline under a different sqlglot is
        # a different baseline with no visible difference.
        # sqlglot ships no __version__ export; read it the way it is published.
        "sqlglot_version": getattr(sqlglot, "__version__", "unknown"),
        "platform": _platform_triple(duckdb.__version__),
        "date": _git("log", "-1", "--format=%cs") or "unknown",
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
    args = parser.parse_args()

    logging.disable(logging.CRITICAL)
    warnings.filterwarnings("ignore")

    written = generate(args.fixture, args.out, args.model_json)
    for path in written:
        sys.stdout.write(f"  wrote {rel(path, ROOT)}\n")
    sys.stdout.write(f"{len(written)} file(s) written.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
