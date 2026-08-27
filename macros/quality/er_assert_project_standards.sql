{% macro er_assert_project_standards() %}
  {#-
    The compile gate. Section 2: the ONLY gate that travels with the package,
    because dbt-bouncer reads artifacts from this repository and therefore
    protects this repository only.

    `graph` is fully populated in an on-run-start hook: ManifestLoader calls
    build_flat_graph() before any task executes. At dbt-core 1.12.2 the flat
    graph exposes nodes, sources, exposures, metrics, groups, semantic_models,
    saved_queries, functions AND unit_tests -- which is what makes unit-test
    coverage enforceable here without a third-party tool.

    graph.nodes values are plain nested dicts (FlatGraphMapping.__getitem__
    returns .to_dict(omit_none=False)), so .get() is used throughout.

    C.4's delta table is applied here. Rows 1-3 were written; rows 4-11 are the
    ones RC49 enumerates and F12 left open, and they are written now.
  -#}

  {%- if not execute -%}
    {#- Hook Jinja is also rendered at parse time for ref extraction. -#}
    {{ return("select 1 as er_standards_not_evaluated_at_parse_time") }}
  {%- endif -%}

  {%- set pkg = 'dbt_er' -%}

  {#- ---- C.4 delta 7: the escape hatch, documented rather than discovered.
      Section 2.1's fourth row. Default true; a consumer who must disable the
      gate can, and 3.51 asserts in CI that WE never ship it false. -#}
  {%- if not var('er_standards_enabled', true) -%}
    {{ return("select 0 as er_standards_disabled_by_configuration") }}
  {%- endif -%}

  {#- ---- C.4 deltas 4 and 10: hardening values come from a package-owned
      macro, NOT from var(). See er_hardening() for why. -#}
  {%- set hard = dbt_er.er_hardening() -%}
  {%- set allowed_mat      = hard['allowed_materializations'] -%}
  {%- set must_carry       = hard['must_carry_constraints'] -%}
  {%- set exempt           = hard['standards_exempt'] -%}
  {%- set allowed_tags     = hard['allowed_tags'] -%}
  {%- set volatile_columns = hard['volatile_columns'] -%}
  {%- set run_contract     = hard['run_contract_columns'] -%}
  {%- set pair_grain       = hard['pair_grain_models'] -%}
  {%- set max_exemptions   = hard['max_standards_exemptions'] -%}

  {#- ---- policy vars stay vars: they are MEANT to be overridable. -#}
  {%- set min_model_desc = var('er_min_model_description_chars', 40) -%}
  {%- set min_col_desc   = var('er_min_column_description_chars', 10) -%}
  {%- set retain_lr      = var('er_retain_matching_columns', false) -%}

  {%- set sections = ['**Purpose**', '**Grain**', '**Upstream**',
                      '**Splink parity**', '**Determinism**', '**Caveats**'] -%}

  {%- set violations = [] -%}
  {%- set n = namespace(models=0) -%}

  {#- ---- 3.43: the exemption cap, asserted here rather than only in CI. -#}
  {%- if (exempt | length) > max_exemptions -%}
    {%- do violations.append(
      "er_standards_exempt holds " ~ (exempt | length) ~ " entries but the cap is "
      ~ max_exemptions ~ ". Raising the cap is a change to er_hardening(), "
      ~ "reviewed as code.") -%}
  {%- endif -%}

  {%- set unit_tested = [] -%}
  {%- for ut in graph.get('unit_tests', {}).values() -%}
    {%- do unit_tested.append(ut.get('model')) -%}
  {%- endfor -%}

  {%- for node in graph.nodes.values() -%}
    {%- if node.get('resource_type') == 'model'
           and node.get('package_name') == pkg -%}

      {%- set n.models = n.models + 1 -%}
      {%- set name     = node.get('name') -%}
      {%- set sql_path = node.get('original_file_path', '') -%}
      {%- set cfg      = node.get('config', {}) or {} -%}
      {%- set meta     = cfg.get('meta', {}) or {} -%}
      {%- set columns  = node.get('columns', {}) or {} -%}

      {#- ---- C.4 delta 6 / section 18.1: exemption is PER CHECK.
          The v1 macro short-circuited at the top of this loop on
          `name not in exempt`, which exempts a model from EVERY standard at
          once -- the form 18.1 condemns. `skip` is consulted per check, so an
          exemption names exactly what it excuses. -#}
      {%- set skip = exempt.get(name, []) -%}

      {#- ---- 1:1 colocated <model>.yml ---------------------------------- -#}
      {%- if '3.1' not in skip -%}
        {%- set raw_patch = node.get('patch_path') -%}
        {%- if raw_patch is none -%}
          {%- do violations.append(
            name ~ ": no properties file. Create "
            ~ sql_path | replace('.sql', '.yml')) -%}
        {%- else -%}
          {%- set yml = raw_patch.split('://')[-1] -%}
          {%- if yml.split('/')[-1] != name ~ '.yml' -%}
            {%- do violations.append(
              name ~ ": documented in '" ~ yml.split('/')[-1] ~ "'. The 1:1 rule "
              ~ "requires '" ~ name ~ ".yml'; folder-level schema.yml is banned.") -%}
          {%- endif -%}
          {%- if yml.rsplit('/', 1)[0] != sql_path.rsplit('/', 1)[0] -%}
            {%- do violations.append(
              name ~ ": properties file is not colocated with the .sql.") -%}
          {%- endif -%}
        {%- endif -%}
      {%- endif -%}

      {#- ---- 3.33 / section 10.5: identifiers (C.4 delta row: RC49 item 6) -#}
      {%- if '3.33' not in skip and not name.startswith('er_') -%}
        {%- do violations.append(
          name ~ ": model names carry the `er_` prefix. "
          ~ "require_unique_project_resource_names is on, so an unprefixed name "
          ~ "can collide with a consumer's own model.") -%}
      {%- endif -%}

      {#- ---- 3.42: the tag vocabulary is governed (RC49 item 7) ---------- -#}
      {%- if '3.42' not in skip -%}
        {%- for tag in (cfg.get('tags', []) or []) -%}
          {%- if tag not in allowed_tags -%}
            {%- do violations.append(
              name ~ ": tag '" ~ tag ~ "' is outside er_allowed_tags. A tag "
              ~ "nobody governs is a selector that silently selects nothing.") -%}
          {%- endif -%}
        {%- endfor -%}
      {%- endif -%}

      {#- ---- materialisation policy ------------------------------------- -#}
      {%- set mat = cfg.get('materialized') -%}
      {%- if '3.11' not in skip and mat not in allowed_mat -%}
        {%- do violations.append(
          name ~ ": materialized='" ~ mat ~ "' but policy allows only "
          ~ (allowed_mat | join('/')) ~ ". Constraints are SILENTLY INERT on "
          ~ "view/ephemeral -- dbt only warns -- so this is the only signal.") -%}
      {%- endif -%}

      {#- ---- 3.12: er_must_be_table -> er_must_carry_constraints.
          The property wanted is ENFORCEMENT; `table` is one way to get it, not
          the requirement (section 7.2). Never waivable, so `skip` is not
          consulted. -#}
      {%- if name in must_carry and mat not in ['table', 'incremental'] -%}
        {%- do violations.append(
          name ~ ": MUST carry physical constraint enforcement; got '" ~ mat
          ~ "'. dbt's materialization_enforces_constraints is true only for "
          ~ "table and incremental -- anywhere else the constraints are dropped "
          ~ "with a warning. Never waivable (3.12).") -%}
      {%- endif -%}

      {#- ---- contract + primary key ------------------------------------- -#}
      {%- set contract = cfg.get('contract', {}) or {} -%}
      {%- if '3.3' not in skip and mat in ['table', 'incremental']
             and not contract.get('enforced', false) -%}
        {%- do violations.append(
          name ~ ": contract.enforced is false. Contracts are how we prove every "
          ~ "output column is declared and typed -- dbt raises on an empty column "
          ~ "list, which no linter can be fooled into passing.") -%}
      {%- endif -%}

      {%- set has_pk = namespace(v=false) -%}
      {%- for c in node.get('constraints', []) -%}
        {%- if c.get('type') == 'primary_key' -%}{%- set has_pk.v = true -%}{%- endif -%}
        {%- if c.get('type') == 'foreign_key' -%}
          {%- do violations.append(
            name ~ ": foreign_key constraints are BANNED on dbt-duckdb. DuckDB "
            ~ "refuses ALTER TABLE RENAME/DROP on an FK parent and dbt renames "
            ~ "existing->backup on every rebuild, so the SECOND run fails "
            ~ "permanently. Use a `relationships` test instead.") -%}
        {%- endif -%}
      {%- endfor -%}
      {%- for col in columns.values() -%}
        {%- for c in col.get('constraints', []) -%}
          {%- if c.get('type') == 'primary_key' -%}{%- set has_pk.v = true -%}{%- endif -%}
          {%- if c.get('type') == 'foreign_key' -%}
            {%- do violations.append(name ~ ": column-level foreign_key is banned.") -%}
          {%- endif -%}
        {%- endfor -%}
      {%- endfor -%}
      {%- if '3.6' not in skip and mat == 'table' and not has_pk.v
             and not meta.get('primary_key_by_test_reason') -%}
        {%- do violations.append(
          name ~ ": no primary_key constraint. Pair-grain models may omit the DDL "
          ~ "key -- a composite VARCHAR PK measured ~100x the cost of the test and "
          ~ "builds a multi-GiB ART index -- but MUST record "
          ~ "config.meta.primary_key_by_test_reason and carry "
          ~ "dbt_utils.unique_combination_of_columns.") -%}
      {%- endif -%}

      {#- ---- 3.53: the column budget (RC49 item 9) ----------------------
          D11 rec 3: the wide `_l`/`_r` passthrough variant is opt-in per run
          and NEVER the default. 946 B/pair against 69.4 measured. -#}
      {#- The pair KEY ends in `_l`/`_r` too, and it is not passthrough -- it is
          the grain. Flagging it made every Stage 4 model unbuildable, and the
          fix that first suggests itself is `er_retain_matching_columns: true`,
          which silently commits the project to the 946 B/pair wide shape and
          the capacity budget derived from 69.4 (D.0 finding 86). -#}
      {%- set pair_key = ['unique_id_l', 'unique_id_r'] -%}
      {%- if name in pair_grain and not retain_lr -%}
        {%- for col_name in columns.keys() if col_name not in pair_key -%}
          {%- if col_name.endswith('_l') or col_name.endswith('_r') -%}
            {%- do violations.append(
              name ~ "." ~ col_name ~ ": `_l`/`_r` passthrough column on a "
              ~ "pair-grain model with er_retain_matching_columns false. The wide "
              ~ "shape measured 946 B/pair against 69.4 narrow (D11); it is a "
              ~ "debug variant, never the default.") -%}
          {%- endif -%}
        {%- endfor -%}
      {%- endif -%}

      {#- ---- 3.48: run-contract columns must be excluded from every hash
          (RC49 item 8). Splitting this pairing is how every determinism hash
          starts changing on every run, and the gate that proves output
          stability starts proving the opposite. -#}
      {%- for col_name in columns.keys() -%}
        {%- if col_name in run_contract and col_name not in volatile_columns -%}
          {%- do violations.append(
            name ~ "." ~ col_name ~ ": run-contract column is not listed in "
            ~ "er_volatile_columns. It changes every run, so leaving it in the "
            ~ "content hash makes the determinism gate prove the opposite of "
            ~ "what it claims (3.48).") -%}
        {%- endif -%}
      {%- endfor -%}

      {#- ---- documentation ---------------------------------------------- -#}
      {%- if '3.5' not in skip -%}
        {%- set desc = (node.get('description') or '') | trim -%}
        {%- if desc | length < min_model_desc -%}
          {%- do violations.append(
            name ~ ": model description missing or under " ~ min_model_desc
            ~ " characters.") -%}
        {%- endif -%}
        {%- for s in sections -%}
          {%- if s not in desc -%}
            {%- do violations.append(
              name ~ ": description is missing the " ~ s ~ " section.") -%}
          {%- endif -%}
        {%- endfor -%}
      {%- endif -%}
      {%- if '3.4' not in skip -%}
        {%- if (columns | length) == 0 -%}
          {%- do violations.append(name ~ ": no columns documented.") -%}
        {%- endif -%}
        {%- for col_name, col in columns.items() -%}
          {%- if ((col.get('description') or '') | trim | length) < min_col_desc -%}
            {%- do violations.append(
              name ~ "." ~ col_name ~ ": column description missing or too short.") -%}
          {%- endif -%}
          {%- if not col.get('data_type') -%}
            {%- do violations.append(
              name ~ "." ~ col_name ~ ": no data_type declared.") -%}
          {%- endif -%}
        {%- endfor -%}
      {%- endif -%}

      {#- ---- unit-test coverage (C.4 deltas 1, 2 and 3) ------------------
          Delta 1: BOTH guards dropped. `ephemeral` has no subject under D11,
          and the `recursive` guard was the automatic exemption D12 removes --
          M17's own [RECON] showed recursive USING KEY models unit-test fine on
          the pinned toolchain, so the guard argued against its own evidence.

          Delta 2: the message names the FIX, not the escape. The v1 text read
          "Models using recursive SQL are exempt by policy but must declare
          config.meta.sql_features: 'recursive'" -- an error advertising the
          waiver is a waiver being recommended.

          Delta 3: sql_features keeps its other readers (section 7.2's custom
          materialization path). It simply stops being read HERE. -#}
      {%- if '3.20' not in skip and name not in unit_tested -%}
        {%- do violations.append(
          name ~ ": no unit test. Every model needs one (DbtBestPractices 3.20 / "
          ~ "DesignDoc D12); see section 12.2 for the case checklist, and note "
          ~ "that a `unit_tests:` block nested under a `models:` entry is "
          ~ "SILENTLY IGNORED -- it belongs at the top level of the file.") -%}
      {%- endif -%}

    {%- endif -%}
  {%- endfor -%}

  {%- if (violations | length) > 0 -%}
    {%- set msg -%}
dbt-er project standards: {{ violations | length }} violation(s) across {{ n.models }} model(s).
{% for v in violations | sort %}  - {{ v }}
{% endfor %}
Fix these. The policy vars (er_min_*_chars) are tunable; the hardening values
are not, and are defined in the package macro er_hardening() precisely so a
consumer's root configuration cannot reach them (section 2.1).

An exemption is a dated, reasoned, per-check entry in er_hardening()'s
`standards_exempt` mapping -- reviewed as code, capped, and echoed on every run.
See docs/DbtBestPractices.md section 18.
    {%- endset -%}
    {%- do exceptions.raise_compiler_error(msg) -%}
  {%- endif -%}

  {#- ---- 3.43: the active exemption list is ECHOED on every successful run.
      "A waiver that is greppable is a waiver someone can revisit." -#}
  {%- if (exempt | length) > 0 -%}
    {%- do log("dbt-er: " ~ (exempt | length) ~ " active standards exemption(s): "
               ~ (exempt | tojson), info=true) -%}
  {%- endif -%}

  {{ return("select " ~ n.models ~ " as er_models_passing_standards") }}
{% endmacro %}
