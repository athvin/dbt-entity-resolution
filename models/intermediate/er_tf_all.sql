{#-
  D7a -- term frequency is FROZEN BY SNAPSHOT, not recomputed from the corpus.

  §3.5 describes how Splink *computes* TF, and the naive reading of D7 recomputes
  it from `stg_input` on every run. That is the wrong default, for a reason that
  has nothing to do with parity: **TF is a property of the whole corpus**, so
  recomputing it makes every pair's score a function of unrelated records.

  D7a measures it. With `u_exact = 0.001` and `POW(u_exact / GREATEST(tf_l,
  tf_r), w)`, a value whose `tf` moves from 0.0909 to 0.45 because **nine
  unrelated records arrived** shifts that comparison's contribution from
  **-6.51 to -8.81 bits** -- on a pair whose own two records did not change.
  Entities churn on ingest of data that has nothing to do with them.

  So `frozen` is the default and the mode every parity and production run uses.
  `refresh` recomputes from the live corpus and exists only to MINT a snapshot.

  **A missing value RAISES. It is never COALESCEd.** This is D7a step 2 and it is
  the rule most likely to be "fixed" by someone reading a null as a gap: §3.2
  shows Splink's NULL semantics are *"substitute the other side, or no adjustment
  if neither"*, which is **not** the same as treating a value as unseen.
  Defaulting an absent `tf` produces wrong scores that look entirely plausible --
  no error, no null, just a systematically mis-weighted comparison. The guard is
  a data test (`er_tf_all_covers_the_corpus`) rather than something this SQL can
  express, because it is a statement about the join between two models.

  Two consequences of freezing, both D7a's rather than this model's. A refresh is
  a **full rescore** of the whole corpus -- `er_tf_snapshot_id` is part of every
  scored row's identity (M8) -- see G7. And the snapshot **outlives the records
  that produced it**: it is a distribution over the corpus, so erasing a source
  record does not erase its contribution, by design. An erasure event is
  therefore a refresh trigger -- see G4.
-#}

{{ config(materialized='table') }}

{%- set mode = var('er_tf_mode', 'frozen') -%}
{%- set model_sha = var('er_model_sha', '') -%}
{%- set snapshot_id = var('er_tf_snapshot_id', '') -%}
{%- set snapshot_relation = var('er_tf_snapshot_relation', '') -%}
{%- set tf_columns = var('er_tf_columns', []) -%}

{%- if mode not in ['frozen', 'refresh'] -%}
  {{ exceptions.raise_compiler_error(
       "ER-062: er_tf_mode is '" ~ mode ~ "', which is neither 'frozen' nor "
       ~ "'refresh'. Frozen reads a minted snapshot and is what every parity and "
       ~ "production run uses; refresh recomputes from the live corpus and exists "
       ~ "only to mint one. See D7a."
  ) }}
{%- endif -%}

{#- The identity of a scored row includes both of these (M8). Without them a
    snapshot cannot be told from its successor, and a partial rescore -- which
    D7a makes illegal -- becomes undetectable rather than impossible. -#}
{%- if not model_sha -%}
  {{ exceptions.raise_compiler_error(
       "ER-063: `er_model_sha` is not set, so term frequencies cannot be keyed to "
       ~ "the model that produced them. The sidecar publishes it; run "
       ~ "scripts/er_sidecar.py over your model JSON. See D7a and M8."
  ) }}
{%- endif -%}

{%- if not snapshot_id -%}
  {{ exceptions.raise_compiler_error(
       "ER-064: `er_tf_snapshot_id` is not set. A TF refresh is an explicit, "
       ~ "dated operation that mints a new id and re-scores (D7a step 4); without "
       ~ "one, drift becomes a side effect of ingest rather than a reviewable "
       ~ "event."
  ) }}
{%- endif -%}

{%- if mode == 'frozen' -%}

  {%- if not snapshot_relation -%}
    {{ exceptions.raise_compiler_error(
         "ER-065: er_tf_mode is 'frozen' but `er_tf_snapshot_relation` is not "
         ~ "set. This package ships no sources (M4b) -- name the relation holding "
         ~ "your minted snapshot, keyed (er_model_sha, er_tf_snapshot_id, "
         ~ "column_name, value, tf). To mint one, set er_tf_mode='refresh'."
    ) }}
  {%- endif -%}

  {#- Filtered, not trusted: a snapshot relation may legitimately hold several
      generations. Selecting all of them would multiply every downstream join
      by the number of snapshots present -- silently, and with plausible
      results, because each row on its own is well-formed. -#}
  select
      er_model_sha,
      er_tf_snapshot_id,
      column_name,
      value,
      tf
  from {{ snapshot_relation }}
  where er_model_sha = '{{ model_sha }}'
    and er_tf_snapshot_id = '{{ snapshot_id }}'

{%- else -%}

  {%- if tf_columns | length == 0 -%}
    {{ exceptions.raise_compiler_error(
         "ER-066: er_tf_mode is 'refresh' but `er_tf_columns` is empty, so the "
         ~ "minted snapshot would hold no distributions at all -- and every TF "
         ~ "adjustment would silently vanish rather than fail. The sidecar "
         ~ "derives this list from the same predicate that decides bf_tf_adj_; "
         ~ "an empty one means no comparison carries a TF adjustment. See D7a."
    ) }}
  {%- endif -%}

  {%- for column in tf_columns %}
  select
      '{{ model_sha }}' as er_model_sha,
      '{{ snapshot_id }}' as er_tf_snapshot_id,
      column_name,
      value,
      tf
  from (
      {{ er_term_frequency_long_sql(column, ref('er_stg_input')) }}
  ) as tf_long_{{ loop.index0 }}
  {%- if not loop.last %}
  union all
  {%- endif -%}
  {%- endfor %}

{%- endif -%}
