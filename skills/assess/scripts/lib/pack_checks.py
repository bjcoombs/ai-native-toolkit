"""The pack collector (#1273, part B of #1266): every ``--pack`` check evaluated offline, counts only.

``collect(repo_root, excludes, packs)`` is the ``pack_checks`` entry of the scan table
(``lib/scan_registry.py``), a spread entry that writes two run-context keys, and neither
when no pack was passed, so a run without ``--pack`` writes the same ``run-context.json`` as
before:

- ``pack``: ``{<pack>: {<check>: value}}``, so a check reads at ``pack.<pack>.<check>``, the
  signal path ``lib/packs.py`` gives it. ``exists`` and ``key_exists`` give a boolean,
  ``glob_count`` and ``contains`` a count of files.
- ``packs``: each pack's ``{name, version, sha256}`` (``packs.pack_identity``), in pass order.

A check that cannot be answered reads ``{"available": false, "reason": <code>}``, never false
or 0: ``unreadable_file`` (a file the walk found but could not read), ``unparsable_file`` (a
``key_exists`` file that is not valid TOML, JSON or YAML) or ``unsupported_yaml`` (YAML the
reader in ``lib/pack_yaml.py`` does not follow). The reason is a code alone, never a path.

What it reads. The files under the repository root, walked once per run without following a
symlinked directory, minus ``.git``, ``.assess``, the dependency and cache folders an install
or a tool run writes (``SKIPPED_DIRS``: ``node_modules``, ``.venv``, ``__pycache__`` and the
like, at any depth, so a check reads the same before and after an install) and the
``.assess/config.toml`` excludes (``assess_config.is_user_excluded``). It does not ask git which
files are tracked, since the collector runs no program, so any other untracked file is read,
build output included (``dist/``, ``build/``, ``target/``): some repositories track those
folders, so they are not skipped, and a pack author whose globs reach them excludes them in
``.assess/config.toml``. A symlinked file counts only when it resolves inside the
root, so a link pointing outside is never followed or read. ``exists`` is true for a file of
the walk or a directory holding one; ``glob_count`` counts the walk's files whose path matches
the glob (``*`` and ``?`` within one segment, ``**`` across any number, ``[...]`` a class);
``contains`` counts the matching files holding the literal text, compared as UTF-8 bytes;
``key_exists`` reads the one named file with the reader its suffix picks, ignoring case as
the loader does (``.toml`` ``tomllib``, ``.json`` ``json``, ``.yaml``/``.yml`` ``pack_yaml``),
and a missing file is false. It never runs a program.

A path the ``.assess/config.toml`` excludes is outside the assessment, so it reads as absent:
``exists`` and ``key_exists`` false, and no file counted. A ``[...]`` class is a plain set or
range (``[!...]`` negates); a ``]`` cannot be its first member.

Privacy: no path, file name or file text reaches the run context; the values are booleans,
counts and reason codes, and the counts-only copy keeps the pack and check names as
identifier keys (``counts_only_rules.IDENTIFIER_KEYED_MAPS``). Counts-only drops an
unavailable check's reason code (the global ``reason`` rule) and keeps ``available: false``. Tests:
``tests/test_pack_collector.py``.
"""
from __future__ import annotations

import json
import os
import re
import tomllib
from collections.abc import Mapping, Sequence
from functools import cached_property
from pathlib import Path, PurePosixPath
from typing import Any, TypedDict

from lib.assess_config import ASSESS_DIR, is_user_excluded
from lib.pack_yaml import YamlError, lookup, parse
from lib.packs import Pack, PackIdentity, Rule, pack_identity

PACK_BLOCK = "pack"
IDENTITY_BLOCK = "packs"
UNREADABLE = "unreadable_file"
UNPARSABLE = "unparsable_file"

Excludes = tuple[set[str], list[str]]
# Folders an install or a tool run writes, never part of a clone: skipped at any depth.
SKIPPED_DIRS = frozenset({".git", "node_modules", ".venv", "venv", ".tox", ".nox", "__pycache__",
                          ".mypy_cache", ".pytest_cache", ".ruff_cache", ".gradle", "mutants"})
CheckValue = bool | int | dict[str, Any]


class PackBlocks(TypedDict, total=False):
    pack: dict[str, dict[str, CheckValue]]
    packs: list[PackIdentity]


def _unavailable(code: str) -> dict[str, Any]:
    return {"available": False, "reason": code}


def _segment(part: str) -> str:
    out: list[str] = []
    i = 0
    while i < len(part):
        ch = part[i]
        if ch == "*":
            out.append("[^/]*")
        elif ch == "?":
            out.append("[^/]")
        elif ch == "[" and part.find("]", i + 2) > 0:
            end = part.find("]", i + 2)
            body = part[i + 1:end].replace("\\", "\\\\")
            out.append("[" + ("^" + body[1:] if body.startswith("!") else body) + "]")
            i = end
        else:
            out.append(re.escape(ch))
        i += 1
    return "".join(out)


def glob_regex(glob: str) -> re.Pattern[str]:
    """The pattern a repository-relative POSIX path must fully match for ``glob``."""
    parts = glob.split("/")
    out: list[str] = []
    for index, part in enumerate(parts):
        last = index == len(parts) - 1
        if part == "**":
            out.append(".*" if last else "(?:[^/]+/)*")
        else:
            out.append(_segment(part) + ("" if last else "/"))
    return re.compile("".join(out))


class _Tree:
    """The repository's files, walked once: repository-relative POSIX path -> real file."""

    def __init__(self, root: Path, excludes: Excludes) -> None:
        self.root = root
        self.real_root = root.resolve()
        self.excludes = excludes

    def _inside(self, path: Path) -> Path | None:
        try:
            real = path.resolve()
        except (OSError, RuntimeError):
            return None
        return real if real == self.real_root or self.real_root in real.parents else None

    @cached_property
    def files(self) -> dict[str, Path]:
        dirs, patterns = self.excludes
        found: dict[str, Path] = {}
        for dirpath, dirnames, filenames in os.walk(self.root):
            rel_dir = Path(dirpath).relative_to(self.root)
            dirnames[:] = sorted(d for d in dirnames
                                 if d not in SKIPPED_DIRS and not (rel_dir == Path(".") and d == ASSESS_DIR)
                                 and not is_user_excluded(rel_dir / d, dirs, patterns))
            for name in sorted(filenames):
                rel = rel_dir / name
                if is_user_excluded(rel, dirs, patterns):
                    continue
                real = self._inside(Path(dirpath) / name)
                if real is not None and real.is_file():
                    found[rel.as_posix()] = real
        return found

    @cached_property
    def dirs(self) -> frozenset[str]:
        return frozenset(str(parent) for rel in self.files
                         for parent in PurePosixPath(rel).parents if str(parent) != ".")

    def matching(self, glob: str) -> list[Path]:
        pattern = glob_regex(glob)
        return [real for rel, real in sorted(self.files.items()) if pattern.fullmatch(rel)]


def _contains(tree: _Tree, glob: str, text: str) -> CheckValue:
    needle = text.encode("utf-8")
    count = 0
    for real in tree.matching(glob):
        try:
            count += needle in real.read_bytes()
        except OSError:
            return _unavailable(UNREADABLE)
    return count


def _key_exists(tree: _Tree, file: str, key: str) -> CheckValue:
    real = tree.files.get(file)
    if real is None:
        return False
    try:
        text = real.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return _unavailable(UNREADABLE)
    suffix = file.lower()
    try:
        if suffix.endswith((".yaml", ".yml")):
            return lookup(parse(text), key)
        data: Any = tomllib.loads(text) if suffix.endswith(".toml") else json.loads(text)
    except YamlError as e:
        return _unavailable(e.code)
    except (tomllib.TOMLDecodeError, ValueError):
        return _unavailable(UNPARSABLE)
    for part in key.split("."):
        if not isinstance(data, Mapping) or part not in data:
            return False
        data = data[part]
    return True


def evaluate(rule: Rule, tree: _Tree) -> CheckValue:
    """One check's value: a boolean, a count, or the unavailable marker with a reason code."""
    if rule["kind"] == "exists":
        return rule["path"] in tree.files or rule["path"] in tree.dirs
    if rule["kind"] == "glob_count":
        return len(tree.matching(rule["glob"]))
    if rule["kind"] == "contains":
        return _contains(tree, rule["glob"], rule["text"])
    return _key_exists(tree, rule["file"], rule["key"])


def collect(repo_root: Path, excludes: Excludes, packs: Sequence[Pack] | None) -> PackBlocks:
    """The ``pack`` and ``packs`` blocks for the passed packs; ``{}`` when none was passed."""
    if not packs:
        return {}
    tree = _Tree(repo_root, excludes or (set(), []))
    signals = {pack["name"]: {check["id"]: evaluate(check["rule"], tree) for check in pack["checks"]}
               for pack in packs}
    return {"pack": signals, "packs": [pack_identity(pack) for pack in packs]}
