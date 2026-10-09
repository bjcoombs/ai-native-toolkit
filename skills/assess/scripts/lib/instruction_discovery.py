"""Discover nested and path-scoped agent instruction files (issue #511).

Every major agent tool loads instruction files below the repository root or
through path-scoped rules, not only from the fixed root locations
``instruction_files.INSTRUCTION_FILE_PATHS`` grades:

* ``CLAUDE.md`` / ``AGENTS.md`` / ``AGENTS.override.md`` / ``GEMINI.md`` in any
  directory (Claude Code, Codex, the AGENTS.md spec, Gemini CLI);
* ``.claude/rules/**/*.md``, scoped by ``paths`` frontmatter (Claude Code);
* ``.github/instructions/**/*.instructions.md``, scoped by ``applyTo``
  (GitHub Copilot);
* ``.cursor/rules/**/*.mdc``, scoped by ``globs`` / ``alwaysApply`` (Cursor),
  where a plain ``.md`` is ignored by the tool.

Each tracked file outside the built-in and ``.assess/config.toml`` excludes is
graded with ``grade_instructions`` and recorded with its scope; the block also
carries the always-loaded budget per tool (``instruction_budget``) and four
integrity findings: a scope glob that matches no tracked file, a plain ``.md``
under ``.cursor/rules``, a ``CLAUDE.md`` that shadows a different ``AGENTS.md``
in its directory without importing it, and a nested file that mostly repeats
its parent. Deterministic and read-only: no network, no model.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from lib.agent_instructions_grader import grade_instructions
from lib.instruction_content import RepoContext
from lib.assess_config import is_user_excluded
from lib.doc_graph import EXCLUDE_DIRS, EXCLUDE_PATH_SEQUENCES, is_repo_file
from lib.git_churn import GIT_TIMEOUT_SECONDS, ContentClock
from lib.instruction_budget import (
    CHARS_PER_TOKEN,
    follow_imports,
    claude_code_budget,
    codex_budget,
)
from lib.instruction_globs import (
    is_true,
    pattern_matches_any,
    scope_patterns,
    split_frontmatter,
)
from lib.run_context_types import (
    InstructionFinding,
    InstructionScope,
    NestedInstructionFile,
    NestedInstructionsBlock,
)

DIRECTORY_FILE_NAMES = ("CLAUDE.md", "AGENTS.md", "AGENTS.override.md", "GEMINI.md")
_TOOL_BY_NAME = {
    "CLAUDE.md": "claude_code",
    "AGENTS.md": "agents_md",
    "AGENTS.override.md": "codex",
    "GEMINI.md": "gemini",
}
# The built-in excludes, minus `.claude`: that tree is excluded from the doc
# graph as configuration, but it is exactly where Claude Code keeps rules.
_PRUNE_DIRS = (EXCLUDE_DIRS - {".claude"}) | {".git"}
# Copilot reads a pattern that covers every file as "apply always".
_ALL_FILES_GLOBS = {"**", "**/*", "*"}
# A nested file whose distinct lines are at least this share copies of its
# parent's, over at least this many lines, repeats its parent.
OVERLAP_THRESHOLD = 0.5
OVERLAP_MIN_LINES = 5
MAX_FILES = 200
MAX_FINDINGS = 100


@dataclass
class _Candidate:
    rel: str
    kind: str
    tool: str
    directory: str
    globs: list[str] = field(default_factory=list)
    always_loaded: bool = False
    body: str = ""


def _contains(parts: tuple[str, ...], seq: tuple[str, ...]) -> int:
    """Index where ``seq`` starts inside ``parts``, or -1."""
    n = len(seq)
    for i in range(len(parts) - n + 1):
        if parts[i:i + n] == seq:
            return i
    return -1


def _pruned(rel: Path, extra_dirs: set[str], extra_pats: list[str]) -> bool:
    if rel.name in _PRUNE_DIRS:
        return True
    if any(_contains(rel.parts, seq) >= 0 for seq in EXCLUDE_PATH_SEQUENCES):
        return True
    return is_user_excluded(rel, extra_dirs, extra_pats)


def _walk(repo_root: Path, extra_dirs: set[str], extra_pats: list[str]) -> list[Path]:
    """Repo-relative paths of every file outside the pruned trees."""
    out: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(repo_root):
        rel_dir = Path(dirpath).relative_to(repo_root)
        dirnames[:] = sorted(
            d for d in dirnames if not _pruned(rel_dir / d, extra_dirs, extra_pats))
        for name in filenames:
            rel = rel_dir / name
            if not is_user_excluded(rel, extra_dirs, extra_pats):
                out.append(rel)
    return out


def _dir_of(parts: tuple[str, ...]) -> str:
    return "/".join(parts) or "."


def _rule_candidate(rel: PurePosixPath, text: str) -> _Candidate | None:
    """A path-scoped rule file, or None when ``rel`` is not one."""
    parts = rel.parts
    fields, body = split_frontmatter(text)
    at = _contains(parts, (".claude", "rules"))
    if at >= 0 and rel.suffix == ".md":
        globs = scope_patterns(fields.get("paths", []))
        return _Candidate(rel.as_posix(), "claude_rule", "claude_code", _dir_of(parts[:at]),
                          globs, always_loaded=not globs and at == 0, body=body)
    if parts[:2] == (".github", "instructions") and rel.name.endswith(".instructions.md"):
        globs = scope_patterns(fields.get("applyTo", []))
        return _Candidate(rel.as_posix(), "copilot_instructions", "copilot", ".", globs,
                          always_loaded=bool(set(globs) & _ALL_FILES_GLOBS), body=body)
    at = _contains(parts, (".cursor", "rules"))
    if at >= 0 and rel.suffix == ".mdc":
        globs = scope_patterns(fields.get("globs", []))
        return _Candidate(rel.as_posix(), "cursor_rule", "cursor", _dir_of(parts[:at]),
                          globs, always_loaded=is_true(fields.get("alwaysApply")),
                          body=body)
    return None


def _directory_candidate(rel: PurePosixPath, text: str) -> _Candidate | None:
    if rel.name not in DIRECTORY_FILE_NAMES:
        return None
    parts = rel.parts[:-1]
    # `.claude/CLAUDE.md` is Claude Code's alternative spot for a directory's file.
    if rel.name == "CLAUDE.md" and parts[-1:] == (".claude",):
        parts = parts[:-1]
    return _Candidate(rel.as_posix(), "directory_file", _TOOL_BY_NAME[rel.name],
                      _dir_of(parts), always_loaded=parts == (), body=text)


def _is_stray_cursor_md(rel: PurePosixPath) -> bool:
    return _contains(rel.parts, (".cursor", "rules")) >= 0 and rel.suffix == ".md"


def _norm_lines(text: str) -> set[str]:
    out: set[str] = set()
    for line in text.splitlines():
        s = line.strip().lower()
        if len(s) > 3 and any(ch.isalnum() for ch in s):
            out.add(s)
    return out


@dataclass
class _Surface:
    """Everything one discovery pass found, keyed for the finding checks."""

    candidates: list[_Candidate]
    stray_cursor_md: list[str]
    universe: list[str]


def _collect(
    repo_root: Path, tracked: frozenset[Path] | None,
    extra_dirs: set[str], extra_pats: list[str],
) -> _Surface:
    candidates: list[_Candidate] = []
    stray: list[str] = []
    for rel_path in _walk(repo_root, extra_dirs, extra_pats):
        rel = PurePosixPath(rel_path.as_posix())
        maybe_rule = (rel.suffix in (".md", ".mdc")
                      and (rel.name in DIRECTORY_FILE_NAMES or "rules" in rel.parts
                           or "instructions" in rel.parts))
        if not maybe_rule:
            continue
        if not is_repo_file(repo_root / rel_path, repo_root, tracked):
            continue
        if _is_stray_cursor_md(rel):
            stray.append(rel.as_posix())
            continue
        try:
            text = (repo_root / rel_path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        cand = _rule_candidate(rel, text) or _directory_candidate(rel, text)
        if cand is not None:
            candidates.append(cand)
    return _Surface(sorted(candidates, key=lambda c: c.rel), sorted(stray),
                    _universe(repo_root, tracked))


def _ls_files(repo_root: Path) -> set[str]:
    """Tracked paths as git names them, so a tracked symlink keeps its own name."""
    try:
        raw = subprocess.run(
            ["git", "-C", str(repo_root), "ls-files", "-z"],
            capture_output=True, text=True, check=True, timeout=GIT_TIMEOUT_SECONDS,
        ).stdout
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        return set()
    return {name for name in raw.split("\0") if name}


def _universe(repo_root: Path, tracked: frozenset[Path] | None) -> list[str]:
    """Repo-relative POSIX paths a scope glob may match: every tracked file.

    ``tracked`` holds resolved paths, which name a tracked symlink by its
    target; git's own names are added so a glob naming the link still matches.
    """
    if tracked is not None:
        names = {p.relative_to(repo_root).as_posix()
                 for p in tracked if p.is_relative_to(repo_root)}
        return sorted(names | _ls_files(repo_root))
    out: list[str] = []
    for dirpath, dirnames, filenames in os.walk(repo_root):
        dirnames[:] = [d for d in dirnames if d != ".git"]
        rel_dir = Path(dirpath).relative_to(repo_root)
        out.extend((rel_dir / f).as_posix() for f in filenames)
    return sorted(out)


def _prefix_on_disk(repo_root: Path, pattern: str) -> bool:
    """True when the pattern's wildcard-free leading directory exists on disk.

    A rule for a generated or git-ignored tree (``dist/**``) matches no tracked
    file yet still loads when the agent works there, so it is not dead.
    """
    pat = pattern.strip()
    while pat.startswith("./"):
        pat = pat[2:]
    parts: list[str] = []
    for part in pat.lstrip("/").split("/")[:-1]:
        if any(ch in part for ch in "*?[{!"):
            break
        parts.append(part)
    if not parts:
        return False
    target = (repo_root / "/".join(parts)).resolve()
    return target.is_relative_to(repo_root) and target.is_dir()


def _pattern_live(repo_root: Path, surface: _Surface, cand: _Candidate, pattern: str) -> bool:
    # A rule under a nested `.claude/rules` or `.cursor/rules` may name paths
    # relative to its own directory; either reading counts.
    local = pattern if cand.directory == "." else f"{cand.directory}/{pattern}"
    return (pattern_matches_any(pattern, surface.universe)
            or pattern_matches_any(local, surface.universe)
            or _prefix_on_disk(repo_root, pattern)
            or _prefix_on_disk(repo_root, local))


def _dead_globs(repo_root: Path, surface: _Surface) -> list[InstructionFinding]:
    """Rules that never load: every scope pattern matches nothing.

    One live pattern loads the rule, and an always-loaded rule (Cursor
    ``alwaysApply: true``) loads whatever its globs say, so neither is dead.
    """
    out: list[InstructionFinding] = []
    for cand in surface.candidates:
        if not cand.globs or cand.always_loaded:
            continue
        if any(_pattern_live(repo_root, surface, cand, p) for p in cand.globs):
            continue
        shown = ", ".join(f"`{p}`" for p in cand.globs)
        out.append({
            "kind": "dead_glob", "path": cand.rel, "pattern": ", ".join(cand.globs),
            "detail": f"no scope pattern ({shown}) matches a tracked file, "
                      "so the rule never loads",
        })
    return out


def _stray_md(surface: _Surface) -> list[InstructionFinding]:
    return [{
        "kind": "ignored_cursor_md", "path": rel,
        "detail": "Cursor reads only `.mdc` files under `.cursor/rules`; "
                  "rename it to `.mdc` with frontmatter or move it",
    } for rel in surface.stray_cursor_md]


def _directory_files(surface: _Surface) -> dict[str, list[_Candidate]]:
    by_dir: dict[str, list[_Candidate]] = {}
    for cand in surface.candidates:
        if cand.kind == "directory_file":
            by_dir.setdefault(cand.directory, []).append(cand)
    return by_dir


def _shadowing(
    repo_root: Path, by_dir: dict[str, list[_Candidate]], tracked: frozenset[Path] | None,
) -> list[InstructionFinding]:
    """A directory whose CLAUDE.md hides a different, unimported AGENTS.md."""
    out: list[InstructionFinding] = []

    def is_tracked(p: Path) -> bool:
        return is_repo_file(p, repo_root, tracked)

    for directory, cands in sorted(by_dir.items()):
        claude = [c for c in cands if PurePosixPath(c.rel).name == "CLAUDE.md"]
        agents = [c for c in cands if PurePosixPath(c.rel).name == "AGENTS.md"]
        if not claude or not agents:
            continue
        agent = agents[0]
        if any(c.body.strip() == agent.body.strip() for c in claude):
            continue
        agent_real = (repo_root / agent.rel).resolve()
        if any((repo_root / c.rel).resolve() == agent_real for c in claude):
            continue
        loaded, _skipped = follow_imports([c.rel for c in claude], repo_root, is_tracked)
        if agent.rel in loaded:
            continue
        out.append({
            "kind": "claude_shadows_agents", "path": claude[0].rel, "parent": agent.rel,
            "detail": f"Claude Code reads `{claude[0].rel}` and never `{agent.rel}`, "
                      "which differs; add `@AGENTS.md` to the CLAUDE.md or make one "
                      "a symlink to the other",
        })
    return out


def _nearest_parent(directory: str, by_dir: dict[str, list[_Candidate]]) -> str | None:
    parts = directory.split("/")
    for i in range(len(parts) - 1, -1, -1):
        parent = "/".join(parts[:i]) or "."
        if parent in by_dir:
            return parent
    return None


def _repeats(by_dir: dict[str, list[_Candidate]]) -> list[InstructionFinding]:
    """Nested directory files that mostly copy the nearest ancestor's."""
    out: list[InstructionFinding] = []
    for directory, cands in sorted(by_dir.items()):
        if directory == ".":
            continue
        parent = _nearest_parent(directory, by_dir)
        if parent is None:
            continue
        parent_lines: set[str] = set()
        for pc in by_dir[parent]:
            parent_lines |= _norm_lines(pc.body)
        for cand in cands:
            lines = _norm_lines(cand.body)
            if len(lines) < OVERLAP_MIN_LINES:
                continue
            overlap = len(lines & parent_lines) / len(lines)
            if overlap >= OVERLAP_THRESHOLD:
                out.append({
                    "kind": "repeats_parent", "path": cand.rel,
                    "parent": by_dir[parent][0].rel, "overlap": round(overlap, 3),
                    "detail": f"{round(overlap * 100)}% of its lines repeat "
                              f"`{by_dir[parent][0].rel}`, which the tool already loads",
                })
    return out


def _grade(
    cand: _Candidate, repo_root: Path, clock: ContentClock, skills_present: bool,
    repo: RepoContext | None,
) -> NestedInstructionFile:
    days = clock.days(repo_root / cand.rel)
    freshness = days if days is not None else 0
    grade = grade_instructions(cand.body, freshness_days=freshness,
                               skills_present=skills_present, repo=repo, path=cand.rel)
    scope: InstructionScope = {
        "directory": cand.directory, "globs": cand.globs,
        "always_loaded": cand.always_loaded,
    }
    return {
        "path": cand.rel, "tool": cand.tool, "kind": cand.kind, "scope": scope,
        "grade": grade.grade, "score": grade.score, "subscores": dict(grade.subscores),
        "findings": grade.findings,
        "freshness_days": freshness, "line_count": len(cand.body.splitlines()),
    }


def _budget_inputs(
    surface: _Surface, by_dir: dict[str, list[_Candidate]],
) -> tuple[list[str], list[str], dict[str, str]]:
    root_names = {c.rel for c in by_dir.get(".", [])}
    claude_roots: list[str] = [p for p in ("CLAUDE.md", ".claude/CLAUDE.md") if p in root_names]
    if not claude_roots and "AGENTS.md" in root_names:
        claude_roots = ["AGENTS.md"]
    unscoped = [c.rel for c in surface.candidates
                if c.kind == "claude_rule" and c.always_loaded]
    agents_by_dir: dict[str, str] = {}
    for directory, cands in by_dir.items():
        names = {PurePosixPath(c.rel).name: c for c in cands}
        # Codex reads the first non-empty file of override, then AGENTS.md.
        for name in ("AGENTS.override.md", "AGENTS.md"):
            chosen = names.get(name)
            if chosen is not None and chosen.body.strip():
                agents_by_dir[directory] = chosen.rel
                break
    return claude_roots, unscoped, agents_by_dir


def discover_nested_instructions(
    repo_root: Path,
    *,
    tracked: frozenset[Path] | None,
    clock: ContentClock,
    skills_present: bool,
    repo: RepoContext | None,
    graded_elsewhere: frozenset[str],
    extra_exclude_dirs: set[str] | None = None,
    extra_exclude_patterns: list[str] | None = None,
) -> NestedInstructionsBlock:
    """Build the ``nested_instructions`` run-context block.

    ``repo`` is the context commands and references resolve against
    (``lib.instruction_content.build_repo_context``), the same one the root
    files are graded with. ``graded_elsewhere`` holds the root locations ``grade_instruction_files``
    already grades; they feed the budget and the findings but get no second
    graded entry here.
    """
    repo_root = repo_root.resolve()
    surface = _collect(repo_root, tracked, extra_exclude_dirs or set(),
                       list(extra_exclude_patterns or []))
    by_dir = _directory_files(surface)

    def is_tracked(p: Path) -> bool:
        return is_repo_file(p, repo_root, tracked)

    graded = [_grade(c, repo_root, clock, skills_present, repo)
              for c in surface.candidates if c.rel not in graded_elsewhere]
    findings = (_dead_globs(repo_root, surface) + _stray_md(surface)
                + _shadowing(repo_root, by_dir, tracked) + _repeats(by_dir))
    claude_roots, unscoped, agents_by_dir = _budget_inputs(surface, by_dir)
    return {
        "available": True,
        "files": graded[:MAX_FILES],
        "files_total": len(graded),
        "budget": {
            "chars_per_token": CHARS_PER_TOKEN,
            "claude_code": claude_code_budget(repo_root, claude_roots, unscoped, is_tracked),
            "codex": codex_budget(repo_root, agents_by_dir),
        },
        "findings": findings[:MAX_FINDINGS],
        "findings_total": len(findings),
    }
