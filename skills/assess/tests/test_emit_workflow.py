"""Tests for the emit-workflow CLI wrapper (assess_emit_workflow.py).

The wrapper derives sensible defaults (version from run-context, branch from
git, tools from PATH) so the orchestrator can emit the frozen-harness workflow
with a single argument. These tests pin the default derivation and arg parsing.
"""
from __future__ import annotations

import json
from pathlib import Path

from assess_emit_workflow import _plugin_version, main


def _write_ctx(tmp_path: Path, version: str | None) -> None:
    (tmp_path / ".assess").mkdir(parents=True, exist_ok=True)
    ctx = {"plugin_version": version} if version is not None else {}
    (tmp_path / ".assess" / "run-context.json").write_text(json.dumps(ctx))


def test_plugin_version_from_context(tmp_path):
    _write_ctx(tmp_path, "1.23.0")
    assert _plugin_version(tmp_path) == "1.23.0"


def test_plugin_version_missing_falls_back(tmp_path):
    assert _plugin_version(tmp_path) == "latest"


def test_main_emits_with_explicit_flags(tmp_path):
    _write_ctx(tmp_path, "1.23.0")
    rc = main([str(tmp_path), "--version", "1.23.0", "--branch", "develop", "--tools", "lizard,scc"])
    assert rc == 0
    workflow = (tmp_path / ".github" / "workflows" / "assess-gate.yml").read_text()
    assert "v1.23.0" in workflow
    assert "branches: [develop]" in workflow
    assert "Install scc" in workflow


def test_main_derives_version_from_context(tmp_path):
    _write_ctx(tmp_path, "9.9.9")
    rc = main([str(tmp_path), "--branch", "main", "--tools", "lizard"])
    assert rc == 0
    assert "v9.9.9" in (tmp_path / ".github" / "workflows" / "assess-gate.yml").read_text()


def test_main_no_args_usage_error(capsys):
    assert main([]) == 2
    assert "Usage" in capsys.readouterr().err
