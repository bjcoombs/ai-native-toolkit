"""Tests for the fleet runner core, lib/fleet.py (issue #567)."""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import jsonschema
import pytest

from lib import fleet
from lib.fleet import ScanOptions, Target
from lib.fleet_schema import SCHEMA_PATH, build_schema, render


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True,
    ).stdout


def make_checkout(path: Path, files: dict[str, str] | None = None, remote: str | None = None) -> Path:
    """A committed git repository at ``path``."""
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q")
    _git(path, "config", "user.email", "t@example.com")
    _git(path, "config", "user.name", "t")
    for name, text in (files or {"README.md": "x\n"}).items():
        (path / name).parent.mkdir(parents=True, exist_ok=True)
        (path / name).write_text(text, encoding="utf-8")
    if remote:
        _git(path, "remote", "add", "origin", remote)
    _git(path, "add", "-A")
    _git(path, "commit", "-q", "-m", "init")
    return path


def make_repo(root: Path, slug: str, **kwargs: object) -> Path:
    """A ghsync-layout repository ``<root>/<slug>/<name>-main`` plus ``worktree/``."""
    repo_dir = root / slug
    (repo_dir / "worktree").mkdir(parents=True, exist_ok=True)
    return make_checkout(repo_dir / f"{repo_dir.name}-main", **kwargs)  # type: ignore[arg-type]


# --- discovery -------------------------------------------------------------


def test_discover_root_reads_the_layout_at_any_depth(tmp_path):
    root = tmp_path / "froot"
    make_repo(root, "grpb/lib1")
    make_repo(root, "grpa/svc1")
    make_repo(root, "grpa/sub/deep/svc3")
    (root / ".hidden" / "x" / "x-main").mkdir(parents=True)
    # A directory inside a repo's worktree/ that looks like a repo is not one.
    (root / "grpa" / "svc1" / "worktree" / "w" / "w-main").mkdir(parents=True)
    (root / "grpa" / "loose").mkdir()

    targets = fleet.discover_root(root)

    assert [t.repo for t in targets] == ["grpa/sub/deep/svc3", "grpa/svc1", "grpb/lib1"]
    assert [t.group for t in targets] == ["grpa/sub/deep", "grpa", "grpb"]
    assert targets[1].checkout == (root / "grpa" / "svc1" / "svc1-main").resolve()


def test_discover_root_prefixes_a_single_segment_with_the_root_name(tmp_path):
    root = tmp_path / "myorg"
    make_repo(root, "tool")
    assert [(t.repo, t.group) for t in fleet.discover_root(root)] == [("myorg/tool", "myorg")]


def test_discover_root_keeps_a_broken_checkout(tmp_path):
    root = tmp_path / "froot"
    checkout = make_repo(root, "grpb/lib1")
    subprocess.run(["rm", "-rf", str(checkout / ".git")], check=True)
    (checkout / ".git").write_text("", encoding="utf-8")
    assert [t.repo for t in fleet.discover_root(root)] == ["grpb/lib1"]


# --- list input ------------------------------------------------------------


def test_list_entries_in_every_form_name_the_same_repositories(tmp_path):
    root = tmp_path / "froot"
    make_repo(root, "grpa/svc1")
    make_repo(root, "grpa/svc2")
    make_repo(root, "grpb/lib1")
    entries = fleet.parse_list(
        "# checkouts, a repo dir and a porcelain path\n\n"
        f"{root}/grpa/svc1/svc1-main\n{root}/grpa/svc2\ngrpb/lib1\n"
        f"{root}/grpa/svc1\n",  # a repeat of svc1
    )
    assert len(entries) == 4

    with_root = fleet.targets_from_list(entries, root, tmp_path)
    assert with_root == fleet.discover_root(root)

    # Without a root, absolute entries still give the layout ids.
    no_root = fleet.targets_from_list(entries[:2] + [f"{root}/grpb/lib1/lib1-main"], None, tmp_path)
    assert no_root == fleet.discover_root(root)


def test_uncloned_porcelain_entry_keeps_its_layout_id(tmp_path):
    root = tmp_path / "froot"
    root.mkdir()
    targets = fleet.targets_from_list(["a/b/svc9"], root, tmp_path)
    assert [(t.repo, t.group) for t in targets] == [("a/b/svc9", "a/b")]
    assert targets[0].checkout == (root / "a" / "b" / "svc9" / "svc9-main").resolve()
    assert fleet.checkout_problem(targets[0].checkout) == fleet.MISSING_CHECKOUT


def test_missing_listed_checkout_keeps_its_layout_id(tmp_path):
    root = tmp_path / "froot"
    root.mkdir()
    entry = str(root / "grpa" / "svc1" / "svc1-main")
    for base_root in (root, None):
        targets = fleet.targets_from_list([entry], base_root, tmp_path)
        assert [t.repo for t in targets] == ["grpa/svc1"]
        assert fleet.checkout_problem(targets[0].checkout) == fleet.MISSING_CHECKOUT


def test_list_entry_relative_to_the_working_directory(tmp_path):
    make_repo(tmp_path / "froot", "grpa/svc1")
    targets = fleet.targets_from_list(["froot/grpa/svc1/svc1-main"], None, tmp_path)
    assert [t.repo for t in targets] == ["grpa/svc1"]


def test_list_entry_outside_the_root_uses_the_remote_path(tmp_path):
    checkout = make_repo(tmp_path / "elsewhere", "x/svc9",
                         remote="git@gl.test:top/sub/svc9.git")
    targets = fleet.targets_from_list([str(checkout)], tmp_path / "froot", tmp_path)
    assert [(t.repo, t.group) for t in targets] == [("top/sub/svc9", "top/sub")]


def test_plain_checkout_outside_the_layout(tmp_path):
    checkout = make_checkout(tmp_path / "work" / "plain")
    targets = fleet.targets_from_list([str(checkout)], None, tmp_path)
    assert targets == [Target("work/plain", checkout.resolve())]


def test_missing_list_entry_becomes_a_target_that_fails(tmp_path):
    targets = fleet.targets_from_list([str(tmp_path / "grp" / "gone")], None, tmp_path)
    assert targets == [Target("grp/gone", (tmp_path / "grp" / "gone" / "gone-main").resolve())]
    assert fleet.checkout_problem(targets[0].checkout) == fleet.MISSING_CHECKOUT


@pytest.mark.parametrize(("url", "path"), [
    ("https://gl.test/grp/sub/repo.git", "grp/sub/repo"),
    ("https://token@github.com/org/repo", "org/repo"),
    ("ssh://git@gl.test:2222/grp/repo.git", "grp/repo"),
    ("git@github.com:org/repo.git", "org/repo"),
    ("file:///srv/bare/grp/repo.git", None),
    ("file://", None),
    ("/srv/bare/repo.git", None),
])
def test_remote_path(tmp_path, url, path):
    checkout = make_checkout(tmp_path / "r", remote=url)
    assert fleet.remote_path(checkout) == path


@pytest.mark.parametrize(("url", "path"), [
    ("https://h/../../x", "x"),
    ("https://h/a/./../b//c.git", "a/b/c"),
    ("https://h/../..", None),
])
def test_remote_path_drops_dot_segments(tmp_path, url, path):
    assert fleet.remote_path(make_checkout(tmp_path / "r", remote=url)) == path


def test_file_remote_does_not_leak_a_local_path(tmp_path):
    checkout = make_repo(tmp_path / "elsewhere", "grp/svc", remote="file:///home/user/bare/svc.git")
    targets = fleet.targets_from_list([str(checkout)], None, tmp_path)
    assert [t.repo for t in targets] == ["grp/svc"]


def test_duplicate_list_ids_are_reported(tmp_path, capsys):
    root = tmp_path / "froot"
    make_repo(root, "grpa/svc1")
    fleet.targets_from_list(["grpa/svc1", "grpa/svc1/svc1-main"], root, tmp_path)
    assert "skipped list entry grpa/svc1/svc1-main: same id grpa/svc1" in capsys.readouterr().err


def test_dot_only_remote_falls_back_to_directory_names(tmp_path):
    checkout = make_repo(tmp_path / "elsewhere", "grp/svc", remote="https://h/../..")
    targets = fleet.targets_from_list([str(checkout)], None, tmp_path)
    assert [t.repo for t in targets] == ["grp/svc"]


def test_remote_path_without_a_remote(tmp_path):
    assert fleet.remote_path(make_checkout(tmp_path / "r")) is None


# --- scanning --------------------------------------------------------------


def test_checkout_problem(tmp_path):
    good = make_checkout(tmp_path / "good")
    (good / "sub").mkdir()
    broken = tmp_path / "broken"
    broken.mkdir()
    (broken / ".git").write_text("", encoding="utf-8")
    assert fleet.checkout_problem(good) is None
    assert fleet.checkout_problem(good / "sub") == fleet.NOT_A_WORK_TREE
    assert fleet.checkout_problem(broken) == fleet.NOT_A_WORK_TREE
    assert fleet.checkout_problem(tmp_path / "absent") == fleet.MISSING_CHECKOUT


def test_signals_command_flags(tmp_path):
    base = [sys.executable, str(fleet.SIGNALS_SCRIPT), str(tmp_path), "--out", str(tmp_path / "o.json")]
    assert fleet.signals_command(tmp_path, tmp_path / "o.json", ScanOptions()) == base + ["--counts-only"]
    full = ScanOptions(counts_only=False, hash_salt="s", window="90d", scans="agent_ops")
    assert fleet.signals_command(tmp_path, tmp_path / "o.json", full) == base + [
        "--window", "90d", "--scans", "agent_ops"]
    hashed = ScanOptions(hash_salt="s")
    assert fleet.signals_command(tmp_path, tmp_path / "o.json", hashed) == base + [
        "--counts-only", "--hash-salt", "s"]


def test_run_salt():
    assert fleet.run_salt(False, None) is None
    assert fleet.run_salt(True, "fixed") == "fixed"
    assert fleet.run_salt(False, "fixed") == "fixed"
    drawn = fleet.run_salt(True, None)
    assert drawn and len(drawn) == 32


def _fake_signals(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, body: str) -> None:
    script = tmp_path / "fake_signals.py"
    script.write_text("import sys, json, time\nout = sys.argv[sys.argv.index('--out') + 1]\n" + body,
                      encoding="utf-8")
    monkeypatch.setattr(fleet, "SIGNALS_SCRIPT", script)


def test_scan_exit_writes_a_stderr_tail_beside_the_rows(tmp_path, monkeypatch):
    _fake_signals(tmp_path, monkeypatch, "sys.stderr.write('x' * 5000 + 'Traceback: boom')\nsys.exit(4)\n")
    logs = tmp_path / "out" / fleet.LOGS_DIR
    row = fleet.scan_target(Target("grp/sub/r", make_checkout(tmp_path / "r")), tmp_path,
                            ScanOptions(log_dir=logs))
    assert row["reason"] == "scan_exit_4"
    assert "boom" not in json.dumps(row)
    tail = (logs / "grp" / "sub" / "r.stderr").read_text(encoding="utf-8")
    assert tail.endswith("Traceback: boom") and len(tail) == fleet.STDERR_TAIL_CHARS


def test_stderr_tail_never_leaves_the_log_dir(tmp_path):
    logs = tmp_path / "out" / "logs"
    assert fleet.write_stderr_tail(logs, Target("../../escape", tmp_path), "err") is None
    assert not (tmp_path / "escape.stderr").exists()
    assert not (tmp_path / "out" / "escape.stderr").exists()


def test_run_fleet_clears_old_logs_first(tmp_path):
    logs = tmp_path / "out" / "logs"
    (logs / "old").mkdir(parents=True)
    (logs / "old" / "repo.stderr").write_text("stale", encoding="utf-8")
    (logs / "important.txt").write_text("keep", encoding="utf-8")
    (logs / "keep").mkdir()
    (logs / "keep" / "notes.md").write_text("keep", encoding="utf-8")

    def scan(target: Target, scratch: Path, options: ScanOptions) -> fleet.FleetRow:
        fleet.write_stderr_tail(options.log_dir, target, "fresh")
        return fleet._row(target, reason="scan_exit_1")

    fleet.run_fleet([Target("g/r", tmp_path)], ScanOptions(log_dir=logs), jobs=1, scan=scan)
    assert sorted(p.relative_to(logs).as_posix() for p in logs.rglob("*") if p.is_file()) == [
        "g/r.stderr", "important.txt", "keep/notes.md"]
    assert not (logs / "old").exists()
    fleet.clear_stderr_logs(tmp_path / "absent")  # no directory: nothing to do


def test_stderr_tail_is_skipped_without_a_log_dir_or_on_a_write_error(tmp_path):
    target = Target("g/r", tmp_path)
    assert fleet.write_stderr_tail(None, target, "err") is None
    (tmp_path / "blocked").write_text("", encoding="utf-8")  # a file where the directory goes
    assert fleet.write_stderr_tail(tmp_path / "blocked", target, "err") is None


@pytest.mark.parametrize(("body", "reason"), [
    ("sys.exit(3)\n", "scan_exit_3"),
    ("open(out, 'w').write('not json')\n", fleet.INVALID_OUTPUT),
    ("open(out, 'w').write(json.dumps({'scans': []}))\n", fleet.INVALID_OUTPUT),
    ("time.sleep(30)\n", fleet.SCAN_TIMEOUT),
])
def test_scan_failures_become_reason_tokens(tmp_path, monkeypatch, body, reason):
    _fake_signals(tmp_path, monkeypatch, body)
    target = Target("grp/r", make_checkout(tmp_path / "r"))
    row = fleet.scan_target(target, tmp_path, ScanOptions(timeout=2))
    assert row == {"fleet_row_version": fleet.FLEET_ROW_VERSION, "repo": "grp/r", "group": "grp",
                   "status": "failed", "reason": reason, "signals_schema_version": None, "scans": None}
    jsonschema.validate(row, build_schema())


def test_scan_that_cannot_start(tmp_path, monkeypatch):
    monkeypatch.setattr(fleet.sys, "executable", str(tmp_path / "no-python"))
    row = fleet.scan_target(Target("g/r", make_checkout(tmp_path / "r")), tmp_path, ScanOptions())
    assert row["reason"] == fleet.SCAN_ERROR


def test_scan_child_runs_with_optional_locks_off(tmp_path, monkeypatch):
    """Every git call the scan process makes inherits GIT_OPTIONAL_LOCKS=0."""
    monkeypatch.setenv("GIT_OPTIONAL_LOCKS", "1")
    _fake_signals(tmp_path, monkeypatch, "import os\nsys.exit(0 if os.environ.get('GIT_OPTIONAL_LOCKS') == '0' else 7)\n")
    row = fleet.scan_target(Target("g/r", make_checkout(tmp_path / "r")), tmp_path, ScanOptions())
    assert row["reason"] == fleet.INVALID_OUTPUT  # exited 0: the variable was set; wrote nothing


def test_scan_ok_keeps_only_the_scans(tmp_path, monkeypatch):
    doc = {"signals_schema_version": 1, "stamp": {"scan_date": "x"}, "scans": {"a": {"n": 1}}}
    _fake_signals(tmp_path, monkeypatch, f"open(out, 'w').write(json.dumps({doc!r}))\n")
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    row = fleet.scan_target(Target("g/r", make_checkout(tmp_path / "r")), scratch, ScanOptions())
    assert row["status"] == "ok" and row["reason"] is None
    assert row["scans"] == {"a": {"n": 1}} and row["signals_schema_version"] == 1
    assert list(scratch.iterdir()) == []  # the scan's document is cleaned up
    jsonschema.validate(row, build_schema())


def test_run_fleet_isolates_a_raising_scan_and_sorts(tmp_path):
    targets = [Target("b/two", tmp_path), Target("a/one", tmp_path), Target("c/three", tmp_path)]

    def scan(target: Target, scratch: Path, options: ScanOptions) -> fleet.FleetRow:
        if target.repo == "b/two":
            raise RuntimeError("boom")
        time.sleep(0.05 if target.repo == "a/one" else 0)  # finish out of order
        return fleet._row(target, document={"signals_schema_version": 1, "scans": {}})

    rows = fleet.run_fleet(targets, ScanOptions(), jobs=3, scan=scan)
    assert [(r["repo"], r["status"], r["reason"]) for r in rows] == [
        ("a/one", "ok", None), ("b/two", "failed", fleet.SCAN_ERROR), ("c/three", "ok", None)]


# --- output ----------------------------------------------------------------


def test_write_rows_is_sorted_compact_jsonl(tmp_path):
    rows = [fleet._row(Target("g/r", tmp_path), reason=fleet.MISSING_CHECKOUT)]
    path = fleet.write_rows(tmp_path / "out", rows)
    assert path == tmp_path / "out" / fleet.REPOS_FILE
    text = path.read_text(encoding="utf-8")
    assert text == json.dumps(rows[0], sort_keys=True, separators=(",", ":")) + "\n"
    assert sorted(p.name for p in path.parent.iterdir()) == [fleet.REPOS_FILE]


def test_write_rows_leaves_no_partial_file(tmp_path, monkeypatch):
    def fail(*_args: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(fleet.os, "replace", fail)
    with pytest.raises(OSError):
        fleet.write_rows(tmp_path / "out", [])
    assert list((tmp_path / "out").iterdir()) == []


def test_out_inside_target(tmp_path):
    checkout = tmp_path / "g" / "r" / "r-main"
    plain = tmp_path / "plain"
    targets = [Target("g/r", checkout), Target("x/plain", plain)]
    assert fleet.out_inside_target(checkout / "deep", targets) == targets[0]
    assert fleet.out_inside_target(tmp_path / "g" / "r", targets) == targets[0]
    assert fleet.out_inside_target(plain, targets) == targets[1]
    assert fleet.out_inside_target(tmp_path / "g" / "r" / "worktree" / "x", targets) == targets[0]
    assert fleet.out_inside_target(tmp_path / "g" / "sibling", targets) is None
    assert fleet.out_inside_target(tmp_path / "out", targets) is None


def test_out_beside_a_plain_checkout_named_main(tmp_path):
    """A plain checkout whose name ends in -main is its own repository directory."""
    plain = tmp_path / "work" / "svc-main"
    targets = [Target("work/svc-main", plain)]
    assert fleet.out_inside_target(tmp_path / "work" / "out", targets) is None
    assert fleet.out_inside_target(plain / "out", targets) == targets[0]


def test_schema_file_matches_the_type():
    assert SCHEMA_PATH.read_text(encoding="utf-8") == render(), (
        "regenerate: cd skills/assess && uv run --locked python scripts/lib/fleet_schema.py")


def test_schema_rejects_an_inconsistent_row(tmp_path):
    row = fleet._row(Target("g/r", tmp_path), reason=fleet.MISSING_CHECKOUT)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({**row, "status": "ok"}, build_schema())
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({**row, "reason": "git said something long"}, build_schema())
