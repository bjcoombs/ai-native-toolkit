"""Progressive disclosure of each skill and agent file: size in tokens, description and references (#1045).

A skill loads its whole ``SKILL.md`` every time it triggers, and an agent loads its whole
definition. Anthropic's skill authoring guidance keeps that cost down: a small body, a
description within its limit, and detail in files referenced one level deep. This module
measures each file the ``instruction_placement`` scan reads (``skill_and_agent_files``: the
skills and agents Claude Code loads, plus manifest-less agents) against that guidance and
lists the rules each file breaks. No model is involved, and nothing here sets a condition;
the condition, its per-skill-unit reading and its advice are the next step (#1045).

The tendency it compensates for is accretion: a skill grows a branch at a time, and every
trigger pays for every branch.

**The rules** (``BREAKS``), each a named constant with its source:

- ``body_lines``: the body (the text after the frontmatter) is over ``SKILL_BODY_MAX_LINES``
  (500) lines.
- ``tokens``: the whole file is over ``SKILL_MAX_TOKENS`` (5000) estimated tokens, the
  ``instruction_placement`` threshold (``LOADED_IN_FULL_MIN_TOKENS``), estimated as the
  always-on grading does (``instruction_budget.CHARS_PER_TOKEN``, 4 characters a token).
- ``name_missing``, ``description_missing``: the frontmatter has no ``name`` or no
  ``description`` (both are required).
- ``description_long``: the description is over ``DESCRIPTION_MAX_CHARS`` (1024) characters.
  A YAML block scalar (``description: >`` or ``|``) is unfolded before it is counted.
- ``reference_missing``: a reference names a file that does not exist.
- ``reference_deep``: a referenced file names a further reference that the file itself does
  not, so the detail sits two levels deep.

Sources: the 500-line body, the required name and description, the 1024-character
description, references one level deep and the table of contents past about 100 lines are
from https://docs.claude.com/en/docs/agents-and-tools/agent-skills/best-practices; the 5000
tokens from https://docs.claude.com/en/docs/agents-and-tools/agent-skills/overview (level 2,
the instructions a skill loads when it triggers).

**References, precision over recall.** A reference is a path into the file's own folder whose
first part is ``references/``, outside fenced code: a Markdown link
(``[text](references/x.md)``, resolved against the linking file's folder), a
``${CLAUDE_SKILL_DIR}/references/...`` path, or a bare backticked ``references/...`` path. The
first two name the file's own folder, so a missing target is ``reference_missing``; a bare
backticked path may name another skill's folder (the prose says "the fleet skill's
``references/level.schema.json``"), so it counts only when it resolves. Other links are not references: a link out of
the folder (``../../docs/windows.md``) and a path the skill writes as output
(``./complexity-heatmap.svg``) are not the skill's own detail, and counting them would report
a working skill as broken. A referenced Markdown file is read for its own references one hop
down (depth 2). Each reference records ``resolved``, its ``depth``, its ``lines`` and, past
``REFERENCE_TOC_MIN_LINES`` (100) lines, whether it has a table of contents (``toc``: a
"Contents" heading, or ``TOC_MIN_ANCHORS`` in-page links in its first ``TOC_WINDOW_LINES``
lines). ``toc`` is recorded, not a rule.

Block (``skill_disclosure`` in run-context.json)::

    {"available": True,
     "thresholds": {"body_lines": 500, "tokens": 5000, "description_chars": 1024,
                    "reference_toc_lines": 100},
     "files": [{"path", "kind" (skill | agent), "total_lines", "body_lines", "est_tokens",
                "name_present", "description_present", "description_chars" (int | None),
                "references": [{"target", "resolved", "depth", "lines" (int | None),
                                "toc" (bool | None)}],
                "breaks": [rule, ...]}],
     "files_considered": int, "files_breaking": int}

With ``scope`` (a ``--scope`` folder, or a skill unit's folder in the unit pass), the files are
the repository's, kept when they sit under it: discovery starts at the repository root, since a
skill folder holds no plugin manifest or ``.claude/`` of its own. ``files`` is sorted by path. A file that cannot be read is left out, so ``files_considered``
counts the files listed. With no skill or agent file the list is empty and
``files_considered`` is 0, which the condition reads as not measured, never as met.

Skill and agent disclosure (#1045), no model. `scan_skill_disclosure(repo_root, scope,
excludes)` reads each file `instruction_placement.skill_and_agent_files` lists and records
`body_lines` (after the frontmatter), `est_tokens` (whole file, `instruction_placement.est_tokens`),
name and description presence, `description_chars` (block scalars unfolded,
`description_text`), and its `references` (`find_references`: `references/` paths in Markdown
links or backticks outside fences, one hop down for depth 2; `resolved`, `lines`, `toc` past
100 lines via `has_toc`), and `breaks` from `BREAKS` against `SKILL_BODY_MAX_LINES` (500),
`SKILL_MAX_TOKENS` (5000), `DESCRIPTION_MAX_CHARS` (1024). Tests: `tests/test_skill_disclosure.py`.
"""
from __future__ import annotations

import posixpath
import re
from collections.abc import Iterator
from pathlib import Path
from typing import TypedDict

from lib.claude_config import parse_frontmatter
from lib.instruction_placement import LOADED_IN_FULL_MIN_TOKENS, est_tokens, fence_mask, skill_and_agent_files

# --- thresholds (#1045; sources in the module doc) -------------------------------------

SKILL_BODY_MAX_LINES = 500
SKILL_MAX_TOKENS = LOADED_IN_FULL_MIN_TOKENS  # 5000, the Agent Skills overview's level-2 budget
DESCRIPTION_MAX_CHARS = 1024
REFERENCE_TOC_MIN_LINES = 100
# A table of contents: a "Contents" heading, or this many in-page links near the top.
TOC_MIN_ANCHORS = 3
TOC_WINDOW_LINES = 40

BREAKS = ("body_lines", "tokens", "name_missing", "description_missing", "description_long",
          "reference_missing", "reference_deep")
REFERENCE_DIR = "references"

_LINK = re.compile(r"\[[^\]]*\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)")
_TICKED = re.compile(r"`(\$\{CLAUDE_SKILL_DIR\}/|\./)?(references/[^`\s]+)`")
_TOC_HEADING = re.compile(r"^#{1,6}\s+(?:table of\s+)?contents\b", re.IGNORECASE)
_ANCHOR = re.compile(r"\]\(#[^)]+\)")
_BLOCK_SCALAR = re.compile(r"^[>|][+-]?$")
_COMMENT = re.compile(r"\s+#.*$")  # a plain scalar's trailing comment, as YAML drops it


class SkillReference(TypedDict):
    target: str  # the referenced path, relative to the file's folder (POSIX)
    resolved: bool
    depth: int  # 1: named by the file itself; 2: named only by a file it references
    lines: int | None  # None when not resolved
    toc: bool | None  # None at or under REFERENCE_TOC_MIN_LINES, or not resolved


class SkillFile(TypedDict):
    path: str
    kind: str  # skill | agent
    total_lines: int
    body_lines: int
    est_tokens: int
    name_present: bool
    description_present: bool
    description_chars: int | None
    references: list[SkillReference]
    breaks: list[str]


class DisclosureThresholds(TypedDict):
    body_lines: int
    tokens: int
    description_chars: int
    reference_toc_lines: int


class SkillDisclosureBlock(TypedDict):
    """``run-context.json`` ``skill_disclosure`` (see the module doc)."""

    available: bool
    thresholds: DisclosureThresholds
    files: list[SkillFile]
    files_considered: int
    files_breaking: int


# --- frontmatter -----------------------------------------------------------------------


def _frontmatter_end(lines: list[str]) -> int:
    """The index of the first body line: after a closed leading ``---`` block, else 0."""
    if not lines or lines[0].lstrip("﻿").rstrip() != "---":
        return 0
    for i in range(1, len(lines)):
        if lines[i].rstrip() in ("---", "..."):
            return i + 1
    return 0


def description_text(text: str) -> str | None:
    """The frontmatter ``description``, with its continuation lines.

    ``parse_frontmatter`` keeps only a key's first line, so the indented lines after it are
    gathered here: a block scalar (``>`` folds them with spaces, ``|`` keeps the line breaks)
    or a plain or quoted value that wraps (YAML folds it with spaces). Outer quotes and a
    plain value's trailing ``# comment`` are dropped, as the parser drops them on one line."""
    fm = parse_frontmatter(text)
    entry = next((e for e in fm.entries if e.key == "description"), None) if fm else None
    if entry is None:
        return None
    lines = text.splitlines()
    end = _frontmatter_end(lines) - 1  # the closing fence's index
    raw = lines[entry.line - 1].split(":", 1)[1].strip()
    more: list[str] = []
    for line in lines[entry.line:end]:
        if line.strip() and not line[:1].isspace():
            break
        more.append(line.strip())
    if _BLOCK_SCALAR.match(raw):
        joined = (" " if raw.startswith(">") else "\n").join(more).strip()
    elif not any(more):
        return entry.value  # one line: the parser's own reading (quotes and a comment dropped)
    elif raw[:1] in "\"'":
        joined = " ".join(part for part in [raw, *more] if part)
        quote = joined.rfind(raw[0])
        joined = joined[1:quote] if quote > 0 else joined[1:]  # a comment may follow the close
    else:
        joined = " ".join(_COMMENT.sub("", part) for part in [raw, *more] if part)
    return joined or None


# --- references ------------------------------------------------------------------------


def _targets(text: str, here: str = ".") -> Iterator[tuple[str, bool]]:
    """The ``references/`` paths ``text`` names outside fenced code, relative to the skill folder.

    A Markdown link resolves against ``here``, the folder of the file that holds it (``.``
    for the skill file itself); a backticked path is written from the skill folder, as
    ``${CLAUDE_SKILL_DIR}`` paths are. Targets outside ``references/`` are not yielded. Each
    comes with ``explicit``: a link or a ``${CLAUDE_SKILL_DIR}/`` path names the file's own
    folder; a bare backticked path may name another skill's (``the fleet skill's
    `references/x.json` ``), so it counts only when it resolves (``find_references``)."""
    lines = text.splitlines()
    for line, fenced in zip(lines, fence_mask(lines)):
        if fenced:
            continue
        for match in _LINK.finditer(line):
            target = match.group(1).split("#", 1)[0]
            if not target or "://" in target or target.startswith(("/", "mailto:", "$")):
                continue
            joined = posixpath.normpath(posixpath.join(here, target))
            if joined.startswith(f"{REFERENCE_DIR}/"):
                yield joined, True
        for match in _TICKED.finditer(line):
            yield posixpath.normpath(match.group(2).split("#", 1)[0]), match.group(1) == "${CLAUDE_SKILL_DIR}/"


def _inside(folder: Path, target: str) -> Path | None:
    path = (folder / target).resolve()
    return path if path.is_relative_to(folder.resolve()) else None


def has_toc(text: str) -> bool:
    """A "Contents" heading anywhere, or ``TOC_MIN_ANCHORS`` in-page links near the top."""
    lines = text.splitlines()
    if any(_TOC_HEADING.match(line) for line in lines):
        return True
    return sum(len(_ANCHOR.findall(line)) for line in lines[:TOC_WINDOW_LINES]) >= TOC_MIN_ANCHORS


def _reference(folder: Path, target: str, depth: int) -> tuple[SkillReference, str | None]:
    path = _inside(folder, target)
    text = None
    if path is not None and path.is_file():
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = None
    lines = len(text.splitlines()) if text is not None else None
    toc = has_toc(text) if text is not None and lines is not None and lines > REFERENCE_TOC_MIN_LINES else None
    return SkillReference(target=target, resolved=text is not None, depth=depth, lines=lines, toc=toc), text


def _named(text: str, here: str = ".") -> list[str]:
    """The distinct ``references/`` targets ``text`` names, explicit ones first seen wins."""
    out: dict[str, bool] = {}
    for target, explicit in _targets(text, here):
        out[target] = out.get(target, False) or explicit
    return [t for t in out if out[t]] + [t for t in out if not out[t]]


def find_references(path: Path, text: str) -> list[SkillReference]:
    """The file's references (depth 1) and the further ones its Markdown references name (depth 2).

    A bare backticked path that does not resolve is left out (see ``_targets``)."""
    folder = path.parent
    explicit = {t for t, e in _targets(text) if e}
    out: list[SkillReference] = []
    direct = _named(text)
    seen = set(direct)
    for target in direct:
        ref, body = _reference(folder, target, 1)
        if not ref["resolved"] and target not in explicit:
            continue
        out.append(ref)
        if body is None or not target.endswith(".md"):
            continue
        here = posixpath.dirname(target)
        nested_explicit = {t for t, e in _targets(body, here) if e}
        for nested in _named(body, here):
            if nested in seen:
                continue
            seen.add(nested)
            nested_ref = _reference(folder, nested, 2)[0]
            if nested_ref["resolved"] or nested in nested_explicit:
                out.append(nested_ref)
    out.sort(key=lambda r: (r["depth"], r["target"]))
    return out


# --- the scan --------------------------------------------------------------------------


def _breaks(entry: SkillFile) -> list[str]:
    checks = {
        "body_lines": entry["body_lines"] > SKILL_BODY_MAX_LINES,
        "tokens": entry["est_tokens"] > SKILL_MAX_TOKENS,
        "name_missing": not entry["name_present"],
        "description_missing": not entry["description_present"],
        "description_long": (entry["description_chars"] or 0) > DESCRIPTION_MAX_CHARS,
        "reference_missing": any(not r["resolved"] for r in entry["references"]),
        "reference_deep": any(r["depth"] > 1 for r in entry["references"]),
    }
    return [rule for rule in BREAKS if checks[rule]]


def measure(path: Path, rel: str, kind: str, text: str) -> SkillFile:
    """One file's record (see ``SkillFile``)."""
    lines = text.splitlines()
    fm = parse_frontmatter(text)
    keys = {e.key for e in fm.entries} if fm else set()
    description = description_text(text)
    entry = SkillFile(
        path=rel, kind=kind, total_lines=len(lines), body_lines=len(lines) - _frontmatter_end(lines),
        est_tokens=est_tokens(text), name_present="name" in keys,
        description_present=description is not None,
        description_chars=len(description) if description is not None else None,
        references=find_references(path, text), breaks=[])
    entry["breaks"] = _breaks(entry)
    return entry


def scan_skill_disclosure(
    repo_root: Path, scope: Path | None = None, excludes: tuple[set[str], list[str]] | None = None,
) -> SkillDisclosureBlock:
    """Build the ``skill_disclosure`` block (see the module doc)."""
    repo = repo_root.resolve()
    folder = scope.resolve() if scope is not None else repo
    files: list[SkillFile] = []
    # The repository's files, then those under the scope: a skill folder holds no plugin
    # manifest or .claude/ of its own, so discovery from the scope would find nothing there.
    for path, kind in skill_and_agent_files(repo, None, excludes):  # excludes already applied
        if not path.resolve().is_relative_to(folder):
            continue
        rel = path.relative_to(repo)
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        files.append(measure(path, rel.as_posix(), kind, text))
    files.sort(key=lambda f: f["path"])
    return SkillDisclosureBlock(
        available=True,
        thresholds=DisclosureThresholds(body_lines=SKILL_BODY_MAX_LINES, tokens=SKILL_MAX_TOKENS,
                                        description_chars=DESCRIPTION_MAX_CHARS,
                                        reference_toc_lines=REFERENCE_TOC_MIN_LINES),
        files=files, files_considered=len(files), files_breaking=sum(bool(f["breaks"]) for f in files))
