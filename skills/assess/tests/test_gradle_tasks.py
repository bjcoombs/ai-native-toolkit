"""Gradle option values, ``build``, and project-path tasks on the CI reader (#1124, #1126).

Each expected reading was checked against Gradle 9.4 (``--task-graph``) on a build with
``app`` and ``lib`` subprojects, each applying ``java`` and ``jacoco``, and on a build with
only the ``base`` plugin:

- Every space-separated option value below is read as the option's value, never a task:
  ``--warning-mode all check`` runs ``:app:check`` and ``:lib:check``. ``-Msha256`` and
  ``-Foff`` are ``-M`` / ``-F`` with an attached value, not ``-m``. ``test --tests Foo``
  runs ``test`` only. (``-b`` and ``-c`` are gone from 9.x; they stay for older wrappers
  and were not run.)
- ``build`` depends on ``check`` with the base plugin alone; ``check`` runs ``test`` with
  ``java``. ``build -x check`` runs no ``check``.
- ``:app:jacocoTestCoverageVerification`` and ``app:jacocoTestCoverageVerification`` run
  in ``app`` only; ``:jacocoTestCoverageVerification`` runs in the root project only (and
  fails when the root has no such task).
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from lib.check_command import check_verdict, is_lint_run, may_check
from lib.ci.ci_checks import scan_ci_checks
from lib.runner_args import (
    gradle_build_verdict, gradle_dry_run, gradle_project, gradle_runs_in, gradle_task_paths,
    gradle_tasks, gradle_tests_in_check)
from lib.testing.coverage_gate import detect_coverage_gate

VALUED = [
    ("--warning-mode", "all"), ("--console", "plain"), ("--console-unicode", "disable"),
    ("--max-workers", "2"), ("--priority", "low"), ("-I", "init.gradle"),
    ("--init-script", "x"), ("-c", "settings.gradle"), ("-g", "dir"), ("-b", "build.gradle"),
    ("--project-dir", "."), ("-p", "."), ("-P", "m"), ("-D", "k"), ("--system-prop", "k"),
    ("--project-prop", "m"), ("-F", "off"), ("--dependency-verification", "off"),
    ("-M", "sha256"), ("--write-verification-metadata", "sha256"),
    ("--project-cache-dir", "pc"), ("--configuration-cache-problems", "warn"),
    ("--update-locks", "x:y"), ("--gradle-user-home", "gh"), ("--include-build", "b"),
]


def _repo(root: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=root, check=True, encoding="utf-8", errors="replace")
    subprocess.run(["git", "add", "-A"], cwd=root, check=True, encoding="utf-8", errors="replace")
    return root


def _wf(*runs: str) -> str:
    steps = "".join(f"      - run: {r}\n" for r in runs)
    return f"on: pull_request\njobs:\n  t:\n    runs-on: ubuntu-latest\n    steps:\n{steps}"


def _scan(root: Path, line: str, files: dict[str, str]) -> tuple[bool | None, str]:
    block = scan_ci_checks(_repo(root, {".github/workflows/ci.yml": _wf(line), **files}))
    return block["enforced_check_runs"], block["enforced_basis"]


JAVA = {"app/build.gradle": "plugins { id 'java' }\n"}


# --- #1124: option values ------------------------------------------------------------

@pytest.mark.parametrize(("option", "value"), VALUED)
def test_an_option_value_is_no_task(option: str, value: str) -> None:
    assert gradle_tasks([option, value, "check"]) == (["check"], set())
    assert check_verdict(f"./gradlew {option} {value} check") is True
    assert check_verdict(f"./gradlew {option} {value} assemble") is False
    assert check_verdict(f"./gradlew {option} {value} test") is True


@pytest.mark.parametrize(("args", "dry"), [
    ("-Msha256 check", False), ("-Foff check", False), ("-m check", True), ("-qm check", True),
])
def test_attached_m_and_f_values_are_no_dry_run(args: str, dry: bool) -> None:
    assert gradle_dry_run(args.split()) is dry
    assert (check_verdict(f"./gradlew {args}") is True) is not dry


def test_the_tests_filter_is_no_task() -> None:
    assert gradle_tasks(["test", "--tests", "Foo"]) == (["test"], set())


@pytest.mark.parametrize(("line", "check"), [
    ("./gradlew clean check", True), ("./gradlew clean test", True),
    ("./gradlew clean assemble", False), ("./gradlew --warning-mode all clean check", True),
])
def test_every_named_task_runs(line: str, check: bool) -> None:
    assert (check_verdict(line) is True) is check


def test_a_lint_task_after_an_option_value_is_a_lint_run() -> None:
    assert is_lint_run("./gradlew --warning-mode all ktlintCheck")
    assert not is_lint_run("./gradlew --warning-mode all assemble")


@pytest.mark.parametrize("line", [
    "./gradlew --warning-mode all check", "./gradlew --console plain check",
    "./gradlew -I init.gradle check", "./gradlew --max-workers 2 check",
])
def test_issue_lines_credit_layer_5(tmp_path: Path, line: str) -> None:
    assert _scan(tmp_path, line, JAVA) == (True, "enforced_check")


# --- #1124: build ----------------------------------------------------------------------

@pytest.mark.parametrize(("text", "applies"), [
    ("plugins { id 'java' }\n", True),
    ("plugins {\n    id \"java-library\"\n}\n", True),
    ("apply plugin: 'java'\n", True),
    ("apply(plugin = \"application\")\n", True),
    ("plugins { id(\"org.jetbrains.kotlin.jvm\") version \"2.0.0\" }\n", True),
    ("plugins {\n    kotlin(\"jvm\") version \"2.0.0\"\n}\n", True),
    ("plugins {\n    java\n}\n", True),
    ("plugins {\n    `java-library`\n}\n", True),
    ("plugins { id 'com.android.application' }\n", True),
    ("plugins { id 'org.jetbrains.kotlin.jvm' version '2.0.0' apply false }\n", False),
    ("plugins {\n    kotlin(\"jvm\") version \"2.0.0\" apply false\n}\n", False),
    ("// apply plugin: 'java'\n", False),
    ("/* plugins { id 'java' } */\n", False),
    ("plugins { id 'base' }\njava {\n    toolchain {}\n}\n", False),
    ("plugins { alias(libs.plugins.kotlin.jvm) }\n", False),
])
def test_plugins_that_put_tests_in_check(tmp_path: Path, text: str, applies: bool) -> None:
    assert gradle_tests_in_check(_repo(tmp_path, {"build.gradle.kts": text})) is applies


@pytest.mark.parametrize(("line", "files", "expected"), [
    ("./gradlew build", JAVA, (True, "enforced_check")),
    ("gradle build", JAVA, (True, "enforced_check")),
    ("./gradlew clean build", JAVA, (True, "enforced_check")),
    ("./gradlew --warning-mode all build", JAVA, (True, "enforced_check")),
    ("./gradlew build", {"build.gradle": "plugins { id 'base' }\n"}, (None, "check_target")),
    ("./gradlew build", {"build.gradle": "plugins { alias(libs.plugins.kotlin.jvm) }\n"},
     (None, "check_target")),
    ("./gradlew build -x test", JAVA, (None, "check_target")),
    ("./gradlew build -x :app:check", JAVA, (None, "check_target")),
    ("./gradlew build -x check", JAVA, (False, "no_check_command")),
    ("./gradlew -m build", JAVA, (False, "no_check_command")),
    ("./gradlew --dry-run build", JAVA, (False, "no_check_command")),
    ("./gradlew assemble", JAVA, (False, "no_check_command")),
])
def test_build_reads_by_the_plugin(tmp_path: Path, line: str, files: dict[str, str],
                                   expected: tuple[bool | None, str]) -> None:
    assert _scan(tmp_path, line, files) == expected


def test_build_outside_a_scan_is_unknown() -> None:
    assert check_verdict("./gradlew build") is None and may_check("./gradlew build")
    assert gradle_build_verdict(["./gradlew", "build", "-x", "check"]) is False
    assert gradle_build_verdict(["mvn", "build"]) is False


# --- #1126: project-path tasks --------------------------------------------------------

@pytest.mark.parametrize(("task", "project"), [
    ("check", None), (":check", ""), (":app:check", "app"), ("app:check", "app"),
    (":a:b:check", "a/b"),
])
def test_gradle_project(task: str, project: str | None) -> None:
    assert gradle_project(task) == project


def test_task_paths_keep_the_project() -> None:
    assert gradle_task_paths(["--console", "plain", ":app:check", "-x", "test"]) == [":app:check"]
    assert gradle_tasks([":app:check"]) == (["check"], set())


V = frozenset({"jacocoTestCoverageVerification"})


@pytest.mark.parametrize(("args", "file", "runs"), [
    (":app:jacocoTestCoverageVerification", "app/build.gradle", True),
    ("app:jacocoTestCoverageVerification", "app/build.gradle.kts", True),
    (":a:b:jacocoTestCoverageVerification", "a/b/build.gradle", True),
    (":jacocoTestCoverageVerification", "build.gradle", True),
    ("jacocoTestCoverageVerification", "lib/build.gradle", True),
    (":app:jacocoTestCoverageVerification", "lib/build.gradle", None),
    (":app:jacocoTestCoverageVerification", "build.gradle", None),
    (":jacocoTestCoverageVerification", "app/build.gradle", None),
    (":app:jacocoTestCoverageVerification", "app/jacoco.gradle", None),
    ("-p sub :app:jacocoTestCoverageVerification", "app/build.gradle", None),
    ("--project-dir=sub :app:jacocoTestCoverageVerification", "app/build.gradle", None),
    (":lib:jacocoTestCoverageVerification jacocoTestCoverageVerification", "app/build.gradle",
     True),
    (":app:check", "app/build.gradle", False),
    # No settings file read (outside a scan): the gate's directory ends with the path.
    (":app:jacocoTestCoverageVerification", "android/app/build.gradle", True),
    ("-p android :app:jacocoTestCoverageVerification", "android/app/build.gradle", True),
    ("-p ./android :app:jacocoTestCoverageVerification", "android/app/build.gradle", True),
    ("-p android :lib:jacocoTestCoverageVerification", "android/app/build.gradle", None),
    ("-c other.gradle :app:jacocoTestCoverageVerification", "app/build.gradle", None),
    ("-p $DIR :app:jacocoTestCoverageVerification", "app/build.gradle", None),
])
def test_gradle_runs_in(args: str, file: str, runs: bool | None) -> None:
    assert gradle_runs_in(args.split(), V, file) is runs
    assert gradle_runs_in(args.split(), V, None) is (runs is not False)


GATE = ('plugins { id "java"; id "jacoco" }\njacocoTestCoverageVerification {\n'
        '  violationRules { rule { limit { minimum = 0.80 } } }\n}\n')
WIRED = GATE + "check.dependsOn jacocoTestCoverageVerification\n"
ANDROID = {"android/settings.gradle": "include ':app'\n", "android/app/build.gradle": GATE}
ROOTED = {"settings.gradle": "include ':app', ':lib'\n"}


def _gate(root: Path, line: str, files: dict[str, str]) -> tuple[bool | None, str]:
    block = detect_coverage_gate(_repo(root, {".github/workflows/ci.yml": _wf(line), **files}))
    return block["enforced_meets_minimum"], block["enforced_basis"]


@pytest.mark.parametrize(("line", "files", "expected"), [
    ("./gradlew :app:jacocoTestCoverageVerification", {"app/build.gradle": GATE},
     (True, "ci_run")),
    ("./gradlew :app:jacocoTestCoverageVerification", {"lib/build.gradle": GATE},
     (None, "indirect_run")),
    ("./gradlew :app:jacocoTestCoverageVerification", {"build.gradle": GATE},
     (None, "indirect_run")),
    ("./gradlew jacocoTestCoverageVerification", {"lib/build.gradle": GATE},
     (True, "ci_run")),
    ("./gradlew :app:check", {"app/build.gradle": WIRED}, (True, "ci_run")),
    ("./gradlew :app:check", {"lib/build.gradle": WIRED}, (None, "indirect_run")),
    ("./gradlew check", {"lib/build.gradle": WIRED}, (True, "ci_run")),
    ("./gradlew --warning-mode all jacocoTestCoverageVerification", {"build.gradle": GATE},
     (True, "ci_run")),
    # The Gradle root is the nearest tracked settings file (claude-review on #1143).
    ("cd android && ./gradlew :app:jacocoTestCoverageVerification", ANDROID, (True, "ci_run")),
    ("./gradlew -p android :app:jacocoTestCoverageVerification", ANDROID, (True, "ci_run")),
    ("./gradlew --project-dir=android :app:check",
     {**ANDROID, "android/app/build.gradle": WIRED}, (True, "ci_run")),
    ("cd android && ./gradlew :lib:jacocoTestCoverageVerification", ANDROID,
     (None, "indirect_run")),
    ("./gradlew :app:jacocoTestCoverageVerification", ROOTED | {"app/build.gradle": GATE},
     (True, "ci_run")),
    ("./gradlew :app:jacocoTestCoverageVerification", ROOTED | {"lib/build.gradle": GATE},
     (None, "indirect_run")),
    ("./gradlew :app:jacocoTestCoverageVerification", ROOTED | {"lib/app/build.gradle": GATE},
     (None, "indirect_run")),
    ("./gradlew -c other.gradle :app:jacocoTestCoverageVerification",
     ROOTED | {"app/build.gradle": GATE}, (None, "indirect_run")),
])
def test_coverage_gate_by_project(tmp_path: Path, line: str, files: dict[str, str],
                                  expected: tuple[bool | None, str]) -> None:
    assert _gate(tmp_path, line, files) == expected
