"""A sharded fleet's pages (#950): the index, each shard's page and the links between them.

The shard pages are the fixture's live page (``fleet_page_live.live_page``) cut to each shard of
``fleet_shard.shard_plan``; their script runs under Node (``node:vm``, no browser), as in
``test_fleet_page.py``, so the breadcrumb and the cross-shard line are read as a viewer reads them.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from typing import Any

import pytest

import fleet_page_fixture as fx
import lib.fleet.fleet_publish as fp
import lib.fleet.fleet_shard as fs
from lib.fleet.fleet_page_live import live_page, shard_bundle
from lib.fleet.fleet_shard_index import index_page

NODE = shutil.which("node")
SMALL = fp.LIMITS._replace(documents=6)
INDEX = "https://claude.ai/artifact/index-0001"
RUNNER = r"""
const vm = require("node:vm");
const fs = require("node:fs");
const input = JSON.parse(fs.readFileSync(0, "utf8"));
const ctx = {};
vm.createContext(ctx);
vm.runInContext(input.script, ctx);
const b = input.bundle;
const out = {};
b.views.forEach(function (v) {
  v.units.forEach(function (u) { out[v.view + "\n" + u.key] = ctx.FleetView.renderPage(b, {view: v.view, unit: u.key, repo: null}, b.targets, false); });
});
process.stdout.write(JSON.stringify(out));
"""


def _registry(plan: fs.ShardPlan) -> fs.ShardRegistry:
    return {"index": INDEX, "shards": {s["key"]: f"https://claude.ai/artifact/shard-{n}" for n, s in enumerate(plan["shards"])}}


def _sharded(name: str = "counts-only", registry: fs.ShardRegistry | None = None) -> tuple[fs.ShardPlan, dict[str, str]]:
    data: Any = fx.run(fx.DATES[-1], fx.VARIANTS[name])
    plan, _ = fs.shard_plan(data, SMALL, registry=registry)
    if registry is None:
        plan, _ = fs.shard_plan(data, SMALL, registry=_registry(plan))
    mode = "hashed" if name == "hashed" else "counts_only"
    page = fx.goldens()[f"expected-{name}.html"]
    return plan, {s["key"]: live_page(page, data, mode, fx.DATES[-1], s["view"]) for s in plan["shards"]}


def _bundle(html: str) -> dict[str, Any]:
    found = re.search(r'<script type="application/json" id="fleet-bundle">(.*?)</script>', html, re.S)
    assert found is not None
    out: dict[str, Any] = json.loads(found.group(1))
    return out


def _drawn(html: str) -> dict[str, str]:
    if NODE is None:
        pytest.skip("node is not on PATH")
    script = next(s for s in re.findall(r"<script>(.*?)</script\s*>", html, re.S | re.I) if "globalThis.FleetView" in s)
    done = subprocess.run([NODE, "-e", RUNNER], input=json.dumps({"script": script, "bundle": _bundle(html)}),
                          capture_output=True, text=True, encoding="utf-8", errors="replace", check=True)
    out: dict[str, str] = json.loads(done.stdout)
    return out


def test_every_shard_page_links_to_the_index_and_the_index_links_to_every_shard() -> None:
    plan, pages = _sharded()
    for shard in plan["shards"]:
        drawn = _drawn(pages[shard["key"]])
        assert drawn
        for text in drawn.values():  # every unit page: the breadcrumb starts at the index
            crumbs = re.search(r'<nav class="crumbs">(.*?)</nav>', text, re.S)
            assert crumbs is not None and crumbs.group(1).startswith(f'<a href="{INDEX}" target="_blank"')
    assert plan["index"] is not None
    html = index_page(plan["index"])
    for shard in plan["shards"]:
        assert f'<a href="{shard["url"]}" target="_blank" rel="noopener">Open</a>' in html
    assert html.count('rel="noopener"') == len(plan["shards"])


def test_a_shard_page_shows_only_its_own_units_and_repositories() -> None:
    plan, pages = _sharded()
    for shard in plan["shards"]:
        bundle = _bundle(pages[shard["key"]])
        assert sorted(r["key"] for r in bundle["repos"]) == shard["view"]["repos"]
        assert {v["view"]: sorted(u["key"] for u in v["units"]) for v in bundle["views"]} == shard["view"]["units"]
        assert bundle["front"] is None and bundle["pictures"] == []
        assert all(t["unit"] in shard["view"]["units"][t["view"]] for t in bundle["targets"])


def test_the_system_view_spanning_two_shards_shows_the_cross_shard_count_and_link() -> None:
    plan, pages = _sharded()
    sub = next(s for s in plan["shards"] if s["key"] == "ownership/grp/sub")
    other = next(s for s in plan["shards"] if s["key"] == fs.UNASSIGNED)
    text = _drawn(pages[sub["key"]])["system\nsystem 1"]
    assert (f'In other shards: <a href="{other["url"]}" target="_blank" rel="noopener">unassigned repositories</a> '
            "(1 repository).") in text
    back = _drawn(pages[other["key"]])["system\nsystem 1"]
    assert f'<a href="{sub["url"]}" target="_blank" rel="noopener">team 2</a> (1 repository)' in back
    assert "In other shards" not in _drawn(pages[sub["key"]])["ownership\nteam 2"]  # the system view only


def test_a_page_with_no_url_yet_says_so_and_writes_no_link() -> None:
    data: Any = fx.run(fx.DATES[-1], fx.VARIANTS["counts-only"])
    plan, _ = fs.shard_plan(data, SMALL)
    _, pages = _sharded(registry={"index": None, "shards": {}})
    for html in pages.values():
        for text in _drawn(html).values():
            assert "Fleet index <span class=\"muted\">(not published yet)</span>" in text
    assert plan["index"] is not None and "not published yet" in index_page(plan["index"])


def test_a_url_that_is_not_claude_ai_is_never_linked() -> None:
    data: Any = fx.run(fx.DATES[-1], fx.VARIANTS["counts-only"])
    first, _ = fs.shard_plan(data, SMALL)
    bad: fs.ShardRegistry = {"index": "javascript:alert(1)", "shards": {s["key"]: "https://evil.example/x" for s in first["shards"]}}
    plan, pages = _sharded(registry=bad)
    for html in pages.values():
        bundle = _bundle(html)
        assert bundle["shard"]["index_url"] is None
        assert all(e["url"] is None for v in bundle["shard"]["elsewhere"].values() for e in v)
    assert plan["index"] is not None and "evil.example" not in index_page(plan["index"])


@pytest.mark.parametrize("name", ["counts-only", "hashed"])
def test_the_index_holds_no_repository_document_or_name(name: str) -> None:
    plan, pages = _sharded(name)
    assert plan["index"] is not None
    html = index_page(plan["index"], [{"step": 1, "name": "Assisted"}, {"step": 2, "name": "Docs"}])
    assert "repository 1" not in html and "<script" not in html and "Step 2 (Docs)" in html
    for leak in fx.NAMES:
        assert leak not in html, leak
        for page in pages.values() if name == "hashed" else ():  # a plain page's doc ids carry names (#949)
            assert leak not in page, leak


def test_a_shard_page_drills_from_the_index_to_unit_repository_tag_and_condition() -> None:
    """#1625 on a shard (#950): the index links to the shard, whose trail reads index, then its
    units, a repository, a tag and a condition, each tier drawn below the one above it."""
    if NODE is None:
        pytest.skip("node is not on PATH")
    plan, pages = _sharded()
    shard = next(s for s in plan["shards"] if s["key"] == "ownership/grp/sub")
    assert plan["index"] is not None and f'<a href="{shard["url"]}"' in index_page(plan["index"])
    html = pages[shard["key"]]
    bundle = _bundle(html)
    repo = next(r for r in bundle["repos"] if r["tags"])
    unit = repo["units"]["ownership"]
    base = {"view": "ownership", "unit": unit, "repo": repo["key"]}
    states = [base, {**base, "tag": "docs"}, {**base, "tag": "docs", "cond": {"row": 0, "level": 1}}]
    script = next(s for s in re.findall(r"<script>(.*?)</script\s*>", html, re.S | re.I) if "globalThis.FleetView" in s)
    runner = RUNNER.split("const out")[0] + ("process.stdout.write(JSON.stringify(input.states.map(function (s) {"
                                             " return ctx.FleetView.renderPage(b, s, b.targets, false); })));")
    done = subprocess.run([NODE, "-e", runner], input=json.dumps({"script": script, "bundle": bundle, "states": states}),
                          capture_output=True, text=True, encoding="utf-8", errors="replace", check=True)
    navs = [re.search(r'<nav class="crumbs">(.*?)</nav>', p, re.S) for p in json.loads(done.stdout)]
    assert all(n is not None and n.group(1).startswith(f'<a href="{INDEX}"') for n in navs)  # the index first
    trail = [[t for t in re.split(r"<[^>]+>", n.group(1)) if t.strip() and t != "›"] for n in navs if n]
    units = trail[0][1:-1]
    assert units and units[-1] == unit
    assert trail[0] == ["Fleet index", *units, repo["key"]]
    assert trail[1] == ["Fleet index", *units, repo["key"], "Docs"]
    assert trail[2] == ["Fleet index", *units, repo["key"], "Docs", "Instruction docs, level 1"]


def test_an_unassigned_shard_with_no_ownership_unit_still_holds_the_root_and_draws() -> None:
    """#1635 (1): grp/proj4 loses its ownership unit, so no repository of the unassigned shard has
    one; the shard still holds the ownership root, which its page opens on."""
    data: Any = fx.run(fx.DATES[-1], fx.VARIANTS["counts-only"])
    proj4 = next(r for r in data["repos"] if r["key"] == "grp/proj4")
    proj4["units"] = [u for u in proj4["units"] if u["view"] != "ownership"]
    plan, bundles = fs.shard_plan(data, SMALL)
    shard = next(s for s in plan["shards"] if s["key"] == fs.UNASSIGNED)
    assert [u["key"] for v in bundles[fs.UNASSIGNED]["views"] if v["view"] == "ownership" for u in v["units"]] == [
        "ownership/grp"]
    root = shard["view"]["units"]["ownership"]
    assert len(root) == 1
    html = live_page(fx.goldens()["expected-counts-only.html"], data, "counts_only", fx.DATES[-1], shard["view"])
    assert _drawn(html)[f"ownership\n{root[0]}"]


def test_a_shard_page_keeps_only_the_credit_of_its_own_units_by_view() -> None:
    """#1635 (2): a label is unique only within a view, so a system unit's credit whose label matches
    an ownership unit the shard shows, or a unit's credit whose label matches a shown repository,
    belongs to another shard and is left out."""
    bundle: dict[str, Any] = {
        "views": [{"view": "ownership", "units": [{"key": "team 2"}]}, {"view": "system", "units": []}],
        "repos": [{"key": "repository 1"}], "recount": None, "front": None, "pictures": [],
        "targets": [{"view": v, "unit": u, "step": 2} for v, u in (
            ("ownership", "team 2"), ("system", "team 2"), ("repo", "repository 1"), ("repo", "team 2"))],
        "achievements": [{"view": v, "unit": u, "text": f"{v} {u}"} for v, u in (
            ("ownership", "team 2"), ("system", "team 2"), ("repo", "repository 1"), ("repo", "team 2"),
            ("ownership", "repository 1"))]}
    view: fs.ShardView = {"unit": "team 2", "label": "team 2", "index_url": None, "repos": ["repository 1"],
                          "units": {"ownership": ["team 2"], "system": []}, "elsewhere": {}}
    cut = shard_bundle(bundle, view)
    assert [a["text"] for a in cut["achievements"]] == ["ownership team 2", "repo repository 1"]
    # targets follow the same rule: a repository target shows on the shard that shows the repository
    assert [(t["view"], t["unit"]) for t in cut["targets"]] == [("ownership", "team 2"), ("repo", "repository 1")]
