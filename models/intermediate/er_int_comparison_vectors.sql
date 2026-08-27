{#-
  Stage 4 -- COMPARISON VECTORS. The critical path's second half.

  For every candidate pair, the gamma each comparison assigns it. Splink's
  `__splink__df_comparison_vectors`.

  **This is where the rename happens.** `er_int_candidate_pairs` carries
  `join_key_l`/`join_key_r` because that is what Splink's blocked-pair table
  calls them; Splink renames to `unique_id_l`/`unique_id_r` at this stage, and so
  does this model, so the two agree column-for-column at every stage boundary.

  **The gamma CASE is generated, not written.** `er_gamma_case_sql` is verified
  bit-identical against Splink's `Comparison._case_statement`, and it takes its
  levels from `er_comparisons` -- merged by the sidecar from two sources that
  cannot be merged here:

  * `sql_condition` is the raw model JSON's;
  * `comparison_vector_value` is **Splink's**, resolved by importing Splink
    itself, because A.2 C2 establishes it cannot be inferred from list position.

  Three things that are silently wrong if got wrong, all of them A.2/§3.3:

  1. **Levels render in exact JSON list order.** They are not sorted, not
     reordered highest-first, and the null level is not hoisted. §3.3's `[v1
     ERROR]` records the opposite belief; the ordering only looks descending
     because Splink's own creators write it that way, and a model that does not
     would silently score differently.
  2. **The ELSE level is identified by its `sql_condition` sentinel**, never by
     `comparison_vector_value == 0`. A model whose else level carries a
     different value -- or whose gamma 0 is an ordinary `WHEN` -- renders wrongly
     under the naive test and produces plausible numbers.
  3. **Transforms are applied INLINE in the CASE, never upstream** (D8). The
     projection below selects RAW columns; `lower()`, `substr` and
     `try_strptime` live inside the conditions, exactly as Splink emits them.
     Hoisting one into `er_stg_input` would apply it twice.

  The `*_l` / `*_r` attribute columns are carried through because the gamma
  conditions reference them by those names. The `tf_*_l` / `tf_*_r` columns come
  from `er_tf_all` -- **this is where D7a's frozen snapshot attaches to a pair**,
  and it is a LEFT join on purpose: a NULL source value has no snapshot row and
  must yield a NULL term frequency, which is what Splink does. A value that is
  present and unmatched is a different thing entirely and is a failure, caught by
  the `er_tf_all_covers_the_corpus` test rather than papered over with a
  `coalesce` here (D7a step 2).

  The column set is Splink's, exactly: comparison columns and their term
  frequencies, and NOT the ground-truth `cluster`, which is never an input to
  resolution.
-#}

{{ config(materialized='table', tags=['parity_stage_4']) }}

{%- set raw = var('er_comparisons') -%}
{%- set comparisons = fromjson(raw) if raw is string else raw -%}

{%- if comparisons | length == 0 -%}
  {{ exceptions.raise_compiler_error(
       "ER-071: `er_comparisons` is empty, so every pair would receive an "
       ~ "identical, empty comparison vector and Stage 5 would score them all "
       ~ "the same. The sidecar publishes this from the VALIDATED model JSON; "
       ~ "export DBT_ER_COMPARISONS from `er_sidecar.py --emit er_comparisons`. "
       ~ "See section 3.3 and A.2 C2."
  ) }}
{%- endif -%}

{#- Both lists come from the sidecar, so neither can drift from the model JSON
    the way a hand-maintained copy would (M2). -#}
{%- set compared = comparisons | map(attribute='output_column_name') | list -%}

{#- D11 rec 3 / 3.53: NARROW is the default and the wide `_l`/`_r` passthrough
    is opt-in per run. Measured 946 B/pair wide against 69.4 narrow -- a 13.6x
    difference, and the capacity budget in `er_max_pairs` is derived from the
    narrow figure. Splink's own equivalent is `retain_matching_columns`, and
    `gen_baseline.py` sets it TRUE deliberately (M14) because a baseline taken
    at defaults makes gamma equality the sole gate over a self-consistent wrong
    numbering. The oracle is wide; the product is narrow; the acceptance
    criterion is gamma equality, which does not need the passthrough.

    The term frequencies are NOT carried here either. They belong to scoring and
    Stage 5 joins `er_tf_all` for itself -- eight DOUBLEs per row would roughly
    double the narrow shape for a value that is one join away. -#}
{%- set retain = var('er_retain_matching_columns', false) -%}
{%- set input_columns = var('er_input_columns') -%}

select
    pairs.join_key_l as unique_id_l,
    pairs.join_key_r as unique_id_r,
    pairs.match_key,
    {%- if retain %}
    {#- The debug variant, opt-in per run and never the default. -#}
    {%- for column in compared %}
    "{{ column }}_l",
    "{{ column }}_r",
    {%- endfor %}
    {%- endif %}
    {%- for comparison in comparisons %}
    {{ dbt_er.er_gamma_case_sql(comparison['output_column_name'], comparison['levels']) }}
    {{- "," if not loop.last }}
    {%- endfor %}
{#- FROM-clause column aliasing, and it is what makes the narrow shape possible
    at all.

    Splink's level conditions arrive verbatim as `"city_l" = "city_r"` -- those
    names are part of the artefact this stage is asserting parity against, so
    rewriting them to `l."city" = r."city"` would mean the package no longer
    executes Splink's conditions but a translation of them. The obvious way to
    make them resolve is to project `l."city" as "city_l"`, which is exactly the
    `_l`/`_r` passthrough D11 rec 3 forbids: *"int_comparison_vectors carries
    unique_id_l, unique_id_r, match_key, and the gamma_* columns ONLY"*, measured
    at 267.9 B/pair wide against 69.4 narrow.

    `FROM tbl AS alias(col, ...)` renames the columns in the FROM clause, so the
    conditions resolve without anything being selected. Neither a subquery nor a
    CTE, both of which are banned here (`forbid_subquery_in = both`, and
    dbt-bouncer's leading-`with` rule).

    The alias list is positional, so it must match `er_stg_input`'s column order
    exactly -- which is `er_input_columns`, the same var that model projects
    from. A drift between them is a wrong-column-name error at build time, not a
    silent mis-comparison. -#}
from {{ ref('er_int_candidate_pairs') }} as pairs
inner join {{ ref('er_stg_input') }} as l(
    {%- for column in input_columns %}"{{ column }}_l"{{ ", " if not loop.last }}{% endfor %})
    on l."unique_id_l" = pairs.join_key_l
inner join {{ ref('er_stg_input') }} as r(
    {%- for column in input_columns %}"{{ column }}_r"{{ ", " if not loop.last }}{% endfor %})
    on r."unique_id_r" = pairs.join_key_r
