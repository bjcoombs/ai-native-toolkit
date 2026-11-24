"""Tests for lib/agent_outcome.py: the revert and rework rate of agent commits (#1085).

Real git fixtures with fixed dates: three agent commits with one reverted read a
revert share of one in three; a hand-made inverse diff counts as a restore; a
line rewritten inside the rework span counts, one rewritten after it does not;
unknown reads null with a reason; counts-only output keeps every count and no
sha, path or identity.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from lib import agent_outcome
from lib.agent_outcome import (
    LIMITS,
    REWORK_DAYS,
    AgentOutcomeBlock,
    scan_agent_outcome,
)
from lib.ai_usage import scan_ai_usage
from lib.counts_only.content_free import counts_only_scans
from lib.scan_registry import SCANS

CLAUDE = "Claude Helper <noreply@anthropic.com>"
DAY = 86400
T0 = 1_700_000_000  # a fixed epoch: the fixtures never depend on today


def _git(repo: Path, *args: str, at: int = T0) -> str:
    stamp = f"@{at} +0000"
    env = {**os.environ, "GIT_AUTHOR_DATE": stamp, "GIT_COMMITTER_DATE": stamp}
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True,
                          text=True, encoding="utf-8", errors="replace", env=env).stdout


def _commit(repo: Path, path: str, text: str, at: int, agent: bool = False,
            message: str | None = None) -> str:
    (repo / path).parent.mkdir(parents=True, exist_ok=True)
    (repo / path).write_text(text, encoding="utf-8")
    _git(repo, "add", path, at=at)
    msg = message or f"change {path}"
    if agent:
        msg += f"\n\nCo-authored-by: {CLAUDE}"
    _git(repo, "commit", "-q", "-m", msg, at=at)
    return _git(repo, "rev-parse", "HEAD").strip()


def _init(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    r.mkdir()
    _git(r, "init", "-q", "-b", "main")
    _git(r, "config", "user.name", "Ada Person")
    _git(r, "config", "user.email", "ada.person@example.org")
    _commit(r, "README.md", "readme\n", T0)
    return r


@pytest.fixture
def reverted_repo(tmp_path: Path) -> tuple[Path, str]:
    """Three agent commits, the second reverted by ``git revert``."""
    r = _init(tmp_path)
    _commit(r, "a.txt", "a\n", T0 + 1 * DAY, agent=True)
    b = _commit(r, "b.txt", "b\n", T0 + 2 * DAY - 3600, agent=True)
    _commit(r, "c.txt", "c\n", T0 + 3 * DAY, agent=True)
    _git(r, "revert", "--no-edit", b, at=T0 + 4 * DAY)
    return r, b


def _ok(block: object) -> AgentOutcomeBlock:
    assert isinstance(block, dict) and block["available"] is True
    return block  # type: ignore[return-value]


def test_three_agent_commits_one_reverted_reads_one_in_three(reverted_repo: tuple[Path, str]) -> None:
    repo, _ = reverted_repo
    block = _ok(scan_agent_outcome(repo, None, None))
    assert block["agent_commits"] == 3
    assert block["reverted_commits"] == 1
    assert block["reverted_by_message"] == 1
    assert block["revert_share"] == round(1 / 3, 4)
    assert block["revert_null_reason"] is None
    assert block["informational"] is True
    assert block["agent_rule"] == "trailer_or_author"
    assert block["window"] == "all-time" and block["since"] is None
    assert block["limits"] == list(LIMITS)


def test_agent_commits_match_ai_usage_either_count(reverted_repo: tuple[Path, str]) -> None:
    repo, _ = reverted_repo
    usage = scan_ai_usage(repo, None, None)
    assert usage["available"] is True
    assert _ok(scan_agent_outcome(repo, None, None))["agent_commits"] == usage["agent_either_commits"]


def test_a_hand_made_inverse_diff_counts_as_a_restore(tmp_path: Path) -> None:
    r = _init(tmp_path)
    _commit(r, "README.md", "readme\nagent line\n", T0 + DAY, agent=True)
    _commit(r, "README.md", "readme\n", T0 + 2 * DAY, message="tidy readme")
    block = _ok(scan_agent_outcome(r, None, None))
    assert block["agent_commits"] == 1
    assert block["reverted_by_message"] == 0
    assert block["reverted_by_restore"] == 1
    assert block["revert_share"] == 1.0


def test_an_earlier_inverse_is_not_a_restore(tmp_path: Path) -> None:
    """An agent commit re-adding a line an earlier commit removed is not reverted."""
    r = _init(tmp_path)
    _commit(r, "README.md", "readme\nline\n", T0 + DAY)
    _commit(r, "README.md", "readme\n", T0 + 2 * DAY)
    _commit(r, "README.md", "readme\nline\n", T0 + 3 * DAY, agent=True)
    block = _ok(scan_agent_outcome(r, None, None))
    assert block["agent_commits"] == 1
    assert block["reverted_commits"] == 0
    assert block["revert_share"] == 0.0


def test_rework_inside_the_span_counts_and_after_it_does_not(tmp_path: Path) -> None:
    r = _init(tmp_path)
    _commit(r, "x.txt", "one\ntwo\nthree\n", T0 + DAY, agent=True)       # reworked at +5d
    _commit(r, "y.txt", "one\ntwo\nthree\n", T0 + DAY, agent=True)       # untouched
    _commit(r, "z.txt", "one\ntwo\nthree\n", T0 + DAY, agent=True)       # changed after the span
    _commit(r, "x.txt", "one\nTWO\nthree\n", T0 + 5 * DAY)
    _commit(r, "z.txt", "one\nTWO\nthree\n", T0 + (REWORK_DAYS + 10) * DAY)
    _commit(r, "late.txt", "late\n", T0 + (REWORK_DAYS + 20) * DAY, agent=True)  # too young
    block = _ok(scan_agent_outcome(r, None, None))
    assert block["agent_commits"] == 4
    assert block["rework_days"] == REWORK_DAYS
    assert block["rework_observed_commits"] == 3
    assert block["rework_sampled_commits"] == 3
    assert block["reworked_commits"] == 1
    assert block["rework_share"] == round(1 / 3, 4)
    assert block["rework_null_reason"] is None


def test_a_deleted_file_is_skipped_not_counted(tmp_path: Path) -> None:
    r = _init(tmp_path)
    _commit(r, "gone.txt", "one\n", T0 + DAY, agent=True)
    _git(r, "rm", "-q", "gone.txt", at=T0 + 2 * DAY)
    _git(r, "commit", "-q", "-m", "drop gone", at=T0 + 2 * DAY)
    _commit(r, "pad.txt", "pad\n", T0 + (REWORK_DAYS + 5) * DAY)
    block = _ok(scan_agent_outcome(r, None, None))
    assert block["rework_sampled_commits"] == 1
    assert block["reworked_commits"] == 0


def test_a_branch_merged_after_the_span_is_not_reworked(tmp_path: Path) -> None:
    """The blame point descends from the agent commit: mainline work on the file
    before a late merge never reads as rework."""
    r = _init(tmp_path)
    _commit(r, "s.txt", "".join(f"line {i}\n" for i in range(10)), T0 + DAY)
    _git(r, "checkout", "-q", "-b", "feat", at=T0 + DAY)
    lines = [f"line {i}\n" for i in range(10)]
    lines[8] = "agent line 8\n"
    _commit(r, "s.txt", "".join(lines), T0 + 3 * DAY, agent=True)
    _git(r, "checkout", "-q", "main", at=T0 + 3 * DAY)
    main_lines = [f"line {i}\n" for i in range(10)]
    main_lines[1] = "human line 1\n"
    _commit(r, "s.txt", "".join(main_lines), T0 + 10 * DAY)
    _git(r, "merge", "-q", "--no-ff", "-m", "merge feat", "feat", at=T0 + 20 * DAY)
    _commit(r, "pad.txt", "pad\n", T0 + (REWORK_DAYS + 10) * DAY)
    block = _ok(scan_agent_outcome(r, None, None))
    assert block["rework_sampled_commits"] == 1
    assert block["reworked_commits"] == 0


def test_a_rename_adds_no_line_and_is_not_rework(tmp_path: Path) -> None:
    r = _init(tmp_path)
    _commit(r, "f.txt", "".join(f"line {i}\n" for i in range(10)), T0 + DAY)
    _git(r, "mv", "f.txt", "g.txt", at=T0 + 2 * DAY)
    _git(r, "commit", "-q", "-m", f"rename\n\nCo-authored-by: {CLAUDE}", at=T0 + 2 * DAY)
    _commit(r, "g.txt", "".join(f"line {i}\n" for i in range(10)) + "appended\n", T0 + 3 * DAY)
    _commit(r, "pad.txt", "pad\n", T0 + (REWORK_DAYS + 10) * DAY)
    assert agent_outcome._added_lines(r, _git(r, "rev-parse", "HEAD~2").strip()) == {}
    block = _ok(scan_agent_outcome(r, None, None))
    assert block["rework_sampled_commits"] == 1
    assert block["reworked_commits"] == 0


def test_a_rename_with_an_edit_counts_only_the_edited_lines(tmp_path: Path) -> None:
    r = _init(tmp_path)
    _commit(r, "f.txt", "".join(f"line {i}\n" for i in range(10)), T0 + DAY)
    _git(r, "mv", "f.txt", "g.txt", at=T0 + 2 * DAY)
    (r / "g.txt").write_text("".join(f"line {i}\n" for i in range(10)) + "agent\n", encoding="utf-8")
    _git(r, "add", "g.txt", at=T0 + 2 * DAY)
    _git(r, "commit", "-q", "-m", "rename and add", at=T0 + 2 * DAY)
    assert agent_outcome._added_lines(r, _git(r, "rev-parse", "HEAD").strip()) == {"g.txt": 1}


def test_blame_ignores_a_configured_ignore_revs_file(tmp_path: Path) -> None:
    r = _init(tmp_path)
    _git(r, "config", "blame.ignoreRevsFile", "missing-ignore-revs")
    _commit(r, "x.txt", "one\ntwo\n", T0 + DAY, agent=True)
    _commit(r, "x.txt", "one\nTWO\n", T0 + 2 * DAY)
    _commit(r, "pad.txt", "pad\n", T0 + (REWORK_DAYS + 5) * DAY)
    block = _ok(scan_agent_outcome(r, None, None))
    assert block["reworked_commits"] == 1


def test_rework_reads_only_lines_inside_the_scope(tmp_path: Path) -> None:
    r = _init(tmp_path)
    (r / "out.txt").write_text("one\ntwo\n", encoding="utf-8")
    _git(r, "add", "out.txt", at=T0 + DAY)
    _commit(r, "pkg/in.txt", "in\n", T0 + DAY, agent=True)  # also commits out.txt
    _commit(r, "out.txt", "one\nTWO\n", T0 + 2 * DAY)       # rework outside the scope
    _commit(r, "pad.txt", "pad\n", T0 + (REWORK_DAYS + 5) * DAY)
    assert _ok(scan_agent_outcome(r, None, None))["reworked_commits"] == 1
    scoped = _ok(scan_agent_outcome(r, r / "pkg", None))
    assert scoped["agent_commits"] == 1 and scoped["reworked_commits"] == 0


def test_a_rename_into_the_scope_is_not_rework(tmp_path: Path) -> None:
    r = _init(tmp_path)
    body = "".join(f"line {i}\n" for i in range(20))
    _commit(r, "a/f.txt", body, T0 + DAY)
    (r / "b").mkdir()
    _git(r, "mv", "a/f.txt", "b/f.txt", at=T0 + 2 * DAY)
    (r / "b/f.txt").write_text(body + "agent\n", encoding="utf-8")
    _git(r, "add", "b/f.txt", at=T0 + 2 * DAY)
    _git(r, "commit", "-q", "-m", f"move\n\nCo-authored-by: {CLAUDE}", at=T0 + 2 * DAY)
    _commit(r, "b/f.txt", "touched\n" + body + "agent\n", T0 + 3 * DAY)
    _commit(r, "pad.txt", "pad\n", T0 + (REWORK_DAYS + 5) * DAY)
    for scope in (None, r / "b", Path("b")):
        block = _ok(scan_agent_outcome(r, scope, None))
        assert block["rework_sampled_commits"] == 1
        assert block["reworked_commits"] == 0, scope


def test_rework_sample_is_the_newest_observed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(agent_outcome, "REWORK_SAMPLE", 1)
    r = _init(tmp_path)
    _commit(r, "old.txt", "one\n", T0 + DAY, agent=True)
    _commit(r, "old.txt", "ONE\n", T0 + 2 * DAY)                 # reworks the old commit
    _commit(r, "new.txt", "one\n", T0 + 3 * DAY, agent=True)     # newest observed: kept
    _commit(r, "pad.txt", "pad\n", T0 + (REWORK_DAYS + 5) * DAY)
    block = _ok(scan_agent_outcome(r, None, None))
    assert block["rework_observed_commits"] == 2
    assert block["rework_sampled_commits"] == 1
    assert block["reworked_commits"] == 0


def test_no_agent_commits_reads_null_with_a_reason(tmp_path: Path) -> None:
    r = _init(tmp_path)
    block = _ok(scan_agent_outcome(r, None, None))
    assert block["agent_commits"] == 0
    assert block["revert_share"] is None and block["revert_null_reason"] == "no_agent_commits"
    assert block["rework_share"] is None and block["rework_null_reason"] == "no_agent_commits"


def test_young_agent_commits_read_rework_null(reverted_repo: tuple[Path, str]) -> None:
    repo, _ = reverted_repo
    block = _ok(scan_agent_outcome(repo, None, None))
    assert block["rework_observed_commits"] == 0
    assert block["rework_share"] is None and block["rework_null_reason"] == "none_old_enough"


def test_a_window_counts_back_from_head(reverted_repo: tuple[Path, str]) -> None:
    repo, _ = reverted_repo
    block = _ok(scan_agent_outcome(repo, None, "2d"))
    assert block["window"] == "2d" and block["since"] is not None
    assert block["agent_commits"] == 1  # only c.txt is inside two days of the revert


def test_a_shallow_clone_is_unavailable_with_a_code(reverted_repo: tuple[Path, str], tmp_path: Path) -> None:
    repo, _ = reverted_repo
    shallow = tmp_path / "shallow"
    subprocess.run(["git", "clone", "-q", "--depth", "2", f"file://{repo}", str(shallow)],
                   check=True, capture_output=True)
    block = scan_agent_outcome(shallow, None, None)
    assert block["available"] is False
    assert block["reason"].startswith("shallow_clone: ")
    reduced = counts_only_scans({"agent_outcome": block})["agent_outcome"]
    assert reduced == {"available": False, "reason": "shallow_clone"}


def test_not_a_repository_is_unavailable(tmp_path: Path) -> None:
    block = scan_agent_outcome(tmp_path, None, None)
    assert block["available"] is False and block["reason"].startswith("not_a_repo: ")


def test_counts_only_keeps_every_count_and_no_identity(reverted_repo: tuple[Path, str]) -> None:
    repo, sha = reverted_repo
    block = scan_agent_outcome(repo, None, None)
    reduced = counts_only_scans({"agent_outcome": block})["agent_outcome"]
    numeric = {k for k, v in block.items() if isinstance(v, (int, float)) or v is None}
    assert numeric <= set(reduced), "a count was dropped from counts-only output"
    for key in numeric:
        assert reduced[key] == block[key]  # type: ignore[literal-required]
    assert reduced["agent_rule"] == "trailer_or_author"
    assert reduced["limits"] == len(LIMITS)
    text = json.dumps(reduced)
    for leak in (sha, sha[:7], "a.txt", "b.txt", "Ada Person", "ada.person", "anthropic",
                 "Claude Helper", "This reverts"):
        assert leak not in text


def test_registered_after_ai_usage_with_its_reads() -> None:
    keys = [s.key for s in SCANS]
    assert keys.index("ai_usage") < keys.index("agent_outcome")
    spec = next(s for s in SCANS if s.key == "agent_outcome")
    assert spec.reads == ("repo_root", "scope", "history_window", "fleet_config")


def test_every_reason_code_survives_counts_only() -> None:
    from lib.counts_only.reason_codes import BLOCK_REASON_CODES, reason_token
    for sentence, code in agent_outcome.REASON_CODES.items():
        assert code in BLOCK_REASON_CODES
        assert reason_token(f"{code}: {sentence}") == code
