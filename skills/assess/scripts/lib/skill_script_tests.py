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
  (``TEST_CODE_EXTENSIONS``) under a ``tests``/``test`` folder; nothing under test data
  (``TEST_DATA_DIRS``: fixtures, goldens, snapshots) is a test, so a README or a fixture tree
  there counts for nothing.

A skill with scripts and no test beside them is ``untested``. **Tested elsewhere** (information
only): a test file outside the skill folder whose text names one of the skill's script file names
together with the skill folder's name as a path segment (``"fleet" / "scripts" / "fleet.py"``,
``skills/ghsync/scripts/ghsync.sh``) is listed under ``tested_elsewhere``, up to
``ELSEWHERE_MAX``. It changes nothing: those tests do not ship with the skill, so the skill still
reads untested, and the report says where its tests are and to move or copy them into the folder.

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
"""
from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path, PurePosixPath
from typing import TypedDict

from lib.assess_config import is_user_excluded
from lib.instruction_placement import skill_and_agent_files

SCRIPT_EXTENSIONS = frozenset({".py", ".sh", ".bash", ".js", ".mjs", ".cjs", ".ts", ".rb"})
SKIP_DIRS = frozenset({"tests", "test", "fixtures", "examples", "__pycache__", "node_modules", ".venv",
                       ".pytest_cache", ".mypy_cache", ".ruff_cache", ".git"})
TEST_DIRS = frozenset({"tests", "test"})
# A file under a tests folder is a test only when it is code, outside fixtures and goldens:
# a README, a fixture tree or a golden JSON there is test data, not a test.
TEST_CODE_EXTENSIONS = SCRIPT_EXTENSIONS | {".go", ".bats", ".tsx", ".jsx"}
TEST_DATA_DIRS = frozenset({"fixtures", "golden", "goldens", "data", "snapshots", "__snapshots__"})
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
    return rel.suffix in TEST_CODE_EXTENSIONS and any(part in TEST_DIRS for part in folders)


def _files(folder: Path, dirs: set[str], pats: list[str], repo: Path) -> Iterator[Path]:
    for path in sorted(folder.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        rel = path.relative_to(repo)
        if any(part in {"__pycache__", "node_modules", ".venv", ".git"} for part in rel.parts):
            continue
        if not is_user_excluded(rel, dirs, pats):
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


def _elsewhere(repo: Path, folder: Path, scripts: list[str], outside: list[Path]) -> list[str]:
    """Outside test files naming one of ``scripts`` with the skill folder's name as a path segment."""
    name = re.escape(folder.name)
    segment = re.compile(rf"(?:[\"'/\\]){name}(?:[\"'/\\])")
    names = [re.compile(rf"(?<![\w.-]){re.escape(s)}(?![\w-])") for s in sorted(set(scripts))]
    found: list[str] = []
    for path in outside:
        if path.is_relative_to(folder):
            continue
        try:
            if path.stat().st_size > _READ_MAX_BYTES:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if segment.search(text) and any(n.search(text) for n in names):
            found.append(path.relative_to(repo).as_posix())
            if len(found) == ELSEWHERE_MAX:
                break
    return found


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
    outside: list[Path] | None = None
    skills: list[SkillTests] = []
    for folder in folders:
        scripts, tests = _count(folder, repo, dirs, pats)
        untested = None if not scripts else tests == 0
        elsewhere: list[str] = []
        if untested:
            if outside is None:  # read once, only when some skill needs it
                outside = [p for p in _files(repo, dirs, pats, repo)
                           if is_test_file(PurePosixPath(p.relative_to(repo).as_posix()))]
            elsewhere = _elsewhere(repo, folder, scripts, outside)
        skills.append(SkillTests(path=folder.relative_to(repo).as_posix(), scripts=len(scripts), tests=tests,
                                 untested=untested, tested_elsewhere=elsewhere))
    return SkillScriptTestsBlock(
        available=True, skills=skills,
        skills_with_scripts=sum(s["untested"] is not None for s in skills),
        skills_untested=sum(s["untested"] is True for s in skills))
