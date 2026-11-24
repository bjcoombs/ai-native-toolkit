"""``docs/checks.html``: the measures catalog (``lib.catalog``) as a browsable page (#1236).

``render_html(catalog, version)`` is pure and deterministic: the same catalog and toolkit
version give the same bytes, with no timestamp and no commit id. The header names the
catalog version and the toolkit version (``.claude-plugin/plugin.json``). The page is
self-contained: no network fetch, the landing page's colours (``docs/index.html``) and its
font stacks with system fallbacks, light and dark themes, and one column at phone width.

In order it holds: a sticky index; one section per truth tag, in ``catalog["tags"]`` order,
with one card per condition the tag lists (``listed``, the condition's first tag) and a line
linking the shared conditions listed under another tag, so each card appears once; a section
for conditions with no tag, when there are any; the Steps and their gates; the evidence
grades; the findings; the scans; and "Configure the checks" (``lib.catalog_configure``).

A card (``<article class="card" id="<condition id>">``) shows the condition id, its practice
(the row title), its level, its Step gates, its grade and whether it counts, a hazard mark,
what it requires and its tags, then per criterion the rule, why, fix, signal and scan. Every
card carries ``data-text``, the lower-cased words the client-side filter matches; without
JavaScript every card shows.

``inline(text)`` renders the configure prose's two marks, `` `code` `` and ``[text](url)``,
after escaping. ``scripts/gen_catalog.py`` writes the page beside the other catalog files,
so ``tests/test_catalog.py::test_committed_catalog_is_current`` is its drift test; the page's
own tests are ``tests/test_catalog_html.py``.
"""
from __future__ import annotations

import html
import re

from lib.catalog import Catalog, CatalogCriterion, CatalogLevel, CatalogRow, CatalogScan
from lib.catalog_configure import INTRO, ConfigureOption, options
from lib.catalog_tags import CatalogTag
from lib.manifest.manifest_whys import PLUMBING

TITLE = "Assess check reference"
LEDE = ("Every condition `/assess` checks, grouped by the truth tag it keeps honest. "
        "Each card gives the rule it applies, why it matters and how to fix it.")
SECTION_LEDES = {
    "steps": "Only gates decide the Step. A Step needs every gate listed for it.",
    "grades": ("A condition's grade comes from what its signals read. "
               "Only counted grades add to the practices in place."),
    "findings": "Findings the report raises across practices, apart from the conditions.",
    "scans": "The scans behind the signals. Network and platform scans run only when asked.",
}
UNTAGGED = "Conditions with no truth tag."

CSS = """\
:root {
  --bg: #f6f8f7; --surface: #ffffff; --fg: #14201d; --muted: #52635e; --line: #d5dfdb;
  --teal: #0f766e; --teal-soft: #ccebe6; --amber: #b45309; --amber-soft: #fdecd3;
  --rose: #9f1239; --rose-soft: #fde4ea;
  --display: "Bricolage Grotesque", "Segoe UI", system-ui, sans-serif;
  --body: "IBM Plex Sans", "Segoe UI", system-ui, sans-serif;
  --mono: "IBM Plex Mono", ui-monospace, Menlo, Consolas, monospace;
}
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) {
  --bg: #0e1513; --surface: #16201d; --fg: #e6efec; --muted: #9fb3ad; --line: #2a3834;
  --teal: #5cc8bb; --teal-soft: #173b36; --amber: #f2a54a; --amber-soft: #3d2a12;
  --rose: #fb7185; --rose-soft: #3b1520; color-scheme: dark } }
:root[data-theme="dark"] {
  --bg: #0e1513; --surface: #16201d; --fg: #e6efec; --muted: #9fb3ad; --line: #2a3834;
  --teal: #5cc8bb; --teal-soft: #173b36; --amber: #f2a54a; --amber-soft: #3d2a12;
  --rose: #fb7185; --rose-soft: #3b1520; color-scheme: dark }
body { background: var(--bg); color: var(--fg); font-family: var(--body); font-size: 15px; line-height: 1.55; margin: 0; }
.wrap { display: grid; grid-template-columns: 230px minmax(0, 1fr); gap: 28px; max-width: 1180px; margin: 0 auto;
  padding-block: 32px 64px; padding-left: max(16px, env(safe-area-inset-left, 0px));
  padding-right: max(16px, env(safe-area-inset-right, 0px)); }
nav { position: sticky; top: 16px; align-self: start; max-height: calc(100vh - 32px); overflow: auto;
  display: grid; gap: 2px; font-size: 14px; }
nav a { color: var(--fg); text-decoration: none; padding: 4px 8px; border-radius: 6px;
  display: flex; justify-content: space-between; gap: 8px; }
nav a:hover, nav a:focus-visible { background: var(--teal-soft); outline: none; }
nav .n { color: var(--muted); font-family: var(--mono); font-size: 12px; }
nav h2 { margin: 14px 8px 4px; font-family: var(--mono); font-size: 11px; letter-spacing: .08em;
  text-transform: uppercase; color: var(--muted); }
main { display: grid; gap: 36px; min-width: 0; }
h1, h2, h3 { font-family: var(--display); margin: 0; line-height: 1.2; text-wrap: balance; }
h1 { font-size: clamp(28px, 5vw, 40px); }
h2 { font-size: 22px; scroll-margin-top: 16px; }
h3 { font-size: 17px; scroll-margin-top: 16px; }
p { margin: 0; max-width: 72ch; }
a { color: var(--teal); }
.eyebrow { font-family: var(--mono); font-size: 12px; letter-spacing: .08em; text-transform: uppercase; color: var(--teal); }
.lede, .tagwhy, .note { color: var(--muted); }
.tagwhy { font-style: italic; }
header, section, .tagsec, .option { display: grid; gap: 12px; }
input[type=search] { width: 100%; max-width: 420px; box-sizing: border-box; padding: 9px 12px; border: 1px solid var(--line);
  border-radius: 8px; background: var(--surface); color: var(--fg); font: inherit; }
.card { background: var(--surface); border: 1px solid var(--line); border-radius: 10px; padding: 16px 18px;
  display: grid; gap: 10px; min-width: 0; scroll-margin-top: 16px; }
.card.hazard { border-left: 3px solid var(--rose); }
.head { display: flex; flex-wrap: wrap; align-items: baseline; gap: 6px 12px; }
.cid { font-family: var(--mono); font-size: 13px; color: var(--teal); }
.ctitle { font-weight: 600; }
.chips { display: flex; flex-wrap: wrap; gap: 6px; }
.chip { font-family: var(--mono); font-size: 11.5px; padding: 1px 7px; border-radius: 999px; background: var(--teal-soft); }
.chip.gate { background: var(--amber-soft); color: var(--amber); font-weight: 600; }
.chip.hazard { background: var(--rose-soft); color: var(--rose); }
.chip.dim { background: transparent; border: 1px solid var(--line); color: var(--muted); }
.crit { border-top: 1px solid var(--line); padding-top: 10px; display: grid; gap: 6px; }
.crit:first-of-type { border-top: 0; padding-top: 0; }
dl { display: grid; grid-template-columns: 5.5rem minmax(0, 1fr); gap: 4px 12px; margin: 0; font-size: 14px; }
dt { color: var(--muted); }
dd { margin: 0; min-width: 0; overflow-wrap: anywhere; }
code { font-family: var(--mono); font-size: .9em; background: var(--teal-soft); padding: 1px 5px; border-radius: 4px;
  overflow-wrap: anywhere; }
pre { margin: 0; background: var(--surface); border: 1px solid var(--line); border-radius: 8px; padding: 12px 14px;
  overflow-x: auto; font-family: var(--mono); font-size: 13px; line-height: 1.5; }
pre code { background: none; padding: 0; overflow-wrap: normal; }
.example { display: grid; gap: 4px; min-width: 0; }
.example span { font-family: var(--mono); font-size: 12px; color: var(--muted); }
.tbl { overflow-x: auto; }
table { border-collapse: collapse; width: 100%; font-size: 14px; }
th, td { text-align: left; padding: 8px 10px; border-bottom: 1px solid var(--line); vertical-align: top; }
th { font-family: var(--mono); font-size: 11.5px; text-transform: uppercase; letter-spacing: .06em; color: var(--muted); }
.empty { color: var(--muted); }
.configure { border: 2px solid var(--amber); background: var(--amber-soft); border-radius: 10px; padding: 20px; }
.configure .option { background: var(--surface); border: 1px solid var(--line); border-radius: 10px; padding: 16px 18px; }
footer { font-size: 13px; color: var(--muted); display: flex; flex-wrap: wrap; gap: 8px 20px; }
@media (max-width: 760px) { .wrap { grid-template-columns: 1fr; } nav { position: static; max-height: none; } }
@media (max-width: 480px) { dl { grid-template-columns: 1fr; gap: 0; } dd { margin-bottom: 6px; } }
"""

SCRIPT = """\
document.getElementById("q").addEventListener("input", function (e) {
  var q = e.target.value.trim().toLowerCase();
  document.querySelectorAll(".card").forEach(function (el) {
    el.hidden = q !== "" && el.dataset.text.indexOf(q) < 0;
  });
  document.querySelectorAll(".tagsec").forEach(function (sec) {
    sec.hidden = q !== "" && !sec.querySelector(".card:not([hidden])");
  });
});
"""

_CODE = re.compile(r"`([^`]+)`")
_LINK = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")


def _e(value: object) -> str:
    return html.escape(str(value), quote=True)


def inline(text: str) -> str:
    """``text`` escaped, with `` `code` `` as ``<code>`` and ``[text](url)`` as a link."""
    out = _e(text)
    out = _CODE.sub(lambda m: f"<code>{m.group(1)}</code>", out)
    return _LINK.sub(lambda m: f'<a href="{m.group(2)}">{m.group(1)}</a>', out)


def _chip(text: str, kind: str = "", title: str = "") -> str:
    cls = f"chip {kind}".strip()
    tip = f' title="{_e(title)}"' if title else ""
    return f'<span class="{cls}"{tip}>{_e(text)}</span>'


def _criterion(c: CatalogCriterion, scans: dict[str, CatalogScan]) -> str:
    scan = c["producer"]["scan"]
    scan_title = f" · {_e(scans[scan]['title'])}" if scan in scans else ""
    why = _e(c["why"]) if c["why"] else '<span class="empty">no why recorded</span>'
    fix = _e(c["fix"]) if c["fix"] else '<span class="empty">none</span>'
    return (f'<div class="crit"><div class="ctitle">{_e(c["title"] or c["text"])}</div><dl>'
            f'<dt>Rule</dt><dd>{_e(c["text"])}</dd>'
            f"<dt>Why</dt><dd>{why}</dd>"
            f"<dt>Fix</dt><dd>{fix}</dd>"
            f'<dt>Signal</dt><dd><code>{_e(c["signal"])}</code></dd>'
            f"<dt>Scan</dt><dd><code>{_e(scan)}</code>{scan_title}</dd></dl></div>")


def _search_text(row: CatalogRow, level: CatalogLevel) -> str:
    words = [level["id"], row["title"], *level["truth"], *level["step_gates"]]
    for c in level["criteria"]:
        words += [c["title"], c["text"], c["why"], c["fix"], c["signal"], c["producer"]["scan"]]
    return " ".join(w for w in words if w).lower()


def card(row: CatalogRow, level: CatalogLevel, scans: dict[str, CatalogScan]) -> str:
    """One condition's card."""
    hazard = level["polarity"] == "hazard"
    chips = [_chip(f"level {level['level']}")]
    chips += [_chip(f"gate: {g}", "gate", "Step gate") for g in level["step_gates"]]
    counted = level["grade"] if level["counts"] else f"{level['grade']} · not counted"
    chips.append(_chip(counted, "" if level["counts"] else "dim", "Evidence grade"))
    if hazard:
        chips.append(_chip("hazard", "hazard"))
    chips += [_chip(f"requires {r}", "dim", "Requires") for r in level["requires"]]
    if level["guarded_by"] is not None:
        chips.append(_chip(f"guarded by {level['guarded_by']}", "dim"))
    chips += [_chip(f"#{t}", "dim", "Truth tag") for t in level["truth"]]
    crits = "".join(_criterion(c, scans) for c in level["criteria"])
    return (f'<article class="card{" hazard" if hazard else ""}" id="{_e(level["id"])}" '
            f'data-text="{_e(_search_text(row, level))}">'
            f'<div class="head"><span class="cid">{_e(level["id"])}</span>'
            f'<span class="ctitle">{_e(row["title"])}</span></div>'
            f'<div class="chips">{"".join(chips)}</div>{crits}</article>')


def _tag_section(tag: CatalogTag, cards: list[str], shared_elsewhere: list[str]) -> str:
    body = "".join(cards) if cards else ""
    if shared_elsewhere:
        links = ", ".join(f'<a href="#{_e(c)}"><code>{_e(c)}</code></a>' for c in shared_elsewhere)
        body += f'<p class="note">Also carries this tag, shown under its first tag: {links}.</p>'
    if not cards and not shared_elsewhere:
        body = '<p class="empty">No condition carries this tag yet.</p>'
    return (f'<section class="tagsec" id="tag-{_e(tag["id"])}"><div><h2>{_e(tag["title"])} '
            f'<span class="cid">#{_e(tag["id"])}</span></h2><p class="tagwhy">{_e(tag["why"])}</p></div>'
            f"{body}</section>")


def _table(head: list[str], rows: list[list[str]]) -> str:
    th = "".join(f"<th>{h}</th>" for h in head)
    trs = "".join("<tr>" + "".join(f"<td>{cell}</td>" for cell in r) + "</tr>" for r in rows)
    return f'<div class="tbl"><table><thead><tr>{th}</tr></thead><tbody>{trs}</tbody></table></div>'


def _reference(key: str, title: str, table: str) -> str:
    return f'<section id="{key}"><h2>{title}</h2><p class="lede">{inline(SECTION_LEDES[key])}</p>{table}</section>'


def _steps(catalog: Catalog) -> str:
    rows = []
    for s in catalog["steps"]:
        gates = "<br>".join(f'<code>{_e(g["id"])}</code> ({_e(g["row"])} level {g["level"]})' for g in s["gates"])
        rows.append([str(s["step"]), _e(s["name"]), gates or '<span class="empty">none</span>'])
    return _reference("steps", "Steps and their gates", _table(["Step", "Name", "Gates"], rows))


def _grades(catalog: Catalog) -> str:
    rows = [[f'<code>{_e(g["grade"])}</code>', "yes" if g["counts"] else "no, listed",
             ", ".join(_e(c) for c in g["input_classes"]) or "none"] for g in catalog["grades"]]
    return _reference("grades", "Evidence grades", _table(["Grade", "Counted", "Input classes"], rows))


def _findings(catalog: Catalog) -> str:
    rows = [[f'<code>{_e(f["name"])}</code><br>{_e(f["title"])}', _e(f["why"]), _e(f["action"])]
            for f in catalog["findings"]]
    return _reference("findings", "Findings", _table(["Finding", "Why it matters", "Next step"], rows))


def _scans(catalog: Catalog) -> str:
    rows = []
    for s in catalog["scans"]:
        marks = "".join(f" {_chip(m, 'dim')}" for m, on in (("network", s["network"]), ("platform", s["platform"])) if on)
        why = "Run plumbing: no row or finding reads this scan." if s["why"] == PLUMBING else s["why"]
        rows.append([f'<code>{_e(s["name"])}</code>{marks}<br>{_e(s["title"])}', _e(s["summary"]), _e(why)])
    return _reference("scans", "Scans", _table(["Scan", "What it reads", "Why"], rows))


def _option(o: ConfigureOption) -> str:
    paras = "".join(f"<p>{inline(p)}</p>" for p in o["paragraphs"])
    examples = "".join(f'<div class="example"><span>{_e(x["label"])}</span><pre><code>{_e(x["code"])}</code></pre></div>'
                       for x in o["examples"])
    refs = " · ".join(f'<a href="{_e(url)}">{_e(text)}</a>' for text, url in o["references"])
    return (f'<div class="option" id="cfg-{_e(o["id"])}"><h3>{_e(o["title"])}</h3>{paras}{examples}'
            f'<p class="note">Reference: {refs}</p></div>')


def _configure(version: str) -> str:
    body = "".join(_option(o) for o in options(version))
    return (f'<section class="configure" id="configure"><h2>Configure the checks</h2>'
            f"<p>{inline(INTRO)}</p>{body}</section>")


def render_html(catalog: Catalog, version: str) -> str:
    """The whole page for ``catalog``, naming toolkit ``version`` (``3.0.0``)."""
    scans = {s["name"]: s for s in catalog["scans"]}
    levels = {lv["id"]: (row, lv) for row in catalog["rows"] for lv in row["levels"]}
    sections, nav = [], ['<h2>Truth tags</h2>']
    for tag in catalog["tags"]:
        listed = [card(*levels[c], scans) for c in tag["listed"] if c in levels]
        elsewhere = [c for c in tag["conditions"] if c not in tag["listed"]]
        sections.append(_tag_section(tag, listed, elsewhere))
        nav.append(f'<a href="#tag-{_e(tag["id"])}">{_e(tag["title"])}<span class="n">{len(tag["conditions"])}</span></a>')
    untagged = [card(row, lv, scans) for row, lv in levels.values() if not lv["truth"]]
    if untagged:
        sections.append(_tag_section({"id": "untagged", "title": "Untagged", "conditions": [], "listed": [],
                                      "shared": [], "why": UNTAGGED, "why_source": ""}, untagged, []))
        nav.append(f'<a href="#tag-untagged">Untagged<span class="n">{len(untagged)}</span></a>')
    nav += ['<h2>Reference</h2>', '<a href="#steps">Steps and gates</a>', '<a href="#grades">Evidence grades</a>',
            f'<a href="#findings">Findings<span class="n">{len(catalog["findings"])}</span></a>',
            f'<a href="#scans">Scans<span class="n">{len(catalog["scans"])}</span></a>',
            '<a href="#configure">Configure the checks</a>']
    source = (f'Generated from <code>skills/assess/references/measures.json</code> (catalog version '
              f'{catalog["catalog_version"]}, toolkit {_e(version)}) by <code>make regen</code>. '
              f'{len(levels)} conditions.')
    header = (f'<header><span class="eyebrow">AI Native Toolkit · /assess</span><h1>{TITLE}</h1>'
              f'<p class="lede">{inline(LEDE)}</p><p class="note">{source} '
              f'<a href="#configure">Configure the checks</a>.</p>'
              '<input type="search" id="q" placeholder="Filter by id, practice, signal or text" '
              'aria-label="Filter conditions"></header>')
    footer = ('<footer><a href="./">AI Native Toolkit</a><a href="https://github.com/bjcoombs/ai-native-toolkit/blob/'
              'main/docs/what-we-measure.md">What /assess measures</a><a href="https://github.com/bjcoombs/'
              'ai-native-toolkit/blob/main/skills/assess/references/measures.json">measures.json</a></footer>')
    parts = [
        "<!doctype html>", '<html lang="en">', "<head>", '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">',
        f"<title>{TITLE}</title>",
        '<meta name="description" content="Every check /assess makes, with its rule, why, fix, tags and grade, '
        'and how to configure them.">',
        '<meta name="color-scheme" content="light dark">',
        "<!-- Generated by skills/assess/scripts/gen_catalog.py (make regen); do not edit by hand. -->",
        f"<style>\n{CSS}</style>", "</head>", "<body>", '<div class="wrap">',
        f'<nav aria-label="Index">{"".join(nav)}</nav>', "<main>", header, *sections,
        _steps(catalog), _grades(catalog), _findings(catalog), _scans(catalog), _configure(version), footer,
        "</main>", "</div>", f"<script>\n{SCRIPT}</script>", "</body>", "</html>",
    ]
    return "\n".join(parts) + "\n"
