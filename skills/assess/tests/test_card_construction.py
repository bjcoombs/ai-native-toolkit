"""Construction tests for the report's first screen, the top card (#1253).

The card (#1252) replaced four summaries that each restated the Step. Nothing stopped a fifth,
so these tests render the deterministic report for each card state and read its first screen
(every paragraph after the "Generated" stamp, up to the first ``## `` heading or ``<details>``
fold, whichever comes first) and fail when:

- **a fact is stated twice, or a line states none.** Each line comes from a fact function that
  gives it a ``fact_id`` (``top_card.card_facts``, ``profiles.model_facts``,
  ``assess_report.report_facts``); a paragraph no fact function wrote, or a ``fact_id`` that
  appears twice, fails;
- **the card is over its word budget.** ``TOP_BUDGET`` words at the top Step, ``BUDGET``
  otherwise, counted on the visible words (link text, not URLs). The goldens
  ``fixtures/golden/card-<state>.md`` pin the first screen of each state; regenerate them with
  ``CARD_GOLDEN_REGEN=1`` (``make regen`` does);
- **a line carries no evidence.** Every paragraph holds a digit, a Markdown link or a
  backticked command or path (``lib/card_lint.lint_errors``, the rule ``assess_finalize.py``
  applies to each Top 3 ``done_when``, #1300).

A narrowed run (``--profile citizen``, #1268) keeps every section heading a full run under the
same profile shows, with the not-run line under it, and no two non-blank lines in a row are the
same (#1279 review: the findings and Structure Drift headings went missing and the keyhole
section read "Not run under profile citizen." twice).

The cold-reader release step for a card change is in ``docs/contributing/release.md``.
"""
from __future__ import annotations

import os
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

import lib.exec_summary as es
from assess_report import FINDINGS_HEADING, render_report, report_facts
from lib.card_facts import CardFact
from lib.card_lint import LINK, lint_errors
from lib.exec_summary import summarised, summary_facts
from lib.headline import headline
from lib.keyhole.keyhole_signals import render_findings_markdown
from lib.level_tally import default_model
from lib.top_card import FACT_IDS, PROFILES_URL
from test_headline import ci2  # noqa: F401 - ci2 is a fixture
from test_top_card import FIRST, KNOWN, ROLLUP, SHA, _below, _top

MODEL = default_model()
GOLDEN = Path(__file__).parent / "fixtures" / "golden"
TOP_BUDGET, BUDGET = 120, 180
# The rank-1 action's Start here and Done when are the model's words from actions.json; the
# fixture writes its check as a command in backticks, as a Done when should.
ACTION = {"start": '`rg -n "noqa: E402" skills/assess/scripts/assess_report.py`',
          "done_when": "`cd skills/assess && uv run --locked ruff check .` passes"}

# Each card state the goldens pin: (level, since, rollup, action), and whether it is the top Step.
STATES: dict[str, tuple[Any, Any, Any, Any, bool]] = {
    "top-step": (_top(), {**KNOWN, "closed_count": 1, "closed": [{"completed_sha": SHA}]}, None, ACTION, True),
    "below-top-step": (_below(), KNOWN, ROLLUP, None, False),
    "first-run": (_below(), FIRST, None, ACTION, False),
}
# Further states the facts and lint tests read: every since line and fallback the card can say.
EXTRA: dict[str, tuple[Any, Any, Any, Any, bool]] = {
    "since-not-known": (_below(), None, None, None, False),
    "gates-not-known": (_top(), {**KNOWN, "gates_gained": None, "gates_lost": None}, None, None, True),
    "closed-not-known": (_below(), {**KNOWN, "closed_count": None, "closed_reason": "shallow_clone"},
                         ROLLUP, ACTION, False),
    "top-no-action": (_top(0), KNOWN, None, None, True),
}


def _render(state: tuple[Any, Any, Any, Any, bool]) -> tuple[str, list[CardFact], dict[str, Any]]:
    """The report for a card state, the facts behind its first screen and its run context."""
    level, since, rollup, action, _ = state
    block = summarised(headline(level, MODEL), level, since, MODEL, None, rollup, SHA, action)
    ctx = {"headline": block, "level": level, "since_last_run": since}
    facts = summary_facts(level, MODEL, since, None, rollup, SHA, action)
    rendered = [{"fact_id": f["fact_id"], "text": md}
                for f, md in zip(facts, block["summary_markdown"].split("\n\n"))]
    return render_report(ctx, "demo"), [*rendered, *report_facts(ctx)], ctx  # type: ignore[list-item]


def first_screen(report: str) -> list[str]:
    """The paragraphs after the "Generated" stamp, up to the first ``## `` or ``<details>``."""
    lines = report.splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("_Generated "))
    end = next(i for i, line in enumerate(lines) if line.startswith(("## ", "<details>")))
    return [p.strip() for p in "\n".join(lines[start + 1:end]).split("\n\n") if p.strip()]


def visible_words(paragraphs: Sequence[str]) -> int:
    """Words a reader sees: link text without its URL, emphasis marks dropped."""
    return sum(len(LINK.sub(r"\1", p).replace("**", "").split()) for p in paragraphs)


def fact_errors(paragraphs: Sequence[str], facts: Sequence[CardFact]) -> list[str]:
    """A paragraph no fact function wrote, and a fact stated twice."""
    by_text = {f["text"]: f["fact_id"] for f in facts}
    errors, seen = [], set()
    for paragraph in paragraphs:
        fact_id = by_text.get(paragraph)
        if fact_id is None:
            errors.append(f"no fact_id: {paragraph!r}")
        elif fact_id in seen:
            errors.append(f"{fact_id} stated twice")
        seen.add(fact_id)
    return errors


def budget_errors(paragraphs: Sequence[str], top: bool) -> list[str]:
    budget = TOP_BUDGET if top else BUDGET
    words = visible_words(paragraphs)
    return [f"{words} words, over the budget of {budget}"] if words > budget else []


ALL = {**STATES, **EXTRA}


@pytest.mark.parametrize("name", list(ALL))
def test_each_fact_is_stated_once_by_a_fact_function(name: str) -> None:
    report, facts, _ = _render(ALL[name])
    assert fact_errors(first_screen(report), facts) == []
    ids = [f["fact_id"] for f in facts]
    assert len(ids) == len(set(ids)) and set(ids) <= {*FACT_IDS, "narrowed", "glossary"}


def test_a_fact_function_called_twice_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    real = es.card_facts

    def twice(*args: Any) -> list[CardFact]:
        facts = real(*args)
        return [*facts, *(f for f in facts if f["fact_id"] == "holding")]

    monkeypatch.setattr(es, "card_facts", twice)
    report, facts, _ = _render(STATES["top-step"])
    assert fact_errors(first_screen(report), facts) == ["holding stated twice"]


def test_a_line_no_fact_function_wrote_fails() -> None:
    report, facts, _ = _render(STATES["top-step"])
    added = report.replace("_New to these words?", "Everything else is fine.\n\n_New to these words?", 1)
    assert fact_errors(first_screen(added), facts) == ["no fact_id: 'Everything else is fine.'"]


@pytest.mark.parametrize("name", list(STATES))
def test_each_card_state_keeps_its_word_budget_and_its_golden(name: str) -> None:
    report, _, _ = _render(STATES[name])
    screen = first_screen(report)
    assert budget_errors(screen, STATES[name][4]) == []
    golden = GOLDEN / f"card-{name}.md"
    text = "\n\n".join(screen) + "\n"
    if os.environ.get("CARD_GOLDEN_REGEN") == "1":
        golden.write_text(text, encoding="utf-8", newline="\n")
    assert text == golden.read_text(encoding="utf-8")


def test_a_200_word_top_step_card_fails_its_budget() -> None:
    report, _, _ = _render(STATES["top-step"])
    screen = first_screen(report)
    padded = [*screen, " ".join(["word"] * (200 - visible_words(screen)))]
    assert visible_words(padded) == 200
    assert budget_errors(padded, top=True) == ["200 words, over the budget of 120"]
    assert budget_errors(padded[:-1], top=True) == []


@pytest.mark.parametrize("name", list(ALL))
def test_every_first_screen_line_carries_a_number_link_or_command(name: str) -> None:
    report, _, _ = _render(ALL[name])
    assert lint_errors(first_screen(report)) == []


def test_a_line_with_no_number_link_or_command_fails() -> None:
    assert lint_errors(["Since last run: not known.", "Step 3 of 4.", "[x](y)", "run `/assess`"]) == \
        ["Since last run: not known."]


def test_the_no_steps_and_profile_lines_carry_a_link() -> None:
    from lib.top_card import card_markdown

    flat = card_markdown(["This maturity model defines no Steps."])
    custom = card_markdown(["Maturity model: my.toml, a custom file, not the default.",
                            "Not every practice met (model: my.toml). Missing: Tests.",
                            "Not in the default model: Tests.", "Step 1 of 1."], lead_at=3)
    assert lint_errors([flat, *custom.split("\n\n")]) == []
    assert "`my.toml`" in custom
    # a row title holding "profile" is never taken for a profile name (#1296 review)
    titles = card_markdown(["Maturity model: my.toml, a custom file, not the default.",
                            "Not ready (model: my.toml). Missing: Complexity profile tracked (not met).",
                            "Not in the default model: Test profile coverage, level 2.", "Step 1 of 1."], lead_at=3)
    assert "/profiles/" not in titles
    handover = card_markdown(["Maturity model: profile handover, not the default.",
                              "Ready for handover (profile: handover).", "Step 4 of 4."], lead_at=2)
    assert handover.split("\n\n")[1] == f"Ready for handover ([profile: handover]({PROFILES_URL}/handover.toml))."


def test_the_report_names_the_findings_heading_the_core_writes() -> None:
    assert render_findings_markdown([], []).startswith(FINDINGS_HEADING)


def _headings(report: str) -> list[str]:
    return [line for line in report.splitlines() if line.startswith("## ")]


def _repeats(report: str) -> list[str]:
    lines = [line for line in report.splitlines() if line.strip()]
    return [a for a, b in zip(lines, lines[1:]) if a == b]


def test_a_narrowed_run_keeps_every_heading_and_repeats_no_line(ci2: Path) -> None:  # noqa: F811
    from test_scan_plan import _core

    narrow = render_report(_core(ci2, "--profile", "citizen"), "ci2")
    full = render_report(_core(ci2, "--profile", "citizen", "--all-scans"), "ci2")
    missing = [h for h in _headings(full) if h not in _headings(narrow)]
    assert missing == []
    for heading in (FINDINGS_HEADING, "## Structure Drift"):
        section = narrow.split(f"{heading}\n", 1)[1].split("\n## ", 1)[0]
        assert section.strip().splitlines()[0] == "_Not run under profile citizen._", heading
    assert _repeats(narrow) == [] and _repeats(full) == []
    assert lint_errors(first_screen(narrow)) == []


def test_repeats_finds_two_identical_lines_in_a_row() -> None:
    assert _repeats("a\n\n_x._\n\n_x._\n") == ["_x._"]
    assert _repeats("a\n\n_x._\n\n## H\n\n_x._\n") == []
