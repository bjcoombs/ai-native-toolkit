"""Vault-native navigation edges: Obsidian Bases (`.base`) and Dataview queries.

In an Obsidian vault the primary navigation surface is often *not* static
``[[wikilinks]]`` / ``[text](path)`` links. Notes are surfaced dynamically by a
``.base`` view (Obsidian Bases) or a ```` ```dataview ```` query block: a hub
declares a *query* - "every note in folder ``_jira``", "every note tagged
``#project``" - and Obsidian materialises the edges at view time. A doc graph
that only reads static links scores such a vault as massively orphaned even
though every note is reachable in the app (issue #176).

This module recognises those query hubs as **edge sources**, statically and
deterministically - no running Obsidian, no live plugin, nothing a CI run
couldn't reproduce from the committed files alone. We parse the query for the
predicates we can resolve from the repo on disk:

  - **folder** - ``inFolder("_jira")`` (Bases) / ``FROM "_jira"`` (Dataview):
    the hub connects to every note under that folder.
  - **tag** - ``FROM #project`` / ``tags.contains("project")`` /
    ``hasTag("project")``: the hub connects to every note carrying that tag in
    its YAML frontmatter.
  - **frontmatter field** - ``status == "open"`` (Bases) / ``WHERE status =
    "open"`` (Dataview): the hub connects to notes whose frontmatter matches.

Selection is **union across predicate types** on purpose: for a navigability
read, over-linking a few extra notes is far less harmful than leaving a
genuinely-reachable note scored as an orphan, and a union can never be emptied
by an unresolvable predicate (e.g. a ``file.ext == "md"`` guard, which we drop
anyway). The cost is that a hub combining ``inFolder(...)`` *and* a frontmatter
filter links the union rather than the intersection Obsidian would show - a
documented, deliberate over-approximation in the safe direction.

The module is pure: it parses text and resolves predicates against a
caller-supplied doc list and frontmatter accessor. Filesystem discovery and
exclude handling stay in ``lib.doc_graph`` (which owns the repo walk), so this
module imports no sibling that imports it back.
"""
from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path


# A ```` ```dataview ```` fenced block. ``dataviewjs`` (arbitrary JS, not
# statically resolvable) is excluded: ``\bdataview\b`` won't match ``dataviewjs``
# because there is no word boundary between ``dataview`` and ``js``.
_DATAVIEW_BLOCK_RE = re.compile(r"```+[ \t]*dataview\b(.*?)```+",
                                re.DOTALL | re.IGNORECASE)

# Folder predicates: Bases ``inFolder("X")`` / ``file.inFolder('X')``.
_INFOLDER_RE = re.compile(r"inFolder\(\s*[\"']([^\"']+)[\"']\s*\)", re.IGNORECASE)
# Dataview source clause: ``FROM "folder" or #tag and "other"``. ``FROM`` can
# trail the query type on the same line (``LIST FROM "x"``), so it is matched
# inline (not anchored to line start); the clause is the rest of that line.
_FROM_RE = re.compile(r"(?i)\bFROM\b([^\n]*)")
_QUOTED_RE = re.compile(r"[\"']([^\"']+)[\"']")
_HASHTAG_RE = re.compile(r"#([A-Za-z0-9][\w/-]*)")
# Tag predicates: ``hasTag("project")`` / ``tags.contains("project")``.
_HASTAG_FN_RE = re.compile(
    r"(?:hasTag\(|tags\.contains\()\s*[\"']#?([\w/-]+)[\"']", re.IGNORECASE)
# Frontmatter-field equality: ``status == "open"`` / ``note.status = "open"``.
_FIELD_EQ_RE = re.compile(
    r"(?P<ns>\b\w+\.)?(?P<key>[A-Za-z_][\w-]*)\s*={1,2}\s*[\"'](?P<val>[^\"']+)[\"']")

# Keys that are query keywords or file-metadata accessors, never a user's
# frontmatter field - dropped so a ``file.ext == "md"`` guard can't manufacture
# a phantom field predicate that selects nothing.
_FIELD_KEY_SKIP = {
    "infolder", "hastag", "contains", "ext", "from", "where", "and", "or",
    "name", "tags", "file", "note",
}

# Leading-frontmatter block: ``---\n ... \n---`` at the very top of a note.
_FRONTMATTER_RE = re.compile(r"\A﻿?---[ \t]*\n(.*?)\n---[ \t]*(?:\n|$)",
                             re.DOTALL)
_FM_LIST_ITEM_RE = re.compile(r"\s*-\s+(.*)$")
_FM_KV_RE = re.compile(r"([A-Za-z_][\w-]*)\s*:\s*(.*)$")
_FM_TAG_TOKEN_RE = re.compile(r"[#\w/-]+")

# Frontmatter tags are stashed under this synthetic key so a scalar field named
# "tags" can't collide with the parsed tag set.
TAGS_KEY = "__tags__"


@dataclass
class VaultQuery:
    """A resolved dynamic-navigation query: the predicates we can match on disk."""
    folders: set[str] = field(default_factory=set)
    tags: set[str] = field(default_factory=set)
    fields: list[tuple[str, str]] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not (self.folders or self.tags or self.fields)


def parse_frontmatter(text: str) -> dict[str, object]:
    """Parse a note's leading YAML frontmatter into a flat dict.

    Lightweight and dependency-free (no yaml import): scalar ``key: value``
    lines become string entries; ``tags`` - inline (``tags: [a, b]`` /
    ``tags: a, b``) or block (``tags:\\n  - a``) - is collected into a lowercased
    set under ``TAGS_KEY``. Anything it can't parse is skipped, never raised:
    frontmatter parsing must not break the graph build.
    """
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}
    data: dict[str, object] = {}
    tags: set[str] = set()
    current_list_key: str | None = None
    for raw in m.group(1).splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        item = _FM_LIST_ITEM_RE.match(line)
        if item and current_list_key == "tags":
            tags.add(item.group(1).strip().strip("\"'").lstrip("#").lower())
            continue
        if item:
            continue
        kv = _FM_KV_RE.match(line)
        if not kv:
            continue
        key = kv.group(1).lower()
        val = kv.group(2).strip()
        current_list_key = key if val == "" else None
        if val == "":
            continue
        if key == "tags":
            for tok in _FM_TAG_TOKEN_RE.findall(val):
                tags.add(tok.lstrip("#").lower())
        else:
            data[key] = val.strip("\"'")
    if tags:
        data[TAGS_KEY] = tags
    return data


def _parse_query_text(text: str) -> VaultQuery:
    """Extract folder / tag / frontmatter-field predicates from query text."""
    folders: set[str] = set()
    tags: set[str] = set()
    fields: list[tuple[str, str]] = []
    for m in _INFOLDER_RE.finditer(text):
        folders.add(m.group(1))
    for m in _FROM_RE.finditer(text):
        clause = m.group(1)
        for q in _QUOTED_RE.finditer(clause):
            folders.add(q.group(1))
        for t in _HASHTAG_RE.finditer(clause):
            tags.add(t.group(1).lower())
    for m in _HASTAG_FN_RE.finditer(text):
        tags.add(m.group(1).lower())
    for m in _FIELD_EQ_RE.finditer(text):
        ns = (m.group("ns") or "").lower()
        if ns.startswith("file."):  # file metadata, not a frontmatter field
            continue
        key = m.group("key").lower()
        if key in _FIELD_KEY_SKIP:
            continue
        fields.append((key, m.group("val")))
    return VaultQuery(folders=folders, tags=tags, fields=fields)


def parse_base_queries(text: str) -> list[VaultQuery]:
    """Parse a ``.base`` file into its navigation query.

    A ``.base`` may declare several views, each with its own filter; we fold the
    whole file into one query (folders/tags unioned) because, for navigability,
    the base surfaces every note any of its views selects.
    """
    q = _parse_query_text(text)
    return [q] if not q.is_empty() else []


def parse_dataview_queries(text: str) -> list[VaultQuery]:
    """Parse every ```` ```dataview ```` block in a note into its query."""
    out: list[VaultQuery] = []
    for m in _DATAVIEW_BLOCK_RE.finditer(text):
        q = _parse_query_text(m.group(1))
        if not q.is_empty():
            out.append(q)
    return out


def _under_folder(rel: Path, folder: str) -> bool:
    """True if `rel` lives under `folder` (a repo-relative folder path).

    An empty / root folder (``""`` or ``"/"``, e.g. a Dataview ``FROM "/"``)
    selects the whole vault - a legitimate "all notes" navigation surface.
    """
    norm = folder.strip().strip("/").replace("\\", "/")
    if norm == "":
        return True
    fparts = tuple(p for p in norm.split("/") if p)
    return rel.parts[:len(fparts)] == fparts


def select_notes(
    query: VaultQuery,
    doc_rels: Iterable[tuple[Path, Path]],
    frontmatter_of: Callable[[Path], dict[str, object]],
) -> set[Path]:
    """Notes a query selects, as a set of absolute doc paths.

    `doc_rels` is ``(absolute_path, repo_relative_path)`` per candidate note;
    `frontmatter_of` lazily yields a note's parsed frontmatter. Selection is the
    **union** of the folder, tag and field predicates (see the module docstring
    for why union, not intersection).
    """
    docs = list(doc_rels)
    selected: set[Path] = set()
    if query.folders:
        for d, r in docs:
            if any(_under_folder(r, f) for f in query.folders):
                selected.add(d)
    if query.tags:
        for d, _r in docs:
            doc_tags = frontmatter_of(d).get(TAGS_KEY)
            if isinstance(doc_tags, set) and doc_tags & query.tags:
                selected.add(d)
    if query.fields:
        for d, _r in docs:
            fm = frontmatter_of(d)
            for key, val in query.fields:
                fv = fm.get(key)
                if isinstance(fv, str) and fv.lower() == val.lower():
                    selected.add(d)
                    break
    return selected
