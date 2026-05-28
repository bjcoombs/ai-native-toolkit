"""Shared pytest fixtures."""
from __future__ import annotations

import datetime as _dt
import os
import subprocess
from pathlib import Path

import pytest


@pytest.fixture
def fixtures_dir() -> Path:
    """Path to tests/fixtures/."""
    return Path(__file__).parent / "fixtures"


@pytest.fixture
def tmp_assess_dir(tmp_path: Path) -> Path:
    """A clean .assess/ directory in a temp location."""
    assess_dir = tmp_path / ".assess"
    assess_dir.mkdir()
    (assess_dir / "hotspots").mkdir()
    return assess_dir


def _git(repo: Path, *args: str, env: dict | None = None) -> None:
    full_env = {**os.environ, **(env or {})}
    subprocess.run(["git", "-C", str(repo), *args],
                   check=True, capture_output=True, text=True, env=full_env)


@pytest.fixture
def git_repo(tmp_path: Path):
    """Create an initialised git repo and return (repo_path, commit_fn).

    commit_fn(message, days_ago=None) stages everything and commits; pass an
    integer `days_ago` to backdate both author and committer time, which lets a
    test simulate a stale doc beside churning code. Git's date env vars reject
    relative strings ("500 days ago"), so we convert to a strict ISO timestamp.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")

    def commit(message: str, days_ago: int | None = None) -> None:
        _git(repo, "add", "-A")
        env = {}
        if days_ago is not None:
            when = _dt.datetime.now() - _dt.timedelta(days=days_ago)
            stamp = when.strftime("%Y-%m-%dT%H:%M:%S")
            env = {"GIT_AUTHOR_DATE": stamp, "GIT_COMMITTER_DATE": stamp}
        _git(repo, "commit", "-q", "-m", message, env=env)

    return repo, commit
