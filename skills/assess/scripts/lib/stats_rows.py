"""Per-file row lists written into ``complexity-stats.json``.

``top_lists`` ranks the enriched hotspot rows into the three top-10 lists;
``file_ccn_rows`` emits the uncapped ``files`` list, one ``{path, ccn}`` row per
scored file, which the doc-complexity join reads so it sees every scored file
rather than only the files that made a top-10 list (issue #556). Stdlib only.
"""

from __future__ import annotations

from typing import cast

from lib.run_context_types import FileCcnRow, HotspotRow, ScoredHotspotRow


def strip_score(rows: list[ScoredHotspotRow]) -> list[HotspotRow]:
    """Drop the private ``_score`` ranking key from each row."""
    return [cast(HotspotRow, {k: v for k, v in r.items() if k != "_score"}) for r in rows]


def top_lists(enriched: list[ScoredHotspotRow]) -> dict[str, list[HotspotRow]]:
    """``top_hotspots`` / ``top_complex`` / ``top_large``, ten rows each."""
    # Ties break on the repository-relative path, ascending, under Python's
    # default byte ordering for `str`. Without it a stable single-key sort
    # hands ties back in scanner emission order, so which of a tied group
    # makes the top ten depends on how lizard and scc happened to enumerate
    # the tree - and membership of `top_hotspots` decides which files get a
    # wiki page and a first-flagged date (issue #426). The primary keys are
    # unchanged, so no file moves when the values differ; this is the same
    # `(-primary, path)` shape the attention list took in #357.
    by_score = sorted(enriched, key=lambda f: (-f["_score"], f["path"]))
    by_ccn = sorted(enriched, key=lambda f: (-f["ccn"], f["path"]))
    by_loc = sorted(enriched, key=lambda f: (-f["loc"], f["path"]))
    return {
        "top_hotspots": strip_score(by_score[:10]),
        "top_complex": strip_score(by_ccn[:10]),
        "top_large": strip_score(by_loc[:10]),
    }


def file_ccn_rows(enriched: list[ScoredHotspotRow]) -> list[FileCcnRow]:
    """Every scored file as ``{path, ccn}``, sorted by path, with no cap.

    ``ccn`` is the file-level aggregate, the same value the top lists carry.
    Sorted by path so the list is stable across runs whatever order the
    scanners emitted the files in.
    """
    return [{"path": r["path"], "ccn": r["ccn"]}
            for r in sorted(enriched, key=lambda r: r["path"])]
