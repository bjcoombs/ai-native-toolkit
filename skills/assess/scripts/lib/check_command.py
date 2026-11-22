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
import shlex
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
    if (head in RUNNERS or head.startswith("./") or head in LINT_PROGRAMS) and _check_target(
            words, LINT_WORDS, LINT_PROGRAMS) or _task_target(words, LINT_WORDS):
        return True
    program = _wrapped(words) or _hatch_run(words)
    return program is not None and bool(program) and _lint_words(program)


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
    """A go-task, turbo or nx target (``task lint``, ``turbo run lint``, ``nx affected -t lint``):
    these are not modelled runners, so they are read here."""
    return words[0] in ("task", "turbo", "nx") and any(
        is_check_name(t, names) for t in _targets(words[0], words[1:]))


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
    outer = _outer_wrapper(command_words(text))
    if outer:   # docker compose run app ruff check ., bundle exec rubocop: the program runs
        return is_lint_run(shlex.join(outer))
    # ``type-check`` / ``type_check`` is one word here, not a ``check`` target.
    words = [_TYPE_CHECK.sub("typecheck", w) for w in command_words(text)]
    if not words or _INFO_FLAGS.intersection(words):
        return False
    program = _exec_program(words)
    if program is not None:   # npx / pnpm exec / yarn dlx <program>: the program names the run
        return bool(program) and (is_check_name(program[0], _RUN_WORDS)
                                  or _task_target(program, _RUN_WORDS))
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
})
# Programs a container may run that are known not to be a check (``docker run img echo hi``).
_PLAIN_PROGRAMS = frozenset({"echo", "true", "printf", "cat", "ls", "env", "sleep", "exit"})
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
    "--user", "--publish", "-l", "--label",
})
_NX_TARGET_FLAGS = ("-t", "--target", "--targets")
_GRADLE = frozenset({"gradle", "gradlew", "./gradlew"})
# A Gradle test task: ``test``, ``testDebugUnitTest``, ``:app:testReleaseUnitTest``,
# ``connectedAndroidTest`` (Android variants and instrumented tests).
_GRADLE_TEST = re.compile(r"^(?:test[A-Z0-9]\w*|\w*Test)$")
# xcodebuild options that take a value (``-scheme App``); its actions are bare words.
_XCODE_FLAGS = frozenset({
    "-scheme", "-project", "-workspace", "-destination", "-configuration", "-sdk", "-target",
    "-testPlan", "-derivedDataPath", "-resultBundlePath", "-only-testing", "-skip-testing",
    "-arch", "-xcconfig", "-toolchain", "-destination-timeout",
})
# A target runner asked for its version or its target list runs no target.
_RUNNER_INFO_FLAGS = _INFO_FLAGS | {"-h", "--list", "--list-all", "-T", "--tasks", "--summary"}


def _outer_wrapper(words: list[str]) -> list[str] | None:
    """The command ``docker compose run|exec <service>`` or ``bundle exec`` runs, ``[]`` when
    a compose service runs its default command, None for any other command."""
    if not words:
        return None
    if words[:2] == ["docker", "compose"]:
        words = ["docker-compose", *words[2:]]
    if words[0] == "bundle":
        pos = _positionals(words[1:])
        if pos[:1] != ["exec"]:
            return None
        rest = _drop_leading_flags(words[words.index("exec") + 1:])
        return rest or None
    if words[:2] == ["docker", "run"]:   # docker run [flags] image [command]
        rest = _drop_leading_flags(words[2:], _DOCKER_RUN_FLAGS)
        return rest[1:]
    if words[0] != "docker-compose":
        return None
    pos = _positionals(words[1:], _COMPOSE_FLAGS)
    if pos[:1] not in (["run"], ["exec"]):
        return None
    rest = _drop_leading_flags(words[words.index(pos[0], 1) + 1:], _COMPOSE_FLAGS)
    return rest[1:]   # rest[0] is the service


def _wrapped(words: list[str]) -> list[str] | None:
    """The program a wrapper runs (#920): ``docker compose run``, ``bundle exec``, ``uv run``,
    ``poetry run``, ``npx`` and ``pnpm exec``; None for any other command or no program."""
    head = words[0]
    if head in ("uv", "poetry") and _positionals(words[1:])[:1] == ["run"]:
        return _drop_leading_flags(words[words.index("run") + 1:]) or None
    return _outer_wrapper(words) or _exec_program(words) or None


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
            return [pos[1].rsplit(":", 1)[-1]] if len(pos) > 1 else []
        return [pos[0].rsplit(":", 1)[-1]] if pos else []   # nx test app, nx affected:test
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
    rest = words[words.index(pos[0]):]   # pnpm turbo run test: a bin of the package
    return True if _check_verdict(rest) is True else _target_verdict(pos[:1])


def _script_verdict(words: list[str]) -> bool | None:
    """A committed script whose body is not read (``./scripts/foo.sh``, ``bash ci/x.sh``)."""
    head = words[0]
    path = head
    if head in ("bash", "sh", "zsh"):
        if any(w.startswith("-") and "c" in w[1:] for w in words[1:]):
            return False   # bash -c '...': the program is a string, read elsewhere
        pos = _positionals(words[1:])
        if not pos:
            return False
        path = pos[0]
    elif head in RUNNERS:
        return False   # ./gradlew, ./mvnw: modelled runners
    if path.startswith(("/", "$", "~")) or ("/" not in path and not path.endswith(".sh")):
        return False
    return _target_verdict([path])


def _toolchain_test(words: list[str]) -> bool | None:
    """Whether a toolchain command runs its tests (``dotnet test``, ``./gradlew testDebug``,
    ``xcodebuild -scheme App test``, ``php artisan test``); None for another command."""
    head, pos = words[0], _positionals(words[1:])
    name = PurePosixPath(head).name
    if name in _GRADLE:   # ./gradlew testDebugUnitTest, :app:testReleaseUnitTest
        return any(_GRADLE_TEST.match(t.rsplit(":", 1)[-1]) for t in pos)
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
    if compose == [] and container:   # the image's or service's default command is not read
        return None
    program = _wrapped(words)
    if program is not None:
        verdict = _check_verdict(program)
        if container and verdict is False and program[0] not in _PLAIN_PROGRAMS | frozenset(
                RUNNERS) | TARGET_RUNNERS | TEST_SUBCOMMANDS:
            return None   # docker run img some-tool: a program this scan does not know
        return verdict
    if head == "deno" and _positionals(words[1:])[:1] == ["task"]:   # deno task foo: not read
        return _target_verdict(_positionals(words[1:])[1:2])
    toolchain = _toolchain_test(words)
    if toolchain is not None:
        return toolchain
    if head in TARGET_RUNNERS:
        if _RUNNER_INFO_FLAGS.intersection(words):   # make --version, task --list, rake -T
            return False
        return _target_verdict(_targets(head, words[1:]), rake=head == "rake")
    if head in ("npm", "pnpm", "yarn", "bun"):
        return _js_script_verdict(head, words)
    return _script_verdict(words)


def check_verdict(text: str) -> bool | None:
    """``is_check_command`` as three states (#920): None for an unrecognised target of a
    runner whose recipe is not read, so CI reads it unknown rather than absent."""
    words = command_words(text)
    return _check_verdict(words) if words else False
