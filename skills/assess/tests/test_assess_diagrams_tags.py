"""The repository tag profile strip (``lib/assess_diagrams_tags.py``, #940).

The goldens under ``tests/fixtures/assess_diagrams/`` lock the whole render; these tests hold
each honesty rule of #940 on its own. Regenerate the goldens with
``uv run --locked python tests/assess_diagrams_tags_fixture.py`` from ``skills/assess``.
"""
from __future__ import annotations

import copy
import re
from typing import Any

import pytest

import lib.assess_diagrams_tags as td
from assess_diagrams_tags_fixture import FIXTURES, LEVEL, PRIOR, PRIOR_OTHER, TAGS, goldens
from lib.assess_diagrams_review import ALL_FILES, build_all
from lib.scoring.level_tally import default_model, level_result
from lib.scoring.level_tags import conditions_label
from lib.scoring.levels_fields import TRUTH_TAG_TITLES, TRUTH_TAGS
from lib.vocabulary import banned_hits
from svg_cascade import overridden_text_attributes


def _svg(ctx: dict[str, Any]) -> str:
    drawn = td.tag_diagrams(ctx)[0]["svg"]
    assert drawn is not None
    return drawn


def _row(svg: str, tag: str) -> str:
    return svg.split(f'<g data-tag="{tag}"', 1)[1].split("</g>", 1)[0]


SAME = {"level": LEVEL, "prior_level": PRIOR}
OTHER = {"level": LEVEL, "prior_level": PRIOR_OTHER}


# --- goldens and determinism ----------------------------------------------------


@pytest.mark.parametrize("name", sorted(goldens()))
def test_render_matches_golden(name: str) -> None:
    assert goldens()[name] == (FIXTURES / name).read_text(encoding="utf-8")


def test_two_renders_are_byte_identical() -> None:
    assert td.tag_diagrams(copy.deepcopy(SAME)) == td.tag_diagrams(copy.deepcopy(SAME))


@pytest.mark.parametrize("ctx", [SAME, OTHER])
def test_stylesheet_never_overrides_a_text_attribute(ctx: dict[str, Any]) -> None:
    assert overridden_text_attributes(_svg(ctx)) == []


def test_the_strip_follows_the_waffle_and_owns_its_file() -> None:
    files = [d["file"] for d in build_all(SAME)]
    # the relative order this depends on, not a position: the Step ladder (#1102) leads
    assert files[files.index("evidence-waffle.svg") + 1] == td.TAG_FILE
    assert td.TAG_FILE in ALL_FILES


# --- the honesty rules ----------------------------------------------------------


def test_row_order_is_fixed_and_every_label_carries_its_condition_count() -> None:
    svg = _svg(SAME)
    assert re.findall(r'<g data-tag="([a-z_]+)"', svg) == list(TRUTH_TAGS)
    for row in TAGS:
        assert f">{TRUTH_TAG_TITLES[row['tag']]} · {conditions_label(row['conditions'])}<" in _row(svg, row["tag"])
    assert ">Contracts · 0 conditions<" in svg and ">Supply chain · 2 conditions<" in svg
    # the order is the tag list's, not the rows' order in the data
    shuffled = {**LEVEL, "tags": list(reversed(TAGS))}
    assert td.tag_diagrams({"level": shuffled})[0]["svg"] is None


def test_a_tag_with_no_measured_condition_is_dashed_with_its_reason_never_at_zero() -> None:
    svg = _svg(SAME)
    docs = _row(svg, "docs")
    assert 'data-state="not_measured"' in docs and 'data-mark="dashed"' in docs
    assert 'data-mark="dot"' not in docs
    assert "not measured: needs GitHub or GitLab access" in docs  # the most frequent reason
    assert "waits on another condition" in _row(svg, "governance")
    assert "its conditions are listed, never counted" in _row(svg, "agent_runtime")
    assert "excluded by the owner or declined" in _row(svg, "supply_chain")


def test_the_dashed_reason_comes_from_the_countable_conditions_only() -> None:
    """Governance's two listed conditions share a reason that outvotes its one countable one."""
    gov = next(r for r in TAGS if r["tag"] == "governance")
    assert [m["reason"] for m in gov["not_measured"]].count("block_absent") == 2
    assert td.main_reason(gov) == "requires:layer-7-review.1"
    assert td.dashed_text(gov) == "not measured: waits on another condition"
    assert "scan did not run" not in _row(_svg(SAME), "governance")
    # listed conditions not measured never turn a set-aside row into a not-measured one
    supply = next(r for r in TAGS if r["tag"] == "supply_chain")
    assert supply["not_measured"] and td.row_state(supply) == "set_aside"


def test_a_real_zero_of_n_measured_plots_at_zero() -> None:
    structure = _row(_svg(SAME), "structure")
    assert 'data-state="share"' in structure and 'data-mark="dot"' in structure
    assert "0 of 2 met" in structure


def test_a_model_tag_with_no_condition_reads_not_yet_measured() -> None:
    contracts = _row(_svg(SAME), "contracts")
    assert 'data-state="none"' in contracts and "not yet measured" in contracts
    assert 'data-mark="dot"' not in contracts
    shipped = level_result(default_model(), {})
    assert "not yet measured" in _row(_svg({"level": shipped}), "contracts")


def test_marks_are_dots_on_one_linear_scale_with_no_filled_area() -> None:
    svg = _svg(SAME)
    assert "<path" not in svg and "<polygon" not in svg and "<polyline" not in svg
    xs = {tag: float(re.search(r'<circle cx="([\d.]+)"[^>]*data-mark="dot"', _row(svg, tag)).group(1))  # type: ignore[union-attr]
          for tag in ("behaviour", "structure", "agent_context")}
    zero, full = xs["structure"], xs["agent_context"]
    assert xs["behaviour"] == pytest.approx(zero + (full - zero) * 2 / 3, abs=0.1)


def test_hover_text_is_each_tag_why() -> None:
    svg = _svg(SAME)
    whys = td.tag_whys()
    for tag in TRUTH_TAGS:
        assert f"<title>{whys[tag]}</title>" in _row(svg, tag)
        assert whys[tag] != TRUTH_TAG_TITLES[tag]  # every shipped tag has a why (#939)


def test_an_earlier_run_under_the_same_model_draws_an_outline_dot() -> None:
    svg = _svg(SAME)
    assert 'data-mark="prior"' in _row(svg, "behaviour")
    assert 'data-mark="prior"' not in _row(svg, "agent_context")  # not measured earlier: no outline
    assert "Hollow dots: the earlier run, under the same maturity model fixture-2." in svg


def test_an_earlier_run_under_another_model_draws_no_outline_and_says_why_in_one_line() -> None:
    svg = _svg(OTHER)
    assert 'data-mark="prior"' not in svg
    note = td.prior_note(PRIOR_OTHER, LEVEL)
    assert note == ("Earlier dots not drawn: the earlier run used maturity model fixture-1 and this "
                    "run fixture-2, so the two do not compare.")
    assert "\n" not in note and "the earlier run used maturity model fixture-1" in svg
    assert note in td.tag_diagrams(OTHER)[0]["caption"]


def test_a_same_model_earlier_run_that_measured_nothing_says_so() -> None:
    blank = {**PRIOR, "tags": [{**row, "met": 0, "measured": 0} for row in PRIOR["tags"]]}
    drawn = td.tag_diagrams({"level": LEVEL, "prior_level": blank})[0]
    assert drawn["svg"] is not None and 'data-mark="prior"' not in drawn["svg"]
    assert "measured no tag" in drawn["caption"] and "Hollow dots" not in drawn["svg"]


def _exclude(*tags: str, grades: tuple[str, ...] = ("measured", "verified")) -> dict[str, str]:
    """``row_state`` of each tag when the owner excludes every countable condition with it."""
    from lib.scoring.level_tags import truth_map
    model = default_model()
    graded = level_result(model, {})["conditions"]
    truth = truth_map(model)
    ids = [c["id"] for c in graded if c["grade"] in grades and set(truth[c["id"]]) & set(tags)]
    result = level_result(model, {}, [{"id": cid, "kind": "other", "source": "repo"} for cid in ids])
    return {r["tag"]: td.row_state(r) for r in result["tags"]}


def test_an_owner_exclusion_reads_set_aside_never_listed() -> None:
    """Through the engine: an excluded countable condition is set aside, not listed."""
    states = _exclude("governance", "behaviour")
    assert states["governance"] == "set_aside" and states["behaviour"] == "set_aside"
    assert "excluded by the owner" in td.dashed_text(
        next(r for r in level_result(default_model(), {}, [
            {"id": c["id"], "kind": "other", "source": "repo"}
            for c in level_result(default_model(), {})["conditions"]])["tags"] if r["tag"] == "behaviour"))
    # with nothing excluded, a tag of declared and config-only conditions alone reads listed
    plain = {r["tag"]: td.row_state(r) for r in level_result(default_model(), {})["tags"]}
    assert plain["supply_chain"] == "listed" and plain["behaviour"] == "not_measured"  # agent_runtime counts since #849


def test_countable_grades_are_the_tally_counted_grades() -> None:
    from lib.scoring.level_tags import COUNTABLE_GRADES
    from lib.scoring.level_tally import COUNTED_GRADES
    assert COUNTABLE_GRADES == COUNTED_GRADES


def test_no_earlier_file_draws_no_outline() -> None:
    drawn = td.tag_diagrams({"level": LEVEL, "prior_level": None})[0]
    assert drawn["svg"] is not None and 'data-mark="prior"' not in drawn["svg"]
    assert "No earlier run is recorded here" in drawn["caption"]


@pytest.mark.parametrize("prior", [{"available": False, "reason": "unreadable"}, {"prior_level_version": 99}, "junk"])
def test_an_unreadable_earlier_record_draws_no_outline_and_says_so(prior: Any) -> None:
    drawn = td.tag_diagrams({"level": LEVEL, "prior_level": prior})[0]
    assert drawn["svg"] is not None and 'data-mark="prior"' not in drawn["svg"]
    assert "The earlier run's record could not be read, so no earlier dots are drawn." in drawn["caption"]
    assert "No earlier run is recorded here" not in drawn["caption"]


def test_an_earlier_dot_on_a_dashed_row_is_kept_and_keyed() -> None:
    svg = _svg(SAME)
    docs = _row(svg, "docs")
    assert 'data-mark="dashed"' in docs and 'data-mark="prior"' in docs and 'data-mark="dot"' not in docs
    key = "A hollow dot on a dashed row is the earlier run&#x27;s share; this run did not measure it"
    assert key in svg or key.replace("&#x27;", "'") in svg
    # no earlier dot on a dashed row: no such key line
    only_shares = {**PRIOR, "tags": [{**r, "met": 0, "measured": 0, "not_measured": []}
                                     if r["tag"] in ("docs", "agent_context") else r for r in PRIOR["tags"]]}
    assert "on a dashed row" not in _svg({"level": LEVEL, "prior_level": only_shares})


def test_the_strip_names_no_path_repository_or_person() -> None:
    svg = _svg(SAME)
    for value in ("doc.1", "st.3", "gov.2", "layer-4-architecture", "layer-7-review"):
        assert value not in svg


def test_captions_and_labels_pass_the_vocabulary_with_no_layer_number() -> None:
    for ctx in (SAME, OTHER, {"level": level_result(default_model(), {})}):
        drawn = td.tag_diagrams(ctx)[0]
        text = "\n".join([drawn["in_place"], drawn["caption"], drawn["svg"] or ""])
        assert banned_hits(text) == []
        assert not re.search(r"\blayer\s*\d", text, re.IGNORECASE)
        assert drawn["in_place"].startswith("In place:")


@pytest.mark.parametrize(("level", "reason"), [
    (None, "the run has no level result"),
    ({"conditions": []}, "the level result has no per-tag rows"),
    ({"conditions": [], "tags": [{"tag": "docs"}]}, "the level result has no per-tag rows"),
])
def test_not_drawn_says_why(level: Any, reason: str) -> None:
    drawn = td.tag_diagrams({"level": level})[0]
    assert drawn["svg"] is None and drawn["skipped"] is not None and drawn["skipped"].startswith(reason)


def test_a_renderer_error_becomes_a_skip_line(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_: Any) -> str:
        raise RuntimeError("x")
    monkeypatch.setattr(td, "render_strip", boom)
    assert td.tag_diagrams(SAME)[0]["skipped"] == "the renderer raised RuntimeError."


def test_the_strip_reads_the_runs_own_model_record() -> None:
    """#1184: a profile run draws its earlier dots from its own record under ``models``."""
    from lib.assess_diagrams_tags import earlier_shares, prior_note

    row = {"tag": "behaviour", "conditions": 1, "countable": 1, "set_aside": 0, "met": 1, "measured": 1,
           "not_measured": [], "countable_not_measured": []}
    citizen = {"levels_version": "profile-citizen-2026.11.23"}
    default_only = {"prior_level_version": 3, "levels_version": "default-2026.11.10", "tags": [row]}
    assert earlier_shares(default_only, citizen) == {}
    assert prior_note(default_only, citizen).startswith("No earlier run under this maturity model")
    with_citizen = {**default_only, "models": {"citizen": {**citizen, "tags": [row]}}}
    assert earlier_shares(with_citizen, citizen) == {"behaviour": (1, 1)}
    assert earlier_shares(with_citizen, {"levels_version": "default-2026.11.10"}) == {"behaviour": (1, 1)}
    profile_only = {"prior_level_version": 3, "models": {"citizen": {**citizen, "tags": [row]}}}
    custom = {"levels_version": "team-1"}
    assert earlier_shares(profile_only, custom) == {}
    assert prior_note(profile_only, custom).startswith("No earlier run under this maturity model")
