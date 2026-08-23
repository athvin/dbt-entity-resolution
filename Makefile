# dbt-er
#
# Section 17: EVERY TARGET HERE IS ALSO A CI STEP, and every CI step is also a
# target here. A gate that exists only in CI does not exist, because nobody can
# run it before pushing; a target that exists only here is a gate CI does not
# enforce.
#
# Appendix D.1 records the bootstrap order. Targets whose tooling has not landed
# yet SAY SO and exit non-zero. They do not pass quietly -- a green target that
# checked nothing is the single worst outcome a gate can produce, and it is the
# vacuity section 12.7 is written about.

.DEFAULT_GOAL := help
SHELL := /bin/bash
.SHELLFLAGS := -eu -o pipefail -c

# ---------------------------------------------------------------------------
# 3.58 / section 22.2: determinism is PINNED, not assumed. These are exported to
# every recipe, and the determinism job ASSERTS them rather than trusting that
# this file was used.
# ---------------------------------------------------------------------------
export PYTHONHASHSEED := 0
export TZ            := UTC
export LC_ALL        := C

export DBT_PROFILES_DIR := $(CURDIR)/profiles
DBT ?= uv run dbt
IT  := --project-dir integration_tests

# The package deliberately has no default threshold (DesignDoc 1.8, DR-22): the
# one that used to exist measurably cost ~330 true pairs for zero precision
# benefit. integration_tests/ supplies its own, so only package-root invocations
# -- which is to say the linter -- need this.
#
# A JSON STRING, supplied through the environment. sqlfluff has no --vars, and
# dbt renders a Jinja-bearing vars: value to a string, so this is the only route
# that both dbt and the linter can see. Same mechanism as DBT_ER_MODEL_JSON.
export DBT_ER_THRESHOLDS ?= [{"auto_merge": 0.9}]

# Section 22.1: float-exact gates are anchored to linux/amd64. On anything else
# they are ADVISORY, and must say so rather than appearing to pass.
UNAME_M := $(shell uname -m)
IS_AMD64 := $(filter x86_64 amd64,$(UNAME_M))

.PHONY: help install lint build docs bouncer ci capacity baseline \
        clean platform-note python-tests precommit verify-gates

help:
	@echo "dbt-er targets (section 17 -- each is also a CI step)"
	@echo
	@echo "  install    uv sync --locked + pre-commit install"
	@echo "  lint       sqlfluff + yamllint + ruff + mypy + the repo checks"
	@echo "  build      dbt seed && dbt build --full-refresh (unit + data tests)"
	@echo "  docs       catalog.json, for the bouncer catalog tier"
	@echo "  bouncer    all three artifact tiers"
	@echo "  python-tests  pytest over scripts/ and dbt_bouncer_checks/ (3.57)"
	@echo "  verify-gates  prove each standard FAILS when violated (3.38)"
	@echo "  comparator-sensitivity  no mutant survives (3.46) -- NEVER auto-rerun"
	@echo "  precommit  every pre-commit hook, over all files"
	@echo "  ci         everything CI runs, in CI's order"
	@echo "  capacity   measured bytes/pair -> er_max_pairs (D11 rec 5)"
	@echo "  baseline   regenerate Splink baselines AND their manifests"
	@echo
	@echo "Not yet implemented targets exit non-zero and name what is missing."

install:
	uv sync --locked --all-groups
	@if [ -f .pre-commit-config.yaml ]; then \
		uv run pre-commit install; \
	else \
		echo "NOTE: .pre-commit-config.yaml not present yet (D.1 step 3)."; \
	fi
	$(DBT) deps
	$(DBT) deps $(IT)

platform-note:
	@if [ -z "$(IS_AMD64)" ]; then \
		echo ""; \
		echo "  ** PLATFORM NOTE ($(UNAME_M)) **"; \
		echo "  Float-exact parity and determinism gates are anchored to"; \
		echo "  linux/amd64 (section 22.1, 3.59). On this machine they are"; \
		echo "  ADVISORY. A local green does NOT stand in for the CI result."; \
		echo "  Set-equality, partition-equality, integer/string and row-count"; \
		echo "  comparisons are platform-independent and do stand."; \
		echo ""; \
	fi

# ---------------------------------------------------------------------------
lint: platform-note
	uv run yamllint --strict -c .yamllint.yml .
	uv run ruff check .
	uv run ruff format --check .
	@if compgen -G "scripts/*.py" > /dev/null || compgen -G "harness/*.py" > /dev/null; then \
		uv run mypy; \
	else \
		echo "mypy: no Python sources yet -- skipping, and saying so."; \
	fi
	$(DBT) parse
	$(DBT) parse $(IT)
	@# `dbt parse` does NOT execute on-run-start hooks, so it does not fire the
	@# compile gate -- despite section 2 calling that gate "compile". C.7 carries
	@# an explicit run-operation step for exactly this reason, and lint needs the
	@# same one or the only gate that travels with the package goes unrun until
	@# `make build`.
	$(DBT) run-operation er_assert_project_standards $(IT)
	@# Section 17 writes this as `sqlfluff lint models tests`, but sqlfluff
	@# errors on a path that does not exist and `tests/` has no singular tests
	@# yet. Named explicitly rather than mkdir'd into existence: an empty
	@# directory that exists only to satisfy a command is a subject the gate
	@# cannot actually check.
	@paths="models"; \
	if [ -d tests ] && compgen -G "tests/*.sql" > /dev/null; then \
		paths="$$paths tests"; \
	else \
		echo "  note: tests/ has no singular tests yet -- linting models only"; \
	fi; \
	uv run sqlfluff lint $$paths
	@$(MAKE) --no-print-directory repo-checks

# The four things no off-the-shelf tool does (section 4). Each arrives with its
# verify_gates.py injection in D.1 step 4; until then this target reports which
# are missing instead of pretending the set is complete.
.PHONY: repo-checks
repo-checks:
	@missing=0; \
	for s in check_yml_pairing check_no_nondeterminism check_flags_parity \
	         check_root_packages_minimal check_workflow_hardening \
	         check_standards_matrix check_verified_markers \
	         check_divergence_log check_baseline_manifests \
	         check_unit_test_fixtures check_canonical_homes \
	         check_pii_heuristics; do \
		if [ -f "scripts/$$s.py" ]; then \
			echo "  running scripts/$$s.py"; \
			uv run python "scripts/$$s.py"; \
		else \
			echo "  MISSING scripts/$$s.py (D.1 step 3/4)"; \
			missing=$$((missing+1)); \
		fi; \
	done; \
	if [ "$$missing" -gt 0 ]; then \
		echo "repo-checks: $$missing of 12 enforcement scripts are not written yet."; \
		echo "Waiver B-1 (Appendix D.1) covers the bootstrap interval."; \
	fi

# 3.57 / section 15's `python-tests` job. Every enforcement script ships a
# FAILING-case test: section 0 is explicit that "observed to pass" is never
# "observed to fail when violated", and only the second shows a gate enforces
# anything.
# 3.46 / section 12.7. NEVER auto-rerun this: it is one of the three never-retry
# gates, and a surviving mutant means the comparator cannot detect a real
# divergence -- re-running only loses the evidence of which one survived.
comparator-sensitivity:
	@echo "== comparator sensitivity (3.46) -- no mutant may survive =="
	uv run pytest harness -q

python-tests:
	uv run pytest tests_python -q

# 3.38 / section 15's `verify-gates` job. Injects each violation in a scratch
# copy and asserts a non-zero exit AND the expected error string. Section 0: a
# standard that has never been observed to FAIL is not known to be enforced.
verify-gates:
	uv run python scripts/verify_gates.py

precommit:
	@# SKIP=no-commit-to-branch: the hook checks the CURRENT branch, so it fails
	@# whenever this target runs on main -- including in CI on a push. It guards
	@# a developer's `git commit`, which is where it stays armed.
	SKIP=no-commit-to-branch uv run pre-commit run --all-files

# ---------------------------------------------------------------------------
build:
	@if compgen -G "integration_tests/seeds/*.csv" > /dev/null; then \
		$(DBT) seed $(IT) --target ci; \
	else \
		echo "dbt seed: no seeds yet (Stage 0.2) -- skipping, and saying so."; \
	fi
	$(DBT) build $(IT) --target ci --empty --fail-fast
	$(DBT) build $(IT) --target ci --full-refresh --fail-fast

docs:
	$(DBT) docs generate $(IT) --target ci

bouncer:
	@if [ -f dbt-bouncer.yml ]; then \
		uv run dbt-bouncer run --config-file dbt-bouncer.yml \
			--output-file target/bouncer.json --output-format json; \
		uv run python scripts/check_bouncer_ran.py target/bouncer.json; \
	else \
		echo "dbt-bouncer.yml does not exist yet (D.1 step 4, and C.5 -- its"; \
		echo "text was lost with the deleted scaffold and must be"; \
		echo "reconstructed, per RC50)."; \
		exit 1; \
	fi

capacity:
	@echo "make capacity is DesignDoc Stage 0.6 (D11 rec 5): measure bytes/pair"
	@echo "for the fixture model and derive er_max_pairs from it. The current"
	@echo "value, 42000000, came from the WIDE 946 B/pair shape D11 superseded"
	@echo "and under-provisions the narrow one by roughly 10x."
	@exit 1

baseline:
	@echo "make baseline is DesignDoc Stage 0.3: gen_baseline.py dumping every"
	@echo "intermediate as parquet with a provenance manifest (3.62). Section"
	@echo "20.1: regeneration happens ONLY through this target, never by hand,"
	@echo "because the target is what writes the manifest."
	@exit 1

# ---------------------------------------------------------------------------
# Everything CI runs, in CI's order. Section 15's ordering constraints are not
# arbitrary: `dbt docs generate` must precede the catalog checks because they
# need a catalog built against a real database, and the parity harness must run
# after dbt has EXITED because DuckDB takes a process-level lock.
ci: lint python-tests comparator-sensitivity verify-gates build docs bouncer

clean:
	$(DBT) clean || true
	$(DBT) clean $(IT) || true
	rm -rf target integration_tests/target exports .duckdb_tmp
