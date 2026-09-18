"""log.md entry replacement and re-chain across core runs and finalize (#355).

A core run on the same date and measured commit as the previous, never
finalized run replaces that run's log entry instead of stacking a second one.
Finalize fills the entry stamped with its run id, re-chains the log, and
refuses while an earlier same-date entry still carries placeholders.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from assess_core import build_run_context
from assess_finalize import FinalizeValidationError, finalize_run
from lib.badge import maturity_band
from lib.wiki_writer import (
    LOG_PLACEHOLDER,
    read_log_entries,
    rewrite_log_entry,
    verify_log_chain,
)

DAY = "2026-09-18"


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.email=t@example.com", "-c", "user.name=T", *args],
        check=True, capture_output=True, text=True, env=os.environ,
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "hot.py").write_text("def hot(a):\n    return a\n", encoding="utf-8")
    _git(repo, "init", "-q")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "c1")
    assess_dir = repo / ".assess"
    assess_dir.mkdir()
    (assess_dir / "complexity-stats.json").write_text(json.dumps({
        "files_scored": 1, "loc": {"total": 2}, "ccn": {"max": 1, "mean": 1},
        "top_hotspots": [{"path": "src/hot.py", "loc": 2, "ccn": 1, "commits": 1}],
        "top_complex": [], "top_large": [],
    }), encoding="utf-8")
    return repo


def _run(repo: Path) -> dict:
    return build_run_context(repo_root=repo, run_date=DAY, non_interactive=True)


def _stage_finalize(assess_dir: Path, run_id: str) -> None:
    (assess_dir / ".cache").mkdir(exist_ok=True)
    (assess_dir / ".cache" / "finalize-input.json").write_text(json.dumps({
        "run_id": run_id, "score": 4.0, "denominator": 8,
        "maturity_label": maturity_band(4.0, 8),
        "top_action": "fixture action", "hotspot_actions": {},
    }), encoding="utf-8")


def _day_headings(assess_dir: Path) -> int:
    log = (assess_dir / "log.md").read_text(encoding="utf-8")
    return sum(1 for line in log.splitlines() if line.startswith(f"## {DAY}"))


def test_core_replaces_superseded_same_day_entry(repo: Path) -> None:
    assess_dir = repo / ".assess"
    first = _run(repo)
    second = _run(repo)
    log = (assess_dir / "log.md").read_text(encoding="utf-8")
    assert _day_headings(assess_dir) == 1
    assert first["run_id"] not in log
    assert second["run_id"] in log
    assert verify_log_chain(assess_dir) == (True, None)
    assert second["log_integrity"]["valid"] is True

    _stage_finalize(assess_dir, second["run_id"])
    finalize_run(assess_dir=assess_dir)
    assert _day_headings(assess_dir) == 1
    assert verify_log_chain(assess_dir) == (True, None)

    # A finalized entry is history: the next run appends, and discloses nothing.
    third = _run(repo)
    log = (assess_dir / "log.md").read_text(encoding="utf-8")
    assert _day_headings(assess_dir) == 2
    assert "fixture action" in log
    assert "History integrity broken" not in log
    assert third["log_integrity"]["valid"] is True


def test_core_keeps_superseded_same_day_entry_on_new_commit(repo: Path) -> None:
    assess_dir = repo / ".assess"
    _run(repo)
    (repo / "src" / "warm.py").write_text("def warm(a):\n    return a\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "c2")
    _run(repo)
    assert _day_headings(assess_dir) == 2


def test_finalize_refuses_earlier_same_date_placeholder_entry(repo: Path) -> None:
    assess_dir = repo / ".assess"
    first = _run(repo)
    (repo / "src" / "warm.py").write_text("def warm(a):\n    return a\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "c2")
    second = _run(repo)
    before = (assess_dir / "log.md").read_text(encoding="utf-8")
    _stage_finalize(assess_dir, second["run_id"])
    with pytest.raises(FinalizeValidationError, match=first["run_id"]):
        finalize_run(assess_dir=assess_dir)
    # Fail-closed: nothing written.
    assert (assess_dir / "log.md").read_text(encoding="utf-8") == before
    assert before.count(LOG_PLACEHOLDER) == 2


def test_rewrite_log_entry_keeps_an_earlier_break_detectable(repo: Path) -> None:
    """Re-chaining after an edit must not bless an entry that was already broken."""
    assess_dir = repo / ".assess"
    for n in range(3):
        (repo / "src" / f"m{n}.py").write_text("x = 1\n", encoding="utf-8")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", f"m{n}")
        _run(repo)
    log_path = assess_dir / "log.md"
    # An untampered log: rewriting the first entry re-chains the two after it.
    rewrite_log_entry(assess_dir, 0, read_log_entries(assess_dir)[0] + "\n")
    assert verify_log_chain(assess_dir) == (True, None)
    # Tamper with the middle entry only, then rewrite the first one.
    text = log_path.read_text(encoding="utf-8")
    cut = text.index("<!-- chain:") + len("<!-- chain:0123456789abcdef -->\n")
    log_path.write_text(
        text[:cut] + text[cut:].replace("**Files scored:**", "**Files scored (edited):**", 1),
        encoding="utf-8",
    )
    assert verify_log_chain(assess_dir) == (False, 2)
    entries = read_log_entries(assess_dir)
    rewrite_log_entry(assess_dir, 0, entries[0] + "\n")
    assert verify_log_chain(assess_dir) == (False, 2)


def test_drop_entry_clears_earlier_same_date_placeholder_refusal(repo: Path) -> None:
    """The refusal names a supported recovery; following it lets finalize run
    and leaves a chain that verifies."""
    assess_dir = repo / ".assess"
    first = _run(repo)
    (repo / "src" / "warm.py").write_text("def warm(a):\n    return a\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "c2")
    second = _run(repo)
    _stage_finalize(assess_dir, second["run_id"])
    with pytest.raises(FinalizeValidationError, match=f"--drop-entry {first['run_id']}"):
        finalize_run(assess_dir=assess_dir)

    script = Path(__file__).resolve().parents[1] / "scripts" / "assess_finalize.py"
    done = subprocess.run(
        [sys.executable, str(script), str(repo), "--drop-entry", first["run_id"]],
        capture_output=True, text=True,
    )
    assert done.returncode == 0, done.stderr
    log = (assess_dir / "log.md").read_text(encoding="utf-8")
    # The run is recorded as a tombstone, not erased: no heading, no placeholders.
    assert f"> Dropped run {first['run_id']} ({DAY})" in log
    assert log.count(LOG_PLACEHOLDER) == 1
    assert _day_headings(assess_dir) == 1
    assert verify_log_chain(assess_dir) == (True, None)
    finalize_run(assess_dir=assess_dir)
    assert _day_headings(assess_dir) == 1
    assert verify_log_chain(assess_dir) == (True, None)

    # A finalized entry is history: --drop-entry refuses it.
    refused = subprocess.run(
        [sys.executable, str(script), str(repo), "--drop-entry", second["run_id"]],
        capture_output=True, text=True,
    )
    assert refused.returncode == 1
    assert "finalized" in refused.stderr


def test_finalize_refuses_stamped_log_without_this_runs_entry(repo: Path) -> None:
    """In a stamped log, a run whose entry is missing must not fall back to
    filling another run's unfilled entry."""
    assess_dir = repo / ".assess"
    other = _run(repo)
    before = (assess_dir / "log.md").read_text(encoding="utf-8")
    _stage_finalize(assess_dir, other["run_id"])
    ctx_path = assess_dir / "run-context.json"
    ctx = json.loads(ctx_path.read_text(encoding="utf-8"))
    ctx["run_id"] = "20260918000000-deadbeef"
    ctx_path.write_text(json.dumps(ctx), encoding="utf-8")
    fi = assess_dir / ".cache" / "finalize-input.json"
    fi.write_text(fi.read_text(encoding="utf-8").replace(other["run_id"], ctx["run_id"]), encoding="utf-8")
    with pytest.raises(FinalizeValidationError, match="no entry stamped"):
        finalize_run(assess_dir=assess_dir)
    assert (assess_dir / "log.md").read_text(encoding="utf-8") == before


def test_core_replaces_superseded_same_day_entry_without_git(tmp_path: Path) -> None:
    """A target with no git has no commit to key on; two same-day runs still
    replace rather than stack, so finalize is not blocked."""
    root = tmp_path / "plain"
    (root / "src").mkdir(parents=True)
    (root / "src" / "hot.py").write_text("def hot(a):\n    return a\n", encoding="utf-8")
    first = build_run_context(repo_root=root, run_date=DAY, non_interactive=True)
    assert first["measured_commit"]["available"] is False
    second = build_run_context(repo_root=root, run_date=DAY, non_interactive=True)
    assess_dir = root / ".assess"
    assert _day_headings(assess_dir) == 1
    assert second["run_id"] in (assess_dir / "log.md").read_text(encoding="utf-8")
    assert verify_log_chain(assess_dir) == (True, None)


_LEGACY_LOG = (
    "# Assess Log\n\n"
    "## 2026-05-01\n\n"
    "- **Files scored:** 80\n"
    "- **AI Readiness:** 5.0 / 8 (Solid)\n\n"
    "---\n"
)


def test_superseded_same_day_entry_keeps_pre_chain_history(repo: Path) -> None:
    """A pre-chain log's body and the first chained entry parse as one span;
    superseding must not remove it, or the legacy history goes with it."""
    assess_dir = repo / ".assess"
    (assess_dir / "log.md").write_text(_LEGACY_LOG, encoding="utf-8")
    first = _run(repo)
    _run(repo)
    log = (assess_dir / "log.md").read_text(encoding="utf-8")
    assert "## 2026-05-01" in log
    assert first["run_id"] in log
    assert _day_headings(assess_dir) == 2
    assert verify_log_chain(assess_dir) == (True, None)


def test_drop_entry_refuses_span_holding_pre_chain_history(repo: Path) -> None:
    from assess_finalize import drop_unfinalized_entry

    assess_dir = repo / ".assess"
    (assess_dir / "log.md").write_text(_LEGACY_LOG, encoding="utf-8")
    first = _run(repo)
    before = (assess_dir / "log.md").read_text(encoding="utf-8")
    with pytest.raises(FinalizeValidationError, match="before the integrity chain"):
        drop_unfinalized_entry(assess_dir=assess_dir, run_id=first["run_id"])
    assert (assess_dir / "log.md").read_text(encoding="utf-8") == before


def test_earlier_same_date_placeholder_without_run_id_does_not_block_finalize(repo: Path) -> None:
    """An unstamped earlier entry cannot be dropped by id; refusing on it would
    leave finalize with no way out, so it stays as history and finalize fills
    this run's entry."""
    from lib.wiki_writer import LogEntry, append_log_entry

    assess_dir = repo / ".assess"
    append_log_entry(assess_dir, LogEntry(
        run_date=DAY, files_scored=1, readiness_score=0.0,
        maturity_label=LOG_PLACEHOLDER, instructions_grade=None,
        graduated_count=0, regressed_count=0, new_count=0, persistent_count=0,
        top_action="Deterministic ranker not yet wired (LLM picks Top 3)",
    ))
    ctx = _run(repo)
    _stage_finalize(assess_dir, ctx["run_id"])
    finalize_run(assess_dir=assess_dir)
    log = (assess_dir / "log.md").read_text(encoding="utf-8")
    assert log.count(LOG_PLACEHOLDER) == 1
    assert "fixture action" in log
    assert verify_log_chain(assess_dir) == (True, None)
