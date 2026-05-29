"""Tests for Layer 1 write-side truth pressure: mutation tier + cheap heuristics."""
from __future__ import annotations

import subprocess
from pathlib import Path

import lib.test_pressure as tp
from lib.test_pressure import (
    compute_cheap_heuristics,
    compute_gap_signal,
    compute_survivor_density,
    detect_assertion_on_internal,
    detect_duplicate_truth,
    detect_mutation_config,
    detect_untested_boundaries,
    identify_survivor_clusters,
    run_bounded_mutation,
    scan_test_pressure,
    _parse_cargo_mutants,
    _parse_gremlins,
    _parse_mutmut,
    _parse_stryker_json,
)


def _write(root: Path, rel: str, text: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


# ════════════════════════════════════════════════════════════════════════════
# TASK 2 - mutation config detection
# ════════════════════════════════════════════════════════════════════════════

def test_mutation_config_none(tmp_path: Path) -> None:
    _write(tmp_path, "app.py", "x = 1")
    r = detect_mutation_config(tmp_path)
    assert r["present"] is False
    assert r["tools"] == []
    assert r["ci_integrated"] is False


def test_mutation_config_stryker_file(tmp_path: Path) -> None:
    _write(tmp_path, "stryker.conf.json", "{}")
    r = detect_mutation_config(tmp_path)
    assert r["present"] is True
    assert "stryker" in r["tools"]
    assert r["ci_integrated"] is False


def test_mutation_config_mutmut_dotfile_and_pyproject(tmp_path: Path) -> None:
    _write(tmp_path, ".mutmut.toml", "")
    r = detect_mutation_config(tmp_path)
    assert "mutmut" in r["tools"]

    other = tmp_path / "proj2"
    _write(other, "pyproject.toml", "[tool.mutmut]\npaths_to_mutate = 'src/'")
    r2 = detect_mutation_config(other)
    assert "mutmut" in r2["tools"]


def test_mutation_config_cosmic_ray(tmp_path: Path) -> None:
    _write(tmp_path, "cosmic-ray.toml", "[cosmic-ray]")
    assert "cosmic-ray" in detect_mutation_config(tmp_path)["tools"]


def test_mutation_config_cargo_mutants(tmp_path: Path) -> None:
    _write(tmp_path, "Cargo.toml",
           "[dev-dependencies]\n# uses cargo-mutants in CI\n")
    assert "cargo-mutants" in detect_mutation_config(tmp_path)["tools"]


def test_mutation_config_ci_integration(tmp_path: Path) -> None:
    """A CI file that invokes a mutation tool sets ci_integrated even with no
    config file present."""
    _write(tmp_path, ".github/workflows/mutation.yml",
           "jobs:\n  mut:\n    steps:\n      - run: npx stryker run\n")
    r = detect_mutation_config(tmp_path)
    assert r["present"] is True
    assert r["ci_integrated"] is True
    assert "stryker" in r["tools"]


def test_mutation_config_gremlins_in_gitlab_ci(tmp_path: Path) -> None:
    _write(tmp_path, ".gitlab-ci.yml", "mutation:\n  script:\n    - gremlins unleash\n")
    r = detect_mutation_config(tmp_path)
    assert r["ci_integrated"] is True
    assert "gremlins" in r["tools"]


def test_mutation_config_jenkinsfile(tmp_path: Path) -> None:
    _write(tmp_path, "Jenkinsfile", "sh 'cargo mutants'")
    r = detect_mutation_config(tmp_path)
    assert "cargo-mutants" in r["tools"]
    assert r["ci_integrated"] is True


# ════════════════════════════════════════════════════════════════════════════
# TASK 2 - mutation output parsers
# ════════════════════════════════════════════════════════════════════════════

def test_parse_stryker_json() -> None:
    out = (
        '{"files": {"src/a.ts": {"mutants": ['
        '{"status": "Killed"}, {"status": "Survived"}, {"status": "Survived"}]}}}'
    )
    parsed = _parse_stryker_json(out)
    assert len(parsed) == 1
    assert parsed[0]["file"] == "src/a.ts"
    assert parsed[0]["killed"] == 1
    assert parsed[0]["survived"] == 2
    assert parsed[0]["total"] == 3


def test_parse_stryker_json_garbage_degrades() -> None:
    assert _parse_stryker_json("not json") == []


def test_parse_mutmut_survivors_only() -> None:
    out = "src/foo.py:12\nsrc/foo.py:15\nsrc/bar.py:7\n"
    parsed = {p["file"]: p for p in _parse_mutmut(out)}
    assert parsed["src/foo.py"]["survived"] == 2
    assert parsed["src/foo.py"]["total"] is None  # mutmut only lists survivors
    assert parsed["src/bar.py"]["survived"] == 1


def test_parse_gremlins() -> None:
    out = (
        "KILLED   pkg/x.go:10:2\n"
        "LIVED    pkg/x.go:12:4\n"
        "NOT COVERED pkg/y.go:3:1\n"
    )
    by_file = {p["file"]: p for p in _parse_gremlins(out)}
    assert by_file["pkg/x.go"]["killed"] == 1
    assert by_file["pkg/x.go"]["survived"] == 1
    assert by_file["pkg/y.go"]["survived"] == 1


def test_parse_cargo_mutants() -> None:
    out = (
        "MISSED   src/lib.rs:10:5: replace foo -> bar\n"
        "CAUGHT   src/lib.rs:12:1: replace baz\n"
        "UNVIABLE src/lib.rs:14:1: nope\n"
    )
    by_file = {p["file"]: p for p in _parse_cargo_mutants(out)}
    assert by_file["src/lib.rs"]["survived"] == 1  # MISSED
    assert by_file["src/lib.rs"]["killed"] == 1    # CAUGHT, UNVIABLE ignored
    assert by_file["src/lib.rs"]["total"] == 2


# ════════════════════════════════════════════════════════════════════════════
# TASK 2 - bounded mutation run
# ════════════════════════════════════════════════════════════════════════════

def test_run_bounded_mutation_requires_opt_in(tmp_path: Path) -> None:
    _write(tmp_path, "app.py", "x = 1")
    r = run_bounded_mutation(tmp_path, hot_files=["app.py"], opt_in=False)
    assert r["mutation_run"] is False
    assert r["available"] is False
    assert "opt-in" in r["reason"]


def test_run_bounded_mutation_tool_absent(tmp_path: Path, monkeypatch) -> None:
    _write(tmp_path, "app.py", "x = 1")
    monkeypatch.setattr(tp.shutil, "which", lambda _t: None)
    r = run_bounded_mutation(tmp_path, hot_files=["app.py"], opt_in=True)
    assert r["mutation_run"] is False
    assert r["available"] is False


def test_run_bounded_mutation_runs_and_parses(tmp_path: Path, monkeypatch) -> None:
    _write(tmp_path, "app.py", "def f(): pass")
    monkeypatch.setattr(tp.shutil, "which", lambda t: "/usr/bin/" + t)

    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(
            cmd, 0, stdout="app.py:3\napp.py:9\n", stderr="")

    monkeypatch.setattr(tp.subprocess, "run", fake_run)
    r = run_bounded_mutation(tmp_path, hot_files=["app.py"], opt_in=True)
    assert r["mutation_run"] is True
    assert r["tool"] == "mutmut"
    assert r["per_file"][0]["survived"] == 2


def test_run_bounded_mutation_caps_scope(tmp_path: Path, monkeypatch) -> None:
    _write(tmp_path, "app.py", "x = 1")
    monkeypatch.setattr(tp.shutil, "which", lambda t: "/usr/bin/" + t)
    monkeypatch.setattr(
        tp.subprocess, "run",
        lambda cmd, **k: subprocess.CompletedProcess(cmd, 0, stdout="", stderr=""))
    many = [f"f{i}.py" for i in range(20)]
    r = run_bounded_mutation(tmp_path, hot_files=many, opt_in=True)
    assert len(r["scope"]) == tp.MAX_FILES_TO_MUTATE


def test_run_bounded_mutation_timeout_degrades(tmp_path: Path, monkeypatch) -> None:
    _write(tmp_path, "app.py", "x = 1")
    monkeypatch.setattr(tp.shutil, "which", lambda t: "/usr/bin/" + t)

    def fake_run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, tp.MUTATION_TIMEOUT)

    monkeypatch.setattr(tp.subprocess, "run", fake_run)
    r = run_bounded_mutation(tmp_path, hot_files=["app.py"], opt_in=True)
    assert r["mutation_run"] is False
    assert "timeout" in r["reason"].lower()
    assert r["per_file"] == []


def test_run_bounded_mutation_prefers_detected_tool(tmp_path: Path, monkeypatch) -> None:
    """When a stryker config is present and both go+ts source exist, the
    config-detected tool wins over a merely-language-present one."""
    _write(tmp_path, "src/a.ts", "export const x = 1;")
    _write(tmp_path, "main.go", "package main")
    _write(tmp_path, "stryker.conf.json", "{}")
    monkeypatch.setattr(tp.shutil, "which", lambda t: "/usr/bin/" + t)
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="{}", stderr="")

    monkeypatch.setattr(tp.subprocess, "run", fake_run)
    r = run_bounded_mutation(tmp_path, hot_files=["src/a.ts"], opt_in=True)
    assert r["tool"] == "stryker"
    assert captured["cmd"][0] == "stryker"


# ════════════════════════════════════════════════════════════════════════════
# TASK 2 - aggregation
# ════════════════════════════════════════════════════════════════════════════

def test_compute_survivor_density_with_totals() -> None:
    per_file = [
        {"file": "a.ts", "killed": 8, "survived": 2, "total": 10},
        {"file": "b.ts", "killed": 5, "survived": 5, "total": 10},
    ]
    d = compute_survivor_density(per_file)
    assert d["total_survived"] == 7
    assert d["total_mutants"] == 20
    assert abs(d["overall"] - 0.35) < 1e-9
    assert d["by_file"]["b.ts"] == 5


def test_compute_survivor_density_no_totals_is_none() -> None:
    per_file = [{"file": "x.py", "killed": None, "survived": 3, "total": None}]
    d = compute_survivor_density(per_file)
    assert d["overall"] is None
    assert d["total_mutants"] is None
    assert d["total_survived"] == 3


def test_identify_survivor_clusters() -> None:
    per_file = [
        {"file": "hot.py", "survived": 7},
        {"file": "warm.py", "survived": 3},
        {"file": "cool.py", "survived": 1},
    ]
    clusters = identify_survivor_clusters(per_file)
    assert [c["file"] for c in clusters] == ["hot.py", "warm.py"]  # cool below threshold
    assert clusters[0]["survived"] == 7  # sorted descending


def test_compute_gap_signal_variants() -> None:
    assert compute_gap_signal(0.9, 0.3) == "high coverage + low mutation score"
    assert compute_gap_signal(0.9, 0.8) == "no gap"
    assert compute_gap_signal(None, 0.3) == "not assessed"
    assert compute_gap_signal(0.9, None) == "not assessed"


# ════════════════════════════════════════════════════════════════════════════
# TASK 3 - assertion on internal
# ════════════════════════════════════════════════════════════════════════════

def test_assertion_on_internal_flags_private_only(tmp_path: Path) -> None:
    """The hollow fingerprint: asserts on a private field, no public assertion."""
    _write(tmp_path, "test_guard.py",
           "def test_resume():\n"
           "    g = Guard()\n"
           "    assert g._resume_count == 1\n")
    findings = detect_assertion_on_internal(tmp_path)
    assert len(findings) == 1
    assert findings[0]["internal_field"] == "_resume_count"
    assert findings[0]["confidence"] == "medium"


def test_assertion_on_internal_honest_test_not_flagged(tmp_path: Path) -> None:
    """A test that also asserts on a public attribute is honest - not flagged."""
    _write(tmp_path, "test_guard.py",
           "def test_resume():\n"
           "    g = Guard()\n"
           "    assert g._resume_count == 1\n"
           "    assert g.status == 'ok'\n")
    assert detect_assertion_on_internal(tmp_path) == []


def test_assertion_on_internal_unittest_style(tmp_path: Path) -> None:
    _write(tmp_path, "test_svc_test.py",
           "class T:\n"
           "    def test_it(self):\n"
           "        self.assertEqual(svc._cache, {})\n")
    findings = detect_assertion_on_internal(tmp_path)
    assert findings and findings[0]["internal_field"] == "_cache"


def test_assertion_on_internal_dunder_not_flagged(tmp_path: Path) -> None:
    """Dunders (__len__ etc.) are protocol, not private internals."""
    _write(tmp_path, "test_x.py",
           "def test_len():\n    assert obj.__len__() == 3\n")
    assert detect_assertion_on_internal(tmp_path) == []


def test_assertion_on_internal_ts_private(tmp_path: Path) -> None:
    _write(tmp_path, "guard.test.ts",
           "it('guards', () => { expect(g._resumeCount).toBe(1); });\n")
    findings = detect_assertion_on_internal(tmp_path)
    assert findings and findings[0]["confidence"] == "low"


def test_assertion_on_internal_skips_non_test_files(tmp_path: Path) -> None:
    """A production file with `_field` access is not a test - not scanned."""
    _write(tmp_path, "guard.py", "assert g._x == 1\n")
    assert detect_assertion_on_internal(tmp_path) == []


def test_assertion_on_internal_degrades_on_syntax_error(tmp_path: Path) -> None:
    _write(tmp_path, "test_broken.py", "def test_(:\n  assert x._y\n")
    assert detect_assertion_on_internal(tmp_path) == []  # no crash


# ════════════════════════════════════════════════════════════════════════════
# TASK 3 - untested boundaries
# ════════════════════════════════════════════════════════════════════════════

def test_untested_boundaries_requires_coverage(tmp_path: Path) -> None:
    _write(tmp_path, "app.py", "def f(n):\n    return n <= 10\n")
    assert detect_untested_boundaries(tmp_path, coverage_data=None) == []


def test_untested_boundaries_python_compare(tmp_path: Path) -> None:
    _write(tmp_path, "app.py", "def f(n):\n    if n <= 10:\n        return n + 1\n")
    cov = {"app.py": [2, 3]}  # both lines covered
    findings = detect_untested_boundaries(tmp_path, coverage_data=cov)
    ops = {f["operator"] for f in findings}
    assert "<=" in ops
    assert "+1" in ops
    assert all(f["boundary_tested"] is False for f in findings)  # candidate only


def test_untested_boundaries_only_covered_lines(tmp_path: Path) -> None:
    _write(tmp_path, "app.py",
           "def f(n):\n    if n < 5:\n        return 0\n    if n > 9:\n        return 1\n")
    cov = {"app.py": [2]}  # only the first comparison line covered
    findings = detect_untested_boundaries(tmp_path, coverage_data=cov)
    lines = {f["line"] for f in findings}
    assert lines == {2}  # line 4 (n > 9) not covered, excluded


def test_untested_boundaries_coverage_dict_form(tmp_path: Path) -> None:
    """Coverage as {file: {line: hits}} with a zero-hit line excluded."""
    _write(tmp_path, "app.go", "func f(n int) bool {\n    return n >= 3\n}\n")
    cov = {"app.go": {2: 5, 1: 0}}
    findings = detect_untested_boundaries(tmp_path, coverage_data=cov)
    assert findings and findings[0]["operator"] == ">="


def test_untested_boundaries_skips_test_files(tmp_path: Path) -> None:
    _write(tmp_path, "app_test.go", "func TestF(t *testing.T) {\n    if n <= 1 {}\n}\n")
    cov = {"app_test.go": [2]}
    assert detect_untested_boundaries(tmp_path, coverage_data=cov) == []


# ════════════════════════════════════════════════════════════════════════════
# TASK 3 - duplicate truth
# ════════════════════════════════════════════════════════════════════════════

def test_duplicate_truth_python_direct_copy(tmp_path: Path) -> None:
    _write(tmp_path, "model.py",
           "class M:\n"
           "    def __init__(self, balance):\n"
           "        self.balance = balance\n"
           "        self.shadow = self.balance\n")
    findings = detect_duplicate_truth(tmp_path)
    dup = [f for f in findings if f["field_name"] == "shadow"]
    assert dup and dup[0]["derives_from"] == "balance"


def test_duplicate_truth_python_offset(tmp_path: Path) -> None:
    _write(tmp_path, "model.py",
           "class M:\n"
           "    def shift(self):\n"
           "        self.next_index = self.index + 1\n")
    findings = detect_duplicate_truth(tmp_path)
    assert any(f["field_name"] == "next_index"
               and f["derives_from"] == "index" for f in findings)


def test_duplicate_truth_independent_computation_not_flagged(tmp_path: Path) -> None:
    """A field also assigned from an independent computation is honest."""
    _write(tmp_path, "model.py",
           "class M:\n"
           "    def calc(self, items):\n"
           "        self.total = self.base\n"
           "        self.total = sum(items)\n")  # second assignment is independent
    findings = detect_duplicate_truth(tmp_path)
    assert all(f["field_name"] != "total" for f in findings)


def test_duplicate_truth_ts(tmp_path: Path) -> None:
    _write(tmp_path, "model.ts",
           "class M {\n  update() {\n    this.mirror = this.source;\n  }\n}\n")
    findings = detect_duplicate_truth(tmp_path)
    assert any(f["field_name"] == "mirror"
               and f["derives_from"] == "source" for f in findings)


def test_duplicate_truth_degrades_on_syntax_error(tmp_path: Path) -> None:
    _write(tmp_path, "broken.py", "class M(:\n  x =\n")
    assert detect_duplicate_truth(tmp_path) == []  # no crash


# ════════════════════════════════════════════════════════════════════════════
# Aggregation + public entry point
# ════════════════════════════════════════════════════════════════════════════

def test_compute_cheap_heuristics_shape(tmp_path: Path) -> None:
    _write(tmp_path, "test_x.py", "def test_a():\n    assert o._p == 1\n")
    r = compute_cheap_heuristics(tmp_path)
    assert set(r) == {
        "assertion_on_internal", "untested_boundaries",
        "duplicate_truth", "confidence_note",
    }
    assert r["confidence_note"] == tp.CHEAP_HEURISTIC_NOTE
    assert r["assertion_on_internal"]  # the hollow test was flagged


def test_scan_test_pressure_full_block(tmp_path: Path) -> None:
    _write(tmp_path, "stryker.conf.json", "{}")
    _write(tmp_path, "src/a.ts", "export const x = 1;")
    _write(tmp_path, "guard.test.ts",
           "it('x', () => { expect(g._secret).toBe(1); });\n")
    block = scan_test_pressure(tmp_path, hot_files=None, opt_in=False)
    expected_keys = {
        "mutation_config_present", "mutation_tools_detected", "ci_integrated",
        "mutation_run", "mutation_scope", "per_file", "survivor_density",
        "survivor_clusters", "gap_signal", "cheap_heuristics",
    }
    assert expected_keys <= set(block)
    assert block["mutation_config_present"] is True
    assert "stryker" in block["mutation_tools_detected"]
    assert block["mutation_run"] is False         # opt_in defaulted off
    assert block["gap_signal"] == "not assessed"  # no coverage / no run
    assert block["cheap_heuristics"]["assertion_on_internal"]  # ts hollow test


def test_scan_test_pressure_never_raises_on_empty(tmp_path: Path) -> None:
    block = scan_test_pressure(tmp_path)
    assert block["mutation_config_present"] is False
    assert block["per_file"] == []
    assert block["survivor_clusters"] == []
