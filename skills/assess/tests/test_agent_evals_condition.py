"""lib/ci/agent_evals.py and the agent-evals condition (#850): eval suites CI runs against a baseline."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import lib.scoring.level_tally as lt
from lib.ci.agent_evals import baseline_kinds, suite_of
from lib.ci.ci_model_jobs import scan_ci_model_jobs
from lib.counts_only.content_free import counts_only

CID = "agent-evals.1"
EVAL_CASE = "from anthropic import Anthropic\n\ndef test_answers():\n    client = Anthropic()\n"
EXPRESSION_EVALUATOR = "def evaluate(expr):\n    return parse(expr).fold()\n"
RUNS_EVALS = """name: evals
on:
  pull_request:
    paths: ['prompts/**']
jobs:
  evals:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: uv run pytest evals/ --snapshot-warn-unused
"""
FILTER_ONLY = """name: ci
on:
  push:
    paths: ['evals/**']
jobs:
  test:
    steps:
      - run: pytest tests/ --ignore=evals
"""


def _repo(root: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


def _block(root: Path) -> dict[str, Any]:
    return dict(scan_ci_model_jobs(root)["agent_evals"])


def _state(root: Path) -> tuple[str, str | None]:
    block = scan_ci_model_jobs(root)
    cond = next(c for c in lt.level_result(lt.default_model(), {"ci_model_jobs": block})["conditions"]
                if c["id"] == CID)
    return cond["state"], cond.get("reason")


def test_a_suite_ci_runs_with_a_committed_baseline_meets(tmp_path: Path) -> None:
    root = _repo(tmp_path, {"evals/test_answers.py": EVAL_CASE,
                            "evals/__snapshots__/test_answers.ambr": "# snapshot\n",
                            ".github/workflows/evals.yml": RUNS_EVALS})
    block = _block(root)
    assert block["dirs"] == [{"path": "evals", "ci": True, "baseline": ["snapshot"]}]
    assert (block["present"], block["suites"], block["ci_run"], block["baselined"]) == (True, 1, 1, 1)
    assert _state(root)[0] == "met"


def test_a_suite_no_ci_names_is_unmet(tmp_path: Path) -> None:
    root = _repo(tmp_path, {"evals/test_answers.py": EVAL_CASE,
                            "evals/baseline.json": "{}",
                            ".github/workflows/ci.yml": FILTER_ONLY})
    block = _block(root)
    assert (block["present"], block["dirs"][0]["ci"]) == (False, False)
    assert _state(root)[0] == "unmet"


def test_a_suite_ci_runs_with_no_baseline_is_unmet(tmp_path: Path) -> None:
    root = _repo(tmp_path, {"evals/test_answers.py": EVAL_CASE, ".github/workflows/evals.yml": RUNS_EVALS})
    block = _block(root)
    assert (block["present"], block["dirs"][0]["baseline"]) == (False, [])
    assert _state(root)[0] == "unmet"


def test_an_expression_evaluator_named_eval_does_not_count(tmp_path: Path) -> None:
    root = _repo(tmp_path, {"src/eval/evaluator.py": EXPRESSION_EVALUATOR,
                            "src/eval/golden_results.json": "[]",
                            ".github/workflows/ci.yml": RUNS_EVALS.replace("evals/", "src/eval/")})
    block = _block(root)
    assert (block["suites"], block["set_aside"], block["present"], block["dirs"]) == (0, 1, False, [])
    assert _state(root)[0] == "unmet"


def test_a_recorded_response_fixture_counts_as_a_model_reference(tmp_path: Path) -> None:
    root = _repo(tmp_path, {"eval/run.py": EXPRESSION_EVALUATOR,
                            "eval/cassettes/answers.yaml": "interactions: []\n",
                            ".github/workflows/evals.yml": RUNS_EVALS.replace("evals/", "eval/")})
    assert _block(root)["dirs"] == [{"path": "eval", "ci": True, "baseline": ["cached_responses"]}]


def test_no_suite_is_unmet(tmp_path: Path) -> None:
    root = _repo(tmp_path, {"src/app.py": "print(1)\n"})
    assert _block(root) == {"suites": 0, "set_aside": 0, "ci_run": 0, "baselined": 0, "present": False, "dirs": []}


@pytest.mark.parametrize("folder", ["tests/agent", "tests/llm", "llm_evals", "agent-evals", "evaluations"])
def test_differently_named_eval_tiers_match(tmp_path: Path, folder: str) -> None:
    root = _repo(tmp_path, {f"{folder}/test_x.py": EVAL_CASE, f"{folder}/tool_budget.json": "{}",
                            ".github/workflows/evals.yml": RUNS_EVALS.replace("evals/", f"{folder}/")})
    block = _block(root)
    assert (block["dirs"][0]["path"], block["present"]) == (folder, True)


def test_an_agent_folder_outside_a_test_root_is_not_a_tier() -> None:
    assert suite_of("src/agent/loop.py") is None
    assert suite_of("tests/agent/test_loop.py") == "tests/agent"
    assert suite_of("node_modules/x/evals/a.py") is None
    assert suite_of("docs/design/evals/spec.md") is None
    assert suite_of("evals/a/b/c.json") == "evals"


@pytest.mark.parametrize(("command", "named"), [
    ("pytest -m eval tests/", True),
    ("pytest -m 'not eval'", False),
    ("python -m evals.run", True),
    ("pytest tests/ --ignore=evals", False),
    ("git commit -m eval", False),
])
def test_ci_names_the_suite_by_marker_or_module(tmp_path: Path, command: str, named: bool) -> None:
    flow = f"on: push\njobs:\n  t:\n    steps:\n      - run: {command}\n"
    root = _repo(tmp_path, {"evals/test_x.py": EVAL_CASE, ".github/workflows/t.yml": flow})
    assert _block(root)["dirs"][0]["ci"] is named


@pytest.mark.parametrize("jobs", [
    "  evals:\n    steps:\n      - run: echo hi\n",
    "  t:\n    steps:\n      - name: Skip evals\n        run: pytest tests\n",
    "  t:\n    steps:\n      - run: pytest -k 'not evals' tests\n",
    "  t:\n    steps:\n      - run: echo 'evals are run manually'\n",
    "  t:\n    steps:\n      - run: |\n          echo running evals/ later\n          pytest tests\n",
    "  evals:\n    if: false\n    steps:\n      - run: pytest evals/\n",
    "  t:\n    steps:\n      - if: ${{ false }}\n        run: pytest evals/\n",
    "  t:\n    steps:\n      - run: pytest -m evaluation_slow tests\n",
    "  t:\n    steps:\n      - run: git ls-files -z 'evals/**/scaffold.sh'\n",
])
def test_a_name_that_runs_nothing_is_not_a_run(tmp_path: Path, jobs: str) -> None:
    root = _repo(tmp_path, {"evals/test_x.py": EVAL_CASE, "evals/baseline.json": "{}",
                            ".github/workflows/t.yml": "on: push\njobs:\n" + jobs})
    block = _block(root)
    assert (block["dirs"][0]["ci"], block["present"]) == (False, False)


@pytest.mark.parametrize("jobs", [
    "  t:\n    steps:\n      - run: |\n          uv sync\n          uv run pytest evals/\n",
    "  t:\n    defaults:\n      run:\n        working-directory: evals\n    steps:\n      - run: pytest\n",
    "  t:\n    steps:\n      - uses: promptfoo/promptfoo-action@v1\n        with:\n          config: evals/promptfooconfig.yaml\n",
])
def test_a_run_bearing_value_is_a_run(tmp_path: Path, jobs: str) -> None:
    root = _repo(tmp_path, {"evals/test_x.py": EVAL_CASE, ".github/workflows/t.yml": "on: push\njobs:\n" + jobs})
    assert _block(root)["dirs"][0]["ci"] is True


def test_a_paths_filter_or_comment_is_not_a_run(tmp_path: Path) -> None:
    flow = FILTER_ONLY + "      # - run: pytest evals/\n"
    root = _repo(tmp_path, {"evals/test_x.py": EVAL_CASE, ".github/workflows/ci.yml": flow})
    assert _block(root)["dirs"][0]["ci"] is False


@pytest.mark.parametrize(("runner", "files"), [
    ("make evals", {"Makefile": "evals:\n\tuv run pytest evals/\n"}),
    ("just evals", {"justfile": "evals:\n    pytest evals/\n"}),
    ("npm run evals", {"package.json": '{"scripts": {"evals": "vitest run evals/"}}'}),
])
def test_one_task_runner_level_is_followed(tmp_path: Path, runner: str, files: dict[str, str]) -> None:
    flow = f"on: push\njobs:\n  t:\n    steps:\n      - run: {runner}\n"
    root = _repo(tmp_path, {"evals/test_x.py": EVAL_CASE, "evals/baseline.json": "{}",
                            ".github/workflows/t.yml": flow, **files})
    assert _block(root)["present"] is True


def test_a_task_runner_target_that_does_not_name_the_suite_is_no_run(tmp_path: Path) -> None:
    flow = "on: push\njobs:\n  t:\n    steps:\n      - run: make test\n"
    root = _repo(tmp_path, {"evals/test_x.py": EVAL_CASE, ".github/workflows/t.yml": flow,
                            "Makefile": "test:\n\tpytest tests/\nevals:\n\tpytest evals/\n"})
    assert _block(root)["dirs"][0]["ci"] is False


def test_a_gitlab_job_and_a_local_action_count(tmp_path: Path) -> None:
    gitlab = _repo(tmp_path / "gl", {"evals/test_x.py": EVAL_CASE,
                                     ".gitlab-ci.yml": "evals:\n  script:\n    - pytest evals/\n"})
    assert _block(gitlab)["dirs"][0]["ci"] is True
    action = _repo(tmp_path / "act", {"evals/test_x.py": EVAL_CASE,
                                      ".github/actions/evals/action.yml": "runs:\n  using: composite\n  steps:\n"
                                                                          "    - run: pytest evals\n      shell: bash\n"})
    assert _block(action)["dirs"][0]["ci"] is True


def test_an_unread_ci_file_leaves_a_baselined_suite_not_measured(tmp_path: Path) -> None:
    root = _repo(tmp_path, {"evals/test_x.py": EVAL_CASE, "evals/baseline.json": "{}",
                            ".github/workflows/empty.yml": ""})
    block = _block(root)
    assert (block["present"], block["present_reason"]) == (None, "eval_ci_unread")
    assert _state(root) == ("not_measured", "eval_ci_unread")


@pytest.mark.parametrize(("inner", "kinds"), [
    ("__snapshots__/a.ambr", {"snapshot"}),
    ("test_snapshot.py", set()),
    ("cassettes/run.yaml", {"cached_responses"}),
    ("cached_answers.json", {"cached_responses"}),
    ("tool_budget.yaml", {"tool_budget"}),
    ("golden/out.txt", {"baseline"}),
    ("cases.yaml", set()),
])
def test_baseline_kinds(inner: str, kinds: set[str]) -> None:
    assert baseline_kinds(inner) == kinds


def test_counts_only_keeps_every_count_and_token(tmp_path: Path) -> None:
    root = _repo(tmp_path, {"evals/test_answers.py": EVAL_CASE,
                            "evals/__snapshots__/test_answers.ambr": "# snapshot\n",
                            ".github/workflows/evals.yml": RUNS_EVALS})
    block = scan_ci_model_jobs(root)
    out = counts_only({"ci_model_jobs": block})["ci_model_jobs"]["agent_evals"]
    for field in ("suites", "set_aside", "ci_run", "baselined", "present"):
        assert out[field] == block["agent_evals"][field], field  # type: ignore[literal-required]
    assert out["dirs"] == block["agent_evals"]["dirs"]


def test_counts_only_keeps_the_present_reason(tmp_path: Path) -> None:
    root = _repo(tmp_path, {"evals/test_x.py": EVAL_CASE, "evals/baseline.json": "{}",
                            ".github/workflows/empty.yml": ""})
    out = counts_only({"ci_model_jobs": scan_ci_model_jobs(root)})["ci_model_jobs"]["agent_evals"]
    assert (out["present"], out["present_reason"]) == (None, "eval_ci_unread")
