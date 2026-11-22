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
  ``not_found``; 429 still refused after the backoff gives ``rate_limited``;
  any other non-zero exit is ``glab_error``. A refusal is never read as a
  clean result. ``glab`` runs through ``lib.platform_cli.run_cli`` as ``gh`` does.

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
answered only by the rule of that name. The list is read page by page
(``PROTECTED_PAGE`` rules each) until a short page ends it, so an exact rule
and every wildcard on any page are merged; a list still full after
``PROTECTED_MAX_PAGES`` pages raises ``truncated`` rather than merge part of
it. "Unprotected" is said only from that complete list with no covering rule.
A 403 or 404 on GitLab usually means "no access", so it raises like any other
refusal, never "unprotected".

Typical use::

    try:
        project = open_gitlab(repo_root)        # None: not a GitLab remote
        if project is not None:
            mrs = glab_api(project, f"projects/{project.encoded}/merge_requests")
    except GhUnavailable as e:
        return unavailable(e.reason)

Pure subprocess + stdlib; imports no orchestrator.

The GitLab side of the platform seam, with `gh_cli`'s contract: the `glab` binary as a
subprocess with `--hostname` on every call, no token read, JSON parsed in Python, every
call capped (`GLAB_TIMEOUT_SECONDS`, 20; the self-managed probe
`GLAB_PROBE_TIMEOUT_SECONDS`, 8). Failures raise `gh_cli.GhUnavailable`: `glab_not_installed`,
`not_authenticated`, `unreachable` (ghsync's network patterns, or a probe timeout),
`no_access` (HTTP 401/403), `not_found` (HTTP 404), `rate_limited` (HTTP 429 after the
backoff), `glab_timeout`, `glab_error` (any other non-zero exit), `glab_bad_json`. `glab`
runs through `platform_cli.run_cli`. `open_gitlab` detects the provider from the
remote: github.com or no remote returns None without invoking `glab`; a `gitlab.com`
remote is GitLab and must pass `glab auth status --hostname gitlab.com` (else it
raises); any other host is GitLab only when that probe passes for it, and any probe
failure returns None so the caller degrades exactly as before. `parse_remote` reads
https/http (port kept), scp-like and `ssh://` (port dropped) remotes with nested
groups, and rejects a host or path segment outside a strict character set (the remote
comes from the assessed repository and reaches argv). `normalize_host` ports
`ghsync.sh`'s `--host` normalisation. `branch_protection(project, branch, rules=None)` returns the
protected-branch rules covering a branch: the rule of that exact name and every `*`
wildcard rule matching it via `branch_pattern`, combined, since GitLab applies every
matching rule and an exact name does not override a wildcard (issue #648; a name holding
`*` is answered only by its own rule). Covering rules (`main` and `m*` on `main`, or
`release/*` and `*-stable` on `release/1-stable`) are merged
by `merge_rules` as GitLab applies them, never one picked by order: each `*_access_levels`
list is the union of the rules' grants with the role grants collapsed to the most
permissive (lowest non-zero `access_level`, `0` only when every role grant is `0`),
`allow_force_push` and `code_owner_approval_required` are True when any rule sets them
(force push follows the most permissive rule, code-owner approval the most restrictive),
and `name` joins the rule names in name order with `", "`, except that a merge with an
exact rule keeps the exact rule's `name`, so a config_drift snapshot of that rule sees
the wildcards' settings, never a renamed rule. It reuses a
`protected_branches(project)` read when given one (config_drift reads the list once per
scan). `protected_branches` reads `PROTECTED_PAGE` (100) rules a page until a short page
ends the list (as `ghreport.sh` does with `--paginate`), so an exact rule and every
wildcard on any page merge; a refused or malformed later page raises, never a partial
merge, and a list still full after `PROTECTED_MAX_PAGES` (20) pages raises `truncated`.
None comes only from that complete list with no cover. A 403 or 404 raises
like any refusal, never "unprotected". Tests: `tests/test_review_reality_gitlab.py`,
`tests/test_gitlab_protected_branches.py` (stubbed `glab`/`gh` as the whole `PATH`, offline).
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
from lib.platform_cli import rate_limited, run_cli

GITLAB_COM = "gitlab.com"

# Cap every glab call, as gh_cli caps gh.
GLAB_TIMEOUT_SECONDS = 20

# The self-managed host probe runs on every non-GitHub remote, where the scan
# made no network call before; keep its worst case short.
GLAB_PROBE_TIMEOUT_SECONDS = 8

GITHUB_HOSTS = frozenset({"github.com", "www.github.com"})

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
_STATUS_TEXT_RE = re.compile(
    r"\b(401|403|404|429) (?:Unauthorized|Forbidden|Too Many Requests|[A-Za-z ]*Not Found)")

# ghsync's reachability patterns (ghsync.sh gitlab_preflight).
_UNREACHABLE_RE = re.compile(
    r"no such host|dial tcp|connection refused|timed? ?out|could not resolve"
    r"|network is unreachable|tls handshake|i/o timeout",
    re.IGNORECASE,
)


# GitLab's REST page maximum; a protected-branch page this long may continue.
PROTECTED_PAGE = 100
# Pages read before a still-full protected-branch list is called truncated.
PROTECTED_MAX_PAGES = 20


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


def _run_glab(args: list[str], timeout: int = GLAB_TIMEOUT_SECONDS, cache: bool = True) -> str:
    """Run ``glab`` and return stdout, raising GhUnavailable with a mapped reason."""
    cmd = ["glab", *args]
    shown = " ".join(cmd)
    try:
        proc = run_cli(cmd, timeout, cache)
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
    if rate_limited(stderr):  # any 429 form glab prints, still limited after the backoff
        raise GhUnavailable(f"rate_limited: `{shown}` was still rate limited after retries ({detail})")
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
    if host in GITHUB_HOSTS:
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


def glab_api(project: GitlabProject, path: str, cache: bool = True) -> Any:
    """``glab api --hostname <host> <path>`` parsed as JSON (a REST GET, one page).
    ``cache=False`` keeps the response out of the ``--platform`` response cache."""
    args = ["api", "--hostname", project.host, path]
    out = _run_glab(args, cache=cache)
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
    """Every protected-branch rule of the project, each with a name.

    Reads ``PROTECTED_PAGE`` rules a page until a short page ends the list.
    Raises GhUnavailable on a refused or malformed page, and ``truncated``
    when the list is still full after ``PROTECTED_MAX_PAGES`` pages. Read it
    once and pass it to :func:`branch_protection` for every branch a scan asks
    about.
    """
    base = f"projects/{project.encoded}/protected_branches?per_page={PROTECTED_PAGE}"
    rules: list[dict[str, Any]] = []
    for page in range(1, PROTECTED_MAX_PAGES + 1):
        got = glab_api(project, base if page == 1 else f"{base}&page={page}")
        if not isinstance(got, list):
            raise GhUnavailable("glab_bad_json: the protected branch list was not a list")
        named = [r for r in got if isinstance(r, dict) and isinstance(r.get("name"), str)]
        if len(named) != len(got):
            raise GhUnavailable("glab_bad_json: a protected branch has no name")
        rules += named
        if len(got) < PROTECTED_PAGE:
            return rules
    raise GhUnavailable(
        f"truncated: the protected branch list is still full after {len(rules)} rules"
        f" ({PROTECTED_MAX_PAGES} pages)"
    )


def branch_protection(project: GitlabProject, branch: str,
                      rules: list[dict[str, Any]] | None = None) -> dict[str, Any] | None:
    """The protected-branch rules covering ``branch``, merged by
    :func:`merge_rules` (an exact rule with every matching wildcard), or None
    when unprotected.

    ``rules`` is a complete :func:`protected_branches` read to reuse; it is
    read here when omitted. See the module docstring for the matching order. A
    ``branch`` holding ``*`` is a rule name, not a branch: only the rule of
    that name answers for it, never another wildcard. Raises GhUnavailable on
    any refused or malformed read, and when the list is truncated.
    """
    if rules is None:
        rules = protected_branches(project)
    exact = next((r for r in rules if r["name"] == branch), None)
    wild: list[dict[str, Any]] = []
    if "*" not in branch:  # a name holding "*" is a rule name: only its own rule answers
        wild = [r for r in sorted((r for r in rules if "*" in r["name"]),
                                  key=lambda r: str(r["name"]))
                if branch_pattern(r["name"]).match(branch)]
    return _with_exact(exact, wild)


def _with_exact(exact: dict[str, Any] | None,
                wild: list[dict[str, Any]]) -> dict[str, Any] | None:
    """``exact`` merged with the wildcards ``wild`` covering the same branch,
    keeping the exact rule's ``name``: a snapshot of that rule names it alone,
    so the wildcards it combines with change its settings, never its name."""
    if exact is None:
        return merge_rules(wild)
    return {**(merge_rules([exact, *wild]) or exact), "name": exact["name"]}
