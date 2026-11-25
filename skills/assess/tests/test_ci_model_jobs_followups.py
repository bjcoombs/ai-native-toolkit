"""lib/ci_model_jobs.py: the #1249 follow-ups to the model-backed CI gating reader (#1242).

Each case reads the right way, and its mirror still reads as before. No false-credit case reads
gated or met.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

import lib.level_tally as lt
from lib.ci_masking import IndentTree, parse_lines
from lib.ci_model_jobs import scan_ci_model_jobs, signs_in

CID = "model-ci-gated.1"
EVAL_STEPS = """jobs:
  evals:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: npx promptfoo@latest eval -c promptfooconfig.yaml
        env:
          OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}
"""
SCHEDULE = "  schedule:\n    - cron: '0 3 * * *'\n"


def _files(root: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


def _git(root: Path, files: dict[str, str]) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, check=True)
    _files(root, files)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-qm", "c"],
                   cwd=root, check=True)
    return root


def _scan(root: Path, files: dict[str, str]) -> dict[str, Any]:
    return dict(scan_ci_model_jobs(_files(root, files)))


def _workflow(root: Path, on: str, jobs: str = EVAL_STEPS) -> dict[str, Any]:
    return _scan(root, {".github/workflows/evals.yml": f"name: x\non:\n{on}\n{jobs}"})


def _with_if(condition: str, jobs: str = EVAL_STEPS) -> str:
    return jobs.replace("    runs-on:", f"    if: {condition}\n    runs-on:")


def _job(block: dict[str, Any], path: str = ".github/workflows/evals.yml") -> dict[str, Any]:
    return next(j for j in block["jobs"] if j["path"] == path)


def _state(block: Any) -> tuple[str, str | None]:
    cond = next(c for c in lt.level_result(lt.default_model(), {"ci_model_jobs": block})["conditions"]
                if c["id"] == CID)
    return cond["state"], cond.get("reason")


def _signs(text: str) -> list[str]:
    tree = IndentTree(parse_lines(text.splitlines()))
    return signs_in(tree, range(len(tree.lines)))


# --- false credit -------------------------------------------------------------------------

@pytest.mark.parametrize(("condition", "state"), [
    ("(github.event_name == 'push') == false", "ungated"),
    ("github.event_name != 'push'", "ungated"),
    ("github.event_name == 'pull_request'", "ungated"),  # mirror: narrows to the ungated pull_request
])
def test_a_negative_event_requirement_does_not_narrow(tmp_path: Path, condition: str, state: str) -> None:
    job = _job(_workflow(tmp_path, "  push:\n    paths: [p/**]\n  pull_request:", _with_if(condition)))
    assert (job["state"], job["gates"]) == (state, [])


def test_a_false_guard_beside_an_event_requirement_still_narrows(tmp_path: Path) -> None:
    jobs = _with_if("github.event_name == 'pull_request' && startsWith(github.head_ref, 'dependabot') == false")
    job = _job(_workflow(tmp_path, "  push:\n  pull_request:\n    paths: ['prompts/**']", jobs))
    assert (job["state"], job["triggers"], job["gates"]) == ("gated", ["pull_request"], ["event_if"])


@pytest.mark.parametrize("condition", [
    "false == contains(github.event.pull_request.labels.*.name, 'skip-evals')",
    "contains(github.event.pull_request.labels.*.name, 'skip-evals') == false",
])
def test_a_label_test_compared_with_false_either_way_reads_unknown(tmp_path: Path, condition: str) -> None:
    job = _job(_workflow(tmp_path, "  pull_request:", _with_if(condition)))
    assert (job["state"], job["reason"]) == ("unknown", "condition_unread")


def test_a_positive_event_requirement_still_narrows_to_a_gated_route(tmp_path: Path) -> None:
    jobs = _with_if("github.event_name == 'push'")
    job = _job(_workflow(tmp_path, "  push:\n    paths: [p/**]\n  pull_request:", jobs))
    assert (job["state"], job["gates"]) == ("gated", ["event_if"])


def _callee(condition: str) -> str:
    return "on:\n  workflow_call:\n" + _with_if(condition)


def _caller(event: str, name: str = "evals.yml") -> str:
    return f"on:\n  {event}:\njobs:\n  call:\n    uses: ./.github/workflows/{name}\n"


def test_a_callee_condition_applies_to_each_caller_route(tmp_path: Path) -> None:
    files = {".github/workflows/evals.yml": _callee("github.event_name == 'pull_request'"),
             ".github/workflows/a.yml": _caller("push"), ".github/workflows/b.yml": _caller("pull_request")}
    block = _scan(tmp_path, files)
    assert (_job(block)["state"], _job(block)["triggers"]) == ("ungated", ["pull_request"])
    assert _state(block) == ("unmet", None)
    # Mirror: with only the push caller, the pull_request requirement leaves no ungated route.
    del files[".github/workflows/b.yml"]
    job = _job(_scan(tmp_path / "b", files))
    assert (job["state"], job["gates"]) == ("gated", ["event_if"])


def test_a_called_and_dispatched_workflow_with_no_local_caller_reads_unknown(tmp_path: Path) -> None:
    callee = "on:\n  workflow_call:\n  workflow_dispatch:\n" + EVAL_STEPS
    block = _scan(tmp_path, {".github/workflows/evals.yml": callee})
    assert (_job(block)["state"], _job(block)["reason"]) == ("unknown", "called_workflow")
    assert _state(block) == ("not_measured", "trigger_unresolved")
    # Mirror: a local scheduled caller settles the route.
    block = _scan(tmp_path / "b", {".github/workflows/evals.yml": callee,
                                   ".github/workflows/nightly.yml": _caller("schedule").replace(
                                       "  schedule:\n", SCHEDULE)})
    assert _job(block)["state"] == "gated" and _state(block) == ("met", None)


GITLAB_EVAL = "evals:\n  script:\n    - deepeval test run tests/\n"
GITLAB_SCHEDULE = "  rules:\n    - if: $CI_PIPELINE_SOURCE == \"schedule\"\n"


def test_a_root_override_of_an_included_model_job_is_read_merged(tmp_path: Path) -> None:
    files = {".gitlab-ci.yml": "include:\n  - local: ci/evals.yml\nevals:\n  rules:\n    - when: always\n",
             "ci/evals.yml": GITLAB_EVAL + GITLAB_SCHEDULE}
    block = dict(scan_ci_model_jobs(_git(tmp_path / "a", files)))
    assert [(j["job"], j["state"], j["line"]) for j in block["jobs"]] == [("evals", "ungated", 3)]
    assert _state(block) == ("unmet", None)
    # Mirror: with no root override, the included schedule-only job stays gated.
    files[".gitlab-ci.yml"] = "include:\n  - local: ci/evals.yml\n"
    block = dict(scan_ci_model_jobs(_git(tmp_path / "b", files)))
    assert [(j["state"], j["gates"], j["line"]) for j in block["jobs"]] == [("gated", ["event_trigger"], 2)]


def test_an_unsettled_merge_reads_unknown(tmp_path: Path) -> None:
    # An include under rules: GitLab may leave it out, so the merged job is not settled.
    files = {".gitlab-ci.yml": "include:\n  - local: ci/evals.yml\n    rules:\n      - if: $NIGHTLY\n"
                               "evals:\n  rules:\n    - if: $CI_PIPELINE_SOURCE == \"schedule\"\n",
             "ci/evals.yml": GITLAB_EVAL}
    block = dict(scan_ci_model_jobs(_git(tmp_path, files)))
    assert [(j["state"], j.get("reason")) for j in block["jobs"]] == [("unknown", "merge_unread")]
    assert _state(block) == ("not_measured", "trigger_unresolved")


# --- false unmet ----------------------------------------------------------------------------

@pytest.mark.parametrize(("filters", "state"), [
    ("    paths: ['*']", "gated"),
    ("    paths-ignore: ['*']", "gated"),
    ("    paths: ['**', '!docs/**']", "gated"),
    ("    paths: ['!docs/**', '**']", "ungated"),  # mirror: the later catch-all lets docs back in
    ("    paths: ['**']", "ungated"),
])
def test_paths_patterns_are_read_in_order(tmp_path: Path, filters: str, state: str) -> None:
    assert _job(_workflow(tmp_path, "  pull_request:\n" + filters))["state"] == state


STEP_GATES = """jobs:
  evals:
    runs-on: ubuntu-latest
    steps:
      - if: contains(github.event.pull_request.labels.*.name, 'evals')
        run: npx promptfoo@latest eval
      - if: {second}
        run: claude -p 'review'
"""


@pytest.mark.parametrize(("second", "state", "gates"), [
    ("github.event_name == 'schedule'", "gated", ["event_if", "label"]),
    ("github.actor == 'someone'", "ungated", []),  # mirror: one sign step with no gate
    ("steps.x.outputs.y == 'true'", "unknown", []),
])
def test_recognised_step_gates_combine(tmp_path: Path, second: str, state: str, gates: list[str]) -> None:
    job = _job(_workflow(tmp_path, "  pull_request:\n" + SCHEDULE, STEP_GATES.format(second=second)))
    assert (job["state"], job["gates"]) == (state, gates)


def test_a_disabled_caller_registers_no_route(tmp_path: Path) -> None:
    callee = "on:\n  workflow_call:\n" + EVAL_STEPS
    live = _caller("schedule", "evals.yml").replace("  schedule:\n", SCHEDULE).replace("  call:\n", "  nightly:\n")
    dead = _caller("pull_request").replace("    uses:", "    if: false\n    uses:")
    files = {".github/workflows/evals.yml": callee, ".github/workflows/n.yml": live, ".github/workflows/pr.yml": dead}
    block = _scan(tmp_path, files)
    assert _job(block)["state"] == "gated" and _state(block) == ("met", None)
    # Mirror: the same call enabled makes the callee ungated.
    files[".github/workflows/pr.yml"] = _caller("pull_request")
    assert _job(_scan(tmp_path / "b", files))["state"] == "ungated"


# --- recall ---------------------------------------------------------------------------------

@pytest.mark.parametrize(("command", "signs"), [
    ("claude -p 'add regression tests'", ["agent_cli"]),
    ("claude -p \"install the fixtures, then add tests\"", ["agent_cli"]),
    ("claude -p add tests", ["agent_cli"]),
    ("claude -p \"Run npm install and fix tests\"", ["agent_cli"]),
    ("npm install -g @anthropic-ai/claude-code && claude -p review", ["agent_cli"]),
    ("npx promptfoo eval -c add.yaml", ["eval_harness"]),
    # Mirrors: an install in command position is still never a sign.
    ("pip install ragas", []),
    ("python -m pip install deepeval", []),
    ("npm install -g @anthropic-ai/claude-code", []),
    ("poetry add ragas", []),
    ("sudo python3 -m pip install ragas", []),
    ("docker run img sh -c \"pip install ragas\"", []),
    ("uv pip install deepeval", []),
    ("yarn global add promptfoo", []),
])
def test_install_is_read_in_command_position_only(command: str, signs: list[str]) -> None:
    assert _signs(f"jobs:\n  j:\n    steps:\n      - run: {command}\n") == signs


def test_a_gitlab_job_extending_a_model_template_is_found(tmp_path: Path) -> None:
    files = {".gitlab-ci.yml": ".eval:\n  script:\n    - deepeval test run tests/\n"
                               "evals:\n  extends: .eval\n  stage: test\n"}
    block = dict(scan_ci_model_jobs(_git(tmp_path, files)))
    assert [(j["job"], j["state"], j["line"], j["signs"]) for j in block["jobs"]] == [
        ("evals", "ungated", 4, ["eval_harness"])]
    # Mirror: a gated extending job reads gated.
    files[".gitlab-ci.yml"] += GITLAB_SCHEDULE
    block = dict(scan_ci_model_jobs(_git(tmp_path / "b", files)))
    assert [(j["state"], j["gates"]) for j in block["jobs"]] == [("gated", ["event_trigger"])]


def test_nested_local_calls_carry_the_outer_route(tmp_path: Path) -> None:
    files = {".github/workflows/a.yml": _caller("pull_request", "b.yml"),
             ".github/workflows/b.yml": "on:\n  workflow_call:\njobs:\n  call:\n    uses: ./.github/workflows/c.yml\n",
             ".github/workflows/c.yml": "on:\n  workflow_call:\n" + EVAL_STEPS}
    block = _scan(tmp_path, files)
    assert (_job(block, ".github/workflows/c.yml")["state"], _state(block)) == ("ungated", ("unmet", None))
    # Mirror: a scheduled outer caller gates the inner model job.
    files[".github/workflows/a.yml"] = _caller("schedule", "b.yml").replace("  schedule:\n", SCHEDULE)
    block = _scan(tmp_path / "b", files)
    assert (_job(block, ".github/workflows/c.yml")["state"], _state(block)) == ("gated", ("met", None))


def test_a_local_caller_passing_a_provider_secret_is_not_a_job(tmp_path: Path) -> None:
    caller = (_caller("schedule").replace("  schedule:\n", SCHEDULE)
              + "    secrets:\n      OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}\n")
    files = {".github/workflows/evals.yml": "on:\n  workflow_call:\n" + EVAL_STEPS, ".github/workflows/n.yml": caller}
    block = _scan(tmp_path, files)
    assert [j["path"] for j in block["jobs"]] == [".github/workflows/evals.yml"]
    assert _state(block) == ("met", None)
    # Mirror: a remote reusable workflow passed the key still reads as a secret-only job.
    remote = caller.replace("./.github/workflows/evals.yml", "org/ci/.github/workflows/e.yml@v1").replace(
        SCHEDULE, "  push:\n")
    block = _scan(tmp_path / "b", {".github/workflows/r.yml": remote})
    assert [(j["state"], j.get("reason")) for j in block["jobs"]] == [("unknown", "secret_only")]


@pytest.mark.parametrize(("condition", "state", "gates"), [
    ("github.event.pull_request.draft == false && contains(github.event.pull_request.labels.*.name, 'evals')",
     "gated", ["label"]),
    ("contains(github.event.pull_request.labels.*.name, 'skip') == false", "unknown", []),  # mirror
])
def test_a_false_guard_beside_a_label_test_keeps_the_gate(tmp_path: Path, condition: str, state: str,
                                                          gates: list[str]) -> None:
    job = _job(_workflow(tmp_path, "  pull_request:", _with_if(condition)))
    assert (job["state"], job["gates"]) == (state, gates)


@pytest.mark.parametrize(("text", "signs"), [
    ("jobs:\n  j:\n    steps:\n      - name: Rotate OPENAI_API_KEY\n        run: ./rotate.sh\n", []),
    ("jobs:\n  j:\n    steps:\n      - run: ruff check .\n        name: lint ANTHROPIC_API_KEY usage\n", []),
    # Mirrors: env:, with: and secrets: entries are still signs.
    ("jobs:\n  j:\n    env:\n      OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}\n", ["provider_secret"]),
    ("jobs:\n  j:\n    steps:\n      - uses: x/y@v1\n        with:\n          key: ${{ secrets.OPENAI_API_KEY }}\n",
     ["provider_secret"]),
    ("jobs:\n  j:\n    secrets:\n      ANTHROPIC_API_KEY: ${{ secrets.K }}\n", ["provider_secret"]),
    ("evals:\n  variables:\n    OPENAI_API_KEY: $KEY\n", ["provider_secret"]),
])
def test_a_provider_secret_is_a_sign_only_when_handed_to_the_job(text: str, signs: list[str]) -> None:
    assert _signs(text) == signs


def test_a_step_name_naming_a_key_does_not_make_a_lint_job_a_model_job(tmp_path: Path) -> None:
    lint = "jobs:\n  lint:\n    steps:\n      - name: Check OPENAI_API_KEY is not logged\n        run: ruff check .\n"
    files = {".github/workflows/evals.yml": f"on:\n{SCHEDULE}" + EVAL_STEPS, ".github/workflows/lint.yml": "on: push\n" + lint}
    assert _state(_scan(tmp_path, files)) == ("met", None)


@pytest.mark.parametrize("key", ['"on"', "'on'", "on"])
def test_a_quoted_on_key_reads_its_filters(tmp_path: Path, key: str) -> None:
    text = f"{key}:\n  pull_request:\n    paths: ['prompts/**']\n" + EVAL_STEPS
    block = _scan(tmp_path, {".github/workflows/evals.yml": text})
    assert (_job(block)["state"], _job(block)["gates"]) == ("gated", ["paths"])
    assert _state(block) == ("met", None)


def test_a_remote_call_under_a_gated_caller_is_not_unread(tmp_path: Path) -> None:
    remote = f"on:\n{SCHEDULE}  workflow_dispatch:\njobs:\n  shared:\n    uses: org/ci/.github/workflows/build.yml@v1\n"
    files = {".github/workflows/evals.yml": f"on:\n{SCHEDULE}" + EVAL_STEPS, ".github/workflows/r.yml": remote}
    block = _scan(tmp_path, files)
    assert (block["unread"], _state(block)) == (0, ("met", None))
    # Mirror: the same call on push may hold an ungated model job.
    files[".github/workflows/r.yml"] = "on: push\njobs:\n  shared:\n    uses: org/ci/.github/workflows/build.yml@v1\n"
    block = _scan(tmp_path / "b", files)
    assert (block["unread"], _state(block)) == (1, ("not_measured", "ci_jobs_unread"))
