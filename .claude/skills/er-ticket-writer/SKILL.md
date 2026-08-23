---
name: er-ticket-writer
description: Writes one well-formed dbt-er ticket — a model, SQL-generation macro, harness comparator, enforcement gate, spike, or decision — with Given/When/Then acceptance criteria, a named oracle, the parity gate from A.4, and the repo's real Definition of Done from the enforcement matrix. Use when asked to write, draft, or fix up a ticket, issue, story, or task for this project, or to add acceptance criteria or a definition of done to existing work.
---

# Write one ticket

## Scope

One ticket. For a whole stage use `er-epic-breakdown`; for "what should we do first" use `er-backlog-preflight`.

## Step 1 — Establish the facts before drafting

Three lookups, all cheap. Do them first — a ticket written from memory of these docs will restate an AC the docs have already disproved.

```bash
# What blocks this stage, and what A.5 changed about it
python3 .claude/skills/er-backlog-preflight/scripts/doc_index.py --stage <n> --format text

# Review notes — some ACs in the body are known-broken
python3 .claude/skills/er-backlog-preflight/scripts/doc_index.py --section reviews --format text
```

Then read the stage's row in `.claude/skills/er-epic-breakdown/references/stage-inventory.md`. It carries the §5 text, A.5's changes, the blocking decisions, and the traps in one place.

Open `docs/DesignDoc.md` itself only to quote an AC verbatim — which you should do rather than paraphrase.

Every ticket also carries a **`Serves:`** field naming which of `GOAL.md`'s five success criteria it
advances. `GOAL.md` is non-normative, so it never supplies an AC, an oracle or a tolerance — but a ticket
that cannot name a criterion is either out of scope or is a step written as an outcome. See the common
header in `references/ticket-templates.md`.

## Step 2 — Pick the ticket type by what it produces

| Produces | Type |
|---|---|
| a `.sql` + `.yml` model pair | **model** |
| a Jinja macro rendering SQL from the model JSON | **sql-generation** |
| a pytest comparator, generator or fixture family | **harness** |
| an enforcement-matrix standard | **gate** |
| an answer to a question | **spike** |
| a doc edit and a register row | **decision** |

Templates for all six: `references/ticket-templates.md`.
Per-type Definition of Done: `references/definition-of-done.md`.

Getting this wrong is the most common failure. A spike written as a delivery ticket gets an estimate instead of a kill criterion, and M21's complaint — that spikes with real failure probability carry the same visual weight as `int_edges`, which is one `WHERE` clause — reproduces itself one ticket at a time.

## Step 3 — Write acceptance criteria that can fail

Four rules, in priority order:

**1. Name the oracle.** Four exist in this project: a Splink baseline; a Python reference implementation (union-find, for clustering and `is_bridge`); hand-built fixtures with machine-checked ground truth (Stage 7 has no Splink oracle); a committed training trace (Stage 9, per B5). "Tested" with no oracle is not testable here.

**2. Quote the stage's AC, do not paraphrase it.** These encode measured facts. "Exact `(unique_id_l, unique_id_r, match_key)` set equality, with `match_key` compared **as VARCHAR**" is not a wordier "the pairs match" — the VARCHAR clause is the point, because `match_key` is VARCHAR in Splink (`blocking.py:203-206`) and a coercing comparator normalises a real divergence away.

**3. State the negative case.** Many ACs here assert something *does not* happen: `stg_input` performs no transformation (D8); a value absent from the frozen TF snapshot **raises** rather than coalescing (D7a); ghost-node fixtures assert we emit **nothing** where Splink emits a spurious NULL-id row. An AC that only checks the happy path passes for a model that silently normalises its input.

**4. Put the tolerance in the right place — or nowhere.** Implement from **A.4**, never §6.1 (§6.1 omits the relative term `1e-9 + 1e-12·|mw|`, the rule that probability parity is vacuous above `mw = 54`, and the row saying float aggregates are not a gate at all; and its clusters row says partition equality where A.4 and Stage 6's AC require label equality). And tolerance never appears in a dbt test — dbt compares with exact equality and cannot express one, so every float comparison lives in the pytest harness (§12.3). Choose fixture m/u values so Bayes factors are exact powers of two, which keeps unit tests exact by construction.

## Step 4 — Attach the real Definition of Done

Copy the relevant checklist from `references/definition-of-done.md` into the ticket, then add the non-default items for this specific piece of work. The ones most often missed:

- **Pair-grain models** cannot take a DDL primary key at acceptable cost — the §8.3 grain split routes them to `dbt_utils.unique_combination_of_columns` **plus a recorded waiver** (3.7).
- **Models whose columns come from the model JSON** cannot be contracted or unit-tested the ordinary way (M2). §9 and 3.23 give the mechanism.
- **Unit-test fixtures use `format: sql` with an explicit cast on every column** (§12.2). `format: dict` is typed by agate's inference, which read DATE from `"not-a-date"`; the resulting error was the lucky outcome, because had the sample values all looked like dates the fixture would have silently become a DATE column and a test whose whole purpose is proving the model does not transform values would have run against pre-parsed input.
- **`er_run_id` must appear in `er_volatile_columns`** as well as on the model, or it breaks every content hash (3.48).
- **A new gate ships with its `verify_gates.py` injection** (3.38). A rule without an injection has not been shown to fire.

## Step 5 — Emit

Write to `docs/backlog/ER-<n>-<slug>.md`, creating `docs/backlog/` if needed. Number sequentially from what is already there.

Only run `gh issue create` when the user asks for it. The repo has a live remote and `gh` is authenticated, so it will really create the issue — and `DbtBestPractices.md`'s own posture on side effects (*"a package may not create relations a consumer did not ask for"*) is the right instinct for a backlog too.

## Traps

**Writing an AC the docs already disproved.** Stage 9's §5 AC — "EM within 1e-4 of Splink's on the same blocking pass with the same iteration count" — is unfalsifiable: B5 measured a **1.63** match-weight spread under Splink's default `seed=None`, the iteration count is unobservable, and 1e-4 *is* Splink's own `em_convergence`, so a sub-tolerance parameter difference moves the early stop by one iteration and produces a supra-tolerance difference in every parameter. Use A.5's trace oracle. Check the review notes before copying any AC forward.

**A comparator ticket without the mutant catalogue.** §12.7 is explicit that the parity comparator is the one thing nothing proves can fail, and that the suite is built *before* the baselines it guards. Every harness ticket carries the eight mutants and the expected-localisation-string requirement.

**Treating a flake as noise.** §21: no gate is retried automatically, and a flake in a determinism or parity gate is a **product defect until proven otherwise** — in this project that is the base rate, not a pessimistic default. If a ticket's AC could flake, the ticket says what the quarantine entry would look like (owner, date, reason, expiry) rather than adding a retry.

**Adding `severity: warn` as a soft landing.** It is inert. Under `warn_error_options: {error: all}` a test configured `severity: warn` still fails the build (§12.6). There is no soft failure — a gate is blocking or it is deleted, and `docs/quarantine.md` is where "soft" lives now.

**Deciding an open question inside a ticket.** If drafting reveals the ticket depends on DR-08, DR-09, DR-12, B.1 or B.8, stop and write the decision ticket instead. §B.5 item 6: *"No open decision was resolved unilaterally — every CONFLICT, OPEN and MISSING row in §B.3 is still yours to close."*
