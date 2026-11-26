"""Doc-graph node keys are repo-relative POSIX paths on every platform (#1286).

On Windows, `ownership_parser._resolve_ref` hands back `Path` objects whose
`str()` uses backslashes, while the graph keys its docs by forward-slash paths,
so a repo-root reference to a nested README raised
`KeyError: 'docs\\\\sub\\\\README.md'`. These tests live apart from
test_doc_graph.py, which sits at its file-size ceiling.
"""
from __future__ import annotations

from pathlib import Path, PurePath, PureWindowsPath

import lib.docs.ownership_parser as ownership_parser
from lib.docs.doc_graph import build_doc_graph


def _write(root: Path, rel: str, text: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _nested_readme_repo(root: Path) -> None:
    # docs/guide.md names the nested README by its repo-root path, so the
    # doc-relative guess (docs/docs/sub/README.md) misses and the reference
    # goes through `_resolve_ref`.
    _write(root, "README.md", "[guide](docs/guide.md)")
    _write(root, "docs/guide.md", "Read `docs/sub/README.md` next.")
    _write(root, "docs/sub/README.md", "# Sub")


def _edges(r) -> list[list]:
    return sorted([u, v, d.get("kind")] for u, v, d in r.graph.edges(data=True))


def test_nested_readme_reference_resolves(tmp_path: Path) -> None:
    """A doc that cites a nested README by path gets a reference edge to it."""
    _nested_readme_repo(tmp_path)
    r = build_doc_graph(tmp_path)
    assert ["docs/guide.md", "docs/sub/README.md", "reference"] in _edges(r)
    assert "docs/sub/README.md" not in r.unreachable


def test_backslash_resolved_path_enters_the_graph_as_posix(
    tmp_path: Path, monkeypatch,
) -> None:
    """The resolver as Windows runs it: each `Path` it returns stringifies with
    backslashes. The reference still lands on the forward-slash doc key."""
    real = ownership_parser._resolve_ref

    def windows_resolve(ref: str, repo_root: Path, rel_paths: set[str]) -> set[PurePath]:
        return {PureWindowsPath(p) for p in real(ref, repo_root, rel_paths)}

    monkeypatch.setattr(ownership_parser, "_resolve_ref", windows_resolve)
    _nested_readme_repo(tmp_path)
    r = build_doc_graph(tmp_path)
    assert ["docs/guide.md", "docs/sub/README.md", "reference"] in _edges(r)
