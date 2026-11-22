"""Lint, analyser and formatter-check commands beyond Python and JavaScript (issue #926).

``lib.check_command`` reads these words for ``is_lint_command`` and ``is_lint_run``; this
module holds the vocabulary so that file stays under the size ratchet. All of it reads the
words of one command (``lib.command_resolver.command_words``) and nothing else: pure, no
file or network access.

- ``ANALYSER_PROGRAMS``: linters and static analysers that check in any mode (``phpstan``,
  ``psalm``, ``phpcs``, ``detekt``, ``swiftlint``, ``clang-tidy``, ``cppcheck``), matched by
  ``program_name``: the basename with a ``.phar`` or numeric version suffix dropped
  (``vendor/bin/phpstan``, ``clang-tidy-18``).
- ``formatter_mode``: a formatter counts as lint only in its check mode (maintainer decision
  2 on #926: ``pint --test``, ``php-cs-fixer fix --dry-run``, ``swift-format lint``,
  ``dotnet format --verify-no-changes``, ``clang-format --dry-run --Werror``,
  ``mix format --check-formatted``, ``dart format --set-exit-if-changed``). Its rewrite mode
  (``clang-format -i``) is read by ``lib.ci_lint_gates``: a gate only when a later line of
  the same job runs ``git diff --exit-code``.
- ``subcommand_lint``: build-tool lint subcommands and tasks: ``mix credo``, ``mix dialyzer``
  (not ``--plt``, which only builds the lookup table), ``dart analyze``, ``flutter analyze``,
  and Gradle tasks named ``lint*``, ``ktlint*Check``, ``detekt*``, ``spotless*Check`` and
  ``checkstyle*``, apart from the ones that rewrite or record (``lintFix``, ``ktlintFormat``,
  ``spotlessApply``, ``detektBaseline``) and a task named after ``-x``.
- ``compiler_gate``: a compiler run with warnings as errors (``mix compile
  --warnings-as-errors``, ``-Werror`` on a compile or CMake line, ``dotnet build
  -warnaserror``). Not a lint run (maintainer decision 1); ``lib.ci_checks`` reads it as the
  null reason ``compiler_gate_only`` when it is the only static gate.
- ``dotnet_build``: a ``dotnet build`` / ``dotnet msbuild`` / ``msbuild`` line, which runs the
  Roslyn analysers a committed project file configures (``analysers_in_build``).
"""
from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import Literal

ANALYSER_PROGRAMS = frozenset({
    "phpstan", "psalm", "phpcs", "detekt", "swiftlint", "clang-tidy", "cppcheck",
})
FORMATTER_PROGRAMS = frozenset({"clang-format", "swift-format", "pint", "php-cs-fixer",
                                "dotnet-format"})
_SUFFIX = re.compile(r"(?:\.phar|\.exe|-\d+(?:\.\d+)*)$")

FormatMode = Literal["check", "rewrite"]


def program_name(word: str) -> str:
    """A program's name as the vocabulary knows it: ``vendor/bin/phpstan`` -> ``phpstan``,
    ``clang-tidy-18`` -> ``clang-tidy``, ``phpstan.phar`` -> ``phpstan``."""
    return _SUFFIX.sub("", PurePosixPath(word).name)


def _pos(args: list[str]) -> list[str]:
    return [w for w in args if not w.startswith("-")]


def _check_or_rewrite(check: bool) -> FormatMode:
    return "check" if check else "rewrite"


def formatter_mode(words: list[str]) -> FormatMode | None:
    """``check`` for a formatter's check mode, ``rewrite`` when it rewrites files in place,
    None for any other command (or a formatter that only prints, ``clang-format a.cc``)."""
    if not words:
        return None
    head, args = program_name(words[0]), words[1:]
    flags, sub = set(args), _pos(args)[:1]
    if head == "clang-format":
        if flags & {"--dry-run", "-n"} and flags & {"--Werror", "-Werror"}:   # LLVM takes both
            return "check"
        return "rewrite" if "-i" in flags else None
    if head == "swift-format":
        if sub == ["lint"]:
            return "check"
        return "rewrite" if flags & {"-i", "--in-place"} else None
    if head == "pint":
        return _check_or_rewrite("--test" in flags)
    if head == "php-cs-fixer":
        if sub == ["check"]:
            return "check"
        return _check_or_rewrite("--dry-run" in flags) if sub == ["fix"] else None
    if head == "dotnet-format":
        return _check_or_rewrite(bool(flags & {"--verify-no-changes", "--check"}))
    if head == "dotnet" and sub == ["format"]:
        return _check_or_rewrite("--verify-no-changes" in flags)
    if head == "mix" and sub == ["format"]:
        return _check_or_rewrite("--check-formatted" in flags)
    if head in ("dart", "flutter") and sub == ["format"]:
        if "--set-exit-if-changed" in flags:
            return "check"
        prints = "--output=none" in flags or any(
            w in ("-o", "--output") and args[i + 1:i + 2] == ["none"] for i, w in enumerate(args))
        return None if prints else "rewrite"
    return None


# Gradle flags that take the next word as a value (``-x lint`` excludes the task).
_GRADLE_VALUED = frozenset({
    "-x", "--exclude-task", "-p", "--project-dir", "-b", "--build-file", "-c", "--settings-file",
    "-I", "--init-script", "-g", "--gradle-user-home", "--include-build", "-D", "-P",
})
_GRADLE_LINT = re.compile(
    r"^(?!\w*(?:Fix|Format|Baseline|Apply|GenerateConfig))"
    r"(?:lint\w*|ktlint\w*Check|detekt\w*|spotless\w*Check|checkstyle\w*)$")
_GRADLE = frozenset({"gradle", "gradlew"})


def gradle_tasks(args: list[str]) -> list[str]:
    """The tasks a Gradle command runs, each without its ``:project:`` prefix."""
    tasks: list[str] = []
    skip = False
    for word in args:
        if skip:
            skip = False
        elif word.startswith("-"):
            skip = word in _GRADLE_VALUED
        else:
            tasks.append(word.rsplit(":", 1)[-1])
    return tasks


def subcommand_lint(words: list[str]) -> bool:
    """A build tool's lint subcommand or task (``mix credo``, ``dart analyze``,
    ``./gradlew :app:lintRelease``)."""
    head, sub = program_name(words[0]), _pos(words[1:])[:1]
    if head == "mix":
        return sub == ["credo"] or (sub == ["dialyzer"] and "--plt" not in words)
    if head in ("dart", "flutter"):
        return sub == ["analyze"]
    if head in _GRADLE:
        return any(_GRADLE_LINT.match(t) for t in gradle_tasks(words[1:]))
    return False


def ecosystem_lint(words: list[str]) -> bool:
    """A lint run this module's vocabulary reads: an analyser, a formatter in check mode or
    a build-tool lint subcommand; ``php <program>`` reads as the program it runs."""
    if not words:
        return False
    if program_name(words[0]) == "php" and len(words) > 1 and not words[1].startswith("-"):
        return ecosystem_lint(words[1:])
    return (program_name(words[0]) in ANALYSER_PROGRAMS or formatter_mode(words) == "check"
            or subcommand_lint(words))


_COMPILERS = frozenset({
    "cmake", "gcc", "g++", "clang", "clang++", "cc", "c++", "make", "gmake", "ninja", "meson",
})
# ``-Werror`` alone or after ``=`` (``-DCMAKE_CXX_FLAGS=-Werror``), never ``--Werror``.
_WERROR = re.compile(r"(?:^|[=\s\"'])-Werror\b")
_MSBUILD_WARNASERROR = re.compile(
    r"^(?:[-/]{1,2}warnaserror\+?|[-/]{1,2}p(?:roperty)?:\w*treatwarningsaserrors=true)$",
    re.IGNORECASE)


def dotnet_build(words: list[str]) -> bool:
    """A .NET build line: ``dotnet build``, ``dotnet msbuild``, ``msbuild``."""
    if not words:
        return False
    head = program_name(words[0])
    return head == "msbuild" or (head == "dotnet" and _pos(words[1:])[:1] in (["build"], ["msbuild"]))


def compiler_gate(words: list[str]) -> bool:
    """A compiler run with warnings as errors: not a lint run (decision 1 on #926)."""
    if not words:
        return False
    head = program_name(words[0])
    if head == "mix":
        return _pos(words[1:])[:1] == ["compile"] and "--warnings-as-errors" in words
    if dotnet_build(words):
        return any(_MSBUILD_WARNASERROR.match(w) for w in words[1:])
    if head in _COMPILERS:
        return any(_WERROR.search(w) or w.upper().startswith("-DCMAKE_COMPILE_WARNING_AS_ERROR=ON")
                   for w in words[1:])
    return False
