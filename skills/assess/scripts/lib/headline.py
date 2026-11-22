"""The deterministic /assess headline: the Step, its next unlock and the verified tally (#747).

The headline is a pure function of the run's ``level`` block (``lib/level_tally.py``)
and the maturity model that produced it, so the same commit always prints the same
headline and the LLM never sets it. It retires the old layered "X / 8" score and
its band names. Three lines, in the order the scoring-model decisions on #740
fix:

1. the Step, phrased as the contracts in place: ``Step 3 · Unattended agent PRs ·
   contracts in place``;
2. the Next line, naming the next Step and the one change that reaches it:
   ``Next: Step 4 · Autonomous loops unlocks when a coverage gate is enforced``.
   The blocking gates come first. The count shortfall is worked out after they
   hold (a counted gate condition adds to the tally itself), so when the gates
   alone reach the threshold the line names only them; otherwise it adds "and N
   more verified practices are met";
3. the tally: ``Verified practices: N of M measured · K not measured (reasons) ·
   model V``. N is ``tally.met``, M is ``tally.measured`` (measured conditions only),
   K is ``tally.not_measured`` with each reason code and its count, V the
   maturity-model version. When the owner excluded conditions (#910), the line
   adds "E not applicable (excluded by owner)" before the model, where E counts
   every condition that left the denominator that way, the excluded ones and those
   whose ``requires`` names one. A blocking gate whose condition is excluded says
   so in the Next line: the Step stays out of reach.

The per-tag strip is a later third line (#844); the model carries no tags yet.

``summary`` is the one-line form the badge and the ``log.md`` entry carry
(``Step 3 · 4 of 4 measured``). A run whose ``level`` block is missing or
unavailable gets ``available: false`` and a headline that says the Step was not
measured, never a guessed Step.

The deterministic `/assess` headline (#747): `headline(level, model)` words a
`LevelResult` under the model that produced it, and `run_context_headline(level)`
(the `headline` scan, `core_scans.headline`, reading the `level` block) uses the
default model. Three `lines`: the Step as the contracts in place ("Step 3 ·
Unattended agent PRs · contracts in place"), the Next line naming the next Step and
the change that reaches it (`GATE_CHANGE` per blocking gate id, then, only when the
count still falls short once the gates hold and their counted conditions join the
tally, "N more verified practices are met, for example `<first unmet counted
condition>`"; a not-measured gate says
so with its reason, and a gate whose condition the owner excluded says "excluded by
owner: <kind>; the Step waits while the exclusion stands"; at the top Step it says the
Step holds while its gates hold), and "Verified practices: N of M measured · K not
measured (reason: count, ...) · model V" (`REASON_WORDS` words the reason codes), with
"E not applicable (excluded by owner)" before the model when the level lists exclusions,
E counting the excluded conditions and those whose `requires` names one (#910). Also `markdown` (the lines, the
first bold, pasted verbatim by the report and `assess_report.py`), `summary` (the
badge message and the `log.md` readiness line), `step`, `next_step`,
`levels_version` and `headline_version`. A missing or degraded `level` block gives
`available: false` and "Step not measured", never a guessed Step; `as_headline`
returns that form for a degraded `headline` block. Shape: `Headline` in
`level_result_types.py`; under counts-only the text fields are dropped and the
numbers kept. The per-tag strip is a later line (#844). Tests: `tests/test_headline.py`.
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
    "no_ownership_map": "no ownership map",
    "scan_failed": "scan did not complete",
    "no_scored_files": "no files scored",
    "no_manifests": "no manifests read",
}
# Words an unlisted code is never shown with (lib/vocabulary.py, #743), and what reads instead.
_SUPPORTIVE = (("failed", "did not complete"), ("missing", "not found"))


def _step_names(model: MaturityModel) -> dict[int, str]:
    return {s["step"]: s["name"] for s in model.get("steps", [])}


def reason_word(code: str) -> str:
    """A not-measured reason code in supportive words (the headline and the /assess diagrams)."""
    if code.startswith("requires:"):
        return f"waits on {code.split(':', 1)[1]}"
    if code in REASON_WORDS:
        return REASON_WORDS[code]
    words = f" {code.replace('_', ' ')} "
    for word, instead in _SUPPORTIVE:
        words = words.replace(f" {word} ", f" {instead} ")
    return words.strip()


def _gate_change(blocker: Blocker) -> str:
    gate = blocker.get("gate", "")
    change = GATE_CHANGE.get(gate, f"the {gate} gate holds")
    if blocker.get("disposition") == "excluded_by_owner":
        kind = blocker.get("exclusion_kind", "other").replace("_", " ")
        change += f" (excluded by owner: {kind}; the Step waits while the exclusion stands)"
    elif blocker.get("state") == "not_measured":
        change += f" (not measured here: {reason_word(blocker.get('reason', ''))})"
    return change


def _gate_gain(level: LevelResult, model: MaturityModel, step: int,
               gates: list[Blocker]) -> tuple[int, set[str]]:
    """The tally the blocking gates add once they hold, and the conditions they cover.

    A gate holds when its row's conditions up to its level are all met; each of
    those not met yet that is counted adds one to ``tally.met`` when the gate holds.
    """
    blocked = {b.get("gate") for b in gates}
    step_def = next((s for s in model.get("steps", []) if s["step"] == step), None)
    covered: set[str] = set()
    for gate in (step_def["gates"] if step_def else []):
        if gate["id"] in blocked:
            covered |= {f"{gate['row']}.{n}" for n in range(1, gate["level"] + 1)}
    gain = sum(1 for c in level["conditions"]
               if c["id"] in covered and c["counted"] and c["state"] != "met")
    return gain, covered


def _threshold_change(short: int, level: LevelResult, covered: set[str]) -> str:
    noun = "practice is" if short == 1 else "practices are"
    example = next((c["id"] for c in level["conditions"]
                    if c["counted"] and c["state"] == "unmet" and c["id"] not in covered), None)
    tail = f", for example `{example}`" if example else ""
    return f"{short} more verified {noun} met{tail}"


def _next_line(level: LevelResult, model: MaturityModel, names: Mapping[int, str]) -> str:
    """The next Step and the change that reaches it.

    The gates come first. The count shortfall is worked out after the gates hold,
    because a counted gate condition adds to the tally itself: when the gates alone
    reach the threshold, the line names only them.
    """
    nxt = level["next_step"]
    if nxt is None:
        top = level["step"]
        return f"Next: Step {top} is the top Step; it holds while its gates hold"
    gates = [b for b in nxt["blockers"] if b["kind"] == "gate"]
    gain, covered = _gate_gain(level, model, nxt["step"], gates)
    changes = [_gate_change(b) for b in gates]
    for b in nxt["blockers"]:
        if b["kind"] == "threshold":
            short = b.get("threshold", 0) - b.get("met", 0) - gain
            if short > 0:
                changes.append(_threshold_change(short, level, covered))
    unlock = " and ".join(changes) if changes else "its gates hold"
    return f"Next: Step {nxt['step']} · {names.get(nxt['step'], '')} unlocks when {unlock}"


def _tally_line(level: LevelResult) -> str:
    tally = level["tally"]
    line = f"Verified practices: {tally['met']} of {tally['measured']} measured"
    reasons = Counter(entry["reason"] for entry in level["not_measured"])
    detail = ", ".join(f"{reason_word(code)}: {n}" for code, n in sorted(reasons.items()))
    line += f" · {tally['not_measured']} not measured" + (f" ({detail})" if detail else "")
    if level.get("excluded"):
        # only an exclusion makes a condition not_applicable, directly or through `requires`
        gone = sum(c["state"] == "not_applicable" for c in level["conditions"])
        line += f" · {gone} not applicable (excluded by owner)"
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
        lines.append(_next_line(level, model, names))
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
