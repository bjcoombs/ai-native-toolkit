"""Where a CI command runs: its working directory, relative to the repository root (#1162).

``run_cwd(span, words, lines, gitlab)`` reads the directory the command ``words`` on the CI
file's ``lines[span]`` runs in, from GitHub workflows and ``.gitlab-ci.yml`` (moved from
``lib.testing.coverage_root_config``, #1032, unchanged; ``lib.testing.coverage_ci`` reads it
for a Gradle run, #1162):

- the repository root, unless the step's ``working-directory:`` or a ``cd`` / ``pushd``
  earlier in the same step (GitLab: the same job, whose script lines share one shell), or on
  the command's own line before it, moves it. A ``cd`` in a ``(...)`` subshell that closes
  before the command, a GitLab ``cd`` in an inherited ``before_script`` (top level, or under
  ``default:``), a GitLab job with ``extends:`` or a ``<<:`` merge key, a ``before_script``
  key after the command (GitLab runs it first) and a ``defaults:`` ``working-directory``
  anywhere in the file leave it unresolved (None). A GitLab ``after_script`` runs in its own
  shell, so its ``cd`` is not read.
- ``move(cwd, target)``: literal relative paths are resolved (``cd pkg``, ``cd ../..``);
  anything else (``$VAR``, ``~``, an absolute path, ``cd -``, ``popd``, a path above the
  root) is unresolved.
- ``uv --directory X`` and ``poetry -C X`` / ``--directory X`` run the tool in X.
- ``exact_cwd`` (the Gradle reading) reads only what runs before the command (a ``cd`` on a
  later folded or continued line of its span is not applied) and is also unresolved where
  ``run_cwd`` may misplace the run: a line holding a ``cd`` that names the program twice, a
  ``cd`` after a quote (``bash -c "cd x && ..."``), or a ``cd`` as the alternative of
  ``||`` (``cd app || cd ../lib``).

``PurePosixPath()`` is the repository root. Inward-only imports: stdlib and
``lib.instructions.command_index``; never an orchestrator. Tests: ``tests/test_coverage_root_config.py``,
``tests/test_gradle_workdir.py``.
"""
from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import PurePosixPath

from lib.instructions.command_index import ci_line_text

# Runner flags that run the command in another directory.
_DIRECTORY_FLAGS = {"uv": ("--directory",), "poetry": ("-C", "--directory")}
# ``cd`` at the start of a command: the line's start, or after ``;``, ``&&``, ``||``, ``|``, ``(``.
_CD_RE = re.compile(r"(?:^|[;&|(])\s*(cd|pushd|popd)(?=\s|$|[;&|)])\s*([^\s;&|)]*)")
_WORKDIR_RE = re.compile(r"^(\s*)(?:-\s+)?working-directory\s*:\s*(.*?)\s*(?:#.*)?$")
_STEP_RE = re.compile(r"^(\s*)-\s+")
# A path step: "" is the repository root; None is unresolved.
Cwd = PurePosixPath | None


def _step_lines(lines: Sequence[str], row: int, gitlab: bool) -> range:
    """The lines of the step (GitLab: the job) holding ``lines[row]``."""
    if gitlab:
        start = next((r for r in range(row, -1, -1)
                      if lines[r][:1] not in ("", " ", "\t", "#")), 0)
        end = next((r for r in range(row + 1, len(lines))
                    if lines[r][:1] not in ("", " ", "\t", "#")), len(lines))
        return range(start, end)
    indent = len(lines[row]) - len(lines[row].lstrip())
    found = next(((r, m) for r in range(row, -1, -1) if (m := _STEP_RE.match(lines[r]))
                  and (r == row or len(m.group(1)) < indent)), None)
    if found is None:
        return range(row, row + 1)
    start, dash = found[0], len(found[1].group(1))
    end = next((r for r in range(start + 1, len(lines))
                if lines[r].strip() and len(lines[r]) - len(lines[r].lstrip()) <= dash),
               len(lines))
    return range(start, end)


def move(cwd: Cwd, target: str | None) -> Cwd:
    """``cwd`` after ``cd target``: literal relative paths only, never above the root."""
    if cwd is None or not target or target == "-" or target[0] in "/~$\"'`" or "$" in target:
        return None
    parts: list[str] = list(cwd.parts)
    for part in PurePosixPath(target).parts:
        if part == "..":
            if not parts:
                return None
            parts.pop()
        elif part != ".":
            parts.append(part)
    return PurePosixPath(*parts) if parts else PurePosixPath()


def _defaults_workdir(lines: Sequence[str]) -> bool:
    """A ``working-directory`` under a ``defaults:`` block (job or workflow level)."""
    for row, line in enumerate(lines):
        match = _WORKDIR_RE.match(line)
        if match is None or line.lstrip().startswith("-"):
            continue
        indent = len(match.group(1))
        parents = [lines[r].strip() for r in range(row - 1, -1, -1)
                   if lines[r].strip() and not lines[r].strip().startswith("#")
                   and len(lines[r]) - len(lines[r].lstrip()) < indent]
        if parents[:2] == ["run:", "defaults:"]:
            return True
    return False


def _inherited_cd(lines: Sequence[str]) -> bool:
    """A GitLab ``before_script`` at the top level or under ``default:`` holding a ``cd``:
    it runs in the same shell as every job's ``script``."""
    parent = ""
    for row, line in enumerate(lines):
        indent = len(line) - len(line.lstrip())
        if line.strip() and indent == 0 and not line.startswith("#"):
            parent = line.split(":", 1)[0].strip()
        key = line.strip().split(":", 1)[0]
        if key != "before_script" or not (indent == 0 or parent == "default"):
            continue
        texts = [ci_line_text(line.split(":", 1)[1])]
        for text in lines[row + 1:]:
            outdent = len(text) - len(text.lstrip())
            # A YAML list may sit at its key's own indent (``before_script:`` then ``- cd``).
            if text.strip() and (outdent < indent or outdent == indent
                                 and not text.lstrip().startswith("-")):
                break
            texts.append(ci_line_text(text))
        if any(_CD_RE.search(t.strip("[]")) for t in texts):
            return True
    return False


def run_cwd(span: range, words: Sequence[str], lines: Sequence[str], gitlab: bool) -> Cwd:
    """The directory the command ``words`` on ``lines[span]`` runs in, relative to the
    root, or None when unresolved (module docstring)."""
    row = span[0]
    step = _step_lines(lines, row, gitlab)
    cwd: Cwd = PurePosixPath()
    # GitLab: a ``before_script`` inherited from the top level, ``default:``, an
    # ``extends:`` template or a ``<<:`` merge key may move the job; not followed.
    if gitlab and (_inherited_cd(lines) or any(
            lines[r].strip().startswith(("extends:", "<<:")) for r in step)):
        return None
    # GitLab runs a job's ``before_script`` first, wherever the key sits in the job.
    if gitlab and any(lines[r].strip().startswith("before_script:")
                      for r in step if r > span[-1]):
        return None
    if not gitlab:
        if _defaults_workdir(lines):
            return None
        dash = _STEP_RE.match(lines[step.start])   # step keys sit where the first key does
        key_indent = len(dash.group(0)) if dash else -1
        for r in step:
            match = _WORKDIR_RE.match(lines[r])
            if match and (r == step.start or len(match.group(1)) == key_indent):
                cwd = move(cwd, match.group(2).strip("\"'") or None)
    after = _after_script_rows(lines, step) if gitlab else set()
    for r in range(step.start, span[-1] + 1):
        if r in after:
            continue   # GitLab runs ``after_script`` in its own shell, after ``script``
        text = ci_line_text(lines[r])
        if r in span:
            word = re.search(r"(?<![\w./-])" + re.escape(words[0]) + r"(?![\w./-])", text)
            at = word.start() if word else -1
            text = text[:at] if at >= 0 else text
        for match in _CD_RE.finditer(text):
            if match.group(1) == "popd" or _in_closed_group(text, match.start(1)):
                return None   # a subshell ``(cd docs && make)`` does not move the run
            cwd = move(cwd, match.group(2))
    return _runner_directory(cwd, words)


# A ``cd`` / ``pushd`` right after a quote or backtick (``bash -c "cd x && ..."``), which
# ``_CD_RE`` does not read.
_QUOTED_CD_RE = re.compile(r"[\"'`]\s*(?:cd|pushd)(?=\s)")
# A ``cd`` / ``pushd`` as the alternative of ``||`` (``cd app || cd ../lib``): which ran is
# not settled. ``cd app || exit 1`` holds none.
_ALT_CD_RE = re.compile(r"\|\|\s*[({]?\s*(?:cd|pushd)(?=\s|$)")


def exact_cwd(span: range, words: Sequence[str], lines: Sequence[str], gitlab: bool) -> Cwd:
    """``run_cwd`` for a caller that relies on the exact directory (a Gradle run, #1162).
    Only what runs before the command moves it: a span line after the one naming
    ``words[0]`` (a folded ``run: >`` or ``\\`` continuation) is not read. None (unresolved)
    also when the run's own lines hold a ``cd`` and name ``words[0]`` twice (``cd a &&
    ./gradlew x && cd ../b && ./gradlew y``: ``run_cwd`` reads both runs at the first), a
    ``cd`` after a quote (``bash -c "cd android && ./gradlew x"``), a ``cd`` as the
    alternative of ``||`` before the command (``cd app || cd ../lib; ./gradlew x``), or no
    span line names ``words[0]``."""
    if not words:
        return None
    named = re.compile(r"(?<![\w./-])" + re.escape(words[0]) + r"(?![\w./-])")
    text = " ".join(ci_line_text(lines[r]) for r in span)
    if (len(named.findall(text)) > 1 and _CD_RE.search(text)) or _QUOTED_CD_RE.search(text):
        return None
    row = next((r for r in span if named.search(ci_line_text(lines[r]))), None)
    if row is None:
        return None
    head = ci_line_text(lines[row])
    found = named.search(head)
    before = [ci_line_text(lines[r]) for r in range(_step_lines(lines, span[0], gitlab).start, row)]
    if found is None or _ALT_CD_RE.search(" ".join([*before, head[:found.start()]])):
        return None
    kept = ["" if row < r <= span[-1] else line for r, line in enumerate(lines)]
    return run_cwd(span, words, kept, gitlab)


def _runner_directory(cwd: Cwd, words: Sequence[str]) -> Cwd:
    """``cwd`` after a ``uv --directory X`` / ``poetry -C X`` lead, which runs the tool in X."""
    if not words or words[0] not in _DIRECTORY_FLAGS:
        return cwd
    flags = _DIRECTORY_FLAGS[words[0]]
    for i, word in enumerate(words[1:], 1):
        if word in flags:
            return move(cwd, words[i + 1] if i + 1 < len(words) else None)
        if any(word.startswith(f + "=") for f in flags if f.startswith("--")):
            return move(cwd, word.split("=", 1)[1])
    return cwd


def _after_script_rows(lines: Sequence[str], step: range) -> set[int]:
    """The rows of a GitLab job's ``after_script:`` block (key line included)."""
    rows: set[int] = set()
    key: int | None = None
    for r in step:
        line = lines[r]
        indent = len(line) - len(line.lstrip())
        if key is not None and line.strip() and (
                indent < key or indent == key and not line.lstrip().startswith("-")):
            key = None
        if key is None and line.strip().startswith("after_script:"):
            key = indent
        if key is not None:
            rows.add(r)
    return rows


def _in_closed_group(text: str, at: int) -> bool:
    """``text[at]`` sits inside a ``(...)`` group that closes within ``text``: the run,
    which is not before it in that text, is outside the group."""
    depth = text[:at].count("(") - text[:at].count(")")
    if depth <= 0:
        return False
    for char in text[at:]:
        depth += {"(": 1, ")": -1}.get(char, 0)
        if depth == 0:
            return True
    return False
