"""Write the measures catalog: ``docs/what-we-measure.md`` and ``references/measures.json`` (#794).

Both are generated from the check manifest (``checks/core.toml``), the default maturity
model (``levels/default.toml``) and the signal input classes (``lib/level_signals.py``) by
``lib.catalog`` and ``lib.catalog_md``. ``tests/test_catalog.py`` fails when a committed
copy differs from what this writes, so regenerate after changing any of them.

Run (from the repository root): ``make catalog``, or
``cd skills/assess && uv run --locked python scripts/gen_catalog.py``. ``--check`` writes
nothing and exits 1 naming each stale file.
"""
# /// script
# requires-python = ">=3.11"
# ///
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib.catalog import build_catalog, render_json  # noqa: E402
from lib.catalog_md import render_markdown  # noqa: E402

# skills/assess/scripts/gen_catalog.py -> the toolkit checkout root.
ROOT = Path(__file__).resolve().parents[3]
MARKDOWN = ROOT / "docs" / "what-we-measure.md"
JSON = ROOT / "skills" / "assess" / "references" / "measures.json"


def outputs() -> dict[Path, str]:
    """Each catalog file and the content it should hold."""
    catalog = build_catalog()
    return {MARKDOWN: render_markdown(catalog), JSON: render_json(catalog)}


def stale(expected: dict[Path, str]) -> list[Path]:
    """The files whose committed text differs from ``expected`` (or that are missing)."""
    return [path for path, text in expected.items()
            if not path.is_file() or path.read_text(encoding="utf-8") != text]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true",
                        help="Write nothing; exit 1 naming each stale catalog file.")
    args = parser.parse_args(argv)
    expected = outputs()
    if args.check:
        old = stale(expected)
        for path in old:
            print(f"stale: {path.relative_to(ROOT).as_posix()} (run make catalog)", file=sys.stderr)
        return 1 if old else 0
    for path, text in expected.items():
        path.write_text(text, encoding="utf-8", newline="\n")
        print(f"wrote {path.relative_to(ROOT).as_posix()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
