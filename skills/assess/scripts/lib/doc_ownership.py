"""The one doc-to-code ownership rule (issues #424 and #555).

Which code does a document describe? ``lib.doc_staleness`` measures a doc's
staleness against that code, and ``lib.doc_complexity_join`` measures the
complexity the doc summarises over the same code. Both read it from here, so the
two halves of one ``lying_map`` verdict describe the same files.

The rule, in precedence order (the PRD's ordered fallbacks):

  1. ``nearest-ancestor`` -- each code file is owned by the nearest *base doc*
     walking up its directory ancestry (the ``CODEOWNERS`` / ``.gitignore``
     nearest-match rule). A directory has at most one base doc, picked by
     ``BASE_DOC_PRECEDENCE``, so two docs never tie for a directory: a
     ``README.md`` beside a ``design.md`` owns the directory's code. A skill's
     ``SKILL.md`` is a base doc, so it owns its skill directory's code (#614).
  2. ``parallel-docs-tree`` -- a doc under a ``docs/`` tree whose name matches
     a code directory describes every code file under that directory.
  3. ``explicit-links`` -- the code files the doc links to.
  4. ``repo-baseline`` -- no derivable subject (an empty one).

The rule is resolved in two steps. ``subject_spec`` decides, per doc, the method
and its *roots* (the doc's directory, the matched code directories, or the
linked files), plus the directory the doc is the base doc of (``base_dir``), as
repo-relative POSIX strings, the same keys ``complexity-stats.json`` uses. ``resolve_subjects`` then expands the specs over
any set of repo-relative code paths: doc staleness expands them over every
discovered code file, the complexity join over the scored files. The specs ride
on each ``doc_staleness.docs`` row as ``subject_roots`` and ``base_doc_dir``, so
the join applies the rule from the serialized block without a second copy of it.

Every base doc stays an ancestor in the walk, including one that owns no
discovered code (its method is then a fallback). The treemap scores languages
the doc-staleness discovery does not list, so a scored file under such a doc's
directory is still that doc's, never handed to a doc further up.

The one doc-to-code ownership rule (issues #424, #555), shared by `doc_staleness.py` and
`doc_complexity_join.py`. Precedence: the nearest-ancestor base doc (one per directory,
ranked by `BASE_DOC_PRECEDENCE`, so a `README.md` beside a `design.md` owns the
directory's code; a skill's `SKILL.md` is a base doc ranked just after `README.md`, so it
owns its skill's subtree, #614), then a parallel `docs/` tree, explicit code links, and the empty
`repo-baseline` subject. `subject_spec` records each doc's method and roots as
repo-relative POSIX keys (the keys `complexity-stats.json` uses, also under
`/assess <path>`); `resolve_subjects` expands the specs over any set of code paths.
Staleness expands them over every discovered code file and exports each row's
`subject_roots`, `base_doc_dir`, `subject_code_count` and up to `SUBJECT_PATHS_CAP`
sorted `subject_paths`; the join expands the same roots over the scored files, so the cap
never hides an owned file from it. Every base doc stays an ancestor in the walk, even one
that owns no discovered code, because the treemap scores languages the staleness
discovery does not list. Tests: `tests/test_doc_ownership.py`.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path, PurePosixPath
from typing import TypedDict

from lib.run_context_types import DocToCodeEdge

# Docs that describe the directory they live in. Precedence (best first) when a
# directory holds more than one candidate. A skill's ``SKILL.md`` is a base doc
# (issue #614): it describes the skill directory's scripts, so it owns that
# subtree's code. A ``README.md`` beside it still wins the directory.
BASE_DOC_PRECEDENCE = [
    "readme.md", "skill.md", "index.md", "_index.md", "agents.md", "claude.md",
]
# Boilerplate that names a directory but does not *describe* its code.
BOILERPLATE_BASENAMES = {
    "license.md", "license", "changelog.md", "contributing.md",
    "code_of_conduct.md", "security.md", "notice.md", "authors.md",
}
# A staleness row lists at most this many of its subject paths (sorted); the
# full count is ``subject_code_count``. Enough to spot-check a verdict without
# a root README listing every file in a large repository.
SUBJECT_PATHS_CAP = 20

NEAREST_ANCESTOR = "nearest-ancestor"
PARALLEL_DOCS_TREE = "parallel-docs-tree"
EXPLICIT_LINKS = "explicit-links"
REPO_BASELINE = "repo-baseline"


class SubjectSpec(TypedDict):
    """How one doc's subject is derived: the method and its repo-relative roots.

    ``roots`` is the doc's own directory (``""`` at the repo root) for
    ``nearest-ancestor``, the matched code directories for
    ``parallel-docs-tree``, the linked code files for ``explicit-links``, and
    empty for ``repo-baseline``.
    """

    method: str
    roots: list[str]
    base_dir: str | None


def rel_posix(path: Path, repo_root: Path) -> str:
    """Repo-relative POSIX key for `path` (``""`` for the root itself); the
    absolute POSIX path when it lies outside the repo."""
    try:
        rel = path.relative_to(repo_root).as_posix()
    except ValueError:
        return path.as_posix()
    return "" if rel == "." else rel


def is_base_doc(doc: Path) -> bool:
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


def base_doc_for_dir(docs_in_dir: list[Path]) -> Path | None:
    """Pick the single base doc representing a directory by precedence."""
    base = [d for d in docs_in_dir if is_base_doc(d)]
    if not base:
        return None
    def rank(d: Path) -> int:
        name = d.name.lower()
        return BASE_DOC_PRECEDENCE.index(name) if name in BASE_DOC_PRECEDENCE else len(BASE_DOC_PRECEDENCE)
    return sorted(base, key=lambda d: (rank(d), str(d)))[0]


def build_base_doc_dirs(docs: list[Path]) -> dict[Path, Path]:
    """Map directory -> its base doc, for every directory that has one."""
    by_dir: dict[Path, list[Path]] = {}
    for d in docs:
        by_dir.setdefault(d.parent, []).append(d)
    result: dict[Path, Path] = {}
    for directory, dir_docs in by_dir.items():
        base = base_doc_for_dir(dir_docs)
        if base is not None:
            result[directory] = base
    return result


def nearest_base_doc(code_file: Path, base_doc_dirs: dict[Path, Path], repo_root: Path) -> Path | None:
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


def code_ownership(
    code_files: list[Path], base_doc_dirs: dict[Path, Path], repo_root: Path,
) -> dict[Path, Path]:
    """Nearest-ancestor ownership: code file -> owning base doc."""
    code_owner: dict[Path, Path] = {}
    for c in code_files:
        owner = nearest_base_doc(c, base_doc_dirs, repo_root)
        if owner is not None:
            code_owner[c] = owner
    return code_owner


def parallel_docs_dirs(
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


def explicit_links(
    repo_root: Path, doc_to_code_edges: list[DocToCodeEdge] | None,
) -> dict[str, list[Path]]:
    """Explicit doc->code links (fallback c): doc rel -> [code abs paths]."""
    explicit: dict[str, list[Path]] = {}
    for edge in (doc_to_code_edges or []):
        code_abs = (repo_root / edge["code"]).resolve()
        explicit.setdefault(edge["doc"], []).append(code_abs)
    return explicit


def subject_spec(
    d: Path,
    doc_rel: str,
    repo_root: Path,
    owning_docs: set[Path],
    base_docs: set[Path],
    code_dirs: set[Path],
    explicit: dict[str, list[Path]],
) -> SubjectSpec:
    """The method that derives `d`'s subject, its repo-relative roots, and the
    directory it is the base doc of (None when it is not one).

    `owning_docs` is every base doc that owns at least one code file under the
    nearest-ancestor walk (the values of ``code_ownership``); `base_docs` is
    every directory's base doc (the values of ``build_base_doc_dirs``).
    """
    base_dir = rel_posix(d.parent, repo_root) if d in base_docs else None
    if d in owning_docs:
        return {"method": NEAREST_ANCESTOR, "roots": [rel_posix(d.parent, repo_root)],
                "base_dir": base_dir}
    par = parallel_docs_dirs(d, repo_root, code_dirs)
    if par is not None:
        return {"method": PARALLEL_DOCS_TREE,
                "roots": [rel_posix(p, repo_root) for p in par], "base_dir": base_dir}
    if explicit.get(doc_rel):
        return {"method": EXPLICIT_LINKS,
                "roots": sorted({rel_posix(p, repo_root) for p in explicit[doc_rel]}),
                "base_dir": base_dir}
    return {"method": REPO_BASELINE, "roots": [], "base_dir": base_dir}


def _under(code: str, root: str) -> bool:
    """True if `code` sits in directory `root` or below (``""`` is the repo)."""
    return root == "" or code.startswith(root + "/")


def _nearest_owner(code: str, ancestor_dirs: Mapping[str, str]) -> str | None:
    """The doc whose directory is the deepest ancestor of `code`, if any."""
    parent = PurePosixPath(code).parent
    for directory in (parent, *parent.parents):
        key = directory.as_posix()
        owner = ancestor_dirs.get("" if key == "." else key)
        if owner is not None:
            return owner
    return None


def resolve_subjects(
    code_paths: Iterable[str], specs: Mapping[str, SubjectSpec],
) -> dict[str, list[str]]:
    """Expand each doc's spec over `code_paths`: doc -> its sorted subject paths.

    Each code path belongs to its nearest base doc walking up (the code below a
    base doc's directory down to the next base doc). One directory has one base
    doc, so there is no tie to break. A parallel-docs or explicit-links doc adds
    the code its roots select. Over the discovered code files a base doc that
    owns code is a ``nearest-ancestor`` doc, so the two parts never mix there;
    over scored files the doc-staleness discovery missed, a fallback-method base
    doc still owns what sits under its directory. A doc whose spec is missing
    or unknown gets an empty subject.
    """
    paths = sorted(set(code_paths))
    ancestor_dirs: dict[str, str] = {}
    for doc, spec in specs.items():
        base_dir = spec.get("base_dir")
        if base_dir is None and spec["method"] == NEAREST_ANCESTOR and spec["roots"]:
            base_dir = spec["roots"][0]  # a row written before base_doc_dir
        if base_dir is not None:
            ancestor_dirs[base_dir] = doc
    subjects: dict[str, set[str]] = {doc: set() for doc in specs}
    if ancestor_dirs:
        for code in paths:
            owner = _nearest_owner(code, ancestor_dirs)
            if owner is not None:
                subjects[owner].add(code)
    for doc, spec in specs.items():
        roots = spec["roots"]
        if spec["method"] == PARALLEL_DOCS_TREE:
            subjects[doc].update(c for c in paths if any(_under(c, r) for r in roots))
        elif spec["method"] == EXPLICIT_LINKS:
            subjects[doc].update(set(paths) & set(roots))
    return {doc: sorted(codes) for doc, codes in subjects.items()}


def bounded_subject_paths(subject: list[str]) -> list[str]:
    """The first ``SUBJECT_PATHS_CAP`` subject paths, sorted."""
    return sorted(subject)[:SUBJECT_PATHS_CAP]
