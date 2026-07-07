"""Tests for the doc-staleness metric and doc->code association."""
from __future__ import annotations

from pathlib import Path

from lib.doc_staleness import LARGE_REPO_CODE_FILES, analyze_doc_staleness


def _write(root: Path, rel: str, text: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def test_nearest_ancestor_respects_inner_base_doc(tmp_path: Path) -> None:
    """A base doc owns its subtree down to the next base doc, not past it."""
    _write(tmp_path, "src/payments/README.md", "payments")
    _write(tmp_path, "src/payments/pay.py", "x")
    _write(tmp_path, "src/payments/refund.py", "y")
    _write(tmp_path, "src/payments/ledger/ledger.md", "ledger")  # <dir>.md convention
    _write(tmp_path, "src/payments/ledger/l.py", "z")

    r = analyze_doc_staleness(tmp_path)
    by_path = {d["path"]: d for d in r["docs"]}
    # README owns pay.py + refund.py (2), but NOT ledger/l.py (claimed by ledger.md)
    assert by_path["src/payments/README.md"]["subject_code_count"] == 2
    assert by_path["src/payments/README.md"]["subject_method"] == "nearest-ancestor"
    assert by_path["src/payments/ledger/ledger.md"]["subject_code_count"] == 1


def test_parallel_docs_tree_fallback(tmp_path: Path) -> None:
    _write(tmp_path, "docs/auth.md", "auth docs")
    _write(tmp_path, "src/auth/a.py", "x")
    r = analyze_doc_staleness(tmp_path)
    auth = next(d for d in r["docs"] if d["path"] == "docs/auth.md")
    assert auth["subject_method"] == "parallel-docs-tree"
    assert auth["subject_code_count"] == 1


def test_explicit_links_fallback(tmp_path: Path) -> None:
    _write(tmp_path, "notes.md", "see [code](src/x.py)")
    _write(tmp_path, "src/x.py", "x")
    r = analyze_doc_staleness(
        tmp_path, doc_to_code_edges=[{"doc": "notes.md", "code": "src/x.py"}]
    )
    notes = next(d for d in r["docs"] if d["path"] == "notes.md")
    assert notes["subject_method"] == "explicit-links"


def test_repo_baseline_when_no_association(tmp_path: Path) -> None:
    _write(tmp_path, "floating.md", "no links, no co-location")
    _write(tmp_path, "src/x.py", "x")
    r = analyze_doc_staleness(tmp_path)
    floating = next(d for d in r["docs"] if d["path"] == "floating.md")
    assert floating["subject_method"] == "repo-baseline"


def test_boilerplate_is_not_a_base_doc(tmp_path: Path) -> None:
    _write(tmp_path, "src/LICENSE.md", "MIT")
    _write(tmp_path, "src/x.py", "x")
    r = analyze_doc_staleness(tmp_path)
    # LICENSE must not claim ownership of src/x.py as a base doc
    assert r["association"]["code_under_base_doc"] == 0


def test_modularity_large_repo_flag(tmp_path: Path) -> None:
    for i in range(LARGE_REPO_CODE_FILES + 1):
        _write(tmp_path, f"mod{i}/f.py", "x")
    r = analyze_doc_staleness(tmp_path)
    assert r["modularity"]["large_repo"] is True
    assert r["modularity"]["base_doc_coverage_when_present"] == 0.0  # no base docs anywhere
    assert r["modularity"]["base_doc_dir_ratio"] == 0.0


def test_base_doc_coverage_is_size_weighted(tmp_path: Path) -> None:
    """One 30-file service with a base doc should not be drowned by ten
    1-file utility dirs without one. Size-weighting reflects what an agent
    actually needs to navigate; the un-weighted dir ratio is reported alongside
    for transparency.
    """
    # A large module (30 files) with a base doc...
    _write(tmp_path, "services/payments/README.md", "payments")
    for i in range(30):
        _write(tmp_path, f"services/payments/f{i}.py", "x")
    # ...and ten utility/leaf dirs (1 file each) with no doc.
    for i in range(10):
        _write(tmp_path, f"internal/util{i}/u.py", "x")

    r = analyze_doc_staleness(tmp_path)
    # Un-weighted dir ratio is low (1 doc'd dir out of 11), but the size-weighted
    # coverage reflects that ~75% of code sits under a maintained base doc.
    assert r["modularity"]["base_doc_dir_ratio"] < 0.2
    assert r["modularity"]["base_doc_coverage_when_present"] >= 0.7
    # Sanity: the when-present number matches what the association block reports.
    assert r["modularity"]["base_doc_coverage_when_present"] == r["association"]["pct_code_under_base_doc"]


def test_no_git_degrades_to_zero_churn(tmp_path: Path) -> None:
    """Without git history, churn is zero and last_commit_days is None - no crash."""
    _write(tmp_path, "README.md", "doc")
    _write(tmp_path, "app.py", "x")
    r = analyze_doc_staleness(tmp_path)
    assert r["available"] is True
    readme = next(d for d in r["docs"] if d["path"] == "README.md")
    assert readme["last_commit_days"] is None
    assert readme["code_churn_in_window"] == 0


def test_stale_doc_beside_churny_code_has_high_ratio(git_repo) -> None:
    """The decaying-map signal: old doc + actively churning subject = high ratio."""
    repo, commit = git_repo
    (repo / "README.md").write_text("module map", encoding="utf-8")
    (repo / "app.py").write_text("v = 0", encoding="utf-8")
    # Doc committed long ago, outside the 12-month window.
    commit("initial docs+code", days_ago=500)
    # Code churns repeatedly and recently; the doc never moves again.
    for i in range(5):
        (repo / "app.py").write_text(f"v = {i + 1}", encoding="utf-8")
        commit(f"change {i}", days_ago=20 - i * 2)

    r = analyze_doc_staleness(repo)
    readme = next(d for d in r["docs"] if d["path"] == "README.md")
    assert readme["last_commit_days"] is not None and readme["last_commit_days"] >= 400
    assert readme["code_churn_in_window"] >= 5
    assert readme["doc_churn_in_window"] == 0  # didn't move inside the window
    assert readme["ratio"] >= 5  # frozen map of a churning module


def test_staleness_uses_author_time_not_committer_time(git_repo) -> None:
    """A rebase must not certify a stale doc as fresh.

    A doc authored 90 days ago but rebased/cherry-picked yesterday (fresh
    committer time, original author time) must still read ~90 days stale. Author
    time (`%at`) reflects when the change was originally made; committer time
    (`%ct`) would reset the clock on every rebase and hide the decay.
    """
    repo, commit = git_repo
    (repo / "README.md").write_text("module map", encoding="utf-8")
    (repo / "app.py").write_text("v = 0", encoding="utf-8")
    # Authored 90 days ago, but committer time is yesterday (the rebase artifact).
    commit("docs authored long ago, rebased yesterday", days_ago=90, committer_days_ago=1)

    r = analyze_doc_staleness(repo)
    readme = next(d for d in r["docs"] if d["path"] == "README.md")
    # Author time wins: ~90 days, not ~1. A committer-time read would report <=2.
    assert readme["last_commit_days"] is not None
    assert readme["last_commit_days"] >= 85


def test_churn_degenerate_flag_on_single_commit_per_file_repo(git_repo) -> None:
    """Issue #172: a history where every file shows one commit (a bulk import /
    shallow clone / squashed tree) is degenerate - the churn count is an
    extraction artifact, not activity. The doc-staleness block surfaces that as
    ``churn_degenerate: True`` so downstream consumers discount churn findings."""
    repo, commit = git_repo
    (repo / "README.md").write_text("module map", encoding="utf-8")
    for i in range(6):
        (repo / f"mod{i}.py").write_text(f"x = {i}", encoding="utf-8")
    commit("bulk import - one commit touches every file", days_ago=10)

    r = analyze_doc_staleness(repo)
    assert r["churn_degenerate"] is True


def test_churn_not_degenerate_with_genuine_variance(git_repo) -> None:
    """Regression guard: a repo with a real spread of commits-per-file is NOT
    degenerate, so its high-confidence churn findings are untouched."""
    repo, commit = git_repo
    (repo / "README.md").write_text("module map", encoding="utf-8")
    for i in range(6):
        (repo / f"mod{i}.py").write_text("start", encoding="utf-8")
    commit("init", days_ago=30)
    # mod0 churns repeatedly; mod1 once more; mod2..5 stay frozen -> a spread.
    for j in range(8):
        (repo / "mod0.py").write_text(f"v = {j}", encoding="utf-8")
        commit(f"churn {j}", days_ago=20 - j)
    (repo / "mod1.py").write_text("v = 99", encoding="utf-8")
    commit("touch mod1", days_ago=5)

    r = analyze_doc_staleness(repo)
    assert r["churn_degenerate"] is False


def test_fresh_doc_beside_fresh_code_has_low_ratio(git_repo) -> None:
    repo, commit = git_repo
    (repo / "README.md").write_text("module map", encoding="utf-8")
    (repo / "app.py").write_text("v = 1", encoding="utf-8")
    commit("recent docs+code", days_ago=5)

    r = analyze_doc_staleness(repo)
    readme = next(d for d in r["docs"] if d["path"] == "README.md")
    assert readme["last_commit_days"] is not None and readme["last_commit_days"] <= 30
    # doc and code moved together -> ratio stays near 1 (not a decaying map)
    assert readme["ratio"] <= 2


def test_untracked_files_excluded_from_staleness(git_repo) -> None:
    """Untracked personal docs/code are not part of the repo and aren't scored."""
    repo, commit = git_repo
    (repo / "README.md").write_text("doc", encoding="utf-8")
    (repo / "app.py").write_text("x = 1", encoding="utf-8")
    commit("init", days_ago=3)
    (repo / "personal.md").write_text("private", encoding="utf-8")  # untracked

    r = analyze_doc_staleness(repo)
    doc_paths = {d["path"] for d in r["docs"]}
    assert "README.md" in doc_paths
    assert "personal.md" not in doc_paths
    assert r["association"]["doc_count"] == 1  # only the tracked doc counted


def test_doc_staleness_honors_user_excludes(tmp_path: Path) -> None:
    """User-supplied excludes drop docs AND code under the named dir from
    every staleness calculation - so a `regulatory-raw/` directory full of
    CSV-driven Python loaders doesn't inflate the code-churn denominator
    or surface as a stale-hub candidate."""
    _write(tmp_path, "README.md", "main doc")
    _write(tmp_path, "src/app.py", "x = 1")
    _write(tmp_path, "regulatory-raw/loader.py", "y = 2")
    _write(tmp_path, "regulatory-raw/notes.md", "ref data")

    # Baseline: regulatory-raw files are counted.
    r = analyze_doc_staleness(tmp_path)
    assert r["association"]["doc_count"] == 2
    assert r["association"]["code_file_count"] == 2

    # With the exclude: regulatory-raw drops out of both code and docs.
    r = analyze_doc_staleness(
        tmp_path, extra_exclude_dirs={"regulatory-raw"},
    )
    assert r["association"]["doc_count"] == 1
    assert r["association"]["code_file_count"] == 1
    assert all("regulatory-raw" not in d["path"] for d in r["docs"])


def test_doc_staleness_excludes_test_fixtures(tmp_path: Path) -> None:
    """Markdown and code under `**/tests/fixtures/**` are scanner inputs, not
    repo content, so they must not count toward the staleness association
    (issue #83)."""
    _write(tmp_path, "README.md", "main doc")
    _write(tmp_path, "src/app.py", "x = 1")
    _write(tmp_path, "tests/fixtures/sample/CLAUDE.md", "fixture")
    _write(tmp_path, "tests/fixtures/sample/loader.py", "y = 2")

    r = analyze_doc_staleness(tmp_path)
    assert r["association"]["doc_count"] == 1
    assert r["association"]["code_file_count"] == 1
    assert all("fixtures" not in d["path"] for d in r["docs"])


# --- Provenance-aware staleness for generated docs (issue #178) -----------

def test_generated_doc_source_newer_is_flagged(git_repo) -> None:
    """A generated doc whose declared source committed AFTER it -> source_newer
    True. Staleness is measured against the source, not the doc's own age."""
    repo, commit = git_repo
    (repo / "data").mkdir()
    (repo / "data" / "jira.tsv").write_text("rows v1", encoding="utf-8")
    (repo / "notes").mkdir()
    (repo / "notes" / "dump.md").write_text(
        "---\nsource: data/jira.tsv\ngenerated_by: scripts/gen.py\n---\nnotes",
        encoding="utf-8",
    )
    commit("generate notes from source", days_ago=24)
    # The source moves on; the generated dump is never regenerated.
    (repo / "data" / "jira.tsv").write_text("rows v2", encoding="utf-8")
    commit("source data updated", days_ago=1)

    r = analyze_doc_staleness(repo)
    dump = next(d for d in r["docs"] if d["path"] == "notes/dump.md")
    assert dump["provenance"]["method"] == "frontmatter"
    assert dump["provenance"]["sources"] == ["data/jira.tsv"]
    assert dump["provenance"]["generated_by"] == "scripts/gen.py"
    assert dump["provenance"]["source_newer"] is True


def test_generated_doc_source_not_newer_is_fresh(git_repo) -> None:
    """An old-but-accurate generated doc (regenerated AFTER its source last
    moved) is fresh: source_newer False and ratio forced to 0 so no downstream
    consumer reads it as a decaying/lying map."""
    repo, commit = git_repo
    (repo / "data").mkdir()
    (repo / "data" / "jira.tsv").write_text("rows", encoding="utf-8")
    commit("source data", days_ago=30)
    # Regenerate the dump well after the source last changed.
    (repo / "notes").mkdir()
    (repo / "notes" / "dump.md").write_text(
        "---\nsource: data/jira.tsv\n---\nnotes", encoding="utf-8",
    )
    commit("regenerate notes", days_ago=2)

    r = analyze_doc_staleness(repo)
    dump = next(d for d in r["docs"] if d["path"] == "notes/dump.md")
    assert dump["provenance"]["source_newer"] is False
    assert dump["ratio"] == 0.0  # provably matches source -> not a decaying map


def test_generated_doc_via_config_mapping(tmp_path: Path) -> None:
    """A bulk-generated tree declares provenance via .assess/config.toml rather
    than per-file frontmatter; staleness still measured against the source."""
    import os
    (tmp_path / "data").mkdir()
    src = tmp_path / "data" / "jira.tsv"
    src.write_text("rows", encoding="utf-8")
    (tmp_path / "notes").mkdir()
    doc = tmp_path / "notes" / "123.md"
    doc.write_text("no frontmatter here", encoding="utf-8")
    os.utime(doc, (1_000_000, 1_000_000))
    os.utime(src, (2_000_000, 2_000_000))  # source newer than the doc

    r = analyze_doc_staleness(
        tmp_path, generated_sources=[("notes", ["data/jira.tsv"])]
    )
    doc_row = next(d for d in r["docs"] if d["path"] == "notes/123.md")
    assert doc_row["provenance"]["method"] == "config"
    assert doc_row["provenance"]["source_newer"] is True


def test_ordinary_doc_has_no_provenance_block(tmp_path: Path) -> None:
    _write(tmp_path, "README.md", "plain doc, no source declared")
    _write(tmp_path, "app.py", "x")
    r = analyze_doc_staleness(tmp_path)
    readme = next(d for d in r["docs"] if d["path"] == "README.md")
    assert "provenance" not in readme
