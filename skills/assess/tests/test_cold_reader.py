"""The cold-reader test (#1102): the first screen of the tour's sample report answers three questions.

A newcomer reading only the top of the report must be able to say where the repository is,
what that means and what to do next, in one pass. CI has no model, so the cold reader here
is a parser that knows nothing of the scoring: it reads the report's first screen (every line
between the "Generated" stamp and the first ``## `` section: the top card, #1252) and answers
from the words alone. Its answers are checked against the level result the run computed and
the tables the words come from (the model's Step names, ``GATE_GAP``, ``GATE_TASK``), so a
change of wording moves the expected answer with it, and a card that stops answering fails.

The first screen must also hold no jargon a newcomer cannot read: no condition id, no layer,
no snake_case gate id and no catalogue count. A term the glossary defines may appear only as
a link to its glossary entry.

It must also be plain English (#1174), for readers whose first language is not English: no
sentence over ``plain_english.MAX_WORDS`` words and no idiom or phrasal verb from
``plain_english.IDIOMS`` (``plain_english.lint``, read on the visible words).

The report is the one ``assess_tour.py report --out`` writes, the same file the CI step
uploads as ``tour-sample-report``.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import TypedDict

import pytest

import assess_tour
from lib.card_facts import step_name, top_step
from lib.scoring.headline import GLOSSARY_URL
from lib.scoring.level_tally import default_model
from lib.top_card import GATE_GAP, GATE_TASK, STEP_GATES_URL

LINK = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
# Terms a newcomer would need the glossary for, and the marks of an internal name. "gate"
# is not here: the coverage task says "enforce a coverage gate in CI", the ordinary CI sense.
JARGON = ("layer", "condition", "practice", "level", "evidence grade", "truth tag", "catalogue",
          "measured", "verified", "contracts in place", "hazard")
INTERNAL = (
    re.compile(r"\blayer-\d"),           # a maturity-model row id
    re.compile(r"\b[a-z]+(?:_[a-z]+)+\b"),  # a snake_case id such as ci_runs_tests
    re.compile(r"\b\d+ of \d+ measured\b"),  # a catalogue count
)


class ColdReading(TypedDict):
    step: int
    of: int
    meaning: str
    next_step: int
    blocked_by: str
    actions: int
    done_when: int
    start_here: list[tuple[str, str]]


def first_screen(report: str) -> list[str]:
    """The lines between the "Generated" stamp and the first ``## `` section, blanks dropped."""
    lines = report.splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("_Generated "))
    end = next(i for i, line in enumerate(lines) if line.startswith("## "))
    return [line for line in lines[start + 1:end] if line.strip()]


def visible(line: str) -> str:
    """The line as read: link text without its URL, emphasis marks dropped."""
    return LINK.sub(r"\1", line).replace("**", "").strip()


def cold_read(screen: list[str]) -> ColdReading:
    """Answer "where are we, what does that mean, what next" from the first screen's words."""
    where = re.fullmatch(r"Step (\d+) of (\d+): (.+)\.", visible(screen[0]))
    assert where, f"the first line does not say where we are: {screen[0]!r}"
    blocks = [visible(line) for line in screen if visible(line).startswith("Blocks Step ")]
    assert len(blocks) == 1, f"one Blocks line expected, found {blocks}"
    what = re.fullmatch(r"Blocks Step (\d+): (.+?)\.", blocks[0])
    assert what, f"the Blocks line does not name the next Step and its gate: {blocks[0]!r}"
    start = [line for line in screen if visible(line).startswith("Start here:")]
    done = [line for line in screen if visible(line).startswith("Done when:")]
    return {
        "step": int(where.group(1)), "of": int(where.group(2)), "meaning": where.group(3),
        "next_step": int(what.group(1)), "blocked_by": what.group(2), "actions": len(start),
        "done_when": len(done),
        "start_here": [(m.group(1), m.group(2)) for line in start for m in LINK.finditer(line)],
    }


@pytest.fixture(scope="module")
def reports(tmp_path_factory: pytest.TempPathFactory) -> dict[str, tuple[assess_tour.TourResult, str]]:
    out: dict[str, tuple[assess_tour.TourResult, str]] = {}
    for label, fixed in (("sample", False), ("fixed", True)):
        path = tmp_path_factory.mktemp(label) / "tour-sample-report.md"
        result = assess_tour.report(path, fixed=fixed)
        out[label] = (result, path.read_text(encoding="utf-8"))
    return out


@pytest.mark.parametrize("label", ["sample", "fixed"])
def test_the_first_screen_says_where_what_and_next(
        reports: dict[str, tuple[assess_tour.TourResult, str]], label: str) -> None:
    result, report = reports[label]
    reading = cold_read(first_screen(report))
    step, gate = result["step"], result["next_gates"][0]
    assert step is not None and result["next_step"] == step + 1
    model = default_model()
    name = step_name(model, step)
    assert (reading["step"], reading["of"]) == (step, top_step(model))
    assert reading["meaning"] == name[:1].lower() + name[1:]
    assert (reading["next_step"], reading["blocked_by"]) == (step + 1, GATE_GAP[gate])
    # One call to action: the gate as a task, linked to how to do it, and when it is done.
    assert (reading["actions"], reading["done_when"]) == (1, 1)
    assert reading["start_here"] == [(GATE_TASK[gate][0].upper() + GATE_TASK[gate][1:], STEP_GATES_URL)]


def test_the_sample_reads_step_2_with_ci_next(reports: dict[str, tuple[assess_tour.TourResult, str]]) -> None:
    # The values the tour promises the newcomer, pinned once beside the general rule above.
    reading = cold_read(first_screen(reports["sample"][1]))
    assert (reading["step"], reading["blocked_by"]) == (2, GATE_GAP["ci_runs_tests"])
    reading = cold_read(first_screen(reports["fixed"][1]))
    assert (reading["step"], reading["blocked_by"]) == (3, GATE_GAP["coverage_gate"])


@pytest.mark.parametrize("label", ["sample", "fixed"])
def test_the_first_screen_holds_no_jargon(
        reports: dict[str, tuple[assess_tour.TourResult, str]], label: str) -> None:
    for line in first_screen(reports[label][1]):
        # A glossary term may stand only as a link to its entry: drop those links, then read.
        unlinked = LINK.sub(lambda m: "" if m.group(2).startswith(GLOSSARY_URL) else m.group(1), line)
        text = unlinked.replace("**", "").lower()
        assert not [term for term in JARGON if re.search(rf"\b{term}s?\b", text)], line
        assert not [p.pattern for p in INTERNAL if p.search(text)], line


@pytest.mark.parametrize("label", ["sample", "fixed"])
def test_the_first_screen_is_plain_english(
        reports: dict[str, tuple[assess_tour.TourResult, str]], label: str) -> None:
    """#1174: short sentences, no idioms, as a reader in a second language needs."""
    from lib.plain_english import lint
    assert [issue for line in first_screen(reports[label][1]) for issue in lint(visible(line))] == []


@pytest.mark.parametrize("label", ["sample", "fixed"])
def test_the_first_screen_points_to_the_glossary(
        reports: dict[str, tuple[assess_tour.TourResult, str]], label: str) -> None:
    pointers = [line for line in first_screen(reports[label][1]) if line.startswith("_New to these words?")]
    assert len(pointers) == 1 and f"]({GLOSSARY_URL})" in pointers[0]


def test_the_cold_reader_rejects_a_screen_that_does_not_answer() -> None:
    with pytest.raises(AssertionError, match="where we are"):
        cold_read(["Catalogue coverage: 1 of 5 measured"])
    with pytest.raises(AssertionError, match="one Blocks line"):
        cold_read(["Step 2 of 4: an agent opens pull requests, a person reviews each one."])


def test_first_screen_stops_at_the_first_section() -> None:
    report = "# T\n\n_Generated today._\n\nStep 1 of 4: x.\n\n## Since Last Run\n\nNext: no.\n"
    assert first_screen(report) == ["Step 1 of 4: x."]


def test_the_report_path_is_the_one_ci_uploads() -> None:
    workflow = (Path(__file__).resolve().parents[3] / ".github" / "workflows" / "tests.yml").read_text(
        encoding="utf-8")
    assert "scripts/assess_tour.py report --out" in workflow
    assert "tour-sample-report" in workflow
