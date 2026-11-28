"""`/issues` triage applies the shared definition of ready through the refiner's checks (#1627).

The definition of ready (`docs/contributing/definition-of-ready.md`) is one
checklist for the refiner and `/issues` triage. These tests pin the triage side:
`skills/issues/SKILL.md` names the checklist file and runs `refine.py`, the
command it shows parses and runs against recorded `gh` output, every judgement
item of the checklist has an owning triage step, and the size marker triage
writes is the one the script reads.
"""
import re
import shlex
from pathlib import Path
from typing import Any

import yaml  # required: the plugin contract job installs pyyaml
from refine_env import DEFINITION, FIXTURE_REPO, NOW, recorded_backlog

import refine  # noqa: E402  (refine_env puts the scripts on sys.path)

REPO = Path(__file__).resolve().parent.parent
SKILL = REPO / "skills" / "issues" / "SKILL.md"
SCRIPT_REL = "skills/refine/scripts/refine.py"
FENCE_RE = re.compile(r"^```yaml\n(.*?)^```$", re.MULTILINE | re.DOTALL)


def _skill() -> str:
    return SKILL.read_text(encoding="utf-8")


def _readiness_section() -> str:
    match = re.search(r"^### Readiness Check\n(.*?)^### ", _skill(), re.MULTILINE | re.DOTALL)
    assert match, "skills/issues/SKILL.md has a Readiness Check step"
    return match.group(1)


def _ready_block() -> Any:
    return yaml.safe_load(FENCE_RE.findall(DEFINITION.read_text(encoding="utf-8"))[0])


def _check_command() -> list[str]:
    lines = [ln for ln in _readiness_section().splitlines() if SCRIPT_REL in ln]
    assert len(lines) == 1, "the Readiness Check runs refine.py on exactly one line"
    return shlex.split(lines[0].split("#", 1)[0])


def test_triage_names_the_shared_checklist_and_the_check_command() -> None:
    section = _readiness_section()
    assert "docs/contributing/definition-of-ready.md" in section
    assert f'"${{CLAUDE_PLUGIN_ROOT}}/{SCRIPT_REL}"' in section
    assert (REPO / SCRIPT_REL).is_file()
    assert "Readiness Check" in _skill().split("## Triage Mode", 1)[1].split("### Promote on Confirmation", 1)[0]


def test_the_check_command_parses_and_runs(tmp_path: Path) -> None:
    argv = _check_command()
    assert argv[0] == "python3"
    flags = argv[2:]
    # Substitute the shell values the skill sets, then run the script's own entry point.
    out = tmp_path / "verdict.json"
    values = {"$ORG/$REPO": FIXTURE_REPO, "$VERDICT": str(out)}
    args = [values.get(a, a) for a in flags]
    assert "--dry-run" in args, "triage reads the backlog without writing"
    code = refine.main([*args, "--definition", str(DEFINITION), "--now", NOW], backlog=recorded_backlog())
    assert code == 0
    assert out.is_file()


def test_every_judgement_item_has_an_owning_triage_step() -> None:
    section = _readiness_section()
    judgement = [e["id"] for e in _ready_block()["checklist"] if e["check"] == "judgement"]
    missing = [i for i in judgement if f"`{i}`" not in section]
    assert not missing, f"Readiness Check names no triage step for {missing}"
    for verdict in ("not_ready", "needs_judgement", "ready"):
        assert f"`{verdict}`" in section


def test_the_size_marker_triage_writes_is_the_one_the_script_reads() -> None:
    marker = re.compile(_ready_block()["size_marker"])
    match = re.search(r"printf '(<!-- refine:size %s -->)", _skill())
    assert match, "Size by Judgment records the size as a marker comment"
    assert marker.fullmatch(match.group(1).replace("%s", "5"))


def test_definition_no_longer_says_triage_uses_its_own_judgement() -> None:
    text = DEFINITION.read_text(encoding="utf-8")
    assert "by its own sizing judgement today" not in text
    assert "Readiness Check" in text
