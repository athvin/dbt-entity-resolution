#!/usr/bin/env python3
"""Fixtures and seeds are synthetic; no secrets, no real person data (3.55, §20.4).

§20.4 names the exposure precisely, and it is the ordinary path rather than a
careless one:

> The natural way to debug a parity failure is to reproduce it on the data that
> failed, which is real. One `git add` later it is in history permanently.

3.55's mechanism is *"`detect-private-key` + a PII heuristic scan"*. The first
half has been wired since the scaffold landed. **This is the second half**, which
did not exist -- and it had no subject to protect until `fixtures/` gained 1,000
person-shaped records.

**The primary control is a declaration, not a heuristic.** Every data file under
`seeds/` and `fixtures/` must carry a manifest saying `synthetic: true`. That
makes "this is not real data" a conscious assertion someone signs, rather than
something a regex tries to infer. Heuristics cannot prove data is synthetic; a
person can, and then the check holds them to it.

**The heuristics catch what a declaration cannot.** Two classes:

  * **Structured identifiers** -- Luhn-valid card numbers, NI numbers, SSNs,
    IBANs. These should never appear even in synthetic data, because a
    well-formed identifier is indistinguishable from a real one.
  * **Consumer email providers** -- `gmail.com` and friends. A blocklist, not an
    allowlist: the vendored `fake_1000` uses surname-derived domains like
    `humphrey.com`, which an allowlist would reject wholesale while a blocklist
    correctly ignores. Real person data overwhelmingly carries consumer
    providers; synthetic generators almost never emit them.
"""

from __future__ import annotations

import re
import sys
from typing import TYPE_CHECKING

import yaml

from _er_paths import ROOT, rel

if TYPE_CHECKING:
    from pathlib import Path

SCANNED_DIRS = ("seeds", "fixtures", "harness")
DATA_SUFFIXES = (".csv", ".json", ".parquet")

# If the walk finds fewer than this, the check has lost its subject rather than
# the repository having become clean -- section 6.1's vacuous-pass failure.
MIN_FILES_SCANNED = 5

# Real person data overwhelmingly carries these; synthetic generators do not.
_CONSUMER_PROVIDERS = frozenset(
    {
        "gmail.com",
        "googlemail.com",
        "yahoo.com",
        "yahoo.co.uk",
        "hotmail.com",
        "hotmail.co.uk",
        "outlook.com",
        "live.com",
        "icloud.com",
        "me.com",
        "aol.com",
        "protonmail.com",
        "proton.me",
    }
)

_EMAIL = re.compile(r"[\w.+-]+@([\w-]+\.[\w.-]+)")
# UK National Insurance: two letters, six digits, a final letter A-D.
_NI_NUMBER = re.compile(r"\b[A-CEGHJ-PR-TW-Z]{2}\d{6}[A-D]\b")
# US SSN, excluding the ranges the SSA never issues.
_SSN = re.compile(r"\b(?!000|666|9\d\d)\d{3}-(?!00)\d{2}-(?!0000)\d{4}\b")
_IBAN = re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b")
_CARD_CANDIDATE = re.compile(r"\b(?:\d[ -]?){13,19}\b")


def _luhn_valid(digits: str) -> bool:
    """Return True when `digits` passes the Luhn checksum."""
    total = 0
    for index, char in enumerate(reversed(digits)):
        value = int(char)
        if index % 2 == 1:
            value *= 2
            if value > 9:  # noqa: PLR2004
                value -= 9
        total += value
    return total % 10 == 0


def _data_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for name in SCANNED_DIRS:
        base = root / name
        if not base.is_dir():
            continue
        files.extend(
            p
            for p in base.rglob("*")
            if p.is_file() and p.suffix in DATA_SUFFIXES and not p.name.endswith(".manifest.yml")
        )
    return sorted(files)


def _declared_synthetic(path: Path) -> bool:
    """Report whether this file's sidecar manifest asserts `synthetic: true`."""
    sidecar = path.with_suffix(path.suffix + ".manifest.yml")
    if not sidecar.is_file():
        return False
    loaded = yaml.safe_load(sidecar.read_text(encoding="utf-8"))
    return isinstance(loaded, dict) and loaded.get("synthetic") is True


def _scan_text(name: str, text: str) -> list[str]:
    """Return every structured-identifier or consumer-provider hit."""
    findings: list[str] = []

    domains = {match.lower() for match in _EMAIL.findall(text)}
    findings.extend(
        f"{name}: email domain `{domain}` is a consumer provider. Real person "
        f"data carries these; synthetic generators do not (3.55, §20.4)."
        for domain in sorted(domains & _CONSUMER_PROVIDERS)
    )

    for label, pattern in (("UK National Insurance number", _NI_NUMBER), ("US SSN", _SSN)):
        hits = sorted(set(pattern.findall(text)))[:3]
        findings.extend(
            f"{name}: looks like a {label} (`{hit}`). A well-formed identifier is "
            f"indistinguishable from a real one, so it does not belong in a fixture."
            for hit in hits
        )

    iban_hits = sorted({h for h in _IBAN.findall(text) if any(c.isdigit() for c in h[4:])})[:3]
    findings.extend(f"{name}: looks like an IBAN (`{hit}`)." for hit in iban_hits)

    for candidate in set(_CARD_CANDIDATE.findall(text)):
        digits = re.sub(r"[ -]", "", candidate)
        if 13 <= len(digits) <= 19 and _luhn_valid(digits):  # noqa: PLR2004
            findings.append(
                f"{name}: a {len(digits)}-digit run passes the Luhn checksum "
                f"(`{digits[:4]}...{digits[-4:]}`). That is a card-number shape."
            )
    return findings


def _readable(path: Path) -> str | None:
    """Return the text of a scannable file, or None when it is binary."""
    if path.suffix == ".parquet":
        return None
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def check(root: Path = ROOT) -> list[str]:
    """Return every undeclared or suspicious data file under the scanned trees."""
    files = _data_files(root)
    if len(files) < MIN_FILES_SCANNED:
        return [
            (
                f"scanned only {len(files)} data file(s) across {list(SCANNED_DIRS)}, "
                f"expected at least {MIN_FILES_SCANNED}. A PII scan that walks an "
                f"empty tree exits 0 and reads as a pass."
            )
        ]

    errors: list[str] = []
    for path in files:
        name = rel(path, root)
        if not _declared_synthetic(path):
            errors.append(
                f"{name}: no sidecar manifest declaring `synthetic: true`. §20.4's "
                f"rule is that fixtures and seeds are synthetic ONLY -- state it, so "
                f"the claim is something a person made rather than something a "
                f"heuristic guessed."
            )
        text = _readable(path)
        if text is not None:
            errors.extend(_scan_text(name, text))

    sys.stdout.write(f"3.55: {len(files)} data file(s) scanned for PII indicators.\n")
    return errors


def main() -> int:
    """Return 0 when every fixture is declared synthetic and looks it."""
    errors = check()
    for err in errors:
        sys.stderr.write(f"ERROR: {err}\n")
    if errors:
        sys.stderr.write(f"\n{len(errors)} PII/synthetic-declaration finding(s) (3.55).\n")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
