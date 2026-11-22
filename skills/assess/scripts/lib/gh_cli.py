"""Shared, optional GitHub reads for the /assess deterministic core.

Some signals need the live platform state a repository's files only describe:
the live ruleset a committed snapshot claims to mirror, the merged pull requests
a review policy governs. This module is the one way the core reaches GitHub, so
every scan that does degrades the same way.

Contract (every caller relies on it):

- GitHub is reached only through the ``gh`` binary on ``PATH``, run as a
  subprocess. No direct HTTP, no token read from the environment: ``gh`` owns
  authentication. JSON is parsed here in Python; ``--jq`` and ``--template``
  are never passed.
- Order of work: resolve the GitHub remote from git first, and give up without
  invoking ``gh`` when there is none; then the auth probe; then the calls.
- Every failure raises :class:`GhUnavailable` carrying a non-empty reason, which
  the caller turns into ``{"available": False, "reason": ...}`` via
  :func:`unavailable`. An API refusal (HTTP 403, in either form ``gh`` prints
  it: ``(HTTP 403)`` or ``HTTP 403: ...``) gives a reason starting
  ``no_access``, as GitLab does - never a clean result; a rate limit (HTTP 429, or a 403
  saying "rate limit"), still refused after the backoff, gives ``rate_limited``.
- ``gh`` runs through ``lib.platform_cli.run_cli``: stdin closed, prompts
  disabled, a bounded 429 backoff, and the per-run response cache when
  ``fleet --platform`` turns it on. A read whose response holds review or
  comment bodies passes ``cache=False`` (``gh_json(..., cache=False)``), so no
  body is written to disk (#778).

Typical use::

    try:
        repo = open_github(repo_root)            # remote, then auth
        rulesets = gh_api(f"repos/{repo.slug}/rulesets")
    except GhUnavailable as e:
        return unavailable(e.reason)

Pure subprocess + stdlib; imports no orchestrator.
"""
from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lib.platform_cli import rate_limited, run_cli

# Cap every gh call so a hung network read or credential prompt degrades the
# signal to unavailable instead of stalling the assessment.
GH_TIMEOUT_SECONDS = 20

# github.com remotes in the three URL forms git accepts: https, scp-like ssh,
# and ssh:// (with an optional user and port).
_REMOTE_RE = re.compile(
    r"^(?:https?://(?:[^@/]+@)?github\.com/"
    r"|(?:[^@/]+@)?github\.com:"
    r"|ssh://(?:[^@/]+@)?github\.com(?::\d+)?/)"
    r"(?P<owner>[A-Za-z0-9_.-]+)/(?P<name>[A-Za-z0-9_.-]+?)(?:\.git)?/?$"
)

# ``gh`` prints a REST status as ``Not Found (HTTP 404)`` and a GraphQL one (``gh pr
# list``) as ``HTTP 403: ...``; both forms map the same way.
_HTTP_STATUS_RE = re.compile(r"\bHTTP (\d{3})\b")


class GhUnavailable(Exception):
    """A GitHub read could not be made or was refused; ``reason`` says why."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class GithubRepo:
    owner: str
    name: str

    @property
    def slug(self) -> str:
        return f"{self.owner}/{self.name}"


# The reason a platform read gets in a headless run without ``--platform`` (lib/signals.py).
NETWORK_REASON = ("network: reads GitHub or GitLab through gh or glab; the headless runner makes no "
                  "network calls without --platform")


def unavailable(reason: str) -> dict[str, Any]:
    """The degraded block shape every gh-backed scan emits on failure."""
    return {"available": False, "reason": reason or "unavailable"}


def parse_github_remote(url: str) -> GithubRepo | None:
    """``owner/name`` from a github.com remote URL, or None for any other host."""
    m = _REMOTE_RE.match(url.strip())
    if not m:
        return None
    return GithubRepo(owner=m.group("owner"), name=m.group("name"))


def git_remote_url(repo_root: Path) -> str | None:
    """The URL of the repository's ``origin`` remote, else of its sole remote.

    Pure git; never invokes ``gh`` or ``glab``. None when the directory is not a
    git repository or has no single remote to choose. Shared with ``gitlab_cli``
    so both providers read the same remote.
    """
    def _git(*args: str) -> str | None:
        try:
            proc = subprocess.run(
                ["git", "-C", str(repo_root), *args],
                capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=GH_TIMEOUT_SECONDS,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return None
        return proc.stdout.strip() if proc.returncode == 0 else None

    url = _git("remote", "get-url", "origin")
    if not url:
        remotes = (_git("remote") or "").split()
        if len(remotes) != 1:
            return None
        url = _git("remote", "get-url", remotes[0])
    return url or None


def resolve_github_remote(repo_root: Path) -> GithubRepo | None:
    """The repository's github.com remote (``origin``, else the sole remote).

    Pure git; never invokes ``gh``. None when the directory is not a git
    repository, has no remote, or its remote is not on github.com.
    """
    url = git_remote_url(repo_root)
    return parse_github_remote(url) if url else None


def _run_gh(args: list[str], timeout: int = GH_TIMEOUT_SECONDS, cache: bool = True) -> str:
    """Run ``gh`` and return stdout, raising GhUnavailable with a mapped reason.
    ``cache=False`` keeps the response out of the ``--platform`` response cache."""
    cmd = ["gh", *args]
    shown = " ".join(cmd)
    try:
        proc = run_cli(cmd, timeout, cache)
    except FileNotFoundError:
        raise GhUnavailable("gh_not_installed: the gh CLI is not on PATH") from None
    except subprocess.TimeoutExpired:
        raise GhUnavailable(
            f"gh_timeout: `{shown}` gave no answer within {timeout}s"
        ) from None
    if proc.returncode == 0:
        return proc.stdout
    stderr = (proc.stderr or "").strip()
    detail = stderr.splitlines()[-1] if stderr else f"exit {proc.returncode}"
    status = _HTTP_STATUS_RE.findall(stderr)
    if rate_limited(stderr):  # a 429, or GitHub's rate-limit 403, still limited after the backoff
        raise GhUnavailable(f"rate_limited: `{shown}` was still rate limited after retries ({detail})")
    if status and status[-1] == "403":
        raise GhUnavailable(f"no_access: `{shown}` was refused ({detail})")
    if status and status[-1] == "404":
        raise GhUnavailable(f"not_found: `{shown}` ({detail})")
    raise GhUnavailable(f"gh_error: `{shown}` failed ({detail})")


def open_github(repo_root: Path, timeout: int = GH_TIMEOUT_SECONDS) -> GithubRepo:
    """Resolve the remote, then confirm ``gh`` is authenticated.

    Raises GhUnavailable (without invoking ``gh``) when there is no github.com
    remote, and when ``gh`` is missing or not logged in.
    """
    repo = resolve_github_remote(repo_root)
    if repo is None:
        raise GhUnavailable("no_remote: no github.com remote to compare against")
    try:
        _run_gh(["auth", "status", "--hostname", "github.com"], timeout)
    except GhUnavailable as e:
        if e.reason.startswith("gh_not_installed"):
            raise
        raise GhUnavailable(f"not_authenticated: gh is not logged in ({e.reason})") from None
    return repo


def _parse(out: str, shown: str) -> Any:
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        raise GhUnavailable(f"gh_bad_json: `{shown}` did not return JSON") from None


def gh_api(path: str) -> Any:
    """``gh api <path>`` parsed as JSON (a REST GET)."""
    return _parse(_run_gh(["api", path]), f"gh api {path}")


def gh_json(args: list[str], timeout: int = GH_TIMEOUT_SECONDS, cache: bool = True) -> Any:
    """Any other ``gh`` command whose output is JSON, e.g.
    ``["pr", "list", "--repo", slug, "--state", "merged", "--json", "number"]``.
    ``timeout`` shortens the per-call cap for an optional probe. ``cache=False``
    reads it in memory only: a response holding review or comment bodies is
    never written to the ``--platform`` response cache."""
    return _parse(_run_gh(list(args), timeout, cache), "gh " + " ".join(args))
