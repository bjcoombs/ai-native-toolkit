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

from lib.coverage_ci import read_ci_runs
from lib.coverage_gate import detect_coverage_gate
from lib.coverage_root_config import ROOT_UNRESOLVED, root_config, root_config_applies

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
    block = detect_coverage_gate(root, Path("pkg"))
    assert [(g["file"], g["threshold"]) for g in block["gates"]] == [("pyproject.toml", 85.0)]
    assert block["configured"] is True
    assert block["threshold_meets_minimum"] is True
    assert block["unmeasured_reason"] is None
    assert (block["enforced_meets_minimum"], block["enforced_basis"]) == (True, "ci_run")


def test_no_ci_step_reads_the_root_floor_as_unknown_not_absent(tmp_path: Path) -> None:
    root = _repo(tmp_path, None)
    block = detect_coverage_gate(root, Path("pkg"))
    assert [g["file"] for g in block["gates"]] == ["pyproject.toml"]
    assert block["threshold_meets_minimum"] is None
    assert block["unmeasured_reason"] == ROOT_UNRESOLVED
    assert block["enforced_meets_minimum"] is None


def test_a_ci_run_only_inside_the_scope_drops_the_root_floor(tmp_path: Path) -> None:
    root = _repo(tmp_path, "      - run: pytest --cov\n        working-directory: pkg\n")
    block = detect_coverage_gate(root, Path("pkg"))
    assert block["gates"] == []
    assert (block["configured"], block["threshold_meets_minimum"]) == (False, False)
    assert block["unmeasured_reason"] is None


def test_a_low_root_floor_reads_false_either_way(tmp_path: Path) -> None:
    """Whether a 30 percent floor applies or not, the scope has no floor of 50 percent."""
    root = _repo(tmp_path, None, **{"pyproject.toml": "[tool.coverage.report]\nfail_under = 30\n"})
    block = detect_coverage_gate(root, Path("pkg"))
    assert (block["threshold_meets_minimum"], block["unmeasured_reason"]) == (False, None)


def test_the_scopes_own_config_wins(tmp_path: Path) -> None:
    root = _repo(tmp_path, "      - run: pytest --cov\n",
                 **{"pkg/.coveragerc": "[report]\nshow_missing = true\n"})
    assert root_config(root, root / "pkg", (set(), [])) is None
    assert detect_coverage_gate(root, Path("pkg"))["gates"] == []


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
])
def test_where_ci_runs_coverage_decides(tmp_path: Path, steps: str, applies: bool | None) -> None:
    assert _applies(_repo(tmp_path, steps)) is applies


def test_a_defaults_working_directory_is_unresolved(tmp_path: Path) -> None:
    root = _write(tmp_path, {"pyproject.toml": ROOT_FLOOR, "pkg/a.py": "", CI: (
        "on: [pull_request]\njobs:\n  t:\n    defaults:\n      run:\n"
        "        working-directory: pkg\n    steps:\n      - run: pytest --cov\n")})
    assert _applies(root) is None


@pytest.mark.parametrize(("script", "applies"), [
    ("test:\n  script:\n    - cd pkg\n    - pytest --cov\n", False),
    ("test:\n  script:\n    - pytest --cov\n", True),
    ("lint:\n  script:\n    - cd pkg\ntest:\n  script:\n    - pytest --cov\n", True),
])
def test_gitlab_cd_holds_for_the_job(tmp_path: Path, script: str, applies: bool) -> None:
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
    assert [g["file"] for g in detect_coverage_gate(root, Path("pkg"))["gates"]] == [name]


def test_a_setup_cfg_without_coverage_sections_falls_through(tmp_path: Path) -> None:
    root = _write(tmp_path, {"setup.cfg": "[flake8]\nmax-line-length = 99\n",
                             "pyproject.toml": ROOT_FLOOR, "pkg/a.py": ""})
    assert root_config(root, root / "pkg", (set(), [])) == "pyproject.toml"
