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
) -> None:
    """(Re)write index.md from the current set of hotspot entries.

    ``run_id`` / ``schema_version`` (when supplied) prepend a non-rendering
    HTML-comment provenance stamp; omitted, output is byte-identical to before.
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


# --- log.md integrity chain (issue: assess-obey-thyself, task 11) -------------
#
# Each appended log entry carries a non-rendering chain marker
# ``<!-- chain:<hash> -->`` where ``hash = sha256(prev_chain_hash + entry_text)``
# truncated to 16 hex chars, and ``prev`` is the literal string ``"genesis"`` for
# the first entry. The chain lets a run verify that no earlier entry has been
# edited after the fact: tampering with entry N breaks the recomputation at N.
# This is the guardrail against a *lying history* - the log is meant to be an
# append-only record, and an unpressured record silently drifts (CLAUDE.md north
# star: a self-description under no pressure to stay true). The marker is an HTML
# comment, so it is invisible to a human reading the rendered Markdown.
_GENESIS = "genesis"
_CHAIN_LINE_RE = re.compile(r"<!-- chain:([0-9a-f]{16}) -->\n?")
_LOG_HEADER = "# Assess Log\n\n"


def _chain_hash(prev: str, entry_text: str) -> str:
    """The chained checksum for an entry: sha256(prev + entry_text)[:16].

    Deterministic for identical ``(prev, entry_text)`` - the same content always
    yields the same marker, so a clean re-run reproduces the chain byte-for-byte.
    """
    return hashlib.sha256((prev + entry_text).encode("utf-8")).hexdigest()[:16]


def _parse_log_entries(text: str) -> list[tuple[str, str | None]]:
    """Split a log.md body into ``(entry_text, stored_chain_hash)`` pairs.

    ``entry_text`` excludes the trailing chain-marker line so it hashes exactly as
    it was written. An entry with no marker (a legacy log predating the chain, or
    a hand-authored tail) yields a ``None`` stored hash - unverifiable, not a break.
    """
    body = text[len(_LOG_HEADER):] if text.startswith(_LOG_HEADER) else text
    entries: list[tuple[str, str | None]] = []
    pos = 0
    for m in _CHAIN_LINE_RE.finditer(body):
        entries.append((body[pos:m.start()], m.group(1)))
        pos = m.end()
    tail = body[pos:]
    if tail.strip():
        entries.append((tail, None))
    return entries


def _chain_tail(text: str) -> tuple[str, str]:
    """Return ``(prev_hash, trailing)`` for chaining a new entry onto ``text``.

    ``prev_hash`` is the last *stored* chain marker's hash (or ``"genesis"`` when
    there is none). ``trailing`` is any unchained text after that last marker -
    ``""`` for a normal chained log, but the whole body for a legacy log that has
    no markers yet. A new entry hashes ``prev_hash`` over ``trailing + entry_text``
    because ``_parse_log_entries`` groups everything between two markers into one
    entry: the trailing legacy text has no delimiter of its own, so on verify it
    is read as part of the next entry, and append must hash it the same way.
    """
    # Strip the file header exactly as _parse_log_entries does, so the trailing
    # span append hashes matches the entry content verify re-reads.
    body = text[len(_LOG_HEADER):] if text.startswith(_LOG_HEADER) else text
    prev = _GENESIS
    end = 0
    for m in _CHAIN_LINE_RE.finditer(body):
        prev = m.group(1)
        end = m.end()
    return prev, body[end:]


def _verify_chain_text(text: str) -> tuple[bool, int | None]:
    """Verify the integrity chain of an in-memory log body.

    Returns ``(valid, broken_at_n)``: ``broken_at_n`` is the 1-based index of the
    first entry whose stored hash disagrees with a recomputation from the prior
    hash plus its content, or ``None`` when the chain is intact. Unchained (legacy)
    entries can't be verified, so they advance the running hash from their content
    without being flagged - a chained entry appended after them still validates.
    """
    prev = _GENESIS
    for n, (content, stored) in enumerate(_parse_log_entries(text), start=1):
        if stored is None:
            prev = _chain_hash(prev, content)
            continue
        if stored != _chain_hash(prev, content):
            return False, n
        prev = stored
    return True, None


def verify_log_chain(assess_dir: Path) -> tuple[bool, int | None]:
    """Verify the log.md integrity chain on disk. See ``_verify_chain_text``.

    A missing log (genesis / fresh install) is vacuously valid - there is no prior
    entry to contradict.
    """
    log_path = assess_dir / "log.md"
    if not log_path.exists():
        return True, None
    return _verify_chain_text(log_path.read_text(encoding="utf-8"))


def append_log_entry(assess_dir: Path, entry: LogEntry) -> None:
    """Append a dated entry to log.md (create the file if absent).

    Before appending, the existing chain is verified; if a prior entry was edited
    the new entry leads with a one-time disclosure line so a reader can't miss that
    the history is compromised. Every appended entry then carries its own chain
    marker (see the chain block above).
    """
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
    # Verify the chain of what's already on disk. On a break, lead this entry with
    # a disclosure line - but only once: if the exact line is already in the log a
    # prior run already caught this break, so we don't spam it every run.
    valid, broken_at = _verify_chain_text(existing)
    disclosure = ""
    if not valid:
        line = (
            f"> **Warning:** History integrity broken at entry {broken_at}. "
            "Prior entries may have been modified."
        )
        if line not in existing:
            disclosure = line + "\n\n"
    # Stamp this entry (not the whole file) so the log stays a per-run history:
    # each run's line carries its own run_id. "" when no run_id is set, keeping
    # the appended snippet byte-identical for legacy callers.
    entry_text = _run_id_comment(entry.run_id, entry.schema_version) + disclosure + snippet
    # Chain this entry to the prior one so a later edit is detectable. Hash over
    # any unchained trailing text (a legacy log's body) plus this entry, keyed off
    # the last stored marker - that is exactly the span verify re-reads as one
    # entry (see _chain_tail).
    prev_hash, trailing = _chain_tail(existing)
    chain = _chain_hash(prev_hash, trailing + entry_text)
    entry_text += f"<!-- chain:{chain} -->\n"
    base = existing if existing else _LOG_HEADER
    log_path.write_text(base + entry_text, encoding="utf-8")


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


# --- orphan hotspot pruning (issue: assess-obey-thyself, task 9) --------------
#
# A hotspot page whose source file has been deleted is a lying map: it keeps
# describing a file that no longer exists, and its status token still reads
# "active" (or new/persistent/...). Rather than delete the page - the .assess/
# wiki is a *compounding* history where past hotspots stay visible even after
# they graduate - each run stamps an orphaned page RETIRED. History is preserved,
# but the page no longer claims to describe a live file. This mirrors the
# graduated-hotspot idiom (a page that survives after the file leaves the top
# list) rather than the deletion idiom, which the wiki has none of.
RETIRED_STATUS = "retired - file deleted"

# The source path a hotspot page describes lives in its `# Hotspot: `<path>``
# heading (there is no YAML frontmatter). The status lives in the italic
# metadata line `_First flagged: .... Status: <status>._`.
_HOTSPOT_PATH_RE = re.compile(r"^# Hotspot: `(?P<path>.+?)`", re.MULTILINE)
_HOTSPOT_STATUS_RE = re.compile(r"(?P<prefix>Status: )(?P<status>.+?)(?P<suffix>\._)")


def hotspot_page_source_path(content: str) -> str | None:
    """The source file path a hotspot page describes, from its heading, or None."""
    m = _HOTSPOT_PATH_RE.search(content)
    return m.group("path") if m else None


def hotspot_page_status(content: str) -> str | None:
    """The status token a hotspot page carries in its metadata line, or None."""
    m = _HOTSPOT_STATUS_RE.search(content)
    return m.group("status") if m else None


def prune_orphan_hotspots(assess_dir: Path, repo_root: Path) -> list[str]:
    """Stamp every hotspot page whose source file is absent from disk as retired.

    Returns the sorted list of source paths retired *this* call (already-retired
    pages and live-file pages are left untouched, so the operation is idempotent).
    A retired page keeps all its history; only its status token flips and a visible
    retirement banner is inserted, so no active page ever references a missing file.
    """
    hotspots_dir = assess_dir / "hotspots"
    if not hotspots_dir.is_dir():
        return []
    retired: list[str] = []
    for page in sorted(hotspots_dir.glob("*.md")):
        content = page.read_text(encoding="utf-8")
        path = hotspot_page_source_path(content)
        if path is None:
            continue  # not a recognisable hotspot page - leave it alone
        if hotspot_page_status(content) == RETIRED_STATUS:
            continue  # already retired - idempotent
        if (repo_root / path).exists():
            continue  # source still on disk - a legitimate hotspot, untouched
        banner = (
            "\n> **Retired:** the source file was absent from disk at the latest "
            "run (deleted, moved, or renamed). This page is preserved for history "
            "and no longer describes a live file."
        )
        stamped = _HOTSPOT_STATUS_RE.sub(
            lambda m: f"{m.group('prefix')}{RETIRED_STATUS}{m.group('suffix')}{banner}",
            content, count=1,
        )
        page.write_text(stamped, encoding="utf-8")
        retired.append(path)
    return sorted(retired)
