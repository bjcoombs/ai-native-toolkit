"""Detect a line-coverage threshold *configured* in a repo's config - read-only.

`coverage_report.py` answers "is coverage measured, and what is it?". This
module answers the Layer 6 question that measurement cannot: "does the tooling
fail when coverage drops below a number?". A repo that writes ``coverage.xml``
but configures no threshold measures coverage without gating it; a repo with
``fail_under = 85`` gates it. Without this signal the Layer 6 verdict rested on
the scorer noticing a threshold by hand.

Honesty contract: every gate reported carries the file, the 1-based line and the
threshold exactly as written (a JaCoCo ``0.80`` stays ``0.80`` with unit
``ratio``; ``threshold`` is never rescaled or guessed). Beside it,
``threshold_percent`` is the same number as a percentage (``80.0`` for that
ratio), derived from ``unit`` alone, and null when it is not a percentage of
coverage (an uncovered-line count) or was not read (a template, a tool default).
When nothing is found the block says ``configured: false`` with an empty ``gates``
list - a gate is never inferred from coverage *measurement*.

``threshold_meets_minimum`` is the Layer 6 level 1 signal (#781): true when a
counted gate's ``threshold_percent`` is at least ``MINIMUM_THRESHOLD_PERCENT``
(50), null when none is but a counted gate's percentage was not read (it could
be 50 or more), false otherwise. It is read from config at HEAD only, never
from measured coverage, so the level stays a function of the commit. ``not_detected`` names the common forms this scan does
not read, so "no gate found" is never misread as "no gate exists".

Go and Rust (#788): go-test-coverage's ``.testcoverage.yml`` ``threshold:``
keys, ``cargo tarpaulin --fail-under N`` and ``cargo llvm-cov
--fail-under-lines N`` are read. Their other common gate is a script comparing
``go tool cover -func`` (or a report) to a number, which this scan cannot read,
so when the walk holds a ``go.mod`` (or a ``Cargo.toml``) and no gate of that
language is read, a ``threshold_meets_minimum`` that is false reads null (not
measured) with ``unmeasured_reason`` ``go_rust_gate_form_not_read`` (#937: a low
Python gate beside ``go.mod`` is not unmet). A gate meeting the minimum keeps true.
``go_rust_manifests`` exposes the same walk for the monorepo root unit's no-gate
reading (``lib.unit_readings``, #864).

A configured threshold is evidence the tooling fails below it *when coverage
runs*. ``enforced_meets_minimum`` (#921) is ``threshold_meets_minimum`` with
that run required: an enforced CI line (unmasked, on ordinary triggers) runs the
coverage tool that reads the threshold, or carries the threshold flag itself.
Layer 6 level 1, and so the Step 4 ``coverage_gate`` gate, reads it; the rules
and ``enforced_basis`` tokens are in ``lib.testing.coverage_ci``. A JaCoCo
``<maximum>`` on a ``MISSEDCOUNT`` / ``MISSEDRATIO`` counter is a gate whose
percentage is not read (unit ``uncovered_count`` / ``missed_ratio``), so it
reads unknown, never 0.

On a scoped run with no coverage.py config of its own, the root config coverage.py
reads counts when CI runs coverage from the root, and reads unknown when where CI runs
it is not resolved (#1032, ``lib.testing.coverage_root_config``).

What switches coverage on for a plain ``pytest`` or ``jest`` run (``addopts``,
``collectCoverage``), read per config directory, is ``lib.testing.coverage_activation``'s.

Inward-only imports: stdlib plus ``lib.docs.doc_graph``'s path-exclusion rule,
``lib.assess_config.is_user_excluded``, ``lib.testing.coverage_activation`` and
``lib.testing.coverage_ci``; never an orchestrator.

Full reference: lib/docs/coverage_gate.md.
"""
from __future__ import annotations

import os
import re
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Literal, TypedDict

from lib.assess_config import is_user_excluded
from lib.testing.coverage_activation import read_activation
from lib.testing.coverage_ci import NO_RUNS, CiRuns, EnforcedBasis, enforced_meets_minimum, read_ci_runs
from lib.testing.coverage_root_config import ROOT_UNRESOLVED, root_config, root_config_applies
from lib.docs.doc_graph import is_excluded_path
from lib.runner_args import reads_pdm_scripts

# The user's ``--exclude`` / config.toml excludes: (directory names, basename globs).
Excludes = tuple[set[str], list[str]]

# How deep below the repo root config files are looked for: deep enough for a
# monorepo package (``packages/web/jest.config.js``, ``skills/x/pyproject.toml``),
# shallow enough to stay cheap on a large tree.
_MAX_DEPTH = 4
# Config files are small; anything larger is not a config file worth reading.
_MAX_BYTES = 1_000_000
# Lines after (and, for nyc/c8, around) an anchor key searched for the metric.
_WINDOW = 15

_NUM = r"(-?\d+(?:\.\d+)?)"
# A threshold supplied by a variable: ``${{ env.COV_MIN }}`` (GitHub Actions),
# ``${COV_MIN}`` / ``$COV_MIN`` (shell, GitLab), ``$(COV_MIN)`` (Make, Azure),
# ``$$COV_MIN`` / ``$${COV_MIN}`` (a shell variable escaped in a Makefile),
# ``{{ cov_min }}`` (Jinja, Taskfile). The flag still gates; the number is not
# readable here, so the gate carries ``threshold: null``.
_TEMPLATE = r"\$\$\{[^}]*\}|\$\$\w+|\$\{\{[^}]*\}\}|\$\{[^}]*\}|\$\([^)]*\)|\$\w+|\{\{[^}]*\}\}"
_VALUE = r"(?:" + _NUM + r"|(?:" + _TEMPLATE + r"))"

# INI-style section that holds coverage.py's ``fail_under``, by file name.
_COVERAGE_PY_SECTIONS: dict[str, str] = {
    "pyproject.toml": "tool.coverage.report",
    ".coveragerc": "report",
    "setup.cfg": "coverage:report",
    "tox.ini": "coverage:report",
}
_SECTION_RE = re.compile(r"^\s*\[([^\]]+)\]\s*(?:[#;].*)?$")
_FAIL_UNDER_RE = re.compile(r"^\s*fail_under\s*[=:]\s*[\"']?" + _VALUE)
# coverage.py's run section, by file name: ``branch = true`` there makes
# ``fail_under`` gate the combined line-and-branch total, not line coverage.
_COVERAGE_PY_RUN_SECTIONS: dict[str, str] = {
    "pyproject.toml": "tool.coverage.run",
    ".coveragerc": "run",
    "setup.cfg": "coverage:run",
    "tox.ini": "coverage:run",
}
_BRANCH_TRUE_RE = re.compile(r"^\s*branch\s*[=:]\s*[\"']?(?:true|yes|on|1)\b",
                             re.IGNORECASE)
_COV_BRANCH_FLAG_RE = re.compile(r"--cov-branch\b")

# Command-line forms, read from CI and task-runner files.
_CLI_FILES = {
    "pyproject.toml", "setup.cfg", "tox.ini", "pytest.ini", "noxfile.py",
    "Makefile", "package.json", ".gitlab-ci.yml", ".pre-commit-config.yaml",
    "Jenkinsfile", "azure-pipelines.yml", ".travis.yml", "bitbucket-pipelines.yml",
    "justfile", "Justfile", "Taskfile.yml", "Taskfile.yaml",
}
_PYTEST_COV_RE = re.compile(r"--cov-fail-under[=\s]+[\"']?" + _VALUE)
_COVERAGE_CLI_RE = re.compile(
    r"\bcoverage\s+(report|xml|json|html|lcov)\b.*--fail-under[=\s]+[\"']?" + _VALUE)
# Both the ``--check-coverage`` flag and the ``check-coverage`` subcommand, but
# not the ``"check-coverage": true`` config key (read by ``_CHECK_COVERAGE_KEY``).
_CHECK_COVERAGE_CLI_RE = re.compile(
    r"\b(nyc|c8)\b.*(?<![\w-])(?:--)?check-coverage\b(?![\"']?\s*:)")
_LINES_FLAG_RE = re.compile(r"--lines[=\s]+" + _NUM)
# Rust (#788): anchored on the tool, so a coverage.py ``--fail-under`` never
# reads as one; tarpaulin's ``--fail-under`` is not llvm-cov's ``-lines`` flag.
# A toolchain selector (``cargo +nightly tarpaulin``) may sit between; the flag
# must be in the same command, not after a ``&&``, ``;`` or ``|``.
_TARPAULIN_RE = re.compile(
    r"\bcargo(?:\s+\+[\w.-]+)?[\s-]+tarpaulin\b[^;&|]*--fail-under(?![\w-])[=\s]+[\"']?" + _VALUE)
_LLVM_COV_RE = re.compile(
    r"\bcargo(?:\s+\+[\w.-]+)?[\s-]+llvm-cov\b[^;&|]*--fail-under-lines(?![\w-])[=\s]+[\"']?" + _VALUE)

# go-test-coverage (#788): ``threshold:`` then ``file:``, ``package:`` and
# ``total:`` percentages, one level deeper (or an inline ``{total: 80}`` map).
_TESTCOVERAGE_FILES = {".testcoverage.yml", ".testcoverage.yaml"}
_TC_THRESHOLD_RE = re.compile(r"^(\s*)threshold\s*:\s*(.*)$")
_TC_KEY_RE = re.compile(r"\b(file|package|total)\s*:\s*[\"']?" + _NUM)
# Manifests whose ecosystem gates coverage mostly in forms not read here.
_GO_RUST_MANIFESTS = {"go.mod", "Cargo.toml"}
_MANIFEST_TOOLS = {"go.mod": {"go-test-coverage"},
                   "Cargo.toml": {"cargo-tarpaulin", "cargo-llvm-cov"}}
GO_RUST_UNMEASURED = "go_rust_gate_form_not_read"

# Read at the repo root even on a scoped run: CI and task-runner files run the
# gates for the scoped package. Package configs (pyproject.toml, package.json,
# tox.ini, ...) at the root belong to the root package and are dropped. The root
# module's go-test-coverage config gates every package below it, so it is kept.
_ROOT_CI_FILES = (_CLI_FILES - {"pyproject.toml", "setup.cfg", "tox.ini", "pytest.ini",
                                "noxfile.py", "package.json"}) | _TESTCOVERAGE_FILES

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
_POM_MINIMUM_RE = re.compile(r"<minimum>\s*" + _NUM + r"\s*(%?)\s*</minimum>")
_POM_COUNTER_RE = re.compile(r"<counter>\s*(\w+)\s*</counter>")
_GRADLE_MINIMUM_RE = re.compile(r"\bminimum\s*=\s*[^0-9\n]{0,20}?" + _NUM)
_GRADLE_COUNTER_RE = re.compile(r"\bcounter\s*=\s*[\"'](\w+)[\"']")
# A ``<maximum>`` limit on a missed counter (``MISSEDCOUNT``, ``MISSEDRATIO``).
_POM_MAXIMUM_RE = re.compile(r"<maximum>\s*" + _NUM + r"\s*</maximum>")
_POM_VALUE_RE = re.compile(r"<value>\s*(\w+)\s*</value>")
_GRADLE_MAXIMUM_RE = re.compile(r"\bmaximum\s*=\s*[^0-9\n]{0,20}?" + _NUM)
_GRADLE_VALUE_RE = re.compile(r"\bvalue\s*=\s*[\"'](\w+)[\"']")

# Common gate forms this scan does not read - reported so an empty result reads
# as "none of the detected forms", never as "no gate anywhere".
NOT_DETECTED: tuple[str, ...] = (
    "Go: `go test -coverprofile` with a script comparing the `go tool cover "
    "-func` total to a number",
    "Rust: `cargo llvm-cov` `--fail-under-regions` / `--fail-under-functions`, "
    "and a tarpaulin `fail-under` in `tarpaulin.toml`",
    "Codecov / Coveralls status targets (server-side; a gate only when the "
    "status check is required)",
    "Kover `minBound`, sbt-scoverage `coverageMinimumStmtTotal`, "
    "SimpleCov `minimum_coverage`",
    "JaCoCo rules built programmatically or in a shared Gradle convention plugin",
    "CLI thresholds in shell scripts or in CI and task files other than GitHub "
    "workflows and composite actions, `.gitlab-ci.yml`, CircleCI, Jenkinsfile, "
    "Azure Pipelines, Travis, Bitbucket Pipelines, `Makefile`, `justfile`, "
    "`Taskfile.yml`, `noxfile.py`, `tox.ini` and `package.json` scripts",
    "coverage.py `fail_under` in a TOML inline table (`report = { fail_under = "
    "85 }` under `[tool.coverage]`)",
    "Vitest before 1.0: `lines` / `branches` directly under `coverage` with no "
    "`thresholds` key",
)
# Added on a scoped run: what the scope-limited walk leaves unread or unproven.
NOT_DETECTED_SCOPED: tuple[str, ...] = (
    "Root-level package configs on a scoped run (`pyproject.toml`, `setup.cfg`, "
    "`tox.ini`, `pytest.ini`, `noxfile.py`, `package.json` at the repo root): "
    "they belong to the root package and are not read, except the coverage.py "
    "config for a Python scope with none of its own",
    "Whether a gate in a root CI or task-runner file targets the scope: such a "
    "gate is reported for every scoped run, even one that runs another "
    "package's suite (`cd packages/b && pytest --cov-fail-under=90`)",
)


Unit = Literal["percent", "ratio", "uncovered_count", "missed_ratio"]
# Units that bound what is missed: any value gates, zero included (no line missed).
_MISSED_UNITS = {"uncovered_count", "missed_ratio"}

# The lowest configured threshold, as a percentage, that Layer 6 level 1 credits.
MINIMUM_THRESHOLD_PERCENT = 50.0


class CoverageGate(TypedDict):
    """One enforced threshold, as the ``coverage_gate.gates`` entries carry it."""

    file: str
    line: int
    tool: str
    form: str
    threshold: float | None
    unit: Unit
    threshold_percent: float | None
    metric: str | None


class CoverageGateBlock(TypedDict):
    """The ``coverage_gate`` run-context block."""

    available: bool
    configured: bool
    gates: list[CoverageGate]
    max_threshold_percent: float | None
    threshold_meets_minimum: bool | None
    unmeasured_reason: str | None
    enforced_meets_minimum: bool | None
    enforced_basis: EnforcedBasis
    not_detected: list[str]


# Tools whose check runs at a built-in default when no number is given, so a
# ``threshold: null`` still gates. For any other tool a null threshold means the
# value was not read (an empty ``coverageThreshold``, a ``thresholds`` block with
# only ``autoUpdate``), which is reported but not counted as enforced.
_DEFAULT_THRESHOLD_TOOLS = {"nyc", "c8", "nyc/c8"}
# A null threshold from coverage.py or pytest-cov only arises from a templated
# value (``--cov-fail-under=${{ env.COV_MIN }}``): the flag is set, so it gates.
_NULL_THRESHOLD_GATES = _DEFAULT_THRESHOLD_TOOLS | {"coverage.py", "pytest-cov",
                                                    "cargo-tarpaulin", "cargo-llvm-cov"}


def threshold_percent(threshold: float | None, unit: Unit) -> float | None:
    """``threshold`` as a percentage of coverage: as written for ``percent``, times
    100 for ``ratio`` (rounded, so ``0.55`` is ``55.0``), and None for an
    ``uncovered_count`` or ``missed_ratio`` or a threshold that was not read."""
    if threshold is None or unit in _MISSED_UNITS:
        return None
    return threshold if unit == "percent" else round(threshold * 100, 6)


def _gate(file: str, line: int, tool: str, form: str,
          threshold: float | None, unit: Unit, metric: str | None) -> CoverageGate:
    return {"file": file, "line": line, "tool": tool, "form": form,
            "threshold": threshold, "unit": unit,
            "threshold_percent": threshold_percent(threshold, unit), "metric": metric}


def _num(text: str) -> float:
    return float(text)


def _value(text: str | None) -> float | None:
    """A threshold as written, or None when it is a template variable."""
    return None if text is None else float(text)


def _branch_configured(name: str, lines: list[str]) -> bool:
    """True when the file's coverage.py run section sets ``branch = true``."""
    section = _COVERAGE_PY_RUN_SECTIONS.get(name)
    if section is None:
        return False
    current = None
    for line in lines:
        header = _SECTION_RE.match(line)
        if header:
            current = header.group(1).strip().strip("\"'")
        elif current == section and _BRANCH_TRUE_RE.match(line):
            return True
    return False


def _total_or_lines(branch: bool) -> str:
    """coverage.py gates the line-and-branch total once branch coverage is on."""
    return "total" if branch else "lines"


def _is_comment(line: str) -> bool:
    stripped = line.lstrip()
    return stripped.startswith(("#", ";", "//"))


def _scan_fail_under(rel: str, lines: list[str], section: str,
                     branch: bool) -> list[CoverageGate]:
    """coverage.py ``fail_under`` inside the named INI/TOML section."""
    gates: list[CoverageGate] = []
    current = None
    for idx, line in enumerate(lines, 1):
        header = _SECTION_RE.match(line)
        if header:
            current = header.group(1).strip().strip("\"'")
            continue
        match = _FAIL_UNDER_RE.match(line) if current == section else None
        if match:
            gates.append(_gate(rel, idx, "coverage.py", f"[{section}] fail_under",
                               _value(match.group(1)), "percent",
                               _total_or_lines(branch)))
    return gates


def _scan_cli(rel: str, lines: list[str], branch: bool = False) -> list[CoverageGate]:
    """``--cov-fail-under``, ``coverage report --fail-under`` and nyc/c8 flags.

    ``branch`` is the file's own coverage.py ``branch = true``; ``--cov-branch``
    on the line has the same effect on the metric. A CI or task-runner file
    carries no coverage.py config, and the package config coverage.py reads at
    run time is not resolved, so ``lines`` on such a gate means branch mode was
    not determined, not that it is off.
    """
    gates: list[CoverageGate] = []
    for idx, line in enumerate(lines, 1):
        if _is_comment(line):
            continue
        gates.extend(_cli_line_gates(rel, idx, line, branch))
    return gates


def _cli_line_gates(rel: str, idx: int, line: str, branch: bool) -> list[CoverageGate]:
    gates = []
    metric = _total_or_lines(branch or bool(_COV_BRANCH_FLAG_RE.search(line)))
    # One gate per flag: ``echo --cov-fail-under=10 && pytest --cov --cov-fail-under=90``.
    for flag in _PYTEST_COV_RE.finditer(line):
        gates.append(_gate(rel, idx, "pytest-cov", "--cov-fail-under",
                           _value(flag.group(1)), "percent", metric))
    match = _COVERAGE_CLI_RE.search(line)
    if match:
        gates.append(_gate(rel, idx, "coverage.py",
                           f"coverage {match.group(1)} --fail-under",
                           _value(match.group(2)), "percent", metric))
    match = _CHECK_COVERAGE_CLI_RE.search(line)
    if match:
        lines_flag = _LINES_FLAG_RE.search(line)
        gates.append(_gate(rel, idx, match.group(1), "--check-coverage",
                           _num(lines_flag.group(1)) if lines_flag else None,
                           "percent", "lines" if lines_flag else None))
    for pattern, tool, form in ((_TARPAULIN_RE, "cargo-tarpaulin", "--fail-under"),
                                (_LLVM_COV_RE, "cargo-llvm-cov", "--fail-under-lines")):
        match = pattern.search(line)
        if match:
            gates.append(_gate(rel, idx, tool, form, _value(match.group(1)),
                               "percent", "lines"))
    return gates


def _scan_testcoverage(rel: str, lines: list[str]) -> list[CoverageGate]:
    """go-test-coverage ``threshold:`` keys; each gate's line holds its value."""
    gates: list[CoverageGate] = []
    for idx, line in enumerate(lines):
        anchor = None if _is_comment(line) else _TC_THRESHOLD_RE.match(line)
        if anchor is None:
            continue
        rest = anchor.group(2).split("#", 1)[0]
        if rest.strip():
            gates.extend(_testcoverage_gate(rel, idx + 1, m)
                         for m in _TC_KEY_RE.finditer(rest))
            continue
        indent = len(anchor.group(1))
        for row in range(idx + 1, len(lines)):
            text = lines[row]
            if not text.strip() or _is_comment(text):
                continue
            if len(text) - len(text.lstrip()) <= indent:
                break
            match = _TC_KEY_RE.match(text.strip().split("#", 1)[0])
            if match:
                gates.append(_testcoverage_gate(rel, row + 1, match))
    return gates


def _testcoverage_gate(rel: str, line: int, match: re.Match[str]) -> CoverageGate:
    return _gate(rel, line, "go-test-coverage", f"threshold.{match.group(1)}",
                 _num(match.group(2)), "percent", match.group(1))


def _window_metric(window: list[str]) -> tuple[float | None, str | None]:
    """First ``lines`` value in ``window``, else the first other metric."""
    for metric, pattern in _METRIC_RE.items():
        for line in window:
            match = pattern.search(line)
            if match:
                return _num(match.group(1)), metric
    return None, None


def _close(lines: list[str], row: int, col: int, limit: int) -> tuple[int, int] | None:
    """(row, col) of the ``}`` closing the ``{`` at ``lines[row][col]``."""
    depth = 0
    for r in range(row, min(limit, len(lines))):
        text = lines[r]
        for c in range(col if r == row else 0, len(text)):
            depth += {"{": 1, "}": -1}.get(text[c], 0)
            if depth == 0:
                return r, c
    return None


def _open_before(lines: list[str], row: int, col: int, limit: int) -> tuple[int, int] | None:
    """(row, col) of the unmatched ``{`` enclosing ``lines[row][col]``."""
    depth = 0
    for r in range(row, max(row - limit, -1), -1):
        text = lines[r]
        for c in range((col if r == row else len(text)) - 1, -1, -1):
            depth += {"}": 1, "{": -1}.get(text[c], 0)
            if depth < 0:
                return r, c
    return None


def _slice(lines: list[str], start: tuple[int, int], end: tuple[int, int]) -> list[str]:
    """The text from ``start`` to ``end`` inclusive, as lines."""
    if start[0] == end[0]:
        return [lines[start[0]][start[1]:end[1] + 1]]
    return ([lines[start[0]][start[1]:]] + lines[start[0] + 1:end[0]]
            + [lines[end[0]][:end[1] + 1]])


def _block(lines: list[str], row: int, match: re.Match[str], enclosing: bool
           ) -> list[str] | None:
    """The braced object an anchor opens (``coverageThreshold: {...}``), or, when
    ``enclosing``, the one it sits in (``{"check-coverage": true, ...}``).

    Bounding the read to the object keeps one Jest project's threshold from
    being credited to its neighbour. None for brace-less config (YAML).
    """
    if enclosing:
        start = _open_before(lines, row, match.start(), _WINDOW + 1)
    else:
        brace = lines[row].find("{", match.end())
        start = (row, brace) if brace >= 0 else None
        if start is None and row + 1 < len(lines) and lines[row + 1].lstrip().startswith("{"):
            start = (row + 1, lines[row + 1].index("{"))
    end = _close(lines, *start, start[0] + _WINDOW * 4) if start else None
    return _slice(lines, start, end) if start and end else None


def _scan_anchor(rel: str, lines: list[str], anchor: re.Pattern[str],
                 tool: str, form: str, before: int = 0) -> list[CoverageGate]:
    """A threshold block opened by ``anchor``; the metric is read from the braced
    object, or from a line window where the config has no braces."""
    gates: list[CoverageGate] = []
    for idx, line in enumerate(lines, 1):
        match = None if _is_comment(line) else anchor.search(line)
        if match is None:
            continue
        block = _block(lines, idx - 1, match, enclosing=before > 0)
        if block is None:
            block = lines[max(idx - 1 - before, 0):idx + _WINDOW]
        threshold, metric = _window_metric(block)
        unit: Unit = ("uncovered_count" if threshold is not None and threshold < 0
                      else "percent")
        gates.append(_gate(rel, idx, tool, form, threshold, unit, metric))
    return gates


def _scan_package_json(rel: str, lines: list[str]) -> list[CoverageGate]:
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
                 counter: re.Pattern[str], form: str) -> list[CoverageGate]:
    """JaCoCo ``minimum`` values in a build file that applies JaCoCo."""
    if not any("jacoco" in line.lower() for line in lines):
        return []
    gates: list[CoverageGate] = []
    for idx, line in enumerate(lines, 1):
        match = None if _is_comment(line) else minimum.search(line)
        if match:
            # JaCoCo reads ``0.80`` as a ratio and ``80%`` as a percentage; the
            # unit follows the ``%`` as written (Gradle's DSL takes ratios only).
            value = _num(match.group(1))
            has_percent = match.lastindex == 2 and match.group(2) == "%"
            unit: Unit = "percent" if has_percent else "ratio"
            gates.append(_gate(rel, idx, "jacoco", form, value, unit,
                               _nearest_counter(lines, idx, counter)))
        gates.extend(_jacoco_maximum(rel, lines, idx, form, counter))
    return gates


def _jacoco_maximum(rel: str, lines: list[str], idx: int, form: str,
                    counter: re.Pattern[str]) -> list[CoverageGate]:
    """A JaCoCo ``maximum`` on a missed counter, on line ``idx``: its percentage is not read."""
    pom = form.startswith("<")
    line = lines[idx - 1]
    match = None if _is_comment(line) else (_POM_MAXIMUM_RE if pom else _GRADLE_MAXIMUM_RE).search(line)
    if match is None:
        return []
    value_re = _POM_VALUE_RE if pom else _GRADLE_VALUE_RE
    before = lines[max(idx - _WINDOW, 0):idx - 1] + [line[:match.start()]]
    values = [m.group(1).upper() for text in before for m in value_re.finditer(text)]
    if not values or not values[-1].startswith("MISSED"):
        return []
    unit: Unit = "missed_ratio" if values[-1] == "MISSEDRATIO" else "uncovered_count"
    return [_gate(rel, idx, "jacoco", form.replace("minimum", "maximum"), _num(match.group(1)),
                  unit, _nearest_counter(lines, idx, counter))]


def _scanners_for(name: str, rel_parts: tuple[str, ...]
                  ) -> list[Callable[[str, list[str]], list[CoverageGate]]]:
    """The scanners that apply to a file, by its name and location."""
    scanners: list[Callable[[str, list[str]], list[CoverageGate]]] = []
    if name in _COVERAGE_PY_SECTIONS:
        section = _COVERAGE_PY_SECTIONS[name]
        scanners.append(lambda r, ls: _scan_fail_under(
            r, ls, section, _branch_configured(name, ls)))
    if name == "package.json":
        scanners.append(_scan_package_json)
    elif name in _CLI_FILES or _is_workflow(name, rel_parts):
        scanners.append(lambda r, ls: _scan_cli(r, ls, _branch_configured(name, ls)))
    scanners.extend(_js_jvm_scanners(name))
    return scanners


def _js_jvm_scanners(name: str) -> list[Callable[[str, list[str]], list[CoverageGate]]]:
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
    if name in _TESTCOVERAGE_FILES:
        return [_scan_testcoverage]
    if name == "pom.xml":
        return [lambda r, ls: _scan_jacoco(r, ls, _POM_MINIMUM_RE,
                                           _POM_COUNTER_RE, "<rules> <minimum>")]
    if name in _JACOCO_GRADLE:
        return [lambda r, ls: _scan_jacoco(r, ls, _GRADLE_MINIMUM_RE,
                                           _GRADLE_COUNTER_RE,
                                           "jacocoTestCoverageVerification minimum")]
    return []


def _is_workflow(name: str, rel_parts: tuple[str, ...]) -> bool:
    """A CI definition read by location: GitHub workflows and composite actions,
    and CircleCI's ``.circleci/config.yml``."""
    if not name.endswith((".yml", ".yaml")):
        return False
    if rel_parts[:2] == (".github", "workflows"):
        return True
    if rel_parts[:2] == (".github", "actions") and name in {"action.yml", "action.yaml"}:
        return True
    return rel_parts[:1] == (".circleci",)


def _walk(root: Path, base: Path, excludes: Excludes,
          wanted: Callable[[str, tuple[str, ...]], object] | None = None) -> Iterator[Path]:
    """Repo-relative files under ``base`` (bounded depth below it, built-in and
    user excludes) that have a scanner, or that ``wanted`` accepts."""
    keep = wanted or _scanners_for
    extra_dirs, extra_patterns = excludes
    for dirpath, dirnames, filenames in os.walk(base):
        rel_dir = Path(dirpath).relative_to(root)
        if len(Path(dirpath).relative_to(base).parts) >= _MAX_DEPTH:
            dirnames[:] = []
        dirnames[:] = sorted(
            d for d in dirnames
            if not is_excluded_path(rel_dir / d)
            and not is_user_excluded(rel_dir / d, extra_dirs, extra_patterns))
        for name in sorted(filenames):
            rel = rel_dir / name
            if (keep(name, rel.parts)
                    and not is_user_excluded(rel, extra_dirs, extra_patterns)):
                yield rel


def walk_named(root: Path, excludes: Excludes, names: set[str],
               base: Path | None = None) -> Iterator[Path]:
    """Repo-relative files under ``base`` (default ``root``) whose name is in ``names``, by
    the same bounded, excluding walk the coverage scan uses (``lib.guardrails`` reads it)."""
    return _walk(root, base or root, excludes, lambda name, _parts: name in names)


def _candidate_files(root: Path, scope: Path | None,
                     excludes: Excludes) -> Iterator[tuple[Path, str]]:
    """Files with a scanner: the whole repo, or for a scoped run the scope plus
    the root CI and task-runner files, which run the gates for every package.
    A package-level config outside the scope is not read."""
    if scope is None or scope == root:
        rels = list(_walk(root, root, excludes))
    else:
        rels = list(_walk(root, scope, excludes))
        rels += [rel for rel in _walk(root, root, excludes)
                 if _is_root_ci(rel) and rel not in rels]
    for rel in rels:
        yield root / rel, rel.as_posix()


def _go_rust_manifests(root: Path, scope: Path | None, excludes: Excludes) -> set[str]:
    """The ``go.mod`` / ``Cargo.toml`` names in the walk; on a scoped run, in the scope
    or in a directory above it up to the repo root (the module or workspace the
    scope belongs to, as Go and Cargo resolve it). A manifest under a ``testdata``
    directory is a fixture (the Go tool ignores it too), so it does not count."""
    def wanted(name: str, parts: tuple[str, ...]) -> bool:
        return name in _GO_RUST_MANIFESTS and "testdata" not in parts
    base = root if scope is None else scope
    found: set[str] = set()
    for rel in _walk(root, base, excludes, wanted):
        found.add(rel.name)
        if found == _GO_RUST_MANIFESTS:
            break
    ancestors = [d for d in base.parents if d == root or root in d.parents]
    return found | {name for d in ancestors for name in _GO_RUST_MANIFESTS
                    if (d / name).is_file()
                    and not is_user_excluded((d / name).relative_to(root), *excludes)}


def go_rust_manifests(repo_root: Path | str, excludes: Excludes | None = None) -> set[str]:
    """The ``go.mod`` / ``Cargo.toml`` names anywhere in the repository walk (#864: the
    root unit's no-gate reading is not measured when one is present, as here)."""
    return _go_rust_manifests(Path(repo_root).resolve(), None, excludes or (set(), []))


def _is_root_ci(rel: Path) -> bool:
    """A CI definition (by location) or a task-runner file at the repo root."""
    if len(rel.parts) == 1:
        return rel.name in _ROOT_CI_FILES
    return _is_workflow(rel.name, rel.parts)


def _read_lines(path: Path) -> list[str] | None:
    try:
        if path.stat().st_size > _MAX_BYTES:
            return None
        return path.read_text(encoding="utf-8", errors="replace").splitlines()
    except (OSError, ValueError):
        return None


def gates_something(gate: CoverageGate) -> bool:
    """A gate `configured` counts: a non-zero threshold, a null one from nyc/c8, or any
    bound on what is missed (a JaCoCo ``MISSEDCOUNT`` maximum of 0 misses nothing)."""
    if gate["unit"] in _MISSED_UNITS:
        return True
    if gate["threshold"] is None:
        return gate["tool"] in _NULL_THRESHOLD_GATES
    return gate["threshold"] != 0


def max_threshold_percent(gates: list[CoverageGate]) -> float | None:
    """The highest non-null ``threshold_percent`` across ``gates``, or None."""
    percents = [g["threshold_percent"] for g in gates if g["threshold_percent"] is not None]
    return max(percents) if percents else None


def threshold_meets_minimum(gates: list[CoverageGate]) -> bool | None:
    """Layer 6 level 1: does a counted gate hold coverage at 50 percent or more?

    Only the gates ``configured`` counts are read, so a ``fail_under = 0`` still
    gates nothing. True when one has ``threshold_percent`` of at least
    ``MINIMUM_THRESHOLD_PERCENT``; otherwise None (not measured) when one has a
    null ``threshold_percent`` (a template, an nyc/c8 default, an uncovered-line
    count), since it could be 50 or more; otherwise False.
    """
    counted = [g for g in gates if gates_something(g)]
    percents = [g["threshold_percent"] for g in counted]
    if any(p is not None and p >= MINIMUM_THRESHOLD_PERCENT for p in percents):
        return True
    if any(p is None for p in percents):
        return None
    return False


def _root_floor(root: Path, scope: Path | None, excludes: Excludes
                ) -> tuple[list[CoverageGate], list[str], bool | None, CiRuns | None]:
    """#1032: the root coverage.py config's gates for a scoped run, its lines, whether
    it governs the scope (``lib.testing.coverage_root_config``) and the CI runs read for that."""
    config = root_config(root, scope, excludes) if scope not in (None, root) else None
    lines = _read_lines(root / config) if config else None
    if config is None or lines is None:
        return [], [], False, None
    gates = _scan_fail_under(config, lines, _COVERAGE_PY_SECTIONS[config],
                             _branch_configured(config, lines))
    if not gates:
        return [], [], False, None
    ci = read_ci_runs(root)
    return gates, lines, root_config_applies(root, config, ci), ci


@reads_pdm_scripts   # pdm run pytest with a pytest script runs the script (#1046)
def detect_coverage_gate(repo_root: Path | str, scope: Path | None = None,
                         excludes: Excludes | None = None) -> CoverageGateBlock:
    """Return the ``coverage_gate`` run-context block for ``repo_root``.

    ``{"available": True, "configured": bool, "gates": [...],
    "max_threshold_percent": float | None, "threshold_meets_minimum": bool | None,
    "unmeasured_reason": str | None, "not_detected": [...]}`` where each gate is ``{file, line, tool, form,
    threshold, unit, threshold_percent, metric}``.
    ``configured`` is true when at least one gate has a non-zero threshold, or a
    null one from a tool that checks at a built-in default (nyc/c8). A
    ``fail_under = 0``, and a Jest/Vitest anchor whose threshold was not read, are
    reported but count as gating nothing. A templated threshold
    (``--cov-fail-under=${{ env.COV_MIN }}``) is a gate with ``threshold: null``.

    ``scope`` (absolute, or relative to ``repo_root``) limits the walk to that
    subtree plus the root CI files; ``excludes`` is the user's ``(dirs,
    patterns)`` pair, matched as ``is_user_excluded`` matches it everywhere.
    """
    root = Path(repo_root).resolve()
    scope_abs = (root / scope).resolve() if scope else None
    gates: list[CoverageGate] = []
    read: list[tuple[str, list[str]]] = []
    for path, rel in _candidate_files(root, scope_abs, excludes or (set(), [])):
        lines = _read_lines(path)
        if lines is None:
            continue
        for scan in _scanners_for(path.name, Path(rel).parts):
            gates.extend(scan(rel, lines))
        read.append((rel, lines))
    excl = excludes or (set(), [])
    root_gates, root_lines, applies, ci = _root_floor(root, scope_abs, excl)
    own = threshold_meets_minimum(gates)
    if applies is not False:
        gates += root_gates
    if applies:
        read.append((root_gates[0]["file"], root_lines))
    manifests = _go_rust_manifests(root, scope_abs if scope_abs != root else None,
                                   excludes or (set(), []))
    meets = threshold_meets_minimum(gates)
    # #937: no gate read for a Go or Rust module present makes a false reading unknown;
    # one language's low gate never reads unmet for another's (a null keeps its reason).
    unread = {m for m in manifests if not any(g["tool"] in _MANIFEST_TOOLS[m] for g in gates)}
    unmeasured = GO_RUST_UNMEASURED if unread and meets is False else None
    meets = None if unmeasured else meets
    if applies is None and unmeasured is None and meets != own:
        meets, unmeasured = None, ROOT_UNRESOLVED   # the root floor may not govern the scope
    # Root gates that may not govern the scope are reported, never counted or credited.
    known = gates if applies is not None else gates[:len(gates) - len(root_gates)]
    enforced, basis = enforced_meets_minimum(
        known, meets, (ci or read_ci_runs(root)) if meets else NO_RUNS,
        lambda text: _cli_line_gates("", 0, text, False), counted=gates_something,
        minimum=MINIMUM_THRESHOLD_PERCENT, activation=read_activation(read))
    return {
        "available": True,
        "configured": any(gates_something(g) for g in known),
        "gates": gates,
        "max_threshold_percent": max_threshold_percent(known),
        "threshold_meets_minimum": meets,
        "unmeasured_reason": unmeasured,
        "enforced_meets_minimum": enforced,
        "enforced_basis": basis,
        "not_detected": list(NOT_DETECTED) + (
            list(NOT_DETECTED_SCOPED) if scope_abs not in (None, root) else []),
    }
