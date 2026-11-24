"""Committed guards against secrets: a pre-commit hook and a CI scan step (#1173).

A token committed once lives in the history of every clone. A guard catches it before it lands:
a secrets hook in the pre-commit config, which a contributor can skip with ``--no-verify``, or a
secrets scan in CI, which runs on every change whoever sends it. This module reads both from the
commit; GitHub's own secret scanning and push protection are repository settings, read by a
platform collector. No model is involved and no scanner runs.

The tendency it compensates for is guardrail erosion: a token pasted into a config or a skill
"just for now" is never removed, and nothing at the door stops the next one.

**What counts** (the tool names in ``SECRET_TOOLS``):

- ``pre_commit``: a root ``.pre-commit-config.yaml`` (or ``.yml``) whose ``repo:`` or ``id:``
  names a secrets tool (gitleaks, detect-secrets, trufflehog, ggshield, talisman, secretlint,
  git-secrets, ripsecrets).
- ``ci``: a GitHub Actions workflow directly under ``.github/workflows/`` (the files GitHub runs)
  or the root ``.gitlab-ci.yml`` with a step that uses a secrets action
  (``ACTION_SLUGS``: ``gitleaks/gitleaks-action``, ``trufflesecurity/trufflehog``,
  ``GitGuardian/ggshield-action``, ...) or runs a secrets tool as a command (its name, then a
  subcommand, a flag or the end of the line), or that includes GitLab's
  ``Secret-Detection.gitlab-ci.yml`` template or defines a visible ``secret_detection`` job (a
  hidden ``.secret_detection`` job is a template GitLab never runs). Comments, whole-line or
  trailing, never count, so neither does a ``gitleaks:allow`` note or a ``.gitleaks.toml`` path.
  A masked scan does not count, since it guards nothing: a line ending ``|| true``, or a GitHub
  step or job with ``continue-on-error: true`` or ``if: false``, a GitLab job with
  ``allow_failure: true`` that runs a tool command, or a GitLab ``secret_detection`` job with
  ``when: never``. GitLab's Secret-Detection template and its ``secret_detection`` job count even
  though the template sets ``allow_failure: true``: that job never reports by failing; its
  findings reach the merge request through its report artifact, where an approval policy can
  require them, so ``allow_failure`` masks nothing it reports. A GitHub workflow counts only when its ``on:`` names a change event
  (``CHANGE_EVENTS``: push, pull_request, pull_request_target, merge_group): a schedule-only
  workflow scans nothing before it lands.

**The two levels** (maintainer decision on #1173):

- ``configured`` (level 1): a guard is configured from any source. A pre-commit hook is enough,
  so the condition is graded ``config_only``: listed, not counted.
- ``enforcement`` (level 2): a guard runs where an agent cannot skip it, a CI scan (and, once
  read, GitHub's secret scanning or push protection). Graded ``measured`` and counted. With no CI
  scan on a github.com remote, GitHub's own settings could still guard the repository, so the
  reading is not measured (``secret_scanning_not_read``), never unmet; likewise
  (``ci_includes_unresolved``) when a GitLab include that could hold a scan was not read, since
  ``.gitlab-ci.yml``'s includes are read through ``lib.ci_includes``; on any other remote, or
  none, there is no such setting, and no CI scan reads unmet.
- ``sources`` records each source that shows a guard with its own grade (``pre_commit``
  config_only, ``ci`` measured), since a condition's grade is fixed per signal.

Block (``secrets_guard`` in run-context.json)::

    {"available": True,
     "pre_commit": {"present": bool, "file": str | None, "tools": [str, ...]},
     "ci": {"present": bool, "files": [str, ...], "tools": [str, ...]},
     "configured": bool,
     "enforcement": {"available": True, "present": bool, "source": "ci" | None}
                    | {"available": False, "reason": "secret_scanning_not_read: ..."},
     "sources": [{"source": "pre_commit" | "ci", "grade": str}, ...]}

Counts-only output drops the file paths, or hashes them under ``--hash-paths``, and keeps the
booleans and tool tokens.

Secrets guard (#1173), no model. `scan_secrets_guard(repo_root, scope, ghsync_root)` reads the root pre-commit
config for a `SECRET_TOOLS` hook and the GitHub workflows and `.gitlab-ci.yml` for a secrets action,
command, the GitLab `Secret-Detection` template or a `secret_detection` job (a masked `|| true` line
does not count). Tests: `tests/test_secrets_guard.py`.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import TypedDict

from lib.ci_includes import SETTLED, resolve_includes

# The secrets scanners a hook, an action or a command can name.
SECRET_TOOLS = ("gitleaks", "detect-secrets", "trufflehog", "ggshield", "talisman", "secretlint",
                "git-secrets", "ripsecrets")
PRE_COMMIT_FILES = (".pre-commit-config.yaml", ".pre-commit-config.yml")
_TOOL = re.compile(r"(?<![\w-])(" + "|".join(re.escape(t) for t in SECRET_TOOLS) + r")(?![\w-])", re.IGNORECASE)
_PRE_COMMIT_KEY = re.compile(r"^\s*-?\s*(?:repo|id)\s*:\s*(.+)$")
# GitLab's template (Auto DevOps includes it) or its CI/CD component (``.../secret-detection@<v>``).
_GITLAB_TEMPLATE = re.compile(r"(?:Secret-Detection(?:\.latest)?|Auto-DevOps)\.gitlab-ci\.yml"
                              r"|component:\s*['\"]?\S*/secret-detection@\S+", re.IGNORECASE)
# The include kinds that can add a scan this run cannot see; a template or component is named by
# its path, which _GITLAB_TEMPLATE reads, so an unread one hides nothing.
_HIDING_KINDS = frozenset({"local", "project", "remote", "unknown"})
# A visible job: a hidden one (``.secret_detection:``) is a template GitLab never runs.
_GITLAB_JOB = re.compile(r"^secret_detection\s*:", re.MULTILINE)
_MASKED = re.compile(r"\|\|\s*(?:true|:|exit\s+0)\b")
_COMMENT = re.compile(r"^\s*#")
_TRAILING_COMMENT = re.compile(r"\s+#.*$")
# The secrets actions a ``uses:`` step names (owner/name, any ref).
ACTION_SLUGS = ("gitleaks/gitleaks-action", "trufflesecurity/trufflehog", "gitguardian/ggshield-action",
                "secretlint/secretlint-action", "zricethezav/gitleaks-action")
_USES = re.compile(r"^\s*-?\s*uses:\s*['\"]?([\w.-]+/[\w.-]+)", re.IGNORECASE)
# A tool run as a command: its name as a word, then a subcommand or flag or the end of the line,
# at the start of a shell line (indented, as inside a ``run: |`` or ``script:`` block), after ``run:``/``script:``/``- ``, after a pipe or ``&&``, or after
# ``docker run <image>`` (so a path such as ``.gitleaks.toml`` or a ``gitleaks:allow`` note is not).
_COMMAND = re.compile(
    r"(?:^\s*|[;&|]\s*|(?:run|script):\s*|^\s*-\s+|docker run\s+(?:--?\S+\s+)*\S*/)(?:[\w./-]*/)?("
    + "|".join(re.escape(t) for t in SECRET_TOOLS) + r")(?=\s|$|@)", re.IGNORECASE)
# A step or job that cannot fail the run (GitHub ``continue-on-error: true``, GitLab
# ``allow_failure: true``) or never runs (``if: false``).
_CONTINUE_ON_ERROR = re.compile(r"^\s*(?:-\s+)?(?:(?:continue-on-error|allow_failure):\s*['\"]?true\b|if:\s*(?:\$\{\{\s*)?false\s*(?:\}\})?\s*$)",
                                re.IGNORECASE)
# The events that run a GitHub workflow on each change, before or as it lands.
CHANGE_EVENTS = frozenset({"push", "pull_request", "pull_request_target", "merge_group"})
_ON = re.compile(r"""^(?:on|"on"|'on'|true)\s*:\s*(.*)$""")
_WORD = re.compile(r"[\w-]+")
_WHEN_NEVER = re.compile(r"^\s*-?\s*when:\s*['\"]?never\b", re.IGNORECASE)
_STEP_START = re.compile(r"^(\s*)-\s")
# A list of job or stage names, not commands: ``needs: [- gitleaks]`` names a job called gitleaks.
_NAME_LIST = re.compile(r"^\s*(?:needs|dependencies|extends|stages)\s*:\s*$")


class PreCommitGuard(TypedDict):
    present: bool
    file: str | None
    tools: list[str]


class CiGuard(TypedDict):
    present: bool
    files: list[str]
    tools: list[str]
    includes_unresolved: int  # GitLab includes that could hold a scan and were not read


class Enforcement(TypedDict, total=False):
    available: bool  # false: GitHub's own scanning was not read (secret_scanning_not_read)
    reason: str
    present: bool
    source: str | None  # "ci" (and "platform" once the GitHub setting is read)


class SourceGrade(TypedDict):
    source: str  # pre_commit | ci
    grade: str  # the source's own evidence grade (lib.signal_grades): config_only | measured


class SecretsGuardBlock(TypedDict):
    """``run-context.json`` ``secrets_guard`` (see the module doc)."""

    available: bool
    pre_commit: PreCommitGuard
    ci: CiGuard
    configured: bool  # level 1: a guard is configured, from any source
    enforcement: Enforcement  # level 2: a guard runs where an agent cannot skip it
    sources: list[SourceGrade]  # each source that shows a guard, with its own grade


def _read(path: Path) -> str | None:
    if not path.is_file() or path.is_symlink():
        return None
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _tools_in(text: str) -> list[str]:
    return sorted({m.group(1).lower() for m in _TOOL.finditer(text)})


def pre_commit_guard(root: Path) -> PreCommitGuard:
    """The root pre-commit config's secrets hooks (a ``repo:`` URL or an ``id:`` naming a tool)."""
    for name in PRE_COMMIT_FILES:
        text = _read(root / name)
        if text is None:
            continue
        tools: set[str] = set()
        for line in text.splitlines():
            if _COMMENT.match(line):
                continue
            m = _PRE_COMMIT_KEY.match(line)
            if m:
                tools.update(_tools_in(m.group(1)))
        return {"present": bool(tools), "file": name, "tools": sorted(tools)}
    return {"present": False, "file": None, "tools": []}


def _ci_files(root: Path) -> list[Path]:
    workflows = root / ".github" / "workflows"
    found = sorted(p for p in workflows.iterdir() if p.suffix in (".yml", ".yaml")) if workflows.is_dir() else []
    gitlab = root / ".gitlab-ci.yml"
    return [*found, *([gitlab] if gitlab.is_file() else [])]


def _code_lines(text: str) -> list[str]:
    """The lines with comments removed, a whole-line comment as an empty line so line numbers hold."""
    return ["" if _COMMENT.match(line) else _TRAILING_COMMENT.sub("", line) for line in text.splitlines()]


def _block_end(lines: list[str], start: int, indent: int) -> int:
    """The index after the YAML block that opens at ``start`` with ``indent`` (its list item or key)."""
    for i in range(start + 1, len(lines)):
        line = lines[i]
        if line.strip() and len(line) - len(line.lstrip()) <= indent:
            return i
    return len(lines)


def _masked_lines(lines: list[str]) -> set[int]:
    """The lines inside a GitHub step or job that sets ``continue-on-error: true``: that step's or job's
    failure never stops the run, so a scan in it guards nothing."""
    masked: set[int] = set()
    for i, line in enumerate(lines):
        if not _CONTINUE_ON_ERROR.match(line):
            continue
        indent = len(line) - len(line.lstrip())
        if _STEP_START.match(line):  # ``- continue-on-error: true`` opens the step it masks
            masked.update(range(i, _block_end(lines, i, indent)))
            continue
        for j in range(i, -1, -1):  # the enclosing step (a list item) or job (a key one level up)
            opener = lines[j]
            if not opener.strip():
                continue
            ind = len(opener) - len(opener.lstrip())
            if ind < indent or (ind == indent - 2 and _STEP_START.match(opener)):
                masked.update(range(j, _block_end(lines, j, ind)))
                break
    return masked


def _runs_on_change(lines: list[str]) -> bool:
    """Whether a GitHub workflow's ``on:`` names a change event (``CHANGE_EVENTS``): a schedule-only or
    manual workflow scans nothing before it lands. A workflow with no readable ``on:`` does not count."""
    for i, line in enumerate(lines):
        m = _ON.match(line)
        if not m:
            continue
        if m.group(1).strip():
            return bool(CHANGE_EVENTS & set(_WORD.findall(m.group(1))))
        end = _block_end(lines, i, 0)
        keys = [ln.strip().lstrip("- ").split(":")[0].strip() for ln in lines[i + 1:end] if ln.strip()]
        return bool(CHANGE_EVENTS & set(keys))
    return False


def _gitlab_job_disabled(lines: list[str]) -> bool:
    """Whether the visible ``secret_detection`` job sets ``when: never``, which switches off the job
    and the template's job it overrides."""
    for i, line in enumerate(lines):
        if _GITLAB_JOB.match(line):
            return any(_WHEN_NEVER.match(ln) for ln in lines[i + 1:_block_end(lines, i, 0)])
    return False


def _in_name_list(lines: list[str], i: int) -> bool:
    """Whether list item ``i`` sits under a ``needs:``, ``dependencies:``, ``extends:`` or ``stages:`` key."""
    indent = len(lines[i]) - len(lines[i].lstrip())
    for j in range(i - 1, -1, -1):
        line = lines[j]
        if line.strip() and len(line) - len(line.lstrip()) < indent:
            return bool(_NAME_LIST.match(line))
        if line.strip() and len(line) - len(line.lstrip()) == indent and not _STEP_START.match(line):
            return bool(_NAME_LIST.match(line))
    return False


def _ci_tools(text: str) -> list[str]:
    """The secrets tools a CI file uses or runs, unmasked; ``secret_detection`` for GitLab's template
    or visible job. Comments never count, and neither does a masked step or job."""
    lines = _code_lines(text)
    masked = _masked_lines(lines)
    tools: set[str] = set()
    for i, line in enumerate(lines):
        if i in masked or not line.strip() or _MASKED.search(line):
            continue
        uses = _USES.match(line)
        if uses:
            slug = uses.group(1).lower()
            tools.update(t for t in SECRET_TOOLS if slug in ACTION_SLUGS and t in slug)
            continue
        if _STEP_START.match(line) and _in_name_list(lines, i):
            continue
        tools.update(m.group(1).lower() for m in _COMMAND.finditer(line))
    code = "\n".join(lines)
    if (_GITLAB_TEMPLATE.search(code) or _GITLAB_JOB.search(code)) and not _gitlab_job_disabled(lines):
        tools.add("secret_detection")
    return sorted(tools)


def ci_guard(root: Path, ghsync_root: Path | None = None) -> CiGuard:
    """The CI files with a secrets scan step, and the tools they use."""
    files: list[str] = []
    tools: set[str] = set()
    for path in _ci_files(root):
        text = _read(path)
        found = _ci_tools(text) if text is not None else []
        if found and path.relative_to(root).parts[:2] == (".github", "workflows") \
                and not _runs_on_change(_code_lines(text or "")):
            found = []
        if found:
            files.append(path.relative_to(root).as_posix())
            tools.update(found)
    unresolved = 0
    if (root / ".gitlab-ci.yml").is_file():  # the files .gitlab-ci.yml includes (lib.ci_includes)
        resolution = resolve_includes(root, ghsync_root)  # a project include resolves from a ghsync clone
        for included in resolution.files:
            found = _ci_tools(included.text)
            if found:
                files.append(f".gitlab-ci.yml include (line {included.line})")
                tools.update(found)
        unresolved = sum(r["status"] not in SETTLED and r["kind"] in _HIDING_KINDS
                         for r in resolution.includes)
    return {"present": bool(files), "files": files, "tools": sorted(tools), "includes_unresolved": unresolved}


REASON_NOT_READ = "secret_scanning_not_read"
REASON_INCLUDES = "ci_includes_unresolved"
# A github.com remote, HTTPS or SSH (the shape lib.hosting.gh_cli parses; read here with plain git
# so this offline scan never imports a platform client).
_GITHUB_REMOTE = re.compile(r"^(?:https?://(?:[^@/]+@)?github\.com/|git@github\.com:|ssh://git@github\.com/)",
                            re.IGNORECASE)


def _on_github(root: Path) -> bool:
    """Whether ``origin`` (else the sole remote) is on github.com; false when git cannot tell."""
    def remote(*args: str) -> str:
        try:
            proc = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True,
                                  encoding="utf-8", errors="replace", timeout=10)
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return ""
        return proc.stdout.strip() if proc.returncode == 0 else ""
    url = remote("remote", "get-url", "origin")
    if not url:
        names = remote("remote").split()
        url = remote("remote", "get-url", names[0]) if len(names) == 1 else ""
    return bool(_GITHUB_REMOTE.match(url))
_GRADES = {"pre_commit": "config_only", "ci": "measured"}


def _enforcement(root: Path, ci: CiGuard) -> Enforcement:
    """Level 2: a CI scan is present; else, on a github.com remote, GitHub's own secret scanning
    could still guard the repository and is not read here, so the reading is not measured."""
    if ci["present"]:
        return {"available": True, "present": True, "source": "ci"}
    if ci["includes_unresolved"]:
        return {"available": False,
                "reason": f"{REASON_INCLUDES}: no secrets scan in the CI read, and "
                          f"{ci['includes_unresolved']} GitLab include(s) that could hold one were not read"}
    if _on_github(root):
        return {"available": False,
                "reason": f"{REASON_NOT_READ}: no secrets scan in CI, and GitHub's own secret scanning "
                          "and push protection, repository settings, were not read"}
    return {"available": True, "present": False, "source": None}


def scan_secrets_guard(repo_root: Path, scope: Path | None = None,
                       ghsync_root: Path | None = None) -> SecretsGuardBlock:
    """Build the ``secrets_guard`` block (see the module doc). The pre-commit config and CI are the
    repository's, so a ``scope`` reads the root's, as ``guardrails`` reads the root pre-commit config."""
    root = repo_root.resolve()
    hook, ci = pre_commit_guard(root), ci_guard(root, ghsync_root)
    sources = [SourceGrade(source=name, grade=_GRADES[name])
               for name, found in (("pre_commit", hook["present"]), ("ci", ci["present"])) if found]
    return {"available": True, "pre_commit": hook, "ci": ci, "configured": bool(sources),
            "enforcement": _enforcement(root, ci), "sources": sources}
