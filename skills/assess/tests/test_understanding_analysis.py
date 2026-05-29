"""Tests for the understanding analysis (B4) + velocity clock (D2).

Two styles, matching the contract:
  - Pure-logic tests mock ``authorship_by_path`` / ``doc_staleness`` /
    ``complexity_stats`` and assert the orphaned-understanding logic and
    intent-source detection in isolation (repo_root is a non-git temp dir, so
    the velocity clock is exercised through its ``None`` degrade path).
  - Git-integration tests build synthetic histories with controlled commit
    metadata, feed the *real* ``authorship_analysis`` output in, and assert the
    end-to-end classification and the velocity clock against backdated commits.

Expected values are hand-noted so the contract stays auditable.
"""
from __future__ import annotations

import datetime as _dt
import os
import subprocess
from pathlib import Path

from lib.change_coupling import authorship_analysis
from lib.understanding_analysis import analyze_understanding


# --- git fixture helpers (local, for full control of author/committer/date) --

def _git(repo: Path, *args: str, env: dict | None = None) -> None:
    full_env = {**os.environ, **(env or {})}
    subprocess.run(["git", "-C", str(repo), *args],
                   check=True, capture_output=True, text=True, env=full_env)


def _write(repo: Path, rel: str, text: str) -> None:
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _commit(
    repo: Path,
    files: dict[str, str],
    message: str = "change",
    *,
    author: tuple[str, str] | None = None,
    committer: tuple[str, str] | None = None,
    co_authors: list[str] | None = None,
    days_ago: int | None = None,
) -> None:
    """Write ``files`` (rel path -> contents), stage, and commit.

    ``author``/``committer`` are (name, email) tuples; ``co_authors`` is a list
    of "Name <email>" strings folded into Co-Authored-By trailers; ``days_ago``
    backdates author + committer time (git rejects relative strings, so we emit
    a strict ISO timestamp) to drive the velocity clock.
    """
    for rel, text in files.items():
        _write(repo, rel, text)
    _git(repo, "add", "-A")
    body = message
    for ca in co_authors or []:
        body += f"\n\nCo-Authored-By: {ca}"
    env: dict[str, str] = {}
    if author:
        env["GIT_AUTHOR_NAME"], env["GIT_AUTHOR_EMAIL"] = author
    if committer:
        env["GIT_COMMITTER_NAME"], env["GIT_COMMITTER_EMAIL"] = committer
    if days_ago is not None:
        when = _dt.datetime.now() - _dt.timedelta(days=days_ago)
        stamp = when.strftime("%Y-%m-%dT%H:%M:%S")
        env["GIT_AUTHOR_DATE"] = stamp
        env["GIT_COMMITTER_DATE"] = stamp
    _git(repo, "commit", "-q", "-m", body, env=env)


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "dev@example.com")
    _git(repo, "config", "user.name", "Dev Human")
    return repo


# Complexity stats with one high-CCN file. p95 absent -> MIN_HIGH_CCN (10) floor.
def _stats_high(path: str, ccn: float = 25.0) -> dict:
    return {"ccn": {"p95": 0.0}, "top_complex": [{"path": path, "ccn": ccn}]}


def _no_docs() -> dict:
    return {"available": False, "docs": []}


# --- pure-logic: the orphaned-understanding finding --------------------------

def test_orphaned_when_agent_high_complexity_no_doc(tmp_path: Path) -> None:
    """High complexity ∧ no human anchor ∧ no intent source -> orphaned."""
    authorship = {"lib/x.py": {"human_anchor": False, "authorship_class": "agent",
                               "intent_source": False, "contributors": []}}
    result = analyze_understanding(
        tmp_path, authorship, _no_docs(), _stats_high("lib/x.py"))

    assert result["available"] is True
    assert result["orphaned_understanding"] == ["lib/x.py"]
    mod = result["modules"][0]
    assert mod["finding"] == "orphaned_understanding"
    assert mod["authorship_class"] == "agent"
    assert mod["human_anchor"] is False
    assert mod["intent_source"] is False
    assert mod["recommendation"] is not None
    # Non-git repo_root -> the velocity clock degrades to None, never "fresh".
    assert mod["days_since_comprehension_event"] is None


def test_human_anchor_suppresses_orphan(tmp_path: Path) -> None:
    authorship = {"lib/x.py": {"human_anchor": True, "authorship_class": "human",
                               "intent_source": True, "contributors": []}}
    result = analyze_understanding(
        tmp_path, authorship, _no_docs(), _stats_high("lib/x.py"))

    assert result["orphaned_understanding"] == []
    assert result["modules"][0]["finding"] is None
    assert result["modules"][0]["recommendation"] is None


def test_intent_source_suppresses_orphan(tmp_path: Path) -> None:
    """A co-located doc is an intent source even with no human anchor."""
    authorship = {"lib/x.py": {"human_anchor": False, "authorship_class": "agent",
                               "intent_source": False, "contributors": []}}
    doc_staleness = {"available": True, "docs": [{"path": "lib/README.md"}]}
    result = analyze_understanding(
        tmp_path, authorship, doc_staleness, _stats_high("lib/x.py"))

    assert result["orphaned_understanding"] == []
    mod = result["modules"][0]
    assert mod["intent_source"] is True
    assert mod["finding"] is None


def test_low_complexity_not_orphaned(tmp_path: Path) -> None:
    """Below the CCN gate, agent + no doc is still not orphaned."""
    authorship = {"lib/x.py": {"human_anchor": False, "authorship_class": "agent",
                               "intent_source": False, "contributors": []}}
    # ccn 3 is below the MIN_HIGH_CCN (10) floor.
    result = analyze_understanding(
        tmp_path, authorship, _no_docs(), _stats_high("lib/x.py", ccn=3.0))

    assert result["orphaned_understanding"] == []
    assert result["modules"][0]["finding"] is None


def test_root_doc_covers_everything(tmp_path: Path) -> None:
    """A repo-root doc is an ancestor of all paths -> intent source everywhere."""
    authorship = {"lib/x.py": {"human_anchor": False, "authorship_class": "agent",
                               "intent_source": False, "contributors": []}}
    doc_staleness = {"available": True, "docs": [{"path": "README.md"}]}
    result = analyze_understanding(
        tmp_path, authorship, doc_staleness, _stats_high("lib/x.py"))

    assert result["modules"][0]["intent_source"] is True
    assert result["orphaned_understanding"] == []


def test_unrelated_doc_is_not_intent_source(tmp_path: Path) -> None:
    """A doc in a sibling subtree does not cover the module."""
    authorship = {"lib/x.py": {"human_anchor": False, "authorship_class": "agent",
                               "intent_source": False, "contributors": []}}
    doc_staleness = {"available": True, "docs": [{"path": "other/README.md"}]}
    result = analyze_understanding(
        tmp_path, authorship, doc_staleness, _stats_high("lib/x.py"))

    assert result["modules"][0]["intent_source"] is False
    assert result["orphaned_understanding"] == ["lib/x.py"]


def test_unavailable_when_no_authorship(tmp_path: Path) -> None:
    result = analyze_understanding(tmp_path, {}, _no_docs(), {})
    assert result["available"] is False
    assert result["modules"] == []
    assert result["orphaned_understanding"] == []


def test_mixed_class_passthrough(tmp_path: Path) -> None:
    authorship = {"lib/x.py": {"human_anchor": True, "authorship_class": "mixed",
                               "intent_source": True, "contributors": []}}
    result = analyze_understanding(
        tmp_path, authorship, _no_docs(), _stats_high("lib/x.py"))
    assert result["modules"][0]["authorship_class"] == "mixed"


# --- git-integration: real authorship + the velocity clock -------------------

def test_agent_only_repo_is_orphaned(tmp_path: Path) -> None:
    """Only agent commits, no doc, high complexity -> orphaned; clock is None."""
    repo = _init_repo(tmp_path)
    _commit(repo, {"svc.py": "v1"}, "feat",
            author=("dependabot[bot]", "49699333+dependabot[bot]@users.noreply.github.com"),
            committer=("dependabot[bot]", "49699333+dependabot[bot]@users.noreply.github.com"))

    authorship = {"svc.py": authorship_analysis(repo, "svc.py")}
    assert authorship["svc.py"]["authorship_class"] == "agent"

    result = analyze_understanding(repo, authorship, _no_docs(), _stats_high("svc.py"))
    assert result["orphaned_understanding"] == ["svc.py"]
    mod = result["modules"][0]
    assert mod["finding"] == "orphaned_understanding"
    # No human-authored commit exists -> clock indeterminate.
    assert mod["days_since_comprehension_event"] is None


def test_human_commit_sets_anchor_and_clock(tmp_path: Path) -> None:
    """A backdated human commit -> human_anchor, no orphan, dated velocity clock."""
    repo = _init_repo(tmp_path)
    _commit(repo, {"svc.py": "v1"}, "feat",
            author=("Alice", "alice@example.com"),
            committer=("Alice", "alice@example.com"),
            days_ago=30)

    authorship = {"svc.py": authorship_analysis(repo, "svc.py")}
    assert authorship["svc.py"]["human_anchor"] is True

    result = analyze_understanding(repo, authorship, _no_docs(), _stats_high("svc.py"))
    assert result["orphaned_understanding"] == []
    mod = result["modules"][0]
    assert mod["finding"] is None
    # Clock reads ~30 days; allow a day of rounding/clock drift.
    assert mod["days_since_comprehension_event"] is not None
    assert 29 <= mod["days_since_comprehension_event"] <= 31


def test_mixed_repo_classified_mixed(tmp_path: Path) -> None:
    """Human author + agent co-author -> mixed, and the clock follows the human."""
    repo = _init_repo(tmp_path)
    _commit(repo, {"svc.py": "v1"}, "feat",
            author=("Bob", "bob@example.com"),
            committer=("Bob", "bob@example.com"),
            co_authors=["Claude <noreply@anthropic.com>"],
            days_ago=10)

    authorship = {"svc.py": authorship_analysis(repo, "svc.py")}
    assert authorship["svc.py"]["authorship_class"] == "mixed"

    result = analyze_understanding(repo, authorship, _no_docs(), _stats_high("svc.py"))
    mod = result["modules"][0]
    assert mod["authorship_class"] == "mixed"
    assert mod["human_anchor"] is True
    assert mod["finding"] is None
    assert 9 <= mod["days_since_comprehension_event"] <= 11


def test_agent_commit_does_not_count_as_comprehension_event(tmp_path: Path) -> None:
    """Newest commit is an agent's; the clock dates the older human commit."""
    repo = _init_repo(tmp_path)
    _commit(repo, {"svc.py": "v1"}, "human work",
            author=("Carol", "carol@example.com"),
            committer=("Carol", "carol@example.com"),
            days_ago=40)
    _commit(repo, {"svc.py": "v2"}, "agent tweak",
            author=("github-actions[bot]", "github-actions[bot]@users.noreply.github.com"),
            committer=("github-actions[bot]", "github-actions[bot]@users.noreply.github.com"),
            days_ago=5)

    authorship = {"svc.py": authorship_analysis(repo, "svc.py")}
    result = analyze_understanding(repo, authorship, _no_docs(), _stats_high("svc.py"))
    mod = result["modules"][0]
    # The agent commit (5 days ago) is ignored; the clock reads the human one.
    assert 39 <= mod["days_since_comprehension_event"] <= 41
