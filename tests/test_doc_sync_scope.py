"""The doc-sync scope and findings scripts (issue #1453).

`skills/doc-contradictions/scripts/doc_clusters.py` builds the clusters and,
with `--changed-from`, keeps only those a change touches;
`doc_sync_findings.py` checks the comparers' citations and sorts the findings
into review threads and the summary. The fixture repository is
`skills/doc-contradictions/tests/fixtures/doc-vs-code-default/`, whose planted
contradiction is `docs/caching.md:9` (600 seconds) against `src/cache.py:4`
(300). The comparer itself is a model and is graded by the evals under
`evals/doc-contradictions/`; here its output is the finding that eval expects.
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "skills" / "doc-contradictions" / "scripts"
FIXTURES = REPO / "skills" / "doc-contradictions" / "tests" / "fixtures"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import doc_clusters  # noqa: E402
import doc_sync_findings  # noqa: E402

GIT_ENV = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
           "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com"}

PLANTED = {
    "cluster": "c01",
    "kind": "doc-vs-code",
    "subject": "cache expiry default",
    "a": {"path": "docs/caching.md", "line": 9, "quote": "Entries expire after 600 seconds by default."},
    "b": {"path": "src/cache.py", "line": 4, "quote": "DEFAULT_TTL_SECONDS = 300"},
    "explanation": "The doc says 600 seconds; the code default is 300.",
    "confidence": "high",
}


def git(repo: Path, *args: str) -> str:
    done = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, encoding="utf-8",
                          errors="replace", env=GIT_ENV, check=True)
    return done.stdout


@pytest.fixture
def fixture_repo(tmp_path: Path) -> Path:
    """The planted fixture as a git repository, plus one unrelated file."""
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURES / "doc-vs-code-default", repo)
    (repo / "tools").mkdir()
    (repo / "tools" / "notes.txt").write_text("unrelated\n", encoding="utf-8")
    git(repo, "init", "-q", "-b", "main")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "base")
    git(repo, "checkout", "-q", "-b", "pr")
    return repo


def commit(repo: Path, rel: str, text: str) -> None:
    (repo / rel).write_text(text, encoding="utf-8")
    git(repo, "commit", "-q", "-am", "change " + rel)


def scope(repo: Path, *extra: str) -> Dict[str, Any]:
    out = repo.parent / "clusters.json"
    assert doc_clusters.main(["--repo", str(repo), "--out", str(out), *extra]) == 0
    result: Dict[str, Any] = json.loads(out.read_text(encoding="utf-8"))
    return result


def plan(repo: Path, raw: List[Dict[str, Any]], *extra: str) -> Dict[str, Any]:
    clusters = repo.parent / "clusters.json"
    if not clusters.exists():
        scope(repo, "--changed-from", "main", *extra)
    raw_path = repo.parent / "raw.json"
    raw_path.write_text(json.dumps({"findings": raw}), encoding="utf-8")
    out = repo.parent / "findings.json"
    assert doc_sync_findings.main(["--repo", str(repo), "--clusters", str(clusters),
                                   "--raw", str(raw_path), "--out", str(out)]) == 0
    result: Dict[str, Any] = json.loads(out.read_text(encoding="utf-8"))
    return result


# --- doc_clusters.py --------------------------------------------------------

def test_full_scan_clusters_the_doc_with_its_subject_code(fixture_repo: Path) -> None:
    result = scope(fixture_repo)
    assert result["changed_from"] is None
    assert result["clusters"] == [{"id": "c01", "docs": ["README.md", "docs/caching.md"],
                                   "subject_code": ["src/cache.py"]}]


def test_changed_code_keeps_the_cluster_that_describes_it(fixture_repo: Path) -> None:
    commit(fixture_repo, "src/cache.py",
           (fixture_repo / "src/cache.py").read_text(encoding="utf-8") + "# touched\n")
    result = scope(fixture_repo, "--changed-from", "main")
    assert result["changed"] == ["src/cache.py"]
    assert [c["docs"] for c in result["clusters"]] == [["README.md", "docs/caching.md"]]
    assert result["clusters_total"] == 1


def test_unrelated_change_keeps_no_cluster(fixture_repo: Path) -> None:
    commit(fixture_repo, "tools/notes.txt", "still unrelated\n")
    result = scope(fixture_repo, "--changed-from", "main")
    assert result["changed"] == ["tools/notes.txt"]
    assert result["clusters"] == []
    assert result["cross_cluster"] is False


def test_changed_from_limits_clusters_to_the_touched_ones(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    for rel, text in {
        "docs/a.md": "See `src/a.py`.\n", "src/a.py": "A = 1\n",
        "docs/b.md": "See `src/b.py`.\n", "src/b.py": "B = 1\n",
        "guide/c.md": "Loose doc.\n",
    }.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(text, encoding="utf-8")
    git(repo, "init", "-q", "-b", "main")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "base")
    git(repo, "checkout", "-q", "-b", "pr")
    assert scope(repo)["clusters_total"] == 3
    commit(repo, "src/b.py", "B = 2\n")
    result = scope(repo, "--changed-from", "main")
    assert [c["docs"] for c in result["clusters"]] == [["docs/b.md"]]
    assert result["clusters_total"] == 3


def test_deleted_subject_code_still_selects_the_doc_that_names_it(fixture_repo: Path) -> None:
    git(fixture_repo, "rm", "-q", "src/cache.py")
    git(fixture_repo, "commit", "-q", "-m", "drop the cache")
    result = scope(fixture_repo, "--changed-from", "main")
    assert any("docs/caching.md" in c["docs"] for c in result["clusters"])


def test_cross_cluster_pass_runs_only_when_a_hub_changed(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    files = {"index.md": "[a](docs/a.md) [b](docs/b.md)\n", "docs/a.md": "[i](../index.md)\n",
             "docs/b.md": "[i](../index.md)\n", "guide/x.md": "[i](../index.md)\n",
             "other/y.md": "loose\n", "other/z.md": "loose\n", "src/m.py": "M = 1\n"}
    for rel, text in files.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(text, encoding="utf-8")
    git(repo, "init", "-q", "-b", "main")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "base")
    git(repo, "checkout", "-q", "-b", "pr")
    full = scope(repo)
    assert full["hubs"] == ["index.md"] and full["cross_cluster"] is True
    commit(repo, "other/y.md", "loose, edited\n")
    assert scope(repo, "--changed-from", "main")["cross_cluster"] is False
    commit(repo, "index.md", "[a](docs/a.md) [b](docs/b.md) edited\n")
    assert scope(repo, "--changed-from", "main")["cross_cluster"] is True


def test_assess_record_and_history_are_excluded(fixture_repo: Path) -> None:
    for rel in (".assess/report.md", "docs/design/old.md"):
        (fixture_repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (fixture_repo / rel).write_text("See `src/cache.py`.\n", encoding="utf-8")
    git(fixture_repo, "add", "-A", "-f")
    git(fixture_repo, "commit", "-q", "-m", "record")
    scoped = scope(fixture_repo, "--exclude", ".assess/**")
    assert scoped["excluded"]["docs/design/**"] == 1 and scoped["excluded"][".assess/**"] == 1
    docs = {d for c in scoped["clusters"] for d in c["docs"]}
    assert ".assess/report.md" not in docs and "docs/design/old.md" not in docs
    docs = {d for c in scope(fixture_repo, "--no-default-excludes")["clusters"] for d in c["docs"]}
    assert {".assess/report.md", "docs/design/old.md"} <= docs


def test_oversized_cluster_is_split(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / "docs").mkdir(parents=True)
    for i in range(10):
        (repo / "docs" / ("d%d.md" % i)).write_text("See `src/x.py`.\n", encoding="utf-8")
    (repo / "src").mkdir()
    (repo / "src" / "x.py").write_text("X = 1\n", encoding="utf-8")
    sizes = [len(c["docs"]) for c in scope(repo)["clusters"]]
    assert sizes == [8, 2]


@pytest.mark.parametrize("pattern,path,hit", [
    ("CHANGELOG*", "pkg/CHANGELOG.md", True),
    ("docs/design/**", "docs/design/a/b.md", True),
    ("docs/design/**", "docs/designs.md", False),
    ("**/tests/fixtures/**", "skills/x/tests/fixtures/a.md", True),
    (".assess/**", ".assess/wiki/x.md", True),
    (".assess/**", "docs/.assess.md", False),
])
def test_glob_regex(pattern: str, path: str, hit: bool) -> None:
    assert bool(doc_clusters.glob_regex(pattern).match(path)) is hit


# --- doc_sync_findings.py ---------------------------------------------------

def test_planted_contradiction_gives_exactly_one_thread(fixture_repo: Path) -> None:
    commit(fixture_repo, "src/cache.py",
           (fixture_repo / "src/cache.py").read_text(encoding="utf-8") + "# touched\n")
    result = plan(fixture_repo, [PLANTED])
    assert result["status"] == "checked"
    assert [(t["a"]["path"], t["a"]["line"], t["b"]["line"]) for t in result["threads"]] == [
        ("docs/caching.md", 9, 4)]
    assert result["summary"] == [] and result["dropped"] == []


def test_high_finding_on_unchanged_files_goes_to_the_summary(fixture_repo: Path) -> None:
    commit(fixture_repo, "README.md",
           (fixture_repo / "README.md").read_text(encoding="utf-8") + "\nMore.\n")
    result = plan(fixture_repo, [PLANTED])
    assert result["threads"] == [] and len(result["summary"]) == 1


def test_citation_check_moves_near_lines_and_drops_misses(fixture_repo: Path) -> None:
    commit(fixture_repo, "src/cache.py",
           (fixture_repo / "src/cache.py").read_text(encoding="utf-8") + "# touched\n")
    near = json.loads(json.dumps(PLANTED))
    near["a"]["line"] = 11
    missing = json.loads(json.dumps(PLANTED))
    missing["b"]["quote"] = "DEFAULT_TTL_SECONDS = 999"
    multi = json.loads(json.dumps(PLANTED))
    multi["a"]["quote"] = "line one\nline two"
    escape = json.loads(json.dumps(PLANTED))
    escape["b"]["path"] = "../outside.py"
    result = plan(fixture_repo, [near, missing, multi, escape])
    assert [t["a"]["line"] for t in result["threads"]] == [9]
    assert len(result["dropped"]) == 3


def test_duplicates_merge_and_text_is_cut(fixture_repo: Path) -> None:
    commit(fixture_repo, "src/cache.py",
           (fixture_repo / "src/cache.py").read_text(encoding="utf-8") + "# touched\n")
    low = dict(PLANTED, confidence="low", explanation="x" * 5000)
    flipped = dict(PLANTED, a=PLANTED["b"], b=PLANTED["a"], kind="doc-vs-doc")
    result = plan(fixture_repo, [low, PLANTED, flipped])
    assert len(result["threads"]) == 1 and result["summary"] == []
    assert result["threads"][0]["confidence"] == "high"
    lone = plan(fixture_repo, [low])["summary"][0]
    assert len(lone["why"]) <= doc_sync_findings.MAX_TEXT


def test_model_text_cannot_mention_users(fixture_repo: Path) -> None:
    commit(fixture_repo, "src/cache.py",
           (fixture_repo / "src/cache.py").read_text(encoding="utf-8") + "# touched\n")
    noisy = dict(PLANTED, subject="ping @someone", explanation="see @octocat")
    ghost = dict(PLANTED, a={"path": "@types/node/README.md", "line": 1, "quote": "x"})
    result = plan(fixture_repo, [noisy, ghost])
    thread = result["threads"][0]
    assert "@someone" not in thread["subject"] and "@octocat" not in thread["why"]
    assert len(result["dropped"]) == 1 and "@types" not in result["dropped"][0]


def test_finding_citing_an_excluded_path_is_dropped(fixture_repo: Path) -> None:
    (fixture_repo / ".assess").mkdir()
    (fixture_repo / ".assess" / "run.md").write_text("TTL is 600\n", encoding="utf-8")
    git(fixture_repo, "add", "-A", "-f")
    git(fixture_repo, "commit", "-q", "-m", "record")
    cited = dict(PLANTED, a={"path": ".assess/run.md", "line": 1, "quote": "TTL is 600"})
    result = plan(fixture_repo, [cited], "--exclude", ".assess/**")
    assert result["threads"] == [] and result["summary"] == [] and result["dropped_excluded"] == 1


def test_missing_model_output_is_an_error_plan(fixture_repo: Path, tmp_path: Path) -> None:
    clusters = tmp_path / "clusters.json"
    scope(fixture_repo, "--changed-from", "main")
    out = tmp_path / "findings.json"
    assert doc_sync_findings.main(["--repo", str(fixture_repo), "--clusters", str(clusters),
                                   "--raw", str(tmp_path / "absent.json"), "--out", str(out)]) == 0
    assert json.loads(out.read_text(encoding="utf-8"))["status"] == "error"
    assert doc_sync_findings.main(["--clusters", str(clusters), "--skipped", "no change",
                                   "--quiet", "--out", str(out)]) == 0
    skipped = json.loads(out.read_text(encoding="utf-8"))
    assert skipped["status"] == "skipped" and skipped["quiet"] is True
