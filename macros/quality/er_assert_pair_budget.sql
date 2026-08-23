{#-
  Stage 0.6 / D11 rec 5 / G14 -- the pair-budget guard, in the DAG.

  G14's finding is the whole reason this is a macro and not a `make` target:

    "a Makefile target reports; it does not stop a build."

  So this raises at COMPILE time, from the model that is about to materialise a
  pair-grain relation, before Stage 3 writes a single row. A report that arrives
  after the OOM is a post-mortem.

  Why it can only be a hard stop, not a warning: G13 measured that DuckDB 1.5.5
  exposes NO STATEMENT TIMEOUT -- `duckdb_settings()` returns zero rows for
  `%timeout%` -- and D4a measured `USING KEY` clustering OOMing rather than
  degrading. There is no mechanism that interrupts an over-large build partway.
  `memory_limit` is the real backstop and it fails hard.

  The cap is DERIVED, never a constant. `er_max_pairs = 42,000,000` was carried
  from a 946 B/pair wide shape; at the measured narrow cost the same budget
  admits several times more, and at a smaller configured `memory_limit` it
  admits far fewer. A number that cannot be checked against the configuration it
  protects is not a guardrail.
-#}

{% macro er_assert_pair_budget(projected_pairs, model_name=none) %}
  {%- if not execute -%}
    {{ return('') }}
  {%- endif -%}

  {%- set cap = var('er_max_pairs') -%}
  {%- set bytes_per_pair = var('er_bytes_per_pair') -%}
  {%- set who = model_name or (this.identifier if this is defined else 'this model') -%}

  {%- if projected_pairs is none -%}
    {{ exceptions.raise_compiler_error(
         "ER-020: er_assert_pair_budget() was called with no projected pair count for "
         ~ who ~ ". A budget check that does not know the size it is checking is not a "
         ~ "check -- pass the blocking-rule cardinality estimate."
    ) }}
  {%- endif -%}

  {%- if projected_pairs > cap -%}
    {%- set projected_gb = (projected_pairs * bytes_per_pair / 1073741824.0) | round(1) -%}
    {%- set cap_gb = (cap * bytes_per_pair / 1073741824.0) | round(1) -%}
    {{ exceptions.raise_compiler_error(
         "ER-021: " ~ who ~ " projects " ~ projected_pairs ~ " pairs, over the "
         ~ cap ~ "-pair budget (er_max_pairs). At the measured "
         ~ bytes_per_pair ~ " B/pair that is ~" ~ projected_gb ~ " GiB against a ~"
         ~ cap_gb ~ " GiB allowance.\n\n"
         ~ "This is a HARD stop, not a warning, because nothing interrupts an "
         ~ "over-large build partway: DuckDB 1.5.5 has no statement timeout (G13) "
         ~ "and clustering OOMs rather than degrading (D4a).\n\n"
         ~ "Fix by tightening the blocking rules, not by raising the cap -- the cap "
         ~ "is derived from er_bytes_per_pair and the configured memory budget, and "
         ~ "raising it does not create memory."
    ) }}
  {%- endif -%}

  {{ return('') }}
{% endmacro %}
