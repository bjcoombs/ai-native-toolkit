"""Find the committed platform-config snapshots: ruleset and branch-protection exports (#856).

Split out of ``lib.hosting.config_drift``, which diffs each snapshot against the live
setting through ``gh`` or ``glab``, so an offline reader (``lib.ci.review_rules``) can find
the same files without reaching a platform client. ``find_snapshots(repo_root)`` lists the
tracked snapshots, sorted by path, as ``{"file", "kind", "doc"}`` (plus ``"branch"`` for a
protection export); the shapes are described in ``lib.hosting.config_drift``'s docstring. A
file over ``MAX_SNAPSHOT_BYTES``, one holding none of the snapshot keys, or JSON that does not
parse is skipped. Makes no network call. Tests: ``tests/test_config_drift.py``,
``tests/test_review_rules.py``.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from lib.history.git_churn import tracked_files

PROTECTION_KEYS = ("required_status_checks", "enforce_admins", "required_pull_request_reviews")
GITLAB_PROTECTION_KEYS = ("push_access_levels", "merge_access_levels")

# A snapshot bigger than this is not a hand-kept config export.
MAX_SNAPSHOT_BYTES = 1_000_000

_BRANCH_FROM_URL = re.compile(r"/branches/(?P<branch>.+)/protection/?$")

# A snapshot must name one of these keys; a file whose text holds none of them
# is skipped without a JSON parse, so a repo with many tracked JSON files pays
# a substring probe per file, not a parse.
_PROBE_KEYS = ('"rules"', '"required_status_checks"', '"enforce_admins"',
               '"required_pull_request_reviews"', '"push_access_levels"',
               '"merge_access_levels"')


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


def _is_gitlab_protection(doc: Any) -> bool:
    return isinstance(doc, dict) and any(k in doc for k in GITLAB_PROTECTION_KEYS)


def find_snapshots(repo_root: Path) -> list[dict[str, Any]]:
    """Tracked snapshots under ``repo_root``, sorted by path.

    Each item: ``{"file", "kind", "doc"}`` with kind ``ruleset``,
    ``branch_protection`` or ``gitlab_protected_branch``, plus ``"branch"``
    for a protection export.
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
        under_gitlab = rel.startswith(".gitlab/")
        doc = _load_json(path)
        if doc is None:
            continue
        if _is_ruleset(doc):
            out.append({"file": rel, "kind": "ruleset", "doc": doc})
        elif under_github and _is_protection(doc):
            m = _BRANCH_FROM_URL.search(str(doc.get("url") or ""))
            branch = m.group("branch") if m else path.stem
            out.append({"file": rel, "kind": "branch_protection", "doc": doc, "branch": branch})
        elif under_gitlab and _is_gitlab_protection(doc):
            name = doc.get("name")
            branch = name if isinstance(name, str) and name else path.stem
            out.append({"file": rel, "kind": "gitlab_protected_branch", "doc": doc,
                        "branch": branch})
    return out
