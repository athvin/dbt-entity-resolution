# GitHub CLI reference

## Contents

- [Snapshot JSON schema](#snapshot-json-schema)
- [Commands the watcher runs](#commands-the-watcher-runs)
- [Diagnosing a failure by hand](#diagnosing-a-failure-by-hand)
- [Exit codes and gotchas](#exit-codes-and-gotchas)
- [Watcher state files](#watcher-state-files)

## Snapshot JSON schema

`--once` prints this to stdout; `--watch` writes it to `snapshot_file` on every poll.

```jsonc
{
  "pr": {
    "number": 42, "title": "...", "url": "...", "repo": "owner/name",
    "head_sha": "489d5b5...", "head_branch": "feature/x",
    "state": "OPEN", "is_draft": false, "merged": false, "closed": false,
    "mergeable": "MERGEABLE|CONFLICTING|UNKNOWN",
    "merge_state_status": "CLEAN|DIRTY|BLOCKED|BEHIND|UNSTABLE|DRAFT",
    "review_decision": "APPROVED|CHANGES_REQUESTED|REVIEW_REQUIRED|"
  },
  "checks": {
    "passed_count": 28, "failed_count": 3, "pending_count": 0, "total_count": 35,
    "all_terminal": true,
    "failed_names": ["lint (blocking)"], "pending_names": [], "failed_links": ["https://..."],
    "never_retry_hit": null            // check name if a parity/determinism gate failed
  },
  "failed_runs": [{"run_id": 123, "name": "ci", "conclusion": "failure", "url": "..."}],
  "failed_jobs": [{
    "run_id": 123, "job_id": 456, "name": "lint (blocking)", "workflow": "ci",
    "conclusion": "failure", "url": "...",
    "logs_endpoint": "repos/owner/name/actions/jobs/456/logs"
  }],
  "new_review_items": [{
    "kind": "issue_comment|review_comment|review",
    "id": 789, "author": "octocat", "self_authored": false,
    "state": "COMMENT|APPROVED|CHANGES_REQUESTED",   // reviews only
    "path": "models/x.sql", "line": 12,              // inline comments only
    "created_at": "...", "url": "...", "body": "truncated to 400 chars"
  }],
  "actions": ["diagnose_ci_failure", "retry_failed_checks"],
  "retry_state": {"used_for_current_sha": 0, "max_flaky_retries": 3, "never_retry_hit": null},
  "state_file": "...", "snapshot_file": "...", "captured_at": 1750000000
}
```

`body` is truncated to 400 characters. Fetch the comment `url` when you need the full text.

## Commands the watcher runs

```bash
# PR metadata — also resolves `--pr auto` from the current branch
gh pr view --json number,url,state,mergedAt,closedAt,headRefName,headRefOid,\
mergeable,mergeStateStatus,reviewDecision,isDraft,title

# Check summary
gh pr checks <n> --json name,state,bucket,link,workflow,completedAt

# Workflow runs for the head SHA
gh api "repos/<owner>/<repo>/actions/runs?head_sha=<sha>&per_page=100"

# Jobs within a run — the source of failed_jobs[]
gh api "repos/<owner>/<repo>/actions/runs/<run-id>/jobs?per_page=100"

# Review feedback (paged manually, 100/page)
gh api "repos/<owner>/<repo>/issues/<n>/comments"   # PR-level comments
gh api "repos/<owner>/<repo>/pulls/<n>/comments"    # inline review comments
gh api "repos/<owner>/<repo>/pulls/<n>/reviews"     # review submissions

# Rerun failed jobs only
gh run rerun <run-id> --failed
```

An inline comment's `pull_request_review_id` links it to its parent review. Parents in state `PENDING` are drafts only their author can see — the watcher skips those comments and does **not** mark them seen, so they surface once the review is submitted.

## Diagnosing a failure by hand

Prefer the job logs endpoint. It works as soon as a single job fails, whereas `gh run view --log-failed` is scoped to the whole run and often returns nothing until the run completes.

```bash
gh api repos/<owner>/<repo>/actions/jobs/<job-id>/logs > /tmp/job-<job-id>.zip
unzip -p /tmp/job-<job-id>.zip | tail -200

# Fallback, once the run is complete
gh run view <run-id> --log-failed

# Which step failed, without downloading logs
gh api "repos/<owner>/<repo>/actions/runs/<run-id>/jobs" \
  --jq '.jobs[] | select(.conclusion=="failure") | {name, steps: [.steps[] | select(.conclusion=="failure") | .name]}'
```

Resolving a review thread needs GraphQL — there is no REST endpoint. Only do this within the write policy in SKILL.md:

```bash
gh api graphql -f query='
  mutation($id:ID!){ resolveReviewThread(input:{threadId:$id}){ thread { isResolved } } }' \
  -f id=<thread-node-id>
```

Thread node IDs come from:

```bash
gh api graphql -f query='
  query($owner:String!,$repo:String!,$n:Int!){
    repository(owner:$owner,name:$repo){ pullRequest(number:$n){
      reviewThreads(first:100){ nodes { id isResolved comments(first:1){ nodes { author{login} path body } } } } } } }' \
  -f owner=<owner> -f repo=<repo> -F n=<n>
```

## Exit codes and gotchas

- **`gh pr checks` exits 8 when checks are pending and 1 when some failed.** Both are normal, and stdout still holds valid JSON. The watcher tolerates `{0, 1, 8}`; any shell wrapper of your own must do the same or it will treat a red PR as a tool error.
- **`gh api` rejects `-R/--repo` on some versions.** Put the repo in the endpoint path instead.
- **`gh pr checks` needs an explicit PR argument when `-R` is used.** After resolving `--pr auto`, pass the concrete number.
- **`gh api --paginate` output shape varies by version.** The watcher pages manually with `per_page`/`page` and stops on a short page.
- **Runs for superseded SHAs show up as `cancelled`.** Always compare `head_sha` against the PR's current head before treating a cancellation as a failure.
- **Rate limits.** The watcher makes roughly five REST calls per poll; the 60-second default keeps a long watch well inside the secondary limits. Do not lower `--poll-seconds` below 30.

## Watcher state files

Both live in `.git/babysit-pr/` — inside the git directory, so they can never be committed:

| File | Holds |
|---|---|
| `<repo>-pr<n>.state.json` | Seen comment/review IDs, retry count per SHA, start time |
| `<repo>-pr<n>.snapshot.json` | The most recent full snapshot |

Deleting the state file replays every currently-open review item once. That is the intended way to re-surface feedback you want another look at, and it is why a `/loop`-driven watch survives context compaction — the memory is on disk, not in the conversation.

Outside a git worktree both fall back to the system temp directory. Override with `--state-file` / `--snapshot-file`.
