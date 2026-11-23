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

Inward-only imports: stdlib, ``lib.testing.coverage_activation``, ``lib.testing.coverage_ci``, ``lib.command_index``,
``lib.docs.doc_graph``, ``lib.assess_config`` and ``lib.runner_args``; never ``lib.testing.coverage_gate`` (which calls it)
or an orchestrator. Tests: ``tests/test_coverage_root_config.py``.
"""
from __future__ import annotations

import os
import re
from collections.abc import Iterator, Sequence
from pathlib import Path, PurePosixPath

from lib.assess_config import is_user_excluded
from lib.command_index import CI_CONFIG_FILES, ci_line_text, read_config
from lib.testing.coverage_activation import Activation, read_activation
from lib.runner_args import pdm_scripts_set
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
# Runner flags that run the command in another directory.
_DIRECTORY_FLAGS = {"uv": ("--directory",), "poetry": ("-C", "--directory")}
# The root files whose pytest ``addopts`` a run from the root reads.
_PYTEST_CONFIGS = ("pyproject.toml", "pytest.ini", "setup.cfg", "tox.ini")
# ``cd`` at the start of a command: the line's start, or after ``;``, ``&&``, ``||``, ``|``, ``(``.
_CD_RE = re.compile(r"(?:^|[;&|(])\s*(cd|pushd|popd)(?=\s|$|[;&|)])\s*([^\s;&|)]*)")
_WORKDIR_RE = re.compile(r"^(\s*)(?:-\s+)?working-directory\s*:\s*(.*?)\s*(?:#.*)?$")
_STEP_RE = re.compile(r"^(\s*)-\s+")
# A path step: "" is the repository root; None is unresolved.
Cwd = PurePosixPath | None


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


def _step_lines(lines: Sequence[str], row: int, gitlab: bool) -> range:
    """The lines of the step (GitLab: the job) holding ``lines[row]``."""
    if gitlab:
        start = next((r for r in range(row, -1, -1)
                      if lines[r][:1] not in ("", " ", "\t", "#")), 0)
        end = next((r for r in range(row + 1, len(lines))
                    if lines[r][:1] not in ("", " ", "\t", "#")), len(lines))
        return range(start, end)
    indent = len(lines[row]) - len(lines[row].lstrip())
    found = next(((r, m) for r in range(row, -1, -1) if (m := _STEP_RE.match(lines[r]))
                  and (r == row or len(m.group(1)) < indent)), None)
    if found is None:
        return range(row, row + 1)
    start, dash = found[0], len(found[1].group(1))
    end = next((r for r in range(start + 1, len(lines))
                if lines[r].strip() and len(lines[r]) - len(lines[r].lstrip()) <= dash),
               len(lines))
    return range(start, end)


def _move(cwd: Cwd, target: str | None) -> Cwd:
    """``cwd`` after ``cd target``: literal relative paths only, never above the root."""
    if cwd is None or not target or target == "-" or target[0] in "/~$\"'`" or "$" in target:
        return None
    parts: list[str] = list(cwd.parts)
    for part in PurePosixPath(target).parts:
        if part == "..":
            if not parts:
                return None
            parts.pop()
        elif part != ".":
            parts.append(part)
    return PurePosixPath(*parts) if parts else PurePosixPath()


def _defaults_workdir(lines: Sequence[str]) -> bool:
    """A ``working-directory`` under a ``defaults:`` block (job or workflow level)."""
    for row, line in enumerate(lines):
        match = _WORKDIR_RE.match(line)
        if match is None or line.lstrip().startswith("-"):
            continue
        indent = len(match.group(1))
        parents = [lines[r].strip() for r in range(row - 1, -1, -1)
                   if lines[r].strip() and not lines[r].strip().startswith("#")
                   and len(lines[r]) - len(lines[r].lstrip()) < indent]
        if parents[:2] == ["run:", "defaults:"]:
            return True
    return False


def _inherited_cd(lines: Sequence[str]) -> bool:
    """A GitLab ``before_script`` at the top level or under ``default:`` holding a ``cd``:
    it runs in the same shell as every job's ``script``."""
    parent = ""
    for row, line in enumerate(lines):
        indent = len(line) - len(line.lstrip())
        if line.strip() and indent == 0 and not line.startswith("#"):
            parent = line.split(":", 1)[0].strip()
        key = line.strip().split(":", 1)[0]
        if key != "before_script" or not (indent == 0 or parent == "default"):
            continue
        texts = [ci_line_text(line.split(":", 1)[1])]
        for text in lines[row + 1:]:
            outdent = len(text) - len(text.lstrip())
            # A YAML list may sit at its key's own indent (``before_script:`` then ``- cd``).
            if text.strip() and (outdent < indent or outdent == indent
                                 and not text.lstrip().startswith("-")):
                break
            texts.append(ci_line_text(text))
        if any(_CD_RE.search(t.strip("[]")) for t in texts):
            return True
    return False


def _cwd(run: Run, lines: Sequence[str], gitlab: bool) -> Cwd:
    """The directory ``run`` runs in, relative to the root, or None when unresolved."""
    row = run.span[0]
    step = _step_lines(lines, row, gitlab)
    cwd: Cwd = PurePosixPath()
    # GitLab: a ``before_script`` inherited from the top level, ``default:``, an
    # ``extends:`` template or a ``<<:`` merge key may move the job; not followed.
    if gitlab and (_inherited_cd(lines) or any(
            lines[r].strip().startswith(("extends:", "<<:")) for r in step)):
        return None
    # GitLab runs a job's ``before_script`` first, wherever the key sits in the job.
    if gitlab and any(lines[r].strip().startswith("before_script:")
                      for r in step if r > run.span[-1]):
        return None
    if not gitlab:
        if _defaults_workdir(lines):
            return None
        dash = _STEP_RE.match(lines[step.start])   # step keys sit where the first key does
        key_indent = len(dash.group(0)) if dash else -1
        for r in step:
            match = _WORKDIR_RE.match(lines[r])
            if match and (r == step.start or len(match.group(1)) == key_indent):
                cwd = _move(cwd, match.group(2).strip("\"'") or None)
    after = _after_script_rows(lines, step) if gitlab else set()
    for r in range(step.start, run.span[-1] + 1):
        if r in after:
            continue   # GitLab runs ``after_script`` in its own shell, after ``script``
        text = ci_line_text(lines[r])
        if r in run.span:
            word = re.search(r"(?<![\w./-])" + re.escape(run.words[0]) + r"(?![\w./-])", text)
            at = word.start() if word else -1
            text = text[:at] if at >= 0 else text
        for match in _CD_RE.finditer(text):
            if match.group(1) == "popd" or _in_closed_group(text, match.start(1)):
                return None   # a subshell ``(cd docs && make)`` does not move the run
            cwd = _move(cwd, match.group(2))
    return _runner_directory(cwd, run.words)


def _runner_directory(cwd: Cwd, words: Sequence[str]) -> Cwd:
    """``cwd`` after a ``uv --directory X`` / ``poetry -C X`` lead, which runs the tool in X."""
    if not words or words[0] not in _DIRECTORY_FLAGS:
        return cwd
    flags = _DIRECTORY_FLAGS[words[0]]
    for i, word in enumerate(words[1:], 1):
        if word in flags:
            return _move(cwd, words[i + 1] if i + 1 < len(words) else None)
        if any(word.startswith(f + "=") for f in flags if f.startswith("--")):
            return _move(cwd, word.split("=", 1)[1])
    return cwd


def _after_script_rows(lines: Sequence[str], step: range) -> set[int]:
    """The rows of a GitLab job's ``after_script:`` block (key line included)."""
    rows: set[int] = set()
    key: int | None = None
    for r in step:
        line = lines[r]
        indent = len(line) - len(line.lstrip())
        if key is not None and line.strip() and (
                indent < key or indent == key and not line.lstrip().startswith("-")):
            key = None
        if key is None and line.strip().startswith("after_script:"):
            key = indent
        if key is not None:
            rows.add(r)
    return rows


def _in_closed_group(text: str, at: int) -> bool:
    """``text[at]`` sits inside a ``(...)`` group that closes within ``text``: the run,
    which is not before it in that text, is outside the group."""
    depth = text[:at].count("(") - text[:at].count(")")
    if depth <= 0:
        return False
    for char in text[at:]:
        depth += {"(": 1, ")": -1}.get(char, 0)
        if depth == 0:
            return True
    return False


def _named_config(words: Sequence[str]) -> str | None:
    for i, word in enumerate(words):
        for flag in _CONFIG_FLAGS:
            if word == flag and i + 1 < len(words):
                return words[i + 1]
            if word.startswith(flag + "="):
                return word.split("=", 1)[1]
    return None


def _reads_root(run: Run, lines: Sequence[str], gitlab: bool, config: str) -> bool | None:
    cwd = _cwd(run, lines, gitlab)
    named = _named_config(run.words)
    named = None if named == _DEFAULT_RCFILE else named
    if cwd is None:
        return None
    if named is None:
        return cwd == PurePosixPath()
    target = _move(cwd, named)
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
