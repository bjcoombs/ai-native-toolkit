"""Tests for lib/ci_lint_gates.py and the issue #926 ``lint_runs`` states in lib/ci_checks.py:
a rewrite-then-diff formatter gate, compiler warnings as errors (``compiler_gate_only``), .NET
analysers in the build (``analysers_in_build``) and Layer 3 aligned with Layer 5 on unread
targets (``check_target``)."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from lib.ci_checks import scan_ci_checks
from lib.ci_lint_gates import dotnet_props, job_ids, rewrite_gated
from lib.counts_only import counts_only_scans
from lib.level_signals import load_default_levels
from lib.levels import Group, evaluate

_HEAD = "on: [push, pull_request]\njobs:\n"


def _step(run: str, *, job: str = "t", extra: str = "") -> str:
    """One job with a ``run: |`` step whose lines are ``run``."""
    body = "".join(f"          {line}\n" for line in run.splitlines())
    return f"  {job}:\n    runs-on: ubuntu-latest\n    steps:\n      - run: |\n{body}{extra}"


def _wf(*jobs: str) -> dict[str, str]:
    return {".github/workflows/ci.yml": _HEAD + "".join(jobs)}


def _scan(tmp_path: Path, files: dict[str, str]) -> tuple[bool | None, str]:
    for rel, text in files.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    block = scan_ci_checks(tmp_path)
    return block["lint_runs"], block["lint_basis"]


_FMT = ("find include src -name '*.h' -o -name '*.cc' | \\\n"
        "xargs clang-format-21 -i -style=file -fallback-style=none\n")
_PROPS = "Directory.Build.props"


def _props(*props: str) -> str:
    body = "".join(f"    <{p}>true</{p}>\n" for p in props)
    return f"<Project>\n  <PropertyGroup>\n{body}  </PropertyGroup>\n</Project>\n"


@pytest.mark.parametrize(("files", "expected"), [
    # Criterion 3: fmtlib/fmt's lint step, rewrite then git diff --exit-code.
    pytest.param(_wf(_step(_FMT + "git diff --exit-code")), (True, "lint_command"), id="FMT"),
    pytest.param(_wf(_step(_FMT + "git diff --quiet")), (True, "lint_command"), id="FMT-quiet"),
    pytest.param(_wf(_step("clang-format -i a.cc && git diff --exit-code")),
                 (True, "lint_command"), id="FMT-one-line"),
    # Criterion 4: no diff, a commit, or the diff in another job.
    pytest.param(_wf(_step(_FMT)), (False, "no_lint_command"), id="REWRITE"),
    pytest.param(_wf(_step("swift-format format -i -r .\ngit commit -am fmt && git push")),
                 (False, "no_lint_command"), id="REWRITE-COMMIT"),
    pytest.param(_wf(_step(_FMT, job="a"), _step("git diff --exit-code", job="b")),
                 (False, "no_lint_command"), id="REWRITE-OTHERJOB"),
    pytest.param(_wf(_step("git diff --exit-code\nmix format")), (False, "no_lint_command"),
                 id="DIFF-BEFORE-REWRITE"),
    pytest.param(_wf(_step("mix format\ngit diff --exit-code || true")),
                 (False, "no_lint_command"), id="MASKED-DIFF"),
    pytest.param(_wf(_step("mix format\ngit diff --quiet || git commit -am fmt")),
                 (False, "no_lint_command"), id="REWRITE-DIFF-OR-COMMIT"),
    pytest.param(_wf(_step("mix format\ngit diff --quiet || (git commit -am fmt && git push)")),
                 (False, "no_lint_command"), id="REWRITE-DIFF-OR-COMMIT-PUSH"),
    pytest.param(_wf(_step("mix format\ngit diff --quiet || echo changed")),
                 (False, "no_lint_command"), id="REWRITE-DIFF-OR-ECHO"),
    pytest.param(_wf(_step("mix format\ngit diff --exit-code || echo \"false alarm, ignoring\"")),
                 (False, "no_lint_command"), id="REWRITE-DIFF-OR-ECHO-FALSE"),
    pytest.param(_wf(_step("mix format\ngit diff --quiet || false")),
                 (True, "lint_command"), id="REWRITE-DIFF-OR-FALSE"),
    pytest.param(_wf(_step("mix format\ngit diff --exit-code || (echo 'run mix format'; exit 1)")),
                 (True, "lint_command"), id="REWRITE-DIFF-OR-EXIT-1"),
    pytest.param({".gitlab-ci.yml": "fmt:\n  script:\n    - dart format .\n"
                                    "    - git diff --exit-code\n"},
                 (True, "lint_command"), id="GITLAB-REWRITE-DIFF"),
    pytest.param({".gitlab-ci.yml": "fmt:\n  script:\n    - clang-format -i src/a.cc\n"
                                    "  after_script:\n    - git diff --exit-code\n"},
                 (False, "no_lint_command"), id="GITLAB-AFTER-SCRIPT-DIFF"),
    pytest.param({".gitlab-ci.yml": "fmt:\n  after_script:\n    - echo done\n"
                                    "  script:\n    - clang-format -i src/a.cc\n"
                                    "    - git diff --exit-code\n"},
                 (True, "lint_command"), id="GITLAB-SCRIPT-AFTER-AFTER-SCRIPT"),
    pytest.param({".gitlab-ci.yml": "a:\n  script:\n    - dart format .\n"
                                    "b:\n  script:\n    - git diff --exit-code\n"},
                 (False, "no_lint_command"), id="GITLAB-OTHERJOB"),
    # Criterion 5: warnings as errors alone.
    pytest.param(_wf(_step("mix compile --warnings-as-errors")), (None, "compiler_gate_only"),
                 id="PHX"),
    pytest.param(_wf(_step("mix compile --warnings-as-errors\nmix credo")),
                 (True, "lint_command"), id="PHX+CREDO"),
    pytest.param(_wf(_step("cmake -DCMAKE_CXX_FLAGS=-Werror ..")), (None, "compiler_gate_only"),
                 id="CMAKE-WERROR"),
    pytest.param({**_wf(_step("dotnet build")), _PROPS: _props("TreatWarningsAsErrors")},
                 (None, "compiler_gate_only"), id="DOTNET-WARNASERROR-PROPS"),
    pytest.param(_wf(_step("dotnet build -warnaserror")), (None, "compiler_gate_only"),
                 id="DOTNET-WARNASERROR-FLAG"),
    # Criterion 6: .NET analysers in the build.
    pytest.param({**_wf(_step("dotnet build")), _PROPS: _props("EnforceCodeStyleInBuild")},
                 (None, "analysers_in_build"), id="NETAN"),
    pytest.param({**_wf(_step("dotnet build")),
                  _PROPS: _props("EnforceCodeStyleInBuild", "TreatWarningsAsErrors")},
                 (None, "analysers_in_build"), id="NETAN+WARNASERROR"),
    pytest.param({**_wf(_step("dotnet build")),
                  "src/App/App.csproj": "<Project><PropertyGroup><AnalysisLevel>latest-recommended"
                                        "</AnalysisLevel></PropertyGroup></Project>\n"},
                 (None, "analysers_in_build"), id="NETAN-CSPROJ-ANALYSISLEVEL"),
    pytest.param(_wf(_step("dotnet build")), (False, "no_lint_command"), id="DOTNET-PLAIN"),
    pytest.param({**_wf(_step("dotnet restore")), _PROPS: _props("EnforceCodeStyleInBuild")},
                 (False, "no_lint_command"), id="NETAN-NO-BUILD"),
    # Criterion 7: Layer 3 aligned with Layer 5 on targets whose recipe is not read.
    pytest.param(_wf(_step("make foo")), (None, "check_target"), id="make-foo"),
    pytest.param(_wf(_step("npm run foo")), (None, "check_target"), id="npm-run-foo"),
    pytest.param(_wf(_step("./scripts/foo.sh")), (None, "check_target"), id="script-foo"),
    pytest.param(_wf(_step("make install")), (False, "no_lint_command"), id="make-install"),
    pytest.param(_wf(_step("npm run build")), (False, "no_lint_command"), id="npm-run-build"),
    pytest.param(_wf(_step("nx graph")), (False, "no_lint_command"), id="nx-graph"),
    pytest.param(_wf(_step("bazel test //...")), (None, "check_target"), id="bazel-test"),
    # Criterion 8: a masked gate or linter reads the masked state.
    pytest.param(_wf(_step("mix compile --warnings-as-errors",
                           extra="        continue-on-error: true\n")),
                 (False, "not_enforced"), id="PHX-continue-on-error"),
    pytest.param(_wf(_step("vendor/bin/phpstan analyse || true")), (False, "not_enforced"),
                 id="phpstan-or-true"),
    # A masked linter beside a compiler gate or a .NET build stays false, never null.
    pytest.param(_wf(_step("ruff check . || true\ngcc -Werror -o a a.c")),
                 (False, "not_enforced"), id="MASKED-LINT+WERROR"),
    pytest.param({**_wf(_step("vendor/bin/phpstan analyse || true\ndotnet build")),
                  _PROPS: _props("EnforceCodeStyleInBuild")}, (False, "not_enforced"),
                 id="MASKED-LINT+NETAN"),
    # A bare msbuild value under matrix: is not a build.
    pytest.param({".github/workflows/ci.yml": _HEAD + "  t:\n    strategy:\n      matrix:\n"
                  "        tool:\n          - msbuild\n    runs-on: x\n    steps:\n"
                  "      - run: echo ${{ matrix.tool }}\n",
                  _PROPS: _props("EnforceCodeStyleInBuild")}, (False, "no_lint_command"),
                 id="MATRIX-MSBUILD"),
    # A lint line still wins over every new null code.
    pytest.param({**_wf(_step("dotnet build\ndotnet format --verify-no-changes")),
                  _PROPS: _props("EnforceCodeStyleInBuild")}, (True, "lint_command"),
                 id="NETAN+FORMAT"),
    pytest.param(_wf(_step("make foo\nmix compile --warnings-as-errors")), (None, "check_target"),
                 id="check-target-before-gate"),
])
def test_lint_runs_issue_926_states(tmp_path: Path, files: dict[str, str],
                                    expected: tuple[bool | None, str]) -> None:
    assert _scan(tmp_path, files) == expected


def test_an_unread_ci_system_still_reads_ahead_of_the_compiler_gate(tmp_path: Path) -> None:
    files = {**_wf(_step("mix compile --warnings-as-errors")), "Jenkinsfile": "pipeline {}\n"}
    assert _scan(tmp_path, files) == (None, "unread_ci_system")


def test_nx_graph_is_not_an_enforced_check_either(tmp_path: Path) -> None:
    for rel, text in _wf(_step("nx graph")).items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text, encoding="utf-8")
    assert scan_ci_checks(tmp_path)["enforced_check_runs"] is False


def test_job_ids_split_github_jobs_and_gitlab_top_level_keys() -> None:
    gha = (_HEAD + _step("a", job="one") + _step("b", job="two")).splitlines()
    ids = job_ids(gha)
    assert ids[gha.index("          a")] != ids[gha.index("          b")]
    gitlab = "stages: [x]\none:\n  script:\n    - a\ntwo:\n  script:\n    - b\n".splitlines()
    ids = job_ids(gitlab)
    assert ids[3] != ids[6] and ids[2] == ids[3]


def test_rewrite_gated_names_the_rewrite_line() -> None:
    text = _HEAD + _step("mix format\ngit diff --exit-code")
    lines = text.splitlines()
    assert rewrite_gated(text) == frozenset({lines.index("          mix format")})


def test_dotnet_props_reads_by_path(tmp_path: Path) -> None:
    (tmp_path / _PROPS).write_text(_props("TreatWarningsAsErrors"), encoding="utf-8")
    (tmp_path / "a.csproj").write_text("<Project><PropertyGroup><AnalysisLevel>none"
                                       "</AnalysisLevel></PropertyGroup></Project>\n",
                                       encoding="utf-8")
    assert dotnet_props(tmp_path, frozenset({_PROPS, "a.csproj"})) == (True, False)
    assert dotnet_props(tmp_path, frozenset()) == (False, False)


@pytest.mark.parametrize("files", [
    pytest.param(_wf(_step("mix compile --warnings-as-errors")), id="PHX"),
    pytest.param({**_wf(_step("dotnet build")), _PROPS: _props("EnforceCodeStyleInBuild")},
                 id="NETAN"),
])
def test_a_new_null_code_reads_layer_3_not_measured_never_level_0(
        tmp_path: Path, files: dict[str, str]) -> None:
    """Acceptance criteria 5, 6 and 9: Layer 3 level 1 reads the null as not measured, and a
    counts-only row keeps lint_basis as the new token."""
    for rel, text in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text, encoding="utf-8")
    block = scan_ci_checks(tmp_path)
    kept = counts_only_scans({"ci_checks": block})["ci_checks"]
    assert (kept["lint_runs"], kept["lint_basis"]) == (block["lint_runs"], block["lint_basis"])
    assert block["lint_basis"] in ("compiler_gate_only", "analysers_in_build")

    def layer3(ci: dict[str, object]) -> Any:
        group: Group = {"name": "g", "settings": None, "repos": {"r": {"ci_checks": ci}}}
        return next(r for r in evaluate(load_default_levels(), group)["rows"]
                    if r["id"] == "layer-3-linters")

    row = layer3(dict(kept))
    assert row["status"] == "unknown" and row["level"] is None
    assert "ci_checks.lint_runs" in row["missing_signals"]
    assert layer3({**kept, "lint_runs": False})["level"] == 0
