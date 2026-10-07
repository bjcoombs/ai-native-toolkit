"""Detect an *enforced* line-coverage threshold in a repo's config - read-only.

`coverage_report.py` answers "is coverage measured, and what is it?". This
module answers the Layer 6 question that measurement cannot: "does the tooling
fail when coverage drops below a number?". A repo that writes ``coverage.xml``
but configures no threshold measures coverage without gating it; a repo with
``fail_under = 85`` gates it. Without this signal the Layer 6 verdict rested on
the scorer noticing a threshold by hand.

Honesty contract: every gate reported carries the file, the 1-based line and the
threshold exactly as written (a JaCoCo ``0.80`` stays ``0.80`` with unit
``ratio``; it is never rescaled or guessed). When nothing is found the block says
``enforced: false`` with an empty ``gates`` list - a gate is never inferred from
coverage *measurement*. ``not_detected`` names the common forms this scan does
not read, so "no gate found" is never misread as "no gate exists".

A configured threshold is evidence the tooling fails below it *when coverage
runs*; whether CI runs coverage at all is a separate read (Layer 5).

Inward-only imports: stdlib plus ``lib.doc_graph``'s path-exclusion rule; never
an orchestrator.
"""
from __future__ import annotations

import os
import re
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

from lib.doc_graph import is_excluded_path

# How deep below the repo root config files are looked for: deep enough for a
# monorepo package (``packages/web/jest.config.js``, ``skills/x/pyproject.toml``),
# shallow enough to stay cheap on a large tree.
_MAX_DEPTH = 4
# Config files are small; anything larger is not a config file worth reading.
_MAX_BYTES = 1_000_000
# Lines after (and, for nyc/c8, around) an anchor key searched for the metric.
_WINDOW = 15

_NUM = r"(-?\d+(?:\.\d+)?)"

# INI-style section that holds coverage.py's ``fail_under``, by file name.
_COVERAGE_PY_SECTIONS: dict[str, str] = {
    "pyproject.toml": "tool.coverage.report",
    ".coveragerc": "report",
    "setup.cfg": "coverage:report",
    "tox.ini": "coverage:report",
}
_SECTION_RE = re.compile(r"^\s*\[([^\]]+)\]\s*$")
_FAIL_UNDER_RE = re.compile(r"^\s*fail_under\s*[=:]\s*[\"']?" + _NUM)

# Command-line forms, read from CI and task-runner files.
_CLI_FILES = {
    "pyproject.toml", "setup.cfg", "tox.ini", "pytest.ini", "noxfile.py",
    "Makefile", "package.json", ".gitlab-ci.yml", ".pre-commit-config.yaml",
}
_PYTEST_COV_RE = re.compile(r"--cov-fail-under[=\s]+[\"']?" + _NUM)
_COVERAGE_CLI_RE = re.compile(r"\bcoverage\s+report\b.*--fail-under[=\s]+" + _NUM)
_CHECK_COVERAGE_CLI_RE = re.compile(r"\b(nyc|c8)\b.*--check-coverage")
_LINES_FLAG_RE = re.compile(r"--lines[=\s]+" + _NUM)

# JS/TS config files and the anchor key that opens a threshold block in each.
_JEST_FILES = re.compile(r"^jest\.config\.(js|ts|mjs|cjs|json)$")
_VITEST_FILES = re.compile(r"^(vitest|vite)\.config\.(js|ts|mjs|cjs|mts|cts)$")
_NYC_FILES = {".nycrc", ".nycrc.json", ".nycrc.yml", ".nycrc.yaml",
              ".c8rc", ".c8rc.json"}
_JEST_ANCHOR = re.compile(r"\bcoverageThreshold\b")
_VITEST_ANCHOR = re.compile(r"\bthresholds[\"']?\s*:")
_CHECK_COVERAGE_KEY = re.compile(r"[\"']?check-coverage[\"']?\s*:\s*true\b")
_METRIC_RE = {
    m: re.compile(r"\b" + m + r"[\"']?\s*:\s*" + _NUM)
    for m in ("lines", "statements", "branches", "functions")
}

# JaCoCo: Maven ``<minimum>`` and Gradle ``minimum = 0.8`` / ``BigDecimal("0.8")``.
_JACOCO_GRADLE = {"build.gradle", "build.gradle.kts"}
_POM_MINIMUM_RE = re.compile(r"<minimum>\s*" + _NUM + r"%?\s*</minimum>")
_POM_COUNTER_RE = re.compile(r"<counter>\s*(\w+)\s*</counter>")
_GRADLE_MINIMUM_RE = re.compile(r"\bminimum\s*=\s*[^0-9\n]{0,20}?" + _NUM)
_GRADLE_COUNTER_RE = re.compile(r"\bcounter\s*=\s*[\"'](\w+)[\"']")

# Common gate forms this scan does not read - reported so an empty result reads
# as "none of the detected forms", never as "no gate anywhere".
NOT_DETECTED: tuple[str, ...] = (
    "Go: `go test -coverprofile` with a script comparing the `go tool cover "
    "-func` total to a number, or go-test-coverage's `.testcoverage.yml`",
    "Codecov / Coveralls status targets (server-side; a gate only when the "
    "status check is required)",
    "Kover `minBound`, sbt-scoverage `coverageMinimumStmtTotal`, "
    "cargo-tarpaulin `--fail-under`, SimpleCov `minimum_coverage`",
    "JaCoCo rules built programmatically or in a shared Gradle convention plugin",
)


def _gate(file: str, line: int, tool: str, form: str,
          threshold: float | None, unit: str, metric: str | None) -> dict[str, Any]:
    return {"file": file, "line": line, "tool": tool, "form": form,
            "threshold": threshold, "unit": unit, "metric": metric}


def _num(text: str) -> float:
    return float(text)


def _is_comment(line: str) -> bool:
    stripped = line.lstrip()
    return stripped.startswith(("#", ";", "//"))


def _scan_fail_under(rel: str, lines: list[str], section: str) -> list[dict]:
    """coverage.py ``fail_under`` inside the named INI/TOML section."""
    gates: list[dict] = []
    current = None
    for idx, line in enumerate(lines, 1):
        header = _SECTION_RE.match(line)
        if header:
            current = header.group(1).strip().strip("\"'")
            continue
        match = _FAIL_UNDER_RE.match(line) if current == section else None
        if match:
            gates.append(_gate(rel, idx, "coverage.py", f"[{section}] fail_under",
                               _num(match.group(1)), "percent", "lines"))
    return gates


def _scan_cli(rel: str, lines: list[str]) -> list[dict]:
    """``--cov-fail-under``, ``coverage report --fail-under`` and nyc/c8 flags."""
    gates: list[dict] = []
    for idx, line in enumerate(lines, 1):
        if _is_comment(line):
            continue
        gates.extend(_cli_line_gates(rel, idx, line))
    return gates


def _cli_line_gates(rel: str, idx: int, line: str) -> list[dict]:
    gates = []
    match = _PYTEST_COV_RE.search(line)
    if match:
        gates.append(_gate(rel, idx, "pytest-cov", "--cov-fail-under",
                           _num(match.group(1)), "percent", "lines"))
    match = _COVERAGE_CLI_RE.search(line)
    if match:
        gates.append(_gate(rel, idx, "coverage.py", "coverage report --fail-under",
                           _num(match.group(1)), "percent", "lines"))
    match = _CHECK_COVERAGE_CLI_RE.search(line)
    if match:
        lines_flag = _LINES_FLAG_RE.search(line)
        gates.append(_gate(rel, idx, match.group(1), "--check-coverage",
                           _num(lines_flag.group(1)) if lines_flag else None,
                           "percent", "lines" if lines_flag else None))
    return gates


def _window_metric(lines: list[str], start: int, end: int
                   ) -> tuple[float | None, str | None]:
    """First ``lines`` value in ``lines[start:end]``, else the first other metric."""
    window = lines[max(start, 0):end]
    for metric, pattern in _METRIC_RE.items():
        for line in window:
            match = pattern.search(line)
            if match:
                return _num(match.group(1)), metric
    return None, None


def _scan_anchor(rel: str, lines: list[str], anchor: re.Pattern[str],
                 tool: str, form: str, before: int = 0) -> list[dict]:
    """A threshold block opened by ``anchor``; the metric is read from a window."""
    gates: list[dict] = []
    for idx, line in enumerate(lines, 1):
        if _is_comment(line) or not anchor.search(line):
            continue
        threshold, metric = _window_metric(lines, idx - 1 - before, idx + _WINDOW)
        unit = "uncovered_count" if threshold is not None and threshold < 0 else "percent"
        gates.append(_gate(rel, idx, tool, form, threshold, unit, metric))
    return gates


def _scan_package_json(rel: str, lines: list[str]) -> list[dict]:
    """Jest ``coverageThreshold``, nyc/c8 ``check-coverage`` and script flags."""
    return (_scan_anchor(rel, lines, _JEST_ANCHOR, "jest", "coverageThreshold")
            + _scan_anchor(rel, lines, _CHECK_COVERAGE_KEY, "nyc/c8",
                           "check-coverage", before=_WINDOW)
            + _scan_cli(rel, lines))


def _nearest_counter(lines: list[str], idx: int, pattern: re.Pattern[str]) -> str | None:
    """The JaCoCo counter (LINE, BRANCH, ...) declared nearest above line ``idx``."""
    for line in reversed(lines[max(idx - _WINDOW, 0):idx]):
        match = pattern.search(line)
        if match:
            return match.group(1).lower()
    return None


def _scan_jacoco(rel: str, lines: list[str], minimum: re.Pattern[str],
                 counter: re.Pattern[str], form: str) -> list[dict]:
    """JaCoCo ``minimum`` values in a build file that applies JaCoCo."""
    if not any("jacoco" in line.lower() for line in lines):
        return []
    gates: list[dict] = []
    for idx, line in enumerate(lines, 1):
        match = None if _is_comment(line) else minimum.search(line)
        if match:
            value = _num(match.group(1))
            unit = "ratio" if value <= 1 else "percent"
            gates.append(_gate(rel, idx, "jacoco", form, value, unit,
                               _nearest_counter(lines, idx, counter)))
    return gates


def _scanners_for(name: str, rel_parts: tuple[str, ...]
                  ) -> list[Callable[[str, list[str]], list[dict]]]:
    """The scanners that apply to a file, by its name and location."""
    scanners: list[Callable[[str, list[str]], list[dict]]] = []
    if name in _COVERAGE_PY_SECTIONS:
        section = _COVERAGE_PY_SECTIONS[name]
        scanners.append(lambda r, ls: _scan_fail_under(r, ls, section))
    if name == "package.json":
        scanners.append(_scan_package_json)
    elif name in _CLI_FILES or _is_workflow(name, rel_parts):
        scanners.append(_scan_cli)
    scanners.extend(_js_jvm_scanners(name))
    return scanners


def _js_jvm_scanners(name: str) -> list[Callable[[str, list[str]], list[dict]]]:
    if _JEST_FILES.match(name):
        return [lambda r, ls: _scan_anchor(r, ls, _JEST_ANCHOR, "jest",
                                           "coverageThreshold")]
    if _VITEST_FILES.match(name):
        return [lambda r, ls: _scan_anchor(r, ls, _VITEST_ANCHOR, "vitest",
                                           "coverage.thresholds")]
    if name in _NYC_FILES:
        tool = "c8" if name.startswith(".c8") else "nyc"
        return [lambda r, ls: _scan_anchor(r, ls, _CHECK_COVERAGE_KEY, tool,
                                           "check-coverage", before=_WINDOW)]
    if name == "pom.xml":
        return [lambda r, ls: _scan_jacoco(r, ls, _POM_MINIMUM_RE,
                                           _POM_COUNTER_RE, "<rules> <minimum>")]
    if name in _JACOCO_GRADLE:
        return [lambda r, ls: _scan_jacoco(r, ls, _GRADLE_MINIMUM_RE,
                                           _GRADLE_COUNTER_RE,
                                           "jacocoTestCoverageVerification minimum")]
    return []


def _is_workflow(name: str, rel_parts: tuple[str, ...]) -> bool:
    return (rel_parts[:2] == (".github", "workflows")
            and name.endswith((".yml", ".yaml")))


def _candidate_files(root: Path) -> Iterator[tuple[Path, str]]:
    """Files under ``root`` (bounded depth, built-in excludes) with a scanner."""
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = Path(dirpath).relative_to(root)
        if len(rel_dir.parts) >= _MAX_DEPTH:
            dirnames[:] = []
        dirnames[:] = sorted(d for d in dirnames
                             if not is_excluded_path(rel_dir / d))
        for name in sorted(filenames):
            rel = rel_dir / name
            if _scanners_for(name, rel.parts):
                yield root / rel, rel.as_posix()


def _read_lines(path: Path) -> list[str] | None:
    try:
        if path.stat().st_size > _MAX_BYTES:
            return None
        return path.read_text(encoding="utf-8", errors="replace").splitlines()
    except (OSError, ValueError):
        return None


def detect_coverage_gate(repo_root: Path | str) -> dict[str, Any]:
    """Return the ``coverage_gate`` run-context block for ``repo_root``.

    ``{"available": True, "enforced": bool, "gates": [...], "not_detected": [...]}``
    where each gate is ``{file, line, tool, form, threshold, unit, metric}``.
    ``enforced`` is true when at least one gate has a non-zero threshold (or a
    tool-default one, ``threshold: null``); a ``fail_under = 0`` is reported but
    gates nothing.
    """
    root = Path(repo_root)
    gates: list[dict] = []
    for path, rel in _candidate_files(root):
        lines = _read_lines(path)
        if lines is None:
            continue
        for scan in _scanners_for(path.name, Path(rel).parts):
            gates.extend(scan(rel, lines))
    return {
        "available": True,
        "enforced": any(g["threshold"] != 0 for g in gates),
        "gates": gates,
        "not_detected": list(NOT_DETECTED),
    }
