#!/usr/bin/env python3
"""Assert dbt-bouncer actually ran its checks (3.40).

3.40 originally read: *"assert expected check names and a minimum count in the
bouncer result; loader WARNINGs are treated as errors"*. Measured on this
repository, that is not enough.

Omitting ``package_name`` made dbt-bouncer filter to a project that owns no
models. The coverage checks divided by zero, dbt-bouncer **downgraded them to
warnings**, and the run reported ``SUCCESS=0 WARN=2 ERROR=0`` and **exited 0**.
So a check raising *during execution* is silent too, not just a loader failure --
and ``SUCCESS=0`` is a green run. Appendix D.0 finding 20.

This reads the ``--output-format json`` artefact rather than the rendered table,
because **the rich table wraps and truncates check names to the terminal width**.
An earlier version grepped that table and passed locally while failing in CI at
a different width, which is the same class of defect one layer up: an assertion
whose result depends on something other than what it claims to measure.
"""

from __future__ import annotations

import collections
import json
import sys
from pathlib import Path

# Below today's 25 so adding a model does not trip it; far enough above zero that
# a config matching nothing does. Raise it as the model count grows.
MIN_SUCCESSES = 20

# 6.2: an import failure in a custom check is a WARNING and the run stays green,
# so the check being *configured* proves nothing about it having *loaded*.
REQUIRED_CHECKS = ("check_one_yml_per_sql",)

# Anything that is not a success. `warning` is the dangerous one: it is how a
# raising check reports, and it does not fail the run.
BAD_OUTCOMES = ("warning", "error", "fail")


def check(results_path: Path) -> list[str]:
    """Return every reason the bouncer run cannot be trusted."""
    if not results_path.is_file():
        return [f"{results_path} does not exist -- dbt-bouncer produced no results file."]

    runs = json.loads(results_path.read_text(encoding="utf-8"))
    outcomes = collections.Counter(r["outcome"] for r in runs)
    names = {r["check_run_id"].split(":")[0] for r in runs}
    sys.stdout.write(f"bouncer: {len(runs)} check run(s), outcomes={dict(outcomes)}\n")

    errors: list[str] = []
    if outcomes["success"] < MIN_SUCCESSES:
        errors.append(
            f"only {outcomes['success']} check(s) succeeded; expected at least "
            f"{MIN_SUCCESSES}. A config that matches nothing exits 0."
        )
    errors.extend(
        f"{outcomes[bad]} check(s) had outcome {bad!r}. A check that raises is "
        f"downgraded to a warning and the run stays green."
        for bad in BAD_OUTCOMES
        if outcomes[bad]
    )
    errors.extend(
        f"`{required}` did not register. A custom check that fails to import is a "
        f"WARNING that leaves the run green (section 6.2)."
        for required in REQUIRED_CHECKS
        if required not in names
    )
    return errors


def main() -> int:
    """Return 0 when the bouncer run is trustworthy."""
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("bouncer.json")
    errors = check(path)
    for err in errors:
        sys.stderr.write(f"::error::{err}\n")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
