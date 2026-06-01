"""Signal-consumption tests for the capability-driven JVM offer flow (#113).

The CI contract is *signal consumption*: given a tool's output (canned
``mvn dependency:analyze`` text), the deterministic core feeds the scorecard
correctly. The agent's runtime tool *choice* is human-judged and not tested
here. A synthetic Maven fixture under ``tests/fixtures/maven_project/`` stands in
for the real Helidon repo CI cannot reach.
"""
from __future__ import annotations

from pathlib import Path

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
    _write(tmp_path, "tests/fixtures/sample/pom.xml", "<project/>")
    system, _ = detect_build_system(tmp_path)
    assert system is None


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
