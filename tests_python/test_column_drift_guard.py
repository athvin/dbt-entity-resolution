"""RC57's drift guard: three artefacts, one model JSON (M2, D12).

M2's recommendation ends with *"add a pytest asserting `er_gamma_columns`
equals what `comparison_vector_sql` actually renders, or the two drift"*. D12's
interaction extends that to **three** artefacts, because the wrapper emitting
the column lists must also emit the unit-test fixtures:

  1. the **rendered SQL** -- what the model actually produces
  2. the **contracted column list** -- what `schema.yml` declares
  3. the **unit-test fixture** -- what 3.20's coverage gate checks

Hand-writing any of them re-creates exactly the second-hand-maintained copy M2
is about. The guard is what makes one JSON the single source.

**Why this is Stage 1 work and not Stage 4's.** dbt parses `schema.yml` as YAML
*before* rendering Jinja, so a `{% for %}` cannot emit YAML structure; the
column list must arrive as a `var`, and `SchemaYamlContext` offers no project
macros. M2's failure scenario is noticing that mid-Stage-4 -- *"a Stage-1
decision being made in Stage 4"*.
"""

from __future__ import annotations

import json
import pathlib
import re
import sys
from typing import Any

import jinja2

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import er_sidecar  # noqa: E402

MODEL_JSON = ROOT / "fixtures" / "model_jsons" / "fake_1000_v1.json"


def _artefact() -> dict[str, Any]:
    return er_sidecar.build(MODEL_JSON.read_text(encoding="utf-8"))


def _macros() -> Any:  # noqa: ANN401 -- a Jinja module has no useful static type
    combined = "\n".join(
        (text[text.index("{% macro") :] if "{% macro" in text else text)
        for text in (
            p.read_text(encoding="utf-8")
            for p in sorted((ROOT / "macros" / "sql_gen").glob("*.sql"))
        )
    )
    env = jinja2.Environment(loader=jinja2.DictLoader({"m": combined}), autoescape=False)  # noqa: S701

    def _raise(message: str) -> None:
        raise RuntimeError(message)

    env.globals["exceptions"] = type("E", (), {"raise_compiler_error": staticmethod(_raise)})
    return env.get_template("m").make_module()


def _levels_for(name: str) -> list[dict[str, Any]]:
    raw_model = json.loads(MODEL_JSON.read_text(encoding="utf-8"))
    resolved = _artefact()["resolved"]
    raw = next(c for c in raw_model["comparisons"] if c["output_column_name"] == name)[
        "comparison_levels"
    ]
    res = next(c for c in resolved["comparisons"] if c["output_column_name"] == name)["levels"]
    return [{**a, **b} for a, b in zip(raw, res, strict=True)]


def test_gamma_columns_match_what_the_gamma_case_renders() -> None:
    """M2's own wording: "or the two drift"."""
    artefact = _artefact()
    declared = [c["name"] for c in artefact["er_gamma_columns"]]
    rendered: list[str] = []
    for comparison in artefact["resolved"]["comparisons"]:
        name = comparison["output_column_name"]
        match = re.search(r"as (gamma_\w+)", _macros().er_gamma_case_sql(name, _levels_for(name)))
        assert match is not None, f"{name} rendered no gamma column at all"
        rendered.append(match.group(1))
    assert declared == rendered


def test_bf_tf_adj_is_declared_only_where_the_sql_emits_it() -> None:
    """`dob` has an exact-match level but no TF adjustment.

    Declaring `bf_tf_adj_dob` unconditionally would contract a column the SQL
    never produces, and dbt raises on that mismatch at **run** time inside the
    materialization -- long after review.
    """
    artefact = _artefact()
    declared = {c["name"] for c in artefact["er_bf_columns"]}
    assert "bf_dob" in declared
    assert "bf_tf_adj_dob" not in declared
    assert "bf_tf_adj_city" in declared


def test_the_bf_columns_match_splinks_own_product() -> None:
    """The list Splink multiplies in its predict projection, in order.

    Captured from `fixtures/snapshots/scoring_fake_1000_v1.sql`, so this is a
    parity assertion rather than a restatement of my own derivation.
    """
    snapshot = (ROOT / "fixtures" / "snapshots" / "scoring_fake_1000_v1.sql").read_text(
        encoding="utf-8"
    )
    in_splink = re.findall(r"\b(bf_tf_adj_\w+|bf_\w+)\b", snapshot)
    ordered: list[str] = []
    for name in in_splink:
        if name not in ordered:
            ordered.append(name)
    declared = [c["name"] for c in _artefact()["er_bf_columns"]]
    assert declared == ordered


def test_every_declared_column_carries_a_data_type() -> None:
    """`columns_spec_ddl.sql:35-41` raises on an empty column list.

    A contract with names but no types is a contract that checks the names
    only -- M3's finding, one level down.
    """
    artefact = _artefact()
    for key in ("er_gamma_columns", "er_bf_columns"):
        for column in artefact[key]:
            assert column["name"]
            assert column["data_type"] in {"INTEGER", "DOUBLE"}


def test_the_lists_are_native_jinja_shaped_not_strings() -> None:
    """M2's measured mechanism: a string leaf renders to a genuine list of dicts.

    `[RECON]` driving dbt's own `SchemaYamlRenderer` returned `columns` as a
    real `list` with `name`/`data_type` intact for `"{{ var('er_cols') }}"`,
    while the `{% for %}` form returned a `str`. The shape emitted here has to
    be the one that survives that path.
    """
    artefact = _artefact()
    assert isinstance(artefact["er_gamma_columns"], list)
    assert all(isinstance(c, dict) for c in artefact["er_gamma_columns"])
    assert set(artefact["er_gamma_columns"][0]) == {"name", "data_type"}


def test_names_are_normalised_the_same_way_the_lint_checks() -> None:
    """M2's `.replace(" ", "_")` -- the same normalisation `ER-040` rejects on.

    `[RECON]` `[ExactMatch('city'), LevenshteinAtThresholds('city',2)]` yields
    `['gamma_city','gamma_city']` with no error or warning from Splink.
    """
    raw = json.dumps(
        {
            "comparisons": [
                {
                    "output_column_name": "first name",
                    "comparison_levels": [
                        {"sql_condition": '"a_l" = "a_r"', "m_probability": 0.9},
                        {"sql_condition": "ELSE"},
                    ],
                }
            ]
        }
    )
    artefact = er_sidecar.build(raw)
    assert artefact["er_gamma_columns"][0]["name"] == "gamma_first_name"
