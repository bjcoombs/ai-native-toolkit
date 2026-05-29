"""Tests for the git-log change-coupling and authorship primitives (B1/B2/B4).

Fixtures build synthetic git histories in tmp dirs. Expected values are
hand-computed in each test's docstring/comments so the contract is auditable.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from lib.change_coupling import (
    authorship_analysis,
    change_coupling_pairs,
    containment_ratio,
    parse_commit_file_sets,
)


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
) -> None:
    """Write ``files`` (rel path -> contents), stage, and commit.

    ``author``/``committer`` are (name, email) tuples; ``co_authors`` is a list
    of "Name <email>" strings folded into Co-Authored-By trailers.
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
    _git(repo, "commit", "-q", "-m", body, env=env)


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "dev@example.com")
    _git(repo, "config", "user.name", "Dev Human")
    return repo


# --- parse_commit_file_sets --------------------------------------------------

def test_parse_commit_file_sets_one_set_per_commit(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    _commit(repo, {"a.py": "1", "b.py": "1"}, "c1")
    _commit(repo, {"a.py": "2"}, "c2")

    sets = parse_commit_file_sets(repo)
    # Newest first: [{a.py}, {a.py, b.py}]
    assert len(sets) == 2
    assert sets[0] == {Path("a.py")}
    assert sets[1] == {Path("a.py"), Path("b.py")}


def test_parse_commit_file_sets_no_git_returns_empty(tmp_path: Path) -> None:
    plain = tmp_path / "nogit"
    plain.mkdir()
    (plain / "a.py").write_text("x", encoding="utf-8")
    assert parse_commit_file_sets(plain) == []


def test_parse_commit_file_sets_since_window(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    old_date = "2020-01-01T00:00:00"
    _commit_env = {"GIT_AUTHOR_DATE": old_date, "GIT_COMMITTER_DATE": old_date}
    _write(repo, "old.py", "x")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "old", env=_commit_env)
    _commit(repo, {"new.py": "y"}, "new")  # committed "now"

    sets = parse_commit_file_sets(repo, since="1 year ago")
    flat = {p for s in sets for p in s}
    assert Path("new.py") in flat
    assert Path("old.py") not in flat


# --- change_coupling_pairs (B1) ----------------------------------------------

def test_change_coupling_detects_co_changing_trio(tmp_path: Path) -> None:
    """Three files always committed together over 4 commits -> 3 pairs, count 4."""
    repo = _init_repo(tmp_path)
    for i in range(4):
        _commit(repo, {"a.py": str(i), "b.py": str(i), "c.py": str(i)}, f"c{i}")

    sets = parse_commit_file_sets(repo)
    pairs = change_coupling_pairs(sets, min_support=3)
    # combinations of {a,b,c} = (a,b),(a,c),(b,c); each co-changed 4 times.
    assert len(pairs) == 3
    for p in pairs:
        assert p["co_change_count"] == 4
        # 4 of 4 commits -> 100% support.
        assert p["support_pct"] == 100.0
    keys = {(p["file_a"], p["file_b"]) for p in pairs}
    assert keys == {("a.py", "b.py"), ("a.py", "c.py"), ("b.py", "c.py")}


def test_change_coupling_respects_min_support(tmp_path: Path) -> None:
    """A pair co-changing only twice is dropped at the default min_support=3."""
    repo = _init_repo(tmp_path)
    _commit(repo, {"x.py": "1", "y.py": "1"}, "c1")
    _commit(repo, {"x.py": "2", "y.py": "2"}, "c2")  # x,y co-change = 2
    _commit(repo, {"x.py": "3"}, "c3")

    sets = parse_commit_file_sets(repo)
    assert change_coupling_pairs(sets, min_support=3) == []
    # Lowering the threshold surfaces it.
    low = change_coupling_pairs(sets, min_support=2)
    assert len(low) == 1
    assert low[0]["co_change_count"] == 2
    assert (low[0]["file_a"], low[0]["file_b"]) == ("x.py", "y.py")


def test_change_coupling_single_file_commits_yield_no_pairs(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    for i in range(5):
        _commit(repo, {"solo.py": str(i)}, f"c{i}")
    sets = parse_commit_file_sets(repo)
    assert change_coupling_pairs(sets) == []


def test_change_coupling_empty_input() -> None:
    assert change_coupling_pairs([]) == []


# --- containment_ratio (B2) --------------------------------------------------

def test_containment_fully_contained_module(tmp_path: Path) -> None:
    """A module whose commits never reach outside it has ratio 1.0."""
    repo = _init_repo(tmp_path)
    for i in range(3):
        _commit(repo, {f"mod/f{i}.py": str(i), "mod/core.py": str(i)}, f"c{i}")
    sets = parse_commit_file_sets(repo)
    assert containment_ratio(repo, "mod", sets) == 1.0


def test_containment_bleeding_module(tmp_path: Path) -> None:
    """Module edits that routinely drag in outside files give a low ratio."""
    repo = _init_repo(tmp_path)
    # 1 self-contained commit...
    _commit(repo, {"mod/a.py": "1"}, "contained")
    # ...and 3 that also touch files outside the module.
    for i in range(3):
        _commit(repo, {"mod/a.py": f"v{i}", "other/b.py": f"v{i}"}, f"bleed{i}")
    sets = parse_commit_file_sets(repo)
    # 4 commits touch mod, only 1 touches mod-only -> 1/4 = 0.25
    assert containment_ratio(repo, "mod", sets) == 0.25


def test_containment_zero_commits_returns_one(tmp_path: Path) -> None:
    """A module no commit touches is vacuously contained (documented: 1.0)."""
    repo = _init_repo(tmp_path)
    _commit(repo, {"other/a.py": "1"}, "c1")
    sets = parse_commit_file_sets(repo)
    assert containment_ratio(repo, "nonexistent", sets) == 1.0


def test_containment_accepts_absolute_module_path(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    for i in range(2):
        _commit(repo, {f"mod/f{i}.py": str(i)}, f"c{i}")
    sets = parse_commit_file_sets(repo)
    # Absolute path is normalised to repo-relative internally.
    assert containment_ratio(repo, repo / "mod", sets) == 1.0


# --- authorship_analysis (B4) ------------------------------------------------

def test_authorship_human_only(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    _commit(repo, {"app.py": "1"}, "c1",
            author=("Alice", "alice@example.com"),
            committer=("Alice", "alice@example.com"))
    r = authorship_analysis(repo, "app.py")
    assert r["authorship_class"] == "human"
    assert r["human_anchor"] is True
    assert r["intent_source"] is True
    assert r["contributors"][0]["email"] == "alice@example.com"
    assert r["contributors"][0]["classification"] == "human"


def test_authorship_mixed_human_author_agent_coauthor(tmp_path: Path) -> None:
    """Human author + Claude co-author = agent involvement alongside a human."""
    repo = _init_repo(tmp_path)
    _commit(repo, {"app.py": "1"}, "feat: thing",
            author=("Bob", "bob@example.com"),
            committer=("Bob", "bob@example.com"),
            co_authors=["Claude <noreply@anthropic.com>"])
    r = authorship_analysis(repo, "app.py")
    assert r["authorship_class"] == "mixed"
    assert r["human_anchor"] is True
    assert r["intent_source"] is True


def test_authorship_pure_agent_via_bot_committer(tmp_path: Path) -> None:
    """Bot author + bot committer with no human anywhere -> 'agent'."""
    repo = _init_repo(tmp_path)
    _commit(repo, {"dep.lock": "1"}, "chore: bump",
            author=("dependabot[bot]", "49699333+dependabot[bot]@users.noreply.github.com"),
            committer=("dependabot[bot]", "49699333+dependabot[bot]@users.noreply.github.com"))
    r = authorship_analysis(repo, "dep.lock")
    assert r["authorship_class"] == "agent"
    assert r["human_anchor"] is False
    assert r["intent_source"] is False
    assert r["contributors"][0]["classification"] == "agent"


def test_authorship_agent_authored_human_committed(tmp_path: Path) -> None:
    """Bot author + human committer: class is 'agent', but intent_source is True."""
    repo = _init_repo(tmp_path)
    _commit(repo, {"x.py": "1"}, "apply bot patch",
            author=("renovate[bot]", "29139614+renovate[bot]@users.noreply.github.com"),
            committer=("Carol", "carol@example.com"))
    r = authorship_analysis(repo, "x.py")
    # agent author + human committer: agent involved, but no human *author*, and
    # n_unknown==0, so the class is 'agent'. The human committer still makes
    # them the intent source (a human directed/applied the change).
    assert r["authorship_class"] == "agent"
    assert r["human_anchor"] is False
    assert r["intent_source"] is True


def test_authorship_human_named_claude_not_libelled(tmp_path: Path) -> None:
    """A real human named 'Claude' with a normal e-mail must NOT be called agent."""
    repo = _init_repo(tmp_path)
    _commit(repo, {"app.py": "1"}, "c1",
            author=("Claude Dupont", "claude.dupont@example.com"),
            committer=("Claude Dupont", "claude.dupont@example.com"))
    r = authorship_analysis(repo, "app.py")
    assert r["authorship_class"] == "human"
    assert r["human_anchor"] is True
    assert r["contributors"][0]["classification"] == "human"


def test_authorship_no_history_degrades(tmp_path: Path) -> None:
    plain = tmp_path / "nogit"
    plain.mkdir()
    r = authorship_analysis(plain, "whatever.py")
    assert r == {
        "human_anchor": False,
        "authorship_class": "unknown",
        "intent_source": False,
        "contributors": [],
    }


def test_authorship_contributor_line_stats(tmp_path: Path) -> None:
    """numstat line counts aggregate per author across commits."""
    repo = _init_repo(tmp_path)
    _commit(repo, {"app.py": "line1\nline2\n"}, "c1",
            author=("Alice", "alice@example.com"),
            committer=("Alice", "alice@example.com"))
    _commit(repo, {"app.py": "line1\nline2\nline3\n"}, "c2",
            author=("Alice", "alice@example.com"),
            committer=("Alice", "alice@example.com"))
    r = authorship_analysis(repo, "app.py")
    alice = r["contributors"][0]
    assert alice["commits"] == 2
    # First commit adds 2 lines; second adds 1 more. 3 added total, 0 removed.
    assert alice["lines_added"] == 3
    assert alice["lines_removed"] == 0
