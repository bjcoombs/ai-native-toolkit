"""Is a shell command one an agent runs to check its work? (issues #514, #698)

``is_check_command`` classifies one command by the target it runs: a test or
check target of a runner ``lib.command_resolver`` models (``make test``,
``npm run ci``, ``tox``, ``uv run pytest``, the program after ``uv run``) or a
committed script named for a check (``./scripts/test.sh``). An install command
such as ``npm ci`` or ``uv sync --extra test`` is never a check.
``lib.agent_environment`` applies it to the commands instruction files name,
``lib.ci_checks`` to the lines of CI configuration. Pure: no file or network
access, so a scan that only classifies commands stays offline.
"""
from __future__ import annotations

import re
from pathlib import PurePosixPath

from lib.command_resolver import RUNNERS, command_words

CHECK_WORDS = frozenset({
    "test", "tests", "check", "checks", "verify", "ci", "validate", "pytest", "tox", "nox",
})
_ALWAYS_CHECK = frozenset({"pytest", "py.test", "tox", "nox"})
_PM_TEST = frozenset({"test", "t", "tst"})
_PM_INSTALL = frozenset({
    "install", "i", "add", "remove", "rm", "update", "up", "upgrade", "dlx", "exec",
    "create", "init", "link", "publish", "setup", "x",
})
_JS_TEST_RUNNERS = frozenset({"jest", "vitest", "mocha", "ava", "jasmine", "cypress"})
_VALUED_FLAGS = frozenset({
    "-C", "-f", "--file", "--makefile", "--directory", "--dir", "--cwd", "--prefix", "--project",
    "--with", "--extra", "--group", "--only-group", "--python", "-p", "--package", "--from",
    "--filter", "-F", "--workspace", "-w", "-e", "-s", "--manifest-path",
})


def is_check_name(name: str) -> bool:
    """A target or script name built from a check word (``test``, ``check:all``, ``ci``)."""
    return any(part in CHECK_WORDS for part in re.split(r"[:_./-]", name.lower()))


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


def _js_check(head: str, pos: list[str]) -> bool:
    """A package-manager or ``npx`` / ``bunx`` command that runs a check."""
    if not pos:
        return False
    if head in ("npx", "bunx"):
        if pos[0] == "playwright":
            return pos[1:2] == ["test"]
        return pos[0] in _JS_TEST_RUNNERS or is_check_name(pos[0])
    if pos[0] in ("run", "run-script"):
        return len(pos) > 1 and is_check_name(pos[1])
    # npm runs only its test aliases without `run`; `npm ci` installs.
    # `bun ci` is a frozen install; in pnpm and yarn `ci` is a script name.
    if head == "npm" or pos[0] in _PM_INSTALL or (head == "bun" and pos[0] == "ci"):
        return pos[0] in _PM_TEST
    # pnpm, yarn and bun run a package script named as the subcommand.
    return pos[0] in _PM_TEST or is_check_name(pos[0])


def _check_target(words: list[str]) -> bool:
    """Whether the program, subcommand or target a command runs is a check."""
    head, pos = words[0], _positionals(words[1:])
    if head in _ALWAYS_CHECK:
        return True
    if head in ("uv", "poetry", "uvx"):
        rest = words[1:]
        if head != "uvx":
            if not pos or pos[0] != "run":
                return False  # sync, install, add: setup, not a check
            rest = rest[rest.index("run") + 1:]
        program = _drop_leading_flags(rest)
        return bool(program) and _check_target(program)
    if head in ("npx", "bunx", "npm", "pnpm", "yarn", "bun"):
        return _js_check(head, pos)
    if head in ("python", "python3"):
        if "-m" in words:
            module = words[words.index("-m") + 1:words.index("-m") + 2]
            return bool(module) and module[0] in _ALWAYS_CHECK
        return bool(pos) and is_check_name(PurePosixPath(pos[0]).name)
    if head in ("bash", "sh", "zsh", "source"):
        return bool(pos) and is_check_name(PurePosixPath(pos[0]).name)
    if head.startswith("./") and head not in RUNNERS:
        return is_check_name(PurePosixPath(head).name)
    # make, just, go, cargo, mvn, gradle: the first target or goal names the work.
    return bool(pos) and is_check_name(pos[0])


def is_check_command(text: str) -> bool:
    """A command an agent runs to check its work: a check target of a modelled runner or script."""
    words = command_words(text)
    if not words:
        return False
    head = words[0]
    if head not in RUNNERS and not head.startswith("./"):
        return False
    return _check_target(words)
