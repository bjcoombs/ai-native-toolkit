"""Tests for the doc-staleness metric and doc->code association."""
from __future__ import annotations

from pathlib import Path

from lib.docs.doc_staleness import analyze_doc_staleness


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


def test_association_precedence_and_summary_counts(tmp_path: Path) -> None:
    """Pins the fallback order (nearest-ancestor, parallel docs/ tree, explicit
    links, repo baseline) and the summary counts derived from it."""
    # nearest-ancestor wins over a parallel-tree match on the same name.
    _write(tmp_path, "docs/auth/README.md", "auth")
    _write(tmp_path, "docs/auth/x.py", "x")
    _write(tmp_path, "src/auth/a.py", "x")
    # parallel docs/ tree wins over the doc's explicit links.
    _write(tmp_path, "docs/billing.md", "billing")
    _write(tmp_path, "src/billing/b.py", "x")
    _write(tmp_path, "src/other/o1.py", "x")
    _write(tmp_path, "src/other/o2.py", "x")
    # explicit links, then the baseline.
    _write(tmp_path, "notes.md", "notes")
    _write(tmp_path, "floating.md", "floating")
    r = analyze_doc_staleness(
        tmp_path,
        doc_to_code_edges=[
            {"doc": "docs/billing.md", "code": "src/other/o1.py"},
            {"doc": "docs/billing.md", "code": "src/other/o2.py"},
            {"doc": "notes.md", "code": "src/other/o1.py"},
        ],
    )
    by_path = {d["path"]: d for d in r["docs"]}
    assert by_path["docs/auth/README.md"]["subject_method"] == "nearest-ancestor"
    assert by_path["docs/auth/README.md"]["subject_code_count"] == 1
    assert by_path["docs/billing.md"]["subject_method"] == "parallel-docs-tree"
    assert by_path["docs/billing.md"]["subject_code_count"] == 1
    assert by_path["notes.md"]["subject_method"] == "explicit-links"
    assert by_path["floating.md"]["subject_method"] == "repo-baseline"
    assert by_path["floating.md"]["subject_code_count"] == 0  # no subject (#555)
    assert by_path["floating.md"]["confidence"] == "low"
    assert by_path["notes.md"]["confidence"] == "high"
    assert r["association"] == {
        "code_file_count": 5,
        "doc_count": 4,
        "code_under_base_doc": 1,
        "pct_code_under_base_doc": 0.2,
        "docs_mapping_to_code": 3,
        "pct_docs_mapping_to_code": 0.75,
        "methods": {
            "nearest-ancestor": 1,
            "parallel-docs-tree": 1,
            "explicit-links": 1,
            "repo-baseline": 1,
        },
    }


def test_boilerplate_is_not_a_base_doc(tmp_path: Path) -> None:
    _write(tmp_path, "src/LICENSE.md", "MIT")
    _write(tmp_path, "src/x.py", "x")
    r = analyze_doc_staleness(tmp_path)
    # LICENSE must not claim ownership of src/x.py as a base doc
    assert r["association"]["code_under_base_doc"] == 0


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


# --- Behind-the-subject cap: a lying map must be behind its subject now -----

def _lying_map_paths(staleness: dict, repo: Path) -> list[str]:
    """Run the doc->complexity join with `app.py` over the high-CCN bar."""
    from lib.docs.doc_complexity_join import analyze_doc_complexity_join

    stats = {"ccn": {"p95": 10.0}, "top_complex": [{"path": "app.py", "ccn": 30.0}]}
    joined = analyze_doc_complexity_join(stats, staleness, repo)
    return [u["path"] for u in joined["findings"]["lying_maps"]]


def _churn_then_fix(repo: Path, commit) -> None:
    """Doc frozen for months beside heavy churn, then corrected."""
    (repo / "README.md").write_text("module map", encoding="utf-8")
    (repo / "app.py").write_text("v = 0", encoding="utf-8")
    commit("initial docs+code", days_ago=200)
    for i in range(12):
        (repo / "app.py").write_text(f"v = {i + 1}", encoding="utf-8")
        commit(f"change {i}", days_ago=100 - i * 5)
    (repo / "README.md").write_text("module map, corrected", encoding="utf-8")
    commit("docs: correct the module map", days_ago=6)


def test_doc_edited_after_subject_churn_is_not_flagged(git_repo) -> None:
    """A doc corrected after its subject's last change is current, however
    busy the subject was across the window: ratio 0, no lying_map."""
    repo, commit = git_repo
    _churn_then_fix(repo, commit)

    r = analyze_doc_staleness(repo)
    readme = next(d for d in r["docs"] if d["path"] == "README.md")
    assert readme["window_ratio"] >= 5  # the old whole-window signal still fires
    assert readme["code_churn_since_doc_change"] == 0
    assert readme["ratio"] == 0.0
    assert _lying_map_paths(r, repo) == []


def test_doc_older_than_heavy_churn_is_flagged(git_repo) -> None:
    repo, commit = git_repo
    (repo / "README.md").write_text("module map", encoding="utf-8")
    (repo / "app.py").write_text("v = 0", encoding="utf-8")
    commit("initial docs+code", days_ago=200)
    for i in range(6):
        (repo / "app.py").write_text(f"v = {i + 1}", encoding="utf-8")
        commit(f"change {i}", days_ago=50 - i * 5)

    r = analyze_doc_staleness(repo)
    readme = next(d for d in r["docs"] if d["path"] == "README.md")
    assert readme["code_churn_since_doc_change"] == 6
    assert readme["ratio"] >= 4
    assert _lying_map_paths(r, repo) == ["README.md"]


def test_doc_fixed_then_outrun_again_is_flagged(git_repo) -> None:
    """The fix resets the clock, not the verdict: heavy churn after the
    correction makes the doc a lying map again."""
    repo, commit = git_repo
    _churn_then_fix(repo, commit)
    for i in range(5):
        (repo / "app.py").write_text(f"w = {i}", encoding="utf-8")
        commit(f"post-fix change {i}", days_ago=5 - i)

    r = analyze_doc_staleness(repo)
    readme = next(d for d in r["docs"] if d["path"] == "README.md")
    assert readme["code_churn_since_doc_change"] == 5
    assert readme["ratio"] >= 4
    assert _lying_map_paths(r, repo) == ["README.md"]


def test_doc_and_code_changed_together_is_current(git_repo) -> None:
    """The commit that changes the doc and its code together does not count
    as churn after the doc."""
    repo, commit = git_repo
    (repo / "README.md").write_text("module map", encoding="utf-8")
    (repo / "app.py").write_text("v = 0", encoding="utf-8")
    commit("initial docs+code", days_ago=200)
    for i in range(6):
        (repo / "app.py").write_text(f"v = {i + 1}", encoding="utf-8")
        commit(f"change {i}", days_ago=50 - i * 5)
    (repo / "README.md").write_text("module map v2", encoding="utf-8")
    (repo / "app.py").write_text("v = 99", encoding="utf-8")
    commit("feat: change app and its doc", days_ago=1)

    r = analyze_doc_staleness(repo)
    readme = next(d for d in r["docs"] if d["path"] == "README.md")
    assert readme["code_churn_since_doc_change"] == 0
    assert readme["ratio"] == 0.0


def test_generated_doc_provenance_unaffected_by_cap(git_repo) -> None:
    """Provenance still decides a generated doc: source newer -> lying_map,
    even though the doc's own subject churn since it changed is zero."""
    repo, commit = git_repo
    (repo / "data").mkdir()
    (repo / "data" / "jira.tsv").write_text("rows v1", encoding="utf-8")
    (repo / "README.md").write_text(
        "---\nsource: data/jira.tsv\n---\nnotes", encoding="utf-8",
    )
    (repo / "app.py").write_text("v = 0", encoding="utf-8")
    commit("generate", days_ago=10)
    (repo / "data" / "jira.tsv").write_text("rows v2", encoding="utf-8")
    commit("source moved on", days_ago=1)

    r = analyze_doc_staleness(repo)
    readme = next(d for d in r["docs"] if d["path"] == "README.md")
    assert readme["provenance"]["source_newer"] is True
    assert readme["code_churn_since_doc_change"] == 0
    assert _lying_map_paths(r, repo) == ["README.md"]


def test_failed_commit_read_falls_back_to_window_ratio(git_repo, monkeypatch) -> None:
    """Honest degrade: when the per-commit read fails, the cap is not applied
    and the doc reports the uncapped window ratio with no since-doc count."""
    import lib.docs.doc_staleness as ds

    repo, commit = git_repo
    _churn_then_fix(repo, commit)
    monkeypatch.setattr(ds, "commit_epochs_by_file", lambda *_a, **_k: None)

    r = analyze_doc_staleness(repo)
    readme = next(d for d in r["docs"] if d["path"] == "README.md")
    assert readme["code_churn_since_doc_change"] is None
    assert readme["ratio"] == readme["window_ratio"] >= 5


def test_window_since_maps_every_churn_window_label() -> None:
    """The cap reads the same window `pick_churn_window` chose; an unmatched
    label would silently widen it to full history."""
    from lib.docs.doc_staleness import _window_since
    from lib.history.git_churn import CHURN_WINDOWS

    for label, since in CHURN_WINDOWS:
        assert _window_since(f"commits ({label})") == since
    assert _window_since(None) is None


def test_repo_baseline_doc_is_capped_by_repo_wide_churn(git_repo) -> None:
    """A floating doc (no derivable subject) measures churn after it against
    every code file."""
    repo, commit = git_repo
    (repo / "pkg").mkdir()
    (repo / "pkg" / "app.py").write_text("v = 0", encoding="utf-8")
    (repo / "notes").mkdir()
    (repo / "notes" / "design.md").write_text("design", encoding="utf-8")
    commit("initial", days_ago=200)
    for i in range(4):
        (repo / "pkg" / "app.py").write_text(f"v = {i + 1}", encoding="utf-8")
        commit(f"change {i}", days_ago=100 - i * 5)
    (repo / "notes" / "design.md").write_text("design v2", encoding="utf-8")
    commit("docs: refresh design", days_ago=50)
    (repo / "pkg" / "app.py").write_text("v = 9", encoding="utf-8")
    commit("after doc", days_ago=10)

    r = analyze_doc_staleness(repo)
    design = next(d for d in r["docs"] if d["path"] == "notes/design.md")
    assert design["subject_method"] == "repo-baseline"
    assert design["subject_code_count"] == 0
    assert design["code_churn_since_doc_change"] == 1
    assert design["ratio"] == 1.0


def test_one_multi_file_commit_after_fix_counts_once(git_repo) -> None:
    """The cap counts distinct commits: one follow-up commit touching three
    subject files after a doc fix is one commit behind, not a lying map."""
    repo, commit = git_repo
    _churn_then_fix(repo, commit)
    for name in ("app.py", "util.py", "cli.py"):
        (repo / name).write_text(f"# follow-up {name}", encoding="utf-8")
    commit("feat: one follow-up across three files", days_ago=2)

    r = analyze_doc_staleness(repo)
    readme = next(d for d in r["docs"] if d["path"] == "README.md")
    assert readme["code_churn_since_doc_change"] == 1
    assert readme["ratio"] == 1.0
    assert _lying_map_paths(r, repo) == []


def test_doc_frozen_before_window_keeps_file_commit_ratio(git_repo) -> None:
    """A doc never touched inside the window is behind by all of it, so the
    cap does not swap in the distinct-commit count: three wide refactors keep
    their full file-commit weight and still flag the doc."""
    repo, commit = git_repo
    (repo / "README.md").write_text("module map", encoding="utf-8")
    for i in range(10):
        (repo / f"m{i}.py").write_text("v = 0", encoding="utf-8")
    commit("initial", days_ago=500)
    for j in range(3):
        for i in range(10):
            (repo / f"m{i}.py").write_text(f"v = {j + 1}", encoding="utf-8")
        commit(f"refactor {j}", days_ago=40 - j * 10)

    r = analyze_doc_staleness(repo)
    assert r["churn_window"] == "commits (last 12mo)"
    readme = next(d for d in r["docs"] if d["path"] == "README.md")
    assert readme["code_churn_since_doc_change"] == 3
    assert readme["ratio"] == readme["window_ratio"] == 30.0


# --- Direct-children normalisation for base docs (issue #822) ---------------

def _root_doc_fixture(repo: Path, commit) -> None:
    """A root README naming its direct child `helper.py` and a deep file, with
    deep code under it and no base doc in between, edited 60 days ago."""
    _write(repo, "README.md", "Start at `helper.py`; the engine is `pkg/sub/deep.py`.\n")
    _write(repo, "helper.py", "h = 0\n")
    _write(repo, "app.py", "a = 0\n")
    _write(repo, "pkg/sub/deep.py", "d = 0\n")
    _write(repo, "pkg/sub/other.py", "o = 0\n")
    commit("initial", days_ago=90)
    _write(repo, "README.md", "Start at `helper.py`; the engine is `pkg/sub/deep.py`. Edited.\n")
    commit("docs: edit the readme", days_ago=60)


def _readme(repo: Path) -> dict:
    r = analyze_doc_staleness(repo)
    return next(d for d in r["docs"] if d["path"] == "README.md")


def test_root_doc_not_stale_from_unrelated_deep_commits(git_repo) -> None:
    """Fifty commits to deep files the root doc owns but does not sit beside
    leave it fresh: a base doc is measured against its direct children."""
    from lib.docs.doc_complexity_join import is_stale

    repo, commit = git_repo
    _root_doc_fixture(repo, commit)
    for i in range(50):
        _write(repo, f"pkg/sub/deep{i % 5}.py", f"d = {i}\n")
        commit(f"deep change {i}", days_ago=50 - i // 2)

    readme = _readme(repo)
    assert readme["subject_code_count"] == 9  # ownership still covers the whole tree
    assert readme["measured_code_count"] == 2  # helper.py, app.py
    assert readme["code_churn_since_doc_change"] == 0
    assert not is_stale(readme)


def test_root_doc_stale_once_direct_children_outrun_it(git_repo) -> None:
    from lib.docs.doc_complexity_join import is_stale

    repo, commit = git_repo
    _root_doc_fixture(repo, commit)
    for i in range(3):
        _write(repo, "app.py", f"a = {i + 1}\n")
        commit(f"app change {i}", days_ago=30 - i)

    readme = _readme(repo)
    assert readme["code_churn_since_doc_change"] == 3
    assert is_stale(readme)


def test_renaming_a_named_direct_child_makes_the_doc_stale(git_repo) -> None:
    from lib.docs.doc_complexity_join import is_stale

    repo, commit = git_repo
    _root_doc_fixture(repo, commit)
    (repo / "helper.py").rename(repo / "tools.py")
    commit("refactor: rename helper", days_ago=10)

    readme = _readme(repo)
    assert readme["renamed_children_named"] == 1
    assert is_stale(readme)


def test_renaming_a_named_deeper_path_does_not(git_repo) -> None:
    from lib.docs.doc_complexity_join import is_stale

    repo, commit = git_repo
    _root_doc_fixture(repo, commit)
    (repo / "pkg/sub/deep.py").rename(repo / "pkg/sub/core.py")
    commit("refactor: rename deep", days_ago=10)

    readme = _readme(repo)
    assert readme["renamed_children_named"] == 0
    assert not is_stale(readme)


def test_rename_before_the_doc_edit_does_not_count(git_repo) -> None:
    repo, commit = git_repo
    _root_doc_fixture(repo, commit)
    (repo / "helper.py").rename(repo / "tools.py")
    commit("refactor: rename helper", days_ago=10)
    _write(repo, "README.md", "Start at `tools.py`.\n")
    commit("docs: follow the rename", days_ago=5)

    assert _readme(repo)["renamed_children_named"] == 0


def test_base_doc_with_no_direct_child_code_is_not_measured(git_repo) -> None:
    """A root doc that owns only deep code has nothing beside it to measure,
    so it leaves `measured_docs` rather than reading fresh."""
    repo, commit = git_repo
    _write(repo, "README.md", "map\n")
    _write(repo, "pkg/sub/deep.py", "d = 0\n")
    commit("initial", days_ago=30)

    r = analyze_doc_staleness(repo)
    readme = next(d for d in r["docs"] if d["path"] == "README.md")
    assert readme["subject_method"] == "nearest-ancestor"
    assert readme["measured_code_count"] == 0
    assert r["summary"]["measured_docs"] == 0


def _row(path: str, ratio: float, method: str = "nearest-ancestor") -> dict:
    return {"path": path, "subject_method": method, "ratio": ratio,
            "measured_code_count": 1}


def test_fewer_than_twenty_measured_docs_reads_not_measured() -> None:
    from lib.docs.doc_staleness import MIN_MEASURED_DOCS, staleness_summary

    assert MIN_MEASURED_DOCS == 20
    rows = [_row(f"d{i}/README.md", 5.0 if i < 3 else 0.0) for i in range(19)]
    summary = staleness_summary(rows, reliable=True)
    assert summary["measured_docs"] == 19
    assert summary["stale_docs"] == 3
    assert summary["stale_pct"] is None
    assert summary["stale_pct_reason"] == "too_few_measured_docs"

    rows.append(_row("d19/README.md", 0.0))
    summary = staleness_summary(rows, reliable=True)
    assert summary["stale_pct"] == 15
    assert "stale_pct_reason" not in summary


def test_too_few_measured_docs_reads_not_measured_in_the_tally() -> None:
    """The reason code reaches the level tally as the condition's reason."""
    from lib.scoring.level_tally import _block_reason

    data = {"doc_staleness": {"summary": {
        "measured_docs": 4, "stale_docs": 1, "stale_pct": None,
        "stale_pct_reason": "too_few_measured_docs"}}}
    assert _block_reason(data, "doc_staleness.summary.stale_pct") == "too_few_measured_docs"


def test_block_records_the_normalisation_rule(tmp_path: Path) -> None:
    _write(tmp_path, "README.md", "doc")
    _write(tmp_path, "app.py", "x")
    r = analyze_doc_staleness(tmp_path)
    assert r["normalisation"] == {
        "base_doc_subject": "direct_children",
        "rename_scope": "direct_children",
        "min_measured_docs": 20,
    }


def test_counts_only_keeps_the_822_fields(git_repo) -> None:
    """Counts-only output keeps the rule, the per-row counts and the reason code."""
    from lib.counts_only.content_free import counts_only_scans

    repo, commit = git_repo
    _root_doc_fixture(repo, commit)
    kept = counts_only_scans({"doc_staleness": analyze_doc_staleness(repo)})["doc_staleness"]
    assert kept["normalisation"] == {"base_doc_subject": "direct_children",
                                     "rename_scope": "direct_children", "min_measured_docs": 20}
    row = kept["docs"][0]
    assert (row["measured_code_count"], row["renamed_children_named"]) == (2, 0)
    assert kept["summary"]["stale_pct_reason"] == "too_few_measured_docs"


def test_renaming_the_doc_itself_does_not_make_it_stale(git_repo) -> None:
    """A doc that mentions its former name stays fresh after its own rename: a
    rename is not a content change, so it would otherwise read stale for good."""
    from lib.docs.doc_complexity_join import is_stale

    repo, commit = git_repo
    _write(repo, "CLAUDE.md", "Copy or symlink as `CLAUDE.md`. Start at `app.py`.\n")
    _write(repo, "app.py", "a = 0\n")
    commit("initial", days_ago=30)
    (repo / "CLAUDE.md").rename(repo / "AGENTS.md")
    commit("docs: rename the guide", days_ago=10)

    r = analyze_doc_staleness(repo)
    agents = next(d for d in r["docs"] if d["path"] == "AGENTS.md")
    assert agents["renamed_children_named"] == 0
    assert not is_stale(agents)


def test_a_renamed_path_that_exists_again_does_not_count(git_repo) -> None:
    repo, commit = git_repo
    _root_doc_fixture(repo, commit)
    (repo / "helper.py").rename(repo / "tools.py")
    commit("refactor: rename helper", days_ago=10)
    _write(repo, "helper.py", "h = 1\n")
    commit("feat: a new helper", days_ago=8)

    assert _readme(repo)["renamed_children_named"] == 0


def test_a_base_doc_with_a_source_verdict_stays_measured() -> None:
    """No code beside it, but a provenance verdict that does not rest on churn."""
    from lib.docs.doc_staleness import staleness_summary

    row = {**_row("README.md", 0.0), "measured_code_count": 0,
           "provenance": {"method": "frontmatter", "sources": ["a.py"],
                          "generated_by": None, "source_newer": True}}
    assert staleness_summary([row], reliable=True)["measured_docs"] == 1


def test_doc_freshness_measures_a_sphinx_doc_set(tmp_path: Path) -> None:
    """reST pages and Sphinx `.txt` sources are docs here too (#840)."""
    _write(tmp_path, "docs/conf.py", "source_suffix = ['.rst', '.txt']\n")
    _write(tmp_path, "docs/index.rst", "Demo\n====\n\n.. toctree::\n\n   billing\n   ledger\n")
    _write(tmp_path, "docs/billing.rst", "Billing\n=======\n")
    _write(tmp_path, "docs/ledger.txt", "Ledger\n======\n")
    _write(tmp_path, "docs/requirements.txt", "sphinx\n")
    _write(tmp_path, "src/demo/billing.py", "x = 1\n")
    _write(tmp_path, "src/demo/ledger.py", "y = 1\n")
    r = analyze_doc_staleness(tmp_path, doc_to_code_edges=[
        {"doc": "docs/index.rst", "code": "src/demo/billing.py"}])
    by_path = {d["path"]: d for d in r["docs"]}
    assert sorted(by_path) == ["docs/billing.rst", "docs/index.rst", "docs/ledger.txt"]
    assert by_path["docs/billing.rst"]["subject_method"] == "parallel-docs-tree"
    assert by_path["docs/ledger.txt"]["subject_method"] == "parallel-docs-tree"
    assert by_path["docs/index.rst"]["subject_method"] == "explicit-links"
    assert r["summary"]["measured_docs"] == 3
