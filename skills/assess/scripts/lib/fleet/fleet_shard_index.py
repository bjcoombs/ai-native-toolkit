"""The index page of a sharded fleet: one link per shard and its Step roll-up (issue #950).

``index_page(index, steps)`` renders ``fleet_shard.IndexDoc`` as one self-contained HTML page:
per shard its label, unit kind, lowest Step (named from ``steps``, the page bundle's
``[{step, name}]``), "N of M measured" and a link to the shard's artifact, or "not published
yet" before its first publish; then the shards no longer in the fleet, each still linked. It
holds no repository document and no database: every number is the plan's, and the page makes
no network request. Every string is HTML-escaped, and a link is written only for a
``https://claude.ai/`` URL (``record_shard`` refuses any other), opening in a new tab.

Deterministic: the same index and steps give the same bytes. Tests:
``tests/test_fleet_page_shard.py``.
"""
from __future__ import annotations

import html
from collections.abc import Mapping, Sequence
from typing import Any, Final

from lib.fleet.fleet_shard import IndexDoc

URL_PREFIX: Final = "https://claude.ai/"
STYLE: Final = """
:root{--bg:#ffffff;--fg:#1d2321;--muted:#5d6763;--rule:#d9dedb;--accent:#1f6f6b}
@media (prefers-color-scheme: dark){:root{--bg:#1a201e;--fg:#e6ebe8;--muted:#9aa5a0;--rule:#2c3532;--accent:#5fb8b1}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif}
main{max-width:960px;margin:0 auto;padding:16px}
h1{font-size:22px;margin:8px 0 4px}h2{font-size:18px;margin:28px 0 8px;border-bottom:1px solid var(--rule);padding-bottom:4px}
.muted{color:var(--muted)}a{color:var(--accent)}
table{border-collapse:collapse;width:100%}th,td{text-align:left;padding:6px 8px;border-bottom:1px solid var(--rule)}
.wrap{overflow-x:auto}
"""


def link(url: str | None, text: str) -> str:
    """An anchor for a claude.ai artifact URL, or the muted note that it is not published yet."""
    if url is None or not url.startswith(URL_PREFIX):
        return '<span class="muted">not published yet</span>'
    return f'<a href="{html.escape(url)}" target="_blank" rel="noopener">{html.escape(text)}</a>'


def _step(steps: Mapping[int, str], step: int | None) -> str:
    if step is None:
        return "no measured Step"
    name = steps.get(step)
    return f"Step {step}" + (f" ({name})" if name else "")


def index_page(index: IndexDoc, steps: Sequence[Mapping[str, Any]] = ()) -> str:
    """The whole HTML document (see the module docstring)."""
    names = {int(s["step"]): str(s.get("name") or "") for s in steps}
    rows = "".join(
        f"<tr><td>{html.escape(s['label'])}</td><td>{html.escape(s['kind'] or 'unassigned')}</td>"
        f"<td>{html.escape(_step(names, s['lowest_step']))}</td>"
        f"<td>{s['measured']} of {s['repos']} measured</td><td>{link(s['url'], 'Open')}</td></tr>"
        for s in index["shards"])
    out = [
        "<!doctype html>", '<html lang="en">', "<head>", '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        "<title>Fleet index</title>", f"<style>{STYLE}</style>", "</head>", "<body>", "<main>",
        "<h1>Fleet index</h1>",
        f'<p class="muted">Run of {html.escape(str(index["scan_date"]))}. The fleet is published as one page per '
        "top-level unit; each page links back here. The lowest Step and the measured count are each unit's "
        "own, from the run's files.</p>",
        '<h2>Units</h2><div class="wrap"><table><thead><tr><th>Unit</th><th>Kind</th><th>Lowest Step</th>'
        f"<th>Measured</th><th>Page</th></tr></thead><tbody>{rows}</tbody></table></div>",
    ]
    if index["retired"]:
        out.append("<h2>No longer in the fleet</h2><p class=\"muted\">These pages are kept as they were last "
                   "published; nothing deletes them.</p><ul>"
                   + "".join(f"<li>{link(r['url'], 'An earlier unit page')}</li>" for r in index["retired"])
                   + "</ul>")
    out += ["</main>", "</body>", "</html>", ""]
    return "\n".join(out)
