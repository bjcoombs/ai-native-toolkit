"""Tests for bot and agent account discovery and the [identities] config (issue #765).

The fixture is the issue's: a coverage bot, Renovate, ``claude[bot]``, a person
named ``cursor`` and a person. Before confirmation only ``claude[bot]`` counts and
the three automated-looking accounts await confirmation; confirming them in
``[identities]`` moves ``cursor`` into the agent count, the rerun lists nothing,
and no account name reaches the block or its counts-only form.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import identity_candidates as cli  # noqa: E402
from lib.agent_identity import AGENT, DEPENDENCY_BOT, OTHER, AuthorMatcher, ConfirmedIdentities  # noqa: E402
from lib.counts_only import counts_only_scans  # noqa: E402
from lib.fleet_config import (  # noqa: E402
    FleetConfigError,
    config_sha256,
    empty_config,
    merge_repo_sections,
    parse_fleet_config,
    section_entries,
)
from lib.account_discovery import EVIDENCE, HistoryCommit, discover, evidence_for  # noqa: E402
from lib.signals import collect  # noqa: E402

CLAUDE = ("claude[bot]", "209825114+claude[bot]@users.noreply.github.com")
CODECOV = ("codecov[bot]", "22429695+codecov[bot]@users.noreply.github.com")
RENOVATE = ("renovate[bot]", "29139614+renovate[bot]@users.noreply.github.com")
CURSOR = ("cursor", "cursor@example.com")
ANN = ("Ann", "ann@example.com")
T = ("t", "t@example.com")
SECRETS = ("codecov", "renovate", "cursor", "ann@example.com", "claude[bot]")

CONFIRMED = ('[identities]\nagents = ["cursor@example.com"]\n'
             'bots = ["codecov[bot]", "renovate[bot]"]\n')


def _commit(repo: Path, day: int, author: tuple[str, str], subject: str, trailer: str = "") -> None:
    date = f"2026-10-{day:02d}T10:00:00Z"
    env = {**os.environ, "GIT_AUTHOR_DATE": date, "GIT_COMMITTER_DATE": date}
    with (repo / "a.py").open("a", encoding="utf-8") as fh:
        fh.write(f"# {day}\n")
    message = subject + (f"\n\nCo-authored-by: {trailer}" if trailer else "")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "-c", f"user.name={author[0]}", "-c", f"user.email={author[1]}",
                    "commit", "-q", "-m", message], check=True, capture_output=True, env=env)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    r.mkdir()
    subprocess.run(["git", "-C", str(r), "init", "-q", "-b", "main"], check=True, capture_output=True)
    _commit(r, 19, T, "init")
    day = 20
    for author, n in ((CLAUDE, 2), (CODECOV, 2), (RENOVATE, 2), (CURSOR, 3), (ANN, 3)):
        for i in range(n):
            _commit(r, day, author, f"{author[0]} change {i} of {day}")
            day += 1
    return r


def _ai_usage(repo: Path) -> dict[str, object]:
    block = collect(repo, ["ai_usage"], window="6mo")["ai_usage"]
    assert block["available"] is True
    return block  # type: ignore[no-any-return]


def _confirm(repo: Path) -> None:
    (repo / ".assess").mkdir(exist_ok=True)
    (repo / ".assess" / "config.toml").write_text(CONFIRMED, encoding="utf-8")


def test_before_confirmation_only_the_agent_app_counts(repo: Path) -> None:
    block = _ai_usage(repo)
    assert block["agent_authored_commits"] == 2  # claude[bot]; cursor counts 0
    assert block["dependency_bot_commits"] == 2
    assert block["identities_awaiting_confirmation"] == 3  # codecov, renovate, cursor


def test_confirmed_identities_move_the_counts_and_leave_none_awaiting(repo: Path) -> None:
    _confirm(repo)
    block = _ai_usage(repo)
    assert block["agent_authored_commits"] == 5  # claude[bot] and cursor
    assert block["identities_awaiting_confirmation"] == 0


def test_no_identity_reaches_the_block_or_counts_only(repo: Path) -> None:
    for confirmed in (False, True):
        if confirmed:
            _confirm(repo)
        scans = collect(repo, ["ai_usage"], window="6mo")
        reduced = counts_only_scans(scans)
        assert reduced["ai_usage"]["identities_awaiting_confirmation"] == (0 if confirmed else 3)
        block = {k: v for k, v in scans["ai_usage"].items() if k not in ("limits", "covered_agents", "covered_author_agents")}
        for text in (json.dumps(block), json.dumps(reduced)):
            for needle in SECRETS:
                assert needle not in text.lower()


def test_discovery_lists_each_candidate_with_its_evidence(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main([str(repo), "--json"]) == 0
    rows = {r["account"]: r for r in json.loads(capsys.readouterr().out)}
    assert set(rows) == {"codecov[bot]", "renovate[bot]", "cursor@example.com"}
    assert rows["codecov[bot]"]["evidence"] == ["bot_suffix", "noreply_address"]
    assert rows["renovate[bot]"]["evidence"] == ["bot_suffix", "dependency_bot_hint", "noreply_address"]
    assert "agent_name_hint" in rows["cursor@example.com"]["evidence"]
    assert rows["cursor@example.com"]["commits"] == 3
    assert cli.main([str(repo)]) == 0
    text = capsys.readouterr().out
    assert text.startswith("3 accounts awaiting confirmation") and "bot_suffix" in text


def test_confirm_writes_identities_and_a_rerun_lists_nothing(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    config = repo / ".assess" / "config.toml"
    config.parent.mkdir()
    config.write_text('exclude_dirs = ["vendor"]\n\n[identities]\npeople = ["cursor@example.com"]\n\n'
                      '[review]\nai_review_markers = ["x"]\n', encoding="utf-8")
    assert cli.main([str(repo), "--confirm", "agent=cursor@example.com", "--confirm", "bot=codecov[bot]",
                     "--confirm", "bot=renovate[bot]"]) == 0
    data = tomllib.loads(config.read_text(encoding="utf-8"))
    assert data["identities"] == {"agents": ["cursor@example.com"],
                                  "bots": ["codecov[bot]", "renovate[bot]"]}  # moved out of people
    assert data["exclude_dirs"] == ["vendor"] and data["review"] == {"ai_review_markers": ["x"]}
    capsys.readouterr()
    assert cli.main([str(repo)]) == 0
    assert capsys.readouterr().out.strip() == "No accounts awaiting confirmation."
    assert _ai_usage(repo)["agent_authored_commits"] == 5


def test_fleet_listing_aggregates_and_the_fleet_config_extends_each_repo(
        repo: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    other = tmp_path / "other"
    subprocess.run(["git", "clone", "-q", str(repo), str(other)], check=True, capture_output=True)
    listing = tmp_path / "repos.txt"
    listing.write_text(f"# fleet\n{repo}\n{other}\n", encoding="utf-8")
    assert cli.main(["--from-list", str(listing), "--json"]) == 0
    rows = {r["account"]: r for r in json.loads(capsys.readouterr().out)}
    assert rows["codecov[bot]"]["repositories"] == 2 and rows["codecov[bot]"]["commits"] == 4
    fleet = tmp_path / "fleet.toml"
    assert cli.main(["--from-list", str(listing), "--confirm", "bot=codecov[bot]"]) == 2  # needs --config
    assert cli.main(["--from-list", str(listing), "--confirm", "bot=codecov[bot]", "--config", str(fleet)]) == 0
    capsys.readouterr()
    assert cli.main(["--from-list", str(listing), "--fleet-config", str(fleet), "--json"]) == 0
    assert "codecov[bot]" not in {r["account"] for r in json.loads(capsys.readouterr().out)}


@pytest.mark.parametrize("argv", [["--confirm", "robot=x"], ["--confirm", "agent="], [],
                                  ["--window", ""], ["--window", "null"]])
def test_bad_arguments_exit_2(repo: Path, argv: list[str]) -> None:
    assert cli.main(([str(repo)] if argv else []) + argv) == 2


def test_full_history_window_and_a_bad_config_file(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main([str(repo), "--window", "all-time", "--json"]) == 0
    assert len(json.loads(capsys.readouterr().out)) == 3
    (repo / ".assess").mkdir()
    (repo / ".assess" / "config.toml").write_text("identities = 3\n", encoding="utf-8")
    assert cli.main([str(repo), "--confirm", "bot=codecov[bot]"]) == 2
    (repo / ".assess" / "config.toml").write_text("[identities\n", encoding="utf-8")
    assert cli.main([str(repo), "--confirm", "bot=codecov[bot]"]) == 2


def test_a_missing_directory_or_bad_fleet_config_exits_2(repo: Path, tmp_path: Path) -> None:
    assert cli.main([str(tmp_path / "nope")]) == 2
    bad = tmp_path / "bad.toml"
    bad.write_text("[identities]\nagents = 3\n", encoding="utf-8")
    assert cli.main([str(repo), "--fleet-config", str(bad)]) == 2
    assert cli.main(["--from-list", str(tmp_path / "missing.txt")]) == 2
    assert cli.main([str(tmp_path)]) == 2  # not a git repository


def test_a_confirmed_class_beats_every_pinned_rule() -> None:
    confirmed = ConfirmedIdentities.from_section({"people": ["Claude[bot]"], "agents": ["helper@corp.example"],
                                                  "bots": ["renovate[bot]"]})
    m = AuthorMatcher(confirmed=confirmed)
    assert m.classify(CLAUDE[1], CLAUDE[0]).bucket == OTHER  # a confirmed person never counts
    assert m.classify("helper@corp.example", "Helper").bucket == AGENT
    assert m.classify(RENOVATE[1], RENOVATE[0]).bucket == DEPENDENCY_BOT  # never an agent
    assert m.classify_username("helper@corp.example").bucket == AGENT
    assert AuthorMatcher().classify(CLAUDE[1], CLAUDE[0]).bucket == AGENT
    both = ConfirmedIdentities.from_section({"agents": ["x@y"], "bots": ["x@y"]})
    assert both.class_of(frozenset({"x@y"})) == "bot"


def test_entries_are_literal_never_patterns() -> None:
    confirmed = ConfirmedIdentities.from_section({"agents": ["codecov[bot]"]})
    m = AuthorMatcher(confirmed=confirmed)
    assert m.classify("c@example.com", "c").bucket == OTHER  # not a character class
    assert m.classify(CODECOV[1], CODECOV[0]).bucket == AGENT


def test_weak_evidence_alone_makes_no_candidate() -> None:
    commits = [HistoryCommit("12+ann@users.noreply.github.com", "Ann", "Bump 1", "")] * 4
    assert evidence_for("12+ann@users.noreply.github.com", "Ann", ["Bump 1", "Bump 2", "Bump 3"]) == [
        "noreply_address", "uniform_subjects"]
    assert discover(commits, AuthorMatcher()) == []


def test_co_authors_gitlab_tokens_and_automation_names_are_candidates() -> None:
    commits = [
        HistoryCommit("ann@example.com", "Ann", "x", "Release Bot <release@corp.example>\x1d"
                      "Claude <noreply@anthropic.com>"),
        HistoryCommit("project_7_bot_ab12@noreply.gitlab.example", "token", "y", ""),
        HistoryCommit("release@corp.example", "Release Bot", "z", ""),
    ]
    rows = {r["account"]: r for r in discover(commits, AuthorMatcher())}
    assert set(rows) == {"release@corp.example", "project_7_bot_ab12"}
    assert rows["release@corp.example"]["evidence"] == ["automation_name_hint", "co_author_trailer"]
    assert rows["release@corp.example"]["commits"] == 1
    trailer_only = [HistoryCommit("ann@example.com", "Ann", "x", "coderabbitai[bot] <c@example.com>")]
    assert discover(trailer_only, AuthorMatcher()) == []  # authored nothing: no count would move
    assert rows["project_7_bot_ab12"]["evidence"] == ["gitlab_token_user", "automation_name_hint",
                                                         "noreply_address"]
    assert set(EVIDENCE) >= {e for r in rows.values() for e in r["evidence"]}


def test_identities_section_validates_merges_and_hashes() -> None:
    fleet = parse_fleet_config('[identities]\nbots = ["codecov[bot]"]\n')
    assert section_entries(fleet)["identities"] == 1
    merged = merge_repo_sections(fleet, {"identities": {"bots": ["ci[bot]", "codecov[bot]"], "agents": ["a@b"]}})
    assert merged.config["identities"] == {"bots": ["codecov[bot]", "ci[bot]"], "agents": ["a@b"]}
    assert config_sha256(empty_config()) == config_sha256(parse_fleet_config("[identities]\n"))
    with pytest.raises(FleetConfigError, match="identities.robots"):
        parse_fleet_config('[identities]\nrobots = ["x"]\n')
