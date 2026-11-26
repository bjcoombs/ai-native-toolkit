"""A scoped run counts a root coverage.py floor that still governs the scope (#1032).

coverage.py reads its config from the directory it runs in, so a root
``[tool.coverage]`` floor gates a package with no config of its own when CI runs
coverage from the root. These tests pin the three readings: counted (CI runs it from
the root, or names the root config), dropped (CI runs it only in the scope), and
unknown (where CI runs it is not resolved), which reads null with a reason, never
"no floor".
"""
from __future__ import annotations

from pathlib import Path

import pytest

from lib.run.run_scope import Scope
from lib.testing.coverage_ci import read_ci_runs
from lib.testing.coverage_gate import detect_coverage_gate
from lib.testing.coverage_root_config import ROOT_UNRESOLVED, root_config, root_config_applies

ROOT_FLOOR = "[project]\nname = 'root'\n\n[tool.coverage.report]\nfail_under = 85\n"
CI = ".github/workflows/ci.yml"


def _write(root: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


def _repo(tmp_path: Path, workflow: str | None, **extra: str) -> Path:
    files = {"pyproject.toml": ROOT_FLOOR, "pkg/src/a.py": "x = 1\n", **extra}
    if workflow is not None:
        files[CI] = "on: [pull_request]\njobs:\n  t:\n    runs-on: ubuntu-latest\n" \
                    "    steps:\n" + workflow
    return _write(tmp_path, files)


def _applies(root: Path) -> bool | None:
    return root_config_applies(root, "pyproject.toml", read_ci_runs(root))


def test_a_ci_run_from_the_root_counts_the_root_floor(tmp_path: Path) -> None:
    root = _repo(tmp_path, "      - run: pytest --cov pkg\n")
    block = detect_coverage_gate(root, Scope.of(root, Path("pkg")))
    assert [(g["file"], g["threshold"]) for g in block["gates"]] == [("pyproject.toml", 85.0)]
    assert block["configured"] is True
    assert block["threshold_meets_minimum"] is True
    assert block["unmeasured_reason"] is None
    assert (block["enforced_meets_minimum"], block["enforced_basis"]) == (True, "ci_run")


def test_no_ci_step_reads_the_root_floor_as_unknown_not_absent(tmp_path: Path) -> None:
    root = _repo(tmp_path, None)
    block = detect_coverage_gate(root, Scope.of(root, Path("pkg")))
    assert [g["file"] for g in block["gates"]] == ["pyproject.toml"]
    assert block["threshold_meets_minimum"] is None
    assert block["unmeasured_reason"] == ROOT_UNRESOLVED
    assert block["enforced_meets_minimum"] is None


def test_an_unresolved_root_floor_is_reported_but_not_configured(tmp_path: Path) -> None:
    """The root gates stay in ``gates`` as evidence; ``configured`` and the maximum read
    only the gates known to govern the scope."""
    low = _repo(tmp_path / "low", None, **{"pyproject.toml": "[tool.coverage.report]\nfail_under = 30\n"})
    block = detect_coverage_gate(low, Scope.of(low, Path("pkg")))
    assert [g["file"] for g in block["gates"]] == ["pyproject.toml"]
    assert (block["configured"], block["max_threshold_percent"]) == (False, None)
    own = _repo(tmp_path / "own", None, **{
        "pyproject.toml": "[tool.coverage.report]\nfail_under = 95\n",
        "pkg/jest.config.js": "module.exports = { coverageThreshold: { global: { lines: 60 } } };\n"})
    block = detect_coverage_gate(own, Scope.of(own, Path("pkg")))
    assert (block["configured"], block["max_threshold_percent"]) == (True, 60.0)
    assert block["threshold_meets_minimum"] is True


def test_an_unresolved_root_floor_is_never_credited_as_run(tmp_path: Path) -> None:
    """The scope's own 80 is what CI may run; the unresolved root 90 earns no ci_run."""
    root = _repo(tmp_path, "      - run: cd $PKG && coverage report\n", **{
        "pyproject.toml": "[tool.coverage.report]\nfail_under = 90\n",
        "pkg/Makefile": "cov:\n\tpytest --cov --cov-fail-under=80\n"})
    block = detect_coverage_gate(root, Scope.of(root, Path("pkg")))
    assert block["threshold_meets_minimum"] is True
    assert block["enforced_meets_minimum"] is not True


def test_a_templated_root_floor_names_the_reason(tmp_path: Path) -> None:
    root = _repo(tmp_path, None, **{"pyproject.toml": "[tool.coverage.report]\nfail_under = ${COV_MIN}\n"})
    block = detect_coverage_gate(root, Scope.of(root, Path("pkg")))
    assert (block["threshold_meets_minimum"], block["unmeasured_reason"]) == (None, ROOT_UNRESOLVED)


def test_a_ci_run_only_inside_the_scope_drops_the_root_floor(tmp_path: Path) -> None:
    root = _repo(tmp_path, "      - run: pytest --cov\n        working-directory: pkg\n")
    block = detect_coverage_gate(root, Scope.of(root, Path("pkg")))
    assert block["gates"] == []
    assert (block["configured"], block["threshold_meets_minimum"]) == (False, False)
    assert block["unmeasured_reason"] is None


def test_a_low_root_floor_reads_false_either_way(tmp_path: Path) -> None:
    """Whether a 30 percent floor applies or not, the scope has no floor of 50 percent."""
    root = _repo(tmp_path, None, **{"pyproject.toml": "[tool.coverage.report]\nfail_under = 30\n"})
    block = detect_coverage_gate(root, Scope.of(root, Path("pkg")))
    assert (block["threshold_meets_minimum"], block["unmeasured_reason"]) == (False, None)


def test_the_scopes_own_config_wins(tmp_path: Path) -> None:
    root = _repo(tmp_path, "      - run: pytest --cov\n",
                 **{"pkg/.coveragerc": "[report]\nshow_missing = true\n"})
    assert root_config(root, root / "pkg", (set(), [])) is None
    assert detect_coverage_gate(root, Scope.of(root, Path("pkg")))["gates"] == []


def test_a_scope_with_no_python_is_not_governed(tmp_path: Path) -> None:
    root = _write(tmp_path, {"pyproject.toml": ROOT_FLOOR, "web/index.js": "1\n"})
    assert root_config(root, root / "web", (set(), [])) is None


def test_the_whole_repo_run_is_unchanged(tmp_path: Path) -> None:
    root = _repo(tmp_path, None)
    block = detect_coverage_gate(root)
    assert block["threshold_meets_minimum"] is True
    assert block["unmeasured_reason"] is None


@pytest.mark.parametrize(("steps", "applies"), [
    ("      - run: cd pkg && pytest --cov\n", False),
    ("      - run: cd pkg && pytest --cov --cov-config=../pyproject.toml\n", True),
    ("      - run: coverage report --rcfile pyproject.toml\n        working-directory: .\n", True),
    ("      - run: |\n          cd pkg\n          pytest --cov\n", False),
    ("      - run: cd pkg\n      - run: pytest --cov\n", True),
    ("      - run: cd $PKG && pytest --cov\n", None),
    ("      - run: pytest --cov\n        working-directory: ${{ matrix.dir }}\n", None),
    ("      - name: cd into nothing\n        run: pytest --cov\n", True),
    ("      - run: pytest\n", None),
    ("      - run: cd pkg && pytest --cov\n      - run: coverage xml\n", True),
    ("      - run: |\n          (cd docs && make html)\n          pytest --cov\n", None),
    ("      - run: (cd docs && make html) && pytest --cov\n", None),
    ("      - run: (cd pkg && pytest --cov)\n", False),
    ("      - run: pytest --cov --cov-config=.coveragerc\n", True),
    ("      - run: uv run --directory pkg pytest --cov\n", False),
    ("      - run: uv --directory=pkg run pytest --cov\n", False),
    ("      - run: poetry -C pkg run pytest --cov\n", False),
    ("      - run: uv run --directory $D pytest --cov\n", None),
    ("      - run: uv run pytest --cov\n", True),
    ("      -   name: t\n          run: pytest --cov\n          working-directory: pkg\n", False),
    ("      - run: cd pytest-plugin && pytest --cov\n", False),
    ("      - run: pytest -c ci/pytest.ini\n      - run: cd pkg && pytest --cov\n", None),
])
def test_where_ci_runs_coverage_decides(tmp_path: Path, steps: str, applies: bool | None) -> None:
    assert _applies(_repo(tmp_path, steps)) is applies


def test_a_bare_pytest_reads_the_root_addopts(tmp_path: Path) -> None:
    root = _repo(tmp_path, "      - run: pytest\n", **{"pyproject.toml": (
        "[tool.pytest.ini_options]\naddopts = \"--cov=pkg\"\n\n"
        "[tool.coverage.report]\nfail_under = 85\n")})
    assert _applies(root) is True
    block = detect_coverage_gate(root, Scope.of(root, Path("pkg")))
    assert (block["threshold_meets_minimum"], block["enforced_meets_minimum"]) == (True, True)


def test_a_dispatch_only_root_run_does_not_count(tmp_path: Path) -> None:
    root = _write(tmp_path, {"pyproject.toml": ROOT_FLOOR, "pkg/a.py": "",
        ".github/workflows/manual.yml": "on: [workflow_dispatch]\njobs:\n  t:\n    steps:\n"
                                        "      - run: pytest --cov\n",
        CI: "on: [pull_request]\njobs:\n  t:\n    steps:\n"
            "      - run: pytest --cov\n        working-directory: pkg\n"})
    assert _applies(root) is False


@pytest.mark.parametrize("comment", ["", "      # note\n"])
def test_a_defaults_working_directory_is_unresolved(tmp_path: Path, comment: str) -> None:
    root = _write(tmp_path, {"pyproject.toml": ROOT_FLOOR, "pkg/a.py": "", CI: (
        "on: [pull_request]\njobs:\n  t:\n    defaults:\n" + comment + "      run:\n"
        "        working-directory: pkg\n    steps:\n      - run: pytest --cov\n")})
    assert _applies(root) is None


@pytest.mark.parametrize(("script", "applies"), [
    ("test:\n  script:\n    - cd pkg\n    - pytest --cov\n", False),
    ("test:\n  script:\n    - pytest --cov\n", True),
    ("lint:\n  script:\n    - cd pkg\ntest:\n  script:\n    - pytest --cov\n", True),
    ("default:\n  before_script: [cd pkg]\ntest:\n  script:\n    - pytest --cov\n", None),
    ("before_script:\n- cd pkg\ntest:\n  script:\n    - pytest --cov\n", None),
    (".base:\n  before_script: [cd pkg]\ntest:\n  extends: .base\n  script:\n"
     "    - pytest --cov\n", None),
    (".base: &base\n  before_script: [cd pkg]\ntest:\n  <<: *base\n  script:\n"
     "    - pytest --cov\n", None),
    ("test:\n  script:\n    - pytest --cov\n  before_script:\n    - cd pkg\n", None),
    ("test:\n  after_script: [cd reports && ls]\n  script:\n    - pytest --cov\n", True),
    ("test:\n  after_script:\n    - cd reports\n  script:\n    - pytest --cov\n", True),
    ("default:\n  image: python\nbefore_script:\n  - pip install .\n"
     "test:\n  script:\n    - pytest --cov\n", True),
])
def test_gitlab_cd_holds_for_the_job(tmp_path: Path, script: str,
                                     applies: bool | None) -> None:
    root = _write(tmp_path, {"pyproject.toml": ROOT_FLOOR, "pkg/a.py": "",
                             ".gitlab-ci.yml": script})
    assert _applies(root) is applies


@pytest.mark.parametrize(("name", "text"), [
    (".coveragerc", "[report]\nfail_under = 80\n"),
    ("setup.cfg", "[coverage:report]\nfail_under = 80\n"),
    ("tox.ini", "[coverage:report]\nfail_under = 80\n"),
])
def test_every_coverage_py_config_name_is_read(tmp_path: Path, name: str, text: str) -> None:
    root = _write(tmp_path, {name: text, "pkg/a.py": "",
                             CI: "on: [pull_request]\njobs:\n  t:\n    steps:\n"
                                 "      - run: pytest --cov\n"})
    assert root_config(root, root / "pkg", (set(), [])) == name
    assert [g["file"] for g in detect_coverage_gate(root, Scope.of(root, Path("pkg")))["gates"]] == [name]


def test_a_setup_cfg_without_coverage_sections_falls_through(tmp_path: Path) -> None:
    root = _write(tmp_path, {"setup.cfg": "[flake8]\nmax-line-length = 99\n",
                             "pyproject.toml": ROOT_FLOOR, "pkg/a.py": ""})
    assert root_config(root, root / "pkg", (set(), [])) == "pyproject.toml"
