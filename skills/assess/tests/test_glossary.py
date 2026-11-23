"""The plain-English glossary, ``docs/glossary.md`` (#1102 part B, item 7).

A newcomer meets Step, gate, practice, level and evidence grade in the first screen of the
report, so each has a short entry there, linked from its first use in the report template
(``skills/assess-findings/SKILL.md``), the README and the Map of Content. These tests pin
that every term is defined in one or two sentences, in the supportive voice, with no ids
or layer numbers, and that every link into the glossary lands on a heading.

    cd skills/assess && uv run --locked pytest tests/test_glossary.py
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from assess_report import GLOSSARY_LINE, render_summary_section
from lib.assess_diagrams import waffle_caption
from lib.levels import load_levels
from lib.signal_grades import DEFAULT_LEVELS, INPUT_GRADES
from lib.vocabulary import banned_hits

REPO_ROOT = Path(__file__).resolve().parents[3]
GLOSSARY = REPO_ROOT / "docs" / "glossary.md"
GLOSSARY_URL = "https://github.com/bjcoombs/ai-native-toolkit/blob/main/docs/glossary.md"
FINDINGS_SKILL = REPO_ROOT / "skills" / "assess-findings" / "SKILL.md"
README = REPO_ROOT / "README.md"
INDEX = REPO_ROOT / "docs" / "index.md"
SCORING_MODEL = REPO_ROOT / "skills" / "assess" / "references" / "scoring-model.md"

# The five terms #1102 names, then the ones the report uses that a newcomer would stumble on.
REQUIRED = ("Step", "Gate", "Practice", "Level", "Evidence grade")
ALSO_DEFINED = ("Condition", "Truth tag", "Not measured", "Catalogue coverage", "Maturity model")
# The template's glossary line links each of these at the summary, their first use.
TEMPLATE_TERMS = ("step", "gate", "practice", "level", "evidence-grade", "truth-tag")

_LINK = re.compile(r"\]\(([^)\s]+)\)")
_SENTENCE_END = re.compile(r"[.!?](?=\s|$)")


def _slug(heading: str) -> str:
    """GitHub's heading anchor: lower case, punctuation dropped, spaces as hyphens."""
    return re.sub(r"[^\w\- ]", "", heading.strip().lower()).replace(" ", "-")


def _entries() -> dict[str, str]:
    """Each ``### `` heading of the glossary with its body text."""
    entries: dict[str, str] = {}
    current: str | None = None
    for line in GLOSSARY.read_text(encoding="utf-8").splitlines():
        if line.startswith("### "):
            current = line[4:].strip()
            entries[current] = ""
        elif line.startswith("#"):
            current = None
        elif current is not None and line.strip():
            entries[current] = f"{entries[current]} {line.strip()}".strip()
    return entries


def _glossary_anchors(path: Path) -> list[str]:
    """The anchor of every link from ``path`` into the glossary ("" for a bare link)."""
    relative = Path("docs/glossary.md")
    anchors = []
    for target in _LINK.findall(path.read_text(encoding="utf-8")):
        base, _, anchor = target.partition("#")
        if base == GLOSSARY_URL or (base and (path.parent / base).resolve() == (REPO_ROOT / relative).resolve()):
            anchors.append(anchor)
    return anchors


def _glossary_anchors_in(text: str) -> list[str]:
    """The anchor of every absolute-URL link into the glossary within ``text``."""
    return [t.partition("#")[2] for t in _LINK.findall(text) if t.partition("#")[0] == GLOSSARY_URL]


def test_every_term_is_defined_in_one_or_two_sentences() -> None:
    entries = _entries()
    for term in (*REQUIRED, *ALSO_DEFINED):
        assert term in entries, f"docs/glossary.md has no '### {term}' entry"
        body = entries[term]
        assert body, f"the glossary entry for {term} is empty"
        sentences = len(_SENTENCE_END.findall(body))
        assert 1 <= sentences <= 2, f"the glossary entry for {term} has {sentences} sentences, not one or two"


def test_glossary_uses_the_supportive_voice() -> None:
    hits = banned_hits(GLOSSARY.read_text(encoding="utf-8"))
    assert not hits, f"docs/glossary.md uses words the vocabulary guard leaves out: {hits}"


def test_glossary_names_no_ids_or_layers() -> None:
    model = load_levels(DEFAULT_LEVELS)
    ids = {row["id"] for row in model["rows"]}
    ids |= {gate["id"] for step in model.get("steps", []) for gate in step["gates"]}
    ids |= set(INPUT_GRADES) | set(INPUT_GRADES.values())
    # A bare word such as the "instructions" gate is plain English; ids that read as ids are not.
    ids = {i for i in ids if "_" in i or "-" in i}
    assert ids, "the default maturity model should give ids to look for"
    text = GLOSSARY.read_text(encoding="utf-8")
    for ident in sorted(ids):
        assert ident not in text, f"docs/glossary.md names the id {ident!r}; say it in plain words"
    for word in (r"\blayers?\b", r"\bL[0-8]\b", r"\brubric\b"):
        assert not re.search(word, text, re.I), f"docs/glossary.md matches {word}; #1099 dropped layers"


@pytest.mark.parametrize("source", [FINDINGS_SKILL, README, INDEX, SCORING_MODEL],
                         ids=lambda p: p.relative_to(REPO_ROOT).as_posix())
def test_links_into_the_glossary_land_on_a_heading(source: Path) -> None:
    anchors = _glossary_anchors(source)
    assert anchors, f"{source.relative_to(REPO_ROOT).as_posix()} does not link the glossary"
    headings = {_slug(term) for term in _entries()}
    for anchor in anchors:
        assert not anchor or anchor in headings, (
            f"{source.relative_to(REPO_ROOT).as_posix()} links docs/glossary.md#{anchor}, which is no heading")


def test_report_template_links_each_term_at_the_summary() -> None:
    text = FINDINGS_SKILL.read_text(encoding="utf-8")
    summary = text.index("<paste `headline.summary_markdown`")
    line_start = text.index("_New to these words?", summary)
    assert line_start < text.index("## Since Last Run", summary), "the glossary line sits under the summary"
    line = text[line_start:text.index("\n", line_start)]
    linked = set(_glossary_anchors_in(line))
    assert linked >= set(TEMPLATE_TERMS), f"the template's glossary line leaves out {set(TEMPLATE_TERMS) - linked}"


def test_readme_links_the_assess_terms() -> None:
    linked = set(_glossary_anchors(README))
    assert {"step", "gate", "practice", "evidence-grade"} <= linked


def test_both_report_paths_carry_the_same_glossary_line() -> None:
    assert GLOSSARY_LINE in FINDINGS_SKILL.read_text(encoding="utf-8"), (
        "skills/assess-findings/SKILL.md must carry assess_report.GLOSSARY_LINE verbatim")
    assert set(_glossary_anchors_in(GLOSSARY_LINE)) >= set(TEMPLATE_TERMS)
    assert render_summary_section({}) == f"{GLOSSARY_LINE}\n\n", "the line renders without a summary too"


def test_waffle_caption_uses_level_as_the_glossary_does() -> None:
    # The glossary's level is a waffle column; the squares count toward catalogue coverage.
    caption = waffle_caption()
    assert "toward the level" not in caption
    assert "catalogue coverage" in caption
