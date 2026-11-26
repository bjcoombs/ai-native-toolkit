"""The side effects of a run, applied after every scan has finished.

The scan table only reads, so ``assess_core.build_run_context`` writes nothing;
``assess_core.build_and_publish_run`` (the CLI run) calls this step. It writes: the artifact directory, the
``.assess/`` wiki (hotspot pages, index.md, the log.md entry and its checksum
chain, first-flagged.json), the findings badge, the diagrams
(``lib/assess_diagrams.py`` and ``lib/assess_diagrams_review.py``), the committed findings snapshot
(findings.json) and ``run-context.json``. The
wiki's results fill the four keys the run header reserved for them
(``run_setup.WIKI_KEYS``) in place, so their position in the file is unchanged.

The run's side effects, applied after every scan (#557) by
`assess_core.build_and_publish_run`, never by `build_run_context` (#660): `publish_run` creates the
artifact directory, writes the wiki (`run_wiki.write_run_wiki`), fills the four wiki
keys the run header reserved in place (the `log.md` entry opens with the `since_last_run`
block's bullets, #1083), writes `badge.json` from the `headline` block
(`badge.write_headline_badge`, #747; the `log.md` entry's readiness line is its `summary`), reads the previous
`prior-level.json` into `prior_level` (`lib/prior_level.py`, #940) before drawing the diagrams and then
rewrites it from the `level` block (the evidence waffle titles its rows from the run's
model, `profiles.model_rows`, so a practice pack's rows read their titles, #1315), reads the previous
`findings.json` into `prior_findings_snapshot` and rewrites it
(`findings_snapshot`; kept as it is when a `--profile` scan plan left the findings out, #1268; the
hotspot pages then say the promissory-marker scan did not run), and then writes `run-context.json`. Tests: `tests/test_run_setup.py`.
"""
from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from lib.artifact_schema import ASSESS_METHOD_VERSION
from lib.assess_diagrams_review import build_all, write_all
from lib.report.badge import write_headline_badge
from lib.scoring.headline import as_headline
from lib.report.findings_snapshot import build_snapshot, load_snapshot, write_snapshot
from lib.prior_level import load_prior_level, write_prior_level
from lib.profiles import model_rows
from lib.since_last_run import baseline_record, pruning_record
from lib.run.run_setup import read_plugin_version
from lib.report.run_wiki import write_run_wiki
from lib.run.scan_plan import not_run_text


def _since_markdown(ctx: Mapping[str, Any]) -> str:
    """The since-last-run bullets that open this run's log.md entry (#1083); "" when absent."""
    block = ctx.get("since_last_run")
    if isinstance(block, Mapping) and block.get("available") is True and isinstance(block.get("markdown"), str):
        return str(block["markdown"])
    return ""


def _marker_debt(promissory: Mapping[str, Any], current: Mapping[str, Any]) -> dict[str, Any]:
    """Each hotspot's stale promissory markers; when a scan plan left the scan out (#1268), a
    ``not_run`` note per current hotspot, so its page never reads as carrying none."""
    not_run = not_run_text(promissory)
    if not_run is not None:
        return {h["path"]: {"not_run": not_run} for h in current.get("top_hotspots", [])
                if isinstance(h, Mapping) and "path" in h}
    return dict(promissory.get("stale_by_file", {})) if promissory.get("available") else {}


def publish_run(ctx: dict[str, Any], state: Mapping[str, Any]) -> None:
    """Write the wiki, badge and run-context.json for a finished scan.

    ``state`` is the inputs mapping ``run_scans`` filled: the provided inputs
    plus every private and spread entry's value.
    """
    assess_dir = state["assess_dir"]
    assess_dir.mkdir(parents=True, exist_ok=True)
    promissory = state["promissory"]
    _, scope_rel, _ = state["run_scope"]
    provisional_and_excluded = state["unfinalized"]
    wiki = write_run_wiki(
        state["current"], assess_dir=assess_dir, repo_root=state["repo_root"],
        run_date=state["run_date"], run_id=state["run_id"], diff=state["diff"],
        first_flagged_map=state["first_flagged"], superseded=state["superseded"],
        marker_debt_by_file=_marker_debt(promissory, state["current"]),
        accretion_by_file=state["accretion_by_file"],
        excluded_unfinalized=provisional_and_excluded[1],
        excluded_as_generated=state["excluded_as_generated"], scope_rel=scope_rel,
        instructions_grade=state["instruction_grading"][1],
        plugin_version=read_plugin_version(),
        hot_test_index=state["hot_test_index"],
        readiness=as_headline(ctx.get("headline"))["summary"],
        since_last_run=_since_markdown(ctx),
    )
    ctx["pruned_hotspots"] = wiki.pruned_hotspots
    ctx["retired_excluded_hotspots"] = wiki.retired_excluded
    ctx["dropped_first_flagged"] = wiki.dropped_first_flagged
    ctx["log_integrity"] = {"valid": wiki.log_valid, "broken_at_entry": wiki.log_broken_at}
    write_headline_badge(
        assess_dir, as_headline(ctx.get("headline")), run_id=state["run_id"], scope=scope_rel,
    )
    # The earlier run's per-tag values (#940), read before this run's are written, so the tag
    # profile strip draws the earlier run from the context and a later redraw reads the same.
    ctx["prior_level"] = load_prior_level(assess_dir)
    # The /assess diagrams (#942, #943), drawn from the finished context; the report links them.
    # The waffle titles its rows from the run's own model, so a --pack row reads its title (#1315).
    write_all(assess_dir, build_all(ctx, model_rows(state.get("maturity_model"))))
    write_prior_level(assess_dir, ctx.get("level"), baseline_record(ctx), pruning_record(ctx), ctx.get("packs"))
    # The committed findings snapshot (#554): read the previous one back before
    # overwriting it, so the gate can explain a method-version change by the
    # findings that appeared and cleared since.
    ctx["prior_findings_snapshot"] = load_snapshot(assess_dir)
    # A --profile run whose plan left the findings out keeps the committed snapshot (#1268):
    # an empty one would read as every finding cleared.
    if not_run_text(ctx["derived_findings"]) is None:
        write_snapshot(assess_dir, build_snapshot(ctx["derived_findings"], ASSESS_METHOD_VERSION))
    (assess_dir / "run-context.json").write_text(json.dumps(ctx, indent=2), encoding="utf-8")
