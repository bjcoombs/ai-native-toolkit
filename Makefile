# One-command bootstrap and verification for contributors and agents.
#
#   make setup   install the locked dev environments (uv, ripgrep check)
#   make check   run every merge-gating test, lint and type check
#
# `check` runs the commands the required jobs in .github/workflows/tests.yml
# run, so a green `make check` predicts a green CI. When a job there changes,
# change the matching target here. Needs uv (https://docs.astral.sh/uv/).

.POSIX:
.PHONY: help setup check lock-check test-assess test-scripts test-contract lint

help:
	@echo "make setup          sync the locked dev environments for skills/assess and scripts"
	@echo "make check          run everything below (the required CI checks)"
	@echo "make lock-check     fail if a uv.lock no longer matches its pyproject.toml"
	@echo "make test-assess    skills/assess pytest, with its coverage floor"
	@echo "make test-scripts   scripts/ pytest, with its coverage floor"
	@echo "make test-contract  plugin contract pytest (tests/)"
	@echo "make lint           ruff and mypy gates for both packages"

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
	cd skills/assess && uv run --locked pytest -v --cov=scripts --cov-report=term --cov-report=xml:coverage.xml

test-scripts:
	cd scripts && uv run --locked pytest -v --cov=. --cov-report=term --cov-report=xml:coverage.xml

test-contract:
	uv run --with pytest --with pyyaml pytest -v tests/

lint:
	cd skills/assess && uv run --locked ruff check .
	cd scripts && uv run --locked ruff check .
	cd skills/assess && uv run --locked mypy
	cd scripts && uv run --locked mypy
