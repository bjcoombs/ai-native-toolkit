#!/usr/bin/env python3
"""Build the /doc-contradictions clusters deterministically (issue #1453).

Steps 1 and 2 of `skills/doc-contradictions/SKILL.md` as code: enumerate the
documents, collect each one's links and backticked paths, group the docs
into clusters by the doc graph and shared subject code, cap the cluster size
and rank the hubs. With `--changed-from <ref>` it keeps only the clusters
that hold a changed document or a changed subject-code file, and keeps the
cross-cluster pass only when a changed file is a hub. That is the scope the
per-PR `doc-sync` workflow runs the comparers over.

    doc_clusters.py [--repo PATH] [--no-default-excludes] [--exclude GLOB]...
                    [--changed-from REF] [--out FILE]

Writes one JSON object (to `--out`, or stdout):

- `documents`, `excludes`: what Step 1 scanned and the globs it left out;
  `excluded`: how many documents each glob removed, for the report header.
- `clusters`: `[{id, docs, subject_code}]`, repo-relative paths.
- `hubs`: the most-linked docs; `cross_cluster`: whether the hub pass runs.
- `changed_from`, `changed`, `clusters_total`: the `--changed-from` scope,
  `null`, `[]` and the full cluster count without it.

A changed path that no longer exists still selects the clusters whose docs
point at it: a deleted file a doc still names is the drift the check is for.

No model, no network, stdlib only, Python 3.8 compatible.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

DOC_SUFFIXES = (".md", ".mdx", ".rst", ".adoc")
ALWAYS_EXCLUDED = (".git/**", ".doc-contradictions/**", "**/node_modules/**", "**/vendor/**",
                   "**/tests/fixtures/**", "CHANGELOG*")
HISTORICAL = ("docs/design/**", "docs/superpowers/**")
MAX_DOCS = 8
MAX_LINES = 2000
MAX_HUBS = 5
LINK_RE = re.compile(r"\]\(\s*<?([^)#\s>]+)")
TICK_RE = re.compile(r"`([^`\s]+/[^`\s]+\.[A-Za-z0-9]+)(?::\d+(?:-\d+)?)?`")


def glob_regex(pattern: str) -> "re.Pattern[str]":
    """Translate a ripgrep-style glob to a regex over a repo-relative path.

    A pattern with no `/` matches the basename anywhere, as in rg and
    gitignore; `**` spans directories and `*` stays inside one.
    """
    if "/" not in pattern.rstrip("/"):
        pattern = "**/" + pattern
    out = []
    i = 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.compile("".join(out) + r"\Z")


def excluded(path: str, patterns: Sequence["re.Pattern[str]"]) -> bool:
    return any(p.match(path) for p in patterns)


def _git(repo: Path, args: Sequence[str]) -> Optional[str]:
    try:
        done = subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                              encoding="utf-8", errors="surrogateescape", check=False)
    except OSError:
        return None
    return done.stdout if done.returncode == 0 else None


def list_files(repo: Path) -> List[str]:
    """Every file the repository holds: git's view when it is a work tree."""
    out = _git(repo, ["ls-files", "-z", "--cached", "--others", "--exclude-standard"])
    if out is not None:
        return sorted({p for p in out.split("\0") if p and (repo / p).is_file()})
    found: List[str] = []
    for root, dirs, files in os.walk(repo):
        dirs[:] = [d for d in dirs if d != ".git"]
        for name in files:
            found.append((Path(root) / name).relative_to(repo).as_posix())
    return sorted(found)


def is_doc(path: str) -> bool:
    return path.lower().endswith(DOC_SUFFIXES)


def _normalise(repo: Path, base: str, target: str) -> Optional[str]:
    """`target` as a repo-relative path, or None when it leaves the repo."""
    if "://" in target or target.startswith(("mailto:", "#")):
        return None
    joined = os.path.normpath(target.lstrip("/") if target.startswith("/") else os.path.join(base, target))
    rel = Path(joined).as_posix()
    if rel.startswith("../") or rel == ".." or os.path.isabs(joined):
        return None
    return rel


def references(repo: Path, doc: str, text: str) -> Set[str]:
    """Repo paths a doc points at, existing or not (Step 2, doc edges)."""
    base = os.path.dirname(doc)
    refs: Set[str] = set()
    for target in LINK_RE.findall(text):
        rel = _normalise(repo, base, target)
        if rel:
            refs.add(rel)
    for target in TICK_RE.findall(text):
        for candidate in (_normalise(repo, "", target), _normalise(repo, base, target)):
            if candidate and (repo / candidate).is_file():
                refs.add(candidate)
                break
        else:
            # Not on disk (perhaps deleted): keep both readings, so a deleted
            # file a doc names relative to itself still selects that doc.
            for candidate in (_normalise(repo, "", target), _normalise(repo, base, target)):
                if candidate:
                    refs.add(candidate)
    refs.discard(doc)
    return refs


def _subject_code(doc: str, refs: Set[str], files: List[str], file_set: Set[str],
                  patterns: Sequence["re.Pattern[str]"]) -> Set[str]:
    """Step 2.2: the non-doc files a doc points at, plus a module README's directory."""
    own = {r for r in refs if r in file_set and not is_doc(r)}
    if Path(doc).stem.lower() == "readme":
        here = os.path.dirname(doc)
        own |= {f for f in files if os.path.dirname(f) == here and not is_doc(f)
                and not excluded(f, patterns)}
    return own


class _Union:
    def __init__(self, items: Iterable[str]) -> None:
        self.parent = {i: i for i in items}

    def find(self, x: str) -> str:
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def join(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[max(ra, rb)] = min(ra, rb)


def _split(docs: List[str], lines: Dict[str, int]) -> List[List[str]]:
    """Step 2.5: split by subdirectory, then into runs under both caps."""
    if len(docs) <= MAX_DOCS and sum(lines[d] for d in docs) <= MAX_LINES:
        return [docs]
    groups: Dict[str, List[str]] = {}
    for d in sorted(docs):
        groups.setdefault(os.path.dirname(d), []).append(d)
    parts: List[List[str]] = []
    for _, members in sorted(groups.items()):
        run: List[str] = []
        for d in members:
            if run and (len(run) >= MAX_DOCS or sum(lines[x] for x in run) + lines[d] > MAX_LINES):
                parts.append(run)
                run = []
            run.append(d)
        if run:
            parts.append(run)
    return parts


def _components(docs: List[str], doc_links: Dict[str, Set[str]], code: Dict[str, Set[str]],
                union: _Union) -> Dict[str, List[str]]:
    """Step 2.3 and 2.4: connected docs together, edgeless docs by top-level directory."""
    linked = {t for d in docs for t in doc_links[d]}
    components: Dict[str, List[str]] = {}
    for d in docs:
        if doc_links[d] or d in linked or code[d]:
            components.setdefault(union.find(d), []).append(d)
        else:
            top = d.split("/", 1)[0] if "/" in d else "."
            components.setdefault("leftover:" + top, []).append(d)
    return components


def build(repo: Path, excludes: Sequence[str], changed: Optional[Set[str]] = None) -> Dict[str, object]:
    patterns = [glob_regex(g) for g in excludes]
    files = list_files(repo)
    docs = [f for f in files if is_doc(f) and not excluded(f, patterns)]
    file_set = set(files)
    doc_set = set(docs)
    refs: Dict[str, Set[str]] = {}
    lines: Dict[str, int] = {}
    for d in docs:
        text = (repo / d).read_text(encoding="utf-8", errors="replace")
        lines[d] = text.count("\n") + 1
        refs[d] = {r for r in references(repo, d, text) if not excluded(r, patterns)}
    doc_links = {d: {r for r in refs[d] if r in doc_set} for d in docs}
    code = {d: _subject_code(d, refs[d], files, file_set, patterns) for d in docs}
    union = _Union(docs)
    owner: Dict[str, str] = {}
    for d in docs:
        for t in doc_links[d]:
            union.join(d, t)
        for c in code[d]:
            if c in owner:
                union.join(d, owner[c])
            else:
                owner[c] = d
    components = _components(docs, doc_links, code, union)
    parts: List[Tuple[str, List[str], List[str]]] = []
    for members in sorted(components.values(), key=lambda m: sorted(m)[0]):
        for part in _split(sorted(members), lines):
            subject = sorted(set().union(*(code[d] for d in part)))
            parts.append(("c%02d" % (len(parts) + 1), part, subject))
    inbound: Dict[str, int] = {d: 0 for d in docs}
    for d in docs:
        for t in doc_links[d]:
            inbound[t] += 1
    ranked = sorted((d for d in docs if inbound[d] >= 2), key=lambda d: (-inbound[d], d))
    hubs = ranked[:MAX_HUBS]
    result: Dict[str, object] = {
        "documents": len(docs),
        "excludes": list(excludes),
        "excluded": {g: sum(1 for f in files if is_doc(f) and p.match(f)) for g, p in zip(excludes, patterns)},
        "clusters_total": len(parts),
        "hubs": hubs,
        "hub_subject_code": sorted(set().union(*(code[h] for h in hubs))) if hubs else [],
        "cross_cluster": len(parts) >= 2 and bool(hubs),
        "changed_from": None,
        "changed": [],
    }
    if changed is not None:
        gone = {p for p in changed if not (repo / p).exists()}

        def touched(part: Tuple[str, List[str], List[str]]) -> bool:
            names = set(part[1]) | set(part[2])
            for d in part[1]:
                names |= refs[d] & gone
            return bool(names & changed)
        parts = [p for p in parts if touched(p)]
        result["cross_cluster"] = bool(result["cross_cluster"]) and bool(set(hubs) & changed)
        result["changed"] = sorted(changed)
    result["clusters"] = [{"id": i, "docs": d, "subject_code": c} for i, d, c in parts]
    return result


def changed_paths(repo: Path, ref: str) -> Set[str]:
    """Paths changed since the merge base of `ref` and HEAD, work tree included."""
    base = _git(repo, ["merge-base", ref, "HEAD"])
    if base is None:
        raise SystemExit("doc_clusters: cannot find the merge base of %r and HEAD in %s" % (ref, repo))
    diff = _git(repo, ["diff", "--name-only", "-z", "--no-renames", base.strip()])
    untracked = _git(repo, ["ls-files", "-z", "--others", "--exclude-standard"])
    if diff is None:
        raise SystemExit("doc_clusters: git diff against %s failed" % base.strip())
    return {p for p in (diff + (untracked or "")).split("\0") if p}


def default_excludes(no_default: bool, extra: Sequence[str]) -> Tuple[str, ...]:
    return ALWAYS_EXCLUDED + (() if no_default else HISTORICAL) + tuple(extra)


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo", default=".", help="repository root (default: the current directory)")
    ap.add_argument("--no-default-excludes", action="store_true",
                    help="also scan docs/design/ and docs/superpowers/")
    ap.add_argument("--exclude", action="append", default=[], help="another glob to exclude; repeatable")
    ap.add_argument("--changed-from", metavar="REF",
                    help="keep only the clusters a change since REF touches")
    ap.add_argument("--out", help="write the JSON here instead of stdout")
    args = ap.parse_args(argv)
    repo = Path(args.repo).resolve()
    changed = changed_paths(repo, args.changed_from) if args.changed_from else None
    result = build(repo, default_excludes(args.no_default_excludes, args.exclude), changed)
    if args.changed_from:
        result["changed_from"] = args.changed_from
    text = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
