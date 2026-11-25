"""Committed guards against secrets: a pre-commit hook and a CI scan step (#1173).

A token committed once lives in the history of every clone. A guard catches it before it lands:
a secrets hook in the pre-commit config, which a contributor can skip with ``--no-verify``, or a
secrets scan in CI, which runs on every change whoever sends it. This module reads both from the
commit; GitHub's own secret scanning and push protection are repository settings, read on the
platform pass by ``lib.hosting.github_secret_scanning`` and passed in as ``github_secret_scanning``.
No model is involved and no scanner runs.

The tendency it compensates for is guardrail erosion: a token pasted into a config or a skill
"just for now" is never removed, and nothing at the door stops the next one.

**What counts** (the tool names in ``SECRET_TOOLS``). Only files git tracks are read
(``lib.command_index.repo_files``, #1204), from the work tree, as ``ci_checks`` and
``lib.ci.ci_includes`` read them: an untracked file is never read, and an uncommitted edit to a
tracked file is read as it stands.

- ``pre_commit``: a root ``.pre-commit-config.yaml`` (or ``.yml``) whose ``repo:`` or ``id:``
  names a secrets tool (gitleaks, detect-secrets, trufflehog, ggshield, talisman, secretlint,
  git-secrets, ripsecrets). A hook that runs only by hand (``stages: [manual]``) guards no commit
  and does not count (``lib.secrets_guard_hooks``, #1204).
- ``ci``: a GitHub Actions workflow directly under ``.github/workflows/`` (the files GitHub runs)
  or the root ``.gitlab-ci.yml`` with a step that uses a secrets action
  (``ACTION_SLUGS``: ``gitleaks/gitleaks-action``, ``trufflesecurity/trufflehog``,
  ``GitGuardian/ggshield-action``, ``GitGuardian/ggshield/actions/secret``, ...) or runs a secrets
  tool as a command (its name, then a subcommand, a flag or the end of the line; ``git secrets``
  reads as ``git-secrets``), or that includes GitLab's ``Secret-Detection.gitlab-ci.yml`` template
  (or its component). A GitHub workflow is read as ``ci_checks`` reads it
  (``lib.ci.ci_expressions.follow_workflow``): a local composite action's steps, matrix commands and
  a ``$NAME`` command a literal ``env:`` sets are read through. A visible ``secret_detection``
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
  ``--exit-code 0`` does not count. For every tool, a command that scans nothing in a CI checkout
  does not count: help and version, a staged-only or pre-commit scan (``--staged``, ``gitleaks
  protect``, ``--pre-commit``, ``ggshield secret scan pre-commit``) and git-secrets setup
  (``--install``, ``--register-aws``). A ``||`` whose right side is not a non-zero exit or
  ``false`` masks the command, and so does a pipe after it; a command sent to the background
  (``gitleaks detect &``) never fails the job and does not count (``&&``, ``&>`` and ``2>&1``
  are no background); a scan last in a pipe sets its status
  and counts. The shell's quoting is read (``lib.secrets_guard_lines``, #1204): a whole
  YAML-quoted value is unwrapped, an operator inside quotes (``--log-opts="a|b"``) is an
  argument, and so is a tool name inside quotes (``echo 'a; gitleaks detect'``). A command
  continued with a trailing backslash is judged whole.
- CI that runs the pre-commit hooks counts for the secrets hooks it selects: ``uses:
  pre-commit/action``, or ``pre-commit run`` with ``--all-files``/``-a``, ``--from-ref`` or
  ``--files`` (a bare ``pre-commit run`` checks staged files only). A named hook id counts for its
  tool (``gitleaks-docker`` runs gitleaks), and a hook whose ``stages:`` leaves out the stage the
  run uses (``pre-commit``, or the one ``--hook-stage`` names) is not selected.
- GitLab CI is read as GitLab merges it (``lib.secrets_guard_gitlab`` over
  ``lib.ci.ci_includes.merged_config``, #1204): a job defined in the root file and an include, or
  through ``extends:``, is read once with its effective keys, so a root ``when: never`` or
  ``script:`` over an included scan job removes it and a visible job running a hidden template's
  scan counts. A merged job the resolver does not settle (``unresolved``, or ``open`` while an
  include that could hold a job was not read) is held as unevaluated, as is a job from an include
  under ``rules:``, and any job while such an include is unread. GitLab ``rules:`` are not evaluated. A job
  with no ``rules:``, ``only:`` or ``except:``, and a job-level ``when:`` of ``always``,
  ``on_success`` or none, and no YAML merge key ``<<:`` (which can bring rules in), runs: its scan counts. A job-level ``when: never`` or ``manual`` is off:
  its scan does not count, and level 2 can read unmet. Any other job (``rules:``,
  ``only:``/``except:``, ``when: delayed``) is unevaluated: its scan is held in
  ``ci.rules_unevaluated`` and level 2 reads not measured (``gitlab_rules_unevaluated``), never
  met. Under-counting a guarded repository is acceptable; reading met for one that is not is not.
  The template follows the same rule over the merged ``secret_detection`` job, and
  ``SECRET_DETECTION_DISABLED: "true"`` in the pipeline's ``variables:`` or the job's own switches
  it off. An ``after_script:`` scan and a hidden
  job do not count, since neither fails the pipeline. Two assumptions are stated rather than
  evaluated: a pipeline-level GitLab ``workflow: rules:`` is assumed to let the pipeline
  run, and a GitHub step or job ``if:`` expression is assumed to hold (only a literal ``if:
  false`` masks it). GitLab job ``rules:`` are held back because they commonly gate a scan job;
  ``workflow:`` and ``if:`` gate whole pipelines or are rarely set on a scan step.

**The two levels** (maintainer decision on #1173):

- ``configured`` (level 1): a guard is configured from any source. A pre-commit hook is enough,
  so the condition is graded ``config_only``: listed, not counted. A GitLab scan held as
  unevaluated (``rules_unevaluated``) is configured too (#1204). It reads the files this run
  read: an unread GitLab include leaves level 2 not measured (``ci_includes_unresolved``) but
  level 1 unmet. The asymmetry is deliberate: level 1 is listed and moves no count, so an unmet
  there costs nothing, while level 2 is counted and must not read unmet on evidence it lacks.
- ``enforcement`` (level 2): a guard that can stop the change, where an agent cannot skip it. A
  CI scan fails a check that can gate the merge; GitHub push protection (source ``platform``)
  rejects the push. Secret scanning alone raises an alert that gates nothing, so it meets level
  1, not level 2. Graded ``measured`` and
  counted. With no CI scan on a github.com remote and the settings not read (no ``--platform``,
  no admin, owner or security manager rights), push protection could still guard the repository, so the reading is not
  measured (``secret_scanning_not_read``), never unmet; read and off, it is unmet. On a public
  repository GitHub also turns on push protection for each user by default; that does not show in
  the repository's settings and a pusher can opt out of it, so it does not meet level 2.
  Repository push protection does meet it, though by default a pusher can bypass a block by
  stating a reason (``delegated_bypass`` false). That bypass is recorded and attributed, like an
  admin overriding a failing CI check, so level 2 still counts it; likewise
  (``ci_includes_unresolved``) when a GitLab include that could hold a scan was not read, since
  ``.gitlab-ci.yml``'s includes are read through ``lib.ci.ci_includes``;
  a GitHub Enterprise Server or ``*.ghe.com`` remote is not recognised as GitHub yet, so it reads
  unmet with no CI scan although those platforms offer secret scanning; on any other remote, or
  none, there is no such setting, and no CI scan reads unmet.
- ``sources`` records each source that shows a guard with its own grade (``pre_commit``
  config_only, ``ci`` measured, ``platform`` measured: secret scanning or push protection on),
  since a condition's grade is fixed per signal.

Block (``secrets_guard`` in run-context.json)::

    {"available": True,
     "pre_commit": {"present": bool, "file": str | None, "tools": [str, ...]},
     "ci": {"present": bool, "files": [str, ...], "tools": [str, ...], "includes_unresolved": int,
            "rules_unevaluated": [str, ...]},
     "configured": bool,
     "enforcement": {"available": True, "present": bool, "source": "ci" | None}
                    | {"available": False, "reason": "<code>: ..."},  # secret_scanning_not_read,
                    # ci_includes_unresolved or gitlab_rules_unevaluated
     "sources": [{"source": "pre_commit" | "ci", "grade": str}, ...]}

Counts-only output drops the file paths, or hashes them under ``--hash-paths``, and keeps the
booleans and tool tokens.

Secrets guard (#1173), no model. `scan_secrets_guard(repo_root, scope, ghsync_root)` reads the root pre-commit
config for a `SECRET_TOOLS` hook and the GitHub workflows and `.gitlab-ci.yml` for a secrets action,
command, the GitLab `Secret-Detection` template or a `secret_detection` job (a masked `|| true` line
does not count). Tests: `tests/test_secrets_guard.py`, `tests/test_secrets_guard_resolved.py`.
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from collections.abc import Mapping
from typing import TypedDict

from lib.ci.ci_action_inputs import ReadLocal
from lib.ci.ci_expressions import follow_workflow
from lib.ci.ci_includes import SETTLED, merged_config, resolve_includes
from lib.command_index import ci_config_files, repo_files
from lib.secrets_guard_gitlab import GITLAB_CI, scan_texts, template_state
from lib.secrets_guard_hooks import (
    PRE_COMMIT_FILES as PRE_COMMIT_FILES, HookStages, PreCommitGuard as PreCommitGuard, hook_id, hook_stage,
    hook_tool, pre_commit_guard as pre_commit_guard, secret_hooks, selected)
from lib.secrets_guard_lines import (
    SECRET_TOOLS as SECRET_TOOLS, STEP_START as _STEP_START, blank_quoted, block_end as _block_end,
    code_lines as _code_lines, in_spans, list_block_end as _list_block_end, quoted_spans, read_text, shell_text)

# The include kinds that can add a scan this run cannot see. A GitLab template is named by its path,
# which the template reader reads, so an unread one hides nothing; a component can be anyone's.
_HIDING_KINDS = frozenset({"local", "project", "remote", "component", "unknown"})
# A command whose failure is swallowed: an ``||`` whose right side is not a non-zero exit or ``false``
# (``|| true``, ``|| :``, ``|| echo leaks found``), or a pipe after it (``gitleaks detect | tee
# out.txt``: without ``pipefail``, the last command's status wins). Both are read with the shell's
# quoted spans blanked (``--log-opts="a|b"`` is no pipe). A scan last in a pipe (``git diff |
# gitleaks stdin``) sets the pipe's status, so it counts. A pipe under ``set -o pipefail`` still
# masks a scan before it: a follow-up.
_OR_MASK = re.compile(r"\|\|(?!\s*(?:exit\s+[1-9]|false\b))")
_SHELL_OPERATOR = re.compile(r"&&|\|\||;|\||&")
_PIPE = re.compile(r"(?<!\|)\|(?!\|)")
# A lone ``&`` ends a list (``a && b &``) and sends all of it to the background, where its failure
# never reaches the job; a ``;`` ends a list in the foreground. ``&&``, ``&>`` and ``2>&1`` are no ``&``.
_LIST_END = re.compile(r";")
_BACKGROUND = re.compile(r"(?<![&<>])&(?![&>])")


def _backgrounded(bare: str, start: int) -> bool:
    """Whether the list holding the command at ``bare[start:]`` (quoted spans blanked) runs in the
    background: a lone ``&`` before the next ``;`` or the end of the line."""
    end = m.start() if (m := _LIST_END.search(bare, start)) else len(bare)
    return _BACKGROUND.search(bare, start, end) is not None
# The secrets actions a ``uses:`` step names (owner/name, any ref).
# GitGuardian's maintained action is a path in its repository (``GitGuardian/ggshield/actions/secret``).
ACTION_SLUGS = ("gitleaks/gitleaks-action", "trufflesecurity/trufflehog", "gitguardian/ggshield-action",
                "secretlint/secretlint-action", "zricethezav/gitleaks-action", "gitguardian/ggshield/actions/secret")
_USES = re.compile(r"^\s*-?\s*uses:\s*['\"]?([\w.-]+/[\w.-]+(?:/[\w.-]+)*)", re.IGNORECASE)
# A tool run as a command: its name as a word, then a subcommand or flag or the end of the line,
# at the start of a shell line (indented, as inside a ``run: |`` or ``script:`` block), after ``run:``/``script:``/``- ``, after a pipe or ``&&``, or after
# ``docker run <image>`` (so a path such as ``.gitleaks.toml`` or a ``gitleaks:allow`` note is not).
# The command names: the tools, and ``detect-secrets-hook``, the form of detect-secrets that fails.
_COMMAND_NAMES = sorted((*SECRET_TOOLS, "detect-secrets-hook"), key=len, reverse=True)
# A command that cannot fail the job guards nothing: ``detect-secrets scan`` writes a baseline and
# exits 0, ``trufflehog`` exits 0 on findings without ``--fail``, ``gitleaks --exit-code 0``.
_TRUFFLEHOG_FAIL = re.compile(r"(?<![\w-])--fail(?![\w-])")  # not --fail-on-scan-errors
# Commands that scan nothing in a CI checkout, for every tool: help and version; a staged-only or
# pre-commit scan (a CI checkout has nothing staged: ``--staged``, ``gitleaks protect``,
# ``--pre-commit``, ``ggshield secret scan pre-commit``); setup and auth (git-secrets
# ``--install``, ``--register-aws``, ``--add``, ``--list``; ``ggshield auth``/``install``/``config``;
# ``--init``; talisman ``--githook``).
_NO_SCAN = re.compile(
    r"(?<!\S)(?:--help|-h|--version|--staged|--pre-commit|--install|--register-aws|--add|--add-provider|--list)(?!\S)"
    r"|\bgitleaks\s+(?:version|help|protect)\b|\bggshield\s+secret\s+scan\s+pre-commit\b"
    r"|\bggshield\s+(?:auth|install|config)\b|(?<!\S)(?:--init|--githook)(?!\S)")
# A bare ``gitleaks`` shell call prints usage. Only for a command: a ``uses: docker://...gitleaks``
# step ends its line too, with its arguments under ``with:`` below.
_BARE_CALL = re.compile(r"\bgitleaks\s*['\"]?\s*$")
_GITLEAKS_NO_FAIL = re.compile(r"--exit-code(?:=|\s+)0\b")
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
# A GitLab job's run condition, read from its direct keys only. ``rules:``, ``only:``, ``except:``,
# and ``extends:`` or a YAML merge key ``<<:`` (which can bring rules in from elsewhere), are not
# evaluated: a job with any of them is unevaluated (``gitlab_rules_unevaluated``), never
# met. A job-level ``when:`` is certain: ``always``/``on_success`` run, ``never``/``manual`` do not.
_JOB_CONDITION_KEYS = frozenset({"rules", "only", "except", "extends", "<<"})
_JOB_WHEN = re.compile(r"^when\s*:\s*['\"]?([\w-]+)")
_RUNS = frozenset({"always", "on_success"})
_OFF = frozenset({"never", "manual"})
# Top-level GitLab keys that are not jobs.
_GITLAB_GLOBAL_KEYS = frozenset({"include", "variables", "stages", "default", "workflow", "image",
                                 "services", "before_script", "after_script", "cache", "spec"})
_DATA_BLOCK = re.compile(r"^\s*-?\s*(?:env|variables)\s*:\s*$")
_SKIP_VAR = re.compile(r"^\s*-?\s*['\"]?SKIP['\"]?\s*:\s*['\"]?([^'\"#]*)")
_HIDDEN_JOB = re.compile(r"^\.[\w.-]+\s*:")
_AFTER_SCRIPT = re.compile(r"^\s*after_script\s*:")
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
    source: str | None  # "ci", or "platform" (GitHub push protection)


class SourceGrade(TypedDict):
    source: str  # pre_commit | ci | platform
    grade: str  # the source's own evidence grade (lib.signal_grades): config_only | measured


class SecretsGuardBlock(TypedDict):
    """``run-context.json`` ``secrets_guard`` (see the module doc)."""

    available: bool
    pre_commit: PreCommitGuard
    ci: CiGuard
    configured: bool  # level 1: a guard is configured, from any source
    enforcement: Enforcement  # level 2: a guard runs where an agent cannot skip it
    sources: list[SourceGrade]  # each source that shows a guard, with its own grade


def _masked_lines(lines: list[str], gitlab: bool = False) -> set[int]:
    """The lines whose scan guards nothing: inside a GitHub step or job that sets
    ``continue-on-error: true`` (its failure never stops the run), a GitLab hidden job, or a GitLab
    ``after_script:`` (its failure never sets the job status). The GitLab masks run only on GitLab
    files (``gitlab``); a GitLab job's own run condition is ``_job_shape``'s."""
    masked: set[int] = set()
    for i, line in enumerate(lines):
        if _DATA_BLOCK.match(line):  # ``env:``/``variables:`` values are data, never run as a command
            # A step that opens with ``- env:`` keeps its other keys (``run:``) at the key's column.
            step = _STEP_START.match(line)
            col = len(line) - len(line[len(step.group(1)) + 1:].lstrip()) if step else len(line) - len(line.lstrip())
            masked.update(range(i, _block_end(lines, i, col)))
            continue
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


def _tool_that_can_fail(name: str, line: str, command: bool = True) -> str | None:
    """The tool a command on ``line`` runs, if the command can fail the job; else None."""
    if _NO_SCAN.search(line):
        return None  # help, version, setup, or a staged-only scan: nothing in a CI checkout
    if name == "detect-secrets-hook":
        return "detect-secrets"
    if name == "detect-secrets":
        return None  # ``detect-secrets scan`` and ``audit`` always exit 0
    if name == "trufflehog" and not _TRUFFLEHOG_FAIL.search(line):
        return None
    if name == "gitleaks" and (_GITLEAKS_NO_FAIL.search(line) or (command and _BARE_CALL.search(line))):
        return None
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
    names = [k.split(":", 1)[0].strip().strip("'\"") for k in keys]  # as _gitlab_jobs reads job names
    if any(n in _JOB_CONDITION_KEYS for n in names):
        return "unevaluated"
    unquoted = [f"{n}:{k.split(':', 1)[1]}" if ":" in k else k for n, k in zip(names, keys)]
    when = next((m.group(1).lower() for k in unquoted if (m := _JOB_WHEN.match(k))), None)
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


def _image_tools(lines: list[str], i: int, commands: set[str]) -> set[str]:
    """The secrets tools a container image on line ``i`` runs, judged on its arguments: after the
    image on the line, or a ``uses: docker://`` step's ``with: args:`` on later lines. A gitleaks
    container with none only prints its usage."""
    line, tools = lines[i], set()
    for m in _DOCKER_IMAGE.finditer(line):
        name = m.group(1).lower()
        if name in commands:
            continue
        args = (_step_value(lines, i, "args") or "") if "docker://" in line else line[m.end():]
        if name == "gitleaks" and not args.strip():
            continue
        tool = _tool_that_can_fail(name, f"{line} {args}", command=False)
        if tool:
            tools.add(tool)
    return tools


def _ci_tools(text: str, hooks: HookStages | None = None, gitlab: bool = False) -> list[str]:
    """The secrets tools a CI file runs where a failure fails the run; see ``_ci_scan``."""
    return _ci_scan(text, hooks or {}, gitlab)[0]


def _hook_tools(hooks: HookStages, args: str) -> set[str]:
    """The tools of the secrets hooks a ``pre-commit run`` with ``args`` selects: its stage, then
    the one hook it names, if any."""
    tokens = args.split()
    ids, named = selected(hooks, hook_stage(tokens)), hook_id(tokens)
    return {hook_tool(h) for h in (ids if named is None else ids & {named})}


def _ci_scan(text: str, hooks: HookStages, gitlab: bool = False,
             held_lines: frozenset[int] = frozenset()) -> tuple[list[str], list[str]]:
    """The secrets tools a CI file uses or runs, unmasked, and those held back as unevaluated: in a
    GitLab job with ``rules:``/``only:``/``except:`` or an uncertain ``when:``, or on a line in
    ``held_lines`` (a GitLab job the merge did not settle). Comments never count, and neither does
    a masked step or job, or a GitLab job that is ``off``. GitLab's template is
    ``lib.secrets_guard_gitlab``'s."""
    lines = _code_lines(text)
    masked = _masked_lines(lines, gitlab)
    held: set[int] = set(held_lines)
    if gitlab:
        for _, start, end in _gitlab_jobs(lines):
            shape = _job_shape(lines, start)
            if shape == "off":
                masked.update(range(start, end))
            elif shape == "unevaluated":
                held.update(range(start, end))
    found: dict[int, set[str]] = {}
    # pre-commit skips a hook whose id is in the SKIP variable; read file-wide, so a skip set on
    # another job under-counts rather than credits a hook that does not run.
    skipped = {h.strip().lower() for ln in lines if (m := _SKIP_VAR.match(ln)) for h in m.group(1).split(",")}
    hooks = {h: stages for h, stages in hooks.items() if h not in skipped}
    for i, raw in enumerate(lines):
        if i in masked or not raw.strip():
            continue
        line = shell_text(raw)
        bare = blank_quoted(line)
        if _OR_MASK.search(bare):
            continue
        pipe = max((m.start() for m in _PIPE.finditer(bare)), default=-1)
        tools = found.setdefault(i, set())
        if _PRE_COMMIT_ACTION.match(line):  # every hook over every file, or the hook ``extra_args`` names
            if pipe < 0:
                tools.update(_hook_tools(hooks, _step_value(lines, i, "extra_args") or ""))
            continue
        run_hooks = _PRE_COMMIT_RUN.match(line)
        if run_hooks:  # the pre-commit secrets hooks, run where they cannot be skipped
            args = _SHELL_OPERATOR.split(run_hooks.group(1), maxsplit=1)[0]  # this command's own arguments
            if pipe < 0 and not _backgrounded(bare, run_hooks.start(1)) and _PRE_COMMIT_SCOPE.search(args):
                tools.update(_hook_tools(hooks, args))
            continue
        uses = _USES.match(line)
        if uses:
            slug = uses.group(1).lower()
            known = slug in ACTION_SLUGS or "/".join(slug.split("/")[:2]) in ACTION_SLUGS  # or a path in one
            tools.update(t for t in SECRET_TOOLS if pipe < 0 and known and t in slug)
            continue
        if _STEP_START.match(raw) and _in_name_list(lines, i):
            continue
        spans = quoted_spans(line)
        # A command inside quotes is an argument (``echo 'a; gitleaks detect'``); one before a pipe
        # is masked by it.
        commands = {m.group(1).lower() for m in _COMMAND.finditer(line)
                    if not in_spans(m.start(1), spans) and m.start(1) > pipe
                    and not _backgrounded(bare, m.start(1))}
        tools.update(t for t in (_tool_that_can_fail(n, line) for n in commands) if t)
        if pipe < 0:
            tools.update(_image_tools(lines, i, commands))
    counted = {t for i, ts in found.items() if i not in held for t in ts}
    unevaluated = {t for i, ts in found.items() if i in held for t in ts} - counted
    return sorted(counted), sorted(unevaluated)


def _reader(root: Path, tracked: frozenset[str]) -> ReadLocal:
    """Read a tracked file inside ``root`` (a local composite action), never one a symlink takes out."""
    def read(rel: str) -> str | None:
        if rel not in tracked or not (root / rel).resolve().is_relative_to(root):
            return None
        return read_text(root / rel)
    return read


def ci_guard(root: Path, ghsync_root: Path | None = None, hooks: HookStages | None = None,
             tracked: frozenset[str] | None = None) -> CiGuard:
    """The CI files with a secrets scan step, and the tools they use. ``hooks`` are the pre-commit
    secrets hooks, which a CI ``pre-commit run`` runs; ``tracked`` the files at the assessed commit."""
    hooks = hooks or {}
    tracked = repo_files(root) if tracked is None else tracked
    files: list[str] = []
    tools: set[str] = set()
    held: set[str] = set()
    for rel in ci_config_files(tracked):
        text = read_text(root / rel) if rel != GITLAB_CI else None
        if text is None:
            continue
        # Composite actions, matrix commands and expression commands read as ``ci_checks`` reads them.
        text = follow_workflow(text, _reader(root, tracked)).text
        found, _ = _ci_scan(text, hooks)
        if found and _runs_on_change(_code_lines(text)):
            files.append(rel)
            tools.update(found)
    unresolved = 0
    config = merged_config(root, ghsync_root) if GITLAB_CI in tracked else None
    if config is not None:  # .gitlab-ci.yml and its includes, as GitLab merges them (lib.ci.ci_includes)
        resolution = resolve_includes(root, ghsync_root)  # a project include resolves from a ghsync clone
        # GitLab's own secret-detection component is the scan, read by its path; it hides nothing.
        unresolved = sum(r["status"] not in SETTLED and r["kind"] in _HIDING_KINDS
                         and "/secret-detection" not in (r["id"] or "") for r in resolution.includes)
        for label, text, hold in scan_texts(config, resolution, hides=unresolved > 0):
            found, unevaluated = _ci_scan(text, hooks, gitlab=True, held_lines=hold)
            held.update(unevaluated)
            if found:
                files.append(label)
                tools.update(found)
        template = template_state(config, resolution)
        if template == "runs" and not unresolved:
            tools.add("secret_detection")
            if GITLAB_CI not in files:
                files.append(GITLAB_CI)
        elif template == "unevaluated":
            held.add("secret_detection")
        # A template that runs beside an unread include is neither: the include could override its
        # job, so it is left to ``includes_unresolved`` (``ci_includes_unresolved``).
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


_GRADES = {"pre_commit": "config_only", "ci": "measured", "platform": "measured"}


def _platform(block: Mapping[str, object] | None) -> tuple[bool, bool] | None:
    """(secret scanning on, push protection on) from the ``github_secret_scanning`` platform block
    (``lib.hosting.github_secret_scanning``); None when it was not read (offline, no admin, owner or security manager rights,
    not GitHub)."""
    if not isinstance(block, Mapping) or block.get("available") is not True:
        return None
    return bool(block.get("secret_scanning")), bool(block.get("push_protection"))


def _enforcement(root: Path, ci: CiGuard, platform: tuple[bool, bool] | None) -> Enforcement:
    """Level 2: a guard that can stop the change. A CI scan fails a check that can gate the merge;
    push protection rejects the push; secret scanning alone raises an alert that gates nothing, so it
    does not meet level 2. With neither, a setting that was not read leaves it not measured."""
    if ci["present"]:
        return {"available": True, "present": True, "source": "ci"}
    if platform is not None and platform[1]:
        return {"available": True, "present": True, "source": "platform"}
    if ci["includes_unresolved"]:
        return {"available": False,
                "reason": f"{REASON_INCLUDES}: no secrets scan settled in the CI read, and "
                          f"{ci['includes_unresolved']} GitLab include(s) that could hold or override one were not read"}
    if ci["rules_unevaluated"]:
        return {"available": False,
                "reason": f"{REASON_RULES}: a GitLab scan ({', '.join(ci['rules_unevaluated'])}) runs under "
                          "rules, only/except or a when that this reader does not evaluate"}
    if _on_github(root) and platform is None:
        return {"available": False,
                "reason": f"{REASON_NOT_READ}: no secrets scan in CI, and GitHub's own secret scanning "
                          "and push protection, repository settings, were not read"}
    return {"available": True, "present": False, "source": None}


def scan_secrets_guard(repo_root: Path, scope: Path | None = None, ghsync_root: Path | None = None,
                       github_secret_scanning: Mapping[str, object] | None = None) -> SecretsGuardBlock:
    """Build the ``secrets_guard`` block (see the module doc). The pre-commit config and CI are the
    repository's, so a ``scope`` reads the root's, as ``guardrails`` reads the root pre-commit config."""
    root = repo_root.resolve()
    tracked = repo_files(root)
    hook = pre_commit_guard(root, tracked)
    ci = ci_guard(root, ghsync_root, secret_hooks(root, hook["file"]), tracked)
    platform = _platform(github_secret_scanning)
    sources = [SourceGrade(source=name, grade=_GRADES[name])
               for name, found in (("pre_commit", hook["present"]), ("ci", ci["present"]),
                                   ("platform", platform is not None and any(platform))) if found]
    # A GitLab scan held as unevaluated is still a configured guard: level 1 lists it (#1204).
    return {"available": True, "pre_commit": hook, "ci": ci, "configured": bool(sources or ci["rules_unevaluated"]),
            "enforcement": _enforcement(root, ci, platform), "sources": sources}
