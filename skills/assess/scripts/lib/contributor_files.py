"""The files that let an outside contributor, human or agent, send a correct patch (#1045).

A contributing guide says how the project builds, tests and shapes a change it will merge; a
security policy gives a private route for a vulnerability instead of a public issue. Without
them a newcomer guesses, and an agent asked to send a patch has nothing to read. This module
finds them by name in the places the forges read them from, and records issue and pull or
merge request templates as evidence. No model is involved.

The tendency it compensates for is unactioned intent at the project boundary: the
conventions live in the maintainers' heads, so each outside change restates them in review.

**What counts** (``GUIDE_NAMES``, ``POLICY_NAMES`` and ``PLACES``, the root, ``.github/`` and
``docs/``, the folders GitHub reads them from; GitLab reads the root):

- ``guide``: a ``CONTRIBUTING`` file (``.md``, ``.rst``, ``.txt``, ``.adoc`` or no extension,
  any case).
- ``policy``: a ``SECURITY`` file, or a link to GitHub private vulnerability reporting
  (``/security/advisories/new``) in the guide or the root README.
- ``templates`` (evidence only, not part of the condition): an issue template
  (``.github/ISSUE_TEMPLATE/`` or ``ISSUE_TEMPLATE.md``, ``.gitlab/issue_templates/``) and a pull
  or merge request template (``pull_request_template.md`` or ``PULL_REQUEST_TEMPLATE/``,
  ``.gitlab/merge_request_templates/``).

**The owner's shared policy.** GitHub shows the security policy of the owner's public
``.github`` repository on every repository of the owner that has none of its own. The commit does
not hold it, so when the tree holds no policy the scan reads ``org_security_policy``
(``lib.hosting.org_security_policy``, a network scan): a policy there reads present, with
``source: "org"``; an owner with none reads absent. When that block is unavailable (offline,
headless without ``--platform``, a GitLab or other remote, no ``gh`` sign-in, an API error) the
``policy`` reading is ``{"available": False, "reason": "org_policy_not_read: ..."}``, so the
condition reads not measured, never a false "missing". A policy in the tree needs no network.

**The condition** (``contributor-files.1``) reads ``guide.present`` and ``policy.present``. No
guide is unmet whatever the policy; a guide with an unread owner policy is not measured
(``org_policy_not_read``). ``ready`` repeats the verdict: true, false, or null while the policy is
not measured. A knowledge base (``archetype`` ``knowledge-base``, ``lib.keyhole.archetype``) takes
no code patches, so the block is ``{"available": False, "reason": "knowledge_base: ..."}`` and the
condition reads not measured. ``/assess`` has no mirror archetype (the ``/fleet`` labels are
display only), so a mirror reads as the software it holds.

Block (``contributor_files`` in run-context.json)::

    {"available": True, "ready": bool | None,
     "guide": {"present": bool, "path": str | None},
     "policy": {"available": True, "present": bool, "path": str | None,
                "kind": "file" | "private_reporting" | "org_file" | None,
                "source": "repo" | "org" | None}
               | {"available": False, "reason": "org_policy_not_read: ..."},
     "issue_template": bool, "change_template": bool}

Counts-only output drops the paths, or hashes them under ``--hash-paths``, and keeps the
booleans, ``kind``, ``source`` and the reason code.

Contributor files (#1045), no model. `scan_contributor_files(repo_root, archetype,
org_security_policy)` finds a `CONTRIBUTING` guide and a `SECURITY` policy (or a
`/security/advisories/new` link in the guide or README) at the root, in `.github/` or in `docs/`;
with no policy in the tree it reads the owner's shared one from `org_security_policy`, and an
unread one makes the policy not measured (`org_policy_not_read`). Templates are evidence; a
`knowledge-base` archetype is unavailable (`knowledge_base`). Tests: `tests/test_contributor_files.py`.
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal, TypedDict

PLACES = ("", ".github", "docs")
GUIDE_NAMES = ("contributing",)
POLICY_NAMES = ("security",)
EXTENSIONS = ("", ".md", ".rst", ".txt", ".adoc")
KNOWLEDGE_BASE = "knowledge-base"
REASON_KNOWLEDGE_BASE = "knowledge_base"
_PRIVATE_REPORTING = re.compile(r"/security/advisories/new\b")
_READMES = ("README.md", "README.rst", "README", "readme.md")

REASON_ORG_NOT_READ = "org_policy_not_read"
PolicyKind = Literal["file", "private_reporting", "org_file"]
PolicySource = Literal["repo", "org"]


class GuideReading(TypedDict):
    present: bool
    path: str | None  # repository-relative


class PolicyReading(TypedDict, total=False):
    available: bool  # false: the owner's shared policy could not be read (org_policy_not_read)
    reason: str
    present: bool
    path: str | None  # repository-relative, or inside the owner's .github repository for "org"
    kind: PolicyKind | None
    source: PolicySource | None


class ContributorFilesBlock(TypedDict, total=False):
    """``run-context.json`` ``contributor_files`` (see the module doc)."""

    available: bool
    reason: str
    ready: bool | None  # null while the policy is not measured
    guide: GuideReading
    policy: PolicyReading
    issue_template: bool
    change_template: bool


def _find(root: Path, stems: tuple[str, ...]) -> str | None:
    """The first file named ``<stem><ext>`` (any case) in ``PLACES``, repository-relative."""
    for place in PLACES:
        folder = root / place if place else root
        if not folder.is_dir():
            continue
        names = {p.name.lower(): p for p in folder.iterdir() if p.is_file()}
        for stem in stems:
            for ext in EXTENSIONS:
                hit = names.get(stem + ext)
                if hit is not None:
                    return hit.relative_to(root).as_posix()
    return None


def _links_private_reporting(root: Path, files: list[str]) -> str | None:
    for rel in files:
        try:
            if _PRIVATE_REPORTING.search((root / rel).read_text(encoding="utf-8", errors="replace")):
                return rel
        except OSError:
            continue
    return None


def _any_file(folder: Path) -> bool:
    return folder.is_dir() and any(p.is_file() for p in folder.iterdir())


def _templates(root: Path) -> tuple[bool, bool]:
    gh = root / ".github"
    gh_names = {p.name.lower() for p in gh.iterdir()} if gh.is_dir() else set()
    issue = (_any_file(gh / "ISSUE_TEMPLATE") or "issue_template.md" in gh_names
             or _any_file(root / ".gitlab" / "issue_templates"))
    change = ("pull_request_template.md" in gh_names or _any_file(gh / "PULL_REQUEST_TEMPLATE")
              or (root / "pull_request_template.md").is_file() or (root / "docs" / "pull_request_template.md").is_file()
              or _any_file(root / ".gitlab" / "merge_request_templates"))
    return bool(issue), bool(change)


def _org_policy(org: Mapping[str, object] | None) -> PolicyReading:
    """The policy reading when the tree holds none: the owner's shared one, or not measured."""
    if isinstance(org, Mapping) and org.get("available") is True:
        found = org.get("found") is True
        where = org.get("path")
        return {"available": True, "present": found, "path": where if found and isinstance(where, str) else None,
                "kind": "org_file" if found else None, "source": "org" if found else None}
    why = org.get("reason") if isinstance(org, Mapping) else None
    return {"available": False,
            "reason": f"{REASON_ORG_NOT_READ}: no security policy in the repository, and the owner's "
                      "shared .github repository, whose policy GitHub would show, was not read"
                      + (f" ({why})" if why else "")}


def tree_policy(repo_root: Path) -> tuple[str | None, PolicyKind | None]:
    """The policy the tree holds: a ``SECURITY`` file, else a private-reporting link in the guide or
    README; ``(None, None)`` when there is neither. ``lib.hosting.org_security_policy`` reads it to
    skip the network when the tree holds one."""
    root = repo_root.resolve()
    path = _find(root, POLICY_NAMES)
    if path is not None:
        return path, "file"
    guide = _find(root, GUIDE_NAMES)
    readmes = [name for name in _READMES if (root / name).is_file()]
    link = _links_private_reporting(root, [*([guide] if guide else []), *readmes])
    return (link, "private_reporting") if link else (None, None)


def scan_contributor_files(repo_root: Path, archetype: Mapping[str, Any] | None = None,
                           org_security_policy: Mapping[str, object] | None = None) -> ContributorFilesBlock:
    """Build the ``contributor_files`` block (see the module doc)."""
    if isinstance(archetype, Mapping) and archetype.get("archetype") == KNOWLEDGE_BASE:
        return {"available": False,
                "reason": f"{REASON_KNOWLEDGE_BASE}: a knowledge base takes no code patches, so a "
                          "contributing guide and a security policy are not measured"}
    root = repo_root.resolve()
    guide = _find(root, GUIDE_NAMES)
    path, kind = tree_policy(root)
    policy: PolicyReading = ({"available": True, "present": True, "path": path, "kind": kind, "source": "repo"}
                             if path is not None else _org_policy(org_security_policy))
    ready: bool | None = (False if guide is None else
                          None if policy.get("available") is False else bool(policy.get("present")))
    issue, change = _templates(root)
    return {"available": True, "ready": ready, "guide": {"present": guide is not None, "path": guide},
            "policy": policy, "issue_template": issue, "change_template": change}
