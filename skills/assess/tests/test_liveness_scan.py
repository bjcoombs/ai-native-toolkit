"""Tests for Layer 1 liveness inputs: dead-code tier + observability rungs."""
from __future__ import annotations

import subprocess
from pathlib import Path

import lib.liveness_scan as liveness
from lib.liveness_scan import (
    _parse_deadcode,
    _parse_staticcheck,
    _parse_ts_prune,
    _parse_vulture,
    scan_dead_code,
    scan_observability,
)


def _write(root: Path, rel: str, text: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


# ── dead-code parsers ──────────────────────────────────────────────────────

def test_parse_vulture() -> None:
    out = (
        "src/foo.py:12: unused function 'bar' (60% confidence)\n"
        "src/foo.py:3: unused import 'os' (90% confidence)\n"
    )
    parsed = _parse_vulture(out)
    assert len(parsed) == 2
    assert parsed[0]["symbol"] == "bar"
    assert parsed[0]["line"] == 12


def test_parse_ts_prune_skips_used_in_module() -> None:
    out = "src/a.ts:4 - unusedThing\nsrc/b.ts:9 - helper (used in module)\n"
    parsed = _parse_ts_prune(out)
    assert len(parsed) == 1
    assert parsed[0]["symbol"] == "unusedThing"


def test_parse_staticcheck_and_deadcode() -> None:
    sc = "pkg/x.go:10:2: func unusedHelper is unused (U1000)\n"
    assert _parse_staticcheck(sc)[0]["line"] == 10
    dc = "pkg/y.go:5:1: unreachable func: deadOne\n"
    assert "unreachable" in _parse_deadcode(dc)[0]["kind"]


# ── dead-code scan plumbing ────────────────────────────────────────────────

def test_dead_code_degrades_when_tool_absent(tmp_path: Path, monkeypatch) -> None:
    _write(tmp_path, "foo.py", "x = 1")
    monkeypatch.setattr(liveness.shutil, "which", lambda _tool: None)
    r = scan_dead_code(tmp_path).as_dict()
    assert r["available"] is False
    assert r["candidate_count"] == 0
    assert any(t["status"] == "tool_absent" for t in r["tools"])
    assert "external consumer" in r["caveat"]  # the hard-limit caveat is present


def test_dead_code_runs_and_parses(tmp_path: Path, monkeypatch) -> None:
    _write(tmp_path, "foo.py", "def unused(): pass")
    monkeypatch.setattr(liveness.shutil, "which", lambda _tool: "/usr/bin/" + _tool)

    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(
            cmd, 3, stdout="foo.py:1: unused function 'unused' (60% confidence)\n",
            stderr="",
        )

    monkeypatch.setattr(liveness.subprocess, "run", fake_run)
    r = scan_dead_code(tmp_path).as_dict()
    assert r["available"] is True
    assert r["candidate_count"] == 1
    assert r["candidates"][0]["symbol"] == "unused"


def test_dead_code_timeout_degrades(tmp_path: Path, monkeypatch) -> None:
    _write(tmp_path, "foo.py", "x = 1")
    monkeypatch.setattr(liveness.shutil, "which", lambda _tool: "/usr/bin/" + _tool)

    def fake_run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, 60)

    monkeypatch.setattr(liveness.subprocess, "run", fake_run)
    r = scan_dead_code(tmp_path).as_dict()
    assert any(t["status"] == "timeout" for t in r["tools"])
    assert r["candidate_count"] == 0  # no crash


def test_dead_code_no_languages(tmp_path: Path) -> None:
    _write(tmp_path, "README.md", "just docs")
    r = scan_dead_code(tmp_path).as_dict()
    assert r["available"] is False
    assert r["candidate_count"] == 0


# ── observability rungs ────────────────────────────────────────────────────

def test_observability_none(tmp_path: Path) -> None:
    _write(tmp_path, "README.md", "nothing runtime here")
    assert scan_observability(tmp_path).rung == 0


def test_instrumented_but_human_only_is_rung_2(tmp_path: Path) -> None:
    """The meridian case: telemetry + a prose runbook, but no agent-invokable path."""
    _write(tmp_path, "package.json", '{"dependencies":{"prom-client":"1","winston":"3"}}')
    _write(tmp_path, "OBSERVABILITY.md",
           "Dashboards live in Grafana. SLOs are tracked by the platform team.")
    r = scan_observability(tmp_path).as_dict()
    assert r["rung"] == 2
    assert r["instrumented"]["present"] is True
    assert r["discoverable"]["present"] is True
    assert r["reachable"]["present"] is False


def test_agent_queryable_is_rung_3_via_mcp_and_runbook(tmp_path: Path) -> None:
    _write(tmp_path, "pyproject.toml", '[project]\ndependencies = ["opentelemetry-sdk"]')
    _write(tmp_path, ".mcp.json", '{"mcpServers":{"loki-logs":{"command":"loki-mcp"}}}')
    _write(tmp_path, "runbooks/oncall.md",
           "Query logs:\n```bash\nkubectl logs deploy/api\n```\n")
    r = scan_observability(tmp_path).as_dict()
    assert r["rung"] == 3
    assert r["reachable"]["present"] is True
    # both the MCP server and the runnable runbook are recorded
    assert len(r["reachable"]["signals"]) >= 2


def test_prose_mention_of_grafana_does_not_reach_rung_3(tmp_path: Path) -> None:
    """A runbook that merely *names* a dashboard tool isn't agent-reachable."""
    _write(tmp_path, "go.mod", "require go.opentelemetry.io/otel v1.0.0")
    _write(tmp_path, "runbooks/notes.md",
           "We use Grafana and Datadog. Ask the on-call engineer for access.")
    r = scan_observability(tmp_path)
    assert r.rung == 2  # discoverable, not reachable - no runnable command in a fence


def test_repo_skill_for_logs_is_reachable(tmp_path: Path) -> None:
    _write(tmp_path, "requirements.txt", "structlog\n")
    _write(tmp_path, ".claude/skills/tail-logs/SKILL.md", "# tail logs")
    r = scan_observability(tmp_path)
    assert r.rung == 3
    assert any("repo skill" in s for s in r.reachable)


def test_overloaded_token_names_do_not_reach_rung_3(tmp_path: Path) -> None:
    """changelog/blog/login etc. must not be mistaken for telemetry channels."""
    _write(tmp_path, "requirements.txt", "structlog\n")  # rung 1 instrumentation
    _write(tmp_path, ".mcp.json", '{"mcpServers":{"changelog-bot":{"command":"x"},"login":{"command":"y"}}}')
    _write(tmp_path, "skills/blog-publisher/SKILL.md", "# blog")
    _write(tmp_path, "skills/catalog-search/SKILL.md", "# catalog")
    r = scan_observability(tmp_path)
    assert r.reachable == []      # no genuine log/metric/trace tool
    assert r.rung == 1            # instrumented only


def test_single_body_token_does_not_reach_discoverable(tmp_path: Path) -> None:
    """A lone product 'dashboard'/'alerting' mention isn't an observability doc."""
    _write(tmp_path, "features.md", "Our product dashboard shows charts to users.")
    _write(tmp_path, "ops.md", "We have alerting in the UI.")
    assert scan_observability(tmp_path).rung == 0


def test_two_body_tokens_reach_discoverable(tmp_path: Path) -> None:
    _write(tmp_path, "ops.md",
           "We watch SLOs in Grafana and page via alerting when budgets burn.")
    r = scan_observability(tmp_path)
    assert r.rung == 2
    assert r.discoverable


# ── read-only: build-mutating dead-code tools are gated ────────────────────

def test_build_tools_not_run_by_default(tmp_path: Path, monkeypatch) -> None:
    """Go tools (deadcode/staticcheck) compile the project, so a read-only run
    reports them available-but-not-run rather than executing them."""
    _write(tmp_path, "main.go", "package main\nfunc main(){}")
    monkeypatch.setattr(liveness.shutil, "which", lambda t: "/usr/bin/" + t)

    def fake_run(cmd, **kwargs):
        raise AssertionError("a build tool was executed in a read-only scan")

    monkeypatch.setattr(liveness.subprocess, "run", fake_run)
    r = scan_dead_code(tmp_path).as_dict()  # run_build_tools defaults False
    deadcode = next(t for t in r["tools"] if t["tool"] == "deadcode")
    assert deadcode["status"] == "available_not_run"
    assert "build" in deadcode["reason"].lower()
    assert r["candidate_count"] == 0


def test_build_tools_run_when_opted_in(tmp_path: Path, monkeypatch) -> None:
    _write(tmp_path, "main.go", "package main\nfunc main(){}")
    monkeypatch.setattr(liveness.shutil, "which", lambda t: "/usr/bin/" + t)

    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(
            cmd, 0, stdout="main.go:9:1: unreachable func: helper\n", stderr="")

    monkeypatch.setattr(liveness.subprocess, "run", fake_run)
    r = scan_dead_code(tmp_path, run_build_tools=True).as_dict()
    assert r["available"] is True
    assert r["candidate_count"] == 1


def test_vendored_dead_code_is_filtered(tmp_path: Path, monkeypatch) -> None:
    """Candidates under .venv/node_modules are dropped so the cap stays about THIS repo."""
    _write(tmp_path, "app.py", "x = 1")
    monkeypatch.setattr(liveness.shutil, "which", lambda t: "/usr/bin/" + t)

    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 3, stderr="", stdout=(
            ".venv/lib/dep.py:5: unused function 'vendored' (60% confidence)\n"
            "app.py:1: unused function 'mine' (60% confidence)\n"))

    monkeypatch.setattr(liveness.subprocess, "run", fake_run)
    r = scan_dead_code(tmp_path).as_dict()
    paths = {c["path"] for c in r["candidates"]}
    assert paths == {"app.py"}  # vendored candidate dropped
