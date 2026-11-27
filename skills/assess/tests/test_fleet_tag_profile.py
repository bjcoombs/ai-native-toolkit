"""The fleet median tag profile strip and ``fleet-data.json`` ``tag_profile`` (#940).

The goldens under ``tests/fixtures/fleet_diagrams/`` lock the whole render; these tests hold
each honesty rule of #940 on its own. Regenerate with
``uv run --locked python tests/fleet_tag_profile_fixture.py`` from ``skills/assess``.
"""
from __future__ import annotations

import json
import re
from typing import Any

import pytest

import lib.fleet.fleet_data as fd
import lib.fleet.fleet_tag_profile as tp
from fleet_data_fixture import ROWS, SALT, VARIANTS, build, model
from fleet_tag_profile_fixture import FIXTURES, TALLIES, goldens, rows
from lib.assess_diagrams_tags import tag_whys
from lib.class_sizes import percentile
from lib.fleet.fleet_diagrams import build_diagrams
from lib.fleet.fleet_rescore import rescore
from lib.scoring.level_result_types import TagCell
from lib.scoring.level_tally import default_model
from lib.scoring.level_tags import conditions_label
from lib.scoring.levels_fields import TRUTH_TAG_TITLES, TRUTH_TAGS
from lib.vocabulary import banned_hits
from svg_cascade import overridden_text_attributes


def _svg(profile: list[tp.FleetTagRow] | None = None) -> str:
    return tp.render(profile if profile is not None else rows(), tag_whys())


def _row(svg: str, tag: str) -> str:
    return svg.split(f'<g data-tag="{tag}"', 1)[1].split("</g>\n", 1)[0]


def _by_tag(profile: list[tp.FleetTagRow]) -> dict[str, tp.FleetTagRow]:
    return {r["tag"]: r for r in profile}


# --- goldens and determinism ----------------------------------------------------


@pytest.mark.parametrize("name", sorted(goldens()))
def test_render_matches_golden(name: str) -> None:
    assert goldens()[name] == (FIXTURES / name).read_text(encoding="utf-8")


def test_two_renders_are_byte_identical_and_the_cascade_is_clean() -> None:
    assert _svg() == _svg()
    assert overridden_text_attributes(_svg()) == []


# --- the data: one tally --------------------------------------------------------


@pytest.mark.parametrize("name", sorted(VARIANTS))
def test_the_profile_is_read_from_each_repository_tally(name: str) -> None:
    """No second tally: the profile's numbers follow from ``repos[].tags`` alone."""
    data = build(VARIANTS[name])
    profile = data["tag_profile"]
    assert profile is not None and [r["tag"] for r in profile] == list(TRUTH_TAGS)
    tallies = [r["tags"] for r in data["repos"] if r["tags"] is not None]
    for row in profile:
        shares = [c["met"] / c["measured"] for cells in tallies for c in cells
                  if c["tag"] == row["tag"] and c["measured"] > 0]
        assert row["repos"] == len(tallies) and row["measured"] == len(shares)
        if shares:
            assert row["median"] == round(percentile(shares, 50), tp.PLACES)
            assert row["p25"] == round(percentile(shares, 25), tp.PLACES)
            assert row["p75"] == round(percentile(shares, 75), tp.PLACES)
        else:
            assert row["median"] is row["p25"] is row["p75"] is None


def test_the_document_names_no_repository_in_the_profile() -> None:
    data = build(fd.Privacy(salt=SALT))
    text = json.dumps(data["tag_profile"])
    for row in ROWS:
        assert row["repo"] not in text
    assert all(set(r) == set(tp.FleetTagRow.__annotations__) for r in data["tag_profile"] or [])


def test_no_model_writes_no_profile() -> None:
    assert tp.tag_profile(None, TALLIES) is None


def test_a_rescore_rebuilds_the_profile_of_an_entry_written_before_it() -> None:
    current = build(fd.Privacy())
    old: Any = json.loads(fd.dumps_fleet_data(current))
    del old["tag_profile"]
    again = rescore(old, ROWS, model(), fd.Privacy().key)
    assert again is not None and again["tag_profile"] == current["tag_profile"]


def test_one_repository_collapses_the_whisker_to_the_median_dot() -> None:
    one = tp.tag_profile(default_model(), TALLIES[:1])
    assert one is not None
    behaviour = _by_tag(one)["behaviour"]
    assert behaviour["median"] == behaviour["p25"] == behaviour["p75"] == round(2 / 3, tp.PLACES)
    svg = _svg(one)
    assert 'data-mark="whisker"' not in svg and 'data-mark="dot"' in _row(svg, "behaviour")
    assert "median 67%" in _row(svg, "behaviour") and "middle half" not in _row(svg, "behaviour")


def test_several_repositories_draw_a_whisker_between_the_quartiles() -> None:
    svg = _svg()
    behaviour = _row(svg, "behaviour")
    assert 'data-mark="whisker"' in behaviour
    assert "median 67%, middle half 50% to 83%" in behaviour
    xs = re.findall(r'<line x1="([\d.]+)" y1="[\d.]+" x2="([\d.]+)"', behaviour.split('data-mark="whisker"')[1])
    lo, hi = float(xs[0][0]), float(xs[0][1])
    dot = float(re.search(r'<circle cx="([\d.]+)"', behaviour).group(1))  # type: ignore[union-attr]
    assert lo < dot < hi


# --- the honesty rules ----------------------------------------------------------


def test_row_order_is_fixed_and_labels_carry_conditions_and_repositories_measured() -> None:
    svg = _svg()
    assert re.findall(r'<g data-tag="([a-z_]+)"', svg) == list(TRUTH_TAGS)
    for row in rows():
        label = (f">{TRUTH_TAG_TITLES[row['tag']]} · {conditions_label(row['conditions'])} · "
                 f"{row['measured']} of {row['repos']} ")
        assert label in _row(svg, row["tag"])
    assert ">Behaviour · 7 conditions · 3 of 4 repositories<" in svg  # two #828 hazards
    assert ">Supply chain · 1 condition · 0 of 4 repositories<" in svg


def test_a_tag_no_repository_measured_is_dashed_with_its_reason_never_zero() -> None:
    svg = _svg()
    for tag, reason in (("governance", "not measured on any repository"),
                        ("supply_chain", "not counted: its conditions are listed, never counted"),
                        ("contracts", "not yet measured: no condition has this tag")):
        row = _row(svg, tag)
        assert 'data-mark="dashed"' in row and 'data-mark="dot"' not in row and reason in row
    assert _by_tag(rows())["governance"]["median"] is None


def test_a_real_zero_median_plots_at_zero() -> None:
    zero = tp.tag_profile(default_model(), [[_cell("structure", 0, 5)], [_cell("structure", 0, 2)]])
    assert zero is not None and _by_tag(zero)["structure"]["median"] == 0.0
    assert 'data-mark="dot"' in _row(_svg(zero), "structure")


def _cell(tag: str, met: int, measured: int) -> TagCell:
    return {"tag": tag, "met": met, "measured": measured, "verified": 0, "not_measured": 0,
            "hazards": 0, "declined": 0}


def test_marks_are_dots_and_lines_with_no_filled_area() -> None:
    svg = _svg()
    assert "<path" not in svg and "<polygon" not in svg and "<polyline" not in svg


def test_hover_text_is_each_tag_why() -> None:
    svg, whys = _svg(), tag_whys()
    for tag in TRUTH_TAGS:
        assert f"<title>{whys[tag]}</title>" in _row(svg, tag)


def test_text_passes_the_vocabulary_with_no_layer_number() -> None:
    text = "\n".join([_svg(), tp.in_place(rows()), tp.caption()])
    assert banned_hits(text) == []
    assert not re.search(r"\blayer\s*\d", text, re.IGNORECASE)
    assert tp.in_place(rows()).startswith("In place:")


# --- the fleet report -----------------------------------------------------------


def test_the_strip_leads_the_fleet_report_and_skips_with_a_reason() -> None:
    drawn = build_diagrams(default_model(), [], "6mo", tag_profile=rows())
    assert drawn[0]["file"] == tp.FILE and drawn[0]["svg"] is not None
    assert build_diagrams(None, [], "6mo", tag_profile=rows())[0]["skipped"] == (
        "skipped: the run had no maturity model (--levels), so no levels.")
    assert "wrote no per-tag tally" in (build_diagrams(default_model(), [], "6mo")[0]["skipped"] or "")
    empty = tp.tag_profile(default_model(), [None])
    assert "none was scanned" in (build_diagrams(default_model(), [], "6mo", tag_profile=empty)[0]["skipped"] or "")
