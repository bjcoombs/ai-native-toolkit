"""Per-section placement of always-loaded instruction text (issue #559, part of #419).

The grader scores an instruction file on its whole size, so its advice is
generic ("use skills"). This module splits each file on its headings and
classifies every section as always-on (who the agent is here, hard rules,
pointer lists) or fetch-on-need (reference material for one tool, language or
subtree, a block of commands, text an existing ``SKILL.md`` already holds), so
the report can name the sections to move, with line ranges and a suggested
home. No model is involved. The block is the advice; the grader scores each
instruction file on the same split (``analyse_file``, issue #560): always-on
lines past the flag point and lines that belong elsewhere cost points.

Precision over recall. An always-on signal keeps a section where it is, even
when a skill restates it, unless a size signal (``command_reference`` or
``long_section``) is among the fetch-on-need signals that make it relocatable:
those count lines, so a rename or a few directive lines cannot flip the
section and lower the file's penalty (#619). Otherwise a section is relocatable only when the text
is already in a skill, or when at least ``MIN_FETCH_SIGNALS`` fetch-on-need
signals fire. Anything else stays where it is, and a section that matches
nothing clearly is ``unclassified`` and counted as always-on.

Two lists. ``files`` covers the repository instruction files (the root
locations and the nested and path-scoped files ``nested_instructions``
grades). ``loaded_in_full`` covers agent definitions and ``SKILL.md`` files
over ``LOADED_IN_FULL_MIN_LINES``, which load whole when invoked; it is
informational and reported separately. A ``.md`` file under the root ``agents/`` shaped as
an agent definition counts even without a plugin manifest (#662).

The tendency it compensates for is accretion: an always-loaded file grows a
section at a time, and every session pays for the growth.

Per-section placement of instruction text (#559, part of #419), no model. The
grader scores each instruction file on the same split (`analyse_file`, #560); the
block itself is advice. `split_sections` splits a file on ATX headings outside fences (`fence_mask`, CommonMark closing: same character, at least the opening length, nothing after) and
frontmatter, at the shallowest level (the next one down when a single heading is a
document title); deeper headings roll into their section, text before the first
heading is a section with an empty heading, and a file with no headings is one
section. `classify` labels each section `always_on`, `fetch_on_need` or
`unclassified` (counted as always-on), precision over recall: any always-on signal
(`identity_or_rules` heading, `hard_rules` directive density, `pointer_list` of links
to documents) keeps a section, even one a skill restates, so a guardrail is never
told to become a pointer, unless the section is relocatable with a size signal
(`SIZE_SIGNALS`: `command_reference` or `long_section`) among its fetch signals, so a
heading rename or a few `Never` lines cannot flip it (#619); otherwise a section whose substantive lines overlap an
existing `SKILL.md` by `SKILL_OVERLAP_MIN_PCT` (the
`instruction_content.readme_overlap_pct` line overlap) is `duplicates_skill`, and
any other section moves only when
`MIN_FETCH_SIGNALS` of `scoped_heading` (one tool, language or platform),
`scoped_paths` (backticked paths mostly under one top-level directory, unless the
file already sits under it),
`command_reference` (`FENCE_MIN_LINES` fenced lines or more, an absolute count so
prose padding cannot dilute it, #619) and `long_section` (60 lines or more, an absolute count so that
lines elsewhere cannot declassify a section, in a file with at least two headed
sections) fire. Each threshold is a
named constant with its rationale. Suggested homes: `existing_skill` (the
duplicated `SKILL.md`), `nested_instruction_file` (`<dir>/<basename>` for
`scoped_paths`), `skill` (`<skills dir>/<slug>/SKILL.md`), and for agent and skill
files `reference_doc` (`<skill dir>/references/<slug>.md`, or `null` for an agent), or
`existing_skill` when the text is already in another skill. Between two skills the
pointer is one-way: a skill counts as the duplicate's home only when its path sorts
before the file's, so the lexically first keeps the canonical copy.
`detect_skills_dir` lists the skills directories and `SKILL.md` files (the
`skills_present` evidence); `skill_texts(repo_root)` returns every `SKILL.md` text by
path, the map the scan and the grader's callers both compare sections against.
`scan_instruction_placement(repo_root, instruction_files, nested_instructions,
scope, excludes)` runs from the `scan_registry` table. The block:
`{available, thresholds {always_on_lines, always_on_share,
loaded_in_full_min_lines}, files[], flagged[], loaded_in_full[],
loaded_in_full_considered}`. Each file record (`files`: the root instruction files
that are present and not aliases, plus `nested_instructions.files`, each real file
once; `loaded_in_full`: agent and `SKILL.md` files from
`claude_config.loaded_config_files`, plus root `agents/**/*.md` files shaped as agents
when there is no `.claude-plugin/plugin.json` (#662: `name` or `description`
frontmatter, or no frontmatter and not a `claude_config.DOC_STEMS` name such as
`README`; frontmatter without either key marks a document), over 200 lines, largest first) is `{path, kind
(instruction | agent | skill), total_lines, always_on_lines, always_on_share,
always_on_words, fetch_on_need_lines, unclassified_lines, sections_total, flagged,
relocatable[],
relocatable_total}`, where `flagged` (instruction files only; always `false` in `loaded_in_full`) is always-on lines over 200 and share over
0.5 and each `relocatable` entry (largest first, capped at 10) is `{heading,
start_line, end_line, lines, signals[], home {kind, target}}`. `flagged` at block
level lists the instruction files only; `loaded_in_full` is informational.
Tests: `tests/test_instruction_placement.py`.
"""
from __future__ import annotations

import re
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Literal, TypedDict

from lib.assess_config import is_user_excluded
from lib.claude_config import DOC_STEMS, loaded_config_files, parse_frontmatter
from lib.instruction_content import path_references, readme_overlap_pct
from lib.run_context_types import JsonDict

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

# command_reference: a section holding a block of fenced code is reference to
# look up when running that tool, not guidance to hold in mind. A few lines of
# exact commands are what earns a file its grade, so a floor keeps a short
# "Commands" block always-on. An absolute count, not a share of the section:
# with a share, prose padded around the fence diluted it under half, the
# section turned always-on and the file's penalty fell (#619).
FENCE_MIN_LINES = 20
# long_section: a section of LONG_SECTION_MIN_LINES lines is a topic that
# outgrew an always-loaded file (a third of the 200-line budget on its own).
# An absolute count, not a share of the file: with a share, lines added to an
# unrelated section could shrink this one under the share and lower the
# file's score (#560). It needs two headed sections, so a file with one
# heading (or none), with or without a preamble above it, is never "long".
LONG_SECTION_MIN_LINES = 60
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
# Size signals: they count the section's own lines, so a heading rename or a
# few "Never" lines cannot take them away. A relocatable section with one of
# them stays relocatable whatever always-on signal its wording adds (#619).
SIZE_SIGNALS = frozenset({"command_reference", "long_section"})

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
    always_on_words: int
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
    """The ``SKILL.md`` that already holds the section's text, if any.

    One-way between skills: when the file is itself a skill, only a skill whose
    path sorts before it counts, so of two skills sharing a section the
    lexically first keeps the canonical copy and only the other is told to
    point at it. Following both pointers would delete the text from both.
    """
    text = "\n".join(section.lines)
    is_skill = self_rel in skills
    for rel, skill_text in sorted(skills.items()):
        if rel == self_rel or (is_skill and rel > self_rel):
            continue
        if readme_overlap_pct(text, skill_text) >= SKILL_OVERLAP_MIN_PCT:
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


def fetch_signals(
    section: Section, headed_sections: int = 2, rel: str = "",
) -> list[str]:
    """The fetch-on-need signals for one section.

    ``long_section`` needs ``headed_sections`` (the file's sections that have a
    heading) to be two or more: a section is long only relative to other
    headed sections, so a file with one heading, with or without a preamble
    line above it, is never long relative to itself. ``scoped_paths`` does not
    fire when ``rel`` already sits under the dominant directory: a nested file
    about its own subtree is already where that guidance belongs.
    """
    signals: list[str] = []
    words = set(re.findall(r"[a-z0-9]+", section.heading.lower()))
    if words & SCOPED_HEADING_TERMS:
        signals.append("scoped_heading")
    directory = dominant_directory(section)
    if directory and not rel.startswith(f"{directory}/"):
        signals.append("scoped_paths")
    fenced, _prose = _fenced_and_prose(section.lines)
    size = len(section.lines)
    if fenced >= FENCE_MIN_LINES:
        signals.append("command_reference")
    if headed_sections >= 2 and size >= LONG_SECTION_MIN_LINES:
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
    section: Section, rel: str, kind: FileKind,
    skills: Mapping[str, str], skills_dir: str = ".claude/skills",
    headed_sections: int = 2,
) -> Classified:
    # Always-on signals win, even over a duplicate: a rules, identity or
    # pointer section a skill restates is still a guardrail every session needs,
    # so it is never told to become a pointer. Except over a size signal: those
    # read the section's line counts, which wording cannot shrink, so renaming
    # "Kubernetes commands" to "Kubernetes conventions" or adding two "Never"
    # lines no longer turns a fenced kubectl section always-on (#619).
    fetch = fetch_signals(section, headed_sections, rel)
    relocatable = len(fetch) >= MIN_FETCH_SIGNALS
    keep = always_on_signals(section)
    if keep and not (relocatable and SIZE_SIGNALS.intersection(fetch)):
        return Classified(section, "always_on", tuple(keep), None)
    duplicate = _duplicated_skill(section, skills, rel)
    if duplicate:
        return Classified(section, "fetch_on_need", ("duplicates_skill",),
                          {"kind": "existing_skill", "target": duplicate})
    if relocatable:
        return Classified(section, "fetch_on_need", tuple(fetch),
                          suggest_home(section, rel, kind, fetch, skills_dir))
    return Classified(section, "unclassified", tuple(fetch), None)


def analyse_file(
    text: str, rel: str, kind: FileKind, skills: Mapping[str, str],
    skills_dir: str = ".claude/skills",
) -> PlacementFile:
    """One file's placement record (see ``PlacementFile``)."""
    total = len(text.splitlines())
    sections = split_sections(text)
    headed = sum(1 for s in sections if s.heading)
    results = [classify(s, rel, kind, skills, skills_dir, headed) for s in sections]
    lines_by: Counter[str] = Counter()
    fetch_words = 0
    for c in results:
        lines_by[c.status] += len(c.section.lines)
        if c.status == "fetch_on_need":
            fetch_words += sum(len(line.split()) for line in c.section.lines)
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
        "always_on_words": len(text.split()) - fetch_words,
        "fetch_on_need_lines": lines_by["fetch_on_need"],
        "unclassified_lines": lines_by["unclassified"],
        "sections_total": len(results),
        # Only instruction files are flagged: loaded_in_full is informational
        # and never ranks.
        "flagged": (kind == "instruction" and always_on > ALWAYS_ON_FLAG_LINES
                    and share > ALWAYS_ON_FLAG_SHARE),
        "relocatable": moves[:MAX_RELOCATABLE_PER_FILE],
        "relocatable_total": len(moves),
    }


# --- the scan ----------------------------------------------------------------------------

def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def detect_skills_dir(repo_root: Path) -> JsonDict:
    """Check for the presence of skills directories in the repo.

    Looks for `.claude/skills/` and `skills/` and counts the `*/SKILL.md`
    files within: the ``skills_present`` / ``skill_files`` evidence, and the
    skills a section's text is compared against (``duplicates_skill``).
    """
    skills_paths = [
        repo_root / ".claude" / "skills",
        repo_root / "skills",
    ]
    found_dirs: list[str] = []
    skill_files: list[str] = []
    for sp in skills_paths:
        if sp.is_dir():
            found_dirs.append(sp.relative_to(repo_root).as_posix())
            for skill_md in sp.glob("*/SKILL.md"):
                skill_files.append(skill_md.relative_to(repo_root).as_posix())
    return {
        "skills_dirs_present": len(found_dirs) > 0,
        "skills_dirs": found_dirs,
        "skills_count": len(skill_files),
        "skill_files": skill_files,
    }


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


def _is_agent_definition(path: Path) -> bool:
    """Whether a file under a manifest-less ``agents/`` is shaped as an agent.

    ``name`` or ``description`` frontmatter is an agent definition. No
    frontmatter is one too, as the plugin loader would treat it, unless the
    name is a repository document's (``README``, ``CHANGELOG``: the
    ``stray_markdown`` stems). Frontmatter without either key is a document.
    """
    text = _read(path)
    if text is None:
        return False
    fm = parse_frontmatter(text)
    if fm is None:
        return path.stem.lower() not in DOC_STEMS
    return any(e.key in ("name", "description") for e in fm.entries)


def _manifestless_agents(root: Path) -> list[tuple[Path, str]]:
    """Agent definitions under the root ``agents/`` (recursive) in a repository with no plugin
    manifest (#662). Claude Code loads them only once the directory is
    installed as a plugin, but they are written to load whole, so they belong
    in ``loaded_in_full``. ``_is_agent_definition`` keeps documents out."""
    if (root / ".claude-plugin" / "plugin.json").is_file():
        return []
    agents = root / "agents"
    found = sorted(agents.rglob("*.md")) if agents.is_dir() else []
    return [(p, "agent") for p in found if p.is_file() and _is_agent_definition(p)]


def _config_files(
    repo: Path, scope: Path | None, excludes: tuple[set[str], list[str]] | None,
) -> list[tuple[Path, str]]:
    dirs, pats = excludes or (set(), [])
    root = (scope or repo).resolve()
    found: list[tuple[Path, str]] = list(loaded_config_files(root))
    seen = {p.resolve() for p, _ in found}
    found.extend(item for item in _manifestless_agents(root) if item[0].resolve() not in seen)
    return [(p, k) for p, k in found
            if k in ("skill", "agent") and p.is_relative_to(repo)
            and not is_user_excluded(p.relative_to(repo), dirs, pats)]


def _skill_map(repo: Path, config: list[tuple[Path, str]]) -> tuple[dict[str, str], str]:
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
    return skills, skills_dir


def skill_texts(repo_root: Path) -> dict[str, str]:
    """Every ``SKILL.md`` the scan compares sections against, by repo-relative path.

    The grader's callers pass this to ``grade_instructions`` so a file's grade
    rests on the same split as its ``instruction_placement`` record.
    """
    repo = repo_root.resolve()
    return _skill_map(repo, _config_files(repo, None, None))[0]


def scan_instruction_placement(
    repo_root: Path,
    instruction_files: Mapping[str, Any],
    nested_instructions: Any,
    scope: Path | None = None,
    excludes: tuple[set[str], list[str]] | None = None,
) -> InstructionPlacementBlock:
    """Build the ``instruction_placement`` block (see the module doc)."""
    repo = repo_root.resolve()
    config = _config_files(repo, scope, excludes)
    skills, skills_dir = _skill_map(repo, config)

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
