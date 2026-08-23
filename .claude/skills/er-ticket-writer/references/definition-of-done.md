# Definition of Done, by ticket type

Derived from `DbtBestPractices.md` §3 (the enforcement matrix), §10 (documentation), §12 (testing) and `DesignDoc.md` §6/§8. Rule ids are stable and never reused (§23), so `3.n` citations stay valid.

Verify the live matrix rather than trusting this file to stay current:

```bash
python3 .claude/skills/er-backlog-preflight/scripts/doc_index.py --section standards --format json
```

**Caveat that applies to every list here:** the verified engineering scaffold was deleted (`DbtBestPractices.md` Appendix D). The scripts, configs and custom checks these rules cite exist only as fenced blocks inside Appendix C until the rebuild lands. 3.39's own subject files do not exist yet (RC33). Until then, every DoD item citing a script is a *requirement on the rebuild*, not a command you can run.

---

## Model ticket

A dbt model is done when all of these hold. Not "the SQL returns the right rows".

### Structure
- [ ] `.sql` and same-basename `.yml` colocated in the same directory (3.1). Folder-level `schema.yml` is banned.
- [ ] No orphan `.yml`; non-resource YAML starts with `_` (3.2).
- [ ] Name matches the per-directory convention and the `er_` prefix (3.33).
- [ ] `+access: private` unless the model is on §19.1's public list — and if it is public, it appears in that list, which CI checks both directions (3.30, 3.61).

### Contract and keys
- [ ] `contract: {enforced: true}`; every column declared with name and type (3.3).
- [ ] Primary key declared (3.6). Apply the **§8.3 grain split**: entity-grain → DDL `primary_key`; pair-grain → `dbt_utils.unique_combination_of_columns` **plus a recorded waiver** (3.7).
- [ ] `not_null` constraints wherever they apply — DuckDB enforces all five constraint types (3.8, §8.2).
- [ ] Parity invariants encoded as model-level CHECK constraints (3.9). **Do not extend `isfinite` to `bf_*` columns** — they can legitimately hold `Infinity` (R4, §3.4).
- [ ] No `foreign_key` (3.10).
- [ ] `materialized: table` (3.11, per D11). No `ephemeral`, and `incremental` is not carved out — see RC10 before writing an incremental model.
- [ ] Model-JSON-derived columns are contracted via `columns: "{{ var('er_gamma_columns') }}"` (3.23, §9). Ordinary contracts cannot express them (M2).

### Documentation
- [ ] Description carries all six headings, checked literally (3.5, §10.1): **Purpose · Grain · Upstream · Splink parity · Determinism · Caveats**.
- [ ] Description exceeds `er_min_model_description_chars` (40).
- [ ] Every column has `data_type` and a description over `er_min_column_description_chars` (10), saying what it *means* and whether it is nullable **and why** — in entity resolution missingness is a signal, not an oversight (3.4, §10.2).
- [ ] **Caveats** names what will silently break if someone "improves" this model. This section earns its place; `er_stg_input`'s warns against adding a `lower()` that would diverge on every transformed comparison, silently, in the gamma values only.

### Tests
- [ ] Unit test present for fixed-schema models (3.20). Every fixture uses `format: sql` with an **explicit cast on every column** (§12.2) — agate's type inference on `format: dict` inferred DATE from `"not-a-date"`, and the error was the *lucky* outcome.
- [ ] Uniqueness test, and tests by type (3.19).
- [ ] Parents exist before unit tests run — `dbt run --empty --select <parents>` satisfies the `unit` materialization's type read-back cheaply (§12.2, M17).
- [ ] No float tolerance anywhere in a dbt test. dbt compares with exact equality; tolerance lives in the pytest harness (§12.3). Choose fixture m/u so Bayes factors are exact powers of two.
- [ ] Singular tests declare `{{ config(group='er_core') }}` — they belong to no group otherwise and are refused access to a private model (§12.4).

### Determinism
- [ ] No non-deterministic Jinja (3.16) — Tier 1 (`set()`, randomised iteration order) is banned everywhere, no exemptions.
- [ ] No statement-level `ORDER BY` in the model body (3.24).
- [ ] No non-recursive CTE; `WITH RECURSIVE` is permitted (3.67, §7.3). Note 3.68 is unenforced for lack of a parser, so `WITH RECURSIVE` is currently a one-word waiver for 3.67 — do not use it as one.
- [ ] SQLFluff clean under `rules = all`, dialect `duckdb`, templater `dbt` (3.14, 3.15).
- [ ] Content-stable across two runs and under input row-order permutation, after canonical ordering and excluding volatile columns (3.25, §6.3). **Not** parquet byte-identity — compression, metadata, row-group boundaries and timestamps all vary.

### Provenance
- [ ] `er_model_sha` + `er_tf_snapshot_id` columns with `count(distinct …) = 1` tests (3.32, M8).
- [ ] `er_run_id` present, **and** listed in `er_volatile_columns` so it is excluded from every hash (3.48).

### Parity
- [ ] The stage's parity gate is green, implemented from **A.4** (not §6.1).
- [ ] Any deliberate divergence has **both** a `docs/divergence-log.md` entry and a pinning test — checked in both directions (3.49).
- [ ] PR carries a Splink source permalink for any parity-affecting change (3.36 — convention, a human gate).

---

## Harness / comparator ticket

- [ ] Lives under `harness/`, not `tests/` (which is dbt's `test-paths`) and not `tests_python/`.
- [ ] **Mutation-tested before it is trusted.** The §12.7 catalogue applies at every parity stage and **no mutant may survive** (3.46): drop a pair · add a pair · flip a `match_key` `'1'`→`'2'` · coerce `match_key` to INT · change one gamma by ±1 · shift one `match_weight` by 2× tolerance · swap `unique_id_l`/`unique_id_r` on one pair · merge two clusters · split one · relabel one component · inject one NULL key.
- [ ] Each mutant asserts the **expected localisation string**, not merely failure. A comparator that fails for the wrong reason is still broken, and it is the harder defect to notice later.
- [ ] Handles the two verified traps: `match_key` is **VARCHAR** in Splink (`blocking.py:203-206`), so a dtype-coercing comparator normalises a real divergence away; and Splink's clustering emits **spurious NULL-node rows** on dangling edges (`connected_components.py:89-100`), so a comparator that drops NULL keys before diffing also hides real rows.
- [ ] Baselines carry a provenance manifest: Splink version, model-JSON sha, seed, DuckDB version, producing commit (3.62). Add a sqlglot version field (F2's sequel edit).
- [ ] Never uses `state:modified` or `--defer` — `same_body` compares unrendered `raw_code` and cannot see `--vars`, so a parity job would skip every uncontracted model and report green on stale scores (3.31, §6.2).
- [ ] Runs on `linux/amd64` if it asserts float-exact equality or hashes DOUBLE columns (3.59, §22.1). Set equality, partition equality, integer/string comparisons and row counts are platform-independent.
- [ ] `PYTHONHASHSEED=0`, `TZ=UTC`, `LC_ALL=C` set *and asserted* (3.58, §22.2).

---

## Gate / standard ticket

Per §23, every new or changed standard states three things **in the same PR**:

- [ ] Its **mechanism** — the script, check or config that enforces it.
- [ ] Its **gate** — C (compile) · P (pre-commit) · B (build) · CI.
- [ ] Its **`verify_gates.py` injection** (3.38): inject the violation in a scratch copy, assert non-zero exit **and** the expected error string. *A rule without an injection has not been shown to fire.*
- [ ] A stable new id. 3.1–3.37 keep their numbers permanently; additions continue from the current maximum. Ids are never reused.
- [ ] If it is a Make target, it is also a CI step — and vice versa (§17). *"A gate that exists only in CI does not exist."*
- [ ] Removing a rule instead? State **why the failure it prevented is no longer possible** (3.65). This is the requirement most likely to be skipped, because removing a rule always feels like cleanup.

Waivers, if the ticket needs one (§18) — there is exactly one legal way:
reason recorded in `config.meta` under a key the tooling knows · exemption scoped in **one** place, never a scattered `-- noqa` · capped where a cap makes sense · a var in preference to an edit, except for **hardening** values which are deliberately not vars (§2.1) · echoed on every run. *"A waiver that is greppable is a waiver someone can revisit."*

---

## Spike ticket

- [ ] States the **question**, not the task.
- [ ] States the **timebox**.
- [ ] States a **written kill criterion** — what result means stop, in advance. M21 flags that spikes with real failure probability currently carry the same visual weight as a one-`WHERE`-clause model.
- [ ] Names what the answer unblocks and what happens to the plan under each outcome.
- [ ] Output is a **decision plus evidence**, not a model. Evidence classes: `[SRC]` read from source on disk · `[RUN]` executed this session, reported with the query shape · `[RECON]` executed in the prior verification corpus · `[DERIVED]` arithmetic from those. Anything unlabelled is reasoning and is marked **UNVERIFIED**.

---

## Decision ticket

Closes a `DesignDoc.md` §B.3 register row or a `DbtBestPractices.md` Appendix B open decision. Deliverable is a document edit, never code.

- [ ] The decision is recorded in §B.3 with a **status** and a **value in force**.
- [ ] Every section the decision invalidates carries an explicit `Supersedes:` line naming both the finding and the companion sections (3.45, §1.1). D11 named `dbt_project.yml` but not §7, §3.7, §3.11 or §8.3 — which is how four sections stayed stale while the decision replacing them sat three pages away.
- [ ] The sections that implemented the old answer are updated **in the same PR**.
- [ ] If the decision is cross-document, the reconciliation ledger is updated once, not twice. RC27: status now lives in two inventories and for one revision they disagreed — the same two-normative-inventories pathology R3 diagnoses, one level up.
- [ ] The decision is **the user's**. §B.5 item 6: *"No open decision was resolved unilaterally."* A doc's stated **Recommendation** is carried forward attributed as a recommendation, never adopted as a decision.

---

## Programme-level Definition of Done (§8)

1. Stage 0–11 ACs green in CI. *(RC14: omits Stage 12 — resolve or state why.)*
2. `dbt build --vars "{er_model: …, er_threshold: 0.9}"` on a fresh clone produces golden records end-to-end with **zero Python in the dbt run** — the run is Python-free because the JSON carries rendered SQL; the sidecar's two resolutions happen at compile time. "Zero Python anywhere" is not claimed.
3. Ten green nightly differential runs. *(M18 recommends splitting into parallel correctness + concurrent stability; an uncompressible ≥10-day tail otherwise.)*
4. A divergence log covering every Splink subtlety found, each pinned by a test — including the deliberately-replicated `min(match_key)` VARCHAR bug (S4).
5. `PARITY.md` stating, with evidence links, exactly what is identical and what is bounded, per **A.4**.
