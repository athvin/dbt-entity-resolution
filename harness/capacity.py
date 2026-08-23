"""Bytes-per-pair, measured rather than inherited (Stage 0.6, D11 rec 5, G14).

`er_max_pairs = 42,000,000` was derived from a **946 B/pair** wide shape, and
§5 Stage 0.6 says the figure "under-provisions the narrow one by roughly 10x".
Re-deriving it needs a measurement, and the measurement needs its **units**
stated, because they change the answer by more than the shape does.

**What this module measured on DuckDB 1.5.5** (200,000 rows, `[RUN]`):

| data | shape | disk B/pair | memory B/pair |
|---|---|---|---|
| compressible | narrow | 17.1 | **104.5** |
| compressible | wide | 45.9 | 343.1 |
| high-entropy | narrow | 53.8 | **151.7** |
| high-entropy | wide | 298.9 | 665.5 |

Two things follow.

**1. The published figures are MEMORY figures.** The compressible-narrow memory
result, 104.5 B/pair, reproduces §5's "the measured narrow ~100 B/pair" almost
exactly. Disk is ~6x smaller for the same rows, so measuring the obvious way --
the size of the database file -- over-provisions the cap by about six.

**2. Entropy moves the number as much as width does.** High-entropy narrow
(151.7) costs more than compressible narrow (104.5) by 1.45x, and the disk
figures move by 3x, because DuckDB's dictionary and RLE compression works on
repetitive synthetic data and does not work on real names and emails. **A
capacity figure measured on tidy synthetic data is optimistic about production**,
which is the wrong direction for a guardrail whose failure mode is an OOM.

So `er_max_pairs` is not a constant here. It is derived from a stated memory
budget and a stated per-pair cost, and both are visible where the cap is set.
"""

from __future__ import annotations

import pathlib
import tempfile
from dataclasses import dataclass
from typing import TYPE_CHECKING

import duckdb

if TYPE_CHECKING:
    from collections.abc import Sequence

# Measured on this pin. Section 0's rule applies: a bump re-measures.
MEASURED_ON_DUCKDB = "1.5.5"

# The conservative cell of the table above -- high-entropy narrow, in memory.
# Conservative because a guardrail that fails as an OOM should err toward
# refusing work, and because real person data does not compress like `i % 97`.
BYTES_PER_PAIR_NARROW = 152

# Fraction of the memory budget the pair relation may occupy. The rest is the
# join that produces it, the hash tables, and everything else in the build --
# a cap that assumes the whole budget is available to one relation is not a cap.
PAIR_BUDGET_FRACTION = 0.5

NARROW_COLUMNS = (
    "unique_id_l",
    "unique_id_r",
    "match_key",
    "gamma_*",
    "match_weight",
    "match_probability",
)


@dataclass(frozen=True)
class Measurement:
    """One B/pair measurement, with the two facts that make it interpretable."""

    shape: str
    entropy: str
    rows: int
    disk_bytes_per_pair: float
    memory_bytes_per_pair: float


def _projection(*, wide: bool, high_entropy: bool) -> str:
    if high_entropy:
        ident, number = "md5(i::varchar)", "random()"

        def text(salt: int) -> str:
            return f"md5((i * {salt})::varchar)"
    else:
        ident, number = "'id-' || i::varchar", "(i * 0.001)::double"

        def text(salt: int) -> str:
            return f"'v' || (i % {salt})::varchar"

    columns = [
        f"{ident} as unique_id_l",
        f"{ident} as unique_id_r",
        "(i % 4)::varchar as match_key",
        *[f"(i % {4 + k})::int as gamma_{k}" for k in range(5)],
        f"{number} as match_weight",
        f"{number} as match_probability",
    ]
    if wide:
        columns += [f"{number} as bf_{k}" for k in range(5)]
        columns += [f"{number} as tf_{k}" for k in range(5)]
        columns += [f"{text(97 + k)} as attr_{k}" for k in range(10)]
    return ", ".join(columns)


def _ddl(rows: int, *, wide: bool, high_entropy: bool) -> str:
    return (
        f"create or replace table pairs as "  # noqa: S608 - a constant projection
        f"select {_projection(wide=wide, high_entropy=high_entropy)} "
        f"from range({rows}) t(i)"
    )


def measure(rows: int = 200_000, *, wide: bool, high_entropy: bool) -> Measurement:
    """Measure one shape both ways, because the two answers differ by ~6x."""
    ddl = _ddl(rows, wide=wide, high_entropy=high_entropy)

    with tempfile.TemporaryDirectory(prefix="er-capacity-") as tmp:
        path = pathlib.Path(tmp) / "capacity.duckdb"
        con = duckdb.connect(str(path))
        try:
            con.execute(ddl)
            con.execute("checkpoint")
        finally:
            con.close()
        disk = path.stat().st_size / rows

    con = duckdb.connect()
    try:
        con.execute(ddl)
        used = con.execute("select sum(memory_usage_bytes) from duckdb_memory()").fetchone()
        memory = (used[0] or 0) / rows if used else 0.0
    finally:
        con.close()

    return Measurement(
        shape="wide" if wide else "narrow",
        entropy="high-entropy" if high_entropy else "compressible",
        rows=rows,
        disk_bytes_per_pair=disk,
        memory_bytes_per_pair=memory,
    )


def max_pairs(memory_budget_bytes: int, bytes_per_pair: int = BYTES_PER_PAIR_NARROW) -> int:
    """Derive the pair cap from a stated budget and a stated per-pair cost.

    This is the function `er_max_pairs` should be set from. A constant carried
    forward from a different shape -- which is what 42,000,000 was -- cannot be
    checked against the configuration it is meant to protect.
    """
    if memory_budget_bytes <= 0 or bytes_per_pair <= 0:
        msg = "memory budget and bytes-per-pair must both be positive"
        raise ValueError(msg)
    return int(memory_budget_bytes * PAIR_BUDGET_FRACTION / bytes_per_pair)


def summarise(measurements: Sequence[Measurement]) -> str:
    """Render the table this module's docstring records."""
    lines = [f"{'data':<14}{'shape':<8}{'disk B/pair':>13}{'memory B/pair':>15}"]
    lines.extend(
        f"{m.entropy:<14}{m.shape:<8}{m.disk_bytes_per_pair:>13.1f}{m.memory_bytes_per_pair:>15.1f}"
        for m in measurements
    )
    return "\n".join(lines)
