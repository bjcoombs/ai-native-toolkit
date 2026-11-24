"""The top card that opens the /assess report: where the repository stands and what to do next, each fact once (#1252).

The card replaces the three summaries that used to open the report (the executive summary,
the Since Last Run list and the headline's Step and Next lines). Each fact on it comes from
one fact function in ``lib/card_facts.py``; this module only words them.
``card_lines(level, model, since, rollup=None, head_sha=None, action=None)`` returns, in order:

1. the lead line, bold in the markdown:

   - at the top Step: ``Holding Step 4 of 4 · 2 advisories, none blocks the Step.``, where
     the count is ``card_facts.advisories`` (unmet conditions no Step gate waits on). One
     reads ``1 advisory, and it blocks no Step.``; none reads ``Holding Step 4 of 4.``;
   - below it: ``Step 3 of 4: unattended agent PRs.``, the Step's name in the model, its first
     letter lowered unless the name opens with an acronym (``CI-gated merges`` stays);
   - below the first Step of a model whose Step 1 has a gate (#889): ``Not yet at Step 1 of 3.``;
   - a model without Steps: ``This maturity model defines no Steps.``;

2. ``Holding: 3 of 3 gates held at `600cca4` · 9 of 15 measured catalogue conditions met, 3 verified.``
   At the top Step the gates are the top Step's; below it, the next Step's
   (``2 of 3 Step 4 gates held``). The commit is left out when the run recorded none;
3. below the top Step only, the blocking gates, which lead every gate line:
   ``Blocks Step 4: no coverage gate is enforced.`` (``GATE_GAP``, plus ``headline.gate_note``
   when the gate was not measured or the owner excluded it). When the monorepo unit roll-up
   (``unit_rollup.learn_from``, #1155) names a unit that already holds the first blocking
   gate, the line adds ``` `skills/assess` already does this; copy it.```;
4. what moved since the last run, following the dark-cockpit rule: a run with no committed
   baseline reads ``since_last_run.FIRST_RUN`` ("This run is your baseline; ...") once;
   otherwise one ``Since last run: ...`` line naming only what is non-zero or changed (actions
   closed with their commits, gates now in place or no longer in place, each removal the
   pruning ledger credits, a model change),
   and what could not be read, with its reason. Nothing moved: no line. A missing or
   degraded block reads ``Since last run: not known.``;
5. ``Start here: ...`` and 6. ``Done when: ...``, exactly one each: the first open entry of
   ``.assess/actions.json`` by rank (``first_action``; its ``first_step``, in backticks unless
   it already holds some, else its ``action``, and its ``done_when``). With no open action:
   below the top Step, the first blocking gate as a task (``GATE_TASK``), linked to its
   catalog entry; at the top Step, the next run of ``/assess``. The core reads ``actions.json``
   as it stands when the run starts, the file the previous run's finalize step wrote (or the
   committed one), because this run's finalize writes its own Top 3 later. ``assess_finalize.py``
   then re-renders these two lines from the Top 3 it has just written (``lib/card_refresh.py``,
   which calls ``start_lines``), so the finished card names this run's first open action.
   The file read is ``<assess_dir>/actions.json`` only: a scoped run reads
   ``.assess/<slug>/actions.json`` and never the whole-repo ``.assess/actions.json``, whose
   actions belong to another scope; without its own file it falls back as above.

A profile or ``--levels`` run leads the card with ``profiles.model_lines`` (``lib/exec_summary.py``
adds them). ``card_markdown(lines)`` joins the lines into paragraphs with the lead line bold and
its first "Step" linked to the glossary (``headline.GLOSSARY_URL``, #1102), as are "measured
catalogue conditions" and "verified" on the Holding line; ``lines`` stay plain text.
The badge message and the ``log.md`` readiness line (``headline.summary``) read the same
``card_facts`` functions.
Tests: ``tests/test_top_card.py``.
"""
from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, TypedDict

from lib.card_facts import advisories, blocking_gates, step_gate_ids, step_name, top_step
from lib.headline import GATE_CHANGE, GLOSSARY_URL, gate_note, verified_count
from lib.levels import MaturityModel
from lib.model_change import model_changed_line
from lib.since_last_run import FIRST_RUN, UNKNOWN_CLOSED

__all__ = ["GATE_GAP", "GATE_TASK", "STEP_GATES_URL", "CardAction", "card_lines",
           "card_markdown", "first_action", "since_line", "read_first_action", "start_lines"]

# The catalog's Step gates entry: what each gate covers. measured_appendix.CATALOG_URL names the
# same page; importing it here would close a cycle through lib.signals.
STEP_GATES_URL = "https://github.com/bjcoombs/ai-native-toolkit/blob/main/docs/what-we-measure.md#step-gates"

# Each Step gate as a task a person can do. A gate id the table does not know reads
# "make the <id> gate hold".
GATE_TASK = {
    "instructions": "commit an instruction file agents read, such as AGENTS.md or CLAUDE.md",
    "ci_runs_tests": "make CI turn red when a check breaks",
    "coverage_gate": "enforce a coverage gate in CI",
}

# Each Step gate as the gap the card's Blocks line names. A gate id the table does not know
# reads "the <id> gate does not hold yet".
GATE_GAP = {
    "instructions": "no instruction file an agent reads is committed",
    "ci_runs_tests": "CI does not turn red when a check breaks",
    "coverage_gate": "no coverage gate is enforced",
}


# "Step" as a word, never inside "Steps" (the no-Steps lead)
_STEP_WORD = re.compile(r"\bStep\b")


class CardAction(TypedDict):
    """The rank-1 open action the card's Start here and Done when lines read."""

    start: str
    done_when: str


def _lower_first(text: str) -> str:
    """``text`` with its first letter lowered, unless it opens an acronym (``CI-gated merges``)."""
    return text if text[1:2].isupper() else text[:1].lower() + text[1:]


def _sentence(text: str) -> str:
    text = " ".join(text.split()).rstrip(".")
    return f"{text}." if text else text


def first_action(payload: Any) -> CardAction | None:
    """The first open entry of an ``actions.json`` payload by rank: its ``first_step`` (in
    backticks unless it already holds some) or ``action``, and its ``done_when``; None when
    no entry is open and complete."""
    entries = payload.get("actions") if isinstance(payload, Mapping) else None
    if not isinstance(entries, list):
        return None
    open_ = [e for e in entries if isinstance(e, Mapping) and e.get("status") != "done"
             and isinstance(e.get("done_when"), str) and e["done_when"].strip()
             and isinstance(e.get("rank"), int)]
    for entry in sorted(open_, key=lambda e: e["rank"]):
        step, action = entry.get("first_step"), entry.get("action")
        if isinstance(step, str) and step.strip():
            step = " ".join(step.split())
            return {"start": step if "`" in step else f"`{step}`", "done_when": entry["done_when"]}
        if isinstance(action, str) and action.strip():
            return {"start": " ".join(action.split()), "done_when": entry["done_when"]}
    return None


def read_first_action(assess_dir: Path | None) -> CardAction | None:
    """``first_action`` of ``<assess_dir>/actions.json``; None when it is absent or unreadable."""
    if assess_dir is None:
        return None
    try:
        payload = json.loads((assess_dir / "actions.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return first_action(payload)


def _gap(blocker: Mapping[str, Any]) -> str:
    gate = str(blocker.get("gate", ""))
    return GATE_GAP.get(gate, f"the {gate} gate does not hold yet") + gate_note(blocker)  # type: ignore[arg-type]


def _lead(level: Mapping[str, Any], model: MaturityModel) -> str:
    top = top_step(model)
    step, nxt = level.get("step"), level.get("next_step")
    if top is None:
        return "This maturity model defines no Steps."
    if step is None:
        target = nxt.get("step", 1) if isinstance(nxt, Mapping) else 1
        return f"Not yet at Step {target} of {top}."
    if nxt is None:
        k = advisories(level, model)
        tail = ("" if k == 0 else " · 1 advisory, and it blocks no Step" if k == 1
                else f" · {k} advisories, none blocks the Step")
        return f"Holding Step {step} of {top}{tail}."
    name = step_name(model, step)
    return f"Step {step} of {top}: {_lower_first(name)}." if name else f"Step {step} of {top}."


def _holding(level: Mapping[str, Any], model: MaturityModel, head_sha: str | None) -> str:
    nxt = level.get("next_step")
    target = nxt.get("step") if isinstance(nxt, Mapping) else level.get("step")
    gates = step_gate_ids(model, target) if isinstance(target, int) else []
    blocked = {b.get("gate") for b in blocking_gates(level)}
    held = sum(1 for g in gates if g not in blocked)
    which = "gates" if not isinstance(nxt, Mapping) else f"Step {target} gates"
    at = f" at `{head_sha[:7]}`" if head_sha else ""
    tally = level["tally"]
    return (f"Holding: {held} of {len(gates)} {which} held{at} · {tally['met']} of {tally['measured']} "
            f"measured catalogue conditions met, {verified_count(level['conditions'])} verified.")


def _sibling(gate: str, rollup: Any) -> str | None:
    entries = rollup.get("learn_from") if isinstance(rollup, Mapping) else None
    for entry in entries if isinstance(entries, list) else []:
        if not isinstance(entry, Mapping):
            continue
        names = entry.get("names")
        if entry.get("gate") == gate and isinstance(names, list) and names and isinstance(names[0], str):
            return names[0]
    return None


def _blocks(level: Mapping[str, Any], rollup: Any) -> str | None:
    gates = blocking_gates(level)
    nxt = level.get("next_step")
    if not gates or not isinstance(nxt, Mapping):
        return None
    line = f"Blocks Step {nxt.get('step')}: {'; '.join(_gap(b) for b in gates)}."
    sibling = _sibling(str(gates[0].get("gate", "")), rollup)
    return f"{line} `{sibling}` already does this; copy it." if sibling else line


def _gate_words(gates: Sequence[str]) -> str:
    return " and ".join(GATE_CHANGE.get(g, f"the {g} gate holds") for g in gates)


def since_line(since: Any) -> str | None:
    """The dark-cockpit since line: only what is non-zero, changed or unknown; None when nothing moved."""
    if not isinstance(since, Mapping) or since.get("available") is not True:
        return "Since last run: not known."
    if since.get("first_run") is True:
        return FIRST_RUN
    closed, gained, lost = since.get("closed_count"), since.get("gates_gained"), since.get("gates_lost")
    pruned = since.get("pruned")
    parts: list[str] = []
    if isinstance(closed, int) and closed:
        shas = [c.get("completed_sha") for c in since.get("closed") or [] if isinstance(c, Mapping)]
        named = ", ".join(f"`{sha[:7]}`" for sha in shas[:3] if isinstance(sha, str))
        parts.append(("1 action closed" if closed == 1 else f"{closed} actions closed")
                     + (f" ({named}{', ...' if len(shas) > 3 else ''})" if named else ""))
    if isinstance(gained, list) and gained:
        parts.append(f"now in place: {_gate_words(gained)}")
    if isinstance(lost, list) and lost:
        parts.append(f"no longer in place: {_gate_words(lost)}")
    for entry in pruned if isinstance(pruned, list) else []:
        if isinstance(entry, Mapping) and isinstance(entry.get("path"), str) and isinstance(entry.get("saved"), int):
            parts.append(f"pruned always-on text in `{entry['path']}`, saved {entry.get('saved')} always-on lines")
    changed = model_changed_line(since)
    if changed is not None and not isinstance(gained, list):
        parts.append(_lower_first(changed).replace(" since last run", ""))
    elif not isinstance(gained, list) or not isinstance(lost, list):
        parts.append("gate changes not known")
    if not isinstance(closed, int):
        reason = since.get("closed_reason")
        why = UNKNOWN_CLOSED.get(reason, "") if isinstance(reason, str) else ""
        parts.append(f"closed actions not known ({why})" if why else "closed actions not known")
    return f"Since last run: {'; '.join(parts)}." if parts else None


def start_lines(level: Mapping[str, Any], action: CardAction | None) -> tuple[str, str]:
    """The card's ``Start here:`` and ``Done when:`` lines: ``action``'s, else the first blocking
    gate as a task, else the next run of ``/assess``."""
    if action is not None:
        return f"Start here: {action['start']}", f"Done when: {_sentence(action['done_when'])}"
    gates = blocking_gates(level)
    if gates:
        gate = str(gates[0].get("gate", ""))
        task = GATE_TASK.get(gate, f"make the {gate} gate hold")
        return (f"Start here: [{task[:1].upper()}{task[1:]}]({STEP_GATES_URL}){gate_note(gates[0])}",
                f"Done when: the next `/assess` run reads that {GATE_CHANGE.get(gate, f'the {gate} gate holds')}.")
    return ("Start here: run `/assess` again after your next change",
            "Done when: that run still holds every gate.")


def card_lines(level: Any, model: MaturityModel, since: Any, rollup: Any = None,
               head_sha: str | None = None, action: CardAction | None = None) -> list[str]:
    """The card's lines in order (see the module docstring); ``[]`` when ``level`` is not a level result."""
    if not isinstance(level, Mapping) or "tally" not in level:
        return []
    lines = [_lead(level, model)]
    if top_step(model) is not None:
        lines.append(_holding(level, model, head_sha))
    blocks = _blocks(level, rollup)
    if blocks:
        lines.append(blocks)
    moved = since_line(since)
    if moved:
        lines.append(moved)
    lines.extend(start_lines(level, action))
    return lines


def card_markdown(lines: Sequence[str], lead_at: int = 0) -> str:
    """The lines as paragraphs: the lead line (index ``lead_at``) bold, its first "Step" linked
    to the glossary, and "measured catalogue conditions" and "verified" on the Holding line linked
    to the catalogue coverage and evidence grade entries."""
    out: list[str] = []
    for i, line in enumerate(lines):
        if i == lead_at:
            linked = _STEP_WORD.sub(f"[Step]({GLOSSARY_URL}#step)", line, count=1)
            line = f"**{linked}**"
        elif line.startswith("Holding: "):
            line = line.replace(" measured catalogue conditions met,",
                                f" [measured catalogue conditions]({GLOSSARY_URL}#catalogue-coverage) met,", 1)
            line = line.replace(" verified.", f" [verified]({GLOSSARY_URL}#evidence-grade).", 1)
        out.append(line)
    return "\n\n".join(out)

