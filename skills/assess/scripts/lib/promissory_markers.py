"""Promissory-marker scan: stale TODO/FIXME, suppressions, disabled tests.

Detects the four families of *promissory markers* - lines where the code makes
a promise about its own future - and ages each one by the number of commits to
its file that have landed since the marker was introduced ("survived touches").
A marker that survived many edits to an actively-maintained file is unactioned
intent: the damning case. A marker in a dormant file is just dormant; calendar
age alone cannot tell these apart, so survived-touches is the primary metric.

Families and the layer each one wounds:

- ``todo``           TODO / FIXME / HACK / XXX / TBD          -> Layer 8 (intent tracking)
- ``deprecation``    @deprecated / DEPRECATED / remove-after  -> Layer 2 (design honesty)
- ``suppression``    noqa / type: ignore / eslint-disable ... -> Layer 3 (linter integrity)
- ``disabled_test``  pytest.mark.skip / it.skip / @Disabled   -> Layer 5 (CI integrity)

A marker is *tracked* (pressure exists) when it cites an issue, ticket, URL, or
deadline date - or, for suppressions, when it carries a trailing justification
(``//nolint:x // reason``). The bare remainder is the debt. Each marker's
introducing commit is also classified agent/human (reusing the conservative B4
identity rules from ``change_coupling``), so "agent-introduced unactioned
intent" is a measured quantity, not an article of faith.

Pure subprocess (rg + git) and stdlib. No LLM calls. Degrades to
``available: False`` when ``rg`` is missing or the directory is not a git
repo; never raises out of ``scan_promissory_markers``.

CLI (standalone use)::

    uv run promissory_markers.py <repo_root> [--stale-touches 5] [--json OUT]
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from collections import defaultdict
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    from lib.change_coupling import _coauthors_have_agent, _identity_is_agent
    from lib.git_churn import churn_is_degenerate
except ImportError:  # standalone CLI: script dir is lib/, put scripts/ on path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from lib.change_coupling import _coauthors_have_agent, _identity_is_agent
    from lib.git_churn import churn_is_degenerate

# Default: a marker is stale once this many commits to its file landed after it.
STALE_TOUCHES_DEFAULT = 5

# Severity weight per family: a stale suppression or disabled test is a hole in
# an enforcement layer; a bare TODO is unactioned intent but wounds nothing yet.
FAMILY_WEIGHTS = {
    "suppression": 3,
    "disabled_test": 3,
    "deprecation": 2,
    "todo": 1,
}

# One rg pattern per family. Kept deliberately coarse: precision comes from the
# comment-context filter and the survived-touches join, not from the regex.
# Case-sensitive with word boundaries on purpose - a case-insensitive TODO
# matches every Dart ``toDouble()``. Ecosystem-specific syntaxes (Dart's
# ``// ignore:``) must be listed explicitly; absence means a silent miss, so
# new entries need a fixture in tests/test_promissory_markers.py.
FAMILY_PATTERNS = {
    "todo": r"\b(TODO|FIXME|HACK|XXX|TBD)\b|remove (after|before|once|when)|temporary (workaround|hack|fix)",
    "deprecation": r"@[Dd]eprecated\b|\bDEPRECATED\b",
    "suppression": (
        r"#\s*noqa|#\s*type:\s*ignore|eslint-disable|//\s*nolint|"
        r"@SuppressWarnings|#\s*nosec|rubocop:disable|pylint:\s*disable|"
        r"@ts-ignore|@ts-nocheck|//\s*NOSONAR|"
        r"//\s*ignore(_for_file)?:"  # Dart analyzer
    ),
    "disabled_test": (
        r"pytest\.mark\.skip|@unittest\.skip|\bxfail\b|"
        r"\b(it|test|describe|xit|xdescribe)\.skip\(|"
        r"t\.Skip\(|@Disabled\b|@Ignore\b|"
        r"\bskip:\s*(true|')"  # Dart test() / Playwright fixme param
    ),
}

# A marker counts as "linked" (tracked intent - pressure exists) when it cites
# an issue, a ticket, a URL, or a deadline date.
LINKED_RE = re.compile(r"#\d+|\b[A-Z][A-Z0-9]+-\d+\b|https?://|\b\d{4}-\d{2}-\d{2}\b")

# A suppression with a trailing justification is tracked: recorded reasoning is
# pressure (nolintlint-style). Matched as a second comment segment after the
# directive, e.g. ``//nolint:nilerr // error conveyed via response status``.
JUSTIFIED_SUPPRESSION_RE = re.compile(
    r"(nolint[^/]*//|noqa[^#]*#|eslint-disable[^*]*\*/|"
    r"//\s*ignore:[^/]*//|@SuppressWarnings\(.+\)\s*//)\s*\S"
)

# Comment leaders; a todo/deprecation hit must sit after one of these on its
# line (suppressions and disabled tests are syntactic and skip the check).
# Prose files count whole-line for todo/deprecation, and are excluded entirely
# for the syntactic families (a t.Skip in a guide is an example, not debt).
COMMENT_LEADERS = ("#", "//", "/*", "*", "<!--", "--", ";;", "%", '"""', "'''")
PROSE_SUFFIXES = {".md", ".markdown", ".rst", ".txt", ".adoc"}

# Generated / vendored / lockfile noise that rg's gitignore pass won't catch
# when the files are committed. Mirrors the treemap's exclude spirit; the
# generated-Dart entries proved load-bearing (1,684 of a Flutter repo's 1,698
# suppressions were codegen ``ignore_for_file`` boilerplate).
EXCLUDE_GLOBS = [
    "!**/*.pb.go", "!**/*_pb2.py", "!**/*_pb.ts", "!**/*.g.dart",
    "!**/*.freezed.dart", "!**/*.gr.dart", "!**/*.min.js", "!**/*.bundle.js",
    "!**/package-lock.json", "!**/deno.lock", "!**/*.lock", "!**/*.map",
    "!**/node_modules/**", "!**/vendor/**", "!**/dist/**", "!**/build/**",
    "!**/.assess/**", "!**/tests/fixtures/**", "!**/*.svg",
]

# Cap the markers carried into run-context.json so a pathological repo can't
# bloat the bus; family totals always reflect the full scan.
MAX_TOP_OFFENDERS = 10


@dataclass
class Marker:
    path: str
    line: int
    family: str
    text: str
    linked: bool
    commit: str = ""
    author_time: int = 0
    agent_introduced: bool | None = None  # None = could not classify
    survived_touches: int = -1  # -1 = could not be aged
    severity: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "line": self.line,
            "family": self.family,
            "text": self.text[:160],
            "linked": self.linked,
            "commit": self.commit[:12],
            "agent_introduced": self.agent_introduced,
            "survived_touches": self.survived_touches,
            "severity": round(self.severity, 2),
        }


@dataclass
class MarkerScan:
    available: bool
    reason: str = ""
    stale_touches_threshold: int = STALE_TOUCHES_DEFAULT
    markers: list[Marker] = field(default_factory=list)
    aging_reliable: bool = True  # False when history is too thin to age markers

    @property
    def stale(self) -> list[Marker]:
        return [
            m
            for m in self.markers
            if m.survived_touches >= self.stale_touches_threshold
        ]

    def stale_by_file(self) -> dict[str, dict[str, Any]]:
        """Per-file rollup of stale markers, for hotspot pages and findings."""
        rollup: dict[str, dict[str, Any]] = {}
        for m in self.stale:
            entry = rollup.setdefault(
                m.path, {"count": 0, "families": set(), "max_survived": 0}
            )
            entry["count"] += 1
            entry["families"].add(m.family)
            entry["max_survived"] = max(entry["max_survived"], m.survived_touches)
        return {
            p: {**e, "families": sorted(e["families"])} for p, e in rollup.items()
        }

    def summary(self) -> dict[str, Any]:
        fam: dict[str, dict[str, int]] = defaultdict(
            lambda: {"total": 0, "stale": 0, "linked": 0, "agent_introduced": 0}
        )
        for m in self.markers:
            fam[m.family]["total"] += 1
            fam[m.family]["linked"] += int(m.linked)
            fam[m.family]["agent_introduced"] += int(bool(m.agent_introduced))
            if m.survived_touches >= self.stale_touches_threshold:
                fam[m.family]["stale"] += 1
        bare = sum(1 for m in self.markers if m.family == "todo" and not m.linked)
        linked = sum(1 for m in self.markers if m.family == "todo" and m.linked)
        stale = self.stale
        top = sorted(stale, key=lambda m: -m.severity)[:MAX_TOP_OFFENDERS]
        return {
            "available": self.available,
            "reason": self.reason,
            "aging_reliable": self.aging_reliable,
            "stale_touches_threshold": self.stale_touches_threshold,
            "families": dict(fam),
            "todo_bare": bare,
            "todo_linked": linked,
            "total_markers": len(self.markers),
            "total_stale": len(stale),
            "stale_agent_introduced": sum(
                1 for m in stale if m.agent_introduced
            ),
            "stale_by_file": self.stale_by_file(),
            "top_offenders": [m.to_dict() for m in top],
        }


def _unavailable_summary(reason: str) -> dict[str, Any]:
    return MarkerScan(available=False, reason=reason).summary()


def _run(cmd: list[str], cwd: Path) -> str:
    out = subprocess.run(
        cmd, cwd=cwd, capture_output=True, text=True, errors="replace"
    )
    return out.stdout


def _extra_globs(
    extra_exclude_dirs: Iterable[str] | None,
    extra_exclude_patterns: Iterable[str] | None,
) -> list[str]:
    """Translate `.assess/config.toml` excludes into rg globs (excludes parity)."""
    globs: list[str] = []
    for d in sorted(extra_exclude_dirs or []):
        globs.append(f"!**/{d}/**")
    for p in sorted(extra_exclude_patterns or []):
        globs.append(f"!**/{p}")
    return globs


def _detect(repo_root: Path, extra_globs: list[str]) -> list[Marker]:
    """Stage 1: one rg pass per family, comment-context filtered."""
    markers: list[Marker] = []
    for family, pattern in FAMILY_PATTERNS.items():
        cmd = ["rg", "-n", "--no-heading", "--no-messages", "-e", pattern]
        for g in [*EXCLUDE_GLOBS, *extra_globs]:
            cmd += ["--glob", g]
        cmd.append(".")
        for raw in _run(cmd, repo_root).splitlines():
            parts = raw.split(":", 2)
            if len(parts) != 3:
                continue
            path, line_s, text = parts
            path = path.removeprefix("./")
            is_prose = Path(path).suffix.lower() in PROSE_SUFFIXES
            # Syntactic families in prose files are code examples, not debt.
            if family in ("suppression", "disabled_test") and is_prose:
                continue
            if family in ("todo", "deprecation") and not _comment_context(
                is_prose, text, pattern
            ):
                continue
            linked = bool(LINKED_RE.search(text))
            if family == "suppression" and not linked:
                linked = bool(JUSTIFIED_SUPPRESSION_RE.search(text))
            markers.append(
                Marker(
                    path=path,
                    line=int(line_s),
                    family=family,
                    text=text.strip(),
                    linked=linked,
                )
            )
    return markers


def _comment_context(is_prose: bool, text: str, pattern: str) -> bool:
    """Keep a todo/deprecation hit only when it sits in a comment-ish context.

    Prose files count whole-line; code files require a comment leader at or
    before the match position on the same line. This is a line-local heuristic,
    not a parser - block-comment interiors that start with a bare word are the
    known false-negative, and string-literal mentions are the false-positive it
    exists to drop.
    """
    if is_prose:
        return True
    m = re.search(pattern, text)
    if not m:
        return False
    prefix = text[: m.start()]
    return any(lead in prefix for lead in COMMENT_LEADERS) or prefix.strip() == ""


def _blame_ages(repo_root: Path, markers: list[Marker]) -> None:
    """Stage 2: batched git blame per hit-file -> introducing commit + time."""
    by_file: dict[str, list[Marker]] = defaultdict(list)
    for m in markers:
        by_file[m.path].append(m)

    def blame_one(item: tuple[str, list[Marker]]) -> None:
        path, ms = item
        cmd = ["git", "blame", "--porcelain"]
        for m in ms:
            cmd += ["-L", f"{m.line},{m.line}"]
        cmd += ["--", path]
        out = _run(cmd, repo_root)
        # Porcelain emits ranges in the order requested; each range opens with
        # "<sha> <orig_line> <final_line> <n>" followed by headers incl.
        # author-time, then the content line (tab-prefixed).
        idx = 0
        sha, atime = "", 0
        for ln in out.splitlines():
            if re.match(r"^[0-9a-f]{40} \d+ \d+", ln):
                sha = ln.split()[0]
            elif ln.startswith("author-time "):
                atime = int(ln.split()[1])
            elif ln.startswith("\t"):
                if idx < len(ms):
                    ms[idx].commit = sha
                    ms[idx].author_time = atime
                idx += 1

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(blame_one, by_file.items()))


def _classify_authorship(repo_root: Path, markers: list[Marker]) -> None:
    """Stage 3: classify each marker's introducing commit agent/human.

    One batched ``git log --no-walk`` over the unique SHAs (single subprocess),
    reusing the conservative B4 identity rules: ``[bot]`` marker, AI e-mail
    hints, or an agent Co-Authored-By trailer. Conservative by construction -
    a human's work is never labelled agent on weak evidence.
    """
    shas = sorted({m.commit for m in markers if m.commit})
    if not shas:
        return
    out = subprocess.run(
        ["git", "log", "--no-walk", "--stdin",
         "--format=%H%x02%ae%x02%an%x02%(trailers:key=Co-Authored-By,valueonly,separator=%x1d)"],
        cwd=repo_root, input="\n".join(shas), capture_output=True,
        text=True, errors="replace",
    ).stdout
    agent_by_sha: dict[str, bool] = {}
    for ln in out.splitlines():
        parts = ln.split("\x02")
        if len(parts) != 4:
            continue
        sha, email, name, coauthors = parts
        agent_by_sha[sha] = (
            _identity_is_agent(email, name) or _coauthors_have_agent(coauthors)
        )
    for m in markers:
        m.agent_introduced = agent_by_sha.get(m.commit)


def _file_commit_times(repo_root: Path) -> dict[str, list[int]]:
    """One git-log pass: per-file list of commit timestamps (newest first)."""
    # %at (author time) to match blame's author-time header - committer time
    # diverges after rebases and would skew the survived-touches comparison.
    out = _run(
        ["git", "log", "--format=%x01%at", "--name-only", "--no-renames"],
        repo_root,
    )
    times: dict[str, list[int]] = defaultdict(list)
    current = 0
    for ln in out.splitlines():
        if ln.startswith("\x01"):
            current = int(ln[1:])
        elif ln.strip():
            times[ln.strip()].append(current)
    return times


def scan_promissory_markers(
    repo_root: Path,
    stale_touches: int = STALE_TOUCHES_DEFAULT,
    extra_exclude_dirs: Iterable[str] | None = None,
    extra_exclude_patterns: Iterable[str] | None = None,
) -> MarkerScan:
    """Full pipeline: detect -> blame-age -> authorship -> severity."""
    try:
        subprocess.run(["rg", "--version"], capture_output=True, check=True)
    except (OSError, subprocess.CalledProcessError):
        return MarkerScan(available=False, reason="rg not on PATH")
    if not (repo_root / ".git").exists():
        return MarkerScan(available=False, reason="not a git repository root")

    try:
        markers = _detect(
            repo_root, _extra_globs(extra_exclude_dirs, extra_exclude_patterns)
        )
        _blame_ages(repo_root, markers)
        _classify_authorship(repo_root, markers)
        commit_times = _file_commit_times(repo_root)
        counts = {p: len(ts) for p, ts in commit_times.items()}

        # Degenerate history (every file ~1 commit: shallow clone, squashed
        # import) means survived-touches carries no information. Stay honest:
        # report markers but mark aging unreliable so nothing reads as "clean".
        # Same verdict definition as every other churn consumer (git_churn).
        aging_reliable = not churn_is_degenerate(counts.values())
        ranked = sorted(counts.values())
        n_ranked = len(ranked)
        for m in markers:
            ts = commit_times.get(m.path, [])
            if m.author_time:
                m.survived_touches = sum(1 for t in ts if t > m.author_time)
            file_count = counts.get(m.path, 0)
            # bisect-free decile: fraction of files with fewer commits
            below = sum(1 for c in ranked if c < file_count)
            decile = (below / n_ranked) * 10 if n_ranked else 0.0
            m.severity = FAMILY_WEIGHTS[m.family] * max(m.survived_touches, 0) * (
                1 + decile / 10
            )
        return MarkerScan(
            available=True,
            stale_touches_threshold=stale_touches,
            markers=markers,
            aging_reliable=aging_reliable,
        )
    except Exception as exc:  # noqa: BLE001 - degrade, never crash the core
        return MarkerScan(available=False, reason=f"{type(exc).__name__}: {exc}")


def main() -> int:
    import argparse
    import time

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("repo_root", type=Path)
    ap.add_argument("--stale-touches", type=int, default=STALE_TOUCHES_DEFAULT)
    ap.add_argument("--json", type=Path, help="write full summary JSON here")
    args = ap.parse_args()

    t0 = time.monotonic()
    scan = scan_promissory_markers(args.repo_root.resolve(), args.stale_touches)
    elapsed = time.monotonic() - t0
    s = scan.summary()
    s["elapsed_seconds"] = round(elapsed, 2)

    if args.json:
        args.json.write_text(json.dumps(s, indent=2))

    if not scan.available:
        print(f"unavailable: {scan.reason}")
        return 1
    print(f"scanned in {elapsed:.2f}s  threshold={scan.stale_touches_threshold} touches")
    print(f"{'family':<15}{'total':>7}{'stale':>7}{'linked':>8}{'agent':>7}")
    for fam, row in sorted(s["families"].items()):
        print(
            f"{fam:<15}{row['total']:>7}{row['stale']:>7}{row['linked']:>8}"
            f"{row['agent_introduced']:>7}"
        )
    print(
        f"\nbare:linked TODOs = {s['todo_bare']}:{s['todo_linked']}   "
        f"total stale = {s['total_stale']}/{s['total_markers']}   "
        f"stale agent-introduced = {s['stale_agent_introduced']}"
    )
    print("\ntop offenders (severity = weight x survived x churn-decile):")
    for m in s["top_offenders"]:
        who = {True: "agent", False: "human", None: "?"}[m["agent_introduced"]]
        print(
            f"  {m['severity']:>7.1f}  survived {m['survived_touches']:>3}  "
            f"[{m['family']}/{who}] {m['path']}:{m['line']}  {m['text'][:60]}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
