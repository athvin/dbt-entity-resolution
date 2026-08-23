# Stage inventory — the union of §5 and A.5

> **Status: RECONCILED, 2026-08-23. This file is now a secondary source — read `DesignDoc.md` §5 instead.**
>
> **DR-11 is CURRENT** and R3 is closed: §5 absorbed A.5 and is the single inventory. A.5 is retained in `DesignDoc.md` as evidence and is stale wherever the two disagree.
>
> Three things this file still records as open were *decided* by that reconciliation. **Stage 2b** closes as the explicit non-goal — v1 is full-rebuild, `is_incremental()` and the record-lifecycle machinery move to v2 together (§5 Stage 8). **Stage 4's** AC relaxes to *reachable* threshold constants. **`entity_clusters_1to1`** is tagged v2. **DR-17 and DR-16 have since closed too** (both CURRENT 2026-08-23): the model JSON is untrusted input validated once at compile time in the sidecar (`DesignDoc.md` §1.5), and the input contract is §2.0. **B.1 / DR-13 has since closed too** (CURRENT 2026-08-23): the harness reads only parquet and never opens the database; dbt keeps a file database. The per-stage blocking decisions below that are **still open** — B.8, DR-08, DR-09, DR-12 — still block. Check `docs/backlog/LOOP-STATE.md` for live status before trusting any "blocked by" line here.
>
> **Where this file and §5 disagree, §5 is right.** It is kept because its sizing table, its per-stage traps and its reusable-oracle pointers are planning material that does not live in §5 — not because it is a second inventory. Prefer §5 for *what the stages are*; use this for *what to watch out for*.

This file was assembled as the *union* of the two inventories, so a planner saw the whole superset instead of silently planning from whichever list they opened. Every row states which document it came from.

Precedence, when you must pick one to quote (`DbtBestPractices.md` §1.1): measured `[RUN]`/`[RECON]` findings in Appendix A → numbered decisions D1–D12 **and the §B.3 register** → the companion → habit. Within `DesignDoc.md`, the body is normative and appendices are evidence, **with §B.3 carved out by name as normative for decision *status***. That body-over-appendix rule is *why* A.5's changes were not automatically in force, and why DR-11 was a conflict rather than an obvious win for A.5.

---

## Critical path and parallelism

**Critical path: `1 → 3 → 4 → 5`** (A.5, restating M21). Stage 1 is the critical path because `load_model_json` owns five recomputed values (D1) and *every downstream baseline is only meaningful once that reader is right*.

**Parallel from day one: Stages 6, 7, 10 and 12b** build from injected baselines. A.5: *"that decoupling is the single largest schedule lever in the document"* — and A.5 explicitly says to state it in §5, where it still is not. It depends on M4's per-model injection mapping; the six-line mechanism currently in §5 is the defective form (RC12) — a single global boolean that injects at every model at once, which A.5 calls "the one configuration that tests nothing".

**Sizing (M21, marked UNVERIFIED — reasoning from verified complexity, not measurement):**

| Class | Items |
|---|---|
| **Days** | Stage 0 scaffolding · Stage 2 · `int_edges` · `train_m_from_labels` (one GROUP BY) · Stage 10 aggregates · `node_metrics`/`cluster_metrics` · `int_deterministic_links` |
| **Weeks** | Stage 1 (five recomputed fields) · Stage 3 (four WHERE arms, the coalesce chain, exploding rules as CTEs, VARCHAR `match_key`) · Stage 5 (linear product, clamp, TF adjustment, degenerate params) · Stage 11 + harness |
| **Multi-week spike, needs a written kill criterion** | D5 EM in SQL · `is_bridge` (biconnected components, no oracle) · the iteration guardrail |

M21's core complaint is that three items are **spikes with real failure probability, not tasks**, and they carry the same visual weight as `int_edges` (one `WHERE` clause). Never size a spike as a task.

---

## Stage 0 — Scaffolding, fixtures, oracle, spikes

| Sub | Source | Content |
|---|---|---|
| **0.0** | RC8 proposal — in neither list | **Pre-flight.** Sequences the five competing "do this first" directives. See the `er-backlog-preflight` skill. |
| 0.1 | §5 | dbt-duckdb project; pin `splink`, `duckdb`, `dbt-core`, `dbt-duckdb` **and `sqlglot`** exactly; `make` targets. sqlglot arrives transitively and is parity-critical — it, not Splink, decides which levels get a TF adjustment (A.2 C2, G11). |
| 0.2 | §5 | Vendor `fake_1000`; seeded synthetic generator. |
| 0.3 | §5 + **A.5 extends** | `gen_baseline.py` dumping every intermediate as parquet with a manifest. Baselines generated from a model JSON **saved and reloaded** (§3.4). A.5 adds: normative on `retain_matching_columns=True` **and** `retain_intermediate_calculation_columns=True` (M14, not Splink's default), ground-truth labels (M12), and **training traces** for Stage 9 (B5). **B.1 / DR-13 is closed** (CURRENT 2026-08-23): the harness reads only parquet, so this script's parquet baselines and `integration_tests/`'s `COPY` post-hook exports are both sides of every comparator. |
| 0.4 | §5 | Freeze `model_jsons/fake_1000_v1.json` + baselines. **Must follow 0.7**, per §12.7. |
| 0.5 | §5 | **Clustering spike — resolved, retained as a regression gate.** D4 must reproduce a union-find partition on random, chain and star graphs with recorded runtimes. Re-runs on every DuckDB bump. |
| **0.6** | **A.5 only** | Materialisation & capacity spike: measure B/pair for the fixture model, publish `er_max_pairs`. The *decide `ephemeral` vs `table`* clause is `[SUPERSEDED by D11]`; **the measurement stands** and D11's follow-through requires it (`make capacity`). No §5 stage schedules it (RC29). |
| **0.7** | **A.5 only** | **Comparator sensitivity suite** (M10). §12.7: the one standard that must be built *before* the thing it guards. Mutant catalogue is given verbatim in §12.7 — 8 mutants, no survivors permitted, each asserting the **expected localisation string**, not merely failure. Sized at one day; "the cheapest credibility available". |
| **0.8** | RC46 proposal | `EXPLAIN ANALYZE` spike for B.8 option (c) (DuckDB lateral column alias — does it evaluate once?). Without it, option (a) is adopted untested by default. |
| — | A.5 | Frozen **model library matrix** (M13). |
| — | RC8 item 4 | **Rebuild the deleted engineering scaffold** from `DbtBestPractices.md` Appendix C with the v2 delta tables applied. Appendix D records the deletion; Appendix C is currently the only copy. **In no task list in either document.** |

**§5 AC:** `make baseline` is hash-stable across two runs; the D4 gate is green with published timings.

---

## Stage 1 — Model JSON ingestion & SQL generation · CRITICAL PATH · weeks

**§5:** `load_model_json` (D1, five recomputed fields); `blocking_sql` (D2); `comparison_vector_sql` (§3.3); `bayes_factor_sql` + `tf_adjustment_sql` (§3.1–3.2).

**A.5 extends:** the **compile-time sidecar** (§A.2) — a generated, committed, hashed artefact resolving what Jinja cannot: `comparison_vector_value`, resolved `m`/`u` after Splink's defaulting, `tf_u_exact_match`, `er_backend_link_type`, `er_has_source_dataset`, `er_left_table`. Guard with a byte-equality regeneration test. Plus lints: asymmetric-level detection (M1), `output_column_name` uniqueness post-normalisation (M2), `set()` ban (M15), `m == 0`/`u == 0` **hard error** (M13). Publish `er_gamma_columns`/`er_bf_columns` as vars (M2).

**§5 AC:** rendered SQL for the fixture model matches reviewed snapshots; malformed JSON fails compilation with actionable errors; a level with `m_probability` absent renders `_default_m_values`, **not NULL**; `dbt compile` output has zero Jinja residue and reproduces `cast(… as float8)` wrappers.

**Blocked by:** **B.8** only — the snapshot AC reviews rendered scoring SQL containing D11 rec 4's subquery, which §11.1's `forbid_subquery_in = both` forbids (RC46).

**DR-16 is closed** (§2.0) and adds a Stage 1 deliverable: the compile-time check that every column the model JSON references appears in `er_input_columns`. It is the same pass that emits `er_gamma_columns`, and it turns a `Binder Error` from inside a generated `CASE` into an error naming the column.

**DR-17 is closed and is now a Stage 1 *deliverable* rather than a blocker.** §1.5 makes the sidecar the trust boundary's enforcement point: a closed allow-list checked against the parsed tree, non-deterministic functions and subqueries rejected, the input bounded, and `er_model_sha` as the hash of the *validated* artifact — a JSON that has not passed the sidecar has no sha and does not build. Five negative tests ship with it.

---

## Stage 2 — Staging & term frequency · days

**§5:** `stg_input` (D8, **bare passthrough**); `tf_all` (D7 shape, **D7a semantics — frozen by snapshot**, keyed `(er_model_sha, er_tf_snapshot_id, column_name, value, tf)`).

**A.5 (same decision, stated as a change):** `tf_all` reads a frozen snapshot by default; live-corpus TF is the opt-in `er_tf_mode='refresh'` snapshot-minting path. Building it as a live aggregate and freezing later changes the model's grain, its contract, and every downstream join key — this is the expensive order.

**§5 AC:** exact TF parity per column including the **non-null denominator** (§3.5) — assert `sum(tf) = 1.0` per column · `stg_input` equals Splink's concat **excluding `__splink_salt`** (S1) · a value in the corpus but absent from the frozen snapshot **raises**, with a test proving it raises — do **not** `COALESCE` · re-scoring a pair under unchanged `(er_model_sha, er_tf_snapshot_id)` yields a **bit-identical** `match_weight` after unrelated records are appended.

That last AC is what makes frozen TF meaningful rather than decorative, and it is cheap now and awkward to retrofit.

---

## Stage 2b — Record lifecycle · A.5 ONLY · half-triggered

**A.5:** New, **or an explicit non-goal**. If `is_incremental()` ships in Stage 8, this must exist: `is_deleted`/`valid_to` on `stg_input`, an `edges ⊆ nodes` referential-integrity test, and an explicit reap step. The cheap correct v1 choice is to declare all models `table` and put `is_incremental()` out of scope. **What is not acceptable is Stage 8 shipping `is_incremental()` with neither.**

**That is the current state** (RC10/RC15). D11 has since decided all-`table` — 2b's "cheap correct v1 choice" in all but name — yet body Stage 8 still ships the incremental path M8 specifies, and neither section mentions the other. Resolve with Stage 8, not separately.

*Why: dbt's `delete+insert` cannot remove a key the SELECT excludes — the incumbent hit this exact trap and needed a post-hook.*

---

## Stage 3 — Blocking · CRITICAL PATH · weeks · highest-risk parity stage

**§5 AC:** exact `(unique_id_l, unique_id_r, match_key)` set equality with `match_key` compared **as VARCHAR** · per-rule pair counts · adversarial fixtures for overlapping rules, NULL-heavy keys, and **empty-string keys** (D2).

**A.5 extends:** blocking **recall** with `er_blocking_recall_floor` as a two-sided guardrail (M12) — *recall loss here is unrecoverable downstream and is currently ungated*. Replace the ported `max_rows_limit = 1e9` with the **byte-derived budget** (B1: Splink's limit is *per-rule*; at this design's measured 946 B/pair it would admit a 946 GB build). Restrict to the supported-configuration matrix (Stage 12.1), which de-risks this stage materially.

**Ready-made regression fixture:** the incumbent's `int_blocking_keys_union.sql` emits `where {expr} is not null and {expr} <> ''`. Splink applies no such filter and DuckDB treats `'' = ''` as true, so two empty-string keys **do** form a Splink pair. The `is not null` half is a correct no-op; the `<> ''` half is a real one-directional divergence.

**Reusable oracle — do not rebuild:** `tests/helpers/pairs.py::splink_blocked_pairs` in the incumbent is a working Splink blocking oracle via `deterministic_link()` — exactly this stage's oracle (A.3 Group 3).

---

## Stage 4 — Comparison vectors · CRITICAL PATH

**§5 AC:** 100% gamma equality · a boundary fixture for **every distinct threshold constant** in the model JSON (catches `>` vs `>=`) · a fixture where JSON list order and gamma order disagree (catches §3.3).

**A.5 — DIRECT TEXTUAL CONFLICT:** relaxes "every distinct threshold constant" to **reachable** constants, with the unreachable ones documented. Adds fixtures for null-level-not-first, no-ELSE-level, no-null-level (M14). RC29 flags this as the one *direct* conflict in R3's scope, as opposed to an omission.

---

## Stage 5 — Scoring · CRITICAL PATH · weeks

**§5 AC:** parity per §6 (→ implement from **A.4**) · per-comparison `bf_<name>` and `bf_tf_adj_<name>` emitted for localisation, which needs `retain_intermediate_calculation_columns=True` in the baseline — **not Splink's default** · fixtures covering `m=0`, `u=0`, not-observed levels, missing TF entries, and the clamp region.

**A.5:** unchanged in substance; enforce the single-evaluation `_bf_clamped` column (B1 rec 2).

**Blocked by B.8.** §11.1 concedes that until B.8 is decided, `er_int_scored_pairs` **cannot be written to satisfy both rules at once** (§7.3's CTE ban vs `forbid_subquery_in = both` vs float-parity's rejection of repeating the expression). One of the three must give.

**Arithmetic contract:** linear space, then **one** `log2` (§3.1, DR-06) — not a log-space sum. Splink's clamp applies.

---

## Stage 6 — Clustering · parallelisable from day one

**§5:** `int_edges` (threshold, **`>=`**); `entity_clusters` (D4 `WITH RECURSIVE … USING KEY`, monotone min-label); `entity_clusters_1to1`; `node_metrics`/`cluster_metrics`/`edge_metrics` (§3.6).

**§5 AC:** **Label** parity, not merely partition parity, at thresholds {0.5, 0.9, 0.99} · singletons present; ghost-node and NULL-endpoint fixtures assert we emit **nothing** where Splink emits a spurious NULL-id row · **thread-determinism gate**: same graph at `threads=1` and `threads=8`, ten runs each, one content hash — the only test that catches a missing `GROUP BY` (D4 trap 1) · an **iteration cap**, not a pre-flight diameter estimate (M5: computing diameter is as expensive as the clustering, and every cheap proxy is anti-correlated with cost by ~200×) · runtime recorded **against Splink's own time on the same graph** — per D4a we expect to be slower; the criterion is that the ratio is known and does not regress.

**A.5 extends:** `int_edges` → `edges_by_threshold`; `entity_clusters` → composite key `(thr, unique_id, entity_id)` (M16 — the ACs require three thresholds *simultaneously*). Absolute runtime gate alongside the ratio (M11). Per-model ACs and a **Python union-find oracle** for `is_bridge` and the 1:1 tie semantics (M11). Max-cluster-size gates (M12).

**Blocked by DR-09** (one threshold or a gray band — marked *blocks Stage 6* in the register) and **DR-08 / companion B.2** (threshold as a var or a dimension — this changes the *contract* of `er_int_edges` and `er_entity_clusters`, not just their SQL).

**RC9:** `entity_clusters_1to1` is **dead code** under Stage 12.1's v1 matrix — `cluster_using_single_best_links` is defined over source datasets, and the matrix forbids `source_dataset`. Tag it v2 or state why it survives.

**Performance, stated honestly (D4a):** 3.4–18.5× slower than Splink's Python loop, worst at the largest scale. Chains: 10k = 63–207 s, 20k = 523 s, 100k infeasible. `USING KEY` is memory-resident and does not spill — it OOMs rather than degrading; working set ≈ 10–20× base tables. D4b (custom `iterative_fixpoint` materialization with pointer jumping) is **prototyped and working** at 0.92 s vs 206.63 s on a 10k chain, and is sequenced *after* Stage 6 (DR-05). Adopting it cannot cost parity, because the min-label fixpoint is unique.

---

## Stage 6b — Entity identity · A.5 ONLY

**A.5:** New, **or rename**. Either build `entity_keys` + `cluster_lineage` + `entity_events` (adopt the incumbent's `INV-PERM`), or rename the column to `component_label` and remove it as a key from downstream models. *"Choosing 'rename' is legitimate and cheap; leaving it ambiguous is not."*

**DR-12 is OPEN with a fired trigger** (RC16). It says "decide with DR-14", and DR-14 is CURRENT as of 2026-08-20. §A.6 Q1 already declares the consequences "binding, not conditional" — including that `entity_id` becomes `component_label` — while the body still emits `entity_id` everywhere (D4, D10's `PARTITION BY entity_id`, §2, Stage 6) with no pending-rename marker. Pick one state.

*Why it matters (M6): `entity_id` is emitted as an identifier while §1.3 disclaims identity, and it relabels 100% of a cluster on one insert.*

---

## Stage 7 — Survivorship & golden records · parallelisable from day one

**§5:** Per D10, including the multi-column-attribute rule. **No Splink oracle** — hand-built fixtures, property tests, and a row-order permutation test.

**A.5 extends (M19):** rule chains, field groups, multi-valued output, an **unmergeable-conflict path**, config validators, per-field-group property test. M19's complaint: survivorship is single-strategy-per-attribute, drops multi-valued attributes, and has no unmergeable-conflict path.

**G10:** `golden_records` still has no declared grain.

---

## Stage 8 — Incremental · CONFLICT with D11

**§5 claim, corrected in the same section:** one blocking query with `where a.is_new or b.is_new` covers both cases — but **it does not make the run cheaper**. It still evaluates every blocking rule over the whole corpus; measured, **≥ 100% of a full rebuild** (B4). Incremental cost must come from restricting the *blocked* side.

**A.5:** two explicitly-driven joins, not the disjunctive predicate; **`< 10% of full` as a measured AC** (B4). `er_model_sha` + `er_tf_snapshot_id` on every row with single-value tests (M8 — an incremental `int_scored_pairs` otherwise silently mixes scores from two model JSONs and two TF snapshots). Optional `er_assertions`/`er_cut_edges` inputs (M20).

**§5 AC:** 80/20 split equivalence when no merges occur; merges flagged, never silently mis-clustered; frozen-TF approximation documented and bounded.

**RC10 — unresolved:** D11 decides `table` everywhere and drops `ephemeral` from `er_allowed_materializations` **without carving out `incremental`**, yet this stage ships the incremental path. Either D11 carves out `incremental` and Stage 2b's lifecycle requirements come due, or Stage 8 is restated as a full-rebuild flow and its incremental framing moves to v2.

---

## Stage 9 — Training · SPIKE, needs a kill criterion

**§5:** 9.1 `train_prior`; 9.2 `train_u` (D9, seeded, reproducible); 9.3 `train_m_from_labels` (one GROUP BY — *days*); 9.4 `train_em` (D5) with per-session column removal, blocking-adjusted λ, and **median** combination.

**§5 AC — DO NOT USE (RC11).** It requires "EM within 1e-4 of Splink's on the same blocking pass with the same iteration count". B5 proves this unfalsifiable: the training oracle is not a function of (data, seed) — measured max |Δ match-weight| = **1.63** under Splink's default `seed=None`; the iteration count is **unobservable**; and 1e-4 equals Splink's own `em_convergence`, so a sub-tolerance parameter difference moves the early stop by one iteration and produces a supra-tolerance difference in every parameter.

**A.5's replacement AC:** compare **per-iteration trajectories** against a committed training trace; **require `seed`**; assert cap-vs-converge explicitly. The trace must be captured in Stage 0.3 — retrofitting after 0.4 freezes the baseline format is the expensive path.

*Under the body-is-normative rule, the unachievable AC is currently the normative one.*

---

## Stage 10 — Evaluation & diagnostics · A.5 MOVES IT EARLIER

**§5:** `eval_accuracy`, `eval_errors`, `eval_unlinkables`, `diag_comparison_vector_distribution`, `diag_match_weights_histogram`. AC: confusion-matrix parity against `accuracy_analysis_from_labels_table` on a labelled fixture.

**A.5 — split it (M12).** The **measurement models** build immediately after Stage 2 (they need only labels and scores) so their outputs can gate **Stage 3's recall floor** and **Stage 6's quality tests**. The **parity AC** stays at Stage 10. *"A quality stage that runs after the stages it should gate cannot gate them."* This is M12's central point and it is inert while Stage 10 stays where it is.

**Why this is not optional (M12):** the frozen fixture model measures **F1 = 0.72, blocking recall = 0.51** on `fake_1000`. A two-rule change lifts F1 to 0.98. **Parity gates cannot see the difference.** Without an owner for the quality floor, Stage 10 is a reporting stage and the product ships at 0.72. §A.6 Q5 asks who owns (a) committed per-fixture F1 and recall floors, (b) the justification for `er_threshold`, (c) whether the gray band is two thresholds or one.

---

## Stage 11 — The differential loop · weeks (with harness)

**§5:** nightly randomized seeds → both engines → compare every stage → scoreboard; failures freeze into `fixtures/regressions/`. **Vary data against a frozen library of model JSONs** — v1 trained a new Splink model per seed, which makes a failure un-attributable between the model, the data and the SQL. Model-varying runs are a separate, explicitly-labelled job.

**A.5 extends:** both-modes CI rule; **per-model injection mapping**; baseline↔JSON hash binding (M4). Failure-bundle schema + a **bundle-reproduces** CI job (M18). Split DoD item 3 into parallel correctness + concurrent stability (M18: "ten green nightly differential runs" is an uncompressible ≥10-day tail, and a failure is not reproducible).

---

## Stage 12 — Cutover

`grep -ic` over v2 returned **0** for `cutover`, `shadow`, `rollback` and `migration`. Delivery is a **swap** — the highest-risk operation in the programme — and every prior stage proves correctness against *fixtures*, not the production corpus.

- **12.1 Supported-configuration matrix.** State what v1 supports and **fail compilation on anything else**: `dedupe_only`, VARCHAR `unique_id`, no `source_dataset`, plain equi-join blocking rules, no `arrays_to_explode`. It matches the incumbent exactly, which makes every hard case in D3 and S2 **dead code for the actual migration target** and deferrable to v2. `salting_partitions` can be **ignored** rather than errored — pair sets and `match_key`s are provably invariant under salting (verified: 3,281 pairs identical).
- **12.2 Shadow run.** Both engines on production data, diffing at the platform boundary: edge set at the auto-merge threshold first, then the cluster partition.
- **12.3 Numeric go/no-go**, not a judgement call: symmetric difference of the edge set = 0; partition delta ≤ N entities, **N stated in advance**.
- **12.4 Rollback switch and its trigger criteria**, documented and **exercised at least once**.

**DR-15 is CURRENT but "not propagated"** — G18 catalogues the dead code the matrix creates and its list is already known to be incomplete (RC9).

---

## Stage 12b — Provenance & observability · A.5 ONLY

**A.5:** `er_run_id` stamped everywhere; `_er_run_manifest` via `on-run-end`; per-run perf artefact; named owners for `divergence-log.md` and `PARITY.md` with a CI check that every deliberate divergence has both a log entry and a pinning test.

*"Three of the document's own requirements (§6.2, §6.4 Performance, §7 Q1, DoD 4–5) currently belong to no stage."* Parallelisable from day one.

---

## Definition of Done (§8) — and what it omits

1. Stage 0–11 acceptance criteria green in CI.
2. `dbt build --vars "{er_model: …, er_threshold: 0.9}"` on a fresh clone produces golden records end-to-end with **zero Python in the dbt run** — narrowed: the *run* is Python-free, but TF exact-match-level detection (sqlglot CNF) and backend `link_type` selection are not arithmetic and belong to the compile-time sidecar. "Zero Python anywhere" is not achievable and is not claimed.
3. Ten green nightly differential runs.
4. A divergence log documenting every Splink subtlety found, each pinned by a test — including the deliberately-replicated `min(match_key)` VARCHAR bug (S4).
5. `PARITY.md` stating, with evidence links, exactly what is identical and what is bounded — using **A.4's** policy.

**RC14:** item 1 says "Stage 0–11" and **omits Stage 12**, which the same revision added and calls the highest-risk operation in the programme. Either extend to 0–12, or state why cutover sits outside "done" (defensible: Stage 12's AC is not CI-checkable, so the package is done before the migration is). Item 3 also keeps the serial ten-nightly calendar gate M18 recommends splitting.
