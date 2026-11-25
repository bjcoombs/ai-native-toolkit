"""A ghsync ``--from-list`` file of the CI include projects a fleet run could not read (issue #1356).

A fleet run lists each GitLab CI include project not cloned under the ghsync root as
``not_cloned_includes`` in ``run.json`` (``lib/fleet/fleet_includes.py``, #1355). This module
checks each one through the GitLab API and writes the reachable ones to a file ghsync's
``--from-list`` clones; the unreachable ones are reported with their status, so one ghsync run
fetches every include project the user can read. ghsync itself is unchanged.

- **Mapping** (``include_project``): an include item names a project. ``project: <path>`` is
  ``<path>``; ``component: <host>/<project>/<name>@<ver>`` is ``<project>`` (the host and the
  version dropped by ``lib.ci.shared_tooling.component_source``, then the component name). A
  path holding a CI variable, or any segment outside GitLab's path characters, maps to None.
  Paths are lower-cased, as ``lib/ci/ci_includes.py`` writes its include ids.
- **Access** (``IncludeApi.access``): ``projects/<id>`` answered is ``reachable``; HTTP 404 is
  ``not_found`` and HTTP 401 or 403 ``no_access``; any other refusal of one project is
  ``error``. ``GlabIncludeApi.open`` runs ``glab auth status`` for the host first, because
  ``glab api`` alone reports a missing login as 401/404 per project; a host-wide failure
  (``glab`` not installed, not logged in, host unreachable) raises ``IncludeListError``, and
  nothing is written.
- **Recursion** (``walk``): ``run.json`` keeps the include project, not the files it names, so
  a reachable project's nested includes are read from every YAML file in its default branch
  (the first ``MAX_FILES`` of them, by path): a superset of the files a pipeline reads. A
  project named there and not seen before is checked one level deeper. The run's projects are
  depth 1, and a project at ``lib.ci.ci_includes.MAX_DEPTH`` (fleet's nested-include depth) is
  checked but not read, so the walk stops where fleet's offline walk stops. An extra reachable
  project costs one clone; the next fleet run then resolves the includes exactly. A project
  whose tree or a file could not be read, or with more YAML files than the cap, is
  ``partial`` and the report names it.
- **Unusable entries**: fleet records an include path built from a CI variable as
  ``not_cloned`` too; ``seeds`` sets such an entry aside and ``report`` names it, so the other
  projects are still checked.
- **Output**: ``list_file`` is the ``--porcelain`` format ghsync reads, a ``#`` header then one
  path per line, sorted; a reachable project is written as the API spells its path when that
  differs from the include only in case. ``report`` is the Markdown summary: the counts, then
  one row per unreachable project with its status and the project that named it.

The project paths come from a ``--full`` run, the only mode in which ``run.json`` names them;
``seeds`` refuses a counts-only or hashed run. The real API is ``GlabIncludeApi``, which reads
through ``lib.hosting.gitlab_cli.glab_api`` (``glab`` owns the token). The command line is
``skills/fleet/scripts/fleet_include_list.py``. Tests: ``tests/test_fleet_include_list.py``.
"""
from __future__ import annotations

import base64
import binascii
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal, Protocol
from urllib.parse import quote

from lib.ci.ci_include_items import IncludeItem, include_items
from lib.ci.ci_includes import MAX_DEPTH, MAX_FILES
from lib.ci.shared_tooling import component_source
from lib.command_index import MAX_CONFIG_BYTES
from lib.hosting.gh_cli import GhUnavailable
from lib.hosting.gitlab_cli import GitlabProject, glab_api, glab_auth

Access = Literal["reachable", "not_found", "no_access", "error"]
TREE_PAGE = 100  # GitLab's REST page maximum
TREE_MAX_PAGES = 10  # a template project with more entries is read in part
HEADER = "# CI include projects for ghsync --from-list, from a fleet run (fleet_include_list.py)\n"
# A path segment GitLab allows; a CI variable ($VAR) or a template expression is not one.
_SEGMENT = re.compile(r"^(?!\.+$)[a-z0-9_.][a-z0-9_.-]*$")
# Reasons that mean the host, not one project, could not be read. A timeout is not one: after
# ``open`` passes, one slow read is that project's ``error`` or ``partial``, not the whole run's.
_HOST_FAILURES = ("glab_not_installed", "not_authenticated", "unreachable")


class IncludeListError(Exception):
    """The inputs cannot be read, or the GitLab host cannot be reached at all."""


@dataclass(frozen=True)
class ProjectAccess:
    status: Access
    path: str | None = None  # path_with_namespace as the API spells it; None when not reachable
    ref: str | None = None  # the default branch; None for an empty project


@dataclass(frozen=True)
class Checked:
    project: str  # the include path, lower-cased
    status: Access
    depth: int  # 1: named by the fleet run
    via: str | None  # the project whose CI files named it; None at depth 1
    path: str | None = None  # the path to clone: the API's spelling, else the include's
    partial: bool = False  # reachable, but its CI files were not all read (see ``report``)


class IncludeApi(Protocol):
    def access(self, project: str) -> ProjectAccess: ...

    def ci_files(self, project: str, ref: str) -> tuple[list[str], bool]: ...

    def read(self, project: str, file: str, ref: str) -> str | None: ...


def _valid(path: str) -> str | None:
    path = path.strip().strip("/").lower()
    segments = path.split("/")
    return path if len(segments) >= 2 and all(_SEGMENT.match(s) for s in segments) else None


def include_project(item: IncludeItem) -> str | None:
    """The project an include item names, or None for another kind or an unusable path."""
    if item.kind == "project":
        return _valid(item.source)
    if item.kind == "component":
        full = component_source(item.source)
        return _valid(full.rsplit("/", 1)[0]) if "/" in full else None
    return None


def seeds(run: Mapping[str, Any]) -> tuple[list[str], list[str]]:
    """The include projects a ``--full`` fleet ``run.json`` names, in its order, deduplicated,
    and the entries that name no project the API can look up: fleet records a path built from a
    CI variable (``$CI_PROJECT_NAMESPACE/ci-templates``) as ``not_cloned`` too.

    Raises IncludeListError for a run that did not name them (counts-only or hashed)."""
    entries = run.get("not_cloned_includes") or []
    if not isinstance(entries, list):
        raise IncludeListError("run.json not_cloned_includes is not a list")
    out: list[str] = []
    unusable: list[str] = []
    for entry in entries:
        project = entry.get("project") if isinstance(entry, Mapping) else None
        if not isinstance(project, str) or project.startswith("h:"):
            raise IncludeListError(
                "run.json does not name the include projects: run fleet with --full")
        path = _valid(project)
        if path is None:
            if project not in unusable:
                unusable.append(project)
        elif path not in out:
            out.append(path)
    return out, unusable


def walk(start: Sequence[str], api: IncludeApi, max_depth: int = MAX_DEPTH) -> list[Checked]:
    """Every project checked, breadth first: ``start`` at depth 1, then the projects their
    CI files name, to ``max_depth`` and no further (see the module docstring)."""
    queue: list[tuple[str, int, str | None]] = [(p, 1, None) for p in start]
    seen = set(start)
    checked: list[Checked] = []
    while queue:
        project, depth, via = queue.pop(0)
        access = api.access(project)
        path = access.path if access.path and access.path.lower() == project else project
        partial = False
        if access.status == "reachable" and access.ref is not None and depth < max_depth:
            files, complete = api.ci_files(project, access.ref)
            partial = not complete or len(files) > MAX_FILES
            for file in sorted(files)[:MAX_FILES]:
                text = api.read(project, file, access.ref)
                partial = partial or text is None
                for item in include_items(text)[0] if text is not None else []:
                    named = include_project(item)
                    if named is not None and named not in seen:
                        seen.add(named)
                        queue.append((named, depth + 1, project))
        checked.append(Checked(project, access.status, depth, via,
                               path if access.status == "reachable" else None, partial))
    return checked


def list_file(checked: Sequence[Checked]) -> str:
    """The ghsync ``--from-list`` file: the header, then each reachable project, sorted."""
    paths = sorted({c.path for c in checked if c.path is not None})
    return HEADER + "".join(f"{p}\n" for p in paths)


def report(checked: Sequence[Checked], list_path: str, host: str,
           unusable: Sequence[str] = ()) -> str:
    """The Markdown summary: how many were reachable, and each unreachable one with its status."""
    reachable = sum(c.status == "reachable" for c in checked)
    missing = [c for c in checked if c.status != "reachable"]
    lines = [f"{len(checked)} CI include projects checked, {reachable} reachable, "
             f"written to {list_path}.",
             f"Clone them with: ghsync --provider gitlab --host {host} --from-list {list_path}"]
    if missing:
        lines += ["", f"{len(missing)} not reachable:", "",
                  "| Project | Status | Named by |", "|---|---|---|"]
        lines += [f"| `{c.project}` | {c.status} | {f'`{c.via}`' if c.via else 'the fleet run'} |"
                  for c in missing]
    if unusable:
        lines += ["", f"{len(unusable)} not looked up, because the path is no project path "
                  "(a CI variable, or a single segment): " + ", ".join(f"`{u}`" for u in unusable)]
    partial = [c.project for c in checked if c.partial]
    if partial:
        lines += ["", f"{len(partial)} reachable but not fully read, so an include they hold may be "
                  "missing from the list (a refused or oversized file, or more than "
                  f"{MAX_FILES} YAML files): " + ", ".join(f"`{p}`" for p in partial)]
    return "\n".join(lines) + "\n"


class GlabIncludeApi:
    """``IncludeApi`` over ``glab api`` on one host; no response is cached."""

    def __init__(self, host: str) -> None:
        self.host = host

    @classmethod
    def open(cls, host: str) -> GlabIncludeApi:
        """The API on ``host`` once ``glab auth status`` passes for it. ``glab api`` alone
        reports a missing login as 401/404 and a dead host as ``glab_error``, which would read
        as unreachable projects; this check raises IncludeListError instead."""
        try:
            glab_auth(host)
        except GhUnavailable as e:
            raise IncludeListError(e.reason) from None
        return cls(host)

    def _get(self, project: str, rest: str = "") -> Any:
        target = GitlabProject(self.host, project)
        try:
            return glab_api(target, f"projects/{target.encoded}{rest}", cache=False)
        except GhUnavailable as e:
            if e.reason.startswith(_HOST_FAILURES):
                raise IncludeListError(e.reason) from None
            raise

    def access(self, project: str) -> ProjectAccess:
        try:
            got = self._get(project)
        except GhUnavailable as e:
            if e.reason.startswith("not_found"):
                return ProjectAccess("not_found")
            return ProjectAccess("no_access" if e.reason.startswith("no_access") else "error")
        if not isinstance(got, dict):
            return ProjectAccess("error")
        path, ref = got.get("path_with_namespace"), got.get("default_branch")
        return ProjectAccess("reachable", path if isinstance(path, str) else None,
                             ref if isinstance(ref, str) and ref else None)

    def ci_files(self, project: str, ref: str) -> tuple[list[str], bool]:
        """The YAML files on ``ref``, and whether the listing is complete."""
        files: list[str] = []
        for page in range(1, TREE_MAX_PAGES + 1):
            try:
                got = self._get(project, f"/repository/tree?recursive=true&per_page={TREE_PAGE}"
                                         f"&page={page}&ref={quote(ref, safe='')}")
            except GhUnavailable:
                return files, False
            if not isinstance(got, list):
                return files, False
            files += [e["path"] for e in got if isinstance(e, dict) and e.get("type") == "blob"
                      and isinstance(e.get("path"), str) and e["path"].endswith((".yml", ".yaml"))]
            if len(got) < TREE_PAGE:
                return files, True
        return files, False

    def read(self, project: str, file: str, ref: str) -> str | None:
        try:
            got = self._get(project, f"/repository/files/{quote(file, safe='')}?ref={quote(ref, safe='')}")
        except GhUnavailable:
            return None
        content = got.get("content") if isinstance(got, dict) else None
        if not isinstance(content, str) or got.get("encoding") != "base64":
            return None
        try:
            raw = base64.b64decode(content, validate=True)
        except (binascii.Error, ValueError):
            return None
        return raw.decode("utf-8", errors="replace") if len(raw) <= MAX_CONFIG_BYTES else None
