"""Git-log change-coupling and authorship primitives for the /assess core.

Three deterministic signals derived purely from ``git log``, mirroring the
subprocess+stdlib style of ``lib.git_churn`` (no AI, no heavy deps, every git
call capped by ``GIT_TIMEOUT_SECONDS`` and degrading to an empty/neutral result
on failure):

  - **B1 change-coupling** (:func:`change_coupling_pairs`) - file pairs that
    keep co-changing in the same commit. Hidden edges an agent can't see from
    the import graph: edit A and you probably need to edit B.
  - **B2 containment** (:func:`containment_ratio`) - what fraction of the
    commits that touch a module touch *only* that module. High = a safe island
    an agent can change without ripples leaking out.
  - **B4 authorship** (:func:`authorship_analysis`) - whether a path has a human
    anchor and a human intent source, and a deliberately conservative
    human/agent/mixed/unknown class. We never label a human's work "agent" on
    weak evidence (PRD Open Question 6): detection is e-mail-based, never on a
    person's *name* (someone really can be called "Claude").

All returned structures are JSON-serialisable (paths as strings, plain
dict/list/bool/number) so task #5 can drop them straight into run-context.json.
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from itertools import combinations
from pathlib import Path

# Cap every git call so a stuck invocation (huge repo, lock contention, a hung
# credential prompt) degrades to "no data" rather than blocking the run. Same
# value and rationale as lib.git_churn.
GIT_TIMEOUT_SECONDS = 20

# Commits that touch more files than this are bulk/mechanical (mass renames,
# vendoring, reformatting, license headers). Pairing every file in them would
# both explode combinatorially (n^2) and drown the genuine coupling signal in
# noise, so they are excluded from pair generation. They still count toward the
# commit total (the support_pct denominator).
MAX_COMMIT_FILES_FOR_COUPLING = 50

# --- Agent detection (e-mail based, conservative) ----------------------------
# We classify by e-mail, NEVER by display name: "Claude", "Cursor" and "Codex"
# are all real human given names/surnames, and mislabelling a person's work as
# agent-generated is the failure mode PRD Open Question 6 tells us to avoid.
# `[bot]` is the GitHub Apps convention (dependabot, renovate, github-actions,
# copilot all commit as `name[bot]`); the anthropic/copilot addresses cover the
# common Co-Authored-By trailers. We deliberately do NOT treat bare
# `noreply@github.com` / `users.noreply.github.com` as a bot - those are the
# privacy addresses humans use for web-UI commits.
AI_EMAIL_HINTS = (
    "[bot]",
    "noreply@anthropic.com",
    "copilot@",
)

_CO_AUTHORED_BY = re.compile(r"co-authored-by:\s*(.+)", re.IGNORECASE)


def repo_top(repo_root: Path) -> str | None:
    """Absolute repo top-level for ``repo_root``, or None if not in a git repo."""
    try:
        return subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, check=True, timeout=GIT_TIMEOUT_SECONDS,
        ).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        return None


def parse_commit_file_sets(
    repo_root: Path, since: str | None = None, *, top: str | None = None,
) -> list[set[Path]]:
    """Return one set of touched files per commit, parsed from ``git log``.

    Each set holds the repo-relative paths (as git prints them with
    ``--name-only``) changed by a single commit, newest first. Merge commits and
    commits that changed no files yield an empty set, so the list length equals
    the number of commits in the window - callers that need a commit count can
    use ``len(...)``. Returns ``[]`` when ``repo_root`` is not inside a git repo.

    Pass ``since`` as a git date expression (e.g. ``"12 months ago"``) to window
    the history; ``None`` (default) means full history reachable from HEAD.
    Renames are not followed: a commit made before a rename lists the file under
    its old path. Pass the sets through :func:`fold_renames` with
    :func:`build_rename_map`'s paths to count that history under current paths.
    ``top`` is the repo top-level when the caller already resolved it (see
    :func:`repo_top`), saving a ``git rev-parse``.
    """
    top = top or repo_top(repo_root)
    if top is None:
        return []

    # \x1e (ASCII record separator) marks the start of each commit so we can
    # split unambiguously; the name-only file list follows on its own lines.
    # -M pinned so a rename commit lists only the new name whatever the user's
    # diff.renames setting, matching build_rename_map's detection.
    # core.quotepath=false keeps non-ASCII paths literal, not octal-escaped, so
    # they match files on disk.
    cmd = ["git", "-c", "core.quotepath=false", "-C", top, "log", "--name-only", "-M",
           "--pretty=format:\x1e%H"]
    if since:
        cmd.append(f"--since={since}")
    try:
        raw = subprocess.run(
            cmd, capture_output=True, text=True, check=True, timeout=GIT_TIMEOUT_SECONDS,
        ).stdout
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return []

    commit_sets: list[set[Path]] = []
    for chunk in raw.split("\x1e"):
        if not chunk.strip():
            continue
        lines = chunk.splitlines()
        # lines[0] is the commit hash; the rest are touched files.
        files: set[Path] = set()
        for line in lines[1:]:
            line = line.strip()
            if line:
                files.add(Path(line))
        commit_sets.append(files)
    return commit_sets


@dataclass(frozen=True)
class RenameMap:
    """Historical path -> current path, and whether git history was read.

    ``complete`` is False when git history exists but could not be read (git
    failed or timed out). Outside a git repo there is no history to rename, so
    the empty map is complete. An empty ``paths`` then means "unknown", not "no
    renames", so a caller must not treat an unmapped old path as deleted.
    """

    paths: dict[str, str]
    complete: bool


def build_rename_map(repo_root: Path, *, top: str | None = None) -> RenameMap:
    """Map each historical path that git saw renamed to its current path.

    Parsed from ``git log --topo-order --name-status -M --diff-filter=R``. A
    path starts from its first rename, and the chain moves on only through a
    rename made in a commit that descends from the one before it (``a -> b``
    then ``b -> c`` maps ``a`` to ``c``; ``b -> c`` then ``a -> b`` maps ``a``
    to ``b``, and so do renames on sibling branches). A source path that exists
    again in the working tree is left out, so a name reused after a rename keeps
    its own history. Paths are repo-relative, as :func:`parse_commit_file_sets`
    prints them. Outside a git repo the result is empty and complete; on a git
    failure it is empty and ``complete`` is False. ``top`` is as for
    :func:`parse_commit_file_sets`.
    """
    top = top or repo_top(repo_root)
    if top is None:
        return RenameMap({}, complete=True)
    # \x1e marks each commit and carries its hash. --topo-order lists every
    # commit before its ancestors, so reversed, an ancestor always has the
    # smaller index.
    cmd = ["git", "-c", "core.quotepath=false", "-C", top, "log", "--topo-order",
           "--name-status", "-M", "--diff-filter=R", "--pretty=format:\x1e%H"]
    try:
        raw = subprocess.run(
            cmd, capture_output=True, text=True, check=True, timeout=GIT_TIMEOUT_SECONDS,
        ).stdout
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return RenameMap({}, complete=False)

    # edges[src] lists (index, commit, dst) with the oldest rename first.
    edges: dict[str, list[tuple[int, str, str]]] = {}
    chunks = [c for c in raw.split("\x1e") if c.strip()]
    for index, chunk in enumerate(reversed(chunks)):
        lines = chunk.splitlines()
        for line in lines[1:]:
            parts = line.split("\t")
            if len(parts) == 3 and parts[0].startswith("R"):
                edges.setdefault(parts[1], []).append((index, lines[0].strip(), parts[2]))

    ancestry: dict[tuple[str, str], bool] = {}

    def descends(commit: str, ancestor: str) -> bool:
        """True when ``commit`` has ``ancestor`` in its history (cached).

        Exit 0 is yes and 1 is no. Anything else (128 for an object git cannot
        resolve, as at a shallow or grafted boundary) is a failure, raised so the
        map comes back incomplete rather than reading as unrelated commits.
        """
        key = (ancestor, commit)
        if key not in ancestry:
            cmd = ["git", "-C", top, "merge-base", "--is-ancestor", ancestor, commit]
            result = subprocess.run(cmd, capture_output=True, timeout=GIT_TIMEOUT_SECONDS)
            if result.returncode not in (0, 1):
                raise subprocess.CalledProcessError(result.returncode, cmd)
            ancestry[key] = result.returncode == 0
        return ancestry[key]

    def next_edge(path: str, index: int, commit: str) -> tuple[int, str, str] | None:
        """The first rename of ``path`` in a later commit descending from ``commit``."""
        return next((e for e in edges.get(path, [])
                     if e[0] > index and descends(e[1], commit)), None)

    # The walk follows an edge only when its commit descends from the one that
    # moved the content to the current name, so a name freed by one rename and
    # refilled by another (b -> c, then a -> b, in sequence or on sibling
    # branches) is not chained through: a maps to b, not c. Chains resolve before
    # sources that exist again in the working tree are dropped, so a reused
    # intermediate name (a -> b, b -> c, then a fresh b) still leads a to c.
    resolved: dict[str, str] = {}
    try:
        for old, outgoing in edges.items():
            index, commit, cur = outgoing[0]
            hop = next_edge(cur, index, commit)
            while hop is not None:
                index, commit, cur = hop
                hop = next_edge(cur, index, commit)
            resolved[old] = cur
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return RenameMap({}, complete=False)
    top_path = Path(top)
    return RenameMap(
        {old: new for old, new in resolved.items() if not (top_path / old).exists()},
        complete=True,
    )


def fold_renames(
    commit_sets: list[set[Path]], rename_map: dict[str, str],
) -> list[set[Path]]:
    """Rewrite each commit's paths through ``rename_map`` (a :class:`RenameMap`'s paths).

    History recorded under an old path is counted under the current one, so a
    pair of files renamed after they co-changed keeps its co-change count under
    the new names. Returns ``commit_sets`` unchanged when the map is empty.
    """
    if not rename_map:
        return commit_sets
    return [
        {Path(rename_map.get(f.as_posix(), f.as_posix())) for f in files}
        for files in commit_sets
    ]


def change_coupling_pairs(
    commit_sets: list[set[Path]], min_support: int = 3,
) -> list[dict]:
    """B1: file pairs that co-change, from :func:`parse_commit_file_sets` output.

    For every commit, all unordered file pairs are tallied; a pair is reported
    only when its ``co_change_count`` reaches ``min_support`` (default 3). Each
    entry is ``{file_a, file_b, co_change_count, support_pct}`` with paths as
    strings and ``file_a < file_b`` lexicographically. ``support_pct`` is the
    percentage of *all* commits in the window in which the pair co-changed
    (``100 * co_change_count / len(commit_sets)``).

    Commits touching more than ``MAX_COMMIT_FILES_FOR_COUPLING`` files are
    skipped for pairing (bulk/mechanical noise) but still count toward the
    support denominator. Results are sorted by count descending, then path.
    """
    total_commits = len(commit_sets)
    counts: dict[tuple[str, str], int] = {}
    for files in commit_sets:
        if len(files) < 2 or len(files) > MAX_COMMIT_FILES_FOR_COUPLING:
            continue
        # sorted() over Paths gives a deterministic, file_a<file_b ordering.
        for a, b in combinations(sorted(files), 2):
            key = (str(a), str(b))
            counts[key] = counts.get(key, 0) + 1

    pairs = [
        {
            "file_a": a,
            "file_b": b,
            "co_change_count": count,
            "support_pct": round(100.0 * count / total_commits, 2) if total_commits else 0.0,
        }
        for (a, b), count in counts.items()
        if count >= min_support
    ]
    # dict values are heterogeneous (str | int | float), so mypy types the
    # lookup as ``object``; the negation is valid at runtime (count is int).
    pairs.sort(key=lambda d: (-d["co_change_count"], d["file_a"], d["file_b"]))  # type: ignore[operator]
    return pairs


def _normalise_module(repo_root: Path, module_path: Path | str) -> Path:
    """Return ``module_path`` as a repo-relative Path to match commit file sets."""
    mod = Path(module_path)
    if mod.is_absolute():
        top = repo_top(repo_root)
        if top:
            try:
                mod = mod.resolve().relative_to(Path(top).resolve())
            except ValueError:
                pass
    return mod


def containment_ratio(
    repo_root: Path, module_path: Path | str, commit_sets: list[set[Path]],
) -> float:
    """B2: fraction of commits touching ``module_path`` that touch *only* it.

    ``commits touching only files in the module / commits touching the module at
    all``. 1.0 means every change to the module was self-contained (a safe
    island an agent can edit without ripples); a low value means edits to the
    module routinely drag in files elsewhere.

    ``module_path`` may be a directory or a file, absolute or repo-relative; it
    is normalised to the repo-relative form that :func:`parse_commit_file_sets`
    produces. A file is "in the module" if it equals or sits under that path.

    **Zero commits touch the module -> returns 1.0.** With no observed bleed
    there is nothing to contradict containment, so it is treated as vacuously
    contained. Callers that must tell "safe island" from "no history" should
    check module activity separately (e.g. via churn).
    """
    mod = _normalise_module(repo_root, module_path)

    def in_module(f: Path) -> bool:
        if f == mod:
            return True
        try:
            f.relative_to(mod)
            return True
        except ValueError:
            return False

    touching = 0
    only = 0
    for files in commit_sets:
        if not files:
            continue
        in_mod = [f for f in files if in_module(f)]
        if in_mod:
            touching += 1
            if len(in_mod) == len(files):
                only += 1

    if touching == 0:
        return 1.0
    return only / touching


def _identity_is_agent(email: str, name: str = "") -> bool:
    """True if an identity belongs to a known bot/agent.

    Matches the ``[bot]`` GitHub Apps marker in either name or e-mail (a reserved
    convention no human carries, so safe to match on the name), plus the
    AI-tool e-mail hints. AI tool *names* ("Claude", "Cursor") are never matched
    - those are real human names too (PRD Open Question 6).
    """
    e = email.lower()
    n = name.lower()
    if "[bot]" in e or "[bot]" in n:
        return True
    return any(hint in e for hint in AI_EMAIL_HINTS)


def _coauthors_have_agent(coauthor_field: str) -> bool:
    """True if any Co-Authored-By trailer names an agent (matched on its e-mail)."""
    # Trailer values look like "Claude <noreply@anthropic.com>"; we match the
    # bracketed e-mail, never the display name.
    for value in coauthor_field.split("\x1d"):
        value = value.strip()
        if not value:
            continue
        emails = re.findall(r"<([^>]+)>", value)
        target = emails[0] if emails else value
        if _identity_is_agent(target):
            return True
    return False


def authorship_analysis(repo_root: Path, path: Path | str) -> dict:  # noqa: C901  # multi-signal B4 heuristic; ccn 19, ratchet target
    """B4: human-anchor / intent-source signals and a conservative class for ``path``.

    Returns ``{human_anchor, authorship_class, intent_source, contributors}``:

      - ``human_anchor`` (bool): a confirmed human authored at least one commit -
        someone who can be asked about the code.
      - ``intent_source`` (bool): a human authored *or* committed at least one
        commit - a human directed the change even if an agent wrote the diff.
      - ``authorship_class``: one of ``'human'``, ``'agent'``, ``'mixed'``,
        ``'unknown'``. Deliberately cautious - ``'agent'`` requires that *every*
        commit is a confirmed agent with no human and no ambiguous author;
        anything uncertain falls back to ``'unknown'`` rather than risk
        attributing a person's work to a machine (PRD Open Question 6).
      - ``contributors``: per-author ``{name, email, commits, lines_added,
        lines_removed, classification}``, sorted by commit count descending.

    Degrades to ``{human_anchor: False, authorship_class: 'unknown',
    intent_source: False, contributors: []}`` when there is no git history (path
    untracked, repo absent, git missing/slow).
    """
    default = {
        "human_anchor": False,
        "authorship_class": "unknown",
        "intent_source": False,
        "contributors": [],
    }
    top = repo_top(repo_root)
    if top is None:
        return default

    # One record per commit, RS-delimited; fields US-delimited. Co-author
    # trailers are folded onto one line with a GS (\x1d) separator so the header
    # stays single-line and numstat rows follow it cleanly.
    fmt = "\x1e%H\x1f%an\x1f%ae\x1f%cn\x1f%ce\x1f%(trailers:key=Co-authored-by,valueonly,separator=%x1d)"
    try:
        raw = subprocess.run(
            ["git", "-C", top, "log", "--no-merges", "--numstat",
             f"--format={fmt}", "--", str(path)],
            capture_output=True, text=True, check=True, timeout=GIT_TIMEOUT_SECONDS,
        ).stdout
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        return default

    any_agent = False
    any_human = False
    n_unknown = 0
    intent_source = False
    # Aggregate per author identity (name, email).
    contributors: dict[tuple[str, str], dict] = {}
    total_commits = 0

    for chunk in raw.split("\x1e"):
        if not chunk.strip():
            continue
        lines = chunk.split("\n")
        fields = lines[0].split("\x1f")
        # Pad in case a field is empty/missing.
        fields += [""] * (6 - len(fields))
        _h, an, ae, cn, ce, coauthors = fields[:6]
        total_commits += 1

        author_agent = _identity_is_agent(ae, an)
        committer_agent = _identity_is_agent(ce, cn)
        coauthor_agent = _coauthors_have_agent(coauthors)
        author_human = bool(ae.strip()) and "@" in ae and not author_agent
        committer_human = bool(ce.strip()) and "@" in ce and not committer_agent

        agent_involved = author_agent or committer_agent or coauthor_agent
        if agent_involved:
            any_agent = True
        if author_human:
            any_human = True
        if author_human or committer_human:
            intent_source = True
        if not author_human and not agent_involved:
            n_unknown += 1

        # Line stats: numstat rows are "added\tremoved\tfile"; "-" marks binary.
        added = removed = 0
        for row in lines[1:]:
            row = row.strip()
            if not row:
                continue
            parts = row.split("\t")
            if len(parts) >= 2:
                added += int(parts[0]) if parts[0].isdigit() else 0
                removed += int(parts[1]) if parts[1].isdigit() else 0

        if author_agent:
            classification = "agent"
        elif author_human:
            classification = "human"
        else:
            classification = "unknown"
        key = (an, ae)
        agg = contributors.get(key)
        if agg is None:
            contributors[key] = {
                "name": an,
                "email": ae,
                "commits": 1,
                "lines_added": added,
                "lines_removed": removed,
                "classification": classification,
            }
        else:
            agg["commits"] += 1
            agg["lines_added"] += added
            agg["lines_removed"] += removed

    if total_commits == 0:
        return default

    if any_agent and any_human:
        authorship_class = "mixed"
    elif any_agent and not any_human:
        # Agent evidence but no confirmed human. Only call it pure 'agent' when
        # there is zero ambiguity; otherwise stay 'unknown' to avoid claiming a
        # possibly-human commit was agent-made.
        authorship_class = "agent" if n_unknown == 0 else "unknown"
    elif any_human and not any_agent:
        authorship_class = "human"
    else:
        authorship_class = "unknown"

    contributor_list = sorted(
        contributors.values(), key=lambda c: (-c["commits"], c["name"], c["email"]),
    )
    return {
        "human_anchor": any_human,
        "authorship_class": authorship_class,
        "intent_source": intent_source,
        "contributors": contributor_list,
    }


# --- E2: self-referential test authorship -------------------------------------

def find_self_referential_tests(
    repo_root: Path,
    test_to_code_map: dict[str, str],
    commit_sets: list[set[Path]] | None = None,
) -> list[dict]:
    """E2: tests added in the same commit as the code they cover.

    A test introduced alongside its subject in one commit risks verifying
    *internal consistency* (the author's mental model at the moment of writing)
    rather than *truth* (independently specified behaviour). This is the
    high-precision, same-commit signal: each ``{test_file, source_file}`` pair
    from ``test_to_code_map`` is flagged when at least one commit touches both
    files. It deliberately does NOT chase code+tests split across two commits -
    start with the precise signal, measure the miss rate before widening.

    ``commit_sets`` is reused from the orchestrator's single ``git log`` parse
    when supplied; otherwise it is parsed here. Returns a list of
    ``{test_file, source_file, reason}`` dicts, sorted by ``test_file`` for
    determinism. Empty map or no git history yields ``[]``.
    """
    if not test_to_code_map:
        return []
    if commit_sets is None:
        commit_sets = parse_commit_file_sets(Path(repo_root))
    commit_path_sets = [{str(f) for f in files} for files in commit_sets]
    self_ref: list[dict] = []
    for test_file, source_file in sorted(test_to_code_map.items()):
        for paths in commit_path_sets:
            if test_file in paths and source_file in paths:
                self_ref.append({
                    "test_file": test_file,
                    "source_file": source_file,
                    "reason": "test added in same commit as code",
                })
                break
    return self_ref
