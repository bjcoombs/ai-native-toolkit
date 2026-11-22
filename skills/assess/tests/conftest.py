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
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

# Applied at import time so it is in effect before any module/session-scoped
# fixture creates a repo. See the module docstring for the why.
os.environ["GIT_CONFIG_GLOBAL"] = os.devnull
os.environ["GIT_CONFIG_SYSTEM"] = os.devnull
os.environ["GIT_CONFIG_NOSYSTEM"] = "1"
# The fleet runner keeps each run under ${XDG_DATA_HOME}/ai-native-toolkit/fleet
# by default (#552): point it at a throwaway directory so no test writes into
# the real ~/.local/share.
_XDG_DATA_HOME = tempfile.mkdtemp(prefix="assess-tests-xdg-")
os.environ["XDG_DATA_HOME"] = _XDG_DATA_HOME


# Windows skip markers (#713). The windows-latest job runs this suite; a test
# that needs POSIX-only machinery carries one of these markers and is skipped
# there with the marker's reason, so the skip list reads as a category, not a
# scatter of ad-hoc skipif lines. Anything else that fails on Windows is a bug.
WINDOWS_SKIP_MARKERS = {
    "needs_symlink": "symlink creation is not reliable on Windows",
    "needs_posix_perms": "chmod/getuid permission semantics are POSIX-only",
    "needs_posix_shell": "runs a #!/bin/sh stub or sh -c, which Windows cannot execute",
    # Not a POSIX-only test: a known Windows failure awaiting its fix.
    "windows_followup": "known Windows failure, tracked in "
                        "https://github.com/bjcoombs/ai-native-toolkit/issues/762",
}


# windows_followup by node id, added while test_complexity_treemap.py sat at its
# file-size ceiling (since split, #720).
WINDOWS_FOLLOWUP_NODES = {
    "tests/test_complexity_treemap.py::test_write_svg_token_tooltip_and_label_pluralise",
}


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Skip the POSIX-only marked tests on Windows."""
    if sys.platform != "win32":
        return
    for item in items:
        if item.nodeid.split("[", 1)[0] in WINDOWS_FOLLOWUP_NODES:
            item.add_marker(pytest.mark.windows_followup)
        for name, reason in WINDOWS_SKIP_MARKERS.items():
            if item.get_closest_marker(name):
                item.add_marker(pytest.mark.skip(reason=f"Windows: {reason}"))
                break


def skip_without_posix_shell() -> None:
    """Call first in a fixture that puts a ``#!/bin/sh`` stub (fake gh/glab) on PATH.

    The fixture-level form of ``needs_posix_shell``: Windows cannot execute the
    stub, so every test using the fixture skips with the same reason.
    """
    if sys.platform == "win32":
        pytest.skip(f"Windows: {WINDOWS_SKIP_MARKERS['needs_posix_shell']}")


@pytest.fixture(scope="session", autouse=True)
def _remove_xdg_data_home() -> Iterator[None]:
    """Delete the redirected XDG_DATA_HOME after the last test; the redirect stays for the session."""
    yield
    shutil.rmtree(_XDG_DATA_HOME, ignore_errors=True)


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

    commit_fn(message, days_ago=None, committer_days_ago=None) stages everything
    and commits; pass an integer `days_ago` to backdate both author and committer
    time, which lets a test simulate a stale doc beside churning code. Pass
    `committer_days_ago` to backdate the committer time independently of the
    author time - a rebase/cherry-pick keeps the original author time but stamps a
    fresh committer time, so this lets a test prove staleness reads from author
    time (`%at`), not committer time (`%ct`). Git's date env vars reject relative
    strings ("500 days ago"), so we convert to a strict ISO timestamp.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")

    def _stamp(days_ago: int) -> str:
        when = _dt.datetime.now() - _dt.timedelta(days=days_ago)
        return when.strftime("%Y-%m-%dT%H:%M:%S")

    def commit(
        message: str,
        days_ago: int | None = None,
        committer_days_ago: int | None = None,
    ) -> None:
        _git(repo, "add", "-A")
        env = {}
        if days_ago is not None:
            author_stamp = _stamp(days_ago)
            # Committer time defaults to author time; override it to simulate a
            # rebase/cherry-pick, where the commit is re-applied "now" but keeps
            # its original author date.
            committer_at = committer_days_ago if committer_days_ago is not None else days_ago
            env = {
                "GIT_AUTHOR_DATE": author_stamp,
                "GIT_COMMITTER_DATE": _stamp(committer_at),
            }
        _git(repo, "commit", "-q", "-m", message, env=env)

    return repo, commit


# One commit of the dated history: (ISO time, {path: text to append}, message).
DatedCommit = tuple[str, dict[str, str], str]

# The issue #773 history (contract I773-1). Every date is absolute, so the
# windows the scans read are fixed by HEAD (2020-01-15), never by the clock.
# The 2019-10-03 edit is out of date order and carries the one agent trailer.
DATED_HISTORY: list[DatedCommit] = [
    ("2019-01-10T12:00:00Z", {
        "README.md": "# App\n\nSee [app](pkg/app.py).\n",
        "pkg/README.md": "# pkg\n\nHolds app.py.\n",
        "pkg/app.py": "def f():\n    return 1\n",
    }, "init"),
    ("2019-03-01T12:00:00Z", {"CLAUDE.md": "# Project\n\nNotes.\n"}, "instructions"),
    ("2019-11-01T12:00:00Z", {"pkg/app.py": "# edit 1\n"}, "edit 1"),
    ("2019-12-02T12:00:00Z", {"pkg/app.py": "# edit 2\n"}, "edit 2"),
    ("2019-10-03T12:00:00Z", {"pkg/app.py": "# edit 3\n"},
     "edit 3\n\nCo-authored-by: Claude <noreply@anthropic.com>"),
    ("2019-11-04T12:00:00Z", {"pkg/app.py": "# edit 4\n"}, "edit 4"),
    ("2019-12-05T12:00:00Z", {"pkg/app.py": "# edit 5\n"}, "edit 5"),
    ("2020-01-15T12:00:00Z", {"pkg/app.py": "# head\n"}, "head"),
]


def build_dated_repo(repo: Path, history: list[DatedCommit] = DATED_HISTORY) -> Path:
    """A repository whose commits carry these absolute author and committer times."""
    repo.mkdir(parents=True)
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    for when, appends, message in history:
        for rel, text in appends.items():
            path = repo / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as fh:
                fh.write(text)
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", message,
             env={"GIT_AUTHOR_DATE": when, "GIT_COMMITTER_DATE": when})
    return repo


@pytest.fixture
def dated_git_repo(tmp_path: Path) -> Path:
    """The issue #773 history with absolute dates; HEAD is 2020-01-15T12:00:00Z.

    The ``git_repo`` fixture dates commits relative to the current time, so it
    cannot show that a window is counted from HEAD rather than from today.
    """
    return build_dated_repo(tmp_path / "dated")
