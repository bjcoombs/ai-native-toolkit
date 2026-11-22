"""Guardrails stated: do the instruction files tell an agent what the gates enforce? (#767)

An agent that trips an undocumented gate learns it for that session only; the
next session fails on the same gate. This module lists the gates the repository
configures at HEAD, each with its threshold and where it is set, and checks
whether a graded instruction file states it. No model: every gate is read from a
fixed config form, and every check is a line match.

Gate kinds read (one gate per configuring file):

- ``complexity_limit``: ruff's mccabe limit, when ``C901`` is selected (``select``
  or ``extend-select`` names ``C901``, ``C90``, ``C9``, ``C`` or ``ALL``) in
  ``pyproject.toml`` (``[tool.ruff]`` / ``[tool.ruff.lint]``), ``ruff.toml`` or
  ``.ruff.toml``; the threshold is ``max-complexity`` (``[...mccabe]``), or ruff's
  default 10 when unset. flake8's ``max-complexity`` in ``[flake8]`` of
  ``.flake8``, ``setup.cfg`` or ``tox.ini`` (setting it turns C901 on).
- ``coverage_floor``: the ``coverage_gate`` block's gates that gate something (a
  non-zero threshold, or a tool default), read once, never re-parsed. One gate
  per file that sets a floor; the highest ``threshold_percent`` in that file is
  its threshold.
- ``pre_commit``: a root ``.pre-commit-config.yaml`` (or ``.yml``) with at least
  one ``- id:`` hook. One gate per config, whatever its hook count.
- ``type_strictness``: mypy ``strict = true`` in ``[tool.mypy]`` of
  ``pyproject.toml`` or ``[mypy]`` of ``mypy.ini`` / ``.mypy.ini`` / ``setup.cfg``.

Every kind counts one gate per configuring file. Ruff, flake8 and mypy configs
are read wherever they sit in the repository (a package's own ``pyproject.toml``
is a gate of its own), through the walk ``coverage_gate`` uses, so a monorepo's
package gates are counted beside its coverage floors. Within one directory only
the config the tool itself reads counts: the first, in the tool's own order, that
configures the tool at all (ruff: ``.ruff.toml``, ``ruff.toml``, then a
``pyproject.toml`` with ``[tool.ruff]``; flake8: the first of ``setup.cfg``,
``tox.ini``, ``.flake8`` with a ``[flake8]`` section; mypy: ``mypy.ini``,
``.mypy.ini``, then a ``pyproject.toml`` with ``[tool.mypy]``, then ``setup.cfg``
with ``[mypy]``). A lower-precedence file the tool never applies is not a gate.

Not read yet (named in ``not_read``): eslint ``complexity``, TypeScript
``strict``, file-size ratchets, architecture or import-boundary tests, required
CI checks and branch protection, sign-off rules.

A gate is **stated** when one line of a graded instruction file (the keys of the
core's ``instruction_files``) names its rule and, where the gate has a threshold,
the threshold as a standalone number:

- ``complexity_limit``: ``complexity``, ``C901`` or ``mccabe`` and the limit;
- ``coverage_floor``: ``coverage`` and the threshold as a percentage (``80``) or
  a ratio (``0.80``); with no readable threshold, ``coverage`` beside ``fail_under``,
  ``threshold``, ``floor`` or ``minimum``;
- ``pre_commit``: ``pre-commit``;
- ``type_strictness``: ``mypy`` and ``strict``.

The local command is not required: the snippet carries it. Each unstated gate
gets ``snippet``, a one-line sentence an instruction file can take as written,
built from a template and the gate's own figures, never from file text.

``stated_pct`` is the share of gates stated, as a percentage; null when no gate
is found, so the maturity-model row reads unknown rather than a false 0.
"""
from __future__ import annotations

import configparser
import re
import tomllib
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Any, Literal, TypedDict

from lib.coverage_gate import CoverageGate, Excludes, gates_something, walk_named

GateKind = Literal["complexity_limit", "coverage_floor", "pre_commit", "type_strictness"]

RUFF_DEFAULT_MAX_COMPLEXITY = 10
_C901_SELECTORS = frozenset({"C901", "C90", "C9", "C", "ALL"})
_C901_IGNORES = frozenset({"C901", "C90", "C9", "C"})
_FAIL_UNDER_TOOLS = frozenset({"coverage.py", "pytest-cov"})
# Each tool's own discovery order within one directory.
_RUFF_FILES = (".ruff.toml", "ruff.toml", "pyproject.toml")
_FLAKE8_FILES = ("setup.cfg", "tox.ini", ".flake8")
_MYPY_FILES = ("mypy.ini", ".mypy.ini", "pyproject.toml", "setup.cfg")
_PRE_COMMIT_FILES = (".pre-commit-config.yaml", ".pre-commit-config.yml")
_HOOK_RE = re.compile(r"^\s*-\s*id:\s*\S")

NOT_READ: tuple[str, ...] = (
    "eslint `complexity` rule",
    "TypeScript `strict` in tsconfig.json",
    "file-size ratchets",
    "architecture or import-boundary tests",
    "required CI checks and branch protection",
    "sign-off rules",
)


class Guardrail(TypedDict):
    kind: GateKind
    tool: str
    rule: str
    threshold: float | None
    file: str
    line: int | None
    stated: bool
    stated_in: str | None
    snippet: str | None


class GuardrailsBlock(TypedDict):
    available: bool
    gates: list[Guardrail]
    enforced: int
    stated: int
    unstated: int
    stated_pct: float | None
    instruction_files_read: int
    not_read: list[str]


# --- reading the gates ---------------------------------------------------------

def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _line_of(text: str, pattern: str) -> int | None:
    """The 1-based line of the first line matching ``pattern``, ignoring comments."""
    regex = re.compile(pattern)
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.lstrip().startswith("#") and regex.search(line):
            return number
    return None


def _table(data: Mapping[str, Any], dotted: str) -> Mapping[str, Any]:
    node: Any = data
    for part in dotted.split("."):
        node = node.get(part) if isinstance(node, Mapping) else None
        if node is None:
            return {}
    return node if isinstance(node, Mapping) else {}


def _names_any(tables: Iterable[Mapping[str, Any]], keys: tuple[str, ...],
               codes: frozenset[str]) -> bool:
    for table in tables:
        for key in keys:
            listed = table.get(key)
            if isinstance(listed, list) and any(str(code) in codes for code in listed):
                return True
    return False


def _selects_c901(*tables: Mapping[str, Any]) -> bool:
    """C901 is selected and not ignored (``select = ["ALL"]`` beside ``ignore = ["C901"]``)."""
    if _names_any(tables, ("ignore", "extend-ignore"), _C901_IGNORES):
        return False
    return _names_any(tables, ("select", "extend-select"), _C901_SELECTORS)


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _ruff_gate(root: Path, name: str) -> Guardrail | None:
    text = _read_text(root / name)
    if text is None:
        return None
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return None
    base = _table(data, "tool.ruff") if Path(name).name == "pyproject.toml" else data
    if not base:
        return None
    lint = _table(base, "lint")
    if not _selects_c901(base, lint):
        return None
    mccabe = _table(lint, "mccabe") or _table(base, "mccabe")
    limit = _number(mccabe.get("max-complexity"))
    line = _line_of(text, r"^\s*max-complexity\s*=") if limit is not None else None
    return _gate("complexity_limit", "ruff", "C901",
                 limit if limit is not None else float(RUFF_DEFAULT_MAX_COMPLEXITY),
                 name, line or _line_of(text, r"C9|\"C\"|'C'|ALL"))


def _ini_option(root: Path, name: str, section: str, option: str) -> tuple[str, str] | None:
    text = _read_text(root / name)
    if text is None:
        return None
    parser = configparser.ConfigParser(interpolation=None)
    try:
        parser.read_string(text)
    except configparser.Error:
        return None
    if not parser.has_option(section, option):
        return None
    return parser.get(section, option).strip(), text


def _flake8_gate(root: Path, name: str) -> Guardrail | None:
    found = _ini_option(root, name, "flake8", "max-complexity")
    if found is None:
        return None
    value, text = found
    try:
        limit = float(value)
    except ValueError:
        return None
    if limit <= 0:
        return None
    return _gate("complexity_limit", "flake8", "max-complexity", limit, name,
                 _line_of(text, r"^\s*max[-_]complexity\s*[=:]"))


def _mypy_gate(root: Path, name: str) -> Guardrail | None:
    if Path(name).name == "pyproject.toml":
        text = _read_text(root / name)
        if text is None:
            return None
        try:
            strict = _table(tomllib.loads(text), "tool.mypy").get("strict")
        except tomllib.TOMLDecodeError:
            return None
        if strict is not True:
            return None
        return _gate("type_strictness", "mypy", "strict", None, name,
                     _line_of(text, r"^\s*strict\s*="))
    found = _ini_option(root, name, "mypy", "strict")
    if found is None or found[0].lower() not in {"true", "1", "yes", "on"}:
        return None
    return _gate("type_strictness", "mypy", "strict", None, name,
                 _line_of(found[1], r"^\s*strict\s*[=:]"))


def _pre_commit_gate(root: Path) -> Guardrail | None:
    for name in _PRE_COMMIT_FILES:
        text = _read_text(root / name)
        if text is not None and any(_HOOK_RE.match(line) for line in text.splitlines()):
            return _gate("pre_commit", "pre-commit", "hooks", None, name, None)
    return None


def _coverage_gates(block: Mapping[str, Any] | None) -> list[Guardrail]:
    """One gate per file of the ``coverage_gate`` block: its counted gate with the highest
    percent, so a floor counts per configuring file as every other kind does."""
    if not isinstance(block, Mapping) or block.get("available") is False:
        return []
    gates: list[CoverageGate] = list(block.get("gates") or [])
    by_file: dict[str, list[CoverageGate]] = {}
    for gate in gates:
        if gates_something(gate):
            by_file.setdefault(gate["file"], []).append(gate)
    found: list[Guardrail] = []
    for file in sorted(by_file):
        best = max(by_file[file], key=lambda gate: gate["threshold_percent"] or -1.0)
        rule = "fail_under" if best["tool"] in _FAIL_UNDER_TOOLS else "threshold"
        found.append(_gate("coverage_floor", best["tool"], rule, best["threshold_percent"],
                           best["file"], best["line"]))
    return found


def _gate(kind: GateKind, tool: str, rule: str, threshold: float | None,
          file: str, line: int | None) -> Guardrail:
    return {"kind": kind, "tool": tool, "rule": rule, "threshold": threshold, "file": file,
            "line": line, "stated": False, "stated_in": None, "snippet": None}


def _toml(root: Path, rel: str) -> Mapping[str, Any] | None:
    text = _read_text(root / rel)
    if text is None:
        return None
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return None


def _ini_sections(root: Path, rel: str) -> set[str]:
    text = _read_text(root / rel)
    parser = configparser.ConfigParser(interpolation=None)
    try:
        parser.read_string(text or "")
    except configparser.Error:
        return set()
    return set(parser.sections())


def _configures_ruff(root: Path, rel: str) -> bool:
    data = _toml(root, rel)
    if data is None:
        return False
    return bool(_table(data, "tool.ruff")) if Path(rel).name == "pyproject.toml" else True


def _configures_flake8(root: Path, rel: str) -> bool:
    return "flake8" in _ini_sections(root, rel)


def _configures_mypy(root: Path, rel: str) -> bool:
    name = Path(rel).name
    if name == "pyproject.toml":
        data = _toml(root, rel)
        return data is not None and bool(_table(data, "tool.mypy"))
    if name == "setup.cfg":
        return "mypy" in _ini_sections(root, rel)
    return (root / rel).is_file()


def _applied(root: Path, files: list[str], order: tuple[str, ...],
             configures: Callable[[Path, str], bool]) -> list[str]:
    """Per directory, the one config the tool reads: the first in ``order`` that configures it."""
    by_dir: dict[str, dict[str, str]] = {}
    for rel in files:
        path = Path(rel)
        by_dir.setdefault(path.parent.as_posix(), {})[path.name] = rel
    chosen: list[str] = []
    for directory in sorted(by_dir):
        present = by_dir[directory]
        pick = next((present[name] for name in order
                    if name in present and configures(root, present[name])), None)
        if pick is not None:
            chosen.append(pick)
    return chosen


def config_files(root: Path, excludes: Excludes | None = None) -> list[str]:
    """Repo-relative POSIX paths of the ruff, flake8 and mypy config files anywhere
    under ``root`` (a package's own ``pyproject.toml`` included), the walk
    ``coverage_gate`` uses: bounded depth, built-in and user excludes."""
    wanted = set(_RUFF_FILES) | set(_FLAKE8_FILES) | set(_MYPY_FILES)
    return [rel.as_posix() for rel in walk_named(root, excludes or (set(), []), wanted)]


def find_gates(root: Path, coverage_gate: Mapping[str, Any] | None,
               excludes: Excludes | None = None) -> list[Guardrail]:
    """Every gate this module reads under ``root``, in kind order then file order."""
    files = config_files(root, excludes)
    gates: list[Guardrail | None] = [
        _ruff_gate(root, rel) for rel in _applied(root, files, _RUFF_FILES, _configures_ruff)]
    gates += [_flake8_gate(root, rel)
              for rel in _applied(root, files, _FLAKE8_FILES, _configures_flake8)]
    gates += [*_coverage_gates(coverage_gate), _pre_commit_gate(root)]
    gates += [_mypy_gate(root, rel)
              for rel in _applied(root, files, _MYPY_FILES, _configures_mypy)]
    return [gate for gate in gates if gate is not None]


# --- stating them ----------------------------------------------------------------

def _figure(value: float) -> str:
    return str(int(value)) if value == int(value) else f"{value:g}"


def _number_re(value: float) -> re.Pattern[str]:
    figure = _figure(value)
    zeros = "0*" if "." in figure else r"(?:\.0+)?"
    return re.compile(rf"(?<![\d.]){re.escape(figure)}{zeros}(?!\.?\d)")


_RULE_WORDS: dict[GateKind, re.Pattern[str]] = {
    "complexity_limit": re.compile(r"complexity|\bC901\b|mccabe", re.IGNORECASE),
    "coverage_floor": re.compile(r"coverage", re.IGNORECASE),
    "pre_commit": re.compile(r"pre-commit", re.IGNORECASE),
    "type_strictness": re.compile(r"\bmypy\b", re.IGNORECASE),
}
_COVERAGE_FLOOR_WORDS = re.compile(r"fail_under|threshold|floor|minimum", re.IGNORECASE)
_STRICT_WORD = re.compile(r"strict", re.IGNORECASE)


def states(gate: Guardrail, line: str) -> bool:
    """True when one instruction-file ``line`` states ``gate``'s rule (and threshold)."""
    kind = gate["kind"]
    if not _RULE_WORDS[kind].search(line):
        return False
    if kind == "type_strictness":
        return bool(_STRICT_WORD.search(line))
    threshold = gate["threshold"]
    if kind == "pre_commit":
        return True
    if threshold is None:
        return kind != "coverage_floor" or bool(_COVERAGE_FLOOR_WORDS.search(line))
    figures = [threshold, threshold / 100] if kind == "coverage_floor" else [threshold]
    return any(_number_re(figure).search(line) for figure in figures)


def snippet(gate: Guardrail) -> str:
    """A one-line sentence an instruction file can take as written."""
    kind, tool, file = gate["kind"], gate["tool"], gate["file"]
    threshold = gate["threshold"]
    if kind == "complexity_limit":
        limit = _figure(threshold) if threshold is not None else "the configured limit"
        if tool == "ruff":
            return (f"Functions stay at or under cyclomatic complexity {limit} (ruff C901, "
                    f"`max-complexity` in `{file}`): run `ruff check .` before pushing.")
        return (f"Functions stay at or under cyclomatic complexity {limit} (flake8 "
                f"`max-complexity` in `{file}`): run `flake8` before pushing.")
    if kind == "coverage_floor":
        floor = (f"at or above {_figure(threshold)} percent" if threshold is not None
                 else "at or above the configured threshold")
        return (f"Test coverage stays {floor} ({tool} threshold in `{file}`): run the tests "
                f"with coverage before pushing; the run fails below it.")
    if kind == "pre_commit":
        return (f"Pre-commit hooks run on every commit (`{file}`): run "
                f"`pre-commit run --all-files` before pushing.")
    return (f"Type checking is strict (mypy `strict = true` in `{file}`): run `mypy` "
            f"before pushing.")


def _instruction_lines(root: Path, files: Iterable[str]) -> tuple[list[tuple[str, str]], int]:
    """``(file, line)`` for each line of each readable instruction file, and the file count."""
    lines: list[tuple[str, str]] = []
    seen: set[Path] = set()
    for rel in files:
        candidate = root / rel
        try:
            real = candidate.resolve()
        except (OSError, RuntimeError):
            continue
        text = _read_text(candidate)
        if text is None or real in seen:
            continue
        seen.add(real)
        lines.extend((rel, line) for line in text.splitlines())
    return lines, len(seen)


def scan_guardrails(repo_root: Path | str, instruction_files: Iterable[str],
                    coverage_gate: Mapping[str, Any] | None,
                    excludes: Excludes | None = None) -> GuardrailsBlock:
    """Return the ``guardrails`` run-context block for ``repo_root``.

    ``instruction_files`` are paths relative to ``repo_root`` (the keys of the
    core's ``instruction_files``); ``coverage_gate`` is that scan's block;
    ``excludes`` is the user's ``(dirs, patterns)`` pair.
    """
    root = Path(repo_root)
    gates = find_gates(root, coverage_gate, excludes)
    lines, read = _instruction_lines(root, instruction_files)
    for gate in gates:
        where = next((rel for rel, line in lines if states(gate, line)), None)
        gate["stated"] = where is not None
        gate["stated_in"] = where
        gate["snippet"] = None if where is not None else snippet(gate)
    stated = sum(1 for gate in gates if gate["stated"])
    return {
        "available": True,
        "gates": gates,
        "enforced": len(gates),
        "stated": stated,
        "unstated": len(gates) - stated,
        "stated_pct": round(100.0 * stated / len(gates), 1) if gates else None,
        "instruction_files_read": read,
        "not_read": list(NOT_READ),
    }
