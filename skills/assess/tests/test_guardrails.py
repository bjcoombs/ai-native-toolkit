"""Guardrails stated (issue #767): configured gates checked against the instruction files."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from lib.counts_only import counts_only
from lib.coverage_gate import detect_coverage_gate
from lib.guardrails import scan_guardrails
from lib.level_signals import load_default_levels
from lib.levels import evaluate

PYPROJECT = (
    "[project]\nname = 'x'\nversion = '0.1.0'\n\n"
    "[tool.ruff.lint]\nselect = ['C901']\n\n"
    "[tool.ruff.lint.mccabe]\nmax-complexity = 15\n\n"
    "[tool.coverage.report]\nfail_under = 90\n"
)
PRE_COMMIT = (
    "repos:\n  - repo: https://github.com/astral-sh/ruff-pre-commit\n"
    "    rev: v0.6.9\n    hooks:\n      - id: ruff\n"
)
AGENTS = "# Agents\n\nKeep test coverage at or above 90 percent (coverage fail_under = 90).\n"


def _write(root: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


def _scan(root: Path, instruction_files: tuple[str, ...] = ("AGENTS.md",)) -> dict[str, object]:
    block = scan_guardrails(root, instruction_files, detect_coverage_gate(root))
    return dict(block)


def _repo_g(tmp_path: Path) -> Path:
    return _write(tmp_path, {"pyproject.toml": PYPROJECT, ".pre-commit-config.yaml": PRE_COMMIT,
                             "AGENTS.md": AGENTS, "src/a.py": "x = 1\n"})


def test_the_issue_fixture_reports_one_of_three_gates_stated(tmp_path: Path) -> None:
    block = scan_guardrails(_repo_g(tmp_path), ["AGENTS.md"], detect_coverage_gate(tmp_path))
    by_kind = {gate["kind"]: gate for gate in block["gates"]}
    assert sorted(by_kind) == ["complexity_limit", "coverage_floor", "pre_commit"]
    assert (block["enforced"], block["stated"], block["unstated"]) == (3, 1, 2)
    assert block["stated_pct"] == 33.3
    coverage = by_kind["coverage_floor"]
    assert coverage["stated"] and coverage["stated_in"] == "AGENTS.md"
    assert coverage["snippet"] is None and coverage["threshold"] == 90
    complexity = by_kind["complexity_limit"]
    assert (complexity["tool"], complexity["rule"], complexity["threshold"]) == ("ruff", "C901", 15)
    assert complexity["file"] == "pyproject.toml" and complexity["line"] == 9
    assert not complexity["stated"]
    assert complexity["snippet"] is not None
    assert "complexity 15" in complexity["snippet"] and "C901" in complexity["snippet"]
    assert "ruff check" in complexity["snippet"]
    pre_commit = by_kind["pre_commit"]
    assert not pre_commit["stated"] and pre_commit["snippet"] is not None
    assert "pre-commit run --all-files" in pre_commit["snippet"]


def test_counts_only_keeps_kinds_and_counts_and_drops_text(tmp_path: Path) -> None:
    block = scan_guardrails(_repo_g(tmp_path), ["AGENTS.md"], detect_coverage_gate(tmp_path))
    kept = counts_only({"guardrails": dict(block)})["guardrails"]
    text = json.dumps(kept)
    assert "astral-sh" not in text and "90 percent" not in text and "ruff check" not in text
    assert [gate["kind"] for gate in kept["gates"]] == [
        "complexity_limit", "coverage_floor", "pre_commit"]
    assert [gate["stated"] for gate in kept["gates"]] == [False, True, False]
    # Every count survives; dropping any of these fields fails here.
    for field in ("enforced", "stated", "unstated", "stated_pct", "instruction_files_read"):
        assert kept[field] == block[field], field
    assert kept["not_read"] == len(block["not_read"])
    assert all("snippet" not in gate or gate["snippet"] is None for gate in kept["gates"])


def test_no_gate_reads_a_null_share(tmp_path: Path) -> None:
    root = _write(tmp_path, {"README.md": "# x\n", "src/a.py": "x = 1\n"})
    block = scan_guardrails(root, [], detect_coverage_gate(root))
    assert block["gates"] == [] and block["enforced"] == 0
    assert block["stated_pct"] is None


@pytest.mark.parametrize(("line", "stated"), [
    ("Functions stay under complexity 15 (ruff C901).", True),
    ("Keep mccabe at 15.", True),
    ("Complexity is capped at 150.", False),          # a different number
    ("Run 15 tests before pushing.", False),          # the number without the rule
    ("Keep complexity low.", False),                  # the rule without the number
])
def test_complexity_needs_the_rule_and_the_limit_on_one_line(
        tmp_path: Path, line: str, stated: bool) -> None:
    root = _write(tmp_path, {"pyproject.toml": PYPROJECT, "CLAUDE.md": f"# P\n\n{line}\n"})
    gates = scan_guardrails(root, ["CLAUDE.md"], None)["gates"]
    assert [gate["kind"] for gate in gates] == ["complexity_limit"]
    assert gates[0]["stated"] is stated


def test_ruff_without_c901_is_not_a_complexity_gate(tmp_path: Path) -> None:
    root = _write(tmp_path, {"pyproject.toml": "[tool.ruff.lint]\nselect = ['E', 'F']\n"
                                               "[tool.ruff.lint.mccabe]\nmax-complexity = 5\n"})
    assert scan_guardrails(root, [], None)["gates"] == []


def test_ruff_toml_with_c901_and_no_limit_reads_the_default(tmp_path: Path) -> None:
    root = _write(tmp_path, {"ruff.toml": "[lint]\nextend-select = ['C90']\n"})
    (gate,) = scan_guardrails(root, [], None)["gates"]
    assert (gate["tool"], gate["threshold"], gate["file"]) == ("ruff", 10, "ruff.toml")


def test_flake8_and_mypy_strict_are_read(tmp_path: Path) -> None:
    root = _write(tmp_path, {
        "setup.cfg": "[flake8]\nmax-complexity = 12\n\n[mypy]\nstrict = True\n",
        "AGENTS.md": "mypy runs in strict mode.\n",
    })
    gates = scan_guardrails(root, ["AGENTS.md"], None)["gates"]
    by_kind = {gate["kind"]: gate for gate in gates}
    assert by_kind["complexity_limit"]["tool"] == "flake8"
    assert by_kind["complexity_limit"]["threshold"] == 12
    assert by_kind["complexity_limit"]["line"] == 2
    assert by_kind["type_strictness"]["file"] == "setup.cfg"
    assert by_kind["type_strictness"]["stated"] is True
    assert by_kind["complexity_limit"]["stated"] is False


def test_mypy_strict_in_pyproject(tmp_path: Path) -> None:
    root = _write(tmp_path, {"pyproject.toml": "[tool.mypy]\nstrict = true\n"})
    (gate,) = scan_guardrails(root, [], None)["gates"]
    assert (gate["kind"], gate["line"]) == ("type_strictness", 2)
    assert gate["snippet"] is not None and "`mypy`" in gate["snippet"]


def test_a_zero_coverage_floor_and_an_empty_pre_commit_are_not_gates(tmp_path: Path) -> None:
    root = _write(tmp_path, {
        "pyproject.toml": "[tool.coverage.report]\nfail_under = 0\n",
        ".pre-commit-config.yaml": "repos: []\n",
    })
    assert scan_guardrails(root, [], detect_coverage_gate(root))["gates"] == []


def test_an_unavailable_coverage_block_adds_no_gate(tmp_path: Path) -> None:
    root = _write(tmp_path, {"pyproject.toml": "[tool.coverage.report]\nfail_under = 80\n"})
    assert scan_guardrails(root, [], {"available": False, "reason": "boom"})["gates"] == []


def test_a_coverage_floor_without_a_readable_threshold_needs_a_floor_word(tmp_path: Path) -> None:
    coverage = {"available": True, "enforced": True, "gates": [
        {"file": "package.json", "line": 3, "tool": "nyc", "form": "check-coverage",
         "threshold": None, "unit": "percent", "threshold_percent": None, "metric": None}]}
    root = _write(tmp_path, {"A.md": "Coverage has a minimum.\n", "B.md": "We like coverage.\n"})
    (gate,) = scan_guardrails(root, ["B.md", "A.md"], coverage)["gates"]
    assert gate["stated_in"] == "A.md"
    assert gate["threshold"] is None


def test_an_aliased_instruction_file_is_read_once(tmp_path: Path) -> None:
    root = _write(tmp_path, {"CLAUDE.md": "nothing\n"})
    (root / "AGENTS.md").symlink_to("CLAUDE.md")
    assert scan_guardrails(root, ["CLAUDE.md", "AGENTS.md", "missing.md"], None)[
        "instruction_files_read"] == 1


def test_unparseable_config_is_skipped(tmp_path: Path) -> None:
    root = _write(tmp_path, {"pyproject.toml": "[tool.ruff\n", "setup.cfg": "no header\n",
                             "ruff.toml": "select = ['C901']\n"})
    (gate,) = scan_guardrails(root, [], None)["gates"]
    assert gate["file"] == "ruff.toml"


def _row(scans: dict[str, Any]) -> dict[str, Any]:
    result = evaluate(load_default_levels(), {"name": "g", "settings": None, "repos": {"r": scans}})
    return next(r for r in result["rows"] if r["id"] == "guardrails-stated")


@pytest.mark.parametrize(("stated", "level"), [(0, 0), (1, 1), (2, 2)])
def test_the_guardrails_stated_row(stated: int, level: int) -> None:
    row = _row({"guardrails": {"available": True, "stated_pct": 100.0 * stated / 2}})
    assert (row["status"], row["level"]) == ("level", level)


def test_the_guardrails_stated_row_is_unknown_with_no_gate() -> None:
    assert _row({"guardrails": {"available": True, "stated_pct": None}})["status"] == "unknown"


def test_ruff_all_with_c901_ignored_is_not_a_complexity_gate(tmp_path: Path) -> None:
    root = _write(tmp_path, {"pyproject.toml": "[tool.ruff.lint]\nselect = ['ALL']\n"
                                               "ignore = ['C901']\n"})
    assert scan_guardrails(root, [], None)["gates"] == []


@pytest.mark.parametrize("line", ["Coverage minimum is 0.80 (JaCoCo).", "Keep coverage at 80%."])
def test_a_ratio_floor_is_stated_as_a_ratio_or_a_percentage(tmp_path: Path, line: str) -> None:
    coverage = {"available": True, "enforced": True, "gates": [
        {"file": "pom.xml", "line": 9, "tool": "jacoco", "form": "minimum", "threshold": 0.8,
         "unit": "ratio", "threshold_percent": 80.0, "metric": "lines"}]}
    root = _write(tmp_path, {"AGENTS.md": f"{line}\n"})
    (gate,) = scan_guardrails(root, ["AGENTS.md"], coverage)["gates"]
    assert (gate["rule"], gate["threshold"], gate["stated"]) == ("threshold", 80.0, True)


def test_package_configs_are_gates_of_their_own(tmp_path: Path) -> None:
    root = _write(tmp_path, {
        "pkg/pyproject.toml": PYPROJECT + "\n[tool.mypy]\nstrict = true\n",
        "node_modules/dep/pyproject.toml": PYPROJECT,
        "CLAUDE.md": "Keep coverage at or above 90 percent.\n",
    })
    block = scan_guardrails(root, ["CLAUDE.md"], detect_coverage_gate(root), (set(), []))
    assert [(g["kind"], g["file"]) for g in block["gates"]] == [
        ("complexity_limit", "pkg/pyproject.toml"), ("coverage_floor", "pkg/pyproject.toml"),
        ("type_strictness", "pkg/pyproject.toml")]
    assert (block["stated"], block["stated_pct"]) == (1, 33.3)
    excluded = scan_guardrails(root, [], detect_coverage_gate(root), ({"pkg"}, []))
    assert [g["kind"] for g in excluded["gates"]] == ["coverage_floor"]


def test_only_the_config_the_tool_reads_in_a_directory_counts(tmp_path: Path) -> None:
    root = _write(tmp_path, {
        # ruff reads ruff.toml before pyproject.toml: its C901-free config wins.
        "ruff.toml": "[lint]\nselect = ['E']\n",
        # mypy reads mypy.ini before pyproject.toml: one strict gate, from mypy.ini.
        "mypy.ini": "[mypy]\nstrict = True\n",
        "pyproject.toml": PYPROJECT + "\n[tool.mypy]\nstrict = true\n",
        # flake8 reads setup.cfg before .flake8.
        "pkg/setup.cfg": "[flake8]\nmax-complexity = 8\n",
        "pkg/.flake8": "[flake8]\nmax-complexity = 30\n",
        "lib/.mypy.ini": "[mypy]\nstrict = true\n",
    })
    gates = scan_guardrails(root, [], None)["gates"]
    assert [(g["kind"], g["tool"], g["file"], g["threshold"]) for g in gates] == [
        ("complexity_limit", "flake8", "pkg/setup.cfg", 8),
        ("type_strictness", "mypy", "mypy.ini", None),
        ("type_strictness", "mypy", "lib/.mypy.ini", None),
    ]


def test_a_pyproject_without_tool_ruff_yields_to_nothing_else(tmp_path: Path) -> None:
    root = _write(tmp_path, {"pyproject.toml": "[project]\nname = 'x'\n",
                             ".ruff.toml": "select = ['C901']\n"})
    (gate,) = scan_guardrails(root, [], None)["gates"]
    assert (gate["file"], gate["threshold"]) == (".ruff.toml", 10)


def test_each_file_with_a_coverage_floor_is_one_gate(tmp_path: Path) -> None:
    def cov(file: str, pct: float) -> dict[str, object]:
        return {"file": file, "line": 1, "tool": "coverage.py", "form": "fail_under",
                "threshold": pct, "unit": "percent", "threshold_percent": pct, "metric": "lines"}
    block = {"available": True, "enforced": True,
             "gates": [cov("a/pyproject.toml", 80), cov("a/pyproject.toml", 90),
                       cov("b/pyproject.toml", 70)]}
    gates = scan_guardrails(tmp_path, [], block)["gates"]
    assert [(g["file"], g["threshold"]) for g in gates] == [
        ("a/pyproject.toml", 90), ("b/pyproject.toml", 70)]
