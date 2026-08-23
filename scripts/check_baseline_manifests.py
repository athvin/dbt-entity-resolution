#!/usr/bin/env python3
"""Every baseline carries a complete provenance manifest (3.62, section 20.1).

A baseline is the oracle every parity gate compares against. Without provenance
it is a binary blob asserting a number, and section 20.1 names the two fields
whose absence is silent rather than loud:

  * **sqlglot version** -- "it, not Splink, decides which comparison levels
    receive a TF adjustment", and it arrives transitively. A baseline generated
    under a different sqlglot is a different baseline *with no visible
    difference*.
  * **the platform triple** -- Appendix A measured "exact bit equality"
    in-process on darwin arm64, while these baselines are compared on
    linux/amd64 in CI (G5, section 22.1). "A manifest that cannot say which
    platform produced a baseline cannot support the parity claim built on it."

Both are the same failure: a baseline that is wrong in a way no diff shows.
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING, Any

import yaml

from _er_paths import ROOT, rel
from _er_pending import Pending

if TYPE_CHECKING:
    from pathlib import Path

FIXTURES = "fixtures"

# Artefacts that need a sidecar. Model JSONs are baselines too -- section 5's
# tree is "model JSONs, Splink baselines, each with a .manifest.yml".
_BASELINE_SUFFIXES = (".parquet", ".json")

# Section 20.1, in full. Each is here because its absence is undetectable later.
_REQUIRED = (
    "splink_version",
    "model_json_sha256",
    "seed",
    "duckdb_version",
    "sqlglot_version",
    "platform",
    "date",
    "producing_commit",
)

# The platform triple, `(os, architecture, DuckDB build)`.
_PLATFORM_KEYS = ("os", "architecture", "duckdb_build")

_SHA256_LEN = 64


def _sidecar(path: Path) -> Path:
    return path.with_suffix(path.suffix + ".manifest.yml")


def _baselines(base: Path) -> list[Path]:
    return sorted(
        p
        for p in base.rglob("*")
        if p.is_file() and p.suffix in _BASELINE_SUFFIXES and not p.name.endswith(".manifest.yml")
    )


def _field_errors(name: str, manifest: dict[str, Any]) -> list[str]:
    """Return every missing or malformed provenance field."""
    errors: list[str] = []
    for field in _REQUIRED:
        value = manifest.get(field)
        if value is None or (isinstance(value, str) and not value.strip()):
            errors.append(f"{name}: manifest is missing `{field}` (section 20.1).")

    platform = manifest.get("platform")
    if platform is not None:
        if not isinstance(platform, dict):
            errors.append(
                f"{name}: `platform` must be the triple {list(_PLATFORM_KEYS)}, not "
                f"{type(platform).__name__}."
            )
        else:
            errors.extend(
                f"{name}: `platform` is missing `{key}`. The triple exists because "
                f"Appendix A measured bit equality on darwin arm64 while CI compares "
                f"on linux/amd64 (G5)."
                for key in _PLATFORM_KEYS
                if not platform.get(key)
            )

    sha = manifest.get("model_json_sha256")
    if isinstance(sha, str) and sha.strip() and len(sha.strip()) != _SHA256_LEN:
        errors.append(
            f"{name}: `model_json_sha256` is {len(sha.strip())} characters, expected "
            f"{_SHA256_LEN}. A truncated hash still compares equal to itself."
        )

    return errors


def check(root: Path = ROOT) -> list[str]:
    """Return every baseline whose provenance is missing or incomplete."""
    pending = Pending("check_baseline_manifests.py", root=root)
    errors: list[str] = list(pending.errors())

    base = root / FIXTURES
    if not base.is_dir():
        if pending.is_pending(FIXTURES):
            sys.stdout.write(pending.notice(FIXTURES) + "\n")
            return errors
        errors.append(
            f"{FIXTURES}/ does not exist and is not declared pending. 3.62 over an "
            f"absent directory is a check with no subject."
        )
        return errors

    baselines = _baselines(base)
    if not baselines:
        errors.append(
            f"{FIXTURES}/ exists but contains no baselines. Either remove it or "
            f"declare it pending -- an empty walk exits 0 and reads as a pass."
        )
        return errors

    for baseline in baselines:
        name = rel(baseline, root)
        sidecar = _sidecar(baseline)
        if not sidecar.is_file():
            errors.append(
                f"{name}: no sidecar at {rel(sidecar, root)}. A baseline without "
                f"provenance is a number with no way to reproduce it (3.62)."
            )
            continue
        loaded = yaml.safe_load(sidecar.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict):
            errors.append(f"{name}: sidecar is not a YAML mapping.")
            continue
        errors.extend(_field_errors(name, loaded))

    sys.stdout.write(f"3.62: {len(baselines)} baseline(s) checked.\n")
    return errors


def main() -> int:
    """Return 0 when every baseline carries complete provenance."""
    errors = check()
    for err in errors:
        sys.stderr.write(f"ERROR: {err}\n")
    if errors:
        sys.stderr.write(f"\n{len(errors)} baseline provenance defect(s) (3.62).\n")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
