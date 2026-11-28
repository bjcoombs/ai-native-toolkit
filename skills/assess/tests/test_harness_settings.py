"""Non-Claude agent configuration (#846): other harnesses' files widen the existing conditions.

Every fixture is invented: hook names, commands and instruction text exist only here.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

import lib.scoring.level_tally as lt
from lib.agents.agent_harness import scan_agent_harness
from lib.agents.agent_ops import scan_agent_ops
from lib.agents.harness_settings import (HOOK_CONDITIONS, HOOKS_DISABLED, auto_approve, broad_in_text, hook_acts,
                                         is_broad_rule, read_hooks_disabled)
from lib.counts_only.content_free import counts_only
from lib.instructions.instruction_files import grade_instruction_files, nested_instruction_surface
from lib.owner_exclusions import owner_exclusions_block
from lib.run.decline_markers import decline_badge
from lib.scoring.layer_grades import scan_layer_grades
from lib.scoring.level_result_types import DeclineRecord

COPILOT_HOOK = json.dumps({"version": 1, "hooks": {"sessionStart": [
    {"type": "command", "bash": "./scripts/check-env.sh", "powershell": "./scripts/check-env.ps1"}]}})
CLAUDE_HOOK = json.dumps({"hooks": {"PreToolUse": [
    {"matcher": "Bash", "hooks": [{"type": "command", "command": "./hooks/guard.sh"}]}]}})
INSTRUCTIONS = "# Project\n\nRun `make test` before every push.\n"


def _repo(root: Path, files: dict[str, str]) -> Path:
    """A committed repository holding ``files`` and a README."""
    root.mkdir(parents=True, exist_ok=True)
    for rel, text in {"README.md": "fixture\n", **files}.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    for args in (["init", "-q"], ["add", "-A"],
                 ["-c", "user.email=t@example.com", "-c", "user.name=T", "commit", "-qm", "init"]):
        subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)
    return root


def _present(repo: Path) -> bool | None:
    return scan_layer_grades(grade_instruction_files(repo), nested_instruction_surface(repo))[
        "instruction_file_present"]


def _data(repo: Path) -> dict[str, Any]:
    return {"layer_grades": {"instruction_file_present": _present(repo)}, "agent_ops": scan_agent_ops(repo),
            "owner_exclusions": owner_exclusions_block(repo)}


def _cond(result: lt.LevelResult, cid: str) -> lt.ConditionResult:
    return next(c for c in result["conditions"] if c["id"] == cid)


def _met(result: lt.LevelResult) -> int:
    return sum(1 for c in result["conditions"] if c["state"] == "met")


# --- instruction files ---------------------------------------------------------------------


@pytest.mark.parametrize("files", [
    {".github/copilot-instructions.md": INSTRUCTIONS},
    {".github/instructions/python.instructions.md": f"---\napplyTo: \"**/*.py\"\n---\n{INSTRUCTIONS}"},
])
def test_a_repository_with_only_copilot_files_meets_the_instruction_condition(tmp_path: Path, files: dict[str, str]) -> None:
    repo = _repo(tmp_path, files)
    assert _present(repo) is True
    assert _cond(lt.level_result(lt.default_model(), _data(repo)), "layer-0-instructions.1")["state"] == "met"


def test_a_cursor_folder_of_editor_state_counts_for_nothing(tmp_path: Path) -> None:
    repo = _repo(tmp_path, {".cursor/environment.json": '{"install": "npm ci"}',
                            ".cursor/mcp.json": '{"mcpServers": {}}', ".vscode/settings.json": '{"editor.tabSize": 2}'})
    assert _present(repo) is False
    block = scan_agent_ops(repo)
    assert block["settings"] == []
    assert (block["summary"]["hooks_present"], block["summary"]["permissions_encoded"]) == (False, False)


def test_claude_and_copilot_files_meet_no_more_conditions_than_claude_alone(tmp_path: Path) -> None:
    claude = {"CLAUDE.md": INSTRUCTIONS, ".claude/settings.json": CLAUDE_HOOK}
    copilot = {".github/copilot-instructions.md": INSTRUCTIONS, ".github/hooks/build.json": COPILOT_HOOK,
               ".cursor/hooks.json": json.dumps({"version": 1, "hooks": {"afterFileEdit": [{"command": "./fmt.sh"}]}})}
    both, alone = _repo(tmp_path / "both", {**claude, **copilot}), _repo(tmp_path / "alone", claude)
    model = lt.default_model()
    met = _met(lt.level_result(model, _data(alone)))
    assert met > 0 and _met(lt.level_result(model, _data(both))) == met


# --- hooks ---------------------------------------------------------------------------------


def test_a_copilot_hook_file_alone_meets_the_hook_signal(tmp_path: Path) -> None:
    repo = _repo(tmp_path, {".github/hooks/build.json": COPILOT_HOOK})
    block = scan_agent_ops(repo)
    assert [(s["path"], s["tool"], s["hook_events"]) for s in block["settings"]] == [
        (".github/hooks/build.json", "copilot", 1)]
    assert block["summary"]["hooks_present"] is True


@pytest.mark.parametrize("rel, text, events", [
    (".cursor/hooks.json", {"version": 1, "hooks": {"beforeShellExecution": [{"command": "./guard.sh"}]}}, 1),
    (".gemini/settings.json", {"hooks": {"BeforeTool": [{"matcher": "write_file",
                                                          "hooks": [{"type": "command", "command": "./lint.sh"}]}]}}, 1),
    (".gemini/settings.json", {"hooks": {"BeforeTool": [{"type": "command", "command": "./lint.sh"}]}}, 0),
    (".github/hooks/noop.json", {"version": 1, "hooks": {"sessionStart": [{"type": "command", "bash": "true"}]}}, 0),
])
def test_each_harness_hook_shape(tmp_path: Path, rel: str, text: dict[str, Any], events: int) -> None:
    repo = _repo(tmp_path, {rel: json.dumps(text)})
    block = scan_agent_ops(repo)
    assert block["settings"][0]["hook_events"] == events
    assert block["summary"]["hooks_present"] is (events > 0)


def test_an_untracked_harness_hook_is_reported_but_never_credited(tmp_path: Path) -> None:
    (tmp_path / ".github/hooks").mkdir(parents=True)
    (tmp_path / ".github/hooks/build.json").write_text(COPILOT_HOOK, encoding="utf-8")
    block = scan_agent_ops(tmp_path)
    assert block["settings"][0]["tracked"] is False and block["summary"]["hooks_present"] is False


def test_hook_acts_reads_flat_and_grouped_entries() -> None:
    assert hook_acts({"command": "./x.sh"}, flat=True) is True
    assert hook_acts({"command": "./x.sh"}) is False  # a typeless grouped hook (#922)
    assert hook_acts({"type": "command", "powershell": "./x.ps1"}, flat=True) is True
    assert hook_acts({"type": "command", "bash": "exit 0"}, flat=True) is False
    assert hook_acts({"type": "prompt", "prompt": "check"}) is True
    assert hook_acts("./x.sh", flat=True) is False


ROUTINE = {".claude/routines/triage.md": "Label new issues each morning.\n"}


def test_a_disabled_hook_file_reads_declined_not_met(tmp_path: Path) -> None:
    repo = _repo(tmp_path, {".github/hooks/build.json.disabled": COPILOT_HOOK, **ROUTINE})
    block = scan_agent_ops(repo)
    assert block["settings"] == [] and block["summary"]["hooks_present"] is False
    assert block["summary"]["hooks_disabled"] == 1
    record = read_hooks_disabled(repo)
    assert record is not None and (record["source"], record["conditions"]) == (HOOKS_DISABLED, list(HOOK_CONDITIONS))
    assert owner_exclusions_block(repo)["declines"] == [record]
    result = lt.level_result(lt.default_model(), _data(repo))
    cond = _cond(result, "layer-8-agent-ops.2")
    assert (cond["state"], cond["counted"]) == ("declined", False)
    assert decline_badge(record) == "agent hooks disabled in .github/hooks/build.json.disabled"


def test_a_disabled_hook_never_hides_missing_agent_work(tmp_path: Path) -> None:
    repo = _repo(tmp_path, {".github/hooks/build.json.disabled": COPILOT_HOOK})
    assert read_hooks_disabled(repo) is not None  # the files are read
    assert "declines" not in owner_exclusions_block(repo)  # but level 2 also needs agent work
    cond = _cond(lt.level_result(lt.default_model(), _data(repo)), "layer-8-agent-ops.2")
    assert cond["state"] == "unmet"


def test_a_disabled_file_beside_a_live_hook_declines_nothing(tmp_path: Path) -> None:
    repo = _repo(tmp_path, {".github/hooks/build.json.disabled": COPILOT_HOOK, ".claude/settings.json": CLAUDE_HOOK})
    assert read_hooks_disabled(repo) is None
    assert "declines" not in owner_exclusions_block(repo)


@pytest.mark.parametrize("text", ["{}", json.dumps({"hooks": {"sessionStart": [{"bash": ":"}]}}), "not json"])
def test_a_disabled_file_without_a_hook_that_acts_declines_nothing(tmp_path: Path, text: str) -> None:
    repo = _repo(tmp_path, {".cursor/hooks.json.disabled": text})
    assert read_hooks_disabled(repo) is None
    assert scan_agent_ops(repo)["summary"]["hooks_disabled"] == 0


def test_an_untracked_disabled_file_declines_nothing(tmp_path: Path) -> None:
    (tmp_path / ".github/hooks").mkdir(parents=True)
    (tmp_path / ".github/hooks/build.json.disabled").write_text(COPILOT_HOOK, encoding="utf-8")
    assert read_hooks_disabled(tmp_path) is None


def test_hook_conditions_name_every_default_condition_that_reads_the_hook_signal() -> None:
    model = lt.default_model()
    reading = [lt.condition_id(row["id"], level["level"]) for row in model["rows"] for level in row["levels"]
               if any(c["signal"] == "agent_ops.summary.hooks_present" for c in level["criteria"])]
    assert reading == list(HOOK_CONDITIONS)


def test_the_hooks_record_names_itself_when_counts_only_drops_its_reason() -> None:
    record: DeclineRecord = {"source": HOOKS_DISABLED, "conditions": list(HOOK_CONDITIONS), "declined_by": None,
                             "declined_at": None, "version": None, "reason": None}
    assert decline_badge(record) == "agent hooks disabled"


def test_a_hooks_record_never_covers_a_condition_without_an_agent_tag() -> None:
    record: DeclineRecord = {"source": HOOKS_DISABLED, "conditions": ["layer-6-coverage.1", *HOOK_CONDITIONS],
                             "declined_by": None, "declined_at": None, "version": None, "reason": "x"}
    assert list(lt.declined_ids(lt.default_model(), [record])) == list(HOOK_CONDITIONS)


# --- VS Code auto-approve --------------------------------------------------------------------


def _vscode(tmp_path: Path, settings: str) -> Path:
    return _repo(tmp_path, {".vscode/settings.json": settings})


def test_a_broad_terminal_auto_approve_raises_the_finding(tmp_path: Path) -> None:
    repo = _vscode(tmp_path, '{\n  // agents\n  "chat.tools.terminal.autoApprove": {"*": true, "rm": false,},\n}\n')
    block = scan_agent_ops(repo)
    assert [(s["tool"], s["allow_count"], s["deny_count"]) for s in block["settings"]] == [("vscode", 1, 1)]
    assert block["summary"]["permissions_encoded"] is True
    findings = [(f["kind"], f["file"], f["line"]) for f in scan_agent_harness(repo)["findings"]]
    assert findings == [("broad_auto_approve", ".vscode/settings.json", 3)]


def test_an_untracked_vscode_settings_file_raises_nothing(tmp_path: Path) -> None:
    repo = _repo(tmp_path, {})
    (repo / ".vscode").mkdir()
    (repo / ".vscode/settings.json").write_text('{"chat.tools.terminal.autoApprove": {"*": true}}', encoding="utf-8")
    assert scan_agent_harness(repo)["findings"] == []
    assert scan_agent_ops(repo)["summary"]["permissions_encoded"] is False


@pytest.mark.parametrize("text, broad", [
    ('{"chat.tools.terminal.autoApprove": {"*": true, "rm": false}', ["*"]),  # no closing brace
    ('{"chat.tools.terminal.autoApprove": {"/^.+$/": {"approve": true}},, }', ["/^.+$/"]),
    ('{"chat.tools.global.autoApprove": true,, "x": }', ["chat.tools.global.autoApprove"]),
    ('{"files.exclude": {"*": true}, "chat.tools.terminal.autoApprove": {"npm test": true}', []),
])
def test_an_unparseable_auto_approve_file_is_listed_and_never_reads_clean(
        tmp_path: Path, text: str, broad: list[str]) -> None:
    repo = _vscode(tmp_path, text)
    entries = scan_agent_ops(repo)["settings"]
    assert [(e["tool"], e["parse_ok"], e["allow_count"]) for e in entries] == [("vscode", False, 0)]
    assert scan_agent_ops(repo)["summary"]["permissions_encoded"] is False  # unread is never credited
    findings = scan_agent_harness(repo)["findings"]
    assert [(f["kind"], f["file"]) for f in findings] == [("broad_auto_approve", ".vscode/settings.json")] * len(broad)
    assert broad_in_text(text) == broad


def test_an_unparseable_editor_file_without_auto_approve_is_not_listed(tmp_path: Path) -> None:
    repo = _vscode(tmp_path, '{"editor.tabSize": 2,, }}')
    assert scan_agent_ops(repo)["settings"] == [] and scan_agent_harness(repo)["findings"] == []


def test_a_narrow_auto_approve_list_raises_nothing(tmp_path: Path) -> None:
    repo = _vscode(tmp_path, json.dumps({"chat.tools.terminal.autoApprove": {
        "npm test": True, "/^git (status|log)\\b/": True, "git push": {"approve": False}}}))
    assert scan_agent_harness(repo)["findings"] == []
    assert scan_agent_ops(repo)["settings"][0]["allow_count"] == 2


@pytest.mark.parametrize("key, broad", [
    ("*", True), ("* *", True), ("/.*/", True), ("/^.+$/i", True), ("/[\\s\\S]*/", True), ("/^/", True), ("/$/", True), ("//", True),
    ("npm *", False), ("/^npm .*/", False), ("", False), ("git", False),
])
def test_broad_rule_keys(key: str, broad: bool) -> None:
    assert is_broad_rule(key) is broad


def test_an_all_tools_auto_approve_is_broad() -> None:
    rules = auto_approve({"chat.tools.global.autoApprove": True})
    assert rules is not None and rules.broad == ["chat.tools.global.autoApprove"]
    assert auto_approve({"chat.tools.autoApprove": False}) is None


def test_counts_only_keeps_the_tool_and_the_disabled_count(tmp_path: Path) -> None:
    repo = _repo(tmp_path, {".github/hooks/build.json": COPILOT_HOOK,
                            ".github/hooks/old.json.disabled": COPILOT_HOOK})
    out = counts_only({"agent_ops": scan_agent_ops(repo)})["agent_ops"]
    assert out["settings"][0]["tool"] == "copilot" and out["settings"][0]["path"] == ".github/hooks/build.json"
    assert out["summary"]["hooks_disabled"] == 1 and out["summary"]["hooks_present"] is True
