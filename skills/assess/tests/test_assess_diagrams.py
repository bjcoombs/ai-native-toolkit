"""Tests for the /assess diagrams (issue #942): lib/assess_diagrams.py.

The goldens under ``tests/fixtures/assess_diagrams/`` lock the whole render; the semantic
tests below read the squares back out of the SVG and hold them to the level result.
Regenerate the goldens with ``uv run --locked python tests/assess_diagrams_fixture.py``
from ``skills/assess``.
"""
from __future__ import annotations

import copy
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from assess_core import build_and_publish_run
from assess_diagrams_fixture import FIXTURES, LEVEL, MODEL_ROWS, goldens
from assess_report import render_report
from lib import assess_diagrams as ad
from lib.level_result_types import LevelResult
from lib.level_tally import run_context_level
from lib.vocabulary import IN_PLACE, banned_hits
from svg_cascade import overridden_text_attributes
from test_decomposition_parity import _build_parity_fixture

SVG_NS = "{http://www.w3.org/2000/svg}"
_TEXT = re.compile(r"<(text|title|desc)\b[^>]*>(.*?)</\1>", re.S)


def _waffle(level: LevelResult = LEVEL) -> str:
    return ad.render_waffle(level, ad._titles(MODEL_ROWS))


def _squares(content: str) -> list[ET.Element]:
    root = ET.fromstring(content.encode("utf-8"))
    return [r for r in root.iter(f"{SVG_NS}rect") if "data-category" in r.attrib]


def _prose(content: str) -> str:
    return "\n".join(re.sub(r"<[^>]+>", " ", body) for _, body in _TEXT.findall(content))


# --- goldens and determinism ----------------------------------------------------


@pytest.mark.parametrize("name", sorted(goldens()))
def test_render_matches_golden(name: str) -> None:
    assert goldens()[name] == (FIXTURES / name).read_text(encoding="utf-8")


def test_two_renders_are_byte_identical() -> None:
    assert ad.build_diagrams({"level": copy.deepcopy(LEVEL)}, MODEL_ROWS) == \
        ad.build_diagrams({"level": copy.deepcopy(LEVEL)}, MODEL_ROWS)


def test_stylesheet_never_overrides_a_text_attribute() -> None:
    assert overridden_text_attributes(_waffle()) == []


# --- the squares equal the level result -----------------------------------------


def _assert_matches(level: LevelResult, content: str) -> None:
    drawn = [s.attrib["data-category"] for s in _squares(content)]
    assert len(drawn) == len(level["conditions"])
    assert drawn == [ad.category(c) for c in level["conditions"]]
    assert sum(category in ad.COUNTED for category in drawn) == level["tally"]["met"]
    assert drawn.count("not_measured") == level["tally"]["not_measured"]


def test_fixture_squares_match_the_conditions_and_the_tally() -> None:
    _assert_matches(LEVEL, _waffle())


def test_real_level_result_squares_match(tmp_path: Path) -> None:
    ctx = build_and_publish_run(repo_root=_build_parity_fixture(tmp_path / "repo"), run_date="2026-01-01")
    level = run_context_level(ctx)
    written = (tmp_path / "repo" / ".assess" / ad.WAFFLE_FILE).read_text(encoding="utf-8")
    _assert_matches(level, written)
    assert written == ad.build_diagrams(ctx)[0]["svg"]


def test_category_reads_state_grade_and_counted() -> None:
    by_id = {c["id"]: ad.category(c) for c in LEVEL["conditions"]}
    assert by_id["layer-5-ci.1"] == "verified"
    assert by_id["layer-0-instructions.2"] == "measured"
    assert by_id["layer-0-instructions.1"] == "listed"  # declared
    assert by_id["layer-5-ci.2"] == "listed"  # config_only
    assert by_id["guardrails.3"] == "listed"  # no producer grade
    assert by_id["review.3"] == "unmet"


def test_declined_has_its_own_colour_and_not_applicable_is_an_outline() -> None:
    squares = {s.attrib["data-category"]: s for s in _squares(_waffle())}
    assert squares["declined"].attrib["class"].split()[0] == "f-ev-declined"
    assert ad.ROLES["ev-declined"] == ad.DECLINED_COLOURS
    assert len(set(ad.ROLES.values())) == len(ad.ROLES)
    outline = squares["not_applicable"]
    assert outline.attrib["class"] == "f-bg s-muted" and "fill" not in outline.attrib


def test_not_measured_is_hatched_with_its_reason_in_the_tooltip_and_key() -> None:
    content = _waffle()
    hatched = [s for s in _squares(content) if s.attrib["data-category"] == "not_measured"]
    assert hatched and all(s.attrib["fill"] == "url(#fleet-hatch)" for s in hatched)
    tips = [s.find(f"{SVG_NS}title").text for s in hatched]  # type: ignore[union-attr]
    assert tips[0].endswith("(needs GitHub or GitLab access)")  # type: ignore[union-attr]
    assert "(waits on CI and the agent's local check level 3)" in tips[1]  # type: ignore[operator]
    assert "needs GitHub or GitLab access: 2" in content
    assert "waits on CI and the agent's local check level 3: 1" in content


def test_a_cut_row_label_carries_the_full_title() -> None:
    assert "Guardrails stated in the instructio…<title>Guardrails stated in the instruction files" in _waffle()


# --- captions -------------------------------------------------------------------


def test_text_names_no_layer_number_and_no_banned_word(tmp_path: Path) -> None:
    ctx = build_and_publish_run(repo_root=_build_parity_fixture(tmp_path / "repo"), run_date="2026-01-01")
    real = ad.build_diagrams(ctx)
    texts = {"fixture svg": _prose(_waffle()), "fixture section": goldens()["expected-section.md"],
             "real svg": _prose(real[0]["svg"] or ""), "real section": ad.diagrams_section(real)}
    for name, content in texts.items():
        assert not re.search(r"\blayer[- ]\d", content, re.I), name
        assert not banned_hits(content), (name, banned_hits(content))


def test_section_opens_with_in_place_and_links_the_file() -> None:
    section = goldens()["expected-section.md"]
    lines = section.splitlines()
    assert lines[0] == "## Evidence behind the level"
    assert lines[2] == f"{IN_PLACE} 3 counted conditions met (2 verified, 1 measured) and 3 listed from config or a declaration."
    assert "[![Evidence behind the level](./evidence-waffle.svg)](./evidence-waffle.svg)" in section
    assert ad.waffle_caption() in section


def test_row_label_drops_the_layer_prefix() -> None:
    assert ad.row_label("Layer 0: agent instructions") == "Agent instructions"
    assert ad.row_label("Documentation freshness") == "Documentation freshness"
    assert ad.row_label("Layer 3:") == "Layer 3:"


def test_wrap_keeps_a_long_word_on_its_own_line() -> None:
    assert ad.wrap("a bb ccccc d", 4) == ["a bb", "ccccc", "d"]


# --- not drawn ------------------------------------------------------------------


@pytest.mark.parametrize("ctx, reason", [
    ({}, "the run has no level result"),
    ({"level": {"available": False}}, "the run has no level result"),
    ({"level": {**LEVEL, "conditions": []}}, "the maturity model has no conditions."),
    ({"level": {**LEVEL, "conditions": [{**LEVEL["conditions"][0], "state": "odd"}]}},
     "the renderer raised ValueError."),
])
def test_without_conditions_nothing_is_drawn_and_the_reason_is_said(ctx: dict[str, object], reason: str) -> None:
    [diagram] = ad.build_diagrams(ctx, MODEL_ROWS)
    assert diagram["svg"] is None and reason in (diagram["skipped"] or "")
    section = ad.diagrams_section([diagram])
    assert f"Not drawn: {diagram['skipped']}" in section and "](./" not in section
    assert section.splitlines()[2].startswith(IN_PLACE)


def test_unknown_category_raises() -> None:
    with pytest.raises(ValueError):
        ad.cell(0, 0, "odd")


def test_write_removes_a_stale_file_and_a_symlink(tmp_path: Path) -> None:
    drawn = ad.build_diagrams({"level": LEVEL}, MODEL_ROWS)
    assert ad.write_diagrams(tmp_path, drawn) == [tmp_path / ad.WAFFLE_FILE]
    assert (tmp_path / ad.WAFFLE_FILE).read_bytes() == goldens()["expected-evidence-waffle.svg"].encode("utf-8")
    assert ad.write_diagrams(tmp_path, ad.build_diagrams({}, MODEL_ROWS)) == []
    assert not (tmp_path / ad.WAFFLE_FILE).exists()


def test_write_never_writes_through_a_symlink(tmp_path: Path) -> None:
    target = tmp_path / "elsewhere.svg"
    target.write_text("keep", encoding="utf-8")
    try:
        (tmp_path / ad.WAFFLE_FILE).symlink_to(target)
    except OSError:  # pragma: no cover - Windows without symlink rights
        pytest.skip("symlinks unavailable")
    ad.write_diagrams(tmp_path, ad.build_diagrams({"level": LEVEL}, MODEL_ROWS))
    assert target.read_text(encoding="utf-8") == "keep"
    assert not (tmp_path / ad.WAFFLE_FILE).is_symlink()


def test_report_links_the_waffle_the_run_wrote(tmp_path: Path) -> None:
    repo = _build_parity_fixture(tmp_path / "repo")
    report = render_report(build_and_publish_run(repo_root=repo, run_date="2026-01-01"), "r")
    assert "[![Evidence behind the level](./evidence-waffle.svg)](./evidence-waffle.svg)" in report
    assert (repo / ".assess" / ad.WAFFLE_FILE).is_file()


def test_a_path_that_cannot_be_written_is_skipped_not_raised(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / ad.WAFFLE_FILE).mkdir()
    assert ad.write_diagrams(tmp_path, ad.build_diagrams({"level": LEVEL}, MODEL_ROWS)) == []
    assert f"{ad.WAFFLE_FILE} not written" in capsys.readouterr().err
