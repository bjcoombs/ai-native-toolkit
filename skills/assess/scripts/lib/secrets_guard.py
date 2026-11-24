"""Committed guards against secrets: a pre-commit hook and a CI scan step (#1173).

A token committed once lives in the history of every clone. A guard catches it before it lands:
a secrets hook in the pre-commit config, which a contributor can skip with ``--no-verify``, or a
secrets scan in CI, which runs on every change whoever sends it. This module reads both from the
commit; GitHub's own secret scanning and push protection are repository settings, which a
platform collector will read (part B of #1173). No model is involved and no scanner runs.

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
  ``Secret-Detection.gitlab-ci.yml`` template (or its component). A visible ``secret_detection``
  job counts only as an override of that template, never alone. Comments, whole-line or
  trailing, never count, so neither does a ``gitleaks:allow`` note or a ``.gitleaks.toml`` path.
  A masked scan does not count, since it guards nothing: a line ending ``|| true``, or a GitHub
  step or job with ``continue-on-error: true`` or ``if: false``, a GitLab job with
  ``allow_failure: true`` that runs a tool command. GitLab's Secret-Detection template and its ``secret_detection`` job count even
  though the template sets ``allow_failure: true``: that job never reports by failing; its
  findings reach the merge request through its report artifact, where an approval policy can
  require them, so ``allow_failure`` masks nothing it reports. A GitHub workflow counts only when its ``on:`` names a change event
  (``CHANGE_EVENTS``: push, pull_request, pull_request_target, merge_group): a schedule-only
  workflow scans nothing before it lands.
- A command counts only when it can fail the job: ``detect-secrets scan`` always exits 0, so
  only ``detect-secrets-hook`` counts; ``trufflehog`` needs ``--fail``; ``gitleaks`` with
  ``--exit-code 0``, ``--staged`` (a CI checkout has nothing staged), ``version`` or ``--help``
  does not count. A ``||`` whose right side is not a non-zero exit or ``false``, and a pipe, mask
  the command; a command continued with a trailing backslash is judged whole.
- CI that runs the pre-commit hooks counts for the secrets hooks it selects: ``uses:
  pre-commit/action``, or ``pre-commit run`` with ``--all-files``/``-a``, ``--from-ref`` or
  ``--files`` (a bare ``pre-commit run`` checks staged files only). A named hook id counts for its
  tool (``gitleaks-docker`` runs gitleaks), and a hook whose ``stages:`` leaves out the
  pre-commit stage is not selected.
- A GitLab job is judged from its direct keys only, and GitLab ``rules:`` are not evaluated. A job
  with no ``rules:``, ``only:`` or ``except:``, and a job-level ``when:`` of ``always``,
  ``on_success`` or none, runs: its scan counts. A job-level ``when: never`` or ``manual`` is off:
  its scan does not count, and level 2 can read unmet. Any other job (``rules:``,
  ``only:``/``except:``, ``when: delayed``) is unevaluated: its scan is held in
  ``ci.rules_unevaluated`` and level 2 reads not measured (``gitlab_rules_unevaluated``), never
  met. Under-counting a guarded repository is acceptable; reading met for one that is not is not.
  The template follows the same rule over every ``secret_detection`` definition in the merged
  pipeline (root and includes); definitions that disagree leave it unevaluated, and
  ``SECRET_DETECTION_DISABLED: "true"`` switches it off. An ``after_script:`` scan and a hidden
  job do not count, since neither fails the pipeline.

**The two levels** (maintainer decision on #1173):

- ``configured`` (level 1): a guard is configured from any source. A pre-commit hook is enough,
  so the condition is graded ``config_only``: listed, not counted. It reads the files this run
  read: an unread GitLab include leaves level 2 not measured (``ci_includes_unresolved``) but
  level 1 unmet. The asymmetry is deliberate: level 1 is listed and moves no count, so an unmet
  there costs nothing, while level 2 is counted and must not read unmet on evidence it lacks.
- ``enforcement`` (level 2): a guard runs where an agent cannot skip it, a CI scan (and, once
  read, GitHub's secret scanning or push protection). Graded ``measured`` and counted. With no CI
  scan on a github.com remote, GitHub's own settings could still guard the repository, so the
  reading is not measured (``secret_scanning_not_read``), never unmet; likewise
  (``ci_includes_unresolved``) when a GitLab include that could hold a scan was not read, since
  ``.gitlab-ci.yml``'s includes are read through ``lib.ci_includes``;
  a GitHub Enterprise Server or ``*.ghe.com`` remote is not recognised as GitHub yet, so it reads
  unmet with no CI scan although those platforms offer secret scanning (part B reads the setting); on any other remote, or
  none, there is no such setting, and no CI scan reads unmet.
- ``sources`` records each source that shows a guard with its own grade (``pre_commit``
  config_only, ``ci`` measured), since a condition's grade is fixed per signal.

Block (``secrets_guard`` in run-context.json)::

    {"available": True,
     "pre_commit": {"present": bool, "file": str | None, "tools": [str, ...]},
     "ci": {"present": bool, "files": [str, ...], "tools": [str, ...], "includes_unresolved": int},
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

import os
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
# The include kinds that can add a scan this run cannot see. A GitLab template is named by its path,
# which _GITLAB_TEMPLATE reads, so an unread one hides nothing; a component can be anyone's.
_HIDING_KINDS = frozenset({"local", "project", "remote", "component", "unknown"})
# A visible job: a hidden one (``.secret_detection:``) is a template GitLab never runs.
_GITLAB_JOB = re.compile(r"^secret_detection\s*:", re.MULTILINE)
# A command whose failure is swallowed: an ``||`` whose right side is not a non-zero exit or ``false``
# (``|| true``, ``|| :``, ``|| echo leaks found``), or a pipe (``| tee out.txt``: without
# ``pipefail``, the default in GitLab and in a GitHub step with no ``shell: bash``, the last
# command's status wins). A pipe under ``set -o pipefail`` reads masked too: listed as a follow-up.
_MASKED = re.compile(r"\|\|(?!\s*(?:exit\s+[1-9]|false\b))|(?<!\|)\|(?![|])")
_COMMENT = re.compile(r"^\s*#")
# A trailing `` #`` comment outside quotes (``echo "build #1"`` keeps its ``#``).
_TRAILING_COMMENT = re.compile(r"""^((?:[^'"#]|'[^']*'|"[^"]*"|(?<!\s)#)*?)\s+#.*$""")
# The secrets actions a ``uses:`` step names (owner/name, any ref).
ACTION_SLUGS = ("gitleaks/gitleaks-action", "trufflesecurity/trufflehog", "gitguardian/ggshield-action",
                "secretlint/secretlint-action", "zricethezav/gitleaks-action")
_USES = re.compile(r"^\s*-?\s*uses:\s*['\"]?([\w.-]+/[\w.-]+)", re.IGNORECASE)
# A tool run as a command: its name as a word, then a subcommand or flag or the end of the line,
# at the start of a shell line (indented, as inside a ``run: |`` or ``script:`` block), after ``run:``/``script:``/``- ``, after a pipe or ``&&``, or after
# ``docker run <image>`` (so a path such as ``.gitleaks.toml`` or a ``gitleaks:allow`` note is not).
# The command names: the tools, and ``detect-secrets-hook``, the form of detect-secrets that fails.
_COMMAND_NAMES = sorted((*SECRET_TOOLS, "detect-secrets-hook"), key=len, reverse=True)
# A command that cannot fail the job guards nothing: ``detect-secrets scan`` writes a baseline and
# exits 0, ``trufflehog`` exits 0 on findings without ``--fail``, ``gitleaks --exit-code 0``.
_TRUFFLEHOG_FAIL = re.compile(r"(?<![\w-])--fail(?![\w-])")  # not --fail-on-scan-errors
_STAGED_OR_INFO = re.compile(r"--staged\b|\bgitleaks\s+(?:version|help)\b|(?<!\S)(?:--help|-h|--version)(?!\S)")
# A bare ``gitleaks`` shell call prints usage. Only for a command: a ``uses: docker://...gitleaks``
# step ends its line too, with its arguments under ``with:`` below.
_BARE_CALL = re.compile(r"\bgitleaks\s*['\"]?\s*$")
_GITLEAKS_NO_FAIL = re.compile(r"--exit-code[= ]0\b")
_COMMAND = re.compile(
    r"(?:^\s*|[;&|]\s*|(?<![\w-])(?:run|script):\s*|^\s*-\s+|"
    r"(?:npx|bunx|uvx|pipx run|pnpm (?:exec|dlx)|yarn dlx)\s+(?:--?\S+\s+)*|docker run\s+(?:--?\S+\s+)*\S*/)(?:[\w./-]*/)?("
    + "|".join(re.escape(t) for t in _COMMAND_NAMES) + r")(?=\s|$|@)", re.IGNORECASE)
# A step or job that cannot fail the run (GitHub ``continue-on-error: true``, GitLab
# ``allow_failure: true``) or never runs (``if: false``).
_CONTINUE_ON_ERROR = re.compile(r"^\s*(?:-\s+)?(?:(?:continue-on-error|allow_failure):\s*(?:['\"]?true\b|\$\{\{\s*true\s*\}\})|if:\s*(?:\$\{\{\s*)?false\s*(?:\}\})?\s*$)",
                                re.IGNORECASE)
# The events that run a GitHub workflow on each change, before or as it lands.
CHANGE_EVENTS = frozenset({"push", "pull_request", "pull_request_target", "merge_group"})
_ON = re.compile(r"""^(?:on|"on"|'on'|true)\s*:\s*(.*)$""")
_WORD = re.compile(r"[\w-]+")
# GitLab's documented switch for the template (a pipeline or job variable): the scan never runs.
_DETECTION_DISABLED = re.compile(r"^\s*SECRET_DETECTION_DISABLED\s*:\s*['\"]?(?:true|1|yes)\b",
                                 re.IGNORECASE | re.MULTILINE)
# A GitLab job's run condition, read from its direct keys only. ``rules:``, ``only:`` and ``except:``
# are not evaluated: a job with any of them is unevaluated (``gitlab_rules_unevaluated``), never
# met. A job-level ``when:`` is certain: ``always``/``on_success`` run, ``never``/``manual`` do not.
_JOB_CONDITION_KEYS = frozenset({"rules", "only", "except"})
_JOB_WHEN = re.compile(r"^when\s*:\s*['\"]?([\w-]+)")
_RUNS = frozenset({"always", "on_success"})
_OFF = frozenset({"never", "manual"})
# Top-level GitLab keys that are not jobs.
_GITLAB_GLOBAL_KEYS = frozenset({"include", "variables", "stages", "default", "workflow", "image",
                                 "services", "before_script", "after_script", "cache", "spec"})
_HIDDEN_JOB = re.compile(r"^\.[\w.-]+\s*:")
_AFTER_SCRIPT = re.compile(r"^\s*after_script\s*:")
_STEP_START = re.compile(r"^(\s*)-\s")
# CI that runs the repository's own pre-commit hooks over the checkout, in a command position:
# ``pre-commit run [<hook>] --all-files`` (or ``--from-ref``/``--files``), or ``uses: pre-commit/action``.
# A bare ``pre-commit run`` checks staged files only, which a CI checkout has none of.
_PRE_COMMIT_RUN = re.compile(r"(?:^\s*(?:-\s+)?(?:run:\s*)?|[;&|]\s*)pre-commit run\b([^\n]*)")
_PRE_COMMIT_ACTION = re.compile(r"^\s*-?\s*uses:\s*['\"]?pre-commit/action@")
_PRE_COMMIT_SCOPE = re.compile(r"(?:^|\s)(?:-a\b|--(?:all-files|from-ref|files)\b)")
# A list of names, not commands: jobs (``needs:``), stages, runner tags, branches and paths. A
# ``needs: [- gitleaks]`` names a job called gitleaks; a ``paths: [- trufflehog]`` a folder.
_NAME_LIST = re.compile(r"^\s*(?:needs|dependencies|extends|stages|tags|tags-ignore|paths|paths-ignore|"
                        r"branches|branches-ignore|only|except)\s*:\s*$")
# A tool's container image, tag allowed: ``docker run -v $PWD:/p zricethezav/gitleaks:latest detect``,
# ``ghcr.io/gitleaks/gitleaks:v8.18.0`` or a ``uses: docker://...`` step. The image has an owner.
_DOCKER_IMAGE = re.compile(
    r"(?:\bdocker run\b.*?\s|docker://)(?:[\w.-]+/)+("
    + "|".join(re.escape(t) for t in SECRET_TOOLS) + r")(?::[\w.@-]+)?(?=\s|$|['\"])", re.IGNORECASE)


class PreCommitGuard(TypedDict):
    present: bool
    file: str | None
    tools: list[str]


class CiGuard(TypedDict):
    present: bool
    files: list[str]
    tools: list[str]
    includes_unresolved: int  # GitLab includes that could hold a scan and were not read
    rules_unevaluated: list[str]  # GitLab scans under rules/only/except: not measured, never met


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
            m = _PRE_COMMIT_KEY.match(_TRAILING_COMMENT.sub(r"\1", line))
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
    lines = ["" if _COMMENT.match(line) else _TRAILING_COMMENT.sub(r"\1", line) for line in text.splitlines()]
    # A shell command continued with a trailing backslash is judged whole: join it onto its first
    # line and leave the continued lines empty, so masks and line numbers hold.
    for i in range(len(lines)):
        j = i
        while lines[i].rstrip().endswith("\\") and j + 1 < len(lines):
            j += 1
            lines[i] = lines[i].rstrip()[:-1].rstrip() + " " + lines[j].strip()
            lines[j] = ""
    return lines


def _list_block_end(lines: list[str], start: int, indent: int) -> int:
    """As ``_block_end``, but a list item at the key's own indent (``after_script:`` then ``- x`` with
    no indent, valid YAML) stays in the block."""
    for i in range(start + 1, len(lines)):
        line = lines[i]
        ind = len(line) - len(line.lstrip())
        if line.strip() and (ind < indent or (ind == indent and not _STEP_START.match(line))):
            return i
    return len(lines)


def _block_end(lines: list[str], start: int, indent: int) -> int:
    """The index after the YAML block that opens at ``start`` with ``indent`` (its list item or key)."""
    for i in range(start + 1, len(lines)):
        line = lines[i]
        if line.strip() and len(line) - len(line.lstrip()) <= indent:
            return i
    return len(lines)


def _masked_lines(lines: list[str], gitlab: bool = False) -> set[int]:
    """The lines whose scan guards nothing: inside a GitHub step or job that sets
    ``continue-on-error: true`` (its failure never stops the run), a GitLab hidden job, or a GitLab
    ``after_script:`` (its failure never sets the job status). The GitLab masks run only on GitLab
    files (``gitlab``); a GitLab job's own run condition is ``_job_shape``'s."""
    masked: set[int] = set()
    for i, line in enumerate(lines):
        if gitlab and _HIDDEN_JOB.match(line):  # a hidden job runs only through a visible job's ``extends:``
            masked.update(range(i, _block_end(lines, i, 0)))
            continue
        if gitlab and _AFTER_SCRIPT.match(line):  # GitLab ignores after_script failures for the job status
            masked.update(range(i, _list_block_end(lines, i, len(line) - len(line.lstrip()))))
            continue
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
        body = [ln for ln in lines[i + 1:end] if ln.strip()]
        if not body:
            return False
        level = min(len(ln) - len(ln.lstrip()) for ln in body)  # the event keys, not their inputs
        keys = [ln.strip().lstrip("- ").split(":")[0].strip() for ln in body
                if len(ln) - len(ln.lstrip()) == level]
        return bool(CHANGE_EVENTS & set(keys))
    return False


_VALUED_FLAGS = frozenset({"--from-ref", "--to-ref", "--source", "--origin", "-s", "-o", "--hook-stage",
                           "--color", "--config", "-c", "--remote-name", "--remote-url"})


def _step_value(lines: list[str], i: int, key: str) -> str | None:
    """The value of ``key`` (under ``with:``) in the step that opens at or above line ``i``."""
    indent = len(lines[i]) - len(lines[i].lstrip())
    for k in range(i + 1, len(lines)):
        ln = lines[k]
        if ln.strip() and (len(ln) - len(ln.lstrip()) < indent or
                           (len(ln) - len(ln.lstrip()) == indent and _STEP_START.match(ln))):
            break
        m = re.match(rf"\s*{re.escape(key)}\s*:\s*['\"]?(.*?)['\"]?\s*$", ln)
        if m:
            return m.group(1)
    return None


def _hook_tool(hook_id: str) -> str:
    """The tool a hook id runs: ``gitleaks-docker`` and ``gitleaks-system`` run gitleaks."""
    return next((t for t in SECRET_TOOLS if hook_id == t or hook_id.startswith(f"{t}-")), hook_id)


_DEFAULT_STAGES = frozenset({"pre-commit", "commit"})


def _stage_values(lines: list[str], at: int) -> set[str]:
    """The stage names a ``stages:`` or ``default_stages:`` key on line ``at`` lists, inline or as a
    block list below it."""
    values = lines[at].split(":", 1)[1].strip()
    if not values:  # a block list: the key, then ``- pre-commit`` lines below it
        key_indent = len(lines[at]) - len(lines[at].lstrip())
        values = " ".join(x.strip().lstrip("- ") for x in lines[at + 1:_list_block_end(lines, at, key_indent)])
    return set(re.findall(r"[\w-]+", values))


def _hooks_run_in_ci(root: Path, hook: PreCommitGuard) -> frozenset[str]:
    """The ids of the secrets hooks a plain ``pre-commit run`` selects: a hook whose ``stages:`` (or,
    with none, the top-level ``default_stages:``) leaves out the pre-commit stage (``[manual]``,
    ``[pre-push]``) is skipped, so CI does not run it. Ids, not tools: ``pre-commit run gitleaks``
    selects the hook whose id is ``gitleaks``, never a configured ``gitleaks-docker``."""
    if not hook["file"]:
        return frozenset()
    lines = _code_lines(_read(root / hook["file"]) or "")
    # A top-level ``default_stages:`` applies to every hook that sets no ``stages:`` of its own.
    top = next((k for k, ln in enumerate(lines) if ln.startswith("default_stages")
                and re.match(r"default_stages\s*:", ln)), None)
    default = _stage_values(lines, top) if top is not None else None
    selected: set[str] = set()
    for i, line in enumerate(lines):  # each list item; a hook item has its own ``id:`` key
        item = _STEP_START.match(line)
        if not item:
            continue
        dash = len(item.group(1))
        end = _block_end(lines, i, dash)
        keys = [(i, line[dash + 1:].strip())] + [(k, lines[k].strip()) for k in range(i + 1, end)
                                                  if len(lines[k]) - len(lines[k].lstrip()) == dash + 2]
        hook_id = next((m.group(1) for _, v in keys if (m := re.match(r"id\s*:\s*(\S+)", v))), None)
        stages_at = next((k for k, v in keys if re.match(r"stages\s*:", v)), None)
        if hook_id is None:
            continue
        stages = _stage_values(lines, stages_at) if stages_at is not None else default
        if (stages is None or _DEFAULT_STAGES & stages) and _hook_tool(hook_id.lower()) in hook["tools"]:
            selected.add(hook_id.lower())
    return frozenset(selected)


def _hook_id(tokens: list[str]) -> str | None:
    """The hook ``pre-commit run`` names, wherever it sits among the flags; None for every hook."""
    skip = False
    for token in tokens:
        if skip:
            skip = False
        elif token == "--files":
            return None  # the rest are file names
        elif token in _VALUED_FLAGS:
            skip = True
        elif not token.startswith("-"):
            return token.lower()
    return None


def _tool_that_can_fail(name: str, line: str, command: bool = True) -> str | None:
    """The tool a command on ``line`` runs, if the command can fail the job; else None."""
    if name == "detect-secrets-hook":
        return "detect-secrets"
    if name == "detect-secrets":
        return None  # ``detect-secrets scan`` and ``audit`` always exit 0
    if name == "trufflehog" and not _TRUFFLEHOG_FAIL.search(line):
        return None
    if name == "gitleaks" and (_GITLEAKS_NO_FAIL.search(line) or _STAGED_OR_INFO.search(line)
                               or (command and _BARE_CALL.search(line))):
        return None  # a CI checkout has nothing staged; version and help scan nothing
    return name


def _in_name_list(lines: list[str], i: int) -> bool:
    """Whether list item ``i`` sits under a key that lists names, not commands (``_NAME_LIST``)."""
    indent = len(lines[i]) - len(lines[i].lstrip())
    for j in range(i - 1, -1, -1):
        line = lines[j]
        if line.strip() and len(line) - len(line.lstrip()) < indent:
            return bool(_NAME_LIST.match(line))
        if line.strip() and len(line) - len(line.lstrip()) == indent and not _STEP_START.match(line):
            return bool(_NAME_LIST.match(line))
    return False


def _job_shape(lines: list[str], job: int) -> str:
    """``plain``, ``off`` or ``unevaluated`` for the GitLab job whose key is on line ``job``, from its
    direct keys only (see ``_JOB_CONDITION_KEYS``)."""
    end = _block_end(lines, job, 0)
    body = [ln for ln in lines[job + 1:end] if ln.strip()]
    if not body:
        return "plain"
    child = min(len(ln) - len(ln.lstrip()) for ln in body)
    keys = [ln.strip() for ln in body if len(ln) - len(ln.lstrip()) == child]
    if any(k.split(":", 1)[0].strip() in _JOB_CONDITION_KEYS for k in keys):
        return "unevaluated"
    when = next((m.group(1).lower() for k in keys if (m := _JOB_WHEN.match(k))), None)
    if when is None or when in _RUNS:
        return "plain"
    return "off" if when in _OFF else "unevaluated"


def _gitlab_jobs(lines: list[str]) -> list[tuple[str, int, int]]:
    """Each visible GitLab job as (name, start line, end line)."""
    jobs: list[tuple[str, int, int]] = []
    for i, line in enumerate(lines):
        if not line.strip() or line[0].isspace() or line.startswith((".", "-")) or ":" not in line:
            continue
        name = line.split(":", 1)[0].strip().strip("'\"")
        if name not in _GITLAB_GLOBAL_KEYS:
            jobs.append((name, i, _block_end(lines, i, 0)))
    return jobs


def _ci_tools(text: str, hooks: frozenset[str] = frozenset(), gitlab: bool = False) -> list[str]:
    """The secrets tools a CI file runs where a failure fails the run; see ``_ci_scan``."""
    return _ci_scan(text, hooks, gitlab)[0]


def _ci_scan(text: str, hooks: frozenset[str] = frozenset(),
             gitlab: bool = False) -> tuple[list[str], list[str]]:
    """The secrets tools a CI file uses or runs, unmasked, and those held back as unevaluated: in a
    GitLab job with ``rules:``/``only:``/``except:`` or an uncertain ``when:``. Comments never count,
    and neither does a masked step or job, or a GitLab job that is ``off``. GitLab's template is
    ``_gitlab_template_state``'s."""
    lines = _code_lines(text)
    masked = _masked_lines(lines, gitlab)
    held: set[int] = set()
    if gitlab:
        for _, start, end in _gitlab_jobs(lines):
            shape = _job_shape(lines, start)
            if shape == "off":
                masked.update(range(start, end))
            elif shape == "unevaluated":
                held.update(range(start, end))
    found: dict[int, set[str]] = {}
    for i, line in enumerate(lines):
        if i in masked or not line.strip() or _MASKED.search(line):
            continue
        tools = found.setdefault(i, set())
        if _PRE_COMMIT_ACTION.match(line):  # every hook over every file, or the hook ``extra_args`` names
            extra = _step_value(lines, i, "extra_args")
            named = _hook_id(extra.split()) if extra else None
            tools.update(_hook_tool(h) for h in (hooks if named is None else hooks & {named}))
            continue
        run_hooks = _PRE_COMMIT_RUN.match(line)
        if run_hooks:  # the pre-commit secrets hooks, run where they cannot be skipped
            if _PRE_COMMIT_SCOPE.search(run_hooks.group(1)):
                hook_id = _hook_id(run_hooks.group(1).split())
                tools.update(_hook_tool(h) for h in (hooks if hook_id is None else hooks & {hook_id}))
            continue
        uses = _USES.match(line)
        if uses:
            slug = uses.group(1).lower()
            tools.update(t for t in SECRET_TOOLS if slug in ACTION_SLUGS and t in slug)
            continue
        if _STEP_START.match(line) and _in_name_list(lines, i):
            continue
        commands = {m.group(1).lower() for m in _COMMAND.finditer(line)}
        images = {m.group(1).lower() for m in _DOCKER_IMAGE.finditer(line)} - commands
        tools.update(t for t in (_tool_that_can_fail(n, line) for n in commands) if t)
        tools.update(t for t in (_tool_that_can_fail(n, line, command=False) for n in images) if t)
    counted = {t for i, ts in found.items() if i not in held for t in ts}
    unevaluated = {t for i, ts in found.items() if i in held for t in ts} - counted
    return sorted(counted), sorted(unevaluated)


def _gitlab_template_state(texts: list[str]) -> str:
    """``absent``, ``runs``, ``off`` or ``unevaluated`` for GitLab's Secret-Detection template (or
    component) over the merged pipeline: the root ``.gitlab-ci.yml`` and every file it includes are
    one pipeline. ``SECRET_DETECTION_DISABLED`` switches it off; so does a ``secret_detection`` job
    that is ``off`` (job-level ``when: never``/``manual``). Any ``secret_detection`` definition with
    ``rules:``/``only:``/``except:`` or an uncertain ``when:``, or definitions that disagree, leave it
    unevaluated."""
    lines = [ln for text in texts for ln in _code_lines(text)]
    code = "\n".join(lines)
    if not _GITLAB_TEMPLATE.search(code):
        return "absent"
    if _DETECTION_DISABLED.search(code):
        return "off"
    shapes = [_job_shape(lines, start) for name, start, _ in _gitlab_jobs(lines) if name == "secret_detection"]
    explicit_runs = any(_job_shape(lines, start) == "plain" and any(
        _JOB_WHEN.match(ln.strip()) for ln in lines[start + 1:end])
        for name, start, end in _gitlab_jobs(lines) if name == "secret_detection")
    if "unevaluated" in shapes or ("off" in shapes and explicit_runs):
        return "unevaluated"
    return "off" if "off" in shapes else "runs"


def ci_guard(root: Path, ghsync_root: Path | None = None, hooks: frozenset[str] = frozenset()) -> CiGuard:
    """The CI files with a secrets scan step, and the tools they use. ``hooks`` are the pre-commit
    secrets hooks, which a CI ``pre-commit run`` runs."""
    files: list[str] = []
    tools: set[str] = set()
    held: set[str] = set()
    for path in _ci_files(root):
        text = _read(path)
        found, unevaluated = _ci_scan(text, hooks, gitlab=path.name == ".gitlab-ci.yml") if text is not None else ([], [])
        held.update(unevaluated)
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
            found, unevaluated = _ci_scan(included.text, hooks, gitlab=True)
            held.update(unevaluated)
            if found:
                files.append(f".gitlab-ci.yml include (line {included.line})")
                tools.update(found)
        unresolved = sum(r["status"] not in SETTLED and r["kind"] in _HIDING_KINDS
                         for r in resolution.includes)
        template = _gitlab_template_state([_read(root / ".gitlab-ci.yml") or "", *(f.text for f in resolution.files)])
        if template == "runs":
            tools.add("secret_detection")
            if ".gitlab-ci.yml" not in files:
                files.append(".gitlab-ci.yml")
        elif template == "unevaluated":
            held.add("secret_detection")
    return {"present": bool(files), "files": files, "tools": sorted(tools), "includes_unresolved": unresolved,
            "rules_unevaluated": sorted(held - tools)}


REASON_NOT_READ = "secret_scanning_not_read"
REASON_INCLUDES = "ci_includes_unresolved"
REASON_RULES = "gitlab_rules_unevaluated"
# A github.com remote, HTTPS or SSH: the same prefixes as ``lib.hosting.gh_cli._REMOTE_RE`` (any
# SSH user, an ``ssh://`` port), pinned by a parity test. Read here with plain git rather than
# imported, so this offline scan never imports a platform client and is not flagged as network.
_GITHUB_REMOTE = re.compile(r"^(?:https?://(?:[^@/]+@)?github\.com/|(?:[^@/]+@)?github\.com:"
                            r"|ssh://(?:[^@/]+@)?github\.com(?::\d+)?/)")


def _on_github(root: Path) -> bool:
    """Whether ``origin`` (else the sole remote) is on github.com; false when git cannot tell."""
    def remote(*args: str) -> str:
        try:
            proc = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True,
                                  encoding="utf-8", errors="replace", timeout=10,
                                  env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"})  # read only
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
    if ci["rules_unevaluated"]:
        return {"available": False,
                "reason": f"{REASON_RULES}: a GitLab scan ({', '.join(ci['rules_unevaluated'])}) runs under "
                          "rules, only/except or a when that this reader does not evaluate"}
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
    hook = pre_commit_guard(root)
    ci = ci_guard(root, ghsync_root, _hooks_run_in_ci(root, hook))
    sources = [SourceGrade(source=name, grade=_GRADES[name])
               for name, found in (("pre_commit", hook["present"]), ("ci", ci["present"])) if found]
    return {"available": True, "pre_commit": hook, "ci": ci, "configured": bool(sources),
            "enforcement": _enforcement(root, ci), "sources": sources}
