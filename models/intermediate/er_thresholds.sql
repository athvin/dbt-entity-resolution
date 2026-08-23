{#-
  The threshold dimension (DesignDoc 1.7, DR-08 / DR-09).

  Every threshold at which edges and clusters are computed, as DATA rather than
  as a build-time var, so that all of them exist in one build and cross-threshold
  properties are expressible as ordinary tests. Stage 6's acceptance criteria
  require label parity at three thresholds simultaneously, and cross-threshold
  monotonicity is not expressible as a dbt test at all when the partitions never
  coexist.

  There is deliberately NO DEFAULT. [RECON], Appendix A M12: on the improved
  fixture model F1 peaks at 0.9809 at t=0.5 and falls to 0.9219 at t=0.9 -- the
  value section 8's own Definition of Done used as its example -- costing ~330
  true pairs for zero precision benefit, because precision is already 1.0000 at
  the lower threshold. A default nobody justified was silently choosing worse
  output on this project's own reference corpus. See 1.8.
-#}
{%- set thresholds = var('er_thresholds') -%}

{%- if thresholds | length == 0 -%}
  {{ exceptions.raise_compiler_error(
      "ER-010: er_thresholds is empty. A threshold is a precision/recall trade "
      ~ "against a cost function this package cannot see, so it has no default "
      ~ "and must be supplied. Set it to a list of mappings, e.g. "
      ~ "er_thresholds: [{auto_merge: 0.9}] or "
      ~ "[{auto_merge: 0.9, review_low: 0.7}]. See DesignDoc 1.7 and 1.8."
  ) }}
{%- endif -%}

{%- for pair in thresholds -%}
  {%- if 'auto_merge' not in pair -%}
    {{ exceptions.raise_compiler_error(
        "ER-011: er_thresholds[" ~ loop.index0 ~ "] has no 'auto_merge' key. "
        ~ "Every entry needs one; 'review_low' is optional and defaults to it, "
        ~ "which makes the gray band empty. See DesignDoc 1.7."
    ) }}
  {%- endif -%}
  {%- if pair.get('review_low', pair['auto_merge']) > pair['auto_merge'] -%}
    {{ exceptions.raise_compiler_error(
        "ER-012: er_thresholds[" ~ loop.index0 ~ "] has review_low ("
        ~ pair['review_low'] ~ ") above auto_merge (" ~ pair['auto_merge']
        ~ "). The gray band is the half-open interval "
        ~ "[review_low, auto_merge), so review_low <= auto_merge. "
        ~ "See DesignDoc 1.7."
    ) }}
  {%- endif -%}
{%- endfor -%}

{#-
  The DOUBLE casts are load-bearing, not stylistic. DuckDB types a bare decimal
  literal as DECIMAL; comparing a DECIMAL threshold against a DOUBLE
  match_probability shifts the boundary, and a boundary comparison is precisely
  what a threshold is. [RECON], Appendix A M16.
-#}
{% for pair in thresholds %}
select
    cast({{ pair['auto_merge'] }} as double) as thr_auto_merge,
    cast({{ pair.get('review_low', pair['auto_merge']) }} as double)
        as thr_review_low
{%- if not loop.last %}
union all
{% endif %}
{%- endfor %}
