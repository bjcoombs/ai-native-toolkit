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


def _growth_profile_line(accretion: dict | None) -> str:
    """One briefing line naming a hotspot's monotonic-growth profile, or "".

    ``accretion`` is the per-file accretion-ratchet entry for *this* hotspot
    (the serialized AccretionFile dict: ``net_additions`` / ``commit_count`` /
    ``time_span_months``), plus a ``reliable`` flag threaded down from the scan.
    A file absent from the accretion data (no entry, or None) earns no line -
    growth that wasn't flagged as pure accretion is normal development, not a
    ratchet. When the underlying git history is degenerate (shallow/squashed
    clone) the count is still reported but disclaimed, since the scan can't see
    the full sequence.
    """
    if not accretion:
        return ""
    net = accretion.get("net_additions", 0)
    commits = accretion.get("commit_count", 0)
    months = round(accretion.get("time_span_months", 0))
    line = (
        f"Growth profile: monotonic "
        f"(+{net} LOC, 0 net reductions over {commits} commits in {months} months)."
    )
    if accretion.get("reliable") is False:
        line += " (history may be incomplete - shallow/squashed repo)"
    return line


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
    # Run provenance (issue: assess-obey-thyself). When set, each appended entry
    # carries a non-rendering HTML-comment stamp so a machine can trace the log
    # line back to the run-context.json that produced it. Optional for
    # backwards-compat with callers that don't pass it yet.
    run_id: str | None = None
    schema_version: str | None = None


def _run_id_comment(run_id: str | None, schema_version: str | None) -> str:
    """An HTML-comment provenance line stamping a wiki artifact with its run.

    Returns "" when no run_id is supplied so legacy callers (and every test that
    doesn't thread a run_id) produce byte-identical output. HTML comments don't
    render in Markdown, so the stamp is invisible to a human reading the wiki but
    lets a machine trace a page back to the run that wrote it.
    """
    if not run_id:
        return ""
    version = schema_version or "unknown"
    return f"<!-- assess:run_id={run_id} artifact_schema_version={version} -->\n"


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


def write_index(
    assess_dir: Path, entries: list[HotspotEntry], *, last_updated: str,
    run_id: str | None = None, schema_version: str | None = None,
    scope: str | None = None,
) -> None:
    """(Re)write index.md from the current set of hotspot entries.

    ``run_id`` / ``schema_version`` (when supplied) prepend a non-rendering
    HTML-comment provenance stamp; omitted, output is byte-identical to before.

    ``scope`` (the repo-relative subtree of a ``/assess <path>`` run) adds a
    scope line under the title so the wiki page names what subtree it covers;
    None (a whole-repo run) leaves the body byte-identical to before.
    """
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
    if scope:
        # Insert a scope line right after the H1 title so a reader (and any
        # committed diff) sees the page is subtree-scoped, not whole-repo.
        content = content.replace(
            "# Assess Wiki Index\n",
            f"# Assess Wiki Index\n\n_Scope: `{scope}`_\n",
            1,
        )
    (assess_dir / "index.md").write_text(
        _run_id_comment(run_id, schema_version) + content, encoding="utf-8"
    )


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
    # Stamp this entry (not the whole file) so the log stays a per-run history:
    # each run's line carries its own run_id. "" when no run_id is set, keeping
    # the appended snippet byte-identical for legacy callers.
    snippet = _run_id_comment(entry.run_id, entry.schema_version) + snippet
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
    accretion_data: dict | None = None,
    run_id: str | None = None,
    schema_version: str | None = None,
) -> None:
    """(Re)write hotspots/<slug>.md.

    has_tests=None means "we don't know yet" - shown as "unknown" in the page.
    Test-to-code pairing is a deferred feature; honest reporting beats lying.

    ``accretion_data`` is the accretion-ratchet entry for *this* file (the
    serialized AccretionFile dict plus the scan's ``reliable`` flag), or None
    when the file isn't accreting. When present, one growth-profile line is
    appended to the briefing - no new section header - so the page names the
    monotonic-growth tendency right where an agent is briefed before editing.
    """
    hotspots_dir = assess_dir / "hotspots"
    hotspots_dir.mkdir(exist_ok=True)
    if has_tests is None:
        has_tests_str = "unknown"
    else:
        has_tests_str = "yes" if has_tests else "no"
    growth = _growth_profile_line(accretion_data)
    if growth:
        briefing = f"{briefing} {growth}"
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
    (hotspots_dir / f"{slug_for_path(path)}.md").write_text(
        _run_id_comment(run_id, schema_version) + content, encoding="utf-8"
    )
