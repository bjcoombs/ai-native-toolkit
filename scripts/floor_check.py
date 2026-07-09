#!/usr/bin/env python3
"""Floor enforcement checks for the acceptance-contract workflow.

Two independent checks, exposed as subcommands and as pure functions the CI
workflow (``.github/workflows/floor.yml``) and pytest both drive:

``markers``
    Base-vs-head *removal detection*. For each marked file, fail iff a floor
    token (the marker comment or a gate invocation string) was present on the
    merge-base but is absent on the PR head. This is deliberately NOT an
    unconditional grep: the markers are added later in the marathon (tasks that
    wire the gates into the skills), so an unconditional check would turn every
    intermediate PR red. Removal detection arms itself automatically the moment
    a marker lands and bites only when one is taken away -- including when the
    whole marked file is deleted.

``clauses``
    Unconditional integrity check of ``FLOOR.md``: the file must exist and each
    of the four clauses must be present, anchor *and* key phrase, so a PR that
    guts a clause's text while leaving its anchor comment still fails.

Stdlib only; runnable as ``python scripts/floor_check.py <subcommand>``.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

# ── Floor tokens ─────────────────────────────────────────────────────────────

MARKER = "<!-- floor:cold-verify-completion -->"
INVOCATIONS = ("start_gate.py", "spawn_verifier.py", "complete_gate.py")
FLOOR_TOKENS = (MARKER, *INVOCATIONS)

# Files that carry a floor obligation (repo-root-relative). The markers are not
# all present yet -- removal detection is a no-op for a token a file never had.
MARKED_FILES = (
    "skills/marathon/SKILL.md",
    "skills/pr-review-merge/SKILL.md",
    "commands/tm.md",
    "commands/issues.md",
)

# FLOOR.md clause integrity: each clause must carry its anchor AND a distinctive
# phrase, so gutting the prose while keeping the anchor comment still fails.
FLOOR_FILE = "FLOOR.md"
REQUIRED_CLAUSES = {
    "i": ("<!-- floor-clause:i -->", "run-complete"),
    "ii": ("<!-- floor-clause:ii -->", "unamendable"),
    "iii": ("<!-- floor-clause:iii -->", "out-of-band"),
    "iv": ("<!-- floor-clause:iv -->", "immutab"),
}


# ── Pure logic (unit-tested) ─────────────────────────────────────────────────

def removed_tokens(
    base_text: str | None,
    head_text: str | None,
    tokens=FLOOR_TOKENS,
) -> list[str]:
    """Tokens present in ``base_text`` but absent from ``head_text``.

    ``base_text is None`` means the file did not exist on the base -> nothing
    could be removed. ``head_text is None`` (or empty) means the file was
    deleted on head -> every token the base carried counts as removed.
    """
    if base_text is None:
        return []
    present_in_base = [t for t in tokens if t in base_text]
    if not present_in_base:
        return []
    head = head_text or ""
    return [t for t in present_in_base if t not in head]


def missing_clauses(floor_text: str | None) -> list[str]:
    """Clause ids whose anchor or key phrase is missing from ``FLOOR.md``.

    ``None`` (file absent) reports every clause missing.
    """
    if floor_text is None:
        return list(REQUIRED_CLAUSES)
    missing = []
    for clause_id, required in REQUIRED_CLAUSES.items():
        if any(token not in floor_text for token in required):
            missing.append(clause_id)
    return missing


# ── Git plumbing ─────────────────────────────────────────────────────────────

def _git_show(ref: str, path: str) -> str | None:
    """Content of ``path`` at ``ref``, or ``None`` if it did not exist there."""
    result = subprocess.run(
        ["git", "show", f"{ref}:{path}"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    return result.stdout


def _read_head(path: str) -> str | None:
    """Content of ``path`` in the working tree, or ``None`` if absent."""
    p = Path(path)
    if not p.exists():
        return None
    return p.read_text(encoding="utf-8")


# ── Subcommands ──────────────────────────────────────────────────────────────

def cmd_markers(args: argparse.Namespace) -> int:
    base = args.base
    files = args.files or list(MARKED_FILES)
    failed = False
    for path in files:
        base_text = _git_show(base, path)
        head_text = _read_head(path)
        removed = removed_tokens(base_text, head_text)
        if removed:
            failed = True
            for token in removed:
                print(
                    f"FAIL {path}: floor token removed -> {token!r} "
                    f"(present on {base}, absent on head)"
                )
        else:
            carried = [t for t in FLOOR_TOKENS if base_text and t in base_text]
            state = f"{len(carried)} token(s) intact" if carried else "no floor tokens (ok)"
            print(f"ok   {path}: {state}")
    if failed:
        print(
            "\nFloor markers were removed. Restore them, or obtain the "
            "maintainer's out-of-band sign-off (FLOOR.md clause iii)."
        )
        return 1
    print("\nMarker check passed: no floor tokens removed.")
    return 0


def cmd_clauses(args: argparse.Namespace) -> int:
    floor_text = _read_head(args.floor)
    if floor_text is None:
        print(f"FAIL {args.floor} does not exist -- the floor file is mandatory.")
        return 1
    missing = missing_clauses(floor_text)
    if missing:
        print(f"FAIL {args.floor}: clauses not intact -> {', '.join(missing)}")
        print("Each clause needs its anchor comment and its key phrase.")
        return 1
    print(f"ok   {args.floor}: all four clauses intact.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_markers = sub.add_parser(
        "markers", help="base-vs-head removal detection for floor tokens"
    )
    p_markers.add_argument(
        "--base", required=True, help="git ref for the merge-base (e.g. origin/main)"
    )
    p_markers.add_argument(
        "--files", nargs="*", help="override the marked-file list (defaults to all)"
    )
    p_markers.set_defaults(func=cmd_markers)

    p_clauses = sub.add_parser("clauses", help="FLOOR.md four-clause integrity")
    p_clauses.add_argument("--floor", default=FLOOR_FILE, help="path to FLOOR.md")
    p_clauses.set_defaults(func=cmd_clauses)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
