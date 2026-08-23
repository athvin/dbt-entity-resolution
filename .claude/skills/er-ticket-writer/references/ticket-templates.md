# Ticket templates

Five shapes. Pick by what the ticket *produces*, not by which stage it sits in.

Common header fields on every ticket:

```
**Stage:**       <stage id, or "cross-cutting">
**Type:**        model | sql-generation | harness | gate | spike | decision | doc
**Size:**        days | weeks | multi-week spike        (M21, UNVERIFIED)
**Blocked by:**  <DR-nn / B.n / ticket id, or "nothing">
**Blocks:**      <ticket ids, or "nothing">
**Build mode:**  upstream ref() | injected baseline | both        (models only)
**Oracle:**      <Splink baseline | Python reference | hand fixtures | training trace | none>
**Serves:**      <GOAL.md success criterion 1–5, or the scope row it advances>
```

`Blocked by: nothing` is written explicitly. An absent field reads as unexamined.

**On `Serves:`.** `GOAL.md` lists five things that define success — the pipeline runs end-to-end with no
Python in the run; parity is demonstrated, bounded and published; every deliberate divergence is logged and
pinned; every model has unit tests written *with* the model; the gates enforce all of it unattended. Name
the one this ticket advances. Two uses: a ticket that cannot name one is either out of scope or is
describing a step rather than an outcome, and a criterion no ticket serves is a criterion nobody is
building toward. `GOAL.md` is non-normative, so this field never supplies an AC or a tolerance — it records
*which* end this work is a means to. The AC still comes from the stage and from A.4.

---

## 1. Model ticket

```markdown
## ER-<n>: `er_<model_name>`

**Stage:** 2 · **Type:** model · **Size:** days
**Blocked by:** nothing · **Blocks:** ER-<n> (scoring reads this)
**Build mode:** upstream ref() · **Oracle:** Splink baseline `<artifact>`

### What it computes
<One paragraph. The design-doc decision it implements, cited by id — D7a, §3.5.>

### Grain
One row per <what>. <Why that grain, and what would break at a different one.>

### Acceptance criteria

**Given** <input state>
**When** <the model builds / a specific operation runs>
**Then** <observable outcome>

- [ ] <Parity AC, quoted from the stage's own AC — not paraphrased>
- [ ] <Invariant AC, expressible as a dbt data test or CHECK constraint>
- [ ] <Adversarial AC — the fixture that catches the known trap>

### Parity gate
<The equivalence claim, per A.4. Name the tolerance and the space it is stated in.
For exact gates say "exact after canonical ordering" and mean it.>

### Definition of Done
See `.claude/skills/er-ticket-writer/references/definition-of-done.md` → **Model ticket**.
Non-default items for this model:
- <e.g. pair-grain, so PK is dbt_utils.unique_combination_of_columns + a recorded waiver (3.7)>
- <e.g. columns come from the model JSON, so contract via er_gamma_columns (3.23, §9)>

### Notes and traps
<The specific way this model gets silently broken. Quote the doc.>
```

**Worked example of the AC style** — Stage 2's frozen-TF criterion, which is the shape to aim for:

> **Given** a scored pair under `(er_model_sha=X, er_tf_snapshot_id=Y)`
> **When** unrelated records are appended to the corpus and the pair is re-scored under the same two keys
> **Then** `match_weight` is **bit-identical** to the first score.

That AC is what makes frozen TF meaningful rather than decorative, and the design doc notes it is cheap to write now and awkward to retrofit. Prefer ACs with that property: mechanical, falsifiable, and cheap only if written early.

---

## 2. SQL-generation ticket

For Jinja macros that render model SQL from the model JSON. Same shape as a model ticket, with two substitutions:

```markdown
### Acceptance criteria
- [ ] Rendered SQL for the fixture model matches the reviewed snapshot.
- [ ] Malformed input fails **compilation** with an actionable error — not at runtime, and not silently.
- [ ] `dbt compile` output contains zero Jinja residue.
- [ ] <The degenerate-input case: e.g. a level with `m_probability` absent renders
      `_default_m_values`, not NULL.>

### Snapshot review
<Which rendered artifact a reviewer reads, and what they are checking for.>
```

Two standing constraints for this type:

- **Comparison-level SQL passes through verbatim** (D6). A macro that "tidies" a `sql_condition` diverges from the oracle.
- **Fail compilation rather than guessing.** TF exact-match-level resolution is a sqlglot CNF analysis Jinja can only string-match (A.2 C2); the rule is to restrict to the resolvable shapes and error on anything else. The exact resolution belongs to the compile-time sidecar.

---

## 3. Harness ticket

```markdown
## ER-<n>: <comparator | generator | fixture family>

**Stage:** 0.7 · **Type:** harness · **Size:** days
**Blocked by:** B.1 (runtime substrate — the harness and dbt cannot both hold the DuckDB file)

### What it proves
<The equivalence or invariant. "Localised per stage" is the design intent — say which stage.>

### Acceptance criteria
- [ ] <The comparison it performs, and the exact join key it performs it on>
- [ ] Every mutant in the §12.7 catalogue **fails** it, each with the expected localisation string.
- [ ] It cannot pass vacuously: <the specific zero-rows-vs-zero-rows path, closed>.

### Definition of Done
See `definition-of-done.md` → **Harness / comparator ticket**.
```

The vacuity clause is the point of this ticket type. §12.7: *"A comparator with a wrong join key returns '0 differences' by comparing zero rows to zero rows. Every stage is green, `PARITY.md` ships claiming verified equivalence, and the first real divergence surfaces in production as mis-merged entities — the most damaging error an ER system can make."*

---

## 4. Spike ticket

```markdown
## ER-<n>: SPIKE — <the question, phrased as a question>

**Stage:** 0.x · **Type:** spike · **Size:** multi-week · **Timebox:** <n days>
**Blocked by:** nothing · **Blocks:** <what cannot be planned until this answers>

### Question
<One sentence, answerable yes/no or with a number.>

### Kill criterion
<Written in advance: the result that means stop. Not "if it looks hard".>

### What each outcome does to the plan
| Outcome | Consequence |
|---|---|
| <yes> | <which tickets become writable> |
| <no>  | <which stage's product changes, and what gets scheduled instead> |

### Evidence required
<Which class: [SRC] source on disk · [RUN] executed, with the query shape ·
[RECON] prior verification corpus · [DERIVED] arithmetic from those.
Unlabelled reasoning is marked UNVERIFIED and is not an answer.>

### Output
A decision plus its evidence. Not a model.
```

Example of a kill criterion done right — A.6 Q3: *"If **no** at your scale, Stage 6's product is dbt-driven materialised iterations and the recursive CTE becomes the reference oracle the fast path is tested against — a different amount of work that must be scheduled before Stage 6 starts, not after."*

---

## 5. Decision ticket

```markdown
## ER-<n>: DECIDE — <DR-nn / B.n>: <the decision>

**Type:** decision · **Size:** hours · **Class:** CONFLICT | MISSING | OPEN (deadline fired)
**Blocks:** <stages that cannot be planned until this closes>

### The question
<What is actually unsettled, in one sentence.>

### Options, as the docs state them
| Option | Consequence | Source |
|---|---|---|

### What the docs recommend
<Quoted and attributed. This is a recommendation, not a decision — §B.5 item 6:
"No open decision was resolved unilaterally.">

### Cost of not deciding
<Specifically what gets built wrong, or resolved by drift. E.g. RC45: the Appendix C
profiles.yml is already option-(b)-shaped, so scaffolding rebuilds it verbatim and B.1
resolves itself — which this appendix's own header forbids.>

### Acceptance criteria
- [ ] Recorded in `DesignDoc.md` §B.3 with a status and a value in force.
- [ ] Every invalidated section carries a `Supersedes:` line naming it (3.45, §1.1).
- [ ] Sections implementing the old answer are updated in the same PR.
- [ ] <If the row postdates §B.3 — e.g. B.8 — it gets a register row (RC46).>
```

---

## Writing the acceptance criteria

**Name the oracle.** "Tested" with no oracle is not testable in this project. Four exist: a Splink baseline; a Python reference implementation (union-find for clustering and `is_bridge`); hand-built fixtures with machine-checked ground truth (Stage 7 has no Splink oracle at all); and a committed training trace (Stage 9, per B5).

**Quote the stage's own AC rather than paraphrasing.** These ACs encode measured facts. "Exact `(unique_id_l, unique_id_r, match_key)` set equality, with `match_key` compared **as VARCHAR**" is not a wordier way of saying "the pairs match" — the VARCHAR clause is the whole point, because `match_key` is VARCHAR in Splink (`blocking.py:203-206`) and a coercing comparator normalises a real divergence away.

**Check the review notes before copying an AC forward.** Some are known-broken. Stage 9's is unfalsifiable (RC11/B5). Stage 4's "boundary fixture for every distinct threshold constant" conflicts with A.5's relaxation to reachable constants. Run `doc_index.py --section reviews` and grep for the stage.

**Prefer an AC that is cheap now and awkward later.** The design doc flags several explicitly — the frozen-TF bit-identity test, the baseline format carrying gammas/bfs/labels/training traces from day one, the thread-determinism gate. These are the ACs whose omission costs a rewrite rather than a patch.

**State the negative case.** Several ACs in this project assert something *does not* happen: `stg_input` performs no transformation; a value absent from the frozen TF snapshot **raises** rather than coalescing; ghost-node fixtures assert we emit **nothing** where Splink emits a spurious NULL-id row. An AC that only checks the happy path passes for a model that silently normalises its input.
