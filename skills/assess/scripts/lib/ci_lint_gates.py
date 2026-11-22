"""Layer 3 lint gates a single CI line cannot show (issue #926), for ``lib.ci_checks.lint_runs``.

- ``rewrite_gated``: a formatter that rewrites files (``clang-format -i``, ``mix format``,
  ``dart format .``; ``check_command.is_format_rewrite``) is a lint run only when a later
  unmasked line of the same job runs ``git diff --exit-code`` or ``git diff --quiet``, which
  fails the job on any change. On its own, or followed by a commit, it reads false as before.
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
"""
from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import Path, PurePosixPath
from typing import Literal

from lib.check_command import is_format_rewrite
from lib.ci_masking import unmasked_lines
from lib.command_index import ci_line_text, read_config
from lib.command_resolver import Command, command_words, extract_commands
from lib.lint_vocabulary import compiler_gate, dotnet_build

GateBasis = Literal["analysers_in_build", "compiler_gate_only"]


def line_commands(line: str) -> list[Command]:
    """The commands of one CI line, read on its own (a trailing ``\\`` dropped)."""
    text = ci_line_text(line).removesuffix("\\").strip()
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


def _diff_gate(line: str) -> bool:
    """``git diff --exit-code`` or ``git diff --quiet``: fails the job on any change."""
    for c in line_commands(line):
        words = command_words(c.text)
        if words[:2] == ["git", "diff"] and {"--exit-code", "--quiet"} & set(words):
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
    diffs = [j for j, line in enumerate(unmasked_lines(text)) if _diff_gate(line)]
    return frozenset(i for i in rewrites if any(j >= i and jobs[j] == jobs[i] for j in diffs))


def line_compiler_gate(line: str, *, script_item: bool = True) -> bool:
    """One CI line that runs a compiler with warnings as errors (``mix compile
    --warnings-as-errors``, ``cmake -DCMAKE_CXX_FLAGS=-Werror ..``, ``dotnet build -warnaserror``)."""
    return any(compiler_gate(command_words(c.text)) for c in line_commands(line))


def line_dotnet_build(line: str, *, script_item: bool = True) -> bool:
    """One CI line that runs a .NET build (``dotnet build``, ``msbuild``)."""
    return any(dotnet_build(command_words(c.text)) for c in line_commands(line))


_WARNASERROR = re.compile(r"<TreatWarningsAsErrors>\s*true\s*<", re.IGNORECASE)
_ANALYSERS = re.compile(
    r"<EnforceCodeStyleInBuild>\s*true\s*<|<AnalysisLevel>\s*(?!none\s*<)[^<\s]", re.IGNORECASE)


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
