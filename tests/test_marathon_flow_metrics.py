"""Behavioural tests for skills/marathon/scripts/flow_metrics.py (issue #1090).

Every input is a saved file, so each test builds a tracking file and a GitHub
response directory in tmp_path and checks the computed numbers by hand.
"""
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "skills" / "marathon" / "scripts" / "flow_metrics.py"

_spec = importlib.util.spec_from_file_location("flow_metrics", SCRIPT)
fm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fm)


def run(sha, name, conclusion, updated_at):
    return {"head_sha": sha, "name": name, "conclusion": conclusion,
            "updated_at": updated_at, "event": "pull_request"}


def write(path: Path, data) -> None:
    path.write_text(json.dumps(data), encoding="utf-8")


def build(tmp_path: Path):
    gh = tmp_path / "gh"
    gh.mkdir()
    # PR 1: two pushes (a, b), one merge of main (b), reviews on both heads.
    # First green: head a at 10:30 (both workflows); merged at 11:00 -> 30 min.
    write(gh / "1.pull.json", {"created_at": "2026-11-22T10:00:00Z", "merged_at": "2026-11-22T11:00:00Z",
                               "closed_at": "2026-11-22T11:00:00Z", "head": {"sha": "b"}})
    write(gh / "1.commits.json", [{"sha": "a", "parents": [{}]}, {"sha": "b", "parents": [{}, {}]}])
    write(gh / "1.reviews.json", [{"commit_id": "a"}, {"commit_id": "a"}, {"commit_id": "b"}])
    write(gh / "1.runs.json", {"workflow_runs": [
        run("a", "tests", "success", "2026-11-22T10:20:00Z"),
        run("a", "floor", "success", "2026-11-22T10:30:00Z"),
        run("b", "tests", "success", "2026-11-22T10:50:00Z"),
        run("b", "floor", "success", "2026-11-22T10:55:00Z"),
        {"head_sha": "a", "name": "release", "conclusion": "success",
         "updated_at": "2026-11-22T09:00:00Z", "event": "push"},
    ]})
    # PR 2: green only on its second head; still open at the end of the run.
    write(gh / "2.pull.json", {"created_at": "2026-11-22T10:30:00Z", "merged_at": None,
                               "closed_at": None, "head": {"sha": "d"}})
    tracking = {
        "meta": {"tag": "run-x"},
        "flow": {
            "started_at": "2026-11-22T10:00:00Z",
            "ended_at": "2026-11-22T12:00:00Z",
            "units": {"task-1": {"pr": 1, "spawned_at": "2026-11-22T09:45:00Z"},
                      "task-2": {"pr": 2}},
            "events": [
                {"type": "semantic_conflict", "at": "2026-11-22T10:40:00Z", "pr": 1},
                {"type": "recheck_failure", "at": "2026-11-22T10:41:00Z", "pr": 1},
                {"type": "contract_refreeze", "at": "2026-11-22T10:42:00Z"},
                {"type": "contract_refreeze", "at": "2026-11-22T10:43:00Z"},
                {"type": "main_red", "start": "2026-11-22T11:00:00Z", "end": "2026-11-22T11:25:00Z"},
                {"type": "main_red", "start": "2026-11-22T11:50:00Z"},
                {"type": "load", "at": "2026-11-22T10:10:00Z", "load1": 40.5},
                {"type": "load", "at": "2026-11-22T11:10:00Z", "load1": 122.0},
            ],
            "tokens": {"total": 1000},
        },
    }
    track = tmp_path / "pr-tracking.json"
    write(track, tracking)
    return track, gh


def test_per_pr_metrics(tmp_path):
    track, gh = build(tmp_path)
    result = fm.compute(json.loads(track.read_text()), gh)
    pr1, pr2 = result["per_pr"]
    assert pr1["pushes"] == 2  # the push-event run is not a PR push
    assert pr1["merges_of_main"] == 1
    assert pr1["review_rounds"] == 2
    assert pr1["wait_green_to_merged_min"] == 30.0
    assert pr1["spawn_to_pr_min"] == 15.0
    # Missing files give null, never a false 0.
    assert pr2["pushes"] is None and pr2["merges_of_main"] is None
    assert pr2["wait_green_to_merged_min"] is None and pr2["spawn_to_pr_min"] is None


def test_per_run_metrics(tmp_path):
    track, gh = build(tmp_path)
    per_run = fm.compute(json.loads(track.read_text()), gh)["per_run"]
    assert per_run["merged"] == 1
    assert per_run["throughput_per_hour"] == 0.5  # 1 merge over 2 hours
    # PR 1 open 10:00-11:00, PR 2 open 10:30-12:00: 2.5 PR-hours over 2 hours.
    assert per_run["mean_wip"] == 1.25
    assert per_run["peak_wip"] == 2
    assert per_run["semantic_conflicts"] == 1
    assert per_run["recheck_failures"] == 1
    assert per_run["contract_refreezes"] == 2
    # 25 min closed incident plus an open one counted to the run end (10 min).
    assert per_run["main_red_incidents"] == 2
    assert per_run["main_red_min"] == 35.0
    assert per_run["load_peak"] == 122.0
    assert per_run["load_peak_at"] == "2026-11-22T11:10:00Z"
    assert per_run["tokens_per_merge"] == 1000


def test_first_green_needs_every_workflow_green_at_merge():
    runs = [run("a", "tests", "success", "2026-11-22T10:20:00Z"),
            run("a", "floor", "failure", "2026-11-22T10:30:00Z"),
            run("b", "tests", "success", "2026-11-22T10:50:00Z"),
            run("b", "floor", "success", "2026-11-22T10:55:00Z")]
    assert fm.first_green(runs, "b") == fm.parse_ts("2026-11-22T10:55:00Z")
    assert fm.first_green(runs, "z") is None


def test_first_green_restarts_after_a_red_head():
    # a green 10:30, b red 11:00, c green 12:00: rework after a is not wait.
    runs = [run("a", "tests", "success", "2026-11-22T10:30:00Z"),
            run("b", "tests", "failure", "2026-11-22T11:00:00Z"),
            run("c", "tests", "success", "2026-11-22T12:00:00Z")]
    assert fm.first_green(runs, "c") == fm.parse_ts("2026-11-22T12:00:00Z")


def test_first_green_ignores_a_failure_rerun_to_green():
    runs = [run("a", "tests", "failure", "2026-11-22T10:20:00Z"),
            run("a", "tests", "success", "2026-11-22T10:30:00Z"),
            run("b", "tests", "success", "2026-11-22T11:00:00Z")]
    assert fm.first_green(runs, "b") == fm.parse_ts("2026-11-22T10:30:00Z")


def test_truncated_runs_read_null(tmp_path):
    track, gh = build(tmp_path)
    runs = json.loads((gh / "1.runs.json").read_text())
    runs["total_count"] = len(runs["workflow_runs"]) + 1
    write(gh / "1.runs.json", runs)
    pr1 = fm.compute(json.loads(track.read_text()), gh)["per_pr"][0]
    assert pr1["pushes"] is None
    assert pr1["wait_green_to_merged_min"] is None
    assert pr1["merges_of_main"] == 1


def test_tokens_absent_reads_null(tmp_path):
    track, gh = build(tmp_path)
    tracking = json.loads(track.read_text())
    del tracking["flow"]["tokens"]
    assert fm.compute(tracking, gh)["per_run"]["tokens_per_merge"] is None


def test_cli_markdown_compares_with_previous_and_names_largest_wait(tmp_path):
    track, gh = build(tmp_path)
    previous = tmp_path / "previous.json"
    write(previous, {"per_run": {"throughput_per_hour": 2.0, "mean_wip": 1.0}})
    out = subprocess.run(
        [sys.executable, str(SCRIPT), "--tracking", str(track), "--github", str(gh),
         "--previous", str(previous)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", check=True,
    ).stdout
    assert "| #1 | 2 | 1 | 2 | 30.0 | 15.0 |" in out
    assert "| throughput_per_hour | 0.5 | 2.0 | -1.5 |" in out
    assert "| mean_wip | 1.25 | 1.0 | +0.25 |" in out
    assert "| semantic_conflicts | 1 | n/a | n/a |" in out
    assert "Largest wait: PR #1, 30.0 min" in out


def test_cli_json_is_stable(tmp_path):
    track, gh = build(tmp_path)
    args = [sys.executable, str(SCRIPT), "--tracking", str(track), "--github", str(gh), "--format", "json"]
    first = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace", check=True).stdout
    second = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace", check=True).stdout
    assert first == second
    assert json.loads(first)["run"] == "run-x"


def waves_fixture():
    # Wave 1: the DAG allowed 5 units; two combines over lib/shared.py left 3.
    # plugin.json is touched by every PR and merges in order, never combines.
    # Wave 2: no combine.
    return [
        {"wave": 1, "dag_width": 5, "units": 3,
         "combines": [{"units": ["task-1", "task-2"], "hot_files": ["lib/shared.py"]},
                      {"units": ["task-3", "task-4"], "hot_files": ["lib/shared.py", ".claude-plugin/plugin.json"]}],
         "sequential_hot_files": [".claude-plugin/plugin.json"]},
        {"wave": 2, "dag_width": 2, "units": 2, "combines": [],
         "sequential_hot_files": [".claude-plugin/plugin.json"]},
    ]


def test_two_combines_over_one_file_lose_two_units(tmp_path):
    track, gh = build(tmp_path)
    tracking = json.loads(track.read_text())
    tracking["flow"]["waves"] = waves_fixture()
    result = fm.compute(tracking, gh)
    assert result["per_run"]["units_lost_to_combining"] == 2
    assert result["per_run"]["dag_width"] == 7
    assert result["per_run"]["units_after_combining"] == 5
    combine = result["combining"]
    assert [w["units_lost"] for w in combine["per_wave"]] == [2, 0]
    assert combine["hot_files"] == [{"file": "lib/shared.py", "count": 2}]


def test_sequential_hot_file_is_reported_apart(tmp_path):
    track, gh = build(tmp_path)
    tracking = json.loads(track.read_text())
    tracking["flow"]["waves"] = waves_fixture()
    combine = fm.compute(tracking, gh)["combining"]
    assert ".claude-plugin/plugin.json" not in [r["file"] for r in combine["hot_files"]]
    assert combine["sequential_hot_files"] == [{"file": ".claude-plugin/plugin.json", "count": 2}]
    out = fm.render_markdown(fm.compute(tracking, gh), None)
    assert "Top combining hot files: `lib/shared.py` (2 combines, previous n/a)." in out
    assert ("Sequential-merge hot files (merge order, not combining): "
            "`.claude-plugin/plugin.json` (2 waves, previous n/a).") in out
    assert "| 1 | 5 | 3 | 2 | 2 |" in out


def test_tracking_without_waves_reads_combining_null(tmp_path):
    track, gh = build(tmp_path)
    result = fm.compute(json.loads(track.read_text()), gh)
    assert result["combining"] is None
    for key in ("dag_width", "units_after_combining", "units_lost_to_combining"):
        assert result["per_run"][key] is None
    assert "Hot-file combining: n/a" in fm.render_markdown(result, None)


def test_wave_missing_a_count_reads_null_not_zero(tmp_path):
    track, gh = build(tmp_path)
    tracking = json.loads(track.read_text())
    waves = waves_fixture()
    del waves[1]["dag_width"]
    tracking["flow"]["waves"] = waves
    result = fm.compute(tracking, gh)
    assert result["per_run"]["units_lost_to_combining"] is None
    assert [w["units_lost"] for w in result["combining"]["per_wave"]] == [2, None]


def test_combining_compares_with_previous_run(tmp_path):
    track, gh = build(tmp_path)
    tracking = json.loads(track.read_text())
    tracking["flow"]["waves"] = waves_fixture()
    previous = {"per_run": {"units_lost_to_combining": 5},
                "combining": {"hot_files": [{"file": "lib/shared.py", "count": 4}], "sequential_hot_files": []}}
    out = fm.render_markdown(fm.compute(tracking, gh), previous)
    assert "| units_lost_to_combining | 2 | 5 | -3 |" in out
    assert "`lib/shared.py` (2 combines, previous 4)" in out


def test_sequential_exclusion_is_per_wave(tmp_path):
    # lib/gen.py caused a real combine in wave 1; wave 2 merges it in order.
    track, gh = build(tmp_path)
    tracking = json.loads(track.read_text())
    tracking["flow"]["waves"] = [
        {"dag_width": 3, "units": 2, "combines": [{"units": ["task-1", "task-2"], "hot_files": ["lib/gen.py"]}]},
        {"dag_width": 2, "units": 2, "combines": [], "sequential_hot_files": ["lib/gen.py"]},
    ]
    combine = fm.compute(tracking, gh)["combining"]
    assert combine["hot_files"] == [{"file": "lib/gen.py", "count": 1}]
    assert combine["sequential_hot_files"] == [{"file": "lib/gen.py", "count": 1}]


def test_hot_files_written_as_a_string_are_ignored(tmp_path):
    track, gh = build(tmp_path)
    tracking = json.loads(track.read_text())
    tracking["flow"]["waves"] = [{"dag_width": 2, "units": 1,
                                  "combines": [{"units": ["a", "b"], "hot_files": "lib/x.py"}]}]
    assert fm.compute(tracking, gh)["combining"]["hot_files"] == []


def test_bool_counts_are_rejected(tmp_path):
    # JSON `true` is a Python bool, which isinstance(..., int) accepts; it is not a count.
    track, gh = build(tmp_path)
    tracking = json.loads(track.read_text())
    waves = waves_fixture()
    waves[1]["units"] = True
    tracking["flow"]["waves"] = waves
    tracking["flow"]["tokens"] = {"total": True}
    result = fm.compute(tracking, gh)
    assert [w["units_lost"] for w in result["combining"]["per_wave"]] == [2, None]
    for key in ("units_after_combining", "units_lost_to_combining", "tokens_per_merge"):
        assert result["per_run"][key] is None


def test_units_above_dag_width_reads_null_and_names_the_wave(tmp_path):
    track, gh = build(tmp_path)
    tracking = json.loads(track.read_text())
    waves = waves_fixture()
    waves[1]["units"] = 4  # more units than the DAG allowed (2): bad input, not -2 lost
    tracking["flow"]["waves"] = waves
    result = fm.compute(tracking, gh)
    assert [w["units_lost"] for w in result["combining"]["per_wave"]] == [2, None]
    assert result["per_run"]["units_lost_to_combining"] is None
    out = fm.render_markdown(result, None)
    assert "| 2 | 2 | 4 | n/a | 0 |" in out
    assert ("Wave 2 records more units after combining (4) than its DAG width (2), "
            "so its units lost reads n/a.") in out
