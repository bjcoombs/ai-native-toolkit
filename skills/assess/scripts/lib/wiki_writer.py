"""Render and write the .assess/ wiki files from templates.

No LLM calls. Pure string formatting + file IO. Deterministic.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, replace
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
    # emptied (issue #52 Bug 1). ``ccn`` is the file aggregate, which the
    # stats sidecar may carry as a float.
    ccn: float | None
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
    """(Re)write index.md: this run's entries merged into the prior catalog.

    The index is the catalog of every hotspot ever flagged (#420), so it is
    seeded from the rows already in ``index.md``; ``entries`` (this run's top
    hotspots and graduations) replace the row for their path, and every other
    path keeps its last known row. A hotspot page with no row in either (a page
    orphaned by an older writer, or an index that was deleted) is backfilled
    from the page itself. A carried row whose page is now retired takes the
    page's retired status, so the index never calls a deleted file live. A
    page retired as excluded before finalize (#356) was never part of a
    finished assessment, so its path gets no carried or backfilled row.

    Row order: this run's entries first, then carried rows in their prior
    order, then backfilled rows by path.

    ``run_id`` / ``schema_version`` (when supplied) prepend a non-rendering
    HTML-comment provenance stamp; omitted, output is byte-identical to before.

    ``scope`` (the repo-relative subtree of a ``/assess <path>`` run) adds a
    scope line under the title so the wiki page names what subtree it covers;
    None (a whole-repo run) leaves the body byte-identical to before.
    """
    index_path = assess_dir / "index.md"
    prior = (
        parse_index_rows(index_path.read_text(encoding="utf-8"))
        if index_path.exists() else []
    )
    pages = read_hotspot_page_entries(assess_dir)
    merged = merge_index_entries(prior, entries, pages)
    rows = []
    for e in merged:
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
    index_path.write_text(
        _run_id_comment(run_id, schema_version) + content, encoding="utf-8"
    )


def merge_index_entries(
    prior: list[HotspotEntry], current: list[HotspotEntry],
    pages: dict[str, HotspotEntry],
) -> list[HotspotEntry]:
    """Merge this run's index entries into the prior catalog (#420).

    ``pages`` maps each hotspot page's source path to the entry read from the
    page (see ``read_hotspot_page_entries``).

    A path absent from ``current`` is not a hotspot this run, so a carried or
    backfilled row never keeps a live status (new, active, persistent,
    regressed): it renders ``graduated``, the legend's "was a hotspot, no
    longer is". A page is rewritten only while its file is ranked, so an
    orphaned page still carries the live status of its last ranked run. A
    retired page's status wins over the row's.
    """
    merged: dict[str, HotspotEntry] = {}
    for e in current:
        merged.setdefault(e.path, e)
    never_assessed = {
        p for p, page in pages.items() if page.status == RETIRED_EXCLUDED_STATUS
    }
    carried = [e for e in prior if e.path not in merged]
    carried_paths = {e.path for e in carried}
    backfilled = [pages[p] for p in sorted(pages) if p not in merged and p not in carried_paths]
    for e in [*carried, *backfilled]:
        if e.path in never_assessed or e.path in merged:
            continue
        merged[e.path] = _not_current(e, pages.get(e.path))
    return list(merged.values())


def _not_current(entry: HotspotEntry, page: HotspotEntry | None) -> HotspotEntry:
    """The index row for a path this run did not rank: retired if its page is
    retired, else graduated."""
    if page is not None and is_retired_status(page.status):
        return replace(entry, status=page.status)
    if is_retired_status(entry.status):
        return entry
    return replace(entry, status="graduated")


_INDEX_ROW_RE = re.compile(
    r"^\| `(?P<path>[^`]+)` \| (?P<first>[^|]*?) \| (?P<last>[^|]*?) \| "
    r"(?P<status>[^|]*?) \| (?P<ccn>[^|]*?) \| (?P<loc>[^|]*?) \|$",
    re.MULTILINE,
)


def _parse_metric(cell: str) -> float | None:
    """A rendered metric cell back to a number; "-" (unknown) to None."""
    cell = cell.strip()
    try:
        value = float(cell)
    except ValueError:
        return None
    return int(value) if value.is_integer() and "." not in cell else value


def parse_index_rows(content: str) -> list[HotspotEntry]:
    """The hotspot rows of an ``index.md``, in file order."""
    entries = []
    for m in _INDEX_ROW_RE.finditer(content):
        loc = _parse_metric(m.group("loc"))
        entries.append(HotspotEntry(
            path=m.group("path"),
            first_flagged=m.group("first").strip(),
            last_seen=m.group("last").strip(),
            status=m.group("status").strip(),
            ccn=_parse_metric(m.group("ccn")),
            loc=None if loc is None else int(loc),
        ))
    return entries


_HOTSPOT_META_RE = re.compile(
    r"_First flagged: (?P<first>.+?)\. Last seen: (?P<last>.+?)\. Status: (?P<status>.+?)\._"
)
_PAGE_LOC_RE = re.compile(r"^\| LOC \| (?P<v>[^|]*?) \|$", re.MULTILINE)
_PAGE_CCN_RE = re.compile(
    r"^\| Cyclomatic complexity \(file (?:max|aggregate)\) \| (?P<v>[^|]*?) \|$",
    re.MULTILINE,
)


def read_hotspot_page_entries(assess_dir: Path) -> dict[str, HotspotEntry]:
    """An index entry for every recognisable hotspot page, keyed by source path.

    Read from the page's heading, metadata line and current-metrics table.
    Pages missing the heading or metadata line are skipped.
    """
    hotspots_dir = assess_dir / "hotspots"
    if not hotspots_dir.is_dir():
        return {}
    out: dict[str, HotspotEntry] = {}
    for page in sorted(hotspots_dir.glob("*.md")):
        content = page.read_text(encoding="utf-8")
        path = hotspot_page_source_path(content)
        meta = _HOTSPOT_META_RE.search(content)
        if path is None or meta is None:
            continue
        loc_m = _PAGE_LOC_RE.search(content)
        ccn_m = _PAGE_CCN_RE.search(content)
        loc = _parse_metric(loc_m.group("v")) if loc_m else None
        out[path] = HotspotEntry(
            path=path,
            first_flagged=meta.group("first"),
            last_seen=meta.group("last"),
            status=meta.group("status"),
            ccn=_parse_metric(ccn_m.group("v")) if ccn_m else None,
            loc=None if loc is None else int(loc),
        )
    return out


def _short_run_id(run_id: str) -> str:
    """The unique tail of a run id: the random suffix of the orchestrator's
    ``YYYYMMDDHHMMSS-<8 hex>`` form (the date is already in the heading), or the
    whole id when it has no ``-`` separator."""
    return run_id.rsplit("-", 1)[-1] or run_id


def _build_log_heading(
    *, run_date: str, plugin_version: str | None, existing: str,
    run_id: str | None = None,
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

    When the entry carries a ``run_id`` its short form is always rendered
    (``## YYYY-MM-DD (vX.Y.Z, run <id>)``): ``HH:MM`` cannot separate runs that
    share a minute (#317), and the run id is unique per run. Two distinct ids can
    still share the short suffix, so on a clash the full run id is rendered, and
    a ``#N`` counter follows if even that heading exists. Without a run id the
    legacy behaviour above is unchanged.
    """
    parts = []
    if plugin_version:
        parts.append(f"v{plugin_version}")
    if run_id:
        parts.append(f"run {_short_run_id(run_id)}")
    base = f"## {run_date} ({', '.join(parts)})" if parts else f"## {run_date}"
    if base not in existing:
        return base
    if run_id:
        # A short id is unique per run in practice, but two ids can share an
        # 8-hex suffix: fall back to the full run id, then a counter, so the
        # heading is unique by construction rather than by probability.
        full = base.replace(f"run {_short_run_id(run_id)}", f"run {run_id}", 1)
        candidate, n = full, 2
        while _heading_exists(candidate, existing):
            candidate = f"{full[:-1]} #{n})"
            n += 1
        return candidate
    # Already an entry with this exact heading - disambiguate with time.
    stamp = datetime.now().strftime("%H:%M")
    return f"{base[:-1]} {stamp})" if parts else f"{base} {stamp}"


def _heading_exists(heading: str, existing: str) -> bool:
    """True when ``heading`` is already a whole line of ``existing``."""
    return heading in existing.splitlines()


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


# --- log entry targeting and re-chain (issue #355) ----------------------------
#
# The core writes each entry with placeholders the LLM finalize fills later. An
# entry that still carries LOG_PLACEHOLDER belongs to a run that was never
# finalized. Entries are addressed by the ``assess:run_id`` stamp they carry, and
# any in-place change goes through ``rewrite_log_entry`` so the chain markers of
# the changed entry and every later one are recomputed: an edit made by the tool
# itself must not read as tampering on the next verify.
LOG_PLACEHOLDER = "(LLM fills in)"
_RUN_ID_STAMP_RE = re.compile(r"<!-- assess:run_id=(\S+) ")
_HEADING_DATE_RE = re.compile(r"^## (\d{4}-\d{2}-\d{2})", re.MULTILINE)


def log_entry_is_unfinalized(content: str) -> bool:
    """True when a log entry still carries the core's unfilled placeholders."""
    return LOG_PLACEHOLDER in content


def log_entry_run_id(content: str) -> str | None:
    """The run id an entry's ``assess:run_id`` stamp names, or None (legacy)."""
    m = _RUN_ID_STAMP_RE.search(content)
    return m.group(1) if m else None


def log_entry_owns_span(content: str, run_id: str) -> bool:
    """True when the entry text begins with ``run_id``'s own stamp.

    On a log written before the chain existed, the unchained legacy body and the
    first chained entry parse as one span (see ``_chain_tail``). Such a span
    carries the run's stamp but not at its start; removing or replacing it would
    take the whole legacy history with it, so callers that drop an entry require
    this to hold.
    """
    return content.startswith(f"<!-- assess:run_id={run_id} ")


def log_entry_date(content: str) -> str | None:
    """The ``YYYY-MM-DD`` date of an entry's first ``## `` heading, or None."""
    m = _HEADING_DATE_RE.search(content)
    return m.group(1) if m else None


def read_log_entries(assess_dir: Path) -> list[str]:
    """The entry texts of log.md in file order (chain markers stripped).

    An index into this list is what ``rewrite_log_entry`` takes. A legacy log
    with no chain markers reads as a single entry.
    """
    log_path = assess_dir / "log.md"
    if not log_path.exists():
        return []
    return [c for c, _ in _parse_log_entries(log_path.read_text(encoding="utf-8"))]


def find_log_entry(assess_dir: Path, run_id: str) -> int | None:
    """Index of the log entry stamped with ``run_id``, or None."""
    for i, content in enumerate(read_log_entries(assess_dir)):
        if log_entry_run_id(content) == run_id:
            return i
    return None


def rewrite_log_entry(assess_dir: Path, index: int, new_content: str | None) -> None:
    """Replace (or, with ``None``, remove) log entry ``index`` and re-chain.

    The chain is recomputed from the entry's predecessor: the rewritten entry and
    every later one get fresh markers. Only entries whose stored marker verified
    before the rewrite are re-stamped; the walk stops at the first entry that was
    already broken, so a pre-existing break stays detectable rather than being
    blessed by the re-chain.
    """
    log_path = assess_dir / "log.md"
    text = log_path.read_text(encoding="utf-8")
    entries = _parse_log_entries(text)
    # Local validity before the rewrite: entry k verifies against entry k-1 alone.
    prev = _GENESIS
    was_valid: list[bool] = []
    for content, stored in entries:
        was_valid.append(stored is None or stored == _chain_hash(prev, content))
        prev = stored if stored is not None else _chain_hash(prev, content)
    if new_content is None:
        del entries[index]
        del was_valid[index]
    else:
        entries[index] = (new_content, entries[index][1])
    out: list[str] = []
    prev = _GENESIS
    rechaining = True
    for k, (content, stored) in enumerate(entries):
        if k >= index and rechaining:
            if not was_valid[k]:
                rechaining = False
            elif stored is not None:
                stored = _chain_hash(prev, content)
        out.append(content if stored is None else f"{content}<!-- chain:{stored} -->\n")
        prev = stored if stored is not None else _chain_hash(prev, content)
    header = _LOG_HEADER if text.startswith(_LOG_HEADER) else ""
    log_path.write_text(header + "".join(out), encoding="utf-8")


def last_log_entry_is_unfinalized_run(assess_dir: Path, run_id: str) -> bool:
    """True when the log's last entry is ``run_id``'s own and still unfinalized.

    This is the condition under which ``supersede_unfinalized_log_entry`` acts,
    exposed so the core can learn before it writes the wiki that the previous
    run was never finalized (#356).
    """
    return _last_entry_is_unfinalized_run(read_log_entries(assess_dir), run_id)


def _last_entry_is_unfinalized_run(entries: list[str], run_id: str) -> bool:
    if not entries:
        return False
    last = entries[-1]
    return log_entry_owns_span(last, run_id) and log_entry_is_unfinalized(last)


def supersede_unfinalized_log_entry(assess_dir: Path, run_id: str) -> bool:
    """Remove the last log entry when it is ``run_id``'s and still unfinalized.

    The caller decides the run is superseded (same date, same measured commit);
    this only acts when the log's last entry is that run's and carries unfilled
    placeholders. A finalized entry, or any entry that is not the last, is never
    removed, and neither is a span that also holds unchained legacy history.
    Returns True when an entry was removed.
    """
    entries = read_log_entries(assess_dir)
    if not _last_entry_is_unfinalized_run(entries, run_id):
        return False
    rewrite_log_entry(assess_dir, len(entries) - 1, None)
    return True


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
        run_id=entry.run_id,
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
    briefing: str,
    actions: str,
    accretion_data: dict | None = None,
    run_id: str | None = None,
    schema_version: str | None = None,
    max_fn_ccn: float | None = None,
    max_fn_name: str | None = None,
    superseded_run_id: str | None = None,
) -> None:
    """(Re)write hotspots/<slug>.md, keeping the page's run history.

    The history table compounds across runs (#421): the rows already on the
    page are read back, this run's row (``last_seen``, the short run id and
    the current metrics) is appended, and the rows are ordered by run date.
    Only a row carrying this run's date *and* run id is replaced, so a re-run
    of the same run never duplicates its row while two distinct runs on one
    day both stay. ``superseded_run_id`` names a never-finalized run this one
    replaces; its row is dropped, matching the log entry the core drops.

    ``ccn`` is the file aggregate (the sum over the file's functions).
    ``max_fn_ccn`` / ``max_fn_name`` are the worst single function from the
    stats sidecar; the page shows that row only when both are present (#423).

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
    page_path = hotspots_dir / f"{slug_for_path(path)}.md"
    existing = page_path.read_text(encoding="utf-8") if page_path.exists() else ""
    run_cell = _short_run_id(run_id) if run_id else "-"
    new_row = [last_seen, run_cell, str(loc), str(ccn), str(commits), status]
    drop = {_short_run_id(superseded_run_id)} if superseded_run_id else set()
    history = merge_history_rows(parse_history_rows(existing), new_row, drop_runs=drop)
    content = _load_template("hotspot.md.template").format(
        path=path,
        first_flagged=first_flagged,
        last_seen=last_seen,
        status=status,
        loc=loc,
        ccn=ccn,
        commits=commits,
        has_tests=has_tests_str,
        worst_fn_row=_worst_fn_row(max_fn_ccn, max_fn_name),
        history_rows="\n".join(_render_row(r) for r in history),
        briefing=briefing,
        actions=actions,
    )
    page_path.write_text(
        _run_id_comment(run_id, schema_version) + content, encoding="utf-8"
    )


def _worst_fn_row(max_fn_ccn: float | None, max_fn_name: str | None) -> str:
    """The "Worst function" metrics row, or "" when the sidecar has no
    per-function data for the file (scc-scored files carry nulls)."""
    if max_fn_ccn is None or not max_fn_name:
        return ""
    return f"| Worst function | `{max_fn_name}` ({max_fn_ccn}) |\n"


# --- hotspot page history table (#421) ----------------------------------------
#
# Columns: Run date | Run | LOC | CCN | Commits | Status. ``Run`` is the short run
# id (the same tail log.md headings use), or "-" when the writer had none. Pages
# written before the Run column existed carry five cells; they are read with
# "-" in the Run position so their rows survive the upgrade.
_HISTORY_HEADING = "## History across runs"
_HISTORY_COLUMNS = 6


def _split_row(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _render_row(cells: list[str]) -> str:
    return "| " + " | ".join(cells) + " |"


def parse_history_rows(content: str) -> list[list[str]]:
    """The data rows of a hotspot page's history table, as 6-cell lists.

    Returns [] when the page has no history section. Header, separator and
    malformed rows are skipped.
    """
    start = content.find(_HISTORY_HEADING)
    if start == -1:
        return []
    rows: list[list[str]] = []
    in_table = False
    for line in content[start + len(_HISTORY_HEADING):].splitlines():
        if not line.startswith("|"):
            if in_table:
                break  # the table ended
            continue
        in_table = True
        cells = _split_row(line)
        if cells and (cells[0] == "Run date" or set(cells[0]) <= set("-:")):
            continue  # header or separator
        if len(cells) == _HISTORY_COLUMNS - 1:
            cells.insert(1, "-")  # legacy row from before the Run column
        if len(cells) == _HISTORY_COLUMNS:
            rows.append(cells)
    return rows


def merge_history_rows(
    rows: list[list[str]], new_row: list[str], *, drop_runs: set[str] | None = None,
) -> list[list[str]]:
    """Append ``new_row`` to ``rows``, ordered by run date (stable).

    A row with the same run date and run id as ``new_row`` is replaced; a row
    whose run id is in ``drop_runs`` is removed. Every other row is kept.
    """
    drop = drop_runs or set()
    kept = [
        r for r in rows
        if (r[0], r[1]) != (new_row[0], new_row[1])
        and not (r[1] != "-" and r[1] in drop)
    ]
    return sorted([*kept, new_row], key=lambda r: r[0])


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
# A file first flagged by a run that was never finalized, then excluded by
# `.assess/config.toml` before the superseding run (#356). The file may still be
# on disk, so this is a separate wording; every retired status begins "retired".
RETIRED_EXCLUDED_STATUS = "retired - excluded before finalize"
_RETIRED_PREFIX = "retired"

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
        if is_retired_status(hotspot_page_status(content)):
            continue  # already retired (for any reason) - idempotent
        if (repo_root / path).exists():
            continue  # source still on disk - a legitimate hotspot, untouched
        _stamp_retired(page, content, RETIRED_STATUS, (
            "the source file was absent from disk at the latest "
            "run (deleted, moved, or renamed). This page is preserved for history "
            "and no longer describes a live file."
        ))
        retired.append(path)
    return sorted(retired)


def retire_excluded_hotspots(
    assess_dir: Path, paths: list[str],
) -> tuple[list[str], list[str]]:
    """Stamp the pages of ``paths`` retired as excluded before finalize (#356).

    The caller picks the paths: excluded by config and first flagged only by a
    run that was never finalized. Returns ``(retired, unstamped)``, both sorted:
    the paths whose page this call retired, and the paths whose page exists but
    carries no status token to stamp (left as-is, so the caller can keep their
    first-flagged entries). A path with no page, or whose page is already
    retired, is in neither list.
    """
    retired: list[str] = []
    unstamped: list[str] = []
    for path in sorted(set(paths)):
        page = assess_dir / "hotspots" / f"{slug_for_path(path)}.md"
        if not page.exists():
            continue
        content = page.read_text(encoding="utf-8")
        status = hotspot_page_status(content)
        if status is None:
            unstamped.append(path)
            continue
        if is_retired_status(status):
            continue
        _stamp_retired(page, content, RETIRED_EXCLUDED_STATUS, (
            "this file was first flagged by a run that was never finalized and "
            "is now excluded by `.assess/config.toml`. This page is preserved for "
            "history and no longer describes a live hotspot."
        ))
        retired.append(path)
    return retired, unstamped


def is_retired_status(status: str | None) -> bool:
    """True for any retired status token: every one begins with ``retired``."""
    return status is not None and status.startswith(_RETIRED_PREFIX)


def _stamp_retired(page: Path, content: str, status: str, reason: str) -> None:
    """Flip the page's status token to ``status`` and add a retirement banner."""
    banner = f"\n> **Retired:** {reason}"
    stamped = _HOTSPOT_STATUS_RE.sub(
        lambda m: f"{m.group('prefix')}{status}{m.group('suffix')}{banner}",
        content, count=1,
    )
    page.write_text(stamped, encoding="utf-8")
