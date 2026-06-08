"""Render and write the .assess/ wiki files from templates.

No LLM calls. Pure string formatting + file IO. Deterministic.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


# Templates live alongside the scripts/lib/ package, one directory up under templates/
_TEMPLATES_DIR = Path(__file__).resolve().parents[2] / "templates"


# Default "## Suggested actions" body for a hotspot page that has not (yet) been
# finalized with file-specific LLM actions. Worded as a deliberate pointer, not a
# TODO: a page that is never finalized - a hotspot flagged outside the run's Top 3,
# which assess_finalize is only required to fill for the Top 3 - still reads as
# intentional rather than as unfinished work. assess_finalize overwrites this
# section for the pages it's handed concrete actions; the heading is unchanged so
# that rewrite contract still holds (issue #165).
UNFINALIZED_ACTIONS_POINTER = (
    "This file is flagged but outside this run's Top 3. "
    "See the report's Top 3 Actions, or run a focused /assess pass "
    "for file-specific guidance."
)


@dataclass(frozen=True)
class HotspotEntry:
    path: str
    first_flagged: str
    last_seen: str
    status: str   # active | new | graduated | regressed | persistent
    # `ccn` and `loc` are `None` when the file's current metrics are not
    # carried in the latest stats sidecar (e.g. a graduated file that fell
    # off every top-N list). The wiki renders `None` as "-" - the file
    # may still be sized, we just don't have current numbers. Zero is
    # reserved for "actually zero LOC" and must never stand in for
    # "unknown" - that misleads reviewers into thinking the file was
    # emptied (issue #52 Bug 1).
    ccn: int | None
    loc: int | None


@dataclass(frozen=True)
class LogEntry:
    run_date: str
    files_scored: int
    readiness_score: float
    maturity_label: str
    # Optional[str]: None means no instruction file was found at any known
    # location (the schema convention in CLAUDE.md). The log template renders it
    # via str.format, so a None prints as "None" - unchanged from prior runtime
    # behaviour; only the annotation is corrected to match the data.
    instructions_grade: str | None
    graduated_count: int
    regressed_count: int
    new_count: int
    persistent_count: int
    top_action: str
    # Plugin version that produced this entry. Always rendered in the
    # heading so the log doubles as a version history and two runs on the
    # same calendar day stay distinguishable. Optional only for
    # backwards-compat with callers that don't pass it yet; new code
    # should always set it (issue #52 Bug 2).
    plugin_version: str | None = None
    report_link: str = "./assess-report.md"


def slug_for_path(path: str) -> str:
    """Convert a file path into a safe, collision-resistant filename slug.

    The slug is the normalized path (alphanumeric joined by hyphens) followed
    by a short hash of the original path. The hash ensures distinct paths
    that normalize identically (e.g., `src/foo-bar.py` vs `src/foo/bar.py`)
    don't overwrite each other's hotspot pages.
    """
    readable = re.sub(r"[^a-zA-Z0-9]+", "-", path).strip("-").lower()
    digest = hashlib.sha256(path.encode("utf-8")).hexdigest()[:8]
    return f"{readable}-{digest}"


def _load_template(name: str) -> str:
    return (_TEMPLATES_DIR / name).read_text(encoding="utf-8")


def write_index(assess_dir: Path, entries: list[HotspotEntry], *, last_updated: str) -> None:
    """(Re)write index.md from the current set of hotspot entries."""
    rows = []
    for e in entries:
        # `None` -> "-" so an unknown metric never reads as "the file was
        # emptied." Real zeros (rare for tracked source code) still render
        # as `0`.
        ccn_cell = "-" if e.ccn is None else str(e.ccn)
        loc_cell = "-" if e.loc is None else str(e.loc)
        rows.append(
            f"| `{e.path}` | {e.first_flagged} | {e.last_seen} | {e.status} | {ccn_cell} | {loc_cell} |"
        )
    content = _load_template("index.md.template").format(
        last_updated=last_updated,
        hotspot_rows="\n".join(rows) if rows else "| _no hotspots tracked yet_ | | | | | |",
    )
    (assess_dir / "index.md").write_text(content, encoding="utf-8")


def _build_log_heading(
    *, run_date: str, plugin_version: str | None, existing: str,
) -> str:
    """Build a unique `## ...` heading for a new log.md entry.

    Two collisions to defend against (issue #52 Bug 2):

    1. The plugin version is always rendered when present (so the log
       doubles as a version history). Two same-day runs at different
       versions are naturally distinguished.
    2. If the same `## YYYY-MM-DD (vX.Y.Z)` heading already exists in
       the file, append the current local time `HH:MM` so anchor links
       don't collide and markdownlint MD024 stays quiet. Using local
       time matches `run_date` (which is also local), so a reader
       doesn't see a timezone mismatch.
    """
    if plugin_version:
        base = f"## {run_date} (v{plugin_version})"
    else:
        base = f"## {run_date}"
    if base not in existing:
        return base
    # Already an entry with this exact heading - disambiguate with time.
    stamp = datetime.now().strftime("%H:%M")
    return f"{base[:-1]} {stamp})" if plugin_version else f"{base} {stamp}"


def append_log_entry(assess_dir: Path, entry: LogEntry) -> None:
    """Append a dated entry to log.md (create the file if absent)."""
    log_path = assess_dir / "log.md"
    existing = log_path.read_text(encoding="utf-8") if log_path.exists() else ""
    heading = _build_log_heading(
        run_date=entry.run_date,
        plugin_version=entry.plugin_version,
        existing=existing,
    )
    snippet = _load_template("log_entry.md.template").format(
        heading=heading,
        files_scored=entry.files_scored,
        readiness_score=entry.readiness_score,
        maturity_label=entry.maturity_label,
        instructions_grade=entry.instructions_grade,
        graduated_count=entry.graduated_count,
        regressed_count=entry.regressed_count,
        new_count=entry.new_count,
        persistent_count=entry.persistent_count,
        top_action=entry.top_action,
        report_link=entry.report_link,
    )
    if existing:
        log_path.write_text(existing + snippet, encoding="utf-8")
    else:
        log_path.write_text("# Assess Log\n\n" + snippet, encoding="utf-8")


def write_hotspot_page(
    assess_dir: Path,
    *,
    path: str,
    first_flagged: str,
    last_seen: str,
    status: str,
    loc: int,
    ccn: int,
    commits: int,
    has_tests: bool | None,
    history_rows: str,
    briefing: str,
    actions: str,
) -> None:
    """(Re)write hotspots/<slug>.md.

    has_tests=None means "we don't know yet" - shown as "unknown" in the page.
    Test-to-code pairing is a deferred feature; honest reporting beats lying.
    """
    hotspots_dir = assess_dir / "hotspots"
    hotspots_dir.mkdir(exist_ok=True)
    if has_tests is None:
        has_tests_str = "unknown"
    else:
        has_tests_str = "yes" if has_tests else "no"
    content = _load_template("hotspot.md.template").format(
        path=path,
        first_flagged=first_flagged,
        last_seen=last_seen,
        status=status,
        loc=loc,
        ccn=ccn,
        commits=commits,
        has_tests=has_tests_str,
        history_rows=history_rows,
        briefing=briefing,
        actions=actions,
    )
    (hotspots_dir / f"{slug_for_path(path)}.md").write_text(content, encoding="utf-8")
