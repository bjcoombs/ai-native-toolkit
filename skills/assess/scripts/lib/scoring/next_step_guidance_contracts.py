"""Repository-native wording for the contracts-tagged flags (#852, #853), read by ``next_step_guidance``.

``CONTRACT_FLAGS`` holds one ``(change, done_when)`` pair per ``eq true`` signal that keeps a
contract true: docs and generated files kept true by CI (``doc_staleness.kept_true.present``) and
API compatibility checks CI runs (``api_compat.present``, ``lib.ci.api_compat``).
``lib.scoring.next_step_guidance`` wraps each in its flag wording. ``API_COMPAT_HINT`` is the
``api_compat`` block's measure hint. It sits apart from ``next_step_guidance.py`` to keep that file
under its size ceiling, and imports nothing. Tests: ``tests/test_fleet_next_steps.py`` (every
default signal has wording).
"""
from __future__ import annotations

CONTRACT_FLAGS: dict[str, tuple[str, str]] = {
    "doc_staleness.kept_true.present": (
        "Add a CI step that regenerates the generated files and docs and then runs "
        "`git diff --exit-code`, or run the doc examples as tests (`pytest --doctest-modules`).",
        "A CI job regenerates files and stops on a diff, runs a verify script paired with an "
        "update script, or runs doc examples as tests."),
    "api_compat.present": (
        "Run an API compatibility check in CI against the last release (`apidiff`, `japicmp`, "
        "`api-extractor run`, `griffe check`, `cargo semver-checks`, `buf breaking`), or commit a "
        "test that pins the public exports (`assert set(__all__) == set(EXPECTED_ALL)`) and run "
        "the tests in CI.",
        "A CI job runs a compatibility tool that turns the job red on a breaking change, or a committed test "
        "compares the public exports with a fixed list and CI runs the tests."),
}

API_COMPAT_HINT = ("Not measured (`tests_not_read`) when a test pins the public exports but whether "
                   "CI runs tests is not settled, and (`ci_not_read`) when a compatibility tool is "
                   "named in a build file and a CI file this reader cannot follow may run it.")
