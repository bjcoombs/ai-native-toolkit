"""The estate-wide fleet tag heatmap (``lib/fleet/fleet_tag_heatmap.py``, #940).

The goldens under ``tests/fixtures/fleet_diagrams/`` lock the whole render; these tests hold
each honesty rule of #940 on its own. Regenerate with
``uv run --locked python tests/fleet_tag_heatmap_fixture.py`` from ``skills/assess``.
"""
from __future__ import annotations

import copy
import re
from typing import Any

import pytest

import lib.fleet.fleet_data as fd
import lib.fleet.fleet_tag_heatmap as th
from fleet_data_fixture import ROWS, SALT, build, model
from fleet_tag_heatmap_fixture import COUNTS_ONLY, FIXTURES, FULL, HASHED, NAMES, goldens
from lib.fleet.fleet_diagrams import build_diagrams
from lib.fleet.fleet_tag_profile import FILE as PROFILE_FILE
from lib.levels_fields import TRUTH_TAGS
from lib.vocabulary import banned_hits
from svg_cascade import overridden_text_attributes


def _svg(data: dict[str, Any]) -> str:
    return th.render(th.heatmap_rows(data), th.uncarried(data))


def _row(svg: str, label: str) -> str:
    return svg.split(f'<g data-row="{label}"', 1)[1].split("</g>", 1)[0]


@pytest.mark.parametrize("name", sorted(goldens()))
def test_render_matches_golden(name: str) -> None:
    assert goldens()[name] == (FIXTURES / name).read_text(encoding="utf-8")


def test_two_renders_are_byte_identical_and_the_cascade_is_clean() -> None:
    assert _svg(copy.deepcopy(FULL)) == _svg(copy.deepcopy(FULL))
    assert overridden_text_attributes(_svg(FULL)) == []


def test_rows_are_sorted_by_step_highest_first_then_key_and_every_repository_is_there() -> None:
    rows = th.heatmap_rows(FULL)
    assert [r["label"] for r in rows] == ["acme/web-frontend", "acme/data-pipeline", "acme/payments-api",
                                          "acme/infra-terraform", "acme/legacy-billing"]
    assert [r["step"] for r in rows] == [3, 2, 2, None, None]


def test_columns_are_the_fixed_tag_order() -> None:
    svg = _svg(FULL)
    first = _row(svg, "acme/web-frontend")
    assert re.findall(r'data-tag="([a-z_]+)"', first) == list(TRUTH_TAGS)


def test_shares_come_from_each_repository_tally() -> None:
    """No second tally: a cell is the repository's cell ``met`` over ``measured``."""
    for row, repo in zip(th.heatmap_rows(FULL), sorted(FULL["repos"], key=th._step_key)):
        cells = {c["tag"]: c for c in repo["tags"] or []}
        for tag, share in zip(TRUTH_TAGS, row["shares"]):
            cell = cells.get(tag)
            expected = cell["met"] / cell["measured"] if cell and cell["measured"] else None
            assert share == expected


def test_not_measured_is_hatched_never_shaded_and_a_real_zero_is_shaded() -> None:
    svg = _svg(FULL)
    pipeline = _row(svg, "acme/data-pipeline")
    structure = re.search(r'<rect[^>]*data-tag="structure"[^>]*>', pipeline).group(0)  # type: ignore[union-attr]
    assert 'data-cell="share"' in structure and "#ffffe5" in structure  # 0 of 5: the palest step, shaded
    docs = re.search(r'<rect[^>]*data-tag="docs"[^>]*>', pipeline).group(0)  # type: ignore[union-attr]
    assert 'data-cell="not_measured"' in docs and "url(#fleet-hatch)" in docs
    failed = _row(svg, "acme/legacy-billing")
    assert failed.count('data-cell="not_measured"') == len(TRUTH_TAGS) and "%" not in failed.split("</text>", 2)[2]


def test_a_tag_no_condition_carries_says_so_in_its_header() -> None:
    assert "none yet" in _svg(FULL)
    assert "none yet" not in _svg({**FULL, "tag_profile": []})


def test_names_appear_only_with_full_and_hashed_ids_under_hash_salt() -> None:
    full, counts_only, hashed = _svg(FULL), _svg(COUNTS_ONLY), _svg(HASHED)
    assert all(name in full for name in NAMES)
    for svg in (counts_only, hashed):
        assert not any(name in svg for name in NAMES)
        assert "acme" not in svg
    assert re.findall(r'<g data-row="(repository \d+)"', counts_only) == [f"repository {i}" for i in range(1, 6)]
    assert all(r["key"] in hashed for r in HASHED["repos"])


@pytest.mark.parametrize("privacy", [fd.Privacy(), fd.Privacy(salt=SALT)])
def test_a_real_fleet_data_document_names_no_repository_or_unit(privacy: fd.Privacy) -> None:
    data = build(privacy)
    svg = _svg(dict(data))
    for row in ROWS:
        assert row["repo"] not in svg
    for view in data["views"]:
        for unit in view["units"]:
            assert unit["key"] not in svg
    assert 'data-unit' not in svg


def test_marks_are_cells_only_no_filled_area_and_text_passes_the_vocabulary() -> None:
    svg = _svg(FULL)
    assert "<path" not in svg and "<polygon" not in svg
    text = "\n".join([svg, th.in_place(th.heatmap_rows(FULL)), th.caption(), th.QUESTION])
    assert banned_hits(text) == []
    assert not re.search(r"\blayer\s*\d", text, re.IGNORECASE)


def test_the_heatmap_follows_the_strip_in_the_fleet_report_and_skips_with_a_reason() -> None:
    data = dict(build(fd.Privacy()))
    drawn = build_diagrams(model(), [], "6mo", data=data)
    assert [d["file"] for d in drawn[:2]] == [PROFILE_FILE, th.FILE]
    assert drawn[1]["svg"] is not None
    assert "no maturity model" in (build_diagrams(None, [], "6mo", data=data)[1]["skipped"] or "")
    assert "no fleet-data.json" in (build_diagrams(model(), [], "6mo")[1]["skipped"] or "")
    none = {**data, "repos": [{**r, "tags": None} for r in data["repos"]]}
    assert "none was scanned" in (build_diagrams(model(), [], "6mo", data=none)[1]["skipped"] or "")
