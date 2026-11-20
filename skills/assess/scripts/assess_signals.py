"""Headless, signals-only entry point for /assess (issue #545).

Runs the deterministic scans from the registry in ``lib/scan_registry.py``
against a repository and writes one JSON document to ``--out``. Built for
callers scanning many repositories they may not own:

- never writes into the target repository (``--out`` inside it is refused),
- makes no network calls (network scans run offline or report unavailable),
- renders no SVG and asks no consent questions.

Blocks ``assess_core.py`` assembles through its own table entries are listed as
``{"available": false, "reason": ...}``. The output shape is published as
``skills/assess/references/signals.schema.json``.

Exit codes:
    0  document written
    2  usage error: unknown scan name, bad --window, --out inside the target,
       a target that is not a directory, or an unreadable or invalid
       --fleet-config (the message names the key)

Run:
    uv run assess_signals.py <repo> --out FILE.json [--scans a,b,c] [--window 6mo]
        [--counts-only [--hash-paths | --hash-salt VALUE]] [--fleet-config FILE]
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
import sys
import tempfile
from pathlib import Path

# No __pycache__ from the imports below: scanning the toolkit checkout itself
# must not write into it either.
sys.dont_write_bytecode = True

# Make sibling lib package importable when run as a script
sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib.counts_only import (  # noqa: E402
    add_cli_arguments,
    counts_only_scans,
    hash_options,
    validate_cli_args,
)
from lib.fleet_config import FleetConfig, FleetConfigError, load_fleet_config  # noqa: E402
from lib.signals import (  # noqa: E402
    DEFAULT_WINDOW,
    UnknownScanError,
    build_document,
    known_scans,
    parse_window,
    select_scans,
)


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
    requested = None
    if args.scans is not None:
        requested = [name.strip() for name in args.scans.split(",") if name.strip()]
    try:
        select_scans(requested)
        parse_window(args.window)
    except UnknownScanError as e:
        print(f"error: {e}. Known scans: {', '.join(known_scans())}", file=sys.stderr)
        return 2
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    document = build_document(repo_root, requested, args.window, fleet_config=fleet_config)
    if args.counts_only:
        # Content-free mode: free text, identities and the target's commit
        # are dropped; references/counts-only.md lists what each block keeps.
        hash_paths, salt = hash_options(args)
        document["scans"] = counts_only_scans(document["scans"], hash_paths=hash_paths, salt=salt)
        document["stamp"]["target_head"] = None
    _write_atomically(out, json.dumps(document, indent=2, default=str) + "\n")
    return 0


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
