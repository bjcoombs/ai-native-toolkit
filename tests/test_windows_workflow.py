"""Keep the split Windows workflow's required check honest (#938).

`.github/workflows/windows.yml` runs the suites as parallel matrix legs, and one
aggregator job carries the required check name `windows suites + smoke runs`.
Branch protection reads only that name, so the aggregator must report on every
run and must be green only when every leg is green: a failed, cancelled or
skipped leg turns it red. The ghsync/ghreport path gate (#971) stays a step
inside its leg, so no leg is ever skipped at job level on purpose.

The shared `Install ripgrep` step retries choco, then falls back to the pinned
ripgrep GitHub release zip, checked against a pinned SHA256, so a Chocolatey
outage cannot turn every leg red. Both paths install the same version.

The assess and ghsync legs run as shards (tests/pytest_shard.py). Every shard
number 1..N appears once per suite, each sharded suite step passes that leg's
`--shard`, and the steps that must run once (the fleet golden run and the
CPU-budget tests) run in shard 1 alone. tests/test_pytest_shard.py checks that
the N shards of a collection are disjoint and cover it.
"""
import re
from pathlib import Path
from typing import Any

import yaml  # required: the plugin contract job installs pyyaml

REPO = Path(__file__).resolve().parent.parent
WORKFLOW = REPO / ".github" / "workflows" / "windows.yml"
REQUIRED_NAME = "windows suites + smoke runs"
LEGS = {"assess", "scripts-contract", "ghsync", "smoke"}
SHARDS = {"assess": 5, "ghsync": 2}
SHARD_ARG = "-p pytest_shard --shard ${{ matrix.shard }}/${{ matrix.shards }}"
# Steps that run in one shard of their leg: the golden run and the serial
# CPU-budget pass each run once.
SHARD_ONE_STEPS = {
    "Fleet golden run against the committed Linux repos.jsonl",
    "skills/assess CPU-budget tests",
}
# The shared setup every leg runs (plus the unnamed actions/checkout step).
SETUP_STEPS = {
    "Keep LF line endings",
    "Fetch the PR base ref",
    "Set up Python",
    "Install uv",
    "Install ripgrep",
    "Git Bash tools",
    "Python version",
}
GHSYNC_SUITE ="ghsync and ghreport suites under Git Bash"
# Runs in every sharded leg; it only uploads timings.
SHARD_UPLOAD = "Upload the shard's seconds per test file"
# Every non-setup step and the one leg it runs in.
STEP_LEG = {
    "Shell scripts are LF in a CRLF checkout": "smoke",
    "Fleet golden run against the committed Linux repos.jsonl": "assess",
    "skills/assess pytest": "assess",
    "skills/assess CPU-budget tests": "assess",
    "scripts/ pytest": "scripts-contract",
    "plugin contract pytest": "scripts-contract",
    "Decide whether the ghsync and ghreport suites run": "ghsync",
    GHSYNC_SUITE: "ghsync",
    "Standalone skill ZIPs": "smoke",
    "Eval scaffolds build their fixtures": "smoke",
    "Smoke fixture estate": "smoke",
    "Smoke assess_signals.py": "smoke",
    "Smoke assess_core.py": "smoke",
    "Smoke the SVG scripts on Python 3.12": "smoke",
    "Smoke fleet.py over a ghsync-layout root": "smoke",
    "Smoke fleet.py --from-list": "smoke",
    "Smoke the scanners on a path over 260 characters": "smoke",
    "Smoke start_gate.py and complete_gate.py": "smoke",
}


def _jobs() -> dict[str, Any]:
    doc = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    return doc["jobs"]


def _aggregator(jobs: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    named = [(jid, job) for jid, job in jobs.items() if job.get("name") == REQUIRED_NAME]
    assert len(named) == 1, f"exactly one job must be named {REQUIRED_NAME!r}: {named}"
    return named[0]


def _matrix_job(jobs: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    legs = [(jid, job) for jid, job in jobs.items() if "strategy" in job]
    assert len(legs) == 1, f"one matrix job expected: {[jid for jid, _ in legs]}"
    return legs[0]


def test_required_name_is_written_where_the_floor_anchor_reads_it() -> None:
    # scripts/floor_anchor.py reads job names with a line regex, not YAML:
    # a four-space, unquoted `name:` line.
    lines = WORKFLOW.read_text(encoding="utf-8").splitlines()
    assert f"    name: {REQUIRED_NAME}" in lines


def test_matrix_runs_every_leg_without_fail_fast() -> None:
    _, job = _matrix_job(_jobs())
    strategy = job["strategy"]
    assert strategy["fail-fast"] is False
    assert set(strategy["matrix"]) == {"include"}
    assert {leg["suite"] for leg in strategy["matrix"]["include"]} == LEGS
    assert job["timeout-minutes"] == 45
    assert job["runs-on"] == "windows-latest"
    assert "if" not in job, "a job-level if could skip every leg"
    assert REQUIRED_NAME not in str(job.get("name", ""))


def test_aggregator_needs_the_matrix_and_always_reports() -> None:
    jobs = _jobs()
    matrix_id, _ = _matrix_job(jobs)
    _, agg = _aggregator(jobs)
    needs = agg["needs"]
    assert (needs if isinstance(needs, list) else [needs]) == [matrix_id]
    assert agg["if"] == "always()"
    assert "continue-on-error" not in agg


def test_aggregator_passes_only_on_success() -> None:
    jobs = _jobs()
    matrix_id, _ = _matrix_job(jobs)
    _, agg = _aggregator(jobs)
    steps = agg["steps"]
    assert len(steps) == 1
    step = steps[0]
    assert step["env"]["RESULT"] == f"${{{{ needs.{matrix_id}.result }}}}"
    assert '[ "$RESULT" = "success" ]' in step["run"]
    assert "continue-on-error" not in step


def test_ghsync_gate_stays_a_step_in_its_leg() -> None:
    _, job = _matrix_job(_jobs())
    by_id = {s["id"]: s for s in job["steps"] if "id" in s}
    gate = by_id["ghsync_gate"]
    assert "matrix.suite == 'ghsync'" in gate["if"]
    suite = next(s for s in job["steps"] if s.get("name") == "ghsync and ghreport suites under Git Bash")
    assert "steps.ghsync_gate.outputs.run != 'false'" in suite["if"]
    assert "matrix.suite == 'ghsync'" in suite["if"]


def test_every_suite_step_runs_in_exactly_its_leg() -> None:
    # An exact `if:` per step: a typo'd leg name or an extra `&& false` would
    # otherwise drop the step while its leg stays green.
    _, job = _matrix_job(_jobs())
    steps = {s.get("name"): s for s in job["steps"]}
    missing = sorted(set(STEP_LEG) - set(steps))
    assert not missing, f"steps the map expects but the workflow lacks: {missing}"
    for name, leg in STEP_LEG.items():
        extra = " && steps.ghsync_gate.outputs.run != 'false'" if name == GHSYNC_SUITE else ""
        if name in SHARD_ONE_STEPS:
            extra = " && matrix.shard == 1"
        expected = f"${{{{ !cancelled() && matrix.suite == '{leg}'{extra} }}}}"
        assert steps[name].get("if") == expected, name


def test_every_shard_of_a_suite_is_a_leg() -> None:
    # A missing shard number would drop its files from Windows CI while every
    # leg stayed green; a repeat would run them twice.
    _, job = _matrix_job(_jobs())
    legs = job["strategy"]["matrix"]["include"]
    for suite in LEGS:
        mine = [leg for leg in legs if leg["suite"] == suite]
        n = SHARDS.get(suite)
        if n is None:
            assert mine == [{"suite": suite}], suite
            continue
        assert sorted(leg["shard"] for leg in mine) == list(range(1, n + 1)), suite
        assert {leg["shards"] for leg in mine} == {n}, suite
        assert all(set(leg) == {"suite", "shard", "shards"} for leg in mine), suite


def test_sharded_suite_steps_pass_the_leg_shard() -> None:
    _, job = _matrix_job(_jobs())
    steps = {s.get("name"): s for s in job["steps"]}
    for name in ("skills/assess pytest", GHSYNC_SUITE):
        run = " ".join(steps[name]["run"].split())
        assert SHARD_ARG in run, name
        # The legs of a suite share this step; the shard is its only per-leg input.
        assert set(re.findall(r"matrix\.(\w+)", run)) == {"shard", "shards"}, name
        assert "--dist loadfile" in run, name
        assert steps[name]["env"]["PYTHONPATH"] == "${{ github.workspace }}/tests", name
        assert '--shard-record "${{ runner.temp }}/shard-seconds.json"' in run, name
    assessed = " ".join(steps["skills/assess pytest"]["run"].split())
    assert "--shard-weights tests/shard-weights.json" in assessed
    assert (REPO / "skills" / "assess" / "tests" / "shard-weights.json").is_file()
    # No other step shards: an unsharded leg runs its whole suite.
    for name, step in steps.items():
        if name not in ("skills/assess pytest", GHSYNC_SUITE):
            assert "--shard" not in str(step.get("run", "")), name


def test_shard_seconds_upload_never_fails_a_leg() -> None:
    _, job = _matrix_job(_jobs())
    step = next(s for s in job["steps"] if s.get("name") == SHARD_UPLOAD)
    assert step["if"] == "${{ !cancelled() && matrix.shards > 1 }}"
    assert step["continue-on-error"] is True
    assert step["with"]["if-no-files-found"] == "ignore"


def test_leg_names_carry_the_shard() -> None:
    _, job = _matrix_job(_jobs())
    assert job["name"] == (
        "windows / ${{ matrix.suite }}"
        "${{ matrix.shards && format(' {0}/{1}', matrix.shard, matrix.shards) || '' }}"
    )


def test_no_step_names_an_unknown_leg() -> None:
    _, job = _matrix_job(_jobs())
    for step in job["steps"]:
        cond = str(step.get("if", ""))
        for leg in re.findall(r"matrix\.suite == '([^']*)'", cond):
            assert leg in LEGS, f"{step.get('name')}: unknown leg {leg!r}"
        if "matrix.suite" in cond:
            assert step.get("name") in STEP_LEG, f"leg step missing from STEP_LEG: {step.get('name')}"
        else:
            # A step with no leg guard runs in every leg: only shared setup may.
            label = step.get("name", step.get("uses", ""))
            assert label in SETUP_STEPS | {SHARD_UPLOAD} or str(label).startswith("actions/checkout@"), (
                f"{label!r} has no matrix.suite guard, so it runs in every leg"
            )


def test_superseded_pr_runs_cancel() -> None:
    doc = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    assert doc["concurrency"] == {
        "group": "windows-${{ github.event.pull_request.number || github.sha }}",
        "cancel-in-progress": "${{ github.event_name == 'pull_request' }}",
    }


# The ripgrep install: choco with retries, then the pinned GitHub release
# checked against a pinned SHA256. A version bump changes these two constants
# and the step's env together; the SHA256 is computed from the release asset
# and compared with the release's published .sha256 asset.
RG_VERSION = "15.2.0"
RG_SHA256 = "71b2fef860abe467217a538ff31de02f5258807c0129f771846f87bd029aafc5"


def _ripgrep_step() -> dict[str, Any]:
    _, job = _matrix_job(_jobs())
    steps = [s for s in job["steps"] if s.get("name") == "Install ripgrep"]
    assert len(steps) == 1
    return steps[0]


def test_ripgrep_install_pins_one_version_and_checksum() -> None:
    step = _ripgrep_step()
    assert step["shell"] == "pwsh"
    assert step["env"] == {"RG_VERSION": RG_VERSION, "RG_SHA256": RG_SHA256}
    assert re.fullmatch(r"[0-9a-f]{64}", RG_SHA256)
    # Shared setup with no leg guard: every leg needs rg.
    assert "if" not in step
    assert "continue-on-error" not in step


def test_ripgrep_choco_install_retries_the_pinned_version() -> None:
    run = _ripgrep_step()["run"]
    assert "foreach ($attempt in 1..3)" in run
    assert "choco install ripgrep --version $env:RG_VERSION -y --no-progress" in run
    assert "if ($LASTEXITCODE -eq 0) { $installed = $true; break }" in run
    assert "Start-Sleep" in run


def test_ripgrep_falls_back_to_the_checked_github_release() -> None:
    run = _ripgrep_step()["run"]
    fallback = run[run.index("if (-not $installed)"):]
    assert (
        "https://github.com/BurntSushi/ripgrep/releases/download/$env:RG_VERSION/$name.zip"
        in fallback
    )
    assert '$name = "ripgrep-$env:RG_VERSION-x86_64-pc-windows-msvc"' in fallback
    # The hash check sits between the download and the extract, and a
    # mismatch exits before rg reaches PATH.
    download = fallback.index("Invoke-WebRequest")
    check = fallback.index("Get-FileHash -Path $zip -Algorithm SHA256")
    mismatch = fallback.index("if ($hash -ne $env:RG_SHA256)")
    extract = fallback.index("Expand-Archive")
    path = fallback.index("$env:GITHUB_PATH")
    assert download < check < mismatch < extract < path
    assert "exit 1" in fallback[mismatch:extract]


def test_ripgrep_step_fails_unless_rg_is_the_pinned_version() -> None:
    run = _ripgrep_step()["run"]
    tail = run[run.rindex("rg --version"):]
    assert "^ripgrep $([regex]::Escape($env:RG_VERSION))(\\s|$)" in tail
    assert "exit 1" in tail


def test_long_path_step_builds_the_fixture_and_runs_the_scanners() -> None:
    # #1405: the scanners must read a file past Windows' 260-character limit
    # in a checkout configured as ghsync clones it (core.longpaths=true).
    _, job = _matrix_job(_jobs())
    step = next(s for s in job["steps"] if s.get("name") == "Smoke the scanners on a path over 260 characters")
    run = step["run"]
    assert step["shell"] == "bash"
    assert step["working-directory"] == "skills/assess"
    assert "continue-on-error" not in step
    build = run.index("tests/fixtures/windows_smoke/longpath.py build")
    signals = run.index("scripts/assess_signals.py")
    core = run.index("scripts/assess_core.py")
    check = run.index("tests/fixtures/windows_smoke/longpath.py check --require-all")
    assert build < signals < core < check
    assert "config --get core.longpaths" in run
    # vulture is not in the locked environment; without it liveness is not measured.
    assert '--with "vulture==' in run[signals:check]
    assert (REPO / "tests" / "fixtures" / "windows_smoke" / "longpath.py").is_file()


# The Python 3.15 probe (#1490): a job outside the matrix that tries to
# reproduce the pytest-xdist startup crash on 3.15. It must never gate a merge.
PROBE_ID = "python315"
PROBE_SUITES = ("assess", "contract")


def _probe(jobs: dict[str, Any]) -> dict[str, Any]:
    assert PROBE_ID in jobs, f"windows.yml has no {PROBE_ID} job"
    return jobs[PROBE_ID]


def test_python315_probe_is_not_a_required_check() -> None:
    jobs = _jobs()
    probe = _probe(jobs)
    _, agg = _aggregator(jobs)
    needs = agg["needs"]
    assert PROBE_ID not in (needs if isinstance(needs, list) else [needs])
    assert probe["name"] != REQUIRED_NAME
    assert "not required" in probe["name"]
    assert "strategy" not in probe, "the probe must stay out of the matrix the aggregator reads"
    assert probe["runs-on"] == "windows-latest"


def test_python315_probe_runs_parallel_suites_that_cannot_fail_the_job() -> None:
    steps = {s.get("id"): s for s in _probe(_jobs())["steps"] if "id" in s}
    for sid in PROBE_SUITES:
        step = steps[sid]
        run = " ".join(step["run"].split())
        assert step["continue-on-error"] is True, sid
        assert step["shell"] == "bash", sid  # the reporter ran under Git Bash
        assert "--python 3.15" in run, sid
        assert "-n auto --dist loadfile" in run, sid
        assert "set -o pipefail" in run, sid  # tee must not hide pytest's exit code


def test_python315_probe_records_each_outcome() -> None:
    steps = _probe(_jobs())["steps"]
    record = next(s for s in steps if s.get("name") == "Record the 3.15 outcome")
    assert record["if"] == "${{ !cancelled() }}"
    assert "continue-on-error" not in record
    for sid in PROBE_SUITES:
        assert f"${{{{ steps.{sid}.outcome }}}}" in record["env"].values(), sid
    assert "Errno 22" in record["run"]
    assert "GITHUB_STEP_SUMMARY" in record["run"]
