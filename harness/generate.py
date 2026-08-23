"""A seeded synthetic person-record generator (Stage 0.2, §20.2, §20.4).

`fake_1000` is fixed at 1,000 records. Stage 11's nightly differential loop needs
**data-varying** runs, and Stage 0.6's capacity work needs scale — so the
programme needs a generator as well as a fixture, and §5 Stage 0.2 asks for one.

**Everything is seeded.** Same seed, byte-identical output; different seed,
different data. A fixture nobody can regenerate is not a regression test, and a
nightly that cannot reproduce its own failing input is not debuggable — which is
the failure §14 records as *"a nightly failure is not reproducible without the
failure-bundle schema"*.

**Calibrated against the vendored fixture rather than invented.** Measured on
`fake_1000`:

| property | `fake_1000` | this generator |
|---|---|---|
| cluster sizes | 1-7, roughly uniform (43/30/37/34/33/33/41) | 1..`max_cluster_size`, uniform |
| `first_name` missing | 16.9% | ~17% |
| `surname` missing | 18.1% | ~18% |
| `city` missing | 18.7% | ~19% |
| `email` missing | 21.1% | ~21% |
| **`dob` missing** | **0%** | **0%** |

`dob` is never null in `fake_1000`, and that is not an accident worth smoothing
away: it is the one attribute always available to block on, which is exactly
what makes `block_on(dob)` the rule that lifted blocking recall from 0.5057 to
0.8124 in Stage 0.4.

**It complements `fake_1000` rather than duplicating it, and the difference is
the point.** `[RUN]` over two seeds, same model and training:

| | rows | true pairs | blocking recall | precision |
|---|---|---|---|---|
| `fake_1000` | 1,000 | 2,031 | 0.8124 | **1.0000** |
| generated, seed 1 | 978 | 1,900 | 0.9153 | **0.9798** |
| generated, seed 2 | 927 | 1,785 | 0.9109 | **0.9847** |

**Precision is 1.0000 on `fake_1000` at every threshold** — it produces no false
positives at all, so no gate built on it can detect a precision regression. The
generated corpora do produce them, because the name pool is deliberately small
and distinct personas can therefore resemble each other. A fixture that cannot
fail in a given way cannot defend against it, which is the same argument §12.7
makes about comparators, applied to data.

**Email domains are surname-derived**, the shape `fake_1000` uses
(`humphrey.com`, `smith.net`). Not a stylistic choice: `check_pii_heuristics.py`
(3.55) rejects consumer providers, so a generator reaching for `gmail.com` would
fail its own repository's gate. The gate shaping code that did not exist when it
was written is the right direction of influence.
"""

from __future__ import annotations

import random
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Sequence

# Deliberately small vocabularies: the point is duplicate structure, not variety.
# A larger name pool would make blocking trivially effective and the fixture
# useless for exercising the comparison levels.
_FIRST_NAMES = (
    "Robert",
    "Elizabeth",
    "James",
    "Margaret",
    "William",
    "Patricia",
    "Charles",
    "Susan",
    "Thomas",
    "Karen",
    "Richard",
    "Nancy",
    "Joseph",
    "Sandra",
    "Daniel",
    "Ashley",
    "Matthew",
    "Emily",
)

_NICKNAMES = {
    "Robert": ("Rob", "Bob", "Bobby"),
    "Elizabeth": ("Liz", "Beth", "Eliza"),
    "James": ("Jim", "Jamie"),
    "Margaret": ("Maggie", "Meg"),
    "William": ("Will", "Bill", "Billy"),
    "Patricia": ("Pat", "Patty"),
    "Charles": ("Charlie", "Chuck"),
    "Susan": ("Sue", "Susie"),
    "Thomas": ("Tom", "Tommy"),
    "Daniel": ("Dan", "Danny"),
    "Richard": ("Rick", "Dick"),
    "Matthew": ("Matt",),
}

_SURNAMES = (
    "Smith",
    "Jones",
    "Humphrey",
    "Johnson",
    "Sharp",
    "King",
    "Randall",
    "Rivera",
    "Levine",
    "Alan",
    "Allen",
    "Young",
    "Hughes",
    "Baker",
)

_CITIES = ("London", "Leeds", "Bristol", "Cardiff", "Glasgow", "Norwich", "York")

# Matched to the measured rates above. `dob` is absent on purpose.
_MISSING_RATES = {
    "first_name": 0.17,
    "surname": 0.18,
    "city": 0.19,
    "email": 0.21,
}

_DEFAULT_CLUSTERS = 250
_DEFAULT_MAX_CLUSTER = 7

COLUMNS = ("unique_id", "first_name", "surname", "dob", "city", "email", "cluster")


def _typo(rng: random.Random, value: str) -> str:
    """One character substituted, deleted or inserted."""
    if len(value) < 2:
        return value
    index = rng.randrange(len(value))
    mode = rng.choice(("substitute", "delete", "insert"))
    if mode == "delete":
        return value[:index] + value[index + 1 :]
    letter = rng.choice("abcdefghijklmnopqrstuvwxyz")
    if mode == "insert":
        return value[:index] + letter + value[index:]
    return value[:index] + letter + value[index + 1 :]


def _transpose(rng: random.Random, value: str) -> str:
    """Two adjacent characters swapped -- the commonest real typing error."""
    if len(value) < 3:
        return value
    i = rng.randrange(len(value) - 1)
    return value[:i] + value[i + 1] + value[i] + value[i + 2 :]


def _shift_date(rng: random.Random, dob: str) -> str:
    """Shift the date within the same year -- a nearby day or month.

    `fake_1000`'s very first cluster shows exactly this: `1971-06-24` against
    `1971-05-24` for the same person.
    """
    year, month, day = (int(part) for part in dob.split("-"))
    if rng.random() < 0.5:
        month = max(1, min(12, month + rng.choice((-1, 1))))
    else:
        day = max(1, min(28, day + rng.choice((-1, 1))))
    return f"{year:04d}-{month:02d}-{day:02d}"


def _email_for(rng: random.Random, first: str, surname: str, index: int) -> str:
    """Build an address on a surname-derived domain, never a consumer provider (3.55).

    `check_pii_heuristics.py` blocklists `gmail.com` and its peers, so this is
    the shape the repository's own gate permits -- and the shape `fake_1000`
    itself uses.
    """
    local = f"{first.lower()}{index}{rng.randrange(10, 99)}"
    tld = rng.choice(("com", "net", "org"))
    return f"{local}@{surname.lower()}.{tld}"


def _corrupt(rng: random.Random, record: dict[str, Any], first: str) -> None:
    """Apply at most one corruption per attribute, in place."""
    if rng.random() < 0.35 and first in _NICKNAMES:
        record["first_name"] = rng.choice(_NICKNAMES[first])
    elif rng.random() < 0.25:
        record["first_name"] = _typo(rng, record["first_name"])

    if rng.random() < 0.25:
        record["surname"] = (
            _transpose(rng, record["surname"])
            if rng.random() < 0.5
            else _typo(rng, record["surname"])
        )

    if rng.random() < 0.30:
        record["dob"] = _shift_date(rng, record["dob"])

    if rng.random() < 0.20:
        record["city"] = rng.choice(_CITIES)


def generate(
    clusters: int = _DEFAULT_CLUSTERS,
    seed: int = 20260823,
    max_cluster_size: int = _DEFAULT_MAX_CLUSTER,
) -> list[dict[str, Any]]:
    """Return synthetic person records with a ground-truth `cluster` column.

    Deterministic in `seed`: the same seed yields byte-identical output on any
    machine, because every choice goes through one seeded `random.Random` and
    nothing iterates a set or a dict whose order could vary.
    """
    if clusters < 1 or max_cluster_size < 1:
        msg = "clusters and max_cluster_size must both be at least 1"
        raise ValueError(msg)

    rng = random.Random(seed)  # noqa: S311 -- fixtures, not cryptography
    records: list[dict[str, Any]] = []
    next_id = 0

    for cluster in range(clusters):
        first = rng.choice(_FIRST_NAMES)
        surname = rng.choice(_SURNAMES)
        dob = f"{rng.randrange(1940, 2005)}-{rng.randrange(1, 13):02d}-{rng.randrange(1, 29):02d}"
        city = rng.choice(_CITIES)
        email = _email_for(rng, first, surname, cluster)

        for _ in range(rng.randint(1, max_cluster_size)):
            record: dict[str, Any] = {
                "unique_id": f"r{next_id:07d}",
                "first_name": first,
                "surname": surname,
                "dob": dob,
                "city": city,
                "email": email,
                "cluster": f"c{cluster:06d}",
            }
            next_id += 1
            _corrupt(rng, record, first)

            # Missingness LAST, so a corrupted value can still go missing --
            # which is what happens in real data, and what makes the null
            # comparison levels reachable.
            for column, rate in _MISSING_RATES.items():
                if rng.random() < rate:
                    record[column] = None

            records.append(record)

    return records


def to_csv(records: Sequence[dict[str, Any]]) -> str:
    """Render records as CSV, columns in `COLUMNS` order."""
    lines = [",".join(COLUMNS)]
    lines.extend(
        ",".join("" if record[column] is None else str(record[column]) for column in COLUMNS)
        for record in records
    )
    return "\n".join(lines) + "\n"
