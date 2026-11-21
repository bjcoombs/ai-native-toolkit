"""Per-repository facts the fleet units tree and ``fleet-data.json`` need (issue #703).

Two facts are read from each checkout, before any scan runs (``--delete-after``
may remove a clone once it is scanned, and a counts-only scan drops owners):

- **Web URL.** The ``origin`` remote, normalised to ``https://<host>/<path>``:
  scp-style (``git@host:grp/repo.git``), ``ssh://`` (any user and port) and
  ``http(s)://`` remotes all give the same URL; embedded credentials, an ssh
  port, a trailing ``.git`` and a trailing slash are dropped (an http(s) port is kept). GitHub and
  GitLab serve a repository at exactly that URL (GitLab at any group depth).
  A local remote (``file://``, a bare path) or none gives None.
- **Default owners.** The owners on the last catch-all rule (``*`` or ``**``) of
  the first CODEOWNERS file found in ``.github/``, ``.gitlab/``, the root or
  ``docs/`` (GitHub's and GitLab's locations). GitLab section headers
  (``[Section]``, ``^[Section][2] @owner``) are skipped. These are the
  repository-ownership fallback; owners are identities, so counts-only output
  keeps only their count (``lib/fleet_data.py``).

Read only: ``git config`` runs with ``GIT_OPTIONAL_LOCKS=0`` (``lib.fleet._git``),
and a checkout that cannot be read gives None and no owners, never an error.
"""
from __future__ import annotations

import re
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import NamedTuple

from lib.fleet import Target, _git

CODEOWNERS_FILES = (".github/CODEOWNERS", ".gitlab/CODEOWNERS", "CODEOWNERS", "docs/CODEOWNERS")
CATCH_ALL = frozenset({"*", "**", "/**"})

# Userinfo runs to the LAST "@" before the host (a password may hold "@") and is never
# kept; a host never holds "@". A one-character host is a Windows drive letter
# (C:/repos/x), never a remote.
_SCP = re.compile(r"^(?:[^/\s]*@)?(?P<host>[^/:@\s]{2,}):(?!//)(?P<path>.+)$")
_URL = re.compile(r"^(?P<scheme>ssh|git|https?|git\+ssh|ssh\+git)://(?:[^/\s]*@)?(?P<host>[^/:@\s]+)"
                  r"(?::(?P<port>\d*))?/(?P<path>.+)$", re.IGNORECASE)
_OWNER = re.compile(r"^@\S+$|^[^@\s]+@[^@\s]+$")


class RepoFacts(NamedTuple):
    web_url: str | None
    owners: tuple[str, ...]


def web_url(remote: str | None) -> str | None:
    """``https://<host>/<path>`` for a remote URL; None for a local or unparsable one."""
    if not remote:
        return None
    remote = remote.strip()
    match = _URL.match(remote) or (None if "://" in remote else _SCP.match(remote))
    if match is None:
        return None
    if "@" in match.group("host"):  # pragma: no cover - the patterns exclude it; never emit userinfo
        return None
    segments = [s for s in match.group("path").split("/") if s not in ("", ".", "..")]
    if segments and segments[-1].endswith(".git"):
        segments[-1] = segments[-1][: -len(".git")]
    segments = [s for s in segments if s]
    if not segments:
        return None
    host = match.group("host").lower()
    # An http(s) remote's port serves the web UI too; an ssh port never does.
    port = match.groupdict().get("port")
    if port and match.group("scheme").lower().startswith("http"):
        host = f"{host}:{port}"
    return f"https://{host}/{'/'.join(segments)}"


def default_owners(text: str) -> tuple[str, ...]:
    """The owners of the last catch-all rule in a CODEOWNERS file, in file order."""
    owners: tuple[str, ...] = ()
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or line.startswith(("[", "^[")):
            continue
        pattern, *rest = line.split()
        if pattern in CATCH_ALL:
            owners = tuple(o for o in rest if _OWNER.match(o))
    return owners


def codeowners_text(checkout: Path) -> str | None:
    """The first CODEOWNERS file's text; a file resolving outside the checkout (a
    symlinked ``.github/``, say) is never read."""
    root = checkout.resolve()
    for name in CODEOWNERS_FILES:
        path = checkout / name
        if path.is_file() and not path.is_symlink() and path.resolve().is_relative_to(root):
            try:
                return path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                return None
    return None


def repo_facts(target: Target) -> RepoFacts:
    """One checkout's web URL and CODEOWNERS default owners."""
    try:
        if not target.checkout.is_dir():
            return RepoFacts(None, ())
        text = codeowners_text(target.checkout)
    except OSError:  # an unreadable checkout never stops the run; its scan reports it
        return RepoFacts(None, ())
    return RepoFacts(web_url(_git(target.checkout, "config", "--get", "remote.origin.url")),
                     default_owners(text) if text is not None else ())


def collect_facts(targets: Iterable[Target], jobs: int = 1) -> dict[str, RepoFacts]:
    """Facts for every target, keyed by repository id."""
    items = list(targets)
    with ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
        facts = list(pool.map(repo_facts, items))
    return {t.repo: f for t, f in zip(items, facts)}
