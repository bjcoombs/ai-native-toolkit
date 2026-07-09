#!/usr/bin/env python3
"""porcelain - a small, deterministic repo-list normalizer (canary reference impl).

Behaviour derived from this repo's real `ghsync --porcelain` contract
(`tests/test_ghsync_porcelain.py`): the machine-parseable half of a tool must put
*only* the payload on stdout (one item per line, no chatter, no prefix), send all
informational output to stderr, and exit 0 on success. Downstream tooling parses
stdout; humans read stderr.

This is the CLI-class known-good fixture: a genuinely-working tool with tier-1
exit-code + output criteria on real input. It has zero dependencies and runs
cold with just `python3`.

Input (stdin): one repo record per line, `name<TAB>archived`, where archived is
`true` or `false`. Blank lines and lines beginning with `#` are ignored.

Output (stdout): the names of non-archived repos, de-duplicated and sorted, one
per line. Output (stderr): a single summary line, e.g. `discovered 3 repos`.

Exit codes: 0 success; 2 usage error (unknown flag or malformed record).
"""
import sys


USAGE = "usage: porcelain.py [--help]   (reads repo records on stdin)"


def normalize(lines):
    """Return (sorted unique non-archived names, discovered_count).

    Raises ValueError on a malformed record so the caller can exit 2.
    """
    names = set()
    count = 0
    for raw in lines:
        line = raw.rstrip("\n")
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) != 2 or parts[1] not in ("true", "false"):
            raise ValueError(f"malformed record: {line!r}")
        name, archived = parts[0].strip(), parts[1]
        if not name:
            raise ValueError(f"malformed record: {line!r}")
        count += 1
        if archived == "false":
            names.add(name)
    return sorted(names), count


def main(argv, stdin, stdout, stderr):
    args = argv[1:]
    if args:
        if args == ["--help"]:
            stdout.write(USAGE + "\n")
            return 0
        stderr.write(f"error: unknown flag {args[0]!r}\n{USAGE}\n")
        return 2
    try:
        names, count = normalize(stdin)
    except ValueError as exc:
        stderr.write(f"error: {exc}\n")
        return 2
    for name in names:
        stdout.write(name + "\n")
    stderr.write(f"discovered {count} repos\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv, sys.stdin, sys.stdout, sys.stderr))
