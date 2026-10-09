"""End-to-end test for the assess_core orchestrator.

We don't run lizard/scc here - we drive assess_core via its public functions
to exercise the deterministic plumbing.

Tests for code that lives in a lib/ module sit in that module's
test_<module>.py; this file keeps the orchestration itself. Shared
repo-seeding helpers live in assess_core_helpers.py.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import assess_core
from assess_core import build_run_context
from assess_core_helpers import _minimal_repo, _seed_assess, _write_min_stats
from lib import artifact_schema


def test_archetype_block_emitted_for_software_repo(git_repo) -> None:
    """Issue #224: run-context carries an archetype block; a code repo is software."""
    repo, commit = git_repo
    _seed_assess(repo)
    src = repo / "src"
    src.mkdir()
    for i in range(12):
        (src / f"mod_{i}.py").write_text(f"def f{i}():\n    return {i}\n", encoding="utf-8")
    (repo / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    commit("software repo")

    ctx = build_run_context(repo_root=repo, run_date="2026-05-28")
    arch = ctx["archetype"]
    assert arch["available"] is True
    assert arch["archetype"] == "software"
    assert arch["na_layers"] == []
    assert arch["denominator"] == 8


def test_archetype_block_detects_knowledge_base(git_repo) -> None:
    """Issue #224: a markdown-only repo is detected as a knowledge base with
    write-side layers N/A and a renormalised denominator."""
    repo, commit = git_repo
    _seed_assess(repo)
    notes = repo / "notes"
    notes.mkdir()
    for i in range(15):
        (notes / f"note-{i}.md").write_text(f"# Note {i}\n\nbody\n", encoding="utf-8")
    (repo / "CLAUDE.md").write_text(
        "# KB\nRaw sources are immutable. A periodic consolidation pass lints the wiki.\n",
        encoding="utf-8",
    )
    commit("knowledge base")

    ctx = build_run_context(repo_root=repo, run_date="2026-05-28")
    arch = ctx["archetype"]
    assert arch["archetype"] == "knowledge-base"
    assert arch["na_layers"] == [2, 3, 4, 5, 6, 7]
    assert arch["denominator"] == 3
    assert arch["kb_maintenance"]["documented"] is True


def test_archetype_override_marker_suppresses(git_repo) -> None:
    """Issue #224: an `assess-archetype: software` marker forces software even
    on a markdown-only repo."""
    repo, commit = git_repo
    _seed_assess(repo)
    notes = repo / "notes"
    notes.mkdir()
    for i in range(15):
        (notes / f"note-{i}.md").write_text(f"# Note {i}\n", encoding="utf-8")
    (repo / "CLAUDE.md").write_text(
        "# Docs repo\n\n<!-- assess-archetype: software -->\n", encoding="utf-8"
    )
    commit("override to software")

    ctx = build_run_context(repo_root=repo, run_date="2026-05-28")
    arch = ctx["archetype"]
    assert arch["archetype"] == "software"
    assert arch["detected_via"] == "override"


def test_build_run_context_first_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No prior .assess/, no instruction files - 'new' diff, empty instructions."""
    repo = tmp_path / "repo"
    repo.mkdir()
    assess_dir = repo / ".assess"
    assess_dir.mkdir()

    current_stats = {
        "files_scored": 50,
        "loc": {"p50": 30, "p95": 200, "max": 500},
        "ccn": {"p50": 2, "p95": 8, "max": 20},
        "top_hotspots": [
            {"path": "src/a.go", "loc": 500, "ccn": 20, "commits": 5},
        ],
        "top_complex": [{"path": "src/a.go", "ccn": 20}],
        "top_large": [{"path": "src/a.go", "loc": 500}],
    }
    (assess_dir / "complexity-stats.json").write_text(json.dumps(current_stats))

    # Clear the ambient CI signal so the default (no-flag) run is genuinely
    # interactive regardless of where the suite runs (locally or under CI).
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("ASSESS_NON_INTERACTIVE", raising=False)
    ctx = build_run_context(repo_root=repo, run_date="2026-05-22")

    assert ctx["run_date"] == "2026-05-22"
    assert ctx["stats_summary"]["files_scored"] == 50
    assert ctx["instruction_files"] == {}  # nothing found
    assert ctx["instructions_grade"] is None  # no instructions = None (distinct from F)
    assert ctx["diff"]["new"] == 1
    assert ctx["diff"]["graduated"] == 0
    assert (assess_dir / "log.md").exists()
    assert (assess_dir / "index.md").exists()
    # No decline markers in a bare repo -> empty block, no re-offer.
    assert ctx["decline_markers"] == []
    assert ctx["reoffer_mutation"] is False
    assert ctx["decline_disclosures"] == []
    # Default run (no --non-interactive, no CI env): interactive, offers left
    # empty for the orchestrator to present live - NOT pre-recorded as skipped.
    assert ctx["interactive"] is True
    assert ctx["offers"] == []

    # Explicit headless signal: every offer is pre-recorded as skipped and the
    # orchestrator makes zero prompts. This is the wiring the CLI --non-interactive
    # flag drives, verified independent of the ambient environment.
    ctx_headless = build_run_context(
        repo_root=repo, run_date="2026-05-22", non_interactive=True
    )
    assert ctx_headless["interactive"] is False
    assert {o["type"] for o in ctx_headless["offers"]}  # non-empty
    assert all(o["status"] == "skipped" for o in ctx_headless["offers"])


def test_build_run_context_surfaces_decline_markers(tmp_path: Path) -> None:
    """A JSON decline marker flows into run-context with provenance + disclosure."""
    repo = tmp_path / "repo"
    repo.mkdir()
    assess_dir = repo / ".assess"
    assess_dir.mkdir()
    (assess_dir / "complexity-stats.json").write_text(json.dumps({
        "files_scored": 1, "loc": {}, "ccn": {},
        "top_hotspots": [], "top_complex": [], "top_large": [],
    }))
    # Marker declined under an ancient major -> re-offer eligible.
    (assess_dir / ".no-mutmut").write_text(json.dumps({
        "declined_by": "ben", "declined_at": "2025-01-01",
        "plugin_version": "0.9.0",
    }))

    ctx = build_run_context(repo_root=repo, run_date="2026-05-22")

    markers = ctx["decline_markers"]
    assert len(markers) == 1
    assert markers[0]["tool"] == "mutmut"
    assert markers[0]["declined_by"] == "ben"
    assert ctx["reoffer_mutation"] is True
    assert any("Mutation testing permanently declined by ben on 2025-01-01" in d
               for d in ctx["decline_disclosures"])


def test_build_run_context_second_run_sees_diff(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    assess_dir = repo / ".assess"
    assess_dir.mkdir()

    # First run state - persist prior stats
    prior_stats = {
        "files_scored": 50, "loc": {"p50": 30, "p95": 200, "max": 500},
        "ccn": {"p50": 2, "p95": 8, "max": 20},
        "top_hotspots": [
            {"path": "src/legacy.go", "loc": 500, "ccn": 20, "commits": 5},
        ],
        "top_complex": [{"path": "src/legacy.go", "ccn": 20}],
        "top_large": [{"path": "src/legacy.go", "loc": 500}],
    }
    (assess_dir / "complexity-stats.prior.json").write_text(json.dumps(prior_stats))

    current_stats = {
        "files_scored": 55, "loc": {"p50": 30, "p95": 220, "max": 550},
        "ccn": {"p50": 2, "p95": 9, "max": 22},
        "top_hotspots": [
            {"path": "src/new.go", "loc": 400, "ccn": 18, "commits": 4},
        ],
        "top_complex": [{"path": "src/new.go", "ccn": 18}],
        "top_large": [{"path": "src/new.go", "loc": 400}],
    }
    (assess_dir / "complexity-stats.json").write_text(json.dumps(current_stats))

    ctx = build_run_context(repo_root=repo, run_date="2026-05-22")
    assert ctx["diff"]["graduated"] == 1
    assert ctx["diff"]["new"] == 1


def test_build_run_context_includes_anomalies_field(tmp_path: Path) -> None:
    """Every run-context.json must have an anomalies array (possibly empty)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    assess_dir = repo / ".assess"
    assess_dir.mkdir()
    (assess_dir / "complexity-stats.json").write_text(json.dumps({
        "files_scored": 0,
        "loc": {}, "ccn": {},
        "top_hotspots": [], "top_complex": [], "top_large": [],
    }))

    ctx = build_run_context(repo_root=repo, run_date="2026-05-22")
    assert "anomalies" in ctx
    codes = {a["code"] for a in ctx["anomalies"]}
    assert "ZERO_FILES_SCORED" in codes


def test_run_context_has_deterministic_keyhole_products(tmp_path: Path) -> None:
    """assess-dogfooded Part 1: run-context.json carries the deterministic
    report-skeleton products - the pre-rendered findings markdown, the keyhole
    readiness summary, and the prescribed Top-3 actions - plus the eight derived
    findings (six original + E1/E2 trust axis)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    assess_dir = repo / ".assess"
    assess_dir.mkdir()
    (assess_dir / "complexity-stats.json").write_text(json.dumps({
        "files_scored": 1, "loc": {}, "ccn": {},
        "top_hotspots": [{"path": "src/a.py", "loc": 100, "ccn": 12, "commits": 3}],
        "top_complex": [{"path": "src/a.py", "ccn": 12}],
        "top_large": [{"path": "src/a.py", "loc": 100}],
    }))

    ctx = build_run_context(repo_root=repo, run_date="2026-05-22")

    # Task 2: deterministic findings markdown is present and well-formed.
    assert "findings_markdown" in ctx
    assert ctx["findings_markdown"].startswith(
        "## Cross-Layer Findings (Keyhole Readiness)"
    )
    # Task 3: keyhole readiness summary reported alongside the 0-8 score.
    assert "keyhole_summary" in ctx
    assert set(ctx["keyhole_summary"]) == {
        "concerns", "safe_zones", "total_concerns", "summary_text"
    }
    # Task 4: prescribed actions array exists (possibly empty for a clean repo).
    assert "prescribed_actions" in ctx
    assert isinstance(ctx["prescribed_actions"], list)
    # The low-signal marker is always emitted as a boolean beside attention.
    assert isinstance(ctx["attention_low_signal"], bool)
    # Task 5: derived findings now carry the nine named axes in fixed order.
    names = [f["name"] for f in ctx["derived_findings"]]
    assert names == [
        "hidden_coupling", "lying_map", "unexplained_complexity",
        "untrusted_hotspot", "self_referential_tests",
        "unactioned_intent", "accretion_ratchet",
        "orphaned_understanding", "candidate_dead_weight",
        "override_contradicts_signals", "refactor_boundary",
    ]


def test_repo_root_not_in_ctx(tmp_path: Path) -> None:
    """ctx should not contain repo_root - it leaks the author's absolute path.

    The LLM consumer has $REPO_ROOT from its shell context; no need to serialize it.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    assess_dir = repo / ".assess"
    assess_dir.mkdir()
    (assess_dir / "complexity-stats.json").write_text(json.dumps({
        "files_scored": 0, "loc": {}, "ccn": {},
        "top_hotspots": [], "top_complex": [], "top_large": [],
    }))

    ctx = build_run_context(repo_root=repo, run_date="2026-05-22")
    assert "repo_root" not in ctx


def test_readside_blocks_present_in_ctx(tmp_path: Path) -> None:
    """run-context.json must carry the Layer 0/1 read-side blocks."""
    repo = _minimal_repo(tmp_path)
    (repo / "README.md").write_text("# Project\nsee [code](app.py)\n")
    (repo / "app.py").write_text("x = 1\n")

    ctx = build_run_context(repo_root=repo, run_date="2026-05-27")
    for key in ("doc_graph", "doc_staleness", "stale_hubs", "dead_code", "observability"):
        assert key in ctx, f"missing read-side block: {key}"
    assert ctx["doc_graph"]["available"] is True
    assert ctx["doc_graph"]["doc_count"] == 1
    assert ctx["doc_staleness"]["available"] is True
    assert isinstance(ctx["stale_hubs"], list)
    assert "rung" in ctx["observability"]
    assert "candidate_count" in ctx["dead_code"]


def test_keyhole_signal_failure_degrades_not_crashes(tmp_path: Path, monkeypatch) -> None:
    """A raising keyhole signal must degrade to available:false, not crash the
    run or disturb the existing blocks (defensive-wiring constraint)."""
    repo = _minimal_repo(tmp_path)

    def boom(*_a, **_k):
        raise RuntimeError("simulated structure failure")

    monkeypatch.setattr(assess_core, "analyze_structure", boom)
    ctx = build_run_context(repo_root=repo, run_date="2026-05-29")
    assert ctx["structure"]["available"] is False
    assert "failed" in ctx["structure"]["reason"]
    # The rest of the run is intact, including the other keyhole blocks.
    assert "behaviour" in ctx
    assert "derived_findings" in ctx
    assert "observability" in ctx


def test_readside_scan_failure_degrades_not_crashes(tmp_path: Path, monkeypatch) -> None:
    """A raising scan must degrade to an unavailable marker, not blow up the run."""
    repo = _minimal_repo(tmp_path)

    def boom(*_a, **_k):
        raise RuntimeError("simulated scan failure")

    monkeypatch.setattr(assess_core, "build_doc_graph", boom)
    ctx = build_run_context(repo_root=repo, run_date="2026-05-27")
    assert ctx["doc_graph"]["available"] is False
    assert "failed" in ctx["doc_graph"]["reason"]
    # downstream blocks still present
    assert "observability" in ctx
    assert ctx["stale_hubs"] == []  # can't join hubs without a graph


def test_coverage_gate_block_reports_configured_threshold(tmp_path: Path) -> None:
    """The run context carries the coverage_gate block: absent by default, and
    the file, line and threshold once a fail_under is configured."""
    repo = _minimal_repo(tmp_path)
    ctx = build_run_context(repo_root=repo, run_date="2026-05-27")
    assert ctx["coverage_gate"]["enforced"] is False
    assert ctx["coverage_gate"]["gates"] == []

    (repo / ".coveragerc").write_text("[report]\nfail_under = 80\n")
    ctx = build_run_context(repo_root=repo, run_date="2026-05-27")
    gate = ctx["coverage_gate"]["gates"][0]
    assert ctx["coverage_gate"]["enforced"] is True
    assert (gate["file"], gate["line"], gate["threshold"]) == (".coveragerc", 2, 80.0)


def test_plugin_version_in_ctx(tmp_path: Path) -> None:
    """ctx should include plugin_version so the LLM can surface it in the report.

    Mitigates the multi-version cache footgun: if /reload-plugins lands on an old
    cached version, the report shows that version and the user can spot the drift.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    assess_dir = repo / ".assess"
    assess_dir.mkdir()
    (assess_dir / "complexity-stats.json").write_text(json.dumps({
        "files_scored": 0, "loc": {}, "ccn": {},
        "top_hotspots": [], "top_complex": [], "top_large": [],
    }))

    ctx = build_run_context(repo_root=repo, run_date="2026-05-22")
    assert "plugin_version" in ctx
    assert isinstance(ctx["plugin_version"], str)
    assert ctx["plugin_version"].count(".") >= 1


def test_config_excludes_apply_to_all_scans(tmp_path: Path) -> None:
    """`.assess/config.toml` excludes are loaded once by the orchestrator
    and applied uniformly to the doc graph, doc staleness, and liveness
    scan. A `regulatory-raw/` dir vanishes from every layer's view, not
    just the treemap. This is the single load-bearing test for the
    consistent-excludes design - if it passes, the schema rename and the
    per-scan plumbing are wired correctly end-to-end."""
    repo = _minimal_repo(tmp_path)
    # Config opt-in.
    (repo / ".assess" / "config.toml").write_text(
        'exclude_dirs = ["regulatory-raw"]\n',
        encoding="utf-8",
    )
    # Two docs (one in scope, one excluded) and two code files (same).
    (repo / "README.md").write_text("see [main](./src/app.py)\n", encoding="utf-8")
    (repo / "src").mkdir()
    (repo / "src" / "app.py").write_text("def used(): pass\n", encoding="utf-8")
    (repo / "regulatory-raw").mkdir()
    (repo / "regulatory-raw" / "notes.md").write_text("ref data note\n", encoding="utf-8")
    (repo / "regulatory-raw" / "loader.py").write_text("x = 1\n", encoding="utf-8")

    ctx = build_run_context(repo_root=repo, run_date="2026-05-29")

    # Doc graph: only README.md is counted.
    assert ctx["doc_graph"]["doc_count"] == 1
    # Doc staleness: only the in-scope doc + code file are counted.
    assert ctx["doc_staleness"]["association"]["doc_count"] == 1
    assert ctx["doc_staleness"]["association"]["code_file_count"] == 1
    # Liveness: any candidate paths from the dead-code scan must not
    # mention regulatory-raw (vulture etc. would either skip the dir
    # via --exclude or get post-filtered).
    for c in ctx["dead_code"].get("candidates", []):
        assert "regulatory-raw" not in c.get("path", "")


def test_test_pressure_passes_hotspots_as_hot_files(tmp_path: Path, monkeypatch) -> None:
    """The wiring threads the current top-hotspot paths into scan_test_pressure
    as hot_files, so an opt-in mutation run would target the files that matter."""
    repo = tmp_path / "repo"
    repo.mkdir()
    assess_dir = repo / ".assess"
    assess_dir.mkdir()
    (assess_dir / "complexity-stats.json").write_text(json.dumps({
        "files_scored": 10, "loc": {"p50": 10, "p95": 30, "max": 50},
        "ccn": {"p50": 1, "p95": 3, "max": 5},
        "top_hotspots": [
            {"path": "src/hot.py", "loc": 500, "ccn": 20, "commits": 5},
        ],
        "top_complex": [{"path": "src/hot.py", "ccn": 20}],
        "top_large": [{"path": "src/hot.py", "loc": 500}],
    }))
    captured = {}

    def fake_scan(repo_root, hot_files=None, opt_in=False, coverage_data=None):
        captured["hot_files"] = hot_files
        captured["opt_in"] = opt_in
        return {"mutation_config_present": False, "cheap_heuristics": {}}

    monkeypatch.setattr(assess_core, "scan_test_pressure", fake_scan)
    build_run_context(repo_root=repo, run_date="2026-05-29")
    assert captured["hot_files"] == ["src/hot.py"]
    assert captured["opt_in"] is False  # read-only by default


def test_core_always_writes_deterministic_badge_linking_to_report(tmp_path: Path) -> None:
    """The shipped badge is the deterministic findings form, written on every
    run and linking to the report - never an LLM-authored score number."""
    repo = tmp_path / "repo"
    repo.mkdir()
    assess_dir = repo / ".assess"
    assess_dir.mkdir()
    (assess_dir / "complexity-stats.json").write_text(json.dumps({
        "files_scored": 1, "loc": {}, "ccn": {},
        "top_hotspots": [], "top_complex": [], "top_large": [],
    }), encoding="utf-8")

    build_run_context(repo_root=repo, run_date="2026-06-12")
    badge = json.loads((assess_dir / "badge.json").read_text(encoding="utf-8"))
    assert badge["label"] == "AI-readiness"
    assert "findings" in badge["message"]        # deterministic form
    assert "/8" not in badge["message"]           # no LLM score fraction
    assert badge["link"] == "./assess-report.md"  # funnels to the report

    # A stale LLM-scored badge from a prior run is overwritten by the
    # deterministic producer, not preserved - the badge stays reproducible.
    (assess_dir / "badge.json").write_text(json.dumps({
        "schemaVersion": 1, "label": "AI-readiness",
        "message": "7.0/8 · AI-Native", "color": "brightgreen",
    }), encoding="utf-8")
    build_run_context(repo_root=repo, run_date="2026-06-13")
    badge2 = json.loads((assess_dir / "badge.json").read_text(encoding="utf-8"))
    assert "findings" in badge2["message"]
    assert badge2["message"] != "7.0/8 · AI-Native"
    assert badge2["link"] == "./assess-report.md"


# ── opt-in mutation affordance (run_opt_in_mutation) ──────────────────────────

def _seed_run_context(repo: Path, *, test_focus: dict) -> Path:
    """Write a minimal run-context.json carrying a test_focus block."""
    assess_dir = repo / ".assess"
    assess_dir.mkdir(parents=True, exist_ok=True)
    ctx_path = assess_dir / "run-context.json"
    ctx_path.write_text(json.dumps({
        "run_date": "2026-06-19",
        "test_focus": test_focus,
        "test_pressure": {
            "available": True,
            "mutation_run": False,
            "mutation_config_present": False,
            "cheap_heuristics": {
                "assertion_on_internal": [],
                "untested_boundaries": [],
                "duplicate_truth": [],
            },
        },
    }), encoding="utf-8")
    return ctx_path


def test_run_opt_in_mutation_rewrites_test_pressure(tmp_path: Path, monkeypatch) -> None:
    """An accepted mutation pass re-runs scan_test_pressure scoped to the
    test_focus targets that carry test evidence, with opt_in=True, and rewrites
    the test_pressure block. Entries with no test (no_covering_test,
    unsupported) stay out of the scope even when they rank first."""
    repo = tmp_path / "repo"
    repo.mkdir()
    ctx_path = _seed_run_context(repo, test_focus={
        "available": True,
        "coverage_present": True,
        "entries": [
            {"path": "src/a.py", "risk_band": "high", "test_signal": "no_covering_test"},
            {"path": "src/u.py", "risk_band": "high", "test_signal": "unsupported"},
            {"path": "src/b.py", "risk_band": "medium", "test_signal": "covered_but_hollow"},
            {"path": "src/c.py", "risk_band": "low", "test_signal": "sibling_test_only"},
        ],
        "total_focus_targets": 4,
    })

    captured: dict = {}

    def fake_scan(repo_root, hot_files=None, opt_in=False, coverage_data=None):
        captured["hot_files"] = hot_files
        captured["opt_in"] = opt_in
        return {
            "available": True,
            "mutation_run": True,
            "mutation_config_present": False,
            "mutation_scope": hot_files,
            "per_file": [{"file": "src/a.py", "survived": 3, "total": 10}],
            "cheap_heuristics": {
                "assertion_on_internal": [],
                "untested_boundaries": [],
                "duplicate_truth": [],
            },
        }

    monkeypatch.setattr(assess_core, "scan_test_pressure", fake_scan)
    rc = assess_core.run_opt_in_mutation(repo)
    assert rc == 0
    # Scoped to the focus targets, with the bounded pass enabled.
    assert captured["opt_in"] is True
    assert captured["hot_files"] == ["src/b.py", "src/c.py"]
    # test_pressure block rewritten in place with the mutation results.
    ctx = json.loads(ctx_path.read_text(encoding="utf-8"))
    assert ctx["test_pressure"]["mutation_run"] is True
    assert ctx["test_pressure"]["per_file"] == [
        {"file": "src/a.py", "survived": 3, "total": 10}
    ]


def test_run_opt_in_mutation_no_focus_targets(tmp_path: Path, monkeypatch) -> None:
    """Empty test_focus.entries means nothing to mutate - the scan never runs
    and the context is left untouched."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _seed_run_context(repo, test_focus={
        "available": True, "coverage_present": True, "entries": [],
        "total_focus_targets": 0,
    })

    called = {"ran": False}

    def fake_scan(*a, **k):
        called["ran"] = True
        return {}

    monkeypatch.setattr(assess_core, "scan_test_pressure", fake_scan)
    rc = assess_core.run_opt_in_mutation(repo)
    assert rc == 0
    assert called["ran"] is False


def test_run_opt_in_mutation_missing_context(tmp_path: Path) -> None:
    """No prior run-context.json -> non-zero return, no crash."""
    repo = tmp_path / "repo"
    repo.mkdir()
    rc = assess_core.run_opt_in_mutation(repo)
    assert rc == 1


def test_run_opt_in_mutation_degrades_on_scan_failure(tmp_path: Path, monkeypatch) -> None:
    """A raising scan degrades to the unavailable shape rather than crashing,
    preserving the cheap_heuristics schema so the consumer's shape is stable."""
    repo = tmp_path / "repo"
    repo.mkdir()
    ctx_path = _seed_run_context(repo, test_focus={
        "available": True, "coverage_present": True,
        "entries": [{"path": "src/a.py", "risk_band": "high",
                     "test_signal": "covered_but_hollow"}],
        "total_focus_targets": 1,
    })

    def boom(*a, **k):
        raise RuntimeError("mutation exploded")

    monkeypatch.setattr(assess_core, "scan_test_pressure", boom)
    rc = assess_core.run_opt_in_mutation(repo)
    assert rc == 0
    ctx = json.loads(ctx_path.read_text(encoding="utf-8"))
    tp = ctx["test_pressure"]
    assert tp["available"] is False
    assert tp["mutation_config_present"] is None
    assert tp["cheap_heuristics"] == {
        "assertion_on_internal": [],
        "untested_boundaries": [],
        "duplicate_truth": [],
    }


# --- run_id / schema_version stamps + Layer 6 cap (assess-obey-thyself) -------


def test_run_context_carries_run_id_and_schema_version(tmp_path: Path) -> None:
    repo = _minimal_repo(tmp_path)
    ctx = build_run_context(repo_root=repo, run_date="2026-07-07")
    assert ctx["artifact_schema_version"] == artifact_schema.ARTIFACT_SCHEMA_VERSION == "1.4.0"
    # run_id shape: YYYYMMDDHHMMSS-<8 hex>
    run_id = ctx["run_id"]
    stamp, _, suffix = run_id.partition("-")
    assert len(stamp) == 14 and stamp.isdigit()
    assert len(suffix) == 8 and all(c in "0123456789abcdef" for c in suffix)
    # The same id is persisted to run-context.json on disk.
    on_disk = json.loads((repo / ".assess" / "run-context.json").read_text())
    assert on_disk["run_id"] == run_id
    assert on_disk["artifact_schema_version"] == "1.4.0"


def test_run_context_run_id_is_unique_per_run(tmp_path: Path) -> None:
    repo = _minimal_repo(tmp_path)
    a = build_run_context(repo_root=repo, run_date="2026-07-07")["run_id"]
    b = build_run_context(repo_root=repo, run_date="2026-07-07")["run_id"]
    assert a != b


def test_run_context_run_id_stamped_on_badge(tmp_path: Path) -> None:
    """The deterministic fallback badge carries the run_id (no prior badge)."""
    repo = _minimal_repo(tmp_path)
    ctx = build_run_context(repo_root=repo, run_date="2026-07-07")
    badge = json.loads((repo / ".assess" / "badge.json").read_text())
    assert badge["run_id"] == ctx["run_id"]


# ════════════════════════════════════════════════════════════════════════════
# Archetype override contradiction finding (override_contradicts_signals)
# ════════════════════════════════════════════════════════════════════════════

def test_override_contradiction_fires_finding_end_to_end(git_repo) -> None:
    """A software marker on a pure-doc repo lands the override_contradicts_signals
    finding pointed at the marker's file, while the archetype block records the
    contradiction (the override still wins the classification)."""
    repo, commit = git_repo
    (repo / ".assess").mkdir()
    _write_min_stats(repo / ".assess")
    (repo / "notes").mkdir()
    for i in range(12):
        (repo / "notes" / f"n{i}.md").write_text(f"# Note {i}\n")
    (repo / "CLAUDE.md").write_text(
        "# KB\n\n<!-- assess-archetype: software -->\n"
    )
    commit("init")

    ctx = build_run_context(repo_root=repo, run_date="2026-07-07")
    arch = ctx["archetype"]
    assert arch["archetype"] == "software"  # override wins
    assert arch["override_contradicts_signals"] is True
    assert arch["contradiction_details"]

    finding = next(
        f for f in ctx["derived_findings"]
        if f["name"] == "override_contradicts_signals"
    )
    assert finding["paths"] == ["CLAUDE.md"]


def test_no_override_no_contradiction_finding_end_to_end(git_repo) -> None:
    """A plain software repo (no marker) leaves the finding silent."""
    repo, commit = git_repo
    (repo / ".assess").mkdir()
    _write_min_stats(repo / ".assess")
    (repo / "app.py").write_text("def f():\n    return 1\n")
    (repo / "pyproject.toml").write_text("[project]\nname='app'\n")
    commit("init")

    ctx = build_run_context(repo_root=repo, run_date="2026-07-07")
    assert ctx["archetype"]["override_contradicts_signals"] is False
    finding = next(
        f for f in ctx["derived_findings"]
        if f["name"] == "override_contradicts_signals"
    )
    assert finding["paths"] == []


def _gap_repo(tmp_path: Path, *, lcov: bool, linked_docs: int) -> Path:
    """Two hotspots, nine docs of which README links the first ``linked_docs``."""
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "docs").mkdir()
    for name in ("zeta", "mid"):
        (repo / "src" / f"{name}.py").write_text("v = 1\n")
    readme = ["# Fixture", ""]
    for n in range(9):
        (repo / "docs" / f"d{n}.md").write_text(f"# d{n}\n")
        if n < linked_docs:
            readme.append(f"- [d{n}](docs/d{n}.md)")
    (repo / "README.md").write_text("\n".join(readme) + "\n")
    if lcov:
        (repo / "lcov.info").write_text("SF:src/zeta.py\nLF:1\nLH:1\nend_of_record\n")
    (repo / ".assess").mkdir()
    (repo / ".assess" / "complexity-stats.json").write_text(json.dumps({
        "files_scored": 2, "loc": {}, "ccn": {}, "top_complex": [], "top_large": [],
        "top_hotspots": [{"path": f"src/{n}.py", "loc": 1, "ccn": 1.0, "commits": 1}
                         for n in ("zeta", "mid")],
    }))
    return repo


def test_run_context_gap_actions_coverage_first_names_hotspots(tmp_path: Path) -> None:
    ctx = build_run_context(repo_root=_gap_repo(tmp_path, lcov=False, linked_docs=9),
                            run_date="2026-05-22")
    assert [g["signal"] for g in ctx["gap_actions"]] == ["coverage_report"]
    assert sorted(ctx["gap_actions"][0]["paths"]) == ["src/mid.py", "src/zeta.py"]


def test_run_context_gap_actions_empty_with_coverage_and_reachable_docs(tmp_path: Path) -> None:
    ctx = build_run_context(repo_root=_gap_repo(tmp_path, lcov=True, linked_docs=9),
                            run_date="2026-05-22")
    assert ctx["gap_actions"] == []


def test_run_context_gap_actions_flags_low_doc_reachability(tmp_path: Path) -> None:
    ctx = build_run_context(repo_root=_gap_repo(tmp_path, lcov=True, linked_docs=2),
                            run_date="2026-05-22")
    assert ctx["doc_graph"]["reachability_pct"] == 0.3
    gaps = ctx["gap_actions"]
    assert [g["signal"] for g in gaps] == ["doc_graph"]
    assert "docs/d8.md" in gaps[0]["paths"]
