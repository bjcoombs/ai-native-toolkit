"""GitLab CI include projects looked up across a rename (issue #1357, part 3 of #1285).

``lib/ci/ci_includes.py`` resolves a ``project:`` or ``component:`` include from the clone
at its written path under the ghsync root. A template project renamed or moved since the
include was written has no clone at that path, so its jobs read ``not_cloned`` though
ghsync holds the project under its new path. GitLab answers an API read of the old path
with the project at its new one (``path_with_namespace``), following the redirect a rename
leaves. This module asks for each include project with no clone and hands the answers to
``ci_includes``, which then reads the clone at the new path: never a checkout at the old one.

It is the private ``network`` registry spec ``gitlab_include_renames``, read by
``ci_checks``. The headless runner calls it only under ``--platform`` (``ci_checks`` is in
``signals.PLATFORM_SCANS``), so ``fleet --platform`` runs it in its platform pass, and a full
``/assess`` run reads it as it reads ``gitlab_merged_ci``; offline it
calls :func:`no_include_renames`, which reads nothing, and every include with no clone records
``rename: not_checked``.

- **Who is asked.** A ``project`` or ``component`` include ``resolve_includes`` recorded
  ``not_cloned``, by its project path (a component's path without its name). Nested
  includes of a followed project are asked in the next round, up to ``ci_includes.MAX_DEPTH``
  rounds, so the lookup reaches as deep as the offline walk.
- **The call names its target.** ``glab api --hostname <host> projects/<encoded path>`` on
  the repository's own GitLab host (``gitlab_cli.open_gitlab``), with ``cache=False``: the
  project document holds free text (#778). Nothing is asked for a repository without a
  GitLab remote.
- **What counts as checked.** An answer (the project, at any path) or ``not_found`` puts the
  path in ``IncludeRenames.checked``; an answer under another path adds ``(old, new)`` to
  ``moved``. A refused or failed read leaves the path unchecked, so its record says
  ``not_checked`` rather than claim a look-up that did not happen. GitLab answers 404 for a
  project the token cannot see as for one that does not exist, so ``checked`` means no
  visible move, not that the project was never renamed. A host-wide failure
  (``glab`` missing, not logged in, unreachable) stops the asking.

Paths are compared lower-cased, as GitLab compares them and as ``ci_includes`` writes include
ids. Tests: ``tests/test_ci_include_renames.py``.
"""
from __future__ import annotations

from pathlib import Path
from urllib.parse import quote

from lib.ci.ci_includes import MAX_DEPTH, IncludeRenames, resolve_includes
from lib.hosting.gh_cli import GhUnavailable
from lib.hosting.gitlab_cli import GitlabProject, glab_api, open_gitlab

_PREFIXES = {"project": "gitlab_project:", "component": "gitlab_component:"}
_HOST_FAILURES = ("glab_not_installed", "not_authenticated", "unreachable")


def no_include_renames(_repo_root: Path, _ghsync_root: Path | None = None) -> None:
    """The offline reading: nothing asked, so no rename is checked."""
    return None


def _unread(repo_root: Path, ghsync_root: Path, renames: IncludeRenames) -> set[str]:
    """The project paths of every include still ``not_cloned`` under ``renames``."""
    out: set[str] = set()
    for record in resolve_includes(repo_root, ghsync_root, renames).includes:
        prefix, ident = _PREFIXES.get(record["kind"]), record["id"]
        # rename null: a clone at the written path whose HEAD does not resolve, not a move
        if record["status"] != "not_cloned" or record["rename"] is None or prefix is None \
                or ident is None:
            continue
        path = ident[len(prefix):]
        if record["kind"] == "component":
            path = path.rsplit("/", 1)[0] if "/" in path else ""
        if "/" in path:
            out.add(path)
    return out


def _ask(project: GitlabProject, path: str) -> str | None:
    """The project's path as the API answers for ``path``, lower-cased; ``""`` when GitLab has
    none (``not_found``). Raises GhUnavailable for any other refusal."""
    try:
        got = glab_api(GitlabProject(project.host, path), f"projects/{quote(path, safe='')}",
                       cache=False)
    except GhUnavailable as e:
        if e.reason.startswith("not_found"):
            return ""
        raise
    new = got.get("path_with_namespace") if isinstance(got, dict) else None
    return new.strip("/").lower() if isinstance(new, str) and new.strip("/") else None


def read_include_renames(repo_root: Path, ghsync_root: Path | None = None) -> IncludeRenames | None:
    """Each include project with no clone, looked up through the API (module docstring).
    None when there is no ghsync root or no GitLab remote: nothing could be checked."""
    if ghsync_root is None:
        return None
    root = repo_root.resolve()
    renames = IncludeRenames()
    if not _unread(root, ghsync_root, renames):
        return renames
    try:
        project = open_gitlab(root)
    except GhUnavailable:
        return None
    if project is None:
        return None
    checked: set[str] = set()
    moved: dict[str, str] = {}
    asked: set[str] = set()
    for _ in range(MAX_DEPTH):
        pending = sorted(_unread(root, ghsync_root, renames) - asked)
        if not pending:
            break
        for path in pending:
            asked.add(path)
            try:
                new = _ask(project, path)
            except GhUnavailable as e:
                if e.reason.startswith(_HOST_FAILURES):
                    return IncludeRenames(frozenset(checked), tuple(sorted(moved.items())))
                continue
            if new is None:
                continue  # an answer of an unexpected shape: not checked
            checked.add(path)
            if new and new != path:
                moved[path] = new
        renames = IncludeRenames(frozenset(checked), tuple(sorted(moved.items())))
    return renames
