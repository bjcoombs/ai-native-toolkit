"""The CI include projects a fleet run could not read because they are not cloned (issue #1355).

Fleet resolves GitLab CI includes offline from the ghsync root (``lib/ci_includes.py``), and
``scans.ci_checks.includes`` records each item per repository. An item whose ``kind`` is
``project`` or ``component`` and whose ``status`` is ``not_cloned`` names a project the root
holds no clone of, so the CI jobs it carries were not read. ``not_cloned_includes`` rolls
those records up across the run's scanned rows: one entry per include project, with its
``kind`` and how many repositories include it (a repository counts once per project however
many items name it), most-included first, then ``project`` before ``component``, then by id.

Privacy follows the ``HASHED_PATH`` rule of ``lib/counts_only/counts_only_rules.py``. The
``project`` field is the clear project path only under ``--full``: a ``project:`` include's
path, or a ``component:`` include's path without its last segment (the component name), so
two components of one project count as that project once; under ``--hash-paths`` or
``--hash-salt`` it is the row's hashed include id, the same ``h:`` value ``repos.jsonl``
holds, so the two join (a hash cannot drop the component name, so there each component counts
on its own); in plain counts-only output it is null. The counts and the kind are
kept in every mode, so a counts-only ``run.json`` or ``report.md`` names no include path.

``includes_section`` renders the ``report.md`` section from that list, or None when the run
found no such include, so a fleet without GitLab includes writes the report it wrote before.

CI include projects not cloned under the ghsync root (#1355). `not_cloned_includes(rows,
counts_only=, paths_hashed=)` rolls each ok row's `scans.ci_checks.includes` records with kind
`project` or `component` and status `not_cloned` into one `NotClonedInclude` per project
(`project`, `kind`, `repositories`; a component's project is its path without the name),
sorted by repositories descending, project before component, then id.
`project` is the clear path under `--full`, the row's hashed include id under hashing, and
null in plain counts-only output (the `HASHED_PATH` rule). `fleet_stamp.build_stamp` writes the
list to `run.json` as `not_cloned_includes` (only when non-empty); `includes_section` is the
`report.md` section `fleet_diagrams.report_markdown` places after the coverage line. Tests:
`tests/test_fleet_includes.py`.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, TypedDict

from lib.fleet.runner import STATUS_OK, FleetRow
from lib.vocabulary import IN_PLACE

INCLUDE_PROJECT_KINDS = ("project", "component")
NOT_CLONED = "not_cloned"
_PREFIXES = ("gitlab_project:", "gitlab_component:")  # lib/ci_includes.py include ids


class NotClonedInclude(TypedDict):
    project: str | None  # clear path under --full, hashed id under hashing, None in plain counts-only
    kind: str  # one of INCLUDE_PROJECT_KINDS
    repositories: int  # repositories that include it at any depth


def _records(row: FleetRow) -> list[Mapping[str, Any]]:
    block = (row["scans"] or {}).get("ci_checks") if row["status"] == STATUS_OK else None
    includes = block.get("includes") if isinstance(block, Mapping) else None
    return [r for r in includes if isinstance(r, Mapping)] if isinstance(includes, list) else []


def _key(ident: str, kind: str) -> str:
    """The include project an id names: ``gitlab_project:<path>`` is ``<path>``, and
    ``gitlab_component:<group>/<project>/<name>`` is ``<group>/<project>``. A hashed id (``h:``)
    stays as it is, so under hashing each component counts on its own."""
    for prefix in _PREFIXES:
        if ident.startswith(prefix):
            path = ident[len(prefix):]
            return path.rsplit("/", 1)[0] if kind == "component" and "/" in path else path
    return ident


def not_cloned_includes(rows: Sequence[FleetRow], *, counts_only: bool,
                        paths_hashed: bool) -> list[NotClonedInclude]:
    """Each include project not cloned under the root, once, with its repository count."""
    counts: dict[tuple[str, str], int] = {}
    for row in rows:
        seen: set[tuple[str, str]] = set()
        for record in _records(row):
            kind, ident = record.get("kind"), record.get("id")
            if record.get("status") != NOT_CLONED or kind not in INCLUDE_PROJECT_KINDS:
                continue
            if not isinstance(ident, str) or not ident:
                continue
            key = (_key(ident, str(kind)), str(kind))
            if key not in seen:
                seen.add(key)
                counts[key] = counts.get(key, 0) + 1
    ordered = sorted(counts.items(), key=lambda item: (-item[1], INCLUDE_PROJECT_KINDS.index(item[0][1]),
                                                     item[0][0]))
    # assess_signals already hashed the id under a salt; plain counts-only output drops it
    named = paths_hashed or not counts_only
    return [{"project": project if named else None, "kind": kind, "repositories": n}
            for (project, kind), n in ordered]


def _repos(n: int) -> str:
    return f"{n} repository" if n == 1 else f"{n} repositories"


def includes_section(entries: Sequence[NotClonedInclude] | None) -> str | None:
    """The ``report.md`` section naming each include project not cloned, or None without one."""
    if not entries:
        return None
    named = any(e["project"] is not None for e in entries)
    lines = ["## CI include projects not cloned", "",
             f"{IN_PLACE} every CI include project cloned under the root was looked up. "
             f"{len(entries)} include {'project is' if len(entries) == 1 else 'projects are'} "
             "not cloned under the root, so the CI jobs they hold were not read. Clone them "
             "(ghsync `--from-list`) and run again to measure that CI.", ""]
    if not named:
        lines += ["Counts-only output leaves out the project paths. Run with `--full` to name them, "
                  "or with `--hash-paths` to join them with `repos.jsonl`.", ""]
    lines += ["| Project | Kind | Included by |", "|---|---|---|"]
    for e in entries:
        project = f"`{e['project']}`" if e["project"] is not None else "(not named)"
        lines.append(f"| {project} | {e['kind']} | {_repos(e['repositories'])} |")
    return "\n".join(lines) + "\n"
