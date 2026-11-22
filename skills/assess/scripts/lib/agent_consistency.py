"""Cross-agent instruction consistency: does every agent system get the same map? (#768)

Teams run several agent systems on one repository. Each loads its own
instruction file (``lib.agent_systems.SYSTEMS``), so the files drift: one tells
Claude Code to run ``make test`` while ``.windsurfrules`` still says ``npm test``.
``scan_agent_consistency`` builds the ``agent_consistency`` block from the files
``lib.instruction_discovery.collect_instruction_files`` finds, deterministically:

1. **Root context.** Per system, its files that load at the repository root at
   session start (``scope.always_loaded`` at directory ``.``), empty files
   dropped. Aider counts when ``.aider.conf.yml`` reads a file: its
   ``CONVENTIONS.md``, or the canonical file itself (link ``config_read``).
2. **Canonical source.** The root ``AGENTS.md`` when there is one; otherwise
   the first root-context file in ``SYSTEMS`` order, then path order.
3. **Link.** Each file is ``canonical``, a ``symlink`` resolving to it, an
   ``import`` of it (``@AGENTS.md``, followed by
   ``lib.instruction_budget.follow_imports``), a ``pointer`` (a line naming
   the canonical file's name, as ``#[[file:AGENTS.md]]`` or a markdown link
   does; never for Claude Code or Gemini CLI, which load only imports, so a
   prose mention there links nothing and their own text is compared), a ``mirror`` (identical below its header, ``mirror_pair``: a
   hand-kept copy, so not linked) or ``independent``. A system is linked when
   one of its files links; its other files are supplementary rules, compared
   for divergence like the rest.
4. **Divergence**, over each system's effective text (its files plus the
   canonical text for a file that imports or points to it):
   ``command``: a test or lint command (``named_check.is_named_check``, #951 /
   ``is_lint_command``, including a bare inline ```pytest```) one system names
   and another does not; ``threshold``: two systems whose numbers on lines
   naming ``coverage``, ``complexity`` or ``line length`` share none (a
   leading heading or list ordinal, ``## 2. Coverage``, is no number).

``conflicts`` (divergences, the level 1 signal of the ``agent-consistency``
row) and ``unlinked`` (systems not linked, level 2) are null, and
``applicable`` false, when fewer than two systems are present: one system has
nothing to agree with, so the row reads not measured, never 0. Each unlinked
system gets a ``next_steps`` entry, on its own single-file location (``CLAUDE.md``)
before a supplementary rule file, with the exact pointer line for its tool
(``@AGENTS.md`` for an import-capable tool, ``#[[file:AGENTS.md]]`` for Kiro,
``read: AGENTS.md`` in ``.aider.conf.yml`` for Aider, a markdown pointer line
otherwise) and, for the system's own single-file location only, a symlink
command, both built from templates and paths, never from file text. Read-only, no network, no model.

Cross-agent instruction consistency (#768): does every agent system get the same map?
`scan_agent_consistency(repo_root, excludes)` runs from the scan table
(`agent_consistency`, post-offers stage, headless `registered`) and returns the
`agent_consistency` block (`AgentConsistencyBlock`): `{available, systems,
systems_total, applicable, canonical, files, divergences, conflicts, unlinked,
consistent, next_steps, not_read}`. It reads each system's root-context files (scope
`always_loaded` at directory `.`, empty files dropped; Aider when `.aider.conf.yml`
reads a file, as `config_read` when it reads the canonical file directly). The canonical
file is the root `AGENTS.md`, else the first root file in `SYSTEMS` order, then path
order. Each file (`ConsistencyFile`) carries `link`: `canonical`, `symlink` (resolves to
it), `import` (`@AGENTS.md`, through `instruction_budget.follow_imports`), `pointer` (a
line naming the canonical file's name, as a markdown link or Kiro's
`#[[file:AGENTS.md]]` does; never for Claude Code or Gemini CLI, which load only
imports, so a prose mention there links nothing, matching `claude_shadows_agents`), `mirror` (identical below the header, through
`instruction_discovery.mirror_pair`; a hand-kept copy, so not linked) or `independent`.
A system is linked when one of its files links (its other always-loaded files are
supplementary rules, still compared for divergence). Each
`Divergence` is `{kind, value, named_in, missing_from, stated}` over each system's
effective text (its files, plus the canonical text where a file imports or points to
it): `command`, a test or lint command (`named_check.is_named_check` or
`is_lint_command`, plus a one-word inline `pytest`, `tox`, `nox` or linter) named by
some systems and not others; `threshold`, two systems whose numbers on lines naming
`coverage`, `complexity` or `line length` share none (a stray second number is no conflict;
a leading heading or list ordinal such as `## 2. Coverage` or `1. ` is dropped first, #960). `conflicts` (divergences)
and `unlinked` (systems not linked) are the default model's `agent-consistency` row
signals, level 1 `conflicts == 0` and level 2 `unlinked == 0` with `requires` level 1,
both `head_structure` (graded measured). With fewer than two systems `applicable` is
false and `conflicts`, `unlinked` and `consistent` are null, so the row reads unknown,
never 0. `next_steps` holds one `ConsistencyStep` per unlinked system, on the system's own
single-file location (`CLAUDE.md`) when it has one, else its first unlinked file (#960): `pointer`, the
exact line for its tool (`pointer_line`: `@AGENTS.md` relative to the file for Claude
Code and Gemini CLI, `#[[file:AGENTS.md]]` for Kiro, `read: AGENTS.md` in
`.aider.conf.yml` for Aider, `Follow the project instructions in
[AGENTS.md](AGENTS.md).` otherwise) and `symlink` (`ln -sf`, only for the system's own single-file location: null for
Aider and for a supplementary rule file, which a symlink would overwrite), built from
templates and paths, never file text; written into the file, each passes the link check.
`not_read` names the forms not compared (thresholds without a keyword, Aider `--read`
on the command line, user-level rules, scoped and nested files). Counts-only keeps the
systems, links, kinds and counts, paths as paths, and drops `value`, `pointer`,
`symlink` and `not_read`. Known limit: for a system that follows text, any line naming the canonical file's name counts
as a pointer. Stdlib plus `lib.check_command`, `lib.command_resolver`,
`lib.instruction_budget` and `lib.instruction_discovery`. Tests:
`tests/test_agent_consistency.py`.
"""
from __future__ import annotations

import posixpath
import re
from collections.abc import Iterable
from pathlib import Path, PurePosixPath
from typing import Literal, TypedDict

from lib.agent_systems import AIDER_CONFIG, SYSTEM_BY_ID, SYSTEM_ORDER
from lib.check_command import ALWAYS_CHECK, LINT_PROGRAMS, is_lint_command
from lib.named_check import is_named_check
from lib.command_resolver import command_words, extract_commands
from lib.doc_graph import is_repo_file
from lib.git_churn import tracked_files
from lib.instruction_budget import follow_imports
from lib.instruction_discovery import InstructionCandidate, collect_instruction_files, mirror_pair

Link = Literal["canonical", "symlink", "import", "pointer", "config_read", "mirror", "independent"]
LINKED: frozenset[str] = frozenset({"canonical", "symlink", "import", "pointer", "config_read"})
CANONICAL_NAME = "AGENTS.md"
THRESHOLD_KEYWORDS: dict[str, re.Pattern[str]] = {
    "coverage": re.compile(r"\bcoverage\b", re.IGNORECASE),
    "complexity": re.compile(r"\bcomplexity\b", re.IGNORECASE),
    "line_length": re.compile(r"\bline[ -]length\b|\bmax-line-length\b", re.IGNORECASE),
}
_NUMBER = re.compile(r"(?<![\w.])(\d+(?:\.\d+)?)(?!\w|\.\d)")
# A heading or list ordinal (``## 2. Coverage``, ``### 3.1 Coverage``, ``1. ``,
# ``2) ``) numbers the line, it states no threshold (issue #960). A heading
# number needs a ``.``/``)`` or a dotted form, so ``## 90 percent coverage``
# keeps its 90. A dotted heading number (``### 3.1``) is read as a section
# number, so a heading opening with a decimal threshold (``## 85.5``) states none.
_ORDINAL = re.compile(
    r"^\s*(?:#{1,6}[ \t]+(?:\d+(?:\.\d+)+[.)]?|\d+[.)])|\d+[.)])(?=\s|$)")
_INLINE = re.compile(r"(?<!`)`([^`\n]+)`(?!`)")
_FENCE = re.compile(r"^\s*(`{3,}|~{3,})")
NOT_READ = [
    "thresholds stated without a coverage, complexity or line-length keyword on the line",
    "files Aider is given on the command line (--read) rather than in .aider.conf.yml",
    "user-level and global rules (Windsurf global rules, Cline global rules, Kiro user steering)",
    "scoped rule files (globs, paths, fileMatch) and nested directory files",
]


class ConsistencyFile(TypedDict):
    """One root-context instruction file and how it links to the canonical source."""

    path: str
    system: str
    link: Link


class ThresholdStatement(TypedDict):
    system: str
    number: float


class Divergence(TypedDict):
    """A command or threshold the systems disagree on.

    ``value`` is the command as written, or the threshold keyword; ``named_in``
    the systems that name it; ``missing_from`` the systems that do not (a
    command) and ``stated`` each system's numbers (a threshold, else empty).
    """

    kind: Literal["command", "threshold"]
    value: str
    named_in: list[str]
    missing_from: list[str]
    stated: list[ThresholdStatement]


class ConsistencyStep(TypedDict):
    """The supportive next step for one unlinked system: point it at the canonical file."""

    system: str
    path: str
    pointer: str
    symlink: str | None


class AgentConsistencyBlock(TypedDict):
    """``run-context.json`` ``agent_consistency`` (issue #768)."""

    available: bool
    systems: list[str]
    systems_total: int
    applicable: bool
    canonical: str | None
    files: list[ConsistencyFile]
    divergences: list[Divergence]
    conflicts: int | None
    unlinked: int | None
    consistent: bool | None
    next_steps: list[ConsistencyStep]
    not_read: list[str]


def check_commands(text: str) -> set[str]:
    """The test and lint commands a text names, word-normalised."""
    found = {" ".join(command_words(c.text)) for c in extract_commands(text)
             if is_named_check(c.text) or is_lint_command(c.text)}
    in_fence = False
    for line in text.splitlines():
        if _FENCE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        for match in _INLINE.finditer(line):
            words = command_words(match.group(1))
            # extract_commands reads a one-word inline span as prose; a bare
            # `pytest` or `ruff` is still the command an agent runs.
            if len(words) == 1 and words[0] in ALWAYS_CHECK | LINT_PROGRAMS:
                found.add(words[0])
    return {c for c in found if c}


def thresholds(text: str) -> dict[str, set[float]]:
    """Numbers stated on lines naming a threshold keyword, per keyword.

    A leading heading or list ordinal is dropped first, so ``## 2. Coverage``
    states no number.
    """
    out: dict[str, set[float]] = {}
    for line in text.splitlines():
        numbers = {float(n) for n in _NUMBER.findall(_ORDINAL.sub("", line, count=1))}
        if not numbers:
            continue
        for key, pattern in THRESHOLD_KEYWORDS.items():
            if pattern.search(line):
                out.setdefault(key, set()).update(numbers)
    return out


def _rel_from(path: str, target: str) -> str:
    start = PurePosixPath(path).parent.as_posix() or "."
    return posixpath.relpath(target, start)


def pointer_line(system: str, path: str, canonical: str) -> str:
    """The exact line that points ``path`` (a file of ``system``) at ``canonical``."""
    style = SYSTEM_BY_ID[system].pointer if system in SYSTEM_BY_ID else "text"
    rel = _rel_from(path, canonical)
    if style == "import":
        return f"@{rel}"
    if style == "kiro_include":
        return f"#[[file:{canonical}]]"
    if style == "aider_read":
        return f"read: {canonical}"
    name = PurePosixPath(canonical).name
    return f"Follow the project instructions in [{name}]({rel})."


def _sort_systems(systems: Iterable[str]) -> list[str]:
    return sorted(set(systems), key=lambda s: (SYSTEM_ORDER.get(s, len(SYSTEM_ORDER)), s))


def _root_context(cands: list[InstructionCandidate]) -> list[InstructionCandidate]:
    return [c for c in cands if c.directory == "." and c.always_loaded and c.body.strip()]


def _canonical(files: list[InstructionCandidate]) -> InstructionCandidate | None:
    for cand in files:
        if cand.rel == CANONICAL_NAME:
            return cand
    ranked = sorted(files, key=lambda c: (SYSTEM_ORDER.get(c.tool, len(SYSTEM_ORDER)), c.rel))
    return ranked[0] if ranked else None


def _link(cand: InstructionCandidate, canon: InstructionCandidate, repo_root: Path,
          tracked: frozenset[Path] | None) -> Link:
    if cand.rel == canon.rel:
        return "canonical"
    if (repo_root / cand.rel).resolve() == (repo_root / canon.rel).resolve():
        return "symlink"
    loaded, _skipped = follow_imports(
        [cand.rel], repo_root, lambda p: is_repo_file(p, repo_root, tracked))
    if canon.rel in loaded:
        return "import"
    # Claude Code and Gemini CLI load only what a file imports: a prose mention of
    # AGENTS.md is no link for them (instruction_discovery's claude_shadows_agents
    # rule), so their own text is compared as it stands.
    follows_text = SYSTEM_BY_ID[cand.tool].pointer != "import" if cand.tool in SYSTEM_BY_ID else True
    name = re.escape(PurePosixPath(canon.rel).name)
    if follows_text and re.search(rf"(?<![\w-]){name}(?![\w-])", cand.body):
        return "pointer"
    mine, theirs, _notes = mirror_pair(cand.body, canon.body)
    return "mirror" if mine == theirs else "independent"


def _divergences(texts: dict[str, str]) -> list[Divergence]:
    systems = _sort_systems(texts)
    commands = {s: check_commands(texts[s]) for s in systems}
    out: list[Divergence] = []
    for command in sorted(set().union(*commands.values())):
        named = [s for s in systems if command in commands[s]]
        missing = [s for s in systems if command not in commands[s]]
        if missing:
            out.append({"kind": "command", "value": command, "named_in": named,
                        "missing_from": missing, "stated": []})
    stated = {s: thresholds(texts[s]) for s in systems}
    for key in THRESHOLD_KEYWORDS:
        values = {s: stated[s][key] for s in systems if key in stated[s]}
        # A conflict only when two systems share no number: a stray figure on the
        # line (an issue number, a second limit) beside the agreed one is no conflict.
        sets = list(values.values())
        if any(not (a & b) for i, a in enumerate(sets) for b in sets[i + 1:]):
            out.append({"kind": "threshold", "value": key, "named_in": list(values),
                        "missing_from": [],
                        "stated": [{"system": s, "number": n}
                                   for s in values for n in sorted(values[s])]})
    return out


def _primary(system: str, path: str) -> bool:
    """A system's own single-file location, which a symlink may replace whole.

    A supplementary rule file (``.claude/rules/x.md``) or Aider's config is
    never offered a symlink: it would overwrite that file's own content.
    """
    known = SYSTEM_BY_ID.get(system)
    return (system != "aider" and known is not None
            and path in {loc for loc in known.locations if "*" not in loc})


def check_consistency(repo_root: Path, cands: list[InstructionCandidate],
                      aider_reads: frozenset[str],
                      tracked: frozenset[Path] | None) -> AgentConsistencyBlock:
    """The ``agent_consistency`` block over discovered instruction files."""
    files = _root_context(cands)
    canon = _canonical(files)
    by_system: dict[str, list[InstructionCandidate]] = {}
    for cand in files:
        by_system.setdefault(cand.tool, []).append(cand)
    rows: list[ConsistencyFile] = []
    linked: dict[str, bool] = {}
    texts: dict[str, str] = {}
    aider_reads_canon = canon is not None and canon.rel in aider_reads
    for system in _sort_systems([*by_system, *(["aider"] if aider_reads_canon else [])]):
        parts: list[str] = []
        links: list[Link] = []
        if system == "aider" and aider_reads_canon and canon is not None:
            rows.append({"path": AIDER_CONFIG, "system": "aider", "link": "config_read"})
            links.append("config_read")
            parts.append(canon.body)
        for cand in sorted(by_system.get(system, []), key=lambda c: c.rel):
            link = _link(cand, canon, repo_root, tracked) if canon is not None else "independent"
            rows.append({"path": cand.rel, "system": system, "link": link})
            links.append(link)
            parts.append(cand.body)
            if link in ("import", "pointer") and canon is not None:
                parts.append(canon.body)
        # One linked file links the system: its other always-loaded files are
        # supplementary rules, and a divergence among them is still reported.
        linked[system] = any(lk in LINKED for lk in links)
        texts[system] = "\n".join(parts)
    systems = _sort_systems(texts)
    applicable = len(systems) >= 2
    divergences = _divergences(texts) if applicable else []
    unlinked = [s for s in systems if not linked[s]] if applicable else []
    steps: list[ConsistencyStep] = []
    for system in unlinked:
        # The system's own single-file location (CLAUDE.md) takes the pointer
        # before a supplementary rule file (.claude/rules/x.md) that sorts first.
        candidates = [r["path"] for r in rows
                      if r["system"] == system and r["link"] not in LINKED]
        path = next((c for c in candidates if _primary(system, c)), candidates[0])
        assert canon is not None  # two or more systems hold at least one file
        steps.append({
            "system": system,
            "path": AIDER_CONFIG if system == "aider" else path,
            "pointer": pointer_line(system, path, canon.rel),
            "symlink": f"ln -sf {_rel_from(path, canon.rel)} {path}"
            if _primary(system, path) else None,
        })
    return {
        "available": True,
        "systems": systems,
        "systems_total": len(systems),
        "applicable": applicable,
        "canonical": canon.rel if canon is not None else None,
        "files": sorted(rows, key=lambda r: r["path"]),
        "divergences": divergences,
        "conflicts": len(divergences) if applicable else None,
        "unlinked": len(unlinked) if applicable else None,
        "consistent": (not divergences and not unlinked) if applicable else None,
        "next_steps": steps,
        "not_read": list(NOT_READ),
    }


def scan_agent_consistency(
    repo_root: Path, excludes: tuple[set[str], list[str]] | None = None,
) -> AgentConsistencyBlock:
    """Scan-table entry: discover the instruction files, then compare them."""
    repo_root = repo_root.resolve()
    extra_dirs, extra_pats = excludes if excludes is not None else (set(), [])
    tracked = tracked_files(repo_root)
    cands, aider_reads = collect_instruction_files(repo_root, tracked, extra_dirs, list(extra_pats))
    return check_consistency(repo_root, cands, aider_reads, tracked)
