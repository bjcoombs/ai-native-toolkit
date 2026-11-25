"""Write a starter maturity model you can edit and run with ``--levels`` (#1151).

    uv run scripts/levels_new.py my-team.toml                      # copy the default model
    uv run scripts/levels_new.py my-team.toml --profile handover   # copy a profile

The copy opens with a comment on each field of the format, and its ``version`` is
``<file stem>-1`` unless ``--version`` names another. It refuses to replace an existing file
without ``--force``, and a copy that does not load leaves an existing file as it was. On success it prints one line naming the file and the command that runs
it; on any error it prints the reason to stderr and exits 2. The logic is
``lib/scoring/levels_starter.py``; the guide is ``docs/build-your-own-practice.md`` at the repository
root.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Make the sibling lib package importable when run as a script
sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib.scoring.levels_starter import StarterError, write_starter
from lib.profiles import profile_names


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="levels_new.py", description=__doc__.split("\n")[0])
    parser.add_argument("out", type=Path, help="The file to write, such as my-team.toml.")
    parser.add_argument("--profile", metavar="NAME",
                        help=f"Copy a shipped profile ({', '.join(profile_names())}) instead of the default model.")
    parser.add_argument("--version", help="The model's version (default: the file stem plus -1).")
    parser.add_argument("--force", action="store_true", help="Replace the file if it exists.")
    args = parser.parse_args(argv)
    try:
        out = write_starter(args.out, args.profile, args.version, args.force)
    except StarterError as exc:
        print(f"levels_new.py: {exc}", file=sys.stderr)
        return 2
    print(f"Wrote {out}. Run it with: /assess --levels {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
