"""Cross-repo doc freshness: a docs repository's declared mappings, joined in ``/fleet`` (#664).

A central docs repository that describes other services has no in-repo subject,
so ``doc_staleness`` measures its docs against a repo baseline and never calls
them lying maps. A ``[[describes]]`` table in that repository's
``.assess/config.toml`` (``assess_config.load_describes``) declares, per doc or
folder, the service repository and the paths inside it (``scope``) the doc
describes. The fleet run, which holds every checkout, counts the service
commits to that scope authored after the doc's last content change and writes
one row per mapping to ``<out>/doc_freshness.jsonl``.

Opt-in: with no ``[[describes]]`` table anywhere nothing is measured and no
file is written. Trust before coverage, so every rule leans toward saying less:

- **Declared only.** A mapping is used only when a human wrote it; nothing is
  inferred from links. ``scope`` is required; ``scope = ["."]`` (the whole
  repository) is measured with ``confidence: "low"``.
- **Noise filter.** A commit does not count when its author is an automation
  bot (Dependabot, Renovate, any ``[bot]`` account except an AI assistant's,
  ``agent_identity.is_ai_assistant``), when its only in-scope files are
  lockfiles or dependency manifests (unless the scope names that file
  itself, as ``scope = ["package.json"]`` does), or when it is a bulk mechanical commit by
  ``git_churn``'s rule (``bulk_commit_need``), applied with the service's
  tracked files as the population. Merges are skipped.
- **Same clock as ``doc_staleness``.** The doc side is ``git_churn``'s content
  clock (bulk commits skipped); a folder takes its newest doc. Both sides use
  author time (``%at``), so a service commit authored before the doc edit and
  merged after it does not count.
- **Threshold.** ``behind`` needs ``BEHIND_THRESHOLD`` counted commits (3, a
  starting value awaiting real-fleet tuning); 1-2 is ``minor_drift``, 0 is
  ``current``. Only ``behind`` is a finding.
- **Mapping rot.** A doc path or scope path that no longer exists is
  ``broken``, never fresh. A repository not in this fleet run (or whose
  checkout cannot be read) is ``unknown`` with a reason: a run over a subset is
  not rot.
- **Honest degrade.** A shallow service checkout whose boundary is newer than
  the doc's last change, a doc with no readable history, a doc whose visible
  history starts at a shallow boundary (unless already ``behind``), or a git
  failure is ``unknown`` with a reason token.

Cost: no model call. One ``git log`` per mapping on the service checkout, plus
one content-clock pass per docs repository that declares mappings.

Counts-only (the fleet default) rows carry no free text: commit subjects and
shas appear only under ``--full``; with ``--hash-paths`` the doc and scope
paths are hashed like the scans' paths. The standalone ``/assess`` view is
``assess_config.declared_block``: the declared mappings listed as not measured
outside fleet mode, nothing guessed.
"""
from __future__ import annotations

import json
import subprocess
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import NamedTuple, TypedDict

from lib.agent_identity import is_ai_assistant
from lib.assess_config import DescribesMapping, load_describes
from lib.git_churn import (
    GIT_TIMEOUT_SECONDS,
    ContentClock,
    _dt_date,
    _shallow_boundary,
    _shallow_probe,
    bulk_commit_need,
    content_commit_clock,
    tracked_files,
)

FRESHNESS_FILE = "doc_freshness.jsonl"
FRESHNESS_ROW_VERSION = 1
METHOD = "cross-repo-explicit"
# Counted commits at which a mapping is reported behind. A starting value: the
# release notes say it awaits tuning on a real fleet.
BEHIND_THRESHOLD = 3
SUBJECTS_CAP = 5

CURRENT = "current"
MINOR_DRIFT = "minor_drift"
BEHIND = "behind"
UNKNOWN = "unknown"
BROKEN = "broken"
INVALID = "invalid"

# Reason tokens (rows hold no free text).
REPO_NOT_IN_FLEET = "repo_not_in_fleet"
REPO_CHECKOUT_UNREADABLE = "repo_checkout_unreadable"
DOC_PATH_MISSING = "doc_path_missing"
SCOPE_PATH_MISSING = "scope_path_missing"
DOC_HISTORY_UNKNOWN = "doc_history_unknown"
DOC_HISTORY_TRUNCATED = "doc_history_truncated"
SERVICE_HISTORY_TRUNCATED = "service_history_truncated"
SERVICE_HISTORY_UNKNOWN = "service_history_unknown"
INVALID_MAPPING = "invalid_mapping"  # also assess_config.declared_block's token

# Basenames of lockfiles and dependency manifests: a commit whose in-scope files
# are all of these is a dependency bump, not a change to the documented surface.
DEPENDENCY_FILES = frozenset({
    "package.json", "package-lock.json", "npm-shrinkwrap.json", "yarn.lock",
    "pnpm-lock.yaml", "bun.lockb", "go.mod", "go.sum", "go.work.sum",
    "cargo.toml", "cargo.lock", "pyproject.toml", "poetry.lock", "uv.lock",
    "pipfile", "pipfile.lock", "setup.cfg", "gemfile", "gemfile.lock",
    "composer.json", "composer.lock", "pom.xml", "build.gradle", "build.gradle.kts",
    "gradle.lockfile", "settings.gradle", "settings.gradle.kts", "libs.versions.toml",
    "mix.exs", "mix.lock", "package.swift", "package.resolved", "podfile",
    "podfile.lock", "pubspec.yaml", "pubspec.lock", "packages.lock.json",
    "directory.packages.props", "flake.lock", ".terraform.lock.hcl",
})
_BOT_HINTS = ("dependabot", "renovate")


class CommitRef(TypedDict):
    sha: str
    date: str  # author date, YYYY-MM-DD
    subject: str


class FreshnessRow(TypedDict):
    freshness_row_version: int
    docs_repo: str  # fleet id of the repository that declares the mapping
    path: str | None  # doc or folder in docs_repo (hashed under --hash-paths)
    repo: str | None  # fleet id of the described repository
    scope: list[str]  # paths inside repo (hashed under --hash-paths)
    method: str  # "cross-repo-explicit"
    status: str  # current | minor_drift | behind | unknown | broken | invalid
    reason: str | None  # token; None when measured
    confidence: str | None  # "high" | "low" (scope ["."] or a doc clock caveat); None unmeasured
    behind_threshold: int
    commits_since_doc_change: int | None  # counted service commits; None unmeasured
    doc_last_change: str | None  # YYYY-MM-DD of the doc's last content change
    commits: list[CommitRef] | None  # newest first, at most SUBJECTS_CAP; --full only


class FreshnessResult(NamedTuple):
    rows: list[FreshnessRow]
    errors: list[str]  # "<docs repo>: <message>" per rejected entry, for stderr


@dataclass(frozen=True)
class Checkout:
    """A fleet repository: its id and the checkout path (structurally a ``fleet.Target``)."""

    repo: str
    checkout: Path


class _Commit(NamedTuple):
    sha: str
    at: int
    subject: str


def status_for(count: int) -> str:
    if count >= BEHIND_THRESHOLD:
        return BEHIND
    return MINOR_DRIFT if count else CURRENT


def is_automation(name: str, email: str) -> bool:
    """A dependency or automation bot; an AI assistant's commit is real work and counts."""
    n, e = name.lower(), email.lower()
    if any(h in n or h in e for h in _BOT_HINTS):
        return True
    return ("[bot]" in n or "[bot]" in e) and not is_ai_assistant(e)


def is_dependency_file(rel: str) -> bool:
    return rel.rsplit("/", 1)[-1].lower() in DEPENDENCY_FILES


def _in_scope(rel: str, scope: Sequence[str]) -> bool:
    return any(s == "." or rel == s or rel.startswith(s + "/") for s in scope)


def _git(checkout: Path, *args: str) -> str | None:
    try:
        return subprocess.run(
            ["git", "-C", str(checkout), "-c", "core.quotepath=off", *args],
            capture_output=True, text=True, check=True, errors="replace",
            timeout=GIT_TIMEOUT_SECONDS,
        ).stdout
    except (subprocess.CalledProcessError, OSError, subprocess.TimeoutExpired):
        return None


@lru_cache(maxsize=8)
def _doc_clock(docs_root: Path) -> tuple[frozenset[Path], ContentClock]:
    """The docs repository's docs and content clock: one ``git log`` pass per repository."""
    from lib.doc_staleness import discover_doc_files

    docs = frozenset(d.resolve() for d in discover_doc_files(docs_root))
    return docs, content_commit_clock(docs_root, docs)


def _doc_epoch(docs_root: Path, path: str) -> tuple[int | None, bool, bool]:
    """``(epoch, low_confidence, lower_bound)`` of the doc's (or folder's newest doc's) last
    content change, by the content clock ``doc_staleness`` uses."""
    docs, clock = _doc_clock(docs_root.resolve())
    target = (docs_root / path).resolve()
    members = [target] if target.is_file() else [d for d in docs if d.is_relative_to(target)]
    epochs = [(e, d) for d in members if (e := clock.epoch(d)) is not None]
    if not epochs:
        return None, False, False
    epoch, doc = max(epochs, key=lambda pair: pair[0])
    low = not clock.complete or doc in clock.creation_fallback
    return epoch, low, doc in clock.lower_bound


def _service_commits(checkout: Path, scope: Sequence[str], after: int) -> list[_Commit] | None:
    """Counted commits to ``scope`` authored after ``after``, newest first; None on a git failure.

    The one ``git log`` per mapping: ``--full-diff`` lists every file a commit
    touched, so the bulk share is taken over the whole commit.
    """
    tracked = tracked_files(checkout.resolve())
    if tracked is None:
        return None
    need = bulk_commit_need(len(tracked))
    raw = _git(checkout, "log", "--no-merges", "--no-renames", "--full-diff", "--name-only",
               f"--since=@{after}", "--format=%x01%H%x00%at%x00%an%x00%ae%x00%s",
               "--", *[f":(literal){s}" for s in scope])
    if raw is None:
        return None
    out: list[_Commit] = []
    for block in raw.split("\x01")[1:]:
        head, _, names = block.partition("\n")
        sha, at, name, email, subject = (head.split("\x00") + [""] * 5)[:5]
        files = [ln.strip() for ln in names.splitlines() if ln.strip()]
        in_scope = [f for f in files if _in_scope(f, scope)]
        if (not at.isdigit() or int(at) <= after or is_automation(name, email)
                or len(files) >= need
                or all(is_dependency_file(f) and f not in scope for f in in_scope)):
            continue
        out.append(_Commit(sha, int(at), subject))
    return out


def _row(docs_repo: str, mapping: DescribesMapping | None, status: str,
         reason: str | None = None) -> FreshnessRow:
    return {
        "freshness_row_version": FRESHNESS_ROW_VERSION, "docs_repo": docs_repo,
        "path": mapping["path"] if mapping else None, "repo": mapping["repo"] if mapping else None,
        "scope": list(mapping["scope"]) if mapping else [], "method": METHOD,
        "status": status, "reason": reason, "confidence": None,
        "behind_threshold": BEHIND_THRESHOLD, "commits_since_doc_change": None,
        "doc_last_change": None, "commits": None,
    }


def measure(docs: Checkout, mapping: DescribesMapping, service: Checkout | None,
            *, full: bool) -> FreshnessRow:
    """One mapping's row (paths unhashed)."""
    row = _row(docs.repo, mapping, UNKNOWN)
    if not (docs.checkout / mapping["path"]).exists():
        return {**row, "status": BROKEN, "reason": DOC_PATH_MISSING}
    if service is None:
        return {**row, "reason": REPO_NOT_IN_FLEET}
    if _git(service.checkout, "rev-parse", "--verify", "-q", "HEAD") is None:
        return {**row, "reason": REPO_CHECKOUT_UNREADABLE}
    if any(not (service.checkout / s).exists() for s in mapping["scope"]):
        return {**row, "status": BROKEN, "reason": SCOPE_PATH_MISSING}
    epoch, low, lower_bound = _doc_epoch(docs.checkout, mapping["path"])
    if epoch is None:
        return {**row, "reason": DOC_HISTORY_UNKNOWN}
    row["doc_last_change"] = _dt_date(epoch)
    top = str(service.checkout)
    shallow = _shallow_probe(top)
    if shallow is None:
        return {**row, "reason": SERVICE_HISTORY_UNKNOWN}
    boundary = max(_shallow_boundary(top).values(), default=None) if shallow == "true" else None
    if shallow == "true" and (boundary is None or boundary > epoch):
        return {**row, "reason": SERVICE_HISTORY_TRUNCATED}
    commits = _service_commits(service.checkout, mapping["scope"], epoch)
    if commits is None:
        return {**row, "reason": SERVICE_HISTORY_UNKNOWN}
    status = status_for(len(commits))
    if lower_bound and status != BEHIND:
        # The doc's real last change may be older than its visible history: more
        # commits could follow it, so "current" cannot be vouched for.
        return {**row, "reason": DOC_HISTORY_TRUNCATED}
    low = low or lower_bound or mapping["scope"] == ["."]
    return {
        **row, "status": status, "confidence": "low" if low else "high",
        "commits_since_doc_change": len(commits),
        "commits": ([{"sha": c.sha[:12], "date": _dt_date(c.at), "subject": c.subject}
                     for c in commits[:SUBJECTS_CAP]] if full else None),
    }


def _hashed(row: FreshnessRow, salt: str) -> FreshnessRow:
    from lib.counts_only import hash_path

    path = hash_path(row["path"], salt) if row["path"] is not None else None
    return {**row, "path": path,
            "scope": [h for s in row["scope"] if (h := hash_path(s, salt)) is not None]}


def freshness_rows(targets: Iterable[Checkout], *, full: bool,
                   hash_salt: str | None) -> FreshnessResult:
    """Every declared mapping across ``targets``, sorted by docs repo then declaration order."""
    by_repo = {t.repo: Checkout(t.repo, t.checkout) for t in targets}
    rows: list[FreshnessRow] = []
    errors: list[str] = []
    for repo in sorted(by_repo):
        docs = by_repo[repo]
        if not (docs.checkout / ".assess" / "config.toml").is_file():
            continue
        cfg = load_describes(docs.checkout)
        errors += [f"{repo}: {msg}" for msg in cfg.errors]
        rows += [measure(docs, m, by_repo.get(m["repo"]), full=full) for m in cfg.mappings]
        rows += [_row(repo, None, INVALID, INVALID_MAPPING) for _ in cfg.errors]
    if hash_salt is not None:
        rows = [_hashed(r, hash_salt) for r in rows]
    return FreshnessResult(rows, errors)


def write_freshness(out_dir: Path, rows: Sequence[FreshnessRow]) -> Path | None:
    """Write ``doc_freshness.jsonl``; with no rows, remove one left by an earlier run."""
    path = out_dir / FRESHNESS_FILE
    if not rows:
        path.unlink(missing_ok=True)
        return None
    path.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in rows), encoding="utf-8")
    return path


def summary_line(rows: Sequence[FreshnessRow]) -> str:
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    parts = ", ".join(f"{counts[s]} {s}" for s in
                      (BEHIND, MINOR_DRIFT, CURRENT, UNKNOWN, BROKEN, INVALID) if s in counts)
    return f"{len(rows)} declared mappings: {parts}"
