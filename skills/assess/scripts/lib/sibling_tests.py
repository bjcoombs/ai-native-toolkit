"""The one home for test-file naming conventions and the sibling-test probe.

Three consumers ask "does this file have a test file?" and must answer the same
way in one run: the hotspot wiki page (`assess_core._has_sibling_test`, the
``Has test file`` row), the E2 test-to-code map (`keyhole_signals`), and the
test-focus signal (`test_focus`). Each used to carry its own copy of the idiom
list, and the copies drifted: a Java ``src/FooTest.java`` credited ``Foo.java``
on the hotspot page while the focus table called the same file ``unsupported``.
The conventions live here once so a new idiom lands in every consumer at once.

Layers, narrowest first:

- :func:`sibling_test_names` / :func:`is_test_path` - pure name rules.
- :func:`find_colocated_test` - the test beside the source or in an adjacent
  ``__tests__/`` / ``tests/`` / ``test/`` / ``spec/`` directory. E2 uses this
  layer alone: its evidence is "co-located AND co-committed", so a far-away
  mirror tree is out of its scope by definition.
- :func:`sibling_test_match` - co-location, then a ``tests/`` / ``test/`` /
  ``spec/`` tree at any ancestor mirroring the source path (``MATCH_DIRECT``),
  then a conventionally named test anywhere in the repository that shares a
  directory with the source (``MATCH_BASENAME``: parallel trees such as
  ``app/unit-tests/`` or Dart's ``test/unit/`` that do not mirror the path),
  then a bounded flat tree holding the bare name (``MATCH_FLAT``, weaker).
- :func:`has_sibling_test` - the yes/no/unknown verdict the hotspot page and the
  focus signal both read, with a flat-only match dropped for a bare name that
  more than one hot file shares (:func:`shared_name_keys`).

The basename tier reads a :class:`TestIndex` of the repository's files, built
once per run by :func:`build_test_index` (``git ls-files`` when the root is a
git repository, a pruned walk otherwise; built-in excluded trees such as
``node_modules`` and ``tests/fixtures`` skipped). A test found this way belongs
to the same-named source whose directory shares the deepest common ancestor
with it; a tie across sources (two ``index.js`` equally close) credits none, and
a common ancestor of only the repository root credits nothing. A non-git walk
past :data:`MAX_INDEX_FILES`, or one that cannot read a directory, yields an
empty index, so the tier credits nothing.

Two stated limits of the basename tier:

- Existence differs by tier. The co-located, mirrored and flat tiers probe a
  named path with ``is_file()``, so an untracked test beside the source counts.
  The basename tier needs a repository-wide listing, and in a git repository
  that listing is ``git ls-files``: it leaves out build output and untracked
  scratch, which a disk walk would index as evidence. An untracked parallel
  test is therefore invisible to it.
- A helper module whose name matches a test convention (``test_utils.py`` in
  ``pkg/b/helpers/``) is indexed as a test with no same-named source to compete
  for it, so it credits a lone ``utils.py`` elsewhere under the same directory.
  In a single-package layout (everything under one ``src/``) that directory is
  the whole package, and the rivals count carries the precision alone. The
  credit is ``sibling_test_only``, which sends the file to mutation testing,
  where a helper that tests nothing shows up as all-surviving mutants.

Inward-only: stdlib plus ``lib.git_churn`` / ``lib.doc_graph``, imports no
orchestrator. Beyond the index, file I/O is existence checks (``is_file`` /
``is_dir``) bounded by :data:`MAX_ANCESTOR_LEVELS`. Never raises.
"""
from __future__ import annotations

import os
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

from lib.doc_graph import EXCLUDE_DIRS, is_excluded_path
from lib.git_churn import tracked_files

# Test-file name builders keyed off a source file's stem + suffix (``.ext``
# including the dot, or empty). A cheap precision heuristic, not a build graph.
TEST_SIBLING_BUILDERS: tuple[Callable[[str, str], str], ...] = (
    lambda stem, ext: f"{stem}_test{ext}",    # Go, Python (pytest co-located)
    lambda stem, ext: f"{stem}.test{ext}",    # JS/TS (jest)
    lambda stem, ext: f"{stem}.spec{ext}",    # JS/TS/Angular (jasmine/jest)
    lambda stem, ext: f"{stem}_spec{ext}",    # Ruby (rspec), some JS
    lambda stem, ext: f"test_{stem}{ext}",    # Python (unittest / pytest)
    lambda stem, ext: f"{stem}Test{ext}",     # Java/Kotlin/C# (JUnit)
    lambda stem, ext: f"{stem}Tests{ext}",    # C#/Swift (XCTest, MSTest)
)
# Directories beside a source file that hold its tests. Probed for every
# builder name and for the bare source name.
ADJACENT_TEST_DIRS: tuple[str, ...] = ("__tests__", "tests", "test", "spec")
# Directories that hold a parallel test tree at some ancestor. ``__tests__`` is a
# JS co-location idiom, never a repo-level tree, so it is not one of them.
TREE_TEST_DIRS: tuple[str, ...] = ("tests", "test", "spec")
# Stem markers meaning the file IS a test.
IS_TEST_RE = re.compile(r"(^test_|_test$|\.test$|\.spec$|_spec$|Tests?$)")
# Bound on how many ancestor directories the tree walk visits.
MAX_ANCESTOR_LEVELS = 16
# A flat tree (test named by bare file name, no path mirrored) is only trusted
# within this many components of the source directory: beside it (1) or beside
# its top-level package directory (2: ``skills/assess/tests`` for
# ``skills/assess/scripts/lib/``). Further up a bare-name match carries no path
# relationship: a root ``tests/test_mod.py`` would credit every ``mod.py``.
MAX_FLAT_BELOW = 2

# Bound on the files a non-git walk indexes. A walk that reaches it yields an
# empty index: the files it dropped may include a rival same-named source, so a
# partial index could credit a test the complete one calls a tie. The basename
# tier then credits nothing (``git ls-files`` is uncapped).
MAX_INDEX_FILES = 200_000

MATCH_DIRECT = "direct"  # co-located, mirrored, or the file is itself a test
MATCH_BASENAME = "basename"  # a same-named test elsewhere, closest source wins
MATCH_FLAT = "flat"  # only a bounded flat tree held the bare name


@dataclass(frozen=True)
class TestIndex:
    """The repository's files split for the basename tier: test files keyed by
    file name, other files keyed by :func:`name_key`, each value the directory
    parts of every repo-relative path carrying that name."""

    __test__ = False  # not a pytest class, despite the name

    tests_by_name: dict[str, list[tuple[str, ...]]] = field(default_factory=dict)
    sources_by_key: dict[str, list[tuple[str, ...]]] = field(default_factory=dict)


def sibling_test_names(name: str) -> list[str]:
    """Conventional test-file names for a source file name, one per builder. A
    hyphenated stem also yields its underscore spelling, since a Python test for
    ``complexity-treemap.py`` has to be importable as
    ``test_complexity_treemap.py``."""
    p = Path(name)
    stem, ext = p.stem, p.suffix
    if not stem:
        return []
    stems = [stem] + ([stem.replace("-", "_")] if "-" in stem else [])
    return [build(s, ext) for s in stems for build in TEST_SIBLING_BUILDERS]


def is_test_path(rel_path: str) -> bool:
    """True when the file is itself a test: its stem follows a test naming
    convention, or it lives under a ``__tests__/`` directory."""
    p = Path(rel_path)
    return bool(IS_TEST_RE.search(p.stem)) or "__tests__" in p.parts[:-1]


def name_key(rel_path: str) -> str:
    """Bare file name with hyphens folded to underscores: two files with the
    same key resolve to the same conventional test names."""
    return Path(rel_path).name.replace("-", "_")


def shared_name_keys(paths: Iterable[str]) -> frozenset[str]:
    """Name keys carried by more than one path: a flat-tree match on such a
    name cannot say which of them the test belongs to."""
    counts: dict[str, int] = {}
    for path in paths:
        key = name_key(path)
        counts[key] = counts.get(key, 0) + 1
    return frozenset(k for k, n in counts.items() if n > 1)


def _repo_files(repo_root: Path) -> list[Path] | None:
    """Repo-relative file paths: the git-tracked files under ``repo_root`` when
    it is in a git repository and still on disk, else a walk pruned of
    :data:`EXCLUDE_DIRS`; ``None`` when that walk finds more than
    :data:`MAX_INDEX_FILES` files. A directory the walk cannot read raises."""
    root = repo_root.resolve()
    tracked = tracked_files(root)
    if tracked is not None:
        out: list[Path] = []
        for path in tracked:
            try:
                rel = path.relative_to(root)
            except ValueError:
                continue  # tracked, but outside the assessed root
            if (root / rel).is_file():  # a deletion not yet staged is gone
                out.append(rel)
        return out
    walked: list[Path] = []
    def fail_closed(error: OSError) -> None:
        raise error  # an unread subtree may hold a rival source

    for dirpath, dirnames, filenames in os.walk(root, onerror=fail_closed):
        dirnames[:] = sorted(d for d in dirnames if d not in EXCLUDE_DIRS)
        rel_dir = Path(dirpath).relative_to(root)
        for name in sorted(filenames):
            if len(walked) >= MAX_INDEX_FILES:
                return None  # truncated: fail closed
            walked.append(rel_dir / name)
    return walked


def build_test_index(repo_root: Path) -> TestIndex:
    """Index the repository once for the basename tier. An empty index when the
    root or any directory under it cannot be read, or the walk passed
    :data:`MAX_INDEX_FILES`. Never raises."""
    index = TestIndex()
    try:
        files = _repo_files(Path(repo_root))
    except (OSError, ValueError):
        return index
    for rel in files or ():
        if is_excluded_path(rel):
            continue
        dirs = rel.parts[:-1]
        if is_test_path(rel.as_posix()):
            index.tests_by_name.setdefault(rel.name, []).append(dirs)
        else:
            index.sources_by_key.setdefault(name_key(rel.as_posix()), []).append(dirs)
    return index


def _common_depth(a: tuple[str, ...], b: tuple[str, ...]) -> int:
    depth = 0
    for x, y in zip(a, b):
        if x != y:
            break
        depth += 1
    return depth


def _basename_match(index: TestIndex, rel_path: str) -> bool:
    """True when some conventionally named test in the index belongs to this
    source: they share at least one directory, and no other same-named source
    shares as deep a common ancestor with that test."""
    src_dirs = Path(rel_path).parts[:-1]
    peers = list(index.sources_by_key.get(name_key(rel_path), []))
    if src_dirs not in peers:
        peers.append(src_dirs)  # an untracked source still competes for its test
    for name in sibling_test_names(Path(rel_path).name):
        for test_dirs in index.tests_by_name.get(name, []):
            depth = _common_depth(src_dirs, test_dirs)
            if depth == 0:
                continue  # only the root in common: no path relationship
            rivals = sum(1 for p in peers if _common_depth(p, test_dirs) >= depth)
            if rivals == 1:  # this source alone is the closest
                return True
    return False


def find_colocated_test(repo_root: Path, rel_path: str) -> Path | None:
    """The co-located test file for a source path, or ``None``: a builder name
    beside the source, or a builder name or the bare source name in an adjacent
    test directory. Does not check whether ``rel_path`` is itself a test."""
    try:
        src = repo_root / rel_path
        directory = src.parent
        names = sibling_test_names(src.name)
        for name in names:
            if (directory / name).is_file():
                return directory / name
        for sub in ADJACENT_TEST_DIRS:
            test_dir = directory / sub
            if not test_dir.is_dir():
                continue
            for name in [*names, src.name]:
                if (test_dir / name).is_file():
                    return test_dir / name
    except (OSError, ValueError):
        return None
    return None


def _tree_dirs_for(rel_dir: Path) -> list[tuple[Path, bool]]:
    """Repo-relative tree directories that may hold a test for a source in
    ``rel_dir``, most local first, each paired with whether it is a flat probe.
    At each ancestor up to the root: a test tree mirroring the source's path
    below that ancestor, the same with the first component (a ``src/``-style
    root) dropped, and - within ``MAX_FLAT_BELOW`` components - the flat tree.
    The adjacent directories are left to :func:`find_colocated_test`."""
    dirs: dict[Path, bool] = {rel_dir / d: False for d in ADJACENT_TEST_DIRS}
    parts = rel_dir.parts
    for depth in range(len(parts), -1, -1)[:MAX_ANCESTOR_LEVELS]:
        ancestor = Path(*parts[:depth])
        below = parts[depth:]
        for tree in TREE_TEST_DIRS:
            base = ancestor / tree
            if below:
                dirs.setdefault(base.joinpath(*below), False)
                if len(below) > 1:
                    dirs.setdefault(base.joinpath(*below[1:]), False)
            if len(below) <= MAX_FLAT_BELOW:
                dirs.setdefault(base, True)
    adjacent = {rel_dir / d for d in ADJACENT_TEST_DIRS}
    return [(d, flat) for d, flat in dirs.items() if d not in adjacent]


def sibling_test_match(
    repo_root: Path, rel_path: str, index: TestIndex | None = None,
) -> str | None:
    """How a conventionally named test for ``rel_path`` was found, or ``None``.

    ``MATCH_DIRECT``: the file is itself a test, a test is co-located, or a test
    tree at an ancestor mirrors the source path. ``MATCH_BASENAME``: a test in
    the repository index (``index``, built here when not passed) shares a
    directory with the source and no same-named source sits closer to it.
    ``MATCH_FLAT``: only a bounded flat tree holds a builder name - weaker
    evidence the caller disambiguates with :func:`shared_name_keys`. A source
    not on disk (a stale stats entry for a deleted file) is never credited.
    Never raises."""
    try:
        source = repo_root / rel_path
        if not source.is_file():
            return None
        if is_test_path(rel_path):
            return MATCH_DIRECT
        if find_colocated_test(repo_root, rel_path) is not None:
            return MATCH_DIRECT
        rel_dir = Path(rel_path).parent
        if ".." in rel_dir.parts or rel_dir.is_absolute():
            return None
        names = sibling_test_names(source.name)
        flat_hit = False
        for rel, is_flat in _tree_dirs_for(rel_dir):
            if is_flat and flat_hit:
                continue  # already have the weak match; only a direct one helps
            directory = repo_root / rel
            if not directory.is_dir():
                continue
            if any((directory / n).is_file() for n in names):
                if not is_flat:
                    return MATCH_DIRECT
                flat_hit = True
        if index is None:
            index = build_test_index(repo_root)
        if _basename_match(index, Path(rel_path).as_posix()):
            return MATCH_BASENAME
        return MATCH_FLAT if flat_hit else None
    except (OSError, ValueError):
        return None


def has_sibling_test(
    repo_root: Path, rel_path: str, shared_names: frozenset[str] = frozenset(),
    index: TestIndex | None = None,
) -> bool | None:
    """Does this file have a test file? ``None`` when the file is not on disk
    (honestly unknown), ``True`` for a direct or basename match or a flat match
    on a name no other considered file shares, otherwise ``False``. Callers
    probing several files pass one ``index`` from :func:`build_test_index`."""
    try:
        if not (repo_root / rel_path).is_file():
            return None
    except (OSError, ValueError):
        return None
    match = sibling_test_match(repo_root, rel_path, index)
    if match in (MATCH_DIRECT, MATCH_BASENAME):
        return True
    return match == MATCH_FLAT and name_key(rel_path) not in shared_names
