"""``docs/checks.html``, the browsable check reference (#1236): complete, self-contained, plain.

The drift test is ``tests/test_catalog.py::test_committed_catalog_is_current``, which covers
every file ``gen_catalog.outputs()`` writes, this page included.
"""
from __future__ import annotations

import copy
import re
from html.parser import HTMLParser

import gen_catalog

from lib.report.catalog import Catalog, build_catalog
from lib.report.catalog_configure import CHECKOUT, EXAMPLE_EXCLUDED, INTRO, options
from lib.report.catalog_html import LEDE, SECTION_LEDES, inline, render_html
from lib.plain_english import lint
from lib.vocabulary import banned_hits


def _page(catalog: Catalog | None = None, version: str = "9.9.9", profiles: bool = True) -> str:
    return render_html(catalog or build_catalog(), version, profiles)


class _Ids(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.cards: list[str] = []
        self.ids: list[str] = []
        self.srcs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = dict(attrs)
        if a.get("id"):
            self.ids.append(str(a["id"]))
        if tag == "article" and "card" in (a.get("class") or ""):
            self.cards.append(str(a["id"]))
        if tag in ("script", "link", "img") and (a.get("src") or a.get("href")):
            self.srcs.append(str(a.get("src") or a.get("href")))


def _parse(page: str) -> _Ids:
    parser = _Ids()
    parser.feed(page)
    return parser


def test_every_condition_has_exactly_one_card() -> None:
    catalog = build_catalog()
    conditions = [lv["id"] for r in catalog["rows"] for lv in r["levels"]]
    cards = _parse(_page(catalog)).cards
    assert sorted(cards) == sorted(conditions) and len(cards) == len(set(cards))
    assert len(cards) > 20
    assert f"{len(conditions)} conditions" not in _page(catalog)  # no count line (#1239)
    assert 'class="n"' not in _page(catalog)


def test_ids_are_unique_and_every_anchor_resolves() -> None:
    page = _page()
    ids = _parse(page).ids
    assert len(ids) == len(set(ids))
    anchors = set(re.findall(r'href="#([^"]+)"', page))
    assert anchors <= set(ids), sorted(anchors - set(ids))


def test_a_card_shows_its_frame_and_each_criterion() -> None:
    catalog = build_catalog()
    page = _page(catalog)
    row = next(r for r in catalog["rows"] for lv in r["levels"] if lv["step_gates"])
    level = next(lv for lv in row["levels"] if lv["step_gates"])
    start = page.index(f'id="{level["id"]}"')
    card = page[start:page.index("</article>", start)]
    assert row["title"] in card and f"level {level['level']}" in card
    assert all(f"gate: {g}" in card for g in level["step_gates"])
    assert level["grade"] in card and all(f"#{t}" in card for t in level["truth"])
    for c in level["criteria"]:
        assert c["signal"] in card and c["producer"]["scan"] in card
    for label in ("Rule", "Why", "Fix", "Signal", "Scan"):
        assert f"<dt>{label}</dt>" in card


def test_a_hazard_a_shared_tag_and_an_untagged_condition_render() -> None:
    catalog = copy.deepcopy(build_catalog())
    first = catalog["rows"][0]["levels"][0]
    first["polarity"] = "hazard"
    last = catalog["rows"][-1]["levels"][-1]
    for tag in catalog["tags"]:
        tag["conditions"] = [c for c in tag["conditions"] if c != last["id"]]
        tag["listed"] = [c for c in tag["listed"] if c != last["id"]]
    last["truth"] = []
    page = _page(catalog)
    assert f'<article class="card hazard" id="{first["id"]}"' in page
    assert 'id="tag-untagged"' in page and _parse(page).cards.count(last["id"]) == 1
    # a condition with two tags has its card under the first and a link under the second
    shared = next(t for t in build_catalog()["tags"] if set(t["conditions"]) - set(t["listed"]))
    other = sorted(set(shared["conditions"]) - set(shared["listed"]))[0]
    page = _page()
    section = page[page.index(f'id="tag-{shared["id"]}"'):]
    section = section[:section.index("</section>")]
    assert f'<a href="#{other}">' in section and f'id="{other}"' not in section


def test_a_tag_with_no_condition_says_so() -> None:
    catalog = copy.deepcopy(build_catalog())
    catalog["tags"].append({"id": "empty", "title": "Empty", "conditions": [], "listed": [], "shared": [],
                            "why": "Nothing.", "why_source": ""})
    assert "No condition carries this tag yet." in _page(catalog)


def test_steps_grades_findings_and_scans_are_listed() -> None:
    catalog = build_catalog()
    page = _page(catalog)
    for section in ("steps", "grades", "findings", "scans", "configure"):
        assert f'id="{section}"' in page
    assert all(s["name"] in page for s in catalog["steps"])
    assert all(f"<code>{f['name']}</code>" in page for f in catalog["findings"])
    assert all(f"<code>{s['name']}</code>" in page for s in catalog["scans"])


def test_the_page_is_self_contained_and_names_its_versions() -> None:
    catalog = build_catalog()
    page = _page(catalog, "9.9.9")
    assert _parse(page).srcs == []  # no external script, stylesheet or image
    assert "@import" not in page and "fonts.googleapis" not in page
    assert "prefers-color-scheme: dark" in page and "system-ui" in page
    assert f"catalog version {catalog['catalog_version']}, toolkit 9.9.9" in page
    assert '<input type="search" id="q"' in page


def test_the_committed_page_stamps_the_plugin_version() -> None:
    page = gen_catalog.outputs()[gen_catalog.HTML]
    tag = f"v{gen_catalog.toolkit_version()}"
    assert f"bjcoombs/ai-native-toolkit@{tag}" in page
    assert f"ai-native-toolkit/{tag}/templates/assess-gate.yml" in page


def test_the_configure_section_covers_every_option() -> None:
    page = _page()
    section = page[page.index('id="configure"'):]
    for needle in ("[checks]", "exclude = [", "--profile handover", "--profile citizen", "--levels FILE",
                   "--managed-settings", "managed_settings = true", "[gate]", "fail_on_gate_loss = true",
                   "--fail-on-gate-loss", "fail_on_gate_loss: &#x27;true&#x27;", "include:",
                   "fail_on_gate_loss: true", "docs/build-your-own-practice.md"):
        assert needle in section, needle
    for o in options("9.9.9", True):
        assert o["references"], o["id"]


def test_profiles_are_planned_until_their_directory_ships() -> None:
    """#1150 not merged: no --profile example, and --levels shown as the /fleet flag."""
    page = _page(profiles=False)
    section = page[page.index('id="configure"'):]
    assert "/assess --profile" not in section and "/assess --levels" not in section
    assert "not in this release yet" in section and "/fleet ~/src --levels" in section
    assert "issues/1150" in section
    committed = gen_catalog.outputs()[gen_catalog.HTML]
    assert ("/assess --profile handover" in committed) is gen_catalog.PROFILES.is_dir()


def test_the_configure_examples_match_the_code() -> None:
    """The example condition is a real id, and the CI inputs exist in action.yml and the template."""
    catalog = build_catalog()
    assert EXAMPLE_EXCLUDED in {lv["id"] for r in catalog["rows"] for lv in r["levels"]}
    root = gen_catalog.ROOT
    action = (root / "action.yml").read_text(encoding="utf-8")
    template = (root / "templates" / "assess-gate.yml").read_text(encoding="utf-8")
    assert "  fail_on_gate_loss:" in action and "  config:" in action
    assert "    fail_on_gate_loss:" in template and "    config:" in template
    assert CHECKOUT.split("  #")[0] in (root / "README.md").read_text(encoding="utf-8")


def test_the_fixed_prose_is_plain_english_and_avoids_the_banned_words() -> None:
    texts = [LEDE, INTRO, *SECTION_LEDES.values()]
    for shipped in (True, False):
        for o in options("9.9.9", shipped):
            texts += [o["title"], *o["paragraphs"]]
    assert {t: [i["reason"] for i in lint(t)] for t in texts if lint(t)} == {}
    assert [t for t in texts if banned_hits(t)] == []


def test_inline_escapes_then_marks_code_and_links() -> None:
    assert inline("Use `a<b` and [docs](https://x.test/a?b=1&c=2).") == (
        'Use <code>a&lt;b</code> and <a href="https://x.test/a?b=1&amp;c=2">docs</a>.')
