{#-
  Stage 3 -- BLOCKING. The critical path's highest-risk parity stage.

  Every pair the rest of the pipeline can ever score is decided here, and
  **recall lost here is unrecoverable downstream**: no comparison, no scoring
  and no clustering can recover a pair blocking never generated. §1.8 measures
  the cost of getting it wrong -- two missing rules take blocking recall from
  0.8124 to 0.5057, and every parity gate stays green throughout, because both
  engines agree about the pairs they were both given.

  The SQL is generated in full by `er_blocking_sql`, which is verified
  bit-identical against Splink's own `block_using_rules_sqls`. Nothing here is
  hand-written, and that is deliberate: this model's job is to *materialise* the
  generator's output under a contract, not to restate it.

  **Four properties that are each a way to be silently wrong.** All four are
  properties of the generator; they are recorded here because this is the file a
  reviewer reads, and every one of them fails without an error.

  1. **No filter on the join keys.** No `is not null`, no `<> ''`, no
     `coalesce(key, '')`. Splink's generated SQL contains none, so adding one
     diverges -- and D2's empty-string fixture measures the cost: the natural
     `where key is not null and key <> ''` deletes 3 of 4 pairs.

  2. **`AND NOT (coalesce(<prev>, false) OR ...)`, as ONE combined clause**, not
     one `AND NOT` per rule (v1's error). Stripping the `coalesce` costs
     **926 of 3,989 pairs, 23.2%**, and it is silent by construction: per-rule
     counts go 1998/1351/466/174 -> 1998/853/152/60 with no error and the right
     output shape. A NULL comparison is not false, and `NOT NULL` is NULL.

  3. **`match_key` is a VARCHAR literal**, never an integer. §3.1 and A.4 both
     require it compared as VARCHAR, and DuckDB will implicitly cast `'0' = 0`
     to true -- so a SQL-level equality test cannot see this defect at all. The
     comparator is type-strict for exactly this reason.

  4. **`where l.<uid> < r.<uid>`, strictly.** This is what makes the pair set
     canonical -- one row per unordered pair, no self-pairs. G9's cost is that
     two records sharing a `unique_id` therefore never pair with each other,
     which is why §2.0 tests uniqueness rather than assuming it. And the
     comparison is TYPE-DEPENDENT: `'507' < '64'` is true as text and false as
     integers, which moved 148 of 3,989 pairs before D.0 finding 82 was fixed.

  **`join_key_l` / `join_key_r`, not `unique_id_l` / `unique_id_r`.** These are
  Splink's own column names for this table (`__splink__blocked_id_pairs`), and
  matching them is what lets the parity assertion be a set comparison rather
  than a set comparison plus a renaming convention. Splink renames in Stage 4;
  so does this package.
-#}

{{ config(materialized='table', tags=['parity_stage_3']) }}

{%- set rules = var('er_blocking_rules') -%}
{%- set upstream = ref('er_stg_input') -%}
{%- set blocking_sql = dbt_er.er_blocking_sql(rules, upstream, upstream, 'unique_id') -%}

{#- The budget fires HERE, in the DAG, before the table is written.

    G14's finding is that "a Makefile target reports; it does not stop a build",
    so a `make capacity` target cannot be where this lives -- it has to fire
    where the pairs are about to be materialised. B1 is why it matters: Splink's
    ported `max_rows_limit = 1e9` is PER RULE, and at the measured 152 B/pair
    that limit would admit a 946 GB build.

    The projection is COUNTED, not estimated. `count(*)` over the blocking SQL
    does the joins but materialises no columns and writes no table, so it is
    cheap next to the build it is guarding and it is EXACT -- and an exact
    projection cannot be wrong in the direction that matters. A row-count
    heuristic (`n * (n-1) / 2`) would reject every correctly-blocked large
    corpus, which is the false positive that gets a budget check deleted.

    Nothing interrupts an over-large build partway: DuckDB 1.5.5 exposes no
    statement timeout (G13) and clustering OOMs rather than degrading (D4a). By
    the time a build is too big it is too late, which is why this is a hard stop
    before the write rather than a check on the result. -#}
{#- Skipped when dbt is compiling this model for a UNIT TEST, because there the
    upstream is an injected CTE and not a materialised relation -- `run_query`
    would look for `__dbt__cte__er_stg_input` in the catalog and fail. The probe
    needs something to count, and a unit test deliberately has nothing.

    Detected from the rendered ref rather than from a flag: dbt exposes no
    "am I a unit test" signal in the model context, and the CTE prefix is the
    thing that actually makes the probe impossible. Named explicitly so this
    reads as a stated limit rather than a mystery guard. -#}
{%- set unit_testing = '__dbt__cte__' in (upstream | string) -%}
{%- if execute and not unit_testing -%}
  {%- set projected = run_query(
        "select count(*) as n from (" ~ blocking_sql ~ ") as candidate_pairs") -%}
  {{ dbt_er.er_assert_pair_budget(
       projected.columns[0].values()[0], model_name='er_int_candidate_pairs') }}
{%- endif -%}

{{ blocking_sql }}
