"""Extract the shell commands an instruction file gives and check each names a real target.

An instruction file earns its keep by handing an agent exact commands: agents
run the tools a file names (Gloaguen et al., 2026, https://arxiv.org/html/2602.11988v1).
A command only helps if it works. ``npm run lint`` with no ``lint`` script, or
``make check`` with no ``check`` target, is a map an agent trusts and fails on.
This module finds the commands and checks each against the repository at HEAD,
without running anything. Public API:

- ``extract_commands(text) -> list[Command]``: every command segment in a
  shell-like fenced block (``bash``, ``sh``, ``shell``, ``zsh``, ``console``,
  or an unlabelled fence whose line starts with a known runner) and every
  inline code span that starts with a known runner (``RUNNERS``). A line is
  split on ``&&``, ``||``, ``;`` and ``|``; ``cd <dir>`` sets the directory
  later segments in the same block resolve paths against.
- ``resolve(command, repo_root=None, *, index=None) -> Resolution``: one
  verdict per command. ``resolved`` (the script, target, recipe, environment,
  session, file or tool config exists), ``missing`` (the runner's config is
  absent or lacks the named target: a finding), or ``unknown`` (a runner this
  module does not model, a placeholder, or a tool the repo does not declare:
  no evidence either way, never a failure). ``reason`` says which.
- ``build_index(repo_root)`` (re-exported from ``lib.command_index``) reads the
  repo's config once; pass the index to ``resolve`` when resolving many.

Runner coverage: npm / pnpm / yarn / bun (``run X``, shorthand ``pnpm X``,
``npm test``/``start``, built-ins), npx / bunx (declared packages), make,
just, uv / poetry (``run X``: a ``--with`` package, a declared dependency,
script or ``[tool.*]`` table, a file), uvx, tox (``-e`` environments), nox
(``-s`` sessions), pytest and ``python -m pytest`` (pytest configured, path
arguments exist), ``python X.py``, go (any ``go.mod``), cargo (``Cargo.toml``,
aliases), mvn / ``./mvnw`` (``pom.xml``), gradle / ``./gradlew`` (a Gradle
build file), ``bash X.sh`` and ``./X`` (the file exists). A command whose
runner is not modelled resolves when a CI configuration line runs it
verbatim; otherwise it is ``unknown``.

Leniency is deliberate: config files are unioned across the repo (a workspace
package's script resolves), and a computed Makefile target or tox brace
expansion makes the family accept any name. A false ``missing`` puts a wrong
finding in front of a reader; a false ``resolved`` earns the grader's per-command
credit (``COMMAND_POINTS``, 12) for a command that would fail, so built-in lists
stay exact. A path the repo keeps out of git by design (absolute, ``~`` or
``$VAR``-led, under an excluded tree such as ``.venv`` or ``node_modules``, or
gitignored) is ``unknown``, never ``missing``.
"""
from __future__ import annotations

import posixpath
import re
import shlex
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal

from lib.command_index import RepoIndex, TargetSet, build_index, is_gitignored
from lib.doc_graph import is_excluded_path

__all__ = [
    "Command", "Resolution", "RepoIndex", "RUNNERS",
    "build_index", "extract_commands", "resolve",
]

Verdict = Literal["resolved", "missing", "unknown"]


@dataclass(frozen=True)
class Command:
    """One command segment as written, with the 1-based line it sits on."""

    text: str
    line: int
    cwd: str = ""
    source: Literal["fenced", "inline"] = "fenced"


@dataclass(frozen=True)
class Resolution:
    command: str
    verdict: Verdict
    reason: str

    @property
    def resolved(self) -> bool:
        return self.verdict == "resolved"


SHELL_FENCE_LANGS = frozenset({
    "bash", "sh", "shell", "zsh", "console", "shell-session", "sh-session",
    "terminal", "shellsession", "fish",
})
_FENCE = re.compile(r"^\s*(`{3,}|~{3,})\s*([\w+-]*)")
_INLINE = re.compile(r"(?<!`)`([^`\n]+)`(?!`)")
_PLACEHOLDER = re.compile(r"<[^<>\s][^<>]*>|\{\{|\$\{|\$\(|…|\[[a-z_-]+\]")
_REDIRECT = re.compile(r"(?:^|\s)\d*(?:>>?|<)&?\s*\S+")
_ENV_ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_PREFIXES = frozenset({"sudo", "time", "env", "exec", "command", "nohup"})
_PROMPT = re.compile(r"^(?:\$|%|❯|>)\s+")


def _split_segments(line: str) -> list[str]:
    """Split a shell line on ``&&``, ``||``, ``;`` and ``|`` outside quotes."""
    segments: list[str] = []
    buf: list[str] = []
    quote = ""
    i = 0
    while i < len(line):
        ch = line[i]
        if quote:
            quote = "" if ch == quote else quote
        elif ch in "'\"":
            quote = ch
        elif ch in ";|&" and not _is_redirect_amp(line, i):
            segments.append("".join(buf))
            buf = []
            i += 2 if line[i:i + 2] in ("&&", "||") else 1
            continue
        buf.append(ch)
        i += 1
    segments.append("".join(buf))
    return [s.strip() for s in segments if s.strip()]


def _strip_subshell(segment: str) -> str:
    """``(cd x`` -> ``cd x`` and ``y)`` -> ``y``: the parens of a ``( ... )`` group."""
    if segment.startswith("(") and segment.count("(") > segment.count(")"):
        segment = segment[1:]
    if segment.endswith(")") and segment.count(")") > segment.count("("):
        segment = segment[:-1]
    return segment.strip()


def _is_redirect_amp(line: str, i: int) -> bool:
    """True for the ``&`` of ``2>&1`` or ``&>``, which joins streams, not commands."""
    return line[i] == "&" and ((i > 0 and line[i - 1] == ">") or line[i + 1:i + 2] == ">")


def _tokens(segment: str) -> list[str]:
    """Shell words of one segment with redirections, env assignments and prefixes dropped."""
    cleaned = _REDIRECT.sub(" ", segment)
    try:
        words = shlex.split(cleaned)
    except ValueError:
        words = cleaned.split()
    while words and (_ENV_ASSIGN.match(words[0]) or words[0] in _PREFIXES):
        words.pop(0)
    return words


def _cd_target(words: list[str], cwd: str) -> str | None:
    """New cwd for a ``cd``/``pushd`` segment, ``""`` when it leaves the repo."""
    if not words or words[0] not in ("cd", "pushd"):
        return None
    if len(words) < 2 or words[1].startswith(("/", "~", "$", "-")):
        return ""
    joined = posixpath.normpath(posixpath.join(cwd, words[1]))
    return "" if joined in (".", "") or joined.startswith("..") else joined


def _line_commands(line: str, lineno: int, cwd: str, source: Literal["fenced", "inline"],
                   require_runner: bool) -> tuple[list[Command], str]:
    """Commands on one line, and the cwd after it (a ``( cd x ... )`` group restores it)."""
    out: list[Command] = []
    saved = cwd
    for raw in _split_segments(line):
        segment = _strip_subshell(raw)
        if raw.startswith("("):
            saved = cwd
        words = _tokens(segment)
        new_cwd = _cd_target(words, cwd)
        if new_cwd is not None:
            cwd = new_cwd
        elif words and _counts_as_command(words, source, require_runner):
            out.append(Command(text=segment, line=lineno, cwd=cwd, source=source))
        if raw.endswith(")") and segment != raw:
            cwd = saved
    return out, cwd


def _counts_as_command(words: list[str], source: str, require_runner: bool) -> bool:
    """A runner-led segment; inline, a bare tool name (`` `npm` ``) is prose, not a command."""
    if require_runner and not _is_runner(words[0]):
        return False
    return source != "inline" or len(words) > 1 or _looks_executable(words[0])


_SCRIPT_SUFFIXES = frozenset({".sh", ".bash", ".zsh", ".py", ".js", ".mjs", ".cjs", ".ts", ".rb", ".pl"})


def _looks_executable(word: str) -> bool:
    """``./deploy`` or ``./run.sh`` alone is a command; ``./docs/guide.md`` is a file mention."""
    if not word.startswith("./"):
        return False
    suffix = PurePosixPath(word).suffix.lower()
    return suffix == "" or suffix in _SCRIPT_SUFFIXES


def _is_runner(word: str) -> bool:
    return word in RUNNERS or word.startswith("./")


def _strip_comment(line: str) -> tuple[str, bool]:
    """Drop a trailing `` # comment`` outside quotes; also report an unclosed quote."""
    quote = ""
    for i, ch in enumerate(line):
        if quote:
            quote = "" if ch == quote else quote
        elif ch in "'\"":
            quote = ch
        elif ch == "#" and (i == 0 or line[i - 1].isspace()):
            return line[:i].rstrip(), False
    return line, bool(quote)


def _fenced_lines(lines: list[str], start: int, lang: str) -> tuple[list[tuple[int, str]], int]:
    """Command lines of the fence opened at ``start``; returns them and the closing index.

    A line ending in ``\\`` or inside an open quote continues on the next line;
    the joined command carries the line it starts on.
    """
    out: list[tuple[int, str]] = []
    pending = ""
    first = 0
    i = start + 1
    while i < len(lines) and not _FENCE.match(lines[i]):
        raw = lines[i].strip()
        i += 1
        if not pending:
            if lang == "console" and not _PROMPT.match(raw):
                continue
            raw = _PROMPT.sub("", raw)
            first = i
        joined, open_quote = _strip_comment(pending + raw)
        if not joined:
            continue
        if open_quote:
            pending = joined + "\n"
        elif joined.endswith("\\"):
            pending = joined[:-1] + " "
        else:
            out.append((first, joined))
            pending = ""
    return out, i


def extract_commands(text: str) -> list[Command]:
    """Every command segment in shell fences and runner-led inline code spans."""
    lines = text.splitlines()
    commands: list[Command] = []
    i = 0
    while i < len(lines):
        fence = _FENCE.match(lines[i])
        if not fence:
            for match in _INLINE.finditer(lines[i]):
                found, _ = _line_commands(match.group(1), i + 1, "", "inline", True)
                commands.extend(found)
            i += 1
            continue
        lang = fence.group(2).lower()
        body, close = _fenced_lines(lines, i, lang)
        if lang in SHELL_FENCE_LANGS or lang == "":
            cwd = ""
            for lineno, line in body:
                found, cwd = _line_commands(line, lineno, cwd, "fenced", lang == "")
                commands.extend(found)
        i = close + 1
    return commands


# --- Resolution -------------------------------------------------------------

Outcome = tuple[Verdict, str]
_Resolver = Callable[[RepoIndex, list[str], str], Outcome]

_UNKNOWN_RUNNER: Outcome = ("unknown", "unresolved: unknown runner")


def _positionals(args: list[str], valued: frozenset[str]) -> list[str]:
    """Non-option words, skipping the value of each option in ``valued``."""
    out: list[str] = []
    skip = False
    for arg in args:
        if skip:
            skip = False
        elif arg == "--":
            break
        elif arg.startswith("-"):
            skip = arg in valued
        else:
            out.append(arg)
    return out


def _path_exists(index: RepoIndex, rel: str, cwd: str) -> bool:
    rel = rel[2:] if rel.startswith("./") else rel
    candidates = {posixpath.normpath(posixpath.join(cwd, rel)), posixpath.normpath(rel)}
    return any(index.has_path(c) for c in candidates)


def _outside_git_by_design(index: RepoIndex, rel: str, cwd: str) -> bool:
    """Absolute, home or variable paths, excluded trees and gitignored paths."""
    if rel.startswith(("/", "~", "$")):
        return True
    norm = posixpath.normpath(posixpath.join(cwd, rel[2:] if rel.startswith("./") else rel))
    return is_excluded_path(Path(norm)) or is_gitignored(index.root, norm)


def _file_outcome(index: RepoIndex, rel: str, cwd: str) -> Outcome:
    if _path_exists(index, rel, cwd):
        return "resolved", f"file `{rel}` exists"
    if _outside_git_by_design(index, rel, cwd):
        return "unknown", f"unresolved: `{rel}` is outside what git tracks by design"
    return "missing", f"no file `{rel}` at HEAD"


def _target(family: TargetSet | None, name: str, what: str, config: str) -> Outcome:
    if family is None:
        return "missing", f"no {config} in the repo"
    if family.has(name):
        return "resolved", f"{what} `{name}` in {config}"
    return "missing", f"no {what} `{name}` in {config}"


# npm/pnpm/yarn/bun subcommands that are the package manager's own, not a script.
_PM_BUILTINS = frozenset({
    "install", "i", "ci", "add", "remove", "rm", "uninstall", "un", "update", "up",
    "upgrade", "dlx", "exec", "x", "create", "init", "link", "unlink", "publish",
    "pack", "why", "outdated", "list", "ls", "audit", "config", "store", "info",
    "view", "global", "workspace", "workspaces", "patch", "rebuild", "import",
    "prune", "dedupe", "licenses", "version", "login", "logout", "cache", "bin",
    "root", "env", "help", "set", "plugin", "constraints", "node", "npm",
    "fund", "doctor", "explain", "pm", "self-update", "upgrade-interactive",
})
# pnpm's own commands that yarn and bun would run as a package.json script.
_PNPM_ONLY_BUILTINS = frozenset({"deploy", "setup"})
_PM_VALUED = frozenset({"--prefix", "-C", "--dir", "--cwd", "--filter", "-F", "--workspace", "-w"})
_NPM_TEST = frozenset({"test", "t", "tst"})


def _script(index: RepoIndex, name: str) -> Outcome:
    return _target(index.npm_scripts, name, "script", "package.json")


def _builtin(index: RepoIndex, tool: str) -> Outcome:
    if index.npm_scripts is None:
        return "unknown", f"`{tool}` built-in outside a JavaScript project"
    return "resolved", f"`{tool}` built-in"


def _npm(index: RepoIndex, words: list[str], cwd: str) -> Outcome:
    pos = _positionals(words[1:], _PM_VALUED)
    if not pos:
        return _builtin(index, "npm")
    sub = pos[0]
    if sub in ("run", "run-script", "rum", "urn"):
        return _script(index, pos[1]) if len(pos) > 1 else _builtin(index, "npm run")
    if sub in _NPM_TEST:
        return _script(index, "test")
    if sub in ("start", "stop", "restart"):
        if sub == "start" and _path_exists(index, "server.js", cwd):
            return "resolved", "`npm start` runs server.js"
        return _script(index, sub)
    return _builtin(index, "npm")


def _pnpm_like(index: RepoIndex, words: list[str], cwd: str) -> Outcome:
    tool = words[0]
    pos = _positionals(words[1:], _PM_VALUED)
    if not pos or pos[0] in _PM_BUILTINS or (tool == "pnpm" and pos[0] in _PNPM_ONLY_BUILTINS):
        return _builtin(index, tool)
    if pos[0] == "run":
        return _script(index, pos[1]) if len(pos) > 1 else _builtin(index, f"{tool} run")
    name = pos[0]
    if tool == "bun" and name == "build":
        return _builtin(index, "bun build")  # bun's bundler; pnpm/yarn `build` is a script
    if tool == "bun" and name == "test":
        if index.npm_scripts is not None and index.npm_scripts.has("test"):
            return "resolved", "script `test` in package.json"
        return _builtin(index, "bun test")
    if tool == "bun" and _path_exists(index, name, cwd):
        return "resolved", f"file `{name}` exists"
    return _script_or_bin(index, name)


def _script_or_bin(index: RepoIndex, name: str) -> Outcome:
    """``pnpm X`` / ``yarn X`` / ``bun X`` runs script X, else a dependency's binary X."""
    outcome = _script(index, name)
    if outcome[0] != "missing" or index.npm_scripts is None:
        return outcome
    if name in index.npm_packages:
        return "resolved", f"package `{name}` declared in package.json"
    if index.npm_packages:
        # A bin name can differ from its package (`typescript` ships `tsc`).
        return "unknown", f"`{name}` is no script; it may be a dependency's binary"
    return outcome


def _package_name(spec: str) -> str:
    """``eslint@9`` -> ``eslint``; ``@scope/pkg@1`` -> ``@scope/pkg``."""
    if spec.startswith("@"):
        return "@" + spec[1:].partition("@")[0]
    return spec.partition("@")[0]


def _npx(index: RepoIndex, words: list[str], cwd: str) -> Outcome:
    pos = _positionals(words[1:], frozenset({"-p", "--package"}))
    if not pos:
        return _UNKNOWN_RUNNER
    package = _package_name(pos[0])
    if package in index.npm_packages:
        return "resolved", f"package `{package}` declared in package.json"
    return "unknown", f"`{package}` is not declared in package.json"


_MAKE_VALUED = frozenset({"-C", "-f", "--file", "--makefile", "--directory", "-I", "-o", "-W", "-j"})


def _make(index: RepoIndex, words: list[str], cwd: str) -> Outcome:
    pos = [p for p in _positionals(words[1:], _MAKE_VALUED) if "=" not in p and not p.isdigit()]
    if index.make_targets is None:
        return "missing", "no Makefile in the repo"
    for name in pos:
        outcome = _target(index.make_targets, name, "target", "a Makefile")
        if outcome[0] != "resolved":
            return outcome
    if pos:
        return "resolved", f"target `{pos[0]}` in a Makefile"
    if index.make_targets.names or index.make_targets.lenient:
        return "resolved", "default Makefile target"
    return "missing", "Makefile defines no target"


def _just(index: RepoIndex, words: list[str], cwd: str) -> Outcome:
    if index.just_recipes is None:
        return "missing", "no justfile in the repo"
    args = words[1:]
    if any(a in ("--list", "-l", "--summary", "--choose", "--evaluate") for a in args):
        return "resolved", "`just` built-in"
    pos = _positionals(args, frozenset({"-f", "--justfile", "-d", "--working-directory", "--set"}))
    if not pos:
        return "resolved", "default justfile recipe"
    return _target(index.just_recipes, pos[0], "recipe", "a justfile")


_UV_VALUED = frozenset({
    "--with", "--with-editable", "--with-requirements", "--project", "--directory",
    "--python", "-p", "--package", "--extra", "--group", "--env-file", "--index",
    "--index-url", "--extra-index-url", "--from", "--only-group", "--no-group",
})
_PY_BUILTIN_SUBS = frozenset({
    "sync", "lock", "add", "remove", "pip", "venv", "tree", "export", "build",
    "publish", "init", "version", "format", "install", "update", "shell", "check",
    "show", "env", "new", "config", "self", "cache", "python", "help", "list",
})


def _with_packages(args: list[str]) -> set[str]:
    """Package names a ``--with``/``--from`` option provides for this one run."""
    names: set[str] = set()
    for i, arg in enumerate(args):
        value = None
        if arg in ("--with", "--from") and i + 1 < len(args):
            value = args[i + 1]
        elif arg.startswith(("--with=", "--from=")):
            value = arg.split("=", 1)[1]
        if value:
            for spec in value.split(","):
                match = re.match(r"[A-Za-z0-9][A-Za-z0-9._-]*", spec.strip())
                if match:
                    names.add(match.group(0).lower())
    return names


def _python_target(index: RepoIndex, run_args: list[str], cwd: str, provided: set[str]) -> Outcome:
    """Resolve what ``uv run`` / ``poetry run`` would execute, from the words after ``run``."""
    pos = _positionals(run_args, _UV_VALUED)
    if not pos:
        return _UNKNOWN_RUNNER
    name = pos[0]
    rest = run_args[run_args.index(name) + 1:]  # the target's own args, flags kept
    lowered = name.lower()
    if lowered in provided:
        return "resolved", f"`{name}` provided by --with"
    if "/" in name or name.endswith((".py", ".sh")):
        return _file_outcome(index, name, cwd)
    if lowered in ("python", "python3"):
        return _python(index, [name, *rest], cwd)
    if lowered in ("pytest", "py.test"):
        return _pytest_outcome(index, rest, cwd)
    if lowered in index.python_names:
        return "resolved", f"`{name}` declared in pyproject.toml"
    return "unknown", f"`{name}` is not declared in pyproject.toml"


def _uv(index: RepoIndex, words: list[str], cwd: str) -> Outcome:
    if words[0] == "uvx":
        return _uvx(index, words[1:])
    pos = _positionals(words[1:], _UV_VALUED)
    if not pos:
        return _UNKNOWN_RUNNER
    if pos[0] == "tool" and pos[1:2] == ["run"]:
        return _uvx(index, words[words.index("run") + 1:])
    if pos[0] == "run":
        run_args = words[words.index("run") + 1:]
        target = _positionals(run_args, _UV_VALUED)
        # uv's own -m/--module sits before the target; after it, -m is the
        # target's flag (`uv run pytest -m slow` selects a marker).
        head = run_args[:run_args.index(target[0])] if target else run_args
        if "-m" in head or "--module" in head:
            return _python(index, ["python", *run_args], cwd)
        return _python_target(index, run_args, cwd, _with_packages(run_args))
    if pos[0] in _PY_BUILTIN_SUBS:
        return ("resolved", "`uv` built-in") if index.has_pyproject else \
            ("unknown", "`uv` built-in outside a Python project")
    return _UNKNOWN_RUNNER


def _uvx(index: RepoIndex, args: list[str]) -> Outcome:
    pos = _positionals(args, _UV_VALUED)
    if pos and pos[0].lower() in index.python_names:
        return "resolved", f"`{pos[0]}` declared in pyproject.toml"
    return "unknown", "runs a tool the repo does not declare"


def _poetry(index: RepoIndex, words: list[str], cwd: str) -> Outcome:
    pos = _positionals(words[1:], frozenset({"-C", "--directory", "-P", "--project"}))
    if pos[:1] == ["run"] and len(pos) > 1:
        return _python_target(index, words[words.index("run") + 1:], cwd, set())
    if not index.has_pyproject:
        return "missing", "no pyproject.toml in the repo"
    return "resolved", "`poetry` built-in"


_TOX_DEFAULT_ENV = re.compile(r"^py(?:\d+(?:\.\d+)?|py\d*)?$")


def _tox(index: RepoIndex, words: list[str], cwd: str) -> Outcome:
    if index.tox_envs is None:
        return "missing", "no tox configuration in the repo"
    envs: list[str] = []
    args = words[1:]
    for i, arg in enumerate(args):
        if arg in ("-e", "--env") and i + 1 < len(args):
            envs.extend(args[i + 1].split(","))
        elif arg.startswith(("-e=", "--env=")):
            envs.extend(arg.split("=", 1)[1].split(","))
    for env in (e.strip() for e in envs):
        if env and env != "ALL" and not _TOX_DEFAULT_ENV.match(env) and not index.tox_envs.has(env):
            return "missing", f"no tox environment `{env}`"
    return "resolved", "tox configuration present"


def _nox(index: RepoIndex, words: list[str], cwd: str) -> Outcome:
    if index.nox_sessions is None:
        return "missing", "no noxfile.py in the repo"
    args = words[1:]
    sessions: list[str] = []
    collecting = False
    for arg in args:
        if arg in ("-s", "--session", "--sessions", "-e"):
            collecting = True
        elif arg.startswith("-"):
            collecting = False
        elif collecting:
            sessions.append(arg)
    for session in sessions:
        base = re.split(r"[-(]", session, maxsplit=1)[0]
        if not (index.nox_sessions.has(session) or index.nox_sessions.has(base)):
            return "missing", f"no nox session `{session}`"
    return "resolved", "noxfile.py present"


def _pytest_outcome(index: RepoIndex, args: list[str], cwd: str) -> Outcome:
    if not index.pytest_present:
        return "missing", "no pytest configuration, dependency or test file"
    valued = frozenset({"-k", "-m", "-c", "-p", "-o", "--rootdir", "--cov", "-n", "--maxfail"})
    for arg in _positionals(args, valued):
        path = arg.split("::", 1)[0]
        if "/" in path or path.endswith(".py"):
            outcome = _file_outcome(index, path, cwd)
            if outcome[0] != "resolved":
                return outcome
    return "resolved", "pytest configured"


def _pytest(index: RepoIndex, words: list[str], cwd: str) -> Outcome:
    return _pytest_outcome(index, words[1:], cwd)


def _python(index: RepoIndex, words: list[str], cwd: str) -> Outcome:
    args = words[1:]
    if "-c" in args:
        return _UNKNOWN_RUNNER  # an inline program, not a path
    if "-m" in args:
        rest = args[args.index("-m") + 1:]
        if not rest:
            return _UNKNOWN_RUNNER
        module = rest[0]
        if module in ("pytest", "py.test"):
            return _pytest_outcome(index, rest[1:], cwd)
        as_path = module.replace(".", "/")
        if _path_exists(index, as_path + ".py", cwd) or _path_exists(index, as_path, cwd):
            return "resolved", f"module `{module}` exists"
        if module.lower() in index.python_names:
            return "resolved", f"`{module}` declared in pyproject.toml"
        return "unknown", f"module `{module}` is not in the repo"
    pos = _positionals(args, frozenset({"-W", "-X"}))
    if pos and (pos[0].endswith(".py") or "/" in pos[0]):
        return _file_outcome(index, pos[0], cwd)
    return _UNKNOWN_RUNNER


_GO_BUILD = frozenset({"test", "vet", "build", "run", "generate"})


def _go(index: RepoIndex, words: list[str], cwd: str) -> Outcome:
    sub = words[1] if len(words) > 1 else ""
    if sub == "install" and any("@" in w for w in words[2:]):
        return "unknown", "installs a tool"
    if index.has_basename("go.mod"):
        return "resolved", "go.mod present"
    if sub in _GO_BUILD:
        return "missing", "no go.mod in the repo"
    return _UNKNOWN_RUNNER


_CARGO_BUILTINS = frozenset({
    "build", "b", "check", "c", "test", "t", "run", "r", "clippy", "fmt", "bench",
    "doc", "d", "clean", "update", "fetch", "tree", "metadata", "publish", "package",
})


def _cargo(index: RepoIndex, words: list[str], cwd: str) -> Outcome:
    pos = _positionals(words[1:], frozenset({"-p", "--package", "--manifest-path", "--features", "-F"}))
    sub = pos[0] if pos else ""
    if sub == "install":
        return "unknown", "installs a tool"
    has_manifest = index.has_basename("Cargo.toml")
    if sub in _CARGO_BUILTINS:
        return ("resolved", "Cargo.toml present") if has_manifest else ("missing", "no Cargo.toml in the repo")
    return _UNKNOWN_RUNNER


def _maven(index: RepoIndex, words: list[str], cwd: str) -> Outcome:
    if words[0].startswith("./") and not _path_exists(index, words[0], cwd):
        return "missing", f"no file `{words[0]}` at HEAD"
    if index.has_basename("pom.xml"):
        return "resolved", "pom.xml present"
    return "missing", "no pom.xml in the repo"


_GRADLE_FILES = ("build.gradle", "build.gradle.kts", "settings.gradle", "settings.gradle.kts")


def _gradle(index: RepoIndex, words: list[str], cwd: str) -> Outcome:
    if words[0].startswith("./") and not _path_exists(index, words[0], cwd):
        return "missing", f"no file `{words[0]}` at HEAD"
    if any(index.has_basename(name) for name in _GRADLE_FILES):
        return "resolved", "Gradle build file present"
    return "missing", "no Gradle build file in the repo"


def _shell(index: RepoIndex, words: list[str], cwd: str) -> Outcome:
    pos = _positionals(words[1:], frozenset({"-o"}))
    if "-c" in words[1:] or not pos:
        return _UNKNOWN_RUNNER
    return _file_outcome(index, pos[0], cwd)


RUNNERS: dict[str, _Resolver] = {
    "npm": _npm, "pnpm": _pnpm_like, "yarn": _pnpm_like, "bun": _pnpm_like,
    "npx": _npx, "bunx": _npx,
    "make": _make, "gmake": _make, "just": _just,
    "uv": _uv, "uvx": _uv, "poetry": _poetry, "tox": _tox, "nox": _nox,
    "pytest": _pytest, "py.test": _pytest, "python": _python, "python3": _python,
    "go": _go, "cargo": _cargo,
    "mvn": _maven, "./mvnw": _maven, "gradle": _gradle, "./gradlew": _gradle,
    "bash": _shell, "sh": _shell, "zsh": _shell, "source": _shell,
}


def _ci_runs(index: RepoIndex, text: str) -> bool:
    needle = " ".join(text.split())
    return len(needle) > 3 and any(needle in line for line in index.ci_lines)


def resolve(command: Command | str, repo_root: Path | None = None, *,
            index: RepoIndex | None = None) -> Resolution:
    """Check that ``command`` names a target the repository defines at HEAD.

    Pass ``index`` (from ``build_index``) when resolving many commands; with
    only ``repo_root`` the index is built for this one call.
    """
    cmd = command if isinstance(command, Command) else Command(text=command, line=0)
    if index is None:
        if repo_root is None:
            raise ValueError("resolve needs repo_root or index")
        index = build_index(repo_root)
    if _PLACEHOLDER.search(cmd.text):
        return Resolution(cmd.text, "unknown", "unresolved: contains a placeholder")
    words = _tokens(cmd.text)
    if not words:
        return Resolution(cmd.text, "unknown", "unresolved: empty command")
    head = words[0]
    resolver = RUNNERS.get(head)
    if resolver is not None:
        verdict, reason = resolver(index, words, cmd.cwd)
    elif "/" in head:
        verdict, reason = _file_outcome(index, head, cmd.cwd)
    else:
        verdict, reason = _UNKNOWN_RUNNER
    if verdict == "unknown" and _ci_runs(index, cmd.text):
        verdict, reason = "resolved", "a CI configuration step runs it"
    return Resolution(cmd.text, verdict, reason)
