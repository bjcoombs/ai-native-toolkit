"""Ownership-map parsing for Layer 0 structure-drift detection.

Ownership is declared in two places an LLM contributor reads as authoritative
boundaries: a GitHub ``CODEOWNERS`` file (glob -> owner) and a freeform
``ARCHITECTURE.md`` (prose -> "module X owns these paths"). Both are *declared*
maps of how the code is meant to be organised. The structure-drift signals
(tasks 9/10) compare those declarations against where the code actually lives
and how it actually changes - a glob that matches nothing, a declared module
whose files have scattered, a boundary the commit history no longer respects.

This module is only the parse half: turn the two declaration formats into
``{declared_boundary: {matched_file_paths}}`` maps and flag the globs that
already match zero files (the cheapest drift - a boundary the filesystem has
left behind). It mirrors ``doc_graph.py`` for module shape: a single shared
file walk with the same ``EXCLUDE_DIRS`` / ``is_excluded_path`` resolution,
``tracked_files`` to honour ``.gitignore``, and honest degradation to an
``available=False`` result rather than ever crashing the assessment.

Determinism is a contract: the same repo must produce byte-identical output, so
every returned collection is sorted at the boundary and no set/dict iteration
order leaks out. The drift signals that consume this module (tasks 9/10) and the
orchestrator wiring (task 11) are deliberately not here - this is the parser and
empty-glob detector only.
"""
from __future__ import annotations

import fnmatch
import re
import sys
from pathlib import Path

from lib.doc_graph import is_excluded_path, is_repo_file
from lib.git_churn import tracked_files

# Where an ownership map can legitimately live. CODEOWNERS is recognised by
# GitHub at the repo root, in ``.github/``, or in ``docs/`` - we honour the same
# three so a repo that keeps it in any of them is read, not missed.
CODEOWNERS_LOCATIONS: tuple[str, ...] = (
    "CODEOWNERS",
    ".github/CODEOWNERS",
    "docs/CODEOWNERS",
)

# Markdown docs that declare module boundaries in prose. ``ARCHITECTURE.md`` is
# the convention, but a repo often carries the same boundary map in a top-level
# or per-package ``README.md`` (a "co-change seam map" / module reference). We
# scan the conventional names wherever they sit so the declaration is read from
# whatever file actually holds it, not only one hard-coded path.
ARCHITECTURE_BASENAMES: frozenset[str] = frozenset({
    "architecture.md",
    "design.md",
})

# A README only counts as an architecture doc when it actually declares module
# boundaries - a generic project README is not an ownership map. We treat a
# README as a boundary declaration when its prose carries an ownership/seam
# vocabulary (see ``_declares_boundaries``); otherwise it is skipped.
README_BASENAMES: frozenset[str] = frozenset({"readme.md"})

# Prose that marks a markdown doc as declaring module ownership/boundaries. Used
# to admit a README as an architecture doc only when it genuinely maps modules.
_BOUNDARY_VOCAB_RE = re.compile(
    r"\b(owns?|owner|ownership|boundar(?:y|ies)|module(?:s)?|seam(?:s)?|"
    r"co-?change|cohesion|co-?locat)",
    re.IGNORECASE,
)

# Inline-code spans and fenced blocks carry the path references we extract from
# prose - ``doc_graph.py`` strips these to *avoid* phantom links, but here a
# path written as code (`` `skills/assess/scripts` ``) is exactly the boundary
# declaration we want, so we read them rather than strip them.
_FENCE_RE = re.compile(r"```.*?\n.*?```", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`([^`\n]{1,200})`")
_WIKILINK_RE = re.compile(r"\[\[([^\[\]]+?)\]\]")
# A bare path reference in prose: a slash-bearing token that looks like a repo
# path. Anchored on a path separator so plain words don't match; trailing
# punctuation is trimmed by the caller.
_BARE_PATH_RE = re.compile(r"(?<![\w./-])([A-Za-z0-9_.-]+/[A-Za-z0-9_./-]+)")

# A markdown section header (``#``..``######``). The text after the hashes names
# the module the section is about; its path references are attributed to it.
_HEADER_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*$")

# Glob characters that make a CODEOWNERS pattern a wildcard rather than a literal
# path. Used only to classify a pattern for reporting; matching uses fnmatch.
_GLOB_CHARS = set("*?[")


def _warn(msg: str) -> None:
    """Best-effort warning to stderr; never raises, never blocks the scan."""
    print(f"note: ownership_parser: {msg}", file=sys.stderr)


def _strip_anchor(target: str) -> str:
    """Drop a ``|alias`` (wikilink) and ``#anchor`` / ``?query`` from a target."""
    target = target.split("|", 1)[0]
    target = target.split("#", 1)[0]
    target = target.split("?", 1)[0]
    return target.strip().strip("\"'")


def _codeowners_path(repo_root: Path) -> Path | None:
    """The single CODEOWNERS file in effect, by GitHub's location precedence."""
    for rel in CODEOWNERS_LOCATIONS:
        candidate = repo_root / rel
        if candidate.is_file():
            return candidate
    return None


def _glob_to_fnmatch(pattern: str) -> tuple[str, ...]:
    """Translate a CODEOWNERS glob to the fnmatch patterns it matches against.

    Returns one or more fnmatch patterns (any match counts), normalising the two
    places CODEOWNERS rules differ from shell globs:

    - A leading ``/`` anchors to the repo root; without it the pattern matches at
      any depth. fnmatch has no anchor concept, so an unanchored pattern is
      tried both as-is (matching at root) *and* prefixed with ``*/`` (matching in
      any subdirectory) - so a bare ``Makefile`` claims both ``Makefile`` and
      ``tools/Makefile``. An anchored or already-path-qualified pattern is tried
      root-relative only.
    - A trailing ``/`` (a directory) matches everything beneath it, so ``docs/``
      becomes ``docs/*``.

    fnmatch's ``*`` crosses ``/`` for us, so ``**`` and ``*`` behave the same;
    every ``**`` is collapsed to ``*`` for one predictable form.
    """
    pat = pattern.strip()
    anchored = pat.startswith("/")
    pat = pat.lstrip("/")
    if pat.endswith("/"):
        pat = pat + "*"
    pat = pat.replace("**", "*")
    # An anchored or path-qualified pattern is root-relative; a bare, unanchored
    # pattern matches at any depth, so also try the subdirectory form.
    if anchored or "/" in pat.rstrip("*"):
        return (pat,)
    return (pat, "*/" + pat)


def _match_glob(pattern: str, rel_paths: list[str]) -> set[Path]:
    """Repo-relative paths matching a CODEOWNERS glob.

    Matches both the file itself and any file beneath a directory pattern, so a
    ``src/`` rule claims every file under ``src``. A path matches when it matches
    any of the fnmatch forms ``_glob_to_fnmatch`` derives for the pattern.
    """
    forms = _glob_to_fnmatch(pattern)
    out: set[Path] = set()
    for rp in rel_paths:
        if any(fnmatch.fnmatch(rp, form) for form in forms):
            out.add(Path(rp))
    return out


def parse_codeowners(repo_root: Path) -> dict[str, set[Path]]:
    """Parse the repo's CODEOWNERS into ``{glob_pattern: {matched_file_paths}}``.

    GitHub CODEOWNERS format: each non-comment line is ``pattern @owner...``;
    we keep the pattern (the declared boundary) and resolve it against the
    git-tracked file set so ``.gitignore``'d files never count. Comment (``#``)
    and blank lines are skipped. A pattern that appears twice is unioned. Every
    returned path set is the resolved match set; the empty-glob detector reads
    these to flag patterns that match nothing.

    Degrades to an empty dict when no CODEOWNERS file exists - the caller reads
    that as ``available: False`` ("no ownership map"). A malformed individual
    line is skipped with a warning rather than aborting the parse.
    """
    repo_root = repo_root.resolve()
    path = _codeowners_path(repo_root)
    if path is None:
        return {}

    tracked = tracked_files(repo_root)
    rel_paths = _tracked_rel_paths(repo_root, tracked)

    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError as exc:
        _warn(f"could not read {path}: {exc}")
        return {}

    out: dict[str, set[Path]] = {}
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        # ``pattern @owner1 @owner2`` - the pattern is the first whitespace token.
        try:
            pattern = line.split()[0]
        except IndexError:  # pragma: no cover - split() of non-empty never empty
            _warn(f"{path}:{lineno}: could not parse line, skipping")
            continue
        out.setdefault(pattern, set()).update(_match_glob(pattern, rel_paths))
    return out


def _tracked_rel_paths(
    repo_root: Path, tracked: frozenset[Path] | None,
) -> list[str]:
    """Repo-relative POSIX path strings for every tracked, non-excluded file.

    Honours the same ``EXCLUDE_DIRS`` / ``is_excluded_path`` resolution the doc
    graph uses, and falls back to a filesystem walk (with the symlink guard)
    when the tree is not under git, so the glob resolver always has a file set.
    """
    rels: list[str] = []
    if tracked is not None:
        for abs_path in tracked:
            try:
                rel = abs_path.relative_to(repo_root)
            except ValueError:
                continue
            if is_excluded_path(rel):
                continue
            rels.append(rel.as_posix())
        return sorted(rels)
    # Non-git tree: walk the filesystem, applying the same excludes + symlink guard.
    for abs_path in repo_root.rglob("*"):
        if not abs_path.is_file():
            continue
        try:
            rel = abs_path.relative_to(repo_root)
        except ValueError:
            continue
        if is_excluded_path(rel):
            continue
        if not is_repo_file(abs_path, repo_root, None):
            continue
        rels.append(rel.as_posix())
    return sorted(rels)


def _discover_arch_docs(repo_root: Path) -> list[Path]:
    """Markdown docs that declare module boundaries, in deterministic order.

    Admits the conventional architecture filenames (``ARCHITECTURE.md`` /
    ``DESIGN.md``) wherever they sit, plus any ``README.md`` whose prose carries
    the ownership/seam vocabulary (a generic README is skipped). Mirrors the doc
    graph's exclude resolution and symlink guard so vendored or build-artifact
    docs never count.
    """
    repo_root = repo_root.resolve()
    tracked = tracked_files(repo_root)
    found: list[Path] = []
    for path in repo_root.rglob("*"):
        if not path.is_file() or path.suffix.lower() != ".md":
            continue
        try:
            rel = path.relative_to(repo_root)
        except ValueError:
            continue
        if is_excluded_path(rel):
            continue
        if not is_repo_file(path, repo_root, tracked):
            continue
        name = path.name.lower()
        if name in ARCHITECTURE_BASENAMES:
            found.append(path)
        elif name in README_BASENAMES and _readme_declares_boundaries(path):
            found.append(path)
    return sorted(found)


def _readme_declares_boundaries(path: Path) -> bool:
    """True if a README's prose carries the module-ownership/seam vocabulary."""
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return False
    return _declares_boundaries(text)


def _declares_boundaries(text: str) -> bool:
    """True if ``text`` reads as a module-ownership / seam declaration."""
    return bool(_BOUNDARY_VOCAB_RE.search(text))


def _extract_path_refs(segment: str) -> set[str]:
    """All path references in a prose segment: inline code, wikilinks, bare paths.

    Reads code spans rather than stripping them (the opposite of the doc graph),
    because a path written as code is exactly the boundary declaration we want.
    Returns raw, repo-relative-looking path strings; resolution to real files is
    the caller's job.
    """
    refs: set[str] = set()
    for m in _INLINE_CODE_RE.finditer(segment):
        token = _strip_anchor(m.group(1))
        if "/" in token or token.endswith(".md") or token.endswith(".py"):
            refs.add(token.rstrip("/.,;:)"))
    for m in _WIKILINK_RE.finditer(segment):
        token = _strip_anchor(m.group(1))
        if token:
            refs.add(token.rstrip("/.,;:)"))
    # Bare paths in plain prose, but not inside the code spans we already read
    # (those are caught above with cleaner boundaries).
    defenced = _INLINE_CODE_RE.sub(" ", segment)
    for m in _BARE_PATH_RE.finditer(defenced):
        token = _strip_anchor(m.group(1))
        refs.add(token.rstrip("/.,;:)"))
    return {r for r in refs if r}


def _resolve_ref(
    ref: str, repo_root: Path, rel_paths: set[str],
) -> set[Path]:
    """Resolve a declared path reference to the tracked files it names.

    A reference can be an exact file (``lib/doc_graph.py``), a directory whose
    every file is claimed (``skills/assess/scripts``), or a bare note name
    (``doc_graph.py``) matched by basename. Returns the set of repo-relative
    file paths it resolves to; empty when it matches nothing tracked.
    """
    norm = ref.lstrip("/").rstrip("/")
    if not norm:
        return set()
    # Exact tracked file.
    if norm in rel_paths:
        return {Path(norm)}
    # Directory prefix: claim every tracked file beneath it.
    prefix = norm + "/"
    under = {Path(rp) for rp in rel_paths if rp.startswith(prefix)}
    if under:
        return under
    # Bare basename: match any tracked file with this name.
    if "/" not in norm:
        by_name = {Path(rp) for rp in rel_paths if Path(rp).name == norm}
        if by_name:
            return by_name
    return set()


def parse_architecture_md(repo_root: Path) -> dict[str, set[Path]]:
    """Parse boundary-declaring docs into ``{declared_module: {file_paths}}``.

    Walks each architecture doc (``ARCHITECTURE.md`` / ``DESIGN.md`` / a
    boundary-declaring ``README.md``), splits it into sections by markdown
    header, and attributes every path reference in a section's body to the
    module the header names. Path references are read from inline code,
    wikilinks, and bare prose paths, then resolved against the tracked file set;
    references that resolve to nothing are dropped (a declared module keeps only
    its real files).

    The declared-module key is namespaced by the doc that declares it
    (``<rel-doc>::<header>``) so two docs declaring a "Module reference" section
    don't collide. Degrades to an empty dict when no boundary doc exists. A doc
    that fails to read is skipped with a warning; the rest still parse.
    """
    repo_root = repo_root.resolve()
    docs = _discover_arch_docs(repo_root)
    if not docs:
        return {}

    tracked = tracked_files(repo_root)
    rel_paths = set(_tracked_rel_paths(repo_root, tracked))

    out: dict[str, set[Path]] = {}
    for doc in docs:
        try:
            text = doc.read_text(encoding="utf-8", errors="ignore")
        except OSError as exc:
            _warn(f"could not read {doc}: {exc}")
            continue
        doc_rel = doc.relative_to(repo_root).as_posix()
        _parse_one_arch_doc(text, doc_rel, repo_root, rel_paths, out)
    return out


def _parse_one_arch_doc(
    text: str,
    doc_rel: str,
    repo_root: Path,
    rel_paths: set[str],
    out: dict[str, set[Path]],
) -> None:
    """Attribute one doc's path references to the modules its headers name.

    Mutates ``out`` in place, keying each declared module ``<doc_rel>::<header>``
    and unioning the files its section's references resolve to. References before
    the first header are attributed to the doc itself (``<doc_rel>::<doc>``).
    """
    # Strip fenced code blocks: a fence is a sample/listing, and the bare-path
    # regex over its contents would manufacture spurious module->file edges.
    body = _FENCE_RE.sub("\n", text)
    current = f"{doc_rel}::{Path(doc_rel).name}"
    buffer: list[str] = []

    def flush(section_key: str, lines: list[str]) -> None:
        if not lines:
            return
        segment = "\n".join(lines)
        files: set[Path] = set()
        for ref in _extract_path_refs(segment):
            files |= _resolve_ref(ref, repo_root, rel_paths)
        if files:
            out.setdefault(section_key, set()).update(files)

    for line in body.splitlines():
        m = _HEADER_RE.match(line)
        if m:
            flush(current, buffer)
            buffer = []
            header_text = m.group(2).strip()
            current = f"{doc_rel}::{header_text}"
        else:
            buffer.append(line)
    flush(current, buffer)


def is_glob(pattern: str) -> bool:
    """True if a CODEOWNERS pattern is a wildcard rather than a literal path."""
    return any(c in _GLOB_CHARS for c in pattern) or pattern.endswith("/")


def find_empty_globs(ownership_map: dict[str, set[Path]]) -> list[dict]:
    """CODEOWNERS patterns that match zero tracked files.

    An empty glob is the cheapest structure-drift signal: a declared boundary
    the filesystem has already left behind (a renamed directory, a deleted
    module, a typo'd pattern). Returns ``[{pattern, declared_in}]`` sorted by
    pattern for deterministic output. ``declared_in`` is the fixed source
    ``"CODEOWNERS"`` - the only producer of these glob keys.
    """
    empties = [
        {"pattern": pattern, "declared_in": "CODEOWNERS"}
        for pattern, files in ownership_map.items()
        if not files
    ]
    return sorted(empties, key=lambda e: e["pattern"])


def parse_ownership(repo_root: Path) -> dict:
    """Combined ownership parse with the module-shape degradation contract.

    Runs both parsers and the empty-glob detector and returns a JSON-serialisable
    summary mirroring ``doc_graph.py``'s ``available``/``reason`` shape: when no
    ownership map of either kind is found, ``available`` is False with reason
    ``"no ownership map"``. Every collection is sorted at the boundary (sets ->
    sorted lists) so the same repo yields byte-identical output.
    """
    repo_root = repo_root.resolve()
    codeowners = parse_codeowners(repo_root)
    architecture = parse_architecture_md(repo_root)

    if not codeowners and not architecture:
        return {
            "available": False,
            "reason": "no ownership map",
            "codeowners_globs": [],
            "architecture_modules": [],
            "empty_globs": [],
        }

    empty = find_empty_globs(codeowners)
    return {
        "available": True,
        "reason": "",
        "codeowners_globs": [
            {"pattern": pattern, "matched_files": sorted(str(p) for p in files)}
            for pattern, files in sorted(codeowners.items())
        ],
        "architecture_modules": [
            {"module": module, "files": sorted(str(p) for p in files)}
            for module, files in sorted(architecture.items())
        ],
        "empty_globs": empty,
    }
