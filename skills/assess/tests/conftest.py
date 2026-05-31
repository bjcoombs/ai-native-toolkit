"""Shared pytest fixtures.

Hermetic git. The fixtures below build throwaway git repos and assert on
commit *metadata* (authorship, dates, coupling), never on signatures. A git
subprocess inherits the ambient global/system config, and some environments
enable commit signing there (``commit.gpgsign=true`` with an SSH/GPG signing
program, e.g. Claude Code's web sandbox). Signing then fails inside the
disposable test repos and ``git commit`` exits 128 — breaking the whole
git-backed suite in any signing environment while CI (which doesn't sign)
stays green. We neutralise ambient git config for the whole test process at
import time (before any fixture builds a repo) by pointing GIT_CONFIG_GLOBAL
/ GIT_CONFIG_SYSTEM at the null device. Each repo still sets its own local
identity, so commits resolve an author/committer with no global config.
"""
from __future__ import annotations

import datetime as _dt
import os
import subprocess
from pathlib import Path

import pytest

# Applied at import time so it is in effect before any module/session-scoped
# fixture creates a repo. See the module docstring for the why.
os.environ["GIT_CONFIG_GLOBAL"] = os.devnull
os.environ["GIT_CONFIG_SYSTEM"] = os.devnull
os.environ["GIT_CONFIG_NOSYSTEM"] = "1"


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
