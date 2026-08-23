{#-
  §3.5 / D7 -- term frequency. The denominator is the NON-NULL count.

  Splink's generator (`term_frequencies.py:33-48`):

      select <col>, cast(count(*) as float8) / (select count(<col>) from <t>)
      from <t> where <col> is not null group by <col>

  **`count(<col>)`, not `count(*)`.** SQL's `count(<col>)` skips NULLs, and the
  `WHERE` clause skips them in the numerator too -- so both sides of the
  division cover the same population and the frequencies sum to exactly `1.0`.

  Using `count(*)` is the natural mistake and it is **silent**: every frequency
  comes out proportionally too small, the ordering is unchanged, the values look
  entirely reasonable, and the term-frequency adjustment is systematically
  wrong. The measured size of the error is the null rate -- `fake_1000` carries
  17-21% nulls per column, so `sum(tf)` would land near 0.8 instead of 1.0.

  §3.5's own test catches it directly: **assert `sum(tf) = 1.0` per column.**
  That is the assertion this macro exists to keep true, and it is cheaper than
  comparing frequencies value by value.

  D7a's freezing is the stage's other half and lives in the model, not here:
  `tf_all` is keyed `(er_model_sha, er_tf_snapshot_id, column_name, value, tf)`
  and is *read* from a frozen snapshot on every parity and production run. This
  macro is the `er_tf_mode='refresh'` path used only when minting one.
-#}

{% macro er_term_frequency_sql(column_name, relation) %}
  {%- if not column_name -%}
    {{ exceptions.raise_compiler_error(
         "ER-054: er_term_frequency_sql() was called with no column name. A term "
         ~ "frequency table with no subject is the empty-set pass section 6.1 "
         ~ "diagnoses."
    ) }}
  {%- endif -%}
    select
    "{{ column_name }}",
    cast(count(*) as float8) / (
        select count("{{ column_name }}") as total from {{ relation }}
    ) as tf_{{ column_name }}
    from {{ relation }}
    where "{{ column_name }}" is not null
    group by "{{ column_name }}"
{% endmacro %}
