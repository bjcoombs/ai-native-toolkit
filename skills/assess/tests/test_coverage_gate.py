"""Contract suite for the coverage-gate detector.

``detect_coverage_gate`` reports whether a line-coverage threshold is enforced,
where (file and 1-based line) and the threshold exactly as written. These tests
pin one small fixture per detected form, the absent case, and the honesty rules:
a measured-but-ungated repo reads ``enforced: false``, a JaCoCo ratio is never
rescaled, and ``fail_under = 0`` is reported but gates nothing.
"""
from __future__ import annotations

from pathlib import Path

from lib.coverage_gate import detect_coverage_gate


def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def _only_gate(root: Path) -> dict:
    block = detect_coverage_gate(root)
    assert block["available"] is True
    assert len(block["gates"]) == 1, block["gates"]
    return block["gates"][0]


# --- absent ---------------------------------------------------------------

def test_no_config_reports_not_enforced(tmp_path: Path) -> None:
    block = detect_coverage_gate(tmp_path)
    assert block["enforced"] is False
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
    assert block["enforced"] is False
    assert block["gates"] == []


# --- Python ---------------------------------------------------------------

def test_pyproject_fail_under(tmp_path: Path) -> None:
    _write(tmp_path, "pyproject.toml",
           "[project]\nname = 'x'\n\n[tool.coverage.report]\nfail_under = 85\n")
    gate = _only_gate(tmp_path)
    assert gate == {"file": "pyproject.toml", "line": 5, "tool": "coverage.py",
                    "form": "[tool.coverage.report] fail_under",
                    "threshold": 85.0, "unit": "percent", "metric": "lines"}
    assert detect_coverage_gate(tmp_path)["enforced"] is True


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
    assert block["enforced"] is False


def test_ci_workflow_cov_fail_under(tmp_path: Path) -> None:
    _write(tmp_path, ".github/workflows/tests.yml",
           "jobs:\n  test:\n    steps:\n"
           "      - run: pytest --cov=src --cov-fail-under=88 -q\n")
    gate = _only_gate(tmp_path)
    assert gate == {"file": ".github/workflows/tests.yml", "line": 4,
                    "tool": "pytest-cov", "form": "--cov-fail-under",
                    "threshold": 88.0, "unit": "percent", "metric": "lines"}


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
    assert block["enforced"] is False

    _write(tmp_path, ".nycrc", '{ "check-coverage": true }\n')
    assert detect_coverage_gate(tmp_path)["enforced"] is True


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
                    "unit": "ratio", "metric": "line"}


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
    assert block["enforced"] is True


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
