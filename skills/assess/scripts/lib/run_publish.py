"""The side effects of a run, applied after every scan has finished.

The scan table only reads. This step writes: the artifact directory, the
``.assess/`` wiki (hotspot pages, index.md, the log.md entry and its checksum
chain, first-flagged.json), the findings badge and ``run-context.json``. The
wiki's results fill the four keys the run header reserved for them
(``run_setup.WIKI_KEYS``) in place, so their position in the file is unchanged.
"""
from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from lib.badge import write_findings_badge
from lib.run_setup import read_plugin_version
from lib.run_wiki import write_run_wiki


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
        marker_debt_by_file=(
            promissory.get("stale_by_file", {}) if promissory.get("available") else {}
        ),
        accretion_by_file=state["accretion_by_file"],
        excluded_unfinalized=provisional_and_excluded[1],
        excluded_as_generated=state["excluded_as_generated"], scope_rel=scope_rel,
        instructions_grade=state["instruction_grading"][1],
        plugin_version=read_plugin_version(),
        hot_test_index=state["hot_test_index"],
    )
    ctx["pruned_hotspots"] = wiki.pruned_hotspots
    ctx["retired_excluded_hotspots"] = wiki.retired_excluded
    ctx["dropped_first_flagged"] = wiki.dropped_first_flagged
    ctx["log_integrity"] = {"valid": wiki.log_valid, "broken_at_entry": wiki.log_broken_at}
    write_findings_badge(
        assess_dir, promissory, ctx["derived_findings"], run_id=state["run_id"], scope=scope_rel,
    )
    (assess_dir / "run-context.json").write_text(json.dumps(ctx, indent=2), encoding="utf-8")
