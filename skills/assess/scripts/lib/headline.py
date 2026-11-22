"""The deterministic /assess headline: the Step, its next unlock and the verified tally (#747).

The headline is a pure function of the run's ``level`` block (``lib/level_tally.py``)
and the maturity model that produced it, so the same commit always prints the same
headline and the LLM never sets it. It retires the old layered "X / 8" score and
its band names. Three lines, in the order the scoring model fixes
(``skills/assess/references/scoring-model.md``):

1. the Step, phrased as the contracts in place: ``Step 3 · Unattended agent PRs ·
   contracts in place``;
2. the Next line, naming the next Step and the one change that reaches it:
   ``Next: Step 4 · Autonomous loops unlocks when a coverage gate is enforced``.
   Each blocker of the next Step is named, gates first, in the engine's order;
3. the tally: ``Verified practices: N of M measured · K not measured (reasons) ·
   model V``. N is ``tally.met``, M is ``tally.measured`` (measured conditions only),
   K is ``tally.not_measured`` with each reason code and its count, V the
   maturity-model version.

The per-tag strip is a later third line (#844); the model carries no tags yet.

``summary`` is the one-line form the badge and the ``log.md`` entry carry
(``Step 3 · 4 of 4 measured``). A run whose ``level`` block is missing or
unavailable gets ``available: false`` and a headline that says the Step was not
measured, never a guessed Step.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from typing import Any

from lib.level_result_types import Blocker, Headline, LevelResult
from lib.level_tally import default_model
from lib.levels import MaturityModel

HEADLINE_VERSION = 1

__all__ = ["Headline", "as_headline", "headline", "run_context_headline"]

# The change that holds each Step gate, as the Next line words it. A gate id the
# table does not know (a caller's own model) reads "the <id> gate holds".
GATE_CHANGE = {
    "instructions": "an instruction file an agent reads is committed",
    "ci_runs_tests": "CI runs the tests",
    "coverage_gate": "a coverage gate is enforced",
}

# Reason codes as the tally line words them; an unlisted code is shown with its
# underscores as spaces.
REASON_WORDS = {
    "needs_full_run": "needs a full run",
    "needs_platform": "needs GitHub or GitLab access",
    "block_absent": "scan did not run",
    "no_value": "no value read",
    "type_mismatch": "value of another type",
    "no_remote": "no remote",
}


def _step_names(model: MaturityModel) -> dict[int, str]:
    return {s["step"]: s["name"] for s in model.get("steps", [])}


def _reason_word(code: str) -> str:
    if code.startswith("requires:"):
        return f"waits on {code.split(':', 1)[1]}"
    return REASON_WORDS.get(code, code.replace("_", " "))


def _blocker_change(blocker: Blocker, level: LevelResult) -> str:
    if blocker["kind"] == "gate":
        gate = blocker.get("gate", "")
        change = GATE_CHANGE.get(gate, f"the {gate} gate holds")
        if blocker.get("state") == "not_measured":
            change += f" (not measured here: {_reason_word(blocker.get('reason', ''))})"
        return change
    short = blocker.get("threshold", 0) - blocker.get("met", 0)
    noun = "practice is" if short == 1 else "practices are"
    example = next((c["id"] for c in level["conditions"]
                    if c["counted"] and c["state"] == "unmet"), None)
    tail = f", for example `{example}`" if example else ""
    return f"{short} more verified {noun} met{tail}"


def _next_line(level: LevelResult, names: Mapping[int, str]) -> str:
    nxt = level["next_step"]
    if nxt is None:
        top = level["step"]
        return f"Next: Step {top} is the top Step; it holds while its gates hold"
    changes = [_blocker_change(b, level) for b in nxt["blockers"]]
    unlock = " and ".join(changes) if changes else "its gates hold"
    return f"Next: Step {nxt['step']} · {names.get(nxt['step'], '')} unlocks when {unlock}"


def _tally_line(level: LevelResult) -> str:
    tally = level["tally"]
    line = f"Verified practices: {tally['met']} of {tally['measured']} measured"
    reasons = Counter(entry["reason"] for entry in level["not_measured"])
    detail = ", ".join(f"{_reason_word(code)}: {n}" for code, n in sorted(reasons.items()))
    line += f" · {tally['not_measured']} not measured" + (f" ({detail})" if detail else "")
    return line + f" · model {level['levels_version']}"


def _unavailable(reason: str) -> Headline:
    line = "Step not measured: the level result is unavailable for this run"
    return {"headline_version": HEADLINE_VERSION, "available": False, "levels_version": None,
            "step": None, "next_step": None, "lines": [line], "markdown": f"**{line}**",
            "summary": "Step not measured", "reason": reason}


def headline(value: Any, model: MaturityModel) -> Headline:
    """The headline for one ``LevelResult`` under the model that produced it."""
    if not isinstance(value, dict) or "tally" not in value:
        reason = value.get("reason") if isinstance(value, dict) else None
        return _unavailable(str(reason or "no_level_result"))
    level: LevelResult = value  # type: ignore[assignment]
    names = _step_names(model)
    lines: list[str] = []
    step = level["step"]
    tally = level["tally"]
    if step is not None:
        lines.append(f"Step {step} · {names.get(step, '')} · contracts in place")
        lines.append(_next_line(level, names))
    lines.append(_tally_line(level))
    nxt = level["next_step"]
    counts = f"{tally['met']} of {tally['measured']} measured"
    summary = f"Step {step} · {counts}" if step is not None else counts
    markdown = "\n\n".join([f"**{lines[0]}**", *lines[1:]])
    return {"headline_version": HEADLINE_VERSION, "available": True,
            "levels_version": level["levels_version"], "step": step,
            "next_step": nxt["step"] if nxt else None, "lines": lines,
            "markdown": markdown, "summary": summary}


def run_context_headline(level: Any) -> Headline:
    """The run context's ``headline`` block: the default model over its ``level`` block."""
    return headline(level, default_model())


def as_headline(value: Any) -> Headline:
    """``value`` when it is a headline block, else the unavailable headline (a degraded scan)."""
    if isinstance(value, dict) and isinstance(value.get("summary"), str) and "lines" in value:
        return value  # type: ignore[return-value]
    reason = value.get("reason") if isinstance(value, dict) else None
    return _unavailable(str(reason or "no_headline"))
