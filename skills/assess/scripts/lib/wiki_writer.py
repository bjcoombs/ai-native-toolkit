"""Render and write the .assess/ wiki files from templates.

No LLM calls. Pure string formatting + file IO. Deterministic.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path


# Templates live alongside the scripts/lib/ package, one directory up under templates/
_TEMPLATES_DIR = Path(__file__).resolve().parents[2] / "templates"


@dataclass(frozen=True)
class HotspotEntry:
    path: str
    first_flagged: str
    last_seen: str
    status: str   # active | graduated | regressed | persistent
    ccn: int
    loc: int


@dataclass(frozen=True)
class LogEntry:
    run_date: str
    files_scored: int
    readiness_score: float
    maturity_label: str
    instructions_grade: str
    graduated_count: int
    regressed_count: int
    new_count: int
    persistent_count: int
    top_action: str
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
        rows.append(
            f"| `{e.path}` | {e.first_flagged} | {e.last_seen} | {e.status} | {e.ccn} | {e.loc} |"
        )
    content = _load_template("index.md.template").format(
        last_updated=last_updated,
        hotspot_rows="\n".join(rows) if rows else "| _no hotspots tracked yet_ | | | | | |",
    )
    (assess_dir / "index.md").write_text(content, encoding="utf-8")


def append_log_entry(assess_dir: Path, entry: LogEntry) -> None:
    """Append a dated entry to log.md (create the file if absent)."""
    log_path = assess_dir / "log.md"
    snippet = _load_template("log_entry.md.template").format(
        run_date=entry.run_date,
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
    if log_path.exists():
        log_path.write_text(log_path.read_text(encoding="utf-8") + snippet, encoding="utf-8")
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
