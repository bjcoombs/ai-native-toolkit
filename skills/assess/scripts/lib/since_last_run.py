"""What changed since the previous /assess run: actions closed, gates gained or lost, the next blocker (#1083).

The report and the ``log.md`` entry open with this section, so the credit a team sees is
the work it closed, not a count of catalogue entries. It reads three things, all before
this run's publish step overwrites any of them:

- **the previous run**: ``run-context.json`` in the run's ``.assess/`` directory as the
  previous run left it. None there means this is the first run, and the section says so.
  A file that cannot be read gives unknowns (``None``, ``previous_unreadable``), never a
  first run. A run that supersedes a same-day run on the same commit (the ``superseded``
  scan input) takes that run's own baseline from its ``since_last_run`` block instead, so
  the credit that run showed survives its log entry being replaced;
- **closed actions**: the entries of the whole-repo ``.assess/actions.json`` *as committed
  at HEAD*, the one file finalize writes whatever the run's scope (``actions`` and
  ``completed``, as ``lib/reviewed_findings.py`` reads them) with ``status: "done"``
  and a ``completed_sha`` that is on HEAD's history and is not an ancestor of the commit
  the previous run measured (``measured_commit.head_sha``). An action closed before the
  previous run is not credited again, and a branch commit squashed or rebased away is never
  credited. A sha this clone does not hold is left out and counted in ``unresolved``.
  Action text is folded onto one line with ``<!--`` escaped, so it cannot inject a log
  heading or chain marker. An ``actions.json`` not committed at HEAD, or one that cannot be
  parsed, gives ``None`` (``actions_not_committed``, ``actions_unreadable``): a zero always
  means zero.
  Reading HEAD, not the working tree, keeps the block a function of the commit. With no
  previous commit, or one this clone does not hold (a rewritten history, a shallow clone),
  the list is ``None`` (unknown, ``closed_reason`` says why), never empty;
- **gates**: every gate of the default model's Steps, held when
  ``level_tally.gate_blockers`` names no blocker for it, in this run's ``level`` block and
  in the previous run's. ``gates_gained`` and ``gates_lost`` list the ids in Step order;
  ``None`` when the previous run has no level result or one that lacks a gate's
  condition (an older model), so the rest of the block still reads.

``next_blocker`` is the first gate blocking this run's next Step (``None`` at the top Step).
``lines`` and ``markdown`` word the block (``GATE_NOUN`` names each gate, as the badge
does); counts-only output keeps the counts, ids and shas and drops the action text and the
lines. ``since_last_run(level, assess_dir, repo_root, superseded)`` is the ``since_last_run`` scan
(``core_scans.since_last_run``). Shape: ``SinceLastRun`` in
``run_context_types_since.py``. Tests: ``tests/test_since_last_run.py``.
"""
from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from lib.headline import GATE_NOUN
from lib.level_tally import default_model, gate_blockers
from lib.levels import StepDef
from lib.reviewed_findings import action_entries
from lib.run_context_types_since import ClosedAction, SinceLastRun

__all__ = ["ClosedAction", "SinceLastRun", "closed_actions", "gate_changes", "since_last_run"]

_SHA = re.compile(r"[0-9a-f]{7,40}")
FIRST_RUN = "This is the first run, so there is no earlier run to compare with."
# Why ``closed`` is unknown, as the line words each ``closed_reason`` code.
UNKNOWN_CLOSED = {
    "no_previous_commit": "the previous run recorded no commit",
    "previous_commit_absent": "the previous run's commit is not in this clone",
    "actions_unreadable": "the committed actions.json could not be read",
    "actions_not_committed": "no .assess/actions.json is committed; commit it for closed actions to count",
    "previous_unreadable": "the previous run-context.json could not be read",
}


def _git(repo_root: Path, *args: str) -> subprocess.CompletedProcess[str] | None:
    """Run git in ``repo_root``; None when git cannot run."""
    try:
        return subprocess.run(["git", "-C", str(repo_root), *args], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", check=False, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None


def _gate_ids(steps: Sequence[StepDef]) -> list[str]:
    """Every gate id of the Steps, once, in Step order."""
    return list(dict.fromkeys(g["id"] for s in steps for g in s["gates"]))


def _held(steps: Sequence[StepDef], level: Any) -> list[str] | None:
    """The gates a level block holds, in Step order; None when it is not a level result or
    lacks a gate's condition (a block from an older model)."""
    if not isinstance(level, Mapping) or not isinstance(level.get("conditions"), list):
        return None
    ids = {c.get("id") for c in level["conditions"] if isinstance(c, Mapping)}
    if any(f"{g['row']}.{n}" not in ids for s in steps for g in s["gates"] for n in range(1, g["level"] + 1)):
        return None
    blocked = {b.get("gate") for b in gate_blockers(steps, level["conditions"])}
    return [g for g in _gate_ids(steps) if g not in blocked]


def gate_changes(steps: Sequence[StepDef], level: Any,
                 previous: Any) -> tuple[list[str] | None, list[str] | None]:
    """``(gained, lost)`` gate ids between the previous level block and this one."""
    now, then = _held(steps, level), _held(steps, previous)
    if now is None or then is None:
        return None, None
    return [g for g in now if g not in then], [g for g in then if g not in now]


def _committed_actions(repo_root: Path) -> tuple[list[dict[str, Any]] | None, str | None]:
    """The action entries of the whole-repo ``.assess/actions.json`` as committed at HEAD (the
    one file finalize writes, whatever the run's scope), or ``None`` with the reason it is
    unknown: not committed, or not readable. Never an empty list standing in for unknown."""
    shown = _git(repo_root, "show", "HEAD:./.assess/actions.json")
    if shown is None:
        return None, "actions_unreadable"
    if shown.returncode != 0:
        return None, "actions_not_committed"
    try:
        payload = json.loads(shown.stdout)
    except json.JSONDecodeError:
        return None, "actions_unreadable"
    return (action_entries(payload), None) if isinstance(payload, Mapping) else (None, "actions_unreadable")


def _one_line(text: str) -> str:
    """Action text on one line with ``<!--`` escaped, so it can never start a log heading
    or carry a chain marker into ``log.md`` (``wiki_writer`` matches markers anywhere)."""
    return " ".join(text.split()).replace("<!--", "&lt;!--")


def is_commit(repo_root: Path, sha: str) -> bool:
    """Whether ``sha`` names a commit in this clone."""
    found = _git(repo_root, "cat-file", "-e", f"{sha}^{{commit}}")
    return found is not None and found.returncode == 0


def _ancestor(repo_root: Path, sha: str, of: str) -> int | None:
    """``merge-base --is-ancestor``'s exit code: 0 an ancestor, 1 not; None when git cannot run."""
    done = _git(repo_root, "merge-base", "--is-ancestor", sha, of)
    return None if done is None else done.returncode


def closed_actions(repo_root: Path, previous: str) -> tuple[list[ClosedAction], int] | str:
    """The done actions at HEAD whose ``completed_sha`` landed after ``previous``, in file order,
    and how many done entries name a commit this clone does not hold (left unchecked).

    A sha counts only when it is on HEAD's history and not on ``previous``'s, so a branch
    commit that was squashed or rebased away is never credited, once or again. A reason code
    instead when the committed ``actions.json`` is not there or cannot be read."""
    entries, why = _committed_actions(repo_root)
    if entries is None:
        return why or "actions_unreadable"
    out: list[ClosedAction] = []
    seen: set[tuple[str, str]] = set()
    unresolved = 0
    for entry in entries:
        sha, action = entry.get("completed_sha"), entry.get("action")
        if entry.get("status") != "done" or not isinstance(sha, str) or not _SHA.fullmatch(sha):
            continue
        text = _one_line(action) if isinstance(action, str) else ""
        if (text, sha) in seen:
            continue
        if not is_commit(repo_root, sha):
            unresolved += 1
            continue
        if _ancestor(repo_root, sha, "HEAD") != 0 or _ancestor(repo_root, sha, previous) != 1:
            continue
        seen.add((text, sha))
        condition, finding = entry.get("condition"), entry.get("finding")
        out.append({"action": text, "condition": condition if isinstance(condition, str) else None,
                    "finding": finding if isinstance(finding, str) else None, "completed_sha": sha})
    return out, unresolved


def _noun(gate: str) -> str:
    return GATE_NOUN.get(gate, gate)


def _gates_line(word: str, gates: list[str] | None, unreadable: bool) -> str:
    if gates is None:
        why = ("the previous run-context.json could not be read" if unreadable
               else "the previous run has no level result this model can compare")
        return f"Gates {word}: not known ({why})"
    return f"Gates {word}: " + (", ".join(_noun(g) for g in gates) if gates else "none")


def _next_blocker(level: Mapping[str, Any]) -> str | None:
    nxt = level.get("next_step")
    blockers = nxt.get("blockers", []) if isinstance(nxt, Mapping) else []
    return next((b["gate"] for b in blockers if b.get("kind") == "gate" and b.get("gate")), None)


def _words(block: SinceLastRun, has_steps: bool) -> list[str]:
    """The section's lines; a closed action is an indented line under its count."""
    if block["first_run"]:
        lines = [FIRST_RUN]
    else:
        closed = block["closed"]
        if closed is None:
            lines = [f"Closed since last run: not known ({UNKNOWN_CLOSED.get(block['closed_reason'] or '', 'not read')})"]
        else:
            lines = [f"Closed since last run: {len(closed)}"]
            lines += [f"  `{c['completed_sha'][:7]}` {c['action']}".rstrip() for c in closed]
            if block["unresolved"]:
                lines.append(f"  {block['unresolved']} more done action(s) name a commit not in this clone, "
                             "so they were not checked")
        unreadable = block["closed_reason"] == "previous_unreadable"
        lines += [_gates_line("gained", block["gates_gained"], unreadable),
                  _gates_line("lost", block["gates_lost"], unreadable)]
    if block["next_blocker"] is not None:
        lines.append(f"Next blocker: {_noun(block['next_blocker'])}")
    else:
        lines.append("Next blocker: none, every gate is held" if has_steps
                     else "Next blocker: none, the model defines no Steps")
    return lines


def _markdown(lines: list[str]) -> str:
    return "\n".join(f"  - {line.strip()}" if line.startswith("  ") else f"- {line}" for line in lines)


def _baseline(assess_dir: Path, superseded: Any) -> tuple[bool, bool, Mapping[str, Any] | None,
                                                         Mapping[str, Any] | None]:
    """``(first_run, unreadable, previous context, inherited block)``.

    A run that supersedes a same-day run on the same commit (``superseded``) takes that run's
    own baseline from its ``since_last_run`` block, so the credit it showed is not lost when
    its log entry is replaced; the commit is the same, so its gate changes still hold."""
    if isinstance(superseded, Mapping):
        inherited = superseded.get("since_last_run")
        if isinstance(inherited, Mapping) and inherited.get("available") is True:
            return bool(inherited.get("first_run")), False, None, inherited
    path = assess_dir / "run-context.json"
    if not path.is_file():
        return True, False, None, None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False, True, None, None
    return (False, False, data, None) if isinstance(data, Mapping) else (False, True, None, None)


def since_last_run(level: Any, assess_dir: Path, repo_root: Path, superseded: Any = None) -> SinceLastRun:
    """The ``since_last_run`` block for this run's ``level`` block (see the module docstring)."""
    steps = default_model().get("steps", [])
    first, unreadable, prev, inherited = _baseline(assess_dir, superseded)
    if inherited is not None:
        previous_commit, run_id = inherited.get("previous_commit"), inherited.get("previous_run_id")
        gained, lost = inherited.get("gates_gained"), inherited.get("gates_lost")
    else:
        commit = (prev or {}).get("measured_commit")
        previous_commit = commit.get("head_sha") if isinstance(commit, Mapping) else None
        run_id = (prev or {}).get("run_id")
        gained, lost = gate_changes(steps, level, (prev or {}).get("level")) if prev else (None, None)
    previous_commit = previous_commit if isinstance(previous_commit, str) and previous_commit else None
    closed: list[ClosedAction] | None = None
    unresolved: int | None = None
    reason: str | None = None
    if first:
        pass
    elif unreadable:
        reason = "previous_unreadable"
    elif previous_commit is None:
        reason = "no_previous_commit"
    elif not is_commit(repo_root, previous_commit):
        reason = "previous_commit_absent"  # a rewritten history or a shallow clone: not zero
    elif isinstance(found := closed_actions(repo_root, previous_commit), str):
        reason = found
    else:
        closed, unresolved = found
    current = level if isinstance(level, Mapping) else {}
    block: SinceLastRun = {
        "available": True, "first_run": first,
        "previous_run_id": run_id if isinstance(run_id, str) else None,
        "previous_commit": previous_commit, "closed": closed, "closed_reason": reason,
        "closed_count": None if closed is None else len(closed), "unresolved": unresolved,
        "gates_gained": gained if isinstance(gained, list) else None,
        "gates_lost": lost if isinstance(lost, list) else None,
        "next_blocker": _next_blocker(current), "lines": [], "markdown": ""}
    block["lines"] = _words(block, bool(steps))
    block["markdown"] = _markdown(block["lines"])
    return block
