"""``prior-level.json`` and the run context's ``prior_level`` (``lib/prior_level.py``, #940)."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest

from lib.level_tally import default_model, level_result
from lib.prior_level import (
    PRIOR_LEVEL_FILE,
    PRIOR_LEVEL_VERSION,
    as_prior_level,
    build_prior_level,
    load_prior_level,
    same_model,
    write_prior_level,
)


def _level() -> dict[str, Any]:
    return dict(level_result(default_model(), {}))


def test_no_file_reads_none(tmp_path: Path) -> None:
    assert load_prior_level(tmp_path) is None


def test_write_then_load_round_trips_the_tags_and_model_version(tmp_path: Path) -> None:
    level = _level()
    path = write_prior_level(tmp_path, level)
    assert path == tmp_path / PRIOR_LEVEL_FILE
    prior = load_prior_level(tmp_path)
    assert prior == {"prior_level_version": PRIOR_LEVEL_VERSION, "levels_version": level["levels_version"],
                     "tags": level["tags"]}
    assert same_model(prior, level)
    assert not same_model(prior, {**level, "levels_version": "other"})
    assert not same_model(None, level)
    first = path.read_bytes()
    write_prior_level(tmp_path, level)
    assert path.read_bytes() == first and b"\r\n" not in first


@pytest.mark.parametrize("content", [
    "not json",
    "[]",
    json.dumps({"prior_level_version": 99, "levels_version": "m", "tags": []}),
    json.dumps({"prior_level_version": 1, "levels_version": 7, "tags": []}),
    json.dumps({"prior_level_version": 1, "levels_version": "m", "tags": [{"tag": "nope"}]}),
    json.dumps({"prior_level_version": 1, "levels_version": "m", "tags": [
        {"tag": "docs", "conditions": 1, "counted": 1, "met": -1, "measured": 1, "not_measured": []}]}),
    json.dumps({"prior_level_version": 1, "levels_version": "m", "tags": [
        {"tag": "docs", "conditions": 1, "counted": 1, "met": True, "measured": 1, "not_measured": []}]}),
    json.dumps({"prior_level_version": 1, "levels_version": "m", "tags": [
        {"tag": "docs", "conditions": 1, "counted": 1, "met": 0, "measured": 1, "not_measured": [{"id": 1}]}]}),
])
def test_a_malformed_file_reads_none(tmp_path: Path, content: str) -> None:
    (tmp_path / PRIOR_LEVEL_FILE).write_text(content, encoding="utf-8")
    assert load_prior_level(tmp_path) is None


def test_a_level_without_tags_writes_nothing_and_removes_a_stale_file(tmp_path: Path) -> None:
    write_prior_level(tmp_path, _level())
    assert write_prior_level(tmp_path, {"available": False, "reason": "x"}) is None
    assert not (tmp_path / PRIOR_LEVEL_FILE).exists()
    assert build_prior_level({**_level(), "levels_version": None}) is None
    assert build_prior_level(None) is None


def test_as_prior_level_keeps_a_valid_value_only() -> None:
    prior = build_prior_level(_level())
    assert as_prior_level(prior) == prior
    assert as_prior_level(None) is None


def test_counts_only_keeps_every_count_and_token() -> None:
    from lib.counts_only import counts_only

    prior = build_prior_level(_level())
    assert counts_only({"prior_level": prior})["prior_level"] == prior  # fails if a field is dropped


# --- through a real run ------------------------------------------------------

REPO = {
    "src/a.py": "def f():\n    return 1\n",
    "CLAUDE.md": "# Project\n\nRun `make test` before every commit.\n",
}


def _git(repo: Path, *args: str) -> None:
    env = {**os.environ, "GIT_AUTHOR_DATE": "2026-11-01T10:00:00Z", "GIT_COMMITTER_DATE": "2026-11-01T10:00:00Z"}
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, env=env)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    for rel, text in REPO.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8", newline="\n")
    _git(root, "add", "-A")
    _git(root, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-qm", "one")
    return root


def test_a_run_reads_the_earlier_file_before_writing_its_own(repo: Path) -> None:
    from assess_core import build_and_publish_run

    first = build_and_publish_run(repo_root=repo, run_date="2026-11-22", non_interactive=True)
    assert first["prior_level"] is None
    stored = json.loads((repo / ".assess" / PRIOR_LEVEL_FILE).read_text(encoding="utf-8"))
    assert stored["tags"] == first["level"]["tags"]
    strip = (repo / ".assess" / "tag-profile.svg").read_text(encoding="utf-8")
    assert 'data-mark="prior"' not in strip and "No earlier run is recorded here" in strip

    # A hand-edited earlier file: the second run draws it, so it must have read it before writing.
    stored["tags"] = [{**row, "met": 0, "measured": 1} if row["tag"] == "agent_context" else row
                      for row in stored["tags"]]
    (repo / ".assess" / PRIOR_LEVEL_FILE).write_text(json.dumps(stored), encoding="utf-8")
    second = build_and_publish_run(repo_root=repo, run_date="2026-11-23", non_interactive=True)
    assert second["prior_level"] == stored
    strip = (repo / ".assess" / "tag-profile.svg").read_text(encoding="utf-8")
    assert strip.count('data-mark="prior"') == sum(row["measured"] > 0 for row in stored["tags"])
    assert '<g data-tag="agent_context"' in strip
    agent_row = strip.split('<g data-tag="agent_context"', 1)[1].split("</g>", 1)[0]
    assert 'data-mark="prior"' in agent_row
    after = json.loads((repo / ".assess" / PRIOR_LEVEL_FILE).read_text(encoding="utf-8"))
    assert after["tags"] == second["level"]["tags"]
    ctx = json.loads((repo / ".assess" / "run-context.json").read_text(encoding="utf-8"))
    assert ctx["prior_level"] == stored
