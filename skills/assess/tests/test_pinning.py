"""The lockfile check across ecosystems, libraries and direct pins (issue #927 part B)."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from lib.agent_environment import scan_agent_environment  # noqa: E402
from lib.counts_only import counts_only_scans  # noqa: E402
from lib.level_tally import default_model, level_result  # noqa: E402

GRADLE_DEPS = 'plugins { id("java") }\ndependencies {\n    implementation(libs.guava)\n}\n'
CSPROJ = ('<Project Sdk="Microsoft.NET.Sdk"><ItemGroup>'
          '<PackageReference Include="Newtonsoft.Json" /></ItemGroup></Project>\n')
SWIFT_APP = ('// swift-tools-version:5.9\nimport PackageDescription\nlet package = Package(\n'
             '  name: "App",\n  products: [.executable(name: "app", targets: ["App"])]\n)\n')
SWIFT_LIB = SWIFT_APP.replace('.executable(name: "app", targets: ["App"])',
                              '.library(name: "Lib", targets: ["Lib"])')
PUB_APP = "name: app\npublish_to: none\ndependencies:\n  http: ^1.0.0\n"
PUB_PACKAGE = "name: pkg\nversion: 1.0.0\ndependencies:\n  http: ^1.0.0\n"
VCPKG = {"name": "x", "dependencies": ["fmt"]}
NODE = {"package.json": json.dumps({"dependencies": {"a": "1"}})}


def _repo(tmp_path: Path, files: dict[str, str]) -> Path:
    root = tmp_path / "repo"
    root.mkdir(parents=True)
    env = {**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null"}
    for rel, body in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(body, encoding="utf-8")
    for args in (("init", "-q", "-b", "main"), ("add", "-A"),
                 ("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "init")):
        subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, env=env)
    return root


def _pinning(tmp_path: Path, files: dict[str, str]) -> dict[str, Any]:
    return dict(scan_agent_environment(_repo(tmp_path, files), probe_ci=False)["pinning"])


def _rows(pinning: dict[str, Any]) -> dict[str, tuple[bool, str | None, str | None]]:
    return {r["manifest"]: (r["locked"], r["lockfile"], r["note"]) for r in pinning["manifests"]}


def _condition(pinning: dict[str, Any]) -> tuple[str, str | None]:
    result = level_result(default_model(), {"agent_environment": {"available": True, "pinning": pinning}})
    row = next(c for c in result["conditions"] if c["id"] == "layer-5-ci.2")
    return row["state"], row.get("reason")


# One fixture pair per manifest kind: (files without the lock, the lock files, ecosystem).
KINDS = {
    "mix": ({"mix.exs": "defmodule X.MixProject do\nend\n"}, {"mix.lock": "%{}\n"}, "elixir"),
    "pub-app": ({"pubspec.yaml": PUB_APP}, {"pubspec.lock": "packages: {}\n"}, "dart"),
    "swift-executable": ({"Package.swift": SWIFT_APP}, {"Package.resolved": "{}\n"}, "swift"),
    "swift-xcworkspace": ({"Package.swift": SWIFT_APP},
                          {"App.xcworkspace/xcshareddata/swiftpm/Package.resolved": "{}\n"}, "swift"),
    "csproj": ({"App.csproj": CSPROJ}, {"packages.lock.json": "{}\n"}, "dotnet"),
    "gradle-kts": ({"build.gradle.kts": GRADLE_DEPS}, {"gradle.lockfile": "# lock\n"}, "jvm"),
    "vcpkg": ({"vcpkg.json": json.dumps(VCPKG)},
              {"vcpkg.json": json.dumps({**VCPKG, "builtin-baseline": "3426db05"})}, "cpp"),
    "conan": ({"conanfile.txt": "[requires]\nfmt/10.2.1\n"}, {"conan.lock": "{}\n"}, "cpp"),
}


@pytest.mark.parametrize("kind", sorted(KINDS))
@pytest.mark.parametrize("locked", [True, False], ids=["locked", "unlocked"])
def test_each_manifest_kind_reads_met_with_its_lock_and_unmet_without(
        tmp_path: Path, kind: str, locked: bool) -> None:
    base, lock, ecosystem = KINDS[kind]
    pinning = _pinning(tmp_path, {**base, **lock} if locked else base)
    [row] = pinning["manifests"]
    expected_lock = next(iter(lock)) if locked else None
    assert (row["ecosystem"], row["locked"], row["lockfile"], row["note"]) == (
        ecosystem, locked, expected_lock, None)
    assert (pinning["manifests_total"], pinning["unlocked_total"]) == (1, 0 if locked else 1)
    assert _condition(pinning) == (("met", None) if locked else ("unmet", None))


@pytest.mark.parametrize("files", [
    pytest.param({"composer.json": json.dumps({"type": "library", "require": {"php": ">=8"}})},
                 id="COMPOSER-LIB"),
    pytest.param({"composer.json": json.dumps({"require": {"php": ">=8"}})}, id="COMPOSER-NOTYPE"),
    pytest.param({"pubspec.yaml": PUB_PACKAGE}, id="PUB-PUBLISHED"),
    pytest.param({"Package.swift": SWIFT_LIB, ".gitignore": "Package.resolved\n"}, id="SWIFT-LIB"),
])
def test_a_library_without_a_lock_is_listed_but_not_counted(tmp_path: Path, files: dict[str, str]) -> None:
    pinning = _pinning(tmp_path, files)
    [row] = pinning["manifests"]
    assert (row["locked"], row["note"]) == (False, "skipped_library")
    assert (pinning["manifests_total"], pinning["unlocked_total"], pinning["all_locked"]) == (0, 0, None)
    assert _condition(pinning) == ("not_measured", "no_manifests")


def test_a_library_that_commits_its_lock_counts_as_locked(tmp_path: Path) -> None:
    pinning = _pinning(tmp_path, {"composer.json": json.dumps({"type": "library"}), "composer.lock": "{}"})
    assert _rows(pinning) == {"composer.json": (True, "composer.lock", None)}
    assert pinning["manifests_total"] == 1


def test_a_composer_project_without_a_lock_is_unmet(tmp_path: Path) -> None:
    pinning = _pinning(tmp_path, {"composer.json": json.dumps({"type": "project"})})
    assert _rows(pinning) == {"composer.json": (False, None, None)}
    assert _condition(pinning) == ("unmet", None)


def test_mixed_library_and_unlocked_app_counts_only_the_app(tmp_path: Path) -> None:
    pinning = _pinning(tmp_path, {"lib/composer.json": json.dumps({"type": "library"}), **NODE})
    assert _rows(pinning) == {"lib/composer.json": (False, None, "skipped_library"),
                              "package.json": (False, None, None)}
    assert (pinning["manifests_total"], pinning["unlocked_total"]) == (1, 1)
    assert _condition(pinning) == ("unmet", None)


@pytest.mark.parametrize("files, manifest", [
    pytest.param({"build.gradle.kts": GRADLE_DEPS, "gradle/libs.versions.toml": "[versions]\n"},
                 "build.gradle.kts", id="GRADLE-CATALOG"),
    pytest.param({"Directory.Packages.props": "<Project />\n", "src/App/App.csproj": CSPROJ},
                 "src/App/App.csproj", id="NUGET-CPM"),
])
def test_direct_pins_only_read_unlocked_with_a_note(
        tmp_path: Path, files: dict[str, str], manifest: str) -> None:
    pinning = _pinning(tmp_path, files)
    assert _rows(pinning) == {manifest: (False, None, "direct_pins_only")}
    assert (pinning["manifests_total"], pinning["unlocked_total"]) == (1, 1)
    assert _condition(pinning) == ("unmet", None)


@pytest.mark.parametrize("lock", ["settings-gradle.lockfile", "gradle/verification-metadata.xml"])
def test_a_gradle_root_lock_covers_every_project(tmp_path: Path, lock: str) -> None:
    pinning = _pinning(tmp_path, {"app/build.gradle": "dependencies {\n}\n", lock: "x\n",
                                  "gradle/libs.versions.toml": "[versions]\n"})
    assert _rows(pinning) == {"app/build.gradle": (True, lock, None)}


def test_a_cpm_project_with_its_lock_reads_locked(tmp_path: Path) -> None:
    pinning = _pinning(tmp_path, {"Directory.Packages.props": "<Project />\n", "App.csproj": CSPROJ,
                                  "packages.lock.json": "{}"})
    assert _rows(pinning) == {"App.csproj": (True, "packages.lock.json", None)}


@pytest.mark.parametrize("files", [
    pytest.param({"build.gradle.kts": 'plugins { id("base") }\n'}, id="gradle-no-dependencies"),
    pytest.param({"App.csproj": '<Project Sdk="Microsoft.NET.Sdk" />\n'}, id="csproj-no-package-reference"),
    pytest.param({"vcpkg.json": json.dumps({"name": "x"})}, id="vcpkg-no-dependencies"),
    pytest.param({"vcpkg.json": "{"}, id="vcpkg-unparsed"),
    pytest.param({"Directory.Packages.props": "<Project />\n"}, id="cpm-props-alone"),
    pytest.param({"CMakeLists.txt": "project(x)\n"}, id="CMAKE"),
])
def test_a_manifest_with_nothing_to_lock_is_not_a_row(tmp_path: Path, files: dict[str, str]) -> None:
    pinning = _pinning(tmp_path, files)
    assert pinning["manifests"] == [] and pinning["manifests_total"] == 0
    assert _condition(pinning) == ("not_measured", "no_manifests")


def test_a_per_project_lock_in_a_parent_does_not_count(tmp_path: Path) -> None:
    """A .NET or Gradle lock locks the project beside it, never a child project."""
    pinning = _pinning(tmp_path, {"packages.lock.json": "{}", "src/App.csproj": CSPROJ,
                                  "gradle.lockfile": "x", "app/build.gradle.kts": GRADLE_DEPS})
    assert _rows(pinning) == {"app/build.gradle.kts": (False, None, None),
                              "src/App.csproj": (False, None, None)}


def test_an_xcodeproj_workspace_lock_counts_and_a_child_package_is_its_own(tmp_path: Path) -> None:
    resolved = "App.xcodeproj/project.xcworkspace/xcshareddata/swiftpm/Package.resolved"
    pinning = _pinning(tmp_path, {"Package.swift": SWIFT_APP, resolved: "{}",
                                  "Modules/Core/Package.swift": SWIFT_LIB})
    assert _rows(pinning) == {"Modules/Core/Package.swift": (False, None, "skipped_library"),
                              "Package.swift": (True, resolved, None)}


def test_skipped_libraries_do_not_block_setup_ready(tmp_path: Path) -> None:
    block = scan_agent_environment(_repo(tmp_path, {"composer.json": json.dumps({"type": "library"})}),
                                   probe_ci=False)
    assert block["setup_ready"] is True
    assert "setup_or_lockfile" not in block["layer5_cap"]["unmet"]


def test_counts_only_keeps_the_note_token(tmp_path: Path) -> None:
    block = scan_agent_environment(_repo(tmp_path, {"build.gradle.kts": GRADLE_DEPS,
                                                    "gradle/libs.versions.toml": "[versions]\n"}),
                                   probe_ci=False)
    kept = counts_only_scans({"agent_environment": block})["agent_environment"]
    [row] = kept["pinning"]["manifests"]
    assert row["note"] == "direct_pins_only"
    assert kept["pinning"]["manifests_total"] == 1
