"""Tests for the starter files ``/assess --fix`` plans and writes (issue #1154).

The planner is fed facts directly, so each case pins one choice: the check command picked
from the scan's entry points, the CI file for the detected system, the coverage form the gate
scan reads, the deny rule, and the rule that an existing file is never replaced.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

from lib.next_step_guidance import DENY_RULE_EXAMPLE, guidance  # noqa: E402
from lib.run_context_types_fix import RepoFacts  # noqa: E402
from lib.starter_files import (  # noqa: E402
    GUARDRAIL,
    remote_system,
    detect_facts,
    pick_check,
    plan_files,
    write_files,
)
from lib.starter_templates import coverage_command, deny_rules, github_workflow, gitlab_job  # noqa: E402


def _facts(**over: object) -> RepoFacts:
    base: RepoFacts = {"language": "python", "check_command": "pytest", "check_source": "pytest configuration",
                       "ci_system": "github", "ci_basis": "default", "default_branch": "main", "files": []}
    base.update(over)  # type: ignore[typeddict-item]
    return base


def test_pick_check_prefers_a_test_target_over_lint_and_the_build_default() -> None:
    entries = [{"command": "make lint", "source": "Makefile"}, {"command": "make test", "source": "Makefile"},
               {"command": "pytest", "source": "pytest configuration"}]
    assert pick_check(entries) == ("make test", "Makefile")
    assert pick_check([{"command": "npm run check", "source": "package.json"}]) == ("npm run check", "package.json")
    assert pick_check([]) is None


def test_detect_facts_reads_the_scan_and_lets_an_override_win(tmp_path: Path) -> None:
    (tmp_path / "main.go").write_text("package main\n", encoding="utf-8")
    (tmp_path / "go.mod").write_text("module x\n", encoding="utf-8")
    scans = {"agent_environment": {"check": {"entry_points": [{"command": "go test ./...", "source": "go.mod"}]}}}
    facts = detect_facts(tmp_path, scans)
    assert (facts["language"], facts["check_command"], facts["ci_system"]) == ("go", "go test ./...", "github")
    assert facts["ci_basis"] == "default" and "go.mod" in facts["files"]
    forced = detect_facts(tmp_path, scans, check_command="make ci", ci="gitlab")
    assert (forced["check_command"], forced["check_source"], forced["ci_basis"]) == ("make ci", "override", "override")


def test_empty_repository_takes_the_language_default_check(tmp_path: Path) -> None:
    facts = detect_facts(tmp_path, {}, language="rust")
    assert (facts["check_command"], facts["check_source"]) == ("cargo test", "rust default")
    assert detect_facts(tmp_path, {})["check_command"] is None


def test_each_gate_gets_its_file_and_the_guardrail_its_deny_rule(tmp_path: Path) -> None:
    planned = plan_files(tmp_path, ["instructions", "ci_runs_tests", "coverage_gate"], _facts(), guardrail=True)
    paths = [(p["path"], p["gate"]) for p in planned]
    assert paths == [("CLAUDE.md", "instructions"), (".github/workflows/tests.yml", "ci_runs_tests"),
                     (".github/workflows/coverage.yml", "coverage_gate"), (".claude/settings.json", GUARDRAIL)]
    assert "pytest" in planned[0]["content"] and "Guardrails" in planned[0]["content"]
    settings = json.loads(planned[-1]["content"])
    assert settings["permissions"]["deny"][0] == "Bash(git push origin main)"


def test_deny_rule_is_the_one_the_guidance_words() -> None:
    # #1153's wording for agent_ops.summary.permissions_encoded names this rule.
    words = guidance("agent_ops.summary.permissions_encoded", "eq", True)
    assert words is not None and DENY_RULE_EXAMPLE in words["change"]
    assert deny_rules(_facts())[0] == DENY_RULE_EXAMPLE
    assert deny_rules(_facts(default_branch="develop"))[0] == "Bash(git push origin develop)"


def test_no_guardrail_paragraph_when_the_settings_file_exists(tmp_path: Path) -> None:
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "settings.json").write_text("{}\n", encoding="utf-8")
    planned = plan_files(tmp_path, ["instructions"], _facts(), guardrail=True)
    assert "Guardrails" not in planned[0]["content"]


def test_no_check_command_names_the_manual_step(tmp_path: Path) -> None:
    planned = plan_files(tmp_path, ["instructions", "ci_runs_tests"], _facts(check_command=None, language=None),
                         guardrail=False)
    assert planned[0]["path"] == "CLAUDE.md" and planned[0]["manual"]
    assert planned[1]["path"] == "" and "--check-command" in (planned[1]["manual"] or "")


@pytest.mark.parametrize(("facts", "expected"), [
    (_facts(), "pytest --cov --cov-fail-under=50"),
    (_facts(files=["uv.lock"]), "uv run --with pytest-cov pytest --cov --cov-fail-under=50"),
    (_facts(language="rust", check_command="cargo test"), "cargo llvm-cov --fail-under-lines 50"),
    (_facts(language="typescript", check_command="npm test"), "npx c8 --check-coverage --lines 50 npm test"),
])
def test_coverage_forms_are_ones_the_gate_scan_reads(facts: RepoFacts, expected: str) -> None:
    form = coverage_command(facts)
    assert form is not None and form[0] == expected


def test_a_language_with_no_coverage_form_gets_a_manual_step(tmp_path: Path) -> None:
    facts = _facts(language="go", check_command="go test ./...")
    assert coverage_command(facts) is None
    planned = plan_files(tmp_path, ["coverage_gate"], facts, guardrail=False)
    assert planned[0]["path"] == "" and "coverage threshold" in (planned[0]["manual"] or "")


def test_github_workflow_fits_the_repository() -> None:
    uv = github_workflow(_facts(files=["uv.lock"]), "tests", "pytest")
    assert "astral-sh/setup-uv" in uv and "uv sync --locked" in uv and "run: uv run pytest" in uv
    node = github_workflow(_facts(language="javascript", check_command="npm test", files=["package-lock.json"]),
                           "tests", "npm test")
    assert "actions/setup-node" in node and "run: npm ci" in node
    assert "branches: [main]" in node and "pull_request" in node


def test_gitlab_new_file_merges_jobs_and_an_existing_one_gets_an_include(tmp_path: Path) -> None:
    facts = _facts(ci_system="gitlab")
    planned = plan_files(tmp_path, ["ci_runs_tests", "coverage_gate"], facts, guardrail=False)
    assert [p["path"] for p in planned] == [".gitlab-ci.yml"]
    assert "tests:" in planned[0]["content"] and "coverage:" in planned[0]["content"]
    (tmp_path / ".gitlab-ci.yml").write_text("lint:\n  script: [echo hi]\n", encoding="utf-8")
    planned = plan_files(tmp_path, ["ci_runs_tests"], facts, guardrail=False)
    assert planned[0]["path"] == ".gitlab/ci/tests.yml" and "include" in (planned[0]["manual"] or "")
    assert gitlab_job(facts, "tests", "pytest").startswith("tests:\n  image: python:3.12")


def test_write_files_never_overwrites(tmp_path: Path) -> None:
    (tmp_path / "CLAUDE.md").write_text("mine\n", encoding="utf-8")
    planned = plan_files(tmp_path, ["instructions"], _facts(), guardrail=True)
    out = write_files(tmp_path, planned)
    assert (tmp_path / "CLAUDE.md").read_text(encoding="utf-8") == "mine\n"
    assert out[0]["state"] == "conflict" and out[0]["suggested"] and "pytest" in out[0]["suggested"]
    assert out[1]["state"] == "written" and (tmp_path / ".claude" / "settings.json").is_file()
    assert write_files(tmp_path, planned)[1]["state"] == "conflict"


@pytest.mark.parametrize(("url", "system"), [
    ("git@github.com:acme/gitlab-migration.git", "github"),
    ("https://github.com/acme/pkg.git", "github"),
    ("git@gitlab.example.com:team/pkg.git", "gitlab"),
    ("https://gitlab.com/group/sub/pkg.git", "gitlab"),
    ("https://git.example.com/github/pkg.git", None),
    (None, None),
])
def test_remote_system_reads_the_host_not_the_path(url: str | None, system: str | None) -> None:
    assert remote_system(url) == system


def test_an_unusual_check_command_is_quoted_into_valid_yaml() -> None:
    import yaml

    command = 'echo "run: now" && pytest'
    for text in (github_workflow(_facts(language=None), "tests", command), gitlab_job(_facts(language=None), "tests", command)):
        data = yaml.safe_load(text)
        steps = data["jobs"]["tests"]["steps"] if "jobs" in data else None
        assert (steps[-1]["run"] if steps else data["tests"]["script"][0]) == command
    assert "run: pytest\n" in github_workflow(_facts(), "tests", "pytest")  # a plain command stays plain
