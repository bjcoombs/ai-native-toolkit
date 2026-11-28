"""Repository-native wording for the repo-guards criteria (#854), read by ``next_step_guidance``.

The repo-guards and repo-guard-tested rows (``levels/default.toml``) read two boolean signals of
the ``repo_guards`` block (``lib/ci/repo_guards.py``). Each is an ``eq true`` flag, so
``GUARD_FLAGS`` holds one ``(change, done_when)`` pair per signal and
``lib.scoring.next_step_guidance`` wraps each in its flag wording. ``GUARD_HINT`` is the
``repo_guards`` block's measure hint: why either can read not measured. It sits apart from
``next_step_guidance.py`` to keep that file under its size ceiling, and imports nothing.
Tests: ``tests/test_fleet_next_steps.py`` (every default signal has wording).
"""
from __future__ import annotations

GUARD_FLAGS: dict[str, tuple[str, str]] = {
    "repo_guards.present": (
        "Run a boundary checker in CI (`lint-imports`, `import-boss`, or a script that exits non-zero on a "
        "forbidden import), or commit a lint baseline with a make target that updates it and a CI "
        "step that reads it, or cap file size in a CI check.",
        "A CI job on pushes or pull requests runs a lint baseline it can update, a boundary checker "
        "or a file-size cap."),
    "repo_guards.guard_tested": (
        "Add a test file named for the guard script (`test_check_layers.py` beside "
        "`check_layers.py`) with one case it must reject and one it must pass.",
        "A guard script CI runs has a test file of its own that names it."),
}

GUARD_HINT = ("Not measured (`ci_not_read`) when no guard is found and a CI file, include or step this "
              "reader cannot settle may run one, and (`trigger_unknown`) when a guard runs only in a "
              "workflow whose trigger is not settled. Guard tests are not measured (`no_guard_script`) "
              "when every guard is a third-party tool.")
