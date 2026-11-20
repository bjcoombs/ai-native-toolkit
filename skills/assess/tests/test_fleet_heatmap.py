"""Tests for the fleet leadership heat map (issue #570): lib/fleet_view.py,
lib/fleet_heatmap.py and skills/fleet/scripts/heatmap.py.

The golden SVGs under ``tests/fixtures/fleet_heatmap/`` lock the whole render;
the semantic tests check what a byte comparison cannot explain on failure.
Regenerating the goldens: ``tests/fixtures/fleet_heatmap/README.md``.
"""
from __future__ import annotations

import importlib.util
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from types import ModuleType

import jsonschema
import pytest

from lib import fleet_heatmap as fh
from lib import fleet_view as fv
from lib.fleet import FLEET_ROW_VERSION
from lib.fleet_group_schema import build_schema
from lib.fleet_groups import GroupRow
from lib.rubric import load_rubric
from fleet_heatmap_fixture import build
from svg_cascade import overridden_text_attributes

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "fleet_heatmap"
HEATMAP_SCRIPT = Path(__file__).resolve().parents[2] / "fleet" / "scripts" / "heatmap.py"
SVG_NS = "{http://www.w3.org/2000/svg}"


def _load_cli() -> ModuleType:
    spec = importlib.util.spec_from_file_location("fleet_heatmap_cli", HEATMAP_SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


cli = _load_cli()


def _render(tmp_path: Path, *extra: str) -> str:
    out = tmp_path / "out.svg"
    assert cli.main([str(FIXTURES), "--rubric", str(FIXTURES / "rubric.toml"),
                     "--output", str(out), *extra]) == 0
    return out.read_text(encoding="utf-8")


def _groups() -> list[GroupRow]:
    return fv.load_groups(FIXTURES)


def _write_lines(path: Path, rows: list[object]) -> Path:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    return path


def _cells(svg: str) -> dict[str, str]:
    """Cell tooltip prefix ``group · column`` -> fill, from the grid rects."""
    root = ET.fromstring(svg)
    out: dict[str, str] = {}
    for rect in root.iter(f"{SVG_NS}rect"):
        title = rect.find(f"{SVG_NS}title")
        if title is not None and title.text:
            out[title.text.split(":", 1)[0]] = rect.get("fill", "")
    return out


# --- golden files ---------------------------------------------------------


@pytest.mark.parametrize(("golden", "extra"), [
    ("expected.svg", ()),
    ("expected-compare.svg", ("--compare", str(FIXTURES / "earlier"))),
])
def test_render_matches_golden(tmp_path, golden, extra):
    assert _render(tmp_path, *extra) == (FIXTURES / golden).read_text(encoding="utf-8")


def test_rerender_is_byte_identical(tmp_path):
    first = _render(tmp_path, "--compare", str(FIXTURES / "earlier"))
    assert _render(tmp_path, "--compare", str(FIXTURES / "earlier")) == first


def test_committed_fixture_runs_match_a_fresh_build(tmp_path):
    build(tmp_path)
    for name in ("groups.jsonl", "earlier/groups.jsonl"):
        assert (tmp_path / name).read_bytes() == (FIXTURES / name).read_bytes(), name


def test_fixture_runs_match_the_group_schema():
    for path in (FIXTURES / "groups.jsonl", FIXTURES / "earlier" / "groups.jsonl"):
        for line in path.read_text(encoding="utf-8").splitlines():
            jsonschema.validate(json.loads(line), build_schema())


# --- semantics the golden cannot explain ----------------------------------


def test_every_group_and_row_present_and_unknown_hatched_apart_from_zero(tmp_path):
    svg = _render(tmp_path)
    cells = _cells(svg)
    groups = [g["group"] for g in _groups()]
    titles = ["Two-factor", "Team instructions and shared tooling across repositories"]
    assert sorted(cells) == sorted(f"{g} · {t}" for g in groups for t in titles)
    assert cells["grpb · Two-factor"] == fh.ORRD_9[0]  # level 0: a ramp colour
    assert cells["grpc · Two-factor"] == fh.UNKNOWN_FILL  # unknown: the hatch
    assert cells["grpa · Two-factor"] == fh.ORRD_9[-1]  # top level of its row
    assert fh.UNKNOWN_FILL not in fh.ORRD_9


def test_unknown_cells_name_the_missing_signal_in_visible_text(tmp_path):
    svg = _render(tmp_path)
    texts = [t.text or "" for t in ET.fromstring(svg).iter(f"{SVG_NS}text")]
    assert "? a" in texts and "missing two_factor.value" in texts
    assert "missing shared_tooling.available" in texts
    assert any(t.startswith("unknown: not measured, never level 0") for t in texts)


def test_long_group_name_keeps_its_tail_and_the_full_name_on_hover(tmp_path):
    svg = _render(tmp_path)
    assert "…ement-and-reconciliation/batch-services" in svg
    assert "<title>platform/payments/settlement-and-reconciliation/batch-services</title>" in svg
    assert "1 repository without a level" in svg and "1 repositories" not in svg


def test_counts_in_the_subtitle_agree_in_number():
    groups = _groups()[:1]
    columns = fh.columns_for(groups, None)[:1]
    svg = fh.render_heatmap(groups, columns)
    assert "1 group \u00b7 1 rubric row \u00b7" in svg


def test_svg_parses_has_a_background_and_no_stylesheet_override(tmp_path):
    svg = _render(tmp_path, "--compare", str(FIXTURES / "earlier"))
    root = ET.fromstring(svg)
    first_rect = next(root.iter(f"{SVG_NS}rect"))
    assert first_rect.get("fill") == "#ffffff"  # readable on light and dark pages
    assert overridden_text_attributes(svg) == []


def test_comparison_text_and_removed_groups(tmp_path):
    svg = _render(tmp_path, "--compare", str(FIXTURES / "earlier"))
    texts = [t.text or "" for t in ET.fromstring(svg).iter(f"{SVG_NS}text")]
    for marker in ("+2", "-2", "=", "was 2", "new"):
        assert marker in texts
    assert "groups in the earlier run only: grpd" in texts


def test_comparison_across_rubric_versions_is_not_shown(tmp_path):
    earlier = [dict(g, rubric_version="fixture-0") for g in _groups()]
    path = _write_lines(tmp_path / "earlier.jsonl", list(earlier))
    svg = _render(tmp_path, "--compare", str(path))
    assert "change not shown: the earlier run used rubric fixture-0, this run fixture-1" in svg
    assert "+2" not in svg


def test_without_a_rubric_file_levels_index_the_ramp(tmp_path):
    out = tmp_path / "plain.svg"
    assert cli.main([str(FIXTURES / "groups.jsonl"), "--output", str(out)]) == 0
    svg = out.read_text(encoding="utf-8")
    assert "not scaled to each row" in svg
    assert _cells(svg)["grpa · teamdocs"] == fh.ORRD_9[2]


def test_default_output_is_beside_groups_jsonl(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    (run / "groups.jsonl").write_bytes((FIXTURES / "groups.jsonl").read_bytes())
    assert cli.main([str(run)]) == 0
    out = run / cli.HEATMAP_FILE
    assert out.read_text(encoding="utf-8").startswith("<?xml")
    assert out.stat().st_mode & 0o777 == 0o644
    assert sorted(p.name for p in run.iterdir()) == sorted(["groups.jsonl", cli.HEATMAP_FILE])


# --- encoding helpers -----------------------------------------------------


@pytest.mark.parametrize(("level", "top", "step"), [
    (0, 2, 0), (1, 2, 4), (2, 2, 8), (1, 1, 8), (1, 3, 3), (2, 3, 5), (3, 3, 8),
    (0, None, 0), (2, None, 2), (12, None, 8), (5, 0, 5),
])
def test_ramp_step(level, top, step):
    assert fh.ramp_step(level, top) == step


def test_text_colour_table_covers_every_step():
    assert len(fh.STEP_TEXT) == len(fh.ORRD_9)
    assert fh.STEP_TEXT[0] == fh.TEXT_DARK and fh.STEP_TEXT[-1] == fh.TEXT_LIGHT


@pytest.mark.parametrize(("index", "label"), [(0, "a"), (25, "z"), (26, "aa"), (27, "ab"), (701, "zz")])
def test_key_label(index, label):
    assert fh.key_label(index) == label


def test_truncate():
    assert fh.truncate("short", 10) == "short"
    assert fh.truncate("abcdefghij", 5) == "abcd…"
    assert fh.truncate("abcdefghij", 5, keep_tail=True) == "…ghij"


@pytest.mark.parametrize(("change", "text"), [
    ({"kind": "new", "then": None, "delta": None}, "new"),
    ({"kind": "was", "then": 2, "delta": None}, "was 2"),
    ({"kind": "was", "then": None, "delta": None}, "was ?"),
    ({"kind": "delta", "then": 1, "delta": 0}, "="),
    ({"kind": "delta", "then": 1, "delta": 1}, "+1"),
    ({"kind": "delta", "then": 2, "delta": -2}, "-2"),
])
def test_change_text(change, text):
    assert fh.change_text(change) == text


def test_unknown_with_no_missing_signal_says_no_evidence():
    (group, *_rest) = _groups()
    entry = dict(group["rubric"][0], status="unknown", level=None, missing_signals=[])
    empty: GroupRow = dict(group, rubric=[entry])  # type: ignore[assignment]
    columns: list[fh.Column] = [{"id": entry["id"], "title": "t", "top": 1}]
    assert list(fh.unknown_keys([empty], columns)) == ["no evidence (no repository scanned)"]


# --- the shared reader ----------------------------------------------------


def test_load_groups_reads_a_directory_or_a_file_sorted():
    by_dir = fv.load_groups(FIXTURES)
    assert by_dir == fv.load_groups(FIXTURES / "groups.jsonl")
    assert [g["group"] for g in by_dir] == sorted(g["group"] for g in by_dir)
    assert fv.rubric_columns(by_dir) == ["org2fa", "teamdocs"]
    assert fv.run_rubric_version(by_dir) == "fixture-1"


def _one() -> dict[str, object]:
    return dict(_groups()[0])


@pytest.mark.parametrize(("rows", "message"), [
    (["not json"], "groups.jsonl:1: not JSON"),
    ([[1]], "not a JSON object"),
    ([dict(_one(), fleet_group_version=99)], "fleet_group_version is 99, expected 1"),
    ([dict(_one(), group=None)], "needs a string 'group'"),
    ([dict(_one(), rubric=[{"id": "x"}])], "lacks one of"),
    ([dict(_one(), rubric=[{"id": "x", "status": "maybe", "level": None,
                            "missing_signals": [], "unknown_repos": []}])], "no whole level"),
    ([_one(), _one()], "appears twice"),
    ([{k: v for k, v in _one().items() if k != "rubric_version"}], "rubric_version"),
    ([dict(_one(), rubric_version=3)], "rubric_version"),
    ([dict(_one(), rubric=[{"id": "x", "status": "unknown", "level": None,
                            "missing_signals": "sig", "unknown_repos": []}])], "malformed"),
    ([dict(_one(), rubric=[{"id": 1, "status": "unknown", "level": None,
                            "missing_signals": [], "unknown_repos": []}])], "malformed"),
])
def test_load_groups_refuses_malformed_runs(tmp_path, rows, message):
    path = tmp_path / "groups.jsonl"
    path.write_text("".join((r if isinstance(r, str) else json.dumps(r)) + "\n" for r in rows),
                    encoding="utf-8")
    with pytest.raises(fv.FleetViewError, match=message):
        fv.load_groups(path)


def test_read_repos_reads_a_directory_or_a_file_sorted(tmp_path):
    rows = [{"fleet_row_version": FLEET_ROW_VERSION, "repo": r, "group": "g"} for r in ("g/b", "g/a")]
    _write_lines(tmp_path / "repos.jsonl", list(rows))
    assert [r["repo"] for r in fv.read_repos(tmp_path)] == ["g/a", "g/b"]
    assert fv.read_repos(tmp_path / "repos.jsonl") == fv.read_repos(tmp_path)
    _write_lines(tmp_path / "repos.jsonl", [{"fleet_row_version": FLEET_ROW_VERSION, "repo": "g/a"}])
    with pytest.raises(fv.FleetViewError, match="repos.jsonl:1: no repo or group id"):
        fv.read_repos(tmp_path)


def test_an_empty_run_loads_as_no_groups_and_the_heat_map_exits_2(tmp_path, capsys):
    (tmp_path / "groups.jsonl").write_text("", encoding="utf-8")
    assert fv.load_groups(tmp_path) == []
    assert fv.run_rubric_version([]) is None
    assert cli.main([str(tmp_path)]) == 2
    assert "no groups" in capsys.readouterr().err
    assert not (tmp_path / cli.HEATMAP_FILE).exists()
    comparison = fv.compare_levels(_groups(), [])
    assert comparison["comparable"]
    assert comparison["changes"]["grpa"]["org2fa"]["kind"] == "new"


def test_a_boolean_level_is_not_a_whole_level(tmp_path):
    entry = {"id": "x", "status": "level", "level": True, "missing_signals": [], "unknown_repos": []}
    path = _write_lines(tmp_path / "groups.jsonl", [dict(_one(), rubric=[entry])])
    with pytest.raises(fv.FleetViewError, match="no whole level"):
        fv.load_groups(path)


def test_load_groups_missing_file(tmp_path):
    with pytest.raises(fv.FleetViewError, match="cannot read"):
        fv.load_groups(tmp_path / "absent.jsonl")


def test_run_shape_errors():
    a, b, *_rest = _groups()
    with pytest.raises(fv.FleetViewError, match="--rubric"):
        fv.rubric_columns([dict(a, rubric=[])])  # type: ignore[list-item]
    with pytest.raises(fv.FleetViewError, match="different rubric rows"):
        fv.rubric_columns([a, dict(b, rubric=b["rubric"][:1])])  # type: ignore[list-item]
    with pytest.raises(fv.FleetViewError, match="different rubric versions"):
        fv.run_rubric_version([a, dict(b, rubric_version="other")])  # type: ignore[list-item]
    with pytest.raises(fv.FleetViewError, match="no rubric row"):
        fv.cell(a, "absent")


def test_check_rubric_refuses_another_rubric(tmp_path):
    rubric = load_rubric(FIXTURES / "rubric.toml")
    fv.check_rubric(rubric, _groups())
    with pytest.raises(fv.FleetViewError, match="version"):
        fv.check_rubric(dict(rubric, version="fixture-2"), _groups())  # type: ignore[arg-type]
    with pytest.raises(fv.FleetViewError, match="rows differ"):
        fv.check_rubric(dict(rubric, rows=rubric["rows"][:1]), _groups())  # type: ignore[arg-type]


def test_compare_levels_kinds():
    now = _groups()
    then = fv.load_groups(FIXTURES / "earlier")
    comparison = fv.compare_levels(now, then)
    assert comparison["comparable"] and comparison["removed_groups"] == ["grpd"]
    changes = comparison["changes"]
    assert changes["grpa"]["teamdocs"] == {"kind": "delta", "then": 0, "delta": 2}
    assert changes["grpc"]["teamdocs"] == {"kind": "was", "then": 2, "delta": None}
    assert changes["grpc"]["org2fa"] == {"kind": "delta", "then": None, "delta": 0}
    long = "platform/payments/settlement-and-reconciliation/batch-services"
    assert changes[long]["org2fa"]["kind"] == "new"
    assert fv.compare_levels(now, now)["changes"]["grpa"]["org2fa"]["delta"] == 0


# --- CLI exit codes -------------------------------------------------------


def test_cli_exits_2_on_a_run_without_rubric_levels(tmp_path, capsys):
    path = _write_lines(tmp_path / "groups.jsonl", [dict(_one(), rubric=[], rubric_version=None)])
    assert cli.main([str(path)]) == 2
    assert "--rubric" in capsys.readouterr().err


def test_cli_exits_2_on_a_rubric_file_from_another_run(tmp_path, capsys):
    other = tmp_path / "other.toml"
    other.write_text(re.sub(r'version = "fixture-1"', 'version = "fixture-9"',
                            (FIXTURES / "rubric.toml").read_text(encoding="utf-8")), encoding="utf-8")
    assert cli.main([str(FIXTURES), "--rubric", str(other), "--output", str(tmp_path / "x.svg")]) == 2
    assert "fixture-9" in capsys.readouterr().err
    assert not (tmp_path / "x.svg").exists()


def test_cli_exits_2_on_an_unreadable_comparison(tmp_path):
    assert cli.main([str(FIXTURES), "--compare", str(tmp_path / "absent"),
                     "--output", str(tmp_path / "x.svg")]) == 2
