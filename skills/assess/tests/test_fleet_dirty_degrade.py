"""Fleet's unknown dirty state (#932): a null ``dirty`` carries a reason and is never read as clean.

Sparse, skip-worktree and assume-unchanged entries are ones ``git status`` never
compares, so a clean status there is not a clean checkout. With a ``HEAD`` such a
checkout is scanned from its committed tree, like a dirty one.
"""
from __future__ import annotations

from pathlib import Path

import jsonschema
import pytest

from lib import fleet
from lib.fleet import ScanOptions, Target
from lib.fleet_schema import build_schema
from test_fleet import _fake_signals, _git, make_checkout

FILES = {"CLAUDE.md": "# guide\n", "src/a.py": "x = 1\n"}

# The fake scan reports whether the tree it was given holds the committed guide.
_SEES_COMMITTED_GUIDE = (
    "import os\nrepo = sys.argv[1]\npath = os.path.join(repo, 'CLAUDE.md')\n"
    "seen = os.path.exists(path) and open(path, encoding='utf-8').read() == '# guide\\n'\n"
    "doc = {'signals_schema_version': 1, 'stamp': {}, 'scans': {'probe': {'guide': seen}}}\n"
    "open(out, 'w').write(json.dumps(doc))\n")


def _head(repo: Path) -> str:
    return _git(repo, "rev-parse", "HEAD").strip()


def _sparse(repo: Path) -> None:
    """Only ``src/`` checked out: CLAUDE.md is outside the sparse set and absent."""
    _git(repo, "sparse-checkout", "set", "--no-cone", "/src/")


def _skip_worktree(repo: Path) -> None:
    _git(repo, "update-index", "--skip-worktree", "CLAUDE.md")
    (repo / "CLAUDE.md").write_text("# local edit\n", encoding="utf-8")


def _assume_unchanged(repo: Path) -> None:
    _git(repo, "update-index", "--assume-unchanged", "CLAUDE.md")
    (repo / "CLAUDE.md").write_text("# local edit\n", encoding="utf-8")


@pytest.mark.parametrize(("change", "reason"), [
    (_sparse, fleet.SPARSE_CHECKOUT),
    (_skip_worktree, fleet.SKIP_WORKTREE),
    (_assume_unchanged, fleet.ASSUME_UNCHANGED),
])
def test_entries_git_status_never_compares_read_as_unknown(tmp_path, change, reason):
    repo = make_checkout(tmp_path / "r", FILES)
    change(repo)
    assert _git(repo, "status", "--porcelain", "--untracked-files=no").strip() == ""  # git calls it clean
    assert fleet.commit_state(repo) == {"head_sha": _head(repo), "dirty": None, "dirty_reason": reason}


@pytest.mark.parametrize("change", [_sparse, _skip_worktree, _assume_unchanged])
def test_an_unknown_state_is_scored_from_the_committed_tree(tmp_path, monkeypatch, change):
    _fake_signals(tmp_path, monkeypatch, _SEES_COMMITTED_GUIDE)
    repo = make_checkout(tmp_path / "r", FILES)
    change(repo)
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    with fleet.committed_tree(repo, scratch, fleet.commit_state(repo)) as tree:
        assert tree is not None and tree != repo and scratch in tree.parents
        assert (tree / "CLAUDE.md").read_text(encoding="utf-8") == "# guide\n"
    row = fleet.scan_target(Target("g/r", repo), scratch, ScanOptions())
    assert row["status"] == "ok" and row["scans"] == {"probe": {"guide": True}}
    assert (row["head_sha"], row["dirty"]) == (_head(repo), None)
    assert row["dirty_reason"] in fleet.DIRTY_UNKNOWN_REASONS
    jsonschema.validate(row, build_schema())


def test_a_sparse_checkout_with_a_real_edit_is_dirty(tmp_path):
    repo = make_checkout(tmp_path / "r", FILES)
    _sparse(repo)
    (repo / "src/a.py").write_text("x = 2\n", encoding="utf-8")
    assert fleet.commit_state(repo) == {"head_sha": _head(repo), "dirty": True, "dirty_reason": None}


def test_a_sparse_set_that_excludes_nothing_is_clean(tmp_path):
    repo = make_checkout(tmp_path / "r", FILES)
    _git(repo, "sparse-checkout", "set", "--no-cone", "/*")
    assert fleet.commit_state(repo) == {"head_sha": _head(repo), "dirty": False, "dirty_reason": None}


def _failing(monkeypatch: pytest.MonkeyPatch, subcommand: str) -> None:
    real = fleet._git_output

    def git_output(path: Path, *args: str) -> str | None:
        return None if args[0] == subcommand else real(path, *args)
    monkeypatch.setattr(fleet, "_git_output", git_output)


@pytest.mark.parametrize(("subcommand", "reason"), [
    ("status", fleet.STATUS_ERROR),
    ("ls-files", fleet.INDEX_READ_ERROR),
])
def test_a_failed_read_is_unknown_and_never_scanned_in_place(tmp_path, monkeypatch, subcommand, reason):
    repo = make_checkout(tmp_path / "r", FILES)
    head = _head(repo)
    _failing(monkeypatch, subcommand)
    commit = fleet.commit_state(repo)
    assert commit == {"head_sha": head, "dirty": None, "dirty_reason": reason}
    with fleet.committed_tree(repo, tmp_path, commit) as tree:
        assert tree is not None and tree != repo


def test_no_commit_is_scanned_in_place_with_its_reason(tmp_path, monkeypatch):
    _fake_signals(tmp_path, monkeypatch, _SEES_COMMITTED_GUIDE)
    repo = tmp_path / "r"
    repo.mkdir()
    _git(repo, "init", "-q")
    with fleet.committed_tree(repo, tmp_path, fleet.commit_state(repo)) as tree:
        assert tree == repo  # no sha to check out
    row = fleet.scan_target(Target("g/r", repo), tmp_path, ScanOptions())
    assert (row["status"], row["head_sha"], row["dirty"], row["dirty_reason"]) == (
        "ok", None, None, fleet.NO_COMMIT)
    jsonschema.validate(row, build_schema())


def test_a_known_state_carries_no_reason(tmp_path, monkeypatch):
    _fake_signals(tmp_path, monkeypatch, _SEES_COMMITTED_GUIDE)
    repo = make_checkout(tmp_path / "r", FILES)
    clean = fleet.scan_target(Target("g/r", repo), tmp_path, ScanOptions())
    (repo / "CLAUDE.md").unlink()
    dirty = fleet.scan_target(Target("g/r", repo), tmp_path, ScanOptions())
    assert (clean["dirty"], clean["dirty_reason"]) == (False, None)
    assert (dirty["dirty"], dirty["dirty_reason"]) == (True, None)


def test_a_failed_row_has_no_dirty_reason(tmp_path):
    row = fleet.scan_target(Target("g/r", tmp_path / "absent"), tmp_path, ScanOptions())
    assert (row["status"], row["dirty"], row["dirty_reason"]) == ("failed", None, None)
    jsonschema.validate(row, build_schema())


def _with_submodule(tmp_path: Path) -> tuple[Path, Path]:
    lib = make_checkout(tmp_path / "lib", {"lib.py": "y = 1\n"})
    repo = make_checkout(tmp_path / "r", FILES)
    _git(repo, "-c", "protocol.file.allow=always", "submodule", "add", "-q", str(lib), "vendor/lib")
    _git(repo, "commit", "-q", "-m", "add submodule")
    return repo, repo / "vendor" / "lib"


def test_a_submodule_s_own_edit_never_marks_the_checkout_dirty(tmp_path):
    repo, sub = _with_submodule(tmp_path)
    (sub / "lib.py").write_text("y = 2\n", encoding="utf-8")
    assert _git(repo, "status", "--porcelain", "--untracked-files=no").strip() != ""  # git alone says modified
    assert fleet.commit_state(repo) == {"head_sha": _head(repo), "dirty": False, "dirty_reason": None}


def test_a_moved_submodule_commit_still_marks_the_checkout_dirty(tmp_path):
    repo, sub = _with_submodule(tmp_path)
    _git(sub, "config", "user.email", "t@example.com")
    _git(sub, "config", "user.name", "t")
    (sub / "lib.py").write_text("y = 2\n", encoding="utf-8")
    _git(sub, "commit", "-q", "-am", "move")
    assert fleet.commit_state(repo)["dirty"] is True
