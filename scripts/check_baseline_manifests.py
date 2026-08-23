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

import hashlib
import sys
from typing import TYPE_CHECKING, Any

import yaml

from _er_paths import ROOT, rel
from _er_pending import Pending

if TYPE_CHECKING:
    from pathlib import Path

FIXTURES = "fixtures"

# Artefacts that need a sidecar. Section 5's tree is "model JSONs, Splink
# baselines, each with a .manifest.yml" -- and .csv is here because a VENDORED
# fixture needs provenance more urgently than a generated one, not less:
# `splink_datasets.fake_1000` downloads from a mutable `master` ref at
# attribute-access time, so without a recorded hash "fake_1000" names whatever
# that URL served most recently.
#
# `.sql` is here because a REVIEWED SNAPSHOT is a baseline: Stage 1's AC compares
# rendered SQL against Splink's own captured output, so the snapshot needs the
# same provenance as a parquet -- which Splink version produced it, and from
# which model JSON.
_BASELINE_SUFFIXES = (".parquet", ".json", ".csv", ".sql")

# Three kinds of artefact live under fixtures/, and they cannot share one field
# list: a generated baseline records how it was produced, a vendored file
# records where it came from, and a hand-authored fixture records what it probes.
_KIND_FIELDS = {
    "baseline": (
        "splink_version",
        "model_json_sha256",
        "seed",
        "duckdb_version",
        "sqlglot_version",
        "platform",
        "date",
        "producing_commit",
    ),
    "vendored": (
        "sha256",
        "bytes",
        "retrieved",
        "source_url",
        "licence",
        "copyright",
    ),
    "synthetic": ("authored", "generator", "shape", "probes"),
}


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


def _field_errors(name: str, manifest: dict[str, Any], artefact: Path) -> list[str]:
    """Return every missing or malformed provenance field for this artefact's kind."""
    errors: list[str] = []
    kind = manifest.get("kind")
    if kind not in _KIND_FIELDS:
        return [
            (
                f"{name}: manifest declares kind={kind!r}; expected one of "
                f"{sorted(_KIND_FIELDS)}. Without a kind there is no way to know "
                f"which provenance fields apply, so none can be required."
            )
        ]

    for field in _KIND_FIELDS[kind]:
        value = manifest.get(field)
        if value is None or (isinstance(value, str) and not value.strip()):
            errors.append(f"{name}: manifest is missing `{field}` (kind={kind}, section 20.1).")

    if kind == "vendored":
        errors.extend(_vendored_errors(name, manifest, artefact))

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


def _vendored_errors(name: str, manifest: dict[str, Any], artefact: Path) -> list[str]:
    """Verify a vendored file against its recorded hash.

    Recording a hash and never checking it is the same class of defect as a
    `[VERIFIED]` marker nobody re-earns: it looks like provenance and asserts
    nothing. This is the check that makes vendoring worth the bytes.
    """
    declared = str(manifest.get("sha256", "")).strip()
    if not declared:
        return []
    actual = hashlib.sha256(artefact.read_bytes()).hexdigest()
    errors: list[str] = []
    if actual != declared:
        errors.append(
            f"{name}: sha256 is {actual}, manifest says {declared}. The vendored "
            f"file and its provenance disagree -- every baseline generated from it "
            f"is unattributable."
        )
    size = manifest.get("bytes")
    if isinstance(size, int) and size != artefact.stat().st_size:
        errors.append(f"{name}: {artefact.stat().st_size} bytes on disk, manifest says {size}.")
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
        errors.extend(_field_errors(name, loaded, baseline))

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
