"""Doc-staleness metric for Layer 0 (the decaying-map signal).

Absolute doc age is not the signal -- a two-year-old doc beside two-year-old
code is fine. The signal is a doc that has *frozen while its subject moves*: a
stale map of a churning module. So for every doc we compute:

  - ``last_commit_days``     -- days since the doc's last content change: its
                                newest commit that is not a bulk mechanical
                                commit (see ``git_churn.content_commit_clock``)
  - ``code_churn_in_window`` -- file-commits to the *code the doc describes*
                                (one commit touching three subject files counts 3)
  - ``ratio``                -- how far the doc is behind its subject: the
                                smaller of ``window_ratio`` and
                                ``code_churn_since_doc_change``; high = decaying map
  - ``window_ratio``         -- code churn per unit of doc maintenance over
                                the whole window (``code_churn / max(doc_churn, 1)``)
  - ``code_churn_since_doc_change`` -- distinct commits to the subject authored
                                after the doc's last content change (0 = the
                                doc is current; one commit touching three
                                subject files counts 1)

The window ratio alone keys on how often the subject moved over months, so a
doc corrected this morning beside a busy module still reads as a lie. Capping
it by the churn since the doc's last content change makes the signal require
the doc to be *behind* its subject now: a doc edited after its subject's last
change has ratio 0, whatever the window ratio says. The cap applies only to a
doc that changed inside the window; one behind by every subject commit in it
keeps the uncapped ``window_ratio``. Both sides use author time
(``%at``), so a code commit authored before the doc fix but merged after it
(a long-lived branch, a rebase that keeps author dates) does not count as
behind - the same trade the content clock makes. Bulk mechanical commits are
skipped on the doc side only: a code-only sweep after a doc fix (a formatter
run, a licence-header pass over source files) counts in full on the code side,
which is the one route left by which a just-corrected doc can be re-flagged.

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

from bisect import bisect_right
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from lib.doc_graph import (
    CODE_EXTENSIONS,
    DOC_EXTENSIONS,
    is_excluded_path,
    is_repo_file,
)
from lib.doc_provenance import resolve_doc_sources, source_is_newer
from lib.git_churn import (
    CHURN_WINDOWS,
    GIT_TIMEOUT_SECONDS,
    ContentClock,
    churn_is_degenerate,
    content_commit_clock,
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
    # Whole-window ratio and the subject churn after the doc's last content
    # change; `ratio` is the smaller of the two (see the module docstring).
    # `code_churn_since_doc_change` is None when the doc has no commit or the
    # per-commit read failed, and `ratio` then falls back to `window_ratio`.
    window_ratio: float = 0.0
    code_churn_since_doc_change: int | None = None
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
    # "creation" when every commit touching the doc is a bulk commit, so
    # `last_commit_days` is the doc's creation date, not a content age (a doc
    # regenerated in bulk on every release). "content" otherwise.
    last_change_basis: str = "content"

    @property
    def confidence(self) -> str:
        # repo-baseline uses repo-wide churn (no derivable subject), so a
        # stale-ratio computed against it is a coarse proxy. Mark it low so a
        # reader knows to discount before acting on the ranking. A creation-date
        # fallback is low for the same reason: its age is not a content age.
        if self.subject_method == "repo-baseline" or self.last_change_basis == "creation":
            return "low"
        return "high"

    def as_dict(self) -> dict:
        d: dict = {
            "path": self.path,
            "last_commit_days": self.last_commit_days,
            "doc_churn_in_window": self.doc_churn_in_window,
            "code_churn_in_window": self.code_churn_in_window,
            "subject_code_count": self.subject_code_count,
            "subject_method": self.subject_method,
            "ratio": round(self.ratio, 2),
            "window_ratio": round(self.window_ratio, 2),
            "code_churn_since_doc_change": self.code_churn_since_doc_change,
            "confidence": self.confidence,
        }
        if self.last_change_basis != "content":
            d["last_change_basis"] = self.last_change_basis
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
              extra_exclude_patterns: list[str] | None = None,
              scope: Path | None = None) -> list[Path]:
    """In-repo files with the given extensions (tracked + within repo only).

    `scope` (an absolute path under `repo_root`) restricts discovery to a
    subtree for `/assess <path>` monorepo scoping; omit it for a whole-repo run.
    """
    from lib.assess_config import is_user_excluded
    repo_root = repo_root.resolve()
    scope_abs = scope.resolve() if scope is not None else None
    tracked = tracked_files(repo_root)
    extra_dirs = extra_exclude_dirs or set()
    extra_pats = extra_exclude_patterns or []
    files: list[Path] = []
    for path in repo_root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in exts:
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
        files.append(path)
    return sorted(files)


def discover_code_files(repo_root: Path,
                        extra_exclude_dirs: set[str] | None = None,
                        extra_exclude_patterns: list[str] | None = None,
                        scope: Path | None = None,
                        ) -> list[Path]:
    return _discover(
        repo_root, CODE_EXTENSIONS,
        extra_exclude_dirs=extra_exclude_dirs,
        extra_exclude_patterns=extra_exclude_patterns,
        scope=scope,
    )


def discover_doc_files(repo_root: Path,
                       extra_exclude_dirs: set[str] | None = None,
                       extra_exclude_patterns: list[str] | None = None,
                       scope: Path | None = None,
                       ) -> list[Path]:
    return _discover(
        repo_root, DOC_EXTENSIONS,
        extra_exclude_dirs=extra_exclude_dirs,
        extra_exclude_patterns=extra_exclude_patterns,
        scope=scope,
    )


def content_clock(repo_root: Path) -> ContentClock:
    """The repo's bulk-commit-aware last-change clock (issue #333).

    The bulk-share denominator is every doc under the built-in exclusions for
    the whole repo, independent of `/assess <path>` scope and user excludes, so
    the doc-staleness metric and the instruction grader agree on which commits
    are bulk. Both calls share one build per HEAD (see `_clock_at`).
    """
    import subprocess

    repo_root = repo_root.resolve()
    try:
        head = subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "HEAD"],
            capture_output=True, text=True, check=False, timeout=GIT_TIMEOUT_SECONDS,
        ).stdout.strip() or None
    except (FileNotFoundError, subprocess.TimeoutExpired):
        head = None
    return _clock_at(repo_root, head)


@lru_cache(maxsize=4)
def _clock_at(repo_root: Path, head: str | None) -> ContentClock:
    """Build the clock once per (repo, HEAD): doc discovery, rename map, git pass."""
    from lib.change_coupling import build_rename_map

    # A rename map that could not be read leaves renames unfollowed, so the scan
    # is not complete: a renamed doc whose only visible commit is a bulk rename
    # falls back to a creation date.
    rename_map = build_rename_map(repo_root)
    renames = tuple(sorted(rename_map.paths.items()))
    clock = content_commit_clock(
        repo_root, frozenset(discover_doc_files(repo_root)), head, renames
    )
    return clock if rename_map.complete else clock._replace(complete=False)


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


def _window_since(churn_label: str | None) -> str | None:
    """The `--since` expression behind a `pick_churn_window` label."""
    for label, since in CHURN_WINDOWS:
        if churn_label == f"commits ({label})":
            return since
    return None


def commit_epochs_by_file(
    repo_root: Path, since: str | None,
) -> dict[Path, list[tuple[int, int]]] | None:
    """{abs_path: [(author epoch, commit index)]} over the churn window.

    The per-commit counterpart of `git_churn.git_churn_scores`: the same walk,
    keeping each commit's author time (`%at`, the clock the doc side uses) so a
    caller can count only the commits after a given moment. The commit index
    lets a caller count distinct commits rather than file-commits. None when
    git fails.
    """
    import subprocess

    cmd = ["git", "-C", str(repo_root), "log", "--relative",
           "--pretty=format:%x00%at", "--name-only"]
    if since:
        cmd.append(f"--since={since}")
    try:
        raw = subprocess.run(
            cmd, capture_output=True, text=True, check=True,
            timeout=GIT_TIMEOUT_SECONDS,
        ).stdout
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired,
            FileNotFoundError):
        return None
    epochs: dict[Path, list[tuple[int, int]]] = {}
    current = (0, -1)
    for line in raw.splitlines():
        line = line.strip()
        if line.startswith("\x00"):
            current = (int(line[1:] or 0), current[1] + 1)
        elif line:
            epochs.setdefault((repo_root / line).resolve(), []).append(current)
    return epochs


def subject_epochs(
    files: list[Path], epochs: dict[Path, list[tuple[int, int]]] | None,
) -> list[int] | None:
    """Sorted author epochs of the distinct commits touching `files` (a commit
    that touches several subject files appears once); None when the per-commit
    read failed. Built once per subject so each doc is one bisect."""
    if epochs is None:
        return None
    commits = {c for f in files for c in epochs.get(f, ())}
    return sorted(ts for ts, _ in commits)


def churn_since(sorted_epochs: list[int] | None, after: int | None) -> int | None:
    """Distinct subject commits authored strictly after `after` (the doc's last
    content change). A commit that changed the doc and its code together does
    not count. None when either side is unknown."""
    if sorted_epochs is None or after is None:
        return None
    return len(sorted_epochs) - bisect_right(sorted_epochs, after)


def behind_ratio(
    window_ratio: float, sorted_epochs: list[int] | None, after: int | None,
) -> tuple[float, int | None]:
    """``(ratio, churn_since)``: the window ratio capped by the subject churn
    after the doc's last content change.

    The cap applies only when some subject commit in the window predates the
    doc's last change - the doc caught up inside the window. A doc behind by
    the whole window keeps the uncapped file-commit ratio, so two wide refactors
    beside a doc frozen for a year still score by their full file-commit count.
    Uncapped as well when the churn after the doc is unknown."""
    since_doc = churn_since(sorted_epochs, after)
    if since_doc is None or sorted_epochs is None or since_doc == len(sorted_epochs):
        return window_ratio, since_doc
    return min(window_ratio, float(since_doc)), since_doc


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


@dataclass(frozen=True)
class _ChurnContext:
    """Repo-wide inputs every doc's staleness row is measured against."""
    repo_root: Path
    code_files: list[Path]
    churn_map: dict[Path, int]
    commit_epochs: dict[Path, list[tuple[int, int]]] | None
    baseline_epochs: list[int] | None
    repo_wide_code_churn: int
    clock: ContentClock
    generated_sources: list[tuple[str, list[str]]]


def _resolve_docs(
    repo_root: Path,
    doc_files: list[Path] | None,
    extra_exclude_dirs: set[str] | None,
    extra_exclude_patterns: list[str] | None,
    scope: Path | None,
) -> list[Path]:
    """The caller's doc list, or every discovered doc, as resolved paths."""
    if doc_files is None:
        doc_files = discover_doc_files(
            repo_root,
            extra_exclude_dirs=extra_exclude_dirs,
            extra_exclude_patterns=extra_exclude_patterns,
            scope=scope,
        )
    return [d.resolve() for d in doc_files]


def _explicit_links(
    repo_root: Path, doc_to_code_edges: list[dict] | None,
) -> dict[str, list[Path]]:
    """Explicit doc->code links (fallback c): doc rel -> [code abs paths]."""
    explicit: dict[str, list[Path]] = {}
    for edge in (doc_to_code_edges or []):
        code_abs = (repo_root / edge["code"]).resolve()
        explicit.setdefault(edge["doc"], []).append(code_abs)
    return explicit


def _code_ownership(
    code_files: list[Path], base_doc_dirs: dict[Path, Path], repo_root: Path,
) -> tuple[dict[Path, Path], dict[Path, list[Path]]]:
    """Nearest-ancestor ownership: (code file -> owning base doc, base doc ->
    the code subtree it owns down to the next base doc)."""
    code_owner: dict[Path, Path] = {}
    for c in code_files:
        owner = _nearest_base_doc(c, base_doc_dirs, repo_root)
        if owner is not None:
            code_owner[c] = owner
    owned_by_doc: dict[Path, list[Path]] = {}
    for code, owner in code_owner.items():
        owned_by_doc.setdefault(owner, []).append(code)
    return code_owner, owned_by_doc


def _doc_subject(
    d: Path,
    doc_rel: str,
    ctx: _ChurnContext,
    owned_by_doc: dict[Path, list[Path]],
    code_dirs: set[Path],
    explicit: dict[str, list[Path]],
) -> tuple[list[Path], str]:
    """The code a doc describes and the method that found it.

    Association precedence (per the PRD's ordered fallbacks): co-located base
    doc (nearest-ancestor) -> a parallel docs/ tree -> the doc's explicit code
    links -> repo-wide churn baseline (an empty subject).
    """
    if d in owned_by_doc:
        return owned_by_doc[d], "nearest-ancestor"
    par = _parallel_docs_subject(d, ctx.repo_root, code_dirs)
    if par is not None:
        subject = [c for c in ctx.code_files if any(sd in c.parents for sd in par)]
        return subject, "parallel-docs-tree"
    if explicit.get(doc_rel):
        return explicit[doc_rel], "explicit-links"
    return [], "repo-baseline"


def _subject_churn(
    subject: list[Path], method: str, ctx: _ChurnContext,
) -> tuple[int, int, list[int] | None]:
    """``(code churn, subject file count, sorted subject commit epochs)``."""
    if method != "repo-baseline":
        return (
            sum(ctx.churn_map.get(c, 0) for c in subject),
            len(subject),
            subject_epochs(subject, ctx.commit_epochs),
        )
    # repo-baseline has no derivable subject, so the ratio uses repo-wide
    # churn - a coarse proxy. In an active repo this can be high even for a
    # freshly-written floating doc, so `ratio` alone over-flags here.
    # `last_commit_days` is the corrective signal (the heatmap colours by
    # staleness, and a floating doc won't be a graph hub, so its stale_hubs
    # priority stays low). Read ratio together with subject_method and
    # last_commit_days, not on its own.
    return (
        ctx.repo_wide_code_churn,
        len(ctx.code_files),
        ctx.baseline_epochs,
    )


def _provenance(
    d: Path, ctx: _ChurnContext,
) -> tuple[str, tuple[str, ...], str | None, bool | None]:
    """``(method, source rels, generated_by, source_newer)`` for a generated doc
    (issue #178); empty/None for an ordinary hand-written doc."""
    prov_sources, generated_by, prov_method = resolve_doc_sources(
        d, ctx.repo_root, ctx.generated_sources
    )
    if not prov_method:
        return prov_method, (), generated_by, None
    src_newer = source_is_newer(d, prov_sources)
    prov_source_rels = tuple(_safe_rel(s, ctx.repo_root) for s in prov_sources)
    return prov_method, prov_source_rels, generated_by, src_newer


def _doc_row(
    d: Path, doc_rel: str, subject: list[Path], method: str, ctx: _ChurnContext,
) -> DocStaleness:
    """One doc's staleness row against its subject."""
    code_churn, subject_count, measured = _subject_churn(subject, method, ctx)
    doc_churn = ctx.churn_map.get(d, 0)
    window_ratio = code_churn / max(doc_churn, 1)
    # Behind-the-subject cap: only churn after the doc's last content change
    # (bulk commits already skipped by the clock) can make it a lying map.
    ratio, since_doc = behind_ratio(window_ratio, measured, ctx.clock.epoch(d))

    # Provenance (issue #178): a *generated* doc that declares a source is
    # measured against that source, not its own age/churn. When the source
    # has NOT moved on, the doc provably matches its source, so its
    # decaying-map ratio is zero by construction - this is what keeps a
    # freshly-accurate generated doc out of the lying_map bucket regardless
    # of how busy the surrounding code is. When the source HAS moved on,
    # `source_newer` carries the staleness verdict for the join to sign
    # freshness directly; the churn ratio is left untouched as a secondary
    # signal.
    prov_method, prov_source_rels, generated_by, src_newer = _provenance(d, ctx)
    if src_newer is False:
        ratio = 0.0

    return DocStaleness(
        path=doc_rel,
        last_commit_days=ctx.clock.days(d),
        last_change_basis="creation" if d in ctx.clock.creation_fallback else "content",
        doc_churn_in_window=doc_churn,
        code_churn_in_window=code_churn,
        subject_code_count=subject_count,
        subject_method=method,
        ratio=ratio,
        window_ratio=window_ratio,
        code_churn_since_doc_change=since_doc,
        provenance_method=prov_method,
        provenance_sources=prov_source_rels,
        provenance_generated_by=generated_by,
        source_newer=src_newer,
    )


def _modularity(
    code_files: list[Path], base_doc_dirs: dict[Path, Path],
    code_dirs: set[Path], pct_code_under_base: float,
) -> dict:
    """The size-weighted modularity coverage block."""
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
    return {
        "module_dir_count": module_dir_count,
        "module_dirs_with_base_doc": module_dirs_with_base,
        # Headline first: fraction of code-containing dirs with a base doc.
        "base_doc_dir_ratio": round(base_doc_dir_ratio, 3),
        "base_doc_coverage_when_present": round(pct_code_under_base, 3),
        "code_file_count": len(code_files),
        "large_repo": len(code_files) >= LARGE_REPO_CODE_FILES,
    }


def analyze_doc_staleness(
    repo_root: Path,
    doc_files: list[Path] | None = None,
    doc_to_code_edges: list[dict] | None = None,
    extra_exclude_dirs: set[str] | None = None,
    extra_exclude_patterns: list[str] | None = None,
    generated_sources: list[tuple[str, list[str]]] | None = None,
    scope: Path | None = None,
) -> dict:
    """Compute the doc-staleness metric and doc->code association summary.

    ``generated_sources`` is the ``[[generated]]`` folder->source map (issue
    #178). When None it is read from ``.assess/config.toml``; pass an explicit
    list to override (tests, or a caller that already loaded the config).

    ``scope`` (an absolute path under ``repo_root``) restricts both the doc and
    code discovery to a subtree for ``/assess <path>`` monorepo scoping; omit it
    for a whole-repo run.
    """
    repo_root = repo_root.resolve()
    if generated_sources is None:
        from lib.assess_config import load_generated_sources
        generated_sources = load_generated_sources(repo_root)
    docs = _resolve_docs(
        repo_root, doc_files, extra_exclude_dirs, extra_exclude_patterns, scope,
    )
    code_files = discover_code_files(
        repo_root,
        extra_exclude_dirs=extra_exclude_dirs,
        extra_exclude_patterns=extra_exclude_patterns,
        scope=scope,
    )

    # Churn: pick a window over the code files (the subject we care about), then
    # score both docs and code in that window for the ratio.
    churn_map, churn_label = pick_churn_window(repo_root, code_files + docs)
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

    # Per-commit author times over the same window, for the behind-the-subject
    # cap. A repo-baseline doc measures against every code file, so that list is
    # sorted once here rather than per doc.
    commit_epochs = commit_epochs_by_file(repo_root, _window_since(churn_label))
    # Last content change per doc, skipping bulk mechanical commits (#333).
    clock = content_clock(repo_root)
    ctx = _ChurnContext(
        repo_root=repo_root,
        code_files=code_files,
        churn_map=churn_map,
        commit_epochs=commit_epochs,
        baseline_epochs=subject_epochs(code_files, commit_epochs),
        repo_wide_code_churn=sum(churn_map.get(c, 0) for c in code_files),
        clock=clock,
        generated_sources=generated_sources,
    )

    base_doc_dirs = _build_base_doc_dirs(repo_root, docs)
    code_dirs = {c.parent for c in code_files}
    explicit = _explicit_links(repo_root, doc_to_code_edges)
    code_owner, owned_by_doc = _code_ownership(code_files, base_doc_dirs, repo_root)

    results: list[DocStaleness] = []
    method_counts: dict[str, int] = {}
    for d in docs:
        doc_rel = str(d.relative_to(repo_root))
        subject, method = _doc_subject(
            d, doc_rel, ctx, owned_by_doc, code_dirs, explicit,
        )
        method_counts[method] = method_counts.get(method, 0) + 1
        results.append(_doc_row(d, doc_rel, subject, method, ctx))
    docs_mapping_to_code = len(docs) - method_counts.get("repo-baseline", 0)

    # Association-derivability is itself a Layer 0 signal.
    code_under_base = sum(1 for c in code_files if c in code_owner)
    pct_code_under_base = code_under_base / len(code_files) if code_files else 0.0
    pct_docs_mapping = docs_mapping_to_code / len(docs) if docs else 0.0

    return {
        "available": True,
        "churn_window": churn_label,
        # Churn-measurement reliability, independent of doc->code association
        # precision. True = the window has no usable churn signal (every file ~1
        # commit), so any finding built on `code_churn_in_window` / `ratio` must
        # be discounted - see `lib.git_churn.churn_is_degenerate`.
        "churn_degenerate": churn_degenerate,
        "docs": [r.as_dict() for r in sorted(results, key=lambda r: -r.ratio)],
        # Bulk mechanical commits (a commit touching more than half of the
        # repo's docs, and at least ten) that `last_commit_days` skipped:
        # newest first, capped; the total sits beside it. `complete` False means
        # the full history was not read: a git failure (the plain newest-commit
        # clock ran) or a shallow clone (only the visible history was read).
        "bulk_commits_skipped": list(clock.skipped),
        "bulk_commits_skipped_total": clock.skipped_total,
        "bulk_commit_scan_complete": clock.complete,
        # Docs whose every commit is bulk: `last_commit_days` is a creation
        # date, marked `last_change_basis: "creation"` and confidence "low".
        "creation_date_fallback_count": sum(
            1 for d in docs if d in clock.creation_fallback
        ),
        "association": {
            "code_file_count": len(code_files),
            "doc_count": len(docs),
            "code_under_base_doc": code_under_base,
            "pct_code_under_base_doc": round(pct_code_under_base, 3),
            "docs_mapping_to_code": docs_mapping_to_code,
            "pct_docs_mapping_to_code": round(pct_docs_mapping, 3),
            "methods": method_counts,
        },
        "modularity": _modularity(
            code_files, base_doc_dirs, code_dirs, pct_code_under_base,
        ),
    }
