"""Per-section placement of always-loaded instruction text (issue #559, part of #419).

The grader scores an instruction file on its whole size, so its advice is
generic ("use skills"). This module splits each file on its headings and
classifies every section as always-on (who the agent is here, hard rules,
pointer lists) or fetch-on-need (reference material for one tool, language or
subtree, a block of commands, text an existing ``SKILL.md`` already holds), so
the report can name the sections to move, with line ranges and a suggested
home. No model is involved, and nothing here changes a grade: the block is
evidence and advice only (the score on always-on size is #560).

Precision over recall. A section is relocatable only when the text is already
in a skill, or when at least ``MIN_FETCH_SIGNALS`` fetch-on-need signals fire
and no always-on signal does. Anything else stays where it is, and a section
that matches nothing clearly is ``unclassified`` and counted as always-on.

Two lists. ``files`` covers the repository instruction files (the root
locations and the nested and path-scoped files ``nested_instructions``
grades). ``loaded_in_full`` covers agent definitions and ``SKILL.md`` files
over ``LOADED_IN_FULL_MIN_LINES``, which load whole when invoked; it is
informational and reported separately.

The tendency it compensates for is accretion: an always-loaded file grows a
section at a time, and every session pays for the growth.
"""
from __future__ import annotations

import re
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Literal, TypedDict

from lib.agent_instructions_grader import detect_skills_dir
from lib.assess_config import is_user_excluded
from lib.claude_config import loaded_config_files
from lib.instruction_content import path_references, readme_overlap_pct

# --- thresholds (maintainer decisions on #419) ----------------------------------

# Flag a file when its always-on content exceeds 200 lines (the point the size
# curve from #517 starts at, and Claude Code's own CLAUDE.md guidance) and is
# more than half the file.
ALWAYS_ON_FLAG_LINES = 200
ALWAYS_ON_FLAG_SHARE = 0.5
# Agent and skill files are reported only past this size; below it there is
# little to gain from moving a section out.
LOADED_IN_FULL_MIN_LINES = 200

# --- fetch-on-need signals -------------------------------------------------------

# command_reference: a section that is mostly fenced code is reference to look
# up when running that tool, not guidance to hold in mind. A few lines of
# exact commands are what earns a file its grade, so a floor keeps a short
# "Commands" block always-on.
FENCE_DENSITY_MIN = 0.5
FENCE_MIN_LINES = 20
# long_section: one section holding a quarter of the file is a topic that
# outgrew the file. The floor stops a 12-line section of a 40-line file firing.
LONG_SECTION_SHARE = 0.25
LONG_SECTION_MIN_LINES = 40
# duplicates_skill: share of the section's substantive lines (30+ characters,
# markup stripped, at least five) also in an existing SKILL.md - the line
# overlap the README check in lib.instruction_content uses. The text is already
# on disk, so a pointer replaces it with nothing lost.
SKILL_OVERLAP_MIN_PCT = 50
# scoped_paths: most of the section's backticked paths sit under one top-level
# directory, so the guidance is for work in that subtree and a nested
# instruction file there loads it only when it applies.
DOMINANT_PATH_MIN_REFS = 3
DOMINANT_PATH_SHARE = 0.75
# scoped_heading: the heading names one tool, language or platform.
SCOPED_HEADING_TERMS = frozenset("""
kubernetes k8s kubectl helm docker compose terraform ansible pulumi aws gcp azure
gcloud python java kotlin scala go golang rust ruby rails php javascript typescript
node nodejs react vue angular svelte nextjs django flask fastapi spring dart flutter
swift ios android graphql grpc postgres postgresql mysql sqlite mongodb redis kafka
elasticsearch nginx airflow spark dbt jenkins gradle maven npm yarn pnpm webpack
vite storybook playwright cypress jest pytest junit
""".split())
# A section needs this many of the signals above (duplicates_skill excepted).
MIN_FETCH_SIGNALS = 2

# --- always-on signals -------------------------------------------------------------

# identity_or_rules: the heading names the agent's role, the hard rules or the
# corrections to default behaviour for this environment.
_ALWAYS_ON_HEADING = re.compile(
    r"\b(?:role|identity|persona|who you are|you are|rules?|invariants?|constraints?|"
    r"guardrails?|must|never|always|do not|don'?t|critical|important|safety|"
    r"conventions?|gotchas?|pitfalls?|quirks?|principles?|scope)\b",
    re.IGNORECASE,
)
# hard_rules: this share of the section's prose lines (two at least) open with
# a directive or carry an all-caps one.
DIRECTIVE_SHARE = 0.3
_DIRECTIVE = re.compile(
    r"^\s*(?:[-*+]|\d+[.)])?\s*(?:\*\*)?(?:never|always|do not|don'?t|must|only)\b"
    r"|(?-i:\b(?:MUST|NEVER|ALWAYS|DO NOT)\b)",
    re.IGNORECASE,
)
# pointer_list: this share of the prose lines (three at least) are list items
# that point at a document (a markdown link or a backticked .md, .rst, .txt or
# .adoc path). A list naming code files is reference about that code, not a
# pointer, so it does not count.
POINTER_LIST_SHARE = 0.6
_LIST_ITEM = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")
_POINTER = re.compile(r"\]\(|`[^`\s]+\.(?:md|mdx|rst|txt|adoc)`", re.IGNORECASE)

MAX_RELOCATABLE_PER_FILE = 10

_FENCE = re.compile(r"^\s*(`{3,}|~{3,})(.*)$")
_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_INSTRUCTION_BASENAMES = ("CLAUDE.md", "AGENTS.md", "GEMINI.md")

Status = Literal["always_on", "fetch_on_need", "unclassified"]
FileKind = Literal["instruction", "agent", "skill"]


class SuggestedHome(TypedDict):
    """Where a relocatable section should live.

    ``kind``: ``skill`` (a new skill at ``target``), ``nested_instruction_file``
    (an instruction file in the subtree the section is about), ``existing_skill``
    (a ``SKILL.md`` that already holds the text: replace the section with a
    pointer) or ``reference_doc`` (a document the skill or agent reads on need;
    ``target`` is None for an agent, whose directory holds agents only).
    """

    kind: str
    target: str | None


class RelocatableSection(TypedDict):
    heading: str
    start_line: int
    end_line: int
    lines: int
    signals: list[str]
    home: SuggestedHome


class PlacementFile(TypedDict):
    """One file's split: line counts per status and the sections to move."""

    path: str
    kind: str
    total_lines: int
    always_on_lines: int
    always_on_share: float
    fetch_on_need_lines: int
    unclassified_lines: int
    sections_total: int
    flagged: bool
    relocatable: list[RelocatableSection]
    relocatable_total: int


class PlacementThresholds(TypedDict):
    always_on_lines: int
    always_on_share: float
    loaded_in_full_min_lines: int


class InstructionPlacementBlock(TypedDict):
    """``run-context.json`` ``instruction_placement`` (see the module doc)."""

    available: bool
    thresholds: PlacementThresholds
    files: list[PlacementFile]
    flagged: list[str]
    loaded_in_full: list[PlacementFile]
    loaded_in_full_considered: int


@dataclass(frozen=True)
class Section:
    heading: str
    level: int
    start: int  # 1-based, inclusive
    end: int
    lines: tuple[str, ...]


@dataclass(frozen=True)
class Classified:
    section: Section
    status: Status
    signals: tuple[str, ...]
    home: SuggestedHome | None


# --- splitting -----------------------------------------------------------------------

def fence_mask(lines: list[str] | tuple[str, ...]) -> list[bool]:
    """True for each line that is a code fence or inside one (CommonMark rules).

    A fence closes only on a line of the opening character at least as long as
    the opening run, with nothing after it but whitespace, so a ```` ``` ```` line
    inside a ``~~~`` block or a shorter run inside a longer fence stays code.
    An unclosed fence runs to the end of the text.
    """
    mask: list[bool] = []
    opening: str | None = None
    for line in lines:
        match = _FENCE.match(line)
        if opening is None:
            if match and not (match.group(1)[0] == "`" and "`" in match.group(2)):
                opening = match.group(1)
            mask.append(opening is not None)
            continue
        mask.append(True)
        if (match and match.group(1)[0] == opening[0]
                and len(match.group(1)) >= len(opening) and not match.group(2).strip()):
            opening = None
    return mask


def _headings(lines: list[str]) -> list[tuple[int, int, str]]:
    """(0-based line, level, text) for each ATX heading outside fences and frontmatter."""
    out: list[tuple[int, int, str]] = []
    start = 0
    if lines and lines[0].strip() == "---":
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                start = i + 1
                break
    code = fence_mask(lines[start:])
    for i in range(start, len(lines)):
        match = None if code[i - start] else _HEADING.match(lines[i])
        if match:
            out.append((i, len(match.group(1)), match.group(2)))
    return out


def _split_level(headings: list[tuple[int, int, str]]) -> int:
    """The heading level that starts a section; deeper headings roll into it.

    The shallowest level, unless a single heading holds it (a document title),
    in which case the next level down.
    """
    levels = [lvl for _, lvl, _ in headings]
    top = min(levels)
    deeper = [lvl for lvl in levels if lvl > top]
    if levels.count(top) == 1 and deeper:
        return min(deeper)
    return top


def split_sections(text: str) -> list[Section]:
    """Split ``text`` into sections covering every line, in order.

    Text before the first heading is a section with an empty heading; a file
    with no headings is one such section.
    """
    lines = text.splitlines()
    if not lines:
        return []
    headings = _headings(lines)
    if not headings:
        return [Section("", 0, 1, len(lines), tuple(lines))]
    level = _split_level(headings)
    starts = [(i, lvl, h) for i, lvl, h in headings if lvl <= level]
    if starts[0][0] > 0:
        starts.insert(0, (0, 0, ""))
    sections: list[Section] = []
    for n, (i, lvl, heading) in enumerate(starts):
        end = starts[n + 1][0] if n + 1 < len(starts) else len(lines)
        sections.append(Section(heading, lvl, i + 1, end, tuple(lines[i:end])))
    return sections


# --- signals ---------------------------------------------------------------------------

def _fenced_and_prose(lines: tuple[str, ...]) -> tuple[int, list[str]]:
    code = fence_mask(lines)
    prose = [line for line, in_code in zip(lines, code, strict=True)
             if not in_code and line.strip() and not _HEADING.match(line)]
    return sum(code), prose


def dominant_directory(section: Section) -> str | None:
    """The top-level directory most of the section's backticked paths sit under."""
    tops = [ref.text.split("/", 1)[0] for ref in path_references("\n".join(section.lines))
            if "/" in ref.text.rstrip("/")]
    if len(tops) < DOMINANT_PATH_MIN_REFS:
        return None
    top, count = Counter(tops).most_common(1)[0]
    total = len(path_references("\n".join(section.lines)))
    return top if count / total >= DOMINANT_PATH_SHARE and top not in (".", "..") else None


def _duplicated_skill(section: Section, skills: Mapping[str, str], self_rel: str) -> str | None:
    text = "\n".join(section.lines)
    for rel, skill_text in sorted(skills.items()):
        if rel != self_rel and readme_overlap_pct(text, skill_text) >= SKILL_OVERLAP_MIN_PCT:
            return rel
    return None


def always_on_signals(section: Section) -> list[str]:
    signals: list[str] = []
    if section.heading and _ALWAYS_ON_HEADING.search(section.heading):
        signals.append("identity_or_rules")
    _fenced, prose = _fenced_and_prose(section.lines)
    directives = sum(1 for line in prose if _DIRECTIVE.search(line))
    if directives >= 2 and directives / max(len(prose), 1) >= DIRECTIVE_SHARE:
        signals.append("hard_rules")
    pointers = sum(1 for line in prose if _LIST_ITEM.match(line) and _POINTER.search(line))
    if pointers >= 3 and pointers / max(len(prose), 1) >= POINTER_LIST_SHARE:
        signals.append("pointer_list")
    return signals


def fetch_signals(section: Section, file_lines: int) -> list[str]:
    signals: list[str] = []
    words = set(re.findall(r"[a-z0-9]+", section.heading.lower()))
    if words & SCOPED_HEADING_TERMS:
        signals.append("scoped_heading")
    if dominant_directory(section):
        signals.append("scoped_paths")
    fenced, _prose = _fenced_and_prose(section.lines)
    size = len(section.lines)
    if fenced >= FENCE_MIN_LINES and fenced / size >= FENCE_DENSITY_MIN:
        signals.append("command_reference")
    if size >= LONG_SECTION_MIN_LINES and size / max(file_lines, 1) >= LONG_SECTION_SHARE:
        signals.append("long_section")
    return signals


# --- classification --------------------------------------------------------------------

def _slug(heading: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", heading.lower()).strip("-")[:40].strip("-")
    return slug or "section"


def suggest_home(
    section: Section, rel: str, kind: FileKind, signals: list[str], skills_dir: str,
) -> SuggestedHome:
    slug = _slug(section.heading)
    if kind == "skill":
        return {"kind": "reference_doc",
                "target": f"{PurePosixPath(rel).parent.as_posix()}/references/{slug}.md"}
    if kind == "agent":
        return {"kind": "reference_doc", "target": None}
    directory = dominant_directory(section) if "scoped_paths" in signals else None
    if directory:
        name = PurePosixPath(rel).name
        basename = name if name in _INSTRUCTION_BASENAMES else "CLAUDE.md"
        return {"kind": "nested_instruction_file", "target": f"{directory}/{basename}"}
    return {"kind": "skill", "target": f"{skills_dir}/{slug}/SKILL.md"}


def classify(
    section: Section, file_lines: int, rel: str, kind: FileKind,
    skills: Mapping[str, str], skills_dir: str = ".claude/skills",
) -> Classified:
    duplicate = _duplicated_skill(section, skills, rel)
    if duplicate:
        return Classified(section, "fetch_on_need", ("duplicates_skill",),
                          {"kind": "existing_skill", "target": duplicate})
    keep = always_on_signals(section)
    if keep:
        return Classified(section, "always_on", tuple(keep), None)
    fetch = fetch_signals(section, file_lines)
    if len(fetch) >= MIN_FETCH_SIGNALS:
        return Classified(section, "fetch_on_need", tuple(fetch),
                          suggest_home(section, rel, kind, fetch, skills_dir))
    return Classified(section, "unclassified", tuple(fetch), None)


def analyse_file(
    text: str, rel: str, kind: FileKind, skills: Mapping[str, str],
    skills_dir: str = ".claude/skills",
) -> PlacementFile:
    """One file's placement record (see ``PlacementFile``)."""
    total = len(text.splitlines())
    results = [classify(s, total, rel, kind, skills, skills_dir) for s in split_sections(text)]
    lines_by: Counter[str] = Counter()
    for c in results:
        lines_by[c.status] += len(c.section.lines)
    always_on = total - lines_by["fetch_on_need"]
    share = round(always_on / total, 2) if total else 0.0
    moves: list[RelocatableSection] = [
        {"heading": c.section.heading, "start_line": c.section.start,
         "end_line": c.section.end, "lines": len(c.section.lines),
         "signals": list(c.signals), "home": c.home}
        for c in results if c.status == "fetch_on_need" and c.home is not None
    ]
    moves.sort(key=lambda m: -m["lines"])
    return {
        "path": rel, "kind": kind, "total_lines": total,
        "always_on_lines": always_on, "always_on_share": share,
        "fetch_on_need_lines": lines_by["fetch_on_need"],
        "unclassified_lines": lines_by["unclassified"],
        "sections_total": len(results),
        "flagged": always_on > ALWAYS_ON_FLAG_LINES and share > ALWAYS_ON_FLAG_SHARE,
        "relocatable": moves[:MAX_RELOCATABLE_PER_FILE],
        "relocatable_total": len(moves),
    }


# --- the scan ----------------------------------------------------------------------------

def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _instruction_paths(
    instruction_files: Mapping[str, Any], nested: Any,
) -> list[str]:
    paths = [rel for rel, entry in instruction_files.items()
             if isinstance(entry, Mapping) and entry.get("present")
             and not entry.get("is_alias")]
    if isinstance(nested, Mapping) and nested.get("available"):
        paths.extend(f["path"] for f in nested.get("files", [])
                     if isinstance(f, Mapping) and isinstance(f.get("path"), str))
    return paths


def scan_instruction_placement(
    repo_root: Path,
    instruction_files: Mapping[str, Any],
    nested_instructions: Any,
    scope: Path | None = None,
    excludes: tuple[set[str], list[str]] | None = None,
) -> InstructionPlacementBlock:
    """Build the ``instruction_placement`` block (see the module doc)."""
    repo = repo_root.resolve()
    dirs, pats = excludes or (set(), [])
    config = [(p, k) for p, k in loaded_config_files(scope or repo)
              if k in ("skill", "agent") and p.is_relative_to(repo)
              and not is_user_excluded(p.relative_to(repo), dirs, pats)]
    skill_info = detect_skills_dir(repo)
    skills: dict[str, str] = {}
    for rel in skill_info["skill_files"]:
        text = _read(repo / rel)
        if text is not None:
            skills[Path(rel).as_posix()] = text
    for path, kind in config:
        rel = path.relative_to(repo).as_posix()
        if kind == "skill" and rel not in skills:
            text = _read(path)
            if text is not None:
                skills[rel] = text
    skills_dir = skill_info["skills_dirs"][0] if skill_info["skills_dirs"] else ".claude/skills"

    seen: set[Path] = set()
    files: list[PlacementFile] = []
    for rel in _instruction_paths(instruction_files, nested_instructions):
        real = (repo / rel).resolve()
        text = _read(repo / rel)
        if text is None or real in seen:
            continue
        seen.add(real)
        files.append(analyse_file(text, rel, "instruction", skills, skills_dir))

    loaded: list[PlacementFile] = []
    for path, kind in config:
        text = _read(path)
        if text is None or len(text.splitlines()) <= LOADED_IN_FULL_MIN_LINES:
            continue
        file_kind: FileKind = "skill" if kind == "skill" else "agent"
        loaded.append(analyse_file(text, path.relative_to(repo).as_posix(), file_kind,
                                   skills, skills_dir))
    loaded.sort(key=lambda f: (-f["total_lines"], f["path"]))
    return {
        "available": True,
        "thresholds": {
            "always_on_lines": ALWAYS_ON_FLAG_LINES,
            "always_on_share": ALWAYS_ON_FLAG_SHARE,
            "loaded_in_full_min_lines": LOADED_IN_FULL_MIN_LINES,
        },
        "files": files,
        "flagged": [f["path"] for f in files if f["flagged"]],
        "loaded_in_full": loaded,
        "loaded_in_full_considered": len(config),
    }
