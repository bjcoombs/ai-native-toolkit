"""The opt-in mutation pass rebuilds every block derived from test_pressure.

``assess_core --opt-in-mutation`` used to rewrite only ``test_pressure`` and the
Layer 6 cap, so the E1 ``untrusted_hotspot`` finding (and the attention list,
report markdown, summary, prescribed actions and badge built from the findings)
kept the mutation-off values and E1 could never fire in a real run.

Edge cases covered: survivors over the threshold on a hotspot (end to end, and
equal to a default run fed the same block), all killed, a second pass clearing
a prior E1, a timed-out partial pass, the tool absent, no focus targets, a
scoped run, survivors on a non-hotspot, a run-context older than the keyhole
blocks, findings missing the E1 entry, a config-excluded hotspot (and a later
pass retracting it), an archive hotspot, and a recompute failure.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

import assess_core
from lib.keyhole_signals import FINDING_ORDER
from lib.mutation_refresh import recorded_excludes, refresh_mutation_findings
from lib.run_scope import resolve_scope

_PRODUCTS = (
    "derived_findings", "attention", "attention_low_signal", "findings_markdown",
    "keyhole_summary", "prescribed_actions", "excluded_as_archive",
    "excluded_by_config",
)

_CHEAP = {"assertion_on_internal": [], "untested_boundaries": [], "duplicate_truth": []}


def _mutation_block(per_file: list[dict], *, ran: bool = True) -> dict:
    return {
        "available": True,
        "mutation_run": ran,
        "mutation_config_present": True,
        "mutation_scope": [e["file"] for e in per_file],
        "per_file": per_file,
        "cheap_heuristics": _CHEAP,
    }


def _stats(*paths: str) -> dict:
    hot = [{"path": p, "loc": 300, "ccn": 25, "commits": 6} for p in paths]
    return {"files_scored": len(hot), "loc": {}, "ccn": {}, "top_hotspots": hot,
            "top_complex": hot, "top_large": hot}


def _make_repo(git_repo: Any, prefix: str = "") -> Path:
    """A committed repo whose hotspot src/hot.py has a sibling test."""
    repo, commit = git_repo
    base = repo / prefix if prefix else repo
    (base / "src").mkdir(parents=True)
    (base / "tests").mkdir()
    (base / "src" / "hot.py").write_text(
        "def f(x):\n    if x > 1:\n        return x * 2\n    return x\n", encoding="utf-8")
    (base / "src" / "cold.py").write_text("def g():\n    return 1\n", encoding="utf-8")
    (base / "tests" / "test_hot.py").write_text(
        "from src.hot import f\n\ndef test_f():\n    f(3)\n", encoding="utf-8")
    commit("seed")
    return repo


def _seed_stats(repo: Path, stats: dict, slug: str | None = None) -> Path:
    assess_dir = repo / ".assess" / slug if slug else repo / ".assess"
    assess_dir.mkdir(parents=True, exist_ok=True)
    (assess_dir / "complexity-stats.json").write_text(json.dumps(stats), encoding="utf-8")
    return assess_dir


def _focus(*paths: str) -> dict:
    return {"available": True, "coverage_present": True, "total_focus_targets": len(paths),
            "entries": [{"path": p, "risk_band": "high", "test_signal": "covered_but_hollow"}
                        for p in paths]}


def _patch_scan(monkeypatch: pytest.MonkeyPatch, block: dict) -> None:
    """Return ``block`` for the opt-in pass; the default pass runs for real."""
    real = assess_core.scan_test_pressure

    def scan(repo_root, hot_files=None, opt_in=False, coverage_data=None):
        if opt_in:
            return block
        return real(repo_root, hot_files=hot_files, opt_in=False, coverage_data=coverage_data)

    monkeypatch.setattr(assess_core, "scan_test_pressure", scan)


def _default_run(
    repo: Path, focus: dict | None = None, scope: Path | None = None,
) -> Path:
    """Run the default pass and return run-context.json, optionally seeding test_focus."""
    ctx = assess_core.build_run_context(repo_root=repo, run_date="2026-10-08", scope=scope)
    slug = resolve_scope(repo, scope)[2]
    ctx_path = (repo / ".assess" / slug if slug else repo / ".assess") / "run-context.json"
    if focus is not None:
        ctx["test_focus"] = focus
        ctx_path.write_text(json.dumps(ctx, indent=2), encoding="utf-8")
    return ctx_path


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _untrusted(ctx: dict) -> list[str]:
    return next(f["paths"] for f in ctx["derived_findings"] if f["name"] == "untrusted_hotspot")


# -- end to end ---------------------------------------------------------------

def test_e2e_high_survivor_density_on_hotspot_fires_untrusted(
    git_repo: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _make_repo(git_repo)
    assess_dir = _seed_stats(repo, _stats("src/hot.py", "src/cold.py"))
    ctx_path = _default_run(repo, _focus("src/hot.py"))
    before = _load(ctx_path)
    assert _untrusted(before) == []
    badge_before = _load(assess_dir / "badge.json")

    _patch_scan(monkeypatch, _mutation_block(
        [{"file": "src/hot.py", "survived": 6, "killed": 4, "total": 10}]))
    assert assess_core.run_opt_in_mutation(repo) == 0

    ctx = _load(ctx_path)
    assert _untrusted(ctx) == ["src/hot.py"]
    row = next(a for a in ctx["attention"] if a["path"] == "src/hot.py")
    assert "untrusted_hotspot" in row["findings"]
    assert "### untrusted_hotspot" in ctx["findings_markdown"]
    assert "- src/hot.py" in ctx["findings_markdown"]
    assert {"name": "untrusted_hotspot", "count": 1} in ctx["keyhole_summary"]["concerns"]
    assert any(p["path"] == "src/hot.py" for p in ctx["prescribed_actions"])
    assert ctx["mutation_not_run_cap"]["applies"] is False
    # The badge counts the newly fired finding and keeps the run's id.
    badge = _load(assess_dir / "badge.json")
    assert badge["run_id"] == before["run_id"]
    assert badge["message"] != badge_before["message"]
    # Blocks that do not read test_pressure are untouched.
    for key in ("test_focus", "gap_actions", "behaviour", "documentation", "run_id"):
        assert ctx[key] == before[key]


def test_e2e_refresh_matches_default_run_with_same_inputs(
    git_repo: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Refreshed products equal what integrate produces when the default run
    is handed the same mutation block: one implementation, not two."""
    repo = _make_repo(git_repo)
    _seed_stats(repo, _stats("src/hot.py", "src/cold.py"))
    ctx_path = _default_run(repo, _focus("src/hot.py"))
    block = _mutation_block([{"file": "src/hot.py", "survived": 4, "total": 10}])
    _patch_scan(monkeypatch, block)
    assert assess_core.run_opt_in_mutation(repo) == 0
    refreshed = _load(ctx_path)

    monkeypatch.setattr(assess_core, "scan_test_pressure", lambda *a, **k: block)
    direct = assess_core.build_run_context(repo_root=repo, run_date="2026-10-08")
    for key in _PRODUCTS:
        assert refreshed[key] == direct[key], key


# -- matrix -------------------------------------------------------------------

def test_all_killed_leaves_findings_empty(
    git_repo: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _make_repo(git_repo)
    _seed_stats(repo, _stats("src/hot.py"))
    ctx_path = _default_run(repo, _focus("src/hot.py"))
    before = _load(ctx_path)
    _patch_scan(monkeypatch, _mutation_block(
        [{"file": "src/hot.py", "survived": 0, "killed": 10, "total": 10}]))
    assert assess_core.run_opt_in_mutation(repo) == 0
    ctx = _load(ctx_path)
    assert _untrusted(ctx) == []
    for key in _PRODUCTS:
        assert ctx[key] == before[key], key


def test_second_pass_clears_prior_untrusted(
    git_repo: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _make_repo(git_repo)
    _seed_stats(repo, _stats("src/hot.py"))
    ctx_path = _default_run(repo, _focus("src/hot.py"))
    before = _load(ctx_path)
    _patch_scan(monkeypatch, _mutation_block([{"file": "src/hot.py", "survived": 9, "total": 10}]))
    assess_core.run_opt_in_mutation(repo)
    assert _untrusted(_load(ctx_path)) == ["src/hot.py"]
    _patch_scan(monkeypatch, _mutation_block([{"file": "src/hot.py", "survived": 1, "total": 10}]))
    assess_core.run_opt_in_mutation(repo)
    ctx = _load(ctx_path)
    for key in _PRODUCTS:
        assert ctx[key] == before[key], key


def test_partial_pass_counts_only_measured_files(
    git_repo: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A timed-out group leaves rows with no total; only measured rows count."""
    repo = _make_repo(git_repo)
    _seed_stats(repo, _stats("src/hot.py", "src/cold.py"))
    ctx_path = _default_run(repo, _focus("src/hot.py", "src/cold.py"))
    block = _mutation_block([
        {"file": "src/hot.py", "survived": 5, "total": 10},
        {"file": "src/cold.py", "survived": 0, "total": 0},
        {"file": "src/cold.py", "survived": 3},
    ])
    block["mutation_note"] = "timed out after 600s"
    _patch_scan(monkeypatch, block)
    assert assess_core.run_opt_in_mutation(repo) == 0
    assert _untrusted(_load(ctx_path)) == ["src/hot.py"]


def test_tool_absent_keeps_blocks_unchanged(
    git_repo: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _make_repo(git_repo)
    _seed_stats(repo, _stats("src/hot.py"))
    ctx_path = _default_run(repo, _focus("src/hot.py"))
    before = _load(ctx_path)
    block = _mutation_block([], ran=False)
    block["mutation_note"] = "mutmut not installed"
    _patch_scan(monkeypatch, block)
    assert assess_core.run_opt_in_mutation(repo) == 0
    ctx = _load(ctx_path)
    assert ctx["mutation_not_run_cap"]["applies"] is True
    for key in _PRODUCTS:
        assert ctx[key] == before[key], key


def test_no_focus_targets_leaves_run_context_byte_identical(
    git_repo: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _make_repo(git_repo)
    _seed_stats(repo, _stats("src/hot.py"))
    ctx_path = _default_run(repo, _focus())
    raw = ctx_path.read_bytes()
    _patch_scan(monkeypatch, _mutation_block([{"file": "src/hot.py", "survived": 9, "total": 10}]))
    assert assess_core.run_opt_in_mutation(repo) == 0
    assert ctx_path.read_bytes() == raw


def test_scoped_run_reads_scoped_stats(
    git_repo: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _make_repo(git_repo, prefix="pkg")
    slug = resolve_scope(repo, Path("pkg"))[2]
    assert slug
    assess_dir = _seed_stats(repo, _stats("pkg/src/hot.py"), slug=slug)
    # A whole-repo sidecar naming a different hotspot must not be read.
    _seed_stats(repo, _stats("pkg/src/cold.py"))
    ctx_path = _default_run(repo, _focus("pkg/src/hot.py"), scope=Path("pkg"))
    assert ctx_path.parent == assess_dir
    _patch_scan(monkeypatch, _mutation_block([
        {"file": "pkg/src/hot.py", "survived": 8, "total": 10},
        {"file": "pkg/src/cold.py", "survived": 8, "total": 10},
    ]))
    assert assess_core.run_opt_in_mutation(repo, scope=Path("pkg")) == 0
    assert _untrusted(_load(ctx_path)) == ["pkg/src/hot.py"]
    assert _load(assess_dir / "badge.json")["run_id"] == _load(ctx_path)["run_id"]


def test_non_hotspot_survivors_do_not_fire(
    git_repo: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _make_repo(git_repo)
    _seed_stats(repo, _stats("src/hot.py"))
    ctx_path = _default_run(repo, _focus("src/hot.py", "src/cold.py"))
    _patch_scan(monkeypatch, _mutation_block([
        {"file": "src/hot.py", "survived": 1, "total": 10},
        {"file": "src/cold.py", "survived": 10, "total": 10},
    ]))
    assert assess_core.run_opt_in_mutation(repo) == 0
    ctx = _load(ctx_path)
    assert _untrusted(ctx) == []
    assert all(a["path"] != "src/cold.py" for a in ctx["attention"])


def test_older_run_context_without_findings_is_left_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A run-context from before the keyhole blocks gets test_pressure only."""
    repo = tmp_path / "repo"
    assess_dir = _seed_stats(repo, _stats("src/hot.py"))
    ctx_path = assess_dir / "run-context.json"
    ctx_path.write_text(json.dumps({
        "run_date": "2026-06-19", "test_focus": _focus("src/hot.py"),
        "test_pressure": {"available": True, "mutation_run": False},
    }), encoding="utf-8")
    _patch_scan(monkeypatch, _mutation_block([{"file": "src/hot.py", "survived": 9, "total": 10}]))
    assert assess_core.run_opt_in_mutation(repo) == 0
    ctx = _load(ctx_path)
    assert ctx["test_pressure"]["mutation_run"] is True
    assert "derived_findings" not in ctx and "attention" not in ctx
    assert not (assess_dir / "badge.json").exists()


# -- unit: refresh_mutation_findings ------------------------------------------

def _ctx(findings: list[dict], per_file: list[dict]) -> dict:
    return {"derived_findings": findings, "test_pressure": {"per_file": per_file},
            "excluded_by_config": {"dirs": [], "patterns": [], "affected_finding_paths": [],
                                   "count": 0},
            "behaviour": {}, "promissory_markers": None}


def _empty_findings() -> list[dict]:
    return [{"name": n, "paths": [], "action": f"act {n}"} for n in FINDING_ORDER]


def test_missing_untrusted_entry_is_inserted_in_order() -> None:
    findings = [f for f in _empty_findings()
                if f["name"] not in ("untrusted_hotspot", "override_contradicts_signals")]
    ctx = _ctx(findings, [{"file": "src/hot.py", "survived": 5, "total": 10}])
    assert refresh_mutation_findings(ctx, _stats("src/hot.py"), set(), []) is True
    # Only E1 is inserted; a finding this pass never computed stays absent.
    assert [f["name"] for f in ctx["derived_findings"]] == [
        n for n in FINDING_ORDER if n != "override_contradicts_signals"]
    assert _untrusted(ctx) == ["src/hot.py"]
    # Stored actions survive; the inserted finding takes its default action.
    lying = next(f for f in ctx["derived_findings"] if f["name"] == "lying_map")
    assert lying["action"] == "act lying_map"
    e1 = next(f for f in ctx["derived_findings"] if f["name"] == "untrusted_hotspot")
    assert e1["action"].startswith("strengthen tests")


def test_config_excluded_hotspot_is_disclosed() -> None:
    ctx = _ctx(_empty_findings(), [{"file": "vendor/hot.py", "survived": 5, "total": 10}])
    refresh_mutation_findings(ctx, _stats("vendor/hot.py"), {"vendor"}, [])
    assert _untrusted(ctx) == []
    assert ctx["excluded_by_config"]["affected_finding_paths"] == ["vendor/hot.py"]
    assert ctx["excluded_by_config"]["count"] == 1


def test_later_pass_retracts_config_exclusion_it_no_longer_makes() -> None:
    """Pass 1 suppresses an excluded hotspot; pass 2 kills every mutant. The
    disclosure drops the path pass 1 added, and keeps the default run's."""
    ctx = _ctx(_empty_findings(), [{"file": "vendor/hot.py", "survived": 6, "total": 10}])
    ctx["excluded_by_config"]["affected_finding_paths"] = ["vendor/old.py"]
    ctx["excluded_by_config"]["count"] = 1
    before = json.loads(json.dumps(ctx["excluded_by_config"]))
    stats = _stats("vendor/hot.py")
    refresh_mutation_findings(ctx, stats, {"vendor"}, [])
    assert ctx["excluded_by_config"]["affected_finding_paths"] == [
        "vendor/hot.py", "vendor/old.py"]
    ctx["test_pressure"] = {"per_file": [{"file": "vendor/hot.py", "survived": 0, "total": 10}]}
    refresh_mutation_findings(ctx, stats, {"vendor"}, [])
    assert ctx["excluded_by_config"] == before


def test_pass_never_retracts_a_path_the_default_run_disclosed() -> None:
    ctx = _ctx(_empty_findings(), [{"file": "vendor/hot.py", "survived": 6, "total": 10}])
    ctx["excluded_by_config"]["affected_finding_paths"] = ["vendor/hot.py"]
    ctx["excluded_by_config"]["count"] = 1
    stats = _stats("vendor/hot.py")
    refresh_mutation_findings(ctx, stats, {"vendor"}, [])
    assert "added_by_mutation_pass" not in ctx["excluded_by_config"]
    ctx["test_pressure"] = {"per_file": []}
    refresh_mutation_findings(ctx, stats, {"vendor"}, [])
    assert ctx["excluded_by_config"]["affected_finding_paths"] == ["vendor/hot.py"]


def test_archive_hotspot_kept_out_of_attention() -> None:
    ctx = _ctx(_empty_findings(), [{"file": "archive/old.py", "survived": 5, "total": 10}])
    refresh_mutation_findings(ctx, _stats("archive/old.py"), set(), [])
    assert _untrusted(ctx) == ["archive/old.py"]
    assert ctx["attention"] == []
    assert ctx["excluded_as_archive"] == {"affected_finding_paths": ["archive/old.py"],
                                          "count": 1}


def test_recompute_failure_keeps_ctx_untouched(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    import lib.mutation_refresh as mr

    def boom(*a: Any, **k: Any) -> dict:
        raise RuntimeError("ranking exploded")

    monkeypatch.setattr(mr, "finding_products", boom)
    ctx = _ctx(_empty_findings(), [{"file": "src/hot.py", "survived": 5, "total": 10}])
    before = json.loads(json.dumps(ctx))
    assert refresh_mutation_findings(ctx, _stats("src/hot.py"), set(), []) is False
    assert ctx == before
    err = capsys.readouterr().err
    assert "not refreshed" in err and "ranking exploded" in err


def test_recorded_excludes_prefer_the_disclosed_filter(tmp_path: Path) -> None:
    """The pass filters with the excludes the default run disclosed, so a config
    edited between the two runs cannot split the filter from its disclosure."""
    (tmp_path / ".assess").mkdir()
    (tmp_path / ".assess" / "config.toml").write_text(
        'exclude_dirs = ["fresh"]\n', encoding="utf-8")
    ctx = {"excluded_by_config": {"dirs": ["vendor"], "patterns": ["*.gen.py"]}}
    assert recorded_excludes(ctx, tmp_path) == ({"vendor"}, ["*.gen.py"])
    fresh_dirs, _ = recorded_excludes({}, tmp_path)
    assert "fresh" in fresh_dirs


def test_missing_disclosure_block_is_created_for_a_suppressed_path() -> None:
    """An older run-context with findings but no excluded_by_config block still
    discloses an E1 path the config suppresses, rather than dropping it silently."""
    ctx = _ctx(_empty_findings(), [{"file": "vendor/hot.py", "survived": 5, "total": 10}])
    del ctx["excluded_by_config"]
    refresh_mutation_findings(ctx, _stats("vendor/hot.py"), {"vendor"}, ["*.gen.py"])
    assert _untrusted(ctx) == []
    assert ctx["excluded_by_config"] == {
        "dirs": ["vendor"], "patterns": ["*.gen.py"],
        "affected_finding_paths": ["vendor/hot.py"], "count": 1,
        "added_by_mutation_pass": ["vendor/hot.py"],
    }


def test_missing_disclosure_block_stays_absent_when_nothing_is_suppressed() -> None:
    ctx = _ctx(_empty_findings(), [{"file": "src/hot.py", "survived": 5, "total": 10}])
    del ctx["excluded_by_config"]
    refresh_mutation_findings(ctx, _stats("src/hot.py"), {"vendor"}, [])
    assert "excluded_by_config" not in ctx
