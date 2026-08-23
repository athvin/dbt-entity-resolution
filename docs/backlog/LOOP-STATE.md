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
| 1 | A fresh clone runs the whole pipeline, zero Python in the run | ❌ not started | — |
| 2 | Parity with Splink demonstrated, bounded, published | ❌ not started | — |
| 3 | Every deliberate divergence logged and pinned by a test | ❌ not started | — |
| 4 | Every model unit-tested, written with the model | ❌ not started | — |
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
| DR-17 model JSON trust boundary | **B** | Stage 1 | open | PA-3 |
| DR-16 input contract | **B** | Stage 1, anything reading `stg_input` | open | PA-4 |
| B.1 / DR-13 runtime substrate | **C** | Stage 0.3 | open | PA-5 |
| B.8 ST05 under the CTE ban | **C** (no register row) | Stages 1 and 5 | open | PA-6 |
| DR-12 `entity_id` vs `component_label` | **C** | Stage 6b, `entity_id` across Stage 6 | open | PA-7 |
| DR-08 / B.2 threshold as var or dimension | D | Stage 6 *contract* | open | PA-8 |
| DR-09 / G15 one threshold or a gray band | D | Stage 6 | open | PA-8 |
| A.6 Q5 quality floor | — (no row exists) | Stage 10's ability to gate | open | PA-9 |
| B.9 companion-CTE collision (RC38/RC2) | — (row not yet created) | Stages 6 and 9 | not raised | PA-6 |
| DR-05, DR-18/19/20, B.3, B.5, B.6, B.7 | D | their own stages | open, scheduled | PA-11 |

Rows already **CURRENT** and not tracked here: DR-01, DR-02, DR-03, DR-04, DR-06, DR-07, DR-10, DR-14,
DR-15, DR-21.

---

## Queue

Ids are `PA-n` … `PE-n`, prefixed because bare `A`–`E` and `D1`–`D12` already mean Appendix A–E and
decisions D1–D12 in these documents. Full descriptions live in the approved plan.

**In flight:** PA-2 — make the rebuild instructions correct before the rebuild follows them.

**Done:** PL-0 (#1, `5711322`) · PA-1 (#2, `59e8d93`) · PA-2 (#3)

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
