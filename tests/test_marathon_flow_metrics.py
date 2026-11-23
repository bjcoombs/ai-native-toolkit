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
