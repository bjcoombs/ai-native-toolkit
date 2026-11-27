"""The diagram tooltip helper as one inline ``<script>`` block, for a page builder to ship (#1364).

``helper_script()`` returns ``templates/diagram-tooltip.js`` (relative to ``skills/assess``) as
``<script id="diagram-tooltip">...</script>``: a built page includes it once, inline, so its
inlined diagrams show each part's details on hover and tap with no host-side code and no
external script URL. The helper delegates from ``document``, so diagrams a page script draws
after load need no ``attach`` call.

The header comment is left out (it shows a ``<script src=...>`` example, which would close the
block early and read as an external URL), and every other ``</`` is written ``<\\/``, which is the
same text inside a JavaScript string, regex or comment, so nothing in the helper can end the
block. A helper holding ``<!--`` is refused, since that opens the HTML script-data escape state.

Stdlib only and deterministic: the same helper gives the same bytes. Used by
``lib/fleet/fleet_page.py``. Tests: ``tests/test_fleet_page.py``.
"""
from __future__ import annotations

import re
from pathlib import Path

HELPER = Path(__file__).resolve().parents[2] / "templates" / "diagram-tooltip.js"
SCRIPT_ID = "diagram-tooltip"
_HEADER = re.compile(r"\A\s*/\*.*?\*/\s*", re.S)


def helper_code(source: str) -> str:
    """``source`` (the helper's text) made safe to sit inside an HTML ``<script>`` element."""
    code = _HEADER.sub("", source, count=1).replace("</", "<\\/")
    if "<!--" in code:
        raise ValueError("diagram-tooltip.js holds '<!--', which an inline script block cannot carry")
    return code


def helper_script() -> str:
    """The shipped helper as one inline ``<script id="diagram-tooltip">`` block."""
    return f'<script id="{SCRIPT_ID}">\n{helper_code(HELPER.read_text(encoding="utf-8"))}</script>'
