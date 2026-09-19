"""The assess-pr no-contributions scan: extract the marked bash block and run it.

The scan decides whether the end-of-run PR offer may target the upstream repo.
It lives as bash inside skills/assess-pr/SKILL.md, so the test drives the exact
text an agent would run rather than a paraphrase of it.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

ASSESS_PR_SKILL = Path(__file__).resolve().parents[2] / "assess-pr" / "SKILL.md"
START = "# no-contributions scan: start"
END = "# no-contributions scan: end"
STATEMENT = "This repository does not accept contributions. Please do not send a pull request."


def _scan_block() -> str:
    lines = ASSESS_PR_SKILL.read_text(encoding="utf-8").splitlines()
    start = lines.index(START)
    end = lines.index(END)
    assert end - start >= 2, "scan block must hold at least one line of bash"
    return "\n".join(lines[start : end + 1]) + "\n"


def _run_scan(repo_root: Path, var: str = "NO_CONTRIBUTIONS") -> str:
    script = f'REPO_ROOT="{repo_root}"\n{_scan_block()}printf %s "${var}"\n'
    result = subprocess.run(
        ["sh", "-c", script], capture_output=True, text=True, check=True
    )
    assert result.stderr == ""
    return result.stdout


@pytest.mark.parametrize(
    "files, expected",
    [
        ({"README.md": f"# App\n{STATEMENT}\n"}, "1"),
        ({"README.md": "# App\n", "CONTRIBUTING.md": f"# Contributing\n{STATEMENT}\n"}, "1"),
        ({"README.md": "# App\nContributions are welcome. Please open a pull request.\n"}, "0"),
        ({"README.md": "# App\nWe are not accepting pull requests at this time.\n"}, "1"),
        ({"README.md": "# App\nPull requests are not accepted.\n"}, "1"),
        ({"README.md": "# App\nPRs welcome! See CONTRIBUTING.md.\n"}, "0"),
        ({"README.md": "# App\nWe cannot accept contributions.\n"}, "1"),
        ({"README.md": "# App\nSorry, we can't accept pull requests.\n"}, "1"),
        ({"README.md": "# App\nThe team is unable to accept external contributions.\n"}, "1"),
        ({"README.md": "# App\nWe don\u2019t accept pull requests.\n"}, "1"),
        ({"README.md": "# App\nThis project does not accept unsolicited pull requests.\n"}, "1"),
        ({"CONTRIBUTING.md": "Please do not open a pull request without first opening an issue.\n"}, "0"),
        ({"CONTRIBUTING.md": "Do not submit a PR until all tests pass locally.\n"}, "0"),
        ({"CONTRIBUTING.md": "Please don't open a PR directly against main.\n"}, "0"),
        ({"CONTRIBUTING.md": "PRs are not accepted without a linked issue.\n"}, "0"),
        ({"CONTRIBUTING.md": "Please do not open a pull request for trivial typo fixes.\n"}, "0"),
        ({"CONTRIBUTING.md": "Do not submit a PR with unrelated changes.\n"}, "0"),
        ({"CONTRIBUTING.md": "Do not open a pull request from a fork of a fork.\n"}, "0"),
        ({"CONTRIBUTING.md": "Do not open PRs to the release branch.\n"}, "0"),
        ({"CONTRIBUTING.md": "Do not open a pull request if you have not signed the CLA.\n"}, "0"),
        ({"README.md": "# App\nDo not open a pull request; open an issue instead.\n"}, "1"),
        ({"README.md": "# App\nPlease don't send PRs\n"}, "1"),
        ({}, "0"),
    ],
    ids=["readme", "contributing", "welcome", "not-accepting-prs", "prs-not-accepted",
         "prs-welcome", "cannot", "cant", "unable-to", "curly-apostrophe", "unsolicited",
         "cond-without", "cond-until", "cond-directly", "cond-accepted-without", "cond-for", "cond-with", "cond-from", "cond-to", "cond-if",
         "imperative-semicolon", "imperative-eol", "no-docs"],
)
def test_scan_sets_no_contributions(tmp_path: Path, files: dict[str, str], expected: str) -> None:
    for name, body in files.items():
        (tmp_path / name).write_text(body, encoding="utf-8")
    assert _run_scan(tmp_path) == expected


def test_scan_keeps_the_matching_file_and_statement(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("# App\n", encoding="utf-8")
    (tmp_path / "CONTRIBUTING.md").write_text(f"# Contributing\n{STATEMENT}\n", encoding="utf-8")
    assert _run_scan(tmp_path, "NO_CONTRIBUTIONS_SOURCE") == "CONTRIBUTING.md"
    assert _run_scan(tmp_path, "NO_CONTRIBUTIONS_STATEMENT") == STATEMENT


def test_scan_reads_github_contributing_with_its_relative_path(tmp_path: Path) -> None:
    (tmp_path / ".github").mkdir()
    (tmp_path / ".github" / "CONTRIBUTING.md").write_text(f"{STATEMENT}\n", encoding="utf-8")
    assert _run_scan(tmp_path) == "1"
    assert _run_scan(tmp_path, "NO_CONTRIBUTIONS_SOURCE") == ".github/CONTRIBUTING.md"


def test_phase_2_variant_is_scoped_to_read_only_targets() -> None:
    text = ASSESS_PR_SKILL.read_text(encoding="utf-8")
    phase2 = text.split("## Phase 2", 1)[1].split("## Step 5", 1)[0]
    bullet = next(line for line in phase2.splitlines() if "no-contributions" in line)
    tail = bullet.split("offer the fork variant instead.", 1)[1]
    assert "read-only" in tail


def test_no_contributions_flow_never_references_the_upstream_pr_step() -> None:
    step5 = _step5()
    flow = step5.split("(no-contributions flow,", 1)[1].split("\n\n", 1)[0]
    assert "steps 1-2" not in flow
    assert "gh pr create --repo <owner>/<repo>" not in flow
    # The PR is created on the fork's own endpoint, never via a base-repo lookup.
    assert "repos/<fork-owner>/<repo>/pulls" in flow


def test_scan_leaves_source_empty_without_a_statement(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("# App\nPRs welcome!\n", encoding="utf-8")
    assert _run_scan(tmp_path, "NO_CONTRIBUTIONS_SOURCE") == ""
    assert _run_scan(tmp_path, "NO_CONTRIBUTIONS_STATEMENT") == ""


def test_push_capable_user_is_told_about_the_statement() -> None:
    text = ASSESS_PR_SKILL.read_text(encoding="utf-8")
    step5 = text.split("## Step 5", 1)[1].split("## Step 6", 1)[0]
    direct = next(line for line in step5.splitlines() if line.startswith("- `CAN_PUSH=1`"))
    assert "NO_CONTRIBUTIONS=1" in direct and "NO_CONTRIBUTIONS_SOURCE" in direct


def _step5() -> str:
    text = ASSESS_PR_SKILL.read_text(encoding="utf-8")
    return text.split("## Step 5", 1)[1].split("## Step 6", 1)[0]


def test_no_contributions_offer_needs_a_github_permission() -> None:
    # CAN_PUSH is 0 both for READ/TRIAGE and when gh returned nothing; only the
    # former can fork, so the no-contributions bullet must name the permission.
    bullet = next(line for line in _step5().splitlines()
                  if line.startswith("- `CAN_PUSH=0`") and "NO_CONTRIBUTIONS=1" in line)
    assert "`READ` / `TRIAGE`" in bullet
    no_remote = next(line for line in _step5().splitlines() if "`$PUSH_INFO` empty" in line)
    assert "every PR offer" in no_remote


def test_pr_body_template_is_shared_by_every_flow() -> None:
    step5 = _step5()
    flows = [m.start() for m in re.finditer(r"If the user \*\*selected the PR offer\*\*", step5)]
    assert len(flows) == 3
    footer = step5.index("plugin reference at the bottom")
    assert footer > flows[-1]
    footer_line = step5[: footer].rsplit("\n", 1)[1]
    assert not re.match(r"\s*\d+\.", footer_line), "template must not be a step of one flow"
    for name in ("direct", "fork", "no-contributions"):
        assert name in step5[step5.rfind("\n", 0, footer) : step5.index("\n", footer)]


def test_each_flow_numbers_its_steps_once() -> None:
    step5 = _step5()
    for block in re.split(r"If the user \*\*selected the PR offer\*\*", step5)[1:]:
        numbers = [int(n) for n in re.findall(r"^(\d+)\. ", block.split("\n\n", 1)[0], re.M)]
        assert numbers == list(range(1, len(numbers) + 1)), numbers


def test_scan_sits_in_step_5_before_the_offer_text() -> None:
    text = ASSESS_PR_SKILL.read_text(encoding="utf-8")
    step5 = text.split("## Step 5", 1)[1].split("## Step 6", 1)[0]
    assert START in step5 and END in step5


def test_step_5_replaces_upstream_offer_when_flag_is_set() -> None:
    text = ASSESS_PR_SKILL.read_text(encoding="utf-8")
    step5 = text.split("## Step 5", 1)[1].split("## Step 6", 1)[0]
    lowered = step5.lower()
    assert step5.count("NO_CONTRIBUTIONS") >= 3
    for phrase in ("inside your fork", "fork's default branch", "share the link"):
        assert phrase in lowered, phrase
    assert re.search(r"disabl[a-z]* actions", lowered)
    # The ordinary fork-to-upstream flow stays for READ/TRIAGE with no statement.
    assert "gh pr create --repo <owner>/<repo>" in step5
    phase2 = text.split("## Phase 2", 1)[1].split("## Step 5", 1)[0]
    assert "no-contributions" in phase2.lower()
