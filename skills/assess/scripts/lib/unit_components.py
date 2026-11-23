"""Skill and plugin-component units: the folders a skills or plugin repository is made of (#1098).

``lib.unit_discovery`` finds units from build manifests. In a skills or plugin repository
(an Agent Skills marketplace, a Claude Code plugin) the part an agent works in is the skill,
which seldom has a manifest. This module finds those folders from the tracked file list;
``unit_discovery`` takes them first and runs manifest discovery around them.

**Kinds** (``UnitEntry.kind``; ``package`` is a build-manifest unit):

- ``skill``: a folder holding a ``SKILL.md``. Folders are taken shallowest first, so a
  ``SKILL.md`` inside a skill (a test fixture, an example) is part of that skill and is
  skipped as ``claimed``, the nesting rule #862 uses for a manifest inside a unit.
- ``agents`` and ``commands``: the ``agents/`` and ``commands/`` folder of a plugin root, a
  folder holding ``.claude-plugin/plugin.json``. A plugin's agents and commands are not
  attributed to a skill: no file ties an agent to the skill that uses it, so the folder is
  one unit. One inside a skill folder is part of that skill instead.

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


def discover_components(files: Iterable[str], dirs: frozenset[str]) -> Components:
    """The skill and plugin-component folders of ``files`` (repo-relative POSIX paths, already
    filtered by the run's excludes); ``dirs`` is every folder holding a tracked file."""
    candidates: dict[str, Kind] = {}
    for rel in files:
        if posixpath.basename(rel) == SKILL_FILE:
            candidates[posixpath.dirname(rel) or "."] = "skill"
    for rel in files:
        if rel == PLUGIN_MANIFEST or rel.endswith("/" + PLUGIN_MANIFEST):
            root = rel[: -len(PLUGIN_MANIFEST)].rstrip("/") or "."
            for name, kind in PLUGIN_COMPONENTS:
                folder = name if root == "." else f"{root}/{name}"
                if folder in dirs:
                    candidates.setdefault(folder, kind)
    units: list[Component] = []
    claimed: list[str] = []
    for folder in sorted(candidates, key=_depth):
        if any(_under(folder, u.path) for u in units):
            claimed.append(folder)
        else:
            units.append(Component(folder, candidates[folder]))
    return Components(sorted(units), sorted(claimed))
