"""Tests that pin behaviour beyond coverage, as evidence on the tests-run-in-CI condition (issue #855):
``lib.testing.behaviour_pins``, ``lib.testing.behaviour_pins_manifests`` and ``lib.scoring.condition_evidence``."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import lib.scoring.level_tally as lt
from lib.counts_only.content_free import counts_only
from lib.testing import behaviour_pins as bp
from lib.testing.behaviour_pins import PRACTICE_LABELS, pins_or_unknown, scan_behaviour_pins
from lib.testing.behaviour_pins_manifests import cargo_names, gradle_groups, maven_groups, python_names

CI = "layer-5-ci.1"
COVERAGE = "layer-6-coverage.1"

FUZZ_CRATE = ('[package]\nname = "parser-fuzz"\nversion = "0.0.0"\n\n[package.metadata]\ncargo-fuzz = true\n\n'
              '[dependencies]\nlibfuzzer-sys = "0.4"\n')
NIGHTLY = ("name: nightly\non:\n  schedule:\n    - cron: '0 3 * * *'\njobs:\n  run:\n    runs-on: ubuntu-latest\n"
           "    steps:\n      - uses: actions/checkout@v4\n      - run: cargo +nightly fuzz run parse -- -max_total_time=600\n")


def _repo(root: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


def _block(root: Path) -> dict[str, Any]:
    return dict(scan_behaviour_pins(root))


def _arms(root: Path) -> list[tuple[str, str, str]]:
    return [(p["practice"], p["arm"], p["path"]) for p in scan_behaviour_pins(root)["pins"]]


def _workflow(on: str, run: str, name: str = "checks") -> str:
    return (f"name: {name}\non: {on}\njobs:\n  run:\n    runs-on: ubuntu-latest\n    steps:\n"
            f"      - uses: actions/checkout@v4\n      - run: {run}\n")


def _level(pins: Any, ci_runs_tests: bool | None) -> dict[str, Any]:
    data: dict[str, Any] = {"ci_checks": {"enforced_check_runs": ci_runs_tests},
                            "coverage_gate": {"enforced_meets_minimum": False}}
    if pins is not None:
        data["behaviour_pins"] = pins
    return dict(lt.level_result(lt.default_model(), data))


def _cond(level: dict[str, Any], cid: str) -> dict[str, Any]:
    return next(c for c in level["conditions"] if c["id"] == cid)


# --- criterion 1: a fuzz crate plus a nightly schedule: scheduled fuzzing, ranked first ---

def test_a_fuzz_crate_and_a_nightly_schedule_read_scheduled_fuzzing_first(tmp_path: Path) -> None:
    root = _repo(tmp_path, {"fuzz/Cargo.toml": FUZZ_CRATE, ".github/workflows/nightly.yml": NIGHTLY,
                            "pyproject.toml": '[project]\nname = "x"\ndependencies = ["hypothesis>=6"]\n'})
    block = _block(root)
    assert block["practices"] == ["scheduled_fuzzing", "fuzz_targets", "property_tests"]
    assert _arms(root)[:2] == [("scheduled_fuzzing", "scheduled-workflow", ".github/workflows/nightly.yml"),
                               ("fuzz_targets", "libfuzzer-sys", "fuzz/Cargo.toml")]
    assert PRACTICE_LABELS[block["practices"][0]] == "scheduled fuzzing"
    level = _level(block, True)
    assert _cond(level, CI)["evidence"][0] == "scheduled_fuzzing"


@pytest.mark.parametrize("files", [
    {".github/workflows/daily_fuzz.yaml": _workflow("\n  schedule:\n    - cron: '0 0 * * *'", "cargo fuzz run x")},
    {".github/workflows/w.yml": _workflow("[schedule]", "go test ./parse -run=^$ -fuzz=FuzzParse -fuzztime=5m")},
    {".github/workflows/w.yml": _workflow("[schedule, push]", "uvx --from ./python/py-fuzzer fuzz --bin x 0-500")},
    {".github/workflows/w.yml": _workflow("[schedule]", "pytest -m fuzz")},
    {".github/workflows/w.yml": _workflow("[schedule]", "pytest -k=fuzz tests")},
    {".github/workflows/w.yml": _workflow("[schedule]", "bash fuzz.sh")},
    {".github/workflows/w.yml": _workflow("[schedule]", "bash scripts/nightly.sh"),
     "scripts/nightly.sh": "#!/bin/sh\nset -e\ncargo fuzz run parse\n"},
    {".github/workflows/w.yml": "name: n\non: [schedule]\njobs:\n  f:\n    runs-on: ubuntu-latest\n    steps:\n"
                                "      - uses: google/oss-fuzz/infra/cifuzz/actions/run_fuzzers@master\n"},
])
def test_each_form_of_a_scheduled_fuzz_workflow_counts(tmp_path: Path, files: dict[str, str]) -> None:
    assert _block(_repo(tmp_path, files))["practices"] == ["scheduled_fuzzing"]


@pytest.mark.parametrize("files", [
    {".github/workflows/fuzz.yml": _workflow("[push, pull_request]", "cargo fuzz run parse")},
    {".github/workflows/w.yml": _workflow("[schedule]", "cargo install cargo-fuzz")},
    {".github/workflows/w.yml": _workflow("[schedule]", "echo fuzz later")},
    {".github/workflows/w.yml": _workflow("[schedule]", "cargo fuzz run parse || true")},
    {".github/workflows/w.yml": _workflow("[schedule]", "make test --no-fuzz")},
    {".github/workflows/w.yml": _workflow("[schedule]", "make nightly")},
    {".github/workflows/w.yml": _workflow("[schedule]", "cargo test --workspace --exclude fuzz")},
    {".github/workflows/w.yml": _workflow("[schedule]", "pytest --ignore=tests/fuzz")},
    {".github/workflows/w.yml": _workflow("[schedule]", 'pytest -k "not fuzz"')},
    {".github/workflows/w.yml": _workflow("[schedule]", "pytest --deselect tests/fuzz/test_parse.py")},
    {".github/workflows/w.yml": _workflow("[schedule]", "cd fuzz && make")},
    {".github/workflows/fuzz.yml": _workflow("[schedule]", "cargo install cargo-fuzz", name="Fuzz")},
    {".github/workflows/w.yml": _workflow("[schedule]", "mkdir -p fuzz")},
    {".github/workflows/w.yml": _workflow("[schedule]", "bash vendor/nightly.sh"),
     "vendor/nightly.sh": "#!/bin/sh\ncargo fuzz run parse\n"},
])
def test_a_fuzz_run_off_schedule_an_install_an_echo_or_a_masked_run_does_not_count(
        tmp_path: Path, files: dict[str, str]) -> None:
    assert _block(_repo(tmp_path, files))["practices"] == []


# --- criterion 2: pytest-socket reads network-isolated tests ---

def test_a_pyproject_declaring_pytest_socket_reads_network_isolated_tests(tmp_path: Path) -> None:
    root = _repo(tmp_path, {"pyproject.toml": '[project]\nname = "x"\n\n[dependency-groups]\n'
                                              'test = ["pytest>=8", "pytest_socket==0.7"]\n'})
    block = _block(root)
    assert block["practices"] == ["network_isolated_tests"]
    assert _arms(root) == [("network_isolated_tests", "pytest-socket", "pyproject.toml")]
    assert PRACTICE_LABELS["network_isolated_tests"] == "network-isolated tests"


@pytest.mark.parametrize(("rel", "text", "names"), [
    ("pyproject.toml", '[project.optional-dependencies]\ntest = ["vcrpy", "syrupy[x]>=4"]\n', {"vcrpy", "syrupy"}),
    ("pyproject.toml", '[tool.poetry.group.dev.dependencies]\nhypothesis = "^6"\n', {"hypothesis"}),
    ("pyproject.toml", '[tool.hatch.envs.test]\ndependencies = ["pytest-recording"]\n', {"pytest-recording"}),
    ("pyproject.toml", '[tool.uv]\ndev-dependencies = ["Hypothesis"]\n', {"hypothesis"}),
    ("pyproject.toml", "not [valid toml", set()),
    ("setup.cfg", "[options]\ninstall_requires =\n    attrs\n[options.extras_require]\ntest =\n    hypothesis\n",
     {"attrs", "hypothesis"}),
    ("requirements-dev.txt", "# hypothesis is not here\n-r base.txt\npytest-socket==0.7  # isolate\n",
     {"pytest-socket"}),
    ("requirements/test.txt", "syrupy\n", {"syrupy"}),
])
def test_python_declarations_are_parsed_not_matched(rel: str, text: str, names: set[str]) -> None:
    assert python_names(rel, text) == names


# --- criterion 3: the word install is not insta; vendor and testdata are never read ---

def test_a_file_saying_install_and_no_insta_dependency_reads_nothing(tmp_path: Path) -> None:
    root = _repo(tmp_path, {
        "Cargo.toml": '[package]\nname = "x"\n# run cargo install to set up; insta is not used\n'
                      '[dependencies]\ninstall = "1"\ninstallation = "1"\n',
        "README.md": "Run `cargo install insta` and `pip install hypothesis`.\n",
        "requirements.txt": "installer\n",
        "package.json": '{"scripts": {"test": "fast-check"}, "description": "install fast-check"}\n',
    })
    assert _block(root)["practices"] == []
    assert cargo_names('[dev-dependencies]\nsnap = { package = "insta", version = "1" }\n') == {"insta"}


def test_an_optional_arbitrary_dependency_is_a_feature_not_a_fuzz_target(tmp_path: Path) -> None:
    root = _repo(tmp_path, {"Cargo.toml": '[dependencies]\narbitrary = { version = "1", optional = true }\n'})
    assert _block(root)["practices"] == []
    root = _repo(tmp_path, {"Cargo.toml": '[dependencies]\narbitrary = { version = "1", features = ["derive"] }\n'})
    assert _block(root)["practices"] == ["fuzz_targets"]


@pytest.mark.parametrize("rel", ["vendor/x/Cargo.toml", "third_party/x/Cargo.toml", "pkg/testdata/Cargo.toml",
                                 "tests/fixtures/app/Cargo.toml", "web/node_modules/x/Cargo.toml"])
def test_a_match_under_vendor_testdata_or_fixtures_reads_nothing(tmp_path: Path, rel: str) -> None:
    root = _repo(tmp_path, {rel: '[dev-dependencies]\ninsta = "1"\n'})
    assert _block(root)["practices"] == []


def test_a_match_beside_an_excluded_copy_names_the_real_file(tmp_path: Path) -> None:
    root = _repo(tmp_path, {"vendor/a/Cargo.toml": '[dev-dependencies]\ninsta = "1"\n',
                            "crates/core/Cargo.toml": '[dev-dependencies]\ninsta = "1"\n'})
    assert _arms(root) == [("snapshot_tests", "insta", "crates/core/Cargo.toml")]


# --- criterion 4: no evidence while tests-run-in-CI is unmet; tally and coverage gate unchanged ---

@pytest.mark.parametrize("ci_runs_tests", [False, None])
def test_with_tests_run_in_ci_not_met_no_evidence_attaches(tmp_path: Path, ci_runs_tests: bool | None) -> None:
    pins = scan_behaviour_pins(_repo(tmp_path, {"fuzz/Cargo.toml": FUZZ_CRATE}))
    level = _level(pins, ci_runs_tests)
    assert not any("evidence" in c for c in level["conditions"])


@pytest.mark.parametrize("ci_runs_tests", [True, False])
def test_the_tally_and_the_coverage_gate_are_unchanged_by_evidence(tmp_path: Path, ci_runs_tests: bool) -> None:
    pins = scan_behaviour_pins(_repo(tmp_path, {"fuzz/Cargo.toml": FUZZ_CRATE, ".github/workflows/n.yml": NIGHTLY}))
    with_pins, without = _level(pins, ci_runs_tests), _level(None, ci_runs_tests)
    for key in ("tally", "counted", "listed", "not_measured", "step", "next_step", "in_place", "tags"):
        assert with_pins[key] == without[key]
    assert _cond(with_pins, COVERAGE) == _cond(without, COVERAGE)
    assert _cond(with_pins, COVERAGE)["state"] == "unmet"
    stripped = [{k: v for k, v in c.items() if k != "evidence"} for c in with_pins["conditions"]]
    assert stripped == without["conditions"]
    assert ("evidence" in _cond(with_pins, CI)) is ci_runs_tests


@pytest.mark.parametrize("pins", [
    {"available": False, "reason": "scan_failed", "files_read": 0, "practices": [], "pins": []},
    {"available": True, "files_read": 1, "practices": [], "pins": []},
    {"available": True, "practices": "scheduled_fuzzing"},
    "not a block",
])
def test_an_empty_unavailable_or_malformed_block_attaches_nothing(pins: Any) -> None:
    assert "evidence" not in _cond(_level(pins, True), CI)


def test_a_reader_fault_reads_unavailable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(_root: Path) -> frozenset[str]:
        raise OSError("x")
    monkeypatch.setattr(bp, "repo_files", boom)
    assert pins_or_unknown(tmp_path) == {"available": False, "reason": "scan_failed", "files_read": 0,
                                         "practices": [], "pins": []}


# --- the other arms ---

@pytest.mark.parametrize(("files", "practices"), [
    ({"parse/parse_test.go": 'package parse\n\nimport "testing"\n\nfunc FuzzParse(f *testing.F) {\n}\n'},
     ["fuzz_targets"]),
    ({"parse/parse_test.go": "package parse\n/*\nfunc FuzzOld(f *testing.F) {}\n*/\n"
                             "var s = `\nfunc FuzzStr(f *testing.F) {}\n`\nfunc TestFuzzy(t *testing.T) {}\n"},
     []),
    ({"parse/parse.go": "package parse\nfunc FuzzParse(f *testing.F) {}\n"}, []),
    ({"parse/parse_test.go": 'package parse\nvar a = "/*"\nfunc FuzzParse(f *testing.F) {}\nvar b = "*/"\n'},
     ["fuzz_targets"]),
    ({"build.gradle": 'tasks.register("check") {} // testImplementation("net.jqwik:jqwik:1.8.0")\n'}, []),
    ({"build.gradle": 'repositories { maven { url "https://repo.example/x" } }\n'
                      'dependencies { testImplementation "net.jqwik:jqwik:1.8.0" }\n'}, ["property_tests"]),
    ({"package.json": '{"devDependencies": {"fast-check": "^3"}}'}, ["property_tests"]),
    ({"package.json": '{"devDependencies": {"@fast-check/vitest": "^0.1"}}'}, ["property_tests"]),
    ({"package.json": "[1, 2]"}, []),
    ({"Cargo.toml": '[target.\'cfg(unix)\'.dev-dependencies]\nproptest = "1"\nquick_check = "1"\n'},
     ["property_tests"]),
    ({"pom.xml": "<project xmlns='http://maven.apache.org/POM/4.0.0'><dependencies><dependency>"
                 "<groupId>com.approvaltests</groupId><artifactId>approvaltests</artifactId>"
                 "</dependency></dependencies></project>"}, ["snapshot_tests"]),
    ({"pom.xml": "<project><!-- net.jqwik --><name>net.jqwik</name></project>"}, []),
    ({"pom.xml": "<project><dependencyManagement><dependencies><dependency><groupId>net.jqwik</groupId>"
                 "</dependency></dependencies></dependencyManagement></project>"}, []),
    ({"build.gradle.kts": 'dependencies {\n    testImplementation("net.jqwik:jqwik:1.8.0")\n}\n'}, ["property_tests"]),
    ({"build.gradle": "dependencies {\n    // testImplementation 'net.jqwik:jqwik:1.8.0'\n}\n"}, []),
    ({"gradle/libs.versions.toml": '[libraries]\njqwik = { module = "net.jqwik:jqwik", version = "1.8" }\n'},
     ["property_tests"]),
])
def test_each_other_arm_reads_its_declaration(tmp_path: Path, files: dict[str, str], practices: list[str]) -> None:
    assert _block(_repo(tmp_path, files))["practices"] == practices


def test_the_jvm_readers_fail_closed_on_text_that_does_not_parse() -> None:
    assert maven_groups("<project><dependency>") == set()
    assert gradle_groups("libs.versions.toml", "[libraries\n") == set()
    assert gradle_groups("build.gradle", "implementation group: 'net.jqwik', name: 'jqwik'") == {"net.jqwik"}


# --- known not detected (re-probe on #855): a repository's own harness ---

def test_a_custom_screenshot_harness_and_test_infrastructure_are_known_not_detected(tmp_path: Path) -> None:
    root = _repo(tmp_path, {
        ".github/workflows/screenshots.yml": _workflow("[pull_request]", "npm run test:component-screenshots"),
        "package.json": '{"scripts": {"test:component-screenshots": "node test/screenshots.js"}}',
        "test-infra/pom.xml": "<project><dependencies><dependency><groupId>org.example.infra</groupId>"
                              "</dependency></dependencies></project>",
    })
    assert _block(root)["practices"] == []


# --- counts-only ---

def test_counts_only_keeps_every_field(tmp_path: Path) -> None:
    block = scan_behaviour_pins(_repo(tmp_path, {"fuzz/Cargo.toml": FUZZ_CRATE, ".github/workflows/n.yml": NIGHTLY}))
    kept = counts_only({"behaviour_pins": block})["behaviour_pins"]
    assert kept == dict(block)
    level = counts_only({"level": _level(block, True)})["level"]
    assert _cond(level, CI)["evidence"] == ["scheduled_fuzzing", "fuzz_targets"]


def test_counts_only_keeps_the_reason() -> None:
    block = {"available": False, "reason": "scan_failed", "files_read": 0, "practices": [], "pins": []}
    assert counts_only({"behaviour_pins": block})["behaviour_pins"]["reason"] == "scan_failed"
