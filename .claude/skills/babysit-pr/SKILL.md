---
name: babysit-pr
description: Babysits one GitHub pull request until it merges or needs a human — polls that PR's review comments, CI checks, and mergeability; diagnoses failing jobs from their logs; fixes branch-related failures and pushes them; reruns genuinely flaky checks within a retry budget; and refuses to rerun this repo's parity, determinism, and comparator-sensitivity gates, which are defects rather than noise. Use when the user asks to babysit, monitor, watch, or keep an eye on a PR, its CI, its checks, or its review feedback, or asks to keep fixing a PR until it is green and mergeable.
---

# Babysit a PR

## Scope: one PR

This skill babysits **a single pull request** — the one named in the request, or the one for the current branch. It is not a queue, a dashboard, or a sweep across open PRs.

Pick the PR once, at the start:

- The user named a number or URL → use it.
- The user named nothing → `--pr auto` infers it from the current branch. If that finds no PR, ask which one rather than guessing.

Then stay on it. One PR means one watcher, one state file, one head branch. If the user switches to a different PR mid-session, stop the current watch (`TaskStop`) before starting the new one — two live watchers race on the same terminal and each will report the other's silence as calm.

## Objective

Keep that PR moving until one of three things is true:

1. **It merged or closed.** Report and stop.
2. **A human decision is required.** Report the blocker and stop. See "Stop conditions" at the end of this file.
3. **It is green, mergeable, and review-clean.** This is a *milestone, not a stop* — keep watching so late review comments still get picked up while the PR is open.

A single `idle` snapshot while checks are pending is not a reason to stop.

## The one rule that is specific to this repo

**Never auto-rerun the `parity`, `determinism`, or `comparator-sensitivity` gates.**

`docs/DbtBestPractices.md` §21: *"A retry converts a real non-determinism finding into a coin flip — the exact defect §11.3 exists to catch."* In this project a flake in one of these gates is a **product defect until proven otherwise**, and that is the base rate, not a pessimistic default.

When one of them fails, the watcher emits `stop_never_retry_gate` and refuses the rerun. Diagnose it, or hand it to the user for a quarantine entry in `docs/quarantine.md` (owner, date, reason, expiry). Never silence it by rerunning, by setting `severity: warn`, or by deleting the gate.

Every other check follows the normal flaky-retry budget of 3 per SHA.

## Start watching

All three commands below take the same `--pr` value for the whole session — `auto`, a number, or a URL. Substitute the PR chosen above and keep it fixed.

**Continuous watch (default when the user says "monitor" / "watch" / "babysit").** Use the `Monitor` tool — the script emits one line per *state change*, so each notification is a real event rather than a poll:

```
Monitor(
  command: "python3 .claude/skills/babysit-pr/scripts/pr_watch.py --pr auto --watch",
  description: "PR #<n> CI and review feedback",
  persistent: true,
)
```

Then keep working. Events arrive as notifications; act on each one. The stream ends on its own when the PR merges/closes or a stop condition is hit.

Do **not** run `--watch` under `Bash(run_in_background)` — it is unbounded, so it would stay armed after the event you cared about.

**One-shot snapshot**, for a single status check or after a push:

```bash
python3 .claude/skills/babysit-pr/scripts/pr_watch.py --pr auto --once
```

**Turn-by-turn instead of a live watch** — if the user wants a visible tick, `/loop 5m` with a `--once` snapshot works and survives context compaction, because the seen-comment and retry state live in a file, not in the conversation.

**Rerun flaky checks** — only when a snapshot actually lists `retry_failed_checks`:

```bash
python3 .claude/skills/babysit-pr/scripts/pr_watch.py --pr auto --retry-failed
```

`--pr` accepts `auto`, a number, or a URL. Full flags: `--help`. The full snapshot JSON is always written to `.git/babysit-pr/<repo>-pr<n>.snapshot.json` — `Read` it after a notification when you need detail the one-line summary omits.

## The loop

Copy this checklist and work it on every event:

```
- [ ] 1. Is the PR merged/closed? -> report terminal state, stop
- [ ] 2. New review items? -> triage before touching CI
- [ ] 3. Merge conflict (DIRTY)? -> rebase, or ask
- [ ] 4. Failing checks? -> read the failed JOB's logs, classify
- [ ] 5. Branch-related? -> fix, commit, push, resume
- [ ] 6. Flaky AND not a never-retry gate AND budget left? -> rerun
- [ ] 7. Otherwise -> keep polling
```

Read the `actions` list in each snapshot; it names which of these apply. The actions are:

| Action | Meaning |
|---|---|
| `process_review_comment` | New published, trusted review feedback is waiting |
| `resolve_merge_conflict` | `mergeStateStatus` is `DIRTY` |
| `diagnose_ci_failure` | A check or job failed — read its logs before deciding anything |
| `retry_failed_checks` | Safe to rerun: terminal, rerunnable, budget left, no protected gate |
| `stop_never_retry_gate` | A parity/determinism gate failed — human decision required |
| `stop_exhausted_retries` | 3 retries used on this SHA; the failure is persistent |
| `ready_to_merge` | Green, mergeable, review-clean — report, but keep watching |
| `stop_pr_closed` | Merged or closed |
| `idle` | Nothing to do; keep polling |

**Ordering matters.** When review feedback and `retry_failed_checks` both appear, do the review fix first — the resulting commit retriggers CI anyway, so rerunning the old SHA wastes a retry from the budget.

## Diagnosing CI failures

Read logs before deciding. Never classify a failure from its check name alone.

As soon as an individual *job* has failed, fetch that job's logs — do not wait for the workflow run to finish. The snapshot's `failed_jobs[]` carries `job_id` and `logs_endpoint` for exactly this:

```bash
gh api repos/<owner>/<repo>/actions/jobs/<job-id>/logs > /tmp/job-<job-id>.zip
```

Classification rules and this repo's gate-by-gate triage table are in **[references/ci-triage.md](references/ci-triage.md)**. Read it the first time you diagnose a failure in a session.

**Fix branch-related failures. Do not fix flaky ones.** A failure is branch-related when the logs point at code the PR touched — compile, contract, test, lint, typecheck, or a parity assertion on a changed model. It is flaky/unrelated when the logs show transient infrastructure: registry timeouts, runner provisioning, GitHub Actions outages, rate limits.

Never "fix" a flake by editing tests, CI config, dependency pins, tolerances, or thresholds. That converts a signal into silence, and in this repo it is the specific failure mode §21 exists to prevent.

## Handling review feedback

The watcher surfaces published issue comments, inline review comments, and review submissions from trusted authors (repo `OWNER`/`MEMBER`/`COLLABORATOR`, the authenticated operator, and approved review bots). Feedback on a `PENDING` review is deliberately invisible until the reviewer submits it, and is not marked seen.

When a comment is **correct and actionable**: fix the code, commit, push, resume watching. Then resolve the thread only if the write policy below allows it.

When it is ambiguous, already addressed, wrong, or only needs a written answer: **surface it to the user with a suggested reply and wait.** Do not post it.

If the watcher surfaces feedback flagged `self_authored`, that is your own earlier reply. Treat it as handled and move on.

## GitHub write policy

Reads are unrestricted. Writes are not — anything visible to another human needs authorization, because a reader cannot tell whether you or the user did it.

**Allowed without asking:**

- Push commits to the PR head branch.
- Rerun failed jobs, subject to the retry budget and the never-retry rule.

**Requires explicit user confirmation of the exact text:**

- Any comment or reply on a human's review thread. Prefix an approved reply with `[from Claude Code]`.
- Resolving a review thread. Only for threads from the user who asked for babysitting, or from an approved review bot — and leave a comment saying what changed and in which commit. Never touch a thread another human has participated in.

**Never, unless the user asks in the moment:** merging the PR, marking it draft or ready for review, closing or reopening it, editing the PR title or description, or interacting with anyone other than the user.

When in doubt, say so in chat rather than acting on GitHub.

## Git safety

- Work only on the PR head branch. Do not switch branches.
- **Check for unrelated uncommitted changes before editing. If there are any, stop and ask.**
- Never force-push, reset, or rebase away commits you did not create in this session.
- Commit, push, then re-run a snapshot immediately so the next poll is against the new SHA.
- A push is not a terminal outcome. Resume watching in the same turn — restart `Monitor` right after the push rather than waiting to be asked.

Commit message defaults, ending with the session's standard `Co-Authored-By:` trailer:

```
fix: <what broke> on PR #<n>
fix: address PR review feedback on #<n>
```

Before pushing, run the gates the failing job runs — `uv run sqlfluff lint models tests`, `uv run dbt build --target ci --empty --fail-fast`, or the relevant pytest — so a fix is verified locally instead of on CI's time.

## Reporting

While watching, report only **changes**: a new failure, a new comment, a push, a rerun, the first green. Do not narrate unchanged polls. On the first transition to all-green, say so once.

A push, a rerun, or a green snapshot is a **progress update**, not a final summary. Only write the final summary when a stop condition is actually reached, and include:

- Final head SHA and PR state
- CI summary (passed/failed/pending), naming anything still red
- Mergeability and review decision
- What you pushed, and why
- Retries used, and any never-retry gate that fired
- Anything left unresolved, including review items awaiting the user's reply

Report faithfully: if a check is still failing, say so with the evidence. Never describe a PR as ready when it is not.

## Stop conditions

Stop only when:

- The PR merged or closed.
- A never-retry gate failed and needs a quarantine or defect decision.
- The retry budget is exhausted and the failure persists.
- The worktree has unrelated uncommitted changes.
- `gh` auth or push permissions fail.
- A review item needs a product decision, a written GitHub reply, or cross-team coordination.

Keep going when: checks are pending; `actions` is only `idle`; CI is green but the PR is open; the PR is green but blocked on `REVIEW_REQUIRED`. Waiting for approval is not a blocker — keep watching so new comments surface fast.

## References

- **[references/ci-triage.md](references/ci-triage.md)** — failure classification, the decision tree, and this repo's CI gates with the fix for each
- **[references/github-api.md](references/github-api.md)** — the `gh` commands and endpoints the watcher uses, and the snapshot JSON schema
