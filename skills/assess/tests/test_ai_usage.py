"""Tests for lib/ai_usage.py and the shared matcher in lib/agent_identity.py.

A real git fixture with pinned dates and known trailers: the counts are exact,
merges are excluded, the window filters by commit date, and no identity string
reaches the block.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from lib import agent_identity, change_coupling  # noqa: E402
from lib.ai_usage import LIMITS, resolve_window, scan_ai_usage  # noqa: E402
from lib.git_churn import CHURN_WINDOWS, window_from_churn_label  # noqa: E402
from lib.scan_registry import SCANS  # noqa: E402

HUMAN = "Ada Person <ada.person@example.org>"
CLAUDE = "Claude Helper <noreply@anthropic.com>"
COPILOT = "Copilot <copilot@users.example.net>"
BOT = "dependabot[bot] <49699333+dependabot[bot]@users.noreply.github.com>"
IDENTITY_STRINGS = (
    "Ada Person", "ada.person", "example.org", "noreply@anthropic.com",
    "Claude Helper", "copilot@users", "dependabot[bot]", "49699333",
)

RECENT = "2026-11-01T12:00:00+00:00"
OLD = "2019-01-01T12:00:00+00:00"


def _git(repo: Path, *args: str, date: str = RECENT) -> None:
    env = {**os.environ, "GIT_AUTHOR_DATE": date, "GIT_COMMITTER_DATE": date}
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, env=env)


def _commit(repo: Path, name: str, trailers: tuple[str, ...] = (), date: str = RECENT) -> None:
    (repo / name).write_text(name, encoding="utf-8")
    _git(repo, "add", name, date=date)
    message = f"add {name}"
    if trailers:
        message += "\n\n" + "\n".join(f"Co-authored-by: {t}" for t in trailers)
    _git(repo, "commit", "-q", "-m", message, date=date)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    r.mkdir()
    _git(r, "init", "-q", "-b", "main")
    _git(r, "config", "user.name", "Ada Person")
    _git(r, "config", "user.email", "ada.person@example.org")
    _commit(r, "old.txt", (CLAUDE,), date=OLD)       # outside a 12mo window
    _commit(r, "plain.txt")                         # no trailer
    _commit(r, "human.txt", (HUMAN,))               # human-only co-author
    _commit(r, "both.txt", (HUMAN, CLAUDE))         # human and agent: counted once
    _commit(r, "bot.txt", (BOT,))                   # [bot] trailer
    _commit(r, "copilot.txt", (COPILOT,))           # copilot@ trailer
    _git(r, "checkout", "-q", "-b", "side")
    _commit(r, "side.txt", (CLAUDE,))               # agent commit on a branch
    _git(r, "checkout", "-q", "main")
    _git(r, "merge", "-q", "--no-ff", "-m", f"merge side\n\nCo-authored-by: {CLAUDE}", "side")
    return r


def test_all_time_counts_are_exact_and_merges_are_excluded(repo: Path) -> None:
    block = scan_ai_usage(repo, None)
    assert block["available"] is True
    assert block["window"] == "all-time" and block["since"] is None
    assert block["merge_rule"] == "excluded"
    # 7 non-merge commits; agent trailers on old, both, bot, copilot, side.
    assert block["total_commits"] == 7
    assert block["agent_trailer_commits"] == 5
    assert block["agent_trailer_share"] == round(5 / 7, 4)
    assert block["lower_bound"] is True
    assert block["limits"] == list(LIMITS)
    assert block["covered_agents"] == list(agent_identity.COVERED_AGENTS)


def test_window_filters_by_commit_date(repo: Path) -> None:
    block = scan_ai_usage(repo, "5y")
    assert block["available"] is True
    assert block["window"] == "5y" and block["since"] == "5 years ago"
    assert block["total_commits"] == 6
    assert block["agent_trailer_commits"] == 4


@pytest.mark.parametrize(("label", "since"), CHURN_WINDOWS)
def test_every_churn_window_resolves_to_its_since(label: str, since: str | None) -> None:
    """assess_core's normalised churn window lands on the churn axis's --since."""
    resolved = resolve_window(window_from_churn_label(f"commits ({label})"))
    assert resolved[1] == since


def test_no_identity_string_reaches_the_block(repo: Path) -> None:
    text = json.dumps(scan_ai_usage(repo, None))
    for needle in IDENTITY_STRINGS:
        assert needle not in text


def test_empty_window_has_null_share(repo: Path) -> None:
    sub = repo / "empty"
    sub.mkdir()
    block = scan_ai_usage(sub, None)
    assert block["available"] is True
    assert block["total_commits"] == 0 and block["agent_trailer_share"] is None


def test_no_git_history_degrades(tmp_path: Path) -> None:
    block = scan_ai_usage(tmp_path, None)
    assert block == {"available": False, "reason": "no readable git history at the run root"}


@pytest.mark.parametrize(("window", "expected"), [
    (None, ("all-time", None)),
    ("6mo", ("6mo", "6 months ago")),
    ("90d", ("90d", "90 days ago")),
    ("12w", ("12w", "12 weeks ago")),
    ("1y", ("1y", "1 years ago")),
])
def test_resolve_window(window: str | None, expected: tuple[str, str | None]) -> None:
    assert resolve_window(window) == expected


@pytest.mark.parametrize("window", ["six months", "0mo", "commits (last 12mo)", "last 12mo", ""])
def test_unrecognised_window_raises(window: str) -> None:
    with pytest.raises(ValueError, match="unrecognised history window"):
        resolve_window(window)


def test_matcher_has_one_definition() -> None:
    """change_coupling re-exports the shared hints rather than defining its own."""
    assert change_coupling.AI_EMAIL_HINTS is agent_identity.AI_EMAIL_HINTS
    assert agent_identity.coauthors_have_agent(f"{HUMAN}\x1d{CLAUDE}")
    assert not agent_identity.coauthors_have_agent(HUMAN)
    assert not agent_identity.coauthors_have_agent("Claude <claude@example.org>")


def test_registered_after_agent_environment() -> None:
    keys = [spec.key for spec in SCANS]
    assert keys[-1] == "ai_usage"
    spec = SCANS[-1]
    assert spec.reads == ("repo_root", "history_window")
