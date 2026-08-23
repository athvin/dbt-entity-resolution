# Merge policy

## Contents

- [The merge mandate](#the-merge-mandate)
- [Preconditions, and why each one](#preconditions-and-why-each-one)
- [Merge method](#merge-method)
- [BEHIND, DIRTY, and UNSTABLE](#behind-dirty-and-unstable)
- [When the merge call fails](#when-the-merge-call-fails)
- [After the merge](#after-the-merge)

## The merge mandate

`babysit-pr` forbids merging unless the user asks in the moment. This skill is that ask, scoped:

- The mandate covers **PRs opened inside this ship loop**. Someone else's PR, or one opened before the loop started, still needs an explicit request.
- The mandate is for **this session**. It does not carry into the next one.
- **Confirm mode** is the default: report the PR is green with the check summary, ask once, merge on yes.
- **Autonomous mode** is granted by "ship it" / "merge when green" / "keep going". It removes the per-PR question, not the preconditions.

Autonomous mode never authorizes merging past a failing precondition. It authorizes not asking twice about a PR that already satisfies all of them.

## Preconditions, and why each one

Check them against a fresh `pr_watch.py --once` snapshot. Each maps to a snapshot field.

| Precondition | Field | Why |
|---|---|---|
| All checks terminal, none failed | `checks.all_terminal`, `checks.failed_count` | A pending check is an unrun gate. `ci-gate` aggregates the rest, so its green is the one that counts — but read the individual failures, not just the aggregate |
| No never-retry gate hit | `checks.never_retry_hit` | A parity, determinism, or comparator-sensitivity failure is a product defect (§21). Merging it moves the defect to main, where it is harder to bisect and blocks everyone |
| Merge state `CLEAN` | `pr.merge_state_status` | Anything else means GitHub itself will refuse, or the merge produces a tree nothing tested |
| Not `CHANGES_REQUESTED` | `pr.review_decision` | A human blocked it. Only that human unblocks it |
| No unresolved human review thread | `new_review_items` | An unanswered comment merged past reads as ignoring the reviewer, and cannot be un-merged |
| Not a draft | `pr.is_draft` | Draft means the author is not done. If the author is you, mark it ready first and let CI settle |
| PR opened in this loop | — | The mandate is scoped. Merging someone else's work is their call |
| Confirm mode: user said yes | — | The whole point of the mode |

`REVIEW_REQUIRED` deserves its own note. It is a human gate, not a CI state, and this repo has two of them by design (§19.4). Waiting on it is not a blocker to report and abandon — `babysit-pr` keeps watching so new comments surface fast. But it is an absolute bar to merging.

**Zero checks is not green.** A PR whose `checks.total_count` is 0 has not been verified by anything — which is every PR while `.github/workflows/` is absent. Run `make ci` locally on the branch, merge on that basis, and say in the report that local gates are what verified it.

## Merge method

```bash
gh pr merge <n> --squash --delete-branch
```

**Squash by default.** One task, one commit on main. That is what makes the post-merge verification unambiguous (one SHA to check), a `git bisect` meaningful (one change per step), and a revert a single command. The repo allows merge commits and rebase merges too; use `--merge` only when the user wants the individual commits kept, and never rebase-merge a branch whose intermediate commits were not individually green.

**`--delete-branch` is explicit** because the repository has `deleteBranchOnMerge: false`. Without it, dead branches accumulate and `--pr auto` resolution gets ambiguous later.

**Do not use `--auto`.** Auto-merge fires whenever the last check turns green, with no precondition pass and nobody watching. That is exactly the review-thread-and-never-retry-gate check this skill exists to perform.

## BEHIND, DIRTY, and UNSTABLE

| State | Meaning | Move |
|---|---|---|
| `DIRTY` | Merge conflict with main | Rebase on main, re-run local gates, push. `babysit-pr` handles this; return to step 4 afterwards |
| `BEHIND` | Main moved ahead; the branch has not merged it | Update the branch and let CI re-run. Merging `BEHIND` ships a combination nothing tested |
| `UNSTABLE` | Non-required checks are failing | Read them. If a failing check is not required, it is either a gate that should be required or noise that should not exist — say which, do not just merge |
| `BLOCKED` | A required check or review is missing | Find which. `REVIEW_REQUIRED` waits for a human; a missing required check waits for CI |

Updating a `BEHIND` branch:

```bash
git fetch origin main
git rebase origin/main
make ci                       # re-verify against the new base before pushing
git push --force-with-lease   # only on a branch whose commits are all yours
```

`--force-with-lease`, never plain `--force`, and only when every commit on the branch was created in this session. If someone else has pushed to the branch, stop and ask.

## When the merge call fails

`gh pr merge` failing is informative, not a retry prompt. Read the error:

- **"Pull request is not mergeable"** — state changed between your snapshot and the merge. Re-snapshot; usually main moved and the PR is now `BEHIND`.
- **"Required status check … is expected"** — a required check has not reported. It may not have started. Do not merge with admin override.
- **"Changes requested"** / **"Review required"** — a human gate. Stop.
- **403 / "not authorized"** — permission problem. Stop and tell the user; do not try another path to the same write.

Never reach for `--admin`. It exists to bypass exactly the gates this loop is built to respect.

## After the merge

1. Capture the merge SHA: `gh pr view <n> --json mergeCommit --jq '.mergeCommit.oid'`. The PR head SHA is **not** the merge SHA under squash, and step 7 verifies the merge SHA.
2. Update the local checkout: `git checkout main && git pull --ff-only`.
3. Go to step 7. A merged PR is not a shipped change until main is verified.
