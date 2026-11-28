"""Tests that pin behaviour beyond coverage: fuzzing, property, network-isolated and snapshot tests (issue #855).

Theme 10 of #826, and evidence only (maintainer decision): no condition reads this block, so it
moves no tally, no Step and never the ``coverage_gate`` gate. ``lib.scoring.condition_evidence``
attaches its ``practices`` to the tests-run-in-CI condition, and only while that condition is met.

``scan_behaviour_pins(repo_root)`` reads the tracked files and returns the ``behaviour_pins``
block. Each arm is a parsed declaration or signature (``lib.testing.behaviour_pins_manifests``),
never a substring, so a file holding the word ``install`` is not the ``insta`` crate:

- ``scheduled_fuzzing``, ranked first, the strongest: a GitHub Actions workflow whose top-level
  ``on`` names ``schedule`` (``lib.ci.ci_triggers.workflow_events``) and that runs a fuzzer: an
  unmasked ``uses:`` action holds a fuzz word (``fuzz``, ``fuzzer``, ``fuzzing``, ``cifuzz``:
  ``google/oss-fuzz/infra/cifuzz/...``), or a command it runs is a fuzz run (``cargo fuzz run``,
  ``go test -fuzz=FuzzParse``). The file name or ``name:`` alone never counts. Commands are the job's unmasked
  ``run:`` lines (``lib.ci.ci_job_commands.job_lines``, the #775 masking rule) after
  ``lib.ci.ci_step_follower.follow_workflow``, and the scripts, make recipes and local actions
  they call through ``StepFollower`` up to ``_MAX_FOLLOW`` levels. A command behind a passing
  ``||`` fallback, an install (``cargo install cargo-fuzz``), a command that only names files
  or text (``echo``, ``cd fuzz``, ``mkdir fuzz``), and a fuzz word past the program's first
  operand (``--exclude fuzz``, ``--ignore=tests/fuzz``, ``-k "not fuzz"``) do not count. A
  command is a fuzz run when the program or its first operand or subcommand is a fuzz word
  (``cargo fuzz run``, ``cargo +nightly fuzz``, ``bash fuzz.sh``), a ``-fuzz`` flag is given
  (``go test -fuzz=FuzzParse``) or a ``-k`` / ``-m`` expression selects fuzz tests
  (``pytest -m fuzz``). A followed script under an excluded folder is not read. The arm is ``scheduled-workflow``.
- ``fuzz_targets``: the ``libfuzzer-sys`` or ``arbitrary`` crate, or a Go ``func FuzzXxx(f
  *testing.F)`` in a ``_test.go`` file (``go-fuzz-test``).
- ``property_tests``: ``hypothesis``, the ``proptest`` or ``quickcheck`` crate, jqwik
  (``net.jqwik``) or ``fast-check``.
- ``network_isolated_tests``: ``pytest-socket``, ``vcrpy`` or ``pytest-recording``.
- ``snapshot_tests``: ``syrupy``, the ``insta`` crate or approvaltests (``com.approvaltests``).

A file under a ``vendor``, ``third_party``, ``third-party``, ``testdata``, ``fixtures`` or
``node_modules`` folder is never read. ``pins`` holds one entry per practice and arm with the
first file in path order that declares it; ``practices`` lists each practice found once, in
``PRACTICES`` order. Every read goes through ``lib.ci.ci_step_paths.local_reader`` (tracked,
inside the checkout, size-capped); nothing is executed. ``pins_or_unknown`` is the collector: a
reader fault reads ``available: false`` with ``scan_failed``, and attaches nothing.

Known not detected: a repository's own harness, such as component screenshot tests run by a CI
label or a custom test infrastructure module, and a GitLab job run only by a pipeline schedule
(the schedule is set in GitLab, not in the file). Tests: ``tests/test_behaviour_pins_evidence.py``.
"""
from __future__ import annotations

import fnmatch
import re
from collections.abc import Callable
from pathlib import Path

from lib.ci.ci_action_inputs import ReadLocal
from lib.ci.ci_job_commands import commands, installs, job_lines, program
from lib.ci.ci_masking import mask_lines
from lib.ci.ci_step_follower import StepFollower, follow_workflow
from lib.ci.ci_step_paths import local_reader
from lib.ci.ci_triggers import workflow_events
from lib.instructions.command_index import CI_CONFIG_FILES, ci_config_files, ci_line_text, repo_files
from lib.run_context_types_behaviour_pins import BehaviourPin, BehaviourPinsBlock
from lib.testing.behaviour_pins_manifests import (CARGO_ARMS, FUZZ_TARGETS, GO_ARM, NETWORK_ISOLATED, NPM_ARMS,
                                                  PROPERTY_TESTS, PYTHON_ARMS, SCHEDULED_FUZZING, SNAPSHOT_TESTS,
                                                  arms, cargo_names, go_fuzz_tests, gradle_groups, jvm_arms,
                                                  maven_groups, npm_names, python_names)

__all__ = ["PRACTICES", "PRACTICE_LABELS", "REASON_SCAN_FAILED", "pins_or_unknown", "scan_behaviour_pins"]

# Strongest first: a schedule shows the practice runs, a declaration only that it is set up.
PRACTICES = (SCHEDULED_FUZZING, FUZZ_TARGETS, PROPERTY_TESTS, NETWORK_ISOLATED, SNAPSHOT_TESTS)
PRACTICE_LABELS = {SCHEDULED_FUZZING: "scheduled fuzzing", FUZZ_TARGETS: "fuzz targets",
                   PROPERTY_TESTS: "property-based tests", NETWORK_ISOLATED: "network-isolated tests",
                   SNAPSHOT_TESTS: "snapshot tests"}
REASON_SCAN_FAILED = "scan_failed"
SCHEDULE_ARM = "scheduled-workflow"

_EXCLUDED_DIRS = frozenset({"vendor", "third_party", "third-party", "testdata", "fixtures", "node_modules"})
_FUZZ_WORDS = frozenset({"fuzz", "fuzzer", "fuzzers", "fuzzing", "cifuzz", "fuzztest"})
_WORDS = re.compile(r"[^a-z0-9]+")
_USES = re.compile(r"^\s*(?:-\s+)?uses:\s*(\S+)")
# Commands whose operands only name files or text: `mkdir fuzz` and `cd fuzz` run no fuzzer.
_QUIET = frozenset({"echo", "printf", ":", "true", "cd", "pushd", "popd", "ls", "mkdir", "rm", "cp", "mv",
                    "touch", "cat", "test", "[", "export", "chmod", "tar", "find", "grep"})
_MAX_FOLLOW = 3
_SELECTORS = frozenset({"-k", "-m"})

Reader = Callable[[str, str], set[tuple[str, str]]]


def _fuzz_word(text: str) -> bool:
    return bool(_FUZZ_WORDS & set(_WORDS.split(text.lower())))


def _python(rel: str, text: str) -> set[tuple[str, str]]:
    return arms(python_names(rel, text), PYTHON_ARMS)


def _cargo(rel: str, text: str) -> set[tuple[str, str]]:
    return arms(cargo_names(text), CARGO_ARMS)


def _npm(rel: str, text: str) -> set[tuple[str, str]]:
    return arms(npm_names(text), NPM_ARMS)


def _maven(rel: str, text: str) -> set[tuple[str, str]]:
    return jvm_arms(maven_groups(text))


def _gradle(rel: str, text: str) -> set[tuple[str, str]]:
    return jvm_arms(gradle_groups(rel, text))


def _go(rel: str, text: str) -> set[tuple[str, str]]:
    return {(FUZZ_TARGETS, GO_ARM)} if go_fuzz_tests(text) else set()


def _reader(rel: str) -> Reader | None:
    """The declaration reader for one tracked file, or None when it declares nothing read here."""
    parts = rel.split("/")
    base, parent = parts[-1], parts[-2] if len(parts) > 1 else ""
    if base in ("pyproject.toml", "setup.cfg") or fnmatch.fnmatchcase(base, "requirements*.txt") or (
            parent == "requirements" and base.endswith(".txt")):
        return _python
    readers: dict[str, Reader] = {"Cargo.toml": _cargo, "package.json": _npm, "pom.xml": _maven}
    if base in readers:
        return readers[base]
    if base.endswith((".gradle", ".gradle.kts", ".versions.toml")):
        return _gradle
    return _go if base.endswith("_test.go") else None


def _selects_fuzz(expr: str) -> bool:
    """A ``-k`` / ``-m`` expression that selects fuzz tests: a fuzz word and no ``not``."""
    words = set(_WORDS.split(expr.lower()))
    return bool(_FUZZ_WORDS & words) and "not" not in words


def _fuzz_command(words: list[str]) -> bool:
    """The program, or its first operand or subcommand, is a fuzz word (``cargo fuzz run``,
    ``cargo +nightly fuzz``, ``bash fuzz.sh``), a ``-fuzz`` flag is given (``go test
    -fuzz=FuzzParse``), or a ``-k`` / ``-m`` expression selects fuzz tests."""
    prog = program(words)
    if not prog or installs(words) or prog[0] in _QUIET:
        return False
    lead = [w for w in prog[:3] if not w.startswith("+")][:2]
    if any(_fuzz_word(w) for w in lead if not w.startswith("-")):
        return True
    for i, word in enumerate(prog):
        flag, _, value = word.partition("=")
        if flag in ("-fuzz", "--fuzz"):
            return True
        if flag in _SELECTORS and _selects_fuzz(value or (prog[i + 1] if i + 1 < len(prog) else "")):
            return True
    return False


def _runs_fuzz(line: str, follower: StepFollower, depth: int = 0) -> bool:
    """One CI line runs a fuzz command, directly or through what it calls (module docstring)."""
    if any(_fuzz_command(c["words"]) for c in commands(ci_line_text(line)) if not c["masked"]):
        return True
    if depth >= _MAX_FOLLOW:
        return False
    return any(_runs_fuzz(sub, follower, depth + 1) for found in follower.step(line) for sub in found.commands)


def _scheduled_fuzz(rel: str, text: str, read_local: ReadLocal, follower: StepFollower) -> bool:
    """A workflow on ``schedule`` that is a fuzz workflow (module docstring)."""
    if "schedule" not in (workflow_events(text) or []):
        return False
    settled = follow_workflow(text, read_local).text
    unmasked, _unsure = mask_lines(settled)
    if any((m := _USES.match(line)) is not None and _fuzz_word(m.group(1)) for line in unmasked):
        return True
    return any(_runs_fuzz(line, follower) for lines in job_lines(settled, False) for line in lines)


def _excluded(rel: str) -> bool:
    return any(part in _EXCLUDED_DIRS for part in rel.split("/")[:-1])


def scan_behaviour_pins(repo_root: Path) -> BehaviourPinsBlock:
    """Build the ``behaviour_pins`` block (module docstring)."""
    repo = repo_root.resolve()
    tracked = repo_files(repo)
    tracked_read = local_reader(repo, tracked)

    def read(rel: str) -> str | None:
        return None if _excluded(rel) else tracked_read(rel)

    follower = StepFollower(read, tracked)
    found: dict[tuple[str, str], str] = {}
    files_read = 0
    for rel in ci_config_files(tracked):
        if rel in CI_CONFIG_FILES or (SCHEDULED_FUZZING, SCHEDULE_ARM) in found:
            continue
        text = read(rel)
        files_read += 1
        if text and _scheduled_fuzz(rel, text, read, follower):
            found[(SCHEDULED_FUZZING, SCHEDULE_ARM)] = rel
    for rel in sorted(tracked):
        reader = _reader(rel)
        if reader is None or _excluded(rel) or (reader is _go and (FUZZ_TARGETS, GO_ARM) in found):
            continue
        text = read(rel)
        files_read += 1
        for key in sorted(reader(rel, text or "")):
            found.setdefault(key, rel)
    pins = [BehaviourPin(practice=p, arm=a, path=found[(p, a)])
            for p, a in sorted(found, key=lambda k: (PRACTICES.index(k[0]), k[1]))]
    return BehaviourPinsBlock(available=True, files_read=files_read,
                              practices=list(dict.fromkeys(p["practice"] for p in pins)), pins=pins)


def pins_or_unknown(repo_root: Path) -> BehaviourPinsBlock:
    """``scan_behaviour_pins``, or an unavailable block (``scan_failed``) when the reader raises."""
    try:
        return scan_behaviour_pins(repo_root)
    except Exception:   # noqa: BLE001 - a reader fault reads not measured and attaches nothing
        return BehaviourPinsBlock(available=False, reason=REASON_SCAN_FAILED, files_read=0, practices=[], pins=[])
