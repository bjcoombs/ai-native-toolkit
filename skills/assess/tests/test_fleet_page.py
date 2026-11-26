"""``fleet-view.html`` (#946): the page, its bundle and its script.

Goldens: ``tests/fixtures/fleet_page/`` (regenerate with
``uv run --locked python tests/fleet_page_fixture.py`` from ``skills/assess``). The script is
run under Node (``node:vm``, no browser and no DOM): its pure ``renderPage`` and ``cellTitle``
draw the fixture's page, so the hover text and the rendered strings are tested as a viewer
would read them.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

import fleet_data_fixture as fdf
import fleet_page_fixture as fx
from lib.fleet.fleet_page import PAGE_FILE, build_page, clear_page, embed, write_page
from lib.fleet.fleet_page_data import COUNTS_ONLY, FULL, HASHED, mode_of
from lib.vocabulary import banned_hits
from test_fleet_cli import fleet_cli, froot  # noqa: F401 - froot is a fixture

NODE = shutil.which("node")
RUNNER = r"""
const vm = require("node:vm");
const fs = require("node:fs");
const input = JSON.parse(fs.readFileSync(0, "utf8"));
const ctx = {};
vm.createContext(ctx);
vm.runInContext(input.script, ctx);
const api = ctx.FleetView;
const b = input.bundle;
const out = {pages: [], titles: []};
b.views.forEach(function (v) {
  v.units.forEach(function (u) {
    out.pages.push(api.renderPage(b, {view: v.view, unit: u.key, repo: null}, b.targets, false));
  });
});
b.repos.forEach(function (r) {
  const unit = r.units.ownership;
  if (unit) { out.pages.push(api.renderPage(b, {view: "ownership", unit: unit, repo: r.key}, b.targets, false)); }
  r.cells.forEach(function (c) { out.titles.push({repo: r.key, row: c.row, status: c.status, title: api.cellTitle(b, r, c)}); });
});
out.withSet = api.renderPage(b, {view: "ownership", unit: b.views[0].units[0].key, repo: null}, b.targets, true);
out.withSetNoId = out.withSet;
b.views[0].units[0].target_id = "ownership~x";
out.withSet = api.renderPage(b, {view: "ownership", unit: b.views[0].units[0].key, repo: null}, b.targets, true);
out.staticHasSet = typeof api.staticSource(b).setTarget;
out.sections = api.SECTIONS.length;
out.first = api.SECTIONS[0](b, {view: b.views[0].view, unit: b.views[0].units[0].key, repo: null}, b.targets, false, {});
process.stdout.write(JSON.stringify(out));
"""


def _parts(html: str) -> tuple[str, dict[str, Any]]:
    script = re.findall(r"<script>(.*?)</script\s*>", html, re.S | re.I)[0]
    raw = re.search(r'<script type="application/json" id="fleet-bundle">(.*?)</script\s*>', html, re.S | re.I)
    assert raw is not None
    return script, json.loads(raw.group(1))


def _drawn(name: str) -> dict[str, Any]:
    if NODE is None:
        pytest.skip("node is not on PATH")
    script, bundle = _parts((fx.FIXTURES / f"expected-{name}.html").read_text(encoding="utf-8"))
    done = subprocess.run([NODE, "-e", RUNNER], input=json.dumps({"script": script, "bundle": bundle}),
                          capture_output=True, text=True, encoding="utf-8", errors="replace", check=True)
    out: dict[str, Any] = json.loads(done.stdout)
    return out


def _text(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))


@pytest.mark.parametrize("name", sorted(fx.goldens()))
def test_page_matches_golden(name: str) -> None:
    assert fx.goldens()[name] == (fx.FIXTURES / name).read_text(encoding="utf-8")


def test_two_builds_are_byte_identical() -> None:
    assert fx.goldens() == fx.goldens()


@pytest.mark.parametrize("name", sorted(fx.VARIANTS))
def test_page_is_self_contained(name: str) -> None:
    html = fx.goldens()[f"expected-{name}.html"]
    assert not re.search(r"""(src|href)=["'](?!data:)""", html.replace('href=\\"', ""))
    assert "http://" not in html.replace("http://www.w3.org/2000/svg", "")
    assert "fetch(" not in html and "XMLHttpRequest" not in html and "import(" not in html
    assert "prefers-color-scheme: dark" in html and 'name="viewport"' in html


def test_the_page_computes_nothing_and_every_count_is_the_bundle_s() -> None:
    _, bundle = _parts(fx.goldens()["expected-full.html"])
    data = fx.run(fx.DATES[-1], fx.VARIANTS["full"])
    root = next(u for u in data["views"][0]["units"] if u["parent"] is None)
    page_root = next(u for u in bundle["views"][0]["units"] if u["parent"] is None)
    assert page_root["lowest_step"] == root["step"]["lowest_step"] and page_root["measured"] == root["step"]["measured"]
    assert bundle["targets"][0]["missing"] == [{k: g[k] for k in ("gate", "held", "not_measured", "excluded")}
                                               for g in bundle["targets"][0]["missing"]]


# --- privacy ----------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["counts-only", "hashed"])
def test_counts_only_and_hashed_pages_name_no_repository_unit_url_or_person(name: str) -> None:
    html = fx.goldens()[f"expected-{name}.html"]
    for leak in fx.NAMES:
        assert leak not in html, leak
    assert "CI runs in the release pipeline" not in html  # the owner's reason: --full only


def test_the_full_page_names_repositories_and_shows_the_exclusion_reason() -> None:
    html = fx.goldens()["expected-full.html"]
    assert "grp/sub/proj1" in html and "CI runs in the release pipeline" in html
    assert "bob" not in html and "checkout-leads" not in html  # people never appear


def test_modes_follow_the_run_stamp() -> None:
    assert [mode_of(fx.run(fx.DATES[-1], p)) for p in fx.VARIANTS.values()] == [FULL, COUNTS_ONLY, HASHED]


def test_no_layer_number_or_condition_id_reaches_the_page() -> None:
    for html in fx.goldens().values():
        assert not re.search(r"layer|\bdocs\.1\b|\bci\.1\b", html)


# --- rendered in Node -------------------------------------------------------------------


@pytest.mark.parametrize("name", sorted(fx.VARIANTS))
def test_every_rendered_string_passes_the_vocabulary_guard(name: str) -> None:
    out = _drawn(name)
    for page in [*out["pages"], *(t["title"] for t in out["titles"])]:
        assert banned_hits(_text(page)) == []
        assert not re.search(r"\blayer\b", page, re.I)


def test_hover_names_state_condition_and_next_step_and_a_not_measured_cell_shows_its_reason() -> None:
    out = _drawn("full")
    by = {(t["repo"], t["row"]): t["title"] for t in out["titles"]}
    unknown = by[("grp/other/proj3", 0)]
    assert "State: not measured (unavailable)" in unknown and "level 0 of" not in unknown
    assert "Condition: Instruction docs" in unknown and "reads docs.lines" in unknown
    assert "Step 2 · Docs needs: docs (not measured: unavailable)" in unknown
    known = by[("grp/sub/proj2", 0)]
    assert "State: level 1 of 2" in known and "Condition: Instruction docs, level 2, reads docs.grade" in known
    assert "Step 3 · Docs and CI needs: ci" in known
    for t in out["titles"]:
        assert t["title"].splitlines()[1].startswith("State: ")


def test_sections_come_in_the_design_order_and_target_entry_is_static_hidden() -> None:
    out = _drawn("counts-only")
    page = out["pages"][0]
    heads = re.findall(r"<h2>(.*?)</h2>", page)
    assert heads == ["Fleet at a glance", "Since last run", "Where this unit stands", "Your target", "Drill-down",
                     "Practice shape", "Fleet pictures"]
    assert out["staticHasSet"] == "undefined" and "set-target" not in page
    assert "set-target" in out["withSet"]  # a live source with setTarget draws the entry for a unit with a target id
    assert "set-target" not in out["withSetNoId"]  # and none for a unit without one (#949)
    assert out["sections"] == 8  # Practice packs (#1276) draws nothing without a pack


def test_the_root_page_shows_the_target_gates_the_credit_and_the_exclusion() -> None:
    out = _drawn("full")
    root = _text(out["pages"][0])
    assert "Target Step 3 · Docs and CI. 2 Steps to go, 3 repositories below it." in root
    assert "set aside by the owner" in root
    assert "grp/sub reached level 1 of 2 on Instruction docs." in root
    detail = _text(next(p for p in out["pages"] if "proj1</strong>" in p))
    assert "CI gate, level 1 (measured_elsewhere): CI runs in the release pipeline repository." in detail


def test_units_are_listed_by_name_never_by_rank() -> None:
    out = _drawn("full")
    page = out["pages"][0]
    listed = re.findall(r'data-unit="([^"]+)">\1</button> <span', page)
    assert listed == sorted(listed)


# --- writing ------------------------------------------------------------------------------


def test_write_page_embeds_drawn_pictures_and_clears_without_data(tmp_path: Path) -> None:
    data = fx.run(fx.DATES[-1], fx.VARIANTS["counts-only"])
    drawn = [{"file": "step-funnel.svg", "title": "T", "question": "Q", "svg": "<svg/>"},
             {"file": "slope.svg", "title": "S", "question": "Q", "svg": "<svg/>"},
             {"file": "one-change-away.svg", "title": "O", "question": "Q", "svg": None}]
    path = write_page(tmp_path, dict(data), None, fdf.model(), drawn)
    assert path is not None and path.name == PAGE_FILE
    _, bundle = _parts(path.read_text(encoding="utf-8"))
    assert [p["file"] for p in bundle["pictures"]] == ["step-funnel.svg"]
    assert write_page(tmp_path, None, None, fdf.model(), drawn) is None and not path.exists()
    path = write_page(tmp_path, dict(data), None, fdf.model(), [])
    assert path is not None
    clear_page(tmp_path)
    assert not path.exists()


def test_embedded_json_cannot_close_its_script_block() -> None:
    bundle = fx.bundle("full")
    bundle["note"] = "</script><script>alert(1)</script>"
    html = build_page(bundle)
    assert "</script><script>alert" not in html and json.loads(embed(bundle))["note"] == bundle["note"]


@pytest.mark.parametrize("mode", [[], ["--full"], ["--hash-salt", "s3cret"]], ids=["counts-only", "full", "hashed"])
def test_fleet_writes_the_page_and_names_no_repository_outside_full(froot: Path, tmp_path: Path,  # noqa: F811 - froot is a pytest fixture
                                                                    mode: list[str]) -> None:
    from lib.scoring.level_signals import DEFAULT_LEVELS
    out = tmp_path / "out"
    history = ["--no-history"] if mode == ["--full"] else ["--history-dir", str(tmp_path / "h")]
    assert fleet_cli.main([str(froot), "--levels", str(DEFAULT_LEVELS), *history, "--out", str(out), *mode]) == 0
    html = (out / PAGE_FILE).read_text(encoding="utf-8")
    names = ("grpa", "grpb", "svc1", "svc2", "lib1", str(froot))
    if mode == ["--full"]:
        assert "grpa/svc1" in html
    else:
        assert not [n for n in names if n in html]
    assert str(froot) not in html
    assert fleet_cli.main([str(froot), "--levels", str(DEFAULT_LEVELS), *history, "--out", str(out), *mode,
                           "--no-diagrams"]) == 0
    assert not (out / PAGE_FILE).exists()


def test_every_achievement_kind_and_the_recount_take_the_page_labels() -> None:
    data = dict(fx.run(fx.DATES[-1], fx.VARIANTS["counts-only"]))
    key = data["repos"][0]["key"]
    ev = {"row": "docs", "from_level": 0, "to_level": 1, "level": 1, "from_step": 1, "to_step": 2,
          "target_step": 2, "runs": 3, "moves": []}
    ach = {"run": "r2", "runs": ["r1", "r2"], "note": None, "targets": [],
           "achievements": [{"kind": k, "view": "repo", "unit": key, "unit_kind": "repo", "run": "r2", "evidence": ev}
                            for k in ("level_up", "first", "target_hit", "streak")]
           + [{"kind": "streak", "view": "repo", "unit": key, "unit_kind": "repo", "run": "r1", "evidence": ev}],
           "recount": {"from_version": "a", "to_version": "b", "run": "r1",
                       "lines": [{"repo": key, "row": "docs", "cause": "Now checked", "from_level": 1, "to_level": 0}]}}
    from lib.fleet.fleet_page_data import page_bundle
    bundle = page_bundle(data, ach, fdf.model())
    assert [a["text"] for a in bundle["achievements"]] == [
        "repository 1 reached level 1 of 2 on Instruction docs.",
        "A first for this fleet: repository 1 is the first repository at level 1 on Instruction docs.",
        "repository 1 reached its target of Step 2: Step 1 to Step 2.",
        "repository 1 held every level for 3 runs in a row."]
    assert bundle["recount"] is not None and bundle["recount"]["lines"][0]["repo"] == "repository 1"
    assert key not in embed(bundle)


@pytest.mark.parametrize("name", ["counts-only", "hashed"])
def test_keys_not_in_this_run_never_reach_the_page_by_their_raw_name(name: str) -> None:
    """A dropped repository's Recount line, a target for a unit this run did not build and an
    achievement for a unit not in the run are left out, never shown by the raw key."""
    privacy = fx.VARIANTS[name]
    data = dict(fx.run(fx.DATES[-1], privacy))
    gone_repo, gone_unit = privacy.key("grp/gone/oldrepo"), privacy.key("ownership/grp/oldteam")
    ev = {"row": "docs", "from_level": 0, "to_level": 1, "level": 1, "from_step": 1, "to_step": 2,
          "target_step": 2, "runs": 3, "moves": []}
    ach = {"run": "r2", "runs": ["r1", "r2"], "note": None,
           "achievements": [{"kind": "level_up", "view": "ownership", "unit": gone_unit, "unit_kind": "team",
                             "run": "r2", "evidence": ev}],
           "targets": [{"view": "ownership", "unit": gone_unit, "step": 3, "lowest_step": None, "measured": 0,
                        "repos": 0, "distance": None, "held": 0, "missing_gates": []}],
           "recount": {"from_version": "a", "to_version": "b", "run": "r1",
                       "lines": [{"repo": gone_repo, "row": "docs", "cause": "Now checked", "from_level": 1,
                                  "to_level": 0}]}}
    from lib.fleet.fleet_page_data import page_bundle
    bundle = page_bundle(data, ach, fdf.model())
    html = build_page(bundle)
    assert bundle["achievements"] == [] and bundle["targets"] == []
    assert bundle["recount"] is not None and bundle["recount"]["lines"] == []
    for leak in ("gone", "oldrepo", "oldteam", *fx.NAMES):
        assert leak not in html, leak
    if name == "hashed":
        assert gone_repo not in html and gone_unit not in html


def test_the_shipped_model_s_gate_ids_carry_no_layer_number() -> None:
    """Gate ids reach the page as written, so the shipped ones must be layer-free."""
    from lib.scoring.level_signals import DEFAULT_LEVELS
    from lib.scoring.levels import load_levels
    gates = {g["id"] for s in load_levels(DEFAULT_LEVELS).get("steps", []) for g in s["gates"]}
    assert gates and not [g for g in gates if re.search(r"layer", g, re.I)]


def test_the_next_level_comes_from_the_bundle() -> None:
    _, bundle = _parts(fx.goldens()["expected-full.html"])
    cells = [c for r in bundle["repos"] for c in r["cells"]]
    assert all(c["next_level"] == (c["level"] + 1 if c["status"] == "level" and c["signal"] else None) for c in cells)
    script, _ = _parts(fx.goldens()["expected-full.html"])
    assert "cell.level + 1" not in script


def test_a_credit_shows_only_in_its_own_view_when_two_views_share_a_unit_path() -> None:
    """--full labels are unique within a view only: a system unit and an ownership unit both
    called ``payments`` keep their own credits."""
    if NODE is None:
        pytest.skip("node is not on PATH")
    script, _ = _parts(fx.goldens()["expected-full.html"])
    unit = {"kind": "team", "parent": None, "repos": 1, "measured": 1, "lowest_step": 1, "next": None,
            "tags": [], "tags_measured": 1}
    bundle = {"scan_date": "d", "rows": [], "steps": [], "tags": [], "note": None, "recount": None, "targets": [],
              "pictures": [], "repos": [{"key": "r", "units": {"ownership": "payments", "system": "payments"},
                                         "cells": [], "step": 1, "next": None, "exclusions": []}],
              "views": [{"view": "ownership", "units": [{**unit, "key": "payments"}]},
                        {"view": "system", "units": [{**unit, "key": "payments"}]}],
              "achievements": [{"kind": "level_up", "view": "ownership", "unit": "payments", "run": "d",
                                "text": "OWNERSHIP CREDIT"},
                               {"kind": "level_up", "view": "system", "unit": "payments", "run": "d",
                                "text": "SYSTEM CREDIT"},
                               {"kind": "level_up", "view": "repo", "unit": "r", "run": "d", "text": "REPO CREDIT"}]}
    runner = """
const vm = require("node:vm"); const fs = require("node:fs");
const input = JSON.parse(fs.readFileSync(0, "utf8")); const ctx = {}; vm.createContext(ctx);
vm.runInContext(input.script, ctx); const b = input.bundle;
process.stdout.write(JSON.stringify(["ownership", "system"].map(function (v) {
  return ctx.FleetView.renderPage(b, {view: v, unit: "payments", repo: null}, [], false); })));
"""
    done = subprocess.run([NODE, "-e", runner], input=json.dumps({"script": script, "bundle": bundle}),
                          capture_output=True, text=True, encoding="utf-8", errors="replace", check=True)
    ownership, system = json.loads(done.stdout)
    assert "OWNERSHIP CREDIT" in ownership and "SYSTEM CREDIT" not in ownership and "REPO CREDIT" in ownership
    assert "SYSTEM CREDIT" in system and "OWNERSHIP CREDIT" not in system and "REPO CREDIT" in system


# --- the front screen (#1255) ------------------------------------------------------------


def _render(bundle: dict[str, Any]) -> str:
    """The first section of ``bundle``'s page, drawn by the shipped script under Node."""
    if NODE is None:
        pytest.skip("node is not on PATH")
    script, _ = _parts(fx.goldens()["expected-full.html"])
    runner = """
const vm = require("node:vm"); const fs = require("node:fs");
const input = JSON.parse(fs.readFileSync(0, "utf8")); const ctx = {}; vm.createContext(ctx);
vm.runInContext(input.script, ctx); const b = input.bundle;
process.stdout.write(JSON.stringify(ctx.FleetView.SECTIONS[0](b, {view: "ownership", unit: b.views[0].units[0].key,
  repo: null}, b.targets, false, {})));
"""
    done = subprocess.run([NODE, "-e", runner], input=json.dumps({"script": script, "bundle": bundle}),
                          capture_output=True, text=True, encoding="utf-8", errors="replace", check=True)
    first: str = json.loads(done.stdout)
    return first


@pytest.mark.parametrize("name", sorted(fx.VARIANTS))
def test_the_first_section_renders_the_front_screen(name: str) -> None:
    out = _drawn(name)
    first = _text(out["first"])
    assert re.findall(r"<h2>(.*?)</h2>", out["first"]) == ["Fleet at a glance"]
    assert "Step 1 · Assisted: 1 repository" in first and "Step 2 · Docs: 2 repositories" in first
    assert "1 repository moved up a Step and 1 repository moved down; 1 stayed at the same Step." in first
    assert "1 move came from rule changes alone." in first
    assert "1 repository is one change from the next Step." in first
    assert "ci : make the ci gate hold. Moves 1 repository to the next Step." in first
    assert out["pages"][0].startswith(out["pages"][0].split("<h2>")[0] + out["first"])  # first on every page


def test_the_front_screen_s_gates_are_sorted_by_repositories_unblocked_descending() -> None:
    from lib.fleet.fleet_front import front_screen
    from lib.fleet.fleet_page_data import page_bundle
    data: dict[str, Any] = dict(fx.run(fx.DATES[-1], fx.VARIANTS["counts-only"]))
    lifts = [("docs.1", "docs", 2, 1), ("ci.1", "ci", 3, 3), ("cov.1", "coverage_gate", 3, 2),
             ("docs.2", "docs", 3, 4)]
    data["opportunities"] = {**data["opportunities"], "step_lifts": [
        {"condition": c, "row": c.split(".")[0], "title": c, "gate": g, "step": st, "repo_count": n}
        for c, g, st, n in lifts]}
    bundle = page_bundle(data, None, fdf.model(), (), dict(front_screen(fdf.model(), data)))
    first = _render(dict(bundle))
    gates = re.findall(r"<li><code>([^<]+)</code>: [^<]*Moves (\d+) repositor", first)
    assert gates == [("docs", "5"), ("ci", "3"), ("coverage_gate", "2")]
    assert [int(n) for _, n in gates] == sorted((int(n) for _, n in gates), reverse=True)
    assert "enforce a coverage gate in CI" in first  # the gate's wording is the top card's (#1252)


def test_the_front_screen_s_team_links_are_alphabetical() -> None:
    from lib.fleet.fleet_front import front_screen
    from lib.fleet.fleet_page_data import page_bundle
    data: dict[str, Any] = dict(fx.run(fx.DATES[-1], fx.VARIANTS["full"]))
    view = data["views"][0]
    extra = [{**u, "key": f"ownership/{name}", "parent": None} for name in ("zeta", "alpha", "mid")
             for u in view["units"] if u.get("kind") == "team"][::2]
    data["views"] = [{**view, "units": [*reversed(view["units"]), *extra]}, *data["views"][1:]]
    bundle = page_bundle(data, None, fdf.model(), (), dict(front_screen(fdf.model(), data)))
    teams = re.findall(r'data-view="ownership" data-unit="([^"]+)"', _render(dict(bundle)))
    assert teams == sorted(teams) and len(teams) >= 3 and "grp/sub" in teams


@pytest.mark.parametrize("name", ["counts-only", "hashed"])
def test_the_front_screen_names_no_repository_or_group_outside_full(name: str) -> None:
    first = _drawn(name)["first"]
    for leak in fx.NAMES:
        assert leak not in first, leak


def test_a_pack_run_s_page_names_each_pack_with_its_counts() -> None:
    """#1276: the Practice packs section, copied from fleet-data.json's packs roll-up; no
    condition id reaches the page."""
    from lib.fleet.fleet_page_data import page_bundle
    if NODE is None:
        pytest.skip("node is not on PATH")
    script, bundle = _parts((fx.FIXTURES / "expected-counts-only.html").read_text(encoding="utf-8"))
    roll = [{"name": "team", "version": "1", "sha256": "a" * 64, "repos": 3, "practices": [
        {"condition": "team-adrs.1", "row": "team-adrs", "title": "Decision records", "level": 1,
         "met": 1, "measured": 2, "not_measured": 1}]}]
    copied = page_bundle({"run": {}, "views": [], "repos": [], "packs": roll}, None, {"version": "x", "rows": []})
    assert copied["packs"] == [{"name": "team", "version": "1", "repos": 3, "practices": [
        {"title": "Decision records", "level": 1, "met": 1, "measured": 2, "not_measured": 1}]}]
    bundle["packs"] = copied["packs"]
    runner = (RUNNER.split("const out")[0] + "process.stdout.write(api.renderPage(b, {view: b.views[0].view, "
              "unit: b.views[0].units[0].key, repo: null}, b.targets, false));")
    page = subprocess.run([NODE, "-e", runner], input=json.dumps({"script": script, "bundle": bundle}),
                          capture_output=True, text=True, encoding="utf-8", errors="replace", check=True).stdout
    heads = re.findall(r"<h2>(.*?)</h2>", page)
    assert heads[:2] == ["Fleet at a glance", "Practice packs"]
    text = _text(page)
    assert "Pack team (version 1)" in text and "Decision records: 1 of 2 measured repositories meet it" in text
    assert "1 repository not measured" in text and "team-adrs" not in page


# --- practice shape (#1368, part 3 of #1314) ---------------------------------------------


def _shape(page: str) -> str:
    start = page.index("<h2>Practice shape</h2>")
    end = page.find("<h2>Fleet pictures</h2>")
    return page[start:] if end < 0 else page[start:end]


def _root_page(bundle: dict[str, Any]) -> str:
    """The page on the ownership root of ``bundle``, drawn by the shipped script under Node."""
    if NODE is None:
        pytest.skip("node is not on PATH")
    script, _ = _parts(fx.goldens()["expected-full.html"])
    runner = (RUNNER.split("const out")[0] + "const r = b.views[0].units.filter(function (u) { return !u.parent; })[0];"
              "process.stdout.write(api.renderPage(b, {view: \"ownership\", unit: r.key, repo: null}, b.targets, false));")
    return subprocess.run([NODE, "-e", runner], input=json.dumps({"script": script, "bundle": bundle}),
                          capture_output=True, text=True, encoding="utf-8", errors="replace", check=True).stdout


@pytest.mark.parametrize("name", sorted(fx.VARIANTS))
def test_the_practice_shape_panel_draws_one_radar_per_unit_and_the_table_fallback(name: str) -> None:
    """The root page draws one chart per ownership unit, each its own ``<svg>`` holding one unit's
    outline (never overlaid), units by label, then a table of met/measured, practice by unit."""
    _, bundle = _parts(fx.goldens()[f"expected-{name}.html"])
    shape = _shape(_root_page(bundle))
    keys = sorted(u["key"] for u in bundle["views"][0]["units"])
    charts = re.findall(r'<svg class="radar"[^>]*data-unit="([^"]+)">(.*?)</svg>', shape, re.S)
    assert [k for k, _ in charts] == keys
    for key, svg in charts:
        assert set(re.findall(r"<title>([^,<]+), ", svg)) == {key}
        assert [t for t in re.findall(r"<title>(\d+)%</title>", svg)] == ["25", "50", "75", "100"]
    table = re.search(r'<table class="radar-table">(.*?)</table>', shape, re.S)
    assert table is not None
    assert re.findall(r"<th>([^<]+)</th>", table.group(1)) == keys
    titles = [bundle["rows"][i]["title"] for i in bundle["radar"]["axes"]]
    body = table.group(1).split("<tbody>")[1]
    assert re.findall(r'<tr><th class="repo">([^<]+)</th>', body) == titles
    root = next(u for u in bundle["views"][0]["units"] if u["parent"] is None)
    assert f"<td>{root['practice'][0]['met']}/{root['practice'][0]['measured']}</td>" in table.group(1)
    if name != "full":
        for leak in fx.NAMES:
            assert leak not in shape, leak


def test_an_unmeasured_practice_breaks_the_outline_with_no_point_at_zero() -> None:
    """``grp/other`` has no measured Instruction docs repository: its spoke is dashed and labelled
    not measured, it has no point, no closed outline, and the table cell reads not measured."""
    _, bundle = _parts(fx.goldens()["expected-full.html"])
    shape = _shape(_root_page(bundle))
    svg = re.search(r'data-unit="grp/other">(.*?)</svg>', shape, re.S)
    assert svg is not None
    assert '<tspan x="200.0" dy="-11">Instruction docs</tspan><tspan x="200.0" dy="11">(not measured)</tspan>' in svg.group(1)
    assert 'class="spoke gap"' in svg.group(1)
    assert "<polygon" not in svg.group(1)
    assert re.findall(r"<title>(grp/other, [^<]+)</title>", svg.group(1)) == [
        "grp/other, CI gate: 1/1 measured repositories at the top level"]
    assert '<td class="unknown">not measured</td>' in shape
    closed = re.search(r'data-unit="grp/sub">(.*?)</svg>', shape, re.S)
    assert closed is not None and closed.group(1).count("<polygon") == 1


def test_each_point_s_title_names_unit_practice_and_met_of_measured() -> None:
    _, bundle = _parts(fx.goldens()["expected-full.html"])
    shape = _shape(_root_page(bundle))
    assert "<title>grp, Instruction docs: 1/2 measured repositories at the top level</title>" in shape
    assert "<title>grp, CI gate: 2/3 measured repositories at the top level</title>" in shape


def test_a_practice_measured_in_under_a_fifth_of_the_fleet_is_not_a_spoke() -> None:
    from lib.fleet.fleet_page_data import RADAR_MIN_SHARE, page_bundle, radar_axes
    data: dict[str, Any] = dict(fx.run(fx.DATES[-1], fx.VARIANTS["counts-only"]))
    rows = {"docs": 0, "ci": 1}
    assert RADAR_MIN_SHARE == 20
    assert radar_axes(data, rows) == [0, 1]  # docs 2 of 4 measured, ci 3 of 4
    assert radar_axes(data, rows, 60) == [1] and radar_axes(data, rows, 80) == []
    assert radar_axes({"repos": []}, rows) == []
    for repo in data["repos"]:  # leave docs measured in 1 of 4 repositories, then 0 of 4
        for cell in repo["rows"]:
            if cell["row"] == "docs" and repo["key"] != "grp/sub/proj1":
                cell["status"], cell["level"] = "unknown", None
    assert radar_axes(data, rows) == [0, 1]  # 25% still reaches the 20% floor
    for cell in data["repos"][2]["rows"]:
        cell["status"] = "unknown"
    assert radar_axes(data, rows) == [1]
    bundle = dict(page_bundle(data, None, fdf.model()))
    assert bundle["radar"] == {"min_share": 20, "axes": [1]}
    shape = _shape(_root_page(bundle))
    assert "Instruction docs" not in shape and "CI gate" in shape


def test_no_spoke_draws_no_chart_and_says_why() -> None:
    _, bundle = _parts(fx.goldens()["expected-counts-only.html"])
    bundle["radar"]["axes"] = []
    shape = _shape(_root_page(bundle))
    assert '<svg class="radar"' not in shape and "radar-table" not in shape
    assert "No practice is measured in enough of the fleet's repositories to draw." in shape


def _six_spokes(measured: list[int], title: str = "Practice") -> dict[str, Any]:
    """The counts-only bundle cut to six spokes and its root unit's counts set from ``measured``."""
    _, bundle = _parts(fx.goldens()["expected-counts-only.html"])
    bundle["rows"] = [{"title": f"{title} {i}", "top_level": 1} for i in range(6)]
    bundle["radar"]["axes"] = list(range(6))
    root = next(u for u in bundle["views"][0]["units"] if u["parent"] is None)
    root["practice"] = [{"row": i, "met": 0, "measured": m} for i, m in enumerate(measured)]
    return bundle


def _points(svg: str) -> list[tuple[float, float]]:
    return [(float(x), float(y)) for x, y in re.findall(r'class="point" cx="([^"]+)" cy="([^"]+)"', svg)]


def test_unmeasured_spokes_split_the_outline_into_runs_that_never_cross_a_gap() -> None:
    """Spokes 2 and 5 of six unmeasured: two runs, {0, 1} and {3, 4}, each joining adjacent measured
    spokes only. A measured practice with nothing met is a real 0, drawn at the centre."""
    bundle = _six_spokes([2, 2, 0, 2, 2, 0])
    root = next(u for u in bundle["views"][0]["units"] if u["parent"] is None)
    root["practice"] = [{"row": i, "met": [2, 1, 0, 0, 2, 0][i], "measured": m}
                        for i, m in enumerate([2, 2, 0, 2, 2, 0])]
    svg = re.search(r'data-unit="org 1">(.*?)</svg>', _shape(_root_page(bundle)), re.S)
    assert svg is not None
    runs = re.findall(r'<polyline class="outline" points="([^"]+)"></polyline>', svg.group(1))
    assert "<polygon" not in svg.group(1) and len(runs) == 2
    points = _points(svg.group(1))
    assert len(points) == 4  # the two gap spokes have none
    assert [tuple(float(v) for v in xy.split(",")) for run in runs for xy in run.split()] == [
        points[2], points[3], points[0], points[1]]  # the run after the first gap, then the wrap to spoke 0
    assert (200.0, 150.0) in points  # spoke 3: 0 of 2 met, a measured 0
    assert svg.group(1).count('class="spoke gap"') == 2


def test_long_practice_titles_wrap_inside_the_chart() -> None:
    bundle = _six_spokes([1] * 6, "Cross-agent instruction consistency and coverage gates")
    svg = re.search(r'data-unit="org 1">(.*?)</svg>', _shape(_root_page(bundle)), re.S)
    assert svg is not None
    lines = re.findall(r"<tspan[^>]*>([^<]+)</tspan>", svg.group(1))
    assert lines and max(len(line) for line in lines) <= 16
    assert " ".join(lines[:4]) == "Cross-agent instruction consistency and coverage gates 0"
    for x, anchor, first in re.findall(r'<text x="([^"]+)" y="[^"]+" text-anchor="(\w+)">(.*?)</text>', svg.group(1)):
        width = 6 * max(len(t) for t in re.findall(r">([^<]+)</tspan>", first))
        left = float(x) - (width if anchor == "end" else width / 2 if anchor == "middle" else 0)
        assert 0 <= left and left + width <= 400, (x, anchor)


def test_eighteen_spokes_are_numbered_titled_and_keyed_to_the_table() -> None:
    """The 18 team rows of ``default.toml``: each spoke is labelled by its number, the full title is
    in the spoke's ``<title>``, the table lists the practices under the same numbers, and no two
    labels' boxes (6 units a character, 11 a line) overlap."""
    _, bundle = _parts(fx.goldens()["expected-counts-only.html"])
    bundle["rows"] = [{"title": f"Cross-agent practice number {i}", "top_level": 1} for i in range(18)]
    bundle["radar"]["axes"] = list(range(18))
    root = next(u for u in bundle["views"][0]["units"] if u["parent"] is None)
    root["practice"] = [{"row": i, "met": 1, "measured": 0 if i == 4 else 2} for i in range(18)]
    shape = _shape(_root_page(bundle))
    svg = re.search(r'data-unit="org 1">(.*?)</svg>', shape, re.S)
    assert svg is not None
    numbers = [str(i) for i in range(1, 19)]
    labels = re.findall(r'<text x="([^"]+)" y="([^"]+)" text-anchor="(\w+)"[^>]*><tspan[^>]*>([^<]+)</tspan></text>',
                        svg.group(1))
    assert [t for *_, t in labels] == numbers
    assert re.findall(r'<line class="spoke[^"]*"[^>]*><title>([^<]+)</title></line>', svg.group(1)) == [
        f"{i + 1}. Cross-agent practice number {i}" + (" (not measured)" if i == 4 else "") for i in range(18)]
    boxes = []
    for x, y, anchor, text in labels:
        width = 6 * len(text)
        left = float(x) - (width if anchor == "end" else width / 2 if anchor == "middle" else 0)
        boxes.append((left, float(y) - 10, left + width, float(y) + 1))
    for i, a in enumerate(boxes):
        for b in boxes[i + 1:]:
            assert a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1], (a, b)
    table = re.search(r'<table class="radar-table">(.*?)</table>', shape, re.S)
    assert table is not None
    assert re.findall(r'<tr><th class="repo">([^<]+)</th>', table.group(1).split("<tbody>")[1]) == [
        f"{i + 1}. Cross-agent practice number {i}" for i in range(18)]
    assert "labelled by its number" in shape


def test_eight_spokes_keep_their_titles_beside_the_spoke() -> None:
    _, bundle = _parts(fx.goldens()["expected-counts-only.html"])
    bundle["rows"] = [{"title": f"Practice {i}", "top_level": 1} for i in range(8)]
    bundle["radar"]["axes"] = list(range(8))
    shape = _shape(_root_page(bundle))
    assert "<tspan" in shape and ">Practice 7</tspan>" in shape and "</title></line>" not in shape
    assert '<th class="repo">Practice 0</th>' in shape and "labelled by its number" not in shape
