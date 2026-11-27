"""AsciiDoc and Antora docs in the doc graph (#841).

Kept beside ``test_doc_graph.py``, which sits at its file-size ceiling.
"""
from __future__ import annotations

from pathlib import Path

from lib.docs.doc_adoc import blank_verbatim, is_antora_nav
from lib.docs.doc_graph import build_doc_graph, discover_doc_files


def _write(root: Path, rel: str, text: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _rels(root: Path, paths: list[Path]) -> list[str]:
    return sorted(p.resolve().relative_to(root.resolve()).as_posix() for p in paths)


ROOT = "docs/antora/modules/ROOT"


def _antora(root: Path, descriptor: str = "name: demo\nversion: ~\nnav:\n- modules/ROOT/nav.adoc\n") -> None:
    _write(root, "docs/antora/antora.yml", descriptor)
    _write(root, f"{ROOT}/nav.adoc", (
        "* xref:index.adoc[Home]\n** xref:guide/install.adoc[Install]\n"
        "* xref:reference:config.adoc#props[Config]\n"
    ))
    _write(root, f"{ROOT}/pages/index.adoc", "= Home\n\nWelcome.\n")
    _write(root, f"{ROOT}/pages/guide/install.adoc", "= Install\n\nSee xref:./usage.adoc[usage].\n")
    _write(root, f"{ROOT}/pages/guide/usage.adoc", "= Usage\n")
    _write(root, "docs/antora/modules/reference/pages/config.adoc", "= Config\n")


def test_antora_nav_reaches_pages_linked_by_xref(tmp_path: Path) -> None:
    _antora(tmp_path)
    r = build_doc_graph(tmp_path)
    nav = f"{ROOT}/nav.adoc"
    assert nav in r.entry_points
    assert {(nav, f"{ROOT}/pages/index.adoc"), (nav, f"{ROOT}/pages/guide/install.adoc"),
            (nav, "docs/antora/modules/reference/pages/config.adoc"),
            (f"{ROOT}/pages/guide/install.adoc", f"{ROOT}/pages/guide/usage.adoc"),
            } <= set(r.graph.edges())
    assert r.unreachable == []
    assert r.reachability_pct == 1.0
    assert r.broken_links == []
    assert r.as_dict()["unparsed_doc_formats"] == []


def test_include_is_an_edge_and_a_missing_one_is_broken(tmp_path: Path) -> None:
    _write(tmp_path, "README.adoc", (
        "= Demo\n\ninclude::docs/setup.adoc[]\ninclude::docs/gone.adoc[]\n"
        "include::{snippets}/x.adoc[]\ninclude::src/App.java[lines=1..3]\n"
    ))
    _write(tmp_path, "docs/setup.adoc", "== Setup\n")
    _write(tmp_path, "src/App.java", "class App {}\n")
    r = build_doc_graph(tmp_path)
    assert ("README.adoc", "docs/setup.adoc") in r.graph.edges()
    assert r.graph.edges["README.adoc", "docs/setup.adoc"]["kind"] == "link"
    assert "README.adoc" in r.entry_points
    assert r.broken_links == [{"from": "README.adoc", "target": "docs/gone.adoc", "kind": "asciidoc"}]


def test_antora_partial_include_resolves_to_the_module_partials(tmp_path: Path) -> None:
    _antora(tmp_path)
    _write(tmp_path, f"{ROOT}/pages/index.adoc", "= Home\n\ninclude::partial$intro.adoc[]\n")
    _write(tmp_path, f"{ROOT}/partials/intro.adoc", "Intro text.\n")
    r = build_doc_graph(tmp_path)
    assert (f"{ROOT}/pages/index.adoc", f"{ROOT}/partials/intro.adoc") in r.graph.edges()
    assert r.broken_links == []


def test_plain_xref_and_link_resolve_relative_to_the_doc(tmp_path: Path) -> None:
    _write(tmp_path, "README.adoc", (
        "= Demo\n\nlink:CONTRIBUTING.adoc[Contributing] and xref:docs/a.adoc#top[A]\n"
        "and <<docs/b.adoc#,B>> and <<local-anchor>> and xref:section-id[here]\n"
        "and xref:docs/missing.adoc[gone] and link:site/page.adoc[site]\n"
    ))
    _write(tmp_path, "CONTRIBUTING.adoc", "= Contributing\n")
    _write(tmp_path, "docs/a.adoc", "= A\n\n[[anchor]]\nText <<anchor>>.\n")
    _write(tmp_path, "docs/b.adoc", "= B\n")
    r = build_doc_graph(tmp_path)
    assert {("README.adoc", "CONTRIBUTING.adoc"), ("README.adoc", "docs/a.adoc"),
            ("README.adoc", "docs/b.adoc")} <= set(r.graph.edges())
    # `[[anchor]]` is an AsciiDoc anchor, never a wikilink; `link:` is never broken.
    assert r.broken_links == [
        {"from": "README.adoc", "target": "docs/missing.adoc", "kind": "asciidoc"}]


def test_verbatim_blocks_and_comments_hide_sample_links(tmp_path: Path) -> None:
    _write(tmp_path, "README.adoc", (
        "= Demo\n\n----\nxref:docs/sample.adoc[]\ninclude::docs/shown.adoc[]\n----\n"
        "////\ninclude::docs/commented.adoc[]\n////\n// xref:docs/c2.adoc[]\n"
        "\\xref:docs/escaped.adoc[]\n"
    ))
    _write(tmp_path, "docs/shown.adoc", "shown\n")
    r = build_doc_graph(tmp_path)
    # An include inside a listing block is still processed; the rest are samples.
    assert ("README.adoc", "docs/shown.adoc") in r.graph.edges()
    assert r.broken_links == []


def test_blank_verbatim_keeps_line_numbers() -> None:
    text = "a\n....\nxref:x.adoc[]\n....\n// c\nb\n"
    links, includes = blank_verbatim(text)
    assert len(links) == len(includes) == len(text.splitlines())
    assert "xref" not in "\n".join(links)
    assert "xref:x.adoc[]" in includes and "// c" not in includes


def test_collector_component_does_not_count_generated_targets_broken(tmp_path: Path) -> None:
    _antora(tmp_path, "name: demo\nnav:\n- modules/ROOT/nav.adoc\next:\n  collector:\n  - run: gradle\n")
    _write(tmp_path, f"{ROOT}/pages/index.adoc", "= Home\n\nxref:generated/props.adoc[]\n")
    r = build_doc_graph(tmp_path)
    assert r.broken_links == []


def test_cross_component_xref_to_another_project_is_skipped(tmp_path: Path) -> None:
    _antora(tmp_path)
    _write(tmp_path, f"{ROOT}/pages/index.adoc", (
        "= Home\n\nxref:6.2@framework:core:beans.adoc[]\nxref:demo:reference:config.adoc[]\n"
        "xref:demo::guide/usage.adoc[]\n"))
    r = build_doc_graph(tmp_path)
    assert (f"{ROOT}/pages/index.adoc", "docs/antora/modules/reference/pages/config.adoc") \
        in r.graph.edges()
    assert (f"{ROOT}/pages/index.adoc", f"{ROOT}/pages/guide/usage.adoc") in r.graph.edges()
    assert r.broken_links == []


def test_nav_files_are_antora_entry_points_only_in_a_component(tmp_path: Path) -> None:
    _antora(tmp_path)
    _write(tmp_path, "docs/antora/modules/extra/nav.adoc", "* xref:x.adoc[]\n")
    _write(tmp_path, "notes/nav.adoc", "not antora\n")
    root = tmp_path.resolve()
    assert is_antora_nav(root / ROOT / "nav.adoc", root)
    assert is_antora_nav(root / "docs/antora/modules/extra/nav.adoc", root)
    assert not is_antora_nav(root / "notes/nav.adoc", root)
    assert not is_antora_nav(root / ROOT / "pages/index.adoc", root)


def test_adoc_files_are_discovered_as_docs(tmp_path: Path) -> None:
    _write(tmp_path, "README.adoc", "= Demo\n")
    _write(tmp_path, "guide.asciidoc", "= Guide\n")
    _write(tmp_path, "build/out.adoc", "= built\n")
    assert _rels(tmp_path, discover_doc_files(tmp_path)) == ["README.adoc", "guide.asciidoc"]
