"""Keyhole-signal unit tests for one finding family.

Covers find_untrusted_hotspots (E1) and the test-to-code mapping (E2).

The pure, git-free derivation core; end-to-end wiring (build_run_context)
is covered in test_assess_core.py. Split from the former
test_keyhole_signals.py by finding family; shared integrate() fixtures live
in keyhole_helpers.py.
"""
from __future__ import annotations

from pathlib import Path

from lib import keyhole_signals as ks


# --- Task 5 E1: find_untrusted_hotspots --------------------------------------

def test_find_untrusted_hotspots_flags_high_survivor_density() -> None:
    complexity_stats = {"top_hotspots": [
        {"path": "src/hot.py"}, {"path": "src/cold.py"},
    ]}
    test_pressure = {"per_file": [
        {"file": "src/hot.py", "survived": 4, "total": 10},   # 0.4 >= 0.3
        {"file": "src/cold.py", "survived": 1, "total": 10},  # 0.1 < 0.3
    ]}
    assert ks.find_untrusted_hotspots(complexity_stats, test_pressure) == ["src/hot.py"]


def test_find_untrusted_hotspots_silent_without_mutation_data() -> None:
    complexity_stats = {"top_hotspots": [{"path": "src/hot.py"}]}
    # No per_file -> no mutation evidence -> nothing flagged (read-only default).
    assert ks.find_untrusted_hotspots(complexity_stats, {"per_file": []}) == []
    assert ks.find_untrusted_hotspots(complexity_stats, {}) == []
    assert ks.find_untrusted_hotspots(complexity_stats, None) == []


def test_find_untrusted_hotspots_only_flags_actual_hotspots() -> None:
    complexity_stats = {"top_hotspots": [{"path": "src/hot.py"}]}
    test_pressure = {"per_file": [
        {"file": "src/not_a_hotspot.py", "survived": 9, "total": 10},
    ]}
    assert ks.find_untrusted_hotspots(complexity_stats, test_pressure) == []


# --- Task 5 E2: test-to-code mapping -----------------------------------------

def test_build_test_to_code_map_finds_colocated_tests(tmp_path: Path) -> None:
    repo = tmp_path
    (repo / "pkg").mkdir()
    (repo / "pkg" / "svc.go").write_text("package pkg")
    (repo / "pkg" / "svc_test.go").write_text("package pkg")
    (repo / "pkg" / "lonely.go").write_text("package pkg")  # no sibling test
    mapping = ks.build_test_to_code_map(repo, ["pkg/svc.go", "pkg/lonely.go"])
    assert mapping == {"pkg/svc_test.go": "pkg/svc.go"}


def test_build_test_to_code_map_python_and_adjacent_dir(tmp_path: Path) -> None:
    repo = tmp_path
    (repo / "src").mkdir()
    (repo / "src" / "mod.py").write_text("x = 1")
    (repo / "src" / "tests").mkdir()
    (repo / "src" / "tests" / "test_mod.py").write_text("x = 1")
    mapping = ks.build_test_to_code_map(repo, ["src/mod.py"])
    assert mapping == {"src/tests/test_mod.py": "src/mod.py"}


def test_find_sibling_test_skips_test_files_themselves(tmp_path: Path) -> None:
    repo = tmp_path
    (repo / "foo_test.go").write_text("package x")
    assert ks._find_sibling_test(repo, "foo_test.go") is None
    assert ks.build_test_to_code_map(repo, ["foo_test.go"]) == {}
