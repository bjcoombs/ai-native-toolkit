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
  skipped.

A sentence that fits no kind is skipped silently. Adding a kind means one
extractor in ``_EXTRACTORS`` (sentence -> claims) and one verifier in
``_VERIFIERS`` (claim -> None when it holds, else extra fields for the failure).

Sentences are read per paragraph, so a claim wrapped across lines is still
found; its ``line`` is the 1-based line the sentence starts on. Fenced code is
not prose and is not read.
"""
from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

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
_COUNT = re.compile(r"(?<![\w.,%$/-])(\d+)(?=\s+[A-Za-z])")
_GLOB_CHARS = re.compile(r"[*?]")
COUNT_TOLERANCE = 0.10
COUNT_MIN_DELTA = 2

_FENCE = re.compile(r"^\s*(```|~~~)")
# A line that starts its own block rather than continuing the paragraph above.
_BLOCK_START = re.compile(r"^\s*(?:#{1,6}\s|[-*+]\s|\d+[.)]\s|\||>)")
_HEADING = re.compile(r"^\s*#{1,6}\s")
_SENTENCE_END = re.compile(r"[.!?](?=\s|$)")


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
    spans = _BACKTICK_SPAN.findall(sentence)
    patterns = [s for s in spans if _GLOB_CHARS.search(s) and not re.search(r"\s", s)]
    if len(patterns) != 1:
        return []
    pattern = patterns[0].removeprefix("./")
    if pattern.startswith(("/", "~")) or ".." in Path(pattern).parts:
        return []
    numbers = _COUNT.findall(_BACKTICK_SPAN.sub(" ", sentence))
    if len(numbers) != 1:
        return []
    return [Claim("count", line, pattern, {"claimed": int(numbers[0])})]


def count_within_tolerance(claimed: int, actual: int) -> bool:
    """True when the difference is at most the larger of 10% or 2."""
    allowed = max(COUNT_TOLERANCE * max(claimed, actual), COUNT_MIN_DELTA)
    return abs(claimed - actual) <= allowed


def _count_matches(repo_root: Path, pattern: str) -> int:
    root = repo_root.resolve()
    try:
        matches = [p for p in root.glob(pattern)
                   if ".git" not in p.relative_to(root).parts and p.is_file()]
    except (OSError, ValueError, NotImplementedError):
        return 0
    return len(matches)


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
            detail = _VERIFIERS[claim.kind](root, claim)
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
