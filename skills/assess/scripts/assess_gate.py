"""CI gate for /assess - the enforcement half of the frozen harness.

Reads ``.assess/run-context.json`` (written by ``assess_core.py``) and the
``[gate]`` section of ``.assess/config.toml``, then decides whether the current
snapshot should fail a pull request. Two kinds of check, both strictly opt-in:

- **Readiness floors** (absolute, evaluated on the current snapshot): a finding
  in ``fail_on`` is present, p95 complexity exceeds ``ccn_p95_max``, or the
  safe-zone containment ratio drops below ``containment_min``.
- **Regression** (cross-run): with ``fail_on_regression = true``, fail when the
  diff ``assess_core`` computed against the prior committed snapshot reports
  hotspots whose complexity/churn increased. This needs a prior snapshot and a
  reliable diff; on a first run or an unreliable diff it is skipped, never
  fired - a freshly-cloned repo never trips a regression gate on its first PR.
- **Gate loss** (cross-run, #1152): with ``fail_on_gate_loss = true`` (or the
  ``--fail-on-gate-loss`` flag, which the GitLab template passes), fail when a
  Step gate the committed baseline held no longer holds: the run-context's
  ``since_last_run.gates_lost``, read against ``.assess/prior-level.json``. A
  Step needs its gates and nothing else, so a lost Step is always a lost gate.
  On a first run, or when the gate changes are not known (``gates_lost`` is
  null), nothing fires. Only a gate that now reads ``unmet`` can fail: one that
  could not be read this run (``not_measured``) is a warn line, never a loss.
  A loss counts only against the committed baseline: one ``since_last_run`` read from an
  earlier ``run-context.json`` left on disk is a warn line. Without the opt-in a lost
  gate is a warn line only. A run whose gates were not compared across maturity models
  (``gate_loss.not_compared``, #1150) prints a warn line naming why, never "clean snapshot";
  with ``fail_on_gate_loss`` on and a committed baseline, a run whose previous run used another
  model (the ``since_last_run`` model-changed line names both) fails: "gates not compared: model
  changed (the default model -> profile handover); cannot confirm no gate was lost". With no
  committed baseline that change came from a ``run-context.json`` left on disk, so it is a warn
  line beside "no baseline yet", as a lost gate read from it is. A profile run compares with its
  own committed record (#1184) and fails or passes on its own gates; with no record yet it reads
  "no baseline yet", as a default first run does. Without the opt-in each is a warn line. Printed output is ASCII apart from the
  headline's own text: a model pair reads ``->``, which a cp1252 console can encode.

A third, explanatory part: when the committed ``.assess/findings.json`` (read
from disk, or the prior the run recorded before rewriting it) was
written under another finding method version (``ASSESS_METHOD_VERSION``), the
log states that the method change is not a code change, lists the findings that
appeared and cleared by path against that snapshot (which also reflect any code
changed since it was committed), and prints the re-freeze command, or, when
this run's toolkit is older than the snapshot's, the pin to raise instead. It explains
the other checks' results and never fails the gate by itself.

``--summary`` prints the executive summary that opens the report
(``headline.summary_lines``, ``lib/exec_summary.py``) above the verdict, so a CI
log shows the Step, the next gate and what changed since the last run.

The defaults are warn-only: with no config, every finding is reported but
nothing fails, so adopting the emitted workflow never blocks a pipeline by
surprise.

Exit codes:
    0  pass (nothing failing, the gate is disabled / warn-only, OR an
       infrastructure failure - missing/corrupt run-context - was skipped: an
       infra failure is never a red check on an unrelated PR)
    1  fail (a floor was breached, or an opted-in regression fired)
    2  usage error (no repo_root argument)

Run:
    uv run assess_gate.py <repo_root> [--config <path>] [--fail-on-gate-loss] [--summary]
"""
# /// script
# requires-python = ">=3.11"
# ///
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, NotRequired, TypedDict, cast

# scripts/ is on sys.path (pyproject pythonpath); lib is a package under it.
from lib.assess_config import GateConfig, load_gate_config, load_gate_config_file
from lib.findings_snapshot import committed_prior, method_change
from lib.gate_loss import (held_at_baseline, model_change_reason, no_baseline, not_compared, split_lost,
                           step_change)
from lib.reviewed_findings import open_findings
from lib.run_context_types import DerivedFinding, JsonDict, MethodChange


class FindingHit(TypedDict):
    """A finding that fired: its name, path count and the first five paths."""

    finding: str
    count: int
    paths: list[str]


class GateVerdict(TypedDict):
    """``evaluate``'s result, rendered by ``format_verdict``."""

    enabled: bool
    failed: bool
    failures: list[FindingHit]
    warnings: list[FindingHit]
    # A threshold breach carries ``value``/``threshold`` and a regression
    # breach ``count``/``paths``; ``metric`` tells them apart.
    threshold_breaches: list[JsonDict]
    excluded_by_config: JsonDict | None
    # A list of rows, or the unavailable marker when that scan failed.
    excluded_generated: list[JsonDict] | JsonDict | None
    # Set when the committed findings snapshot was written by another finding
    # method version: the findings moved because the method did.
    method_change: NotRequired[MethodChange | None]
    # Step gates the committed baseline held that this run does not, when the
    # loss was not opted into failing (#1152): reported, never failing.
    gates_lost_warning: NotRequired[list[str]]
    # Gates the baseline held that this run could not read: unknown, never a loss.
    gates_unread_warning: NotRequired[list[str]]
    # Why no gate was compared across maturity models (#1150): a warn line, never "clean".
    gates_not_compared: NotRequired[str]
    # fail_on_gate_loss is on but there is no committed baseline to compare with.
    no_baseline: NotRequired[bool]


def _findings_by_name(ctx: JsonDict) -> dict[str, JsonDict]:
    """Index ``derived_findings`` by finding name, reviewed paths taken out (#721)."""
    rows = [
        f for f in ctx.get("derived_findings", [])
        if isinstance(f, dict) and "name" in f and isinstance(f.get("paths"), list)
    ]
    return {f["name"]: cast(JsonDict, f) for f in open_findings(cast("list[DerivedFinding]", rows))}


def check_finding_regressions(
    ctx: JsonDict, gate: GateConfig
) -> tuple[list[FindingHit], list[FindingHit]]:
    """Split flagged findings into (failures, warnings) per the gate config.

    A finding "fires" when it has a non-empty ``paths`` list - that is the
    deterministic core's own signal that the concern is present in this repo.
    ``fail_on`` takes precedence over ``warn_on`` so a finding named in both is
    only ever counted once, as a failure.
    """
    findings = _findings_by_name(ctx)
    fail_on = gate.get("fail_on", [])
    failures: list[FindingHit] = []
    warnings: list[FindingHit] = []

    def _fired(name: str) -> FindingHit | None:
        f = findings.get(name)
        if f and f.get("paths"):
            return {"finding": name, "count": len(f["paths"]), "paths": f["paths"][:5]}
        return None

    for name in fail_on:
        hit = _fired(name)
        if hit is not None:
            failures.append(hit)
    for name in gate.get("warn_on", []):
        if name in fail_on:
            continue
        hit = _fired(name)
        if hit is not None:
            warnings.append(hit)
    return failures, warnings


def check_complexity_threshold(ctx: JsonDict, gate: GateConfig) -> list[JsonDict]:
    """Return a one-element list when p95 file CCN exceeds ``ccn_p95_max``."""
    threshold = gate.get("ccn_p95_max")
    if threshold is None:
        return []
    current = ctx.get("stats_summary", {}).get("ccn", {}).get("p95")
    if isinstance(current, (int, float)) and current > threshold:
        return [{"metric": "ccn_p95", "value": current, "threshold": threshold}]
    return []


def _containment_ratio(ctx: JsonDict) -> float | None:
    """Safe zones / (safe zones + total concerns) from the keyhole summary.

    The deterministic core already rolls the findings into a keyhole summary
    with a safe-zone count and a total-concern count. The ratio is the share of
    examined units that are clean refactor boundaries; ``None`` when there is
    nothing to score (no units flagged either way).
    """
    summary = ctx.get("keyhole_summary") or {}
    safe = summary.get("safe_zones")
    concerns = summary.get("total_concerns")
    if not isinstance(safe, int) or not isinstance(concerns, int):
        return None
    denom = safe + concerns
    if denom <= 0:
        return None
    return safe / denom


def check_containment_threshold(ctx: JsonDict, gate: GateConfig) -> list[JsonDict]:
    """Return a one-element list when the containment ratio drops below the floor."""
    floor = gate.get("containment_min")
    if floor is None:
        return []
    ratio = _containment_ratio(ctx)
    if ratio is not None and ratio < floor:
        return [{"metric": "containment", "value": round(ratio, 3), "threshold": floor}]
    return []


def check_diff_regression(ctx: JsonDict, gate: GateConfig) -> list[JsonDict]:
    """Return a regression breach when the cross-run diff reports a worsening.

    Only fires when ``fail_on_regression`` is set AND there is a reliable diff
    against a prior committed snapshot. ``assess_core`` already computed the diff
    (graduated / new / regressed / restructured) into the run-context; "regressed"
    is its term for hotspots whose worst function (or, without a per-function
    breakdown, summed complexity or churn) increased since the prior run. A
    ``restructured`` hotspot - worst function down, sum up - does not trip it. On a
    first run (no prior) or an unreliable diff (version-mismatched filter), it
    returns nothing - a freshly-adopted repo can't trip a regression gate.
    """
    if not gate.get("fail_on_regression"):
        return []
    if not ctx.get("prior_stats_exists") or not ctx.get("diff_reliable", True):
        return []
    regressed = ctx.get("diff_detail", {}).get("regressed", [])
    if not regressed:
        return []
    return [{
        "metric": "regressed_hotspots",
        "count": len(regressed),
        "paths": [r.get("path", "?") for r in regressed[:5]],
    }]


def check_gate_loss(ctx: JsonDict, gate: GateConfig) -> list[JsonDict]:
    """Return a gate-loss breach when ``fail_on_gate_loss`` is set and a gate the
    baseline held now reads unmet."""
    if not gate.get("fail_on_gate_loss"):
        return []
    # A run that compared no gate because the previous run used another model cannot show a
    # loss, so the opt-in fails with that reason rather than reading "could not check" as clean.
    # With no committed baseline a model change came from a run-context.json left on disk, which
    # can only warn, as a lost gate read from it does.
    changed = model_change_reason(ctx)
    if changed is not None and not no_baseline(ctx):
        return [{"metric": "gates_not_compared", "count": 0, "paths": [], "reason": changed}]
    unmet = held_at_baseline(ctx, split_lost(ctx)[0])
    if not unmet:
        return []
    breach: JsonDict = {"metric": "gates_lost", "count": len(unmet), "paths": unmet}
    change = step_change(ctx)
    if change is not None:
        breach["step_from"], breach["step_to"] = change
    return [breach]


def evaluate(ctx: JsonDict, gate: GateConfig) -> GateVerdict:
    """Run every check and return a structured verdict.

    ``failed`` is the gate decision; ``warnings`` are reported but never fail.
    A disabled gate collects the same diagnostics (so the log is honest) but
    always reports ``failed = False``.
    """
    failures, warnings = check_finding_regressions(ctx, gate)
    threshold_breaches = (
        check_complexity_threshold(ctx, gate)
        + check_containment_threshold(ctx, gate)
        + check_diff_regression(ctx, gate)
        + check_gate_loss(ctx, gate)
    )
    blocking = bool(failures or threshold_breaches)
    verdict: GateVerdict = {
        "enabled": gate.get("enabled", True),
        "failed": blocking and gate.get("enabled", True),
        "failures": failures,
        "warnings": warnings,
        "threshold_breaches": threshold_breaches,
        # Carried through so the verdict log can disclose findings the config
        # excludes suppressed - the gate must never read clean when a real
        # finding was filtered out by an exclude.
        "excluded_by_config": ctx.get("excluded_by_config"),
        # Files the treemap dropped as generated, so the log names the reasons.
        "excluded_generated": ctx.get("excluded_generated"),
        # Explanation only: a method change never fails the gate by itself, but
        # it states why the findings below moved.
        "method_change": method_change(ctx),
    }
    unmet, unread = split_lost(ctx)
    failing = held_at_baseline(ctx, unmet) if gate.get("fail_on_gate_loss") else []
    if [g for g in unmet if g not in failing]:
        verdict["gates_lost_warning"] = [g for g in unmet if g not in failing]
    if unread:
        verdict["gates_unread_warning"] = unread
    if gate.get("fail_on_gate_loss") and no_baseline(ctx):
        verdict["no_baseline"] = True
    reason = not_compared(ctx)
    if reason is not None:
        verdict["gates_not_compared"] = reason
    return verdict


def _breach_line(breach: JsonDict) -> str:
    """One threshold breach as its FAIL line in the CI log."""
    if breach["metric"] == "regressed_hotspots":
        sample = ", ".join(breach["paths"])
        return (
            f"  FAIL regression: {breach['count']} hotspot(s) worsened since "
            f"the prior snapshot ({sample})"
        )
    elif breach["metric"] == "gates_not_compared":
        return (
            f"  FAIL gates not compared: {breach['reason']}; cannot confirm no gate was lost. "
            "fail_on_gate_loss compares runs of one maturity model, so run and commit the model "
            "the baseline used"
        )
    elif breach["metric"] == "gates_lost":
        step = (f" (Step {breach['step_from']} -> Step {breach['step_to']})"
                if "step_from" in breach else "")
        return (
            f"  FAIL gate lost: {', '.join(breach['paths'])} held at the "
            f"committed baseline and no longer holds{step}"
        )
    else:
        return (
            f"  FAIL threshold: {breach['metric']} = {breach['value']} "
            f"(max/min {breach['threshold']})"
        )


def format_verdict(verdict: GateVerdict) -> str:
    """Render a human-readable summary for the CI log."""
    lines: list[str] = ["/assess gate"]
    if not verdict["enabled"]:
        lines.append("  gate disabled in config - reporting only, never failing")
    lines.extend(_format_method_change(verdict.get("method_change")))
    lines.extend(_breach_line(breach) for breach in verdict["threshold_breaches"])
    for f in verdict["failures"]:
        sample = ", ".join(f["paths"])
        lines.append(f"  FAIL finding: {f['finding']} x{f['count']} ({sample})")
    for w in verdict["warnings"]:
        sample = ", ".join(w["paths"])
        lines.append(f"  warn finding: {w['finding']} x{w['count']} ({sample})")
    lost = verdict.get("gates_lost_warning")
    if lost:
        lines.append(
            f"  warn gate lost: {', '.join(lost)} held at the last run and no longer "
            "holds (it fails the gate with fail_on_gate_loss on and a committed "
            ".assess/prior-level.json that held it)"
        )
    unread = verdict.get("gates_unread_warning")
    if unread:
        lines.append(
            f"  warn no longer measured: {', '.join(unread)} held at the committed "
            "baseline and could not be read this run, so it is not counted as lost"
        )
    skipped = verdict.get("gates_not_compared")
    failing_skip = any(b["metric"] == "gates_not_compared" for b in verdict["threshold_breaches"])
    if skipped and not failing_skip:
        lines.append(f"  warn gates not compared: {skipped}")
    if verdict.get("no_baseline"):
        lines.append(
            "  no baseline yet: commit .assess/prior-level.json from an /assess run "
            "so the next run can compare gates and Steps"
        )
    if (not verdict["failures"] and not verdict["threshold_breaches"]
            and not verdict["warnings"] and not lost and not unread and not skipped):
        lines.append("  no findings fired - clean snapshot")
    disclosure = _format_exclusion_disclosure(verdict.get("excluded_by_config"))
    if disclosure:
        lines.append(disclosure)
    generated = _format_generated_disclosure(verdict.get("excluded_generated"))
    if generated:
        lines.append(generated)
    lines.append("  RESULT: " + ("FAIL" if verdict["failed"] else "PASS"))
    return "\n".join(lines)


def _format_exclusion_disclosure(excluded_by_config: JsonDict | None) -> str:
    """One indented line disclosing findings the config excludes suppressed.

    Returns ``""`` when nothing was suppressed, so a clean run's log is
    unchanged. When a path that would have been a finding was filtered out by a
    config exclude, the suppression is stated with the excluding dirs/patterns so
    a reader never mistakes a filtered PASS for a genuinely clean one.
    """
    block = excluded_by_config or {}
    count = block.get("count", 0)
    if not isinstance(count, int) or count <= 0:
        return ""
    dirs = ", ".join(block.get("dirs", [])) or "none"
    patterns = ", ".join(block.get("patterns", [])) or "none"
    noun = "finding" if count == 1 else "findings"
    return (
        f"  {count} {noun} suppressed by config excludes "
        f"(dirs: {dirs}; patterns: {patterns})"
    )


# Per-direction lines the method-change note lists before "+N more".
METHOD_CHANGE_MAX_PATHS = 20


def _format_method_change(change: MethodChange | None) -> list[str]:
    """The method-version note: the method change is not a code change, the
    findings that appeared and cleared against the committed snapshot (which
    also carry any code changed since it), and the re-freeze command, or on a
    downgrade the toolkit pin to raise.
    Empty when the versions match or there is no committed snapshot."""
    if not change:
        return []
    lines = [
        f"  NOTE method change: the /assess finding method changed from "
        f"version {change['prior_method_version']} to {change['method_version']} "
        f"since {change['snapshot']} was committed.",
        "    The method change is not a code change: it moves findings on "
        "unchanged code. The moves below combine it with any code changed "
        "since the snapshot was committed, this PR's included.",
    ]
    if change["downgrade"]:
        lines.append(
            "    This run's toolkit is older than the one that wrote the "
            "snapshot, so re-freezing with it would undo the newer method.")
    for label, moves in (("appeared", change["appeared"]), ("cleared", change["cleared"])):
        if not moves:
            lines.append(f"    {label}: none")
            continue
        lines.append(f"    {label} ({len(moves)}):")
        shown = moves[:METHOD_CHANGE_MAX_PATHS]
        lines.extend(f"      {m['finding']}: {m['path']}" for m in shown)
        if len(moves) > len(shown):
            lines.append(f"      +{len(moves) - len(shown)} more")
    label = "Raise pin" if change["downgrade"] else "Re-freeze"
    lines.append(f"    {label}: {change['refreeze']}")
    return lines


# Per-file lines the gate log lists before summarising the rest as "+N more".
GENERATED_DISCLOSURE_MAX_PATHS = 10


def _format_generated_disclosure(excluded_generated: list[JsonDict] | JsonDict | None) -> str:
    """A summary line naming each distinct reason files were excluded as
    generated, then one indented ``path (reason)`` line per file (capped at
    ``GENERATED_DISCLOSURE_MAX_PATHS``), or ``""`` when none were, so a clean
    run's log is unchanged. A failed scan (the unavailable marker) says the
    exclusions were not disclosed rather than reading as none."""
    if isinstance(excluded_generated, dict):
        return f"  generated-file exclusions not disclosed: {excluded_generated.get('reason')}"
    rows = [
        r for r in (excluded_generated or [])
        if isinstance(r, dict) and r.get("path") and r.get("reason")
    ]
    if not rows:
        return ""
    reasons = sorted({r["reason"] for r in rows})
    noun = "file" if len(rows) == 1 else "files"
    lines = [
        f"  {len(rows)} {noun} excluded from scoring as generated "
        f"(reasons: {', '.join(reasons)})"
    ]
    shown = rows[:GENERATED_DISCLOSURE_MAX_PATHS]
    lines.extend(f"    {r['path']} ({r['reason']})" for r in shown)
    if len(rows) > len(shown):
        lines.append(f"    +{len(rows) - len(shown)} more")
    return "\n".join(lines)


def load_context(repo_root: Path) -> dict[str, Any]:
    """Load ``.assess/run-context.json`` from a repo root."""
    ctx_path = repo_root / ".assess" / "run-context.json"
    ctx: dict[str, Any] = json.loads(ctx_path.read_text(encoding="utf-8"))
    return ctx


def format_summary(ctx: JsonDict) -> str:
    """The executive summary that opens the report, one line each, or ``""``
    when the headline has none (an unavailable level result)."""
    headline = ctx.get("headline")
    raw = headline.get("summary_lines") if isinstance(headline, dict) else None
    lines = [ln for ln in raw if isinstance(ln, str)] if isinstance(raw, list) else []
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    # --config points the gate at a config file outside the conventional
    # .assess/config.toml. Consume both the flag and its value so the value
    # isn't mistaken for the positional repo root.
    config_path: str | None = None
    fail_on_gate_loss = "--fail-on-gate-loss" in args
    summary = "--summary" in args
    positional: list[str] = []
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "--config":
            if i + 1 < len(args):
                config_path = args[i + 1]
            i += 2
            continue
        if arg.startswith("-"):
            i += 1
            continue
        positional.append(arg)
        i += 1
    if not positional:
        print("Usage: assess_gate.py <repo_root> [--config <path>] "
              "[--fail-on-gate-loss] [--summary]", file=sys.stderr)
        return 2
    repo_root = Path(positional[0]).resolve()
    try:
        ctx = load_context(repo_root)
    except (OSError, json.JSONDecodeError) as e:
        # The run-context is missing or corrupt because the deterministic core
        # failed to produce it - an infrastructure failure, not an AI-readiness
        # finding. Skip with a clear notice and exit 0 so a broken render never
        # fails a PR that has nothing to do with the breakage. The gate runs
        # again on the next push, when the core has a clean run-context.
        print(
            f"/assess gate skipped: infrastructure failure "
            f"({type(e).__name__}: {e}).\n"
            "This is an infra issue, not a finding. The gate will run on the "
            "next push.",
            file=sys.stderr,
        )
        return 0
    gate = (
        load_gate_config_file(Path(config_path))
        if config_path is not None
        else load_gate_config(repo_root)
    )
    if fail_on_gate_loss:
        gate["fail_on_gate_loss"] = True
    # Compare against the committed findings.json, not only the prior the
    # pipeline recorded: the gate may run on a snapshot edited or checked out
    # after the last pipeline run (#661).
    ctx["prior_findings_snapshot"] = committed_prior(ctx, repo_root)
    verdict = evaluate(ctx, gate)
    # the summary's model-changed line carries U+2192, which a cp1252 console cannot encode
    if summary and format_summary(ctx):
        print(format_summary(ctx).replace("\u2192", "->") + "\n")
    print(format_verdict(verdict).replace("\u2192", "->"))
    return 1 if verdict["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
