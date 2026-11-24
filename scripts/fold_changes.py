#!/usr/bin/env python3
"""Fold the pending version notes into each counter's history file (issues #1011, #1262).

During a version freeze a PR that would bump a version counter adds one file,
``skills/assess/changes/<issue>.<counter>.md``, holding its note as one or more
markdown bullets. No two PRs write the same file, so the notes never conflict.

The release PR runs ``make fold RELEASE=3.0.0`` from the repository root, which
runs ``fold_changes.py --release 3.0.0`` here and then ``make regen``, since the
generated schemas, catalog and goldens stamp the counter values.

Each counter's history lives in its own file, ``skills/assess/changes/history/<counter>.md``,
newest entry first. The source file keeps one comment line directly above the
counter that points to it, so a fold changes only the counter's value there and
the source file's length never grows with the history.

For each counter with notes the fold writes one history entry at the top of the
history file, below its marker line: ``<new> (<release> release): <n> changes
since <old>.`` followed by the bullets in issue order. It sets the counter to
``<new>`` and deletes the notes. ``<new>`` is the next minor for a semver
counter, the next integer for an integer one and ``default-<date>`` for the
maturity model; ``--set counter=value`` overrides it. ``--check`` validates the
notes and the layout (pointer line, no history comment left in a source file,
history file with one marker) and writes nothing.
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
HISTORY_DIR = CHANGES_DIR / "history"
FRAGMENT_NAME = re.compile(r"^(?P<issue>[1-9][0-9]*)\.(?P<counter>[a-z_]+)\.md$")
# Entries in the changes directory that are not notes.
NOT_NOTES = frozenset({"README.md", HISTORY_DIR.name})
NUMBER_WORDS = ("zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten")
# The fold writes each new entry directly below this line of a history file.
MARKER = "<!-- make fold writes each new entry below this line, newest first. -->"
# A history entry left as a comment in a source file: ``# 1.4.0 (also): ...``,
# ``# 39 (4.0.0 release): ...``, ``# default-2026.11.24 (...): ...``, ``# 2: ...``.
HISTORY_COMMENT = re.compile(r"^#\s*(default-[0-9.]+|[0-9]+(\.[0-9]+\.[0-9]+)?)( \([^)]*\))?:\s")


@dataclass(frozen=True)
class Counter:
    """A version counter, the file that assigns it and how its next value is picked."""

    path: Path
    assignment: re.Pattern[str]  # group "value" is the counter's current value
    kind: str  # "semver", "int" or "dated"
    noun: str = "changes"
    # Why the fold will not pick this counter's next value: the release must pass --set.
    explicit: str = ""


COUNTERS: dict[str, Counter] = {
    "artifact_schema": Counter(
        Path("skills/assess/scripts/lib/artifact_schema.py"),
        re.compile(r'^ARTIFACT_SCHEMA_VERSION = "(?P<value>[0-9]+\.[0-9]+\.[0-9]+)"$'),
        "semver",
    ),
    "assess_method": Counter(
        Path("skills/assess/scripts/lib/assess_method.py"),
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
    ),
    "fleet_data": Counter(
        Path("skills/assess/scripts/lib/fleet/fleet_data.py"),
        re.compile(r"^FLEET_DATA_VERSION = (?P<value>[0-9]+)$"),
        "int",
    ),
    "achievements": Counter(
        Path("skills/assess/scripts/lib/fleet/fleet_achievements.py"),
        re.compile(r"^ACHIEVEMENTS_VERSION = (?P<value>[0-9]+)$"),
        "int",
    ),
    "fleet_row": Counter(
        Path("skills/assess/scripts/lib/fleet/runner.py"),
        re.compile(r"^FLEET_ROW_VERSION = (?P<value>[0-9]+)$"),
        "int",
        explicit="the fleet history readers accept only the current row version, so a bump"
                 " moves them too; --set fleet_row=<current> records the notes without one",
    ),
}


def history_path(name: str) -> Path:
    """The history file of counter ``name``, relative to the repository root."""
    return HISTORY_DIR / f"{name}.md"


def pointer(name: str) -> str:
    """The comment line directly above counter ``name`` that points to its history file."""
    return f"# History: {history_path(name).as_posix()}, newest entry first."


class FoldError(Exception):
    """A note, a source file or a history file the fold cannot read."""


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


def _read_lines(root: Path, path: Path) -> list[str]:
    if not (root / path).is_file():
        raise FoldError(f"{path.as_posix()}: missing")
    return (root / path).read_text(encoding="utf-8").split("\n")


def _assignment(lines: list[str], name: str) -> int:
    counter = COUNTERS[name]
    hits = [i for i, line in enumerate(lines) if counter.assignment.match(line)]
    if len(hits) != 1:
        raise FoldError(f"{counter.path.as_posix()}: expected one {name} assignment line, found {len(hits)}")
    return hits[0]


def _marker(lines: list[str], name: str) -> int:
    hits = [i for i, line in enumerate(lines) if line == MARKER]
    if len(hits) != 1:
        raise FoldError(f"{history_path(name).as_posix()}: expected one marker line {MARKER!r}, found {len(hits)}")
    return hits[0]


def layout_errors(root: Path, name: str) -> list[str]:
    """What is wrong with counter ``name``'s source and history files under ``root``."""
    counter = COUNTERS[name]
    shown = counter.path.as_posix()
    errors: list[str] = []
    try:
        lines = _read_lines(root, counter.path)
        at = _assignment(lines, name)
    except FoldError as exc:
        errors.append(str(exc))
    else:
        if at == 0 or lines[at - 1] != pointer(name):
            errors.append(f"{shown}: the line above the {name} assignment must read {pointer(name)!r}")
        # The comment block above the counter, blank lines included: no history entry stays there.
        top = at
        while top > 0 and (lines[top - 1].startswith("#") or not lines[top - 1].strip()):
            top -= 1
        left = [line for line in lines[top:at] if HISTORY_COMMENT.match(line)]
        if left:
            errors.append(f"{shown}: history comment above {name}; move it to "
                          f"{history_path(name).as_posix()}: {left[0]!r}")
    try:
        _marker(_read_lines(root, history_path(name)), name)
    except FoldError as exc:
        errors.append(str(exc))
    return errors


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


def fold_counter(source: list[str], history: list[str], name: str, notes: list[Note], release: str,
                 new: str | None, date: datetime.date) -> tuple[list[str], list[str]]:
    """``source`` with counter ``name`` set, and ``history`` with the new entry on top."""
    counter = COUNTERS[name]
    at = _assignment(source, name)
    match = counter.assignment.match(source[at])
    assert match is not None
    old = match.group("value")
    if new is None and counter.explicit:
        raise FoldError(f"{name} needs --set {name}=<value>: {counter.explicit}")
    new = new or next_value(counter, old, date)
    start, end = match.span("value")
    assigned = source[at][:start] + new + source[at][end:]
    if not counter.assignment.match(assigned):
        raise FoldError(f"--set {name}={new} is not a value {name} takes ({counter.assignment.pattern})")
    bullets = [b for note in notes for b in note.bullets]
    count = _count(len(bullets), counter.noun)
    tail = f"{count}, without a bump." if new == old else f"{count} since {old}."
    entry = [f"{new} ({release} release): {tail}", *(line for bullet in bullets for line in bullet)]
    top = _marker(history, name) + 1
    return (source[:at] + [assigned] + source[at + 1:],
            history[:top] + ["", *entry] + history[top:])


def fold(root: Path, release: str, date: datetime.date, overrides: dict[str, str]) -> list[str]:
    """Fold every note under ``root``; return the counters folded."""
    notes = read_notes(root)
    unknown = sorted(set(overrides) - set(COUNTERS))
    if unknown:
        raise FoldError(f"--set names unknown counters: {', '.join(unknown)}")
    by_counter: dict[str, list[Note]] = {}
    for note in notes:
        by_counter.setdefault(note.counter, []).append(note)
    unused = sorted(set(overrides) - set(by_counter))
    if unused:
        raise FoldError(f"--set names counters with no notes to fold: {', '.join(unused)}")
    errors = [error for name in sorted(by_counter) for error in layout_errors(root, name)]
    if errors:
        raise FoldError("\n".join(errors))
    texts: dict[Path, list[str]] = {}
    for name, group in by_counter.items():
        source, history = COUNTERS[name].path, history_path(name)
        texts[source], texts[history] = fold_counter(
            texts.get(source) or _read_lines(root, source), _read_lines(root, history),
            name, group, release, overrides.get(name), date)
    for path, lines in texts.items():
        # LF on every platform: a Windows fold must not turn the files into CRLF.
        (root / path).write_text("\n".join(lines), encoding="utf-8", newline="\n")
    for note in notes:
        note.path.unlink()
    return sorted(by_counter)


def check(root: Path) -> int:
    """Validate the notes and every counter's layout under ``root``; return the note count."""
    notes = read_notes(root)
    errors = [error for name in COUNTERS for error in layout_errors(root, name)]
    if errors:
        raise FoldError("\n".join(errors))
    return len(notes)


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
    parser.add_argument("--check", action="store_true",
                        help="validate the notes and the history layout, and write nothing")
    parser.add_argument("--root", type=Path, default=REPO_ROOT, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    try:
        if args.check:
            print(f"{check(args.root)} notes valid; history layout valid")
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
