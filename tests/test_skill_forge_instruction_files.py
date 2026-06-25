"""Contract checks for skill-forge's instruction-file generalization (#226).

No AI, no network. Two kinds of assertion:

1. The count-surface trap fixture is *real and reproducible* - the CLAUDE.md
   checklist names a proper subset of what the synthetic validator enforces, and
   updating only the checklist surfaces leaves an enforced surface stale (a build
   break). This is the deterministic proof behind success criterion 3: a re-forge
   of the trap reproduces the count-surface finding without hand-holding.
2. The documentation invariants the generalization rests on are present: the
   artifact-type detector lists the instruction-file types, the lens-selector
   drops Trigger/routing for an instruction file, Fidelity carries the accuracy
   sub-check, and ab-equivalence's runner ships the instruction-file variant.
"""
import importlib.util
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
FORGE = REPO / "skills" / "skill-forge"
FIXTURE = FORGE / "tests" / "fixtures" / "flawed-instruction-file"


def _load_check_counts():
    spec = importlib.util.spec_from_file_location(
        "flawed_instruction_file_check_counts", FIXTURE / "check_counts.py"
    )
    assert spec and spec.loader, "could not load fixture check_counts.py"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _section(text: str, heading: str) -> str:
    """Return the body of the markdown section under *heading* (## level)."""
    lines = text.splitlines()
    out: list[str] = []
    capturing = False
    for line in lines:
        if line.strip().startswith("## "):
            if capturing:
                break
            capturing = line.strip() == f"## {heading}"
            continue
        if capturing:
            out.append(line)
    return "\n".join(out)


# ── the count-surface trap is real and reproducible ───────────────────────────


def test_fixture_files_present():
    for name in ("CLAUDE.md", "check_counts.py", "DEFECTS.md"):
        assert (FIXTURE / name).is_file(), f"flawed-instruction-file fixture missing {name}"


def test_validator_enforces_more_than_one_surface():
    cc = _load_check_counts()
    assert len(cc.ENFORCED_COUNT_SURFACES) >= 2, (
        "a count-surface trap needs the validator to enforce >1 surface"
    )


def test_checklist_is_proper_subset_of_enforced_surfaces():
    cc = _load_check_counts()
    enforced = set(cc.ENFORCED_COUNT_SURFACES)
    section = _section((FIXTURE / "CLAUDE.md").read_text("utf-8"), "Adding a component")
    backticked = set(re.findall(r"`([^`]+)`", section))
    checklist = backticked & enforced

    assert checklist, "the checklist names no enforced count surface - trap is not set"
    assert checklist < enforced, (
        "the count-surface trap requires the checklist to name a PROPER subset of "
        f"the enforced surfaces; checklist={sorted(checklist)} enforced={sorted(enforced)}"
    )
    omitted = enforced - checklist
    assert omitted, "expected at least one enforced-but-unlisted surface"


def test_trusting_the_checklist_breaks_the_build():
    """A runner that trusts the checklist leaves the omitted surface stale -> CI fails."""
    cc = _load_check_counts()
    enforced = set(cc.ENFORCED_COUNT_SURFACES)
    section = _section((FIXTURE / "CLAUDE.md").read_text("utf-8"), "Adding a component")
    checklist = set(re.findall(r"`([^`]+)`", section)) & enforced

    old_count, new_count = 11, 12
    # The agent adds a component and updates only the surfaces the checklist names;
    # every enforced surface started in sync at old_count.
    surface_counts = {s: old_count for s in enforced}
    for surface in checklist:
        surface_counts[surface] = new_count

    assert not cc.build_passes(surface_counts, new_count), (
        "trusting the checklist must break the build - else the trap is toothless"
    )
    stale = cc.stale_surfaces(surface_counts, new_count)
    assert set(stale) == (enforced - checklist), (
        f"the stale surfaces must be exactly the unlisted enforced ones; got {stale}"
    )

    # Control: updating EVERY enforced surface passes - the gate is satisfiable.
    assert cc.build_passes({s: new_count for s in enforced}, new_count)


# ── documentation invariants the generalization rests on ──────────────────────

INSTRUCTION_FILE_TOKENS = ("CLAUDE.md", "AGENTS.md", "GEMINI.md")


def test_skill_md_documents_artifact_type_detection():
    text = (FORGE / "SKILL.md").read_text("utf-8")
    for tok in INSTRUCTION_FILE_TOKENS:
        assert tok in text, f"skill-forge SKILL.md should name the {tok} instruction-file type"
    assert re.search(r"artifact.type", text, re.IGNORECASE), (
        "skill-forge SKILL.md should describe artifact-type detection"
    )


def test_lens_selector_drops_trigger_for_instruction_files():
    text = (FORGE / "references" / "judge-lenses.md").read_text("utf-8")
    assert re.search(r"artifact.type selector", text, re.IGNORECASE), (
        "judge-lenses.md should document the artifact-type lens selector"
    )
    # The selector must say Trigger/routing is dropped for an instruction file.
    assert "Trigger/routing dropped" in text, (
        "judge-lenses.md should state Trigger/routing is dropped for an instruction file"
    )


def test_fidelity_accuracy_subcheck_documented():
    text = (FORGE / "references" / "judge-lenses.md").read_text("utf-8")
    assert "accuracy sub-check" in text, "Fidelity accuracy sub-check must be documented"
    assert "count-surface trap" in text, "the count-surface trap must be named in judge-lenses.md"


def test_gate_hierarchy_includes_accuracy_at_gate1():
    text = (FORGE / "references" / "gate-hierarchy.md").read_text("utf-8")
    assert "accuracy sub-check" in text, (
        "gate-hierarchy.md should fold the accuracy sub-check into Gate 1"
    )


def test_runner_prompt_has_instruction_file_variant():
    runner = REPO / "skills" / "ab-equivalence" / "references" / "runner-prompt.md"
    text = runner.read_text("utf-8")
    assert "instruction-file variant" in text.lower(), (
        "ab-equivalence runner-prompt.md must ship the instruction-file variant"
    )
    assert re.search(r"read-only|sandbox", text, re.IGNORECASE), (
        "the instruction-file runner variant must be read-only / sandboxed"
    )


def test_test_taxonomy_maps_instruction_file_tasks():
    text = (FORGE / "references" / "test-taxonomy.md").read_text("utf-8")
    assert "trap case" in text.lower(), "test-taxonomy.md should require a trap case for instruction files"
    assert "flawed-instruction-file" in text, (
        "test-taxonomy.md should point at the flawed-instruction-file fixture"
    )


@pytest.mark.parametrize("path", [FIXTURE / "DEFECTS.md", FIXTURE / "CLAUDE.md"])
def test_fixture_answer_key_names_the_omitted_surface(path):
    cc = _load_check_counts()
    enforced = set(cc.ENFORCED_COUNT_SURFACES)
    section = _section((FIXTURE / "CLAUDE.md").read_text("utf-8"), "Adding a component")
    omitted = enforced - (set(re.findall(r"`([^`]+)`", section)) & enforced)
    text = path.read_text("utf-8")
    for surface in omitted:
        assert surface in text, f"{path.name} should reference the omitted surface {surface}"
