"""The deterministic /assess headline: catalogue coverage, the per-tag strip and the badge summary (#747, #844, #1083, #1252).

The headline is a pure function of the run's ``level`` block (``lib/scoring/level_tally.py``)
and the maturity model that produced it, so the same commit always prints the same
headline and the LLM never sets it. ``headline(level, model)`` words a ``LevelResult``
under the model that produced it; ``run_context_headline(level)`` (the ``headline`` scan,
``core_scans.headline``, reading the ``level`` block) uses the default model. It retires
the layered "X / 8" score and its band names. A Step reads its gates only (#1083). The
Step itself and the gate that blocks the next one are stated once, on the report's top card
(``lib/top_card.py``, #1252), which reads ``card_facts``; ``gate_change`` stays public so the
Step ladder's caption words gates as the headline did. The lines, in order:

1. catalogue coverage: ``Catalogue coverage: K not measured (reasons) · model <version>``.
   K is ``tally.not_measured`` with each reason code (``REASON_WORDS``) and its count,
   <version> the maturity-model version. The met, measured and verified counts are the top
   card's Holding line (#1252), stated there once. When the owner excluded conditions, the line adds "E not applicable
   (excluded by owner)" before the model, E counting the excluded conditions and those
   whose ``requires`` names one. The line says how much of the catalogue this run could
   read; it is never a progress figure and never in the badge (#1083);
2. the per-tag strip: ``By tag: behaviour 2 of 3 · docs 0 of 0 (NOT MEASURED: 1) ·
   governance 1 of 1 (HAZARD: 1)``, one cell per truth tag that any condition in the
   result carries, in ``TRUTH_TAGS`` order, counted like coverage over the conditions
   with that tag. NOT MEASURED, HAZARD (a hazard condition met) and DECLINED are badges
   with their counts, shown only when non-zero. No line when no condition is tagged.

``summary`` is the one-line form the badge and the ``log.md`` entry carry. It leads with what
holds (#1334): the Step, then the practices fully in place (``practices_held``, the level
result's ``in_place`` rows at their top level, left out at 0), then the next gate:
``Step 1 · 2 practices fully in place · next: instruction file``. No tally count is in it.
Without a practice at its top level it reads ``Step 3 · next: coverage gate`` (``GATE_NOUN`` names the first gate
``card_facts.blocking_gates`` returns, the function the top card reads),
``Step 1 · one change from Step 3: instruction file`` when one gate blocks the next Step and
the gates above it already hold (``one_change_ceiling``, reading the level result's
``ceiling_step``, #1333). ``lone_condition`` is the one test of "one change": exactly one
condition of the next Step's gates open and unmet on its own criteria. The top card, the Step
ladder and the fleet's one-change-away count (``lib/fleet/fleet_step_lifts.py``) read it too.
The summary reads ``Step 4 · all gates held`` at the top Step, ``no Step yet · next: <gate>`` below the first
Step of a model whose Step 1 has a gate (#889), and ``no Steps defined`` for a model without
``[[steps]]``. ``markdown`` is the lines, pasted verbatim by the report and
``assess_report.py``. ``verified`` and ``tags`` (``TagCell`` per cell, from ``tag_cells``, which lives in
``lib/scoring/level_tags.py`` with the level's ``tags`` rows (#940) and which the fleet's
per-tag tally also reads, #1073) hold the coverage
numbers for counts-only readers, which drop the text lines. ``next_why`` (#939) is, for
each blocking gate, the ``why`` of every condition of its row up to its level that is not
met, each once and joined by one space (``""`` at the top Step).
``summary_lines``, ``call_to_action`` and ``summary_markdown`` (#1102) hold the top card
that opens the report (``lib/top_card.py``, #1252); this module leaves them empty and
``lib/exec_summary.py`` fills them. The card reads the ``since_last_run`` block and
``.assess/actions.json``, so, unlike the rest of the block, it can differ between two runs
of one commit.
A run whose ``level``
block is missing or unavailable gets ``available: false`` and "Step not measured", never
a guessed Step; ``as_headline`` returns that form for a degraded ``headline`` block.
Shape: ``Headline`` in ``level_result_types.py``; under counts-only the text fields are
dropped and the numbers and tag tokens kept. Tests: ``tests/test_headline.py``.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any

from lib.card_facts import blocking_gates, fully_in_place
from lib.scoring.level_result_types import Blocker, ConditionResult, Headline, LevelResult, TagCell
from lib.scoring.level_tags import tag_cells
from lib.scoring.level_tally import NOT_APPLICABLE_REASONS, default_model, scan_not_applicable
from lib.scoring.levels import MaturityModel, StepDef, condition_id

# Bumped when the headline block's shape changes. It has no changes/ counter, so a
# release PR moves it by hand during a version freeze.
# 2 (3.0.0 release): the block gains `verified` and `tags` (#844).
# 3 (#1083): Steps read gates only. `summary` reads "Step N · next: <gate>" or "Step N · all
#   gates held", with no count; the Next line names gates only; the tally line is relabelled
#   "Catalogue coverage". No key is added or removed.
# 4 (4.0.0 release): the block gains `summary_lines`, `call_to_action` and
#   `summary_markdown`, the report's three-line summary and its one call to action (#1102).
HEADLINE_VERSION = 4

__all__ = ["GATE_CHANGE", "GATE_NOUN", "GLOSSARY_URL", "Headline", "as_headline", "gate_change", "gate_note", "headline",
           "lone_condition", "one_change_ceiling", "practices_held", "reason_word", "run_context_headline", "tag_cells", "verified_count"]

# The plain-English glossary (#1102). The report's markdown links a term's first use to its
# heading anchor; exec_summary.py and assess_report.py read it from here.
GLOSSARY_URL = "https://github.com/bjcoombs/ai-native-toolkit/blob/main/docs/glossary.md"

# The change that holds each Step gate, as the Next line words it. A gate id the
# table does not know (a caller's own model) reads "the <id> gate holds".
GATE_CHANGE = {
    "instructions": "an instruction file an agent reads is committed",
    "ci_runs_tests": "CI turns red on a broken check",
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
    "fewer_than_two_systems": "fewer than two agent systems",
    "no_configured_gates": "no configured gate",
    "no_complexity_stats": "no complexity stats written",
}
# Words an unlisted code is never shown with (lib/vocabulary.py, #743), and what reads instead.
_SUPPORTIVE = (("failed", "did not complete"), ("missing", "not found"))


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


def gate_note(blocker: Blocker) -> str:
    """Why a blocking gate is open when it was not measured or the owner excluded it, as
    `` (...)``; ``""`` for a gate that is simply not yet in place."""
    if blocker.get("disposition") == "excluded_by_owner":
        kind = blocker.get("exclusion_kind", "other").replace("_", " ")
        return f" (excluded by owner: {kind}; the Step waits while the exclusion stands)"
    if blocker.get("state") == "not_measured":
        return f" (not measured here: {reason_word(blocker.get('reason', ''))})"
    if blocker.get("state") == "not_applicable" and blocker.get("reason") in NOT_APPLICABLE_REASONS:
        # a scan's own code only: a requires:<id> blocker may trace to the owner's exclusion
        return f" (not applicable here: {reason_word(blocker.get('reason', ''))}; a gate holds only when met)"
    return ""


def gate_change(blocker: Blocker) -> str:
    """A blocking gate as the Next line words it: its ``GATE_CHANGE`` change, plus ``gate_note``.
    The Step ladder's caption and the executive summary (``lib/exec_summary.py``) read it too."""
    gate = blocker.get("gate", "")
    return GATE_CHANGE.get(gate, f"the {gate} gate holds") + gate_note(blocker)


def _summary(level: LevelResult, model: MaturityModel) -> str:
    """The badge message and the log.md readiness line: the Step and its next blocker, no count.
    It reads ``card_facts.blocking_gates``, the function the report's top card reads (#1252)."""
    step, nxt = level["step"], level["next_step"]
    if step is None and nxt is None:
        return "no Steps defined"
    gates = blocking_gates(level)
    gate = gates[0].get("gate", "") if gates else None
    held = practices_held(level)
    if step is None:  # below Step 1 of a model whose Step 1 has a gate (#889)
        return f"no Step yet{held} · next: {GATE_NOUN.get(gate, gate)}" if gate else f"no Step yet{held}"
    if gate is None:
        return f"Step {step}{held} · all gates held"
    ceiling = one_change_ceiling(level, model.get("steps", []))
    if ceiling is not None:
        return f"Step {step}{held} · one change from Step {ceiling}: {GATE_NOUN.get(gate, gate)}"
    return f"Step {step}{held} · next: {GATE_NOUN.get(gate, gate)}"


def practices_held(level: Mapping[str, Any]) -> str:
    """`` · N practices fully in place`` for the summary (#1334): the practices at their top
    level (``card_facts.fully_in_place``); ``""`` when none is, so a summary never leads with 0."""
    n = len(fully_in_place(level))
    return "" if n == 0 else f" · {n} {'practice' if n == 1 else 'practices'} fully in place"


def lone_condition(level: Mapping[str, Any], steps: Sequence[StepDef]) -> tuple[str, str] | None:
    """``(condition, gate)`` when meeting that one condition holds every gate of the next Step
    (#941, #1333): exactly one condition its gates read (each gate's row from level 1 up to the
    gate's level; a condition two gates share is one change) is not met, and it is ``unmet`` on
    its own criteria, never ``not_measured``, ``not_applicable`` or unmet only through a
    ``requires`` (reason ``requires:<id>``), since meeting that alone changes nothing. ``gate`` is
    the first gate of the next Step that reads it. None otherwise."""
    nxt, conditions = level.get("next_step"), level.get("conditions")
    if level.get("step") is None or not isinstance(nxt, Mapping) or not isinstance(conditions, list):
        return None
    definition = next((s for s in steps if s["step"] == nxt.get("step")), None)
    if definition is None:
        return None
    by_id = {c["id"]: c for c in conditions if isinstance(c, Mapping)}
    missing: dict[str, str] = {}
    for gate in definition["gates"]:
        for n in range(1, gate["level"] + 1):
            cid = condition_id(gate["row"], n)
            if cid in by_id and by_id[cid]["state"] != "met":
                missing.setdefault(cid, gate["id"])
    if len(missing) != 1:
        return None
    ((cid, gate_id),) = missing.items()
    cond = by_id[cid]
    if cond["state"] != "unmet" or str(cond.get("reason", "")).startswith("requires:"):
        return None
    return cid, gate_id


def one_change_ceiling(level: Mapping[str, Any], steps: Sequence[StepDef]) -> int | None:
    """The Step one change takes the repository to when that is past its next Step (#1333):
    ``ceiling_step`` when ``lone_condition`` finds the one change and ``ceiling_step`` is above
    the next Step; None otherwise, and for a result stored before #1374 without ``ceiling_step``.
    The fleet's one-change-away count (``lib/fleet/fleet_step_lifts.py``) reads the same test."""
    nxt, ceiling = level.get("next_step"), level.get("ceiling_step")
    if not isinstance(nxt, Mapping) or not isinstance(ceiling, int) or ceiling <= nxt.get("step", ceiling):
        return None
    return ceiling if lone_condition(level, steps) is not None else None


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
    # the met, measured and verified counts are the top card's Holding line (#1252)
    tally = level["tally"]
    reasons = Counter(entry["reason"] for entry in level["not_measured"])
    detail = ", ".join(f"{reason_word(code)}: {n}" for code, n in sorted(reasons.items()))
    line = f"Catalogue coverage: {tally['not_measured']} not measured" + (f" ({detail})" if detail else "")
    # not_applicable by a scan's reason (level_tally.NOT_APPLICABLE_REASONS, #1407), apart from
    # the owner's exclusions and the conditions that require one
    by_id = {c["id"]: c for c in level["conditions"]}
    apart = [code for c in level["conditions"] if (code := scan_not_applicable(c, by_id)) is not None]
    if apart:
        words = ", ".join(f"{reason_word(code)}: {n}" for code, n in sorted(Counter(apart).items()))
        line += f" · {len(apart)} not applicable here ({words})"
    if level.get("excluded"):
        gone = sum(c["state"] == "not_applicable" for c in level["conditions"]) - len(apart)
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
            "summary_lines": [], "call_to_action": "", "summary_markdown": "", "reason": reason}


def headline(value: Any, model: MaturityModel) -> Headline:
    """The headline for one ``LevelResult`` under the model that produced it."""
    if not isinstance(value, dict) or "tally" not in value:
        reason = value.get("reason") if isinstance(value, dict) else None
        return _unavailable(str(reason or "no_level_result"))
    level: LevelResult = value  # type: ignore[assignment]
    step = level["step"]
    # the Step and the gate that blocks the next one are the top card's (lib/top_card.py, #1252)
    lines = [_tally_line(level)]
    cells = tag_cells(level, model)
    if cells:
        lines.append(_strip_line(cells))
    nxt = level["next_step"]
    summary = _summary(level, model)
    markdown = "\n\n".join(lines)
    return {"headline_version": HEADLINE_VERSION, "available": True,
            "levels_version": level["levels_version"], "step": step,
            "next_step": nxt["step"] if nxt else None, "lines": lines,
            "markdown": markdown, "summary": summary,
            "verified": verified_count(level["conditions"]), "tags": cells,
            "next_why": next_why(level, model),
            # the top card (#1252), empty until exec_summary.summarised fills it
            "summary_lines": [], "call_to_action": "", "summary_markdown": ""}


def run_context_headline(level: Any) -> Headline:
    """The run context's ``headline`` block: the default model over its ``level`` block."""
    return headline(level, default_model())


def as_headline(value: Any) -> Headline:
    """``value`` when it is a headline block, else the unavailable headline (a degraded scan)."""
    if isinstance(value, dict) and isinstance(value.get("summary"), str) and "lines" in value:
        if "next_why" in value and "summary_markdown" in value:
            return value  # type: ignore[return-value]
        # a pre-#939 block has no next_why, a pre-#1102 block no summary fields
        return {"next_why": "", "summary_lines": [], "call_to_action": "", "summary_markdown": "",
                **value}  # type: ignore[typeddict-item]
    reason = value.get("reason") if isinstance(value, dict) else None
    return _unavailable(str(reason or "no_headline"))
