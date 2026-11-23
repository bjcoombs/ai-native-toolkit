"""The one path classifier: production source, test, fixture, example, vendored or generated (#820).

``path_class`` reads a repository-relative path and nothing else (no file
content), so every scan that needs to set non-production files aside asks the
same question and gets the same answer. #820 uses it to split the file-size
percentile; #823 (runbook pattern) and #824 (promissory-marker fixtures) reuse
it rather than keeping their own lists.

The first rule that matches wins, in this order:

1. ``generated``: the basename matches a generated-file name pattern
   (``lib.complexity.generated_files.GENERATED_NAME_PATTERNS``: ``*.generated.*``,
   ``*.gen.ts``, ``database.types.ts``). Content checks (a generator header,
   payload-length lines) stay with the callers that read the file.
2. ``vendored``: a directory segment is one of ``VENDORED_DIRS``
   (``vendor``, ``third_party``, ``node_modules`` ...).
3. ``fixture``: a directory segment is one of ``FIXTURE_DIRS`` (``fixtures``,
   ``__fixtures__``, ``testdata``, ``mdtest``, ``snapshots``, ``__snapshots__``
   ...) at any depth, or the path holds the consecutive segments of a
   ``FIXTURE_SEQUENCES`` entry (``resources/test``, the JVM and Rust test-data
   root).
4. ``test``: a directory segment is one of ``TEST_DIRS`` (``test``, ``tests``,
   ``__tests__``, ``spec``, ``e2e`` ...), or the file is itself a test by name
   (``lib.sibling_tests.is_test_path``: ``test_x.py``, ``x_test.go``,
   ``x.spec.ts``, ``FooTest.java``, ``.bats``).
5. ``example``: a directory segment is one of ``EXAMPLE_DIRS`` (``examples``,
   ``samples``, ``demo`` ...).
6. ``source``: everything else, the production code.

Directory segments compare case-insensitively; the file name itself is never
read as a directory. A fixture under a test tree (``tests/fixtures/a.py``)
is a fixture, since rule 3 runs before rule 4. Tests:
``tests/test_class_sizes.py``.
"""
from __future__ import annotations

from pathlib import PurePath, PurePosixPath
from typing import Literal

from lib.complexity.generated_files import matches_generated_name
from lib.sibling_tests import is_test_path

PathClass = Literal["source", "test", "fixture", "example", "vendored", "generated"]

# Every class, production source first: the key order of a per-class block.
PATH_CLASSES: tuple[PathClass, ...] = (
    "source", "test", "fixture", "example", "vendored", "generated",
)

VENDORED_DIRS = frozenset({
    "vendor", "vendors", "vendored", "third_party", "third-party", "thirdparty",
    "node_modules", "bower_components",
})
FIXTURE_DIRS = frozenset({
    "fixture", "fixtures", "__fixtures__", "testdata", "test_data", "test-data",
    "mdtest", "snapshots", "__snapshots__",
})
FIXTURE_SEQUENCES: tuple[tuple[str, ...], ...] = (("resources", "test"),)
TEST_DIRS = frozenset({"test", "tests", "__tests__", "spec", "specs", "e2e"})
EXAMPLE_DIRS = frozenset({"example", "examples", "sample", "samples", "demo", "demos"})


def _has_sequence(dirs: tuple[str, ...], seq: tuple[str, ...]) -> bool:
    n = len(seq)
    return any(dirs[i:i + n] == seq for i in range(len(dirs) - n + 1))


def path_class(rel: str | PurePath) -> PathClass:
    """The class of a repository-relative path, by the rules in the module docstring."""
    path = PurePosixPath(PurePath(rel).as_posix())
    dirs = tuple(part.lower() for part in path.parts[:-1])
    if matches_generated_name(path.name):
        return "generated"
    if any(part in VENDORED_DIRS for part in dirs):
        return "vendored"
    if (any(part in FIXTURE_DIRS for part in dirs)
            or any(_has_sequence(dirs, seq) for seq in FIXTURE_SEQUENCES)):
        return "fixture"
    if any(part in TEST_DIRS for part in dirs) or is_test_path(path.as_posix()):
        return "test"
    if any(part in EXAMPLE_DIRS for part in dirs):
        return "example"
    return "source"


def is_production(rel: str | PurePath) -> bool:
    """True when ``rel`` is production source (``path_class`` reads ``source``)."""
    return path_class(rel) == "source"
