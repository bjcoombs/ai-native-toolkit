"""The pruning ledger: credit for removing a practice that cost more than it paid (#1084).

A removal is a done action whose ``finding`` is ``gap_actions.REMOVAL_FINDING`` (always-on
instruction text the placement scan flags), with its measured ``cost`` (``{metric, path,
value}``) and a ``completed_sha`` on HEAD's history. ``update_ledger`` keeps one
``PruningEntry`` per removal (keyed by path and commit) and counts the runs it has held:

- an entry records the Step gates it must keep when it is first seen (``gates``: those held at
  the previous run, before the removal landed, else those held now);
- a run on a **new** measured commit adds one hold when the file is no longer flagged (the
  scan's own flag, ``instruction_placement`` ``flagged``) **with** fewer always-on lines than
  at the proposal (a file can clear the flag by share alone, saving nothing) **and** every one
  of the entry's ``gates`` is still held, so a gate the removal broke
  for good never stops counting against it;
- either failing resets the count to 0, even when the other reads unknown; an unknown alone
  (placement unavailable, a file the scan does not list while it is still on disk, the gates
  not known) leaves it as it was, never counted;
- a re-run on the commit the previous run measured adds nothing, so superseding a run never
  counts twice;
- several pending removals of one file (a file trimmed in rounds) fold into one entry measured
  from the first round's starting size (``_merge_rounds``, #1168), so one continuous drop is
  credited once (the first round is the one an earlier run stored; rounds first seen in the same
  run use the largest start);
- a pending entry whose removal is no longer a done action on HEAD's history (squashed away,
  reopened, deleted) is dropped; a credited one stays as the record. When the done removals
  cannot be read (``actions.json`` not committed or not readable, a shallow clone, git failing)
  the ledger is left exactly as it was: no drop and no hold;
- at ``HOLD_RUNS`` holds the entry is credited once, with ``saved`` the measured drop: the
  always-on lines at the proposal less the lines now (all of them when the file is gone
  from disk), and ``credited_at``, the commit; a re-run on that commit shows the credit again.

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
            "runs_held": 0, "credited": False, "saved": None, "gates": None, "credited_at": None}


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


def _merge_rounds(entries: dict[tuple[str, str], PruningEntry], stored: set[tuple[str, str]]) -> None:
    """Fold several pending removals of one file into one entry (#1168), so one continuous drop
    is credited once, measured from the first round's starting size.

    The first round is the pending entry already stored by an earlier run (at most one per file,
    since earlier runs folded the rest); among removals first seen in the same run, the largest
    ``cost_before`` stands in for it. The kept entry is the newest removal (of the new ones, the
    smallest ``cost_before``) with its commit and hold count; it takes the first round's starting
    size and gates and lists the folded commits in ``merged`` so they are never added again. A
    file that regrows between rounds is still measured from the first round's start when that
    round was stored by an earlier run; rounds first seen together in one run use the largest
    start instead."""
    by_path: dict[str, list[tuple[str, str]]] = {}
    for key, e in entries.items():
        if not e["credited"]:
            by_path.setdefault(e["path"], []).append(key)
    for keys in by_path.values():
        if len(keys) < 2:
            continue
        # stored (older) rounds first, then the new ones from the largest start to the smallest
        rounds = sorted((entries[k] for k in keys),
                        key=lambda e: ((e["path"], e["completed_sha"]) not in stored, -e["cost_before"],
                                       e["completed_sha"]))
        first, newest = rounds[0], rounds[-1]
        folded = sorted({sha for e in rounds[:-1] for sha in (e["completed_sha"], *e.get("merged", []))}
                        | set(newest.get("merged", [])))
        newest["cost_before"], newest["gates"] = first["cost_before"], first["gates"]
        newest["merged"] = folded
        for e in rounds[:-1]:
            del entries[(e["path"], e["completed_sha"])]


def update_ledger(prior: Sequence[PruningEntry], done_removals: Iterable[PruningEntry] | None, placement: Any, *,
                  held_now: list[str] | None, held_before: list[str] | None, head_sha: str | None,
                  new_commit: bool, repo_root: Path) -> tuple[list[PruningEntry], list[PruningEntry]]:
    """``(every entry, the entries credited at this commit)`` (see the module docstring).

    ``held_now`` and ``held_before`` are the Step gates held now and at the previous run (None
    when unknown). An entry records the gates it must keep when it is first seen: the gates
    held before the removal landed, else those held now."""
    if done_removals is None:  # the done removals are unknown: leave the ledger as it was
        kept = [cast(PruningEntry, dict(e)) for e in prior]
        return kept, [e for e in kept if e["credited"] and head_sha and e["credited_at"] == head_sha]
    done = {(e["path"], e["completed_sha"]): e for e in done_removals}
    # a pending entry counts only while its removal is still done on HEAD's history: one
    # squashed away, reopened or deleted is dropped, so it never counts beside a fresh entry
    # a folded entry (#1168) needs every round it folded still done, else its starting size
    # would overstate the saving: dropped, its surviving rounds fold again from scratch
    entries = {(e["path"], e["completed_sha"]): cast(PruningEntry, dict(e)) for e in prior
               if e["credited"] or all((e["path"], sha) in done
                                       for sha in (e["completed_sha"], *e.get("merged", [])))}
    stored = {k for k, e in entries.items() if not e["credited"]}
    absorbed = {(e["path"], sha) for e in entries.values() for sha in e.get("merged", [])}
    for key, e in done.items():
        if key not in absorbed:
            entries.setdefault(key, cast(PruningEntry, dict(e)))
    _merge_rounds(entries, stored)
    for e in entries.values():
        if e["credited"] or not new_commit:
            continue
        if e["gates"] is None:
            e["gates"] = held_before if held_before is not None else held_now
        now = _placement_now(placement, e["path"], repo_root)
        # unflagged by share alone (more fetch-on-need text, the same always-on lines) saves nothing
        flagged = now is not None and (now[0] or now[1] >= e["cost_before"])
        gate_lost = held_now is not None and e["gates"] is not None and any(g not in held_now for g in e["gates"])
        if flagged or gate_lost:  # a known failure resets, even beside an unknown
            e["runs_held"] = 0
            continue
        if now is None or held_now is None or e["gates"] is None:
            continue  # unknown: neither counted nor reset
        e["runs_held"] += 1
        if e["runs_held"] >= HOLD_RUNS:
            e["credited"], e["saved"], e["credited_at"] = True, max(e["cost_before"] - now[1], 0), head_sha
    ordered = [entries[k] for k in sorted(entries)]
    # credited at this commit: a re-run on the same commit shows the same credit again
    return ordered, [e for e in ordered if e["credited"] and head_sha and e["credited_at"] == head_sha]


def ledger_lines(entries: Sequence[PruningEntry], credited: Sequence[PruningEntry]) -> list[str]:
    """The "since last run" lines: each removal credited by this run, then each still pending."""
    lines = [f"Pruned: always-on text in `{e['path']}`, saved {e['saved']} always-on lines"
             for e in credited]
    lines += [f"Pruning in progress: `{e['path']}` ({e['runs_held']} of {HOLD_RUNS} runs with the file "
              "unflagged and no gate lost)" for e in entries if not e["credited"]]
    return lines
