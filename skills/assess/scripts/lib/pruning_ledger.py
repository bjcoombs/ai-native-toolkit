"""The pruning ledger: credit for removing a practice that cost more than it paid (#1084).

A removal is a done action whose ``finding`` is ``gap_actions.REMOVAL_FINDING`` (always-on
instruction text the placement scan flags), with its measured ``cost`` (``{metric, path,
value}``) and a ``completed_sha`` on HEAD's history. ``update_ledger`` keeps one
``PruningEntry`` per removal (keyed by path and commit) and counts the runs it has held:

- a run on a **new** measured commit adds one hold when the file is no longer flagged (the
  scan's own flag, ``instruction_placement`` ``flagged``: a file can clear it by size or by
  share) **and** no Step gate was lost since the previous run (``gates_lost == []``; a gate
  never held cannot be lost, so this checks for losses, not that every gate holds);
- either failing resets the count to 0, and an unknown (placement unavailable, a file the
  scan does not list while it is still on disk, gate changes not known) leaves it as it was,
  never counted;
- a re-run on the commit the previous run measured adds nothing, so superseding a run never
  counts twice;
- at ``HOLD_RUNS`` holds the entry is credited once, with ``saved`` the measured drop: the
  always-on lines at the proposal less the lines now (all of them when the file is gone
  from disk).

Credit is advice and record only: the number of practices is never scored, and a credited
removal moves no Step. The entries are stored in the committed ``prior-level.json`` beside
the baseline (``prior_level.PruningEntry``), so the count survives a fresh clone and CI.
``ledger_lines`` words the newly credited ("Pruned: ...") and the pending removals for the
"since last run" section. Tests: ``tests/test_pruning_ledger.py``.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any, cast

from lib.gap_actions import REMOVAL_FINDING
from lib.prior_level import PruningEntry

# Two runs on new commits, each with the file unflagged and no gate lost: one run shows
# the removal landed, the second that nothing came back or broke once work went on.
HOLD_RUNS = 2


def removal(entry: Mapping[str, Any]) -> PruningEntry | None:
    """A new ledger entry for one done removal action, or None when it is not one."""
    cost = entry.get("cost")
    if (entry.get("finding") != REMOVAL_FINDING or entry.get("status") != "done"
            or not isinstance(cost, Mapping) or not isinstance(cost.get("path"), str)
            or type(cost.get("value")) is not int or not isinstance(entry.get("completed_sha"), str)):
        return None
    return {"path": cost["path"], "completed_sha": entry["completed_sha"], "cost_before": cost["value"],
            "runs_held": 0, "credited": False, "saved": None}


def _placement_now(placement: Any, path: str, repo_root: Path) -> tuple[bool, int] | None:
    """``(flagged, always-on lines)`` for ``path`` now; ``(False, 0)`` only when the file is gone
    from disk. None (unknown) when the placement scan is unavailable or does not list a file that
    is still there: a nested scan that did not run, or an unreadable file, is no proof of a saving."""
    if not isinstance(placement, Mapping) or placement.get("available") is not True:
        return None
    for f in placement.get("files") or []:
        if isinstance(f, Mapping) and f.get("path") == path:
            lines = f.get("always_on_lines")
            return (f.get("flagged") is True, lines) if isinstance(lines, int) else None
    return None if (repo_root / path).exists() else (False, 0)


def update_ledger(prior: Sequence[PruningEntry], done_removals: Iterable[PruningEntry], placement: Any,
                  gates_lost: list[str] | None, new_commit: bool,
                  repo_root: Path) -> tuple[list[PruningEntry], list[PruningEntry]]:
    """``(every entry, the entries credited by this run)`` (see the module docstring)."""
    entries = {(e["path"], e["completed_sha"]): cast(PruningEntry, dict(e)) for e in prior}
    for e in done_removals:
        entries.setdefault((e["path"], e["completed_sha"]), cast(PruningEntry, dict(e)))
    credited: list[PruningEntry] = []
    for e in entries.values():
        now = _placement_now(placement, e["path"], repo_root)
        if e["credited"] or not new_commit or now is None or gates_lost is None:
            continue
        flagged, lines = now
        e["runs_held"] = e["runs_held"] + 1 if not flagged and not gates_lost else 0
        if e["runs_held"] >= HOLD_RUNS:
            e["credited"], e["saved"] = True, max(e["cost_before"] - lines, 0)
            credited.append(e)
    return [entries[k] for k in sorted(entries)], credited


def ledger_lines(entries: Sequence[PruningEntry], credited: Sequence[PruningEntry]) -> list[str]:
    """The "since last run" lines: each removal credited by this run, then each still pending."""
    lines = [f"Pruned: always-on text in `{e['path']}`, saved {e['saved']} always-on lines"
             for e in credited]
    lines += [f"Pruning in progress: `{e['path']}` ({e['runs_held']} of {HOLD_RUNS} runs with the file "
              "unflagged and no gate lost)" for e in entries if not e["credited"]]
    return lines
