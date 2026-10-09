"""The instruction text each agent tool loads before it starts, per tool.

Claude Code loads the root ``CLAUDE.md`` (and ``.claude/CLAUDE.md``), every
file those ``@import`` (recursively, up to four hops), and each
``.claude/rules`` file with no ``paths`` frontmatter at launch; a root
``AGENTS.md`` stands in only when no root ``CLAUDE.md`` exists
(https://code.claude.com/docs/en/memory). Codex joins ``AGENTS.md`` files
root-down, one per directory with ``AGENTS.override.md`` preferred, and stops
at ``project_doc_max_bytes`` (32 KiB by default). This module measures both
sets in lines, words, bytes and estimated tokens. Read-only: imports resolve
relative to the importing file and are followed only into tracked files
inside the repository.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable
from pathlib import Path, PurePosixPath

from lib.run_context_types import (
    ClaudeCodeBudget,
    CodexBudget,
    CodexChain,
    InstructionTotals,
    SkippedImport,
)

# Same ~4-characters-per-token estimate as the treemap's keyhole budget
# (``complexity-treemap.py``): model-agnostic and always labelled an estimate.
CHARS_PER_TOKEN = 4
# Codex's default ``project_doc_max_bytes``.
CODEX_MAX_BYTES = 32 * 1024
# Claude Code follows imports "with a maximum depth of four hops"
# (https://code.claude.com/docs/en/memory).
MAX_IMPORT_DEPTH = 4
MAX_SKIPPED_IMPORTS = 40

_FENCE_RE = re.compile(r"^\s*(```|~~~)")
_INLINE_CODE_RE = re.compile(r"`[^`]*`")
_IMPORT_RE = re.compile(r"(?:^|(?<=[\s(\[]))@([^\s)\]>]+)")
_TRAILING_PUNCT = ".,;:!?'\""

IsTracked = Callable[[Path], bool]


def find_imports(text: str) -> list[str]:
    """``@path`` imports in ``text``, outside code blocks and code spans.

    A bare ``@name`` with neither a ``/`` nor a ``.`` is a mention, not a file,
    and is dropped so it never reads as a broken import.
    """
    out: list[str] = []
    in_fence = False
    for line in text.splitlines():
        if _FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        for match in _IMPORT_RE.finditer(_INLINE_CODE_RE.sub("", line)):
            target = match.group(1).rstrip(_TRAILING_PUNCT)
            if "/" in target or "." in target:
                out.append(target)
    return out


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def totals(repo_root: Path, rel_paths: list[str]) -> InstructionTotals:
    """Lines, words, bytes and estimated tokens across ``rel_paths``."""
    lines = words = size = chars = 0
    for rel in rel_paths:
        text = _read(repo_root / rel)
        lines += len(text.splitlines())
        words += len(text.split())
        size += len(text.encode("utf-8"))
        chars += len(text)
    return {
        "files": list(rel_paths),
        "lines": lines,
        "words": words,
        "bytes": size,
        "est_tokens": math.ceil(chars / CHARS_PER_TOKEN),
    }


def resolve_import(
    source_rel: str, target: str, repo_root: Path, is_tracked: IsTracked,
) -> tuple[str | None, str]:
    """Resolve one import to a repo-relative path, or ``(None, reason)``.

    A relative target resolves against the importing file's directory. A
    home-relative (``~/``) target, or one resolving outside the repository,
    never reaches a clone and is skipped.
    """
    if target.startswith("~"):
        return None, "outside repository"
    base = repo_root / PurePosixPath(source_rel).parent
    candidate = Path(target) if target.startswith("/") else base / target
    try:
        real = candidate.resolve()
    except OSError:
        return None, "unreadable"
    if not real.is_relative_to(repo_root):
        return None, "outside repository"
    if not real.is_file():
        return None, "missing"
    if not is_tracked(candidate):
        return None, "untracked"
    return real.relative_to(repo_root).as_posix(), ""


def follow_imports(
    roots: list[str], repo_root: Path, is_tracked: IsTracked,
) -> tuple[list[str], list[SkippedImport]]:
    """Breadth-first import walk from ``roots``, cycle-guarded and depth-capped."""
    loaded: list[str] = list(roots)
    seen = set(roots)
    skipped: list[SkippedImport] = []
    frontier = list(roots)
    for _hop in range(MAX_IMPORT_DEPTH):
        nxt: list[str] = []
        for source in frontier:
            for target in find_imports(_read(repo_root / source)):
                rel, reason = resolve_import(source, target, repo_root, is_tracked)
                if rel is None:
                    if len(skipped) < MAX_SKIPPED_IMPORTS:
                        skipped.append({"source": source, "target": target, "reason": reason})
                    continue
                if rel in seen:
                    continue
                seen.add(rel)
                loaded.append(rel)
                nxt.append(rel)
        if not nxt:
            break
        frontier = nxt
    return loaded, skipped


def claude_code_budget(
    repo_root: Path, root_files: list[str], unscoped_rules: list[str],
    is_tracked: IsTracked,
) -> ClaudeCodeBudget:
    """Root memory files, their imports, and the rules loaded at launch.

    ``root_files`` are the tracked root ``CLAUDE.md`` / ``.claude/CLAUDE.md``
    (or the root ``AGENTS.md`` when neither exists); ``unscoped_rules`` the
    root ``.claude/rules`` files with no ``paths``.
    """
    loaded, skipped = follow_imports(root_files, repo_root, is_tracked)
    for rule in unscoped_rules:
        if rule not in loaded:
            loaded.append(rule)
    base = totals(repo_root, loaded)
    return {**base, "skipped_imports": skipped}


def codex_chain(repo_root: Path, directory: str, agents_by_dir: dict[str, str]) -> CodexChain:
    """The AGENTS.md files Codex joins for ``directory``, root first."""
    parts = [] if directory == "." else directory.split("/")
    dirs = ["."] + ["/".join(parts[:i]) for i in range(1, len(parts) + 1)]
    files = [agents_by_dir[d] for d in dirs if d in agents_by_dir]
    base = totals(repo_root, files)
    return {**base, "directory": directory, "exceeds_limit": base["bytes"] > CODEX_MAX_BYTES}


def codex_budget(repo_root: Path, agents_by_dir: dict[str, str]) -> CodexBudget:
    """Root AGENTS.md against 32 KiB, plus the heaviest root-down chain.

    ``agents_by_dir`` maps a repo-relative directory ("." for the root) to the
    one file Codex reads there. ``deepest_chain`` is the nested directory whose
    joined chain is largest; None when no nested AGENTS.md exists.
    """
    root = codex_chain(repo_root, ".", agents_by_dir)
    nested = [d for d in agents_by_dir if d != "."]
    deepest: CodexChain | None = None
    for directory in sorted(nested):
        chain = codex_chain(repo_root, directory, agents_by_dir)
        if deepest is None or chain["bytes"] > deepest["bytes"]:
            deepest = chain
    return {
        "files": root["files"],
        "lines": root["lines"],
        "words": root["words"],
        "bytes": root["bytes"],
        "est_tokens": root["est_tokens"],
        "limit_bytes": CODEX_MAX_BYTES,
        "exceeds_limit": root["exceeds_limit"],
        "deepest_chain": deepest,
    }
