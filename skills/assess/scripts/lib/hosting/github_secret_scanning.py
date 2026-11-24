"""GitHub's own secret scanning and push protection, read from the repository's settings (#1173).

A repository on github.com can be guarded without any file in its commit: GitHub's secret
scanning alerts on a token that lands, and push protection blocks the push that carries one. Both
are repository settings, returned in the ``security_and_analysis`` object of
``GET /repos/{owner}/{repo}``. GitHub returns that object only to a caller with admin rights on
the repository; anyone else gets ``null``. So an unreadable setting is not measured
(``settings_not_readable``), never read as off.

A network scan (``network = true``, ``platform_pass = true``): it runs in ``/assess`` with ``gh``
signed in to GitHub and with ``--platform``; a headless run without ``--platform`` reports the
``network`` reason. A remote that is not on github.com (GitLab, Bitbucket, none) is the
``gh_cli.unavailable`` block, as for every GitHub read. ``lib.secrets_guard`` reads this block for
its level 2: an enabled setting meets it, both settings read as disabled leave it unmet, and an
unavailable block leaves it not measured.

Block (``github_secret_scanning`` in run-context.json)::

    {"available": True, "secret_scanning": bool, "push_protection": bool}
    | {"available": False, "reason": "settings_not_readable: ..."}  # or a gh_cli reason

GitHub secret scanning (#1173), network. `scan_github_secret_scanning(repo_root)` resolves the
github.com remote (`gh_cli.open_github`), reads `GET /repos/{owner}/{repo}` and reports whether
`security_and_analysis.secret_scanning` and `.secret_scanning_push_protection` are `enabled`; a
`null` object (no admin rights) is `settings_not_readable`; no github.com remote, no `gh`, no
sign-in or an API error is the `gh_cli.unavailable` block. Tests:
`tests/test_github_secret_scanning.py`.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, TypedDict

from lib.hosting.gh_cli import GhUnavailable, gh_api, open_github, unavailable

REASON_NOT_READABLE = ("settings_not_readable: GitHub returns a repository's secret scanning and push "
                       "protection settings only to a caller with admin rights on it")


class GithubSecretScanningBlock(TypedDict, total=False):
    """``run-context.json`` ``github_secret_scanning`` (see the module doc)."""

    available: bool
    reason: str
    secret_scanning: bool
    push_protection: bool


def _enabled(settings: dict[str, Any], key: str) -> bool:
    entry = settings.get(key)
    return isinstance(entry, dict) and entry.get("status") == "enabled"


def scan_github_secret_scanning(repo_root: Path) -> GithubSecretScanningBlock | dict[str, Any]:
    """Build the ``github_secret_scanning`` block (see the module doc)."""
    try:
        repo = open_github(repo_root)
        meta = gh_api(f"repos/{repo.owner}/{repo.name}")
    except GhUnavailable as e:
        return unavailable(e.reason)
    settings = meta.get("security_and_analysis") if isinstance(meta, dict) else None
    if not isinstance(settings, dict):
        return unavailable(REASON_NOT_READABLE)
    return {"available": True, "secret_scanning": _enabled(settings, "secret_scanning"),
            "push_protection": _enabled(settings, "secret_scanning_push_protection")}
