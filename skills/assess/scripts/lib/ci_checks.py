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
  linter (``npm install eslint``, ``uv sync --extra lint``) never counts, nor
  does a bare list item that only names a linter (``- mypy`` under
  ``matrix:``, ``needs:`` or ``stages:``); ``- ruff check .`` does.
- A bare list item that only names an always-check program (``- pytest``,
  ``- tox``, ``- py.test``, ``- nox``) counts only when its parent key is a
  GitLab ``script:``, ``before_script:`` or ``after_script:``; under
  ``needs:``, a ``matrix:`` axis or any other key it is a value, not a run
  (issue #725). ``- pytest -q`` counts wherever it sits.
- Known limit of the line-based scan (no YAML parser): every line is a
  candidate, so a command inside a non-script value, such as an ``env:`` block
  scalar holding ``pytest``, counts as a run.
- Each line is classified on its own, so an apostrophe in a step name or a
  fenced block inside a script cannot swallow the lines after it; a command
  continued with ``\\`` is read from its first line.
- Triggers are not read: a check in a workflow that runs only on push counts
  the same as one on pull requests.

GitLab includes are resolved offline where the included file is on disk
(``lib/ci_includes.py``, #766): ``local:`` files of the checkout, and
``project:`` / ``component:`` files of a clone in the ghsync root passed as
``ghsync_root`` (``assess_signals --ghsync-root``; ``fleet.py`` passes its
ROOT). Each resolved file's lines are classified the same way and counted in
``included_check_commands``. ``includes`` lists one record per include item
(kind, id, status, ref_basis, depth; never a file name or text), with the
``resolved_includes`` / ``unresolved_includes`` counts.

Under ``--platform`` (and in a full ``/assess`` run) a repository whose
includes did not all resolve offline also gets GitLab's merged configuration
from the CI lint API (``lib/gitlab_ci_lint.py``, #796). Its check lines are
counted in ``merged_check_commands``, apart from the offline counts (the merged
file repeats the own and resolved jobs). ``merged_config`` says what happened:
``not_read`` (offline), ``not_needed`` (every include resolved offline: the
offline reading wins), ``read``, ``invalid`` or ``unavailable``, with the
reason code in ``merged_config_reason``. The merged text is never published.
The merged configuration can lack jobs: the lint API merges in its own
context, so an include behind ``rules:`` may be left out, and a child or
downstream pipeline (a ``trigger:`` job) is not expanded. A merged
configuration with no check therefore reads false only when neither shows
(``merged_partial``: a ``rules:`` key in the ``include:`` section of the
repository's file or of a template read offline, or a ``trigger:`` job in any
of those or in the merged file); otherwise the signal stays null with
``basis: merged_config_partial``. Unknown beats wrong. A ``rules:`` include
inside a template neither the clone nor the merge shows is not seen.

``test_or_lint_runs`` is true when any CI line, own or included, runs a check.
With none it is false, except when a CI file pulls jobs from a file this run
could not read (a GitLab include that did not resolve, a GitHub job-level
``uses:`` calling another repository's reusable workflow;
``shared_tooling.pulls_jobs_from_elsewhere``): the check may live there, so the
signal is null (unknown), never false. A GitLab file whose every include
resolved and holds no check reads false, and so does one whose merged
configuration was read in full and holds none. ``basis`` names which case applied:
``merged_config`` when only the merged configuration found the check.
"""
from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import Path
from typing import Literal, NotRequired, TypedDict

from lib.check_command import ALWAYS_CHECK, LINT_PROGRAMS, is_check_command, is_lint_command
from lib.ci_includes import IncludeRecord, Resolution, resolve_includes
from lib.command_index import CI_CONFIG_FILES, ci_config_files, ci_line_text, read_config, repo_files
from lib.command_resolver import command_words, extract_commands
from lib.merged_ci import MergedStatus, as_merged
from lib.shared_tooling import include_lines, pulls_jobs_from_elsewhere

# A ``rules:`` key inside the include section, block (``rules:``) or flow (``{..., rules: [..]}``).
_INCLUDE_RULES = re.compile(r"""(?:^|[\s{,-])["']?rules["']?\s*:""")


def conditional_include(text: str) -> bool:
    """True when the top-level ``include:`` section of a GitLab CI file has a ``rules:`` key:
    the lint API merges in its own context and may leave that include out (#796)."""
    return any(_INCLUDE_RULES.search(line.split("#", 1)[0]) for _, line, _ in include_lines(text))


# A job-level ``trigger:`` key: a child or downstream pipeline the merge does not expand.
_TRIGGER = re.compile(r"""^\s+["']?trigger["']?\s*:""", re.MULTILINE)


def child_pipeline(text: str) -> bool:
    """True when a GitLab CI file has a job with ``trigger:`` (a child or downstream pipeline)."""
    return _TRIGGER.search(text) is not None


def merged_partial(text: str, resolution: Resolution | None, merged_text: str | None) -> bool:
    """True when GitLab's merged configuration may lack jobs (#796): an include under
    ``rules:`` in the repository's file or a template read offline (the lint API merges in
    its own context and may leave it out), or a ``trigger:`` job in the file, a template or
    the merged file (a child pipeline is not expanded). A check-free merge then stays null."""
    texts = [text, *(f.text for f in (resolution.files if resolution is not None else ()))]
    return (any(conditional_include(t) for t in texts)
            or any(child_pipeline(t) for t in (*texts, merged_text or "")))


CiChecksBasis = Literal["check_command", "merged_config", "included_jobs",
                        "merged_config_partial", "no_check_command", "no_ci"]


class CiChecksBlock(TypedDict):
    """``run-context.json`` ``ci_checks``; a degraded block is ``{available: false, reason}``."""

    available: bool
    reason: NotRequired[str]
    ci_files: int
    check_commands: int
    included_files: int
    included_check_commands: int  # check lines in included files read offline (#766)
    resolved_includes: int
    unresolved_includes: int
    includes: list[IncludeRecord]
    merged_config: MergedStatus  # the CI lint API read (#796)
    merged_config_reason: str | None  # its reason code when unavailable
    merged_check_commands: int  # check lines in the merged configuration
    test_or_lint_runs: bool | None
    basis: CiChecksBasis


# A YAML list item with no key (``- mypy``), as opposed to ``- run: ...``.
_BARE_ITEM = re.compile(r"^\s*-\s+(?![\w.-]+:(?:\s|$))")
# A GitLab key whose list items are shell lines (``script:``, ``  before_script: &s``).
_SCRIPT_KEY = re.compile(r"^\s*(?:before_script|after_script|script):\s*(?:&\S+\s*)?(?:#.*)?$")


def _line_runs_check(line: str, *, script_item: bool = True) -> bool:
    """One CI line, classified on its own so an open quote or a stray fence cannot carry over.

    ``script_item`` is false for a bare list item whose parent key is not a script key
    (``_script_items``): a lone ``- pytest`` there is a value, not a run.
    """
    text = ci_line_text(line).removesuffix("\\").strip()
    if not text:
        return False
    commands = extract_commands(f"```bash\n{text}\n```\n")
    bare_item = _BARE_ITEM.match(line) is not None
    return any(
        (is_check_command(c.text) and not (bare_item and not script_item and _lone_check(c.text)))
        or (is_lint_command(c.text) and not (bare_item and _lone_linter(c.text)))
        for c in commands
    )


def _lone_linter(text: str) -> bool:
    """A linter name with no argument: under ``matrix:`` / ``needs:`` / ``stages:`` a value, not a run."""
    words = command_words(text)
    return len(words) == 1 and words[0] in LINT_PROGRAMS


def _lone_check(text: str) -> bool:
    """An always-check program with no argument (``pytest``, ``tox``): a value unless under ``script:``."""
    words = command_words(text)
    return len(words) == 1 and words[0] in ALWAYS_CHECK


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _script_items(lines: Sequence[str]) -> list[bool]:
    """Per line: false for a bare list item whose parent key is not a GitLab script key.

    The parent is the nearest line above at the item's indent or less that is not a
    sibling item, skipping blank and comment lines and lines nested deeper (a sibling's
    block scalar). An indentless list (``script:`` then ``- pytest`` at its indent) works.
    """
    flags = [True] * len(lines)
    for i, line in enumerate(lines):
        if _BARE_ITEM.match(line) is None or not _lone_check(ci_line_text(line)):
            continue   # only a lone always-check item needs its parent
        depth = _indent(line)
        parent = ""
        for above in reversed(lines[:i]):
            stripped = above.strip()
            if not stripped or stripped.startswith("#") or _indent(above) > depth:
                continue
            if _indent(above) == depth and _BARE_ITEM.match(above) is not None:
                continue
            parent = above
            break
        flags[i] = _SCRIPT_KEY.match(parent) is not None
    return flags


def count_check_lines(lines: Sequence[str], *, context: Sequence[str] | None = None) -> int:
    """How many of one CI file's lines run a test or lint check (a blanked line runs none).

    ``context`` is the file's original lines when ``lines`` is a rewrite of them with
    the same length (``ci_masking.unmasked_lines``): parent keys are read from it.
    """
    flags = _script_items(lines if context is None else context)
    return sum(1 for line, script_item in zip(lines, flags, strict=True)
               if _line_runs_check(line, script_item=script_item))


def check_commands(text: str) -> int:
    """How many lines of one CI file's text run a test or lint check."""
    return count_check_lines(text.splitlines())


def scan_ci_checks(repo_root: Path, ghsync_root: Path | None = None,
                   merged_ci: object = None) -> CiChecksBlock:
    """Build the ``ci_checks`` block for the repository at ``repo_root``. ``merged_ci`` is the
    ``gitlab_merged_ci`` reading (``lib/gitlab_ci_lint.py``); anything else is not read."""
    root = repo_root.resolve()
    merged = as_merged(merged_ci)
    files = ci_config_files(repo_files(root))
    checks = 0
    included = 0
    elsewhere = 0  # files whose pulled jobs this run cannot read
    partial = False  # the merged configuration may lack jobs (a rules: include, a child pipeline)
    resolution = resolve_includes(root, ghsync_root) if any(f in CI_CONFIG_FILES for f in files) else None
    for rel in files:
        text = read_config(root, rel)
        checks += check_commands(text)
        gitlab = rel in CI_CONFIG_FILES
        pulls = pulls_jobs_from_elsewhere(text, gitlab=gitlab)
        included += pulls
        # Fully read only when the resolver saw the includes (a record each) and read them
        # all: a symlinked .gitlab-ci.yml it refuses stays unknown, never false.
        read_all = resolution is not None and bool(resolution.includes) and resolution.unresolved == 0
        if gitlab and merged.read and merged_partial(text, resolution, merged.text):
            partial = True
        merged_all = gitlab and merged.read and not partial
        if pulls and not (gitlab and (read_all or merged_all)):
            elsewhere += 1
    included_checks = sum(check_commands(f.text) for f in resolution.files) if resolution else 0
    merged_checks = check_commands(merged.text) if merged.read and merged.text is not None else 0
    records = list(resolution.includes) if resolution else []
    runs: bool | None
    basis: CiChecksBasis
    if checks or included_checks:
        runs, basis = True, "check_command"
    elif merged_checks:
        runs, basis = True, "merged_config"
    elif elsewhere:
        runs, basis = None, "merged_config_partial" if partial else "included_jobs"
    elif files:
        runs, basis = False, "no_check_command"
    else:
        runs, basis = False, "no_ci"
    return {"available": True, "ci_files": len(files), "check_commands": checks,
            "included_files": included, "included_check_commands": included_checks,
            "resolved_includes": sum(r["status"] == "resolved" for r in records),
            "unresolved_includes": sum(r["status"] != "resolved" for r in records),
            "includes": records, "merged_config": merged.status,
            "merged_config_reason": merged.reason, "merged_check_commands": merged_checks,
            "test_or_lint_runs": runs, "basis": basis}
