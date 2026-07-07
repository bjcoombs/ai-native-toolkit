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
    uv run assess_gate.py <repo_root> [--config <path>]
"""
# /// script
# requires-python = ">=3.11"
# ///
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

# scripts/ is on sys.path (pyproject pythonpath); lib is a package under it.
from lib.assess_config import load_gate_config, load_gate_config_file


def _findings_by_name(ctx: dict) -> dict[str, dict]:
    """Index ``derived_findings`` by finding name for O(1) lookup."""
    return {
        f["name"]: f
        for f in ctx.get("derived_findings", [])
        if isinstance(f, dict) and "name" in f
    }


def check_finding_regressions(
    ctx: dict, gate: dict
) -> tuple[list[dict], list[dict]]:
    """Split flagged findings into (failures, warnings) per the gate config.

    A finding "fires" when it has a non-empty ``paths`` list - that is the
    deterministic core's own signal that the concern is present in this repo.
    ``fail_on`` takes precedence over ``warn_on`` so a finding named in both is
    only ever counted once, as a failure.
    """
    findings = _findings_by_name(ctx)
    fail_on = gate.get("fail_on", [])
    failures: list[dict] = []
    warnings: list[dict] = []

    def _fired(name: str) -> dict | None:
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


def check_complexity_threshold(ctx: dict, gate: dict) -> list[dict]:
    """Return a one-element list when p95 file CCN exceeds ``ccn_p95_max``."""
    threshold = gate.get("ccn_p95_max")
    if threshold is None:
        return []
    current = ctx.get("stats_summary", {}).get("ccn", {}).get("p95")
    if isinstance(current, (int, float)) and current > threshold:
        return [{"metric": "ccn_p95", "value": current, "threshold": threshold}]
    return []


def _containment_ratio(ctx: dict) -> float | None:
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


def check_containment_threshold(ctx: dict, gate: dict) -> list[dict]:
    """Return a one-element list when the containment ratio drops below the floor."""
    floor = gate.get("containment_min")
    if floor is None:
        return []
    ratio = _containment_ratio(ctx)
    if ratio is not None and ratio < floor:
        return [{"metric": "containment", "value": round(ratio, 3), "threshold": floor}]
    return []


def check_diff_regression(ctx: dict, gate: dict) -> list[dict]:
    """Return a regression breach when the cross-run diff reports a worsening.

    Only fires when ``fail_on_regression`` is set AND there is a reliable diff
    against a prior committed snapshot. ``assess_core`` already computed the diff
    (graduated / new / regressed) into the run-context; "regressed" is its term
    for hotspots whose complexity or churn increased since the prior run. On a
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


def evaluate(ctx: dict, gate: dict) -> dict:
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
    )
    blocking = bool(failures or threshold_breaches)
    return {
        "enabled": gate.get("enabled", True),
        "failed": blocking and gate.get("enabled", True),
        "failures": failures,
        "warnings": warnings,
        "threshold_breaches": threshold_breaches,
        # Carried through so the verdict log can disclose findings the config
        # excludes suppressed - the gate must never read clean when a real
        # finding was filtered out by an exclude.
        "excluded_by_config": ctx.get("excluded_by_config"),
    }


def format_verdict(verdict: dict) -> str:
    """Render a human-readable summary for the CI log."""
    lines: list[str] = ["/assess gate"]
    if not verdict["enabled"]:
        lines.append("  gate disabled in config - reporting only, never failing")
    for breach in verdict["threshold_breaches"]:
        if breach["metric"] == "regressed_hotspots":
            sample = ", ".join(breach["paths"])
            lines.append(
                f"  FAIL regression: {breach['count']} hotspot(s) worsened since "
                f"the prior snapshot ({sample})"
            )
        else:
            lines.append(
                f"  FAIL threshold: {breach['metric']} = {breach['value']} "
                f"(max/min {breach['threshold']})"
            )
    for f in verdict["failures"]:
        sample = ", ".join(f["paths"])
        lines.append(f"  FAIL finding: {f['finding']} x{f['count']} ({sample})")
    for w in verdict["warnings"]:
        sample = ", ".join(w["paths"])
        lines.append(f"  warn finding: {w['finding']} x{w['count']} ({sample})")
    if not verdict["failures"] and not verdict["threshold_breaches"] and not verdict["warnings"]:
        lines.append("  no findings fired - clean snapshot")
    disclosure = _format_exclusion_disclosure(verdict.get("excluded_by_config"))
    if disclosure:
        lines.append(disclosure)
    lines.append("  RESULT: " + ("FAIL" if verdict["failed"] else "PASS"))
    return "\n".join(lines)


def _format_exclusion_disclosure(excluded_by_config: dict | None) -> str:
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


def load_context(repo_root: Path) -> dict[str, Any]:
    """Load ``.assess/run-context.json`` from a repo root."""
    ctx_path = repo_root / ".assess" / "run-context.json"
    return json.loads(ctx_path.read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    # --config points the gate at a config file outside the conventional
    # .assess/config.toml. Consume both the flag and its value so the value
    # isn't mistaken for the positional repo root.
    config_path: str | None = None
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
        print("Usage: assess_gate.py <repo_root> [--config <path>]", file=sys.stderr)
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
    verdict = evaluate(ctx, gate)
    print(format_verdict(verdict))
    return 1 if verdict["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
