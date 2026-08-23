# Post-merge verification and recovery

## Contents

- [Why the merge SHA gets its own pass](#why-the-merge-sha-gets-its-own-pass)
- [Verifying main](#verifying-main)
- [When there is no push CI](#when-there-is-no-push-ci)
- [Main is red: the decision](#main-is-red-the-decision)
- [The fix-forward PR](#the-fix-forward-pr)
- [The revert PR](#the-revert-pr)
- [Never-retry gates on main](#never-retry-gates-on-main)
- [Taking the next task](#taking-the-next-task)

## Why the merge SHA gets its own pass

The PR was green at its head SHA. The commit on main is a different commit:

- **Squash** replays the whole change onto main's current tip. New parent, new tree, new SHA.
- **Semantic conflicts merge cleanly.** Your PR renames a column; someone else's PR, green in parallel, adds a model that reads the old name. Both merge. Main breaks. No conflict marker appears anywhere.
- **The `--empty` contract smoke test and the parity harness both run against the merged tree**, not against either branch.

`babysit-pr` cannot see any of this — it stops at the merge. This is the gap the ship loop closes.

## Verifying main

```bash
python3 .claude/skills/ship-pr/scripts/main_verify.py --commit <merge-sha> --once
```

Or under `Monitor` with `--watch` for one notification per state change. Flags: `--branch` (default `main`), `--repo`, `--poll-seconds` (minimum 30), `--timeout-seconds` (default 3600, sized for a full seed → build → parity → two determinism builds run), `--snapshot-file`.

The full snapshot is written to `.git/ship-pr/<repo>-main.json`. `Read` it when the one-line summary is not enough.

Snapshot fields worth knowing:

| Field | Use |
|---|---|
| `commit.on_base` | False means the merge SHA is not an ancestor of main — the merge did not land, or the SHA is wrong |
| `base.moved_on` | Main has advanced past this commit. A `cancelled` run for your SHA may be cancel-in-progress by design, not a failure |
| `checks.never_retry_hit` | Named gate if parity/determinism/comparator-sensitivity failed |
| `checks.excluded_failed_names` | Runs failing on this SHA that are **not** this commit's verification — see below |
| `checks.runs_truncated` | The run list is incomplete. Verify locally as well; do not trust the counts |
| `failed_jobs[].logs_endpoint` | Fetch this before classifying anything |
| `local.*` | Whether your checkout is on main, clean, and current |

**Only `push`, `merge_group`, and `workflow_dispatch` runs count as verification.** A `schedule` run shares the base branch's head SHA, so this repo's `nightly` job (§15) would otherwise be counted as the verification of whatever merged last — and a failing nightly would read as a broken merge. Those runs are reported in `excluded_failed_names` instead of counted. A failing nightly is still a real finding; it is just not this commit's.

Diagnose from the failing job's log, never from the check name:

```bash
gh api repos/<owner>/<repo>/actions/jobs/<job-id>/logs > /tmp/job-<job-id>.zip
unzip -p /tmp/job-<job-id>.zip | tail -200
```

The classification rules and this repo's gate-by-gate triage table are the same ones the PR loop uses: `.claude/skills/babysit-pr/references/ci-triage.md`.

## When there is no push CI

`actions: ["verify_locally"]` means no workflow ran for the merge commit. While `.github/workflows/` is absent, that is every commit — the §15 topology is designed but not wired.

It does not mean verification is skipped. It means you run it:

```bash
git checkout main && git pull --ff-only
git status --porcelain     # must be empty; a dirty tree invalidates the run
make ci
```

Then report precisely what verified the change: *"verified by local `make ci` on main at `<sha>`; no push CI exists yet"*. Never write "CI passed on main" when no CI ran. That sentence is how a false green gets recorded as a fact.

If `make ci` does not exist yet either, run the gates that do exist, name them, and name the ones you could not run.

## Main is red: the decision

Main being red is time-sensitive in a way a red PR is not — every branch cut from now inherits the breakage, and the next person's red CI is not their fault.

Work this in order:

1. **Is it yours?** Compare the failing job's log against the change you merged. `base.moved_on` plus a `cancelled` conclusion often means a later push superseded your run — not a failure. Check `head_sha` before treating a cancellation as breakage.
2. **Is it a never-retry gate?** Then it is a defect that reached main. See below. Do not rerun it.
3. **Do you understand the cause, and is the fix small and contained?** → **fix forward**.
4. **Anything else** — cause unclear, fix touches the scoring/blocking/clustering path, fix needs a baseline regenerated, or you are more than a few minutes from a verified answer → **propose a revert** and get the user's confirmation.

Bias toward revert when in doubt. A revert is cheap, reversible, and restores everyone else immediately; a speculative fix-forward on a red main is two unverified changes stacked.

Never rerun a red main job hoping it passes. §21's rule is about what a retry does to the signal, and it does not soften because the failure is on main.

## The fix-forward PR

A new PR, re-entering the loop at step 2. Not a direct push to main, not an amend.

- Branch: `fix/<what-broke>`
- Title: `fix: <what broke> on main after #<n>`
- Body: what broke, the failing job link, why the original PR did not catch it, and what now would.

That last point matters more than the fix. A break that reached main is a gate that did not exist or did not run. If the answer is "nothing would have caught it," say so in the PR — that is a finding about the gate topology, and §3's enforcement matrix is where it belongs.

Run `make ci` locally against the fix before pushing. Then babysit it like any other PR.

## The revert PR

```bash
gh pr create --base main --title "revert: #<n> — <what broke>" ...
```

Generate it with `git revert -m 1 <merge-sha>` for a merge commit, or `git revert <sha>` for a squash commit, on a fresh branch off main.

**A revert is still a PR.** It goes through CI. A revert that itself breaks main is a real outcome, usually when later commits already depend on the reverted change.

Confirm with the user before opening it. A revert is visible to everyone watching the repository and undoes work someone chose to merge — including, possibly, work that was not yours. State plainly: what broke, why revert rather than fix forward, and what the revert undoes.

After a revert merges, the original task is **not** done. It returns to the queue with what you learned attached.

## Never-retry gates on main

`parity`, `determinism`, and `comparator-sensitivity` failing on main is the most serious outcome this loop can produce. A defect the gates exist to catch is now on the base branch.

`main_verify.py` emits `stop_never_retry_gate` and stops. Then:

1. **Do not rerun.** §21: *"A retry converts a real non-determinism finding into a coin flip."* On main this is worse, not better — a coin flip on the base branch is how a real non-determinism bug gets triaged as "CI is flaky" for weeks (§11.3).
2. **Read the harness output.** A parity failure names the stage and the diverging column; a determinism failure names the two content hashes.
3. **Report to the user with the evidence and a recommendation.** Revert is usually right: it restores a known-deterministic main while the defect is investigated.
4. **Quarantine is a human decision only.** If the user chooses it, the entry goes in `docs/quarantine.md` with owner, date, reason, and expiry (§21). Never `severity: warn` — §12.6 shows it is inert under `error: all`. Never deletion.

Common real causes, from DesignDoc: a missing `ORDER BY`; a non-deterministic aggregate; a `USING KEY` recursion without the `GROUP BY` on the key (D4 trap 1 — six different answers in six runs at `threads=8`); a float path that skipped Splink's clamp.

## Taking the next task

Only after main is verified green.

Where the next task comes from, in order:

1. A queue the user gave at the start of the loop.
2. The task list, if one is being tracked in this session.
3. The `er-backlog-preflight` skill, which reports what in this programme is startable and what is blocked on an open decision. Take a **startable** item; never take one whose blocking decision is still open.
4. Ask. Do not infer the next task from a TODO comment, an unchecked doc box, or an `[UNVERIFIED]` marker — those are the author's notes, not an assignment.

Before starting it: `git checkout main && git pull --ff-only`, confirm the worktree is clean, then re-enter at step 1 with the new task's scope and the same merge mandate.

Report each shipped change in one line as you go. A loop that ships four tasks and reports once at the end is impossible to interrupt at the right moment.
