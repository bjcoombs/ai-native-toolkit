"""Does the repository's CI run a test or lint command? (issue #698)

Layer 5 level 1 of the default maturity model reads this block, so the CI row
measures CI rather than the instruction file Layer 0 already scores. The
evidence is static, read from the files at HEAD:

- The CI configuration files ``lib.command_index`` indexes: GitHub Actions
  workflows (``.github/workflows/*.yml|yaml``) and ``.gitlab-ci.yml``. Other CI
  systems (Jenkins, CircleCI, Azure Pipelines) are not read, so a repository
  that uses only them reads ``no_ci``.
- Each line with its ``- `` / ``run:`` / ``script:`` lead dropped
  (``command_index.ci_line_text``), split into shell segments and tokenised the
  way instruction-file commands are (``command_resolver.extract_commands``),
  and classified with ``check_command.is_check_command``: any test or
  check target of a modelled runner counts (``pytest``, ``make check``,
  ``npm test``, ``uv run ruff check``, ``./scripts/test.sh``).
- ``is_check_command`` knows test and check targets but not lint ones
  (``make lint`` is not a check it credits in an instruction file), so a CI
  line also counts when ``check_command.is_lint_command`` credits it: a linter
  run as the program (``ruff``, ``eslint``, ``mypy``, ``uv run flake8``), or a
  lint target of a runner or script (``make lint``, ``npm run lint``,
  ``go vet``, ``cargo clippy``, ``./scripts/lint.sh``). An install that names a
  linter (``npm install eslint``) never counts.
- Each line is classified on its own, so an apostrophe in a step name or a
  fenced block inside a script cannot swallow the lines after it; a command
  continued with ``\\`` is read from its first line.
- Triggers are not read: a check in a workflow that runs only on push counts
  the same as one on pull requests.

``test_or_lint_runs`` is true when any CI line runs a check. With none it is
false, except when a CI file pulls jobs from a file this checkout does not
hold (GitLab ``include:`` of any kind, a GitHub job-level ``uses:`` calling
another repository's reusable workflow; ``shared_tooling.pulls_jobs_from_elsewhere``):
the check may live there, so the signal is null (unknown), never false.
``basis`` names which case applied.
"""
from __future__ import annotations

from pathlib import Path
from typing import Literal, NotRequired, TypedDict

from lib.check_command import is_check_command, is_lint_command
from lib.command_index import CI_CONFIG_FILES, ci_config_files, ci_line_text, read_config, repo_files
from lib.command_resolver import extract_commands
from lib.shared_tooling import pulls_jobs_from_elsewhere

CiChecksBasis = Literal["check_command", "included_jobs", "no_check_command", "no_ci"]


class CiChecksBlock(TypedDict):
    """``run-context.json`` ``ci_checks``; a degraded block is ``{available: false, reason}``."""

    available: bool
    reason: NotRequired[str]
    ci_files: int
    check_commands: int
    included_files: int
    test_or_lint_runs: bool | None
    basis: CiChecksBasis


def _line_runs_check(line: str) -> bool:
    """One CI line, classified on its own so an open quote or a stray fence cannot carry over."""
    text = ci_line_text(line).removesuffix("\\").strip()
    if not text:
        return False
    commands = extract_commands(f"```bash\n{text}\n```\n")
    return any(is_check_command(c.text) or is_lint_command(c.text) for c in commands)


def check_commands(text: str) -> int:
    """How many lines of one CI file's text run a test or lint check."""
    return sum(1 for line in text.splitlines() if _line_runs_check(line))


def scan_ci_checks(repo_root: Path) -> CiChecksBlock:
    """Build the ``ci_checks`` block for the repository at ``repo_root``."""
    root = repo_root.resolve()
    files = ci_config_files(repo_files(root))
    checks = 0
    included = 0
    for rel in files:
        text = read_config(root, rel)
        checks += check_commands(text)
        included += pulls_jobs_from_elsewhere(text, gitlab=rel in CI_CONFIG_FILES)
    runs: bool | None
    basis: CiChecksBasis
    if checks:
        runs, basis = True, "check_command"
    elif included:
        runs, basis = None, "included_jobs"
    elif files:
        runs, basis = False, "no_check_command"
    else:
        runs, basis = False, "no_ci"
    return {"available": True, "ci_files": len(files), "check_commands": checks,
            "included_files": included, "test_or_lint_runs": runs, "basis": basis}
