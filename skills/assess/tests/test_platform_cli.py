"""Tests for lib/platform_cli.py: how gh and glab are run (issue #714).

No prompt (stdin closed, prompt variables set), a bounded 429 backoff that
honours a printed Retry-After, and the per-run response cache. The CLIs are
shell stubs on PATH, so every test runs offline.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from lib import gh_cli, gitlab_cli, platform_cli
from lib.gh_cli import GhUnavailable
from lib.gitlab_cli import GitlabProject

STUB = """#!/bin/sh
echo "$*" >> "$STUB_DIR/log"
n=$(cat "$STUB_DIR/count" 2>/dev/null || echo 0); n=$((n+1)); echo $n > "$STUB_DIR/count"
if [ -f "$STUB_DIR/limited" ] && [ "$n" -le "$(cat "$STUB_DIR/limited")" ]; then
  cat "$STUB_DIR/message" >&2; exit 1
fi
echo '{"ok": true}'
"""


@pytest.fixture
def stub(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    bindir, data = tmp_path / "bin", tmp_path / "data"
    bindir.mkdir()
    data.mkdir()
    for tool in ("gh", "glab"):
        (bindir / tool).write_text(STUB, encoding="utf-8")
        (bindir / tool).chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("STUB_DIR", str(data))
    monkeypatch.delenv(platform_cli.CACHE_ENV, raising=False)
    return data


@pytest.fixture
def waits(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Every backoff wait, instead of sleeping it."""
    seen: list[float] = []
    monkeypatch.setattr(platform_cli, "SLEEP", seen.append)
    return seen


def _calls(data: Path) -> list[str]:
    log = data / "log"
    return log.read_text(encoding="utf-8").splitlines() if log.exists() else []


def _limit(data: Path, times: int, message: str) -> None:
    (data / "limited").write_text(str(times), encoding="utf-8")
    (data / "message").write_text(message + "\n", encoding="utf-8")


def test_every_call_closes_stdin_and_disables_prompts(monkeypatch):
    seen: dict[str, object] = {}

    def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        seen.update(kwargs)
        return subprocess.CompletedProcess(cmd, 0, "{}", "")

    monkeypatch.setattr(platform_cli.subprocess, "run", fake_run)
    monkeypatch.setenv("KEEP_ME", "1")
    assert platform_cli.run_cli(["gh", "api", "x"], 5) == platform_cli.CliResult(0, "{}", "")
    env = seen["env"]
    assert isinstance(env, dict)
    assert seen["stdin"] is subprocess.DEVNULL
    assert env["GH_PROMPT_DISABLED"] == "1" and env["NO_PROMPT"] == "1"
    assert env["GIT_TERMINAL_PROMPT"] == "0" and env["KEEP_ME"] == "1"
    assert seen["encoding"] == "utf-8" and seen["timeout"] == 5


def test_a_429_is_retried_after_the_printed_retry_after(stub, waits):
    _limit(stub, 1, "Retry-After: 7\ngh: rate limited (HTTP 429)")
    assert gh_cli.gh_api("repos/o/r") == {"ok": True}
    assert len(_calls(stub)) == 2
    assert waits == [7.0]


def test_without_retry_after_the_wait_doubles_and_is_capped(stub, waits):
    _limit(stub, 3, "glab: 429 Too Many Requests")
    project = GitlabProject(host="gitlab.example.com", path="g/app")
    assert gitlab_cli.glab_api(project, "projects/g%2Fapp") == {"ok": True}
    assert waits == [2.0, 4.0, 8.0]
    assert platform_cli.retry_wait("Retry-After: 9999", 0) == platform_cli.MAX_WAIT
    assert platform_cli.retry_wait("", 10) == platform_cli.MAX_WAIT


@pytest.mark.parametrize(("tool", "message", "call"), [
    ("gh", "gh: API rate limit exceeded (HTTP 429)", lambda: gh_cli.gh_api("repos/o/r")),
    ("glab", "glab: 429 Too Many Requests",
     lambda: gitlab_cli.glab_api(GitlabProject(host="gl.example", path="g/app"), "projects/x")),
])
def test_a_429_that_outlasts_the_retries_is_rate_limited_never_clean(stub, waits, tool, message, call):
    _limit(stub, 99, message)
    with pytest.raises(GhUnavailable) as caught:
        call()
    assert caught.value.reason.startswith("rate_limited:")
    assert len(_calls(stub)) == platform_cli.MAX_RETRIES + 1


def test_another_failure_is_not_retried(stub, waits):
    _limit(stub, 99, "gh: Not Found (HTTP 404)")
    with pytest.raises(GhUnavailable) as caught:
        gh_cli.gh_api("repos/o/r")
    assert caught.value.reason.startswith("not_found:")
    assert len(_calls(stub)) == 1
    assert waits == []


def test_the_cache_answers_a_repeated_call_and_stores_only_successes(stub, tmp_path, monkeypatch):
    cache = tmp_path / "cache"
    cache.mkdir()
    monkeypatch.setenv(platform_cli.CACHE_ENV, str(cache))
    _limit(stub, 1, "gh: Not Found (HTTP 404)")
    with pytest.raises(GhUnavailable):
        gh_cli.gh_api("users/someone%5Bbot%5D")
    assert list(cache.iterdir()) == []  # a failure is never cached
    assert gh_cli.gh_api("users/someone%5Bbot%5D") == {"ok": True}
    assert gh_cli.gh_api("users/someone%5Bbot%5D") == {"ok": True}
    assert len(_calls(stub)) == 2  # the third call came from the cache
    assert gh_cli.gh_api("users/other%5Bbot%5D") == {"ok": True}
    assert len(_calls(stub)) == 3
    assert len([p for p in cache.iterdir() if not p.name.startswith(".")]) == 2


def test_without_the_cache_variable_nothing_is_cached(stub):
    assert gh_cli.gh_api("repos/o/r") == {"ok": True}
    assert gh_cli.gh_api("repos/o/r") == {"ok": True}
    assert len(_calls(stub)) == 2


def test_an_unwritable_cache_is_skipped(stub, tmp_path, monkeypatch):
    monkeypatch.setenv(platform_cli.CACHE_ENV, str(tmp_path / "missing"))
    assert gh_cli.gh_api("repos/o/r") == {"ok": True}
    assert gh_cli.gh_api("repos/o/r") == {"ok": True}
    assert len(_calls(stub)) == 2
