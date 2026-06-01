"""CI regression gate for /assess - the enforcement half of the frozen harness.

Reads ``.assess/run-context.json`` (written by ``assess_core.py``) and the
``[gate]`` section of ``.assess/config.toml``, then decides whether the current
snapshot represents a regression worth failing a pull request over.

The defaults are warn-only: with no config, every finding is reported but
nothing fails, so adopting the emitted workflow never blocks a pipeline by
surprise. Failing is strictly opt-in (``fail_on``, ``ccn_p95_max``,
``containment_min``).

Exit codes:
    0  pass (no failing regression, or the gate is disabled / warn-only)
    1  fail (a fail_on finding is present, or a threshold was breached)
    2  usage error (missing run-context, bad arguments)

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
from lib.assess_config import load_gate_config


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


def evaluate(ctx: dict, gate: dict) -> dict:
    """Run every check and return a structured verdict.

    ``failed`` is the gate decision; ``warnings`` are reported but never fail.
    A disabled gate collects the same diagnostics (so the log is honest) but
    always reports ``failed = False``.
    """
    failures, warnings = check_finding_regressions(ctx, gate)
    threshold_breaches = check_complexity_threshold(ctx, gate) + check_containment_threshold(ctx, gate)
    blocking = bool(failures or threshold_breaches)
    return {
        "enabled": gate.get("enabled", True),
        "failed": blocking and gate.get("enabled", True),
        "failures": failures,
        "warnings": warnings,
        "threshold_breaches": threshold_breaches,
    }


def format_verdict(verdict: dict) -> str:
    """Render a human-readable summary for the CI log."""
    lines: list[str] = ["/assess regression gate"]
    if not verdict["enabled"]:
        lines.append("  gate disabled in config - reporting only, never failing")
    for breach in verdict["threshold_breaches"]:
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
    lines.append("  RESULT: " + ("FAIL" if verdict["failed"] else "PASS"))
    return "\n".join(lines)


def load_context(repo_root: Path) -> dict[str, Any]:
    """Load ``.assess/run-context.json`` from a repo root."""
    ctx_path = repo_root / ".assess" / "run-context.json"
    return json.loads(ctx_path.read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    # --config is accepted for symmetry with the emitted workflow, but the gate
    # config is read from the repo's .assess/config.toml via load_gate_config,
    # so the flag's value is informational (it points at that same file). Consume
    # both the flag and its value so the value isn't mistaken for the repo root.
    positional: list[str] = []
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "--config":
            i += 2  # skip the flag and its value
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
        print(
            f"error: could not read {repo_root}/.assess/run-context.json ({e}); "
            "run assess_core.py first",
            file=sys.stderr,
        )
        return 2
    gate = load_gate_config(repo_root)
    verdict = evaluate(ctx, gate)
    print(format_verdict(verdict))
    return 1 if verdict["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
