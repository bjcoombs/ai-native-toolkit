"""Tests for the doc graph's render-side helpers (radial shells, node
classes, broken-link grouping)."""
from __future__ import annotations

from pathlib import Path

from lib.doc_graph import build_doc_graph
from lib.doc_graph_layout import classify_node, group_broken_links, radial_shells


def _write(root: Path, rel: str, text: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def test_radial_shells_and_classify(tmp_path: Path) -> None:
    """The headline claim — reachable = central, unreachable = banished to the
    rim — is the BFS/shell logic; lock it in deterministically (no rendering)."""
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
