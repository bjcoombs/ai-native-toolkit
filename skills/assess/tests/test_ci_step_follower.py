"""Issue #845: one shared CI-step follower (``lib.ci.ci_step_follower``) reads what a CI step runs
from scripts, local composite actions, local reusable workflows, make targets, and the plugins
and tasks Gradle and Maven bind, repo-local only and never executed."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from lib.ci.ci_step_follower import Followed, StepFollower, follow_step
from lib.ci.ci_step_paths import local_calls, local_reader, local_workflow, read_script
from lib.instructions.command_index import repo_files


def _repo(root: Path, files: dict[str, str]) -> StepFollower:
    root.mkdir(parents=True, exist_ok=True)
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "init", "-q"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True, capture_output=True)
    tracked = repo_files(root.resolve())
    return StepFollower(local_reader(root.resolve(), tracked), tracked)


def _one(follower: StepFollower, line: str) -> Followed:
    found = follower.step(line)
    assert len(found) == 1, found
    return found[0]


_ACTION = """name: x
runs:
  using: composite
  steps:
    - shell: bash
      run: ruff check .
    - shell: bash
      run: |
        # lint
        mypy src
"""
_WORKFLOW = """on: workflow_call
jobs:
  check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: pytest -q
      - uses: ./.github/actions/x
"""


def test_a_script_resolves_to_its_commands(tmp_path: Path) -> None:
    follower = _repo(tmp_path, {"hack/verify-x.sh": "#!/bin/sh\nset -e\n# check\ngo vet \\\n  ./...\n"})
    got = _one(follower, "run: hack/verify-x.sh")
    assert got.kind == "script" and got.paths == ("hack/verify-x.sh",)
    assert got.commands == ("set -e", "go vet ./...")
    assert _one(follower, "bash ./hack/verify-x.sh").commands == got.commands


def test_a_composite_action_resolves_to_its_steps(tmp_path: Path) -> None:
    follower = _repo(tmp_path, {".github/actions/x/action.yml": _ACTION})
    got = _one(follower, "- uses: ./.github/actions/x")
    assert got == Followed("composite", (".github/actions/x/action.yml",), ("ruff check .", "mypy src"))


def test_a_node_action_is_not_followed(tmp_path: Path) -> None:
    follower = _repo(tmp_path, {".github/actions/n/action.yml": "runs:\n  using: node20\n  main: x.js\n"})
    assert follower.step("uses: ./.github/actions/n") == []


def test_a_reusable_workflow_resolves_to_its_steps(tmp_path: Path) -> None:
    follower = _repo(tmp_path, {".github/workflows/check.yml": _WORKFLOW,
                                ".github/actions/x/action.yml": _ACTION})
    got = _one(follower, "uses: ./.github/workflows/check.yml")
    assert got.kind == "workflow"
    assert got.commands == ("pytest -q", "ruff check .", "mypy src")
    assert got.paths == (".github/workflows/check.yml", ".github/actions/x/action.yml")


def test_a_workflow_that_calls_itself_is_read_once(tmp_path: Path) -> None:
    loop = "on: workflow_call\njobs:\n  a:\n    uses: ./.github/workflows/loop.yml\n"
    follower = _repo(tmp_path, {".github/workflows/loop.yml": loop})
    got = _one(follower, "uses: ./.github/workflows/loop.yml")
    assert got.commands == () and got.unknown == ("./.github/workflows/loop.yml",)


_MAKEFILE = """.PHONY: verify lint test
GO ?= go

verify: lint test ## run every check
\t@echo verifying
\t$(MAKE) vet

lint:
\t-golangci-lint run ./...

test: lint
\t$(GO) test ./...

vet:
\tgo vet ./...
"""


def test_make_verify_resolves_to_its_recipe_and_prerequisites(tmp_path: Path) -> None:
    follower = _repo(tmp_path, {"Makefile": _MAKEFILE})
    got = _one(follower, "run: make verify")
    assert got.kind == "make" and got.paths == ("Makefile",)
    assert got.commands == ("golangci-lint run ./...", "$(GO) test ./...", "echo verifying",
                            "$(MAKE) vet", "go vet ./...")
    assert got.unknown == ()


def test_make_reads_the_directory_and_default_goal(tmp_path: Path) -> None:
    follower = _repo(tmp_path, {"svc/Makefile": "all:\n\tcargo test\n", "Makefile": "x:\n\ttrue\n"})
    assert _one(follower, "make -C svc").commands == ("cargo test",)
    assert _one(follower, "cd svc && make all").commands == ("cargo test",)
    missing = _one(follower, "make nothing")
    assert missing.commands == () and missing.unknown == ("nothing",)


_GRADLE = """plugins {
    id 'java'
    id 'checkstyle'
    id 'me.champeau.gradle.japicmp' version '0.4.2'
    id 'com.diffplug.spotless' version '6.0.0' apply false
}

// check.dependsOn notThis
tasks.register('japicmp', me.champeau.gradle.japicmp.JapicmpTask) {
}
check.dependsOn japicmp
"""
_GRADLE_KTS = """plugins {
    `java-library`
    id("io.gitlab.arturbosch.detekt")
}
tasks.named("check") {
    dependsOn(tasks.named("apiDump"))
}
"""


def test_gradle_check_resolves_to_bound_plugins_and_tasks(tmp_path: Path) -> None:
    follower = _repo(tmp_path, {"build.gradle": _GRADLE, "gradlew": "#!/bin/sh\n"})
    got = _one(follower, "./gradlew check --no-daemon")
    assert got.kind == "gradle" and got.paths == ("build.gradle",)
    assert set(got.plugins) == {"java", "checkstyle", "me.champeau.gradle.japicmp"}
    assert set(got.tasks) == {"test", "checkstyleMain", "checkstyleTest", "japicmp"}
    assert "notThis" not in got.tasks and "spotlessCheck" not in got.tasks
    assert set(_one(follower, "gradle build").tasks) >= {"check", "assemble", "jar", "japicmp"}


def test_gradle_reads_the_kotlin_dsl_and_the_asked_project(tmp_path: Path) -> None:
    follower = _repo(tmp_path, {"build.gradle": "plugins { id 'base' }\n", "lib/build.gradle.kts": _GRADLE_KTS})
    got = _one(follower, "./gradlew :lib:check")
    assert got.paths == ("lib/build.gradle.kts",)
    assert set(got.plugins) == {"java-library", "io.gitlab.arturbosch.detekt"}
    assert set(got.tasks) == {"test", "detekt", "apiDump"}


_POM = """<?xml version="1.0"?>
<project xmlns="http://maven.apache.org/POM/4.0.0">
  <artifactId>x</artifactId>
  <build>
    <plugins>
      <plugin>
        <groupId>com.github.siom79.japicmp</groupId>
        <artifactId>japicmp-maven-plugin</artifactId>
        <executions>
          <execution><goals><goal>cmp</goal></goals></execution>
        </executions>
      </plugin>
      <plugin>
        <artifactId>maven-checkstyle-plugin</artifactId>
        <executions>
          <execution><phase>validate</phase><goals><goal>check</goal></goals></execution>
        </executions>
      </plugin>
      <plugin>
        <artifactId>odd-maven-plugin</artifactId>
        <executions>
          <execution><goals><goal>run</goal></goals></execution>
        </executions>
      </plugin>
      <plugin>
        <groupId>com.diffplug.spotless</groupId>
        <artifactId>spotless-maven-plugin</artifactId>
      </plugin>
    </plugins>
  </build>
</project>
"""


def test_mvn_verify_resolves_to_plugins_bound_up_to_verify(tmp_path: Path) -> None:
    follower = _repo(tmp_path, {"pom.xml": _POM})
    got = _one(follower, "run: mvn -B -ntp verify")
    assert got.kind == "maven" and got.paths == ("pom.xml",)
    assert "japicmp-maven-plugin" in got.plugins and "japicmp-maven-plugin:cmp" in got.tasks
    assert got.tasks.index("maven-checkstyle-plugin:check") < got.tasks.index("maven-surefire-plugin:test")
    assert got.tasks.index("maven-surefire-plugin:test") < got.tasks.index("japicmp-maven-plugin:cmp")
    assert "maven-install-plugin:install" not in got.tasks
    assert got.unknown == ("odd-maven-plugin:run",)
    early = _one(follower, "./mvnw test")
    assert "japicmp-maven-plugin:cmp" not in early.tasks and "maven-surefire-plugin:test" in early.tasks
    goal = _one(follower, "mvn spotless:check")
    assert goal.tasks == ("spotless-maven-plugin:check",) and goal.plugins == ("spotless-maven-plugin",)


def test_a_pom_that_cannot_be_read_is_unknown_not_empty(tmp_path: Path) -> None:
    doctype = '<?xml version="1.0"?><!DOCTYPE p [<!ENTITY a "aa">]><project/>'
    assert _one(_repo(tmp_path / "a", {"pom.xml": doctype}), "mvn verify").unknown == ("doctype: pom.xml",)
    assert _one(_repo(tmp_path / "b", {"pom.xml": "<project>"}), "mvn verify").unknown == ("unparsed: pom.xml",)


@pytest.mark.skipif(os.name == "nt", reason="symlinks need privileges on Windows")
def test_a_symlink_out_of_the_repository_is_not_read(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.sh").write_text("cat /etc/passwd\n", encoding="utf-8")
    (outside / "Makefile").write_text("verify:\n\tsteal\n", encoding="utf-8")
    root = tmp_path / "repo"
    (root / "hack").mkdir(parents=True)
    (root / "hack" / "verify-x.sh").symlink_to(outside / "secret.sh")
    (root / "Makefile").symlink_to(outside / "Makefile")
    follower = _repo(root, {})
    assert follower.step("hack/verify-x.sh") == []
    assert follower.step("make verify") == []
    assert read_script(root, "hack/verify-x.sh") is None


def test_no_followed_command_is_executed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    marker = tmp_path / "ran"
    follower = _repo(tmp_path / "r", {"ci.sh": f"#!/bin/sh\ntouch {marker.as_posix()}\n",
                                      "Makefile": f"verify:\n\ttouch {marker.as_posix()}\n",
                                      "build.gradle": "plugins { id 'java' }\n", "pom.xml": _POM})

    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("the follower ran a process")

    for name in ("run", "Popen", "call", "check_call", "check_output"):
        monkeypatch.setattr(subprocess, name, refuse)
    monkeypatch.setattr(os, "system", refuse)
    lines = ["./ci.sh", "make verify", "./gradlew check", "mvn verify"]
    assert [f.kind for line in lines for f in follow_step(line, follower.read_local, follower.files)] == [
        "script", "make", "gradle", "maven"]
    assert not marker.exists()


def test_the_path_helpers_name_local_workflows() -> None:
    assert local_workflow("./.github/workflows/check.yml") == ".github/workflows/check.yml"
    assert local_workflow("org/repo/.github/workflows/check.yml@v1") is None
    assert local_calls("jobs:\n  a:\n    uses: './.github/workflows/t.yaml'\n") == {"t.yaml"}


def test_the_readers_report_what_they_cannot_settle(tmp_path: Path) -> None:
    profile = _POM.replace("</project>", "<profiles><profile><id>ci</id><build><plugins><plugin>"
                           "<artifactId>a</artifactId></plugin></plugins></build></profile></profiles>"
                           "<packaging>bundle</packaging></project>")
    follower = _repo(tmp_path, {"mk/rules.mk": "include common.mk\nci:\n\tpytest\n",
                                "app/build.gradle": "apply from: 'x.gradle'\nplugins { id 'java' }\n",
                                "sub/pom.xml": profile})
    made = _one(follower, "make -f mk/rules.mk ci")
    assert made.commands == ("pytest",) and made.unknown == ("include",)
    gradle = _one(follower, "./gradlew -p app check")
    assert gradle.unknown == ("apply from: app/build.gradle",) and gradle.tasks == ("test",)
    maven = _one(follower, "mvn -f sub/pom.xml verify lifecycle-nope")
    assert set(maven.unknown) >= {"profile: ci", "packaging: bundle", "phase: lifecycle-nope"}
    assert "maven-surefire-plugin:test" not in maven.tasks


@pytest.mark.parametrize("line", [
    "cd /tmp && make ci", "cd .. && ./ci.sh", "make -C ../x", "./gradlew -p ../x check",
    "mvn -f ../pom.xml verify", "uses: ./.github/actions/missing", "pytest -q", "make -C \"$D\" ci",
])
def test_a_step_outside_the_checkout_or_unread_follows_nothing(tmp_path: Path, line: str) -> None:
    follower = _repo(tmp_path, {"Makefile": "ci:\n\ttrue\n", "ci.sh": "true\n", "pom.xml": _POM,
                                "build.gradle": "plugins { id 'java' }\n"})
    assert follower.step(line) == []
