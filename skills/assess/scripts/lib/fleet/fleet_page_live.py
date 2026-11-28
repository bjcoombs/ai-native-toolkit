"""The published fleet page: ``fleet-view.html`` with a database data source (issue #949).

``live_page`` takes the static page ``fleet.py`` wrote (#946, ``lib/fleet/fleet_page.py``), adds to
its embedded bundle what the database source needs, and inserts ``LIVE_SCRIPT`` before the page
script, which then renders from ``window.FleetViewSource`` instead of the embedded data alone.

**Added to the bundle** (``live_bundle``), only here, never in the static page:

- ``doc_id`` on each unit and repository: its ``units`` or ``repos`` document id
  (``fleet_publish.doc_id`` of its ``fleet-data.json`` key). A hashed key is its own id, already
  on the page; under ``--allow-names`` the id carries the unit or repository key.
- ``target_id`` on each unit of the ``ownership`` and ``system`` views, **only under
  ``--allow-names``**: the ``targets`` document id of #948's row contract
  (``fleet_targets_import.target_doc_id``). It holds the plain unit key, which is why only a run
  published with names carries it; a hashed page has none and says why (``target_note``), since
  the read-back refuses hashed keys.
- ``run_id``: the published run.

**The source** (``LIVE_SCRIPT``), following the ``db`` and ``user`` capabilities of runtime
contract 0.2.96 (``claude.use``; ``null`` means absent):

- ``load`` resolves the embedded bundle at once: the page draws without the database.
- ``subscribeTargets`` reports the bundle's targets, then subscribes once to the ``targets``
  collection and adds each valid row as a ``pending`` target. A row is viewer-written: it is
  read only when its id is one of this page's ``target_id`` values and its ``step`` is a whole
  number from 2 to the top Step; the page renders it through ``esc()`` like everything else. A
  10 s timer notes when the database has not answered; a snapshot clears the note, and an error
  replaces it with one saying the targets could not be read, so a refused read never looks like
  no targets.
- ``canSetTarget`` resolves ``true`` only when ``db`` is present, the page carries target ids
  and ``user.can("data.write")`` is ``true``; ``null`` (not told) and ``false`` hide the entry.
- ``setTarget(view, unit, step)`` writes ``{step}`` to ``targets/<target_id>``, or deletes it for
  ``null``. The id comes from the bundle, never from input, and must match ``TARGET_ID_RE``
  (``{view}~`` then id characters, at most 200 in all: the same limit ``target_doc_id`` applies,
  so every id the bundle carries is one the page writes), so page input cannot widen what is written.
- ``history(docId)`` reads ``units/<docId>`` once and returns each published run's lowest Step.

**A shard's page** (#950, ``shard``, a ``fleet_shard.ShardView``): the bundle is cut to the units
and repositories the shard shows, by page label (``shard_bundle``), with the achievements, targets
and Recount lines about them (an achievement or a target by its view and label, since a label is
unique only within a view: a repository's when the shard shows the repository, a unit's when the
shard shows that unit in that view, so another shard's never shows here); the fleet-wide front screen and pictures are left out, since they
describe every shard. It gains ``shard``: the shard's label and top-level unit, the index URL
(the breadcrumb's first link) and, per system unit it shows, the system's repositories in other
shards with each shard's URL. A URL that is not ``https://claude.ai/`` is written as null.

Deterministic: the same page, bundle and mode give the same bytes. `live_page(page, data, mode,
run_id, shard=None)` -> str. Tests: `tests/test_fleet_publish_session.py`.
"""
from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any, Final, cast

from lib.fleet.fleet_achievements import REPO_VIEW
from lib.fleet.fleet_page import embed
from lib.fleet.fleet_page_data import COUNTS_ONLY, PageBundle, labels
from lib.fleet.fleet_publish import PublishError, doc_id
from lib.fleet.fleet_shard import ShardView
from lib.fleet.fleet_shard_index import URL_PREFIX
from lib.fleet.fleet_targets_import import target_doc_id

TARGET_VIEWS: Final = ("ownership", "system")
# One length rule with ``target_doc_id``: the whole id is at most 200 characters (``fleet_publish.MAX_ID``).
TARGET_ID_RE: Final = r"^(?=.{1,200}$)(ownership|system)~[A-Za-z0-9_\-:@+~]+$"
HASHED_NOTE: Final = ("Targets can't be set on this page: it was published from a run with hashed names, "
                      "and the next run reads targets by plain unit names.")
_BUNDLE = re.compile(r'(<script type="application/json" id="fleet-bundle">)(.*?)(</script>)', re.S)

LIVE_SCRIPT = r"""
(function () {
  "use strict";
  var TARGET_ID = new RegExp(%(re)s);
  var SLOW = "The page's targets could not be reached yet.";
  var UNREAD = "The page's targets could not be read, so targets set here may not show.";
  var bundle = JSON.parse(document.getElementById("fleet-bundle").textContent);
  var ids = {}, units = {};
  bundle.views.forEach(function (v) {
    v.units.forEach(function (u) {
      if (u.target_id && TARGET_ID.test(u.target_id)) { ids[u.target_id] = {view: v.view, unit: u.key}; }
      units[v.view + "\n" + u.key] = u;
    });
  });
  var top = Math.max.apply(null, bundle.steps.map(function (s) { return s.step; }).concat([1]));
  function use(name) {
    var c = window.claude;
    if (!c || typeof c.use !== "function") { return Promise.resolve(null); }
    return c.use(name).then(function (x) { return x || null; }, function () { return null; });
  }
  var dbP = use("db");
  function pendingFrom(docs) {
    return docs.map(function (d) {
      var at = ids[d.id], body = d.data ? d.data() : null;
      var step = body ? body.step : null;
      if (!at || typeof step !== "number" || Math.floor(step) !== step || step < 2 || step > top) { return null; }
      return {view: at.view, unit: at.unit, step: step, pending: true};
    }).filter(function (x) { return x !== null; });
  }
  window.FleetViewSource = {
    load: function () { return Promise.resolve(bundle); },
    subscribeTargets: function (cb) {
      cb(bundle.targets);
      var stop = function () {};
      dbP.then(function (db) {
        if (!db || !Object.keys(ids).length) { return; }
        var timer = setTimeout(function () { bundle.target_note = SLOW; cb(bundle.targets); }, 10000);
        stop = db.collection("targets").onSnapshot(function (snap) {
          clearTimeout(timer);
          if (bundle.target_note === SLOW) { bundle.target_note = null; }
          cb(bundle.targets.concat(pendingFrom(snap.docs)));
        }, function () {
          clearTimeout(timer);
          bundle.target_note = UNREAD;
          cb(bundle.targets);
        });
      });
      return function () { stop(); };
    },
    canSetTarget: function () {
      if (!Object.keys(ids).length) { return Promise.resolve(false); }
      return Promise.all([dbP, use("user")]).then(function (got) {
        if (!got[0] || !got[1]) { return false; }
        return Promise.resolve(got[1].can("data.write")).then(function (ok) { return ok === true; }, function () { return false; });
      });
    },
    setTarget: function (view, unit, step) {
      var u = units[view + "\n" + unit];
      var id = u ? u.target_id : null;
      if (!id || !TARGET_ID.test(id)) { return Promise.reject(new Error("no target for this unit")); }
      if (step !== null && (typeof step !== "number" || Math.floor(step) !== step || step < 2 || step > top)) {
        return Promise.reject(new Error("not a Step"));
      }
      return dbP.then(function (db) {
        if (!db) { throw new Error("no database"); }
        var ref = db.collection("targets").doc(id);
        return step === null ? ref.delete() : ref.set({step: step});
      });
    },
    history: function (docId) {
      return dbP.then(function (db) {
        if (!db || typeof docId !== "string") { return []; }
        return db.collection("units").doc(docId).get().then(function (snap) {
          var h = snap.exists && snap.data() ? snap.data().history : null;
          if (!h || typeof h !== "object") { return []; }
          return Object.keys(h).sort().map(function (k) {
            var step = h[k] && h[k].step ? h[k].step.lowest_step : null;
            return {run: k.replace(/~2E/g, "."), step: typeof step === "number" ? step : null};
          });
        }, function () { return []; });
      });
    }
  };
})();
"""


def _key_of(data: Mapping[str, Any], mode: str) -> dict[tuple[str, str], str]:
    """``(view, page label)`` to the ``fleet-data.json`` key, for every unit and repository."""
    label = labels(data, mode)
    out = {("repo", label[r["key"]]): r["key"] for r in data.get("repos") or []}
    for view in data.get("views") or []:
        out.update({(view["view"], label[u["key"]]): u["key"] for u in view.get("units") or []})
    return out


def live_bundle(bundle: dict[str, Any], data: Mapping[str, Any], mode: str, run_id: str) -> dict[str, Any]:
    """``bundle`` with the ids the database source reads (see the module docstring)."""
    page_mode = "hashed" if mode == "hashed" else COUNTS_ONLY
    key_of = _key_of(data, page_mode)
    out: dict[str, Any] = json.loads(json.dumps(bundle))
    for view in out["views"]:
        for unit in view["units"]:
            key = key_of[(view["view"], unit["key"])]
            unit["doc_id"] = doc_id(key)
            if mode == "counts_only" and view["view"] in TARGET_VIEWS:
                try:
                    unit["target_id"] = target_doc_id(view["view"], key)
                except PublishError:
                    pass  # a key too long for an id holds no target (#948)
    for repo in out["repos"]:
        repo["doc_id"] = doc_id(key_of[("repo", repo["key"])])
    out["run_id"] = run_id
    out["target_note"] = None if mode == "counts_only" else HASHED_NOTE
    return out


def shard_bundle(bundle: dict[str, Any], shard: ShardView) -> dict[str, Any]:
    """The page bundle cut to one shard (see the module docstring)."""
    out: dict[str, Any] = json.loads(json.dumps(bundle))
    units = {v: set(labels) for v, labels in shard["units"].items()}
    repos = set(shard["repos"])
    for view in out["views"]:
        view["units"] = [u for u in view["units"] if u["key"] in units.get(view["view"], set())]
    out["repos"] = [r for r in out["repos"] if r["key"] in repos]
    out["achievements"] = [a for a in out["achievements"]
                           if a["unit"] in (repos if a["view"] == REPO_VIEW else units.get(a["view"], set()))]
    out["targets"] = [t for t in out["targets"]
                      if t["unit"] in (repos if t["view"] == REPO_VIEW else units.get(t["view"], set()))]
    if out.get("recount"):
        out["recount"]["lines"] = [line for line in out["recount"]["lines"] if line["repo"] in repos]
    out["front"], out["pictures"] = None, []  # fleet-wide: the shard page shows its own unit only
    index = shard["index_url"]
    out["shard"] = {"unit": shard["unit"], "label": shard["label"],
                    "index_url": index if index and index.startswith(URL_PREFIX) else None,
                    "elsewhere": {k: [{**e, "url": e["url"] if e["url"] and e["url"].startswith(URL_PREFIX) else None}
                                      for e in v] for k, v in shard["elsewhere"].items()}}
    return out


def live_page(page: str, data: Mapping[str, Any], mode: str, run_id: str, shard: ShardView | None = None) -> str:
    """The published page: ``page`` (``fleet-view.html``) with the live bundle and source, cut to
    ``shard`` when the fleet is sharded."""
    found = _BUNDLE.search(page)
    if found is None:
        raise PublishError("fleet-view.html holds no embedded bundle: rerun /fleet with --levels")
    bundle = json.loads(found.group(2))
    if shard is not None:
        bundle = shard_bundle(bundle, shard)
    script = LIVE_SCRIPT % {"re": json.dumps(TARGET_ID_RE)}
    body = found.group(1) + embed(cast(PageBundle, live_bundle(bundle, data, mode, run_id))) + found.group(3)
    return page[:found.start()] + body + f"\n<script>{script}</script>" + page[found.end():]
