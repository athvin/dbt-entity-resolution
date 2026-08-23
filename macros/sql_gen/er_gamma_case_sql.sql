{#-
  §3.3 -- the gamma CASE. Exact JSON list order, no reordering, no null-hoisting.

  **[v1 ERROR]** v1 said levels are *"evaluated highest-to-lowest"*. They are
  emitted in **exact JSON list order** (`Comparison._case_statement`). The
  ordering looks descending in practice only because Splink's own creators
  happen to write them that way -- sorting them would be a silent behaviour
  change on any model that does not.

  Splink's output for a two-level comparison, reproduced exactly:

      CASE WHEN "city_l" IS NULL OR "city_r" IS NULL THEN -1
           WHEN "city_l" = "city_r" THEN 1
           ELSE 0 END as gamma_city

  Two details that are easy to get subtly wrong:

  * **The ELSE level is identified by its `sql_condition` sentinel, not by
    `comparison_vector_value == 0`.** §3.2 makes the same point about the TF
    adjustment: *"detected by `_is_else_level`, not by `gamma == 0`"*. A model
    whose else level carries a different value -- or whose gamma 0 is an ordinary
    `WHEN` -- renders wrongly under the naive test.

  * **`comparison_vector_value` comes from the sidecar**, resolved by Splink
    (A.2 C2), never inferred from list position. The null level is `-1` by
    convention, but the convention is Splink's to keep, not ours to assume.
-#}

{% macro er_gamma_case_sql(output_column_name, levels) %}
  {%- if not levels -%}
    {{ exceptions.raise_compiler_error(
         "ER-052: er_gamma_case_sql() was called with no levels for `"
         ~ output_column_name ~ "`. A comparison with no levels emits a constant "
         ~ "gamma, which scores every pair identically on that column."
    ) }}
  {%- endif -%}
  {%- set ns = namespace(has_else=false) -%}
  case
  {%- for level in levels %}
    {%- if level.sql_condition | trim | upper == 'ELSE' -%}
      {%- set ns.has_else = true %}
    else {{ level.comparison_vector_value }}
    {%- else %}
    when {{ level.sql_condition }} then {{ level.comparison_vector_value }}
    {%- endif -%}
  {%- endfor %}
  {%- if not ns.has_else %}
    {#- No ELSE level means an unmatched pair yields NULL gamma, which
        propagates through every downstream bayes factor as NULL rather than
        as a score. Splink's own creators always emit one. -#}
    {{ exceptions.raise_compiler_error(
         "ER-053: `" ~ output_column_name ~ "` has no ELSE level. An unmatched "
         ~ "pair would produce a NULL gamma, and NULL propagates silently "
         ~ "through the bayes factors into a NULL match_weight."
    ) }}
  {%- endif %}
  end as gamma_{{ output_column_name }}
{% endmacro %}
