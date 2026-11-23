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

``ready`` is true when the guide and the policy are both present. An organisation-wide policy in
the organisation's ``.github`` repository, which GitHub also shows on the repository, is not
read: the scan reads the commit only, with no network, so such a repository reads unmet. A knowledge base
(``archetype`` ``knowledge-base``, ``lib.keyhole.archetype``) takes no code patches, so the
block is ``{"available": False, "reason": "knowledge_base: ..."}`` and the condition reads not
measured, never unmet. ``/assess`` has no mirror archetype (the ``/fleet`` labels are display
only), so a mirror reads as the software it holds.

Block (``contributor_files`` in run-context.json)::

    {"available": True, "ready": bool,
     "guide": str | None, "policy": str | None,     # the file found, repository-relative
     "policy_kind": "file" | "private_reporting" | None,
     "issue_template": bool, "change_template": bool}

Counts-only output drops the paths, or hashes them under ``--hash-paths``, and keeps the
booleans and ``policy_kind``.

Contributor files (#1045), no model. `scan_contributor_files(repo_root, archetype)` finds a
`CONTRIBUTING` guide and a `SECURITY` policy (or a `/security/advisories/new` link in the guide
or README) at the root, in `.github/` or in `docs/`, records issue and change templates as
evidence, and sets `ready` when guide and policy are both present; a `knowledge-base` archetype
is unavailable (`knowledge_base`). Tests: `tests/test_contributor_files.py`.
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

PolicyKind = Literal["file", "private_reporting"]


class ContributorFilesBlock(TypedDict, total=False):
    """``run-context.json`` ``contributor_files`` (see the module doc)."""

    available: bool
    reason: str
    ready: bool
    guide: str | None
    policy: str | None
    policy_kind: PolicyKind | None
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


def scan_contributor_files(repo_root: Path, archetype: Mapping[str, Any] | None = None) -> ContributorFilesBlock:
    """Build the ``contributor_files`` block (see the module doc)."""
    if isinstance(archetype, Mapping) and archetype.get("archetype") == KNOWLEDGE_BASE:
        return {"available": False,
                "reason": f"{REASON_KNOWLEDGE_BASE}: a knowledge base takes no code patches, so a "
                          "contributing guide and a security policy are not measured"}
    root = repo_root.resolve()
    guide = _find(root, GUIDE_NAMES)
    policy = _find(root, POLICY_NAMES)
    kind: PolicyKind | None = "file" if policy else None
    if policy is None:
        readmes = [name for name in _READMES if (root / name).is_file()]
        policy = _links_private_reporting(root, [*([guide] if guide else []), *readmes])
        kind = "private_reporting" if policy else None
    issue, change = _templates(root)
    return {"available": True, "ready": guide is not None and policy is not None, "guide": guide,
            "policy": policy, "policy_kind": kind, "issue_template": issue, "change_template": change}
