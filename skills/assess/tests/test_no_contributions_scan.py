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


def _run_scan(repo_root: Path) -> str:
    script = f'REPO_ROOT="{repo_root}"\n{_scan_block()}printf %s "$NO_CONTRIBUTIONS"\n'
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
        ({}, "0"),
    ],
    ids=["readme", "contributing", "welcome", "not-accepting-prs", "prs-not-accepted",
         "prs-welcome", "no-docs"],
)
def test_scan_sets_no_contributions(tmp_path: Path, files: dict[str, str], expected: str) -> None:
    for name, body in files.items():
        (tmp_path / name).write_text(body, encoding="utf-8")
    assert _run_scan(tmp_path) == expected


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
