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

Associating a doc with the code it describes uses the one ownership rule in
``lib.docs.doc_ownership`` (nearest-ancestor base doc, then a parallel ``docs/`` tree,
the doc's explicit code links, then repo-wide churn), the same rule the
complexity join applies. Each row reports the method (``subject_method``), the
rule's roots (``subject_roots``), the subject size (``subject_code_count``) and
up to ``SUBJECT_PATHS_CAP`` of its paths (``subject_paths``), so a finding's
subject can be checked.

Churn comes from ``lib.git_churn`` -- the same machinery the complexity treemap
uses, so churn is computed one way across the whole skill.

Both `stale_hubs` and the `lying_map` join read the capped `ratio`. The association logic
reuses `doc_graph`'s code-link edges. For *generated* docs it reads `doc_provenance`
(per-doc and via the `[[generated]]` config map) and replaces the churn ratio with a
source-vs-doc verdict. `last_commit_days` and the instruction grader's `freshness_days`
(via `content_clock`) both read `git_churn.content_commit_clock`, and the skipped bulk
commits are reported as `bulk_commits_skipped` (newest first, capped) with
`bulk_commits_skipped_total`. A doc dated by the creation fallback carries
`last_change_basis: "creation"` and confidence `low`, which keeps it out of the stale-hub
finding; `creation_date_fallback_count` counts them. On a shallow clone the block
carries `history_truncated: true` and `history_boundary_date`, every row is confidence
`low`, and a doc whose newest visible change is the boundary carries
`last_commit_days_bound: "lower"` (copied onto its `stale_hubs` entry): its age is a
floor. Its ratio still rests only on subject commits after the boundary, so the
boundary listing every code file does not make it stale. `summary` (#699) carries the integer
counts a maturity model reads: `measured_docs` (rows with a code subject of their own,
every method but `repo-baseline`), `stale_docs` (those `doc_complexity_join.is_stale`
marks: ratio above `STALENESS_RATIO_THRESHOLD`, or a newer declared source) and
`stale_pct` (their share, a whole percent rounded up). Both counts are null on a truncated
or degenerate history, or with no measured doc, so the doc-freshness row reads unknown.
A page of only generator directives (`lib.docs.doc_directives`, a mkdocstrings `::: pkg.mod`
stub with no declared provenance) renders from code at build time: its row carries
`directive_only: true` and ratio 0, it leaves `measured_docs`, and
`summary.generator_directive_docs` counts it (#816).
Paths are repo-relative POSIX (`as_posix`) on every platform. The `modularity` block's `base_doc_dir_ratio`
is code directories holding a base doc over all code directories; a base doc in a
directory with no code (`docs/`, `agents/`) counts in neither, so the ratio stays
within 0-1 (tests: `tests/test_doc_modularity.py`).
"""
from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import NotRequired, TypedDict

from lib.docs.doc_complexity_join import is_stale
from lib.docs.doc_directives import is_directive_only
from lib.docs.doc_graph import (
    CODE_EXTENSIONS,
    DOC_EXTENSIONS,
    is_excluded_path,
    is_repo_file,
)
from lib.docs.doc_ownership import (
    REPO_BASELINE,
    SubjectSpec,
    bounded_subject_paths,
    build_base_doc_dirs,
    code_ownership,
    explicit_links,
    rel_posix,
    resolve_subjects,
    subject_spec,
)
from lib.docs.doc_provenance import resolve_doc_sources, source_is_newer
from lib.git_churn import (
    CHURN_WINDOWS,
    GIT_TIMEOUT_SECONDS,
    ContentClock,
    churn_is_degenerate,
    commit_epochs_by_file,
    content_commit_clock,
    pick_churn_window,
    tracked_files,
)
from lib.change_coupling import RenameMap
from lib.assess_config import DeclaredMapping, declared_block
from lib.run_context_types import BulkCommit, DocToCodeEdge


class DocProvenance(TypedDict):
    method: str
    sources: list[str]
    generated_by: str | None
    source_newer: bool | None


class DocStalenessRow(TypedDict):
    """One ``doc_staleness.docs`` row: :meth:`DocStaleness.as_dict`."""

    path: str
    last_commit_days: int | None
    doc_churn_in_window: int
    code_churn_in_window: int
    subject_code_count: int
    subject_method: str
    subject_roots: list[str]
    base_doc_dir: str | None
    subject_paths: list[str]
    ratio: float
    window_ratio: float
    code_churn_since_doc_change: int | None
    confidence: str
    last_change_basis: NotRequired[str]
    last_commit_days_bound: NotRequired[str]
    provenance: NotRequired[DocProvenance]
    directive_only: NotRequired[bool]


class DocAssociation(TypedDict):
    code_file_count: int
    doc_count: int
    code_under_base_doc: int
    pct_code_under_base_doc: float
    docs_mapping_to_code: int
    pct_docs_mapping_to_code: float
    methods: dict[str, int]


class DocModularity(TypedDict):
    module_dir_count: int
    module_dirs_with_base_doc: int
    base_doc_dir_ratio: float
    base_doc_coverage_when_present: float
    code_file_count: int
    large_repo: bool


class DocStalenessSummary(TypedDict):
    """``doc_staleness.summary``: integer counts a maturity model reads (#699).

    ``measured_docs`` counts the docs with a code subject of their own (every
    method but ``repo-baseline``: a baseline doc is measured against all of the
    repository's churn, so it reads stale on any active repository and says
    nothing about the doc), plus any generated doc whose declared source gave a
    ``source_newer`` verdict. ``stale_docs`` counts those that
    ``doc_complexity_join.is_stale`` marks (a ratio above
    ``STALENESS_RATIO_THRESHOLD``, or a newer declared source) and
    ``stale_pct`` is their share of ``measured_docs`` as a whole percent,
    rounded up so a share is never understated. Both are null when the history
    cannot carry the measure (a truncated or degenerate history) or no doc is
    measured, so a reader sees unknown rather than a false fresh.
    ``generator_directive_docs`` counts the directive-only pages left out (#816).
    """

    measured_docs: int
    stale_docs: int | None
    stale_pct: int | None
    generator_directive_docs: int


class DocStalenessBlock(TypedDict):
    """``run-context.json`` ``doc_staleness``: :func:`analyze_doc_staleness`."""

    available: bool
    churn_window: str | None
    churn_degenerate: bool
    docs: list[DocStalenessRow]
    bulk_commits_skipped: list[BulkCommit]
    bulk_commits_skipped_total: int
    bulk_commit_scan_complete: bool
    creation_date_fallback_count: int
    history_truncated: bool
    history_boundary_date: str | None
    association: DocAssociation
    modularity: DocModularity
    # ``[[describes]]`` cross-repo mappings (#664): listed, measured only by /fleet.
    declared_cross_repo: NotRequired[list[DeclaredMapping]]
    # Integer counts for a maturity model (#699); absent in blocks written before it.
    summary: NotRequired[DocStalenessSummary]


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
    # The ownership rule's roots for this doc and its bounded, sorted subject
    # paths (repo-relative POSIX); see lib.docs.doc_ownership.
    subject_roots: tuple[str, ...] = ()
    base_doc_dir: str | None = None
    subject_paths: tuple[str, ...] = ()
    # Whole-window ratio and the subject churn after the doc's last content
    # change; `ratio` is the smaller of the two (see the module docstring).
    # `code_churn_since_doc_change` is None when the doc has no commit or the
    # per-commit read failed, and `ratio` then falls back to `window_ratio`.
    window_ratio: float = 0.0
    code_churn_since_doc_change: int | None = None
    # Provenance (generated docs only; see lib.docs.doc_provenance). When a doc
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
    # False when the rename map could not be read (issue #425): both churn
    # counts may miss commits made under an old name.
    renames_followed: bool = True
    # Shallow clone (issue #544): churn is truncated for every doc, and a doc
    # whose newest visible change is the boundary has a lower-bound age.
    history_truncated: bool = False
    age_is_lower_bound: bool = False
    directive_only: bool = False  # generator-directive page (#816)

    @property
    def confidence(self) -> str:
        # repo-baseline uses repo-wide churn (no derivable subject), so a
        # stale-ratio computed against it is a coarse proxy. Mark it low so a
        # reader knows to discount before acting on the ranking. A creation-date
        # fallback is low for the same reason: its age is not a content age, and
        # so is a churn count that could not follow renames or a truncated history.
        if (self.subject_method == "repo-baseline" or self.last_change_basis == "creation"
                or not self.renames_followed or self.history_truncated):
            return "low"
        return "high"

    def as_dict(self) -> DocStalenessRow:
        d: DocStalenessRow = {
            "path": self.path,
            "last_commit_days": self.last_commit_days,
            "doc_churn_in_window": self.doc_churn_in_window,
            "code_churn_in_window": self.code_churn_in_window,
            "subject_code_count": self.subject_code_count,
            "subject_method": self.subject_method,
            "subject_roots": list(self.subject_roots),
            "base_doc_dir": self.base_doc_dir,
            "subject_paths": list(self.subject_paths),
            "ratio": round(self.ratio, 2),
            "window_ratio": round(self.window_ratio, 2),
            "code_churn_since_doc_change": self.code_churn_since_doc_change,
            "confidence": self.confidence,
        }
        if self.last_change_basis != "content":
            d["last_change_basis"] = self.last_change_basis
        if self.age_is_lower_bound:
            d["last_commit_days_bound"] = "lower"
        if self.directive_only:
            d["directive_only"] = True
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
    repo_root = repo_root.resolve()
    return _clock_at(repo_root, _head(repo_root))


def _head(repo_root: Path) -> str | None:
    """HEAD's sha, the cache key for the clock and the rename map; None on failure."""
    import subprocess

    try:
        return subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "HEAD"],
            capture_output=True, text=True, encoding="utf-8", check=False,
            timeout=GIT_TIMEOUT_SECONDS,
        ).stdout.strip() or None
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None


@lru_cache(maxsize=4)
def _renames_at(repo_root: Path, head: str | None) -> RenameMap:
    """The rename map, built once per (repo, HEAD) for the clock and the churn."""
    from lib.change_coupling import build_rename_map

    return build_rename_map(repo_root)


@lru_cache(maxsize=4)
def _clock_at(repo_root: Path, head: str | None) -> ContentClock:
    """Build the clock once per (repo, HEAD): doc discovery, rename map, git pass."""
    # A rename map that could not be read leaves renames unfollowed, so the scan
    # is not complete: a renamed doc whose only visible commit is a bulk rename
    # falls back to a creation date.
    rename_map = _renames_at(repo_root, head)
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
        return path.relative_to(repo_root).as_posix()
    except ValueError:
        return path.as_posix()


def _window_since(churn_label: str | None) -> str | None:
    """The window (``"12mo"``) behind a `pick_churn_window` label; None for all
    history. Counted back from HEAD's commit time, like the churn map (#773)."""
    for label, window in CHURN_WINDOWS:
        if churn_label == f"commits ({label})":
            return window
    return None


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
    renames_complete: bool = True


def staleness_summary(rows: list[DocStalenessRow], reliable: bool) -> DocStalenessSummary:
    """The ``summary`` counts over the subject-mapped ``rows``; null counts when
    not ``reliable`` or no row is mapped."""
    directive = sum(1 for row in rows if row.get("directive_only"))
    mapped = [row for row in rows if not row.get("directive_only") and (
        row["subject_method"] != REPO_BASELINE
        or row.get("provenance", {}).get("source_newer") is not None)]
    if not reliable or not mapped:
        return {"measured_docs": len(mapped), "stale_docs": None, "stale_pct": None,
                "generator_directive_docs": directive}
    stale = sum(1 for row in mapped if is_stale({**row}))
    return {"measured_docs": len(mapped), "stale_docs": stale,
            "stale_pct": -(-100 * stale // len(mapped)), "generator_directive_docs": directive}


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


def _subject_churn(
    subject: list[Path], method: str, ctx: _ChurnContext,
) -> tuple[int, list[int] | None]:
    """``(code churn, sorted subject commit epochs)``."""
    if method != REPO_BASELINE:
        return (
            sum(ctx.churn_map.get(c, 0) for c in subject),
            subject_epochs(subject, ctx.commit_epochs),
        )
    # repo-baseline has no derivable subject, so the ratio uses repo-wide
    # churn - a coarse proxy. In an active repo this can be high even for a
    # freshly-written floating doc, so `ratio` alone over-flags here.
    # `last_commit_days` is the corrective signal (the heatmap colours by
    # staleness, and a floating doc won't be a graph hub, so its stale_hubs
    # priority stays low). Read ratio together with subject_method and
    # last_commit_days, not on its own. The subject itself stays empty, so
    # `subject_code_count` is 0 here, matching the complexity join.
    return ctx.repo_wide_code_churn, ctx.baseline_epochs


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
    d: Path, doc_rel: str, spec: SubjectSpec, subject_rels: list[str],
    abs_of: dict[str, Path], ctx: _ChurnContext,
) -> DocStaleness:
    """One doc's staleness row against its subject."""
    method = spec["method"]
    subject = [abs_of[r] for r in subject_rels]
    code_churn, measured = _subject_churn(subject, method, ctx)
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
    directive_only = not prov_method and is_directive_only(d)
    if src_newer is False or directive_only:
        ratio = 0.0

    return DocStaleness(
        path=doc_rel,
        last_commit_days=ctx.clock.days(d),
        last_change_basis="creation" if d in ctx.clock.creation_fallback else "content",
        doc_churn_in_window=doc_churn,
        code_churn_in_window=code_churn,
        subject_code_count=len(subject_rels),
        subject_method=method,
        subject_roots=tuple(spec["roots"]),
        base_doc_dir=spec["base_dir"],
        subject_paths=tuple(bounded_subject_paths(subject_rels)),
        ratio=ratio,
        window_ratio=window_ratio,
        code_churn_since_doc_change=since_doc,
        provenance_method=prov_method,
        provenance_sources=prov_source_rels,
        provenance_generated_by=generated_by,
        source_newer=src_newer,
        renames_followed=ctx.renames_complete,
        history_truncated=ctx.clock.truncated,
        age_is_lower_bound=d in ctx.clock.lower_bound,
        directive_only=directive_only,
    )


def _modularity(
    code_files: list[Path], base_doc_dirs: dict[Path, Path],
    code_dirs: set[Path], pct_code_under_base: float,
) -> DocModularity:
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
    # Numerator and denominator both count code directories only: a base doc in
    # a directory with no code (`docs/`, `agents/`) documents no module, and
    # counting it let the ratio exceed 1.0 and move when a README did.
    module_dirs_with_base = len(code_dirs & base_doc_dirs.keys())
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
    doc_to_code_edges: list[DocToCodeEdge] | None = None,
    extra_exclude_dirs: set[str] | None = None,
    extra_exclude_patterns: list[str] | None = None,
    generated_sources: list[tuple[str, list[str]]] | None = None,
    scope: Path | None = None,
) -> DocStalenessBlock:
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
    # Both churn reads follow renames through the clock's rename map (#425).
    head = _head(repo_root)
    rename_map = _renames_at(repo_root, head)
    churn_map, churn_label = pick_churn_window(
        repo_root, code_files + docs, rename_map.paths)
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
    commit_epochs = commit_epochs_by_file(
        repo_root, _window_since(churn_label), rename_map.paths)
    # Last content change per doc, skipping bulk mechanical commits (#333).
    clock = _clock_at(repo_root, head)
    ctx = _ChurnContext(
        repo_root=repo_root,
        code_files=code_files,
        churn_map=churn_map,
        commit_epochs=commit_epochs,
        baseline_epochs=subject_epochs(code_files, commit_epochs),
        repo_wide_code_churn=sum(churn_map.get(c, 0) for c in code_files),
        clock=clock,
        generated_sources=generated_sources,
        renames_complete=rename_map.complete,
    )

    base_doc_dirs = build_base_doc_dirs(docs)
    code_dirs = {c.parent for c in code_files}
    explicit = explicit_links(repo_root, doc_to_code_edges)
    code_owner = code_ownership(code_files, base_doc_dirs, repo_root)

    # One ownership rule (lib.docs.doc_ownership): a spec per doc, expanded over the
    # discovered code files by repo-relative POSIX key, the key the complexity
    # join expands the same specs over. An explicit link to a file outside the
    # discovered code (excluded, or not code) is not part of any subject.
    abs_of = {rel_posix(c, repo_root): c for c in code_files}
    owning_docs = set(code_owner.values())
    base_docs = set(base_doc_dirs.values())
    specs = {
        d.relative_to(repo_root).as_posix(): subject_spec(
            d, d.relative_to(repo_root).as_posix(), repo_root, owning_docs, base_docs,
            code_dirs, explicit,
        )
        for d in docs
    }
    subjects = resolve_subjects(abs_of, specs)

    results: list[DocStaleness] = []
    method_counts: dict[str, int] = {}
    for d in docs:
        doc_rel = d.relative_to(repo_root).as_posix()
        spec = specs[doc_rel]
        method_counts[spec["method"]] = method_counts.get(spec["method"], 0) + 1
        results.append(_doc_row(d, doc_rel, spec, subjects[doc_rel], abs_of, ctx))
    docs_mapping_to_code = len(docs) - method_counts.get(REPO_BASELINE, 0)

    # Association-derivability is itself a Layer 0 signal.
    code_under_base = sum(1 for c in code_files if c in code_owner)
    pct_code_under_base = code_under_base / len(code_files) if code_files else 0.0
    pct_docs_mapping = docs_mapping_to_code / len(docs) if docs else 0.0

    declared = declared_block(repo_root)
    rows = [r.as_dict() for r in sorted(results, key=lambda r: -r.ratio)]
    block: DocStalenessBlock = {
        "available": True,
        "churn_window": churn_label,
        # Churn-measurement reliability, independent of doc->code association
        # precision. True = the window has no usable churn signal (every file ~1
        # commit), so any finding built on `code_churn_in_window` / `ratio` must
        # be discounted - see `lib.git_churn.churn_is_degenerate`.
        "churn_degenerate": churn_degenerate,
        "docs": rows,
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
        # Shallow clone: history before the newest boundary commit's date may be
        # missing; rows then carry confidence "low" and, at the boundary, a bound.
        "history_truncated": clock.truncated,
        "history_boundary_date": clock.boundary_date,
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
    if declared:  # omitted when nothing is declared: the feature is opt-in
        block["declared_cross_repo"] = declared
    # A truncated (shallow) or degenerate history cannot carry the measure.
    block["summary"] = staleness_summary(
        rows, reliable=not (clock.truncated or churn_degenerate))
    return block
