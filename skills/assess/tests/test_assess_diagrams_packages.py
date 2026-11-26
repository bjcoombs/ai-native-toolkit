"""Tests for the package map (issue #942): lib/assess_diagrams_packages.py.

The goldens under ``tests/fixtures/assess_diagrams/`` lock the render; the tests below read
the cells and marks back out of the SVG and hold them to the ``structure`` block. Regenerate
the goldens with ``uv run --locked python tests/assess_diagrams_packages_fixture.py`` from
``skills/assess``.
"""
from __future__ import annotations

import json
import re
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import pytest

from assess_diagrams_packages_fixture import FIXTURES, LARGE, SMALL, goldens
from lib import assess_diagrams_packages as pm
import lib.fleet.fleet_svg as svg
from lib.assess_diagrams import diagrams_section
from lib.counts_only.content_free import HASH_PREFIX, counts_only
from lib.graph.structure_graph import analyze_structure
from lib.vocabulary import IN_PLACE, banned_hits
from svg_cascade import overridden_text_attributes

SVG_NS = "{http://www.w3.org/2000/svg}"
_TEXT = re.compile(r"<(text|title|desc)\b[^>]*>(.*?)</\1>", re.S)
GOLDEN_REPO = Path(__file__).resolve().parent / "fixtures" / "structure_golden" / "repo"


def _rects(content: str, attr: str) -> list[ET.Element]:
    root = ET.fromstring(content.encode("utf-8"))
    return [r for r in root.iter(f"{SVG_NS}rect") if attr in r.attrib]


def _marks(content: str) -> list[ET.Element]:
    root = ET.fromstring(content.encode("utf-8"))
    return [e for e in root.iter() if e.attrib.get("data-mark") == "bypass"]


def _cells(content: str, kind: str) -> list[ET.Element]:
    return [r for r in _rects(content, "data-cell") if r.attrib["data-cell"] == kind]


def _prose(content: str) -> str:
    return "\n".join(re.sub(r"<[^>]+>", " ", body) for _, body in _TEXT.findall(content))


# --- goldens and determinism ----------------------------------------------------


@pytest.mark.parametrize("name", sorted(goldens()))
def test_render_matches_golden(name: str) -> None:
    assert goldens()[name] == (FIXTURES / name).read_text(encoding="utf-8")


@pytest.mark.parametrize("block", [SMALL, LARGE], ids=["small", "large"])
def test_two_renders_are_byte_identical_and_the_stylesheet_is_safe(block: dict[str, Any]) -> None:
    first = pm.render_package_map(json.loads(json.dumps(block)))
    assert first == pm.render_package_map(json.loads(json.dumps(block)))
    assert overridden_text_attributes(first) == []


# --- the cells equal the block --------------------------------------------------


@pytest.mark.parametrize("block", [SMALL, LARGE], ids=["small", "large"])
def test_shaded_cells_and_bypass_marks_equal_the_recorded_pairs(block: dict[str, Any]) -> None:
    content = pm.render_package_map(block)
    view = pm.package_view(block)
    expected = {(s, t): v for s, targets in view["cells"].items() for t, v in targets.items()}
    shaded = _cells(content, "imports")
    assert len(shaded) == len(expected)
    assert sum(int(c.attrib["data-imports"]) for c in shaded) == view["drawn_imports"]
    bypassing = [c for c in shaded if int(c.attrib["data-bypass"]) > 0]
    assert len(_marks(content)) == len(bypassing) == sum(1 for v in expected.values() if v[1])
    assert sum(int(c.attrib["data-bypass"]) for c in shaded) == view["drawn_bypass"]


@pytest.mark.parametrize("block", [SMALL, LARGE], ids=["small", "large"])
def test_drawn_plus_stated_counts_equal_the_block_totals(block: dict[str, Any]) -> None:
    view = pm.package_view(block)
    assert view["drawn_imports"] + view["past_top"] + view["unrecorded"] == block["cross_package_edges"]
    recorded_bypass = sum(e["bypass"] for e in block["package_edges"])
    outside = recorded_bypass - view["drawn_bypass"] + (block["burrow_edge_count"] - recorded_bypass)
    assert view["drawn_bypass"] + outside == view["burrows"] == block["burrow_edge_count"]
    content = pm.render_package_map(block)
    assert f"Front-door bypasses: {view['drawn_bypass']} of {view['burrows']}" in content


def test_twenty_of_many_packages_are_drawn_and_the_rest_is_stated() -> None:
    content = pm.render_package_map(LARGE)
    assert len(_rects(content, "data-loc")) == pm.TOP == 20
    assert "the 20 packages with the most lines of code, of 230." in content
    assert "30 imports to or from packages past the top 20 are not drawn." in content
    assert "177 imports between packages the run does not" in content


def test_with_the_pair_cap_an_unrecorded_cell_is_unknown_never_empty() -> None:
    large = pm.render_package_map(LARGE)
    assert _cells(large, "unknown") and not _cells(large, "none")
    assert all(c.attrib["fill"] == "url(#assess-package-map-fleet-hatch)" for c in _cells(large, "unknown"))
    assert "busiest-pairs limit applied" in large and "No imports" not in large
    small = pm.render_package_map(SMALL)
    assert _cells(small, "none") and not _cells(small, "unknown")
    assert "busiest-pairs limit" not in small


def test_shade_steps_never_use_the_palest_step_and_stay_integer() -> None:
    assert [pm.shade_step(n) for n in (1, 2, 3, 4, 7, 8, 63, 64, 10**6)] == [2, 3, 3, 4, 4, 5, 7, 8, 8]
    assert [pm._bucket(s) for s in range(2, 9)] == [
        "1", "2 to 3", "4 to 7", "8 to 15", "16 to 31", "32 to 63", "64 or more"]


def test_a_cut_label_carries_the_full_package_id() -> None:
    content = pm.render_package_map(SMALL)
    assert "<title>services/billing/internal/ledger&lt;&amp;&gt;core</title>" in content


def test_the_golden_repo_graph_draws_and_matches_its_block() -> None:
    block = analyze_structure(GOLDEN_REPO).as_dict()
    [diagram] = pm.package_diagrams({"structure": block})
    assert diagram["svg"] is not None
    view = pm.package_view(dict(block))
    assert view["drawn_imports"] + view["past_top"] + view["unrecorded"] == block["cross_package_edges"]
    assert len(_rects(diagram["svg"], "data-loc")) == min(pm.TOP, block["package_count"])


# --- not drawn ------------------------------------------------------------------


@pytest.mark.parametrize("ctx, reason", [
    ({}, "the run has no import graph data."),
    ({"structure": {"available": False, "reason": "no_import_graph_rust: no builder"}},
     "not measured here: "),
    ({"structure": {"available": True, "cross_package_edges": 3}}, "has no package list."),
    ({"structure": {**SMALL, "packages": SMALL["packages"][:1]}}, "the import graph has one package"),
    ({"structure": {**SMALL, "cross_package_edges": 0}}, "no import crosses a package boundary."),
    ({"structure": {**SMALL, "packages": [{"id": "a"}, {"id": "b", "loc": "x"}]}},
     "the renderer raised ValueError."),
])
def test_without_a_graph_nothing_is_drawn_and_the_reason_is_said(ctx: dict[str, Any], reason: str) -> None:
    [diagram] = pm.package_diagrams(ctx)
    assert diagram["svg"] is None and reason in (diagram["skipped"] or "")
    section = diagrams_section([diagram])
    assert f"Not drawn: {diagram['skipped']}" in section and "](./" not in section
    assert section.splitlines()[2].startswith(IN_PLACE)
    assert not banned_hits(section)


# --- words and privacy ----------------------------------------------------------


def test_text_names_no_layer_number_and_no_banned_word() -> None:
    texts = {name: (_prose(c) if name.endswith(".svg") else c) for name, c in goldens().items()}
    for name, content in texts.items():
        assert not re.search(r"\blayer[- ]\d", content, re.I), name
        assert not banned_hits(content), (name, banned_hits(content))


def test_hashed_labels_join_and_the_matrix_is_unchanged() -> None:
    hashed = counts_only({"structure": SMALL}, hash_paths=True, salt="fixture-salt")["structure"]
    plain, counted = pm.render_package_map(SMALL), pm.render_package_map(hashed)
    assert [c.attrib["data-imports"] for c in _cells(counted, "imports")] == \
        [c.attrib["data-imports"] for c in _cells(plain, "imports")]
    assert len(_marks(counted)) == len(_marks(plain))
    assert HASH_PREFIX in counted
    for package in SMALL["packages"]:
        for part in package["id"].split("/"):
            assert not re.search(rf"\b{re.escape(part)}\b", _prose(counted)), part


def test_a_counts_only_run_writes_a_hashed_package_map(tmp_path: Path) -> None:
    import assess_core
    repo = tmp_path / "repo"
    shutil.copytree(GOLDEN_REPO, repo)
    assert assess_core.main([str(repo), "--non-interactive", "--counts-only", "--hash-salt", "s"]) == 0
    full = (repo / ".assess" / pm.PACKAGE_FILE).read_text(encoding="utf-8")
    hashed = (repo / ".assess" / pm.PACKAGE_COUNTS_ONLY_FILE).read_text(encoding="utf-8")
    block = json.loads((repo / ".assess" / "run-context.json").read_text(encoding="utf-8"))["structure"]
    names = [p["id"] for p in block["packages"]]
    assert names and all(name in full for name in names)
    assert not any(re.search(rf"\b{re.escape(name)}\b", _prose(hashed)) for name in names)
    assert len(_cells(hashed, "imports")) == len(_cells(full, "imports"))


def _luminance(colour: str) -> float:
    channels = [int(colour[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def _contrast(a: str, b: str) -> float:
    high, low = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def test_the_bypass_mark_is_themed_and_reads_on_every_ramp_step_in_both_schemes() -> None:
    """The ramp fills are fixed colours, so the mark is drawn in the themed foreground with a
    themed background edge: on every step one of the two clears the 3:1 non-text minimum."""
    marks = _marks(pm.render_package_map(SMALL))
    assert marks and all(m.tag == f"{SVG_NS}polygon" and m.attrib["class"] == "f-fg s-bg" for m in marks)
    assert all("fill" not in m.attrib for m in marks)
    for scheme in (0, 1):
        fg, bg = svg.THEME["fg"][scheme], svg.THEME["bg"][scheme]
        for step in range(2, svg.TOP_STEP + 1):
            fill = svg.LEVEL_RAMP[step]
            assert max(_contrast(fg, fill), _contrast(bg, fill)) >= 3.0, (scheme, step)
