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

Link syntax, resolution and the link pass live in ``lib.doc_links``; the
render-side helpers (radial shells, node classes, ghost grouping) live in
``lib.doc_graph_layout``.
"""
from __future__ import annotations

import os
import posixpath
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path

from lib.doc_links import INLINE_CODE_RE, LinkHarvest, harvest_links, strip_fenced_lines
from lib.git_churn import tracked_files
from lib.run_context_types import (
    BrokenLink, DeclaredMoc, DirectoryBreakdownRow, DocGraphBlock, DocHub, DocSignal,
    DocToCodeEdge, ExcludedTree, LinkParent, MissingXref, SourceTree,
)

# Maps an absolute doc path to its repo-relative string form (the graph's node key).
RelFn = Callable[[Path], str]

try:  # networkx is the core dep; degrade rather than crash if it is missing.
    import networkx as nx

    _NETWORKX_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised only on a broken env
    nx = None  # type: ignore[assignment, unused-ignore]  # stubs absent: nx is Any; installed: None mismatches the module
    _NETWORKX_AVAILABLE = False


DOC_EXTENSIONS = {".md", ".mdx", ".markdown"}

# Obsidian Bases view files. Not markdown - they are query hubs that surface
# notes dynamically - so they are discovered separately and added as hub nodes
# (issue #176), never counted as prose docs by the markdown walk.
BASE_EXTENSIONS = {".base"}

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
    ".assess", "mutants",
}

# Path-segment *sequences* (not single dir names) that mark non-navigational
# trees. Test fixtures live at `**/tests/fixtures/**`: they are inputs to the
# scanners (sample CLAUDE.md / monolithic-instruction files that exist only to
# exercise the detectors), never repo docs. Counting them inflates the orphan
# rate and depresses the Layer 0 navigability read (issue #83). Matching the
# consecutive sequence - rather than the bare `fixtures` dir name - avoids
# over-excluding an unrelated top-level `fixtures/` of real content.
EXCLUDE_PATH_SEQUENCES: tuple[tuple[str, ...], ...] = (
    ("tests", "fixtures"),
)


def _contains_sequence(parts: tuple[str, ...], seq: tuple[str, ...]) -> bool:
    """True if `seq` appears as consecutive elements anywhere in `parts`."""
    n = len(seq)
    if n == 0 or n > len(parts):
        return False
    return any(parts[i:i + n] == seq for i in range(len(parts) - n + 1))


def is_excluded_path(rel: Path) -> bool:
    """True if `rel` lies under a built-in non-navigational tree.

    Combines the single-segment `EXCLUDE_DIRS` match (`.assess`, `node_modules`,
    ...) with the multi-segment `EXCLUDE_PATH_SEQUENCES` match (`tests/fixtures`).
    This is the built-in default, applied by every scan; user-supplied
    `config.toml` / `--exclude` excludes layer on top via `is_user_excluded`.
    """
    parts = rel.parts
    if any(part in EXCLUDE_DIRS for part in parts):
        return True
    return any(_contains_sequence(parts, seq) for seq in EXCLUDE_PATH_SEQUENCES)

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

# Caps so a pathological repo can't bloat run-context.json.
MAX_BROKEN_LINKS = 60
MAX_MISSING_XREFS = 60
# directory_breakdown keeps the rows with the largest gaps; directory_count
# carries the full total so a truncated list still says how many there were.
MAX_DIRECTORY_BREAKDOWN = 30
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
    hubs: list[DocHub] = field(default_factory=list)
    orphans: list[str] = field(default_factory=list)      # in_degree == 0 and not an entry
    orphan_rate: float = 0.0
    island_count: int = 0
    reachability_pct: float = 0.0
    entry_points: list[str] = field(default_factory=list)
    unreachable: list[str] = field(default_factory=list)
    declared_mocs: list[DeclaredMoc] = field(default_factory=list)
    moc_named_but_not_wired: list[str] = field(default_factory=list)
    doc_to_code_edges: list[DocToCodeEdge] = field(default_factory=list)
    dangling_links: int = 0
    # Broken links: a link whose target file doesn't exist (a "ghost"). The
    # renderer draws these as ghost nodes - the missing name is the suggested fix.
    broken_links: list[BrokenLink] = field(default_factory=list)
    # Raw-source-tree exclusion (issue #225). The headline read-side metrics
    # above (orphan_rate, reachability_pct, orphans, unreachable, island_count,
    # broken_links, dangling_links) describe the *curated* wiki layer: subtrees
    # of raw, machine-extracted source documents (a disclosure/SAR export of
    # converted .msg/.pdf files) are detected and excluded so they don't inflate
    # the figures. Each excluded tree is named with its file count so the
    # exclusion stays legible; the raw layer's own figures are reported alongside.
    excluded_raw_trees: list[ExcludedTree] = field(default_factory=list)
    raw_source_doc_count: int = 0       # total docs across all excluded raw trees
    curated_doc_count: int = 0          # docs in the curated layer (== doc_count)
    raw_source_orphan_rate: float = 0.0  # orphan rate within the raw layer
    raw_source_broken_links: int = 0     # broken links originating in the raw layer
    # Working-notes exclusion (issue #366): pattern-named notes hung off one or
    # two index files (plans, session logs, tickets) leave the headline the
    # same way, named with a file count, with the notes layer's own figures.
    excluded_working_notes_trees: list[ExcludedTree] = field(default_factory=list)
    working_notes_doc_count: int = 0
    working_notes_orphan_rate: float = 0.0
    working_notes_broken_links: int = 0
    # Link-only figures (issue #353). The headline orphan_rate and
    # reachability_pct count reference edges (a backticked doc path) as well as
    # links; these two are the same figures over link edges alone.
    link_only_orphan_rate: float = 0.0
    link_only_reachability_pct: float = 0.0
    # Per-top-level-directory counts (issue #365) over the same curated layer
    # as the headline. While len(directory_breakdown) == directory_count the
    # rows sum to doc_count, len(unreachable) and dangling_links; a list cut
    # at MAX_DIRECTORY_BREAKDOWN sums to less.
    directory_breakdown: list[DirectoryBreakdownRow] = field(default_factory=list)
    directory_count: int = 0
    # One link-path parent per doc (PRD item 1c), over link edges alone - the
    # same edge set as link_only_reachability_pct, so a doc a reference edge
    # brought in has no parent here. [{path, link_parent, link_entry}], one
    # record per node, sorted by path: len(link_parents) == doc_count is the
    # bound, so a consumer never asks a second question to learn that a doc has
    # no link path. Walking link_parent up from a doc reconstructs the whole
    # strip back to link_entry, the entry document its walk started from.
    # Exported despite the pagerank precedent below: a path strip needs the
    # exact doc a run flags. LLM readers of the block drop it with
    # del(.link_parents), since nothing they score reads it.
    link_parents: list[LinkParent] = field(default_factory=list)
    # Missing cross-references: a doc names another doc but never links to it
    # (Karpathy Lint).
    missing_xrefs: list[MissingXref] = field(default_factory=list)
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
    # None until build_doc_graph fills it (and on the degrade paths).
    graph: nx.DiGraph | None = None

    def as_dict(self) -> DocGraphBlock:
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
            "excluded_raw_trees": self.excluded_raw_trees,
            "raw_source_doc_count": self.raw_source_doc_count,
            "curated_doc_count": self.curated_doc_count,
            "raw_source_orphan_rate": round(self.raw_source_orphan_rate, 3),
            "raw_source_broken_links": self.raw_source_broken_links,
            "excluded_working_notes_trees": self.excluded_working_notes_trees,
            "working_notes_doc_count": self.working_notes_doc_count,
            "working_notes_orphan_rate": round(self.working_notes_orphan_rate, 3),
            "working_notes_broken_links": self.working_notes_broken_links,
            "link_only_orphan_rate": round(self.link_only_orphan_rate, 3),
            "link_only_reachability_pct": round(self.link_only_reachability_pct, 3),
            "directory_breakdown": self.directory_breakdown,
            "directory_count": self.directory_count,
            "link_parents": self.link_parents,
        }


def is_repo_file(path: Path, repo_root: Path, tracked: set[Path] | frozenset[Path] | None) -> bool:
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


def _discover_files(
    repo_root: Path,
    extensions: set[str],
    extra_exclude_dirs: set[str] | None = None,
    extra_exclude_patterns: list[str] | None = None,
    scope: Path | None = None,
) -> list[Path]:
    """Return all in-repo files of the given extensions, skipping excluded dirs.

    The single walk shared by the markdown-doc and `.base`-hub discovery so both
    honour the identical exclude resolution (built-in defaults + user excludes).

    `scope` (an absolute path under `repo_root`) restricts discovery to a
    subtree for `/assess <path>` monorepo scoping; omit it (the default) for a
    whole-repo run, in which case the result is unchanged.
    """
    from lib.assess_config import is_user_excluded
    repo_root = repo_root.resolve()
    scope_abs = scope.resolve() if scope is not None else None
    tracked = tracked_files(repo_root)
    extra_dirs = extra_exclude_dirs or set()
    extra_pats = extra_exclude_patterns or []
    found: list[Path] = []
    for path in repo_root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in extensions:
            continue
        if scope_abs is not None and not path.resolve().is_relative_to(scope_abs):
            continue
        try:
            rel = path.relative_to(repo_root)
        except ValueError:
            continue
        if is_excluded_path(rel):
            continue
        if is_user_excluded(rel, extra_dirs, extra_pats):
            continue
        if not is_repo_file(path, repo_root, tracked):
            continue
        found.append(path)
    return sorted(found)


def discover_doc_files(
    repo_root: Path,
    extra_exclude_dirs: set[str] | None = None,
    extra_exclude_patterns: list[str] | None = None,
    scope: Path | None = None,
) -> list[Path]:
    """Return all in-repo markdown docs under repo_root, skipping excluded dirs."""
    return _discover_files(
        repo_root, DOC_EXTENSIONS, extra_exclude_dirs, extra_exclude_patterns,
        scope=scope,
    )


def discover_base_files(
    repo_root: Path,
    extra_exclude_dirs: set[str] | None = None,
    extra_exclude_patterns: list[str] | None = None,
    scope: Path | None = None,
) -> list[Path]:
    """Return all in-repo Obsidian Bases (`.base`) files, skipping excluded dirs."""
    return _discover_files(
        repo_root, BASE_EXTENSIONS, extra_exclude_dirs, extra_exclude_patterns,
        scope=scope,
    )


def _vault_detected(repo_root: Path) -> bool:
    """True if the repo is, or contains, an Obsidian vault.

    A vault is rooted at the directory holding `.obsidian/`, but that root is
    not always the scan target: `/assess` scans from `git rev-parse
    --show-toplevel`, so a vault kept as a subdirectory of a git repo
    (`repo/notes/.obsidian/`) puts `.obsidian/` *below* repo_root. The old
    `repo_root/.obsidian` check only saw a vault rooted exactly at the scan
    target and reported `false` for the nested case - a false negative that
    silently disabled every downstream vault accommodation (#179).

    We therefore check repo_root and walk its subtree, pruning the same
    non-navigational trees the doc scan skips (`EXCLUDE_DIRS` - `node_modules`,
    `vendor`, ...) so a vendored or build-artifact `.obsidian/` can't trip a
    false positive, while a real vault is found wherever it sits in the repo.
    """
    repo_root = repo_root.resolve()
    if (repo_root / ".obsidian").is_dir():
        return True
    for _dirpath, dirnames, _filenames in os.walk(repo_root):
        if ".obsidian" in dirnames:
            return True
        # Prune heavy / non-navigational subtrees from the descent. `.obsidian`
        # is itself in EXCLUDE_DIRS, but we've already matched it above before
        # pruning, so it never gets removed out from under the search.
        dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS]
    return False


def _obsidiantools_available() -> bool:
    try:  # optional accelerator; never required.
        import obsidiantools  # noqa: F401 - imported only to probe availability

        return True
    except ImportError:
        return False


def _cited_excluded_doc(
    rel_path: str, repo_root: Path, tracked: frozenset[Path] | None, scope: Path | None,
    extra_dirs: set[str], extra_pats: list[str],
) -> Path | None:
    """The `.claude/` doc at `rel_path`, if a reference may bring it in.

    `.claude` stays in `EXCLUDE_DIRS` for the walk, so an uncited agent file is
    never a node; a cited one is navigation an agent follows and joins the
    graph. Every other exclusion (built-in, user, untracked, out of scope)
    still applies.
    """
    from lib.assess_config import is_user_excluded
    parts = Path(rel_path).parts
    if ".claude" not in parts or ".." in parts:
        return None
    if Path(rel_path).suffix.lower() not in DOC_EXTENSIONS:
        return None
    if is_excluded_path(Path(*[x for x in parts if x != ".claude"])):
        return None
    if is_user_excluded(Path(rel_path), extra_dirs, extra_pats):
        return None
    path = repo_root / rel_path
    if not path.is_file() or not is_repo_file(path, repo_root, tracked):
        return None
    if scope is not None and not path.resolve().is_relative_to(scope.resolve()):
        return None
    return path.resolve()


def _reference_paths(text: str, source_rel: str) -> list[tuple[str, str]]:
    """Doc paths named by backticked tokens outside fences, as
    `(raw_ref, doc_relative_candidate)` pairs, in document order.

    Reuses the ownership parser's path-token rules. A span that holds link
    syntax (`[[x]]`, `[x](y)`) is a teaching sample, not a citation, so it is
    skipped here just as the link pass strips it.
    """
    from lib.ownership_parser import _extract_path_refs
    out: list[tuple[str, str]] = []
    for m in INLINE_CODE_RE.finditer(strip_fenced_lines(text)):
        span = m.group(0)
        if "[[" in span or "](" in span:
            continue
        for ref in sorted(_extract_path_refs(span, tuple(DOC_EXTENSIONS))):
            if Path(ref).suffix.lower() not in DOC_EXTENSIONS:
                continue
            local = ref.lstrip("/") if ref.startswith("/") else posixpath.normpath(
                posixpath.join(posixpath.dirname(source_rel), ref))
            out.append((ref, local))
    return out


def _read_doc(path: Path) -> str | None:
    """A doc's text, or None when it cannot be read (Layer 0 is best-effort)."""
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None


def _basename_index(rels: Iterable[str]) -> dict[str, list[str]]:
    """Repo-relative doc paths grouped by basename, built once per graph."""
    out: dict[str, list[str]] = {}
    for r in rels:
        out.setdefault(posixpath.basename(r), []).append(r)
    return out


def _resolve_references(
    text: str, source_rel: str, repo_root: Path,
    doc_by_rel: dict[str, Path], doc_rels: set[str], by_basename: dict[str, list[str]],
    cite: Callable[[str], Path | None],
) -> list[Path]:
    """Docs named by backticked paths in `text`, exact paths before guesses:
    the doc-relative path (walked doc or cited `.claude/` doc), then the
    ownership parser's resolver over the walked docs (repo-root path or a
    basename that names exactly one doc), then a cited `.claude/` doc at the
    literal path. `cite` is `_cited_excluded_doc` bound to the run's excludes.

    `doc_by_rel` / `doc_rels` hold the walked docs only and never grow, so a
    doc's references depend on its own text and the walk, not on read order.
    """
    from lib.ownership_parser import _resolve_ref
    found: list[Path] = []
    for ref, local in _reference_paths(text, source_rel):
        hit = doc_by_rel.get(local) or cite(local)
        if hit is None:
            # A bare basename reads the prebuilt index instead of the resolver's
            # per-call sweep of every doc; a path (or a root-level exact name,
            # which the resolver prefers over a basename match) goes through it.
            hits = (set(by_basename.get(ref, [])) if "/" not in ref and ref not in doc_rels
                    else {str(x) for x in _resolve_ref(ref, repo_root, doc_rels)})
            hit = doc_by_rel[next(iter(hits))] if len(hits) == 1 else cite(ref.lstrip("/"))
        if hit is not None:
            found.append(hit)
    return found


def _settle_references(
    docs: list[Path], texts: dict[Path, str], rel: RelFn,
    resolve: Callable[[str, str], list[Path]],
) -> list[tuple[Path, Path]]:
    """First pass: read every doc into `texts` and resolve its reference edges.

    A cited `.claude/` doc is appended to `docs` and read in turn, so the link
    pass that follows sees the final doc set (and name index) whatever order
    the walk produced. Returns `(source, target)` pairs, self-citations dropped.
    """
    seen = set(docs)
    pairs: list[tuple[Path, Path]] = []
    for d in docs:  # grows while iterating: cited .claude docs join the queue
        text = _read_doc(d)
        if text is None:
            continue
        texts[d] = text
        for tgt in resolve(text, rel(d)):
            if tgt not in seen:
                seen.add(tgt)
                docs.append(tgt)
            if tgt != d:
                pairs.append((d, tgt))
    return pairs


def _missing_xrefs(
    docs: list[Path], texts: dict[Path, str], graph: nx.DiGraph, repo_root: Path, rel: RelFn,
) -> list[MissingXref]:
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
    out: list[MissingXref] = []
    for d in docs:
        text = texts.get(d)
        if not text:
            continue
        body = strip_fenced_lines(text)
        seen: set[Path] = set()
        for m in pattern.finditer(body):
            t = name_to_doc.get(m.group(1).lower())
            if t is None or t == d or t in seen:
                continue
            seen.add(t)
            if (rel(d), rel(t)) not in edges:  # already linked -> not missing
                out.append({"from": rel(d), "to": rel(t)})
    return out


def _settle_reference_pairs(
    docs: list[Path], texts: dict[Path, str], rel: RelFn, repo_root: Path,
    scope: Path | None, extra_exclude_dirs: set[str] | None,
    extra_exclude_patterns: list[str] | None,
) -> list[tuple[Path, Path]]:
    """Reference edges (issue #353) settle first: a backticked token naming an
    existing doc. A cited `.claude/` doc joins `docs` here, before the name
    index and the link pass, so links and wikilinks reach it from any doc."""
    doc_by_rel = {rel(x): x for x in docs}
    cite = partial(
        _cited_excluded_doc, repo_root=repo_root, tracked=tracked_files(repo_root),
        scope=scope, extra_dirs=extra_exclude_dirs or set(),
        extra_pats=extra_exclude_patterns or [],
    )
    return _settle_references(docs, texts, rel, partial(
        _resolve_references, repo_root=repo_root, doc_by_rel=doc_by_rel,
        doc_rels=set(doc_by_rel), by_basename=_basename_index(doc_by_rel), cite=cite,
    ))


def _excluded_layers(
    graph: nx.DiGraph, docs: list[Path], repo_root: Path, rel: RelFn, base_hubs: list[str],
    machine_links: dict[str, int], working_notes_dirs: list[str] | None,
    working_notes_ignore: list[str] | None,
) -> tuple[set[str], list[SourceTree], set[str], list[SourceTree]]:
    """Raw-source and working-notes trees, detected on the final graph.

    Raw-source-tree exclusion (issue #225). Detect subtrees of raw,
    machine-extracted source documents - link-isolated and carrying the
    machine-extraction fingerprint - and exclude them from the headline
    read-side metrics so the curated-wiki signal isn't drowned. Detection runs
    on the *final* graph (after vault edges), so a doc made navigable by a
    `.base` hub or dataview query is not misread as raw.
    """
    raw_docs, raw_trees = _detect_raw_trees(
        graph, docs, repo_root, rel, base_hubs, machine_links,
    )
    # Working-notes trees (issue #366) are the second fingerprint, detected on
    # what the raw pass leaves so no doc belongs to both layers.
    notes_docs, notes_trees = _detect_working_notes_trees(
        graph, {rel(d) for d in docs} - raw_docs,
        force=working_notes_dirs or [], ignore=working_notes_ignore or [],
    )
    return raw_docs, raw_trees, notes_docs, notes_trees


def _curated_result(
    graph: nx.DiGraph, docs: list[Path], repo_root: Path, rel: RelFn, harvest: LinkHarvest,
    missing: list[MissingXref], excluded_docs: set[str], vault: bool, obs: bool,
    base_hubs: list[str],
) -> DocGraphResult:
    """Derive the headline and link-only signals over the curated layer
    (the graph minus excluded docs)."""
    curated_docs = [d for d in docs if rel(d) not in excluded_docs]
    curated_nodes = [n for n in graph.nodes() if n not in excluded_docs]
    curated_graph = graph.subgraph(curated_nodes).copy()
    curated_broken = [b for b in harvest.broken if b.get("from") not in excluded_docs]
    curated_missing = [
        mx for mx in missing
        if mx.get("from") not in excluded_docs and mx.get("to") not in excluded_docs
    ]

    result = _derive_signals(
        graph=curated_graph, docs=curated_docs, repo_root=repo_root, rel=rel,
        doc_to_code=harvest.doc_to_code, dangling=len(curated_broken),
        ambiguous=harvest.ambiguous, vault=vault, obs=obs, base_hubs=base_hubs,
    )
    link_graph = nx.DiGraph()
    link_graph.add_nodes_from(curated_graph)
    link_graph.add_edges_from(
        (u, v) for u, v, k in curated_graph.edges(data="kind") if k != "reference"
    )
    link_only = _derive_signals(
        graph=link_graph, docs=curated_docs, repo_root=repo_root, rel=rel,
        doc_to_code=harvest.doc_to_code, dangling=0, ambiguous=0,
        vault=vault, obs=obs, base_hubs=base_hubs, entries=result.entry_points,
    )
    result.link_only_orphan_rate = link_only.orphan_rate
    result.link_only_reachability_pct = link_only.reachability_pct
    result.link_parents = _link_parents(link_graph, result.entry_points)
    result.broken_links = curated_broken[:MAX_BROKEN_LINKS]
    result.missing_xrefs = curated_missing[:MAX_MISSING_XREFS]
    rows = _directory_breakdown(curated_nodes, result.unreachable, curated_broken)
    result.directory_breakdown = rows[:MAX_DIRECTORY_BREAKDOWN]
    result.directory_count = len(rows)
    return result


def build_doc_graph(
    repo_root: Path, doc_files: list[Path] | None = None,
    extra_exclude_dirs: set[str] | None = None,
    extra_exclude_patterns: list[str] | None = None,
    scope: Path | None = None,
    working_notes_dirs: list[str] | None = None,
    working_notes_ignore: list[str] | None = None,
) -> DocGraphResult:
    """Parse docs, build the link graph, and derive navigability signals.

    `scope` (an absolute path under `repo_root`) restricts the graph to docs
    within a subtree for `/assess <path>` monorepo scoping; omit it for a
    whole-repo run. `.base` hub discovery honours the same scope so a scoped
    graph carries no navigation signal from a sibling directory.
    `working_notes_dirs` / `working_notes_ignore` are the `.assess/config.toml`
    overrides (`lib.assess_config.load_working_notes_config`) that force or
    suppress working-notes classification for repo-relative directories.

    Phases, in order: reference edges settle (and may add cited `.claude/`
    docs), the link pass adds link edges, reference edges fill the pairs no
    link covers, vault-native edges join, the raw-source and working-notes
    layers are detected on that final graph, and the signals are derived over
    the curated remainder.
    """
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
            scope=scope,
        )
    )
    docs = [d.resolve() for d in docs]
    if not docs:
        return DocGraphResult(
            available=True, reason="no markdown docs found", doc_count=0,
            vault_detected=vault, obsidiantools_available=obs,
        )

    def rel(p: Path) -> str:
        return str(p.relative_to(repo_root))

    texts: dict[Path, str] = {}
    discovered = list(docs)  # the walked set; `docs` grows with cited .claude docs
    ref_pairs = _settle_reference_pairs(
        docs, texts, rel, repo_root, scope, extra_exclude_dirs, extra_exclude_patterns,
    )

    graph = nx.DiGraph()
    for d in docs:
        graph.add_node(rel(d))
    harvest = harvest_links(
        docs, texts, repo_root, graph, rel,
        doc_exts=DOC_EXTENSIONS, code_exts=CODE_EXTENSIONS,
    )
    # A link between the same pair keeps kind link.
    graph.add_edges_from([
        (rel(src), rel(tgt)) for src, tgt in ref_pairs
        if not graph.has_edge(rel(src), rel(tgt))
    ], kind="reference")

    missing = _missing_xrefs(discovered, texts, graph, repo_root, rel)

    # Vault-native navigation: `.base` view hubs + ```dataview``` query blocks
    # surface notes dynamically, so a static-link-only graph scores a navigable
    # vault as orphaned (issue #176). Recognise those query hubs as edge sources.
    base_hubs = _apply_vault_edges(
        graph, repo_root, docs, texts, rel,
        extra_exclude_dirs=extra_exclude_dirs,
        extra_exclude_patterns=extra_exclude_patterns,
        scope=scope,
    )

    raw_docs, raw_trees, notes_docs, notes_trees = _excluded_layers(
        graph, docs, repo_root, rel, base_hubs, harvest.machine_links,
        working_notes_dirs, working_notes_ignore,
    )
    result = _curated_result(
        graph, docs, repo_root, rel, harvest, missing,
        raw_docs | notes_docs, vault, obs, base_hubs,
    )

    # Excluded-layer figures, reported separately so the exclusion stays legible.
    result.curated_doc_count = result.doc_count
    (result.excluded_raw_trees, result.raw_source_doc_count,
     result.raw_source_orphan_rate, result.raw_source_broken_links,
     ) = _layer_figures(graph, harvest.broken, raw_docs, raw_trees)
    (result.excluded_working_notes_trees, result.working_notes_doc_count,
     result.working_notes_orphan_rate, result.working_notes_broken_links,
     ) = _layer_figures(graph, harvest.broken, notes_docs, notes_trees)
    return result


def _link_parents(graph: nx.DiGraph, entry_points: list[str]) -> list[LinkParent]:
    """One ``{path, link_parent, link_entry}`` record per node in ``graph``.

    ``graph`` is the link-only subgraph, so a doc reachable only over a
    ``reference`` edge (a backticked doc path) has no link path and carries
    ``null`` for both fields - which is also what a doc nothing reaches carries.
    An entry carries ``link_parent`` ``None`` and ``link_entry`` equal to its own
    path, so the three states stay distinguishable.

    A consumer will draw a link-path strip from it, so a rebuild must be
    byte-identical. That comes from the ordering: seeds are ``entry_points`` in
    their exported ascending byte order, and within a walk the frontier and each
    node's successors are taken in ascending byte order. First write wins, and
    the recorded set is the visited set - a doc already recorded is never
    enqueued again, so an entry met as a successor is not expanded by the
    walking entry and never lends its name to another entry's children.

    Each seed's walk runs to exhaustion before the next starts. That is an
    attribution rule, not a determinism one: a doc belongs to the first entry,
    in entry order, that reaches it at all, so a later entry gaining a shorter
    link does not re-attribute it. The cost is that the recorded path is the
    shortest from its own entry, not the shortest from any entry.
    """
    nodes = set(graph.nodes())
    # (link_parent, link_entry), keyed by path. Entries land before any walk so
    # one entry linking another cannot overwrite it.
    recorded: dict[str, tuple[str | None, str | None]] = {
        e: (None, e) for e in entry_points if e in nodes
    }
    for entry in entry_points:
        if entry not in nodes:
            continue
        frontier = [entry]
        while frontier:
            reached: list[str] = []
            for node in frontier:
                for succ in sorted(graph.successors(node)):
                    if succ in recorded:
                        continue
                    recorded[succ] = (node, entry)
                    reached.append(succ)
            frontier = sorted(reached)
    return [
        {"path": n,
         "link_parent": recorded.get(n, (None, None))[0],
         "link_entry": recorded.get(n, (None, None))[1]}
        for n in sorted(nodes)
    ]


def _top_dir(rel_path: str) -> str:
    """First path segment of a doc's rel path; root-level docs key as ``.``."""
    head, sep, _ = rel_path.partition("/")
    return head if sep else "."


def _directory_breakdown(
    nodes: Iterable[str], unreachable: list[str], broken: list[BrokenLink],
) -> list[DirectoryBreakdownRow]:
    """Doc, unreachable and broken-link counts per top-level directory, largest
    gap first. A broken link counts toward the directory of the doc it is
    written in (``from``)."""
    counts: dict[str, list[int]] = {}
    for n in nodes:
        counts.setdefault(_top_dir(n), [0, 0, 0])[0] += 1
    for n in unreachable:
        counts[_top_dir(n)][1] += 1
    for b in broken:
        counts.setdefault(_top_dir(b.get("from", "")), [0, 0, 0])[2] += 1
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1][1], -kv[1][2], -kv[1][0], kv[0]))
    return [
        {"path": d, "doc_count": c[0], "unreachable_count": c[1], "broken_link_count": c[2]}
        for d, c in ranked
    ]


def _layer_figures(
    graph: nx.DiGraph, broken: list[BrokenLink], layer_docs: set[str], trees: list[SourceTree],
) -> tuple[list[ExcludedTree], int, float, int]:
    """An excluded layer's own figures: its trees as ``{path, file_count}``,
    doc count, orphan rate over the full graph, and broken links it holds."""
    in_deg = dict(graph.in_degree())
    n = len(layer_docs)
    orphans = sum(1 for r in layer_docs if in_deg.get(r, 0) == 0)
    return (
        [{"path": t["path"], "file_count": t["file_count"]} for t in trees],
        n,
        (orphans / n) if n else 0.0,
        sum(1 for b in broken if b.get("from") in layer_docs),
    )


def _detect_working_notes_trees(
    graph: nx.DiGraph, doc_rels: set[str], *, force: list[str], ignore: list[str],
) -> tuple[set[str], list[SourceTree]]:
    """Detect working-notes subtrees and return (excluded_doc_rels, trees).

    ``doc_rels`` is the doc set minus raw-source docs; like the raw pass it
    never classifies a non-doc node (a ``.base`` hub). Signals come from the
    headline graph (link and reference edges) restricted to those docs: each doc's in-degree and the docs its inbound
    edges come from, so the classifier can tell one index holding the links
    from a wiki whose links are spread out. The verdict is
    ``lib.raw_source.classify_working_notes_trees``; ``force`` / ``ignore``
    are the config overrides, passed through.
    """
    from lib.raw_source import classify_working_notes_trees

    signals: dict[str, DocSignal] = {}
    for r in sorted(doc_rels):
        sources = [u for u in graph.predecessors(r) if u in doc_rels]
        signals[r] = {"in_degree": len(sources), "inbound_sources": sources}
    trees = classify_working_notes_trees(signals, force=force, ignore=ignore)
    return {r for t in trees for r in t["docs"]}, trees


def _detect_raw_trees(
    graph: nx.DiGraph, docs: list[Path], repo_root: Path, rel: RelFn, base_hubs: list[str],
    machine_links: dict[str, int],
) -> tuple[set[str], list[SourceTree]]:
    """Detect raw-source subtrees and return (excluded_doc_rels, raw_trees).

    Assembles the per-doc graph signals (in/out degree from the final graph plus
    the machine-extraction fingerprint count) and delegates the threshold-based
    verdict to ``lib.raw_source.classify_raw_trees``. ``base_hubs`` and the
    README/MOC entries are passed as entry points so a doc reachable through a
    dynamic navigation surface is never counted toward a subtree's isolation.
    """
    from lib.raw_source import classify_raw_trees

    in_deg = dict(graph.in_degree())
    out_deg = dict(graph.out_degree())
    doc_rels = {rel(d) for d in docs}
    doc_signals: dict[str, DocSignal] = {
        r: {
            "in_degree": in_deg.get(r, 0),
            "out_degree": out_deg.get(r, 0),
            "machine_links": machine_links.get(r, 0),
        }
        for r in doc_rels
    }
    pagerank = _pagerank(graph)
    entries = set(_pick_entry_points(docs, repo_root, pagerank, rel, base_hubs))
    raw_trees = classify_raw_trees(doc_signals, entries=entries)
    excluded: set[str] = set()
    for tree in raw_trees:
        excluded.update(tree["docs"])
    return excluded, raw_trees


def _apply_vault_edges(
    graph: nx.DiGraph, repo_root: Path, docs: list[Path], texts: dict[Path, str], rel: RelFn,
    *, extra_exclude_dirs: set[str] | None = None,
    extra_exclude_patterns: list[str] | None = None,
    scope: Path | None = None,
) -> list[str]:
    """Add Obsidian Bases (`.base`) and Dataview query edges to `graph`.

    Mutates `graph` in place and returns the rel-paths of the `.base` hub nodes
    it added. A `.base` hub is a dynamic navigation surface (you open it to see
    its notes), so it is treated as an entry point for reachability. Dataview
    query blocks live inside existing notes, so their edges originate from the
    note that declares them - no new node.
    """
    from lib.vault_queries import (
        parse_base_queries,
        parse_dataview_queries,
        parse_frontmatter,
        select_notes,
    )

    doc_rels: list[tuple[Path, Path]] = [(d, d.relative_to(repo_root)) for d in docs]
    _fm_cache: dict[Path, dict[str, object]] = {}

    def frontmatter_of(d: Path) -> dict[str, object]:
        if d not in _fm_cache:
            _fm_cache[d] = parse_frontmatter(texts.get(d, ""))
        return _fm_cache[d]

    # Dataview blocks: edges from the declaring note to the notes it selects.
    for d in docs:
        text = texts.get(d)
        if not text:
            continue
        for query in parse_dataview_queries(text):
            for tgt in select_notes(query, doc_rels, frontmatter_of):
                if tgt != d:
                    graph.add_edge(rel(d), rel(tgt), kind="link")

    # `.base` hubs: a new hub node with edges to every note its query selects.
    base_hubs: list[str] = []
    for bf in discover_base_files(
        repo_root, extra_exclude_dirs, extra_exclude_patterns, scope=scope
    ):
        try:
            btext = bf.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        targets: set[Path] = set()
        for query in parse_base_queries(btext):
            targets |= select_notes(query, doc_rels, frontmatter_of)
        if not targets:  # a base that selects nothing is not a hub edge source
            continue
        hub = rel(bf)
        if hub not in graph:
            graph.add_node(hub)
        base_hubs.append(hub)
        for tgt in targets:
            graph.add_edge(hub, rel(tgt), kind="link")
    return base_hubs


def _pagerank(graph: nx.DiGraph, alpha: float = 0.85, max_iter: int = 100,
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
    docs: list[Path], repo_root: Path, pagerank: dict[str, float], rel: RelFn,
    base_hubs: list[str] | None = None,
) -> list[str]:
    """Entry roots for reachability: root-level README/AGENTS/CLAUDE/index, the
    single highest-PageRank declared MOC, plus any `.base` view hub (a dynamic
    navigation surface). Falls back to the top doc overall so reachability is
    always computable."""
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
    # `.base` hubs are real entries, so add them before the fallback - otherwise
    # the "no entries" fallback fires spuriously and crowns a random sink node.
    for hub in base_hubs or []:
        if hub not in entries:
            entries.append(hub)
    if not entries and pagerank:
        entries.append(max(pagerank, key=lambda k: pagerank[k]))
    return entries


def _derive_signals(
    *, graph: nx.DiGraph, docs: list[Path], repo_root: Path, rel: RelFn,
    doc_to_code: list[DocToCodeEdge], dangling: int, ambiguous: int,
    vault: bool, obs: bool, base_hubs: list[str] | None = None,
    entries: list[str] | None = None,
) -> DocGraphResult:
    nodes = list(graph.nodes())
    n = len(nodes)

    pagerank = _pagerank(graph)

    in_deg = dict(graph.in_degree())
    out_deg = dict(graph.out_degree())

    # `.base` hubs are dynamic navigation surfaces, so they seed reachability
    # alongside the README/AGENTS/MOC entry points (issue #176).
    # `entries` pins the roots (the link-only pass reuses the headline's, so the
    # two figures differ only in their edge set).
    entry_set = set(entries if entries is not None
                    else _pick_entry_points(docs, repo_root, pagerank, rel, base_hubs))

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
        (DocHub(path=x, pagerank=round(pagerank.get(x, 0.0), 4),
                out_degree=out_deg.get(x, 0), in_degree=in_deg.get(x, 0))
         for x in nodes),
        key=lambda h: (-h["pagerank"], -h["out_degree"], h["path"]),
    )[:10]

    declared: list[DeclaredMoc] = []
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

