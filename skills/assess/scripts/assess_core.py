"""Orchestrator for the deterministic core of /assess.

Reads:
    {repo_root}/.assess/complexity-stats.json       (current run)
    {repo_root}/.assess/complexity-stats.prior.json (if it exists)
    {repo_root}/CLAUDE.md, AGENTS.md, GEMINI.md, .cursorrules, .github/copilot-instructions.md (any that exist)

Writes (from ``main`` / ``build_and_publish_run``; ``build_run_context`` alone
writes nothing):
    {repo_root}/.assess/run-context.json   (everything the LLM needs)
    {repo_root}/.assess/index.md           (regenerated each run)
    {repo_root}/.assess/log.md             (appended each run)
    {repo_root}/.assess/hotspots/*.md      (one per top hotspot)
    {repo_root}/.assess/badge.json, findings.json

Run:
    uv run assess_core.py <repo_root>

The LLM still writes assess-report.md (the prose-heavy summary).
The LLM reads run-context.json to ground that prose in deterministic data.
"""
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "networkx",
#     "grimp",
# ]
# ///
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

# Make sibling lib package importable when run as a script
sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib.assess_diagrams import build_diagrams, write_diagrams
from lib.badge import write_headline_badge
from lib.headline import as_headline, run_context_headline
from lib.counts_only import COUNTS_ONLY_FILE
from lib.counts_only import add_cli_arguments as add_counts_only_arguments
from lib.counts_only import apply_cli as apply_counts_only, validate_cli_args as validate_counts_only_args
from lib.coverage_report import load_coverage_data
from lib.level_tally import run_context_level
from lib.mutation_cap import mutation_not_run_cap, normalize_test_pressure
from lib.mutation_refresh import recorded_excludes, refresh_mutation_findings
from lib.run_publish import publish_run
from lib.run_wiki import refresh_log_readiness
from lib.run_scope import assess_dir_for, load_current_stats, resolve_scope
from lib.scan_registry import run_scans
from lib.scan_registry import safe as _safe
from lib.test_focus import mutation_scope
from lib.test_pressure import scan_test_pressure


def build_run_context(
    *, repo_root: Path, run_date: str, non_interactive: bool = False,
    scope: Path | None = None,
) -> dict[str, Any]:
    """Run the deterministic pipeline and return the structured context dict.

    ``non_interactive`` is the orchestrator's explicit headless/CI signal; it
    (together with the ``CI`` / ``ASSESS_NON_INTERACTIVE`` env vars) decides the
    ``interactive`` flag and the pre-recorded ``offers``. It is never inferred
    from ``sys.stdin.isatty()`` - the core always runs as a subprocess with no
    controlling terminal, so an interactive /assess would misread as headless.

    ``scope`` restricts the assessment to a subtree (``/assess <path>`` monorepo
    scoping): artifacts land under ``.assess/<slug>/`` and the file-enumerating
    scans (complexity stats read from the scoped sidecar, doc graph, doc
    staleness) see only the subtree, so the score/badge/wiki carry no signal
    from a sibling directory. None (the default) is a whole-repo run, unchanged.

    Every block comes from the declared table in ``lib/scan_registry.py``. The
    table only reads, so this function has no file-system side effects: it
    creates no ``.assess/`` directory and writes no artifact. The publish step
    (index.md, log.md, hotspots/*.md, badge.json, findings.json and
    run-context.json, through ``lib.run_publish.publish_run``) is
    ``build_and_publish_run``'s, which ``main`` calls.
    """
    return _scan(repo_root, run_date, non_interactive, scope)[0]


def build_and_publish_run(
    *, repo_root: Path, run_date: str, non_interactive: bool = False,
    scope: Path | None = None,
) -> dict[str, Any]:
    """``build_run_context``, then the publish step that writes the artifacts.

    The CLI run: ``publish_run`` writes the wiki, badge, findings snapshot and
    run-context.json, and fills the context keys only it can know
    (``pruned_hotspots``, ``log_integrity``, ``prior_findings_snapshot``, ...).
    """
    ctx, scan_inputs = _scan(repo_root, run_date, non_interactive, scope)
    publish_run(ctx, scan_inputs)
    return ctx


def _scan(
    repo_root: Path, run_date: str, non_interactive: bool, scope: Path | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Run the scan table; return the context and the inputs it filled."""
    scan_inputs: dict[str, Any] = {
        "repo_root": repo_root, "requested_scope": scope,
        "run_date": run_date, "non_interactive": non_interactive,
    }
    ctx: dict[str, Any] = {}
    run_scans(ctx, scan_inputs)
    return ctx, scan_inputs


def run_opt_in_mutation(repo_root: Path, scope: Path | None = None) -> int:
    """Re-run the Layer-1 test-pressure scan with the bounded mutation pass ON.

    The consent-gated counterpart to the default read-only scan in
    ``build_run_context``. The orchestrator (SKILL.md Step 2d) calls this only
    after the user accepts the mutation offer - it mutates and *runs* code, so it
    is never part of the default pass. It does not recompute the whole context:
    it reads the existing ``run-context.json``, takes the focus targets that carry
    test evidence from the ``test_focus`` block (``lib.test_focus.mutation_scope``),
    runs ``scan_test_pressure(..., opt_in=True)`` scoped to them, and rewrites the
    ``test_pressure`` block in place along with every block derived from it: the
    Layer 6 ``mutation_not_run_cap``, the ``level`` tally with the ``headline`` and
    ``badge.json`` worded from it, the E1 ``untrusted_hotspot`` finding in
    ``derived_findings``, and the products ranked or rendered from the findings
    (``attention``, ``attention_low_signal``, ``findings_markdown``,
    ``keyhole_summary``, ``prescribed_actions``, the archive and config-exclude
    disclosures), through ``lib.mutation_refresh``. Blocks that
    do not read ``test_pressure`` (``test_focus``, ``gap_actions``) are kept.
    ``run_bounded_mutation`` itself caps the scope at ``MAX_FILES_TO_MUTATE``, so
    passing every focus path is safe.

    The treemap overlay (regenerated separately by the orchestrator) then reads
    the refreshed ``test_pressure.per_file`` so covered-but-unpinned files get
    hatched. Never raises beyond argparse/IO; degrades to a non-zero return.
    """
    try:
        _scope_abs, _scope_rel, scope_slug = resolve_scope(repo_root, scope)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    assess_dir = repo_root / ".assess" / scope_slug if scope_slug else repo_root / ".assess"
    ctx_path = assess_dir / "run-context.json"
    if not ctx_path.exists():
        print("run-context.json not found - run assess_core.py first",
              file=sys.stderr)
        return 1
    try:
        ctx = json.loads(ctx_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        print(f"could not read run-context.json: {e}", file=sys.stderr)
        return 1

    # Only focus entries with test evidence: mutating a file with no test yields
    # all survivors and would flag it for strengthening tests that do not exist.
    focus_files = mutation_scope(ctx.get("test_focus"))
    if not focus_files:
        print("no test_focus targets with test evidence - nothing to mutate",
              file=sys.stderr)
        return 0

    coverage_data = load_coverage_data(repo_root)
    test_pressure = _safe(
        "test_pressure",
        lambda: scan_test_pressure(repo_root, hot_files=focus_files, opt_in=True,
                                   coverage_data=coverage_data),
    )
    ctx["test_pressure"] = normalize_test_pressure(test_pressure)
    # Refresh the Layer 6 cap: the mutation tier may have run this pass, lifting
    # the ceiling to Present. Written back so a subsequent finalize sees it.
    ctx["mutation_not_run_cap"] = mutation_not_run_cap(ctx["test_pressure"])
    # The tally reads mutation_run (Layer 6 level 2, #744): recount it in place.
    # The headline and the badge carry the tally, so they are rewritten with it (#747).
    if "level" in ctx:
        ctx["level"] = run_context_level(ctx)
        ctx["headline"] = run_context_headline(ctx["level"])
        write_headline_badge(
            assess_dir, as_headline(ctx["headline"]), run_id=ctx.get("run_id"),
            scope=ctx.get("scope"),
        )
        refresh_log_readiness(
            assess_dir, ctx.get("run_id"), as_headline(ctx["headline"])["summary"],
        )
        # The evidence waffle draws the level's conditions (#942): redraw it with them.
        write_diagrams(assess_dir, build_diagrams(ctx))
    # E1 crosses the refreshed survivor density with this run's hotspots; the
    # findings, attention and report products are rebuilt from it.
    refresh_mutation_findings(
        ctx, load_current_stats(assess_dir), *recorded_excludes(ctx, repo_root),
    )
    ctx_path.write_text(json.dumps(ctx, indent=2), encoding="utf-8")

    tp = ctx["test_pressure"]
    print(json.dumps({
        "mutation_run": tp.get("mutation_run", False),
        "mutation_scope": tp.get("mutation_scope", []),
        "mutation_note": tp.get("mutation_note"),
    }))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Deterministic core for /assess; writes run-context.json.",
    )
    parser.add_argument("repo_root", help="Path to the repo root to assess.")
    parser.add_argument(
        "--opt-in-mutation",
        action="store_true",
        help=(
            "Re-run only the Layer-1 test-pressure scan with the bounded "
            "mutation pass enabled, scoped to the existing run-context.json "
            "test_focus targets, and rewrite the test_pressure block and the "
            "findings, attention and report products derived from it in place. "
            "Requires a prior default run. Mutates and runs code - consent-gated."
        ),
    )
    parser.add_argument(
        "--non-interactive",
        action="store_true",
        help=(
            "Mark this a headless/CI run: no human can answer, so every consent "
            "offer is pre-recorded as skipped and the orchestrator makes zero "
            "prompts. Set this (or the ASSESS_NON_INTERACTIVE / CI env vars) only "
            "on a genuinely headless path; a normal interactive /assess omits it. "
            "Interactivity is never inferred from subprocess stdin."
        ),
    )
    parser.add_argument(
        "--scope", type=Path, metavar="SUBDIR",
        help=(
            "Scope the assessment to a subtree (`/assess <path>` monorepo "
            "scoping). A path under the repo root; the metrics, headline, badge, "
            "wiki, and artifacts are all computed for and labelled with the "
            "scope, and land under `.assess/<scope-slug>/`. The scoped scans "
            "carry no signal from a sibling directory. Omit for a whole-repo "
            "run (unchanged). Pass the same `--scope` to `--opt-in-mutation`."
        ),
    )
    add_counts_only_arguments(parser)
    parsed = parser.parse_args(argv)
    validate_counts_only_args(parser, parsed)
    repo_root = Path(parsed.repo_root).resolve()
    if parsed.opt_in_mutation:
        return run_opt_in_mutation(repo_root, scope=parsed.scope)
    run_date = datetime.now().strftime("%Y-%m-%d")
    try:
        ctx = build_and_publish_run(
            repo_root=repo_root, run_date=run_date,
            non_interactive=parsed.non_interactive,
            scope=parsed.scope,
        )
    except ValueError as e:
        # Invalid --scope (missing path or outside the repo). Report cleanly
        # with a non-zero exit rather than a traceback.
        print(f"error: {e}", file=sys.stderr)
        return 2
    content_free = apply_counts_only(ctx, parsed)
    if content_free is not None:
        out = assess_dir_for(repo_root, ctx.get("scope_slug") or "") / COUNTS_ONLY_FILE
        out.write_text(json.dumps(content_free, indent=2), encoding="utf-8")
    print(json.dumps(ctx["diff"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
