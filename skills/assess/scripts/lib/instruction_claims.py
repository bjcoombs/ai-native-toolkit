"""Verify the checkable claims an agent instruction file makes about the repo.

An instruction file that says "`scripts/check-x.sh` is enforced in CI" or "Node
20.11.0 is pinned in `.nvmrc`" is a map an agent trusts without checking. When
the workflow stopped calling the script, or the pin moved, the sentence still
reads as true. This module extracts those sentences and checks each against the
repository, no model: a failed claim is a lying signal with a file and a line.

Claim kinds:

- ``enforcement``: a backticked script path (``.sh``, ``.py``, ``.js`` ...) in a
  sentence holding "enforced", "runs in", "checked by" or the word "CI". Verified
  when the path occurs in any file under ``.github/workflows/`` (the reference
  search of ``lib.evidence_check``). The whole directory is searched even when
  the sentence names a job or workflow: naming parses poorly, and a script that
  no workflow calls is the failure worth catching.
- ``pin``: a sentence holding "pinned in" followed by a backticked file, plus
  exactly one dotted numeric version (``20.11.0``) on either side of the phrase.
  Verified when the file exists and contains the version string. A missing file
  is a failed claim; a sentence with no version, or with two different
  versions, is skipped.

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

WORKFLOWS_DIR = ".github/workflows"

_SCRIPT_EXTENSIONS = "sh|bash|zsh|py|js|mjs|cjs|ts|rb|pl|ps1"
# A script path inside a backticked span: `scripts/check-x.sh` or the path in
# `bash scripts/check-x.sh --fix`.
_SCRIPT_IN_SPAN = re.compile(rf"(?<![\w./-])([\w./-]*\w\.(?:{_SCRIPT_EXTENSIONS}))(?![\w/-])")
_BACKTICK_SPAN = re.compile(r"`([^`]+)`")
_ENFORCEMENT_TRIGGER = re.compile(r"(?i:\benforced\b|\bruns in\b|\bchecked by\b)|\bCI\b")

_PINNED_IN = re.compile(r"pinned in\s+`([^`\s]+)`", re.IGNORECASE)
_VERSION = re.compile(r"(?<![\d.])(\d+(?:\.\d+)+)(?!\.?\d)")

_FENCE = re.compile(r"^\s*(```|~~~)")
# A line that starts its own block rather than continuing the paragraph above.
_BLOCK_START = re.compile(r"^\s*(?:#{1,6}\s|[-*+]\s|\d+[.)]\s|\||>)")
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
            block = [] if fenced or not line.strip() else [(number, line)]
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


def _verify_enforcement(repo_root: Path, claim: Claim) -> dict[str, Any] | None:
    return None if is_referenced_in(repo_root, claim.path, WORKFLOWS_DIR) else {}


def _verify_pin(repo_root: Path, claim: Claim) -> dict[str, Any] | None:
    version = claim.fields["version"]
    return None if is_referenced_in(repo_root, version, claim.path) else {}


_EXTRACTORS: tuple[Callable[[str, int], list[Claim]], ...] = (
    _enforcement_claims,
    _pin_claims,
)
_VERIFIERS: dict[str, Callable[[Path, Claim], dict[str, Any] | None]] = {
    "enforcement": _verify_enforcement,
    "pin": _verify_pin,
}


def extract_claims(text: str) -> list[Claim]:
    """Every checkable claim in ``text``, in document order."""
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
    ``file``, ``line``, ``kind`` and ``path`` (the script or pinned file), plus
    the kind's own fields (``version`` for a pin).
    """
    root = Path(repo_root)
    block = empty_block()
    seen: set[Path] = set()
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
