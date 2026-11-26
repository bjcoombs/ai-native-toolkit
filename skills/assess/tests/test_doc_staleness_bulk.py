"""Doc staleness under bulk mechanical commits (issue #333).

Moved out of `test_doc_staleness.py` (#822) to keep that file under the size ratchet.
"""
from __future__ import annotations

from pathlib import Path

from lib.docs.doc_staleness import analyze_doc_staleness


def _write(root: Path, rel: str, text: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


# --- Bulk mechanical commits (issue #333) -----------------------------------

_BULK_DOCS = [
    "CLAUDE.md", "docs/architecture.md", "docs/billing.md", "docs/caching.md",
    "docs/deploy.md", "docs/events.md", "docs/glossary.md", "docs/logging.md",
    "docs/metrics.md", "docs/onboarding.md", "docs/releases.md",
    "docs/security.md",
]


def _bulk_fixture(repo: Path, commit) -> None:
    """Twelve docs written 300..190 days ago, one licence-header commit 8 days
    ago touching all twelve, then a genuine one-doc edit 3 days ago."""
    days = 300
    for rel in _BULK_DOCS:
        _write(repo, rel, f"Original text of {rel}.\n")
        commit(f"docs: write {rel}", days_ago=days)
        days -= 10
    for rel in _BULK_DOCS:
        p = repo / rel
        p.write_text("<!-- SPDX-License-Identifier: MIT -->\n" + p.read_text(encoding="utf-8"), encoding="utf-8")
    commit("chore: licence headers", days_ago=8)
    p = repo / "docs/caching.md"
    p.write_text(p.read_text(encoding="utf-8") + "A genuine closing section.\n", encoding="utf-8")
    commit("docs: caching invalidation", days_ago=3)


def _head_sha(repo: Path, rev: str) -> str:
    import subprocess
    return subprocess.run(["git", "-C", str(repo), "rev-parse", rev],
                          capture_output=True, text=True, encoding="utf-8", errors="surrogateescape", check=True).stdout.strip()


def test_bulk_commit_does_not_reset_doc_staleness(git_repo) -> None:
    repo, commit = git_repo
    _bulk_fixture(repo, commit)
    r = analyze_doc_staleness(repo)
    days = {d["path"]: d["last_commit_days"] for d in r["docs"]}
    # Ages count to HEAD (3 days ago), not to today (issue #773).
    expected = {rel: 300 - 10 * i - 3 for i, rel in enumerate(_BULK_DOCS)}
    expected["docs/caching.md"] = 0
    # One day of slack: naive local stamps, so a DST change can move one by an hour.
    assert set(days) == set(expected)
    assert all(abs(days[k] - v) <= 1 for k, v in expected.items()), days
    assert days["docs/caching.md"] == 0


def test_bulk_commit_is_recorded_with_full_sha(git_repo) -> None:
    repo, commit = git_repo
    _bulk_fixture(repo, commit)
    r = analyze_doc_staleness(repo)
    bulk_sha = _head_sha(repo, "HEAD~1")
    skipped = r["bulk_commits_skipped"]
    assert [s["sha"] for s in skipped] == [bulk_sha]
    assert len(skipped[0]["sha"]) == 40
    assert skipped[0]["docs_touched"] == 12
    assert skipped[0]["doc_count"] == 12
    assert r["bulk_commits_skipped_total"] == 1
    assert r["bulk_commit_scan_complete"] is True


def test_single_doc_commit_is_not_bulk(git_repo) -> None:
    repo, commit = git_repo
    _bulk_fixture(repo, commit)
    r = analyze_doc_staleness(repo)
    caching_edit = _head_sha(repo, "HEAD")
    assert caching_edit not in {s["sha"] for s in r["bulk_commits_skipped"]}


def test_doc_created_in_bulk_commit_keeps_its_creation_date(git_repo) -> None:
    """A bulk import that adds every doc is the docs' content creation: a later
    bulk sweep falls back to it, not to the sweep and not to None."""
    repo, commit = git_repo
    for rel in _BULK_DOCS:
        _write(repo, rel, f"Imported {rel}.\n")
    commit("import docs", days_ago=100)
    for rel in _BULK_DOCS:
        p = repo / rel
        p.write_text("<!-- header -->\n" + p.read_text(encoding="utf-8"), encoding="utf-8")
    commit("chore: headers", days_ago=5)
    r = analyze_doc_staleness(repo)
    assert {d["last_commit_days"] for d in r["docs"]} == {95}  # to HEAD, 5 days ago
    assert [s["sha"] for s in r["bulk_commits_skipped"]] == [_head_sha(repo, "HEAD")]


def test_small_repo_all_docs_commit_is_not_bulk(git_repo) -> None:
    """Below the minimum-docs floor, editing every doc at once is ordinary work."""
    repo, commit = git_repo
    for rel in ("README.md", "docs/a.md", "docs/b.md"):
        _write(repo, rel, "v1\n")
    commit("docs", days_ago=50)
    for rel in ("README.md", "docs/a.md", "docs/b.md"):
        _write(repo, rel, "v2\n")
    commit("docs: rewrite", days_ago=4)
    r = analyze_doc_staleness(repo)
    assert {d["last_commit_days"] for d in r["docs"]} == {0}  # the rewrite is HEAD
    assert r["bulk_commits_skipped"] == []


def test_no_git_reports_no_bulk_commits(tmp_path: Path) -> None:
    _write(tmp_path, "README.md", "doc")
    r = analyze_doc_staleness(tmp_path)
    assert r["bulk_commits_skipped"] == []
    assert r["bulk_commits_skipped_total"] == 0


def test_instruction_freshness_skips_bulk_commit(git_repo) -> None:
    """The instruction grader's clock shares the bulk-commit skip."""
    from lib.instructions.instruction_files import grade_instruction_files

    repo, commit = git_repo
    _bulk_fixture(repo, commit)
    files = grade_instruction_files(repo)[0]
    assert files["CLAUDE.md"]["freshness_days"] in (296, 297)  # to HEAD 3 days ago; DST slack


def _sweep(repo: Path, commit, tag: str, days_ago: int, extra=()) -> None:
    for rel in list(_BULK_DOCS) + list(extra):
        p = repo / rel
        p.write_text(f"<!-- {tag} -->\n" + p.read_text(encoding="utf-8"), encoding="utf-8")
    commit(f"chore: {tag}", days_ago=days_ago)


def test_doc_regenerated_only_in_bulk_is_flagged_as_creation_date(git_repo) -> None:
    """Docs regenerated in bulk every release have no content commit: the
    creation-date fallback is disclosed and discounted, not passed off as age."""
    repo, commit = git_repo
    for rel in _BULK_DOCS:
        _write(repo, rel, f"Generated {rel}.\n")
    commit("docs: generate", days_ago=400)
    for n, days in enumerate((200, 30, 2)):
        _sweep(repo, commit, f"regen {n}", days)
    r = analyze_doc_staleness(repo)
    assert r["creation_date_fallback_count"] == 12
    for d in r["docs"]:
        assert d["last_change_basis"] == "creation"
        assert d["confidence"] == "low"


def test_content_dated_doc_has_no_basis_key(git_repo) -> None:
    repo, commit = git_repo
    _bulk_fixture(repo, commit)
    r = analyze_doc_staleness(repo)
    assert r["creation_date_fallback_count"] == 0
    assert all("last_change_basis" not in d for d in r["docs"])


def test_skipped_list_is_capped_and_total_is_kept(git_repo) -> None:
    from lib.history.git_churn import BULK_COMMITS_SKIPPED_CAP

    repo, commit = git_repo
    _bulk_fixture(repo, commit)
    sweeps = BULK_COMMITS_SKIPPED_CAP + 1
    for n in range(sweeps):
        _sweep(repo, commit, f"sweep {n}", days_ago=2)
    r = analyze_doc_staleness(repo)
    assert len(r["bulk_commits_skipped"]) == BULK_COMMITS_SKIPPED_CAP
    assert r["bulk_commits_skipped_total"] == sweeps + 1
    assert r["bulk_commits_skipped"][0]["sha"] == _head_sha(repo, "HEAD")


def test_mass_rename_keeps_pre_rename_content_dates(git_repo) -> None:
    import subprocess

    repo, commit = git_repo
    _bulk_fixture(repo, commit)
    subprocess.run(["git", "-C", str(repo), "mv", "docs", "guide"], check=True)
    commit("chore: move docs to guide", days_ago=1)
    r = analyze_doc_staleness(repo)
    days = {d["path"]: d["last_commit_days"] for d in r["docs"]}
    assert days["guide/caching.md"] == 2  # to HEAD, 1 day ago
    assert days["guide/security.md"] in (188, 189)  # DST slack


def test_scan_incomplete_when_git_log_times_out(git_repo, monkeypatch) -> None:
    """A timed-out history read degrades to the plain newest-commit clock and
    says so, rather than reading as "no bulk commits"."""
    import subprocess

    import lib.history.git_churn as gc

    repo, commit = git_repo
    _bulk_fixture(repo, commit)
    real_run = subprocess.run

    def fake_run(cmd, *a, **k):
        if "--no-renames" in cmd:
            raise subprocess.TimeoutExpired(cmd, 1)
        return real_run(cmd, *a, **k)

    gc.content_commit_clock.cache_clear()
    monkeypatch.setattr(gc.subprocess, "run", fake_run)
    r = analyze_doc_staleness(repo)
    gc.content_commit_clock.cache_clear()
    assert r["bulk_commit_scan_complete"] is False
    assert r["bulk_commits_skipped"] == []
    days = {d["path"]: d["last_commit_days"] for d in r["docs"]}
    assert days["CLAUDE.md"] == 5  # plain newest-commit read: 8 days ago, HEAD 3


def test_shallow_clone_marks_scan_incomplete(git_repo, tmp_path: Path) -> None:
    import subprocess

    repo, commit = git_repo
    _bulk_fixture(repo, commit)
    clone = tmp_path / "shallow"
    subprocess.run(["git", "clone", "-q", "--depth", "2", f"file://{repo}", str(clone)],
                   check=True)
    r = analyze_doc_staleness(clone)
    assert r["bulk_commit_scan_complete"] is False


def test_non_doc_instruction_file_skips_bulk_commit(git_repo) -> None:
    """`.cursorrules` is outside the docs' pathspec; the per-file route applies
    the same bulk skip."""
    from lib.instructions.instruction_files import grade_instruction_files

    repo, commit = git_repo
    _write(repo, ".cursorrules", "Prefer small functions.\n")
    commit("add cursor rules", days_ago=400)
    _bulk_fixture(repo, commit)
    rules = repo / ".cursorrules"
    rules.write_text("# header\n" + rules.read_text(encoding="utf-8"), encoding="utf-8")
    for rel in _BULK_DOCS:
        p = repo / rel
        p.write_text("<!-- again -->\n" + p.read_text(encoding="utf-8"), encoding="utf-8")
    commit("chore: headers everywhere", days_ago=5)
    files = grade_instruction_files(repo)[0]
    assert files[".cursorrules"]["freshness_days"] in (394, 395)  # to HEAD; DST slack


def test_shallow_probe_timeout_marks_scan_incomplete(git_repo, monkeypatch) -> None:
    """A timed-out shallow probe must not escape the clock or vouch for it."""
    import subprocess

    import lib.history.git_churn as gc

    repo, commit = git_repo
    _bulk_fixture(repo, commit)
    real_run = subprocess.run

    def fake_run(cmd, *a, **k):
        if "--is-shallow-repository" in cmd:
            raise subprocess.TimeoutExpired(cmd, 1)
        return real_run(cmd, *a, **k)

    gc.content_commit_clock.cache_clear()
    monkeypatch.setattr(gc.subprocess, "run", fake_run)
    r = analyze_doc_staleness(repo)
    gc.content_commit_clock.cache_clear()
    assert r["bulk_commit_scan_complete"] is False
    days = {d["path"]: d["last_commit_days"] for d in r["docs"]}
    assert days["docs/caching.md"] == 0  # the caching edit is HEAD


def test_rename_scan_failure_marks_scan_incomplete(git_repo, monkeypatch) -> None:
    """A rename map that could not be read leaves renames unfollowed, so the
    completeness flag must not vouch for the scan."""
    import lib.history.change_coupling as cc
    import lib.docs.doc_staleness as ds
    import lib.history.git_churn as gc

    repo, commit = git_repo
    _bulk_fixture(repo, commit)
    monkeypatch.setattr(cc, "build_rename_map",
                        lambda *a, **k: cc.RenameMap({}, complete=False))
    ds._clock_at.cache_clear()
    gc.content_commit_clock.cache_clear()
    r = analyze_doc_staleness(repo)
    ds._clock_at.cache_clear()
    assert r["bulk_commit_scan_complete"] is False
    days = {d["path"]: d["last_commit_days"] for d in r["docs"]}
    assert days["docs/caching.md"] == 0  # the caching edit is HEAD

