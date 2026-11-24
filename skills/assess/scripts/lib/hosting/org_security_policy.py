"""The organisation-wide security policy GitHub shows on a repository that has none of its own (#1045).

GitHub falls back to the default community health files of the owner's public ``.github``
repository: a ``SECURITY.md`` there, at its root, in ``.github/`` or in ``docs/``, is shown as the
security policy of every repository of the owner that has none. The repository's commit does not
hold it, so ``lib.contributor_files`` reads this block when it finds no policy in the tree. The
community profile endpoint (``/repos/{owner}/{repo}/community/profile``) does not report a
security file, so the ``.github`` repository is read directly.

A network scan (``network = true``, ``platform_pass = true``): it runs in ``/assess`` with
``gh`` signed in to GitHub and with ``--platform``; a headless run without ``--platform``
reports the ``network`` reason. A remote that is not on github.com (GitLab, Bitbucket, none)
reads ``no_remote``. In every unavailable case ``lib.contributor_files`` reads the policy as not
measured (``org_policy_not_read``), never as missing.

Block (``org_security_policy`` in run-context.json)::

    {"available": True, "found": bool, "path": str | None}  # path inside the owner's .github repo

``found`` is false when the owner has no ``.github`` repository (``not_found``), when it is
private (GitHub uses the default community health files of a public one only, so a member's
``gh`` reading a private one must not count it), or when it holds no security file in those
places. When the tree already holds a policy (``contributor_files.tree_policy``), no call is made:
the block is unavailable with ``repo_policy``, which ``lib.contributor_files`` never reads.
Counts-only output drops ``path``.

Organisation security policy (#1045), network. `scan_org_security_policy(repo_root)` makes no call when
the tree holds a policy (`repo_policy`); otherwise it resolves the
github.com remote (`gh_cli.open_github`), lists the owner's `.github` repository at its root,
`.github/` and `docs/` when the repository is public, and sets `found` for a `SECURITY` file (`.md` or no extension, any case);
a missing `.github` repository reads `found: false`; no github.com remote, no `gh`, no sign-in or an
API error is the `gh_cli.unavailable` block. Tests: `tests/test_contributor_files.py`.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, TypedDict

from lib.contributor_files import tree_policy
from lib.hosting.gh_cli import GhUnavailable, gh_api, open_github, unavailable

PLACES = ("", ".github", "docs")
# The repository holds its own policy, so the owner's is not read (no network call).
REASON_REPO_POLICY = ("repo_policy: the repository holds its own security policy, so the owner's "
                      "shared one is not read")
NAMES = frozenset({"security.md", "security"})


class OrgSecurityPolicyBlock(TypedDict, total=False):
    """``run-context.json`` ``org_security_policy`` (see the module doc)."""

    available: bool
    reason: str
    found: bool
    path: str | None


def _listing(owner: str, place: str) -> list[Any] | None:
    """The owner's ``.github`` repository folder listing; None when the folder is not there."""
    suffix = f"/{place}" if place else ""
    try:
        out = gh_api(f"repos/{owner}/.github/contents{suffix}")
    except GhUnavailable as e:
        if e.reason.startswith("not_found"):
            return None
        raise
    return out if isinstance(out, list) else None


def scan_org_security_policy(repo_root: Path) -> OrgSecurityPolicyBlock | dict[str, Any]:
    """Build the ``org_security_policy`` block (see the module doc)."""
    if tree_policy(repo_root)[0] is not None:
        return unavailable(REASON_REPO_POLICY)
    try:
        repo = open_github(repo_root)
        try:
            meta = gh_api(f"repos/{repo.owner}/.github")
        except GhUnavailable as e:
            if e.reason.startswith("not_found"):
                return {"available": True, "found": False, "path": None}
            raise
        if not isinstance(meta, dict) or meta.get("private") is not False:
            # GitHub uses the default community health files of a public .github only.
            return {"available": True, "found": False, "path": None}
        for place in PLACES:
            listing = _listing(repo.owner, place)
            if listing is None:
                if not place:  # no .github repository at all
                    return {"available": True, "found": False, "path": None}
                continue
            for entry in listing:
                name = entry.get("name") if isinstance(entry, dict) else None
                if isinstance(name, str) and entry.get("type") == "file" and name.lower() in NAMES:
                    return {"available": True, "found": True, "path": f"{place}/{name}".lstrip("/")}
    except GhUnavailable as e:
        return unavailable(e.reason)
    return {"available": True, "found": False, "path": None}
