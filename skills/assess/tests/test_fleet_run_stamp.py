"""Tests for the fleet run stamp and --delete-after (issue #569): lib/fleet_stamp.py, lib/fleet_cleanup.py."""
from __future__ import annotations

import json
import os
import subprocess
from datetime import date
from pathlib import Path

import jsonschema
import pytest

from lib import fleet_cleanup, fleet_stamp
from lib.fleet import STATUS_FAILED, STATUS_OK, FleetRow, ScanOptions, Target, run_fleet
from lib.fleet_cleanup import CloneDeleter, delete_clone
from lib.fleet_schema import STAMP_SCHEMA_PATH, build_stamp_schema, render_stamp
from lib.fleet_activity import is_active, window_start
from lib.fleet_stamp import build_stamp, write_stamp
from test_fleet import make_checkout, make_repo
from test_fleet_cli import fleet_cli


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True).stdout


def _row(repo: str, status: str = STATUS_OK, active: bool | None = True) -> FleetRow:
    ok = status == STATUS_OK
    return {"fleet_row_version": 2, "repo": repo, "group": repo.rsplit("/", 1)[0], "status": status,
            "reason": None if ok else "scan_error", "signals_schema_version": 1 if ok else None,
            "scans": {} if ok else None, "active": active if ok else None}


def _old_commit(path: Path) -> Path:
    """A one-commit repository committed in 2001."""
    path.mkdir(parents=True)
    _git(path, "init", "-q")
    (path / "f").write_text("x", encoding="utf-8")
    _git(path, "add", "-A")
    subprocess.run(["git", "-C", str(path), "-c", "user.email=t@e", "-c", "user.name=t", "commit", "-q", "-m", "x"],
                   check=True, env={**os.environ, "GIT_COMMITTER_DATE": "2001-01-01T00:00:00Z"})
    return path


def _pushed_repo(root: Path, slug: str) -> Path:
    """A ghsync-layout clone of a bare remote, everything pushed: safe to delete."""
    origin = root.parent / "remotes" / f"{slug.replace('/', '_')}.git"
    seed = make_checkout(root.parent / "seeds" / slug.replace("/", "_"))
    subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True)
    _git(seed, "push", "-q", str(origin), "HEAD:refs/heads/main")
    repo_dir = root / slug
    (repo_dir / "worktree").mkdir(parents=True)
    checkout = repo_dir / f"{repo_dir.name}-main"
    subprocess.run(["git", "clone", "-q", "-b", "main", str(origin), str(checkout)], check=True)
    _git(checkout, "config", "user.email", "t@example.com")
    _git(checkout, "config", "user.name", "t")
    return checkout


def _target(checkout: Path, repo: str = "grp/svc") -> Target:
    return Target(repo, checkout)


# --- window and activity ---------------------------------------------------


@pytest.mark.parametrize(("window", "today", "start"), [
    ("90d", date(2026, 11, 20), date(2026, 8, 22)),
    ("2w", date(2026, 11, 20), date(2026, 11, 6)),
    ("6mo", date(2026, 11, 20), date(2026, 5, 20)),
    ("1mo", date(2026, 3, 31), date(2026, 2, 28)),
    ("13mo", date(2026, 1, 15), date(2024, 12, 15)),
    ("1y", date(2028, 2, 29), date(2027, 2, 28)),
])
def test_window_start_counts_back_by_calendar(window, today, start):
    assert window_start(window, today) == start


def test_window_start_rejects_a_bad_window():
    with pytest.raises(ValueError):
        window_start("soon", date(2026, 1, 1))


@pytest.mark.parametrize("window", ["3000y", "999999999d", "99999999999w", "999999mo"])
def test_a_window_before_year_one_starts_at_date_min(window):
    assert window_start(window, date(2026, 11, 20)) == date.min


def test_is_active_reads_the_head_commit_date(tmp_path):
    since = window_start("6mo", date.today())
    assert is_active(make_checkout(tmp_path / "recent"), since)
    assert not is_active(_old_commit(tmp_path / "old"), since)
    assert not is_active(tmp_path / "absent", since)


def test_scan_target_sets_active_only_when_asked(tmp_path, monkeypatch):
    from lib import fleet
    monkeypatch.setattr(fleet, "checkout_problem", lambda _c: None)
    monkeypatch.setattr(fleet, "commit_state", lambda _c: {"head_sha": None, "dirty": None})

    def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        out = Path(command[command.index("--out") + 1])
        out.write_text(json.dumps({"signals_schema_version": 1, "scans": {}}), encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, "", "")
    monkeypatch.setattr(fleet.subprocess, "run", fake_run)
    old = Target("g/old", tmp_path / "old")
    monkeypatch.setattr(fleet, "is_active", lambda checkout, since: checkout.name == "new")
    assert fleet.scan_target(old, tmp_path, ScanOptions())["active"] is None
    assert fleet.scan_target(old, tmp_path, ScanOptions(active_since=date(2026, 1, 1)))["active"] is False
    new = Target("g/new", tmp_path / "new")
    assert fleet.scan_target(new, tmp_path, ScanOptions(active_since=date(2026, 1, 1)))["active"] is True


def test_build_stamp_counts_and_serialises_deterministically(tmp_path):
    rows = [_row("a/x"), _row("a/y", active=False), _row("b/z", STATUS_FAILED)]
    stamp = build_stamp(rows, toolkit_commit="abc", plugin_version="1.0.0",
                        today=date(2026, 11, 20), window="6mo", levels_version=None, counts_only=True,
                        paths_hashed=True, scans=["b", "a"])
    assert stamp["repositories"] == {"selected": 3, "scanned_ok": 2, "failed": 1, "qualifying": 1}
    assert stamp["scans"] == ["a", "b"]
    assert stamp["scan_date"] == "2026-11-20"
    path = write_stamp(tmp_path / "out", stamp)
    assert path.name == "run.json"
    assert json.loads(path.read_text(encoding="utf-8")) == stamp
    jsonschema.validate(stamp, build_stamp_schema())
    assert sorted(p.name for p in path.parent.iterdir()) == ["run.json"]


def test_write_stamp_leaves_no_temp_file_on_failure(tmp_path, monkeypatch):
    def boom(*_args: object) -> None:
        raise OSError("disk full")
    monkeypatch.setattr(fleet_stamp.os, "replace", boom)
    stamp = build_stamp([], toolkit_commit=None, plugin_version="x", today=date(2026, 1, 1),
                        window="6mo", levels_version=None, counts_only=True, paths_hashed=False, scans=None)
    with pytest.raises(OSError):
        write_stamp(tmp_path, stamp)
    assert list(tmp_path.iterdir()) == []


# --- delete-after: the refusals --------------------------------------------


def test_a_pushed_clean_clone_is_deleted_and_its_empty_dirs_go(tmp_path):
    checkout = _pushed_repo(tmp_path / "root", "grp/svc")
    assert delete_clone(_target(checkout), _row("grp/svc"), []) == fleet_cleanup.DELETED
    assert not (tmp_path / "root" / "grp" / "svc").exists()
    assert (tmp_path / "root" / "grp").is_dir()


def test_a_clone_without_a_worktree_dir_still_removes_its_repo_dir(tmp_path):
    checkout = _pushed_repo(tmp_path / "root", "grp/svc")
    worktree = checkout.parent / "worktree"
    if worktree.exists():
        worktree.rmdir()
    assert delete_clone(_target(checkout), _row("grp/svc"), []) == fleet_cleanup.DELETED
    assert not (tmp_path / "root" / "grp" / "svc").exists()
    assert (tmp_path / "root" / "grp").is_dir()


def test_sibling_worktree_content_survives(tmp_path):
    checkout = _pushed_repo(tmp_path / "root", "grp/svc")
    keep = checkout.parent / "worktree" / "notes.txt"
    keep.write_text("mine", encoding="utf-8")
    assert delete_clone(_target(checkout), _row("grp/svc"), []) == fleet_cleanup.DELETED
    assert not checkout.exists()
    assert keep.read_text(encoding="utf-8") == "mine"


@pytest.mark.parametrize(("setup", "reason"), [
    ("failed_row", fleet_cleanup.KEEP_SCAN_NOT_OK),
    ("modified", fleet_cleanup.KEEP_LOCAL_CHANGES),
    ("untracked", fleet_cleanup.KEEP_LOCAL_CHANGES),
    ("ignored", fleet_cleanup.KEEP_LOCAL_CHANGES),
    ("stash", fleet_cleanup.KEEP_STASH),
    ("unpushed", fleet_cleanup.KEEP_UNPUSHED),
    ("unpushed_branch", fleet_cleanup.KEEP_UNPUSHED),
    ("local_tag", fleet_cleanup.KEEP_UNPUSHED),
    ("local_note", fleet_cleanup.KEEP_UNPUSHED),
    ("linked_worktree", fleet_cleanup.KEEP_WORKTREES),
    ("protected_out", fleet_cleanup.KEEP_PROTECTED),
    ("protected_self", fleet_cleanup.KEEP_PROTECTED),
    ("git_error", fleet_cleanup.KEEP_GIT_ERROR),
])
def test_a_clone_with_something_to_lose_is_kept(tmp_path, setup, reason, monkeypatch):
    checkout = _pushed_repo(tmp_path / "root", "grp/svc")
    row = _row("grp/svc", STATUS_FAILED if setup == "failed_row" else STATUS_OK)
    protected: list[Path] = []
    if setup == "modified":
        (checkout / "README.md").write_text("changed\n", encoding="utf-8")
    elif setup == "untracked":
        (checkout / "new.txt").write_text("x", encoding="utf-8")
    elif setup == "ignored":
        (checkout / ".gitignore").write_text(".env\n", encoding="utf-8")
        _git(checkout, "add", ".gitignore")
        _git(checkout, "commit", "-q", "-m", "ignore")
        _git(checkout, "push", "-q", "origin", "main")
        (checkout / ".env").write_text("SECRET=1\n", encoding="utf-8")
    elif setup == "stash":
        (checkout / "README.md").write_text("changed\n", encoding="utf-8")
        _git(checkout, "stash", "-q")
    elif setup == "unpushed":
        _git(checkout, "commit", "-q", "--allow-empty", "-m", "local")
    elif setup == "unpushed_branch":
        _git(checkout, "branch", "side")
        _git(checkout, "checkout", "-q", "side")
        _git(checkout, "commit", "-q", "--allow-empty", "-m", "local")
        _git(checkout, "checkout", "-q", "main")
    elif setup == "local_tag":
        _git(checkout, "checkout", "-q", "--detach")
        _git(checkout, "commit", "-q", "--allow-empty", "-m", "tagged only")
        _git(checkout, "tag", "v-local")
        _git(checkout, "checkout", "-q", "main")
    elif setup == "local_note":
        _git(checkout, "notes", "add", "-m", "only here", "HEAD")  # a commit held only by refs/notes/commits
    elif setup == "linked_worktree":
        _git(checkout, "worktree", "add", "-q", str(checkout.parent / "worktree" / "feat"), "-b", "feat")
    elif setup == "protected_out":
        protected = [checkout / "out"]
    elif setup == "protected_self":
        protected = [checkout]
    elif setup == "git_error":
        def fail(*_args: object) -> str:
            raise fleet_cleanup._GitError("no git")
        monkeypatch.setattr(fleet_cleanup, "_git_out", fail)

    assert delete_clone(_target(checkout), row, protected) == reason
    assert (checkout / ".git").is_dir()


def test_a_plain_checkout_outside_the_layout_is_kept(tmp_path):
    plain = make_checkout(tmp_path / "plain")
    assert delete_clone(Target("x/plain", plain), _row("x/plain"), []) == fleet_cleanup.KEEP_NOT_LAYOUT
    assert plain.is_dir()


def test_a_symlinked_checkout_is_kept(tmp_path):
    real = _pushed_repo(tmp_path / "root", "grp/real")
    link_dir = tmp_path / "root" / "grp" / "svc"
    link_dir.mkdir()
    (link_dir / "svc-main").symlink_to(real)
    assert delete_clone(Target("grp/svc", link_dir / "svc-main"), _row("grp/svc"), []) == fleet_cleanup.KEEP_SYMLINK
    assert real.is_dir()


def test_a_linked_worktree_named_like_a_clone_is_kept(tmp_path):
    main = _pushed_repo(tmp_path / "root", "grp/svc")
    other = tmp_path / "root" / "grp" / "wt"
    other.mkdir()
    _git(main, "worktree", "add", "-q", str(other / "wt-main"), "-b", "wt")
    assert delete_clone(Target("grp/wt", other / "wt-main"), _row("grp/wt"), []) == fleet_cleanup.KEEP_NOT_MAIN_CLONE


def test_a_checkout_that_is_not_its_work_tree_root_is_kept(tmp_path):
    outer = make_repo(tmp_path / "root", "grp/outer")
    inner = outer / "sub" / "sub-main"
    (inner / ".git").mkdir(parents=True)  # a bare-looking .git dir that git does not treat as a repo root
    assert delete_clone(Target("grp/sub", inner), _row("grp/sub"), []) == fleet_cleanup.KEEP_NOT_MAIN_CLONE


def test_a_failed_rmtree_is_kept_and_reported(tmp_path, monkeypatch):
    checkout = _pushed_repo(tmp_path / "root", "grp/svc")

    def boom(_path: object, **_kwargs: object) -> None:
        raise OSError("busy")
    monkeypatch.setattr(fleet_cleanup.shutil, "rmtree", boom)
    assert delete_clone(_target(checkout), _row("grp/svc"), []) == fleet_cleanup.KEEP_DELETE_ERROR


def test_clone_deleter_records_outcomes_and_never_raises(tmp_path, monkeypatch):
    deleter = CloneDeleter([tmp_path / "out"])
    checkout = _pushed_repo(tmp_path / "root", "grp/svc")
    deleter(_target(checkout), _row("grp/svc"))
    deleter(Target("grp/bad", tmp_path / "x"), _row("grp/bad", STATUS_FAILED))

    def boom(*_args: object) -> str:
        raise RuntimeError("unexpected")
    monkeypatch.setattr(fleet_cleanup, "delete_clone", boom)
    deleter(Target("grp/odd", tmp_path / "y"), _row("grp/odd"))
    assert deleter.deleted() == ["grp/svc"]
    assert deleter.kept() == [("grp/bad", fleet_cleanup.KEEP_SCAN_NOT_OK),
                              ("grp/odd", fleet_cleanup.KEEP_DELETE_ERROR)]


def test_run_fleet_calls_after_with_each_row(tmp_path):
    seen: list[tuple[str, str]] = []

    def scan(target: Target, _scratch: Path, _options: ScanOptions) -> FleetRow:
        return _row(target.repo)
    run_fleet([Target("a/x", tmp_path), Target("a/y", tmp_path)], ScanOptions(), 2, scan=scan,
              after=lambda t, r: seen.append((t.repo, r["status"])))
    assert sorted(seen) == [("a/x", STATUS_OK), ("a/y", STATUS_OK)]


# --- the command -----------------------------------------------------------


def test_delete_after_removes_safe_clones_and_keeps_the_rest(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    root = tmp_path / "root"
    safe = _pushed_repo(root, "grp/safe")
    dirty = _pushed_repo(root, "grp/dirty")
    (dirty / "wip.txt").write_text("x", encoding="utf-8")

    assert fleet_cli.main([str(root), "--out", str(tmp_path / "out"), "--scans", "agent_ops",
                           "--delete-after"]) == 0

    assert not safe.exists()
    assert (dirty / "wip.txt").is_file()
    rows = [json.loads(line) for line in (tmp_path / "out" / "repos.jsonl").read_text().splitlines()]
    assert [(r["repo"], r["status"]) for r in rows] == [("grp/dirty", "ok"), ("grp/safe", "ok")]
    stamp = json.loads((tmp_path / "out" / "run.json").read_text(encoding="utf-8"))
    assert stamp["repositories"] == {"selected": 2, "scanned_ok": 2, "failed": 0, "qualifying": 2}
    assert [r["active"] for r in rows] == [True, True]
    assert stamp["window"] == "6mo"
    assert stamp["scans"] == ["agent_ops"]
    assert stamp["levels_version"] is None
    jsonschema.validate(stamp, build_stamp_schema())
    err = capsys.readouterr().err
    assert "deleted 1 clones, kept 1" in err
    assert "kept: grp/dirty (local_changes)" in err


def test_without_delete_after_nothing_is_removed(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    safe = _pushed_repo(tmp_path / "root", "grp/safe")
    assert fleet_cli.main([str(tmp_path / "root"), "--out", str(tmp_path / "out"), "--scans", "agent_ops"]) == 0
    assert safe.is_dir()


def test_an_out_that_cannot_be_created_exits_2_before_scanning(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    safe = _pushed_repo(tmp_path / "root", "grp/safe")
    (tmp_path / "file").write_text("x", encoding="utf-8")
    assert fleet_cli.main([str(tmp_path / "root"), "--out", str(tmp_path / "file" / "out"),
                           "--delete-after"]) == 2
    assert capsys.readouterr().err.startswith("error: cannot create --out")
    assert safe.is_dir()


def _no_scan(*_args: object, **_kwargs: object) -> list[FleetRow]:
    raise AssertionError("a scan ran before --out was checked")


def test_an_out_that_is_a_regular_file_exits_2_before_scanning(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    _pushed_repo(tmp_path / "root", "grp/safe")
    (tmp_path / "file").write_text("x", encoding="utf-8")
    monkeypatch.setattr(fleet_cli, "run_fleet", _no_scan)
    assert fleet_cli.main([str(tmp_path / "root"), "--out", str(tmp_path / "file")]) == 2
    assert capsys.readouterr().err.startswith("error: cannot create --out")


@pytest.mark.skipif(os.name != "posix" or os.geteuid() == 0, reason="needs POSIX permissions, not root")
def test_an_out_that_cannot_be_written_exits_2_before_scanning(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    _pushed_repo(tmp_path / "root", "grp/safe")
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0o500)
    monkeypatch.setattr(fleet_cli, "run_fleet", _no_scan)
    try:
        assert fleet_cli.main([str(tmp_path / "root"), "--out", str(locked)]) == 2
    finally:
        locked.chmod(0o700)
    assert capsys.readouterr().err.startswith("error: cannot write to --out")
    assert list(locked.iterdir()) == []


def test_a_writable_out_is_left_empty_by_the_probe(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _pushed_repo(tmp_path / "root", "grp/safe")
    seen: list[list[str]] = []

    def no_rows(*_args: object, **_kwargs: object) -> list[FleetRow]:
        seen.append(sorted(p.name for p in (tmp_path / "out").iterdir()))
        return []
    monkeypatch.setattr(fleet_cli, "run_fleet", no_rows)
    assert fleet_cli.main([str(tmp_path / "root"), "--out", str(tmp_path / "out")]) == 0
    assert seen == [[]]


@pytest.mark.windows_followup
def test_an_unexpandable_list_entry_exits_2(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "list.txt").write_text("~nosuchuser-fleet-616/repo\n", encoding="utf-8")
    monkeypatch.setattr(fleet_cli, "run_fleet", _no_scan)
    assert fleet_cli.main(["--from-list", str(tmp_path / "list.txt"), "--out", str(tmp_path / "o")]) == 2
    err = capsys.readouterr().err
    assert err.startswith("error: list entry '~nosuchuser-fleet-616/repo'")
    assert "Traceback" not in err


def test_repeated_scans_stamp_the_same_run_json(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _pushed_repo(tmp_path / "root", "grp/safe")
    stamps = []
    for name, scans in (("once", "agent_ops"), ("twice", "agent_ops,agent_ops")):
        assert fleet_cli.main([str(tmp_path / "root"), "--out", str(tmp_path / name), "--scans", scans]) == 0
        stamps.append((tmp_path / name / "run.json").read_bytes())
    assert stamps[0] == stamps[1]
    assert json.loads(stamps[0])["scans"] == ["agent_ops"]


def test_an_extreme_window_completes_the_run(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _pushed_repo(tmp_path / "root", "grp/safe")
    assert fleet_cli.main([str(tmp_path / "root"), "--out", str(tmp_path / "out"), "--scans", "agent_ops",
                           "--window", "999999999d"]) == 0
    rows = [json.loads(line) for line in (tmp_path / "out" / "repos.jsonl").read_text().splitlines()]
    assert [(r["status"], r["active"]) for r in rows] == [("ok", True)]


def test_the_checked_in_stamp_schema_matches_the_type():
    assert STAMP_SCHEMA_PATH.read_text(encoding="utf-8") == render_stamp(), (
        "regenerate: cd skills/assess && uv run --locked python scripts/lib/fleet_schema.py")


def test_a_dormant_repository_is_scanned_but_not_qualifying(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    root = tmp_path / "root"
    make_repo(root, "grp/live")
    (root / "grp" / "dormant" / "worktree").mkdir(parents=True)
    _old_commit(root / "grp" / "dormant" / "dormant-main")
    assert fleet_cli.main([str(root), "--out", str(tmp_path / "out"), "--scans", "agent_ops"]) == 0
    rows = [json.loads(line) for line in (tmp_path / "out" / "repos.jsonl").read_text().splitlines()]
    assert [(r["repo"], r["active"]) for r in rows] == [("grp/dormant", False), ("grp/live", True)]
    stamp = json.loads((tmp_path / "out" / "run.json").read_text(encoding="utf-8"))
    assert stamp["repositories"] == {"selected": 2, "scanned_ok": 2, "failed": 0, "qualifying": 1}
