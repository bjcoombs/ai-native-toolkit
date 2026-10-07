"""Link syntax and link resolution for the doc graph.

The link half of ``lib.doc_graph``: parse both link forms an LLM wiki uses --
``[[wikilinks]]`` (Obsidian / Karpathy-pattern) and ``[text](relative/path)``
(CommonMark) -- strip the code spans and fenced blocks that only *show* link
syntax, resolve each target to a real file, and harvest the results into graph
edges, broken links ("ghosts"), doc->code links, wikilink ambiguity, and the
per-doc machine-link fingerprint the raw-source detector reads.

``lib.doc_graph`` owns discovery, the reference-edge pass, and the signals; it
calls :func:`harvest_links` once per build and reuses :func:`strip_fenced_lines`
and :data:`INLINE_CODE_RE` for its backticked-reference and missing-xref scans.
Inward-only and leaf-level: stdlib only, no ``lib`` import, so the doc and code
extension sets are passed in rather than imported from ``lib.doc_graph``.
"""
from __future__ import annotations

import re
from collections.abc import Callable, Set as AbstractSet
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # annotation only; the caller owns the networkx import
    import networkx as nx

# by-relpath, by-basename and by-stem indexes from ``_build_name_index``.
NameIndex = tuple[dict[str, Path], dict[str, list[Path]], dict[str, list[Path]]]


# Link parsers. Wikilinks: [[target]], [[target|alias]], [[target#anchor]].
_WIKILINK_RE = re.compile(r"\[\[([^\[\]]+?)\]\]")
# Markdown inline links: [text](target). Excludes images handled below.
_MDLINK_RE = re.compile(r"(?<!\!)\[(?:[^\]]*)\]\(([^)]+)\)")
# Schemes / forms that are not intra-repo file links.  Any token matching
# the RFC 3986 URI-scheme pattern (`[a-z][a-z0-9+.-]*:`) is non-navigational:
# `http://`, `https://`, `ftp://` (the scheme-plus-`://` form), but also bare
# schemes such as `tel:`, `mailto:`, `sms:`, `callto:`, `javascript:`, etc.
# Using the generic scheme pattern rather than an allowlist keeps the regex
# stable as new schemes appear and avoids the specific-scheme gap that caused
# `sms:` and `skype:` to be misclassified as broken file references (issue #227).
_EXTERNAL_RE = re.compile(r"^[a-z][a-z0-9+.-]*:", re.IGNORECASE)
# Inline-code spans: backtick-delimited segments on a single logical line. A
# link target inside `[[foo]]` or `[text](./foo.md)` is documentation syntax
# (an Obsidian skill teaching wikilinks, a FORMAT-spec showing a sample), not
# a real navigation edge - the writer formatted it as code on purpose. Caps
# match-length to avoid spanning paragraphs when stray backticks appear.
INLINE_CODE_RE = re.compile(r"`[^`\n]{1,200}`")


def _strip_code_spans(text: str) -> str:
    """Remove fenced code blocks and inline-code spans before link extraction.

    Without this, a markdown doc that *teaches* link syntax (a FORMAT spec, an
    Obsidian-skill how-to) contributes phantom edges to the navigation graph
    and inflates `dangling_links`. The writer formatted those targets as code
    precisely because they are samples, not navigation.
    """
    # Strip fenced blocks first so an inline-code regex can't snag content
    # inside a fence that legitimately contains backticks of its own.
    return INLINE_CODE_RE.sub("", strip_fenced_lines(text))


def _strip_anchor_and_alias(target: str) -> str:
    """Drop a `|alias` (wikilink) and `#anchor` / `?query` from a link target."""
    target = target.split("|", 1)[0]
    target = target.split("#", 1)[0]
    target = target.split("?", 1)[0]
    return target.strip()


def _build_name_index(
    docs: list[Path], repo_root: Path,
) -> NameIndex:
    """Indexes for resolving wikilinks: by relative-path, by basename, by stem.

    Name collisions (two `setup.md` files) are why `Path(link).stem` alone is
    too naive -- by_stem maps a stem to *every* candidate so the resolver can
    disambiguate (prefer same-directory) instead of silently picking one.
    """
    by_relpath: dict[str, Path] = {}
    by_name: dict[str, list[Path]] = {}
    by_stem: dict[str, list[Path]] = {}
    for d in docs:
        rel = d.relative_to(repo_root)
        by_relpath[str(rel).lower()] = d
        by_relpath[str(rel.with_suffix("")).lower()] = d
        by_name.setdefault(d.name.lower(), []).append(d)
        by_stem.setdefault(d.stem.lower(), []).append(d)
    return by_relpath, by_name, by_stem


def _resolve_wikilink(
    raw: str,
    source: Path,
    repo_root: Path,
    by_relpath: dict[str, Path],
    by_name: dict[str, list[Path]],
    by_stem: dict[str, list[Path]],
) -> tuple[Path | None, bool]:
    """Resolve a wikilink target to a doc path. Returns (path, ambiguous)."""
    target = _strip_anchor_and_alias(raw)
    if not target:
        return None, False
    key = target.lower()
    # Path-qualified wikilink (`[[folder/note]]`): try the relative-path index,
    # which is keyed by both the suffixed and suffix-stripped relpath, so
    # `[[folder/note]]` and `[[folder/note.md]]` both resolve here.
    if "/" in target or "\\" in target:
        norm = key.replace("\\", "/")
        if norm in by_relpath:
            return by_relpath[norm], False
    # Bare note name: try basename (with and without .md), then stem.
    candidates: list[Path] = []
    if key in by_name:
        candidates = by_name[key]
    elif f"{key}.md" in by_name:
        candidates = by_name[f"{key}.md"]
    elif key in by_stem:
        candidates = by_stem[key]
    if not candidates:
        return None, False
    if len(candidates) == 1:
        return candidates[0], False
    # Collision: prefer a candidate in the same directory as the source.
    same_dir = [c for c in candidates if c.parent == source.parent]
    if len(same_dir) == 1:
        return same_dir[0], True
    return sorted(candidates)[0], True  # deterministic fallback


def _resolve_mdlink(
    raw: str, source: Path, repo_root: Path,
) -> Path | None:
    """Resolve a CommonMark relative link target to a real file path."""
    target = raw.strip()
    if not target or target.startswith("#"):
        return None
    if _EXTERNAL_RE.match(target):
        return None
    target = _strip_anchor_and_alias(target)
    if not target:
        return None
    # Absolute-from-repo-root ("/docs/x.md") vs relative-to-this-doc.
    if target.startswith("/"):
        candidate = (repo_root / target.lstrip("/"))
    else:
        candidate = (source.parent / target)
    try:
        resolved = candidate.resolve()
    except (OSError, RuntimeError):
        return None
    if not resolved.is_file():
        return None
    try:
        resolved.relative_to(repo_root.resolve())
    except ValueError:
        return None
    return resolved


def _target_exists(raw: str, source: Path, repo_root: Path) -> bool:
    """True if a relative link resolves to an existing path (file OR directory)
    within the repo. Used so a link to a folder (`docs/guides/`) isn't mistaken
    for a broken link just because it isn't a file."""
    target = _strip_anchor_and_alias(raw)
    if not target or target.startswith("#") or _EXTERNAL_RE.match(target):
        return False
    candidate = (repo_root / target.lstrip("/")) if target.startswith("/") else (source.parent / target)
    try:
        resolved = candidate.resolve()
    except (OSError, RuntimeError):
        return False
    if not resolved.exists():
        return False
    try:
        resolved.relative_to(repo_root.resolve())
    except ValueError:
        return False
    return True


# Any indentation (a fence nested under a list item sits four or more spaces
# in), behind any CommonMark container prefix: blockquote `>` markers and a
# list-item marker (`- ~~~`, `1. ~~~`).
_FENCE_OPEN_RE = re.compile(
    r"^[ \t]*(?:>[ \t]*)*(?:(?:[-*+]|\d+[.)])[ \t]+)?(`{3,}|~{3,})"
)


def strip_fenced_lines(text: str) -> str:
    """Drop CommonMark fenced blocks line by line: backtick or tilde fences,
    closed only by the same marker at least as long as the opener. An
    unclosed fence runs to the end of the document."""
    out: list[str] = []
    fence = ""
    for line in text.splitlines():
        m = _FENCE_OPEN_RE.match(line)
        if not fence:
            if m and not (m.group(1)[0] == "`" and "`" in line[m.end():]):
                fence = m.group(1)
            else:
                out.append(line)
        elif m and m.group(1)[0] == fence[0] and len(m.group(1)) >= len(fence) \
                and not line[m.end():].strip():
            fence = ""
    return "\n".join(out)


@dataclass
class LinkHarvest:
    """What the link pass collects besides graph edges, one per build."""

    rel: Callable[[Path], str]
    doc_to_code: list[dict] = field(default_factory=list)
    ambiguous: int = 0
    broken: list[dict] = field(default_factory=list)
    broken_seen: set[tuple[str, str]] = field(default_factory=set)
    # Per-doc count of non-navigational URI-scheme links (mailto:/tel:/external
    # http) - the machine-extraction fingerprint a converted document carries.
    # Feeds raw-source-tree detection (issue #225).
    machine_links: dict[str, int] = field(default_factory=dict)

    def add_broken(self, src: Path, target: str, kind: str) -> None:
        key = (self.rel(src), target)
        if target and key not in self.broken_seen:
            self.broken_seen.add(key)
            self.broken.append({"from": self.rel(src), "target": target, "kind": kind})

    def count_machine_link(self, src: Path) -> None:
        self.machine_links[self.rel(src)] = self.machine_links.get(self.rel(src), 0) + 1


def _harvest_wikilinks(
    d: Path, link_text: str, repo_root: Path, name_index: NameIndex, doc_set: set[Path],
    graph: nx.DiGraph, harvest: LinkHarvest,
) -> None:
    """Wikilinks resolve by note name across the vault."""
    rel = harvest.rel
    for m in _WIKILINK_RE.finditer(link_text):
        # Strip alias/anchor first so the scheme check sees the bare target
        # (e.g. `tel:+1-555-1234` from `[[tel:+1-555-1234|Call us]]`).
        wikilink_target = _strip_anchor_and_alias(m.group(1))
        if _EXTERNAL_RE.match(wikilink_target):
            # Non-navigational URI (`tel:`, `mailto:`, etc.) -- not a note
            # reference; skip without counting as a broken link (issue #227).
            # Count it as a machine-extraction fingerprint (issue #225).
            harvest.count_machine_link(d)
            continue
        tgt, amb = _resolve_wikilink(m.group(1), d, repo_root, *name_index)
        if amb:
            harvest.ambiguous += 1
        if tgt is None:
            harvest.add_broken(d, wikilink_target, "wikilink")
            continue
        if tgt in doc_set and tgt != d:
            graph.add_edge(rel(d), rel(tgt), kind="link")


def _harvest_mdlinks(
    d: Path, link_text: str, repo_root: Path, doc_set: set[Path],
    graph: nx.DiGraph, harvest: LinkHarvest, *, doc_exts: AbstractSet[str], code_exts: AbstractSet[str],
) -> None:
    """CommonMark links resolve relative to the doc's directory. A target with
    a ``doc_exts`` suffix in ``doc_set`` is a graph edge; one with a
    ``code_exts`` suffix is a doc->code link."""
    rel = harvest.rel
    for m in _MDLINK_RE.finditer(link_text):
        raw = m.group(1)
        tgt = _resolve_mdlink(raw, d, repo_root)
        if tgt is None:
            _record_unresolved_mdlink(raw, d, repo_root, harvest)
            continue
        if tgt.suffix.lower() in doc_exts and tgt in doc_set:
            if tgt != d:
                graph.add_edge(rel(d), rel(tgt), kind="link")
        elif tgt.suffix.lower() in code_exts:
            harvest.doc_to_code.append({"doc": rel(d), "code": rel(tgt)})


def _record_unresolved_mdlink(
    raw: str, d: Path, repo_root: Path, harvest: LinkHarvest,
) -> None:
    """A relative-looking link that resolves to nothing is broken (a "ghost").
    External URLs, pure #anchors, and links to an existing directory are not."""
    cleaned = _strip_anchor_and_alias(raw)
    if cleaned and _EXTERNAL_RE.match(cleaned):
        # Non-navigational URI (mailto:/tel:/external http): the
        # machine-extraction fingerprint, not a broken link (#225).
        harvest.count_machine_link(d)
    elif (cleaned and not raw.strip().startswith("#")
            and not _target_exists(raw, d, repo_root)):
        harvest.add_broken(d, cleaned, "mdlink")


def harvest_links(
    docs: list[Path], texts: dict[Path, str], repo_root: Path, graph: nx.DiGraph,
    rel: Callable[[Path], str],
    *, doc_exts: AbstractSet[str], code_exts: AbstractSet[str],
) -> LinkHarvest:
    """Add every wikilink and CommonMark link edge to ``graph`` and collect
    broken links, doc-to-code links, ambiguity, and machine-link counts.
    ``doc_exts`` / ``code_exts`` are ``lib.doc_graph``'s extension sets."""
    name_index = _build_name_index(docs, repo_root)
    doc_set = set(docs)
    harvest = LinkHarvest(rel=rel)
    for d in docs:
        text = texts.get(d)
        if text is None:  # unreadable: skipped, Layer 0 stays best-effort
            continue
        # Strip code spans before harvesting links: a link target inside a
        # fence or backtick span is a documentation sample (FORMAT specs,
        # wikilink-syntax demos), not a navigation edge.
        link_text = _strip_code_spans(text)
        _harvest_wikilinks(d, link_text, repo_root, name_index, doc_set, graph, harvest)
        _harvest_mdlinks(
            d, link_text, repo_root, doc_set, graph, harvest,
            doc_exts=doc_exts, code_exts=code_exts,
        )
    return harvest
