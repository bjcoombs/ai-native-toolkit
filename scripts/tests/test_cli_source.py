"""
Guard tests for the RAW (untransformed) plugin SKILL.md sources.

The standalone ZIP pipeline transforms each SKILL.md for chat / Cowork, but the
Claude Code CLI ships the raw source unchanged. Any standalone-only wording
written as plain body prose — i.e. text that is neither inside a chat-skip
block nor positioned as a chat-replace default line — therefore survives
verbatim into the CLI build, where the model reads and obeys it.

This bit /huddle: standalone "team mode is not reachable" assertions leaked into
the CLI source and silently disabled CLI team mode (the skill's headline
capability) even with the Agent Teams flag live. test_integration.py only
checks the ZIP output; nothing checked the raw source. These tests close that
gap so the bug class cannot return.
"""
from pathlib import Path

import pytest

from standalone_skill_config import SKILLS

REPO_ROOT = Path(__file__).parent.parent.parent

# Phrases that are only true in the standalone (chat / Cowork) build. If any
# appears in a raw SKILL.md it ships to the CLI, where it is false and overrides
# the live capability-detection logic.
STANDALONE_ONLY_PHRASES = [
    "standalone build",
    "not reachable from here",
    "not available in standalone",
    "Claude Code only",
]

# The Agent Teams runtime removed fan-out broadcast: a SendMessage with to="*"
# is rejected ("broadcast (to: "*") is no longer supported — send a message per
# recipient"). Any raw SKILL.md instructing a broadcast ships to the CLI and
# stalls a live team-mode run — phases never get announced and findings never
# get shared. Sends must be addressed to a named recipient, so the literal is
# banned outright (even in "don't do this" prose), keeping this a dead-simple
# substring guard with no usage-vs-mention exceptions to get wrong.
BROADCAST_LITERALS = ['to: "*"', 'to="*"']


def _raw_skill_md(skill_name: str) -> str:
    cfg = SKILLS[skill_name]
    return (REPO_ROOT / cfg["source_dir"] / "SKILL.md").read_text("utf-8")


@pytest.mark.parametrize("skill_name", sorted(SKILLS))
def test_no_standalone_only_phrases_in_raw_source(skill_name):
    raw = _raw_skill_md(skill_name)
    leaked = [phrase for phrase in STANDALONE_ONLY_PHRASES if phrase in raw]
    assert not leaked, (
        f"{skill_name}/SKILL.md ships raw to the CLI and contains standalone-only "
        f"wording {leaked}. Move it into standalone_skill_config.py replacements "
        f"behind a chat-replace marker; the CLI default line must be CLI-correct."
    )


@pytest.mark.parametrize("skill_name", sorted(SKILLS))
def test_standalone_replacements_not_in_raw_source(skill_name):
    """Each replacement value is the STANDALONE wording. If it also appears in
    the raw source, the CLI default line was authored as standalone text instead
    of the CLI variant — exactly the leak that broke huddle team mode."""
    cfg = SKILLS[skill_name]
    raw = _raw_skill_md(skill_name)
    leaked = [
        key
        for key, value in cfg.get("replacements", {}).items()
        if value.strip() and value in raw
    ]
    assert not leaked, (
        f"{skill_name}/SKILL.md contains the standalone replacement text for "
        f"{leaked} verbatim. The line after a chat-replace marker is the CLI "
        f"default and must differ from the config replacement."
    )


@pytest.mark.parametrize("skill_name", sorted(SKILLS))
def test_no_broadcast_literal_in_raw_source(skill_name):
    """No SKILL.md may instruct a fan-out broadcast. to="*" was removed from the
    Agent Teams runtime; a broadcast send is rejected and a live team-mode huddle
    stalls. Both chair and members must address each teammate by name."""
    raw = _raw_skill_md(skill_name)
    leaked = [literal for literal in BROADCAST_LITERALS if literal in raw]
    assert not leaked, (
        f"{skill_name}/SKILL.md contains broadcast literal(s) {leaked}. The Agent "
        f"Teams runtime rejects to=\"*\"; send one SendMessage per named recipient. "
        f"Remove the literal even from explanatory prose (reword to e.g. "
        f"'all-recipients broadcast')."
    )


def test_huddle_raw_source_keeps_cli_team_mode_path():
    """The CLI source must positively describe the team-mode path, so a future
    edit cannot quietly strip it while still passing the negative checks."""
    raw = _raw_skill_md("huddle")
    assert "TeamCreate" in raw, "CLI team-mode infrastructure missing from raw huddle source"
    assert "flag enabled" in raw, (
        "CLI team-mode branch (Size 2+, flag enabled → team mode) missing from raw huddle source"
    )
