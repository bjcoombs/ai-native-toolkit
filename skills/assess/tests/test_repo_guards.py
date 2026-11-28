"""Ratchets and repo-owned guards CI runs, and the guard-is-tested condition (issue #854): ``lib.ci.repo_guards``."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import lib.scoring.level_tally as lt
from lib.ci.repo_guards import guards_or_unknown, scan_repo_guards
from lib.counts_only.content_free import counts_only

GUARDS = "repo-guards.1"
TESTED = "repo-guard-tested.1"


def _workflow(*runs: str, on: str | None = "[push, pull_request]") -> str:
    steps = "".join(f"      - run: {r}\n" for r in runs)
    trigger = "" if on is None else f"on: {on}\n"
    return (f"name: ci\n{trigger}jobs:\n  check:\n    runs-on: ubuntu-latest\n"
            "    steps:\n      - uses: actions/checkout@v4\n" + steps)


def _repo(root: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


def _block(root: Path) -> dict[str, Any]:
    return dict(scan_repo_guards(root))


def _states(root: Path) -> dict[str, str]:
    data = {"repo_guards": scan_repo_guards(root)}
    return {c["id"]: str(c["state"]) for c in lt.level_result(lt.default_model(), data)["conditions"]
            if c["id"] in (GUARDS, TESTED)}


BASELINE = {
    "lint-baseline.json": '{"src/app.py": 3}\n',
    "Makefile": "lint:\n\tmypy-baseline filter --baseline lint-baseline.json\n"
                "update-baseline:\n\tmypy-baseline sync --baseline lint-baseline.json\n",
}
CHECKER = "#!/usr/bin/env python3\n\"\"\"Fail when a module imports across a layer.\"\"\"\nimport sys\nsys.exit(0)\n"


# --- 1390 criterion 1: a baseline read by CI, and a boundary checker run from a workflow step ---

def test_a_baseline_json_read_by_a_ci_step_counts(tmp_path: Path) -> None:
    root = _repo(tmp_path, {**BASELINE, ".github/workflows/ci.yml": _workflow(
        "mypy src | mypy-baseline filter --baseline lint-baseline.json")})
    block = _block(root)
    assert [(g["kind"], g["target"]) for g in block["guards"]] == [("lint_baseline", "lint-baseline.json")]
    assert (block["present"], block["lint_baseline"], block["files_read"]) == (True, 1, 1)
    assert _states(root)[GUARDS] == "met"


def test_a_baseline_read_through_a_make_recipe_counts(tmp_path: Path) -> None:
    root = _repo(tmp_path, {**BASELINE, ".github/workflows/ci.yml": _workflow("make lint")})
    assert _block(root)["lint_baseline"] == 1


def test_a_boundary_checker_run_from_a_workflow_step_counts(tmp_path: Path) -> None:
    root = _repo(tmp_path, {"scripts/check_layers.py": CHECKER,
                            ".github/workflows/ci.yml": _workflow("python scripts/check_layers.py")})
    block = _block(root)
    assert [(g["kind"], g["via"], g["script"]) for g in block["guards"]] == [
        ("boundary_checker", "script", "scripts/check_layers.py")]
    assert block["present"] is True and _states(root)[GUARDS] == "met"


@pytest.mark.parametrize("step", ["lint-imports", "uv run --locked lint-imports", "tach check",
                                  "npx depcruise src", "go-arch-lint check"])
def test_a_known_boundary_tool_counts(tmp_path: Path, step: str) -> None:
    root = _repo(tmp_path, {".github/workflows/ci.yml": _workflow(step)})
    block = _block(root)
    assert block["boundary_checker"] == 1 and block["present"] is True
    # a third-party tool has no script of its own to test: the interaction is not measured
    assert (block["guard_tested"], block["guard_tested_reason"]) == (None, "no_guard_script")


def test_a_verify_script_that_runs_import_boss_counts(tmp_path: Path) -> None:
    root = _repo(tmp_path, {"hack/verify-imports.sh": "#!/bin/sh\nset -e\nimport-boss ./...\n",
                            ".github/workflows/ci.yml": _workflow("hack/verify-imports.sh")})
    kinds = {(g["via"], g["target"]) for g in _block(root)["guards"]}
    assert kinds == {("import-boss", ""), ("script", "hack/verify-imports.sh")}


def test_a_file_size_cap_read_by_a_check_script_counts(tmp_path: Path) -> None:
    root = _repo(tmp_path, {".file-size-ratchet.toml": "[ceilings]\n",
                            "scripts/check_sizes.py": "RATCHET = '.file-size-ratchet.toml'\n",
                            ".github/workflows/ci.yml": _workflow("python scripts/check_sizes.py")})
    block = _block(root)
    assert [(g["kind"], g["target"]) for g in block["guards"]] == [("size_cap", ".file-size-ratchet.toml")]


def test_a_size_cap_script_by_name_counts(tmp_path: Path) -> None:
    root = _repo(tmp_path, {"tools/check-file-size.sh": "#!/bin/sh\nwc -l src/*\n",
                            ".github/workflows/ci.yml": _workflow("bash tools/check-file-size.sh")})
    assert _block(root)["size_cap"] == 1


def test_an_architecture_test_counts_when_ci_runs_tests(tmp_path: Path) -> None:
    arch = "import com.tngtech.archunit.lang.ArchRule;\nclass LayerArchTest {}\n"
    files = {"src/test/java/x/LayerArchTest.java": arch, "src/test/java/x/SearchTest.java": arch}
    root = _repo(tmp_path, {**files, ".github/workflows/ci.yml": _workflow("pytest")})
    assert [g["target"] for g in _block(root)["guards"]] == ["src/test/java/x/LayerArchTest.java"]
    lone = _repo(tmp_path / "lone", {**files, ".github/workflows/ci.yml": _workflow("echo hi")})
    assert _block(lone)["present"] is False


# --- 1390 criterion 2: false positives --------------------------------------------------------

def test_a_benchmark_baseline_no_ci_step_reads_does_not_count(tmp_path: Path) -> None:
    root = _repo(tmp_path, {"baseline.json": '{"p50_ms": 12}\n', "bench/run.py": "print(1)\n",
                            ".github/workflows/ci.yml": _workflow("pytest")})
    block = _block(root)
    assert (block["present"], block["guards"]) == (False, [])
    assert _states(root)[GUARDS] == "unmet"


def test_a_benchmark_baseline_a_ci_step_reads_does_not_count(tmp_path: Path) -> None:
    root = _repo(tmp_path, {"benchmarks/baseline.json": "{}\n",
                            "benchmarks/check_regression.py": "open('benchmarks/baseline.json')\n",
                            ".github/workflows/ci.yml": _workflow(
                                "python benchmarks/check_regression.py --baseline benchmarks/baseline.json")})
    assert _block(root)["present"] is False


def test_a_baseline_with_no_update_command_is_not_a_ratchet(tmp_path: Path) -> None:
    root = _repo(tmp_path, {"lint-baseline.json": "{}\n",
                            ".github/workflows/ci.yml": _workflow("eslint --baseline lint-baseline.json .")})
    block = _block(root)
    assert (block["present"], block["baselines_without_update"]) == (False, 1)


def test_a_do_not_update_comment_is_not_an_update_command(tmp_path: Path) -> None:
    root = _repo(tmp_path, {"lint-baseline.json": "{}\n",
                            "scripts/check_lint.py": "# never update lint-baseline.json by hand\n"
                                                     "BASELINE = 'lint-baseline.json'\n",
                            "Makefile": "# run: make update to refresh lint-baseline.json\nlint:\n\techo\n",
                            ".github/workflows/ci.yml": _workflow("python scripts/check_lint.py")})
    block = _block(root)
    assert (block["present"], block["baselines_without_update"]) == (False, 1)
    _repo(root, {"scripts/check_lint.py": "BASELINE = 'lint-baseline.json'\n"
                                          "if '--update' in ARGS: write('lint-baseline.json')\n"})
    assert _block(root)["lint_baseline"] == 1


def test_an_unrelated_update_word_on_the_reading_line_is_not_an_update_command(tmp_path: Path) -> None:
    root = _repo(tmp_path, {"lint-baseline.json": "{}\n", ".github/workflows/ci.yml": _workflow(
        "uv sync && ruff check --baseline lint-baseline.json")})
    block = _block(root)
    assert (block["present"], block["baselines_without_update"]) == (False, 1)


def test_a_named_checker_keeps_its_credit_when_its_baseline_has_no_update(tmp_path: Path) -> None:
    root = _repo(tmp_path, {"layers-baseline.json": "{}\n",
                            "scripts/check_layers.py": "BASELINE = 'layers-baseline.json'\n",
                            ".github/workflows/ci.yml": _workflow("python scripts/check_layers.py")})
    block = _block(root)
    assert [(g["kind"], g["target"]) for g in block["guards"]] == [("boundary_checker", "scripts/check_layers.py")]
    assert block["baselines_without_update"] == 1


def test_a_script_run_after_cd_is_resolved_from_that_folder(tmp_path: Path) -> None:
    root = _repo(tmp_path, {"tools/check_layers.py": CHECKER,
                            ".github/workflows/ci.yml": _workflow("cd tools && python check_layers.py")})
    assert [g["script"] for g in _block(root)["guards"]] == ["tools/check_layers.py"]
    out = _repo(tmp_path / "out", {"tools/check_layers.py": CHECKER,
                                   ".github/workflows/ci.yml": _workflow("cd $DIR && python check_layers.py")})
    assert _block(out)["present"] is False


def test_an_unsettled_run_expression_with_nothing_found_reads_not_measured(tmp_path: Path) -> None:
    root = _repo(tmp_path, {".github/workflows/ci.yml": _workflow("${{ matrix.cmd }}")})
    assert _block(root)["present_reason"] == "ci_not_read"


@pytest.mark.parametrize("flag", ["--no-sync", "--no-update", "--no-write"])
def test_a_negated_flag_on_the_reading_line_is_not_an_update_command(tmp_path: Path, flag: str) -> None:
    root = _repo(tmp_path, {".basedpyright/baseline.json": "{}\n", ".github/workflows/ci.yml": _workflow(
        f"uv run {flag} basedpyright --baselinefile .basedpyright/baseline.json")})
    block = _block(root)
    assert (block["present"], block["baselines_without_update"]) == (False, 1)


@pytest.mark.parametrize("script", ["scripts/check_lifecycle.sh", "scripts/verify_player.sh"])
def test_a_word_that_only_holds_layer_or_cycle_is_not_a_boundary(tmp_path: Path, script: str) -> None:
    root = _repo(tmp_path, {script: "#!/bin/sh\necho ok\n", ".github/workflows/ci.yml": _workflow(f"bash {script}")})
    assert _block(root)["present"] is False


@pytest.mark.parametrize("step", ["python scripts/check_layers.py || true",
                                  "python scripts/check_layers.py || echo skipped"])
def test_a_masked_guard_does_not_count(tmp_path: Path, step: str) -> None:
    root = _repo(tmp_path, {"scripts/check_layers.py": CHECKER, ".github/workflows/ci.yml": _workflow(step)})
    assert _block(root)["present"] is False


def test_a_masked_line_is_not_followed(tmp_path: Path) -> None:
    root = _repo(tmp_path, {"Makefile": "check:\n\t./hack/arch.sh || true\n",
                            "hack/arch.sh": "#!/bin/sh\nlint-imports\n",
                            ".github/workflows/ci.yml": _workflow("make check")})
    assert _block(root)["present"] is False


@pytest.mark.parametrize("step", ["pip install import-linter", "python -m pip install tach", "npm ci"])
def test_an_install_is_not_a_guard(tmp_path: Path, step: str) -> None:
    root = _repo(tmp_path, {".github/workflows/ci.yml": _workflow(step)})
    assert _block(root)["present"] is False


def test_a_gitlab_after_script_guard_does_not_count(tmp_path: Path) -> None:
    root = _repo(tmp_path, {".gitlab-ci.yml": "lint:\n  script:\n    - echo hi\n  after_script:\n    - lint-imports\n"})
    assert _block(root)["present"] is False


def test_a_gitlab_script_guard_counts(tmp_path: Path) -> None:
    root = _repo(tmp_path, {".gitlab-ci.yml": "lint:\n  script:\n    - lint-imports\n"})
    assert _block(root)["present"] is True


def test_a_manual_only_workflow_guards_no_change(tmp_path: Path) -> None:
    root = _repo(tmp_path, {".github/workflows/ci.yml": _workflow("lint-imports", on="[workflow_dispatch]")})
    assert _block(root)["present"] is False


def test_a_guard_on_an_unsettled_trigger_reads_not_measured(tmp_path: Path) -> None:
    root = _repo(tmp_path, {".github/workflows/ci.yml": _workflow("lint-imports", on=None)})
    block = _block(root)
    assert (block["present"], block["present_reason"]) == (None, "trigger_unknown")
    assert block["guards"][0]["trigger"] == "unknown"
    assert _states(root)[GUARDS] == "not_measured"


def test_an_unread_ci_system_with_nothing_found_reads_not_measured(tmp_path: Path) -> None:
    root = _repo(tmp_path, {"Jenkinsfile": "pipeline {}\n"})
    block = _block(root)
    assert (block["present"], block["present_reason"]) == (None, "ci_not_read")
    assert (block["guard_tested"], block["guard_tested_reason"]) == (None, "ci_not_read")


def test_a_remote_reusable_workflow_with_nothing_found_reads_not_measured(tmp_path: Path) -> None:
    text = _workflow("echo ok") + "  lint:\n    uses: org/repo/.github/workflows/lint.yml@v1\n"
    root = _repo(tmp_path, {".github/workflows/ci.yml": text})
    assert _block(root)["present_reason"] == "ci_not_read"
    found = _repo(tmp_path / "found", {".github/workflows/ci.yml": _workflow("lint-imports") +
                                       "  lint:\n    uses: org/repo/.github/workflows/lint.yml@v1\n"})
    assert _block(found)["present"] is True


def test_a_reader_fault_reads_not_measured(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import lib.ci.repo_guards as rg

    def boom(_root: Path) -> Any:
        raise OSError("x")

    monkeypatch.setattr(rg, "scan_repo_guards", boom)
    block = guards_or_unknown(tmp_path)
    assert (block["present"], block["present_reason"], block["guard_tested"]) == (None, "scan_failed", None)


# --- 1391: the guard is itself tested ---------------------------------------------------------

def test_a_checker_with_its_own_test_meets_the_interaction(tmp_path: Path) -> None:
    root = _repo(tmp_path, {"scripts/check_layers.py": CHECKER,
                            "scripts/tests/test_check_layers.py": "from check_layers import main\n",
                            ".github/workflows/ci.yml": _workflow("python scripts/check_layers.py")})
    block = _block(root)
    assert block["guards"][0]["tested_by"] == ["scripts/tests/test_check_layers.py"]
    assert (block["guard_scripts"], block["guard_scripts_tested"], block["guard_tested"]) == (1, 1, True)
    assert _states(root) == {GUARDS: "met", TESTED: "met"}


def test_the_checker_without_its_test_does_not(tmp_path: Path) -> None:
    root = _repo(tmp_path, {"scripts/check_layers.py": CHECKER,
                            ".github/workflows/ci.yml": _workflow("python scripts/check_layers.py")})
    block = _block(root)
    assert (block["guard_scripts"], block["guard_scripts_tested"], block["guard_tested"]) == (1, 0, False)
    assert _states(root) == {GUARDS: "met", TESTED: "unmet"}


def test_a_test_file_that_does_not_name_the_checker_is_not_its_test(tmp_path: Path) -> None:
    root = _repo(tmp_path, {"scripts/check_layers.py": CHECKER, "tests/test_check_layers.py": "def test_x(): pass\n",
                            ".github/workflows/ci.yml": _workflow("python scripts/check_layers.py")})
    assert _block(root)["guard_tested"] is False


@pytest.mark.parametrize(("script", "step", "test"), [
    ("hack/verify-imports.sh", "hack/verify-imports.sh", "hack/verify-imports.bats"),
    ("tools/check-arch.js", "node tools/check-arch.js", "tools/check-arch.test.js"),
])
def test_tests_in_other_languages_are_joined(tmp_path: Path, script: str, step: str, test: str) -> None:
    root = _repo(tmp_path, {script: "#!/bin/sh\necho checking\n", test: f"run {script}\n",
                            ".github/workflows/ci.yml": _workflow(step)})
    assert _block(root)["guards"][0]["tested_by"] == [test]


def test_a_guard_package_run_as_a_module_is_tested_by_its_tests_folder(tmp_path: Path) -> None:
    root = _repo(tmp_path, {"script/hassfest/__main__.py": "print(1)\n", "script/hassfest/model.py": "x = 1\n",
                            "tests/hassfest/test_model.py": "def test_x(): pass\n",
                            ".github/workflows/ci.yml": _workflow("python -m script.hassfest")})
    block = _block(root)
    assert [(g["kind"], g["script"], g["tested_by"]) for g in block["guards"]] == [
        ("boundary_checker", "script/hassfest", ["tests/hassfest/test_model.py"])]
    assert block["guard_tested"] is True


def test_a_go_run_checker_folder_is_tested_by_its_go_tests(tmp_path: Path) -> None:
    root = _repo(tmp_path, {"hack/tools/importcheck/main.go": "package main\n",
                            "hack/tools/importcheck/main_test.go": "package main\n",
                            ".github/workflows/ci.yml": _workflow("go run ./hack/tools/importcheck ./...")})
    assert _block(root)["guards"][0]["tested_by"] == ["hack/tools/importcheck/main_test.go"]


def test_no_guard_at_all_reads_the_interaction_unmet(tmp_path: Path) -> None:
    root = _repo(tmp_path, {".github/workflows/ci.yml": _workflow("pytest")})
    block = _block(root)
    assert (block["present"], block["guard_tested"]) == (False, False)


def test_the_model_joins_the_two_conditions() -> None:
    rows = {r["id"]: r for r in lt.default_model()["rows"]}
    guards, tested = rows["repo-guards"]["levels"][0], rows["repo-guard-tested"]["levels"][0]
    assert guards["guarded_by"] == TESTED
    assert tested["requires"] == [GUARDS]
    assert guards["truth"] == tested["truth"] == ["structure"]


# --- counts-only ------------------------------------------------------------------------------

def test_counts_only_keeps_every_count_and_drops_no_field(tmp_path: Path) -> None:
    root = _repo(tmp_path, {"scripts/check_layers.py": CHECKER,
                            "scripts/tests/test_check_layers.py": "import check_layers\n",
                            ".github/workflows/ci.yml": _workflow("python scripts/check_layers.py")})
    block = _block(root)
    kept = counts_only({"repo_guards": block})["repo_guards"]
    for field in ("files_read", "lint_baseline", "boundary_checker", "size_cap", "baselines_without_update",
                  "present", "guard_scripts", "guard_scripts_tested", "guard_tested"):
        assert kept[field] == block[field], field
    assert kept["guards"] == block["guards"]   # repo-relative paths and closed-vocabulary tokens


def test_counts_only_keeps_the_reasons(tmp_path: Path) -> None:
    root = _repo(tmp_path, {".github/workflows/ci.yml": _workflow("lint-imports")})
    kept = counts_only({"repo_guards": scan_repo_guards(root)})["repo_guards"]
    assert (kept["guard_tested"], kept["guard_tested_reason"]) == (None, "no_guard_script")
