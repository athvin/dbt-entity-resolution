# GOAL — what `dbt-er` is for

**Status:** north star · **Date:** 2026-08-23
**Reference implementation being replaced:** [Splink 4.0.16](https://github.com/moj-analytical-services/splink) (MIT)
**Target runtime:** dbt-duckdb · DuckDB ≥ 1.5.5 · dbt-core ≥ 1.12.2

---

## The goal, in one sentence

**Reimplement Splink's entity resolution as a pure-SQL dbt project — declarative models a data team can
read, test and version — using recursive CTEs to express the iterative algorithms that today require a
Python driver loop, and proving the result correct by differential testing against Splink itself.**

Splink is a Python library that *emits* SQL and orchestrates it from Python: it materialises temp tables,
inspects them, interpolates new parameters as literals, and issues the next query. Every iteration is a
different query, and the loop lives outside the warehouse. We are keeping the SQL and deleting the driver.

## What "pure SQL in dbt" means here

| Property | What we are shooting for |
|---|---|
| **Declarative** | The pipeline is a dbt DAG of models. What each model computes is stated once, in SQL, and dbt decides when to build it. No orchestration script decides what runs next. |
| **No Python at run time** | `dbt build` executes SQL only. The trained model JSON reaches the project through an environment variable and is parsed at **parse time** (`DesignDoc.md` D1); every column name is a pure function of that JSON, derived before the first query runs. |
| **Inspectable** | `dbt compile` yields static SQL with no Jinja residue. Any model can be pasted into a DuckDB shell and run. |
| **Deterministic** | Two runs over the same input produce the same content — total ordering everywhere, seeded sampling, no wall-clock or `random()` reachable from the scoring path. |
| **Tested like data, not like code** | Contracts, constraints, schema tests and per-model unit tests, plus a parity harness that diffs us against Splink on identical inputs. |

Honest boundary, stated up front so nobody has to discover it: **"zero Python anywhere" is not the goal and
is not achievable.** Two of Splink's resolutions are not arithmetic — deciding which comparison level is
the exact-match level for a TF adjustment (a sqlglot CNF analysis) and backend `link_type` selection (a
runtime table count). Those run **once, at compile time, in a sidecar** that pre-resolves the model JSON.
The dbt *run* is Python-free; the toolchain is not (`DesignDoc.md` §8 DoD 2, §A.2).

## Recursive CTEs are the load-bearing bet

Splink has three genuinely iterative surfaces. All three are expressible as a **single**
`WITH RECURSIVE … USING KEY` statement on DuckDB, and that has been measured, not assumed:

| Iterative surface | Our model | Status of the bet |
|---|---|---|
| Connected components (`cluster_pairwise_predictions_at_threshold`) | `entity_clusters` | **Solved.** Delta-driven, monotone min-label, label-identical to Splink — not merely partition-identical. See `DesignDoc.md` **D4**. |
| Expectation-maximisation parameter training | `train_em` | **Solved.** Full EM — E-step, M-step, normalisation, λ update, convergence test *and* iteration cap — as one statement, matching a Python reference to 3.9e-16 with an identical iteration count. See **D5**. |
| Mutual-best-link / one-to-one clustering | `entity_clusters_1to1` | Expressible; deferred behind the v1 supported-configuration matrix. |

Everything else — **17 of the 20 Splink surfaces** — is ordinary non-recursive SQL: blocking, term
frequency, comparison vectors, Fellegi–Sunter scoring, graph metrics, u-estimation, m-from-labels,
evaluation and diagnostics. The recursion budget is small and bounded by design.

**The bet's known cost, stated as a goal constraint rather than discovered later:** the single-statement
recursive CTE is *correct and deterministic but slower* than Splink's Python-driven materialised loop —
3.4× at 200k nodes, 18.5× at 3M nodes / 10M edges. "Pure SQL is faster" is not a claim this project makes.
What it buys is correctness, determinism, zero Python, and one inspectable statement. Where that trade
stops being worth it, the escape is a custom `iterative_fixpoint` materialization that lets **dbt** drive
the iterations instead of Python — prototyped and working (**D4a**, **D4b**). The recursive CTE then
becomes the reference oracle the fast path is tested against, which is a schedule decision, not a
re-architecture.

## The definition of success

1. **A fresh clone runs the whole pipeline.** `dbt build` with a trained model JSON in the environment
   produces candidate pairs, comparison vectors, match weights, edges, clusters and golden records
   end-to-end, with zero Python in the run.
2. **Parity with Splink is demonstrated, bounded, and published.** Differential testing on identical
   inputs, under one tolerance policy (`DesignDoc.md` **A.4**), with a `PARITY.md` that says exactly what
   is identical and what is bounded, and evidence links for both.
3. **Every deliberate divergence is logged and pinned by a test** — including Splink's `min(match_key)`
   VARCHAR bug, which we **replicate on purpose** because parity demands it, and the spurious NULL-keyed
   cluster rows, which we drop on purpose because they are wrong.
4. **Every model has unit tests, written with the model** — not retrofitted at the end to turn a coverage
   gate green (**D12**).
5. **The gates enforce the above without a human remembering to.** Compile, pre-commit, build and CI, per
   `DbtBestPractices.md` §2.

## What parity can and cannot mean

Splink is **the oracle, not the ceiling**. Four things make blanket bit-parity literally unachievable, and
naming them is part of the goal, not a caveat on it (`DesignDoc.md` §1.2):

- **S1** — Splink adds a random salt column to its concat table unconditionally on DuckDB. Its concat is
  not byte-reproducible; ours is.
- **S2** — For two-table links, which side is "left" depends on a runtime table-alias comparison absent
  from the model JSON. We can match the pair *set*, not the orientation.
- **S3** — `is_bridge` is computed in igraph, in Python, and silently degrades to nothing when igraph is
  missing. Our SQL version is a **replacement**, not a reproduction.
- **S4** — Splink's exploding-rule dedupe takes `min()` on a VARCHAR `match_key`, so with ≥11 rules it
  returns the wrong rule. **Bit-parity requires replicating this bug.** We replicate it and log it.

Where Splink leaves SQL, we improve. Where Splink has a defect, we reproduce it *and say so in writing*.

## Scope

**In scope — everything Splink does that manipulates data:**
inference (blocking → comparison vectors → Fellegi–Sunter scoring → clustering), training (prior,
u-estimation, EM, m-from-labels), evaluation and diagnostics, survivorship/golden records, and the data
transformations that back Splink's charts.

**Out of scope:**
- Charts, dashboards, comparison viewers, cluster studio. Where a chart is backed by a transformation, the
  *transformation* is in scope and the rendering is not.
- Multi-dialect support. DuckDB only; SQL stays portable where that is free.
- Replacing the surrounding platform. **This package is the matching engine, not an MDM system** — it is
  an engine the platform calls (register row DR-14). Ingest, entity permanence and stewardship stay
  outside.

## How we know we are moving toward it

- **The critical path is `Stage 1 → 3 → 4 → 5`** — model JSON ingestion, blocking, comparison vectors,
  scoring. Every downstream baseline is meaningless until the JSON reader is right, because the JSON is
  lossy on reload and five of its values must be *recomputed*, not read.
- **Stages 6, 7, 10 and 12b build in parallel** from injected baselines, from day one.
- **Quality is gated, not merely reported.** The frozen reference model measures F1 = 0.72 and blocking
  recall = 0.51 on `fake_1000`; parity gates cannot see the difference between that and F1 = 0.98. Committed
  per-fixture F1 and recall floors are part of the goal, not a nice-to-have (`DesignDoc.md` §A.6 Q5).

## Where this document sits

**GOAL.md is not normative.** It states the destination and nothing else. Two documents state how to get
there, and they own the answers:

- **`docs/DesignDoc.md`** — what the SQL computes. The arithmetic contract, the architectural decisions
  (D1–D12), the staged plan, the verification policy, and the **decision register** (§B.3) that records
  which decisions are in force, superseded, open, or blocking.
- **`docs/DbtBestPractices.md`** — how the repository stays correct while it changes. Gates, enforcement,
  layout, contracts, materialization, linting, documentation standards.

If this file ever disagrees with either, **this file is wrong**. Precedence between the other two is
`DbtBestPractices.md` §1.1, which ranks this file explicitly rather than leaving it to bind unranked — the
failure `DesignDoc.md` narrates at G1, RC32 and DR-11.

**How this file stays operative instead of decorative.** Every ticket and every epic carries a `Serves:`
field naming which of the five success criteria above it advances (`.claude/skills/er-ticket-writer` and
`er-epic-breakdown`). It supplies no acceptance criterion — this document is non-normative — but it makes
two things visible that a backlog otherwise hides: work that serves no stated criterion, and a criterion
no work serves.

**Open decisions block real work, and that is tracked, not hidden.** As of 2026-08-23 the stage inventory
itself is in conflict (DR-11), the input contract and the model-JSON trust boundary are unwritten (DR-16,
DR-17), and the runtime substrate is undecided (DR-13 / companion B.1). `docs/backlog/preflight-2026-08-23.md`
records what is startable today and what is waiting on a decision. Closing those rows is the first work
item, not a prerequisite someone will get to later.

## Attribution

Splink is MIT-licensed, by the Ministry of Justice Analytical Services. This project reimplements its data
transformations in SQL and uses it as the correctness oracle; it vendors no Splink code into the shipped
package. Attribution and licence compatibility are a delivery requirement (`DesignDoc.md` G20).
