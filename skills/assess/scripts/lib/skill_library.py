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
Every file and link target is read from the commit (``git cat-file --batch``), never the
working tree: a link's target is its committed blob, resolved lexically against the link's
folder, so a checkout with ``core.symlinks=false`` (the Git for Windows default) and an
uncommitted edit read the same as any other checkout. An absolute target, or one that climbs
above the root, is outside.

**Supporting evidence, not counted:**

- ``shared_by_symlink``: a tracked symlink in a known location (``.agents/skills`` ->
  ``.claude/skills``; under ``--scope`` also one at the scope folder's prefixes) that resolves to a folder inside the repository holding counted skills,
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
`SKILL.md` files (`git ls-files -s -z`, contents and link targets from `git cat-file --batch`) in a known location (`skills/` at the root, a harness
folder or `plugins/<name>/`, one or two folders deep), sets aside vendored, fixture, excluded and
unknown-location files, checks frontmatter with `claude_config.parse_frontmatter` (clean, with
`name` and `description`), and records tracked symlinks that share a skill tree inside the
repository or resolve outside it, `skills-lock.json` and hashed marketplace manifests as
evidence. Tests: `tests/test_skill_library_condition.py`.
"""
from __future__ import annotations

import posixpath
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
_DRIVE = re.compile(r"^[A-Za-z]:[/\\\\]")


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


Tracked = dict[str, tuple[str, str]]  # path -> (git mode, blob object id)


def _git(repo: Path, args: list[str], stdin: bytes | None = None) -> bytes | None:
    try:
        out = subprocess.run(["git", "-C", str(repo), *args], input=stdin,
                             capture_output=True, check=False, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout if out.returncode == 0 else None


def _tracked(repo: Path) -> Tracked | None:
    """Tracked path -> (git mode, object id), or None when the root is not a git repository."""
    raw = _git(repo, ["ls-files", "-s", "-z"])
    if raw is None:
        return None
    entries: Tracked = {}
    for record in raw.split(b"\0"):
        if b"\t" not in record:
            continue
        meta, path = record.split(b"\t", 1)
        mode, oid = meta.decode("ascii").split(" ")[:2]
        entries[path.decode("utf-8", "surrogateescape")] = (mode, oid)
    return entries


def _blobs(repo: Path, oids: set[str]) -> dict[str, bytes]:
    """Each object id's committed content (``git cat-file --batch``); a missing one is left out."""
    if not oids:
        return {}
    order = sorted(oids)
    raw = _git(repo, ["cat-file", "--batch"], "".join(f"{o}\n" for o in order).encode("ascii")) or b""
    found: dict[str, bytes] = {}
    pos = 0
    while pos < len(raw):
        eol = raw.index(b"\n", pos)
        head = raw[pos:eol].decode("ascii", "replace").split(" ")
        pos = eol + 1
        if len(head) != 3:
            continue  # "<oid> missing"
        size = int(head[2])
        found[head[0]] = raw[pos:pos + size]
        pos += size + 1
    return found


def _real(path: str, links: dict[str, str], depth: int = 0) -> str | None:
    """``path`` with each tracked symlink in it replaced by its committed target, resolved
    lexically; None when a target is absolute or leaves the repository, or links loop."""
    parts = path.split("/")
    for i in range(len(parts)):
        here = "/".join(parts[:i + 1])
        if here not in links:
            continue
        target = links[here]
        if depth > 8 or target.startswith("/") or _DRIVE.match(target):
            return None
        joined = posixpath.normpath(posixpath.join(posixpath.dirname(here), target))
        if joined == ".." or joined.startswith("../"):
            return None
        rest = "/".join(parts[i + 1:])
        joined = "" if joined == "." else joined
        return _real("/".join(p for p in (joined, rest) if p), links, depth + 1)
    return path


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
    """``path`` repository-relative (POSIX), or None when it is outside the repository."""
    try:
        return path.resolve().relative_to(repo).as_posix()
    except (OSError, ValueError):
        return None


def _set_aside(rel: str, extra: set[str]) -> str | None:
    dirs = PurePosixPath(rel).parts[:-1]
    if any(d in VENDORED_DIRS or d in extra for d in dirs):
        return "vendored"
    if any(d in FIXTURE_DIRS for d in dirs):
        return "fixture"
    return None


def _known(rel: str, scope_rels: list[str]) -> str | None:
    """The known location of ``rel`` (a ``SKILL.md`` path), from the root or a scope folder."""
    found = library_location(rel)
    for base in scope_rels:
        if found is None and rel.startswith(f"{base}/"):
            inner = library_location(rel[len(base) + 1:])
            found = None if inner is None else f"{base}/{inner}"
    return found


def _links(raw: dict[str, str], scope_rels: list[str]) -> tuple[dict[str, str], int]:
    """Known-location symlinks resolving inside the repository (link -> target), and the
    count of tracked symlinks that resolve outside it. Targets are the committed ones."""
    inside: dict[str, str] = {}
    outside = 0
    for rel in raw:
        target = _real(rel, raw)
        if target is None:
            outside += 1
            continue
        # a link to a whole skills/ tree, or to one skill folder, in a known location
        if _known(f"{rel}/x/{SKILL_FILE}", scope_rels) or _known(f"{rel}/{SKILL_FILE}", scope_rels):
            inside[rel] = target
    return inside, outside


def _location(rel: str, scope_rels: list[str], links: dict[str, str]) -> str | None:
    """The known location of ``rel``: from the root, from a scope folder, or through a link."""
    found = _known(rel, scope_rels)
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
    scope_rels = [] if scope is None else [r for r in (_inside(repo, f) for f in scope.folders) if r]
    link_oids = {oid for mode, oid in tracked.values() if mode == _SYMLINK_MODE}
    names = {r for r in tracked if PurePosixPath(r).name == SKILL_FILE or r in MANIFEST_NAMES}
    blobs = _blobs(repo, link_oids | {tracked[r][1] for r in names})
    raw = {r: blobs.get(oid, b"").decode("utf-8", "surrogateescape").strip()
           for r, (mode, oid) in tracked.items() if mode == _SYMLINK_MODE}
    links, outside = _links(raw, scope_rels)
    counts = {"vendored": 0, "fixture": 0, "location": 0}
    skills: dict[str, LibrarySkill] = {}
    invalid: set[str] = set()
    for rel, (mode, _oid) in sorted(tracked.items()):
        if PurePosixPath(rel).name != SKILL_FILE:
            continue
        if scope is not None and not scope.contains(repo / rel):
            continue
        reason = _set_aside(rel, extra)
        if reason is not None:
            counts[reason] += 1
            continue
        real = rel if mode != _SYMLINK_MODE else _real(rel, raw)
        if real is None:
            continue  # counted in symlinks_outside
        if real not in tracked or tracked[real][0] == _SYMLINK_MODE:
            counts["location"] += 1  # a link to no committed file
            continue
        location = _location(rel, scope_rels, links)
        if location is None:
            counts["location"] += 1
            continue
        if real in skills:
            continue
        text = _text(repo, tracked[real][1], blobs)
        if not valid_frontmatter(text):
            invalid.add(real)
            continue
        skills[real] = LibrarySkill(path=real, location=location)
    counted = {s["path"] for s in skills.values()}
    shared = [SharedLink(path=link, target=target) for link, target in sorted(links.items())
              if any(p.startswith(f"{target}/") for p in counted)]
    locks = sorted(r for r in tracked if PurePosixPath(r).name == LOCK_FILE
                   and _set_aside(r, extra) is None)
    hashed = sorted(r for r in tracked if r in MANIFEST_NAMES
                    and _HASH_KEY.search(_text(repo, tracked[r][1], blobs)))
    return SkillLibraryBlock(
        available=True, present=bool(skills), skills_counted=len(skills),
        skills=sorted(skills.values(), key=lambda s: s["path"]),
        invalid_frontmatter=len(invalid), invalid_files=sorted(invalid),
        set_aside_vendored=counts["vendored"], set_aside_fixture=counts["fixture"],
        set_aside_location=counts["location"], symlinks_outside=outside,
        shared_by_symlink=shared, lock_files=locks, hashed_manifests=hashed)


def _text(repo: Path, oid: str, blobs: dict[str, bytes]) -> str:
    """A tracked file's committed text, read from the batch or, past it, on its own."""
    data = blobs.get(oid)
    if data is None:
        data = _git(repo, ["cat-file", "blob", oid]) or b""
    return data.decode("utf-8", "replace")
