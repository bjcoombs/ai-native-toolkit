#!/usr/bin/env python3
"""Floor enforcement checks for the acceptance-contract workflow.

Two independent checks, exposed as subcommands and as pure functions the CI
workflow (``.github/workflows/floor.yml``) and pytest both drive:

``markers``
    Base-vs-head *removal detection*. For each marked file, fail iff a floor
    token (the marker comment or a gate invocation string) was weakened between
    the merge-base and the PR head. "Weakened" is two complementary signals, not
    a bare "absent from the whole file" test:

    1. **Count decrease.** A token whose total occurrences drop base -> head is
       flagged. This is stricter than presence-anywhere: removing the one
       load-bearing anchor line still trips the check even when an *incidental*
       mention of the same string survives elsewhere in the file (e.g. a prose
       or documentation reference to the marker).
    2. **Anchor-line loss** (marker only). The cold-verify marker is load-bearing
       only as a *standalone line*; a backtick-wrapped mention inside prose is
       documentation, not the obligation. So if the base carried at least one
       standalone anchor line and the head carries none, the marker is flagged
       even when the raw occurrence count was held constant (e.g. the anchor
       line deleted and a fresh prose mention added to mask the count).

    A presence-anywhere test alone was a false-negative: the marathon skill
    documents the marker string in its own Retro Boundary prose, so deleting the
    real anchor line left the substring present and the check passed. The two
    signals above close that gap.

    This is deliberately NOT an unconditional grep: the markers are added later
    in the marathon (tasks that wire the gates into the skills), so an
    unconditional check would turn every intermediate PR red. Removal detection
    arms itself automatically the moment a marker lands and bites only when one
    is taken away -- including when the whole marked file is deleted.

    Neither the marked set nor the token set is a constant here. The marked set
    is *discovered* at the base ref (``_discover_marked_files``): every tracked
    file carrying a standalone anchor line, ``FLOOR.md`` excluded because it is
    the file that defines the marker. The token set is read from the
    ``floor-tokens`` fenced block of ``git show <base>:FLOOR.md``. Both are data
    the floor declares, so a file that moves, a file that arrives, and a token
    that is added are all enforced with no edit to this script.

``clauses``
    Unconditional integrity check of ``FLOOR.md``: the file must exist, it must
    declare its ``floor-tokens`` block with at least the four tokens the floor
    ships with, and each of the four clauses must be present, anchor *and* key
    phrase, so a PR that guts a clause's text while leaving its anchor comment
    still fails.

Stdlib only; runnable as ``python scripts/floor_check.py <subcommand>``.
Paths are relative to the current directory, which is the repository root.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

# ── The one anchor string ────────────────────────────────────────────────────
#
# The marker is the single token this script has to know by heart: it is the
# needle the discovery grep looks for. Every other token is declared by the
# floor itself, in FLOOR.md's floor-tokens block.

MARKER = "<!-- floor:cold-verify-completion -->"

FLOOR_FILE = "FLOOR.md"

# The fenced block in FLOOR.md that declares the floor tokens, one per line.
TOKEN_BLOCK_FENCE = "```floor-tokens"

# The floor ships with four tokens (the marker plus three gate invocations).
# Shrinking that set is a floor change, not a refactor, so a block carrying
# fewer than this is refused.
MINIMUM_TOKEN_COUNT = 4

# FLOOR.md clause integrity: each clause must carry its anchor AND its
# distinctive phrases, so gutting the prose while keeping the anchor comment
# still fails.
REQUIRED_CLAUSES = {
    "i": ("<!-- floor-clause:i -->", "run-complete"),
    "ii": ("<!-- floor-clause:ii -->", "unamendable"),
    "iii": ("<!-- floor-clause:iii -->", "out-of-band", "floor-signoff"),
    "iv": ("<!-- floor-clause:iv -->", "immutab"),
}


class FloorTokenError(ValueError):
    """FLOOR.md does not declare a usable floor-tokens block."""


# ── Pure logic (unit-tested) ─────────────────────────────────────────────────

def _parse_token_block(floor_text: str | None) -> list[str]:
    """Floor tokens declared by ``FLOOR.md``, in declaration order.

    The tokens live in exactly one fenced block whose opening fence line is
    ``TOKEN_BLOCK_FENCE``, one token per line. Raises ``FloorTokenError`` when
    the block is absent, duplicated, unterminated, or carries fewer than
    ``MINIMUM_TOKEN_COUNT`` tokens -- shrinking the floor's token set is a floor
    change and has to go red rather than quietly narrow the check.
    """
    if floor_text is None:
        raise FloorTokenError(
            f"{FLOOR_FILE} is absent, so its {TOKEN_BLOCK_FENCE!r} token block "
            f"cannot be read"
        )
    lines = floor_text.splitlines()
    opens = [i for i, line in enumerate(lines) if line.strip() == TOKEN_BLOCK_FENCE]
    if not opens:
        raise FloorTokenError(
            f"no {TOKEN_BLOCK_FENCE!r} token block in {FLOOR_FILE}: the floor "
            f"must declare its tokens"
        )
    if len(opens) > 1:
        raise FloorTokenError(
            f"{len(opens)} {TOKEN_BLOCK_FENCE!r} token blocks in {FLOOR_FILE}: "
            f"the floor must declare exactly one"
        )
    tokens: list[str] = []
    closed = False
    for line in lines[opens[0] + 1:]:
        if line.startswith("```"):
            closed = True
            break
        token = line.strip()
        if token:
            tokens.append(token)
    if not closed:
        raise FloorTokenError(
            f"the {TOKEN_BLOCK_FENCE!r} token block in {FLOOR_FILE} is never closed"
        )
    if len(tokens) < MINIMUM_TOKEN_COUNT:
        raise FloorTokenError(
            f"the {TOKEN_BLOCK_FENCE!r} token block in {FLOOR_FILE} declares "
            f"{len(tokens)} token(s); the floor requires at least "
            f"{MINIMUM_TOKEN_COUNT}"
        )
    return tokens


def standalone_anchor_count(text: str | None, marker: str = MARKER) -> int:
    """Number of *standalone* anchor lines: lines whose stripped content is the
    bare marker.

    A backtick-wrapped mention inside prose (``- the `<!-- ... -->` markers``)
    strips to something other than the bare marker, so it is documentation, not
    a load-bearing anchor, and does not count here.
    """
    if not text:
        return 0
    return sum(1 for line in text.splitlines() if line.strip() == marker)


def removed_tokens(
    base_text: str | None,
    head_text: str | None,
    tokens,
) -> list[str]:
    """Floor tokens *weakened* from ``base_text`` to ``head_text``.

    ``tokens`` is the set the floor declares (see ``_parse_token_block``); it is
    passed in rather than read from a constant so the enforced set is whatever
    ``FLOOR.md`` says it is.

    A token is flagged when either signal fires (see the module docstring):

    * its total occurrence count decreased base -> head, or
    * (marker only) the base carried a standalone anchor line and the head
      carries none -- catching an anchor deletion masked by a fresh prose
      mention that keeps the raw count constant.

    ``base_text is None`` means the file did not exist on the base -> nothing
    could be removed. ``head_text is None`` (or empty) means the file was
    deleted on head -> every token the base carried counts as removed.
    """
    if base_text is None:
        return []
    head = head_text or ""
    removed = []
    for token in tokens:
        base_count = base_text.count(token)
        if base_count == 0:
            continue  # not carried on base -> nothing to remove
        if head.count(token) < base_count:
            removed.append(token)
            continue
        # Count held constant: for the anchor marker, still require a standalone
        # anchor line to survive, so deleting the anchor and adding a prose
        # mention to mask the count is caught.
        if token == MARKER and standalone_anchor_count(base_text) > 0 \
                and standalone_anchor_count(head) == 0:
            removed.append(token)
    return removed


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

def _git_show(ref: str, path: str, cwd: str | Path | None = None) -> str | None:
    """Content of ``path`` at ``ref``, or ``None`` if it did not exist there."""
    result = subprocess.run(
        ["git", "show", f"{ref}:{path}"],
        capture_output=True,
        text=True,
        cwd=cwd,
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


def _discover_marked_files(
    base_ref: str,
    marker: str,
    cwd: str | Path | None = None,
) -> list[str]:
    """Files carrying a floor obligation at ``base_ref``, discovered by token.

    ``git grep`` finds every tracked file mentioning the marker at that ref;
    the anchor filter then keeps only those whose content at the ref holds a
    *standalone* anchor line, which drops the incidental carriers (prose that
    quotes the marker, source that defines it). ``FLOOR.md`` is excluded outright:
    it is the file that declares the marker, so its own mention is a definition,
    not an obligation.
    """
    result = subprocess.run(
        ["git", "grep", "-l", "-F", marker, base_ref, "--", f":!{FLOOR_FILE}"],
        capture_output=True,
        text=True,
        cwd=cwd,
    )
    prefix = f"{base_ref}:"
    discovered = []
    for line in result.stdout.splitlines():
        path = line[len(prefix):] if line.startswith(prefix) else line.split(":", 1)[-1]
        if not path:
            continue
        if standalone_anchor_count(_git_show(base_ref, path, cwd=cwd), marker) > 0:
            discovered.append(path)
    return discovered


# ── Subcommands ──────────────────────────────────────────────────────────────

def cmd_markers(args: argparse.Namespace) -> int:
    base = args.base
    try:
        tokens = _parse_token_block(_git_show(base, FLOOR_FILE))
    except FloorTokenError as exc:
        print(f"FAIL {FLOOR_FILE} at {base}: {exc}")
        return 1
    files = list(args.files) if args.files else _discover_marked_files(base, MARKER)
    if not files:
        print(f"ok   no marked files at {base}: no floor obligation is armed yet.")
        return 0
    failed = False
    for path in files:
        base_text = _git_show(base, path)
        head_text = _read_head(path)
        removed = removed_tokens(base_text, head_text, tokens)
        if removed:
            failed = True
            for token in removed:
                print(
                    f"FAIL {path}: floor token weakened -> {token!r} "
                    f"(occurrences dropped, or its standalone anchor line was "
                    f"removed, between {base} and head)"
                )
        else:
            carried = [t for t in tokens if base_text and t in base_text]
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
    try:
        tokens = _parse_token_block(floor_text)
    except FloorTokenError as exc:
        print(f"FAIL {args.floor}: {exc}")
        print(
            "The floor declares its tokens; removing or shrinking that block "
            "narrows the check and needs the maintainer's out-of-band sign-off."
        )
        return 1
    missing = missing_clauses(floor_text)
    if missing:
        print(f"FAIL {args.floor}: clauses not intact -> {', '.join(missing)}")
        print("Each clause needs its anchor comment and its key phrase.")
        return 1
    print(
        f"ok   {args.floor}: all four clauses intact, "
        f"{len(tokens)} floor token(s) declared."
    )
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
        "--files",
        nargs="*",
        help="override the discovered marked-file set (defaults to discovery)",
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
