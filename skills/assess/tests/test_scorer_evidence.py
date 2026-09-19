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
    assert '"$SKILL_DIR/scripts/lib/evidence_check.py"' in window
    assert "-m lib.evidence_check" not in window
    assert "<!-- chat-replace:evidence-check -->" in window
