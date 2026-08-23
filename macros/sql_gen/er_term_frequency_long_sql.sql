{#-
  D7's LONG format -- one row per `(column_name, value)`, for `er_tf_all`.

  §3.5 and Splink's own generator emit TF **wide**: one table per column, with
  the value column named after the column and a `tf_<col>` beside it. That shape
  makes the grain a function of the model JSON -- add a TF-adjusted comparison
  and the table gains a column, so every contract, every join key and every
  downstream test changes shape with the model. D7a keys `tf_all` on
  `(er_model_sha, er_tf_snapshot_id, column_name, value, tf)` precisely to avoid
  that: the model decides the *rows*, never the columns.

  **This wraps `er_term_frequency_sql` rather than restating it**, and that is
  the whole point of the macro existing. §3.5's denominator rule -- `count(<col>)`
  and not `count(*)` -- is the silent one: with `count(*)` every frequency comes
  out proportionally too small, the ordering is unchanged, the values look
  entirely reasonable, and the adjustment is systematically wrong. Restating the
  arithmetic here would give that rule a second home and 3.82 would guard only
  one of them.

  `value` is cast to VARCHAR because the key is heterogeneous by construction:
  one table holds the distributions of every TF-adjusted column, and those
  columns need not share a type. Splink compares term frequencies by equality on
  the source value, so a lossless text rendering preserves the semantics.
-#}

{% macro er_term_frequency_long_sql(column_name, relation) %}
  {%- if not column_name -%}
    {{ exceptions.raise_compiler_error(
         "ER-055: er_term_frequency_long_sql() was called with no column name. "
         ~ "See section 3.5 and D7a."
    ) }}
  {%- endif -%}
    select
    '{{ column_name }}' as column_name,
    cast("{{ column_name }}" as varchar) as value,
    tf_{{ column_name }} as tf
    from (
        {{ er_term_frequency_sql(column_name, relation) }}
    ) as tf_wide_{{ column_name }}
{% endmacro %}
