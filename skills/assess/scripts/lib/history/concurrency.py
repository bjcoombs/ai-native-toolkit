"""Concurrency readiness: how often the same files change across merges (#985), informational.

The other history scans ask whether one change is safe; this one asks whether many can
land at once. A file that most merges touch is a serialisation point: two builders working
in parallel both edit it, and the second to merge resolves a conflict. Over the newest
``MERGE_SAMPLE`` first-parent commits of the run window (on a squash-merge or merge-commit
branch, one per merged change; ``git log --first-parent -m``, so a merge commit counts its
diff against the first parent), the scan records:

- **touch share**: per file, the share of those merges that touched it, and the count of
  files at or above ``TOUCH_SHARE_THRESHOLD`` (0.25: one merge in four, so with four
  builders in parallel one of them is, on average, holding the file);
- **median files per merge**;
- **generated churn**: files the repository marks generated (a ``linguist-generated``
  attribute in ``.gitattributes``, a ``GENERATED_NAME_PATTERNS`` name, or a generator
  header, ``lib/complexity/generated_files.py``), their share of all file touches
  (``generated_diff_share``), the share of merges touching one (``generated_merge_share``)
  and the count at or above the threshold; a committed generated file every change must
  regenerate is a conflict no one wrote;
- **registry fan-in**: for each file at or above the threshold, the number of distinct
  top-level folders (``.`` for the root) of the other files its merges changed, counted
  below the measured root (the ``--scope`` folder, or ``repo_root``); a high
  fan-in is a registry or index that every feature edits.

Informational: no maturity-model row, Step, count or headline reads it. Full output lists
the files behind the counts (``hot_files``, at most ``TOP_FILES``, by touch share then
path); counts-only output drops that list and keeps the counts and shares.

Concurrency readiness (#985), informational. `scan_concurrency(repo_root, scope,
history_window)` reads the window (anchored at HEAD's committer time, as `ai_usage`)
through `concurrency_block(repo_root, scope, history_window, rev, merge_sample)`, which a
test points at a fixed commit. Block: `{available, window, since, informational,
merge_sample, merges, threshold, files_touched, files_above_threshold, max_touch_share,
median_files_per_merge, generated_files_touched, generated_files_above_threshold,
generated_diff_share, generated_merge_share, max_fan_in, fan_in_null_reason, hot_files[],
limits[]}`; shapes in `lib/run_context_types_concurrency.py`. A share with nothing to
divide by is null; `max_fan_in` is null with `none_above_threshold` when no file reaches
the threshold. Fewer than `MIN_MERGES` merges reads `short_history`, a shallow clone
`shallow_clone` (or `shallow_unknown`), and a git failure `no_git`, `git_timeout`,
`not_a_repo` (the first read) or `git_failed` (a later one), each as `available: false`
with a `code: sentence` reason that counts-only output keeps as its code. Scope (#1110):
the merges read are those that touched the `--scope` folders (or `repo_root`, when it is
below the repository top), and only their files under it are counted, so every share is
relative to the changes into that part of the tree. The linguist-generated attribute and
the generator header are read from the working tree, not from `rev`. Output is sorted, so a pooled run (`--jobs`) writes the
same bytes as `--jobs 1`. Tests: `tests/test_concurrency.py`.
"""
from __future__ import annotations

import statistics
import subprocess
from collections import Counter
from pathlib import Path

from lib.complexity.generated_files import has_generated_header, matches_generated_name
from lib.history.ai_usage import resolve_window
from lib.history.history_window import anchored_since
from lib.run.run_scope import Scope
from lib.run_context_types_concurrency import ConcurrencyBlock, ConcurrencyUnavailable, HotFile

GIT_TIMEOUT_SECONDS = 60
MERGE_SAMPLE = 100
MIN_MERGES = 10
TOUCH_SHARE_THRESHOLD = 0.25
TOP_FILES = 20
NONE_ABOVE_THRESHOLD = "none_above_threshold"

REASON_SHORT = f"short_history: fewer than {MIN_MERGES} first-parent commits in the window"
REASON_SHALLOW = "shallow_clone: the clone is shallow, so the merges read are cut short"
REASON_SHALLOW_UNKNOWN = "shallow_unknown: could not confirm the clone is not shallow"

LIMITS = (
    "Informational: no Step, count or headline reads this block.",
    f"Reads the newest {MERGE_SAMPLE} first-parent commits of the window; each is one merged "
    "change on a squash-merge or merge-commit branch, and one direct commit otherwise.",
    "A file is generated when .gitattributes marks it linguist-generated, its name matches a "
    "generated-name pattern, or its first lines carry a generator marker; a deleted file is "
    "judged by its attribute and name only.",
    "Paths are the names each merge used; a renamed file counts under each of its names.",
    "Under --scope, only merges that touched the scope are read and only its files counted.",
    "Generated is judged from the working tree's .gitattributes and file heads, not each merge's.",
)


class _GitFailed(Exception):
    """A git read that failed; the message is a ``code: sentence`` reason."""


def _git(repo_root: Path | str, *args: str, stdin: str | None = None, first: bool = False) -> str:
    """``git -C repo_root <args>`` stdout. A refused call reads ``not_a_repo`` when it is the
    ``first`` read, and ``git_failed`` for any later one, which the first read has already
    shown runs on a repository."""
    try:
        return subprocess.run(
            ["git", "-C", str(repo_root), "-c", "core.quotepath=false", *args], input=stdin,
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            check=True, timeout=GIT_TIMEOUT_SECONDS,
        ).stdout
    except FileNotFoundError as e:
        raise _GitFailed("no_git: git is not installed") from e
    except subprocess.TimeoutExpired as e:
        raise _GitFailed(f"git_timeout: git timed out after {GIT_TIMEOUT_SECONDS}s") from e
    except subprocess.CalledProcessError as e:
        if first:
            raise _GitFailed("not_a_repo: not a git repository, or one with no commits") from e
        raise _GitFailed("git_failed: a git read of the history failed") from e


def _share(count: int, total: int) -> float | None:
    return round(count / total, 4) if total else None


def _top_folder(path: str, bases: tuple[str, ...] = ()) -> str:
    """The first folder of ``path`` below the measured root: the deepest of ``bases`` (the
    scope roots, or ``repo_root``, relative to the repository top) that holds it; ``.`` for
    a file directly in that root."""
    base = max((b for b in bases if path.startswith(b + "/")), key=len, default="")
    head, sep, _ = path[len(base) + 1 if base else 0:].partition("/")
    return head if sep else "."


def _bases(top: str, repo_root: Path, scope: Scope | None) -> tuple[str, ...]:
    """The measured roots relative to ``top`` (POSIX), the whole repository read as ``()``."""
    roots = scope.roots if scope is not None else (repo_root,)
    out = []
    for root in roots:
        try:
            rel = Path(root).resolve().relative_to(Path(top).resolve()).as_posix()
        except ValueError:
            continue
        if rel != ".":
            out.append(rel)
    return tuple(sorted(out))


def first_parent_merges(repo_root: Path, rev: str, since: str | None, scope: Scope | None,
                        limit: int) -> list[frozenset[str]]:
    """The files each of the newest ``limit`` first-parent commits changed, newest first.

    Paths are relative to the repository top. The pathspec is the scope's roots, or ``.``,
    so a ``repo_root`` below the top reads only the merges that touched it. ``-M`` pins
    rename detection, so a renamed file counts once, under its new name, whatever the
    user's ``diff.renames``."""
    bound = [f"--since={since}"] if since else []
    pathspec = [str(r) for r in scope.roots] if scope is not None else ["."]
    out = _git(repo_root, "log", rev, "--first-parent", "-m", "-M", "--name-only",
               "--format=%x1e", f"-n{limit}", *bound, "--", *pathspec)
    return [frozenset(line for line in record.splitlines() if line.strip())
            for record in out.split("\x1e")[1:]]


def generated_paths(top: str, paths: list[str]) -> set[str]:
    """The ``paths`` (relative to ``top``) the repository marks generated."""
    if not paths:
        return set()
    out = _git(top, "check-attr", "-z", "--stdin", "linguist-generated",
               stdin="\0".join(paths) + "\0")
    fields = out.split("\0")
    marked = {fields[i] for i in range(0, len(fields) - 2, 3)
              if fields[i + 2] in ("set", "true")}
    return marked | {p for p in paths if p not in marked and (
        matches_generated_name(p) or has_generated_header(Path(top) / p))}


def _hot(merges: list[frozenset[str]], counts: Counter[str], generated: set[str],
         bases: tuple[str, ...] = ()) -> list[HotFile]:
    total = len(merges)
    hot = sorted((p for p, n in counts.items() if n / total >= TOUCH_SHARE_THRESHOLD),
                 key=lambda p: (-counts[p], p))
    rows: list[HotFile] = []
    for path in hot:
        folders = {_top_folder(o, bases) for m in merges if path in m for o in m if o != path}
        rows.append({"path": path, "merges": counts[path],
                     "touch_share": round(counts[path] / total, 4),
                     "generated": path in generated, "fan_in": len(folders)})
    return rows


def concurrency_block(repo_root: Path, scope: Scope | None, history_window: str | None,
                      rev: str = "HEAD", merge_sample: int = MERGE_SAMPLE,
                      ) -> ConcurrencyBlock | ConcurrencyUnavailable:
    """The block for the newest ``merge_sample`` first-parent commits of ``rev`` in the window."""
    label = resolve_window(history_window)
    try:
        anchor = _git(repo_root, "log", "-1", "--format=%ct", rev, first=True).strip()
        since = anchored_since(history_window, int(anchor)) if anchor.isdigit() else None
        shallow = _git(repo_root, "rev-parse", "--is-shallow-repository").strip()
        if shallow != "false":
            return {"available": False,
                    "reason": REASON_SHALLOW if shallow == "true" else REASON_SHALLOW_UNKNOWN}
        merges = first_parent_merges(repo_root, rev, since, scope, merge_sample)
        if len(merges) < MIN_MERGES:
            return {"available": False, "reason": REASON_SHORT}
        top = _git(repo_root, "rev-parse", "--show-toplevel").strip()
        counts = Counter(p for m in merges for p in m)
        generated = generated_paths(top, sorted(counts))
    except _GitFailed as e:
        return {"available": False, "reason": str(e)}
    total, touches = len(merges), sum(counts.values())
    hot = _hot(merges, counts, generated, _bases(top, repo_root, scope))
    return {
        "available": True,
        "window": label,
        "since": since,
        "informational": True,
        "merge_sample": merge_sample,
        "merges": total,
        "threshold": TOUCH_SHARE_THRESHOLD,
        "files_touched": len(counts),
        "files_above_threshold": len(hot),
        "max_touch_share": round(max(counts.values(), default=0) / total, 4),
        "median_files_per_merge": float(statistics.median(len(m) for m in merges)),
        "generated_files_touched": len(generated),
        "generated_files_above_threshold": sum(1 for h in hot if h["generated"]),
        "generated_diff_share": _share(sum(counts[p] for p in generated), touches),
        "generated_merge_share": round(sum(1 for m in merges if m & generated) / total, 4),
        "max_fan_in": max((h["fan_in"] for h in hot), default=None),
        "fan_in_null_reason": None if hot else NONE_ABOVE_THRESHOLD,
        "hot_files": hot[:TOP_FILES],
        "limits": list(LIMITS),
    }


def scan_concurrency(repo_root: Path, scope: Scope | None, history_window: str | None,
                     ) -> ConcurrencyBlock | ConcurrencyUnavailable:
    """The ``concurrency`` block at HEAD (informational)."""
    return concurrency_block(repo_root, scope, history_window)
