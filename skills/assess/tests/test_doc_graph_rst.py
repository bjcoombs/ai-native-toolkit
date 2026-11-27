"""reStructuredText and Sphinx docs in the doc graph (#840).

Kept beside ``test_doc_graph.py``, which sits at its file-size ceiling.
"""
from __future__ import annotations

from pathlib import Path

from lib.docs.doc_graph import build_doc_graph, discover_doc_files
from lib.docs.doc_rst import glob_regex, strip_literals, toctree_entries


def _write(root: Path, rel: str, text: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _rels(root: Path, paths: list[Path]) -> list[str]:
    return sorted(p.resolve().relative_to(root.resolve()).as_posix() for p in paths)


def _sphinx(root: Path) -> None:
    _write(root, "docs/conf.py", "project = 'demo'\n")
    _write(root, "docs/index.rst", (
        "Demo\n====\n\n.. toctree::\n   :maxdepth: 2\n   :caption: Contents\n\n"
        "   install\n   Usage guide <usage>\n"
    ))
    _write(root, "docs/install.rst", "Install\n=======\n\npip install demo\n")
    _write(root, "docs/usage.rst", "Usage\n=====\n\nRun it.\n")


def test_sphinx_toctree_reaches_every_page(tmp_path: Path) -> None:
    _sphinx(tmp_path)
    r = build_doc_graph(tmp_path)
    assert r.doc_count == 3
    assert "docs/index.rst" in r.entry_points
    assert r.reachability_pct == 1.0
    assert r.unreachable == []
    assert {("docs/index.rst", "docs/install.rst"), ("docs/index.rst", "docs/usage.rst")} \
        <= set(r.graph.edges())
    assert r.broken_links == []


def test_sphinx_txt_sources_are_docs_only_under_a_sphinx_root(tmp_path: Path) -> None:
    _write(tmp_path, "docs/conf.py", "source_suffix = '.txt'\n")
    _write(tmp_path, "docs/index.txt", "Home\n====\n\n.. toctree::\n\n   topics/intro\n")
    _write(tmp_path, "docs/topics/intro.txt", "Intro\n=====\n\nHello.\n")
    _write(tmp_path, "docs/requirements.txt", "sphinx\n")
    _write(tmp_path, "notes/todo.txt", "not a doc\n")
    _write(tmp_path, "LICENSE.txt", "licence\n")
    assert _rels(tmp_path, discover_doc_files(tmp_path)) == [
        "docs/index.txt", "docs/topics/intro.txt",
    ]
    r = build_doc_graph(tmp_path)
    assert r.doc_count == 2
    assert r.reachability_pct == 1.0


def test_txt_beside_an_rst_index_is_a_doc_only_when_conf_names_txt(tmp_path: Path) -> None:
    _write(tmp_path, "a/conf.py", "project = 'a'\n")
    _write(tmp_path, "a/index.rst", "A\n=\n")
    _write(tmp_path, "a/notes.txt", "scratch\n")
    _write(tmp_path, "b/conf.py", "source_suffix = {'.rst': 'restructuredtext',\n"
                                  "                 '.txt': 'restructuredtext'}\n")
    _write(tmp_path, "b/index.rst", "B\n=\n")
    _write(tmp_path, "b/page.txt", "Page\n")
    assert _rels(tmp_path, discover_doc_files(tmp_path)) == [
        "a/index.rst", "b/index.rst", "b/page.txt",
    ]


def test_ref_doc_and_include_each_produce_an_edge(tmp_path: Path) -> None:
    _write(tmp_path, "docs/conf.py", "")
    _write(tmp_path, "docs/index.rst", "Home\n====\n\nNothing linked here.\n")
    _write(tmp_path, "docs/guide.rst", (
        "Guide\n=====\n\nSee :ref:`the API <API-Section>` and :doc:`/howto/setup`.\n\n"
        ".. include:: shared/footer.rst\n"
    ))
    _write(tmp_path, "docs/api.rst", ".. _api-section:\n\nAPI\n===\n")
    _write(tmp_path, "docs/howto/setup.rst", "Setup\n=====\n\nBack to :doc:`../guide`.\n")
    _write(tmp_path, "docs/shared/footer.rst", "Footer.\n")
    r = build_doc_graph(tmp_path)
    edges = set(r.graph.edges())
    assert ("docs/guide.rst", "docs/api.rst") in edges          # :ref: by label
    assert ("docs/guide.rst", "docs/howto/setup.rst") in edges  # :doc: from the root
    assert ("docs/guide.rst", "docs/shared/footer.rst") in edges  # .. include::
    assert ("docs/howto/setup.rst", "docs/guide.rst") in edges  # :doc: relative
    assert r.broken_links == []


def test_unresolved_rst_targets_are_broken_unless_external(tmp_path: Path) -> None:
    _write(tmp_path, "docs/conf.py", "")
    _write(tmp_path, "docs/index.rst", (
        "Home\n====\n\n:doc:`missing` :doc:`python:library/os` :doc:`!skipped`\n"
        ":ref:`genindex`\n\n.. include:: gone.rst\n.. include:: <isonum.txt>\n\n"
        ".. toctree::\n\n   self\n   https://example.com\n   absent\n"
    ))
    r = build_doc_graph(tmp_path)
    assert sorted(b["target"] for b in r.broken_links) == ["absent", "gone.rst", "missing"]
    assert {b["kind"] for b in r.broken_links} == {"rst"}


def test_toctree_glob_and_literal_blocks(tmp_path: Path) -> None:
    _write(tmp_path, "docs/conf.py", "")
    _write(tmp_path, "docs/index.rst", (
        "Home\n====\n\nExample::\n\n    :doc:`sample`\n\n"
        ".. code-block:: rst\n\n   .. toctree::\n\n      sample\n\n"
        ".. toctree::\n   :glob:\n\n   topics/*\n"
    ))
    _write(tmp_path, "docs/sample.rst", "Sample\n")
    _write(tmp_path, "docs/topics/a.rst", "A\n")
    _write(tmp_path, "docs/topics/b.rst", "B\n")
    _write(tmp_path, "docs/topics/drafts/d.rst", "D\n")
    r = build_doc_graph(tmp_path)
    edges = set(r.graph.edges())
    assert {("docs/index.rst", "docs/topics/a.rst"), ("docs/index.rst", "docs/topics/b.rst")} <= edges
    assert ("docs/index.rst", "docs/topics/drafts/d.rst") not in edges  # `*` stays in one dir
    assert ("docs/index.rst", "docs/sample.rst") not in edges
    assert r.broken_links == []


def test_strip_literals_keeps_line_count_and_toctree_entries() -> None:
    text = "Intro::\n\n   hidden\n\n.. toctree::\n   :glob:\n\n   Title <a/b>\n   c*\nafter\n"
    lines = strip_literals(text)
    assert len(lines) == len(text.splitlines())
    assert "hidden" not in "\n".join(lines)
    assert toctree_entries(lines) == [("a/b", True), ("c*", True)]


def test_markdown_only_repo_reports_no_unparsed_format(tmp_path: Path) -> None:
    _write(tmp_path, "README.md", "hi\n")
    assert build_doc_graph(tmp_path).as_dict()["unparsed_doc_formats"] == []


def test_glob_regex_reads_patterns_as_sphinx_does() -> None:
    assert glob_regex("topics/*").fullmatch("topics/a")
    assert not glob_regex("topics/*").fullmatch("topics/drafts/d")
    assert glob_regex("topics/**").fullmatch("topics/drafts/d")
    assert glob_regex("ref/?").fullmatch("ref/x")
    assert glob_regex("ref/[!a]*").fullmatch("ref/bc")
    assert not glob_regex("ref/[!a]*").fullmatch("ref/abc")
    assert glob_regex("a.b").fullmatch("a.b") and not glob_regex("a.b").fullmatch("axb")


def test_rst_under_build_is_not_a_doc(tmp_path: Path) -> None:
    _write(tmp_path, "docs/conf.py", "")
    _write(tmp_path, "docs/index.rst", "Home\n")
    _write(tmp_path, "docs/_build/html/_sources/index.rst", "Home\n")
    _write(tmp_path, "docs/_build/notes.md", "kept: markdown is not filtered here\n")
    assert _rels(tmp_path, discover_doc_files(tmp_path)) == [
        "docs/_build/notes.md", "docs/index.rst",
    ]
