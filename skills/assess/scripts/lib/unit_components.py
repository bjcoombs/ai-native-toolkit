"""Skill and plugin-component units: the folders a skills or plugin repository is made of (#1098).

``lib.unit_discovery`` finds units from build manifests. In a skills or plugin repository
(an Agent Skills marketplace, a Claude Code plugin) the part an agent works in is the skill,
which seldom has a manifest. This module finds those folders from the tracked file list;
``unit_discovery`` takes them first and runs manifest discovery around them.

**Kinds** (``UnitEntry.kind``; ``package`` is a build-manifest unit):

- ``skill``: a folder holding a ``SKILL.md``. Folders are taken shallowest first, so a
  ``SKILL.md`` inside a skill (a bundled sub-skill) is part of that skill and is
  skipped as ``claimed``, the nesting rule #862 uses for a manifest inside a unit. A
  ``SKILL.md`` at the root is the exception: it claims nothing (``unit_discovery``).
- ``agents`` and ``commands``: the ``agents/`` and ``commands/`` folder of a plugin root, a
  folder holding ``.claude-plugin/plugin.json``. A plugin's agents and commands are not
  attributed to a skill: no file ties an agent to the skill that uses it, so the folder is
  one unit. One inside a skill folder is part of that skill instead.

A candidate's own folder name is never classified (``skills/spec`` and ``skills/demo`` are
skills), only the folders above it, and a candidate is left out when one of them is:

- test data (``lib.path_classes``): a fixture, test, example, vendored or generated folder
  (``pkg/tests/fixtures/sample/SKILL.md``), wherever it sits;
- hidden, a name starting with ``.``: ``.github/skills/``, ``.agents/skills/`` and
  ``.claude/skills/`` hold an app repository's agent configuration, not its products.

A component folder that also holds a build manifest (``skills/assess`` with its
``pyproject.toml``) is one unit of kind ``skill`` with the manifest's ecosystem. A build
manifest inside a component folder is part of it, and a manifest folder above component
units is a unit of the source files no component or nested manifest owns, as for workspace
members. Pure: reads the file list only.
"""
from __future__ import annotations

import posixpath
from collections.abc import Iterable
from typing import Literal, NamedTuple

from lib.path_classes import is_production

Kind = Literal["package", "skill", "agents", "commands"]
KINDS: tuple[Kind, ...] = ("package", "skill", "agents", "commands")
SKILL_FILE = "SKILL.md"
PLUGIN_MANIFEST = ".claude-plugin/plugin.json"
# The plugin-root folders that are units of their own, with their kind.
PLUGIN_COMPONENTS: tuple[tuple[str, Kind], ...] = (("agents", "agents"), ("commands", "commands"))


class Component(NamedTuple):
    """One component unit: its repo-relative folder and kind."""

    path: str
    kind: Kind


class Components(NamedTuple):
    """The component units, and the component folders skipped inside another (``claimed``)."""

    units: list[Component]
    claimed: list[str]


def _under(path: str, folder: str) -> bool:
    return folder == "." or path == folder or path.startswith(folder + "/")


def _depth(folder: str) -> tuple[int, str]:
    return (0 if folder == "." else folder.count("/") + 1, folder)


def _eligible(folder: str) -> bool:
    """True when no folder above ``folder`` is test data or hidden (its own name is not read)."""
    parent = posixpath.dirname(folder) if folder != "." else ""
    if any(part.startswith(".") for part in parent.split("/") if part):
        return False
    return is_production(posixpath.join(parent, SKILL_FILE))


def discover_components(files: Iterable[str], dirs: frozenset[str]) -> Components:
    """The skill and plugin-component folders of ``files`` (repo-relative POSIX paths, already
    filtered by the run's excludes); ``dirs`` is every folder holding a tracked file."""
    candidates: dict[str, Kind] = {}
    files = list(files)
    for rel in files:
        if posixpath.basename(rel) == SKILL_FILE and _eligible(posixpath.dirname(rel) or "."):
            candidates[posixpath.dirname(rel) or "."] = "skill"
    for rel in files:
        if rel == PLUGIN_MANIFEST or rel.endswith("/" + PLUGIN_MANIFEST):
            root = rel[: -len(PLUGIN_MANIFEST)].rstrip("/") or "."
            for name, kind in PLUGIN_COMPONENTS:
                folder = name if root == "." else f"{root}/{name}"
                if folder in dirs and _eligible(folder):  # the plugin root is read too
                    candidates.setdefault(folder, kind)
    units: list[Component] = []
    claimed: list[str] = []
    for folder in sorted(candidates, key=_depth):
        # a root skill claims nothing: the skills and packages below it stay units
        if any(u.path != "." and _under(folder, u.path) for u in units):
            claimed.append(folder)
        else:
            units.append(Component(folder, candidates[folder]))
    return Components(sorted(units), sorted(claimed))
