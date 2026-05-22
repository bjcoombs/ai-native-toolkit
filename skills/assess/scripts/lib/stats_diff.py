"""Compare current complexity stats against a prior run.

Identifies hotspot transitions:
    graduated:  was in prior top_hotspots, absent from current
    regressed:  in both, but ccn or commits got worse
    new:        in current top_hotspots, absent from prior
    persistent: in both, roughly unchanged

No LLM calls. Pure set operations + arithmetic.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class HotspotTransition:
    path: str
    ccn_delta: int = 0
    commits_delta: int = 0
    loc_delta: int = 0


@dataclass
class StatsDiff:
    graduated: list[HotspotTransition] = field(default_factory=list)
    regressed: list[HotspotTransition] = field(default_factory=list)
    new: list[HotspotTransition] = field(default_factory=list)
    persistent: list[HotspotTransition] = field(default_factory=list)

    def summary(self) -> dict[str, int]:
        return {
            "graduated": len(self.graduated),
            "regressed": len(self.regressed),
            "new": len(self.new),
            "persistent": len(self.persistent),
        }


def load_stats(path: Path) -> dict | None:
    """Load stats JSON from path, or None if file doesn't exist."""
    if not path.exists():
        return None
    return json.loads(path.read_text())


def diff_stats(*, prior: dict | None, current: dict) -> StatsDiff:
    """Compute hotspot transitions between two stats snapshots."""
    diff = StatsDiff()

    current_hotspots = {h["path"]: h for h in current.get("top_hotspots", [])}

    if prior is None:
        diff.new = [HotspotTransition(path=p) for p in current_hotspots]
        return diff

    prior_hotspots = {h["path"]: h for h in prior.get("top_hotspots", [])}

    for path in prior_hotspots:
        if path not in current_hotspots:
            diff.graduated.append(HotspotTransition(path=path))

    for path, current_h in current_hotspots.items():
        if path not in prior_hotspots:
            diff.new.append(HotspotTransition(path=path))
            continue

        prior_h = prior_hotspots[path]
        ccn_delta = current_h.get("ccn", 0) - prior_h.get("ccn", 0)
        commits_delta = current_h.get("commits", 0) - prior_h.get("commits", 0)
        loc_delta = current_h.get("loc", 0) - prior_h.get("loc", 0)

        transition = HotspotTransition(
            path=path,
            ccn_delta=ccn_delta,
            commits_delta=commits_delta,
            loc_delta=loc_delta,
        )

        if ccn_delta > 0 or (loc_delta > 50 and commits_delta > 2):
            diff.regressed.append(transition)
        else:
            diff.persistent.append(transition)

    return diff
