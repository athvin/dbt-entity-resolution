---
name: er-backlog-preflight
description: Reports what work in the dbt-er programme is actually startable and what is blocked, by reading the decision register, the open-decision appendices and the inline review notes in docs/DesignDoc.md and docs/DbtBestPractices.md. Produces a readiness report plus decision tickets for the rows that must close first. Use when asked what to work on first, what is blocking, whether the plan is ready, what the pre-flight is, which decisions are open or missing, or to triage the backlog before breaking anything into tickets.
---

# Backlog pre-flight

## What this answers

"What can we start, and what has to close before we can?"

The programme is not code-ready, and the docs say so themselves. `DesignDoc.md` §B.3: *"Rows marked CONFLICT or MISSING are the ones to close before writing models."* Five separate "do this first" directives coexist across the two documents and **none orders the others** (RC8). This skill produces the ordering, and the tickets that close the blockers.

Run it before `er-epic-breakdown`. Breaking a stage into tickets while its blocking decision is open produces tickets that get rewritten.

## The one rule that overrides everything

**Never close an open decision on the user's behalf.** `DesignDoc.md` §B.5 item 6: *"No open decision was resolved unilaterally — every CONFLICT, OPEN and MISSING row in §B.3 is still yours to close."*

This skill surfaces, sequences, and drafts the decision ticket. It does not pick option (a) over option (b), does not reconcile §5 against A.5, and does not mark a row CURRENT. Where a doc records a **Recommendation**, carry it forward *as the doc's recommendation*, attributed, never as a decision.

## Step 1 — Index the docs

```bash
python3 .claude/skills/er-backlog-preflight/scripts/doc_index.py --format text
```

Deterministic parse of both docs. No LLM reasoning, no token cost, and it cannot drift from the source. Flags:

| Flag | Use |
|---|---|
| `--blocking` | only CONFLICT/MISSING rows, unresolved opens, and A.5-only stages |
| `--stage 3` | everything touching one stage — the pre-check before an epic breakdown |
| `--section decisions` | one section only (`decisions`, `opens`, `reviews`, `stages`, `standards`) |
| `--format json` | structured, for further filtering |

Read the JSON before writing anything. Do **not** re-read the 6,400 lines of Markdown to answer a question the index already answers — open the doc only to quote a specific row's reasoning.

## Step 2 — Classify every blocker

Four classes, in this order. The class decides the ticket type and the urgency.

**Class A — CONFLICT.** Two normative statements disagree. Nothing downstream can be planned from either. Currently: **DR-11** (§5 against A.5 — the stage inventory itself). A CONFLICT is not a decision to make; it is a reconciliation to perform, and the reconciliation has a defined scope (R3, extended by RC29).

**Class B — MISSING.** A decision that was never made and has no row value. Currently **DR-16** (input contract) and **DR-17** (model JSON trust boundary) in full; DR-18/19/20 are partially answered by the companion per RC30 — check that note before treating them as fully missing.

**Class C — OPEN with a fired deadline.** An open row whose stated trigger has already passed. These are the dangerous ones, because they get resolved by drift:
- **B.1** (runtime substrate) — "must be settled before the harness lands", and the harness is Stage 0.3. RC45: the C.3 `profiles.yml` is already option-(b)-shaped, so scaffolding rebuilds it verbatim and B.1 resolves itself.
- **B.8** (ST05 under the CTE ban) — blocks Stage 5 and Stage 1's snapshot AC; postdates §B.3 so it has **no register row** (RC46).
- **DR-12** (`entity_id` vs `component_label`) — its trigger DR-14 is CURRENT, so the trigger fired without the row closing (RC16).

**Class D — OPEN, scheduled.** Has an owner and a stage. Not a blocker yet. DR-05, DR-08, B.2, B.3, B.5, B.6, B.7.

For each blocker record: **id · class · what it blocks · who the doc says owns it · the doc's recommendation, if any**.

## Step 3 — Sequence the five competing directives

RC8 names five "do this first" instructions and proposes the order below. It is a **review-note proposal, not a normative section** — present it as the recommended sequence and say where it comes from.

1. **Close DR-11 / R3** — reconcile §5 and A.5 to one stage list. Everything else sequences off it.
2. **Decide DR-17 / G3** (the trust boundary — it is architecture, not policy) and disposition the remaining MISSING rows.
3. **Settle `DbtBestPractices.md` B.1** — its own text says it must precede the Stage 0.3 harness.
4. **Rebuild the engineering scaffold** from `DbtBestPractices.md` Appendix C with the v2 delta tables applied. Appendix D records that the verified scaffold was deleted, so Appendix C is currently the only copy. This step appears in *no* task list in either document.
5. **Build the comparator sensitivity suite** (A.5 Stage 0.7). §12.7: *"the one standard in the document that should be built before the thing it guards"* — before Stage 0.4 freezes baselines against it.
6. Then Stage 0.1–0.5.

Add two the note predates: **B.8's option (c) `EXPLAIN ANALYZE` spike** belongs in Stage 0 (RC46 — otherwise option (a) is adopted untested by default), and **DR-16** (input contract) gates any model that reads `stg_input`.

Two ordering facts worth stating every time, because they are counter-intuitive:
- The comparator suite is built **before** the baselines it will compare. A comparator mutation-tested afterwards has earlier green results nobody can trust retrospectively.
- The scaffold rebuild has no home in either plan. It is the largest un-ticketed item in the programme.

## Step 4 — Write the readiness report

Markdown, to `docs/backlog/preflight-YYYY-MM-DD.md`. `docs/backlog/` may not exist yet — create it. Structure:

```markdown
# Pre-flight — <date>

## Verdict
<One sentence: what is startable today, and what is not.>

## Blocked on a decision
| Blocker | Class | Blocks | Owner per doc | Doc's recommendation |

## Startable now
| Work | Why it is unblocked | Size (M21) | Parallel with |

## Recommended order
<RC8's six steps, attributed, with the two additions.>

## Not yet plannable
<Stages whose scope changes depending on an open row — name the row.>
```

The **Startable now** table is the point of the report. Even with DR-11 open, real work exists: the scaffold rebuild (Appendix C is fully specified), the comparator sensitivity suite (§12.7 gives the mutant catalogue verbatim), and the decision tickets themselves.

## Step 5 — Draft the decision tickets

One ticket per Class A/B/C blocker. Use the **decision ticket** template in
`.claude/skills/er-ticket-writer/references/ticket-templates.md`.

A decision ticket's deliverable is a **document edit plus a register row**, never code. Its acceptance criteria are: the decision is recorded in `DesignDoc.md` §B.3 with a status and a value in force; every section the decision invalidates carries a `Supersedes:` line (3.45, and §1.1's "a superseding decision must name what it invalidates"); and the sections that implemented the old answer are updated in the same PR.

Offer the tickets as Markdown files under `docs/backlog/`. Only run `gh issue create` if the user asks — this repo has a remote and `gh` is authenticated, so it will really create them.

## Recurring traps

**Planning from §5 alone.** §5 is normative and it is *incomplete*: it omits Stages 2b, 6b, 12b, A.5's Stage 0.6 and 0.7, the ground-truth-labels and training-traces extensions to 0.3, the critical path statement, and roughly a dozen per-stage extensions (RC29 enumerates them). A reader planning from §5 builds a different programme from one planning from A.5. Always read the union.

**Treating Appendix B as binding, or as non-binding.** RC32: `DesignDoc` Appendix B ranks in **no tier** of §1.1's precedence list — not tier 1 (it introduces no `[RUN]`/`[RECON]` evidence), not tier 2 — yet the companion already treats it as binding. Its proposed fix: the B.3 register ranks with tier 2, and G/R findings rank as Appendix-A-class evidence. Until that lands, say which tier you are assuming.

**Assuming a `[VERIFIED]` marker still holds.** The verified scaffold was deleted (Appendix D). 3.44 mechanises marker demotion by comparing §4's pins to `uv.lock` — and there is no `uv.lock` yet. Every `[VERIFIED]` marker is currently unbacked by a file.

**Quoting §6.1's tolerance table.** Implement from **A.4**, not §6.1. §6.1 omits the relative term, the `mw > 54` vacuity rule, and the float-aggregate row — and its clusters row says partition equality where A.4 and Stage 6's own AC require label equality (RC13/RC28). Four differences, not the three the docs enumerate.
