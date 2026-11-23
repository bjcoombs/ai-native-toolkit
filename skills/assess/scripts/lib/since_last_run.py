"""What changed since the previous /assess run: actions closed, gates gained or lost, the next blocker (#1083).

The report and the ``log.md`` entry open with this section, so the credit a team sees is
the work it closed, not a count of catalogue entries. It reads three things, all before
this run's publish step overwrites any of them:

- **the previous run**: ``run-context.json`` in the run's ``.assess/`` directory as the
  previous run left it. None there means this is the first run, and the section says so.
  A file that cannot be read gives unknowns (``None``), never a first run;
- **closed actions**: the entries of ``actions.json`` *as committed at HEAD* (``actions``
  and ``completed``, as ``lib/reviewed_findings.py`` reads them) with ``status: "done"``
  and a ``completed_sha`` that is on HEAD's history and is not an ancestor of the commit
  the previous run measured (``measured_commit.head_sha``). An action closed before the
  previous run is not credited again, and a branch commit squashed or rebased away is never
  credited. A sha this clone does not hold is left out and counted in ``unresolved``.
  Action text is folded onto one line, so it cannot inject a log heading or chain marker.
  A committed ``actions.json`` that cannot be parsed gives ``None`` (``actions_unreadable``).
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
lines. ``since_last_run(level, assess_dir, repo_root)`` is the ``since_last_run`` scan
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


def _committed_actions(repo_root: Path, assess_dir: Path) -> list[dict[str, Any]] | None:
    """The action entries of ``actions.json`` as committed at HEAD: none when it is absent,
    ``None`` when it is committed but cannot be read (unknown, never zero)."""
    try:
        rel = assess_dir.resolve().relative_to(repo_root.resolve()).as_posix()
    except ValueError:
        return []
    shown = _git(repo_root, "show", f"HEAD:./{rel}/actions.json")
    if shown is None:
        return None
    if shown.returncode != 0:
        return []
    try:
        payload = json.loads(shown.stdout)
    except json.JSONDecodeError:
        return None
    return action_entries(payload) if isinstance(payload, Mapping) else None


def _one_line(text: str) -> str:
    """Action text on one line, so it can never start a log heading or a chain marker."""
    return " ".join(text.split())


def is_commit(repo_root: Path, sha: str) -> bool:
    """Whether ``sha`` names a commit in this clone."""
    found = _git(repo_root, "cat-file", "-e", f"{sha}^{{commit}}")
    return found is not None and found.returncode == 0


def _ancestor(repo_root: Path, sha: str, of: str) -> int | None:
    """``merge-base --is-ancestor``'s exit code: 0 an ancestor, 1 not; None when git cannot run."""
    done = _git(repo_root, "merge-base", "--is-ancestor", sha, of)
    return None if done is None else done.returncode


def closed_actions(repo_root: Path, assess_dir: Path, previous: str) -> tuple[list[ClosedAction], int] | None:
    """The done actions at HEAD whose ``completed_sha`` landed after ``previous``, in file order,
    and how many done entries name a commit this clone does not hold (left unchecked).

    A sha counts only when it is on HEAD's history and not on ``previous``'s, so a branch
    commit that was squashed or rebased away is never credited, once or again. ``None`` when
    the committed ``actions.json`` cannot be read."""
    entries = _committed_actions(repo_root, assess_dir)
    if entries is None:
        return None
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


def _previous(assess_dir: Path) -> tuple[bool, Mapping[str, Any] | None]:
    """``(first_run, previous context)``: no file is a first run; an unreadable one is unknown."""
    path = assess_dir / "run-context.json"
    if not path.is_file():
        return True, None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False, None
    return False, data if isinstance(data, Mapping) else None


def _noun(gate: str) -> str:
    return GATE_NOUN.get(gate, gate)


def _gates_line(word: str, gates: list[str] | None) -> str:
    if gates is None:
        return f"Gates {word}: not known (the previous run has no level result)"
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
        lines += [_gates_line("gained", block["gates_gained"]), _gates_line("lost", block["gates_lost"])]
    if block["next_blocker"] is not None:
        lines.append(f"Next blocker: {_noun(block['next_blocker'])}")
    else:
        lines.append("Next blocker: none, every gate is held" if has_steps
                     else "Next blocker: none, the model defines no Steps")
    return lines


def _markdown(lines: list[str]) -> str:
    return "\n".join(f"  - {line.strip()}" if line.startswith("  ") else f"- {line}" for line in lines)


def since_last_run(level: Any, assess_dir: Path, repo_root: Path) -> SinceLastRun:
    """The ``since_last_run`` block for this run's ``level`` block (see the module docstring)."""
    steps = default_model().get("steps", [])
    first, prev = _previous(assess_dir)
    commit = (prev or {}).get("measured_commit")
    previous_commit = commit.get("head_sha") if isinstance(commit, Mapping) else None
    previous_commit = previous_commit if isinstance(previous_commit, str) and previous_commit else None
    run_id = (prev or {}).get("run_id")
    closed: list[ClosedAction] | None = None
    unresolved: int | None = None
    reason: str | None = None
    if first:
        pass
    elif previous_commit is None:
        reason = "no_previous_commit"
    elif not is_commit(repo_root, previous_commit):
        reason = "previous_commit_absent"  # a rewritten history or a shallow clone: not zero
    elif (found := closed_actions(repo_root, assess_dir, previous_commit)) is None:
        reason = "actions_unreadable"
    else:
        closed, unresolved = found
    gained, lost = gate_changes(steps, level, (prev or {}).get("level")) if not first else (None, None)
    current = level if isinstance(level, Mapping) else {}
    block: SinceLastRun = {
        "available": True, "first_run": first,
        "previous_run_id": run_id if isinstance(run_id, str) else None,
        "previous_commit": previous_commit, "closed": closed, "closed_reason": reason,
        "closed_count": None if closed is None else len(closed), "unresolved": unresolved,
        "gates_gained": gained, "gates_lost": lost, "next_blocker": _next_blocker(current),
        "lines": [], "markdown": ""}
    block["lines"] = _words(block, bool(steps))
    block["markdown"] = _markdown(block["lines"])
    return block
