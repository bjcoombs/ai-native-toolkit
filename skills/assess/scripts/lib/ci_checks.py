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
  line also counts when it is a lint command (``is_lint_command``): a runner or
  committed script whose target, script or program names lint (``make lint``,
  ``npm run lint``, ``./scripts/lint.sh``), or a linter run directly (``ruff``,
  ``eslint``, ``flake8``, ``mypy``, ``golangci-lint``, ``cargo clippy``,
  ``go vet``).
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

import re
from pathlib import Path, PurePosixPath
from typing import Literal, TypedDict

from lib.check_command import is_check_command
from lib.command_index import CI_CONFIG_FILES, ci_config_files, ci_line_text, read_config, repo_files
from lib.command_resolver import RUNNERS, command_words, extract_commands
from lib.shared_tooling import pulls_jobs_from_elsewhere

CiChecksBasis = Literal["check_command", "included_jobs", "no_check_command", "no_ci"]


class CiChecksBlock(TypedDict):
    """``run-context.json`` ``ci_checks``."""

    available: bool
    ci_files: int
    check_commands: int
    included_files: int
    test_or_lint_runs: bool | None
    basis: CiChecksBasis


# Linters run as their own program, and the words a lint target or script is named by.
LINTERS = frozenset({
    "ruff", "eslint", "flake8", "pylint", "mypy", "golangci-lint", "shellcheck", "rubocop",
    "stylelint", "ktlint", "biome", "clippy", "vet", "lint", "linter", "lints",
})
# How far into a command the linted target can sit: `uv run --frozen ruff`.
_LINT_REACH = 5


def _names_linter(word: str) -> bool:
    return any(part in LINTERS for part in re.split(r"[:_./-]", PurePosixPath(word).name.lower()))


def is_lint_command(text: str) -> bool:
    """A linter run directly, or a runner or committed script whose target names lint."""
    words = command_words(text)
    if not words:
        return False
    if words[0] in LINTERS:
        return True
    if words[0] not in RUNNERS and not words[0].startswith("./"):
        return False
    return any(_names_linter(w) for w in words[:_LINT_REACH] if not w.startswith("-"))


def check_commands(text: str) -> int:
    """How many command segments in one CI file's text run a test or lint check."""
    lines = (ci_line_text(line) for line in text.splitlines())
    fenced = "```bash\n" + "\n".join(line for line in lines if line) + "\n```\n"
    return sum(1 for cmd in extract_commands(fenced)
               if is_check_command(cmd.text) or is_lint_command(cmd.text))


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
