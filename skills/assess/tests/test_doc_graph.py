"""Tests for the doc link-graph (Layer 0 navigability)."""
from __future__ import annotations

from pathlib import Path


import lib.doc_graph as doc_graph
from lib.doc_graph import build_doc_graph, group_broken_links


def _write(root: Path, rel: str, text: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def test_empty_repo_is_available_but_zero(tmp_path: Path) -> None:
    r = build_doc_graph(tmp_path)
    assert r.available is True
    assert r.doc_count == 0


def test_linked_wiki_builds_edges_and_reachability(tmp_path: Path) -> None:
    _write(tmp_path, "index.md", "# Index\n[[setup]] [a](api.md) [[guide#intro]]")
    _write(tmp_path, "setup.md", "see [[guide]]")
    _write(tmp_path, "guide.md", "back to [[index]] and [src](app.py)")
    _write(tmp_path, "api.md", "[[setup]]")
    _write(tmp_path, "app.py", "print(1)")
    _write(tmp_path, "lonely.md", "I link to nobody")

    r = build_doc_graph(tmp_path)
    assert r.doc_count == 5  # app.py is code, not a doc node
    assert r.edge_count >= 5
    # index is a declared MOC and a structural hub (out-degree >= 3)
    assert any(m["path"] == "index.md" and m["is_structural_hub"] for m in r.declared_mocs)
    assert r.moc_named_but_not_wired == []
    # lonely.md has no inbound links and is not an entry -> orphan
    assert "lonely.md" in r.orphans
    # two islands: the linked cluster + lonely
    assert r.island_count == 2
    # reachable from entry (index): everything except lonely
    assert 0.7 <= r.reachability_pct <= 0.85
    assert "lonely.md" in r.unreachable
    # guide.md is reachable, proving the [[guide#intro]] anchor was stripped and
    # still resolved to guide.md (a dangling link would have left it unreachable)
    assert "guide.md" not in r.unreachable


def test_doc_to_code_edges_detected(tmp_path: Path) -> None:
    _write(tmp_path, "guide.md", "code is [here](src/app.py)")
    _write(tmp_path, "src/app.py", "x = 1")
    r = build_doc_graph(tmp_path)
    assert {"doc": "guide.md", "code": "src/app.py"} in r.doc_to_code_edges


def test_declared_moc_not_wired_is_flagged(tmp_path: Path) -> None:
    # index.md is named like a MOC but links to nothing -> named but not wired.
    _write(tmp_path, "index.md", "# Index\nNo links here.")
    _write(tmp_path, "a.md", "content")
    _write(tmp_path, "b.md", "content")
    r = build_doc_graph(tmp_path)
    assert "index.md" in r.moc_named_but_not_wired
    moc = next(m for m in r.declared_mocs if m["path"] == "index.md")
    assert moc["is_structural_hub"] is False


def test_hubs_ranked_by_centrality(tmp_path: Path) -> None:
    # hub.md is pointed to by many docs -> highest PageRank.
    _write(tmp_path, "hub.md", "I am the hub")
    for i in range(4):
        _write(tmp_path, f"leaf{i}.md", "see [hub](hub.md)")
    r = build_doc_graph(tmp_path)
    assert r.hubs[0]["path"] == "hub.md"
    assert r.hubs[0]["in_degree"] == 4
    # full pagerank map exposed for the heatmap, kept off as_dict()
    assert "hub.md" in r.pagerank
    assert "pagerank" not in r.as_dict()


def test_wikilink_collision_is_counted_ambiguous(tmp_path: Path) -> None:
    _write(tmp_path, "one/setup.md", "a")
    _write(tmp_path, "two/setup.md", "b")
    _write(tmp_path, "home.md", "[[setup]]")
    r = build_doc_graph(tmp_path)
    assert r.ambiguous_wikilinks >= 1


def test_dangling_wikilink_counted(tmp_path: Path) -> None:
    _write(tmp_path, "a.md", "[[does-not-exist]]")
    r = build_doc_graph(tmp_path)
    assert r.dangling_links >= 1


def test_wikilink_inside_fenced_code_block_is_not_counted(tmp_path: Path) -> None:
    """A FORMAT spec or Obsidian-syntax tutorial that *shows* `[[foo]]` as a
    sample inside a fenced code block must not contribute a phantom edge or
    a dangling link. The writer formatted it as code on purpose.
    """
    _write(
        tmp_path,
        "obsidian-skill.md",
        "How to write wikilinks:\n\n```markdown\n[[Note Title]]\n[[wikilinks]]\n```\n",
    )
    r = build_doc_graph(tmp_path)
    assert r.dangling_links == 0
    targets = {bl["target"] for bl in r.broken_links}
    assert "Note Title" not in targets
    assert "wikilinks" not in targets


def test_mdlink_inside_fenced_code_block_is_not_counted(tmp_path: Path) -> None:
    """FORMAT specs commonly show `[Ordering](./src/ordering/CONTEXT.md)` as a
    sample of the format they teach. Inside a fence, that's documentation
    syntax, not navigation - it must not show up in broken_links.
    """
    _write(
        tmp_path,
        "context-FORMAT.md",
        "Example layout:\n\n```markdown\n[Ordering](./src/ordering/CONTEXT.md)\n```\n",
    )
    r = build_doc_graph(tmp_path)
    assert r.dangling_links == 0
    targets = {bl["target"] for bl in r.broken_links}
    assert "./src/ordering/CONTEXT.md" not in targets


def test_link_inside_inline_code_span_is_not_counted(tmp_path: Path) -> None:
    """Inline-code spans (single backticks) are equally code: `[[wikilinks]]`
    in a sentence is teaching syntax, not navigating.
    """
    _write(
        tmp_path,
        "guide.md",
        "Use the `[[Note Title]]` syntax to link notes. "
        "Markdown form looks like `[label](./file.md)`.\n",
    )
    r = build_doc_graph(tmp_path)
    assert r.dangling_links == 0


def test_real_links_outside_code_still_extracted(tmp_path: Path) -> None:
    """Prose-form links to real docs must still build edges. Stripping code
    spans is meant to reduce false positives, not break navigation."""
    _write(tmp_path, "README.md", "see [the guide](./guide.md)\n")
    _write(tmp_path, "guide.md", "real doc")
    r = build_doc_graph(tmp_path)
    assert r.dangling_links == 0
    # Edge must be present.
    edges = {(h["path"], h.get("pagerank", 0)) for h in r.hubs}
    assert any(p == "guide.md" for p, _ in edges) or r.edge_count >= 1


def test_doc_graph_honors_user_exclude_dirs(tmp_path: Path) -> None:
    """A user-supplied exclude (from `.assess/config.toml` or CLI) keeps
    docs inside that directory out of the graph entirely - the same
    semantics every other /assess scan applies. See test_assess_core for
    the orchestrator-level integration that loads excludes once and
    threads them everywhere."""
    _write(tmp_path, "README.md", "see [vetted](./regulatory-raw/notes.md)\n")
    _write(tmp_path, "regulatory-raw/notes.md", "ref data note")

    # Baseline: both docs are counted.
    assert build_doc_graph(tmp_path).doc_count == 2

    # With the exclude: regulatory-raw/notes.md vanishes from the graph.
    r = build_doc_graph(tmp_path, extra_exclude_dirs={"regulatory-raw"})
    assert r.doc_count == 1


def test_doc_graph_honors_user_exclude_patterns(tmp_path: Path) -> None:
    """A glob pattern in the user excludes filters by basename. Same
    fnmatch semantics as `EXCLUDE_FILE_PATTERNS`."""
    _write(tmp_path, "README.md", "real")
    _write(tmp_path, "SCRATCH-NOTES.md", "scratch")

    assert build_doc_graph(tmp_path).doc_count == 2
    r = build_doc_graph(tmp_path, extra_exclude_patterns=["SCRATCH-*.md"])
    assert r.doc_count == 1


def test_excludes_assess_and_vendor_dirs(tmp_path: Path) -> None:
    _write(tmp_path, "README.md", "real doc")
    _write(tmp_path, ".assess/log.md", "our own output")
    _write(tmp_path, "node_modules/pkg/readme.md", "vendored")
    r = build_doc_graph(tmp_path)
    assert r.doc_count == 1


def test_excludes_test_fixtures_and_orphan_rate_reflects_it(tmp_path: Path) -> None:
    """Markdown under `**/tests/fixtures/**` is a scanner input, not a repo
    doc, so it must not count toward the doc graph or inflate the orphan rate
    (issue #83). One linked entry doc -> 0% orphans; without the exclusion the
    fixture files would be unreachable orphans and the rate would spike."""
    _write(tmp_path, "README.md", "see [guide](./guide.md)\n")
    _write(tmp_path, "guide.md", "the guide\n")
    # Fixtures that exist only to exercise the detectors - never navigation.
    _write(tmp_path, "skills/assess/tests/fixtures/lean/CLAUDE.md", "fixture")
    _write(tmp_path, "tests/fixtures/monolithic_instructions.md", "fixture")

    r = build_doc_graph(tmp_path)
    assert r.doc_count == 2
    assert not any("fixtures" in o for o in r.orphans)
    assert r.orphan_rate == 0.0


def test_unrelated_top_level_fixtures_dir_not_excluded(tmp_path: Path) -> None:
    """Only the `tests/fixtures` *sequence* is excluded - a top-level
    `fixtures/` of real content (not preceded by `tests`) still counts."""
    _write(tmp_path, "README.md", "real doc")
    _write(tmp_path, "fixtures/data-model.md", "real architecture doc")
    r = build_doc_graph(tmp_path)
    assert r.doc_count == 2


def test_is_excluded_path_helper() -> None:
    from lib.doc_graph import is_excluded_path

    assert is_excluded_path(Path("a/tests/fixtures/x.md"))
    assert is_excluded_path(Path("tests/fixtures/x.md"))
    assert is_excluded_path(Path(".assess/log.md"))
    # `fixtures` not preceded by `tests`, and `tests` not followed by `fixtures`.
    assert not is_excluded_path(Path("src/fixtures/x.md"))
    assert not is_excluded_path(Path("tests/unit/x.md"))
    assert not is_excluded_path(Path("tests/x/fixtures/y.md"))


def test_graph_object_exposed_for_renderer(tmp_path: Path) -> None:
    """DocGraphResult.graph carries the networkx graph (the SVG renderer needs
    the full edge list, which as_dict doesn't serialise)."""
    _write(tmp_path, "a.md", "[[b]]")
    _write(tmp_path, "b.md", "x")
    r = build_doc_graph(tmp_path)
    assert r.graph is not None
    assert set(r.graph.nodes()) == {"a.md", "b.md"}
    assert "graph" not in r.as_dict()


def test_is_repo_file_rejects_symlink_escape(tmp_path: Path) -> None:
    import os
    from lib.doc_graph import is_repo_file
    repo = tmp_path / "repo"
    repo.mkdir()
    outside = tmp_path / "outside.md"
    outside.write_text("not ours", encoding="utf-8")
    (repo / "real.md").write_text("ours", encoding="utf-8")
    os.symlink(outside, repo / "link.md")  # symlink inside repo -> outside
    rr = repo.resolve()
    assert is_repo_file(repo / "real.md", rr, None) is True
    assert is_repo_file(repo / "link.md", rr, None) is False  # resolves outside repo


def test_untracked_files_excluded_in_git_repo(git_repo) -> None:
    """A contributor's untracked personal doc is not part of the repo and must
    not be scanned (the external-CLAUDE.md class of false positive)."""
    repo, commit = git_repo
    (repo / "README.md").write_text("# Home\n[[guide]]", encoding="utf-8")
    (repo / "guide.md").write_text("tracked", encoding="utf-8")
    commit("docs")
    (repo / "personal.md").write_text("my private notes", encoding="utf-8")  # untracked

    r = build_doc_graph(repo)
    nodes = set(r.graph.nodes())
    assert {"README.md", "guide.md"} <= nodes
    assert "personal.md" not in nodes


def test_radial_shells_and_classify(tmp_path: Path) -> None:
    """The headline claim — reachable = central, unreachable = banished to the
    rim — is the BFS/shell logic; lock it in deterministically (no rendering)."""
    from lib.doc_graph import classify_node, radial_shells
    _write(tmp_path, "index.md", "# Index\n[[a]]")
    _write(tmp_path, "a.md", "[[b]]")
    _write(tmp_path, "b.md", "leaf")
    _write(tmp_path, "lonely.md", "nobody links here")
    r = build_doc_graph(tmp_path)
    assert r.entry_points == ["index.md"]

    shells = radial_shells(r.graph, set(r.entry_points))
    assert shells[0] == ["index.md"]          # entry at the centre
    assert "a.md" in shells[1]                 # 1 hop out
    assert "b.md" in shells[2]                 # 2 hops out
    assert "lonely.md" in shells[-1]           # unreachable -> outer rim
    lonely_ring = next(i for i, s in enumerate(shells) if "lonely.md" in s)
    assert lonely_ring > 2                      # past every reachable shell

    entries, unreachable, orphans = set(r.entry_points), set(r.unreachable), set(r.orphans)
    assert classify_node("index.md", entries, unreachable, orphans) == "entry"
    assert classify_node("a.md", entries, unreachable, orphans) == "reachable"
    assert classify_node("lonely.md", entries, unreachable, orphans) == "orphan"


def test_broken_links_recorded_as_ghosts(tmp_path: Path) -> None:
    """Links to files that don't exist are captured (wikilink + CommonMark) so
    the renderer can draw them as ghost nodes."""
    _write(tmp_path, "a.md", "[[ghost-note]] and [also](./missing.md) and [ok](b.md)")
    _write(tmp_path, "b.md", "real")
    r = build_doc_graph(tmp_path).as_dict()
    targets = {bl["target"] for bl in r["broken_links"]}
    assert "ghost-note" in targets          # dangling wikilink
    assert "./missing.md" in targets        # broken CommonMark link
    assert r["dangling_links"] == len(r["broken_links"])
    # the valid link to b.md is not a ghost
    assert not any(bl["target"] == "b.md" for bl in r["broken_links"])


def test_directory_link_not_flagged_broken(tmp_path: Path) -> None:
    """A link to an existing folder is valid navigation, not a broken link."""
    (tmp_path / "guides").mkdir()
    (tmp_path / "guides" / "x.md").write_text("hi", encoding="utf-8")
    _write(tmp_path, "a.md", "see [folder](guides/) and [ghost](nope.md)")
    r = build_doc_graph(tmp_path).as_dict()
    targets = {bl["target"] for bl in r["broken_links"]}
    assert "guides/" not in targets   # existing directory -> not broken
    assert "nope.md" in targets       # genuinely missing -> ghost


def test_missing_xrefs_named_not_linked(tmp_path: Path) -> None:
    """A doc that names another doc's filename in prose but never links it."""
    _write(tmp_path, "overview.md", "The payments.md flow is described elsewhere.")
    _write(tmp_path, "payments.md", "payments")
    _write(tmp_path, "linked.md", "see [payments](payments.md)")  # already linked
    r = build_doc_graph(tmp_path).as_dict()
    pairs = {(x["from"], x["to"]) for x in r["missing_xrefs"]}
    assert ("overview.md", "payments.md") in pairs       # named, not linked
    assert ("linked.md", "payments.md") not in pairs     # already linked -> not missing


def test_group_broken_links_merges_same_missing_file() -> None:
    """Several links to the same missing file collapse to one ghost they share."""
    broken = [
        {"from": "README.md", "target": "CLAUDE.md", "kind": "mdlink"},
        {"from": "CONTRIBUTING.md", "target": "CLAUDE.md", "kind": "mdlink"},
    ]
    groups = group_broken_links(broken)
    assert len(groups) == 1
    assert groups[0]["target"] == "CLAUDE.md"
    assert sorted(groups[0]["sources"]) == ["CONTRIBUTING.md", "README.md"]


def test_group_broken_links_resolves_relative_targets() -> None:
    """Targets written differently but pointing at distinct paths stay separate;
    the same resolved path merges even when the link text differs."""
    broken = [
        {"from": "README.md", "target": "CLAUDE.md", "kind": "mdlink"},
        # resolves to docs/CLAUDE.md, not the root CLAUDE.md -> separate ghost
        {"from": "docs/guide.md", "target": "CLAUDE.md", "kind": "mdlink"},
        # ../CLAUDE.md from docs/ resolves back to root CLAUDE.md -> merges with README
        {"from": "docs/other.md", "target": "../CLAUDE.md", "kind": "mdlink"},
    ]
    groups = {g["target"]: sorted(g["sources"]) for g in group_broken_links(broken)}
    assert groups["CLAUDE.md"] == ["README.md", "docs/other.md"]
    assert groups["docs/CLAUDE.md"] == ["docs/guide.md"]


def test_group_broken_links_merges_root_absolute_spelling() -> None:
    """A root-absolute link (/CLAUDE.md) and a plain one (CLAUDE.md) at the same
    missing root file must merge — they only differ in spelling. Regression for
    the leading-slash key mismatch."""
    broken = [
        {"from": "README.md", "target": "CLAUDE.md", "kind": "mdlink"},
        {"from": "docs/guide.md", "target": "/CLAUDE.md", "kind": "mdlink"},
    ]
    groups = group_broken_links(broken)
    assert len(groups) == 1
    assert groups[0]["target"] == "CLAUDE.md"
    assert sorted(groups[0]["sources"]) == ["README.md", "docs/guide.md"]


def test_group_broken_links_wikilink_and_mdlink_do_not_merge() -> None:
    """Documented limit: a wikilink ([[CLAUDE]]) and a markdown link (CLAUDE.md)
    to the same missing file live in different resolution domains and stay
    separate. Pinned so the behaviour is intentional, not accidental."""
    broken = [
        {"from": "a.md", "target": "CLAUDE", "kind": "wikilink"},
        {"from": "b.md", "target": "CLAUDE.md", "kind": "mdlink"},
    ]
    keys = {g["target"] for g in group_broken_links(broken)}
    assert keys == {"CLAUDE", "CLAUDE.md"}


def test_group_broken_links_wikilinks_key_by_name() -> None:
    """Wikilinks resolve by note name globally, so they key on the bare name
    regardless of the source directory."""
    broken = [
        {"from": "a.md", "target": "ghost-note", "kind": "wikilink"},
        {"from": "deep/b.md", "target": "ghost-note", "kind": "wikilink"},
    ]
    groups = group_broken_links(broken)
    assert len(groups) == 1
    assert groups[0]["target"] == "ghost-note"
    assert sorted(groups[0]["sources"]) == ["a.md", "deep/b.md"]


def test_group_broken_links_orders_by_source_count() -> None:
    """The most-referenced missing file comes first so it renders first."""
    broken = [
        {"from": "x.md", "target": "rare.md", "kind": "mdlink"},
        {"from": "a.md", "target": "popular.md", "kind": "mdlink"},
        {"from": "b.md", "target": "popular.md", "kind": "mdlink"},
    ]
    groups = group_broken_links(broken)
    assert [g["target"] for g in groups] == ["popular.md", "rare.md"]


def test_degrades_when_networkx_unavailable(tmp_path: Path, monkeypatch) -> None:
    _write(tmp_path, "README.md", "[[a]]")
    _write(tmp_path, "a.md", "x")
    monkeypatch.setattr(doc_graph, "_NETWORKX_AVAILABLE", False)
    r = build_doc_graph(tmp_path)
    assert r.available is False
    assert "networkx" in r.reason
    # must not crash; as_dict is still serialisable
    assert r.as_dict()["available"] is False
