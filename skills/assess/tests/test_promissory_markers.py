"""Tests for the promissory-marker scan (stale TODOs, suppressions, skips).

Fixtures build synthetic git histories in tmp dirs with explicit author dates
so survived-touches counts are deterministic. Expected values are hand-computed
in each test's comments so the contract is auditable.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from lib.keyhole_signals import FINDING_ORDER, integrate
from lib.promissory_markers import (
    FAMILY_PATTERNS,
    MarkerScan,
    scan_promissory_markers,
)

_HUMAN = ("Dev One", "dev@example.com")
_AGENT = ("Claude", "claude[bot]@users.noreply.github.com")


def _git(repo: Path, *args: str, env: dict | None = None) -> None:
    full_env = {**os.environ, **(env or {})}
    subprocess.run(["git", "-C", str(repo), *args],
                   check=True, capture_output=True, text=True, env=full_env)


def _init_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init", "-q")
    _git(repo, "config", "user.name", _HUMAN[0])
    _git(repo, "config", "user.email", _HUMAN[1])


def _commit(
    repo: Path,
    files: dict[str, str],
    message: str = "change",
    *,
    day: int = 1,
    author: tuple[str, str] | None = None,
) -> None:
    """Commit files with a deterministic author date (2024-01-<day>)."""
    for rel, text in files.items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    _git(repo, "add", "-A")
    name, email = author or _HUMAN
    date = f"2024-01-{day:02d}T12:00:00"
    _git(
        repo, "commit", "-q", "-m", message,
        env={
            "GIT_AUTHOR_NAME": name, "GIT_AUTHOR_EMAIL": email,
            "GIT_COMMITTER_NAME": name, "GIT_COMMITTER_EMAIL": email,
            "GIT_AUTHOR_DATE": date, "GIT_COMMITTER_DATE": date,
        },
    )


def _scan(repo: Path, **kw) -> MarkerScan:
    scan = scan_promissory_markers(repo, **kw)
    assert scan.available, scan.reason
    return scan


# ---------------------------------------------------------------------------
# Detection + classification
# ---------------------------------------------------------------------------

def test_detects_all_four_families(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    _commit(repo, {
        "app.py": "# TODO fix this\nx = 1  # noqa: E501\n",
        "legacy.java": "// @Deprecated use NewThing\nclass A {}\n",
        "app_test.py": "import pytest\n@pytest.mark.skip\ndef test_x():\n    pass\n",
    }, day=1)
    scan = _scan(repo)
    families = {m.family for m in scan.markers}
    assert families == {"todo", "suppression", "deprecation", "disabled_test"}


def test_string_literal_todo_dropped_but_comment_kept(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    _commit(repo, {
        "app.py": 'msg = "TODO is a word"\n# TODO real one\n',
    }, day=1)
    scan = _scan(repo)
    todos = [m for m in scan.markers if m.family == "todo"]
    assert len(todos) == 1
    assert todos[0].line == 2


def test_case_sensitive_word_boundary() -> None:
    """A Dart toDouble() call must not match the todo family (regression:
    case-insensitive matching flagged every toDouble in a Flutter repo)."""
    import re
    assert not re.search(FAMILY_PATTERNS["todo"], "x = (y as num).toDouble()")


def test_linked_vs_bare_todo(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    _commit(repo, {
        "app.py": "# TODO(#123) tracked\n# TODO untracked\n# TODO JIRA-42 also tracked\n",
    }, day=1)
    scan = _scan(repo)
    s = scan.summary()
    assert s["todo_linked"] == 2
    assert s["todo_bare"] == 1


def test_justified_suppression_counts_as_linked(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    _commit(repo, {
        "a.go": "return nil //nolint:nilerr // error conveyed via response\n",
        "b.go": "return nil //nolint:nilerr\n",
    }, day=1)
    scan = _scan(repo)
    by_path = {m.path: m for m in scan.markers if m.family == "suppression"}
    assert by_path["a.go"].linked is True
    assert by_path["b.go"].linked is False


def test_generated_and_prose_exclusions(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    _commit(repo, {
        # codegen boilerplate: never debt
        "model.g.dart": "// ignore_for_file: type=lint\n",
        # syntactic marker in prose: a code example, not debt
        "docs/guide.md": "Use t.Skip(\"reason\") to skip.\n",
        # prose TODO: real (docs carry intent too)
        "docs/plan.md": "TODO write the rollout section\n",
    }, day=1)
    scan = _scan(repo)
    paths_by_family = {
        fam: {m.path for m in scan.markers if m.family == fam}
        for fam in ("suppression", "disabled_test", "todo")
    }
    assert paths_by_family["suppression"] == set()
    assert paths_by_family["disabled_test"] == set()
    assert paths_by_family["todo"] == {"docs/plan.md"}


# ---------------------------------------------------------------------------
# Aging: survived touches
# ---------------------------------------------------------------------------

def _busy_repo_with_marker(repo: Path, *, edits_after: int) -> None:
    """Marker lands on day 2; `edits_after` later commits touch the same file.

    Three other files get two commits each so churn_is_degenerate() stays
    False (aging_reliable True) without inflating the marker file's count.
    """
    _init_repo(repo)
    _commit(repo, {"app.py": "x = 0\n", "a.py": "a", "b.py": "b", "c.py": "c"}, day=1)
    _commit(repo, {"app.py": "x = 0\n# FIXME handle zero\ny = 1\n"}, day=2)
    for i in range(edits_after):
        _commit(repo, {"app.py": f"x = {i + 1}\n# FIXME handle zero\ny = {i}\n"},
                day=3 + i)
    _commit(repo, {"a.py": "a2", "b.py": "b2", "c.py": "c2"}, day=20)


def test_survived_touches_counts_later_commits(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _busy_repo_with_marker(repo, edits_after=6)
    scan = _scan(repo)
    fixme = [m for m in scan.markers if m.family == "todo"][0]
    # 6 edits after the introducing commit; the introducing commit itself and
    # the day-1 commit don't count.
    assert fixme.survived_touches == 6
    assert fixme.path in scan.stale_by_file()
    assert scan.stale_by_file()["app.py"]["max_survived"] == 6


def test_fresh_marker_is_not_stale(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _busy_repo_with_marker(repo, edits_after=1)
    scan = _scan(repo)
    assert scan.stale_by_file() == {}
    assert scan.summary()["total_stale"] == 0


def test_degenerate_history_marks_aging_unreliable(tmp_path: Path) -> None:
    """Squashed-import shape (every file exactly one commit, many files): the
    scan still reports markers but flags aging as carrying no information."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    files = {f"f{i}.py": f"# TODO item {i}\n" for i in range(12)}
    _commit(repo, files, day=1)
    scan = _scan(repo)
    assert scan.markers  # detection still works
    assert scan.aging_reliable is False


# ---------------------------------------------------------------------------
# Authorship
# ---------------------------------------------------------------------------

def test_agent_vs_human_introduction(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    _commit(repo, {"by_human.py": "# TODO human wrote this\n"}, day=1)
    _commit(repo, {"by_agent.py": "# TODO agent wrote this\n"}, day=2,
            author=_AGENT)
    scan = _scan(repo)
    by_path = {m.path: m for m in scan.markers}
    assert by_path["by_human.py"].agent_introduced is False
    assert by_path["by_agent.py"].agent_introduced is True


# ---------------------------------------------------------------------------
# Degrade contract + keyhole finding
# ---------------------------------------------------------------------------

def test_non_git_dir_degrades(tmp_path: Path) -> None:
    scan = scan_promissory_markers(tmp_path)
    assert scan.available is False
    assert "git" in scan.reason


def test_unactioned_intent_finding_in_integrate(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _busy_repo_with_marker(repo, edits_after=6)
    summary = _scan(repo).summary()
    result = integrate(
        repo_root=repo, complexity_stats={}, doc_staleness={},
        dead_code={}, observability={}, structure={},
        promissory_markers=summary,
    )
    findings = {f["name"]: f for f in result["derived_findings"]}
    assert "unactioned_intent" in findings
    assert findings["unactioned_intent"]["paths"] == ["app.py"]


def test_unactioned_intent_silent_without_reliable_aging(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    files = {f"f{i}.py": f"# TODO item {i}\n" for i in range(12)}
    _commit(repo, files, day=1)
    summary = _scan(repo).summary()
    assert summary["aging_reliable"] is False
    result = integrate(
        repo_root=repo, complexity_stats={}, doc_staleness={},
        dead_code={}, observability={}, structure={},
        promissory_markers=summary,
    )
    findings = {f["name"]: f for f in result["derived_findings"]}
    assert findings["unactioned_intent"]["paths"] == []


def test_finding_order_contains_unactioned_intent() -> None:
    assert "unactioned_intent" in FINDING_ORDER
