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
from lib.diagram_tooltip import HELPER, helper_code
from lib.fleet.fleet_page import PAGE_FILE, build_page, clear_page, embed, write_page
from lib.fleet.fleet_page_data import COUNTS_ONLY, FULL, HASHED, mode_of, picture, unique_scopes
from lib.svg_inline import css_selectors, root_class
from lib.vocabulary import banned_hits
from test_fleet_cli import fleet_cli, froot  # noqa: F401 - froot is a fixture

NODE = shutil.which("node")
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
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


DRILL = [{"unit": "grp"}, {"unit": "grp/sub"}, {"unit": "grp/sub", "repo": "grp/sub/proj2"},
         {"unit": "grp/sub", "repo": "grp/sub/proj2", "tag": "docs"},
         {"unit": "grp/sub", "repo": "grp/sub/proj2", "tag": "docs", "cond": {"row": 0, "level": 2}}]


def _drill(name: str, states: list[dict[str, Any]]) -> list[str]:
    """Each drill state's Drill-down section of the ``name`` golden, drawn by the shipped script under Node."""
    if NODE is None:
        pytest.skip("node is not on PATH")
    script, bundle = _parts(fx.goldens()[f"expected-{name}.html"])
    runner = RUNNER.split("const out")[0] + ("process.stdout.write(JSON.stringify(input.states.map(function (s) {"
                                             " return api.SECTIONS[5](b, Object.assign({view: \"ownership\"}, s)); })));")
    done = subprocess.run([NODE, "-e", runner], input=json.dumps({"script": script, "bundle": bundle, "states": states}),
                          capture_output=True, text=True, encoding="utf-8", errors="replace", check=True)
    pages: list[str] = json.loads(done.stdout)
    return pages


def test_the_drill_runs_org_unit_repository_tag_condition_and_stays_byte_identical() -> None:
    """#1625: from the org, a unit, a repository, its Docs tag (met and unmet counts, each condition
    with its state and the next Step's needs), then one condition's state, reading and needs."""
    org, unit, repo, tag, cond = _drill("full", DRILL)
    assert 'data-unit="grp/sub"' in org and 'data-repo="grp/sub/proj2"' in unit and "data-tag" not in unit
    assert 'data-tag="docs">Docs</button> 0 met, 0 unmet' in repo and 'data-tag="governance"' in repo
    assert "Docs</strong> on grp/sub/proj2" in tag and 'data-row="0" data-level="2"' in tag
    text = _text(tag)
    assert "Instruction docs, level 1 : held, the practice reads level 1 of 2" in text
    assert "Instruction docs, level 2 : not held yet, the practice reads level 1 of 2" in text
    assert "Step 3 · Docs and CI needs: ci" in text
    assert "<strong>Instruction docs, level 2</strong>: not held yet, the practice reads level 1 of 2" in cond
    assert "reads docs.grade (measured C)" in cond and "Step 3 · Docs and CI needs: ci" in cond
    assert 'data-tag="docs">Docs</button>' in cond.split("</nav>")[0]  # the tag crumb links back up
    assert _drill("full", DRILL) == [org, unit, repo, tag, cond] and fx.goldens() == fx.goldens()
    _, full = _parts(fx.goldens()["expected-full.html"])
    assert all(c["unmet"] == c["measured"] - c["met"] for r in full["repos"] for c in r["tags"] or [])
    assert next(r for r in full["repos"] if r["key"] == "grp/proj4")["tags"] is None  # a failed scan: not measured
    assert "Truth tags: not measured" in _drill("full", [{"unit": "grp", "repo": "grp/proj4"}])[0]
    for name in ("counts-only", "hashed"):  # every tier names no repository, unit, URL or person
        _, bundle = _parts(fx.goldens()[f"expected-{name}.html"])
        r = next(x for x in bundle["repos"] if x["tags"])
        tiers = "".join(_drill(name, [{"unit": r["units"]["ownership"], "repo": r["key"], "tag": "docs",
                                        "cond": {"row": 0, "level": 1}}]))
        assert "Instruction docs, level 1" in tiers and not any(leak in tiers for leak in fx.NAMES)


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


def _root_page(bundle: dict[str, Any]) -> str:
    """The page on the ownership root of ``bundle``, drawn by the shipped script under Node."""
    if NODE is None:
        pytest.skip("node is not on PATH")
    script, _ = _parts(fx.goldens()["expected-full.html"])
    runner = (RUNNER.split("const out")[0] + "const r = b.views[0].units.filter(function (u) { return !u.parent; })[0];"
              "process.stdout.write(api.renderPage(b, {view: \"ownership\", unit: r.key, repo: null}, b.targets, false));")
    return subprocess.run([NODE, "-e", runner], input=json.dumps({"script": script, "bundle": bundle}),
                          capture_output=True, text=True, encoding="utf-8", errors="replace", check=True).stdout


# --- inlined diagrams (#1362, parts 1 and 2 of #1313) ------------------------------------

# Each embedded picture -> its generator's golden (tests/fixtures), as fleet.py draws it.
DRAWN = {"tag-profile.svg": "fleet_diagrams/expected-tag-profile.svg",
         "tag-heatmap.svg": "fleet_diagrams/expected-tag-heatmap.svg",
         "step-funnel.svg": "fleet_step_diagrams/expected-step-funnel.svg",
         "one-change-away.svg": "fleet_step_diagrams/expected-one-change-away.svg",
         "step-movement.svg": "fleet_step_diagrams/expected-step-movement.svg"}
HATCHED = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><title>T</title>'
           '<style>.f-bg {fill:#fff} rect.cell {stroke:#000}</style>'
           '<defs><pattern id="fleet-hatch" width="4" height="4"><path d="M0 0L4 4"/></pattern></defs>'
           '<rect class="cell" fill="url(#fleet-hatch)" width="5" height="5"><title>{part}</title></rect></svg>')
HOSTILE = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10" onload="alert(1)"><title>T</title>'
           '<script>alert(2)</script><foreignObject><div>x</div></foreignObject>'
           '<a href="javascript:alert(3)"><rect onclick="alert(4)" width="5" height="5"><title>part</title></rect></a></svg>')


def _page_pictures(tmp_path: Path, drawn: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    data = fx.run(fx.DATES[-1], fx.VARIANTS["counts-only"])
    path = write_page(tmp_path, dict(data), None, fdf.model(), drawn)
    assert path is not None
    html = path.read_text(encoding="utf-8")
    pictures: list[dict[str, Any]] = _parts(html)[1]["pictures"]
    return html, pictures


def _ids(svg: str) -> list[str]:
    return re.findall(r'\sid="([^"]+)"', svg)


def test_the_page_inlines_every_diagram_with_details_and_keeps_the_detail_free_one_an_image(tmp_path: Path) -> None:
    drawn = [{"file": f, "title": f, "question": "Q", "svg": (FIXTURES_DIR / g).read_text(encoding="utf-8")}
             for f, g in DRAWN.items()]
    _, pictures = _page_pictures(tmp_path, drawn)
    inlined = {p["file"]: p["svg"] for p in pictures}
    assert [f for f, svg in inlined.items() if svg is None] == ["step-movement.svg"]
    for file, svg in inlined.items():
        assert next(p for p in pictures if p["file"] == file)["src"].startswith("data:image/svg+xml;base64,")
        if svg is not None:
            assert svg.startswith("<svg ") and "<?xml" not in svg
    if NODE is not None:
        page = _root_page({**fx.bundle("counts-only"), "pictures": pictures})
        shown = page[page.index("<h2>Fleet pictures</h2>"):]
        assert shown.count('<div class="diagram"><svg ') == 4 and shown.count("<img ") == 1
        assert shown.count('download="') == 5


def test_a_hostile_svg_is_inlined_without_script_handlers_or_javascript_links(tmp_path: Path) -> None:
    _, pictures = _page_pictures(tmp_path, [{"file": "tag-profile.svg", "title": "T", "question": "Q", "svg": HOSTILE}])
    svg = pictures[0]["svg"]
    assert svg is not None and "<rect " in svg and "<a>" in svg
    lowered = svg.lower()
    assert "<script" not in lowered and "alert" not in lowered and "foreignobject" not in lowered
    assert re.search(r"\son\w+=", lowered) is None and "javascript:" not in lowered


def test_two_diagrams_defining_fleet_hatch_get_unique_ids_and_their_own_pattern(tmp_path: Path) -> None:
    drawn = [{"file": f, "title": f, "question": "Q", "svg": HATCHED.replace("{part}", f)}
             for f in ("tag-profile.svg", "tag-heatmap.svg")]
    _, pictures = _page_pictures(tmp_path, drawn)
    svgs = [p["svg"] for p in pictures]
    assert all(svgs)
    ids = [i for svg in svgs for i in _ids(svg)]
    assert len(ids) == len(set(ids)) == 2
    for svg in svgs:
        refs = re.findall(r"url\(#([^)]+)\)", svg)
        assert refs and all(r in _ids(svg) for r in refs)
        scope = root_class(svg)
        assert scope is not None and scope.startswith("fleet-page-")
        found = css_selectors(svg.split("<style>")[1].split("</style>")[0], scope)
        assert found and all(s.startswith(f"svg.{scope} ") for s in found)


def test_every_style_selector_in_an_inlined_svg_starts_with_its_scope(tmp_path: Path) -> None:
    drawn = [{"file": f, "title": f, "question": "Q", "svg": (FIXTURES_DIR / g).read_text(encoding="utf-8")}
             for f, g in DRAWN.items()]
    _, pictures = _page_pictures(tmp_path, drawn)
    scopes = []
    for svg in (p["svg"] for p in pictures if p["svg"]):
        scope = root_class(svg)
        assert scope is not None
        scopes.append(scope)
        selectors = [s for css in re.findall(r"<style>(.*?)</style>", svg, re.S) for s in css_selectors(css, scope) or []]
        assert selectors and all(s.startswith(f"svg.{scope} ") for s in selectors)
    assert len(scopes) == len(set(scopes)) == 4


def test_a_second_svg_under_a_scope_already_on_the_page_stays_an_image() -> None:
    drawn = (FIXTURES_DIR / DRAWN["step-funnel.svg"]).read_text(encoding="utf-8")
    first, second = unique_scopes([picture("a.svg", "A", "Q", drawn), picture("b.svg", "B", "Q", drawn)])
    assert first["svg"] is not None and second["svg"] is None and second["src"] == first["src"]


def test_the_page_clips_each_inlined_diagram_to_its_own_box() -> None:
    assert ".pictures .diagram{overflow:hidden;contain:paint}" in build_page(fx.bundle("full"))


def test_an_svg_scoped_outside_fleet_stays_an_image() -> None:
    radar = ('<svg class="radar" xmlns="http://www.w3.org/2000/svg"><title>r</title>'
             '<style>svg.radar .ring{fill:red}</style><rect width="1"><title>part</title></rect></svg>')
    (only,) = unique_scopes([picture("a.svg", "A", "Q", radar)])
    assert only["svg"] is None and only["src"].startswith("data:image/svg+xml;base64,")


def _scoped(scope: str, body: str = "") -> str:
    return (f'<svg class="{scope}" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 40 40"><title>r</title>'
            f'{body}<rect width="1"><title>part</title></rect></svg>')


@pytest.mark.parametrize("first, second", [("fleet-a", "fleet-a-b"), ("fleet-a-b", "fleet-a")])
def test_a_scope_that_is_a_dash_prefix_of_another_stays_an_image(first: str, second: str) -> None:
    # #1502: fleet-a may define fleet-a-b-grad, which fleet-a-b would read as its own id.
    shadow = _scoped("fleet-a", '<linearGradient id="fleet-a-b-grad"/>')
    pics = [picture("a.svg", "A", "Q", shadow if s == "fleet-a" else _scoped(s)) for s in (first, second)]
    kept, dropped = unique_scopes(pics)
    assert kept["svg"] is not None and root_class(kept["svg"]) == first and dropped["svg"] is None


def test_scopes_that_only_share_letters_are_both_inlined() -> None:
    pics = [picture(f"{s}.svg", "A", "Q", _scoped(s)) for s in ("fleet-a", "fleet-ab", "fleet-b-a")]
    assert all(p["svg"] is not None for p in unique_scopes(pics))


def test_no_two_generator_fleet_scopes_share_a_dash_prefix() -> None:
    # unique_scopes leaves the later of two such scopes an image (#1502), so a new generator scope
    # such as fleet-step would quietly stop fleet-step-funnel inlining: fail here instead.
    from lib.svg_scope import SCOPES
    fleet = sorted(s for s in SCOPES.values() if s.startswith("fleet-"))
    assert fleet and not [(a, b) for a in fleet for b in fleet if b.startswith(f"{a}-")]


# --- the default tooltip helper (#1364, part 3 of #1313) ---------------------------------

LINKED = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><title>T</title>'
          '<a href="https://example.com/doc"><rect width="5" height="5" data-path="docs/a.md">'
          '<title>docs/a.md</title></rect></a></svg>')


@pytest.mark.parametrize("name", sorted(fx.VARIANTS))
def test_a_built_page_includes_the_tooltip_helper_once_inline_with_no_external_script(name: str) -> None:
    html = fx.goldens()[f"expected-{name}.html"]
    assert html.count('<script id="diagram-tooltip">') == 1 and "window.DiagramTooltip" in html
    assert re.search(r"<script[^>]*\ssrc=", html, re.I) is None
    helper = html.split('<script id="diagram-tooltip">', 1)[1].split("</script>", 1)[0]
    assert helper == "\n" + helper_code(HELPER.read_text(encoding="utf-8"))
    assert "</" not in helper and "<!--" not in helper and "src=" not in helper
    assert html.index('<script id="diagram-tooltip">') > html.index('id="fleet-bundle"')


def test_the_inline_helper_cannot_close_its_block_or_carry_a_comment_opener() -> None:
    assert helper_code('/* <script src="x.js"></script> */\nvar s = "</script>";') == 'var s = "<\\/script>";'
    with pytest.raises(ValueError):
        helper_code("var s = '<!--';")


def test_a_linked_part_keeps_its_href_after_inlining(tmp_path: Path) -> None:
    _, pictures = _page_pictures(tmp_path, [{"file": "tag-profile.svg", "title": "T", "question": "Q", "svg": LINKED}])
    svg = pictures[0]["svg"]
    assert svg is not None
    assert '<a href="https://example.com/doc"><rect ' in svg and "<title>docs/a.md</title>" in svg
