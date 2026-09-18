"""Configuration drift: committed platform-config snapshots vs the live setting.

Repositories commit copies of GitHub configuration - ruleset exports, classic
branch-protection exports - and describe them as restorable. When the live
setting moves and the snapshot does not, the snapshot is a lying map: restoring
it silently reverts a deliberate change. This scan diffs each tracked snapshot
against the live value read through ``gh`` and reports every differing
parameter. It is a JSON diff; no judgement is involved.

Snapshots (git-tracked only, paths relative to ``repo_root``):

- **Ruleset**: a tracked JSON object with a ``name`` or ``id`` and a top-level
  ``rules`` array of ``{"type": ...}`` entries (the ``.github/rulesets/*.json``
  convention, or anywhere else). A file missing either is not a ruleset export
  and is skipped, never reported. Matched to a live ruleset by ``id``, else by
  ``name``.
- **Classic branch protection**: a JSON file under ``.github/`` whose top-level
  object has any of ``required_status_checks``, ``enforce_admins``,
  ``required_pull_request_reviews``. The branch comes from the export's ``url``
  (``.../branches/<branch>/protection``), else the file stem.

The diff is driven by the snapshot: every key it records is compared; keys only
the live API returns (metadata, fields the export left out) are not drift.
Ids, timestamps and links are never reported. Lists are sets, not sequences:
scalar lists compare sorted, and lists of objects pair items by identity
(``type``, ``context``, ``actor_type``/``actor_id``, ``name``) in both
directions, so an item added or dropped live is drift and a reorder is not. An
item present on one side only is recorded as ``"present"`` / ``"absent"``, never
as the live object, so live org configuration does not land in the committed
wiki. A missing live ruleset, an unprotected branch and a branch that no longer
exists are drift entries, not outages.

Block: ``{"available", "entries": [{"file", "key", "tracked", "live"}],
"snapshots"}``. No snapshots, or none that differ, gives ``available: True``
with ``entries: []``. Any GitHub read that fails degrades the whole block to
``{"available": False, "reason"}`` (``no_access`` on HTTP 403) - never a
partial clean result. GitHub access goes through ``gh_cli``.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import quote

from lib.gh_cli import GhUnavailable, gh_api, open_github, unavailable
from lib.git_churn import tracked_files

# Metadata the API adds or rewrites on every read; never configuration.
IGNORED_KEYS = frozenset({
    "id", "node_id", "created_at", "updated_at",
    "url", "html_url", "contexts_url", "_links", "links",
    "source", "source_type", "current_user_can_bypass",
})

PROTECTION_KEYS = ("required_status_checks", "enforce_admins", "required_pull_request_reviews")

# A snapshot bigger than this is not a hand-kept config export.
MAX_SNAPSHOT_BYTES = 1_000_000

_BRANCH_FROM_URL = re.compile(r"/branches/(?P<branch>.+)/protection/?$")


def _load_json(path: Path) -> Any:
    try:
        if path.stat().st_size > MAX_SNAPSHOT_BYTES:
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None


def _is_ruleset(doc: Any) -> bool:
    if not isinstance(doc, dict) or not (doc.get("name") or doc.get("id") is not None):
        return False
    rules = doc.get("rules")
    return (
        isinstance(rules, list) and bool(rules)
        and all(isinstance(r, dict) and "type" in r for r in rules)
    )


def _is_protection(doc: Any) -> bool:
    return isinstance(doc, dict) and any(k in doc for k in PROTECTION_KEYS)


def find_snapshots(repo_root: Path) -> list[dict[str, Any]]:
    """Tracked snapshots under ``repo_root``, sorted by path.

    Each item: ``{"file", "kind": "ruleset"|"branch_protection", "doc"}``,
    plus ``"branch"`` for a protection export.
    """
    root = repo_root.resolve()
    tracked = tracked_files(root)
    if not tracked:
        return []
    out: list[dict[str, Any]] = []
    for path in sorted(tracked):
        if path.suffix != ".json":
            continue
        try:
            rel = path.relative_to(root).as_posix()
        except ValueError:
            continue
        under_github = rel.startswith(".github/")
        doc = _load_json(path)
        if doc is None:
            continue
        if _is_ruleset(doc):
            out.append({"file": rel, "kind": "ruleset", "doc": doc})
        elif under_github and _is_protection(doc):
            m = _BRANCH_FROM_URL.search(str(doc.get("url") or ""))
            branch = m.group("branch") if m else path.stem
            out.append({"file": rel, "kind": "branch_protection", "doc": doc, "branch": branch})
    return out


def _normalize(value: Any) -> Any:
    """Collapse the ``{"url", "enabled": X}`` read shape to ``X`` so an export
    in the write shape (``"enforce_admins": true``) compares equal."""
    if isinstance(value, dict):
        rest = {k: v for k, v in value.items() if k not in IGNORED_KEYS}
        if set(rest) == {"enabled"}:
            return rest["enabled"]
    return value


def diff_values(tracked: Any, live: Any, key: str = "") -> list[tuple[str, Any, Any]]:
    """``(key_path, tracked, live)`` for every snapshot-recorded value that differs."""
    tracked, live = _normalize(tracked), _normalize(live)
    if isinstance(tracked, dict) and isinstance(live, dict):
        out: list[tuple[str, Any, Any]] = []
        for k in tracked:
            if k in IGNORED_KEYS:
                continue
            sub = f"{key}.{k}" if key else k
            out += diff_values(tracked[k], live.get(k), sub)
        return out
    if isinstance(tracked, list) and isinstance(live, list):
        return _diff_lists(tracked, live, key)
    return [] if tracked == live else [(key, tracked, live)]


def _identity(item: Any) -> str | None:
    """The name a list item is paired by, or None when it has none."""
    if not isinstance(item, dict):
        return None
    for fields in (("type",), ("context",), ("actor_type", "actor_id"), ("name",)):
        if all(item.get(f) is not None for f in fields):
            return ":".join(str(item[f]) for f in fields)
    return None


def _canonical(item: Any) -> str:
    return json.dumps(item, sort_keys=True, default=str)


def _diff_lists(tracked: list, live: list, key: str) -> list[tuple[str, Any, Any]]:
    """Lists in GitHub configuration are sets: compare without regard to order."""
    if all(not isinstance(x, (dict, list)) for x in [*tracked, *live]):
        t_sorted, l_sorted = sorted(tracked, key=_canonical), sorted(live, key=_canonical)
        return [] if t_sorted == l_sorted else [(key, t_sorted, l_sorted)]
    t_ids = [_identity(x) for x in tracked]
    l_ids = [_identity(x) for x in live]
    ids_usable = (
        None not in t_ids and None not in l_ids
        and len(set(t_ids)) == len(t_ids) and len(set(l_ids)) == len(l_ids)
    )
    if not ids_usable:
        # No identity to pair by: compare as a multiset of normalised items and
        # report only the counts, never the live objects themselves.
        t_set = sorted(_canonical(_strip(x)) for x in tracked)
        l_set = sorted(_canonical(_strip(x)) for x in live)
        if t_set == l_set:
            return []
        return [(f"{key}.count", len(tracked), len(live))] if len(tracked) != len(live) \
            else [(f"{key}.items", "differs", "differs")]
    by_t = dict(zip(t_ids, tracked))
    by_l = dict(zip(l_ids, live))
    out: list[tuple[str, Any, Any]] = []
    for ident, item in by_t.items():
        sub = f"{key}[{ident}]"
        if ident not in by_l:
            out.append((sub, "present", "absent"))
        else:
            out += diff_values(item, by_l[ident], sub)
    for ident in by_l:
        if ident not in by_t:
            out.append((f"{key}[{ident}]", "absent", "present"))
    return out


def _strip(value: Any) -> Any:
    """Drop ignored metadata keys at every depth, for multiset comparison."""
    value = _normalize(value)
    if isinstance(value, dict):
        return {k: _strip(v) for k, v in value.items() if k not in IGNORED_KEYS}
    if isinstance(value, list):
        return [_strip(v) for v in value]
    return value


def _live_ruleset(slug: str, doc: dict, summaries: list) -> dict | None:
    tid, tname = doc.get("id"), doc.get("name")
    match = next((s for s in summaries if tid is not None and s.get("id") == tid), None)
    if match is None:
        match = next((s for s in summaries if tname and s.get("name") == tname), None)
    if match is None or match.get("id") is None:
        return None
    detail = gh_api(f"repos/{slug}/rulesets/{match['id']}")
    return detail if isinstance(detail, dict) else None


# A (key, tracked, live) drift entry for a snapshot with nothing live to diff.
Missing = tuple[str, Any, Any]


def _live_protection(slug: str, branch: str) -> dict | Missing:
    try:
        live = gh_api(f"repos/{slug}/branches/{quote(branch, safe='')}/protection")
    except GhUnavailable as e:
        # GitHub answers 404 "Branch not protected" for an unprotected branch;
        # the snapshot says it is protected, so that is drift, not an outage.
        reason = e.reason.lower()
        if reason.startswith("not_found") and "not protected" in reason:
            return ("branch_protection", "present", "absent")
        # A deleted or renamed branch: the snapshot protects a branch that is
        # not there - drift for this snapshot, not an outage for the scan.
        if reason.startswith("not_found") and "branch not found" in reason:
            return ("branch", branch, "absent")
        raise
    return live if isinstance(live, dict) else ("branch_protection", "present", "absent")


def scan_config_drift(repo_root: Path) -> dict[str, Any]:
    """Build the ``config_drift`` run-context block (see module docstring)."""
    snapshots = find_snapshots(repo_root)
    listed = [{"file": s["file"], "kind": s["kind"]} for s in snapshots]
    if not snapshots:
        return {"available": True, "entries": [], "snapshots": []}
    try:
        repo = open_github(repo_root)
        summaries: list | None = None
        entries: list[dict[str, Any]] = []
        for snap in snapshots:
            if snap["kind"] == "ruleset":
                if summaries is None:
                    got = gh_api(f"repos/{repo.slug}/rulesets?per_page=100")
                    summaries = [s for s in got if isinstance(s, dict)] if isinstance(got, list) else []
                found = _live_ruleset(repo.slug, snap["doc"], summaries)
                name = snap["doc"].get("name") or snap["doc"].get("id")
                live: dict | Missing = ("ruleset", name, "absent") if found is None else found
            else:
                live = _live_protection(repo.slug, snap["branch"])
            diffs = [live] if isinstance(live, tuple) else diff_values(snap["doc"], live)
            entries += [
                {"file": snap["file"], "key": k, "tracked": t, "live": lv}
                for k, t, lv in diffs
            ]
    except GhUnavailable as e:
        return {**unavailable(e.reason), "snapshots": listed}
    return {"available": True, "entries": entries, "snapshots": listed}
