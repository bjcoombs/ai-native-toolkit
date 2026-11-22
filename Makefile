# One-command bootstrap and verification for contributors and agents.
#
#   make setup   install the locked dev environments (uv, ripgrep check)
#   make check   run every merge-gating test, lint and type check
#
# `check` runs the commands the required jobs in .github/workflows/tests.yml
# run, so a green `make check` predicts a green CI. When a job there changes,
# change the matching target here. Needs uv (https://docs.astral.sh/uv/).
# docs/windows.md shows each recipe as a make-free command for machines
# without make; tests/test_windows_docs.py fails when the two drift.

.POSIX:
.PHONY: help setup check lock-check test-assess test-scripts test-contract lint catalog regen fold

# The test targets run each suite on every core with pytest-xdist, one test
# file per worker, as CI does. For a serial run to debug a failure:
#   make check XDIST="-p no:xdist"
XDIST = -n auto --dist loadfile

help:
	@echo "make setup          sync the locked dev environments for skills/assess and scripts"
	@echo "make check          run everything below (the required CI checks)"
	@echo "make lock-check     fail if a uv.lock no longer matches its pyproject.toml"
	@echo "make test-assess    skills/assess pytest, with its coverage floor"
	@echo "make test-scripts   scripts/ pytest, with its coverage floor"
	@echo "make test-contract  plugin contract pytest (tests/)"
	@echo "make lint           ruff and mypy gates for both packages"
	@echo "make catalog        regenerate docs/what-we-measure.md and references/measures.json"
	@echo "make regen          regenerate every generated file and golden (run after a conflict in one)"
	@echo "make fold RELEASE=x release PR only: fold skills/assess/changes/ into the counter histories, then regen"

# ripgrep is not a Python dependency: the promissory-marker tests shell out to
# it and degrade without it, so setup warns rather than fails.
setup:
	cd skills/assess && uv sync --locked
	cd scripts && uv sync --locked
	@command -v rg >/dev/null 2>&1 || echo "warning: ripgrep (rg) not on PATH; the promissory-marker tests need it"

check: lock-check test-assess test-scripts test-contract lint

lock-check:
	cd skills/assess && uv lock --check
	cd scripts && uv lock --check

test-assess:
	cd skills/assess && uv run --locked pytest -v $(XDIST) --cov=scripts --cov-report=term --cov-report=xml:coverage.xml
	cd skills/assess && uv run --locked pytest -v -p no:xdist -m cpu_budget --no-cov

test-scripts:
	cd scripts && uv run --locked pytest -v $(XDIST) --cov=. --cov-report=term --cov-report=xml:coverage.xml

test-contract:
	uv run --with pytest --with pyyaml --with "pytest-xdist>=3.6,<4" pytest -v $(XDIST) tests/

# The measures catalog, generated from skills/assess/checks/core.toml (and its fragments), the default
# maturity model, lib/level_signals.py, lib/signals.py (the headless scan list) and
# lib/scan_registry.py (the registry). test-assess fails (tests/test_catalog.py)
# when a committed copy is stale, so run this after changing any of them.
catalog:
	cd skills/assess && uv run --locked python scripts/gen_catalog.py

# Every committed generated file and golden, in dependency order: schemas and
# the catalog read only source; counts-only.md reads the check manifest; the
# fixture builders write their inputs before the goldens rendered from them;
# the pytest-driven goldens come last. A merge conflict in any of these files:
# take either side, then run this (docs/contributing/ci.md). The fleet golden
# needs ripgrep (its test skips without it). tests/test_make_regen.py fails
# when this leaves a diff on a clean tree. Not regenerated, by design: frozen
# oracles written by an earlier implementation or captured from a real run
# (tests/fixtures/structure_golden/, tests/fixtures/golden/run-context-baseline.json,
# tests/fixtures/golden/assess-report-baseline.md, tests/fixtures/fleet_legacy/,
# tests/canaries/).
regen:
	cd skills/assess && uv run --locked python scripts/lib/signals_schema.py
	cd skills/assess && uv run --locked python scripts/lib/fleet_schema.py
	cd skills/assess && uv run --locked python scripts/lib/fleet_level_schema.py
	cd skills/assess && uv run --locked python scripts/lib/fleet_group_schema.py
	cd skills/assess && uv run --locked python scripts/lib/fleet_data_schema.py
	cd skills/assess && uv run --locked python scripts/lib/fleet_achievements_schema.py
	cd skills/assess && uv run --locked python scripts/lib/fleet_labels.py
	cd skills/assess && uv run --locked python scripts/gen_catalog.py
	cd skills/assess && uv run --locked python -c "import sys; sys.path.insert(0, 'scripts'); from lib.counts_only import render_doc; open('references/counts-only.md', 'w', encoding='utf-8', newline='\\n').write(render_doc())"
	cd skills/assess && uv run --locked python tests/fleet_data_fixture.py
	cd skills/assess && uv run --locked python tests/fleet_diagrams_fixture.py
	cd skills/assess && uv run --locked python tests/assess_diagrams_fixture.py
	cd skills/assess && uv run --locked python tests/fleet_trends_fixture.py
	cd skills/assess && uv run --locked python tests/fleet_heatmap_fixture.py
	cd skills/assess && uv run --locked python ../fleet/scripts/heatmap.py tests/fixtures/fleet_heatmap --levels tests/fixtures/fleet_heatmap/levels.toml --output tests/fixtures/fleet_heatmap/expected.svg
	cd skills/assess && uv run --locked python ../fleet/scripts/heatmap.py tests/fixtures/fleet_heatmap --levels tests/fixtures/fleet_heatmap/levels.toml --compare tests/fixtures/fleet_heatmap/earlier --output tests/fixtures/fleet_heatmap/expected-compare.svg
	cd skills/assess && FLEET_GOLDEN_REGEN=1 uv run --locked pytest -q -p no:cacheprovider tests/test_fleet_golden.py
	cd skills/assess && FLEET_NEXT_STEPS_REGEN=1 uv run --locked pytest -q -p no:cacheprovider tests/test_fleet_next_steps.py
	cd skills/assess && PARITY_GOLDEN_REGEN=1 uv run --locked pytest -q -p no:cacheprovider tests/test_decomposition_parity.py

lint:
	cd skills/assess && uv run --locked ruff check .
	cd scripts && uv run --locked ruff check .
	cd skills/assess && uv run --locked mypy
	cd scripts && uv run --locked mypy

# Release PR only (#1011): write the version notes in skills/assess/changes/ into
# each counter's history comment, bump each counter once and delete the notes,
# then regenerate, because schemas, the catalog and goldens stamp the counter
# values. Not part of regen: regen is safe after any conflict, the fold runs once
# per release. FOLD_ARGS passes --date or --set counter=value to fold_changes.py.
fold:
	@test -n "$(RELEASE)" || { echo "usage: make fold RELEASE=3.0.0 [FOLD_ARGS='--set artifact_schema=2.0.0']" >&2; exit 2; }
	cd scripts && uv run --locked python fold_changes.py --release $(RELEASE) $(FOLD_ARGS)
	$(MAKE) regen
