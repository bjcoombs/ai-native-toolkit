"""Compare current complexity stats against a prior run.

Identifies hotspot transitions:
    graduated:    was in prior top_hotspots, absent from current
    regressed:    in both, and its worst function got worse - or, with the worst
                  function flat or unknown, the aggregate ccn or LOC-and-churn
                  got worse
    restructured: in both, the aggregate grew but the worst function fell - the
                  shape an extract-helper refactor leaves behind
    new:          in current top_hotspots, absent from prior
    persistent:   in both, roughly unchanged

Why the worst function decides: a file's ``ccn`` is a sum over its functions, so
splitting one 51-ccn function into five named helpers raises the sum (each
helper adds its own +1) while the complexity a reader must hold at once falls.
Keying regression on the sum told a contributor that the refactor the report
recommends made the file worse. ``max_fn_ccn`` is that per-function worst; it is
null for file-level backends (scc), where the aggregate rule still applies.

No LLM calls. Pure set operations + arithmetic.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class HotspotTransition:
    path: str
    ccn_delta: float = 0
    commits_delta: int = 0
    loc_delta: int = 0
    # Change in the worst single function's ccn; None when either snapshot
    # lacks a per-function breakdown (scc-scored files, older sidecars).
    max_fn_ccn_delta: float | None = None


@dataclass
class StatsDiff:
    graduated: list[HotspotTransition] = field(default_factory=list)
    regressed: list[HotspotTransition] = field(default_factory=list)
    restructured: list[HotspotTransition] = field(default_factory=list)
    new: list[HotspotTransition] = field(default_factory=list)
    persistent: list[HotspotTransition] = field(default_factory=list)

    def summary(self) -> dict[str, int]:
        return {
            "graduated": len(self.graduated),
            "regressed": len(self.regressed),
            "restructured": len(self.restructured),
            "new": len(self.new),
            "persistent": len(self.persistent),
        }


def load_stats(path: Path) -> dict | None:
    """Load stats JSON from path, or None if file doesn't exist."""
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def hotspot_commits(h: dict) -> int:
    """Commit count for a hotspot entry.

    Reads `commits` (current field name), falling back to the legacy `churn`
    key so a prior snapshot written by an older plugin still compares cleanly.
    """
    val = h.get("commits")
    if val is None:
        val = h.get("churn", 0)
    return int(val or 0)


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
        transition = HotspotTransition(
            path=path,
            ccn_delta=current_h.get("ccn", 0) - prior_h.get("ccn", 0),
            commits_delta=hotspot_commits(current_h) - hotspot_commits(prior_h),
            loc_delta=current_h.get("loc", 0) - prior_h.get("loc", 0),
            max_fn_ccn_delta=_max_fn_delta(prior_h, current_h),
        )
        classify(transition, diff)

    return diff


def _max_fn_delta(prior_h: dict, current_h: dict) -> float | None:
    """Worst-function ccn change, or None when either side has no breakdown."""
    before, after = prior_h.get("max_fn_ccn"), current_h.get("max_fn_ccn")
    if before is None or after is None:
        return None
    return after - before


def _aggregate_worsened(t: HotspotTransition) -> bool:
    """The file-level rule: higher summed ccn, OR >50 LOC growth across >2 commits.

    The compound branch is a churn proxy - a single large edit isn't a regression.
    """
    return t.ccn_delta > 0 or (t.loc_delta > 50 and t.commits_delta > 2)


def classify(t: HotspotTransition, diff: StatsDiff) -> None:
    """File a still-ranked hotspot under regressed, restructured or persistent.

    The worst function leads: up is regressed, down with a worsened aggregate is
    restructured. Flat or unknown falls back to the aggregate rule, so adding a
    new function beside an unchanged worst one (accretion) still regresses.
    """
    worst = t.max_fn_ccn_delta
    if worst is not None and worst > 0:
        diff.regressed.append(t)
    elif worst is not None and worst < 0:
        target = diff.restructured if _aggregate_worsened(t) else diff.persistent
        target.append(t)
    elif _aggregate_worsened(t):
        diff.regressed.append(t)
    else:
        diff.persistent.append(t)
