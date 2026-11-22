"""Tests for lib/lint_vocabulary.py and its use in lib/check_command.py (issue #926): the lint
and formatter-check commands of .NET, Gradle, PHP, Elixir, Ruby, Dart, Swift and C++."""
from __future__ import annotations

import pytest

from lib.check_command import (
    check_verdict, is_format_rewrite, is_info_invocation, is_lint_command, is_lint_run)
from lib.lint_vocabulary import compiler_gate, dotnet_build, formatter_mode, program_name

# Acceptance criterion 1: each reads true from is_lint_command and is_lint_run.
LINT_RUNS = [
    "vendor/bin/phpstan analyse", "vendor/bin/psalm", "vendor/bin/phpcs", "vendor/bin/pint --test",
    "vendor/bin/php-cs-fixer fix --dry-run", "./gradlew spotlessCheck",
    "./gradlew :app:lintProdRelease", "./gradlew ktlintCheck", "./gradlew detekt",
    "./gradlew checkstyleMain", "mix format --check-formatted", "mix credo", "mix dialyzer",
    "bundle exec rubocop", "dart analyze", "flutter analyze", "dart format --set-exit-if-changed .",
    "swiftlint lint --strict", "swift-format lint -r .", "clang-tidy src/a.cc",
    "clang-tidy-18 src/a.cc", "cppcheck src", "clang-format --dry-run --Werror a.cc",
    "dotnet format --verify-no-changes", "dotnet tool run dotnet-format --verify-no-changes",
    "xargs clang-format-21 --dry-run --Werror",
    # Beyond the table: other spellings of the same forms.
    "clang-format -n --Werror a.cc", "clang-format --dry-run -Werror a.cc", "php vendor/bin/phpstan analyse", "./vendor/bin/phpstan",
    "gradle detektMain", "gradlew lint", "./gradlew build ktlintMainSourceSetCheck",
    "phpstan.phar analyse", "php-cs-fixer check", "xargs -0 -n 1 clang-tidy -p build",
]

# Acceptance criterion 2: each reads false from is_lint_run.
NOT_LINT_RUNS = [
    "clang-format -i a.cc", "swift-format format -i -r .", "mix format", "dart format .",
    "vendor/bin/pint", "dotnet format", "mix compile --warnings-as-errors",
    "./gradlew assembleRelease", "phpstan --version",
    # Beyond the table: rewrite and setup forms of the new vocabulary.
    "./gradlew lintFix", "./gradlew ktlintFormat", "./gradlew spotlessApply",
    "./gradlew detektBaseline", "./gradlew build -x lint", "mix dialyzer --plt",
    "php-cs-fixer fix", "clang-format --Werror a.cc", "clang-tidy-18 --version",
    "swiftlint --help", "dart pub get", "xargs rm -f", "swiftlint --fix", "swiftlint lint --fix",
    "swiftlint autocorrect",
    # Analyser modes that run no analysis (the lead's pre-review, item 2).
    "vendor/bin/phpcs --config-set installed_paths vendor/x", "vendor/bin/phpcs --config-show",
    "vendor/bin/phpcs -i", "vendor/bin/phpstan analyse --generate-baseline",
    "vendor/bin/phpstan clear-result-cache", "swiftlint version", "swiftlint rules",
    "clang-tidy --list-checks", "clang-tidy-18 -dump-config", "vendor/bin/psalm --init",
    "vendor/bin/psalm --set-baseline=psalm-baseline.xml", "detekt --create-baseline",
    "detekt --generate-config", "mix credo gen.config", "mix credo explain lib/a.ex",
    "php vendor/bin/phpstan analyse --generate-baseline", "clang-tidy -fix src/a.cc",
    # The same modes behind a wrapper or as a package bin (claude-review on 3913f47).
    "npx phpcs --config-set a b", "npx psalm --init", "npx swiftlint --fix",
    "npx swiftlint version", "yarn phpstan --generate-baseline", "uv run clang-tidy -fix a.cc",
    "pnpm exec clang-tidy -fix a.cc", "bundle exec swiftlint --fix",
]


@pytest.mark.parametrize("text", LINT_RUNS)
def test_an_ecosystem_lint_command_is_a_lint_run(text: str) -> None:
    assert is_lint_run(text) is True
    assert is_lint_command(text) is True


@pytest.mark.parametrize("text", NOT_LINT_RUNS)
def test_a_rewrite_compile_or_version_form_is_not_a_lint_run(text: str) -> None:
    assert is_lint_run(text) is False


@pytest.mark.parametrize(("word", "name"), [
    ("clang-tidy-18", "clang-tidy"), ("clang-format-21", "clang-format"),
    ("vendor/bin/phpstan", "phpstan"), ("./vendor/bin/php-cs-fixer", "php-cs-fixer"),
    ("phpstan.phar", "phpstan"), ("./gradlew", "gradlew"), ("ruff", "ruff"),
])
def test_program_name_drops_the_path_and_a_version_suffix(word: str, name: str) -> None:
    assert program_name(word) == name


@pytest.mark.parametrize(("words", "mode"), [
    (["clang-format", "-i", "a.cc"], "rewrite"),
    (["clang-format", "--dry-run", "--Werror"], "check"),
    (["clang-format", "a.cc"], None),                       # prints to stdout, changes nothing
    (["swift-format", "-i", "-r", "."], "rewrite"),
    (["swift-format", "format", "a.swift"], None),
    (["pint"], "rewrite"), (["pint", "--test"], "check"),
    (["php-cs-fixer", "fix"], "rewrite"), (["php-cs-fixer", "fix", "--dry-run"], "check"),
    (["dotnet", "format"], "rewrite"), (["dotnet-format", "--check"], "check"),
    (["mix", "format"], "rewrite"), (["dart", "format", "-o", "none", "."], None),
    (["dart", "format", "--output=none", "."], None), (["flutter", "format", "."], "rewrite"),
    (["ruff", "check", "."], None),
])
def test_formatter_mode(words: list[str], mode: str | None) -> None:
    assert formatter_mode(words) == mode


@pytest.mark.parametrize(("text", "rewrites"), [
    ("clang-format -i a.cc", True), ("xargs clang-format-21 -i -style=file", True),
    ("bundle exec rubocop", False), ("clang-format --dry-run --Werror a.cc", False),
    ("dotnet tool run dotnet-format", True), ("echo clang-format -i", False),
])
def test_is_format_rewrite(text: str, rewrites: bool) -> None:
    assert is_format_rewrite(text) is rewrites


@pytest.mark.parametrize(("words", "gate"), [
    (["mix", "compile", "--warnings-as-errors"], True),
    (["mix", "compile"], False),
    (["cmake", "-DCMAKE_CXX_FLAGS=-Werror", ".."], True),
    (["cmake", "-DCMAKE_COMPILE_WARNING_AS_ERROR=ON", ".."], True),
    (["g++-13", "-Wall", "-Werror", "a.cc"], True),
    (["make", "CXXFLAGS=-Wall -Werror"], True),
    (["clang-format", "--Werror", "a.cc"], False),
    (["cmake", ".."], False),
    (["dotnet", "build", "-warnaserror"], True),
    (["dotnet", "build", "/p:TreatWarningsAsErrors=true"], True),
    (["dotnet", "build", "-p:TreatWarningsAsErrors=false"], False),
    (["msbuild", "/warnaserror", "App.sln"], True),
    (["dotnet", "build"], False),
    (["echo", "-Werror"], False),
])
def test_compiler_gate(words: list[str], gate: bool) -> None:
    assert compiler_gate(words) is gate


@pytest.mark.parametrize(("words", "build"), [
    (["dotnet", "build", "-c", "Release"], True), (["dotnet", "msbuild"], True),
    (["msbuild", "App.sln"], True), (["msbuild.exe", "App.sln"], True),
    (["dotnet", "test"], False), (["dotnet", "restore"], False),
])
def test_dotnet_build(words: list[str], build: bool) -> None:
    assert dotnet_build(words) is build


def test_a_version_suffixed_analyser_asks_only_for_its_version() -> None:
    assert is_info_invocation("clang-tidy-18 -h") and is_info_invocation("phpstan -V")


@pytest.mark.parametrize(("text", "verdict"), [
    ("xargs pytest", True),          # xargs reads as the program it runs (#926)
    ("xargs rm -f", False),          # a known wrapper: a non-check program stays false
    ("nx graph", False), ("nx show projects", False), ("nx list", False), ("nx report", False),
    ("nx foo app", None),
])
def test_check_verdict_of_the_new_wrappers_and_nx_information_commands(
        text: str, verdict: bool | None) -> None:
    assert check_verdict(text) is verdict
