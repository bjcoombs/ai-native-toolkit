"""Fleet coverage: the assessed repositories against what ghsync discovered (issue #745).

``fleet.py --discovered FILE`` reads ghsync's discovery listing: ``--porcelain``
output (one path per line) or ``--porcelain-json`` output (one object per line with a
``path``; ``archived`` and ``default_branch`` are read, every other key is ignored).
Blank lines and ``#`` comments are skipped and a repeated path counts once. The
archived-repos cache ghsync keeps in the root (``ROOT/.gh_archived_repos``, one path
per line) adds the archived repositories its listing leaves out.

Each discovered path lands in exactly one bucket, first match wins:

1. ``assessed``: a row with ``status`` ``ok``;
2. ``failed_scan``: a row with ``status`` ``failed`` (cloned, but it could not be scanned),
   except a ``missing_checkout`` row: a ``--from-list`` entry with no clone falls through;
3. ``archived_or_empty``: ``archived`` true, or an empty ``default_branch`` (ghsync skips both);
4. ``excluded_by_filters``: a ghsync checkout under ROOT that this run was not given
   (a ``--from-list`` run that left it out);
5. ``not_cloned``: anything else.

So the buckets always sum to ``discoverable``. A path maps to a row id the way
``lib/fleet.py`` ``layout_id`` names a checkout: a one-segment GitHub path ``svc`` is
``<root name>/svc``. Two paths naming the same id (``svc`` and ``<root name>/svc``) are
one repository, counted once under the first. ``not_in_listing`` counts the rows no discovered path names; they
are left out of the other counts.

Counts only: no repository name is kept, so ``run.json`` and ``report.md`` stay
counts-only under every output mode. Without a listing the coverage is ``None`` and
``coverage_line`` says it is unknown, never that every repository was assessed.

Fleet coverage against ghsync's discovery listing (issue #745). `read_discovery` parses
`fleet.py --discovered FILE` (`parse_discovery`: ghsync `--porcelain` paths or
`--porcelain-json` objects, `archived` and an empty `default_branch` read, a repeated path
once, a malformed line a `DiscoveryError` naming its number) and adds the paths in the root's
`.gh_archived_repos` cache as archived. `reconcile` puts each discovered path in one bucket,
first match: `assessed` (ok row), `failed_scan` (failed row), `archived_or_empty`,
`excluded_by_filters` (a checkout under the root this run was not given), `not_cloned`; the
buckets sum to `discoverable`, and `not_in_listing` counts rows no path names. A path maps to
a row id as `fleet.layout_id` does (`row_id`). `Coverage` is counts only and is stamped in
`run.json` (`fleet_stamp.FleetStamp.coverage`, null without a listing, optional in the schema since a stamp from before #745 lacks it; not copied into
`fleet-data.json`, `fleet_data.RUN_EXCLUDED`). `coverage_line` is the paragraph
`fleet_diagrams.report_markdown` puts first in `report.md`; without a listing it says the
coverage is unknown. Tests: `tests/test_fleet_coverage.py`.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import NamedTuple, TypedDict

from lib.fleet import MISSING_CHECKOUT, STATUS_OK, FleetRow, checkout_name

ARCHIVED_CACHE = ".gh_archived_repos"  # ghsync's archived-repos cache in the root


class DiscoveryError(ValueError):
    """An unreadable or malformed ``--discovered`` listing (the message names the line)."""


class Discovered(NamedTuple):
    path: str
    archived_or_empty: bool


class Coverage(TypedDict):
    discoverable: int
    assessed: int
    archived_or_empty: int
    not_cloned: int
    failed_scan: int
    excluded_by_filters: int
    not_in_listing: int  # scanned rows no discovered path names; outside the other counts


def _clean(path: str, number: int) -> str:
    """``path`` without surrounding slashes; a path that names nothing, or a ``.`` or ``..``
    segment that could reach outside the root, is a ``DiscoveryError``."""
    cleaned = path.strip().strip("/")
    if not cleaned or any(seg in ("", ".", "..") for seg in cleaned.split("/")):
        raise DiscoveryError(f"line {number}: {path!r} is not a repository path")
    return cleaned


def _entry(line: str, number: int) -> Discovered:
    if not line.startswith(("{", "[")):  # JSON, never a path
        return Discovered(_clean(line, number), False)
    try:
        obj = json.loads(line)
    except json.JSONDecodeError as e:
        raise DiscoveryError(f"line {number}: not a JSON object ({e.msg})") from e
    path = obj.get("path") if isinstance(obj, dict) else None
    if not isinstance(path, str):
        raise DiscoveryError(f"line {number}: no \"path\" string")
    branch = obj.get("default_branch", "main")
    return Discovered(_clean(path, number), obj.get("archived") is True or not branch)


def parse_discovery(text: str) -> list[Discovered]:
    """The listing's entries in first-seen order, one per path. Raises ``DiscoveryError``."""
    seen: dict[str, Discovered] = {}
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        entry = _entry(line, number)
        if entry.path not in seen:
            seen[entry.path] = entry
    return list(seen.values())


def read_discovery(listing: Path, root: Path) -> list[Discovered]:
    """``listing`` parsed, plus the archived paths in ``root``'s ghsync cache that it lacks."""
    try:
        entries = parse_discovery(listing.read_text(encoding="utf-8"))
    except OSError as e:
        raise DiscoveryError(f"cannot read {listing}: {e.strerror}") from e
    cache = root / ARCHIVED_CACHE
    if cache.is_file():
        listed = {e.path for e in entries}
        try:
            archived = parse_discovery(cache.read_text(encoding="utf-8", errors="replace"))
        except (OSError, DiscoveryError) as e:
            raise DiscoveryError(f"ghsync's archived-repos cache {cache}: {e}") from e
        entries += [Discovered(a.path, True) for a in archived if a.path not in listed]
    return entries


def row_id(path: str, root: Path) -> str:
    """The repository id ``lib/fleet.py`` gives the checkout of ``path`` under ``root``."""
    return path if "/" in path else f"{root.name}/{path}"


def reconcile(entries: list[Discovered], rows: list[FleetRow], root: Path,
              targeted: frozenset[str]) -> Coverage:
    """Every discovered path counted in one bucket (module docstring); ``targeted`` is this
    run's repository ids."""
    by_repo = {row["repo"]: row for row in rows}
    coverage: Coverage = {"discoverable": 0, "assessed": 0, "archived_or_empty": 0,
                          "not_cloned": 0, "failed_scan": 0, "excluded_by_filters": 0,
                          "not_in_listing": 0}
    named: set[str] = set()
    for entry in entries:
        repo = row_id(entry.path, root)
        if repo in named:  # another spelling of a path already counted
            continue
        named.add(repo)
        coverage["discoverable"] += 1
        repo_dir = root / entry.path
        row = by_repo.get(repo)
        if row is not None and row["status"] == STATUS_OK:
            coverage["assessed"] += 1
        elif row is not None and row["reason"] != MISSING_CHECKOUT:
            coverage["failed_scan"] += 1
        elif entry.archived_or_empty:
            coverage["archived_or_empty"] += 1
        elif repo not in targeted and (repo_dir / checkout_name(repo_dir)).is_dir():
            coverage["excluded_by_filters"] += 1
        else:
            coverage["not_cloned"] += 1
    coverage["not_in_listing"] = sum(1 for repo in by_repo if repo not in named)
    return coverage


def _repos(n: int) -> str:
    return f"{n} repository" if n == 1 else f"{n} repositories"


def coverage_line(coverage: Coverage | None, scanned: int) -> str:
    """The report's coverage paragraph; ``scanned`` is the run's row count."""
    if coverage is None:
        return ("**Coverage:** unknown. This run was given no discovery listing (`--discovered`), "
                "so it cannot say how many of the repositories you can see it covers. "
                f"It scanned {_repos(scanned)}.")
    c = coverage
    text = (f"**Coverage:** {c['assessed']} of {c['discoverable']} discoverable repositories were "
            f"assessed. Not yet assessed: {c['archived_or_empty']} archived or empty (skipped by "
            f"ghsync), {c['not_cloned']} discovered but not cloned, {c['failed_scan']} cloned but "
            f"could not be scanned, {c['excluded_by_filters']} excluded by filters.")
    if c["not_in_listing"]:
        text += (f" {_repos(c['not_in_listing'])} scanned here "
                 f"{'is' if c['not_in_listing'] == 1 else 'are'} not in the listing and not counted above.")
    return text
