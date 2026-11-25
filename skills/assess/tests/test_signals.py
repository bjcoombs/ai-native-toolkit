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
import lib.hosting.merged_sample as merged_sample
from lib import scan_registry as reg
from lib import signals
from lib.scan_registry import ScanSpec
from lib.signals_schema import SCHEMA_PATH, build_schema, render, type_schema

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
PLUGIN_JSON = SCRIPTS.parents[2] / ".claude-plugin" / "plugin.json"


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, encoding="utf-8", errors="surrogateescape", check=True,
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
    # A wrong Path(__file__).parents[N] reads ``unknown`` without raising (#917).
    assert stamp["plugin_version"] == json.loads(PLUGIN_JSON.read_text(encoding="utf-8"))["version"]
    assert stamp["plugin_version"] != "unknown"
    # Every known scan but the platform-only ones, absent without --platform (#779), and the
    # unit scans, absent without --units (#866).
    assert set(doc["scans"]) == (set(signals.known_scans()) - set(signals.PLATFORM_ONLY_SCANS)
                                 - set(signals.UNIT_SCANS))
    assert stamp["scope"] is None
    assert "delivery" not in doc["scans"] and "review_reality" in doc["scans"]


def test_platform_only_block_is_absent_without_platform(fixture_repo, no_gh):
    """``delivery`` is left out offline, even when named; never a placeholder (#779)."""
    assert signals.PLATFORM_ONLY_SCANS == ("delivery",)
    offline = signals.collect(fixture_repo, ["review_reality", "delivery"])
    assert set(offline) == {"review_reality"}
    assert offline["review_reality"] == {"available": False, "reason": signals.NETWORK_REASON}
    assert not no_gh.exists()


@pytest.mark.parametrize("remote", [None, "not a url"])
def test_review_blocks_read_no_remote_offline_without_a_remote(fixture_repo, no_gh, remote):
    """With no remote (or one neither parser reads) offline review reads ``no_remote``, as
    ``/assess`` and ``--platform`` do, not ``network`` (#908); a github remote keeps ``network``."""
    _git(fixture_repo, "remote", "remove", "origin")
    if remote is not None:
        _git(fixture_repo, "remote", "add", "origin", remote)
    offline = signals.collect(fixture_repo, ["review_reality", "review_evidence"])
    assert offline["review_reality"] == {"available": False, "reason": merged_sample.NO_REMOTE_REASON}
    assert offline["review_evidence"]["reason"] == merged_sample.NO_REMOTE_REASON
    assert not no_gh.exists()


def test_offline_merged_sample_reads_the_remote_only(fixture_repo, no_gh):
    assert merged_sample.offline_merged_sample(fixture_repo, "6mo", "2026-11-22") == {
        "available": False, "reason": signals.NETWORK_REASON}
    _git(fixture_repo, "remote", "set-url", "origin", "git@gitlab.example.com:team/app.git")
    assert merged_sample.offline_merged_sample(fixture_repo, None, "2026-11-22") == {
        "available": False, "reason": signals.NETWORK_REASON}
    _git(fixture_repo, "remote", "remove", "origin")
    assert merged_sample.offline_merged_sample(fixture_repo, None, "2026-11-22")["reason"].startswith(
        "no_remote: ")
    assert not no_gh.exists()


def test_scans_delivery_without_platform_exits_2_naming_platform(fixture_repo, tmp_path, no_gh, capsys):
    """An explicit platform-only scan offline would write ``{}`` with no reason (#779)."""
    out = tmp_path / "signals.json"
    assert _run(fixture_repo, out, "--scans", "agent_ops,delivery") == 2
    assert "--platform" in capsys.readouterr().err and not out.exists()
    assert signals.platform_only_error(["delivery"], platform=True) is None
    assert signals.platform_only_error(["agent_ops"], platform=False) is None
    assert signals.offline_scans("agent_ops, delivery") == "agent_ops"
    assert signals.offline_scans(None) is None


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
    assert _run(fixture_repo, out, "--scans", "dead_code,agent_ops") == 0
    scans = json.loads(out.read_text(encoding="utf-8"))["scans"]
    assert list(scans) == ["agent_ops", "dead_code"]
    assert scans["dead_code"] == {"available": False, "reason": signals.UNREGISTERED_REASON}


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


def test_platform_runs_only_the_listed_network_scans_and_what_they_read(fixture_repo, monkeypatch):
    """``--platform`` (#714): a listed network scan and a network sample it reads run; any
    other network scan still reports ``NETWORK_REASON``."""
    calls: list[str] = []

    def sample(_root):
        calls.append("sample")
        return {"merged": 3}

    def listed(seen):
        calls.append("listed")
        return {"available": True, "saw": seen}

    def other(_root):
        raise AssertionError("an unlisted network scan was called")

    specs = (
        ScanSpec("sample", sample, ("repo_root",), reg.STAGE_READ_SIDE, network=True, private=True),
        ScanSpec("listed", listed, ("sample",), reg.STAGE_READ_SIDE, network=True),
        ScanSpec("other", other, ("repo_root",), reg.STAGE_READ_SIDE, network=True),
    )
    monkeypatch.setattr(signals, "PLATFORM_SCANS", ("listed",))
    scans = signals.collect(fixture_repo, ["listed", "other"], specs, platform=True)
    assert scans["listed"] == {"available": True, "saw": {"merged": 3}}
    assert scans["other"] == {"available": False, "reason": signals.NETWORK_REASON}
    assert calls == ["sample", "listed"]
    offline = signals.collect(fixture_repo, ["listed"], specs)
    assert offline["listed"] == {"available": False, "reason": signals.NETWORK_REASON}


def test_network_reason_names_both_providers_and_keeps_its_code():
    assert signals.NETWORK_REASON.startswith("network: ")
    assert "gh" in signals.NETWORK_REASON and "glab" in signals.NETWORK_REASON


def test_registry_migration_moves_a_name_from_unavailable_to_run(fixture_repo):
    """A hand-wired name that joins the registry runs without an edit to signals."""
    specs = (ScanSpec("doc_graph", lambda root: {"available": True}, ("repo_root",), reg.STAGE_READ_SIDE),)
    assert signals.known_scans(specs).count("doc_graph") == 1
    assert signals.collect(fixture_repo, ["doc_graph"], specs) == {"doc_graph": {"available": True}}


def test_unregistered_names_are_disjoint_from_the_reported_scans():
    """A scan reported headless must leave UNREGISTERED_SCANS in the same PR."""
    reported = {spec.key for spec in signals.SIGNAL_SCANS if signals._reported(spec)}
    assert not set(signals.UNREGISTERED_SCANS) & reported


def test_the_core_entries_are_not_reported_as_scans():
    """#557 moved the core's blocks into the table; their provenance, scalar
    and list keys never become scan names."""
    known = set(signals.known_scans())
    assert not {"run_id", "plugin_version", "anomalies", "current", "run_header"} & known
    assert "dead_code" in known and "dead_code" in signals.UNREGISTERED_SCANS


def test_unregistered_names_are_run_context_keys_the_core_assigns():
    """Guards the names-only list against typos: each is a key assess_core writes."""
    sources = "".join(
        path.read_text(encoding="utf-8")
        for path in (SCRIPTS / "assess_core.py", SCRIPTS / "lib" / "context_blocks.py",
                     SCRIPTS / "lib" / "run_setup.py", SCRIPTS / "lib" / "scan_registry.py")
    )
    for name in signals.UNREGISTERED_SCANS:
        assert f'"{name}"' in sources, name


def _lib_module(dotted: str) -> str:
    """``dotted`` relative to ``lib``, cut back to the deepest part that is a module
    or package on disk: ``lib.hosting.gh_cli.fetch`` names ``hosting.gh_cli``."""
    parts = dotted.split(".")[1:]
    while len(parts) > 1 and not _lib_path(".".join(parts)).exists():
        parts.pop()
    return ".".join(parts)


def _in_lib(dotted: str) -> bool:
    return dotted == "lib" or dotted.startswith("lib.")


def _lib_path(module: str) -> Path:
    base = SCRIPTS.joinpath("lib", *module.split("."))
    return base if base.is_dir() else base.with_suffix(".py")


def _absolute_from(path: Path, node: ast.ImportFrom) -> str:
    """The absolute module a ``from ... import`` in ``path`` reads from: a relative
    import (``from .gh_cli import x`` in ``lib/hosting/scan.py``) resolves against
    the importing file's package (``lib.hosting.gh_cli``)."""
    if node.level == 0:
        return node.module or ""
    package = list(path.relative_to(SCRIPTS).parent.parts)
    base = package[:len(package) - (node.level - 1)]
    return ".".join(base + ([node.module] if node.module else []))


def _lib_imports(module: str) -> set[str]:
    """The ``lib.*`` modules ``module`` imports directly, as dotted names relative to
    ``lib`` (``hosting.gh_cli``); a package stands for any of its files."""
    base = _lib_path(module)
    files = sorted(base.glob("*.py")) if base.is_dir() else [base]
    found: set[str] = set()
    for path in files:
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom) and _in_lib(source := _absolute_from(path, node)):
                found.update(_lib_module(f"{source}.{alias.name}") for alias in node.names)
            elif isinstance(node, ast.Import):
                found.update(_lib_module(a.name) for a in node.names if _in_lib(a.name))
    return found - {""}


def _reaches_gh(module: str) -> bool:
    """True when ``module`` imports ``lib.hosting.gh_cli`` or ``lib.hosting.gitlab_cli`` (the platform
    clients), directly or through other lib modules, at any package depth."""
    seen: set[str] = set()
    pending = [module]
    while pending:
        current = pending.pop()
        if current in seen:
            continue
        seen.add(current)
        pending.extend(_lib_imports(current))
    return bool({"gh_cli", "gitlab_cli"} & {m.rsplit(".", 1)[-1] for m in seen})


def test_reaches_gh_counts_both_platform_clients():
    assert _reaches_gh("hosting.gitlab_cli")
    assert _reaches_gh("hosting.gh_cli")
    # A dotted import names the module, not its package (#917 moves the clients
    # into lib/hosting/): an attribute path is cut back to the module on disk.
    assert _lib_module("lib.hosting.gh_cli._run_gh") == "hosting.gh_cli"
    assert _lib_module("lib.test_pressure.mutation") == "test_pressure.mutation"
    assert _lib_module("lib.test_pressure.scan_test_pressure") == "test_pressure"
    assert "test_pressure" in _lib_imports("core_scans")
    # A relative import is an edge too: ``from .aggregate import ...`` in
    # lib/test_pressure/__init__.py reaches lib.test_pressure.aggregate.
    assert "test_pressure.aggregate" in _lib_imports("test_pressure")


def test_the_network_flag_marks_exactly_the_gh_scans():
    """A scan is ``network`` iff its module reaches a platform client
    (``lib.hosting.gh_cli`` or ``lib.hosting.gitlab_cli``, the only network clients)."""
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
                  "window": "6mo", "target_head": None, "target_dirty": None, "scope": None,
                  "managed_settings": False},
        "scans": {"agent_ops": {"available": False}},
    }
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(doc, build_schema())
    doc["scans"]["agent_ops"]["reason"] = "why"
    jsonschema.validate(doc, build_schema())


def test_schema_names_every_known_scan_and_rejects_others():
    names = build_schema()["properties"]["scans"]["propertyNames"]["enum"]
    assert names == sorted({*signals.known_scans(), *signals.PACK_BLOCKS})  # --pack's block too (#1276)
    doc = {
        "signals_schema_version": 1,
        "stamp": {"toolkit_commit": None, "plugin_version": "x", "scan_date": "2026-01-01",
                  "window": "6mo", "target_head": None, "target_dirty": None, "scope": None,
                  "managed_settings": False},
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
