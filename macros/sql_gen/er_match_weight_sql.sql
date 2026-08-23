{#-
  §3.1 / D6 / DR-06 -- match weight and probability. Linear space, then ONE log2.

  **[v1 ERROR]** v1 specified *"sum of per-comparison log2 Bayes factors"*.
  Splink does not do that. It multiplies Bayes factors in **linear space**,
  clamps, and applies `log2` exactly once (`predict.py:209-218, 113-120`). The
  prior is converted to odds and multiplied as the **first factor** -- never
  log2'd and added.

  The clamp is not cosmetic. §3.1 measured a naive log-space sum diverging by
  **9.97** on underflow and **56.47** on overflow, in match-weight units, where
  A.4's whole tolerance is `1e-9`.

  G5 measured the same effect at the other end of the scale: `product(bf)` and
  `exp(sum(ln(bf)))` differ by **3 ULP** on a seven-factor product even with no
  clamping in play. Linear space is not a stylistic preference.

  **The single-evaluation construct is a lateral column alias (B.8 / DR-23).**
  Splink's own projection repeats the clamped product **three times** -- once in
  the numerator and twice in `(p)/(1+(p))`. D11 rec 4 requires computing it once
  and deriving both outputs from it, and Stage 0.8 measured a lateral column
  alias evaluating **once per row** on DuckDB 1.5.5 -- so no subquery is emitted,
  `ST05` keeps its configured scope, and no pair-grain model is created to hold
  one float.

  Bit-identical to Splink's repeated form: PC-3 measured all three constructs
  producing the same bits, so this costs no parity.
-#}

{% macro er_match_weight_sql(prior_odds, bf_columns) %}
  {%- if not bf_columns -%}
    {{ exceptions.raise_compiler_error(
         "ER-051: er_match_weight_sql() was called with no bayes-factor columns. "
         ~ "The product would collapse to the prior alone, scoring every pair "
         ~ "identically -- which looks like a working model and is not one."
    ) }}
  {%- endif -%}
  {#- `join`, NOT `{% set %}` inside a `{% for %}`. Jinja's loop scoping means an
      assignment inside the loop does not escape it, so the accumulator form
      silently drops every factor and leaves the prior alone. The result is
      valid SQL that runs and scores every pair identically -- which is exactly
      the "looks like a working model and is not one" failure ER-051 describes,
      arriving through the macro instead of through the arguments. Caught by the
      bit-identity test against Splink, not by review. -#}
  {%- set product = "cast(" ~ prior_odds ~ " as float8) * " ~ bf_columns | join(" * ") -%}
    least(greatest({{ product }}, 1e-300), 1e300) as bf_clamped,
    log2(bf_clamped) as match_weight,
    case
        when {% for column in bf_columns -%}
{{ column }} = cast('infinity' as float8){% if not loop.last %} or {% endif %}
{%- endfor %} then 1.0
        else bf_clamped / (1 + bf_clamped)
    end as match_probability
{% endmacro %}
