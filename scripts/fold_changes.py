#!/usr/bin/env python3
"""Fold the pending version notes into each counter's history (issue #1011).

During a version freeze a PR that would bump a version counter adds one file,
``skills/assess/changes/<issue>.<counter>.md``, holding its note as one or more
markdown bullets. No two PRs write the same file, so the notes never conflict.

The release PR runs, from ``scripts/``::

    uv run --locked python fold_changes.py --release 3.0.0

For each counter with notes it writes one history entry directly above the
counter's assignment line, ``# <new> (<release> release): <n> changes since
<old>.`` followed by the bullets in issue order, sets the counter to ``<new>``
and deletes the notes. ``<new>`` is the next minor for a semver counter, the
next integer for an integer one and ``default-<date>`` for the maturity
model; ``--set counter=value`` overrides it. ``--check`` only validates the notes.
"""
from __future__ import annotations

import argparse
import datetime
import re
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CHANGES_DIR = Path("skills/assess/changes")
FRAGMENT_NAME = re.compile(r"^(?P<issue>[1-9][0-9]*)\.(?P<counter>[a-z_]+)\.md$")
# Files in the changes directory that are not notes.
NOT_NOTES = frozenset({"README.md"})
NUMBER_WORDS = ("zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten")


@dataclass(frozen=True)
class Counter:
    """A version counter and the file whose comment holds its history."""

    path: Path
    assignment: re.Pattern[str]  # group "value" is the counter's current value
    kind: str  # "semver", "int" or "dated"
    noun: str = "changes"
    lead: tuple[str, ...] = ()  # comment lines written before a new entry


COUNTERS: dict[str, Counter] = {
    "artifact_schema": Counter(
        Path("skills/assess/scripts/lib/artifact_schema.py"),
        re.compile(r'^ARTIFACT_SCHEMA_VERSION = "(?P<value>[0-9]+\.[0-9]+\.[0-9]+)"$'),
        "semver",
    ),
    "assess_method": Counter(
        Path("skills/assess/scripts/lib/artifact_schema.py"),
        re.compile(r"^ASSESS_METHOD_VERSION = (?P<value>[0-9]+)$"),
        "int",
        noun="rule changes",
    ),
    "level_result": Counter(
        Path("skills/assess/scripts/lib/level_tally.py"),
        re.compile(r"^LEVEL_RESULT_VERSION = (?P<value>[0-9]+)$"),
        "int",
    ),
    "signals_schema": Counter(
        Path("skills/assess/scripts/lib/signals.py"),
        re.compile(r"^SIGNALS_SCHEMA_VERSION = (?P<value>[0-9]+)$"),
        "int",
    ),
    "levels_default": Counter(
        Path("skills/assess/levels/default.toml"),
        re.compile(r'^version = "(?P<value>default-[0-9.]+)"$'),
        "dated",
        lead=("#",),
    ),
}


class FoldError(Exception):
    """A note or a history file the fold cannot read."""


@dataclass(frozen=True)
class Note:
    path: Path
    issue: int
    counter: str
    bullets: tuple[tuple[str, ...], ...]


def _bullets(text: str) -> tuple[tuple[str, ...], ...]:
    """Split a note into bullets: a ``- `` line and its ``  `` continuation lines."""
    bullets: list[list[str]] = []
    for line in text.rstrip("\n").split("\n"):
        line = line.rstrip()
        if line.startswith("- ") and line[2:].strip():
            bullets.append([line])
        elif line.startswith("  ") and line.strip() and bullets:
            bullets[-1].append(line)
        else:
            raise FoldError(f"not a bullet or its continuation: {line!r}")
    if not bullets:
        raise FoldError("holds no bullet")
    return tuple(tuple(b) for b in bullets)


def read_notes(root: Path) -> list[Note]:
    """Every note under ``root``, in issue order; raises FoldError listing each bad one."""
    notes: list[Note] = []
    errors: list[str] = []
    directory = root / CHANGES_DIR
    for path in sorted(directory.iterdir()) if directory.is_dir() else []:
        if path.name in NOT_NOTES:
            continue
        match = FRAGMENT_NAME.match(path.name)
        if not match:
            errors.append(f"{path.name}: name is not <issue>.<counter>.md")
            continue
        counter = match.group("counter")
        if counter not in COUNTERS:
            errors.append(f"{path.name}: unknown counter {counter!r} (known: {', '.join(sorted(COUNTERS))})")
            continue
        try:
            bullets = _bullets(path.read_text(encoding="utf-8"))
        except FoldError as exc:
            errors.append(f"{path.name}: {exc}")
            continue
        notes.append(Note(path, int(match.group("issue")), counter, bullets))
    if errors:
        raise FoldError("\n".join(errors))
    return sorted(notes, key=lambda n: (n.issue, n.path.name))


def next_value(counter: Counter, value: str, date: datetime.date) -> str:
    if counter.kind == "semver":
        major, minor, _ = value.split(".")
        return f"{major}.{int(minor) + 1}.0"
    if counter.kind == "int":
        return str(int(value) + 1)
    return "default-" + date.strftime("%Y.%m.%d")


def _count(n: int, noun: str) -> str:
    word = NUMBER_WORDS[n] if n < len(NUMBER_WORDS) else str(n)
    return f"{word} {noun[:-1] if n == 1 else noun}"


def fold_counter(lines: list[str], name: str, notes: list[Note], release: str, new: str | None,
                 date: datetime.date) -> list[str]:
    """``lines`` with the history entry for ``name`` written and the counter set."""
    counter = COUNTERS[name]
    hits = [i for i, line in enumerate(lines) if counter.assignment.match(line)]
    if len(hits) != 1:
        raise FoldError(f"{counter.path}: expected one {name} assignment line, found {len(hits)}")
    at = hits[0]
    match = counter.assignment.match(lines[at])
    assert match is not None
    old = match.group("value")
    new = new or next_value(counter, old, date)
    bullets = [b for note in notes for b in note.bullets]
    entry = [*counter.lead, f"# {new} ({release} release): {_count(len(bullets), counter.noun)} since {old}."]
    entry += ["# " + line for bullet in bullets for line in bullet]
    insert = at
    while insert > 0 and not lines[insert - 1].strip():
        insert -= 1
    start, end = match.span("value")
    assigned = lines[at][:start] + new + lines[at][end:]
    return lines[:insert] + entry + lines[insert:at] + [assigned] + lines[at + 1:]


def fold(root: Path, release: str, date: datetime.date, overrides: dict[str, str]) -> list[str]:
    """Fold every note under ``root``; return the counters folded."""
    notes = read_notes(root)
    unknown = sorted(set(overrides) - set(COUNTERS))
    if unknown:
        raise FoldError(f"--set names unknown counters: {', '.join(unknown)}")
    by_counter: dict[str, list[Note]] = {}
    for note in notes:
        by_counter.setdefault(note.counter, []).append(note)
    texts: dict[Path, list[str]] = {}
    for name, group in by_counter.items():
        path = COUNTERS[name].path
        lines = texts.get(path) or (root / path).read_text(encoding="utf-8").split("\n")
        texts[path] = fold_counter(lines, name, group, release, overrides.get(name), date)
    for path, lines in texts.items():
        # LF on every platform: a Windows fold must not turn the files into CRLF.
        (root / path).write_text("\n".join(lines), encoding="utf-8", newline="\n")
    for note in notes:
        note.path.unlink()
    return sorted(by_counter)


def _overrides(pairs: list[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for pair in pairs:
        name, sep, value = pair.partition("=")
        if not sep or not value:
            raise FoldError(f"--set wants counter=value, got {pair!r}")
        result[name] = value
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0] if __doc__ else None)
    parser.add_argument("--release", help="the plugin release, for example 3.0.0")
    parser.add_argument("--date", type=datetime.date.fromisoformat, default=datetime.date.today(),
                        help="release date for the maturity model version (default: today)")
    parser.add_argument("--set", action="append", default=[], metavar="COUNTER=VALUE",
                        help="the new value of a counter, in place of the next one")
    parser.add_argument("--check", action="store_true", help="validate the notes and write nothing")
    parser.add_argument("--root", type=Path, default=REPO_ROOT, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    try:
        if args.check:
            notes = read_notes(args.root)
            print(f"{len(notes)} notes valid")
            return 0
        if not args.release:
            parser.error("--release is required unless --check is given")
        folded = fold(args.root, args.release, args.date, _overrides(args.set))
    except FoldError as exc:
        print(exc, file=sys.stderr)
        return 1
    print("folded: " + (", ".join(folded) or "nothing"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
