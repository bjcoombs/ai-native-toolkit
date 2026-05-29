"""Doc link-graph for Layer 0 navigability scoring.

Navigability is a graph property, so we measure it as one rather than checking
for the presence of a README. We parse every doc for both link forms an LLM
wiki uses -- ``[[wikilinks]]`` (Obsidian / Karpathy-pattern) and
``[text](relative/path)`` (CommonMark) -- resolve them to real files, and build
a directed graph. From that graph we derive:

  - **PageRank / centrality** -> the load-bearing docs (hubs / MOCs) surface
    automatically, no filename guessing.
  - **Orphans** -> docs with no inbound links are unreachable by traversal;
    a navigability gap.
  - **Connectivity / reachability** -> a navigable doc set is one connected
    island, fully reachable from the entry points (README / AGENTS.md / top
    MOC). We report orphan-rate, island-count and reachability-%.
  - **MOC validation** -> a *declared* MOC (``index.md``, a note named "MOC")
    is only real if the graph shows it as a structural hub. Declared-but-not-
    wired is a finding: a named map that doesn't actually link its cluster.
  - **Doc->code edges** -> links pointing at source files, a first-class
    doc->code association source for the staleness heatmap.

Core dependency is ``networkx``. The parser is native (handles both link forms,
resolves relative targets, strips ``#anchors``, handles name collisions) so the
module needs no Obsidian-specific package; ``obsidiantools`` is detected and
noted as an optional accelerator when an Obsidian vault is present, but is never
required. If ``networkx`` is unavailable the module degrades to an
``available=False`` result rather than crashing -- the assessment never blocks.
"""
from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass, field
from pathlib import Path

try:  # networkx is the core dep; degrade rather than crash if it is missing.
    import networkx as nx

    _NETWORKX_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised only on a broken env
    nx = None  # type: ignore[assignment]
    _NETWORKX_AVAILABLE = False

from lib.git_churn import tracked_files  # noqa: E402


DOC_EXTENSIONS = {".md", ".mdx", ".markdown"}

# Extensions we treat as "code" when a doc link points at one (doc->code edge).
CODE_EXTENSIONS = {
    ".py", ".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".go", ".java",
    ".kt", ".kts", ".rs", ".rb", ".cs", ".swift", ".dart", ".cpp", ".cc",
    ".cxx", ".c", ".h", ".hpp", ".hh", ".php", ".scala", ".m", ".mm",
    ".sh", ".bash", ".sql", ".vue", ".svelte",
}

# Directories never worth walking for docs. Mirrors the treemap's EXCLUDE_DIRS
# (kept local so this module pulls in no heavy deps) plus .assess itself, since
# /assess writes its own wiki there and we must not analyse our own output.
EXCLUDE_DIRS = {
    ".git", "node_modules", "dist", "build", "target", "vendor",
    ".venv", "venv", "__pycache__", ".gradle", ".idea", ".mvn",
    "worktree", ".understand-anything", ".obsidian", ".taskmaster",
    ".claude", ".next", ".nuxt", ".output", ".svelte-kit", ".astro",
    "out", "coverage", "htmlcov", "Pods", "DerivedData", "flutter_assets",
    ".assess",
}

# Entry-doc basenames: legitimately have no inbound links (they are where a
# reader starts), so they are excluded from the orphan count and used as the
# roots for reachability.
ENTRY_BASENAMES = {"readme.md", "agents.md", "claude.md", "index.md", "home.md"}

# Declared-MOC conventions (filename signals a map-of-content). Cross-checked
# against the graph: a real MOC is a structural hub.
MOC_BASENAMES = {"index.md", "_index.md", "home.md", "moc.md", "_moc.md"}
_MOC_STEM_RE = re.compile(r"(^|[ _-])moc([ _-]|$)|map[ _-]?of[ _-]?content",
                          re.IGNORECASE)

# A declared MOC counts as "wired" (a real structural hub) once it links out to
# at least this many other docs. Below it, the map is named but not built.
HUB_MIN_OUTDEGREE = 3

# Link parsers. Wikilinks: [[target]], [[target|alias]], [[target#anchor]].
_WIKILINK_RE = re.compile(r"\[\[([^\[\]]+?)\]\]")
# Markdown inline links: [text](target). Excludes images handled below.
_MDLINK_RE = re.compile(r"(?<!\!)\[(?:[^\]]*)\]\(([^)]+)\)")
# Schemes / forms that are not intra-repo file links.
_EXTERNAL_RE = re.compile(r"^[a-z][a-z0-9+.-]*://|^mailto:|^tel:", re.IGNORECASE)
_FENCE_RE = re.compile(r"```.*?\n.*?```", re.DOTALL)  # fenced code blocks
# Inline-code spans: backtick-delimited segments on a single logical line. A
# link target inside `[[foo]]` or `[text](./foo.md)` is documentation syntax
# (an Obsidian skill teaching wikilinks, a FORMAT-spec showing a sample), not
# a real navigation edge - the writer formatted it as code on purpose. Caps
# match-length to avoid spanning paragraphs when stray backticks appear.
_INLINE_CODE_RE = re.compile(r"`[^`\n]{1,200}`")


def _strip_code_spans(text: str) -> str:
    """Remove fenced code blocks and inline-code spans before link extraction.

    Without this, a markdown doc that *teaches* link syntax (a FORMAT spec, an
    Obsidian-skill how-to) contributes phantom edges to the navigation graph
    and inflates `dangling_links`. The writer formatted those targets as code
    precisely because they are samples, not navigation.
    """
    # Strip fenced blocks first so an inline-code regex can't snag content
    # inside a fence that legitimately contains backticks of its own.
    return _INLINE_CODE_RE.sub("", _FENCE_RE.sub("", text))

# Caps so a pathological repo can't bloat run-context.json.
MAX_BROKEN_LINKS = 60
MAX_MISSING_XREFS = 60
# Conventional filenames that get mentioned all the time and don't need a
# cross-reference every time they're named - excluded from the missing-xref scan.
_XREF_SKIP_NAMES = {
    "readme.md", "index.md", "_index.md", "license.md", "changelog.md",
    "contributing.md", "code_of_conduct.md", "security.md", "agents.md",
    "claude.md", "gemini.md", "home.md", "notes.md",
}


@dataclass
class DocGraphResult:
    available: bool = True
    reason: str = ""
    doc_count: int = 0
    edge_count: int = 0
    hubs: list[dict] = field(default_factory=list)        # [{path, pagerank, out_degree, in_degree}]
    orphans: list[str] = field(default_factory=list)      # in_degree == 0 and not an entry
    orphan_rate: float = 0.0
    island_count: int = 0
    reachability_pct: float = 0.0
    entry_points: list[str] = field(default_factory=list)
    unreachable: list[str] = field(default_factory=list)
    declared_mocs: list[dict] = field(default_factory=list)  # [{path, out_degree, is_structural_hub}]
    moc_named_but_not_wired: list[str] = field(default_factory=list)
    doc_to_code_edges: list[dict] = field(default_factory=list)  # [{doc, code}]
    dangling_links: int = 0
    # Broken links: a link whose target file doesn't exist (a "ghost"). The
    # renderer draws these as ghost nodes - the missing name is the suggested fix.
    broken_links: list[dict] = field(default_factory=list)  # [{from, target, kind}]
    # Missing cross-references: a doc names another doc but never links to it
    # (Karpathy Lint). [{from, to}].
    missing_xrefs: list[dict] = field(default_factory=list)
    ambiguous_wikilinks: int = 0
    vault_detected: bool = False
    obsidiantools_available: bool = False
    # Full per-doc PageRank, keyed by rel path. Sizes the docs-staleness
    # heatmap (a stale hub must dominate). Kept off as_dict() so run-context
    # stays lean on doc-heavy repos -- the top-10 hubs are serialised instead.
    pagerank: dict[str, float] = field(default_factory=dict)
    # The underlying networkx DiGraph (nodes = doc rel-paths, edges = doc->doc).
    # Kept off as_dict(); the connectivity-graph SVG renderer needs the full
    # edge list that the serialised signals don't carry.
    graph: object = None

    def as_dict(self) -> dict:
        return {
            "available": self.available,
            "reason": self.reason,
            "doc_count": self.doc_count,
            "edge_count": self.edge_count,
            "hubs": self.hubs,
            "orphans": self.orphans,
            "orphan_rate": round(self.orphan_rate, 3),
            "island_count": self.island_count,
            "reachability_pct": round(self.reachability_pct, 3),
            "entry_points": self.entry_points,
            "unreachable": self.unreachable,
            "declared_mocs": self.declared_mocs,
            "moc_named_but_not_wired": self.moc_named_but_not_wired,
            "doc_to_code_edges": self.doc_to_code_edges,
            "dangling_links": self.dangling_links,
            "broken_links": self.broken_links,
            "missing_xrefs": self.missing_xrefs,
            "ambiguous_wikilinks": self.ambiguous_wikilinks,
            "vault_detected": self.vault_detected,
            "obsidiantools_available": self.obsidiantools_available,
        }


def is_repo_file(path: Path, repo_root: Path, tracked: set[Path] | None) -> bool:
    """True if `path` is genuinely part of the repo.

    Excludes two classes of non-repo file the scan must ignore:
      - symlinks (or rglob escapes) whose *resolved* path lands outside the
        repo - e.g. a CLAUDE.md symlinked to the user's home;
      - untracked / git-ignored files when the repo is under git (e.g. a
        contributor's personal notes left in the working tree). `tracked` is
        None for non-git trees, in which case only the symlink guard applies.
    """
    try:
        real = path.resolve()
    except OSError:
        return False
    if not real.is_relative_to(repo_root):
        return False
    if tracked is not None and real not in tracked:
        return False
    return True


def discover_doc_files(
    repo_root: Path,
    extra_exclude_dirs: set[str] | None = None,
    extra_exclude_patterns: list[str] | None = None,
) -> list[Path]:
    """Return all in-repo markdown docs under repo_root, skipping excluded dirs."""
    from lib.assess_config import is_user_excluded
    repo_root = repo_root.resolve()
    tracked = tracked_files(repo_root)
    extra_dirs = extra_exclude_dirs or set()
    extra_pats = extra_exclude_patterns or []
    docs: list[Path] = []
    for path in repo_root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in DOC_EXTENSIONS:
            continue
        try:
            rel = path.relative_to(repo_root)
        except ValueError:
            continue
        if any(part in EXCLUDE_DIRS for part in rel.parts):
            continue
        if is_user_excluded(rel, extra_dirs, extra_pats):
            continue
        if not is_repo_file(path, repo_root, tracked):
            continue
        docs.append(path)
    return sorted(docs)


def _strip_anchor_and_alias(target: str) -> str:
    """Drop a `|alias` (wikilink) and `#anchor` / `?query` from a link target."""
    target = target.split("|", 1)[0]
    target = target.split("#", 1)[0]
    target = target.split("?", 1)[0]
    return target.strip()


def _vault_detected(repo_root: Path) -> bool:
    return (repo_root / ".obsidian").is_dir()


def _obsidiantools_available() -> bool:
    try:  # optional accelerator; never required.
        import obsidiantools  # noqa: F401

        return True
    except ImportError:
        return False


def _build_name_index(
    docs: list[Path], repo_root: Path,
) -> tuple[dict[str, Path], dict[str, list[Path]], dict[str, list[Path]]]:
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


def _missing_xrefs(docs, texts: dict, graph, repo_root: Path, rel) -> list[dict]:
    """Docs that name another doc's filename in prose but never link to it
    (Karpathy Lint: "missing cross-references").

    High-precision: matches the exact filename (e.g. `payments.md`) outside
    fenced code, only for non-conventional target docs, and only when no link
    to that target already exists.
    """
    name_to_doc: dict[str, Path] = {
        d.name.lower(): d for d in docs if d.name.lower() not in _XREF_SKIP_NAMES
    }
    if not name_to_doc:
        return []
    alt = "|".join(re.escape(n) for n in sorted(name_to_doc, key=len, reverse=True))
    pattern = re.compile(r"(?<![\w./-])(" + alt + r")\b", re.IGNORECASE)
    edges = set(graph.edges())
    out: list[dict] = []
    for d in docs:
        text = texts.get(d)
        if not text:
            continue
        body = _FENCE_RE.sub("", text)
        seen: set[Path] = set()
        for m in pattern.finditer(body):
            t = name_to_doc.get(m.group(1).lower())
            if t is None or t == d or t in seen:
                continue
            seen.add(t)
            if (rel(d), rel(t)) not in edges:  # already linked -> not missing
                out.append({"from": rel(d), "to": rel(t)})
    return out


def radial_shells(graph, entries, ring: int = 24) -> list[list[str]]:
    """Order nodes into concentric shells by link-distance from the entry points.

    Shell 0 = the entry points; shell k = docs k hops away (following links);
    then the unreachable docs, chunked into progressively larger outer rings.
    Pure graph traversal - no layout - so it's unit-testable without numpy.
    """
    dist: dict[str, int] = {e: 0 for e in entries if e in graph}
    frontier = list(dist)
    while frontier:
        nxt = []
        for u in frontier:
            for v in graph.successors(u):
                if v not in dist:
                    dist[v] = dist[u] + 1
                    nxt.append(v)
        frontier = nxt
    all_nodes = list(graph.nodes())
    max_d = max(dist.values(), default=0)
    shells = [sorted(n for n in all_nodes if dist.get(n) == d) for d in range(max_d + 1)]
    unreachable = sorted(n for n in all_nodes if n not in dist)
    i, cap = 0, ring
    while i < len(unreachable):
        shells.append(unreachable[i:i + cap])
        i += cap
        cap += 12
    return [s for s in shells if s]


def classify_node(node: str, entries: set, unreachable: set, orphans: set) -> str:
    """Navigability status of a node: entry / reachable / orphan / island."""
    if node in entries:
        return "entry"
    if node not in unreachable:
        return "reachable"
    if node in orphans:
        return "orphan"
    return "island"


def build_doc_graph(
    repo_root: Path, doc_files: list[Path] | None = None,
    extra_exclude_dirs: set[str] | None = None,
    extra_exclude_patterns: list[str] | None = None,
) -> DocGraphResult:
    """Parse docs, build the link graph, and derive navigability signals."""
    repo_root = repo_root.resolve()
    vault = _vault_detected(repo_root)
    obs = _obsidiantools_available()

    if not _NETWORKX_AVAILABLE:
        return DocGraphResult(
            available=False,
            reason="networkx not installed; doc link-graph not assessed",
            vault_detected=vault,
            obsidiantools_available=obs,
        )

    docs = (
        doc_files if doc_files is not None
        else discover_doc_files(
            repo_root,
            extra_exclude_dirs=extra_exclude_dirs,
            extra_exclude_patterns=extra_exclude_patterns,
        )
    )
    docs = [d.resolve() for d in docs]
    if not docs:
        return DocGraphResult(
            available=True, reason="no markdown docs found", doc_count=0,
            vault_detected=vault, obsidiantools_available=obs,
        )

    by_relpath, by_name, by_stem = _build_name_index(docs, repo_root)
    doc_set = set(docs)

    def rel(p: Path) -> str:
        return str(p.relative_to(repo_root))

    graph = nx.DiGraph()
    for d in docs:
        graph.add_node(rel(d))

    doc_to_code: list[dict] = []
    ambiguous = 0
    broken: list[dict] = []
    _broken_seen: set[tuple[str, str]] = set()
    texts: dict[Path, str] = {}

    def _add_broken(src: Path, target: str, kind: str) -> None:
        key = (rel(src), target)
        if target and key not in _broken_seen:
            _broken_seen.add(key)
            broken.append({"from": rel(src), "target": target, "kind": kind})

    for d in docs:
        try:
            text = d.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            # Best-effort scan: skip an unreadable doc rather than aborting the
            # whole graph build (Layer 0 stays best-effort).
            continue
        texts[d] = text
        # Strip code spans before harvesting links: a link target inside a
        # fence or backtick span is a documentation sample (FORMAT specs,
        # wikilink-syntax demos), not a navigation edge.
        link_text = _strip_code_spans(text)
        # Wikilinks resolve by note name across the vault.
        for m in _WIKILINK_RE.finditer(link_text):
            tgt, amb = _resolve_wikilink(
                m.group(1), d, repo_root, by_relpath, by_name, by_stem,
            )
            if amb:
                ambiguous += 1
            if tgt is None:
                _add_broken(d, _strip_anchor_and_alias(m.group(1)), "wikilink")
                continue
            if tgt in doc_set and tgt != d:
                graph.add_edge(rel(d), rel(tgt))
        # CommonMark links resolve relative to the doc's directory.
        for m in _MDLINK_RE.finditer(link_text):
            raw = m.group(1)
            tgt = _resolve_mdlink(raw, d, repo_root)
            if tgt is None:
                # A relative-looking link that resolves to nothing is broken
                # (a "ghost"). External URLs, pure #anchors, and links to an
                # existing directory are not broken.
                cleaned = _strip_anchor_and_alias(raw)
                if (cleaned and not raw.strip().startswith("#")
                        and not _EXTERNAL_RE.match(cleaned)
                        and not _target_exists(raw, d, repo_root)):
                    _add_broken(d, cleaned, "mdlink")
                continue
            if tgt.suffix.lower() in DOC_EXTENSIONS and tgt in doc_set:
                if tgt != d:
                    graph.add_edge(rel(d), rel(tgt))
            elif tgt.suffix.lower() in CODE_EXTENSIONS:
                doc_to_code.append({"doc": rel(d), "code": rel(tgt)})

    missing = _missing_xrefs(docs, texts, graph, repo_root, rel)

    result = _derive_signals(
        graph=graph, docs=docs, repo_root=repo_root, rel=rel,
        doc_to_code=doc_to_code, dangling=len(broken), ambiguous=ambiguous,
        vault=vault, obs=obs,
    )
    result.broken_links = broken[:MAX_BROKEN_LINKS]
    result.missing_xrefs = missing[:MAX_MISSING_XREFS]
    return result


def _pagerank(graph, alpha: float = 0.85, max_iter: int = 100,
              tol: float = 1e-9) -> dict[str, float]:
    """PageRank by pure-Python power iteration (with dangling-node handling).

    networkx's own ``pagerank`` routes through a scipy/numpy backend, which the
    deterministic core deliberately does not depend on. This keeps centrality
    working with networkx alone — the graph structure is networkx's; only the
    iteration is local. Semantics match ``nx.pagerank`` (dangling rank is
    redistributed uniformly each step).
    """
    nodes = list(graph.nodes())
    n = len(nodes)
    if n == 0:
        return {}
    if graph.number_of_edges() == 0:
        return {x: 1.0 / n for x in nodes}
    out_deg = dict(graph.out_degree())
    pr = {x: 1.0 / n for x in nodes}
    for _ in range(max_iter):
        prev = pr
        dangling = sum(prev[x] for x in nodes if out_deg[x] == 0)
        base = (1.0 - alpha) / n + alpha * dangling / n
        nxt = {x: base for x in nodes}
        for src in nodes:
            d = out_deg[src]
            if d == 0:
                continue
            share = alpha * prev[src] / d
            for dst in graph.successors(src):
                nxt[dst] += share
        err = sum(abs(nxt[x] - prev[x]) for x in nodes)
        pr = nxt
        if err < tol:
            break
    return pr


def _is_declared_moc(path: Path) -> bool:
    name = path.name.lower()
    if name in MOC_BASENAMES:
        return True
    return bool(_MOC_STEM_RE.search(path.stem))


def _pick_entry_points(
    docs: list[Path], repo_root: Path, pagerank: dict[str, float], rel,
) -> list[str]:
    """Entry roots for reachability: root-level README/AGENTS/CLAUDE/index plus
    the single highest-PageRank declared MOC. Falls back to the top doc overall
    so reachability is always computable."""
    entries: list[str] = []
    for d in docs:
        r = d.relative_to(repo_root)
        if len(r.parts) == 1 and r.name.lower() in ENTRY_BASENAMES:
            entries.append(rel(d))
    mocs = [(rel(d), pagerank.get(rel(d), 0.0)) for d in docs if _is_declared_moc(d)]
    if mocs:
        top_moc = max(mocs, key=lambda x: x[1])[0]
        if top_moc not in entries:
            entries.append(top_moc)
    if not entries and pagerank:
        entries.append(max(pagerank, key=lambda k: pagerank[k]))
    return entries


def _derive_signals(
    *, graph, docs: list[Path], repo_root: Path, rel,
    doc_to_code: list[dict], dangling: int, ambiguous: int,
    vault: bool, obs: bool,
) -> DocGraphResult:
    nodes = list(graph.nodes())
    n = len(nodes)

    pagerank = _pagerank(graph)

    in_deg = dict(graph.in_degree())
    out_deg = dict(graph.out_degree())

    entry_set = set(_pick_entry_points(docs, repo_root, pagerank, rel))

    orphans = sorted(
        x for x in nodes if in_deg.get(x, 0) == 0 and x not in entry_set
    )
    orphan_rate = len(orphans) / n if n else 0.0

    island_count = nx.number_weakly_connected_components(graph) if n else 0

    reachable: set[str] = set()
    for entry in entry_set:
        if entry in graph:
            reachable.add(entry)
            reachable |= nx.descendants(graph, entry)
    reachability_pct = len(reachable) / n if n else 0.0
    unreachable = sorted(set(nodes) - reachable)

    hubs = sorted(
        ({"path": x, "pagerank": round(pagerank.get(x, 0.0), 4),
          "out_degree": out_deg.get(x, 0), "in_degree": in_deg.get(x, 0)}
         for x in nodes),
        key=lambda h: (-h["pagerank"], -h["out_degree"], h["path"]),
    )[:10]

    declared: list[dict] = []
    not_wired: list[str] = []
    for d in docs:
        if not _is_declared_moc(d):
            continue
        r = rel(d)
        od = out_deg.get(r, 0)
        is_hub = od >= HUB_MIN_OUTDEGREE
        declared.append({"path": r, "out_degree": od, "is_structural_hub": is_hub})
        if not is_hub:
            not_wired.append(r)

    return DocGraphResult(
        available=True,
        doc_count=n,
        edge_count=graph.number_of_edges(),
        hubs=hubs,
        orphans=orphans,
        orphan_rate=orphan_rate,
        island_count=island_count,
        reachability_pct=reachability_pct,
        entry_points=sorted(entry_set),
        unreachable=unreachable,
        declared_mocs=declared,
        moc_named_but_not_wired=sorted(not_wired),
        doc_to_code_edges=doc_to_code,
        dangling_links=dangling,
        ambiguous_wikilinks=ambiguous,
        vault_detected=vault,
        obsidiantools_available=obs,
        pagerank={k: round(v, 6) for k, v in pagerank.items()},
        graph=graph,
    )


def _broken_link_key(src: str, target: str, kind: str | None) -> str:
    """Canonical grouping key for a broken link's missing target.

    Mirrors ``_resolve_mdlink``'s path arithmetic so links that point at the same
    absent file share a key whatever way they're spelt:

    - A markdown link starting ``/`` is root-absolute — resolved from the repo
      root (``/CLAUDE.md`` -> ``CLAUDE.md``), matching ``_resolve_mdlink``'s
      ``repo_root / target.lstrip("/")`` branch. Without this, ``/CLAUDE.md`` and
      ``CLAUDE.md`` would key apart and the duplicate ghost this function exists
      to kill would survive for the root-absolute spelling.
    - Any other markdown link resolves relative to the source file's directory
      (``../CLAUDE.md`` from a subdir collapses onto the root ``CLAUDE.md``).
    - A wikilink resolves by note name globally, so it keys on the bare name.

    Known limit (intentional, not fixed): wikilinks and markdown links live in
    different resolution domains, so ``[[CLAUDE]]`` (key ``CLAUDE``) and
    ``[x](CLAUDE.md)`` (key ``CLAUDE.md``) at the same missing file do not merge.
    """
    if kind == "wikilink":
        return target  # wikilinks resolve by note name, not by directory
    if not target:
        return target
    if target.startswith("/"):
        return posixpath.normpath(target.lstrip("/"))
    return posixpath.normpath(posixpath.join(posixpath.dirname(src), target))


def group_broken_links(broken_links: list[dict]) -> list[dict]:
    """Collapse broken links by the missing file they point at.

    Several links can name the same non-existent target — `README.md` and
    `CONTRIBUTING.md` both linking a missing `CLAUDE.md`, say. They describe one
    absent file, so the renderer should draw one ghost they both tether to, not a
    separate ghost per link.

    Targets are normalised to a canonical key (see ``_broken_link_key``) before
    grouping. Returns ``[{"target", "sources"}]`` ordered by descending source
    count then key, so the most-referenced ghost is rendered first.
    """
    groups: dict[str, list[str]] = {}
    for bl in broken_links:
        src = bl.get("from") or ""
        target = bl.get("target") or "?"
        key = _broken_link_key(src, target, bl.get("kind"))
        sources = groups.setdefault(key, [])
        if src not in sources:
            sources.append(src)
    return [
        {"target": key, "sources": sources}
        for key, sources in sorted(
            groups.items(), key=lambda kv: (-len(kv[1]), kv[0])
        )
    ]
