#!/usr/bin/env python3
"""Check and sort the comparers' findings for the doc-sync workflow (issue #1453).

Step 5 of `skills/doc-contradictions/SKILL.md` as code, run after the model
step with no model in the loop. It reads the raw comparer findings the
orchestrator returned and the cluster scope `doc_clusters.py` wrote, and
writes the posting plan the `post` job applies:

    doc_sync_findings.py --repo PR_HEAD --clusters clusters.json \
        --raw raw.json --out findings.json
    doc_sync_findings.py --clusters clusters.json --skipped REASON --out findings.json

- **Citation check.** Each side's quote must be on its cited line, or on
  exactly one line within two of it (the line then moves there); an empty or
  multi-line quote, a quote shorter than `MIN_QUOTE` characters once trimmed
  (one such as `e` is on almost every line, so it proves nothing), a missing
  file or any other result drops the finding.
- **Excluded paths.** A finding that cites an excluded path is dropped.
- **Dedupe.** Two findings citing the same pair of locations are one.
- **Sort.** A high-confidence finding with at least one side in a file the
  pull request changed becomes a review thread (at most `MAX_THREADS`); every
  other finding goes to the one summary comment, which does not block.

Every free-text field is cut to a fixed length, and each finding carries a
stable id taken from its two paths and quotes, so a later push does not post
the same thread twice. Stdlib only, Python 3.8 compatible.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from doc_clusters import excluded, glob_regex
from doc_sync_types import Finding, Plan, Side

MAX_THREADS = 10
MAX_TEXT = 400
MAX_QUOTE = 200
MIN_QUOTE = 8
CONFIDENCE_ORDER = {"high": 0, "medium": 1, "low": 2}


def _text(value: object, limit: int) -> str:
    text = " ".join(str(value if value is not None else "").split()).replace("@", "@\u200b")
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _cut(text: str, limit: int) -> str:
    """A quote kept verbatim apart from its length; it is posted inside backticks."""
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _path(value: object) -> str:
    path = str(value or "").strip().replace("\\", "/")
    for prefix in ("./", "pr-head/"):
        if path.startswith(prefix):
            path = path[len(prefix):]
    return path


def check_side(repo: Path, side: Any) -> Tuple[Optional[Side], str]:
    """The side with its line confirmed by the file, or None and the reason."""
    if not isinstance(side, dict):
        return None, "side is not an object"
    path = _path(side.get("path"))
    quote = side.get("quote")
    if not isinstance(quote, str) or not quote.strip() or "\n" in quote:
        return None, "%s: empty or multi-line quote" % path
    if len(quote.strip()) < MIN_QUOTE:
        return None, "%s: quote shorter than %d characters" % (path, MIN_QUOTE)
    try:
        line = int(side["line"])
    except (KeyError, TypeError, ValueError):
        return None, "%s: no line number" % path
    target = (repo / path).resolve()
    if not path or path.startswith("../") or repo.resolve() not in target.parents or not target.is_file():
        return None, "%s: file not found" % path
    lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
    needle = quote.strip()
    hits = [i + 1 for i, text in enumerate(lines) if needle in text]
    if line in hits:
        return Side(path=path, line=line, quote=_cut(needle, MAX_QUOTE)), ""
    near = [h for h in hits if abs(h - line) <= 2]
    if len(near) == 1:
        return Side(path=path, line=near[0], quote=_cut(needle, MAX_QUOTE)), ""
    return None, "%s:%d quote not found at that line (%d hits)" % (path, line, len(hits))


def finding_id(a: Side, b: Side) -> str:
    pair = sorted([(a["path"], a["quote"]), (b["path"], b["quote"])])
    return hashlib.sha256(json.dumps(pair).encode("utf-8")).hexdigest()[:16]


def sort_findings(repo: Path, scope: Dict[str, Any], raw: Any) -> Plan:
    patterns = [glob_regex(str(g)) for g in scope.get("excludes") or []]
    changed = {str(p) for p in scope.get("changed") or []}
    items = raw.get("findings", []) if isinstance(raw, dict) else raw
    kept: Dict[Tuple[Tuple[str, int], ...], Finding] = {}
    dropped: List[str] = []
    excluded_count = 0
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            dropped.append("a finding that is not an object")
            continue
        subject = _text(item.get("subject"), 120)
        a, why_a = check_side(repo, item.get("a"))
        b, why_b = check_side(repo, item.get("b"))
        if a is None or b is None:
            dropped.append(_text("%s: %s" % (subject, why_a or why_b), MAX_TEXT))
            continue
        if excluded(a["path"], patterns) or excluded(b["path"], patterns):
            excluded_count += 1
            continue
        confidence = str(item.get("confidence", "low")).lower()
        finding = Finding(
            id=finding_id(a, b),
            kind="doc-vs-code" if item.get("kind") == "doc-vs-code" else "doc-vs-doc",
            subject=subject, a=a, b=b,
            why=_text(item.get("explanation"), MAX_TEXT),
            confidence=confidence if confidence in CONFIDENCE_ORDER else "low",
            cluster=_text(item.get("cluster"), 20),
        )
        key = tuple(sorted([(a["path"], a["line"]), (b["path"], b["line"])]))
        prior = kept.get(key)
        if prior is None or CONFIDENCE_ORDER[finding["confidence"]] < CONFIDENCE_ORDER[prior["confidence"]]:
            kept[key] = finding
    ordered = sorted(kept.values(), key=lambda f: (CONFIDENCE_ORDER[f["confidence"]],
                                                   f["kind"] != "doc-vs-code", f["id"]))
    threads: List[Finding] = []
    summary: List[Finding] = []
    for f in ordered:
        touches = f["a"]["path"] in changed or f["b"]["path"] in changed
        if f["confidence"] == "high" and touches and len(threads) < MAX_THREADS:
            threads.append(f)
        else:
            summary.append(f)
    return Plan(status="checked", clusters=len(scope.get("clusters") or []),
                cross_cluster=bool(scope.get("cross_cluster")), threads=threads,
                summary=summary, dropped=dropped, dropped_excluded=excluded_count)


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo", default=".", help="the pull request's checkout")
    ap.add_argument("--clusters", required=True, help="doc_clusters.py output")
    ap.add_argument("--raw", help="the orchestrator's structured output")
    ap.add_argument("--skipped", metavar="REASON", help="write a plan with no findings and this reason")
    ap.add_argument("--quiet", action="store_true",
                    help="with --skipped: a skip that needs no new summary comment")
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    scope = json.loads(Path(args.clusters).read_text(encoding="utf-8"))
    if args.skipped:
        plan = Plan(status="skipped", reason=_text(args.skipped, MAX_TEXT), quiet=args.quiet,
                    clusters=0, threads=[], summary=[], dropped=[])
    else:
        raw_path = Path(args.raw) if args.raw else None
        raw = json.loads(raw_path.read_text(encoding="utf-8")) if raw_path and raw_path.is_file() else None
        if raw is None:
            plan = Plan(status="error", reason="the model step returned no findings document",
                        clusters=len(scope.get("clusters") or []), threads=[], summary=[], dropped=[])
        else:
            plan = sort_findings(Path(args.repo), scope, raw)
    Path(args.out).write_text(json.dumps(plan, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
