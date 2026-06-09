"""Doc-staleness metric for Layer 0 (the decaying-map signal).

Absolute doc age is not the signal -- a two-year-old doc beside two-year-old
code is fine. The signal is a doc that has *frozen while its subject moves*: a
stale map of a churning module. So for every doc we compute three things:

  - ``last_commit_days``     -- days since the doc itself last changed
  - ``code_churn_in_window`` -- commits to the *code the doc describes*
  - ``ratio``                -- code churn per unit of doc maintenance
                                (``code_churn / max(doc_churn, 1)``); high = decaying map

Associating a doc with the code it describes uses the **nearest-ancestor
base-doc rule** (same nearest-match logic as ``CODEOWNERS`` / ``.gitignore``):
each code file is owned by the nearest base doc walking up its directory
ancestry, and a base doc's subject is its subtree down to the next base doc.
When co-location is absent we fall back, in order, to a parallel ``docs/`` tree,
the doc's explicit code links, then repo-wide churn. The method used is reported
per doc so the limits are auditable.

Churn comes from ``lib.git_churn`` -- the same machinery the complexity treemap
uses, so churn is computed one way across the whole skill.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from lib.doc_graph import (
    CODE_EXTENSIONS,
    DOC_EXTENSIONS,
    is_excluded_path,
    is_repo_file,
)
from lib.doc_provenance import resolve_doc_sources, source_is_newer
from lib.git_churn import (
    churn_is_degenerate,
    file_last_commit_days,
    pick_churn_window,
    tracked_files,
)


# Docs that describe the directory they live in. Precedence (best first) when a
# directory holds more than one candidate.
BASE_DOC_PRECEDENCE = ["readme.md", "index.md", "_index.md", "agents.md", "claude.md"]
# Boilerplate that names a directory but does not *describe* its code.
BOILERPLATE_BASENAMES = {
    "license.md", "license", "changelog.md", "contributing.md",
    "code_of_conduct.md", "security.md", "notice.md", "authors.md",
}
# A repo at or above this many hand-written code files is "large" enough that
# missing modular base docs is a navigability gap rather than needless overhead.
LARGE_REPO_CODE_FILES = 40


@dataclass
class DocStaleness:
    path: str
    last_commit_days: int | None
    doc_churn_in_window: int
    code_churn_in_window: int
    subject_code_count: int
    subject_method: str
    ratio: float
    # Provenance (generated docs only; see lib.doc_provenance). When a doc
    # declares a source, staleness is measured against that source instead of
    # the doc's own age/churn: `provenance_method` names how it was declared
    # ("frontmatter"/"config"), `provenance_sources` are the resolved source rel
    # paths, and `source_newer` is True iff a source has changed more recently
    # than the doc. All None/empty for an ordinary hand-written doc.
    provenance_method: str = ""
    provenance_sources: tuple[str, ...] = ()
    provenance_generated_by: str | None = None
    source_newer: bool | None = None

    @property
    def confidence(self) -> str:
        # repo-baseline uses repo-wide churn (no derivable subject), so a
        # stale-ratio computed against it is a coarse proxy. Mark it low so a
        # reader knows to discount before acting on the ranking.
        return "low" if self.subject_method == "repo-baseline" else "high"

    def as_dict(self) -> dict:
        d: dict = {
            "path": self.path,
            "last_commit_days": self.last_commit_days,
            "doc_churn_in_window": self.doc_churn_in_window,
            "code_churn_in_window": self.code_churn_in_window,
            "subject_code_count": self.subject_code_count,
            "subject_method": self.subject_method,
            "ratio": round(self.ratio, 2),
            "confidence": self.confidence,
        }
        if self.provenance_method:
            d["provenance"] = {
                "method": self.provenance_method,
                "sources": list(self.provenance_sources),
                "generated_by": self.provenance_generated_by,
                "source_newer": self.source_newer,
            }
        return d


def _discover(repo_root: Path, exts: set[str],
              extra_exclude_dirs: set[str] | None = None,
              extra_exclude_patterns: list[str] | None = None) -> list[Path]:
    """In-repo files with the given extensions (tracked + within repo only)."""
    from lib.assess_config import is_user_excluded
    repo_root = repo_root.resolve()
    tracked = tracked_files(repo_root)
    extra_dirs = extra_exclude_dirs or set()
    extra_pats = extra_exclude_patterns or []
    files: list[Path] = []
    for path in repo_root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in exts:
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
        files.append(path)
    return sorted(files)


def discover_code_files(repo_root: Path,
                        extra_exclude_dirs: set[str] | None = None,
                        extra_exclude_patterns: list[str] | None = None,
                        ) -> list[Path]:
    return _discover(
        repo_root, CODE_EXTENSIONS,
        extra_exclude_dirs=extra_exclude_dirs,
        extra_exclude_patterns=extra_exclude_patterns,
    )


def discover_doc_files(repo_root: Path,
                       extra_exclude_dirs: set[str] | None = None,
                       extra_exclude_patterns: list[str] | None = None,
                       ) -> list[Path]:
    return _discover(
        repo_root, DOC_EXTENSIONS,
        extra_exclude_dirs=extra_exclude_dirs,
        extra_exclude_patterns=extra_exclude_patterns,
    )


def _safe_rel(path: Path, repo_root: Path) -> str:
    """Repo-relative path string, falling back to the absolute path when the
    target lies outside the repo root (a provenance source can resolve via the
    doc's own directory to a sibling tree)."""
    try:
        return str(path.relative_to(repo_root))
    except ValueError:
        return str(path)


def _is_base_doc(doc: Path) -> bool:
    """True if `doc` describes the directory it lives in (a base doc)."""
    name = doc.name.lower()
    if name in BOILERPLATE_BASENAMES:
        return False
    if name in BASE_DOC_PRECEDENCE:
        return True
    # `<dir>.md` convention: a doc named after its own parent directory.
    if doc.stem.lower() == doc.parent.name.lower():
        return True
    # MOC notes describe a cluster, so they act as base docs too.
    from lib.doc_graph import _is_declared_moc

    return _is_declared_moc(doc)


def _base_doc_for_dir(directory: Path, docs_in_dir: list[Path]) -> Path | None:
    """Pick the single base doc representing `directory` by precedence."""
    base = [d for d in docs_in_dir if _is_base_doc(d)]
    if not base:
        return None
    def rank(d: Path) -> int:
        name = d.name.lower()
        return BASE_DOC_PRECEDENCE.index(name) if name in BASE_DOC_PRECEDENCE else len(BASE_DOC_PRECEDENCE)
    return sorted(base, key=lambda d: (rank(d), str(d)))[0]


def _build_base_doc_dirs(repo_root: Path, docs: list[Path]) -> dict[Path, Path]:
    """Map directory -> its base doc, for every directory that has one."""
    by_dir: dict[Path, list[Path]] = {}
    for d in docs:
        by_dir.setdefault(d.parent, []).append(d)
    result: dict[Path, Path] = {}
    for directory, dir_docs in by_dir.items():
        base = _base_doc_for_dir(directory, dir_docs)
        if base is not None:
            result[directory] = base
    return result


def _nearest_base_doc(code_file: Path, base_doc_dirs: dict[Path, Path], repo_root: Path) -> Path | None:
    """Walk up from the code file's directory; return the nearest base doc."""
    current = code_file.parent
    while True:
        if current in base_doc_dirs:
            return base_doc_dirs[current]
        if current == repo_root or current.parent == current:
            return None
        if repo_root not in current.parents and current != repo_root:
            return None
        current = current.parent


def _parallel_docs_subject(
    doc: Path, repo_root: Path, code_dirs: set[Path],
) -> list[Path] | None:
    """Fallback (b): a doc under a `docs/` tree mapping to a code dir by name.

    `docs/payments.md` (or `docs/payments/index.md`) -> the `payments` code dir.
    """
    rel = doc.relative_to(repo_root)
    if "docs" not in {p.lower() for p in rel.parts}:
        return None
    candidates = {doc.stem.lower(), doc.parent.name.lower()}
    matches = [d for d in code_dirs if d.name.lower() in candidates]
    if not matches:
        return None
    # Prefer the shallowest matching code dir for determinism.
    return sorted(matches, key=lambda d: (len(d.parts), str(d)))[:1]


def analyze_doc_staleness(
    repo_root: Path,
    doc_files: list[Path] | None = None,
    doc_to_code_edges: list[dict] | None = None,
    extra_exclude_dirs: set[str] | None = None,
    extra_exclude_patterns: list[str] | None = None,
    generated_sources: list[tuple[str, list[str]]] | None = None,
) -> dict:
    """Compute the doc-staleness metric and doc->code association summary.

    ``generated_sources`` is the ``[[generated]]`` folder->source map (issue
    #178). When None it is read from ``.assess/config.toml``; pass an explicit
    list to override (tests, or a caller that already loaded the config).
    """
    repo_root = repo_root.resolve()
    if generated_sources is None:
        from lib.assess_config import load_generated_sources
        generated_sources = load_generated_sources(repo_root)
    docs = [
        d.resolve() for d in (
            doc_files if doc_files is not None
            else discover_doc_files(
                repo_root,
                extra_exclude_dirs=extra_exclude_dirs,
                extra_exclude_patterns=extra_exclude_patterns,
            )
        )
    ]
    code_files = discover_code_files(
        repo_root,
        extra_exclude_dirs=extra_exclude_dirs,
        extra_exclude_patterns=extra_exclude_patterns,
    )

    def rel(p: Path) -> str:
        return str(p.relative_to(repo_root))

    # Churn: pick a window over the code files (the subject we care about), then
    # score both docs and code in that window for the ratio.
    all_paths = code_files + docs
    churn_map, churn_label = pick_churn_window(repo_root, all_paths)
    if churn_map is None:
        churn_map = {}
        churn_label = None

    # Is the churn measurement itself trustworthy? A degenerate history (shallow
    # clone, fresh import, squashed/extracted tree) shows ~1 commit per file, so
    # `code_churn_in_window` swells to the file count and inflates every ratio
    # below. We measure degeneracy over the *code* distribution (the subject the
    # ratio's numerator sums) and surface it as the single source of truth other
    # consumers read - the doc->complexity join caps confidence, the keyhole
    # summary drops churn-derived findings, the report carries a snapshot caveat.
    churn_degenerate = churn_is_degenerate(
        churn_map.get(c, 0) for c in code_files
    )

    base_doc_dirs = _build_base_doc_dirs(repo_root, docs)
    code_dirs = {c.parent for c in code_files}

    # Explicit doc->code links (fallback c): doc rel -> [code abs paths].
    explicit: dict[str, list[Path]] = {}
    for edge in (doc_to_code_edges or []):
        code_abs = (repo_root / edge["code"]).resolve()
        explicit.setdefault(edge["doc"], []).append(code_abs)

    # Nearest-ancestor ownership: code file -> owning base doc.
    code_owner: dict[Path, Path] = {}
    for c in code_files:
        owner = _nearest_base_doc(c, base_doc_dirs, repo_root)
        if owner is not None:
            code_owner[c] = owner
    # Invert: base doc -> the code subtree it owns (down to the next base doc).
    owned_by_doc: dict[Path, list[Path]] = {}
    for code, owner in code_owner.items():
        owned_by_doc.setdefault(owner, []).append(code)

    repo_wide_code_churn = sum(churn_map.get(c, 0) for c in code_files)

    results: list[DocStaleness] = []
    method_counts: dict[str, int] = {}
    docs_mapping_to_code = 0

    # Association precedence (per the PRD's ordered fallbacks): co-located base
    # doc (nearest-ancestor) -> a parallel docs/ tree -> the doc's explicit code
    # links -> repo-wide churn baseline.
    for d in docs:
        subject: list[Path]
        method: str
        if d in owned_by_doc:
            subject = owned_by_doc[d]
            method = "nearest-ancestor"
        elif (par := _parallel_docs_subject(d, repo_root, code_dirs)) is not None:
            subject = [c for c in code_files if any(sd in c.parents for sd in par)]
            method = "parallel-docs-tree"
        elif explicit.get(rel(d)):
            subject = explicit[rel(d)]
            method = "explicit-links"
        else:
            subject = []
            method = "repo-baseline"

        if method != "repo-baseline":
            docs_mapping_to_code += 1
            code_churn = sum(churn_map.get(c, 0) for c in subject)
            subject_count = len(subject)
        else:
            # repo-baseline has no derivable subject, so the ratio uses
            # repo-wide churn - a coarse proxy. In an active repo this can be
            # high even for a freshly-written floating doc, so `ratio` alone
            # over-flags here. `last_commit_days` is the corrective signal (the
            # heatmap colours by staleness, and a floating doc won't be a graph
            # hub, so its stale_hubs priority stays low). Read ratio together
            # with subject_method and last_commit_days, not on its own.
            code_churn = repo_wide_code_churn
            subject_count = len(code_files)

        method_counts[method] = method_counts.get(method, 0) + 1
        doc_churn = churn_map.get(d, 0)
        ratio = code_churn / max(doc_churn, 1)

        # Provenance (issue #178): a *generated* doc that declares a source is
        # measured against that source, not its own age/churn. When the source
        # has NOT moved on, the doc provably matches its source, so its
        # decaying-map ratio is zero by construction - this is what keeps a
        # freshly-accurate generated doc out of the lying_map bucket regardless
        # of how busy the surrounding code is. When the source HAS moved on,
        # `source_newer` carries the staleness verdict for the join to sign
        # freshness directly; the churn ratio is left untouched as a secondary
        # signal.
        prov_sources, generated_by, prov_method = resolve_doc_sources(
            d, repo_root, generated_sources
        )
        src_newer: bool | None = None
        prov_source_rels: tuple[str, ...] = ()
        if prov_method:
            src_newer = source_is_newer(d, prov_sources)
            prov_source_rels = tuple(
                _safe_rel(s, repo_root) for s in prov_sources
            )
            if src_newer is False:
                ratio = 0.0

        results.append(DocStaleness(
            path=rel(d),
            last_commit_days=file_last_commit_days(d),
            doc_churn_in_window=doc_churn,
            code_churn_in_window=code_churn,
            subject_code_count=subject_count,
            subject_method=method,
            ratio=ratio,
            provenance_method=prov_method,
            provenance_sources=prov_source_rels,
            provenance_generated_by=generated_by,
            source_newer=src_newer,
        ))

    # Association-derivability is itself a Layer 0 signal.
    code_under_base = sum(1 for c in code_files if c in code_owner)
    pct_code_under_base = code_under_base / len(code_files) if code_files else 0.0
    pct_docs_mapping = docs_mapping_to_code / len(docs) if docs else 0.0

    # Modularity coverage is *size-weighted*: a 200-file service without a base
    # doc is a real navigability gap, a 3-file utility dir without one isn't.
    # Counting every code-containing directory equally (the un-weighted ratio
    # below) penalises nested internal dirs (`services/<x>/internal/`,
    # `adapters/persistence/`) the same as top-level service roots and pushes
    # the headline to near-zero on any non-trivial repo. The weighted ratio is
    # the fraction of *code* (by file count) sitting under a base doc - identical
    # to `pct_code_under_base_doc`. Both are reported so the denominator stays
    # auditable.
    module_dirs_with_base = len(base_doc_dirs)
    module_dir_count = len(code_dirs)
    base_doc_dir_ratio = module_dirs_with_base / module_dir_count if module_dir_count else 0.0
    # `pct_code_under_base` reaches 1.0 whenever a single root-level base doc
    # (a top README) is an ancestor of every code file - it does NOT mean every
    # module is documented. Reported as `base_doc_coverage_when_present` so it
    # is never misread as headline coverage; `base_doc_dir_ratio` (fraction of
    # code-containing dirs that actually hold a base doc) is the headline.
    base_doc_coverage_when_present = pct_code_under_base

    return {
        "available": True,
        "churn_window": churn_label,
        # Churn-measurement reliability, independent of doc->code association
        # precision. True = the window has no usable churn signal (every file ~1
        # commit), so any finding built on `code_churn_in_window` / `ratio` must
        # be discounted - see `lib.git_churn.churn_is_degenerate`.
        "churn_degenerate": churn_degenerate,
        "docs": [r.as_dict() for r in sorted(results, key=lambda r: -r.ratio)],
        "association": {
            "code_file_count": len(code_files),
            "doc_count": len(docs),
            "code_under_base_doc": code_under_base,
            "pct_code_under_base_doc": round(pct_code_under_base, 3),
            "docs_mapping_to_code": docs_mapping_to_code,
            "pct_docs_mapping_to_code": round(pct_docs_mapping, 3),
            "methods": method_counts,
        },
        "modularity": {
            "module_dir_count": module_dir_count,
            "module_dirs_with_base_doc": module_dirs_with_base,
            # Headline first: fraction of code-containing dirs with a base doc.
            "base_doc_dir_ratio": round(base_doc_dir_ratio, 3),
            "base_doc_coverage_when_present": round(base_doc_coverage_when_present, 3),
            "code_file_count": len(code_files),
            "large_repo": len(code_files) >= LARGE_REPO_CODE_FILES,
        },
    }
