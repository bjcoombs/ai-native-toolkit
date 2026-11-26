"""The diagram tooltip helper is dependency-free and reads data attributes first (#1360, #1361).

Static assertions on the source only: no browser and no DOM test dependency (maintainer
decision on #1312). The helper is ``templates/diagram-tooltip.js``; the embedding guidance
that points hosts at it is in ``references/embedding-diagrams.md`` and the fleet report docs.
"""
from __future__ import annotations

import re
from pathlib import Path

from svg_data_attrs import CODE_KEYS, EXTRA_KEYS

SKILL = Path(__file__).resolve().parents[1]
HELPER = SKILL / "templates" / "diagram-tooltip.js"
SOURCE = HELPER.read_text(encoding="utf-8")
# The source with its comments removed, so a rule is met by code, not by its description.
CODE = re.sub(r"/\*.*?\*/|//[^\n]*", "", SOURCE, flags=re.S)


def _listeners() -> set[str]:
    return set(re.findall(r'addEventListener\("([a-z]+)"', CODE))


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
    tap = CODE[CODE.index('addEventListener("pointerup"'):CODE.index('addEventListener("click"')]
    assert 'pointerType !== "touch"' in tap
    assert "shown === part" in tap and "hide()" in tap
    assert "blockClick = Boolean(linkOf(part))" in tap
    click = CODE[CODE.index('addEventListener("click"'):CODE.index('addEventListener("keydown"')]
    assert "if (!touching)" in click and click.index("if (!touching)") < click.index("preventDefault")
    assert "if (blockClick)" in click and "event.preventDefault()" in click


def test_the_tooltip_leaves_out_the_same_machine_codes_the_title_check_does() -> None:
    skip = re.search(r"var SKIP = \[([^\]]*)\]", CODE)
    assert skip is not None
    assert set(re.findall(r'"([\w-]+)"', skip.group(1))) == CODE_KEYS | EXTRA_KEYS
    assert "SKIP.indexOf(attr.name.slice(5)) < 0" in CODE


def test_the_tooltip_is_themed_for_both_colour_schemes() -> None:
    assert "prefers-color-scheme: dark" in CODE
    for var in ("--diagram-tooltip-bg", "--diagram-tooltip-fg", "--diagram-tooltip-border"):
        assert CODE.count(var) >= 2, var


def test_it_reaches_object_embeds_and_mutes_the_native_title_while_showing() -> None:
    assert 'object[type="image/svg+xml"]' in CODE and "contentDocument" in CODE
    assert "function muteTitle" in CODE and "function restoreTitle" in CODE
