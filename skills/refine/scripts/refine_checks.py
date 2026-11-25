"""Per-issue readiness checks for the refinement script: pure functions over one `Issue` (issue #1320).

The readiness rules are shared with `/issues` triage (`skills/issues/SKILL.md`,
section "Triage Mode"): an issue is `agent-ready` only when its scope is
clear, it overlaps no open PR, it is sized to one PR and its dependencies are
blocked-by edges. The definition of ready
(`docs/contributing/definition-of-ready.md`, "Ready checklist") writes those
rules as five checklist items. This module decides what a script can decide
and marks the rest `needs_judgement`:

| Item / rule | Source | Here |
|---|---|---|
| `sections-present` | definition, script | heading match per the block; `missing_sections` |
| `criteria-checkable` | definition, judgement | fail when Success Criteria holds no list item; otherwise judgement, with each item that has no code span and names no test as a hint |
| `sized` | definition, script | the newer of a `<!-- refine:size N -->` marker comment and a `Size: <n> points` body line is at most `max_points`, or the issue has sub-issues; with neither the issue is `unsized` (needs sizing, `needs_judgement`), never `fail` |
| `blockers-recorded` | definition, judgement | fail when prose says "blocked by" or "depends on" (not negated) an open issue that has no blocked-by edge; a closed one is a hint; otherwise judgement |
| `no-open-question` | definition, judgement | always judgement |
| overlap with an open PR | `/issues` "Overlap Sweep" | an open PR that closes the issue, or changes a file the issue names in a code span; `overlaps` |

An item the definition marks `judgement` is never passed by the script; an
item it marks `script` with no evaluator here is `needs_judgement` with a
detail saying so, so a new checklist row degrades to a reader, not to a pass.

Text sources: the issue body plus every comment whose first line is
`<!-- triage:clarified-scope -->`, which the definition lets supply or replace
a section. A clarified-scope comment replaces the body's version of each
section it contains and leaves the others alone; between comments the newest
wins (by creation time, then comment order), the same newest-wins rule the
size marker follows.
The size comes from two producers, whichever is newer: a comment whose first
line matches `size_marker` (`<!-- refine:size N -->`, written by `/issues`
triage and the refiner with a one-line reason) or a `size_pattern` line in the
body. The body is dated by the
issue's creation, since the host reports no body edit time, so any marker
comment is newer than the body line.

Section match: a heading at one of `heading_levels` whose text equals the
name (trimmed, any case) has content when a line before the next heading of
the same or a higher level is neither blank nor an `empty_markers` entry. A
deeper heading inside the section counts as content. Lines inside code fences
are never headings.

Python 3.8 compatible and stdlib only.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Dict, Iterator, List, Optional, Set, Tuple

from refine_types import ChecklistResult, Definition, Issue, Overlap, PullRequest

CLARIFIED_MARKER = "<!-- triage:clarified-scope -->"
UNTRIAGED_DAYS = 7

_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_FENCE = re.compile(r"^\s*(```|~~~)")
_LIST_ITEM = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+(.*)$")
_CODE_SPAN = re.compile(r"`[^`]+`")
_TEST_REF = re.compile(r"\btest_\w+|\btests?/\S+|\bpytest\b|\.py\b")
_BLOCKER_PHRASE = re.compile(r"(?i)\b(?:blocked by|depends on)\b")
_NEGATION = re.compile(r"(?i)(?:\bnot|n't|\bno longer|\bnever)\s+$")
_PATH_SPAN = re.compile(r"`([\w.\-/]+)`")


def text_sources(issue: Issue) -> List[str]:
    """The body, then each clarified-scope comment, oldest first (creation time, then comment order)."""
    clarified = sorted(
        (c["created_at"], order, c["body"]) for order, c in enumerate(issue["comments"])
        if c["body"].lstrip().startswith(CLARIFIED_MARKER))
    return [issue["body"]] + [body for _, _, body in clarified]


def _headings(lines: List[str]) -> Iterator[Tuple[int, int, str]]:
    """(line index, level, text) of each heading outside code fences."""
    fenced = False
    for idx, line in enumerate(lines):
        if _FENCE.match(line):
            fenced = not fenced
            continue
        match = None if fenced else _HEADING.match(line)
        if match:
            yield idx, len(match.group(1)), match.group(2).strip()


def section_texts(text: str, definition: Definition) -> Dict[str, str]:
    """Each named section with content, keyed by its name from the definition."""
    lines = text.splitlines()
    heads = list(_headings(lines))
    wanted = {name.strip().lower(): name for name in definition["sections"]}
    markers = {m.strip() for m in definition["empty_markers"]}
    found: Dict[str, str] = {}
    for pos, (idx, level, title) in enumerate(heads):
        name = wanted.get(title.lower())
        if name is None or level not in definition["heading_levels"] or name in found:
            continue
        end = next((h[0] for h in heads[pos + 1:] if h[1] <= level), len(lines))
        body = lines[idx + 1:end]
        if any(ln.strip() and ln.strip() not in markers for ln in body):
            found[name] = "\n".join(body)
    return found


def all_sections(issue: Issue, definition: Definition) -> Dict[str, str]:
    """Sections from every text source; the newest source holding a section wins.

    A clarified-scope comment replaces the body's version of each section it
    contains; a section it lacks keeps the body's (or an older comment's) version.
    """
    merged: Dict[str, str] = {}
    for source in text_sources(issue):
        merged.update(section_texts(source, definition))
    return merged


def missing_sections(issue: Issue, definition: Definition) -> List[str]:
    """The definition's sections with no content in any text source, in definition order."""
    present = all_sections(issue, definition)
    return [name for name in definition["sections"] if name not in present]


def criteria_items(section: str) -> List[str]:
    """The list items of a Success Criteria section, continuation lines joined."""
    items: List[str] = []
    for line in section.splitlines():
        match = _LIST_ITEM.match(line)
        if match:
            items.append(match.group(1).strip())
        elif items and line.strip():
            items[-1] = items[-1] + " " + line.strip()
    return items


def unanchored(items: List[str]) -> List[str]:
    """Items with no code span and no test reference: hints for the reader."""
    return [i for i in items if not _CODE_SPAN.search(i) and not _TEST_REF.search(i)]


def size_reading(issue: Issue, definition: Definition) -> Tuple[Optional[int], Optional[str]]:
    """(points, source): the newer of the last body size line and the last size marker comment.

    `source` is "marker" or "body"; (None, None) when the issue has neither.
    """
    found: List[Tuple[str, int, int, str]] = []  # (time, order, points, source)
    body_matches = list(re.compile(definition["size_pattern"]).finditer(issue["body"]))
    if body_matches:
        found.append((issue["created_at"], 0, int(body_matches[-1].group(1)), "body"))
    marker = re.compile(definition["size_marker"])
    for order, comment in enumerate(issue["comments"], 1):
        lines = comment["body"].strip().splitlines()
        match = marker.fullmatch(lines[0].strip()) if lines else None
        if match:
            found.append((comment["created_at"], order, int(match.group(1)), "marker"))
    if not found:
        return None, None
    _, _, points, source = max(found)
    return points, source


def size_points(issue: Issue, definition: Definition) -> Optional[int]:
    return size_reading(issue, definition)[0]


def _issue_refs(text: str, repo: str) -> Set[int]:
    refs = {int(n) for n in re.findall(r"(?<![\w/])#(\d+)\b", text)}
    url = re.compile(r"https://github\.com/" + re.escape(repo) + r"/issues/(\d+)")
    return refs | {int(n) for n in url.findall(text)}


def prose_blockers(issue: Issue, repo: str) -> Set[int]:
    """Issues named after "blocked by" or "depends on", up to the end of the sentence.

    A negated phrase ("not blocked by", "no longer depends on") names no blocker.
    """
    found: Set[int] = set()
    for source in text_sources(issue):
        for match in _BLOCKER_PHRASE.finditer(source):
            if _NEGATION.search(source[max(0, match.start() - 16):match.start()]):
                continue
            tail = re.split(r"\.\s|\n", source[match.end():], maxsplit=1)[0]
            found |= _issue_refs(tail, repo)
    found.discard(issue["number"])
    return found


def overlaps(issue: Issue, prs: List[PullRequest]) -> List[Overlap]:
    """Open PRs that close this issue or change a file it names (`/issues` Overlap Sweep)."""
    named = set()
    for source in text_sources(issue):
        named |= {p[2:] if p.startswith("./") else p for p in _PATH_SPAN.findall(source) if "/" in p or "." in p.strip(".")}
    dirs = {p for p in named if p.endswith("/")}
    out: List[Overlap] = []
    for pr in prs:
        if issue["number"] in pr["closes"]:
            out.append(Overlap(pr=pr["number"], kind="closes", files=[]))
            continue
        hit = sorted(f for f in pr["files"] if f in named or any(f.startswith(d) for d in dirs))
        if hit:
            out.append(Overlap(pr=pr["number"], kind="files", files=hit))
    return out


def untriaged_days(issue: Issue, definition: Definition, now: datetime) -> Optional[int]:
    """Whole days open when the issue is untriaged for more than 7 days, else None.

    Untriaged: it carries the triage label, or no label at all.
    """
    labels = issue["labels"]
    if labels and definition["labels"]["triage"] not in labels:
        return None
    created = datetime.fromisoformat(issue["created_at"].replace("Z", "+00:00")).astimezone(timezone.utc)
    age = now - created
    if age.total_seconds() <= UNTRIAGED_DAYS * 86400:
        return None
    return age.days


def _result(item_id: str, check: str, status: str, detail: str, hints: Optional[List[str]] = None) -> ChecklistResult:
    return ChecklistResult(id=item_id, check=check, status=status, detail=detail, hints=hints or [])


def _sections_item(missing: List[str]) -> Tuple[str, str]:
    if missing:
        return "fail", "missing: " + ", ".join(missing)
    return "pass", "all sections present"


def _criteria_item(issue: Issue, definition: Definition) -> Tuple[str, str, List[str]]:
    section = all_sections(issue, definition).get("Success Criteria")
    items = criteria_items(section) if section is not None else []
    if not items:
        return "fail", "Success Criteria holds no list item", []
    loose = unanchored(items)
    detail = f"{len(items)} item(s); {len(loose)} with no code span or test reference"
    return "needs_judgement", detail, loose


def _sized_item(points: Optional[int], source: Optional[str], subs: int, max_points: int) -> Tuple[str, str]:
    if subs > 0:
        return "pass", f"decomposed into {subs} sub-issue(s)"
    if points is None:
        return "needs_judgement", "unsized: needs sizing (no refine:size comment and no size line)"
    if points > max_points:
        return "fail", f"{points} points exceeds {max_points} with no sub-issues"
    return "pass", f"{points} point(s), from the {source}"


def _blockers_item(issue: Issue, repo: str, open_numbers: Optional[Set[int]]) -> Tuple[str, str, List[str]]:
    """Fail only on an open issue named as a blocker in prose with no edge.

    A closed (or unknown) issue named that way orders nothing, so it is a hint
    for the reader, not a failure. With `open_numbers` None every named issue
    counts as open.
    """
    edges = {b["number"] for b in issue["blocked_by"]}
    unrecorded = prose_blockers(issue, repo) - edges
    still_open = sorted(n for n in unrecorded if open_numbers is None or n in open_numbers)
    if still_open:
        names = ", ".join(f"#{n}" for n in still_open)
        return "fail", f"prose names open {names} as a blocker with no blocked-by edge", []
    hints = [f"prose names #{n} as a blocker; it is not open, so no edge is needed"
             for n in sorted(unrecorded)]
    detail = f"{len(edges)} blocked-by edge(s)" if edges else "no blocked-by edges"
    return "needs_judgement", detail, hints + ["confirm no dependency is missing an edge"]


def checklist(issue: Issue, definition: Definition, repo: str,
              open_numbers: Optional[Set[int]] = None) -> List[ChecklistResult]:
    """Every checklist item from the definition, in its order, with a status.

    `open_numbers` is the set of open issue numbers, for `blockers-recorded`.
    """
    results: List[ChecklistResult] = []
    missing = missing_sections(issue, definition)
    points, source = size_reading(issue, definition)
    for entry in definition["checklist"]:
        item, check = entry["id"], entry["check"]
        hints: List[str] = []
        if item == "sections-present":
            status, detail = _sections_item(missing)
        elif item == "criteria-checkable":
            status, detail, hints = _criteria_item(issue, definition)
        elif item == "sized":
            status, detail = _sized_item(points, source, issue["sub_issues_total"], definition["max_points"])
        elif item == "blockers-recorded":
            status, detail, hints = _blockers_item(issue, repo, open_numbers)
        else:
            status, detail = "needs_judgement", "no script check for this item"
        if check == "judgement" and status == "pass":
            status = "needs_judgement"
        results.append(_result(item, check, status, detail, hints))
    return results
