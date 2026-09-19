"""Tests for lib/instruction_claims.py - verifying claims in agent instruction files.

An instruction file that says "`scripts/check-x.sh` is enforced in CI" or "Node
20.11.0 is pinned in `.nvmrc`" makes a checkable promise. The scan extracts each
such sentence and checks it against the repository, so a claim nothing backs
surfaces as a failure with its file and line.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from lib.instruction_claims import extract_claims, scan_instruction_claims

ENFORCED = "# Agents\n\nKeep changes small.\n\n`scripts/check-x.sh` is enforced in CI.\n"
WORKFLOW_WITHOUT = (
    "on: push\njobs:\n  hello:\n    runs-on: ubuntu-latest\n    steps:\n      - run: echo hello\n"
)
WORKFLOW_WITH = (
    "on: push\njobs:\n  lint:\n    runs-on: ubuntu-latest\n    steps:\n"
    "      - run: bash scripts/check-x.sh\n"
)


def _failures(block: dict) -> list[list]:
    return [[f["file"], f["line"], f["kind"]] for f in block["failures"]]


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "check-x.sh").write_text("echo ok\n")
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    return tmp_path


def test_enforcement_claim_with_no_workflow_reference_fails_with_its_line(repo: Path) -> None:
    (repo / "AGENTS.md").write_text(ENFORCED)
    (repo / ".github" / "workflows" / "ci.yml").write_text(WORKFLOW_WITHOUT)
    block = scan_instruction_claims(repo, ["AGENTS.md"])
    assert (block["total"], block["verified"], block["failed"]) == (1, 0, 1)
    assert _failures(block) == [["AGENTS.md", 5, "enforcement"]]
    assert block["failures"][0]["path"] == "scripts/check-x.sh"


def test_enforcement_claim_verifies_when_a_workflow_calls_the_script(repo: Path) -> None:
    (repo / "AGENTS.md").write_text(ENFORCED)
    (repo / ".github" / "workflows" / "ci.yml").write_text(WORKFLOW_WITH)
    block = scan_instruction_claims(repo, ["AGENTS.md"])
    assert block == {"total": 1, "verified": 1, "failed": 0, "failures": []}


def test_enforcement_claim_is_skipped_when_the_repo_has_no_ci_config(tmp_path: Path) -> None:
    # Nothing to check against: unverifiable, not false - no accusation.
    (tmp_path / "AGENTS.md").write_text(ENFORCED)
    assert scan_instruction_claims(tmp_path, ["AGENTS.md"])["total"] == 0


def test_enforcement_failure_carries_a_reason(repo: Path) -> None:
    (repo / "AGENTS.md").write_text(ENFORCED)
    (repo / ".github" / "workflows" / "ci.yml").write_text(WORKFLOW_WITHOUT)
    reason = scan_instruction_claims(repo, ["AGENTS.md"])["failures"][0]["reason"]
    assert "references the script" in reason


@pytest.mark.parametrize("ci_file", [".gitlab-ci.yml", "Jenkinsfile", ".circleci/config.yml"])
def test_enforcement_claim_verifies_against_non_github_ci(tmp_path: Path, ci_file: str) -> None:
    (tmp_path / "AGENTS.md").write_text(ENFORCED)
    (tmp_path / ci_file).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / ci_file).write_text("lint:\n  script: bash scripts/check-x.sh\n")
    block = scan_instruction_claims(tmp_path, ["AGENTS.md"])
    assert (block["total"], block["verified"]) == (1, 1)


@pytest.mark.parametrize("runner, body", [
    ("Makefile", "lint:\n\tbash scripts/check-x.sh\n"),
    ("package.json", '{"scripts": {"lint": "scripts/check-x.sh"}}\n'),
    (".pre-commit-config.yaml", "- id: x\n  entry: scripts/check-x.sh\n"),
])
def test_enforcement_claim_verifies_through_a_task_runner(repo: Path, runner: str, body: str) -> None:
    # The workflow calls `make lint` / `npm run lint` / pre-commit, not the path.
    (repo / "AGENTS.md").write_text(ENFORCED)
    (repo / ".github" / "workflows" / "ci.yml").write_text(WORKFLOW_WITHOUT)
    (repo / runner).write_text(body)
    block = scan_instruction_claims(repo, ["AGENTS.md"])
    assert (block["total"], block["verified"]) == (1, 1)


def test_file_with_no_matching_pattern_yields_zero_claims(repo: Path) -> None:
    (repo / "AGENTS.md").write_text("# Agents\n\nKeep changes small. Prefer plain names.\n")
    block = scan_instruction_claims(repo, ["AGENTS.md"])
    assert block == {"total": 0, "verified": 0, "failed": 0, "failures": []}


def test_no_instruction_files_yields_the_empty_block(tmp_path: Path) -> None:
    assert scan_instruction_claims(tmp_path, []) == {
        "total": 0, "verified": 0, "failed": 0, "failures": []}


def test_unreadable_or_missing_file_is_skipped_not_raised(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_bytes(b"\xff\xfe not utf-8 `scripts/a.sh` runs in CI")
    block = scan_instruction_claims(tmp_path, ["AGENTS.md", "CLAUDE.md"])
    assert block["total"] == 0


def test_pin_claims_verify_on_version_either_side_of_pinned_in(tmp_path: Path) -> None:
    (tmp_path / ".nvmrc").write_text("18.19.0\n")
    (tmp_path / ".tool-versions").write_text("golang 1.22.3\n")
    (tmp_path / "AGENTS.md").write_text(
        "# Agents\n\nNode 20.11.0 is pinned in `.nvmrc`.\n\n"
        "The Go toolchain is pinned in `.tool-versions` at 1.22.3.\n\n"
        "Ruby 3.3.0 is pinned in `.ruby-version`.\n"
    )
    block = scan_instruction_claims(tmp_path, ["AGENTS.md"])
    assert (block["total"], block["verified"], block["failed"]) == (3, 1, 2)
    assert sorted(_failures(block)) == [["AGENTS.md", 3, "pin"], ["AGENTS.md", 7, "pin"]]
    by_line = {f["line"]: f for f in block["failures"]}
    assert (by_line[3]["path"], by_line[3]["version"]) == (".nvmrc", "20.11.0")
    assert by_line[3]["reason"] == "pinned file does not contain the version"
    assert by_line[7]["path"] == ".ruby-version"
    assert by_line[7]["reason"] == "pinned file does not exist"


def test_pin_sentence_without_a_version_is_skipped() -> None:
    assert extract_claims("The toolchain is pinned in `.tool-versions`.\n") == []


def test_pin_sentence_with_two_versions_is_skipped_as_ambiguous() -> None:
    text = "Node 20.11.0 is pinned in `.nvmrc`, upgraded from 18.19.0.\n"
    assert extract_claims(text) == []


def test_claim_in_a_wrapped_paragraph_reports_the_line_the_sentence_starts_on() -> None:
    text = "# Agents\n\nKeep it small. The lint script\n`scripts/lint.sh` is enforced\nin CI.\n"
    claims = extract_claims(text)
    assert [(c.kind, c.line, c.path) for c in claims] == [("enforcement", 3, "scripts/lint.sh")]


def test_fenced_code_is_not_read_as_claims() -> None:
    text = "```\n`scripts/lint.sh` is enforced in CI.\n```\n"
    assert extract_claims(text) == []


def test_enforcement_needs_a_trigger_phrase_and_a_script_path() -> None:
    assert extract_claims("Run `scripts/lint.sh` before pushing.\n") == []
    assert extract_claims("Linting is enforced in CI.\n") == []
    # "CI" is a word, not a substring of another word.
    assert extract_claims("Run `scripts/lint.sh` for CIRCLE builds.\n") == []
    # A backticked workflow file is not a script the workflows would call.
    assert extract_claims("CI runs `.github/workflows/ci.yml`.\n") == []


@pytest.mark.parametrize("sentence", [
    "Do not edit `src/db/env.py`; CI will fail if you do.",
    "`src/index.ts` must compile before CI passes.",
    "`lib/scripts_helper.rb` is checked by the linter.",
])
def test_ordinary_source_files_are_not_enforcement_claims(sentence: str) -> None:
    assert extract_claims(sentence + "\n") == []


@pytest.mark.parametrize("path", ["scripts/gate.py", "bin/check.js", "tools/ci/lint.ts",
                                  "hack/verify.rb", "ops/deploy.sh"])
def test_script_paths_that_ci_invokes_are_enforcement_claims(path: str) -> None:
    claims = extract_claims(f"`{path}` is enforced in CI.\n")
    assert [c.path for c in claims] == [path]


@pytest.mark.parametrize("phrase", ["is enforced by the pipeline", "runs in the lint job",
                                    "is checked by the gate", "gates CI"])
def test_each_enforcement_trigger_phrase_makes_a_claim(phrase: str) -> None:
    claims = extract_claims(f"`./scripts/lint.sh` {phrase}.\n")
    assert [(c.kind, c.path) for c in claims] == [("enforcement", "scripts/lint.sh")]


def test_duplicate_file_through_a_symlink_is_scanned_once(repo: Path) -> None:
    (repo / "CLAUDE.md").write_text(ENFORCED)
    (repo / "AGENTS.md").symlink_to("CLAUDE.md")
    block = scan_instruction_claims(repo, ["CLAUDE.md", "AGENTS.md"])
    assert block["total"] == 1


def test_block_is_json_serialisable(repo: Path) -> None:
    (repo / "AGENTS.md").write_text(ENFORCED)
    json.dumps(scan_instruction_claims(repo, ["AGENTS.md"]))


def test_build_run_context_carries_the_block(tmp_path: Path) -> None:
    from assess_core import build_run_context

    (tmp_path / "AGENTS.md").write_text(ENFORCED)
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    (tmp_path / ".github" / "workflows" / "ci.yml").write_text(WORKFLOW_WITHOUT)
    ctx = build_run_context(repo_root=tmp_path, run_date="2026-09-18")
    assert _failures(ctx["instruction_claims"]) == [["AGENTS.md", 5, "enforcement"]]
    written = json.loads((tmp_path / ".assess" / "run-context.json").read_text())
    assert written["instruction_claims"]["failed"] == 1
