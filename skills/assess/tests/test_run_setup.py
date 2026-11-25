"""Tests for the setup-stage adapters and the publish step (issue #557)."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from assess_core_helpers import _minimal_repo
from lib import core_scans, run_setup
from lib.run_publish import publish_run
from lib.scan_registry import STAGE_SETUP, run_scans
from lib.complexity.stats_diff import StatsDiff


def _setup(repo: Path, scope: Path | None = None) -> tuple[dict, dict]:
    ctx: dict = {}
    state: dict = {"repo_root": repo, "requested_scope": scope,
                   "run_date": "2026-11-20", "non_interactive": True}
    run_scans(ctx, state, STAGE_SETUP)
    return ctx, state


def test_new_run_id_is_a_stamp_plus_suffix() -> None:
    assert re.fullmatch(r"\d{14}-[0-9a-f]{8}", run_setup.new_run_id())
    assert run_setup.new_run_id() != run_setup.new_run_id()


def test_read_plugin_version_degrades_to_unknown(tmp_path: Path) -> None:
    assert run_setup.read_plugin_version(tmp_path / "missing.json") == "unknown"
    bad = tmp_path / "plugin.json"
    bad.write_text("{not json", encoding="utf-8")
    assert run_setup.read_plugin_version(bad) == "unknown"
    bad.write_text('{"version": "9.9.9"}', encoding="utf-8")
    assert run_setup.read_plugin_version(bad) == "9.9.9"
    # The default path is the real manifest: a wrong Path(__file__).parents[N]
    # after a #917 move would read ``unknown`` here without raising.
    real = Path(__file__).resolve().parents[3] / ".claude-plugin" / "plugin.json"
    assert run_setup.read_plugin_version() == json.loads(real.read_text(encoding="utf-8"))["version"]
    assert run_setup.read_plugin_version() != "unknown"


def test_run_header_emits_its_declared_keys_in_order(tmp_path: Path) -> None:
    header = run_setup.run_header(
        "id", "2026-11-20", (None, None, ""), {"available": False},
        {"files_scored": 0}, None,
        ({}, None, [], [], {}, {}), StatsDiff(), ({"b.py", "a.py"}, []),
    )
    assert tuple(header) == run_setup.RUN_HEADER_KEYS
    assert all(header[key] is None for key in run_setup.WIKI_KEYS)
    assert header["provisional_first_flagged"] == ["a.py", "b.py"]
    assert header["prior_stats_exists"] is False
    assert header["scope_slug"] is None


def _header_for(current: dict) -> dict:
    return run_setup.run_header(
        "id", "2026-11-20", (None, None, ""), {"available": False},
        current, None,
        ({}, None, [], [], {}, {}), StatsDiff(), (set(), []),
    )


def test_a_missing_stats_sidecar_reads_unavailable_not_zero(tmp_path: Path) -> None:
    """#959: no complexity-stats.json is not a measured zero, so the empty-set rule
    (no_scored_files) never fires on it and the tally names the missing sidecar."""
    from lib.scoring import level_tally
    from lib.run_scope import NO_STATS_REASON, load_current_stats

    stats = _header_for(load_current_stats(tmp_path))["stats_summary"]
    assert stats["available"] is False and stats["reason"] == NO_STATS_REASON
    assert stats["files_scored"] is None and stats["loc"] == {}
    signals = {"stats_summary": stats, "structure_drift": {"tier_0": {"boundary_patterns": 2}}}
    result = level_tally.level_result(level_tally.default_model(), signals)
    reasons = {c["id"]: c.get("reason") for c in result["conditions"]}
    assert reasons["layer-4-architecture.2"] == "no_complexity_stats"
    # a written sidecar with no file scored is still the measured empty set
    scored = _header_for({"files_scored": 0, "loc": {}})["stats_summary"]
    assert "available" not in scored and scored["files_scored"] == 0


def test_a_sidecar_before_the_class_split_reads_no_class_split() -> None:
    """#820: a stored sidecar without loc_by_class is not a measured 0, so L4.2 reads
    not measured with its code; a missing sidecar carries no class-split marker."""
    from lib.scoring import level_tally
    from lib.class_sizes import loc_by_class
    from lib.run_scope import load_current_stats

    older = {"schema_version": 7, "files_scored": 3, "loc": {"p95": 40.0}}
    stats = _header_for(older)["stats_summary"]
    assert stats["loc_by_class"]["available"] is False
    assert stats["loc_by_class"]["reason"].startswith("no_class_split:")
    signals = {"stats_summary": stats, "structure_drift": {"tier_0": {"boundary_patterns": 2}}}
    reasons = {c["id"]: (c["state"], c.get("reason")) for c in
               level_tally.level_result(level_tally.default_model(), signals)["conditions"]}
    assert reasons["layer-4-architecture.2"] == ("not_measured", "no_class_split")
    current = {**older, "loc_by_class": loc_by_class([("src/a.py", 40)])}
    assert _header_for(current)["stats_summary"]["loc_by_class"]["source"]["p95"] == 40.0
    assert "loc_by_class" not in _header_for(load_current_stats(Path("no-such-dir")))["stats_summary"]


def test_run_header_copies_complexity_unreliable() -> None:
    """A schema-7 sidecar's complexity_unreliable reaches stats_summary (#590);
    a schema-6 sidecar without the key reads as an empty list."""
    row = {"path": "scripts/doctor.sh", "language": "Shell", "loc": 2150,
           "ccn": 5.0, "density": 0.0023, "language_median_density": 0.18}
    current = {"schema_version": 7, "files_scored": 2, "complexity_unreliable": [row]}
    assert _header_for(current)["stats_summary"]["complexity_unreliable"] == [row]
    older = {"schema_version": 6, "files_scored": 2}
    assert _header_for(older)["stats_summary"]["complexity_unreliable"] == []


def test_stats_diff_drops_a_graduation_caused_by_the_generated_filter() -> None:
    prior = {"top_hotspots": [{"path": "gen.py", "score": 9}, {"path": "src.py", "score": 8}]}
    current = {"top_hotspots": []}
    kept = run_setup.stats_diff(prior, current, lambda path: path == "gen.py")
    assert [h.path for h in kept.graduated] == ["src.py"]


def test_setup_stage_only_reads(tmp_path: Path) -> None:
    """The table writes nothing: the scoped artifact directory does not exist
    until the publish step creates it."""
    repo = _minimal_repo(tmp_path)
    (repo / "sub").mkdir()
    ctx, state = _setup(repo, scope=repo / "sub")
    assert state["assess_dir"] == repo / ".assess" / "sub"
    assert not state["assess_dir"].exists()
    assert ctx["scope"] == "sub" and ctx["scope_slug"] == "sub"
    assert state["scope"] == repo / "sub"


def test_an_invalid_scope_stops_the_run(tmp_path: Path) -> None:
    repo = _minimal_repo(tmp_path)
    with pytest.raises(ValueError):
        _setup(repo, scope=repo / "missing")


def test_publish_fills_the_reserved_wiki_keys_in_place(tmp_path: Path) -> None:
    repo = _minimal_repo(tmp_path)
    ctx, state = _setup(repo)
    order = list(ctx)
    ctx["derived_findings"] = []
    publish_run(ctx, state)
    assert list(ctx)[: len(order)] == order
    assert ctx["pruned_hotspots"] == [] and ctx["dropped_first_flagged"] == []
    assert ctx["log_integrity"] == {"valid": True, "broken_at_entry": None}
    on_disk = json.loads((repo / ".assess" / "run-context.json").read_text(encoding="utf-8"))
    assert on_disk == ctx
    for name in ("index.md", "log.md", "badge.json", "first-flagged.json"):
        assert (repo / ".assess" / name).exists(), name


def test_core_scan_adapters_split_their_sources() -> None:
    grading = ({}, "B", ["u.md"], [], {"skills_dirs_present": [".claude/skills"],
                                       "skills_count": 2, "skill_files": ["a", "b"]},
               {"CLAUDE.md": []})
    assert core_scans.untracked_instruction_files(grading) == ["u.md"]
    assert core_scans.sensitive_instruction_content(grading) == {"CLAUDE.md": []}
    assert core_scans.skills_blocks(grading) == {
        "skills_present": [".claude/skills"], "skills_count": 2, "skill_files": ["a", "b"],
    }
    assert core_scans.offers_blocks(True)["interactive"] is False
    assert core_scans.uninstall_instructions_path() == "references/uninstall.md"
    assert core_scans.promissory_block({"available": True}) == {"available": True}


def test_anomalies_read_only_the_run_header() -> None:
    header = {"stats_summary": {"files_scored": 10, "ccn": {"p95": 0, "max": 0}},
              "instruction_files": {}, "diff": {}, "prior_stats_exists": False}
    codes = [a["code"] for a in core_scans.anomalies(header)]
    assert codes


def test_detect_anomalies_reads_only_run_header_keys() -> None:
    """The anomalies entry hands detect_anomalies the run header, not the whole
    context; a read of any other key would silently get its default."""
    read: set[str] = set()

    class Recording(dict):
        def get(self, key, default=None):
            read.add(key)
            return super().get(key, default)

        def __getitem__(self, key):
            read.add(key)
            return super().__getitem__(key)

    header = Recording({"stats_summary": {"files_scored": 10, "ccn": {}, "top_hotspots": []},
                        "instruction_files": {}, "diff": {}, "prior_stats_exists": True})
    core_scans.anomalies(header)
    assert read and read <= set(run_setup.RUN_HEADER_KEYS)
