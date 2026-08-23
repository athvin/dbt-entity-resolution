---
name: er-epic-breakdown
description: Breaks a dbt-er stage, design-doc section, or architectural decision into an epic with well-scoped child tickets, reconciled across DesignDoc.md section 5 and its A.5 corrected stage list so no work is planned from the incomplete inventory. Assigns size classes, marks the critical path, separates spikes from delivery tasks, and gates each ticket on the decisions that block it. Use when asked to break down a stage or epic, scope work, turn the design doc into tickets, plan a sprint, or decompose the entity-resolution problem into deliverable pieces.
---

# Epic breakdown

## Before anything else

**Read `references/stage-inventory.md` in this skill directory.** It is the union of `DesignDoc.md` §5 and A.5, which are two normative inventories in unresolved conflict (**DR-11**, R3). Planning from §5 alone omits Stages 2b, 6b, 12b, sub-stages 0.6 and 0.7, three extensions to 0.3, the critical-path statement, and roughly a dozen per-stage extensions. *"A reader planning from §5 builds a different programme from one planning from A.5."*

Then check what blocks the target stage:

```bash
python3 .claude/skills/er-backlog-preflight/scripts/doc_index.py --stage 3 --format text
```

If a **CONFLICT** or **MISSING** row blocks it, say so first and produce the decision ticket before the delivery tickets. Do not quietly plan around an open decision — the stage inventory records which ones change a model's *contract*, not just its SQL, and those rewrite tickets rather than adjusting them.

## Step 1 — Frame the epic

An epic is one stage or one sub-stage. Its frame is five lines:

```markdown
# Epic: Stage 3 — Blocking

**Goal:** <what exists at the end, in one sentence>
**Serves:** <the GOAL.md success criterion this epic advances, 1–5>
**Parity gate:** <the specific equivalence claim — from the stage's AC, per A.4>
**Blocked by:** <DR/B rows, or "nothing">
**Critical path:** <yes 1→3→4→5 | no, parallel from day one via injected baselines>
**Size:** <days | weeks | multi-week spike> (M21, UNVERIFIED)
```

The **Serves** line points at `GOAL.md`'s definition of success and costs one lookup. It is not an AC —
`GOAL.md` is non-normative and settles nothing — but an epic that cannot name a criterion it advances is
either out of scope or is a step dressed up as an outcome, and that is worth catching before the child
tickets exist rather than after.

The **parity gate** line is what makes an epic here different from a normal software epic. Every stage's real definition of success is an equivalence claim against Splink, stated in the stage's own AC. Quote it; do not paraphrase it. Implement tolerance from **A.4**, never §6.1 (§6.1 omits the relative term, the `mw > 54` vacuity rule, the float-aggregate row, and its clusters row contradicts Stage 6's own AC).

## Step 2 — Decompose along this project's real seams

Not frontend/backend/database. The seams that produce independently-deliverable, independently-testable units here are:

| Seam | Unit of work | Typical size |
|---|---|---|
| **Model** | one `.sql` + its `.yml`, contracted, keyed, documented, unit-tested | days |
| **SQL generation** | one Jinja macro that renders model SQL from the model JSON | days–weeks |
| **Sidecar** | the compile-time Python step resolving what Jinja cannot (§A.2 C2/C3/C4) | days |
| **Harness** | one pytest comparator, generator, or fixture family | days |
| **Gate** | one enforcement-matrix standard: mechanism + gate + `verify_gates.py` injection | hours–days |
| **Spike** | a question with a written kill criterion and a timebox | multi-week |
| **Decision** | a doc edit plus a register row — never code | hours |
| **Doc** | `PARITY.md`, `divergence-log.md`, `quarantine.md` entries | hours |

**One model per ticket.** Two models in one ticket cannot fail independently, and localisation is the whole point of the staged design.

**A model ticket is not done when the SQL compiles.** The vertical slice for this repo is: SQL + colocated `.yml` + enforced contract + primary key (per the §8.3 grain split) + six-section description + column descriptions + unit test with `format: sql` and explicit casts + data tests + the parity gate green. Anything less ships a model that the four gates will reject at a worse moment. The full checklist lives in `.claude/skills/er-ticket-writer/references/definition-of-done.md`.

## Step 3 — Apply the INVEST check, adapted

Standard INVEST, with three project-specific readings:

- **Independent** — in this project independence is *bought*, not found. Every model can read from either its upstream `ref()` or an injected Splink baseline, so a ticket that would otherwise wait on Stage 4 can start on day one. **Every ticket must state which mode it builds in.** Note the mechanism in §5 is the defective single-global-boolean form (RC12); M4's corrected form is per-model injection mapping, a harness-owned source, a both-modes CI rule, and `sha256(model JSON)` binding. If a ticket depends on injection, it also depends on M4 landing.
- **Testable** — the AC must name the oracle. Splink baseline? A Python union-find reference (`is_bridge`, D4 clustering)? Hand-built fixtures (Stage 7 has no Splink oracle)? A committed training trace (Stage 9, per B5)? "Tested" with no named oracle is not testable here.
- **Small** — if it is a spike, it is not a story. M21's core complaint is that three spikes with real failure probability carry the same visual weight as `int_edges`, which is one `WHERE` clause. Spikes get a **written kill criterion**, not an estimate.

## Step 4 — Order by dependency, not by stage number

Three ordering facts override the numbering:

1. **Critical path is `1 → 3 → 4 → 5`.** Stage 1 leads because `load_model_json` owns five recomputed values (D1) and every downstream *baseline* is meaningless until the reader is right.
2. **Stages 6, 7, 10 and 12b build in parallel from day one** via injected baselines. A.5 calls this "the single largest schedule lever in the document" and it is currently buried in a six-line paragraph.
3. **Two things run before the work they support.** The comparator sensitivity suite (0.7) is built *before* Stage 0.4 freezes the baselines it will compare — §12.7 is explicit, and a comparator mutation-tested afterwards has earlier green results nobody can trust retrospectively. And Stage 10's *measurement* models build after Stage 2, not at Stage 10, so they can gate Stage 3's recall floor and Stage 6's quality tests (M12).

For each ticket record **Blocked by** (a decision, or another ticket) and **Blocks**. If nothing blocks it, say "nothing" explicitly — an absent field reads as unexamined.

## Step 5 — Separate the three ticket temperaments

Mixing these in one backlog is how a plan hides its risk.

**Delivery tickets** have an oracle, an AC, and an estimate. Most model and gate tickets.

**Spikes** have a question, a timebox, and a **written kill criterion** — what result means stop. Currently: D5's EM in SQL (B5 shows the oracle is non-reproducible); `is_bridge` in SQL, i.e. biconnected components, with no oracle (M11); the iteration guardrail (M5 measured every cheap diameter proxy as anti-correlated with cost by ~200×); and B.8 option (c)'s `EXPLAIN ANALYZE` check. A spike's output is a decision, not a model.

**Decision tickets** produce a doc edit and a register row. Their AC is: recorded in §B.3 with a status and a value in force; a `Supersedes:` line naming every section invalidated (3.45, §1.1); and the sections implementing the old answer updated in the same PR. §1.1 narrates exactly what happens without that last clause — four sections stayed stale while the decision replacing them sat three pages away.

## Step 6 — Write the epic file

Markdown to `docs/backlog/epic-stage-NN.md` (create `docs/backlog/` if absent). One epic frame, then the child tickets in dependency order, each in the format from
`.claude/skills/er-ticket-writer/references/ticket-templates.md`.

End the file with a short **Deferred / not in this epic** list naming what you consciously left out and why — a stage's A.5 extensions, a v2-tagged model, a capability that belongs to the platform per §A.3 Group 2. Silence there reads as an oversight, and G18 exists because un-remarked dead code accumulated exactly that way.

Offer `gh issue create` only if the user asks. The repo has a live remote and `gh` is authenticated.

## Traps specific to this decomposition

**Sizing a spike as a task.** See Step 5.

**Writing an AC the docs already disproved.** Stage 9's §5 AC ("EM within 1e-4 … with the same iteration count") is unfalsifiable — B5 measured 1.63 match-weight spread under Splink's default `seed=None`, the iteration count is unobservable, and 1e-4 *is* Splink's own `em_convergence`. Use A.5's trace oracle. Check the review notes for the target stage before copying its AC forward: `--section reviews` in the doc index.

**Planning a model whose columns come from the model JSON as if it were fixed-schema.** M2: those models cannot be contracted or unit-tested the ordinary way. §9 and 3.23 give the mechanism (`columns: "{{ var('er_gamma_columns') }}"`); the ticket must reference it or it will fail the contract gate.

**Forgetting that a threshold change is a contract change.** DR-08 / companion B.2: `var('er_threshold')` builds one partition per run, but Stage 6's AC needs three thresholds simultaneously and cross-threshold monotonicity is not expressible as a dbt test under the var approach. That changes the contract of `er_int_edges` and `er_entity_clusters`, not just their SQL.

**Scheduling Stage 8 as incremental work.** RC10: D11 decided `table` everywhere without carving out `incremental`, and B4 measured the `where a.is_new or b.is_new` design at **≥100% of a full rebuild** — it is not incremental at all. Resolve the D11/Stage-8/Stage-2b triangle before writing these tickets.
