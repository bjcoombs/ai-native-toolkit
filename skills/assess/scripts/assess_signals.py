"""Headless, signals-only entry point for /assess (issue #545).

Runs the deterministic scans from the registry in ``lib/scan_registry.py``
against a repository and writes one JSON document to ``--out``. Built for
callers scanning many repositories they may not own:

- never writes into the target repository (``--out`` inside it is refused),
- makes no network calls (network scans run offline or report unavailable),
- renders no SVG and asks no consent questions.

Scans still hand-wired in ``assess_core.py`` are listed as
``{"available": false, "reason": ...}``. The output shape is published as
``skills/assess/references/signals.schema.json``.

Exit codes:
    0  document written
    2  usage error: unknown scan name, bad --window, --out inside the target,
       or a target that is not a directory

Run:
    uv run assess_signals.py <repo> --out FILE.json [--scans a,b,c] [--window 6mo]
"""
# /// script
# requires-python = ">=3.11"
# ///
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Make sibling lib package importable when run as a script
sys.path.insert(0, str(Path(__file__).resolve().parent))

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
        help="History window recorded in the run stamp, e.g. 6mo, 90d, 12w, 1y (default: %(default)s).",
    )
    return parser.parse_args(argv)


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
    document = build_document(repo_root, requested, args.window)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(document, indent=2, default=str) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
