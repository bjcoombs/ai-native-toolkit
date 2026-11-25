"""The pre-commit secrets hooks the secrets guard reads (#1173, #1204).

``pre_commit_guard(root, tracked)`` reads the root ``.pre-commit-config.yaml`` (or ``.yml``),
only when git tracks it (``tracked``, the files at the assessed commit, as the other CI readers
read), for a hook that names a secrets tool by its ``repo:`` URL or its ``id:``. A hook whose
``stages:`` (or, with none, the top-level ``default_stages:``) holds only ``manual`` never runs on
commit, so it is not a guard and does not count toward level 1 (#1204); a repository entry counts
by its URL while any of its hooks runs on commit, or when none of its hooks could be read.

``secret_hooks(root, file)`` maps each secrets hook id (lower case, quotes stripped:
``id: "gitleaks"`` is ``gitleaks``) to its stages, or None for every stage. CI that runs the
hooks selects them with ``selected(hooks, stage)``: a plain ``pre-commit run`` uses the
``pre-commit`` stage, and ``--hook-stage <stage>`` (#1204) another, so ``--hook-stage manual``
runs a ``stages: [manual]`` hook. The legacy stage names (``commit``, ``push``) read as their
current ones. ``hook_tool(id)`` is the tool an id runs (``gitleaks-docker`` runs gitleaks);
``hook_id(tokens)`` and ``hook_stage(tokens)`` read a ``pre-commit run`` argument list.

Inward-only: ``lib.secrets_guard_lines``. Tests: ``tests/test_secrets_guard.py``,
``tests/test_secrets_guard_resolved.py``.
"""
from __future__ import annotations

import re
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import TypedDict

from lib.secrets_guard_lines import SECRET_TOOLS, STEP_START, block_end, code_lines, list_block_end, read_text, tools_in

PRE_COMMIT_FILES = (".pre-commit-config.yaml", ".pre-commit-config.yml")
# Legacy stage names pre-commit still accepts.
_STAGE_ALIASES = {"commit": "pre-commit", "push": "pre-push", "merge-commit": "pre-merge-commit"}
_VALUED_FLAGS = frozenset({"--from-ref", "--to-ref", "--source", "--origin", "-s", "-o", "--hook-stage",
                           "--color", "--config", "-c", "--remote-name", "--remote-url"})

HookStages = Mapping[str, frozenset[str] | None]  # hook id -> its stages, None for every stage


class PreCommitGuard(TypedDict):
    present: bool
    file: str | None
    tools: list[str]


def _stage(name: str) -> str:
    return _STAGE_ALIASES.get(name, name)


def _stage_values(lines: list[str], at: int) -> frozenset[str]:
    """The stage names a ``stages:`` or ``default_stages:`` key on line ``at`` lists, inline or as a
    block list below it."""
    values = lines[at].split(":", 1)[1].strip()
    if not values:  # a block list: the key, then ``- pre-commit`` lines below it
        key_indent = len(lines[at]) - len(lines[at].lstrip())
        values = " ".join(x.strip().lstrip("- ") for x in lines[at + 1:list_block_end(lines, at, key_indent)])
    return frozenset(_stage(s) for s in re.findall(r"[\w-]+", values))


def _items(lines: list[str]) -> Iterator[dict[str, int]]:
    """Each list item's own keys, as key name -> line. The keys sit at the column of the item's
    first key, after the dash: ``- id:`` (dash + 2) or the ``-   id:`` that ``pre-commit
    sample-config`` writes (dash + 4)."""
    for i, line in enumerate(lines):
        item = STEP_START.match(line)
        if not item:
            continue
        dash = len(item.group(1))
        end = block_end(lines, i, dash)
        key_col = len(line) - len(line[dash + 1:].lstrip())
        keys: dict[str, int] = {}
        for k, text in [(i, line[dash + 1:].strip())] + [(k, lines[k].strip()) for k in range(i + 1, end)
                                                          if len(lines[k]) - len(lines[k].lstrip()) == key_col]:
            m = re.match(r"([\w-]+)\s*:", text)
            if m:
                keys.setdefault(m.group(1), k)
        yield keys


def _value(lines: list[str], at: int) -> str:
    return lines[at].split(":", 1)[1].strip().strip("'\"")


def _hooks(lines: list[str]) -> Iterator[tuple[str | None, str | None, frozenset[str] | None]]:
    """(repo URL, hook id, stages) for each item: a repository entry has a URL and no id."""
    top = next((k for k, ln in enumerate(lines) if re.match(r"default_stages\s*:", ln)), None)
    default = _stage_values(lines, top) if top is not None else None
    for keys in _items(lines):
        repo = _value(lines, keys["repo"]) if "repo" in keys else None
        hook = _value(lines, keys["id"]) if "id" in keys else None
        # An empty ``stages: []`` is unset: pre-commit falls back to ``default_stages``.
        stages = (_stage_values(lines, keys["stages"]) if "stages" in keys else None) or default
        if repo is not None or hook is not None:
            yield repo, hook, stages


def _manual_only(stages: frozenset[str] | None) -> bool:
    return stages is not None and stages <= {"manual"}


def pre_commit_guard(root: Path, tracked: frozenset[str] | None = None) -> PreCommitGuard:
    """The root pre-commit config's secrets hooks that run on commit (module docstring)."""
    for name in PRE_COMMIT_FILES:
        if tracked is not None and name not in tracked:
            continue
        text = read_text(root / name)
        if text is None:
            continue
        lines = code_lines(text)
        tools: set[str] = set()
        repos: list[tuple[list[str], list[bool]]] = []  # (tools its URL names, whether each hook runs)
        for repo, hook, stages in _hooks(lines):
            if repo is not None:
                repos.append((tools_in(repo), []))
            if hook is not None:
                runs = not _manual_only(stages)
                if repos:
                    repos[-1][1].append(runs)
                if runs:
                    tools.update(tools_in(hook))
        for named, hooks in repos:
            if not hooks or any(hooks):
                tools.update(named)
        return {"present": bool(tools), "file": name, "tools": sorted(tools)}
    return {"present": False, "file": None, "tools": []}


def hook_tool(hook_id: str) -> str:
    """The tool a hook id runs: ``gitleaks-docker`` and ``gitleaks-system`` run gitleaks."""
    return next((t for t in SECRET_TOOLS if hook_id == t or hook_id.startswith(f"{t}-")), hook_id)


def secret_hooks(root: Path, file: str | None) -> dict[str, frozenset[str] | None]:
    """Each secrets hook id in the pre-commit config ``file``, with its stages (module docstring).
    Ids, not tools: ``pre-commit run gitleaks`` selects the hook whose id is ``gitleaks``, never a
    configured ``gitleaks-docker``."""
    if not file:
        return {}
    lines = code_lines(read_text(root / file) or "")
    out: dict[str, frozenset[str] | None] = {}
    for _, hook, stages in _hooks(lines):
        if hook is not None and hook_tool(hook.lower()) in SECRET_TOOLS:
            hook = hook.lower()
            # Two hooks with one id: the union of their stages, so either one being selected counts.
            if hook not in out:
                out[hook] = stages
            else:
                prior = out[hook]
                out[hook] = None if prior is None or stages is None else prior | stages
    return out


def selected(hooks: HookStages, stage: str = "pre-commit") -> frozenset[str]:
    """The hook ids a run at ``stage`` selects: a hook with no stages runs at every stage."""
    stage = _stage(stage)
    return frozenset(h for h, stages in hooks.items() if stages is None or stage in stages)


def hook_id(tokens: list[str]) -> str | None:
    """The hook ``pre-commit run`` names, wherever it sits among the flags; None for every hook."""
    skip = False
    for token in tokens:
        if skip:
            skip = False
        elif token == "--files":
            return None  # the rest are file names
        elif token in _VALUED_FLAGS:
            skip = True
        elif not token.startswith("-"):
            return token.lower().strip("'\"")
    return None


def hook_stage(tokens: list[str]) -> str:
    """The stage ``--hook-stage`` names (``--hook-stage manual`` or ``--hook-stage=manual``), else
    ``pre-commit``."""
    for n, token in enumerate(tokens):
        if token == "--hook-stage" and n + 1 < len(tokens):
            return tokens[n + 1].strip("'\"")
        if token.startswith("--hook-stage="):
            return token.split("=", 1)[1].strip("'\"")
    return "pre-commit"
