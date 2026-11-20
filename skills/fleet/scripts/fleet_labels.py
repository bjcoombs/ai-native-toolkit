"""Fleet repository labels: the deterministic half of the optional label step (issue #572).

Labelling runs after ``fleet.py`` and never changes its output. The model step
(a Haiku subagent the skill runs) sits between two commands here:

    prompt  OUT [--batch-size N] [--batch K]
            Print the model instructions plus one compact digest per repository
            of OUT/repos.jsonl (batch K of the rows sorted by id; the batch count
            goes to stderr). Reads the rows only, never source.
    write   OUT --model ID [--answers FILE]
            Read the model's reply (FILE, or stdin), join it to OUT/repos.jsonl
            and write OUT/labels.jsonl: one row per repository, every label in
            the closed set (anything else becomes "other"; a missing answer is
            "other" with confidence 0). A summary goes to stderr.
    check   OUT
            Validate OUT/labels.jsonl against OUT/repos.jsonl.

repos.jsonl is only ever read. The row shape is
``skills/fleet/references/labels.schema.json``; the logic lives in
``skills/assess/scripts/lib/fleet_labels.py``.

Exit codes:
    0  done (check: labels.jsonl is valid)
    1  check: labels.jsonl has problems (printed to stderr)
    2  usage error: OUT has no readable repos.jsonl, a batch out of range,
       an unreadable answers file or an empty --model

Run:
    uv run fleet_labels.py prompt fleet-out --batch 0
    uv run fleet_labels.py write fleet-out --model claude-haiku-5-5 --answers answers.txt
    uv run fleet_labels.py check fleet-out
"""
# /// script
# requires-python = ">=3.11"
# ///
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.dont_write_bytecode = True

# skills/fleet/scripts -> skills/assess/scripts (the plugin ships both skills)
ASSESS_SCRIPTS = Path(__file__).resolve().parents[2] / "assess" / "scripts"
sys.path.insert(0, str(ASSESS_SCRIPTS))

from lib.fleet import REPOS_FILE  # noqa: E402
from lib.fleet_labels import (  # noqa: E402
    DEFAULT_BATCH_SIZE,
    LABELS_FILE,
    LabelsError,
    batches,
    build_label_rows,
    build_prompt,
    check_labels,
    parse_answers,
    read_repos,
    write_labels,
)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Optional repository labels for a fleet run: model prompt, validated write, check.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    prompt = sub.add_parser("prompt", help="Print the model prompt for one batch of OUT/repos.jsonl.")
    prompt.add_argument("out", help="The fleet output directory holding repos.jsonl.")
    prompt.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE,
                        help="Repositories per prompt (default: %(default)s).")
    prompt.add_argument("--batch", type=int, default=0, help="Which batch, from 0 (default: 0).")
    write = sub.add_parser("write", help="Join the model's reply to repos.jsonl and write labels.jsonl.")
    write.add_argument("out", help="The fleet output directory holding repos.jsonl.")
    write.add_argument("--model", required=True, help="The model id that produced the answers.")
    write.add_argument("--answers", metavar="FILE",
                       help="The model's reply (JSON lines; fences and prose are skipped). Default: stdin.")
    check = sub.add_parser("check", help="Validate OUT/labels.jsonl against OUT/repos.jsonl.")
    check.add_argument("out", help="The fleet output directory.")
    return parser.parse_args(argv)


def _prompt(args: argparse.Namespace, out: Path) -> int:
    if args.batch_size < 1:
        raise LabelsError("--batch-size must be at least 1")
    parts = batches(read_repos(out / REPOS_FILE), args.batch_size)
    if not 0 <= args.batch < len(parts):
        raise LabelsError(f"--batch {args.batch} is out of range: {len(parts)} batch(es)")
    print(f"fleet_labels: batch {args.batch} of {len(parts)} ({len(parts[args.batch])} repositories)",
          file=sys.stderr)
    sys.stdout.write(build_prompt(parts[args.batch]))
    return 0


def _write(args: argparse.Namespace, out: Path) -> int:
    if not args.model.strip():
        raise LabelsError("--model needs a non-empty model id")
    repos = read_repos(out / REPOS_FILE)
    if args.answers is None:
        text = sys.stdin.read()
    else:
        try:
            text = Path(args.answers).read_text(encoding="utf-8")
        except OSError as e:
            raise LabelsError(f"cannot read --answers {args.answers}: {e.strerror}") from e
    rows, report = build_label_rows(repos, parse_answers(text), args.model.strip())
    path = write_labels(out, rows)
    print(f"fleet_labels: {len(rows)} repositories, {report['labelled']} answered -> {path}",
          file=sys.stderr)
    notes = (("missing", report["missing"]), ("coerced", report["coerced"]),
             ("unknown", report["unknown"]), ("duplicates", report["duplicates"]))
    for key, paths in notes:
        if paths:
            print(f"  {key} ({len(paths)}): {', '.join(paths)}", file=sys.stderr)
    return 0


def _check(out: Path) -> int:
    repos = read_repos(out / REPOS_FILE)
    try:
        text = (out / LABELS_FILE).read_text(encoding="utf-8")
    except OSError as e:
        raise LabelsError(f"cannot read {out / LABELS_FILE}: {e.strerror}") from e
    problems = check_labels(text, (row["repo"] for row in repos))
    for problem in problems:
        print(f"  {problem}", file=sys.stderr)
    print(f"fleet_labels: {LABELS_FILE} {'invalid' if problems else 'valid'}", file=sys.stderr)
    return 1 if problems else 0


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    out = Path(args.out)
    try:
        if args.command == "prompt":
            return _prompt(args, out)
        if args.command == "write":
            return _write(args, out)
        return _check(out)
    except LabelsError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
