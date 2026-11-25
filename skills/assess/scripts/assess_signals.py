"""Headless, signals-only entry point for /assess (issue #545).

Runs the deterministic scans from the registry in ``lib/scan_registry.py``
against a repository and writes one JSON document to ``--out``. Built for
callers scanning many repositories they may not own:

- never writes into the target repository (``--out`` inside it is refused),
- makes no network calls (network scans run offline or report unavailable),
  except under the opt-in ``--platform``, where the scans in
  ``lib/signals.py`` ``PLATFORM_SCANS`` read GitHub or GitLab through gh or glab,
- renders no SVG and asks no consent questions.

Blocks ``assess_core.py`` assembles through its own table entries are listed as
``{"available": false, "reason": ...}``. The output shape is published as
``skills/assess/references/signals.schema.json``.

Exit codes:
    0  document written
    2  usage error: unknown scan name, bad --window, --out inside the target,
       a target or --ghsync-root that is not a directory, a --scope that is missing or
       outside the target, --scope with --units or a --scans unit scan, an unreadable or invalid
       --fleet-config (the message names the key), or a --pack that does not load

Run:
    uv run assess_signals.py <repo> --out FILE.json [--scans a,b,c] [--window 6mo]
        [--counts-only [--hash-paths | --hash-salt VALUE]] [--fleet-config FILE] [--platform]
        [--ghsync-root DIR] [--scope PATH | --units] [--pack FILE ...]

``--ghsync-root DIR`` names the ghsync root holding the target: GitLab
``include: project:`` and ``component:`` files are then read from the clones
under it (``lib/ci/ci_includes.py``, #766). ``fleet.py`` passes its ROOT.

``--scope PATH`` (#866) measures one folder of the target, as ``assess_core --scope``
does: the scans that read the scope (suppressions, the doc graph and staleness, structure,
size, the coverage gate) read that folder, and the repository-wide ones (CI, review,
agent settings, the root instruction file) read the whole checkout. The stamp records
the folder as ``scope``; counts-only output writes ``set`` in its place, or its salted hash
under ``--hash-paths``, so ``null`` still means the whole checkout.

``--units`` (#866) adds the monorepo unit scans (``lib/signals.py`` ``UNIT_SCANS``): the
``units`` block and ``unit_readings``, each unit's own readings of the scoped scans with
every condition tagged ``unit`` or ``inherited``. ``fleet.py --monorepo-units`` passes it, and the
fleet computes each unit's Step from them. Counts-only output drops each unit's folder, or
hashes it under ``--hash-paths``.

``--pack FILE`` (#1276), repeatable, evaluates a practice pack's checks (``lib/packs.py``,
``lib/pack_checks.py``) whatever ``--scans`` selects, into the ``pack`` block,
``{<pack>: {<check>: value}}``: booleans, counts and unavailable markers, no path or file
text. ``fleet.py --pack`` passes every repository the same packs in the same order.
"""
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     # The layer 2-4 blocks (lib/headless_layers.py): the import graph and the
#     # file size and complexity scores. Without them each block reports
#     # unavailable with a named reason. lizard is pinned to the treemap's version.
#     "networkx",
#     "grimp",
#     "lizard==1.23.0",
# ]
# ///
from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
import tempfile
from pathlib import Path

# No __pycache__ from the imports below: scanning the toolkit checkout itself
# must not write into it either.
sys.dont_write_bytecode = True

# Make sibling lib package importable when run as a script
sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib.counts_only.content_free import (
    add_cli_arguments,
    counts_only_scans,
    hash_options,
    hash_value,
    validate_cli_args,
)
from lib.fleet_config import FleetConfig, FleetConfigError, load_fleet_config
from lib.packs import PackError, load_packs
from lib.run_context_types import SignalsDocument
from lib.run_scope import resolve_scope
from lib.signals import (
    DEFAULT_WINDOW,
    UnknownScanError,
    build_document,
    known_scans,
    parse_window,
    platform_only_error,
    scope_units_error,
    select_scans,
)
from lib.tool_lookup import warn_missing_packages


# A scoped counts-only run's stamp ``scope`` without --hash-paths: the folder is hidden,
# and the run still reads as scoped (null is the whole checkout).
SCOPE_REDACTED = "set"


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the offline /assess scans headless and write one JSON document.",
    )
    parser.add_argument("repo_root", help="Path to the repository to scan (read only).")
    parser.add_argument("--out", required=True, help="Output JSON path; must be outside the repository.")
    parser.add_argument(
        "--scans", default=None,
        help="Comma-separated scan names (default: all). Known: " + ", ".join(known_scans()),
    )
    parser.add_argument(
        "--window", default=DEFAULT_WINDOW,
        help="History window stamped on the run and read by history scans (ai_usage), e.g. 6mo, 90d, 12w, 1y (default: %(default)s).",
    )
    parser.add_argument(
        "--fleet-config", metavar="FILE",
        help="Fleet settings (TOML, lib/fleet_config.py): the [ai_usage], [review] and [platform] "
             "sections, extended by the target's .assess/config.toml sections.",
    )
    parser.add_argument(
        "--platform", action="store_true",
        help="Make the platform reads (gh or glab) of the scans in lib/signals.py PLATFORM_SCANS "
             "instead of reporting them unavailable. Off by default: no network call.",
    )
    parser.add_argument(
        "--managed-settings", action="store_true",
        help="Read this machine's Claude Code managed settings for the agent permissions "
             "practice (#1153), and stamp the run with it. Off by default: it measures the "
             "machine, not the repository. The target's .assess/config.toml never turns it on.",
    )
    parser.add_argument(
        "--ghsync-root", metavar="DIR",
        help="The ghsync root (<root>/<path>/<repo>-main) holding the target: GitLab include: "
             "project: and component: files are read from the clones under it (lib/ci/ci_includes.py). "
             "Without it only local: includes resolve.",
    )
    parser.add_argument(
        "--pack", action="append", default=[], metavar="FILE",
        help="A practice pack (<name>.pack.toml, lib/packs.py) whose checks are evaluated offline "
             "into the pack block, under pack.<name>.<check>; repeat for several.",
    )
    unit = parser.add_mutually_exclusive_group()
    unit.add_argument(
        "--scope", metavar="PATH",
        help="Measure one folder of the target (relative to it, or absolute under it), as "
             "assess_core --scope does; repository-wide scans still read the whole checkout.",
    )
    unit.add_argument(
        "--units", action="store_true",
        help="Add the monorepo unit scans (units, unit_readings): each unit's own readings of "
             "the scoped scans. One scoped pass per unit.",
    )
    add_cli_arguments(parser)
    parsed = parser.parse_args(argv)
    validate_cli_args(parser, parsed)
    return parsed


def _inside(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    repo_root = Path(args.repo_root).resolve()
    out = Path(args.out).resolve()
    if not repo_root.is_dir():
        print(f"error: {args.repo_root} is not a directory", file=sys.stderr)
        return 2
    if _inside(out, repo_root):
        print(f"error: --out {args.out} is inside the target repository; "
              "the signals run never writes into the target", file=sys.stderr)
        return 2
    fleet_config: FleetConfig | None = None
    if args.fleet_config is not None:
        try:
            fleet_config = load_fleet_config(Path(args.fleet_config))
        except FleetConfigError as e:
            print(f"error: --fleet-config {args.fleet_config}: {e}", file=sys.stderr)
            return 2
    try:
        packs = load_packs(args.pack)
    except PackError as e:
        print(f"error: --pack: {e}", file=sys.stderr)
        return 2
    ghsync_root: Path | None = None
    if args.ghsync_root is not None:
        ghsync_root = Path(args.ghsync_root).resolve()
        if not ghsync_root.is_dir():
            print(f"error: --ghsync-root {args.ghsync_root} is not a directory", file=sys.stderr)
            return 2
    try:
        scope = _scope(repo_root, args.scope)
    except ValueError as e:
        print(f"error: --scope {args.scope}: {e}", file=sys.stderr)
        return 2
    requested = None
    if args.scans is not None:
        requested = [name.strip() for name in args.scans.split(",") if name.strip()]
    try:
        select_scans(requested)
        parse_window(args.window)
        problem = platform_only_error(requested, args.platform) or scope_units_error(requested, scope)
        if problem is not None:
            raise ValueError(problem)
    except UnknownScanError as e:
        print(f"error: {e}. Known scans: {', '.join(known_scans())}", file=sys.stderr)
        return 2
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    warn_missing_packages()  # a no-uv run: say how to install what the layer blocks need
    document = build_document(repo_root, requested, args.window, fleet_config=fleet_config,
                              platform=args.platform, ghsync_root=ghsync_root,
                              scope=scope, units=args.units,
                              managed_settings=args.managed_settings, packs=packs)
    if args.counts_only:
        _counts_only(document, args)
    _write_atomically(out, json.dumps(document, indent=2, default=str) + "\n")
    return 0


def _scope(repo_root: Path, raw: str | None) -> Path | None:
    """``--scope`` as an absolute folder under the target; None for none or the target itself,
    so the stamp's null is the only way to say "whole checkout". Raises ``ValueError``."""
    if raw is None:
        return None
    scope = resolve_scope(repo_root, Path(raw))[0]
    return None if scope == repo_root else scope


def _counts_only(document: SignalsDocument, args: argparse.Namespace) -> None:
    """Content-free mode, in place: free text, identities and the target's commit are
    dropped; references/counts-only.md lists what each block keeps."""
    hash_paths, salt = hash_options(args)
    if hash_paths and salt is None:
        salt = secrets.token_hex(16)  # one salt for the scans and the stamp's scope
    document["scans"] = counts_only_scans(document["scans"], hash_paths=hash_paths, salt=salt)
    document["stamp"]["target_head"] = None
    stamped = document["stamp"]["scope"]
    # the scope folder, like a unit's: never in clear, hashed under --hash-paths, else
    # SCOPE_REDACTED, so null keeps meaning the whole checkout
    if stamped is not None:
        document["stamp"]["scope"] = hash_value(stamped, salt) if salt else SCOPE_REDACTED


def _write_atomically(out: Path, text: str) -> None:
    """Write through a temp file and ``os.replace``: a hard link at ``out`` into
    the target is unlinked from the name, never truncated, and a failed write
    leaves no partial document."""
    out.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=out.parent, prefix=f".{out.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.chmod(tmp, 0o644)
        os.replace(tmp, out)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


if __name__ == "__main__":
    sys.exit(main())
