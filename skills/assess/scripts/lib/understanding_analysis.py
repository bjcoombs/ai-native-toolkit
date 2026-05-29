"""Signals B4 + D2: where understanding lives, and the velocity clock.

The keyhole question the rest of `/assess` can't answer on its own: for a
complex module that no single context window can hold, *does anyone still
understand it*? Classic ownership ("who wrote the most lines") is the wrong
lens in the AI era - a directory churned by hundreds of agent sessions has
enormous activity and effectively zero retained understanding. So instead of an
owner we compute, per module:

  - **human anchor** (B4) - has a confirmed human substantively authored it?
    Someone who can be asked. (Straight from
    ``change_coupling.authorship_analysis`` - reused, never re-derived, so the
    conservative agent/human classification stays defined one way.)
  - **intent source** (B4) - is there an externalised spec/doc stating what the
    code *should* do? Computed from doc->code association (a doc whose directory
    is an ancestor of the module), the same path-proximity edges
    ``doc_staleness`` / the C-signal join use. Presence, not freshness: a stale
    doc still externalises intent (its *staleness* is signal C's lying-map
    finding, not this one's).
  - **authorship class** (B4) - human / agent / mixed / unknown, passed through
    from the authorship analysis.
  - **days_since_comprehension_event** (D2, the velocity clock) - calendar age
    is dead under AI velocity; "legacy" means *orphaned understanding*, which
    can happen on day one. So age is measured from the last
    **comprehension-event**: a human-authored commit touching the module (the
    git-log-reachable, deterministic instance of "a human-originated act that
    demonstrates understanding"). Fully automated commits don't count. ``None``
    when no human-authored commit is found (indeterminate, not "fresh").

The primary finding is **orphaned understanding**: high complexity ∧ no human
anchor ∧ no intent source - code agents wrote that no human understands and no
spec explains. The worst case, and the direct feed for the velocity clock.

This module is **standalone**: it consumes the JSON-serialisable outputs of
``authorship_analysis`` (per path), ``analyze_doc_staleness``, and the
complexity treemap (``complexity-stats.json``), and returns a JSON-serialisable
dict. It never imports or edits ``assess_core`` - wiring the ``understanding``
block into ``run-context.json`` is a separate task's job.
"""
from __future__ import annotations

import datetime as _dt
import subprocess
from pathlib import Path, PurePosixPath

# Reuse the shared, deliberately conservative agent-detection primitives from
# the change-coupling module rather than re-implementing them: agent/human
# classification must be defined exactly once (PRD Open Question 6 - never
# libel a human author), and the velocity clock's "human-authored commit" test
# has to agree with what `authorship_analysis` already called a human.
from lib.change_coupling import (
    GIT_TIMEOUT_SECONDS,
    _identity_is_agent,
    _repo_top,
)

# McCabe's classic "moderate risk" line, used as the floor for "high
# complexity". We gate the orphaned-understanding finding on the *higher* of
# this floor and the repo's own 95th-percentile CCN, so a genuinely simple repo
# never sprouts findings while a complex repo self-calibrates to its worst ~5%.
# Same value and rationale as the C-signal join (``doc_complexity_join``), kept
# consistent so "high complexity" means one thing across both findings.
MIN_HIGH_CCN = 10.0

# Advice for a human, never an instruction to a tool. The orphaned-understanding
# remedy is to put a person back in the loop before the next change - not to
# auto-generate a doc (that manufactures lying maps; see the C-signal guard).
_ORPHANED_RECOMMENDATION = (
    "Assign a human anchor before further change. This is complex code with no "
    "confirmed human author and no spec stating what it should do - agents can "
    "keep changing it, but no one retains the understanding to review them. Do "
    "NOT auto-generate a doc to clear this; a synthetic summary is a lying map."
)


def _extract_file_ccn(complexity_stats: dict) -> dict[str, float]:
    """Build path -> max-CCN from the per-file lists the stats expose.

    ``complexity-stats.json`` carries per-file CCN in its ranked lists
    (``top_complex`` / ``top_hotspots`` / ``top_large``) and, optionally, a full
    ``files`` list; we union them and keep the highest CCN seen per path. Mirrors
    the C-signal join's extraction so both findings read complexity identically.
    """
    ccn: dict[str, float] = {}
    for key in ("files", "top_complex", "top_hotspots", "top_large"):
        for entry in complexity_stats.get(key) or []:
            path = entry.get("path")
            if path is None or entry.get("ccn") is None:
                continue
            value = float(entry["ccn"])
            if value > ccn.get(path, float("-inf")):
                ccn[path] = value
    return ccn


def _high_ccn_threshold(complexity_stats: dict) -> float:
    """The CCN at or above which a module counts as 'high complexity'."""
    p95 = float((complexity_stats.get("ccn") or {}).get("p95", 0.0) or 0.0)
    return max(p95, MIN_HIGH_CCN)


def _doc_dirs(doc_staleness: dict) -> list[tuple[str, ...]]:
    """Directory parts of every doc, only when the staleness signal is available.

    A repo-root doc yields ``()`` (it is an ancestor of everything), matching the
    doc->code association the C-signal join and ``doc_staleness`` use.
    """
    if not doc_staleness.get("available", False):
        return []
    return [
        PurePosixPath(doc["path"]).parent.parts
        for doc in doc_staleness.get("docs", [])
        if doc.get("path")
    ]


def _has_intent_source(code_path: str, doc_dirs: list[tuple[str, ...]]) -> bool:
    """True if any doc's directory is an ancestor of ``code_path``.

    Same path-proximity rule as the doc-staleness association: a doc covers code
    in its own directory and below. A repo-root doc (``()``) therefore covers
    everything - intentionally, so this stays consistent with how the C-signal
    join decides a unit is documented; the orphaned-understanding finding is
    deliberately conservative (it should fire only when there is *no* externalised
    intent anywhere up the tree).
    """
    code_parts = PurePosixPath(code_path).parts
    return any(code_parts[: len(d)] == d for d in doc_dirs)


def _days_since_last_human_commit(repo_top: str, path: str) -> int | None:
    """Velocity clock (D2): days since the last human-authored commit on ``path``.

    A comprehension-event is a human-originated act demonstrating understanding;
    the deterministic, git-log-reachable instance is a commit whose *author* is a
    confirmed human (same human/agent test ``authorship_analysis`` uses). ``git
    log`` lists newest-first, so the first human-authored commit we hit is the
    most recent. Returns ``None`` when git is unavailable or no human-authored
    commit exists (indeterminate - never silently treated as "fresh").
    """
    fmt = "\x1e%ct\x1f%an\x1f%ae"
    try:
        raw = subprocess.run(
            ["git", "-C", repo_top, "log", "--no-merges", f"--format={fmt}", "--", path],
            capture_output=True, text=True, check=True, timeout=GIT_TIMEOUT_SECONDS,
        ).stdout
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        return None

    now = _dt.datetime.now().timestamp()
    for chunk in raw.split("\x1e"):
        chunk = chunk.strip()
        if not chunk:
            continue
        parts = chunk.split("\x1f")
        parts += [""] * (3 - len(parts))
        ct, an, ae = parts[:3]
        author_agent = _identity_is_agent(ae, an)
        author_human = bool(ae.strip()) and "@" in ae and not author_agent
        if not author_human:
            continue
        try:
            ts = int(ct)
        except ValueError:
            continue
        return max(0, int((now - ts) // 86400))
    return None


def analyze_understanding(
    repo_root: Path,
    authorship_by_path: dict[str, dict],
    doc_staleness: dict,
    complexity_stats: dict,
) -> dict:
    """Signals B4 + D2: per-module understanding signals and the orphaned finding.

    Args:
        repo_root: repository root, used only for the velocity-clock git query.
        authorship_by_path: ``{path: authorship_analysis(...) result}`` - each
            value carries ``human_anchor`` / ``authorship_class`` from
            ``change_coupling.authorship_analysis``. The set of keys defines the
            modules analysed.
        doc_staleness: the dict returned by ``analyze_doc_staleness`` (its
            ``docs[].path`` list drives intent-source detection).
        complexity_stats: the ``complexity-stats.json`` sidecar (per-file CCN in
            its ranked lists; CCN percentiles under ``ccn``).

    Returns a JSON-serialisable dict::

        {
          "available": bool,             # there was authorship data to analyse
          "high_ccn_threshold": float,   # CCN gate used for the finding
          "modules": [ {path, human_anchor, intent_source, authorship_class,
                        days_since_comprehension_event, finding, recommendation} ],
          "orphaned_understanding": [ ...paths ],
        }

    ``finding`` is ``"orphaned_understanding"`` when a module is high-complexity
    ∧ has no human anchor ∧ has no intent source, else ``None``. The module list
    covers every path in ``authorship_by_path`` (full understanding picture for
    the report), not only the flagged ones. Suitable as-is for
    ``run-context.json``'s ``understanding`` block (a later task's job to place).
    """
    repo_root = Path(repo_root)
    repo_top = _repo_top(repo_root)

    file_ccn = _extract_file_ccn(complexity_stats)
    threshold = _high_ccn_threshold(complexity_stats)
    doc_dirs = _doc_dirs(doc_staleness)

    modules: list[dict] = []
    orphaned: list[str] = []

    for path in sorted(authorship_by_path):
        record = authorship_by_path[path] or {}
        human_anchor = bool(record.get("human_anchor", False))
        authorship_class = record.get("authorship_class", "unknown")
        intent_source = _has_intent_source(path, doc_dirs)
        is_high_complexity = file_ccn.get(path, 0.0) >= threshold

        days_since = (
            _days_since_last_human_commit(repo_top, path)
            if repo_top is not None
            else None
        )

        finding: str | None = None
        if is_high_complexity and not human_anchor and not intent_source:
            finding = "orphaned_understanding"
            orphaned.append(path)

        modules.append({
            "path": path,
            "human_anchor": human_anchor,
            "intent_source": intent_source,
            "authorship_class": authorship_class,
            "days_since_comprehension_event": days_since,
            "finding": finding,
            "recommendation": _ORPHANED_RECOMMENDATION if finding else None,
        })

    return {
        "available": bool(authorship_by_path),
        "high_ccn_threshold": round(threshold, 2),
        "modules": modules,
        "orphaned_understanding": sorted(orphaned),
    }
