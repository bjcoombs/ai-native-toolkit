"""lib/ci/ci_model_jobs.py and the model-ci-gated condition (#1242): model-backed CI jobs and their gates."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import lib.level_tally as lt
from lib.counts_only.content_free import counts_only
from lib.ci_masking import IndentTree, parse_lines
from lib.ci.ci_model_jobs import GATES, scan_ci_model_jobs, signs_in, without_changes

CID = "model-ci-gated.1"
REPO = Path(__file__).resolve().parents[3]

EVAL_STEPS = """jobs:
  evals:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: npx promptfoo@latest eval -c promptfooconfig.yaml
        env:
          OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}
"""


def _repo(root: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


def _workflow(tmp_path: Path, on: str, jobs: str = EVAL_STEPS, name: str = "evals.yml") -> dict[str, Any]:
    root = _repo(tmp_path, {f".github/workflows/{name}": f"name: x\non:\n{on}\n{jobs}"})
    return dict(scan_ci_model_jobs(root))


def _state(block: Any) -> tuple[str, str | None]:
    cond = next(c for c in lt.level_result(lt.default_model(), {"ci_model_jobs": block})["conditions"]
                if c["id"] == CID)
    return cond["state"], cond.get("reason")


def _signs(text: str) -> list[str]:
    tree = IndentTree(parse_lines(text.splitlines()))
    return signs_in(tree, range(len(tree.lines)))


# --- detection -------------------------------------------------------------------------

@pytest.mark.parametrize(("line", "sign"), [
    ("      - run: npx promptfoo@latest eval", "eval_harness"),
    ("      - run: deepeval test run tests/test_llm.py", "eval_harness"),
    ("      - run: oaieval gpt-4o-mini my-eval", "eval_harness"),
    ("      - run: inspect eval tasks/arc.py --model openai/gpt-4o", "eval_harness"),
    ("      - run: python -m lm_eval --model hf --tasks hellaswag", "eval_harness"),
    ("      - run: claude -p 'summarise the diff'", "agent_cli"),
    ("      - run: npx -y @anthropic-ai/claude-code --print 'hi'", "agent_cli"),
    ("      - run: pytest -m eval", "eval_tests"),
    ("      - run: uv run pytest evals/", "eval_tests"),
    ("      - run: python evals/run.py", "eval_tests"),
    ("      - uses: anthropics/claude-code-action@v1", "agent_action"),
    ("      - uses: 'openai/codex-action@main'", "agent_action"),
    ("        env:\n          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}", "provider_secret"),
    ("      - uses: x/y@v1\n        with:\n          claude_code_oauth_token: ${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}",
     "provider_secret"),
])
def test_each_sign_is_found(line: str, sign: str) -> None:
    assert sign in _signs("jobs:\n  j:\n    steps:\n" + line + "\n")


@pytest.mark.parametrize("line", [
    "      - run: pip install ragas promptfoo deepeval",
    "      - run: echo 'Claude Desktop, claude.ai and Claude Code'",
    "      - run: python3 -c \"print(open('.claude-plugin/plugin.json').read())\"",
    "      - run: pytest -m 'not slow' tests/",
    "      - run: pytest tests/test_evaluate.py",
    "      - uses: actions/checkout@v4",
    "      # - run: claude -p 'disabled'",
])
def test_lines_that_are_not_signs(line: str) -> None:
    assert _signs("jobs:\n  j:\n    steps:\n" + line + "\n") == []


def test_a_block_run_is_read_line_by_line() -> None:
    text = "jobs:\n  j:\n    steps:\n      - run: |\n          set -e\n          claude -p \"review\"\n"
    assert _signs(text) == ["agent_cli"]


def test_this_repositorys_standalone_build_is_not_a_model_job() -> None:
    block = scan_ci_model_jobs(REPO)
    assert all(j["path"] != ".github/workflows/build-standalone-skills.yml" for j in block["jobs"])


# --- this repository: the first positive case ----------------------------------------

def test_this_repository_reads_unmet_naming_claude_review() -> None:
    block = scan_ci_model_jobs(REPO)
    review = next(j for j in block["jobs"] if j["job"] == "claude-review")
    assert review["path"] == ".github/workflows/claude-review.yml"
    assert review["line"] > 0 and review["state"] == "ungated"
    assert review["triggers"] == ["pull_request"] and review["gates"] == []
    assert "agent_action" in review["signs"]
    assert review["cancel_superseded"] is True  # recorded, not a gate
    assert _state(block) == ("unmet", None)


# --- GitHub gates ----------------------------------------------------------------------

def test_a_paths_filtered_eval_reads_met(tmp_path: Path) -> None:
    block = _workflow(tmp_path, "  pull_request:\n    paths:\n      - 'prompts/**'\n"
                                "  push:\n    branches: [main]\n    paths-ignore: ['docs/**']")
    assert block["ungated"] == 0 and block["gated"] == 1
    assert block["jobs"][0]["gates"] == ["paths"]
    assert _state(block) == ("met", None)


def test_paths_on_one_event_beside_a_bare_one_is_ungated(tmp_path: Path) -> None:
    block = _workflow(tmp_path, "  push:\n    paths: ['prompts/**']\n  pull_request:")
    job = block["jobs"][0]
    assert (job["state"], job["triggers"]) == ("ungated", ["pull_request"])
    assert _state(block) == ("unmet", None)


@pytest.mark.parametrize(("on", "gate"), [
    ("  schedule:\n    - cron: '0 3 * * *'\n  workflow_dispatch:", "event_trigger"),
    ("  issue_comment:\n    types: [created]", "event_trigger"),
    ("  pull_request:\n    types: [labeled]", "label"),
    ("  pull_request:\n    types: [opened, reopened]", "pr_types"),
    ("  push:\n    tags: ['v*']", "tags_only"),
])
def test_event_gates(tmp_path: Path, on: str, gate: str) -> None:
    block = _workflow(tmp_path, on)
    assert block["jobs"][0]["gates"] == [gate] and block["ungated"] == 0
    assert gate in GATES


def test_branches_alone_and_synchronize_are_not_gates(tmp_path: Path) -> None:
    block = _workflow(tmp_path, "  push:\n    branches: [main]\n  pull_request:\n    types: [opened, synchronize]")
    assert block["jobs"][0]["triggers"] == ["pull_request", "push"]
    assert block["ungated"] == 1


def test_a_flow_list_on_is_ungated(tmp_path: Path) -> None:
    root = _repo(tmp_path, {".github/workflows/e.yml": "on: [push, pull_request]\n" + EVAL_STEPS})
    assert scan_ci_model_jobs(root)["ungated"] == 1


def test_a_label_if_on_the_job_is_a_gate(tmp_path: Path) -> None:
    jobs = EVAL_STEPS.replace("    runs-on:", "    if: contains(github.event.pull_request.labels.*.name, 'run-evals')\n    runs-on:")
    block = _workflow(tmp_path, "  pull_request:", jobs)
    assert block["jobs"][0]["gates"] == ["label"] and _state(block) == ("met", None)


def test_a_label_if_on_the_model_step_is_a_gate(tmp_path: Path) -> None:
    jobs = EVAL_STEPS.replace("      - run: npx", "      - if: github.event.label.name == 'evals'\n        run: npx")
    block = _workflow(tmp_path, "  pull_request:\n    types: [opened, synchronize, labeled]", jobs)
    assert block["jobs"][0]["gates"] == ["label"]


def test_a_label_if_joined_with_or_is_not_measured(tmp_path: Path) -> None:
    jobs = EVAL_STEPS.replace(
        "    runs-on:", "    if: github.event_name == 'push' || contains(github.event.pull_request.labels.*.name, 'x')\n    runs-on:")
    block = _workflow(tmp_path, "  pull_request:", jobs)
    assert block["jobs"][0]["state"] == "unknown" and block["jobs"][0]["reason"] == "condition_unread"
    assert block["ungated"] is None and _state(block) == ("not_measured", "trigger_unresolved")


def test_an_actor_if_is_not_a_gate(tmp_path: Path) -> None:
    jobs = EVAL_STEPS.replace("    runs-on:", "    if: github.actor != 'dependabot[bot]'\n    runs-on:")
    assert _workflow(tmp_path, "  pull_request:", jobs)["ungated"] == 1


def test_a_schedule_event_if_is_a_gate(tmp_path: Path) -> None:
    jobs = EVAL_STEPS.replace("    runs-on:", "    if: github.event_name == 'schedule'\n    runs-on:")
    block = _workflow(tmp_path, "  push:\n  schedule:\n    - cron: '0 0 * * *'", jobs)
    assert block["jobs"][0]["gates"] == ["event_if"]


def test_cancel_in_progress_is_recorded_not_a_gate(tmp_path: Path) -> None:
    on = "  pull_request:\nconcurrency:\n  group: x\n  cancel-in-progress: true"
    job = _workflow(tmp_path, on)["jobs"][0]
    assert job["cancel_superseded"] is True and job["state"] == "ungated"


def test_an_unreadable_on_is_not_measured(tmp_path: Path) -> None:
    root = _repo(tmp_path, {".github/workflows/e.yml": "name: x\n" + EVAL_STEPS})
    block = scan_ci_model_jobs(root)
    assert block["jobs"][0]["state"] == "unknown"
    assert _state(block) == ("not_measured", "trigger_unresolved")


def test_an_ungated_job_beside_an_unknown_one_reads_unmet(tmp_path: Path) -> None:
    root = _repo(tmp_path, {".github/workflows/a.yml": "name: a\n" + EVAL_STEPS,
                            ".github/workflows/b.yml": "on: pull_request\n" + EVAL_STEPS})
    block = scan_ci_model_jobs(root)
    assert (block["ungated"], block["unknown"]) == (1, 1)
    assert _state(block) == ("unmet", None)


def test_a_called_workflow_takes_its_callers_route(tmp_path: Path) -> None:
    called = "on:\n  workflow_call:\n" + EVAL_STEPS
    caller = "on:\n  pull_request:\n    paths: ['prompts/**']\njobs:\n  run:\n    uses: ./.github/workflows/evals.yml\n"
    root = _repo(tmp_path, {".github/workflows/evals.yml": called, ".github/workflows/ci.yml": caller})
    block = scan_ci_model_jobs(root)
    assert block["jobs"][0]["gates"] == ["paths"] and _state(block) == ("met", None)
    root2 = _repo(tmp_path / "b", {".github/workflows/evals.yml": called})
    assert scan_ci_model_jobs(root2)["jobs"][0]["reason"] == "called_workflow"


def test_a_remote_reusable_workflow_blocks_a_met_reading(tmp_path: Path) -> None:
    remote = "on: push\njobs:\n  shared:\n    uses: org/ci/.github/workflows/build.yml@v1\n"
    root = _repo(tmp_path, {".github/workflows/e.yml": "on:\n  schedule:\n    - cron: '0 0 * * *'\n" + EVAL_STEPS,
                            ".github/workflows/r.yml": remote})
    block = scan_ci_model_jobs(root)
    assert block["unread"] == 1 and block["ungated"] is None
    assert _state(block) == ("not_measured", "ci_jobs_unread")


# --- GitLab ----------------------------------------------------------------------------

GITLAB_JOB = """evals:
  stage: test
  script:
    - deepeval test run tests/
{rules}"""


def _gitlab(tmp_path: Path, rules: str) -> dict[str, Any]:
    return dict(scan_ci_model_jobs(_repo(tmp_path, {".gitlab-ci.yml": GITLAB_JOB.format(rules=rules)})))


def test_gitlab_rules_changes_is_a_gate(tmp_path: Path) -> None:
    block = _gitlab(tmp_path, "  rules:\n    - changes:\n        - prompts/**/*\n")
    job = block["jobs"][0]
    assert (job["platform"], job["gates"], job["line"]) == ("gitlab", ["changes"], 1)
    assert _state(block) == ("met", None)


def test_gitlab_changes_on_one_rule_beside_an_open_one_is_ungated(tmp_path: Path) -> None:
    rules = ("  rules:\n    - if: $CI_PIPELINE_SOURCE == \"merge_request_event\"\n      changes: [prompts/**]\n"
             "    - if: $CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH\n")
    assert _gitlab(tmp_path, rules)["ungated"] == 1


def test_gitlab_job_with_no_rules_is_ungated(tmp_path: Path) -> None:
    block = _gitlab(tmp_path, "")
    assert block["jobs"][0]["state"] == "ungated" and _state(block) == ("unmet", None)


def test_gitlab_schedule_only_is_a_gate(tmp_path: Path) -> None:
    block = _gitlab(tmp_path, "  rules:\n    - if: $CI_PIPELINE_SOURCE == \"schedule\"\n")
    assert block["jobs"][0]["gates"] == ["event_trigger"]


def test_without_changes_keeps_line_numbers() -> None:
    text = "j:\n  rules:\n    - changes:\n        - a/**\n      when: never\n    - when: on_success\n"
    out = without_changes(text)
    assert len(out.splitlines()) == len(text.splitlines())
    assert out.splitlines()[2] == "    - if: $CI_COMMIT_TAG"


# --- no model job ----------------------------------------------------------------------

def test_no_model_job_is_not_measured(tmp_path: Path) -> None:
    root = _repo(tmp_path, {".github/workflows/ci.yml": "on: push\njobs:\n  t:\n    steps:\n      - run: pytest\n"})
    block = scan_ci_model_jobs(root)
    assert (block["model_jobs"], block["ungated"]) == (0, 0)
    assert _state(block) == ("not_measured", "no_model_jobs")


def test_no_ci_file_is_not_measured(tmp_path: Path) -> None:
    assert _state(scan_ci_model_jobs(tmp_path)) == ("not_measured", "no_model_jobs")


# --- counts-only -----------------------------------------------------------------------

def test_counts_only_keeps_every_count_and_token_and_drops_the_job_name() -> None:
    block = scan_ci_model_jobs(REPO)
    out = counts_only({"ci_model_jobs": block})["ci_model_jobs"]
    for field in ("available", "files_read", "model_jobs", "gated", "unknown", "unread", "ungated"):
        assert out[field] == block[field], field
    job = out["jobs"][0]
    assert "job" not in job
    for field in ("path", "line", "platform", "signs", "triggers", "gates", "state", "cancel_superseded"):
        assert job[field] == block["jobs"][0][field], field  # type: ignore[literal-required]


def test_counts_only_keeps_the_ungated_reason(tmp_path: Path) -> None:
    root = _repo(tmp_path, {".github/workflows/e.yml": "name: x\n" + EVAL_STEPS})
    out = counts_only({"ci_model_jobs": scan_ci_model_jobs(root)})["ci_model_jobs"]
    assert (out["ungated"], out["ungated_reason"], out["jobs"][0]["reason"]) == (
        None, "trigger_unresolved", "on_unread")


# --- corpus findings: precision --------------------------------------------------------

def test_a_secret_passed_on_by_a_command_is_not_a_sign() -> None:
    text = ("jobs:\n  deploy:\n    steps:\n      - run: |\n"
            "          aws secretsmanager put-secret-value --secret-string \"${{ secrets.ANTHROPIC_API_KEY }}\"\n")
    assert _signs(text) == []


def test_a_never_true_step_is_not_read(tmp_path: Path) -> None:
    jobs = EVAL_STEPS.replace("      - run: npx", "      - if: false\n        run: npx")
    assert _workflow(tmp_path, "  pull_request:", jobs)["model_jobs"] == 0


def test_a_step_if_on_a_step_output_is_not_measured(tmp_path: Path) -> None:
    jobs = EVAL_STEPS.replace("      - run: npx", "      - if: steps.scope.outputs.scope == 'prompts'\n        run: npx")
    block = _workflow(tmp_path, "  push:", jobs)
    assert block["jobs"][0]["reason"] == "condition_unread"
    assert _state(block) == ("not_measured", "trigger_unresolved")


def test_a_job_if_on_a_changes_job_is_not_measured(tmp_path: Path) -> None:
    jobs = EVAL_STEPS.replace("    runs-on:", "    needs: changes\n    if: needs.changes.outputs.prompts == 'true'\n    runs-on:")
    assert _workflow(tmp_path, "  pull_request:", jobs)["jobs"][0]["state"] == "unknown"


def test_a_job_if_on_an_ordinary_event_narrows_the_trigger(tmp_path: Path) -> None:
    jobs = EVAL_STEPS.replace("    runs-on:", "    if: github.event_name == 'pull_request'\n    runs-on:")
    job = _workflow(tmp_path, "  push:\n  pull_request:", jobs)["jobs"][0]
    assert (job["state"], job["triggers"]) == ("ungated", ["pull_request"])
    gated = _workflow(tmp_path / "b", "  push:\n  pull_request:\n    paths: [p/**]", jobs)["jobs"][0]
    assert (gated["state"], gated["gates"]) == ("gated", ["event_if"])


# --- pre-review: false credits, missed jobs, over-detection -----------------------------

SECRET_ONLY = """jobs:
  store:
    runs-on: ubuntu-latest
    steps:
      - run: gcloud secrets versions add key --data-file=- <<< "$OPENAI_API_KEY"
        env:
          OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}
"""


@pytest.mark.parametrize(("filters", "state"), [
    ("    paths: ['**']", "ungated"),
    ("    paths: ['**/*', 'src/**']", "ungated"),
    ("    paths-ignore: []", "ungated"),
    ("    paths-ignore: ['**']", "ungated"),
    ("    paths: ['prompts/**']", "gated"),
    ("    paths-ignore: ['docs/**']", "gated"),
])
def test_a_catch_all_paths_pattern_gates_nothing(tmp_path: Path, filters: str, state: str) -> None:
    assert _workflow(tmp_path, "  pull_request:\n" + filters)["jobs"][0]["state"] == state


@pytest.mark.parametrize("on", ["on: ${{ fromJSON(vars.EVENTS) }}\n", "on: [made_up_event]\n",
                                "on:\n  schedule_typo:\n"])
def test_an_unknown_event_reads_unknown_never_gated(tmp_path: Path, on: str) -> None:
    root = _repo(tmp_path, {".github/workflows/e.yml": on + EVAL_STEPS})
    block = scan_ci_model_jobs(root)
    assert (block["jobs"][0]["state"], block["jobs"][0]["reason"]) == ("unknown", "on_unread")
    assert _state(block) == ("not_measured", "trigger_unresolved")


def test_a_known_gating_event_still_gates(tmp_path: Path) -> None:
    assert _workflow(tmp_path, "  release:\n    types: [published]")["jobs"][0]["gates"] == ["event_trigger"]


def test_a_workflow_env_secret_reaches_every_job(tmp_path: Path) -> None:
    jobs = ("env:\n  ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}\njobs:\n"
            "  agent:\n    steps:\n      - run: claude -p 'triage'\n"
            "  lint:\n    steps:\n      - run: ruff check .\n")
    block = _workflow(tmp_path, "  pull_request:", jobs)
    states = {j["job"]: (j["state"], j["signs"]) for j in block["jobs"]}
    assert states["agent"] == ("ungated", ["agent_cli", "provider_secret"])
    assert states["lint"][0] == "unknown"  # the secret alone: secret_only
    assert _state(block) == ("unmet", None)


def test_a_local_composite_holding_an_agent_action_is_followed(tmp_path: Path) -> None:
    action = "runs:\n  using: composite\n  steps:\n    - uses: anthropics/claude-code-action@v1\n"
    jobs = "jobs:\n  review:\n    steps:\n      - uses: ./.github/actions/review\n"
    root = _repo(tmp_path, {".github/actions/review/action.yml": action,
                            ".github/workflows/r.yml": "on: pull_request\n" + jobs})
    job = scan_ci_model_jobs(root)["jobs"][0]
    assert (job["state"], job["signs"]) == ("ungated", ["agent_action"])
    plain = _repo(tmp_path / "b", {".github/actions/review/action.yml": action.replace("anthropics/claude-code-action", "actions/cache"),
                                   ".github/workflows/r.yml": "on: pull_request\n" + jobs})
    assert scan_ci_model_jobs(plain)["model_jobs"] == 0


@pytest.mark.parametrize("jobs", [SECRET_ONLY, "jobs:\n  sync:\n    steps:\n      - uses: some/vault-sync@v1\n"
                                               "        with:\n          secrets: OPENAI_API_KEY\n"])
def test_a_job_whose_only_sign_is_a_secret_reads_unknown(tmp_path: Path, jobs: str) -> None:
    block = _workflow(tmp_path, "  push:", jobs)
    assert (block["jobs"][0]["state"], block["jobs"][0]["reason"]) == ("unknown", "secret_only")
    assert _state(block) == ("not_measured", "trigger_unresolved")


def test_a_secret_beside_another_sign_stays_a_model_job(tmp_path: Path) -> None:
    assert _workflow(tmp_path, "  push:")["jobs"][0]["state"] == "ungated"  # promptfoo plus the key


@pytest.mark.parametrize("condition", ["\"!contains(github.event.pull_request.labels.*.name, 'skip-ai')\"",
                                       "${{ !(github.event_name == 'workflow_dispatch') }}"])
def test_a_negated_label_or_event_test_reads_unknown(tmp_path: Path, condition: str) -> None:
    jobs = EVAL_STEPS.replace("    runs-on:", f"    if: {condition}\n    runs-on:")
    job = _workflow(tmp_path, "  pull_request:", jobs)["jobs"][0]
    assert (job["state"], job["reason"]) == ("unknown", "condition_unread")


def test_an_evals_path_needs_a_runner() -> None:
    assert _signs("jobs:\n  j:\n    steps:\n      - run: ruff check src/ evals/\n") == []
    assert _signs("jobs:\n  j:\n    steps:\n      - run: uv run python evals/run.py\n") == ["eval_tests"]


# --- review round 2: called workflows, negative labels, unread files ----------------------

LABEL_IF = "    if: contains(github.event.pull_request.labels.*.name, 'run-evals')\n    runs-on:"
CALLER = "on:\n  pull_request:\njobs:\n  run:\n    uses: ./.github/workflows/evals.yml\n"


def test_a_called_job_keeps_its_own_label_gate(tmp_path: Path) -> None:
    callee = "on:\n  workflow_call:\n" + EVAL_STEPS.replace("    runs-on:", LABEL_IF)
    block = scan_ci_model_jobs(_repo(tmp_path, {".github/workflows/evals.yml": callee,
                                                ".github/workflows/ci.yml": CALLER}))
    assert [(j["path"], j["state"], j["gates"]) for j in block["jobs"]] == [
        (".github/workflows/evals.yml", "gated", ["label"])]
    ungated = scan_ci_model_jobs(_repo(tmp_path / "b", {".github/workflows/evals.yml": "on:\n  workflow_call:\n" + EVAL_STEPS,
                                                        ".github/workflows/ci.yml": CALLER}))
    assert [(j["path"], j["state"]) for j in ungated["jobs"]] == [(".github/workflows/evals.yml", "ungated")]


def test_a_callee_with_direct_triggers_takes_the_worst_route(tmp_path: Path) -> None:
    callee = "on:\n  workflow_call:\n  push:\n    paths: ['prompts/**']\n" + EVAL_STEPS
    block = scan_ci_model_jobs(_repo(tmp_path, {".github/workflows/evals.yml": callee,
                                                ".github/workflows/ci.yml": CALLER}))
    assert block["jobs"][0]["state"] == "ungated"  # the caller's pull_request is ungated
    gated_caller = CALLER.replace("  pull_request:\n", "  pull_request:\n    paths: ['prompts/**']\n")
    block = scan_ci_model_jobs(_repo(tmp_path / "b", {".github/workflows/evals.yml": callee,
                                                      ".github/workflows/ci.yml": gated_caller}))
    assert (block["jobs"][0]["state"], block["jobs"][0]["gates"]) == ("gated", ["paths"])


@pytest.mark.parametrize(("condition", "state"), [
    ("contains(github.event.pull_request.labels.*.name, 'skip-ai') == false", "unknown"),
    ("contains(github.event.pull_request.labels.*.name, 'run') != true", "unknown"),
    ("contains(github.event.pull_request.labels.*.name, 'run') == true", "gated"),
])
def test_a_negative_label_comparison_is_not_a_gate(tmp_path: Path, condition: str, state: str) -> None:
    jobs = EVAL_STEPS.replace("    runs-on:", f"    if: {condition}\n    runs-on:")
    assert _workflow(tmp_path, "  pull_request:", jobs)["jobs"][0]["state"] == state


def test_an_unread_workflow_file_blocks_a_met_reading(tmp_path: Path) -> None:
    files = {".github/workflows/e.yml": "on:\n  schedule:\n    - cron: '0 0 * * *'\n" + EVAL_STEPS,
             ".github/workflows/empty.yml": ""}
    block = scan_ci_model_jobs(_repo(tmp_path, files))
    assert (block["unread"], block["ungated"]) == (1, None)
    assert _state(block) == ("not_measured", "ci_jobs_unread")
    del files[".github/workflows/empty.yml"]
    assert _state(scan_ci_model_jobs(_repo(tmp_path / "b", files))) == ("met", None)


# --- review round 3: a provider key beside a gated call, an unread-only repository ------

JOB_ENV_KEY = """jobs:
  evals:
    runs-on: ubuntu-latest
    env:
      OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}
    steps:
      - uses: actions/checkout@v4
      - name: key only
        run: echo ready
        env:
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
      - {cond}run: npx promptfoo@latest eval
"""


def test_a_job_env_key_does_not_undo_a_label_gated_step(tmp_path: Path) -> None:
    gated = JOB_ENV_KEY.format(cond="if: contains(github.event.pull_request.labels.*.name, 'evals')\n        ")
    job = _workflow(tmp_path, "  pull_request:", gated)["jobs"][0]
    assert (job["state"], job["gates"]) == ("gated", ["label"])
    open_job = _workflow(tmp_path / "b", "  pull_request:", JOB_ENV_KEY.format(cond=""))["jobs"][0]
    assert open_job["state"] == "ungated"


def test_a_remote_reusable_workflow_alone_reads_unread_not_no_model_jobs(tmp_path: Path) -> None:
    remote = "on: pull_request\njobs:\n  review:\n    uses: org/ci/.github/workflows/claude-review.yml@v1\n"
    block = scan_ci_model_jobs(_repo(tmp_path, {".github/workflows/r.yml": remote}))
    assert (block["model_jobs"], block["unread"], block["ungated"]) == (None, 1, None)
    assert _state(block) == ("not_measured", "ci_jobs_unread")
    plain = "on: pull_request\njobs:\n  t:\n    steps:\n      - run: pytest\n"
    assert _state(scan_ci_model_jobs(_repo(tmp_path / "b", {".github/workflows/t.yml": plain}))) == (
        "not_measured", "no_model_jobs")


# --- review round 4: a continued flow list, a GitLab docs-skip rule ----------------------------

@pytest.mark.parametrize(("types", "state"), [
    ("[opened,\n      synchronize]", "unknown"),
    ("[opened, reopened]", "gated"),
])
def test_a_flow_list_continued_on_the_next_line_is_not_read(tmp_path: Path, types: str, state: str) -> None:
    assert _workflow(tmp_path, f"  pull_request:\n    types: {types}")["jobs"][0]["state"] == state


def test_a_gitlab_docs_skip_rule_is_not_a_gate(tmp_path: Path) -> None:
    rules = "  rules:\n    - changes: [docs/**]\n      when: never\n    - when: always\n"
    assert _gitlab(tmp_path, rules)["jobs"][0]["state"] == "ungated"
    only = "  rules:\n    - changes: [prompts/**]\n      when: always\n"
    assert _gitlab(tmp_path / "b", only)["jobs"][0]["gates"] == ["changes"]


@pytest.mark.parametrize(("command", "signs"), [
    ("pytest --ignore=evals/ tests", []),
    ("pytest --ignore evals/ tests", []),
    ("pytest --ignore-glob=evals/* tests", []),
    ("pytest --deselect evals/test_llm.py tests", []),
    ("pytest evals/", ["eval_tests"]),
    ("pytest --ignore=tests/slow evals/", ["eval_tests"]),
])
def test_an_excluded_evals_path_is_not_a_sign(command: str, signs: list[str]) -> None:
    assert _signs(f"jobs:\n  j:\n    steps:\n      - run: {command}\n") == signs
