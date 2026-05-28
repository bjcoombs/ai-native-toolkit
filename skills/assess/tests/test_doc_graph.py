"""Tests for the doc link-graph (Layer 0 navigability)."""
from __future__ import annotations

from pathlib import Path

import pytest

import lib.doc_graph as doc_graph
from lib.doc_graph import build_doc_graph


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


def test_excludes_assess_and_vendor_dirs(tmp_path: Path) -> None:
    _write(tmp_path, "README.md", "real doc")
    _write(tmp_path, ".assess/log.md", "our own output")
    _write(tmp_path, "node_modules/pkg/readme.md", "vendored")
    r = build_doc_graph(tmp_path)
    assert r.doc_count == 1


def test_degrades_when_networkx_unavailable(tmp_path: Path, monkeypatch) -> None:
    _write(tmp_path, "README.md", "[[a]]")
    _write(tmp_path, "a.md", "x")
    monkeypatch.setattr(doc_graph, "_NETWORKX_AVAILABLE", False)
    r = build_doc_graph(tmp_path)
    assert r.available is False
    assert "networkx" in r.reason
    # must not crash; as_dict is still serialisable
    assert r.as_dict()["available"] is False
