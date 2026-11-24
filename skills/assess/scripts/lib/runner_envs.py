"""What ``tox -e <env>`` and ``nox -s <session>`` run, read from the checkout (issue #1180).

``tox -e lint`` on a CI line read as a target whose recipe is not read, so ``lint_runs`` stayed
null (``check_target``) even where the environment runs ``ruff check``. Under
``reads_runner_envs`` (a decorator on a scan whose first argument is the repository root:
``ci_checks.scan_ci_checks``), ``env_commands(words)`` gives the commands the named
environments or sessions run, and ``check_command.is_lint_run`` reads each the way it reads a
CI line, as it reads a pdm script (``lib.runner_args``). Only what is certain is returned; a
command or environment that is not read gives nothing, so the line keeps its null reading:

- **tox**: ``tox [run|r] -e a,b`` (``--env``, ``-ea``) with the one tox configuration at the
  root, ``tox.ini`` (``[testenv:<name>]``) or ``tox.toml`` (``[env.<name>]``). Not read: a
  second tox configuration anywhere in the tree (a ``cd`` may pick it), ``setup.cfg`` and
  ``pyproject.toml`` forms, ``-c`` / ``--conf`` / ``--root``, ``-m``, ``ALL``, positional
  arguments after ``--``, an environment without its own ``commands`` (it inherits) or with a
  ``base``, one whose ``ignore_outcome`` (its own, else ``[testenv]`` / ``env_run_base``'s) is
  not false, a factor-conditional line (``py311: pytest``) and a substitution
  other than ``{posargs}`` (``{posargs:x}`` reads ``x``). A command prefixed ``-`` or ``!``
  cannot fail the run, so it is not returned.
- **nox**: ``nox -s a b`` (``--session``, ``--sessions``, ``-e``) with the one
  ``noxfile.py``, at the root, parsed with ``ast``. A session is a function decorated
  ``@nox.session`` (its ``name=`` when given); its commands are the ``session.run(...)`` calls
  that are statements of the function body itself, every argument a string literal
  (``*session.posargs`` is empty without ``--``). A call with ``success_codes=``, under an
  ``if`` or loop, or with a computed argument is not returned. Not read: ``-k``, ``-t``,
  ``-f``, a parametrized name (``lint-3.12``), positional arguments after ``--``.

A command that itself runs tox or nox is not returned (no loop). Inward-only imports: stdlib
and ``lib.command_index``. Tests: ``tests/test_runner_envs.py``.
"""
from __future__ import annotations

import ast
import configparser
import functools
import re
import shlex
import tomllib
from collections.abc import Callable, Sequence
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import ParamSpec, TypeVar

from lib.command_index import read_config, repo_files

_TOX_CONFIGS = frozenset({"tox.ini", "tox.toml"})
_TOX_RUN = frozenset({"run", "r"})
_TOX_UNREAD = frozenset({"-c", "--conf", "--root", "-m", "--workdir"})
_NOX_SESSION_FLAGS = frozenset({"-s", "-e", "--session", "--sessions"})
_NOX_UNREAD = frozenset({"-k", "--keywords", "-t", "--tags", "-f", "--noxfile"})
_FACTOR = re.compile(r"^[\w.,!{}-]+:\s")
_POSARGS = re.compile(r"\{posargs(?::([^{}]*))?\}")
_RUNNERS = frozenset({"tox", "nox"})


@dataclass(frozen=True)
class RunnerEnvs:
    """Each readable tox environment's and nox session's commands; None: not read."""

    tox: dict[str, tuple[str, ...]] | None
    nox: dict[str, tuple[str, ...]] | None


_ENVS: ContextVar[RunnerEnvs | None] = ContextVar("runner_envs", default=None)


def _kept(command: str) -> bool:
    words = command.split()
    return bool(words) and PurePosixPath(words[0]).name not in _RUNNERS


def _ini_commands(value: str) -> tuple[str, ...]:
    out: list[str] = []
    for line in value.replace("\\\n", " ").splitlines():
        line = line.strip()
        if not line or line.startswith(("#", "-", "!")) or _FACTOR.match(line):
            continue
        line = _POSARGS.sub(lambda m: m.group(1) or "", line)
        if "{" not in line and _kept(line):
            out.append(line)
    return tuple(out)


def _tox_ini(text: str) -> dict[str, tuple[str, ...]]:
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    try:
        parser.read_string(text)
    except configparser.Error:
        return {}
    envs: dict[str, tuple[str, ...]] = {}
    base = parser["testenv"] if parser.has_section("testenv") else None
    for section in parser.sections():
        name = section.removeprefix("testenv:")
        own = parser[section]
        if name == section or "{" in name or "commands" not in own or "base" in own:
            continue
        # [testenv] settings apply to every environment that does not set its own.
        outcome = own.get("ignore_outcome", base.get("ignore_outcome", "") if base is not None else "")
        if outcome.strip().lower() not in ("", "false"):
            continue
        envs[name] = _ini_commands(parser[section]["commands"])
    return envs


def _tox_toml(text: str) -> dict[str, tuple[str, ...]]:
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return {}
    envs: dict[str, tuple[str, ...]] = {}
    table = data.get("env")
    run_base = data.get("env_run_base")
    base = run_base if isinstance(run_base, dict) else {}
    for name, env in (table.items() if isinstance(table, dict) else ()):
        commands = env.get("commands") if isinstance(env, dict) else None
        if not isinstance(commands, list) or "base" in env or env.get(
                "ignore_outcome", base.get("ignore_outcome", False)) is not False:
            continue
        envs[name] = tuple(
            text for argv in commands
            if isinstance(argv, list) and argv and all(isinstance(a, str) for a in argv)
            and not argv[0].startswith(("-", "!")) and _kept(text := shlex.join(argv)))
    return envs


def _session_name(func: ast.FunctionDef) -> str | None:
    """The session name of a ``@nox.session`` function, or None when it is not one."""
    for deco in func.decorator_list:
        call = deco if isinstance(deco, ast.Call) else None
        target = call.func if call is not None else deco
        if not (isinstance(target, ast.Attribute) and target.attr == "session"
                and isinstance(target.value, ast.Name) and target.value.id == "nox"):
            continue
        for kw in call.keywords if call is not None else ():
            if kw.arg == "name":
                value = kw.value
                return value.value if isinstance(value, ast.Constant) and isinstance(value.value, str) else None
        return func.name
    return None


def _run_command(stmt: ast.stmt, param: str) -> str | None:
    """``session.run("ruff", "check", ".")`` as a command, or None when not read whole."""
    if not (isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call)):
        return None
    call = stmt.value
    if not (isinstance(call.func, ast.Attribute) and call.func.attr == "run"
            and isinstance(call.func.value, ast.Name) and call.func.value.id == param):
        return None
    if any(kw.arg in ("success_codes", None) for kw in call.keywords):
        return None
    words: list[str] = []
    for arg in call.args:
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            words.append(arg.value)
        elif not (isinstance(arg, ast.Starred) and ast.unparse(arg.value) == f"{param}.posargs"):
            return None
    return shlex.join(words) if words else None


def _noxfile(text: str) -> dict[str, tuple[str, ...]]:
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return {}
    sessions: dict[str, tuple[str, ...]] = {}
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef) or not node.args.args:
            continue
        name = _session_name(node)
        if name is None:
            continue
        param = node.args.args[0].arg
        sessions[name] = tuple(c for stmt in node.body
                               if (c := _run_command(stmt, param)) is not None and _kept(c))
    return sessions


def read_envs(root: Path) -> RunnerEnvs:
    """``root``'s tox environments and nox sessions (module docstring)."""
    files = repo_files(root)
    tox_files = [f for f in files if PurePosixPath(f).name in _TOX_CONFIGS]
    other = any(marker in read_config(root, f) for f in ("setup.cfg", "pyproject.toml")
                if f in files for marker in ("[tox:tox]", "[tool.tox"))
    tox: dict[str, tuple[str, ...]] | None = None
    if len(tox_files) == 1 and tox_files[0] in _TOX_CONFIGS and not other:
        text = read_config(root, tox_files[0])
        tox = _tox_ini(text) if tox_files[0] == "tox.ini" else _tox_toml(text)
    noxfiles = [f for f in files if PurePosixPath(f).name == "noxfile.py"]
    nox = _noxfile(read_config(root, "noxfile.py")) if noxfiles == ["noxfile.py"] else None
    return RunnerEnvs(tox, nox)


def _tox_names(args: Sequence[str]) -> list[str] | None:
    if args[:1] and not args[0].startswith("-"):
        if args[0] not in _TOX_RUN:
            return None
        args = args[1:]
    names: list[str] = []
    words = iter(args)
    for word in words:
        if word in _TOX_UNREAD or word == "--":
            return None
        if word in ("-e", "--env"):
            names += next(words, "").split(",")
        elif word.startswith("--env="):
            names += word.partition("=")[2].split(",")
        elif word.startswith("-e") and len(word) > 2:
            names += word[2:].split(",")
    return names if names and "ALL" not in names else None


def _nox_names(args: Sequence[str]) -> list[str] | None:
    names: list[str] = []
    collecting = False
    for word in args:
        if word in _NOX_UNREAD or word == "--" or word.startswith(("--noxfile=", "--keywords=")):
            return None
        if word in _NOX_SESSION_FLAGS:
            collecting = True
        elif word.startswith("-"):
            collecting = False
        elif collecting:
            names.append(word)
    return names or None


def env_commands(words: Sequence[str]) -> tuple[str, ...] | None:
    """The commands ``tox -e`` / ``nox -s`` runs, from the environments read under
    ``reads_runner_envs``; None for another command, outside a scan, or when no named
    environment is read."""
    envs = _ENVS.get()
    if envs is None or not words:
        return None
    head = PurePosixPath(words[0]).name
    table = envs.tox if head == "tox" else envs.nox if head == "nox" else None
    names = (_tox_names if head == "tox" else _nox_names)(words[1:]) if table is not None else None
    if table is None or names is None:
        return None
    read = [table[n] for n in names if n in table]
    return tuple(c for commands in read for c in commands) if read else None


P = ParamSpec("P")
R = TypeVar("R")


def reads_runner_envs(scan: Callable[P, R]) -> Callable[P, R]:
    """Run ``scan`` (first argument: the repository root) with its tox and nox runs read."""
    @functools.wraps(scan)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        root = args[0] if args else kwargs.get("repo_root")
        if not isinstance(root, (Path, str)):
            return scan(*args, **kwargs)
        token = _ENVS.set(read_envs(Path(root)))
        try:
            return scan(*args, **kwargs)
        finally:
            _ENVS.reset(token)
    return wrapper
