"""lib/ci_model_jobs.py (#1249): ``claude plugin eval`` is an ``agent_cli`` sign, and more package
managers' install lines are not signs.

Each recognised form has a mirror: a job that only installs or names the CLI (in an ``echo``, a
comment or a quoted argument) is not credited.
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


def _signs(text: str) -> list[str]:
    tree = IndentTree(parse_lines(text.splitlines()))
    return signs_in(tree, range(len(tree.lines)))


def _step(command: str) -> list[str]:
    return _signs(f"jobs:\n  j:\n    steps:\n      - run: {command}\n")


def _git(root: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-qm", "c"],
                   cwd=root, check=True)
    return root


def _state(block: Any) -> tuple[str, str | None]:
    cond = next(c for c in lt.level_result(lt.default_model(), {"ci_model_jobs": block})["conditions"]
                if c["id"] == CID)
    return cond["state"], cond.get("reason")


# --- claude plugin eval: one line -----------------------------------------------------------

@pytest.mark.parametrize("command", [
    "claude plugin eval ./my-plugin",
    "claude plugin eval --json .",                      # options after eval
    "claude --debug plugin eval .",                     # an option before plugin
    "claude --model sonnet plugin eval .",              # an option with a value before plugin
    "claude plugin --verbose eval",                     # an option between plugin and eval
    "npx -y @anthropic-ai/claude-code@latest plugin eval .",
    "'claude plugin eval .'",                           # a quoted command
    "\"claude plugin eval .\"",
    "bash -c 'claude plugin eval .'",
    "npm install -g @anthropic-ai/claude-code && claude plugin eval .",
    "cd plugins/x && claude plugin eval",
])
def test_plugin_eval_is_an_agent_cli_sign(command: str) -> None:
    assert _step(command) == ["agent_cli"]


@pytest.mark.parametrize("command", [
    # Mirrors: installing or naming the CLI is not running an eval.
    "npm install -g @anthropic-ai/claude-code",
    "claude plugin install my-plugin",
    "claude plugin validate .",
    "claude --version",
    "which claude",
    "echo 'claude plugin eval'",
    "echo claude plugin eval",
    "printf '%s' 'run claude plugin eval'",
    "git commit -m \"run claude plugin eval\"",
    "bash -c \"echo 'claude plugin eval'\"",
])
def test_installing_or_naming_the_cli_is_not_a_sign(command: str) -> None:
    assert _step(command) == []


# --- claude plugin eval: a multi-line run: or script: ----------------------------------------

@pytest.mark.parametrize(("text", "signs"), [
    ("jobs:\n  j:\n    steps:\n      - run: |\n          set -e\n          claude plugin \\\n            eval .\n",
     ["agent_cli"]),
    ("jobs:\n  j:\n    steps:\n      - run: >\n          claude plugin\n          eval --json .\n", ["agent_cli"]),
    ("evals:\n  script: |\n    claude --model sonnet \\\n      plugin eval .\n", ["agent_cli"]),
    ("evals:\n  script:\n    - >\n      claude --model sonnet\n      plugin eval .\n", ["agent_cli"]),
    ("evals:\n  script:\n    - claude plugin eval .\n", ["agent_cli"]),
    ("evals:\n  script:\n    - |\n      # a comment line first\n      run_as_user claude plugin eval . \\\n"
     "        --json r.json\n    - echo done\n", ["agent_cli"]),
    # Mirrors: a comment, or an echo whose quoted text runs over lines, names the CLI only.
    ("jobs:\n  j:\n    steps:\n      - run: |\n          # claude plugin eval .\n          make test\n", []),
    ("jobs:\n  j:\n    steps:\n      - run: |\n          echo \"next:\n          claude plugin eval\"\n", []),
    ("evals:\n  script:\n    - echo \"claude plugin eval\"\n", []),
    ("evals:\n  script:\n    - >\n      npm install -g\n      @anthropic-ai/claude-code\n", []),
])
def test_a_multi_line_body_is_read_as_the_shell_reads_it(text: str, signs: list[str]) -> None:
    assert _signs(text) == signs


def test_a_github_plugin_eval_job_on_every_pull_request_reads_ungated(tmp_path: Path) -> None:
    jobs = "jobs:\n  eval:\n    steps:\n      - run: npm install -g @anthropic-ai/claude-code\n" \
           "      - run: claude plugin eval .\n"
    block = dict(scan_ci_model_jobs(_git(tmp_path, {".github/workflows/e.yml": "on: pull_request\n" + jobs})))
    assert [(j["job"], j["signs"], j["state"]) for j in block["jobs"]] == [("eval", ["agent_cli"], "ungated")]
    assert _state(block) == ("unmet", None)
    # Mirror: the same job that only installs the CLI is not a model job.
    only = jobs.replace("claude plugin eval .", "claude --version")
    block = dict(scan_ci_model_jobs(_git(tmp_path / "b", {".github/workflows/e.yml": "on: pull_request\n" + only})))
    assert (block["model_jobs"], _state(block)) == (0, ("not_measured", "no_model_jobs"))


def test_a_gitlab_plugin_eval_job_counts_on_the_command_not_an_echoed_key(tmp_path: Path) -> None:
    ci = "evals:\n  script:\n    - npm install -g @anthropic-ai/claude-code\n    - claude plugin eval .\n"
    block = dict(scan_ci_model_jobs(_git(tmp_path, {".gitlab-ci.yml": ci})))
    assert [(j["job"], j["signs"], j["state"]) for j in block["jobs"]] == [("evals", ["agent_cli"], "ungated")]
    # Mirror: a job that installs the CLI and echoes the command is not a model job.
    echoed = ci.replace("- claude plugin eval .", "- echo \"run claude plugin eval by hand\"")
    block = dict(scan_ci_model_jobs(_git(tmp_path / "b", {".gitlab-ci.yml": echoed})))
    assert block["model_jobs"] == 0


# --- install lines: more package managers ----------------------------------------------------

@pytest.mark.parametrize(("command", "signs"), [
    ("pip3.11 install ragas", []),
    ("pip3.12 install deepeval", []),
    ("python3.11 -m pip3.11 install ragas", []),
    ("pipenv install ragas", []),
    ("rye add ragas", []),
    ("rye tools install ragas", []),
    ("pixi add ragas", []),
    ("pixi global install promptfoo", []),
    ("hatch run pip install ragas", []),
    ("hatch python install 3.12 && pip install ragas", []),
    # Mirrors: the same managers running the harness are still signs.
    ("pipenv run ragas evaluate", ["eval_harness"]),
    ("rye run deepeval test run tests/", ["eval_harness"]),
    ("pixi run promptfoo eval", ["eval_harness"]),
    ("hatch run deepeval test run tests/", ["eval_harness"]),
    ("pip3.11 list && deepeval test run tests/", ["eval_harness"]),
])
def test_versioned_pips_and_more_managers_install_without_a_sign(command: str, signs: list[str]) -> None:
    assert _step(command) == signs
