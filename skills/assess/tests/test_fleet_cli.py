"""End-to-end tests for skills/fleet/scripts/fleet.py over real assess_signals runs (issue #567)."""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import ModuleType

import jsonschema
import pytest

from assess_test_support import pin_fleet_run_date
from lib.counts_only.content_free import classify
from lib.fleet.fleet_cleanup import _rmtree
from lib.fleet.fleet_schema import build_schema
from test_fleet import make_repo

FLEET_SCRIPT = Path(__file__).resolve().parents[2] / "fleet" / "scripts" / "fleet.py"

LEVELS = """\
version = "fixture-1"

[[rows]]
id = "org2fa"
title = "Two-factor"
scope = "org"

  [[rows.levels]]
  level = 1
  criteria = [{ signal = "two_factor.value", op = "eq", value = "on" }]
"""


def _load_cli() -> ModuleType:
    """``fleet.py`` loaded once as ``sys.modules["fleet_cli"]``. pytest collects this file as
    ``tests.test_fleet_cli`` while other test files import it as ``test_fleet_cli``, so one
    shared module keeps the conftest date pin (#1029) on the object every test calls."""
    if "fleet_cli" in sys.modules:
        return sys.modules["fleet_cli"]
    spec = importlib.util.spec_from_file_location("fleet_cli", FLEET_SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["fleet_cli"] = module
    spec.loader.exec_module(module)
    return module


fleet_cli = _load_cli()
UTC_TODAY = fleet_cli.utc_today  # the real clock, before any test pins it (#1029)


@pytest.fixture(scope="module")
def froot(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The contract's FROOT: grpa/svc1, grpa/svc2, grpb/lib1 in the ghsync layout."""
    root = tmp_path_factory.mktemp("fleet") / "froot"
    make_repo(root, "grpa/svc1", files={
        "CLAUDE.md": "".join(f"line {i}\n" for i in range(20)),
        ".claude-plugin/marketplace.json": json.dumps({"name": "mkt-a", "plugins": []}),
    })
    make_repo(root, "grpa/svc2", files={
        ".claude-plugin/marketplace.json": json.dumps({"name": "mkt-b", "plugins": []}),
    })
    make_repo(root, "grpb/lib1", files={
        ".claude/settings.json": json.dumps(
            {"extraKnownMarketplaces": {"mkt-a": {"source": {"source": "git", "url": "grpa/svc1"}}}}),
    })
    return root


def _snapshot(root: Path) -> dict[str, str]:
    """Every checkout's status and full file listing (including .git), to prove no writes."""
    state: dict[str, str] = {}
    # The listing is read first and this status runs with optional locks off:
    # a plain ``git status`` would itself refresh a racy .git/index.
    for checkout in sorted(root.glob("*/*/*-main")):
        listing = sorted(f"{p.relative_to(checkout)}:{p.stat().st_mtime_ns}"
                         for p in checkout.rglob("*") if p.is_file())
        status = subprocess.run(["git", "-C", str(checkout), "status", "--porcelain", "--ignored"],
                                capture_output=True, text=True, encoding="utf-8", errors="surrogateescape", check=True,
                                env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"}).stdout
        state[str(checkout)] = status + "\n".join(listing)
    return state


def _rows(out: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in (out / "repos.jsonl").read_text(encoding="utf-8").splitlines()]


def _long_strings(value: object, pattern: str = "") -> list[str]:
    """Strings over 40 characters whose field the counts-only rules do not classify as a path."""
    if isinstance(value, str):
        return [pattern] if len(value) > 40 and classify(pattern) is None else []
    if isinstance(value, dict):
        return [s for k, v in value.items() for s in _long_strings(v, f"{pattern}.{k}" if pattern else k)]
    if isinstance(value, list):
        return [s for v in value for s in _long_strings(v, pattern)]
    return []


def test_root_run_is_byte_identical_and_matches_the_list_run(froot, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "rub.toml").write_text(LEVELS, encoding="utf-8")
    before = _snapshot(froot)

    assert fleet_cli.main([str(froot), "--levels", "rub.toml", "--out", "out1"]) == 0
    assert fleet_cli.main([str(froot), "--levels", "rub.toml", "--out", "out2", "--jobs", "1"]) == 0
    listing = tmp_path / "list.txt"
    listing.write_text("".join(f"{c}\n" for c in sorted(froot.glob("*/*/*-main"))), encoding="utf-8")
    assert fleet_cli.main(["--from-list", str(listing), "--levels", "rub.toml", "--out", "out3"]) == 0

    first = (tmp_path / "out1" / "repos.jsonl").read_bytes()
    assert first == (tmp_path / "out2" / "repos.jsonl").read_bytes()
    assert first == (tmp_path / "out3" / "repos.jsonl").read_bytes()
    assert sorted(p.name for p in (tmp_path / "out1").iterdir()) == [
        "achievements.json", "agent_clusters.json", "condition_effect.json", "demotions.json", "fleet-data.json",
        "fleet-view.html", "groups.jsonl", "level.json", "report.md", "repos.jsonl", "run.json", "shared-tooling.svg", "tag-heatmap.svg",
        "tag-profile.svg"]
    for name in ("report.md", "shared-tooling.svg", "tag-profile.svg", "tag-heatmap.svg", "agent_clusters.json",
                 "fleet-data.json", "achievements.json", "level.json",
                 "condition_effect.json", "demotions.json", "fleet-view.html"):  # byte-identical across runs
        drawn = (tmp_path / "out1" / name).read_bytes()
        assert drawn == (tmp_path / "out2" / name).read_bytes()
        assert drawn == (tmp_path / "out3" / name).read_bytes()
    report = (tmp_path / "out1" / "report.md").read_text(encoding="utf-8")
    assert "[![Shared tooling](./shared-tooling.svg)](./shared-tooling.svg)" in report
    assert "Not drawn: skipped: the maturity model has no team practices" in report
    groups = (tmp_path / "out1" / "groups.jsonl").read_bytes()
    assert groups == (tmp_path / "out2" / "groups.jsonl").read_bytes()
    assert groups == (tmp_path / "out3" / "groups.jsonl").read_bytes()
    stamp = (tmp_path / "out1" / "run.json").read_bytes()
    assert stamp == (tmp_path / "out2" / "run.json").read_bytes()
    assert stamp == (tmp_path / "out3" / "run.json").read_bytes()
    assert json.loads(stamp)["levels_version"] == "fixture-1"
    assert str(froot).encode() not in stamp
    assert _snapshot(froot) == before, "the fleet run wrote into a target"

    rows = _rows(tmp_path / "out1")
    assert [(r["repo"], r["group"], r["status"]) for r in rows] == [
        ("grpa/svc1", "grpa", "ok"), ("grpa/svc2", "grpa", "ok"), ("grpb/lib1", "grpb", "ok")]
    schema = build_schema()
    for row in rows:
        jsonschema.validate(row, schema)
        assert _long_strings(row) == []
        assert str(froot) not in json.dumps(row)
    svc1 = rows[0]["scans"]
    assert isinstance(svc1, dict)
    assert svc1["shared_tooling"]["marketplace_ids"] == ["marketplace:mkt-a"]
    assert svc1["nested_instructions"]["budget"]["claude_code"]["files"] == ["CLAUDE.md"]


TEAM_LEVELS = """\
version = "diagrams-e2e-1"

[[rows]]
id = "instructions"
title = "Agent instructions"
scope = "team"
aggregation = "min"

  [[rows.levels]]
  level = 1
  criteria = [{ signal = "nested_instructions.budget.claude_code.lines", op = "gt", value = 0 }]
"""


def _ci(ref: str) -> str:
    return f"include:\n  - project: grp/ci-templates\n    ref: {ref}\n    file: /build.yml\n"


class _MidnightClock(datetime):
    """A UTC clock that reads 23:59:59Z on its first read and 00:00:01Z the next day after."""

    reads = 0

    @classmethod
    def now(cls, tz: object = None) -> _MidnightClock:  # type: ignore[override]
        cls.reads += 1
        late = cls(2026, 11, 22, 23, 59, 59, tzinfo=timezone.utc)
        return late if cls.reads == 1 else late + timedelta(seconds=2)  # type: ignore[return-value]


def _stamped_dates(froot: Path, tmp_path: Path, *outs: str) -> list[str]:
    for out in outs:
        assert fleet_cli.main([str(froot), "--no-history", "--no-diagrams", "--out", str(tmp_path / out)]) == 0
    return [json.loads((tmp_path / out / "run.json").read_text(encoding="utf-8"))["scan_date"] for out in outs]


def test_the_autouse_pin_reaches_the_module_this_file_runs() -> None:
    """#1029: the conftest pin holds here without an explicit call, under either import name."""
    assert fleet_cli is sys.modules["fleet_cli"]
    assert fleet_cli.utc_today is not UTC_TODAY
    assert fleet_cli.utc_today() == UTC_TODAY()


def test_runs_either_side_of_utc_midnight_stamp_one_date_once_pinned(froot, tmp_path, monkeypatch):
    """#1029: a byte-identical test whose runs cross 00:00Z. On the real clock the second run
    is stamped a day later; with the date pinned (``conftest._one_fleet_run_date``, which
    every test gets) both runs carry the first date and their stamps match byte for byte."""
    monkeypatch.setattr(fleet_cli, "datetime", _MidnightClock)
    monkeypatch.setattr(fleet_cli, "utc_today", UTC_TODAY)
    monkeypatch.setattr(_MidnightClock, "reads", 0)
    assert _stamped_dates(froot, tmp_path, "a1", "a2") == ["2026-11-22", "2026-11-23"]  # the flake

    monkeypatch.setattr(_MidnightClock, "reads", 0)
    assert pin_fleet_run_date(monkeypatch, fleet_cli).isoformat() == "2026-11-22"
    assert _stamped_dates(froot, tmp_path, "b1", "b2") == ["2026-11-22", "2026-11-22"]
    for name in ("run.json", "fleet-data.json"):
        assert (tmp_path / "b1" / name).read_bytes() == (tmp_path / "b2" / name).read_bytes(), name


def test_diagrams_are_drawn_from_a_real_run_and_byte_identical(tmp_path, monkeypatch):
    root = tmp_path / "droot"
    make_repo(root, "grpa/svc1", files={"CLAUDE.md": "rules\n", ".gitlab-ci.yml": _ci("v4")})
    make_repo(root, "grpa/svc2", files={".gitlab-ci.yml": _ci("v4")})
    make_repo(root, "grpb/lib1", files={".gitlab-ci.yml": _ci("v5")})
    monkeypatch.chdir(tmp_path)
    (tmp_path / "levels.toml").write_text(TEAM_LEVELS, encoding="utf-8")
    for out in ("d1", "d2"):
        assert fleet_cli.main([str(root), "--levels", "levels.toml", "--out", out, "--no-history"]) == 0
    for name in ("quadrant.svg", "shared-tooling.svg", "report.md"):
        assert (tmp_path / "d1" / name).read_bytes() == (tmp_path / "d2" / name).read_bytes()
    rows = {r["repo"]: r for r in _rows(tmp_path / "d1")}
    scans = rows["grpb/lib1"]["scans"]
    assert isinstance(scans, dict)
    assert isinstance(scans["ai_usage"]["total_commits"], int)
    assert [e["ref"] for e in scans["shared_tooling"]["ci_includes"]] == ["v5"]
    quadrant = (tmp_path / "d1" / "quadrant.svg").read_text(encoding="utf-8")
    assert "grpa/svc1: " in quadrant  # plotted, with its tooltip
    tooling = (tmp_path / "d1" / "shared-tooling.svg").read_text(encoding="utf-8")
    assert "1 pinned off the fleet's most common ref (dashed)" in tooling
    assert 'stroke-dasharray="5 4"><title>grpb/lib1 → gitlab_project:grp/ci-templates @ v5' in tooling


def test_the_slope_chart_is_drawn_only_with_compare(tmp_path, monkeypatch, capsys):
    root = tmp_path / "sroot"
    make_repo(root, "grpa/svc1", files={"CLAUDE.md": "rules\n"})
    make_repo(root, "grpb/lib1")
    monkeypatch.chdir(tmp_path)
    (tmp_path / "levels.toml").write_text(TEAM_LEVELS, encoding="utf-8")
    base = [str(root), "--levels", "levels.toml", "--history-dir", "hist"]
    assert fleet_cli.main([*base, "--out", "first"]) == 0
    assert "slope.svg skipped: no earlier run compared" in capsys.readouterr().err
    first = (tmp_path / "first" / "report.md").read_text(encoding="utf-8")
    assert "Not drawn: skipped: no earlier run compared (no --compare, or no history entry matched this run)." in first
    assert "layer-bars" not in first and "by layer" not in first.lower()  # #1073: the layer bars are retired
    assert not (tmp_path / "first" / "slope.svg").exists()
    assert fleet_cli.main([*base, "--out", "second", "--compare"]) == 0
    slope = (tmp_path / "second" / "slope.svg").read_text(encoding="utf-8")
    assert "grpa 1.00" in slope and "grpb 0.00" in slope
    report = (tmp_path / "second" / "report.md").read_text(encoding="utf-8")
    assert "[![Before and after](./slope.svg)](./slope.svg)" in report


def test_a_raising_renderer_keeps_exit_0_and_the_history(froot, tmp_path, monkeypatch, capsys):
    import lib.fleet.fleet_tooling_map as fleet_tooling_map

    def bug(*_args: object) -> str:
        raise RuntimeError("render bug")
    monkeypatch.setattr(fleet_tooling_map, "render_tooling_map", bug)
    monkeypatch.chdir(tmp_path)
    assert fleet_cli.main([str(froot), "--out", "out", "--history-dir", "hist"]) == 0
    assert sorted(p.name for p in (tmp_path / "hist").glob("*/*/*")) == [
        "fleet-data.json", "groups.jsonl", "repos.jsonl", "run.json"]
    report = (tmp_path / "out" / "report.md").read_text(encoding="utf-8")
    assert "Not drawn: skipped: the renderer raised RuntimeError." in report
    assert "shared-tooling.svg skipped: the renderer raised RuntimeError." in capsys.readouterr().err


def test_no_diagrams_draws_nothing_and_removes_a_previous_runs(froot, tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert fleet_cli.main([str(froot), "--out", "out", "--no-history"]) == 0
    assert (tmp_path / "out" / "report.md").is_file()
    assert "fleet: diagrams -> " in capsys.readouterr().err
    assert fleet_cli.main([str(froot), "--out", "out", "--no-history", "--no-diagrams"]) == 0
    assert sorted(p.name for p in (tmp_path / "out").iterdir()) == [
        "achievements.json", "agent_clusters.json", "fleet-data.json", "groups.jsonl", "repos.jsonl", "run.json"]


def _seed_earlier(entry: Path, dates: tuple[str, ...]) -> None:
    """Copy a history entry to earlier scan dates, as if the fleet ran on those days."""
    for date in dates:
        earlier = entry.parent / date
        shutil.copytree(entry, earlier)
        stamp = json.loads((earlier / "run.json").read_text(encoding="utf-8"))
        (earlier / "run.json").write_text(json.dumps({**stamp, "scan_date": date}), encoding="utf-8")


def test_trend_lines_need_three_dated_runs_in_the_history(tmp_path, monkeypatch, capsys):
    root = tmp_path / "troot"
    make_repo(root, "grpa/svc1", files={"CLAUDE.md": "rules\n"})
    make_repo(root, "grpb/lib1")
    monkeypatch.chdir(tmp_path)
    (tmp_path / "levels.toml").write_text(TEAM_LEVELS, encoding="utf-8")
    args = [str(root), "--levels", "levels.toml", "--history-dir", "hist"]
    assert fleet_cli.main([*args, "--out", "o1"]) == 0
    report = (tmp_path / "o1" / "report.md").read_text(encoding="utf-8")
    assert "Not drawn: skipped: 1 comparable dated run(s), the trend needs at least 3" in report
    assert "trend.svg skipped: 1 comparable" in capsys.readouterr().err
    (today,) = (tmp_path / "hist" / "grpa+grpb").iterdir()
    _seed_earlier(today, ("2000-01-01", "2000-02-01"))
    assert fleet_cli.main([*args, "--out", "o2"]) == 0
    assert fleet_cli.main([*args, "--out", "o3"]) == 0
    trend = (tmp_path / "o2" / "trend.svg").read_bytes()
    assert trend == (tmp_path / "o3" / "trend.svg").read_bytes()
    assert b"3 dated runs, 2000-01-01 to " in trend
    assert "[![Group trends](./trend.svg)](./trend.svg)" in (tmp_path / "o2" / "report.md").read_text(encoding="utf-8")
    assert fleet_cli.main([*args, "--out", "o2", "--no-history"]) == 0
    assert not (tmp_path / "o2" / "trend.svg").exists()
    assert "the run is not kept in the run history" in (tmp_path / "o2" / "report.md").read_text(encoding="utf-8")


def test_an_unreadable_history_is_no_trend_never_a_failure(tmp_path, monkeypatch, capsys):
    from fleet_trends_fixture import CURRENT_GROUPS, CURRENT_STAMP

    def boom(*_args: object) -> None:
        raise PermissionError("denied")
    monkeypatch.setattr(fleet_cli, "history_runs", boom)
    assert fleet_cli._trend_history(tmp_path, tmp_path, CURRENT_STAMP, CURRENT_GROUPS, []) is None
    assert "fleet: diagrams: run history not read (PermissionError)" in capsys.readouterr().err
    assert fleet_cli._trend_history(None, None, CURRENT_STAMP, CURRENT_GROUPS, []) is None


def test_a_diagram_write_failure_keeps_exit_0(tmp_path, monkeypatch, capsys):
    def boom(*_args: object) -> None:
        raise OSError("disk full")
    monkeypatch.setattr(fleet_cli, "write_diagrams", boom)
    fleet_cli._diagrams(tmp_path, False, None, [], "6mo")
    assert "fleet: diagrams: not written (OSError: disk full)" in capsys.readouterr().err

    def bug(*_args: object, **_kwargs: object) -> None:
        raise ValueError("render bug")
    monkeypatch.setattr(fleet_cli, "build_diagrams", bug)
    fleet_cli._diagrams(tmp_path, False, None, [], "6mo")
    assert "fleet: diagrams: not written (ValueError: render bug)" in capsys.readouterr().err


def test_a_broken_checkout_is_a_failed_row_and_the_run_completes(froot, tmp_path, capsys):
    root = tmp_path / "froot"
    shutil.copytree(froot, root, symlinks=True)
    lib1 = root / "grpb" / "lib1" / "lib1-main"
    _rmtree(lib1 / ".git")  # git objects are read-only on Windows
    (lib1 / ".git").write_text("", encoding="utf-8")

    assert fleet_cli.main([str(root), "--out", str(tmp_path / "out")]) == 0

    rows = {r["repo"]: r for r in _rows(tmp_path / "out")}
    assert rows["grpb/lib1"]["status"] == "failed"
    assert rows["grpb/lib1"]["reason"] == "not_a_git_work_tree"
    assert rows["grpa/svc1"]["status"] == rows["grpa/svc2"]["status"] == "ok"
    assert rows["grpa/svc1"]["scans"]
    assert "1 failed" in capsys.readouterr().err


def test_hash_salt_hashes_paths_and_full_keeps_text(froot, tmp_path):
    assert fleet_cli.main([str(froot), "--out", str(tmp_path / "h"), "--hash-salt", "s",
                           "--scans", "shared_tooling"]) == 0
    hashed = _rows(tmp_path / "h")[0]["scans"]
    assert isinstance(hashed, dict)
    assert hashed["shared_tooling"]["marketplaces"][0]["path"] != ".claude-plugin/marketplace.json"
    assert fleet_cli.main([str(froot), "--out", str(tmp_path / "f"), "--full",
                           "--scans", "shared_tooling", "--window", "90d"]) == 0
    full = _rows(tmp_path / "f")[0]["scans"]
    assert isinstance(full, dict)
    assert full["shared_tooling"]["marketplaces"][0]["path"] == ".claude-plugin/marketplace.json"


@pytest.mark.parametrize("argv", [
    ["--out", "o"],
    ["ROOT", "--out", "o", "--full", "--hash-paths"],
    ["ROOT", "--out", "o", "--hash-salt", ""],
    ["ROOT", "--out", "o", "--jobs", "0"],
])
def test_usage_errors(froot, argv):
    with pytest.raises(SystemExit) as exc:
        fleet_cli.main([str(froot) if a == "ROOT" else a for a in argv])
    assert exc.value.code == 2


@pytest.mark.parametrize("case", ["no_root", "no_list", "empty", "levels", "scans", "window", "inside"])
def test_refusals_exit_2(froot, tmp_path, case, capsys):
    (tmp_path / "bad.toml").write_text('version = "x"\n[[rows]]\nid = "r"\n', encoding="utf-8")
    (tmp_path / "empty").mkdir()
    argv = {
        "no_root": [str(tmp_path / "absent")],
        "no_list": ["--from-list", str(tmp_path / "absent.txt")],
        "empty": [str(tmp_path / "empty")],
        "levels": [str(froot), "--levels", str(tmp_path / "bad.toml")],
        "scans": [str(froot), "--scans", "no_such_scan"],
        "window": [str(froot), "--window", "soon"],
        "inside": [str(froot)],
    }[case]
    out = froot / "grpa" / "svc1" / "svc1-main" / "x" if case == "inside" else tmp_path / "out"
    assert fleet_cli.main([*argv, "--out", str(out)]) == 2
    assert capsys.readouterr().err.startswith("error:")
    assert not out.exists()


def test_script_runs_standalone(froot, tmp_path):
    """The documented invocation: the script finds the assess lib on its own."""
    env = {**os.environ, "PYTHONPATH": ""}
    done = subprocess.run([sys.executable, str(FLEET_SCRIPT), str(froot), "--out", str(tmp_path / "o"),
                           "--scans", "agent_ops"], capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, check=False)
    assert done.returncode == 0, done.stderr
    assert len(_rows(tmp_path / "o")) == 3


def test_missing_layer_package_prints_the_pip_hint_once(froot, tmp_path, monkeypatch, capsys):
    from lib import tool_lookup

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(tool_lookup, "missing_packages", lambda **_: ["grimp"])
    assert fleet_cli.main([str(froot), "--out", "out", "--no-history", "--no-diagrams"]) == 0
    err = capsys.readouterr().err
    assert err.count("python -m pip install --user grimp") == 1


def test_agent_clusters_are_unnamed_unless_asked(froot, tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert fleet_cli.main([str(froot), "--out", "out", "--no-history", "--no-diagrams"]) == 0
    assert json.loads((tmp_path / "out" / "agent_clusters.json").read_text(encoding="utf-8"))["named"] is False
    assert "fleet: agent clusters: " in capsys.readouterr().err
    assert fleet_cli.main([str(froot), "--out", "out", "--no-history", "--no-diagrams",
                           "--name-agent-clusters"]) == 0
    assert json.loads((tmp_path / "out" / "agent_clusters.json").read_text(encoding="utf-8"))["named"] is True


def test_similar_agent_builders_in_two_groups_are_one_unnamed_cluster(tmp_path, monkeypatch):
    root = tmp_path / "agents"
    loop = 'if response.stop_reason == "tool_use":\n    pass\n'
    for repo in ("team-alpha/ticket-agent", "team-beta/mr-agent"):
        make_repo(root, repo, files={"requirements.txt": "anthropic\npython-gitlab\njira\n", "agent.py": loop})
    monkeypatch.chdir(tmp_path)
    assert fleet_cli.main([str(root), "--out", "out", "--no-history", "--no-diagrams", "--jobs", "1"]) == 0
    text = (tmp_path / "out" / "agent_clusters.json").read_text(encoding="utf-8")
    result = json.loads(text)
    assert [(c["size"], c["groups"], c["pairing"]) for c in result["clusters"]] == [(2, 2, True)]
    for name in ("team-alpha", "team-beta", "ticket-agent", "mr-agent"):
        assert name not in text
    assert fleet_cli.main([str(root), "--out", "out", "--no-history", "--no-diagrams", "--jobs", "1",
                           "--name-agent-clusters"]) == 0
    named = json.loads((tmp_path / "out" / "agent_clusters.json").read_text(encoding="utf-8"))
    assert named["clusters"][0]["repo_ids"] == ["team-alpha/ticket-agent", "team-beta/mr-agent"]


def test_include_root_is_root_unless_delete_after(tmp_path: Path) -> None:
    """GitLab includes (#766) resolve against ROOT; --delete-after could remove a template
    clone before a dependant is scanned, so then no root is passed."""
    import argparse
    assert fleet_cli.include_root(argparse.Namespace(root=str(tmp_path), delete_after=False)) == tmp_path.resolve()
    assert fleet_cli.include_root(argparse.Namespace(root=str(tmp_path), delete_after=True)) is None
    assert fleet_cli.include_root(argparse.Namespace(root=None, delete_after=False)) is None


def test_a_root_run_resolves_gitlab_includes_from_sibling_clones(tmp_path, monkeypatch):
    """#766: ROOT reaches every scan, so a template cloned in the root is read."""
    monkeypatch.chdir(tmp_path)
    root = tmp_path / "gi"
    tpl = make_repo(root, "grp/ci-templates", files={
        "templates/test.yml": "unit:\n  script:\n    - pytest -q\n",
        "templates/review.yml": "include:\n  - component: gl.test/tools/ai-review@1.2.0\n"})
    subprocess.run(["git", "-C", str(tpl), "branch", "-M", "main"], check=True)  # the pinned ref

    def ci(project: str, file: str) -> dict[str, str]:
        return {".gitlab-ci.yml": f"include:\n  - project: {project}\n    file: {file}\n    ref: main\n",
                "src/a.py": "x = 1\n"}
    make_repo(root, "grp/app", files=ci("grp/ci-templates", "/templates/test.yml"))
    make_repo(root, "grp/rev", files=ci("grp/ci-templates", "/templates/review.yml"))
    make_repo(root, "grp/gone", files=ci("grp/not-cloned", "/t.yml"))
    assert fleet_cli.main([str(root), "--out", "o", "--scans", "ci_checks,review_automation",
                           "--no-diagrams", "--no-history"]) == 0
    rows = {r["repo"]: r["scans"] for r in map(json.loads, (tmp_path / "o" / "repos.jsonl").read_text(encoding="utf-8").splitlines())}
    assert rows["grp/app"]["ci_checks"]["test_or_lint_configured"] is True
    assert rows["grp/gone"]["ci_checks"]["test_or_lint_configured"] is None
    assert rows["grp/rev"]["review_automation"]["present"] is True
