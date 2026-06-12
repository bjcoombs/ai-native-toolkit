"""Contract tests for the repo-root composite action (action.yml).

The action is the published form of the assess gate - the thing consumers pin
by version and Dependabot upgrades. These tests hold it to the same invariants
the emitted-workflow template carried before the logic moved here: pinned
supply chain, warn-only infra degrade, and the gate step as the only path to a
red check.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

_ACTION_PATH = Path(__file__).resolve().parents[3] / "action.yml"


def _action() -> dict:
    return yaml.safe_load(_ACTION_PATH.read_text(encoding="utf-8"))


def _steps() -> list[dict]:
    return _action()["runs"]["steps"]


def test_action_is_composite_at_repo_root():
    action = _action()
    assert action["runs"]["using"] == "composite"
    # Root placement is what makes `uses: bjcoombs/ai-native-toolkit@v<semver>`
    # resolve (and keeps the action Marketplace-eligible).
    assert _ACTION_PATH.parent.name == "ai-native-toolkit" or (
        _ACTION_PATH.parent / ".claude-plugin"
    ).exists()


def test_action_installs_pinned_ripgrep():
    """The marker scan needs rg; ubuntu-latest does not ship it. Unpinned
    installs could move the regression baseline with no change in the tree."""
    text = _ACTION_PATH.read_text(encoding="utf-8")
    rg_lines = [ln for ln in text.splitlines() if "ripgrep/releases/download" in ln]
    assert rg_lines, "expected a pinned ripgrep download"
    assert re.search(r"/download/\d+\.\d+\.\d+/", rg_lines[0])


def test_action_installs_pinned_uv():
    text = _ACTION_PATH.read_text(encoding="utf-8")
    uv_lines = [ln for ln in text.splitlines() if "astral.sh/uv/" in ln]
    assert uv_lines, "expected a pinned uv installer"
    assert re.search(r"astral\.sh/uv/\d+\.\d+\.\d+/install\.sh", uv_lines[0]), (
        f"unpinned uv installer: {uv_lines[0].strip()}"
    )


def test_action_contains_no_floating_latest():
    assert "@latest" not in _ACTION_PATH.read_text(encoding="utf-8")


def test_action_runs_the_four_core_scripts():
    text = _ACTION_PATH.read_text(encoding="utf-8")
    for script in (
        "complexity-treemap.py",
        "assess_core.py",
        "assess_report.py",
        "assess_gate.py",
    ):
        assert script in text, f"missing core script: {script}"


def test_assessment_steps_are_guarded_on_uv():
    """Render + gate only run when uv is available; unavailable uv must skip
    with a notice (warn-only contract), never fail the consumer's check."""
    guarded = [
        s for s in _steps() if s.get("if") == "steps.ensure-uv.outputs.ok == 'true'"
    ]
    assert len(guarded) == 2
    ensure = next(s for s in _steps() if s.get("id") == "ensure-uv")
    assert "::notice::" in ensure["run"]
    assert "ok=false" in ensure["run"]


def test_infra_steps_cannot_red_the_check():
    """The ripgrep step must always exit 0 (degrade = reduced coverage); only
    the gate step's exit code may fail the consumer's PR."""
    rg_step = next(s for s in _steps() if "ripgrep" in s["name"].lower())
    assert rg_step["run"].rstrip().endswith("exit 0")
    gate_step = next(s for s in _steps() if "assess_gate.py" in s.get("run", ""))
    assert "exit 0" not in gate_step["run"]
    assert "continue-on-error" not in gate_step


def test_config_input_default():
    assert _action()["inputs"]["config"]["default"] == ".assess/config.toml"


def test_own_workflow_self_tests_the_action():
    """This repo's own gate must run `uses: ./` so every PR exercises the
    action from the branch under review, not a stale released tag."""
    wf = (
        _ACTION_PATH.parent / ".github" / "workflows" / "assess-gate.yml"
    ).read_text(encoding="utf-8")
    assert "uses: ./" in wf
