"""Wording and padding inside a section never lower the instruction-file penalty (issue #619).

Follow-up to #560 (PR #613): monotonicity held for lines added outside a
section, but a change inside one could move it from fetch-on-need to always-on
(where it is free under 200 lines) or across a cap boundary. Each test below is
named after the transformation it applies and asserts the penalty does not
fall:

- prose padding diluting a fenced command block (``command_reference``);
- a heading rename onto an always-on word, or ``Never`` lines added, on a
  section a size signal (``command_reference`` or ``long_section``) marks
  relocatable;
- a section growing across ``LONG_SECTION_MIN_LINES`` when the relocatable
  term is at its cap, or the file sits near the B-cap line;
- any section moving from always-on to fetch-on-need, on the size terms alone.
"""
from __future__ import annotations

import random

import pytest

from lib.agent_instructions_grader import (
    ALWAYS_ON_GRADE_CAP_LINES,
    ALWAYS_ON_LINES_PER_POINT,
    RELOCATABLE_LINES_PER_POINT,
    RELOCATABLE_MAX_PENALTY,
    WORDS_PER_LINE,
    grade_instructions,
    placement_penalty,
)
from lib.instruction_placement import LONG_SECTION_MIN_LINES, PlacementFile, analyse_file

RELOCATABLE_CAP_LINES = RELOCATABLE_MAX_PENALTY * RELOCATABLE_LINES_PER_POINT


def _sub(text: str) -> dict[str, int]:
    return grade_instructions(text, 1, repo=None, path="CLAUDE.md").subscores


def _rules(lines: int) -> str:
    """An always-on block of ``lines`` lines (a rules section)."""
    out = ["# CLAUDE.md", "## Rules"]
    out += [f"- Never merge change {i} before the release owner signs it off." for i in range(lines - 2)]
    return "\n".join(out) + "\n"


def _prose(heading: str, lines: int) -> str:
    out = [f"## {heading}"]
    out += [f"Note {i}: the service talks to the billing API through a queue." for i in range(lines - 1)]
    return "\n".join(out) + "\n"


def _commands(heading: str, fenced: int, prose: int = 0, directives: int = 0) -> str:
    """A section of ``fenced`` fenced lines (fences included), then prose and directives."""
    out = [f"## {heading}", "```bash"]
    out += [f"kubectl get pods -n team-{i} --selector app=service-{i}" for i in range(fenced - 2)]
    out += ["```"]
    out += [f"The team-{i} namespace holds the service of the same name." for i in range(prose)]
    out += [f"- Never delete namespace team-{i} by hand." for i in range(directives)]
    return "\n".join(out) + "\n"


def _non_increasing_score(texts: list[str]) -> None:
    """Penalty and B cap never fall, and the score never rises, along ``texts``."""
    grades = [grade_instructions(t, 1, repo=None, path="CLAUDE.md") for t in texts]
    penalties = [g.subscores["bloat_penalty"] for g in grades]
    capped = [g.subscores["size_grade_capped"] for g in grades]
    scores = [g.score for g in grades]
    assert penalties == sorted(penalties), penalties
    assert capped == sorted(capped), capped
    assert scores == sorted(scores, reverse=True), scores


# --- command_reference density ----------------------------------------------------------


def test_padding_a_command_section_with_prose_never_lowers_the_penalty() -> None:
    """The issue case: a 20-line kubectl fence padded with 30 prose lines fell
    under half fenced, became always-on and dropped its penalty from 4 to 0."""
    base = _rules(40)
    texts = [base + _commands("Kubernetes commands", 22, prose=n) for n in range(0, 80, 3)]
    assert _sub(texts[0])["relocatable_penalty"] == 3
    _non_increasing_score(texts)


def test_prose_padding_keeps_command_reference() -> None:
    text = _rules(40) + _commands("Kubernetes commands", 22, prose=40)
    record = analyse_file(text, "CLAUDE.md", "instruction", {})
    [move] = record["relocatable"]
    assert "command_reference" in move["signals"]


# --- wording: heading renames and directive lines -----------------------------------------


@pytest.mark.parametrize("fenced", [30, 295])
@pytest.mark.parametrize("heading", [
    "Kubernetes conventions", "Kubernetes rules", "Kubernetes gotchas",
    "Critical Kubernetes notes", "Kubernetes: never guess",
])
def test_renaming_a_command_section_onto_an_always_on_word_never_lowers_the_penalty(
    fenced: int, heading: str,
) -> None:
    base = _rules(40) + _prose("Background", 10)
    before = _sub(base + _commands("Kubernetes commands", fenced))
    after = _sub(base + _commands(heading, fenced))
    assert before["relocatable_penalty"] > 0
    assert after["bloat_penalty"] >= before["bloat_penalty"]


@pytest.mark.parametrize("fenced", [30, 295])
def test_adding_never_lines_to_a_command_section_never_lowers_the_penalty(fenced: int) -> None:
    base = _rules(40) + _prose("Background", 10)
    texts = [base + _commands("Kubernetes commands", fenced, directives=n) for n in range(6)]
    assert _sub(texts[0])["relocatable_penalty"] > 0
    _non_increasing_score(texts)


@pytest.mark.parametrize("change", ["rename", "directives"])
def test_wording_on_a_long_scoped_section_never_lowers_the_penalty(change: str) -> None:
    """``long_section`` is a size signal too: a 70-line Python section does not
    become always-on when renamed to "Python conventions" or when a third of
    its lines turn into Never rules."""
    base = _rules(40)
    plain = _prose("Python notes", 70)
    if change == "rename":
        changed = plain.replace("Python notes", "Python conventions")
    else:
        changed = plain + "".join(f"- Never import vendored module {i}.\n" for i in range(30))
    before, after = _sub(base + plain), _sub(base + changed)
    assert before["relocatable_penalty"] == 7
    assert after["bloat_penalty"] >= before["bloat_penalty"]


# --- the cap boundary at LONG_SECTION_MIN_LINES -------------------------------------------


@pytest.mark.parametrize(("always_on", "fetch"), [
    (150, 0), (230, 0), (380, 0), (430, 0),
    (150, 290), (230, 300), (380, 320), (60, 400),
])
def test_a_section_crossing_the_long_section_line_never_lowers_the_penalty(
    always_on: int, fetch: int,
) -> None:
    """A scoped section growing from 55 to 65 lines turns fetch-on-need at 60.
    With the relocatable term at its cap that used to cut the always-on term by
    six points with no offset, and near 400 always-on lines lift the B cap."""
    base = _rules(always_on)
    if fetch:
        base += _commands("Kubernetes commands", fetch)
    texts = [base + _prose("Python notes", n)
             for n in range(LONG_SECTION_MIN_LINES - 5, LONG_SECTION_MIN_LINES + 6)]
    _non_increasing_score(texts)


def test_crossing_at_the_relocatable_cap_keeps_the_b_cap() -> None:
    """The PR #613 case: relocatable already at 30, 345 always-on lines plus a
    59-line section; one more line used to drop always-on to 345 and lift the cap."""
    base = _rules(345) + _commands("Kubernetes commands", 300)
    at_59 = _sub(base + _prose("Python notes", 59))
    at_60 = _sub(base + _prose("Python notes", 60))
    assert at_59["size_grade_capped"] == 1
    assert at_60["size_grade_capped"] == 1
    assert at_60["bloat_penalty"] >= at_59["bloat_penalty"]


# --- the size terms on their own ----------------------------------------------------------


def _split(always_on: int, fetch: int, a_words: int) -> PlacementFile:
    total = always_on + fetch
    return {
        "path": "CLAUDE.md", "kind": "instruction", "total_lines": total,
        "always_on_lines": always_on,
        "always_on_share": round(always_on / total, 2) if total else 0.0,
        "always_on_words": a_words,
        "fetch_on_need_lines": fetch, "unclassified_lines": 0, "sections_total": 1,
        "flagged": False, "relocatable": [], "relocatable_total": 0,
    }


def test_moving_any_section_to_fetch_on_need_never_lowers_the_size_terms() -> None:
    """Property over random splits: moving a section of ``k`` lines and ``w``
    words from always-on to fetch-on-need, the file unchanged, never lowers the
    summed penalty or lifts the B cap. Covers the two-ceiling rounding, the
    relocatable cap, and sections of very long lines."""
    rng = random.Random(619)
    for _ in range(5000):
        always_on = rng.randint(0, 900)
        fetch = rng.randint(0, 600)
        k = rng.randint(1, max(1, always_on))
        if k > always_on:
            continue
        per_line = rng.choice([1, 5, 12, 30, 80])
        a_words = always_on * rng.choice([1, 5, 12, 30])
        f_words = fetch * rng.choice([1, 5, 12, 30])
        w = min(a_words, k * per_line)
        before = placement_penalty(_split(always_on, fetch, a_words), f_words)
        after = placement_penalty(_split(always_on - k, fetch + k, a_words - w), f_words + w)
        total_before = before["always_on_penalty"] + before["relocatable_penalty"]
        total_after = after["always_on_penalty"] + after["relocatable_penalty"]
        case = (always_on, fetch, k, a_words, f_words, w)
        assert total_after >= total_before, case
        assert after["capped"] >= before["capped"], case


def test_b_cap_reads_the_whole_loaded_file() -> None:
    """Reclassification cannot lift the B cap: it reads every loaded line (or
    word-equivalent), always-on or not, so only moving text out of the file does."""
    assert placement_penalty(_split(300, 101, 300 * 5), 101 * 5)["capped"] is True
    assert placement_penalty(_split(300, 100, 300 * 5), 100 * 5)["capped"] is False
    words = (ALWAYS_ON_GRADE_CAP_LINES + 1) * WORDS_PER_LINE
    assert placement_penalty(_split(100, 50, words - 600), 600)["capped"] is True


def test_fetch_lines_past_the_relocatable_cap_count_as_always_on() -> None:
    """Past 300 fetch-on-need lines the relocatable term charges nothing more, so
    the rest still count toward the always-on term instead of going free."""
    at_cap = placement_penalty(_split(250, RELOCATABLE_CAP_LINES, 250 * 5))
    past = placement_penalty(_split(250, RELOCATABLE_CAP_LINES + 60, 250 * 5))
    assert at_cap == {"always_on_penalty": 5, "relocatable_penalty": 30, "capped": True}
    assert past["always_on_penalty"] == 11


def test_both_terms_round_on_the_same_line_unit() -> None:
    """``placement_penalty`` sums both terms in lines and rounds once, which
    only splits back into points cleanly while the two units match."""
    assert ALWAYS_ON_LINES_PER_POINT == RELOCATABLE_LINES_PER_POINT
