"""A committed skill library: at least one valid ``SKILL.md`` in a known skill location (#848).

A skill library gives every agent session the team's procedures on demand: one ``SKILL.md`` a
skill, loaded when it triggers. Kept in the repository, every clone and every harness that
reads it gets the same skills; kept in one person's home folder, nobody else does. This module
reads the committed files only (``git ls-files``), so an untracked skill or one in a home folder
does not count. No model is involved.

The tendency it compensates for is unactioned intent: procedures that live in heads or in one
machine's configuration are restated in every session instead of being written down once.

**Known skill locations** (``HARNESS_DIRS``, ``LIBRARY_PREFIXES``): a ``skills/`` folder at the
repository root, in a harness folder (``.claude``, ``.agents``, ``.gemini``, ``.codex``,
``.github``, ``.cursor``, ``.opencode``) or in a plugin folder (``plugins/<name>/``), with the
``SKILL.md`` one or two folders below it (``skills/<name>/SKILL.md`` or
``skills/<group>/<name>/SKILL.md``). Under ``--scope`` the same prefixes are also read relative
to the scope folder, and only files under the scope count.

**What does not count.** A ``SKILL.md`` with a path segment in ``SET_ASIDE_DIRS``
(``node_modules``, vendored and third-party folders, fixture and test-data folders) or in the
``.assess/config.toml`` exclude dirs (``set_aside_vendored``, ``set_aside_fixture``); one
outside any known location (``set_aside_location``); and one whose frontmatter is missing, does
not parse cleanly (``claude_config.parse_frontmatter``, the same parser the Claude Code
configuration check uses) or has no ``name`` or no ``description`` (``invalid_frontmatter``,
listed in ``invalid_files``). A tracked symlink (git mode ``120000``) that resolves outside the
repository counts for nothing (``symlinks_outside``), and a ``SKILL.md`` that is itself a
symlink counts only through its target, so no skill counts twice.

**Supporting evidence, not counted:**

- ``shared_by_symlink``: a tracked symlink in a known location (``.agents/skills`` ->
  ``.claude/skills``) that resolves to a folder inside the repository holding counted skills,
  so several harnesses read one tree. Each entry is ``{"path", "target"}``, both
  repository-relative. A skill reached only through such a link (its tree outside the known
  prefixes) counts, since the harness loads it from the link.
- ``lock_files``: a tracked ``skills-lock.json``.
- ``hashed_manifests``: a tracked ``.claude-plugin/marketplace.json`` or ``plugin.json`` whose
  text holds a content hash key (``sha256``, ``integrity``, ``hash``, ``sha``, ``digest``).

Block (``skill_library`` in run-context.json)::

    {"available": True, "present": bool, "skills_counted": int,
     "skills": [{"path", "location"}],
     "invalid_frontmatter": int, "invalid_files": [str],
     "set_aside_vendored": int, "set_aside_fixture": int, "set_aside_location": int,
     "symlinks_outside": int, "shared_by_symlink": [{"path", "target"}],
     "lock_files": [str], "hashed_manifests": [str]}

``present`` is ``skills_counted > 0`` and feeds the ``skill-library.1`` condition. Lists are
sorted by path. A root that is not a git repository, or a failed ``git ls-files``, gives
``{"available": False, "reason": "not_a_git_repository: ..."}``: committed files cannot be told
apart, so the condition reads not measured, never unmet.

Skill library (#848), no model. `scan_skill_library(repo_root, scope, excludes)` lists tracked
`SKILL.md` files (`git ls-files -s -z`) in a known location (`skills/` at the root, a harness
folder or `plugins/<name>/`, one or two folders deep), sets aside vendored, fixture, excluded and
unknown-location files, checks frontmatter with `claude_config.parse_frontmatter` (clean, with
`name` and `description`), and records tracked symlinks that share a skill tree inside the
repository or resolve outside it, `skills-lock.json` and hashed marketplace manifests as
evidence. Tests: `tests/test_skill_library_condition.py`.
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path, PurePosixPath
from typing import TypedDict

from lib.instructions.claude_config import parse_frontmatter
from lib.run.run_scope import Scope

HARNESS_DIRS = (".claude", ".agents", ".gemini", ".codex", ".github", ".cursor", ".opencode")
# A skills/ folder at one of these prefixes ("" is the root); plugins/<name>/ is matched apart.
LIBRARY_PREFIXES = ("", *(f"{d}/" for d in HARNESS_DIRS))
SKILL_FILE = "SKILL.md"
VENDORED_DIRS = frozenset({"node_modules", "vendor", "vendored", "third_party", "third-party",
                           "bower_components", "site-packages", ".venv", "venv"})
FIXTURE_DIRS = frozenset({"fixtures", "fixture", "__fixtures__", "testdata", "test-data",
                          "test_data", "test-fixtures", "__snapshots__"})
SET_ASIDE_DIRS = VENDORED_DIRS | FIXTURE_DIRS
LOCK_FILE = "skills-lock.json"
MANIFEST_NAMES = (".claude-plugin/marketplace.json", ".claude-plugin/plugin.json")
_HASH_KEY = re.compile(r'"(?:sha256|sha512|sha|integrity|hash|digest|contentHash)"\s*:\s*"[^"]{8,}"',
                       re.IGNORECASE)
_SYMLINK_MODE = "120000"


class LibrarySkill(TypedDict):
    path: str
    location: str


class SharedLink(TypedDict):
    path: str
    target: str


class SkillLibraryBlock(TypedDict, total=False):
    available: bool
    reason: str
    present: bool
    skills_counted: int
    skills: list[LibrarySkill]
    invalid_frontmatter: int
    invalid_files: list[str]
    set_aside_vendored: int
    set_aside_fixture: int
    set_aside_location: int
    symlinks_outside: int
    shared_by_symlink: list[SharedLink]
    lock_files: list[str]
    hashed_manifests: list[str]


def _tracked(repo: Path) -> dict[str, str] | None:
    """Tracked path -> git mode, or None when the root is not a git repository."""
    try:
        out = subprocess.run(["git", "-C", str(repo), "ls-files", "-s", "-z"],
                             capture_output=True, check=False, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    entries: dict[str, str] = {}
    for record in out.stdout.split(b"\0"):
        if b"\t" not in record:
            continue
        meta, path = record.split(b"\t", 1)
        entries[path.decode("utf-8", "surrogateescape")] = meta.split(b" ", 1)[0].decode("ascii")
    return entries


def library_location(rel: str) -> str | None:
    """The known ``skills/`` folder holding ``rel`` (a ``SKILL.md`` path), or None."""
    parts = PurePosixPath(rel).parts
    if not parts or parts[-1] != SKILL_FILE:
        return None
    for depth in (2, 3):  # skills/<name>/SKILL.md, skills/<group>/<name>/SKILL.md
        if len(parts) <= depth or parts[-depth - 1] != "skills":
            continue
        prefix = "/".join(parts[:-depth - 1])
        prefix = f"{prefix}/" if prefix else ""
        plugin = len(parts[:-depth - 1]) == 2 and parts[0] == "plugins"
        if prefix in LIBRARY_PREFIXES or plugin:
            return f"{prefix}skills"
    return None


def valid_frontmatter(text: str) -> bool:
    """True when ``text`` opens with clean frontmatter naming a ``name`` and a ``description``."""
    fm = parse_frontmatter(text)
    if fm is None or fm.errors:
        return False
    keys = {e.key for e in fm.entries if e.value}
    return {"name", "description"} <= keys


def _inside(repo: Path, path: Path) -> str | None:
    """``path`` resolved, repository-relative (POSIX), or None when it leaves the repository."""
    try:
        real = Path(os.path.realpath(path))
        return real.relative_to(repo).as_posix()
    except (OSError, ValueError):
        return None


def _set_aside(rel: str, extra: set[str]) -> str | None:
    dirs = PurePosixPath(rel).parts[:-1]
    if any(d in VENDORED_DIRS or d in extra for d in dirs):
        return "vendored"
    if any(d in FIXTURE_DIRS for d in dirs):
        return "fixture"
    return None


def _links(repo: Path, tracked: dict[str, str]) -> tuple[dict[str, str], int]:
    """Known-location symlinks resolving inside the repository (link -> target), and the
    count of tracked symlinks that resolve outside it."""
    inside: dict[str, str] = {}
    outside = 0
    for rel, mode in tracked.items():
        if mode != _SYMLINK_MODE:
            continue
        target = _inside(repo, repo / rel)
        if target is None:
            outside += 1
            continue
        # a link to a whole skills/ tree, or to one skill folder, in a known location
        if library_location(f"{rel}/x/{SKILL_FILE}") or library_location(f"{rel}/{SKILL_FILE}"):
            inside[rel] = target
    return inside, outside


def _location(rel: str, scope_rels: list[str], links: dict[str, str]) -> str | None:
    """The known location of ``rel``: from the root, from a scope folder, or through a link."""
    found = library_location(rel)
    for base in scope_rels:
        if found is None and rel.startswith(f"{base}/"):
            inner = library_location(rel[len(base) + 1:])
            found = None if inner is None else f"{base}/{inner}"
    if found is None:
        found = next((link for link, target in sorted(links.items()) if rel.startswith(f"{target}/")), None)
    return found


def scan_skill_library(repo_root: Path, scope: Scope | None = None,
                       excludes: tuple[set[str], list[str]] | None = None) -> SkillLibraryBlock:
    """Build the ``skill_library`` block (see the module doc)."""
    repo = repo_root.resolve()
    tracked = _tracked(repo)
    if tracked is None:
        return SkillLibraryBlock(available=False,
                                 reason="not_a_git_repository: committed files cannot be listed")
    extra = set(excludes[0]) if excludes else set()
    links, outside = _links(repo, tracked)
    scope_rels = [] if scope is None else [r for r in (_inside(repo, f) for f in scope.folders) if r]
    counts = {"vendored": 0, "fixture": 0, "location": 0}
    skills: dict[str, LibrarySkill] = {}
    invalid: set[str] = set()
    for rel, mode in sorted(tracked.items()):
        if PurePosixPath(rel).name != SKILL_FILE:
            continue
        if scope is not None and not scope.contains(repo / rel):
            continue
        reason = _set_aside(rel, extra)
        if reason is not None:
            counts[reason] += 1
            continue
        real = rel
        if mode == _SYMLINK_MODE:
            resolved = _inside(repo, repo / rel)
            if resolved is None:
                continue  # counted in symlinks_outside
            real = resolved
        location = _location(rel, scope_rels, links)
        if location is None:
            counts["location"] += 1
            continue
        if real in skills:
            continue
        try:
            text = (repo / real).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if not valid_frontmatter(text):
            invalid.add(real)
            continue
        skills[real] = LibrarySkill(path=real, location=location)
    counted = {s["path"] for s in skills.values()}
    shared = [SharedLink(path=link, target=target) for link, target in sorted(links.items())
              if any(p.startswith(f"{target}/") for p in counted)]
    locks = sorted(r for r in tracked if PurePosixPath(r).name == LOCK_FILE
                   and _set_aside(r, extra) is None)
    hashed = sorted(r for r in tracked if r in MANIFEST_NAMES and _hashed(repo / r))
    return SkillLibraryBlock(
        available=True, present=bool(skills), skills_counted=len(skills),
        skills=sorted(skills.values(), key=lambda s: s["path"]),
        invalid_frontmatter=len(invalid), invalid_files=sorted(invalid),
        set_aside_vendored=counts["vendored"], set_aside_fixture=counts["fixture"],
        set_aside_location=counts["location"], symlinks_outside=outside,
        shared_by_symlink=shared, lock_files=locks, hashed_manifests=hashed)


def _hashed(path: Path) -> bool:
    try:
        return bool(_HASH_KEY.search(path.read_text(encoding="utf-8", errors="replace")))
    except OSError:
        return False
