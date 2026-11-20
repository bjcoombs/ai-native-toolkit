"""Shared, optional GitLab reads for the /assess deterministic core.

The GitLab side of the platform seam ``gh_cli`` opened for GitHub, with the
same contract:

- GitLab is reached only through the ``glab`` binary on ``PATH``, run as a
  subprocess with ``--hostname`` on every call. No direct HTTP, no token read
  from the environment: ``glab`` owns authentication. JSON is parsed here.
- Every call is capped (``GLAB_TIMEOUT_SECONDS``); the self-managed host probe
  has a shorter cap (``GLAB_PROBE_TIMEOUT_SECONDS``).
- Every failure raises :class:`lib.gh_cli.GhUnavailable` with a non-empty
  reason, which the caller turns into ``{"available": False, "reason": ...}``
  via :func:`lib.gh_cli.unavailable`. HTTP 403 gives ``no_access`` and 404
  ``not_found``; any other non-zero exit is ``glab_error``. A refusal is never
  read as a clean result.

Provider detection (:func:`open_gitlab`), from the same remote ``gh_cli``
reads (``origin``, else the sole remote):

- a github.com remote, or none, is not GitLab: ``glab`` is never invoked;
- a ``gitlab.com`` remote is GitLab; ``glab`` must then be installed and
  authenticated for the host (the preflight ``ghsync`` runs), or the read
  degrades with ``glab_not_installed`` / ``not_authenticated`` /
  ``unreachable``;
- any other host is GitLab only when ``glab auth status --hostname <host>``
  succeeds. Any probe failure means "not GitLab", so the caller degrades
  exactly as it did before this seam existed.

Protected branches (:func:`branch_protection`): the rule covering a branch is
read from ``projects/<id>/protected_branches``. GitLab combines every rule
that matches a branch, and an exact name does not override a wildcard: the
rule named exactly as the branch and every rule whose name read as a wildcard
(``*`` is GitLab's only wildcard) matches cover it together, merged as GitLab
applies them (:func:`merge_rules`), never one picked by order. A merge that
includes an exact rule keeps that rule's ``name`` (a snapshot of the rule
names it alone); wildcards alone join their names. A name holding ``*`` is
answered only by the rule of that name. "Unprotected" is said only
from a readable, complete list (fewer than ``PROTECTED_PAGE`` rules) with no
covering rule. A full page (which may continue) without an exact rule asks for
the branch by name (``protected_branches/<branch>``, the ``ghreport``
fallback), so an exact rule on a later page joins the wildcards on the first;
when that read has no answer a wildcard cover from the page stands, and
without one the read raises ``truncated``. A 403 or 404 on GitLab usually means
"no access", so it raises like any other refusal, never "unprotected".

Typical use::

    try:
        project = open_gitlab(repo_root)        # None: not a GitLab remote
        if project is not None:
            mrs = glab_api(project, f"projects/{project.encoded}/merge_requests")
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
from urllib.parse import quote

from lib.gh_cli import GhUnavailable, git_remote_url

GITLAB_COM = "gitlab.com"

# Cap every glab call, as gh_cli caps gh.
GLAB_TIMEOUT_SECONDS = 20

# The self-managed host probe runs on every non-GitHub remote, where the scan
# made no network call before; keep its worst case short.
GLAB_PROBE_TIMEOUT_SECONDS = 8

_GITHUB_HOSTS = frozenset({"github.com", "www.github.com"})

# A host as it may reach argv and a network probe: a DNS name with an optional
# port, nothing else. The remote comes from the assessed (untrusted) repo.
_HOST_RE = re.compile(r"^[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?(?::\d{1,5})?$")

# A project path: two or more segments (nested groups allowed) of the
# characters GitLab permits; a segment may start with a dot but is never
# only dots.
_SEGMENT_RE = re.compile(r"^(?!\.+$)[A-Za-z0-9_.][A-Za-z0-9_.-]*$")

_HTTP_REMOTE_RE = re.compile(r"^(?P<host>https?://[^/]+)/(?P<path>.+)$")
_SSH_REMOTE_RE = re.compile(
    r"^ssh://(?:[^@/]+@)?(?P<host>[^/:@]+)(?::\d+)?/(?P<path>.+)$"
)
_SCP_REMOTE_RE = re.compile(r"^(?:[^@/]+@)?(?P<host>[^/:@]+):(?P<path>[^/].*)$")

_HTTP_STATUS_RE = re.compile(r"\(HTTP (\d{3})\)")
_STATUS_TEXT_RE = re.compile(r"\b(401|403|404) (?:Unauthorized|Forbidden|[A-Za-z ]*Not Found)")

# ghsync's reachability patterns (ghsync.sh gitlab_preflight).
_UNREACHABLE_RE = re.compile(
    r"no such host|dial tcp|connection refused|timed? ?out|could not resolve"
    r"|network is unreachable|tls handshake|i/o timeout",
    re.IGNORECASE,
)


# GitLab's REST page maximum; a protected-branch list this long may continue.
PROTECTED_PAGE = 100


@dataclass(frozen=True)
class GitlabProject:
    host: str
    path: str  # path_with_namespace, nested groups included

    @property
    def encoded(self) -> str:
        """The URL-encoded project id ``glab api`` takes (``grp%2Fsub%2Fapp``)."""
        return quote(self.path, safe="")


def normalize_host(raw: str) -> str:
    """A host as ``ghsync`` normalises ``--host``: scheme and any path dropped.

    ``https://gitlab.example.com/`` and ``gitlab.example.com/x`` both give
    ``gitlab.example.com``; a port is kept. Lower-cased; empty when nothing
    is left.
    """
    host = raw.strip()
    for scheme in ("http://", "https://"):
        if host.lower().startswith(scheme):
            host = host[len(scheme):]
            break
    host = host.split("/", 1)[0]
    return host.rsplit("@", 1)[-1].lower()


def parse_remote(url: str) -> tuple[str, str] | None:
    """``(host, path)`` from a git remote URL in any of git's three forms.

    https/http keep a port in the host (a self-managed API port); ssh forms
    drop it (an ssh port is not the API's). None for a URL that does not parse
    or whose host or path fails the strict character checks.
    """
    url = url.strip()
    host: str
    m = _HTTP_REMOTE_RE.match(url)
    if m:
        host = normalize_host(m.group("host"))
    else:
        m = _SSH_REMOTE_RE.match(url) or _SCP_REMOTE_RE.match(url)
        if not m:
            return None
        host = m.group("host").lower()
    path = m.group("path").rstrip("/")
    if path.endswith(".git"):
        path = path[: -len(".git")]
    segments = path.split("/")
    if not _HOST_RE.match(host) or len(segments) < 2:
        return None
    if not all(_SEGMENT_RE.match(s) for s in segments):
        return None
    return host, path


def _status(stderr: str) -> str | None:
    codes = _HTTP_STATUS_RE.findall(stderr) or _STATUS_TEXT_RE.findall(stderr)
    return codes[-1] if codes else None


def _run_glab(args: list[str], timeout: int = GLAB_TIMEOUT_SECONDS) -> str:
    """Run ``glab`` and return stdout, raising GhUnavailable with a mapped reason."""
    cmd = ["glab", *args]
    shown = " ".join(cmd)
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        raise GhUnavailable("glab_not_installed: the glab CLI is not on PATH") from None
    except subprocess.TimeoutExpired:
        raise GhUnavailable(
            f"glab_timeout: `{shown}` gave no answer within {timeout}s"
        ) from None
    if proc.returncode == 0:
        return proc.stdout
    stderr = (proc.stderr or "").strip()
    detail = stderr.splitlines()[-1] if stderr else f"exit {proc.returncode}"
    status = _status(stderr)
    if status in ("401", "403"):
        raise GhUnavailable(f"no_access: `{shown}` was refused ({detail})")
    if status == "404":
        raise GhUnavailable(f"not_found: `{shown}` ({detail})")
    raise GhUnavailable(f"glab_error: `{shown}` failed ({detail})")


def glab_auth(host: str, timeout: int = GLAB_TIMEOUT_SECONDS) -> None:
    """``glab auth status --hostname <host>``; raises GhUnavailable unless it passes.

    Reasons: ``glab_not_installed``; ``unreachable`` when the probe's output
    names a network failure (or it timed out); ``not_authenticated`` otherwise.
    """
    try:
        _run_glab(["auth", "status", "--hostname", host], timeout)
    except GhUnavailable as e:
        if e.reason.startswith("glab_not_installed"):
            raise
        if e.reason.startswith("glab_timeout") or _UNREACHABLE_RE.search(e.reason):
            raise GhUnavailable(f"unreachable: GitLab host {host} ({e.reason})") from None
        raise GhUnavailable(
            f"not_authenticated: glab is not logged in to {host} ({e.reason})"
        ) from None


def open_gitlab(repo_root: Path) -> GitlabProject | None:
    """The repository's GitLab project, or None when its remote is not GitLab.

    See the module docstring for the detection rules. Raises GhUnavailable only
    for a gitlab.com remote whose ``glab`` is missing, unauthenticated or
    cannot reach the host.
    """
    url = git_remote_url(repo_root)
    parsed = parse_remote(url) if url else None
    if parsed is None:
        return None
    host, path = parsed
    if host in _GITHUB_HOSTS:
        return None
    project = GitlabProject(host=host, path=path)
    if host == GITLAB_COM:
        glab_auth(host)
        return project
    try:
        glab_auth(host, GLAB_PROBE_TIMEOUT_SECONDS)
    except GhUnavailable:
        return None
    return project


def glab_api(project: GitlabProject, path: str) -> Any:
    """``glab api --hostname <host> <path>`` parsed as JSON (a REST GET, one page)."""
    args = ["api", "--hostname", project.host, path]
    out = _run_glab(args)
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        raise GhUnavailable(
            f"glab_bad_json: `glab {' '.join(args)}` did not return JSON"
        ) from None


def branch_pattern(name: str) -> re.Pattern[str]:
    """A GitLab protected-branch name as a regex: ``*`` is the only wildcard."""
    return re.compile("^" + ".*".join(re.escape(p) for p in name.split("*")) + "$")


_GRANTEES = ("user_id", "group_id", "deploy_key_id")


def _merge_levels(lists: list[list[Any]]) -> list[Any]:
    """Access level lists of overlapping rules as the one list GitLab enforces.

    GitLab grants an action when any matching rule's level allows it, so the
    grants are a union. Role grants (no user, group or deploy key) collapse to
    the most permissive one: the lowest non-zero ``access_level`` (a role at or
    above it passes), or ``0`` ("No one") when every role grant is ``0``.
    """
    roles: list[dict[str, Any]] = []
    others: list[Any] = []
    seen: list[Any] = []
    for item in (i for levels in lists for i in levels):
        if not isinstance(item, dict):
            key: Any = item
        elif (isinstance(item.get("access_level"), int)
              and all(item.get(k) is None for k in _GRANTEES)):
            roles.append(item)
            continue
        else:  # the same grant on two rules carries two ids: compare the grant
            key = tuple(item.get(k) for k in ("access_level", *_GRANTEES))
        if key not in seen:
            seen.append(key)
            others.append(item)
    if roles:
        allowed = [r for r in roles if r["access_level"] > 0]
        best = min(allowed or roles, key=lambda r: int(r["access_level"]))
        others.insert(0, best)
    return others


def merge_rules(rules: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The rules covering one branch, merged as GitLab applies them.

    None for no rule, the rule itself for one. For several (in the given
    order; :func:`branch_protection` puts an exact rule first, then wildcards
    in name order): every ``*_access_levels`` list is
    merged by :func:`_merge_levels` (the most permissive level wins),
    ``allow_force_push`` and ``code_owner_approval_required`` are True when any
    rule sets them (GitLab checks each across every matching rule), ``name``
    joins the rule names with ``", "``, and any other key is the first rule's.

    The two flags pull in opposite directions under that one ``any``: force
    push follows the most permissive rule (one rule allowing it allows it),
    while code-owner approval is the exception that follows the most
    restrictive (one rule requiring it requires it).
    """
    if len(rules) < 2:
        return rules[0] if rules else None
    merged = dict(rules[0])
    merged["name"] = ", ".join(str(r["name"]) for r in rules)
    for key in {k for r in rules for k in r if k.endswith("_access_levels")}:
        merged[key] = _merge_levels([r[key] for r in rules if isinstance(r.get(key), list)])
    for flag in ("allow_force_push", "code_owner_approval_required"):
        if any(flag in r for r in rules):
            merged[flag] = any(r.get(flag) is True for r in rules)
    return merged


def protected_branches(project: GitlabProject) -> list[dict[str, Any]]:
    """The first page of the project's protected-branch rules, each with a name.

    Raises GhUnavailable on a refused or malformed read. Read it once and pass
    it to :func:`branch_protection` for every branch a scan asks about.
    """
    got = glab_api(project, f"projects/{project.encoded}/protected_branches"
                            f"?per_page={PROTECTED_PAGE}")
    if not isinstance(got, list):
        raise GhUnavailable("glab_bad_json: the protected branch list was not a list")
    rules = [r for r in got if isinstance(r, dict) and isinstance(r.get("name"), str)]
    if len(rules) != len(got):
        raise GhUnavailable("glab_bad_json: a protected branch has no name")
    return rules


def branch_protection(project: GitlabProject, branch: str,
                      rules: list[dict[str, Any]] | None = None) -> dict[str, Any] | None:
    """The protected-branch rules covering ``branch``, merged by
    :func:`merge_rules` (an exact rule with every matching wildcard), or None
    when unprotected.

    ``rules`` is a :func:`protected_branches` read to reuse; it is read here
    when omitted. See the module docstring for the matching order. A
    ``branch`` holding ``*`` is a rule name, not a branch: only the rule of
    that name answers for it, never another wildcard. Raises GhUnavailable on
    any refused or malformed read, and when the list may be incomplete.
    """
    if rules is None:
        rules = protected_branches(project)
    exact = next((r for r in rules if r["name"] == branch), None)
    wild: list[dict[str, Any]] = []
    if "*" not in branch:  # a name holding "*" is a rule name: only its own rule answers
        wild = [r for r in sorted((r for r in rules if "*" in r["name"]),
                                  key=lambda r: str(r["name"]))
                if branch_pattern(r["name"]).match(branch)]
    if exact is not None or len(rules) < PROTECTED_PAGE:
        return _with_exact(exact, wild)
    cover = merge_rules(wild)
    # A full page may continue: an exact rule on a later page combines with
    # the wildcards on this one, so ask for the branch by name first.
    enc = project.encoded
    try:
        single = glab_api(project, f"projects/{enc}/protected_branches/{quote(branch, safe='')}")
    except GhUnavailable as e:
        if cover is not None:
            return cover
        raise GhUnavailable(
            f"truncated: {len(rules)} protected branches listed without {branch}"
            f" and the single-branch read gave no answer ({e.reason})"
        ) from None
    if not isinstance(single, dict) or not isinstance(single.get("name"), str):
        raise GhUnavailable("glab_bad_json: the protected branch was not a named object")
    return _with_exact(single, [r for r in wild if r["name"] != single["name"]])


def _with_exact(exact: dict[str, Any] | None,
                wild: list[dict[str, Any]]) -> dict[str, Any] | None:
    """``exact`` merged with the wildcards ``wild`` covering the same branch,
    keeping the exact rule's ``name``: a snapshot of that rule names it alone,
    so the wildcards it combines with change its settings, never its name."""
    if exact is None:
        return merge_rules(wild)
    return {**(merge_rules([exact, *wild]) or exact), "name": exact["name"]}
