# Design Document: `dbt-er` — Splink's Data Transformations as Pure SQL in dbt

**Status:** Draft v2 (adversarial revision) · **Date:** 2026-08-18 · **Appendix B added:** 2026-08-20
**D12 (unit-test policy) added:** 2026-08-23 — see §4 D12, §5's stage-deliverable rule, §6.4, DoD 6, DR-21
**Target runtime:** dbt-duckdb, DuckDB ≥ 1.5.5, dbt-core ≥ 1.12.2
**Reference implementation:** Splink 4.0.16
**Destination:** [`../GOAL.md`](../GOAL.md) — what the project is shooting for. Non-normative; ranked below.

> **v2 note.** v1 of this document was written in the abstract. Every technical claim in it has now been
> checked against Splink 4.0.16 source on disk and against live DuckDB 1.5.5 / dbt-core 1.12.2 execution.
> Six load-bearing decisions were wrong — four in ways that silently produce incorrect output, one that
> cannot compile at all, and one that makes the clustering model **hang** rather than fail. Two of v1's
> pessimistic conclusions were also wrong: EM *is* expressible as a single recursive CTE today, and
> u-estimation *is* bit-reproducible. Corrections are marked **[v1 ERROR]** so the reasoning is auditable.
>
> Every normative claim below cites `splink/internals/<file>.py:<line>` or a query that was executed.
> Claims that are not verified are labelled **UNVERIFIED**.
>
> **Appendix A** is a second adversarial pass run against *this* draft by five independent red teams
> (parity, dbt feasibility, scale, ER domain, test/program). It contains five further blockers — the
> largest being that materialising one model per Splink CTE costs **17.6× resident bytes** — plus a
> verdict on the two core theses and a list of findings explicitly *rejected* as already fixed here.
> **Read §A.1 B1–B5 before writing any model.** Body sections that Appendix A contradicts have been
> corrected in place and point to the relevant finding.
>
> **Appendix B** is a third pass, run against v2 *and* Appendix A together. It attacks a different
> surface. Appendix A asked *"is this faithful to Splink and buildable in dbt?"* and answered it
> thoroughly; Appendix B asks *"is this a product a second team can run on real people's records?"*
> It contains 21 findings (**G1–G21**), four cross-document reconciliations (**R1–R4**) against
> `DbtBestPractices.md`, and the **decision register** (§B.3) — the artifact that keeps this
> document's history of reversals auditable instead of scattered through prose.
> **Read §B.1 G1–G5 before writing any model**, and treat §B.3's `CONFLICT` and `MISSING` rows as
> blocking.
>
> **Precedence inside this document (normative).** The **body is normative; the appendices are
> evidence.** Where the body answers an appendix finding explicitly and by name, the body wins; where
> the body is silent, the appendix stands. This rule is stated because its absence was load-bearing:
> see §B.1 **G1**, where one decision had four live statements and the companion document
> implemented the superseded one.
>
> **§B.3 is carved out of that rule by name.** The decision register is **normative for decision
> *status*** — which value is in force, and where it lives. The body stays normative for decision
> *content*. **Any body edit that changes a decision must touch its DR row in the same commit**, which is
> `DbtBestPractices.md` 3.45's `Supersedes:` pattern applied one level up.
>
> Without the carve-out, the rule pointed at its own cure: the v2 note calls the register *"the artifact
> that keeps this document's history of reversals auditable"* and says to treat `CONFLICT` and `MISSING`
> rows as blocking, while the unqualified rule classed the register as evidence any body paragraph
> silently outranks. At the next reversal a body edit and a DR row could disagree, and the rule as written
> would pick the body without anyone noticing the row — G1's exact mechanism.
>
> The companion carries the mirror half at `DbtBestPractices.md` §1.1, where the register ranks with
> tier 2 and G/R findings rank as Appendix-A-class evidence.

> **[REVIEW 2026-08-23] Fixed (F16) — RC1 is closed by the carve-out above**, and its companion half by
> the §1.1 edit RC32 asked for.

> **`GOAL.md` sits outside that order, deliberately.** The repository root carries
> [`GOAL.md`](../GOAL.md), a one-page statement of the destination: Splink's data transformations as
> declarative pure-SQL dbt models, with recursive CTEs carrying the three iterative surfaces (D4, D5), and
> Splink as the differential oracle. It is **not normative and owns no decision** — it cites this document
> and §B.3's register rather than restating them, and where the two ever disagree, `GOAL.md` is the one
> that is wrong. It is ranked here explicitly, rather than left unmentioned, because a source that binds in
> practice while ranking nowhere in writing is the exact failure this document keeps narrating: **G1**
> (one decision, four live statements), **RC32** (Appendix B binds de facto, ranks below habit de jure),
> **DR-11** (two normative stage inventories). A fourth document arriving unranked would reproduce it.

---

## 1. Purpose & scope

### 1.1 Purpose

Reimplement, as pure SQL dbt models on DuckDB, **everything Splink does that manipulates data**:
inference (blocking → comparison vectors → Fellegi–Sunter scoring → clustering), training (prior,
u-estimation, EM, m-from-labels), and evaluation/diagnostics. Correctness is established by differential
testing against Splink on identical inputs.

*Why this is worth doing, and what "done" looks like end-to-end, is [`../GOAL.md`](../GOAL.md). This
section is the scope statement it defers to; §1.3 below is the authority on non-goals.*

Charts, dashboards, and interactive tooling are **out of scope**. Where a Splink chart is backed by a
data transformation (`comparison_vector_distribution`, `match_weights_histogram`, `unlinkables`), the
*transformation* is in scope and the rendering is not.

### 1.2 Honest scope — what parity cannot mean

v1 claimed a blanket "identical pairs, gammas, and clusters." Four things make that literally
unachievable, and the project is healthier for naming them up front.

| # | Obstacle | Consequence |
|---|---|---|
| S1 | `__splink_salt = random()` is added to `__splink__df_concat` **unconditionally on DuckDB**, whether or not any rule is salted (`settings.py:644-654`; verified in captured SQL for a run with zero salted rules) | Splink's concat table is not byte-reproducible. Any concat-level comparison must exclude `__splink_salt`. |
| S2 | `two_dataset_link_only` uses `where 1=1` with no id ordering; which input is "left" is decided by `min()`/`max()` of the input table **alias** (`blocking.py:617-634`) | l/r orientation for the 2-table link case depends on a runtime fact absent from the model JSON. We can match the pair *set*, not the orientation, without being told the alias order. |
| S3 | `is_bridge` in `edge_metrics.py:121` is computed **in igraph, in Python**, by pulling the whole edge list to pandas. Without igraph installed Splink silently degrades to returning no metrics | There is no SQL oracle for bridges. Our SQL implementation is a *replacement*, not a reproduction. |
| S4 | Splink's exploding-rule dedupe takes `min(match_key)` on a **VARCHAR** (`blocking.py:683-697`), so with ≥11 rules `min('2','10') = '10'` | Verified by construction: 12 rules, rule 2 exploding, rule 10 re-producing the pair → Splink returns `match_key = 10` where numeric semantics give `2`. Bit-parity requires **replicating this bug**. Flagged in the divergence log, not fixed. |

Everything else is reachable. In particular the load-bearing assumption **holds**: the saved model JSON
contains fully-rendered, dialect-specific DuckDB SQL strings. Gamma `CASE` statements were built from a
real trained model with **no splink import** and executed correctly in DuckDB 1.5.5.

### 1.3 Non-goals

- Charts, dashboards, comparison viewers, cluster studio.
- Multi-dialect support. DuckDB only; keep SQL portable where free.
- Replacing the surrounding platform (ingest, entity permanence, stewardship). This package is the
  matching engine, not an MDM system.

### 1.4 Guiding principles

1. **Splink is the oracle, not the ceiling.** Parity proves the migration; it does not cap the design.
   Where Splink has a defect (S4) we replicate it *and log it*. Where Splink leaves SQL (S3) we improve.
2. **Every column name is a pure function of the model JSON, derived at parse time.** Never introspected.
   See D1.
3. **Determinism is a feature.** Total ordering everywhere. Two runs on the same input produce identical
   content (see §6.3 for the correct form of this claim — "byte-identical" is the wrong assertion).
4. **The model JSON is the contract — but it is not self-describing.** Five things must be recomputed
   from it rather than read out of it. See D1. **And it is a contract only after it has been validated**
   — until then it is untrusted input. See §1.5.

### 1.5 The model JSON is a trust boundary

**Normative. Supersedes:** principle 4's unqualified *"the model JSON is the contract"*, and D6's framing
of its function list as a *"lint whitelist"*. **Closes G3. Register row: DR-17.**

The model JSON is an **input, not a contract, until it has been validated.** Principle 4 states what it
*means* once trusted; this section states what makes it trusted.

The reason is D6. Comparison-level SQL is passed through **verbatim** into compiled SQL, and D1 delivers
the JSON through an environment variable — so it never passes review as code. In a consumer's project
`DBT_ER_MODEL_JSON` may be set from a pipeline variable rather than a reviewed file, at which point this
package executes arbitrary SQL with that consumer's warehouse credentials. That is the hostile reading.
The benign one is likelier and nearly as damaging: an analyst adds an age-band level containing
`current_date` — the natural way to write one — gamma becomes a function of the wall clock, and every
parity and determinism gate in §6 silently stops being true while CI merely looks flaky.

**Validation happens once, at compile time, in the sidecar** (§A.2), because the sidecar already parses
every `sql_condition` with sqlglot and therefore already holds the tree. Validating the raw string instead
of the parsed tree is what makes an allow-list bypassable, and it is the same mistake §A.2 C2 records for
TF exact-match resolution: *"Jinja can only string-match."*

Five rules. All enforced at compile time, all failing the build rather than warning:

1. **Every function in a parsed `sql_condition` appears in D6's allow-list**, which is normative and
   closed. An unlisted function fails compilation **naming the function**, not "invalid condition".
2. **Non-deterministic functions are rejected outright**, listed or not: `current_date`,
   `current_timestamp`, `now()`, `random()`, `nextval`, `uuid()`, and anything else reading session or
   wall-clock state. This is the rule that keeps §6.3's determinism claim true rather than aspirational.
3. **Structural rejection.** No subquery, no statement terminator, no set operation, no CTE, no
   `INTO` / `COPY` / `ATTACH` / `INSTALL` / `LOAD`, no side-effecting call. A `sql_condition` is a scalar
   boolean expression over columns of the current row pair; anything else is not a comparison level,
   however it is spelled.
4. **The input is bounded** — `er_max_comparisons`, `er_max_levels_per_comparison`,
   `er_max_model_json_bytes`. Exceeding one is a named error rather than a pathological build. M2 measures
   397 B/level, so generous defaults are cheap; the point is that "the JSON is enormous" becomes a
   diagnosis instead of a symptom.
5. **`er_model_sha` is the hash of the validated artifact**, never of whatever arrived in the environment.
   `dbt build` refuses a model JSON whose sidecar output is absent or whose hash does not match. This is
   what makes rules 1–4 unskippable: an unvalidated JSON has no sha, and a model with no sha does not
   build.

**What this costs, stated rather than discovered.** A model JSON that **Splink itself produced can fail
this validation.** Splink has no such allow-list, and a user-supplied `CustomLevel` may legitimately
contain a function D6 never enumerated. That is a **supported-configuration boundary** — narrower than
Stage 12.1's, and in the same class — not a bug. When it bites, the fix is a reviewed addition to D6's
list with the determinism argument written down, never a bypass flag. A bypass flag would return the
package to executing unreviewed SQL with the consumer's credentials, which is the whole of what this
section prevents.

**Negative tests are part of the deliverable, not a follow-up.** A level containing `current_date` fails
compilation; a level containing a subquery fails compilation; a level calling an unlisted function fails
compilation naming the function; a model JSON exceeding a bound fails compilation naming the bound; and
`dbt build` refuses a JSON whose sidecar hash does not match.

> **Decided under delegated authority 2026-08-23.**
> **Recommendation source:** G3 recommendations 1–5, adopted in full.
> **Reversible:** reopen DR-17 in §B.3.

### 1.6 The cluster label is `component_label`, and it is not an identifier

**Normative. Closes M6. Register row: DR-12.**

The clustering output column is **`component_label`**. It is the minimum `unique_id` in the connected
component, per D4's monotone min-label formulation — a deterministic function of the **current** graph, and
nothing more.

**It is not an entity identifier, and the name now says so.** §1.3 lists entity permanence as a non-goal and
DR-14 puts it on the platform's side of the boundary. Emitting a column called `entity_id` while disclaiming
identity invites exactly the thing the disclaimer forbids: a consumer storing it as a CRM master key or a
warehouse dimension key.

**What makes it not an identifier is measured, not argued.** M6, `[RECON]` on DuckDB 1.5.5 with D4's exact
formulation: five records chained under `crm:100 … crm:104` all label as `'crm:100'`; adding **one** record
`billing:7` with a single edge changed the label for **5 of 5 pre-existing records**, though no member left
the component. Lexicographic ordering is D3's own verified property, so onboarding a source whose alias
sorts early relabels every cluster it touches, and deleting the minimum member relabels every survivor. A
merge, a split and a pure relabel are indistinguishable from the outside, because there is no old→new
mapping and no event log — the only observable is that the row is gone.

**What the contract says, and what it therefore permits:**

| Property | Holds? |
|---|---|
| Deterministic for a fixed graph and threshold | **Yes** — this is what makes it a valid grouping key *within* one run's output, and what D4's parity gate asserts |
| Stable across runs when the corpus changes | **No.** Not weakly, not usually — one insert can rewrite the whole label space |
| Usable as a durable key by a downstream system | **No.** It is removed as a key from every downstream model, and every column description saying otherwise is a defect |

**Stage 6b collapses into an interface contract**, which is what §A.6 Q1 already declared binding under
DR-14. The engine does not build `entity_keys`, `cluster_lineage` or `entity_events`; the platform owns
entity permanence and the incumbent's `INV-PERM`. What the engine owes that platform is the two things
permanence is computed *from* — the **edge set** at each threshold and the **partition** — both of which it
already publishes. That is the whole of the interface.

**Why the rename rather than the build.** Both were legitimate (A.5: *"choosing 'rename' is legitimate and
cheap; leaving it ambiguous is not"*), and the build is the one this project is not for. §1.3 says so, DR-14
says so, and §A.3 Group 2 already assigns the surrounding machinery to the platform. Building entity
permanence here would make `dbt-er` an MDM system by accretion.

> **Decided under delegated authority 2026-08-23.**
> **Recommendation source:** M6 rec (a), and §A.6 Q1's already-binding consequence of DR-14. This row's own
> trigger — *"decide with DR-14"* — fired on 2026-08-20 without the row closing (RC16); this closes it in
> the direction Q1 had already declared.
> **Reversible:** reopen DR-12 in §B.3.

### 1.7 Thresholds are a dimension, and there are two of them

**Normative. Closes G15. Register rows: DR-08 (with companion B.2) and DR-09.**

Two decisions that look separate and are one contract change to Stage 6, which is why they are settled
together and before that stage is broken down.

#### The threshold is a dimension of the model, not a build-time var

`var('er_threshold')` builds **one partition per run**. Stage 6's acceptance criteria require label parity
at **{0.5, 0.9, 0.99} simultaneously**, and cross-threshold monotonicity — that a component at a higher
threshold is contained in one at a lower threshold — is **not expressible as a dbt test at all** under the
var approach, because the two partitions never exist in the same build.

M16 verified the alternative rather than proposing it. `[RECON]`: a single `USING KEY` model with a
composite key produces all three partitions in one statement, and on 6 nodes / 3 edges yields
`0.50 → {a-0},{a-1,a-2,a-3},{a-4,a-5}`; `0.90 → {a-0},{a-1,a-2,a-3},{a-4},{a-5}`;
`0.99 → {a-0},{a-1,a-2},{a-3},{a-4},{a-5}` — a correct refinement chain.

So: `edges_by_threshold` and `entity_clusters` carry `thr` as a real column, cross-joined from a
`thresholds` relation. The baseline comparator joins on `thr`, and cross-threshold properties become plain
singular tests.

> **The trap, from the same `[RECON]`:** **cast the thresholds relation to `DOUBLE`.** DuckDB types a bare
> decimal literal as `DECIMAL`, which changes the boundary comparison against `match_probability` — and a
> boundary comparison is precisely what a threshold is. This belongs in the model, not in a reviewer's
> memory.

**`er_thresholds` defaults to a single row, so production cost is unchanged.** The dimension is what makes
the *tests* possible; it does not oblige anyone to run three partitions in production.

#### There are two thresholds, and the band between them is not clustered

The `thresholds` relation is a relation of **pairs**:

| Column | Meaning |
|---|---|
| `thr_auto_merge` | Pairs at or above this are edges. This is "the threshold" everywhere else in the document |
| `thr_review_low` | Pairs in `[thr_review_low, thr_auto_merge)` are **gray**: emitted for review, never clustered |

`edges_by_threshold` contains pairs with `match_probability >= thr_auto_merge`. Gray pairs go to a
**`review_pairs`** relation, keyed `(thr, unique_id_l, unique_id_r)`, which the platform consumes. They
never enter the graph, so they change no partition and no Stage 6 acceptance criterion.

This matches the incumbent's contract exactly — A.3 Group 1: *"half-open `review_low ≤ p < auto_merge`;
gray-band pairs are **not** clustered"* — which is what DR-14's posture requires, since the platform on the
other side of that boundary already expects these semantics.

**`thr_review_low` defaults to `thr_auto_merge`, making the band empty.** A single threshold is the
degenerate case of two, so nothing changes for a caller who wants one.

**This costs no parity, and that is worth stating because it looks like it should.** Splink clusters at one
threshold: pairs below it are excluded. Gray pairs are *below* `thr_auto_merge` by definition, so Splink
excludes them from clustering too. The edge set fed to clustering is **identical** either way. `review_pairs`
is purely additive — it surfaces rows Splink computes and discards, and A.4's cluster gate is unaffected.

**Why decide it now rather than at Stage 6.** G15's failure scenario is that Stage 6 gets built and gated
against one threshold, and the gray band arrives during Stage 12 integration as an interface requirement —
*"and it is not a var change; it changes the clustering input, the cluster contract, and every AC that
references a partition."* M12 rec 5 is blunter: *"a single threshold cannot express 'uncertain'."*

The review **queue** stays out of scope per §1.3. The two-threshold contract and the `review_pairs` relation
are the engine's to provide; what anyone does with them is the platform's.

> **Decided under delegated authority 2026-08-23.**
> **Recommendation source:** `DbtBestPractices.md` B.2 (*"the dimension"*) and G15's recommendation, both
> adopted in full; M12 rec 5 and M16 supply the evidence. The pairing of the two thresholds into one
> `thresholds` relation is this document's, and follows from adopting both.
> **Reversible:** reopen DR-08, DR-09 and B.2.

### 1.8 The quality floor, and who owns it

**Normative. Answers §A.6 Q5. Register row: DR-22.**

Parity is not quality, and this project can be 100% parity-green on a configuration that is badly wrong.
Its own reference fixture **is** such a configuration: `[RECON]` on `fake_1000` with the model Stage 0.4
freezes measures **F1 = 0.7138 and blocking recall = 0.5550** at t = 0.9 — 1,651 of 2,975 true pairs found,
1,324 missed. Adding two blocking rules takes recall to **0.9173** and end-to-end F1 to **0.9809**, an
improvement of **+0.26 F1 that is invisible to every gate in §6.4** (M12).

Q5 asked three things. (c) — whether the gray band is two thresholds or one — is answered by §1.7: two.
The other two are answered here.

#### (a) The floors are a Stage 0.4 gate, not an aspiration

**The floors are committed numbers, per fixture, and Stage 0.4 cannot complete without them.** They are
measured from the **fixed** model, not the current one — M12 rec 6 requires fixing Stage 0.4's frozen model
rather than freezing a bad one, and §5 Stage 0.4 already carries that.

The numbers are set at Stage 0.4 and are **deliberately not invented here**, because they must come from a
measurement of the model that actually ships. What is fixed now is the rule:

| Gate | Where | Form |
|---|---|---|
| `er_blocking_recall_floor` | Stage 3 | **Two-sided.** Failing below is a regression; failing *above* means the fixture or the oracle moved, not the code — and that is equally a finding (M12 rec 2) |
| `er_f1_floor` | Stage 10's measurement models, gating from Stage 2 onward | Per fixture, committed |
| `er_max_cluster_size` | Stage 6 | A **hard test**, not a warning. Cluster-level error amplifies: `[RECON]` edge precision 0.9764 against **cluster** precision 0.7495 — 14.8× (M12 rec 3) |

**The floors M12 already measured are the minimum the fixed model must beat**, recorded so Stage 0.4 has a
target rather than a blank page: recall ≥ 0.9173 and F1 ≥ 0.9809 on `fake_1000`, both achieved by adding
`block_on(dob)` and `block_on(email)`.

#### (b) `er_threshold` has no defensible default, so the package requires it

**There is no package-level default threshold.** An unset `thr_auto_merge` fails compilation, exactly as an
unset `er_input_relation` does (§2.0) and an unvalidated model JSON does (§1.5). The package fails rather
than guessing.

This is not fastidiousness. **The default that was there is measurably harmful.** `[RECON]` on the
*improved* model: F1 peaks at **0.9809 at t = 0.5** and falls to **0.9219 at t = 0.9** — the value §8's own
Definition of Done used as its example — costing roughly **330 true pairs for zero precision benefit**,
because precision is already 1.0000 at the lower threshold. A default nobody justified was silently
choosing worse output on the project's own reference corpus.

A threshold is a precision/recall trade against a cost function the package cannot see. `integration_tests/`
and each fixture set theirs explicitly, with the target metric and the measured P/R/F1 at that point
recorded beside it, and the harness fails if a configured threshold is **not on the committed curve**
(M12 rec 5).

#### Who owns it

*"Without an owner, Stage 10 is a reporting stage and the product ships at 0.72."* Ownership is made real
the same way `PARITY.md`'s and `divergence-log.md`'s is: **the floors live in a committed file with a
`CODEOWNERS` entry**, so changing one is a reviewed act with a name attached rather than a var edit. A
floor anybody can lower to make CI green is not a floor.

> **Decided under delegated authority 2026-08-23.**
> **Recommendation source:** M12 recommendations 2, 3, 5 and 6, adopted. Removing the default threshold
> outright goes beyond rec 5's *"commit it with a justification"* — the justification measured here is that
> the default was wrong, and a package cannot justify a number for a corpus it has never seen.
> **Reversible:** reopen DR-22 in §B.3.

---

## 2. What Splink does, as data transformations

### 2.0 The input contract

**Normative. Closes G2 and G9. Register row: DR-16.**

Every model in the DAG derives from `stg_input`, and until now nothing said how a consumer supplies data to
it. This is the one interface every user touches, and it was the only major interface with no
specification. What follows is that specification.

**Wiring: a relation-name var, and the package ships zero sources.** The consumer sets
`er_input_relation` to a fully-qualified relation name; the package reads it at parse time. A `source()`
declared inside the package would force its database and schema onto every consumer and trips
`source-override-deprecation` on dbt-core 1.12.2 (M4b), and `ref()` cannot reach a relation the package
does not own.

The cost of that choice is real and is the consumer's to manage: **a relation name creates no DAG edge**,
so nothing orders the input's construction before `stg_input` builds. §15 records the same hazard for
seeds. The contract states it rather than leaving it to be discovered: *the consumer is responsible for the
input relation existing and being complete before `dbt build` runs.*

**Arity: exactly one relation, in v1.** Splink's `vertically_concatenate` unions its input tables, and §2's
table describes `stg_input` as a *"bare `UNION ALL` passthrough"* for that reason. **Under Stage 12.1's
supported-configuration matrix — `dedupe_only`, no `source_dataset` — there is exactly one input table, so
the union degenerates to a plain select.** v1 therefore takes one relation and does not need a list var, an
arity decision, or an owner for the union.

That question returns with `link_only` / `link_and_dedupe` in v2, and the answer is recorded now because it
is load-bearing for correctness rather than ergonomics: **the consumer owns the union.** §3.5 requires term
frequency to be computed over the *global* concat, and only the consumer knows what "global" means for
their corpus. A list var in the package would let per-source TF be assembled by accident, and the failure is
invisible — every adjusted comparison off by a uniform bit shift, which is the exact defect §3.5 exists to
prevent.

**The column contract.**

| Column | Requirement |
|---|---|
| `unique_id` | **VARCHAR**, `NOT NULL`, `UNIQUE`. The type is a correctness fact, not a convention — D3's pair ordering is lexicographic, and `'ds-__-9' < 'ds-__-100'` is false where `9 < 100` is true, so a BIGINT id and a VARCHAR id are different products |
| Every column named in the model JSON | Must exist. Types are whatever the comparison expressions require; the contract asserts **presence**, and lets the expression own the type, because only the expression knows what it needs |
| Anything else | Passes through untouched (D8). `stg_input` performs no transformation, and the **Caveats** section of its properties file says why |

The declared input column set is itself a **parse-time var**, `er_input_columns`. That is D1's corollary —
*every column name is a pure function of the model JSON, derived at parse time, never introspected* —
applied to inputs, where it was previously written only for outputs.

`unique_id`'s VARCHAR requirement was previously stated only in passing inside Stage 12.1, a cutover stage,
which is not where a reader looks for an input contract. It is hoisted here, which is also what G18 asks
for the rest of that matrix.

**Missing columns fail at compile time, naming the column.** A compile-time check asserts that every column
the model JSON references appears in `er_input_columns`. Without it the failure is a DuckDB `Binder Error`
raised from inside a generated `CASE`, hundreds of lines into compiled SQL, naming a column the user never
wrote. This error belongs in G13's catalogue with a stable id, alongside the preconditions below.

**Preconditions, normative and testable — and they ship as tests in the package**, so they fail in a
consumer's build rather than only in this repository's CI:

| Precondition | Enforced by | Why it is not merely hygiene |
|---|---|---|
| `unique_id` is **unique** | A dbt test in the package, plus the `PRIMARY KEY` constraint `DbtBestPractices.md` §8.2 already puts on `er_stg_input` | **This is the one that fails silently.** D3's predicate is `l.<uid> < r.<uid>`, so two records sharing an id **never pair with each other** — strict inequality excludes them. A duplicated id removes exactly the match most likely to matter, with no error and no warning, and the symptom is a recall miss indistinguishable from a blocking gap, which M12's recall floor would blame on the blocking rules |
| `unique_id` is **not null** | `NOT NULL` constraint — DuckDB enforces it (§8.2) | A NULL id propagates into blocking and into D4's node seed, where Splink's own spurious-NULL-row defect already lives |
| The corpus is **non-empty** | A singular test in the package | An empty corpus produces a green build over zero rows — §12.7's vacuity, arriving through the front door |

Stage 0.2's degenerate-corpus fixture set exercises these alongside the ones that are merely awkward:
single-row, all-identical (worst-case pair explosion against B1's ceiling), and an all-NULL blocking column.
D4's spike already tests the degenerate *graph* shapes thoroughly — chain, star, cycle, self-loop, empty
node table; the degenerate *corpus* shapes had no equivalent.

> **Decided under delegated authority 2026-08-23.**
> **Recommendation source:** G2's recommendation and G9's, adopted in full. The v1 arity answer follows
> from Stage 12.1 rather than from either finding.
> **Reversible:** reopen DR-16 in §B.3.

---

v1's table had 7 rows and covered only the inference path. The complete non-chart surface:

> **Naming.** Model names below are written unprefixed for readability. The project ships them with an
> `er_` prefix (`er_stg_input`, `er_int_candidate_pairs`, `er_entity_clusters`, …) because
> `require_unique_project_resource_names` is on and the package must not collide with a consumer's
> models. Every `.sql` has a paired `.yml` — enforced by the custom dbt-bouncer check
> `check_one_yml_per_sql`.

| Splink surface | Our model(s) | SQL difficulty | Note |
|---|---|---|---|
| Input concat (`vertically_concatenate`) | `stg_input` | Trivial | Bare `UNION ALL` passthrough — which degenerates to a plain select in v1, since Stage 12.1 admits exactly one input table. **No transforms** — see D8. Its contract and preconditions are **§2.0** (DR-16). |
| Term frequency (`compute_tf_table`) | `tf_all` | Easy | Long format, not per-column. See D7. |
| Blocking (`block_using_rules_sqls`) | `int_candidate_pairs` | Moderate | See D2. |
| Comparison vectors | `int_comparison_vectors` | Moderate | See §3.3. |
| Scoring (`predict`) | `int_scored_pairs` | **Subtle** | See §3.1–3.2. The whole of §3 exists for this. |
| `deterministic_link` | `int_deterministic_links` | Easy | Blocking with no scoring. |
| `compare_two_records` | `compare_two_records` (macro) | Easy | The "explain this pair" primitive. |
| `find_matches_to_new_records` | folded into incremental | Easy | See §5 Stage 8 — SQL removes the two-pass wart. |
| `cluster_pairwise_predictions_at_threshold` | `entity_clusters` | **Solved — see D4** | Composite key `(thr, unique_id, component_label)` — §1.6, §1.7 |
| *(no Splink surface)* | `review_pairs` | Trivial | The half-open gray band `[thr_review_low, thr_auto_merge)`, emitted for the platform and never clustered. An **addition**, not a divergence — §1.7 |
| `cluster_using_single_best_links` / `one_to_one_clustering` | `entity_clusters_1to1` | Moderate | Mutual-best-link with a duplicate-free constraint. **Absent from v1 entirely.** |
| `compute_graph_metrics` (node/cluster) | `cluster_metrics`, `node_metrics` | Easy | Pure aggregates. Formulas in §3.6. |
| `edge_metrics` (`is_bridge`) | `edge_metrics` | Moderate | Not a reproduction — see S3. |
| `estimate_probability_two_random_records_match` | `train_prior` | Easy | Deterministic rules + recall, one pass. |
| `estimate_u_using_random_sampling` | `train_u` | Easy, **and reproducible** | See D9. |
| `estimate_parameters_using_expectation_maximisation` | `train_em` | **Solved — see D5** | |
| `estimate_m_from_pairwise_labels` / `_from_label_column` | `train_m_from_labels` | **Easy — one GROUP BY** | No iteration at all. |
| `accuracy_analysis_from_labels_*`, `prediction_errors_from_labels_*` | `eval_accuracy`, `eval_errors` | Easy | Join + confusion matrix. **v1 had no evaluation stage at all.** |
| `unlinkables` | `eval_unlinkables` | Easy | |
| `comparison_vector_distribution`, `match_weights_histogram` | `diag_*` | Easy | Data only, no charts. |

**One shared aggregate serves three of the training paths.** Splink reuses `compute_new_parameters_sql`
for EM, u-estimation and m-from-labels (`estimate_u.py:212-221`, `m_from_labels.py:44-50`,
`expectation_maximisation.py:44-85`). We mirror that with one macro, `m_u_counts`.

---

## 3. The arithmetic contract

*This section did not exist in v1, and its absence is why v1's scoring model was wrong. Everything here
is normative and citation-backed. Implementations must match it exactly.*

### 3.1 Match weight and probability — linear space, then one log

**[v1 ERROR]** v1 §1.4 and §5.1 specified "sum of per-comparison log2 Bayes factors." Splink does not do
that. It multiplies Bayes factors in **linear space**, clamps, and applies `log2` **exactly once**
(`predict.py:209-218`, `predict.py:113-120`):

```sql
log2(least(greatest(
  cast(<prior_odds> as float8) * bf_a * bf_tf_adj_a * bf_b * bf_tf_adj_b * ...,
  1e-300), 1e300)) as match_weight,
CASE WHEN bf_a = cast('infinity' as float8) OR ... THEN 1.0
     ELSE (<clamped_product>)/(1+(<clamped_product>)) END as match_probability
```

The prior is converted to odds and multiplied as the first factor — **never** log2'd and added.

The clamp is not cosmetic. Measured in DuckDB 1.5.5, a naive log-space sum diverges from Splink by:

| case | Splink | log-space sum | Δ |
|---|---|---|---|
| underflow (prior 0.001 × twelve BFs of 1e-25) | `-996.5784284662087` | `-1006.5442127508709` | **9.97** |
| overflow (prior 0.001 × eight BFs of 1e40) | `+996.5784284662087` | `1053.051206079294` | **56.47** |

That is 7–8 orders of magnitude outside v1's `1e-6` tolerance, and it bites precisely on the
high-confidence pairs that drive clustering. v1's risk table named the mitigation as "log2-space
arithmetic mirrors Splink"; that mitigation *is* the bug.

**Rule:** implement in linear space with Splink's clamp. If a log-space form is ever used, it must clamp
the summed weight to `[-996.5784284662087, +996.5784284662087]` (= `[log2(1e-300), log2(1e300)]`), which
reproduces Splink's saturation exactly (`1e-300/(1+1e-300) = 1e-300`, `1e300/(1+1e300) = 1.0`, verified).

Two further traps:
- `log2()` in DuckDB 1.5.5 **raises** on 0 or negative input rather than returning `-inf`. A macro that
  emits `log2(<expr>)` on a runtime value must guarantee positivity. **The clamp above is that
  guarantee** — `least(greatest(<product>, 1e-300), 1e300)` cannot yield a non-positive argument — so the
  invariant and its proof must stay attached to each other, and the AC must test it. See **G13**.
- DuckDB is asymmetric on NaN: `greatest(NaN, 1e-300) = NaN` but `least(NaN, 1e300) = 1e300`. A `0 × inf`
  product therefore scores `match_weight = +996.578…` — a *certain match*. Reproducible; log it.

### 3.2 Term frequency adjustment

**[v1 ERROR]** v1 said "`log2(tf_adjustment)` term with `tf_adjustment_weight`." The real form
(`comparison_level.py:576-643`) is a separate multiplicative column `bf_tf_adj_<name>`, a CASE covering
**every** gamma value, with non-adjusted levels emitting `cast(1 as float8)`:

```
POW( u_exact_match / divisor , tf_adjustment_weight )
```

Three independent corrections:

1. **The numerator is the exact-match level's `u`, not the level's own `u`**
   (`_u_probability_corresponding_to_exact_match`, `comparison_level.py:538-563`). Proven by construction:
   a fuzzy level with its own `u = 0.01` carrying the TF adjustment rendered `cast(0.001 as float8)` —
   the *exact* level's u. **This value is not in the model JSON.** Splink resolves it by sqlglot CNF
   analysis of sibling levels' `sql_condition`. Our macro must do the same resolution; a naive string
   match on `"<col>_l" = "<col>_r"` diverges on levels like `a_l = a_r AND b_l = b_r`. Override:
   `disable_tf_exact_match_detection: true` uses the level's own u.
2. **The divisor is `GREATEST(tf_l, tf_r)`** — a CASE with mutual `coalesce`, not `LEAST`, not
   `coalesce(tf_l, tf_r)` (`comparison_level.py:595-617`):
   ```sql
   CASE WHEN coalesce(tf_l,tf_r) >= coalesce(tf_r,tf_l) THEN coalesce(tf_l,tf_r)
        ELSE coalesce(tf_r,tf_l) END
   ```
   Splink deliberately uses the *more common* term's frequency, giving the smaller boost. `LEAST` flips
   the direction on every pair where l and r differ — a systematic, not floating-point, divergence.
3. **`tf_minimum_u_value` floors the divisor** (`comparison_level.py:610-630`), capping the boost at
   `(u_exact / tf_min)^weight`. It is **omitted from the JSON when 0** (`:665-666`), so readers must
   default it to `0.0`, and D1's validation must not reject it as an out-of-range probability.

NULL handling: the guard is `coalesce(tf_l, tf_r) IS NOT NULL`, so the adjustment is 1.0 only when
**both** sides are NULL; with exactly one NULL the other side is substituted and the adjustment **is**
applied. `COALESCE(tf, 0)` would produce `+inf`; `COALESCE(tf, 1)` zeroes the adjustment differently
than Splink.

A level emits the constant `1.0` when: it is the null level, or has no `tf_adjustment_column`, or
`tf_adjustment_weight == 0`, or **it is the ELSE level** — detected by `_is_else_level`, not by `gamma == 0`.

### 3.3 Gamma numbering and the comparison CASE

**[v1 ERROR]** v1 §1.3 said levels are "evaluated highest-to-lowest." They are emitted in **exact JSON
list order** with no reordering and no null-hoisting (`Comparison._case_statement`).

Numbering (`comparison.py:108-110`) — and **this value is not persisted in the model JSON**; it appears
only in `_as_completed_dict`, which is chart-only code:

```
num_levels = count of levels with is_null_level == False
counter    = num_levels - 1
for level in comparison_levels (list order):
    if level.is_null_level:  cvv = -1
    else:                    cvv = counter; counter -= 1
```

So the **first non-null level gets the highest gamma**, null levels are `-1` wherever they sit, and
`ELSE` is `0` only because it is conventionally last. Numbering ascending in list order — the intuitive
reading — inverts every gamma and every Bayes-factor lookup, and there is no cross-check available. The
macro owns this algorithm.

`ELSE` is not SQL: it is the literal string `sql_condition.strip().upper() == "ELSE"` and must be emitted
as a bare `ELSE <n>` with no `WHEN`/`THEN`.

Gamma column name: `f"{gamma_prefix}{output_column_name}".replace(" ", "_")`, prefix `gamma_`.

### 3.4 Degenerate parameters and the lossy model JSON

Per-level Bayes factor (`comparison_level.py:349-357, 565-574`):

| condition | `_bayes_factor` | emitted SQL |
|---|---|---|
| null level (cvv = -1) | hardcoded `1.0` | `cast(1.0 as float8)` |
| `m = 0` | `0.0` | `cast(0.0 as float8)` — no epsilon at level scope |
| `u = 0` | `math.inf` | `cast('Infinity' as float8)` |
| level not observed in training | epsilon **`1e-6`** (`:211-212`, `:229-230`) | e.g. `cast(2e-06 as float8)` |

**[v1 ERROR] — the model JSON round-trip is lossy and silent.** `ComparisonLevel.as_dict` guards with a
*truthiness* test: `if self._m_probability and self._m_is_trained`. So `m_probability == 0.0` is
**dropped**, and not-observed levels are dropped. On reload the missing value is replaced by
`_default_m_values(n)` = `[0.05/(n-1)]*(n-1) + [0.95]`, indexed by comparison vector value.

This has already happened in a real trained model. Round-tripping a production `model_test_v1.json`:

| comparison | level | json m | effective m after reload | bf |
|---|---|---|---|---|
| email | `split_part(email_l,'@',1) = split_part(email_r,'@',1)` | absent | 0.025 | 214.758 |
| birth_date | `date_trunc('month', …)` | absent | 0.025 | 22.523 |
| addr_postal | `ELSE` | absent | 0.050 | 0.0501 |

**Three of six comparisons** have a level whose `m` is invented at load time. Consequences:

- The macro **must replicate `_default_m_values` exactly**, indexed by the gamma it computed itself, or
  it emits NULL or fails on a real model.
- **The parity harness must generate baselines from a *reloaded* JSON**, never from the in-memory
  freshly-trained linker. An in-memory not-observed level scores `m = 1e-6`; the same level after
  save/load scores `m = 0.95` — roughly 20 match-weight units apart on identical data. v1 tasks 0.4 and
  1.1 both assumed a faithful round-trip.

### 3.5 Term frequency table

`term_frequencies.py:33-48`:

```sql
select <col>, cast(count(*) as float8) / (select count(<col>) from __splink__df_concat) as tf_<col>
from __splink__df_concat where <col> is not null group by <col>
```

**[v1 ERROR]** v1 §2.2 said "`count(*)/total`" and "NULLs excluded from TF" — correct for the numerator,
wrong for the **denominator**, which is `count(<col>)`, the **non-null** count. Verified on fake_1000:
1000 rows, 109 NULL cities, TF min = `1/891`, `sum(tf) = 1.0` exactly. Using `count(*)` inflates every tf
by 891/1000 — a uniform `log2(1000/891) = 0.1665` bit shift on every adjusted comparison. Uniform,
systematic, and easy to miss.

TF is computed on the **global** concat (all sources unioned), never per source, and is left-joined onto
the node table **before** blocking.

### 3.6 Graph metric formulas

From `graph_metrics.py:28-113, 257-315`, all pure SQL:

- `node_degree` = `COUNT(*) FILTER (WHERE neighbour IS NOT NULL)` over the doubled edge list, left-joined
  from clusters so edge-less nodes get 0
- `cluster_size` = `COUNT(*) OVER (PARTITION BY cluster_id)`
- `node_centrality` = `CASE WHEN cluster_size > 1 THEN 1.0*node_degree/(cluster_size-1) ELSE 0 END`
- `n_edges` = `SUM(node_degree)/2.0`
- `density` = `1.0*(n_edges*2)/(n_nodes*(n_nodes-1))`, NULL when `n_nodes <= 1`
- `cluster_centralisation` = `1.0*(COUNT(*)*MAX(node_degree) - SUM(node_degree)) / ((COUNT(*)-1)*(COUNT(*)-2))`,
  NULL when `n_nodes <= 2`

### 3.7 Float type

DOUBLE (`float8`) everywhere; no float4, no DECIMAL. Every literal in the scoring path is wrapped
`cast(<x> as float8)`. **Reproduce the casts**, not just the precision: a bare literal risks DuckDB
inferring DECIMAL for the expression, which changes rounding and overflow behaviour. The clamp literals
`1e-300`/`1e300` are bare and are already DOUBLE (`typeof(1e-300)` → `DOUBLE`).

---

## 4. Architecture & decisions

### D1 — Model JSON arrives via `env_var`, parsed at compile time

**[v1 ERROR]** v1 specified `fromjson(load_file(...))`. **`load_file` is not a dbt function.** The
dbt-core 1.12.2 base Jinja context is exactly 23 names; `modules` exposes only `pytz, datetime, re,
itertools`; the environment is a `SandboxedEnvironment` constructed with **no loader**. Verified:

```
{{ fromjson(load_file('/etc/hosts')) }}  → UndefinedMacroError: 'load_file' is undefined
{{ open('/etc/hosts').read() }}          → UndefinedMacroError: 'open' is undefined
{% include '/etc/hosts' %}               → CompilationError: no loader for this environment specified
```

**Corrected mechanism:** the JSON arrives through an **environment variable**, read at parse time.

```bash
export DBT_ER_MODEL_JSON="$(cat fixtures/model_jsons/fake_1000_v1.json)"
dbt build          # macros consume fromjson(env_var('DBT_ER_MODEL_JSON'))
```

**[v2 CORRECTION — `env_var`, not `--vars`.]** An earlier v2 draft specified
`--vars "{er_model: $(cat …)}"`. That works for fixture models but fails on real ones for two reasons,
both discovered during implementation:

1. **`--vars` is bounded by `MAX_ARG_STRLEN`** — 128 KiB per argv element, roughly 330 comparison levels.
   A production model JSON exceeds it, and the failure is an exec-time `E2BIG`, not a dbt error.
   `env_var` has no such bound.
2. **`env_var` is one of only *two* functions available in the schema.yml rendering context** (the other
   is `var`). That is what lets JSON-derived column lists reach model contracts — see the
   `columns: "{{ var('er_gamma_columns') }}"` mechanism in Appendix A, M2. A macro cannot be called there.

Both are available at parse time, so the properties v1 wanted are preserved either way: compile-time
literals, fully static inspectable SQL, zero Jinja residue. `env_var` is simply the one that scales.

Note the division of labour this forces: `er_model` is the raw JSON, while the *derived* column lists
(`er_gamma_columns`, `er_bf_columns`, …) must be computed **where the env var is emitted** — not by a
macro — precisely because the schema.yml context has no macros.

**Corollary (normative):** every column name must be a pure function of the `er_model` var, derived at
**parse** time, never introspected. `run_query` returns `None` at parse time (`execute == False`), and
columns discovered only at run time silently lose model contracts, per-column schema tests, and dbt unit
tests — three of the four layers §6 depends on.

This bans introspecting **column names**. It does **not** ban cardinality queries: the capacity guard
(D11 rec 5) needs an input row count at run time, which is legal in a hook or a test. See **G14**, which
also names the enforcement point the guard currently lacks. And because the JSON arrives through an
environment variable rather than as reviewed code, it is an **input, not a contract**, until validated —
see **G3**.

**Five things the JSON does not contain**, which `load_model_json` must therefore compute or default:

1. `comparison_vector_value` — the gamma number (§3.3).
2. `m_probability` / `u_probability` may be **absent**; apply `_default_m_values` (§3.4).
3. Nothing marks the exact-match level for a TF adjustment; it must be resolved (§3.2).
4. `tf_minimum_u_value` is absent when 0 (§3.2).
5. `SettingsCreator.from_path_or_dict` deletes per-rule `sql_dialect`, so round-tripping is not identity.

Do **not** validate against `internals/files/settings_jsonschema.json` — it is documentation only, never
used for validation, and already stale.

### D2 — Blocking and `match_key`

**[v1 ERROR]** v1's D2 described "rule 2 `AND NOT` (rule 1)". The real generator emits a **single**
`AND NOT (… OR …)` over `coalesce`-wrapped rules, in the **WHERE** clause (`blocking.py:151-184`):

```sql
AND NOT (coalesce((<rule 0>),false) OR coalesce((<rule 1>),false) ...)
```

The `coalesce(…, false)` is load-bearing — without it, NULLs in a previous rule *delete* pairs. Splink's
own comment says so.

`match_key` is a **VARCHAR** string literal (`'0'`, `'1'`, …), not an integer.

Pairs are combined with `UNION ALL` — never `UNION`, never `DISTINCT`. The only dedupe is the
exploding-rule path (S4).

NULL blocking keys need **no** special handling: Splink relies purely on SQL equality semantics; there is
no NULL filter, no `COALESCE` on join keys, no `IS NOT DISTINCT FROM` anywhere in the blocking path
(verified: all-NULL rows never pair). Any `<> ''` filter is a divergence — DuckDB treats `'' = ''` as
true, so two empty-string keys *do* form a Splink pair.

### D3 — Composite unique id and pair ordering

**[v1 ERROR]** v1 used `source_dataset || '-' || source_row_id`. The separator is **`'-__-'`**
(`CONCAT_SEPARATOR`, `unique_id_concat.py:5`), joined with SQL `||`.

Per link type (`_sql_gen_where_condition`, `blocking.py:617-634`):

| link_type | WHERE |
|---|---|
| `dedupe_only`, `link_and_dedupe` | `l.<sds> \|\| '-__-' \|\| l.<uid> < r.<sds> \|\| '-__-' \|\| r.<uid>` |
| `link_only` | as above **and** `l.<sds> != r.<sds>` |
| `two_dataset_link_only`, `self_link` | `1=1` (see S2) |
| `dedupe_only`, one table, no sds column | collapses to `l."unique_id" < r."unique_id"` |

Because `||` yields VARCHAR, **ordering is lexicographic** whenever a source_dataset exists. Verified:
`('ds'||'-__-'||9) < ('ds'||'-__-'||100)` → **false**, while `9 < 100` → true. Records 9 and 100 swap
sides between `dedupe_only` and `link_and_dedupe` on identical data. This also makes `cluster_id`
lexicographic — see D4.

`lower_id_on_lhs.py` is **not** used in the predict or clustering path; its only call site is
`block_from_labels.py:39`. Canonical ordering happens at blocking time only.

### D4 — Clustering: `WITH RECURSIVE … USING KEY`, monotone min-label

**[v1 ERROR] — this was the most dangerous error in v1.** v1 said the recursive term "propagates the
smaller component label across edges via the `recurring.` pseudo-schema." The semantics are **inverted**:
inside a `USING KEY` CTE the **unqualified** name is the delta/working set, and **`recurring.<name>` is
the full accumulated table**.

Decisive test (`<A>,<B>` substituted into a counter recursion):

| `<A>, <B>` | result |
|---|---|
| `t, t` | (1,2),(2,1),(3,0) |
| `recurring.t, t` | (1,2),(2,1),(3,0) |
| `recurring.t, recurring.t` | (1,3),(2,3),(3,0) |

Driving from `recurring.` re-derives rows forever. Measured: a v1-shaped query **did not terminate**
(interrupted at 8s on a trivially-terminating shape; hung past 180s on 5,000 nodes / 15,000 edges). An
independent sweep of 250 random graphs found **214/250 failures** for the v1-shaped query.

There is a second, algorithmic trap. `USING KEY` *replaces* the row for a key, so a bare
`min(neighbour.comp)` can **raise** a node's label when a neighbour in the delta holds a higher one — the
labels oscillate and never converge. Verified: the corrected-driver-but-non-monotone form hung past 100s
on 5,000 nodes and past 90s on 300 nodes.

**The formulation that works** — delta drives, `recurring` is a lookup, and emission is guarded to strict
improvements so the label sequence decreases monotonically:

```sql
with recursive
  bidir as (
      select unique_id_l as src, unique_id_r as dst from int_edges
    union all
      select unique_id_r as src, unique_id_l as dst from int_edges
  ),
  cc(unique_id, entity_id) using key (unique_id) as (
      select unique_id, unique_id as entity_id from stg_input   -- seed from NODES → singletons appear
    union all                                                    -- UNION ALL, not UNION (see below)
      select b.dst as unique_id, min(c.entity_id) as entity_id
      from cc as c                                    -- DELTA / frontier: last iteration only
      join bidir as b          on b.src = c.unique_id
      join recurring.cc as cur on cur.unique_id = b.dst   -- FULL accumulated state
      group by b.dst, cur.entity_id                   -- MANDATORY — see "correctness traps"
      having min(c.entity_id) < cur.entity_id         -- MANDATORY termination guard
  )
select unique_id, entity_id from cc
```

> **The shipped column is `component_label`, not `entity_id` (DR-12, CURRENT 2026-08-23).** The listing
> above is kept **as executed** — the `[RECON]` runs that established termination, the mandatory `GROUP BY`
> and the monotone guard all used `entity_id`, and rewriting the evidence would break the claim it
> supports. The *name* is not what was measured. Read every `entity_id` above as `component_label`; §1.6
> says why, and Stage 6 ships it under the new name.

> **[REVIEW 2026-08-23] Fixed (F32) — RC2 is closed by `DbtBestPractices.md` B.9 / DR-24.** §7.3.1 is
> **scoped**, not waived: a `WITH RECURSIVE` clause may also contain a companion CTE that is a pure
> single-source projection of a `ref()`ed relation. `bidir` is exactly that — an orientation-doubling
> adapter — and undirected-graph recursion always needs one, so a rule every correct instance must violate
> was the wrong rule rather than a situation needing waivers. Option (a) would cost a `table` at 2× the
> edge count to hold an orientation flip; option (b) would require raising a waiver cap §7.3.3 sets to zero
> deliberately. Enforcement shares 3.68's parser gap and is labelled as review, not as a gate.
>
> <details><summary>Original review note (RC2), retained</summary>
>
> **RC2 — This formulation collides with `DbtBestPractices.md` §7.3.1 / 3.67–3.68.**
> That rule reads "a `WITH RECURSIVE` clause may contain only its recursive term or terms", and `bidir` is
> a non-recursive companion CTE. §7.3.1's own escape — the seed relation may select directly from a
> `ref()` — does not cover it: `bidir` is joined from the *recursive term*, and an undirected-graph
> recursion always needs a doubled-edge adapter, so the ban binds on the flagship model from day one
> despite §7.3.1's claim that it is "rarely binding". The collision is unregistered (the companion's B.8
> covers only ST05) and cannot be waited out: D4b keeps this query as the reference implementation.
> Options, B.8-style: (a) make `bidir` a model — a doubled-edge relation at 2×|`int_edges`| rows, `table`
> under the companion's §7; (b) a §18 `cte_waiver_reason` on `entity_clusters` (the waiver cap defaults to
> zero, so this is a visible diff); (c) scope 7.3.1 to exempt orientation-doubling adapters. D5's
> `init_params` is the second instance (see RC5 there). Register the decision as the companion's Appendix
> B.9 and name the resolution here.
>
> </details>

**Correctness traps — all three are silent:**

1. **`GROUP BY` on the key is required for correctness, not performance.** If the recursive term can emit
   more than one row for a key in a single iteration, only one survives (DuckDB keeps "the last"), the
   minimum is silently lost, and the result is *non-deterministic across threads*. Measured: at
   `threads=8` the same query on the same data returned **six different answers in six runs**.
2. **Never mix `recurring.cc` in `FROM` with a bare `cc` in the guard.** The bare name sees only the last
   delta, so the correlated guard returns NULL for un-updated nodes, `min(...) < NULL` is NULL, and those
   rows are dropped — propagation stalls early with no error.
3. **Termination is "the recursive term emits zero rows", not "the state stopped changing."** Re-emitting
   an identical row still counts as a row. Without the `HAVING` guard the recursion never ends, and
   **DuckDB 1.5.5 has no max-recursion-depth setting** — it runs until the process is killed.

**Use `UNION ALL`.** In 1.5.5 `UNION` and `UNION ALL` behave identically here, but `UNION` is deprecated
(DuckDB ships `deprecated_using_key_syntax` for exactly this) and is slated for removal in 2.1.0.

Measured on DuckDB 1.5.5, in-memory, against a Python union-find oracle (partition **and** exact
min-label equality). Correct on every shape tested: chain, star, cycle, self-loop, duplicate edges,
reverse-only orientation, disjoint components, two components joined by one edge, isolated singletons,
empty node table; and on random graphs at 5k, 20k, 50k, and 100k nodes.

| graph | nodes | edges | time |
|---|---|---|---|
| random | 5,000 | 15,000 | 0.20 s |
| random | 100,000 | 300,000 | 2.56 s |
| star | 50,000 | 50,000 | 0.27 s |
| chain | 10,000 | 10,000 | **93.71 s** |

**Determinism:** 20 runs (10 at `threads=1`, 10 at `threads=8`) on a 3,000-node / 9,000-edge graph
produced exactly **one** SHA-256 digest of the ordered result. Min-label is order-independent because the
fixpoint — `min(unique_id)` per component — is unique regardless of iteration order.

**Agreement with Splink: label-identical, not merely partition-identical.** Splink seeds
`node_id as representative` and iterates `min(representative)`, so its fixpoint is also the component
minimum, for BIGINT and for VARCHAR composite ids.

**Two deliberate disagreements, where we are correct and Splink is not.** Splink's `LEFT JOIN` in
`_cc_update_representatives_loop_cond` emits a spurious **NULL-keyed row** for (a) an edge whose endpoint
is absent from the nodes table, and (b) an edge with a NULL endpoint. The `USING KEY` query drops both.
These go in the divergence log as *improvements*, and the parity comparator must special-case them.

### D4a — Clustering cost, and an honest performance statement

**The cost model is `O(iterations × edges)` with `iterations ≈ graph diameter`** (a 2,000-node chain runs
exactly 1,999 iterations). The failure mode is *deep* components, not many edges: chains scale
super-linearly — 1k = 2.1 s, 5k = 22.8 s, 10k = 63.2 s, 20k = 523.3 s — while a 500k-node / 1M-edge
random graph completes in 39.3 s as a single statement.

Splink's own loop is **also** O(diameter) — measured at exactly `L-1` iterations on a chain of length `L`
— so the complexity class is shared. But the constant factor is not, and this must be stated plainly:

| scale | Splink's Python-driven loop | single `USING KEY` statement | ratio |
|---|---|---|---|
| 200k nodes | — | — | **3.4× slower** |
| 1M nodes | — | — | **8.3× slower** |
| 3M nodes / 10M edges | **22.1 s** | **409.9 s** | **18.5× slower** |

On identical in-memory data, in the same process, producing identical results, **Splink's
materialised-temp-table loop beats the single-statement recursive CTE, and the gap widens with scale.**
The 10M-edge case is exactly the scale v1's Stage 6 acceptance criterion named.

This does not invalidate the approach — correctness, determinism, zero Python, and one inspectable
statement are the goals — but it does mean **"pure SQL is faster" is not a claim this project can make
about clustering**, and Stage 6's budget must be set with that known. The mitigation if it becomes
binding is to materialise iterations (matching Splink's strategy) driven by dbt rather than by Python,
trading the single-statement property for speed.

For reference, the *classic* non-`USING KEY` transitive-closure recursion is not an option at all: it
materialises O(Σ|component|²) reachability pairs and measured **300× slower at 2,000 nodes**, timing out
from 5,000 upward.

**Memory:** the keyed table is memory-resident, respects `SET memory_limit`, throws `OutOfMemoryException`
rather than growing unbounded, and **does not spill to disk**. Working set ≈ 10–20× the base node+edge
tables: 200k nodes / 600k edges (14.8 MiB base) needs ≥ 256 MB; 1M nodes / 3M edges (72 MiB base) needs
≳ 1 GB.

### D4b — A custom `iterative_fixpoint` materialization — **prototyped and working**

**Status: proven in real dbt; scheduled after Stage 6 parity.** Deferring is safe, and adopting it later
is safe, for one reason: **min-label propagation has a unique fixpoint** (`min(unique_id)` per component),
so any correct algorithm returns bit-identical output. Clustering performance work cannot cost parity —
only the tests that prove it need to keep passing.

**It is still pure SQL in dbt.** dbt's own `table` and `incremental` materializations are Jinja macros that
run several statements; `iterative_fixpoint` is no less dbt-native. The loop is Jinja orchestration over
SQL — no Python in the run.

**Verified end-to-end** on dbt-core 1.12.2 / dbt-duckdb 1.11.0 / DuckDB 1.5.5, on a 10,000-node chain (the
pathological deep-diameter case):

```
04:05:51  iterative_fixpoint: converged after 14 iterations
04:05:51  OK created sql iterative_fixpoint model main.entity_clusters [OK in 2.33s]
04:05:51  Completed successfully — PASS=3 WARN=0 ERROR=0

rows 10000 · components 1 · all labels = 0 (correct min-label) · temps cleaned up · re-run idempotent
```

**2.33 s versus 206.63 s** for the recursive CTE on the same graph, because the step does hooking *plus*
pointer jumping (`rep[v] = rep[rep[v]]`) — 14 iterations instead of ~10,000.

> **[REVIEW 2026-08-23] RC3 — Sixty-one lines of working Jinja is implementation, not design.** The
> standing rule for these documents is code brief and by reference. The design content here is the four
> traps and the measured claim, and the traps prose below already names its load-bearing lines
> (`namespace()`, `statement('main')`, `adapter.commit()`, parse-time `ref()` invisibility). Given the
> scaffold-deletion history (companion Appendix D), if this listing is the prototype's only surviving
> copy, keep it labelled explicitly as a preserved artifact pending Stage 0 — then land it as
> `macros/materializations/iterative_fixpoint.sql` when D4b is picked up, and reduce this section to the
> traps plus a path reference.

```sql
{% materialization iterative_fixpoint, adapter='duckdb' %}
  {%- set target_relation  = this.incorporate(type='table') -%}
  {%- set step_macro       = config.require('step_macro') -%}
  {%- set max_iterations   = config.get('max_iterations', 100) -%}

  {{ run_hooks(pre_hooks, inside_transaction=False) }}
  {{ run_hooks(pre_hooks, inside_transaction=True) }}

  {#- TRAP 2: `prev` MUST live on a namespace (see below) -#}
  {%- set state = namespace(converged=false, iters=0,
        prev=api.Relation.create(database=target_relation.database,
              schema=target_relation.schema,
              identifier=target_relation.identifier ~ '__it_0', type='table')) -%}

  {% call statement('seed') %}
    create or replace table {{ state.prev }} as ({{ sql }})
  {% endcall %}

  {% for i in range(1, max_iterations + 1) %}
    {%- set nxt = api.Relation.create(database=target_relation.database,
          schema=target_relation.schema,
          identifier=target_relation.identifier ~ '__it_' ~ i, type='table') -%}

    {% call statement('step_' ~ i) %}
      create or replace table {{ nxt }} as ({{ context[step_macro](state.prev) }})
    {% endcall %}

    {%- set changed_sql -%}
      select count(*) as n from (
        select * from {{ nxt }} except select * from {{ state.prev }}) d
    {%- endset -%}
    {%- set changed = run_query(changed_sql).columns[0][0] -%}

    {% do adapter.drop_relation(state.prev) %}
    {%- set state.prev = nxt -%}
    {%- set state.iters = i -%}

    {% if changed == 0 %}
      {%- set state.converged = true -%}
      {% do log("iterative_fixpoint: converged after " ~ i ~ " iterations", info=True) %}
      {% break %}
    {% endif %}
  {% endfor %}

  {#- Non-convergence is an ERROR, never a silent truncation -#}
  {% if not state.converged %}
    {% do exceptions.raise_compiler_error(
         "iterative_fixpoint: did NOT converge within max_iterations=" ~ max_iterations) %}
  {% endif %}

  {#- TRAP 3: the statement must be named 'main' -#}
  {% call statement('main') %}
    create or replace table {{ target_relation }} as (select * from {{ state.prev }})
  {% endcall %}
  {% do adapter.drop_relation(state.prev) %}

  {{ run_hooks(post_hooks, inside_transaction=True) }}
  {{ adapter.commit() }}          {#- TRAP 4: MANDATORY -#}
  {{ run_hooks(post_hooks, inside_transaction=False) }}
  {{ return({'relations': [target_relation]}) }}
{% endmaterialization %}
```

**Four traps, all found by building it, all silent:**

1. **`ref()` inside the step macro is invisible to dbt's parse-time dependency extractor.** The model needs
   an explicit `-- depends_on: {{ ref('sym_edges') }}` hint, or the DAG edge is missing and the model can
   build before its input exists. dbt does detect this one and names the fix.
2. **Jinja loop scoping.** `{% set prev = nxt %}` inside a `for` body is scoped to that iteration; the next
   pass reverts to the relation just dropped, giving `Table ... __it_0 does not exist`. The iteration
   relation must live on a `namespace()`.
3. **A statement must literally be named `main`**, or the run fails with
   `main is not being called during running model` — dbt's runner does `load_result('main')`.
4. **`adapter.commit()` is mandatory.** Without it dbt issues a `ROLLBACK` at connection cleanup, discards
   every statement, and **still reports the model as `OK`**. Observed directly: `dbt run` printed
   `Completed successfully — PASS=3` while `entity_clusters` did not exist in the database. This is the
   most dangerous failure mode in the whole design, because CI would be green on an empty warehouse.

**Testing requirement:** a custom materialization bypasses `duckdb__create_table_as`, so **model contracts
are not enforced unless the materialization calls the contract path itself**. Add a test that asserts the
relation exists and is non-empty after `dbt run` — trap 4 proves "dbt said OK" is not evidence.

**When to pick this up:** after Stage 6 parity is green, or earlier if the iteration guardrail fires on
real data. The recursive CTE stays as the reference implementation the materialization is differentially
tested against — same input, assert identical labels.

**But materialising iterations is not where the win is.** It is the same O(diameter) algorithm with a
better constant. The win is that a materialization can run an algorithm the recursive CTE cannot express
cleanly — **pointer jumping** (`rep[v] = rep[rep[v]]`), which needs a self-join of the *full* state and
collapses O(diameter) to O(log diameter).

Measured (DuckDB 1.5.5, `threads=4`, in-memory, all verified against a union-find oracle):

| graph | recursive CTE | materialised loop | loop + pointer jumping |
|---|---|---|---|
| random, 100k nodes / 300k edges | 5.71 s | 3.41 s (10 iters) | **3.35 s** (8 iters) |
| chain, 10k nodes | 206.63 s | did not converge within a 3,001-iteration cap (needs ~10,000) | **0.92 s** (14 iters) |

A **~225× speedup on the pathological case**, and it removes the deep-component risk (§7) rather than
guarding against it. Note the plain materialised loop does *not* help on chains — only shortcutting does.

> **[REVIEW 2026-08-23] Fixed (F3):** a second, near-verbatim copy of the "**When to pick this up:**"
> paragraph stood here ("starts firing" for "fires"; otherwise identical to the one above the pointer-jumping
> digression) — an editing artifact three adversarial passes did not catch. Deleted: two copies of a
> scheduling rule is how one gets updated and the other becomes the version somebody implements.

Other verified clustering facts:
- **`cluster_id` = `min(unique_id)` over the component**, and because of D3 that minimum is
  **lexicographic** on the composite string: nodes `['a-10','a-2','a-9']` get `cluster_id = 'a-10'`.
- **Singletons are included**, and nodes come from the **concat table**, never inferred from edges
  (`connected_components.py:196-205`). An edge referencing a node absent from the nodes table makes
  Splink emit a **NULL node id** row — a defect worth asserting against.
- Splink's convergence test is "no edge remains whose endpoints have different representatives", not
  v1's "no label decreases". Equivalent at fixpoint, different predicate.
- **`materialized: table`.** v1 justified this by a dbt/recursive-CTE interaction; that justification is
  **obsolete**. dbt-core 1.12.2 inserts injected CTEs *after* the `RECURSIVE` keyword, and dbt's real
  `inject_ctes_into_sql` output executed correctly in DuckDB across four shapes including an ephemeral
  parent whose own body is a `USING KEY` recursion. Keep `table` for the performance reason; `int_edges`
  may be ephemeral.

### D5 — EM as a single recursive CTE

**[v1 ERROR]** v1 made EM a non-goal and its single-statement form "blocked on DuckDB 2.0 stable". Both
are wrong. A complete EM — E-step, M-step, per-comparison normalisation, lambda update, convergence test
and iteration cap — was built as **one `WITH RECURSIVE … USING KEY` statement** and matched a Python
reference implementing Splink's formulas to **3.9e-16** with an identical iteration count.

Two DuckDB facts make it work, both non-standard and both verified:
1. **Aggregates and window functions are allowed in the recursive term** — `sum() OVER ()`,
   `sum() OVER (PARTITION BY …)`, `GROUP BY` with `sum()`/`max()`, and correlated scalar subqueries over
   static tables all execute.
2. **`USING KEY` permits multiple references to the CTE** in the recursive term — needed to read both the
   parameter values and the previous delta for the convergence test.

Shape — key is `(comparison, level)`, scalar state replicated on every row:

```sql
WITH RECURSIVE em(comp, lvl, m, u, lam, iter, maxchg) USING KEY (comp, lvl) AS (
    SELECT comp, lvl, m, u, CAST(<lam0> AS DOUBLE), 0, CAST(1.0 AS DOUBLE) FROM init_params
  UNION
    SELECT comp, lvl, m_new, u_new, lam_new, iter,
           max(greatest(abs(m_new-m_old), abs(u_new-u_old), abs(lam_new-lam_old))) OVER () AS maxchg
    FROM ( ... E-step over agreement patterns, M-step normalisation ... )
    WHERE iter < <max_iterations> AND maxchg >= <em_convergence>
)
```

> **[REVIEW 2026-08-23] RC5 — The sketch disagrees with D4 on `UNION`, and `init_params` is unresolved.**
> The recursion above is driven with bare `UNION` where D4, for the identical `USING KEY` construct,
> rules: "Use `UNION ALL`. In 1.5.5 `UNION` and `UNION ALL` behave identically here, but `UNION` is
> deprecated (DuckDB ships `deprecated_using_key_syntax` for exactly this) and is slated for removal in
> 2.1.0." Change to `UNION ALL`, or state why EM specifically needs `UNION` semantics — if it does, that
> is a D4/D5 disagreement worth a sentence, not a silent divergence between the document's only two
> recursive models. Separately, `FROM init_params` is legal under `DbtBestPractices.md` §7.3.1 only if
> `init_params` is a model — as a companion CTE it is the second instance of the collision flagged at D4
> (`bidir`), and both belong to the same B.9 decision proposed there.

**The real obstacle v1 never identified:** Splink interpolates the current parameters as **SQL literals**
into each iteration, so every iteration is a *different query*. A pure-SQL version must carry parameters
as **data** and have the E-step join to them. That is the whole design change.

Two Splink behaviours must be replicated:
- **The agreement-pattern collapse.** `count_agreement_patterns_sql` reduces the comparison-vector table
  to one row per distinct gamma vector. For 6 comparisons at ≤5 levels that is **≤ 15,625 rows**, not
  millions of pairs. Opt-in in Splink via `estimate_without_term_frequencies` (default `False`).
- **Per-session semantics.** One EM session per blocking rule. Within a session, every comparison whose
  columns intersect the training rule's columns is **removed** and not estimated
  (`em_training_session.py:98-122`), and `probability_two_random_records_match` is replaced by a
  blocking-adjusted value (`:123-125, 289-320`). Results across sessions are combined by **MEDIAN**, not
  by the lookup module — a detail easy to get wrong.

Defaults: `max_iterations = 25`, `em_convergence = 0.0001`; convergence is the max |Δ| over every non-null
level's m and u plus |Δλ|, and null levels are explicitly skipped.

**Fallback.** If a future DuckDB removes recursive-term aggregates, Jinja-unrolled iterations
(`{% for i in range(max_iter) %}`) produce the same result with a bounded, non-hanging cost. Keep the
unrolled path behind a var as the escape hatch.

### D6 — Comparison level SQL is passed through verbatim

**Confirmed.** All 21 comparison-level types in `comparison_level_library` render to pure scalar DuckDB
expressions — no subquery, no UDF, no Python at runtime — and every one executed in DuckDB 1.5.5.

Two need care in the macro:
- `PairwiseStringDistanceFunctionLevel` uses DuckDB **lambdas** (`list_transform`, `flatten`) and carries
  `@unsupported_splink_dialects(["sqlite","postgres","athena"])`.
- `PercentageDifferenceLevel` and `DistanceInKMLevel` contain a **nested CASE** that must be spliced into
  the outer gamma CASE without breaking `WHEN`/`THEN` parsing.

**The allow-list, normative and closed** (§1.5 rule 1, DR-17): `levenshtein, damerau_levenshtein,
jaro_similarity, jaro_winkler_similarity, jaccard, array_cosine_similarity, list_intersect,
array_intersect, array_length, list_max, list_min, list_transform, flatten, try_strptime, epoch, radians,
acos, sin, cos, least, greatest, regexp_extract, nullif, substring, lower, date_trunc, split_part`.

**This is an allow-list, not a lint suggestion.** v1 introduced it as a *"lint whitelist"*, which said
nothing about where it is enforced, what it is matched against, or what a violation does — and G3 records
that gap as the finding. **§1.5 answers all three**: enforcement is at compile time in the A.2 sidecar,
matching is against the **parsed tree** rather than the raw string, and a violation fails the build naming
the offending function. Non-deterministic functions are rejected whether or not they appear above.

Adding to this list is a reviewed act with the determinism argument written down. It is not a
configuration knob, because verbatim passthrough is what makes the model JSON arbitrary SQL executed with
the consumer's warehouse credentials.

### D7 — One long-format TF model, not one model per column

**[v1 ERROR]** v1 §2.1 specified `models/intermediate/tf/` holding "generated-per-column TF models
(macro-driven)". **dbt cannot do this.** Models are discovered by filesystem search over `model-paths`,
one node per file; there is no parse-time API to synthesize a model with SQL. The `dbt.plugins`
`PluginNodes.add_model` escape takes `ModelNodeArgs` with **no `raw_code`** — those describe external
relations dbt never builds, and the API is marked experimental.

**Corrected:** a single `tf_all(column_name, value, tf)` model, one macro emitting one `UNION ALL` branch
per TF column from the var. Fixed 3-column schema keeps it contract-able and keeps one node in the DAG.
Splink's per-column `__splink__df_tf_<col>` is still comparable by filtering.

### D7a — TF must be **frozen by snapshot**, not recomputed from the live corpus

*Resolves Appendix A, B3. This changes `tf_all`'s shape, so it must be settled before Stage 2 ships.*

§3.5 describes how Splink *computes* TF, and a naive reading of D7 recomputes it from `stg_input` on
every run. **That is the wrong default**, for a reason that has nothing to do with parity: TF is a
property of the whole corpus, so recomputing it makes every pair's score a function of unrelated records.

Measured magnitude: with `u_exact = 0.001` and `POW(u_exact / GREATEST(tf_l, tf_r), w)`, a value whose
`tf` moves from 0.0909 to 0.45 because **nine unrelated records arrived** shifts that comparison's
contribution from **−6.51 to −8.81 bits** — on a pair whose own two records did not change. Entities
churn on ingest of data that has nothing to do with them.

The incumbent already solved this and the solution is load-bearing there: `src/er/matching/tf.py` calls
`register_term_frequency_lookup` per column and **never** `compute_tf_table` (a unit test guards that
`compute_tf_table` has no call site), backed by a `tf_lookup` relation keyed
`(model_version, tf_snapshot_id, column_name, value, tf_value)`. A missing row is a precondition failure,
never a silent fallback.

**Decision.**

1. **`tf_all` sources from a frozen snapshot** keyed `(er_model_sha, er_tf_snapshot_id, column_name,
   value, tf)`. This is the default and the mode used for every parity run.
2. **A missing value is an error, not a fallback.** Do not `COALESCE` an absent `tf` — §3.2 shows Splink's
   NULL semantics are "substitute the other side, or no adjustment if neither", which is *not* the same
   as treating the value as unseen. Silently defaulting produces wrong scores that look plausible.
3. **Live-corpus TF becomes the opt-in path used only when minting a new snapshot**
   (`var('er_tf_mode', 'frozen')`).
4. **A TF refresh is an explicit, dated operation** that mints a new `er_tf_snapshot_id` and re-scores, so
   drift is a reviewable event rather than a side effect of ingest.

Two consequences of freezing, both of which belong to this decision rather than to the stage that
discovers them. **The refresh is a full rescore of the whole corpus**, not of the changed records —
`er_tf_snapshot_id` is part of every scored row's identity (M8) — so it is the largest recurring cost in
the system: see **G7**. And **the snapshot outlives the records that produced it**: it is a value
distribution over the corpus, so erasing a source record does not erase its contribution, *by design*.
An erasure event is therefore a refresh trigger — see **G4**.

**This is what makes Stage 8's acceptance criterion meaningful.** The 80/20 full-vs-incremental
equivalence test is unachievable *by construction* under live TF, because the two arms see different
corpora and therefore different `tf` values. Under frozen TF it becomes a real assertion. Add the
companion AC: *re-scoring the same pair under the same `(er_model_sha, er_tf_snapshot_id)` yields a
bit-identical `match_weight` regardless of what else is in the corpus.*

### D8 — `stg_input` is a bare passthrough

**Confirmed and now mandatory.** `ColumnExpression` transforms (`lower()`, `substr`, `try_strptime`) are
applied **inline inside the comparison CASE**, never in the concat table. Verified: with
`ColumnExpression('first_name').lower().substr(1,3)` the gamma CASE contained
`SUBSTRING(LOWER("first_name_l"), 1, 3)` while the projection feeding it selected the **raw** column.

So hoisting a transform into `stg_input` would diverge. v1's "No cleaning logic here" rule is correct —
and the reason is stronger than v1 gave.

### D9 — u-estimation is reproducible

**[v1 ERROR]** v1's Stage 10.3 said parity "may require injecting the sample rather than sampling
in-engine." Not needed. DuckDB emits `USING SAMPLE bernoulli(<pct>%) REPEATABLE(<seed>)` when a seed is
given (`dialects.py:308-317`), and Splink first wraps the source in `(select * from __splink__df_concat
order by <unique_id>)` for exactly this reason (`estimate_u.py:127-148`).

Measured: `USING SAMPLE bernoulli(10%) REPEATABLE(7)` over 100,000 ordered rows returned **identical row
sets across repeat runs and across `SET threads=1/4/8`**.

Sample size (dedupe): `sample_rows = 0.5*((8*max_pairs + 1)**0.5 + 1)`, `proportion = sample_rows/total_nodes`,
both clamped to 1.0. For `link_only`: `proportion = (max_pairs/total_links)**0.5`. During estimation, TF
adjustments are disabled on every level and `match_probability` is forced to `cast(0.0 as float8)`.

### D10 — Survivorship (unchanged from v1 in intent)

Macro-generated window SQL driven by a seed: `attribute, strategy, order_col, tiebreak`, emitting
`ARG_MAX`/`FIRST_VALUE … OVER (PARTITION BY component_label ORDER BY <keys>, unique_id)`. The trailing
`unique_id` makes every chain a total order. Output carries `<attr>__source_record` and
`<attr>__rule_applied` lineage.

`component_label` is the partition column here and **nothing more** (§1.6, DR-12). It groups the members of
one component for the duration of one run; it does not identify the golden record across runs, and
`golden_records` must not be keyed on it by anything downstream. A consumer wanting a durable key gets it
from the platform's permanence layer, which is where DR-14 puts that responsibility.

One rule v1 omitted and which matters in practice: **multi-column attributes must survive as a unit.**
When an address wins, every `addr_*` column comes from the *single* winning record — never assembled
field-by-field across records, which produces incoherent composites.

### D11 — Materialisation contract: **always `table`, always narrow**

**Decision: every stage model is `materialized='table'`.** No `ephemeral` intermediates, no fused-vs-staged
mode switch. *(This decision supersedes A.1 B1 rec 1, A.5 Stage 0.6 and A.7 Thesis 2, all three of which
still argued for `ephemeral` and are now marked. `DbtBestPractices.md` §7 implements the superseded
branch and must be rewritten — see §B.1 **G1** and §B.2 **R1**. Under the precedence rule in the v2 note,
this body section wins.)* Rationale: every stage stays a real, queryable relation, so the parity harness can compare
any stage directly, stage decoupling (§5) needs no special-casing, and a failing run can be inspected
where it failed. This is a deliberate trade of scale headroom for inspectability and debuggability.

> **[REVIEW 2026-08-23] RC6 — The parenthetical above is stale as of `DbtBestPractices.md` v2.1
> (2026-08-23).** Its §7 has been rewritten to this contract — the header there reads "D11 supersedes B1.
> §7 below is D11's contract" — and its Appendix E marks R1 **Closed** (§7, §7.1, §7.2, A.4, C.1 deltas
> 1–4); its §1.1 also gained the tier-2 precedence rule under which a D-number outranks the measurement it
> reinterprets. Recast "must be rewritten" in the past tense, and note the same fact is corrected at its
> other live sites in this pass (G1 rec 2, R1, DR-01) — left uncorrected, this document held four live
> statements of one superseded fact about its companion, the exact shape G1 diagnosed.

**Appendix A B1 argues against this** — it measured one-model-per-CTE at 946 B/pair versus Splink's fused
54 B/pair (17.6×) and derived a ~2.75M-record ceiling. That finding stands as measured, but it conflates
two costs, and separating them changes the conclusion:

| relation | B/pair (measured, 200k records → 3.9M pairs, 6 comparisons) |
|---|---|
| `int_candidate_pairs` | 30.2 |
| comparison vectors, **narrow** — ids + `match_key` + 6 `gamma_*` | **69.4** |
| comparison vectors, **wide** — narrow + `_l`/`_r` source-column passthrough | **267.9** (**3.9×** narrow) |
| pairs + narrow | **99.7** |
| pairs + wide | **298.1** (**3.0×**) |

At a 40 GB DuckDB `memory_limit`: **401M pairs narrow vs 134M wide.** The dominant cost is the `_l`/`_r`
passthrough, not the decision to materialise. *(Measured on DuckDB 1.5.5, threads=4, in-memory, via
`duckdb_memory()` tag `IN_MEMORY_TABLE`. Covers `int_candidate_pairs` + comparison vectors only —
`int_scored_pairs` is not in this measurement, so it is not directly comparable to B1's 946 B/pair
figure, which spans stages 3–5 at full retain semantics.)*

**Therefore `table` everywhere is affordable, on one condition — narrowness is normative:**

1. **`int_comparison_vectors` carries `unique_id_l`, `unique_id_r`, `match_key`, and the `gamma_*` columns
   only.** No source-column passthrough. Splink's `retain_matching_columns` shape is a debugging
   convenience, not a parity requirement — gammas are what the next stage consumes.
2. **`int_scored_pairs` carries ids, `match_weight`, `match_probability`, and the per-comparison
   `bf_*` / `bf_tf_adj_*` columns.** Anything a human wants to read alongside a pair is re-derivable by
   joining `stg_input` on two ids.
3. **A `wide` debug variant is opt-in per run** (`var('er_retain_matching_columns', false)`), used by the
   parity harness and by pair-level investigation — never the default, never in production.
4. **Compute the clamped Bayes-factor product once** in an inner subquery and derive both `match_weight`
   and `match_probability` from that column. Splink's own projection repeats the product three times
   (`predict.py:196-220`); evaluating it once is both cheaper and strictly better for float parity (§3.1).
5. **`make capacity`** reports measured `er_bytes_per_pair` for the active model JSON and derives
   `er_max_pairs = memory_limit × headroom / bytes_per_pair`. This replaces Splink's ported
   `max_rows_limit = 1e9`, which is a *per-rule* row cap and says nothing about total bytes.

**Accepted consequence.** Even narrow, this stays above Splink's fused 54 B/pair, so a single-node ceiling
below Splink's remains real. It is accepted deliberately: the project's goal is the pipeline in dbt as
inspectable SQL, and performance work is sequenced after parity (see D4b for the same trade on
clustering). The ceiling must be *published* in `PARITY.md`, not discovered in production.

**Follow-through required in the current project config.** `dbt_project.yml` predates this decision and
still encodes the ephemeral strategy. Three things need reconciling:

| current | required by D11 |
|---|---|
| `er_materialise_intermediates: false` — "the two widest intermediates go ephemeral and dbt fuses them back into a single CTAS" | Remove the var, or default it `true`. Under D11 there is no ephemeral path to fall back to; keeping a var that can silently switch materialisation defeats the inspectability the decision was made for |
| `er_allowed_materializations: ["table", "ephemeral"]` | Drop `ephemeral` — the policy list should not permit what the contract forbids |
| `er_max_pairs: 42000000`, commented as derived from 946 B/pair | Re-derive. That figure came from the **wide** shape; at the measured narrow ~100 B/pair the same 40 GB budget admits ~400M pairs. Deriving the cap from a byte cost the project no longer incurs under-provisions by ~10× |

The `er_must_be_table: ["er_entity_clusters"]` list becomes redundant under a blanket `table` contract,
but is harmless as a belt-and-braces assertion — and it will matter again if D4b's custom materialization
lands, since `iterative_fixpoint` is neither `table` nor `ephemeral`. Add it to
`er_allowed_materializations` at that point, not before.

### D12 — Every model ships unit tests, and their cases are decided when the model is written

**Decision: every model in this package carries at least one dbt unit test, and the set of cases that
test must cover is chosen while the model is being built — in the same PR that adds the model, not in a
follow-up ticket.** Reference: [dbt — Unit tests](https://docs.getdbt.com/docs/build/unit-tests?version=2).

*Supersedes:* Appendix A **M17 rec (c)** — *"keep dbt unit tests for fixed-schema, non-recursive models
(`stg_input`, `tf_all`, `int_edges`, `golden_records`, `cluster_membership`); move gamma/bf/clustering
logic tests into the pytest harness"* — which scoped unit tests to those five models on the strength of
two constraints that M17's own evidence withdrew (recursion) or that M2 solved (JSON-derived columns). In
`DbtBestPractices.md` this invalidates **3.20**'s "fixed-schema models" scope, **§12.2**'s
recursive-SQL exclusion, **§18.1**'s worked exemption example (whose stated reason is that exclusion), and
Appendix **C.4**'s automatic `'recursive' not in sql_features` branch. Registered as **DR-21**.

**Why every model rather than the models where it is cheap.** dbt's own guidance says to unit test models
with complex logic, models that are public or contracted, models upstream of an exposure, and models where
a bug has already been found. Applied to this DAG that filter selects all of it: every model is contracted
(`DbtBestPractices.md` 3.3), every model is a parity artefact, and the pipeline is a single chain in which
one wrong `gamma_*` reaches the golden records. The one case dbt's guidance says not to bother with — a
model that only wraps a warehouse built-in — does not occur here.

**What a unit test proves here, and what it does not.** Its expected rows are written by us, so it pins
*our stated intent*, never equivalence with Splink: a unit test whose expectation encodes a misreading of
the model JSON is a faithfully-pinned bug with a green tick beside it. So unit tests pin the rules this
document states — D8's bare passthrough, §3.3's gamma numbering, D2/S4's replicated `min(match_key)`
VARCHAR behaviour, §3.1's clamp, D4's monotone min-label, D7a's raise-on-missing-TF — and the parity
harness pins equivalence to Splink. Neither substitutes for the other, and §6.4 keeps them as separate
layers for that reason.

**"Decided when the model is written" means six questions, answered in the PR that adds the model:**

1. **Which numbered rule does this model implement?** (D-number or §.) That rule is case 1, written as an
   input row that would violate it and an expected row that does not.
2. **Which branches does its SQL contain** — every `CASE` arm, every `COALESCE`, every join type? One case
   per arm, *including* the arm the fixture corpus never produces. This is the unit-test half of M9's
   finding that 5 of 18 gamma cells were never observed in 101,797 pairs: hand-built rows can reach a
   branch that generated data cannot.
3. **Which boundary constants does it read from the model JSON or from a var?** One case either side of
   each. This is what catches `>` against `>=` (§3.3, Stage 4's AC, and the `int_edges` threshold in D2).
4. **What happens to NULL, to empty string, and to a duplicate key on each input?**
5. **Which published behaviour would a well-meaning refactor break?** For `stg_input` that is someone
   adding a `lower()` or a `trim()`; the case exists so the diff fails immediately rather than surfacing
   later as a handful of wrong gammas in a parity report.
6. **Which bug has this model already had?** Every fixed defect earns a case named for the defect.

Where a question has no answer for a given model, the answer is *recorded* in that model's properties file
alongside the tests — a stated "no branches" is reviewable, a blank is not.

**Three constraints this decision inherits. None of them is a reason to skip a model:**

- **Exact equality.** dbt compares actual against expected in Python over sorted rows, with no tolerance
  (§6.1; `DbtBestPractices.md` §12.3). Fixture m/u values are therefore chosen so Bayes factors are exact
  powers of two, which makes `int_scored_pairs` unit-testable at all. Anything that genuinely needs a
  tolerance is a harness comparison and does not belong in a unit test.
- **Parents must already exist.** M17's `[SRC]` finding: the `unit` materialization reads column types
  back from the parent relations. `dbt run --empty --select <parents>` satisfies that cheaply, and is a
  required step in every job that runs unit tests.
- **JSON-derived column sets.** `int_comparison_vectors` and `int_scored_pairs` have a column set that is
  data, not source code (M2). Their fixtures must be generated from the same model JSON that produces
  `er_gamma_columns` / `er_bf_columns`, by the same wrapper, and M2's drift-guard pytest extends to the
  fixture as well as the contract. This is the one place where D12 costs real machinery, and it is
  **Stage 1** work rather than Stage 4 work — the same "a Stage-1 decision being made in Stage 4" trap M2
  describes.

**Recursive SQL is not an exemption.** docs.getdbt.com lists recursive SQL as unsupported for unit tests;
M17(c) `[RECON]` found that all three dbt wrapper shapes execute correctly against a `USING KEY` model on
DuckDB 1.5.5. For the toolchain we pin, the measurement outranks the documentation — and it is
`[UNVERIFIED]` for any other, so it is re-checked on every dbt or DuckDB bump alongside the D4 gate.
`entity_clusters` is therefore unit-tested like everything else, with the pytest harness covering it *in
addition* rather than *instead*. If a bump breaks it, the result is a **dated waiver naming the version
that broke it** — per-check, reasoned, capped, echoed on every run (`DbtBestPractices.md` 3.43) — never a
standing policy exclusion, because a standing exclusion is precisely what this decision removes.

**"Every model" needs a model list, and this document does not have one.** **G6** records that no single
place enumerates what the package ships and that the three partial enumerations disagree; **DR-11** records
the same for the stage list. A coverage rule quantified over an undefined set is enforced only by whatever
the manifest happens to contain — which is a real gate, but not the one this decision states. The §2.1
model inventory G6 recommends is therefore the subject list for D12 and for `DbtBestPractices.md` 3.20, and
it acquires one more column when it is written: **the unit-test cases each model's tests must cover.** The
starter table in §5 is scoped to those cases and is deliberately not a fourth enumeration — where it and
the inventory disagree, the inventory is right and the table is stale.

**Cost, stated rather than discovered.** A new model now costs its unit tests before it can merge, and for
the two JSON-derived models it costs generated fixtures. That is the same trade `DbtBestPractices.md` §7.3
makes for the CTE ban: pay at the moment the stage is created, rather than while bisecting a parity
failure through a model nobody can localise inside.

---

## 5. Staged plan

Stages are numbered as in v1 where they correspond. **Stage 0 gains a spike; training and evaluation are
promoted from stretch goal to real stages.**

**Supersedes:** A.5 (Corrected stage list) as a *second inventory*. A.5's content is absorbed here and A.5
is reduced to a provenance record. Also supersedes: §5 Stage 4's "every distinct threshold constant" AC,
§5 Stage 9's EM acceptance criterion, and the Stage-decoupling mechanism as written in v2 — each named at
the point it changed.

> **[REVIEW 2026-08-23] Fixed (F13) — RC7, RC8, RC11, RC12, RC29 and R3 are closed by this revision.**
> DR-11 was the register's only `CONFLICT`: §5 and A.5 were two normative inventories, and *"a reader
> planning from §5 builds a different programme from one planning from A.5."* This section is now the
> single inventory. The merge was executed from **RC29's enumeration**, not R3's own — RC29 records that
> R3's delta list is materially incomplete, and it is the longer list. What moved:
>
> | From A.5 | Landed at |
> |---|---|
> | Stages 2b, 6b, 12b — absent from §5 entirely | New sections below |
> | Sub-stages 0.6 (capacity) and 0.7 (comparator suite) | Stage 0 |
> | 0.3's three extensions — both retain flags, ground-truth labels, training traces | Stage 0.3 |
> | The critical path, and day-one parallelism | Stage 0.0 and Stage decoupling |
> | ~12 per-stage extensions | Each stage's own section |
> | The one *direct* textual conflict — Stage 4's threshold constants | Stage 4, resolved in A.5's favour |
>
> Two items that were in **neither** list also land here: **Stage 0.0 pre-flight** (RC8 — five "do this
> first" directives coexisted and none ordered the others) and **Stage 0.9, the scaffold rebuild**, which
> `DbtBestPractices.md` Appendix D records as deleted and which appeared in no task list in either
> document.
>
> **Decided under delegated authority 2026-08-23.**
> **Recommendation source:** R3 (*"reconcile to one list"*), scoped by RC29.
> **Reversible:** reopen DR-11 in §B.3.

### Unit tests are a stage deliverable, not a follow-up (D12)

Per **D12**, a stage is not done when its models build and its parity gate is green. It is done when each
model it adds also carries the unit tests that pin what that model is *supposed* to do, and those cases are
chosen while the model is written — by answering D12's six questions in the same PR. The acceptance
criteria below are parity and behaviour criteria; **D12's criterion applies to every stage in addition to
them**, and `DbtBestPractices.md` 3.20 is the gate that fails a model arriving without one.

The table below is a **starting point, not the answer**: it records the cases that are already decidable
from this document today, so that the per-model exercise starts from the rules rather than from a blank
page. Each row is expected to grow when its model is actually written — that growth is the deliverable.

| Stage | Model | Cases decidable today, from the rules this document states |
|---|---|---|
| 2 | `stg_input` | D8 bare passthrough: mixed case, leading/trailing whitespace, an unparseable date, and NULL attributes all survive unchanged. Excludes `__splink_salt` (S1) |
| 2 | `tf_all` | Non-null denominator (§3.5): a column with NULLs still sums to 1.0. D7a: a value absent from the frozen snapshot **raises**, and is not `COALESCE`d. Long-format grain, one row per `(column_name, value)` |
| 3 | `int_candidate_pairs` | `match_key` is VARCHAR and is compared as VARCHAR (`blocking.py:203-206`). Overlapping rules dedupe to the S4 `min()`-on-VARCHAR result with ≥11 rules — the replicated bug gets its own named case. NULL and empty-string blocking keys. D3 pair ordering |
| 4 | `int_comparison_vectors` | §3.3 gamma numbering: seeded at the non-null level count, descending, with the JSON list order and gamma order deliberately disagreeing in one case. One case either side of every **reachable** threshold constant (`>` vs `>=`), with unreachable ones documented instead. The null level, a null level that is not first, a level set with no `ELSE`, and one with no null level (M14). A comparison level that no fixture row reaches (M9) |
| 5 | `int_scored_pairs` | `m=0`, `u=0`, level-not-observed, missing TF entry, and both ends of §3.1's clamp. Fixture m/u chosen so every Bayes factor is an exact power of two, per D12's exactness constraint. `bf_tf_adj_*` present only for the levels §3.2 says receive an adjustment |
| 6 | `edges_by_threshold` | The threshold predicate is `>=` (§3.1 note / D2): one pair exactly *at* the threshold, one just below. With the threshold as a dimension (M16), also: the same pair present at one threshold and absent at a higher one, and cross-threshold monotonicity |
| 6 | `entity_clusters` | D4: singletons present; a chain, a star and a cycle each collapse to one min label; the ghost-node / NULL-endpoint case asserts we emit **nothing** where Splink emits a spurious NULL-id row; the monotone guard holds under a re-run |
| 6 | metrics models (§3.6) | Each formula on a hand-built graph whose answer is arithmetic by hand — including the degenerate single-node and single-edge components |
| 7 | `golden_records`, `cluster_membership` | D10: a tie broken by the trailing `unique_id`; a multi-column attribute surviving **as a unit** from one winning record; an all-NULL attribute; the `__source_record` / `__rule_applied` lineage columns |
| 8 | full-rebuild path | v1 ships no incremental materialization, so there is no `is_incremental()` case to write: the 80/20 split rebuild is a data test, not a unit test. What *is* a unit test here is the row-stamp invariant — `er_model_sha` and `er_tf_snapshot_id` single-valued across the output (M8). The `overrides: {macros: {is_incremental: …}}` pair comes due with the v2 incremental path |
| 9 | training models | D9 seeded u-estimation on a fixed sample; m-from-labels as one GROUP BY over hand-built labels; D5's EM for a **single** iteration against a hand-computed step, which is the part a unit test can pin without the oracle problem B5 describes |
| 10 | `eval_*`, `diag_*` | A hand-built confusion matrix with one of each cell, including the empty-cell case; histogram bucket boundaries |

One model is deliberately absent: `entity_clusters_1to1` is dead code under Stage 12.1's supported matrix
(RC9), and it gets unit tests if and when it is un-deferred. Two rows carry a caveat — the Stage 4 and
Stage 5 models need **generated** fixtures rather than hand-written ones, because their column set comes
from the model JSON (M2). That generator is Stage 1 work (RC57), and D12 names it as the one piece of
machinery this rule costs.

### The critical path, and what runs beside it

Stated here rather than left to be inferred, because it is the largest schedule lever in the document.

**The critical path is `1 → 3 → 4 → 5`.** Stage 1 leads because `load_model_json` owns five values the
model JSON does not contain and that must be *recomputed* rather than read (D1). Every downstream baseline
is meaningless until that reader is right.

**Stages 6, 7, 10's measurement models and 12b build in parallel from day one**, from injected baselines,
via the per-model injection mapping in *Stage decoupling* below. They are not gated on the critical path.

Two things run **before** the work they support, and both orderings are counter-intuitive enough to be
worth stating twice:

- **0.7, the comparator sensitivity suite, is built before 0.4 freezes the baselines it guards.**
  `DbtBestPractices.md` §12.7 is explicit that this is the one standard that must precede the thing it
  checks. A comparator mutation-tested afterwards leaves earlier green results nobody can trust
  retrospectively.
- **Stage 10's *measurement* models are built immediately after Stage 2**, not at Stage 10, so their
  outputs can gate Stage 3's recall floor and Stage 6's quality tests. A quality stage that runs after the
  stages it should gate cannot gate them (M12).

### Stage 0 — Scaffolding, fixtures, oracle, and the clustering spike

- **0.0 Pre-flight.** Sequence the competing "do this first" directives and close what blocks planning.
  Five of them coexisted with nothing ordering them (RC8): this task list; §B.3's *"rows marked CONFLICT
  or MISSING are the ones to close before writing models"*; `DbtBestPractices.md` §12.7's *"built before
  the thing it guards"*; its Appendix E's *"the first [G3] is the one to take next"*; and rebuilding the
  deleted engineering scaffold, which appeared in no list at all. **The order:**
  1. Close **DR-11 / R3** — one stage list, so everything else has something to sequence off.
  2. Decide **DR-17 / G3** (the model-JSON trust boundary — the one that is architecture), then
     disposition the remaining `MISSING` rows. Per RC30 only DR-16 and DR-17 are missing in full.
  3. Close **DR-16 / G2+G9** — the input contract. It gates every model reading `stg_input`.
  4. ~~Settle **`DbtBestPractices.md` B.1 / DR-13**~~ — **done 2026-08-23**, before 0.9 could touch
     `profiles.yml`, which is what RC45 asked for. The harness reads only parquet and never opens the
     database; dbt keeps a file database.
  5. Rebuild the scaffold — **0.9**, below.
  6. Build the comparator sensitivity suite — **0.7**, below.
  7. Then 0.1–0.6 and 0.8.

  **B.8** is decided out of band: its recommendation is *"(a), after testing (c)"*, so **0.8** runs first
  and its result sets the value (RC46). **DR-09** and **DR-08 / B.2** close before Stage 6 is broken down,
  because both change that stage's *contract* rather than its SQL.
- 0.1 dbt-duckdb project; pin `splink`, `duckdb`, `dbt-core`, `dbt-duckdb` **and `sqlglot`** exactly;
  `make` targets. sqlglot is parity-critical — it, not Splink, decides which levels receive a TF
  adjustment (A.2 C2) — and it arrives transitively, so it is invisible to a pin list that names only the
  four. See **G11**. *Pin it explicitly and by exact version: `sqlglot`'s resolved upper bound comes from
  `dbt-bouncer`, not from Splink (`splink 4.0.16` asks only for `>=17.6.0`; `dbt-bouncer 3.8.0` requires
  `>=25,<31`), so a routine lint-tool bump can move a parity-critical dependency. `dbt-bouncer` therefore
  joins the four exact pins on Dependabot's ignore list (`DbtBestPractices.md` §16).*
- 0.2 Vendor `fake_1000`; seeded synthetic generator. Include the **degenerate-corpus fixture set** G9
  asks for: empty corpus, single row, all-identical records, an all-NULL blocking column, a NULL
  `unique_id`, and two records sharing a `unique_id`.
  **Partially done 2026-08-23 (PC-1).** `fake_1000` is vendored at `fixtures/source/` with a sha-verified
  manifest — *not* read through `splink_datasets`, which downloads it from a mutable `master` ref at
  attribute-access time (Appendix D.0 finding 37). All six degenerate corpora exist under
  `fixtures/degenerate/`, each with a manifest stating what it probes. **The seeded synthetic generator is
  still outstanding** and is what `er_max_pairs` (0.6) and the scale work will need.
- 0.3 `gen_baseline.py` dumping every intermediate as parquet with a manifest.
  **Normative: baselines are generated from a model JSON that has been saved and reloaded** (§3.4).
  The baseline format must carry, **from day one**, everything a later stage will need — retrofitting it
  after 0.4 freezes it is the expensive path:
  - `retain_matching_columns=True` **and** `retain_intermediate_calculation_columns=True`. Neither is
    Splink's default. Without the first, Stage 4's baseline may contain no gamma columns at all, which
    makes gamma equality the sole gate over a self-consistent wrong numbering (M14); without the second,
    Stage 5 has no per-comparison `bf_*` to localise a divergence with.
  - **Ground-truth labels** on every fixture, so Stage 10's measurement models have something to measure
    (M12).
  - **Per-iteration training traces**, so Stage 9 has the oracle B5 shows it cannot get from a procedure.
  - A provenance manifest per baseline: Splink version, model-JSON sha, seed, DuckDB version, **sqlglot
    version** (RC54), producing commit, and the **platform triple** G5 needs and RC21 records as still
    missing.

  **The harness reads only parquet** (B.1 / DR-13, CURRENT). DuckDB's process-level lock means the harness
  and dbt cannot both hold the database, so the harness never opens it: `integration_tests/` exports every
  compared model to parquet with a `COPY` post-hook, and this script's baselines are parquet already. Both
  sides of every comparator are therefore the same format, which is what makes a comparator's join key the
  only thing that can be wrong.
- 0.4 Freeze `model_jsons/fake_1000_v1.json` + baselines. **Must follow 0.7.** Also: fix the frozen model
  rather than freezing a bad one — it measures F1 = 0.72 and blocking recall = 0.51, and two extra
  blocking rules take it to F1 = 0.98 (M12, §A.6 Q5).
- 0.5 **Clustering spike — now resolved, retained as a regression gate.** The D4 formulation must
  reproduce a union-find partition on random, chain, and star graphs with recorded runtimes. This gate
  re-runs on every DuckDB bump.
- **0.6 Materialisation & capacity spike.** Measure bytes-per-pair for the fixture model and publish
  `er_max_pairs` from the measurement. ~~Decide `ephemeral` vs `table` per intermediate~~
  **[SUPERSEDED by D11]** — but the *measurement* stands and D11's follow-through requires it, because
  `er_max_pairs = 42,000,000` was derived from the wide 946 B/pair shape and under-provisions the narrow
  one by roughly 10×. The check needs a home **in the DAG**, firing before Stage 3 materialises: a
  `make` target reports, it does not stop a build (G14).
- **0.7 Comparator sensitivity suite.** `DbtBestPractices.md` §12.7's mutant catalogue, applied to a
  known-good output at every parity stage, with **no mutant permitted to survive** and each asserting the
  **expected localisation string** rather than merely failing. Sized at one day and *"the cheapest
  credibility available"* (M10). Nothing else in the programme proves the parity comparator can fail.
- **0.8 `EXPLAIN ANALYZE` spike for B.8 option (c).** Does a DuckDB lateral column alias evaluate once, or
  expand textually? D11 rec 4 requires single evaluation to be *structural*. Without this, B.8's option
  (a) is adopted untested by default (RC46). Timebox and kill criterion written in advance.
- **0.9 Rebuild the engineering scaffold** from `DbtBestPractices.md` Appendix C with its v2 delta tables
  applied. Appendix D records that the verified original was deleted, so Appendix C is the only copy —
  and only six of its seven blocks have text (C.5 `dbt-bouncer.yml` has none, RC50), while `pyproject.toml`,
  `uv.lock`, the `Makefile` bodies, `.yamllint.yml`, `.gitignore`, the workflows beyond `ci.yml`, and eight
  of ten enforcement scripts have no content anywhere in either document. This is the largest single item
  in Stage 0 and it appeared in no task list until now. `DbtBestPractices.md` Appendix D's bootstrap-order
  note (RC53) owns its sequencing.
- **Frozen model library matrix.** The 11-cell matrix M13 specifies — every entry saved, reloaded, hashed
  and committed — rather than the one sentence the plan carried.

**AC:** `make baseline` is hash-stable across two runs; the D4 gate is green with published timings; no
mutant in §12.7's catalogue survives the comparator suite; `er_max_pairs` is derived from a measurement
rather than carried forward as a literal; and the rebuilt scaffold re-earns or demotes every `[VERIFIED]`
marker it claims (`DbtBestPractices.md` 3.44).

### Stage 1 — Model JSON ingestion & SQL generation · **critical path**

`load_model_json` (D1) including the five recomputed fields; `blocking_sql` (D2); `comparison_vector_sql`
(§3.3); `bayes_factor_sql` + `tf_adjustment_sql` (§3.1–3.2).

**The compile-time sidecar** (§A.2) is part of this stage, not a later discovery. It is a generated,
committed, hashed artefact that resolves what Jinja provably cannot: the `comparison_vector_value` per
level, the resolved `m`/`u` after Splink's own defaulting, `tf_u_exact_match`, and the three configuration
facts that are runtime observations rather than JSON contents — `er_backend_link_type`,
`er_has_source_dataset`, `er_left_table`. TF exact-match-level resolution is a sqlglot CNF analysis that
string-matching gets wrong in both directions (A.2 C2), so it is resolved once, at compile time, and never
approximated.

**Lints that belong here**, because each catches a Stage-1 fact at Stage 1 rather than at the stage that
trips over it: asymmetric comparison levels (M1), `output_column_name` uniqueness *after* `.replace(" ",
"_")` normalisation (M2), the `set()` ban (M15), and `m == 0` / `u == 0` as a **hard error** while absent
m/u stays valid input (M13). `er_gamma_columns` and `er_bf_columns` are published as vars from the same
pass (M2, and `DbtBestPractices.md` §9).

**AC:** rendered SQL for the fixture model matches reviewed snapshots; malformed JSON fails compilation
with actionable errors; **a level with `m_probability` absent renders `_default_m_values`, not NULL**;
`dbt compile` output contains zero Jinja residue and reproduces `cast(… as float8)` wrappers; the sidecar
regenerates **byte-identically** from the same model JSON; and *the wrapper emits `er_gamma_columns`,
`er_bf_columns` and a `format: sql` fixture per JSON-derived model from one pass over the model JSON, with
a drift-guard test that fails if any of the three disagrees with the rendered SQL* (RC57).

That last clause is D12's one piece of new machinery. `int_comparison_vectors` and `int_scored_pairs`
cannot have hand-written fixtures — their column set is data (M2) — so the wrapper that emits the column
lists must emit the fixtures too, and the drift guard covers three artefacts against one model JSON rather
than two. Left to Stage 4 it becomes exactly the failure M2 describes, *"a Stage-1 decision being made in
Stage 4"*, only now it blocks a gate (`DbtBestPractices.md` 3.20) rather than a contract.

**The sidecar enforces §1.5's trust boundary** (DR-17, CURRENT). Its five rules — the closed allow-list
against the parsed tree, the non-determinism rejection, the structural rejection, the input bounds, and
`er_model_sha` as the hash of the *validated* artifact — are Stage 1 acceptance criteria, with the five
negative tests §1.5 names. A build whose JSON has not passed the sidecar has no sha and does not run.

**§2.0's compile-time column check is Stage 1 work**, because it is the same pass over the model JSON that
emits `er_gamma_columns`: every column the JSON references must appear in `er_input_columns`, and a missing
one fails compilation naming the column rather than producing a `Binder Error` from inside a generated
`CASE` (DR-16).

**Blocked by `DbtBestPractices.md` B.8**, because the snapshot AC above reviews rendered scoring SQL
containing D11 rec 4's subquery, which §11.1's `forbid_subquery_in = both` forbids (RC46). That is now this
stage's only open blocker.

### Stage 2 — Staging & term frequency

`stg_input` (D8, pure passthrough); `tf_all` (D7 shape, **D7a semantics — frozen by snapshot**).

D7a is the decision that shapes this stage: `tf_all` is keyed
`(er_model_sha, er_tf_snapshot_id, column_name, value, tf)` and is *read* from a frozen snapshot on every
parity and production run. Computing it from the live corpus is the opt-in `er_tf_mode='refresh'` path
used only when minting a snapshot. Building `tf_all` as a plain live aggregate over `stg_input` and adding
freezing later is the expensive order — it changes the model's grain, its contract, and every downstream
join key.

**AC:**
- Exact TF parity per column against Splink, including the **non-null denominator** (§3.5) — assert
  `sum(tf) = 1.0` per column, which catches the denominator bug directly.
- `stg_input` equals Splink's concat **excluding `__splink_salt`** (S1).
- A value present in the corpus but absent from the frozen snapshot **raises**, and a test proves it
  raises. Do not `COALESCE` (D7a).
- Re-scoring a pair under an unchanged `(er_model_sha, er_tf_snapshot_id)` yields a bit-identical
  `match_weight` after unrelated records are appended to the corpus. This is the test that makes frozen
  TF meaningful rather than decorative, and it is cheap to write now and awkward to retrofit.

**§2.0 is this stage's contract** (DR-16, CURRENT). `stg_input` reads the relation named by
`er_input_relation`, performs no transformation, and carries the three preconditions as tests that ship
with the package: `unique_id` unique and not null, corpus non-empty. The uniqueness test is the one that
matters most — D3's `l.<uid> < r.<uid>` means two records sharing an id never pair with each other, and
without the test that is silent.

### Stage 2b — Record lifecycle

**Either build it, or declare it an explicit non-goal. Leaving it ambiguous is what is not acceptable.**

If `is_incremental()` ships in Stage 8, this stage must exist: `is_deleted` / `valid_to` on `stg_input`, an
`edges ⊆ nodes` referential-integrity test, and an explicit reap step. dbt's `delete+insert` cannot remove
a key the `SELECT` excludes — the incumbent hit this exact trap and needed a post-hook.

The cheap correct v1 choice is to declare every model `table` (full rebuild) and put `is_incremental()` out
of scope, which makes deletion a non-issue by construction. **D11 has since decided all-`table`**, which is
that choice in all but name — yet Stage 8 still ships the incremental path. That triangle is resolved in
Stage 8's own section, not here, because resolving it in two places is how it stayed open (RC10, RC15).

### Stage 3 — Blocking · **critical path** · highest-risk parity stage

**AC:**
- Exact `(unique_id_l, unique_id_r, match_key)` set equality, with `match_key` compared as VARCHAR.
- Per-rule pair counts.
- Adversarial fixtures for overlapping rules, NULL-heavy keys, and **empty-string keys** (D2).
- **Blocking recall against ground truth, with `er_blocking_recall_floor` as a two-sided guardrail**
  (M12). Recall lost here is unrecoverable downstream and was otherwise ungated: the frozen fixture model
  finds 1,651 of 2,975 true pairs. A floor that is too *high* is also a failure — it means the fixture, not
  the code, changed.
- The `max_rows_limit = 1e9` ported from Splink is replaced by the **byte-derived budget** from 0.6.
  Splink's limit is per-rule; at the measured wide shape it would admit a 946 GB build (B1).

**Restricted to the supported-configuration matrix** (Stage 12.1), which makes every hard case in D3 and S2
dead code for the actual migration target and materially de-risks this stage.

**Reusable oracle — do not rebuild it.** The incumbent's `tests/helpers/pairs.py::splink_blocked_pairs`
is a working Splink blocking oracle via `deterministic_link()` (A.3 Group 3).

### Stage 4 — Comparison vectors · **critical path**

**AC:** 100% gamma equality; a boundary fixture either side of every **reachable** threshold constant in
the model JSON (catches `>` vs `>=`), with the unreachable constants **documented** rather than fixtured;
a fixture where the JSON list order and gamma order disagree (catches §3.3); and fixtures for
null-level-not-first, no-`ELSE`-level, and no-null-level (M14).

**Supersedes:** this AC previously read *"a boundary fixture for **every distinct** threshold constant"*.
That was the one *direct* textual conflict between §5 and A.5 rather than an omission (RC29), and it is
resolved in A.5's favour: a constant no fixture row can reach cannot have a boundary fixture, so demanding
one makes the AC unsatisfiable rather than strict. Documenting the unreachable ones keeps the information
the strict form was reaching for.

### Stage 5 — Scoring · **critical path**

**AC:** parity per **A.4** (not §6.1 — see §6.1's own note); per-comparison `bf_<name>` and
`bf_tf_adj_<name>` emitted for localisation (needs `retain_intermediate_calculation_columns=True` in the
baseline, which is **not** Splink's default); fixtures covering `m=0`, `u=0`, not-observed levels, missing
TF entries, and the clamp region; and the clamped Bayes-factor product computed **once**, as a structural
single-evaluation `_bf_clamped` column rather than a repeated expression (D11 rec 4, B1 rec 2).

**Blocked by `DbtBestPractices.md` B.8.** Its §11.1 concedes that until B.8 closes, `er_int_scored_pairs`
cannot be written to satisfy §7.3's CTE ban, `forbid_subquery_in = both`, and float parity's rejection of a
repeated expression all at once. One of the three must give.

### Stage 6 — Clustering · parallelisable from day one

`edges_by_threshold` (threshold, **`>=`** — §3.1 note / D2); `entity_clusters` (D4); **`review_pairs`**
(§1.7's gray band); `node_metrics` / `cluster_metrics` / `edge_metrics` (§3.6).

**On the two model names.** `int_edges` becomes `edges_by_threshold` and `entity_clusters` takes the
composite key `(thr, unique_id, component_label)` — the label column is `component_label`, not `entity_id`
(§1.6, DR-12) — because the acceptance criteria below require three thresholds
**simultaneously** and `var('er_threshold')` builds one partition per run. Cross-threshold monotonicity is
not expressible as a dbt test under the var approach at all (M16). This changes these models' *contract*,
not merely their SQL — which is why **DR-08 / `DbtBestPractices.md` B.2 closes before this stage is broken
down**, and why it cannot be deferred as an implementation detail.

**`entity_clusters_1to1` is deferred to v2** (closes RC9). `cluster_using_single_best_links` is defined
*over* source datasets — per-cluster `contains_<sd>` flags, `not ((l.contains_A and r.contains_A) or …)` —
and Stage 12.1's supported-configuration matrix forbids `source_dataset`, so there is nothing for the
one-per-dataset constraint to range over. It is dead code for the actual migration target. It is tagged
alongside the other deferred link-shape work (D3's composite ordering, S2, M1, A.2 C4) rather than left
unmarked in the v1 plan; **G18's dead-code catalogue gains this row**, which it previously omitted.

**AC:**
- **Label** parity (not merely partition parity) at thresholds {0.5, 0.9, 0.99} — D4 shows the labels are
  identical, so the weaker gate would hide real drift.
- Singletons present; ghost-node and NULL-endpoint fixtures assert we emit **nothing** where Splink emits
  a spurious NULL-id row, and the comparator treats that as expected.
- **Thread-determinism gate:** the same graph at `threads=1` and `threads=8`, ten runs each, yields one
  content hash. This is the only test that catches a missing `GROUP BY` (D4 trap 1).
- An iteration guardrail bounds a deep component. **Not** a pre-flight diameter estimate — computing
  diameter is as expensive as the clustering itself, and every cheap proxy is anti-correlated with cost
  by ~200× (Appendix A, M5). Use an in-query iteration cap plus a post-hoc alert instead.
- Runtime recorded **against Splink's own time on the same graph**. Per D4a we expect to be slower; the
  criterion is that the ratio is known and does not regress, not that we win. **An absolute budget runs
  alongside the ratio** — `≤ var('er_cluster_budget_s')` — because a ratio-only criterion cannot fire on
  the deep-component case at all (M11).
- **Per-model acceptance criteria for the metrics models**, with a **Python union-find oracle** for
  `is_bridge`: a bridge is an edge whose removal increases the component count. There is no SQL oracle —
  Splink computes it in igraph, in Python, and degrades silently to nothing when igraph is missing (S3),
  so our version is a *replacement* and needs an oracle of its own (M11).
- **Max-cluster-size gates** (M12). Cluster precision amplifies edge error: 0.9764 at the edge level
  became 0.7495 at the cluster level on the fixture, a 14.8× amplification of false positives.

**DR-09 and DR-08 are closed** (§1.7), and both land in this stage's models rather than blocking it.
`edges_by_threshold` takes pairs with `match_probability >= thr_auto_merge`; the half-open band
`[thr_review_low, thr_auto_merge)` goes to **`review_pairs`** and never enters the graph, so no acceptance
criterion above changes. `thresholds` is cast **DOUBLE** — a bare decimal literal types as `DECIMAL` in
DuckDB and shifts the boundary comparison, which is the one thing a threshold must not do.

### Stage 6b — Entity identity · **not built; an interface contract** (DR-12)

**Decided 2026-08-23: the column is renamed, not the machinery built.** `entity_id` becomes
**`component_label`** and is removed as a key from every downstream model. §1.6 carries the decision and
the measurement behind it; §A.6 Q1 had already declared this consequence of DR-14 *"binding, not
conditional"*, and DR-12's own trigger fired on 2026-08-20 without the row closing (RC16).

**The engine builds none of `entity_keys`, `cluster_lineage` or `entity_events`.** Entity permanence and
the incumbent's `INV-PERM` belong to the platform, per §1.3's non-goal and DR-14's posture. Building them
here would make `dbt-er` an MDM system by accretion, which is the one thing §1.3 says it is not.

**What this stage *is*, therefore, is the interface**, and it is short: the platform's permanence layer is
computed from the **edge set at each threshold** and the **partition**, and the engine already publishes
both — `edges_by_threshold` and `entity_clusters`. Stage 6b's deliverable is that this is stated in
`PARITY.md`'s scope section and in the public API surface (§19.1), so a consumer reads it before building
on the label rather than after.

No models. No acceptance criteria beyond §1.6's contract table being reflected in `entity_clusters`'s
column descriptions, which §10.2 requires to say what a column means and whether it is stable.

### Stage 7 — Survivorship & golden records · parallelisable from day one

Per D10, including the multi-column-attribute rule. No Splink oracle; hand-built fixtures, property tests,
and a row-order permutation test.

**Extended per M19**, whose complaint is that survivorship as specified is single-strategy-per-attribute,
drops multi-valued attributes, and has nowhere to put a conflict it cannot resolve: ordered **rule chains**,
**field groups**, **multi-valued output**, an explicit **unmergeable-conflict path** and the relation it
writes to, **config validators**, and a **per-field-group property test**.

`golden_records` and `cluster_membership` also need a declared grain, which they do not yet have (G10).

### Stage 8 — Incremental

Pure SQL removes Splink's two-pass *awkwardness*: `find_matches_to_new_records` does **not** pair new
records with each other, so Splink needs a second `predict()` pass over the batch. One blocking query
with `where a.is_new or b.is_new` covers both cases.

**It does not, however, make the run cheaper — and an earlier draft of this section wrongly implied it
did.** `where a.is_new or b.is_new` still evaluates every blocking rule over the *whole* corpus; measured,
it costs ≥ 100% of a full rebuild (Appendix A, B4). Incremental cost has to come from restricting the
*blocked* side, not from a predicate applied after blocking. See B4 for the corrected design.

**In v1 this stage is a full-rebuild flow. `is_incremental()` moves to v2, together with Stage 2b.**

**Supersedes:** M8's incremental `unique_key` guidance *for v1*; A.5's Stage 8 row insofar as it schedules
incremental delivery in v1. Both stand as the v2 design. Closes RC10 and settles Stage 2b's either/or.

Three facts decide it, and they all point the same way:

1. **D11 decided `table` everywhere** and dropped `ephemeral` from `er_allowed_materializations` without
   carving out `incremental`. Shipping `is_incremental()` under that contract requires a materialization
   the contract does not admit.
2. **The specified incremental design is not incremental.** `where a.is_new or b.is_new` still evaluates
   every blocking rule over the whole corpus: measured at **139% of a full rebuild** on 1M/10k, and 5.4×
   the batch-driven form for identical output (B4). Carving out a materialization to run something slower
   than the thing it replaces is not a trade worth making.
3. **A.5's Stage 2b names this exact resolution** *"the cheap correct v1 choice"*: declare every model
   `table`, put `is_incremental()` out of scope, and deletion becomes a non-issue by construction. What it
   called unacceptable was shipping `is_incremental()` with **neither** the lifecycle machinery nor the
   explicit non-goal. This is the explicit non-goal.

The cost, stated rather than discovered: every run re-scores the whole corpus. That is less of a change
than it sounds, because D7a already makes a TF-snapshot refresh a full rescore (G7), and DR-14 puts run
scheduling on the platform side of the boundary.

**v1 AC:** a full rebuild over the 80/20 fixture split produces the same edges and the same partition as a
single build over the whole corpus; `er_model_sha` and `er_tf_snapshot_id` appear on every row with
`count(distinct …) = 1` tests, so a run cannot silently mix scores from two model JSONs or two TF
snapshots (M8); the frozen-TF approximation is documented and bounded.

**v2 design, recorded so B4's measurement is not lost:** two explicitly-driven joins —
`(batch ⋈ corpus) UNION ALL (batch ⋈ batch)` — never the disjunctive predicate, with a **measured**
acceptance criterion that the incremental blocking model's wall time is **< 10% of the full model's** on
the 1M fixture. Optional `er_assertions` / `er_cut_edges` inputs upstream of the edges model (M20), and
Stage 2b's `is_deleted` / `valid_to`, `edges ⊆ nodes` test and reap step come due at the same time.

### Stage 9 — Training *(promoted from stretch goal)*

9.1 `train_prior`; 9.2 `train_u` (D9, seeded and reproducible); 9.3 `train_m_from_labels` (one GROUP BY);
9.4 `train_em` (D5) including per-session column removal, blocking-adjusted λ, and **median** combination.

**The EM oracle is a committed artefact, not a procedure.** D5's `train_em` is a **spike** with a timebox
and a written kill criterion, not a delivery task.

**AC:** u-estimation **exact** given the same seed; m-from-labels exact; and for EM, the **per-iteration
trajectory** matches a committed training trace, with `seed` **required** and cap-versus-converge asserted
explicitly.

**Supersedes:** the previous AC, *"EM within 1e-4 of Splink's on the same blocking pass with the same
iteration count"*, which B5 proves unfalsifiable on three independent counts (RC11): the training oracle is
not a function of (data, seed) — measured max |Δ match-weight| = **1.63** across 16,553 pairs under
Splink's default `seed=None`; the iteration count is **unobservable**, because *"EM converged after 25
iterations"* prints unconditionally outside the break; and 1e-4 **is** Splink's own `em_convergence`, so a
sub-tolerance parameter difference moves the early stop by one iteration and produces a supra-tolerance
difference in every parameter. The trace must be captured back in **Stage 0.3** — retrofitting it after 0.4
freezes the baseline format is the expensive path.

### Stage 10 — Evaluation & diagnostics *(new)* · split, and it gates

`eval_accuracy`, `eval_errors`, `eval_unlinkables`, `diag_comparison_vector_distribution`,
`diag_match_weights_histogram`.

**The measurement models build immediately after Stage 2**, not here. They need only labels and scores, and
building them early is what lets their outputs gate **Stage 3's blocking-recall floor** and **Stage 6's
quality tests**. A quality stage that runs after the stages it should gate cannot gate them (M12). Only the
*parity* acceptance criterion stays at this position in the sequence.

**AC:** confusion-matrix parity against `accuracy_analysis_from_labels_table` on a labelled fixture. This
is the first stage that measures whether the *output is good*, not merely whether it matches Splink.

**The quality floor is a committed number, not a report.** The frozen fixture model measures F1 = 0.7138
and blocking recall = 0.5550 at t = 0.9; adding `block_on(dob)` lifts recall to 0.8061 and `block_on(email)`
to 0.9173, ending at F1 = 0.9809. Parity gates cannot see the difference between 0.72 and 0.98. Per-fixture
F1 and recall floors are therefore committed and enforced, not merely measured — otherwise this stage
reports and the product ships at 0.72 (§A.6 Q5, M12).

### Stage 11 — The differential loop

Nightly randomized seeds → both engines → compare every stage → scoreboard. Failures freeze into
`fixtures/regressions/`.

**Change from v1:** vary **data** against a *frozen library* of model JSONs. v1 trained a new Splink model
per seed, which makes a failure un-attributable between the model, the data, and our SQL. Model-varying
runs are a separate, explicitly-labelled job.

**Extended per M4 and M18:**
- The **both-modes CI rule** — every model is exercised on `ref()` *and* on an injected baseline, because
  injected mode alone is not a release gate.
- The **per-model injection mapping** and the `sha256(model JSON)` binding that ties a baseline to the
  model it was generated from. See *Stage decoupling*.
- A **failure-bundle schema**, and a CI job that **reproduces the verdict from the bundle alone**. A
  nightly failure that cannot be reproduced from its artefact is a finding nobody can act on.
- §8's DoD item 3 splits into **parallel correctness** (many seeded runs at once) and **concurrent
  stability** (consecutive calendar days). Ten serial green nights is an uncompressible ≥10-day tail —
  ≈13.4 nights at p = 0.95 — and only the second half of the split actually needs the calendar.

### Stage 12 — Cutover *(new; resolves Appendix A, B3)*

`grep -ic` over v2 returned **0** for `cutover`, `shadow`, `rollback` and `migration`. Since §1.3 positions
dbt-er as a drop-in matching engine, delivery is a **swap** — the highest-risk operation in the programme
— and every prior stage proves correctness against *fixtures*, not against the production corpus.

- 12.1 **Supported-configuration matrix.** State what v1 supports and **fail compilation on anything
  else**: `dedupe_only`, VARCHAR `unique_id`, no `source_dataset`, plain equi-join blocking rules, no
  `arrays_to_explode`. This is not a narrowing for its own sake — it matches the incumbent exactly
  (`LINK_TYPE = "dedupe_only"`, `UNIQUE_ID_COLUMN = "record_key"`, no source_dataset), which means every
  hard case in D3 (`-__-` composite, lexicographic ordering) and S2 (`two_dataset_link_only`) is **dead
  code for the actual migration target** and can be deferred to v2. It materially de-risks Stage 3, the
  highest-risk parity stage.
  *Note `salting_partitions` can be **ignored** rather than errored: the pair set and `match_key`s are
  exactly invariant under salting (verified — 3,281 pairs identical, salted vs unsalted).*
- 12.2 **Shadow run.** Both engines on production data, diffing at the platform boundary: first the edge
  set at the auto-merge threshold, then the cluster partition.
- 12.3 **Numeric go/no-go**, not a judgement call: symmetric difference of the edge set = 0; partition
  delta ≤ N entities, with N stated in advance.
- 12.4 **Rollback switch and its trigger criteria**, documented and exercised at least once.

**AC:** a shadow run completes on production-scale data; the go/no-go metrics are published; the rollback
path has been executed in anger at least once in a non-production environment.

### Stage 12b — Provenance & observability · parallelisable from day one

`er_run_id` stamped on every materialised model and listed in `er_volatile_columns` so it is excluded from
every content hash; `_er_run_manifest` written via `on-run-end`; a per-run performance artefact; and named
owners for `docs/divergence-log.md` and `PARITY.md`, with a CI check that every deliberate divergence has
**both** a log entry and a pinning test, checked in both directions.

This stage exists because three of the document's own requirements — §6.2, §6.4's performance criteria, §7
Q1, and DoD items 4–5 — otherwise belong to no stage at all (M7).

### Stage decoupling (replaces v1's strict serial gating)

v1's principle 2 — "no stage starts until the prior stage's parity gate is green" — serialises the whole
project and makes Stage 6/7 hostage to Stage 3/4/5. Instead, **every model can source its input from either
its upstream model or an injected Splink baseline**. This gives per-stage isolation *and* end-to-end parity,
localises failures, and is what lets **Stages 6, 7, 10's measurement models and 12b be built from day one**
rather than waiting on the critical path. That parallelism is the largest schedule lever in the document.

**Supersedes:** the single-global-boolean form this section carried in v2 —
`var('er_inject_baseline', false)` selecting `source('baseline', …)` against `ref(…)` — which M4 shows is
defective in four verified ways (closes RC12). The corrected mechanism has four parts:

1. **Injection is per model, not global.** A single boolean flips *every* model at once, which A.5 calls
   *"the one configuration that tests nothing"*: it never exercises the seam between a real upstream and an
   injected one. The selector is a **mapping** — model name to source — so exactly one boundary is injected
   at a time.
2. **The baseline source is owned by the harness, not the package.** `source('baseline', …)` declared
   inside `dbt_er` forces its database and schema onto every consumer and trips
   `source-override-deprecation` on dbt-core 1.12.2. Package models never declare it.
3. **Injected mode is not a release gate.** CI runs **both modes**, and green in injected mode alone does
   not ship. This is stated here because it is the assumption that quietly stops being true.
4. **A baseline is bound to the model JSON that produced it** by `sha256`. An injected baseline from a
   different model JSON is a silent wrong answer, not a failure — the numbers are all plausible.

**Every ticket states which mode it builds in.** A ticket that depends on injection also depends on this
mechanism existing, which is Stage 11 work.

---

## 6. Verification

### 6.1 Tolerance policy — corrected

**[v1 ERROR]** v1's D7 set `|Δ match_weight| ≤ 1e-6` **and** `|Δ probability| ≤ 1e-8`. These are mutually
inconsistent. With `p = 2^mw/(1+2^mw)`, `dp/dmw = ln2 · p(1-p)`, maximised at `p = 0.5` where it is
`≈ 0.1733`. A 1e-6 weight difference therefore produces a `1.73e-7` probability difference — **17× the
stated probability tolerance**. The probability gate is strictly tighter than the weight gate near 0.5 and
vastly looser at the tails.

**Corrected policy.** One tolerance, stated in match-weight (log-odds) space; the probability bound is
*derived*, not asserted:

| artefact | gate |
|---|---|
| pair sets, `match_key`, gammas, TF tables | **exact** after canonical ordering |
| `match_weight` | **exact bit equality** expected; `1e-9` absolute permitted with a divergence-log entry |
| `match_probability` | derived: `≤ ln2 · p(1-p) · Δmw`, not an independent constant |
| **`int_edges` (edge-set membership)** | **exact set equality — no tolerance** (see below) |
| clusters | partition equality (canonical relabel **and** pairwise co-membership) |
| golden records | exact |

> **[REVIEW 2026-08-23] RC13 — The clusters row contradicts A.4 and this document's own Stage 6 AC.** A.4's
> corrected table reads "**label equality** on the component label, primary; partition equality … as
> fallback diagnostic. D4 proves labels are identical, so the weaker gate hides real drift (M6)", and
> Stage 6's AC reads "**Label** parity (not merely partition parity)" — yet this row says partition
> equality only. The subset note below enumerates exactly three items A.4 adds, and R2 repeats the same
> three; this row is a fourth difference, and unlike the others it is not a subset relation but a
> disagreement about which gate is primary. A harness built from this table implements the gate the rest
> of the document calls the one that hides real drift. Correct the row (and add it to R2's merge list) so
> the eventual single table does not silently resolve toward the weaker gate.

Exact bit equality is the right default because **both engines run float8 on the same DuckDB**. Where the
expression tree is identical, the result is identical. Tolerance is for where it provably cannot be.

**That justification has a validity domain, and it must be stated: `(platform, architecture, DuckDB
build)`.** It was measured in one process on one machine; a committed baseline parquet compared on a CI
runner is a different claim. See **G5**, which is UNVERIFIED and cheap to settle. Note also that this
table is a strict subset of A.4's — the relative term, the `mw > 54` vacuity rule and the
float-aggregate row are only there. **R2** says to merge them; until that happens, **A.4 is the one to
implement from.**

**Why `int_edges` needs its own exact gate** *(resolves Appendix A, B2)*. A tolerant scoring gate and an
exact cluster gate cannot both hold across a hard threshold. A pair whose weight differs by 1e-9 and sits
within 1e-9 of the threshold flips membership, and connected components turns one flipped edge into a
whole-component merge — so **the permitted scoring drift is unbounded in cluster space**. The edge set is
the interface between the tolerant stage and the exact stage, so that is where the tolerance must be
reconciled. The gate is boolean agreement, not value agreement:

> for every tested threshold `t` and every pair: `(p_dbt >= t) == (p_splink >= t)`.

**The threshold predicate, stated normatively.** Splink applies the threshold in **two different spaces**
depending on which API produced the baseline, and they disagree at zero arithmetic drift:

- `predict.py:104-111` — `where log2(<bf_expr>) >= <threshold_as_mw>` (weight space, expression recomputed)
- `linker_components/clustering.py:105-118` — `where match_probability >= <threshold>` (probability space,
  materialised column)

Measured at `t = 0.9`, where `threshold_as_mw = log2(0.9/0.1) = 3.1699250014423126` ≠ `log2(9.0) = 3.169925001442312`:

| `bf` | `log2(bf) >= 3.1699250014423126` | `bf/(1+bf) >= 0.9` |
|---|---|---|
| 8.999999999999998 | false | **true** |
| **9.0 exactly** | false | **true** |
| 9.000000000000002 | true | true |

**dbt-er implements `match_probability >= var('er_threshold')` on the materialised column**, matching
`linker.clustering`. Consequently `gen_baseline.py` must call `predict()` with **no threshold** and apply
the threshold only via `cluster_pairwise_predictions_at_threshold`; a harness assertion should verify no
baseline parquet was produced with a predict-level threshold. Log the discrepancy in the divergence log.

**Restore the boundary fixture v1 had and v2 dropped:** for each threshold, find the pair minimising
`|p − t|`, assert both engines agree on inclusion, and emit the **blast radius**
(`|component_a| + |component_b|`) as a CI artefact so the exposure stays visible even when green.

### 6.2 CI hazard — `state:modified` cannot see the model JSON

`same_body` compares `raw_code`, the *unrendered* Jinja source; neither it nor `same_config` sees
`--vars`. So `dbt build --select state:modified --vars '{er_model: v4.json}'` after a v3 baseline
considers every uncontracted model unmodified and **skips it**, leaving v3-derived scores in place while
reporting green.

**Rule:** parity and baseline jobs must never use `state:modified` or `--defer`.

An earlier draft added "hash the model JSON into a var and surface it through a contracted column so a
change is visible to `same_contract`." **That does not work** — the contract checksum hashes column
*names* and types, not values, so a changed hash in a column's *data* is invisible to it (Appendix A, M3).
The workable mechanisms are in M3.

### 6.3 Determinism — the correct assertion

v1 asserted "byte-identical outputs" and "byte-identical parquet across two runs." Parquet byte-identity
is not a reasonable assertion — compression, metadata, row-group boundaries and timestamps all vary.

**Correct form:** after canonical ordering and excluding volatile columns, the *content hash* of each
output is stable across two runs, and stable under input row-order permutation.

### 6.4 Test layers

| Layer | Proves | Runs |
|---|---|---|
| Macro/SQL snapshot | JSON → SQL generation is correct and stable | every PR |
| dbt unit tests | model logic on hand-built fixtures — **every model carries them** (D12) | every PR |
| dbt data tests + contracts | invariants on real outputs | every build |
| Stage parity vs frozen baselines | equivalence with Splink, localised per stage | every PR |
| Property / adversarial | invariants under randomized input | nightly |
| Ground-truth evaluation (Stage 10) | the output is *good*, not merely identical | nightly |
| Performance | scale budget, diameter guardrail | nightly |

**Coverage metric** (absent from v1): assert in CI that every gamma value of every comparison is observed
at least *K* times across the fixture set, and that every `match_key` is observed. Without this the
fixture set can silently stop exercising a level.

**Two things to note about layer 2 under D12.** First, its coverage is a *gate*, not an aspiration:
`DbtBestPractices.md` 3.20 fails any model that arrives without a unit test, so this row's scope is the
whole model list rather than the fixed-schema subset M17 rec (c) proposed. Second, it has a precondition
the other layers do not — the `unit` materialization reads column types back from the parent relations, so
every job running unit tests first runs `dbt run --empty --select <parents>` (M17). Mechanics, selection
syntax (`test_type:unit`), fixture formats and the reason every fixture is `format: sql` live in
`DbtBestPractices.md` §12.2; the upstream reference is
[dbt — Unit tests](https://docs.getdbt.com/docs/build/unit-tests?version=2).

---

## 7. Risks & open questions

| Risk | Mitigation |
|---|---|
| **DuckDB PR #24647 changes `USING KEY` semantics.** Open and labelled *Ready To Merge* as of 2026-08-10 — eight days before this document. It redefines `UNION` under `USING KEY` from candidate-frontier to changed-key delta, **removes the `deprecated_using_key_syntax` compatibility setting**, and adds a `RECURSIVE_KEY_JOIN` probe that does not exist in 1.5.5 | Directly load-bearing on D4. Pin DuckDB exactly; use `UNION ALL` (unaffected by the redefinition); make the D4 correctness gate (§ Stage 0.5) a **blocking** check on every DuckDB bump; track the PR |
| **Deep components make clustering unbounded.** Chains: 10k = 63–207 s, 20k = 523 s, 100k infeasible. No max-recursion-depth setting exists — an unterminated recursion runs until the process is killed | Short term: an in-query iteration cap (not a diameter estimate — see M5). Real fix: **D4b**, a custom materialization with pointer jumping, measured at **0.92 s vs 206.63 s** on a 10k chain. The O(diameter) pathology affects Splink equally, so the fallback is not "use Splink" |
| **Clustering is 3.4–18.5× slower than Splink's Python loop**, worst at the largest scale | Stated openly in D4a; Stage 6's budget is set against Splink's measured time, not against an aspiration. If binding, switch to materialised iterations and give up the single-statement property |
| `USING KEY` is memory-resident and does not spill; OOMs rather than degrading | Working set ≈ 10–20× base tables (D4a). Size the pod from measured floors; assert `memory_limit` is set so the failure is a clean exception |
| Model JSON round-trip silently invents `m` values (§3.4) | Baselines generated from reloaded JSON; a test asserts the reload diff on the fixture model |
| TF exact-match-level resolution is a sqlglot CNF analysis we approximate in Jinja (§3.2) | Restrict to the resolvable shapes and **fail compilation** on anything else, rather than guessing |
| Splink version drift | Pin exactly; baselines carry a version manifest; a non-blocking canary job runs against latest |
| Pair explosion | Port Splink's estimator (`count_comparisons_from_blocking_rule`, `n_largest_blocks`) for the **estimate**, but derive the **threshold** locally. Splink's `max_rows_limit = 1e9` is a *per-rule* check; at this design's measured 946 B/pair it would admit a **946 GB** build (Appendix A, B1) |
| **Materialising one model per Splink CTE inflates resident bytes** and lowers the single-node ceiling below Splink's (Appendix A, B1: 17.6× at full retain semantics) | **Resolved by D11**, not by `ephemeral`. Decision is `table` everywhere for inspectability; the mitigation is *narrowness* — measured, the `_l`/`_r` passthrough is 3.0× of the pairs+CV total, so dropping it recovers most of the gap (401M vs 134M pairs at 40 GB). A reduced ceiling remains and is accepted and published, not engineered away |

**Open questions**

1. **Target scale is unstated — now the highest-value open question.** With D11 fixing materialisation to
   `table` and D4b sequenced after parity, record count is the one input that decides whether either
   trade is comfortable or binding. Needed: a target record count and the pod size it runs on. The
   incumbent's committed envelope (`benchmarks/scales.yaml`, a `1m` scale on 16 cores / 56 GB) is the
   obvious anchor rather than inventing one. Stage 6's v1 criterion ("budget set after first
   measurement") is not an acceptance criterion; anchor to Splink's runtime on the same graph.
2. ~~**Is the single-statement property worth 3.4–18.5×?**~~ **Resolved in principle, deferred in
   practice.** D4b shows the escape hatch exists (custom materialization + pointer jumping, measured
   225× on the pathological case) and that adopting it cannot cost parity, because the min-label
   fixpoint is unique. Decision: **ship the recursive CTE for parity, revisit under D4b afterwards.**
   The remaining question is only *when*, which is a scheduling call, not an architectural one.
3. **`two_dataset_link_only` orientation** (S2) — decide whether to support it at all, or require
   `link_only` with ≥3 tables / explicit ordering.
4. **EM at production scale.** D5's recursive-CTE EM is verified correct against a Python reference, but
   only on the agreement-pattern-collapsed table. Its runtime on a real corpus, and whether the
   per-pair (TF-aware, `estimate_without_term_frequencies=False`) variant is tractable in one statement,
   is **UNVERIFIED**.

## 8. Definition of Done

1. Stage 0–11 acceptance criteria green in CI. **Stage 12 is deliberately outside this item**: its AC is a
   production-scale shadow run and a rehearsed rollback, neither of which is CI-checkable. The package is
   done before the migration is, and Stage 12's own AC governs the migration (closes RC14's first half).
2. `dbt build` on a fresh clone, with the model JSON in **`DBT_ER_MODEL_JSON`**, produces golden records
   end-to-end with **zero Python in the dbt run**. Note the narrowing: the *run* is Python-free because
   the JSON carries rendered SQL (§1.2), but two of Splink's resolutions — TF exact-match-level detection
   (sqlglot CNF) and backend `link_type` selection (a runtime table count) — are not arithmetic and
   cannot be done in Jinja. They belong to a **compile-time sidecar** that pre-resolves the model JSON
   once (Appendix A, §A.2). "Zero Python anywhere" is not achievable in any design and is not claimed.
3. **Correctness and stability, split** (M18): a parallel seeded-run sweep for correctness, **and** ten
   consecutive green nightly differential runs for concurrent stability. Only the second half needs the
   calendar. The serial form alone is an uncompressible ≥10-day tail — ≈13.4 nights at p = 0.95 — and a
   failure in it is not reproducible without Stage 11's failure-bundle schema (closes RC14's second half).
4. A divergence log documenting every Splink subtlety found, each pinned by a test — including the
   deliberately-replicated `min(match_key)` VARCHAR bug (S4).
5. `PARITY.md` stating, with evidence links, exactly what is identical and what is bounded — using
   **A.4's** policy, not §6.1's strict subset and not v1's inconsistent pair of tolerances.
6. **Every model has unit tests, and every model's unit tests were written with the model** (D12).
   Mechanised by `DbtBestPractices.md` 3.20, so item 1 already fails without it; stated here because the
   second half — *written with the model, not retrofitted* — is the part no gate can see, and a batch of
   unit tests added at the end to turn a coverage gate green is the failure mode this item names.

> **[REVIEW 2026-08-23] Fixed (F14) — RC14 is closed by items 1 and 3 above.** Stage 12's exclusion is now
> stated with its reason rather than left as apparent staleness, and item 3 adopts M18's split. Item 2 also
> changed: it read `dbt build --vars "{er_model: …}"`, which **D1 supersedes** — `--vars` fails at
> `MAX_ARG_STRLEN` (128 KiB, ≈330 levels) and is unreachable from `schema.yml`, so the model JSON arrives
> through `env_var('DBT_ER_MODEL_JSON')`. A definition of done stated as a command nobody can run is not
> checkable.

---

# Appendix A — Adversarial Review of Draft v2

**Method.** Five independent red-team passes (parity/correctness, dbt feasibility, scale/production, ER domain, test strategy/execution) produced 67 raw findings against this document. This appendix is the deduplicated, ranked survivor set. v2 had already absorbed ~40% of the raw findings as one-to-six-line corrections; those are listed in *Rejected* with the reason, so the reasoning stays auditable rather than being silently re-litigated.

**Evidence classes.** `[SRC]` = read from Splink 4.0.16 / dbt-core 1.12.2 source on disk. `[RUN]` = executed in this session (DuckDB 1.5.5 in-memory, threads=4, darwin arm64) — reported with the query shape. `[RECON]` = executed in the prior verification corpus. `[DERIVED]` = arithmetic from `[RUN]`/`[SRC]` constants. Anything unlabelled is reasoning and is marked **UNVERIFIED**.

---

## A.1 Ranked findings

Ranked by *severity × how early the decision is irreversible*. Blockers 1–3 change models you have not written yet; deferring them means rewriting Stages 2, 6 and 8.

---

### B1 — One model per Splink CTE costs 17.6× resident bytes, and the design has an underivable-but-derivable single-node ceiling

**Severity:** BLOCKER · **Attacks:** §2 surface table (the 1:1 surface→model mapping), D4a (honest about clustering, silent about scoring), §5 stage decoupling, §7 Open Question 1

**Claim.** Splink executes `blocked_with_cols → __splink__df_comparison_vectors → __splink__df_match_weight_parts → __splink__df_predict` as **one** `CREATE TABLE … AS WITH …` and materialises none of the intermediates `[RECON: inference.py:271-301, comparison_vector_values.py:103-127]`. §2 maps each of those CTEs to its own dbt model, and the §5 stage-decoupling mechanism makes that *mandatory* (a model must be a real relation for `er_inject_baseline` to swap it). The document never states the cost of that conversion. It is the single largest scale consequence of the architecture and D4a's "honest performance statement" covers only clustering.

**Evidence.**
- `[RUN]` 200k records → 28,471,500 pairs, resident bytes via `duckdb_memory()` tag `IN_MEMORY_TABLE`: 3-column pair table = **29.9 B/pair**; 19-column comparison-vector table (4 comparisons, `retain_matching_columns` semantics) = **166.8 B/pair** → **5.6× amplification** for one stage at four comparisons.
- `[RECON]` 1,000,000 records / 5,570,104 pairs / 6 comparisons: `int_candidate_pairs` 239 MB (43 B/pair), `int_comparison_vectors` 2,385 MB (428 B/pair), `int_scored_pairs` 2,644 MB (475 B/pair) → stages 3+4+5 = **5,268 MB = 946 B/pair**, build 21.1 s. Splink's fused single CTAS on the same data = **299 MB (54 B/pair)**, build 12.2 s. **17.6× bytes, 1.74× wall time.**
- `[RECON]` The naive counter-fix is worse: collapsing the four models into one statement with plain CTEs measured **3.12 s vs 1.25 s staged (2.4× slower)** at 40k nodes / 513k pairs, because DuckDB inlines CTEs and Splink's final projection repeats the Bayes-factor product three times (once in `log2`, twice in `bf/(1+bf)` — `predict.py:196-220`). With `AS MATERIALIZED` on each CTE it returns to 1.25 s.
- `[DERIVED]` Ceiling on a 40 GB DuckDB `memory_limit`: dbt-er `40e9/946 = 42.3M` pairs; Splink `40e9/54 = 741M` pairs. Against the reference blocking config's measured Σp² (name+postal prefix = 1.114e-5), 42.3M pairs ⇒ **N ≈ 2.75M records** vs Splink's ≈ 11.5M on the same pod. At 10M records the same config yields 557M pairs ⇒ ~527 GB for stages 4–5 alone.

**Failure scenario.** The team builds to green CI on `fake_1000`, ships, and hits a hard `OutOfMemoryException` mid-`dbt build` at ~2.75M records with stages 0–4 already materialised. There is no documented exit path, and the ceiling is **4.2× below** what the system being migrated from already provisions for (`benchmarks/scales.yaml` defines a `1m` scale on a 16-core/56 GB runner). The migration is a silent scale regression.

**Recommendation.**
1. **[SUPERSEDED by D11 — see §B.1 G1.]** Add a normative **§2.1 Materialisation contract**: per model, `table` | `view` | `ephemeral`, with the measured B/row. Make `int_comparison_vectors` and the wide half of `int_scored_pairs` **`ephemeral` by default** via `var('er_materialise_intermediates', false)`, which dbt fuses back into one CTAS — `[RECON]` dbt-core 1.12.2's `inject_ctes_into_sql` (compilation.py:844-927) handles this correctly including ahead of `RECURSIVE`. The parity harness sets the var to `true`; production leaves it `false`.
2. **Mandatory in `int_scored_pairs`:** compute `least(greatest(<product>,1e-300),1e300) as _bf_clamped` **once** in an inner subquery and derive both `match_weight = log2(_bf_clamped)` and `match_probability = _bf_clamped/(1+_bf_clamped)` from that column. dbt's ephemeral wrapper hard-codes ` {name} as (…)` with no `MATERIALIZED` (compilation.py:616-625), so single-evaluation must be structural, not a hint. This is also strictly better for float parity than repeating the product.
3. Drop the wide `<col>_l`/`<col>_r` passthrough from the persisted `int_scored_pairs` — it is 428 of the 475 B/pair and is re-derivable by joining `stg_input` on two ids.
4. Add `make capacity`: reports `er_bytes_per_pair` measured on the actual model JSON and `er_max_pairs = memory_limit × headroom / bytes_per_pair`. **Replace Splink's ported `max_rows_limit = 1e9`** — at 946 B/pair that admits a **946 GB** build, 23.6× past a 40 GB pod, and it is a *per-rule* check in Splink (`blocking_analysis.py:253, 260-285`) where the budget here is bytes summed across rules. Port `count_comparisons_from_blocking_rule` / `n_largest_blocks` for the *estimate*; derive the *threshold* locally.
5. Close Open Question 1 with a record-count target and its pod size, adopted from the incumbent's committed envelope rather than invented, and publish the ceiling in `PARITY.md`.

---

### B2 — The edge set is not a parity artefact, its threshold predicate is unspecified, and §6.1's tolerance is mutually unsatisfiable with the cluster gate

**Severity:** BLOCKER · **Attacks:** §6.1 tolerance table, Stage 6 (`int_edges`, `>=`), Stage 0.3, Stage 5 AC

**Claim.** §6.1 permits `|Δ match_weight| ≤ 1e-9` "with a divergence-log entry" while requiring **partition equality** for clusters. These cannot both hold: `int_edges` is a hard predicate, so a pair whose weight differs by 1e-9 and sits within 1e-9 of the threshold flips membership, and connected components turns one flipped edge into a whole-component merge. The permitted scoring drift is *unbounded in cluster space*. Compounding it, Splink applies the threshold in **two different spaces** depending on which API produced the baseline, so the edge sets can differ at **zero** arithmetic drift. v1 listed "two components joined by exactly one edge at exactly the threshold" as a Stage 6 fixture; v2's Stage 6 AC dropped it.

**Evidence.**
- `[SRC]` `predict.py:104-111` — `threshold_expr = f" where log2({bayes_factor_expr}) >= {threshold_as_mw} "` (weight space, recomputed expression). `linker_components/clustering.py:105-118, 266-279` — `match_p_expr = f"where match_probability >= {threshold}"` (probability space, materialised column). Conversion at `misc.py:193-230`.
- `[RUN]` DuckDB 1.5.5, `t = 0.9`, `threshold_as_mw = log2(0.9/0.1) = 3.1699250014423126` (≠ `log2(9.0) = 3.169925001442312`):

  | bf | `log2(bf) >= 3.1699250014423126` | `bf/(1+bf) >= 0.9` |
  |---|---|---|
  | 8.999999999999998 | **false** | **true** |
  | **9.0 exactly** | **false** | **true** |
  | 9.000000000000002 | true | true |

  The two predicates disagree **at bf = 9.0 exactly**, with no arithmetic error anywhere.

  > **[REVIEW 2026-08-23] Fixed (F4):** the table's header cell above was split by a raw newline
  > mid-number (`…144231` / `26`), which broke Markdown rendering of the whole table; rejoined. The §6.1
  > copy of this table was already well-formed.
- `[RUN]` Probability parity is *vacuous* above `mw = 54`: `2**54/(1+2**54)` is exactly `1.0` in float64 (`1-p = 0.0`); at `mw = 53`, `1-p = 1.11e-16`. So any absolute probability tolerance is satisfied for free in exactly the region where Splink's `[1e-300, 1e300]` clamp and `'Infinity'` sentinel diverge most.
- `[RECON]` Achievable drift is 5 orders *tighter* than the permitted gate: enumerating all 2,880 gamma vectors of a real production model, max `|Δmw|` between linear-product-then-log2 and sum-of-logs = **2.84e-14**.

**Failure scenario.** Stage 5 is green at Δmw = 8e-10 on a pair at `match_probability = 0.9000000004`. Stage 6 fails partition parity by one giant merged cluster. The localisation points at the clustering model — correctly. An engineer rewrites clustering, cannot reproduce, and eventually loosens the cluster gate from partition equality to pairwise-F1 ≥ 0.999, after which the package can ship arbitrary wrong merges silently.

**Recommendation.**
1. **Promote `int_edges` to a first-class parity artefact with exact set equality.** It is the interface between the tolerant stage and the exact stage; that is where the tolerance must be reconciled, not at the cluster.
2. **State the predicate normatively:** dbt-er implements `match_probability >= var('er_threshold')` on the *materialised* column, matching `linker.clustering`. `gen_baseline.py` must therefore call `predict()` with **no threshold** and apply the threshold only via `cluster_pairwise_predictions_at_threshold`. Add a harness assertion that no baseline parquet was produced with a predict-level threshold, and log the weight-vs-probability discrepancy in the divergence log.
3. **Add the decision gate to §6.1** (see §A.4): for every tested threshold `t`, assert `(p_dbt >= t) == (p_splink >= t)` for every pair — exact boolean agreement on edge-set membership. This is the only gate that protects the property that matters.
4. Restore and generalise the boundary fixture: for each threshold find the pair minimising `|p − t|`, assert both engines agree on inclusion, and emit the **blast radius** (`|component_a| + |component_b|`) as a CI artefact so the exposure is visible even when green.
5. Write `>=` explicitly everywhere; `>` silently drops the at-threshold edge.

---

### B3 — No cutover stage, and live-corpus TF is a silent breaking change against the system being replaced

**Severity:** BLOCKER · **Attacks:** §1.3 non-goals, §3.5, §5 stage list, Stage 8 AC, §8 DoD

**Claim.** `grep -ic` over the document returns **0** for `cutover`, `shadow`, `rollback`, `migration`. §1.3 positions dbt-er as a drop-in matching engine inside the existing platform, which makes delivery a **swap** — the highest-risk operation in the programme — with no dual-run period, no go/no-go metric, no rollback trigger, and no supported-configuration matrix. Worse, §3.5 contains an unnamed behavioural conflict: dbt-er computes TF from the live corpus every run, while the incumbent **freezes** it.

**Evidence.**
- `[RECON]` Incumbent contract: `src/er/matching/tf.py:261-319` calls `register_term_frequency_lookup` per TF column and **never** `compute_tf_table` (a unit test guards that `compute_tf_table` has no call site in `src/`); `tf_lookup` is keyed `(model_version, tf_snapshot_id, column_name, value, tf_value)`; missing rows are a precondition failure (exit 3), never a fallback. The purpose is the named invariant that a pair's `match_probability` is a pure function of `(model_version, tf_snapshot_id, rec_a_key, rec_b_key, both content hashes)`.
- `[RUN]`/`[RECON]` Magnitude: with `u_exact = 0.001` and Splink's `POW(u_exact / GREATEST(tf_l,tf_r), w)`, a value whose tf moves from 0.0909 to 0.45 because 9 unrelated records arrived moves that comparison's contribution from **−6.51 to −8.81 bits** — 2.31 bits on a pair whose two records did not change.
- `[RECON]` Incumbent shape the doc never scopes to: `LINK_TYPE = "dedupe_only"` hardcoded and `UNIQUE_ID_COLUMN = "record_key"` (VARCHAR), **no `source_dataset` column** (`src/er/matching/model.py:210,213,214`). Every hard case in D3 (`-__-` composite, lexicographic ordering) and S2 (`two_dataset_link_only`) is **dead code** for the actual migration target.

**Failure scenario.** dbt-er reaches Stage 11 green on synthetic fixtures, is swapped in, and the first production ingest re-scores every existing pair because TF moved — silently reclassifying gray-band pairs and re-partitioning entities, with no rollback path and no shadow run that would have caught it. Separately, Stage 8's own AC (80/20 full-vs-incremental equivalence) is unachievable *by construction* rather than approximately, because the two paths use different TF.

**Recommendation.**
1. **Make frozen TF a first-class, tested mode and the default for the migration.** `tf_all` sources from a snapshot relation keyed `(model_version, tf_snapshot_id)`; a missing value is a compile/run-time error mirroring the incumbent's precondition semantics; live-corpus TF becomes the opt-in variant used only when minting a new snapshot. Then Stage 8's equivalence AC becomes meaningful instead of accidental.
2. Add an explicit **TF-refresh operation** that mints a new `tf_snapshot_id` and rescores, so TF drift is a dated, reviewable event rather than a side effect of ingest. Add the AC: *rescoring the same pair under the same `(model_version, tf_snapshot_id)` yields a bit-identical `match_weight` regardless of what else is in the corpus.*
3. Add **Stage 12 — Cutover**: a shadow-run job running both engines on production data, diffing at the platform boundary (edge set at the auto-merge threshold, then cluster partition), with a numeric go/no-go (symmetric difference of the edge set = 0; partition delta ≤ N entities, N stated), a documented rollback switch, and its trigger criteria.
4. Publish a **supported-configuration matrix** for v1 — `dedupe_only`, VARCHAR `unique_id`, no `source_dataset`, plain equi-join blocking rules, no `arrays_to_explode` — with compile-time errors on everything else. This also lets Stage 3 defer `link_only` / `two_dataset_link_only` / exploding rules to v2 and materially de-risks the highest-risk parity stage. Note `salting_partitions` can be **ignored** rather than errored: `[RECON]` the pair set and match_keys are exactly invariant under salting (3,281 pairs identical, salted == unsalted including `match_key`, stable across runs).

---

### B4 — Stage 8's `where a.is_new or b.is_new` is not incremental: measured ≥100% of a full rebuild

**Severity:** BLOCKER · **Attacks:** §2 table row `find_matches_to_new_records` ("SQL removes the two-pass wart"), Stage 8

**Claim.** The document presents the disjunctive predicate as strictly better than Splink's two-pass incremental. It is correct but **not incremental**: DuckDB cannot push a predicate that references both join sides through the join, so the query builds the full self-join and filters afterwards. The claimed simplification converts an O(batch × corpus) operation into O(corpus²). Splink's "wart" is not an API defect — it is what makes the operation cheap.

**Evidence.** `[RUN]` DuckDB 1.5.5, threads=4, blocking key `family_prefix || '|' || postcode`, count-only queries (so materialisation cost is excluded and the measurement isolates the join):

| N / batch | (a) full self-join | (b) `… where (l.is_new or r.is_new)` | (c) batch-driven `batch ⋈ corpus` |
|---|---|---|---|
| 300k / 3k, 1.10M pairs | 0.04 s | **0.05 s** (24,000 pairs) | 0.01 s |
| 1M / 10k, 13.39M pairs | 0.35 s | **0.49 s** (270,000 pairs) | 0.09 s |

At 1M records, (b) costs **139% of the full rebuild** and **5.4× the batch-driven form**, for identical output (270,000 pairs both). `[RECON]` at 1M/10k on a denser rule the same comparison measured 10.96 s vs 0.22 s = **50.4×**. The ratio depends on selectivity; the structural result — (b) ≥ (a) — does not.

**Failure scenario.** The headline benefit of Stage 8 is not delivered. A 1% daily batch costs more than re-running everything, so the incremental path is indistinguishable from a full rebuild, and Stage 8's AC (80/20 equivalence) passes while the performance premise fails silently. At the B1 ceiling, a nightly 10k batch on a 2.5M corpus pays the full ~5 GB / ~21 s stage-3-5 cost every night.

**Recommendation.** Specify Stage 8 as a **UNION of two explicitly-driven joins**, mirroring Splink's decomposition for the reason Splink has it: `(batch ⋈ corpus)` `UNION ALL` `(batch ⋈ batch)`, with `batch` on the build side of both, canonicalising orientation in the projection. Add an AC: *the incremental blocking model's wall time is < 10% of the full model's on the 1M fixture* — a measured gate, since the disjunctive form passes every correctness test. Record in the divergence log that Splink's two-pass structure is **reproduced for cost**, not worked around. (The genuine dbt simplification remains real and worth stating: dbt accepts a precomputed pair table, so this is one query and one connection, where the incumbent needs two `DuckDBAPI` handles because Splink caches `__splink__df_concat_with_tf` by templated name — `src/er/matching/incremental.py:578-590`.)

---

### B5 — Stage 9's training oracle is not reproducible: measured 1.63 match-weight spread, and "the same iteration count" is unobservable

**Severity:** BLOCKER · **Attacks:** Stage 9 AC, D5, D9, Stage 0.3

**Claim.** Splink training is not a function of `(data, seed)`. Stage 9's AC asserts "EM within 1e-4 of Splink's on the same blocking pass **with the same iteration count**" against a reference that varies run to run, whose iteration count is not observable, and whose stated tolerance equals Splink's own convergence threshold.

**Evidence.**
- `[RECON]` Two independent trainings on byte-identical data, splink 4.0.16, in-memory DuckDB, 400 rows, prior + `estimate_u` + 2 EM sessions: with a **fixed** `estimate_u` seed the saved model JSON was **not byte-identical** (max `|Δ match_weight| = 5.68e-12`). With Splink's **default** `seed=None` and `max_pairs=5000` (forcing `proportion < 1`, i.e. real sampling), max `|Δ match_weight| = **1.6284809531477045**` across **16,553 / 16,553 pairs**. Default confirmed at `[SRC] linker_components/training.py:169` (`seed: int = None`).
- `[SRC]` `expectation_maximisation.py:332-335`: `if max_change_dict[...] < em_convergence: break` inside the loop, then `logger.info(f"\nEM converged after {i} iterations")` **outside** it, unconditionally. `[RECON]` observed it print "EM converged after 25 iterations" on a run whose iteration-25 max change was 1.96e-4 — above the criterion. The log cannot distinguish convergence from cap exhaustion.
- `[SRC]` `settings.py:195-196`: `em_convergence = 0.0001`. The AC's tolerance equals it, so a sub-tolerance parameter difference can move the early stop by one iteration and produce a supra-tolerance difference in *every* parameter.

**Failure scenario.** A Stage 9 parity run is red or green depending on which Splink run it was compared against. The nightly job regenerates the oracle, so a 3am failure is not reproducible from the same seed and the same data. Weeks are spent chasing a difference that is in the oracle.

**Recommendation.** Make the training oracle an **artefact, not a procedure**.
1. Freeze, per model in the library, a captured Splink **training trace**: initial parameters, the per-iteration `m_u_counts` output, and the final JSON — generated once, reviewed, committed, hashed.
2. Compare **per-iteration parameter trajectories** against that trace, not the endpoint. This converts a 1e-4 endpoint check into an exact per-iteration check where drift is attributable.
3. Require `seed` on `estimate_u` everywhere (Splink's default is `None`) and assert its presence in the harness.
4. Record in the trace whether the run ended by `break` or by exhausting `max_iterations`, and require our EM to agree — the log message cannot tell you.
5. Extend Stage 0.3's baseline format now to carry traces; retrofitting after the format is frozen is the expensive path.

---

### M1 — Asymmetric comparison levels invalidate S2's "match the set, not the orientation" escape

**Severity:** MAJOR · **Attacks:** S2, D3, Stage 4

**Claim.** S2 concedes orientation for `two_dataset_link_only` and proposes matching the pair *set* instead. That fallback is unsound: **gamma is not orientation-invariant** for two shipped level types, so a canonicalised set comparison at Stage 3 hides a real Stage-4 divergence rather than deferring it.

**Evidence.** `[RECON]` rendering the level creators against `SplinkDialect.from_string('duckdb')`: `ColumnsReversedLevel('forename','surname')` → `"forename_l" = "surname_r"` — note **`symmetrical: bool = False` is the default** (`comparison_level_library.py:363-397`); `LiteralMatchLevel('city','London',side_of_comparison='left')` → `"city_l" = 'London'` (`:333-354`). Executed in DuckDB 1.5.5 on `(forename_l='Smith', surname_l='John', forename_r='John', surname_r='Zed')`: the Splink orientation gives `false`, the flipped orientation gives `true` — same pair, opposite gamma. Counter-check that kills the broader version of this finding: the TF divisor **is** orientation-invariant — `CASE WHEN coalesce(tf_l,tf_r) >= coalesce(tf_r,tf_l) …` returns identically under l/r swap for `(0.01,0.03)`, `(NULL,0.03)`, `(0.01,NULL)`, `(NULL,NULL)`.

**Failure scenario.** A link-only job canonicalises at Stage 3 (green), then Stage 4 diverges on every pair scored through an asymmetric level, and Stage 5's per-comparison `bf_<name>` localisation names the right comparison with no explanation. Dormant on dedupe-only configs with symmetric levels — i.e. until the first link job or the first `ColumnsReversed` level.

**Recommendation.** Add a **Stage 1 compile-time lint**: scan every `sql_condition` for asymmetric shapes (`<a>_l = <b>_r` with `a ≠ b`; any condition referencing only `_l` or only `_r`) and hard-fail if any exists while orientation is not pinned. Add explicit compile-time vars `er_backend_link_type ∈ {dedupe_only, link_and_dedupe, link_only, two_dataset_link_only}` and, for the two-table case, `er_left_table` — the backend link type is **not** the JSON's `link_type` (`[SRC] inference.py:227-246` selects it from `len(input_tables_dict)`), so it cannot be read from the contract. Fold into B3's supported-configuration matrix: v1 supports `dedupe_only` only, and this class disappears.

---

### M2 — Models whose columns come from the JSON cannot be contracted or unit-tested, and the mechanism that would fix it is unstated

**Severity:** MAJOR · **Attacks:** D1 corollary, Stage 2.3-equivalent contract work, Stage 4, Stage 5, §6.4 layers 2–3

**Claim.** D1's corollary correctly says columns must be parse-time-derived, but the document never says **how** `int_comparison_vectors` (one `gamma_<name>` per comparison) and `int_scored_pairs` (one `bf_<name>` + optional `bf_tf_adj_<name>`) declare those columns. dbt parses schema.yml as YAML **before** rendering Jinja, so `{% for %}` cannot emit YAML structure — which silently removes contracts, per-column tests, docs and unit-test fixtures from exactly the two models Stages 4 and 5 are about. There **is** a working mechanism, and it constrains D1's ingestion choice.

**Evidence.**
- `[SRC]` `dbt/parser/schemas.py:128-155` — `load_yaml_text(...)` runs before any render; `dbt/config/renderer.py:43-48` — `render_value` calls `get_rendered(value, self.context, native=True)`, i.e. **native** Jinja, so a string leaf that renders to a Python literal becomes that object.
- `[SRC]` `dbt/context/configured.py:80-98` — `SchemaYamlContext` adds exactly `var` and `env_var` to the base context. **No project macros.** So the column list must come from a `var`, not a macro.
- `[RECON]` Driving dbt's own `SchemaYamlRenderer(ctx,'models').render_data({... 'columns': "{{ var('er_cols') }}"})` returned `columns` as a genuine `list` of dicts with `name`/`data_type` intact; the `{% for %}` form returned a `str`.
- `[SRC]` Contracts are enforced at **run** time inside the materialization (`dbt/include/duckdb/macros/adapters.sql:157-160`), and `columns_spec_ddl.sql:35-41` raises `raise_contract_error([], [])` when `model['columns']` is empty — so an unlisted column set is a hard failure, not a soft skip.

**Failure scenario.** Nobody notices, and §6.4's "dbt data tests + contracts" row quietly applies only to the fixed-schema models while the two highest-risk models ship untyped. Or it is noticed mid-Stage-4, at which point the fix requires the model JSON to be reachable as a plain `var` in the schema.yml context — a Stage-1 decision being made in Stage 4.

**Recommendation.** Split models by schema stability and say so:
- **Fixed schema, contract normally:** `stg_input`, `tf_all` (3 cols — a further argument for D7), `int_candidate_pairs` (3 cols: `match_key VARCHAR`, `unique_id_l`, `unique_id_r`, mirroring `__splink__blocked_id_pairs`), `int_edges`, `entity_clusters`, `cluster_membership`, `golden_records`.
- **JSON-derived schema:** publish `er_gamma_columns` / `er_bf_columns` as vars alongside `er_model` and write literally `columns: "{{ var('er_gamma_columns') }}"` and `contract: {enforced: "{{ var('er_enforce_contracts', true) }}"}`. Because the schema.yml context has no macros, this derivation must live where `er_model` is emitted (the wrapper), not in a macro. Add a pytest asserting `er_gamma_columns` equals what `comparison_vector_sql` actually renders, or the two drift.
- Add the compile-time check Splink lacks: `output_column_name` must be unique **after** `.replace(" ", "_")` — `[RECON]` `[cl.ExactMatch('city'), cl.LevenshteinAtThresholds('city',2)]` yields `['gamma_city','gamma_city']` with no error or warning.
- Note in D1: `--vars "{er_model: $(cat f.json)}"` (double-quoted, as written) is correct and preserves both quote styles, but is bounded by `MAX_ARG_STRLEN` = 128 KiB (~330 levels at the measured 397 B/level). `fromjson(env_var('DBT_ER_MODEL_JSON'))` has no such bound and is available in the macro, model **and** schema.yml contexts. Prefer it; keep `--vars` as the documented CI form.

**Interaction with D12 (added 2026-08-23).** This finding's title says these two models "cannot be … unit-tested"; the recommendation shows they can, and D12 now requires it. The consequence is that the wrapper emitting `er_gamma_columns` / `er_bf_columns` must also emit the **unit-test fixtures** for those two models, since a fixture is a column list with values attached and hand-writing it re-creates exactly the second-hand-maintained copy this finding is about. The drift-guard pytest in the third bullet therefore covers three artefacts against one model JSON — the rendered SQL, the contracted column list, and the fixture — not two.

---

### M3 — §6.2's own mitigation does not work: the contract checksum hashes column *names*, not values

**Severity:** MAJOR · **Attacks:** §6.2

**Claim.** §6.2 correctly identifies that `--vars` is invisible to `state:modified`, then prescribes "hash the model JSON into a var and surface it through a contracted column so a change is visible to `same_contract`." A hash carried as a column **value** can never change the contract checksum.

**Evidence.** `[SRC]` `dbt/contracts/graph/nodes.py:687-709` — `build_contract_checksum` accumulates `f"|{column.name}" + str(column.data_type) + str(column.constraints)` over sorted columns, plus materialization and model constraints when enforced, then sha256. No data, no config, no vars. `:369-370` — `same_body` is `self.raw_code == other.raw_code`. `:385-389` — `same_config` compares `unrendered_config`, which stores raw Jinja text, so `config(meta={'sha': var('er_model_sha')})` is textually invariant across model JSONs.

**Failure scenario.** Someone adds `--select state:modified` to make the nightly loop cheaper, believing §6.2 protects them. Uncontracted models are skipped, stale v3-derived scores remain, and the parity run reports green against a v4 model JSON — the exact failure §6.2 exists to prevent, recreated by §6.2's own recommendation.

**Recommendation.** Put the hash in the **name space**: a contracted column literally named `er_model_<sha8>` (name change ⇒ checksum change), or suffix the target schema/alias with the hash so `same_database_representation` fires. Make the prohibition enforceable rather than advisory: a CI lint that greps `.github/workflows/**` and the Makefile for `state:` and `--defer` and fails the build.

---

### M4 — Stage decoupling is the right idea in six lines that will not work as written

**Severity:** MAJOR · **Attacks:** §5 "Stage decoupling"

**Claim.** The mechanism is sound — `[RECON]` `dbt_extractor.py_extract_from_source` raises on a model containing a conditional `source()`/`ref()`, so dbt falls back to full Jinja render (`dbt/parser/models.py:279-282`) where vars are available; driving `get_rendered` with a capturing context showed var OFF → `refs [('int_candidate_pairs',)] sources []`, var ON → `refs [] sources [('er_baseline','int_candidate_pairs')]`, i.e. only the taken branch becomes a DAG edge. Four things in the six lines make it unusable or unsafe.

**Evidence & the four defects.**
1. `er_inject_baseline` is a **single global boolean**. Flipping it injects at *every* model at once — the one configuration that tests nothing.
2. `source('baseline', …)` in what §1.3 calls a package forces its database/schema on every consumer; the `overrides:` escape emits `source-override-deprecation` in dbt 1.12.2 (`[SRC] dbt/parser/sources.py:344,373`; `dbt/jsonschemas/jsonschemas.py:222-227`).
3. Nothing states that injected mode is **insufficient**: a green stage-N run with an injected stage-(N−1) input proves nothing about stage N−1, and only the un-injected mode catches compounding drift.
4. Nothing binds the injected baseline to the model JSON, so a stage-4 test can be green against a stage-3 baseline built from a different model. And per §6.2, the injection var is itself invisible to dbt's change detection, so a build left in injected mode is unauditable.

**Failure scenario.** The first engineer flips one boolean, every model reads a baseline, all stage tests pass, and the suite has silently stopped testing the package. Or the source ships in the package and no external consumer can build it.

**Recommendation.** (a) `var('er_inject', {})` is a **mapping** model-name → bool; each injectable model reads `{% if var('er_inject', {}).get('<this model>') %}`. (b) The baseline source lives in the harness project, never the package; the package accepts an optional relation-name var and ships **zero sources**. (c) Normative CI rule: every stage runs in **both** modes on every PR — `isolated` for localisation, `integrated` as the release gate; a stage is green only if both are. (d) Every baseline manifest carries `sha256(model JSON)`; the injection macro fails compilation if it differs from the active `er_model`. (e) Stamp `er_inject_baseline` into a contracted column name (per M3) so the mode is queryable after the fact, and assert it is `false` on every row of the end-to-end run.

---

### M5 — The diameter guardrail cannot be computed more cheaply than the thing it guards; every cheap proxy is anti-correlated with cost

**Severity:** MAJOR · **Attacks:** Stage 6 AC ("a diameter guardrail fires"), §7 risk row 2

**Claim.** Graph diameter — even a double-sweep 2-approximation — requires the same traversal and the same `O(diameter × edges)` cost as the clustering it is meant to guard. The guardrail as specified is either free-and-useless (size-keyed) or correct-and-not-a-guardrail (traversal-based).

**Evidence.** `[RECON]` DuckDB 1.5.5, threads=4, D4's formulation: 4,000-node / 3,999-edge **chain** = 32.2 s (1 component); 800,000-node / 2,400,000-edge **random** = 51.4 s (2,011 components). A graph 200× smaller in nodes and 600× smaller in edges costs 63% of the wall time. Combined with D4a's own table (20k chain = 523.3 s; 500k-node/1M-edge random = 39.3 s) the mis-ranking is ~330× in the wrong direction: a size threshold admitting the 39 s job admits the 523 s job. `[RECON]` `SELECT * FROM duckdb_settings() WHERE lower(name) LIKE '%recur%'` returns **0 rows** — there is no depth cap; an unguarded recursion was killed only via `con.interrupt()`.

**Failure scenario.** The AC is satisfied by a node/edge-count guardrail (the only cheap signal), which provably does not fire on the case it exists for. The 20k-chain pathology reaches production and, with no depth cap, runs until the liveness probe kills the pod — losing the whole `dbt build` including the expensive already-materialised stages 3–5.

**Recommendation.** Replace the diameter guardrail with an **iteration cap you can actually enforce**: carry an `iter` column in the `USING KEY` state and add `and cur.iter < {{ var('er_max_cc_iterations', 200) }}` to the recursive term's guard, so the statement terminates cleanly; then a dbt test asserts `count(*) where not converged = 0` and **fails loudly**. O(1) to add, cannot be evaded, turns an unbounded hang into a named test failure. Pair with a *diagnostic* (non-gating) max-component-size check from `cluster_metrics`. ~~and a wall-clock statement timeout in the profile so the pod fails fast rather than being OOM-killed~~ — **there is no such knob: `[RUN]` 2026-08-23 on `duckdb==1.5.5`, `duckdb_settings()` returns 0 rows for `%timeout%` and `%interrupt%` (G13).** The iteration cap is therefore the *only* guardrail, not one of two; the backstop that does exist is `memory_limit`, which fails hard rather than timing out. Add the absolute runtime gate from M11.

---

### M6 — `entity_id` is emitted as an identifier while §1.3 disclaims identity; it relabels 100% of a cluster on one insert · **CLOSED 2026-08-23**

> **Closed by §1.6** (DR-12) via this finding's own rec (a): the column is `component_label`, it is removed
> as a key from every downstream model, and Stage 6b collapses to an interface contract rather than being
> built. The `[RECON]` evidence below is what the decision rests on and is why the rename was not merely
> cosmetic.

**Severity:** MAJOR · **Attacks:** D4 (`select unique_id, entity_id from cc`), D4a, §1.3, Stage 7, §6.1 cluster gate

**Claim.** The document declares entity permanence out of scope, then names the output column `entity_id`, partitions the golden-record layer by it (D10 `PARTITION BY entity_id`), and calls golden records the differentiator. You cannot simultaneously ship the keyed artefact and disclaim the key's stability. Under min-label the value is a function of the *current* member set, so it is a contract-breaking column on every run.

**Evidence.** `[RECON]` DuckDB 1.5.5, D4's exact formulation. Run 1: `crm:100 … crm:104` chained → every `entity_id = 'crm:100'`. Run 2: add **one** record `billing:7` with one edge to `crm:102` → `entity_id` changed for **5/5 pre-existing records** (all now `'billing:7'`), no member left the entity. Run 3: drop one edge → partition splits and ids reshuffle again. Lexicographic ordering is D3's own verified property (`('ds'||'-__-'||9) < ('ds'||'-__-'||100)` → **false**), so `'billing:7' < 'crm:100'`; onboarding a source whose alias sorts early relabels every cluster it touches. Deleting the minimum member relabels every survivor.

**Failure scenario.** Any consumer storing `entity_id` — a CRM master key, a warehouse dimension key, yesterday's `golden_records` — has its entire key space rewritten by ordinary ingest. A merge, a split and a pure relabel are indistinguishable, because there is no old→new mapping and no event log: the only observable is that the row is gone. Under erasure it is worse: deleting the one record a subject asked you to erase has an observable side effect on every other member's identifier.

**Recommendation.** Pick one and write it down.
- **(a) Rename.** Call it `component_label` everywhere, remove it from `golden_records`/`cluster_membership` as a key, and state in §1.3 that the package emits **component labels** which a surrounding system maps to durable entity ids. Cheapest, honest, consistent with §1.3.
- **(b) Bring identity in scope** as a real stage (see §A.5, Stage 6b): a persisted `entity_keys(entity_key, first_seen_run, status ∈ {active,merged,retired}, merged_into)`; a `cluster_lineage(run_id, component_label, entity_key, overlap_count, disposition)` implementing a largest-overlap claimant rule with a total tiebreak; an append-only `entity_events`. This is the incumbent's `INV-PERM` and is already fully specified there — adopt rather than re-derive.

Either way, §6.1's cluster gate should assert **label equality on the component label** (D4 proves labels are identical to Splink) as the primary gate, with partition equality as fallback diagnostic; label equality localises drift to individual records instead of whole partitions and is the gate that catches a D3 separator or id-type mistake.

---

### M7 — No run identity, provenance, resumability, or partial-failure semantics

**Severity:** MAJOR · **Attacks:** §5 (whole stage list), §6.2, §6.4 Performance row, §7 Q1, §8 DoD 4–5

**Claim.** `grep -ic` returns **0** for `run_id`, `resume`, `idempot`, `retry`, `partial`, `rollback`, `observab`, `backfill`. dbt has no transaction spanning a DAG, so a failure at Stage 6 leaves stages 0–5 at run N and marts at run N−1, with nothing recording that this happened. Several of the document's own requirements presuppose a provenance layer no stage builds: §6.2 wants the model hash surfaced on outputs; §6.4 lists a Performance layer with no artefact; §7 Q1 wants a runtime anchored to Splink's, which requires storing both engines' timings; §8 DoD 4–5 require a divergence log and `PARITY.md` with no owner and no stage.

**Evidence.** The greps above. The exposure is not hypothetical at this design's scale: `[RECON]` 21.1 s and 5.3 GB for stages 3–5 at 1M records; 523.3 s for a 20k-node chain — both long enough to be interrupted by a pod eviction. `[RECON]` the incumbent has this as tested machinery: `src/er/resume.py` (`resume_plan` as a pure function over `run_stages`, refusing exit 2 on config-hash drift, exit 3 if already succeeded, never resuming mid-stage), snapshot *ranges* as the rollback unit, and a 5-value exit-code taxonomy where 10 = "nothing to do".

**Failure scenario.** After an interrupted build nobody can answer "which stages are from which run?" without hand-diffing content hashes, and `dbt build --select stage6+` re-runs against upstream tables of unknown provenance. Combined with M8, a retry can compound the damage rather than repair it.

**Recommendation.** Add **§5.0 Run contract** and **Stage 12b Provenance**: (a) an `er_run_id` (ULID) var stamped as a column on every materialised model; (b) a `_er_run_manifest` model written by `on-run-end` hooks carrying run id, `sha256(model JSON)`, `er_tf_snapshot_id`, threshold, resolved dbt/dbt-duckdb/duckdb/splink versions, per-stage row counts and wall time — this simultaneously closes §6.4's Performance row and §7 Q1's anchoring requirement; (c) an explicit per-model idempotency key, written down, which is what makes whole-stage restart safe; (d) an exit-code taxonomy distinguishing *parity failed* / *precondition failed* / *infra failed* / *nothing to do*, since Stage 11 and M12 both need to branch on it; (e) named owners for `divergence-log.md` and `PARITY.md`, plus a CI check that every deliberate divergence (S1–S4 today) has both a log entry and a linked pinning test.

---

### M8 — An incremental `int_scored_pairs` silently mixes scores from two model JSONs and two TF snapshots

**Severity:** MAJOR · **Attacks:** Stage 8, §6.2, D1

**Claim.** §6.2 finds that `--vars` is invisible to dbt's change detection but draws only the CI conclusion. The operational conclusion is worse: an incremental materialisation keyed on new records **retains** rows scored under model v3 while appending rows scored under v4. There is no model-version column, no guard, and no full-refresh trigger tied to the JSON. Clustering then thresholds across incommensurable evidence.

**Evidence.** `[SRC]` `same_body` = `raw_code` comparison (`nodes.py:369-370`); `same_config` = `unrendered_config` (`:385-389`); neither reads vars. Stage 8 specifies an incremental path with no version column. `[RECON]` the incumbent treats this as a hard precondition: `match_scores` is keyed `(rec_a_key, rec_b_key, model_version, tf_snapshot_id)` so mixing is *representable*, and `er reconcile` exits 3 if more than one distinct `model_version` appears among active rows above the review threshold. `[RECON]` magnitude of a TF-snapshot mismatch alone: a `count(*)` vs `count(<col>)` denominator difference shifts every adjusted weight by a uniform `log2(1000/891) = 0.1665` bits — and a real snapshot change is larger.

**Failure scenario.** After a retrain, `int_scored_pairs` holds v3 weights for old pairs and v4 for new. Every parity test still passes, because the harness runs full-refresh on fixtures. This is exactly the class of failure the document exists to prevent, arriving through the operational door.

**Recommendation.** Make model identity part of the **data**: `load_model_json` computes `er_model_sha`; every scored row carries it plus `er_tf_snapshot_id`; a dbt test asserts `count(distinct er_model_sha) = 1` and `count(distinct er_tf_snapshot_id) = 1` on `int_scored_pairs` and fails the build; the incremental `unique_key` includes both so a re-score appends rather than colliding; `int_edges` selects a single pair of values. This is the same column that M3 requires to live in the *name* space for `state:modified` to see it — do both.

---

### M9 — The coverage metric is unsatisfiable by generated data: measured 5 of 18 gamma cells never observed in 101,797 pairs

**Severity:** MAJOR · **Attacks:** §6.4 "Coverage metric"

**Claim.** §6.4's one-sentence metric ("every gamma value of every comparison observed at least K times; every `match_key` observed") cannot be met by random person data, and it is structurally blind to most of the generated SQL.

**Evidence.** `[RECON]` splink 4.0.16, in-memory DuckDB, 1,000-row synthetic dedupe, 3 blocking rules, 4 comparisons → **101,797 pairs**: `gamma_first_name` observed `{-1,0,1,2,3,4}` complete; `gamma_surname` observed only `{0,3,4}` — **missing −1, 1, 2** (both Jaro-Winkler bands and the null level); `gamma_dob` missing −1; `gamma_city` missing −1. **5 of 18 (comparison, gamma) cells unobserved**, i.e. ~28% of emitted `WHEN` branches never evaluated, while every parity gate is green. `match_key '2'` appeared 70 times. During the same training Splink itself warned "m probability not trained for surname … never observed in the training data" for exactly the two missing bands.

Beyond gammas, the metric is blind to: every `bf_tf_adj_<name>` branch, the clamp region, the degenerate paths (`m=0`, `u=0`, not-observed epsilon, missing-`m` default), the ELSE-level TF suppression, and — unfixably by observation — the `AND NOT (coalesce(…) OR …)` **exclusion** path, since a suppressed pair produces no row.

**Failure scenario.** CI reports green only if K is low enough to be meaningless, or the metric is disabled after the first red build. The fixture set silently stops exercising a `>=` boundary or a whole level — precisely what Stage 4's boundary fixtures exist to catch.

**Recommendation.** Split into **inventory × observation × construction**. (1) *Inventory* is a compile-time artefact derived from `er_model`: every gamma `WHEN`, every `bf_` and `bf_tf_adj_` branch, every blocking rule, every degenerate path. (2) *Observation* measures **branches, not values**: emit a parallel debug-only `CASE` returning the branch ordinal, assert ≥ K per branch. (3) *Construction*: the generator must **solve** for records hitting each cell (synthesise a pair at a target Jaro-Winkler similarity; synthesise the NULL) — random data provably will not. (4) Add **negative coverage** for the exclusion path: for each rule `r > 0`, at least K pairs satisfy `r` and are absent because an earlier rule claimed them.

---

### M10 — The parity comparator is never tested: no negative controls, no mutation testing, no flake taxonomy

**Severity:** MAJOR · **Attacks:** §6.4 test-layer table, Stage 11

**Claim.** Every gate depends on comparator code that has no test proving it *can* fail. `grep -ic` returns 0 for `flaky`, `triage`, `quarantine`, `retry`, `negative control`; `mutation` appears twice, both times about data mutation, never as a testing technique.

**Evidence.** The failure modes are cheap and green: a comparator inner-joining on `(unique_id_l, unique_id_r)` that finds zero rows because one side used the composite id; two empty frames because an injection-var typo (M4) makes both sides read the baseline; a float comparator comparing `str()` forms; a set comparator comparing `len()` before contents. The risk is concretely elevated by two verified facts: `match_key` is **VARCHAR** in Splink (`[SRC] blocking.py:203-206`, `select '{self.match_key}' as match_key`), so a dtype-coercing comparator normalises a real divergence away; and Splink's clustering emits **spurious NULL-node rows** on dangling edges (`[SRC] connected_components.py:89-100`), which D4 already asks the comparator to special-case — a comparator that drops NULL keys before diffing also hides real rows.

**Failure scenario.** The whole verification argument rests on "the comparator would have caught it." If it is broken, every stage is green, `PARITY.md` ships, and the failure surfaces in production as mis-merged entities.

**Recommendation.** Add a **comparator sensitivity suite** as a required CI job. For each stage, take a known-good dbt output and apply a mutant catalogue — drop a pair; add a pair; flip a `match_key` `'1'→'2'`; change one gamma by ±1; shift one `match_weight` by 2× tolerance; swap `unique_id_l`/`unique_id_r` on one pair; merge two clusters; split one cluster; relabel one component; coerce `match_key` to INT; inject one NULL key — and assert the comparator **fails** on every mutant, with the expected localisation string. Fail CI if any mutant survives. One day of work, and the cheapest credibility available. Add the flake taxonomy and distinct exit codes it needs (see M7, M12).

---

### M11 — Four new models ship with no acceptance criteria, and one has no oracle strategy at all

**Severity:** MAJOR · **Attacks:** Stage 6 task line vs Stage 6 AC; S3; §2 rows `one_to_one_clustering`, `edge_metrics`

**Claim.** Stage 6's task line adds `entity_clusters_1to1`, `node_metrics`, `cluster_metrics`, `edge_metrics`; its AC covers none of them. For `edge_metrics`/`is_bridge`, S3 declares there is no SQL oracle and then stops — leaving a model in the DAG that nothing can falsify, and it is precisely the model where principle 1 claims we *improve* on Splink. `entity_clusters_1to1` carries tie semantics that are exactly the silent-divergence shape the rest of §3 is careful about.

**Evidence.** `[SRC]` `edge_metrics.py:75-143` computes `is_bridge` in igraph in Python via `edges_for_igraph.as_pandas_dataframe()` at `:121`, degrading silently to `_basic_edge_metrics_sql` when igraph is absent (`:28-51`, `graph_metrics.py:239-254`). `[SRC]` `one_to_one_clustering.py:184-197` (per-cluster `contains_<sd>` flags), `:217-241` (`not ((l.contains_A and r.contains_A) or …)`), `:229-251` (`rank() over (partition by l.representative order by match_probability desc, r.node_id)` on both sides, mutual rank-1 only), `:14-100` (`ties_method='drop'`). `[RECON]` the failure this model exists to prevent, reproduced: nodes `mdm-1`, `mdm-2` (a de-duplicated master) and `crm-9` with edges `(crm-9,mdm-1,0.97)` and `(crm-9,mdm-2,0.93)` → plain CC at threshold 0.9 returns a **single cluster** merging two records the source guarantees are different people.

**Failure scenario.** Four models ship untested. A 1:1 tie mis-handled over-merges master records — the most damaging ER error, because master records are what other systems key on — and Stage 6's only gate (partition parity against `cluster_pairwise_predictions_at_threshold`) faithfully reproduces the *wrong algorithm* and passes.

**Recommendation.** Per-model AC, with the oracle chosen per model rather than defaulting to Splink:
- `is_bridge`: **Python union-find reference oracle** — an edge is a bridge iff removing it increases the component count. Exact, ten lines, no optional dependency, strictly better than Splink's igraph path. Say in S3 that this is the plan, not that there is no oracle.
- `entity_clusters_1to1`: differential test against `cluster_using_single_best_links` on fixtures that **force ties**, plus a dedicated `ties_method='drop'` fixture.
- `node_metrics`/`cluster_metrics`: exact parity against `compute_graph_metrics` (pure SQL both sides — §3.6 already has the formulas).
- Add the compile-time error: if `link_type` is `link_only`/`link_and_dedupe` and only CC is implemented, fail. Add the adversarial fixture above.
- Add an absolute Stage-6 runtime gate alongside the ratio: `entity_clusters` wall time ≤ `var('er_cluster_budget_s')` on the 1M fixture. A ratio-only criterion has no failure mode in absolute time and — because the O(diameter) pathology is *shared* — cannot fire on the deep-component case at all (on a 20k chain our ratio to Splink actually *improves*).

---

### M12 — Stage 10 measures quality but does not gate it, and the frozen fixture model is a bad model: measured F1 = 0.72, blocking recall = 0.51

**Severity:** MAJOR · **Attacks:** Stage 10 AC, Stage 3 AC, Stage 0.4, §6.4, §8 DoD 5

**Claim.** Promoting evaluation to Stage 10 is the right correction, but it is a *reporting* stage with a parity AC ("confusion-matrix parity against `accuracy_analysis_from_labels_table`") and no quality floor. The package can therefore be 100% parity-green on a configuration that is badly wrong — and the frozen reference model in Stage 0.4 **is** such a configuration.

**Evidence.** `[RECON]` splink 4.0.16 + DuckDB 1.5.5, `fake_1000` (which carries a ground-truth `cluster` column), trained with Splink's own demo settings — the exact shape Stage 0.4 freezes as `fake_1000_v1.json` (`block_on(first_name)`, `block_on(surname)`):

| threshold | pairwise precision | recall | F1 |
|---|---|---|---|
| 0.5 | 0.9964 | 0.5661 | **0.7220** |
| 0.9 | 1.0000 | 0.5550 | 0.7138 |
| 0.99 | 1.0000 | 0.5213 | 0.6854 |

2,975 true pairs; at 0.9 the system finds 1,651 and misses 1,324. **Blocking recall is the binding constraint:** the two-rule set generates 4,821 pairs covering 1,521/2,975 true pairs = **0.5113**. Adding `block_on(dob)` → 0.8061; adding `block_on(email)` too → **0.9173** for 6,042 pairs (1.2% of the 499,500 cartesian), which lifts end-to-end F1 to **0.9809** — a +0.26 F1 improvement completely invisible to every gate in §6.4. Threshold sensitivity on the better model: F1 peaks at 0.9809 (t=0.5) and falls to 0.9219 at the DoD's own `er_threshold: 0.9`, costing ~330 true pairs for zero precision benefit.

Cluster-level amplification, same corpus: `[RECON]` at threshold 0.001 edge precision reads a healthy 0.9764 while **cluster** precision is 0.7495 — a 14.8× false-positive amplification, with max cluster size drifting 10 → 32 against a true maximum of 10. Splink ships the whole unused surface: `[SRC] accuracy.py:253-288` (precision/recall/f1/f2/f0_5/p4/phi, pure SQL), `:293-309` `_select_found_by_blocking_rules`, `graph_metrics.py:28-113, 257-315`.

**Failure scenario.** v1 ships, `PARITY.md` truthfully states "identical pairs, gammas, and clusters," and the product misses ~45% of the duplicates in its own reference dataset. Because the document frames Splink as the oracle and divergence as the only failure mode, nobody is assigned to notice; the first person to discover it is a customer reconciling duplicate counts.

**Recommendation.**
1. **Move evaluation earlier and make it gate.** It cannot be Stage 10 if it must gate Stage 3 (blocking recall) and Stage 6 (cluster quality). See §A.5.
2. **Stage 3 gains a second-sided guardrail:** `blocking_recall = |generated ∩ true| / |true|` per rule and cumulative, with `er_blocking_recall_floor` failing below, alongside the existing pair budget failing above. Report the recall/cost frontier and per-rule *marginal* recall so a rule adding pairs but no true pairs is visibly dead weight. Splink's `cumulative_comparisons_to_be_scored_from_blocking_rules_data` already supplies the per-`match_key` denominators.
3. **Stage 6 gains hard cluster-quality tests** (not warnings): max cluster size ≤ `er_max_cluster_size`; count of oversized clusters; cluster-size distribution snapshotted per run and diffed. `node_metrics`/`cluster_metrics` are already in the task list — wire them to gates.
4. **Every fixture carries a ground-truth label column** (the generator already knows it), and Stage 0.3 dumps the truth-space table, prediction errors (top-N FP/FN by weight — the most useful debugging artefact in the harness), and unlinkables. Extend Stage 0's AC to "full stage-by-stage golden set **and** the quality baseline."
5. **Commit `er_threshold` with a justification** (target metric, fixture, measured P/R/F1 at that point) and fail the harness if the configured threshold is not on the committed curve. Replace the single knob with `er_threshold_auto_merge` / `er_threshold_review_low`: a single threshold cannot express "uncertain" and forces every borderline pair into a silent wrong answer.
6. **Fix Stage 0.4's frozen model** or add a second one with adequate blocking, and state in `PARITY.md` both the parity claim and the measured quality per fixture.

---

### M13 — The frozen model library is one sentence, and routinely-trained models violate Stage 1's own validator

**Severity:** MAJOR · **Attacks:** Stage 11 ("frozen library of model JSONs"), Stage 1 AC

**Claim.** The frozen-library correction is right and leaves three holes: it never says library JSONs must be produced by **save→reload** (§3.4 proves this is mandatory); it never says what the library must **span**; and Stage 9 is a model-varying activity by definition, so the un-attributability the fix removes reappears in training with no mitigation. Empirically, ordinary trained models will not even load under Stage 1's AC as stated.

**Evidence.** `[RECON]` a routine 400-row seeded training saved a JSON with **3 of 14 non-null levels missing `m_probability`** (`first_name` idx 5, `surname` idx 3 and 4) and **2 missing `u_probability`**. Mechanism at `[SRC] comparison_level.py:654-658` — the truthiness guard `if self._m_probability and self._m_is_trained` drops `0.0` and not-observed. §3.4 documents that reload substitutes `_default_m_values`. Stage 1's AC says "malformed JSON fails compilation" *and* "a level with `m_probability` absent renders `_default_m_values`" — the tension is unresolved. `[RECON]` the same production model has **3 levels with `m_probability == 1.0` exactly**, which a naive "probabilities in (0,1)" open-interval check rejects.

**Failure scenario.** The nightly model-varying job reds on `dbt compile` because the validator rejects a perfectly ordinary Splink artefact; someone "fixes" it by weakening the validator, losing the guard on genuinely malformed input. Meanwhile the unspecified library fills with three near-identical `fake_1000` variants exercising none of the shapes §3.4 warns about.

**Recommendation.** Specify the library as a **matrix with one stated cell per row**, every entry generated by save→reload, hashed, committed, with a regeneration script and a review gate: `{≥1 level absent m}`, `{a level with m = 0.0}`, `{a level with u = 0.0 → infinity sentinel}`, `{a not-observed level}`, `{TF on an exact level}`, `{TF on a fuzzy level with disable_tf_exact_match_detection}`, `{tf_minimum_u_value ≠ 0}`, `{null level not first}`, `{no ELSE level}`, `{≥11 blocking rules}`, `{the incumbent's real model_test_v1.json}`. Resolve the Stage 1 tension explicitly: **absent `m`/`u` is valid input** that renders `_default_m_values`; `m`/`u` ∈ **[0,1] closed**; the validator hard-**errors** only on structural malformation (missing `sql_condition`, non-duckdb `sql_dialect`, duplicate `output_column_name` post-normalisation) and on `m == 0` or `u == 0`, which produce `bf = 0` / `+inf` and cannot be made parity-safe in log space.

---

### M14 — Stage 4's baseline may contain no gamma columns, and three structural level variants are untested

**Severity:** MODERATE (high-rank: it makes Stage 4 vacuous) · **Attacks:** Stage 0.3, Stage 4 AC, §3.3

**Claim.** A wrong gamma numbering is **self-consistent** — the gamma `CASE` and the `bf` `CASE` come from the same macro and the same counter, so `WHEN gamma_x = <wrong k> THEN <that level's bf>` still selects the right Bayes factor. Match weight, probability, edges, clusters and golden records are all *correct* under a wrong numbering. The only observable is the gamma column, so Stage 4's gamma equality is the **sole** gate — and Splink emits gamma columns from `predict()` only when `retain_matching_columns=True`, which Stage 0.3 does not require (Stage 5 requires only `retain_intermediate_calculation_columns`).

**Evidence.** `[SRC]` `settings.py:391-450`, `comparison.py:266-292` — gammas are projected only under `retain_matching_columns` (or `training_mode`); `bf_` columns only under `retain_intermediate_calculation_columns`. `[RECON]` with both flags false — the `SettingsCreator` default — `predict()` emits exactly `match_weight, match_probability, "unique_id_l", "unique_id_r", match_key`. The incumbent's committed model has `retain_matching_columns: True` but `retain_intermediate_calculation_columns: False`. `[RECON]` structural variants Splink accepts and Stage 4 does not test: a null level in position 3 renders `… WHEN a_l IS NULL OR a_r IS NULL THEN -1 ELSE 0 END` and, with a NULL-tolerant level above it, gives gamma **2** where null-first gives **−1**; a comparison with **no ELSE** renders a `CASE` with no `ELSE`, so non-matching pairs get gamma NULL → `bf` NULL → `match_weight` NULL, and the pair is silently dropped by `WHERE match_probability >= t`; a comparison with **no null level** only logs a warning (`settings.py:262-274`) and NULL inputs fall through to gamma 0 (a *disagreement* weight) rather than −1 (weight 0).

**Recommendation.** Make Stage 0.3 normative: the baseline Linker is constructed with **both** `retain_matching_columns=True` **and** `retain_intermediate_calculation_columns=True` (this changes only the projection — the `bf` columns are computed in `__splink__df_match_weight_parts` regardless), and the harness fails the baseline build if the parquet lacks one `gamma_<name>` and one `bf_<name>` per comparison. Add four negative unit tests for the wrong-numbering modes (ascending in list order; by list index including nulls; seeded at `len(levels)-1` instead of the non-null count; null hoisted to position 0) and three fixtures for the structural variants above. Note that on the predict path `__splink__df_comparison_vectors` **does not exist as a table** — the Stage-4 baseline must come from `compute_comparison_vector_values_from_id_pairs_sqls` invoked separately or from a `debug_mode` run.

---

### M15 — Determinism has three unstated preconditions, and "total ordering everywhere" costs 4.2×

**Severity:** MODERATE · **Attacks:** §1.4 principle 3, §6.3

**Claim.** §6.3 correctly retires byte-identical parquet, but every operative term in the replacement is undefined, principle 3 still says "total ordering everywhere" with no price, and one class of output is genuinely non-deterministic above one thread.

**Evidence.**
- `[RECON]` `SET threads=8`, 3,000,000 DOUBLEs, 6 repeat queries on the same table in the same connection: **5 distinct `sum()` results** and **6 distinct `avg()` results**; at `threads=1`, one of each. So any float aggregate the harness computes (drift totals, checksums, density, benchmark summaries) varies run to run.
- `[RECON]` a total `ORDER BY` in the CTAS on 5,570,104 rows × 17 columns costs **7.18 s vs 1.69 s (4.2×)**. At five materialised models on the 1M corpus that is +27 s against a measured 21.1 s for the entire stages-3-5 arithmetic — sorting would cost more than scoring. It is also unnecessary: `[RECON]` D4's clustering output produced one identical SHA-256 of the **unordered** physical row sequence across 3 runs at `threads=1` and 3 at `threads=8` on a 20k-node graph.
- `[RECON]` `set()`/`set_strict()` are in the dbt Jinja context (`[SRC] dbt/context/base.py:503-541`) and return real Python sets, whose string iteration order is randomised **per process** (`PYTHONHASHSEED` unset): four interpreters rendering the same 5-element set of gamma names produced four different orders. One `set()` in a macro makes Stage 1's snapshot test fail ~80% of the time.

**Recommendation.**
1. Split principle 3 into **(a) tie-break determinism** — every window function, `ARG_MAX` and `min()`-style selection has a total ordering in its `ORDER BY` (this is where D10's trailing `unique_id` belongs, and it is free) — and **(b) output stability**, asserted by the harness at verification time with **no `ORDER BY` in model bodies**.
2. Make §6.3 executable: `content_hash(model) = sha256` over rows sorted by a **declared primary key per model** (state it in schema.yml), with a **named volatile-exclusion list** (`__splink_salt`, `er_run_id`, `_loaded_at`), doubles hashed as their **8 IEEE-754 bytes** (not text — `printf('%.17g')` and DuckDB's shortest-round-trip default disagree on the same double), and the sorted column-name+type list included in the digest.
3. Determinism gates hash the sorted **relation**, never a float aggregate; drift reports quote max/mean over sorted rows and are advisory. Run the gate at `threads=8`, never `threads=1` — `[RECON]` a single-threaded check also passed the *wrong* `USING KEY` formulation, which was deterministic and wrong.
4. Add a static lint banning `set(`/`set_strict(`, `invocation_id`, `run_started_at`, `thread_id` and model-level `env_var` from anything reaching `compiled_code`; pin snapshots to a single `mem` target so `this` renders constant. State that `dbt compile` does **not** evaluate contracts, so snapshots prove SQL generation only.

---

### M16 — Thresholds are a build-time var but the AC requires three simultaneously

**Severity:** MODERATE · **Attacks:** Stage 6 AC ({0.5, 0.9, 0.99}), `int_edges`

**Claim.** `int_edges` filters at `var('er_threshold')` and `entity_clusters` consumes it, so one build yields one partition. Label parity at three thresholds therefore requires three sequential builds of the slowest model in the DAG, and no cross-threshold property can ever be a dbt test.

**Evidence.** `[RECON]` a single `USING KEY` model with a **composite key** produces all three partitions in one statement: `cc(thr, unique_id, entity_id) using key (thr, unique_id)`, seeded `select t.thr, n.unique_id, n.unique_id from stg_input n cross join thresholds t`, with the recursive term joining on `b.thr = c.thr` and `cur.thr = c.thr` and grouping by `c.thr, b.dst, cur.entity_id`. Verified on 6 nodes / 3 edges: 0.50 → `{a-0},{a-1,a-2,a-3},{a-4,a-5}`; 0.90 → `{a-0},{a-1,a-2,a-3},{a-4},{a-5}`; 0.99 → `{a-0},{a-1,a-2},{a-3},{a-4},{a-5}` — a correct refinement chain. (Cast the thresholds relation to `DOUBLE`; DuckDB types bare decimal literals as `DECIMAL`, which changes the boundary comparison against `match_probability`.)

**Recommendation.** Make the threshold a **dimension of the model**, not a var: `edges_by_threshold(thr DOUBLE, unique_id_l, unique_id_r)` cross-joined to a `thresholds` relation, and `entity_clusters(thr, unique_id, entity_id)` in the composite-key form. Default `er_thresholds` to a single value so production cost is unchanged. The baseline comparator then joins on `thr`, and cross-threshold properties become plain singular tests.

---

### M17 — Runtime substrate is unspecified: DuckDB's cross-process lock makes the harness and dbt mutually exclusive, and two §6.4 layers have hidden preconditions

**Severity:** MODERATE · **Attacks:** §6.4 layers 2 and 7, §8 DoD 2

**Claim.** The architecture has a Python harness and a dbt project both touching DuckDB and never picks a substrate; two of the seven test layers have preconditions the document does not state; and DoD 2's "zero Python at runtime" is literally false.

**Evidence.**
- `[RECON]` `duckdb.connect(p)` held in one process, then a second process connecting to the same path → `IOException: Could not set lock on file … Conflicting lock is held`. A second connect in the **same** process is fine. `[SRC]` dbt-duckdb keeps one process-wide `Environment` with one `duckdb.connect()` and hands each dbt thread a **cursor** (`connections.py:28-29, 48-68`; `environments/local.py:75-85`), so `dbt threads` and DuckDB's `settings.threads` are different knobs and must not be conflated.
- `[SRC]` dbt unit tests: the `unit` materialization runs `get_create_table_as_sql(True, temp_relation, get_empty_subquery_sql(sql))` to read back column types (`unit.sql:12-19`), and `get_fixture_sql` falls back to `adapter.get_columns_in_relation(this)`, raising "Not able to get columns for unit test … because the relation doesn't exist". Parents must already exist. docs.getdbt.com lists **"Recursive SQL"** and "Introspective queries" as unsupported for unit tests — `[RECON]` all three dbt wrapper shapes execute correctly against a `USING KEY` model on DuckDB 1.5.5 today, so this is policy, not a hard failure.
- DoD 2: dbt-core 1.12.2, dbt-duckdb 1.11.0, Jinja2 and agate are all Python. The achievable property is *no Splink, no pandas, no ER-specific Python*, which `[RECON]` is real — gamma `CASE`s were built from a real trained model JSON with no splink import and executed correctly.

**Recommendation.** (a) dbt writes to `path: ':memory:'` and models export to parquet (dbt-duckdb `external` materialization or a `COPY` post-hook); the harness reads **only parquet**, so it never opens the database — this also matches Stage 0.3's baseline format, making both sides of every comparator parquet. If a file DB is required, run the harness *inside* the dbt process via a plugin or `on-run-end` hook, never as a sibling. (b) Pin `threads: 1` in the dbt profile and set DuckDB `threads`/`memory_limit` under `settings:` from env vars, with a comment that the two must not be conflated; record the pairing in the run manifest so a mismatched pod is reported non-comparable rather than as a regression. (c) **[SUPERSEDED by D12 — 2026-08-23]** *This clause scoped unit tests to five fixed-schema, non-recursive models. Its recursion premise is withdrawn by its own `[RECON]` evidence two paragraphs up, and its JSON-derived-column premise is answered by M2's var-driven `columns:` mechanism; D12 raises the scope to every model, keeps the harness as an additional layer rather than a substitute, and retains the `--empty` precondition below. The original text stands for the record:* keep dbt unit tests for fixed-schema, non-recursive models (`stg_input`, `tf_all`, `int_edges`, `golden_records`, `cluster_membership`); move gamma/bf/clustering logic tests into the pytest harness, which can run compiled SQL against hand-built relations with none of these constraints; add `dbt run --empty --select <parents>` before unit tests to satisfy the existence precondition cheaply. (d) Rewrite DoD 2 as three checkable claims: the runtime dependency set is exactly `{dbt-core, dbt-duckdb, duckdb}` (enforced by a clean-venv CI job); `dbt compile` output has zero Jinja residue; the JSON is consumed as text and never requires Splink to interpret it — plus the negative test that a non-`duckdb` `sql_dialect` fails compilation.

---

### M18 — "Ten green nightly differential runs" is an uncompressible ≥10-day tail, and a failure is not reproducible

**Severity:** MODERATE · **Attacks:** §8 DoD 3, Stage 11 ("failures freeze into `fixtures/regressions/`")

**Claim.** DoD 3 is a serial *calendar* gate on a project whose other items are content gates; it cannot be compressed with compute, and it resets on any failure including infra flakes, for which there is no policy. Separately, the freeze target is undefined: under D1 the compiled SQL depends on a runtime `--vars` payload and is not recoverable from the repo, and under B5 re-running does not regenerate the same oracle.

**Evidence.** `[DERIVED]` expected nights to first 10-in-a-row, `E(p,n) = (1-p^n)/(p^n(1-p))`: p=0.99 → 10.6; 0.95 → 13.4; 0.90 → 18.7; 0.85 → 27.2; 0.80 → 41.6. `[SRC]` `dbt/compilation.py:797-808` — `_write_node` writes `compiled_code` to `target/compiled/…`, the only place the executed SQL exists, and it is not a repo artefact.

**Recommendation.** Split DoD 3 into **correctness** (on demand, parallel: 500 seed-runs across ≥3 frozen model JSONs × ≥3 size regimes × ≥3 dirtiness regimes with zero parity failures — a strictly stronger statement about the input space, runnable in an afternoon) and **stability** (14 calendar days with no *parity* failure, running concurrently rather than gating, with infra failures classified separately and not resetting the counter). Define a **failure bundle** schema emitted automatically on any red stage: the exact `--vars` payload bytes (JSON inline, not a path), `sha256(input)` plus the input, the baseline parquet set and manifest, `target/compiled/**` for the failing model and ancestors, the comparator's worst-N localisation output with both sides, resolved versions, and the run id. Add the test that makes it real: **a CI job that reproduces the same red/green verdict on a clean runner from the bundle alone.** Name an owner and a triage SLA.

---

### M19 — Survivorship is single-strategy-per-attribute, drops multi-valued attributes, and has no unmergeable-conflict path

**Severity:** MODERATE · **Attacks:** D10, Stage 7

**Claim.** D10's addition of the multi-column-attribute rule fixes the worst incoherence, but three gaps remain that make the "differentiator" weaker than what the incumbent already specifies.

**Evidence.** `[RECON]` the incumbent uses ordered rule **chains** per attribute drawn from five rules (`source_priority`, `recency`, `frequency`, `completeness`, `validated`) with a **mandatory terminal `record_key ASC`**, plus config validators V2–V5 (one of which requires every chain to contain a rule able to separate two records from the *same* source), a separate `golden_lineage` relation, and an explicit composite-address rule. D10 has one strategy plus a tiebreak, one lineage pair of columns, and no validators. Independently: a single-valued output silently **discards** a person's second email or address — data loss presented as resolution — and when a chain does not separate the top candidates before the `unique_id` tiebreak, the conflict is coin-flipped rather than escalated.

**Recommendation.** (a) Ordered **chains**, not single strategies; keep `record_key`/`unique_id` as a mandatory terminal element and set `__rule_applied = 'tiebreak_deterministic'` when it decides. (b) Add config validators: chain rules must be known names; `validated` requires an `<attr>_valid` column; every chain must contain a rule that can separate two records from one source. (c) Emit `golden_record_attributes(entity_id, attribute, value, rank, source_record)` alongside the flat table so secondary values are retained. (d) Emit unmergeable conflicts to a conflicts relation rather than coin-flipping. (e) Replace Stage 7's per-column property ("every surviving value exists in some member record" — which passes on an incoherent composite) with a **per-field-group** property: every field group's values jointly originate from a single member record. (f) State whether temporal validity (`valid_from`/`valid_to`) is supported or an explicit non-goal; it is currently neither.

---

### M20 — Human-in-the-loop is scoped out, but forced-unlink is structurally incompatible with the clustering model, which is an *interface* constraint the engine must satisfy

**Severity:** MODERATE · **Attacks:** §1.3, Stage 6, Stage 8

**Claim.** §1.3 legitimately scopes stewardship out. But scoping it out does not make the architecture compatible with it, and the incompatibility is one-directional: forced-**link** is trivial (inject an edge at p=1.0); forced-**unlink** cannot be expressed as an edge deletion, because deleting `(a,c)` does not separate `a` from `c` if `a–b–c` survives. Honouring it requires an iterate-cut-recluster fixpoint wrapped **around** the clustering model — a second loop with no place in the current design.

**Evidence.** `[RECON]` DuckDB 1.5.5: nodes `a,b,c` with edges `(a,b),(b,c),(a,c)` → one cluster. Deleting the direct `(a,c)` edge and re-running the identical `USING KEY` query → **still one cluster** `{a,b,c}`. `[RECON]` the incumbent specifies the resolution as shortest-path minimum-probability edge cutting to a bounded fixpoint with a protected-edge floor, escalating unsatisfiable cases to review, plus a persisted `cut_edges` relation excluded from every later run — and a pre-clustering contradiction check that fails the run when a `never` pair sits inside one component of the `always` edges.

**Recommendation.** Do not build stewardship, but **make the engine interfaceable**: (a) accept an optional `er_assertions(record_a, record_b, kind ∈ {always, never}, active)` **input** relation and an optional `er_cut_edges` input, both entering the DAG **upstream of `int_edges`** — this is the key architectural point, because overrides applied downstream do not survive a full refresh; (b) union `always` edges at p=1.0 and anti-join `cut_edges` inside `int_edges`; (c) state in §1.3 that partition-level `never` resolution (the outer loop) is out of scope for v1 and belongs to the surrounding platform, with the reason; (d) add the AC that a supplied assertion survives a full refresh. Cost: two optional refs and a where clause. Benefit: the platform can adopt the engine without redesigning it later.

---

### M21 — No effort sizing, no critical path, and v2 roughly doubled the scope

**Severity:** MODERATE · **Attacks:** §5 (the stage list has no sizing at all)

**Claim.** v2 promoted training to Stage 9 (four models incl. EM in SQL) and evaluation to Stage 10 (five models) and added `entity_clusters_1to1`, `node_metrics`, `cluster_metrics`, `edge_metrics`, `int_deterministic_links`, `compare_two_records` and two `diag_*` models — ~12 additional models and one research-grade algorithm — with zero sizing anywhere. Three items are **spikes with real failure probability**, not tasks, and they carry the same visual weight as `int_edges` (one `WHERE` clause). The true critical path is also misidentified by omission.

**Evidence.** The spikes: (i) D5's EM to 1e-4 against an oracle B5 shows is non-reproducible; (ii) §3.2's TF exact-match-level resolution, whose mitigation ("restrict to the resolvable shapes and fail compilation") shrinks the supported model space by an **unmeasured** amount — `[RECON]` Splink's `_is_exact_match` matches `"name_r" = "name_l"` and `NOT ("name_l" <> "name_r")` but **not** `"name_l" = "name_r" AND 1=1`, and skips `a_l=a_r AND b_l=b_r` because `len(colnames) != 1`; (iii) `is_bridge` in SQL, i.e. biconnected components, with no oracle (M11). The critical path is **Stage 1**, because `load_model_json` owns five recomputed values (D1) and every downstream *baseline* is only meaningful once that reader is right.

**Recommendation.** Attach a size class and owner to every stage and separate spikes from delivery. **UNVERIFIED** (reasoning from verified complexity, not measurement): *Days* — Stage 0 scaffolding, Stage 2, `int_edges`, `train_m_from_labels` (one GROUP BY), Stage 10 aggregates, `node_metrics`/`cluster_metrics`, `int_deterministic_links`. *Weeks* — Stage 1 (five recomputed fields), Stage 3 (four WHERE arms, the `coalesce` chain, exploding rules as CTEs, VARCHAR `match_key`), Stage 5 (linear product, clamp, TF adjustment, degenerate params), Stage 11 + harness (comparators, generator, coverage, failure bundles, sensitivity suite). *Multi-week spike with a written kill criterion* — D5 EM in SQL, `is_bridge`, the guardrail. Declare the critical path **1 → 3 → 4 → 5**, and state explicitly that with M4's per-model injection in place **Stages 6, 7 and 10 are parallelisable from day one** — that decoupling is the single largest schedule lever in the document and it is currently buried in a six-line paragraph.

---

## A.2 What Splink does that cannot be pure static SQL

Six items, with the honest scope statement they imply. Everything **not** on this list was verified reachable.

| # | Not expressible as static SQL | Why | Disposition |
|---|---|---|---|
| C1 | **`is_bridge`** | `[SRC] edge_metrics.py:75-143` computes it in igraph, in Python, pulling the whole edge list to pandas at `:121`; degrades to no metrics without igraph (`:28-51`). Biconnected-component decomposition has no min-label / `USING KEY` formulation. | **Replacement, not reproduction.** Verify against a Python union-find oracle (edge is a bridge iff removal increases component count). Declare in `PARITY.md`. |
| C2 | **TF exact-match-level resolution** | `[SRC] comparison_level.py:30-41, 502-563` parses `sql_condition` with sqlglot, normalises to CNF via `simplify(normalize(tree))`, splits top-level ANDs and compares tree signatures. Jinja can only string-match. `[RECON]` divergences: `"name_r" = "name_l"` → True; `NOT ("name_l" <> "name_r")` → True; `"name_l" = "name_r" AND 1=1` → **False**; `lower(a_l)=lower(a_r)` → False. | **Compile-time sidecar** (see below), not a Jinja approximation. |
| C3 | **Backend `link_type` selection** | `[SRC] inference.py:227-246` rewrites `link_only` → `two_dataset_link_only` when `len(input_tables_dict) == 2`. `[RECON]` `source_dataset_column_name` is always `"source_dataset"` in the JSON even for `dedupe_only`, while the runtime `source_dataset_input_column` is `None`. | Explicit compile-time vars `er_backend_link_type`, `er_has_source_dataset`. |
| C4 | **`two_dataset_link_only` l/r orientation** | `[SRC] vertically_concatenate.py:293-294` — `min(df_obj.templated_name)` over input-table aliases. Not in the JSON. | Explicit `er_left_table` var, or refuse the configuration (Open Question 3). |
| C5 | **Variable numbers of models** | `[SRC] dbt/parser/read_files.py:151-175` — filesystem search, one node per file; `PluginNodes.add_model` takes `ModelNodeArgs` with **no `raw_code`**. | D7 already fixes TF. **Exploding rules need the same treatment**: `[SRC] blocking.py:518-614` materialises one `__splink__marginal_exploded_ids_blocking_rule_mk_<k>` table per rule and switches the terminal projection to `min(match_key) GROUP BY join_key_l, join_key_r`. Emit the marginals as **CTEs in one model** (`unnest` composes in a CTE) and switch the terminal shape when `arrays_to_explode` appears. Out of scope for v1 per B3. |
| C6 | **Recursion depth / statement bounds** | `[RUN]` 2026-08-23 on `duckdb==1.5.5`: `duckdb_settings()` returns **0 rows** for `%recur%`, `%timeout%` **and** `%interrupt%`. No depth cap, **and no statement timeout** (G13). | Iteration counter in the `USING KEY` state (M5) — **the only guardrail**. `memory_limit` is the backstop and fails hard rather than timing out, which matches D4a's finding that `USING KEY` OOMs rather than degrading. |

**Sidecar (the fix for C2, C3, C4 that keeps runtime Python-free).** Amend principle 4 to: *"The model JSON plus a small, explicitly-versioned compile-time sidecar is the contract."* The sidecar is a generated, committed, hashed artefact produced by a Python preprocessing step that imports Splink and emits, per level: the resolved `comparison_vector_value`, the resolved `m`/`u` after Splink's own defaulting, the resolved `tf_u_exact_match` (or null), plus `er_backend_link_type`, `er_has_source_dataset` and `er_left_table`. Guard it with a byte-equality regeneration test — the same generated-file-with-parity-test pattern the incumbent already uses for `models/sources.yml`. This keeps `dbt build` free of Splink at **runtime** (DoD 2 as corrected in M17) while making the two algorithms Jinja cannot express *exact* rather than approximated. The gamma counter and `_default_m_values` are portable arithmetic and stay in Jinja.

**The sidecar is also the enforcement point for §1.5, and that is not optional (DR-17).** This paragraph originally described the sidecar as a convenience that resolves what Jinja cannot compute — which is why G3 could observe that *"the document never says a JSON must pass through it, only that its outputs are used."* It says so now: **a model JSON that has not passed the sidecar has no `er_model_sha`, and a build with no `er_model_sha` fails.** The sidecar already parses every `sql_condition` with sqlglot for C2, so validating against the parse tree costs one pass it is already making — the allow-list check, the non-determinism rejection, the structural rejection and the size bounds all ride on the tree it already holds.

**Honest scope statement for `PARITY.md`:**

> For a model trained in Splink 4.0.16 with `link_type = dedupe_only`, plain equi-join blocking rules (no `arrays_to_explode`), and every comparison level carrying `m, u ∈ (0,1)`, `dbt-er` produces **byte-identical** candidate pairs and `match_key` (as VARCHAR), **identical** gamma vectors, **identical** connected-component labels, **identical** edge-set membership at every tested threshold, and match weights within `1e-9 + 1e-12·|mw|`.
>
> Explicitly excluded, with the code reference that makes each out of scope: `two_dataset_link_only` l/r orientation (C4); `arrays_to_explode` (C5, v2); `link_only`/`link_and_dedupe` (C3, v2); `is_bridge` (C1, replacement not reproduction); the `min(match_key)` VARCHAR bug, which is **deliberately replicated** (S4).
>
> `salting_partitions` is **ignored by design** — the pair set and `match_key`s are provably invariant under salting (`[RECON]` 3,281 pairs, salted == unsalted including `match_key`, stable across runs). `__splink_salt` is excluded from all concat-level comparison (S1).

---

## A.3 Capabilities in the existing `entity-resolution-engine` that this document omits

Classified by what each implies for **this** package, which is more useful than a flat list. The incumbent is 66/104 tickets done; notably, **clustering and the golden layer are the unbuilt half in both repos**, which is what makes "finish it in dbt instead of Python" coherent.

**Group 1 — Must be interfaced by this engine, even though the capability itself is out of scope (cheap now, expensive later).**

| Capability | Incumbent spec | What this package must expose |
|---|---|---|
| Frozen term frequency | `tf_lookup(model_version, tf_snapshot_id, column_name, value, tf_value)`; `register_term_frequency_lookup` only, never `compute_tf_table`; missing rows = exit 3 | `tf_all` sourced from a snapshot relation (B3) |
| Steward assertions / cut edges | `assertions` (always/never, `never` dominates, write-time conflict rejection, retraction); `cut_edges` excluded from every later run | Optional `er_assertions` / `er_cut_edges` **input** relations upstream of `int_edges` (M20) |
| Gray band | half-open `review_low ≤ p < auto_merge`; gray-band pairs are **not** clustered | Two thresholds, not one (M12) |
| Model registry | `model_registry` with active/superseded, activation guard (exit 3 on mixed `model_version` above `review_low`), `config_hash`, artifact-before-row ordering | `er_model_sha` + `er_tf_snapshot_id` stamped on every scored row, with a single-value dbt test (M8) |
| Entity permanence | `INV-PERM`: overlap-matrix claimant rule, merge/split/retire, deterministic mint order, `entity_events` replay | Either rename to `component_label` or build Stage 6b (M6) |
| Evidence audit | `evidence JSON` per scored pair (gamma vector + per-comparison Bayes factors) persisted durably | `bf_<name>` / `bf_tf_adj_<name>` retained in output, not debug-only (M14) |
| Run lifecycle | `runs`/`run_stages`, snapshot **ranges** as the rollback unit, `--resume <run_id>`, 5-value exit-code taxonomy | `er_run_id` + `_er_run_manifest` (M7) |

**Group 2 — Genuinely out of scope for a matching engine; must be named in §1.3 so the platform owner knows they are theirs.**
Ingest (`raw_records` append-only version history, `content_hash`, `ingest_batches`); deletion/tombstones/`--full-refresh-keys`/resurrection; supersession (greatest `ingested_at`, ties by `ingest_batch_id DESC`); edge invalidation in place (`is_active=false`, never a second row); standardisation (8 dbt macros, nickname seed, address parser, the `name_variants` element-0 symmetry guarantee); `review_queue` and `er review resolve`; the `CONTRADICTION-1` pre-clustering check; partition-level `never` resolution; `er correct`; single-writer advisory lock; tenancy-as-namespace; DuckLake/Postgres/S3 substrate and time travel; schema evolution with `ERR_SCHEMA_BREAKING`; config-as-validated-document (14 blocks, 16 validators, `config_hash`); the CLI contract.

**Group 3 — Directly reusable; do not rebuild.**
`tests/helpers/pairs.py::splink_blocked_pairs` (a working Splink blocking oracle via `deterministic_link()` — exactly Stage 3's oracle); `tests/helpers/compare.py` (`assert_partition_equal` / `assert_ids_stable` / `assert_golden_equal`); the fixture format (phases base/batch/refresh/resurrect with per-phase expected CSVs); `fixtures/static/base_10` with 8 designed traps and machine-checked ground truth (23 records / 10 personas / 18 true pairs — a better adversarial suite than three synthetic seeds); the seeded generator; `benchmarks/scales.yaml`'s resource envelope and its `NON_COMPARABLE` rule (adopt rather than invent edge-count benchmarks with no stated pod size); `scripts/gates.sh`'s content-hashed gate cache, JSON receipts, and suppression-pattern hygiene gate.

One caution: the incumbent's `S4.0b` contract deliberately keeps every `__splink__` relation **out of the lake** and asserts none exist. This document's harness reads Splink intermediates as oracles — that code is genuinely new and has no precedent to inherit.

**One live divergence already in the incumbent, ready-made as a regression fixture:** `dbt/macros/blocking/int_blocking_keys_union.sql` emits `where {expr} is not null and {expr} <> ''`. Splink applies no such filter and DuckDB treats `'' = ''` as true, so two empty-string keys **do** form a Splink pair. The `is not null` half is a correct no-op (`||` propagates NULL); the `<> ''` half is a real one-directional divergence. D2 already names this — keep the fixture.

---

## A.4 Corrected tolerance policy

§6.1's correction of v1's inconsistent pair is right and its derivation is right. Three additions make it complete and executable.

**The math, restated and verified.** With `p = 2^mw/(1+2^mw)`, `dp/dmw = ln2 · p(1−p)`, maximised at `p = 0.5` where it is `0.1732868`. `[RUN]`:

| mw | p | `dp/dmw` | derived `Δp` at `Δmw = 1e-9` |
|---|---|---|---|
| 0 | 0.5000000000 | 1.732868e-01 | 1.733e-10 |
| 3 | 0.8888888889 | 6.845898e-02 | 6.846e-11 |
| 7 | 0.9922480620 | 5.331581e-03 | 5.332e-12 |
| 10 | 0.9990243902 | 6.755814e-04 | 6.756e-13 |

For the record, v1's crossover — the `|mw|` below which its `1e-8` probability gate was strictly tighter than its `1e-6` weight gate — was `±6.072531581076437`, and the worst-case ratio at `mw = 0` was `17.3287`.

**Addition 1 — probability parity is vacuous above `mw = 54`.** `[RUN]` `2**54/(1+2**54) == 1.0` exactly in float64 (`1-p = 0.0`); at `mw = 53`, `1-p = 1.11e-16`. So *any* absolute probability tolerance passes for free in exactly the region where Splink's `[1e-300,1e300]` clamp, its `'Infinity'` sentinel and its degenerate-`m`/`u` behaviour diverge most violently. State this: **for `|mw| > 54`, assert `p == 1.0` (or `1-p == 0.0`) exactly, and record that probability parity carries no information there.**

**Addition 2 — the gate that actually protects the output is the decision, not the value.** Neither tolerance protects the only property that changes downstream: whether a pair lands on the same side of the threshold. Add it as a first-class row.

**Addition 3 — the permitted tolerance has five orders of unused headroom.** `[RECON]` enumerating all 2,880 gamma vectors of a real production model, max `|Δmw|` between Splink's linear-product-then-log2 and a sum-of-logs reimplementation was **2.842e-14**. So `1e-9` is generous; the smallest *semantic* bug in this document's finding set is `0.1665` bits (the TF denominator), i.e. `1.7e13` times the gate. The tolerance is not where risk lives — the edge decision is.

**Corrected §6.1 table:**

| artefact | gate |
|---|---|
| pair sets, `match_key` (as VARCHAR), gammas, TF tables | **exact** after canonical ordering |
| `match_weight` | **exact bit equality** expected; `1e-9 + 1e-12·|mw|` permitted **with a divergence-log entry** |
| `match_probability` | **derived**, never asserted independently: `≤ ln2 · p(1−p) · Δmw + 4·2⁻⁵³`. For `|mw| > 54`, assert `p == 1.0` exactly. |
| **edge-set membership** *(new, and the binding gate)* | **exact boolean agreement**: for every threshold `t`, `(p_dbt >= t) == (p_splink >= t)` for every pair. Both sides use `match_probability >= t` on the materialised column (B2). |
| clusters | **label equality** on the component label, primary; partition equality (canonical relabel + pairwise co-membership) as fallback diagnostic. D4 proves labels are identical, so the weaker gate hides real drift (M6). |
| golden records | exact |
| any float **aggregate** | **not a gate.** Non-deterministic above one thread (M15); advisory only. |

Add the standing note: *exact bit equality is the right default because both engines run float8 on the same DuckDB; where the expression tree is identical the result is identical.* **Verified across platforms 2026-08-23 (G5, closed): all five probe values are bit-identical on darwin/arm64 and linux/amd64 under DuckDB 1.5.5, `log2` included, and `harness/test_float_parity.py` asserts it on both platforms every run.** Tolerance is for where it provably cannot be — which, after §3.1's linear-space rule, is almost nowhere.

---

## A.5 Corrected stage list — **absorbed into §5 on 2026-08-23; retained as evidence**

> **This table is no longer an inventory.** Every row below has been merged into **§5**, which is now the
> single stage list, and §5 is normative. DR-11 is closed and R3 is discharged. The table is kept because
> §B.5 point 1 makes absence claims verifiable by grep and because the *reasoning* in the "Why" column is
> the evidence §5's decisions rest on — deleting it would remove the justification along with the
> duplication. **If this table and §5 ever disagree, §5 is right and this one is stale.**
>
> Two rows resolved differently from how they are worded here, and both are recorded at the point of
> change in §5: **Stage 2b's** either/or is closed as the explicit non-goal, with `is_incremental()` and
> the record-lifecycle machinery moving to v2 together (see §5 Stage 8); and **Stage 4's** relaxation to
> reachable threshold constants — the one *direct* textual conflict rather than an omission — is adopted.

**Principle:** two of v2's stages are in the wrong position (evaluation must gate stages it currently follows), one is missing entirely (cutover), and the critical path is not stated.

| Stage | Change | Why |
|---|---|---|
| **0** — Scaffolding, fixtures, oracle, spikes | **Extend.** Add **0.6 Materialisation & capacity spike**: measure B/pair for the fixture model, publish `er_max_pairs`, ~~decide `ephemeral` vs `table` per intermediate~~ (**[SUPERSEDED by D11] — see §B.1 G1**). Add **0.7 Comparator sensitivity suite** (M10). Make 0.3 normative on `retain_matching_columns=True` **and** `retain_intermediate_calculation_columns=True` (M14), on ground-truth labels (M12), and on **training traces** for Stage 9 (B5). Add the **frozen model library matrix** (M13). | B1 sets the DAG shape; changing it after Stage 5 is a rewrite. The baseline format must carry gammas, bfs, labels and training traces from day one — retrofitting after Stage 0.4 freezes it is the expensive path. |
| **1** — Model JSON ingestion & SQL generation | **Extend.** Add the **compile-time sidecar** (§A.2). Add lints: asymmetric-level detection (M1), `output_column_name` uniqueness post-normalisation (M2), `set()` ban (M15), `m == 0`/`u == 0` hard error (M13). Publish `er_gamma_columns`/`er_bf_columns` as vars (M2). | **This is the critical path.** Every downstream *baseline* is meaningless until the reader is right. |
| **2** — Staging & TF | **Change.** `tf_all` reads a **frozen snapshot** keyed `(model_version, tf_snapshot_id)` by default; live-corpus TF is the opt-in snapshot-minting variant. | B3. This is a contract change to a Stage-2 model; deciding it at Stage 8 means rebuilding it. |
| **2b** — Record lifecycle | **New, or an explicit non-goal.** If `is_incremental()` ships in Stage 8, this must exist: `is_deleted`/`valid_to` on `stg_input`, an `edges ⊆ nodes` referential-integrity test, and an explicit reap step. **The cheap correct v1 choice is to declare all models `table` (full rebuild) and put `is_incremental()` out of scope**, which makes deletion a non-issue by construction. What is not acceptable is Stage 8 shipping `is_incremental()` with neither. | dbt's `delete+insert` cannot remove a key the SELECT excludes — the incumbent hit this exact trap and needed a post-hook. |
| **3** — Blocking | **Extend.** Add **blocking recall** with `er_blocking_recall_floor` as a two-sided guardrail (M12). Replace the ported `max_rows_limit = 1e9` with the byte-derived budget (B1). Restrict to the supported-configuration matrix (B3). | Recall loss here is unrecoverable downstream and is currently ungated. |
| **4** — Comparison vectors | **Extend.** Add fixtures for null-level-not-first, no-ELSE-level, no-null-level (M14); relax the "boundary fixture for every threshold constant" AC to reachable constants with the unreachable ones documented. | |
| **5** — Scoring | Unchanged in substance. Enforce the single-evaluation `_bf_clamped` column (B1 rec 2). | |
| **6** — Clustering | **Extend.** `int_edges` becomes `edges_by_threshold` and `entity_clusters` becomes composite-key `(thr, unique_id, entity_id)` (M16). Replace the diameter guardrail with an **iteration cap** (M5). Add an **absolute** runtime gate alongside the ratio (M11). Add per-model ACs and a Python union-find oracle for `is_bridge` and the 1:1 tie semantics (M11). Add max-cluster-size gates (M12). | |
| **6b** — Entity identity | **New, or rename.** Either build `entity_keys` + `cluster_lineage` + `entity_events` (adopt the incumbent's `INV-PERM`), or rename the column to `component_label` and remove it as a key from downstream models. | M6. Choosing "rename" is legitimate and cheap; leaving it ambiguous is not. |
| **7** — Survivorship | **Extend.** Rule chains, field groups, multi-valued output, unmergeable-conflict path, config validators, per-field-group property test (M19). | |
| **8** — Incremental | **Change.** Two explicitly-driven joins, not the disjunctive predicate; `< 10% of full` as a measured AC (B4). `er_model_sha` + `er_tf_snapshot_id` on every row with single-value tests (M8). Optional `er_assertions`/`er_cut_edges` inputs (M20). | |
| **9** — Training | **Change the oracle.** Compare per-iteration trajectories against a **committed training trace**; require `seed`; assert cap-vs-converge explicitly (B5). | |
| **10** — Evaluation | **Move earlier and make it gate.** Split: the *measurement models* (`eval_accuracy`, `eval_errors`, `eval_unlinkables`, `diag_*`) build immediately after Stage 2 (they need only labels and scores) so their outputs can gate Stage 3's recall floor and Stage 6's quality tests; the *parity AC* against `accuracy_analysis_from_labels_table` stays where it is. | M12. A quality stage that runs after the stages it should gate cannot gate them. |
| **11** — Differential loop | **Extend.** Both-modes CI rule; per-model injection mapping; baseline↔JSON hash binding (M4). Failure-bundle schema + a bundle-reproduces CI job (M18). Split DoD 3 into parallel correctness + concurrent stability (M18). | |
| **12** — **Cutover** | **New.** Shadow run against the incumbent on production data; numeric go/no-go on edge-set symmetric difference and partition delta; documented rollback switch and its trigger. | B3. Without it, delivery is an unrehearsed swap. |
| **12b** — **Provenance & observability** | **New.** `er_run_id` stamped everywhere; `_er_run_manifest` via `on-run-end`; per-run perf artefact; named owners for `divergence-log.md` and `PARITY.md` with a CI check that every deliberate divergence has a log entry and a pinning test. | M7. Three of the document's own requirements (§6.2, §6.4 Performance, §7 Q1, DoD 4–5) currently belong to no stage. |

**Critical path:** `1 → 3 → 4 → 5`. With M4's per-model injection in place, **Stages 6, 7, 10 and 12b build in parallel from injected baselines starting on day one.** ~~Say this in §5~~ — **said in §5, 2026-08-23**, under *The critical path, and what runs beside it*.

> **[REVIEW 2026-08-23] RC15 — Two notes on this table.** **(Fixed, F5):** the Stage 0 cell previously
> read "(**decided by D11; see §B.1 G1**)" while G1 rec 2, DR-01 and §B.5 all claim the literal
> `[SUPERSEDED by D11]` marker was placed here — and §B.5 point 1 makes grep-ability the verification
> lifecycle, so the mismatched token made the claim un-greppable at one of its three sites. Normalised to
> the literal marker. **Stage 2b (Fixed, F15):** this row's either/or was half-triggered and never closed.
> It is now closed as the **explicit non-goal** — every model is `table`, `is_incremental()` is out of
> scope for v1, and the record-lifecycle machinery moves to v2 alongside it. §5 Stage 8 carries the
> decision and its three grounds; the corrected incremental design (two explicitly-driven joins, the
> measured `< 10%` AC) is recorded there as the v2 target so B4's measurement is not lost.

---

## A.6 The five open questions only you can answer

1. ~~**Is `dbt-er` an engine the existing platform calls, or a platform replacement?**~~ **RESOLVED
   (2026-08-20): an engine the existing platform calls**, as §1.3 always said — register row **DR-14**.
   The consequences are now binding, not conditional: the parity target is the edge set /
   `match_scores` rows, `entity_id` becomes `component_label`, Stages 6b/2b collapse into interface
   contracts (M6, M20), and §A.3 Group 2 is explicitly the platform's. The replacement branch — roughly
   30 capabilities from §A.3 Groups 1–2, and a rewritten Definition of Done — is closed. Appendix B is
   scoped against this answer throughout and tags every finding (`[interface]`) whose class would change
   if it were ever revisited; §B.5 lists them.

2. **What is the target record count and pod size?** B1 derives a ~2.75M-record single-node ceiling from measured constants — 4.2× below the incumbent's own committed `1m` envelope. If the target is above it, the exit path (blocked-key sharding of stages 3–5, or a distributed engine) is a v1 design decision, not a v2 discovery. Adopt `benchmarks/scales.yaml` rather than inventing edge-count benchmarks with no stated pod size.

3. **Is the single-statement clustering property worth 3.4–18.5×?** (v2's own Open Question 2, restated with the consequence.) If **no** at your scale, Stage 6's product is dbt-driven materialised iterations and the recursive CTE becomes the reference oracle the fast path is tested against — a different amount of work that must be scheduled before Stage 6 starts, not after.

4. ~~**Frozen TF or live TF as the default?**~~ **RESOLVED (2026-08-20): frozen by snapshot** — D7a;
   register row **DR-03** records "§A.6 Q4 answered". *(Original rationale, kept:)* Frozen preserves the incumbent's invariant, makes Stage 8's equivalence AC achievable by construction, and makes entity membership reproducible from a record's own data — at the cost of an explicit refresh operation and a snapshot relation. Live is simpler and makes every score corpus-dependent. This is a Stage-2 model contract, so it must be decided before `tf_all` is written (B3).

5. ~~**What is the quality floor, and who owns it?**~~ **RESOLVED (2026-08-23)** — §1.8, register row **DR-22**. Committed per-fixture floors set at Stage 0.4 from the fixed model; no package default threshold, because the one that was there measurably costs ~330 true pairs for zero precision benefit; ownership mechanised through `CODEOWNERS` rather than named in prose. Part (c) was answered separately by §1.7 (DR-09): two thresholds. *(Original text, kept:)* The frozen reference model in Stage 0.4 measures **F1 = 0.72 / blocking recall = 0.51** on `fake_1000`; a two-rule change lifts F1 to 0.98. Parity gates cannot see the difference. Someone must own (a) the committed per-fixture F1 and recall floors, (b) the justification for `er_threshold`, and (c) whether the gray band is two thresholds or one. Without an owner, Stage 10 is a reporting stage and the product ships at 0.72.

> **[REVIEW 2026-08-23] Fixed (F31) — RC16 is closed: the rename is decided.** DR-12 is CURRENT, §1.6
> carries the decision, and the body no longer emits `entity_id` as the shipped name — D4's listing is kept
> as executed with a note, D10 partitions by `component_label`, and Stage 6's composite key is
> `(thr, unique_id, component_label)`. Stage 6b is an interface contract, not a build, which is the
> "collapse" Q1 declared. R3's scope no longer lists 2b/6b as open stage deltas — 2b closed as the explicit
> non-goal in the DR-11 reconciliation, and 6b closes here.
>
> <details><summary>Original review note (RC16/RC17), retained</summary>
>
> **RC16/RC17 — Two entries above are out of sync with the register.**
> **Q1 (RC16):** its resolution declares the DR-14 consequences "binding, not conditional", including
> "`entity_id` becomes `component_label`" and "Stages 6b/2b collapse into interface contracts" — but DR-12
> records the rename as "OPEN — decide with DR-14" even though DR-14 is CURRENT (2026-08-20), so its
> stated trigger has fired without the row closing; the body still emits `entity_id` everywhere (D4, D10's
> `PARTITION BY entity_id`, §2, Stage 6) with no pending-rename marker; and R3 still lists 2b/6b as
> stage-inventory deltas, which is incompatible with their having collapsed into interface contracts. Pick
> one state: either the rename is decided — close DR-12, mark D4/D10/§2, drop 2b/6b from R3's scope — or
> it is open, in which case soften "binding, not conditional".
> **Q4 (RC17, Fixed F6):** was answered by D7a (DR-03: "§A.6 Q4 answered") but carried no resolution
> marker, unlike Q1 — now marked above. Q3 is in a similar half-state: body §7 Open Question 2 is struck
> through as "Resolved in principle, deferred in practice" via D4b and DR-05's value column reads
> "Recursive CTE for parity; D4b after Stage 6", while Q3 here is unmarked — a scheduling-only residue
> presented as an open architectural question.
>
> </details>

---

## A.7 Verdict on the two core theses

### Thesis 1 — "Recursive CTEs solve the tough looping problems in dbt."

**VERDICT: SURVIVES on expressiveness. FAILS on performance. Correctness is conditional on three non-obvious clauses.**

**Survives.** Both hard loops are solved, verified, today, on DuckDB 1.5.5:
- **Connected components.** `[RECON]` the D4 formulation is **label-identical** (not merely partition-identical) to Splink's own algorithm — 0 label-diffs across four random graphs from 2k to 50k nodes and on VARCHAR composite ids — and correct on chain, star, cycle, self-loop, duplicate edges, reverse-only orientation, disjoint components, one-edge joins, singletons and the empty node table, plus 250 random graphs against a Python union-find oracle (0 failures for the corrected form vs **214/250** for the v1 form).
- **EM.** `[RECON]` a complete EM — E-step, M-step, per-comparison normalisation, λ update, convergence test **and** iteration cap — as **one** `WITH RECURSIVE … USING KEY` statement matched a Python reference to **3.885e-16** with an identical iteration count; both exit paths verified (`eps=0.05 → iter 2`; `eps=1e-6, max=200 → iter 200`). This kills v1's "blocked on DuckDB 2.0" outright, and it does something Jinja-unrolling cannot: reproduce Splink's *early-stopping* convergence.
- **u-estimation.** `[RECON]` bit-reproducible via `USING SAMPLE bernoulli(p%) REPEATABLE(seed)` — identical row sets and checksum `495571872` across `threads=1/4/8`.
- **Determinism.** `[RECON]` 20 runs (10 at `threads=1`, 10 at `threads=8`) on a 3,000-node graph → exactly **one** SHA-256.

**Fails on performance.** `[RECON]` on identical in-memory data in the same process producing identical results, Splink's Python-driven materialised-temp-table loop beats the single statement by **3.4× at 200k nodes, 8.3× at 1M, and 18.5× at 3M nodes / 10M edges (22.1 s vs 409.9 s)**. Root cause: `[RECON]` `EXPLAIN` shows every `recurring.cc` reference executes as a plain `HASH_JOIN` (`RECURSIVE_KEY_JOIN=False`; the specialised probe does not exist in the 1.5.5 binary), so the full accumulated table is re-hashed every iteration. The thesis buys correctness, determinism, zero Python and one inspectable statement — it does **not** buy speed, and D4a is right to say so.

**Conditional correctness.** Three clauses are load-bearing and all fail *silently*: the `GROUP BY` on the key (without it, `[RECON]` six different answers in six runs at `threads=8`, and it was *deterministic and wrong* at `threads=1`); never mixing `recurring.cc` in `FROM` with a bare `cc` in the guard; and the strict-decrease `HAVING` (without it the query hangs, and there is no depth cap). Plus one scheduled external risk: duckdb/duckdb **#24647** (open, *Ready To Merge*, 2026-08-10) redefines `UNION` under `USING KEY` and **removes** `deprecated_using_key_syntax` — `UNION ALL` makes it a no-op, which is why D4's insistence on `UNION ALL` is correct and should be a lint, not a note.

**And it is not optional.** `[RECON]` the classic non-`USING KEY` transitive-closure form is **300× slower at 2,000 nodes** and times out from 5,000 up; `[RECON]` stock DuckDB 1.5.5 has no SQL/PGQ, no graph extension in the core registry, and no connected-components function. There is no plan B inside the engine, which raises the stakes on version pinning and makes the Stage 0.5 gate correctly blocking.

> **[REVIEW 2026-08-23] RC18 — "No plan B" is likely stale against D4b.** It was true of the reviewed
> draft, but the body now contains D4b — "A custom `iterative_fixpoint` materialization — prototyped and
> working", measured 0.92 s vs 206.63 s on the 10k chain — and §7 risk row 2 calls it "the real fix". That
> is a plan B for the performance failure this paragraph describes (dbt-driven materialised iterations
> with pointer jumping), even though the no-graph-extension observation stands for the single-statement
> formulation. §B.5 placed a superseded marker at Thesis 2 below but not here, so a reader doing the
> appendix pass concludes the version-pinning stakes are higher than the body believes. Add a qualifier:
> no plan B inside a *single statement*; D4b is the engine-level escape, prototyped — see §7 risk 2 /
> DR-05.

### Thesis 2 — "Most of it can be standard models."

**VERDICT: SURVIVES on count and on vocabulary. FAILS as drawn on cost, and needs three mechanical corrections dbt imposes.**

**Survives — strongly, on the part that mattered most.**
- **17 of the 20 Splink surfaces in §2 are non-recursive standard SQL.** Only `entity_clusters`, `entity_clusters_1to1` and `train_em` need recursion. ~85% by count.
- **The comparison-level vocabulary is closed and 100% pure scalar expressions.** `[RECON]` all 21 level types in `comparison_level_library` render to pure boolean scalars over `<col>_l`/`<col>_r` — no subquery, no UDF, no Python — and every one **executed** in DuckDB 1.5.5. Two need care (lambdas in `PairwiseStringDistanceFunctionLevel`; nested `CASE` in `PercentageDifference`/`DistanceInKM`) and D6 names both.
- **The blocking-rule vocabulary is closed and 100% pure join predicates.** `[RECON]` every `BlockingRuleCreator` subclass returns a boolean SQL condition; the only two non-predicate features (`salting_partitions`, `arrays_to_explode`) are orthogonal modifiers and are mutually exclusive by an explicit `ValueError` (`blocking.py:58-62`).
- **The load-bearing D1 assumption holds.** `[RECON]` gamma `CASE`s built from a real trained model JSON **with no splink import** executed correctly in DuckDB 1.5.5. The JSON carries fully-rendered, dialect-specific DuckDB SQL, because `ComparisonLevelCreator.create_level_dict` renders `create_sql(sql_dialect)` at authoring time.
- **Splink's whole inference path is a single non-recursive SQL statement.** `[RECON]` the captured `CREATE TABLE __splink__df_predict_<hash> AS WITH blocked_with_cols …, __splink__df_comparison_vectors …, __splink__df_match_weight_parts … SELECT log2(…)` maps 1:1 onto the proposed dbt model boundaries. Nothing in blocking, TF, comparison vectors or scoring needs a loop.

**Fails as drawn.** "Standard models" ≠ "one materialised model per Splink CTE." `[RUN]`/`[RECON]` that conversion costs **5.6× bytes for one stage at four comparisons** and **17.6× / 946 B-per-pair across stages 3–5 at six**, which is what sets the ~2.75M-record ceiling (B1). The fix — `ephemeral` intermediates, which dbt fuses back into Splink's own shape — is free and *preserves* the thesis, but it must be a deliberate decision because it conflicts with the stage-decoupling mechanism, which needs those intermediates to be real relations. ~~Resolve by making materialisation a **mode** (`table` for parity, `ephemeral` for production) with a CI job asserting `int_edges` is byte-identical between them.~~ **[SUPERSEDED by D11 — see §B.1 G1.** The decision is `table` everywhere plus normative narrowness; there is no `ephemeral` path and therefore no mode to reconcile. D11 separates the two costs this paragraph conflates: the `_l`/`_r` passthrough, not the decision to materialise, is what B1 measured.**]**

**Three mechanical corrections dbt imposes on "just write models":**
1. **A variable number of models is impossible** (C5). D7 fixes TF; exploding rules need the same CTE treatment; anything else per-config must be long-format or a CTE.
2. **Models with JSON-derived column sets cannot be contracted or unit-tested** without the var-driven native-rendered `columns:` mechanism, and that mechanism forbids macros in the schema.yml context — so the column derivation must live where `er_model` is emitted (M2).
3. **Two of Splink's resolutions are not arithmetic and cannot be Jinja** — TF exact-match-level resolution (sqlglot CNF) and backend `link_type` selection (a runtime table count). These need the compile-time sidecar, which keeps runtime Python-free without pretending Jinja can do CNF normalisation (§A.2).

---

## A.8 Rejected findings

Dropped, with the reason, so they are not re-raised.

| Raw finding | Why rejected |
|---|---|
| Tolerance policy is `1e-6`/`1e-8` and inconsistent | **Already fixed** in v2 §6.1, with the correct `ln2·p(1−p)` derivation and the 17× figure. Only the *decision gate*, the `mw > 54` vacuity, and the measured 2.84e-14 headroom survive → folded into §A.4. |
| D2 describes `AND NOT (rule 1)` without `coalesce` | **Already fixed** in v2 D2, including the load-bearing `coalesce(…, false)` and Splink's own comment. |
| TF adjustment: wrong numerator / `LEAST` vs `GREATEST` / missing `tf_minimum_u_value` | **Already fixed** in v2 §3.2, all three corrections plus the NULL guard and the ELSE-level suppression. |
| TF denominator is `count(*)` not `count(<col>)` | **Already fixed** in v2 §3.5, with the `1/891` verification and the 0.1665-bit consequence. |
| `load_file` is not a dbt function | **Already fixed** in v2 D1. The residual 128 KiB `MAX_ARG_STRLEN` bound and the `env_var` alternative are folded into M2 as one note. |
| `recurring.` semantics are inverted; `never ephemeral` is obsolete | **Already fixed** in v2 D4/D4a, including both traps and the obsolete-justification correction. |
| Per-column TF models are impossible in dbt | **Already fixed** in v2 D7. The measured +18% long-format join cost and the VARCHAR-cast-symmetry requirement are worth one line in D7 but do not warrant a finding. |
| Gamma numbering is ascending / null-hoisted | **Already fixed** in v2 §3.3. Only the *invisibility* of a wrong counter and the `retain_matching_columns` baseline gap survive → M14. |
| Model JSON round-trip is lossy | **Already fixed** in v2 §3.4 with the production `model_test_v1.json` table. Only the Stage-1 validator tension survives → M13. |
| `u = 0` produces NaN in a log-space implementation | **Moot** under v2 §3.1's linear-space rule and the clamp/NaN notes. Residual: Stage 5's property should read `IS NOT NULL AND NOT isnan(p) AND between 0 and 1`, since `p ∈ [0,1]` passes vacuously on NaN — one word in an AC, not a finding. |
| Stage 3 property tests ("no duplicate pairs", "fails all earlier rules") contradict Splink | **Moot** — v2's Stage 3 AC no longer states them. Re-add only if exploding rules come into scope. |
| Stage 8 `ref('golden_records')` creates a DAG cycle | **Moot** — v2's Stage 8 no longer blocks against golden records. Superseded by B4, which attacks what v2 actually says. |
| Threshold monotonicity is a tautology | **Moot** — v2's Stage 6 AC no longer includes it. (Correct, though: `edges(0.99) ⊆ edges(0.9)` by construction and CC is monotone under edge subset, so refinement holds unconditionally — including at the worst measured quality point. Do not re-add it as a quality test.) |
| No ground-truth evaluation anywhere | **Partly fixed** — v2 adds Stage 10. Survives only as "measures but does not gate, and the frozen fixture model is bad" → M12. |
| One-to-one clustering / link jobs are ignored | **Partly fixed** — v2 adds `entity_clusters_1to1`. Survives only as "no AC, tie semantics untested" → M11. |
| Survivorship builds incoherent composites | **Partly fixed** — v2's D10 adds the multi-column-unit rule, which is the worst case. Residual gaps → M19. |
| `is_bridge` cannot be pure SQL | **Already stated** in v2 S3. Survives only as "S3 names the problem and proposes no verification" → M11 + §A.2 C1. |
| Splink's `min(match_key)` VARCHAR bug | **Already stated** in v2 S4 with the correct disposition (replicate + log). No action. |
| `__splink_salt` breaks concat parity | **Already stated** in v2 S1 and Stage 2's AC. No action. |
| Cluster `cluster_id` is lexicographic on the composite | **Already stated** in v2 D3/D4a. Survives only as the *identity* problem → M6. |
| Splink's spurious NULL-keyed rows on dangling edges | **Already stated** in v2 D4 as a deliberate disagreement with a comparator special-case. No action. |
| dbt `--empty`, `dbt docs`, `local_md5`, the `recurring` schema-name collision, DuckDB parquet `created_by` | Verified non-issues or one-line notes; no design consequence. |

---

# Appendix B — Third pass: product, trust, and consistency gaps

**Date:** 2026-08-20 · **Attacks:** the v2 body **and** Appendix A, together, plus
`docs/DbtBestPractices.md` v1
**Posture taken as given:** `dbt-er` is an **engine the existing platform calls**, as §1.3 states —
not a platform replacement. This resolves §A.6 Q1 (now marked) and is the premise every finding below
is scoped against.

---

## B.0 How to read this, and what kind of evidence it carries

Draft v2 was written against Splink. Appendix A was five red teams written against *v2*, and it is
good: 5 blockers, 21 ranked findings, a rejected-findings table so nothing gets re-litigated. Between
them they cover the arithmetic, the DuckDB semantics, the dbt mechanics, the parity policy, the
clustering cost and the quality floor.

**This pass attacks a different surface.** Appendix A asked *"is this design faithful to Splink and
buildable in dbt?"* — and answered it thoroughly. It did not ask *"is this a product a second team can
run on real people's records?"* Almost every finding below falls in that second question: the package's
own inputs and outputs, its trust boundary, the data it holds, its life as versioned software, and — the
largest single class — its **self-consistency after three revisions**.

### B.0.1 Evidence classes

This pass ran **no engine measurements**. It introduces no `[RUN]` and no `[RECON]` evidence, and
nothing below should be read as if it did. Two classes are added to §A's:

| Class | Meaning |
|---|---|
| `[DOC]` | Read from this document or `DbtBestPractices.md` on disk. Verifiable by opening the file at the cited section. |
| `[GREP]` | A reproducible absence or presence count. The command is given so a future revision can re-run it and the finding can *expire*. |
| **UNVERIFIED** | Reasoning, or a claim requiring measurement this pass did not perform. Marked inline, never smoothed away. |

The distinction matters more here than usual, because the strongest findings below are *absences*, and
an absence is the easiest thing in the world to assert carelessly. Every one carries its grep.

**Citations are by section id, not line number** — deliberately, because this appendix was merged into
a document it cites, and line numbers rot on the next edit.

### B.0.2 The grep suite

Counts were taken against the **pre-merge** document — the body plus Appendix A, before this appendix
and its inline pointers existed. To re-run, restrict the range to everything above the `# Appendix B`
heading:

```
end=$(grep -n '^# Appendix B' DesignDoc.md | cut -d: -f1)
for t in PII privacy GDPR retention redact mask licen copyright semver changelog \
         cadence threat untrusted 'allow-?list' current_date 'now\(\)' x86 linux \
         'input contract' 'duplicate unique_id' 'semantic version' 'deprecation policy'; do
  printf "%-22s %s\n" "$t" "$(sed -n "1,${end}p" DesignDoc.md | grep -icE "$t")"
done
```

All twenty-two returned **0** pre-merge. Several are now non-zero above the fence, because the merge
added the pointers these findings asked for (D1, D6, D7a, §3.1, §6.1, Stage 0.1) — that is the findings
being *acted on*, not invalidated. A finding expires when its **recommendation** is implemented, not
when its keyword appears.

Three near-misses are worth stating precisely, because a careless reading of the counts would overstate
the case:

- `injection` returned 6 `[GREP]` — **every one is baseline injection** (`er_inject_baseline`, M4).
  None was about SQL injection.
- `whitelist` returned 1 `[GREP]` — D6's function list, discussed in **G3**.
- `statement timeout` returned 2 `[GREP]` — both are *recommendations to add one* (M5 rec, A.2 C6),
  neither is evidence that DuckDB offers one. See **G13**.

### B.0.3 Ranking

Same rule Appendix A used: **severity × how early the decision is irreversible.** G1–G5 change things
not yet built, or change what may safely be built at all. G1 is first because it is the only finding
that makes the *other twenty-five findings in this repository* ambiguous.

---

## B.1 Ranked findings

---

### G1 — The design decides materialisation four different ways, and had no precedence rule between the body and its own appendix

**Severity:** BLOCKER · **Attacks:** D11, A.1 B1, A.5 Stage 0, A.7 Thesis 2, `DbtBestPractices.md` §7
· **Scope:** in-scope

**Claim.** The most-cited decision in the programme had four live, mutually exclusive statements inside
one document, and the companion document implements the one the body explicitly overrode. An
implementer opening the design at any of four places got a different instruction, and each read as
normative.

**Evidence.** `[DOC]`

| Where | Says |
|---|---|
| **D11** | "**Decision: every stage model is `materialized='table'`.** No `ephemeral` intermediates, no fused-vs-staged mode switch." Also: remove `er_materialise_intermediates` or default it `true`, and drop `ephemeral` from `er_allowed_materializations`. |
| **A.1 B1 rec 1** | "Make `int_comparison_vectors` and the wide half of `int_scored_pairs` **`ephemeral` by default** via `var('er_materialise_intermediates', false)`." |
| **A.5 Stage 0.6** | "…**decide** `ephemeral` vs `table` per intermediate." |
| **A.7 Thesis 2** | "Resolve by making materialisation a **mode** (`table` for parity, `ephemeral` for production) with a CI job asserting `int_edges` is byte-identical between them." |
| **`DbtBestPractices.md` §7** | A per-model table making the two widest models `ephemeral`, `er_materialise_intermediates` default `false` — i.e. **B1's recommendation, which D11 supersedes**. |

The mechanism that allowed this was a missing rule, not an oversight. The v2 note says "Body sections
that Appendix A contradicts have been corrected in place" — D11 *is* that correction, argued from a
later measurement — but A.1, A.5 and A.7 were left standing as written. `DbtBestPractices.md` §1 sets
precedence **between documents** ("measured findings in `DesignDoc.md` Appendix A → this document →
habit"), which read literally makes B1 (measured, Appendix A) outrank D11 (body). That is backwards from
what the body intends, and it is why §7 of the companion document implements the superseded branch.

**Failure scenario.** Two engineers start in the same week. One reads D11 and writes eight `table`
models with contracts and constraints. The other reads `DbtBestPractices.md` §7 — the document whose
*job* is to say which materialization a model may use — and makes the two widest models `ephemeral`,
which per M2 silently removes their contracts and per §7's own admission removes their timing rows. Both
cite a normative source. The disagreement surfaces at the first capacity measurement, when the two
builds have different byte costs and neither matches the published `er_max_pairs`.

**Recommendation.**

1. **State the intra-document precedence rule.** *The body is normative; the appendices are evidence.
   Where the body answers an appendix finding explicitly and by name, the body wins; where it is silent,
   the appendix stands.* — **DONE**, in the v2 note, as part of this merge.
2. **Reconcile all five statements to one.** D11 is right on the merits — it separates the two costs B1
   conflated and is argued from a later measurement. A.1 B1 rec 1, A.5 Stage 0.6 and A.7 Thesis 2 are now
   marked **[SUPERSEDED by D11]**; `DbtBestPractices.md` §7 still needs rewriting (**R1**).
3. **Adopt the decision register** (§B.3). This finding is a symptom; the register is the treatment. A
   document that has reversed six load-bearing decisions and expects to reverse more needs the current
   value of each one to be a single row, not a paragraph in whichever section was edited last.

---

> **[REVIEW 2026-08-23] RC19 — Done, 2026-08-23.** `DbtBestPractices.md` v2 rewrote its §7 to D11's
> contract and added a tier 2 to its §1.1 precedence (a D-number that reinterprets an Appendix A
> measurement wins) — closing the inversion this finding diagnosed from both ends. R1 is closed; see the
> companion's Appendix E and the review note at D11.

### G2 — The package's entry point is unspecified · **CLOSED 2026-08-23**

> **Closed by §2.0**, which adopts this finding's recommendation in full. **DR-16 is CURRENT.** All five
> undefined things below now have answers: the wiring mechanism (a relation-name var, since M4b rules out
> `source()`), the arity (one relation in v1, because Stage 12.1 forbids `source_dataset` — and the
> consumer owns the union when v2 needs one, since only they know what "global" means for §3.5), the column
> contract with `unique_id` VARCHAR hoisted out of Stage 12.1, the missing-column failure mode as a
> compile-time error naming the column, and D1's corollary applied to inputs via `er_input_columns`.

**Severity:** BLOCKER · **Attacks:** §2 (`stg_input`), D1 corollary, D8, M4(b), Stage 2 · **Scope:** in-scope

**Claim.** Every model in the DAG is derived from `stg_input`, and nothing in the document says how a
consumer supplies data to it. `[GREP]` `input contract` → 0. This is the one interface every user
touches, and it is the only major interface with no specification at all.

**Evidence.** `[DOC]` §2 gives `stg_input` as "Bare `UNION ALL` passthrough. **No transforms**", and D8
explains *why* it must not transform — both correct, and neither says where the rows come from.
Specifically undefined:

- **The wiring mechanism.** `source()`, a relation-name var, or `ref()` into consumer models? M4(b)
  settles that the package ships **zero sources** — correct, and it sharpens the omission, because it
  rules out the obvious mechanism without naming the replacement.
- **Arity.** One input relation or many? If many, who owns the `UNION ALL` — the package (needing a list
  var) or the consumer (needing a pre-unioned relation)? §3.5 requires TF to be computed on the
  **global** concat, so the answer is load-bearing for correctness, not just ergonomics.
- **The column contract.** Which columns must exist, at what types. D3 shows `unique_id`'s *type* changes
  pair ordering — `'ds-__-9' < 'ds-__-100'` is false where `9 < 100` is true — so a BIGINT id and a
  VARCHAR id are different products. Stage 12.1 fixes VARCHAR for v1 in passing, inside a cutover stage,
  which is not where a reader looks for an input contract.
- **The missing-column failure mode.** The model JSON names columns; the input may not have them. Today
  the failure is a DuckDB `Binder Error` raised from inside a generated `CASE`, hundreds of lines into
  compiled SQL, naming a column the user never wrote.
- **The interaction with D1's corollary.** Every column name must be parse-time derived, never
  introspected — that rule is written for *outputs*. For *inputs* it implies the declared input column
  set must itself be a parse-time var, which nothing states, and which is the kind of thing discovered in
  Stage 4 and fixed in Stage 1.

**Failure scenario.** The parity harness wires input one way; the first consumer wires it another. Both
build green. The consumer's concat is per-source rather than global, so their TF denominators differ from
the frozen snapshot, and every adjusted comparison is off by a uniform bit shift — the exact defect §3.5
was written to prevent, arriving through the door nobody specified.

**Recommendation.** Add a normative **§2.0 Input contract**: the relation-name var (and its list form for
multi-source), who owns the union, the required/optional column declaration as a parse-time var, the type
contract for `unique_id` and every model-JSON-referenced column, and a **compile-time check that every
column the JSON references is declared** — which turns a `Binder Error` into an actionable message and
belongs in G13's error catalogue. Cross-reference D3 so the id-type decision is visible where people look
for it.

---

### G3 — The model JSON is untrusted SQL executed with the consumer's credentials, and there is no trust boundary · **CLOSED 2026-08-23**

> **Closed by §1.5**, which adopts all five recommendations below. **DR-17 is CURRENT.** The trust boundary
> is declared, D6's list is a closed allow-list validated against the parsed tree, the input is bounded,
> `er_model_sha` is the hash of the validated artifact, and the five negative tests are Stage 1 acceptance
> criteria. §A.2's sidecar paragraph — which G3 correctly noted *"never says a JSON must pass through it"* —
> now says it.

**Severity:** BLOCKER · **Attacks:** D6, D1, §6.3, principle 4 · **Scope:** in-scope

**Claim.** D6 passes every `sql_condition` through **verbatim** into compiled SQL. D1 delivers the JSON
through an environment variable, so it never passes review as code. The document treats the JSON as a
*contract* (principle 4) and never as a *trust boundary*, and the difference is the whole of this
finding. `[GREP]` `untrusted` → 0, `threat` → 0, `allow-?list` → 0.

**Evidence.** `[DOC]`

- D6 confirms verbatim passthrough and gives a 27-function list introduced as *"Lint whitelist for D6's
  validation step"* — the only appearance of the word `whitelist` `[GREP]`. It was never normative: it
  did not say allow-list or deny-list, where it is enforced, what it is matched against (raw string or
  parsed tree), or what happens on a violation.
- **Nothing banned non-deterministic functions inside a level.** `[GREP]` (pre-merge) `current_date` → 0,
  `now\(\)` → 0; `random\(\)` appeared once, only as S1's note about `__splink_salt`. A level containing
  `current_date` — an age-band comparison is the natural way to write one — makes gamma a function of the
  wall clock. Every parity gate in §6.1 and every determinism assertion in §6.3 silently becomes untrue,
  and the failure looks like flaky CI, not like a bad model.
- M15 bans `invocation_id`, `run_started_at`, `thread_id` and `set()` from anything reaching
  `compiled_code` — the right instinct, applied only to *our* Jinja. The larger untrusted surface,
  spliced in wholesale by D6, was not covered.
- **Nothing constrains syntactic shape or size.** A `sql_condition` is arbitrary text placed inside a
  `CASE WHEN`. Subqueries, statement terminators and side-effecting calls are excluded by nothing stated.
  No bound on JSON size or level count, though M2 measures 397 B/level for a different purpose.
- **Nothing states the artifact's provenance requirement.** A.2's sidecar is described as a *convenience*
  that resolves what Jinja cannot compute. It is also the natural enforcement point, because it already
  parses every condition with sqlglot — but the document never says a JSON must pass through it, only
  that its outputs are used.

**Failure scenario.** Benign: an analyst hand-edits a level to add a date-relative condition, CI goes
intermittently red for a week, and the cause is invisible because the SQL is *correct* — it just is not
the same SQL tomorrow. Hostile: the package is installed in a consumer's project, `DBT_ER_MODEL_JSON` is
set from a pipeline variable rather than a reviewed file, and arbitrary SQL executes with the warehouse
credentials of every project that installs it. Neither is exotic, and the first will happen.

**Recommendation.**

1. **Declare the model JSON a trust boundary** in §1 and amend principle 4: it is an input, not a
   contract, until validated.
2. **Make D6's list a normative allow-list**, validated against the **parsed** condition, not the raw
   string — the sidecar already holds the parse tree. Explicitly reject non-deterministic functions,
   subqueries, statement terminators, and any function outside the list. *(D6 now carries this note.)*
3. **Bound the input**: max levels, max comparisons, max JSON bytes. Cheap, and it converts a class of
   pathological inputs into a named error.
4. **Require sidecar processing and hashing before `dbt build` accepts a JSON** — i.e. `er_model_sha`
   (already required by M8) is the hash of a *validated* artifact, not of whatever arrived in the
   environment.
5. Negative tests: a level containing `current_date` fails compilation; a level containing a subquery
   fails compilation; an unlisted function fails compilation **naming the function**.

---

### G4 — PII and data protection are absent, and two accepted decisions actively conflict with erasure

**Severity:** BLOCKER · **Attacks:** §1.3, D7a, D11 rec 3, M18, Stage 11 · **Scope:** in-scope for
*disclosure*; `[interface]` for the erasure *mechanism*

**Claim.** This is a person-matching engine. Its fixtures are names, dates of birth, email addresses and
postal addresses. The document contains no data-classification statement, no retention rule, no handling
constraint — `[GREP]` `PII` → 0, `privacy` → 0, `GDPR` → 0, `retention` → 0, `mask` → 0, `redact` → 0;
`erasure` appeared exactly once, in M6, and about identifier churn rather than about erasure. Two
accepted recommendations do not merely omit the concern — they cut against it.

**Evidence.** `[DOC]`

1. **M18's failure bundle exports the input data.** It specifies "the exact `--vars` payload bytes …,
   `sha256(input)` **plus the input**, the baseline parquet set and manifest, `target/compiled/**` …".
   Stage 11 then freezes failures into `fixtures/regressions/`. On a production or shadow run (Stage
   12.2 — *"Both engines on production data"*), that is a CI artifact and a git commit containing real
   records. M18 is right that a non-reproducible failure is worthless; the bundle is the correct instinct
   pointed at data nobody has classified.
2. **D7a's frozen TF snapshot outlives the records that produced it.** Freezing is the right decision and
   this pass endorses it. Its unstated consequence: the snapshot *is* a value distribution over the
   corpus — surnames, email locals, postcodes, with frequencies — so deleting a source record does not
   remove its contribution, **by design**. "Erase this person" is not satisfied by deleting their row, and
   the refresh that would satisfy it is precisely the operation D7a makes rare and deliberate. A genuine
   tension between two correct goals (score stability and erasure), which the document should name rather
   than inherit. *(D7a now carries this note.)*
3. **Smaller surfaces, unnamed.** D11 rec 3 keeps a `wide` debug variant that persists `_l`/`_r` source
   values into intermediates — PII duplicated per *pair* rather than per record, i.e. the largest copy of
   it in the system. `persist_docs` (`DbtBestPractices.md` §8.2) writes column descriptions into the
   warehouse catalog. Neither is wrong; neither is stated.

**Failure scenario.** A Stage-12 shadow run goes red on production data. The harness does exactly what
M18 specifies: it writes a bundle containing real records into CI artifact storage, and an engineer
commits the interesting case to `fixtures/regressions/` so it never regresses. The records are now in a
git history, replicated to every clone, outside every retention control the platform has. Nobody did
anything the document told them not to.

**Recommendation.**

1. Add **§1.5 Data classification**: what class of data each model holds (`stg_input` and the `wide`
   variant hold source PII; `tf_all` holds a derived value distribution; narrow `int_*` holds ids and
   integers; marts hold surviving attribute values), so exposure is reasonable about per relation.
2. **Fixtures and failure bundles are synthetic-or-scrubbed, enforced by a gate.** Amend M18: the bundle
   carries `sha256(input)` and a *scrubbed or synthesised* reproduction, with the real input referenced
   by pointer into whatever store the platform already governs. A CI check on `fixtures/**` is the
   enforcement, since the alternative — remembering — has a known failure rate.
3. **Name the TF-snapshot erasure obligation** in D7a: an erasure event is a **refresh trigger**, and the
   refresh is how erasure reaches the frozen snapshot. `[interface]` — the platform decides and executes;
   the engine states what it holds and exposes the trigger.
4. State the `wide` variant's exposure at D11 rec 3 and forbid it in production explicitly, which D11
   implies and does not say in those terms.

---

> **[REVIEW 2026-08-23] RC20 — Half-expired per B.5's rule (2026-08-23).** `DbtBestPractices.md` §20.4 now
> rules fixtures and seeds synthetic-only, enforced by its 3.55 (PII scan over `seeds/`, `fixtures/` and
> `harness/`) — which also gates the `fixtures/regressions/` freeze in this finding's failure scenario —
> and its Appendix B.7 covers observability redaction. Still unimplemented anywhere: the M18 amendment
> itself. The failure bundle is a CI artifact outside 3.55's scan, and M18 still specifies it carries the
> real input rather than a scrubbed reproduction plus a pointer into a platform-governed store. Recs 1, 3
> and 4 remain this document's to write.

### G5 — "Exact bit equality" was measured on one platform, and committed baselines cross platforms in CI

> **CLOSED 2026-08-23 `[RUN]`.** Measured on DuckDB 1.5.5 across **darwin/arm64** and **linux/amd64**:
> all five probe values are **bit-identical**, including `log2` — the libm call this finding is about.
>
> | value | darwin/arm64 | linux/amd64 |
> |---|---|---|
> | `via_product` | `413498e8ffffffff` | `413498e8ffffffff` |
> | `via_logs` | `413498e8fffffffc` | `413498e8fffffffc` |
> | `clamped` | `413498e8ffffffff` | `413498e8ffffffff` |
> | **`match_weight`** (`log2`) | `40345d48400a308f` | `40345d48400a308f` |
> | `match_probability` | `3feffffe724742f5` | `3feffffe724742f5` |
>
> §B.5 item 4 said this *"either closes the finding permanently or changes the tolerance table"*. **It
> closes it: A.4's exact-bit-equality default holds across platforms, and no tolerance is spent.**
>
> **It is not recorded as a measurement, because a measurement decays.** `harness/float_probe.py` commits
> the reference and `harness/test_float_parity.py` asserts it — locally on darwin/arm64, and in CI on
> `ubuntu-24.04`, which is native linux/amd64. **Two platforms assert the same bits on every run**, which
> is a stronger claim than either measuring once, and a divergence fails at the probe rather than being
> discovered as a baseline that stopped reproducing. Scoped to DuckDB 1.5.5 per §0; the version is
> asserted, so a bump reopens the finding by failing.
>
> *(One caveat stated rather than buried: the amd64 run used the genuine linux amd64 DuckDB wheel under
> `docker --platform linux/amd64` on an arm64 host. That is why the gate — not the measurement — is the
> deliverable: CI's `ubuntu-24.04` is native amd64 and asserts the same reference continuously.)*

**Severity:** BLOCKER · **Attacks:** §6.1, A.4, Stage 0.3, M18 · **Scope:** in-scope

**Claim.** §6.1 makes **exact bit equality** the default gate, justified by "both engines run float8 on
the same DuckDB. Where the expression tree is identical, the result is identical." That justification is
sound for the way it was *measured* — one process, one machine, both engines in the same DuckDB. It does
not automatically transfer to the way the gate will be *run*: a baseline parquet generated on one machine
and compared against a dbt build on another.

**Evidence.** `[DOC]` Appendix A's evidence classes state the environment as "DuckDB 1.5.5 in-memory,
threads=4, **darwin arm64**" — `[GREP]` `arm64` → 1, `darwin` → 1, `linux` → 0, `x86` → 0. Stage 0.3
commits baselines as parquet with a manifest; Stage 0.4 freezes them; `DbtBestPractices.md` §15 puts the
full build in CI. So the *artifact* is portable and the *claim* was measured in-process.

`log2` and `pow` — the two functions §3.1 and §3.2 put on the critical path — are libm calls, and DuckDB's
build, compiler and vectorisation differ across platforms. Whether that produces a 1-ULP difference on any
reachable input is **UNVERIFIED**; this pass ran nothing. The finding is not "the floats will differ" — it
is that **the document asserts a gate whose validity domain it never states**, and M18's headline
requirement ("a CI job that reproduces the same red/green verdict on a clean runner from the bundle
alone") is exactly a cross-machine claim.

**Failure scenario.** Baselines are generated on a developer's Mac and committed. CI runs on Linux and goes
red on a `match_weight` bit-difference in the last place. Because §6.1 says exact equality is *expected*,
the team reads a 1-ULP platform artifact as a real divergence and spends a week in the scoring macro. Or —
worse and likelier — someone relaxes the gate to a tolerance to make CI green, and A.4's measured five
orders of unused headroom (2.84e-14 achievable against a 1e-9 gate) is quietly spent on something that was
never an arithmetic error at all.

**Recommendation.**

1. **Scope the parity claim to `(platform, architecture, DuckDB build)`** in §6.1, A.4 and the `PARITY.md`
   scope statement. One clause. *(§6.1 now carries the pointer.)*
2. **Make the baseline manifest carry the platform triple**, alongside the Splink version and model JSON
   sha it already carries — G16 needs the same field, and M18's bundle should **refuse to compare** across
   a mismatch rather than report a difference.
3. **Add a one-off CI job that regenerates a baseline on the CI platform and diffs it against the committed
   one.** This converts the UNVERIFIED above into a measured fact in an afternoon. If it is bit-identical,
   say so in `PARITY.md` and the concern closes permanently. If it is not, the tolerance rows must be
   architecture-qualified — and that is far cheaper to learn at Stage 0 than at Stage 5.

---

> **[REVIEW 2026-08-23] RC21 — Currency (2026-08-23): rec 2 now has a named home and a live gap.** Rec 1
> is partially implemented — `DbtBestPractices.md` §22.1/3.59 states the validity domain, anchoring float
> gates to linux/amd64. Rec 2 is not: §20.1's mandated manifest (Splink version, model-JSON sha256,
> generator seed, DuckDB version, date, producing commit — asserted by its 3.62) carries **no platform
> triple**, so a platform-anchored gate compares against an artifact whose producing platform is
> unrecorded — the exact mismatch this rec exists to make refusable, sharpened by the companion's own
> admission that development happens on arm64. One field in §20.1's list closes it. Rec 3's cross-platform
> regeneration measurement also remains unrun; the companion's Appendix E registers this half as
> unanswered.

### G6 — There is no model inventory, and no statement of what is public

**Severity:** MAJOR · **Attacks:** §2, D11, M2, `DbtBestPractices.md` §7 · **Scope:** in-scope

**Claim.** No single place lists what the package ships. §2's table is organised by *Splink surface*, so
anything without a Splink counterpart has no row — and those include two of the marts. Three partial
enumerations exist and none agrees with the others.

**Evidence.** `[DOC]` §2 covers 20 Splink surfaces. `golden_records` and `cluster_membership` appear
nowhere in §2 or §5 `[GREP]` — `cluster_membership` occurs 4 times, all inside Appendix A (M2, M17, A.3,
D11's discussion) and never in the body's stage plan, yet it is named a public mart. M2 enumerates seven
fixed-schema models plus two JSON-derived; D11 enumerates a different set; `DbtBestPractices.md` §7
enumerates nine with materializations. No enumeration carries grain, and none says which models a
consumer may `ref()`.

**Failure scenario.** Consumers `ref()` `er_int_scored_pairs` because it is the interesting one. Renaming
or narrowing it — which D11 recs 1 and 2 *require* — becomes a breaking change nobody declared, and G12
has no way to classify it because there is no declared surface to break.

**Recommendation.** A **§2.1 Model inventory**: one row per shipped model with name, grain,
materialization, contract status, public/internal, and the ACs that gate it. It is the index the document
lacks, it makes G10's four uncovered models visible by construction, and it is what G12 needs to define
"breaking". **Added 2026-08-23:** one more column, and one more consumer — **D12** quantifies unit-test
coverage over "every model", so this inventory is the set that phrase ranges over, and the row should carry
the unit-test cases that model's tests must cover. Until it exists, 3.20 is enforced over whatever the
manifest contains, which is not the same set as the one the design intends.

---

> **[REVIEW 2026-08-23] RC22 — Currency (2026-08-23).** The last clause no longer holds of the companion:
> `DbtBestPractices.md` §19.1 now enumerates the public surface (`er_entity_clusters`,
> `er_golden_records`, `er_cluster_membership`; asserted by its 3.61), and its rewritten §7 per-model
> table marks the marts "Public marts". Still open, and now the whole of this finding: the §2.1 inventory
> in this document — grain, contract status, and gating ACs per model — and the reconciliation of this
> document's own three partial enumerations (§2, M2, D11).

### G7 — Retraining and TF refresh force a full rescore, and that cost appears nowhere

**Severity:** MAJOR · **Attacks:** D7a, M8, §7 Q1, Stage 8 · **Scope:** in-scope

**Claim.** M8 correctly makes `er_model_sha` and `er_tf_snapshot_id` part of the data and forbids mixing
them. The direct consequence is never stated: **a new model JSON or a new TF snapshot invalidates every
scored pair in the corpus.** Not the new ones — all of them. That forces a full rescore, a full
re-cluster and a full golden-record rebuild, which at B1's measured cost is the single largest recurring
expense in the system. No budget, no cadence, no AC, no blast-radius report. `[GREP]` `cadence` → 0.

**Evidence.** `[DOC]` M8 requires `count(distinct er_model_sha) = 1` on `int_scored_pairs` — exactly the
assertion that makes a partial rescore illegal. D7a step 4 names the refresh as "an explicit, dated
operation that mints a new `er_tf_snapshot_id` and re-scores" without costing it. B1 measures stages 3–5
at 5,268 MB / 21.1 s for 1M records; the retrain path pays that plus clustering plus survivorship, and per
M6 it churns entity labels downstream. §7 Q1 asks for a target scale but not for a *retrain* budget, which
is a different and larger number.

**Failure scenario.** A model is retrained to fix M12's F1 = 0.72 problem — exactly the outcome the
document wants. The rescore runs into the B1 ceiling on the production corpus, or takes long enough that
it cannot fit the maintenance window, and the improvement cannot be shipped. The blocker is discovered at
the moment of greatest urgency, because nobody costed the operation that makes any model change
deliverable.

**Recommendation.** Add a **§5.x Reprocessing** subsection: the full-rescore cost model derived from the
same measured B/pair as `make capacity`; who triggers it and on what signal; how `er_model_sha` and
`er_tf_snapshot_id` roll forward together; and a **blast-radius artifact** — entities changed, merged,
split, and pairs crossing the threshold in each direction — as a required output of any rescore. That
artifact is also what G8 needs and what Stage 12.3's go/no-go already implies without naming it.

---

### G8 — There is no output-state rollback and no run-over-run diff

**Severity:** MAJOR · **Attacks:** D11, Stage 12.4, M6, M7 · **Scope:** in-scope for the *artifact*;
`[interface]` for the retention policy

**Claim.** D11 makes every model `table`, so every run is a destructive rebuild: the previous run's
clusters, edges and golden records are gone the moment the new ones land. Stage 12.4 provides a rollback
for the **engine** (switch back to the incumbent). Nothing provides a rollback for the **data**, and M7's
manifest records that a run happened without preserving what it replaced.

**Evidence.** `[DOC]` D11 `table` everywhere. Stage 12.4 is scoped to the engine swap. M7 proposes
`_er_run_manifest` — metadata, not state. M6 establishes that a single new record can relabel 100% of a
cluster, so "the output changed" is the normal case and cannot itself be the alarm.

**Failure scenario.** A bad TF snapshot ships. Entities re-partition across the corpus. Recovery requires
re-running the previous inputs, which requires that they still exist and that the run is bit-reproducible
— plausible under frozen TF and a pinned model sha, and nowhere asserted. Meanwhile there is no diff
showing what changed, so the blast radius is unknown at the moment someone must decide whether to roll
back.

**Recommendation.** Decide retention explicitly: either keep N prior runs of the cluster and mart layer
(cheap — they are the narrowest relations in the DAG), or declare **"re-run is the rollback"** and add the
AC that proves it: *the same `(input snapshot, er_model_sha, er_tf_snapshot_id)` reproduces the prior
run's content hash.* Either way, make the **run-over-run delta a required artifact** — the same report G7
needs and Stage 12.3 already depends on.

---

### G9 — Input preconditions are unstated, and the most likely one fails silently · **CLOSED 2026-08-23**

> **Closed by §2.0** (DR-16), which adopts this finding's recommendation in full. The three preconditions —
> `unique_id` unique, `unique_id` not null, corpus non-empty — are normative and ship as tests **in the
> package**, so they fail in a consumer's build rather than only in this repository's CI. The duplicate-id
> case is called out as the one that fails silently, with D3's strict inequality named as the mechanism.
> Stage 0.2 carries the degenerate-corpus fixture set (§5, added 2026-08-23), which is the counterpart to
> the degenerate-graph set D4's spike already had.

**Severity:** MAJOR · **Attacks:** D3, §2 `stg_input`, Stage 3 AC, Stage 0.2 · **Scope:** in-scope

**Claim.** Nothing states what must be true of the input for the engine's guarantees to hold, and one
violation is silent in the worst way — it *removes* pairs rather than erroring.

**Evidence.** `[DOC]` D3 gives the pair predicate as `l.<uid> < r.<uid>`. Two records sharing a
`unique_id` therefore **never pair with each other** — strict inequality excludes them — so a duplicated
id silently suppresses exactly the match most likely to matter, with no error, no warning and no test.
`[GREP]` `duplicate unique_id` → 0; `primary key` → 1, and that occurrence is M15's content-hash
discussion, not a precondition. `DbtBestPractices.md` §8.2 *does* put `unique_id VARCHAR PRIMARY KEY` on
`er_stg_input` — so the guard exists, in the other document, framed as a keying convention rather than
tied to the failure it prevents.

Also unspecified: NULL `unique_id` (propagates into blocking and into D4's node seed, where Splink's own
NULL-row defect is already documented); the empty corpus; the single-row corpus; an all-identical corpus
(worst-case pair explosion against the B1 ceiling); an all-NULL blocking column. D4's spike tests the
*graph* degenerate shapes thoroughly — chain, star, cycle, self-loop, empty node table. The *corpus*
degenerate shapes have no equivalent.

**Failure scenario.** A consumer's upstream emits a record twice under one id after a partial reload. The
engine does not merge them, does not fail, and reports nothing — the pair simply is not in the output. The
symptom is a recall miss indistinguishable from a blocking gap, which M12's recall floor would attribute
to the blocking rules.

**Recommendation.** State the preconditions in §2.0 (G2's new section) as normative and testable:
`unique_id` unique and not null, corpus non-empty; each with a dbt test **in the package**, so it fails at
build time in a consumer's project and not only in this repo's CI. Add the degenerate-corpus fixture set
to Stage 0.2 alongside the degenerate-graph set that already exists.

---

### G10 — Four more models ship with no grain, no contract and no acceptance criteria

**Severity:** MAJOR · **Attacks:** §2, §5 Stages 6–7, M11 · **Scope:** in-scope

**Claim.** M11 caught four Stage-6 models with tasks but no ACs. The same audit applied to the rest of the
DAG finds four more: `golden_records`, `cluster_membership`, `compare_two_records` and
`int_deterministic_links`.

**Evidence.** `[DOC]` `int_deterministic_links` and `compare_two_records` each appear once in §2 marked
"Easy", and never again — no stage owns them, no AC mentions them, and `compare_two_records` is the
"explain this pair" primitive G19's debugging workflow depends on. `golden_records` is covered by D10 and
Stage 7 as a *strategy* but never as a *model* — no grain, no column list, no contract.
`cluster_membership` has no body appearance at all (G6).

**Failure scenario.** M11's failure mode, repeated. For `golden_records` it is worse than untested — Stage
7 has no Splink oracle by construction, so if its grain is not written down there is nothing to test
*against*, and M19's per-field-group property (the one that catches incoherent composites) cannot even be
expressed without a declared grain.

**Recommendation.** Fold into G6's inventory and give each a one-line AC in its stage. For
`compare_two_records`, state that it is the supported pair-level debugging entry point (G19) and gate it
accordingly.

---

### G11 — sqlglot is parity-critical and appeared on no pin list

**Severity:** MAJOR · **Attacks:** A.2 C2, Stage 0.1, `DbtBestPractices.md` §16 · **Scope:** in-scope

**Claim.** The TF exact-match resolution — which decides *which levels get a term-frequency adjustment*,
and therefore changes scores — is sqlglot's normalisation behaviour, not Splink's. sqlglot was named five
times as the mechanism and never once as a dependency to control.

**Evidence.** `[DOC]` A.2 C2: Splink "parses `sql_condition` with sqlglot, normalises to CNF via
`simplify(normalize(tree))`, splits top-level ANDs and compares tree signatures", and the recorded
divergences are exquisitely sensitive to that normalisation — `"name_l" = "name_r" AND 1=1` resolves
**False** where the same condition without the tautology resolves True. Stage 0.1 pinned "`splink`,
`duckdb`, `dbt-core`, `dbt-duckdb` exactly". `DbtBestPractices.md` §16 puts exactly those four on
Dependabot's ignore list and routes everything else through automated minor and patch bumps. sqlglot
arrives transitively under Splink, so a lockfile refresh can move it without touching any protected pin.

The mitigating control exists and is not wired up: A.2's sidecar carries a **byte-equality regeneration
test**. That test *would* catch a sqlglot behaviour change — if it ran on dependency updates, which §16's
ritual (blocking D4 gate, parity suite, scale benchmark) specifies only for DuckDB bumps.

**Failure scenario.** A routine dependency PR bumps sqlglot a patch version. Its CNF simplification changes
on one shape. One comparison silently stops receiving its TF adjustment. Every downstream test is green —
gammas unchanged, arithmetic unchanged — and match weights shift by up to the `(u_exact/tf)^w` term, which
D7a measures at 2.31 bits on a realistic case. The parity suite catches it only if baselines are
regenerated in the same PR, which is exactly what §16's automation path does not do.

**Recommendation.** Name sqlglot parity-critical: exact pin *(Stage 0.1 now says so)*, Dependabot ignore,
version recorded in the sidecar header and the run manifest, and **the sidecar regeneration test added to
the dependency-bump ritual** in `DbtBestPractices.md` §16 alongside the D4 gate. Cheapest fix in this
appendix.

---

> **[REVIEW 2026-08-23] RC23 — Currency (2026-08-23).** The manifests this recommendation asks for now
> exist in the companion, and both omit sqlglot: `DbtBestPractices.md` §14.9(b)'s run manifest records
> "resolved dbt / dbt-duckdb / duckdb / splink versions", and its §20.1 baseline manifest records Splink
> and DuckDB versions — neither carries a sqlglot version. Its §4 pin table gains splink and sqlglot rows
> in this pass (flagged there), and the bump ritual still lacks the sidecar regeneration test. The finding
> stands (companion Appendix E registers it unanswered); what remains is now two one-line manifest edits
> with named homes.

### G12 — No versioning, compatibility or deprecation policy

**Severity:** MAJOR · **Attacks:** §1.3, §8 DoD, §7 (Splink drift row) · **Scope:** in-scope

**Claim.** `dbt-er` is a package other projects install, and nothing states how it evolves. `[GREP]`
`semver` → 0, `semantic version` → 0, `changelog` → 0, `deprecation policy` → 0; `breaking change` → 1,
in a sentence about something else.

**Evidence.** `[DOC]` §7 handles Splink drift with "pin exactly; baselines carry a version manifest; a
non-blocking canary job runs against latest" — good for *this* repo's CI, silent on what a consumer
experiences when Splink 4.1 lands: does the package refuse the JSON, warn, or work? Undefined, and it is
the first question a consumer will ask. Nothing defines a breaking change for the package itself (a
contract column, a renamed model, a changed default `er_threshold` — which per M12 changes *results* while
changing no schema), and there is no changelog requirement to record one.

**Recommendation.** A short **§8.x Versioning and compatibility**: semver with an explicit definition of
breaking that includes *result-changing default changes*, not just schema changes; a Splink compatibility
matrix with a stated behaviour outside it (refuse, with G13's error id); a changelog requirement tied to
the divergence log M7 already wants an owner for. Depends on G6 — you cannot define breaking without a
declared surface.

---

> **[REVIEW 2026-08-23] RC24 — Currency (2026-08-23).** Largely implemented in `DbtBestPractices.md` — its
> §19.2 defines breaking for a dbt package (the MAJOR-trigger table includes "a var renamed, or its default
> changed", covering the result-changing-defaults requirement), with SemVer, Keep-a-Changelog
> `CHANGELOG.md`, and a mechanised gate (its 3.60) comparing contracted column sets against the previous
> release tag; its §19.1 supplies the declared surface this finding said it depends on. Still absent from
> both documents: the Splink compatibility matrix with a stated out-of-matrix behaviour (refuse, with
> G13's error id). Re-status DR-20 to match (see the register review note).

### G13 — There is no error catalogue, and one documented trap had no mechanism

**Severity:** MAJOR · **Attacks:** §3.1, M7(d), D7a, D11 rec 5, Stage 12.1 · **Scope:** in-scope

**Claim.** M7(d) asks for an exit-code taxonomy for the *harness*. Nothing specifies how failures surface
to a *user*, and the document has accumulated at least eight distinct precondition failures with no
identifiers, no message contract and no tests.

**Evidence.** `[DOC]` The failures already specified, each in its own section, none with an identifier:
unsupported configuration (Stage 12.1), missing TF snapshot value (D7a rec 2 — "a missing value is an
error, not a fallback"), non-convergence (D4b), capacity exceeded (D11 rec 5), model JSON validation (M13,
`m == 0`/`u == 0` hard error), asymmetric level with unpinned orientation (M1), missing input column (G2),
invalid JSON (G3).

One was worse than uncatalogued. §3.1 records that DuckDB's `log2()` **raises** on zero or negative input,
and states the requirement as *"a macro that emits `log2(<expr>)` on a runtime value must guarantee
positivity"* — with no stated mechanism and no AC, while the clamp that provides it in practice is
specified in a different paragraph for a different reason. The invariant and its proof were not connected.
*(§3.1 now connects them.)*

~~Also **UNVERIFIED and worth checking:**~~ **VERIFIED 2026-08-23 — the timeout does not exist.** `[RUN]`
on the pinned engine, `duckdb==1.5.5`, `select version()` → `v1.5.5`:

| `duckdb_settings()` where name ilike | rows |
|---|---|
| `%timeout%` | **0** |
| `%recur%` | **0** |
| `%interrupt%` | **0** |
| `%limit%` | 3 — `memory_limit`, `pivot_limit`, `write_buffer_row_group_memory_limit` |

**There is no statement timeout, and there is no recursion-depth setting.** The only interruption
mechanism is `Connection.interrupt()`, a Python call from another thread — which is Python at runtime,
unavailable from a profile and unavailable inside `dbt build`. It is not a knob this project can use.

**Two consequences, and M5 and A.2 C6 are corrected accordingly.** First, **M5's guardrail rests entirely
on the in-query iteration cap**, exactly as this finding said to state if the timeout turned out not to
exist. There is no second line of defence, which raises the cap from a belt-and-braces addition to the only
thing standing between a pathological component and an unbounded recursion. Second, the backstop that
*does* exist is `memory_limit`, and it is a **hard failure rather than a timeout** — consistent with D4a's
measurement that `USING KEY` is memory-resident and **OOMs rather than degrading**. A runaway recursion
fails loudly at a bounded memory ceiling; it does not run forever, and it does not take the machine with it.

This also confirms on the *pinned* version what D4 trap 3 recorded — no `max-recursion-depth` setting —
rather than leaving it resting on an earlier recollection.

**Recommendation.** An **error catalogue** with stable identifiers (`ER-001` …), each mapped to M7's
exit-code taxonomy, each with one test that provokes it and asserts the message. ~~Verify the
statement-timeout claim and correct M5 / A.2 C6 either way.~~ **Done 2026-08-23 — see above.** The error
catalogue itself remains open and is Stage 12b work; §2.0's missing-column error and §1.5's five validation
errors are its first entries.

---

### G14 — The capacity check has no home in the DAG

**Severity:** MAJOR · **Attacks:** D11 rec 5, B1 rec 4, D1 corollary · **Scope:** in-scope

**Claim.** `make capacity` reports `er_bytes_per_pair` and derives `er_max_pairs`. A Makefile target
reports; it does not stop a build. The check must fire **before stage 3 materialises**, and nothing says
what mechanism does that.

**Evidence.** `[DOC]` D11 rec 5 and B1 rec 4 both specify the measurement and neither specifies the
enforcement point. B1's own failure scenario is a hard `OutOfMemoryException` "mid-`dbt build` … with
stages 0–4 already materialised" — i.e. the failure this prevents happens *inside* a build, so the guard
must live inside the build too: `on-run-start`, a pre-flight model, or a test that gates the DAG.

Worth stating explicitly: the estimate needs an input **row count**, which is a run-time query. That is
legal under D1's corollary — which bans introspecting *column names*, for reasons specific to contracts
and unit tests — but the corollary reads absolutely ("never introspected"), and someone will read it as
forbidding the row count. *(D1 now carries the clause.)*

**Recommendation.** Name the enforcement point and make exceeding `er_max_pairs` a G13 error id.

---

### G15 — The gray band is recommended twice and never reaches the model layer · **CLOSED 2026-08-23**

> **Closed by §1.7** (DR-09), which adopts this finding's recommendation in full: two thresholds, the
> half-open band, gray pairs excluded from the edge relation, and a `review_pairs` output the platform
> consumes. All three structural questions are answered — `thr_auto_merge` feeds the edges, gray pairs are
> excluded from the graph, and they are emitted to `review_pairs` keyed by `thr`. M16's dimension is
> settled in the same section (DR-08), which is what this finding says compounds it.

**Severity:** MAJOR · **Attacks:** M12 rec 5, A.3 Group 1, D4, Stage 6, M16 · **Scope:** `[interface]`

**Claim.** Two separate Appendix A sections require two thresholds instead of one. Neither §5, D4 nor
Stage 6 reflects what that does to the clustering input — and under the incumbent's semantics it changes
it materially.

**Evidence.** `[DOC]` M12 rec 5: "Replace the single knob with `er_threshold_auto_merge` /
`er_threshold_review_low`: a single threshold cannot express 'uncertain'". A.3 Group 1 records the
incumbent's contract: "half-open `review_low ≤ p < auto_merge`; gray-band pairs are **not** clustered."
Meanwhile D4 seeds clustering from all edges at `var('er_threshold')` and Stage 6's ACs are written
against one threshold. The unresolved questions are structural: which threshold feeds `int_edges`;
whether gray-band pairs are excluded from the graph (changing every Stage 6 partition and therefore every
Stage 6 AC); and where they are emitted for the platform to consume. M16 compounds it by making the
threshold a **dimension** — so the composite key becomes `(thr, unique_id)` and "which threshold" becomes
"which *set*, with which semantics per band".

**Failure scenario.** Stage 6 is built and gated against a single threshold. The gray band arrives as an
interface requirement during Stage 12 integration, and it is not a var change — it changes the clustering
input, the cluster contract, and every AC that references a partition.

**Recommendation.** Decide it in §5 before Stage 6 starts and record it in the register: two thresholds
with the half-open band, gray pairs excluded from `int_edges`, and a `review_pairs` output relation the
platform consumes. `[interface]` — the review queue stays out of scope per §1.3, but the two-threshold
contract and the excluded-pairs relation are the engine's to provide.

---

### G16 — Baseline artifact lifecycle is unspecified, and the storage model does not scale to the fixture the ACs require

**Severity:** MODERATE · **Attacks:** Stage 0.3, Stage 0.4, M13, M18 · **Scope:** in-scope

**Claim.** Baselines are "generated once, reviewed, committed, hashed" (B5 rec 1). That works for
`fake_1000`. It does not work for the 1M-record fixture that B1, B4 and M11's runtime gates all depend on
— that is gigabytes of parquet in git history, per model, per stage, per model-JSON in M13's library
matrix.

**Evidence.** `[DOC]` M13 specifies a library of **eleven** stated cells, each generated by save→reload,
hashed and committed. Stage 0.3 dumps "every intermediate as parquet". B4 and M11 both write ACs against a
**1M fixture**. Multiplying those out is the finding: the storage strategy is unstated, and so are
retention, size caps and the regeneration trigger.

**Recommendation.** State the storage tier per fixture size (committed in-repo for small fixtures; object
store or LFS with a manifest for large ones), the retention rule, and the identity a baseline is bound to
— `(Splink version, sqlglot version, model JSON sha, platform triple)`, which is G5's and G11's field list
too.

---

> **[REVIEW 2026-08-23] RC25 — Currency (2026-08-23).** `DbtBestPractices.md` §20.1 now supplies most of
> this — the manifest (its 3.62), make-target-only regeneration, the human-readable diff report as the
> review surface, baseline-only PRs. Narrow this finding to its residue: the storage tier for the 1M
> fixture is still undecided (§20.1 only observes the 4 MB per-file cap "is not a limit anyone chose"), no
> retention rule exists, and the mandated manifest binds only half the identity — sqlglot version and the
> platform triple are absent from its field list.

### G17 — The operational envelope is unstated

**Severity:** MODERATE · **Attacks:** §5, §7 Q1, M17 · **Scope:** `[interface]`

**Claim.** No cadence, no end-to-end wall-clock budget, no upstream contract, no concurrency policy.
`[GREP]` `cadence` → 0.

**Evidence.** `[DOC]` Runtime gates exist per model (M11's absolute Stage-6 gate, M5's cap, B4's <10%
incremental AC) and none composes into an end-to-end budget, which is the number an operator needs.
Nothing states what must be true of ingest before a run starts. And M17 establishes that a second process
connecting to the same DuckDB file **hard-fails** with a lock error — which makes "two runs at once" an
outage mode with no stated guard, and it is the ordinary consequence of a slow run overlapping the next
schedule.

**Recommendation.** A short **§5.0 Run contract** (M7 already proposes the name): cadence, end-to-end
budget, the upstream precondition, and an explicit single-writer statement with the guard that enforces
it. `[interface]` for scheduling; in-scope for the concurrency guard, because the failure is the engine's.

---

> **[REVIEW 2026-08-23] RC26 — Name collision (2026-08-23).** `DbtBestPractices.md` §14.9 has since taken
> the title "The run contract" for M7's scope — `er_run_id`, `_er_run_manifest`, the idempotency key, the
> exit-code taxonomy. The §5.0 proposed here is a different contract: cadence, end-to-end budget, upstream
> precondition, single-writer guard. Rename one (e.g. "§5.0 Operating envelope") or scope both explicitly,
> before the first cross-reference conflates them.

### G18 — The v1 supported-configuration matrix was never applied back to the body

**Severity:** MODERATE · **Attacks:** §1.2, D3, S2, M1, A.2 C4, Stage 12.1 · **Scope:** in-scope

**Claim.** B3 and Stage 12.1 narrow v1 to `dedupe_only`, VARCHAR ids, no `source_dataset`, plain equi-join
rules, no `arrays_to_explode` — and observe that this makes several of the document's hardest sections
**dead code for the migration target**. The body was never re-marked, so material that does not ship in v1
still reads as normative on first pass.

**Evidence.** `[DOC]` Stage 12.1 says it plainly: "every hard case in D3 (`-__-` composite, lexicographic
ordering) and S2 (`two_dataset_link_only`) is **dead code for the actual migration target**". Yet S2 sits
in §1.2 as a headline scope limitation, D3's link-type table is presented without a v1/v2 marker, M1
exists entirely to protect a configuration v1 does not support, and A.2 C4 is an open question about it.
Open Question 3 still asks whether to support `two_dataset_link_only` at all — while Stage 12.1 has
already answered no for v1.

**Failure scenario.** Not a defect — a schedule cost. Stage 1 and Stage 3 are the critical path (M21), and
a reader building them cannot tell which of D2/D3's four WHERE arms are v1 work. The most expensive form
is building and testing the composite-id path for a target that has no `source_dataset` column.

**Recommendation.** Tag each affected section `v1` / `v2`, resolve Open Question 3 to match Stage 12.1, and
hoist the supported-configuration matrix out of Stage 12 into §1 where scope belongs. Purely editorial; it
shortens the critical path.

---

### G19 — There is no development loop

**Severity:** MODERATE · **Attacks:** §5, §6.4, §2 (`compare_two_records`) · **Scope:** in-scope

**Claim.** Twelve stages, multi-minute builds, and no statement of how a person iterates. No sampled or dev
mode, no documented pair-level debugging workflow, no expected local runtime.

**Evidence.** `[DOC]` `[GREP]` `sample` → 5, all about `USING SAMPLE` for u-estimation (D9), none about
running the pipeline on a subset. `compare_two_records` — the natural "why did this pair score that?" tool
— gets one row in §2 and no further mention (G10). M17(c) mentions `dbt run --empty` but as a unit-test
precondition, not a development affordance.

**Recommendation.** Name a dev mode (a sample var or a documented fixture-size ladder), and write the
pair-level debugging workflow: given two ids, get gammas, per-comparison `bf_*`, the TF adjustment and the
final weight — exactly what D11 rec 2 already retains and what A.3's "evidence audit" row requires. The
capability exists; the path through it is undocumented.

---

### G20 — Licensing and attribution are absent · **evidence gathered 2026-08-23; the §1 paragraph is PE-2**

**Severity:** MODERATE · **Attacks:** §1, §8 DoD · **Scope:** in-scope

**Claim.** `[GREP]` `licen` → 0, `copyright` → 0. The package reproduces Splink's rendered SQL,
reimplements its algorithms from source study, quotes its source line-by-line throughout this document,
and **deliberately replicates one of its defects** (S4's `min(match_key)` VARCHAR bug). The repository has
a `LICENSE`; the design says nothing about the relationship.

**Evidence.** `[DOC]` S4 commits to replicating the bug; §A.2's scope statement positions the output as a
Splink-compatible artifact. ~~**UNVERIFIED**~~ — **verified 2026-08-23 from two independent sources, as
this finding instructed (from package metadata, not from memory):**

| Source | Result |
|---|---|
| PyPI metadata for `splink==4.0.16` | `license_expression: MIT` |
| `github.com/moj-analytical-services/splink` at tag **`v4.0.16`**, `LICENSE` | `MIT License` · `Copyright (c) 2020 Ministry of Justice` |

This repository's own `LICENSE` is also MIT (`Copyright (c) 2026 AthVIN`), so the licences are compatible
and no copyleft obligation attaches. **The package vendors no Splink code**; it reimplements the
transformations from source study and passes through SQL strings that the *consumer's own trained model*
contains. What it does carry is attribution, which is the substance of the recommendation below.

**Recommendation.** One paragraph in §1: Splink's license, the attribution this package carries, and what
its own `LICENSE` means for a consumer. Low effort, and annoying to retrofit after external adoption.

---

### G21 — There is no decision register

**Severity:** MODERATE · **Attacks:** the document as an artifact · **Scope:** in-scope

**Claim.** The programme has reversed six load-bearing decisions from v1 to v2, several more across
Appendix A, and expects further reversals (§7 Open Questions 1–4, §A.6's five). That history was recorded
in **prose**, distributed across whichever sections were edited. `[GREP]` `changelog` → 0. G1 is the direct
consequence: the materialisation decision had four live statements because no single row held its current
value.

**Recommendation.** §B.3 seeds the register. Maintaining it costs one row per decision and is the only
structural defence against G1 recurring at the next revision.

---

## B.2 Cross-document reconciliation

`DbtBestPractices.md` §1 sets precedence between the documents. These four are places where following it
produces the wrong answer, or where the two documents simply disagree. **None of these is fixed by this
merge** — they require editing the companion document, which is a separate act.

> **[REVIEW 2026-08-23] RC27 — The separate act has since happened.** `DbtBestPractices.md` v2 closes R1
> (its §7 is now D11's contract), R2 (its §1 tolerance row routes to A.4 alone, citing this appendix), and
> R4 (its §8.4 carries the `isfinite` qualifications and the `bf_*` exclusion, citing R4 by name); its
> Appendix E records all three. Only R3 remains open — and R3 is an edit to *this* document, not the
> companion. Meta-point: reconciliation status now lives in two inventories (this section and the
> companion's E.1 table), and for one revision they disagreed — the same two-normative-inventories
> pathology R3 itself diagnoses, one level up. Make §B.3 the single cross-document status ledger (one row
> per R-item, as DR-01 already does for R1) and reduce the companion's E.1 table to a pointer, so the next
> closure cannot leave a stale "still open" on the other side.

### R1 — Materialisation: `DbtBestPractices.md` §7 implements a superseded decision

§7 is built entirely on B1's recommendation — `ephemeral` for `er_int_comparison_vectors` and the wide half
of `er_int_scored_pairs`, `er_materialise_intermediates` default `false`, a waiver mechanism, a
`dbt-bouncer` exemption list, and the honest note that ephemeral models lose contracts and timing rows.
D11 overrides that with `table` everywhere plus narrowness. Under the *inter-document* precedence rule
("measured findings in Appendix A → this document"), B1 outranks §7 and §7 is *implementing* B1, so
nothing in the old rules flagged the conflict. The **intra-document rule added in the v2 note resolves
it**: D11 wins. `DbtBestPractices.md` §7's table and its waiver machinery still need rewriting, and note
what falls out with them — `materialisation_waiver_reason`, the bouncer `exclude:` list, and the "CI runs
with `er_materialise_intermediates: true`" rule all become dead once there is no ephemeral path.

### R2 — Tolerance: §6.1 is a strict subset of A.4, and both are cited as one

`DbtBestPractices.md` §1 routes "what tolerance is a parity failure?" to "`DesignDoc.md` §6.1 / A.4" as if
they were a single source. They are not. §6.1 lacks three things A.4 has: the relative term
(`1e-9 + 1e-12·|mw|` against a bare `1e-9`), the rule that probability parity is **vacuous above
`mw = 54`** and must be asserted as exact `p == 1.0` there, and the row stating that **float aggregates
are not a gate at all**. Someone implementing from §6.1 writes a materially weaker harness. **Merge into
one table with one home**; until then §6.1 now says A.4 is the one to implement from.

> **[REVIEW 2026-08-23] RC28 — Partially closed by `DbtBestPractices.md` v2 (2026-08-23).** Its §1
> tolerance row now reads "implement from A.4, not §6.1", with a note stating the three omissions and
> citing this finding. The remaining act is this document's — merging §6.1 and A.4 into one table with one
> home — and it is R3's sibling: an edit here, not there. When merging, include the fourth difference the
> three-item enumeration above misses: §6.1's clusters row says partition equality where A.4 and Stage 6's
> AC mandate label equality as primary (see the review note at §6.1).

### R3 — Stage list: §5 and A.5 are two normative inventories · **CLOSED 2026-08-23**

> **Closed by the §5 revision of 2026-08-23**, executed from RC29's enumeration below rather than from this
> finding's own shorter list. §5 is now the single inventory; A.5 is retained as evidence and marked stale
> on conflict. DR-11 moves from `CONFLICT` to `CURRENT`. With R1, R2 and R4 already closed, **Appendix B.2
> carries no open reconciliation item.**
>
> Two consequences this finding names are now real and are worth following through separately: **G6's model
> inventory** and **G21's register** both key off the stage list, and the register is done — G6's §2.1
> model inventory is not, and is the remaining half of that finding.

§5 absorbed Stage 12 from B3 but not A.5's other structural changes: Stage **2b** (record lifecycle),
**6b** (entity identity), **12b** (provenance & observability), and the instruction to **move evaluation
earlier so it can gate Stages 3 and 6** — which is M12's central point and is inert while Stage 10 stays
where it is. A reader planning from §5 builds a different programme from one planning from A.5.
**Reconcile to one list**; G6's inventory and G21's register both key off it.

> **[REVIEW 2026-08-23] RC29 — This delta list is materially incomplete, which matters because R3's closure
> will be executed from it.** **Executed 2026-08-23: every item enumerated below landed in §5**, and this
> note — not R3's shorter list — was the scope used, which is why it is retained rather than struck.
> Besides 2b, 6b, 12b and the evaluation move, §5 also lacks: A.5's **0.7**
> (the comparator sensitivity suite — the one item `DbtBestPractices.md` §12.7 says must be built *before*
> 0.4 freezes baselines against it); the live half of **0.6** (measure B/pair and publish `er_max_pairs` —
> the decide-clause is superseded by D11, but the measurement is exactly what D11's follow-through table
> and rec 5's `make capacity` require, and no §5 stage schedules it); **0.3's extensions** (ground-truth
> labels and training traces in the baseline format — "retrofitting after Stage 0.4 freezes it is the
> expensive path"); the **critical-path / day-one-parallelism statement** A.5 explicitly says to "say in
> §5"; and the per-stage extensions §5 never absorbed — among them the Stage 3 blocking-recall floor
> (M12), Stage 6's absolute runtime gate and per-model ACs (M11), Stage 7's M19 items, Stage 8's row
> stamps and measured `<10%` AC (M8/B4), and Stage 9's trace oracle (B5) — plus one direct textual
> conflict: §5 Stage 4 requires "a boundary fixture for **every distinct threshold constant**" where A.5
> relaxes to reachable constants with unreachable ones documented. List them here so reconciling to one
> list closes all of R3, not most of it.

### R4 — Constraints versus degenerate arithmetic

`DbtBestPractices.md` §8.4 proposes `CHECK isfinite(match_weight)` and `match_probability between 0 and 1`.
§3.4 emits `cast('Infinity' as float8)` for a `u = 0` level, and §3.1 documents a NaN path in which
`greatest(NaN, 1e-300) = NaN` yields a saturated weight. The clamp should keep `match_weight` finite in
every reachable case, and M13 recommends hard-erroring on `m == 0` / `u == 0` at validation — so the
constraint is probably safe. Two things follow: **verify it against a deliberately degenerate model**
rather than assuming, and note that `bf_*` columns can legitimately hold `Infinity`, so the same CHECK must
**not** be extended to them. Second-order: under R1's resolution the contracts return to the two models
that lost them, which is the outcome M2 requires — worth stating in §8.4 so the connection is not
rediscovered later.

---

## B.3 Decision register

The artifact G1 and G21 ask for. One row per decision made, reversed, or pending. Status: **CURRENT** (in
force), **SUPERSEDED** (with the pointer), **OPEN** (needs an answer), **CONFLICT** / **MISSING** (blocking).

| id | Decision | Status | Value in force | Notes |
|---|---|---|---|---|
| DR-01 | Materialisation of intermediates | **CURRENT** (was CONFLICT) | D11: `table` everywhere, narrow | Supersedes A.1 B1 rec 1, A.5 Stage 0.6, A.7 Thesis 2 — all now marked. R1 closed by `DbtBestPractices.md` v2 (its Appendix E); its §7 now implements D11 |
| DR-02 | Model JSON ingestion | CURRENT | `env_var`, parsed at parse time (D1) | Supersedes v1's `load_file` and v2's `--vars` draft |
| DR-03 | Term frequency source | CURRENT | Frozen snapshot by default (D7a) | Supersedes §3.5's implied live recompute; §A.6 Q4 answered. Cost: **G7**; erasure: **G4** |
| DR-04 | Clustering formulation | CURRENT | `USING KEY`, delta-driven, monotone guard (D4) | Supersedes v1's `recurring.`-driven form |
| DR-05 | Clustering performance path | OPEN (scheduled) | Recursive CTE for parity; D4b after Stage 6 | §A.6 Q3 |
| DR-06 | Scoring arithmetic space | CURRENT | Linear product, single `log2`, Splink clamp (§3.1) | Supersedes v1's log-space sum |
| DR-07 | Threshold predicate | CURRENT | `match_probability >= t` on the materialised column (§6.1 / B2) | — |
| DR-08 | Threshold as var or dimension | **CURRENT (2026-08-23)** | **The dimension** (§1.7). `edges_by_threshold` and `entity_clusters` carry `thr` as a real column from a `thresholds` relation, cast **DOUBLE**; `er_thresholds` defaults to one row so production cost is unchanged | Closes `DbtBestPractices.md` **B.2**. M16's `[RECON]` verified the composite-key form and its refinement chain. Cross-threshold monotonicity is not expressible as a dbt test under the var approach. **Delegated authority** |
| DR-09 | One threshold or a gray band | **CURRENT (2026-08-23)** | **A gray band** (§1.7). `thresholds` is a relation of `(thr_auto_merge, thr_review_low)` pairs; `[thr_review_low, thr_auto_merge)` is gray, emitted to `review_pairs` and **never clustered**; `thr_review_low` defaults to `thr_auto_merge`, making the band empty | Closes **G15**. Matches the incumbent's half-open contract (A.3 Group 1) as DR-14's posture requires. **Costs no parity** — gray pairs are below the threshold, so Splink excludes them from clustering too; `review_pairs` is purely additive. **Delegated authority** |
| DR-10 | Tolerance policy | CURRENT, split across two tables | A.4's table | **R2** |
| DR-11 | Stage inventory | **CURRENT (2026-08-23)** | **§5 is the single inventory.** A.5 absorbed into it and retained as evidence; A.5 is stale where the two disagree | Closes **R3**, executed from RC29's enumeration. Supersedes A.5-as-inventory, §5 Stage 4's "every distinct threshold constant" AC, §5 Stage 9's EM AC, and the single-boolean stage-decoupling mechanism. Adds Stages 2b, 6b, 12b, sub-stages 0.0/0.6/0.7/0.8/0.9, and the critical path. **Delegated authority** — see the F13 note at §5 |
| DR-12 | `entity_id` vs `component_label` | **CURRENT (2026-08-23)** | **`component_label`** (§1.6). Deterministic for a fixed graph and threshold; **not** stable across runs; removed as a key from every downstream model. Stage 6b collapses to an interface contract — the engine publishes the edge set and the partition, the platform owns permanence | Closes **M6**, whose `[RECON]` measured one insert relabelling 5/5 pre-existing records. Its trigger fired 2026-08-20 when DR-14 went CURRENT; §A.6 Q1 had already declared this binding (RC16). **Delegated authority** |
| DR-13 | Runtime substrate | **CURRENT (2026-08-23)** | **The harness reads only parquet and never opens the database; dbt keeps a file database.** Models the harness compares are exported by a `COPY` post-hook in `integration_tests/`, never in the package (3.52) | Closes `DbtBestPractices.md` **B.1**, adopting M17 rec (a)'s *harness contract* but **rejecting its `:memory:` substrate**: C.7's build job runs five separate dbt invocations, so an in-memory database would make `dbt seed` unimplementable and `dbt docs generate` catalog an empty database — a vacuously-passing catalog tier. D11 unaffected; the export is a post-hook, not a materialization. **Delegated authority** |
| DR-14 | Product posture | **CURRENT (2026-08-20)** | **Engine the platform calls** | §A.6 Q1, resolved and marked |
| DR-15 | Supported configuration for v1 | CURRENT, not propagated | `dedupe_only`, VARCHAR id, no sds, equi-join only | Stage 12.1; **G18** |
| DR-16 | Input contract | **CURRENT (2026-08-23)** | **§2.0.** One relation named by `er_input_relation` (the package ships zero sources); v1 arity is one table because Stage 12.1 forbids `source_dataset`, and the consumer owns the union when v2 needs one; `unique_id` VARCHAR / NOT NULL / UNIQUE; declared columns are a parse-time var; a missing column fails at **compile time naming the column**; the three preconditions ship as tests **in the package** | Closes **G2**, **G9**. Hoists `unique_id`'s VARCHAR requirement out of Stage 12.1. The consumer owns input ordering — a relation name creates no DAG edge. **Delegated authority** — see §2.0 |
| DR-17 | Model JSON trust boundary | **CURRENT (2026-08-23)** | **Untrusted input, validated once at compile time in the sidecar (§1.5).** D6's list is a closed allow-list checked against the *parsed* tree; non-deterministic functions, subqueries and statement terminators rejected; input bounded; `er_model_sha` is the hash of the **validated** artifact and `dbt build` refuses a JSON without one | Closes **G3**. Supersedes principle 4's unqualified "the model JSON is the contract" and D6's "lint whitelist" framing. Accepted cost: a Splink-produced JSON can fail validation — a supported-configuration boundary, not a bug. **Delegated authority** — see §1.5 |
| DR-18 | Data classification & retention | **MISSING** | — | **G4** |
| DR-19 | Parity claim's validity domain | **MISSING** | — | **G5** |
| DR-20 | Package versioning & compatibility | **MISSING** | — | **G12**, depends on **G6** |
| DR-23 | **ST05 under the CTE ban** | **CLOSED 2026-08-23 — option (c), the lateral column alias. ST05 is NOT relaxed.** | `forbid_subquery_in` keeps its configured scope; Stage 5 computes `bf_clamped` once as a lateral column alias and derives `match_weight` and `match_probability` from it | Stage 0.8's spike **ran** and answered the question the recommendation was contingent on. `[RUN]` on DuckDB 1.5.5: a lateral column alias evaluates the expression **once per row** (1,000 evaluations over 1,000 rows), satisfying D11 rec 4 with no subquery, no CTE and no new pair-grain model. Pinned by `harness/test_duckdb_expression_semantics.py`. **The recommendation was "(a), after testing (c)" — (c) tested successfully, so (a) is not adopted and 3.15's determinism rule set is untouched.** Second, unlooked-for finding: DuckDB 1.5.5 **eliminates a repeated identical subexpression** (three calls → 1,000 evaluations, the planner splitting the projection), so B.8's cost argument against repetition is unfounded on this engine. (c) is still preferred — it states the intent in the SQL instead of depending on an optimiser pass — but the argument is recorded as wrong rather than left standing because its conclusion survived |
| DR-24 | **Companion CTE inside `WITH RECURSIVE`** | **CURRENT (2026-08-23)** | **§7.3.1 is scoped, not waived**: a `WITH RECURSIVE` clause may also contain a companion CTE that is a **pure single-source projection of a `ref()`ed relation** — an adapter, not a stage | `DbtBestPractices.md` **B.9**, opened and closed together (RC38, RC2). D4's `bidir` is an orientation-doubling projection and undirected-graph recursion always needs one, so the ban bound on the flagship model from day one. Enforcement shares 3.68's parser gap. **Delegated authority** |
| DR-22 | **Quality floor and its owner** | **CURRENT (2026-08-23)** | **§1.8.** Committed per-fixture `er_blocking_recall_floor` (two-sided), `er_f1_floor` and `er_max_cluster_size`, set at Stage 0.4 from the **fixed** model and gating from Stage 2 onward. **No package default threshold** — an unset `thr_auto_merge` fails compilation. Floors live in a file with a `CODEOWNERS` entry | Answers §A.6 **Q5**, which had no register row and no owner. Evidence: M12's `[RECON]` — the frozen model measures F1 0.7138 / recall 0.5550, two rules take it to 0.9809 / 0.9173, and the removed default t = 0.9 costs ~330 true pairs for zero precision benefit. **Delegated authority** |
| DR-21 | Unit-test coverage policy | **CURRENT (2026-08-23)** | **Every model; cases decided when the model is written (D12)** | Supersedes M17 rec (c)'s five-model scope — marked there. No automatic exemption class: a recursive-SQL or toolchain-regression exclusion is a dated, reasoned waiver under `DbtBestPractices.md` 3.43. Gated by its 3.20 |

Rows marked CONFLICT or MISSING are the ones to close before writing models. Rows marked OPEN have a
stated owner in Appendix A or in `DbtBestPractices.md` Appendix B.

> **[REVIEW 2026-08-23] RC30 — Register currency, and one row fixed.** DR-01's note previously read
> "`DbtBestPractices.md` §7 still stale: **R1**" — stale itself as of the companion's v2, which rewrote §7
> to D11's contract and closed R1 (its Appendix E); the note is updated above **(Fixed, F7)**, because the
> register is the one artifact that must not carry a superseded status row. Three MISSING rows now also
> overstate. **DR-18**: companion §20.4 rules fixtures/seeds synthetic-only (its 3.55) and its B.7
> addresses observability redaction — the classification and D7a erasure trigger remain. **DR-19**:
> companion §22.1/3.59 states the validity domain (float gates anchored to linux/amd64) — the
> baseline-manifest platform field and G5 rec 3's cross-platform measurement remain. **DR-20**: companion
> §19.1–19.2 supply the surface, SemVer, changelog and release gate — the Splink compatibility matrix
> remains. Only DR-16 and DR-17 are still MISSING in full, as the companion's Appendix E confirms. Two
> rows are absent outright: the companion's open decisions **B.1** (runtime substrate — its own text says
> it must be settled before the Stage 0.3 harness lands; DR-13 should carry a "blocks Stage 0.3" marker
> like DR-09's) and **B.8** (ST05 / CTE-ban / D11 rec 4 — blocks Stages 1 and 5) postdate this register
> and need DR entries.

---

## B.4 What this pass deliberately does not re-raise

In the style of A.8, so nothing here gets re-litigated. Every one is **already covered**, and where this
appendix touches the same area it is from a different angle:

| Already handled | Where | This pass's relation to it |
|---|---|---|
| Resident-byte cost of one model per CTE | B1, D11 | G1 attacks the *contradiction between the answers*, not the answer |
| Edge set as the binding parity gate | B2, A.4 | G5 scopes the gate's validity domain; the gate itself stands |
| Frozen TF and cutover | B3, D7a, Stage 12 | G4 adds the erasure interaction; G7 adds the refresh cost |
| Incremental blocking cost | B4 | Untouched |
| Training-oracle reproducibility | B5 | Untouched |
| Asymmetric levels / orientation | M1 | G18 notes it is v2-only work under the v1 matrix |
| JSON-derived columns and contracts | M2 | G6 adds the inventory M2's split implies |
| `state:modified` blindness | M3, M8 | G7 takes the operational consequence further |
| Stage decoupling mechanics | M4 | Untouched |
| Diameter guardrail | M5 | G13 flags only the unverified statement-timeout half |
| `entity_id` churn | M6 | Registered as DR-12; the engine posture answers it |
| Run identity and provenance | M7 | G8 adds state rollback, which M7 explicitly does not cover |
| Coverage metric | M9 | Untouched |
| Comparator sensitivity | M10 | Untouched |
| Stage-6 model ACs | M11 | G10 extends the same audit to four more models |
| Quality floor and thresholds | M12 | G15 carries the two-threshold decision into the model layer |
| Determinism preconditions | M15 | G3 extends the non-determinism ban to the untrusted JSON |
| Runtime substrate | M17 | G17 adds the concurrency consequence |
| Survivorship depth | M19 | G10 notes `golden_records` still has no declared grain |
| Stewardship interface | M20 | Untouched; same `[interface]` posture |
| Effort sizing | M21 | G18 notes the v1 matrix shortens the critical path |

---

## B.5 How to verify Appendix B

1. **Re-run §B.0.2's grep suite against the pre-`# Appendix B` range.** Every absence claim expires the
   moment its *recommendation* is implemented. That is the intended lifecycle: a finding that cannot
   expire is an opinion.
2. **Check every `[DOC]` citation by section id.** Citations are deliberately by id, not line number, so
   they survive edits to this document.
3. **Two claims are UNVERIFIED by design and must be settled before they are treated as facts:** Splink
   4.0.16's license terms (**G20**), and whether DuckDB 1.5.5 / dbt-duckdb 1.11.0 expose a statement
   timeout (**G13**, affecting M5 rec and A.2 C6).
4. **One claim needs a measurement this pass could not make:** cross-platform float parity (**G5**). It is
   an afternoon of CI time and it either closes the finding permanently or changes the tolerance table.
5. **Posture check.** Every finding is scoped in-scope or `[interface]` under DR-14. If the posture is ever
   revisited, the `[interface]` findings — G4's erasure mechanism, G8's retention policy, G15's review
   queue, G17's scheduling — are the ones that change class.
6. **What this merge changed in the body**, so it can be reviewed as a diff rather than rediscovered: the
   intra-document precedence rule (v2 note); `[SUPERSEDED by D11]` markers at A.1 B1 rec 1, A.5 Stage 0.6
   and A.7 Thesis 2; §A.6 Q1 marked resolved; the sqlglot pin at Stage 0.1; and pointers at §2, §3.1, D1,
   D6, D7a, D11 and §6.1. **No open decision was resolved unilaterally** — every CONFLICT, OPEN and
   MISSING row in §B.3 is still yours to close.
