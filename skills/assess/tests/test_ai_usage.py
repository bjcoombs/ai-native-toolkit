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

from lib import agent_identity, ai_usage, change_coupling  # noqa: E402
from lib.ai_usage import LIMITS, resolve_window, scan_ai_usage  # noqa: E402
from lib.git_churn import CHURN_WINDOWS, window_from_churn_label  # noqa: E402
from lib.scan_registry import SCANS  # noqa: E402

HUMAN = "Ada Person <ada.person@example.org>"
CLAUDE = "Claude Helper <noreply@anthropic.com>"
COPILOT = "Copilot <copilot@users.example.net>"
BOT = "dependabot[bot] <49699333+dependabot[bot]@users.noreply.github.com>"
COPILOT_BOT = "Copilot <198982749+Copilot@users.noreply.github.com>"
COPILOT_AGENT = "copilot-swe-agent[bot] <198982749+copilot-swe-agent[bot]@users.noreply.github.com>"
IDENTITY_STRINGS = (
    "Ada Person", "ada.person", "example.org", "noreply@anthropic.com",
    "Claude Helper", "copilot@users", "dependabot[bot]", "49699333", "198982749",
    "copilot-swe-agent",
)

RECENT = "2026-11-01T12:00:00+00:00"
OLD = "2019-01-01T12:00:00+00:00"


def _git(repo: Path, *args: str, date: str = RECENT) -> None:
    env = {**os.environ, "GIT_AUTHOR_DATE": date, "GIT_COMMITTER_DATE": date}
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, env=env)


def _commit(repo: Path, name: str, trailers: tuple[str, ...] = (), date: str = RECENT) -> None:
    (repo / name).parent.mkdir(parents=True, exist_ok=True)
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
    _commit(r, "bot.txt", (BOT,))                   # automation [bot] trailer: not counted
    _commit(r, "pkg/agent.txt", (COPILOT_AGENT,))   # Copilot coding agent [bot], in pkg/
    _commit(r, "copilot.txt", (COPILOT,))           # copilot@ trailer
    _git(r, "checkout", "-q", "-b", "side")
    _commit(r, "side.txt", (CLAUDE,))               # agent commit on a branch
    _git(r, "checkout", "-q", "main")
    _git(r, "merge", "-q", "--no-ff", "-m", f"merge side\n\nCo-authored-by: {CLAUDE}", "side")
    return r


def test_all_time_counts_are_exact_and_merges_are_excluded(repo: Path) -> None:
    block = scan_ai_usage(repo, None, None)
    assert block["available"] is True
    assert block["window"] == "all-time" and block["since"] is None
    assert block["merge_rule"] == "excluded"
    # 8 non-merge commits; AI trailers on old, both, pkg/agent, copilot, side.
    assert block["total_commits"] == 8
    assert block["agent_trailer_commits"] == 5
    assert block["agent_trailer_share"] == round(5 / 8, 4)
    assert block["lower_bound"] is True
    assert block["limits"] == list(LIMITS)
    assert block["covered_agents"] == list(agent_identity.COVERED_AGENTS)


def test_window_filters_by_commit_date(repo: Path) -> None:
    block = scan_ai_usage(repo, None, "5y")
    assert block["available"] is True
    assert block["window"] == "5y" and block["since"] == "5 years ago"
    assert block["total_commits"] == 7
    assert block["agent_trailer_commits"] == 4


def test_scope_counts_only_the_subtree(repo: Path) -> None:
    block = scan_ai_usage(repo, repo / "pkg", None)
    assert block["available"] is True
    assert block["total_commits"] == 1 and block["agent_trailer_commits"] == 1


@pytest.mark.parametrize(("label", "since"), CHURN_WINDOWS)
def test_every_churn_window_resolves_to_its_since(label: str, since: str | None) -> None:
    """assess_core's normalised churn window lands on the churn axis's --since."""
    resolved = resolve_window(window_from_churn_label(f"commits ({label})"))
    assert resolved[1] == since


def test_no_identity_string_reaches_the_block(repo: Path) -> None:
    text = json.dumps(scan_ai_usage(repo, None, None))
    for needle in IDENTITY_STRINGS:
        assert needle not in text


def test_empty_window_has_null_share(repo: Path) -> None:
    sub = repo / "empty"
    sub.mkdir()
    block = scan_ai_usage(repo, sub, None)
    assert block["available"] is True
    assert block["total_commits"] == 0 and block["agent_trailer_share"] is None


def test_no_git_history_degrades(tmp_path: Path) -> None:
    block = scan_ai_usage(tmp_path, None, None)
    assert block == {"available": False, "reason": ai_usage.REASON_NOT_A_REPO}


def test_shallow_clone_degrades(repo: Path, tmp_path: Path) -> None:
    clone = tmp_path / "shallow"
    subprocess.run(
        ["git", "clone", "-q", "--depth", "1", f"file://{repo}", str(clone)],
        check=True, capture_output=True,
    )
    block = scan_ai_usage(clone, None, None)
    assert block == {"available": False, "reason": ai_usage.REASON_SHALLOW}


def test_missing_git_and_timeout_have_their_own_reasons(
    repo: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    def no_git(*_a: object, **_k: object) -> None:
        raise FileNotFoundError("git")

    def slow(*_a: object, **_k: object) -> None:
        raise subprocess.TimeoutExpired("git", ai_usage.GIT_TIMEOUT_SECONDS)

    monkeypatch.setattr(ai_usage.subprocess, "run", no_git)
    assert scan_ai_usage(repo, None, None)["reason"] == ai_usage.REASON_NO_GIT  # type: ignore[typeddict-item]
    monkeypatch.setattr(ai_usage.subprocess, "run", slow)
    assert scan_ai_usage(repo, None, None)["reason"] == ai_usage.REASON_TIMEOUT  # type: ignore[typeddict-item]


def test_unconfirmed_shallow_probe_degrades(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    real = subprocess.run

    def probe_fails(cmd: list[str], **kw: object) -> object:
        if "--is-shallow-repository" in cmd:
            raise subprocess.CalledProcessError(128, cmd)
        return real(cmd, **kw)  # type: ignore[call-overload]

    monkeypatch.setattr(ai_usage.subprocess, "run", probe_fails)
    assert scan_ai_usage(repo, None, None) == {
        "available": False, "reason": ai_usage.REASON_SHALLOW_UNKNOWN,
    }


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


@pytest.mark.parametrize(("trailer", "assistant"), [
    (CLAUDE, True), (COPILOT, True), (COPILOT_BOT, True), (COPILOT_AGENT, True),
    (BOT, False), ("pre-commit-ci[bot] <66853113+pre-commit-ci[bot]@users.noreply.github.com>", False),
    ("github-actions[bot] <41898282+github-actions[bot]@users.noreply.github.com>", False),
    (HUMAN, False),
])
def test_ai_assistant_excludes_automation_bots(trailer: str, assistant: bool) -> None:
    assert agent_identity.coauthors_have_ai_assistant(trailer) is assistant


def test_registered_after_the_setup_values_it_reads() -> None:
    keys = [spec.key for spec in SCANS]
    spec = SCANS[keys.index("ai_usage")]
    assert spec.reads == ("repo_root", "scope", "history_window")
    assert keys.index("scope") < keys.index("ai_usage")
    assert keys.index("history_window") < keys.index("ai_usage")
