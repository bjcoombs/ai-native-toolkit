"""``fleet-view.html``: the generated page text talks about the repositories, never about the tool.

The page names no toolkit issue (``#<digits>``), bug, limitation or roadmap item, and every
not-measured reading says how to confirm it. Checked on the freshly built fixture page in each
output mode (``tests/fleet_page_fixture.py``): the whole HTML (page script, embedded bundle and
inlined diagrams) and the text the script renders under Node for every unit, repository and
hover, with the runner of ``tests/test_fleet_page.py``.
"""
from __future__ import annotations

import json
import re
import subprocess
from typing import Any

import pytest

import fleet_page_fixture as fx
from lib.fleet import fleet_step_movement as movement
from lib.fleet.fleet_page_data import page_bundle
from test_fleet_page import NODE, RUNNER, _parts, _text

# An issue or pull request number: ``#`` and digits, not part of a word or an entity (``&#160;``)
# and not a colour value (``fill:#238443``, ``fill="#238443"``).
ISSUE_REF = re.compile(r"(?<![\w&#:=\"'\\])#\d+\b")
# Wording about the tool or its tracker rather than about the repositories (case-insensitive).
TOOL_WORDING = re.compile("|".join([
    r"\btoolkit\b", r"\bscanner\b", r"\bbugs?\b", r"\broadmap\b", r"\blimitations?\b",
    r"\bknown issues?\b", r"\b(?:issue|ticket|pull request|PR) ?#", r"\bnot (?:yet )?(?:supported|implemented)\b",
    r"\bfuture (?:release|version)s?\b", r"\bfollow-up\b", r"\btracked (?:in|by|separately)\b",
    r"\b(?:can't|cannot|can not|doesn't|does not) (?:\w+ ){0,3}yet\b",
]), re.IGNORECASE)
# How a not-measured reading is confirmed, in plain words: the page script's ``CONFIRM``.
CONFIRM = "full assessment run"


def tool_talk(text: str) -> list[str]:
    """Every issue reference and tool-limitation phrase in ``text``, ``<style>`` blocks left out."""
    text = re.sub(r"<style\b[^>]*>.*?</style\s*>", " ", text, flags=re.S | re.I)
    return [m.group(0) for m in ISSUE_REF.finditer(text)] + [m.group(0) for m in TOOL_WORDING.finditer(text)]


def _rendered(name: str) -> dict[str, Any]:
    if NODE is None:
        pytest.skip("node is not on PATH")
    script, bundle = _parts(fx.goldens()[f"expected-{name}.html"])
    done = subprocess.run([NODE, "-e", RUNNER], input=json.dumps({"script": script, "bundle": bundle}),
                          capture_output=True, text=True, encoding="utf-8", errors="replace", check=True)
    out: dict[str, Any] = json.loads(done.stdout)
    return out


@pytest.mark.parametrize("text", [
    "Not measured, see #1335.", "fixed in PR #12", "the scanner can't read this yet",
    "a known bug in the tool", "on the roadmap", "this toolkit does not read it", "not supported yet",
    "tracked in a follow-up", "a limitation of the scan",
])
def test_the_guard_finds_issue_numbers_and_tool_wording(text: str) -> None:
    assert tool_talk(text)


@pytest.mark.parametrize("text", [
    "<style>.a {fill:#123456}</style>3 repositories", '.t-top {fill:#238443}', '<rect fill="#238443"/>',
    '{\\"fill\\":\\"#238443\\"}', "&#160;Step 2", "not held yet", "Step 2 · Docs",
    "no Step moves are counted yet", "debugging", "issues of the repository",
])
def test_the_guard_leaves_colours_entities_and_plain_text_alone(text: str) -> None:
    assert tool_talk(text) == []


@pytest.mark.parametrize("name", sorted(fx.VARIANTS))
def test_the_page_names_no_toolkit_issue_bug_or_roadmap(name: str) -> None:
    assert tool_talk(fx.goldens()[f"expected-{name}.html"]) == []
    out = _rendered(name)
    for page in [*out["pages"], out["first"], *(t["title"] for t in out["titles"])]:
        assert tool_talk(page) == []


def test_reasons_a_comparison_gives_name_no_tool_limitation() -> None:
    """The Step movement grid's reasons reach the front screen's "Not compared" line."""
    for reason in (movement.UNREADABLE, movement.NO_JOIN):
        assert tool_talk(reason) == [], reason


@pytest.mark.parametrize("name", sorted(fx.VARIANTS))
def test_every_not_measured_reading_names_how_to_confirm_it(name: str) -> None:
    """Each section that shows a not-measured reading says how to confirm it once, in its visible
    text, and each hover that reads one says it too."""
    out = _rendered(name)
    sections = 0
    for page in out["pages"]:
        for section in re.split(r"(?=<h2>)", page):
            if re.search(r"not measured", _text(section), re.I):
                sections += 1
                assert _text(section).count(CONFIRM) == 1, _text(section)[:200]
    titles = [t["title"] for t in out["titles"] if re.search(r"not measured", t["title"], re.I)]
    assert sections and titles  # the fixture fleet has a not-measured cell and a failed scan
    for title in titles:
        assert CONFIRM in title, title
        # a full run alone never settles a reason that needs the platform: the line says what it needs
        assert "hosting settings" in title and "confirms it" not in title, title


@pytest.mark.parametrize("unread", [0, 1])
def test_the_packs_section_carries_the_note_only_when_a_pack_check_was_not_measured(unread: int) -> None:
    """A fully read pack run shows no not-measured reading, so its section carries no note."""
    if NODE is None:
        pytest.skip("node is not on PATH")
    script, bundle = _parts(fx.goldens()["expected-counts-only.html"])
    roll = [{"name": "team", "version": "1", "sha256": "a" * 64, "repos": 3, "practices": [
        {"condition": "team-adrs.1", "row": "team-adrs", "title": "Decision records", "level": 1,
         "met": 1, "measured": 3 - unread, "not_measured": unread}]}]
    bundle["packs"] = page_bundle({"run": {}, "views": [], "repos": [], "packs": roll}, None,
                                  {"version": "x", "rows": []})["packs"]
    runner = (RUNNER.split("const out")[0] + "process.stdout.write(api.renderPage(b, {view: b.views[0].view, "
              "unit: b.views[0].units[0].key, repo: null}, b.targets, false));")
    page = subprocess.run([NODE, "-e", runner], input=json.dumps({"script": script, "bundle": bundle}),
                          capture_output=True, text=True, encoding="utf-8", errors="replace", check=True).stdout
    packs = page.split("<h2>Practice packs</h2>")[1].split("<h2>")[0]
    assert (CONFIRM in packs) == bool(unread)


def test_a_not_measured_condition_s_drill_down_says_how_to_confirm_it_once() -> None:
    """The condition tier lists the hover's lines, which carry the confirmation already."""
    if NODE is None:
        pytest.skip("node is not on PATH")
    script, bundle = _parts(fx.goldens()["expected-full.html"])
    state = {"view": "ownership", "unit": "grp/other", "repo": "grp/other/proj3", "tag": "docs",
             "cond": {"row": 0, "level": 1}}
    runner = RUNNER.split("const out")[0] + "process.stdout.write(api.renderPage(b, input.state, b.targets, false));"
    page = subprocess.run([NODE, "-e", runner], input=json.dumps({"script": script, "bundle": bundle, "state": state}),
                          capture_output=True, text=True, encoding="utf-8", errors="replace", check=True).stdout
    drill = page.split("<h2>Drill-down</h2>")[1].split("<h2>")[0]
    assert "State: not measured" in drill
    assert _text(drill).count(CONFIRM) == 1
