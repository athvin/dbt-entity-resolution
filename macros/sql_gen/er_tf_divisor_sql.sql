{#-
  §3.2 correction 2 -- the term-frequency divisor.

  `GREATEST(tf_l, tf_r)` expressed as a CASE with mutual `coalesce`, and it is
  **never** `LEAST` and never `coalesce(tf_l, tf_r)`
  (`comparison_level.py:595-617`). Splink deliberately uses the **more common**
  term's frequency, which gives the **smaller** boost -- so `LEAST` flips the
  direction on every pair where the two sides differ. A systematic divergence,
  not a floating-point one.

  `tf_minimum_u_value` floors the divisor, capping the boost at
  `(u_exact / tf_min)^weight`. It is **omitted from the JSON when 0**
  (`:665-666`), so it must default to `0.0` -- and D1's validation must not
  reject that as an out-of-range probability.

  In its own file because 3.33 requires a macro's name to match its file name,
  which dbt-bouncer's `check_macro_name_matches_file_name` enforces.
-#}

{% macro er_tf_divisor_sql(tf_left, tf_right, tf_minimum_u_value=0.0) %}
{%- set greatest -%}
case
        when coalesce({{ tf_left }}, {{ tf_right }}) >= coalesce({{ tf_right }}, {{ tf_left }})
        then coalesce({{ tf_left }}, {{ tf_right }})
        else coalesce({{ tf_right }}, {{ tf_left }})
    end
{%- endset -%}
{%- if tf_minimum_u_value and tf_minimum_u_value > 0 -%}
greatest({{ greatest }}, cast({{ tf_minimum_u_value }} as float8))
{%- else -%}
{{ greatest }}
{%- endif -%}
{% endmacro %}
