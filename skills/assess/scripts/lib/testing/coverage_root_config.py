"""Does the repository root's coverage.py config govern a scoped run? (issue #1032)

A scoped run of ``coverage_gate`` reads the scope's own configs plus the root CI and
task-runner files; a root package config is dropped. That drops a root
``[tool.coverage]`` (or ``.coveragerc``) floor that still applies: coverage.py reads
its config from the directory it runs in, not from the package it measures, so when CI
runs coverage from the repository root the root config's ``fail_under`` gates the
scope's files too.

``root_config(root, scope, excludes)`` names the root config coverage.py reads (the
first of ``.coveragerc``, then ``setup.cfg`` / ``tox.ini`` with a ``[coverage:...]``
section, then ``pyproject.toml`` with ``[tool.coverage...]``, coverage.py's own order),
or None when it does not concern the scope: the scope holds no ``.py`` file within the
walk's depth (coverage.py measures Python only), or the scope directory holds a
coverage.py config of its own (``coverage_gate`` reads that one, as before).

``root_config_applies(root, config, ci)`` reads where each enforced CI command
that runs coverage.py (``pytest`` with ``--cov``, ``coverage report|xml|json|html|lcov``)
runs, from GitHub workflows and ``.gitlab-ci.yml``:

- the working directory is the repository root, unless the step's
  ``working-directory:`` or a ``cd`` / ``pushd`` earlier in the same step (GitLab: the
  same job, whose script lines share one shell), or on the command's own line before it,
  moves it. A ``cd`` in a ``(...)`` subshell that closes before the command, a GitLab
  ``cd`` in an inherited ``before_script`` (top level, or under ``default:``), and a
  GitLab job with ``extends:`` or a ``<<:`` merge key leave it unresolved. Literal relative paths are resolved (``cd pkg``, ``cd ../..``); anything
  else (``$VAR``, ``~``, an absolute path, ``cd -``, ``popd``, a ``defaults:``
  ``working-directory`` anywhere in the file) leaves it unresolved.
- ``--rcfile`` / ``--cov-config`` names the config instead, resolved against that
  working directory (``.coveragerc`` itself is coverage.py's default, so discovery).
- ``uv --directory X`` and ``poetry -C X`` / ``--directory X`` run the tool in X.
- a plain ``pytest`` is a coverage run when the root config's ``addopts`` holds
  ``--cov``; a run whose coverage state is not settled (``pytest -c`` naming a file not
  read) counts as unresolved unless it is known not to read the root config.
- a GitLab job whose ``before_script`` key sits after the command (GitLab runs it
  first) leaves the directory unresolved; a GitLab ``after_script`` runs in its own shell,
  so its ``cd`` is not read.

True when one run reads the root config (from the root, or by naming it). False when
every run is resolved and none does (each runs in the scope or another directory with
no ``--rcfile`` naming the root config). None otherwise: no coverage run is found, a
run's directory is unresolved, or CI is not fully read (``CiRuns.unread`` or jobs
included from elsewhere). ``coverage_gate`` counts the root gates when true, drops them
when false, and when None reports them in ``gates`` (outside ``configured`` and
``max_threshold_percent``, never credited as run by CI) with ``threshold_meets_minimum`` null and
``unmeasured_reason`` ``ROOT_UNRESOLVED`` wherever they change its value. Known limits:
a job-level ``defaults`` working directory reads unresolved, not resolved; step ``if:``
conditions and matrix values are not read; a run from the root is credited even when
its test paths name another package.

Only runs on ordinary triggers count (``lib.ci_triggers``): a ``workflow_dispatch`` or
``schedule``-only run is skipped, and one whose triggers are not settled reads unresolved.
Runs are read with the repository's pdm scripts (``CiRuns.pdm_scripts``, #1046), so ``pdm run
pytest`` with a ``pytest`` script is the script, not pytest.

Inward-only imports: stdlib, ``lib.testing.coverage_activation``, ``lib.testing.coverage_ci``, ``lib.command_index``, ``lib.testing.ci_workdir`` (the working-directory reading above, #1162),
``lib.docs.doc_graph``, ``lib.assess_config`` and ``lib.runner_args``; never ``lib.testing.coverage_gate`` (which calls it)
or an orchestrator. Tests: ``tests/test_coverage_root_config.py``.
"""
from __future__ import annotations

import os
import re
from collections.abc import Iterator, Sequence
from pathlib import Path, PurePosixPath

from lib.assess_config import is_user_excluded
from lib.command_index import CI_CONFIG_FILES, read_config
from lib.testing.coverage_activation import Activation, read_activation
from lib.runner_args import pdm_scripts_set
from lib.testing.ci_workdir import move, run_cwd
from lib.testing.coverage_ci import CiRuns, Run, runs_tool
from lib.docs.doc_graph import is_excluded_path

# The unmeasured_reason when the root floor may or may not govern the scope.
ROOT_UNRESOLVED = "root_coverage_config_unresolved"

Excludes = tuple[set[str], list[str]]

# coverage.py's own lookup order in the directory it runs in, with the section prefix
# that makes each file a coverage config (None: the file is one by its name alone).
_CONFIGS: tuple[tuple[str, str | None], ...] = (
    (".coveragerc", None), ("setup.cfg", "coverage:"), ("tox.ini", "coverage:"),
    ("pyproject.toml", "tool.coverage"))
_SECTION_RE = re.compile(r"^\s*\[([^\]]+)\]")
_MAX_DEPTH = 4
_CONFIG_FLAGS = ("--rcfile", "--cov-config")
# coverage.py treats this name as its own default: discovery, not a named file.
_DEFAULT_RCFILE = ".coveragerc"
# The root files whose pytest ``addopts`` a run from the root reads.
_PYTEST_CONFIGS = ("pyproject.toml", "pytest.ini", "setup.cfg", "tox.ini")


def _config_in(directory: Path) -> str | None:
    """The coverage.py config file coverage.py reads in ``directory``, or None."""
    for name, prefix in _CONFIGS:
        path = directory / name
        if not path.is_file():
            continue
        if prefix is None:
            return name
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            header = _SECTION_RE.match(line)
            if header and header.group(1).strip().strip("\"'").startswith(prefix):
                return name
    return None


def _holds_python(root: Path, scope: Path, excludes: Excludes) -> bool:
    for dirpath, dirnames, filenames in os.walk(scope):
        rel_dir = Path(dirpath).relative_to(root)
        if len(Path(dirpath).relative_to(scope).parts) >= _MAX_DEPTH:
            dirnames[:] = []
        dirnames[:] = [d for d in dirnames if not is_excluded_path(rel_dir / d)
                       and not is_user_excluded(rel_dir / d, *excludes)]
        if any(n.endswith(".py") and not is_user_excluded(rel_dir / n, *excludes)
               for n in filenames):
            return True
    return False


def root_config(root: Path, scope: Path, excludes: Excludes) -> str | None:
    """The root coverage.py config that may govern ``scope`` (module docstring), or None."""
    config = _config_in(root)
    if (config is None or is_user_excluded(Path(config), *excludes)
            or _config_in(scope) is not None or not _holds_python(root, scope, excludes)):
        return None
    return config


def _named_config(words: Sequence[str]) -> str | None:
    for i, word in enumerate(words):
        for flag in _CONFIG_FLAGS:
            if word == flag and i + 1 < len(words):
                return words[i + 1]
            if word.startswith(flag + "="):
                return word.split("=", 1)[1]
    return None


def _reads_root(run: Run, lines: Sequence[str], gitlab: bool, config: str) -> bool | None:
    cwd = run_cwd(run.span, run.words, lines, gitlab)
    named = _named_config(run.words)
    named = None if named == _DEFAULT_RCFILE else named
    if cwd is None:
        return None
    if named is None:
        return cwd == PurePosixPath()
    target = move(cwd, named)
    return None if target is None else target == PurePosixPath(config)


def _root_activation(root: Path) -> Activation:
    """What a run from the root reads of pytest ``addopts`` (``--cov`` / ``--no-cov``)."""
    files = [(name, read_config(root, name).splitlines())
             for name in _PYTEST_CONFIGS if (root / name).is_file()]
    return read_activation(files)


def _coverage_runs(ci: CiRuns, activation: Activation) -> Iterator[tuple[Run, bool | None]]:
    """The runs of coverage.py, with None where whether coverage is on is not settled."""
    on, off = "" in activation.pytest_cov, "" in activation.pytest_off
    for run in ci.runs:
        if run.trigger == "not_ordinary":
            continue   # a dispatch- or schedule-only run gates no change
        with pdm_scripts_set(ci.pdm_scripts):   # pdm run pytest with a pytest script (#1046)
            state = runs_tool(run.words, "coverage.py", pytest_cov=on, pytest_off=off,
                              activation=activation)
        if state is not False:
            yield run, (None if run.trigger == "unknown" else state)


def root_config_applies(root: Path, config: str, ci: CiRuns) -> bool | None:
    """Does CI run coverage.py where it reads the root ``config``? (module docstring)"""
    states: list[bool | None] = []
    texts: dict[str, list[str]] = {}
    for run, on in _coverage_runs(ci, _root_activation(root)):
        if run.file not in texts:
            texts[run.file] = read_config(root, run.file).splitlines()
        reads = _reads_root(run, texts[run.file], run.file in CI_CONFIG_FILES, config)
        states.append(reads if on else (None if reads is not False else False))
    if True in states:
        return True
    if not states or None in states or ci.unread or ci.elsewhere:
        return None
    return False
