"""Layer 3 lint gates a single CI line cannot show (issue #926), for ``lib.ci.ci_checks.lint_runs``.

- ``rewrite_gated``: a formatter that rewrites files (``clang-format -i``, ``mix format``,
  ``dart format .``; ``check_command.is_format_rewrite``) is a lint run only when a later
  unmasked line of the same job runs ``git diff --exit-code`` or ``git diff --quiet``, which
  fails the job on any change. On its own, or followed by a commit, it reads false as before,
  and so does a diff whose failure an ``||`` handler absorbs (``git diff --quiet || git
  commit -am fmt``); ``|| exit 1`` re-raises it and still counts.
  Jobs are the keys under a GitHub workflow's ``jobs:`` and a GitLab file's top-level keys
  (``job_ids``).
- ``line_compiler_gate`` / ``line_dotnet_build``: one CI line that runs a compiler with
  warnings as errors (``lint_vocabulary.compiler_gate``), or a .NET build.
- ``dotnet_props``: whether a committed ``Directory.Build.props`` or ``*.csproj`` sets
  ``TreatWarningsAsErrors`` true, and whether one turns on the build's analysers
  (``EnforceCodeStyleInBuild`` true, or an ``AnalysisLevel`` other than ``none``). Read by
  path; no text leaves this module, only the two booleans.
- ``lint_gate``: the null reason those give ``lint_runs`` when no lint or may-lint line is
  found: ``analysers_in_build`` (a CI .NET build plus the analyser properties; the severities
  live in ``.editorconfig``, which the scan does not evaluate) ahead of
  ``compiler_gate_only`` (warnings as errors is the only static gate; maintainer decisions 1
  and 3 on #926).

Layer 3 lint gates one CI line cannot show (issue #926), for `ci_checks.lint_runs`.
`rewrite_gated(text)` is the set of line indices of rewrite-formatter lines
(`check_command.is_format_rewrite`) that a later, or the same, unmasked line of the same
job turns into a lint run with `git diff --exit-code` or `git diff --quiet`; a rewrite
alone, followed by a commit, or with the diff in another job is not one, and nor is a diff
whose failure an `||` handler absorbs without re-raising it (`git diff --quiet || git
commit -am fmt`; `|| exit 1` and `|| false` re-raise). A diff under a GitLab `after_script:`
key is no gate either (`_after_script`): GitLab ignores its exit codes. Nor is a diff piped
into another command (`git diff --exit-code | tee d.txt`): without `pipefail`, which
GitHub's default `bash -e` does not set, the step takes the last command's status. Nor is a diff with a
`git add`, `git commit`, `git stash`, `git restore`, `git checkout -- .` or `git reset
--hard` between it and the rewrite (`_stages`), since the working tree then shows no
change, nor one that compares the index or commits (`_working_tree`: `--cached`,
`--staged`, a revision such as `HEAD~1` before `--`). An `||` handler re-raises only when
`exit 1` or `false` runs as a command, never inside echoed text. A property tag with a `Condition` attribute counts (it may apply, so
it reads null, never false). `job_ids(lines)`
numbers the jobs: each top-level key (a GitLab job) and each key at the first indent
under a top-level `jobs:` (GitHub). `line_commands(line)` is the per-line command reader
`ci_checks` and `ci_lines` share; it reads each segment past its compound-command lead
(`lib.ci.ci_compound`, #1061). `line_compiler_gate` and `line_dotnet_build` are per-line tests over
`lint_vocabulary.compiler_gate` and `dotnet_build`. `dotnet_props(root, tracked)` reads
the committed `Directory.Build.props` files, then the `*.csproj` files, by path through
`command_index.read_config`, and returns two booleans: `TreatWarningsAsErrors` true, and
the analysers on (`EnforceCodeStyleInBuild` true or an `AnalysisLevel` other than `none`);
no text leaves the module. `lint_gate(root, tracked, compiler=, dotnet=)` is
`analysers_in_build` for a CI .NET build with the analysers on, else
`compiler_gate_only` for a compiler-gate line or a CI .NET build with
`TreatWarningsAsErrors` committed, else None (decisions 1, 3 and 4 on #926). Tests:
`tests/test_ci_lint_gates.py`.
"""
from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import Path, PurePosixPath
from typing import Literal

from lib.ci.check_command import is_format_rewrite
from lib.ci.ci_compound import compound_core
from lib.ci.ci_masking import unmasked_lines
from lib.command_index import ci_line_text, read_config
from lib.command_resolver import Command, command_words, extract_commands
from lib.ci.lint_vocabulary import compiler_gate, dotnet_build

GateBasis = Literal["analysers_in_build", "compiler_gate_only"]


def line_commands(line: str) -> list[Command]:
    """The commands of one CI line, read on its own (a trailing ``\\`` dropped), each from the
    command a compound-command lead runs (``if ! pytest``, ``{ pytest``, ``(pytest)``; #1061)."""
    text = compound_core(ci_line_text(line).removesuffix("\\").strip())
    return extract_commands(f"```bash\n{text}\n```\n") if text else []


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _content(line: str) -> str:
    stripped = line.strip()
    return "" if stripped.startswith("#") else stripped


def job_ids(lines: Sequence[str]) -> list[int]:
    """Per line, the number of the job it belongs to: a top-level key starts one (a GitLab
    job), and so does each key at the first indent under a top-level ``jobs:`` (GitHub)."""
    ids: list[int] = []
    job, in_jobs, job_indent = 0, False, -1
    for line in lines:
        text = _content(line)
        if text and not text.startswith("-"):
            depth = _indent(line)
            if depth == 0:
                job += 1
                in_jobs, job_indent = text.split(":", 1)[0].strip("\"'") == "jobs", -1
            elif in_jobs and job_indent < 0:
                job, job_indent = job + 1, depth
            elif in_jobs and depth == job_indent:
                job += 1
        ids.append(job)
    return ids


def _rewrites(line: str) -> bool:
    return any(is_format_rewrite(c.text) for c in line_commands(line))


# A ``||`` after a ``git diff`` on its line: what follows handles the diff's failure.
_OR_AFTER_DIFF = re.compile(r"\bgit\s+diff\b[^|]*\|\|(.*)$")
# A git diff piped into another command (one ``|``, not ``||``): without ``pipefail``, which
# GitHub's default ``bash -e`` does not set, the step takes the last command's status.
_PIPED_DIFF = re.compile(r"\bgit\s+diff\b[^|;&]*\|(?!\|)")
# A handler that still fails the step: ``|| exit 1``, ``|| (echo x; exit 1)``, ``|| false``.
# ``false`` counts only as a command, never inside echoed text (``|| echo "false alarm"``).
# Both count only as a command, never inside echoed text (``|| echo "exit 1 is wise"``).
_RERAISE = re.compile(r"(?:^|[;&|({])\s*(?:exit\s+(?:[1-9]|\$\?)|false\b)")
# A diff that compares the index or commits, not the working tree a rewrite changed:
# ``git diff --cached``, ``--staged``, ``git diff --exit-code HEAD~1 HEAD``.
_INDEX_FLAGS = frozenset({"--cached", "--staged"})
_REVISION = re.compile(r"^(?:HEAD|FETCH_HEAD|ORIG_HEAD|origin/\S+|[0-9a-f]{7,40})(?:[~^]\d*)*$|\.\.")


def _working_tree(words: list[str]) -> bool:
    """A ``git diff`` that compares the working tree: no ``--cached`` / ``--staged`` and no
    revision argument before ``--`` (paths after it are fine)."""
    args = words[2:words.index("--")] if "--" in words else words[2:]
    return not (_INDEX_FLAGS & set(args)
                or any(_REVISION.search(a) for a in args if not a.startswith("-")))


def _diff_gate(line: str) -> bool:
    """``git diff --exit-code`` or ``git diff --quiet``: fails the job on any change. A
    failure handled by ``||`` counts only when the handler re-raises it (``|| exit 1``):
    ``git diff --quiet || git commit -am fmt`` commits the fix and never fails."""
    if not any((words := command_words(c.text))[:2] == ["git", "diff"]
               and {"--exit-code", "--quiet"} & set(words) and _working_tree(words)
               for c in line_commands(line)):
        return False
    text = ci_line_text(line)
    if _PIPED_DIFF.search(text):   # git diff --exit-code | tee d.txt: the pipe hides its status
        return False
    handler = _OR_AFTER_DIFF.search(text)
    return handler is None or _RERAISE.search(handler.group(1)) is not None


_AFTER_SCRIPT = re.compile(r"^(\s*)after_script:")


def _after_script(lines: Sequence[str]) -> frozenset[int]:
    """Indices of the lines under a GitLab ``after_script:`` key, whose exit codes GitLab
    ignores: a ``git diff --exit-code`` there cannot fail the job."""
    out: set[int] = set()
    depth = -1
    for i, line in enumerate(lines):
        text = _content(line)
        if not text:
            continue
        indent = _indent(line)
        if depth >= 0 and (indent > depth or (indent == depth and text.startswith("-"))):
            out.add(i)
            continue
        match = _AFTER_SCRIPT.match(line)
        depth = len(match.group(1)) if match else -1
    return frozenset(out)


def _stages(line: str) -> bool:
    """A git command after which ``git diff`` sees no working-tree change: one that stages
    or commits the rewrite (``git add``, ``git commit``) or throws it away (``git stash``,
    ``git restore``, ``git checkout -- .``, ``git reset --hard``)."""
    for c in line_commands(line):
        words = command_words(c.text)
        sub = words[1] if words[:1] == ["git"] and len(words) > 1 else ""
        if sub in ("add", "commit", "stash", "restore"):
            return True
        if sub == "checkout" and ("--" in words or "." in words):
            return True
        if sub == "reset" and "--hard" in words:
            return True
    return False


def rewrite_gated(text: str) -> frozenset[int]:
    """Indices of the rewrite-formatter lines a later unmasked ``git diff`` gate of the same
    job turns into a lint run (the same line counts: ``clang-format -i a && git diff --quiet``)."""
    lines = text.splitlines()
    rewrites = [i for i, line in enumerate(lines) if _rewrites(line)]
    if not rewrites:
        return frozenset()
    jobs = job_ids(lines)
    ignored = _after_script(lines)
    diffs = [j for j, line in enumerate(unmasked_lines(text))
             if j not in ignored and _diff_gate(line)]
    staged = [k for k, line in enumerate(lines) if _stages(line)]
    return frozenset(i for i in rewrites if any(
        j >= i and jobs[j] == jobs[i]
        and not any(i <= k <= j and jobs[k] == jobs[i] for k in staged) for j in diffs))


# A YAML list item with no key (``- msbuild``), as ``ci_lines._BARE_ITEM`` reads it.
_BARE_ITEM = re.compile(r"^\s*-\s+(?![\w.-]+:(?:\s|$))")


def _line_words(line: str, script_item: bool) -> list[list[str]]:
    """The words of each command on one CI line, without a lone one-word bare item whose
    parent is not a script key (``- msbuild`` under ``matrix:`` is a value, not a run)."""
    lone_value = not script_item and _BARE_ITEM.match(line) is not None
    words = [command_words(c.text) for c in line_commands(line)]
    return [w for w in words if not (lone_value and len(w) == 1)]


def line_compiler_gate(line: str, *, script_item: bool = True) -> bool:
    """One CI line that runs a compiler with warnings as errors (``mix compile
    --warnings-as-errors``, ``cmake -DCMAKE_CXX_FLAGS=-Werror ..``, ``dotnet build -warnaserror``)."""
    return any(compiler_gate(w) for w in _line_words(line, script_item))


def line_dotnet_build(line: str, *, script_item: bool = True) -> bool:
    """One CI line that runs a .NET build (``dotnet build``, ``msbuild``)."""
    return any(dotnet_build(w) for w in _line_words(line, script_item))


# A property tag may carry a Condition attribute (``<TreatWarningsAsErrors
# Condition="'$(CI)'=='true'">``): it may apply, so it reads null, never false.
_WARNASERROR = re.compile(r"<TreatWarningsAsErrors\b[^>]*>\s*true\s*<", re.IGNORECASE)
_ANALYSERS = re.compile(
    r"<EnforceCodeStyleInBuild\b[^>]*>\s*true\s*<|<AnalysisLevel\b[^>]*>\s*(?!none\s*<)[^<\s]",
    re.IGNORECASE)


def _project_files(tracked: frozenset[str]) -> list[str]:
    """Committed ``Directory.Build.props`` files first, then ``*.csproj``, each sorted."""
    props = sorted(f for f in tracked if PurePosixPath(f).name == "Directory.Build.props")
    return props + sorted(f for f in tracked if f.endswith(".csproj"))


def dotnet_props(root: Path, tracked: frozenset[str]) -> tuple[bool, bool]:
    """``(warnings_as_errors, analysers)`` from the committed .NET project files."""
    warn = False
    for rel in _project_files(tracked):
        text = read_config(root, rel)
        if _ANALYSERS.search(text):
            return warn or _WARNASERROR.search(text) is not None, True
        warn = warn or _WARNASERROR.search(text) is not None
    return warn, False


def lint_gate(root: Path, tracked: frozenset[str], *, compiler: bool,
              dotnet: bool) -> GateBasis | None:
    """The null reason for ``lint_runs`` when no lint or may-lint line is found: a CI .NET
    build with the analysers on, then a compiler gate (a line, or a .NET build with
    ``TreatWarningsAsErrors`` committed); None when neither applies."""
    if dotnet:
        warn, analysers = dotnet_props(root, tracked)
        if analysers:
            return "analysers_in_build"
        compiler = compiler or warn
    return "compiler_gate_only" if compiler else None
