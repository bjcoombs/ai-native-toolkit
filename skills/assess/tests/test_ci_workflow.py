"""Tests for the frozen-harness workflow emitter (lib/ci_workflow.py).

The emitter bakes the discovered toolchain into a GitHub Action. These tests pin
the substitution (version, branch, tool steps), the literal-dollar escaping for
shell vars, and that the result is well-formed YAML when a parser is available.
"""
from __future__ import annotations


import pytest

from lib.ci_workflow import emit_ci_workflow, render_ci_workflow


def test_render_substitutes_version_and_branch():
    out = render_ci_workflow(plugin_version="1.23.0", default_branch="develop")
    assert "ai-native-toolkit v1.23.0" in out
    assert 'branch "v1.23.0"' in out
    assert "branches: [develop]" in out


def test_render_escapes_shell_vars():
    """${RUNNER_TEMP} / ${GITHUB_WORKSPACE} must survive as literal shell refs."""
    out = render_ci_workflow(plugin_version="1.23.0")
    assert "${RUNNER_TEMP}/ai-native-toolkit" in out
    assert "${GITHUB_WORKSPACE}/.assess" in out
    # No unresolved template placeholders leaked through.
    assert "$plugin_version" not in out
    assert "$tool_steps" not in out
    assert "$default_branch" not in out


def test_working_directory_uses_actions_expression_not_shell_var():
    """`working-directory:` only expands `${{ }}` expressions, not shell `${VAR}`.

    Regression: a `working-directory: ${RUNNER_TEMP}/...` cd's into a literal
    `${RUNNER_TEMP}` dir and the step dies with 'No such file or directory'. The
    runner temp dir must be referenced as the GitHub expression `${{ runner.temp }}`.
    """
    out = render_ci_workflow(plugin_version="1.23.0")
    for line in out.splitlines():
        stripped = line.strip()
        if stripped.startswith("working-directory:"):
            assert "${{ runner.temp }}" in stripped, stripped
            assert "${RUNNER_TEMP}" not in stripped, stripped


def test_render_emits_scc_install_step():
    out = render_ci_workflow(plugin_version="1.23.0", discovered_tools=["lizard", "scc"])
    assert "Install scc" in out
    assert "go install github.com/boyter/scc" in out


def test_render_skips_python_dep_tools():
    """Python deps (lizard, grimp) ride uv - they never get an OS install step."""
    out = render_ci_workflow(plugin_version="1.23.0", discovered_tools=["lizard", "grimp"])
    assert "Install scc" not in out
    assert "without an install recipe" not in out


def test_render_comments_unknown_tools():
    out = render_ci_workflow(plugin_version="1.23.0", discovered_tools=["weirdtool"])
    assert "without an install recipe: weirdtool" in out


def test_render_dedupes_tools():
    out = render_ci_workflow(plugin_version="1.23.0", discovered_tools=["scc", "scc"])
    assert out.count("Install scc") == 1


def test_render_runs_the_four_core_scripts():
    out = render_ci_workflow(plugin_version="1.23.0")
    for script in ("complexity-treemap.py", "assess_core.py", "assess_report.py", "assess_gate.py"):
        assert script in out


def test_render_is_valid_yaml():
    yaml = pytest.importorskip("yaml")
    out = render_ci_workflow(plugin_version="1.23.0", discovered_tools=["scc"])
    doc = yaml.safe_load(out)
    assert doc["name"] == "Assess Gate"
    # PyYAML parses the unquoted `on:` key as boolean True (the YAML 1.1 norm).
    on = doc.get("on", doc.get(True))
    assert "pull_request" in on
    assert "assess" in doc["jobs"]


def test_emit_writes_workflow_file(tmp_path):
    path = emit_ci_workflow(tmp_path, ["scc"], "1.23.0", default_branch="main")
    assert path == tmp_path / ".github" / "workflows" / "assess-gate.yml"
    assert path.is_file()
    assert "Assess Gate" in path.read_text()


def test_emit_creates_nested_dirs(tmp_path):
    """No pre-existing .github/ - the emitter creates the full path."""
    path = emit_ci_workflow(tmp_path, [], "1.23.0")
    assert path.is_file()
