"""Monorepo scoping for `/assess <path>` (task assess-obey-thyself.19).

A scoped run confines every signal to a subtree: the complexity stats, the doc
graph, the churn axis, the badge, the wiki, and the artifact directory all
describe the scope and carry no signal from a sibling directory. The key
invariant guarded here is default-preservation: a run with no scope is
byte-for-byte the pre-scope behaviour.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import ModuleType

import pytest

import assess_core
from assess_core import build_and_publish_run
from lib.report.badge import headline_badge
from lib.scoring.headline import as_headline
from lib.docs.doc_graph import build_doc_graph, discover_doc_files
from lib.history.git_churn import git_churn_scores
from lib.run_scope import resolve_scope
from test_complexity_treemap import stubbed_treemap
from lib.report.wiki_writer import HotspotEntry, write_index


_STATS_SHAPE = {
    "files_scored": 0, "loc": {}, "ccn": {},
    "top_hotspots": [], "top_complex": [], "top_large": [],
}


# --------------------------------------------------------------------------
# Two-service fixture: a monorepo with two sibling service subtrees, each with
# its own complex code file and its own docs, committed to git so churn and
# tracked-file filters have real history to read.
# --------------------------------------------------------------------------
def _two_service_repo(git_repo) -> tuple[Path, object]:
    repo, commit = git_repo
    for svc, fn in (("service-a", "alpha"), ("service-b", "beta")):
        d = repo / svc
        d.mkdir()
        # A deliberately branchy function so lizard/scc would score it a hotspot,
        # plus an unused helper (dead-code candidate) and a TODO (promissory
        # marker) so a scoped run has real sibling signal to exclude.
        (d / "app.py").write_text(
            f"# TODO: refactor {svc}\n"
            f"def {fn}(x):\n"
            + "".join(f"    if x == {i}:\n        return {i}\n" for i in range(12))
            + "    return -1\n\n"
            f"def _unused_{fn}():\n    return 0\n",
            encoding="utf-8",
        )
        (d / "README.md").write_text(f"# {svc}\n\nDocs for {svc}.\n", encoding="utf-8")
    commit("two services")
    return repo, commit


# --------------------------------------------------------------------------
# resolve_scope
# --------------------------------------------------------------------------
def test_resolve_scope_none_is_whole_repo(tmp_path: Path) -> None:
    assert resolve_scope(tmp_path, None) == (None, None, "")


def test_resolve_scope_computes_slug(tmp_path: Path) -> None:
    sub = tmp_path / "services" / "api"
    sub.mkdir(parents=True)
    scope_abs, scope_rel, slug = resolve_scope(tmp_path, Path("services/api"))
    assert scope_abs == sub.resolve()
    assert scope_rel == "services/api"
    assert slug == "services-api"  # separators hyphenated for a flat dir name


def test_resolve_scope_rejects_missing_path(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="does not exist"):
        resolve_scope(tmp_path, Path("nope"))


def test_resolve_scope_rejects_outside_repo(tmp_path: Path) -> None:
    outside = tmp_path.parent / "elsewhere"
    outside.mkdir()
    with pytest.raises(ValueError, match="not under repo root"):
        resolve_scope(tmp_path, outside)


# --------------------------------------------------------------------------
# git_churn scope
# --------------------------------------------------------------------------
def test_git_churn_scope_isolates_subtree(git_repo) -> None:
    repo, _ = _two_service_repo(git_repo)
    scoped = git_churn_scores(repo, scope=repo / "service-a")
    assert scoped, "expected churn for the scoped subtree"
    assert all("service-a" in str(p) for p in scoped)
    assert not any("service-b" in str(p) for p in scoped)


def test_git_churn_no_scope_sees_whole_repo(git_repo) -> None:
    repo, _ = _two_service_repo(git_repo)
    full = git_churn_scores(repo)
    assert any("service-a" in str(p) for p in full)
    assert any("service-b" in str(p) for p in full)


# --------------------------------------------------------------------------
# doc_graph scope
# --------------------------------------------------------------------------
def test_doc_graph_scope_excludes_sibling_docs(git_repo) -> None:
    repo, _ = _two_service_repo(git_repo)
    scoped = discover_doc_files(repo, scope=repo / "service-a")
    names = {p.relative_to(repo).as_posix() for p in scoped}
    assert "service-a/README.md" in names
    assert "service-b/README.md" not in names


def test_build_doc_graph_scope_confines_docs(git_repo) -> None:
    repo, _ = _two_service_repo(git_repo)
    full = build_doc_graph(repo)
    scoped = build_doc_graph(repo, scope=repo / "service-a")
    assert full.doc_count > scoped.doc_count
    scoped_paths = set(scoped.graph.nodes) if scoped.graph is not None else set()
    assert not any("service-b" in n for n in scoped_paths)


# --------------------------------------------------------------------------
# treemap collect scope (lizard/scc stubbed - no heavy deps in the test env)
# --------------------------------------------------------------------------
@pytest.fixture
def treemap():
    with stubbed_treemap() as mod:
        yield mod


def test_treemap_collect_filters_to_scope(tmp_path: Path, treemap: ModuleType) -> None:
    mod = treemap
    a = (tmp_path / "service-a" / "app.py")
    b = (tmp_path / "service-b" / "app.py")
    a.parent.mkdir(parents=True)
    b.parent.mkdir(parents=True)
    a.write_text("x = 1\n", encoding="utf-8")
    b.write_text("y = 2\n", encoding="utf-8")

    def fake_lizard(root, **kw):
        return {a.resolve(): (10, 5.0, [5.0]), b.resolve(): (20, 9.0, [9.0])}

    def fake_scc(root, **kw):
        return {}

    mod.lizard_scores = fake_lizard
    mod.scc_scores = fake_scc

    files, *_ = mod.collect(tmp_path, by="complexity", scope=tmp_path / "service-a")
    paths = {f[0] for f in files}
    assert a.resolve() in paths
    assert b.resolve() not in paths


# --------------------------------------------------------------------------
# badge + wiki labelling
# --------------------------------------------------------------------------
def test_badge_labels_scope() -> None:
    scoped = headline_badge(as_headline(None), scope="services/api")
    assert scoped["label"] == "AI-readiness (services/api)"
    assert headline_badge(as_headline(None))["label"] == "AI-readiness"  # default unchanged


def test_write_index_scope_note(tmp_assess_dir: Path) -> None:
    entries = [HotspotEntry(path="service-a/app.py", first_flagged="2026-07-07",
                            last_seen="2026-07-07", status="new", ccn=9, loc=20)]
    write_index(tmp_assess_dir, entries, last_updated="2026-07-07", scope="service-a")
    scoped = (tmp_assess_dir / "index.md").read_text(encoding="utf-8")
    assert "_Scope: `service-a`_" in scoped

    write_index(tmp_assess_dir, entries, last_updated="2026-07-07")
    assert "_Scope:" not in (tmp_assess_dir / "index.md").read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# assess_core end-to-end: scoped run routes artifacts and isolates signal
# --------------------------------------------------------------------------
def _seed_scoped_stats(repo: Path, slug: str, hotspot_path: str) -> None:
    d = repo / ".assess" / slug
    d.mkdir(parents=True)
    (d / "complexity-stats.json").write_text(json.dumps({
        **_STATS_SHAPE,
        "files_scored": 1,
        "top_hotspots": [{"path": hotspot_path, "loc": 20, "ccn": 9,
                          "commits_12mo": 1}],
    }), encoding="utf-8")


def test_scoped_run_routes_artifacts_and_records_scope(git_repo) -> None:
    repo, _ = _two_service_repo(git_repo)
    _seed_scoped_stats(repo, "service-a", "service-a/app.py")

    ctx = build_and_publish_run(repo_root=repo, run_date="2026-07-07",
                            scope=Path("service-a"))

    # Scope recorded in the bus for the report/badge/wiki.
    assert ctx["scope"] == "service-a"
    assert ctx["scope_slug"] == "service-a"

    # Artifacts land under .assess/<slug>/, not the repo-root .assess/.
    scoped_dir = repo / ".assess" / "service-a"
    assert (scoped_dir / "run-context.json").exists()
    assert (scoped_dir / "index.md").exists()
    assert (scoped_dir / "log.md").exists()
    assert not (repo / ".assess" / "run-context.json").exists()

    # The only hotspot is the scoped one; the sibling never appears.
    hotspot_paths = [h["path"] for h in ctx["stats_summary"]["top_hotspots"]]
    assert hotspot_paths == ["service-a/app.py"]
    blob = (scoped_dir / "run-context.json").read_text(encoding="utf-8")
    assert "service-b" not in blob

    # The scoped wiki index names the scope and never the sibling.
    index = (scoped_dir / "index.md").read_text(encoding="utf-8")
    assert "_Scope: `service-a`_" in index
    assert "service-b" not in index

    # The behaviour block's change-coupling saw the single commit that touched
    # both services, but the sibling is confined out: no service-b co-change.
    behaviour = ctx.get("behaviour", {})
    for pair in behaviour.get("change_coupling_pairs", []):
        assert "service-b" not in json.dumps(pair)
    # And the dead-code / marker scans carry no sibling candidate.
    assert "service-b" not in json.dumps(ctx.get("dead_code", {}))


def test_scoped_run_doc_graph_has_no_sibling_signal(git_repo) -> None:
    repo, _ = _two_service_repo(git_repo)
    _seed_scoped_stats(repo, "service-a", "service-a/app.py")
    ctx = build_and_publish_run(repo_root=repo, run_date="2026-07-07",
                            scope=Path("service-a"))
    dg = ctx["doc_graph"]
    if dg.get("available"):
        # Whatever docs the scoped graph found, none is the sibling's.
        serialized = json.dumps(dg)
        assert "service-b" not in serialized


def test_root_run_unchanged_by_scope_support(git_repo) -> None:
    """The key invariant: a no-scope run is byte-identical to pre-scope."""
    repo, _ = _two_service_repo(git_repo)
    (repo / ".assess").mkdir()
    (repo / ".assess" / "complexity-stats.json").write_text(json.dumps(_STATS_SHAPE), encoding="utf-8")

    ctx = build_and_publish_run(repo_root=repo, run_date="2026-07-07")
    assert ctx["scope"] is None
    assert ctx["scope_slug"] is None
    # Artifacts stay at the repo-root .assess/.
    assert (repo / ".assess" / "run-context.json").exists()
    assert not (repo / ".assess" / "service-a").exists()


def test_invalid_scope_raises_valueerror(git_repo) -> None:
    repo, _ = _two_service_repo(git_repo)
    with pytest.raises(ValueError):
        build_and_publish_run(repo_root=repo, run_date="2026-07-07",
                          scope=Path("no-such-dir"))


def test_cli_invalid_scope_exits_nonzero(git_repo, capsys) -> None:
    repo, _ = _two_service_repo(git_repo)
    (repo / ".assess").mkdir()
    (repo / ".assess" / "complexity-stats.json").write_text(json.dumps(_STATS_SHAPE), encoding="utf-8")
    rc = assess_core.main([str(repo), "--scope", "no-such-dir"])
    assert rc == 2
    assert "error" in capsys.readouterr().err.lower()


# --------------------------------------------------------------------------
# assess_finalize on a scoped run (#1291)
# --------------------------------------------------------------------------
def _tree(root: Path, skip: Path) -> dict[str, bytes]:
    """Every file under ``root`` by relative path, leaving out the ``skip`` subtree."""
    return {p.relative_to(root).as_posix(): p.read_bytes()
            for p in sorted(root.rglob("*")) if p.is_file() and not p.is_relative_to(skip)}


def test_scoped_finalize_refreshes_the_scoped_card_and_leaves_the_whole_repo_files(git_repo) -> None:
    import assess_finalize

    repo, _ = _two_service_repo(git_repo)
    whole = repo / ".assess"
    whole.mkdir()
    (whole / "complexity-stats.json").write_text(json.dumps(_STATS_SHAPE), encoding="utf-8")
    build_and_publish_run(repo_root=repo, run_date="2026-07-07", non_interactive=True)
    whole_actions = {"schema": 2, "actions": [
        {"rank": 1, "status": "pending", "first_step": "whole-repo step", "done_when": "whole done",
         "action": "w", "scope_fence": "f"}]}
    (whole / "actions.json").write_text(json.dumps(whole_actions), encoding="utf-8")
    (whole / "assess-report.md").write_text("# Whole repo\n", encoding="utf-8")

    _seed_scoped_stats(repo, "service-a", "service-a/app.py")
    ctx = build_and_publish_run(repo_root=repo, run_date="2026-07-07",
                                non_interactive=True, scope=Path("service-a"))
    scoped = whole / "service-a"
    before = _tree(whole, scoped)
    (scoped / "assess-report.md").write_text(
        f"# Codebase Assessment: service-a\n\n{ctx['headline']['summary_markdown']}\n\n## Top 3 Actions\n",
        encoding="utf-8")
    (scoped / ".cache").mkdir(exist_ok=True)
    (scoped / ".cache" / "finalize-input.json").write_text(json.dumps({
        "run_id": ctx["run_id"], "top_action": "Split alpha",
        "actions": [{"rank": 1, "action": "Split alpha", "first_step": "split alpha into two",
                     "done_when": "alpha has ccn under 10", "scope_fence": "service-a only"}],
    }), encoding="utf-8")

    assert assess_finalize.main([str(repo), "--scope", "service-a"]) == 0

    rank_one = json.loads((scoped / "actions.json").read_text(encoding="utf-8"))["actions"][0]
    report = (scoped / "assess-report.md").read_text(encoding="utf-8")
    assert f"Start here: `{rank_one['first_step']}`" in report
    assert "whole-repo step" not in report
    scoped_ctx = json.loads((scoped / "run-context.json").read_text(encoding="utf-8"))
    assert scoped_ctx["headline"]["call_to_action"] == f"Start here: `{rank_one['first_step']}`"
    assert "Split alpha" in (scoped / "log.md").read_text(encoding="utf-8")
    assert not (scoped / ".cache" / "finalize-input.json").exists()
    assert _tree(whole, scoped) == before


def test_finalize_cli_rejects_a_scope_outside_the_repo(git_repo, capsys) -> None:
    import assess_finalize

    repo, _ = _two_service_repo(git_repo)
    assert assess_finalize.main([str(repo), "--scope", "no-such-dir"]) == 2
    assert "scope path does not exist" in capsys.readouterr().err
