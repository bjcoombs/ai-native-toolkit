"""The text of the starter files ``/assess --fix`` writes, built from the repository's facts (#1154).

Each function takes a ``RepoFacts`` (``lib/starter_files.detect_facts``) and returns file text,
so the file fits what the repository already has: its language's setup, the check command its
build already exposes, and its CI system. Nothing here touches the disk.

- ``instruction_file``: a short ``CLAUDE.md`` naming the check command (the ``instructions`` gate).
- ``github_workflow`` / ``gitlab_job``: a CI job that runs one command on pull requests and pushes
  to the default branch (the ``ci_runs_tests`` and ``coverage_gate`` gates).
- ``coverage_command``: the check command with a coverage threshold of
  ``COVERAGE_THRESHOLD`` percent, in a form ``lib/testing/coverage_gate.py`` reads (pytest-cov,
  cargo-llvm-cov, c8), or None where no such form fits the repository.
- ``deny_settings``: ``.claude/settings.json`` holding the deny rule ``next_step_guidance`` words
  for ``agent_ops.summary.permissions_encoded`` (#1153), plus the ``.env`` read denies
  ``agent_harness`` asks for.

The setup steps per language are the common hosted defaults (``actions/setup-*``, the official
images); a repository with an unusual toolchain edits the file in review.
"""
from __future__ import annotations

import json
import re

from lib.next_step_guidance import DENY_RULE_EXAMPLE
from lib.run_context_types_fix import RepoFacts

# The lowest threshold coverage_gate.enforced_meets_minimum credits: a start the team raises.
COVERAGE_THRESHOLD = 50

_PY_INSTALLERS = (("pyproject.toml", "python -m pip install -e ."), ("setup.py", "python -m pip install -e ."),
                  ("requirements.txt", "python -m pip install -r requirements.txt"))
_NODE_INSTALL = (("pnpm-lock.yaml", "corepack enable && pnpm install --frozen-lockfile"),
                 ("yarn.lock", "corepack enable && yarn install --immutable"),
                 ("package-lock.json", "npm ci"))
_JS = frozenset({"javascript", "typescript"})
_JVM = frozenset({"java", "kotlin", "scala", "groovy"})


_YAML_PLAIN = re.compile(r"^[A-Za-z0-9./_-][^#]*$")


def _yaml(command: str) -> str:
    """``command`` as a YAML scalar: plain when it is safe, else a JSON string (valid YAML)."""
    plain = _YAML_PLAIN.match(command) and ": " not in command and not command.endswith(":")
    return command if plain else json.dumps(command)


def _uv(facts: RepoFacts) -> bool:
    return facts["language"] == "python" and "uv.lock" in facts["files"]


def _wrapped(facts: RepoFacts, command: str) -> str:
    """``command`` run inside the project's environment: ``uv run`` in a uv project."""
    if _uv(facts) and not command.startswith(("uv ", "make ", "just ")):
        return f"uv run {command}"
    return command


def _python_installs(facts: RepoFacts, command: str) -> list[str]:
    installs = [cmd for marker, cmd in _PY_INSTALLERS if marker in facts["files"]][:1]
    if command.split()[:1] == ["pytest"]:
        installs.append("python -m pip install pytest")
    return installs


def _node_install(facts: RepoFacts) -> str:
    return next((cmd for marker, cmd in _NODE_INSTALL if marker in facts["files"]), "npm install")


def _github_setup(facts: RepoFacts, command: str) -> list[str]:
    """The workflow steps before the command, as YAML lines indented for ``steps:``."""
    lang = facts["language"]
    if _uv(facts):
        return ["      - uses: astral-sh/setup-uv@v6", "      - run: uv sync --locked"]
    if lang == "python":
        return (["      - uses: actions/setup-python@v5", "        with:", "          python-version: \"3.12\""]
                + [f"      - run: {cmd}" for cmd in _python_installs(facts, command)])
    if lang in _JS:
        return ["      - uses: actions/setup-node@v4", "        with:", "          node-version: 22",
                f"      - run: {_node_install(facts)}"]
    if lang == "go":
        return ["      - uses: actions/setup-go@v5", "        with:", "          go-version-file: go.mod"]
    if lang == "rust":
        return ["      - uses: dtolnay/rust-toolchain@stable"]
    if lang in _JVM:
        return ["      - uses: actions/setup-java@v4", "        with:", "          distribution: temurin",
                "          java-version: \"21\""]
    return []


def _gitlab_image(facts: RepoFacts) -> tuple[str | None, list[str]]:
    """The job image and its ``before_script`` lines."""
    lang = facts["language"]
    if _uv(facts):
        return "ghcr.io/astral-sh/uv:python3.12-bookworm-slim", ["uv sync --locked"]
    if lang == "python":
        return "python:3.12", []
    if lang in _JS:
        return "node:22", [_node_install(facts)]
    if lang == "go":
        return "golang:1.23", []
    if lang == "rust":
        return "rust:1", []
    if lang in _JVM:
        return ("maven:3-eclipse-temurin-21" if "pom.xml" in facts["files"] else "eclipse-temurin:21"), []
    return None, []


def coverage_command(facts: RepoFacts) -> tuple[str, list[str]] | None:
    """The coverage-gated command and any extra install lines, or None where no form fits."""
    command, lang = facts["check_command"], facts["language"]
    if command is None:
        return None
    pytest = command.split()[:1] == ["pytest"] or command.endswith("run pytest")
    if lang == "python" and pytest:
        if _uv(facts):
            return f"uv run --with pytest-cov pytest --cov --cov-fail-under={COVERAGE_THRESHOLD}", []
        return (f"pytest --cov --cov-fail-under={COVERAGE_THRESHOLD}",
                ["python -m pip install pytest-cov"])
    if lang == "rust":
        return f"cargo llvm-cov --fail-under-lines {COVERAGE_THRESHOLD}", ["cargo install cargo-llvm-cov --locked"]
    if lang in _JS and command.split()[:1] in (["npm"], ["pnpm"], ["yarn"]):
        return f"npx c8 --check-coverage --lines {COVERAGE_THRESHOLD} {command}", []
    return None


def github_workflow(facts: RepoFacts, name: str, command: str, installs: list[str] | None = None) -> str:
    """A workflow with one job that runs ``command`` on pull requests and default-branch pushes."""
    setup = _github_setup(facts, command)
    if facts["language"] == "python" and installs and not _uv(facts):
        setup += [f"      - run: {cmd}" for cmd in installs]
    elif installs and facts["language"] == "rust":
        setup = setup + [f"      - run: {cmd}" for cmd in installs]
    lines = [f"name: {name}", "", "on:", "  pull_request:", "  push:",
             f"    branches: [{facts['default_branch']}]", "", "permissions:", "  contents: read", "",
             "jobs:", f"  {name}:", "    runs-on: ubuntu-latest", "    steps:",
             "      - uses: actions/checkout@v4", *setup, f"      - run: {_yaml(_wrapped(facts, command))}"]
    return "\n".join(lines) + "\n"


def gitlab_job(facts: RepoFacts, name: str, command: str, installs: list[str] | None = None) -> str:
    """One GitLab CI job that runs ``command``; a whole ``.gitlab-ci.yml`` or an included file."""
    image, before = _gitlab_image(facts)
    if facts["language"] == "python" and not _uv(facts):
        before = before + _python_installs(facts, command)
    before = before + list(installs or [])
    lines = [f"{name}:"]
    if image:
        lines.append(f"  image: {image}")
    if before:
        lines += ["  before_script:", *(f"    - {cmd}" for cmd in before)]
    lines += ["  script:", f"    - {_yaml(_wrapped(facts, command))}"]
    return "\n".join(lines) + "\n"


_LANGUAGE_NAMES = {"javascript": "JavaScript", "typescript": "TypeScript", "csharp": "C#", "cpp": "C++"}


def instruction_file(repo_name: str, facts: RepoFacts, guardrail: bool) -> str:
    """A short ``CLAUDE.md``: what the project is, the check command, the guardrail."""
    lang = facts["language"]
    kind = f"a {_LANGUAGE_NAMES.get(lang, lang.capitalize())} project" if lang else "a new project"
    lines = [f"# {repo_name}", "", f"Instructions for coding agents. This is {kind}.", "", "## Check", ""]
    if facts["check_command"]:
        lines += ["Run this before you open a pull request; CI runs the same command.", "", "```bash",
                  _wrapped(facts, facts["check_command"]), "```"]
    else:
        lines += ["Name the command that runs the tests here, so agents run it before they push."]
    if guardrail:
        lines += ["", "## Guardrails", "",
                  f"`.claude/settings.json` denies pushing straight to `{facts['default_branch']}` and "
                  "reading `.env` files. Open a pull request instead."]
    return "\n".join(lines) + "\n"


def deny_rules(facts: RepoFacts) -> list[str]:
    """The guidance's deny rule on the repository's default branch, and the ``.env`` read denies."""
    push = DENY_RULE_EXAMPLE.replace(" main)", f" {facts['default_branch']})")
    return [push, "Read(./.env)", "Read(./.env.*)"]


def deny_settings(facts: RepoFacts) -> str:
    """``.claude/settings.json`` with the deny rule; deny rules hold in every permission mode."""
    return json.dumps({"permissions": {"deny": deny_rules(facts)}}, indent=2) + "\n"
