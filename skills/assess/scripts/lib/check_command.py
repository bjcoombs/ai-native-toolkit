"""Is a shell command one an agent runs to check its work? (issues #514, #698)

``is_check_command`` classifies one command by the target it runs: a test or
check target of a runner ``lib.command_resolver`` models (``make test``,
``npm run check``, ``tox``, ``uv run pytest``, the program after ``uv run``) or a
committed script named for a check (``./scripts/test.sh``). An install command
such as ``npm ci`` or ``uv sync --extra test`` is never a check. ``lib.ci_checks``
applies it to CI lines, ``lib.named_check`` to instruction files. ``is_lint_run`` and
``may_lint`` (#782) feed ``ci_checks.lint_runs``: a linter that runs, and a check whose
recipe may run one. Pure: no file or network access, so an offline scan stays offline.

Full reference: lib/docs/check_command.md.
"""
from __future__ import annotations

import re
import shlex
from contextvars import ContextVar
from pathlib import PurePosixPath

from lib.command_flags import SHELLS, make_args, make_runs_no_recipe, shell_args, shell_string
from lib.command_resolver import RUNNERS, command_words
from lib.lint_vocabulary import (
    ANALYSER_PROGRAMS, ecosystem_lint, formatter_mode, program_name, runs_no_analysis)

CHECK_WORDS = frozenset({
    "test", "tests", "check", "checks", "verify", "validate", "pytest", "tox", "nox",
})
CHECK_NAMES: ContextVar[frozenset[str]] = ContextVar("check_names", default=CHECK_WORDS)  # lib.named_check
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
# item such as ``stages: [lint]`` is not a command. The analysers of other ecosystems
# (``phpstan``, ``clang-tidy``, #926) come from ``lib.lint_vocabulary``.
LINT_PROGRAMS = frozenset({
    "ruff", "eslint", "flake8", "pylint", "mypy", "golangci-lint", "shellcheck", "rubocop",
    "stylelint", "ktlint", "biome",
}) | ANALYSER_PROGRAMS
LINT_WORDS = LINT_PROGRAMS | {"lint", "lints", "linter", "vet", "clippy"}
_VALUED_FLAGS = frozenset({
    "-C", "-f", "--file", "--makefile", "--directory", "--dir", "--cwd", "--prefix", "--project",
    "--with", "--extra", "--group", "--only-group", "--python", "-p", "--package", "--from",
    "--filter", "-F", "--workspace", "-w", "-e", "-s", "--manifest-path",
})


def is_check_name(name: str, words: frozenset[str] | None = None) -> bool:
    """A target or script name built from a check word (``test``, ``check:all``, ``test:ci``)."""
    words = CHECK_NAMES.get() if words is None else words
    return any(part in words for part in re.split(r"[:_./-]", name.lower()))


def _positionals(args: list[str], valued: frozenset[str] = _VALUED_FLAGS) -> list[str]:
    """Non-flag words, with the value of a known valued flag (``--with pytest``) dropped."""
    out: list[str] = []
    skip = False
    for word in args:
        if skip:
            skip = False
        elif word.startswith("-"):
            skip = word in valued
        else:
            out.append(word)
    return out


def _drop_leading_flags(args: list[str], valued: frozenset[str] = _VALUED_FLAGS) -> list[str]:
    """``args`` from the first positional on: the program ``uv run --with x`` runs."""
    i = 0
    while i < len(args) and args[i].startswith("-"):
        i += 2 if args[i] in valued else 1
    return args[i:]


def _js_check(head: str, pos: list[str], names: frozenset[str] | None) -> bool:
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


def _check_target(words: list[str], names: frozenset[str] | None = None,
                  programs: frozenset[str] = ALWAYS_CHECK) -> bool:
    """Whether the program, subcommand or target a command runs is a check."""
    head = words[0]
    pos = _positionals(make_args(words[1:]) if head in ("make", "gmake") else words[1:])   # -j 4 (#955)
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
    if head in ("bash", "sh", "zsh", "source"):   # bash -e x.sh: the shell's own flags (#955)
        args = shell_args(words)
        return args is not None and bool(args) and is_check_name(PurePosixPath(args[0]).name, names)
    if head.startswith("./") and head not in RUNNERS:
        return is_check_name(PurePosixPath(head).name, names)
    # make, just, go, cargo, mvn, gradle: the first target or goal names the work. A shell
    # expansion (``mvn ${MVN_ARGS} verify``) is not read as one: it is unknown, the next word
    # is the goal (#799).
    goals = [w for w in pos if not w.startswith("$")]
    return bool(goals) and is_check_name(goals[0], names)


def is_check_command(text: str) -> bool:
    """A command an agent runs to check its work: a check target of a modelled runner or
    script, a test runner (``dotnet test``, ``vendor/bin/phpunit``, ``turbo run test``) or a
    wrapper around one (``docker compose run app pytest``, ``bundle exec rspec``)."""
    return check_verdict(text) is True


def may_check(text: str) -> bool:
    """A target whose recipe is not read, so it may run a check (#920): ``make foo``,
    ``task foo``, a bare ``make``, ``npm run foo``, ``./scripts/foo.sh``, ``docker run img``."""
    return check_verdict(text) is None


def is_lint_command(text: str) -> bool:
    """A linter run directly, or a lint target of a modelled runner or script.

    Anchored like ``is_check_command``: the program, subcommand or target the
    command runs must be the linter, so ``npm install eslint`` or
    ``uv sync --group lint`` (installs) never count.
    """
    words = command_words(text)
    return bool(words) and _lint_words(words)


def _lint_words(words: list[str]) -> bool:
    """``is_lint_command`` on words, reading a wrapper by the program it runs (#920)."""
    head = words[0]
    if _no_analysis(words) or make_runs_no_recipe(words):   # swiftlint --fix, make -n lint (#926, #955)
        return False
    if (head in RUNNERS or head.startswith("./") or head in LINT_PROGRAMS) and _check_target(
            words, LINT_WORDS, LINT_PROGRAMS) or _task_target(words, LINT_WORDS) or ecosystem_lint(words):
        return True
    hatch = _hatch_run(words)
    if hatch and is_check_name(hatch[0], LINT_WORDS):   # hatch run lint, as make lint (#955)
        return True
    program = _wrapped(words) or hatch
    return program is not None and bool(program) and _lint_words(program)


# Flags that ask any program for its version or usage and run no check (#919).
_INFO_FLAGS = frozenset({"--version", "--help"})
# Runners whose ``-h`` is help (``_H_HELP`` adds the linters, type checkers, pytest, tox
# and nox). A committed script is never one: ``./scripts/test.sh -h db`` names a host.
_H_RUNNERS = frozenset({
    "dart", "flutter", "dotnet", "go", "cargo", "ctest", "rspec", "make", "just", "bazel",
    "jest", "vitest", "mocha", "swift", "deno", "phpunit",
})
# Programs that list their tests without running them: ``pytest --collect-only`` /
# ``--co``, ``dotnet test --list-tests``.
_LIST_FLAGS: dict[str, frozenset[str]] = {
    "pytest": frozenset({"--collect-only", "--co"}),
    "py.test": frozenset({"--collect-only", "--co"}),
    "dotnet": frozenset({"--list-tests"}),
    "phpunit": frozenset({"--list-tests"}),
    "swift": frozenset({"--list-tests"}),
    "ctest": frozenset({"-N", "--show-only"}),
    "vstest.console": frozenset({"--list-tests", "/ListTests"}),
}


def is_info_invocation(text: str) -> bool:
    """A command that asks only for a version, usage or a test list, so it runs no check (#919).

    ``--version`` or ``--help`` anywhere; ``rspec --init``, which writes a config and runs
    no spec; and flags read only for the program they belong to (``_info_program`` unwraps
    ``uv run``, ``npx``, ``python -m``, ``bundle exec``, ``docker compose run``): ``-V`` for a program in ``_V_VERSION`` (the
    linters, type checkers, ``pytest``, ``tox``, ``nox``; elsewhere it can run the work,
    as in ``mvn -B -V verify`` or ``./gradlew -V test``), ``-h`` for those and the runners
    in ``_H_RUNNERS``, never for a script (``./scripts/test.sh -h db``), and the
    test-listing flags in ``_LIST_FLAGS`` (``pytest --co``, ``dotnet test --list-tests``).
    """
    words = command_words(text)
    if not words:
        return False
    if _INFO_FLAGS.intersection(words) or ("rspec" in words and "--init" in words):
        return True
    program = _info_program(words)
    if not program:
        return False
    head = program_name(program[0])   # clang-tidy-18 -h, vendor/bin/phpstan -V
    rest = set(program[1:])
    return (("-V" in rest and head in _V_VERSION) or ("-h" in rest and head in _H_HELP)
            or bool(rest & _LIST_FLAGS.get(head, frozenset())))


def _info_program(words: list[str]) -> list[str]:
    """The program a command finally runs, through ``_program`` and the #920 wrappers
    (``bundle exec rspec -h`` -> ``rspec -h``, ``docker compose run app pytest --co``)."""
    for _ in range(8):   # wrappers nest a few deep at most
        if not words:
            return []
        inner = _program(words)
        if inner == words:
            inner = _wrapped(words) or _hatch_run(words) or words
        if inner == words:
            return words
        words = inner
    return words


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
    """A go-task, turbo or nx target (``task lint``, ``turbo run lint``, ``nx affected -t lint``):
    these are not modelled runners, so they are read here."""
    return words[0] in ("task", "turbo", "nx") and any(
        is_check_name(t, names) for t in _targets(words[0], words[1:]))


# Type checkers Layer 3 credits as lint runs (#782), local to ``is_lint_run`` so
# ``is_lint_command`` (Layer 5, ``test_or_lint_configured``) keeps its vocabulary.
TYPE_PROGRAMS = frozenset({"pyright", "basedpyright", "pytype", "pyre", "tsc", "vue-tsc"})
_RUN_PROGRAMS = LINT_PROGRAMS | TYPE_PROGRAMS
# Programs whose ``-V`` prints the version and exits (``is_info_invocation``).
# ``pytype -V`` is ``--python-version`` and runs the check, so pytype is left out.
_V_VERSION = (_RUN_PROGRAMS | ALWAYS_CHECK) - {"pytype"}
_H_HELP = _V_VERSION | _H_RUNNERS
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
            return _drop_leading_flags(words[words.index(pos[0], 1) + 1:])
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
    """A package-manager command (``npm``, ``pnpm``, ``yarn``, ``bun``) that runs a lint script;
    ``is_lint_run`` reads ``npx`` / ``bunx`` by the program they run before it gets here.

    ``_js_check`` credits ``npm test`` and ``npx jest`` whatever the names, which is
    right for a check but not for a lint run.
    """
    if not pos:
        return False
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
    first = command_words(text)
    outer = (_outer_wrapper(first) or _plain_wrapper(first)) if first else None
    if outer:   # docker compose run app ruff check ., bundle exec rubocop, xargs clang-tidy
        return is_lint_run(shlex.join(outer))
    # ``type-check`` / ``type_check`` is one word here, not a ``check`` target.
    words = [_TYPE_CHECK.sub("typecheck", w) for w in command_words(text)]
    if not words or is_info_invocation(text) or make_runs_no_recipe(words):
        return False
    if _no_analysis(words):   # swiftlint --fix rewrites, phpstan --generate-baseline (#926)
        return False
    if ecosystem_lint(words):   # phpstan, mix credo, ./gradlew ktlintCheck, pint --test (#926)
        return True
    program = _exec_program(words)
    if program is not None:   # npx / pnpm exec / yarn dlx <program>: the program names the run
        return bool(program) and (is_check_name(program[0], _RUN_WORDS)
                                  or _task_target(program, _RUN_WORDS))
    if words[0] in ("npm", "pnpm", "yarn", "bun"):
        return _js_lint(words[0], _positionals(words[1:]))
    hatch = _hatch_run(words)
    if hatch is not None:   # hatch run ruff check ., hatch run lint (a lint-named script, #955)
        return bool(hatch) and (hatch[0] in _RUN_PROGRAMS or is_check_name(
            _TYPE_CHECK.sub("typecheck", hatch[0]), _RUN_WORDS))
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
    outer = _outer_wrapper(words)
    if outer is not None:   # docker compose run app (its default command) may lint
        return not outer or may_lint(shlex.join(outer))
    runner = _exec_program(words)
    if runner is not None:
        # npx / pnpm exec / yarn dlx <program>: a linter is a lint run and a test runner runs
        # tests; any other program may lint, so it reads null, never false.
        if not runner or runner[0] in _JS_TEST_RUNNERS:
            return False
        if runner[0] == "playwright" and _positionals(runner[1:])[:1] == ["test"]:
            return False
        if runner[0] == "nx" and _positionals(runner[1:])[:1] and _positionals(
                runner[1:])[0] in _NX_INFO:
            return False   # npx nx graph: information only, as at Layer 5 (#926)
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
    if program[0] in _DIRECT_TESTS or PurePosixPath(program[0]).name in TEST_PROGRAMS:
        return False
    if program[0] == "php" and _toolchain_test(program):   # php artisan test, php phpunit
        return False
    if PurePosixPath(program[0]).name in _TEST_ONLY_SUBCOMMANDS:   # dotnet test, mix test: tests only
        return _positionals(program[1:])[:1] != ["test"]
    if program[0] in ("go", "cargo", "bun"):   # go test, cargo test, bun test: built-in runners
        return _positionals(program[1:])[:1] != ["test"]
    return not (program[0] == "playwright" and _positionals(program[1:])[:1] == ["test"])


# --- Test runners and wrappers beyond ``command_resolver.RUNNERS`` (#920) ----------
# Test programs that run tests and nothing else, matched by basename (``vendor/bin/phpunit``).
TEST_PROGRAMS = frozenset({"ctest", "phpunit", "pest", "rspec"}) | _JS_TEST_RUNNERS
# Toolchains whose ``test`` subcommand runs the tests (``dotnet test``, ``bazel test //...``).
TEST_SUBCOMMANDS = frozenset({
    "dotnet", "swift", "mix", "deno", "bazel", "bazelisk", "hatch", "flutter", "rails", "dart",
})
# The ones whose ``test`` runs tests only, never a linter (``bazel test`` may run lint targets).
_TEST_ONLY_SUBCOMMANDS = TEST_SUBCOMMANDS - {"bazel", "bazelisk"}
# Runners of named targets whose recipe this scan does not read: a check-named target is a
# check, a setup target is not, and any other target (or the default one) may be (null).
TARGET_RUNNERS = frozenset({"make", "gmake", "just", "task", "rake", "turbo", "nx"})
SETUP_TARGETS = frozenset({
    "install", "deps", "dependencies", "setup", "bootstrap", "clean", "help", "init",
    "build", "compile", "deploy", "release", "publish", "docs", "format", "fmt", "prepare",
    "start", "serve", "dev", "watch", "configure", "generate", "codegen", "package", "dist",
    "upload", "migrate", "version",
})
# Programs a wrapper may run that are known not to be a check (``docker run img echo hi``).
_PLAIN_PROGRAMS = frozenset({"echo", "true", "printf", "cat", "ls", "env", "sleep", "exit"})
_WRAPPER_FLAGS = _VALUED_FLAGS | {"--spec"}
_DOCKER_RUN_FLAGS = frozenset({
    "-e", "--env", "--env-file", "-v", "--volume", "--mount", "-w", "--workdir", "--name",
    "-u", "--user", "-p", "--publish", "--network", "--net", "--entrypoint", "-l", "--label",
    "--platform", "--cpus", "-m", "--memory", "--add-host", "--device", "--gpus", "--cap-add",
    "--hostname", "-h", "--ipc", "--pull", "--restart", "--shm-size", "--ulimit",
    "--security-opt", "--tmpfs", "--volumes-from", "--cidfile", "--log-driver", "--log-opt",
})
# A program named alone as a bare YAML list item (``- rspec``, ``- make`` under ``needs:``)
# is a value unless its parent key is a script key (``lib.ci_checks._lone_check``).
BARE_RUN_PROGRAMS = ALWAYS_CHECK | TEST_PROGRAMS | TARGET_RUNNERS
_TARGET_FLAGS = frozenset({
    "-C", "-f", "--file", "--makefile", "--directory", "--dir", "-d", "--taskfile",
    "--justfile", "--working-directory", "-I", "--include-dir", "--filter", "-F", "--rakefile",
})
_COMPOSE_FLAGS = frozenset({
    "-f", "--file", "-p", "--project-name", "--env-file", "--profile", "--project-directory",
    "-e", "--env", "-w", "--workdir", "--entrypoint", "-v", "--volume", "--name", "-u",
    "--user", "--publish", "-l", "--label", "--pull",
})
_NX_TARGET_FLAGS = ("-t", "--target", "--targets")
# nx commands that print workspace information and run no target.
_NX_INFO = frozenset({"graph", "show", "list", "report", "reset", "daemon"})
_GRADLE = frozenset({"gradle", "gradlew", "./gradlew"})
# A Gradle test task: ``test``, ``testDebugUnitTest``, ``:app:testReleaseUnitTest``,
# ``connectedAndroidTest`` (Android variants and instrumented tests).
# Build-only tasks that compile, package or install the tests without running them
# (``assembleAndroidTest``, ``compileTestJava``, ``testClasses``, ``testFixturesJar``) are not.
_GRADLE_TEST = re.compile(
    r"^(?!compile|assemble|bundle|package|install|testFixtures)(?:test[A-Z0-9]\w*|\w*Test)$")
# xcodebuild options that take a value (``-scheme App``); its actions are bare words.
_XCODE_FLAGS = frozenset({
    "-scheme", "-project", "-workspace", "-destination", "-configuration", "-sdk", "-target",
    "-testPlan", "-derivedDataPath", "-resultBundlePath", "-only-testing", "-skip-testing",
    "-arch", "-xcconfig", "-toolchain", "-destination-timeout",
})
# A target runner asked for its version or its target list runs no target.
_RUNNER_INFO_FLAGS = _INFO_FLAGS | {"-h", "--list", "--list-all", "-T", "--tasks", "--summary"}


def _outer_wrapper(words: list[str]) -> list[str] | None:
    """The command ``docker compose run|exec <service>``, ``bundle exec`` or ``bash -c "<cmd>"``
    runs, ``[]`` when a compose service runs its default command or a ``-c`` string is not one
    plain command, None for any other command."""
    if not words:
        return None
    if words[0] in SHELLS and shell_args(words) is None:
        return shell_string(words)
    if words[:2] == ["docker", "compose"]:
        words = ["docker-compose", *words[2:]]
    if words[0] == "bundle":
        pos = _positionals(words[1:])
        if pos[:1] != ["exec"]:
            return None
        rest = _drop_leading_flags(words[words.index("exec") + 1:])
        return rest or None
    if words[:2] == ["docker", "run"]:   # docker run [flags] image [command]
        flags = words[2:len(words) - len(_drop_leading_flags(words[2:], _DOCKER_RUN_FLAGS))]
        return _container_command(flags, _drop_leading_flags(words[2:], _DOCKER_RUN_FLAGS))
    if words[0] != "docker-compose":
        return None
    pos = _positionals(words[1:], _COMPOSE_FLAGS)
    if pos[:1] not in (["run"], ["exec"]):
        return None
    after = words[words.index(pos[0], 1) + 1:]
    rest = _drop_leading_flags(after, _COMPOSE_FLAGS)
    return _container_command(after[:len(after) - len(rest)], rest)


def _container_command(flags: list[str], rest: list[str]) -> list[str] | None:
    """What a container runs, from its flags and ``image-or-service [command]``: the
    ``--entrypoint`` with the command as its arguments, ``[]`` for the default command,
    None when detached (``-d``: its exit cannot fail the step)."""
    if any(f in ("-d", "--detach") or f.startswith("--detach=") for f in flags):
        return None
    for i, flag in enumerate(flags):
        if flag == "--entrypoint" and i + 1 < len(flags):
            return [flags[i + 1], *rest[1:]]
        if flag.startswith("--entrypoint="):
            return [flag.split("=", 1)[1], *rest[1:]]
    return rest[1:]   # rest[0] is the image or service


def _wrapped(words: list[str]) -> list[str] | None:
    """The program a wrapper runs (#920): ``docker compose run``, ``bundle exec``, ``uv run``,
    ``poetry run``, ``pipx run``, ``uvx``, ``npx``, ``bunx`` and ``pnpm exec`` / ``yarn dlx``;
    None for any other command or no program."""
    head = words[0]
    if head in ("uv", "poetry", "pipx") and _positionals(words[1:])[:1] == ["run"]:
        return _drop_leading_flags(words[words.index("run", 1) + 1:], _WRAPPER_FLAGS) or None
    if head == "uvx":
        return _drop_leading_flags(words[1:], _WRAPPER_FLAGS) or None
    return _outer_wrapper(words) or _exec_program(words) or _plain_wrapper(words)


def _no_analysis(words: list[str]) -> bool:
    """``lint_vocabulary.runs_no_analysis`` on the command and on the program it runs: a
    wrapper (``npx phpcs --config-set``, ``uv run clang-tidy -fix``) or a package bin
    (``yarn phpstan --generate-baseline``) runs no analysis either."""
    inner = _info_program(words)
    bin_form = words[1:] if words[0] in ("pnpm", "yarn", "bun") else []
    return (runs_no_analysis(words) or runs_no_analysis(inner)
            or (bool(bin_form) and runs_no_analysis(bin_form)))


# xargs flags that take the next word as a value (``xargs -n 1 clang-tidy``).
_XARGS_FLAGS = frozenset({
    "-I", "-n", "-P", "-L", "-s", "-d", "-a", "-E", "--max-args", "--max-procs", "--delimiter",
    "--arg-file", "--replace", "--max-lines", "--max-chars", "--eof",
})


# xvfb-run and timeout flags that take the next word (``xvfb-run -s "-screen 0 1x1x24"``).
_XVFB_FLAGS = frozenset({"-s", "--server-args", "-n", "--server-num", "-f", "--auth-file",
                         "-e", "--error-file", "-p", "--xauth-protocol", "-w", "--wait"})
_TIMEOUT_FLAGS = frozenset({"-s", "--signal", "-k", "--kill-after"})


def _plain_wrapper(words: list[str]) -> list[str] | None:
    """The program ``xargs [flags]``, ``dotnet tool run``, ``xvfb-run [flags]`` or ``timeout
    [flags] DURATION`` runs (#926, #799): ``xargs clang-format-21 -i`` -> ``clang-format-21 -i``,
    ``xvfb-run -a npm test`` -> ``npm test``; None for any other command or no program."""
    if words[0] == "xargs":
        return _drop_leading_flags(words[1:], _XARGS_FLAGS) or None
    if words[0] == "xvfb-run":
        return _drop_leading_flags(words[1:], _XVFB_FLAGS) or None
    if words[0] == "timeout":
        return _drop_leading_flags(words[1:], _TIMEOUT_FLAGS)[1:] or None
    if words[:3] == ["dotnet", "tool", "run"]:
        return words[3:] or None
    return None


def is_format_rewrite(text: str) -> bool:
    """A formatter run that rewrites files in place (``clang-format -i``, ``mix format``,
    ``xargs clang-format-21 -i``): ``lib.ci_lint_gates`` reads it as a gate only when a
    later line of the job runs ``git diff --exit-code`` (#926)."""
    words = command_words(text)
    return bool(words) and not is_info_invocation(text) and formatter_mode(
        _info_program(words)) == "rewrite"


def _targets(head: str, args: list[str]) -> list[str]:
    """The targets a target runner is asked for (``make -C x VAR=1 test`` -> ``["test"]``)."""
    args = args[:args.index("--")] if "--" in args else args
    if head == "nx":
        targets: list[str] = []
        for i, word in enumerate(args):
            if word in _NX_TARGET_FLAGS:
                targets += args[i + 1].split(",") if i + 1 < len(args) else []
            elif word.split("=", 1)[0] in _NX_TARGET_FLAGS[1:] and "=" in word:
                targets += word.split("=", 1)[1].split(",")
        pos = [w for w in args if not w.startswith("-") and w not in targets]
        if pos[:1] in (["affected"], ["run-many"]):
            return targets
        if pos[:1] == ["run"]:   # nx run app:test
            # project:target[:configuration]
            return [pos[1].split(":")[1] if ":" in pos[1] else pos[1]] if len(pos) > 1 else []
        return [pos[0].rsplit(":", 1)[-1]] if pos else []   # nx test app, nx affected:test
    if head in ("make", "gmake"):   # make -j 4 build: the job count is no target (#955)
        args = make_args(args)
    pos = [w for w in _positionals(args, _TARGET_FLAGS) if "=" not in w]
    if head == "turbo":
        return pos[1:] if pos[:1] == ["run"] else pos[:1]
    return pos


def _setup_name(name: str) -> bool:
    """A target, script or file named for setup or a build (``install``, ``build:prod``,
    ``scripts/deploy.sh``): its first word is in ``SETUP_TARGETS``."""
    stem = PurePosixPath(name).name.lower().removesuffix(".sh")
    return re.split(r"[:_.-]", stem)[0] in SETUP_TARGETS


def _target_verdict(targets: list[str], *, rake: bool = False) -> bool | None:
    """True for a check-named target, False when every target is a setup one, else None."""
    if any(is_check_name(t) or (rake and t in ("spec", "specs")) for t in targets):
        return True
    if targets and all(_setup_name(t) for t in targets):
        return False
    return None


def _js_script_verdict(head: str, words: list[str]) -> bool | None:
    """A package script whose recipe is not read (``npm run foo``, ``pnpm foo``, ``yarn foo``):
    an install or exec form is not one, a setup-named script is false, any other is None."""
    pos = _positionals(words[1:])
    if not pos:
        return False   # bare yarn installs; bare npm run lists the scripts
    if pos[0] in ("run", "run-script"):
        return _target_verdict(pos[1:2]) if len(pos) > 1 else False
    if head == "npm" or pos[0] in _PM_INSTALL or (head == "bun" and pos[0] == "ci"):
        return False
    rest = words[words.index(pos[0], 1):]   # pnpm turbo run test: a bin of the package
    if rest[0] in TARGET_RUNNERS:   # pnpm nx graph, yarn turbo run build: read the runner
        return _check_verdict(rest)
    return True if _check_verdict(rest) is True else _target_verdict(pos[:1])


def _script_verdict(words: list[str]) -> bool | None:
    """A committed script whose body is not read (``./scripts/foo.sh``, ``bash ci/x.sh``)."""
    head = words[0]
    path = head
    if head in ("bash", "sh", "zsh", "source"):   # a sourced script is unread too (#1033)
        pos = shell_args(words)
        if not pos:
            return False   # bash -c '...' is read by _outer_wrapper; no script at all
        path = pos[0]
    elif head in RUNNERS:
        return False   # ./gradlew, ./mvnw: modelled runners
    elif not (head.startswith(("./", "bin/")) or head.endswith(".sh")):
        return False   # a bare word with a slash may be a YAML value (path: test-results/x.xml)
    if path.startswith(("/", "$", "~")):
        return False
    # By file name: a directory (ci/, tests/fixtures/) names no check (``./ci/deploy.sh``).
    return _target_verdict([PurePosixPath(path).name])


def _toolchain_test(words: list[str]) -> bool | None:
    """Whether a toolchain command runs its tests (``dotnet test``, ``./gradlew testDebug``,
    ``xcodebuild -scheme App test``, ``php artisan test``); None for another command."""
    head, pos = words[0], _positionals(words[1:])
    name = PurePosixPath(head).name
    if name in _GRADLE:   # ./gradlew testDebugUnitTest, :app:testReleaseUnitTest
        tasks = [t.rsplit(":", 1)[-1] for t in pos]
        return any(_GRADLE_TEST.match(t) and not t.endswith("Classes") for t in tasks)
    if head == "php":   # php artisan test, php vendor/bin/phpunit
        return pos[:2] == ["artisan", "test"] or (
            bool(pos) and PurePosixPath(pos[0]).name in TEST_PROGRAMS)
    if head == "xcodebuild":
        actions = _positionals(words[1:], _XCODE_FLAGS)
        return any(a in ("test", "test-without-building") for a in actions)
    if name in TEST_SUBCOMMANDS or head == "playwright":
        return pos[:1] == ["test"]
    return None


def _check_verdict(words: list[str]) -> bool | None:
    """Whether a command runs a check: True, False, or None for a target whose recipe is not read."""
    head = words[0]
    if make_runs_no_recipe(words):   # make -n test, make -i test: exits 0 whatever the recipe does
        return False
    if (head in RUNNERS or head.startswith("./")) and _check_target(words):
        return True
    if PurePosixPath(head).name in TEST_PROGRAMS:
        return True
    hatch = _hatch_run(words)
    if hatch is not None:   # hatch run test, hatch run pytest; another script is not read
        if not hatch or _check_verdict(hatch) is True or is_check_name(hatch[0]):
            return bool(hatch)
        return None
    container = words[0] in ("docker", "docker-compose")
    compose = _outer_wrapper(words)
    if compose == [] and (container or head in SHELLS):   # a default command, or bash -c "a && b"
        return None
    program = _wrapped(words)
    if program is not None:
        verdict = _check_verdict(program)
        if (verdict is False and not _setup_program(program) and program[0] not in _KNOWN_PROGRAMS
                and _plain_wrapper(words) is None):   # xargs rm: a known wrapper stays false
            return None   # uv run foo, npx foo, docker run img foo: a program this scan does not know
        return verdict
    if head == "deno" and _positionals(words[1:])[:1] == ["task"]:   # deno task foo: not read
        return _target_verdict(_positionals(words[1:])[1:2])
    toolchain = _toolchain_test(words)
    if toolchain is not None:
        return toolchain
    if head in TARGET_RUNNERS:
        if _RUNNER_INFO_FLAGS.intersection(words):   # make --version, task --list, rake -T
            return False
        if head == "nx" and _positionals(words[1:])[:1] and _positionals(words[1:])[0] in _NX_INFO:
            return False   # nx graph, nx show projects: information only (#926)
        return _target_verdict(_targets(head, words[1:]), rake=head == "rake")
    if head in ("npm", "pnpm", "yarn", "bun"):
        return _js_script_verdict(head, words)
    return _script_verdict(words)


def check_verdict(text: str) -> bool | None:
    """``is_check_command`` as three states (#920): None for an unrecognised target of a
    runner whose recipe is not read, so CI reads it unknown rather than absent."""
    words = command_words(text)
    return _check_verdict(words) if words else False


# Programs whose non-check forms this scan reads as definite (``uv run python -m build``,
# ``npx eslint .`` is a lint line, not a check): behind a wrapper they stay false, where
# any other program reads null (#920).
_KNOWN_PROGRAMS = (_PLAIN_PROGRAMS | frozenset(RUNNERS) | TARGET_RUNNERS | TEST_SUBCOMMANDS
                   | LINT_PROGRAMS | TYPE_PROGRAMS)
# Release, deploy and infrastructure tools: whatever they are asked, they run no check
# (``bundle exec fastlane beta``, ``npx vercel --prod``).
_DEPLOY_TOOLS = frozenset({
    "fastlane", "twine", "wrangler", "changeset", "vercel", "netlify", "firebase", "serverless",
    "sls", "cdk", "terraform", "pulumi", "semantic-release", "gh", "aws", "gcloud", "az",
})


def _setup_program(program: list[str]) -> bool:
    """A wrapped program that only sets up, builds or ships: a setup-named program or one of
    its first two subcommands (``npx wrangler deploy``, ``npx prisma migrate deploy``,
    ``npx playwright install``), or a deploy tool (``bundle exec fastlane beta``)."""
    return (program[0] in _DEPLOY_TOOLS or _setup_name(program[0])
            or any(_setup_name(w) for w in _positionals(program[1:])[:2]))
