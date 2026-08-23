# dbt-er — Engineering Standards

**Status:** v2.2 · **Date:** 2026-08-23 · **Companion to:** `docs/DesignDoc.md` (Draft v2 + Appendices A–B)
**Target runtime:** dbt-core 1.12.2 · dbt-duckdb 1.11.0 · DuckDB 1.5.5 · Python 3.12

> **v2 changes.** A gap review against this document and `DesignDoc.md` produced 33 findings, all merged
> here. The material ones: the materialization contract is replaced by `DesignDoc` **D11** (§7), the
> precedence rule that let it drift is rewritten (§1), the compile gate's own knobs are hardened against the
> consumer it defends against (§2.1), and twenty-nine new standards are appended at 3.38–3.66. Six sections
> are new: §19 release and compatibility, §20 fixtures and test data, §21 when a gate fails, §22 the platform
> contract, §23 amending this document, §24 licensing. **Everything added in v2 is `[UNVERIFIED]`** — it is
> designed, not executed. The v1 scaffold that produced the `[VERIFIED]` markers was not rebuilt. Appendix E
> records the merge.
>
> **v2.1 changes.** Adds **§7.3 — the CTE rule** (3.67, 3.68): a model body may not contain a non-recursive
> common table expression; `WITH RECURSIVE` is permitted, because recursion is a control structure with no
> model-shaped equivalent while a plain CTE is a hidden stage. Writing it surfaced a collision that predates
> it — §11.1's ST05 and `DesignDoc` D11 rec 4 are already mutually unsatisfiable *except* via a CTE — now
> registered as **Appendix B.8**. Also `[UNVERIFIED]`.
>
> **v2.2 changes.** `DesignDoc` **D12** raises unit-test coverage from a subset to **every model**, and
> makes each model's test cases something decided when the model is written. §12.2 is rewritten as the
> unit-test standard against
> [docs.getdbt.com/docs/build/unit-tests](https://docs.getdbt.com/docs/build/unit-tests?version=2); **3.20**
> loses its "fixed-schema" scope; **3.69–3.71** are new (fixture format, the case-class convention, and the
> rule that a consumer's build never runs our unit tests). Two places that encoded the old exclusion are
> corrected — §18.1's worked waiver example and Appendix A.1's closing paragraph — and Appendix C.4 gains
> the delta table RC49 asked for, with the exemption branch as its first row. `[UNVERIFIED]`, per §0.

---

## 0. How to read this

`DesignDoc.md` specifies **what the SQL computes**. This document specifies **how the repository stays
correct while it changes**. They are different problems and they fail differently: the design fails loudly,
in a parity report; the engineering practice fails silently, in a green build that is no longer checking
what you think it checks.

A third file, [`GOAL.md`](../GOAL.md) at the repository root, states **where the project is going** —
Splink's data transformations as declarative pure-SQL dbt models, with recursive CTEs carrying the
iterative surfaces, proven by differential testing against Splink. It is the shortest of the three and the
only one that is **non-normative**: it settles nothing, and it is the one that gets edited when it
disagrees with either of the others. §1.1 ranks it explicitly.

Three conventions govern every rule below.

**1. Every rule names its mechanism and its gate.** A standard nobody can violate accidentally is a
standard. A standard that lives only in a document is a preference. Where no mechanism exists, the rule is
labelled **convention (unenforced)** rather than dressed up as policy — §3 has exactly three of those and
says so.

**2. Every configuration block carries a provenance marker.**

| Marker | Meaning |
|---|---|
| **`[VERIFIED]`** | This exact configuration was executed in this repository and observed to work. Evidence is cited inline. |
| **`[UNVERIFIED]`** | Designed but never executed. Treat as a first draft to validate on first run, not as a proven artifact. |

**A marker is scoped to a toolchain, and expires with it.** Every `[VERIFIED]` in this document means
*observed to work on* dbt-core 1.12.2 · dbt-duckdb 1.11.0 · DuckDB 1.5.5 · dbt-bouncer 3.8.0 ·
SQLFluff 4.3.0 · yamllint 1.38.0 — the pins in §4. That scope was implicit in v1, which is a defect: when a
pin moves, a marker silently becomes a claim about a version nobody is running, and a reader correctly
interpreting "this exact configuration was executed" is misled. **Any pin change demotes every marker scoped
to it to `[UNVERIFIED]` until re-executed** (3.44, §16). Markers that were verified against something other
than the §4 pins carry their own scope inline.

`[VERIFIED]` also means *observed to pass*, never *observed to fail when violated*. Those are different
claims and only the second proves a gate is enforcing anything; 3.38 and §15's `verify-gates` job exist to
supply it.

This distinction is the most valuable thing in the document and must not be smoothed away in editing. A
scaffold was built from these configs and run: `dbt build` reached **83/83 green**, `dbt-bouncer` **82/82
green**, SQLFluff / yamllint / ruff clean. That exercise **disproved six claims** that careful research had
stated with confidence (§A.1). Anything not exercised is marked, because the difference between "this
works" and "this should work" is precisely the difference this project cannot afford to blur.

**3. Where this document and `DesignDoc.md` disagree, §1's precedence rule decides — and it is not the
one-line rule v1 used.** v1 said simply "Appendix A wins," which is true of *measurements* and wrong about
*decisions*, and the difference cost this document its materialization contract for a whole revision (§7,
Appendix E). A measurement is never overturned by argument; a numbered design decision that reinterprets a
measurement and reaches a different conclusion does win. §1 states the full order.

---

## 1. Scope and precedence

| Question | Owned by |
|---|---|
| What does `er_int_scored_pairs` compute? | `DesignDoc.md` §2–§5 |
| What tolerance is a parity failure? | `DesignDoc.md` **A.4** — implement from A.4, not §6.1. See note below |
| Must every model have a primary key, and what happens if it doesn't? | **This document** §8 |
| Which materialization is a model allowed to use? | **This document** §7, per DesignDoc **D11** (B1 superseded; its measurement stands under it as tier-1 data) |
| Which models need unit tests, and what must those tests cover? | **This document** §12.2 and 3.20, per DesignDoc **D12** (M17 rec (c)'s five-model scope superseded; its `--empty` precondition stands under it as tier-1 evidence) |
| Where do performance metrics go? | **This document** §14, satisfying DesignDoc M7 |
| Why does this project exist, and what does "done" look like end to end? | **`GOAL.md`** (repository root) — non-normative. It states the destination and owns no rule, no tolerance and no decision |

> **On the tolerance row `[UNVERIFIED]`.** v1 routed this to "§6.1 / A.4" as though they were one source.
> They are not: A.4 is a strict superset, and §6.1 omits three things — the **relative** term
> (`1e-9 + 1e-12·|mw|`, not a bare `1e-9`), the rule that probability parity is **vacuous above `mw = 54`**
> and must be asserted as exact `p == 1.0` there, and the row stating that **float aggregates are not a gate
> at all** (which §13.2 independently reaches from the other direction). Someone implementing from §6.1
> writes a materially weaker harness. Implement from A.4 until the two are merged into one table with one
> home. *(`DesignDoc` Appendix B, R2.)*

> **[REVIEW 2026-08-23] Fixed (F1):** the materialization row above previously read "bounded by DesignDoc
> B1". B1 is the Appendix A recommendation D11 overrode, and §1.1's tier 2 exists precisely so D11 outranks
> it — citing B1 as the bound re-committed the v1 error §1.1 narrates three paragraphs down. Now routed to
> D11, with B1 retained as the underlying measurement.

### 1.1 Precedence

**Highest first:**

1. **Measured `[RUN]` / `[RECON]` findings in `DesignDoc.md` Appendix A — as data.** A measurement is
   overturned only by a better measurement, never by an argument. **`DesignDoc.md` Appendix B's G-findings
   and R-findings rank here too**, as Appendix-A-class evidence: they are overturned by argument, and are
   adopted only via a D-number or a DR row.
2. **`DesignDoc.md` numbered architectural decisions (D1–D12, and successors), and the `§B.3` decision
   register.** A D-number may *reinterpret* an Appendix A measurement and reach a different conclusion;
   when it does, it wins. The **register ranks with them**, because a DR row records the current value of a
   D-number-class decision and is where supersession is looked up. Register and D-number are two views of
   one decision: the row owns its *status*, the body owns its *content*, and a change to either touches
   both in the same commit.
3. **This document.**
4. **Habit.**

**Tier 2 did not exist in v1, and its absence was not cosmetic.** `DesignDoc` Appendix A **B1** recommended
`ephemeral` intermediates; §7 implemented that recommendation faithfully; `DesignDoc` **D11** then superseded
B1 from the document body, using its own later measurements to separate pair-materialization cost from
`_l`/`_r` passthrough cost. Under v1's rule a reader checking precedence would have concluded — correctly,
per the rule as written — that B1 outranked D11 and the ephemeral policy stood. The rule designed to prevent
drift certified it. §7 is now D11's.

**A superseding decision must name what it invalidates.** Any `D<n>` that overrides an Appendix A
recommendation carries an explicit `Supersedes:` line naming both the finding and the sections of *this*
document it invalidates. D11 named `dbt_project.yml`; it did not name §7, §3.7, §3.11 or §8.3, which is how
four sections stayed stale while the decision that replaced them was three pages away. Enforced by 3.45.

**`GOAL.md` is not in this order, and that is the point.** It states the destination — declarative
pure-SQL dbt models with recursive CTEs for the three iterative surfaces, differential-tested against
Splink — and owns no decision, no tolerance, no standard and no stage. A sentence in it never settles a
question raised here or in `DesignDoc.md`; if it conflicts with either, **`GOAL.md` is wrong and gets
edited**. It is named in this list rather than omitted from it, because RC32 immediately below is what
happens to a document that binds in practice and ranks nowhere in writing. A pointer *to* `GOAL.md` is
orientation; a citation *of* `GOAL.md` as grounds for a technical choice is a defect — the grounds are the
D-number or the DR row it points at.

> **[REVIEW 2026-08-23] Fixed (F17) — RC32 is closed by the two additions above.** `DesignDoc` Appendix B
> ranked in no tier: not tier 1, since its B.0.1 states the pass *"introduces no `[RUN]` and no `[RECON]`
> evidence"*, and not tier 2 — yet this document already treated it as binding (E.1 defers to *"B.3 marks
> all four **MISSING**"*, §12.7 exists to answer a G-class finding, and `DesignDoc`'s v2 note says to treat
> B.3's `CONFLICT`/`MISSING` rows as blocking). De facto it bound; de jure a reader applying this list
> ranked it below habit. Tier 2's absence *"was not cosmetic"* last time, and this was the same hole one
> appendix later. The companion carries the mirror half at its header precedence rule, which now carves
> §B.3 out by name.

---

## 2. The four gates

No single gate catches everything, and each has a specific blind spot. The stack exists because of the
blind spots, not despite them.

| Gate | Runs | Sees | Cannot see |
|---|---|---|---|
| **Compile** — Jinja macro raising `raise_compiler_error` from `on-run-start` | `dbt parse` / `run` / `build`, **and inside every consumer's project** | The manifest: configs, constraints, descriptions, unit-test coverage | Anything requiring the database or the compiled SQL text |
| **Pre-commit** — local scripts, SQLFluff, yamllint, ruff | Every commit | Raw files before dbt ever sees them: 1:1 pairing, banned Jinja, SQL style | Anything requiring a manifest or a build |
| **Build** — contracts, DDL constraints, dbt tests | `dbt build` | Physical reality: column names, types, keys, nullability, data invariants | Anything about files, naming or documentation *text* |
| **CI** — dbt-bouncer, dbt_project_evaluator, the full build | Every PR | Artifacts across the whole project: coverage percentages, DAG shape, timings | Nothing the other three see — but it is the *last* place to learn it |

The one that surprises people is **compile**. dbt-bouncer reads artifacts from *this* repository, so it
protects this repository only. A consumer installing `dbt_er` can override our model configs from their own
`dbt_project.yml` — root configuration beats package configuration — and set `+materialized: view`, which
silently renders every DDL constraint inert while their build stays green. The `on-run-start` macro is the
only gate that travels with the package.

### 2.1 The compile gate's own knobs, in both directions `[UNVERIFIED]`

The argument above has a consequence v1 did not follow through: **root configuration beats package
configuration for the gate's own vars too.** Every threshold `er_assert_project_standards` enforces is read
via `var()`, so the same precedence that motivates the gate also disarms it. A consumer who sets
`er_allowed_materializations: ["table", "view"]`, or `er_min_model_description_chars: 0`, or lists our models
in the exemption var, silences the gate built to survive them — in one file, with no error. The fastest way
for a consumer to stop the `+materialized: view` warning is to make the gate stop asking about it.

The mirror problem is as real. The hook fires in **every** consumer's project, walks the whole graph on
every `parse` / `run` / `build`, and `raise_compiler_error`s on violations *in our package that the consumer
cannot fix* — with `skip_nodes_if_on_run_start_fails: true` (Appendix C.1) their nodes then skip. §14.8
states the principle and reaches the opposite conclusion for hooks: *"shipping hooks that fire in someone
else's project without an off switch is a hostile default."* That reasoning applies with more force here,
because this hook can hard-fail rather than merely write.

Both must be resolved together — fixing either alone makes the other worse:

| | Rule |
|---|---|
| **Policy vars** | Thresholds a consumer may legitimately tune — `er_min_*_chars`, description strictness. Read from `var()`. Overridable by design. |
| **Hardening vars** | The invariants that make our output trustworthy at all — `er_allowed_materializations`, `er_must_carry_constraints`, the exemption list. **Not** read from `var()`; defined in a package-owned macro that root config cannot reach. |
| **Escape hatch** | `er_standards_enabled`, default **true**, documented. A consumer may turn the gate off. |
| **Fail-soft abroad** | Outside our own package the macro warns rather than raises on an *internal* error (as opposed to a policy violation). A bug in our gate must never fail a consumer's build. |

The escape hatch and the hardening are not in tension, because they act on different things: turning the
gate off stops *reporting*, not the physical guarantees. A consumer who disables it still gets contracts,
constraints and keys — those are enforced by DuckDB, not by the macro. The gate is advisory to consumers and
mandatory to us, because in our CI a raise is the correct outcome and 3.51 asserts `er_standards_enabled` is
true there.

**Threat model, stated plainly:** the compile gate defends against *accidental* consumer damage. It does not
defend against a determined consumer, who can fork the package — and that is their prerogative. The gap v1
left is that today the accidental case and the determined case take the same single line.

---

## 3. The enforcement matrix

The normative table. **C** = compile · **P** = pre-commit · **B** = build · **CI** = continuous integration.

| # | Standard | Mechanism | Gate | On violation |
|---|---|---|---|---|
| 3.1 | Every `.sql` has a sibling same-basename `.yml`; every `.csv` seed likewise. Covers **both** project roots | `scripts/check_yml_pairing.py` (primary) + `check_one_yml_per_sql` custom bouncer check + the policy macro's `patch_path` check | P + CI + C | Non-zero exit naming the missing path |
| 3.2 | No orphan `.yml`; non-resource YAML starts with `_` | Same script | P + CI | Non-zero exit |
| 3.3 | Every column declared and typed | **`contract: {enforced: true}`** — dbt raises on an empty column list, and compares name + type + order against the built relation | B | Build fails |
| 3.4 | Every column *described*, with real text | `check_column_description_populated` (catalog) + `check_columns_are_all_documented` + yamllint `empty-values` | CI + P | Bouncer/yamllint non-zero |
| 3.5 | Model descriptions follow the six-section template | `er_assert_project_standards` string match | C | `raise_compiler_error` |
| 3.6 | Every table declares a primary key | `check_model_has_constraints` + `check_model_single_primary_key` + policy macro | CI + C | Bouncer/compile failure |
| 3.7 | Primary keys are **enforced** at the right cost | **Grain split** (§8.3): entity-grain → DDL `primary_key`; pair-grain → `dbt_utils.unique_combination_of_columns` + recorded waiver | B | Constraint error or test failure |
| 3.8 | `not_null` wherever it applies | Column `constraints: [{type: not_null}]` | B | Build fails — DuckDB enforces it |
| 3.9 | Parity invariants encoded as CHECK constraints | Model-level `constraints: [{type: check, …}]` | B | Build fails, naming the constraint |
| 3.10 | `foreign_key` is banned | Policy macro scans `node.constraints` | C | `raise_compiler_error` |
| 3.11 | Materialization policy: `table` for every stage model (§7, per DesignDoc D11) | `check_model_materialization_permitted` + policy macro. No `exclude:` scope — under D11 there is nothing to exclude | CI + C | Failure naming the model |
| 3.12 | Every stage model carries physical constraint enforcement | `er_must_carry_constraints` check in the policy macro. Renamed from v1's `er_must_be_table`: the property wanted is *enforcement*, and `table` is one way to get it, not the requirement (§7.2) | C | `raise_compiler_error`; never waivable |
| 3.13 | Constraints must not silently die | 3.11 + 3.12 together | C + CI | See note below |
| 3.14 | SQL style | SQLFluff 4.3.0, `dialect=duckdb`, `templater=dbt`, `rules = all` | P + CI | Non-zero exit |
| 3.15 | Determinism rule set | SQLFluff AM03/AM04/AM08/AM09, RF03=`qualified`, CV09 blocked words, CV11=`cast`, ST05, ST07 | P + CI | Lint failure |
| 3.16 | No non-deterministic Jinja in model code | `scripts/check_no_nondeterminism.py` | P + CI | Non-zero exit |
| 3.17 | The lint exemption list cannot grow | CI asserts `.sqlfluffignore` holds ≤ 2 model entries | CI | Job fails |
| 3.18 | YAML hygiene | `yamllint --strict` (`empty-values`, `key-duplicates`) | P + CI | Non-zero exit |
| 3.19 | 100% test coverage; every model has a uniqueness test | `check_model_test_coverage`, `check_model_has_unique_test`, `check_model_has_tests_by_type` | CI | Bouncer fails |
| 3.20 | **Every model has at least one unit test.** No scope, no automatic exemption class — an exclusion is a dated 3.43 waiver or it does not exist (§12.2, per DesignDoc **D12**) | `check_model_has_unit_tests` (**unscoped**) + policy macro walking `graph.unit_tests` | CI + C | Failure naming the model |
| 3.21 | Every warning is an error | `flags.warn_error_options: {error: all}` | B | Build fails |
| 3.22 | Both projects share one strictness policy | `scripts/check_flags_parity.py` | P + CI | Non-zero exit |
| 3.23 | JSON-derived columns are contracted | `columns: "{{ var('er_gamma_columns') }}"` (native Jinja on a YAML leaf) | B | Contract error |
| 3.24 | No statement-level `ORDER BY` in a model body | `check_model_code_does_not_contain_regexp_pattern` | CI | Bouncer fails |
| 3.25 | Output is content-stable across runs | pytest content hash over sorted rows, volatile columns excluded | CI | Test fails |
| 3.26 | Model performance is recorded in a table | `on-run-end` macro → `er_meta.model_execution_log` | B | n/a (records) |
| 3.27 | Engine-level metrics are recorded | `enable_profiling` + `enable_logging` drained to `er_meta.query_metrics_log` | B | n/a (records) |
| 3.28 | Performance regressions fail the build | `check_run_results_max_execution_time` per path prefix | CI | Bouncer fails |
| 3.29 | The shipped dependency surface stays minimal | `scripts/check_root_packages_minimal.py` | P + CI | Non-zero exit |
| 3.30 | Consumers cannot `ref()` internals | `restrict-access: true` + `+access: private` + `+group:` | B | `ref()` fails |
| 3.31 | Parity jobs never use state comparison | CI asserts no `--defer` / `state:` in those commands | CI | Job fails |
| 3.32 | Model-JSON identity is visible in the data | `er_model_sha` + `er_tf_snapshot_id` columns, with `count(distinct …) = 1` tests | B | Test fails |
| 3.33 | Naming conventions — models, macros, **vars, groups, tags, singular tests, and the generated column families** | `check_model_names` per directory + `check_macro_name_matches_file_name` + policy-macro identifier check (§10.5) | CI + C | Bouncer/compile failure |
| 3.34 | Python quality | ruff `select = ["ALL"]` + `ruff format` + `mypy --strict` | P + CI | Non-zero exit |
| 3.35 | Description *content quality* | — | — | **Convention (unenforced).** 3.5 proves the six headings exist, not that what follows them is true |
| 3.36 | A Splink source permalink in every parity PR | PR template checkbox | — | **Convention (unenforced)** — a human gate |
| 3.37 | Comment accuracy over time | — | — | **Convention (unenforced).** Nothing checks that a comment still describes the code beneath it |
| | **— v2 additions. All `[UNVERIFIED]`. Numbers 3.1–3.37 are stable ids and are never reused (§23). —** | | | |
| 3.38 | Every standard in this matrix is observed to **fail** when violated | `scripts/verify_gates.py`: inject each violation in a scratch copy, assert non-zero exit **and** the expected error string | CI | Job fails naming the standard that did not fire |
| 3.39 | Every mechanism named in this matrix exists and is wired up | `scripts/check_standards_matrix.py` parses §3 and cross-references `.pre-commit-config.yaml`, `dbt-bouncer.yml`, `.sqlfluff` | P + CI | Non-zero exit naming the orphaned rule |
| 3.40 | Custom bouncer checks are registered **and the suite actually ran** | Assert expected check names, a **minimum SUCCESS count**, and **zero WARNs** in the bouncer result; loader *and execution* warnings are treated as errors. Extended 2026-08-23 by D.0 finding 20: `SUCCESS=0 WARN=2 ERROR=0` **exits 0**, so a registration check alone passes a config that matches nothing | CI | Job fails |
| 3.41 | No CI job may select zero nodes | Minimum-node-count assertion beside every `--select` in the workflow | CI | Job fails |
| 3.42 | The tag vocabulary is governed; every declared tag matches ≥ 1 node | Policy macro rejects tags outside `er_allowed_tags`; CI asserts none is unused | C + CI | `raise_compiler_error` / job fails |
| 3.43 | Waivers are per-check, reasoned, capped, and printed on every run | `er_standards_exempt` is a mapping of model → \[check, …\] with a reason; CI asserts the cap; the macro echoes the active list in its success message | C + CI | Compile failure / cap assertion fails |
| 3.44 | `[VERIFIED]` markers match the installed toolchain | `scripts/check_verified_markers.py` compares §4's pins against `uv.lock` and the document's verified-against block | P + CI | Non-zero exit naming the demoted markers |
| 3.45 | A superseding `DesignDoc` decision names the sections it invalidates | Reference check: every `D<n>` marked superseding carries a `Supersedes:` line resolving to real sections | CI | Job fails |
| 3.46 | The parity comparator is proved capable of failing | `harness/test_comparator_sensitivity.py` over `harness/mutants` — §12.7's catalogue applied to a known-good output; **no mutant may survive**, and each asserts its expected localisation string | CI | Job fails naming the surviving mutant |
| 3.47 | Fixture coverage: every gamma cell and every `match_key` is exercised | Data test over the built comparison vectors, ≥ `er_min_gamma_cell_observations` each; matrix published as an artefact | B + CI | Test fails; artefact shows the empty cells |
| 3.48 | Run identity is in the data, and excluded from every hash | `er_run_id` on every materialised model + `_er_run_manifest` (§14.9); policy macro asserts every run-contract column also appears in `er_volatile_columns` | B + C | Build/compile failure |
| 3.49 | Every deliberate divergence has both a log entry and a pinning test | `scripts/check_divergence_log.py`, both directions | CI | Non-zero exit |
| 3.50 | `PARITY.md` names every stage the DAG contains | Same script, comparing stage tags in the manifest against sections | CI | Non-zero exit |
| 3.51 | The compile gate cannot be disarmed from root config, and cannot brick a consumer | Hardening values live in a package macro, not `vars:` (§2.1); CI asserts `er_standards_enabled` is true | C + CI | Compile failure / job fails |
| 3.52 | The package creates no relations a consumer did not ask for | CI asserts the package's `data_tests:` block sets no `store_failures_as`, and that the package declares **no `on-run-end` hooks** — `on-run-start: er_assert_project_standards` is required by §2 and is explicitly permitted | CI | Job fails |
| 3.53 | Column budget: no `_l`/`_r` passthrough unless the debug var is set | Policy macro checks declared columns on the two pair-grain models against `er_retain_matching_columns` | C | `raise_compiler_error` |
| 3.54 | No build artefact is ever committed | `.gitignore` + a pre-commit hook rejecting staged `target/`, `dbt_packages/`, `*.duckdb` — `.gitignore` alone loses to `git add -f` | P | Hook fails |
| 3.55 | Fixtures and seeds are synthetic; no secrets, no real person data | `detect-private-key` + `scripts/check_pii_heuristics.py` over `seeds/`, `fixtures/`, `harness/`: every data file must declare `synthetic: true` in its manifest, and structured identifiers (Luhn-valid PANs, NI numbers, SSNs, IBANs) and consumer email providers are rejected | P + CI | Non-zero exit naming the file and the indicator |
| 3.56 | CI actions are SHA-pinned, least-privilege, and do not persist credentials | `scripts/check_workflow_hardening.py`: asserts every `uses:` carries a 40-char SHA, every job declares `permissions:`, every checkout sets `persist-credentials: false`, and **no workflow uses `pull_request_target`** — §15 states that position and C.7's `GITHUB_ENV` heredoc is why | P + CI | Non-zero exit naming the workflow and job |
| 3.57 | The enforcement scripts are themselves tested, positively and negatively | pytest over `scripts/` and `dbt_bouncer_checks/` with a coverage floor; every script ships a failing-case test | P + CI | Non-zero exit |
| 3.58 | Environment determinism is pinned, not assumed | `PYTHONHASHSEED=0`, `TZ=UTC`, `LC_ALL=C` set in `Makefile` and workflow env, and asserted in the determinism job | CI | Job fails |
| 3.59 | Float-exact gates run only on the normative platform | `harness/test_float_parity.py` pins the bit patterns and runs on BOTH platforms, so a cross-platform divergence fails at the probe (G5, closed 2026-08-23); the determinism job additionally asserts `linux/amd64` (§22) | CI | Job fails naming the divergent value |
| 3.60 | A MAJOR-triggering change cannot ship without a MAJOR bump | Contract diff against the previous release tag (§19.2) | CI | Job fails naming the breaking change |
| 3.61 | The public API surface is enumerated and matches reality | CI asserts every `+access: public` model appears in §19.1's list, and vice versa | CI | Job fails |
| 3.62 | Every baseline carries a provenance manifest | `scripts/check_baseline_manifests.py`: Splink version, model-JSON sha, seed, DuckDB version, producing commit | P + CI | Non-zero exit |
| 3.63 | Quarantine entries expire | CI fails on any entry in `docs/quarantine.md` past its stated date (§21) | CI | Job fails |
| 3.64 | The published install path builds | Consumer smoke-test job installing the package by git ref into a third project (§19.3) | CI | Job fails |
| 3.65 | Rule lifecycle — a removed rule states why its failure is no longer possible | — | — | **Convention (unenforced)** (§23) |
| 3.66 | Licensing and attribution | — | — | **Convention (unenforced)** (§24) |
| 3.67 | **No non-recursive CTE in a model body.** `WITH RECURSIVE` permitted | `check_model_code_does_not_contain_regexp_pattern` over `raw_code`, comments and string literals stripped first (§7.3.4) | CI | Bouncer fails naming the model |
| 3.68 | A `WITH RECURSIVE` clause contains only its recursive term(s) | — — needs a parser, and neither SQLFluff 4.3.0 nor (unverified) sqlglot handles `USING KEY` (§7.3.4) | — | **Convention (unenforced)** until a parser exists. Without it, `WITH RECURSIVE` is a one-word waiver for 3.67 |
| | **— v2.2 additions (DesignDoc D12). All `[UNVERIFIED]`. —** | | | |
| 3.69 | Every unit-test fixture is `format: sql` with an explicit cast on **every** column (§12.2) | `scripts/check_unit_test_fixtures.py` over every `unit_tests:` block in both project roots: rejects `format: dict`, a missing `format`, and any `select` list column without a `cast(… as …)` | P + CI | Non-zero exit naming the fixture and column |
| 3.70 | A model's unit tests cover the case classes D12 enumerates, and each unanswered question is recorded rather than left blank | — | — | **Convention (unenforced).** A gate can count tests and can require the recorded answer; it cannot know that a `CASE` arm has no case. §12.2 states the checklist and §17 puts it in review |
| 3.71 | A consumer's build never executes this package's unit tests | `consumer_smoke/` job (§19.3, 3.64) asserts zero `unit_test` rows in its `run_results.json`; if dbt does execute them, the package ships the documented `--exclude-resource-type unit_test` guard and the job asserts that instead | CI | Job fails |

| | **— v2.6 addition (Stage 1 / §1.5 / DR-17). `[VERIFIED]` against the §4 pins. —** | | | |
| 3.75 | The model JSON passes the §1.5 trust boundary before anything builds | `scripts/er_sidecar.py` validates against the **parsed sqlglot tree**: D6's closed allow-list, non-deterministic functions rejected listed or not, structural rejection, input bounds, and `er_model_sha` over the validated artefact | P + CI | Non-zero exit naming the level and the function |
| 3.82 | Term frequencies sum to exactly 1.0 per column (§3.5's non-null denominator) | `tests_python/test_term_frequency_sql.py`, executed against the real fixture | CI | Test fails naming the column and the shortfall |
| 3.81 | The JSON-derived column lists, the rendered SQL and the unit-test fixtures agree (RC57, M2, D12) | `tests_python/test_column_drift_guard.py` -- three artefacts checked against one model JSON, including against Splink's own product order | CI | Test fails naming the drifted artefact |
| 3.80 | The gamma CASE and TF adjustment are bit-identical to Splink's | `tests_python/test_gamma_and_tf_sql.py` executes both against Splink's captured SQL over 1,507 rows covering every NULL combination | CI | Test fails naming the divergent row |
| 3.79 | Scoring arithmetic is bit-identical to Splink's, executed rather than compared as text | `tests_python/test_match_weight_sql.py` runs both forms over 2,005 rows including the clamp boundaries and an infinity | CI | Test fails naming the first divergent row |
| 3.78 | Generated SQL matches Splink's own, not a transcription of the prose | `tests_python/test_blocking_sql.py` renders the macro and compares against `fixtures/snapshots/*.sql`, captured from Splink's generator | CI | Test fails showing the first divergent token |
| 3.77 | Stage-1 model-JSON lints: asymmetric levels reported (M1), `output_column_name` unique after `.replace(" ", "_")` (M2), a **present** `m`/`u` of 0 is a hard error while an **absent** one is valid input (M13) | `scripts/er_sidecar.py` -- the same pass that validates and resolves | P + CI | Non-zero exit naming the comparison and the level |
| 3.76 | The A.2 sidecar regenerates byte-identically from its model JSON | `scripts/er_sidecar.py --check` compares the committed artefact against a fresh resolution; resolution is **Splink's own**, never a reimplementation | P + CI | Non-zero exit naming the drifted file |

> **On 3.75 — matching the tree, and matching EVERY alias.** §1.5 requires validation against the parsed
> tree because *"validating the raw string instead of the parsed tree is what makes an allow-list
> bypassable"*. Running it revealed a second, sharper version of the same problem: **the lists are written
> in DuckDB's vocabulary and the tree speaks sqlglot's.** `EPOCH(...)` parses to `time_to_unix`,
> `jaro_winkler_similarity` to `jarowinkler_similarity`, `random()` to `Rand`, `version()` to
> `CURRENT_VERSION`. On the allow-list a mismatch is a noisy false **reject**; on the non-determinism list
> it is a silent false **accept** — `random()` passed validation. The check now tests **every**
> `sql_names()` alias, which is robust to canonicalisation in both directions and needs no hand-kept map.

| | **— v2.5 addition (Stage 0.6 / D11 rec 5 / G14). `[VERIFIED]` against the §4 pins. —** | | | |
| 3.74 | A pair-grain model asserts its projected size against the derived budget **before materialising** | `er_assert_pair_budget()` raises at compile time from the model itself; `er_max_pairs` is derived from `er_bytes_per_pair` and the configured memory budget, never a constant | C | `raise_compiler_error` naming the projection, the cap and both in GiB |

> **On 3.74 — why it is a compile gate and not a `make` target.** G14's finding, verbatim: *"a Makefile
> target reports; it does not stop a build."* And it can only be a hard stop, because **nothing interrupts
> an over-large build partway** — G13 measured that DuckDB 1.5.5 exposes no statement timeout, and D4a
> measured `USING KEY` clustering OOMing rather than degrading. `memory_limit` is the real backstop and it
> fails hard. The cap is **derived**, so raising it to make a build pass is visibly the wrong move: it does
> not create memory, and the message says so.

| | **— v2.3 addition (Appendix D.0 finding 4). `[VERIFIED]` against the §4 pins. —** | | | |
| 3.72 | A `unit_tests:` block is at the **top level** of its properties file, never nested under a `models:` entry | `scripts/check_unit_test_fixtures.py` rejects a `unit_tests` key inside a `models:` entry, in both project roots; the policy macro's 3.20 check catches the consequence | P + CI + C | Non-zero exit naming the file and the model |

| | **— v2.4 addition (§23's canonical-home rule, mechanised). `[VERIFIED]` against the §4 pins. —** | | | |
| 3.73 | A configuration artifact has exactly one canonical home: no document holds a second copy of a file that exists in the repository | `scripts/check_canonical_homes.py` — a heading naming a live path must not carry a fenced block; short illustrations use a counted `<!-- excerpt: -->` marker | P + CI | Non-zero exit naming the section and the live path |

> **On 3.73 — the rule §23 stated and nothing enforced.** §23 says the canonical-home rule "is what makes
> 3.39 mean something after the scaffold lands", and then §23's own opening diagnoses why a rule with no
> mechanism decays: *"mechanisms nothing verified still existed ... is how §7 stayed stale for a revision"*.
> The rule was prose. Reducing Appendix C once is a cleanup; the duplication regrows silently without
> something watching, and a reader cannot tell a stale copy from a live file.
>
> Its first run found **eight** duplicated artifacts totalling **735 lines** — including §11.1's
> `.sqlfluff` and §11.2's `.sqlfluffignore`, which RC40 had flagged as living outside Appendix C and which
> a manual sweep of Appendix C would have missed. That is the argument for the mechanism in one number.

> **On 3.72 — why the consequence is not enough.** D.0 finding 4 measured it: a nested `unit_tests:` block
> is **silently ignored** by dbt-core 1.12.2. Clean parse, exit 0, no warning. The compile gate does fail —
> but it fails with *"no unit test"*, pointing at a model whose properties file visibly contains three of
> them, which is the least actionable true statement the gate could make. 3.72's own mechanism names the
> nesting directly. *(This row closes the "not yet added to §3" note in D.0 finding 4: the injection landed
> in step 4 as promised, and `check_standards_matrix.py` now fails on an injection whose standard has no
> row — which is how the gap was found.)*

> **On 3.13 — why 3.11 and 3.12 are not redundant.** dbt's
> `materialization_enforces_constraints` returns true only for `table` and `incremental`. On any other
> materialization, constraints are dropped with a **warning, not an error**. A single stray
> `+materialized: view` therefore removes every physical guarantee in the project while the build stays
> green. Under `warn_error_options: {error: all}` that warning does become an error — which is exactly why
> 3.21 is load-bearing rather than cosmetic.

> **[REVIEW 2026-08-23] Fixed (F18) — RC33's canonical-home rule is now stated in §23**, and Appendix C's
> header carries its handover half. ~~**3.39 remains inert until the scaffold lands**, because its three
> subject files do not yet exist~~ — **superseded 2026-08-23**: the three subject files exist and
> `scripts/check_standards_matrix.py` is live, parsing 71 rows and resolving 130 citations. Its first run
> found a real orphan: 3.19 names `check_model_has_tests_by_type`, a check dbt-bouncer genuinely ships and
> the reconstructed C.5 never configured. Four rows whose mechanism is a CI job that does not exist yet
> (3.25, 3.46, 3.60, 3.64) are itemised in `scripts/pending_subjects.yml` rather than passing silently.

> **[REVIEW 2026-08-23] Fixed (F19) — RC34: 3.52 is rescoped to `on-run-end` hooks.** As written it read
> *"sets no `store_failures_as` **and declares no hooks**"*, which parsed two ways and was wrong both
> times: hooks cannot be declared inside a `data_tests:` block, so the narrow reading was vacuous, and the
> plain reading banned the `on-run-start` hook Appendix C.1 declares — the one §2 calls *"the only gate
> that travels with the package"* and §2.1 hardens rather than removes. The clause meant the observability
> `on-run-end` hooks §14.8 confines to `integration_tests/`, and now says so. Left unfixed, this assertion
> would have failed forever against a line the same document requires the package to keep.

> **[REVIEW 2026-08-23] Fixed (F8):** 3.20 read "unit-test coverage on fixed-schema models", with
> `check_model_has_unit_tests` scoped and the policy macro skipping anything whose `sql_features` says
> `recursive`. That scope came from `DesignDoc` M17 rec (c), which **D12** supersedes — and it was already
> arguing against M17's own `[RECON]` evidence that recursive `USING KEY` models unit-test fine on the
> pinned toolchain. The row is now unscoped, and 3.69–3.71 add the three mechanisms the wider rule needs.
> Two consequences are carried to the places that encoded the old scope: §18.1's waiver example and
> Appendix A.1's closing paragraph.

> **[REVIEW 2026-08-23] Fixed (F20) — RC55: the four missing injections are named here**, before the
> scaffold rebuild rather than after, so `verify_gates.py` is written against a complete list. 3.69–3.71
> and the rescoped 3.20 stated mechanism and gate and stopped there, against §23's *"a rule without an
> injection has not been shown to fire."* 3.20's injection is materially different post-D12: the violation
> to inject is *any* model without a unit test, not a fixed-schema one.
>
> | Standard | Inject | Expect |
> |---|---|---|
> | **3.20** | A model with no `unit_tests:` entry anywhere in either project root | Compile failure and a bouncer failure, each naming the model — and the message names the fix, not the waiver (C.4 delta 2) |
> | **3.69** | A fixture declared `format: dict` | Non-zero exit naming the fixture |
> | **3.69** | A `format: sql` fixture with one column lacking `cast(… as …)` | Non-zero exit naming **the fixture and the column** |
> | **3.71** | A `consumer_smoke/` build | Zero `unit_test` rows in its `run_results.json`; if dbt does execute them, the documented `--exclude-resource-type unit_test` guard is present and the job asserts that instead |
>
> 3.70 is labelled convention (unenforced) and needs none. 3.69's `check_unit_test_fixtures.py` is a fourth
> file in the class §23's canonical-home rule describes — it has no canonical home until the scaffold lands.

---

## 4. Tool stack and pins

| Tool | Pin | Enforces | Gate | Status |
|---|---|---|---|---|
| dbt-core | `==1.12.2` | Contracts, constraints, flags, unit tests | B | `[VERIFIED]` |
| dbt-duckdb | `==1.11.0` | **Physically enforces** all five constraint types | B | `[VERIFIED]` |
| duckdb | `==1.5.5` | The engine | B | `[VERIFIED]` |
| splink | `==4.0.16` | The parity oracle — baseline generation and the differential loop | CI | `[UNVERIFIED]` — added 2026-08-23, see review note below |
| sqlglot | exact pin (per DesignDoc Stage 0.1) | TF exact-match-level resolution — it, not Splink, decides which levels receive a TF adjustment (DesignDoc A.2 C2, G11) | CI | `[UNVERIFIED]` — added 2026-08-23 |
| SQLFluff + `sqlfluff-templater-dbt` | `==4.3.0` **both** | SQL style + determinism rules | P + CI | `[VERIFIED]` |
| dbt-bouncer | `==3.8.0` | Conventions, coverage, timings | CI | `[VERIFIED]` |
| dbt_utils | `>=1.4.1,<2` | `unique_combination_of_columns`, `expression_is_true` | B | `[VERIFIED]` — the only shipped dependency |
| dbt_project_evaluator | `>=1.3.4,<2` | DAG and governance rules | CI | `[UNVERIFIED]` — demoted 2026-08-23 by 3.44; see below |
| yamllint | `==1.38.0` | `empty-values`, `key-duplicates` | P + CI | `[VERIFIED]` |
| ruff | `>=0.16,<0.17` | Python lint + format | P + CI | `[VERIFIED]` |
| mypy | `>=1.14,<3` | `--strict` over Python | P + CI | `[VERIFIED]` |
| In-repo enforcement scripts | — | The four things no tool does | P + CI | `[VERIFIED]` |
| pre-commit | `>=4.0` | Hook runner | P | `[UNVERIFIED]` |
| GitHub Actions workflows | — | The CI gates | CI | `[UNVERIFIED]` |

> **[REVIEW 2026-08-23] Fixed (F2):** added the `splink` and `sqlglot` rows above. §16 names `splink` among
> the four exact parity-critical pins, but this table had no row for it — and 3.44 "mechanises the demotion
> by comparing §4's pins against `uv.lock`", so a parity-critical pin absent from §4 was invisible to the
> one gate that polices `[VERIFIED]` markers. `sqlglot` closes G11's remaining half (DesignDoc Stage 0.1
> already mandates pinning it exactly; it arrives transitively, so it is invisible to a list naming only
> the four). Both rows are `[UNVERIFIED]` until the scaffold rebuild re-earns markers. Sequel edits with
> the same driver: add a sqlglot version field to §14.9(b)'s run manifest and §20.1's baseline manifest.

**The two-package lockstep rule.** `sqlfluff-templater-dbt==4.3.0` hard-pins `sqlfluff==4.3.0`. Moving one
without the other fails dependency resolution outright. A SQLFluff upgrade is *always* a two-package bump.

**Python floor is 3.12**, the intersection of dbt-bouncer (`>=3.11,<3.15`) and dbt-osmosis
(`>=3.10,<3.14`). Everything else needs only 3.10.

### 4.1 Why SQLFluff and not dbt's own linter

The dbt Fusion engine does ship `dbt lint`. It is not usable here:

- its DuckDB adapter is **beta and CLI-only**, and the bundled driver **cannot load DuckDB extensions** —
  this project needs `json` for the observability layer (§14);
- its autofix **cannot fix** RF02, ST05, ST07, AM06 or CV09 — most of the determinism rule set in §11.

Revisit when the DuckDB adapter leaves beta and can load extensions. Until then the honest answer to "does
dbt have built-in linting now?" is: **yes, but not for DuckDB**.

---

## 5. Repository layout

```
dbt-entity-resolution/
├── dbt_project.yml              # the PACKAGE (name: dbt_er) -- what consumers install
├── packages.yml                 # dbt_utils ONLY. Inherited by every consumer.
├── package-lock.yml
├── profiles/profiles.yml        # checked in; DuckDB has no secrets
├── pyproject.toml + uv.lock     # exact pins for the whole toolchain
├── Makefile                     # every target is also a CI step
├── dbt-bouncer.yml
├── .sqlfluff  .sqlfluffignore  .yamllint.yml  .pre-commit-config.yaml
├── .gitignore                   # target/ dbt_packages/ logs/ .venv/ *.duckdb *.duckdb.wal
│                                # .duckdb_tmp/ .observability/  -- see 3.54
├── README.md  CONTRIBUTING.md  SECURITY.md  CHANGELOG.md  LICENSE  NOTICE
├── .github/
│   ├── workflows/               # ci.yml, nightly.yml
│   ├── CODEOWNERS               # docs/PARITY.md and docs/divergence-log.md have named owners
│   ├── pull_request_template.md # the two human gates (3.36, section 19.4)
│   └── dependabot.yml
│
├── models/
│   ├── _er_groups.yml           # non-resource yml -> leading underscore
│   ├── staging/
│   │   ├── er_stg_input.sql
│   │   ├── er_stg_input.yml     # 1:1, same basename, colocated
│   │   └── er_input/
│   │       └── _er_sources.yml  # sources live in their own directory
│   ├── intermediate/            # er_tf_all, er_int_candidate_pairs, ...
│   └── marts/                   # er_entity_clusters, er_golden_records, ...
│
├── macros/
│   ├── quality/er_assert_project_standards.{sql,yml}
│   ├── observability/er_obs_*.{sql,yml}
│   └── utils/er_*.{sql,yml}
│
├── tests/                       # singular tests, each with its own .yml
├── scripts/                     # the enforcement scripts (section 3 names each one)
├── dbt_bouncer_checks/manifest/ # custom bouncer checks (MUST be a subdirectory)
├── tests_python/                # pytest over scripts/ and dbt_bouncer_checks/ -- see 3.57.
│                                # NOT tests/, which is dbt's test-paths.
│
├── integration_tests/           # the RUNNABLE project -- CI builds here
│   ├── dbt_project.yml          # flags block byte-identical to the package's
│   ├── packages.yml             # local: ../ + evaluator + codegen
│   └── seeds/person_records.{csv,yml}   # in scope for 3.1 -- see section 6.2
│
├── consumer_smoke/              # a THIRD project installing dbt_er by git ref.
│                                # The only place the published install path runs. 3.64.
│
├── harness/                     # pytest parity harness (DesignDoc Stage 0)
│   └── mutants/                 # the comparator sensitivity catalogue -- 3.46, section 12.7
├── fixtures/                    # each artefact carries a .manifest.yml (3.62)
│   ├── source/                  # VENDORED, sha-verified. splink_datasets downloads
│   │                            # fake_1000 from a mutable ref at access time.
│   └── degenerate/              # G9's six shapes fake_1000 never exercises
└── docs/
    ├── DesignDoc.md             # what the SQL computes
    ├── DbtBestPractices.md      # this file -- how the repo stays correct
    ├── PARITY.md                # what is identical, what is bounded, and the published ceiling
    ├── divergence-log.md        # every deliberate difference from the oracle, each pinned by a test
    └── quarantine.md            # flaking gates, dated and expiring -- section 21
```

### 5.1 Two projects, and why

The package at root is what ships. `integration_tests/` installs it via `local: ../` and is where CI
actually builds — this is the standard dbt package convention (`dbt_utils`, `dbt_expectations`).

Two consequences that are easy to get wrong:

**Root `packages.yml` is inherited transitively.** Anything listed there is force-installed into every
project that installs `dbt_er`: its models join their DAG, its hooks fire in their runs, its version
constraints bind their resolution. Adding `dbt_project_evaluator` there to "use it in tests" would ship
~30 extra models to every consumer. Development packages go in `integration_tests/packages.yml`, which is
never inherited. `scripts/check_root_packages_minimal.py` enforces this — the mistake is invisible from
inside this repo, where everything works fine.

**Project `flags:` are read only from the invoked project.** Every CI build runs from
`integration_tests/`, so if that file's flags drift from the package's, the entire strictness policy —
`error: all` included — silently stops applying to every build, with no warning and a green result. That
is the most dangerous failure available here: a gate reporting success because it is no longer running.
`scripts/check_flags_parity.py` compares the parsed mappings.

---

## 6. The 1:1 `.sql` ↔ `.yml` rule

**Rule.** Every `.sql` under `models/`, `macros/` and `tests/`, and every `.csv` under `seeds/`, has a
sibling file of the same basename with a `.yml` extension, in the same directory. Folder-level `schema.yml`
is banned. A `.yml` with no partner is an orphan **unless its name starts with `_`**, the sanctioned escape
hatch for files describing something other than one resource — sources, groups, exposures.

**Why.** The properties file for a model is the file next to the one you are editing. Shared folder-level
files drift silently: a column is added to the SQL, the shared file is not touched, and nothing reports it.

**No released tool enforces this.** That is not an oversight in the tooling; it is a genuine gap:

- **dbt-bouncer 3.8.0** has no such check. Its `check_model_property_file_location` takes **no parameters**
  and hardcodes dbt's `_<dir>__models.yml` convention — it enforces the *opposite* of this rule and would
  fail every model here. It is deliberately not enabled. `[VERIFIED — read from installed source]`
- **dbt-checkpoint**'s `check-model-has-properties-file` asserts only that a model appears in *some* yml.
- **dbt-osmosis** generates properties files; it does not gate them.

So we ship the check. `scripts/check_yml_pairing.py` is ~100 lines with zero dependencies and no version
risk; the custom bouncer check is defence in depth so the violation also lands in the SARIF report.

```python
#!/usr/bin/env python3
"""Enforce the strict 1:1 colocated properties-file convention.  [VERIFIED]"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator

ROOT = Path(__file__).resolve().parents[1]

SQL_DIRS = ("models", "macros", "tests")
SEED_DIRS = ("seeds",)
SKIP_PARTS = {"target", "dbt_packages", "integration_tests", "logs", ".venv", "__pycache__"}
NON_RESOURCE_PREFIX = "_"


def _walk(base: Path, suffix: str) -> Iterator[Path]:
    """Yield files under ``base`` with ``suffix``, skipping generated trees."""
    for path in sorted(base.rglob(f"*{suffix}")):
        if SKIP_PARTS & set(path.relative_to(ROOT).parts):
            continue
        yield path


def main() -> int:
    """Return 0 when every resource is correctly paired, 1 otherwise."""
    errors: list[str] = []

    for dirname in SQL_DIRS + SEED_DIRS:
        base = ROOT / dirname
        if not base.is_dir():
            continue
        partner = ".csv" if dirname in SEED_DIRS else ".sql"

        for src in _walk(base, partner):
            yml = src.with_suffix(".yml")
            if not yml.exists():
                errors.append(
                    f"missing properties file: {yml.relative_to(ROOT)} "
                    f"(required by {src.relative_to(ROOT)})"
                )

        for yml in _walk(base, ".yml"):
            if yml.name.startswith(NON_RESOURCE_PREFIX):
                continue
            if not yml.with_suffix(partner).exists():
                errors.append(
                    f"orphan properties file: {yml.relative_to(ROOT)} (no sibling "
                    f"{partner}). Rename it with a leading '_' if it defines "
                    f"sources, groups, exposures, unit tests or singular-test properties."
                )

        for bad in _walk(base, ".yaml"):
            errors.append(f"use .yml, not .yaml: {bad.relative_to(ROOT)}")

    for err in errors:
        print(f"ERROR: {err}", file=sys.stderr)
    if errors:
        print(f"\n{len(errors)} YAML-pairing violation(s).", file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
```

### 6.1 Two blind spots in the script above `[UNVERIFIED]`

The script is `[VERIFIED]` — it ran and it works. It also does not check what §3.1 claims, in two ways that
only became visible once §5's layout was read against it:

**The seed clause has no subject.** `SKIP_PARTS` contains `integration_tests`, and the only seeds in the
project are `integration_tests/seeds/person_records.{csv,yml}`. The root `seeds/` directory does not exist,
and `if not base.is_dir(): continue` turns that into silence rather than an error. So 3.1's seed half has
been enforcing nothing. This matters because seeds are exactly where agate's type inference does damage —
§12.2 documents that at length for unit-test fixtures, and a seed of date-like strings becoming DATE
violates D8's bare-passthrough guarantee before the first model runs.

**The same exclusion hides a whole project.** Models added under `integration_tests/` are outside the 1:1
rule entirely, and the `.yaml`-not-`.yml` check runs only under the four named directories, so a stray
`.yaml` at repo root or in `harness/` passes.

**Required changes:** walk both project roots rather than one; replace the silent `continue` with a positive
assertion that each configured directory exists, so a renamed directory fails loudly instead of vacuously.
The second is the more valuable of the two — a check whose subject has disappeared is the same failure as a
check that was deleted, and it looks identical from the outside.

### 6.2 The custom dbt-bouncer check `[VERIFIED]`

Two loading rules that are easy to get wrong, both read from the installed 3.8.0 source:

1. **Custom checks must live in a subdirectory.** The loader globs `custom_checks_dir/*/*.py`. A file
   placed directly in `dbt_bouncer_checks/` is never loaded, silently.
2. **An import failure is logged at WARNING and the file is skipped.** A broken custom check produces a
   *green* run. Assert your custom checks are registered rather than assuming.

Point 2 was advice in v1, which is a violation of this document's own convention 1 — it names the worst
outcome available and supplies no mechanism. **3.40 is that mechanism:** CI asserts the expected check names
appear in the bouncer result and that the total meets a floor, and treats any loader WARNING as an error.
Appendix D's "82 checks, 0 errors" is a snapshot, not a floor; the number that would reveal a silently
skipped check is precisely the number nobody asserted. Note the loss is doubled here, because §6 deliberately
runs two independent mechanisms for the 1:1 rule and this failure removes one of them without saying so.

The API is the `@check` decorator with a bare `fail()` — not a `BaseCheck` subclass. The decorator derives
the YAML name from the function name and the class name from PascalCase, which is what the loader scans for.

```python
# dbt_bouncer_checks/manifest/check_one_yml_per_sql.py                    [VERIFIED]
from __future__ import annotations

from pathlib import Path
from typing import Any

from dbt_bouncer.check_framework.decorator import check, fail


# `@check` generates a BaseCheck subclass at import time. dbt-bouncer ships no
# type information, so under `mypy --strict` the decorator is untyped; the narrow
# ignore is preferable to relaxing disallow_untyped_decorators package-wide.
@check  # type: ignore[untyped-decorator]
def check_one_yml_per_sql(model: Any) -> None:  # noqa: ANN401
    """Each model must be documented in `<model_name>.yml` beside `<model_name>.sql`."""
    sql_path = Path(str(model.original_file_path))

    if not model.patch_path:
        fail(f"`{model.name}` has no properties file. Create `{sql_path.with_suffix('.yml')}`.")
        return

    # patch_path is formatted `<package>://models/.../<name>.yml`
    yml_path = Path(str(model.patch_path).split("://")[-1])

    if yml_path.name != f"{model.name}.yml":
        fail(
            f"`{model.name}` is documented in `{yml_path.name}`. The 1:1 rule "
            f"requires `{model.name}.yml`; folder-level schema.yml is banned."
        )

    if yml_path.parent != sql_path.parent:
        fail(f"`{model.name}`: `{yml_path}` is not colocated with `{sql_path}`.")
```

> The `type: ignore` code above is `[untyped-decorator]`. `[misc]` does **not** cover it — mypy reports
> `Error code "untyped-decorator" not covered by "type: ignore[misc]"`, and with
> `enable_error_code = ["ignore-without-code"]` the wrong code is itself an error.

---

## 7. The materialization contract

> **Superseded.** v1 of this section specified `ephemeral` intermediates gated on
> `er_materialise_intermediates`, implementing `DesignDoc` Appendix A **B1**'s recommendation. `DesignDoc`
> **D11** supersedes B1. §7 below is D11's contract. The v1 policy and why it was wrong are kept in
> Appendix A.4 rather than deleted, because the reasoning is instructive and because deleting a rejected
> position invites re-proposing it.

**Every stage model is `materialized='table'`.** No `ephemeral` intermediates, no fused-versus-staged mode
switch, no per-model exception. Every stage stays a real, queryable relation, so the parity harness can
compare any stage directly, stage decoupling needs no special-casing, and a failing run can be inspected
where it failed.

**Narrowness — not materialization choice — is the cost control.** This is the correction D11 makes to B1.
B1 measured one-model-per-CTE at 946 B/pair against Splink's fused 54 B/pair and concluded that
materialisation was the problem. It conflated two costs. Separating them:

| Relation | B/pair (measured: 200k records → 3.9M pairs, 6 comparisons) |
|---|---|
| `er_int_candidate_pairs` | 30.2 |
| comparison vectors, **narrow** — ids + `match_key` + `gamma_*` | **69.4** |
| comparison vectors, **wide** — narrow + `_l`/`_r` source passthrough | **267.9** (3.9× narrow) |
| pairs + narrow | **99.7** |
| pairs + wide | **298.1** (3.0×) |

At a 40 GB `memory_limit`: **401M pairs narrow against 134M wide.** The dominant cost is the `_l`/`_r`
passthrough, not the decision to materialise — which is the distinction v1 did not draw, and the reason it
paid for scale headroom with the project's two most important contracts.

**The contract, per model:**

| Model | Materialization | Columns |
|---|---|---|
| `er_stg_input` | `table` | Fixed schema, entity grain, bare passthrough (D8) |
| `er_tf_all` | `table` | Three columns; a parity artifact in its own right |
| `er_int_candidate_pairs` | `table` | The pair set is a parity gate; must be diffable |
| `er_int_comparison_vectors` | `table` | **`unique_id_l`, `unique_id_r`, `match_key`, `gamma_*` only** |
| `er_int_scored_pairs` | `table` | **ids, `match_weight`, `match_probability`, `bf_*`, `bf_tf_adj_*` only** |
| `er_int_edges` | `table` | Narrow, and the clustering input |
| `er_entity_clusters` | `table` | `WITH RECURSIVE … USING KEY` |
| `er_golden_records`, `er_cluster_membership` | `table` | Public marts |

Splink's `retain_matching_columns` shape is a debugging convenience, not a parity requirement — gammas are
what the next stage consumes, and anything a human wants to read alongside a pair is re-derivable by joining
`er_stg_input` on two ids. The wide shape is therefore an **opt-in per-run debug variant**
(`var('er_retain_matching_columns', false)`), used by the parity harness and by pair-level investigation,
never by production. **3.53** enforces the budget: the policy macro rejects a declared `_l`/`_r` column on
either pair-grain model unless that var is set.

> **[REVIEW 2026-08-23] RC36 — This section carries two verbatim copies of D11 material, without a marker
> saying which copy yields.** The bytes-per-pair table and the 40 GB sentence duplicate `DesignDoc` D11's
> measurement — and drop D11's provenance caveats (DuckDB 1.5.5, threads=4, `duckdb_memory()`, and that
> `int_scored_pairs` is excluded, so these figures are not comparable to B1's 946 B/pair). Two rationale
> sentences ("debugging convenience, not a parity requirement…"; "re-derivable by joining…") are likewise
> verbatim from D11 items 1–2, and the narrow column sets are stated normatively in both documents. Split
> by ownership: measurements belong in `DesignDoc` as tier-1 data (§1.1) — replace the table and the 40 GB
> line with a pointer to D11; the per-model contract table stays here as the binding enforcement surface
> (3.11/3.53 key off it), marked "transcribed from D11 items 1–3" so one copy is declared yielding when
> they diverge. Duplicated prose is exactly what 3.37 admits nothing checks.

### 7.1 What this bought back

Everything §7 v1 listed as the cost of the waiver, which is worth stating as a gain rather than leaving
implicit. `er_int_comparison_vectors` and `er_int_scored_pairs` now carry contracts, DDL constraints, per-model
`run_results.json` rows and therefore timings in the observability tables — on the *production* configuration,
not only under a CI-only var. v1's mitigation was that CI set `er_materialise_intermediates: true` so the
contracts were "still proved on every PR"; that proved them in a configuration production never used, which
is a gate reporting success about something it is not testing.

**Consequences for Appendix C.1**, all of them `[UNVERIFIED]` deltas against the v1 `[VERIFIED]` block:
`er_allowed_materializations` drops `ephemeral`; `er_materialise_intermediates` is removed; `er_max_pairs`
is re-derived, because 42,000,000 came from the **wide** 946 B/pair shape and at the measured narrow
~100 B/pair the same budget admits ~400M — deriving a cap from a byte cost the project no longer incurs
under-provisions by roughly 10×, and the first response to a guardrail firing early is to raise it by hand,
after which it means nothing. D11 item 5 proposes a `make capacity` target that reports measured
`er_bytes_per_pair` for the active model JSON and derives the cap; adopt it rather than hard-coding a second
number.

**Accepted and published, not engineered away.** Even narrow, this stays above Splink's fused 54 B/pair, so a
single-node ceiling below Splink's remains real. It goes in `PARITY.md` (§20.3, 3.50) — D11's words are
"published, not discovered in production."

### 7.2 Custom materializations `[UNVERIFIED]`

v1 built waiver machinery for `ephemeral` — `config.meta.materialisation_waiver_reason`, a single-location
bouncer `exclude:`, the CI var. D11 leaves that machinery without a subject. It should be repointed rather
than deleted, because the construct that will actually need it is already prototyped: `DesignDoc` **D4b**'s
custom `iterative_fixpoint` materialization for clustering, measured at **0.92 s against 206.63 s** for the
recursive CTE on a 10k-node chain.

The hazard is specific. `iterative_fixpoint` is neither `table` nor `incremental`, so dbt's
`materialization_enforces_constraints` returns false for it and **every DDL constraint on
`er_entity_clusters` is dropped** — the one model v1 called never-waivable. The likely sequence is that D4b
lands under performance pressure, the policy macro fires, and someone adds `iterative_fixpoint` to
`er_allowed_materializations` to make the build pass. The never-waivable rule is then waived by a one-line var
edit with no reason recorded, and the project's most important table loses every guarantee in a green build.

**Standard for shipping a custom materialization.** It must:

1. record `config.meta.materialisation_waiver_reason`;
2. state in that reason which of contract, DDL constraints and `run_results` timing it forfeits;
3. carry a **compensating dbt test for each forfeited DDL constraint**, named in the same meta key;
4. be added to `er_allowed_materializations` in the same commit as those tests — never before.

This is also why 3.12 renames `er_must_be_table` to `er_must_carry_constraints`. The property actually wanted
is physical enforcement; `table` is one way to obtain it, not the requirement. D11 anticipates exactly this,
noting the list "will matter again if D4b's custom materialization lands." Note the interaction with
`require_explicit_package_overrides_for_builtin_materializations: true` in Appendix C.1: a package shipping a
materialization is a distinct case from a package overriding a built-in one, and only the latter is what that
flag governs.

### 7.3 One transformation, one model — the CTE rule `[UNVERIFIED]`

**Rule. A model body may not contain a non-recursive common table expression. `WITH RECURSIVE` is
permitted.** A CTE that is not recursive should be a dbt model.

**This is D11's own argument applied one level down**, which is why it belongs in §7 rather than in the
linting section. D11's case for `table` everywhere is that *"every stage stays a real, queryable relation,
so the parity harness can compare any stage directly, stage decoupling needs no special-casing, and a failing
run can be inspected where it failed."* A non-recursive CTE is exactly a stage that is **not** a queryable
relation — a transformation hidden inside another model's statement, invisible to every mechanism this
document is built from. Accepting D11 and permitting CTEs is accepting an argument and then exempting the
most common way of violating it.

Concretely, a stage expressed as a CTE forfeits all of:

| Mechanism | What a CTE loses |
|---|---|
| `contract: {enforced: true}` (§8.1) | No declared columns, no types, no name/order comparison |
| DDL constraints (§8.2, §8.4) | No `not_null`, no primary key, no parity CHECK |
| Data and unit tests (§12) | Nothing to test; 3.19's coverage counts the *parent* model as covered |
| `run_results.json` + §14 | No timing row, so it is invisible in `model_hotspots` and cannot be a perf-gate subject |
| The parity harness | Not diffable per stage — the failure surfaces at the enclosing model, one level too coarse |
| The DAG | No node, so `dbt docs`, `dbt_project_evaluator` and lineage all show a step that isn't there |

The last two are the operative ones for this project. A parity divergence localised to "somewhere inside
`er_int_scored_pairs`" costs materially more to diagnose than one localised to a stage, and localisation is
the entire design of §12.1's stage-parity layer.

**Why recursion is the exception, and the only one.** A recursive CTE is not a hidden stage — it is a
*control structure*, and it is the one thing in SQL that has no model-shaped equivalent. `DesignDoc` D4's
clustering (`WITH RECURSIVE … USING KEY`, monotone min-label) and D5's EM both express a fixpoint iteration
that cannot be decomposed into a finite chain of models, because the iteration count is data-dependent. That
is a real reason, not a convenience. A non-recursive CTE has no such defence: its decomposition is known at
authoring time, and the author simply chose not to write it.

#### 7.3.1 `WITH RECURSIVE` is not a blanket exemption

In standard SQL — and in DuckDB — `RECURSIVE` marks the **whole `WITH` clause**, not an individual term, and
a recursive clause may legally contain non-recursive companion CTEs:

```
with recursive
    helper as (select … from …),        -- not recursive; banned by 7.3
    cc     as (… union all …)           -- recursive; permitted
select … from cc
```

Left unstated, `WITH RECURSIVE` becomes a **one-word waiver for the entire rule**, and it would be applied
first in exactly the two files where it is hardest to notice. So: **a `WITH RECURSIVE` clause may contain
only its recursive term or terms.** A non-recursive companion is a model, or a §18 waiver.

In practice this is rarely binding, and for a reason worth noticing: the seed relation of a recursive CTE can
select directly from a `ref()`, and under §7 every upstream stage already *is* a relation. The companion CTE
is a symptom of an under-decomposed pipeline, which is the thing this rule exists to prevent.

> **[REVIEW 2026-08-23] Fixed (F33) — RC38 is closed by Appendix B.9 / DesignDoc DR-24.** §7.3.1 is scoped
> rather than waived; see B.9 for the definition and for why options (a) and (b) were rejected.
>
> <details><summary>Original review note (RC38), retained</summary>
>
> **RC38 — "Rarely binding" is wrong for the first model this rule meets.** `DesignDoc`
> D4's canonical formulation — the one §7's contract row mandates for `er_entity_clusters`, and the one D4b
> keeps as the reference implementation — opens with `bidir`, a non-recursive companion CTE doubling the
> edge list. The seed-selects-from-`ref()` reasoning above does not cover it: `bidir` is joined from the
> *recursive term*, and an undirected-graph recursion always needs a doubled-edge adapter, so 7.3.1 binds
> on the project's flagship model from day one. The collision is unregistered — B.8 covers only ST05, and
> 3.68 records the enforcement gap, not the conflict. Register as Appendix B.9 with the B.8 option
> structure: (a) `bidir` as a model (2×|`er_int_edges`| rows, `table` under §7); (b) a §18
> `cte_waiver_reason` against the zero-default cap; (c) scoping 7.3.1 to exempt orientation-doubling
> adapters. `DesignDoc` D5's `init_params` is the second instance to settle in the same decision.
>
> </details>

#### 7.3.2 The collision with ST05, which predates this rule

§11.1 sets `forbid_subquery_in = both` (ST05), commented *"Force named, diffable CTEs."* That comment is now
false in its premise, and the conflict is older than 7.3:

- `DesignDoc` **D11 rec 4** and **A.5 item 2** *mandate* an inner subquery in `er_int_scored_pairs` —
  compute `least(greatest(<product>, 1e-300), 1e300)` **once** and derive both `match_weight` and
  `match_probability` from that column, because *"dbt's ephemeral wrapper hard-codes `{name} as (…)` with no
  `MATERIALIZED` (compilation.py:616-625), so single-evaluation must be structural, not a hint."*
- ST05 forbids that subquery and directs the author to a CTE.
- 7.3 bans the CTE.

**Repeating the expression is not an option** — D11 rejects it on parity grounds, noting Splink's own
projection repeats the product three times and that evaluating once is *"strictly better for float parity."*
So the three constructs available are a subquery (banned by ST05), a CTE (banned by 7.3), or a separate model
(a full pair-grain relation, ~100 B/pair against §7's budget, to hold one intermediate float).

**The line that resolves it** is not "CTE versus subquery" but *what the construct is*:

| Construct | Rule |
|---|---|
| Produces a **row set another stage consumes** | It is a stage. Make it a model. |
| Exists only to **evaluate an expression once within a single projection** | Inline is correct; materialising it is the error. |

D11 rec 4 is unambiguously the second. **Recommended resolution: relax ST05 to
`forbid_subquery_in = join`**, permitting a FROM-clause subquery for single-projection expression reuse. A
rule whose only remedy is a construct the project bans is not a rule, and the relaxation is narrowly bounded
precisely because 7.3 forces everything stage-shaped out into models. This changes 3.15's determinism rule
set, so it is registered as **Appendix B.8** rather than applied unilaterally.

**Resolved 2026-08-23 in favour of the alternative, which was tested first `[RUN]` (DR-23, Stage 0.8).**
DuckDB supports **lateral column aliases** — referring to an earlier alias in the same `SELECT` list — which
satisfies D11 rec 4 with no subquery and no CTE, and leaves ST05 untouched. The spike measured the thing D11
actually asks for, using a UDF that counts its own invocations rather than inferring from timings:

| Construct | Evaluations over 1,000 rows |
|---|---|
| **(c) lateral column alias** | **1,000 — once per row** |
| (a) FROM-clause subquery | 1,000 |
| the expression repeated three times | 1,000 |

All three are **bit-identical**. So (c) evaluates once, D11 rec 4 is satisfied structurally, and
**`forbid_subquery_in` keeps its configured scope — 3.15's determinism rule set is not changed.** The
recommendation was *"(a), after testing (c)"*; (c) tested successfully, so (a) is not adopted.

**And the third row is the one worth reading twice.** This section says *"repeating the expression is not an
option"* on a cost argument — and on DuckDB 1.5.5 that argument is **unfounded**: three identical calls cost
one evaluation per row, because the planner splits the projection and computes the common subexpression
once. `EXPLAIN` shows the inner projection doing exactly that. (c) remains the choice, because it states the
intent in the SQL rather than relying on an optimiser pass that a version bump could withdraw — but a
premise that turned out to be false is recorded as false, rather than left standing because the conclusion
it supported survived anyway.

**Both facts are engine behaviour, not guarantees, so they are scoped to the pin** exactly as §0 scopes a
`[VERIFIED]` marker. `harness/test_duckdb_expression_semantics.py` asserts the DuckDB version, both
evaluation counts, and bit-identity — and includes a guard proving the counter can tell two evaluations from
one, so the whole file cannot pass vacuously. A DuckDB bump that folds differently fails there, loudly,
instead of quietly reintroducing the cost D11 rejected on the project's hottest relation.

#### 7.3.3 Cost, honestly

This rule makes the cheapest thing to write — a CTE — into the most expensive: a new model needs a `.sql`, a
colocated `.yml` (3.1), a six-section description (3.5), every column declared, typed and described (3.3,
3.4), a primary key or a recorded reason (3.6, 3.7), a unit test (3.20), and — under §7 — a materialised
table costing real bytes against `er_max_pairs`. At pair grain that is not a rounding error.

**That friction is the point, and it should not be sanded off.** It prices in the cost of a hidden stage at
the moment the stage is created, rather than at the moment someone is bisecting a parity failure through a
400-line model. The failure mode this rule prevents is not a wrong answer; it is a model that grows six CTEs
over a year, each individually reasonable, until no stage inside it can be tested or diffed and the parity
harness can only say that something in it disagrees with Splink.

Where the cost genuinely does not justify the split, §18 applies: `config.meta.cte_waiver_reason`, scoped in
one place, and **capped at zero by default** — so permitting even one is a visible, reviewable diff to
`er_max_cte_waivers` in `dbt_project.yml`, not a quiet edit inside a model.

#### 7.3.4 Enforcement, and where it is blind

Primary mechanism is the one 3.24 already establishes for statement-level `ORDER BY`:
`check_model_code_does_not_contain_regexp_pattern` over `raw_code`, matching a `WITH` that is not followed by
`RECURSIVE`, with comments and string literals stripped first — the two-tier scanner in §11.3 already
demonstrates that handling and should be reused rather than reinvented.

Two blind spots, stated rather than assumed:

1. **The companion-CTE case (7.3.1) is not a regex problem.** Deciding whether a term inside a
   `WITH RECURSIVE` clause is itself recursive requires a parse. SQLFluff cannot supply it: §11.2 records
   that 4.3.0's DuckDB dialect has **no grammar for `WITH RECURSIVE … USING KEY`**, so the two files where
   this bypass would appear are precisely the two that fail to parse. sqlglot is the candidate — it is
   already parity-critical for §3.2's TF exact-match-level analysis (and per `DesignDoc` G11 appears on no
   pin list, which needs fixing regardless) — but whether it parses `USING KEY` is unverified. Until one of
   them can, 7.3.1 is **convention (unenforced)** and should be labelled so in §3 rather than assumed
   covered.
2. **Macro-generated SQL is out of scope.** The check reads `raw_code`, so a CTE emitted by a macro is
   invisible to it. That is deliberate — 7.3 is about authored pipeline structure, and the `{% for %}` loops
   that emit the `gamma_*`/`bf_*` families are not modelling decisions — but it is a real hole, and a macro
   is where someone will eventually put a CTE to avoid writing a model.

---

## 8. Contracts, constraints and keys

### 8.1 Contracts are the primary documentation gate

`contract: {enforced: true}` on every table model. Not for type safety alone — because it is the only
mechanism that **cannot be fooled**:

- dbt raises on an **empty column list**, so "the model has a yml" cannot pass for "the model is documented";
- it compares column **name, type and order** against the relation actually built;
- no catalog, no linter and no heuristic is involved.

Its one blind spot: dbt does **not** warn when a yml documents a column the model no longer produces
(dbt-core #6039, closed `not_planned`). The catalog check `check_columns_are_all_documented` closes that gap
from the other direction, which is why both exist.

`alias_types: false` is set so DuckDB's native types are written without adapter remapping — parity work
compares types directly and silent widening is a defect.

### 8.2 dbt-duckdb enforces all five constraint types `[VERIFIED]`

Most adapters cannot offer this. Read from `dbt/adapters/duckdb/impl.py`:

```python
CONSTRAINT_SUPPORT = {
    ConstraintType.check:       ConstraintSupport.ENFORCED,
    ConstraintType.not_null:    ConstraintSupport.ENFORCED,
    ConstraintType.unique:      ConstraintSupport.ENFORCED,
    ConstraintType.primary_key: ConstraintSupport.ENFORCED,
    ConstraintType.foreign_key: ConstraintSupport.ENFORCED,
}
```

Confirmed in the resulting DDL:

```sql
CREATE TABLE main_entity_resolution.er_stg_input(
    unique_id VARCHAR PRIMARY KEY, first_name VARCHAR, surname VARCHAR,
    dob VARCHAR, city VARCHAR, email VARCHAR,
    CHECK((length(unique_id) > 0))
);
```

`persist_docs: {relation: true, columns: true}` also works — column descriptions land as real database
comments, so the documentation is queryable from the warehouse and not only from `dbt docs`.

### 8.3 The primary-key grain split

Blanket DDL primary keys are the wrong policy here, and the reason is cost, not principle. A composite
VARCHAR primary key on a pair-grain table measured **~23s per 5M rows against ~0.10s for the equivalent dbt
test — roughly 100×** — and builds an in-memory ART index of **~1.8 GiB at 10M rows**, which is a plausible
OOM on a 16 GB runner rather than a slowdown.

| Grain | Models | Key enforced by |
|---|---|---|
| **Entity** | `er_stg_input`, `er_tf_all`, `er_entity_clusters`, `er_cluster_membership`, `er_golden_records` | **DDL `primary_key`** constraint |
| **Pair** | `er_int_candidate_pairs`, `er_int_comparison_vectors`, `er_int_scored_pairs`, `er_int_edges` | **`dbt_utils.unique_combination_of_columns`** test + recorded `config.meta.primary_key_by_test_reason` |

The boundary is held by the `include:` regexes in `dbt-bouncer.yml`, so it cannot rot into "we stopped
adding keys".

**§7's move to `table` everywhere makes this section coherent rather than aspirational.** Under v1's policy
two of the four pair-grain models were `ephemeral` in production, so they had no relation for a uniqueness
test to run against and no contract to declare the key on — the grain split was documented but only half
applied. All four are now real relations, so the test-enforced key is enforced where v1 said it was.

**Primary keys are always declared at model level**, even single-column ones. Two mechanical reasons:

1. `check_model_has_constraints` reads only `model.constraints` — a **column-level `primary_key` is
   invisible to it** `[VERIFIED]`, so the coverage gate would silently pass an unkeyed model;
2. every pair-grain key is composite, which dbt requires at model level anyway (it hard-errors on more than
   one column-level primary key). One rule for both avoids a split convention that only surfaces when the
   first composite key lands.

Declaring both levels is an error: *"Primary key constraints defined at the model level and the columns
level. Primary keys can be defined at the model level or the column level, not both."* `[VERIFIED]`

### 8.4 CHECK constraints carry parity invariants

Measured essentially free — three CHECKs on 5M rows: 0.50s against a 0.44s baseline — and DuckDB has **no
`ALTER TABLE ADD CONSTRAINT`**, so a dbt contract is the only way to attach one at all. Use them for the
invariants that must never be false:

```yaml
constraints:
  - type: check
    name: er_pair_canonical_order
    expression: unique_id_l < unique_id_r
  - type: check
    name: er_match_weight_finite
    expression: isfinite(match_weight)
  - type: check
    name: er_match_probability_range
    expression: match_probability >= 0.0 and match_probability <= 1.0
```

**Two qualifications on `isfinite` `[UNVERIFIED]`.** `DesignDoc` §3.4 emits `cast('Infinity' as float8)` for
a `u = 0` level, and §3.1 documents a NaN path where `greatest(NaN, 1e-300) = NaN` yields a saturated
weight. The Splink clamp should keep `match_weight` finite in every *reachable* case, and M13 recommends
hard-erroring on `m == 0` / `u == 0` at validation, so the constraint is probably safe — but:

1. **Verify it against a deliberately degenerate model rather than assuming.** A CHECK that fires on a
   legitimate input turns a scoring edge case into a build failure, and DuckDB has no
   `ALTER TABLE DROP CONSTRAINT` to back it out with.
2. **Do not extend the same CHECK to `bf_*` columns.** Individual Bayes factors can legitimately hold
   `Infinity`; only the clamped product is guaranteed finite. This is the easy mistake, because the columns
   sit beside each other in the same contract.

*(`DesignDoc` Appendix B, R4.)* Note the second-order consequence R4 identifies: under §7's move to `table`
everywhere, contracts and CHECK constraints **return to the two models that lost them** under v1's ephemeral
policy — which is the outcome `DesignDoc` M2 requires, and is worth stating here so the connection is not
rediscovered later.

### 8.5 `foreign_key` is banned

DuckDB refuses `ALTER TABLE RENAME` and `DROP` on a table that is an FK parent, and dbt renames
`existing → backup` on every rebuild. The result is that **the second run fails, permanently**
(dbt-duckdb #425). An FK also silently injects a DAG edge that no `ref()` explains.

Use a `relationships` test instead. The policy macro scans `node.constraints` and every column's
`constraints` and raises at compile time, because this failure appears one run *after* the change that
caused it — by which time the connection is not obvious.

---

## 9. Models whose columns come from the model JSON

`er_int_comparison_vectors` and `er_int_scored_pairs` emit one `gamma_<comparison>` and one
`bf_<comparison>` column per comparison in the trained Splink model JSON. Their column *set* is data, not
source code — which collides head-on with "every column is declared, typed and described".

**It is still solvable, and the mechanism is narrow.** dbt parses a properties file as YAML **first**, then
renders each leaf **value** with native Jinja. A rendered leaf can therefore return a genuine Python list of
dicts:

```yaml
columns: "{{ var('er_gamma_columns') }}"     # returns a real list -> contract works
```

Two constraints that decide the whole design:

1. **A `{% for %}` loop in a properties file cannot work.** YAML is parsed before rendering, so a loop
   returns a `str`, not list structure. Only a leaf value can carry a list.
2. **`SchemaYamlContext` exposes exactly two functions: `var` and `env_var`.** No macros. So the column list
   must be *derived where the model JSON is emitted* — by the wrapper that sets the environment — and
   handed in as a var. It cannot be computed by a dbt macro.

**Ingestion path.** `fromjson(env_var('DBT_ER_MODEL_JSON'))`, not `--vars`:

- `--vars "{er_model: $(cat f.json)}"` is bounded by `MAX_ARG_STRLEN` (**128 KiB, ~330 comparison levels**)
  and a real production model exceeds it;
- `env_var` has no such bound and — decisively — is available in the schema-YAML context, which is what
  makes the contract above possible at all.

> **[REVIEW 2026-08-23] Fixed (F29) — RC39: the ingestion path is D1/DR-02's, and is not restated here.**
> Three live statements of one decision is the G1 shape at smaller scale, and one of the three disagreed:
> `DesignDoc` Appendix A **M2** ends *"Prefer it; keep `--vars` as the documented CI form"*, which this
> section's flat "not `--vars`" contradicted.
>
> **D1 settles it, and `--vars` does not survive even for small fixtures.** The reason is not the size
> bound — a small fixture would fit — it is constraint 2 above: `SchemaYamlContext` exposes exactly `var`
> and `env_var`, so a `columns:` leaf can read the derived list either way, but the *emitting* wrapper needs
> one path, not two. A second documented form is a second thing that can be stale, and `DesignDoc` §8's DoD
> item 2 is now written against `DBT_ER_MODEL_JSON` specifically. M2's note is superseded on this point.
>
> Constraints 1–2 above stay: the schema-YAML rendering behaviour is this section's own subject and was
> verified here. The bound and the context argument stay in D1.

**Drift guard.** A pytest asserts that `var('er_gamma_columns')` matches exactly what
`er_comparison_vector_sql` renders for the same model JSON. Without it the contract is merely a *second*
hand-maintained copy of the truth, which is the problem it was meant to solve. `[UNVERIFIED]` — designed,
not yet written.

**Related, from DesignDoc M8:** model identity must live in the **data**, not only in the file. Every scored
row carries `er_model_sha` and `er_tf_snapshot_id`, with a test asserting `count(distinct …) = 1`. Without
it, a retrain leaves v3 weights on old pairs and v4 on new ones — every parity test still passes, because
the harness runs full-refresh on fixtures, and clustering then thresholds across incommensurable evidence.

---

## 10. Documentation standard

### 10.1 Model descriptions: six required sections

Every model description contains these six headings, checked literally by the policy macro:

```
**Purpose:**        What it computes and why it exists.
**Grain:**          One row per WHAT. The single most useful line in the file.
**Upstream:**       What it reads, and anything not obvious from ref().
**Splink parity:**  Which Splink artifact it mirrors and what the gate is.
**Determinism:**    Tie-breaks, ordering guarantees, and what is NOT guaranteed.
**Caveats:**        What will silently break if someone "improves" this model.
```

The **Caveats** section earns its place. `er_stg_input` reads, in part:

> This model is a BARE PASSTHROUGH and must stay one. No lowercasing, trimming, date parsing or
> deduplication may be added here. Splink applies `ColumnExpression` transforms inline inside each
> comparison's CASE while the projection feeding it selects the raw column, so any normalisation at this
> stage diverges from the oracle on every transformed comparison — silently, and only in the gamma values.

That is a warning aimed at a future contributor who will otherwise make a change that looks like an
improvement, and whose damage surfaces stages later as a handful of wrong gammas.

**What is enforced:** the six headings exist, and the description exceeds `er_min_model_description_chars`
(40). **What is not:** whether the content is true. §3.35, convention (unenforced) — stated plainly rather
than implied.

### 10.2 Column descriptions

Every column carries `data_type` and a description of at least `er_min_column_description_chars` (10).
Descriptions say what the column *means* and whether it is nullable **and why** — in entity resolution,
nullability is a signal, not an oversight:

> `first_name` — Given name exactly as supplied by the source system, with no normalisation. Nullable:
> missingness is a real signal in entity resolution and Splink's null comparison level depends on seeing it.

### 10.3 The `docs-paths` footgun

Setting `docs-paths` **replaces** the default (all resource paths). Any `{% docs %}` block outside the
listed directories is dropped **silently**. Keep the list a superset of wherever `.md` doc blocks live:

```yaml
docs-paths: ["models", "macros"]
```

### 10.4 The gate contracts cannot provide

`description:` followed by nothing is valid YAML. It produces a null description; dbt accepts it; the
contract still passes because the column *is* declared; and the model looks documented in every listing.

The only thing that catches it is yamllint's `empty-values`. That is why yamllint is a required gate and
not a nicety.

### 10.5 Identifiers `[UNVERIFIED]`

v1's 3.33 governed model names per directory and macro-name-matches-file-name, and nothing else. The rest of
the identifier surface was ungoverned, and Appendix C.1 already broke its own pattern: `dbt_er_enabled` sits
among twenty `er_*` vars.

| Kind | Rule |
|---|---|
| Vars | `er_*`, no exceptions. Rename `dbt_er_enabled` → `er_enabled` |
| Groups | `er_*` |
| Tags | From `er_allowed_tags` only (3.42) |
| Singular tests | `er_<model>_<assertion>` |
| Macros | `er_*`, file name matches macro name |
| Generated columns | `gamma_<comparison>`, `bf_<comparison>`, `bf_tf_adj_<comparison>`, `<column>_l` / `<column>_r` |

**The column grammar is the load-bearing row; the rest is hygiene.** `DesignDoc` guiding principle 2 is that
*"every column name is a pure function of the model JSON, derived at parse time. Never introspected"* — and
§9 establishes that two independent paths derive those names: the SQL generator, and the `er_gamma_columns`
var computed by the wrapper that emits the model JSON. Two derivations of one truth need a written grammar,
including the escaping rule for a comparison name that is not identifier-safe (spaces, an embedded
underscore that collides with the `bf_tf_adj_` prefix, a leading digit). Without one, the first disagreement
gets fixed in whichever path is easier to reach and diverges again.

§9's drift guard — the pytest asserting `var('er_gamma_columns')` matches what `er_comparison_vector_sql`
renders — is what enforces the grammar. It is marked `[UNVERIFIED]` there, designed but not written, and it
should be written before the second comparison type lands rather than after.

---

## 11. Linting

### 11.1 `.sqlfluff` `[VERIFIED]`

> **[REVIEW 2026-08-23] Fixed (F25) — RC40 is closed by §23's canonical-home rule and Appendix D.1 step 3.**
> The listing stays here only until `.sqlfluff` exists as a file; at that point the file is canonical, this
> section is reduced to its rationale plus a pointer by path, and the block is not copied forward. Moving it
> to Appendix C first would be a second move for no gain — Appendix C is itself scheduled to reduce to
> pointers under the same rule. What stays here permanently is the rule-by-rule reasoning that is genuinely
> design content: the blocked-words list against D9's `USING SAMPLE` carve-out, the casting-style choice,
> and the ST05 / §7.3 / D11 collision — whose premise B.8 settled on 2026-08-23, in favour of leaving the
> rule alone.

**Canonical: [`.sqlfluff`](../.sqlfluff).** Per §23 the repository file is canonical; what stays here is
the rule-by-rule reasoning, which is design content rather than configuration.

**Why each non-default choice is what it is.** The dialect is `duckdb` and the templater is `dbt`, so
SQLFluff parses what dbt actually emits. `LT02` indentation is four spaces with `template_blocks_indent`
on — Jinja blocks are indented with the SQL they generate, which is what makes a `{% for %}` emission loop
readable. `CP01`/`CP02` force lowercase keywords and identifiers; `AM04` requires an explicit column list,
because `select *` in a model is a contract that changes without a commit. **`ST05` is the collision**: it
bans subqueries in favour of CTEs, §7.3 bans CTEs inside `WITH RECURSIVE`, and D11 makes every stage a
table — so a rule that is right everywhere else is wrong on the flagship models. **B.8 closed this on
2026-08-23 without touching the rule**: Stage 0.8's spike measured a lateral column alias evaluating **once
per row** on DuckDB 1.5.5, which satisfies D11 rec 4 with no subquery at all, so `ST05` keeps its
configured scope. See §7.3.2 and DR-23.

The **blocked-words list** carries one deliberate carve-out: `USING SAMPLE` is blocked as
non-deterministic, but D9 needs it, so the carve-out is named in the file rather than left to a reviewer.
The casting style is `cast(x as type)` rather than `x::type`, because §12.2's fixtures must spell their
types out and a fixture is easier to read when it matches the model.


Two rules earned their keep immediately on the reference model: **RF04** flagged an alias named `source`
(a keyword), and **RF03** caught an unqualified column reference in a single-table CTE.

> **ST05's comment is now false in its premise `[UNVERIFIED]`.** *"Force named, diffable CTEs"* was written
> when CTEs were the sanctioned way to name an intermediate step. Under **7.3** they are banned, so ST05's
> only remedy is a construct the project forbids — and `DesignDoc` D11 rec 4 independently *mandates* the
> FROM-clause subquery ST05 rejects. §7.3.2 sets out the three-way collision and recommends relaxing this to
> `forbid_subquery_in = join`; because that changes 3.15's determinism rule set, it is registered as
> **Appendix B.8** rather than changed here. Until it is decided, the `[VERIFIED]` block above is reproduced
> as executed and `er_int_scored_pairs` cannot be written to satisfy both rules at once.

### 11.2 `.sqlfluffignore` and the exemption cap `[VERIFIED]`

SQLFluff 4.3.0's duckdb dialect has **no grammar for `WITH RECURSIVE … USING KEY`** — the phrase appears
zero times in `dialect_duckdb.py`, and there is no `CommonTableExpression` override. Affected files raise
PRS (unparsable) and `sqlfluff fix` refuses to touch them.

**Canonical: [`.sqlfluffignore`](../.sqlfluffignore).** It excludes build output — `target/`,
`dbt_packages/`, and both under `integration_tests/` — plus the recursive models SQLFluff cannot parse at
all.

**The cap is the point, not the list.** §11.2 permits at most **two** parse-failure exemptions, and each
must name the SQLFluff version that could not parse it. An ignore file that grows silently is how a linter
stops covering the code it was bought for; a cap that must be raised in a reviewed commit is a
conversation. `scripts/check_no_nondeterminism.py` still walks the ignored files, so the determinism rules
apply to code SQLFluff itself cannot read.


The cap matters more than the exemption. An ignore file with no ceiling becomes the place failures go to
be forgotten.

#### 11.2 deltas required before the rebuild `[UNVERIFIED]`

The block above is kept **as executed**, per §0. These are the changes to apply when the file is recreated.

| # | Change | Driver |
|---|---|---|
| 1 | `models/intermediate/er_int_em_iterations.sql` → **`models/intermediate/er_train_em.sql`** | RC41 |
| 2 | The `≤ 2` cap **stands**, and now has slack | RC41, `DesignDoc` §5 Stage 6 |

**Delta 1 (closes RC41).** `er_int_em_iterations` matches no model in either document. `DesignDoc` §2 names
the EM model `train_em`, and D5 builds EM as a single `WITH RECURSIVE … USING KEY` statement — the
`_iterations` suffix is the pre-D5 *unrolled* design D5 explicitly killed. The consequence of leaving it is
not cosmetic in either direction: the real EM model would raise PRS unexempted and **fail lint**, while the
dead entry consumed one of the two capped slots. The directory is settled as `models/intermediate/` —
training models produce parameters that scoring consumes; they are neither staging nor a mart.

**Delta 2.** RC41 also asked whether the cap itself survives the inventory, since a third model needing
`USING KEY` would make the `≤ 2` assertion fail on legitimate content. It survives:
`er_entity_clusters_1to1` was the candidate third, and `DesignDoc` §5 Stage 6 has since **tagged it v2** —
`cluster_using_single_best_links` is defined over source datasets, which Stage 12.1's matrix forbids. The
two exempt models are `er_entity_clusters` and `er_train_em`. If 1:1 clustering is ever un-deferred, the cap
is raised **in the same PR** with the reason recorded, not quietly.

> **[REVIEW 2026-08-23] Fixed (F23) — RC41 is closed by the delta table above.**
>
> <details><summary>Original review note (RC41), retained</summary>
>
> **RC41 — `er_int_em_iterations` matches no model in either document.** `DesignDoc` §2
> names the EM model `train_em` (`er_train_em` shipped), and its D5 builds EM as one
> `WITH RECURSIVE … USING KEY` statement — "_iterations" is the pre-D5 unrolled design D5 explicitly
> killed. Training is also `DesignDoc` Stage 9, so `models/intermediate/` is at least undecided.
> Consequence of leaving it: the real EM model raises PRS unexempted and fails lint, while this dead entry
> consumes one of the two capped slots. Rename to the inventoried model and settle its directory. Also
> check the cap itself against the inventory: if `er_entity_clusters_1to1` needs `USING KEY` for its
> mutual-best-link iteration, three models need the exemption and the ≤ 2 assertion fails on legitimate
> content.
>
> </details>

### 11.3 The non-determinism lint — the one nothing else does `[VERIFIED]`

DesignDoc M15 measured it: `set()` and `set_strict()` are in dbt's Jinja context and return real Python
sets, whose iteration order over strings is **randomised per process** when `PYTHONHASHSEED` is unset. Four
interpreters rendering the same five-element set produced four different orders.

The consequence is worse than a wrong answer: **one `set()` in a macro makes the generated-SQL snapshot test
fail ~80% of the time**, which presents as a flaky test rather than a defect and gets triaged as "CI is
flaky" for weeks.

No linter catches this. SQLFluff sees only post-render SQL; dbt-bouncer reads the manifest — by which point
the non-determinism is already baked in. So the check is a file-level scan, deliberately in **two tiers**:

```python
# Tier 1: banned EVERYWHERE, no exemptions. Randomised iteration order is never
# acceptable -- it makes output order a function of the interpreter, not the data.
ALWAYS_BANNED = {
    r"\bset_strict\s*\(": "set_strict() iteration order is randomised per process ...",
    r"(?<![\w.])set\s*\(": "set() iteration order is randomised per process ...",
}

# Tier 2: run-context values. Legitimate -- necessary, even -- inside a hook macro
# that records what a run did. Fatal in anything that becomes a model's compiled
# SQL, because they change the SQL text between two runs of identical input.
RUN_CONTEXT_BANNED = {
    r"\binvocation_id\b":  "... must never reach compiled SQL.",
    r"\brun_started_at\b": "... must never reach compiled SQL.",
    r"\bthread_id\b":      "thread_id is scheduling-dependent.",
    r"\benv_var\s*\(":     "env_var() in model code makes compiled SQL host-dependent.",
}

# Files permitted to read run-context values: macros that run ONLY from a hook,
# plus the model-JSON loader. Adding to this set is a reviewable act -- it is the
# only way to defeat Tier 2.
EXEMPT_RUN_CONTEXT = {
    "macros/model_json/er_load_model_json.sql",
    "macros/observability/er_obs_enabled.sql",
    "macros/observability/er_obs_relation.sql",
    "macros/observability/er_obs_bootstrap.sql",
    "macros/observability/er_obs_on_run_start.sql",
    "macros/observability/er_obs_capture_query_metrics.sql",
    "macros/observability/er_obs_log_run_results.sql",
}
```

The two tiers are the point. A single blunt ban would have forced the observability macros — which
legitimately need `invocation_id` and `env_var` — to be exempted wholesale, taking the `set()` ban with
them. Comment lines are skipped so prose explaining the ban does not trip it.

> **[REVIEW 2026-08-23] Fixed (F26) — RC42 is closed by §23's canonical-home rule.** The two-tier design and
> its justification are this document's content; the regex dictionaries — and especially
> `EXEMPT_RUN_CONTEXT` — are the script's, and that allowlist is the one part guaranteed to churn, because
> every new observability macro edits it and every edit happens in `scripts/check_no_nondeterminism.py`
> (3.16). **When the script lands, this section keeps the tier rationale and one illustrative entry per
> tier, and states that the authoritative sets live in the script.** A stale allowlist quoted here is worse
> than none: it tells a reviewer an exemption exists, or does not, with the confidence of a `[VERIFIED]`
> block.

### 11.4 YAML, Python and the rules deliberately turned off `[VERIFIED]`

`yamllint --strict` keeps `empty-values` (§10.4) and `key-duplicates` (two blocks with the same key silently
override one another — in a properties file, a column's tests simply vanish). Two rules are **disabled on
purpose**, and the reasoning generalises:

- **`quoted-strings`** — dbt's own documentation quotes project names, paths and version strings.
  `only-when-needed` flags every one. It catches no defect: a redundant quote cannot silently change meaning.
- **`braces: forbid`** — dbt's documented style uses flow mappings heavily
  (`+persist_docs: {relation: true, columns: true}`). Forbidding them buys no correctness, only churn
  against every example a contributor will copy.

The test for keeping a rule is *"can violating this silently change behaviour?"* — not *"is it tidier?"*.
`empty-values` passes that test; `quoted-strings` does not.

Python: `ruff select = ["ALL"]` plus `ruff format` plus `mypy --strict`. **`PLR2004` (magic values) is
deliberately kept** — it forces the parity tolerance to be a named module constant rather than a literal
scattered across comparators, which is exactly the failure mode DesignDoc §6.1 describes. Per-file ignores
are narrow and reasoned: `T201` (print) for `scripts/`, whose entire interface is writing diagnostics to
stderr; `INP001` for `dbt_bouncer_checks/`, which is exec'd by path rather than imported as a package.

---

## 12. Testing

### 12.1 The layers, and who owns what

| Layer | Tool | Proves | Runs |
|---|---|---|---|
| Unit tests | dbt | Model logic against hand-built rows, no warehouse data — **every model carries them** (3.20, per DesignDoc D12) | Every build |
| Data tests | dbt + dbt_utils | Invariants on real output: uniqueness, nullability, ranges | Every build |
| Singular tests | dbt | Cross-model invariants a generic test cannot express | Every build |
| Contract + constraints | dbt + DuckDB | Physical shape and keys | Every build |
| **Stage parity** | pytest harness | Equivalence to Splink, localised per stage | Every PR |
| **Comparator sensitivity** | pytest harness | That the parity gates **can fail** (§12.7) | Every PR |
| **Fixture coverage** | dbt data test | That the fixtures still exercise every gamma cell and `match_key` | Every build |
| **Determinism** | pytest harness | Content stability across runs and row permutations | Every PR |
| Convention | dbt-bouncer | Coverage, naming, structure, timings | Every PR |
| DAG governance | dbt_project_evaluator | Structural anti-patterns | Every PR |

Two of those rows are new in v2 and both close the same kind of hole: a layer that reports success about a
question narrower than its name. **Fixture coverage** is not §3.19's test coverage — 3.19 counts *nodes with
tests*, this counts *data actually exercised*. `DesignDoc` M9 measured the difference: **5 of 18 gamma cells
never observed in 101,797 pairs**. An unreachable comparison-level `CASE` branch containing a transcription
error from the model JSON passes every parity test, because both engines agree about the pairs that exist,
and ships with a full green history behind it. 3.47 asserts each gamma value is observed at least
`er_min_gamma_cell_observations` times and publishes the cell matrix as a CI artefact even when green, so
shrinking coverage is visible before it reaches zero.

The split between dbt tests and the pytest harness is **invariants versus equivalence**. dbt answers "is
this output self-consistent?"; the harness answers "is this output the same as Splink's?". Neither
substitutes for the other, and the harness is where anything needing float tolerance must live — see 12.3.

### 12.2 Unit tests: every model, and how they are written

**The rule, and where it comes from.** *Every* model carries at least one dbt unit test — 3.20, per
`DesignDoc` **D12**, which also fixes *when* the cases are chosen: while the model is being written, by
answering D12's six questions in the same PR. This section is the mechanics. The upstream reference is
[docs.getdbt.com/docs/build/unit-tests](https://docs.getdbt.com/docs/build/unit-tests?version=2); unit
tests are a dbt ≥ 1.8 feature and §4 pins 1.12.2.

**Why this layer, given the other eight.** A unit test runs **before** the model is materialised, against
static rows we wrote; every other layer in §12.1 runs after, against data. Two consequences justify the
coverage rule. It is the only layer that can reach a branch the fixture corpus never produces — the
unit-test half of `DesignDoc` M9's *5 of 18 gamma cells never observed in 101,797 pairs*. And it is the
only layer that fails before a wrong number is written anywhere, which on a pair-grain model is the
difference between a failed assertion and a materialised relation of wrong scores at ~100 B/pair (§7).

dbt's own "when to unit test" guidance — complex logic, contracted or public models, models upstream of an
exposure, models where a bug has already been found — selects this entire DAG, which is why the coverage
rule is *every model* rather than a judgement call per model. Its one exclusion, a model that only wraps a
warehouse built-in, has no instance here.

**Where they live.** In the model's own colocated `<model>.yml`, under `unit_tests:` — they are properties
of that model, so §6's 1:1 rule already decides this. Not under `tests/`, which is dbt's `test-paths` for
singular tests, and not in a shared file. A `_`-prefixed file may hold unit tests only in the case §6
already sanctions: when it describes no single resource.

**Running them.**

| Intent | Command |
|---|---|
| Everything for one model, in order (unit tests → build → data tests) | `dbt build --select <model>` |
| Only the unit tests of one model | `dbt test --select "<model>,test_type:unit"` |
| Every unit test in the project | `dbt test --select "test_type:unit"` |
| Satisfy the parent-existence precondition first | `dbt run --empty --select <parents>` |
| Keep them out of a production run | `dbt test --exclude-resource-type unit_test` (or `DBT_ENGINE_EXCLUDE_RESOURCE_TYPES=unit_test`, dbt ≥ 1.11) |

**Preconditions** (`DesignDoc` M17): the `unit` materialization reads column types back from the parent
relations, so **parents must already exist** — `get_fixture_sql` otherwise raises *"Not able to get columns
for unit test … because the relation doesn't exist"*. `dbt run --empty --select <parents>` satisfies that
cheaply, and under 3.41 that selection is asserted to be non-empty, so a renamed parent fails loudly rather
than skipping the preparation and then the tests.

**Where that precondition actually binds `[UNVERIFIED]`.** A standalone `dbt test --select "test_type:unit"`
against a fresh database needs the `--empty` step; a full `dbt build` from clean is expected to satisfy it
in DAG order, since parents are built before the child whose unit tests gate it, and §15's `build` job runs
`dbt seed` → `dbt build --empty` → full `dbt build` in that order regardless. Stated as an expectation
rather than a fact because it was not executed: confirm it on the scaffold rebuild before anyone deletes
the `--empty` step as redundant.

**Recursive SQL is not an exemption, and neither is anything else.** dbt's documentation lists recursive
SQL as unsupported; `DesignDoc` M17(c) `[RECON]` found all three dbt wrapper shapes **execute correctly
against a `USING KEY` model on DuckDB 1.5.5**. Under §1.1 the measurement governs for the toolchain §4
pins, so `er_entity_clusters` is unit-tested like every other model and the pytest harness covers it *in
addition*, not instead. That measurement is scoped to those pins and expires with them (§0), so it is
re-checked on every dbt or DuckDB bump alongside the D4 gate (§16). **If a bump breaks it, the result is a
3.43 waiver naming the version that broke it and carrying an expiry date** (§21) — not a standing
exclusion. The distinction is the whole point of D12: *"exempt because dbt 1.13.0 regressed, expires when
1.13.1 lands"* is a fact someone will revisit; *"exempt because recursive"* was a policy nobody had
re-examined against the evidence sitting two paragraphs away from it.

**The fixture-typing trap `[VERIFIED]`.** `format: dict` fixtures are typed by agate's inference over the
literal values. It inferred **DATE** for a `dob` column from the strings `"not-a-date"` and `"1990-13-45"`,
so dbt emitted `cast('not-a-date' as DATE)` and the test died with a conversion error.

The error was the lucky outcome. Had the sample values all looked like dates, the fixture would have
silently become a DATE column — and a test whose entire purpose is proving the model does **not** transform
values would have been running against pre-parsed input.

**Therefore: every fixture uses `format: sql` with an explicit cast on every column** — 3.69, which is the
mechanism this rule lacked while it was prose. dbt compares actual against expected in Python over sorted
rows with **exact** equality — there is no float tolerance in a unit test — so fixture types must be
precisely what the model produces. The shape, in one skeleton:

```yaml
unit_tests:
  - name: ut_er_stg_input_is_a_bare_passthrough
    model: er_stg_input
    given:
      - input: source('er_input', 'person_records')
        format: sql
        rows: "select cast('a-1' as varchar) as unique_id, cast('  RoBeRt  ' as varchar) as first_name"
    expect:
      format: sql
      rows: "select cast('a-1' as varchar) as unique_id, cast('  RoBeRt  ' as varchar) as first_name"
```

The real test is `er_stg_input`'s, and it lives in `er_stg_input.yml` from Stage 2 onward with every column
present. What belongs in *this* document is the rule above and the sentence that makes the test worth
writing, which goes in the shipped test's `description:` where it will be read at failure time: *pins the
D8 rule that this model performs no transformation — if someone later adds a `lower()` or a `trim()` here,
which looks like an improvement, this fails immediately instead of the divergence surfacing much later as a
handful of wrong gamma values in a parity report.* That is D12 question 5, and it is why a
"nothing happens here" model is one of the more valuable models to unit test rather than one of the least.

> **[REVIEW 2026-08-23] Fixed (F9):** RC43 asked for exactly this trim — the six-column artifact that was
> here would have become a fork of the shipped test the moment Stage 2 landed. Reduced to a two-column
> skeleton, with the description text kept as prose and routed to its destination. RC43 is now closed.

**Given, expect, and the overrides that matter here.** Each `given:` entry names an `input:` — a `ref()`, a
`source()`, or `this` — with its own `format` and `rows`. Three uses of `overrides:` are load-bearing in
this project rather than incidental:

| Override | Used for |
|---|---|
| `macros: {is_incremental: …}` | The two Stage 8 cases: full-refresh shape at `false`, and at `true` an `input: this` fixture whose `expect:` is the **inserted rows**, not the final table state. See `DesignDoc` RC10 — Stage 8's materialization is unsettled under D11, and these tests are written against whichever way it resolves |
| `vars: {…}` | Pinning behaviour under an `er_*` policy var without a second build — e.g. the narrow versus `er_retain_matching_columns` shapes (§7, 3.53) |
| `env_vars: {…}` | `DBT_ER_MODEL_JSON`, so a test can pin behaviour for a specific model JSON without the wrapper (§9) |

**The two models whose fixtures cannot be hand-written.** `er_int_comparison_vectors` and
`er_int_scored_pairs` have a column set that is data, not source code (§9, `DesignDoc` M2). A hand-written
fixture for them is a third hand-maintained copy of the model JSON's column list, which is the failure §9
exists to prevent — so their fixtures are **generated by the same wrapper that emits `er_gamma_columns` and
`er_bf_columns`**, and §9's drift-guard pytest covers three artefacts against one model JSON: the rendered
SQL, the contracted column list, and the fixture. Choose the fixture's m/u values so every Bayes factor is
an exact power of two (§12.3) — that is what makes a scoring model unit-testable under exact equality at
all.

**What dbt does not support, and which of those bind.** Materialized views (`[n/a]` — D11 makes every model
a `table`); recursive SQL (contradicted by measurement for the pinned toolchain, above); introspective
queries (`[n/a]` — `DesignDoc` principle 2 forbids introspection independently: every column name is a pure
function of the model JSON); Python models (`[n/a]` — there are none); and models from another project.
That last one has a consumer-facing consequence worth stating in §19's terms: **a consumer cannot unit-test
our models**, so the tests they write against `er_golden_records` are data tests, and every guarantee they
get from *our* unit tests is a guarantee we ran in *our* CI.

> **[REVIEW 2026-08-23] Fixed (F35) — RC56 is answered by measurement, and the answer is "yes".** `[RUN]`
> 2026-08-23: a consuming project's `dbt build` produced **three `unit_test` rows in its
> `run_results.json`**. So **3.71's second branch applies** — the package ships the documented
> `--exclude-resource-type unit_test` guard, and `consumer_smoke/` asserts that guard rather than asserting
> zero rows. Appendix D.0 records the run. *(Caveat carried forward: measured against `integration_tests/`,
> which installs by `local: ../`; the git-ref path is 3.64's job.)*
>
> <details><summary>Original review note (RC56), retained</summary>
>
> **RC56 — Whether a consumer's `dbt build` executes this package's unit tests is
> unverified, and the two answers need different code.** dbt supports unit tests only for models in the
> current project, which settles what a consumer may *write* but not what their build *runs* against our
> `unit_tests:` blocks — and the difference is a §14.8-class hostile default: static-fixture compute in
> someone else's warehouse, on every build, for tests that can only fail for reasons they cannot fix. dbt's
> own guidance is "don't run unit tests in production". 3.71 is written with both branches (assert zero
> unit-test rows in the `consumer_smoke/` run results, or ship the documented
> `--exclude-resource-type unit_test` guard and assert that instead), but which branch is real is one
> `dbt build` in `consumer_smoke/` away. Settle it in the same job that answers 3.64, and delete the branch
> that turns out to be dead.
>
> </details>

### 12.3 Tolerance belongs in the harness, not in dbt

DesignDoc §6.1 corrects v1's tolerance pair (`1e-6` match weight with `1e-8` probability) as **mutually
inconsistent by 17× at p = 0.5**, since `dp/dmw = ln2·p(1-p) ≈ 0.1733`. The corrected policy: **one
tolerance, in match-weight space**, with the probability bound *derived* rather than independently stated.

This is a testing rule as much as a numerical one: dbt unit tests compare with exact equality and cannot
express a tolerance at all, so every float comparison lives in the pytest harness. Fixture m/u values should
be chosen so Bayes factors are exact powers of two, which keeps unit tests exact by construction.

### 12.4 Singular tests need a group

With `restrict-access: true` and staging marked `+access: private`, a singular test is its own node
belonging to no group, and is refused access to the model it tests `[VERIFIED]`:

> `Node test.dbt_er.er_stg_input_preserves_source_rows attempted to reference node model.dbt_er.er_stg_input,`
> `which is not allowed because the referenced node is private to the 'er_core' group.`

Generic tests inherit the group of the node they are attached to; singular tests must declare it:

```sql
{{ config(group='er_core') }}
```

### 12.5 Store failures as tables

```yaml
data_tests:
  dbt_er:
    +severity: error
    +store_failures_as: table
    +schema: er_test_failures
    +limit: 500
```

A failing row you can query beats a failing row that scrolled past in a log. The `+limit` caps stored rows
on pair-grain models, where a broken invariant can produce millions.

**But this block belongs in `integration_tests/dbt_project.yml`, not in the package `[UNVERIFIED]`.** It is
scoped under the `dbt_er:` key, so it applies wherever the package's tests run — creating an
`er_test_failures` schema and materialising up to 500 rows of **person data** in every consumer's warehouse,
by default, under no retention policy they know about. That is the same hostile default §14.8 refuses for
hooks, reached by a different route: *a package may not create relations a consumer did not ask for.* v1
considered the row-volume dimension (hence `+limit`) and not the consumer dimension. Move the block, or gate
it on a var defaulting false; 3.52 asserts the package's `data_tests:` sets no `store_failures_as`. See §20.4
for the retention policy where it *is* on.

### 12.6 `severity: warn` is inert under `error: all` `[VERIFIED]`

`warn_error_options: {error: all}` promotes dbt **warning events** to errors — including test warnings. A
test configured `severity: warn` therefore still fails the build. This surfaced when three
`dbt_project_evaluator` findings configured as warnings failed the run.

The implication is a policy one: under `error: all` there is no "soft" test. Either a finding is worth
failing on, or the check should be disabled explicitly with a recorded reason. There is no middle setting,
and pretending otherwise produces a build that fails for reasons nobody intended. §21 is what fills the space
where "soft" used to live, because removing the middle setting does not remove the pressure that used it.

### 12.7 The comparator sensitivity suite `[UNVERIFIED]`

**Every parity claim this project will ever make rests on comparator code that nothing proves can fail.**
`DesignDoc` M10 states it directly, and it is the single most valuable missing test in the repository.

The failure modes are cheap, plausible and green: a comparator inner-joining on
`(unique_id_l, unique_id_r)` that finds zero rows because one side used the composite id; two empty frames
because a var typo makes both sides read the baseline; a float comparator comparing `str()` forms; a set
comparator comparing `len()` before contents. Two verified facts raise the base rate above hypothetical:
`match_key` is **VARCHAR** in Splink (`blocking.py:203-206`), so a dtype-coercing comparator normalises a
real divergence away; and Splink's clustering emits **spurious NULL-node rows** on dangling edges
(`connected_components.py:89-100`), which D4 already asks the comparator to special-case — so a comparator
that drops NULL keys before diffing also hides real rows.

A comparator with a wrong join key returns "0 differences" by comparing zero rows to zero rows. Every stage
is green, `PARITY.md` ships claiming verified equivalence, and the first real divergence surfaces in
production as mis-merged entities — the most damaging error an ER system can make, because downstream
systems key on master records.

**The rule: a mutant catalogue is applied to a known-good output at every parity stage, and CI fails if any
mutant survives.** M10's catalogue, adopted as written:

| Mutant | Catches |
|---|---|
| Drop a pair · add a pair | Set comparison collapsed to a count |
| Flip a `match_key` `'1'` → `'2'` | Key ignored, or compared after coercion |
| Coerce `match_key` to INT | The VARCHAR-normalisation failure above |
| Change one gamma by ±1 | Column-wise comparison replaced by row-count |
| Shift one `match_weight` by 2× tolerance | Tolerance applied in the wrong space, or not at all |
| Swap `unique_id_l` / `unique_id_r` on one pair | Canonical ordering assumed rather than checked |
| Merge two clusters · split one · relabel one component | Partition equality degraded to cluster-count equality |
| Inject one NULL key | NULL rows silently dropped before diffing |

Each mutant asserts not just *failure* but the **expected localisation string**. A comparator that fails for
the wrong reason is still broken, and it is the harder defect to notice later because the gate looks like it
is working.

**Sequencing.** This is the one standard in the document that should be built *before* the thing it guards.
A comparator written first and mutation-tested afterwards is a comparator whose earlier green results nobody
can trust retrospectively — and Stage 0–3 baselines will have been frozen against exactly those results.
M10 sizes the suite at one day and calls it "the cheapest credibility available"; that assessment holds.

> **[REVIEW 2026-08-23] RC44 — The build-first directive points at a task with no home in the normative
> plan.** The comparator suite appears in `DesignDoc` only as A.5's Stage 0.7 — an appendix, and
> `DesignDoc`'s header rules that the body is normative and appendices are evidence — while the normative
> §5 Stage 0 list (0.1–0.5) omits it, an omission R3's enumeration of un-absorbed A.5 changes also misses.
> Until R3 closes with 0.7 included, the one standard this document says must be "built before the thing it
> guards" is scheduled nowhere a builder plans from, and Stage 0.4 freezes baselines against an untested
> comparator by default. Name the `DesignDoc` anchor here (A.5 Stage 0.7) so the sequencing claim survives
> the reconciliation.

---

## 13. Determinism

Two different properties get confused constantly, and only one of them requires sorting.

| Property | Meaning | How it is achieved |
|---|---|---|
| **Tie-break determinism** | When a rule must choose among equals, it always chooses the same one | Total ordering in `ARG_MAX` / window `ORDER BY`, ending in `unique_id` |
| **Output stability** | Two runs on identical input produce the same *content* | Achieved by construction; **asserted** by hashing the sorted relation |

**Tie-break ordering is mandatory. Statement-level `ORDER BY` is banned.**

DesignDoc M15 measured a total `ORDER BY` in a CTAS at **4.2× (7.18s against 1.69s on 5.5M rows)** and
proved it unnecessary — clustering output hashed identically *unordered* across six runs. Sorting on write
buys nothing because stability is asserted by sorting on *read*.

The bouncer regex targets only a trailing statement-level `ORDER BY`; window and `ARG_MAX` clauses are
required and unaffected:

```yaml
- name: check_model_code_does_not_contain_regexp_pattern
  regexp_pattern: (?is).*\n\s*order\s+by[^)]*$
  description: >-
    No statement-level ORDER BY in a model body. Sorting on write costs 4.2x and
    buys nothing; determinism is asserted by hashing the sorted relation.
```

### 13.1 "Byte-identical" is the wrong assertion

Per DesignDoc §6.3, the correct claim is a **content hash after canonical ordering, excluding named volatile
columns**. Specifically:

- sort by each model's **declared primary key** before hashing;
- exclude volatile columns (`er_run_id`, load timestamps) **by name**, not by position;
- hash DOUBLEs as their **8 IEEE-754 bytes**, never as text — `printf('%.17g')` and DuckDB's
  shortest-round-trip default disagree on the same double;
- include the column name + type list in the digest, so a type change is a hash change.

Four tempting alternatives are wrong, and each fails in a way this project would actually hit:

| Approach | Why it fails |
|---|---|
| Comparing `.duckdb` file bytes | Page-structured; layout depends on insert order and free-list state |
| `bit_xor(md5_number(...))` | **NULL-blind and duplicate-cancelling** — two identical rows XOR to zero, and an extra all-NULL row hashes identically. Both are exactly the bugs an ER pipeline produces |
| `to_json()` / `md5(string_agg(...))` | Text representations of doubles disagree (see above) |
| Diffing `run_results.json` | Contains `execution_time`, `invocation_id`, `generated_at` — inherently non-deterministic. It is the *input* to the perf table, not a determinism proof |

### 13.2 The gate runs at `threads = 8`, never 1

DesignDoc M15: a single-threaded check also passed a **wrong `USING KEY` formulation** — deterministic and
incorrect. Single-threading hides exactly the class of bug the gate exists to find.

The corollary is that **float aggregates must never be hashed**: at `threads=8`, 3M DOUBLEs produced five
distinct `sum()` results and six distinct `avg()` results across six repeat queries. Parallel summation
order is not stable, and it does not need to be — hash sorted *rows*, not aggregates.

`preserve_insertion_order` is left at its default (`true`) on the `ci` target. Setting it `false` lets
DuckDB reorder rows lacking an `ORDER BY`, which would void the whole guarantee; it is confined to the
`bench` target, where determinism explicitly does not apply.

### 13.3 Determinism has a platform, and a process environment

Two preconditions this section assumed and did not state. Both are now §22: the **platform contract** (which
gates are float-exact and therefore anchored to `linux/amd64`, and which are architecture-independent), and
**environment pinning** (`PYTHONHASHSEED`, `TZ`, `LC_ALL`). The second is directly load-bearing here —
`LC_ALL` changes string collation, and §13.1's canonical ordering sorts by VARCHAR primary keys.

---

## 14. Observability

The requirement is "log metrics to a table so we know where the pain points are". DesignDoc **M7**
independently asks for the same thing from the other direction — a run contract with provenance — noting
that `grep -ic` returns **0** for `run_id`, `resume`, `idempot`, `retry`, `partial`, `rollback`, `observab`
and `backfill` across the design. So this layer is not an add-on; it closes §6.4's Performance row and §7's
runtime-anchoring requirement.

### 14.1 Why not an off-the-shelf package `[VERIFIED]`

- **dbt_artifacts** does **not support DuckDB** — its adapter list is Databricks/Spark/Snowflake/BigQuery/
  Postgres/SQL Server/Trino, and the DuckDB pull request has been open and merge-conflicted since
  2025-12-11.
- **Elementary** does support DuckDB but hard-wires its own `on-run-start`/`on-run-end` hooks into its
  `dbt_project.yml`, which every consumer of this package would then inherit.

So: hand-rolled, ~200 lines of macro, zero added package surface.

### 14.2 Two tiers, because one is not enough

**Tier 1 — `model_execution_log`**, one row per node per invocation, from the `results` object dbt passes
to `on-run-end`. Answers *which model is slow*. Records status, total time, **compile and execute phases
separately** (which distinguishes a model slow to *render* from one slow to *run* — a macro-heavy project
can be either), materialization, tags, parent count, plus run identity: dbt and DuckDB versions, target,
both thread counts, git SHA, CI run id and the model-JSON hash.

**Tier 2 — `query_metrics_log`**, drained from DuckDB's own metrics log. Answers *where inside the model the
time went*: latency, CPU time, blocked-thread time, rows scanned, bytes read/written, peak buffer memory,
peak temp-directory size. None of this is in `run_results.json`, because it happens inside the statement.

Both live in a **separate DuckDB file attached under the `obs` alias**, not in the warehouse dbt builds
into. `dbt clean` and `--full-refresh` destroy the warehouse, and a performance history that disappears on
a full refresh cannot answer the only question it exists to answer.

### 14.3 Enabling the engine log `[VERIFIED]`

Two different mechanisms, and conflating them is why this usually fails:

- **`enable_profiling`** is a *setting*, **LOCAL** scope → belongs in `profiles.yml` `settings:`, which
  dbt-duckdb re-issues as `SET key = 'value'` on every cursor, so it reaches all threads.
- **`enable_logging`** is a *table function*, **GLOBAL** scope → **cannot** go in `settings:`; it is called
  once from `on-run-start`.

Both are required. Verified on DuckDB 1.5.5: `enable_logging` alone yields **zero** metric rows, and
`enable_profiling = 'no_output'` — which writes no profile files at all — **still populates the log**. The
21 metrics observed include `LATENCY`, `CPU_TIME`, `BLOCKED_THREAD_TIME`, `ROWS_RETURNED`,
`CUMULATIVE_ROWS_SCANNED`, `TOTAL_BYTES_READ/WRITTEN`, `SYSTEM_PEAK_BUFFER_MEMORY`,
`SYSTEM_PEAK_TEMP_DIR_SIZE` and `QUERY_NAME`.

Per-model `profiling_output` files are **not** used: DuckDB overwrites the file on every subsequent query,
and the setting is LOCAL on a shared database instance, so concurrent statements clobber each other.

```sql
{% macro er_obs_on_run_start() %}                                    {# [VERIFIED] #}
  {% if not dbt_er.er_obs_enabled() %}{% do return('') %}{% endif %}
  {% do dbt_er.er_obs_bootstrap() %}
  {# Truncate first so this run's metrics cannot include the previous run's;
     default log storage is memory, so the drain must happen in-invocation. #}
  {% do run_query("call truncate_duckdb_logs()") %}
  {% do run_query("call enable_logging(['Metrics', 'QueryLog'])") %}
  {% do return('') %}
{% endmacro %}
```

`QueryLog` is enabled alongside `Metrics` because it carries the raw SQL keyed by the same `query_id`, and
being a log type rather than a metric name it survives the metric renaming planned for DuckDB 2.0.

### 14.4 Joining engine metrics back to dbt nodes

dbt stamps a query comment containing `{"node_id": "model.dbt_er.x"}` onto every statement, and
`QUERY_NAME` captures the full SQL text including that comment. Extracting it is what links the two tiers:

```sql
nullif(regexp_extract(pivoted.query_sql, '"node_id"\s*:\s*"([^"]+)"', 1), '') as node_id
```

**Aggregate before joining.** dbt issues several statements per node — the CTAS plus schema creation plus
the `COMMENT` statements from `persist_docs` — and every one carries the same `node_id`. Joining the raw log
directly fans each model into one row per statement, silently multiplying any downstream sum and making
"slowest models" wrong. This was observed: `er_stg_input` appeared three times before the fix `[VERIFIED]`.

```sql
with per_node_metrics as (
    select
        invocation_id, node_id,
        count(*) as n_statements,
        -- Summed: the node's total engine cost across its statements.
        sum(latency_s) as latency_s,
        sum(cpu_time_s) as cpu_time_s,
        sum(cumulative_rows_scanned) as cumulative_rows_scanned,
        -- Peaks are high-water marks, so they are maxed, never summed.
        max(peak_buffer_memory_bytes) as peak_buffer_memory_bytes,
        max(peak_temp_dir_bytes) as peak_temp_dir_bytes
    from "er_meta"."query_metrics_log"
    where node_id is not null
    group by invocation_id, node_id
)
```

### 14.5 Views must not bake in the ATTACH alias `[VERIFIED]`

A view whose body names the `obs` catalog resolves **only while that alias is attached**. Open the metrics
file directly — which is exactly what a human debugging a slow build does, and what a CI artifact download
gives you — and every view fails with `Catalog "obs" does not exist`.

Create views with schema-qualified but **not** catalog-qualified bodies. Relative names resolve against the
view's own catalog either way.

### 14.6 What the tables answer

`model_hotspots`, from a real run:

```
node_name                  total_s  n_stmts  latency_s  peak_buffer_bytes  pct_of_run
stg_nodes                    6.600        2     0.0031           5074944       27.07
er_stg_input                 1.469        3     0.1404           6750208        6.02
fct_direct_join_to_source    1.196        2     0.2028          37695744        4.90
```

`model_perf_trend` adds `prev_execution_time_s`, `delta_s` and a **trailing 7-run median** per node, which
is the number a regression gate should compare against — a single previous run is too noisy on shared CI.

**That median cannot exist on the CI topology as drawn `[UNVERIFIED]`.** §14.2 justifies a separate
observability database so history survives `dbt clean` and `--full-refresh` — a real hazard, correctly
identified. But §15 runs on ephemeral runners and Appendix C.7 only *uploads* the file as a 14-day artefact.
There is no download-and-restore step, so every CI run starts with an empty database: the trend table holds
one run, the 7-run median is always a 1-run median, and Appendix B.5's plan to calibrate 3.28's thresholds
from it has no data source. The failure is silent in the way this document keeps warning about — the views
work, the queries return rows, and the number returned is not what its name says.

Three sections depend on this (§14.6, 3.28, Appendix B.5), so pick a persistence mechanism and write it down:
restore-then-append the prior artefact at job start (simplest, bounded by retention); commit a trend summary
to a branch; or push to external storage. Then reword this paragraph to state which runs the median actually
covers. Until it is decided, **3.28's thresholds are absolute-only** — which is currently true and was not
said. Note the consequence for §15's permissions: anything beyond artefact round-tripping needs write access,
which interacts with 3.56.

### 14.7 Two honest limitations

1. **`rows_affected` is always NULL on dbt-duckdb.** `DuckDBConnectionManager.get_response()` returns a bare
   `AdapterResponse("OK")` and never sets a row count. The column is kept so it means the same thing here as
   on adapters that populate it.
2. **`ROWS_RETURNED` is 1 for a CTAS** — DuckDB reports the count row, not rows written. Use
   `cumulative_rows_scanned` for volume, or count explicitly when an exact figure matters.

Neither is worked around silently. A metric that quietly means something other than its name is worse than
a missing one.

### 14.8 The package ships this switched off

A package's `on-run-end` hook fires in **every consumer's project**, and dbt-core **#10592** — a way to
disable package hooks — is still open. So:

- the macros ship with the package, but `er_obs_enabled` defaults to **false**;
- the hooks are declared **only** in `integration_tests/dbt_project.yml`;
- every macro additionally refuses to act unless the var is explicitly true.

Shipping hooks that fire in someone else's project without an off switch is a hostile default. **That
principle generalises, and v1 applied it here and nowhere else:** §2.1 now applies it to the `on-run-start`
gate, which can hard-fail rather than merely write, and §12.5 applies it to `store_failures_as`. State it
once, as a rule: *a package may not create relations, fire hooks, or fail builds in a consumer's project
without a documented off switch.*

### 14.9 The run contract `[UNVERIFIED]`

§14's two tiers answer *which model is slow*. Neither answers *which run produced this table, from which
inputs, and is it complete* — and `DesignDoc` **M7** records that `grep -ic` returns **0** across the design
for `run_id`, `resume`, `idempot`, `retry`, `partial`, `rollback` and `backfill`. dbt has no transaction
spanning a DAG, so a failure at Stage 6 leaves stages 0–5 at run N and the marts at run N−1, with nothing
recording that this happened.

The exposure is measured, not theoretical: `[RECON]` 21.1 s and 5.3 GB for stages 3–5 at 1M records, and
523.3 s for a 20k-node chain — both long enough to be interrupted by a pod eviction.

| # | Requirement |
|---|---|
| a | **`er_run_id`** (ULID) stamped as a column on every materialised model |
| b | **`_er_run_manifest`** carrying run id, `sha256(model JSON)`, `er_tf_snapshot_id`, threshold, resolved dbt / dbt-duckdb / duckdb / splink / **sqlglot** versions, the **platform triple**, per-stage row counts and wall time |
| c | An explicit, **written** per-model idempotency key — what makes whole-stage restart safe |
| d | A five-value **exit-code taxonomy**: parity failed / precondition failed / infra failed / nothing to do / success. `DesignDoc` Stage 11 and M12 both branch on it |
| e | Named owners for `PARITY.md` and `divergence-log.md` (§5's `CODEOWNERS`) |

(b) also closes `DesignDoc` §6.4's Performance row and §7 Q1's requirement to anchor our runtime against
Splink's, both of which presuppose storing both engines' timings and neither of which had an owner.

**The pairing that must not be split.** `er_run_id` adds a column to every contract and **must** be added to
§13.1's volatile-column exclusion list in the same commit — otherwise every determinism hash changes on every
run and the gate that proves output stability starts proving the opposite. Appendix B.4 recommended adopting
`er_run_id` and said this; the gap was that nothing enforced the pairing. **3.48** does: the policy macro
asserts every run-contract column also appears in `er_volatile_columns`.

The manifest lives in the `obs` database, reusing §14.2's separate-file argument, which applies here with
more force — a provenance record that disappears on `--full-refresh` is worse than none, because its absence
is indistinguishable from a run that never happened.

### 14.10 The observability layer is a data-egress path `[UNVERIFIED]`

§14.3 enables `QueryLog` deliberately, because it *"carries the raw SQL keyed by the same `query_id`."* Raw
SQL text can embed literal attribute values — from seeds, from the `format: sql` unit-test fixtures §12.2
*mandates* (values inline, by construction), from any filter written while debugging. Appendix C.7 then
uploads that database as a **CI artefact with 14-day retention**, downloadable by anyone with repository read
access.

This is not an argument against the observability layer, which is a good design. It is an argument that it
needs a stated position, because it is the most likely path by which person data leaves the build boundary:
either strip or hash `query_sql` before writing it, or classify the artefact and shorten retention. §20.4
decides; the point here is that §14.7's own standard applies — *"a metric that quietly means something other
than its name is worse than a missing one"* — and an artefact labelled "performance metrics" that also
carries attribute values is exactly that.

---

## 15. CI topology `[UNVERIFIED]`

Everything in this section is designed but was never executed. Validate on first run.

| Job | Gate | Runs |
|---|---|---|
| `lint` | SQLFluff, yamllint, ruff, mypy, the enforcement scripts, `dbt parse` | Every PR |
| `build` | `dbt seed` → `dbt build --empty` (contract smoke) → full `dbt build` → `dbt docs generate` | Every PR |
| `bouncer` | All three artifact tiers, **plus the registration assertion** (3.40) | Every PR, after `build` |
| `parity` | pytest harness against frozen Splink baselines | Every PR |
| **`comparator-sensitivity`** | The mutant catalogue (§12.7). No mutant may survive | Every PR |
| `determinism` | Two builds + one row-permuted build, content hashes compared, `threads=8` | Every PR |
| `project-evaluator` | dbt_project_evaluator with `severity=error` | Every PR |
| **`verify-gates`** | Each §3 standard injected and observed to **fail** (3.38) | Every PR |
| **`python-tests`** | pytest over `scripts/` and `dbt_bouncer_checks/`, coverage floor (3.57) | Every PR |
| **`consumer-smoke`** | Install by git ref into `consumer_smoke/`, `dbt deps` + `build` + `docs generate` (3.64) | Every PR |
| `ci-gate` | Aggregates all of the above | Every PR |
| `nightly` | Seeded differential loop, perf trending, upstream canaries | Nightly |

Five jobs are new in v2. `verify-gates` and `comparator-sensitivity` are both answers to the same question —
*does this gate work?* — asked of the §3 matrix and of the parity harness respectively, and neither existed.
`consumer-smoke` is the only place the **published** install path runs: `integration_tests/` uses
`local: ../`, which exercises no git or hub install, no `package-lock.yml` resolution from a downstream
project's perspective, no transitive `dbt_utils` conflict against a consumer that already pins it, and does
not notice a file missing from the published artefact. §2 names the consumer as the threat model and §17's
workflow is entirely internal; until this job exists, nothing in the repository ever plays the consumer's
role — including §2.1's hardening, which is otherwise untested.

**`ci-gate` is the single required status check.** It `needs:` every other job and fails if any reports
`failure` or `cancelled`. Adding a job then never requires editing branch-protection rules — a small thing
that otherwise silently degrades: a new job that is not a required check is a job whose failure nobody
blocks on.

**Ordering constraints that are not obvious:**

- **`dbt seed` must precede `dbt build`.** The package reads a *source*, and a source creates no DAG edge,
  so nothing orders the fixture's creation before the model that reads it `[VERIFIED]`.
- **The parity harness must run after dbt has exited, and reads only parquet** (B.1, resolved). DuckDB
  takes a **process-level lock**; a second process connecting to the same file fails with `Conflicting lock
  is held`, and read-only does not help (DesignDoc M17). Harness and dbt cannot be siblings. The harness
  therefore **never opens the database at all**: `integration_tests/` exports every compared model to
  parquet with a `COPY` post-hook, and the harness reads those files and Stage 0.3's parquet baselines.
  Both sides of every comparator are parquet, which is what B.1's option (a) was really buying.
  The `parity`, `determinism` and `comparator-sensitivity` jobs therefore `needs: build` and consume its
  artefact rather than re-running dbt.
- **`dbt docs generate` must precede the catalog checks**, which need `catalog.json` built against a real
  database.

**Slim CI is banned in parity jobs.** `state:modified` cannot see `--vars`: `same_body` compares unrendered
`raw_code` and `same_config` compares `unrendered_config`, so a **changed model JSON looks unmodified**, the
models are skipped, and CI reports green with stale scores (DesignDoc §6.2). CI asserts no `--defer` or
`state:` appears in those commands. Slim CI also buys nothing here — the warehouse is a file the runner
creates and destroys.

**Do not cache `target/` or the `.duckdb` file.** A stale database or partial-parse cache is the most likely
source of a false-green parity run, and rebuilding the fixture costs seconds.

**Pin the runner image** (`ubuntu-24.04`, not `ubuntu-latest`). A floating label silently invalidates
performance trending the day the image moves.

**Pin the actions the same way, and for a stronger reason `[UNVERIFIED]`.** Appendix C.7 pins the runner
image by exact version and then pins third-party actions by **mutable tag** — `actions/checkout@v5`,
`astral-sh/setup-uv@v6`, `actions/upload-artifact@v7`. The floating-label argument applies with more force to
executable third-party code than to a base image, and it comes with a compromise vector the image does not
have. Pin by 40-character commit SHA with a version comment; 3.56 asserts it.

Three more, absent from v1 and none of them exotic:

- **`persist-credentials: false` on checkout.** The default leaves a usable token in `.git/config` for every
  subsequent step, including third-party actions.
- **Per-job `permissions:`.** The workflow-level `contents: read` is right for today's jobs; the nightly job
  will need more the moment §14.6's trend persistence is resolved, and that should be granted per job rather
  than raised globally.
- **A stated position on `pull_request_target`** — recommend: never. It is the standard way this class of
  workflow is compromised, and this repository has a specific reason to care. Appendix C.7 injects the model
  JSON into `GITHUB_ENV` through a heredoc with a fixed `__EOF__` sentinel. That is safe today because the
  JSON is a repository-controlled fixture; it becomes an environment-injection vector the moment the JSON is
  supplied by a fork or a workflow input — which is exactly what a "test my model" workflow would do. Note
  the constraint beside the step, so a future change does not cross it silently.

Add a toolchain vulnerability scan to `lint` while there. `uv sync --locked` guarantees reproducibility, not
that what is reproduced is safe.

---

## 16. Dependency and version policy

**Exact pins for anything parity-critical.** `dbt-core`, `dbt-duckdb`, `duckdb`, `splink` are pinned to an
exact version in `uv.lock` and placed on Dependabot's **ignore** list. A bot bumping any of them silently
invalidates every frozen Splink baseline and produces a false green. Those upgrades are human PRs that
regenerate baselines and attach the diff report.

**Grouped and automated:** ruff, pytest, pre-commit, mypy, yamllint — minor and patch only.

**The DuckDB bump ritual.** The pin is uncomfortable and the discomfort is real: DuckDB 1.5.x reaches EOL
2026-09-01 and 1.4.0 LTS on 2026-09-16, so *every* current option is near end of life. 1.5.5 is what
DesignDoc's `[RUN]`/`[RECON]` measurements used — changing it invalidates Appendix A. Worse, **DuckDB
PR #24647 redefines `UNION` semantics under `USING KEY` and removes `deprecated_using_key_syntax`**, so a
bump is never routine. Every DuckDB upgrade therefore runs:

1. the D4 clustering correctness gate, **blocking**;
2. the full parity suite against regenerated baselines;
3. the scale benchmark, with results recorded in the perf trend table;
4. a divergence-log entry if any tolerance moves;
5. **re-verification of every `[VERIFIED]` marker scoped to the moved pin**, and re-marking of any that were
   not re-executed `[UNVERIFIED]`.

**Step 5 is new in v2 and applies to every pin, not only DuckDB `[UNVERIFIED]`.** v1's ritual regenerated
Splink baselines and re-ran the D4 gate; it never re-ran the *configuration* verification that produced the
markers. And v1 had no bump ritual at all for dbt-core, dbt-bouncer or SQLFluff beyond "grouped and
automated, minor and patch only" — while dbt-bouncer 3.8.0 → 3.9.0 is a minor bump that could change any of
the four bouncer behaviours Appendix A.1 documents, including the `*/*.py` loader glob and the `@check`
decorator shape. Combined with 3.40's registration assertion, the failure mode is a check that no longer
loads, in a document that still says `[VERIFIED]`. 3.44 mechanises the demotion by comparing §4's pins
against `uv.lock`.

**Lockfile freshness is asserted, not assumed:** `uv lock --check` in CI, and `dbt deps --lock` followed by
`git diff --exit-code` on `package-lock.yml`.

---

## 17. Contributor workflow

```bash
make install     # uv sync --locked + pre-commit install
make lint        # sqlfluff + yamllint + ruff + mypy + the four repo checks
make build       # dbt seed && dbt build --full-refresh (unit + data tests)
make docs        # catalog.json for the bouncer catalog tier
make bouncer     # all three artifact tiers
make ci          # everything CI runs, in CI's order
```

**Every Make target is also a CI step.** If it passes locally it passes in CI, and a gate that exists only
in CI does not exist — nobody can run it before pushing, so it is discovered at the worst moment.

The PR template carries the human gates that cannot be mechanised: a Splink source permalink for any
parity-affecting change, a divergence-log entry for any deliberate difference from the oracle, and — added
in v2.2 — for any PR that adds or changes a model, **D12's six questions answered in that model's
properties file** (3.70). All three are labelled convention (unenforced) in §3, because a checkbox is not a
gate. The third is the one a reviewer can actually check cheaply: 3.20 proves a unit test exists, and only
a human reading the model's `CASE` arms against its `unit_tests:` block can tell whether the test that
exists is the test the model needed.

---

## 18. Waivers

Every rule here can be broken. There is exactly one legal way to do it.

1. **Record the reason in `config.meta`**, in a key the tooling already knows:
   `materialisation_waiver_reason`, `primary_key_by_test_reason`, `sql_features`. The policy macro *requires*
   these for the exceptions it permits — a custom-materialization model with no
   `materialisation_waiver_reason` fails at compile time (§7.2).
2. **Scope the exemption in one place.** The bouncer `exclude:` regex, the `.sqlfluffignore` entry, or the
   `er_standards_exempt` mapping. Never a scattered `-- noqa`.
3. **Cap it where a cap makes sense.** `.sqlfluffignore` is asserted to hold ≤ 2 model entries.
4. **Prefer a var over an edit.** The `er_*` policy vars exist so that relaxing a standard is a visible,
   reviewable diff in `dbt_project.yml` rather than a quiet change to a check. Note the limit §2.1 puts on
   this: *hardening* values are deliberately not vars, because a knob a consumer can reach is a knob that
   disarms the gate defending against that consumer.
5. **Make it visible on every run.** The macro echoes the active exemption list in its success message. An
   exemption nobody sees is the problem; an exemption printed on every build is a standing invitation to
   remove it.

A waiver that is greppable is a waiver someone can revisit. A waiver expressed by deleting a check is not.

### 18.1 The waiver that escaped the waiver policy `[UNVERIFIED]`

v1's `er_standards_exempt_models` met rule 2 and none of the others. It had **no cap**, required **no
reason**, and disabled **every** check in `er_assert_project_standards` for a named model in a single edit —
1:1 pairing, materialization policy, contract enforcement, primary keys, the `foreign_key` ban, all six
description sections, column data types, column descriptions and unit-test coverage — because Appendix C.4
applies it at the top of the model loop, short-circuiting the whole body.

Contrast the two waivers beside it. §3.17 caps `.sqlfluffignore` at two entries, with the reasoning that
*"an ignore file with no ceiling becomes the place failures go to be forgotten."* §7.2's waiver is refused
without a recorded reason. The blanket model exemption was held to neither standard, while being the most
powerful of the three — and it is a `var()`, so per §2.1 a consumer could set it too.

**The corrected shape**, enforced by 3.43:

```
er_standards_exempt:
  er_entity_clusters:
    checks: [unit_test]
    reason: "<toolchain regression, named>; harness covers it meanwhile. Expires <date>."
```

Per-check rather than per-model, reasoned, capped, printed on every run, and a hardening value rather than a
policy var. Six months later this distinguishes *"exempt because dbt 1.13.0 regressed the unit wrapper, and
here is the date that claim expires"* from *"exempt because it was Friday"*, which v1's bare name list could
not. A unit-test waiver additionally gets a dated `docs/quarantine.md` entry, so 3.63 fails the build when
the date passes — otherwise "expires" is a comment.

> **[REVIEW 2026-08-23] Fixed (F10):** this example previously read
> `reason: "Recursive USING KEY; covered by the pytest harness instead (12.2)."` — which `DesignDoc` **D12**
> makes an illegal reason, since M17(c) measured recursive `USING KEY` models unit-testing correctly on the
> pinned toolchain and 3.20 now has no automatic exemption class. Replaced with the shape a legal unit-test
> waiver takes: a **named** toolchain regression and an **expiry**. It is illustrative only — **no
> unit-test waiver is in force**, and D12 removed the only one this document ever named. The irony is worth
> keeping: the section explaining what a disciplined waiver looks like carried, as its worked example, the
> one waiver in the document that had stopped being true.

---

## 19. Release and compatibility `[UNVERIFIED]`

§16 is thorough about **incoming** dependencies and silent about **outgoing** ones. This section is the
symmetric half, and it is the most consumer-visible surface the project has.

### 19.1 The public API surface

§3.30 defines what is **private** — `restrict-access: true` plus `+access: private` plus `+group:`. Nothing
defined what is **public**, and a breaking-change policy with no enumerated surface has no subject.

Note what Appendix C.1 currently implies: `+access: private` is set at the top of the `dbt_er:` block, so
**every** model is private unless individually overridden, and the public surface is strictly empty. That is
almost certainly not the intent, and it is the kind of thing discovered by the first consumer rather than by
us.

The surface is enumerated here and asserted by 3.61: public models (`er_entity_clusters`,
`er_golden_records`, `er_cluster_membership` on current evidence), consumer-settable vars, callable macros,
and required environment variables (`DBT_ER_MODEL_JSON` is already one). Everything not listed is internal
and may change without a MAJOR bump.

### 19.2 What "breaking" means for a dbt package

Not the same as for a library, and each of these breaks a consumer at `ref()` or contract time without being
obvious from a diff:

| Change | Why it breaks |
|---|---|
| A contracted column added, removed or retyped | Downstream contracts and `select *` into a contracted table fail |
| A `data_type` widened | §8.1 already calls silent widening a parity defect; it is equally a consumer break |
| A public model renamed | Their `ref()` fails |
| `+access` or `+group` changed | An existing `ref()` starts failing under `restrict-access` |
| A var renamed, or its default changed | Silent behaviour change, which is worse than a failure |
| `+schema` changed | Their relations move |
| `require-dbt-version` narrowed | Resolution fails on upgrade |
| A new **required** env var | Build fails with a message pointing at our internals |

SemVer, with that table as the MAJOR triggers. `CHANGELOG.md` in Keep-a-Changelog form. Git tags matching
`dbt_project.yml`'s `version`. And **3.60**, which is what makes this policy more than a promise: compare
contracted column sets in the manifest against the previous release tag and fail if a MAJOR-triggering change
ships without a MAJOR bump. That check is mechanisable today, before there is anything to release.

### 19.3 The published install path

`integration_tests/` uses `local: ../`, which is the correct dbt package convention and exercises none of the
real install path. The `consumer-smoke` job (§15, 3.64) is the fix. It should also assert that §2.1's
compile gate fires there, because that is the only way the hardening and the escape hatch are ever tested.

### 19.4 The human gates

Unchanged from §17, restated here because they belong to releases: a Splink source permalink on any
parity-affecting PR (3.36); a divergence-log entry for any deliberate difference from the oracle — no
longer a checkbox, since 3.49 now enforces the log-to-test correspondence in both directions; and, for any
PR touching a model, D12's unit-test questions answered in that model's properties file (3.70). The third
belongs in a *release* section for a reason a reviewer of one PR does not see: under §19.1 these models are
the published surface, and a model whose unit tests were written to satisfy 3.20 rather than to pin its
behaviour is a MAJOR-triggering change waiting to look like a patch.

---

## 20. Fixtures, baselines and test data `[UNVERIFIED]`

§5 lists `fixtures/  # model JSONs, Splink baselines` and §16 says a DuckDB bump "regenerates baselines and
attaches the diff report." That was the whole policy, and it names an artefact with no defined format.

### 20.1 Baseline lifecycle

Storage is parquet, per `DesignDoc` Stage 0.3 — and note this interacts with Appendix B.1's open decision,
since option (a) makes both sides of every comparator parquet. Every baseline carries a sidecar
`*.manifest.yml` with Splink version, model-JSON sha256, generator seed, DuckDB version, **sqlglot
version**, **the platform triple** `(os, architecture, DuckDB build)`, date and producing commit; 3.62
asserts it. Regeneration happens only through a `make` target that writes that manifest, never by hand.

The last two fields are not bookkeeping. **sqlglot**, not Splink, decides which comparison levels receive a
TF adjustment (`DesignDoc` A.2 C2, G11), and it arrives transitively — a baseline generated under a
different sqlglot is a different baseline with no visible difference. **The platform triple** exists because
`DesignDoc` Appendix A measured "exact bit equality" in-process on darwin arm64 while these baselines are
compared on linux/amd64 in CI (G5, §22.1); a manifest that cannot say which platform produced a baseline
cannot support the parity claim built on it.

**The reviewable artefact is a human-readable diff report, not the parquet.** Row counts, changed-cell counts
by column, min/max deltas, worst-N rows. A PR showing twelve changed binary files gets approved on the
strength of a green CI run — which is circular, because CI compares against the new baselines. A regression
baselined as correct is then confirmed by every subsequent parity run, and the parquet is evidence rather
than a review surface.

A baseline regeneration PR contains **only** baseline changes. And Appendix C.6's
`check-added-large-files --maxkb=4096` needs tightening: 4 MB of binary per file, accepted into git history
permanently, is not a limit anyone chose.

### 20.2 Adopt the incumbent's fixtures

`DesignDoc` A.3 Group 3 identifies `fixtures/static/base_10` — 23 records, 10 personas, 18 true pairs, 8
designed traps, machine-checked ground truth — as *"a better adversarial suite than three synthetic seeds"*,
along with a working Splink blocking oracle and comparison helpers. Adopting them is a standards decision
that previously had nowhere to be recorded. Record it here.

### 20.3 `PARITY.md`

Required by `DesignDoc` DoD 5, referenced eleven times there, and absent from every layout, matrix and job
list in v1 of this document. It states, with evidence links, exactly what is identical and what is bounded,
using the §6.1 tolerance policy. Per stage: gate, tolerance, evidence link, known divergences, measured
ceiling. 3.50 asserts it names every stage the DAG contains.

Two things must land in it specifically: **D11's single-node ceiling** — "published, not discovered in
production" — and `DesignDoc` §6.1's **boundary fixture**, the pair minimising `|p − t|` per threshold with
both engines' inclusion agreement and the blast radius (`|component_a| + |component_b|`) emitted as a CI
artefact, so the exposure stays visible even when green.

### 20.4 Person data

This is entity resolution over person records — §12.2's own fixture is `first_name`, `surname`, `dob`,
`city`, `email` — and neither document mentioned PII, personal data or data classification. Three exposures,
in increasing order of how surprising they are:

1. **Fixtures and seeds.** The natural way to debug a parity failure is to reproduce it on the data that
   failed, which is real. One `git add` later it is in history permanently. **Rule: fixtures and seeds are
   synthetic only.** Mechanism: 3.55 — `detect-private-key` plus a PII heuristic scan over `seeds/`,
   `fixtures/` and `harness/`.
2. **Test-failure tables.** §12.5 materialises up to 500 failing rows — real attribute values, by
   construction — into a persistent schema. Moving that config out of the package (3.52) removes the
   consumer-warehouse case; where it *is* on, it carries a retention policy.
3. **The observability database**, per §14.10. Decide: strip or hash `query_sql` before writing, or classify
   the artefact and shorten retention below 14 days. Either is fine; leaving it undecided is not, because the
   artefact is labelled "performance metrics" and would also carry attribute values.

---

## 21. When a gate fails `[UNVERIFIED]`

§11.3 names the pathology exactly — one `set()` in a macro makes a snapshot test fail ~80% of the time, which
*"presents as a flaky test rather than a defect and gets triaged as 'CI is flaky' for weeks"* — and then v1
had no policy for what to do about it.

The gap is sharpened by §12.6's own finding. Under `error: all` there is **no soft failure**: a gate is
blocking or it is deleted. So when one flakes, the available responses are "fix it now" or "remove it," with
nothing in between, and the pressure at 5pm on a release day is predictable. Removing the middle setting did
not remove the demand for one; it removed the *recorded* one.

| Rule | Reason |
|---|---|
| **No gate is retried automatically.** No `pytest-rerunfailures`, no workflow `retry:` on parity or determinism jobs | A retry converts a real non-determinism finding into a coin flip — the exact defect §11.3 exists to catch. This is the load-bearing rule |
| A flaking gate is **quarantined**, in `docs/quarantine.md`, with an owner, a date, a reason and an expiry | Not deleted, and not set `severity: warn` — §12.6 proves that is inert anyway |
| CI fails on an **expired** quarantine entry (3.63) | An expiry nobody enforces is a comment |
| A flake in a **determinism or parity** gate is a product defect until proven otherwise | In this project that is the base rate, not a pessimistic default. §11.3's whole argument is that the symptom of a real non-determinism bug is indistinguishable from CI noise |

The quarantine file is the middle setting, made explicit and time-boxed. It is where "soft" lives now.

---

## 22. The platform contract `[UNVERIFIED]`

### 22.1 Architecture

§13, 3.25 and `DesignDoc` §6.1's **exact bit equality** default are all anchored to one platform:
`ubuntu-24.04`, x86-64. §6.1's justification — *"both engines run float8 on the same DuckDB"* — is a claim
about the DuckDB *version*, not about the instruction set, the libm, or the FMA-contraction decisions the
compiler made for that target. Development on this project happens on arm64.

The exposure is specific rather than general. §3.1's arithmetic is `log2` and `exp`-family calls over float8,
and transcendental results are **not** guaranteed identical across libm implementations. A one-ULP difference
in `log2` is exactly the input to the boundary-flip scenario `DesignDoc` §6.1 analyses at length, where a pair
within 1e-9 of a threshold flips edge membership and connected components turns one flipped edge into a
whole-component merge.

| Gate class | Platform dependence |
|---|---|
| Set equality, partition equality, integer and string comparisons, row counts | **Independent.** Run anywhere |
| Exact float bit equality, content hashes over DOUBLE columns, the 1e-9 tolerance | **Anchored to `linux/amd64`** (3.59) |

`make ci` on another architecture is **advisory** for the anchored subset, and should say so rather than
appearing to pass. Give it a container path so a developer can get an authoritative answer locally, because
§17's promise — *"if it passes locally it passes in CI"* — is otherwise one this document has not established
it can keep. If float parity across architectures is ever needed, that is a much larger decision and should
be made deliberately rather than discovered by an unexplained hash mismatch.

### 22.2 Process environment

§11.3 bans `set()` because iteration order over strings is randomised *"when `PYTHONHASHSEED` is unset"* —
and nothing set or asserted it. The lint is the right primary defence; a pinned seed is the free second one,
and without it the document relies entirely on a regex catching every path to that behaviour, including paths
inside installed packages the regex cannot see.

Pinned in the `Makefile` and the workflow env, and asserted in the determinism job (3.58):

| Variable | Value | Why |
|---|---|---|
| `PYTHONHASHSEED` | `0` | Defence in depth behind §11.3's Tier 1 ban |
| `TZ` | `UTC` | Date handling in a pipeline whose fixtures carry `dob` |
| `LC_ALL` | `C` | String collation, and therefore `ORDER BY` on the VARCHAR keys §13.1 sorts by |

Asserting them in the job matters as much as setting them: a runner-image change that alters a default is
then caught by the gate rather than by a mystery hash mismatch weeks later.

---

## 23. Amending this document `[UNVERIFIED]`

§18 governs **breaking** a rule once. Nothing governed changing the rules themselves — adding, amending,
retiring or re-scoping one — so the document had no defined way to stay true. Combined with markers that
never expired (§0) and mechanisms nothing verified still existed (3.39), that is how §7 stayed stale for a
revision while the decision replacing it sat three pages away in a companion document.

**Rule ids are stable and are never reused.** 3.1–3.37 keep their numbers permanently; v2's additions start
at 3.38 for that reason, which is why the three unenforced conventions sit mid-table rather than at the end.
A retired rule is struck through with its retirement reason, not deleted and not renumbered — renumbering
breaks every cross-reference in the file, and this document cross-references heavily.

Every new or changed standard states, in the same PR: its **mechanism**, its **gate**, and its
**`verify_gates.py` injection** (3.38). A rule without an injection has not been shown to fire.

**Every configuration artifact has exactly one canonical home, and once the repository file exists, it is
the canonical one.** This document thereafter cites it **by path**, keeping the provenance marker, the
delta ledger and the rationale — never a second copy of the content. `[VERIFIED]` describes a file at a
path, not a fenced block in a document.

Without that rule, Appendix C's keep-as-executed convention and the live files diverge with nothing
watching: 3.39 cross-references `.pre-commit-config.yaml`, `dbt-bouncer.yml` and `.sqlfluff`, and while
those exist only as fenced blocks here the check **has no subject** — the same vacuous-pass failure §6.1
diagnoses in `check_yml_pairing.py`'s seed clause. 3.69's `check_unit_test_fixtures.py` is a fourth file in
that class. The rule is what makes 3.39 mean something after the scaffold lands, and Appendix C's handover
rule is its other half. *(Closes RC33.)*

**A removed rule must state why the failure it prevented is no longer possible.** This is the requirement
that stops quiet erosion, and it is the one most likely to be skipped, because removing a rule always feels
like cleanup. `DesignDoc`'s numbered-decision model (D1–D11, superseded by later D-numbers) and its
rejected-findings appendix are the pattern; §1.1 and Appendix A.3 are this document's equivalents.

Labelled convention (unenforced) at 3.65, honestly: nothing mechanical checks that an amendment reasoned
well, only that it filled in the fields.

---

## 24. Licensing and attribution `[UNVERIFIED]`

`DesignDoc` D6 passes Splink's rendered SQL through **verbatim**; S4 deliberately replicates a Splink defect;
both documents cite Splink source files by path and line throughout. The repository has a `LICENSE` and
neither document states the package's own license, its position on Splink-derived material, or whether
attribution is required.

State the license in `README.md` and §5's layout; state the position on verbatim comparison-level SQL
strings and whether they constitute derived work; add a `NOTICE` if attribution is required. One paragraph,
once — cheap now, and awkward at exactly the moment §19 comes due, which is the first time anyone outside
this repository installs the package.

---

## Appendix A — Rejected practices

### A.1 The six claims that execution disproved

Each of these came from careful research and would have been stated confidently — and wrongly — in a
document written without building anything.

| Claim as researched | What actually happens |
|---|---|
| `check_model_property_file_location` can enforce a 1:1 layout via `layout: per_model` | **No such parameter in any release.** At 3.8.0 the function takes only `model` and hardcodes dbt's `_<dir>__models.yml`. Enabling it fails **every** model here |
| dbt-bouncer custom checks subclass `BaseCheck` | The API is the **`@check` decorator with a bare `fail()`**. The decorator generates the class |
| A custom check can live anywhere under `custom_checks_dir` | The loader globs **`*/*.py`** — it must be in a **subdirectory**. A top-level file is silently ignored, and an import error is only *warned* then skipped: a false green |
| `check_model_has_constraints` sees a model's primary key | It reads **only model-level `constraints`**. A column-level `primary_key` is invisible, so the coverage gate passes an unkeyed model |
| `format: dict` unit-test fixtures are typed from the model contract | They are typed by **agate inference over the literal values** — which typed `dob` as DATE from strings that are not dates |
| Analysis views over the attached metrics DB just work | Views naming the `obs` ATTACH alias fail with `Catalog "obs" does not exist` the moment the file is opened directly |

Two smaller ones: `check_model_has_meta_keys` takes `keys` as a **list**, not a mapping of key→null; and
`warn_error_options: {error: all}` promotes **test warnings** to errors, making `severity: warn` inert.

And one correction to a claim made *in this document's own research*: recursive `USING KEY` models **do**
execute correctly under dbt unit tests on DuckDB 1.5.5 (DesignDoc M17(c)). Excluding them was policy, not a
hard failure — and as of v2.2 that policy is reversed: `DesignDoc` **D12** raises 3.20 to every model, so
`er_entity_clusters` is unit-tested and the harness covers it in addition (§12.2).

> **[REVIEW 2026-08-23] Fixed (F11):** the paragraph above ended at "Excluding them is policy, not a hard
> failure" — a standing decision stated in the same sentence as the measurement undermining it, and left
> that way. This is the appendix whose entire subject is claims that survived research and died on contact
> with execution; a measured correction sitting inside it un-acted-on is that same failure one level up.

### A.2 Tools evaluated and dropped

| Tool | Why not |
|---|---|
| **dbt Fusion `dbt lint`** | DuckDB adapter is beta, CLI-only, and cannot load extensions — this project needs `json`. Its autofix cannot fix most of the determinism rule set |
| **dbt-checkpoint** | Its description hooks pass if the value is in the manifest **OR** the yml, so a stale manifest green-lights a deleted description. Fatal under "maximum strictness". Also ships telemetry on by default |
| **dbt-score** | Manifest-only and genuinely fine, but its weighted-score model fights a pass/fail policy and its rule set is a subset of bouncer's. Two tools failing for one defect |
| **dbt-coverage** | Subsumed by `check_model_documentation_coverage` / `check_model_test_coverage` in one config, one gate |
| **dbt_artifacts** | Does not support DuckDB (PR open and conflicted since 2025-12-11) |
| **Elementary** | Supports DuckDB, but hard-wires hooks into its own `dbt_project.yml`, which consumers inherit |
| **re_data** | Unmaintained; never supported DuckDB |
| **data-diff** | Deprecated by Datafold |
| **Recce** | A PR-review diffing UI, not a build gate; the parity harness does strictly more |
| **dbt_expectations** | Forces every consumer to define `vars: 'dbt_date:time_zone'`. Reimplement the 3–4 tests actually needed. (Its DuckDB support is fine — that was never the objection) |
| **sqlfmt** | Non-configurable, and its layout fights SQLFluff's LT rules. Running both is a format war; `sqlfluff fix` is the formatter |
| **dbt-osmosis as a blocking gate** | Audited only to dbt-core 1.11.x while we pin 1.12.2, and `+dbt-osmosis: "{model}.yml"` is undocumented. Useful as a **generator**, not a gate |
| **black + flake8 + isort** | Fully subsumed by ruff + `ruff format` |
| **pre-commit.ci** | Cannot see `uv.lock`, the DuckDB profile, or a `dbt parse`-generated manifest — all of which the dbt templater and bouncer need |

### A.3 Practices rejected on the merits

| Rejected | Why |
|---|---|
| Folder-level `schema.yml` | Contradicts §6 and drifts silently |
| YAML anchors for DRY | Resolve only within one document, so structurally incompatible with 1:1 files |
| `{% for %}` loops in properties files | dbt parses YAML before rendering; a loop returns a `str`, not list structure (§9) |
| Total `ORDER BY` in model bodies | Measured 4.2× and unnecessary (§13) |
| `threads=1` as the determinism gate | Also passed a *wrong* `USING KEY` formulation (§13.2) |
| `preserve_insertion_order: false` on the parity target | Lets DuckDB reorder rows lacking `ORDER BY`, voiding the guarantee |
| `--defer` / `state:modified` in parity jobs | Cannot see `--vars`; reports green with stale scores (§15) |
| `dbt build --empty` as a determinism check | Zero rows makes every fingerprint degenerate. It is a good *contract* smoke test, which is how §15 uses it |
| `ignore_templated_areas = False` | Thousands of unfixable violations inside macro output that cannot be fixed without changing generated SQL, and therefore parity |
| SQLFluff `AL07` (forbid table aliases) | `l`/`r` are structural in every pairwise model |
| SQLFluff `RF01` force-enabled | Upstream disables it for DuckDB by name due to struct/lateral false positives |
| `+docs: {show: false}` as a strictness measure | Only hides nodes from the docs DAG; changes nothing about contracts, descriptions or checks |
| Caching `target/` or the `.duckdb` file in CI | A stale database is the likeliest false-green |
| **Automatic retry on any gate** (v2) | Converts a real non-determinism finding into a coin flip — §21 |
| **`severity: warn` as a quarantine mechanism** (v2) | Inert under `error: all`; §12.6 proved this |
| **Renumbering the §3 matrix when rules are added** (v2) | Breaks every cross-reference in a heavily cross-referenced document. Ids are stable, §23 |
| **A blanket per-model exemption var** (v2) | Escapes every clause of §18's own waiver policy — §18.1 |
| **Mutable action tags in CI** (v2) | The §15 runner-image argument, applied to executable third-party code |
| **Trusting `[VERIFIED]` across a pin change** (v2) | A marker is scoped to a toolchain and expires with it — §0, 3.44 |

### A.4 The v1 materialization policy, and why it was wrong

Kept rather than deleted, because the reasoning was careful and a rejected position that leaves no trace gets
re-proposed. Superseded by §7 per `DesignDoc` D11.

**What v1 said.** Materialize `er_int_comparison_vectors` and the wide half of `er_int_scored_pairs` as
`ephemeral` when `er_materialise_intermediates = false` (the package default), so dbt fuses them into a
single CTAS and recovers Splink's own shape. Production runs fused; CI and the parity harness set the var
`true` so each stage is a real relation. Grounded in `DesignDoc` Appendix A **B1**, which measured
one-model-per-CTE at 946 B/pair against Splink's fused 54 B/pair — 17.6× resident bytes, 1.74× wall time,
and a single-node ceiling of ~2.75M records where Splink reaches ~11.5M.

**Why it was wrong, in three parts.**

1. **B1 conflated two costs.** D11 separated them: the `_l`/`_r` passthrough is 3.0–3.9× of the total, and
   materialisation is the smaller term. Narrow-and-materialised measures ~100 B/pair — affordable — so the
   trade v1 accepted was never necessary.
2. **The mitigation did not mitigate.** "CI runs with `er_materialise_intermediates: true`, so the contracts
   are still proved on every PR" proves them in a configuration production never uses. Under the default,
   the two models Stages 4 and 5 exist to prove carried no contract, no DDL constraints, no `run_results`
   row and no timing — the strictest models in the pipeline were the least verified, in production only.
3. **The derived cap outlived its derivation.** `er_max_pairs: 42000000` came from the wide 946 B/pair
   figure. Under a narrow shape the same budget admits ~400M, so the guardrail under-provisions ~10× and
   fires early — after which someone raises it by hand and it stops meaning anything.

**And the meta-lesson, which is the reason this appendix entry exists at all:** §1's v1 precedence rule
("Appendix A wins") pointed at the *superseded* answer, because the ephemeral strategy was itself an
Appendix A recommendation and D11 lived in the `DesignDoc` body. A precedence rule with only one populated
tier is not a precedence rule. §1.1 has four.

---

## Appendix B — Open decisions

These are genuinely unsettled. Each changes something structural and none should be resolved by drift.

**B.1 — Runtime substrate (DesignDoc M17).** ~~Open.~~ **Resolved in v2.3 — see the resolution below.** DuckDB's process-level lock means the pytest harness and dbt
cannot both hold the database. Two options: (a) dbt writes to `path: ':memory:'` and models export to
parquet, so the harness reads **only parquet** and never opens the database — this also matches the Stage
0.3 baseline format, making both sides of every comparator parquet; or (b) keep a file database and run the
harness *inside* the dbt process via a plugin or `on-run-end` hook, never as a sibling. The verified
`profiles.yml` in Appendix C uses a file path, which is option (b) without the in-process harness — so this
must be settled before the harness lands. **Recommendation: (a).**

> **[REVIEW 2026-08-23] Fixed (F28, completed F30) — RC45 is closed.** B.1 reached ordering artifacts
> first — `DesignDoc` §5 Stage 0.0 step 4, §5 Stage 0.3's blocker line, and Appendix D.1's sequence — and
> then **B.1 itself was decided before the scaffold could rebuild `profiles.yml`**, which is exactly what
> RC45 asked for. The outcome is the mirror image of the drift it warned about: the file survives
> unchanged, but by decision and with the reason recorded beside it. **DR-13 is CURRENT.**
>
> <details><summary>Original review note (RC45), retained</summary>
>
> **RC45 — B.1's deadline reaches no ordering artifact.** "Settled before the harness
> lands" makes this the earliest-deadline open decision in the programme: the harness is a `DesignDoc`
> Stage 0.3 deliverable, and §12.7 wants the comparator suite even before 0.4 freezes baselines. Yet
> `DesignDoc`'s register carries DR-13 as plain OPEN, without the "blocks …" marker its sibling DR-09 has,
> and no §5 stage names it. State the stage explicitly here ("blocks `DesignDoc` Stage 0.3"), and have
> DR-13 marked to match — otherwise the C.3 `profiles.yml` this appendix already flags as option-(b)-shaped
> gets rebuilt verbatim during scaffolding and B.1 is resolved by drift, which this appendix's header
> forbids.
>
> </details>

**B.9 — A companion CTE inside `WITH RECURSIVE` (v2.3).** ~~Open.~~ **Resolved 2026-08-23: §7.3.1 is scoped, not waived.** Register row **DR-24**.

§7.3.1 says a `WITH RECURSIVE` clause may contain only its recursive term(s). D4's `bidir` — `select l as src, r as dst from edges UNION ALL select r as src, l as dst from edges` — is a non-recursive companion joined *from* the recursive term, and §7.3.1's own escape (the seed may select directly from a `ref()`) does not cover it. **Undirected-graph recursion always needs a doubled-edge adapter**, so the ban binds on the flagship model from day one, despite §7.3.1's claim that it is "rarely binding" (RC38, and `DesignDoc` RC2).

**The value in force.** A `WITH RECURSIVE` clause may also contain a companion CTE that is a **pure single-source projection of a `ref()`ed relation**: no aggregate, no join, no set-membership filter — column derivation and orientation only.

**Why (c) rather than (a) or (b).** Option (a), making `bidir` a model, costs a `table` at **2×** the edge count under §7 to hold an orientation flip with no independent meaning; §7.3.2's own test — a construct producing a row set another stage consumes is a stage, one existing only to serve a single statement is not — puts it firmly on the inline side. Option (b), a §18 waiver, is worse in a subtler way: §7.3.3 caps `er_max_cte_waivers` at **zero deliberately**, and requiring a waiver for something structurally unavoidable means the rule is wrong rather than the situation exceptional. A rule that every correct undirected-graph recursion must violate is not a rule.

**The enforcement gap is real and is the same one 3.68 already declares.** Distinguishing "pure projection" from an arbitrary CTE needs a parser, and neither SQLFluff 4.3.0 nor sqlglot handles `USING KEY`. So this exemption is enforced by review, exactly as 3.68 is, and it is labelled as such rather than presented as gated. What keeps it narrow is that it is a *definition* a reviewer can apply mechanically, not a judgement call.

**D5's `init_params` is the second candidate instance** and is *not* pre-approved here: when D5 is written, it is checked against the definition above like anything else.

> #### B.1's resolution, in force — the harness reads only parquet; dbt keeps a file database

**Value in force (B.1, 2026-08-23).** The parity harness **never opens the DuckDB database**. Every model it compares is
exported to parquet by a `COPY` post-hook declared in `integration_tests/dbt_project.yml` — never in the
package, per 3.52 — and the harness reads only those parquet files and Stage 0.3's parquet baselines. dbt
itself keeps a **file** database at `DBT_ER_DB_PATH`, as C.3 already has it.

**This is option (a)'s contract on option (b)'s substrate, and the split is the decision.** M17 bundles two
separate things under (a): the parquet-only harness, and `path: ':memory:'`. The first is what actually
solves the lock problem. The second does not survive contact with this project's own CI.

**Why `:memory:` is rejected.** C.7's `build` job runs **five separate dbt invocations** — `dbt seed`,
`dbt build --empty`, `dbt build --full-refresh`, `dbt run-operation`, `dbt docs generate`. An in-memory
database does not survive between processes, so:

- `dbt seed` would seed a database that dies when the process exits, and §15's *"`dbt seed` must precede
  `dbt build`"* becomes unimplementable rather than merely awkward;
- `dbt docs generate` would build `catalog.json` against an **empty** database. §15 requires it to precede
  the catalog checks *"which need `catalog.json` built against a real database"* — and dbt-bouncer's whole
  catalog tier would then pass over nothing.

That last one decides it. A catalog tier passing vacuously is §12.7's *"zero differences by comparing zero
rows to zero rows"* wearing a different hat, and this document's central commitment is that a gate which
cannot fail is not a gate.

**What `:memory:` was buying, and what replaces it.** It made *"harness and dbt are never siblings"*
**structural** — with no file, there is nothing to lock, so the mistake is impossible. That is a real
benefit and it is given up knowingly. What it prevents, though, is a **loud** failure: DuckDB raises
`IOException: Conflicting lock is held`, immediately and unmistakably. What it costs is a **vacuous pass**.
Trading a loud failure for a silent one is the wrong direction, so the sibling rule stays a sequencing rule
— enforced by `make` target ordering and by the CI job graph, both of which are checkable — rather than
becoming a property of the filesystem.

**Follow-through, because adopting this changes three things:**

1. **`profiles.yml` keeps its file path** — but it is kept *by decision* now, not by drift. RC45's warning
   was that C.3 gets rebuilt verbatim during scaffolding and B.1 resolves itself; the file happens to end
   up the same, and the reason it does is written down here.
2. **A `COPY … TO … (FORMAT PARQUET)` post-hook** is added in `integration_tests/dbt_project.yml`, exporting
   every model the harness compares. It cannot live in the package: 3.52 forbids the package writing
   relations or declaring `on-run-end` hooks into a consumer's warehouse, and §14.8 already confines the
   observability hooks the same way for the same reason.
3. **The parity, determinism and comparator-sensitivity jobs `needs: build`** and read only the parquet
   artefact, never the database. C.7's artefact upload already carries `target/`; it gains the parquet
   directory.

**What this does not change.** D11 stands: every model is still `materialized: table`. The export is a
post-hook, not a materialization, so `er_allowed_materializations` stays `["table"]` and dbt-duckdb's
`external` materialization — which M17 names as one way to do the export — is **not** adopted, because it
would require carving a second entry into a policy D11 deliberately closed.

> **Decided under delegated authority 2026-08-23.**
> **Recommendation source:** M17 rec (a), **adopted in part** — its parquet-only harness contract is taken,
> its `:memory:` substrate is rejected on evidence from C.7 and §15 that postdates the recommendation.
> M17 rec (b), the `threads` pairing, is adopted as written and C.3 already implements it.
> **Reversible:** reopen B.1 and DR-13.

**B.2 — Thresholds as a var or a dimension (DesignDoc M16).** ~~Open.~~ **Resolved 2026-08-23: the dimension**, together with DesignDoc DR-08 and DR-09. See `DesignDoc.md` §1.7 — `thresholds` becomes a relation of `(thr_auto_merge, thr_review_low)` pairs cast **DOUBLE**, `er_thresholds` defaults to one row, and the half-open gray band is emitted to `review_pairs` rather than clustered. `var('er_threshold')` builds one partition per
run; a `thresholds` relation cross-joined with a composite `USING KEY (thr, unique_id)` produces all of them
in one statement. The Stage-6 acceptance criteria require three thresholds simultaneously, and cross-threshold
monotonicity is not expressible as a dbt test under the var approach. This changes the *contract* of
`er_int_edges` and `er_entity_clusters`, not just their SQL. **Recommendation: the dimension.**

**B.3 — DuckDB pin.** Every current option is near EOL (§16). **Recommendation: stay on 1.5.5**, because
Appendix A's measurements used it, and treat the bump as a scheduled project with the D4 gate blocking.

**B.4 — `er_run_id` on every model (DesignDoc M7).** ~~Open.~~ **Resolved in v2: adopted.** Promoted from an
open decision to §14.9's run contract, with the volatile-column pairing mechanised by 3.48 rather than left
as an instruction to remember. What remains open is (c) and (d) of M7 — the written idempotency key and the
five-value exit-code taxonomy — which need `DesignDoc` Stage 11 and M12 to settle how they branch on it.

**B.5 — Calibrating the perf gates.** The `check_run_results_max_execution_time` thresholds in Appendix C
are placeholders (30s/120s/300s). They should be set from the trailing median in `model_perf_trend` once
real fixture data exists, with a ratio-plus-absolute-floor rule so runner noise does not flap the gate.
**Blocked on B.6** — there is currently no trailing median to calibrate against, so until that is resolved
these thresholds are absolute-only.

**B.6 — Where performance history lives (v2).** §14.6's trailing 7-run median, 3.28's regression gate and
B.5's calibration all require history that §15's ephemeral runners cannot accumulate: Appendix C.7 uploads
the observability database as a 14-day artefact and never restores it. Options: (a) restore-then-append the
prior artefact at job start — simplest, bounded by artefact retention; (b) commit a trend summary to a
branch; (c) push to external storage. (b) and (c) need write permissions, which interacts with 3.56.
**Recommendation: (a)**, and reword §14.6 to state which runs the median covers.
**Sequencing: B.7 closes before this one.** Every option here makes the observability artefact accumulate
across runs, and B.7 is about that artefact carrying raw `query_sql` with literal attribute values —
deciding B.6 first turns a single-run exposure into an accumulating one (§14.10, C.7 delta 8, RC52).
**Blocks:** B.5, which has no trailing median to calibrate against until this resolves, and C.7 delta 8.

**B.8 — ST05 under the CTE ban (v2.1).** *(Register row **DR-23** created 2026-08-23, closing RC46's second half; the row is OPEN and blocks DesignDoc Stages 1 and 5. Its value is set by **DesignDoc §5 Stage 0.8**, which owns the option-(c) `EXPLAIN ANALYZE` spike — so (a) is not adopted by default.)* §7.3 bans non-recursive CTEs; §11.1's `forbid_subquery_in = both`
forbids the FROM-clause subquery that `DesignDoc` D11 rec 4 mandates; repeating the expression is rejected on
float-parity grounds. One of the three must give. Options: (a) relax ST05 to `join`, permitting a subquery
for single-projection expression reuse; (b) make the clamped product its own model, which costs a pair-grain
relation (~100 B/pair) to hold one intermediate float; (c) use a DuckDB **lateral column alias**, which needs
`EXPLAIN ANALYZE` first to confirm it evaluates once rather than expanding textually — D11 requires
single-evaluation to be *structural*. **Recommendation: (a), after testing (c).** If (c) holds it is strictly
better, since it leaves the determinism rule set untouched. Changes 3.15 either way.

> **[REVIEW 2026-08-23] Fixed (F34) — RC46 is closed.** B.8 now has a register row, **DR-23**, marked OPEN
> and blocking `DesignDoc` Stages 1 and 5. Its option-(c) spike has an owner: **`DesignDoc` §5 Stage 0.8**,
> whose result sets the value — so option (a) is not adopted untested by default, which was this note's
> central worry.
>
> <details><summary>Original review note (RC46), retained</summary>
>
> **RC46 — B.8's blocking consequences are unstated, and it has no register row.**
> Three sequencing facts belong here. First, §11.1 already concedes that until B.8 is decided
> "`er_int_scored_pairs` cannot be written to satisfy both rules at once" — that makes B.8 a blocker on
> `DesignDoc` Stage 5, and on Stage 1's snapshot AC (which reviews rendered scoring SQL containing D11
> rec 4's subquery); say so. Second, B.8 postdates `DesignDoc` §B.3, so the register — the artifact whose
> closing instruction ("close before writing models") a builder consults first — has no row for it; it
> needs a DR entry marked as blocking Stages 1/5. Third, option (c)'s `EXPLAIN ANALYZE` check is a spike,
> and Stage 0 is where the plan puts spikes; schedule it there, or "after testing (c)" has no owner and (a)
> gets adopted untested by default. (Editorial: B.8 sits before B.7 — a v2.1 insertion artifact; ids are
> stable per §23, so reorder the sections or note the ordering.)
>
> </details>

**B.7 — Observability redaction (v2).** §14.10: `QueryLog` carries raw SQL, which can embed literal attribute
values, into an artefact retained 14 days. Options: strip `query_sql`, hash it, or keep it and shorten
retention with the artefact classified. **Recommendation: hash it** — the column exists to join metrics back
to nodes (§14.4 extracts `node_id` from it), and a hash preserves that join while removing the payload.
Verify the `node_id` regex still works against a hashed column before committing to this; if it does not,
extract `node_id` first and then hash.

---

## Appendix C — Reference configurations

Copy-pasteable. Provenance marked per file.

> **How to read Appendix C in v2.** The blocks below are reproduced **as executed** and keep their
> `[VERIFIED]` markers. v2's changes are listed as explicit deltas after each block and are `[UNVERIFIED]` —
> none has been run. Editing a verified block in place would destroy the only thing §0 says the document
> must not smooth away: the difference between "this works" and "this should work."

> **The handover rule (normative).** This appendix holds ~700 lines of configuration plus a ~170-line Jinja
> macro because Appendix D records that the scaffold was deleted *"so this document could stand alone"* —
> not because a design document should carry code. **When each file lands in the repository, this section
> reduces to three things: its provenance marker, a pointer to the file by path, and its delta ledger.**
> The deltas are what this document uniquely records; the blocks belong under the gates §3 builds to watch
> them. C.5 already shows the by-reference form, and currently dangles for exactly that reason (RC50).
>
> This rule is load-bearing rather than tidy. The keep-as-executed convention above is correct while the
> files do not exist and becomes a guarantee of divergence the moment they do — with 3.37, unenforced by
> this document's own admission, as the only thing watching. §23's canonical-home rule is its other half.
>
> *(Closes RC47.)*

### C.1 `dbt_project.yml` — the package `[VERIFIED — as executed, v1 form]`

**Canonical: [`dbt_project.yml`](../dbt_project.yml).** Per §23 the repository file is canonical. What
this section keeps is the provenance marker above, the reasoning below, and the delta ledger that follows —
the deltas being the part this document uniquely records.

**The shape, for a reader who needs it without opening the file.** `name: dbt_er`, `require-dbt-version`
pinned to the §4 range, `model-paths`/`test-paths`/`macro-paths` at their conventional roots, and a `vars:`
block carrying only the values a consumer is *meant* to set: `er_input_relation`, `er_input_columns`,
`er_thresholds`, and the quality floors. The hardening values live in `macros/quality/er_hardening.sql`,
not here — see delta 10 and the RC48 note below, which is the distinction that keeps the compile gate from
being consumer-disarmable. `on-run-start` invokes `er_assert_project_standards()`, and `flags:` must stay
byte-identical to `integration_tests/dbt_project.yml` (3.22).


#### C.1 deltas required by v2 `[UNVERIFIED]`

| # | Change | Driver |
|---|---|---|
| 1 | `er_allowed_materializations` drops `ephemeral` → `["table"]` | §7 / D11 |
| 2 | `er_materialise_intermediates` removed entirely | §7 / D11 |
| 3 | `er_max_pairs` re-derived from measured narrow B/pair, ideally via `make capacity` rather than a second hard-coded number | §7.1 |
| 4 | `er_must_be_table` → `er_must_carry_constraints` | 3.12, §7.2 |
| 5 | New: `er_retain_matching_columns: false` | §7, 3.53 |
| 6 | `er_standards_exempt_models: []` → `er_standards_exempt: {}`, the per-check reasoned mapping | §18.1, 3.43 |
| 7 | New: `er_standards_enabled: true` | §2.1, 3.51 |
| 8 | New: `er_allowed_tags`, `er_volatile_columns`, `er_min_gamma_cell_observations` | 3.42, 3.48, 3.47 |
| 9 | `dbt_er_enabled` → `er_enabled` | §10.5, 3.33 |
| 10 | Hardening values move **out of `vars:`** into a package-owned macro | §2.1, 3.51 |
| 11 | The `data_tests:` block moves to `integration_tests/dbt_project.yml` | §12.5, 3.52 |
| 12 | **`er_thresholds` loses its `[0.9]` default and becomes required** — an unset `thr_auto_merge` fails compilation. It also changes shape: a relation of `(thr_auto_merge, thr_review_low)` pairs cast **DOUBLE** | DesignDoc §1.7, §1.8, DR-08, DR-09, DR-22 |
| 13 | New: `er_blocking_recall_floor`, `er_f1_floor`, `er_max_cluster_size` — committed per fixture, set at Stage 0.4, owned via `CODEOWNERS` | DesignDoc §1.8, DR-22 |

Deltas 10 and 11 are the two that change shape rather than values, and both are consumer-facing: 10 is what
makes the gate un-disarmable from root config, 11 is what stops the package writing relations into a
warehouse that did not ask for them.

> **On deltas 1, 4 and 6 — read them with delta 10, not as `vars:` edits (closes RC48).** The values those
> three produce — `er_allowed_materializations: ["table"]`, `er_must_carry_constraints`, and the per-check
> `er_standards_exempt` mapping — are precisely the three §2.1 names as **hardening values**, and delta 10
> relocates hardening values **out of `vars:`** into a package-owned macro root configuration cannot reach.
> Their final home is the macro. Applied as a checklist and stopped at the `vars:` block, deltas 1, 4 and 6
> leave all three consumer-reachable, which is the exact disarm §2.1 exists to prevent: a consumer sets
> `er_allowed_materializations: ["view"]` in their own `dbt_project.yml`, every DDL constraint in the
> project silently becomes inert, and their build stays green.
>
> <details><summary>Original review note (RC48), retained</summary>
>
> **RC48 — Deltas 1, 4 and 6 point at the wrong destination.** They are written as
> in-place `vars:` edits to the three values §2.1's hardening row names as "**Not** read from `var()`;
> defined in a package-owned macro" (`er_allowed_materializations`, `er_must_carry_constraints`, the
> exemption list), and §18.1 calls the corrected exemption "a hardening value rather than a policy var" —
> yet the closing sentence above says only 10 and 11 "change shape". Applied as a checklist and stopped
> there, deltas 1/4/6 leave all three consumer-reachable — the exact disarm §2.1 describes. State that the
> values deltas 1, 4 and 6 produce are the "hardening values" delta 10 relocates, and that their final home
> is the macro, not `vars:`.
>
> </details>

> **[REVIEW 2026-08-23] Fixed (F24) — the RC-note on 3.52 versus this block's `on-run-start` hook is closed
> where it belonged.** 3.52 now reads *"declares no `on-run-end` hooks"* and names
> `er_assert_project_standards` as explicitly permitted, so this block keeps the hook §2 calls *"the only
> gate that reaches their build"* without needing a delta of its own. The carve-out went in the standard,
> not in the configuration — see F19 under §3.

### C.2 `packages.yml` — the shipped surface `[VERIFIED]`

**Canonical: [`packages.yml`](../packages.yml).** One entry, `dbt-labs/dbt_utils >=1.4.1,<2.0.0`.

Everything in that file is **force-installed into every consumer project**, because dbt resolves package
dependencies transitively — so a development convenience added there becomes a dependency of every
downstream build, and the person who pays for it never sees the commit. Development packages belong in
`integration_tests/packages.yml`, which no consumer installs. `scripts/check_root_packages_minimal.py`
(3.29) asserts the shipped set never grows, and rejects `local:` and `git:` entries by name.

No deltas.

### C.3 `profiles/profiles.yml` `[VERIFIED]`

> **The file path is deliberate, not inherited (B.1, resolved 2026-08-23).** RC45's concern was that this
> block is option-(b)-shaped and would be rebuilt verbatim during scaffolding, resolving B.1 by drift. The
> block does survive unchanged — but by decision: `:memory:` cannot support C.7's five separate dbt
> invocations, and the property B.1 needed was *"the harness never opens the database"*, which the parquet
> export in `integration_tests/` provides on a file substrate. **Do not switch this to `:memory:`** without
> reopening B.1; `dbt docs generate` would catalog an empty database and the bouncer's catalog tier would
> pass over nothing.

**Canonical: [`profiles/profiles.yml`](../profiles/profiles.yml).** Checked in deliberately: DuckDB is an
in-process file, so there are no secrets in it.

**The shape.** Target `ci` is the **only** one whose determinism guarantees hold — `threads` from
`DBT_ER_DUCKDB_THREADS` defaulting to 8 (§13.2 is explicit that determinism runs at 8, never 1), a
`memory_limit` that fails hard rather than degrading, an explicit `temp_directory` and
`max_temp_directory_size`, and `preserve_insertion_order: false`. The database path comes from
`DBT_ER_DB_PATH`, which is what lets `verify_gates.py` point a scratch copy at its own file.

`memory_limit` matters more than it looks: G13 measured that DuckDB 1.5.5 exposes **no statement timeout**
(`duckdb_settings()` returns zero rows for `%timeout%`), so it, not a clock, is the real backstop — and
D4a's finding that `USING KEY` OOMs rather than degrading is what makes that a hard boundary instead of a
slow one.


### C.4 `macros/quality/er_assert_project_standards.sql` `[VERIFIED]`

The compile-time gate, and the only one that reaches a consumer's build.

**Canonical: [`macros/quality/er_assert_project_standards.sql`](../macros/quality/er_assert_project_standards.sql).**

**The shape.** One `{% macro %}`, invoked from `on-run-start`, looping `graph.nodes` filtered to
`resource_type == 'model'` and `package_name == 'dbt_er'`, collecting violations and raising once with all
of them rather than failing on the first. It reads its policy from `er_hardening()` (§2.1, delta 10) so
that root-project config cannot reach the values, and honours the `er_standards_enabled` escape hatch and
the fail-soft-abroad rule.

**One trap worth stating here, because it is a rule about the gate rather than about the file.** `dbt
parse` does **not** execute `on-run-start` hooks, so parsing a project never fires this gate. C.7 carries a
separate `dbt run-operation er_assert_project_standards` step for exactly that reason, and
`verify_gates.py` invokes the same command — an injection run against `dbt parse` would have passed every
time while proving nothing.


It reports **every** violation at once rather than failing on the first. A gate that surfaces one problem
per run trains people to stop running it.

**C.4 deltas — started in v2.2, and now applied.** The v1 macro was `[VERIFIED]` *as executed*; rows 1–11
below have since been written into the live file, which per §23 is canonical. Only the unit-test rows
are filled in here, because those are what this revision decided — the remaining rows are the ones RC49
enumerates and they are listed unfilled rather than omitted, so the gap is visible.

| # | Current line | Required, and why |
|---|---|---|
| 1 | `{%- if mat in ['table', 'ephemeral'] and 'recursive' not in (meta.get('sql_features') or '') and name not in unit_tested -%}` | Drop **both** guards. `ephemeral` has no subject under D11 (§7); the `recursive` guard is the automatic exemption D12 removes (3.20). The condition becomes `name not in unit_tested`, and the exemption path is the per-check `er_standards_exempt` mapping only |
| 2 | Violation text: *"Models using recursive SQL are exempt by policy but must declare `config.meta.sql_features: 'recursive'`"* | Rewrite to name the fix, not the escape: *"no unit test. Every model needs one (DbtBestPractices 3.20 / DesignDoc D12); see §12.2 for the case checklist."* An error message advertising the waiver is a waiver being recommended |
| 3 | — (absent) | `sql_features` keeps its other readers (§7.2's custom-materialization path); it stops being read here. Assert it is still *declared* where it applies rather than deleting the key |
| 4–11 | — | The eight changes RC49 lists: hardening values out of `var()`, `er_standards_enabled`, fail-soft abroad, the per-check exemption echo (3.43), `er_must_be_table` → `er_must_carry_constraints` (3.12), the identifier check (3.33), the tag check (3.42), the volatile-column assertion (3.48), the column budget (3.53). **Unfilled** |

> **[REVIEW 2026-08-23] Fixed (F12), partially:** RC49 asked for the C.4 delta table that C.1, C.6 and C.7
> each have. It now exists, with the three unit-test rows filled and the rest listed as open. RC49 stays
> open until rows 4–11 are written — a delta table with holes is still better than none, because the holes
> are now countable.

> **[REVIEW 2026-08-23] RC49 — C.4 is the only block with no v2 delta table, and it is the block v2 changes
> most.** The header note above promises "v2's changes are listed as explicit deltas after each block";
> C.1, C.6 and C.7 each carry one. Yet the body names at least eight changes this macro must absorb:
> hardening values stop being read via `var()` — today the lines reading `er_allowed_materializations`,
> `er_must_be_table` and `er_standards_exempt_models` from `var()` are the exact disarm vector §2.1
> describes, and the failure message still invites "relax the policy deliberately via the `er_*` vars in
> `dbt_project.yml`"; the `er_standards_enabled` escape hatch and fail-soft-abroad behaviour (§2.1's
> table); the per-check, reasoned, capped exemption echoed on every success (3.43 — §18.1 names this
> macro's top-of-loop short-circuit as the condemned form); `er_must_be_table` → `er_must_carry_constraints`
> (3.12); the identifier check (3.33/§10.5); the tag check (3.42); the volatile-column assertion (3.48);
> the column budget (3.53). Under D11 the ephemeral waiver branch also loses its subject. Add the C.4 delta
> table — C.1's delta 10 currently gestures at "a package-owned macro" without any block recording what
> changes in it.

### C.5 `dbt-bouncer.yml` — **reconstructed 2026-08-23; the repository file is canonical**

**The file now exists at `dbt-bouncer.yml`, and per §23's canonical-home rule that file is canonical.**
This section keeps the provenance and the reasoning; it does not keep a second copy.

**Provenance: `[UNVERIFIED]` against the lost original, `[RUN]` against dbt-bouncer 3.8.0.** The
as-executed file was `[VERIFIED]`, but it lived in the scaffold Appendix D records as *"built, run, and
then deliberately removed so this document could stand alone"* — and unlike C.1–C.4, C.6 and C.7, its text
was never copied in, so it existed nowhere. What is in the repository is a **reconstruction** from the
sources listed below, and it passes **29** checks on the pinned version. It is not, and cannot be, a
restoration.

That matters beyond the missing file, because three other rules depend on it: 3.39's
`check_standards_matrix.py` is specified to cross-reference it, §8.3 says the pair-versus-entity grain
boundary *"is held by the `include:` regexes in `dbt-bouncer.yml`"*, and 3.40 asserts the custom checks
registered inside it. ~~Each is currently a rule about a file that does not exist.~~ **All three now have
their subject** (2026-08-23): the file exists, `check_standards_matrix.py` cross-references it, and its
first run found that 3.19's `check_model_has_tests_by_type` — a real dbt-bouncer check — was missing from
this reconstruction. That is the rule doing exactly what §23 says it could not do while the file was
fenced text.

**What is known about it**, and what the reconstruction is assembled from:

| Property | Value |
|---|---|
| `dbt_artifacts_dir` | `integration_tests/target` |
| `package_name` | `dbt_er` |
| `custom_checks_dir` | `./dbt_bouncer_checks` |
| `severity` | `error` |
| Tiers | `manifest_checks`, `catalog_checks`, `run_results_checks` — run after `dbt parse`, `dbt docs generate` and `dbt build` respectively. **All three enabled**, affordable precisely because DuckDB is in-process where a cloud-warehouse project would have to skip the catalog tier |

Reconstruction sources, in order of authority: **§3's mechanism column**, which names every check by name;
**§6** and **§6.2** for the 1:1 pairing check and its custom-check registration; **§7** for the
materialization checks; **§8.3's `include:` regexes**, which carry the grain boundary and are the part
most easily lost; and **§13's** regex block, the one fenced fragment of this file that survives anywhere.

**One value cannot be reconstructed and must be re-derived rather than guessed:**
`check_run_results_max_execution_time`'s thresholds. B.5 cites them as *"30s/120s/300s"* and that phrase is
their only appearance in this document — the numbers themselves were in the deleted file. B.5 also says
they should be set from the trailing median in `model_perf_trend` once real fixture data exists, and is
blocked on B.6 for the history to do it. Until then they are absolute-only placeholders, and they are
labelled as such in the reconstructed file rather than presented as the verified originals.

*(Closes RC50.)*

> **[REVIEW 2026-08-23] Fixed (F21) — RC50 is closed by the rewrite above**, taking its second option: the
> loss is recorded rather than implied away, and the reconstruction sources are enumerated. C.5 is
> ironically the by-reference form every other block converges to under the handover rule — it just needs
> its referent to exist, which Appendix D's bootstrap order now schedules.
>
> <details><summary>Original review note (RC50), retained</summary>
>
> **RC50 — "Reproduced in full in the repository" is no longer true.** The repository
> contains only `docs/` and `LICENSE`; Appendix D records that the scaffold holding this file was "built,
> run, and then deliberately removed so this document could stand alone." C.5 is the one block whose full
> text was delegated to the deleted scaffold, so the as-executed `[VERIFIED]` `dbt-bouncer.yml` now exists
> **nowhere** — not in the repo, not in the doc — while 3.39's `check_standards_matrix.py` is specified to
> cross-reference it, §8.3 says the grain boundary "is held by the `include:` regexes in `dbt-bouncer.yml`",
> and B.5 cites "the `check_run_results_max_execution_time` thresholds in Appendix C … (30s/120s/300s)" —
> values that appear nowhere in this document. Its marker can therefore not be re-checked, only re-earned.
> Either reconstruct the full file here (restoring the "stand alone" property Appendix D claims), or —
> better, per the no-code rule — recreate it in-repo as scaffolding lands and reword this to "Reproduced in
> full in the scaffold (since deleted — Appendix D); reconstruct from the quoted parts in §3, §6, §7 and
> §13 and re-verify", so the loss is recorded rather than implied away. Ironically C.5 is the by-reference
> form every other block should converge to (RC47) — it just needs its referent to exist.
>
> </details>

### C.6 `.pre-commit-config.yaml` `[UNVERIFIED]`

**Canonical: [`.pre-commit-config.yaml`](../.pre-commit-config.yaml).**

**The shape.** Upstream hooks for whitespace, YAML and large files; `ruff` and `ruff-format`; `yamllint`;
`sqlfluff-lint`; `no-commit-to-branch --branch main`; and the local `er-*` hooks that run this
repository's own enforcement scripts. Every local hook sets `pass_filenames: false`, because each walks
both project roots itself.

**Two things measured rather than assumed** (Appendix D.0). pre-commit has **no `env:` hook field** and
does not error on one, so a hook that needs an environment variable must carry it in `entry`. And
`pre-commit run --all-files` sees only **tracked** files, so a newly written script reports
"(no files to check)" and passes — staging comes before believing a green run.


dbt-bouncer is deliberately **CI-only**: its checks need a manifest, a catalog and run results. In
pre-commit it would either use a stale manifest — a false green — or force a full build on every commit.

#### C.6 deltas required by v2 `[UNVERIFIED]`

| # | Change | Driver |
|---|---|---|
| 1 | Add `detect-private-key`, plus a PII heuristic scan over `seeds/`, `fixtures/`, `harness/` | 3.55, §20.4 |
| 2 | Add a hook rejecting staged `target/`, `dbt_packages/`, `*.duckdb` — `.gitignore` alone loses to `git add -f` | 3.54 |
| 3 | Tighten `check-added-large-files --maxkb=4096`; 4 MB of binary per file into permanent history is not a limit anyone chose | §20.1 |
| 4 | New local hooks: `er-standards-matrix` (3.39), `er-verified-markers` (3.44), `er-baseline-manifests` (3.62) | §23, §16, §20.1 |
| 5 | Add `python-tests` to the lint path so the enforcement scripts are covered by their own suite | 3.57 |
| 6 | **Drop `files:` from `er-yml-pairing` and `er-no-nondeterminism`.** Both scripts already walk both project roots and both run `pass_filenames: false`, so the filter buys nothing and costs the hook its trigger | 3.1, 3.16, RC51 |

Delta 2 is the one that matters most today: this repository has no `.gitignore` at all, and §15 already
argues that a stale database is the likeliest false-green — a *committed* one is strictly worse, because it
is restored on every checkout on every machine, permanently.

**Delta 6 is the one that is a live defect rather than a hardening (closes RC51).** With
`pass_filenames: false` the `files:` pattern still decides whether the hook runs at all, and
`^(models|macros|tests|seeds)/` matches nothing under `integration_tests/`. A commit touching only
`integration_tests/seeds/person_records.csv` — which §6.1 notes is the only seed the project has — never
triggers `er-yml-pairing`, however correctly the script walks both roots once invoked.
`er-no-nondeterminism`'s `^(models|macros)/` skips `integration_tests/` models the same way. Dropping the
filter is preferred over widening it: these are whole-repo hooks, and a narrowing that has already failed
once is not worth keeping in a more complicated form. **A gate that cannot trigger is §6.1's "check whose
subject has disappeared", one layer up.**

### C.7 `.github/workflows/ci.yml` `[UNVERIFIED]`

**Canonical: [`.github/workflows/ci.yml`](../.github/workflows/ci.yml).**

**The shape.** Six of §15's twelve jobs — `lint`, `python-tests`, `build`, `bouncer`, `verify-gates` and
the `ci-gate` aggregator. The other six arrive **with the machinery they gate**, which is why this is not a
gap. All twelve action references are pinned by 40-character SHA (3.56), `persist-credentials: false` is
set on every checkout, permissions are per-job, and the runner is `ubuntu-24.04` — pinned because §22.1
makes **linux/amd64** the normative float platform and the development machine is arm64.

**`SKIP: no-commit-to-branch` on the pre-commit step is deliberate and was learned the hard way.** That
hook checks the *current* branch, and a push run on `main` is checked out on `main` — so it failed every
push to the base branch and turned `main` red. It is skipped **by name**, not removed: it guards a
developer's `git commit`, CI never commits, and removing a real guard to make CI green is §21's failure
mode.


The `parity`, `determinism` and `project-evaluator` jobs follow the same shape and are added to `ci-gate`'s
`needs:` list as the harness lands.

#### C.7 deltas required by v2 `[UNVERIFIED]`

| # | Change | Driver |
|---|---|---|
| 1 | Pin every `uses:` by 40-char commit SHA with a version comment | 3.56, §15 |
| 2 | `persist-credentials: false` on every checkout | 3.56 |
| 3 | Per-job `permissions:` rather than relying on the workflow default | 3.56 |
| 4 | Add `PYTHONHASHSEED: "0"`, `TZ: UTC`, `LC_ALL: C` to the `env:` block | 3.58, §22.2 |
| 5 | Add a toolchain vulnerability scan to `lint` | §15 |
| 6 | New jobs: `comparator-sensitivity`, `verify-gates`, `python-tests`, `consumer-smoke` — each added to `ci-gate`'s `needs:` | §15 |
| 7 | Minimum-node-count assertion beside every `--select` | 3.41 |
| 8 | **BLOCKED — do not apply yet.** Restore the prior observability artefact before `build`, per Appendix B.6's *recommendation* (a). **B.6 is open, and B.7 must close first** | §14.6, B.6, B.7 |
| 9 | Comment the `GITHUB_ENV` heredoc constraint beside the step | §15 |

Delta 9 is documentation rather than code, and is the cheapest item in this table: the model-JSON injection
is safe *because* the JSON is repository-controlled, and the next person to wire up a workflow input needs to
know that is load-bearing rather than incidental.

**Delta 8 is the one to leave undone, and the reason is not that B.6 is merely unsettled (closes RC52).**
This table previously read *"per Appendix B.6's resolution"*, and B.6 has no resolution: Appendix B opens
with *"These are genuinely unsettled"*, B.6 offers options (a)–(c) with a recommendation, and this
document's convention for a settled item is B.4's explicit `~~Open.~~ Resolved in v2: adopted`, which B.6
does not carry. Downgraded to "recommendation" above.

The **ordering** matters more than the wording. Restore-then-append is what makes the observability
database accumulate across runs — and B.7, equally open, is about precisely that artefact carrying raw
`query_sql` with literal attribute values into a 14-day retained artifact (§14.10, §20.4). **Applying
B.6(a) before B.7 is decided widens the exact egress §14.10 flags**, and turns a single-run exposure into
an accumulating one. B.7 closes first, then B.6, then this delta.

That dependency now belongs to B.6's own row rather than living only here.

---

## Appendix D — Provenance record

The scaffold that produced the `[VERIFIED]` markers was built, run, and then deliberately removed so this
document could stand alone. What it achieved:

| Check | Result |
|---|---|
| `dbt build` (integration_tests, full refresh) | **83 pass, 0 errors** — 41 table models, 30 data tests, 2 unit tests, 4 view models, 2 seeds, 4 project hooks |
| `dbt-bouncer` (manifest + catalog + run_results) | **82 checks, 0 errors** |
| SQLFluff (duckdb dialect, dbt templater) | Clean |
| yamllint `--strict`, ruff `select=ALL`, `ruff format` | Clean |
| `mypy --strict` | Clean after the `[untyped-decorator]` fix in §6.2 |
| DDL inspection | `PRIMARY KEY`, `NOT NULL` and `CHECK` present in the created table; `persist_docs` comments present |
| Observability | Both tables populated; per-model timings, statement counts, latency and peak memory recorded across invocations |

**Not verified:** everything marked `[UNVERIFIED]` — pre-commit, all GitHub Actions workflows, dependabot,
the PR template, CODEOWNERS, and the gate self-test script (`verify_gates.py`), which was designed to prove
each gate *fails* when violated but never written. **Plus everything added in v2**, which is all of §19–§24,
§2.1, §6.1, §7 (the D11 contract), §7.1–7.2, §10.5, §12.7, §13.3, §14.9–14.10, §18.1, standards 3.38–3.66,
and every Appendix C delta table.

> **[REVIEW 2026-08-23] Fixed (F22) — RC53 is closed by D.1 below.** The task that owns the rebuild is now
> `DesignDoc.md` §5 **Stage 0.9**; D.1 is its sequence, and it answers the four questions RC53 raised: which
> C-deltas apply before first run, whether `verify-gates` can land with the first commit, what §2's gates do
> before any model exists, and what §23's same-PR injection rule means when all seventy-one rows are new at
> once.

### D.0 Second execution — 2026-08-23, step 1 of D.1 `[RUN]`

The rebuild has begun. This records what step 1 of D.1 executed and, more usefully, **what it disproved** —
Appendix D's original entry notes that the v1 exercise *"disproved six claims that careful research had
stated with confidence"*, and this one disproved six more. Everything below is `[RUN]` on
dbt-core 1.12.2 · dbt-duckdb 1.11.0 · DuckDB 1.5.5 · Python 3.12.13 · macOS arm64.

#### Confirmed

| Claim | Result |
|---|---|
| §4's pins co-resolve | **Yes.** `uv lock` resolves 108 packages; every exact pin lands at its stated version, including `sqlglot==30.17.0` under dbt-bouncer's `<31` |
| §8.2 — dbt-duckdb enforces DDL constraints | **Yes, and they bite.** `CREATE TABLE … (thr_auto_merge DOUBLE PRIMARY KEY, thr_review_low DOUBLE NOT NULL)`; a duplicate key raises `ConstraintException`, and so does a NULL. *Observed to fail when violated*, which §0 notes `[VERIFIED]` alone never establishes |
| `persist_docs` writes real database comments | **Yes.** Column descriptions are readable from `duckdb_columns()` |
| M16's `DOUBLE` cast trap is avoidable | **Yes.** `cast(… as double)` produces DOUBLE, not DECIMAL |
| §1.8 — no default threshold, fails compilation | **Yes.** `ER-010` raises at parse time with the empty var |

#### Disproved, or absent

1. **`require_generic_test_arguments_property: true` makes the conventional data-test syntax invalid.**
   Test arguments must nest under `arguments:`; the top-level form raises
   `MissingArgumentsPropertyInGenericTestDeprecation`, which `warn_error_options.error: all` turns into an
   error. **Neither document shows the required form**, so every data-test example in both is currently
   unbuildable.
2. **`+group: er_core` requires a `groups:` declaration that Appendix C never provides.** §5's tree names
   `models/_er_groups.yml`; its contents existed nowhere. Without it, parse fails with
   *"Invalid group 'er_core', expected one of []"*.
3. **`given:` is required on every unit test**, even for a model that reads no relation. D12's "every model
   has a unit test" therefore needs `given: []` on any parameter-only model — of which this project will
   have several.
4. **A misplaced `unit_tests:` block is silently ignored.** Nested under a `models:` entry rather than at
   the top level of the properties file, it produces a clean parse, exit 0, **no warning**, and zero unit
   tests. This is not a suppressed warning — `error: all` cannot catch it, because nothing is emitted.
   **3.20's gate then reports "this model has no unit test" for a model that has three**, which is the
   opposite of the truth and sends the reader to the wrong file. Wants a new standard; see below.
5. **A consumer's `dbt build` DOES execute this package's unit tests.** Measured: three `unit_test` rows in
   the consuming project's `run_results.json`. **This settles RC56 and selects 3.71's *second* branch** —
   the package must ship the documented `--exclude-resource-type unit_test` guard, and `consumer_smoke/`
   asserts *that*, not zero rows. *(Caveat: measured against `integration_tests/`, which installs by
   `local: ../`. The true consumer path is a git-ref install, which is what 3.64's job exists for.)*
6. **A failed `dbt parse` leaves the previous `target/manifest.json` in place.** Anything reading artifacts
   after a failed parse reads **stale** data — including dbt-bouncer, whose whole input is `target/`. This
   is a concrete mechanism for §15's existing *"do not cache `target/`"* rule, which previously rested on
   the milder argument about stale databases.

#### Step 2–3 (the local gate loop), same day

Four more, from writing the `Makefile` and the lint configs:

7. **SQLFluff has no `--vars`, and §11.1's "a bare `sqlfluff lint` works with no setup" depended on a
   default that DR-22 removed.** §11.1 reasons that the model JSON reaches the templater through
   `DBT_ER_MODEL_JSON` *"plus the `vars:` defaults in `dbt_project.yml`"* — and `er_thresholds` no longer has
   one, because §1.8 shows the default it had cost ~330 true pairs. **Fix:** route the threshold through the
   environment as a JSON string, exactly as D1 already does for the model JSON. The property §11.1 wanted is
   restored without restoring a default nobody justified.
8. **dbt renders a Jinja-bearing `vars:` value to a *string*.** `er_thresholds: "{{ fromjson(env_var(…)) }}"`
   yields the string *representation* of a list, and iterating it yields characters. **Structure cannot
   survive the `vars:` block**, so a structured value supplied through the environment must be parsed at the
   point of use. This is D1's constraint generalised beyond the model JSON, and it is worth stating once
   rather than rediscovering per var. Models therefore accept **both** a native list and a JSON string.
9. **`ruff format` reformats Python fenced blocks inside these Markdown documents.** It realigned §8.2's
   `CONSTRAINT_SUPPORT` dict — which is **read from `dbt/adapters/duckdb/impl.py`** — and §11.3's constants.
   §0 is explicit that a `[VERIFIED]` block is *"reproduced as executed"*; a formatter silently rewriting one
   destroys the only thing §0 says the document must not smooth away. **`docs/**/*.md` is excluded from ruff**,
   and the exclusion carries its reason.
10. **§17's `make lint` names a path that does not exist in a fresh scaffold.** `sqlfluff lint models tests`
    errors with *"Specified path does not exist"* while `tests/` holds no singular tests. The target now
    names the paths conditionally and **says** it is linting models only — rather than `mkdir`-ing an empty
    directory, which would give the gate a subject it cannot actually check.

**Also observed, not a defect:** `template_blocks_indent = True` requires SQL inside a Jinja block to be
indented one level relative to the tag, so the *rendered* SQL is indented. That is the shape every templated
model must take, and it is cheaper to know now than to rediscover per model.

#### What the local gate loop actually does now

`make lint` (yamllint · ruff · mypy · `dbt parse` both roots · sqlfluff), `make build`
(`--empty` contract smoke, then full) and `make docs` all pass. **`make bouncer` fails, correctly** — it
names C.5's lost text and exits non-zero rather than skipping. `make ci` therefore fails at the bouncer step,
which is the honest state at D.1 step 3 and is what step 4 fixes.

`repo-checks` reports **4 of 4 enforcement scripts missing** rather than passing quietly. Waiver B-1 covers
the interval; the point of reporting is that the interval is visible.

#### Step 4a (the enforcement scripts and pre-commit), same day

Five more. Two are defects in this document's own configuration; three are constraints nothing recorded.

11. **`pre-commit` has no `env:` hook field.** A hook may not set an environment variable that way, and
    pre-commit does **not** error on the unknown key — it is silently ignored. An `env:` block in a hook
    reads exactly like a live one. Supply variables inside the `entry` instead.
12. **A YAML folded scalar (`>-`) joins lines with a space, which breaks a JSON literal containing one.**
    `DBT_ER_THRESHOLDS=[{"auto_merge": 0.9}]` folded into a hook entry split at the space inside the JSON,
    and bash reported `0.9}]}: command not found`. Keep environment-supplied JSON **space-free**.
13. **`mypy` was configured against directories that do not exist yet**, and the failure does not say so:
    naming `harness` and `dbt_bouncer_checks` before either existed produced
    *"Duplicate module named `__main__`"*, which reads like a mypy bug and is not one. `files:` now lists
    only directories that exist, and each is added in the same commit as its first file — the discipline
    C.1 already applies to its `models:` blocks, for the same reason.
14. **`ruff` under `select = ALL` requires `types-PyYAML`** before `--strict` can check the two enforcement
    scripts that parse YAML, and it demands a per-file copyright header (`CPY001`) this project does not
    use. Both are settled in `pyproject.toml` **with the argument written next to them**, which is what
    3.34 asks for rather than a curated allowlist.
15. **`pre-commit run --all-files` only considers *tracked* files.** An untracked new script is not linted,
    so the first run after writing one reports `(no files to check)` and passes. Stage before trusting it.

**`no-commit-to-branch --branch main` is in C.6**, and it is the hook that would have caught `c7ffae6`
being pushed straight to `main`. It had not landed yet. It has now.

#### The two blind spots §6.1 names are closed in the script, not just noted

`check_yml_pairing.py` shipped with `integration_tests` inside `SKIP_PARTS`, which meant 3.1's *"covers
**both** project roots"* walked one, and the seed clause — seeds live only in `integration_tests/` — walked
an empty set and **passed**. Both are fixed, and both have a failing-case test: one asserts a violation in
the second root is found, the other asserts that walking an empty tree is a **finding rather than a pass**.

#### Step 4b (the compile gate and `verify_gates.py`), same day

`verify_gates.py` exists now -- Appendix D calls it *"the most valuable missing piece"* -- and running it
produced more findings than any other step, because it is the first tool whose job is to disagree with the
rest of the scaffold.

16. **`dbt parse` does NOT execute `on-run-start` hooks**, so **the compile gate does not fire during
    `dbt parse`** — despite §2 calling it the *compile* gate. C.7 carries a separate
    `dbt run-operation er_assert_project_standards` step, and that is why. `make lint` now carries the same
    step; without it, the only gate that travels with the package went unrun until `make build`.
17. **`dbt deps` installs a `local:` package as a symlink to the *absolute* path of the source
    repository.** A scratch copy therefore points back at the **live** repository, every injection has no
    effect, and `verify_gates.py` reports that no gate fires while having tested nothing. **That failure is
    silent and self-consistent** — which makes it precisely the class of defect 3.38 exists to expose, found
    here in the tool built to expose it. The copy re-points the symlink.
18. **dbt rejects every materialization our policy forbids *before* the policy macro sees it**, for a model
    that is contracted and carries constraints — which every model here is:
    `view` produces *"Constraint types are not supported for view materializations"*, a **warning** that
    `error: all` turns into an error; `incremental` produces *"must set `on_schema_change` to
    `append_new_columns` or `fail`"*. **3.11 is therefore shadowed in practice.** It is the backstop for the
    case dbt does not catch — an uncontracted or constraint-free model — and not the first line of defence.
    The `view` result is 3.13's claim demonstrated: 3.11 and 3.12 are not redundant, and **3.21 is
    load-bearing rather than cosmetic**.
19. **Three of the first fourteen injections were wrong, and the string assertion caught all three.** A
    one-line edit inside a `description: >` folded block leaves the description long; renaming only a
    model's `.sql` trips 3.1 before 3.33; a `view` materialization proves 3.21 rather than 3.11. Each
    *failed* — and would have counted as a proven gate under a bare non-zero-exit check. This is the
    concrete case for 3.38's *"and the expected error string"*, which v1's phrasing omitted.

**Result: 14 standards have a registered injection and all 14 are shown to fire.** The other 57 rows of the
§3 matrix do not, and `verify_gates.py` prints that number on every run so the gap is a figure rather than
an impression. **Waiver B-1 does not expire yet** — it expires when the matrix is covered, not when the
script exists.

#### Step 4c (`dbt-bouncer.yml`, reconstructed), same day

C.5's file is rebuilt from the sources C.5 itself enumerates — §3's mechanism column, §6 and 6.2, §7,
§8.3's grain boundary, and §13's surviving regex. **25 checks, all passing.** It is `[UNVERIFIED]` against
the lost original and **verified against dbt-bouncer 3.8.0**, which is the claim that matters.

20. **dbt-bouncer can run ZERO checks successfully and still exit 0.** Omitting `package_name: dbt_er` —
    one of the five structural points C.5 *did* record — made bouncer filter to the root project, which owns
    no models: every model belongs to the `dbt_er` package installed into `integration_tests/`. The
    coverage checks then divided by zero, dbt-bouncer downgraded them to **WARN**, and the run reported
    `SUCCESS=0 WARN=2 ERROR=0` and **exited 0**.

    This is §6.2's loader-warning finding **generalised**, and it is worse than 3.40 currently states.
    3.40 says loader warnings must be treated as errors; this shows that a check raising **during
    execution** also becomes a warning, and that `SUCCESS=0` is itself a green run. **3.40's assertion must
    therefore include a minimum success count, not only a registration check** — a config that silently
    matches nothing is exactly the vacuous pass §12.7 is written about.
21. **`dbt-bouncer list` and the config file use different vocabularies.** `list` prints class names in
    PascalCase (`CheckModelNames`); the config expects snake_case (`check_model_names`). A config
    reconstructed from `list` output validates as *entirely unknown checks*. **§3's mechanism column is
    correct** — I briefly recorded the opposite here before `dbt-bouncer validate` disproved it, which is
    the argument for running `validate` before anything else.
22. **`check_model_property_file_location` contradicts 3.1.** It enforces dbt-labs' `_<model>.yml`
    convention, the opposite of this project's `<model>.yml`; adding it produced *"does not start with an
    underscore"* against a correctly-named file. 3.2's underscore rule applies to **non-resource** YAML
    only. Not used.
23. **§8.3's `[VERIFIED]` claim held, against my own model.** `er_thresholds` shipped with a *column-level*
    `primary_key`, which is **invisible to `check_model_has_constraints`** — so the coverage gate would have
    silently passed an unkeyed model. §8.3 says primary keys are always declared at model level, even
    single-column ones, and this is why. The gate caught it; the model was fixed, and the DDL now reads
    `PRIMARY KEY(thr_auto_merge)`.

The perf-gate threshold is a **labelled placeholder**. B.5 cites the original as "30s/120s/300s", numbers
that appear nowhere else in the document because they were in the deleted file, and B.5 is blocked on B.6
for the history to calibrate against. Absolute-only and generous, and it says so.

#### Step 5 (CI), same day

`.github/workflows/ci.yml` exists with C.7's nine deltas applied, and **six of §15's twelve jobs**: `lint`,
`python-tests`, `build`, `bouncer`, `verify-gates`, `ci-gate`. The other six arrive **with the machinery
they gate** — `parity` needs a harness, `comparator-sensitivity` needs Stage 0.7, `consumer-smoke` needs a
published ref. A job asserting nothing is worse than a job that does not exist, because it reads as cover.

24. **3.40 is not sufficient as written, and the fix is in this workflow.** D.0 finding 20 showed
    dbt-bouncer exiting 0 on `SUCCESS=0 WARN=2 ERROR=0`. The row said to assert *"expected check names and a
    minimum count"*; a **registration** check alone still passes a config that matches nothing. The
    `bouncer` job now asserts a **minimum SUCCESS count**, **zero WARNs**, and the custom check by name. The
    3.40 row is amended to match, per §23's rule that a changed standard states its mechanism in the same
    PR.
25. **3.56 had no mechanism, and now has one.** `scripts/check_workflow_hardening.py` asserts every `uses:`
    carries a 40-character SHA, every job declares `permissions:`, every checkout sets
    `persist-credentials: false`, and **no workflow uses `pull_request_target`** — §15 states that position,
    and C.7's `GITHUB_ENV` heredoc is the reason it matters. Five failing-case tests; registered in
    `verify_gates.py`, which brings the covered count to **15**.
26. **Delta 8 is not applied, and that is the correct outcome.** It restores the prior observability
    artefact *"per Appendix B.6's resolution"* — and B.6 is **open**. Applying B.6(a) before B.7 is decided
    also widens the §14.10 egress (RC52). The step is absent with the reason recorded beside it, rather
    than present and inert.
27. **The `GITHUB_ENV` model-JSON step ships commented out**, because
    `fixtures/model_jsons/fake_1000_v1.json` does not exist until Stage 0.4. A heredoc reading a missing
    file would fail the job for a reason unrelated to the change under test.

28. **The first CI run failed, and it failed on my own assertion rather than on the code.** The `bouncer`
    job reported `SUCCESS=25 WARN=0 ERROR=0` and then **exited 1**: my registration check grepped
    dbt-bouncer's rendered table, and **the rich table wraps and truncates check names to the terminal
    width**. It passed locally and failed on a runner of a different width. That is the same class of defect
    as finding 20 one layer up — an assertion whose result depends on something other than what it claims to
    measure. Fixed by reading `--output-format json`, and the assertion moved into
    `scripts/check_bouncer_ran.py` so `make bouncer` and CI run the **same code** rather than two similar
    ones (§17). Five failing-case tests, including the measured `SUCCESS=0` case.

    **`ci-gate` went red because an upstream job did, which is the behaviour it exists for.** The single
    required status check worked on its first outing.

29. **`main` went red on the first push run, and the culprit was the hook that protects `main`.**
    C.6's `no-commit-to-branch --branch main` checks the **current branch** — and on a push-to-`main` run
    the checkout *is* `main`, so `pre-commit run --all-files` fails every time CI runs on the base branch.
    Every other hook passed; the job failed on that one.

    This is the case §15's *"a squash commit is a SHA that never ran CI as part of the PR"* exists for: the
    PR ran on a merge ref and was green, and the same tree on `main` was not. `main_verify.py` caught it,
    `ci-gate` went red, and the fix was forward.

    **Skipped by name in CI, not removed.** The hook guards a *developer's* `git commit`; CI never commits,
    so it has nothing to protect there. And it is the hook that would have caught `c7ffae6` going straight
    to `main`, so removing it to make CI green would trade a real guard for a cosmetic one — which is the
    §21 failure mode.

30. **Four `[VERIFIED]` markers named no toolchain, so nothing could ever have expired them.** The first run
    of `check_verified_markers.py` found `dbt_utils`, `dbt_project_evaluator`, `ruff` and `mypy` marked
    `[VERIFIED]` in §4 while appearing in no scope statement. §0 says a marker "is scoped to that toolchain
    and is demoted when a pin moves" — a marker naming no toolchain is *worse* than a stale one, because a
    stale marker is at least demotable. Three were real and merely undocumented; **`dbt_project_evaluator`
    was not real at all** — it appears in no `packages.yml`, no Make target and no CI job, so it has never
    run, and it is demoted rather than given a scope it did not earn. Before the check existed the four
    were indistinguishable.

31. **3.39's first run found an orphan in a file this rebuild wrote.** 3.19 names
    `check_model_has_tests_by_type`; dbt-bouncer really ships it; the reconstructed C.5 never configured it.
    Its two minimums both **default to 0**, so wiring it in without `min_number_of_schema_tests: 1` would
    have closed the finding while enforcing nothing — the same shape as finding 20, in the fix rather than
    the defect.

32. **A check whose subject does not exist is the failure mode, not an excuse to defer the check.** Three
    of the four scripts this step added police artefacts that arrive in Stage 0 or later. Deferring them
    means writing a two-direction check later, against artefacts already in place, which is exactly when the
    reverse direction gets skipped. They ship now and declare their emptiness in
    `scripts/pending_subjects.yml`, whose load-bearing rule is not "you may skip a missing subject" but
    **"an entry whose subject now exists is an error"**. Without the second half it would be a waiver list.

33. **§23's canonical-home rule had no mechanism, and a manual sweep would have missed a quarter of it.**
    RC33's rule is stated in §23 as prose; nothing checked it. §23's own opening explains why that decays —
    *"mechanisms nothing verified still existed ... is how §7 stayed stale for a revision"*. Written as
    **3.73**, `check_canonical_homes.py` found **eight** duplicated artifacts totalling **735 lines**. Six
    were the Appendix C blocks anyone would have listed. The other two were §11.1's `.sqlfluff` and §11.2's
    `.sqlfluffignore` — outside Appendix C, exactly where RC40 said they lived, and exactly what a sweep of
    Appendix C would have skipped. Reducing the document once is a cleanup; the rule is what stops it
    regrowing.

34. **The comparator suite was itself verified by breaking the comparator, not by watching it pass.** A
    sensitivity suite that goes green on its first run proves nothing — it is the same "observed to pass,
    never observed to fail when violated" gap §0 opens with, one level up from the gates. So four defects
    were injected into `harness/comparators.py` in a scratch copy, and each was caught **by the mutant
    designed for it**: dtype coercion on `match_key` left `coerce_match_key_to_int` surviving; degrading
    the cluster gate to partition equality left `relabel_one_component` surviving, which is M6's finding
    reproduced on demand; dropping NULL keys before diffing left `inject_null_key` surviving; and a wrong
    join key failed **13 of 24 tests**, which is §12.7's opening scenario made loud instead of green. The
    mutants are not a checklist — each one maps to a specific way the comparator can be wrong.

35. **Stage 0.8's spike changed the answer, which is the whole reason it was mandated.** B.8's
    recommendation was *"(a), after testing (c)"* — relax `ST05`, having first checked whether DuckDB's
    lateral column alias evaluates once. `[RUN]` on DuckDB 1.5.5, counted with a UDF rather than inferred
    from timings: **(c) evaluates once per row**, so option (a) is not adopted and 3.15's determinism rule
    set is untouched. Had the spike been skipped, RC46's warning would have landed exactly as written — a
    lint rule relaxed by default, permanently, to solve a problem that did not exist.

36. **B.8's cost argument against repeating the expression is unfounded on DuckDB 1.5.5.** The same spike
    measured the thrice-repeated form at **one evaluation per row**, not three: the planner splits the
    projection and computes the common subexpression once, visible in `EXPLAIN`. This does not change the
    resolution — the lateral alias states the intent in the SQL rather than depending on an optimiser pass
    — but the premise is recorded as false rather than left standing because the conclusion it supported
    happened to survive. Both facts are engine behaviour scoped to the pin, and
    `harness/test_duckdb_expression_semantics.py` fails on a DuckDB bump that folds differently, including
    a guard proving its own counter can tell two evaluations from one.

37. **The parity oracle's input was arriving over the network, from a mutable ref.**
    `splink_datasets.fake_1000` is not bundled with the splink wheel — attribute access
    **downloads** `data/fake_1000.csv` from `raw.githubusercontent.com` at `master`. Every baseline
    generated through that accessor would have depended on a live fetch from a ref that can change, and
    nothing would have noticed if it did: the manifest §20.1 specifies records the *model JSON's* sha, not
    the *fixture's*. §5 Stage 0.2's word is **"vendor"**, and this is what it is for. The file is now
    committed at `fixtures/source/fake_1000.csv` with its sha256, and 3.62 **verifies the hash on every
    run** — a recorded hash nobody checks is provenance in name only, the same defect class as a
    `[VERIFIED]` marker nobody re-earns.

38. **3.62 needed a `kind`, because three artefacts under `fixtures/` cannot share one field list.** A
    generated baseline records *how it was produced* (§20.1's eight fields); a vendored file records
    *where it came from* (source URL, sha, licence, copyright); a hand-authored degenerate corpus records
    *what it probes*. Requiring §20.1's fields of a vendored CSV would have been noise, and requiring none
    of them would have been vacuous — so the manifest declares its kind and the check dispatches on it. An
    unknown kind is an error, not a default.

39. **3.55 was half-enforced, and 3.39 could not see the missing half.** The row names two mechanisms —
    `detect-private-key` **and** a PII heuristic scan. The first was wired from the start; **the second did
    not exist**. 3.39 passed the row anyway, because `_as_hook` was reached only for `er-` prefixed tokens,
    so every *upstream* hook id the matrix cites read as prose and resolved by default — and the scan
    itself was named in prose, with no citation at all. Two fixes: the row now names
    `scripts/check_pii_heuristics.py` by path, and 3.39 resolves any hook-shaped token against the
    configured set, with a written, capped `_NOT_HOOK_IDS` list for dashed tokens that are not hook ids.
    Widening it surfaced two more rows worth checking properly rather than excusing: **3.4 and 3.18 cite
    yamllint rules** (`empty-values`, `key-duplicates`), so 3.39 now verifies those are *enabled* in
    `.yamllint.yml` rather than merely mentioned — §10.4 calls `empty-values` *"the gate contracts cannot
    provide"*, and nothing had been checking it was on.

40. **The PII control that works is a declaration, not a heuristic.** Every data file under `seeds/` and
    `fixtures/` must carry a manifest asserting `synthetic: true`. A regex cannot prove data is synthetic;
    a person can, and the check then holds them to it. The heuristics catch only what a declaration cannot
    — structured identifiers (Luhn-valid PANs, NI numbers, SSNs, IBANs), which should never appear even in
    synthetic data because a well-formed identifier is indistinguishable from a real one, and **consumer
    email providers as a blocklist rather than a domain allowlist**: `fake_1000` uses surname-derived
    domains like `humphrey.com`, which an allowlist would reject wholesale, while real person data
    overwhelmingly carries `gmail.com` and its peers.

41. **G5 is closed, and `log2` was the whole worry.** Appendix A measured "exact bit equality" in-process
    on darwin arm64; baselines are compared on linux/amd64 in CI. The finding named `log2` and `pow`
    specifically — libm calls whose build, compiler and vectorisation differ across platforms. `[RUN]` on
    DuckDB 1.5.5: **all five probe values are bit-identical across darwin/arm64 and linux/amd64,
    `match_weight` (the `log2`) included.** A.4's exact-bit-equality default holds, and none of its
    measured five orders of headroom is spent.

    **The deliverable is a gate, not a measurement**, because a measurement decays and this one is scoped
    to a pin. `harness/test_float_parity.py` asserts the committed reference on both platforms every run —
    locally on arm64, in CI on native amd64 — so two platforms agree continuously rather than once. The
    DuckDB version is asserted too, so a bump reopens the finding by failing instead of by being forgotten.
    Shown to fail: flipping one bit of the reference fails the run naming the column.

42. **Linear space and log space differ by 3 ULP, which is why D6/DR-06 exists.** The probe computes the
    same seven-factor product both ways: `product(bf)` gives `413498e8ffffffff`, `exp(sum(ln(bf)))` gives
    `413498e8fffffffc`. That is the effect A.4 addition 3 bounds at max |Δmw| = 2.842e-14 over all 2,880
    gamma vectors, observed directly. It is pinned as a test because a later "optimisation" to log space
    would look tidier, change every weight in the last few bits, and quietly break the exact-equality gate
    every parity claim rests on.

43. **A trap you cannot reconstruct is a trap you cannot re-verify.** Stage 0.5 set out to prove both of
    D4's documented traps fire on the pinned DuckDB. The **monotone guard** was straightforward: remove
    the `having` clause, run in a subprocess, watch it fail to converge. **v1's inverted driver was not.**
    D4 describes it in prose — *"propagates the smaller component label across edges via the `recurring.`
    pseudo-schema"* — and its decisive counter-recursion test has two substitution points whose full query
    text the document records results for but never prints. A reconstruction from that description
    **terminated in 0.5s**, which is a fact about the reconstruction and no evidence at all about v1.
    Asserting on it would have manufactured confidence. The test was removed and the gap written down
    instead. **The generalisable rule: when a document records a measurement's *result* but not the
    *artefact* that produced it, the claim cannot be re-earned later** — which is §0's marker problem
    wearing different clothes, and an argument for keeping the query next to the number.

44. **A bytes-per-pair figure needs three qualifiers, and the inherited one carried none.** Stage 0.6
    re-derives `er_max_pairs` from a measurement, and the measurement moved by **39×** — 17.1 to 665.5
    B/pair — across choices nobody had stated. **Units:** memory costs ~6× disk, and the published figures
    are memory figures (the measured compressible-narrow 104.5 reproduces §5's *"~100 B/pair"*), so
    measuring the size of the database file over-provisions by six. **Entropy:** DuckDB's dictionary and
    RLE compression works on `i % 97` and not on real names, costing ~1.5×; a capacity figure measured on
    tidy synthetic data is optimistic about production, which is the wrong direction for an OOM guardrail.
    **Row count:** per-pair cost falls from 280.3 at 25k rows to 140.8 at 800k as fixed overhead
    amortises, so a constant validated on a small sample is calibrated on per-block overhead rather than
    on data. The first draft of the test asserted at 50k rows and **failed**, which is how the third
    qualifier was found.

45. **The re-derived cap landed within 1% of the number it replaced, for different reasons.** 42,384,545
    against an inherited 42,000,000 — but the old figure was 40 GB at 946 B/pair (wide) and the new one is
    12 GiB at 152 B/pair (narrow). The constant was approximately right for the shipped configuration **by
    coincidence**, and would have been wrong the moment either premise moved. It would have been easy to
    present this as a correction; the honest description is that the *number* barely changed and the
    *derivation* is the deliverable. A cap nobody can re-derive is the same defect as a `[VERIFIED]` marker
    nobody can re-earn — it looks like a decision and is actually a leftover.

46. **A baseline's manifest should say what the fixture does NOT cover.** §20.1 lists eight provenance
    fields, all about how the artefact was produced. Generating the real thing surfaced a ninth need.
    `[RUN]`: `cluster_id` has **zero** nulls over `fake_1000` — Splink's NULL-node rows on dangling edges
    (`connected_components.py:89-100`) require an edge referencing an absent node, which a `dedupe_only`
    run over a single table cannot produce. The behaviour is not contradicted; **the condition for it never
    arises**. Without saying so in the manifest, a green predictions baseline reads as "NULL handling
    verified" when the path was never entered. `not_exercised_by_this_fixture` is now a required field, and
    a test asserts its content. Same argument as `pending_subjects.yml`, one layer along: **absence has to
    be declared, because silence reads as coverage.**

47. **The parquet writer is DuckDB, not pandas, and that is a dependency decision.** `pandas.to_parquet`
    needs `pyarrow` or `fastparquet`, neither of which is in §4's pin table — and §4's whole point is that
    the pinned set is small and parity-critical. DuckDB writes parquet natively, is already the engine both
    sides of every comparison run on, and is therefore the library that reads these files back. Adding a
    serialisation dependency to write files the pinned engine can write itself would have been the easy
    default and the wrong one.

48. **The frozen model was untrained, and no document said so.** §5 Stage 0.4 says to fix the model and
    names one defect — two missing blocking rules. `[RUN]` found a second, larger one: the model was
    **untrained**, and training alone lifts recall from 0.2354 to 0.4362 on *identical* blocking rules.
    A baseline generated from an untrained model is a baseline of Splink's defaults rather than of a
    model, and every parity number downstream would have been anchored to it.

49. **§A.6 Q5's quality figures do not reproduce on the vendored fixture, and the direction is what
    matters.** Measured: blocking recall **0.5057** against a stated *"0.51"* — reproduces. Precision
    **1.0000 at every threshold** — reproduces exactly. F1 **0.6075** trained against a stated 0.72 — does
    not. And the denominators differ: `fake_1000` contains **2,031** within-cluster pairs by plain
    arithmetic (1,000 records, 251 clusters, sizes 1–7), where the published figures imply **2,975**.
    Every *claim* survives — precision is saturated, so raising the threshold buys nothing and costs
    recall; adding blocking rules materially lifts quality; the gap is invisible to parity gates — while
    the *numbers* do not. **This is the case DR-22 was written for**: floors copied from a document would
    have been unmeetable, and nobody would have known whether the model or the floor was wrong.

50. **A Luhn check false-positives on any document full of high-precision floats.** The new PII scan
    flagged four "card numbers" in the trained model JSON. All four were digit runs inside m/u
    probabilities: `\b` does **not** break at a decimal point, so a mantissa offers a 16-to-19-digit run,
    and **~10% of random digit strings pass Luhn**. Fixed with lookarounds excluding runs adjacent to a
    digit or a dot, plus a major-industry-identifier prefix filter. The detector still catches
    `4111111111111111` and still ignores a non-Luhn 16-digit run — both asserted. A heuristic that fires
    on its own repository is worse than none: it trains people to skip the output.

51. **The reference fixture cannot exercise a precision regression, and the generator can.** `[RUN]`:
    precision on `fake_1000` is **1.0000 at every threshold** — it contains no pair that the model scores
    above the threshold and the ground truth denies. Every quality gate calibrated solely on it is
    therefore blind to precision loss; only recall can move. The seeded generator measures 0.9798 and
    0.9847 over two seeds, because its name pool is small enough that distinct personas resemble each
    other. **A fixture that cannot fail in a given way cannot defend against it** — which is exactly
    §12.7's argument about comparators, one level out. The two corpora are kept for different reasons and
    the manifests should say which.

52. **A gate shaped code that did not exist when it was written.** 3.55 blocklists consumer email
    providers, so the generator emits **surname-derived domains** — `humphrey.com`, `smith.net` — rather
    than the `gmail.com` a naive generator reaches for. Its test runs `check_pii_heuristics.check` itself
    rather than re-stating the rules, so a change to the detectors changes the test with them. A generator
    that tripped the repository's own PII scan would make the scan something people switch off.

53. **D6's "normative and closed" allow-list rejects Splink's own comparison library.** The first run of
    the §1.5 sidecar against the frozen model JSON failed on a stock `DateOfBirthComparison`:
    `ABS(EPOCH(try_strptime("dob_l", ...)) - EPOCH(...)) <= 2629800.0`. **`abs` is absent from the list**,
    though D6 states it surveyed *"all 21 comparison-level types in `comparison_level_library`"*. Added
    with the determinism argument D6 requires — `abs` is pure and reads no session or wall-clock state.
    The list was derived by reading; executing it disproved it.

54. **The allow-list and the parsed tree were in different languages, and on one list that meant a silent
    accept.** §1.5 mandates matching the sqlglot tree rather than the raw string, correctly — but D6's
    list is written in DuckDB's vocabulary. sqlglot canonicalises `EPOCH` to `time_to_unix` and
    `jaro_winkler_similarity` to `jarowinkler_similarity`, so **two allow-listed functions were being
    rejected**. Worse, `random()` parses to `exp.Rand` whose `sql_names()` is `['RAND', 'RANDOM']`:
    reading only the first alias yields `rand`, which was not in the non-determinism list, so
    **`random()` passed validation** — and §6.3's determinism claim would have been quietly untrue.
    `version()` → `CURRENT_VERSION` was the same. **On an allow-list a vocabulary mismatch fails noisily;
    on a deny-list it fails silently**, which is the asymmetry worth remembering. Fixed by testing every
    alias rather than the canonical name.

55. **Splink's else-level `sql_condition` is the literal string `ELSE`**, not SQL — five of the frozen
    model's 28 levels. Parsing it raises *"No expression was parsed from 'ELSE'"*, which reads like a
    malformed model rather than a validator that does not know the format. And **boolean operators are
    `Func` subclasses in sqlglot**: `exp.Or` reports `sql_names() == ["OR"]`, so an ordinary
    `a = b OR c IS NULL` was rejected as calling an unlisted function `or`. `exp.Connector` is what
    separates an operator from a call.

56. **`--check` silently overwrote the file it was meant to check.** The flag was added in the same edit
    that `ruff format` had already collapsed the write call onto one line, so the multi-line replacement
    matched nothing and the check branch was never inserted. `--check` therefore ran the *write* path:
    **exit 0, and the artefact it claimed to verify replaced instead.** Caught only because the success
    message it was supposed to print never appeared. This is the repository's own recurring shape — a
    verb that reports success while doing something else — reproduced by me, in the tool built to prevent
    drift. Now proven both ways: clean regeneration passes, and one altered probability fails with exit 1.

57. **A.2 C2's four measured cases reproduce exactly on splink 4.0.16.** `"name_r" = "name_l"` → exact;
    `NOT ("name_l" <> "name_r")` → exact; `"name_l" = "name_r" AND 1=1` → **not** exact;
    `lower(a_l) = lower(a_r)` → **not** exact. A string match would call two of those wrong in one
    direction and two in the other, which is precisely why A.2 forbids approximating it. **The sidecar
    calls Splink's own resolution rather than reimplementing it** — reimplementing the CNF analysis in
    Python would be the Jinja mistake one language along — and a test asserts structurally that it still
    does, so a future "simplification" that inlines the logic fails.

58. **M13's real content is a distinction, not a check.** *Absent* `m_probability` is valid input;
    *present and zero* is a hard error. Splink's save guard is
    `if self._m_probability and self._m_is_trained` — a truthiness test that drops `0.0` **and**
    not-observed alike — so an ordinary trained model legitimately omits the field, and M13 measured 3 of
    14 non-null levels missing it in a routine 400-row training. A validator that rejected absence would
    red the nightly model-varying job on a perfectly good Splink artefact, and M13's own failure scenario
    is what happens next: someone weakens the validator and loses the guard on genuinely malformed input.
    **And `1.0` exactly is valid** — the same production model carries three levels at `m_probability ==
    1.0`, which a naive open-interval `(0,1)` check rejects. Both directions are tested.

59. **M1 is reported, not rejected — and it is dormant on the only fixture that exists.** An asymmetric
    level is legitimate in a link job whose orientation is settled; what is not legitimate is not knowing,
    so the finding travels with the sidecar artefact rather than failing the build. It finds **zero** on
    the frozen `dedupe_only` model, exactly as M1 predicts (*"dormant until the first link job or the
    first `ColumnsReversed` level"*) — which means the check has no subject on the only model in the
    repository. Its tests supply one, using M1's own measured example: `ColumnsReversedLevel` renders
    `"forename_l" = "surname_r"`, and `symmetrical=False` is the **default**.

60. **The first real parity result, and it confirms D2 against v1.** `er_blocking_sql` renders SQL
    **token-for-token equivalent** to Splink 4.0.16's own `block_using_rules_sqls` output for the frozen
    model. That settles the v1 error D2 records from evidence rather than from reading: v1 described
    *"rule 2 `AND NOT` (rule 1)"* — one exclusion per preceding rule — and the real generator emits a
    **single** `AND NOT ( … OR … )` over every preceding rule. Four rules produce **three** exclusion
    clauses wrapping **six** `coalesce`d predicates, and a test asserts those counts, because the wrong
    shape still produces plausible pairs in plausible quantities.

61. **The snapshot is Splink's output, not a transcription — which is what makes it a parity test.**
    `fixtures/snapshots/blocking_fake_1000_v1.sql` was captured from the generator, so if Splink changes
    shape the snapshot changes and the macro is *shown* to have diverged. A snapshot written by hand from
    the document would only ever test that the macro matches what someone believed. It carries the same
    provenance manifest as a parquet baseline, and 3.62 now covers `.sql` for that reason.

62. **Jinja's `{% set %}` inside a `{% for %}` does not accumulate, and the result was valid SQL that
    scored every pair identically.** Building the bayes-factor product with an in-loop accumulator left
    `least(greatest(cast(<prior> as float8), 1e-300), 1e300)` — **the prior alone, with every factor
    silently dropped**. The SQL parses, runs, and returns plausible probabilities; it is the *"looks like
    a working model and is not one"* failure my own `ER-051` message describes, arriving through the macro
    rather than through its arguments. **Nothing in review would have caught it** — the divergence is
    invisible without executing against the oracle. The fix is `join`, and the test that found it is
    pinned. Second time this session that Jinja's scoping rules produced a silent no-op.

63. **§3.1's clamp figures are `log2(1e-300)` and `log2(1e300)`, not incidental numbers.** ±996.5784284662087
    is the floor and ceiling themselves. My first test transcribed §3.1's underflow figure and applied it
    to four factors instead of twelve, which gave −340.84 — correct arithmetic for those inputs, and a
    failing test for the wrong reason. **Asserting the property beats transcribing the number**: any
    product below `1e-300` scores at the floor, which is precisely what a log-space sum fails to do as it
    continues to −1006.54.

64. **I skipped a gate and CI caught it — which is the system working, and a claim I should not have
    made.** PD-1f ran pre-commit, pytest, mypy, `verify_gates` and `make build`, but **not `make
    bouncer`**, and the PR body said all gates were green. dbt-bouncer then failed on
    `check_macro_name_matches_file_name`: I had put `er_tf_divisor_sql` inside
    `er_tf_adjustment_sql.sql`, violating 3.33. **`make ci` exists precisely so the local set is not a
    subset chosen by hand**, and running the targets individually is how the subset drifts. The rule was
    right, the mechanism worked, and the reporting was the part that failed.

65. **`bf_tf_adj_` exists per comparison only where a TF adjustment does, and declaring it otherwise
    fails at RUN time.** `dob` has an exact-match level but no term-frequency column, so Splink's product
    is `bf_dob` with no `bf_tf_adj_dob` — while `city`, `first_name`, `surname` and `email` all carry
    both. A column list generated by the obvious symmetry would contract a column the SQL never produces,
    and dbt raises that inside the materialization (`adapters.sql:157-160`), long after review. The
    generated list is checked against **Splink's own predict projection order**, so it is a parity
    assertion rather than a restatement of my own derivation.

66. **§3.5's term-frequency denominator is confirmed, and the wrong version is silent by construction.**
    Splink divides by `count(<col>)` -- the **non-null** count -- and excludes NULLs from the numerator
    with a `WHERE` clause, so both sides cover the same population and the frequencies are a genuine
    distribution summing to `1.0`. Using `count(*)` is the natural mistake: every frequency comes out
    **proportionally** too small, so the ordering is unchanged and the values look entirely reasonable
    while the adjustment is systematically wrong. The test suite now **documents the size of the error**
    rather than merely forbidding it — on `fake_1000`, whose columns carry 17-21% NULLs, `sum(tf)` lands
    between 0.75 and 0.85 instead of 1.0. §3.5's own one-line test catches it directly, and it is far
    cheaper than comparing frequencies value by value.

**The third recurrence of one bug produced a shared helper.** `relative_to(ROOT)` raises when a scanned
tree is outside the repository — which is what 3.57's tests and `verify_gates.py`'s scratch copies both
build. Written twice, caught twice by the tests, and on the third script extracted to `scripts/_er_paths.py`
rather than written again.

#### The standard finding 4 asks for

> **3.72 — A `unit_tests:` block is at the top level of its properties file.** *Mechanism:*
> `scripts/check_unit_test_placement.py`, or an added rule in 3.69's `check_unit_test_fixtures.py`, walking
> every `.yml` under both projects' `model-paths` and rejecting a `unit_tests` key nested inside a `models:`
> entry. *Gate:* P + CI. *On violation:* non-zero exit naming the file and the model.
> *`verify_gates.py` injection:* nest a valid `unit_tests:` block under a model entry; assert non-zero exit
> and the expected string. **Not yet added to §3**, because §23 requires a new standard to ship with its
> injection in the same PR and `verify_gates.py` is step 4 of D.1. It lands there, and Waiver B-1 covers the
> interval.

---

### D.1 Bootstrap order `[UNVERIFIED]`

Everything above establishes that **nothing described in this document currently exists**. This section
states the order in which a greenfield repository stands it back up. `DesignDoc.md` §5 **Stage 0.9** is the
task that owns the rebuild; this is its sequence.

**What §2's gates do before any model exists: nothing, and that is the problem rather than the answer.**
The compile gate loops `graph.nodes` filtered to `resource_type == 'model'` and `package_name == 'dbt_er'`;
with no models it iterates zero times, collects zero violations, and passes. dbt-bouncer's coverage checks
divide by a node count of zero or skip. **A green run on an empty project is the "zero differences by
comparing zero rows to zero rows" vacuity §12.7 describes, one layer down.** So step 1 ships **one real
model**, satisfying the full §8.3 / §10 / §12.2 slice, precisely so every gate has a subject on the commit
that introduces it. A scaffold whose gates have never had anything to reject is not a verified scaffold.

**Whether `verify-gates` lands with the first commit: no, and it cannot.** Its own CI job is C.7 delta 6,
and 3.38 requires injecting each violation and asserting **the expected error string** — which needs the
rules to exist, be wired, and have observed failure text. It lands at step 4, and step 4 is the commit that
*earns* the scaffold its markers.

#### Waiver B-1 — bootstrap injections

§23 requires every new or changed standard to ship its `verify_gates.py` injection in the same PR. On a
greenfield repository all seventy-one rows are new across a handful of commits, so satisfying that rule
literally means either one unreviewable PR or a silent exception. This is the exception, stated:

> **Waiver B-1.** Standards 3.1–3.71 ship without their `verify_gates.py` injections across the bootstrap
> sequence. **All injections land in the step-4 commit**, which is the commit that adds
> `scripts/verify_gates.py`. From that commit onward §23 applies unwaived, and a row without an injection
> fails `verify-gates`.
> **Scope:** the bootstrap sequence only, per §18's one-legal-way rule.
> **Expires:** when the §3 matrix is **covered by injections**, not when `verify_gates.py` lands. The
> original wording said the latter, and executing it showed why that is the wrong trigger: the script
> existed and 14 of 71 rows were covered, which is progress and is not the rule being satisfied.
> `verify_gates.py` prints the covered count on every run, so the remaining gap is a figure rather than an
> impression. **Echoed** by the policy macro on every run until it closes.

That waiver is the reason the sequence is short. A longer bootstrap is a longer period in which nothing has
been shown to fire.

#### Which C-deltas apply before first run

| Delta | When | Why |
|---|---|---|
| C.1 1, 2, 4, 5, 7, 8, 9, 10, 11 | **Before first run** | Each changes a value or shape the policy macro reads at compile time. Applied later, the first run verifies a configuration that is already superseded |
| C.1 3 — `er_max_pairs` re-derived | **Trails** to `DesignDoc` Stage 0.6 | It is a *measurement*, and the fixture it measures does not exist yet. Ships as the v1 literal with a comment naming 0.6 as its owner |
| C.1 6 — `er_standards_exempt` shape | **Before first run** | The macro reads the mapping; the old list shape silently disables the per-check waiver |
| C.4 1–3 | **Before first run** | They remove the two guards D12 deleted. Shipping the guards and removing them later exempts every model added in between |
| C.4 4–11 (RC49) | **Before first run**, except 3.53 | These are §2.1's hardening — what makes the gate un-disarmable. 3.53's column budget names the two pair-grain models, so it trails to Stages 4–5 |
| C.6 1, 2, 3, 5, 6 | **Before first run** | Delta 2 especially: this repository has no `.gitignore` at all, and `.gitignore` alone loses to `git add -f` |
| C.6 4 — `er-baseline-manifests` | **Trails** to `DesignDoc` Stage 0.3 | No baselines exist to carry a manifest |
| C.7 1–5, 7, 9 | **Before first run** | Security and determinism properties of the workflow itself. Retrofitting a SHA pin after the workflow has run is a worse position than starting with one |
| C.7 6 — the eight new jobs | **Trails**, each with the machinery it gates | `parity` needs a harness; `comparator-sensitivity` needs Stage 0.7; `consumer-smoke` needs a published ref |
| C.7 8 — restore the observability artefact | **Blocked on B.7, then B.6** | Both are open, and applying B.6(a) first widens the §14.10 egress (RC52) |
| §11.2 deltas 1–2 | **Before first run** | The exemption list names a model that does not exist; left as-is, the real EM model fails lint |

#### The sequence

1. **Toolchain and skeleton.** `uv` project on Python 3.12, `uv.lock` with §4's exact pins, `.gitignore`,
   §5's layout, `dbt_project.yml` + `packages.yml` + `profiles/profiles.yml` with their before-first-run
   deltas, `integration_tests/` with its byte-identical `flags:` block — **and one real model**, per the
   vacuity argument above.
2. **The Makefile.** §17's six targets with real bodies, plus `make capacity` and the baseline target.
   §17's *"every Make target is also a CI step"* runs both ways, so this precedes the workflow: a workflow
   written before the targets it invokes is a workflow nobody can reproduce locally.
3. **Lint and pre-commit.** `.sqlfluff` and `.sqlfluffignore` moved out of §11 into real files under §23's
   canonical-home rule, `.yamllint.yml`, `check_no_nondeterminism.py`, `.pre-commit-config.yaml`.
4. **The four gates.** The policy macro with its deltas, `dbt-bouncer.yml` reconstructed per C.5, the custom
   check with 3.40's registration assertion, the enforcement scripts with their own failing-case tests
   (3.57), and `scripts/verify_gates.py` with every injection. **Waiver B-1 expires here.**
5. **CI.** `.github/workflows/ci.yml` with its before-first-run deltas and the four jobs C.7 specifies. The
   other eight arrive with the machinery they gate.

**Markers are re-earned, not restored.** Every `[VERIFIED]` block in this document describes a scaffold that
no longer exists; per §0 and 3.44 each is `[UNVERIFIED]` until re-executed on the §4 pins. Step 5's first
green run is what re-earns them, and anything that did not run stays marked.

**The scope of the verified markers.** Everything above was executed on dbt-core 1.12.2 · dbt-duckdb 1.11.0 ·
DuckDB 1.5.5 · dbt-bouncer 3.8.0 · SQLFluff 4.3.0 · yamllint 1.38.0 · dbt_utils 1.4.1 · ruff 0.16.4 ·
mypy 2.3.1. Per §0 and 3.44, a marker is scoped to
that toolchain and is demoted when a pin moves. The v2 edits did not rebuild the scaffold, so no marker was
re-earned and none was upgraded.

The last three names were added on 2026-08-23 when `check_verified_markers.py` was first run: each carried a
`[VERIFIED]` marker while appearing in no scope statement, so **nothing could ever have expired it**. That is
worse than a stale marker, because a stale marker is at least demotable. `dbt_utils` earns its scope through
`expression_is_true` executing in `dbt build`; `ruff` and `mypy` through pre-commit and CI. **`dbt_project_evaluator`
had the same defect and does not survive it** — it appears in no `packages.yml`, no Make target and no CI job, so
it has never run and its row is demoted to `[UNVERIFIED]` above rather than given a scope it did not earn. The
distinction is the whole point of the rule: three markers were real and undocumented, one was not real at all,
and before the check existed they were indistinguishable. *(Appendix D.0 finding 30.)*

`verify_gates.py` was the most valuable missing piece and is now **3.38** with its own CI job, rather than a
closing remark. A standard that has never been observed to fail is not known to be enforced: copy the repo to
a scratch directory, inject each violation in the §3 matrix, and assert a non-zero exit **and the expected
error string** for every one. The string assertion is the part v1's phrasing omitted — a check that fails for
the wrong reason is still broken, and is harder to notice later because the gate looks like it is working.
Every new §3 row ships with its injection in the same PR (§23); that pairing is what stops the suite decaying
into a subset of the matrix.

---

## Appendix E — v2 merge record

v2 merged a gap review conducted against this document and `DesignDoc.md`. 33 findings, all resolved here;
the review document was deleted after merging rather than kept as a parallel source of truth.

| Class | n | Where they landed |
|---|---|---|
| Contradictions with `DesignDoc.md` | 3 | §1.1, §7, §7.2, Appendix A.4 |
| Gates `DesignDoc` required that §3 omitted | 5 | 3.46–3.50, §12.7, §14.9, §20.3 |
| Self-consistency holes | 11 | §2.1, §6.1, §6.2, §10.5, §12.5, §18.1, 3.38–3.43, 3.51–3.53 |
| Absent lifecycle policy | 14 | §19–§24, §14.6, §14.10, 3.54–3.66 |

**What the review found that mattered most**, in order:

1. **§7 implemented a superseded decision.** `DesignDoc` D11 replaced Appendix A B1's ephemeral strategy and
   explicitly named this document's config as stale; §7, §3.7, §3.11 and §8.3 all descended from B1. The
   production default carried no contract on the two models Stages 4 and 5 exist to prove.
2. **§1's precedence rule pointed at the wrong answer**, because the superseded position *was* an Appendix A
   finding and the rule had only one populated tier. This is why (1) survived a full revision.
3. **Nothing proved the parity comparator could fail** (§12.7).
4. **The compile gate could be disarmed by the consumer it defends against, and could brick their build** —
   two faces of the same object, resolvable only together (§2.1).
5. **Three sections depended on performance history the CI topology cannot accumulate** (§14.6, B.6).

**Method note.** Every claim of absence in the review was verified with `grep -ic` across both documents
rather than asserted. Two candidate findings were withdrawn when the greps contradicted them. That is the
same discipline §0 describes for the `[VERIFIED]` markers, applied to a review instead of a configuration,
and it is worth repeating on the next pass: the failure mode of a document review is confidently reporting
that something is missing when it is three pages further down.

### E.1 Concurrent third pass on `DesignDoc.md`

`DesignDoc.md` gained an **Appendix B** — a third review pass, 21 findings (G1–G21), a cross-document
reconciliation (R1–R4) and a decision register (B.3) — while this merge was in progress. The two passes were
independent and agree on the material points, which is worth more than either alone: **G1** reaches §7 and
§1's precedence rule by the same route, and **G4, G5, G6, G12, G16, G20, G21** correspond to §20.4, §22.1,
§19.1, §19.2, §20.1, §24 and §23 respectively.

**Of B.2's four reconciliations, this merge closes three:**

| | Assigned to this document | Status |
|---|---|---|
| **R1** | §7 implements a superseded decision; the waiver machinery falls with it | **Closed** — §7, §7.1, §7.2, Appendix A.4, C.1 deltas 1–4 |
| **R2** | §1 cites §6.1 and A.4 as one source; A.4 is a strict superset | **Closed** — §1's routing row and the note above §1.1 |
| **R4** | `isfinite` CHECK against degenerate arithmetic; `bf_*` must be excluded | **Closed** — §8.4 |
| **R3** | Stage inventory: `DesignDoc` §5 against A.5 | **Not ours.** Both inventories live in `DesignDoc`; reconciling them is a `DesignDoc` edit |

**What Appendix B raises that this document does not yet answer.** Registered here so it is not lost, not
merged, because each needs a decision rather than a drafting pass — B.3 marks all four **MISSING**:

- **G3 / DR-17 — the model JSON is untrusted SQL executed with the consumer's credentials.** D6 passes
  Splink's rendered SQL through verbatim, and §9 ingests it via `env_var`. There is no trust boundary. This
  is the most serious finding in either pass and it is absent from this document entirely — §11.3's
  non-determinism scan covers *our* macro code, not the JSON's payload.
- **G2 / DR-16 — the package's entry point and input contract are unspecified.** §19.1 enumerates the API
  surface as a *policy*; G2 observes there is no specified input contract for it to describe.
- **G5 / DR-19 — the parity claim's validity domain.** §22.1 scopes which *gates* are platform-anchored;
  G5 goes further and notes **committed baselines cross platforms in CI**, which §20.1's lifecycle does not
  address.
- **G11 — sqlglot is parity-critical and appears on no pin list**, including §4's.

> **[REVIEW 2026-08-23] Fixed (F27) — RC54's residue is closed.** `DesignDoc` Stage 0.1 already pinned
> sqlglot and §4 gained its row in this pass; the part that remained open was that **neither §14.9(b)'s run
> manifest nor §20.1's baseline manifest recorded a sqlglot version.** Both now do, and both also gained the
> **platform triple** G5 needs and RC21 recorded as still missing — the same class of omission, found by the
> same argument. `DesignDoc` §5 Stage 0.3 carries the matching requirement on the baseline format.
>
> **G11's remaining half is not a drafting fix and is recorded at `DesignDoc` §5 Stage 0.1:** sqlglot's
> resolved *upper* bound comes from `dbt-bouncer` (`>=25,<31`), not from Splink (`>=17.6.0`), so a routine
> lint-tool bump can move a parity-critical dependency. `dbt-bouncer` joins the exact pins on Dependabot's
> ignore list for that reason (§16).

The first is the one to take next. The others are drafting; that one is architecture.
