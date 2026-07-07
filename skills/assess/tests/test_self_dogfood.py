"""The capstone self-check: /assess's deterministic core must obey the very
invariants the assess-obey-thyself marathon added (tasks 2-16).

Every prior task in the marathon hardened one property of the emitted wiki -
a provenance stamp, a contradiction flag, an exclusion disclosure, a mutation
cap, a deterministic badge, a versioned action contract, an orphan-free hotspot
set, a verifiable log chain, decline-marker provenance. This test runs the
*deterministic* pipeline (``build_run_context`` - never the LLM finalize, which
CI cannot drive with no network/model) against a small, hermetic, purpose-built
fixture repo and asserts the output honours each invariant. If a future change
regresses one of them, this build goes red: the tool would no longer obey its
own rules.

Why a built fixture, not the live repo tree: the fixture is fast, deterministic,
and hermetic (the live ``.assess/`` drifts run to run and would make the test
flaky). It is a real ``build_run_context`` run over a real git repo - the same
code path a user's ``/assess`` drives - just over a controlled tree that pins
every signal the invariants read.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

import pytest

import assess_core
from assess_core import build_run_context
from assess_finalize import _write_actions_contract
from lib.keyhole_signals import FINDING_MODE_VALUES, mode_for_finding
from lib.wiki_writer import (
    RETIRED_STATUS,
    hotspot_page_source_path,
    hotspot_page_status,
    verify_log_chain,
)

RUN_ID_RE = re.compile(r"^\d{14}-[0-9a-f]{8}$")

# Two source files that the seeded stats name as top hotspots. They must exist
# on disk so the no-orphan invariant (task 9) is exercised, not vacuously true.
_HOT_ONE = "src/hot_one.py"
_HOT_TWO = "src/hot_two.py"


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True, capture_output=True, text=True, env=os.environ,
    )


def _build_dogfood_repo(root: Path, fixtures_dir: Path) -> Path:
    """A small software repo with every signal the invariants read pinned:
    a committed instruction file, two on-disk hotspot files, a seeded stats
    sidecar naming them, and a provenance-carrying decline marker.
    """
    repo = root / "repo"
    (repo / "src").mkdir(parents=True)
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")

    # Committed instruction file so Layer 0 has a real surface to grade.
    (repo / "CLAUDE.md").write_text(
        (fixtures_dir / "good_instructions.md").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (repo / "pyproject.toml").write_text("[project]\nname='dogfood'\n", encoding="utf-8")

    # Enough code files that archetype classifies the repo as software.
    for i in range(12):
        (repo / "src" / f"mod_{i}.py").write_text(
            f"def f{i}(x):\n    return x + {i}\n", encoding="utf-8"
        )
    # The two named hotspots, present on disk (no-orphan invariant).
    (repo / _HOT_ONE).write_text(
        "def tangled(a, b):\n" + "    a = a + b\n" * 40 + "    return a\n",
        encoding="utf-8",
    )
    (repo / _HOT_TWO).write_text(
        "def sprawl(a):\n" + "    a += 1\n" * 30 + "    return a\n",
        encoding="utf-8",
    )

    assess_dir = repo / ".assess"
    assess_dir.mkdir()
    (assess_dir / "complexity-stats.json").write_text(json.dumps({
        "files_scored": 14,
        "loc": {"total": 900},
        "ccn": {"max": 34, "mean": 6},
        "top_hotspots": [
            {"path": _HOT_ONE, "loc": 620, "ccn": 34, "commits": 8},
            {"path": _HOT_TWO, "loc": 540, "ccn": 22, "commits": 4},
        ],
        "top_complex": [{"path": _HOT_ONE, "ccn": 34}],
        "top_large": [{"path": _HOT_ONE, "loc": 620}],
    }), encoding="utf-8")

    # Decline marker with full provenance (task 12): declined_by / declined_at /
    # plugin_version / reason all present.
    (assess_dir / ".no-mutmut").write_text(json.dumps({
        "declined_by": "ben",
        "declined_at": "2026-07-01",
        "plugin_version": "1.55.5",
        "reason": "bounded mutation pass declined for the fixture",
    }), encoding="utf-8")

    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "dogfood fixture")
    return repo


@pytest.fixture(scope="module")
def dogfood_run(tmp_path_factory, request) -> dict:
    """Run the deterministic pipeline once; every test asserts one invariant on
    the shared result. Module-scoped so the (git-backed) pipeline runs a single
    time - the output is a pure function of the pinned tree, so sharing is safe.
    """
    fixtures_dir = Path(request.fspath).parent / "fixtures"
    root = tmp_path_factory.mktemp("dogfood")
    repo = _build_dogfood_repo(root, fixtures_dir)
    ctx = build_run_context(
        repo_root=repo, run_date="2026-07-07", non_interactive=True,
    )
    assess_dir = repo / ".assess"
    on_disk = json.loads((assess_dir / "run-context.json").read_text(encoding="utf-8"))
    return {"repo": repo, "assess_dir": assess_dir, "ctx": ctx, "on_disk": on_disk}


# --- task 2: run_id + artifact_schema_version provenance stamps ---------------

def test_run_id_and_schema_version_stamped(dogfood_run: dict) -> None:
    ctx = dogfood_run["ctx"]
    assert RUN_ID_RE.match(ctx["run_id"]), ctx["run_id"]
    assert ctx["artifact_schema_version"] == assess_core.ARTIFACT_SCHEMA_VERSION
    # The stamp is persisted, not just returned in-process.
    assert dogfood_run["on_disk"]["run_id"] == ctx["run_id"]
    assert (
        dogfood_run["on_disk"]["artifact_schema_version"]
        == assess_core.ARTIFACT_SCHEMA_VERSION
    )


def test_run_id_propagates_to_badge(dogfood_run: dict) -> None:
    """The run_id stamped in run-context is the same id carried on the artifact
    every consumer reads first - proving the stamp is one run, not per-writer."""
    badge = json.loads((dogfood_run["assess_dir"] / "badge.json").read_text(encoding="utf-8"))
    assert badge["run_id"] == dogfood_run["ctx"]["run_id"]


# --- task 3: archetype.override_contradicts_signals is a real bool ------------

def test_archetype_override_contradicts_signals_present(dogfood_run: dict) -> None:
    arch = dogfood_run["ctx"]["archetype"]
    assert arch["available"] is True
    assert "override_contradicts_signals" in arch
    assert isinstance(arch["override_contradicts_signals"], bool)
    # No override marker in the fixture, so the flag is False (not merely present).
    assert arch["override_contradicts_signals"] is False


# --- task 7: excluded_by_config disclosure block ------------------------------

def test_excluded_by_config_block_present(dogfood_run: dict) -> None:
    block = dogfood_run["ctx"]["excluded_by_config"]
    assert set(block) >= {"dirs", "patterns", "affected_finding_paths", "count"}
    assert isinstance(block["dirs"], list)
    assert isinstance(block["patterns"], list)
    # count is the honest tally of suppressed finding paths - never out of sync.
    assert block["count"] == len(block["affected_finding_paths"])


# --- task 8: mutation_not_run_cap on a default read-only run ------------------

def test_mutation_not_run_cap_applies_and_caps_layer6(dogfood_run: dict) -> None:
    cap = dogfood_run["ctx"]["mutation_not_run_cap"]
    assert cap["applies"] is True
    assert cap["mutation_run"] is False
    assert cap["max_layer6_band"] == "Partial"
    assert cap["annotation"]  # a non-empty annotation the LLM must carry


# --- task 15: deterministic badge (findings count, not an LLM score) ----------

def test_badge_is_deterministic_findings_count(dogfood_run: dict) -> None:
    badge = json.loads((dogfood_run["assess_dir"] / "badge.json").read_text(encoding="utf-8"))
    assert "findings" in badge["message"]
    # The shipped badge never bakes in an LLM-derived layered score.
    assert "/8" not in badge["message"]
    assert badge["link"] == "./assess-report.md"


# --- task 16: actions.json v2 (schema 2, status, derived mode) ----------------

def test_actions_json_v2_schema_status_and_derived_mode(dogfood_run: dict) -> None:
    """actions.json is written by the deterministic ``_write_actions_contract``
    (the writer finalize calls). Drive it directly - CI has no LLM to author the
    Top 3 - and assert the v2 shape: schema 2, a lifecycle ``status`` per action,
    and a ``mode`` derived from each action's finding type."""
    assess_dir = dogfood_run["assess_dir"]
    run_id = dogfood_run["ctx"]["run_id"]
    actions = [
        {"rank": 1, "action": "Characterize the src/hot_one.py seam",
         "done_when": "seam mapped", "scope_fence": "read only",
         "finding": "hidden_coupling"},
        {"rank": 2, "action": "Verify and retire the aged marker",
         "done_when": "marker resolved", "scope_fence": "one file",
         "finding": "unactioned_intent"},
        {"rank": 3, "action": "Split the accreted module",
         "done_when": "module under budget", "scope_fence": "no API change",
         "finding": "accretion_ratchet"},
    ]
    _write_actions_contract(assess_dir, actions, run_id=run_id)
    payload = json.loads((assess_dir / "actions.json").read_text(encoding="utf-8"))

    assert payload["schema"] == 2
    assert payload["run_id"] == run_id
    entries = payload["actions"]
    assert len(entries) == 3
    for entry in entries:
        assert entry["status"] == "pending"  # fresh contract, no carry-forward
        assert entry["mode"] in FINDING_MODE_VALUES
        # The mode is *derived* from the finding type, not free text.
        assert entry["mode"] == mode_for_finding(entry.get("finding"))
    # The derivation is discriminating: distinct finding types earn distinct modes.
    modes = {e["finding"]: e["mode"] for e in entries}
    assert modes["hidden_coupling"] == "characterize_first"
    assert modes["unactioned_intent"] == "verify_then_retire"
    assert modes["accretion_ratchet"] == "refactor_safe"


# --- task 9: no active hotspot page references a path absent from disk ---------

def test_no_orphan_hotspot_pages(dogfood_run: dict) -> None:
    assess_dir = dogfood_run["assess_dir"]
    repo = dogfood_run["repo"]
    pages = sorted((assess_dir / "hotspots").glob("*.md"))
    assert pages, "expected at least one hotspot page (invariant must not be vacuous)"
    active_referenced = 0
    orphans: list[str] = []
    for page in pages:
        content = page.read_text(encoding="utf-8")
        path = hotspot_page_source_path(content)
        if path is None:
            continue
        if hotspot_page_status(content) == RETIRED_STATUS:
            continue
        active_referenced += 1
        if not (repo / path).exists():
            orphans.append(path)
    assert active_referenced >= 1
    assert orphans == []


# --- task 11: log.md integrity chain verifies ---------------------------------

def test_log_chain_verifies(dogfood_run: dict) -> None:
    # The run-context claim...
    integrity = dogfood_run["ctx"]["log_integrity"]
    assert integrity["valid"] is True
    assert integrity["broken_at_entry"] is None
    # ...and the artifact itself, recomputed independently.
    valid, broken_at = verify_log_chain(dogfood_run["assess_dir"])
    assert valid is True
    assert broken_at is None


# --- task 12: decline-marker provenance shape ---------------------------------

def test_decline_marker_provenance_shape(dogfood_run: dict) -> None:
    markers = dogfood_run["ctx"]["decline_markers"]
    assert markers, "fixture writes one decline marker"
    mutmut = next((m for m in markers if m["tool"] == "mutmut"), None)
    assert mutmut is not None
    assert set(mutmut) >= {
        "path", "tool", "declined_by", "declined_at", "version", "reason", "reoffer",
    }
    # Provenance is populated (a legacy marker would carry None here).
    assert mutmut["declined_by"] == "ben"
    assert mutmut["declined_at"] == "2026-07-01"
    assert mutmut["version"] == "1.55.5"
    assert isinstance(mutmut["reoffer"], bool)
