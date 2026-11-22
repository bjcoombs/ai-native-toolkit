"""Reviewed findings: a completed review clears a finding until its source changes.

``self_referential_tests`` keys on commit origin (a test co-committed with the
code it covers), which no later commit can change, so the finding can never
clear by itself. Its action asks for a human review instead. This module reads
that review from the committed ``.assess/actions.json``: an entry with
``finding: self_referential_tests``, ``status: done``, a ``completed_sha`` and
the flagged source path in ``files`` marks the path reviewed when the commit is
an ancestor of HEAD and no commit since it touched the path (issue #721).

A reviewed path stays in its ``derived_findings`` row, which gains a
``reviewed`` list of ``{path, completed_sha, note}``, so nothing is hidden. It
leaves the attention ranking, ``prescribed_actions``, the keyhole summary and
the finding's rendered path list, and the rendered findings section lists it
under "Reviewed". A later commit touching the path re-flags it.

Deterministic: the rule reads only HEAD, its history and the actions.json
committed at HEAD (never the working tree or an earlier run's output). Any
failure (no git, no committed file, bad JSON, an unknown or non-ancestor sha)
leaves the path flagged. Imports no orchestrator and no ``keyhole_signals``.
"""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Any

from lib.run_context_types import DerivedFinding, ReviewedPath

# Findings a recorded review can clear. Only findings that no code change can
# clear belong here; every other finding clears when its signal does.
REVIEWABLE_FINDINGS = frozenset({"self_referential_tests"})

# The actions.json arrays read for done entries: the live Top 3 and the done
# entries assess_finalize carries forward once they leave it.
ACTION_LISTS = ("actions", "completed")

_SHA = re.compile(r"[0-9a-fA-F]{7,64}")


def _git(repo_root: Path, *args: str) -> subprocess.CompletedProcess[str] | None:
    """Run git in ``repo_root`` with literal pathspecs; None when git cannot run."""
    try:
        return subprocess.run(
            ["git", "--literal-pathspecs", "-C", str(repo_root), *args],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            check=False, timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return None


# In-memory marker on a prior entry read from ``completed`` (``tag_completed``);
# never written: ``carried_completed`` strips it.
_FROM_COMPLETED = "_from_completed"


def action_entries(payload: Any, *, tag_completed: bool = False) -> list[dict[str, Any]]:
    """Every dict entry of an actions.json payload's action lists, in file order.

    With ``tag_completed`` an entry from ``completed`` is returned as a copy
    carrying the in-memory ``_FROM_COMPLETED`` marker, which ``reopened_status``
    reads to tell a returning action from one that never left the Top 3.
    """
    if not isinstance(payload, dict):
        return []
    out: list[dict[str, Any]] = []
    for key in ACTION_LISTS:
        entries = payload.get(key)
        if isinstance(entries, list):
            for e in entries:
                if isinstance(e, dict):
                    tag = tag_completed and key == "completed"
                    out.append({**e, _FROM_COMPLETED: True} if tag else e)
    return out


def reopened_status(prior_entry: Mapping[str, Any], status: str) -> str:
    """``reopened`` for a done action back in the Top 3 after leaving it, else ``status``.

    An entry under ``completed`` had left the Top 3; an action that matches it
    is that finding re-flagged (the source changed after the review, or the
    finding regressed), so its old ``done`` must not carry forward.
    """
    return "reopened" if prior_entry.get(_FROM_COMPLETED) and status == "done" else status


def carried_completed(
    prior: Mapping[str, list[dict[str, Any]]], matched: Iterable[Any],
) -> list[dict[str, Any]]:
    """The prior ``done`` entries no new action matched, for actions.json ``completed``.

    ``assess_finalize`` rewrites ``actions`` from this run's Top 3. A done review
    that cleared its finding leaves the Top 3, so without this list the next
    commit's actions.json would lose it and the finding would come back.
    ``prior`` is finalize's carry-forward index (each prior entry under one or
    more keys); ``matched`` holds the prior entries the new actions inherited
    from. Each entry is returned once, in the prior file's order, without the
    in-memory marker. Only reviewable findings are kept: every other finding
    clears on its own signal, so its record need not persist.
    """
    taken = {id(m) for m in matched}
    out: dict[int, dict[str, Any]] = {}
    for entries in prior.values():
        for e in entries:
            if (e.get("status") == "done" and e.get("finding") in REVIEWABLE_FINDINGS
                    and id(e) not in taken):
                out.setdefault(id(e), {k: v for k, v in e.items() if k != _FROM_COMPLETED})
    return list(out.values())


def committed_reviews(repo_root: Path) -> dict[tuple[str, str], list[str]]:
    """``(finding, path) -> [completed_sha, ...]`` from the actions.json at HEAD.

    Only ``done`` entries for a reviewable finding with a hex ``completed_sha``
    count; each names every path in its ``files``. Empty when HEAD carries no
    readable ``.assess/actions.json``.
    """
    shown = _git(repo_root, "show", "HEAD:./.assess/actions.json")
    if shown is None or shown.returncode != 0:
        return {}
    try:
        payload = json.loads(shown.stdout)
    except json.JSONDecodeError:
        return {}
    reviews: dict[tuple[str, str], list[str]] = {}
    for entry in action_entries(payload):
        finding, sha, files = entry.get("finding"), entry.get("completed_sha"), entry.get("files")
        if (entry.get("status") != "done" or finding not in REVIEWABLE_FINDINGS
                or not isinstance(sha, str) or not _SHA.fullmatch(sha)
                or not isinstance(files, list)):
            continue
        for path in files:
            if isinstance(path, str) and path:
                reviews.setdefault((finding, path), []).append(sha)
    return reviews


def unchanged_since(repo_root: Path, sha: str, path: str) -> str | None:
    """The full sha when ``sha`` is an ancestor of HEAD and no commit since touched ``path``."""
    resolved = _git(repo_root, "rev-parse", "--verify", "--quiet", f"{sha}^{{commit}}")
    if resolved is None or resolved.returncode != 0:
        return None
    full = resolved.stdout.strip()
    ancestor = _git(repo_root, "merge-base", "--is-ancestor", full, "HEAD")
    if ancestor is None or ancestor.returncode != 0:
        return None
    touched = _git(repo_root, "rev-list", "-1", f"{full}..HEAD", "--", path)
    if touched is None or touched.returncode != 0 or touched.stdout.strip():
        return None
    return full


def review_note(sha: str) -> str:
    """The supportive note a reviewed path carries."""
    return (
        f"reviewed at {sha}: the source has not changed since, so it is left out of "
        "the attention list and the Top 3 until a commit changes it"
    )


def mark_reviewed(findings: list[DerivedFinding], repo_root: Path) -> None:
    """Add a ``reviewed`` list to each reviewable finding whose paths a review clears."""
    reviews = committed_reviews(repo_root)
    if not reviews:
        return
    for finding in findings:
        if finding["name"] not in REVIEWABLE_FINDINGS:
            continue
        reviewed: list[ReviewedPath] = []
        for path in finding["paths"]:
            for sha in reviews.get((finding["name"], path), []):
                full = unchanged_since(repo_root, sha, path)
                if full is not None:
                    reviewed.append({"path": path, "completed_sha": full,
                                     "note": review_note(full)})
                    break
        if reviewed:
            finding["reviewed"] = reviewed


def open_findings(findings: Iterable[DerivedFinding]) -> list[DerivedFinding]:
    """``findings`` with each row's reviewed paths taken out of its ``paths``.

    What the attention ranking, the prescribed actions, the keyhole summary and
    the rendered path lists read. Rows without a ``reviewed`` list pass through.
    """
    out: list[DerivedFinding] = []
    for finding in findings:
        done = {r["path"] for r in finding.get("reviewed", [])}
        if done:
            finding = {**finding, "paths": [p for p in finding["paths"] if p not in done]}
        out.append(finding)
    return out


def render_reviewed_markdown(
    findings: Iterable[DerivedFinding], display: Callable[[str], str],
) -> str:
    """A "Reviewed" section for the findings markdown, or "" when nothing is reviewed."""
    rows = [
        f"- `{r['path']}` ({display(f['name'])}): {r['note']}"
        for f in findings for r in f.get("reviewed", [])
    ]
    if not rows:
        return ""
    return "\n".join(["", "### Reviewed", "", *rows, ""]) + "\n"
