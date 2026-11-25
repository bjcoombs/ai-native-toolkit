"""How a shell and make read their own options, for ``lib.check_command`` (issue #955).

``shell_args(words)`` is the script path and arguments a shell runs after its own flags:
``bash -e scripts/test.sh`` runs ``scripts/test.sh`` (``-e`` takes no value), ``bash -o
pipefail x.sh`` and ``bash --rcfile f x.sh`` skip the option's value, and a short cluster
ending in ``o`` / ``O`` takes one too (``-eo pipefail``). It is None for ``bash -c '...'``:
only a single-dash cluster holding ``c`` is one, so ``--norc`` is not.
``shell_string(words)`` is the command a ``-c`` string runs when it is one plain command
(``bash -c "make test"`` -> ``make test``), else ``[]``: a list or pipeline (``a && b``,
``pytest || true``, which CI masking cannot see inside the quotes), a redirection, a ``$(...)``
or backtick substitution, or a string that cannot be split,
which ``check_command`` reads as a command it does not read (null), never false.
``make_args(args)`` drops make's option values: ``-o``, ``-W``, ``-E`` and their long forms
always take one, and ``-j`` / ``-l`` take the next word only when it is a number (``make -j 4
build`` runs ``build``; ``make -j build`` also runs ``build``, with unlimited jobs).
``make_runs_no_recipe(words)`` is a make run whose recipes cannot fail it: a dry run (``-n``,
``--dry-run``, ``--just-print``, ``--recon``, ``-t`` / ``--touch``, ``-q`` / ``--question``,
alone or in a cluster such as ``-kn``) or ``-i`` / ``--ignore-errors``; ``-k`` still exits 2,
so it is not one. A cluster is read left to right up to the first letter that takes a value
(``C``, ``f``, ``I``, ``o``, ``O``, ``W``, ``E``, ``N``, or ``j`` / ``l`` before digits), so ``-Ctests``,
``-Iinclude`` and ``-kCtests`` run the target. Each rule
was checked against bash 3.2 and 5.3, dash and GNU make. ``maven_skips_tests(words)`` is a Maven
run with ``-DskipTests`` or ``-Dmaven.test.skip`` (not ``=false``) and lifecycle phases only
(``mvn -DskipTests verify``; an option's value such as ``-pl :mod`` is not a goal, and a plugin
goal is ``prefix:goal``): ``lib.ci.ci_checks`` reads it as a target it cannot settle (#799).
Tests: ``tests/test_check_command.py``.
"""
from __future__ import annotations

import re
import shlex
from pathlib import PurePosixPath

from lib.command_resolver import command_words

SHELLS = frozenset({"bash", "sh", "zsh", "dash"})
# Shell options that take the next word (``bash -o pipefail x.sh``, ``bash --rcfile f x.sh``);
# a short cluster ending in ``o`` / ``O`` does too (``-eo pipefail``). ``-e`` takes none (#955).
_SHELL_VALUED = frozenset({"--rcfile", "--init-file"})


def _shell_flags_end(words: list[str]) -> tuple[int, bool]:
    """Where a shell's own flags end, and whether one of them is a ``-c`` cluster (single dash
    only: ``--norc`` and ``--rcfile`` are not ``-c``)."""
    i, command = 1, False
    while i < len(words) and words[i][:1] in ("-", "+") and len(words[i]) > 1 and words[i] != "--":
        flag = words[i]
        command = command or (flag[0] == "-" and not flag.startswith("--") and "c" in flag[1:])
        valued = flag in _SHELL_VALUED or (not flag.startswith("--") and flag[-1] in "oO")
        i += 2 if valued else 1
    return i + (1 if words[i:i + 1] == ["--"] else 0), command


def shell_args(words: list[str]) -> list[str] | None:
    """A shell's script path and arguments after its own flags (``bash -e x.sh`` -> ``x.sh``),
    or None for ``bash -c '...'``, whose program is a string (``shell_string``)."""
    end, command = _shell_flags_end(words)
    return None if command else words[end:]


def shell_string(words: list[str]) -> list[str]:
    """The words of ``bash -c "<cmd>"``'s string when it is one plain command (``make test``),
    else ``[]``: a list or pipeline (``a && b``, ``pytest || true``), a redirection, a substitution, or a
    string that cannot be split reads as a command this scan does not read (null)."""
    end, _ = _shell_flags_end(words)
    string = words[end] if end < len(words) else ""
    try:
        lexer = shlex.shlex(string, posix=True, punctuation_chars=True)
        tokens = list(lexer)
    except ValueError:
        return []
    if not tokens or "$(" in string or "`" in string or any(set(t) <= set("&|;()<>") for t in tokens):
        return []
    return command_words(string)


# GNU make options that always take the next word, and the two whose number is optional
# (``make -j test`` runs ``test`` with unlimited jobs; ``make -j 4 test`` runs it with four).
_MAKE_VALUED = frozenset({"-o", "-W", "--old-file", "--assume-old", "--what-if", "--new-file",
                          "--assume-new", "-E", "--eval"})
_MAKE_OPTIONAL_NUMBER = frozenset({"-j", "--jobs", "-l", "--load-average", "--max-load"})


# make options under which no recipe's failure fails the run: a dry run prints or touches
# instead of running (``-n``, ``-t``, ``-q``), ``-i`` ignores every error. ``-k`` keeps going
# but still exits 2, so it is not one.
_MAKE_NO_RECIPE = frozenset({"--dry-run", "--just-print", "--recon", "--touch", "--question",
                             "--ignore-errors"})
_MAKE_NO_RECIPE_SHORT = frozenset("ntqi")


def make_runs_no_recipe(words: list[str]) -> bool:
    """``make -n test``, ``make --dry-run test``, ``make -kn test``, ``make -i test``: real make
    exits 0 with a failing recipe (or, for ``-q``, without running one), so no check gates."""
    if words[:1] not in (["make"], ["gmake"]):
        return False
    return any(w in _MAKE_NO_RECIPE or _cluster_letters(w) & _MAKE_NO_RECIPE_SHORT
               for w in make_args(words[1:]))


# Short make options whose value may be attached (``-Ctests``, ``-Iinclude``, ``-otarget``);
# ``-j`` / ``-l`` take attached digits (``-j4``).
_MAKE_VALUE_LETTERS = frozenset("CfIoOWEN")   # O: --output-sync[=TYPE], attached only (make 4)


def _cluster_letters(word: str) -> set[str]:
    """The option letters of one short cluster, left to right up to the first that takes a
    value: ``-kn`` -> k, n; ``-Ctests`` -> C (``tests`` is its value); ``-j4n`` -> j."""
    if word[:1] != "-" or word[1:2] in ("-", ""):
        return set()
    letters: set[str] = set()
    for k, letter in enumerate(word[1:], start=1):
        letters.add(letter)
        if letter in _MAKE_VALUE_LETTERS or (letter in "jl" and word[k + 1:k + 2].isdigit()):
            break
    return letters


def make_args(args: list[str]) -> list[str]:
    """``args`` with make's option values dropped (``-j 4``, ``-l 2.5``, ``-o file``)."""
    out: list[str] = []
    i = 0
    while i < len(args):
        word = args[i]
        nxt = args[i + 1] if i + 1 < len(args) else ""
        if word in _MAKE_VALUED or (word in _MAKE_OPTIONAL_NUMBER and re.fullmatch(r"\d+(\.\d+)?", nxt)):
            i += 2
            continue
        out.append(word)
        i += 1
    return out


# Maven properties that skip the test run: ``-DskipTests`` (compiles, skips running) and
# ``-Dmaven.test.skip`` (skips compiling too). ``-DskipITs`` still runs the unit tests.
_MAVEN_SKIP = frozenset({"skipTests", "maven.test.skip"})


def maven_skips_tests(words: list[str]) -> bool:
    """``mvn -DskipTests verify``: a Maven run with its tests skipped and only lifecycle
    phases asked for (no ``plugin:goal``), so its check reading rests on tests that do not
    run (#799). ``=false`` is no skip; ``-DskipTests checkstyle:check verify`` still runs a
    check goal and is read as usual."""
    if not words or PurePosixPath(words[0]).name not in ("mvn", "mvnw"):
        return False
    skip = any(w.startswith("-D") and w[2:].partition("=")[0] in _MAVEN_SKIP
               and w[2:].partition("=")[2].lower() in ("", "true") for w in words[1:])
    return skip and not any(_PLUGIN_GOAL.fullmatch(w) for w in _maven_positionals(words[1:]))


# Maven options that take the next word (``-pl :mod``, ``-rf :mod``, ``-f pom.xml``).
_MAVEN_VALUED = frozenset({
    "-pl", "--projects", "-rf", "--resume-from", "-f", "--file", "-s", "--settings", "-gs",
    "--global-settings", "-t", "--toolchains", "-T", "--threads", "-P", "--activate-profiles",
    "-D", "--define", "-l", "--log-file", "-b", "--builder",
})
# A plugin goal: ``prefix:goal`` or ``group:artifact[:version]:goal``, with a non-empty prefix.
_PLUGIN_GOAL = re.compile(r"[\w.-]+(?::[\w.-]+)+")


def _maven_positionals(args: list[str]) -> list[str]:
    """Maven's goals and phases: the words left after its options and their values."""
    out: list[str] = []
    skip = False
    for word in args:
        if skip:
            skip = False
        elif word.startswith("-"):
            skip = word in _MAVEN_VALUED
        elif not word.startswith("$"):
            out.append(word)
    return out
