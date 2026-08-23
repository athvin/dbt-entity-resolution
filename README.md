# dbt-er

Splink's entity resolution as declarative pure-SQL dbt models on DuckDB, proven correct by differential
testing against Splink itself.

**Status: scaffolding.** The design is written and the decision register is closed; the models are not
built yet. See [`GOAL.md`](GOAL.md) for where this is going and
[`docs/backlog/LOOP-STATE.md`](docs/backlog/LOOP-STATE.md) for where it currently is.

## The three documents

| File | Owns |
|---|---|
| [`GOAL.md`](GOAL.md) | Why this exists and what "done" looks like. **Non-normative** — it settles nothing, and it is the one that gets edited when it disagrees with either of the others |
| [`docs/DesignDoc.md`](docs/DesignDoc.md) | What the SQL computes: the arithmetic contract, decisions D1–D12, the staged plan, and the decision register at §B.3 |
| [`docs/DbtBestPractices.md`](docs/DbtBestPractices.md) | How the repository stays correct while it changes: the enforcement matrix, the four gates, layout, contracts, linting |

Precedence between them is `DbtBestPractices.md` §1.1. Within `DesignDoc.md`, the body is normative and the
appendices are evidence — with §B.3 carved out by name as normative for decision *status*.

## Running it

```bash
uv sync --all-groups          # Python 3.12; every parity-critical pin is exact
cd integration_tests
dbt deps
dbt build --target ci
```

`integration_tests/` is the runnable project. The repository root is the **package** — what a consumer
installs — and it deliberately refuses to run without being told two things:

- **`er_input_relation`** — a relation expression valid in a `FROM` clause. The package ships zero sources
  (§2.0, DR-16), and it does not default to a name it invented.
- **`er_thresholds`** — a list of `{auto_merge, review_low}` pairs. There is no default, and the reason is
  measured: on this project's own reference corpus the default that used to exist cost ~330 true pairs for
  zero precision benefit (§1.8, DR-22).

The trained Splink model reaches the project through **`DBT_ER_MODEL_JSON`** in the environment, not
`--vars` — `--vars` fails at `MAX_ARG_STRLEN` and is unreachable from `schema.yml` (D1, §9).

## What is unusual about this project

**Splink is the oracle, not the ceiling.** Where Splink has a defect we replicate it *and log it* — including
the `min(match_key)` VARCHAR bug that returns the wrong rule with ≥11 blocking rules. Where Splink leaves
SQL, we improve.

**The model JSON is a trust boundary, not a contract.** It carries rendered SQL that would execute with the
consumer's warehouse credentials, so it is validated against a closed allow-list at compile time, against
the *parsed* tree rather than the raw string (§1.5, DR-17).

**Parity is not quality.** The reference fixture is 100% parity-green at F1 = 0.72. Committed per-fixture F1
and recall floors exist because parity gates cannot see the difference between that and F1 = 0.98
(§1.8, DR-22).

## Licence

MIT — see [`LICENSE`](LICENSE). Splink is MIT, © 2020 Ministry of Justice; this project reimplements its
data transformations and vendors no Splink code.
