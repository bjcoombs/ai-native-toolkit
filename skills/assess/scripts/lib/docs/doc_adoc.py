"""AsciiDoc and Antora docs for the doc graph and doc freshness (#841).

``lib.docs.doc_graph`` discovers ``.adoc`` and ``.asciidoc`` files as docs (:data:`ADOC_EXTENSIONS`),
and ``lib.docs.doc_staleness`` takes the same doc set, so doc freshness measures them.
A root ``README.adoc`` is an entry point, and the nav files of an Antora component
are entry points too (:func:`is_antora_nav`): the files its ``antora.yml`` lists
under ``nav:``, and any ``modules/<module>/nav.adoc`` beside that ``antora.yml``.

Links (:func:`harvest_adoc_links`), each a ``link`` edge:

- ``xref:target.adoc[...]`` and ``<<target.adoc#id,text>>``: a target with no
  ``.adoc`` path is an anchor in the same document and is skipped. Outside an
  Antora component's ``modules/<module>/`` tree (a README beside ``antora.yml``
  included) the path is relative to the citing doc. Inside one it is an
  Antora page ID, ``[version@][component:][module:]path``: ``path`` is relative to
  the module's ``pages/`` directory, or to the citing page's directory when it
  starts with ``./``; the module defaults to the citing file's own. A component
  named in the ID that no ``antora.yml`` in the repository declares belongs to
  another project and is skipped.
- ``include::path[...]`` at the start of a line: relative to the including file,
  or an Antora resource ID with a family (``partial$x.adoc``,
  ``ROOT:example$y.adoc``) resolved to that module's ``partials/`` or
  ``examples/`` directory. A target holding an attribute reference
  (``{snippets}/x.adoc``) or a URL is skipped.
- ``link:path[...]`` to a doc: an edge when the path names a doc, never broken,
  because a ``link:`` may point at the published site.

An ``xref`` or ``include`` naming no file is a broken link of kind ``asciidoc``,
except inside an Antora component whose ``antora.yml`` has a ``collector:`` key: its
build generates pages a checkout does not hold, so a miss there is not counted.
Listing, literal, passthrough and comment blocks (``----``, ``....``, ``++++``,
``////``), inline passthroughs (``+xref:x.adoc[]+``, ``pass:[...]``) and ``//``
comment lines only show syntax, so they are blanked before the
``xref`` and ``link`` parse; ``include::`` is a preprocessor line, so only comments
hide it. Escaped macros (``\\xref:``) are skipped.

Stdlib only and imports no ``lib`` module at runtime, like ``lib.docs.doc_rst``;
``LinkHarvest`` is imported for annotations only. Tests:
``tests/test_doc_graph_adoc.py``, ``tests/test_doc_staleness.py``.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # annotation only; the caller owns the networkx import
    import networkx as nx

    from lib.docs.doc_links import LinkHarvest

ADOC_EXTENSIONS = frozenset({".adoc", ".asciidoc"})
BROKEN_KIND = "asciidoc"
_FAMILY_DIRS = {"page": "pages", "partial": "partials", "example": "examples"}
_DELIMITER = re.compile(r"^(-{4,}|\.{4,}|\+{4,}|/{4,})\s*$")
_COMMENT_LINE = re.compile(r"^//(?!/)")
_INLINE_PASS = re.compile(  # constrained `+x+` / `++x++` need word boundaries, as AsciiDoc's do
    r"(?<![\w+])\+\+(?=\S).*?(?<=\S)\+\+(?![\w+])"
    r"|(?<![\w+])\+(?=\S)[^+\n]*?(?<=\S)\+(?![\w+])|pass:[a-z,]*\[.*?\]")
_XREF = re.compile(r"(?<![\w\\])xref:([^\s\[\]]+)\[")
_SHORTHAND = re.compile(r"(?<!\\)<<([^<>,\s]+?\.(?:adoc|asciidoc)(?:#[^<>,]*)?)(?:,[^<>]*)?>>")
_LINK = re.compile(r"(?<![\w\\])link:([^\s\[\]]+)\[")
_INCLUDE = re.compile(r"^include::([^\[\s]+)\[")
_EXTERNAL = re.compile(r"^[a-z][a-z0-9+.-]*://", re.IGNORECASE)
_NAME = re.compile(r"^name:\s*['\"]?([^'\"\s#]+)", re.MULTILINE)
_COLLECTOR_KEY = re.compile(r"^\s*collector\s*:", re.MULTILINE)
_NAV_ITEM = re.compile(r"^\s*-\s*['\"]?([^'\"\s#]+\.adoc)")


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None


def _nav_entries(descriptor: str) -> set[str]:
    """The files listed under ``nav:`` in an ``antora.yml``."""
    out: set[str] = set()
    in_nav = False
    for line in descriptor.splitlines():
        if re.match(r"^nav:\s*$", line):
            in_nav = True
            continue
        if in_nav and line.strip() and not line[0].isspace() and not line.startswith("-"):
            in_nav = False
        m = _NAV_ITEM.match(line) if in_nav else None
        if m:
            out.add(m.group(1).removeprefix("./"))
    return out


def component_root(doc: Path, stop: Path | None = None) -> Path | None:
    """The nearest ancestor of ``doc`` holding an ``antora.yml``, or None."""
    for parent in doc.parents:
        if (parent / "antora.yml").is_file():
            return parent
        if parent == stop:
            break
    return None


def is_antora_nav(doc: Path, repo_root: Path) -> bool:
    """True when ``doc`` is a nav file of an Antora component (module docstring)."""
    root = component_root(doc, repo_root)
    if root is None:
        return False
    rel = doc.relative_to(root).as_posix()
    parts = rel.split("/")
    if len(parts) == 3 and parts[0] == "modules" and parts[2] == "nav.adoc":
        return True
    return rel in _nav_entries(_read(root / "antora.yml") or "")


def blank_verbatim(text: str) -> tuple[list[str], list[str]]:
    """``(link_lines, include_lines)``: ``text``'s lines with verbatim blocks and
    comments blanked for the link parse, and with only comments blanked for the
    include parse, so line numbers still line up with the source."""
    links: list[str] = []
    includes: list[str] = []
    open_delim: str | None = None
    for line in text.splitlines():
        stripped = line.rstrip()
        if open_delim is not None:
            if stripped == open_delim:
                open_delim = None
                links.append("")
                includes.append("")
                continue
            links.append("")
            includes.append("" if open_delim.startswith("/") else line)
            continue
        if _DELIMITER.match(stripped):
            open_delim = stripped
            links.append("")
            includes.append("")
            continue
        keep = "" if _COMMENT_LINE.match(line) else line
        links.append(_INLINE_PASS.sub("", keep))
        includes.append(keep)
    return links, includes


class _Component:
    """One Antora component: its root, name, and whether a collector feeds it."""

    def __init__(self, root: Path) -> None:
        descriptor = _read(root / "antora.yml") or ""
        name = _NAME.search(descriptor)
        self.root = root
        self.name = name.group(1) if name else ""
        self.collector = bool(_COLLECTOR_KEY.search(descriptor))

    def module_of(self, doc: Path) -> str:
        parts = doc.relative_to(self.root).parts
        return parts[1] if len(parts) > 2 and parts[0] == "modules" else "ROOT"


class _Resolver:
    """Target resolution over one doc set."""

    def __init__(self, docs: list[Path], repo_root: Path) -> None:
        self.repo_root = repo_root
        self.docs = set(docs)
        self._components: dict[Path, _Component] = {}
        self._by_name: dict[str, _Component] | None = None

    def component(self, doc: Path) -> _Component | None:
        """The component ``doc`` is an Antora file of: Antora reads only
        ``<component>/modules/<module>/``, so a README beside ``antora.yml`` is plain."""
        root = component_root(doc, self.repo_root)
        if root is None:
            return None
        parts = doc.relative_to(root).parts
        if len(parts) < 3 or parts[0] != "modules":
            return None
        if root not in self._components:
            self._components[root] = _Component(root)
        return self._components[root]

    def named(self, name: str) -> _Component | None:
        if self._by_name is None:
            self._by_name = {}
            for d in sorted(self.docs):
                comp = self.component(d)
                if comp is not None:
                    self._by_name.setdefault(comp.name, comp)
        return self._by_name.get(name)

    def antora(self, rid: str, source: Path, comp: _Component, family: str) -> Path | None:
        """The file an Antora resource ID names, or None when it is in another project."""
        rid = rid.split("@", 1)[-1]
        parts = rid.split(":")
        if len(parts) > 3:
            return None
        if len(parts) == 3:
            other = self.named(parts[0])
            if other is None:
                return None
            comp, parts = other, parts[1:]
            module = parts[0] or "ROOT"  # `component::page.adoc` names ROOT
        else:
            module = parts[0] if len(parts) == 2 else comp.module_of(source)
        path = parts[-1]
        if "$" in path:
            fam, path = path.split("$", 1)
            if fam not in _FAMILY_DIRS:
                return None
            family = fam
        if path.startswith("./"):
            return Path(os.path.normpath(source.parent / path))
        return Path(os.path.normpath(comp.root / "modules" / module / _FAMILY_DIRS[family] / path))

    def resolve(self, target: str, source: Path, family: str) -> Path | None:
        comp = self.component(source)
        if comp is not None and (":" in target or "$" in target or family == "page"):
            return self.antora(target, source, comp, family)
        if ":" in target:
            return None
        return Path(os.path.normpath(source.parent / target))

    def broken_counts(self, source: Path) -> bool:
        comp = self.component(source)
        return comp is None or not comp.collector


def _is_doc_target(path: str) -> bool:
    return path.lower().endswith(tuple(ADOC_EXTENSIONS))


def _skip(target: str) -> bool:
    return not target or "{" in target or bool(_EXTERNAL.match(target))


def _doc_edges(
    d: Path, text: str, res: _Resolver, harvest: LinkHarvest,
) -> list[Path]:
    """The docs ``d`` links to; records broken xrefs and includes on ``harvest``."""
    link_lines, include_lines = blank_verbatim(text)
    body = "\n".join(link_lines)
    found: list[Path] = []
    counts = res.broken_counts(d)

    def visit(target: str, family: str, strict: bool) -> None:
        if _skip(target):
            return
        hit = res.resolve(target, d, family)
        if hit is None:
            return
        if hit in res.docs:
            found.append(hit)
        elif strict and counts and not hit.exists():
            harvest.add_broken(d, target, BROKEN_KIND)

    refs = [m.group(1) for m in _XREF.finditer(body)] + [m.group(1) for m in _SHORTHAND.finditer(body)]
    for ref in refs:
        path = ref.split("#", 1)[0]
        if _is_doc_target(path):
            visit(path, "page", True)
    for m in _LINK.finditer(body):
        path = m.group(1).split("#", 1)[0]
        if _is_doc_target(path) and ":" not in path:
            visit(path, "", False)
    for line in include_lines:
        inc = _INCLUDE.match(line)
        if inc:
            visit(inc.group(1), "partial", True)
    return found


def harvest_adoc_links(
    docs: list[Path], texts: dict[Path, str], repo_root: Path, graph: nx.DiGraph,
    harvest: LinkHarvest,
) -> None:
    """Add every AsciiDoc ``link`` edge among ``docs`` to ``graph`` (module docstring)."""
    adoc_docs = [d for d in docs if d.suffix.lower() in ADOC_EXTENSIONS and d in texts]
    if not adoc_docs:
        return
    res = _Resolver(docs, repo_root)
    rel = harvest.rel
    for d in adoc_docs:
        for tgt in _doc_edges(d, texts[d], res, harvest):
            if tgt != d:
                graph.add_edge(rel(d), rel(tgt), kind="link")


def without_adoc(texts: dict[Path, str]) -> dict[Path, str]:
    """``texts`` minus AsciiDoc docs, for the Markdown link pass: ``[[id]]`` is an
    AsciiDoc anchor, not a wikilink."""
    return {d: t for d, t in texts.items() if d.suffix.lower() not in ADOC_EXTENSIONS}
