"""Whether each skill's scripts have tests beside them, in the skill's own folder (#1045).

A skill's scripts run with an agent's permissions every time the skill triggers, and in a skill
marketplace a skill ships on its own: only the tests in its folder travel with it. This module
lists, for each skill folder (a folder holding a ``SKILL.md`` that
``instruction_placement.skill_and_agent_files`` reads), its script files and the test files
beside them. No model is involved and no suite runs.

**Presence, not coverage.** The check records whether tests exist beside the scripts. It does not
measure how much of the scripts they cover: ``/assess`` never runs a suite, and a coverage figure
read from a report would describe some earlier run, not the commit.

The tendency it compensates for is guardrail erosion at the edge: a skill's scripts grow without
a test that would catch the change, because nothing in the folder asks for one.

**What counts.** Under the skill folder, outside the ``SKIP_DIRS`` (``tests``, ``test``,
``fixtures``, ``examples``, caches, ``node_modules``):

- a **script** is a file with a ``SCRIPT_EXTENSIONS`` suffix (``.py``, ``.sh``, ``.bash``, ``.js``,
  ``.mjs``, ``.cjs``, ``.ts``, ``.rb``) that is not itself a test file;
- a **test** is a file named like a test anywhere in it (``test_*.py``, ``*_test.py``,
  ``*_test.go``, ``*.test.[jt]s``, ``*.spec.[jt]s``, ``*.bats``), or a code file
  (``TEST_CODE_EXTENSIONS``) under a ``tests``/``test`` folder other than package and fixture
  plumbing (``TEST_SUPPORT_NAMES``: ``__init__.py``, ``conftest.py``, ...); nothing under test data
  (``TEST_DATA_DIRS``: fixtures, goldens, snapshots) is a test, so a README or a fixture tree
  there counts for nothing.

A skill with scripts and no test beside them is ``untested``. **Tested elsewhere** (information
only): a test file outside the skill folder with a line that names the skill folder's name as a
path segment and, after it on the same line, one of the skill's script file names
(``"fleet" / "scripts" / "fleet.py"``, ``skills/ghsync/scripts/ghsync.sh``) is listed under
``tested_elsewhere``, up to
``ELSEWHERE_MAX``. It changes nothing: those tests do not ship with the skill, so the skill still
reads untested, and the report says where its tests are and to move or copy them into the folder.
``script_tested_elsewhere(repo_root, rel_path)`` asks the same question of one script, with the same
matcher, for the "does this file have a test file?" probe in ``lib.testing.sibling_tests`` (#1233):
a script tested only from the top-level ``tests/`` then reads as having a test file in
``test_focus`` and on the hotspot pages, as it does here. The probe applies no user excludes, like
the rest of ``sibling_tests``, so the two agree when ``.assess`` sets none; with excludes set, a
test under an excluded folder credits the script there but is not listed here.

Block (``skill_script_tests`` in run-context.json)::

    {"available": True,
     "skills": [{"path": str, "scripts": int, "tests": int,
                 "untested": bool | None,          # None: the skill holds no script
                 "tested_elsewhere": [str, ...]}], # repository-relative, information only
     "skills_with_scripts": int, "skills_untested": int}

``skills`` is sorted by path. With ``scope`` (a skill unit's folder in the unit pass) only the
skills under it are listed, while ``tested_elsewhere`` still searches the whole repository. With no
skill holding a script, ``skills_with_scripts`` is 0 and the condition reads not measured
(``no_skill_scripts``), never met. Counts-only output drops the paths, or hashes them under
``--hash-paths``.

Skill script tests (#1045), presence not coverage, no model. `scan_skill_script_tests(repo_root,
scope, excludes)` lists each skill folder's scripts (`SCRIPT_EXTENSIONS`, outside `SKIP_DIRS`, not
test-named) and its tests (`is_test_file`); a skill with scripts and no test is `untested`;
`tested_elsewhere` names outside test files that reference a script file name with the skill folder
name as a path segment (information only, up to `ELSEWHERE_MAX`). Tests:
`tests/test_skill_script_tests.py`.

**Read once per run (#1189).** The unit pass calls the scan once per skill unit, and each untested
skill needs the repository's test files. ``_outside_tests`` is cached per (repository root, HEAD,
excludes), the ``doc_staleness`` pattern, so a run reads them once however many units it scans.
``assess_fix.clear_scan_caches`` clears it with the other ``lib`` caches before a re-read.
"""
from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Iterator
from functools import lru_cache
from pathlib import Path, PurePosixPath
from typing import TypedDict

from lib.assess_config import is_user_excluded
from lib.git_churn import GIT_TIMEOUT_SECONDS
from lib.instructions.instruction_placement import skill_and_agent_files

SCRIPT_EXTENSIONS = frozenset({".py", ".sh", ".bash", ".js", ".mjs", ".cjs", ".ts", ".rb"})
SKIP_DIRS = frozenset({"tests", "test", "fixtures", "examples", "__pycache__", "node_modules", ".venv",
                       ".pytest_cache", ".mypy_cache", ".ruff_cache", ".git"})
TEST_DIRS = frozenset({"tests", "test"})
# A file under a tests folder is a test only when it is code, outside fixtures and goldens:
# a README, a fixture tree or a golden JSON there is test data, not a test.
TEST_CODE_EXTENSIONS = SCRIPT_EXTENSIONS | {".go", ".bats", ".tsx", ".jsx"}
TEST_DATA_DIRS = frozenset({"fixtures", "golden", "goldens", "data", "snapshots", "__snapshots__"})
# Package and fixture plumbing under a tests folder: on its own it tests nothing.
TEST_SUPPORT_NAMES = frozenset({"__init__.py", "conftest.py", "setup.py", "jest.config.js", "jest.config.ts",
                                "vitest.config.ts", "vitest.config.js"})
# Folders the repository-wide walk never enters.
WALK_SKIP = frozenset({".git", "node_modules", ".venv", "__pycache__", ".pytest_cache", ".mypy_cache",
                       ".ruff_cache", "dist", "build"})
ELSEWHERE_MAX = 3
_TEST_NAME = re.compile(r"^(test_.+\.py|.+_test\.(py|go)|.+\.(test|spec)\.[cm]?[jt]sx?|.+\.bats)$")
_READ_MAX_BYTES = 512_000  # a test file larger than this is not read for references


class SkillTests(TypedDict):
    path: str  # the skill folder, repository-relative
    scripts: int
    tests: int
    untested: bool | None  # None: the skill holds no script
    tested_elsewhere: list[str]


class SkillScriptTestsBlock(TypedDict):
    """``run-context.json`` ``skill_script_tests`` (see the module doc)."""

    available: bool
    skills: list[SkillTests]
    skills_with_scripts: int
    skills_untested: int


def is_test_file(rel: PurePosixPath) -> bool:
    """A test by name (``test_*.py`` and the like), or a code file under a ``tests``/``test`` folder
    outside its test data (``TEST_DATA_DIRS``)."""
    folders = rel.parts[:-1]
    if any(part in TEST_DATA_DIRS for part in folders):
        return False
    if _TEST_NAME.match(rel.name):
        return True
    return (rel.suffix in TEST_CODE_EXTENSIONS and rel.name not in TEST_SUPPORT_NAMES
            and any(part in TEST_DIRS for part in folders))


def _files(folder: Path, dirs: set[str], pats: list[str], repo: Path) -> Iterator[Path]:
    """The files under ``folder``, pruning ``WALK_SKIP`` and excluded folders as it walks."""
    for here, subdirs, names in os.walk(folder):
        base = Path(here)
        subdirs[:] = sorted(d for d in subdirs if d not in WALK_SKIP and not (base / d).is_symlink()
                            and not is_user_excluded((base / d).relative_to(repo), dirs, pats))
        for name in sorted(names):
            path = base / name
            if path.is_file() and not path.is_symlink() and not is_user_excluded(path.relative_to(repo), dirs, pats):
                yield path


def _count(folder: Path, repo: Path, dirs: set[str], pats: list[str]) -> tuple[list[str], int]:
    """The skill's script file names and its test count."""
    scripts: list[str] = []
    tests = 0
    for path in _files(folder, dirs, pats, repo):
        rel = PurePosixPath(path.relative_to(folder).as_posix())
        if is_test_file(rel):
            tests += 1
        elif path.suffix in SCRIPT_EXTENSIONS and not any(p in SKIP_DIRS for p in rel.parts[:-1]):
            scripts.append(path.name)
    return scripts, tests


def _head(repo: Path) -> str | None:
    """HEAD's sha, the cache key of the test-file index; None outside git or on failure."""
    try:
        return subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True,
                              encoding="utf-8", check=False, timeout=GIT_TIMEOUT_SECONDS).stdout.strip() or None
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None


def _outside_tests(repo: Path, dirs: set[str], pats: list[str]) -> tuple[tuple[Path, tuple[str, ...]], ...]:
    """Every test file in the repository with its lines, read once per run (see the module doc)."""
    return _test_index(repo, _head(repo), frozenset(dirs), tuple(pats))


@lru_cache(maxsize=4)
def _test_index(repo: Path, head: str | None, dirs: frozenset[str],
                pats: tuple[str, ...]) -> tuple[tuple[Path, tuple[str, ...]], ...]:
    """The test-file index, built once per (repository, HEAD, excludes)."""
    out: list[tuple[Path, tuple[str, ...]]] = []
    for path in _files(repo, set(dirs), list(pats), repo):
        if not is_test_file(PurePosixPath(path.relative_to(repo).as_posix())):
            continue
        try:
            if path.stat().st_size > _READ_MAX_BYTES:
                continue
            out.append((path, tuple(path.read_text(encoding="utf-8", errors="replace").splitlines())))
        except OSError:
            continue
    return tuple(out)


def _elsewhere(repo: Path, folder: Path, scripts: list[str],
               outside: tuple[tuple[Path, tuple[str, ...]], ...]) -> list[str]:
    """Outside test files with a line that names one of ``scripts`` after the skill folder's name
    as a path segment (``"fleet" / "scripts" / "fleet.py"``, ``skills/ghsync/scripts/ghsync.sh``)."""
    name = re.escape(folder.name)
    alternatives = "|".join(re.escape(s) for s in sorted(set(scripts)))
    path_like = re.compile(rf"[\"'/\\]{name}[\"'/\\].*?(?<![\w.-])(?:{alternatives})(?![\w-])")
    found: list[str] = []
    for path, lines in outside:
        if path.is_relative_to(folder):
            continue
        if any(path_like.search(line) for line in lines):
            found.append(path.relative_to(repo).as_posix())
            if len(found) == ELSEWHERE_MAX:
                break
    return found


def _skill_folder_of(repo: Path, rel_path: str) -> Path | None:
    """The nearest folder above ``rel_path`` that holds a ``SKILL.md``, inside ``repo``, or None."""
    rel = PurePosixPath(rel_path)
    if rel.is_absolute() or ".." in rel.parts:
        return None  # never a folder outside the repository
    for parent in rel.parents:
        if parent == PurePosixPath("."):
            return None
        folder = repo.joinpath(*parent.parts)
        if (folder / "SKILL.md").is_file():
            return folder
    return None


def script_tested_elsewhere(repo_root: Path, rel_path: str) -> bool:
    """True when ``rel_path`` is one of a skill folder's scripts and a test file outside that folder
    names it, by the matcher behind ``tested_elsewhere`` (#1233). ``test_focus`` and the hotspot
    pages read it through ``sibling_tests.has_sibling_test``, so a script tested from the top-level
    ``tests/`` reads as having a test file there too. A script whose file name another script in the
    same skill folder shares gets no credit, since the matcher keys on the name. Never raises."""
    try:
        repo = repo_root.resolve()
        folder = _skill_folder_of(repo, rel_path)
        if folder is None:
            return False
        rel = PurePosixPath(Path(rel_path).relative_to(folder.relative_to(repo)).as_posix())
        if (rel.suffix not in SCRIPT_EXTENSIONS or is_test_file(rel)
                or any(p in SKIP_DIRS for p in rel.parts[:-1])):
            return False
        scripts, _tests = _count(folder, repo, set(), [])
        if scripts.count(rel.name) > 1:
            return False  # the matcher keys on the name: it cannot say which same-named script a test names
        return bool(_elsewhere(repo, folder, [rel.name], _outside_tests(repo, set(), [])))
    except (OSError, ValueError):
        return False


def _skill_folders(repo: Path, scope: Path | None, excludes: tuple[set[str], list[str]] | None) -> list[Path]:
    folder = scope.resolve() if scope is not None else repo
    return sorted({p.parent.resolve() for p, kind in skill_and_agent_files(repo, None, excludes)
                   if kind == "skill" and p.resolve().is_relative_to(folder)})


def scan_skill_script_tests(
    repo_root: Path, scope: Path | None = None, excludes: tuple[set[str], list[str]] | None = None,
) -> SkillScriptTestsBlock:
    """Build the ``skill_script_tests`` block (see the module doc)."""
    repo = repo_root.resolve()
    dirs, pats = excludes or (set(), [])
    folders = _skill_folders(repo, scope, excludes)
    outside: tuple[tuple[Path, tuple[str, ...]], ...] | None = None
    skills: list[SkillTests] = []
    for folder in folders:
        scripts, tests = _count(folder, repo, dirs, pats)
        untested = None if not scripts else tests == 0
        elsewhere: list[str] = []
        if untested:
            if outside is None:  # only when some skill needs it; cached across the run's scans
                outside = _outside_tests(repo, dirs, pats)
            elsewhere = _elsewhere(repo, folder, scripts, outside)
        skills.append(SkillTests(path=folder.relative_to(repo).as_posix(), scripts=len(scripts), tests=tests,
                                 untested=untested, tested_elsewhere=elsewhere))
    return SkillScriptTestsBlock(
        available=True, skills=skills,
        skills_with_scripts=sum(s["untested"] is not None for s in skills),
        skills_untested=sum(s["untested"] is True for s in skills))
