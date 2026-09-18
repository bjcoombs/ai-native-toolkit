"""Configuration drift: committed platform-config snapshots vs the live setting.

Repositories commit copies of GitHub configuration - ruleset exports, classic
branch-protection exports - and describe them as restorable. When the live
setting moves and the snapshot does not, the snapshot is a lying map: restoring
it silently reverts a deliberate change. This scan diffs each tracked snapshot
against the live value read through ``gh`` and reports every differing
parameter. It is a JSON diff; no judgement is involved.

Snapshots (git-tracked only, paths relative to ``repo_root``):

- **Ruleset**: any ``.github/rulesets/*.json`` file, or any tracked JSON whose
  top-level object holds a ``rules`` array of ``{"type": ...}`` entries.
  Matched to a live ruleset by ``id``, else by ``name``.
- **Classic branch protection**: a JSON file under ``.github/`` whose top-level
  object has any of ``required_status_checks``, ``enforce_admins``,
  ``required_pull_request_reviews``. The branch comes from the export's ``url``
  (``.../branches/<branch>/protection``), else the file stem.

The diff is driven by the snapshot: every key it records is compared; keys only
the live API returns (metadata, fields the export left out) are not drift.
Ids, timestamps and links are never reported. Rules are compared by ``type``,
in both directions - a rule added or dropped live is drift.

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
    rules = doc.get("rules") if isinstance(doc, dict) else None
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
        in_rulesets_dir = rel.startswith(".github/rulesets/") and rel.count("/") == 2
        doc = _load_json(path)
        if doc is None:
            continue
        if (in_rulesets_dir and isinstance(doc, dict)) or _is_ruleset(doc):
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
            if k == "rules" and isinstance(tracked[k], list) and isinstance(live.get(k), list):
                out += _diff_rules(tracked[k], live[k], sub)
            else:
                out += diff_values(tracked[k], live.get(k), sub)
        return out
    if isinstance(tracked, list) and isinstance(live, list) and len(tracked) == len(live):
        out = []
        for i, (t, lv) in enumerate(zip(tracked, live)):
            out += diff_values(t, lv, f"{key}[{i}]")
        return out
    return [] if tracked == live else [(key, tracked, live)]


def _diff_rules(tracked: list, live: list, key: str) -> list[tuple[str, Any, Any]]:
    by_type_t = {r.get("type"): r for r in tracked if isinstance(r, dict)}
    by_type_l = {r.get("type"): r for r in live if isinstance(r, dict)}
    out: list[tuple[str, Any, Any]] = []
    for rtype, rule in by_type_t.items():
        sub = f"{key}[{rtype}]"
        if rtype not in by_type_l:
            out.append((sub, rule, None))
        else:
            out += diff_values(rule, by_type_l[rtype], sub)
    for rtype, rule in by_type_l.items():
        if rtype not in by_type_t:
            out.append((f"{key}[{rtype}]", None, rule))
    return out


def _live_ruleset(slug: str, doc: dict, summaries: list) -> dict | None:
    tid, tname = doc.get("id"), doc.get("name")
    match = next((s for s in summaries if tid is not None and s.get("id") == tid), None)
    if match is None:
        match = next((s for s in summaries if tname and s.get("name") == tname), None)
    if match is None or match.get("id") is None:
        return None
    detail = gh_api(f"repos/{slug}/rulesets/{match['id']}")
    return detail if isinstance(detail, dict) else None


def _live_protection(slug: str, branch: str) -> dict | None:
    try:
        live = gh_api(f"repos/{slug}/branches/{quote(branch, safe='')}/protection")
    except GhUnavailable as e:
        # GitHub answers 404 "Branch not protected" for an unprotected branch;
        # the snapshot says it is protected, so that is drift, not an outage.
        if e.reason.startswith("not_found") and "not protected" in e.reason.lower():
            return None
        raise
    return live if isinstance(live, dict) else None


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
                live = _live_ruleset(repo.slug, snap["doc"], summaries)
                missing = ("ruleset", snap["doc"].get("name") or snap["doc"].get("id"), None)
            else:
                live = _live_protection(repo.slug, snap["branch"])
                missing = ("branch_protection", True, None)
            diffs = [missing] if live is None else diff_values(snap["doc"], live)
            entries += [
                {"file": snap["file"], "key": k, "tracked": t, "live": lv}
                for k, t, lv in diffs
            ]
    except GhUnavailable as e:
        return {**unavailable(e.reason), "snapshots": listed}
    return {"available": True, "entries": entries, "snapshots": listed}
