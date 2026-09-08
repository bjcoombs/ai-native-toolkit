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
    ``floor-tokens`` fenced block of ``git show <base>:FLOOR.md``, falling back
    to the working tree only while the base predates that block. Both are data
    the floor declares, so a file that moves, a file that arrives, and a token
    that is added are all enforced with no edit to this script.

    A move is *followed* rather than read as a deletion. ``git diff -M50%``
    maps each base path to its head path, and the comparison for a mapped pair
    is ``BASE:old`` against ``HEAD:new``, so a byte-identical relocation passes
    with no floor edit while a relocation that also weakens a token still fails
    on the token comparison. A mapped destination has to be a plausible
    component path (``_is_valid_component_path``); a "move" into an archive
    directory is a deletion wearing a rename and is reported as one, naming the
    rejected destination.

``protected``
    Directory-*role* classification of a changed-path list -- the one path
    decision the CI workflow makes, so the workflow itself carries no path
    literals to keep in sync. A path is protected when it falls in one of four
    roles: it lives under the component directory of a marked file (marked at
    the base ref *or* on the head side, so a component marked in the PR itself
    is protected from that PR on), it is gate code, it is canary code or
    fixtures, or it is floor core. Each role is a whole subtree rather than a
    basename allowlist, so the protected set survives a layout move that a
    hand-maintained path regex would silently drop.

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
import re
import subprocess
import sys
from pathlib import Path, PurePosixPath

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

# ── Component shapes ─────────────────────────────────────────────────────────
#
# The three paths a shipped component can live at. A marked file has to sit at
# one of them for a rename to read as a relocation rather than a deletion, and
# for the file to have a component *directory* the `protected` roles can span.
# The shapes are structural, not a list of names, so a component that is added
# or moved between them needs no edit here.

VALID_COMPONENT_RE = re.compile(
    r"^(?:skills/[^/]+/SKILL\.md"
    r"|plugins/[^/]+/skills/[^/]+/SKILL\.md"
    r"|commands/[^/]+\.md)$"
)

# ── Protected roles ──────────────────────────────────────────────────────────
#
# Whole subtrees, never basenames: narrowing any of these to the files that
# happen to live there today would quietly drop the rest on the next move.

ROLE_MARKED_COMPONENT = "marked-component"
ROLE_GATE_CODE = "gate-code"
ROLE_CANARY = "canary"
ROLE_FLOOR_CORE = "floor-core"

ROLES = (ROLE_MARKED_COMPONENT, ROLE_GATE_CODE, ROLE_CANARY, ROLE_FLOOR_CORE)

# The gates the canary suite drives.
GATE_CODE_PREFIXES = ("scripts/contract/",)

# The canary harness and the fixtures it certifies.
CANARY_PREFIXES = ("scripts/canaries/", "tests/canaries/")

# The floor's own machinery: the file that declares it, the two scripts that
# enforce it, and the workflow that runs them.
FLOOR_CORE_PATHS = (
    FLOOR_FILE,
    "scripts/floor_check.py",
    "scripts/floor_anchor.py",
    ".github/workflows/floor.yml",
)


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


def _is_valid_component_path(path: str) -> bool:
    """Is ``path`` one of the three shapes a shipped component can live at?

    Used two ways: to decide whether a rename destination is a relocation or a
    deletion wearing a rename, and to decide whether a marked file has a
    component directory the protected roles can span.
    """
    return bool(VALID_COMPONENT_RE.match(path))


def _component_dir(path: str) -> str | None:
    """The component ``path`` belongs to, or ``None`` if it is not a component.

    A ``SKILL.md`` component is its parent directory and everything beneath it
    (``skills/marathon/forge/`` belongs to the marathon component); a
    ``commands/<x>.md`` component is that single file. A path matching none of
    the component shapes -- a prose carrier under ``docs/``, say -- has no
    component directory, so quoting the marker in prose protects nothing.
    """
    if not _is_valid_component_path(path):
        return None
    if path.endswith("/SKILL.md"):
        return str(PurePosixPath(path).parent)
    return path


def _is_under(path: str, component: str) -> bool:
    """Is ``path`` the component itself, or anything beneath it?"""
    return path == component or path.startswith(f"{component}/")


def classify_path(path: str, component_dirs) -> str | None:
    """The protected role of ``path``, or ``None`` when it is unprotected.

    ``component_dirs`` is the set of marked-component directories in play (see
    ``_component_dir``). The roles do not overlap on this tree, so the order
    below is a reading order, not a precedence rule.
    """
    if path in FLOOR_CORE_PATHS:
        return ROLE_FLOOR_CORE
    if any(path.startswith(prefix) for prefix in GATE_CODE_PREFIXES):
        return ROLE_GATE_CODE
    if any(path.startswith(prefix) for prefix in CANARY_PREFIXES):
        return ROLE_CANARY
    if any(_is_under(path, component) for component in component_dirs):
        return ROLE_MARKED_COMPONENT
    return None


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


def _read_head(path: str, cwd: str | Path | None = None) -> str | None:
    """Content of ``path`` in the working tree, or ``None`` if absent."""
    p = Path(cwd) / path if cwd is not None else Path(path)
    if not p.exists():
        return None
    return p.read_text(encoding="utf-8")


def _get_renames(base_ref: str, cwd: str | Path | None = None) -> dict[str, str]:
    """``{old_path: new_path}`` for every file git maps as a rename.

    ``-M50%`` is the deliberate threshold: a relocation that also edits the file
    stays mapped (and is then judged on its tokens), while a rewrite past the
    threshold leaves rename detection and is judged as a deletion. Raising it to
    ``-M100%`` would make a whitespace change during a move read as a deletion;
    dropping rename detection entirely would make every move read as one.

    The diff is the working tree against ``base_ref``, which is what CI wants:
    the checkout there is the PR merge commit.
    """
    result = subprocess.run(
        ["git", "diff", "-M50%", "--name-status", "--diff-filter=R", base_ref],
        capture_output=True,
        text=True,
        cwd=cwd,
    )
    renames: dict[str, str] = {}
    for line in result.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) == 3 and parts[0].startswith("R"):
            renames[parts[1]] = parts[2]
    return renames


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


def _untracked_component_paths(cwd: str | Path | None = None) -> list[str]:
    """Component-shaped paths present in the working tree but not tracked.

    Deliberately *not* ``--exclude-standard``: the repo's ignore list is not the
    floor's business. A component that exists on disk carries its obligation
    whether or not ``.gitignore`` has caught up with the directory it lives in,
    and a component the ignore list hides is exactly the case where silence
    would be dangerous. The component-shape filter keeps this cheap -- nothing
    else in an untracked tree (build output, virtualenvs, caches) can be a
    component, so nothing else is even opened.
    """
    result = subprocess.run(
        ["git", "ls-files", "--others"],
        capture_output=True,
        text=True,
        cwd=cwd,
    )
    return [
        path
        for path in result.stdout.splitlines()
        if path and _is_valid_component_path(path)
    ]


def _discover_marked_files_head(
    marker: str,
    cwd: str | Path | None = None,
) -> list[str]:
    """The same discovery, run over the working tree instead of a ref.

    A component marked by the PR under review carries no anchor at the base ref,
    so base-side discovery alone would leave it unprotected on the very PR that
    marks it. Reading the head side too closes that window: the obligation binds
    from the commit that declares it, not from the one after.
    """
    result = subprocess.run(
        ["git", "grep", "-l", "-F", marker, "--", f":!{FLOOR_FILE}"],
        capture_output=True,
        text=True,
        cwd=cwd,
    )
    candidates = [path for path in result.stdout.splitlines() if path]
    candidates += _untracked_component_paths(cwd=cwd)
    return [
        path
        for path in dict.fromkeys(candidates)
        if standalone_anchor_count(_read_head(path, cwd=cwd), marker) > 0
    ]


def _protected_component_dirs(
    base_ref: str,
    marker: str = MARKER,
    cwd: str | Path | None = None,
) -> set[str]:
    """Component directories carrying a floor obligation at ``base_ref`` or head."""
    marked = set(_discover_marked_files(base_ref, marker, cwd=cwd))
    marked |= set(_discover_marked_files_head(marker, cwd=cwd))
    return {
        component
        for component in (_component_dir(path) for path in marked)
        if component is not None
    }


# ── Subcommands ──────────────────────────────────────────────────────────────

def _tokens_for_run(base: str) -> tuple[list[str], str | None]:
    """The token set this run enforces, plus a note when it was not read at
    ``base``.

    The enforced set is the base ref's declaration: a token added on the head
    side is not yet enforced by that run, so a PR that widens the floor never
    fails on its own widening. The one fallback is bootstrap - a base that
    predates the token block, which is every base until the block lands - where
    the working-tree declaration is used instead. Raises ``FloorTokenError``
    when neither side declares a usable block.
    """
    try:
        return _parse_token_block(_git_show(base, FLOOR_FILE)), None
    except FloorTokenError as at_base:
        tokens = _parse_token_block(_read_head(FLOOR_FILE))
        return tokens, str(at_base)


def cmd_markers(args: argparse.Namespace) -> int:
    base = args.base
    try:
        tokens, bootstrap = _tokens_for_run(base)
    except FloorTokenError as exc:
        print(f"FAIL {FLOOR_FILE}: {exc}")
        return 1
    if bootstrap:
        print(
            f"note {FLOOR_FILE} at {base}: {bootstrap}; enforcing the "
            f"working-tree declaration instead (bootstrap)."
        )
    files = list(args.files) if args.files else _discover_marked_files(base, MARKER)
    if not files:
        print(f"ok   no marked files at {base}: no floor obligation is armed yet.")
        return 0
    renames = _get_renames(base)
    failed = False
    for path in files:
        base_text = _git_show(base, path)
        destination = renames.get(path)
        if destination is not None and not _is_valid_component_path(destination):
            # A rename git was happy to map, into a path no component can live
            # at. That is a deletion wearing a rename, so say so and name the
            # destination that was rejected.
            failed = True
            print(
                f"FAIL {path}: marked file deleted -- renamed to "
                f"{destination}, which is not a component path "
                f"(skills/<x>/SKILL.md, plugins/<p>/skills/<x>/SKILL.md or "
                f"commands/<x>.md), so the floor obligation was dropped, not moved"
            )
            continue
        head_path = destination or path
        head_text = _read_head(head_path)
        if head_text is None and destination is None:
            # Gone from head with nothing mapping it anywhere: a plain deletion.
            failed = True
            carried = sum(1 for t in tokens if base_text and t in base_text)
            print(
                f"FAIL {path}: marked file deleted between {base} and head "
                f"({carried} floor obligation(s) lost, and no rename maps it to "
                f"a new path)"
            )
            continue
        removed = removed_tokens(base_text, head_text, tokens)
        if removed:
            failed = True
            for token in removed:
                print(
                    f"FAIL {head_path}: floor token weakened -> {token!r} "
                    f"(occurrences dropped, or its standalone anchor line was "
                    f"removed, between {base} and head)"
                )
        else:
            carried = [t for t in tokens if base_text and t in base_text]
            state = f"{len(carried)} token(s) intact" if carried else "no floor tokens (ok)"
            moved = f" (moved from {path})" if destination else ""
            print(f"ok   {head_path}: {state}{moved}")
    if failed:
        print(
            "\nFloor markers were removed. Restore them, or obtain the "
            "maintainer's out-of-band sign-off (FLOOR.md clause iii)."
        )
        return 1
    print("\nMarker check passed: no floor tokens removed.")
    return 0


def _changed_paths(args: argparse.Namespace) -> list[str]:
    """The paths to classify: stdin with ``--changed``, else the base diff.

    In diff mode the input is what the working tree changes relative to
    ``--base``, which is ``git diff --name-only`` plus the component-shaped
    paths that exist now and are not tracked -- the same reason
    ``_untracked_component_paths`` exists. In CI the checkout is a merge commit
    with nothing untracked, so that second half is empty there and the mode is
    exactly the diff.
    """
    if args.changed:
        paths = sys.stdin.read().splitlines()
    else:
        paths = subprocess.run(
            ["git", "diff", "--name-only", args.base],
            capture_output=True,
            text=True,
        ).stdout.splitlines()
        paths += _untracked_component_paths()
    return list(dict.fromkeys(path.strip() for path in paths if path.strip()))


def cmd_protected(args: argparse.Namespace) -> int:
    """Print the protected subset of the changed paths, one per line.

    Classification is not a verdict: a protected path is a path that needs the
    expensive semantic layer and the maintainer's sign-off, not a failure. So
    this exits 0 whenever it classified its input, and the caller decides what
    an empty or non-empty answer means.
    """
    component_dirs = _protected_component_dirs(args.base)
    for path in _changed_paths(args):
        role = classify_path(path, component_dirs)
        if role is None:
            continue
        if args.role and role != args.role:
            continue
        print(path)
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

    p_protected = sub.add_parser(
        "protected", help="classify changed paths by protected directory role"
    )
    p_protected.add_argument(
        "--base", required=True, help="git ref for the merge-base (e.g. origin/main)"
    )
    p_protected.add_argument(
        "--changed",
        action="store_true",
        help="read the changed paths from stdin instead of diffing against --base",
    )
    p_protected.add_argument(
        "--role",
        choices=ROLES,
        help="print only the paths in this role (default: every protected path)",
    )
    p_protected.set_defaults(func=cmd_protected)

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
