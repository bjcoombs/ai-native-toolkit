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
Ids, timestamps and links are never reported. Lists are sets, not sequences.
A write-shape payload lists restricted users, teams and apps as plain names
(``"users": ["octocat"]``) where the read returns objects; the objects are
projected onto ``login`` / ``slug`` / ``name`` and both sides compare as names.
A changed scalar list is one entry whose ``tracked`` is ``{"count", "removed",
"sample"}`` and whose ``live`` is ``{"count", "added", "sample"}``: counts of the
list and of the names that left or joined it, plus at most ``MAX_SAMPLE`` of
those names, never the whole live list. Lists of objects pair items by identity
(``login``, ``slug``, ``type``, ``context``, ``actor_type``/``actor_id``,
``name``; a user's or team's ``type`` is a discriminator, so ``login``/``slug`` win) in both
directions, so an item added or dropped live is drift and a reorder is not. An
item present on one side only - and a snapshot key the live response omits (the
protection read drops ``required_pull_request_reviews`` once reviews are turned
off) - is recorded as ``"present"`` / ``"absent"``, never as the live object, so
live org configuration does not land in the committed wiki. The item's identity
does travel in the ``key`` (``bypass_actors[Team:4821]``); that much is needed
to say which item moved. Repository-level snapshots are matched only against
repository-level rulesets (``includes_parents=false``). A missing live ruleset, an unprotected branch and a branch that no longer
exists are drift entries, not outages.

What is stored, then: scalar values of changed settings (booleans, counts,
enforcement modes), the identity of a paired or one-sided list item in ``key``,
and up to ``MAX_SAMPLE`` added names per changed scalar list. Never stored: a
live object, a whole live list, or more than ``MAX_ENTRIES`` entries.

Block: ``{"available", "entries": [{"file", "key", "tracked", "live"}],
"dropped", "snapshots"}``. Entries are ranked worst first: one-sided
(``"absent"``) entries, then boolean flips, then other value changes; only the
first ``MAX_ENTRIES`` are kept and ``dropped`` counts the rest. No snapshots, or
none that differ, gives ``available: True`` with ``entries: []``. Any GitHub read that fails degrades the whole block to
``{"available": False, "reason"}`` (``no_access`` on HTTP 403) - never a
partial clean result. GitHub access goes through ``gh_cli``.
"""
from __future__ import annotations

import json
import re
from collections import Counter
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

# Entries kept in the block after ranking; the rest are counted in ``dropped``.
MAX_ENTRIES = 10
# Names kept per side of a scalar-list change; the rest are counted only.
MAX_SAMPLE = 3

# The field a read-shape object carries that a write-shape payload lists as a
# plain string: users by ``login``, teams and apps by ``slug``.
_NAME_FIELDS = ("login", "slug", "name")

_BRANCH_FROM_URL = re.compile(r"/branches/(?P<branch>.+)/protection/?$")


# A snapshot must name one of these keys; a file whose text holds none of them
# is skipped without a JSON parse, so a repo with many tracked JSON files pays
# a substring probe per file, not a parse.
_PROBE_KEYS = ('"rules"', '"required_status_checks"', '"enforce_admins"',
               '"required_pull_request_reviews"')


def _load_json(path: Path) -> Any:
    try:
        if path.stat().st_size > MAX_SNAPSHOT_BYTES:
            return None
        text = path.read_text(encoding="utf-8")
        if not any(k in text for k in _PROBE_KEYS):
            return None
        return json.loads(text)
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
            if k not in live:
                # The live response omits the key entirely (e.g. a requirement
                # switched off): removed, not "compared against null".
                was = _normalize(tracked[k])
                if was is None:
                    # A write-shape export disables a block with null; the live
                    # read omits it. Same state - not drift.
                    continue
                gone = "present" if isinstance(was, (dict, list)) else was
                out.append((sub, gone, "absent"))
                continue
            out += diff_values(tracked[k], live[k], sub)
        return out
    if isinstance(tracked, list) and isinstance(live, list):
        return _diff_lists(tracked, live, key)
    if tracked == live:
        return []
    # Scalar-vs-container mismatch (a PUT-shape export disables a block with
    # null; live has the block set): record presence, never the live object,
    # so live org configuration does not reach the committed wiki.
    return [(key, _presence(tracked), _presence(live))]


def _presence(value: Any) -> Any:
    return "present" if isinstance(value, (dict, list)) else value


def _identity(item: Any) -> str | None:
    """The name a list item is paired by, or None when it has none."""
    if not isinstance(item, dict):
        return None
    # login/slug first: users and teams carry `type` too ("User", "organization"),
    # but there it is a class discriminator shared by every item, not an identity.
    for fields in (("login",), ("slug",), ("type",), ("context",),
                   ("actor_type", "actor_id"), ("name",)):
        if all(item.get(f) is not None for f in fields):
            return ":".join(str(item[f]) for f in fields)
    return None


def _canonical(item: Any) -> str:
    return json.dumps(item, sort_keys=True, default=str)


def _as_names(items: list) -> list | None:
    """Project read-shape objects onto the name a write-shape payload lists
    (``restrictions.users: ["octocat"]`` against ``[{"login": "octocat", ...}]``);
    None when an item carries none of the name fields."""
    out = []
    for item in items:
        name = next((item.get(f) for f in _NAME_FIELDS
                     if isinstance(item, dict) and isinstance(item.get(f), str)), None)
        if name is None:
            return None
        out.append(name)
    return out


def _scalar_list_change(tracked: list, live: list, key: str) -> list[tuple[str, Any, Any]]:
    """Scalar lists as multisets. A change is recorded as what was removed from
    and added to the tracked list - counts plus a sorted sample of at most
    ``MAX_SAMPLE`` names per side - never the whole live list."""
    t, lv = Counter(map(_canonical, tracked)), Counter(map(_canonical, live))
    if t == lv:
        return []
    removed = sorted((t - lv).elements())
    added = sorted((lv - t).elements())
    def sample(xs: list[str]) -> list[Any]:
        return [json.loads(x) for x in xs[:MAX_SAMPLE]]

    return [(key,
             {"count": len(tracked), "removed": len(removed), "sample": sample(removed)},
             {"count": len(live), "added": len(added), "sample": sample(added)})]


def _is_scalar(x: Any) -> bool:
    return not isinstance(x, (dict, list))


def _diff_lists(tracked: list, live: list, key: str) -> list[tuple[str, Any, Any]]:
    """Lists in GitHub configuration are sets: compare without regard to order."""
    if all(_is_scalar(x) for x in [*tracked, *live]):
        return _scalar_list_change(tracked, live, key)
    # Write shape (plain names) on one side, read shape (objects) on the other.
    t_names = tracked if all(isinstance(x, str) for x in tracked) else _as_names(tracked)
    l_names = live if all(isinstance(x, str) for x in live) else _as_names(live)
    mixed = (all(_is_scalar(x) for x in tracked) or all(_is_scalar(x) for x in live))
    if mixed and t_names is not None and l_names is not None:
        return _scalar_list_change(t_names, l_names, key)
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
        return {"available": True, "entries": [], "dropped": 0, "snapshots": []}
    try:
        repo = open_github(repo_root)
        summaries: list | None = None
        entries: list[dict[str, Any]] = []
        for snap in snapshots:
            if snap["kind"] == "ruleset":
                if summaries is None:
                    # Repository-level rulesets only: an inherited org ruleset
                    # is not what a repo snapshot mirrors, and its id does not
                    # resolve on the repo-scoped detail endpoint. 100 is the
                    # API's page maximum and a deliberate ceiling - a repo with
                    # more rulesets of its own than that is out of scope.
                    got = gh_api(
                        f"repos/{repo.slug}/rulesets?per_page=100&includes_parents=false"
                    )
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
    ranked = rank_entries(entries)
    return {"available": True, "entries": ranked[:MAX_ENTRIES],
            "dropped": max(0, len(ranked) - MAX_ENTRIES), "snapshots": listed}


def _severity(entry: dict[str, Any]) -> int:
    """0 = something exists on one side only (a rule, requirement or branch gone
    or added); 1 = a boolean flipped; 2 = any other value change."""
    if "absent" in (entry["tracked"], entry["live"]):
        return 0
    if isinstance(entry["tracked"], bool) or isinstance(entry["live"], bool):
        return 1
    return 2


def rank_entries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Worst first, stable within a tier (path, then snapshot key order).

    The report renders only ``entries[0]``, so the order decides which drift a
    reader sees - it must not be the export file's serialisation order.
    """
    return sorted(entries, key=_severity)
