"""The ``/fleet`` publish step (#949): the plan a session sends, and the page's database source.

Nothing here publishes or calls a network. The page's source runs under Node (``node:vm``) against
a fake ``db`` and ``user`` namespace, so target entry, the one ``targets`` write and the handling
of viewer-written rows are tested as the page would run them.
"""
from __future__ import annotations

import importlib.util
import json
import re
import shutil
import subprocess
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

import fleet_page_fixture as fx
import lib.fleet.fleet_publish_session as ps
from lib.fleet.fleet_data import dumps_fleet_data
from lib.fleet.fleet_page_live import HASHED_NOTE, TARGET_ID_RE, live_bundle, live_page
from lib.fleet.fleet_publish import FullRunRefused, PublishError, RepublishConflict, doc_id
from lib.fleet.fleet_targets_import import target_doc_id

NODE = shutil.which("node")
SCRIPT = Path(__file__).resolve().parents[2] / "fleet" / "scripts" / "fleet_publish_plan.py"


def _cli() -> ModuleType:
    spec = importlib.util.spec_from_file_location("fleet_publish_plan_cli", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _data(name: str) -> dict[str, Any]:
    return dict(fx.run(fx.DATES[-1], fx.VARIANTS[name]))


def _page(name: str) -> str:
    return fx.goldens()[f"expected-{name}.html"]


def _bundle_of(html: str) -> dict[str, Any]:
    found = re.search(r'<script type="application/json" id="fleet-bundle">(.*?)</script\s*>', html, re.S | re.I)
    assert found is not None
    out: dict[str, Any] = json.loads(found.group(1))
    return out


def _apply(batches: list[list[ps.PinnedWrite]], db: dict[str, dict[str, Any]]) -> None:
    """A fake ArtifactData: applies pinned batches, refusing an unpinned write to an existing doc."""
    for batch in batches:
        for w in batch:
            name = f"{w['collection']}/{w['doc_id']}"
            held = db.get(name)
            if held is not None:
                assert w.get("if_version") == held["version"], name
            if w["op"] == "set" or held is None:
                assert w["op"] == "set", name  # update needs the document to exist
                db[name] = {"version": (held or {"version": 0})["version"] + 1, "data": json.loads(json.dumps(w["data"]))}
            else:
                merged = held["data"]
                for k, v in w["data"]["history"].items():  # type: ignore[union-attr]
                    merged.setdefault("history", {})[k] = v
                db[name] = {"version": held["version"] + 1, "data": merged}


def _exports(db: dict[str, dict[str, Any]]) -> dict[str, list[ps.StoredDoc]]:
    out: dict[str, list[ps.StoredDoc]] = {c: [] for c in ps.STORED_COLLECTIONS}
    for name, doc in sorted(db.items()):
        collection, _, key = name.partition("/")
        out[collection].append({"doc_id": key, "version": doc["version"], "data": doc["data"]})
    return out


# --- mode -----------------------------------------------------------------------------------


def test_full_is_refused_plain_counts_only_needs_the_opt_in_and_hashed_is_the_default() -> None:
    with pytest.raises(FullRunRefused):
        ps.check_mode(_data("full"), allow_names=True)
    with pytest.raises(PublishError, match="--allow-names"):
        ps.check_mode(_data("counts-only"), allow_names=False)
    assert ps.check_mode(_data("hashed"), allow_names=False) == "hashed"
    assert ps.check_mode(_data("counts-only"), allow_names=True) == "counts_only"
    assert ps.targets_enabled("counts_only") and not ps.targets_enabled("hashed")


def test_the_capabilities_declare_db_only_with_the_option_b_root() -> None:
    caps = ps.capabilities()
    assert list(caps) == ["db"]
    rules = caps["db"]["rules"]  # type: ignore[index]
    assert rules[0] == {"path": "", "read": "view", "write": "interact"}
    assert {r["path"]: r["write"] for r in rules[1:]} == {"runs": "owner", "units": "owner", "repos": "owner",
                                                         "targets": "interact"}
    assert ps.CONTRACT == "0.2.96" and ps.LIMITS_PINNED.documents == 25_000


# --- stored state, pins and republish -------------------------------------------------------


def test_a_second_publish_pins_every_existing_document_and_a_same_bundle_republish_is_allowed() -> None:
    db: dict[str, dict[str, Any]] = {}
    first, batches = ps.plan_publish(_data("hashed"), _exports(db), run_id="2026-11-13")
    assert all("if_version" not in w for b in batches for w in b)
    _apply(batches, db)
    _, again = ps.plan_publish(_data("hashed"), _exports(db), run_id="2026-11-13")
    assert all(w["if_version"] == db[f"{w['collection']}/{w['doc_id']}"]["version"] for b in again for w in b)
    _apply(again, db)
    _, later = ps.plan_publish(_data("hashed"), _exports(db), run_id="2026-11-20")
    ops = {w["op"] for b in later for w in b if w["collection"] != "runs"}
    assert ops == {"update"} and first["mode"] == "hashed" and first["targets_enabled"] is False


def test_a_changed_republish_of_one_run_id_is_refused() -> None:
    db: dict[str, dict[str, Any]] = {}
    _, batches = ps.plan_publish(_data("hashed"), _exports(db), run_id="2026-11-20")
    _apply(batches, db)
    changed = _data("hashed")
    changed["repos"] = changed["repos"][:-1]
    with pytest.raises(RepublishConflict):
        ps.plan_publish(changed, _exports(db), run_id="2026-11-20")


def test_a_malformed_export_row_names_its_index_never_its_content() -> None:
    with pytest.raises(PublishError, match="row 1 ") as err:
        ps.read_export([{"doc_id": "a", "version": 1, "data": {}}, {"doc_id": "<b>secret", "version": "1"}], "units")
    assert "secret" not in str(err.value)
    with pytest.raises(PublishError):
        ps.read_export({"not": "a list"}, "runs")


# --- the live page ------------------------------------------------------------------------


def test_a_hashed_page_carries_no_target_id_and_says_why() -> None:
    html = live_page(_page("hashed"), _data("hashed"), "hashed", "2026-11-20")
    bundle = _bundle_of(html)
    assert '"target_id"' not in json.dumps(bundle) and bundle["target_note"] == HASHED_NOTE
    assert all(u["doc_id"] == doc_id(u["key"]) for v in bundle["views"] for u in v["units"])  # a hash is its own id
    for leak in fx.NAMES:
        assert leak not in html, leak


def test_under_allow_names_each_unit_carries_its_target_id() -> None:
    data = _data("counts-only")
    bundle = live_bundle(_bundle_of(_page("counts-only")), data, "counts_only", "2026-11-20")
    keys = sorted(u["key"] for v in data["views"] for u in v["units"])
    ids = sorted(u["target_id"] for v in bundle["views"] for u in v["units"])
    assert ids == sorted(target_doc_id(k.split("/", 1)[0], k) for k in keys)
    assert all(re.fullmatch(TARGET_ID_RE, i) for i in ids) and bundle["target_note"] is None
    assert bundle["run_id"] == "2026-11-20"


def test_the_live_page_is_byte_identical_across_builds() -> None:
    assert live_page(_page("hashed"), _data("hashed"), "hashed", "r") == \
        live_page(_page("hashed"), _data("hashed"), "hashed", "r")


def test_a_page_without_a_bundle_is_refused() -> None:
    with pytest.raises(PublishError):
        live_page("<html></html>", _data("hashed"), "hashed", "r")


# --- the page's database source, under Node -------------------------------------------------

HARNESS = r"""
const vm = require("node:vm"); const fs = require("node:fs");
const input = JSON.parse(fs.readFileSync(0, "utf8"));
const writes = [];
const rows = input.rows;
function collection(name) {
  return {
    doc: function (id) { return {
      set: function (d) { writes.push(["set", name, id, d]); return Promise.resolve(); },
      delete: function () { writes.push(["delete", name, id]); return Promise.resolve(); },
      get: function () { return Promise.resolve({exists: false, data: function () { return null; }}); } }; },
    onSnapshot: function (next, err) {
      if (input.snapError) { err({code: "unavailable"}); return function () {}; }
      next({docs: rows.map(function (r) { return {id: r.id, data: function () { return r.data; }}; })}); return function () {}; }
  };
}
const db = input.db ? {collection: collection} : null;
const user = {can: function () { return Promise.resolve(input.can); }};
const app = {innerHTML: "", addEventListener: function () {}, querySelector: function () { return {value: "3"}; }};
const ctx = {setTimeout: setTimeout, clearTimeout: clearTimeout, Promise: Promise, JSON: JSON, Math: Math, Object: Object,
  document: {getElementById: function (id) { return id === "app" ? app : {textContent: input.bundle}; }},
  claude: {use: function (n) { return Promise.resolve(n === "db" ? db : n === "user" ? user : null); }}};
ctx.window = ctx;
vm.createContext(ctx);
for (const s of input.scripts) { vm.runInContext(s, ctx); }
setTimeout(async function () {
  const src = ctx.FleetViewSource;
  const out = {page: app.innerHTML, canSet: await src.canSetTarget()};
  if (input.set) {
    try { await src.setTarget(input.set[0], input.set[1], input.set[2]); out.setOk = true; } catch (e) { out.setOk = false; }
  }
  out.writes = writes;
  process.stdout.write(JSON.stringify(out));
  process.exit(0);
}, 50);
"""


def _run(mode: str, *, db: bool = True, can: object = True, rows: list[dict[str, Any]] | None = None,
         target: tuple[str, str, object] | None = None, snap_error: bool = False) -> dict[str, Any]:
    if NODE is None:
        pytest.skip("node is not on PATH")
    name = "hashed" if mode == "hashed" else "counts-only"
    html = live_page(_page(name), _data(name), mode, "2026-11-20")
    bundle = re.search(r'id="fleet-bundle">(.*?)</script\s*>', html, re.S | re.I)
    assert bundle is not None
    scripts = re.findall(r"<script>(.*?)</script\s*>", html, re.S | re.I)
    payload = {"bundle": bundle.group(1).replace("\\u003c", "<"), "scripts": scripts, "db": db, "can": can,
               "rows": rows or [], "set": list(target) if target else None, "snapError": snap_error}
    done = subprocess.run([NODE, "-e", HARNESS], input=json.dumps(payload), capture_output=True, text=True,
                          encoding="utf-8", errors="replace", check=True, timeout=60)
    out: dict[str, Any] = json.loads(done.stdout)
    return out


def _root(mode: str) -> str:
    name = "hashed" if mode == "hashed" else "counts-only"
    bundle = _bundle_of(_page(name))
    return next(u["key"] for u in bundle["views"][0]["units"] if u["parent"] is None)


def test_a_writer_sees_target_entry_and_setting_a_target_writes_one_targets_document() -> None:
    root = _root("counts_only")
    out = _run("counts_only", target=("ownership", root, 3))
    assert out["canSet"] is True and "set-target" in out["page"]
    assert out["setOk"] is True and out["writes"] == [["set", "targets", target_doc_id("ownership", "ownership/grp"),
                                                       {"step": 3}]]


def test_clearing_a_target_deletes_its_document() -> None:
    out = _run("counts_only", target=("ownership", _root("counts_only"), None))
    assert out["writes"] == [["delete", "targets", target_doc_id("ownership", "ownership/grp")]]


@pytest.mark.parametrize("can", [False, None])
def test_a_viewer_without_write_access_sees_no_target_entry(can: object) -> None:
    out = _run("counts_only", can=can)
    assert out["canSet"] is False and "set-target" not in out["page"]


def test_without_a_database_the_page_falls_back_to_its_embedded_data() -> None:
    out = _run("counts_only", db=False, target=("ownership", _root("counts_only"), 3))
    assert out["canSet"] is False and "set-target" not in out["page"] and out["setOk"] is False
    assert "Since last run" in out["page"] and out["writes"] == []


def test_a_hashed_page_offers_no_entry_and_says_why() -> None:
    out = _run("hashed", target=("ownership", _root("hashed"), 3))
    assert out["canSet"] is False and "set-target" not in out["page"] and out["setOk"] is False
    assert "hashed names" in out["page"] and out["writes"] == []


def test_page_input_cannot_widen_what_is_written() -> None:
    for bad in [("ownership", "no such unit", 3), ("system", _root("counts_only"), 3),
                ("ownership", _root("counts_only"), 9), ("ownership", _root("counts_only"), 2.5),
                ("ownership", _root("counts_only"), "3")]:
        out = _run("counts_only", target=bad)
        assert out["setOk"] is False and out["writes"] == [], bad


def test_viewer_written_rows_are_read_only_when_they_match_and_never_as_markup() -> None:
    own = target_doc_id("ownership", "ownership/grp")
    rows = [{"id": own, "data": {"step": 2}},
            {"id": "ownership~other", "data": {"step": 3}},
            {"id": own + "x", "data": {"step": "<img src=x onerror=alert(1)>"}},
            {"id": target_doc_id("ownership", "ownership/grp/sub"), "data": {"step": 7}}]
    out = _run("counts_only", rows=rows)
    assert "A target of Step 2 · Docs was set on this page." in out["page"]
    assert "onerror" not in out["page"] and "Step 7" not in out["page"]


# --- the CLI --------------------------------------------------------------------------------


def _out(tmp_path: Path, name: str) -> Path:
    out = tmp_path / name
    out.mkdir()
    (out / "fleet-data.json").write_text(dumps_fleet_data(fx.run(fx.DATES[-1], fx.VARIANTS[name])), encoding="utf-8")
    (out / "fleet-view.html").write_text(_page(name), encoding="utf-8")
    return out


def test_plan_writes_the_page_capabilities_and_pinned_batches(tmp_path: Path) -> None:
    out = _out(tmp_path, "hashed")
    exports = tmp_path / "exports"
    exports.mkdir()
    assert _cli().main(["plan", str(out), "--exports", str(exports)]) == 0
    folder = out / "publish"
    summary = json.loads((folder / "plan.json").read_text(encoding="utf-8"))
    assert summary["mode"] == "hashed" and summary["contract"] == "0.2.96" and summary["batches"] == ["batch-001.json"]
    assert json.loads((folder / "capabilities.json").read_text(encoding="utf-8")) == ps.capabilities()
    html = (folder / "page.html").read_text(encoding="utf-8")
    assert "FleetViewSource" in html and '"target_id"' not in json.dumps(_bundle_of(html))
    first = (folder / "batch-001.json").read_bytes()
    assert _cli().main(["plan", str(out), "--exports", str(exports)]) == 0
    assert (folder / "batch-001.json").read_bytes() == first


def test_plan_refuses_plain_counts_only_without_the_opt_in_and_full_always(tmp_path: Path,
                                                                         capsys: pytest.CaptureFixture[str]) -> None:
    exports = tmp_path / "exports"
    exports.mkdir()
    assert _cli().main(["plan", str(_out(tmp_path, "counts-only")), "--exports", str(exports)]) == 2
    assert "--allow-names" in capsys.readouterr().err
    assert _cli().main(["plan", str(tmp_path / "counts-only"), "--exports", str(exports), "--allow-names"]) == 0
    page = (tmp_path / "counts-only" / "publish" / "page.html").read_text(encoding="utf-8")
    assert '"target_id"' in json.dumps(_bundle_of(page))
    assert _cli().main(["plan", str(_out(tmp_path, "full")), "--exports", str(exports), "--allow-names"]) == 2


def test_record_keeps_one_artifact_url_per_org(tmp_path: Path) -> None:
    org = tmp_path / "history" / "grp"
    assert _cli().main(["record", str(org), "--url", "https://claude.ai/artifact/abc", "--run-id", "2026-11-13"]) == 0
    assert _cli().main(["record", str(org), "--url", "https://claude.ai/artifact/abc", "--run-id", "2026-11-20"]) == 0
    assert json.loads((org / ps.REGISTRY_FILE).read_text(encoding="utf-8")) == {
        "url": "https://claude.ai/artifact/abc", "runs": ["2026-11-13", "2026-11-20"]}
    assert ps.recorded_url(org) == "https://claude.ai/artifact/abc"
    assert _cli().main(["record", str(org), "--url", "https://claude.ai/artifact/other", "--run-id", "x"]) == 2
    assert _cli().main(["record", str(org), "--url", "http://example.com/a", "--run-id", "x"]) == 2
    assert ps.recorded_url(tmp_path / "none") is None


def test_strays_outside_the_fleet_collections_are_reported_and_counted(tmp_path: Path,
                                                                     capsys: pytest.CaptureFixture[str]) -> None:
    """AR-148: the root lets Contributors write, so the guard reports documents the fleet did not write."""
    own = target_doc_id("ownership", "ownership/grp")
    exports: dict[str, list[ps.StoredDoc]] = {
        "targets": [{"doc_id": own, "version": 1, "data": {"step": 3}},
                    {"doc_id": "junk", "version": 1, "data": {}}],
        "notes": [{"doc_id": "a", "version": 1, "data": {}}, {"doc_id": "b", "version": 2, "data": {}}]}
    assert ps.strays(exports) == {"count": 3, "collections": ["notes", "targets"], "targets": 1}
    clean, _ = ps.plan_publish(_data("hashed"), {}, run_id="r")
    summary, _ = ps.plan_publish(_data("hashed"), exports, run_id="r")
    assert summary["strays"]["count"] == 3
    assert summary["size"]["documents"] == clean["size"]["documents"] + 4  # three strays and one target
    assert ps.strays({}) == {"count": 0, "collections": [], "targets": 0}
    out = _out(tmp_path, "hashed")
    folder = tmp_path / "exports"
    folder.mkdir()
    (folder / "notes.json").write_text(json.dumps(exports["notes"]), encoding="utf-8")
    assert _cli().main(["plan", str(out), "--exports", str(folder)]) == 0
    assert "2 document(s) outside what the fleet writes, in notes" in capsys.readouterr().err


def test_a_refused_targets_read_says_so_instead_of_looking_like_no_targets() -> None:
    out = _run("counts_only", snap_error=True)
    assert "targets could not be read" in out["page"]
    assert "could not be reached yet" not in _run("counts_only")["page"]


def test_a_pending_target_with_no_run_read_target_renders() -> None:
    sub = target_doc_id("ownership", "ownership/grp/sub")
    out = _run("counts_only", rows=[{"id": sub, "data": {"step": 2}}])
    assert "Your target" in out["page"]  # the root page still draws with a pending target elsewhere
    if NODE is None:
        pytest.skip("node is not on PATH")
    script, bundle = _parts_live()
    runner = """
const vm = require("node:vm"); const fs = require("node:fs");
const input = JSON.parse(fs.readFileSync(0, "utf8")); const ctx = {}; vm.createContext(ctx);
vm.runInContext(input.script, ctx); const b = input.bundle; const u = b.views[0].units[1];
process.stdout.write(ctx.FleetView.renderPage(b, {view: "ownership", unit: u.key, repo: null},
  [{view: "ownership", unit: u.key, step: 2, pending: true}], false));
"""
    done = subprocess.run([NODE, "-e", runner], input=json.dumps({"script": script, "bundle": bundle}),
                          capture_output=True, text=True, encoding="utf-8", errors="replace", check=True)
    assert "was set on this page" in done.stdout and "No Step target is set" not in done.stdout


def _parts_live() -> tuple[str, dict[str, Any]]:
    html = _page("counts-only")
    script = re.findall(r"<script>(.*?)</script\s*>", html, re.S | re.I)[0]
    return script, _bundle_of(html)


def test_an_oversized_plan_writes_no_batch(tmp_path: Path, capsys: pytest.CaptureFixture[str],
                                          monkeypatch: pytest.MonkeyPatch) -> None:
    out = _out(tmp_path, "hashed")
    exports = tmp_path / "exports"
    exports.mkdir()
    assert _cli().main(["plan", str(out), "--exports", str(exports)]) == 0
    assert (out / "publish" / "batch-001.json").is_file()
    monkeypatch.setattr(ps, "LIMITS_PINNED", ps.LIMITS_PINNED._replace(documents=1))
    monkeypatch.setattr(ps.plan_publish, "__kwdefaults__", {"allow_names": False, "limits": ps.LIMITS_PINNED})
    assert _cli().main(["plan", str(out), "--exports", str(exports)]) == 2
    assert not list((out / "publish").glob("batch-*.json"))
    assert "nothing was planned" in capsys.readouterr().err
