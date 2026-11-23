"""The deterministic /assess headline: the Step, its next unlock, the tally and the per-tag strip (#747, #844).

The headline is a pure function of the run's ``level`` block (``lib/level_tally.py``)
and the maturity model that produced it, so the same commit always prints the same
headline and the LLM never sets it. It retires the old layered "X / 8" score and
its band names. The lines, in the order the scoring-model decisions on #740
and #844 fix:

1. the Step, phrased as the contracts in place: ``Step 3 · Unattended agent PRs ·
   contracts in place``;
2. the Next line, naming the next Step and the one change that reaches it:
   ``Next: Step 4 · Autonomous loops unlocks when a coverage gate is enforced``.
   The blocking gates come first. The count shortfall is worked out after they
   hold (a counted gate condition adds to the tally itself), so when the gates
   alone reach the threshold the line names only them; otherwise it adds "and N
   more practices are in place";
3. the tally: ``Practices in place: N of M measured, V verified · K not measured
   (reasons) · model <version>``. N is ``tally.met``, M is ``tally.measured`` (measured
   conditions only), V the share of N graded ``verified`` (CI-executed or
   platform-measured; the rest of N is graded ``measured``),
   K is ``tally.not_measured`` with each reason code and its count, <version> the
   maturity-model version. When the owner excluded conditions (#910), the line
   adds "E not applicable (excluded by owner)" before the model, where E counts
   every condition that left the denominator that way, the excluded ones and those
   whose ``requires`` names one. A blocking gate whose condition is excluded says
   so in the Next line: the Step stays out of reach;
4. the per-tag strip: ``By tag: behaviour 2 of 3 · docs 0 of 0 (NOT MEASURED: 1) ·
   governance 1 of 1 (HAZARD: 1)``, one cell per truth tag that any condition in the
   result carries, in ``TRUTH_TAGS`` order. A cell counts like the tally, over the
   conditions with that tag, so a condition with several tags is in each of their
   cells while N counts it once. NOT MEASURED, HAZARD (a hazard condition met: the
   harm is present) and DECLINED are badges with their counts, shown only when
   non-zero. A result with no tagged condition has no strip line.

``summary`` is the one-line form the badge and the ``log.md`` entry carry
(``Step 3 · 4 in place of 4 measured``). ``verified`` and ``tags`` (``TagCell`` per cell)
hold the same numbers for counts-only readers, which drop the text lines. A run whose ``level`` block is missing or
unavailable gets ``available: false`` and a headline that says the Step was not
measured, never a guessed Step.

The deterministic `/assess` headline (#747): `headline(level, model)` words a
`LevelResult` under the model that produced it, and `run_context_headline(level)`
(the `headline` scan, `core_scans.headline`, reading the `level` block) uses the
default model. Three `lines`: the Step as the contracts in place ("Step 3 ·
Unattended agent PRs · contracts in place"), the Next line naming the next Step and
the change that reaches it (`GATE_CHANGE` per blocking gate id, then, only when the
count still falls short once the gates hold and their counted conditions join the
tally, "N more practices are in place, for example `<first unmet counted
condition>`"; a not-measured gate says
so with its reason, and a gate whose condition the owner excluded says "excluded by
owner: <kind>; the Step waits while the exclusion stands"; at the top Step it says the
Step holds while its gates hold), "Practices in place: N of M measured, V verified ·
K not measured (reason: count, ...) · model <version>" (`REASON_WORDS` words the reason codes;
V counts the met, counted conditions graded `verified`), with "E not applicable
(excluded by owner)" before the model when the level lists exclusions, E counting the
excluded conditions and those whose `requires` names one (#910), then the per-tag strip
"By tag: <tag> N of M (NOT MEASURED: k, HAZARD: h, DECLINED: d) · ..." (#844; tags from
the model's `truth`, badges only when non-zero, no line when no condition is tagged).
Also `markdown` (the lines, the first bold, pasted verbatim by the report and
`assess_report.py`), `summary` (the badge message and the `log.md` readiness line,
"Step 3 · 4 in place of 4 measured"), `verified` and `tags` (`TagCell` per strip cell), `step`,
`next_step`, `levels_version`, `headline_version` and `next_why` (#939: for each blocking gate,
the `why` of every condition of its row up to its level that is not met, from the level
block's conditions, each once and joined by one space; `""` at the top Step or with no gate
blocker). A missing or degraded `level` block gives
`available: false` and "Step not measured", never a guessed Step; `as_headline`
returns that form for a degraded `headline` block. Shape: `Headline` in
`level_result_types.py`; under counts-only the text fields are dropped and the
numbers and tag tokens kept. Tests: `tests/test_headline.py`.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from typing import Any

from lib.level_result_types import Blocker, ConditionResult, Headline, LevelResult, TagCell
from lib.level_tally import default_model
from lib.levels import MaturityModel
from lib.levels_fields import TRUTH_TAGS

# Held at 1 during the version freeze (skills/assess/changes/README.md): #844 adds
# `verified` and `tags`, so the release PR moves it to 2; it has no changes/ counter.
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
    "unsupported_host": "the remote's host is not GitHub or a reachable GitLab",
    "block_absent": "scan did not run",
    "no_value": "no value read",
    "type_mismatch": "value of another type",
    "no_remote": "no remote",
    "no_ownership_map": "no ownership map",
    "scan_failed": "scan did not complete",
    "no_scored_files": "no files scored",
    "no_manifests": "no manifests read",
    "no_complexity_stats": "no complexity stats written",
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
    return f"{short} more {noun} in place{tail}"


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


def next_why(level: LevelResult, model: MaturityModel) -> str:
    """The whys behind the Next line (#939): for each blocking gate, the ``why`` of every
    condition of its row up to its level that is not met, in model order, each once, joined
    by one space. ``""`` at the top Step, with no gate blocker, or for conditions with no why."""
    nxt = level["next_step"]
    if nxt is None:
        return ""
    blocked = {b.get("gate") for b in nxt["blockers"] if b["kind"] == "gate"}
    step_def = next((s for s in model.get("steps", []) if s["step"] == nxt["step"]), None)
    waiting: list[str] = []
    for gate in (step_def["gates"] if step_def else []):
        if gate["id"] in blocked:
            waiting += [f"{gate['row']}.{n}" for n in range(1, gate["level"] + 1)]
    by_id = {c["id"]: c for c in level["conditions"]}
    whys = [by_id[cid].get("why", "") for cid in dict.fromkeys(waiting)
            if cid in by_id and by_id[cid]["state"] != "met"]
    return " ".join(dict.fromkeys(w for w in whys if w))


def verified_count(conditions: list[ConditionResult]) -> int:
    """Counted conditions met and graded ``verified``: the verified share of N."""
    return sum(1 for c in conditions if c["counted"] and c["state"] == "met" and c["grade"] == "verified")


def _tally_line(level: LevelResult) -> str:
    tally = level["tally"]
    line = (f"Practices in place: {tally['met']} of {tally['measured']} measured, "
            f"{verified_count(level['conditions'])} verified")
    reasons = Counter(entry["reason"] for entry in level["not_measured"])
    detail = ", ".join(f"{reason_word(code)}: {n}" for code, n in sorted(reasons.items()))
    line += f" · {tally['not_measured']} not measured" + (f" ({detail})" if detail else "")
    if level.get("excluded"):
        # only an exclusion makes a condition not_applicable, directly or through `requires`
        gone = sum(c["state"] == "not_applicable" for c in level["conditions"])
        line += f" · {gone} not applicable (excluded by owner)"
    return line + f" · model {level['levels_version']}"


def _truth(model: MaturityModel) -> dict[str, list[str]]:
    """Each condition id of the model and its truth tags."""
    return {f"{row['id']}.{lv['level']}": lv.get("truth", [])
            for row in model["rows"] for lv in row["levels"]}


def _tag_cells(level: LevelResult, model: MaturityModel) -> list[TagCell]:
    """One cell per truth tag a condition in the result carries, in ``TRUTH_TAGS`` order."""
    truth = _truth(model)
    cells: dict[str, TagCell] = {}
    for c in level["conditions"]:
        for tag in truth.get(c["id"], []):
            cell = cells.setdefault(tag, {"tag": tag, "met": 0, "measured": 0, "verified": 0,
                                          "not_measured": 0, "hazards": 0, "declined": 0})
            state = c["state"]
            if c["counted"] and state in ("met", "unmet"):
                cell["measured"] += 1
                if state == "met":
                    cell["met"] += 1
                    cell["verified"] += c["grade"] == "verified"
            cell["not_measured"] += state == "not_measured"
            cell["hazards"] += c.get("polarity") == "hazard" and state == "met"
            cell["declined"] += state == "declined"
    return [cells[t] for t in TRUTH_TAGS if t in cells]  # the model load admits no other tag


def _strip_line(cells: list[TagCell]) -> str:
    parts = []
    for cell in cells:
        counts = (("NOT MEASURED", cell["not_measured"]), ("HAZARD", cell["hazards"]),
                  ("DECLINED", cell["declined"]))
        badges = ", ".join(f"{word}: {n}" for word, n in counts if n)
        parts.append(f"{cell['tag']} {cell['met']} of {cell['measured']}" + (f" ({badges})" if badges else ""))
    return "By tag: " + " · ".join(parts)


def _unavailable(reason: str) -> Headline:
    line = "Step not measured: the level result is unavailable for this run"
    return {"headline_version": HEADLINE_VERSION, "available": False, "levels_version": None,
            "step": None, "next_step": None, "lines": [line], "markdown": f"**{line}**",
            "summary": "Step not measured", "verified": None, "tags": [], "next_why": "",
            "reason": reason}


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
    cells = _tag_cells(level, model)
    if cells:
        lines.append(_strip_line(cells))
    nxt = level["next_step"]
    counts = f"{tally['met']} in place of {tally['measured']} measured"
    summary = f"Step {step} · {counts}" if step is not None else counts
    markdown = "\n\n".join([f"**{lines[0]}**", *lines[1:]])
    return {"headline_version": HEADLINE_VERSION, "available": True,
            "levels_version": level["levels_version"], "step": step,
            "next_step": nxt["step"] if nxt else None, "lines": lines,
            "markdown": markdown, "summary": summary,
            "verified": verified_count(level["conditions"]), "tags": cells,
            "next_why": next_why(level, model)}


def run_context_headline(level: Any) -> Headline:
    """The run context's ``headline`` block: the default model over its ``level`` block."""
    return headline(level, default_model())


def as_headline(value: Any) -> Headline:
    """``value`` when it is a headline block, else the unavailable headline (a degraded scan)."""
    if isinstance(value, dict) and isinstance(value.get("summary"), str) and "lines" in value:
        if "next_why" in value:
            return value  # type: ignore[return-value]
        return {"next_why": "", **value}  # type: ignore[typeddict-item]  # a pre-#939 block has none
    reason = value.get("reason") if isinstance(value, dict) else None
    return _unavailable(str(reason or "no_headline"))
