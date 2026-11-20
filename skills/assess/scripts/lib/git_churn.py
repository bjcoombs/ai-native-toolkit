"""Shared git-churn machinery for the /assess deterministic core.

`complexity-treemap.py` (code heatmap), `docs-staleness-treemap.py` (docs
heatmap) and `doc_staleness.py` (Layer 0 staleness metric) all need the same
two things: a per-file commit count over a window, and a way to pick a window
that gives a visible gradient. This module is the single source for both so
churn is computed one way, not three (PRD: "do not reinvent churn").

Pure subprocess + git. No heavy dependencies, so it imports cleanly in the
deterministic core (which runs with only networkx) as well as in the treemap
scripts (which also pull in lizard/matplotlib/squarify).
"""
from __future__ import annotations

import math
import re
import subprocess
import sys
from collections.abc import Callable, Iterable, Mapping
from functools import lru_cache
from pathlib import Path
from typing import Literal, NamedTuple

# Both names come from `assess_config`, the module that reads the config file,
# so the pathspecs below are built from the same path it resolves. Aliased:
# `ASSESS_DIR` is, to this module, the tool's own output directory relative to
# the scanned root. A scoped run writes to `.assess/<slug>/`, covered as a
# prefix.
from lib.assess_config import (
    ASSESS_DIR as ASSESS_OUTPUT_DIR,
    CONFIG_FILE as ASSESS_CONFIG_FILE,
)
from lib.change_coupling import build_rename_map
from lib.content_paths import RAW_LOG_ARGS, content_paths
from lib.history_window import WINDOW_PATTERN
from lib.run_context_types import BulkCommit, JsonDict

# Cap every git call so a stuck invocation (huge repo, lock contention, a hung
# credential prompt) degrades to "no churn data" rather than blocking the run.
GIT_TIMEOUT_SECONDS = 20

# Git pathspec dropping that directory from a status call. Exclude-only
# pathspecs are legal: git includes everything else.
ASSESS_EXCLUDE_PATHSPEC = f":(exclude){ASSESS_OUTPUT_DIR}"

# The one file under that directory whose uncommitted state moves what a scan
# measures: `lib.assess_config.load_config` reads `<root>/.assess/config.toml`
# before any scan and threads its excludes through every one of them, so an
# edit there takes the measured figures off HEAD. The dividing line is "moves
# what is measured on this run", not "is read at all" - a later run does read
# the wiki back (`complexity-stats.prior.json` for the cross-run diff,
# `first-flagged.json` for hotspot ages), but that shapes what the report says
# about figures already computed. There is no scoped variant - `load_config`
# takes the repo root, and a scoped run reads the same file. Git applies
# `:(exclude)` after matching, so a positive pathspec cannot re-admit a path
# under an excluded directory; this one is checked by a second status call.
ASSESS_CONFIG_PATHSPEC = f"{ASSESS_OUTPUT_DIR}/{ASSESS_CONFIG_FILE}"


def rename_paths(root: Path) -> dict[str, str]:
    """``build_rename_map(root).paths``, with a stderr note when the map is
    incomplete: git history could not be read for renames, so churn falls back
    to counting each commit under the name it used (issue #425)."""
    rename_map = build_rename_map(root)
    if not rename_map.complete:
        print("note: rename history unreadable - churn does not follow renames "
              "on this run.", file=sys.stderr)
    return rename_map.paths


def _under(top: Path, base: Path) -> Callable[[str], bool]:
    """A top-relative path inside ``base``: the walk's pathspec as a predicate,
    since ``--full-diff`` lists every path a matching commit touched."""
    prefix = base.resolve().relative_to(top).as_posix() if base.resolve() != top else ""
    return lambda rel: not prefix or rel == prefix or rel.startswith(prefix + "/")


def git_churn_scores(
    root: Path, since: str | None = None, scope: Path | None = None,
    renames: Mapping[str, str] | None = None,
) -> dict[Path, int]:
    """Return {abs_path: commit_count} for files under root tracked in git.

    Returns empty dict if root is not inside a git repo. Commit counts are
    over the full history reachable from HEAD by default. Pass `since` as
    a git-compatible date expression (e.g. "6 months ago") to window the
    count.

    `renames` maps historical paths (relative to the git top) to current ones,
    as ``change_coupling.build_rename_map`` gives them (issue #425). A commit
    on an old name is credited to the file's current name, and a commit that
    touches a file under two names counts once. A commit that only renames the
    file (the same blob under the new name) is not a content change and counts
    nothing (contract I425-2, :mod:`lib.content_paths`), so a file committed
    five times, renamed, then committed once scores 6. Without it renames are
    not followed. Git's own rename detection is off (``--no-renames``) either
    way, so the count does not depend on the reader's ``diff.renames`` setting.

    `scope` (an absolute path under `root`) restricts the churn to a subtree:
    git only walks that pathspec and the results are filtered to files under
    it. Omit it (the default) for a whole-repo run. Used by `/assess <path>`
    monorepo scoping so a scoped assessment carries no churn signal from a
    sibling directory. The pathspec does not reach a scoped file's history
    under an old name outside the subtree.
    """
    try:
        repo_top = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", check=True,
            timeout=GIT_TIMEOUT_SECONDS,
        ).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        return {}
    repo = Path(repo_top).resolve()
    scope_abs = scope.resolve() if scope is not None else None

    cmd = ["git", "-C", repo_top, "-c", "core.quotepath=off", "log",
           "--pretty=format:%x00", *RAW_LOG_ARGS]
    if since:
        cmd.append(f"--since={since}")
    if scope_abs is not None:
        cmd += ["--", str(scope_abs)]
    try:
        raw = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", check=True,
            timeout=GIT_TIMEOUT_SECONDS,
        ).stdout
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return {}

    in_scope = _under(repo, scope_abs) if scope_abs is not None else None
    counts: dict[Path, int] = {}
    for commit in raw.split("\x00"):
        touched: set[Path] = set()
        for rel in content_paths(commit.splitlines(), renames, in_scope):
            path = (repo / rel).resolve()
            if not path.is_relative_to(root):
                continue
            # A pathspec narrows which commits git walks, but a commit that
            # touches a scoped file can also touch a sibling one; drop those so
            # the count is strictly the subtree's.
            if scope_abs is not None and not path.is_relative_to(scope_abs):
                continue
            touched.add(path)
        for path in touched:
            counts[path] = counts.get(path, 0) + 1
    return counts


def commit_epochs_by_file(
    repo_root: Path, since: str | None,
    renames: Mapping[str, str] | None = None,
) -> dict[Path, list[tuple[int, int]]] | None:
    """{abs_path: [(author epoch, commit index)]} over the churn window.

    The per-commit counterpart of `git_churn_scores`: the same walk and the
    same `renames` remap, keeping each commit's author time (`%at`, the clock
    the doc side uses) so a caller can count only the commits after a given
    moment. The commit index lets a caller count distinct commits rather than
    file-commits; a commit appears once per file even when it touches the file
    under two names. Paths outside `repo_root` are dropped. None when git fails.
    """
    try:
        top = subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", check=True,
            timeout=GIT_TIMEOUT_SECONDS,
        ).stdout.strip()
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired,
            FileNotFoundError):
        return None
    top_path = Path(top).resolve()
    root = repo_root.resolve()
    cmd = ["git", "-C", top, "-c", "core.quotepath=off", "log",
           "--pretty=format:%x00%at", *RAW_LOG_ARGS]
    if since:
        cmd.append(f"--since={since}")
    cmd += ["--", str(root)]
    try:
        raw = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", check=True,
            timeout=GIT_TIMEOUT_SECONDS,
        ).stdout
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired,
            FileNotFoundError):
        return None
    epochs: dict[Path, list[tuple[int, int]]] = {}
    for index, chunk in enumerate(raw.split("\x00")[1:]):
        at, _, rest = chunk.partition("\n")
        for rel in content_paths(rest.splitlines(), renames, _under(top_path, root)):
            path = (top_path / rel).resolve()
            if path.is_relative_to(root):
                epochs.setdefault(path, []).append((int(at.strip() or 0), index))
    return epochs


def git_commit_info(root: Path) -> JsonDict:
    """Capture the commit the scan measured, so the report can pin its absolute
    LOC/CCN figures to a snapshot and warn when that snapshot is stale.

    Issue #59 observed figures drifting 15-25% low because a run measured an
    older commit than the one a reader later compared against. Absolute numbers
    are only trustworthy against a named commit; this surfaces that commit plus
    two staleness signals.

    Returns a dict:
      - ``available``: False (with ``reason``) when ``root`` isn't a git repo or
        git is unreachable; the report then omits the snapshot line.
      - ``head_sha`` / ``head_short``: the commit HEAD pointed at during the scan.
      - ``committed_date``: ISO-8601 author date of HEAD.
      - ``subject``: HEAD's commit subject line.
      - ``dirty``: True when tracked files have uncommitted changes, so the
        measured numbers reflect the working tree, not any single commit. The
        tool's own ``.assess/`` outputs are excluded - rewriting them cannot
        change what a scan measures. ``.assess/config.toml`` can, because its
        excludes reach every scan, so an edit there does count.
      - ``upstream``: the upstream tracking ref (e.g. ``origin/main``) or None.
      - ``behind``: commits HEAD is behind ``upstream`` (0 = up to date,
        None = no upstream configured), i.e. how stale the snapshot is vs remote.
    """
    def _git(*args: str) -> str | None:
        try:
            out = subprocess.run(
                ["git", "-C", str(root), *args],
                capture_output=True, text=True, encoding="utf-8", errors="replace", check=True,
                timeout=GIT_TIMEOUT_SECONDS,
            )
        except (subprocess.CalledProcessError, FileNotFoundError,
                subprocess.TimeoutExpired):
            return None
        return out.stdout.strip()

    head_sha = _git("rev-parse", "HEAD")
    if not head_sha:
        return {"available": False,
                "reason": "not a git repo or no commits on HEAD"}

    # `--porcelain` with untracked excluded: a non-empty result means the scan
    # saw uncommitted edits to tracked files, so its numbers don't match the
    # HEAD commit exactly. `.assess/` is excluded by pathspec because the run
    # rewrites its own wiki before this snapshot is taken, so on a repository
    # that tracks `.assess/` every run would otherwise report dirty and the
    # warning would stop carrying information (#414). A pathspec narrows the
    # check rather than filtering status codes, so a staged edit or a `git rm`
    # under `.assess/` drops out on the same terms.
    dirty_outside_assess = bool(
        _git("status", "--porcelain", "--untracked-files=no",
             "--", ASSESS_EXCLUDE_PATHSPEC))
    # `.assess/config.toml` is the one path under that directory whose
    # uncommitted state moves what every scan measures, so it is exactly the
    # condition `dirty` exists to report. Checked separately and OR-ed back in.
    # The rest of the directory is read back on a later run, but only to shape
    # what the report says about figures already computed.
    dirty_assess_config = bool(
        _git("status", "--porcelain", "--untracked-files=no",
             "--", ASSESS_CONFIG_PATHSPEC))

    info: JsonDict = {
        "available": True,
        "head_sha": head_sha,
        "head_short": head_sha[:12],
        "committed_date": _git("show", "-s", "--format=%cd", "--date=short",
                               "HEAD"),
        "subject": _git("show", "-s", "--format=%s", "HEAD"),
        "dirty": dirty_outside_assess or dirty_assess_config,
        "upstream": None,
        "behind": None,
    }

    upstream = _git("rev-parse", "--abbrev-ref", "--symbolic-full-name",
                    "@{upstream}")
    if upstream:
        info["upstream"] = upstream
        behind = _git("rev-list", "--count", "HEAD..@{upstream}")
        try:
            info["behind"] = int(behind) if behind is not None else None
        except ValueError:
            info["behind"] = None
    return info


def file_last_commit_epoch(path: Path) -> int | None:
    """Unix timestamp (epoch seconds) of `path`'s last commit. None if untracked.

    Uses author time (``%at``), not committer time (``%ct``). Author time
    reflects when the change was originally made, not when it was
    rebased/cherry-picked - a rebase must not certify stale docs as fresh. A doc
    authored 90 days ago and rebased yesterday should still read 90 days stale;
    ``%ct`` would reset the clock and hide the decay. This matches the author-time
    (``%at``) axis `lib.accretion_ratchet` and `lib.promissory_markers` already use.

    The raw commit time, before the days-ago conversion `file_last_commit_days`
    applies. Provenance-aware staleness needs the absolute timestamp so it can
    compare a generated doc against its declared source on the same axis (epoch
    seconds), independent of "now" - see `lib.doc_provenance`.
    """
    try:
        out = subprocess.run(
            ["git", "log", "-1", "--format=%at", "--", str(path)],
            cwd=path.parent if path.parent.exists() else Path.cwd(),
            capture_output=True, text=True, encoding="utf-8", errors="replace", check=False,
            timeout=GIT_TIMEOUT_SECONDS,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    raw = out.stdout.strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def file_last_commit_days(path: Path) -> int | None:
    """Days since `path` was last committed in git. None if not tracked.

    Distinct from churn count - this is the staleness axis (how long since
    the file last moved), used to colour the docs-staleness heatmap and to
    compute the doc-vs-code staleness ratio. None means "no git history for
    this file" so callers can degrade rather than treating untracked as fresh.
    """
    import datetime as _dt

    ts = file_last_commit_epoch(path)
    if ts is None:
        return None
    delta = _dt.datetime.now().timestamp() - ts
    return max(0, int(delta // 86400))


# Bulk mechanical commits (issue #333). A licence-header sweep, formatter run or
# mass rename touches most of a repository's docs without changing what any of
# them says; counting it as each doc's last change resets every staleness clock
# to the sweep's date and hides the lying maps the metric exists to find.
# Precision first: a commit is bulk only when it touches MORE than this share of
# the repository's docs...
BULK_COMMIT_DOC_SHARE = 0.5
# ...and at least this many of them. Below the floor, editing every doc at once
# is ordinary work on a small doc set (a README and two guides), not a sweep.
BULK_COMMIT_MIN_DOCS = 10


def bulk_commit_need(population: int) -> int:
    """Files of ``population`` a commit must touch to be bulk (the one rule)."""
    return max(BULK_COMMIT_MIN_DOCS, int(BULK_COMMIT_DOC_SHARE * population) + 1)


# Bound on the skipped-commit list written to run-context.json; the total is
# reported beside it.
BULK_COMMITS_SKIPPED_CAP = 20


class ContentClock(NamedTuple):
    """Last content-change time per doc, with bulk mechanical commits skipped.

    ``epochs`` maps a resolved doc path to the author time (``%at``) of its
    newest non-bulk commit, or of its oldest commit when every commit touching
    it is bulk (a bulk import that created it is its content creation).
    ``creation_fallback`` names the docs that took that oldest-commit fallback:
    for a doc regenerated in bulk on every release the value is its creation
    date, not a content age, so callers disclose it and discount it.
    ``bulk_shas`` holds every bulk commit, so :meth:`epoch` can apply the same
    skip to a non-doc file (``.cursorrules``). ``skipped`` lists, newest first
    and capped, the bulk commits that were newer than some doc's chosen commit.
    ``complete`` is False when the full history was not read. On a git
    failure or timeout the maps are empty and :meth:`epoch` falls back to the
    plain newest-commit read, the behaviour before this clock existed. In a
    shallow clone the maps are filled from the visible history, which can omit
    older content commits and bulk commits.

    ``truncated`` is True when git confirms a shallow clone (issue #544); a
    failed probe leaves it False and ``complete`` False, unknown rather than
    truncated. ``boundary_epoch`` is the author time of the newest shallow
    boundary commit reachable from HEAD (issue #633): history before it may be
    missing. ``lower_bound`` names the docs whose newest visible change is a
    boundary commit, so their age is a lower bound (the real last change can be
    older). A boundary commit lists
    every file, like a root commit, so it is never counted as bulk.
    """

    epochs: dict[Path, int]
    bulk_shas: frozenset[str]
    skipped: tuple[BulkCommit, ...]
    skipped_total: int
    doc_count: int
    complete: bool
    creation_fallback: frozenset[Path] = frozenset()
    truncated: bool = False
    boundary_epoch: int | None = None
    lower_bound: frozenset[Path] = frozenset()

    @property
    def boundary_date(self) -> str | None:
        """``YYYY-MM-DD`` of ``boundary_epoch``; None on a full history."""
        return None if self.boundary_epoch is None else _dt_date(self.boundary_epoch)

    def epoch(self, path: Path) -> int | None:
        path = path.resolve()
        if path in self.epochs:
            return self.epochs[path]
        return _file_content_epoch(path, self.bulk_shas)

    def days(self, path: Path) -> int | None:
        import datetime as _dt

        ts = self.epoch(path)
        if ts is None:
            return None
        return max(0, int((_dt.datetime.now().timestamp() - ts) // 86400))


def _file_content_epoch(path: Path, bulk_shas: frozenset[str]) -> int | None:
    """Per-file fallback: newest commit to `path` not in `bulk_shas`, else its
    oldest commit. None if untracked or git fails."""
    if not bulk_shas:
        return file_last_commit_epoch(path)
    try:
        out = subprocess.run(
            ["git", "log", "--format=%H %at", "--", str(path)],
            cwd=path.parent if path.parent.exists() else Path.cwd(),
            capture_output=True, text=True, encoding="utf-8", errors="replace", check=False,
            timeout=GIT_TIMEOUT_SECONDS,
        ).stdout
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    rows = [ln.split() for ln in out.splitlines() if len(ln.split()) == 2]
    for sha, at in rows:
        if sha not in bulk_shas:
            return int(at)
    return int(rows[-1][1]) if rows else None


@lru_cache(maxsize=4)
def content_commit_clock(
    repo_root: Path,
    docs: frozenset[Path],
    head: str | None = None,
    renames: tuple[tuple[str, str], ...] = (),
) -> ContentClock:
    """One ``git log`` pass over the docs' history, skipping bulk commits.

    `docs` (resolved paths) is both the denominator for the bulk share and the
    set the clock covers. The log is restricted to the docs' extensions, so its
    cost scales with doc history, not the whole tree. Outside a git repo the
    clock is empty and complete; on a git failure it is empty and incomplete.
    Cached so the doc-staleness metric and the instruction grader share one pass;
    `head` (the HEAD sha) is only a cache key, so a new commit gets a fresh clock.
    `renames` maps historical repo-relative paths to current ones (as
    ``change_coupling.build_rename_map`` gives them), so a doc's commits under an
    old name still count after a mass rename.
    """
    empty = ContentClock({}, frozenset(), (), 0, len(docs), True)
    try:
        top = subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "--show-toplevel"],
            capture_output=True, text=True,
            encoding="utf-8", errors="replace", check=True, timeout=GIT_TIMEOUT_SECONDS,
        ).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        return empty
    top_path = Path(top).resolve()
    exts = sorted({d.suffix.lower() for d in docs if d.suffix})
    if not exts:
        return empty
    cmd = ["git", "-C", top, "-c", "core.quotepath=off", "log",
           "--format=%x01%H %at", *RAW_LOG_ARGS, "--"]
    cmd += [f":(glob,icase)**/*{e}" for e in exts]
    try:
        raw = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8", check=True,
            timeout=GIT_TIMEOUT_SECONDS, errors="replace",
        ).stdout
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        return ContentClock({}, frozenset(), (), 0, len(docs), False)
    shallow = _shallow_probe(top)
    full_history = shallow == "false"
    boundary = _shallow_boundary(top) if shallow == "true" else {}
    rename_to = dict(renames)

    # Parse newest first: (sha, epoch, docs in `docs` the commit touched).
    commits: list[tuple[str, int, set[Path]]] = []
    for chunk in raw.split("\x01")[1:]:
        head_line, _, rest = chunk.partition("\n")
        sha, _, at_raw = head_line.strip().partition(" ")
        touched = {top_path / rel for rel in content_paths(
            rest.splitlines(), rename_to, lambda rel: Path(rel).suffix.lower() in exts)}
        commits.append((sha, int(at_raw), touched & docs))

    # $GIT_DIR/shallow also lists other shallow refs' boundaries (#633).
    walked = {sha for sha, _, _ in commits}
    boundary = {sha: at for sha, at in boundary.items()
                if sha in walked or _reaches_from_head(top, sha)}
    need = bulk_commit_need(len(docs))
    bulk = {sha for sha, _, touched in commits
            if len(touched) >= need and sha not in boundary}
    epochs: dict[Path, int] = {}
    chosen: dict[Path, str] = {}
    pending: dict[Path, list[str]] = {}  # bulk commits seen before a doc resolves
    oldest: dict[Path, tuple[str, int]] = {}
    skipped: set[str] = set()
    for sha, at, touched in commits:
        for p in touched:
            oldest[p] = (sha, at)
            if p in epochs:
                continue
            if sha in bulk:
                pending.setdefault(p, []).append(sha)
            else:
                epochs[p] = at
                chosen[p] = sha
                skipped.update(pending.pop(p, []))
    # A doc whose every commit is bulk falls back to its oldest (its creation);
    # the bulk commits newer than that were still skipped.
    for p, shas in pending.items():
        epochs[p] = oldest[p][1]
        skipped.update(s for s in shas if s != oldest[p][0])

    records: list[BulkCommit] = [
        {"sha": sha,
         "date": _dt_date(at),
         "docs_touched": len(touched),
         "doc_count": len(docs)}
        for sha, at, touched in commits if sha in skipped
    ]
    return ContentClock(
        epochs=epochs,
        bulk_shas=frozenset(bulk),
        skipped=tuple(records[:BULK_COMMITS_SKIPPED_CAP]),
        skipped_total=len(records),
        doc_count=len(docs),
        complete=full_history,
        creation_fallback=frozenset(pending),
        truncated=shallow == "true",
        boundary_epoch=max(boundary.values(), default=None),
        lower_bound=frozenset(p for p, sha in chosen.items() if sha in boundary),
    )


def _shallow_probe(top: str) -> Literal["true", "false"] | None:
    """``git rev-parse --is-shallow-repository``: "true", "false", or None when
    the probe failed or timed out (it cannot vouch either way)."""
    try:
        probe = subprocess.run(
            ["git", "-C", top, "rev-parse", "--is-shallow-repository"],
            capture_output=True, text=True,
            encoding="utf-8", errors="replace", check=False, timeout=GIT_TIMEOUT_SECONDS,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if probe.returncode != 0:
        return None
    out = probe.stdout.strip()
    if out == "true":
        return "true"
    return "false" if out == "false" else None


def _shallow_boundary(top: str) -> dict[str, int]:
    """The shallow boundary commits (``$GIT_DIR/shallow``) mapped to their
    author time; empty when the file or the commits cannot be read."""
    try:
        rel = subprocess.run(
            ["git", "-C", top, "rev-parse", "--git-path", "shallow"],
            capture_output=True, text=True,
            encoding="utf-8", errors="replace", check=True, timeout=GIT_TIMEOUT_SECONDS,
        ).stdout.strip()
        shas = (Path(top) / rel).read_text().split()
        if not shas:
            return {}
        out = subprocess.run(
            ["git", "-C", top, "show", "-s", "--format=%H %at", *shas],
            capture_output=True, text=True,
            encoding="utf-8", errors="replace", check=True, timeout=GIT_TIMEOUT_SECONDS,
        ).stdout
    except (subprocess.CalledProcessError, FileNotFoundError,
            subprocess.TimeoutExpired, OSError):
        return {}
    rows = [ln.split() for ln in out.splitlines()]
    return {r[0]: int(r[1]) for r in rows if len(r) == 2 and r[1].isdigit()}


def _reaches_from_head(top: str, sha: str) -> bool:
    """Whether ``sha`` is an ancestor of HEAD; kept (True) when git cannot say."""
    try:
        code = subprocess.run(
            ["git", "-C", top, "merge-base", "--is-ancestor", sha, "HEAD"],
            capture_output=True, check=False, timeout=GIT_TIMEOUT_SECONDS,
        ).returncode
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return True
    return code != 1


def _dt_date(epoch: int) -> str:
    import datetime as _dt

    return _dt.datetime.fromtimestamp(epoch, tz=_dt.timezone.utc).strftime("%Y-%m-%d")


@lru_cache(maxsize=8)
def tracked_files(root: Path) -> frozenset[Path] | None:
    """Resolved absolute paths of git-tracked files under `root`.

    This is the precise definition of "files in the repo": it excludes
    untracked and ignored files (e.g. a contributor's personal CLAUDE.md left
    in the working tree). Returns None when `root` isn't a git repo, so callers
    fall back to a plain filesystem walk.

    Cached (read-only result) so the several callers in one assessment - doc
    graph, staleness (x2), instruction grading - don't each shell out to git.
    Pass an already-resolved `root` for cache hits.
    """
    try:
        repo_top = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--show-toplevel"],
            capture_output=True, text=True,
            encoding="utf-8", errors="replace", check=True, timeout=GIT_TIMEOUT_SECONDS,
        ).stdout.strip()
        raw = subprocess.run(
            ["git", "-C", str(root), "ls-files", "--full-name", "-z"],
            capture_output=True, text=True,
            encoding="utf-8", errors="replace", check=True, timeout=GIT_TIMEOUT_SECONDS,
        ).stdout
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        return None
    repo = Path(repo_top)
    out: set[Path] = set()
    for rel in raw.split("\0"):
        if not rel:
            continue
        try:
            out.add((repo / rel).resolve())
        except OSError:
            continue
    return frozenset(out)


# Time windows tried in order from narrowest to widest. The first one
# that gives both gradient depth (max >= MIN_MAX) AND visual coverage
# (>= MIN_COVERAGE_PCT of files touched) wins. If nothing qualifies, we
# fall back to the widest window that returned any data. since=None
# means "full history".
CHURN_WINDOWS: list[tuple[str, str | None]] = [
    ("last 12mo", "12 months ago"),
    ("last 24mo", "24 months ago"),
    ("last 5y",   "5 years ago"),
    ("all-time",  None),
]
_WINDOW_IN_LABEL = re.compile(rf"\blast ({WINDOW_PATTERN})\b")


def window_from_churn_label(label: str | None) -> str | None:
    """The ``<n>d|w|mo|y`` window in a churn label, or None for all history.

    ``pick_churn_window`` labels its window ``"commits (last 12mo)"``; scans
    read ``history_window`` in the headless runner's ``--window`` grammar
    (``"12mo"``), so ``assess_core`` normalises through here. ``all-time``, a
    missing label or any unrecognised one means all history (None).
    """
    match = _WINDOW_IN_LABEL.search(label or "")
    return match.group(1) if match else None


MIN_MAX = 3          # max commits per file must clear this for visible gradient
MIN_COVERAGE_PCT = 10.0   # % of scored files with any activity in the window


def is_tracked(path: Path, tracked: frozenset[Path] | None) -> bool:
    """Whether ``path`` is in a ``tracked_files`` set; untracked when unknowable."""
    if tracked is None:
        return False
    try:
        return path.resolve() in tracked
    except OSError:
        return False


def pick_churn_window(
    root: Path, file_paths: list[Path], renames: Mapping[str, str] | None = None,
) -> tuple[dict[Path, int] | None, str | None]:
    """Walk CHURN_WINDOWS narrowest-to-widest; return the first window
    that gives both gradient depth and visual coverage. Falls back to
    the widest non-empty window if none qualify.

    `file_paths` is the set of scored files; "touched" means touched AND
    scoreable, so coverage % is comparable across windows. Returns
    (per-file-count-dict, "commits (<label>)") or (None, None) when the
    path isn't inside a git repo. Every window follows renames through
    `renames` (as for `git_churn_scores`), built once here when omitted.
    """
    if renames is None:
        renames = rename_paths(root)
    total_files = max(len(file_paths), 1)
    widest_fallback: tuple[dict[Path, int], str] | None = None

    for label, since in CHURN_WINDOWS:
        data = git_churn_scores(root, since=since, renames=renames)
        if not data:
            continue
        scored_hits = {p: data[p] for p in file_paths if p in data}
        if not scored_hits:
            continue
        if widest_fallback is None or label == CHURN_WINDOWS[-1][0]:
            widest_fallback = (data, label)
        coverage = 100.0 * len(scored_hits) / total_files
        max_commits = max(scored_hits.values())
        if max_commits >= MIN_MAX and coverage >= MIN_COVERAGE_PCT:
            if label != CHURN_WINDOWS[0][0]:
                print(f"note: activity sparse - widened window to "
                      f"{label} ({coverage:.0f}% of files touched, "
                      f"max {max_commits} commits).",
                      file=sys.stderr)
            return ({p: data.get(p, 0) for p in file_paths},
                    f"commits ({label})")

    if widest_fallback is not None:
        data, label = widest_fallback
        scored_hits = {p: data[p] for p in file_paths if p in data}
        coverage = 100.0 * len(scored_hits) / total_files
        max_commits = max(scored_hits.values()) if scored_hits else 0
        print(f"note: activity below thresholds in every window; "
              f"using {label} ({coverage:.0f}% coverage, "
              f"max {max_commits}).", file=sys.stderr)
        return ({p: data.get(p, 0) for p in file_paths},
                f"commits ({label})")
    return None, None


# A churn window is "degenerate" when its commits-per-file distribution is
# effectively flat: almost every file that moved in the window shows a single
# commit. That is the fingerprint of a history with no usable churn signal -
# a shallow clone, a fresh import, or a squashed/extracted source tree where
# every file was created in one bulk commit. A count of "1 commit" there is an
# extraction artifact, not a measure of how much the file actually churns.
#
# The danger is that downstream signals read the count as if it meant activity:
# `code_churn_in_window` swells to ~= the file count, the doc-staleness ratio
# inflates, and a *precise* doc->code association (high `subject_method`
# confidence) then stamps a high-confidence `lying_map` onto pure noise. The two
# properties are independent - association precision says *which* code a doc maps
# to; measurement reliability says whether the churn count *means* anything - and
# only this flag carries the second. Consumers that read it degrade: cap finding
# confidence, drop churn-derived findings from the summary, flatten the treemap
# saturation axis. This is the single source of truth; consumers thread the
# boolean rather than recomputing the distribution.
DEGENERATE_CHURN_P95 = 1
# Below this many files with any activity the distribution is too small to judge.
# A three-file utility repo where each file shows one commit is not the
# shallow-clone / squashed-history artifact this guards against, so we never call
# degeneracy on a handful of files - we'd risk flattening a genuinely tiny repo.
MIN_ACTIVE_FILES_FOR_DEGENERACY = 5


def churn_is_degenerate(
    commit_counts: Iterable[int],
    min_active_files: int = MIN_ACTIVE_FILES_FOR_DEGENERACY,
    p95_threshold: int = DEGENERATE_CHURN_P95,
) -> bool:
    """True when a per-file commit-count distribution carries no churn signal.

    `commit_counts` is any iterable of per-file commit counts - the values of a
    churn map (``pick_churn_window`` / ``git_churn_scores`` output). Files with
    zero commits in the window are ignored: degeneracy is a property of the
    *shape* of the activity among files that actually moved, not of how many sat
    idle. Among those active files we take the 95th percentile (nearest-rank, so
    a couple of genuinely-churned files can't mask an otherwise-flat import) and
    call the window degenerate when it is at or below ``p95_threshold`` (1 by
    default - "almost every touched file shows a single commit"). p95 rather than
    max so the detector reports on the bulk of the distribution, equivalent to
    "near-zero variance across files".

    Returns False when fewer than ``min_active_files`` files have any activity:
    too few data points to distinguish a degenerate history from a legitimately
    small or quiet repo, where flattening the churn signal would be the wrong
    call. Pure and side-effect-free so every consumer (doc-staleness join,
    keyhole summary, treemap) can read the same verdict off the same data.
    """
    active = sorted(c for c in commit_counts if c and c > 0)
    if len(active) < min_active_files:
        return False
    # Nearest-rank p95: index of the ceil(0.95 * n)-th value (1-based), so for an
    # all-ones distribution p95 == 1 and a single outlier among thousands of
    # ones still leaves p95 == 1.
    idx = max(0, math.ceil(0.95 * len(active)) - 1)
    return active[idx] <= p95_threshold
