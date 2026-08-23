# CI Triage

## Contents

- [Decision tree](#decision-tree)
- [Classification: branch-related vs flaky](#classification-branch-related-vs-flaky)
- [This repo's gates and what a failure means](#this-repos-gates-and-what-a-failure-means)
- [The never-retry gates](#the-never-retry-gates)
- [Review comment triage](#review-comment-triage)
- [Stop and ask](#stop-and-ask)

## Decision tree

1. PR merged or closed → **stop**.
2. Merge state is `DIRTY` → rebase on the base branch, or ask if the conflict is non-trivial. Do this before chasing CI; a conflicted PR's checks are stale.
3. Any check failed:
   - Read the failed **job's** logs first. Never classify from the check name.
   - Job failed but the run is still in progress? Fetch that job's logs now — do not wait for the run.
   - Failure is a `parity`, `determinism`, or `comparator-sensitivity` gate → **stop**, see [never-retry](#the-never-retry-gates).
   - Branch-related → fix locally, verify locally, commit, push.
   - Flaky/unrelated **and** all checks terminal **and** budget remaining → rerun failed jobs.
   - Flaky/unrelated but not safely rerunnable, or the budget is spent → **stop** and report.
   - Checks still pending with no failed job yet → wait.
4. Independently of CI, triage new review items every pass.

## Classification: branch-related vs flaky

**Branch-related — fix it.** The logs point at code the PR touched:

- Compile, parse, or Jinja errors in changed models or macros
- `dbt build` contract violations: a column missing, renamed, or the wrong type
- DDL constraint failures (`not_null`, `unique`, `primary_key`, `check`) on changed models
- dbt test or pytest failures in the changed area
- SQLFluff, yamllint, ruff, or mypy violations in changed files
- dbt-bouncer failures naming a model the PR added or renamed
- A parity assertion that moved because the PR changed the scoring, blocking, or clustering path

**Flaky or unrelated — do not fix.** The logs show transient or external failure:

- Registry, network, or DNS timeouts pulling dependencies (`uv sync`, `dbt deps`)
- Runner provisioning or image startup failures
- GitHub Actions service degradation
- Rate limits or upstream service outages
- A job cancelled by `cancel-in-progress` because a newer commit superseded it

Cancelled-by-concurrency is the most common false alarm: the run for an older SHA gets cancelled by design. Check the run's `head_sha` against the PR's current head before treating a cancellation as a failure.

**Never fix a flake by editing** tests, tolerances, CI config, dependency pins, thresholds, or `severity`. That trades a signal for silence.

If classification is genuinely ambiguous, diagnose once more before choosing a rerun.

## This repo's gates and what a failure means

The CI topology is specified in `docs/DbtBestPractices.md` §15 and the workflow in §C.7. Match the failing check name to its gate:

| Check | What failed | First move |
|---|---|---|
| `lint (blocking)` | SQLFluff, yamllint, ruff, mypy, `dbt parse`, enforcement scripts, or a stale `uv.lock` / `package-lock.yml` | Reproduce with `uv run sqlfluff lint models tests` or `uv run pre-commit run --all-files`. Almost always branch-related and mechanical. |
| `build + tests (blocking)` | `dbt seed`, `dbt build --empty` (the contract smoke test), the full build, `er_assert_project_standards`, or `dbt docs generate` | An `--empty` failure means the contracted column set changed — reconcile the `.yml` with the model. A full-build failure is a data invariant. |
| `bouncer` | dbt-bouncer conventions, coverage floors, or the 1:1 `.sql` ↔ `.yml` rule | Read the check name in the output; it maps to a numbered standard in §3. Usually a missing `.yml`, description, or contract. |
| `parity` | The pytest harness diverged from a frozen Splink baseline | **Never rerun.** See below. |
| `determinism` | Two builds, or a row-permuted build, produced different content hashes | **Never rerun.** See below. |
| `comparator-sensitivity` | A mutant survived the parity comparator — the gate cannot detect a defect it should | **Never rerun.** See below. |
| `verify-gates` | A §3 standard was injected and did *not* fail — an enforcement gate is inert | The gate is broken, not the code. High severity. |
| `project-evaluator` | DAG or governance rule violation | Usually a naming or layering issue in a new model. |
| `python-tests` | pytest over `scripts/` and `dbt_bouncer_checks/`, or the coverage floor | Branch-related. Every script must ship a failing-case test (§3.57). |
| `consumer-smoke` | The published install path broke | A file missing from the package, or a dependency conflict a consumer would hit. Not reproducible from `integration_tests/`. |
| `ci-gate` | Aggregator only | Never the real failure. Find the upstream job that went red. |

`ci-gate` is the single required status check, so it goes red whenever anything else does. Always look past it.

## The never-retry gates

`parity`, `determinism`, and `comparator-sensitivity` are **never** rerun automatically. From §21:

> No gate is retried automatically. No `pytest-rerunfailures`, no workflow `retry:` on parity or determinism jobs. A retry converts a real non-determinism finding into a coin flip — the exact defect §11.3 exists to catch. This is the load-bearing rule.
>
> A flake in a determinism or parity gate is a product defect until proven otherwise. In this project that is the base rate, not a pessimistic default.

The watcher enforces this: it emits `stop_never_retry_gate` instead of `retry_failed_checks`, and `--retry-failed` refuses with an explanation.

When one fires:

1. Read the harness output. A parity failure names the stage and the diverging column; a determinism failure names the two content hashes.
2. If the PR touched that stage, it is a defect in the PR. Fix it.
3. If it did not, it is still a defect until proven otherwise. Common real causes: a missing `ORDER BY`, a non-deterministic aggregate, a `USING KEY` recursion without the `GROUP BY` on the key (DesignDoc D4 trap 1 — six different answers in six runs at `threads=8`), or a float path that skipped Splink's clamp.
4. Only a human may quarantine it. Surface the failure with a proposed `docs/quarantine.md` entry — owner, date, reason, expiry — and wait. Quarantine is not deletion and not `severity: warn`, which §12.6 shows is inert anyway.

## Review comment triage

**Fix in code when** the comment is technically correct, actionable on this branch, does not conflict with the user's stated intent, and needs no unrelated refactor.

**Surface to the user instead when** the comment is ambiguous, conflicts with an explicit instruction, requires a product or design decision, needs only a written answer or a disagreement, or the worktree is in a state that makes safe editing uncertain.

Already-resolved threads are non-actionable — ignore them unless new unresolved follow-up appears.

## Stop and ask

- Unrelated uncommitted changes in the worktree
- `gh` auth or permission failure, or the branch cannot be pushed
- Failures persisting after the retry budget
- A never-retry gate failure needing a quarantine decision
- Review feedback requiring a product decision or a written GitHub reply
- Anything that would require a GitHub write outside the allowed list in SKILL.md
