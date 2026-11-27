"""reStructuredText and Sphinx docs for the doc graph and doc freshness (#840).

``lib.docs.doc_graph`` and ``lib.docs.doc_staleness`` read Markdown by suffix; this
module adds reST. Discovery: every ``.rst`` file is a doc, and a ``.txt`` file is a
doc only under a Sphinx root that uses ``.txt`` sources. A Sphinx root is a
directory holding ``conf.py`` beside ``index.rst`` or ``index.txt``; it reads
``.txt`` sources when its index is ``index.txt`` or its ``conf.py`` names ``.txt``
in ``source_suffix``. ``requirements*.txt`` / ``constraints*.txt`` and any reST
or ``.txt`` file under a ``_build`` directory are never docs. The root's index is the toctree root,
so ``doc_graph`` takes it as an entry point (:func:`is_sphinx_index`).

Links (:func:`harvest_rst_links`), each a ``link`` edge:

- ``:doc:`target``` and ``:doc:`Title <target>```: a docname relative to the
  citing doc, or to the Sphinx root when it starts with ``/``; tried bare, then
  with ``.rst`` and ``.txt``. Unresolved, it is a broken link of kind ``rst``,
  unless it names another project (``python:library/os``) or opts out (``!``).
- ``:ref:`label``` and ``:ref:`Text <label>```: the doc holding ``.. _label:``,
  matched case- and space-insensitively across the doc set. An unknown label is
  not broken: it may come from intersphinx or ``autosectionlabel``.
- ``.. include:: path``: the included file when it is a doc. A path that names
  no file is a broken ``rst`` link; ``<isonum.txt>``-style standard includes are
  skipped.
- ``.. toctree::`` entries (``target`` or ``Title <target>``), resolved like
  ``:doc:``; under ``:glob:`` an entry is a pattern over the root's docnames, read
  as Sphinx reads it: ``*`` and ``?`` stay within a directory, ``**`` crosses them. ``self`` and URLs are skipped.

Literal blocks (a paragraph ending ``::``) and code directives (``code-block``,
``code``, ``sourcecode``, ``doctest``, ``testcode``, ``testoutput``) only *show*
syntax, so their indented bodies are blanked before any parse.

:data:`UNPARSED_DOC_FORMATS` names doc formats that are present in a repo but not
read for links, so the block can say so rather than read a low reachability as
the whole story. It is empty since AsciiDoc is read (#841, ``lib.docs.doc_adoc``).

Stdlib only and imports no ``lib`` module at runtime, like ``lib.docs.doc_links``;
``LinkHarvest`` is imported for annotations only. Tests:
``tests/test_doc_graph_rst.py``, ``tests/test_doc_staleness.py``.
"""
from __future__ import annotations

import os
import posixpath
import re
from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # annotation only; the caller owns the networkx import
    import networkx as nx

    from lib.docs.doc_links import LinkHarvest

RST_DISCOVERY_EXTENSIONS = frozenset({".rst", ".txt"})
SPHINX_INDEX_NAMES = frozenset({"index.rst", "index.txt"})
# Doc formats found but not parsed for links, by suffix, as the token the block
# reports.
UNPARSED_DOC_FORMATS: dict[str, str] = {}
_NON_DOC_TXT = re.compile(r"^(requirements|constraints)([-_.].*)?\.txt$", re.IGNORECASE)
_TXT_SOURCE_SUFFIX = re.compile(r"source_suffix\s*=[^=]*?['\"]\.txt['\"]")
_ROLE_RE = re.compile(r"(?<![\w`]):(?:std:)?(doc|ref):`([^`]+)`")
_TITLED_RE = re.compile(r"^(.*?)<([^<>]+)>\s*$", re.DOTALL)
_LABEL_RE = re.compile(r"^\s*\.\.\s+_([^:`][^:]*|`[^`]+`):\s*$")
_DIRECTIVE_RE = re.compile(r"^(\s*)\.\.\s+([\w:-]+)::(.*)$")
_EXTERNAL_RE = re.compile(r"^[a-z][a-z0-9+.-]*:", re.IGNORECASE)
_CODE_DIRECTIVES = frozenset({
    "code-block", "code", "sourcecode", "doctest", "testcode", "testoutput",
})


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" \t"))


def _conf_text(directory: Path) -> str | None:
    try:
        return (directory / "conf.py").read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None


def is_sphinx_index(doc: Path) -> bool:
    """True when ``doc`` is a Sphinx root's ``index.rst`` / ``index.txt``."""
    return doc.name.lower() in SPHINX_INDEX_NAMES and (doc.parent / "conf.py").is_file()


def _reads_txt(directory: Path) -> bool:
    """True when ``directory`` is a Sphinx root whose sources are ``.txt``."""
    conf = _conf_text(directory)
    if conf is None:
        return False
    if (directory / "index.txt").is_file():
        return True
    return (directory / "index.rst").is_file() and bool(_TXT_SOURCE_SUFFIX.search(conf))


def sphinx_filter(paths: Iterable[Path], repo_root: Path) -> list[Path]:
    """``paths`` minus the ``.txt`` files that are not Sphinx sources and the
    reST files under a ``_build`` directory."""
    roots: dict[Path, bool] = {}

    def built(p: Path) -> bool:
        try:
            return "_build" in p.relative_to(repo_root).parts
        except ValueError:
            return True

    def is_source(p: Path) -> bool:
        if _NON_DOC_TXT.match(p.name):
            return False
        for parent in p.parents:
            if parent not in roots:
                roots[parent] = _reads_txt(parent)
            if roots[parent]:
                return True
            if parent == repo_root:
                break
        return False

    return [p for p in paths if p.suffix.lower() not in RST_DISCOVERY_EXTENSIONS
            or (not built(p) and (p.suffix.lower() != ".txt" or is_source(p)))]


def unparsed_formats(paths: Iterable[Path]) -> list[str]:
    """The sorted format tokens of the unparsed-format files in ``paths``."""
    return sorted({UNPARSED_DOC_FORMATS[p.suffix.lower()] for p in paths
                   if p.suffix.lower() in UNPARSED_DOC_FORMATS})


def strip_literals(text: str) -> list[str]:
    """``text``'s lines with literal-block and code-directive bodies blanked,
    so line numbers still line up with the source."""
    out: list[str] = []
    skip: int | None = None
    for line in text.splitlines():
        stripped = line.strip()
        if skip is not None:
            if not stripped or _indent(line) > skip:
                out.append("")
                continue
            skip = None
        out.append(line)
        m = _DIRECTIVE_RE.match(line)
        if m:
            if m.group(2).lower() in _CODE_DIRECTIVES:
                skip = _indent(line)
        elif stripped.endswith("::") and not stripped.startswith(".."):
            skip = _indent(line)
    return out


def _target_of(body: str) -> str:
    """The target of ``Title <target>``, or the whole body."""
    m = _TITLED_RE.match(body.strip())
    return (m.group(2) if m else body).strip()


def _norm_label(label: str) -> str:
    return " ".join(label.strip("`").split()).lower()


def toctree_entries(lines: list[str]) -> list[tuple[str, bool]]:
    """Every toctree entry as ``(target, glob)``, in document order."""
    out: list[tuple[str, bool]] = []
    i = 0
    while i < len(lines):
        m = _DIRECTIVE_RE.match(lines[i])
        i += 1
        if not m or m.group(2).lower() != "toctree":
            continue
        base, glob = len(m.group(1)), False
        while i < len(lines):
            stripped = lines[i].strip()
            if stripped and _indent(lines[i]) <= base:
                break
            if stripped.startswith(":"):
                glob = glob or stripped.startswith(":glob:")
            elif stripped:
                out.append((_target_of(stripped), glob))
            i += 1
    return out


class _Resolver:
    """Docname resolution over one doc set."""

    def __init__(self, docs: list[Path], repo_root: Path) -> None:
        self.repo_root = repo_root
        self.docs = set(docs)
        self._roots: dict[Path, Path] = {}

    def source_root(self, doc: Path) -> Path:
        """The nearest ancestor holding ``conf.py``, else the repo root."""
        directory = doc.parent
        if directory not in self._roots:
            found = self.repo_root
            for parent in (directory, *directory.parents):
                if (parent / "conf.py").is_file():
                    found = parent
                    break
                if parent == self.repo_root:
                    break
            self._roots[directory] = found
        return self._roots[directory]

    def base(self, target: str, source: Path) -> Path:
        if target.startswith("/"):
            return self.source_root(source) / target.lstrip("/")
        return source.parent / target

    def docname(self, target: str, source: Path) -> Path | None:
        """The doc a ``:doc:`` / toctree target names, or None."""
        base = Path(os.path.normpath(self.base(target, source)))
        for suffix in ("", ".rst", ".txt"):
            cand = base.with_name(base.name + suffix)
            if cand in self.docs:
                return cand
        return None

    def glob(self, pattern: str, source: Path) -> list[Path]:
        """The docs under ``source``'s root whose docname matches ``pattern``."""
        root = self.source_root(source)
        rel_dir = posixpath.relpath(source.parent.as_posix(), root.as_posix())
        pat = (pattern.lstrip("/") if pattern.startswith("/")
               else posixpath.normpath(posixpath.join(rel_dir, pattern)))
        rx = glob_regex(pat)
        hits: list[Path] = []
        for d in sorted(self.docs):
            if d.suffix.lower() not in RST_DISCOVERY_EXTENSIONS or not d.is_relative_to(root):
                continue
            if rx.fullmatch(d.relative_to(root).with_suffix("").as_posix()):
                hits.append(d)
        return hits


def glob_regex(pattern: str) -> re.Pattern[str]:
    """A toctree glob as Sphinx reads it (``sphinx.util.matching``): ``**``
    crosses directories, ``*`` and ``?`` stay within one, ``[...]`` is a class."""
    out: list[str] = []
    i = 0
    while i < len(pattern):
        ch = pattern[i]
        if pattern.startswith("**", i):
            out.append(".*")
            i += 2
            continue
        if ch == "*":
            out.append("[^/]*")
        elif ch == "?":
            out.append("[^/]")
        elif ch == "[" and "]" in pattern[i + 1:]:
            end = pattern.index("]", i + 1)
            body = pattern[i + 1:end]
            out.append("[" + ("^" + body[1:] if body.startswith("!") else body) + "]")
            i = end + 1
            continue
        else:
            out.append(re.escape(ch))
        i += 1
    return re.compile("".join(out))


def _labels(rst_docs: list[Path], lines_of: dict[Path, list[str]]) -> dict[str, Path]:
    """Label -> the first doc (by path) that defines it."""
    out: dict[str, Path] = {}
    for d in sorted(rst_docs):
        for line in lines_of[d]:
            m = _LABEL_RE.match(line)
            if m:
                out.setdefault(_norm_label(m.group(1)), d)
    return out


def _include_target(line: str) -> str | None:
    m = _DIRECTIVE_RE.match(line)
    if not m or m.group(2).lower() != "include":
        return None
    target = m.group(3).strip()
    return None if not target or target.startswith("<") else target


def _doc_edges(
    d: Path, lines: list[str], res: _Resolver, labels: dict[str, Path], harvest: LinkHarvest,
) -> list[Path]:
    """The docs ``d`` links to through roles, includes and toctrees; records
    broken ``:doc:`` and include targets on ``harvest``."""
    found: list[Path] = []
    for m in _ROLE_RE.finditer("\n".join(lines)):
        target = _target_of(m.group(2))
        if m.group(1) == "ref":
            hit = labels.get(_norm_label(target))
        elif target.startswith("!") or _EXTERNAL_RE.match(target):
            continue
        else:
            hit = res.docname(target.lstrip("~"), d)
            if hit is None:
                harvest.add_broken(d, target, "rst")
        if hit is not None:
            found.append(hit)
    for include in filter(None, map(_include_target, lines)):
        path = Path(os.path.normpath(res.base(include, d)))
        if path in res.docs:
            found.append(path)
        elif not path.exists():
            harvest.add_broken(d, include, "rst")
    for target, glob in toctree_entries(lines):
        if target == "self" or _EXTERNAL_RE.match(target):
            continue
        if glob and any(ch in target for ch in "*?["):
            found.extend(res.glob(target, d))
        else:
            hit = res.docname(target, d)
            if hit is None:
                harvest.add_broken(d, target, "rst")
            else:
                found.append(hit)
    return found


def harvest_rst_links(
    docs: list[Path], texts: dict[Path, str], repo_root: Path, graph: nx.DiGraph,
    harvest: LinkHarvest,
) -> None:
    """Add every reST ``link`` edge among ``docs`` to ``graph`` (module docstring)."""
    rst_docs = [d for d in docs if d.suffix.lower() in RST_DISCOVERY_EXTENSIONS and d in texts]
    if not rst_docs:
        return
    lines_of = {d: strip_literals(texts[d]) for d in rst_docs}
    res = _Resolver(docs, repo_root)
    labels = _labels(rst_docs, lines_of)
    rel = harvest.rel
    for d in rst_docs:
        for tgt in _doc_edges(d, lines_of[d], res, labels, harvest):
            if tgt != d:
                graph.add_edge(rel(d), rel(tgt), kind="link")
