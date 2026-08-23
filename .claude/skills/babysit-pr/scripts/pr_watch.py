#!/usr/bin/env python3
"""Snapshot and watch a GitHub PR's review, CI, and mergeability state.

Three modes:

  --once          Emit one JSON snapshot on stdout and exit.
  --watch         Emit ONE LINE per state change; exit on a terminal state.
                  Designed for the Monitor tool: one line == one notification.
  --retry-failed  Rerun failed jobs for the current SHA, respecting both the
                  flaky-retry budget and this repo's never-retry policy.

The full snapshot is always written to a sidecar JSON file so `--watch` can stay
one line wide while the reader still has the complete state available to Read.

Requires the `gh` CLI, authenticated. No third-party Python packages.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Iterable, Sequence
from urllib.parse import urlparse

# --------------------------------------------------------------------------
# Policy constants
# --------------------------------------------------------------------------

# GitHub reports a run as failed under any of these conclusions. `cancelled` is
# included because a cancelled run leaves the check red and needs a decision;
# `skipped`/`neutral` are not, because they do not block a merge.
FAILED_RUN_CONCLUSIONS = frozenset({"failure", "timed_out", "cancelled", "startup_failure", "stale"})

# `gh pr checks` reports pending work under `bucket: pending`, but older gh
# versions only populate `state`, so both are checked.
PENDING_CHECK_STATES = frozenset({"PENDING", "QUEUED", "IN_PROGRESS", "WAITING", "REQUESTED", "EXPECTED"})

# Review authors whose feedback is surfaced automatically. A drive-by comment
# from an unaffiliated account is not auto-surfaced, because acting on it would
# let any GitHub user steer an autonomous agent's edits.
TRUSTED_AUTHOR_ASSOCIATIONS = frozenset({"OWNER", "MEMBER", "COLLABORATOR"})

# Review bots whose output is real code review rather than status noise.
TRUSTED_REVIEW_BOTS = frozenset(
    {
        "claude[bot]",
        "claude-code[bot]",
        "chatgpt-codex-connector[bot]",
        "copilot-pull-request-reviewer[bot]",
    }
)

# A merge is blocked while the branch is behind, dirty, or conflicted. These are
# surfaced separately from CI because the fix is a rebase, not a code change.
MERGE_BLOCKING_STATES = frozenset({"DIRTY", "BLOCKED", "BEHIND", "UNSTABLE", "DRAFT"})
MERGE_BLOCKING_REVIEW_DECISIONS = frozenset({"CHANGES_REQUESTED", "REVIEW_REQUIRED"})

# THE LOAD-BEARING RULE for this repo. docs/DbtBestPractices.md §21 forbids
# automatic retries of the parity and determinism gates: "A retry converts a real
# non-determinism finding into a coin flip -- the exact defect §11.3 exists to
# catch." A flake in one of these gates is a product defect until proven
# otherwise, so the watcher refuses to recommend a rerun and asks for a human.
NEVER_RETRY_CHECK_PATTERNS = ("parity", "determinism", "comparator-sensitivity", "comparator_sensitivity")

# Three retries is the point where a genuinely transient failure has almost
# always cleared; beyond it the failure is persistent and rerunning only burns
# CI minutes while hiding the signal.
DEFAULT_MAX_FLAKY_RETRIES = 3

# One minute keeps review feedback fresh without approaching GitHub's REST
# secondary rate limits (this script makes ~5 calls per poll).
DEFAULT_POLL_SECONDS = 60

# `gh pr checks` exits 8 when checks are pending and 1 when some failed. Both are
# normal states for this tool, not errors, and stdout still holds valid JSON.
CHECKS_TOLERATED_EXIT_CODES = frozenset({0, 1, 8})


class GhError(RuntimeError):
    """A `gh` invocation failed in a way the caller cannot recover from."""


# --------------------------------------------------------------------------
# gh plumbing
# --------------------------------------------------------------------------


def _run_gh(args: Sequence[str], repo: str | None = None, ok_codes: Iterable[int] = (0,)) -> str:
    """Run `gh` and return stdout, tolerating the exit codes in `ok_codes`."""
    cmd = ["gh"]
    # `gh api` takes the repo inside the endpoint path, and rejects -R on some
    # versions, so the flag is only added for the porcelain subcommands.
    if repo and args and args[0] != "api":
        cmd.extend(["-R", repo])
    cmd.extend(args)
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    except FileNotFoundError as err:
        raise GhError("`gh` is not installed or not on PATH. Install the GitHub CLI and run `gh auth login`.") from err

    if proc.returncode not in set(ok_codes):
        stderr = (proc.stderr or "").strip()
        if "auth login" in stderr or "authentication" in stderr.lower():
            raise GhError(f"gh is not authenticated: {stderr}\nRun `gh auth login`.")
        if "rate limit" in stderr.lower():
            raise GhError(f"GitHub rate limit hit: {stderr}\nWait for the reset window before resuming.")
        raise GhError(f"`{' '.join(cmd)}` exited {proc.returncode}: {stderr or (proc.stdout or '').strip()}")
    return proc.stdout


def _gh_json(args: Sequence[str], repo: str | None = None, ok_codes: Iterable[int] = (0,)) -> Any:
    raw = _run_gh(args, repo=repo, ok_codes=ok_codes).strip()
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError as err:
        raise GhError(f"`gh {' '.join(args)}` did not return JSON. Got: {raw[:200]}") from err


def _gh_api_paged(endpoint: str, per_page: int = 100) -> list[dict[str, Any]]:
    """Page through a list endpoint manually.

    `gh api --paginate` emits concatenated JSON documents whose exact shape has
    changed across gh versions; explicit paging is stable everywhere.
    """
    items: list[dict[str, Any]] = []
    page = 1
    while True:
        sep = "&" if "?" in endpoint else "?"
        data = _gh_json(["api", f"{endpoint}{sep}per_page={per_page}&page={page}"])
        if not isinstance(data, list) or not data:
            break
        items.extend(entry for entry in data if isinstance(entry, dict))
        if len(data) < per_page:
            break
        page += 1
        if page > 20:  # 2000 items; a PR past this is pathological, not a paging bug.
            break
    return items


# --------------------------------------------------------------------------
# PR resolution and state
# --------------------------------------------------------------------------


def _parse_pr_spec(spec: str) -> str | None:
    """Return the argument to pass to `gh pr view`, or None to infer from branch."""
    if spec == "auto":
        return None
    if re.fullmatch(r"\d+", spec):
        return spec
    parsed = urlparse(spec)
    if parsed.scheme and parsed.netloc and "/pull/" in parsed.path:
        return spec
    raise ValueError(f"--pr must be 'auto', a PR number, or a PR URL; got {spec!r}")


def _repo_from_url(pr_url: str) -> str | None:
    parts = [p for p in urlparse(pr_url).path.split("/") if p]
    if len(parts) >= 4 and parts[2] == "pull":
        return f"{parts[0]}/{parts[1]}"
    return None


def resolve_pr(spec: str, repo_override: str | None = None) -> dict[str, Any]:
    target = _parse_pr_spec(spec)
    cmd = ["pr", "view"]
    if target is not None:
        cmd.append(target)
    cmd.extend(
        [
            "--json",
            "number,url,state,mergedAt,closedAt,headRefName,headRefOid,"
            "mergeable,mergeStateStatus,reviewDecision,isDraft,title",
        ]
    )
    try:
        data = _gh_json(cmd, repo=repo_override)
    except GhError as err:
        if target is None and "no pull requests found" in str(err).lower():
            raise GhError(
                "No PR found for the current branch. Push the branch and open a PR, "
                "or pass --pr <number|url>."
            ) from err
        raise

    if not isinstance(data, dict):
        raise GhError("`gh pr view` returned an unexpected payload.")

    url = str(data.get("url") or "")
    repo = repo_override or _repo_from_url(url)
    if not repo:
        raise GhError("Could not determine OWNER/REPO for the PR. Pass --repo OWNER/REPO.")

    state = str(data.get("state") or "")
    return {
        "number": int(data["number"]),
        "title": str(data.get("title") or ""),
        "url": url,
        "repo": repo,
        "head_sha": str(data.get("headRefOid") or ""),
        "head_branch": str(data.get("headRefName") or ""),
        "state": state,
        "is_draft": bool(data.get("isDraft")),
        "merged": bool(data.get("mergedAt")),
        "closed": bool(data.get("closedAt")) or state.upper() == "CLOSED",
        "mergeable": str(data.get("mergeable") or ""),
        "merge_state_status": str(data.get("mergeStateStatus") or ""),
        "review_decision": str(data.get("reviewDecision") or ""),
    }


def _state_dir() -> Path:
    """Prefer .git/ so state is repo-local and can never be committed."""
    try:
        git_dir = subprocess.run(
            ["git", "rev-parse", "--absolute-git-dir"],
            capture_output=True,
            text=True,
            check=False,
        )
        if git_dir.returncode == 0 and git_dir.stdout.strip():
            return Path(git_dir.stdout.strip()) / "babysit-pr"
    except FileNotFoundError:
        pass
    return Path(tempfile.gettempdir()) / "babysit-pr"


def _default_paths(pr: dict[str, Any]) -> tuple[Path, Path]:
    slug = pr["repo"].replace("/", "-")
    base = _state_dir()
    return (
        base / f"{slug}-pr{pr['number']}.state.json",
        base / f"{slug}-pr{pr['number']}.snapshot.json",
    )


def _empty_state() -> dict[str, Any]:
    return {
        "pr": {},
        "started_at": None,
        "last_seen_head_sha": None,
        "retries_by_sha": {},
        "seen_issue_comment_ids": [],
        "seen_review_comment_ids": [],
        "seen_review_ids": [],
        "last_snapshot_at": None,
    }


def load_state(path: Path) -> tuple[dict[str, Any], bool]:
    """Return (state, is_fresh). A corrupt state file is reset, not fatal."""
    if not path.exists():
        return _empty_state(), True
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        # Losing "seen" markers replays existing review comments once, which is
        # noisy but safe. Refusing to run would be worse.
        sys.stderr.write(f"warning: resetting unreadable state file {path}\n")
        return _empty_state(), True
    if not isinstance(data, dict):
        sys.stderr.write(f"warning: resetting malformed state file {path}\n")
        return _empty_state(), True
    merged = _empty_state()
    merged.update(data)
    return merged, False


def save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(state, indent=2, sort_keys=True) + "\n")
        os.replace(tmp, path)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


# --------------------------------------------------------------------------
# Checks, runs, jobs
# --------------------------------------------------------------------------


def get_checks(pr: dict[str, Any]) -> list[dict[str, Any]]:
    data = _gh_json(
        ["pr", "checks", str(pr["number"]), "--json", "name,state,bucket,link,workflow,completedAt"],
        repo=pr["repo"],
        ok_codes=CHECKS_TOLERATED_EXIT_CODES,
    )
    if data is None:
        return []  # No checks configured on this repo yet.
    if not isinstance(data, list):
        raise GhError("`gh pr checks` returned an unexpected payload.")
    return data


def _is_pending(check: dict[str, Any]) -> bool:
    return str(check.get("bucket") or "").lower() == "pending" or str(check.get("state") or "").upper() in PENDING_CHECK_STATES


def never_retry_reason(names: Iterable[str]) -> str | None:
    """Return the first check name that must never be auto-rerun, if any."""
    for name in names:
        lowered = str(name or "").lower()
        for pattern in NEVER_RETRY_CHECK_PATTERNS:
            if pattern in lowered:
                return str(name)
    return None


def summarize_checks(checks: list[dict[str, Any]]) -> dict[str, Any]:
    pending = [c for c in checks if _is_pending(c)]
    failed = [c for c in checks if str(c.get("bucket") or "").lower() == "fail"]
    passed = [c for c in checks if str(c.get("bucket") or "").lower() == "pass"]
    failed_names = [str(c.get("name") or "") for c in failed]
    return {
        "pending_count": len(pending),
        "failed_count": len(failed),
        "passed_count": len(passed),
        "total_count": len(checks),
        "all_terminal": not pending,
        "failed_names": failed_names,
        "pending_names": [str(c.get("name") or "") for c in pending],
        "failed_links": [str(c.get("link") or "") for c in failed],
        "never_retry_hit": never_retry_reason(failed_names),
    }


def get_workflow_runs(repo: str, head_sha: str) -> list[dict[str, Any]]:
    if not head_sha:
        return []
    data = _gh_json(["api", f"repos/{repo}/actions/runs?head_sha={head_sha}&per_page=100"])
    if not isinstance(data, dict):
        return []
    runs = data.get("workflow_runs")
    return [r for r in runs if isinstance(r, dict)] if isinstance(runs, list) else []


def failed_runs(runs: list[dict[str, Any]], head_sha: str) -> list[dict[str, Any]]:
    out = []
    for run in runs:
        if str(run.get("head_sha") or "") != head_sha:
            continue
        if str(run.get("status") or "") != "completed":
            continue
        if str(run.get("conclusion") or "") not in FAILED_RUN_CONCLUSIONS:
            continue
        out.append(
            {
                "run_id": run.get("id"),
                "name": run.get("name"),
                "conclusion": run.get("conclusion"),
                "url": run.get("html_url"),
            }
        )
    return out


def failed_jobs(repo: str, runs: list[dict[str, Any]], head_sha: str) -> list[dict[str, Any]]:
    """Failed jobs, including jobs inside runs that are still in progress.

    A job that has already failed is diagnosable immediately; waiting for the
    whole workflow run to complete wastes minutes on every failure.
    """
    out: list[dict[str, Any]] = []
    for run in runs:
        if str(run.get("head_sha") or "") != head_sha:
            continue
        run_id = run.get("id")
        if run_id in (None, ""):
            continue
        data = _gh_json(["api", f"repos/{repo}/actions/runs/{run_id}/jobs?per_page=100"])
        jobs = data.get("jobs") if isinstance(data, dict) else None
        if not isinstance(jobs, list):
            continue
        for job in jobs:
            if not isinstance(job, dict):
                continue
            if str(job.get("status") or "") != "completed":
                continue
            if str(job.get("conclusion") or "") not in FAILED_RUN_CONCLUSIONS:
                continue
            job_id = job.get("id")
            out.append(
                {
                    "run_id": run_id,
                    "job_id": job_id,
                    "name": job.get("name"),
                    "workflow": run.get("name"),
                    "conclusion": job.get("conclusion"),
                    "url": job.get("html_url"),
                    "logs_endpoint": f"repos/{repo}/actions/jobs/{job_id}/logs" if job_id else None,
                }
            )
    return out


# --------------------------------------------------------------------------
# Review feedback
# --------------------------------------------------------------------------


def _authenticated_login() -> str:
    try:
        data = _gh_json(["api", "user"])
    except GhError:
        return ""
    return str(data.get("login") or "") if isinstance(data, dict) else ""


def _login(obj: Any) -> str:
    return str(obj.get("login") or "") if isinstance(obj, dict) else ""


def _is_trusted(item: dict[str, Any], author: str, me: str) -> bool:
    if author and author == me:
        return True  # Our own prior replies; surfaced so they are not repeated.
    if author in TRUSTED_REVIEW_BOTS:
        return True
    if author.endswith("[bot]"):
        return False  # Every other bot is status noise.
    return str(item.get("author_association") or "").upper() in TRUSTED_AUTHOR_ASSOCIATIONS


def _truncate(body: str, limit: int = 400) -> str:
    body = " ".join(str(body or "").split())
    return body if len(body) <= limit else body[: limit - 1] + "…"


def fetch_new_review_items(
    pr: dict[str, Any], state: dict[str, Any], fresh: bool, me: str
) -> list[dict[str, Any]]:
    """Published, trusted review feedback not yet marked as seen.

    On a fresh state file this returns already-open feedback too, so a PR that
    has been sitting with unaddressed comments is not silently skipped.
    """
    repo, number = pr["repo"], pr["number"]
    reviews = _gh_api_paged(f"repos/{repo}/pulls/{number}/reviews")
    # PENDING reviews are drafts only their author can see. Their inline
    # comments must stay unseen so they surface when the review is submitted.
    review_state_by_id = {r.get("id"): str(r.get("state") or "").upper() for r in reviews}

    seen_issue = set(state.get("seen_issue_comment_ids") or [])
    seen_comment = set(state.get("seen_review_comment_ids") or [])
    seen_review = set(state.get("seen_review_ids") or [])
    new_items: list[dict[str, Any]] = []

    for item in _gh_api_paged(f"repos/{repo}/issues/{number}/comments"):
        ident = item.get("id")
        if ident in seen_issue:
            continue
        seen_issue.add(ident)
        author = _login(item.get("user"))
        if not _is_trusted(item, author, me):
            continue
        new_items.append(
            {
                "kind": "issue_comment",
                "id": ident,
                "author": author,
                "self_authored": author == me,
                "created_at": item.get("created_at"),
                "url": item.get("html_url"),
                "body": _truncate(item.get("body", "")),
            }
        )

    for item in _gh_api_paged(f"repos/{repo}/pulls/{number}/comments"):
        ident = item.get("id")
        if ident in seen_comment:
            continue
        parent_state = review_state_by_id.get(item.get("pull_request_review_id"))
        if parent_state == "PENDING":
            continue  # Deliberately NOT marked seen.
        seen_comment.add(ident)
        author = _login(item.get("user"))
        if not _is_trusted(item, author, me):
            continue
        new_items.append(
            {
                "kind": "review_comment",
                "id": ident,
                "author": author,
                "self_authored": author == me,
                "created_at": item.get("created_at"),
                "url": item.get("html_url"),
                "path": item.get("path"),
                "line": item.get("line") or item.get("original_line"),
                "body": _truncate(item.get("body", "")),
            }
        )

    for item in reviews:
        ident = item.get("id")
        if ident in seen_review:
            continue
        review_state = str(item.get("state") or "").upper()
        if review_state == "PENDING":
            continue
        seen_review.add(ident)
        author = _login(item.get("user"))
        if not _is_trusted(item, author, me):
            continue
        # An APPROVED review with no body carries no action.
        if review_state == "APPROVED" and not str(item.get("body") or "").strip():
            continue
        new_items.append(
            {
                "kind": "review",
                "id": ident,
                "author": author,
                "self_authored": author == me,
                "state": review_state,
                "created_at": item.get("submitted_at"),
                "url": item.get("html_url"),
                "body": _truncate(item.get("body", "")),
            }
        )

    new_items.sort(key=lambda i: (str(i.get("created_at") or ""), str(i.get("kind")), str(i.get("id"))))
    state["seen_issue_comment_ids"] = sorted(seen_issue, key=str)
    state["seen_review_comment_ids"] = sorted(seen_comment, key=str)
    state["seen_review_ids"] = sorted(seen_review, key=str)
    state["fresh_state_replay"] = fresh
    return new_items


# --------------------------------------------------------------------------
# Action recommendation
# --------------------------------------------------------------------------


def _retries_used(state: dict[str, Any], sha: str) -> int:
    try:
        return int((state.get("retries_by_sha") or {}).get(sha, 0))
    except (TypeError, ValueError):
        return 0


def ready_to_merge(pr: dict[str, Any], checks: dict[str, Any], items: list[dict[str, Any]]) -> bool:
    if pr["closed"] or pr["merged"] or pr["is_draft"]:
        return False
    if not checks["all_terminal"] or checks["failed_count"] or checks["pending_count"]:
        return False
    if [i for i in items if not i.get("self_authored")]:
        return False
    if pr["mergeable"] != "MERGEABLE":
        return False
    if pr["merge_state_status"] in MERGE_BLOCKING_STATES:
        return False
    return pr["review_decision"] not in MERGE_BLOCKING_REVIEW_DECISIONS


def recommend_actions(
    pr: dict[str, Any],
    checks: dict[str, Any],
    runs: list[dict[str, Any]],
    jobs: list[dict[str, Any]],
    items: list[dict[str, Any]],
    retries: int,
    max_retries: int,
) -> list[str]:
    actions: list[str] = []
    if pr["closed"] or pr["merged"]:
        if items:
            actions.append("process_review_comment")
        actions.append("stop_pr_closed")
        return actions

    if items:
        actions.append("process_review_comment")

    if pr["merge_state_status"] == "DIRTY":
        actions.append("resolve_merge_conflict")

    if checks["failed_count"] or jobs:
        actions.append("diagnose_ci_failure")
        if checks["never_retry_hit"]:
            # Repo policy: parity/determinism flakes are defects, not noise.
            actions.append("stop_never_retry_gate")
        elif checks["all_terminal"] and retries >= max_retries:
            actions.append("stop_exhausted_retries")
        elif checks["all_terminal"] and runs and retries < max_retries:
            actions.append("retry_failed_checks")

    if not actions and ready_to_merge(pr, checks, items):
        actions.append("ready_to_merge")
    if not actions:
        actions.append("idle")
    return actions


# --------------------------------------------------------------------------
# Snapshot
# --------------------------------------------------------------------------


def collect_snapshot(args: argparse.Namespace) -> tuple[dict[str, Any], Path, Path]:
    pr = resolve_pr(args.pr, repo_override=args.repo)
    default_state, default_snapshot = _default_paths(pr)
    state_path = Path(args.state_file) if args.state_file else default_state
    snapshot_path = Path(args.snapshot_file) if args.snapshot_file else default_snapshot

    state, fresh = load_state(state_path)
    state.setdefault("started_at", None)
    if not state.get("started_at"):
        state["started_at"] = int(time.time())

    items = fetch_new_review_items(pr, state, fresh, _authenticated_login())
    checks_raw = get_checks(pr)
    checks = summarize_checks(checks_raw)
    runs = get_workflow_runs(pr["repo"], pr["head_sha"])
    f_runs = failed_runs(runs, pr["head_sha"])
    f_jobs = failed_jobs(pr["repo"], runs, pr["head_sha"]) if (checks["failed_count"] or checks["pending_count"]) else []

    retries = _retries_used(state, pr["head_sha"])
    actions = recommend_actions(pr, checks, f_runs, f_jobs, items, retries, args.max_flaky_retries)

    state["pr"] = {"repo": pr["repo"], "number": pr["number"]}
    state["last_seen_head_sha"] = pr["head_sha"]
    state["last_snapshot_at"] = int(time.time())
    save_state(state_path, state)

    snapshot = {
        "pr": pr,
        "checks": checks,
        "failed_runs": f_runs,
        "failed_jobs": f_jobs,
        "new_review_items": items,
        "actions": actions,
        "retry_state": {
            "used_for_current_sha": retries,
            "max_flaky_retries": args.max_flaky_retries,
            "never_retry_hit": checks["never_retry_hit"],
        },
        "state_file": str(state_path),
        "snapshot_file": str(snapshot_path),
        "captured_at": int(time.time()),
    }
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    snapshot_path.write_text(json.dumps(snapshot, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return snapshot, state_path, snapshot_path


def retry_failed_now(args: argparse.Namespace) -> dict[str, Any]:
    snapshot, state_path, _ = collect_snapshot(args)
    pr, checks = snapshot["pr"], snapshot["checks"]
    result: dict[str, Any] = {
        "rerun_attempted": False,
        "rerun_run_ids": [],
        "reason": None,
        "snapshot": snapshot,
    }

    if pr["closed"] or pr["merged"]:
        result["reason"] = "pr_closed"
    elif checks["never_retry_hit"]:
        result["reason"] = (
            f"refused: {checks['never_retry_hit']} is a parity/determinism gate. "
            "Repo policy (DbtBestPractices.md §21) forbids auto-retry; treat it as a defect."
        )
    elif not checks["failed_count"]:
        result["reason"] = "no_failed_checks"
    elif not snapshot["failed_runs"]:
        result["reason"] = "no_rerunnable_workflow_runs"
    elif not checks["all_terminal"]:
        result["reason"] = "checks_still_pending"
    elif snapshot["retry_state"]["used_for_current_sha"] >= args.max_flaky_retries:
        result["reason"] = "retry_budget_exhausted"
    else:
        for run in snapshot["failed_runs"]:
            run_id = run.get("run_id")
            if run_id in (None, ""):
                continue
            _run_gh(["run", "rerun", str(run_id), "--failed"], repo=pr["repo"])
            result["rerun_run_ids"].append(run_id)
        if result["rerun_run_ids"]:
            state, _ = load_state(state_path)
            retries = state.setdefault("retries_by_sha", {})
            retries[pr["head_sha"]] = _retries_used(state, pr["head_sha"]) + 1
            save_state(state_path, state)
            result["rerun_attempted"] = True
            result["reason"] = "rerun_triggered"
        else:
            result["reason"] = "failed_runs_missing_ids"
    return result


# --------------------------------------------------------------------------
# Watch mode -- one line per change
# --------------------------------------------------------------------------


def _change_key(snapshot: dict[str, Any]) -> tuple[Any, ...]:
    pr, checks = snapshot["pr"], snapshot["checks"]
    return (
        pr["head_sha"],
        pr["state"],
        pr["mergeable"],
        pr["merge_state_status"],
        pr["review_decision"],
        checks["passed_count"],
        checks["failed_count"],
        checks["pending_count"],
        tuple(str(i.get("id")) for i in snapshot["new_review_items"]),
        tuple(snapshot["actions"]),
    )


def _emit(line: str) -> None:
    sys.stdout.write(line.rstrip() + "\n")
    sys.stdout.flush()


def _summary_lines(snapshot: dict[str, Any], was_green: bool) -> list[str]:
    pr, checks = snapshot["pr"], snapshot["checks"]
    tag = "PR#{n} {sha}".format(n=pr["number"], sha=(pr["head_sha"] or "")[:7])
    lines: list[str] = []

    if pr["merged"]:
        return [f"MERGED {tag} — babysitting complete."]
    if pr["closed"]:
        return [f"CLOSED {tag} — babysitting complete."]

    green = bool(checks["all_terminal"] and not checks["failed_count"] and checks["total_count"])
    if green and not was_green:
        # One-time celebration on the transition, not on every green poll.
        lines.append(f"🚀 CI is all green! {checks['passed_count']}/{checks['total_count']} passed on {tag}.")
    if green:
        state_word = "CI GREEN"
    elif checks["failed_count"]:
        state_word = "CI FAILING"
    elif checks["pending_count"]:
        state_word = "CI RUNNING"
    else:
        state_word = "NO CHECKS"

    lines.append(
        "{s} {tag} — {p} passed / {f} failed / {q} pending · mergeable={m} · review={r} · actions={a}".format(
            s=state_word,
            tag=tag,
            p=checks["passed_count"],
            f=checks["failed_count"],
            q=checks["pending_count"],
            m=pr["merge_state_status"] or pr["mergeable"] or "?",
            r=pr["review_decision"] or "none",
            a=",".join(snapshot["actions"]),
        )
    )
    if checks["failed_names"]:
        lines.append("  failing: " + ", ".join(checks["failed_names"][:8]))
    if checks["never_retry_hit"]:
        lines.append(f"  NEVER-RETRY GATE FAILED: {checks['never_retry_hit']} — treat as a defect, do not rerun.")
    for item in snapshot["new_review_items"]:
        if item.get("self_authored"):
            continue
        where = f" {item['path']}:{item['line']}" if item.get("path") else ""
        lines.append(f"  REVIEW [{item['kind']}] @{item['author']}{where}: {item['body'][:200]}")
    lines.append(f"  snapshot: {snapshot['snapshot_file']}")
    return lines


def run_watch(args: argparse.Namespace) -> int:
    last_key: tuple[Any, ...] | None = None
    consecutive_errors = 0
    was_green = False
    while True:
        try:
            snapshot, _, _ = collect_snapshot(args)
            consecutive_errors = 0
        except GhError as err:
            # A transient gh/network failure must not kill a long watch, but a
            # persistent one must surface rather than loop silently forever.
            consecutive_errors += 1
            if consecutive_errors >= 3:
                _emit(f"WATCH ERROR (3 consecutive): {err}")
                return 1
            time.sleep(args.poll_seconds)
            continue

        key = _change_key(snapshot)
        if key != last_key:
            for line in _summary_lines(snapshot, was_green):
                _emit(line)
        last_key = key
        checks = snapshot["checks"]
        was_green = bool(checks["all_terminal"] and not checks["failed_count"] and checks["total_count"])

        actions = set(snapshot["actions"])
        if "stop_pr_closed" in actions:
            return 0
        if "stop_never_retry_gate" in actions:
            _emit("STOP: a never-retry gate failed. Human decision required (quarantine or fix).")
            return 0
        if "stop_exhausted_retries" in actions:
            _emit("STOP: flaky-retry budget exhausted for this SHA. Human decision required.")
            return 0
        time.sleep(args.poll_seconds)


# --------------------------------------------------------------------------


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--pr", default="auto", help="'auto' (infer from branch), a PR number, or a PR URL")
    parser.add_argument("--repo", help="OWNER/REPO override")
    parser.add_argument("--state-file", help="Path to the seen/retry state JSON")
    parser.add_argument("--snapshot-file", help="Path the full snapshot JSON is written to")
    parser.add_argument("--poll-seconds", type=int, default=DEFAULT_POLL_SECONDS, help="Watch poll interval")
    parser.add_argument("--max-flaky-retries", type=int, default=DEFAULT_MAX_FLAKY_RETRIES)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--once", action="store_true", help="Emit one JSON snapshot and exit")
    mode.add_argument("--watch", action="store_true", help="Emit one line per change until terminal")
    mode.add_argument("--retry-failed", action="store_true", help="Rerun failed jobs for the current SHA")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if args.retry_failed:
            print(json.dumps(retry_failed_now(args), indent=2, sort_keys=True))
            return 0
        if args.watch:
            return run_watch(args)
        snapshot, _, _ = collect_snapshot(args)
        print(json.dumps(snapshot, indent=2, sort_keys=True))
        return 0
    except (GhError, ValueError) as err:
        sys.stderr.write(f"pr_watch.py: {err}\n")
        return 1
    except KeyboardInterrupt:
        sys.stderr.write("pr_watch.py: interrupted\n")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
