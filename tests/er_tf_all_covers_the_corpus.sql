{#-
  D7a step 2 -- a corpus value absent from the frozen snapshot RAISES.

  This is the rule most likely to be "fixed" into uselessness. The tempting
  implementation is `coalesce(tf, <something>)` in the scoring join, and it is
  wrong in the specific way this project keeps meeting: it produces **no error,
  no null, and a plausible-looking score**. Section 3.2 shows Splink's NULL
  semantics are *"substitute the other side, or no adjustment if neither"* --
  which is emphatically not the same as treating a value as unseen. A defaulted
  `tf` silently mis-weights every comparison that touches it.

  It has to be a data test rather than a compile-time raise or a model-level
  constraint, because it is a statement about the **join** between two models:
  every non-null value present in `er_stg_input`, for every TF-adjusted column,
  must have a row in `er_tf_all`. Neither model can see that alone.

  Why this is not merely belt-and-braces over the primary key: the contract
  guarantees the snapshot's rows are well-formed and unique. It says nothing
  about whether they COVER the corpus, and coverage is the property that
  matters -- a snapshot missing a single value is structurally perfect and
  semantically wrong.

  Returns one row per uncovered value, so a failure names what is missing
  rather than only that something is.
-#}

{#- `er_core` because the models it joins are `access: private` to that group
    (dbt_project.yml). Set HERE rather than via a package-level `data_tests:`
    block: C.1 delta 11 keeps that block out of the package deliberately, since
    `store_failures_as: table` there would materialise person data into every
    consumer's warehouse (section 12.5, 3.52). -#}
{{ config(group='er_core') }}

{%- set tf_columns = var('er_tf_columns', []) -%}

{%- if tf_columns | length == 0 -%}
  {#- No TF-adjusted comparison means nothing to cover. Selecting zero rows
      would be a vacuous PASS (section 6.1), so say so explicitly instead. -#}
  select
      'no-tf-columns' as column_name,
      'er_tf_columns is empty, so this test asserted nothing' as value
  where false
{%- else -%}

{%- for column in tf_columns %}
select
    '{{ column }}' as column_name,
    cast(corpus."{{ column }}" as varchar) as value
from {{ ref('er_stg_input') }} as corpus
left join {{ ref('er_tf_all') }} as tf
       on tf.column_name = '{{ column }}'
      and tf.value = cast(corpus."{{ column }}" as varchar)
where corpus."{{ column }}" is not null
  and tf.value is null
{%- if not loop.last %}
union all
{%- endif -%}
{%- endfor %}

{%- endif -%}
