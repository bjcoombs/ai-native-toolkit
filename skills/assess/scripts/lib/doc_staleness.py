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

from lib.doc_graph import CODE_EXTENSIONS, DOC_EXTENSIONS, EXCLUDE_DIRS
from lib.git_churn import file_last_commit_days, git_churn_scores, pick_churn_window


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

    def as_dict(self) -> dict:
        return {
            "path": self.path,
            "last_commit_days": self.last_commit_days,
            "doc_churn_in_window": self.doc_churn_in_window,
            "code_churn_in_window": self.code_churn_in_window,
            "subject_code_count": self.subject_code_count,
            "subject_method": self.subject_method,
            "ratio": round(self.ratio, 2),
        }


def discover_code_files(repo_root: Path) -> list[Path]:
    files: list[Path] = []
    for path in repo_root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in CODE_EXTENSIONS:
            continue
        try:
            rel = path.relative_to(repo_root)
        except ValueError:
            continue
        if any(part in EXCLUDE_DIRS for part in rel.parts):
            continue
        files.append(path)
    return sorted(files)


def discover_doc_files(repo_root: Path) -> list[Path]:
    files: list[Path] = []
    for path in repo_root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in DOC_EXTENSIONS:
            continue
        try:
            rel = path.relative_to(repo_root)
        except ValueError:
            continue
        if any(part in EXCLUDE_DIRS for part in rel.parts):
            continue
        files.append(path)
    return sorted(files)


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
) -> dict:
    """Compute the doc-staleness metric and doc->code association summary."""
    repo_root = repo_root.resolve()
    docs = [d.resolve() for d in (doc_files if doc_files is not None else discover_doc_files(repo_root))]
    code_files = discover_code_files(repo_root)

    def rel(p: Path) -> str:
        return str(p.relative_to(repo_root))

    # Churn: pick a window over the code files (the subject we care about), then
    # score both docs and code in that window for the ratio.
    all_paths = code_files + docs
    churn_map, churn_label = pick_churn_window(repo_root, all_paths)
    if churn_map is None:
        churn_map = {}
        churn_label = None

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

    for d in docs:
        subject: list[Path]
        method: str
        if d in owned_by_doc:
            subject = owned_by_doc[d]
            method = "nearest-ancestor"
        elif explicit.get(rel(d)):
            subject = explicit[rel(d)]
            method = "explicit-links"
        else:
            par = _parallel_docs_subject(d, repo_root, code_dirs)
            if par is not None:
                subject_dirs = par
                subject = [c for c in code_files if any(sd in c.parents for sd in subject_dirs)]
                method = "parallel-docs-tree"
            else:
                subject = []
                method = "repo-baseline"

        if method != "repo-baseline":
            docs_mapping_to_code += 1
            code_churn = sum(churn_map.get(c, 0) for c in subject)
            subject_count = len(subject)
        else:
            code_churn = repo_wide_code_churn
            subject_count = len(code_files)

        method_counts[method] = method_counts.get(method, 0) + 1
        doc_churn = churn_map.get(d, 0)
        ratio = code_churn / max(doc_churn, 1)
        results.append(DocStaleness(
            path=rel(d),
            last_commit_days=file_last_commit_days(d),
            doc_churn_in_window=doc_churn,
            code_churn_in_window=code_churn,
            subject_code_count=subject_count,
            subject_method=method,
            ratio=ratio,
        ))

    # Association-derivability is itself a Layer 0 signal.
    code_under_base = sum(1 for c in code_files if c in code_owner)
    pct_code_under_base = code_under_base / len(code_files) if code_files else 0.0
    pct_docs_mapping = docs_mapping_to_code / len(docs) if docs else 0.0

    module_dirs_with_base = len(base_doc_dirs)
    module_dir_count = len(code_dirs)
    base_doc_coverage = module_dirs_with_base / module_dir_count if module_dir_count else 0.0

    return {
        "available": True,
        "churn_window": churn_label,
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
            "base_doc_coverage": round(base_doc_coverage, 3),
            "code_file_count": len(code_files),
            "large_repo": len(code_files) >= LARGE_REPO_CODE_FILES,
        },
    }
