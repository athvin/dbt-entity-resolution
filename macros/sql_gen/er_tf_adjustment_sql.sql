{#-
  §3.2 -- the term-frequency adjustment, `bf_tf_adj_<name>`.

  **[v1 ERROR]** v1 said *"`log2(tf_adjustment)` term with `tf_adjustment_weight`"*.
  The real form is a **separate multiplicative column**, a CASE covering **every**
  gamma value, with non-adjusted levels emitting `cast(1 as float8)`
  (`comparison_level.py:576-643`):

      POW( u_exact_match / divisor , tf_adjustment_weight )

  Three independent corrections, each a systematic divergence rather than a
  floating-point one:

  1. **The numerator is the EXACT-MATCH level's `u`, not the level's own.** A
     fuzzy level with its own `u = 0.01` renders the exact level's `0.001`.
     **That value is not in the model JSON** -- Splink resolves it by sqlglot CNF
     analysis of sibling levels, and a naive string match on `"<col>_l" =
     "<col>_r"` diverges on conditions like `a_l = a_r AND b_l = b_r`. This
     macro takes it from the A.2 sidecar, which resolves it with Splink's own
     code (`tf_u_exact_match`).

  2. **The divisor is `GREATEST(tf_l, tf_r)` by mutual coalesce** -- never
     `LEAST`, never `coalesce(tf_l, tf_r)`. Splink deliberately uses the *more
     common* term's frequency, giving the **smaller** boost. `LEAST` flips the
     direction on every pair where the two sides differ.

  3. **`tf_minimum_u_value` floors the divisor**, capping the boost at
     `(u_exact / tf_min)^weight`. It is **omitted from the JSON when 0**, so it
     must default to `0.0` -- and D1's validation must not reject that as an
     out-of-range probability.

  **NULL handling.** The guard is `coalesce(tf_l, tf_r) IS NOT NULL`, so the
  adjustment falls back to 1.0 only when **both** sides are NULL. With exactly
  one NULL the other side is substituted and the adjustment **is** applied.
  `COALESCE(tf, 0)` would produce `+inf`; `COALESCE(tf, 1)` zeroes it differently
  than Splink does.

  A level emits the constant `1.0` when it is the null level, has no
  `tf_adjustment_column`, has `tf_adjustment_weight == 0`, **or is the ELSE
  level** -- detected by the sentinel, *not* by `gamma == 0`.
-#}

{% macro er_tf_adjustment_sql(output_column_name, levels, tf_column=none) %}
  {%- set column = tf_column or output_column_name -%}
  {%- set tf_left = '"tf_' ~ column ~ '_l"' -%}
  {%- set tf_right = '"tf_' ~ column ~ '_r"' -%}
  case
  {% for level in levels %}
    {%- set is_else = level.sql_condition is defined
                      and level.sql_condition | trim | upper == 'ELSE' -%}
    {%- set adjusted = (not is_else)
                       and (not level.is_null_level)
                       and level.tf_u_exact_match is not none
                       and (level.tf_adjustment_weight | default(1.0)) != 0 -%}
    when gamma_{{ output_column_name }} = {{ level.comparison_vector_value }} then
    {%- if adjusted %}
      case when coalesce({{ tf_left }}, {{ tf_right }}) is not null
      then pow(
        cast({{ level.tf_u_exact_match }} as float8) / (
    {{- er_tf_divisor_sql(tf_left, tf_right, level.tf_minimum_u_value | default(0.0)) }}),
        cast({{ level.tf_adjustment_weight | default(1.0) }} as float8)
      )
      else cast(1 as float8) end
    {% else %} cast(1 as float8)
    {% endif %}
  {% endfor %}
  end as bf_tf_adj_{{ output_column_name }}
{% endmacro %}
