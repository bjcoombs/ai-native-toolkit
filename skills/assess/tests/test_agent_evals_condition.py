"""lib/ci/agent_evals.py and the agent-evals condition (#850): eval suites CI runs against a baseline."""
from __future__ import annotations

import subprocess
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
    "  t:\n    steps:\n      - run: ruff check evals && pytest tests\n",
    "  t:\n    steps:\n      - run: python -m pip install -r evals/requirements.txt\n",
    "  t:\n    steps:\n      - run: uv pip install -r evals/requirements.txt\n",
    "  t:\n    steps:\n      - run: uv run --frozen ruff check evals\n",
    "  t:\n    steps:\n      - run: |\n          rm -rf evals/.cache\n          sudo pip install evals\n",
    "  t:\n    steps:\n      - run: pytest tests\n      - uses: actions/upload-artifact@v4\n        with:\n"
    "          name: evals\n          path: evals/results.json\n",
    "  t:\n    steps:\n      - uses: actions/cache@v4\n        with:\n          path: evals/.cache\n",
])
def test_a_name_that_runs_nothing_is_not_a_run(tmp_path: Path, jobs: str) -> None:
    root = _repo(tmp_path, {"evals/test_x.py": EVAL_CASE, "evals/baseline.json": "{}",
                            ".github/workflows/t.yml": "on: push\njobs:\n" + jobs})
    block = _block(root)
    assert (block["dirs"][0]["ci"], block["present"]) == (False, False)


@pytest.mark.parametrize("jobs", [
    "  t:\n    steps:\n      - run: |\n          uv sync\n          uv run pytest evals/\n",
    "  t:\n    steps:\n      - run: python evals/run.py\n",
    "  t:\n    steps:\n      - run: python -m pytest evals\n",
    "  t:\n    defaults:\n      run:\n        working-directory: evals\n    steps:\n      - run: pytest\n",
    "  t:\n    steps:\n      - uses: promptfoo/promptfoo-action@v1\n        with:\n          config: evals/promptfooconfig.yaml\n",
])
def test_a_run_bearing_value_is_a_run(tmp_path: Path, jobs: str) -> None:
    root = _repo(tmp_path, {"evals/test_x.py": EVAL_CASE, ".github/workflows/t.yml": "on: push\njobs:\n" + jobs})
    assert _block(root)["dirs"][0]["ci"] is True


@pytest.mark.parametrize(("command", "named"), [
    ('eval "$(ssh-agent -s)"', False),
    ("npx promptfoo eval -c promptfooconfig.yaml", False),
    ("pytest eval/", True),
    ("cd ./eval && pytest", True),
])
def test_a_top_level_eval_folder_is_named_only_as_a_path(tmp_path: Path, command: str, named: bool) -> None:
    flow = f"on: push\njobs:\n  t:\n    steps:\n      - run: {command}\n"
    root = _repo(tmp_path, {"eval/test_agent.py": EVAL_CASE, "eval/baseline.json": "{}", ".github/workflows/t.yml": flow})
    assert _block(root)["present"] is named


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


def _git(root: Path, files: dict[str, str]) -> Path:
    """A committed repository: GitLab local includes are read only from tracked files."""
    _repo(root, files)
    for args in (["init", "-q", "-b", "main"], ["add", "-A"],
                 ["-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-qm", "c"]):
        subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, encoding="utf-8", errors="replace")
    return root


def test_a_suite_run_from_a_gitlab_local_include_meets(tmp_path: Path) -> None:
    # #1573: the include text GitLab merges is read for the suite, not only the root file.
    root = _git(tmp_path, {"evals/test_x.py": EVAL_CASE, "evals/baseline.json": "{}",
                           ".gitlab-ci.yml": "include:\n  - local: ci/evals.yml\n",
                           "ci/evals.yml": "evals:\n  script:\n    - pytest evals/\n"})
    block = _block(root)
    assert (block["present"], block["dirs"][0]["ci"]) == (True, True)
    assert _state(root) == ("met", None)


def test_an_unreadable_gitlab_include_leaves_a_baselined_suite_not_measured(tmp_path: Path) -> None:
    root = _git(tmp_path, {"evals/test_x.py": EVAL_CASE, "evals/baseline.json": "{}",
                           ".gitlab-ci.yml": "include:\n  - local: ci/missing.yml\ntest:\n  script:\n    - pytest tests/\n"})
    block = _block(root)
    assert (block["present"], block["present_reason"]) == (None, "eval_ci_unread")
    assert _state(root) == ("not_measured", "eval_ci_unread")


@pytest.mark.parametrize(("root", "included", "run"), [
    # A root job that replaces the included script: CI runs only the root's command.
    ("include:\n  - local: ci/evals.yml\nevals:\n  script:\n    - pytest tests/\n",
     "evals:\n  script:\n    - pytest evals/\n", False),
    # A hidden template no visible job extends runs nothing.
    ("include:\n  - local: ci/evals.yml\n", ".evals:\n  script:\n    - pytest evals/\n", False),
    # A visible job that extends the included template runs its script.
    ("include:\n  - local: ci/evals.yml\nevals:\n  extends: .evals\n",
     ".evals:\n  script:\n    - pytest evals/\n", True),
])
def test_a_gitlab_include_is_read_as_gitlab_merges_it(tmp_path: Path, root: str, included: str, run: bool) -> None:
    root_dir = _git(tmp_path, {"evals/test_x.py": EVAL_CASE, "evals/baseline.json": "{}",
                               ".gitlab-ci.yml": root, "ci/evals.yml": included})
    block = _block(root_dir)
    assert (block["present"], block["dirs"][0]["ci"]) == (run, run)


@pytest.mark.parametrize("ci", [
    # A hidden template a job takes through a YAML anchor merge runs in that job.
    ".evals: &evals\n  script:\n    - pytest evals/\nevals:\n  <<: *evals\n",
    # So does one a job names through !reference.
    ".evals:\n  script:\n    - pytest evals/\nevals:\n  script:\n    - !reference [.evals, script]\n",
])
def test_a_hidden_template_taken_by_anchor_or_reference_is_a_run(tmp_path: Path, ci: str) -> None:
    root = _repo(tmp_path, {"evals/test_x.py": EVAL_CASE, "evals/baseline.json": "{}", ".gitlab-ci.yml": ci})
    assert _block(root)["dirs"][0]["ci"] is True


INCLUDED_EVALS = "evals:\n  script:\n    - pytest evals/\n"
INCLUDED_TEMPLATE = ".evals:\n  script:\n    - pytest evals/\n"


@pytest.mark.parametrize(("root", "included", "present"), [
    # The merged view cannot settle a flow map over a block map: not a run, and not measured.
    ("include:\n  - local: ci/evals.yml\nevals: {script: [pytest tests/]}\n", INCLUDED_EVALS, None),
    # extends: names a template that does not exist: GitLab rejects the job.
    ("include:\n  - local: ci/evals.yml\nevals:\n  extends: [.evals, .nope]\n", INCLUDED_TEMPLATE, None),
    # An include under rules: GitLab may leave it out.
    ("include:\n  - local: ci/evals.yml\n    rules:\n      - if: $X\n        when: never\n", INCLUDED_EVALS, None),
    # An anchor does not cross files: an anchored template with no alias in its own file runs nothing.
    ("include:\n  - local: ci/evals.yml\n", ".evals: &evals\n  script:\n    - pytest evals/\n", False),
    # A !reference to the template's rules: does not run its script.
    ("include:\n  - local: ci/evals.yml\ntest:\n  rules:\n    - !reference [.evals, rules]\n  script:\n    - pytest tests/\n",
     ".evals:\n  rules:\n    - when: always\n  script:\n    - pytest evals/\n", False),
])
def test_an_unsettled_or_unused_gitlab_job_is_not_a_run(tmp_path: Path, root: str, included: str,
                                                          present: bool | None) -> None:
    block = _block(_git(tmp_path, {"evals/test_x.py": EVAL_CASE, "evals/baseline.json": "{}",
                                   ".gitlab-ci.yml": root, "ci/evals.yml": included}))
    assert (block["present"], block["dirs"][0]["ci"]) == (present, False)


@pytest.mark.parametrize("when", ["never", "manual"])
def test_a_gitlab_job_that_never_runs_on_its_own_is_not_a_run(tmp_path: Path, when: str) -> None:
    root = _repo(tmp_path, {"evals/test_x.py": EVAL_CASE, "evals/baseline.json": "{}",
                            ".gitlab-ci.yml": f"evals:\n  when: {when}\n  script:\n    - pytest evals/\n"})
    assert _block(root)["dirs"][0]["ci"] is False


@pytest.mark.parametrize("ci", [
    # An anchor on a nested key of a hidden job, used by an alias in the same file.
    ".defaults:\n  script: &s\n    - pytest evals/\nevals:\n  script: *s\n",
    # The same, under a hidden job whose own anchor nothing uses.
    ".evals: &evals\n  script: &s\n    - pytest evals/\nevals:\n  script: *s\n",
    # An anchor on a list item.
    ".evals:\n  script:\n    - &run pytest evals/\nevals:\n  script:\n    - *run\n",
])
def test_a_nested_anchor_an_alias_uses_is_a_run(tmp_path: Path, ci: str) -> None:
    root = _repo(tmp_path, {"evals/test_x.py": EVAL_CASE, "evals/baseline.json": "{}", ".gitlab-ci.yml": ci})
    block = _block(root)
    assert (block["present"], block["dirs"][0]["ci"]) == (True, True)


TEMPLATE = ".evals: &evals\n  script:\n    - pytest evals/\n"


@pytest.mark.parametrize("jobs", [
    # #1576: a template a merged job extends stays readable for a job that takes it by !reference.
    "lint:\n  extends: .evals\n  script:\n    - ruff check .\nagent:\n  script:\n    - !reference [.evals, script]\n",
    # The same, for a job that takes it through an anchor alias.
    "lint:\n  extends: .evals\n  script:\n    - ruff check .\nagent:\n  <<: *evals\n",
    # A merging job that sets a key the template does not hold still runs the template's script.
    "evals:\n  <<: *evals\n  image: python:3.12\n",
    # A rules: entry with no when: (on_success) runs the job.
    "evals:\n  <<: *evals\n  rules:\n    - if: $CI_PIPELINE_SOURCE == \"never\"\n      when: never\n    - if: $X\n",
    "evals:\n  script:\n    - pytest evals/\n  rules:\n    - when: always\n",
    # A !reference inside a template a running job takes by alias, or by another !reference.
    ".mid: &mid\n  script:\n    - !reference [.evals, script]\nagent:\n  <<: *mid\n",
    ".mid:\n  script:\n    - !reference [.evals, script]\nagent:\n  script:\n    - !reference [.mid, script]\n",
    # A chain of anchored templates a running job takes through the last one.
    ".mid: &mid\n  <<: *evals\n  image: python:3.12\nevals:\n  <<: *mid\n",
])
def test_a_template_a_running_job_takes_is_a_run(tmp_path: Path, jobs: str) -> None:
    root = _git(tmp_path, {"evals/test_x.py": EVAL_CASE, "evals/baseline.json": "{}",
                           ".gitlab-ci.yml": TEMPLATE + jobs})
    block = _block(root)
    assert (block["present"], block["dirs"][0]["ci"]) == (True, True)


@pytest.mark.parametrize("jobs", [
    # #1576: an alias only in a job that is manual, never runs, or replaces the script.
    "evals:\n  <<: *evals\n  when: manual\n",
    "evals:\n  <<: *evals\n  rules:\n    - when: never\n",
    "evals:\n  <<: *evals\n  rules:\n    - if: $X\n      when: manual\n    - when: never\n",
    "evals:\n  <<: *evals\n  script:\n    - pytest tests/\n",
    # An alias only in a comment, or only in another hidden job.
    "# use *evals later\ntest:\n  script:\n    - pytest tests/\n",
    "test:\n  script:\n    - ruff check .  # was *evals\n",
    ".other:\n  <<: *evals\ntest:\n  script:\n    - pytest tests/\n",
    # An alias under a global keyword holds no job.
    "variables:\n  X: *evals\ntest:\n  script:\n    - pytest tests/\n",
    # A chain whose running job replaces the script the chain carries.
    ".mid: &mid\n  <<: *evals\nevals:\n  <<: *mid\n  script:\n    - pytest tests/\n",
    # A !reference held only by a manual job or by a hidden job nothing uses.
    "evals:\n  when: manual\n  script:\n    - !reference [.evals, script]\n",
    ".other:\n  script:\n    - !reference [.evals, script]\ntest:\n  script:\n    - pytest tests/\n",
    # A job whose every rules: entry is when: never or when: manual.
    "evals:\n  script:\n    - pytest evals/\n  rules:\n    - when: never\n",
    "evals:\n  script:\n    - pytest evals/\n  rules:\n    - if: $X\n      when: manual\n    - when: never\n",
])
def test_a_template_no_running_job_takes_is_not_a_run(tmp_path: Path, jobs: str) -> None:
    root = _git(tmp_path, {"evals/test_x.py": EVAL_CASE, "evals/baseline.json": "{}",
                           ".gitlab-ci.yml": TEMPLATE + jobs})
    block = _block(root)
    assert (block["present"], block["dirs"][0]["ci"]) == (False, False)


def test_a_root_rules_never_over_an_included_job_is_not_a_run(tmp_path: Path) -> None:
    root = _git(tmp_path, {"evals/test_x.py": EVAL_CASE, "evals/baseline.json": "{}",
                           ".gitlab-ci.yml": "include:\n  - local: ci/evals.yml\nevals:\n  rules:\n    - when: never\n",
                           "ci/evals.yml": INCLUDED_EVALS})
    block = _block(root)
    assert (block["present"], block["dirs"][0]["ci"]) == (False, False)


def test_an_included_template_a_merged_job_extends_stays_readable_for_a_reference(tmp_path: Path) -> None:
    # #1576: !reference crosses files; the template ends its include, where the view's blank tail sat.
    root = _git(tmp_path, {"evals/test_x.py": EVAL_CASE, "evals/baseline.json": "{}",
                           ".gitlab-ci.yml": "include:\n  - local: ci/t.yml\nagent:\n  script:\n"
                                             "    - !reference [.evals, script]\nlint:\n  extends: .evals\n"
                                             "  script:\n    - ruff check .\n",
                           "ci/t.yml": INCLUDED_TEMPLATE})
    block = _block(root)
    assert (block["present"], block["dirs"][0]["ci"]) == (True, True)
