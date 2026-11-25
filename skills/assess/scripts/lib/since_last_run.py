"""What changed since the previous /assess run: actions closed, gates gained or lost, the next blocker as data (#1083).

The ``log.md`` entry opens with this section and the report's top card (``lib/top_card.py``,
#1252) states what moved in one line, so the credit a team sees is the work it closed, not a count of catalogue entries. It reads three things, all before
this run's publish step overwrites any of them:

- **the previous run**: for the default model and the shipped profiles, the committed
  baseline in ``prior-level.json`` only (``prior_level.load_baseline``, #1122: the run id,
  its measured commit and the gates it held, written by ``baseline_record`` at publish), the
  run's own model's record (#1184): the default model's, or a profile's under ``models``.
  A fresh clone or a CI checkout has it, so the block reads the same there as on the
  machine that ran the last run. No baseline there makes this run the baseline (#1256):
  ``first_run`` is true and the section reads ``FIRST_RUN`` once, whatever ``log.md`` and
  the local, gitignored ``run-context.json`` hold. That holds only when this run can record
  a baseline (a HEAD commit, a level result with every gate's condition, and the tags
  ``build_prior_level`` needs); otherwise no baseline is written and the claim would repeat
  on every run, so the previous state is unknown (``baseline_not_recorded``). A ``prior-level.json`` that cannot be
  read gives unknowns (``None``, ``baseline_unreadable``), never a first run. A custom
  ``--levels`` run keeps no committed record, so it keeps the older order:
  ``run-context.json`` in the run's ``.assess/`` directory as the previous run left it,
  else a first run when ``log.md`` records no earlier run either, else unknown
  (``previous_state_missing``); an unreadable context is ``previous_unreadable``.
  A run that supersedes a same-day run on the same commit (the ``superseded``
  scan input) takes that run's own baseline from its ``since_last_run`` block instead, so
  the credit that run showed survives its log entry being replaced;
- **closed actions**: the entries of the whole-repo ``.assess/actions.json`` *as committed
  at HEAD*, the one file finalize writes whatever the run's scope (``actions`` and
  ``completed``, as ``lib/report/reviewed_findings.py`` reads them) with ``status: "done"``
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

The **pruning ledger** (``lib/pruning_ledger.py``, #1084) runs here too, on a default-model run
only (a profile run keeps it as stored and credits nothing, since only a default run writes it, #1184): each done removal
action in the committed ``actions.json`` is tracked in ``pruning``, stored in ``prior-level.json``
beside the baseline (``pruning_record``), and ``pruned`` lists the removals this run credits
("Pruned: ..."), counted once per measured commit against the commit that wrote the stored
ledger, so a superseding run never adds a second hold.

When this run's level result cannot be recorded (``prior_level.build_prior_level`` returns None,
so ``prior-level.json`` is left as it was, #1158), the section adds "Baseline kept: ..." naming
the run whose stored baseline the next run will compare with.

``next_blocker`` is the first gate blocking this run's next Step (``None`` at the top Step,
or when this run's level result is unavailable). It is data only: no line words it, because
the report's top card (``lib/top_card.py``, #1252) and the ``log.md`` readiness line state the
next gate once. A first run's lines are ``FIRST_RUN`` alone, the wording the top card uses.
A shallow clone, or a git that cannot answer an ancestry check, reads
the closed actions as unknown (``shallow_clone``, ``ancestry_unknown``), never as zero.
``lines`` and ``markdown`` word the block (``GATE_NOUN`` names each gate, as the badge
does); counts-only output keeps the counts, ids and shas and drops the action text and the
lines. ``since_last_run(level, assess_dir, repo_root, superseded, instruction_placement)`` is the
``since_last_run`` scan
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

from lib.scoring.headline import GATE_NOUN
from lib.scoring.level_tally import default_model, gate_blockers
from lib.scoring.levels import StepDef
from lib.prior_level import (Baseline, PruningEntry, as_prior_level, build_prior_level, default_level,
                             load_baseline, load_prior_level, load_pruning, model_key)
from lib.profiles import model_for_level
from lib.pruning_ledger import ledger_lines, removal, update_ledger
from lib.report.reviewed_findings import action_entries
from lib.run_context_types_since import ClosedAction, SinceLastRun

__all__ = ["ClosedAction", "SinceLastRun", "closed_actions", "gate_changes", "since_last_run"]

_SHA = re.compile(r"[0-9a-f]{7,40}")
# #1256: a run with no committed baseline becomes the baseline; the top card states it once (#1252)
FIRST_RUN = "This run is your baseline; the next `/assess` run reports what moved."
# Why ``closed`` is unknown, as the line words each ``closed_reason`` code.
UNKNOWN_CLOSED = {
    "no_previous_commit": "the previous run recorded no commit",
    "previous_commit_absent": "the previous run's commit is not in this clone",
    "actions_unreadable": "the committed actions.json could not be read",
    "actions_not_committed": "no .assess/actions.json is committed; commit it for closed actions to count",
    "baseline_unreadable": "the committed .assess/prior-level.json could not be read",
    "baseline_not_recorded": "no baseline is committed yet, and this run's result cannot be recorded as one",
    "previous_unreadable": "the previous run's local record could not be read; re-run /assess to rebuild it",
    "previous_state_missing": ("log.md records an earlier run, but a custom --levels run keeps no committed "
                               "baseline and this machine holds no record of it"),
    "shallow_clone": "this is a shallow clone, so commit ancestry cannot be checked",
    "previous_not_ancestor": "the previous run measured a commit that is not in this branch's history",
    "ancestry_unknown": "git could not check a commit's ancestry",
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


def baseline_record(ctx: Mapping[str, Any]) -> Baseline | None:
    """This run's committed baseline for ``prior-level.json`` (#1122): its id, the commit it
    measured and the gates it held; None with no measured commit or no level result."""
    commit = ctx.get("measured_commit")
    sha = commit.get("head_sha") if isinstance(commit, Mapping) else None
    held = _held(_steps(ctx.get("level")), ctx.get("level"))
    if not isinstance(sha, str) or not sha or held is None:
        return None
    run_id = ctx.get("run_id")
    return {"run_id": run_id if isinstance(run_id, str) else None, "head_sha": sha, "held_gates": held}


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
    lists = [payload.get(k) for k in ("actions", "completed")] if isinstance(payload, Mapping) else []
    if not isinstance(payload, Mapping) or any(v is not None and not isinstance(v, list) for v in lists):
        return None, "actions_unreadable"
    return action_entries(payload), None


def _one_line(text: str) -> str:
    """Action text on one line with ``<!--`` escaped, so it can never start a log heading
    or carry a chain marker into ``log.md`` (``wiki_writer`` matches markers anywhere)."""
    return " ".join(text.split()).replace("<!--", "&lt;!--")


def is_commit(repo_root: Path, sha: str) -> bool:
    """Whether ``sha`` names a commit in this clone."""
    found = _git(repo_root, "cat-file", "-e", f"{sha}^{{commit}}")
    return found is not None and found.returncode == 0


def _shallow(repo_root: Path) -> bool:
    """Whether the clone is shallow (or git cannot say): its ancestry is incomplete."""
    found = _git(repo_root, "rev-parse", "--is-shallow-repository")
    return found is None or found.returncode != 0 or found.stdout.strip() != "false"


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
        on_head, before = _ancestor(repo_root, sha, "HEAD"), _ancestor(repo_root, sha, previous)
        if on_head not in (0, 1) or before not in (0, 1):
            return "ancestry_unknown"  # git could not answer: unknown, never a silent zero
        if on_head != 0 or before != 1:
            continue
        seen.add((text, sha))
        condition, finding = entry.get("condition"), entry.get("finding")
        out.append({"action": text, "condition": condition if isinstance(condition, str) else None,
                    "finding": finding if isinstance(finding, str) else None, "completed_sha": sha})
    return out, unresolved


def _noun(gate: str) -> str:
    return GATE_NOUN.get(gate, gate)


def _gates_line(word: str, gates: list[str] | None, reason: str | None) -> str:
    if gates is None:
        why = (UNKNOWN_CLOSED[reason] if reason in ("baseline_unreadable", "baseline_not_recorded",
                                                    "previous_unreadable", "previous_state_missing")
               else "the previous run has no level result this model can compare")
        return f"Gates {word}: not known ({why})"
    return f"Gates {word}: " + (", ".join(_noun(g) for g in gates) if gates else "none")


def _next_blocker(level: Mapping[str, Any]) -> str | None:
    nxt = level.get("next_step")
    blockers = nxt.get("blockers", []) if isinstance(nxt, Mapping) else []
    return next((b["gate"] for b in blockers if b.get("kind") == "gate" and b.get("gate")), None)


def _words(block: SinceLastRun, kept_run_id: str | None = None) -> list[str]:
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
        lines += [_gates_line("gained", block["gates_gained"], block["closed_reason"]),
                  _gates_line("lost", block["gates_lost"], block["closed_reason"])]
    lines += ledger_lines(block["pruning"], block["pruned"])
    if kept_run_id:
        # #1158: this run leaves prior-level.json as it was, so the next run compares with the run
        # that wrote it: name that run, not whichever run this one compared with
        lines.append(f"Baseline kept: this run's level result could not be recorded, so the baseline "
                     f"stays the previous good run, `{kept_run_id}`")
    # the next blocker is the top card's Blocks line and the log.md readiness line (#1252)
    return lines


def _markdown(lines: list[str]) -> str:
    return "\n".join(f"  - {line.strip()}" if line.startswith("  ") else f"- {line}" for line in lines)


def _steps(level: Any) -> list[StepDef]:
    """The Steps of ``level``'s model (``profiles.model_for_level``), the default model's for a
    custom ``--levels`` model the run cannot load (#1184)."""
    return (model_for_level(level) or default_model()).get("steps", [])


def _baseline(assess_dir: Path, superseded: Any, level: Any = None
              ) -> tuple[bool, str | None, Mapping[str, Any] | None, Mapping[str, Any] | None]:
    """``(first_run, why the previous state is unknown, previous context, inherited block)``.

    Read order: the superseded run's own block (below), then the committed baseline in
    ``prior-level.json`` (#1122, returned as ``{run_id, head_sha, held_gates}``). For the
    default model and the shipped profiles that is all (#1256): no baseline there makes this
    run the baseline, and an unreadable file is ``baseline_unreadable``. Only a custom
    ``--levels`` model, which keeps no committed record, goes on to ``run-context.json`` and
    then ``log.md``: no context is a first run only when ``log.md`` records no earlier run
    either, else ``previous_state_missing``. Inheriting a superseded run's block needs that
    run's ``run-context.json``; without it a same-day re-run on the same commit reads the
    committed baseline, whose commit is HEAD, and so credits nothing new: the superseded run's
    own entry in ``log.md`` stays as the record of what it credited.

    A run that supersedes a same-day run on the same commit (``superseded``) takes that run's
    own baseline from its ``since_last_run`` block, so the credit it showed is not lost when
    its log entry is replaced; the commit is the same, so its gate changes still hold.

    The committed baseline read is ``level``'s model's record (``prior_level.load_baseline``,
    #1184); a custom ``--levels`` model has none."""
    if isinstance(superseded, Mapping):
        inherited = superseded.get("since_last_run")
        if isinstance(inherited, Mapping) and inherited.get("available") is True:
            return bool(inherited.get("first_run")), None, None, inherited
    prior = load_prior_level(assess_dir)
    stored = load_baseline(assess_dir, level)  # the committed baseline first (#1122)
    if stored is not None:
        return False, None, {"run_id": stored.get("run_id"), "head_sha": stored["head_sha"],
                             "held_gates": stored["held_gates"]}, None
    if model_key(level) is not None:  # the default model or a shipped profile: committed record only (#1256)
        if prior is not None and as_prior_level(prior) is None:
            return False, "baseline_unreadable", None, None
        return True, None, None, None
    path = assess_dir / "run-context.json"
    if not path.is_file():
        return ((False, "previous_state_missing", None, None) if (assess_dir / "log.md").is_file()
                else (True, None, None, None))
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False, "previous_unreadable", None, None
    return (False, None, data, None) if isinstance(data, Mapping) else (False, "previous_unreadable", None, None)


def _done_removals(repo_root: Path) -> list[PruningEntry] | None:
    """The done removal actions in the committed ``actions.json`` whose commit is on HEAD's
    history; None (unknown) when ``actions.json`` is not committed or not readable, the clone is
    shallow, or git cannot answer, so the ledger is left as it was rather than read as emptied."""
    entries, _ = _committed_actions(repo_root)
    if entries is None or _shallow(repo_root):
        return None
    out: list[PruningEntry] = []
    for e in entries:
        found = removal(e)
        if found is None:
            continue
        on_head = _ancestor(repo_root, found["completed_sha"], "HEAD") if is_commit(repo_root, found["completed_sha"]) else 1
        if on_head not in (0, 1):
            return None
        if on_head == 0:
            out.append(found)
    return out


def pruning_record(ctx: Mapping[str, Any]) -> list[PruningEntry] | None:
    """The pruning ledger to store in ``prior-level.json`` beside the baseline (#1084): this run's,
    or, when the scan degraded, the stored one (``ctx["prior_level"]``), so credit is never lost
    and a removal never credited twice."""
    block = ctx.get("since_last_run")
    found = block.get("pruning") if isinstance(block, Mapping) else None
    if isinstance(found, list):
        return found
    prior = ctx.get("prior_level")
    kept = prior.get("pruning") if isinstance(prior, Mapping) else None
    return kept if isinstance(kept, list) else None


def since_last_run(level: Any, assess_dir: Path, repo_root: Path, superseded: Any = None,
                   instruction_placement: Any = None) -> SinceLastRun:
    """The ``since_last_run`` block for this run's ``level`` block (see the module docstring)."""
    steps = _steps(level)
    first, unknown, prev, inherited = _baseline(assess_dir, superseded, level)
    head = _git(repo_root, "rev-parse", "HEAD")
    head_sha = head.stdout.strip() if head is not None and head.returncode == 0 else None
    if first and inherited is None and model_key(level) is not None and (
            head_sha is None or _held(steps, level) is None or build_prior_level(level) is None):
        first, unknown = False, "baseline_not_recorded"  # "your baseline" only when one is written
    held_before: list[str] | None = None  # the gates held at the previous run, for the ledger
    if inherited is not None:
        previous_commit, run_id = inherited.get("previous_commit"), inherited.get("previous_run_id")
        gained, lost = inherited.get("gates_gained"), inherited.get("gates_lost")
    elif prev is not None and "held_gates" in prev:  # the committed baseline (#1122)
        previous_commit, run_id = prev.get("head_sha"), prev.get("run_id")
        now = _held(steps, level)
        then = [g for g in prev["held_gates"] if isinstance(g, str)]
        held_before = then
        gained, lost = ((None, None) if now is None
                        else ([g for g in now if g not in then], [g for g in then if g not in now]))
    else:
        commit = (prev or {}).get("measured_commit")
        previous_commit = commit.get("head_sha") if isinstance(commit, Mapping) else None
        run_id = (prev or {}).get("run_id")
        gained, lost = gate_changes(steps, level, (prev or {}).get("level")) if prev else (None, None)
        held_before = _held(steps, (prev or {}).get("level"))
    previous_commit = previous_commit if isinstance(previous_commit, str) and previous_commit else None
    closed: list[ClosedAction] | None = None
    unresolved: int | None = None
    reason: str | None = None
    if first:
        pass
    elif unknown is not None:
        reason = unknown
    elif previous_commit is None:
        reason = "no_previous_commit"
    elif not is_commit(repo_root, previous_commit):
        reason = "previous_commit_absent"  # a rewritten history or a shallow clone: not zero
    elif _shallow(repo_root):
        reason = "shallow_clone"  # ancestry stops at the boundary, so "not an ancestor" can lie
    elif _ancestor(repo_root, previous_commit, "HEAD") != 0:
        reason = "previous_not_ancestor"  # another branch's run: its commits are not this history's past
    elif isinstance(found := closed_actions(repo_root, previous_commit), str):
        reason = found
    else:
        closed, unresolved = found
    current = level if isinstance(level, Mapping) else {}
    # a hold counts once per measured commit: compare with the commit that wrote the stored
    # ledger (the committed baseline), not the "since" baseline a superseding run inherits
    stored = load_baseline(assess_dir, level)
    if default_level(level):
        ledger_commit = stored["head_sha"] if stored is not None else previous_commit
        pruning, pruned = update_ledger(load_pruning(assess_dir), _done_removals(repo_root), instruction_placement,
                                        held_now=_held(steps, level), held_before=held_before, head_sha=head_sha,
                                        new_commit=head_sha is not None and head_sha != ledger_commit,
                                        repo_root=repo_root)
    else:
        # the ledger is the default record's alone (#1184): a profile run neither advances nor credits it
        pruning, pruned = load_pruning(assess_dir), []
    block: SinceLastRun = {
        "available": True, "first_run": first,
        "previous_run_id": run_id if isinstance(run_id, str) else None,
        "previous_commit": previous_commit, "closed": closed, "closed_reason": reason,
        "closed_count": None if closed is None else len(closed), "unresolved": unresolved,
        "gates_gained": gained if isinstance(gained, list) else None,
        "gates_lost": lost if isinstance(lost, list) else None,
        "next_blocker": _next_blocker(current), "pruned": pruned, "pruning": pruning,
        "lines": [], "markdown": ""}
    # the line follows the write side's own rule: the file is kept exactly when it is not rebuilt
    kept = build_prior_level(level) is None and stored is not None
    block["lines"] = _words(block, stored.get("run_id") if kept and stored is not None else None)
    block["markdown"] = _markdown(block["lines"])
    return block
