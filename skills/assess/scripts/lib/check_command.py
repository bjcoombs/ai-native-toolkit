"""Is a shell command one an agent runs to check its work? (issues #514, #698)

``is_check_command`` classifies one command by the target it runs: a test or
check target of a runner ``lib.command_resolver`` models (``make test``,
``npm run ci``, ``tox``, ``uv run pytest``, the program after ``uv run``) or a
committed script named for a check (``./scripts/test.sh``). An install command
such as ``npm ci`` or ``uv sync --extra test`` is never a check.
``lib.agent_environment`` applies it to the commands instruction files name,
``lib.ci_checks`` to the lines of CI configuration. ``is_lint_run`` and
``may_lint`` (#782) feed ``ci_checks.lint_runs``: a linter that runs, and a
check whose recipe may run one. Pure: no file or network
access, so a scan that only classifies commands stays offline.
"""
from __future__ import annotations

import re
from pathlib import PurePosixPath

from lib.command_resolver import RUNNERS, command_words

CHECK_WORDS = frozenset({
    "test", "tests", "check", "checks", "verify", "ci", "validate", "pytest", "tox", "nox",
})
ALWAYS_CHECK = frozenset({"pytest", "py.test", "tox", "nox"})
_PM_TEST = frozenset({"test", "t", "tst"})
_PM_INSTALL = frozenset({
    "install", "i", "add", "remove", "rm", "update", "up", "upgrade", "dlx", "exec",
    "create", "init", "link", "publish", "setup", "x",
})
_JS_TEST_RUNNERS = frozenset({"jest", "vitest", "mocha", "ava", "jasmine", "cypress"})
# Linters run as their own program (``ruff check .``, ``npx eslint``, ``uv run mypy``),
# and the words a lint target, script or subcommand is named by (``make lint``,
# ``go vet``, ``cargo clippy``). A bare ``lint`` is never a program: a YAML list
# item such as ``stages: [lint]`` is not a command.
LINT_PROGRAMS = frozenset({
    "ruff", "eslint", "flake8", "pylint", "mypy", "golangci-lint", "shellcheck", "rubocop",
    "stylelint", "ktlint", "biome",
})
LINT_WORDS = LINT_PROGRAMS | {"lint", "lints", "linter", "vet", "clippy"}
_VALUED_FLAGS = frozenset({
    "-C", "-f", "--file", "--makefile", "--directory", "--dir", "--cwd", "--prefix", "--project",
    "--with", "--extra", "--group", "--only-group", "--python", "-p", "--package", "--from",
    "--filter", "-F", "--workspace", "-w", "-e", "-s", "--manifest-path",
})


def is_check_name(name: str, words: frozenset[str] = CHECK_WORDS) -> bool:
    """A target or script name built from a check word (``test``, ``check:all``, ``ci``)."""
    return any(part in words for part in re.split(r"[:_./-]", name.lower()))


def _positionals(args: list[str]) -> list[str]:
    """Non-flag words, with the value of a known valued flag (``--with pytest``) dropped."""
    out: list[str] = []
    skip = False
    for word in args:
        if skip:
            skip = False
        elif word.startswith("-"):
            skip = word in _VALUED_FLAGS
        else:
            out.append(word)
    return out


def _drop_leading_flags(args: list[str]) -> list[str]:
    """``args`` from the first positional on: the program ``uv run --with x`` runs."""
    i = 0
    while i < len(args) and args[i].startswith("-"):
        i += 2 if args[i] in _VALUED_FLAGS else 1
    return args[i:]


def _js_check(head: str, pos: list[str], names: frozenset[str]) -> bool:
    """A package-manager or ``npx`` / ``bunx`` command that runs a check."""
    if not pos:
        return False
    if head in ("npx", "bunx"):
        if pos[0] == "playwright":
            return pos[1:2] == ["test"]
        return pos[0] in _JS_TEST_RUNNERS or is_check_name(pos[0], names)
    if pos[0] in ("run", "run-script"):
        return len(pos) > 1 and is_check_name(pos[1], names)
    # npm runs only its test aliases without `run`; `npm ci` installs.
    # `bun ci` is a frozen install; in pnpm and yarn `ci` is a script name.
    if head == "npm" or pos[0] in _PM_INSTALL or (head == "bun" and pos[0] == "ci"):
        return pos[0] in _PM_TEST
    # pnpm, yarn and bun run a package script named as the subcommand.
    return pos[0] in _PM_TEST or is_check_name(pos[0], names)


def _check_target(words: list[str], names: frozenset[str] = CHECK_WORDS,
                  programs: frozenset[str] = ALWAYS_CHECK) -> bool:
    """Whether the program, subcommand or target a command runs is a check."""
    head, pos = words[0], _positionals(words[1:])
    if head in programs:
        return True
    if head in ("uv", "poetry", "uvx"):
        rest = words[1:]
        if head != "uvx":
            if not pos or pos[0] != "run":
                return False  # sync, install, add: setup, not a check
            rest = rest[rest.index("run") + 1:]
        program = _drop_leading_flags(rest)
        return bool(program) and _check_target(program, names, programs)
    if head in ("npx", "bunx", "npm", "pnpm", "yarn", "bun"):
        return _js_check(head, pos, names)
    if head in ("python", "python3"):
        if "-m" in words:
            module = words[words.index("-m") + 1:words.index("-m") + 2]
            return bool(module) and module[0] in programs
        return bool(pos) and is_check_name(PurePosixPath(pos[0]).name, names)
    if head in ("bash", "sh", "zsh", "source"):
        return bool(pos) and is_check_name(PurePosixPath(pos[0]).name, names)
    if head.startswith("./") and head not in RUNNERS:
        return is_check_name(PurePosixPath(head).name, names)
    # make, just, go, cargo, mvn, gradle: the first target or goal names the work.
    return bool(pos) and is_check_name(pos[0], names)


def is_check_command(text: str) -> bool:
    """A command an agent runs to check its work: a check target of a modelled runner or script."""
    words = command_words(text)
    if not words:
        return False
    head = words[0]
    if head not in RUNNERS and not head.startswith("./"):
        return False
    return _check_target(words)


def is_lint_command(text: str) -> bool:
    """A linter run directly, or a lint target of a modelled runner or script.

    Anchored like ``is_check_command``: the program, subcommand or target the
    command runs must be the linter, so ``npm install eslint`` or
    ``uv sync --group lint`` (installs) never count.
    """
    words = command_words(text)
    if not words:
        return False
    head = words[0]
    if head not in RUNNERS and not head.startswith("./") and head not in LINT_PROGRAMS:
        return False
    return _check_target(words, LINT_WORDS, LINT_PROGRAMS)


# A linter asked only for its version or usage runs no lint (``ruff --version``, #782).
_INFO_FLAGS = frozenset({"--version", "--help", "-V"})
# Test programs that run tests and nothing else: no linter can hide inside them.
_DIRECT_TESTS = (ALWAYS_CHECK - {"tox", "nox"}) | _JS_TEST_RUNNERS


def _program(words: list[str]) -> list[str]:
    """The program a wrapper runs: ``uv run`` / ``poetry run`` / ``uvx`` / ``npx`` / ``python -m``."""
    head = words[0]
    if head in ("uv", "poetry") and "run" in words:
        rest = _drop_leading_flags(words[words.index("run") + 1:])
        return _program(rest) if rest else []
    if head in ("uvx", "npx", "bunx"):
        rest = _drop_leading_flags(words[1:])
        return _program(rest) if rest else []
    if head in ("python", "python3") and "-m" in words:
        return words[words.index("-m") + 1:]
    return words


def _task_target(words: list[str], names: frozenset[str]) -> bool:
    """A go-task target (``task lint``): ``task`` is not a modelled runner, so it is read here."""
    pos = _positionals(words[1:])
    return words[0] == "task" and bool(pos) and is_check_name(pos[0], names)


def _js_lint(head: str, pos: list[str]) -> bool:
    """A package-manager or ``npx`` command that runs a linter or a lint script.

    ``_js_check`` credits ``npm test`` and ``npx jest`` whatever the names, which is
    right for a check but not for a lint run.
    """
    if not pos:
        return False
    if head in ("npx", "bunx"):
        return is_check_name(pos[0], LINT_WORDS)
    if pos[0] in ("run", "run-script"):
        return len(pos) > 1 and is_check_name(pos[1], LINT_WORDS)
    if head == "npm" or pos[0] in _PM_INSTALL or pos[0] in _PM_TEST:
        return False
    return is_check_name(pos[0], LINT_WORDS)


def is_lint_run(text: str) -> bool:
    """``is_lint_command`` that also runs the lint (#782): not ``--version`` / ``--help`` / ``-V``.

    A go-task lint target (``task lint``) counts too, and a package-manager test
    alias (``npm test``, ``npx jest``) does not. ``is_lint_command`` keeps its
    meaning: ``lib.ci_checks.test_or_lint_runs`` and Layer 5 read it unchanged.
    """
    words = command_words(text)
    if not words or _INFO_FLAGS.intersection(words):
        return False
    if words[0] in ("npx", "bunx", "npm", "pnpm", "yarn", "bun"):
        return _js_lint(words[0], _positionals(words[1:]))
    return is_lint_command(text) or _task_target(words, LINT_WORDS)


def may_lint(text: str) -> bool:
    """A check that may run a linter inside it, which a CI line alone cannot show (#782).

    A check target of a runner or a script (``make check``, ``npm test``, ``tox``,
    ``./scripts/test.sh``, ``task check``) or ``pre-commit run``. A direct test-program
    call (``pytest``, ``uv run pytest``, ``go test``, ``cargo test``, ``npx jest``) runs
    tests only, so it is not one.
    """
    words = command_words(text)
    if not words:
        return False
    program = _program(words)
    if program[:2] == ["pre-commit", "run"]:
        return True
    if _task_target(words, CHECK_WORDS):
        return True
    if not is_check_command(text) or not program:
        return False
    if program[0] in _DIRECT_TESTS:
        return False
    if program[0] in ("go", "cargo"):
        return _positionals(program[1:])[:1] != ["test"]
    return not (program[0] == "playwright" and _positionals(program[1:])[:1] == ["test"])
