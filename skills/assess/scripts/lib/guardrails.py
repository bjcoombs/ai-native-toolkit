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
  non-zero threshold, or a tool default), read once, never re-parsed. The
  highest ``threshold_percent`` is the gate's threshold.
- ``pre_commit``: a root ``.pre-commit-config.yaml`` (or ``.yml``) with at least
  one ``- id:`` hook. One gate per config, whatever its hook count.
- ``type_strictness``: mypy ``strict = true`` in ``[tool.mypy]`` of
  ``pyproject.toml`` or ``[mypy]`` of ``mypy.ini`` / ``setup.cfg``.

Not read yet (named in ``not_read``): eslint ``complexity``, TypeScript
``strict``, file-size ratchets, architecture or import-boundary tests, required
CI checks and branch protection, sign-off rules.

A gate is **stated** when one line of a graded instruction file (the keys of the
core's ``instruction_files``) names its rule and, where the gate has a threshold,
the threshold as a standalone number:

- ``complexity_limit``: ``complexity``, ``C901`` or ``mccabe`` and the limit;
- ``coverage_floor``: ``coverage`` and the threshold (as written or as a
  percentage); with no readable threshold, ``coverage`` beside ``fail_under``,
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
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any, Literal, TypedDict

from lib.coverage_gate import CoverageGate, gates_something

GateKind = Literal["complexity_limit", "coverage_floor", "pre_commit", "type_strictness"]

RUFF_DEFAULT_MAX_COMPLEXITY = 10
_C901_SELECTORS = frozenset({"C901", "C90", "C9", "C", "ALL"})
_RUFF_FILES = ("pyproject.toml", "ruff.toml", ".ruff.toml")
_FLAKE8_FILES = (".flake8", "setup.cfg", "tox.ini")
_MYPY_FILES = ("mypy.ini", "setup.cfg")
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


def _selects_c901(*tables: Mapping[str, Any]) -> bool:
    for table in tables:
        for key in ("select", "extend-select"):
            codes = table.get(key)
            if isinstance(codes, list) and any(str(code) in _C901_SELECTORS for code in codes):
                return True
    return False


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
    base = _table(data, "tool.ruff") if name == "pyproject.toml" else data
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


def _mypy_gate(root: Path) -> Guardrail | None:
    text = _read_text(root / "pyproject.toml")
    if text is not None:
        try:
            strict = _table(tomllib.loads(text), "tool.mypy").get("strict")
        except tomllib.TOMLDecodeError:
            strict = None
        if strict is True:
            return _gate("type_strictness", "mypy", "strict", None, "pyproject.toml",
                         _line_of(text, r"^\s*strict\s*="))
    for name in _MYPY_FILES:
        found = _ini_option(root, name, "mypy", "strict")
        if found is not None and found[0].lower() in {"true", "1", "yes", "on"}:
            return _gate("type_strictness", "mypy", "strict", None, name,
                         _line_of(found[1], r"^\s*strict\s*[=:]"))
    return None


def _pre_commit_gate(root: Path) -> Guardrail | None:
    for name in _PRE_COMMIT_FILES:
        text = _read_text(root / name)
        if text is not None and any(_HOOK_RE.match(line) for line in text.splitlines()):
            return _gate("pre_commit", "pre-commit", "hooks", None, name, None)
    return None


def _coverage_gate(block: Mapping[str, Any] | None) -> Guardrail | None:
    """One gate from the ``coverage_gate`` block: its counted gate with the highest percent."""
    if not isinstance(block, Mapping) or block.get("available") is False:
        return None
    gates: list[CoverageGate] = list(block.get("gates") or [])
    counted = [gate for gate in gates if gates_something(gate)]
    if not counted:
        return None
    best = max(counted, key=lambda gate: gate["threshold_percent"] or -1.0)
    return _gate("coverage_floor", best["tool"], "fail_under", best["threshold_percent"],
                 best["file"], best["line"])


def _gate(kind: GateKind, tool: str, rule: str, threshold: float | None,
          file: str, line: int | None) -> Guardrail:
    return {"kind": kind, "tool": tool, "rule": rule, "threshold": threshold, "file": file,
            "line": line, "stated": False, "stated_in": None, "snippet": None}


def find_gates(root: Path, coverage_gate: Mapping[str, Any] | None) -> list[Guardrail]:
    """Every gate this module reads at ``root``, in kind order then file order."""
    gates: list[Guardrail | None] = [_ruff_gate(root, name) for name in _RUFF_FILES]
    gates += [_flake8_gate(root, name) for name in _FLAKE8_FILES]
    gates += [_coverage_gate(coverage_gate), _pre_commit_gate(root), _mypy_gate(root)]
    return [gate for gate in gates if gate is not None]


# --- stating them ----------------------------------------------------------------

def _figure(value: float) -> str:
    return str(int(value)) if value == int(value) else f"{value:g}"


def _number_re(value: float) -> re.Pattern[str]:
    figure = re.escape(_figure(value))
    return re.compile(rf"(?<![\d.]){figure}(?:\.0+)?(?!\.?\d)")


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
    return bool(_number_re(threshold).search(line))


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
                    coverage_gate: Mapping[str, Any] | None) -> GuardrailsBlock:
    """Return the ``guardrails`` run-context block for ``repo_root``.

    ``instruction_files`` are paths relative to ``repo_root`` (the keys of the
    core's ``instruction_files``); ``coverage_gate`` is that scan's block.
    """
    root = Path(repo_root)
    gates = find_gates(root, coverage_gate)
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
