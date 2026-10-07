"""Tests for the doc graph's link pass (``lib.doc_links``), called directly
rather than through ``build_doc_graph``."""
from __future__ import annotations

from pathlib import Path

import networkx as nx

from lib.doc_links import INLINE_CODE_RE, harvest_links, strip_fenced_lines

DOC_EXTS = {".md"}
CODE_EXTS = {".py"}


def _write(root: Path, rel: str, text: str) -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p.resolve()


def _harvest(root: Path, files: dict[str, str]):
    root = root.resolve()
    paths = {rel: _write(root, rel, text) for rel, text in files.items()}
    docs = sorted(p for rel, p in paths.items() if Path(rel).suffix in DOC_EXTS)
    texts = {d: d.read_text(encoding="utf-8") for d in docs}
    graph = nx.DiGraph()
    graph.add_nodes_from(str(d.relative_to(root)) for d in docs)

    def rel(p: Path) -> str:
        return str(p.relative_to(root))

    harvest = harvest_links(
        docs, texts, root, graph, rel, doc_exts=DOC_EXTS, code_exts=CODE_EXTS,
    )
    return graph, harvest


def test_harvest_links_adds_wikilink_and_mdlink_edges(tmp_path: Path) -> None:
    graph, harvest = _harvest(tmp_path, {
        "index.md": "[[guide#intro|Guide]] and [api](api.md)",
        "guide.md": "x",
        "api.md": "see [src](app.py)",
        "app.py": "print(1)",
    })
    assert set(graph.edges()) == {("index.md", "guide.md"), ("index.md", "api.md")}
    assert all(k == "link" for _, _, k in graph.edges(data="kind"))
    assert harvest.doc_to_code == [{"doc": "api.md", "code": "app.py"}]
    assert harvest.broken == []


def test_harvest_links_records_ghosts_and_machine_links(tmp_path: Path) -> None:
    _, harvest = _harvest(tmp_path, {
        "a.md": "[[missing]] [gone](gone.md) [mail](mailto:x@y.z) [[tel:+1|call]]",
        "docs/sub/keep.md": "x",
        "b.md": "[folder](docs/sub/) [top](#heading)",
    })
    assert harvest.broken == [
        {"from": "a.md", "target": "missing", "kind": "wikilink"},
        {"from": "a.md", "target": "gone.md", "kind": "mdlink"},
    ]
    assert harvest.machine_links == {"a.md": 2}


def test_harvest_links_skips_links_shown_as_code(tmp_path: Path) -> None:
    graph, harvest = _harvest(tmp_path, {
        "a.md": "Write `[[b]]` like this.\n\n```\n[x](b.md)\n```\n",
        "b.md": "x",
    })
    assert list(graph.edges()) == []
    assert harvest.broken == []


def test_harvest_links_counts_ambiguous_wikilinks(tmp_path: Path) -> None:
    graph, harvest = _harvest(tmp_path, {
        "one/setup.md": "x",
        "two/setup.md": "x",
        "one/page.md": "[[setup]]",
    })
    assert harvest.ambiguous == 1
    assert ("one/page.md", "one/setup.md") in graph.edges()


def test_strip_fenced_lines_handles_tilde_and_nested_fences() -> None:
    text = "keep\n> ~~~\n> drop\n> ~~~\n- ```\n  drop\n  ```\nkeep too"
    assert strip_fenced_lines(text) == "keep\nkeep too"


def test_inline_code_re_matches_single_line_spans() -> None:
    assert [m.group(0) for m in INLINE_CODE_RE.finditer("a `x.md` b `y\nz`")] == ["`x.md`"]
