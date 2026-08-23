#!/usr/bin/env python3
"""Verify that a merge commit actually landed on the base branch and is green.

Two modes:

  --once    Emit one JSON snapshot on stdout and exit.
  --watch   Emit ONE LINE per state change; exit once the outcome is decided.
            Designed for the Monitor tool: one line == one notification.

This answers the question `pr_watch.py` cannot: the PR merged, but did the
merged code survive contact with the base branch? A squash or merge commit is a
SHA that never ran CI as part of the PR, so it gets its own verification pass.

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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

# --------------------------------------------------------------------------
# Policy constants
# --------------------------------------------------------------------------

# GitHub reports a run as failed under any of these conclusions. `cancelled` is
# included because a cancelled run leaves the branch red and needs a decision;
# `skipped`/`neutral` are not, because they are a deliberate no-op.
# Matches pr_watch.py so a check means the same thing before and after merge.
FAILED_RUN_CONCLUSIONS = frozenset({"failure", "timed_out", "cancelled", "startup_failure", "stale"})

# THE LOAD-BEARING RULE for this repo, and it does not stop at the merge button.
# docs/DbtBestPractices.md §21: "A flake in a determinism or parity gate is a
# product defect until proven otherwise." One of these failing on main is a
# defect that already reached the base branch, which makes it more urgent than
# the same failure on a PR, never less.
NEVER_RETRY_CHECK_PATTERNS = ("parity", "determinism", "comparator-sensitivity", "comparator_sensitivity")

# Only these events verify the pushed commit itself. A `schedule` run shares the
# base branch's head SHA, so this repo's nightly job (§15) would otherwise be
# counted as -- and could fail -- the verification of whatever merged last.
# `pull_request` runs are excluded for the same reason: they verified the PR
# head, which under a squash merge is a different tree entirely.
VERIFYING_RUN_EVENTS = frozenset({"push", "merge_group", "workflow_dispatch"})

# Post-merge CI is what the ship loop is blocked on, so it polls twice as fast
# as the PR watcher. Three REST calls per poll at 30s stays far inside GitHub's
# secondary rate limits. Do not go below 30.
DEFAULT_POLL_SECONDS = 30

# A full main-branch run in this repo seeds, builds, runs the parity harness,
# then runs two determinism builds serially (§15). An hour is generous enough
# that hitting this timeout means a run is stuck or a required job never
# started -- not that CI is merely slow.
DEFAULT_TIMEOUT_SECONDS = 3600

# GitHub registers a push-triggered run within seconds of the push. Two minutes
# covers a queued or slow-to-appear run before the script concludes the branch
# genuinely has no push CI and local verification is the only option.
RUN_REGISTRATION_GRACE_SECONDS = 120


class GhError(RuntimeError):
    """A `gh` invocation failed in a way the caller cannot recover from."""


# --------------------------------------------------------------------------
# gh and git plumbing
# --------------------------------------------------------------------------


def _run_gh(args: Sequence[str], ok_codes: Iterable[int] = (0,)) -> str:
    """Run `gh` and return stdout, tolerating the exit codes in `ok_codes`."""
    cmd = ["gh", *args]
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


def _gh_json(args: Sequence[str], ok_codes: Iterable[int] = (0,)) -> Any:
    raw = _run_gh(args, ok_codes=ok_codes).strip()
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError as err:
        raise GhError(f"`gh {' '.join(args)}` did not return JSON. Got: {raw[:200]}") from err


def _git(args: Sequence[str]) -> str | None:
    """Run `git` and return stripped stdout, or None if it failed.

    Every caller here treats git as optional enrichment: the snapshot is built
    from the GitHub API so it stays correct even when the local clone is stale,
    missing, or checked out somewhere unrelated.
    """
    try:
        proc = subprocess.run(["git", *args], capture_output=True, text=True, check=False)
    except FileNotFoundError:
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.strip()


# --------------------------------------------------------------------------
# Resolution
# --------------------------------------------------------------------------


def resolve_repo(override: str | None) -> str:
    if override:
        if not re.fullmatch(r"[^/\s]+/[^/\s]+", override):
            raise ValueError(f"--repo must be OWNER/REPO; got {override!r}")
        return override
    data = _gh_json(["repo", "view", "--json", "nameWithOwner"])
    if not isinstance(data, dict) or not data.get("nameWithOwner"):
        raise GhError("Could not determine OWNER/REPO from the current directory. Pass --repo OWNER/REPO.")
    return str(data["nameWithOwner"])


def resolve_base_head(repo: str, branch: str) -> str:
    """The current tip of the base branch on the remote."""
    try:
        data = _gh_json(["api", f"repos/{repo}/commits/{branch}"])
    except GhError as err:
        raise GhError(f"Base branch {branch!r} not found in {repo}. Pass --branch <name>. ({err})") from err
    if not isinstance(data, dict) or not data.get("sha"):
        raise GhError(f"Base branch {branch!r} in {repo} returned no commit.")
    return str(data["sha"])


def _parse_iso8601(value: str) -> int | None:
    """GitHub timestamps are `2026-08-23T05:11:09Z`. Return epoch seconds."""
    if not value:
        return None
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return int(parsed.timestamp())


def resolve_commit(repo: str, spec: str, branch: str, base_head: str) -> dict[str, Any]:
    """Describe the commit under verification, and whether it is on the base branch."""
    sha = base_head if spec == "auto" else spec
    if spec != "auto" and not re.fullmatch(r"[0-9a-fA-F]{7,40}", sha):
        raise ValueError(f"--commit must be 'auto' or a git SHA; got {spec!r}")

    data = _gh_json(["api", f"repos/{repo}/commits/{sha}"])
    if not isinstance(data, dict) or not data.get("sha"):
        raise GhError(f"Commit {sha!r} not found in {repo}. Push it, or check the SHA.")

    full_sha = str(data["sha"])
    message = str((data.get("commit") or {}).get("message") or "")
    committed_at = _parse_iso8601(str(((data.get("commit") or {}).get("committer") or {}).get("date") or ""))

    if full_sha == base_head:
        on_base = True
    else:
        # `compare/base...head` reports "behind" or "identical" when head is an
        # ancestor of base. This is an API-only check on purpose: a squash merge
        # produces a SHA the local clone has never fetched.
        compare = _gh_json(["api", f"repos/{repo}/compare/{branch}...{full_sha}"])
        status = str((compare or {}).get("status") or "") if isinstance(compare, dict) else ""
        on_base = status in {"identical", "behind"}

    age = None
    if committed_at is not None:
        age = max(0, int(time.time()) - committed_at)

    return {
        "sha": full_sha,
        "subject": message.splitlines()[0] if message else "",
        "on_base": on_base,
        "is_base_head": full_sha == base_head,
        "committed_at": committed_at,
        "age_seconds": age,
        "url": str(data.get("html_url") or ""),
    }


def local_state(branch: str, base_head: str) -> dict[str, Any]:
    """What the working copy looks like, for the 'is my checkout stale' question."""
    head = _git(["rev-parse", "HEAD"])
    if head is None:
        return {"available": False, "reason": "not a git worktree, or git is unavailable"}
    current = _git(["rev-parse", "--abbrev-ref", "HEAD"])
    porcelain = _git(["status", "--porcelain"])
    return {
        "available": True,
        "branch": current,
        "head_sha": head,
        "dirty": bool(porcelain),
        "on_base_branch": current == branch,
        "up_to_date_with_remote": head == base_head,
    }


# --------------------------------------------------------------------------
# Runs and jobs
# --------------------------------------------------------------------------


def get_runs(repo: str, sha: str) -> tuple[list[dict[str, Any]], bool]:
    """All workflow runs for a SHA, paged. Returns (runs, truncated).

    Paging is not optional here. The endpoint returns newest-first, so on a base
    branch that has been sitting a while the scheduled runs accumulate in front
    of the push runs that actually verified the merge -- reading only page one
    reports "no CI ran" for a commit that CI did run.
    """
    raw: list[Any] = []
    truncated = False
    page = 1
    while True:
        data = _gh_json(["api", f"repos/{repo}/actions/runs?head_sha={sha}&per_page=100&page={page}"])
        batch = data.get("workflow_runs") if isinstance(data, dict) else None
        if not batch:
            break
        raw.extend(batch)
        if len(batch) < 100:
            break
        page += 1
        # 1000 runs for a single SHA is a branch tip buried under scheduled runs,
        # not a merge commit. Stop, and say so rather than reporting a partial
        # picture as complete.
        if page > 10:
            truncated = True
            break

    runs: list[dict[str, Any]] = []
    for run in raw:
        if not isinstance(run, dict):
            continue
        event = str(run.get("event") or "")
        runs.append(
            {
                "run_id": run.get("id"),
                "name": str(run.get("name") or ""),
                "event": event,
                "head_branch": str(run.get("head_branch") or ""),
                "status": str(run.get("status") or ""),
                "conclusion": str(run.get("conclusion") or ""),
                "url": str(run.get("html_url") or ""),
                # Reported rather than filtered out, so an excluded run is
                # visible to the reader instead of silently dropped.
                "verifies_commit": event in VERIFYING_RUN_EVENTS,
            }
        )
    return runs, truncated


def get_failed_jobs(repo: str, runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Failed jobs, with the logs endpoint for each.

    Jobs are collected as soon as a job fails, without waiting for its run to
    finish, because the log of the failing job is what classification needs.
    """
    jobs: list[dict[str, Any]] = []
    for run in runs:
        if not run["verifies_commit"]:
            continue
        failed = run["conclusion"] in FAILED_RUN_CONCLUSIONS
        # An in-progress run can already contain a failed job, and that job's log
        # is what classification needs -- so it is inspected too.
        in_progress = run["status"] != "completed"
        run_id = run["run_id"]
        if not (failed or in_progress) or run_id is None:
            continue
        data = _gh_json(["api", f"repos/{repo}/actions/runs/{run_id}/jobs?per_page=100"])
        raw_jobs = data.get("jobs") if isinstance(data, dict) else None
        for job in raw_jobs or []:
            if not isinstance(job, dict) or job.get("conclusion") not in FAILED_RUN_CONCLUSIONS:
                continue
            job_id = job.get("id")
            jobs.append(
                {
                    "run_id": run_id,
                    "job_id": job_id,
                    "name": str(job.get("name") or ""),
                    "workflow": run["name"],
                    "conclusion": str(job.get("conclusion") or ""),
                    "url": str(job.get("html_url") or ""),
                    "logs_endpoint": f"repos/{repo}/actions/jobs/{job_id}/logs" if job_id else None,
                }
            )
    return jobs


def _never_retry_hit(names: Iterable[str]) -> str | None:
    for name in names:
        lowered = name.lower()
        for pattern in NEVER_RETRY_CHECK_PATTERNS:
            if pattern in lowered:
                return name
    return None


def summarize_runs(runs: list[dict[str, Any]]) -> dict[str, Any]:
    """Counts over the runs that verify this commit; others are reported aside."""
    passed = failed = pending = 0
    failed_names: list[str] = []
    counted = [run for run in runs if run["verifies_commit"]]
    for run in counted:
        if run["status"] != "completed":
            pending += 1
        elif run["conclusion"] in FAILED_RUN_CONCLUSIONS:
            failed += 1
            failed_names.append(run["name"] or f"run {run['run_id']}")
        else:
            passed += 1
    excluded = [run for run in runs if not run["verifies_commit"]]
    return {
        "passed_count": passed,
        "failed_count": failed,
        "pending_count": pending,
        "total_count": len(counted),
        "all_terminal": pending == 0,
        "failed_names": failed_names,
        # Scheduled/PR runs sharing this SHA. Named so a nightly failure is
        # visible without being mistaken for this commit's verification.
        "excluded_count": len(excluded),
        "excluded_failed_names": sorted(
            {
                run["name"] or f"run {run['run_id']}"
                for run in excluded
                if run["conclusion"] in FAILED_RUN_CONCLUSIONS
            }
        ),
    }


# --------------------------------------------------------------------------
# Snapshot
# --------------------------------------------------------------------------


def _snapshot_dir() -> Path:
    """Prefer .git/ so state is repo-local and can never be committed."""
    git_dir = _git(["rev-parse", "--absolute-git-dir"])
    if git_dir:
        return Path(git_dir) / "ship-pr"
    return Path(tempfile.gettempdir()) / "ship-pr"


def decide_actions(commit: dict[str, Any], checks: dict[str, Any], never_retry: str | None) -> list[str]:
    if not commit["on_base"]:
        return ["commit_not_on_base"]
    if never_retry:
        return ["diagnose_main_failure", "stop_never_retry_gate"]
    if checks["failed_count"]:
        return ["diagnose_main_failure", "stop_main_red"]
    if checks["pending_count"]:
        return ["wait_for_main_ci"]
    if checks["total_count"] == 0:
        age = commit["age_seconds"]
        if age is not None and age < RUN_REGISTRATION_GRACE_SECONDS:
            return ["wait_for_main_ci"]
        return ["verify_locally"]
    return ["main_verified"]


def collect_snapshot(args: argparse.Namespace) -> dict[str, Any]:
    repo = resolve_repo(args.repo)
    base_head = resolve_base_head(repo, args.branch)
    commit = resolve_commit(repo, args.commit, args.branch, base_head)
    runs, runs_truncated = get_runs(repo, commit["sha"])
    checks = summarize_runs(runs)
    checks["runs_truncated"] = runs_truncated
    failed_jobs = get_failed_jobs(repo, runs) if checks["failed_count"] or checks["pending_count"] else []
    never_retry = _never_retry_hit([*checks["failed_names"], *(job["name"] for job in failed_jobs)])
    checks["never_retry_hit"] = never_retry

    snapshot = {
        "base": {
            "branch": args.branch,
            "repo": repo,
            "remote_head_sha": base_head,
            # Main moved on: a `cancelled` run for this SHA may be
            # cancel-in-progress by design rather than a real failure.
            "moved_on": not commit["is_base_head"],
        },
        "commit": commit,
        "local": local_state(args.branch, base_head),
        "runs": runs,
        "checks": checks,
        "failed_jobs": failed_jobs,
        "actions": decide_actions(commit, checks, never_retry),
        "captured_at": int(time.time()),
    }

    default_name = "{repo}-{branch}.json".format(
        repo=repo.replace("/", "-"), branch=args.branch.replace("/", "-")
    )
    path = Path(args.snapshot_file) if args.snapshot_file else _snapshot_dir() / default_name
    snapshot["snapshot_file"] = str(path)
    _write_snapshot(path, snapshot)
    return snapshot


def _write_snapshot(path: Path, snapshot: dict[str, Any]) -> None:
    """Persist the snapshot; a write failure degrades to a warning, not a crash.

    The snapshot file is a convenience for the reader. Losing it must not lose
    the verification result, which is already on stdout.
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
        tmp = Path(tmp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(json.dumps(snapshot, indent=2, sort_keys=True) + "\n")
            os.replace(tmp, path)
        except Exception:
            tmp.unlink(missing_ok=True)
            raise
    except OSError as err:
        sys.stderr.write(f"warning: could not write snapshot to {path}: {err}\n")


# --------------------------------------------------------------------------
# Watch mode -- one line per change
# --------------------------------------------------------------------------


def _change_key(snapshot: dict[str, Any]) -> tuple[Any, ...]:
    checks = snapshot["checks"]
    return (
        snapshot["commit"]["sha"],
        snapshot["commit"]["on_base"],
        checks["passed_count"],
        checks["failed_count"],
        checks["pending_count"],
        tuple(snapshot["actions"]),
    )


def _emit(line: str) -> None:
    sys.stdout.write(line.rstrip() + "\n")
    sys.stdout.flush()


def _summary_lines(snapshot: dict[str, Any]) -> list[str]:
    commit, checks = snapshot["commit"], snapshot["checks"]
    tag = "{branch}@{sha}".format(branch=snapshot["base"]["branch"], sha=commit["sha"][:7])
    actions = snapshot["actions"]
    lines: list[str] = []

    if "commit_not_on_base" in actions:
        lines.append(f"NOT ON BASE {tag} — {commit['sha'][:7]} is not an ancestor of {snapshot['base']['branch']}.")
    elif "verify_locally" in actions:
        lines.append(f"NO PUSH CI {tag} — no workflow runs for this commit. Verify locally with `make ci`.")
    elif "main_verified" in actions:
        lines.append(f"✅ MAIN GREEN {tag} — {checks['passed_count']}/{checks['total_count']} runs passed.")
    else:
        state = "MAIN RED" if checks["failed_count"] else "MAIN RUNNING"
        lines.append(
            "{s} {tag} — {p} passed / {f} failed / {q} pending · actions={a}".format(
                s=state,
                tag=tag,
                p=checks["passed_count"],
                f=checks["failed_count"],
                q=checks["pending_count"],
                a=",".join(actions),
            )
        )

    if checks["failed_names"]:
        lines.append("  failing: " + ", ".join(checks["failed_names"][:8]))
    if checks["never_retry_hit"]:
        lines.append(
            f"  NEVER-RETRY GATE FAILED ON {snapshot['base']['branch'].upper()}: {checks['never_retry_hit']}"
            " — a defect reached the base branch. Do not rerun."
        )
    if checks.get("runs_truncated"):
        lines.append("  WARNING: more than 1000 runs share this SHA; the run list is incomplete. Verify locally too.")
    if checks["excluded_failed_names"]:
        lines.append(
            "  not this commit's verification, but failing: "
            + ", ".join(checks["excluded_failed_names"][:5])
            + " (scheduled or PR runs sharing this SHA)"
        )
    if snapshot["base"]["moved_on"] and checks["failed_count"]:
        lines.append("  note: the base branch has moved past this commit; a `cancelled` run may be by design.")
    lines.append(f"  snapshot: {snapshot['snapshot_file']}")
    return lines


def run_watch(args: argparse.Namespace) -> int:
    last_key: tuple[Any, ...] | None = None
    consecutive_errors = 0
    started = time.time()
    while True:
        try:
            snapshot = collect_snapshot(args)
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
            for line in _summary_lines(snapshot):
                _emit(line)
        last_key = key

        actions = set(snapshot["actions"])
        if "main_verified" in actions or "verify_locally" in actions:
            return 0
        if "stop_never_retry_gate" in actions:
            _emit("STOP: a never-retry gate failed on the base branch. Human decision required (fix, revert, or quarantine).")
            return 0
        if "stop_main_red" in actions:
            _emit("STOP: the base branch is red for this commit. Diagnose the failed job's logs, then fix forward or revert.")
            return 0

        if time.time() - started > args.timeout_seconds:
            _emit(
                f"TIMEOUT after {args.timeout_seconds}s with actions={','.join(snapshot['actions'])}."
                " A run is stuck or never started — check the Actions tab."
            )
            return 1
        time.sleep(args.poll_seconds)


# --------------------------------------------------------------------------


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--commit", default="auto", help="'auto' (current tip of --branch) or a merge commit SHA")
    parser.add_argument("--branch", default="main", help="Base branch to verify (default: main)")
    parser.add_argument("--repo", help="OWNER/REPO override")
    parser.add_argument("--snapshot-file", help="Path the full snapshot JSON is written to")
    parser.add_argument("--poll-seconds", type=int, default=DEFAULT_POLL_SECONDS, help="Watch poll interval")
    parser.add_argument("--timeout-seconds", type=int, default=DEFAULT_TIMEOUT_SECONDS, help="Give up watching after this")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--once", action="store_true", help="Emit one JSON snapshot and exit")
    mode.add_argument("--watch", action="store_true", help="Emit one line per change until the outcome is decided")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.poll_seconds < 30:
        sys.stderr.write("main_verify.py: --poll-seconds below 30 risks GitHub secondary rate limits; using 30\n")
        args.poll_seconds = 30
    try:
        if args.watch:
            return run_watch(args)
        print(json.dumps(collect_snapshot(args), indent=2, sort_keys=True))
        return 0
    except (GhError, ValueError) as err:
        sys.stderr.write(f"main_verify.py: {err}\n")
        return 1
    except KeyboardInterrupt:
        sys.stderr.write("main_verify.py: interrupted\n")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
