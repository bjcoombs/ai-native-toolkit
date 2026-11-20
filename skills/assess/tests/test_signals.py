"""Tests for the headless signals entry point (issue #545)."""
from __future__ import annotations

import ast
import json
import os
import subprocess
from pathlib import Path

import jsonschema
import pytest

import assess_signals
from lib import scan_registry as reg
from lib import signals
from lib.scan_registry import ScanSpec
from lib.signals_schema import SCHEMA_PATH, build_schema, render, type_schema

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True,
    ).stdout


@pytest.fixture
def fixture_repo(tmp_path: Path) -> Path:
    """A committed repo carrying instruction files and agent config for the scans to read."""
    repo = tmp_path / "target"
    (repo / ".claude").mkdir(parents=True)
    (repo / "CLAUDE.md").write_text(
        "# Project\n\nRun `make check` before pushing.\n", encoding="utf-8")
    (repo / "AGENTS.md").write_text("# Agents\n\nUse `make check`.\n", encoding="utf-8")
    (repo / ".claude" / "settings.json").write_text(
        json.dumps({"permissions": {"allow": ["Bash(make check)"]}}), encoding="utf-8")
    (repo / "Makefile").write_text("check:\n\ttrue\n", encoding="utf-8")
    (repo / "pyproject.toml").write_text('[project]\nname = "x"\n', encoding="utf-8")
    _git(repo.parent, "init", "-q", str(repo))
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    # A GitHub remote: without one the gh_cli scans stop at no_remote before
    # calling gh, and the no_gh tripwire could not fire.
    _git(repo, "remote", "add", "origin", "https://github.com/example/example.git")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")
    return repo


@pytest.fixture
def no_gh(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A ``gh`` on PATH that records any call; the run must never make one."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    marker = tmp_path / "gh-was-called"
    gh = bin_dir / "gh"
    gh.write_text(f"#!/bin/sh\ntouch {marker}\nexit 1\n", encoding="utf-8")
    gh.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    return marker


def _run(repo: Path, out: Path, *extra: str) -> int:
    return assess_signals.main([str(repo), "--out", str(out), *extra])


def test_full_run_leaves_the_target_untouched_and_validates(fixture_repo, tmp_path, no_gh):
    out = tmp_path / "out" / "signals.json"
    assert _run(fixture_repo, out) == 0

    assert _git(fixture_repo, "status", "--porcelain", "--untracked-files=all") == ""
    assert not (fixture_repo / ".assess").exists()
    assert not no_gh.exists(), "the headless run called gh"

    doc = json.loads(out.read_text(encoding="utf-8"))
    jsonschema.validate(doc, json.loads(SCHEMA_PATH.read_text(encoding="utf-8")))

    stamp = doc["stamp"]
    assert stamp["target_head"] == _git(fixture_repo, "rev-parse", "HEAD").strip()
    assert stamp["target_dirty"] is False
    assert stamp["window"] == "6mo"
    assert stamp["plugin_version"] == signals.read_plugin_version()
    assert set(doc["scans"]) == set(signals.known_scans())


def test_network_scans_are_unavailable_and_agent_environment_runs_offline(fixture_repo, tmp_path, no_gh):
    out = tmp_path / "signals.json"
    assert _run(fixture_repo, out) == 0
    scans = json.loads(out.read_text(encoding="utf-8"))["scans"]

    for key in ("review_reality", "config_drift", "gate_cost_estimate"):
        assert scans[key] == {"available": False, "reason": signals.NETWORK_REASON}
    env = scans["agent_environment"]
    assert env["available"] is True
    assert env["ci_duration"]["available"] is False
    assert env["ci_duration"]["reason"].startswith("not_probed")
    for key in ("agent_ops", "claude_config", "nested_instructions", "agent_harness"):
        assert scans[key].get("available") is not False, key


def test_unregistered_scans_are_listed_unavailable(fixture_repo, tmp_path):
    out = tmp_path / "signals.json"
    assert _run(fixture_repo, out, "--scans", "doc_graph,agent_ops") == 0
    scans = json.loads(out.read_text(encoding="utf-8"))["scans"]
    assert list(scans) == ["agent_ops", "doc_graph"]
    assert scans["doc_graph"] == {"available": False, "reason": signals.UNREGISTERED_REASON}


def test_a_selected_scan_pulls_in_what_it_reads_but_reports_only_itself(fixture_repo, tmp_path):
    out = tmp_path / "signals.json"
    assert _run(fixture_repo, out, "--scans", "agent_environment", "--window", "90d") == 0
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert list(doc["scans"]) == ["agent_environment"]
    assert doc["scans"]["agent_environment"]["available"] is True
    assert doc["stamp"]["window"] == "90d"


def test_unknown_scan_exits_non_zero_and_names_it(fixture_repo, tmp_path, capsys):
    out = tmp_path / "signals.json"
    assert _run(fixture_repo, out, "--scans", "agent_ops,no_such_scan") == 2
    assert "no_such_scan" in capsys.readouterr().err
    assert not out.exists()


def test_bad_window_exits_non_zero(fixture_repo, tmp_path, capsys):
    assert _run(fixture_repo, tmp_path / "s.json", "--window", "six months") == 2
    assert "invalid window" in capsys.readouterr().err


def test_out_inside_the_target_is_refused(fixture_repo, capsys):
    out = fixture_repo / "signals.json"
    assert _run(fixture_repo, out) == 2
    assert "inside the target" in capsys.readouterr().err
    assert not out.exists()


def test_missing_target_is_a_usage_error(tmp_path):
    assert _run(tmp_path / "absent", tmp_path / "s.json") == 2


def test_a_scan_that_raises_is_unavailable_and_the_run_completes(fixture_repo):
    def boom(_root):
        raise RuntimeError("kaput")

    specs = (
        ScanSpec("broken", boom, ("repo_root",), reg.STAGE_READ_SIDE),
        ScanSpec("fine", lambda root: {"available": True}, ("repo_root",), reg.STAGE_READ_SIDE),
    )
    doc = signals.build_document(fixture_repo, specs=specs)
    assert doc["scans"]["broken"] == {"available": False, "reason": "broken scan failed: kaput"}
    assert doc["scans"]["fine"] == {"available": True}
    jsonschema.validate(doc, build_schema(specs))


def test_a_network_scan_never_calls_its_fn(fixture_repo):
    def online(_root):
        raise AssertionError("network scan was called")

    specs = (
        ScanSpec("online", online, ("repo_root",), reg.STAGE_READ_SIDE, network=True),
        ScanSpec("reader", lambda v: {"saw": v}, ("online",), reg.STAGE_READ_SIDE),
    )
    scans = signals.collect(fixture_repo, ["online", "reader"], specs)
    assert scans["online"] == {"available": False, "reason": signals.NETWORK_REASON}
    assert scans["reader"] == {"saw": scans["online"]}


def test_registry_migration_moves_a_name_from_unavailable_to_run(fixture_repo):
    """A hand-wired name that joins the registry runs without an edit to signals."""
    specs = (ScanSpec("doc_graph", lambda root: {"available": True}, ("repo_root",), reg.STAGE_READ_SIDE),)
    assert signals.known_scans(specs).count("doc_graph") == 1
    assert signals.collect(fixture_repo, ["doc_graph"], specs) == {"doc_graph": {"available": True}}


def test_unregistered_names_are_disjoint_from_the_reported_scans():
    """A scan reported headless must leave UNREGISTERED_SCANS in the same PR."""
    reported = {spec.key for spec in reg.SCANS if signals._reported(spec)}
    assert not set(signals.UNREGISTERED_SCANS) & reported


def test_the_core_entries_are_not_reported_as_scans():
    """#557 moved the core's blocks into the table; their provenance, scalar
    and list keys never become scan names."""
    known = set(signals.known_scans())
    assert not {"run_id", "plugin_version", "anomalies", "current", "run_header"} & known
    assert "doc_graph" in known and "doc_graph" in signals.UNREGISTERED_SCANS


def test_unregistered_names_are_run_context_keys_the_core_assigns():
    """Guards the names-only list against typos: each is a key assess_core writes."""
    sources = "".join(
        path.read_text(encoding="utf-8")
        for path in (SCRIPTS / "assess_core.py", SCRIPTS / "lib" / "context_blocks.py",
                     SCRIPTS / "lib" / "run_setup.py", SCRIPTS / "lib" / "scan_registry.py")
    )
    for name in signals.UNREGISTERED_SCANS:
        assert f'"{name}"' in sources, name


def _lib_imports(module: str) -> set[str]:
    """The ``lib.*`` modules ``module`` imports directly (a package: any of its files)."""
    base = SCRIPTS / "lib" / module
    files = sorted(base.glob("*.py")) if base.is_dir() else [base.with_suffix(".py")]
    found: set[str] = set()
    for path in files:
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom) and node.module == "lib":
                found.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and (node.module or "").startswith("lib."):
                found.add(node.module.split(".")[1])
            elif isinstance(node, ast.Import):
                found.update(a.name.split(".")[1] for a in node.names if a.name.startswith("lib."))
    return found


def _reaches_gh(module: str) -> bool:
    """True when ``module`` imports ``lib.gh_cli`` or ``lib.gitlab_cli`` (the platform
    clients), directly or through other lib modules."""
    seen: set[str] = set()
    pending = [module]
    while pending:
        current = pending.pop()
        if current in seen:
            continue
        seen.add(current)
        pending.extend(_lib_imports(current))
    return bool({"gh_cli", "gitlab_cli"} & seen)


def test_reaches_gh_counts_both_platform_clients():
    assert _reaches_gh("gitlab_cli")
    assert _reaches_gh("gh_cli")


def test_the_network_flag_marks_exactly_the_gh_scans():
    """A scan is ``network`` iff its module reaches a platform client
    (``lib.gh_cli`` or ``lib.gitlab_cli``, the only network clients)."""
    reaching = {
        spec.key for spec in reg.SCANS
        if _reaches_gh(spec.fn.__module__.removeprefix("lib."))
    }
    network = {spec.key for spec in reg.SCANS if spec.network}
    assert network == reaching
    assert {"review_reality", "config_drift", "gate_cost_estimate", "agent_environment"} <= network
    offline = {spec.key for spec in reg.SCANS if spec.offline_fn is not None}
    assert offline <= network


def test_the_window_reaches_a_scan_that_reads_history_window(fixture_repo):
    specs = (ScanSpec("hist", lambda window: {"saw": window}, ("history_window",), reg.STAGE_READ_SIDE),)
    inputs = signals.build_inputs(fixture_repo, "90d")
    reg.validate(specs, provided=inputs)
    assert set(inputs) >= set(reg.PROVIDED_INPUTS)
    doc = signals.build_document(fixture_repo, ["hist"], "90d", specs=specs)
    assert doc["scans"] == {"hist": {"saw": "90d"}}


def test_git_runs_with_optional_locks_off_and_the_env_is_restored(fixture_repo, monkeypatch):
    monkeypatch.setenv("GIT_OPTIONAL_LOCKS", "1")
    seen = {}

    def probe(_root):
        seen["locks"] = os.environ.get("GIT_OPTIONAL_LOCKS")
        return {"available": True}

    specs = (ScanSpec("probe", probe, ("repo_root",), reg.STAGE_READ_SIDE),)
    signals.build_document(fixture_repo, specs=specs)
    assert seen["locks"] == "0"
    assert os.environ["GIT_OPTIONAL_LOCKS"] == "1"

    monkeypatch.delenv("GIT_OPTIONAL_LOCKS")
    signals.build_document(fixture_repo, specs=specs)
    assert "GIT_OPTIONAL_LOCKS" not in os.environ


def test_out_replaces_a_hard_link_instead_of_writing_through_it(fixture_repo, tmp_path):
    inside = fixture_repo / "CLAUDE.md"
    before = inside.read_text(encoding="utf-8")
    out = tmp_path / "linked.json"
    os.link(inside, out)
    assert _run(fixture_repo, out, "--scans", "agent_ops") == 0
    assert inside.read_text(encoding="utf-8") == before
    assert json.loads(out.read_text(encoding="utf-8"))["scans"]["agent_ops"]
    assert [p.name for p in tmp_path.glob(".linked.json.*")] == []


def test_undecodable_config_degrades_to_defaults(fixture_repo, tmp_path):
    (fixture_repo / ".assess").mkdir()
    (fixture_repo / ".assess" / "config.toml").write_bytes(b"\xff\xfe[assess]\n")
    doc = signals.build_document(fixture_repo, ["agent_ops"])
    assert doc["scans"]["agent_ops"]


def test_window_parsing():
    for good in ("6mo", "90d", "12w", "1y"):
        assert signals.parse_window(good) == good
    for bad in ("0d", "6", "6m", "-1y", ""):
        with pytest.raises(ValueError):
            signals.parse_window(bad)


def test_stamp_ignores_an_enclosing_repository(fixture_repo):
    nested = fixture_repo / "sub"
    nested.mkdir()
    stamp = signals.build_stamp(nested, "6mo", toolkit_root=nested)
    assert stamp["target_head"] is None and stamp["toolkit_commit"] is None


def test_stamp_degrades_outside_git(tmp_path):
    stamp = signals.build_stamp(tmp_path, "6mo", toolkit_root=tmp_path)
    assert stamp["target_head"] is None and stamp["target_dirty"] is None
    assert stamp["toolkit_commit"] is None
    assert stamp["plugin_version"] == "unknown"


def test_checked_in_schema_matches_the_typed_dicts():
    assert SCHEMA_PATH.read_text(encoding="utf-8") == render(), (
        "references/signals.schema.json is stale: regenerate with "
        "`uv run --locked python scripts/lib/signals_schema.py` from skills/assess"
    )


def test_schema_rejects_an_unavailable_block_without_a_reason():
    doc = {
        "signals_schema_version": 1,
        "stamp": {"toolkit_commit": None, "plugin_version": "x", "scan_date": "2026-01-01",
                  "window": "6mo", "target_head": None, "target_dirty": None},
        "scans": {"agent_ops": {"available": False}},
    }
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(doc, build_schema())
    doc["scans"]["agent_ops"]["reason"] = "why"
    jsonschema.validate(doc, build_schema())


def test_schema_names_every_known_scan_and_rejects_others():
    names = build_schema()["properties"]["scans"]["propertyNames"]["enum"]
    assert names == sorted(signals.known_scans())
    doc = {
        "signals_schema_version": 1,
        "stamp": {"toolkit_commit": None, "plugin_version": "x", "scan_date": "2026-01-01",
                  "window": "6mo", "target_head": None, "target_dirty": None},
        "scans": {"no_such_scan": {}},
    }
    with pytest.raises(jsonschema.ValidationError, match="no_such_scan"):
        jsonschema.validate(doc, build_schema())


def test_type_schema_rejects_an_unmapped_annotation():
    with pytest.raises(TypeError):
        type_schema(float)
    assert type_schema(int | str) == {"type": ["integer", "string"]}


def test_churn_labels_normalise_to_the_window_grammar():
    from lib.git_churn import CHURN_WINDOWS, window_from_churn_label

    assert window_from_churn_label("commits (last 12mo)") == "12mo"
    assert window_from_churn_label("commits (last 5y)") == "5y"
    assert window_from_churn_label("commits (all-time)") is None
    assert window_from_churn_label(None) is None
    for label, _since in CHURN_WINDOWS:
        window = window_from_churn_label(f"commits ({label})")
        assert window is None or signals.parse_window(window) == window
