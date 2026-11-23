"""Contract suite for the coverage-gate detector.

``detect_coverage_gate`` reports whether a line-coverage threshold is configured,
where (file and 1-based line) and the threshold exactly as written. These tests
pin one small fixture per detected form, the absent case, and the honesty rules:
a measured-but-ungated repo reads ``configured: false``, a JaCoCo ratio is never
rescaled, and ``fail_under = 0`` is reported but gates nothing.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from lib.testing.coverage_gate import GO_RUST_UNMEASURED, detect_coverage_gate


def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _only_gate(root: Path) -> dict:
    block = detect_coverage_gate(root)
    assert block["available"] is True
    assert len(block["gates"]) == 1, block["gates"]
    return block["gates"][0]


# --- absent ---------------------------------------------------------------

def test_no_config_reports_not_enforced(tmp_path: Path) -> None:
    block = detect_coverage_gate(tmp_path)
    assert block["configured"] is False
    assert block["gates"] == []
    assert any("Go" in form for form in block["not_detected"])


def test_measurement_alone_is_not_a_gate(tmp_path: Path) -> None:
    """A coverage report and a ``--cov`` run with no threshold gate nothing."""
    _write(tmp_path, "coverage.xml", '<coverage line-rate="0.9"/>')
    _write(tmp_path, ".github/workflows/ci.yml",
           "jobs:\n  t:\n    steps:\n      - run: pytest --cov=src --cov-report=xml\n")
    _write(tmp_path, "pyproject.toml",
           "[tool.coverage.run]\nbranch = true\n[tool.coverage.report]\nshow_missing = true\n")
    block = detect_coverage_gate(tmp_path)
    assert block["configured"] is False
    assert block["gates"] == []


# --- Python ---------------------------------------------------------------

def test_pyproject_fail_under(tmp_path: Path) -> None:
    _write(tmp_path, "pyproject.toml",
           "[project]\nname = 'x'\n\n[tool.coverage.report]\nfail_under = 85\n")
    gate = _only_gate(tmp_path)
    assert gate == {"file": "pyproject.toml", "line": 5, "tool": "coverage.py",
                    "form": "[tool.coverage.report] fail_under",
                    "threshold": 85.0, "unit": "percent", "threshold_percent": 85.0,
                    "metric": "lines"}
    assert detect_coverage_gate(tmp_path)["configured"] is True


def test_section_header_with_trailing_comment(tmp_path: Path) -> None:
    _write(tmp_path, "pyproject.toml",
           "[tool.coverage.report]  # CI gate\nfail_under = 85\n")
    assert _only_gate(tmp_path)["threshold"] == 85.0


def test_fail_under_outside_report_section_is_ignored(tmp_path: Path) -> None:
    _write(tmp_path, "pyproject.toml", "[tool.other]\nfail_under = 85\n")
    assert detect_coverage_gate(tmp_path)["gates"] == []


def test_coveragerc_report_section(tmp_path: Path) -> None:
    _write(tmp_path, ".coveragerc", "[run]\nbranch = True\n\n[report]\nfail_under = 72.5\n")
    gate = _only_gate(tmp_path)
    assert (gate["file"], gate["line"], gate["threshold"]) == (".coveragerc", 5, 72.5)


def test_setup_cfg_coverage_report_section(tmp_path: Path) -> None:
    _write(tmp_path, "setup.cfg", "[metadata]\nname = x\n[coverage:report]\nfail_under: 90\n")
    gate = _only_gate(tmp_path)
    assert (gate["file"], gate["line"], gate["threshold"]) == ("setup.cfg", 4, 90.0)


def test_tox_ini_coverage_report_section(tmp_path: Path) -> None:
    _write(tmp_path, "tox.ini", "[tox]\nenvlist = py311\n[coverage:report]\nfail_under = 80\n")
    gate = _only_gate(tmp_path)
    assert (gate["file"], gate["line"], gate["threshold"]) == ("tox.ini", 4, 80.0)


def test_nested_package_pyproject_is_found(tmp_path: Path) -> None:
    """A monorepo package's own pyproject counts; the path names the package."""
    _write(tmp_path, "packages/core/pyproject.toml",
           "[tool.coverage.report]\nfail_under = 70\n")
    assert _only_gate(tmp_path)["file"] == "packages/core/pyproject.toml"


def test_fail_under_zero_is_reported_but_not_enforced(tmp_path: Path) -> None:
    _write(tmp_path, ".coveragerc", "[report]\nfail_under = 0\n")
    block = detect_coverage_gate(tmp_path)
    assert block["gates"][0]["threshold"] == 0.0
    assert block["configured"] is False


def test_ci_workflow_cov_fail_under(tmp_path: Path) -> None:
    _write(tmp_path, ".github/workflows/tests.yml",
           "jobs:\n  test:\n    steps:\n"
           "      - run: pytest --cov=src --cov-fail-under=88 -q\n")
    gate = _only_gate(tmp_path)
    assert gate == {"file": ".github/workflows/tests.yml", "line": 4,
                    "tool": "pytest-cov", "form": "--cov-fail-under",
                    "threshold": 88.0, "unit": "percent", "threshold_percent": 88.0,
                    "metric": "lines"}


def test_commented_ci_line_is_ignored(tmp_path: Path) -> None:
    _write(tmp_path, ".github/workflows/tests.yml",
           "jobs:\n  test:\n    steps:\n      # - run: pytest --cov-fail-under=88\n")
    assert detect_coverage_gate(tmp_path)["gates"] == []


def test_pytest_addopts_cov_fail_under(tmp_path: Path) -> None:
    _write(tmp_path, "pytest.ini", "[pytest]\naddopts = --cov=pkg --cov-fail-under 60\n")
    gate = _only_gate(tmp_path)
    assert (gate["file"], gate["line"], gate["threshold"]) == ("pytest.ini", 2, 60.0)


def test_coverage_report_cli_fail_under(tmp_path: Path) -> None:
    _write(tmp_path, "Makefile", "cov:\n\tcoverage report --fail-under=75\n")
    gate = _only_gate(tmp_path)
    assert (gate["tool"], gate["line"], gate["threshold"]) == ("coverage.py", 2, 75.0)


def test_coverage_xml_cli_fail_under(tmp_path: Path) -> None:
    _write(tmp_path, ".github/workflows/ci.yml",
           "jobs:\n  t:\n    steps:\n      - run: coverage xml --fail-under=82\n")
    gate = _only_gate(tmp_path)
    assert (gate["form"], gate["threshold"]) == ("coverage xml --fail-under", 82.0)


def test_composite_action_and_circleci_are_read(tmp_path: Path) -> None:
    _write(tmp_path, ".github/actions/test/action.yml",
           "runs:\n  steps:\n    - run: pytest --cov-fail-under=70\n")
    _write(tmp_path, ".circleci/config.yml",
           "jobs:\n  t:\n    steps:\n      - run: pytest --cov-fail-under=71\n")
    files = {g["file"] for g in detect_coverage_gate(tmp_path)["gates"]}
    assert files == {".github/actions/test/action.yml", ".circleci/config.yml"}


# --- JS / TS --------------------------------------------------------------

def test_jest_config_coverage_threshold(tmp_path: Path) -> None:
    _write(tmp_path, "jest.config.js",
           "module.exports = {\n  coverageThreshold: {\n    global: {\n"
           "      branches: 70,\n      lines: 80,\n    },\n  },\n};\n")
    gate = _only_gate(tmp_path)
    assert (gate["file"], gate["line"], gate["tool"]) == ("jest.config.js", 2, "jest")
    assert (gate["threshold"], gate["metric"], gate["unit"]) == (80.0, "lines", "percent")


def test_jest_negative_threshold_is_an_uncovered_count(tmp_path: Path) -> None:
    _write(tmp_path, "jest.config.ts",
           "export default {\n  coverageThreshold: { global: { lines: -10 } },\n};\n")
    gate = _only_gate(tmp_path)
    assert (gate["threshold"], gate["unit"]) == (-10.0, "uncovered_count")


def test_package_json_jest_coverage_threshold(tmp_path: Path) -> None:
    _write(tmp_path, "package.json",
           '{\n  "name": "x",\n  "jest": {\n    "coverageThreshold": {\n'
           '      "global": { "lines": 90 }\n    }\n  }\n}\n')
    gate = _only_gate(tmp_path)
    assert (gate["file"], gate["line"], gate["threshold"]) == ("package.json", 4, 90.0)


def test_unread_jest_threshold_is_reported_but_not_enforced(tmp_path: Path) -> None:
    """An empty ``coverageThreshold`` gates nothing; nyc's null default does."""
    _write(tmp_path, "jest.config.js", "module.exports = { coverageThreshold: {} };\n")
    block = detect_coverage_gate(tmp_path)
    assert block["gates"][0]["threshold"] is None
    assert block["configured"] is False

    _write(tmp_path, ".nycrc", '{ "check-coverage": true }\n')
    assert detect_coverage_gate(tmp_path)["configured"] is True


def test_vitest_thresholds(tmp_path: Path) -> None:
    _write(tmp_path, "vitest.config.ts",
           "export default defineConfig({\n  test: {\n    coverage: {\n"
           "      provider: 'v8',\n      thresholds: {\n        lines: 85,\n"
           "        functions: 80,\n      },\n    },\n  },\n});\n")
    gate = _only_gate(tmp_path)
    assert (gate["tool"], gate["line"], gate["threshold"]) == ("vitest", 5, 85.0)


def test_nycrc_check_coverage(tmp_path: Path) -> None:
    _write(tmp_path, ".nycrc.json",
           '{\n  "lines": 75,\n  "check-coverage": true\n}\n')
    gate = _only_gate(tmp_path)
    assert (gate["tool"], gate["line"], gate["threshold"]) == ("nyc", 3, 75.0)


def test_c8_cli_check_coverage(tmp_path: Path) -> None:
    _write(tmp_path, "package.json",
           '{\n  "scripts": {\n    "test": "c8 --check-coverage --lines 95 node --test"\n  }\n}\n')
    gate = _only_gate(tmp_path)
    assert (gate["tool"], gate["line"], gate["threshold"]) == ("c8", 3, 95.0)


def test_nyc_check_coverage_subcommand(tmp_path: Path) -> None:
    _write(tmp_path, "package.json",
           '{\n  "scripts": {\n    "posttest": "nyc check-coverage --lines 90"\n  }\n}\n')
    gate = _only_gate(tmp_path)
    assert (gate["tool"], gate["line"], gate["threshold"]) == ("nyc", 3, 90.0)


# --- JVM ------------------------------------------------------------------

def test_pom_jacoco_minimum_keeps_ratio(tmp_path: Path) -> None:
    _write(tmp_path, "pom.xml",
           "<project>\n  <plugin>\n    <artifactId>jacoco-maven-plugin</artifactId>\n"
           "    <rules><rule>\n      <limits><limit>\n"
           "        <counter>LINE</counter>\n        <value>COVEREDRATIO</value>\n"
           "        <minimum>0.80</minimum>\n      </limit></limits>\n"
           "    </rule></rules>\n  </plugin>\n</project>\n")
    gate = _only_gate(tmp_path)
    assert gate == {"file": "pom.xml", "line": 8, "tool": "jacoco",
                    "form": "<rules> <minimum>", "threshold": 0.8,
                    "unit": "ratio", "threshold_percent": 80.0, "metric": "line"}


def test_pom_jacoco_percent_minimum_is_percent(tmp_path: Path) -> None:
    """``<minimum>1%</minimum>`` is one percent, not a ratio of 1."""
    _write(tmp_path, "pom.xml",
           "<project>\n  <artifactId>jacoco-maven-plugin</artifactId>\n"
           "  <minimum>1%</minimum>\n</project>\n")
    gate = _only_gate(tmp_path)
    assert (gate["threshold"], gate["unit"]) == (1.0, "percent")


def test_pom_minimum_without_jacoco_is_ignored(tmp_path: Path) -> None:
    _write(tmp_path, "pom.xml", "<project>\n  <minimum>0.80</minimum>\n</project>\n")
    assert detect_coverage_gate(tmp_path)["gates"] == []


def test_gradle_jacoco_verification_minimum(tmp_path: Path) -> None:
    _write(tmp_path, "build.gradle.kts",
           'plugins { jacoco }\ntasks.jacocoTestCoverageVerification {\n'
           '  violationRules {\n    rule {\n      limit {\n'
           '        counter = "LINE"\n        minimum = BigDecimal("0.75")\n'
           '      }\n    }\n  }\n}\n')
    gate = _only_gate(tmp_path)
    assert (gate["file"], gate["line"], gate["threshold"], gate["metric"]) == (
        "build.gradle.kts", 7, 0.75, "line")


# --- scope ----------------------------------------------------------------

def test_excluded_dirs_are_not_scanned(tmp_path: Path) -> None:
    _write(tmp_path, "node_modules/dep/jest.config.js",
           "module.exports = { coverageThreshold: { global: { lines: 99 } } };\n")
    _write(tmp_path, "tests/fixtures/pyproject.toml",
           "[tool.coverage.report]\nfail_under = 99\n")
    assert detect_coverage_gate(tmp_path)["gates"] == []


# --- issue #488 -----------------------------------------------------------

def test_scoped_run_keeps_root_ci_and_drops_other_packages(tmp_path: Path) -> None:
    """A scoped run reads the scope plus root CI and task-runner files; a
    package-level config outside the scope, and a user exclude, are dropped."""
    _write(tmp_path, "packages/web/jest.config.js",
           "module.exports = { coverageThreshold: { global: { lines: 80 } } };\n")
    _write(tmp_path, "packages/api/pyproject.toml",
           "[tool.coverage.report]\nfail_under = 90\n")
    _write(tmp_path, "pyproject.toml", "[tool.coverage.report]\nfail_under = 70\n")
    _write(tmp_path, ".github/workflows/ci.yml",
           "jobs:\n  t:\n    steps:\n      - run: pytest --cov-fail-under=88\n")
    _write(tmp_path, "Makefile", "cov:\n\tcoverage report --fail-under=75\n")
    _write(tmp_path, "packages/web/legacy/.nycrc", '{ "check-coverage": true }\n')

    block = detect_coverage_gate(tmp_path, scope=tmp_path / "packages/web",
                                 excludes=({"legacy"}, []))
    files = sorted(g["file"] for g in block["gates"])
    assert files == [".github/workflows/ci.yml", "Makefile",
                     "packages/web/jest.config.js"]

    assert any("targets the scope" in f for f in block["not_detected"])

    unscoped_block = detect_coverage_gate(tmp_path)
    assert not any("targets the scope" in f for f in unscoped_block["not_detected"])
    unscoped = {g["file"] for g in unscoped_block["gates"]}
    assert {"packages/api/pyproject.toml", "pyproject.toml",
            "packages/web/legacy/.nycrc"} <= unscoped


def test_user_exclude_pattern_drops_a_file(tmp_path: Path) -> None:
    _write(tmp_path, "pyproject.toml", "[tool.coverage.report]\nfail_under = 70\n")
    block = detect_coverage_gate(tmp_path, excludes=(set(), ["pyproject.toml"]))
    assert block["gates"] == []


def test_templated_threshold_is_a_gate_with_null_threshold(tmp_path: Path) -> None:
    _write(tmp_path, ".github/workflows/ci.yml",
           "jobs:\n  t:\n    steps:\n"
           "      - run: pytest --cov=src --cov-fail-under=${{ env.COV_MIN }}\n"
           "      - run: coverage report --fail-under=$COV_MIN\n")
    _write(tmp_path, "Makefile", "cov:\n\tpytest --cov-fail-under $(COV_MIN)\n"
           "\tpytest --cov-fail-under=$$COV_MIN\n")
    block = detect_coverage_gate(tmp_path)
    assert sorted((g["file"], g["line"], g["tool"], g["threshold"]) for g in block["gates"]) == [
        (".github/workflows/ci.yml", 4, "pytest-cov", None),
        (".github/workflows/ci.yml", 5, "coverage.py", None),
        ("Makefile", 2, "pytest-cov", None),
        ("Makefile", 3, "pytest-cov", None),
    ]
    assert block["configured"] is True


def test_single_line_c8_config_is_counted_once(tmp_path: Path) -> None:
    _write(tmp_path, "package.json",
           '{\n  "name": "x",\n  "c8": {"check-coverage": true, "lines": 95}\n}\n')
    gate = _only_gate(tmp_path)
    assert (gate["tool"], gate["line"], gate["threshold"]) == ("nyc/c8", 3, 95.0)


def test_jest_project_threshold_does_not_bleed_into_another(tmp_path: Path) -> None:
    _write(tmp_path, "jest.config.js",
           "module.exports = {\n  projects: [\n"
           "    { displayName: 'a', coverageThreshold: { global: {} } },\n"
           "    {\n      displayName: 'b',\n      coverageThreshold: {\n"
           "        global: { lines: 80 },\n      },\n    },\n  ],\n};\n")
    gates = detect_coverage_gate(tmp_path)["gates"]
    assert [(g["line"], g["threshold"]) for g in gates] == [(3, None), (6, 80.0)]


def test_fail_under_with_branch_coverage_is_total(tmp_path: Path) -> None:
    """``branch = true`` in the same config makes ``fail_under`` gate the
    line-and-branch total; without it the metric stays ``lines``."""
    _write(tmp_path, "pyproject.toml",
           "[tool.coverage.run]\nbranch = true\n\n[tool.coverage.report]\nfail_under = 85\n")
    assert _only_gate(tmp_path)["metric"] == "total"
    _write(tmp_path, "pyproject.toml",
           "[tool.coverage.run]\nbranch = false\n[tool.coverage.report]\nfail_under = 85\n")
    assert _only_gate(tmp_path)["metric"] == "lines"


def test_cov_fail_under_with_cov_branch_is_total(tmp_path: Path) -> None:
    _write(tmp_path, ".github/workflows/ci.yml",
           "jobs:\n  t:\n    steps:\n      - run: pytest --cov-branch --cov-fail-under=88\n")
    assert _only_gate(tmp_path)["metric"] == "total"


def test_this_repository_block_is_unchanged() -> None:
    """The repo sets no branch coverage, so its own gates stay ``lines``."""
    repo = Path(__file__).resolve().parents[3]
    gates = [g for g in detect_coverage_gate(repo)["gates"]
             if g["file"] in {"skills/assess/pyproject.toml", "scripts/pyproject.toml"}]
    assert gates and all(g["metric"] == "lines" for g in gates)


# --- the 50 percent minimum (#781) -----------------------------------------

def _pom(minimum: str) -> str:
    return ("<project>\n  <artifactId>jacoco-maven-plugin</artifactId>\n"
            "  <counter>LINE</counter>\n"
            f"  <minimum>{minimum}</minimum>\n</project>\n")


def _gradle(minimum: str) -> str:
    return ('plugins { jacoco }\ntasks.jacocoTestCoverageVerification {\n'
            '  violationRules { rule { limit {\n'
            f'    minimum = {minimum}\n'
            '  } } }\n}\n')


def _fail_under(value: str) -> str:
    return f"[project]\nname = 'x'\n\n[tool.coverage.report]\nfail_under = {value}\n"


def _workflow(run: str) -> str:
    return f"jobs:\n  t:\n    steps:\n      - run: {run}\n"


_TEMPLATED = "pytest --cov=src --cov-fail-under=${{ env.COV_MIN }}"


@pytest.mark.parametrize(("rel", "text", "threshold", "percent"), [
    ("pom.xml", _pom("0.80"), 0.8, 80.0),
    ("pom.xml", _pom("0.55"), 0.55, 55.0),  # 0.55 * 100 is 55.00000000000001 unrounded
    ("pom.xml", _pom("80%"), 80.0, 80.0),
    ("build.gradle", _gradle("0.5"), 0.5, 50.0),
    ("pyproject.toml", _fail_under("49.9"), 49.9, 49.9),
])
def test_threshold_percent_normalises_ratio_and_percent(
        tmp_path: Path, rel: str, text: str, threshold: float, percent: float) -> None:
    _write(tmp_path, rel, text)
    gate = _only_gate(tmp_path)
    assert gate["threshold_percent"] == percent
    assert gate["threshold"] == threshold  # as written, never rescaled


def test_threshold_percent_is_null_for_counts_and_unread_values(tmp_path: Path) -> None:
    _write(tmp_path, "jest.config.js",
           "module.exports = { coverageThreshold: { global: { lines: -10 } } };\n")
    _write(tmp_path, ".github/workflows/ci.yml", _workflow(_TEMPLATED))
    block = detect_coverage_gate(tmp_path)
    assert [(g["unit"], g["threshold_percent"]) for g in block["gates"]] == [
        ("uncovered_count", None), ("percent", None)]
    assert block["max_threshold_percent"] is None


_PY = "pyproject.toml"
_CI = ".github/workflows/ci.yml"


@pytest.mark.parametrize(("files", "expected"), [
    ({_PY: _fail_under("1")}, False),
    ({_PY: _fail_under("49.9")}, False),
    ({_PY: _fail_under("50")}, True),
    ({"pom.xml": _pom("0.80")}, True),
    ({"pom.xml": _pom("0.01")}, False),
    ({"pom.xml": _pom("80%")}, True),
    ({"build.gradle": _gradle("0.5")}, True),
    ({_CI: _workflow(_TEMPLATED)}, None),
    ({"jest.config.js": "module.exports = { coverageThreshold: { global: { lines: -10 } } };\n"},
     None),
    ({_PY: _fail_under("1"), _CI: _workflow("pytest --cov=src --cov-fail-under=85")}, True),
    ({_PY: _fail_under("1"), _CI: _workflow(_TEMPLATED)}, None),
    ({_PY: _fail_under("0")}, False),
    ({"jest.config.js": "module.exports = { coverageThreshold: {} };\n"}, False),
    ({}, False),
], ids=["fail-under-1", "fail-under-49.9", "fail-under-50", "jacoco-0.80", "jacoco-0.01",
        "jacoco-80pct", "gradle-0.5", "templated-only", "jest-uncovered-count",
        "max-wins", "low-plus-templated", "fail-under-0", "jest-empty", "no-gate"])
def test_threshold_meets_minimum(tmp_path: Path, files: dict[str, str],
                                 expected: bool | None) -> None:
    for rel, text in files.items():
        _write(tmp_path, rel, text)
    block = detect_coverage_gate(tmp_path)
    assert block["threshold_meets_minimum"] is expected


def test_max_threshold_percent_is_the_highest_read_percentage(tmp_path: Path) -> None:
    _write(tmp_path, _PY, _fail_under("1"))
    _write(tmp_path, "pom.xml", _pom("0.70"))
    _write(tmp_path, _CI, _workflow(_TEMPLATED))
    assert detect_coverage_gate(tmp_path)["max_threshold_percent"] == 70.0
    (tmp_path / "empty").mkdir()
    assert detect_coverage_gate(tmp_path / "empty")["max_threshold_percent"] is None


# --- Go and Rust (#788) -----------------------------------------------------

_GO_MOD = "module example.com/x\n\ngo 1.22\n"
_CARGO = "[package]\nname = 'x'\nversion = '0.1.0'\nedition = '2021'\n"


def _testcoverage(body: str) -> str:
    return f"profile: cover.out\nthreshold:\n{body}"


@pytest.mark.parametrize(("files", "expected"), [
    ({"go.mod": _GO_MOD, ".testcoverage.yml": _testcoverage("  total: 80\n")},
     ("go-test-coverage", 80.0, "percent", ".testcoverage.yml", 3, "total")),
    ({"go.mod": _GO_MOD, ".testcoverage.yml": _testcoverage("  total: 10\n")},
     ("go-test-coverage", 10.0, "percent", ".testcoverage.yml", 3, "total")),
    ({"Cargo.toml": _CARGO, _CI: _workflow("cargo tarpaulin --fail-under 80")},
     ("cargo-tarpaulin", 80.0, "percent", _CI, 4, "lines")),
    ({"Cargo.toml": _CARGO, _CI: _workflow("cargo llvm-cov --fail-under-lines 70")},
     ("cargo-llvm-cov", 70.0, "percent", _CI, 4, "lines")),
], ids=["go-80", "go-10", "tarpaulin-80", "llvm-cov-70"])
def test_go_and_rust_gate_forms_are_read(tmp_path: Path, files: dict[str, str],
                                         expected: tuple[object, ...]) -> None:
    for rel, text in files.items():
        _write(tmp_path, rel, text)
    gate = _only_gate(tmp_path)
    assert (gate["tool"], gate["threshold"], gate["unit"], gate["file"], gate["line"],
            gate["metric"]) == expected
    assert gate["threshold_percent"] == expected[1]


@pytest.mark.parametrize(("files", "expected"), [
    ({"go.mod": _GO_MOD, ".testcoverage.yml": _testcoverage("  total: 80\n")}, True),
    ({"go.mod": _GO_MOD, ".testcoverage.yml": _testcoverage("  total: 10\n")}, False),
    ({"Cargo.toml": _CARGO, _CI: _workflow("cargo tarpaulin --fail-under 80")}, True),
    ({"Cargo.toml": _CARGO, _CI: _workflow("cargo llvm-cov --fail-under-lines 70")}, True),
    ({"go.mod": _GO_MOD, _PY: _fail_under("85")}, True),
], ids=["go-80", "go-10", "tarpaulin-80", "llvm-cov-70", "go-plus-python-85"])
def test_go_and_rust_gates_feed_the_minimum(tmp_path: Path, files: dict[str, str],
                                            expected: bool) -> None:
    for rel, text in files.items():
        _write(tmp_path, rel, text)
    block = detect_coverage_gate(tmp_path)
    assert block["threshold_meets_minimum"] is expected
    assert block["unmeasured_reason"] is None


@pytest.mark.parametrize(("files", "expected", "reason"), [
    ({"go.mod": _GO_MOD, "main.go": "package main\n"}, None, "go_rust_gate_form_not_read"),
    ({"Cargo.toml": _CARGO, "src/main.rs": "fn main() {}\n"}, None,
     "go_rust_gate_form_not_read"),
    ({"svc/go.mod": _GO_MOD}, None, "go_rust_gate_form_not_read"),
    ({_PY: "[project]\nname = 'x'\n", "testdata/x/go.mod": _GO_MOD}, False, None),
    ({_PY: "[project]\nname = 'x'\nversion = '0.1.0'\n"}, False, None),
], ids=["go-only", "cargo-only", "nested-go-mod", "testdata-fixture", "pyproject-only"])
def test_go_or_rust_with_no_readable_gate_is_not_measured(
        tmp_path: Path, files: dict[str, str], expected: bool | None,
        reason: str | None) -> None:
    for rel, text in files.items():
        _write(tmp_path, rel, text)
    block = detect_coverage_gate(tmp_path)
    assert block["gates"] == []
    assert block["configured"] is False
    assert block["threshold_meets_minimum"] is expected
    assert block["unmeasured_reason"] == reason


@pytest.mark.parametrize(("files", "expected", "reason"), [
    ({"go.mod": _GO_MOD, _PY: _fail_under("10")}, None, GO_RUST_UNMEASURED),
    ({"go.mod": _GO_MOD, _PY: _fail_under("0")}, None, GO_RUST_UNMEASURED),
    ({"Cargo.toml": _CARGO, _PY: _fail_under("10")}, None, GO_RUST_UNMEASURED),
    ({"go.mod": _GO_MOD, "Cargo.toml": _CARGO, _PY: _fail_under("10"),
      ".testcoverage.yml": _testcoverage("  total: 10\n")}, None, GO_RUST_UNMEASURED),
    ({"go.mod": _GO_MOD, _PY: _fail_under("10"),
      ".testcoverage.yml": _testcoverage("  total: 10\n")}, False, None),
    ({"go.mod": _GO_MOD, _PY: _fail_under("10"),
      ".testcoverage.yml": _testcoverage("  total: 0\n")}, False, None),
    ({"Cargo.toml": _CARGO, _PY: _fail_under("10"),
      _CI: _workflow("cargo tarpaulin --fail-under 10")}, False, None),
    ({"go.mod": _GO_MOD, _PY: _fail_under("85")}, True, None),
    ({"go.mod": _GO_MOD, _CI: _workflow(_TEMPLATED)}, None, None),
    ({_PY: _fail_under("10"), "testdata/x/go.mod": _GO_MOD}, False, None),
], ids=["l6_adv_go_plus_low_py", "go-plus-zero-py", "cargo-plus-low-py",
        "go-read-rust-unread", "go-read-low", "go-read-zero", "rust-read-low",
        "go-plus-high-py", "go-plus-templated-py-keeps-its-reason", "testdata-go-mod"])
def test_a_low_gate_beside_an_unread_language_is_not_measured(
        tmp_path: Path, files: dict[str, str], expected: bool | None,
        reason: str | None) -> None:
    """#937: a low gate for one language is not unmet while a Go or Rust module beside it
    has no gate read; a gate read for each such language, or a gate meeting the minimum,
    keeps the normal reading."""
    for rel, text in files.items():
        _write(tmp_path, rel, text)
    block = detect_coverage_gate(tmp_path)
    assert block["threshold_meets_minimum"] is expected
    assert block["unmeasured_reason"] == reason
    if expected is None:
        assert block["enforced_meets_minimum"] is None
    if expected is False:
        assert block["enforced_meets_minimum"] is False


def test_scoped_low_gate_reads_the_scopes_go_module(tmp_path: Path) -> None:
    """On a scoped run only the scope's own module counts: a Go service elsewhere in the
    repository does not make a Python package's low gate unknown."""
    _write(tmp_path, "services/api/go.mod", _GO_MOD)
    _write(tmp_path, "services/web/pyproject.toml", _fail_under("10"))
    web = detect_coverage_gate(tmp_path, scope=Path("services/web"))
    assert (web["threshold_meets_minimum"], web["unmeasured_reason"]) == (False, None)
    _write(tmp_path, "services/web/go.mod", _GO_MOD)
    web = detect_coverage_gate(tmp_path, scope=Path("services/web"))
    assert (web["threshold_meets_minimum"], web["unmeasured_reason"]) == (
        None, GO_RUST_UNMEASURED)


def test_excluded_go_mod_does_not_make_the_reading_unknown(tmp_path: Path) -> None:
    _write(tmp_path, "node_modules/dep/go.mod", _GO_MOD)
    _write(tmp_path, "vendor/x/Cargo.toml", _CARGO)
    block = detect_coverage_gate(tmp_path)
    assert block["threshold_meets_minimum"] is False
    assert block["unmeasured_reason"] is None


@pytest.mark.parametrize(("body", "expected"), [
    ("  file: 60\n  package: 70\n  total: 85  # project floor\n",
     [("file", 60.0, 3), ("package", 70.0, 4), ("total", 85.0, 5)]),
    ("  total: 0\n", [("total", 0.0, 3)]),
    ("  # total: 90\n  total: 75\nexclude:\n  total: 99\n", [("total", 75.0, 4)]),
], ids=["all-three", "zero", "comment-and-sibling-key"])
def test_testcoverage_keys_map_to_metrics(tmp_path: Path, body: str,
                                          expected: list[tuple[str, float, int]]) -> None:
    _write(tmp_path, ".testcoverage.yml", _testcoverage(body))
    gates = detect_coverage_gate(tmp_path)["gates"]
    assert [(g["metric"], g["threshold"], g["line"]) for g in gates] == expected
    assert all(g["tool"] == "go-test-coverage" for g in gates)


def test_testcoverage_inline_threshold_map(tmp_path: Path) -> None:
    _write(tmp_path, ".testcoverage.yaml", "threshold: {total: 80, file: 50}\n")
    gates = detect_coverage_gate(tmp_path)["gates"]
    assert sorted((g["metric"], g["threshold"], g["line"]) for g in gates) == [
        ("file", 50.0, 1), ("total", 80.0, 1)]


def test_zero_testcoverage_total_gates_nothing(tmp_path: Path) -> None:
    _write(tmp_path, "go.mod", _GO_MOD)
    _write(tmp_path, ".testcoverage.yml", _testcoverage("  total: 0\n"))
    block = detect_coverage_gate(tmp_path)
    assert (block["configured"], block["threshold_meets_minimum"]) == (False, False)


@pytest.mark.parametrize(("run", "expected"), [
    ("cargo tarpaulin --out Xml --fail-under=80", [("cargo-tarpaulin", 80.0)]),
    ("cargo tarpaulin --fail-under ${{ env.COV_MIN }}", [("cargo-tarpaulin", None)]),
    ("cargo llvm-cov --fail-under-lines=70", [("cargo-llvm-cov", 70.0)]),
    ("cargo +nightly tarpaulin --fail-under 80", [("cargo-tarpaulin", 80.0)]),
    ("cargo tarpaulin --out Xml && coverage report --fail-under=80", [("coverage.py", 80.0)]),
    ("cargo llvm-cov --fail-under-regions 70", []),
    ("cargo tarpaulin --fail-under-lines 70", []),
    ("coverage report --fail-under=80", [("coverage.py", 80.0)]),
    ("pytest --cov-fail-under=80", [("pytest-cov", 80.0)]),
], ids=["tarpaulin-eq", "tarpaulin-templated", "llvm-cov-eq", "nightly-tarpaulin",
        "tarpaulin-then-coverage-py",        "llvm-cov-regions",
        "tarpaulin-not-llvm-flag", "coverage-py-not-rust", "pytest-cov-not-rust"])
def test_rust_cli_forms_are_anchored_on_the_tool(tmp_path: Path, run: str,
                                                 expected: list[tuple[str, float | None]]
                                                 ) -> None:
    _write(tmp_path, _CI, _workflow(run))
    gates = detect_coverage_gate(tmp_path)["gates"]
    assert [(g["tool"], g["threshold"]) for g in gates] == expected


def test_templated_rust_threshold_still_gates(tmp_path: Path) -> None:
    _write(tmp_path, "Cargo.toml", _CARGO)
    _write(tmp_path, _CI, _workflow("cargo tarpaulin --fail-under $COV_MIN"))
    block = detect_coverage_gate(tmp_path)
    assert (block["configured"], block["threshold_meets_minimum"]) == (True, None)
    assert block["unmeasured_reason"] is None


def test_read_forms_leave_not_detected(tmp_path: Path) -> None:
    text = " ".join(detect_coverage_gate(tmp_path)["not_detected"])
    assert "tarpaulin `--fail-under`" not in text and ".testcoverage.yml" not in text
    assert "go tool cover" in text


def test_scoped_run_reads_the_root_go_mod(tmp_path: Path) -> None:
    """A Go package directory has no go.mod of its own: the root module counts."""
    _write(tmp_path, "go.mod", _GO_MOD)
    _write(tmp_path, "pkg/a/a.go", "package a\n")
    block = detect_coverage_gate(tmp_path, scope=Path("pkg/a"))
    assert block["unmeasured_reason"] == "go_rust_gate_form_not_read"
    _write(tmp_path, "pkg/b/b.py", "x = 1\n")
    (tmp_path / "go.mod").unlink()
    assert detect_coverage_gate(tmp_path, scope=Path("pkg/a"))["unmeasured_reason"] is None


def test_scoped_run_reads_the_root_testcoverage_config(tmp_path: Path) -> None:
    _write(tmp_path, "go.mod", _GO_MOD)
    _write(tmp_path, ".testcoverage.yml", _testcoverage("  total: 80\n"))
    _write(tmp_path, "pkg/a/a.go", "package a\n")
    block = detect_coverage_gate(tmp_path, scope=Path("pkg/a"))
    assert [g["file"] for g in block["gates"]] == [".testcoverage.yml"]
    assert block["threshold_meets_minimum"] is True


def test_scoped_run_reads_an_intermediate_go_mod(tmp_path: Path) -> None:
    """A manifest between the root and the scope is the scope's module."""
    _write(tmp_path, "services/api/go.mod", _GO_MOD)
    _write(tmp_path, "services/api/cmd/main.go", "package main\n")
    _write(tmp_path, "services/web/x.py", "x = 1\n")
    assert detect_coverage_gate(
        tmp_path, scope=Path("services/api/cmd"))["unmeasured_reason"] == GO_RUST_UNMEASURED
    assert detect_coverage_gate(
        tmp_path, scope=Path("services/web"))["unmeasured_reason"] is None
