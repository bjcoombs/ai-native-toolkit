"""Tests for lib/instruction_claims.py - verifying claims in agent instruction files.

An instruction file that says "`scripts/check-x.sh` is enforced in CI" or "Node
20.11.0 is pinned in `.nvmrc`" makes a checkable promise. The scan extracts each
such sentence and checks it against the repository, so a claim nothing backs
surfaces as a failure with its file and line.
"""
from __future__ import annotations

import json
import os
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


def test_heading_directly_above_prose_does_not_lend_its_words_or_line() -> None:
    # No blank line under the heading: the `CI` in it must not trigger the
    # sentence below, and a claim there reports its own line, not the heading's.
    assert extract_claims("## CI\n`scripts/lint.sh` must pass.\n") == []
    claims = extract_claims("# Agents\n## Lint\n`scripts/lint.sh` is enforced in CI.\n")
    assert [(c.kind, c.line) for c in claims] == [("enforcement", 3)]


@pytest.fixture
def counted(tmp_path: Path) -> Path:
    (tmp_path / "supabase" / "tests").mkdir(parents=True)
    for i in range(177):
        (tmp_path / "supabase" / "tests" / f"t{i}.sql").write_text("")
    (tmp_path / "cmds").mkdir()
    for i in range(7):
        (tmp_path / "cmds" / f"c{i}.md").write_text("# cmd\n")
    return tmp_path


def test_count_claim_far_from_the_pattern_fails_with_both_numbers(counted: Path) -> None:
    (counted / "AGENTS.md").write_text(
        "# Agents\n\nThere are 43 pgTAP suites matching `supabase/tests/*.sql`.\n")
    block = scan_instruction_claims(counted, ["AGENTS.md"])
    assert (block["total"], block["verified"], block["failed"]) == (1, 0, 1)
    failure = block["failures"][0]
    assert [failure["file"], failure["line"], failure["kind"]] == ["AGENTS.md", 3, "count"]
    assert (failure["claimed"], failure["actual"]) == (43, 177)
    assert failure["path"] == "supabase/tests/*.sql"


def test_count_sentence_without_a_backticked_pattern_is_no_claim(counted: Path) -> None:
    (counted / "AGENTS.md").write_text("# Agents\n\nWe maintain 43 pgTAP suites.\n")
    assert scan_instruction_claims(counted, ["AGENTS.md"]) == {
        "total": 0, "verified": 0, "failed": 0, "failures": []}


def test_count_within_ten_percent_or_two_verifies(counted: Path) -> None:
    # 170 vs 177 is inside 10%; 5 vs 7 is a difference of exactly 2; 150 vs 177 is not.
    (counted / "AGENTS.md").write_text(
        "# Agents\n\nThere are 170 pgTAP files matching `supabase/tests/*.sql`.\n\n"
        "The plugin ships 5 commands in `cmds/*.md`.\n\n"
        "The 150 migrations live in `supabase/tests/*.sql`.\n")
    block = scan_instruction_claims(counted, ["AGENTS.md"])
    assert (block["total"], block["verified"], block["failed"]) == (3, 2, 1)
    failure = block["failures"][0]
    assert (failure["line"], failure["claimed"], failure["actual"]) == (7, 150, 177)


def test_count_tolerance_is_the_larger_of_ten_percent_or_two() -> None:
    from lib.instruction_claims import count_within_tolerance

    assert count_within_tolerance(5, 7)
    assert not count_within_tolerance(4, 7)
    assert count_within_tolerance(100, 110)
    assert not count_within_tolerance(100, 112)


def test_count_pattern_matching_nothing_in_an_existing_directory_fails_with_actual_zero(
        tmp_path: Path) -> None:
    (tmp_path / "tests").mkdir()
    (tmp_path / "AGENTS.md").write_text("There are 12 suites in `tests/*.sql`.\n")
    failure = scan_instruction_claims(tmp_path, ["AGENTS.md"])["failures"][0]
    assert (failure["kind"], failure["claimed"], failure["actual"]) == ("count", 12, 0)


def test_count_pattern_whose_directory_is_missing_is_unverifiable_not_failed(
        tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("There are 12 suites in `tests/*.sql`.\n")
    assert scan_instruction_claims(tmp_path, ["AGENTS.md"])["total"] == 0


def _thirty_pages(root: Path) -> None:
    for i in range(30):
        sub = root / "docs" / ("a" if i % 2 else "b/c")
        sub.mkdir(parents=True, exist_ok=True)
        (sub / f"p{i}.md").write_text("")


def test_count_recursive_glob_counts_files_not_directories(tmp_path: Path) -> None:
    _thirty_pages(tmp_path)
    for i in range(10):  # directories whose names match the pattern
        (tmp_path / "docs" / f"legacy{i}.md").mkdir()
        (tmp_path / "docs" / f"legacy{i}.md" / "keep").write_text("")
    (tmp_path / "AGENTS.md").write_text("The 30 pages under `docs/**/*.md` are the map.\n")
    block = scan_instruction_claims(tmp_path, ["AGENTS.md"])
    assert (block["total"], block["verified"]) == (1, 1)


def test_count_skips_git_metadata_and_matches_outside_the_repo(tmp_path: Path) -> None:
    repo, outside = tmp_path / "repo", tmp_path / "outside"
    _thirty_pages(repo)
    (repo / "docs" / ".git").mkdir()
    outside.mkdir()
    for i in range(10):
        (repo / "docs" / ".git" / f"g{i}.md").write_text("")
        (outside / f"o{i}.md").write_text("")
    (repo / "docs" / "vendor").symlink_to(outside)
    (repo / "AGENTS.md").write_text("The 30 pages under `docs/**/*.md` are the map.\n")
    block = scan_instruction_claims(repo, ["AGENTS.md"])
    assert (block["total"], block["verified"]) == (1, 1)


def test_count_does_not_count_a_symlinked_file_outside_the_repo(tmp_path: Path) -> None:
    repo, outside = tmp_path / "repo", tmp_path / "outside"
    _thirty_pages(repo)
    outside.mkdir()
    for i in range(10):
        (outside / f"o{i}.md").write_text("")
        (repo / "docs" / "a" / f"link{i}.md").symlink_to(outside / f"o{i}.md")
    (repo / "AGENTS.md").write_text("The 30 pages under `docs/**/*.md` are the map.\n")
    block = scan_instruction_claims(repo, ["AGENTS.md"])
    assert (block["total"], block["verified"]) == (1, 1)


@pytest.mark.parametrize("pattern", ["skills/*", "skills/*/"])
def test_count_of_directories_is_unverifiable_not_a_zero_count(tmp_path: Path, pattern: str) -> None:
    for i in range(12):
        (tmp_path / "skills" / f"s{i}").mkdir(parents=True)
        (tmp_path / "skills" / f"s{i}" / "SKILL.md").write_text("")
    (tmp_path / "AGENTS.md").write_text(f"The 12 skills live in `{pattern}`.\n")
    assert scan_instruction_claims(tmp_path, ["AGENTS.md"])["total"] == 0


def test_count_ignores_tool_output_and_dependency_trees_below_the_pattern(tmp_path: Path) -> None:
    _thirty_pages(tmp_path)
    for tree in (".assess/hotspots", "node_modules/pkg", ".venv/lib"):
        (tmp_path / tree).mkdir(parents=True)
        for i in range(10):
            (tmp_path / tree / f"x{i}.md").write_text("")
    (tmp_path / "AGENTS.md").write_text("The 30 pages under `**/*.md` are the map.\n")
    # AGENTS.md itself is the 31st page; within tolerance.
    block = scan_instruction_claims(tmp_path, ["AGENTS.md"])
    assert (block["total"], block["verified"]) == (1, 1)


def test_count_inside_an_excluded_directory_named_on_purpose_still_counts(tmp_path: Path) -> None:
    (tmp_path / "vendor" / "docs").mkdir(parents=True)
    for i in range(30):
        (tmp_path / "vendor" / "docs" / f"v{i}.md").write_text("")
    (tmp_path / "AGENTS.md").write_text("We vendor 12 pages in `vendor/docs/*.md`.\n")
    failure = scan_instruction_claims(tmp_path, ["AGENTS.md"])["failures"][0]
    assert (failure["claimed"], failure["actual"]) == (12, 30)


@pytest.mark.skipif(not hasattr(os, "geteuid") or os.geteuid() == 0,
                    reason="root reads a directory whatever its mode")
def test_count_with_an_unreadable_subtree_is_unverifiable(tmp_path: Path) -> None:
    _thirty_pages(tmp_path)
    locked = tmp_path / "docs" / "b"
    locked.chmod(0)
    try:
        (tmp_path / "AGENTS.md").write_text("The 30 pages under `docs/**/*.md` are the map.\n")
        assert scan_instruction_claims(tmp_path, ["AGENTS.md"])["total"] == 0
    finally:
        locked.chmod(0o755)


@pytest.mark.parametrize("sentence", [
    "Keep every page under `docs/**/*.md` below 500 lines.",
    "Keep at most 10 files in `x/*.md`.",
    "Cap `src/**/*.ts` at 80 columns.",
    "Review any change to `skills/*/SKILL.md` within 3 days.",
    "Allow no more than 5 pages in `docs/*.md`.",
])
def test_count_threshold_integer_is_not_a_count_claim(sentence: str) -> None:
    assert [c for c in extract_claims(sentence + "\n") if c.kind == "count"] == []


@pytest.mark.parametrize("sentence", [
    "Indent `scripts/*.sh` with 4 spaces.",
    "Cap `src/**/*.ts` at 15 cyclomatic complexity.",
    "Run the suite 2 times before touching `tests/*.py`.",
    # The pattern before the integer, even with a linking word.
    "Files in `docs/*.md` number 30.",
])
def test_count_needs_the_integer_then_a_linking_word_before_the_pattern(sentence: str) -> None:
    assert [c for c in extract_claims(sentence + "\n") if c.kind == "count"] == []


@pytest.mark.parametrize("sentence, claimed", [
    ("There are 43 pgTAP suites matching `supabase/tests/*.sql`.", 43),
    ("There are 170 pgTAP files matching `supabase/tests/*.sql`.", 170),
    ("The plugin ships 5 commands in `cmds/*.md`.", 5),
    ("The 150 migrations live in `supabase/tests/*.sql`.", 150),
])
def test_count_contract_sentences_pass_the_order_gate(sentence: str, claimed: int) -> None:
    claims = extract_claims(sentence + "\n")
    assert [(c.kind, c.fields) for c in claims] == [("count", {"claimed": claimed})]


def test_count_claim_without_a_unit_or_comparator_is_still_extracted() -> None:
    claims = extract_claims("There are 43 pgTAP suites matching `supabase/tests/*.sql`.\n")
    assert [(c.kind, c.fields) for c in claims] == [("count", {"claimed": 43})]


def test_count_non_recursive_pattern_over_files_and_directories_is_unverifiable(
        tmp_path: Path) -> None:
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "README.md").write_text("")
    for i in range(12):
        (tmp_path / "docs" / f"guide{i}").mkdir()
    (tmp_path / "AGENTS.md").write_text("The 12 guides live in `docs/*`.\n")
    assert scan_instruction_claims(tmp_path, ["AGENTS.md"])["total"] == 0


def test_count_non_recursive_pattern_over_files_only_still_counts(tmp_path: Path) -> None:
    (tmp_path / "docs").mkdir()
    for i in range(30):
        (tmp_path / "docs" / f"g{i}").write_text("")
    (tmp_path / "AGENTS.md").write_text("The 12 guides live in `docs/*`.\n")
    failure = scan_instruction_claims(tmp_path, ["AGENTS.md"])["failures"][0]
    assert (failure["claimed"], failure["actual"]) == (12, 30)


def test_count_glob_error_is_unverifiable_not_a_zero_count(
        counted: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(self: Path, pattern: str) -> list[Path]:
        raise ValueError("Invalid pattern")

    monkeypatch.setattr(Path, "glob", broken)
    (counted / "AGENTS.md").write_text("There are 43 suites in `supabase/tests/*.sql`.\n")
    assert scan_instruction_claims(counted, ["AGENTS.md"])["total"] == 0


@pytest.mark.parametrize("sentence", [
    # A path with no wildcard: a directory may hold files or subdirectories.
    "The 12 skills live in `skills/`.",
    # Two numbers: which one is the count is a guess.
    "Keep 5 of the 7 commands in `cmds/*.md`.",
    # Two patterns: which one the number counts is a guess.
    "There are 7 commands in `cmds/*.md` and `extra/*.md`.",
    # A version, a percentage or a number inside the backticks is not a count.
    "Node 20.11.0 builds `cmds/*.md`.",
    "Keep 80% coverage in `cmds/*.md`.",
    "Run `ls cmds/*.md | head -3` first.",
    # A pattern that leaves the repository is not counted.
    "There are 3 files in `../other/*.md`.",
    "There are 3 files in `/etc/*.conf`.",
    "There are 3 files in `C:\\logs\\*.txt`.",
    # Not path-shaped: code, placeholders, flags.
    "All 3 helpers take `**kwargs`.",
    "All 3 helpers take `*args`.",
    "Write `?` for 3 unless known.",
    "Pass `--only=*` to run 5 checks.",
    # A year is not a count.
    "Since 2024 every migration lives in `supabase/migrations/*.sql`.",
])
def test_count_claim_is_skipped_when_the_sentence_is_ambiguous(sentence: str) -> None:
    assert [c for c in extract_claims(sentence + "\n") if c.kind == "count"] == []


def test_count_claim_is_extracted_with_its_number_and_pattern() -> None:
    claims = extract_claims("# Agents\n\nThe plugin ships 5 commands in `cmds/*.md`.\n")
    assert [(c.kind, c.line, c.path, c.fields) for c in claims] == [
        ("count", 3, "cmds/*.md", {"claimed": 5})]
