"""The shared CI-step follower: what one CI step runs, read from the repository (issue #845).

Detectors that stop at a workflow line miss checks run from what the line calls. This module is
the one place a CI step is followed into the repository, with one set of safety rules: **repo-
local only, nothing executed, and a path resolving outside the checkout is never read** (#799;
every read goes through ``lib.ci.ci_step_paths.local_reader``). No model and no build tool runs.

``StepFollower(read_local, files)`` and ``follow_step(text, read_local, files)`` take one CI line
(``run: make verify``, ``hack/verify-x.sh``, ``uses: ./.github/workflows/check.yml``) and return
one ``Followed`` per part it can follow. ``files``, the tracked files, is optional: with none, a
Gradle build file is found through ``read_local``. A ``Followed``
(``lib.ci.ci_build_follower.Followed``) carries ``kind``, the ``paths`` read, ``commands``,
``plugins``, ``tasks`` and ``unknown``:

- **script**: a committed script (``lib.ci.ci_step_paths.script_path``) resolves to its command
  lines (comments, blank lines and the shebang dropped, ``\\`` continuations joined). One level:
  a caller follows a returned command again if it needs to.
- **composite**: ``uses: ./.github/actions/x`` resolves to the ``run:`` commands of its
  ``runs.steps``, a nested local action's commands in place (``_MAX_DEPTH`` levels, each action
  once on a path). A node or docker action is not followed.
- **workflow**: ``uses: ./.github/workflows/check.yml`` (a job's or a step's) resolves to the
  ``run:`` commands of every job's steps, local actions and reusable workflows it calls followed
  the same way. A reusable workflow in another repository (``org/repo/.github/workflows/x.yml@v1``,
  a job's ``uses:`` or the line itself) is not read and lands in ``unknown``; a step's remote
  action (``actions/checkout@v4``) is not followed and is not unknown.
- **make**, **gradle**, **maven**: ``lib.ci.ci_build_follower``; Gradle tasks and Maven goals
  resolve to the plugins and tasks the build files bind to them.

A line is split on ``&&``, ``||``, ``;`` and ``|``; a leading ``cd <dir>`` sets the directory
the later parts are read from. A part that names nothing followable is skipped.

``follow_workflow(text, read_local)`` is the whole GitHub reading ``lib.ci.ci_checks`` and
``lib.secrets_guard`` run before ``lib.ci.ci_action_inputs`` (moved here from
``lib.ci.ci_expressions``, #845): ``\\`` continuations joined, local composite actions inlined
(``lib.ci.ci_composite``), matrix commands copied out (``lib.ci.ci_matrix``), then
``lib.ci.ci_expressions.settle_expressions``, with the unknown lines of all three merged.

The followers that existed before this module call it: ``lib.ci.ci_scripts`` (script path and
body read), ``lib.ci.ci_composite`` (local action read), ``lib.ci.ci_triggers`` (local reusable
workflow calls), ``lib.ci.ci_model_jobs`` and ``lib.agents.agent_ops`` /
``lib.agents.agent_job_scope`` (local workflows and actions) go through ``lib.ci.ci_step_paths``;
their readings did not change.

Inward-only imports: ``lib.ci.ci_action_inputs``, ``lib.ci.ci_build_follower``, ``lib.ci.ci_composite``,
``lib.ci.ci_continuations``, ``lib.ci.ci_expressions``, ``lib.ci.ci_masking``,
``lib.ci.ci_matrix``, ``lib.ci.ci_step_paths``, ``lib.instructions.command_resolver`` and
``lib.instructions.command_index``. Tests: ``tests/test_ci_step_follower.py``.
"""
from __future__ import annotations

import posixpath
import re

from lib.ci.ci_action_inputs import ReadLocal
from lib.ci.ci_build_follower import Followed, FollowKind, follow_gradle, follow_make, follow_maven
from lib.ci.ci_composite import inline_composites
from lib.ci.ci_continuations import join_continued
from lib.ci.ci_expressions import Settled, settle_expressions
from lib.ci.ci_masking import IndentTree, parse_lines
from lib.ci.ci_matrix import expand_matrix, jobs
from lib.ci.ci_step_paths import local_workflow, read_action, script_path
from lib.instructions.command_index import ci_line_text
from lib.instructions.command_resolver import command_words

__all__ = ["Followed", "FollowKind", "StepFollower", "follow_step", "follow_workflow"]

_MAX_DEPTH = 8
_BLOCK = re.compile(r"^[|>][-+0-9]*\s*(?:#.*)?$")
_SEGMENT = re.compile(r"&&|\|\||[;|]")
_USES = re.compile(r"""^\s*(?:-\s+)?uses:\s*(\S+)""")


def _remote_workflow(uses: str) -> bool:
    """A ``uses:`` value naming a reusable workflow in another repository
    (``org/repo/.github/workflows/ci.yml@v1``): not read, so unknown."""
    return not uses.startswith("./") and "/.github/workflows/" in uses


def follow_workflow(text: str, read_local: ReadLocal) -> Settled:
    """Composites, matrix and expressions read for one GitHub workflow (module docstring)."""
    expanded = expand_matrix(*inline_composites(join_continued(text), read_local))
    settled = settle_expressions(expanded.text)
    return settled._replace(unknown=settled.unknown | expanded.unknown)


def _script_commands(body: str) -> list[str]:
    out: list[str] = []
    for line in join_continued(body).splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            out.append(stripped)
    return out


def _run_commands(tree: IndentTree, lines: list[str], key: int) -> list[str]:
    """The commands of one ``run:`` key: its one-line value, or its block's lines."""
    value = tree.lines[key].value.strip()
    if value and not _BLOCK.match(value):
        return [value.strip("\"'")]
    return [lines[j].strip() for j in tree.subtree(key) if j != key and lines[j].strip()
            and not lines[j].strip().startswith("#")]


class StepFollower:
    """Follow CI steps through one repository's tracked files (module docstring)."""

    def __init__(self, read_local: ReadLocal, files: frozenset[str] = frozenset()) -> None:
        self.read_local, self.files = read_local, files

    def step(self, text: str) -> list[Followed]:
        """One ``Followed`` per part of a CI line that can be followed."""
        uses = _USES.match(text)
        if uses:
            value = uses.group(1).strip("\"'")
            if _remote_workflow(value):
                return [Followed("workflow", (), unknown=(value,))]
            found = self.uses(value)
            return [found] if found is not None else []
        out: list[Followed] = []
        cwd: str | None = ""
        for part in _SEGMENT.split(ci_line_text(text)):
            words = command_words(part.strip().strip("()"))
            if words[:1] == ["cd"]:
                cwd = self._cd(cwd, words)
                continue
            found = self.command(words, cwd) if words and cwd is not None else None
            if found is not None:
                out.append(found)
        return out

    @staticmethod
    def _cd(cwd: str | None, words: list[str]) -> str | None:
        """The directory after ``cd <dir>``; None once it leaves what the reader can settle."""
        if cwd is None or len(words) < 2 or words[1].startswith(("/", "~", "$", "-")):
            return None
        joined = posixpath.normpath(posixpath.join(cwd, words[1]))
        return None if joined == ".." or joined.startswith("../") else "" if joined == "." else joined

    def command(self, words: list[str], cwd: str = "") -> Followed | None:
        """A script, make, Gradle or Maven command, or None."""
        found = (follow_make(words, self.read_local, cwd)
                 or follow_gradle(words, self.read_local, self.files, cwd)
                 or follow_maven(words, self.read_local, cwd))
        if found is not None:
            return found
        path = script_path(" ".join(words))
        rel = posixpath.normpath(posixpath.join(cwd, path)) if path is not None else None
        body = self.read_local(rel) if rel is not None else None
        if rel is None or body is None:
            return None
        return Followed("script", (rel,), tuple(_script_commands(body)))

    def uses(self, uses: str, path: tuple[str, ...] = ()) -> Followed | None:
        """A local composite action or reusable workflow a ``uses:`` value names, or None."""
        workflow = local_workflow(uses)
        if workflow is not None:
            text = self.read_local(workflow)
            kind: FollowKind = "workflow"
            found = (workflow, text) if text is not None else None
        else:
            found = None if uses.endswith((".yml", ".yaml")) else read_action(uses, self.read_local)
            kind = "composite"
        if found is None or found[0] in path or len(path) >= _MAX_DEPTH:
            return None
        rel, text = found
        lines = text.splitlines()
        tree = IndentTree(parse_lines(lines))
        if kind == "composite" and not self._composite(tree):
            return None   # a node or docker action: its code is not read
        commands: list[str] = []
        read = [rel]
        unknown: list[str] = []
        for item in self._steps(tree, kind):
            for k in item:
                key = tree.lines[k].key
                value = tree.lines[k].value.strip("\"'")
                if key == "uses" and _remote_workflow(value):
                    unknown.append(value)
                elif key == "run":
                    commands += _run_commands(tree, lines, k)
                elif key == "uses" and tree.lines[k].value.strip("\"'").startswith("./"):
                    inner = self.uses(tree.lines[k].value.strip("\"'"), (*path, rel))
                    if inner is None:
                        unknown.append(tree.lines[k].value.strip("\"'"))
                        continue
                    commands += inner.commands
                    read += [p for p in inner.paths if p not in read]
                    unknown += inner.unknown
        return Followed(kind, tuple(read), tuple(commands), unknown=tuple(dict.fromkeys(unknown)))

    @staticmethod
    def _composite(tree: IndentTree) -> bool:
        runs = next((i for i, line in enumerate(tree.lines)
                     if line.structural and line.level == 0 and line.key == "runs"), None)
        using = next((k for k in tree.children(runs) if tree.lines[k].key == "using"), None) if runs is not None else None
        return using is not None and tree.lines[using].value.strip("\"'").lower() == "composite"

    @staticmethod
    def _steps(tree: IndentTree, kind: FollowKind) -> list[list[int]]:
        """The step items of an action's ``runs.steps``, or of every job (a job's own ``uses:``
        to a reusable workflow included, as a one-key item)."""
        if kind == "composite":
            runs = [i for i, line in enumerate(tree.lines)
                    if line.structural and line.level == 0 and line.key == "runs"]
            steps = [k for r in runs for k in tree.children(r) if tree.lines[k].key == "steps"]
            return [item for s in steps for item in tree.items(s)]
        out: list[list[int]] = []
        for job in jobs(tree):
            keys = tree.children(job)
            out += [[k] for k in keys if tree.lines[k].key == "uses"]
            for s in (k for k in keys if tree.lines[k].key == "steps"):
                out += tree.items(s)
        return out


def follow_step(text: str, read_local: ReadLocal, files: frozenset[str] = frozenset()) -> list[Followed]:
    """``StepFollower(read_local, files).step(text)`` (module docstring)."""
    return StepFollower(read_local, files).step(text)
