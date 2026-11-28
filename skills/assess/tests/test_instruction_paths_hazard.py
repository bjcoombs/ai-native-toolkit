"""An instruction file that every pull request check filters out holds the ``instructions`` gate (#857).

Every fixture is invented: workflow names, paths and files exist only here.
"""
from __future__ import annotations

from pathlib import Path

import pytest

import lib.scoring.level_tally as lt
from lib.ci.ci_hazards import NO_PR_CHECKS, scan_ci_hazards
from lib.ci.ci_path_filters import admits, glob_regex, pr_filters
from lib.counts_only.content_free import counts_only

HAZARD = "ci-instructions-unchecked.1"

IGNORED = """name: checks
on:
  pull_request:
    paths-ignore: ['AGENTS.md']
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - run: pytest
"""

SCOPED = """name: checks
on:
  pull_request:
    paths:
      - 'src/**'
      - 'tests/**'
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - run: pytest
"""

PLAIN = """name: ci
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - run: pytest
"""


def _repo(tmp_path: Path, files: dict[str, str]) -> Path:
    for rel, text in {"AGENTS.md": "# Agents\n\nRun `pytest`.\n", "src/app.py": "x = 1\n", **files}.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return tmp_path


def _result(block: dict[str, object]) -> lt.LevelResult:
    data = {"layer_grades": {"instruction_file_present": True}, "ci_checks": {"enforced_check_runs": True},
            "ci_hazards": block}
    return lt.level_result(lt.default_model(), data)


# --- the success criteria of #857 ---------------------------------------------------------


def test_paths_ignore_on_the_only_pr_workflow_holds_the_instructions_gate(tmp_path: Path) -> None:
    block = scan_ci_hazards(_repo(tmp_path / "hazard", {".github/workflows/checks.yml": IGNORED}))
    assert block["instructions_unchecked"] == 1
    assert block["unchecked_instruction_files"] == ["AGENTS.md"]
    assert ".github/workflows/checks.yml" in block["ci_hazard_files"]
    clean = _result(scan_ci_hazards(_repo(tmp_path / "clean", {".github/workflows/ci.yml": PLAIN})))  # type: ignore[arg-type]
    present = _result(block)  # type: ignore[arg-type]
    assert clean["step"] >= 2 and present["step"] == 1
    assert present["tally"] == clean["tally"]
    assert present["next_step"] is not None
    assert present["next_step"]["blockers"] == [
        {"kind": "gate", "gate": "instructions", "condition": HAZARD, "state": "met", "polarity": "hazard"}]
    (entry,) = present["hazards"]
    assert entry["id"] == HAZARD and entry["gate"] == "instructions" and entry["fix"]


def test_an_instruction_file_outside_every_paths_filter_raises_it(tmp_path: Path) -> None:
    other = SCOPED.replace("'tests/**'", "'lib/**'")
    block = scan_ci_hazards(_repo(tmp_path, {".github/workflows/checks.yml": SCOPED,
                                             ".github/workflows/more.yml": other}))
    assert (block["instructions_unchecked"], block["unchecked_instruction_files"]) == (1, ["AGENTS.md"])


def test_one_pr_workflow_without_path_filters_clears_it(tmp_path: Path) -> None:
    block = scan_ci_hazards(_repo(tmp_path, {".github/workflows/checks.yml": IGNORED,
                                             ".github/workflows/ci.yml": PLAIN}))
    assert (block["instructions_unchecked"], block["unchecked_instruction_files"]) == (0, [])
    assert block["ci_hazard_files"] == []
    assert not _result(block).get("hazards")  # type: ignore[arg-type]


# --- the reader ---------------------------------------------------------------------------


def test_no_pr_check_workflow_reads_not_measured(tmp_path: Path) -> None:
    nightly = PLAIN.replace("on: [push, pull_request]", "on: [schedule]")
    labeler = IGNORED.replace("      - run: pytest\n", "      - run: echo label\n")
    block = scan_ci_hazards(_repo(tmp_path, {".github/workflows/n.yml": nightly, ".github/workflows/l.yml": labeler}))
    assert (block["instructions_unchecked"], block["instructions_unchecked_reason"]) == (None, NO_PR_CHECKS)
    assert not _result(block).get("hazards")  # type: ignore[arg-type]


def test_an_unfiltered_pr_workflow_whose_check_is_not_seen_clears_it(tmp_path: Path) -> None:
    caller = "on: [pull_request]\njobs:\n  test:\n    uses: org-shared/workflows/.github/workflows/t.yml@v1\n"
    block = scan_ci_hazards(_repo(tmp_path, {".github/workflows/checks.yml": IGNORED,
                                             ".github/workflows/ci.yml": caller}))
    assert (block["instructions_unchecked"], block["unchecked_instruction_files"]) == (0, [])


def test_a_paths_filter_that_names_the_file_clears_it(tmp_path: Path) -> None:
    text = SCOPED.replace("      - 'tests/**'\n", "      - 'tests/**'\n      - '*.md'\n")
    assert scan_ci_hazards(_repo(tmp_path, {".github/workflows/checks.yml": text}))["instructions_unchecked"] == 0


def test_only_tracked_instruction_files_count(tmp_path: Path) -> None:
    repo = _repo(tmp_path, {".github/workflows/checks.yml": SCOPED, "CLAUDE.md": "# Claude\n\nRun pytest.\n"})
    block = scan_ci_hazards(repo)
    assert block["unchecked_instruction_files"] == ["AGENTS.md", "CLAUDE.md"]


@pytest.mark.parametrize(("key", "patterns", "path", "expected"), [
    ("paths-ignore", ["**.md"], "AGENTS.md", False),
    ("paths-ignore", ["*.md"], "docs/AGENTS.md", True),        # * stays within one segment
    ("paths-ignore", ["**/AGENTS.md"], "AGENTS.md", False),    # **/ matches at the root
    ("paths-ignore", ["**.md", "!AGENTS.md"], "AGENTS.md", True),
    ("paths", ["**", "!*.md"], "AGENTS.md", False),
    ("paths", ["src/**"], "AGENTS.md", False),
    ("paths", ["src/**", "AGENTS.md"], "AGENTS.md", True),
])
def test_filters_read_as_github_does(key: str, patterns: list[str], path: str, expected: bool) -> None:
    text = f"on:\n  pull_request:\n    {key}: [{', '.join(repr(p) for p in patterns)}]\n"
    (found,) = pr_filters(text) or []
    assert admits(found, path) is expected


@pytest.mark.parametrize("on", [
    "on:\n  pull_request:\n    paths: ['src/[ab]/**']\n",            # a pattern not read
    "on:\n  pull_request:\n    paths: ['src/**']\n    paths-ignore: ['x']\n",  # both: GitHub rejects it
    "on: {pull_request: {paths: ['src/**']}}\n",                    # a flow mapping
    "on:\n  pull_request:\n    paths: ['src/**']\n  pull_request_target:\n",  # a second PR event runs on all
    "on:\n  pull_request:\n    paths: []\n",                          # an empty filter names nothing
])
def test_a_filter_not_read_lets_every_file_through(on: str) -> None:
    assert any(admits(found, "AGENTS.md") for found in pr_filters(on) or [])


@pytest.mark.parametrize(("key", "expected"), [("paths", True), ("paths-ignore", True)])
def test_a_trailing_comment_on_a_list_item_is_dropped(key: str, expected: bool) -> None:
    item = "'**'  # everything" if key == "paths" else "'src/**' # code"
    (found,) = pr_filters(f"on:\n  pull_request:\n    {key}:\n      - {item}\n") or []
    assert admits(found, "CLAUDE.md") is expected


def test_a_merge_queue_run_clears_it(tmp_path: Path) -> None:
    queued = IGNORED.replace("    paths-ignore: ['AGENTS.md']\n", "    paths-ignore: ['AGENTS.md']\n  merge_group:\n")
    assert scan_ci_hazards(_repo(tmp_path, {".github/workflows/checks.yml": queued}))["instructions_unchecked"] == 0


def test_a_separate_merge_queue_check_workflow_clears_it(tmp_path: Path) -> None:
    queue = PLAIN.replace("on: [push, pull_request]", "on: [merge_group]")
    block = scan_ci_hazards(_repo(tmp_path, {".github/workflows/checks.yml": IGNORED,
                                             ".github/workflows/mq.yml": queue}))
    assert (block["instructions_unchecked"], block["unchecked_instruction_files"]) == (0, [])


def test_glob_regex_refuses_what_it_does_not_read() -> None:
    assert glob_regex("a?b") is None and glob_regex("") is None
    regex = glob_regex("docs/*")
    assert regex is not None and regex.fullmatch("docs/AGENTS.md") and not regex.fullmatch("docs/x/AGENTS.md")


def test_the_block_survives_counts_only_with_its_paths(tmp_path: Path) -> None:
    block = scan_ci_hazards(_repo(tmp_path, {".github/workflows/checks.yml": IGNORED}))
    kept = counts_only({"ci_hazards": block})["ci_hazards"]
    assert kept["instructions_unchecked"] == 1 and kept["unchecked_instruction_files"] == ["AGENTS.md"]
    assert kept["instructions_unchecked_reason"] is None
