"""The deterministic /assess headline: the Step, the gate that blocks the next one, catalogue coverage and the per-tag strip (#747, #844, #1083).

The headline is a pure function of the run's ``level`` block (``lib/level_tally.py``)
and the maturity model that produced it, so the same commit always prints the same
headline and the LLM never sets it. ``headline(level, model)`` words a ``LevelResult``
under the model that produced it; ``run_context_headline(level)`` (the ``headline`` scan,
``core_scans.headline``, reading the ``level`` block) uses the default model. It retires
the layered "X / 8" score and its band names. A Step reads its gates only (#1083), so the
lines that lead name gates, never a count. The lines, in order:

1. the Step, phrased as the contracts in place: ``Step 3 · Unattended agent PRs ·
   contracts in place``;
2. the Next line, naming the next Step and the blocking gates that reach it, in Step
   order: ``Next: Step 4 · Autonomous loops unlocks when a coverage gate is enforced``.
   It never names a count of practices and never picks a condition by model order. A
   not-measured gate says so with its reason, and a gate whose condition the owner
   excluded (#910) says "excluded by owner: <kind>; the Step waits while the exclusion
   stands". At the top Step it says the Step holds while its gates hold;
3. catalogue coverage: ``Catalogue coverage: N of M measured, V verified · K not measured
   (reasons) · model <version>``. N is ``tally.met``, M is ``tally.measured`` (measured
   conditions only), V the share of N graded ``verified``, K is ``tally.not_measured``
   with each reason code (``REASON_WORDS``) and its count, <version> the maturity-model
   version. When the owner excluded conditions, the line adds "E not applicable
   (excluded by owner)" before the model, E counting the excluded conditions and those
   whose ``requires`` names one. The line says how much of the catalogue this run could
   read; it is never a progress figure and never in the badge (#1083);
4. the per-tag strip: ``By tag: behaviour 2 of 3 · docs 0 of 0 (NOT MEASURED: 1) ·
   governance 1 of 1 (HAZARD: 1)``, one cell per truth tag that any condition in the
   result carries, in ``TRUTH_TAGS`` order, counted like coverage over the conditions
   with that tag. NOT MEASURED, HAZARD (a hazard condition met) and DECLINED are badges
   with their counts, shown only when non-zero. No line when no condition is tagged.

``summary`` is the one-line form the badge and the ``log.md`` entry carry, with no count
in it: ``Step 3 · next: coverage gate`` (``GATE_NOUN`` names the first blocking gate),
``Step 4 · all gates held`` at the top Step, and ``no Steps defined`` for a model without
``[[steps]]``. ``markdown`` is the lines, the first bold, pasted verbatim by the report and
``assess_report.py``. ``verified`` and ``tags`` (``TagCell`` per cell, from ``tag_cells``, which lives in
``lib/level_tags.py`` with the level's ``tags`` rows (#940) and which the fleet's
per-tag tally also reads, #1073) hold the coverage
numbers for counts-only readers, which drop the text lines. ``next_why`` (#939) is, for
each blocking gate, the ``why`` of every condition of its row up to its level that is not
met, each once and joined by one space (``""`` at the top Step). A run whose ``level``
block is missing or unavailable gets ``available: false`` and "Step not measured", never
a guessed Step; ``as_headline`` returns that form for a degraded ``headline`` block.
Shape: ``Headline`` in ``level_result_types.py``; under counts-only the text fields are
dropped and the numbers and tag tokens kept. Tests: ``tests/test_headline.py``.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from typing import Any

from lib.level_result_types import Blocker, ConditionResult, Headline, LevelResult, TagCell
from lib.level_tags import tag_cells
from lib.level_tally import default_model
from lib.levels import MaturityModel

# Bumped when the headline block's shape changes. It has no changes/ counter, so a
# release PR moves it by hand during a version freeze.
# 2 (3.0.0 release): the block gains `verified` and `tags` (#844).
# 3 (#1083): Steps read gates only. `summary` reads "Step N · next: <gate>" or "Step N · all
#   gates held", with no count; the Next line names gates only; the tally line is relabelled
#   "Catalogue coverage". No key is added or removed.
HEADLINE_VERSION = 3

__all__ = ["Headline", "as_headline", "headline", "run_context_headline", "tag_cells"]

# The change that holds each Step gate, as the Next line words it. A gate id the
# table does not know (a caller's own model) reads "the <id> gate holds".
GATE_CHANGE = {
    "instructions": "an instruction file an agent reads is committed",
    "ci_runs_tests": "CI runs the tests",
    "coverage_gate": "a coverage gate is enforced",
}

# Each Step gate as the one-line summary names it: the badge and the log.md entry.
# A gate id the table does not know reads as itself.
GATE_NOUN = {
    "instructions": "instruction file",
    "ci_runs_tests": "CI runs tests",
    "coverage_gate": "coverage gate",
}

# Reason codes as the coverage line words them; an unlisted code is shown with its
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


def _next_line(level: LevelResult, names: Mapping[int, str]) -> str:
    """The next Step and the blocking gates that reach it: gates only, never a count (#1083)."""
    nxt = level["next_step"]
    if nxt is None:
        top = level["step"]
        return f"Next: Step {top} is the top Step; it holds while its gates hold"
    changes = [_gate_change(b) for b in nxt["blockers"] if b["kind"] == "gate"]
    unlock = " and ".join(changes) if changes else "its gates hold"
    return f"Next: Step {nxt['step']} · {names.get(nxt['step'], '')} unlocks when {unlock}"


def _summary(level: LevelResult) -> str:
    """The badge message and the log.md readiness line: the Step and its next blocker, no count."""
    step, nxt = level["step"], level["next_step"]
    if step is None:
        return "no Steps defined"
    gate = next((b.get("gate", "") for b in nxt["blockers"] if b["kind"] == "gate"), None) if nxt else None
    if gate is None:
        return f"Step {step} · all gates held"
    return f"Step {step} · next: {GATE_NOUN.get(gate, gate)}"


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
    line = (f"Catalogue coverage: {tally['met']} of {tally['measured']} measured, "
            f"{verified_count(level['conditions'])} verified")
    reasons = Counter(entry["reason"] for entry in level["not_measured"])
    detail = ", ".join(f"{reason_word(code)}: {n}" for code, n in sorted(reasons.items()))
    line += f" · {tally['not_measured']} not measured" + (f" ({detail})" if detail else "")
    if level.get("excluded"):
        # only an exclusion makes a condition not_applicable, directly or through `requires`
        gone = sum(c["state"] == "not_applicable" for c in level["conditions"])
        line += f" · {gone} not applicable (excluded by owner)"
    return line + f" · model {level['levels_version']}"


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
    if step is not None:
        lines.append(f"Step {step} · {names.get(step, '')} · contracts in place")
        lines.append(_next_line(level, names))
    lines.append(_tally_line(level))
    cells = tag_cells(level, model)
    if cells:
        lines.append(_strip_line(cells))
    nxt = level["next_step"]
    summary = _summary(level)
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
