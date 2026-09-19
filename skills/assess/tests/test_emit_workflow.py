"""Tests for the emit-workflow CLI wrapper (assess_emit_workflow.py).

The wrapper derives sensible defaults (version from the running plugin, checked
against the published tags; branch from git; tools from PATH) so the
orchestrator can emit the frozen-harness workflow with a single argument. These
tests pin the default derivation, the tag check and arg parsing. Every network
call goes through ``assess_emit_workflow._run``, stubbed here with a fake
``gh`` / ``git ls-remote`` so no test touches the network.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

import assess_emit_workflow as emit
from assess_emit_workflow import main

RUNNING = "1.60.2"


def _write_ctx(tmp_path: Path, version: str | None) -> None:
    (tmp_path / ".assess").mkdir(parents=True, exist_ok=True)
    ctx = {"plugin_version": version} if version is not None else {}
    (tmp_path / ".assess" / "run-context.json").write_text(json.dumps(ctx))


def _workflow(tmp_path: Path) -> str:
    return (tmp_path / ".github" / "workflows" / "assess-gate.yml").read_text()


def _pins(text: str) -> set[str]:
    """Every toolkit version the workflow names (header comment and uses: line)."""
    return set(re.findall(r"ai-native-toolkit[ @]v([0-9][0-9.]*)", text))


def _fake_remote(tags: list[str] | None, with_action: set[str], *, gh_up: bool = True):
    """A stand-in for ``_run``: ``git ls-remote`` lists ``tags`` (``None`` = git
    offline); ``gh api .../contents/action.yml?ref=<tag>`` succeeds for tags in
    ``with_action`` and answers HTTP 404 otherwise (``gh_up=False`` = gh offline)."""

    def run(cmd: list[str]) -> tuple[int, str, str]:
        if cmd[0] == "git":
            if tags is None:
                return 128, "", "fatal: Could not resolve host: github.com"
            out = "".join(f"{i:040x}\trefs/tags/{t}\n" for i, t in enumerate(tags))
            return 0, out, ""
        if cmd[0] == "gh":
            if not gh_up:
                return 1, "", "error connecting to api.github.com"
            ref = cmd[-1].rsplit("ref=", 1)[-1]
            if ref in with_action:
                return 0, '{"path": "action.yml"}', ""
            return 1, "", f"gh: No commit found for the ref {ref} (HTTP 404)"
        raise AssertionError(f"unexpected command {cmd}")

    return run


@pytest.fixture
def running(monkeypatch):
    monkeypatch.setattr(emit, "_running_version", lambda: RUNNING)


def test_stale_run_context_ignored(tmp_path, monkeypatch, running):
    _write_ctx(tmp_path, "1.23.2")
    monkeypatch.setattr(emit, "_run", _fake_remote(["v1.23.0", f"v{RUNNING}"], {f"v{RUNNING}"}))
    assert main([str(tmp_path), "--branch", "main", "--tools", "lizard"]) == 0
    text = _workflow(tmp_path)
    assert _pins(text) == {RUNNING}
    assert f"uses: bjcoombs/ai-native-toolkit@v{RUNNING}" in text


def test_unpublished_version_falls_back(tmp_path, monkeypatch, running, capsys):
    # The running tag is unpublished and the newest tag ships no action.yml:
    # the generator walks down to the newest tag that does.
    tags = ["v1.23.0", "v1.41.0", "v1.57.0", "v1.58.2", "v1.59.0", "standalone-skills-v1.60.2"]
    monkeypatch.setattr(emit, "_run", _fake_remote(tags, {"v1.57.0", "v1.58.2"}))
    assert main([str(tmp_path), "--branch", "main", "--tools", "lizard"]) == 0
    assert _pins(_workflow(tmp_path)) == {"1.58.2"}
    err = capsys.readouterr().err
    assert "v1.58.2" in err and f"v{RUNNING}" in err


def test_unpublished_version_falls_back_without_gh(tmp_path, monkeypatch, running, capsys):
    # gh unreachable but git works: the newest tag at or after the first
    # release that shipped action.yml is chosen from the ls-remote list.
    tags = ["v1.23.0", "v1.58.2", "v1.41.0", "standalone-skills-v1.60.2"]
    monkeypatch.setattr(emit, "_run", _fake_remote(tags, set(), gh_up=False))
    assert main([str(tmp_path), "--branch", "main", "--tools", "lizard"]) == 0
    assert _pins(_workflow(tmp_path)) == {"1.58.2"}
    assert "v1.58.2" in capsys.readouterr().err


def test_published_version_pinned_without_gh(tmp_path, monkeypatch, running):
    monkeypatch.setattr(emit, "_run", _fake_remote(["v1.58.2", f"v{RUNNING}"], set(), gh_up=False))
    assert main([str(tmp_path), "--branch", "main", "--tools", "lizard"]) == 0
    assert _pins(_workflow(tmp_path)) == {RUNNING}


def test_offline_emits_unverified(tmp_path, monkeypatch, running, capsys):
    _write_ctx(tmp_path, "1.23.2")
    monkeypatch.setattr(emit, "_run", _fake_remote(None, set(), gh_up=False))
    assert main([str(tmp_path), "--branch", "main", "--tools", "lizard"]) == 0
    assert _pins(_workflow(tmp_path)) == {RUNNING}
    err = capsys.readouterr().err
    assert any("unverified" in line.lower() and f"v{RUNNING}" in line for line in err.splitlines())


def test_no_published_tag_with_action_keeps_running_and_warns(tmp_path, monkeypatch, running, capsys):
    monkeypatch.setattr(emit, "_run", _fake_remote(["v1.23.0"], set()))
    assert main([str(tmp_path), "--branch", "main", "--tools", "lizard"]) == 0
    assert _pins(_workflow(tmp_path)) == {RUNNING}
    assert "unverified" in capsys.readouterr().err.lower()


def test_running_version_reads_plugin_json():
    root = Path(emit.__file__).resolve().parents[3]
    expected = json.loads((root / ".claude-plugin" / "plugin.json").read_text())["version"]
    assert emit._running_version() == expected


def test_main_emits_with_explicit_flags(tmp_path, monkeypatch):
    # An explicit --version is the user's override: emitted as given, no lookup.
    def no_network(cmd):
        raise AssertionError(f"explicit --version must not hit the network: {cmd}")

    monkeypatch.setattr(emit, "_run", no_network)
    rc = main([str(tmp_path), "--version", "1.23.0", "--branch", "develop", "--tools", "lizard,scc"])
    assert rc == 0
    workflow = _workflow(tmp_path)
    assert _pins(workflow) == {"1.23.0"}
    assert "branches: [develop]" in workflow
    assert "Install scc" in workflow


def test_main_no_args_usage_error(capsys):
    assert main([]) == 2
    assert "Usage" in capsys.readouterr().err


def _pull_request(tmp_path: Path) -> dict:
    yaml = pytest.importorskip("yaml")
    doc = yaml.safe_load(_workflow(tmp_path))
    on = doc.get("on", doc.get(True))  # PyYAML reads a bare `on:` key as True
    return on["pull_request"]


_FLAGS = ["--version", "9.9.9", "--branch", "main", "--tools", "lizard"]
_NOTICE = "Applied the default paths-ignore"


def _existing_workflow(tmp_path: Path, body: str) -> None:
    wf = tmp_path / ".github" / "workflows"
    wf.mkdir(parents=True)
    (wf / "ci.yml").write_text(body)


_FILTERED = "on:\n  pull_request:\n    paths:\n      - src/**\n"


def test_main_paths_ignore_flag(tmp_path):
    assert main([str(tmp_path), *_FLAGS, "--paths-ignore", "**/*.md"]) == 0
    assert _pull_request(tmp_path) == {"branches": ["main"], "paths-ignore": ["**/*.md"]}


def test_main_paths_flag_repeatable(tmp_path):
    assert main([str(tmp_path), *_FLAGS, "--paths", "src/**", "--paths", "lib/**"]) == 0
    assert _pull_request(tmp_path) == {"branches": ["main"], "paths": ["src/**", "lib/**"]}


def test_main_paths_and_paths_ignore_together_is_usage_error(tmp_path, capsys):
    assert main([str(tmp_path), *_FLAGS, "--paths", "src/**", "--paths-ignore", "**/*.md"]) == 2
    assert "--paths" in capsys.readouterr().err
    assert not (tmp_path / ".github" / "workflows" / "assess-gate.yml").exists()


@pytest.mark.parametrize("body", [_FILTERED, "jobs:\n  t:\n    steps:\n      - uses: dorny/paths-filter@v3\n"])
def test_path_filter_default_applied(tmp_path, capsys, body):
    _existing_workflow(tmp_path, body)
    assert main([str(tmp_path), *_FLAGS]) == 0
    assert _pull_request(tmp_path) == {"branches": ["main"], "paths-ignore": ["**/*.md", ".assess/**"]}
    notice = [line for line in capsys.readouterr().err.splitlines() if _NOTICE in line]
    assert len(notice) == 1
    assert "**/*.md" in notice[0] and ".assess/" in notice[0] and "ci.yml" in notice[0]
    assert "lying_map" in notice[0]  # names what the default stops gating
    assert "required status check" in notice[0]  # a skipped PR reports no gate check


def test_path_filter_default_not_applied_without_filtered_workflow(tmp_path, capsys):
    _existing_workflow(tmp_path, "on:\n  pull_request:\njobs:\n  t:\n    steps:\n      - run: true\n")
    assert main([str(tmp_path), *_FLAGS]) == 0
    assert _pull_request(tmp_path) == {"branches": ["main"]}
    assert _NOTICE not in capsys.readouterr().err


def test_path_filter_default_not_applied_over_explicit_flag(tmp_path, capsys):
    _existing_workflow(tmp_path, _FILTERED)
    assert main([str(tmp_path), *_FLAGS, "--paths", "src/**"]) == 0
    assert _pull_request(tmp_path) == {"branches": ["main"], "paths": ["src/**"]}
    assert _NOTICE not in capsys.readouterr().err


@pytest.mark.parametrize("flag", ["--paths", "--paths-ignore", "--branch"])
def test_main_flag_without_value_is_usage_error(tmp_path, capsys, flag):
    _existing_workflow(tmp_path, _FILTERED)
    assert main([str(tmp_path), "--version", "9.9.9", "--tools", "lizard", flag]) == 2
    assert f"{flag} needs a value" in capsys.readouterr().err
    assert not (tmp_path / ".github" / "workflows" / "assess-gate.yml").exists()


def test_main_unquoted_glob_expansion_is_usage_error(tmp_path, capsys):
    # `--paths src/*` unquoted: the shell hands over src/a and src/b.
    assert main([str(tmp_path), *_FLAGS, "--paths", "src/a", "src/b"]) == 2
    assert "src/b" in capsys.readouterr().err
    assert not (tmp_path / ".github" / "workflows" / "assess-gate.yml").exists()
