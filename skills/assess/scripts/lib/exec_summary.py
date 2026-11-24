"""The three-line executive summary and the one call to action that open the /assess report (#1102).

A newcomer reading only the top of the report should be able to say where the repository
stands, what that means and what to do next. ``summarised(block, level, since, model)``
fills three fields of the ``headline`` block (``lib/headline.py`` leaves them empty, because
it cannot import the Step sentences without an import cycle):

- ``summary_lines``, in order:

  1. ``Step N of M: <what an agent can do here>.`` The sentence is
     ``assess_diagrams_ladder.STEP_MEANING`` (the Step's model name for a Step the table
     does not know); M is the model's top Step. Below the first Step of a model whose Step 1
     has a gate (#889): ``Not yet at Step 1 of M.``. A Step the default model defines under the
     same name reads its ladder sentence; any other Step reads the model's own Step name.
  2. ``Next: <the blocking gates as a task>. Then you reach Step N+1: <what it allows>.``
     Each gate is worded by ``GATE_TASK`` (an imperative), plus ``headline.gate_note`` when
     it was not measured or the owner excluded it, the same note the Next line carries. At
     the top Step: ``Next: every gate holds; keep them in place to stay at Step N.``
  3. ``Since last run: ...`` from the ``since_last_run`` block: the actions closed, the
     gates now in place and those no longer in place (``headline.GATE_CHANGE`` words), each
     part "not known" when the block says so, ``Since last run: not known.`` when nothing is
     known or the block is absent or degraded, and ``First run.`` on a first run.

- ``call_to_action``: ``**Start here:** [<task>](<catalog Step gates entry>)`` for the first
  blocking gate, ``""`` at the top Step. No action carries a how-to link of its own, so the
  link is the gate's entry in the catalog (``STEP_GATES_URL``);
- ``summary_markdown``: the lines and the call to action, one paragraph each, which the
  deterministic report (``assess_report.py``) and the findings template paste verbatim at
  the very top. Its first word, "Step", links the glossary's Step entry
  (``headline.GLOSSARY_URL``, #1102), the one place a newcomer first meets the word;
  ``summary_lines`` stays plain text.

The lines name no layer, no condition id and no count of practices: the only "N of M" is the
Step's place on the ladder. A headline that is unavailable, or a model without Steps, keeps
the fields empty. Under counts-only the three fields are dropped. Tests:
``tests/test_exec_summary.py``.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast

from lib.assess_diagrams_ladder import meaning
from lib.headline import GATE_CHANGE, GLOSSARY_URL, gate_note
from lib.level_result_types import Blocker, Headline, LevelResult
from lib.levels import MaturityModel

__all__ = ["GATE_TASK", "STEP_GATES_URL", "call_to_action", "since_line", "summarised", "summary_lines"]

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


def _task(blocker: Blocker) -> str:
    gate = blocker.get("gate", "")
    return GATE_TASK.get(gate, f"make the {gate} gate hold") + gate_note(blocker)


def _gates(level: LevelResult) -> list[Blocker]:
    nxt = level["next_step"]
    return [b for b in nxt["blockers"] if b["kind"] == "gate"] if nxt else []


def _plain(step: int, model: MaturityModel) -> str:
    """What an agent can do at ``step``: the ladder's sentence for a Step the default model
    defines under the same name, else the model's own Step name (#889), else the sentence."""
    from lib.level_tally import default_model  # deferred: level_tally reaches this module
    name = next((s["name"] for s in model.get("steps", []) if s["step"] == step), "")
    standard = next((s["name"] for s in default_model().get("steps", []) if s["step"] == step), None)
    return (meaning(step) if name == standard else name) or meaning(step) or name


def _gate_words(gates: list[str]) -> str:
    return " and ".join(GATE_CHANGE.get(g, f"the {g} gate holds") for g in gates)


def since_line(since: Any) -> str:
    """The third line, read from the ``since_last_run`` block (never computed again here)."""
    if not isinstance(since, Mapping) or since.get("available") is not True:
        return "Since last run: not known."
    if since.get("first_run") is True:
        return "First run."
    closed = since.get("closed_count")
    gained, lost = since.get("gates_gained"), since.get("gates_lost")
    parts: list[str] = []
    if isinstance(closed, int):
        parts.append("no actions closed" if closed == 0 else
                     "1 action closed" if closed == 1 else f"{closed} actions closed")
    if isinstance(gained, list) and isinstance(lost, list):
        if gained:
            parts.append(f"now in place: {_gate_words(gained)}")
        if lost:
            parts.append(f"no longer in place: {_gate_words(lost)}")
        if not gained and not lost:
            parts.append("no gate changes")
    if not parts:
        return "Since last run: not known."
    if not isinstance(closed, int):
        parts.append("closed actions not known")
    elif not (isinstance(gained, list) and isinstance(lost, list)):
        parts.append("gate changes not known")
    return f"Since last run: {'; '.join(parts)}."


def summary_lines(level: LevelResult, since: Any, model: MaturityModel) -> list[str]:
    """The three summary lines; ``[]`` for a model without Steps."""
    step, nxt = level["step"], level["next_step"]
    if step is None:
        if nxt is None or not model.get("steps"):
            return []
        # below the first Step of a model whose Step 1 has a gate (#889)
        top = max(s["step"] for s in model.get("steps", []))
        task = " and ".join(_task(b) for b in _gates(level)) or "make its gates hold"
        return [f"Not yet at Step {nxt['step']} of {top}.",
                f"Next: {task}. Then you reach Step {nxt['step']}: {_plain(nxt['step'], model)}.",
                since_line(since)]
    top = max(s["step"] for s in model.get("steps", []))
    lines = [f"Step {step} of {top}: {_plain(step, model)}."]
    nxt = level["next_step"]
    gates = _gates(level)
    if nxt is None:
        lines.append(f"Next: every gate holds; keep them in place to stay at Step {step}.")
    else:
        task = " and ".join(_task(b) for b in gates) or "keep the current gates in place"
        lines.append(f"Next: {task}. Then you reach Step {nxt['step']}: {_plain(nxt['step'], model)}.")
    lines.append(since_line(since))
    return lines


def call_to_action(level: LevelResult) -> str:
    """The one call to action: the first blocking gate as a task, linked; ``""`` when none."""
    gates = _gates(level)
    if not gates:  # also below Step 1, where the first gated Step's gate is the action (#889)
        return ""
    gate = gates[0].get("gate", "")
    task = GATE_TASK.get(gate, f"make the {gate} gate hold")
    return f"**Start here:** [{task[:1].upper()}{task[1:]}]({STEP_GATES_URL}){gate_note(gates[0])}."


def summarised(block: Headline, level: Any, since: Any, model: MaturityModel) -> Headline:
    """``block`` with its summary fields filled from ``level`` and ``since``; unchanged when the
    headline is unavailable or ``level`` is not a level result."""
    if block.get("available") is not True or not isinstance(level, dict) or "tally" not in level:
        return block
    result = cast(LevelResult, level)  # narrowed by the tally check above
    lines = summary_lines(result, since, model)
    action = call_to_action(result)
    first = lines[:1] and [lines[0].replace("Step", f"[Step]({GLOSSARY_URL}#step)", 1)]
    markdown = "\n\n".join([*first, *lines[1:], *([action] if action else [])])
    return {**block, "summary_lines": lines, "call_to_action": action, "summary_markdown": markdown}
