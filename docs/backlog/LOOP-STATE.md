# Loop state

**Purpose:** the durable memory of the build loop that runs this programme to `GOAL.md`'s definition of
success. It survives context loss, session restart and machine reboot; the loop reads it at the start of
every iteration and writes it at the end. If this file and a conversation disagree, **this file is right.**

**Not normative.** Like `GOAL.md`, it settles nothing. `docs/DesignDoc.md` and `docs/DbtBestPractices.md`
own every decision, tolerance and standard recorded here; this file records *status*, never *content*.

**Last updated:** 2026-08-23

---

## Operating mandate

| Setting | Value | Granted | Notes |
|---|---|---|---|
| Decision authority | **Full delegation** | 2026-08-23 | The loop closes open `§B.3` / Appendix B rows itself. This deliberately overrides `DesignDoc.md` §B.5 item 6 and the three `er-*` skills' hard rule. Every such decision carries the delegation trailer below and is reversible by reopening the row. |
| Merge mandate | **Autonomous** | 2026-08-23 | `ship-pr` merges without asking once every step-5 precondition holds. **Expires with the session** — re-confirm on resume. |
| Completion target | **All five `GOAL.md` criteria, including §8 DoD 3** | 2026-08-23 | The loop holds across the ≥10-day nightly window rather than stopping short of it. |

**Delegation trailer** — every decision PR carries it verbatim, so the delegated calls are greppable and
none of them becomes invisible precedent:

```
Decided under delegated authority 2026-08-23.
Recommendation source: <doc §, or "none — rationale written in full">.
Reversible: reopen this row.
```

---

## The five criteria

The loop's termination test. Re-evaluated every iteration.

| # | Criterion (`GOAL.md`) | Status | Evidence |
|---|---|---|---|
| 1 | A fresh clone runs the whole pipeline, zero Python in the run | 🟡 skeleton builds | `dbt build` green on 1 model, 2 data tests, 3 unit tests (PB-1) |
| 2 | Parity with Splink demonstrated, bounded, published | ❌ not started | — |
| 3 | Every deliberate divergence logged and pinned by a test | ❌ not started | — |
| 4 | Every model unit-tested, written with the model | 🟡 1/1 models | `er_thresholds` shipped with 3 unit tests in the same PR (PB-1) |
| 5 | The gates enforce the above unattended | ❌ not started | — |
| — | §8 DoD 3 — ten green nightly differential runs | ❌ 0/10 | — |

**On criterion 1's command.** §8 DoD 2 states it as `dbt build --vars "{er_model: …}"`. **D1 supersedes
that**: the model JSON arrives through `env_var('DBT_ER_MODEL_JSON')`, because `--vars` fails at
`MAX_ARG_STRLEN` (128 KiB, ~330 levels) and is unreachable from `schema.yml`. Fixing the stale DoD text is
part of PA-1.

---

## Decision register mirror

Status only. `DesignDoc.md` §B.3 remains the register; this table exists so the loop can answer "is the next
ticket blocked?" without re-reading 6,700 lines. **Class** is `er-backlog-preflight`'s taxonomy:
A = CONFLICT, B = MISSING, C = OPEN with a fired deadline, D = OPEN and scheduled.

| Row | Class | Blocks | Status | Closed by |
|---|---|---|---|---|
| DR-11 stage inventory | **A** | every stage | ✅ **CURRENT 2026-08-23** — §5 is the single inventory | PA-1 (#2) |
| DR-17 model JSON trust boundary | **B** | Stage 1 | ✅ **CURRENT 2026-08-23** — untrusted input, validated in the sidecar (§1.5) | PA-3 (#4) |
| DR-16 input contract | **B** | Stage 1, anything reading `stg_input` | ✅ **CURRENT 2026-08-23** — §2.0, one relation, three preconditions as package tests | PA-4 (#5) |
| B.1 / DR-13 runtime substrate | **C** | Stage 0.3 | ✅ **CURRENT 2026-08-23** — harness reads only parquet; dbt keeps a file database | PA-5 (#6) |
| B.8 ST05 under the CTE ban | **C** → **DR-23** | Stages 1 and 5 | **row created 2026-08-23; OPEN.** Value set by Stage 0.8's spike, so (a) is not adopted by default | PA-9 (#10) |
| DR-12 `entity_id` vs `component_label` | **C** | Stage 6b, the label column across Stage 6 | ✅ **CURRENT 2026-08-23** — `component_label`; Stage 6b is an interface contract, not a build | PA-6 (#7) |
| DR-08 / B.2 threshold as var or dimension | D | Stage 6 *contract* | ✅ **CURRENT 2026-08-23** — the dimension | PA-7 (#8) |
| DR-09 / G15 one threshold or a gray band | D | Stage 6 | ✅ **CURRENT 2026-08-23** — two thresholds, gray band to `review_pairs`, never clustered | PA-7 (#8) |
| A.6 Q5 quality floor | — → **DR-22** | Stage 10's ability to gate | ✅ **CURRENT 2026-08-23** — committed per-fixture floors, no default threshold, `CODEOWNERS` ownership | PA-8 (#9) |
| B.9 companion-CTE collision (RC38/RC2) | — → **DR-24** | Stages 6 and 9 | ✅ **CURRENT 2026-08-23** — §7.3.1 scoped, not waived | PA-9 (#10) |
| DR-05, DR-18/19/20, B.3, B.5, B.6, B.7 | D | their own stages | open, scheduled | PA-11 |

Rows already **CURRENT** and not tracked here: DR-01, DR-02, DR-03, DR-04, DR-06, DR-07, DR-10, DR-14,
DR-15, DR-21.

---

## Queue

Ids are `PA-n` … `PE-n`, prefixed because bare `A`–`E` and `D1`–`D12` already mean Appendix A–E and
decisions D1–D12 in these documents. Full descriptions live in the approved plan.

**In flight:** PB-2/PB-3 — the local gate loop: Makefile, `.sqlfluff`, `.sqlfluffignore`, `.yamllint.yml`.

**Done:** PL-0 (#1) · PA-1 (#2) · PA-2 (#3) · PA-3 (#4) · PA-4 (#5) · PA-5 (#6) · PA-6 (#7) · PA-7 (#8) · PA-8 (#9) · PA-9 (#10) · PB-1 (#11, `d507ee0`) · PB-2/PB-3 (#12)

**Phase A is complete after this.** What remains open is open *by schedule*, not by omission: DR-05 (D4b after Stage 6), DR-23 (waits on Stage 0.8's spike), DR-18/19/20 (MISSING-in-part per RC30, each scoped to its residue), and B.3/B.5/B.6/B.7 (each tied to a stage that has not run).

**Next, in dependency order:**

| Phase | Items |
|---|---|
| A — close the register | PA-1 → PA-2 → PA-3 → PA-4 → PA-5 → PA-6 → PA-7 → PA-8 → PA-9 → PA-10 → PA-11 |
| B — bootstrap | PB-1 → PB-2 → PB-3 → PB-4 → PB-5 → PB-6 |
| C — Stage 0 | PC-1 → PC-2 → PC-3 → PC-4 → PC-5 → PC-6 → PC-7 |
| D — critical path | PD-1 → PD-2 → PD-3 → PD-4 → PD-5 → PD-6 → PD-7 → PD-8 → PD-9 → PD-10 → PD-11 → PD-12 → PD-13 |
| E — close the criteria | PE-1 → PE-2 → PE-3 |

Two orderings inside Phase C are counter-intuitive and are not accidents: **PC-2 (the comparator
sensitivity suite) precedes PC-7 (freezing the baselines it guards)** — §12.7, because a comparator
mutation-tested afterwards leaves earlier green results nobody can trust retrospectively. And **PD-3
(Stage 10's measurement models) precedes the stages they gate** — M12, because a quality stage that runs
after the stages it should gate cannot gate them.

---

## PR in flight

| Field | Value |
|---|---|
| PR | *(none)* |
| Branch | `docs/loop-state` |
| Head SHA | *(pending)* |
| Task | PL-0 |

---

## Nightly differential runs (§8 DoD 3)

Ten **consecutive** green runs. A red run resets the count — this is a stability claim, not a sampling one.
M18 measures the tail at ≈13.4 nights for p = 0.95, and notes a nightly failure is not reproducible without
the failure-bundle schema, which is why that schema lands with PD-12 rather than after it.

| # | Date | Run | Result |
|---|---|---|---|
| — | — | — | not started |

---

## Log

Newest first. One line per shipped change, plus anything that changed the plan.

| Date | Event |
|---|---|
| 2026-08-23 | **PB-2/PB-3 — the local gate loop runs.** `make lint`, `make build` and `make docs` all pass; `make bouncer` **fails correctly**, naming C.5's lost text. `repo-checks` reports 4 of 4 enforcement scripts missing rather than passing quietly. Four more findings, all in Appendix D.0: **sqlfluff has no `--vars`**, so §11.1's "a bare `sqlfluff lint` works with no setup" depended on the default DR-22 removed — fixed by routing the threshold through the environment as a JSON string, the mechanism D1 already uses; **dbt renders a Jinja-bearing `vars:` value to a string**, so structure cannot survive the `vars:` block at all, which generalises D1's constraint beyond the model JSON; **`ruff format` reformats Python fenced blocks inside the design documents** — it realigned §8.2's `CONSTRAINT_SUPPORT`, which is read from dbt-duckdb source, and §0 is explicit that a `[VERIFIED]` block is "reproduced as executed"; and **§17's `make lint` names `tests/`, which errors when the path does not exist**. |
| 2026-08-23 | **PB-1 — the scaffold runs, and first execution disproved six things.** Every §4 pin co-resolves on Python 3.12 (108 packages, `sqlglot==30.17.0` under dbt-bouncer's `<31`). §8.2's DDL claim is confirmed **and shown to bite** — a duplicate key and a NULL are both rejected, which `[VERIFIED]` alone never established. §1.8's compile-time threshold error fires. What broke: `require_generic_test_arguments_property` makes the conventional data-test syntax **invalid**, and neither document shows the required `arguments:` nesting; `+group: er_core` needs a `groups:` file that existed nowhere; `given:` is **required** even for a model with no inputs; **a misplaced `unit_tests:` block is silently ignored** — clean parse, exit 0, no warning, and 3.20 then reports "no unit test" for a model that has three; **a consumer's `dbt build` DOES run our unit tests**, which settles RC56 and selects 3.71's second branch; and a failed `dbt parse` leaves a **stale** `target/manifest.json`, which is a concrete mechanism for §15's "do not cache `target/`". All six recorded in Appendix **D.0**. |
| 2026-08-23 | **PA-9 — two register rows created, one decided, and two UNVERIFIED claims measured rather than asserted.** **DR-23** gives B.8 the row it never had (RC46) and names Stage 0.8's spike as its owner, so option (a) is not adopted by default. **DR-24** closes B.9 (RC38/RC2): §7.3.1 is **scoped, not waived** — a `WITH RECURSIVE` clause may contain a companion CTE that is a pure single-source projection of a `ref()`ed relation. Undirected-graph recursion *always* needs a doubled-edge adapter, so a rule every correct instance must violate was the wrong rule; (a) would cost a `table` at 2× the edge count for an orientation flip, and (b) would require raising a cap §7.3.3 sets to zero deliberately. **G20 verified** from two sources: PyPI `license_expression: MIT`, and the repo `LICENSE` at tag `v4.0.16` — `Copyright (c) 2020 Ministry of Justice`. Compatible with ours; no copyleft. **G13 measured `[RUN]` on `duckdb==1.5.5`: there is no statement timeout, and no recursion-depth setting** — `duckdb_settings()` returns **0 rows** for `%timeout%`, `%recur%` and `%interrupt%`. M5 and A.2 C6 both assumed one; both are corrected. **The in-query iteration cap is now the only guardrail**, not one of two, and the real backstop is `memory_limit`, which fails hard rather than timing out — consistent with D4a's finding that `USING KEY` OOMs rather than degrading. |
| 2026-08-23 | **PA-8 — §A.6 Q5 answered; new register row DR-22.** Parity is not quality, and the project's own reference fixture proves it: the model Stage 0.4 freezes measures **F1 0.7138 / recall 0.5550**, finding 1,651 of 2,975 true pairs. Two extra blocking rules take it to **0.9809 / 0.9173** — **+0.26 F1 invisible to every gate in §6.4**. Committed per-fixture floors (`er_blocking_recall_floor` two-sided, `er_f1_floor`, `er_max_cluster_size` as a hard test) are set at **Stage 0.4 from the fixed model**, and the numbers are deliberately not invented here — they must come from measuring what ships. **The default threshold is removed**: `[RECON]` shows F1 peaks at 0.9809 at t = 0.5 and falls to 0.9219 at t = 0.9 — the value §8's DoD used as its example — costing ~330 true pairs for **zero** precision benefit, since precision is already 1.0000. A default nobody justified was silently choosing worse output. Ownership is mechanised, not named: the floors live in a file with a `CODEOWNERS` entry, because a floor anybody can lower to make CI green is not a floor. |
| 2026-08-23 | **PA-7 — DR-08 and DR-09 closed together**, because they are one contract change to Stage 6. **The threshold becomes a dimension**: `var('er_threshold')` builds one partition per run, Stage 6's AC needs {0.5, 0.9, 0.99} simultaneously, and cross-threshold monotonicity is **not expressible as a dbt test at all** under the var approach. M16 verified the composite-key form rather than proposing it — a correct refinement chain on 6 nodes / 3 edges. **And there are two thresholds**: `thresholds` is a relation of `(thr_auto_merge, thr_review_low)` pairs, the half-open band goes to a new `review_pairs` relation and is **never clustered**, matching the incumbent's contract as DR-14's posture requires. **This costs no parity** — gray pairs are below the threshold, so Splink discards them from clustering too; `review_pairs` surfaces rows Splink computes and throws away. Both default to the degenerate case, so production cost is unchanged. Recorded trap, from M16's own `[RECON]`: cast `thresholds` to **DOUBLE**, or DuckDB types the literal `DECIMAL` and shifts the boundary comparison. |
| 2026-08-23 | **PA-6 — DR-12 closed: the label is `component_label`, and it is not an identifier.** New §1.6. The trigger had already fired — DR-12 said *"decide with DR-14"*, DR-14 went CURRENT on 2026-08-20, and §A.6 Q1 declared this consequence *"binding, not conditional"* — while the body still emitted `entity_id` everywhere (RC16). M6's `[RECON]` is why the rename is not cosmetic: five chained records all labelled `crm:100`; adding **one** record with a single edge changed the label for **5 of 5** pre-existing records, though no member left the component. A merge, a split and a pure relabel are indistinguishable from outside. The contract now states what holds (deterministic for a fixed graph and threshold) and what does not (stability across runs), and the column is removed as a key from every downstream model. **Stage 6b collapses to an interface contract** — the engine publishes the edge set and the partition; the platform owns permanence, per §1.3's non-goal and DR-14. D4's reference query is kept **as executed** with a note, because the `[RECON]` runs used `entity_id` and rewriting evidence would break the claim it supports. |
| 2026-08-23 | **PA-5 — B.1 / DR-13 closed, and *not* the way the document recommended.** M17 rec (a) bundles two things: a parquet-only harness and a `:memory:` substrate. The first is adopted — the harness never opens the database, and `integration_tests/` exports every compared model with a `COPY` post-hook (never the package, per 3.52). The second is **rejected on evidence that postdates the recommendation**: C.7's `build` job runs **five separate dbt invocations**, and an in-memory database does not survive between processes, so `dbt seed` becomes unimplementable and `dbt docs generate` would catalog an **empty** database — leaving dbt-bouncer's whole catalog tier passing over nothing. `:memory:` was buying a *structural* guarantee that harness and dbt are never siblings; what it prevents is a **loud** failure (`Conflicting lock is held`), and what it costs is a **vacuous pass**. That trade runs the wrong way in this project. D11 is unaffected — the export is a post-hook, not a materialization. RC45 closed by the mirror image of what it feared: `profiles.yml` survives unchanged, but by decision, with the reason recorded beside it. |
| 2026-08-23 | **PA-4 — DR-16 closed.** §2.0 is the input contract: one relation named by `er_input_relation` (the package ships zero sources, per M4b); v1 arity is **one table**, because Stage 12.1 forbids `source_dataset` and Splink's `UNION ALL` degenerates to a plain select — and when v2 needs a union, the **consumer owns it**, since §3.5 needs the *global* concat and only they know what global means. `unique_id` VARCHAR / NOT NULL / UNIQUE, hoisted out of Stage 12.1 where a reader would not look for it. Declared columns are a parse-time var (D1's corollary, applied to inputs for the first time). A missing column fails at **compile time naming the column** instead of raising a `Binder Error` from inside a generated `CASE`. The three preconditions ship as tests **in the package**, so they fail in a consumer's build — the uniqueness one above all, because D3's `l.uid < r.uid` means two records sharing an id never pair, silently. Stage 1's only remaining blocker is B.8. |
| 2026-08-23 | **PA-3 — DR-17 closed.** The model JSON is untrusted input, validated once at compile time in the sidecar (§1.5, new): D6's list becomes a closed allow-list checked against the **parsed tree**; non-deterministic functions, subqueries and statement terminators rejected; the input bounded; and `er_model_sha` redefined as the hash of the **validated** artifact, so a JSON that skipped the sidecar has no sha and does not build. Accepted cost, stated: a Splink-produced JSON can fail our validation — a supported-configuration boundary in the same class as Stage 12.1's, not a bug. Stage 1 gains five negative tests and loses a blocker. |
| 2026-08-23 | **PA-2 — the rebuild instructions are now correct.** Appendix D gains **D.1, the bootstrap order** (RC53) with **Waiver B-1** for the commits where all 71 §3 rows are new at once, and a per-delta table saying which C-deltas apply before first run. §23 gains the **canonical-home rule**; Appendix C gains its **handover rule**. Nine further notes closed, each a defect the rebuild would otherwise have faithfully reproduced: 3.52 banned the hook §2 requires (RC34); C.1 deltas 1/4/6 pointed at `vars:` when their home is the hardening macro, leaving the gate consumer-disarmable (RC48); C.5's text exists nowhere and said otherwise (RC50); two pre-commit hooks could never fire on `integration_tests/` (RC51); C.7 delta 8 cited a resolution that does not exist and would widen the §14.10 egress if applied before B.7 (RC52); `.sqlfluffignore` exempted a model that does not exist while the real one would fail lint (RC41); the four missing `verify_gates.py` injections are named (RC55); RC33, RC39, RC40, RC42, RC45's ordering half, RC54. Open review notes **33 → 20**; the companion is down to RC38 and RC46, both needing Appendix B rows that land with PA-6. |
| 2026-08-23 | **PA-1 — DR-11 closed.** §5 absorbed A.5 and is the single stage inventory; A.5 retained as evidence. Merge scope was RC29's enumeration, not R3's shorter list. Also closed: R3, RC1, RC7, RC8, RC9, RC11, RC12, RC14, RC32. Decided along the way, each recorded at its point of change: Stage 2b closes as the **explicit non-goal** (v1 is full-rebuild; `is_incremental()` and record lifecycle move to v2 together), Stage 4 relaxes to **reachable** threshold constants, `entity_clusters_1to1` is **tagged v2**. `doc_index.py` fixed twice — it labelled a `CURRENT` row's stage mentions "blocks", and its per-stage reconciliation caveat outlived the conflict it described. |
| 2026-08-23 | Loop established. Repository is docs-only: no dbt project, no CI, no fixtures. `git rev-list --objects --all` returns 41 objects across two commits, confirming the scaffold Appendix D records as deleted is not recoverable from history. |

---

## Standing rules

Repeated here because an autonomous loop is exactly where they get quietly broken.

- **Never silence a gate to ship.** Editing a test, tolerance, threshold, pin or `severity` to make CI green
  is the §21 failure mode, not a fix. `severity: warn` is inert under `error: all` anyway (§12.6).
- **Never auto-rerun `parity`, `determinism`, or `comparator-sensitivity`.** A flake in one of those is a
  product defect until proven otherwise, and §21 says that is the base rate here, not a pessimistic default.
- **Never push to main.** Every change is a PR, including a revert.
- **Implement tolerance from A.4, never §6.1.** §6.1 omits the relative term, the `mw > 54` vacuity rule and
  the float-aggregate row, and its clusters row contradicts Stage 6's own AC.
- **Quote a stage's AC; do not paraphrase it** — and read the review notes first, because several ACs in the
  body are known-broken. Stage 9's is unfalsifiable (B5/RC11).
- **One model per ticket**, and a model ticket is not done when the SQL compiles. The vertical slice is SQL
  + colocated `.yml` + enforced contract + key + six-section description + column descriptions + a unit test
  using `format: sql` with an explicit cast on every column + data tests + the parity gate green.
- **One unverified change in flight at a time.** When main goes red, the loop must be able to name which
  change did it.
