"""The skill-disclosure condition (#1045): read per skill unit, met, unmet or not measured, no Step gate."""
from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest

import lib.level_tally as lt
import lib.manifest.check_manifest as cm
import lib.unit_readings as ur
from lib.skill_disclosure import scan_skill_disclosure
from lib.unit_components import KINDS
from test_unit_readings import _read, _write

CID = "skill-disclosure.1"
# The maintainer's approved why on #1045, byte for byte.
WHY = ("A skill loads its whole SKILL.md every time it triggers, so detail that only one branch needs "
       "spends an agent's context before work starts; keeping the body small, the description within "
       "its limit and references one level deep keeps that cost to what the task uses.")
WHY_SOURCE = "https://docs.claude.com/en/docs/agents-and-tools/agent-skills/best-practices"

PLUGIN = {
    ".claude-plugin/plugin.json": '{"name": "p"}\n',
    "agents/reviewer.md": "---\nname: reviewer\ndescription: Reviews.\n---\n",
    "skills/good/SKILL.md": "---\nname: good\ndescription: Does good things.\n---\nSee [g](references/g.md).\n",
    "skills/good/references/g.md": "# G\n",
    "skills/bad/SKILL.md": "---\nname: bad\n---\nSee [gone](references/gone.md).\n",
}


def _condition(result: Any) -> Any:
    return next(c for c in result["conditions"] if c["id"] == CID)


# --- the condition ---------------------------------------------------------------------


@pytest.mark.parametrize(("files", "state", "reason"), [
    ({"skills/good/SKILL.md": PLUGIN["skills/good/SKILL.md"], "skills/good/references/g.md": "# G\n"}, "met", None),
    ({"skills/bad/SKILL.md": PLUGIN["skills/bad/SKILL.md"]}, "unmet", None),
    ({"README.md": "# No skills\n"}, "not_measured", "no_skill_files"),
])
def test_the_condition_reads_met_unmet_or_not_measured(tmp_path: Path, files: dict[str, str], state: str,
                                                       reason: str | None) -> None:
    repo = _write(tmp_path / "r", {".claude-plugin/plugin.json": '{"name": "p"}\n', **files})
    cond = _condition(lt.level_result(lt.default_model(), {"skill_disclosure": scan_skill_disclosure(repo)}))
    assert cond["state"] == state and cond.get("reason") == reason
    assert cond["grade"] == "measured"  # it reads the files' structure at the commit: counted


def test_it_gates_no_step() -> None:
    assert not [c for c in lt.gate_conditions(lt.default_model()) if c.startswith("skill-disclosure.")]


def test_the_why_is_the_approved_text_with_its_source() -> None:
    manifest = cm.core_manifest()
    cond = next(e for e in manifest["entries"] if e.get("key") == f"{CID}:skill_disclosure.files_breaking")
    assert (cond["why"], cond["why_source"]) == (WHY, WHY_SOURCE)  # type: ignore[typeddict-item]
    collector = next(c for c in cm.collectors(manifest) if c["id"] == "core.skill_disclosure")
    assert collector.get("whys", {}).get("skill_disclosure") == WHY


# --- per skill unit --------------------------------------------------------------------


def test_each_skill_unit_reads_its_own_skill_and_other_kinds_inherit(tmp_path: Path) -> None:
    block = _read(_write(tmp_path / "plugin", PLUGIN))
    units = {u["path"]: u for u in block["units"]}
    assert units["skills/good"]["kind"] == units["skills/bad"]["kind"] == "skill"
    good = units["skills/good"]["blocks"]["skill_disclosure"]
    bad = units["skills/bad"]["blocks"]["skill_disclosure"]
    assert (good["files_considered"], good["files_breaking"]) == (1, 0)
    assert (bad["files_considered"], bad["files_breaking"]) == (1, 1)
    origin = {u: next(c["origin"] for c in units[u]["conditions"] if c["id"] == CID) for u in units}
    assert origin["skills/good"] == origin["skills/bad"] == "unit"
    assert "skill_disclosure" not in units["agents"]["blocks"] and origin["agents"] == "inherited"


def test_only_skill_units_own_the_block() -> None:
    kinds = ur.unit_kinds(cm.core_manifest())
    assert kinds == {"skill_disclosure": frozenset({"skill"})}
    assert set(cm.UNIT_KINDS) == set(KINDS)  # the manifest's list is the units block's
    assert ur._kind_blocks(["skill_disclosure", "structure"], kinds, "package") == ["structure"]
    assert ur._kind_blocks(["skill_disclosure", "structure"], kinds, "skill") == ["skill_disclosure", "structure"]


@pytest.mark.parametrize("value", [["plugin"], [], "skill"])
def test_a_bad_unit_kinds_fails_the_manifest(value: Any) -> None:
    edited: Any = copy.deepcopy(cm.core_manifest())
    next(e for e in edited["entries"] if e["id"] == "core.skill_disclosure")["unit_kinds"] = value
    errors = cm.validate_manifest(edited)
    assert errors and all("unit_kinds" in e["path"] for e in errors)
