"""The assess-pr no-contributions scan: extract the marked bash block and run it.

The scan decides whether the end-of-run PR offer may target the upstream repo.
It lives as bash inside skills/assess-pr/SKILL.md, so the test drives the exact
text an agent would run rather than a paraphrase of it.
"""
from __future__ import annotations

import re
import shutil
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
        ({"README.md": "# App\nWe are not currently accepting contributions.\n"}, "1"),
        ({"README.md": "# App\nWe are not accepting new contributions.\n"}, "1"),
        ({"README.md": "# App\nThis repo does not accept community contributions.\n"}, "1"),
        ({"README.md": "# App\nThis repo does not accept a pull request from anyone.\n"}, "1"),
        # Word boundary after the noun: "prs?" must not match the start of an
        # ordinary word once the modifier slot lets any word precede it.
        ({"README.md": "# App\nContributions welcome! The API does not accept a promise, only a value.\n"}, "0"),
        ({"README.md": "# App\nThis endpoint does not accept preflight requests.\n"}, "0"),
        ({"README.md": "# App\nWe do not accept private forks of the config.\n"}, "0"),
        ({"README.md": "# App\nThe daemon does not accept process signals.\n"}, "0"),
        ({"README.md": "# App\nThe loader does not accept project files.\n"}, "0"),
        ({"README.md": "# App\nThe CLI does not accept provided defaults.\n"}, "0"),
        ({"README.md": "# App\nWe do not accept prior versions of the schema.\n"}, "0"),
        ({"README.md": "# App\nDo not open private issues.\n"}, "0"),
        ({"README.md": "# App\nWe do not accept PRs.\n"}, "1"),
        ({"README.md": "# App\nWe are not accepting PRs right now.\n"}, "1"),
        ({"README.md": "# App\nWe do not accept PRs\n"}, "1"),
        ({}, "0"),
    ],
    ids=["readme", "contributing", "welcome", "not-accepting-prs", "prs-not-accepted",
         "prs-welcome", "cannot", "cant", "unable-to", "curly-apostrophe", "unsolicited",
         "cond-without", "cond-until", "cond-directly", "cond-accepted-without", "cond-for", "cond-with", "cond-from", "cond-to", "cond-if",
         "imperative-semicolon", "imperative-eol", "not-currently-accepting", "not-accepting-new",
         "community-contributions", "singular-pull-request",
         "fp-promise", "fp-preflight", "fp-private", "fp-process", "fp-project", "fp-provide",
         "fp-prior", "fp-imperative-private", "accept-prs-dot", "accepting-prs-right-now",
         "accept-prs-eol", "no-docs"],
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


def test_scan_reads_docs_contributing_with_its_relative_path(tmp_path: Path) -> None:
    # GitHub also recognises docs/CONTRIBUTING.md; a refusal stated only there
    # must still suppress the fork-to-upstream offer.
    (tmp_path / "README.md").write_text("# App\n", encoding="utf-8")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "CONTRIBUTING.md").write_text(f"{STATEMENT}\n", encoding="utf-8")
    assert _run_scan(tmp_path) == "1"
    assert _run_scan(tmp_path, "NO_CONTRIBUTIONS_SOURCE") == "docs/CONTRIBUTING.md"


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
    assert "repos/$FORK_SLUG/pulls" in flow


def test_reusing_the_current_fork_needs_push_access() -> None:
    # A READ clone of someone else's fork is also IS_FORK=true; it must fork
    # again rather than push to a repo the user cannot write to.
    flow = _step5().split("(no-contributions flow,", 1)[1].split("\n\n", 1)[0]
    assert "`IS_OWN_FORK=true` and `CAN_PUSH=1`" in flow


def _own_fork_block() -> str:
    lines = _step5().splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("VIEWER_LC="))
    end = next(i for i in range(start, len(lines)) if lines[i] == "fi")
    return "\n".join(lines[start : end + 1]) + "\n"


@pytest.mark.parametrize(
    "viewer, owner, is_fork, expected",
    [
        ("alice", "alice", "true", "true"),
        ("Alice", "alice", "true", "true"),
        # Push-capable collaborator on another user's fork: not their fork.
        ("bob", "alice", "true", "false"),
        ("alice", "alice", "false", "false"),
        # gh api user failed: never assume ownership.
        ("", "alice", "true", "false"),
    ],
    ids=["own", "own-case", "collaborator", "not-a-fork", "no-viewer"],
)
def test_is_own_fork_compares_viewer_with_fork_owner(
    tmp_path: Path, viewer: str, owner: str, is_fork: str, expected: str
) -> None:
    fake = tmp_path / "gh"
    body = f"echo '{{\"login\": \"{viewer}\"}}'" if viewer else "exit 1"
    fake.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
    fake.chmod(0o755)
    push_info = f'{{"isFork": {is_fork}, "owner": {{"login": "{owner}"}}}}'
    script = f"IS_FORK={is_fork}\nPUSH_INFO='{push_info}'\n{_own_fork_block()}printf %s \"$IS_OWN_FORK\"\n"
    result = subprocess.run(
        ["sh", "-c", script], capture_output=True, text=True, check=True,
        env={"PATH": f"{tmp_path}:/usr/bin:/bin:/opt/homebrew/bin:/usr/local/bin"},
    )
    assert result.stdout == expected


def test_reusing_the_current_fork_needs_the_viewer_to_own_it() -> None:
    step5 = _step5()
    assert "owner" in step5.split("PUSH_INFO=", 1)[1].split("\n", 1)[0]
    flow = step5.split("(no-contributions flow,", 1)[1].split("\n\n", 1)[0]
    assert "any clone of someone else's fork, whatever the permission" in flow
    assert "skipping its fork step" not in step5


def test_fork_pr_is_based_on_what_was_assessed() -> None:
    # A pre-existing fork's default branch can differ from the assessed
    # checkout; the PR must not carry the commits in between.
    flow = _step5().split("(no-contributions flow,", 1)[1].split("\n\n", 1)[0]
    pulls = flow.index("repos/$FORK_SLUG/pulls")
    for needle in ("BASE_SHA=", "assess/base-<YYYY-MM-DD>", "tell the user"):
        assert needle in flow[:pulls], needle
    assert flow.index("BASE_SHA=") < flow.index("FORK_BRANCH=assess/base-")


def test_fork_pr_url_check_is_host_agnostic() -> None:
    flow = _step5().split("(no-contributions flow,", 1)[1].split("\n\n", 1)[0]
    assert "https://github.com/$FORK_SLUG" not in flow
    assert "/$FORK_SLUG/pull/<number>" in flow


def test_fork_pr_body_file_is_written_before_it_is_used() -> None:
    flow = _step5().split("(no-contributions flow,", 1)[1].split("\n\n", 1)[0]
    assert flow.index("<body-file>") < flow.index("body=@<body-file>")
    assert "temp file" in flow[: flow.index("body=@<body-file>")]


def test_fork_slug_is_derived_not_guessed() -> None:
    step5 = _step5()
    assert "<fork-owner>" not in step5
    flow = step5.split("(no-contributions flow,", 1)[1].split("\n\n", 1)[0]
    assert "FORK_SLUG=" in flow
    assert "repos/$FORK_SLUG/actions/permissions" in flow


def test_fork_clone_of_a_no_contributions_repo_stays_in_the_fork() -> None:
    # In a clone of the user's own fork, viewerPermission is ADMIN (CAN_PUSH=1)
    # and a bare `gh pr create` targets the parent: the statement must still win.
    step5 = _step5()
    assert "isFork" in step5.split("PUSH_INFO=", 1)[1].split("\n", 1)[0]
    assert "IS_FORK=" in step5
    direct = next(line for line in step5.splitlines() if line.startswith("- `CAN_PUSH=1`"))
    assert "IS_FORK=true" in direct and "no-contributions flow" in direct
    flow_head = step5.split("(no-contributions flow,", 1)[1].split("\n", 1)[0]
    assert "IS_FORK=true" in flow_head


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
    assert step5.index(END) < step5.index("Interpret the result")


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


@pytest.mark.skipif(shutil.which("zsh") is None, reason="zsh not installed")
@pytest.mark.parametrize("body, expected", [(STATEMENT, "1"), ("PRs welcome!", "0")])
def test_scan_runs_under_zsh(tmp_path: Path, body: str, expected: str) -> None:
    # Agents often run the block in the user's zsh, where an unbraced
    # "$var[" is a subscript, not a variable followed by a bracket class.
    (tmp_path / "README.md").write_text(f"# App\n{body}\n", encoding="utf-8")
    script = f'REPO_ROOT="{tmp_path}"\n{_scan_block()}printf %s "$NO_CONTRIBUTIONS"\n'
    result = subprocess.run(["zsh", "-c", script], capture_output=True, text=True, check=True)
    assert result.stderr == ""
    assert result.stdout == expected
