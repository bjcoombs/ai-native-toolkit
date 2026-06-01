"""Deterministic report renderer for /assess.

Reads ``.assess/run-context.json`` and renders a Markdown report from a stdlib
``string.Template``. This is the *frozen harness*: it runs with zero model
tokens and produces the same report for the same context, on every PR forever.

It renders:
- a metrics dashboard (complexity profile, churn window, total LOC),
- the top-hotspots table,
- the keyhole-readiness summary + the six cross-layer findings, and
- the regression deltas (graduated / new / regressed / persistent).

It deliberately does NOT reproduce (these require LLM judgement and are written
elsewhere by the orchestrator, see SKILL.md):
- the 0-8 layered readiness score,
- the per-layer present/partial/missing prose, or
- the Top 3 Actions priority narrative.

The findings section, the keyhole summary, and the prescribed actions are
already serialised into ``run-context.json`` by the deterministic core
(``assess_core.build_run_context`` -> ``keyhole_signals``). This renderer
*consumes* those products verbatim; it does not re-derive them.

Run:
    uv run assess_report.py <repo_root>            # writes .assess/deterministic-report.md
    uv run assess_report.py <repo_root> --stdout   # prints the report to stdout
"""
# /// script
# requires-python = ">=3.11"
# ///
from __future__ import annotations

import json
import sys
from pathlib import Path
from string import Template
from typing import Any

# Caps on rows rendered so a pathological repo can't bloat the frozen report.
# The structured arrays in run-context.json keep the full lists.
MAX_HOTSPOT_ROWS = 10
MAX_DIFF_ROWS = 10

# Diff categories rendered in order, with the gloss shown beside each count.
DIFF_CATEGORIES = [
    ("graduated", "Graduated", "left the hotspot list"),
    ("new", "New", "entered the hotspot list"),
    ("regressed", "Regressed", "complexity or churn increased"),
    ("persistent", "Persistent", "still in the hotspot list"),
]

REPORT_TEMPLATE = Template("""# Deterministic Assessment Snapshot: $repo_name

_Generated $run_date by `/assess` v$plugin_version.${commit_note}_

## Metrics Dashboard

- **Files scored:** $files_scored
- **Total LOC:** $loc_total
- **Complexity profile:** p95 LOC $loc_p95 (max $loc_max), p95 CCN $ccn_p95 (max $ccn_max)
- **Churn window:** $churn_window

### Top Hotspots

$hotspots_table

## Keyhole Readiness

$keyhole_summary

$findings_section

## Changes Since Last Run

$diff_section

---

_Deterministic portion of the assessment: metrics, hotspots, and cross-layer findings. The 0-8 layer scores, the per-layer prose, and the Top 3 Actions priority narrative require LLM judgement and are written separately._
""")


def _fmt(value: Any) -> str:
    """Format a metric for display: ints as ints, whole floats without a
    trailing ``.0``, fractional floats to one decimal, ``None`` as ``?``."""
    if value is None:
        return "?"
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, float):
        return str(int(value)) if value.is_integer() else f"{value:.1f}"
    return str(value)


def render_hotspots_table(ctx: dict) -> str:
    """Render the top hotspots as a Markdown table (capped at MAX_HOTSPOT_ROWS)."""
    hotspots = ctx.get("stats_summary", {}).get("top_hotspots", [])
    if not hotspots:
        return "_No hotspots identified._"
    lines = ["| Path | LOC | CCN | Commits |", "|------|-----|-----|---------|"]
    for h in hotspots[:MAX_HOTSPOT_ROWS]:
        lines.append(
            f"| `{h.get('path', '?')}` | {_fmt(h.get('loc'))} | "
            f"{_fmt(h.get('ccn'))} | {_fmt(h.get('commits'))} |"
        )
    return "\n".join(lines)


def render_keyhole_summary(ctx: dict) -> str:
    """Return the pre-built keyhole-readiness summary line (consumed verbatim)."""
    summary = ctx.get("keyhole_summary") or {}
    text = summary.get("summary_text")
    return text if text else "_Keyhole readiness summary unavailable._"


def render_findings_section(ctx: dict) -> str:
    """Return the pre-rendered cross-layer findings section, verbatim.

    The deterministic core already renders the six findings + attention list
    into ``findings_markdown``; this renderer copies it so the section appears
    whether or not an LLM is in the loop.
    """
    markdown = ctx.get("findings_markdown")
    if not markdown or not markdown.strip():
        return "_No cross-layer findings recorded._"
    return markdown.strip()


def _format_transition(category: str, entry: dict) -> str:
    """Render one hotspot transition as a bullet, with deltas for regressions."""
    path = entry.get("path", "?")
    if category != "regressed":
        return f"  - `{path}`"
    bits = []
    ccn_delta = entry.get("ccn_delta", 0)
    loc_delta = entry.get("loc_delta", 0)
    if ccn_delta:
        bits.append(f"CCN {ccn_delta:+d}")
    if loc_delta:
        bits.append(f"LOC {loc_delta:+d}")
    suffix = f" ({', '.join(bits)})" if bits else ""
    return f"  - `{path}`{suffix}"


def render_diff_section(ctx: dict) -> str:
    """Render the regression deltas, honouring the first-run and reliability flags.

    No prior snapshot -> say so. An unreliable diff (plugin-version mismatch in
    the file filter) is suppressed with its note rather than shown, mirroring the
    SKILL.md rule that phantom transitions must not read as real improvement.
    """
    if not ctx.get("prior_stats_exists"):
        return "_No prior run to compare against - this is the first recorded snapshot._"
    if not ctx.get("diff_reliable", True):
        note = ctx.get("diff_version_note") or "prior and current snapshots are not comparable"
        return f"_Diff suppressed: {note}._"

    summary = ctx.get("diff", {})
    detail = ctx.get("diff_detail", {})
    lines: list[str] = []
    for key, label, gloss in DIFF_CATEGORIES:
        entries = detail.get(key, [])
        count = summary.get(key, len(entries))
        lines.append(f"- **{label}** ({gloss}): {count}")
        for entry in entries[:MAX_DIFF_ROWS]:
            lines.append(_format_transition(key, entry))
        overflow = len(entries) - MAX_DIFF_ROWS
        if overflow > 0:
            lines.append(f"  - ...and {overflow} more")
    return "\n".join(lines)


def _render_commit_note(ctx: dict) -> str:
    """Render the measured-commit provenance suffix for the generated-by line.

    Pins the SHA the absolute LOC/CCN numbers were measured at and warns when
    HEAD is dirty or behind upstream, so the figures aren't read as current
    when they describe an uncommitted or stale tree. Returns a leading-space
    string (it follows ``v<version>.``) or ``""`` when provenance is unknown.
    """
    commit = ctx.get("measured_commit") or {}
    if not commit.get("available"):
        return ""
    short = commit.get("head_short") or str(commit.get("head_sha", ""))[:7]
    note = f" Measured at `{short}`"
    subject = commit.get("subject")
    if subject:
        note += f' ("{subject}")'
    warnings = []
    if commit.get("dirty"):
        warnings.append("working tree dirty")
    behind = commit.get("behind")
    if isinstance(behind, int) and behind > 0:
        warnings.append(f"{behind} commit(s) behind upstream")
    if warnings:
        note += f" - {', '.join(warnings)}"
    return note + "."


def _render_churn_window(ctx: dict) -> str:
    """Surface the churn-window label the treemap/doc-staleness pass settled on."""
    doc_staleness = ctx.get("doc_staleness") or {}
    window = doc_staleness.get("churn_window")
    return window if window else "unavailable"


def render_report(ctx: dict, repo_name: str) -> str:
    """Render the full deterministic Markdown report from a run-context dict.

    Pure: takes the loaded context and the repo name, returns the report string.
    ``Template.substitute`` is strict - a missing key raises, which surfaces a
    template/data drift at the seam rather than silently emitting ``$placeholder``.
    """
    stats = ctx.get("stats_summary", {})
    loc = stats.get("loc", {})
    ccn = stats.get("ccn", {})
    report = REPORT_TEMPLATE.substitute(
        repo_name=repo_name,
        run_date=ctx.get("run_date", "unknown"),
        plugin_version=ctx.get("plugin_version", "unknown"),
        commit_note=_render_commit_note(ctx),
        files_scored=stats.get("files_scored", 0),
        loc_total=_fmt(loc.get("total")),
        loc_p95=_fmt(loc.get("p95")),
        loc_max=_fmt(loc.get("max")),
        ccn_p95=_fmt(ccn.get("p95")),
        ccn_max=_fmt(ccn.get("max")),
        churn_window=_render_churn_window(ctx),
        hotspots_table=render_hotspots_table(ctx),
        keyhole_summary=render_keyhole_summary(ctx),
        findings_section=render_findings_section(ctx),
        diff_section=render_diff_section(ctx),
    )
    return report.rstrip() + "\n"


def load_context(repo_root: Path) -> dict:
    """Load ``.assess/run-context.json`` from a repo root."""
    ctx_path = repo_root / ".assess" / "run-context.json"
    return json.loads(ctx_path.read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    to_stdout = "--stdout" in args
    positional = [a for a in args if not a.startswith("-")]
    if not positional:
        print("Usage: assess_report.py <repo_root> [--stdout]", file=sys.stderr)
        return 2
    repo_root = Path(positional[0]).resolve()
    ctx = load_context(repo_root)
    report = render_report(ctx, repo_root.name)
    if to_stdout:
        sys.stdout.write(report)
    else:
        out_path = repo_root / ".assess" / "deterministic-report.md"
        out_path.write_text(report, encoding="utf-8")
        print(str(out_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
