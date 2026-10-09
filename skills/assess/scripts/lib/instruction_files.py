"""Find and grade the agent instruction files at a repository's known locations.

Grades every committed CLAUDE.md / AGENTS.md / GEMINI.md / .cursorrules /
.github/copilot-instructions.md variant through ``agent_instructions_grader``,
lets an alias inherit its canonical file's grade, and reports the instruction
surface's integrity: untracked or dangling files, broken links to an
instruction file, sensitive content, and the ancestor cascade a clone never
sees. Read-only.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from lib.agent_instructions_grader import (
    detect_alias,
    detect_skills_dir,
    grade_instructions,
    scan_sensitive_content,
)
from lib.doc_graph import is_repo_file
from lib.instruction_content import build_repo_context
from lib.doc_staleness import content_clock
from lib.git_churn import ContentClock, tracked_files
from lib.instruction_discovery import discover_nested_instructions
from lib.run_context_types import NestedInstructionsBlock


# Known agent instruction file locations (relative to repo root).
# The same heuristic grader applies to all of them.
INSTRUCTION_FILE_PATHS = [
    # Canonical repo-root locations
    "CLAUDE.md",
    "AGENTS.md",
    "GEMINI.md",
    ".cursorrules",
    # Tool-specific locations under .github/
    ".github/copilot-instructions.md",
    ".github/claude-instructions.md",
    ".github/claude-review-instructions.md",
    # docs/ subdirectory variants used by some projects
    "docs/CLAUDE.md",
    "docs/AGENTS.md",
]

# Grade ranking (best -> worst) for picking a top-level grade across multiple files.
GRADE_RANK = {"A": 7, "A-": 6, "B+": 5, "B": 4, "C": 3, "D": 2, "F": 1}


def _file_freshness_days(file_path: Path, clock: ContentClock) -> int:
    """Days since file_path's last content change in git. 0 if not in git.

    Same clock as `.doc_staleness` (author time, bulk mechanical commits
    skipped - issue #333), so a licence-header sweep cannot make a stale
    instruction file read as fresh.
    """
    days = clock.days(file_path)
    return days if days is not None else 0


def grade_instruction_files(
    repo_root: Path,
) -> tuple[
    dict[str, dict[str, Any]], str | None, list[str], list[dict[str, Any]],
    dict[str, Any], dict[str, Any],
]:
    """Scan all known instruction file locations and grade each one found.

    Returns: (files_dict, best_grade, untracked, dangling_refs, skills_info, sensitive)
        files_dict: keyed by filename, e.g. {"CLAUDE.md": {grade, score, ...}}.
        best_grade: best letter grade across all *tracked, present* files; None
            if none found. None is distinct from "F": None means no committed
            file exists ("create the file"), "F" means one exists but scored
            poorly ("fix the file").
        untracked: instruction files that exist on disk but aren't part of the
            repo (untracked / git-ignored / symlinked from outside). Surfaced as
            a finding so a personal CLAUDE.md isn't silently credited *or*
            silently ignored - the agent should note it isn't committed.
        dangling_refs: instruction files that are dangling symlinks (committed
            `.cursorrules -> missing-target`) - an advertised-but-broken
            instruction surface.
        sensitive: per-path list of REDACTED sensitive-content findings (IPs,
            SSH/host details, credentials, home-dir/PII paths) for any candidate
            on disk - tracked or untracked. Surfaced so the remediation warns
            before recommending a file be committed, especially to a public repo
            (issue #56).
    """
    repo_root = repo_root.resolve()
    tracked = tracked_files(repo_root)
    # Detect skills directories once, before grading any file. A repo that
    # factors guidance into on-demand skills uses progressive disclosure, so a
    # large instruction file is not penalized as bloat (see compute_bloat_penalty).
    skills_info = detect_skills_dir(repo_root)
    skills_present = skills_info["skills_dirs_present"]
    clock = content_clock(repo_root)  # one build for every candidate
    repo_ctx = build_repo_context(repo_root)  # commands and references resolve against it
    found: dict[str, dict[str, Any]] = {}
    untracked: list[str] = []
    dangling_refs: list[dict[str, Any]] = []
    sensitive: dict[str, list[Any]] = {}
    for rel_path in INSTRUCTION_FILE_PATHS:
        candidate = repo_root / rel_path
        # A dangling symlink (an instruction file pointing at a missing target)
        # exists() == False but is_symlink() == True - an advertised, broken
        # instruction reference, not "no file".
        if candidate.is_symlink() and not candidate.exists():
            dangling_refs.append({"path": rel_path, "reason": "symlink target missing"})
            continue
        if not candidate.exists():
            continue
        # Scan every candidate on disk for content unsafe to publish - tracked
        # OR untracked. An untracked file is exactly the one the remediation
        # might tell the user to commit, so it must be scanned before that.
        try:
            disk_text = candidate.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            disk_text = ""
        flags = scan_sensitive_content(disk_text) if disk_text else []
        if flags:
            sensitive[rel_path] = flags
        # Only grade genuine repo files. An on-disk-but-untracked instruction
        # file (a contributor's personal CLAUDE.md, or one symlinked in from
        # outside) is recorded as a finding rather than credited to the score.
        if not is_repo_file(candidate, repo_root, tracked):
            untracked.append(rel_path)
            continue
        text = disk_text
        freshness = _file_freshness_days(candidate, clock)
        grade = grade_instructions(
            text, freshness_days=freshness, skills_present=skills_present,
            repo=repo_ctx, path=rel_path,
        )
        entry = {
            "grade": grade.grade,
            "score": grade.score,
            "subscores": grade.subscores,
            "findings": grade.findings,
            "freshness_days": freshness,
            "line_count": len(text.splitlines()),
            "present": True,
        }
        # Alias detection (issue #57): a committed AGENTS.md/GEMINI.md that is a
        # symlink to - or a thin stub pointing at - a canonical instruction file
        # is the desired single-source-of-truth shape, not a low-scoring bespoke
        # doc. Record the target so the second pass can inherit its grade.
        alias_target = _alias_target(candidate, text, repo_root)
        if alias_target:
            entry["alias_target_basename"] = alias_target
        found[rel_path] = entry

    _resolve_alias_grades(found)

    best = (max(found.values(), key=lambda v: GRADE_RANK.get(v["grade"], 0))["grade"]
            if found else None)
    return found, best, untracked, dangling_refs, skills_info, sensitive


# Basenames (lowercased) of the known instruction files, for cross-referencing
# broken doc links against the instruction surface.
_INSTRUCTION_BASENAMES = {Path(p).name.lower() for p in INSTRUCTION_FILE_PATHS}

# Canonical files an alias would point at (a single source of truth).
_CANONICAL_ALIAS_TARGETS = {"claude.md", "agents.md", "gemini.md"}


def _alias_target(candidate: Path, text: str, repo_root: Path) -> str | None:
    """Return the canonical basename this file aliases, or None.

    Two shapes count as an alias (issue #57):
      * a symlink whose target is a canonical instruction file, or
      * a thin stub whose only real content references a canonical file.
    The alias's own basename is excluded, so CLAUDE.md never aliases itself.
    """
    self_name = candidate.name.lower()
    # Symlink alias - the target is whatever the link resolves to.
    if candidate.is_symlink():
        try:
            target_name = candidate.resolve().name.lower()
        except OSError:
            target_name = ""
        if target_name in _CANONICAL_ALIAS_TARGETS and target_name != self_name:
            return candidate.resolve().name
    # Thin-stub alias - short file that just points at a canonical doc.
    alias = detect_alias(text)
    target = alias["alias_target"]
    if alias["is_alias"] and target:
        if target.lower() != self_name:
            return target
    return None


def _resolve_alias_grades(found: dict[str, dict[str, Any]]) -> None:
    """Let an alias inherit the grade of the canonical file it points at.

    A thin alias/symlink should grade as the single-source-of-truth it routes
    to, not as a low-scoring standalone doc that the remediation would tell the
    user to rewrite. Mutates ``found`` in place: marks ``is_alias`` and copies
    the target's grade/score when the target is itself graded.
    """
    by_basename = {Path(rel).name.lower(): meta for rel, meta in found.items()}
    for meta in found.values():
        target = meta.pop("alias_target_basename", None)
        if not target:
            continue
        target_meta = by_basename.get(target.lower())
        meta["is_alias"] = True
        meta["alias_target"] = target
        if target_meta is not None and target_meta is not meta:
            # Inherit the canonical grade - the alias is as good as what it
            # points at, and carries no maintenance burden of its own.
            meta["grade"] = target_meta["grade"]
            meta["score"] = target_meta["score"]


def detect_ancestor_instructions(repo_root: Path) -> list[str]:
    """Detect committed-elsewhere instruction files that cascade into this repo.

    Claude Code composes ``CLAUDE.md`` from every ancestor directory plus the
    global ``~/.claude/CLAUDE.md``. So "no instruction file at the repo root" is
    not the same as "no instructions anywhere" - a clone gets none of the
    ancestor cascade, but the maintainer working in-tree does (issue #57).

    Returns REDACTED, repo-relative / ``~``-relative descriptors (never absolute
    paths - those would leak a home directory into the committed wiki). Best
    effort: any filesystem error yields an empty list.
    """
    found: list[str] = []
    try:
        repo_root = repo_root.resolve()
        # Walk parent directories above the repo root (bounded depth).
        parent = repo_root.parent
        depth = 1
        while parent != parent.parent and depth <= 6:
            for name in ("CLAUDE.md", "AGENTS.md", "GEMINI.md"):
                if (parent / name).is_file():
                    found.append(f"{name} ({depth} level(s) above repo root)")
            parent = parent.parent
            depth += 1
        # The global user instructions, if present.
        for rel in (".claude/CLAUDE.md", ".codex/AGENTS.md", ".gemini/GEMINI.md"):
            if (Path.home() / rel).is_file():
                found.append(f"~/{rel} (global user instructions)")
    except OSError:
        return []
    return found


def broken_instruction_refs(
    doc_graph: dict[str, Any], dangling_refs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Combine dangling-symlink instruction files with broken doc links whose
    target is an instruction file (an entry doc linking a missing CLAUDE.md).
    These are advertised-but-broken instruction references."""
    refs = list(dangling_refs)
    if doc_graph.get("available"):
        for bl in doc_graph.get("broken_links", []):
            target = bl.get("target", "")
            if Path(target).name.lower() in _INSTRUCTION_BASENAMES:
                refs.append({
                    "from": bl.get("from"), "target": target,
                    "reason": "link to missing instruction file",
                })
    return refs


def instruction_file_size(instruction_files: dict[str, Any]) -> dict[str, Any]:
    """Line/word counts and bloat penalty per graded instruction file."""
    return {
        path: {
            "line_count": meta["subscores"].get("line_count", 0),
            "word_count": meta["subscores"].get("word_count", 0),
            "bloat_penalty": meta["subscores"].get("bloat_penalty", 0),
        }
        for path, meta in instruction_files.items()
    }


def nested_instruction_surface(
    repo_root: Path,
    excludes: tuple[set[str], list[str]] | None = None,
) -> NestedInstructionsBlock:
    """Nested and path-scoped instruction files beyond the root locations.

    The ``nested_instructions`` block (issue #511): every tracked nested
    ``CLAUDE.md`` / ``AGENTS.md`` / ``GEMINI.md``, ``.claude/rules`` file,
    Copilot ``.instructions.md`` and Cursor ``.mdc`` rule, graded with the same
    grader, clock and skills detection as the root files, plus the
    always-loaded budget per tool and the scope-integrity findings. The root
    locations keep their entries in ``instruction_files`` and are not graded
    twice. ``excludes`` is the ``(dirs, patterns)`` pair from
    ``.assess/config.toml``.
    """
    repo_root = repo_root.resolve()
    extra_dirs, extra_pats = excludes if excludes is not None else (set(), [])
    return discover_nested_instructions(
        repo_root,
        tracked=tracked_files(repo_root),
        clock=content_clock(repo_root),
        skills_present=bool(detect_skills_dir(repo_root)["skills_dirs_present"]),
        graded_elsewhere=frozenset(INSTRUCTION_FILE_PATHS),
        extra_exclude_dirs=extra_dirs,
        extra_exclude_patterns=extra_pats,
    )
