"""The treemap render: the survivor-density hatch overlay, the SVG legend,
a11y title and tooltips, and the size-by tree build.

Split from test_complexity_treemap.py (#720); the stubbed loader lives there.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from test_complexity_treemap import stubbed_treemap


@pytest.fixture(scope="module")
def treemap():
    with stubbed_treemap() as mod:
        yield mod


@pytest.fixture(scope="module")
def render_lib(treemap):
    """The shared treemap_render module. Depends on `treemap` so the scripts
    dir is on sys.path and numpy is stubbed before import."""
    import importlib

    return importlib.import_module("lib.complexity.treemap_render")


# ── survivor-density overlay (task 5) ─────────────────────────────────────────


@pytest.mark.parametrize("density,expected", [
    (None, ""),       # unknown -> no overlay
    (0.0, ""),
    (0.30, ""),       # boundary: must exceed, not equal
    (0.31, "diag"),
    (0.50, "diag"),   # boundary: cross only above 0.5
    (0.51, "cross"),
    (0.95, "cross"),
])
def test_hatch_for_density_thresholds(treemap, density, expected):
    assert treemap._hatch_for_density(density) == expected


def test_survivor_overrides_applies_hatch_per_file(treemap):
    p1, p2, p3 = Path("/repo/a.py"), Path("/repo/b.py"), Path("/repo/c.py")
    files = [(p1, 100, 5.0, "lizard"),
             (p2, 50, 3.0, "lizard"),
             (p3, 10, 1.0, "lizard")]
    density = {p1: 0.6, p2: 0.4, p3: 0.1}
    overrides = treemap._survivor_overrides(files, density)
    assert overrides[p1] == {"hatch": "cross"}
    assert overrides[p2] == {"hatch": "diag"}
    assert p3 not in overrides  # below threshold -> no overlay


def test_survivor_overrides_empty_data_is_silent(treemap):
    """Absent or empty survivor data renders no overlay, no error."""
    files = [(Path("/repo/a.py"), 100, 5.0, "lizard")]
    assert treemap._survivor_overrides(files, None) == {}
    assert treemap._survivor_overrides(files, {}) == {}


def test_write_svg_emits_hatch_overlay_and_legend(render_lib, tmp_path):
    """A hatched node gets a pattern-filled overlay, the <defs> patterns are
    emitted, and the legend explains what the hatch means."""
    node = render_lib.Node(name="a.py", rel_path="a.py", loc=100,
                           metric=5.0, color=(0.8, 0.2, 0.1, 1.0),
                           is_file=True, hatch="diag")
    rects = [(0.0, 0.0, 100.0, 100.0, node)]
    out = tmp_path / "hatched.svg"
    render_lib.write_svg(rects, Path("/repo"), 1600.0, 1000.0, out,
                         False, "ccn", show_survivor_legend=True)
    svg = out.read_text(encoding="utf-8")
    # pattern definition + overlay reference
    assert 'id="survivor-diag"' in svg
    assert 'fill="url(#survivor-diag)"' in svg
    # legend explains the survivor meaning with both thresholds
    assert "survivor density" in svg.lower()
    assert "30%" in svg
    assert "50%" in svg
    # canvas extended by the legend band (1000 + 84)
    assert 'height="1084"' in svg


def test_write_svg_emits_a11y_title_and_desc(render_lib, tmp_path):
    """Task 17: the root <svg> is role="img" with a <title>/<desc> pair as its
    first children, so a screen reader announces the image and how it encodes."""
    node = render_lib.Node(name="a.py", rel_path="a.py", loc=100,
                           metric=5.0, color=(0.8, 0.2, 0.1, 1.0),
                           is_file=True)
    rects = [(0.0, 0.0, 100.0, 100.0, node)]
    out = tmp_path / "a11y.svg"
    render_lib.write_svg(rects, Path("/repo"), 1600.0, 1000.0, out, False, "ccn")
    svg = out.read_text(encoding="utf-8")
    assert 'role="img"' in svg
    assert "<title>Complexity Hotspot Heatmap</title>" in svg
    assert "hue indicates cyclomatic complexity" in svg
    assert "saturation indicates git churn" in svg
    # <title>/<desc> are the root's first children (before the <style> block).
    assert svg.index("<title>") < svg.index("<style>")
    assert svg.index("<desc>") < svg.index("<style>")


def test_write_svg_no_overlay_without_survivor_data(render_lib, tmp_path):
    """No hatch and no legend flag -> original full-canvas treemap, untouched:
    no survivor patterns, no <defs>, no extra legend band."""
    node = render_lib.Node(name="a.py", rel_path="a.py", loc=100,
                           metric=5.0, color=(0.8, 0.2, 0.1, 1.0),
                           is_file=True)
    rects = [(0.0, 0.0, 100.0, 100.0, node)]
    out = tmp_path / "plain.svg"
    render_lib.write_svg(rects, Path("/repo"), 1600.0, 1000.0, out,
                         False, "ccn")
    svg = out.read_text(encoding="utf-8")
    assert "survivor-" not in svg
    assert "<defs>" not in svg
    assert 'height="1000"' in svg


def test_load_survivor_density_from_per_file(treemap, tmp_path):
    """Per-file density is survived/total; entries without a total (mutmut)
    are skipped, and paths resolve against the repo root."""
    ctx = {"test_pressure": {"per_file": [
        {"file": "src/a.py", "killed": 2, "survived": 8, "total": 10},
        {"file": "src/b.py", "killed": 9, "survived": 1, "total": 10},
        {"file": "src/c.py", "killed": None, "survived": 4, "total": None},
    ]}}
    j = tmp_path / "run-context.json"
    j.write_text(json.dumps(ctx), encoding="utf-8")
    density = treemap.load_survivor_density(j, tmp_path)
    assert density[(tmp_path / "src/a.py").resolve()] == 0.8
    assert density[(tmp_path / "src/b.py").resolve()] == 0.1
    assert (tmp_path / "src/c.py").resolve() not in density  # no total


def test_mutmut_junitxml_drives_hatch_overlay(treemap, tmp_path, fixtures_dir):
    """End to end: a real mutmut junitxml parse -> run-context per_file ->
    load_survivor_density -> hatch overrides. Before the junitxml fix the mutmut
    path carried no totals, so this overlay could never render on a Python repo."""
    from lib.test_pressure import _parse_mutmut_junitxml

    per_file = _parse_mutmut_junitxml(fixtures_dir / "mutmut-junitxml.xml")
    ctx = {"test_pressure": {"per_file": per_file}}
    j = tmp_path / "run-context.json"
    j.write_text(json.dumps(ctx), encoding="utf-8")

    density = treemap.load_survivor_density(j, tmp_path)
    calc = (tmp_path / "src/calc.py").resolve()
    util = (tmp_path / "src/util.py").resolve()
    assert density[calc] == 2 / 3      # real density, not skipped
    assert density[util] == 0.0         # all killed -> real 0 density (has a total)

    files = [(calc, 100, 5.0, "lizard"), (util, 50, 3.0, "lizard")]
    overrides = treemap._survivor_overrides(files, density)
    assert overrides[calc] == {"hatch": "cross"}  # 0.67 > 0.5
    assert util not in overrides                   # 0.0 below hatch threshold


def test_load_survivor_density_absent_block_is_empty(treemap, tmp_path):
    j = tmp_path / "run-context.json"
    j.write_text(json.dumps({"doc_graph": {}}), encoding="utf-8")
    assert treemap.load_survivor_density(j, tmp_path) == {}


def test_load_survivor_density_missing_file_is_empty(treemap, tmp_path):
    assert treemap.load_survivor_density(tmp_path / "nope.json", tmp_path) == {}


def test_build_tree_size_by_decouples_area_from_loc(render_lib):
    """size_by overrides the block area (estimated tokens) while each leaf keeps
    its real loc and records est_tokens for the tooltip."""
    root = Path("/repo")
    a = root / "a.py"
    files_colored = [(a, 500, 5.0, "lizard", (0.8, 0.2, 0.1, 1.0))]
    tree = render_lib.build_tree(files_colored, root, size_by={a: 1800})
    leaf = tree.children[0]
    assert leaf.size == 1800        # layout area = estimated tokens
    assert leaf.loc == 500          # real loc preserved
    assert leaf.est_tokens == 1800


def test_build_tree_without_size_by_is_unchanged(render_lib):
    """No size_by (the docs-heatmap path) keeps area == loc, est_tokens 0."""
    root = Path("/repo")
    a = root / "a.py"
    files_colored = [(a, 500, 5.0, "lizard", (0.8, 0.2, 0.1, 1.0))]
    leaf = render_lib.build_tree(files_colored, root).children[0]
    assert leaf.size == 500 and leaf.loc == 500 and leaf.est_tokens == 0


def test_write_svg_tooltip_shows_est_tokens_and_loc(render_lib, tmp_path):
    """A code-heatmap node (est_tokens set) leads its tooltip with estimated
    tokens and keeps LOC alongside."""
    node = render_lib.Node(name="a.py", rel_path="a.py", loc=514,
                           est_tokens=8200, metric=12.0,
                           color=(0.8, 0.2, 0.1, 1.0), is_file=True)
    rects = [(0.0, 0.0, 100.0, 100.0, node)]
    out = tmp_path / "tok.svg"
    render_lib.write_svg(rects, Path("/repo"), 1600.0, 1000.0, out, False, "ccn")
    svg = out.read_text(encoding="utf-8")
    assert "8,200 est. tokens" in svg
    assert "514 loc" in svg
