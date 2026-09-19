"""Signal-consumption tests for the capability-driven JVM offer flow (#113).

The CI contract is *signal consumption*: given a tool's output (canned
``mvn dependency:analyze`` text), the deterministic core feeds the scorecard
correctly. The agent's runtime tool *choice* is human-judged and not tested
here. A synthetic Maven fixture under ``tests/fixtures/maven_project/`` stands in
for the real Helidon repo CI cannot reach.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from lib.jvm_capabilities import (
    count_used_undeclared,
    detect_build_system,
    detect_configured_plugins,
    parse_dependency_analyze,
    scan_jvm_capabilities,
)
from lib.liveness_scan import scan_liveness

FIXTURE = Path(__file__).parent / "fixtures" / "maven_project"


def _write(root: Path, rel: str, text: str = "x") -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _analyze_output() -> str:
    return (FIXTURE / "dependency-analyze-output.txt").read_text(encoding="utf-8")


# ── parsing dependency:analyze (the consumed signal) ───────────────────────

def test_parse_dependency_analyze_extracts_unused_declared() -> None:
    candidates = parse_dependency_analyze(_analyze_output(), pom_path="pom.xml")
    symbols = {c["symbol"] for c in candidates}
    assert symbols == {
        "org.apache.commons:commons-lang3",
        "com.google.guava:guava",
    }
    # Used-undeclared (slf4j) is NOT a liveness dead-weight candidate.
    assert "org.slf4j:slf4j-api" not in symbols
    for c in candidates:
        assert c["kind"] == "unused declared dependency"
        assert c["path"] == "pom.xml"


def test_used_undeclared_counted_but_not_a_candidate() -> None:
    assert count_used_undeclared(_analyze_output()) == 1


def test_parse_empty_output_is_no_candidates() -> None:
    assert parse_dependency_analyze("[INFO] BUILD SUCCESS\n") == []


def test_parse_stops_at_section_boundary() -> None:
    # An unused block followed by a non-coordinate line must not bleed into it.
    text = (
        "[WARNING] Unused declared dependencies found:\n"
        "[WARNING]    a.b:c:jar:1.0:compile\n"
        "[INFO] BUILD SUCCESS\n"
        "    d.e:f:jar:2.0:compile\n"  # outside the block - ignored
    )
    syms = {c["symbol"] for c in parse_dependency_analyze(text)}
    assert syms == {"a.b:c"}


# ── build-system detection ─────────────────────────────────────────────────

def test_detect_maven_in_fixture() -> None:
    system, files = detect_build_system(FIXTURE)
    assert system == "maven"
    assert "pom.xml" in files


def test_detect_gradle(tmp_path: Path) -> None:
    _write(tmp_path, "build.gradle", "plugins { id 'java' }")
    _write(tmp_path, "src/Main.java", "class Main {}")
    system, files = detect_build_system(tmp_path)
    assert system == "gradle"
    assert "build.gradle" in files


def test_maven_wins_over_gradle_when_both_present(tmp_path: Path) -> None:
    _write(tmp_path, "pom.xml", "<project/>")
    _write(tmp_path, "build.gradle", "plugins {}")
    _write(tmp_path, "src/main/java/A.java", "class A {}")
    system, _ = detect_build_system(tmp_path)
    assert system == "maven"


def test_detect_none_for_non_jvm(tmp_path: Path) -> None:
    _write(tmp_path, "main.py", "x = 1")
    system, files = detect_build_system(tmp_path)
    assert system is None
    assert files == []


def test_fixture_pom_excluded_when_under_tests_fixtures(tmp_path: Path) -> None:
    # A pom under tests/fixtures/ must not make a repo look like a Maven project
    # (the auto-exclude that keeps the assess run-context baseline stable).
    # JVM source outside the fixture meets the source threshold, so a null
    # result here can only come from the fixture exclusion.
    _write(tmp_path, "tests/fixtures/sample/pom.xml", "<project/>")
    _write(tmp_path, "src/main/java/A.java", "class A {}")
    system, _ = detect_build_system(tmp_path)
    assert system is None


def test_user_exclude_dir_prunes_build_file(tmp_path: Path) -> None:
    _write(tmp_path, "legacy/pom.xml", "<project/>")
    _write(tmp_path, "src/main/java/A.java", "class A {}")
    assert detect_build_system(tmp_path)[0] == "maven"
    assert detect_build_system(tmp_path, extra_exclude_dirs={"legacy"}) == (None, [])


def test_user_exclude_pattern_on_source_drops_threshold(tmp_path: Path) -> None:
    # A basename pattern applies to source files too: excluding the only JVM
    # source leaves the build file below the source threshold.
    _write(tmp_path, "pom.xml", "<project/>")
    _write(tmp_path, "src/main/java/Generated.java", "class Generated {}")
    assert detect_build_system(tmp_path)[0] == "maven"
    assert detect_build_system(
        tmp_path, extra_exclude_patterns=["Generated*.java"]) == (None, [])


def test_requires_jvm_source_groovy_counts(tmp_path: Path) -> None:
    # Grails apps, Jenkins plugins and Gradle plugin projects hold Groovy only.
    _write(tmp_path, "build.gradle", "plugins { id 'groovy' }")
    _write(tmp_path, "src/main/groovy/Plugin.groovy", "class Plugin {}")
    assert detect_build_system(tmp_path) == ("gradle", ["build.gradle"])


# ── JVM source threshold and platform wrappers ──────────────────────────────

def _flutter_app(root: Path, prefix: str = "") -> None:
    _write(root, f"{prefix}pubspec.yaml", "name: demo")
    _write(root, f"{prefix}lib/main.dart", "void main() {}")
    _write(root, f"{prefix}android/build.gradle.kts", "plugins {}")
    _write(root, f"{prefix}android/app/build.gradle.kts", "plugins {}")
    _write(root, f"{prefix}android/app/src/main/kotlin/MainActivity.kt",
           "class MainActivity")


def test_requires_jvm_source_pom_alone_is_not_maven(tmp_path: Path) -> None:
    _write(tmp_path, "pom.xml", "<project/>")
    assert detect_build_system(tmp_path) == (None, [])


def test_requires_jvm_source_one_file_meets_threshold(tmp_path: Path) -> None:
    _write(tmp_path, "build.gradle", "plugins {}")
    _write(tmp_path, "src/main/scala/App.scala", "object App")
    assert detect_build_system(tmp_path) == ("gradle", ["build.gradle"])


def test_requires_jvm_source_outside_wrapper_not_inside(tmp_path: Path) -> None:
    # A root build file does not make a JVM codebase when the only JVM source
    # sits inside the Flutter wrapper.
    _flutter_app(tmp_path)
    _write(tmp_path, "build.gradle", "plugins {}")
    assert detect_build_system(tmp_path) == (None, [])


def test_platform_wrapper_flutter_android_is_not_jvm(tmp_path: Path) -> None:
    _flutter_app(tmp_path)
    assert detect_build_system(tmp_path) == (None, [])
    assert scan_jvm_capabilities(tmp_path, mvn_on_path=False) == {
        "available": False, "build_system": None, "build_files": []}


def test_platform_wrapper_nested_flutter_app_is_not_jvm(tmp_path: Path) -> None:
    _flutter_app(tmp_path, "apps/shop/")
    assert detect_build_system(tmp_path) == (None, [])


@pytest.mark.parametrize("section, package", [
    ("dependencies", "react-native"),
    ("dependencies", "@capacitor/android"),
    ("devDependencies", "cordova-android"),
])
def test_platform_wrapper_package_json_android_is_not_jvm(
        tmp_path: Path, section: str, package: str) -> None:
    _write(tmp_path, "package.json", json.dumps({section: {package: "1.0.0"}}))
    _write(tmp_path, "android/build.gradle", "buildscript {}")
    _write(tmp_path, "android/app/src/main/java/MainActivity.java",
           "class MainActivity {}")
    assert detect_build_system(tmp_path) == (None, [])


def test_platform_wrapper_package_json_without_wrapper_dep_counts(
        tmp_path: Path) -> None:
    # A package.json that names no wrapper dependency leaves android/ counted.
    _write(tmp_path, "package.json", json.dumps({"dependencies": {"left-pad": "1"}}))
    _write(tmp_path, "android/build.gradle", "buildscript {}")
    _write(tmp_path, "android/app/src/main/java/MainActivity.java",
           "class MainActivity {}")
    assert detect_build_system(tmp_path) == ("gradle", ["android/build.gradle"])


def _cordova_app(root: Path, prefix: str = "", package: str | None = None,
                 config_xml: bool = True) -> None:
    # `cordova platform add android` layout: config.xml and package.json at the
    # app root, the generated Android project under platforms/android/.
    if config_xml:
        _write(root, f"{prefix}config.xml",
               '<widget xmlns:cdv="http://cordova.apache.org/ns/1.0"></widget>')
    deps = {"devDependencies": {package: "13.0.0"}} if package else {}
    _write(root, f"{prefix}package.json", json.dumps(deps))
    _write(root, f"{prefix}platforms/android/build.gradle", "buildscript {}")
    _write(root, f"{prefix}platforms/android/app/build.gradle", "apply plugin: 'x'")
    _write(root, f"{prefix}platforms/android/app/src/main/java/MainActivity.java",
           "class MainActivity {}")


@pytest.mark.parametrize("package", [None, "cordova-android"])
def test_platform_wrapper_cordova_platforms_android_is_not_jvm(
        tmp_path: Path, package: str | None) -> None:
    _cordova_app(tmp_path, package=package)
    assert detect_build_system(tmp_path) == (None, [])


def test_platform_wrapper_cordova_package_json_alone_is_not_jvm(
        tmp_path: Path) -> None:
    # No config.xml: the cordova-android package.json alone marks the root.
    _cordova_app(tmp_path, package="cordova-android", config_xml=False)
    assert detect_build_system(tmp_path) == (None, [])


@pytest.mark.parametrize("package", ["react-native", "@capacitor/android"])
def test_platform_wrapper_platforms_android_other_wrapper_package_counts(
        tmp_path: Path, package: str) -> None:
    # Only cordova-android marks platforms/android/; other wrapper packages
    # mark a sibling android/ and nothing else.
    _cordova_app(tmp_path, package=package, config_xml=False)
    assert detect_build_system(tmp_path)[0] == "gradle"


def test_platform_wrapper_nested_cordova_app_is_not_jvm(tmp_path: Path) -> None:
    _cordova_app(tmp_path, "apps/hybrid/")
    assert detect_build_system(tmp_path) == (None, [])


def test_platform_wrapper_platforms_android_without_cordova_counts(
        tmp_path: Path) -> None:
    # platforms/android/ with no Cordova marker beside platforms/ stays counted.
    _write(tmp_path, "platforms/android/build.gradle", "buildscript {}")
    _write(tmp_path, "platforms/android/src/main/java/A.java", "class A {}")
    assert detect_build_system(tmp_path) == (
        "gradle", ["platforms/android/build.gradle"])


def test_platform_wrapper_does_not_hide_backend_jvm_source(tmp_path: Path) -> None:
    _flutter_app(tmp_path, "mobile/")
    _write(tmp_path, "backend/build.gradle.kts", "plugins {}")
    _write(tmp_path, "backend/src/main/kotlin/demo/App.kt", "fun main() {}")
    result = scan_jvm_capabilities(tmp_path, mvn_on_path=False)
    assert result["build_system"] == "gradle"
    assert result["build_files"] == ["backend/build.gradle.kts"]
    assert {k: v["state"] for k, v in result["capabilities"].items()} == {
        c: "honest_degrade" for c in
        ("liveness", "module_graph", "linting", "modernization")}


def test_platform_wrapper_scan_liveness_has_no_java_tool(tmp_path: Path) -> None:
    _flutter_app(tmp_path)
    result = scan_liveness(tmp_path, run_dead_code=False)
    assert "jvm_capabilities" not in result
    assert not [t for t in result["dead_code"]["tools"]
                if t.get("language") == "java"]


# ── plugin crediting ───────────────────────────────────────────────────────

def test_configured_checkstyle_is_credited() -> None:
    served = detect_configured_plugins(FIXTURE, ["pom.xml"])
    assert served.get("linting") == ["Checkstyle"]
    # Nothing configures modernization in the fixture.
    assert "modernization" not in served


def test_error_prone_compiler_arg_credits_linting(tmp_path: Path) -> None:
    _write(tmp_path, "pom.xml",
           "<project><build><plugins><plugin>"
           "<artifactId>maven-compiler-plugin</artifactId>"
           "<configuration><annotationProcessorPaths><path>"
           "<artifactId>error_prone_core</artifactId></path>"
           "</annotationProcessorPaths></configuration>"
           "</plugin></plugins></build></project>")
    served = detect_configured_plugins(tmp_path, ["pom.xml"])
    assert served.get("linting") == ["error-prone"]


# ── capability scan: states ────────────────────────────────────────────────

def test_maven_liveness_offers_run_consent_when_mvn_present() -> None:
    result = scan_jvm_capabilities(FIXTURE, mvn_on_path=True)
    liveness = result["capabilities"]["liveness"]
    assert liveness["state"] == "offer"
    assert liveness["consent"] == "run"
    assert liveness["candidate_tool"] == "mvn dependency:analyze"


def test_maven_liveness_offers_install_consent_when_mvn_absent() -> None:
    result = scan_jvm_capabilities(FIXTURE, mvn_on_path=False)
    liveness = result["capabilities"]["liveness"]
    assert liveness["state"] == "offer"
    assert liveness["consent"] == "install"


def test_served_liveness_feeds_candidates() -> None:
    result = scan_jvm_capabilities(
        FIXTURE, mvn_on_path=True, analyze_output=_analyze_output())
    liveness = result["capabilities"]["liveness"]
    assert liveness["state"] == "served"
    assert liveness["candidate_count"] == 2
    assert liveness["used_undeclared_count"] == 1


def test_linting_credited_module_graph_and_modernization_degrade() -> None:
    result = scan_jvm_capabilities(FIXTURE, mvn_on_path=True)
    caps = result["capabilities"]
    assert caps["linting"]["state"] == "credited"
    assert caps["linting"]["served_by"] == ["Checkstyle"]
    # Honest-degrade is a deliverable: state set AND a candidate tool named.
    assert caps["module_graph"]["state"] == "honest_degrade"
    assert caps["module_graph"]["candidate_tool"] == "jdeps"
    assert caps["modernization"]["state"] == "honest_degrade"
    assert caps["modernization"]["candidate_tool"] == "OpenRewrite"


def test_every_capability_names_a_candidate_tool() -> None:
    # No capability may be silently absent - each carries a candidate.
    caps = scan_jvm_capabilities(FIXTURE, mvn_on_path=True)["capabilities"]
    for name, cap in caps.items():
        assert cap.get("candidate_tool"), f"{name} has no candidate tool"
        assert cap.get("gloss"), f"{name} has no gloss"


def test_gradle_honest_degrades_liveness(tmp_path: Path) -> None:
    _write(tmp_path, "build.gradle", "plugins { id 'java' }")
    _write(tmp_path, "src/Main.java", "class Main {}")
    result = scan_jvm_capabilities(tmp_path, mvn_on_path=True)
    assert result["build_system"] == "gradle"
    liveness = result["capabilities"]["liveness"]
    assert liveness["state"] == "honest_degrade"
    assert liveness["candidate_tool"] == "mvn dependency:analyze"


def test_non_jvm_repo_reports_unavailable(tmp_path: Path) -> None:
    _write(tmp_path, "main.py", "x = 1")
    result = scan_jvm_capabilities(tmp_path)
    assert result == {"available": False, "build_system": None, "build_files": []}


# ── integration: scan_liveness merges JVM signal ───────────────────────────

def test_scan_liveness_attaches_jvm_block_and_offer_tool() -> None:
    result = scan_liveness(FIXTURE, run_dead_code=False)
    assert "jvm_capabilities" in result
    java_tools = [t for t in result["dead_code"]["tools"]
                  if t.get("language") == "java"]
    assert len(java_tools) == 1
    assert java_tools[0]["status"] == "available_not_run"
    assert java_tools[0]["consent"] in {"run", "install"}


def test_scan_liveness_served_merges_candidates_into_dead_code(monkeypatch) -> None:
    # Drive the served path by stubbing the analyze run so dead_code carries the
    # Maven candidates the runtime block (static_reachability) consumes.
    import lib.jvm_capabilities as jc

    monkeypatch.setattr(jc, "_run_dependency_analyze",
                        lambda root: _analyze_output())
    monkeypatch.setattr(jc.shutil, "which", lambda _: "/usr/bin/mvn")
    result = scan_liveness(FIXTURE, run_dead_code=False, run_build_tools=True)
    dc = result["dead_code"]
    java_candidates = [c for c in dc["candidates"]
                       if c["kind"] == "unused declared dependency"]
    assert len(java_candidates) == 2
    assert dc["available"] is True


def test_non_jvm_scan_liveness_has_no_jvm_key(tmp_path: Path) -> None:
    _write(tmp_path, "main.py", "x = 1")
    result = scan_liveness(tmp_path, run_dead_code=False)
    assert "jvm_capabilities" not in result
