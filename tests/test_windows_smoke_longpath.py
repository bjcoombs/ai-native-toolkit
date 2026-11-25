"""The long-path fixture and checker behind the Windows smoke step (#1405)."""
import importlib.util
import json
import os
import subprocess
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

SCRIPT = Path(__file__).resolve().parent / "fixtures" / "windows_smoke" / "longpath.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("longpath", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


LP = _load()
REL = "pkg/a/b/long_module.py"
MODULE = "pkg.a.b.long_module"


def _signals(ccn_max: float, modules: list[str], todo_bare: int) -> dict[str, Any]:
    return {"scans": {
        "stats_summary": {"available": True, "ccn": {"max": ccn_max}},
        "structure": {"available": True, "footprints": [{"module": m} for m in modules]},
        "promissory_markers": {"available": True, "todo_bare": todo_bare},
    }}


def _context(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    return {"dead_code": {"available": True, "candidates": candidates,
                          "tools": [{"tool": "vulture", "status": "ran"}]}}


READ_ALL = (
    _signals(LP.FILE_COMPLEXITY, ["pkg", MODULE], 1),
    _context([{"path": REL, "symbol": LP.LONG_FUNCTION}]),
)


def test_build_tracks_a_module_past_the_limit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    co = tmp_path / "acme" / "deep" / "deep-main"
    rel = LP.build(co)
    assert rel == LP.long_rel(co)
    assert len(os.path.abspath(co / rel)) > LP.LIMIT
    assert rel.startswith("pkg/") and rel.endswith("/" + LP.LONG_MODULE)
    config = subprocess.run(["git", "-C", str(co), "config", "--get", "core.longpaths"],
                            capture_output=True, encoding="utf-8", check=True).stdout
    assert config.strip() == "true"
    # Every directory on the way is a package, so grimp can name the module.
    for depth in range(1, rel.count("/") + 1):
        assert (co.joinpath(*rel.split("/")[:depth]) / "__init__.py").is_file()
    assert "# TODO" in (co / rel).read_text(encoding="utf-8")


def test_every_scanner_reads_the_long_file() -> None:
    results = LP.judge(REL, *READ_ALL)
    assert {s: o for s, (o, _) in results.items()} == {
        "lizard": "read", "grimp": "read", "vulture": "read", "rg": "read"}


def test_a_reading_without_the_long_file_is_missed() -> None:
    signals = _signals(3.0, ["pkg"], 0)
    results = LP.judge(REL, signals, _context([{"path": "app.py", "symbol": "main"}]))
    assert {o for o, _ in results.values()} == {"MISSED"}


def test_an_unavailable_block_is_not_measured_with_its_reason() -> None:
    signals: dict[str, Any] = {"scans": {
        "stats_summary": {"available": False, "reason": "lizard not installed"},
        "promissory_markers": {"available": False, "reason": "rg not found"},
    }}
    results = LP.judge(REL, signals, {})
    assert results["lizard"] == ("not measured", "stats_summary unavailable: lizard not installed")
    assert results["rg"] == ("not measured", "promissory_markers unavailable: rg not found")
    assert results["grimp"] == ("not measured", "no structure block")
    assert results["vulture"] == ("not measured", "no dead_code block")


def test_a_known_gap_is_named_with_its_issue(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(LP, "KNOWN_GAPS", {"grimp": "#9999"})
    signals = _signals(LP.FILE_COMPLEXITY, ["pkg"], 1)
    outcome, detail = LP.judge(REL, signals, READ_ALL[1])["grimp"]
    assert outcome == "known gap"
    assert detail.endswith("tracked in #9999")


def _write_docs(tmp_path: Path, signals: dict[str, Any], context: dict[str, Any]) -> tuple[Path, Path]:
    s, c = tmp_path / "signals.json", tmp_path / "run-context.json"
    s.write_text(json.dumps(signals), encoding="utf-8")
    c.write_text(json.dumps(context), encoding="utf-8")
    return s, c


def test_check_fails_only_on_a_missed_scanner(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    co = tmp_path / "co"
    rel = LP.long_rel(co)
    module = rel.removesuffix(".py").replace("/", ".")
    good = (_signals(LP.FILE_COMPLEXITY, [module], 1), _context([{"path": rel, "symbol": LP.LONG_FUNCTION}]))
    assert LP.check(co, *_write_docs(tmp_path, *good)) == 0
    assert "lizard: read" in capsys.readouterr().out
    unavailable: dict[str, Any] = {"scans": {}}
    assert LP.check(co, *_write_docs(tmp_path, unavailable, {})) == 0
    bad = (_signals(1.0, [module], 1), good[1])
    assert LP.check(co, *_write_docs(tmp_path, *bad)) == 1
    assert "scanners that left out the long file: lizard" in capsys.readouterr().out


def test_main_refuses_unknown_arguments() -> None:
    assert LP.main([]) == 2
    assert LP.main(["check", "a"]) == 2


def test_an_unavailable_dead_code_block_names_its_tool_reasons() -> None:
    context = {"dead_code": {"available": False, "candidates": [],
                             "tools": [{"tool": "vulture", "status": "absent", "reason": "vulture not on PATH"}]}}
    assert LP.judge(REL, READ_ALL[0], context)["vulture"] == (
        "not measured", "dead_code unavailable: vulture: vulture not on PATH")


def test_a_miss_names_where_the_function_was_reported() -> None:
    context = _context([{"path": REL.replace("/", "\\"), "symbol": LP.LONG_FUNCTION}])
    outcome, detail = LP.judge(REL, READ_ALL[0], context)["vulture"]
    assert outcome == "MISSED"
    assert detail.endswith("classify reported at pkg\\a\\b\\long_module.py")
