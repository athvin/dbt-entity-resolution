{#-
  D2 -- blocking, and the `match_key` that goes with it (Stage 1, critical path).

  **v1 got this wrong and D2 records the correction**: v1 described "rule 2
  `AND NOT` (rule 1)", i.e. one exclusion per preceding rule. The real generator
  emits a SINGLE `AND NOT ( ... OR ... )` over every preceding rule, in the
  WHERE clause (`blocking.py:151-184`). Verified against Splink 4.0.16's own
  output, snapshotted at `fixtures/snapshots/blocking_fake_1000_v1.sql`.

  Four details are load-bearing, each one a way to be silently wrong:

  * **`coalesce((<rule>), false)`** -- without it a NULL in a preceding rule
    DELETES pairs rather than failing to exclude them. Splink's own comment says
    so, and this is the difference between "rule 3 finds 500 pairs" and "rule 3
    finds 380 pairs and nobody notices".

  * **`match_key` is a VARCHAR literal** -- `'0'`, `'1'` -- never an integer.
    §12.7's comparator suite carries a mutant for exactly this, because a
    dtype-coercing comparison normalises a real divergence away.

  * **`UNION ALL`, never `UNION`** and never `DISTINCT`. The only dedupe in the
    whole of blocking is the exploding-rule path (S4), which v1 is out of scope.

  * **`l.<uid> < r.<uid>`**, strictly. D3's ordering is what makes the pair set
    canonical -- and G9's finding is its cost: two records sharing a `unique_id`
    never pair with each other, so §2.0 makes `unique_id` UNIQUE and
    `fixtures/degenerate/shared_unique_id.csv` pins the case.

  The emitted relation's key columns are `join_key_l` / `join_key_r`, matching
  Splink. They become `unique_id_l` / `unique_id_r` only after Stage 4 joins the
  attributes back.
-#}

{% macro er_blocking_sql(blocking_rules, left_relation, right_relation, unique_id='unique_id') %}
  {%- if not blocking_rules -%}
    {{ exceptions.raise_compiler_error(
         "ER-050: er_blocking_sql() was called with no blocking rules. A blocking "
         ~ "stage that generates the full cross product is not blocking, and one "
         ~ "that generates nothing produces the zero-rows-to-zero-rows comparison "
         ~ "section 12.7 opens with."
    ) }}
  {%- endif -%}

  {%- for rule in blocking_rules %}
    {%- if not loop.first %}
 UNION ALL
    {%- endif %}
    select
    '{{ loop.index0 }}' as match_key,
    l."{{ unique_id }}" as join_key_l,
    r."{{ unique_id }}" as join_key_r
    from {{ left_relation }} as l
    inner join {{ right_relation }} as r
    on
    ({{ rule }})
    where l."{{ unique_id }}" < r."{{ unique_id }}"
    {%- if not loop.first %}
    AND NOT ({% for previous in blocking_rules[:loop.index0] -%}
coalesce(({{ previous }}),false){% if not loop.last %} OR {% endif %}
{%- endfor %})
    {%- endif %}
  {%- endfor %}
{% endmacro %}
