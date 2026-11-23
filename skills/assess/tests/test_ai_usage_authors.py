"""Tests for the ai_usage author counts (issue #695) and the author matcher in
lib/agent_identity.py.

A real git fixture whose commits are authored by agent apps, an in-house agent,
dependency bots, a GitLab access-token user and a person: each count is exact,
a commit with both an agent author and an agent trailer counts once in the
either share, config rules are credited by position, and no pattern or matched
identity reaches the block or its counts-only form.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from lib import agent_identity  # noqa: E402
from lib.agent_identity import (  # noqa: E402
    AGENT,
    DEPENDENCY_BOT,
    OTHER,
    TOKEN_USER,
    AuthorMatcher,
    is_gitlab_token_user,
    username_from_email,
)
from lib.ai_usage import AiUsageBlock, scan_ai_usage  # noqa: E402
from lib.counts_only.content_free import counts_only_scans  # noqa: E402
from lib.fleet_config import (  # noqa: E402
    FleetConfig,
    FleetConfigError,
    empty_config,
    merge_repo_sections,
    parse_fleet_config,
)
from lib.level_signals import signal_path_validator  # noqa: E402
from lib.signals import collect  # noqa: E402
from lib.levels import load_levels  # noqa: E402

RECENT = (datetime.now(timezone.utc) - timedelta(days=30)).replace(microsecond=0).isoformat()

HUMAN = ("Ada Person", "ada.person@example.org")
CLAUDE_APP = ("claude[bot]", "209825114+claude[bot]@users.noreply.github.com")
DEVIN = ("devin-ai-integration[bot]", "158243242+devin-ai-integration[bot]@users.noreply.github.com")
INHOUSE = ("Build Helper", "inhouse-agent@corp.example")
RENOVATE = ("renovate[bot]", "29139614+renovate[bot]@users.noreply.github.com")
RENOVATE_PLAIN = ("Renovate Bot", "renovate-bot@corp.example")
TOKEN = ("review token", "project_42_bot_4ffca233d8298ea1@noreply.gitlab.corp.example")
CLAUDE_TRAILER = "Claude <noreply@anthropic.com>"

PATTERN = "^inhouse-agent"
IDENTITY_STRINGS = (
    "Ada Person", "ada.person", "claude[bot]", "209825114", "devin-ai-integration",
    "inhouse-agent", "corp.example", "Build Helper", "renovate", "project_42_bot",
    "review token", "noreply@anthropic.com", PATTERN,
)


def _commit(repo: Path, name: str, author: tuple[str, str], trailer: str | None = None) -> None:
    env = {**os.environ, "GIT_AUTHOR_DATE": RECENT, "GIT_COMMITTER_DATE": RECENT,
           "GIT_AUTHOR_NAME": author[0], "GIT_AUTHOR_EMAIL": author[1]}
    (repo / name).write_text(name, encoding="utf-8")
    message = f"add {name}" + (f"\n\nCo-authored-by: {trailer}" if trailer else "")
    for args in (["add", name], ["commit", "-q", "-m", message]):
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, env=env)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    r.mkdir()
    for args in (["init", "-q", "-b", "main"], ["config", "user.name", HUMAN[0]],
                 ["config", "user.email", HUMAN[1]]):
        subprocess.run(["git", "-C", str(r), *args], check=True, capture_output=True)
    _commit(r, "human.txt", HUMAN)
    _commit(r, "assisted.txt", HUMAN, CLAUDE_TRAILER)   # trailer only
    _commit(r, "app.txt", CLAUDE_APP)                   # pinned agent author
    _commit(r, "both.txt", CLAUDE_APP, CLAUDE_TRAILER)  # agent author and trailer: once
    _commit(r, "devin.txt", DEVIN)                      # pinned agent author
    _commit(r, "inhouse1.txt", INHOUSE)                 # config rule only
    _commit(r, "inhouse2.txt", INHOUSE)
    _commit(r, "renovate.txt", RENOVATE)                # dependency bot
    _commit(r, "renovate2.txt", RENOVATE_PLAIN)         # plain renovate-bot user
    _commit(r, "token.txt", TOKEN)                      # GitLab access-token user
    return r


def _config(text: str) -> FleetConfig:
    return parse_fleet_config(text)


def _ok(block: object) -> AiUsageBlock:
    assert isinstance(block, dict) and block["available"] is True
    return block  # type: ignore[return-value]


def test_defaults_alone_count_each_bucket_exactly(repo: Path) -> None:
    block = _ok(scan_ai_usage(repo, None, None))
    assert block["total_commits"] == 10
    assert block["agent_trailer_commits"] == 2
    assert block["agent_authored_commits"] == 3          # app, both, devin
    assert block["agent_either_commits"] == 4            # assisted, app, both, devin
    assert block["agent_either_share"] == round(4 / 10, 4)
    assert block["agent_authored_share"] == round(3 / 10, 4)
    assert block["dependency_bot_commits"] == 2
    assert block["token_user_commits"] == 1
    assert block["agent_identity_rule_commits"] == []
    assert block["covered_author_agents"] == list(agent_identity.COVERED_AUTHOR_AGENTS)


def test_config_rule_is_matched_and_credited_by_position(repo: Path) -> None:
    config = _config(f'[[ai_usage.agent_identities]]\npattern = "^nobody"\n'
                     f'[[ai_usage.agent_identities]]\npattern = "{PATTERN}"\n')
    block = _ok(scan_ai_usage(repo, None, None, config))
    assert block["agent_authored_commits"] == 5
    assert block["agent_either_commits"] == 6
    assert block["agent_identity_rule_commits"] == [0, 2]
    assert block["token_user_commits"] == 1


def test_token_users_are_agents_only_when_the_config_says_so(repo: Path) -> None:
    promoted = _ok(scan_ai_usage(repo, None, None,
                                 _config("[ai_usage]\ntoken_users_as_agents = true\n")))
    assert promoted["token_user_commits"] == 0
    assert promoted["agent_authored_commits"] == 4
    by_rule = _ok(scan_ai_usage(repo, None, None, _config(
        '[[ai_usage.agent_identities]]\npattern = "^project_42_bot_"\nfield = "username"\n')))
    assert by_rule["token_user_commits"] == 0
    assert by_rule["agent_identity_rule_commits"] == [1]


def test_a_dependency_bot_is_never_an_agent(repo: Path) -> None:
    config = _config('[[ai_usage.agent_identities]]\npattern = "renovate"\n')
    block = _ok(scan_ai_usage(repo, None, None, config))
    assert block["dependency_bot_commits"] == 2
    assert block["agent_identity_rule_commits"] == [0]


def test_display_name_is_read_only_by_an_explicit_name_rule(repo: Path) -> None:
    by_default = _ok(scan_ai_usage(repo, None, None, _config(
        '[[ai_usage.agent_identities]]\npattern = "^Build Helper$"\n')))
    assert by_default["agent_identity_rule_commits"] == [0]
    by_name = _ok(scan_ai_usage(repo, None, None, _config(
        '[[ai_usage.agent_identities]]\npattern = "^Build Helper$"\nfield = "name"\n')))
    assert by_name["agent_identity_rule_commits"] == [2]


def test_no_pattern_or_identity_reaches_the_block_or_counts_only(repo: Path) -> None:
    config = _config(f'[[ai_usage.agent_identities]]\npattern = "{PATTERN}"\n')
    block = scan_ai_usage(repo, None, None, config)
    reduced = counts_only_scans({"ai_usage": block})
    for text in (json.dumps(block), json.dumps(reduced)):
        for needle in IDENTITY_STRINGS:
            assert needle not in text
    kept = reduced["ai_usage"]
    assert kept["agent_identity_rule_commits"] == [2]
    for key in ("agent_authored_commits", "agent_either_share", "dependency_bot_commits",
                "token_user_commits"):
        assert kept[key] == block[key]  # type: ignore[literal-required]


def test_a_maturity_model_can_read_the_either_share(tmp_path: Path) -> None:
    levels = tmp_path / "levels.toml"
    levels.write_text(
        'version = "t-1"\n[[rows]]\nid = "agent-use"\ntitle = "Agent use"\nscope = "team"\n'
        'aggregation = "min"\n  [[rows.levels]]\n  level = 1\n'
        '  criteria = [{ signal = "ai_usage.agent_either_share", op = "gt", value = 0.1 }]\n',
        encoding="utf-8")
    model = load_levels(levels, signal_path_validator())
    assert model["rows"][0]["levels"][0]["criteria"][0]["signal"] == "ai_usage.agent_either_share"


# --- matcher -----------------------------------------------------------------

@pytest.mark.parametrize(("username", "bucket"), [
    ("claude[bot]", AGENT), ("devin-ai-integration[bot]", AGENT),
    ("Copilot", OTHER),  # a name-only login: a hint until confirmed (#883)
    ("copilot-swe-agent[bot]", AGENT), ("renovate-bot", DEPENDENCY_BOT),
    ("renovate[bot]", DEPENDENCY_BOT), ("dependabot[bot]", DEPENDENCY_BOT),
    ("project_7_bot_0a1b2c", TOKEN_USER), ("group_9_bot_ffee", TOKEN_USER),
    ("Claude", OTHER), ("cursor", OTHER), ("github-actions[bot]", OTHER),
])
def test_username_form_for_platform_users(username: str, bucket: str) -> None:
    assert AuthorMatcher().classify_username(username).bucket == bucket


def test_username_form_honours_config_rules_and_promotion() -> None:
    rules = agent_identity.compile_config_rules([{"pattern": "^inhouse-"},
                                                 {"pattern": "corp", "field": "email"}])
    matcher = AuthorMatcher(rules, token_users_as_agents=True)
    assert matcher.classify_username("inhouse-agent") == (AGENT, 0)
    assert matcher.classify_username("corp-bot").bucket == OTHER  # an e-mail rule
    assert matcher.classify_username("project_1_bot_ab") == (AGENT, None)


@pytest.mark.parametrize(("value", "token"), [
    ("project_123_bot_4ffca233d8298ea1", True),
    ("group_123_bot_4ffca233d8298ea1", True),
    ("project_5_bot", False),
    ("project_123_bot_4ffca233d8298ea1@noreply.gitlab.example.com", True),
    ("project_123_bot_4ffca233d8298ea1@example.com", False),
    ("project_bot", False), ("user_1_bot_ab", False), ("my_project_1_bot_ab", False),
])
def test_gitlab_token_user_recognition(value: str, token: bool) -> None:
    assert is_gitlab_token_user(value) is token


@pytest.mark.parametrize(("email", "username"), [
    ("49699333+dependabot[bot]@users.noreply.github.com", "dependabot[bot]"),
    ("octo@users.noreply.github.com", "octo"),
    ("1234-ada@users.noreply.gitlab.example.com", "ada"),
    ("project_1_bot_ab@noreply.gitlab.example.com", "project_1_bot_ab"),
    ("ada@example.org", None),
])
def test_username_from_email(email: str, username: str | None) -> None:
    assert username_from_email(email) == username


def test_the_existing_matchers_are_unchanged() -> None:
    """#695 widens ai_usage's author matcher only; the shared matcher stays put."""
    assert agent_identity.AI_EMAIL_HINTS == ("[bot]", "noreply@anthropic.com", "copilot@")
    assert not agent_identity.identity_is_agent("cursoragent@cursor.com")
    assert not agent_identity.identity_is_agent("project_1_bot_ab@noreply.gitlab.example.com")
    assert agent_identity.identity_is_agent("x@example.org", "renovate[bot]")
    assert not agent_identity.is_ai_assistant("1+devin-ai-integration[bot]@users.noreply.github.com")
    assert not agent_identity.is_ai_assistant("1+claude[bot]@users.noreply.github.com")


# --- config ------------------------------------------------------------------

@pytest.mark.parametrize(("text", "key"), [
    ('[[ai_usage.agent_identities]]\npattern = "(unclosed"\n', "ai_usage.agent_identities[0].pattern"),
    ('[[ai_usage.agent_identities]]\npattern = "a"\nfield = "login"\n',
     "ai_usage.agent_identities[0].field"),
    ('[[ai_usage.agent_identities]]\nfield = "name"\n', "ai_usage.agent_identities[0].pattern"),
    ('[[ai_usage.agent_identities]]\npattern = "a"\nkind = "x"\n', "ai_usage.agent_identities[0].kind"),
    ('[ai_usage]\ntoken_users_as_agents = "yes"\n', "ai_usage.token_users_as_agents"),
])
def test_bad_rules_name_the_key_never_the_value(text: str, key: str) -> None:
    with pytest.raises(FleetConfigError) as caught:
        parse_fleet_config(text)
    message = str(caught.value)
    assert message.startswith(key)
    assert "unclosed" not in message and "login" not in message


def test_repository_rules_extend_the_fleet_rules_after_them() -> None:
    fleet = _config('[[ai_usage.agent_identities]]\npattern = "^a"\n')
    repo_config = {"ai_usage": {"agent_identities": [{"pattern": "^b"}, {"pattern": "^a"}]}}
    merged = merge_repo_sections(fleet, repo_config)
    assert merged.config["ai_usage"]["agent_identities"] == [{"pattern": "^a"}, {"pattern": "^b"}]
    bad = merge_repo_sections(empty_config(), {"ai_usage": {"agent_identities": [{"pattern": "("}]}})
    assert bad.config["ai_usage"] == {} and len(bad.warnings) == 1
    assert "(" not in bad.warnings[0]  # the bad pattern is never quoted


def test_headless_run_merges_fleet_and_repository_rules(repo: Path) -> None:
    """The registry input end to end: the fleet rule keeps index 0, the repository's
    .assess/config.toml rule follows, and counts-only keeps the integers only."""
    (repo / ".assess").mkdir()
    (repo / ".assess" / "config.toml").write_text(
        '[[ai_usage.agent_identities]]\npattern = "^devin-ai-integration"\nfield = "username"\n',
        encoding="utf-8")
    fleet = _config(f'[[ai_usage.agent_identities]]\npattern = "{PATTERN}"\n')
    scans = collect(repo, ["ai_usage"], window="5y", fleet_config=fleet)
    block = scans["ai_usage"]
    assert block["agent_identity_rule_commits"] == [2, 1]
    assert block["agent_authored_commits"] == 5
    reduced = counts_only_scans(scans)
    assert reduced["ai_usage"]["agent_identity_rule_commits"] == [2, 1]
    text = json.dumps(reduced)
    for needle in IDENTITY_STRINGS:
        assert needle not in text


@pytest.mark.parametrize(("email", "username", "expected"), [
    ("29139614+renovate[bot]@users.noreply.github.com", "renovate[bot]", True),
    ("bot@renovateapp.com", "", True),
    ("support@dependabot.com", "", True),
    ("", "dependabot", True),
    ("49699333+dependabot[bot]@users.noreply.github.com", "dependabot[bot]", True),
    ("dev@example.com", "renovate-fan", False),
    ("41898282+github-actions[bot]@users.noreply.github.com", "github-actions[bot]", False),
])
def test_is_dependency_bot_reads_the_pinned_rules(email: str, username: str,
                                                  expected: bool) -> None:
    """The public seam ``account_discovery`` uses (#1031): Renovate and Dependabot by
    e-mail or username, as ``AuthorMatcher`` buckets them."""
    assert agent_identity.is_dependency_bot(email, username) is expected
    bucket = agent_identity.AuthorMatcher().classify(email, username=username).bucket
    assert (bucket == agent_identity.DEPENDENCY_BOT) is expected
