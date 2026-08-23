---
name: ship-pr
description: Runs the full ship loop for one task — open a PR, drive it to green with the babysit-pr skill, merge it once the merge preconditions hold, verify the merge commit on main, and then either take the next task or open a fix-forward PR when main breaks. Use when the user asks to ship, land, or merge a change end to end, to take a task all the way to main, to "create a PR and get it merged", to keep working through a queue of tasks autonomously, or to confirm a merge actually landed cleanly on main. For watching a PR that already exists without merging it, use babysit-pr instead.
---

# Ship a change to main

## Scope: one task at a time, all the way to a verified main

This skill owns the **outer** loop: task → branch → PR → green → merged → **verified on main** → next task.

`babysit-pr` owns the **inner** loop: getting one open PR green and review-clean. Do not reimplement it — invoke it at step 4 and let it run to its own stop conditions.

Two things distinguish this skill from just merging:

1. **A merged PR is not a shipped change.** A squash or merge commit is a SHA that never ran CI as part of the PR. It gets its own verification pass on main.
2. **The loop closes.** Main red → fix-forward PR, which re-enters at step 2. Main green → next task, or stop.

Run one task at a time. Do not stack a second PR while the first is unverified — when main goes red, you need to know which change did it.

## The loop

Copy this checklist and work it. Do not skip steps 5 or 7.

```
- [ ] 1. Take the task     — confirm scope and the merge mandate
- [ ] 2. Branch and build  — implement, run local gates
- [ ] 3. Open the PR       — one task, one PR
- [ ] 4. Babysit to green  — Skill(babysit-pr), let it finish
- [ ] 5. Check merge preconditions — every one, no exceptions
- [ ] 6. Merge             — squash, delete the branch
- [ ] 7. Verify on main    — main_verify.py against the merge SHA
- [ ] 8. Green -> next task | Red -> fix-forward PR, back to step 2
```

## Step 1 — Take the task

Establish two things before writing code, because both change what happens later:

**Scope.** One shippable change. If the task needs three PRs, say so and ship the first; do not open all three.

If the task came from a backlog rather than from the user directly, confirm it is actually startable first — `er-backlog-preflight` reports which work is blocked on an open decision. A PR that implements a decision nobody has made gets rewritten, and this loop will happily ship it.

**The merge mandate.** Who presses merge at step 6:

| The user said | Mode |
|---|---|
| "ship it", "get it merged", "merge when green", "keep going" | **Autonomous** — merge without asking once preconditions hold |
| "open a PR and get it passing", or nothing about merging | **Confirm** — report green, ask once, then merge |

When unclear, default to **Confirm** and say which mode you assumed. The mandate covers the PRs *you* open in this loop and nothing else — it never extends to someone else's PR.

## Step 2 — Branch and build

Branch from an up-to-date main. Never commit to main directly, and never push to main directly — even a one-line fix goes through a PR.

```bash
git checkout main && git pull --ff-only
git checkout -b <type>/<short-slug>
```

Stop and ask if the worktree has unrelated uncommitted changes.

Before pushing, run the gates CI runs. `docs/DbtBestPractices.md` §17: every Make target is also a CI step, so a local pass is a CI pass.

```bash
make lint     # sqlfluff + yamllint + ruff + mypy + the repo checks
make build    # dbt seed && dbt build (unit + data tests)
make ci       # everything CI runs, in CI's order — the pre-push gate
```

If the Makefile does not exist yet, run the underlying commands directly and say which gates you could not run.

## Step 3 — Open the PR

Branch naming, the PR body template, and this repo's two unmechanised human gates — the Splink source permalink and the divergence-log entry — are in **[references/pr-authoring.md](references/pr-authoring.md)**. Read it before opening your first PR in a session.

```bash
git push -u origin HEAD
gh pr create --base main --title "<type>: <what changed>" --body-file <path>
```

Open PRs ready for review, not as drafts, unless the user asked for a draft — `babysit-pr` treats `DRAFT` as merge-blocking.

## Step 4 — Babysit to green

Invoke the `babysit-pr` skill on the PR you just opened and let it run to one of its own stop conditions. It handles CI diagnosis, branch-related fixes, flaky retries, review feedback, and the never-retry gates.

Come back to step 5 only when it reports the PR is green, mergeable, and review-clean.

If it stops on a blocker — a never-retry gate, an exhausted retry budget, a review item needing a decision — **the ship loop stops there too**. Report the blocker. Do not merge around it, and do not open a second PR to work past it.

## Step 5 — Check merge preconditions

Every one of these must hold. Verify from a fresh snapshot, not from memory of an earlier poll:

```bash
python3 .claude/skills/babysit-pr/scripts/pr_watch.py --pr <n> --once
```

- [ ] `checks.all_terminal` is true and `checks.failed_count` is 0
- [ ] `checks.never_retry_hit` is null — a parity, determinism, or comparator-sensitivity failure is never merged past
- [ ] `pr.merge_state_status` is `CLEAN`
- [ ] `pr.review_decision` is not `CHANGES_REQUESTED`, and no human review thread is unresolved
- [ ] `pr.is_draft` is false
- [ ] Nothing in `new_review_items` is still awaiting your reply
- [ ] The PR is one you opened in this loop
- [ ] In **Confirm** mode: the user said yes, in this session, to merging this PR

`BLOCKED` on `REVIEW_REQUIRED` is a human gate, not a CI problem. Report it and wait — never merge past a required review.

A green PR with zero checks is not verified, it is unwatched. If `total_count` is 0, treat it like step 7's `verify_locally`: run `make ci` locally before merging, and say that is what you did.

Failure modes, `BEHIND`/`DIRTY` handling, and what to do when the merge call itself fails are in **[references/merge-policy.md](references/merge-policy.md)**.

## Step 6 — Merge

```bash
gh pr merge <n> --squash --delete-branch
```

Squash is the default: one task becomes one commit on main, which is what makes step 7's verification, a later `git bisect`, and a one-command revert all work. Use `--merge` only when the user asks for the individual commits preserved.

Record the merge commit SHA — step 7 needs it:

```bash
gh pr view <n> --json mergeCommit,mergedAt,state --jq '.mergeCommit.oid'
```

## Step 7 — Verify on main

This step is not optional and not a formality. Squashing rewrites the change onto a different parent, so main is running a combination that CI has never seen.

```bash
python3 .claude/skills/ship-pr/scripts/main_verify.py --commit <merge-sha> --once
```

For anything longer than a moment, watch it instead — one line per state change, one notification per real event:

```
Monitor(
  command: "python3 .claude/skills/ship-pr/scripts/main_verify.py --commit <merge-sha> --watch",
  description: "main post-merge verification for PR #<n>",
  persistent: true,
)
```

The snapshot's `actions` list names what to do:

| Action | Meaning |
|---|---|
| `commit_not_on_base` | The merge SHA is not an ancestor of main — the merge did not land, or you have the wrong SHA |
| `wait_for_main_ci` | Runs are pending, or too new to have registered. Keep watching |
| `diagnose_main_failure` | A run failed on main — read the failed job's logs before deciding anything |
| `stop_never_retry_gate` | A parity/determinism/comparator-sensitivity gate failed **on main**. Human decision required |
| `stop_main_red` | Main is red for this commit. Go to step 8's red path |
| `verify_locally` | No push CI ran for this commit — run `make ci` on updated main yourself |
| `main_verified` | Green. The change is shipped |

`verify_locally` fires whenever no workflow ran for the merge commit — which is what happens while `.github/workflows/` is absent and the §15 CI topology is designed but not wired. It does not mean verification is skipped. It means "verified" is you, on updated main, running `make ci` — and saying exactly that rather than claiming CI passed.

```bash
git checkout main && git pull --ff-only && make ci
```

## Step 8 — Next task, or fix forward

**Main verified green.** Report the shipped change in one line: PR number, merge SHA, what verified it. Then:

- The user gave a queue or said "keep going" → take the next task and re-enter at step 1.
- No queue → stop and report. Do not invent the next task.

**Main red.** The decision is fix-forward versus revert, and it is time-sensitive — every other change now branches from a broken main. The decision tree, the revert procedure, and the never-retry-gate case are in **[references/post-merge.md](references/post-merge.md)**.

The short version: cause understood and fix is small → fix-forward PR. Anything else → propose a revert PR and get the user's confirmation. Either way it is a **new PR** that re-enters at step 2 — never a direct push to main.

A fix-forward PR names what it fixes and links the PR that broke main.

## Invariants

These hold in every mode, including autonomous:

- **Never push or force-push to main.** Every change, including a revert, is a PR.
- **Never merge past a red check, a never-retry gate, `CHANGES_REQUESTED`, or a required review.**
- **Never merge a PR you did not open in this loop**, unless the user asks in the moment.
- **Never silence a gate to ship.** Editing a test, tolerance, threshold, pin, or `severity` to make CI green is the §21 failure mode, not a fix.
- **Never close, reopen, retitle, or comment on a PR** outside `babysit-pr`'s write policy.
- **One unverified change in flight at a time.**

## Reporting

While the loop runs, report transitions only: PR opened, green, merged, main verified, main red. Do not narrate unchanged polls — `babysit-pr` and `main_verify.py` already suppress those.

A merge is not the end of the report. The loop's terminal states are: **main verified**, **main red and handed to the user**, or **blocked before merge**.

The final summary carries:

- PR number and URL, merge commit SHA, merge method
- What verified main: the CI runs by name, or "local `make ci` on updated main" when there is no push CI
- Anything shipped with a caveat — a gate that could not run, a check that does not exist yet
- The next task if you are continuing, or what you are waiting on

Report faithfully. A change is shipped when main is verified, not when the merge button was pressed.

## Stop conditions

Stop and hand back when:

- `babysit-pr` hits any of its own stop conditions
- A merge precondition fails and you cannot fix it on the branch
- The merge itself fails, or the branch cannot be pushed
- Main is red and the fix is not a small, understood, fix-forward change
- A never-retry gate fails on main — that is a quarantine or revert decision, and it is the user's
- The task queue is empty

## References

- **[references/pr-authoring.md](references/pr-authoring.md)** — branch and commit naming, the PR body template, this repo's two human gates, and how to size a PR
- **[references/merge-policy.md](references/merge-policy.md)** — the merge mandate, the full precondition list with the reason for each, `BEHIND`/`DIRTY` handling, and merge failure recovery
- **[references/post-merge.md](references/post-merge.md)** — verifying main, the fix-forward-versus-revert decision tree, the revert procedure, and never-retry gates on main
