"""Contract for the layer scorer's structured evidence (issue #361).

The scorer returns an ``evidence`` list beside its prose; the orchestrator
re-checks it with ``lib.evidence_check`` between scoring and report writing, and
the findings step cites only verified entries. These tests pin the three halves:

1. The scorer definition states the evidence schema with one example per kind,
   and every example is true of this repository (the deterministic half of a
   dry run on this repo: an ``evidence`` list and an empty ``evidence_rejected``).
2. ``skills/assess/SKILL.md`` runs the check after scoring and before finalize.
3. ``skills/assess-findings/SKILL.md`` restricts existence and wiring claims to
   verified entries.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from lib.evidence_check import KINDS, check_evidence

REPO_ROOT = Path(__file__).resolve().parents[3]
SCORER = REPO_ROOT / "agents" / "assess-layer-scorer.md"
ASSESS_SKILL = REPO_ROOT / "skills" / "assess" / "SKILL.md"
FINDINGS_SKILL = REPO_ROOT / "skills" / "assess-findings" / "SKILL.md"


def _schema_examples() -> list:
    text = SCORER.read_text(encoding="utf-8")
    headings = re.findall(r"^#{2,4} Evidence schema$", text, flags=re.M)
    assert len(headings) == 1, "scorer must carry exactly one Evidence schema heading"
    after = text[re.search(r"^#{2,4} Evidence schema$", text, flags=re.M).end():]
    block = re.search(r"^```json\n(.*?)^```$", after, flags=re.M | re.S)
    assert block, "no json block under the Evidence schema heading"
    return json.loads(block.group(1))


def test_scorer_return_section_names_evidence_list():
    text = SCORER.read_text(encoding="utf-8")
    # The return list only, not the Evidence schema subsection that follows it,
    # so deleting the bullet fails this test even though the schema prose stays.
    section = text.split("## What you return", 1)[1].split("### Evidence schema", 1)[0]
    bullets = [line for line in section.splitlines() if line.startswith("- ")]
    assert any("`evidence`" in line for line in bullets)


def test_scorer_schema_has_one_example_per_kind():
    examples = _schema_examples()
    assert sorted(e["kind"] for e in examples) == sorted(KINDS)
    assert all(isinstance(e.get("layer"), int) and 0 <= e["layer"] <= 8 for e in examples)


def test_scorer_schema_examples_verify_against_this_repo():
    result = check_evidence(REPO_ROOT, _schema_examples())
    assert result["evidence_rejected"] == []
    assert len(result["evidence"]) == len(KINDS)


def test_orchestrator_checks_evidence_between_scoring_and_finalize():
    text = ASSESS_SKILL.read_text(encoding="utf-8")
    window = text.split("## Step 3: Score the Layers\n", 1)[1].split("## Step 7.5", 1)[0]
    assert "evidence_check" in window
    assert "evidence_rejected" in window


def test_findings_cites_only_verified_evidence():
    assert "cite only verified" in FINDINGS_SKILL.read_text(encoding="utf-8").lower()


def test_orchestrator_runs_the_check_by_script_path():
    # An /assess run's cwd is the target repo, where `python -m lib.evidence_check`
    # fails with "No module named lib" and exits 1 - the same code as "an entry was
    # rejected". The call must name the script by path, like every sibling call.
    text = ASSESS_SKILL.read_text(encoding="utf-8")
    window = text.split("## Step 3: Score the Layers\n", 1)[1].split("## Step 7.5", 1)[0]
    assert '"${CLAUDE_SKILL_DIR}/scripts/lib/evidence_check.py"' in window
    assert "-m lib.evidence_check" not in window
    assert "<!-- chat-replace:evidence-check -->" in window


def _step4_check_paragraph() -> str:
    text = ASSESS_SKILL.read_text(encoding="utf-8")
    step4 = text.split("## Step 4: Write the Report\n", 1)[1].split("## Step 7.5", 1)[0]
    return next(p for p in step4.split("\n\n") if "evidence_check.py" in p)


def test_step4_names_the_check_by_the_substituted_skill_dir():
    # Step 2's shell variables do not survive to Step 4 (Step 3's subagent sits
    # in between), so the check must not lean on one. ${CLAUDE_SKILL_DIR} is
    # substituted into the skill text before the model reads it, so the call
    # carries an absolute path and needs no re-resolution in the shell.
    para = _step4_check_paragraph()
    assert '"${CLAUDE_SKILL_DIR}/scripts/lib/evidence_check.py"' in para
    assert "SKILL_DIR=" not in para
    assert "$SKILL_DIR" not in para
    assert "CLAUDE_PLUGIN_ROOT" not in para
    assert "as in Step 2" not in para


def test_step4_hands_rejected_entries_on_to_the_findings_step():
    # The findings step renders the refuted-claims gap from `evidence_rejected`,
    # so the orchestrator must pass the list on rather than drop it.
    para = _step4_check_paragraph()
    assert "removed from the report input" not in para
    assert "`evidence_rejected`" in para and "handed on" in para


def _evidence_cell_rule() -> str:
    text = FINDINGS_SKILL.read_text(encoding="utf-8")
    return next(p for p in text.split("\n\n") if "`(unverified)`" in p)


def test_evidence_cell_marks_only_unoffered_evidence_unverified():
    # No entries offered is an honest limit of the run: keep the note, flag it.
    rule = _evidence_cell_rule()
    unverified = next(s for s in rule.split(". ") if "`(unverified)`" in s)
    assert "offered no" in unverified
    assert "rejected" not in unverified and "held" not in unverified


def test_evidence_cell_gives_na_layer_no_unverified_marker():
    # An N/A layer is scored with no entries by design (the scorer skips
    # `na_layers`), so it must not fall into the no-entries-offered state.
    rule = _evidence_cell_rule()
    na = next(s for s in rule.split(". ") if "N/A layer" in s)
    assert "`(unverified)`" not in na
    assert "archetype rule" in na and "no marker" in na


def test_evidence_cell_rule_sits_beside_archetype_rule():
    # The two rules govern the same cell; kept apart they contradicted each other.
    text = FINDINGS_SKILL.read_text(encoding="utf-8")
    paras = text.split("\n\n")
    archetype = next(i for i, p in enumerate(paras) if "Archetype-aware Status" in p)
    assert "`(unverified)`" in paras[archetype + 1]


def test_evidence_cell_renders_refuted_claims_as_a_gap():
    # Entries offered and all rejected were checked and found false: repeating
    # the note, even flagged, restates the claim that just failed.
    rule = _evidence_cell_rule()
    assert "no verified evidence - N claim(s) rejected" in rule
    assert "drop the scorer's note" in rule
    assert "`reason`" in rule


def test_evidence_cell_strikes_refuted_claims_when_only_some_entries_fail():
    # The common case is neither empty state: a layer cites three entries, two
    # hold, one is refuted. Rendering the note verbatim publishes the refuted claim.
    rule = _evidence_cell_rule()
    partial = next(s for s in rule.split(". ") if "only some" in s)
    assert "strike the refuted claims" in partial
    assert "N of M claim(s) rejected" in partial
    assert "`(unverified)`" not in partial


def test_cite_only_verified_exempts_only_the_empty_evidence_cell_states():
    # Exempting the whole Evidence cell let a partly refuted note through verbatim.
    text = FINDINGS_SKILL.read_text(encoding="utf-8")
    para = next(p for p in text.split("\n\n") if "cite only verified evidence" in p)
    cell = next(s for s in para.split(". ") if "Evidence cell" in s)
    assert "exempt only in its two empty states" in cell
    assert "some verified entries" in cell
