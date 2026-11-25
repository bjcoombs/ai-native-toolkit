"""The inline CSS and JavaScript of ``fleet-view.html`` (#946), kept apart from the page builder.

``STYLE`` is the page's stylesheet: the #743 palette as CSS custom properties, redefined under
``prefers-color-scheme: dark``, a hatched fill for not-measured cells, and a layout that works
at phone width. ``SCRIPT`` renders the page from the embedded bundle (``fleet_page_data``).

The script computes no level, Step or achievement: it filters the bundle to the pinned unit and
words what it finds. Its parts:

- **Data interface.** ``staticSource(bundle)`` returns ``{load, subscribeTargets}``; a page
  that defines ``window.FleetViewSource`` before the script (the live artifact, #949,
  ``lib/fleet/fleet_page_live.py``) supplies its own, and may add ``setTarget(view, unit, step)``,
  ``canSetTarget()`` (a promise; the entry is drawn only when it resolves ``true``) and
  ``history(docId)`` (a promise of ``[{run, step}]``, the unit's published runs). Target entry is
  drawn only when ``setTarget`` is a function, ``canSetTarget`` says yes and the unit carries a
  ``target_id``, so a static page has none. A target the source reports with ``pending: true``
  (set on the page, not yet read by a run) is shown as set, with its progress to come; a
  ``target_note`` in the bundle says why a page offers no entry.
- **Sections.** ``SECTIONS`` lists the section renderers in page order: Fleet at a glance (the
  front screen, #1255, from the bundle's ``front``: Step counts, moves, one change away, gates
  ranked by the repositories each would move, team units by name, each a button that opens it in
  the ownership view), then Practice packs (a ``--pack`` run's per-pack counts, #1276), then
  Since last run, Where this unit stands, Your target, Drill-down,
  Fleet pictures (the #705 design order).
- **Pinned unit.** A per-viewer ``localStorage`` key, read and written inside try/catch; the
  page opens on the ownership root without it.
- **Order.** Units and repositories are listed by label, never by rank.

``renderPage(bundle, state, targets, canSet, extra)`` returns the HTML of the whole app and
``cellTitle(bundle, repo, cell)`` a heatmap cell's hover text, both pure, so a Node test
(``tests/test_fleet_page.py``) runs them on the fixture without a browser.
"""
from __future__ import annotations

STYLE = """
:root{--bg:#ffffff;--fg:#1d2321;--muted:#5d6763;--rule:#d9dedb;--panel:#f6f7f6;--accent:#1f6f6b;
--l0:#ffffe5;--l1:#d9f0a3;--l2:#78c679;--l3:#238443;--lt:#1a1a1a;--ltd:#ffffff;--unk:#9aa39f}
@media (prefers-color-scheme: dark){:root{--bg:#1a201e;--fg:#e6ebe8;--muted:#9aa5a0;--rule:#2c3532;
--panel:#232a28;--accent:#5fb8b1;--unk:#5f6a66}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif}
main{max-width:960px;margin:0 auto;padding:16px}
h1{font-size:22px;margin:8px 0 4px}h2{font-size:18px;margin:28px 0 8px;border-bottom:1px solid var(--rule);padding-bottom:4px}
.muted{color:var(--muted)}.panel{background:var(--panel);border-radius:8px;padding:10px 12px;margin:8px 0}
ul{padding-left:20px;margin:6px 0}
button.link{background:none;border:0;padding:0;color:var(--accent);cursor:pointer;font:inherit;text-decoration:underline}
button.pin{margin-left:8px;font:inherit;font-size:13px}
nav.crumbs{margin:6px 0 10px;font-size:14px}nav.crumbs span.sep{color:var(--muted);margin:0 6px}
.grid{overflow-x:auto}table{border-collapse:collapse;font-size:13px}
th,td{border:1px solid var(--rule);padding:4px 6px;text-align:center}th.repo{text-align:left;font-weight:normal}
td.l0{background:var(--l0);color:var(--lt)}td.l1{background:var(--l1);color:var(--lt)}
td.l2{background:var(--l2);color:var(--lt)}td.l3{background:var(--l3);color:var(--ltd)}
td.unknown{background:repeating-linear-gradient(45deg,var(--bg) 0 4px,var(--unk) 4px 6px);color:var(--fg);font-style:italic}
.pictures figure{margin:12px 0}.pictures img{max-width:100%;height:auto;display:block}
@media (max-width:600px){main{padding:12px}h1{font-size:19px}}
"""

SCRIPT = r"""
(function () {
  "use strict";
  var PIN_KEY = "fleet-view:pinned";
  function esc(s) {
    return String(s === null || s === undefined ? "" : s).replace(/&/g, "&amp;").replace(/</g, "&lt;")
      .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }
  function plural(n, one, many) { return n + " " + (n === 1 ? one : many); }
  function repos(n) { return plural(n, "repository", "repositories"); }
  function staticSource(bundle) {
    return {
      load: function () { return Promise.resolve(bundle); },
      subscribeTargets: function (cb) { cb(bundle.targets); return function () {}; }
    };
  }
  function units(bundle, view) {
    var v = bundle.views.filter(function (x) { return x.view === view; })[0];
    return v ? v.units : [];
  }
  function unitOf(bundle, view, key) { return units(bundle, view).filter(function (u) { return u.key === key; })[0]; }
  function root(bundle, view) { return units(bundle, view).filter(function (u) { return !u.parent; })[0]; }
  function byKey(a, b) { return a.key < b.key ? -1 : a.key > b.key ? 1 : 0; }
  function children(bundle, view, key) {
    return units(bundle, view).filter(function (u) { return u.parent === key; }).sort(byKey);
  }
  function subtree(bundle, view, key) {
    var out = [key], i = 0;
    while (i < out.length) { children(bundle, view, out[i]).forEach(function (u) { out.push(u.key); }); i += 1; }
    return out;
  }
  function members(bundle, view, key, direct) {
    var keys = direct ? [key] : subtree(bundle, view, key);
    return bundle.repos.filter(function (r) { return keys.indexOf(r.units[view]) >= 0; }).sort(byKey);
  }
  function stepName(bundle, n) {
    var s = bundle.steps.filter(function (x) { return x.step === n; })[0];
    return s ? "Step " + n + " · " + s.name : "Step " + n;
  }
  function gateLine(g) {
    var parts = ["holds back " + repos(g.held)];
    if (g.not_measured) { parts.push(g.not_measured + " of them not measured"); }
    if (g.excluded) { parts.push(g.excluded + " set aside by the owner, so the Step stays unreached"); }
    return "<li><code>" + esc(g.gate) + "</code>: " + esc(parts.join(", ")) + "</li>";
  }
  function since(bundle, state) {
    var keys = subtree(bundle, state.view, state.unit);
    var repoKeys = members(bundle, state.view, state.unit).map(function (r) { return r.key; });
    // unit labels are unique only within a view: a unit's credit matches on its view too
    var mine = bundle.achievements.filter(function (a) {
      return a.view === "repo" ? repoKeys.indexOf(a.unit) >= 0 : a.view === state.view && keys.indexOf(a.unit) >= 0;
    });
    var out = "<h2>Since last run</h2>";
    if (bundle.note) { out += "<p class=\"muted\">" + esc(bundle.note) + "</p>"; }
    out += mine.length ? "<ul>" + mine.map(function (a) { return "<li>" + esc(a.text) + "</li>"; }).join("") + "</ul>"
      : "<p>Nothing new was credited to this unit since the last run.</p>";
    var rc = bundle.recount;
    if (rc) {
      var lines = rc.lines.filter(function (l) { return repoKeys.indexOf(l.repo) >= 0; });
      out += "<div class=\"panel\"><p>Rules changed: this run reads the maturity model under new rules. "
        + "These moves come from the rules, not from the repositories.</p>"
        + (lines.length ? "<ul>" + lines.map(function (l) {
          return "<li>" + esc(l.repo + ": " + l.cause + " on " + l.row) + "</li>";
        }).join("") + "</ul>" : "") + "</div>";
    }
    return out;
  }
  function historyLine(extra, docId) {
    var h = extra && extra.history && docId ? extra.history[docId] : null;
    if (!h || !h.length) { return ""; }
    return "<p class=\"muted\">Published runs: " + h.map(function (e) {
      return esc(e.run + ": " + (e.step === null || e.step === undefined ? "no measured Step" : "Step " + e.step));
    }).join(" · ") + "</p>";
  }
  function stands(bundle, state, extra) {
    var u = unitOf(bundle, state.view, state.unit);
    var out = "<h2>Where this unit stands</h2>";
    if (u.lowest_step === null || u.lowest_step === undefined) {
      out += "<p>No repository below this unit has a measured Step yet (" + esc(u.measured + " of " + repos(u.repos))
        + " measured).</p>";
    } else {
      out += "<p><strong>" + esc(stepName(bundle, u.lowest_step)) + "</strong>, the lowest Step over "
        + esc(u.measured + " of " + repos(u.repos)) + " measured.</p>";
      if (u.next) {
        out += "<p>" + esc(stepName(bundle, u.next.step)) + " unlocks when these gates hold:</p><ul>"
          + u.next.gates.filter(function (g) { return g.held > 0; }).map(gateLine).join("") + "</ul>";
      } else {
        out += "<p>Every measured repository is at the top Step.</p>";
      }
    }
    var titles = {};
    bundle.tags.forEach(function (t) { titles[t.tag] = t.title; });
    out += "<p class=\"muted\">Practices met by truth tag: " + u.tags.map(function (c) {
      return esc(titles[c.tag] || c.tag) + " " + (c.measured ? esc(c.met + " of " + c.measured) : "not measured");
    }).join(" · ") + "</p>";
    return out + historyLine(extra, u.doc_id);
  }
  function target(bundle, state, targets, canSet) {
    var mine = (targets || []).filter(function (x) { return x.view === state.view && x.unit === state.unit; });
    var t = mine.filter(function (x) { return !x.pending; })[0];
    var p = mine.filter(function (x) { return x.pending; })[0];
    var u = unitOf(bundle, state.view, state.unit);
    var out = "<h2>Your target</h2>";
    if (p && (!t || t.step !== p.step)) {
      out += "<p>A target of " + esc(stepName(bundle, p.step)) + " was set on this page. Its progress shows after "
        + "the next run.</p>";
    }
    if (!t) {
      if (!p) { out += "<p>No Step target is set for this unit.</p>"; }
    } else if (t.distance === 0) {
      out += "<p>Target " + esc(stepName(bundle, t.step)) + " is reached.</p>";
    } else {
      out += "<p>Target " + esc(stepName(bundle, t.step)) + ". "
        + (t.distance === null ? "No repository below this unit is measured yet." : esc(plural(t.distance, "Step", "Steps") + " to go, "
          + repos(t.held) + " below it.")) + "</p>";
      if (t.missing.length) {
        out += "<p>Gates not yet held:</p><ul>" + t.missing.map(gateLine).join("") + "</ul>";
      }
    }
    if (bundle.target_note) { out += "<p class=\"muted\">" + esc(bundle.target_note) + "</p>"; }
    if (canSet && u.target_id) {
      out += "<p><label>Set a Step target <select id=\"target-step\">" + bundle.steps.map(function (s) {
        return s.step < 2 ? "" : "<option value=\"" + s.step + "\">" + esc(stepName(bundle, s.step)) + "</option>";
      }).join("") + "</select></label> <button data-act=\"set-target\">Set</button> "
        + "<button data-act=\"clear-target\">Clear</button></p>";
    }
    return out;
  }
  function cellTitle(bundle, repo, cell) {
    var row = bundle.rows[cell.row];
    var lines = [row.title];
    if (cell.status !== "level") {
      lines.push("State: not measured (" + (cell.reason || "no_value") + "), never read as level 0");
    } else {
      lines.push("State: level " + cell.level + " of " + row.top_level);
    }
    if (cell.signal) {
      lines.push("Condition: " + row.title + ", level " + (cell.next_level === null || cell.next_level === undefined ? "next" : cell.next_level)
        + ", reads " + cell.signal + (cell.value !== null && cell.value !== undefined ? " (measured " + cell.value + ")" : ""));
    }
    if (cell.why) { lines.push("Why: " + cell.why); }
    if (repo.next) {
      lines.push(stepName(bundle, repo.next.step) + " needs: " + repo.next.blockers.map(function (b) {
        return b.gate + (b.state === "not_measured" ? " (not measured: " + (b.reason || "no_value") + ")"
          : b.excluded ? " (set aside by the owner)" : "");
      }).join(", "));
    } else if (repo.step === null || repo.step === undefined) {
      lines.push("No measured Step for this repository");
    } else {
      lines.push("At the top Step");
    }
    return lines.join("\n");
  }
  function cellClass(bundle, cell) {
    if (cell.status !== "level") { return "unknown"; }
    var top = bundle.rows[cell.row].top_level || 1;
    return "l" + Math.min(3, Math.round(3 * cell.level / top));
  }
  function repoDetail(bundle, r) {
    var out = "<div class=\"panel\"><p><strong>" + esc(r.key) + "</strong> · "
      + (r.step === null || r.step === undefined ? "no measured Step (" + esc(r.reason || r.status) + ")"
        : esc(stepName(bundle, r.step))) + (r.web_url ? " · <a href=\"" + esc(r.web_url) + "\">open the repository</a>" : "") + "</p>";
    if (r.next) {
      out += "<p>" + esc(stepName(bundle, r.next.step)) + " waits on:</p><ul>" + r.next.blockers.map(function (b) {
        var why = b.state === "not_measured" ? "not measured (" + (b.reason || "no_value") + ")"
          : b.excluded ? "set aside by the owner" : b.state;
        return "<li><code>" + esc(b.gate) + "</code>: " + esc(b.condition + ", " + why) + "</li>";
      }).join("") + "</ul>";
    }
    if (r.exclusions.length) {
      out += "<p>Set aside by the owner in <code>.assess/config.toml</code>, listed and never counted:</p><ul>"
        + r.exclusions.map(function (e) {
          return "<li>" + esc(e.condition + " (" + e.kind + ")" + (e.reason ? ": " + e.reason : "")) + "</li>";
        }).join("") + "</ul>";
    }
    return out + "</div>";
  }
  function drill(bundle, state) {
    var trail = [], key = state.unit;
    while (key) { trail.unshift(key); var u = unitOf(bundle, state.view, key); key = u ? u.parent : null; }
    var out = "<h2>Drill-down</h2><nav class=\"crumbs\">" + trail.map(function (k) {
      return "<button class=\"link\" data-unit=\"" + esc(k) + "\">" + esc(k) + "</button>";
    }).join("<span class=\"sep\">›</span>") + (state.repo ? "<span class=\"sep\">›</span>" + esc(state.repo) : "") + "</nav>";
    var kids = children(bundle, state.view, state.unit);
    if (kids.length) {
      out += "<p>Units below:</p><ul>" + kids.map(function (u) {
        return "<li><button class=\"link\" data-unit=\"" + esc(u.key) + "\">" + esc(u.key) + "</button> "
          + "<span class=\"muted\">" + esc(u.kind + ", " + repos(u.repos)) + "</span></li>";
      }).join("") + "</ul>";
    }
    var mine = members(bundle, state.view, state.unit, true);
    if (mine.length) {
      out += "<div class=\"grid\"><table><thead><tr><th class=\"repo\">Repository</th>" + bundle.rows.map(function (r) {
        return "<th>" + esc(r.title) + "</th>";
      }).join("") + "</tr></thead><tbody>" + mine.map(function (r) {
        return "<tr><th class=\"repo\"><button class=\"link\" data-repo=\"" + esc(r.key) + "\">" + esc(r.key)
          + "</button></th>" + r.cells.map(function (c) {
            return "<td class=\"" + cellClass(bundle, c) + "\" title=\"" + esc(cellTitle(bundle, r, c)) + "\">"
              + (c.status === "level" ? esc(c.level + "/" + bundle.rows[c.row].top_level) : "not measured") + "</td>";
          }).join("") + "</tr>";
      }).join("") + "</tbody></table></div>";
    }
    var picked = bundle.repos.filter(function (r) { return r.key === state.repo; })[0];
    return out + (picked ? repoDetail(bundle, picked) : "");
  }
  function pictures(bundle) {
    if (!bundle.pictures.length) { return ""; }
    return "<h2>Fleet pictures</h2><div class=\"pictures\">" + bundle.pictures.map(function (p) {
      return "<figure><figcaption><strong>" + esc(p.title) + "</strong>: " + esc(p.question) + "</figcaption>"
        + "<img alt=\"" + esc(p.title) + "\" src=\"" + p.src + "\"></figure>";
    }).join("") + "</div>";
  }
  function movedLine(m) {
    if (!m) { return "This run is the baseline: no earlier run was compared, so no Step moves are counted yet."; }
    if (!m.comparable) { return "Not compared with the earlier run: " + m.reasons.join("; ") + "."; }
    var line = repos(m.up) + " moved up a Step and " + repos(m.down) + " moved down; " + m.same
      + " stayed at the same Step.";
    if (m.rule_only) { line += " " + plural(m.rule_only, "move", "moves") + " came from rule changes alone."; }
    return line;
  }
  function glance(bundle) {
    var f = bundle.front;
    if (!f) { return ""; }
    var out = "<h2>Fleet at a glance</h2>";
    if (f.skipped) {
      out += "<p>Not drawn: skipped: " + esc(f.skipped) + "</p>";
    } else {
      out += "<ul>" + f.steps.map(function (s) {
        return "<li>" + esc(stepName(bundle, s.step)) + ": " + esc(repos(s.count)) + "</li>";
      }).join("") + "</ul>";
      if (f.not_measured) { out += "<p class=\"muted\">" + esc(repos(f.not_measured)) + " with no measured Step.</p>"; }
    }
    if (!f.skipped) {
      out += "<p><strong>Since the last run.</strong> " + esc(movedLine(f.moved)) + "</p>";
      out += "<p><strong>One change away.</strong> " + esc(repos(f.one_change) + (f.one_change === 1 ? " is" : " are"))
        + " one change from the next Step.</p>";
      out += f.gates.length ? "<p>The gates that would move the most repositories, most first:</p><ol>"
        + f.gates.map(function (g) {
          return "<li><code>" + esc(g.gate) + "</code>: " + esc(g.task + ". Moves " + repos(g.repos) + " to the next Step.")
            + "</li>";
        }).join("") + "</ol>" : "<p>No single gate takes a repository to its next Step in this run.</p>";
    }
    if (f.teams.length) {
      out += "<p>Team pages, by name:</p><ul class=\"teams\">" + f.teams.map(function (t) {
        return "<li><button class=\"link\" data-view=\"ownership\" data-unit=\"" + esc(t.label) + "\">"
          + esc(t.label) + "</button></li>";
      }).join("") + "</ul>";
    }
    return out;
  }
  function packs(bundle) {
    if (!bundle.packs || !bundle.packs.length) { return ""; }
    return "<h2>Practice packs</h2><p class=\"muted\">Each pack applied to every repository. A repository where a "
      + "pack check could not be read counts as not measured, never as not met.</p>" + bundle.packs.map(function (p) {
        var head = "<h3>Pack " + esc(p.name) + " (version " + esc(p.version) + ")</h3>";
        if (!p.practices.length) { return head + "<p>This pack adds checks only, no practice to score.</p>"; }
        return head + "<ul>" + p.practices.map(function (q) {
          var title = q.level === 1 ? q.title : q.title + ", level " + q.level;
          return "<li>" + esc(title) + ": " + esc(q.met + " of " + q.measured + " measured "
            + (q.measured === 1 ? "repository" : "repositories") + " meet it") + (q.not_measured
            ? " <span class=\"muted\">(" + esc(repos(q.not_measured)) + " not measured)</span>" : "") + "</li>";
        }).join("") + "</ul>";
      }).join("");
  }
  var SECTIONS = [
    function (b) { return glance(b); },
    function (b) { return packs(b); },
    function (b, s) { return since(b, s); },
    function (b, s, t, c, x) { return stands(b, s, x); },
    function (b, s, t, c) { return target(b, s, t, c); },
    function (b, s) { return drill(b, s); },
    function (b) { return pictures(b); }
  ];
  function renderPage(bundle, state, targets, canSet, extra) {
    var u = unitOf(bundle, state.view, state.unit);
    var head = "<h1>Fleet view</h1><p class=\"muted\">Run of " + esc(bundle.scan_date) + ". Every number here comes "
      + "from the run's own files; nothing is worked out in the page.</p><p>Showing <strong>" + esc(u.key)
      + "</strong> (" + esc(u.kind) + ") <button class=\"pin\" data-act=\"pin\">Open on this unit next time</button></p>";
    return head + SECTIONS.map(function (f) { return f(bundle, state, targets, canSet, extra); }).join("");
  }
  function readPin(bundle) {
    try {
      var raw = window.localStorage.getItem(PIN_KEY);
      if (raw) {
        var parts = raw.split("\n");
        if (unitOf(bundle, parts[0], parts[1])) { return {view: parts[0], unit: parts[1], repo: null}; }
      }
    } catch (e) { /* storage blocked: open on the root */ }
    return null;
  }
  function writePin(state) {
    try { window.localStorage.setItem(PIN_KEY, state.view + "\n" + state.unit); } catch (e) { /* not kept */ }
  }
  function start(app, source) {
    source.load().then(function (bundle) {
      var r = root(bundle, "ownership") || bundle.views[0].units[0];
      var state = readPin(bundle) || {view: "ownership", unit: r.key, repo: null};
      var targets = bundle.targets;
      var canSet = false, extra = {history: {}};
      function draw() {
        app.innerHTML = renderPage(bundle, state, targets, canSet, extra);
        var u = unitOf(bundle, state.view, state.unit);
        if (source.history && u && u.doc_id && !(u.doc_id in extra.history)) {
          extra.history[u.doc_id] = null;
          source.history(u.doc_id).then(function (h) { extra.history[u.doc_id] = h; draw(); });
        }
      }
      source.subscribeTargets(function (t) { targets = t; draw(); });
      if (typeof source.setTarget === "function") {
        Promise.resolve(source.canSetTarget ? source.canSetTarget() : true).then(function (ok) {
          canSet = ok === true; draw();
        });
      }
      app.addEventListener("click", function (ev) {
        var el = ev.target.closest("button");
        if (!el) { return; }
        if (el.dataset.unit) { state = {view: el.dataset.view || state.view, unit: el.dataset.unit, repo: null}; }
        else if (el.dataset.repo) { state = {view: state.view, unit: state.unit, repo: el.dataset.repo}; }
        else if (el.dataset.act === "pin") { writePin(state); return; }
        else if (el.dataset.act === "set-target" && canSet) {
          source.setTarget(state.view, state.unit, Number(app.querySelector("#target-step").value)); return;
        } else if (el.dataset.act === "clear-target" && canSet) { source.setTarget(state.view, state.unit, null); return; }
        draw();
      });
      draw();
    });
  }
  var api = {staticSource: staticSource, renderPage: renderPage, cellTitle: cellTitle, SECTIONS: SECTIONS};
  if (typeof window !== "undefined" && window.document) {
    var bundle = JSON.parse(window.document.getElementById("fleet-bundle").textContent);
    start(window.document.getElementById("app"), window.FleetViewSource || staticSource(bundle));
  }
  globalThis.FleetView = api;
})();
"""
