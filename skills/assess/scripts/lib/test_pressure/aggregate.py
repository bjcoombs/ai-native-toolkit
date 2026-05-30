"""Aggregation + the public ``scan_test_pressure`` facade.

Merges the mutation tier and the cheap always-on heuristics into one
``test_pressure`` block ready to drop into run-context.json.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .heuristics import compute_cheap_heuristics
from .mutation import (
    compute_gap_signal,
    compute_survivor_density,
    detect_mutation_config,
    identify_survivor_clusters,
    run_bounded_mutation,
)


@dataclass
class TestPressureResult:
    mutation_config_present: bool = False
    mutation_tools_detected: list = field(default_factory=list)
    ci_integrated: bool = False
    mutation_run: bool = False
    mutation_scope: list = field(default_factory=list)
    per_file: list = field(default_factory=list)
    survivor_density: dict = field(default_factory=dict)
    survivor_clusters: list = field(default_factory=list)
    gap_signal: str = "not assessed"
    cheap_heuristics: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "mutation_config_present": self.mutation_config_present,
            "mutation_tools_detected": self.mutation_tools_detected,
            "ci_integrated": self.ci_integrated,
            "mutation_run": self.mutation_run,
            "mutation_scope": self.mutation_scope,
            "per_file": self.per_file,
            "survivor_density": self.survivor_density,
            "survivor_clusters": self.survivor_clusters,
            "gap_signal": self.gap_signal,
            "cheap_heuristics": self.cheap_heuristics,
        }


def _overall_coverage(coverage_data) -> float | None:
    """Reduce coverage_data to a single line-coverage ratio, if supplied.
    We only have covered lines, not total lines, so we cannot compute a true
    ratio here - return None unless an explicit ``{"_overall": ratio}`` is
    present. Kept separate so the wiring teammate can pass a real ratio."""
    if isinstance(coverage_data, dict):
        overall = coverage_data.get("_overall")
        if isinstance(overall, (int, float)):
            return float(overall)
    return None


def scan_test_pressure(repo_root: Path, hot_files: list | None = None,
                       opt_in: bool = False, coverage_data=None) -> dict:
    """Top-level Layer-1 write-side scan. Merges the mutation tier and the cheap
    always-on heuristics into one ``test_pressure`` block ready to drop into
    run-context.json. Never raises.

    ``opt_in`` gates the (mutating, code-running) bounded mutation pass; the
    cheap heuristics and config detection always run. ``coverage_data``
    (optional) feeds both the boundary heuristic and the mutation gap signal -
    absent, both report "not assessed" rather than guessing.
    """
    repo_root = Path(repo_root)

    config = detect_mutation_config(repo_root)
    mutation = run_bounded_mutation(repo_root, hot_files, opt_in=opt_in)
    per_file = mutation.get("per_file", [])
    density = compute_survivor_density(per_file)
    clusters = identify_survivor_clusters(per_file)

    coverage = _overall_coverage(coverage_data)
    mutation_score = (1.0 - density["overall"]) if density["overall"] is not None else None
    gap = compute_gap_signal(coverage, mutation_score)

    cheap = compute_cheap_heuristics(repo_root, coverage_data)

    result = TestPressureResult(
        mutation_config_present=config["present"],
        mutation_tools_detected=config["tools"],
        ci_integrated=config["ci_integrated"],
        mutation_run=mutation.get("mutation_run", False),
        mutation_scope=mutation.get("scope", []),
        per_file=per_file,
        survivor_density=density,
        survivor_clusters=clusters,
        gap_signal=gap,
        cheap_heuristics=cheap,
    )
    block = result.as_dict()
    # Surface why a mutation run didn't happen, for the report prose.
    if "reason" in mutation:
        block["mutation_note"] = mutation["reason"]
    return block
