{% macro er_hardening() %}
  {#-
    The hardening values (section 2.1, C.1 delta 10, RC48).

    These are NOT read from `var()`, and that is the whole point. Root
    configuration beats package configuration in dbt, so anything the compile
    gate reads from a var can be overridden by the consumer the gate exists to
    defend against. A consumer setting

        vars:
          er_allowed_materializations: ["view"]

    in their own dbt_project.yml would render every DDL constraint in this
    project inert -- dbt only WARNS when a materialization cannot enforce
    constraints -- while their build stayed green.

    C.1's deltas 1, 4 and 6 are written as edits to the `vars:` block. Applied
    that way and stopped there, they leave all three values consumer-reachable,
    which is the exact disarm 2.1 describes. Their final home is here.

    Changing anything in this macro is a change to the package, reviewed as
    code. That is the difference between a policy knob and a hardening value.
  -#}
  {%- do return({
    'allowed_materializations': ['table'],
    'must_carry_constraints': ['er_entity_clusters'],
    'standards_exempt': {},
    'allowed_tags': ['parity', 'parity_stage_2', 'parity_stage_3',
                     'parity_stage_4', 'parity_stage_5', 'parity_stage_6',
                     'parity_stage_7', 'parity_stage_9', 'parity_stage_10',
                     'measurement', 'diagnostic', 'v2'],
    'volatile_columns': ['er_run_id'],
    'run_contract_columns': ['er_run_id'],
    'pair_grain_models': ['er_int_comparison_vectors', 'er_int_scored_pairs'],
    'max_standards_exemptions': 0
  }) -%}
{% endmacro %}
