"""What turns a coverage tool's check on when CI runs it, read per directory (#954).

``coverage_gate`` reads the thresholds a repository configures; ``coverage_ci`` asks
whether an enforced CI line runs the tool that reads one. A plain ``pytest`` or
``jest`` run checks a config threshold only when coverage is switched on in the
config that run reads. ``read_activation(files)`` takes ``(repo-relative path, lines)``
for each candidate config file and returns an ``Activation``:

- ``pytest_cov``: the directories whose pytest config section (``[pytest]``,
  ``[tool:pytest]``, ``[tool.pytest.ini_options]`` in ``pyproject.toml``,
  ``pytest.ini``, ``setup.cfg`` or ``tox.ini``) holds an ``addopts`` with ``--cov`` or
  ``--cov=<source>`` (on its own line, in a TOML list or on INI continuation lines).
- ``pytest_off``: the directories whose pytest ``addopts`` holds ``--no-cov``, which
  turns pytest-cov off whatever its position, a ``--cov`` on the command line included
  (pytest-cov 7.1; ``--no-cov-on-fail`` is not that flag).
- ``jest_collects``: the directories of a file holding ``collectCoverage: true``,
  read with JavaScript comments stripped.
- ``gradle_check``: a ``build.gradle`` / ``build.gradle.kts`` wires
  ``check.dependsOn jacocoTestCoverageVerification`` (still read once per repository).
- ``pytest_lines``: the ``(file, line)`` pairs inside a pytest config section, where a
  flag is ``addopts``, not a ``tox.ini`` ``commands`` line.

``covers(dirs, file)`` is the directory rule ``coverage_ci`` applies: a config gate is
switched on by an activation in its own directory or a directory above it (the config
pytest or Jest finds walking up from the package), never by a sibling package's.
The root directory is ``""``.

Split out of ``coverage_gate`` (#954), which calls it. Inward-only imports: stdlib
only; never ``lib.coverage_gate``, ``lib.coverage_ci`` or an orchestrator.
Tests: ``tests/test_coverage_ci_edges.py``.
"""
from __future__ import annotations

import posixpath
import re
from collections.abc import Iterable
from dataclasses import dataclass

_SECTION_RE = re.compile(r"^\s*\[([^\]]+)\]\s*(?:[#;].*)?$")
# pytest ``addopts`` holding a ``--cov`` flag: a plain ``pytest`` runs coverage.
_ADDOPTS_RE = re.compile(r"^\s*addopts\s*=(.*)$")
# pytest-cov's enabling flag: ``--cov`` or ``--cov=src``, not ``--cov-report``.
_COV_FLAG_RE = re.compile(r"--cov(?![\w-])")
# pytest-cov's disabling flag, not ``--no-cov-on-fail``.
_NO_COV_FLAG_RE = re.compile(r"--no-cov(?![\w-])")
_JS_COMMENT_RE = re.compile(r"/\*.*?\*/|(?<![:\"'])//[^\n]*", re.S)
PYTEST_CONFIG_FILES = frozenset({"pyproject.toml", "pytest.ini", "setup.cfg", "tox.ini"})
_PYTEST_SECTIONS = frozenset({"pytest", "tool:pytest", "tool.pytest.ini_options"})
_JACOCO_GRADLE = frozenset({"build.gradle", "build.gradle.kts"})
# ``check.dependsOn jacocoTestCoverageVerification`` (Groovy or Kotlin DSL, a
# ``tasks.check { dependsOn(...) }`` block): ``gradle check`` / ``build`` runs the gate.
_GRADLE_CHECK_WIRED_RE = re.compile(
    r"\bcheck\b[^\n{]*(?:\{[^}]*)?\bdependsOn\b[^\n]*jacocoTestCoverageVerification")
_JEST_COLLECTS_RE = re.compile(r"[\"']?collectCoverage[\"']?\s*:\s*true\b")


@dataclass(frozen=True)
class Activation:
    """Where coverage is switched on for a plain run of the tool, by config directory."""

    pytest_cov: frozenset[str] = frozenset()
    pytest_off: frozenset[str] = frozenset()
    jest_collects: frozenset[str] = frozenset()
    gradle_check: bool = False
    pytest_lines: frozenset[tuple[str, int]] = frozenset()


def covers(dirs: frozenset[str], file: str) -> bool:
    """A config activation in ``file``'s directory or one above it (``""`` is the root)."""
    here = posixpath.dirname(file)
    return any(d == "" or here == d or here.startswith(d + "/") for d in dirs)


def read_activation(files: Iterable[tuple[str, list[str]]]) -> Activation:
    """The ``Activation`` of the candidate config files, ``(repo-relative path, lines)``."""
    pytest_cov: set[str] = set()
    pytest_off: set[str] = set()
    jest: set[str] = set()
    gradle = False
    pytest_lines: set[tuple[str, int]] = set()
    for rel, lines in files:
        name = posixpath.basename(rel)
        if name in PYTEST_CONFIG_FILES:
            pytest_lines.update((rel, n) for n in section_lines(lines, _PYTEST_SECTIONS))
            state = addopts_cov(lines)
            if state is not False:
                (pytest_cov if state else pytest_off).add(posixpath.dirname(rel))
        source = _JS_COMMENT_RE.sub("", "\n".join(lines))
        gradle = gradle or (name in _JACOCO_GRADLE and bool(_GRADLE_CHECK_WIRED_RE.search(source)))
        if _JEST_COLLECTS_RE.search(source):
            jest.add(posixpath.dirname(rel))
    return Activation(frozenset(pytest_cov), frozenset(pytest_off), frozenset(jest), gradle,
                      frozenset(pytest_lines))


def addopts_cov(lines: list[str]) -> bool | None:
    """True when a pytest ``addopts`` in a pytest config section holds a ``--cov`` flag,
    on its own line, in a TOML list (``addopts = [`` ... ``]``) or on INI continuation
    lines; None when it holds ``--no-cov`` (coverage off); False when it holds neither."""
    section = set(section_lines(lines, _PYTEST_SECTIONS))
    values: list[str] = []
    for idx, line in enumerate(lines, 1):
        match = _ADDOPTS_RE.match(line) if idx in section else None
        if match is None:
            continue
        values.append(match.group(1))
        listed = match.group(1).strip().startswith("[") and "]" not in match.group(1)
        for nxt in lines[idx:]:
            if listed:
                values.append(nxt)
                if "]" in nxt:
                    break
            elif nxt[:1].isspace() and nxt.strip():
                values.append(nxt)
            else:
                break
    if any(_NO_COV_FLAG_RE.search(v) for v in values):
        return None
    return any(_COV_FLAG_RE.search(v) for v in values)


def section_lines(lines: list[str], sections: frozenset[str]) -> list[int]:
    """1-based lines inside any of the named INI/TOML sections."""
    out: list[int] = []
    current = None
    for idx, line in enumerate(lines, 1):
        header = _SECTION_RE.match(line)
        if header:
            current = header.group(1).strip().strip("\"'")
        elif current in sections:
            out.append(idx)
    return out

