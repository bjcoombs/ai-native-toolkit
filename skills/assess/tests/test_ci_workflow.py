"""Tests for the frozen-harness workflow emitter (lib/ci_workflow.py).

The emitter bakes the discovered toolchain into a GitHub Action. These tests pin
the substitution (version, branch, tool steps), the literal-dollar escaping for
shell vars, the supply-chain pins (no @latest, actions on commit SHAs), the
warn-only infra degrade (a failed fetch/install skips instead of failing), and
that the result is well-formed YAML when a parser is available.
"""
from __future__ import annotations

import re

import pytest

from lib.ci_workflow import emit_ci_workflow, render_ci_workflow

# Every external tool the emitter knows how to install - renders the maximal
# workflow so the supply-chain assertions cover all recipes.
ALL_EXTERNAL_TOOLS = ["scc", "staticcheck", "ts-prune", "knip"]


def test_render_substitutes_version_and_branch():
    out = render_ci_workflow(plugin_version="1.23.0", default_branch="develop")
    assert "ai-native-toolkit v1.23.0" in out
    assert "uses: bjcoombs/ai-native-toolkit@v1.23.0" in out
    assert "branches: [develop]" in out


def test_render_leaves_no_unresolved_placeholders():
    out = render_ci_workflow(plugin_version="1.23.0")
    assert "$plugin_version" not in out
    assert "$tool_steps" not in out
    assert "$default_branch" not in out
    assert "$generated_date" not in out


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


def test_render_contains_no_floating_latest():
    """Supply-chain pin: @latest makes the frozen contract non-deterministic.

    A future scc (or other tool) release could shift complexity-stats.json and
    move the regression baseline without any change in the assessed tree.
    """
    out = render_ci_workflow(plugin_version="1.23.0", discovered_tools=ALL_EXTERNAL_TOOLS)
    assert "@latest" not in out


def test_render_pins_go_and_npm_tools_to_versions():
    out = render_ci_workflow(plugin_version="1.23.0", discovered_tools=ALL_EXTERNAL_TOOLS)
    for line in out.splitlines():
        if "go install" in line or "npm install" in line:
            assert re.search(r"@v?\d", line), f"unpinned install: {line.strip()}"


def test_render_pins_actions_to_commit_shas():
    """Every third-party action rides a full commit SHA with a version comment.

    Mutable tags (@v4) can be re-pointed by the action's maintainer (or an
    attacker with push access), silently changing what runs in the gate.
    """
    out = render_ci_workflow(plugin_version="1.23.0", discovered_tools=ALL_EXTERNAL_TOOLS)
    uses_lines = [ln for ln in out.splitlines() if ln.strip().startswith("uses:") or " uses:" in ln]
    assert uses_lines, "expected at least one uses: step"
    for line in uses_lines:
        if "bjcoombs/ai-native-toolkit@" in line:
            # First-party exception: this repo publishes IMMUTABLE releases, so
            # the tag cannot be re-pointed - the guarantee SHA-pinning buys for
            # third-party actions. A tag pin here is what lets Dependabot
            # propose readable version bumps to consumers.
            ref = line.split("@", 1)[1].strip()
            assert re.fullmatch(r"v\d+\.\d+\.\d+", ref), f"not semver-tag-pinned: {line.strip()}"
            continue
        ref = line.split("@", 1)[1].split("#", 1)[0].strip()
        assert re.fullmatch(r"[0-9a-f]{40}", ref), f"not SHA-pinned: {line.strip()}"
        assert re.search(r"#\s*v\d", line), f"missing version comment: {line.strip()}"


def test_checkout_does_not_persist_credentials():
    """Nothing downstream pushes, so the checked-out token must not linger."""
    out = render_ci_workflow(plugin_version="1.23.0")
    assert "persist-credentials: false" in out


def test_render_delegates_to_the_action_not_a_clone():
    """The gate logic ships as a composite action; the emitted workflow is a
    thin pin. No `git clone` of the toolkit may remain - a clone inside a run:
    step is invisible to Dependabot/Renovate, which was the defect this action
    exists to fix. Infra degrade now lives inside the action itself."""
    out = render_ci_workflow(plugin_version="1.23.0")
    assert "git clone" not in out
    assert "uses: bjcoombs/ai-native-toolkit@v1.23.0" in out
    assert "config: .assess/config.toml" in out
    # The Dependabot enablement snippet ships in the header comment.
    assert "package-ecosystem: github-actions" in out


def test_tool_install_failures_do_not_red_the_check():
    """Each install step degrades to reduced coverage, not a failed check."""
    out = render_ci_workflow(plugin_version="1.23.0", discovered_tools=ALL_EXTERNAL_TOOLS)
    installs = out.count("go install") + out.count("npm install")
    assert installs == len(ALL_EXTERNAL_TOOLS)
    # One continue-on-error per tool install; rg/uv degrade now lives inside
    # the composite action, not the emitted workflow.
    assert out.count("continue-on-error: true") == installs


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


def test_render_is_valid_yaml():
    yaml = pytest.importorskip("yaml")
    out = render_ci_workflow(plugin_version="1.23.0", discovered_tools=ALL_EXTERNAL_TOOLS)
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
