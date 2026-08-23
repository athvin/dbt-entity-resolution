{#-
  D8 -- `stg_input` is a BARE PASSTHROUGH, and the reason is stronger than
  "no cleaning logic here".

  `ColumnExpression` transforms -- `lower()`, `substr`, `try_strptime` -- are
  applied **inline inside the comparison CASE**, never in the concat table.
  Verified: with `ColumnExpression('first_name').lower().substr(1,3)` the gamma
  CASE contained `SUBSTRING(LOWER("first_name_l"), 1, 3)` while the projection
  feeding it selected the **raw** column.

  So hoisting a transform here would apply it twice -- once here and once inside
  the CASE -- and diverge from Splink on every level that uses one. The rule is
  not hygiene; it is parity.

  S1: `__splink_salt` is excluded from all concat-level comparison, and the pair
  set is provably invariant under salting, so it is simply not selected.

  Section 2.0 (DR-16): the package ships ZERO sources. A consumer names their
  own relation through `er_input_relation`, and every column the model JSON
  references must appear in `er_input_columns` -- checked at COMPILE time by the
  sidecar, naming the column, rather than surfacing as a `Binder Error` from
  inside a generated CASE.
-#}

{{ config(materialized='table') }}

{%- set relation = var('er_input_relation') -%}
{%- set columns = var('er_input_columns') -%}

{%- if not relation -%}
  {{ exceptions.raise_compiler_error(
       "ER-060: `er_input_relation` is not set. This package ships no sources "
       ~ "(M4b) -- name the relation holding your records, and list its columns "
       ~ "in `er_input_columns` (section 2.0)."
  ) }}
{%- endif -%}

{%- if not columns -%}
  {{ exceptions.raise_compiler_error(
       "ER-061: `er_input_columns` is empty. dbt parses schema.yml as YAML "
       ~ "before rendering Jinja, so the contract's column list must arrive as "
       ~ "a var (M2) -- an empty one contracts nothing and dbt raises "
       ~ "`raise_contract_error([], [])` at run time instead (columns_spec_ddl.sql:35-41)."
  ) }}
{%- endif -%}

select
{%- for column in columns %}
    "{{ column }}"{% if not loop.last %},{% endif %}
{%- endfor %}
from {{ relation }}
