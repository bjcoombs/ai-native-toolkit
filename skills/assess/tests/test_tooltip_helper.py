"""The diagram tooltip helper is dependency-free and reads data attributes first (#1360, #1361).

Static assertions on the source only: no browser and no DOM test dependency (maintainer
decision on #1312). The helper is ``templates/diagram-tooltip.js``; the embedding guidance
that points hosts at it is in ``references/embedding-diagrams.md`` and the fleet report docs.
"""
from __future__ import annotations

import re
from pathlib import Path

from svg_data_attrs import CODE_KEYS, ID_KEYS, STATE_KEYS

SKILL = Path(__file__).resolve().parents[1]
HELPER = SKILL / "templates" / "diagram-tooltip.js"
SOURCE = HELPER.read_text(encoding="utf-8")
# The source with its comments removed, so a rule is met by code, not by its description.
CODE = re.sub(r"/\*.*?\*/|//[^\n]*", "", SOURCE, flags=re.S)


def _listeners() -> set[str]:
    return set(re.findall(r'on\(root, "([a-z]+)"', CODE))


def test_the_helper_has_no_imports_or_external_urls() -> None:
    assert not re.search(r"^\s*(import|export)\b", CODE, re.M)
    assert "require(" not in CODE and "import(" not in CODE
    assert not re.search(r"https?://|//[\w.-]+\.\w+/", CODE)
    assert "fetch(" not in CODE and "XMLHttpRequest" not in CODE
    assert "eval(" not in CODE and "innerHTML" not in CODE  # text only, never markup


def test_it_reads_data_attributes_and_falls_back_to_the_title() -> None:
    fmt = CODE[CODE.index("function defaultFormat"):CODE.index("function muteTitle")]
    assert 'indexOf("data-") === 0' in fmt
    assert fmt.index("data-") < fmt.index("titleOf(part)"), "data attributes come first"
    assert "textContent" in fmt


def test_it_registers_pointer_and_touch_handlers() -> None:
    assert {"pointermove", "pointerout", "pointerleave", "pointerdown", "pointerup", "touchstart",
            "click", "keydown"} <= _listeners()
    assert 'pointerType === "touch"' in CODE


def test_a_second_tap_dismisses_and_a_link_follows_on_mouse_but_not_the_first_tap() -> None:
    # A tap is read on pointerup (iOS Safari fires no delegated click from an SVG part).
    tap = CODE[CODE.index('on(root, "pointerup"'):CODE.index('on(root, "click"')]
    assert 'pointerType !== "touch"' in tap
    assert "shown === part" in tap and "hide()" in tap
    assert "blockClick = Boolean(linkOf(part))" in tap
    click = CODE[CODE.index('on(root, "click"'):CODE.index('on(root, "keydown"')]
    assert "if (!touching)" in click and click.index("if (!touching)") < click.index("preventDefault")
    assert "if (blockClick)" in click and "event.preventDefault()" in click


def test_the_tooltip_hides_ids_and_twinned_codes_but_keeps_state_codes() -> None:
    """Ids are always left out; a state code only beside its readable twin, since for some
    parts (an unknown cell, an orphan doc) the code is the only record of the state."""
    ids = re.search(r"var IDS = \[([^\]]*)\]", CODE)
    twins = re.search(r"var TWINS = \[([^\]]*)\]", CODE)
    assert ids is not None and twins is not None
    assert set(re.findall(r'"([\w-]+)"', ids.group(1))) == ID_KEYS
    assert set(re.findall(r'"([\w-]+)"', twins.group(1))) == {"-label", "-title"}
    assert not ID_KEYS & STATE_KEYS and CODE_KEYS == ID_KEYS | STATE_KEYS
    assert "!hidden(part, attr.name.slice(5))" in CODE


def test_the_tooltip_is_themed_for_both_colour_schemes() -> None:
    assert "prefers-color-scheme: dark" in CODE
    for var in ("--diagram-tooltip-bg", "--diagram-tooltip-fg", "--diagram-tooltip-border"):
        assert CODE.count(var) >= 2, var


def test_it_reaches_object_embeds_and_mutes_the_native_title_while_showing() -> None:
    assert 'object[type="image/svg+xml"]' in CODE and "contentDocument" in CODE
    assert "function muteTitle" in CODE and "function restoreTitle" in CODE


def test_each_event_is_handled_once_however_many_attached_roots_it_crosses() -> None:
    """attach(svg) inside an attached page must not show on the inner root and hide on the
    document's: every listener goes through on(), which marks the event as handled."""
    listen = CODE[CODE.index("function listen"):CODE.index("function attachObject")]
    assert "root.addEventListener" not in listen
    helper = CODE[CODE.index("function on("):CODE.index("function listen")]
    assert "if (event.__diagramTooltip)" in helper and "event.__diagramTooltip = true" in helper


def test_a_format_passed_to_an_attached_root_takes_effect() -> None:
    """The page attaches itself on load, so attach(document, {format}) must update the formatter
    the existing listeners read, not be skipped by the already-attached guard."""
    attach = CODE[CODE.index("function attach("):CODE.index("window.DiagramTooltip")]
    assert attach.index("__diagramTooltipFormat = options.format") < attach.index("if (!target.__diagramTooltip)")
    listen = CODE[CODE.index("function listen"):CODE.index("function attachObject")]
    assert listen.count("formatOf()") == listen.count("show(part, ") == 3  # every show reads it


def test_a_custom_format_gets_the_title_text_even_while_it_is_muted() -> None:
    show = CODE[CODE.index("function show("):CODE.index("function hide(")]
    assert "format(part, part.getAttribute(PARKED) || (title ? title.textContent" in show


def test_a_mouse_click_pins_the_tooltip_until_a_second_click_a_click_elsewhere_scroll_or_escape() -> None:
    """#1364: a click pins; hover then neither moves nor hides it; a linked part's click follows the link."""
    click = CODE[CODE.index('on(root, "click"'):CODE.index('on(root, "keydown"')]
    mouse = click[click.index("if (!touching)"):click.index("if (blockClick)")]
    assert mouse.index("linkOf(part)") < mouse.index("pinned = true")
    assert "!part || (pinned && shown === part)" in mouse and "hide()" in mouse
    for event in ("pointermove", "pointerleave", "pointerout"):
        body = CODE[CODE.index(f'on(root, "{event}"'):]
        assert "pinned" in body[:body.index("});")], event
    hide = CODE[CODE.index("function hide("):CODE.index("function on(")]
    assert "pinned = false" in hide


def test_a_scroll_dismisses_the_tooltip() -> None:
    scroll = CODE[CODE.index('addEventListener("scroll"'):]
    assert "hide()" in scroll[:scroll.index("});")] and "passive: true" in scroll and "capture: true" in scroll


def test_a_pinned_tooltip_whose_part_was_redrawn_away_is_dropped_on_the_next_move() -> None:
    """The fleet page redraws its pictures on live updates: a pinned part left detached must not
    keep its tooltip on screen with every hover handler returning early."""
    move = CODE[CODE.index('on(root, "pointermove"'):CODE.index('on(root, "pointerleave"')]
    guard = move.index("!shown.isConnected")
    assert guard < move.index("hide()") < move.index('pointerType === "touch" || pinned')
