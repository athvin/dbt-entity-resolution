"""Bit-exact probes of the arithmetic every parity claim rests on (G5, §22.1, 3.59).

`DesignDoc` Appendix A measured *"exact bit equality"* **in-process on darwin
arm64**, while baselines are committed and compared on **linux/amd64** in CI.
§B.5 item 4 calls the gap *"an afternoon of CI time"* that *"either closes the
finding permanently or changes the tolerance table"* — and A.4's whole standing
note depends on the answer:

> exact bit equality is the right default because both engines run float8 on the
> same DuckDB; where the expression tree is identical the result is identical.

That is a claim about **two platforms**, evidenced on one.

**This module makes it a continuously asserted property rather than a
measurement.** The reference bit patterns are committed; a test asserts the
current platform reproduces them; that test runs locally on darwin/arm64 and in
CI on `ubuntu-24.04`, which is native linux/amd64. Two platforms therefore
assert the same patterns on every single run, and a divergence fails loudly
instead of being discovered when a baseline stops reproducing.

Bit patterns, not decimal strings: `repr()` round-trips a double, but comparing
formatted output tests the formatter as much as the arithmetic, and 3.59 is about
the bits.
"""

from __future__ import annotations

import platform
import struct
from typing import TYPE_CHECKING

import duckdb

if TYPE_CHECKING:
    from collections.abc import Mapping

# The probes deliberately mirror Stage 5's arithmetic rather than testing
# floating point in the abstract: bayes factors multiplied in LINEAR space, one
# log2 at the end (D6 / DR-06), Splink's clamp, and the probability derivation.
PROBE_SQL = """
with m_u as (
    select * from (values
        (0.9,    0.01),
        (0.7,    0.2),
        (0.99,   0.001),
        (0.5,    0.5),
        (0.3,    0.7),
        (0.001,  0.99),
        (0.9999, 0.0001)
    ) as t(m, u)
),
bf as (
    select m, u, m / u as bayes_factor from m_u
),
prod as (
    select
        product(bayes_factor)      as via_product,
        exp(sum(ln(bayes_factor))) as via_logs
    from bf
)
select
    via_product,
    via_logs,
    least(greatest(via_product, 1e-300), 1e300)       as clamped,
    log2(least(greatest(via_product, 1e-300), 1e300)) as match_weight,
    via_product / (1.0 + via_product)                 as match_probability
from prod
"""

PROBE_COLUMNS = (
    "via_product",
    "via_logs",
    "clamped",
    "match_weight",
    "match_probability",
)

# Measured 2026-08-23 on DuckDB 1.5.5, IDENTICAL on darwin/arm64 and
# linux/amd64. G5 is closed by this table, and it stays closed only while both
# platforms keep reproducing it.
REFERENCE: Mapping[str, str] = {
    "via_product": "413498e8ffffffff",
    "via_logs": "413498e8fffffffc",
    "clamped": "413498e8ffffffff",
    "match_weight": "40345d48400a308f",
    "match_probability": "3feffffe724742f5",
}

MEASURED_ON_DUCKDB = "1.5.5"


def bits(value: float) -> str:
    """Return the IEEE-754 double's 8 bytes as big-endian hex."""
    return struct.pack(">d", value).hex()


def probe() -> dict[str, str]:
    """Run the probe on this machine and return `{column: bit pattern}`."""
    con = duckdb.connect()
    try:
        row = con.execute(PROBE_SQL).fetchone()
    finally:
        con.close()
    if row is None:  # pragma: no cover - the query is a constant projection
        msg = "the float probe returned no row"
        raise RuntimeError(msg)
    return {column: bits(value) for column, value in zip(PROBE_COLUMNS, row, strict=True)}


def platform_tag() -> str:
    """Return the `(os, architecture)` half of §20.1's platform triple."""
    machine = platform.machine().lower()
    # linux reports x86_64, the manifest and section 22.1 say amd64.
    normalised = {"x86_64": "amd64", "aarch64": "arm64"}.get(machine, machine)
    return f"{platform.system().lower()}/{normalised}"
