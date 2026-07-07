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

# Caps on listed empty-ownership patterns so a pathologically broken ownership
# map can't bloat the frozen report. The full list stays in run-context.json.
MAX_EMPTY_PATTERNS_RENDERED = 20

# Tier 1 grouping-disagreement metrics, in render order. Each row is the
# run-context key stem (the ``_count`` suffix is added at read time), a
# human label, and the one-line interpretation - the disagreement (or
# agreement) the metric measures between the declared boundary, the static
# import graph, and the co-change history. The objective count is rendered
# first, the interpretation second (SKILL.md deterministic-findings rule).
TIER1_METRICS = [
    ("human_grouped_static_splits",
     "declared together, import graph splits them",
     "a boundary the dependency structure no longer backs"),
    ("human_split_static_fuses",
     "import graph fuses them, no declared boundary does",
     "cohesion the ownership map misses"),
    ("human_grouped_never_cochange",
     "declared together, commit log never couples them",
     "a boundary history does not exercise as a unit"),
    ("human_split_but_cochange",
     "keep co-changing, no declared boundary groups them",
     "a hidden seam the map omits (folded into hidden_coupling above)"),
    ("human_static_agree",
     "declared and dependency lenses agree",
     "boundary backed by the import graph"),
    ("human_cochange_agree",
     "declared and historical lenses agree",
     "boundary exercised as a unit by history"),
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
$structure_drift_section
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


def _render_tier0_drift(tier_0: dict) -> list[str]:
    """Tier 0 ownership-map drift: declared globs matching zero tracked files.

    Each empty pattern is a slice of the tree the ownership map *claims* to
    cover but no file satisfies, so any edit there silently skips the declared
    owner's review. Lists the pattern, where it was declared, and the owners it
    would have routed to. Returns ``[]`` (caller omits the section) when Tier 0
    is unavailable or no pattern is empty - absence reads as "nothing stale",
    never a broken half-section.
    """
    if not tier_0.get("available"):
        return []
    patterns = tier_0.get("empty_ownership_patterns") or []
    if not patterns:
        return []
    ordered = sorted(
        patterns,
        key=lambda p: (p.get("pattern", ""), p.get("declared_in", "")),
    )
    lines = [
        "### Tier 0 - Ownership Map Drift",
        "",
        (f"{len(patterns)} declared ownership pattern"
         f"{'' if len(patterns) == 1 else 's'} match zero tracked files. "
         "Stale ownership silently drops review coverage: an edit under one of "
         "these patterns routes to no declared owner."),
        "",
    ]
    for p in ordered[:MAX_EMPTY_PATTERNS_RENDERED]:
        owners = p.get("owners") or []
        owners_text = ", ".join(owners) if owners else "_no owners declared_"
        lines.append(
            f"- `{p.get('pattern', '?')}` "
            f"(declared in {p.get('declared_in', '?')}; owners: {owners_text})"
        )
    overflow = len(ordered) - MAX_EMPTY_PATTERNS_RENDERED
    if overflow > 0:
        lines.append(f"- ...and {overflow} more")
    lines.append("")
    return lines


def _render_tier1_disagreement(tier_1: dict, repo_name: str) -> list[str]:
    """Tier 1 grouping disagreement: six set-algebra counts over the declared,
    static-import, and co-change groupings, plus the seam-allowlist note.

    The objective counts lead; the interpretation follows each (SKILL.md
    deterministic-findings rule). The hidden-seam direction
    (``human_split_but_cochange``) already folds into the ``hidden_coupling``
    finding above, so this block surfaces the explicit magnitudes and the
    allowlist transparency line rather than re-rendering that finding. Returns
    ``[]`` (caller omits) when the static lens was unavailable.
    """
    if not tier_1.get("available"):
        return []
    lines = [
        "### Tier 1 - Grouping Disagreement",
        "",
        ("Set-algebra over three groupings - the declared boundary, the static "
         "import graph, and the co-change history - counting the file pairs each "
         "lens pair agrees or disagrees on:"),
        "",
    ]
    for key, label, interpretation in TIER1_METRICS:
        count = tier_1.get(f"{key}_count", 0)
        lines.append(f"- **{count}** `{key}` - {label}: {interpretation}")
    lines.append("")
    if tier_1.get("seam_allowlist_applied"):
        n = tier_1.get("allowlist_pairs_count", 0)
        note = (
            f"Seam allowlist applied: {n} owned seam pair"
            f"{'' if n == 1 else 's'} excluded from the disagreement counts "
            "before they were reported."
        )
        if _is_self_assessment(repo_name):
            note += " See `lib/README.md` for the owned seams."
        lines.append(note)
        lines.append("")
    return lines


def _is_self_assessment(repo_name: str) -> bool:
    """True when /assess is assessing its own repo - the only case where the
    ``lib/README.md`` owned-seams footnote points at a file that exists."""
    return repo_name == "ai-native-toolkit"


def format_structure_drift_findings(
    structure_drift: dict | None, repo_name: str = "",
) -> str:
    """Render the run-context ``structure_drift`` block as a Markdown section.

    Two tiers, each independently omitted when it has nothing to say:
    - **Tier 0** lists declared ownership patterns matching no tracked file.
    - **Tier 1** surfaces the six grouping-disagreement counts and the
      seam-allowlist transparency line.

    Counts lead, interpretation follows; the harness never auto-prescribes
    regenerating the ownership map - that is a human decision, stated here only
    so the human can make it. Returns ``""`` (caller omits the heading) when the
    block is absent or both tiers are empty - graceful degrade, no broken
    markdown.
    """
    if not structure_drift:
        return ""
    body: list[str] = []
    body += _render_tier0_drift(structure_drift.get("tier_0") or {})
    body += _render_tier1_disagreement(
        structure_drift.get("tier_1") or {}, repo_name,
    )
    if not body:
        return ""
    return "## Structure Drift\n\n" + "\n".join(body).rstrip() + "\n"


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
        if ctx.get("diff_trend_reset"):
            # A MAJOR version bump broke comparability: state the reset explicitly
            # so a suppressed diff isn't misread as a clean, unchanged run.
            return (
                f"_Trend baseline reset: {note}. Prior hotspot history is not "
                "comparable across a major version; the trend restarts from this "
                "snapshot._"
            )
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
    """Surface the churn-window label the treemap/doc-staleness pass settled on.

    When the history is degenerate (every file ~1 commit - a snapshot with no
    usable history), the label carries a caveat so the churn axis, the saturation
    treemap channel, and any churn-derived finding are read as inactive rather
    than as a live signal (issue #172).
    """
    doc_staleness = ctx.get("doc_staleness") or {}
    window = doc_staleness.get("churn_window")
    label = window if window else "unavailable"
    if ctx.get("churn_degenerate"):
        return f"{label} - snapshot / no usable history, churn signal flat"
    return label


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
        structure_drift_section=_structure_drift_section(ctx, repo_name),
        diff_section=render_diff_section(ctx),
    )
    return report.rstrip() + "\n"


def _structure_drift_section(ctx: dict, repo_name: str) -> str:
    """Template-ready structure-drift block: the rendered section followed by a
    blank line, or ``""`` when there's nothing to render (the surrounding blank
    line in the template then collapses on the final ``rstrip``)."""
    section = format_structure_drift_findings(ctx.get("structure_drift"), repo_name)
    return f"\n{section.rstrip()}\n" if section else ""


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
    try:
        ctx = load_context(repo_root)
    except (OSError, json.JSONDecodeError) as e:
        # Infrastructure failure (the core never wrote run-context.json, or it is
        # corrupt), not a finding. Emit a skip notice and succeed so a broken
        # snapshot never renders a red check on an unrelated PR - the assessment
        # runs again on the next push.
        print(
            f"/assess report skipped: infrastructure failure "
            f"({type(e).__name__}: {e}).\n"
            "This is an infra issue, not a finding. The report will render on "
            "the next push.",
            file=sys.stderr,
        )
        return 0
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
