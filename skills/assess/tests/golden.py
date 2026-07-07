"""Golden-baseline normalization for /assess dogfood parity tests.

Phase 0 of the `assess-dogfooded` work captures a full `/assess` run against
this repo as a regression baseline. The decomposition work (Part 3) must prove
the decomposed pipeline reproduces the *same* deterministic output the monolith
produces today - a byte-for-byte parity test against the captured golden.

But a raw `run-context.json` / `assess-report.md` carries fields that change on
every commit and every version bump - the plugin version, the run date, the
measured commit, and the cross-run diff (which depends on whatever prior stats
sidecar happened to be on disk). Comparing those verbatim would make the parity
test fail for reasons unrelated to the decomposition.

So the golden fixtures are stored *normalized*: every volatile field is replaced
with the sentinel below. A parity test regenerates `run-context.json`, runs it
through `normalize_run_context`, and compares against the stored golden - which
was itself produced by this same function. Same transform on both sides → the
only differences that can fail the test are real divergences in the
deterministic computation, which is exactly what Part 3 must not introduce.

Keep this module dependency-free (stdlib only) so it imports cleanly in any test
environment, mirroring the deterministic-core contract.
"""
from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any

#: Replaces every volatile scalar. A string sentinel (not ``None``) so a
#: normalized field stays type-stable and visibly intentional in a diff.
SENTINEL = "<<normalized>>"

#: Top-level keys whose entire value is environment- or run-dependent and is
#: therefore replaced wholesale. ``diff`` / ``diff_detail`` depend on which
#: prior stats sidecar was on disk; the version/date keys move every release.
_VOLATILE_TOP_LEVEL = (
    "plugin_version",
    "prior_plugin_version",
    "run_date",
    # run_id is a fresh timestamp+uuid every run (schema_version is stable, so it
    # stays comparable and is NOT masked).
    "run_id",
    "diff",
    "diff_detail",
    "diff_reliable",
    "diff_version_note",
    "prior_stats_exists",
)

#: Sub-keys of ``measured_commit`` that identify the specific commit/work-tree
#: state. ``available`` is preserved (structural), the rest are normalized.
_VOLATILE_MEASURED_COMMIT = (
    "head_sha",
    "head_short",
    "committed_date",
    "subject",
    "dirty",
    "upstream",
    "behind",
)


def normalize_run_context(ctx: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of a ``run-context.json`` dict with volatile fields masked.

    Pure: the input is deep-copied, never mutated. Missing keys are tolerated
    (the schema evolves), so an absent volatile key is simply skipped rather
    than synthesized - the golden then carries no entry for it either.
    """
    out = copy.deepcopy(ctx)

    for key in _VOLATILE_TOP_LEVEL:
        if key in out:
            out[key] = SENTINEL

    mc = out.get("measured_commit")
    if isinstance(mc, dict):
        for key in _VOLATILE_MEASURED_COMMIT:
            if key in mc:
                mc[key] = SENTINEL

    return out


#: The "Generated ..." provenance line at the top of a report. Captures the
#: date and plugin version, both volatile.
_REPORT_GENERATED_RE = re.compile(
    r"^_Generated .*?\._$", re.MULTILINE
)
#: The "Measured at commit" bullet pins absolute figures to a commit + date.
_REPORT_MEASURED_COMMIT_RE = re.compile(
    r"^- \*\*Measured at commit:\*\* .*$", re.MULTILINE
)


def normalize_report(text: str) -> str:
    """Return an ``assess-report.md`` string with volatile lines masked.

    Only the two provenance lines (the ``_Generated ..._`` stamp and the
    ``Measured at commit`` bullet) carry the date/version/commit; the rest of
    the report is deterministic given the deterministic core's output, so it is
    compared verbatim.
    """
    text = _REPORT_GENERATED_RE.sub(f"_Generated {SENTINEL}._", text)
    text = _REPORT_MEASURED_COMMIT_RE.sub(
        f"- **Measured at commit:** {SENTINEL}", text
    )
    return text


def load_golden_run_context() -> dict[str, Any]:
    """Load the normalized golden ``run-context`` baseline."""
    path = Path(__file__).parent / "fixtures" / "golden" / "run-context-baseline.json"
    return json.loads(path.read_text())


def load_golden_report() -> str:
    """Load the normalized golden ``assess-report`` baseline."""
    path = Path(__file__).parent / "fixtures" / "golden" / "assess-report-baseline.md"
    return path.read_text()
