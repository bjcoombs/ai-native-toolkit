"""``fleet-view.html``: one self-contained page over a fleet run, written beside ``report.md`` (#946).

``build_page`` wraps a ``fleet_page_data.page_bundle`` in one HTML file: the stylesheet and
script of ``fleet_page_assets`` inline, the bundle embedded as JSON in a
``<script type="application/json">`` block (every ``<`` written as ``\\u003c``, so no string in
it closes the block), and the drawn fleet SVGs as ``data:`` image URIs. The page makes no
network request and loads nothing; it computes no level, Step or achievement.

``write_page`` builds it from this run's ``fleet-data.json`` document, ``achievements.json``
document, the maturity model and the drawn diagrams (``fleet_diagrams.Diagram``: the tag
profile and heatmap of #940 and the Step diagrams of #941 are embedded as drawn; a skipped
one is left out), and writes it atomically. Without a maturity model or ``fleet-data.json``
there is nothing to show, so it removes any page an earlier run left. ``clear_page`` removes
it (``fleet.py --no-diagrams``). Deterministic: the same inputs give the same bytes.

`build_page(bundle)` -> str, `write_page(out_dir, data, achievements, model, diagrams)` ->
the path or None, `clear_page(out_dir)`. Tests: `tests/test_fleet_page.py`.
"""
from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from lib.fleet.fleet_page_assets import SCRIPT, STYLE
from lib.fleet.fleet_page_data import PageBundle, page_bundle, picture
from lib.levels import MaturityModel

PAGE_FILE = "fleet-view.html"
# The drawn diagrams the page embeds, in page order: #940's tag pictures, then #941's Step pictures.
PICTURES = ("tag-profile.svg", "tag-heatmap.svg", "step-funnel.svg", "one-change-away.svg", "step-movement.svg")


def embed(bundle: PageBundle) -> str:
    """The bundle as inert JSON for a script block: sorted keys, every ``<`` escaped."""
    return json.dumps(bundle, sort_keys=True, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")


def build_page(bundle: PageBundle) -> str:
    """The whole HTML document."""
    return "\n".join([
        "<!doctype html>",
        '<html lang="en">',
        "<head>",
        '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        "<title>Fleet view</title>",
        f"<style>{STYLE}</style>",
        "</head>",
        "<body>",
        '<main id="app"><noscript>This page needs JavaScript to draw the fleet from its embedded data.</noscript></main>',
        f'<script type="application/json" id="fleet-bundle">{embed(bundle)}</script>',
        f"<script>{SCRIPT}</script>",
        "</body>",
        "</html>",
        "",
    ])


def clear_page(out_dir: Path) -> None:
    path = out_dir / PAGE_FILE
    if path.is_file() and not path.is_symlink():
        path.unlink()


def write_page(out_dir: Path, data: Mapping[str, Any] | None, achievements: Mapping[str, Any] | None,
               model: MaturityModel | None, diagrams: Sequence[Mapping[str, Any]]) -> Path | None:
    """Write ``fleet-view.html``; None (and any stale page removed) without a model or data."""
    clear_page(out_dir)
    if data is None or model is None:
        return None
    drawn = {d["file"]: d for d in diagrams if d.get("svg") is not None}
    pictures = [picture(name, drawn[name]["title"], drawn[name]["question"], drawn[name]["svg"])
                for name in PICTURES if name in drawn]
    path = out_dir / PAGE_FILE
    fd, tmp = tempfile.mkstemp(dir=out_dir, prefix=f".{PAGE_FILE}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(build_page(page_bundle(data, achievements, model, pictures)))
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return path
