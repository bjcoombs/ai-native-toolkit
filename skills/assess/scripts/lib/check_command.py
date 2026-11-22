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
# Programs whose ``-V`` runs the work: Maven prints its version and builds, ctest is verbose.
_V_RUNS = frozenset({"mvn", "mvnw", "./mvnw", "ctest"})


def is_info_invocation(text: str) -> bool:
    """A command that asks only for a version or usage, so it runs no check (#919).

    ``--version`` or ``--help`` anywhere, or ``-V`` unless the program it runs uses
    ``-V`` for something else (``mvn -B -V verify``, ``ctest -V``). ``ruff --version``,
    ``pytest --version`` and ``uv run tox --version`` are info invocations.
    """
    words = command_words(text)
    if not words or not _INFO_FLAGS.intersection(words):
        return False
    if {"--version", "--help"}.intersection(words):
        return True
    program = _program(words)
    return not program or program[0] not in _V_RUNS
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


# Type checkers Layer 3 credits as lint runs (#782), local to ``is_lint_run`` so
# ``is_lint_command`` (Layer 5, ``test_or_lint_configured``) keeps its vocabulary.
TYPE_PROGRAMS = frozenset({"pyright", "basedpyright", "pytype", "pyre", "tsc", "vue-tsc"})
_RUN_PROGRAMS = LINT_PROGRAMS | TYPE_PROGRAMS
# Package-manager forms that run a named program: ``npm exec``, ``pnpm dlx``, ``bun x``.
_EXEC_SUBCOMMANDS = frozenset({"exec", "dlx", "x"})


def _exec_program(words: list[str]) -> list[str] | None:
    """The program a package-manager runner form runs (``pnpm exec eslint .`` -> ``eslint .``).

    ``npx`` / ``bunx``, and ``exec`` / ``dlx`` / ``x`` under npm, pnpm, yarn or bun. None
    for any other command, including a package script (``npm run lint``).
    """
    head = words[0]
    if head in ("npx", "bunx"):
        return _drop_leading_flags(words[1:])
    if head in ("npm", "pnpm", "yarn", "bun"):
        pos = _positionals(words[1:])
        if pos[:1] and pos[0] in _EXEC_SUBCOMMANDS:
            return _drop_leading_flags(words[words.index(pos[0]) + 1:])
    return None
_RUN_WORDS = LINT_WORDS | TYPE_PROGRAMS | {"typecheck", "typechecks"}
_TYPE_CHECK = re.compile(r"type[-_]check", re.IGNORECASE)


def _lint_target(words: list[str]) -> bool:
    """``is_lint_command``'s anchoring over the Layer 3 vocabulary (linters and type checkers)."""
    head = words[0]
    if head not in RUNNERS and not head.startswith("./") and head not in _RUN_PROGRAMS:
        return False
    return _check_target(words, _RUN_WORDS, _RUN_PROGRAMS)


def _js_lint(head: str, pos: list[str]) -> bool:
    """A package-manager or ``npx`` command that runs a linter or a lint script.

    ``_js_check`` credits ``npm test`` and ``npx jest`` whatever the names, which is
    right for a check but not for a lint run.
    """
    if not pos:
        return False
    if head in ("npx", "bunx"):
        return is_check_name(pos[0], _RUN_WORDS)
    if pos[0] in ("run", "run-script"):
        return len(pos) > 1 and is_check_name(pos[1], _RUN_WORDS)
    if head == "npm" or pos[0] in _PM_INSTALL or pos[0] in _PM_TEST:
        return False
    return is_check_name(pos[0], _RUN_WORDS)


def _hatch_run(words: list[str]) -> list[str] | None:
    """What ``hatch run`` runs (``hatch run lint`` -> ``["lint"]``), None for another command.

    ``hatch`` is not a modelled runner, so it is read here (#782): the first word after
    ``run`` is a script name (``lint``, ``test:cov``) or a program (``ruff check``).
    """
    if words[0] != "hatch":
        return None
    pos = _positionals(words[1:])
    if pos[:1] != ["run"]:
        return None
    return _drop_leading_flags(words[words.index("run") + 1:])


def _hatch_fmt_check(words: list[str]) -> bool:
    """``hatch fmt --check``: Hatch's ruff lint and format check, failing on a finding."""
    return words[0] == "hatch" and _positionals(words[1:])[:1] == ["fmt"] and "--check" in words


def is_lint_run(text: str) -> bool:
    """``is_lint_command`` that also runs the lint (#782): not ``--version`` / ``--help`` / ``-V``.

    Type checkers count as linters here (``pyright``, ``tsc --noEmit``, ``npx vue-tsc``,
    ``npm run typecheck``, ``make type-check``). A go-task lint target (``task lint``),
    ``hatch fmt --check`` and ``hatch run``
    of a linter program (``hatch run ruff check .``) count too, and a package-manager test
    alias (``npm test``, ``npx jest``) does not. ``is_lint_command`` keeps its
    meaning: ``lib.ci_checks.test_or_lint_configured`` and Layer 5 read it unchanged.
    """
    # ``type-check`` / ``type_check`` is one word here, not a ``check`` target.
    words = [_TYPE_CHECK.sub("typecheck", w) for w in command_words(text)]
    if not words or is_info_invocation(text):
        return False
    program = _exec_program(words)
    if program is not None:   # npx / pnpm exec / yarn dlx <program>: the program names the run
        return bool(program) and is_check_name(program[0], _RUN_WORDS)
    if words[0] in ("npm", "pnpm", "yarn", "bun"):
        return _js_lint(words[0], _positionals(words[1:]))
    hatch = _hatch_run(words)
    if hatch is not None:   # hatch run ruff check .: a linter program; a script name may lint
        return bool(hatch) and hatch[0] in _RUN_PROGRAMS
    return _hatch_fmt_check(words) or _lint_target(words) or _task_target(words, _RUN_WORDS)


def may_lint(text: str) -> bool:
    """A check that may run a linter inside it, which a CI line alone cannot show (#782).

    A check target of a runner or a script (``make check``, ``npm test``, ``tox``,
    ``./scripts/test.sh``, ``task check``), ``pre-commit run``, a program run through
    a package-manager runner that is neither a linter nor a test runner (``npx
    prettier --check``, ``pnpm dlx some-tool``), or any ``hatch run``
    script (``hatch run lint``, ``hatch run test:cov``), whose recipe is not read. A direct test-program
    call (``pytest``, ``uv run pytest``, ``go test``, ``cargo test``, ``bun test``, ``npx jest``) runs
    tests only, so it is not one.
    """
    words = command_words(text)
    if not words:
        return False
    runner = _exec_program(words)
    if runner is not None:
        # npx / pnpm exec / yarn dlx <program>: a linter is a lint run and a test runner runs
        # tests; any other program may lint, so it reads null, never false.
        if not runner or runner[0] in _JS_TEST_RUNNERS:
            return False
        if runner[0] == "playwright" and _positionals(runner[1:])[:1] == ["test"]:
            return False
        return not is_check_name(_TYPE_CHECK.sub("typecheck", runner[0]), _RUN_WORDS)
    program = _program(words)
    if program[:2] == ["pre-commit", "run"]:
        return True
    hatch = _hatch_run(words)
    if hatch is not None:   # hatch run <script>: like tox -e, its recipe is not read
        return bool(hatch) and hatch[0] not in _DIRECT_TESTS | LINT_PROGRAMS
    if _task_target(words, CHECK_WORDS):
        return True
    if not is_check_command(text) or not program:
        return False
    if program[0] in _DIRECT_TESTS:
        return False
    if program[0] in ("go", "cargo", "bun"):   # go test, cargo test, bun test: built-in runners
        return _positionals(program[1:])[:1] != ["test"]
    return not (program[0] == "playwright" and _positionals(program[1:])[:1] == ["test"])
