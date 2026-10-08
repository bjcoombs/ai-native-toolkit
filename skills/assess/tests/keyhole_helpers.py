"""Shared integrate() fixtures for the keyhole-signal tests.

A bleeding-but-statically-modular directory, a stale high-confidence doc over
a complex file, and the helper that reads one finding's paths. Used across
the test_keyhole_*.py family files.
"""
from __future__ import annotations

from pathlib import Path

from lib import keyhole_signals as ks

# A bleeding-but-statically-modular dir -> hidden_coupling; reused across the
# family files.
_BLEEDING_COMMIT_SETS = [
    {Path("looksmodular/x.py"), Path("core/util.py")},
    {Path("looksmodular/y.py"), Path("core/util.py")},
    {Path("looksmodular/x.py"), Path("core/other.py")},
    {Path("looksmodular/z.py"), Path("core/util.py")},
    {Path("looksmodular/x.py"), Path("shared/s.py")},
]
_MODULAR_STRUCTURE = {
    "available": True, "modularity_q": 0.6, "front_door_ratio": 0.95,
}
# A complex code file under a doc dir, so a stale high-confidence doc -> lying_map.
_COMPLEXITY_STATS = {
    "ccn": {"p50": 3.0, "p95": 8.0, "max": 30.0},
    "files_scored": 1,
    "top_complex": [{"path": "pkg/core.py", "loc": 400, "ccn": 30.0}],
    "top_hotspots": [],
    "top_large": [],
}


def _stale_doc_staleness(*, churn_degenerate: bool) -> dict:
    """A high-confidence stale doc over pkg/core.py - a lying_map when the churn
    history is real, suppressed when it is degenerate."""
    return {
        "available": True,
        "churn_window": "commits (last 12mo)",
        "churn_degenerate": churn_degenerate,
        "docs": [{
            "path": "pkg/README.md",
            "ratio": 6.0,
            "last_commit_days": 10,
            "doc_churn_in_window": 1,
            "code_churn_in_window": 6,
            "subject_code_count": 1,
            "subject_method": "nearest-ancestor",
            "confidence": "high",
        }],
    }


def _integrate(*, churn_degenerate: bool) -> dict:
    return ks.integrate(
        repo_root=Path("/nonexistent"),
        complexity_stats=_COMPLEXITY_STATS,
        doc_staleness=_stale_doc_staleness(churn_degenerate=churn_degenerate),
        dead_code={"available": False, "candidate_count": 0,
                   "candidates": [], "tools": []},
        observability={"rung": None, "reachable": {"present": False}},
        structure=_MODULAR_STRUCTURE,
        commit_sets=_BLEEDING_COMMIT_SETS,
    )


def _finding_paths(result: dict, name: str) -> list[str]:
    return next(f["paths"] for f in result["derived_findings"] if f["name"] == name)
