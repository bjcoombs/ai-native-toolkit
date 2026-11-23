"""A Gradle run's working directory selects the project it runs in (#1162, #1163).

``./gradlew -p app <task>`` and ``cd app && ../gradlew <task>`` run an unqualified task in
``app`` and its subprojects only (the readings #1162 sets out), so a JaCoCo gate in ``lib`` is not run: it
reads null (``indirect_run``; ``allprojects {}`` or a ``projectDir`` override can still
reach it), never false. A gate in ``app`` or under it keeps its credit. The Gradle root is
the nearest settings file at or above the known working directory, so ``:app:x`` at the
repository root runs the root build's ``app``, not ``android/app`` of a separate build.
"""
from __future__ import annotations

import subprocess
from pathlib import Path, PurePosixPath

import pytest

from lib.runner_args import gradle_roots_set, gradle_runs_in
from lib.testing.ci_workdir import exact_cwd, run_cwd
from lib.testing.coverage_ci import read_ci_runs
from lib.testing.coverage_gate import detect_coverage_gate

V = frozenset({"jacocoTestCoverageVerification"})
TASK = "jacocoTestCoverageVerification"
GATE = ('plugins { id "java"; id "jacoco" }\njacocoTestCoverageVerification {\n'
        '  violationRules { rule { limit { minimum = 0.80 } } }\n}\n')
WIRED = GATE + "check.dependsOn jacocoTestCoverageVerification\n"
ROOTED = {"settings.gradle": "include ':app', ':app:sub', ':lib'\n"}
TWO_ROOTS = {"settings.gradle": "include ':app'\n", "app/build.gradle": "",
             "android/settings.gradle": "include ':app'\n", "android/app/build.gradle": GATE}


def _repo(root: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=root, check=True, encoding="utf-8", errors="replace")
    subprocess.run(["git", "add", "-A"], cwd=root, check=True, encoding="utf-8", errors="replace")
    return root


def _wf(run: str, workdir: str | None = None) -> str:
    step = f"      - run: {run}\n"
    if workdir is not None:
        step = f"      - working-directory: {workdir}\n        run: {run}\n"
    return f"on: pull_request\njobs:\n  t:\n    runs-on: ubuntu-latest\n    steps:\n{step}"


def _gate(root: Path, line: str, files: dict[str, str],
          workdir: str | None = None) -> tuple[bool | None, str]:
    repo = _repo(root, {".github/workflows/ci.yml": _wf(line, workdir), **files})
    block = detect_coverage_gate(repo)
    return block["enforced_meets_minimum"], block["enforced_basis"]


CREDIT = (True, "ci_run")
NULL = (None, "indirect_run")


@pytest.mark.parametrize(("line", "files", "expected"), [
    # The two lines #1162 names: a gate only in lib reads null.
    (f"./gradlew -p app {TASK}", ROOTED | {"lib/build.gradle": GATE}, NULL),
    (f"cd app && ../gradlew {TASK}", ROOTED | {"lib/build.gradle": GATE}, NULL),
    # The matching project, or a subproject of it, keeps its credit.
    (f"./gradlew -p app {TASK}", ROOTED | {"app/build.gradle": GATE}, CREDIT),
    (f"cd app && ../gradlew {TASK}", ROOTED | {"app/build.gradle": GATE}, CREDIT),
    (f"./gradlew -p app {TASK}", ROOTED | {"app/sub/build.gradle": GATE}, CREDIT),
    (f"cd app && ../gradlew {TASK}", ROOTED | {"app/sub/build.gradle": GATE}, CREDIT),
    (f"./gradlew --project-dir=app {TASK}", ROOTED | {"lib/build.gradle": GATE}, NULL),
    (f"./gradlew --project-dir app {TASK}", ROOTED | {"app/build.gradle": GATE}, CREDIT),
    # cd and -p join: cd app && -p sub is app/sub.
    (f"cd app && ../gradlew -p sub {TASK}", ROOTED | {"app/sub/build.gradle": GATE}, CREDIT),
    (f"cd app && ../gradlew -p sub {TASK}", ROOTED | {"app/build.gradle": GATE}, NULL),
    # check wired to the verification reads the same way.
    ("./gradlew -p app check", ROOTED | {"lib/build.gradle": WIRED}, NULL),
    ("./gradlew -p app check", ROOTED | {"app/build.gradle": WIRED}, CREDIT),
    # A -p naming the root, or no -p and no cd, keeps today's reading.
    (f"./gradlew -p . {TASK}", ROOTED | {"lib/build.gradle": GATE}, CREDIT),
    (f"./gradlew {TASK}", ROOTED | {"lib/build.gradle": GATE}, CREDIT),
    # An unreadable -p: the project is unknown, null for any gate.
    (f"./gradlew -p $DIR {TASK}", ROOTED | {"app/build.gradle": GATE}, NULL),
    # An unresolved cd leaves the working directory unknown: today's reading.
    (f"cd $DIR && ./gradlew {TASK}", ROOTED | {"lib/build.gradle": GATE}, CREDIT),
    # Two runs and a cd on one line, or a cd after a quote: unresolved, today's reading.
    (f"cd app && ../gradlew {TASK} && cd ../lib && ../gradlew {TASK}",
     ROOTED | {"lib/build.gradle": GATE}, CREDIT),
    (f"bash -c \"cd app && ../gradlew {TASK}\"", ROOTED | {"lib/build.gradle": GATE}, CREDIT),
    # Two different Gradle program words on one line: each run is cut at its own word.
    (f"cd lib && gradle {TASK} && cd ../app && ../gradlew {TASK}",
     ROOTED | {"lib/build.gradle": GATE}, CREDIT),
    (f"cd lib && gradle check && cd ../app && ../gradlew {TASK}",
     ROOTED | {"lib/build.gradle": GATE}, NULL),
    # A subshell cd that closes before the run leaves it unresolved: today's reading.
    (f"(cd app) && ./gradlew {TASK}", ROOTED | {"lib/build.gradle": GATE}, CREDIT),
])
def test_working_directory_selects_the_project(
        tmp_path: Path, line: str, files: dict[str, str], expected: tuple[bool | None, str]) -> None:
    assert _gate(tmp_path, line, files) == expected


def test_p_under_a_defaults_working_directory_reads_unsure(tmp_path: Path) -> None:
    """``defaults: run: working-directory:`` leaves the directory unresolved, so ``-p app``
    names a project this cannot place: null, never credit (claude-review on #1175)."""
    text = ("on: pull_request\ndefaults:\n  run:\n    working-directory: android\n"
            "jobs:\n  t:\n    runs-on: ubuntu-latest\n    steps:\n"
            f"      - run: ./gradlew -p app {TASK}\n")
    repo = _repo(tmp_path, {".github/workflows/ci.yml": text, **ROOTED, "app/build.gradle": GATE})
    block = detect_coverage_gate(repo)
    assert (block["enforced_meets_minimum"], block["enforced_basis"]) == NULL


@pytest.mark.parametrize(("workdir", "gate", "expected"), [
    ("app", "lib/build.gradle", NULL),
    ("app", "app/build.gradle", CREDIT),
    ("app", "app/sub/build.gradle", CREDIT),
    (".", "lib/build.gradle", CREDIT),
])
def test_step_working_directory(tmp_path: Path, workdir: str, gate: str,
                                expected: tuple[bool | None, str]) -> None:
    line = f"{'../' if workdir == 'app' else './'}gradlew {TASK}"
    assert _gate(tmp_path, line, ROOTED | {gate: GATE}, workdir) == expected


@pytest.mark.parametrize(("line", "files", "expected"), [
    # #1163: at the root, :app is the root build's app, not android/app.
    (f"./gradlew :app:{TASK}", TWO_ROOTS, NULL),
    (f"cd android && ./gradlew :app:{TASK}", TWO_ROOTS, CREDIT),
    (f"./gradlew -p android :app:{TASK}", TWO_ROOTS, CREDIT),
    # An unqualified task at the root keeps today's reading, whichever settings file is
    # nearer the gate (a root build may include a project holding its own settings file).
    (f"./gradlew {TASK}", TWO_ROOTS, CREDIT),
    (f"cd android && ./gradlew {TASK}", TWO_ROOTS, CREDIT),
    (f"./gradlew {TASK}", {"settings.gradle": "include ':android'\n",
                           "android/settings.gradle": "", "android/build.gradle": GATE}, CREDIT),
    # #1126: with no root settings file, android/app is still matched by its path.
    (f"./gradlew :app:{TASK}",
     {"android/settings.gradle": "include ':app'\n", "android/app/build.gradle": GATE}, CREDIT),
])
def test_gradle_root_from_the_working_directory(
        tmp_path: Path, line: str, files: dict[str, str], expected: tuple[bool | None, str]) -> None:
    assert _gate(tmp_path, line, files) == expected


@pytest.mark.parametrize(("args", "cwd", "file", "runs"), [
    (TASK, None, "lib/build.gradle", True),
    (TASK, "", "lib/build.gradle", True),
    (TASK, "app", "lib/build.gradle", None),
    (TASK, "app", "app/build.gradle", True),
    (TASK, "app", "app/x/build.gradle", True),
    (TASK, "app", "application/build.gradle", None),
    # A -p in an unresolved directory cannot be placed: null for any gate.
    (f"-p app {TASK}", None, "lib/build.gradle", None),
    (f"-p app {TASK}", None, "app/build.gradle", None),
    (f"-p app {TASK}", "", "app/build.gradle", True),
    (f"-p .. {TASK}", "app", "lib/build.gradle", True),
    (f"-p ../.. {TASK}", "app", "lib/build.gradle", None),
    (f"-p /abs {TASK}", "", "lib/build.gradle", None),
    (f"-c other.gradle {TASK}", None, "lib/build.gradle", True),
    (f"-p android :app:{TASK}", None, "android/app/build.gradle", True),
    (f":app:{TASK}", "android", "android/app/build.gradle", True),
    (f":app:{TASK}", "", "app/build.gradle", True),
    ("check", "app", "lib/build.gradle", False),
])
def test_gradle_runs_in_with_cwd(args: str, cwd: str | None, file: str,
                                 runs: bool | None) -> None:
    assert gradle_runs_in(args.split(), V, file, cwd) is runs
    assert gradle_runs_in(args.split(), V, None, cwd) is (runs is not False)


def test_root_comes_from_the_start_for_a_project_path() -> None:
    with gradle_roots_set(frozenset({"", "android"})):
        assert gradle_runs_in([f":app:{TASK}"], V, "android/app/build.gradle", "") is None
        assert gradle_runs_in([f":app:{TASK}"], V, "android/app/build.gradle", "android") is True
        assert gradle_runs_in([f":app:{TASK}"], V, "android/app/build.gradle", None) is True
        assert gradle_runs_in([TASK], V, "android/app/build.gradle", "") is True


def test_ci_runs_carry_the_gradle_working_directory(tmp_path: Path) -> None:
    text = ("on: pull_request\njobs:\n  t:\n    runs-on: ubuntu-latest\n    steps:\n"
            "      - run: ./gradlew check\n"
            "      - run: cd app && ../gradlew check && pytest\n"
            "      - working-directory: lib\n        run: ../gradlew check\n")
    runs = read_ci_runs(_repo(tmp_path, {".github/workflows/ci.yml": text}))
    assert [(r.words[0], r.cwd) for r in runs.runs
            if r.words[0] in ("pytest", "./gradlew", "../gradlew")] == [
        ("./gradlew", ""), ("../gradlew", "app"), ("pytest", None), ("../gradlew", "lib")]


def test_run_cwd_reads_a_step() -> None:
    lines = ["      - run: cd pkg/a && ./gradlew check"]
    assert run_cwd(range(0, 1), ["./gradlew", "check"], lines, False) == PurePosixPath("pkg/a")
    assert exact_cwd(range(0, 1), ["./gradlew", "check"], lines, False) == PurePosixPath("pkg/a")
    assert run_cwd(range(0, 1), ["./gradlew"], ["      - run: cd $X && ./gradlew"], False) is None


@pytest.mark.parametrize("line", [
    "      - run: cd a && ./gradlew x && cd ../b && ./gradlew y",
    "      - run: bash -c \"cd a && ./gradlew x\"",
    "      - run: sh -c 'cd a && ./gradlew x'",
])
def test_exact_cwd_refuses_a_misplaceable_run(line: str) -> None:
    assert exact_cwd(range(0, 1), ["./gradlew", "x"], [line], False) is None


def test_exact_cwd_keeps_two_runs_without_a_cd() -> None:
    line = "      - run: ./gradlew x && ./gradlew y"
    assert exact_cwd(range(0, 1), ["./gradlew", "y"], [line], False) == PurePosixPath()
