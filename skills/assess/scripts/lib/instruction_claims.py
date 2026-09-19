"""Verify the checkable claims an agent instruction file makes about the repo.

An instruction file that says "`scripts/check-x.sh` is enforced in CI" or "Node
20.11.0 is pinned in `.nvmrc`" is a map an agent trusts without checking. When
the workflow stopped calling the script, or the pin moved, the sentence still
reads as true. This module extracts those sentences and checks each against the
repository, no model: a failed claim is a lying signal with a file and a line.

Claim kinds:

- ``enforcement``: a backticked script path in a sentence holding "enforced",
  "runs in", "checked by" or the word "CI". A script path is a shell script
  (``.sh``, ``.bash``, ``.zsh``, ``.ps1``) anywhere, or any script extension
  (``.py``, ``.js``, ``.ts`` ...) under a ``scripts/``, ``bin/``, ``tools/``,
  ``ci/`` or ``hack/`` directory; ordinary source files such as ``src/index.ts``
  are not something CI invokes by path. Verified when the path occurs in any
  CI configuration (``.github/workflows/``, ``.github/actions/``, GitLab,
  Jenkins, CircleCI, Azure, Buildkite, Bitbucket, Travis, Drone) or in a task
  runner CI commonly calls through (``Makefile``, ``package.json``,
  ``.pre-commit-config.yaml``, ``justfile``, ``Taskfile.yml``, ``tox.ini``,
  ``noxfile.py``), using the reference search of ``lib.evidence_check``. A repo
  with no CI configuration at all yields no enforcement claim: there is
  nothing to check against, so the claim is unverifiable, not false.
- ``pin``: a sentence holding "pinned in" followed by a backticked file, plus
  exactly one dotted numeric version (``20.11.0``) on either side of the phrase.
  Verified when the file exists and contains the version as a substring (so
  ``3.11`` verifies against a file holding ``3.11.9``: the check under-reports
  rather than accuse). A missing file is a failed claim; a sentence with no
  version, or with two different versions, is skipped.
- ``count``: an integer followed by a word ("43 pgTAP suites") in a sentence
  that also names one backticked glob pattern (``supabase/tests/*.sql``). The
  pattern is matched relative to the repository root and the matching files
  (not directories) are counted. Verified when the two numbers differ by no
  more than the larger of 10% (of the larger number) or 2. There is no noun
  table: the backticked pattern is the only thing that makes a number
  checkable, so a sentence with no pattern, a pattern with no wildcard (a
  directory may hold files or subdirectories), two integers or two patterns
  (which counts which is a guess), or a pattern that leaves the repository is
  skipped. The pattern must also look like a path (a ``/``, or a last
  segment ending in a plain extension such as ``*.md``), so ``**kwargs`` or a
  ``?`` placeholder is not a pattern, and a four-digit year is not a count.
  When the pattern's wildcard-free directory does not exist, or the glob
  cannot be evaluated, the claim is unverifiable and skipped; a directory
  that exists with no match fails with ``actual`` 0. Matching follows
  ``pathlib``, where ``*`` also matches dotfiles (a shell would not); matches
  that resolve outside the repository (through a symlink) are not counted,
  nor, below the pattern's fixed prefix, the trees every scan excludes
  (``.git``, ``.assess``, ``node_modules``, ``.venv`` ...). A pattern that
  matches only directories, or a subtree the walk cannot read, is also
  unverifiable rather than a wrong count, as is a non-recursive pattern whose
  matches mix files and directories; a Windows drive or UNC path is skipped.
  The sentence must have the frame "<integer> <noun> ... <link> `<pattern>`":
  the integer before the pattern and a word from ``COUNT_LINK_WORDS`` (in,
  under, matching, across, beneath, within, inside) between them. A second,
  closed-list filter then drops an integer followed by one of the unit words
  or preceded by one of the comparators in ``COUNT_NOT_A_COUNT`` ("at most 10
  files in", "500 lines"); a word outside those lists is not recognised.

A sentence that fits no kind is skipped silently. Adding a kind means one
extractor in ``_EXTRACTORS`` (sentence -> claims) and one verifier in
``_VERIFIERS`` (claim -> None when it holds, else extra fields for the failure;
it raises ``Unverifiable`` when the repository cannot settle the claim).

Sentences are read per paragraph, so a claim wrapped across lines is still
found; its ``line`` is the 1-based line the sentence starts on. Fenced code is
not prose and is not read.
"""
from __future__ import annotations

import os
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path, PureWindowsPath
from typing import Any, NamedTuple

from lib.doc_graph import is_excluded_path
from lib.evidence_check import is_referenced_in

# Where CI is configured. The enforcement check needs at least one to exist.
CI_CONFIG_PATHS = (
    ".github/workflows", ".github/actions", ".gitlab-ci.yml", ".gitlab-ci.yaml",
    "Jenkinsfile", ".circleci", "azure-pipelines.yml", ".azure-pipelines",
    ".buildkite", "bitbucket-pipelines.yml", ".travis.yml", ".drone.yml",
)
# Task runners CI calls through (`make lint`, `npm run lint`, `pre-commit run`):
# a script referenced here counts as wired in, which fails open rather than
# accusing a repo whose workflow invokes the script indirectly.
TASK_RUNNER_PATHS = (
    "Makefile", "makefile", "GNUmakefile", "package.json", ".pre-commit-config.yaml",
    "justfile", "Justfile", "Taskfile.yml", "Taskfile.yaml", "tox.ini", "noxfile.py",
)

_SHELL_EXTENSIONS = "sh|bash|zsh|ps1"
_SCRIPT_EXTENSIONS = "sh|bash|zsh|ps1|py|js|mjs|cjs|ts|rb|pl"
_SCRIPT_DIRS = "scripts|bin|tools|ci|hack"
# A script path inside a backticked span: `scripts/check-x.sh` or the path in
# `bash scripts/check-x.sh --fix`. Shell scripts anywhere; other script
# extensions only under a scripts-like directory.
_SCRIPT_IN_SPAN = re.compile(
    rf"(?<![\w./-])((?:\./)?(?:[\w.-]+/)*[\w.-]*\w\.(?:{_SHELL_EXTENSIONS})"
    rf"|(?:\./)?(?:[\w.-]+/)*(?:{_SCRIPT_DIRS})/(?:[\w.-]+/)*[\w.-]*\w\.(?:{_SCRIPT_EXTENSIONS}))"
    rf"(?![\w/-])"
)
_BACKTICK_SPAN = re.compile(r"`([^`]+)`")
_ENFORCEMENT_TRIGGER = re.compile(r"(?i:\benforced\b|\bruns in\b|\bchecked by\b)|\bCI\b")

_PINNED_IN = re.compile(r"pinned in\s+`([^`\s]+)`", re.IGNORECASE)
_VERSION = re.compile(r"(?<![\d.])(\d+(?:\.\d+)+)(?!\.?\d)")

# A count: an integer, not part of a version, decimal, list or percentage,
# followed by a word. Read with backticked spans removed.
# A year (1900-2099) is skipped: far more often a date than a file count.
_COUNT = re.compile(
    r"(?<![\w.,%$/-])(?!(?:19|20)\d\d(?!\d))(\d+)(?=\s+([A-Za-z][\w-]*))")


# The frame of a count sentence: "<integer> <noun> ... <link> `<pattern>`".
# The integer comes before the pattern and one of these words sits between
# them ("43 suites matching `x/*.sql`", "150 migrations live in `x/*.sql`").
COUNT_LINK_WORDS = re.compile(
    r"\b(?:in|under|matching|across|beneath|within|inside)\b", re.IGNORECASE)


class _ThresholdSigns(NamedTuple):
    units: frozenset[str]  # the word after the integer
    comparator: re.Pattern[str]  # the text just before the integer


# Signs that an integer beside a pattern is a threshold, not a file count
# ("below 500 lines", "at most 10 files"). This list only ever removes
# claims, never adds one: it is not a table of things that are counted.
COUNT_NOT_A_COUNT = _ThresholdSigns(
    units=frozenset({
        "line", "lines", "loc", "character", "characters", "char", "chars",
        "word", "words", "byte", "bytes", "kb", "mb", "gb", "token", "tokens",
        "column", "columns", "percent", "ms", "second", "seconds", "sec", "secs",
        "minute", "minutes", "min", "mins", "hour", "hours", "hr", "hrs",
        "day", "days", "week", "weeks", "month", "months", "year", "years",
    }),
    comparator=re.compile(
        r"\b(?:below|under|above|over|at most|at least|up to|no more than|"
        r"fewer than|less than|more than|max|maximum|min|minimum|limit)"
        r"(?:\s+of)?\s*$", re.IGNORECASE),
)
_GLOB_CHARS = re.compile(r"[*?]")
# A path shape: a directory separator, or a last segment with a plain extension.
_PLAIN_EXTENSION = re.compile(r"\.[A-Za-z0-9]+$")
COUNT_TOLERANCE = 0.10
COUNT_MIN_DELTA = 2

_FENCE = re.compile(r"^\s*(```|~~~)")
# A line that starts its own block rather than continuing the paragraph above.
_BLOCK_START = re.compile(r"^\s*(?:#{1,6}\s|[-*+]\s|\d+[.)]\s|\||>)")
_HEADING = re.compile(r"^\s*#{1,6}\s")
_SENTENCE_END = re.compile(r"[.!?](?=\s|$)")


class Unverifiable(Exception):
    """The repository cannot settle the claim either way: skip it, never fail it."""


@dataclass
class Claim:
    kind: str
    line: int
    path: str
    fields: dict[str, Any] = field(default_factory=dict)


def _paragraphs(text: str) -> Iterable[list[tuple[int, str]]]:
    """Yield runs of (1-based line number, line) that form one prose block."""
    block: list[tuple[int, str]] = []
    fenced = False
    for number, line in enumerate(text.splitlines(), start=1):
        if _FENCE.match(line):
            fenced = not fenced
            if block:
                yield block
            block = []
            continue
        if fenced or not line.strip() or _BLOCK_START.match(line):
            if block:
                yield block
            if fenced or not line.strip():
                block = []
            elif _HEADING.match(line):
                # A heading is its own block: the prose under it, blank line
                # or not, must not borrow its words or report its line.
                yield [(number, line)]
                block = []
            else:
                block = [(number, line)]  # a list item or table row seeds its block
            continue
        block.append((number, line))
    if block:
        yield block


def _sentences(block: list[tuple[int, str]]) -> Iterable[tuple[int, str]]:
    """Split one block into (start line, sentence), ignoring stops inside backticks."""
    joined = ""
    starts: list[tuple[int, int]] = []  # (offset in joined, line number)
    for number, line in block:
        if joined:
            joined += " "
        starts.append((len(joined), number))
        joined += line.strip()

    def line_at(offset: int) -> int:
        current = starts[0][1]
        for begin, number in starts:
            if begin > offset:
                break
            current = number
        return current

    ticks = [i for i, ch in enumerate(joined) if ch == "`"]
    begin = 0
    for match in _SENTENCE_END.finditer(joined):
        end = match.end()
        if sum(1 for t in ticks if t < end) % 2:
            continue  # the stop sits inside a backticked span
        yield from _emit(joined, begin, end, line_at)
        begin = end
    yield from _emit(joined, begin, len(joined), line_at)


def _emit(joined: str, begin: int, end: int,
          line_at: Callable[[int], int]) -> Iterable[tuple[int, str]]:
    sentence = joined[begin:end]
    stripped = sentence.strip()
    if stripped:
        yield line_at(begin + len(sentence) - len(sentence.lstrip())), stripped


def _enforcement_claims(sentence: str, line: int) -> list[Claim]:
    if not _ENFORCEMENT_TRIGGER.search(sentence):
        return []
    paths: list[str] = []
    for span in _BACKTICK_SPAN.findall(sentence):
        for raw in _SCRIPT_IN_SPAN.findall(span):
            path = raw.removeprefix("./")
            if path not in paths:
                paths.append(path)
    return [Claim("enforcement", line, path) for path in paths]


def _pin_claims(sentence: str, line: int) -> list[Claim]:
    pins = _PINNED_IN.findall(sentence)
    if len(pins) != 1:
        return []
    rest = _PINNED_IN.sub(" ", sentence)
    versions = set(_VERSION.findall(rest))
    if len(versions) != 1:
        return []
    return [Claim("pin", line, pins[0], {"version": versions.pop()})]


def _count_claims(sentence: str, line: int) -> list[Claim]:
    spans = [m for m in _BACKTICK_SPAN.finditer(sentence)
             if _GLOB_CHARS.search(m.group(1)) and not re.search(r"\s", m.group(1))]
    if len(spans) != 1:
        return []
    span = spans[0]
    pattern = span.group(1).removeprefix("./")
    if (pattern.startswith(("/", "~")) or ".." in Path(pattern).parts
            or PureWindowsPath(pattern).drive):
        return []
    if "/" not in pattern and not _PLAIN_EXTENSION.search(pattern):
        return []  # `**kwargs`, `*args`, a `?` placeholder: not a path
    # Blank the backticked spans in place, so offsets still match ``span``.
    prose = _BACKTICK_SPAN.sub(lambda m: " " * len(m.group(0)), sentence)
    numbers = list(_COUNT.finditer(prose))
    if len(numbers) != 1:
        return []
    number = numbers[0]
    if (number.end() > span.start()
            or not COUNT_LINK_WORDS.search(prose, number.end(), span.start())):
        return []  # not "<integer> <noun> ... <link> `<pattern>`"
    if (number.group(2).lower() in COUNT_NOT_A_COUNT.units
            or COUNT_NOT_A_COUNT.comparator.search(prose[:number.start()])):
        return []  # a threshold ("below 500 lines"), not a count of files
    return [Claim("count", line, pattern, {"claimed": int(number.group(1))})]


def count_within_tolerance(claimed: int, actual: int) -> bool:
    """True when the difference is at most the larger of 10% or 2."""
    allowed = max(COUNT_TOLERANCE * max(claimed, actual), COUNT_MIN_DELTA)
    return abs(claimed - actual) <= allowed


def _unreadable_below(base: Path, rest: tuple[str, ...]) -> bool:
    """True when a directory the pattern would descend into cannot be listed.
    ``Path.glob`` drops such a subtree silently, which would read as a low count."""
    depth = None if "**" in rest else len(rest) - 1
    errors: list[OSError] = []
    for dirpath, dirnames, _ in os.walk(base, onerror=errors.append):
        rel = Path(dirpath).relative_to(base)
        if depth is not None and len(rel.parts) >= depth:
            dirnames[:] = []
        dirnames[:] = [d for d in dirnames if not is_excluded_path(rel / d)]
    return bool(errors)


def _count_matches(repo_root: Path, pattern: str) -> int:
    """Files matching ``pattern`` under the root.

    Below the pattern's wildcard-free prefix, the trees every scan excludes
    (``doc_graph.is_excluded_path``: ``.git``, ``.assess``, ``node_modules``,
    ``.venv`` ...) are not counted; a prefix that names one on purpose still
    counts. Raises ``Unverifiable`` when the prefix directory is missing, the
    glob cannot be evaluated, a subtree cannot be read, or the pattern matched
    only directories, so none of those reads as a wrong count. ``_count_claims``
    guarantees a wildcard in the pattern, so the prefix search always ends.
    """
    root = repo_root.resolve()
    parts = Path(pattern).parts
    split = next(i for i, part in enumerate(parts) if _GLOB_CHARS.search(part))
    base = root.joinpath(*parts[:split])
    try:
        if not base.is_dir() or not base.resolve().is_relative_to(root):
            raise Unverifiable
        if _unreadable_below(base, parts[split:]):
            raise Unverifiable
        dirs = count = 0
        for match in root.glob(pattern):
            if is_excluded_path(match.relative_to(base).parent):
                continue
            if match.is_file():
                if match.resolve().is_relative_to(root):
                    count += 1
            elif match.is_dir():
                dirs += 1
    except (OSError, ValueError, NotImplementedError, RuntimeError) as error:
        raise Unverifiable from error
    if dirs and (not count or "**" not in parts):
        # Only directories, or a flat pattern mixing files and directories:
        # the sentence may count the directories, which this kind cannot check.
        raise Unverifiable
    return count


def _has_ci_config(repo_root: Path) -> bool:
    return any((repo_root / rel).exists() for rel in CI_CONFIG_PATHS)


def _verify_enforcement(repo_root: Path, claim: Claim) -> dict[str, Any] | None:
    for rel in CI_CONFIG_PATHS + TASK_RUNNER_PATHS:
        if is_referenced_in(repo_root, claim.path, rel):
            return None
    return {"reason": "no CI configuration or task runner references the script"}


def _verify_pin(repo_root: Path, claim: Claim) -> dict[str, Any] | None:
    version = claim.fields["version"]
    if is_referenced_in(repo_root, version, claim.path):
        return None
    if not (repo_root / claim.path).exists():
        return {"reason": "pinned file does not exist"}
    return {"reason": "pinned file does not contain the version"}


def _verify_count(repo_root: Path, claim: Claim) -> dict[str, Any] | None:
    actual = _count_matches(repo_root, claim.path)
    if count_within_tolerance(claim.fields["claimed"], actual):
        return None
    return {"actual": actual,
            "reason": "the number of files matching the pattern differs from the claim"}


_EXTRACTORS: tuple[Callable[[str, int], list[Claim]], ...] = (
    _enforcement_claims,
    _pin_claims,
    _count_claims,
)
_VERIFIERS: dict[str, Callable[[Path, Claim], dict[str, Any] | None]] = {
    "enforcement": _verify_enforcement,
    "pin": _verify_pin,
    "count": _verify_count,
}


def extract_claims(text: str) -> list[Claim]:
    """Every claim pattern in ``text``, in document order (before any
    repository-dependent skip, such as enforcement with no CI configured)."""
    claims: list[Claim] = []
    for block in _paragraphs(text):
        for line, sentence in _sentences(block):
            for extract in _EXTRACTORS:
                claims.extend(extract(sentence, line))
    return claims


def empty_block() -> dict[str, Any]:
    return {"total": 0, "verified": 0, "failed": 0, "failures": []}


def scan_instruction_claims(repo_root: Path | str, files: Iterable[str]) -> dict[str, Any]:
    """Extract and verify the claims in each instruction file.

    ``files`` are paths relative to ``repo_root`` (the keys of the core's
    ``instruction_files``). A file that cannot be read as UTF-8 is skipped, and
    two keys that resolve to the same file (an ``AGENTS.md`` symlinked to
    ``CLAUDE.md``) are scanned once, under the first key.

    Returns ``{total, verified, failed, failures}``; each failure carries
    ``file``, ``line``, ``kind``, ``path`` (the script, pinned file or counted
    pattern) and ``reason``, plus the kind's own fields (``version`` for a pin,
    ``claimed`` and ``actual`` for a count).
    """
    root = Path(repo_root)
    block = empty_block()
    seen: set[Path] = set()
    ci_configured = _has_ci_config(root)
    for rel in files:
        candidate = root / rel
        try:
            real = candidate.resolve()
            text = candidate.read_text(encoding="utf-8")
        except (OSError, RuntimeError, UnicodeDecodeError):
            continue
        if real in seen:
            continue
        seen.add(real)
        for claim in extract_claims(text):
            if claim.kind == "enforcement" and not ci_configured:
                continue  # nothing to check against: unverifiable, not false
            try:
                detail = _VERIFIERS[claim.kind](root, claim)
            except Unverifiable:
                continue  # the repository cannot settle it: skipped, not false
            block["total"] += 1
            if detail is None:
                block["verified"] += 1
                continue
            block["failed"] += 1
            block["failures"].append({
                "file": rel, "line": claim.line, "kind": claim.kind,
                "path": claim.path, **claim.fields, **detail,
            })
    return block
