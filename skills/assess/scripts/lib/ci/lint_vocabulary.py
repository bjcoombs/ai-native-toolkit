"""Lint, analyser and formatter-check commands beyond Python and JavaScript (issue #926).

``lib.ci.check_command`` reads these words for ``is_lint_command`` and ``is_lint_run``; this
module holds the vocabulary so that file stays under the size ratchet. All of it reads the
words of one command (``lib.instructions.command_resolver.command_words``) and nothing else: pure, no
file or network access. Gradle's arguments are read by ``lib.runner_args``.

- ``ANALYSER_PROGRAMS``: linters and static analysers that check in any mode (``phpstan``,
  ``psalm``, ``phpcs``, ``detekt``, ``swiftlint``, ``clang-tidy``, ``cppcheck``), matched by
  ``program_name``: the basename with a ``.phar`` or numeric version suffix dropped
  (``vendor/bin/phpstan``, ``clang-tidy-18``).
- ``formatter_mode``: a formatter counts as lint only in its check mode (maintainer decision
  2 on #926: ``pint --test``, ``php-cs-fixer fix --dry-run``, ``swift-format lint``,
  ``dotnet format --verify-no-changes``, ``clang-format --dry-run --Werror``,
  ``mix format --check-formatted``, ``dart format --set-exit-if-changed``). Its rewrite mode
  (``clang-format -i``) is read by ``lib.ci.ci_lint_gates``: a gate only when a later line of
  the same job runs ``git diff --exit-code``.
- ``subcommand_lint``: build-tool lint subcommands and tasks: ``mix credo``, ``mix dialyzer``
  (not ``--plt``, which only builds the lookup table), ``dart analyze``, ``flutter analyze``,
  and Gradle tasks named ``lint*``, ``ktlint*Check``, ``detekt*``, ``spotless*Check`` and
  ``checkstyle*``, apart from the ones that rewrite or record (``lintFix``, ``ktlintFormat``,
  ``spotlessApply``, ``detektBaseline``) and a task named after ``-x``.
- ``compiler_gate``: a compiler run with warnings as errors (``mix compile
  --warnings-as-errors``, ``-Werror`` on a compile or CMake line, ``dotnet build
  -warnaserror``). Not a lint run (maintainer decision 1); ``lib.ci.ci_checks`` reads it as the
  null reason ``compiler_gate_only`` when it is the only static gate.
- ``dotnet_build``: a ``dotnet build`` / ``dotnet msbuild`` / ``msbuild`` line, which runs the
  Roslyn analysers a committed project file configures (``analysers_in_build``).

The lint, analyser and formatter-check words of .NET, Gradle, PHP, Elixir, Dart, Swift
and C++ (issue #926), split from `check_command.py` for the size ratchet; it imports only
`lib.runner_args` (the shared Gradle parser, #1046). `program_name(word)` is the basename with a `.phar`, `.exe` or numeric
version suffix dropped (`vendor/bin/phpstan`, `clang-tidy-18`). `ANALYSER_PROGRAMS`
(`phpstan`, `psalm`, `phpcs`, `detekt`, `swiftlint`, `clang-tidy`, `cppcheck`) lint in any
mode. `formatter_mode(words)` is `check` for a formatter's check mode (`pint --test`,
`php-cs-fixer fix --dry-run` or `check`, `swift-format lint`, `dotnet format
--verify-no-changes`, `dotnet-format --check`, `clang-format --dry-run --Werror` or `-n
--Werror`, `mix format --check-formatted`, `dart` / `flutter format --set-exit-if-changed`),
`rewrite` when it rewrites in place, and None otherwise (`clang-format a.cc` and `dart
format -o none` only print); formatter checks count as lint (maintainer decision 2).
`subcommand_lint(words)`: `mix credo`, `mix dialyzer` (not `--plt`), `dart analyze`,
`flutter analyze`, and Gradle tasks (`gradle_tasks` drops the `:project:` prefix, option
values and the task after `-x`) named `lint*`, `ktlint*Check`, `detekt*`, `spotless*Check` or
`checkstyle*`, never one that fixes, formats, applies or records a baseline (`lintFix`,
`ktlintFormat`, `spotlessApply`, `detektBaseline`). `ecosystem_lint(words)` is any of the
three, with `php <program>` read as the program, and never a run `runs_no_analysis(words)`
names: a formatter's rewrite mode, `swiftlint --fix` / `autocorrect`, or a per-program mode
in `_NO_ANALYSIS` that configures, lists, explains or records a baseline and exits 0
(`phpcs --config-set`, `phpcs -i`, `phpstan analyse --generate-baseline`, `phpstan
clear-result-cache`, `psalm --init` / `--set-baseline`, `detekt --create-baseline` /
`--generate-config`, `swiftlint version` / `rules`, `clang-tidy --list-checks` / `-fix`,
`mix credo gen.config` / `explain`). `check_command` checks it ahead of `LINT_PROGRAMS`, on the
command and on the program a wrapper or package bin runs (`_no_analysis`: `npx phpcs
--config-set`, `uv run clang-tidy -fix`, `yarn phpstan --generate-baseline`), so those
read false at Layer 5 too. `compiler_gate(words)` is a compiler run
with warnings as errors (`mix compile --warnings-as-errors`, `-Werror` after a compiler,
`make`, `ninja` or `cmake`, `-DCMAKE_COMPILE_WARNING_AS_ERROR=ON`, `dotnet build
-warnaserror` or `-p:TreatWarningsAsErrors=true`), never a lint run (decision 1).
`dotnet_build(words)` is `dotnet build`, `dotnet msbuild` or `msbuild`. Tests:
`tests/test_lint_vocabulary.py`.
"""
from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import Literal

from lib.runner_args import gradle_tasks as shared_gradle_tasks

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
    if head == "swiftlint" and ("--fix" in flags or sub == ["autocorrect"]):
        return "rewrite"   # swiftlint --fix / autocorrect rewrite files, like clang-format -i
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


_GRADLE_LINT = re.compile(
    r"^(?!\w*(?:Fix|Format|Baseline|Apply|GenerateConfig))"
    r"(?:lint\w*|ktlint\w*Check|detekt\w*|spotless\w*Check|checkstyle\w*)$")
_GRADLE = frozenset({"gradle", "gradlew"})


def gradle_tasks(args: list[str]) -> list[str]:
    """The tasks a Gradle command runs, each without its ``:project:`` prefix, none that
    ``-x`` names (``lib.runner_args.gradle_tasks``, the one Gradle parser, #1046)."""
    tasks, excluded = shared_gradle_tasks(args)
    return [t for t in tasks if t not in excluded]


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


# Per program, the flags and subcommands that run no analysis: they configure, list,
# explain, fix or record a baseline and exit 0 (``phpcs --config-set``, ``phpstan analyse
# --generate-baseline``, ``swiftlint version``, ``clang-tidy --list-checks``).
_NO_ANALYSIS: dict[str, frozenset[str]] = {
    "phpcs": frozenset({"--config-set", "--config-show", "--config-delete", "-i", "-e"}),
    "phpstan": frozenset({"--generate-baseline", "clear-result-cache", "worker", "list",
                          "dump-deps", "diagnose"}),
    "psalm": frozenset({"--init", "--set-baseline", "--update-baseline", "--alter",
                        "--clear-cache", "--clear-global-cache", "--list-supported-issues"}),
    "detekt": frozenset({"--create-baseline", "-cb", "--generate-config", "-gc"}),
    "swiftlint": frozenset({"--fix", "autocorrect", "version", "rules", "generate-docs",
                            "reporters"}),
    "clang-tidy": frozenset({"--list-checks", "-list-checks", "--dump-config", "-dump-config",
                             "--explain-config", "-explain-config", "--fix", "-fix",
                             "--fix-errors", "-fix-errors"}),
    "cppcheck": frozenset({"--errorlist", "--doc"}),
}
# ``mix credo`` subcommands that run no analysis.
_CREDO_NOT_ANALYSIS = frozenset({"gen.config", "gen.check", "explain", "categories", "info",
                                 "version", "help"})


def runs_no_analysis(words: list[str]) -> bool:
    """An analyser or ``mix credo`` asked for a mode that runs no analysis (#926), or a
    formatter in its rewrite mode: neither is a lint run on its own."""
    if not words:
        return False
    if program_name(words[0]) == "php" and len(words) > 1 and not words[1].startswith("-"):
        return runs_no_analysis(words[1:])
    if formatter_mode(words) == "rewrite":
        return True
    head = program_name(words[0])
    if head == "mix":
        pos = _pos(words[1:])
        return pos[:1] == ["credo"] and bool(set(pos[1:2]) & _CREDO_NOT_ANALYSIS)
    modes = _NO_ANALYSIS.get(head, frozenset())
    return any(w.split("=", 1)[0] in modes for w in words[1:])


def ecosystem_lint(words: list[str]) -> bool:
    """A lint run this module's vocabulary reads: an analyser, a formatter in check mode or
    a build-tool lint subcommand; ``php <program>`` reads as the program it runs."""
    if not words:
        return False
    if program_name(words[0]) == "php" and len(words) > 1 and not words[1].startswith("-"):
        return ecosystem_lint(words[1:])
    if runs_no_analysis(words):
        return False
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
