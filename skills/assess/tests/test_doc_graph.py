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


def test_vault_detected_at_repo_root(tmp_path: Path) -> None:
    """`.obsidian/` at the scan target -> the repo is the vault root."""
    (tmp_path / ".obsidian").mkdir()
    _write(tmp_path, "note.md", "content")
    r = build_doc_graph(tmp_path)
    assert r.vault_detected is True


def test_vault_detected_when_nested_below_repo_root(tmp_path: Path) -> None:
    """A vault kept as a subdirectory of a git repo (`repo/notes/.obsidian/`)
    puts `.obsidian/` below the scan target. The flag must still read true -
    the false negative this guards against silently disabled every downstream
    vault accommodation (#179)."""
    (tmp_path / "notes" / ".obsidian").mkdir(parents=True)
    _write(tmp_path, "notes/note.md", "content")
    r = build_doc_graph(tmp_path)
    assert r.vault_detected is True


def test_vault_not_detected_on_plain_repo(tmp_path: Path) -> None:
    """A repo with no `.obsidian/` anywhere reports false."""
    _write(tmp_path, "README.md", "no vault here")
    r = build_doc_graph(tmp_path)
    assert r.vault_detected is False


def test_vault_not_detected_for_obsidian_under_excluded_dir(tmp_path: Path) -> None:
    """A `.obsidian/` vendored under a pruned tree (e.g. `node_modules/`) is a
    build/dependency artifact, not this repo's vault - it must not trip the
    flag."""
    (tmp_path / "node_modules" / "pkg" / ".obsidian").mkdir(parents=True)
    _write(tmp_path, "README.md", "real repo, not a vault")
    r = build_doc_graph(tmp_path)
    assert r.vault_detected is False


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


# ---- vault-native navigation (issue #176) ---------------------------------

def test_base_hub_links_folder_notes_no_longer_orphaned(tmp_path: Path) -> None:
    # A `.base` viewing `_jira` is the only navigation surface: no static links
    # anywhere. Before #176 every note scored as an orphan / unreachable.
    _write(tmp_path, "tasks.base",
           'filters:\n  and:\n    - file.inFolder("_jira")\n    - file.ext == "md"\n'
           'views:\n  - type: table\n    name: All\n')
    for i in range(5):
        _write(tmp_path, f"_jira/ABC-{i}.md", f"# Ticket {i}\nstatus: open\n")

    r = build_doc_graph(tmp_path)
    # The .base hub is a node + entry point; the 5 notes are its descendants.
    assert "tasks.base" in r.entry_points
    assert r.edge_count == 5
    assert r.orphans == []           # every note has the hub as an inbound link
    assert r.orphan_rate == 0.0
    assert r.reachability_pct == 1.0  # all reachable from the hub entry


def test_base_that_selects_nothing_is_not_added(tmp_path: Path) -> None:
    # A `.base` whose query matches no note must not appear as an orphan hub node.
    _write(tmp_path, "empty.base", '- file.inFolder("does-not-exist")\n')
    _write(tmp_path, "README.md", "# Home\n")
    r = build_doc_graph(tmp_path)
    assert "empty.base" not in r.entry_points
    assert r.doc_count == 1  # only README.md; the empty base is not a node


def test_dataview_hub_links_notes_from_its_note(tmp_path: Path) -> None:
    # A `dataview` block inside a hub note (itself reachable from the README)
    # surfaces the `_archive` folder; those notes become reachable, not orphans.
    _write(tmp_path, "README.md", "Start at the [hub](hub.md)")
    _write(tmp_path, "hub.md",
           "# Hub\n\n```dataview\nLIST\nFROM \"_archive\"\n```\n")
    for i in range(3):
        _write(tmp_path, f"_archive/note-{i}.md", f"old note {i}")

    r = build_doc_graph(tmp_path)
    # hub.md -> each archive note (3) plus README -> hub (1)
    assert r.edge_count == 4
    assert r.orphans == []
    assert r.reachability_pct == 1.0


def test_dataview_tag_hub_uses_frontmatter(tmp_path: Path) -> None:
    _write(tmp_path, "README.md", "see [hub](hub.md)")
    _write(tmp_path, "hub.md", "```dataview\nLIST FROM #project\n```")
    _write(tmp_path, "p1.md", "---\ntags: [project]\n---\nbody")
    _write(tmp_path, "p2.md", "---\ntags: [other]\n---\nbody")

    r = build_doc_graph(tmp_path)
    # hub -> p1 (tagged project) only; p2 is not tagged so stays an orphan.
    assert "p1.md" not in r.orphans
    assert "p2.md" in r.orphans


# ---- non-navigational URI scheme exclusions (issue #227) -------------------

def test_tel_mdlink_not_counted_broken(tmp_path: Path) -> None:
    """[text](tel:+1-555-1234) is a phone-dialer link. It is not a broken
    navigation edge -- the file `tel:+1-555-1234` does not exist, and that
    is expected. The broken-link counter must not count it."""
    _write(tmp_path, "contact.md", "Call us at [phone](tel:+1-555-1234)")
    r = build_doc_graph(tmp_path)
    assert r.dangling_links == 0
    targets = {bl["target"] for bl in r.broken_links}
    assert not any("tel:" in t for t in targets)


def test_mailto_mdlink_not_counted_broken(tmp_path: Path) -> None:
    """[text](mailto:hello@example.com) is an email link, not a broken file
    reference. The broken-link counter must not count it."""
    _write(tmp_path, "contact.md", "Email us at [email](mailto:hello@example.com)")
    r = build_doc_graph(tmp_path)
    assert r.dangling_links == 0
    targets = {bl["target"] for bl in r.broken_links}
    assert not any("mailto:" in t for t in targets)


def test_other_non_http_scheme_mdlink_not_counted_broken(tmp_path: Path) -> None:
    """Non-navigational URI schemes beyond tel:/mailto: (sms:, callto:, etc.)
    are not file references and must not contribute broken links."""
    _write(
        tmp_path,
        "contact.md",
        "Text us at [sms](sms:+1-555-1234) or via [Skype](skype:username)",
    )
    r = build_doc_graph(tmp_path)
    assert r.dangling_links == 0
    targets = {bl["target"] for bl in r.broken_links}
    assert not any("sms:" in t or "skype:" in t for t in targets)


def test_tel_wikilink_not_counted_broken(tmp_path: Path) -> None:
    """[[tel:+1-555-1234]] is a non-navigational URI in wikilink form. It
    must not be counted as a broken wikilink to a missing note."""
    _write(tmp_path, "contact.md", "Dial [[tel:+1-555-1234]] for support")
    r = build_doc_graph(tmp_path)
    assert r.dangling_links == 0
    targets = {bl["target"] for bl in r.broken_links}
    assert not any("tel:" in t for t in targets)


def test_mailto_wikilink_not_counted_broken(tmp_path: Path) -> None:
    """[[mailto:user@example.com]] in wikilink form must not count as a broken
    note reference."""
    _write(tmp_path, "contact.md", "Write to [[mailto:user@example.com]]")
    r = build_doc_graph(tmp_path)
    assert r.dangling_links == 0
    targets = {bl["target"] for bl in r.broken_links}
    assert not any("mailto:" in t for t in targets)


def test_uri_scheme_inside_code_fence_not_counted(tmp_path: Path) -> None:
    """A tel: or mailto: link shown as an example inside a fenced code block
    (e.g. in a FORMAT spec or tutorial) must not count -- it is documentation
    syntax, not a navigation edge."""
    _write(
        tmp_path,
        "guide.md",
        "Contact links look like:\n\n```markdown\n"
        "[phone](tel:+1-555-1234)\n"
        "[email](mailto:hello@example.com)\n"
        "```\n",
    )
    r = build_doc_graph(tmp_path)
    assert r.dangling_links == 0
    targets = {bl["target"] for bl in r.broken_links}
    assert not any("tel:" in t or "mailto:" in t for t in targets)


def test_real_file_links_still_flagged_after_scheme_exclusions(tmp_path: Path) -> None:
    """URI-scheme exclusions must not accidentally suppress genuine broken
    relative-path links. A link to a missing file must still be flagged."""
    _write(tmp_path, "a.md", "[gone](missing-file.md) and [phone](tel:555-1234)")
    r = build_doc_graph(tmp_path)
    targets = {bl["target"] for bl in r.broken_links}
    assert "missing-file.md" in targets
    assert not any("tel:" in t for t in targets)


# --- Raw-source-tree exclusion (issue #225) -------------------------------

def _curated_wiki(root: Path) -> None:
    """A small, well-linked curated wiki: index hub + three linked notes."""
    _write(root, "index.md", "# Index\n[[setup]] [[guide]] [[api]]")
    _write(root, "setup.md", "see [[guide]]")
    _write(root, "guide.md", "back to [[index]]")
    _write(root, "api.md", "[[setup]]")


def _raw_export(root: Path, subdir: str, n: int) -> None:
    """A raw-source dump: ``n`` link-isolated docs each carrying a
    machine-extracted (mailto:/tel:) link, the SAR-export fingerprint."""
    for i in range(n):
        _write(
            root, f"{subdir}/msg-{i:03d}.md",
            f"From: sender{i}@example.com\n"
            f"Contact [email](mailto:user{i}@example.com) or [call](tel:+1-555-{i:04d}).\n"
            "Body text extracted from the original message.\n",
        )


def test_raw_source_tree_excluded_from_metrics(tmp_path: Path) -> None:
    _curated_wiki(tmp_path)
    _raw_export(tmp_path, "sar-export", 14)
    r = build_doc_graph(tmp_path)

    # The raw subtree is named with its file count.
    assert r.excluded_raw_trees == [{"path": "sar-export", "file_count": 14}]
    assert r.raw_source_doc_count == 14

    # Curated metrics exclude the raw docs: none of the raw files appear as
    # orphans, and the curated layer is small + well-connected.
    assert not any(o.startswith("sar-export/") for o in r.orphans)
    assert r.curated_doc_count == 4
    assert r.doc_count == 4  # headline doc_count is the curated layer
    # Orphan rate over the curated wiki is low, not the ~78% the raw dump
    # would have produced if counted.
    assert r.orphan_rate <= 0.25
    # The raw layer's own orphan rate is reported separately and is high.
    assert r.raw_source_orphan_rate >= 0.9


def test_no_raw_tree_is_unaffected(tmp_path: Path) -> None:
    _curated_wiki(tmp_path)
    _write(tmp_path, "lonely.md", "I link to nobody")
    r = build_doc_graph(tmp_path)
    assert r.excluded_raw_trees == []
    assert r.raw_source_doc_count == 0
    assert r.raw_source_broken_links == 0
    assert r.curated_doc_count == r.doc_count == 5
    # lonely.md is still a genuine orphan - not swept up by raw detection.
    assert "lonely.md" in r.orphans


def test_raw_broken_links_excluded_from_headline(tmp_path: Path) -> None:
    _curated_wiki(tmp_path)
    # Raw docs that also carry a broken relative link: the broken link must be
    # attributed to the raw layer, not the curated headline count.
    for i in range(14):
        _write(
            tmp_path, f"dump/msg-{i:03d}.md",
            f"[email](mailto:user{i}@example.com) and [missing](./ghost-{i}.md)\n",
        )
    r = build_doc_graph(tmp_path)
    assert r.excluded_raw_trees == [{"path": "dump", "file_count": 14}]
    # No curated broken link points at a raw ghost target.
    assert not any(bl["from"].startswith("dump/") for bl in r.broken_links)
    assert r.raw_source_broken_links >= 14


def test_isolated_curated_folder_not_excluded(tmp_path: Path) -> None:
    # A folder of hand-written standalone notes (link-isolated but NO machine
    # fingerprint) must not be mistaken for a raw dump.
    _curated_wiki(tmp_path)
    for i in range(14):
        _write(tmp_path, f"notes/note-{i:03d}.md", "A standalone hand-written note.\n")
    r = build_doc_graph(tmp_path)
    assert r.excluded_raw_trees == []
    assert any(o.startswith("notes/") for o in r.orphans)


def _notes_and_wiki(root: Path) -> None:
    """50 plan notes under one backlog index, beside a nine-page wiki the
    README links seven of."""
    for i in range(1, 51):
        _write(root, f"notes/plan_{i:02d}.md", f"# plan {i:02d}\n")
    _write(root, "notes/backlog.md", "".join(
        f"- [plan {i:02d}](plan_{i:02d}.md)\n" for i in range(1, 51)
    ))
    pages = "architecture deploy testing security glossary onboarding releases attic scratch".split()
    for w in pages:
        _write(root, f"wiki/{w}.md", f"# {w}\n")
    _write(root, "README.md", "# Home\n[backlog](notes/backlog.md)\n" + "".join(
        f"[{w}](wiki/{w}.md)\n" for w in pages[:7]
    ))


def test_working_notes_tree_excluded_from_headline(tmp_path: Path) -> None:
    _notes_and_wiki(tmp_path)
    _write(tmp_path, "notes/plan_03.md", "[ghost](./missing.md)\n")
    d = build_doc_graph(tmp_path).as_dict()
    assert d["excluded_working_notes_trees"] == [{"path": "notes", "file_count": 51}]
    assert d["working_notes_doc_count"] == 51
    assert (d["doc_count"], d["orphan_rate"], d["reachability_pct"]) == (10, 0.2, 0.8)
    assert not any(h["path"].startswith("notes/") for h in d["hubs"])
    # The notes layer's own figures are reported beside the headline.
    assert d["working_notes_orphan_rate"] == 0.0
    assert d["working_notes_broken_links"] == 1
    assert d["dangling_links"] == 0
    assert d["excluded_raw_trees"] == []


def test_base_hub_beside_notes_is_not_a_notes_member(tmp_path: Path) -> None:
    # A vault-wide .base stored in notes/ selects the wiki pages. It is not a
    # doc, so it neither joins the tree's count nor leaves the headline graph,
    # and the wiki pages it surfaces keep their inbound edge.
    _notes_and_wiki(tmp_path)
    _write(tmp_path, "notes/pages.base",
           'filters:\n  and:\n    - file.inFolder("wiki")\n    - file.ext == "md"\n'
           'views:\n  - type: table\n    name: All\n')
    d = build_doc_graph(tmp_path).as_dict()
    assert d["excluded_working_notes_trees"] == [{"path": "notes", "file_count": 51}]
    assert d["working_notes_doc_count"] == 51
    assert not any(o.startswith("wiki/") for o in d["orphans"])


def test_no_working_notes_tree_keys_present_and_empty(tmp_path: Path) -> None:
    _curated_wiki(tmp_path)
    d = build_doc_graph(tmp_path).as_dict()
    assert d["excluded_working_notes_trees"] == []
    assert d["working_notes_doc_count"] == 0
    assert d["working_notes_orphan_rate"] == 0.0
    assert d["working_notes_broken_links"] == 0


def test_raw_tree_is_not_also_a_working_notes_tree(tmp_path: Path) -> None:
    _curated_wiki(tmp_path)
    _raw_export(tmp_path, "sar-export", 30)
    r = build_doc_graph(tmp_path)
    assert r.excluded_raw_trees == [{"path": "sar-export", "file_count": 30}]
    assert r.excluded_working_notes_trees == []


# --- Reference edges (backticked doc paths) --------------------------------


def _edges(r) -> list[list]:
    return sorted([u, v, d.get("kind")] for u, v, d in r.graph.edges(data=True))


def test_reference_edge_from_backticked_existing_path(tmp_path: Path) -> None:
    """A backticked token that resolves to an existing doc is a reference edge;
    one that resolves to nothing adds no edge and no node. Markdown links keep
    kind link."""
    _write(
        tmp_path, "CLAUDE.md",
        "# Entry\nSee `docs/arch.md` and `docs/missing.md` and `not-a-path`, "
        "then [guide](guide.md).\n",
    )
    _write(tmp_path, "docs/arch.md", "# Arch")
    _write(tmp_path, "guide.md", "# Guide")
    r = build_doc_graph(tmp_path)
    assert _edges(r) == [
        ["CLAUDE.md", "docs/arch.md", "reference"],
        ["CLAUDE.md", "guide.md", "link"],
    ]
    assert "docs/missing.md" not in r.graph


def test_reference_edge_resolves_relative_to_the_citing_doc(tmp_path: Path) -> None:
    _write(tmp_path, "README.md", "[docs](docs/index.md)")
    _write(tmp_path, "docs/index.md", "Read `setup.md` next.")
    _write(tmp_path, "docs/setup.md", "# Setup")
    r = build_doc_graph(tmp_path)
    assert ["docs/index.md", "docs/setup.md", "reference"] in _edges(r)


def test_reference_inside_fence_adds_no_edge(tmp_path: Path) -> None:
    _write(tmp_path, "README.md", "```\n`guide.md`\n```\n")
    _write(tmp_path, "guide.md", "# Guide")
    r = build_doc_graph(tmp_path)
    assert _edges(r) == []


def test_link_kind_wins_when_a_doc_both_links_and_cites(tmp_path: Path) -> None:
    _write(tmp_path, "README.md", "Read `guide.md`, or [the guide](guide.md).")
    _write(tmp_path, "guide.md", "# Guide")
    r = build_doc_graph(tmp_path)
    assert _edges(r) == [["README.md", "guide.md", "link"]]


def test_cited_claude_file_becomes_a_reachable_node(tmp_path: Path) -> None:
    """A `.claude/` doc that a reference names is a node and reachable; an
    uncited one stays excluded. The headline figures count reference edges;
    the link-only figures sit beside them."""
    _write(tmp_path, "CLAUDE.md", "# Entry\nOpen `.claude/skills/x/SKILL.md` first.\n")
    _write(tmp_path, ".claude/skills/x/SKILL.md", "# x")
    _write(tmp_path, ".claude/skills/y/SKILL.md", "# y")
    _write(tmp_path, "docs/lonely.md", "# lonely")
    r = build_doc_graph(tmp_path)
    d = r.as_dict()
    assert sorted(r.graph.nodes()) == [
        ".claude/skills/x/SKILL.md", "CLAUDE.md", "docs/lonely.md",
    ]
    assert d["unreachable"] == ["docs/lonely.md"]
    assert d["doc_count"] == 3
    assert d["orphan_rate"] == 0.333
    assert d["reachability_pct"] == 0.667
    assert d["link_only_reachability_pct"] < d["reachability_pct"]
    assert d["link_only_orphan_rate"] > d["orphan_rate"]


def test_cited_claude_doc_is_parsed_for_its_own_edges(tmp_path: Path) -> None:
    _write(tmp_path, "CLAUDE.md", "Open `.claude/skills/x/SKILL.md`.")
    _write(tmp_path, ".claude/skills/x/SKILL.md", "See [ref](../../../docs/ref.md).")
    _write(tmp_path, "docs/ref.md", "# Ref")
    r = build_doc_graph(tmp_path)
    assert [".claude/skills/x/SKILL.md", "docs/ref.md", "link"] in _edges(r)
    assert r.unreachable == []


def test_reference_edge_clears_missing_xref(tmp_path: Path) -> None:
    _write(tmp_path, "CLAUDE.md", "# Entry\nRead `guide.md` before editing.\n")
    _write(tmp_path, "guide.md", "# Guide")
    r = build_doc_graph(tmp_path)
    assert _edges(r) == [["CLAUDE.md", "guide.md", "reference"]]
    assert r.as_dict()["missing_xrefs"] == []


def test_link_only_figures_equal_headline_without_references(tmp_path: Path) -> None:
    _write(tmp_path, "README.md", "[a](a.md)")
    _write(tmp_path, "a.md", "# A")
    _write(tmp_path, "b.md", "# B")
    d = build_doc_graph(tmp_path).as_dict()
    assert d["link_only_orphan_rate"] == d["orphan_rate"]
    assert d["link_only_reachability_pct"] == d["reachability_pct"]


def test_reference_inside_tilde_or_long_fence_adds_no_edge(tmp_path: Path) -> None:
    """CommonMark fences: a tilde fence, and a four-backtick fence holding a
    shorter backtick run, both hide their content from the reference pass."""
    _write(
        tmp_path, "README.md",
        "~~~\n`a.md`\n~~~\n\n````\n```\n`b.md`\n```\n````\n",
    )
    _write(tmp_path, "a.md", "# A")
    _write(tmp_path, "b.md", "# B")
    r = build_doc_graph(tmp_path)
    assert _edges(r) == []


def test_bare_basename_resolves_across_the_tree_when_unique(tmp_path: Path) -> None:
    """A bare basename with no doc-relative match falls back to the one doc in
    the tree with that name."""
    _write(tmp_path, "CLAUDE.md", "# Entry\nSee `setup.md`.\n")
    _write(tmp_path, "docs/guides/setup.md", "# Setup")
    r = build_doc_graph(tmp_path)
    assert _edges(r) == [["CLAUDE.md", "docs/guides/setup.md", "reference"]]


def test_ambiguous_bare_basename_adds_no_edge(tmp_path: Path) -> None:
    """Two docs share the cited basename: the citation names neither, so the
    basename fallback adds nothing rather than spraying edges."""
    _write(tmp_path, "README.md", "Read `SKILL.md`.")
    _write(tmp_path, "skills/a/SKILL.md", "# a")
    _write(tmp_path, "skills/b/SKILL.md", "# b")
    r = build_doc_graph(tmp_path)
    assert _edges(r) == []


def test_cited_excluded_doc_guards(tmp_path: Path) -> None:
    """A cited `.claude/` doc joins the graph only when every other exclusion
    lets it: no parent-dir escape, no user exclude, tracked, inside scope."""
    _write(tmp_path, ".claude/skills/x/SKILL.md", "# x")
    _write(tmp_path, "docs/a.md", "# a")
    target = (tmp_path / ".claude/skills/x/SKILL.md").resolve()

    def cite(rel_path, tracked=None, scope=None, dirs=None, pats=None):
        return doc_graph._cited_excluded_doc(
            rel_path, tmp_path, tracked, scope, dirs or set(), pats or [],
        )

    assert cite(".claude/skills/x/SKILL.md") == target
    assert cite(".claude/../.claude/skills/x/SKILL.md") is None
    assert cite(".claude/skills/x/SKILL.md", dirs={"x"}) is None
    assert cite(".claude/skills/x/SKILL.md", pats=["SKILL.md"]) is None
    assert cite(".claude/skills/x/SKILL.md", tracked=frozenset()) is None
    assert cite(".claude/skills/x/SKILL.md", scope=tmp_path / "docs") is None
    assert cite("docs/a.md") is None  # not under .claude: the walk owns it


def test_link_reaches_cited_claude_doc_from_a_doc_read_earlier(tmp_path: Path) -> None:
    """References settle before the link pass, so a doc walked before the
    citing doc still links to (and wikilinks to) the cited `.claude/` doc."""
    _write(tmp_path, "AAA.md", "[x](.claude/skills/x/SKILL.md) and [[notes]]")
    _write(tmp_path, "CLAUDE.md", "Open `.claude/skills/x/SKILL.md` and `.claude/notes.md`.")
    _write(tmp_path, ".claude/skills/x/SKILL.md", "# x")
    _write(tmp_path, ".claude/notes.md", "# notes")
    r = build_doc_graph(tmp_path)
    edges = _edges(r)
    assert ["AAA.md", ".claude/skills/x/SKILL.md", "link"] in edges
    assert ["AAA.md", ".claude/notes.md", "link"] in edges
    assert r.as_dict()["dangling_links"] == 0


def test_tilde_fenced_link_sample_is_not_a_broken_link(tmp_path: Path) -> None:
    """The link harvest uses the same CommonMark fence parser as references: a
    wikilink sample in a tilde fence is neither an edge nor a ghost."""
    _write(tmp_path, "README.md", "~~~\n[[nowhere]] and [x](gone.md)\n~~~\n")
    r = build_doc_graph(tmp_path)
    assert r.as_dict()["dangling_links"] == 0
    assert _edges(r) == []


def test_reference_inside_indented_fence_adds_no_edge(tmp_path: Path) -> None:
    """A fence nested under a list item sits four or more spaces in; its
    content is still a sample, not a citation."""
    _write(tmp_path, "README.md", "- step\n\n      ```\n      `docs/arch.md`\n      ```\n")
    _write(tmp_path, "docs/arch.md", "# Arch")
    r = build_doc_graph(tmp_path)
    assert _edges(r) == []


def test_root_level_mdx_citation_is_a_reference_edge(tmp_path: Path) -> None:
    """Every doc extension the graph walks is citable, with or without a slash."""
    _write(tmp_path, "README.md", "See `guide.mdx` and `docs/intro.markdown`.")
    _write(tmp_path, "guide.mdx", "# Guide")
    _write(tmp_path, "docs/intro.markdown", "# Intro")
    r = build_doc_graph(tmp_path)
    assert _edges(r) == [
        ["README.md", "docs/intro.markdown", "reference"],
        ["README.md", "guide.mdx", "reference"],
    ]


def test_reference_inside_container_prefixed_fence_adds_no_edge(tmp_path: Path) -> None:
    """A fence behind a blockquote or list-item marker hides its body, and its
    indented closer does not reopen a fence that swallows the rest of the doc."""
    _write(
        tmp_path, "README.md",
        "> ```\n> `a.md` [[nowhere]]\n> ```\n\n"
        "- ~~~\n  `b.md`\n  ~~~\n\n"
        "Then [c](c.md).\n",
    )
    for name in ("a.md", "b.md", "c.md"):
        _write(tmp_path, name, "# x")
    r = build_doc_graph(tmp_path)
    assert _edges(r) == [["README.md", "c.md", "link"]]
    assert r.as_dict()["dangling_links"] == 0


def test_root_level_exact_name_beats_a_same_named_doc_elsewhere(tmp_path: Path) -> None:
    """A bare name that is a root-level doc resolves there, even when a doc of
    the same name sits deeper in the tree."""
    _write(tmp_path, "docs/x/index.md", "See `CHANGELOG.md`.")
    _write(tmp_path, "CHANGELOG.md", "# root")
    _write(tmp_path, "pkg/CHANGELOG.md", "# pkg")
    r = build_doc_graph(tmp_path)
    assert ["docs/x/index.md", "CHANGELOG.md", "reference"] in _edges(r)
    assert not any(e[1] == "pkg/CHANGELOG.md" for e in _edges(r))


def test_link_only_figures_share_the_headline_entry_points(tmp_path: Path, monkeypatch) -> None:
    """The link-only pass is handed the headline's entry points instead of
    re-picking them from link-only PageRank, so the two figures differ only in
    their edge set. Without it, a repo with no conventional entry doc could
    measure the two from different roots."""
    calls: list = []
    real = doc_graph._derive_signals

    def spy(**kw):
        out = real(**kw)
        calls.append((kw.get("entries"), out.entry_points))
        return out

    monkeypatch.setattr(doc_graph, "_derive_signals", spy)
    _write(tmp_path, "hub-a.md", "`n1.md` `n2.md`")
    _write(tmp_path, "hub-b.md", "[1](m1.md)")
    for name in ("n1.md", "n2.md", "m1.md"):
        _write(tmp_path, name, "# x")
    build_doc_graph(tmp_path)
    (headline_in, headline_entries), (link_only_in, link_only_entries) = calls
    assert headline_in is None
    assert link_only_in == headline_entries == link_only_entries


# --- directory_breakdown (issue #365) ----------------------------------------


def _two_doc_dirs(root: Path) -> None:
    _write(root, "README.md", "# Home\n[a](docs/a.md) [g1](guides/g1.md)\n")
    _write(root, "docs/a.md", "# a\n")
    _write(root, "docs/b.md", "# b\n[gone](missing.md)\n")
    for n in (1, 2, 3):
        _write(root, f"guides/g{n}.md", f"# g{n}\n")


def test_directory_breakdown_counts_per_top_level_directory(tmp_path: Path) -> None:
    _two_doc_dirs(tmp_path)
    d = build_doc_graph(tmp_path).as_dict()
    rows = {r["path"]: r for r in d["directory_breakdown"]}
    assert rows["docs"] == {"path": "docs", "doc_count": 2,
                            "unreachable_count": 1, "broken_link_count": 1}
    assert rows["guides"] == {"path": "guides", "doc_count": 3,
                              "unreachable_count": 2, "broken_link_count": 0}
    # Root-level docs group under ".".
    assert rows["."] == {"path": ".", "doc_count": 1,
                         "unreachable_count": 0, "broken_link_count": 0}
    assert d["directory_count"] == 3


def test_directory_breakdown_reconciles_with_headline(tmp_path: Path) -> None:
    # A working-notes tree leaves the headline, so it leaves the breakdown too:
    # the rows sum to the headline doc_count, unreachable list and dangling_links.
    _notes_and_wiki(tmp_path)
    _write(tmp_path, "notes/plan_03.md", "[ghost](./missing.md)\n")
    _write(tmp_path, "wiki/scratch.md", "[ghost](./nowhere.md)\n")
    d = build_doc_graph(tmp_path).as_dict()
    rows = d["directory_breakdown"]
    assert "notes" not in {r["path"] for r in rows}
    assert sum(r["doc_count"] for r in rows) == d["doc_count"]
    assert sum(r["unreachable_count"] for r in rows) == len(d["unreachable"])
    assert sum(r["broken_link_count"] for r in rows) == d["dangling_links"] == 1


def test_directory_breakdown_is_capped_gap_first(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(doc_graph, "MAX_DIRECTORY_BREAKDOWN", 2)
    _write(tmp_path, "README.md", "# Home\n[a](a/x.md) [b](b/x.md)\n")
    _write(tmp_path, "a/x.md", "# a\n")
    _write(tmp_path, "b/x.md", "# b\n")
    _write(tmp_path, "c/x.md", "# c orphan\n")
    d = build_doc_graph(tmp_path).as_dict()
    assert d["directory_count"] == 4
    # The directory holding the unreachable doc outranks the healthy ones.
    assert [r["path"] for r in d["directory_breakdown"]] == ["c", "."]


def test_directory_breakdown_empty_repo(tmp_path: Path) -> None:
    d = build_doc_graph(tmp_path).as_dict()
    assert d["directory_breakdown"] == []
    assert d["directory_count"] == 0


def _rows(d: dict) -> dict[str, tuple[str | None, str | None]]:
    return {r["path"]: (r["link_parent"], r["link_entry"]) for r in d["link_parents"]}


def test_link_parent_bfs_records_chain_and_entry(tmp_path: Path) -> None:
    # README -> a -> b, so each reached doc names the doc that first reached it
    # and the entry its walk started from.
    _write(tmp_path, "README.md", "Root. See [a](a.md).\n")
    _write(tmp_path, "a.md", "A doc. See [b](b.md).\n")
    _write(tmp_path, "b.md", "B leaf.\n")

    d = build_doc_graph(tmp_path).as_dict()
    assert d["entry_points"] == ["README.md"]
    # One record per node, sorted by path, so the consumer needs no second lookup.
    assert [r["path"] for r in d["link_parents"]] == ["README.md", "a.md", "b.md"]
    assert len(d["link_parents"]) == d["doc_count"]
    assert {k for r in d["link_parents"] for k in r} == {"path", "link_parent", "link_entry"}
    rows = _rows(d)
    assert rows["README.md"] == (None, "README.md")  # an entry: null parent, itself
    assert rows["a.md"] == ("README.md", "README.md")
    assert rows["b.md"] == ("a.md", "README.md")


def test_link_parent_bfs_no_link_path_is_both_null(tmp_path: Path) -> None:
    # c.md is only *referenced* (a backticked doc path, a reference edge), and
    # orphan.md is named by nothing: neither has a link path, so both fields are
    # null and the page can say "no link path" rather than guess.
    _write(tmp_path, "README.md", "Root. See [a](a.md). Also `c.md` is described here.\n")
    _write(tmp_path, "a.md", "A doc.\n")
    _write(tmp_path, "c.md", "C referenced only.\n")
    _write(tmp_path, "orphan.md", "Nobody links me.\n")

    d = build_doc_graph(tmp_path).as_dict()
    rows = _rows(d)
    # The headline counts the reference edge, so c.md is neither orphan nor
    # unreachable - only the link-path question separates it.
    assert "c.md" not in d["orphans"] and "c.md" not in d["unreachable"]
    assert rows["c.md"] == (None, None)
    assert rows["orphan.md"] == (None, None)
    assert rows["a.md"] == ("README.md", "README.md")
    # Same link_graph, same entries: the docs with a link path are exactly the
    # link-only reachable set, so the two figures cannot drift apart.
    with_path = sum(1 for r in d["link_parents"] if r["link_entry"] is not None)
    assert with_path == round(d["link_only_reachability_pct"] * d["doc_count"]) == 2


def test_link_parent_bfs_seed_exhaustion_beats_shorter_path(tmp_path: Path) -> None:
    # Four ordering rules at once. Written out of byte order on purpose: p10
    # before p1, and t.md before the doc that reaches it.
    _write(tmp_path, "AGENTS.md", "Agents entry. See [x](x.md), [m](m.md) and [readme](README.md).\n")
    _write(tmp_path, "p10.md", "P10. See [y](y.md).\n")
    _write(tmp_path, "p1.md", "P1. See [y](y.md).\n")
    _write(tmp_path, "README.md", "Readme entry. See [x](x.md), [p10](p10.md), [p1](p1.md) and [t](t.md).\n")
    _write(tmp_path, "t.md", "T leaf.\n")
    _write(tmp_path, "m.md", "M hop. See [t](t.md).\n")
    _write(tmp_path, "x.md", "X leaf.\n")
    _write(tmp_path, "y.md", "Y leaf.\n")

    d = build_doc_graph(tmp_path).as_dict()
    assert d["entry_points"] == ["AGENTS.md", "README.md"]
    assert len(d["link_parents"]) == d["doc_count"] == 8
    rows = _rows(d)
    # Entries are recorded before any walk starts, so AGENTS.md linking README.md
    # cannot overwrite README.md's own entry record.
    assert rows["AGENTS.md"] == (None, "AGENTS.md")
    assert rows["README.md"] == (None, "README.md")
    # The earlier entry claims a doc both reach at the same distance.
    assert rows["x.md"] == ("AGENTS.md", "AGENTS.md")
    # Each seed's walk runs to exhaustion first, so t.md goes to AGENTS.md at two
    # hops over README.md at one. A single multi-source walk would say README.md.
    assert rows["m.md"] == ("AGENTS.md", "AGENTS.md")
    assert rows["t.md"] == ("m.md", "AGENTS.md")
    # A sorted frontier at equal depth: p1.md wins over p10.md by byte order,
    # though p10.md was written first.
    assert rows["p1.md"] == ("README.md", "README.md")
    assert rows["p10.md"] == ("README.md", "README.md")
    assert rows["y.md"] == ("p1.md", "README.md")


def test_link_parent_bfs_is_empty_when_networkx_missing(tmp_path: Path, monkeypatch) -> None:
    # A consumer must meet the key on every path, never absent on one of them.
    _write(tmp_path, "README.md", "Root. See [a](a.md).\n")
    _write(tmp_path, "a.md", "A doc.\n")
    monkeypatch.setattr(doc_graph, "_NETWORKX_AVAILABLE", False)

    d = build_doc_graph(tmp_path).as_dict()
    assert d["available"] is False
    assert d["link_parents"] == []
