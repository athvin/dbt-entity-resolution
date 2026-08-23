#!/usr/bin/env python3
"""Baselines must regenerate byte-identically (3.84, §20.1, D.0 finding 71).

Every parity claim in this project is a comparison against `fixtures/baselines/`.
That makes one property load-bearing and, until 2026-08-23, untrue: **the
baselines must be re-derivable from the frozen model.**

They were not. `[RUN]`, three regenerations from the *same* frozen model:

    predictions.parquet   3,989 rows each, set-difference ZERO, bytes DIFFERENT

Splink returns rows in engine order and builds the concat table's `tf_*` columns
in an order that varied between processes, so every artefact was byte-unstable
while being semantically identical. Three things were quietly untrue at once:

  * §20.1's regeneration target could not be run without producing a diff,
    so in practice it was never run;
  * 3.62 verified a `sha256` that changed for no semantic reason, which makes a
    *matching* sha the surprising outcome rather than the expected one;
  * A.4 gates pair sets as **"exact after canonical ordering"** -- describing an
    ordering step nothing in the harness performed.

None of that is a wrong answer. It is the failure mode this repository keeps
meeting: **a gate that looks like it works.** A baseline nobody can regenerate
cannot be audited, and a manifest sha that drifts on its own trains everyone to
ignore it.

`gen_baseline.py` now canonicalises columns and rows before writing, and this
check is what keeps that true: it regenerates into a temporary directory and
compares against the committed bytes.

**Byte identity is a SAME-PLATFORM property, and measuring that closed G5.**
`[RUN]` 2026-08-23, darwin/arm64 vs linux/amd64 (Docker, identical splink,
duckdb, sqlglot, pandas, model sha and fixture sha): **bytes differ on all six
artefacts**, while the data is identical on five and `predictions` differs on
**one pair** -- `max|delta mw| = 1.78e-15`, `max|delta p| = 0`, and **zero edge
flips** at 0.5 and 0.9. So Appendix A's "exact bit equality" does not survive a
platform change, the divergence sits far inside A.4's `1e-9 + 1e-12*|mw|`, and
A.4's *binding* gate -- edge-set membership -- holds exactly. This check is
therefore binding on linux/amd64 and advisory elsewhere, saying which it is.

**It deliberately does not retrain**, and the reason is a separate measurement.
`[RUN]`, four processes, same seed and code: EM-trained `m_probability` values
differ by up to **1.4e-15 relative (~6 ULP)** run to run. Splink's EM is simply
not byte-reproducible, so a check demanding byte equality of a *retrained* model
could never pass. Retraining is therefore `--refreeze` only -- a deliberate,
reviewed act -- and A.4 carries the tolerance that says whether a re-minted
model is the same model or a different one.
"""

from __future__ import annotations

import hashlib
import platform
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from _er_paths import ROOT, rel

BASELINE_DIR = ROOT / "fixtures" / "baselines" / "fake_1000"
MODEL_JSON = ROOT / "fixtures" / "model_jsons" / "fake_1000_v1.json"

# Below this, the check has stopped checking rather than the project having
# become simple -- §6.1's vacuous pass, the same floor 3.83 carries.
MIN_BASELINES = 4

# §22.1 / 3.59: float-exact artefacts are anchored to linux/amd64, and the
# baselines are committed as minted THERE. Byte identity is a same-platform
# property, which G5 asked about and this gate answered -- see below.
NORMATIVE_OS = "linux"
NORMATIVE_ARCH = {"x86_64": "amd64", "amd64": "amd64"}


def on_normative_platform() -> bool:
    """Report whether byte identity is fair to demand of this machine."""
    return (
        platform.system().lower() == NORMATIVE_OS
        and NORMATIVE_ARCH.get(platform.machine().lower()) == "amd64"
    )


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check(root: Path = ROOT) -> list[str]:
    """Regenerate the baselines and compare bytes against what is committed."""
    baseline_dir = root / "fixtures" / "baselines" / "fake_1000"
    committed = sorted(baseline_dir.glob("*.parquet"))
    if len(committed) < MIN_BASELINES:
        return [
            (
                f"only {len(committed)} baseline(s) found under "
                f"{rel(baseline_dir, root)}, expected at least {MIN_BASELINES}. A "
                f"reproducibility check with nothing to reproduce passes "
                f"unconditionally."
            )
        ]

    errors: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "regenerated"
        model_copy = Path(tmp) / "model.json"
        # Copy the frozen model so a bug in the generator cannot overwrite it
        # even if `--refreeze` were somehow passed.
        shutil.copy(root / "fixtures" / "model_jsons" / "fake_1000_v1.json", model_copy)

        result = subprocess.run(  # noqa: S603
            [
                sys.executable,
                str(root / "scripts" / "gen_baseline.py"),
                "--out",
                str(out),
                "--model-json",
                str(model_copy),
            ],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            tail = (result.stderr or result.stdout).strip().splitlines()[-5:]
            return [
                "regenerating the baselines failed, so reproducibility cannot be "
                "established at all:\n    " + "\n    ".join(tail)
            ]

        for path in committed:
            fresh = out / path.name
            if not fresh.is_file():
                errors.append(
                    f"{rel(path, root)} is committed but regeneration did not "
                    f"produce it. Either the generator no longer emits it, or the "
                    f"committed file is an orphan no gate covers."
                )
                continue
            if _sha(path) != _sha(fresh):
                errors.append(
                    f"{rel(path, root)} does not regenerate byte-identically.\n"
                    f"    committed:   {_sha(path)[:32]}\n"
                    f"    regenerated: {_sha(fresh)[:32]}\n"
                    f"  Either the generator changed and the baseline was not "
                    f"refreshed, or an ordering that used to be canonical no "
                    f"longer is. A baseline that cannot be re-derived cannot be "
                    f"audited (D.0 finding 71)."
                )

    sys.stdout.write(f"3.84: {len(committed)} baseline(s) regenerated and compared.\n")
    return errors


def main() -> int:
    """Return 0 when every committed baseline regenerates byte-identically.

    **Off linux/amd64 this reports and returns 0**, because the difference it
    would find is a platform difference and not a defect -- see the module
    docstring's G5 measurement. It says so out loud rather than printing a bare
    "OK": §22.1 requires a local run to state which gates it could not really
    perform, and a gate that silently downgrades itself is worse than one that
    is simply absent.
    """
    errors = check()
    if not on_normative_platform():
        machine = f"{platform.system().lower()}/{platform.machine().lower()}"
        sys.stdout.write(
            f"  ** 3.84 ADVISORY on {machine} **\n"
            f"  Baselines are committed as minted on linux/amd64 (§22.1, 3.59), and\n"
            f"  byte identity does not hold across platforms -- G5, measured: max\n"
            f"  |delta mw| = 1.78e-15, zero edge flips. {len(errors)} difference(s)\n"
            f"  found here are EXPECTED and are not a local failure. CI is the\n"
            f"  authority for this gate.\n"
        )
        return 0
    for err in errors:
        sys.stderr.write(f"ERROR: {err}\n")
    if errors:
        sys.stderr.write(f"\n{len(errors)} baseline(s) not reproducible (3.84).\n")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
