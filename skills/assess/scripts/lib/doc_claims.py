"""Check the claims the documentation makes, against the repo and against each other.

``instruction_claims`` verifies the enforcement, pin and count claims in the
graded agent instruction files. The same sentences turn up across a doc set,
which an agent reads just as literally: a guide that says "43 suites in
`tests/*.sql`" or "Node 20.11.0 is pinned in `.nvmrc`". This module runs the
same extractors and verifiers over the doc set (issue #491), and adds one
cross-document kind, still with no model:

- ``contradiction``: two files make the same checkable claim with different
  values - a different version pinned in the same file, or a different count
  for the same glob pattern. The finding lists every site of that claim
  (file, line, value), so both conflicting lines are cited. It does not need
  the repository to settle which value is right, so a claim the repository
  cannot verify (a pattern whose directory is gone) still takes part.

Like the per-file kinds, the cross-document kind is a pair of tables: an
extractor per claim kind in ``CROSS_DOC_VALUE`` (claim -> the value two files
can disagree on) and a verifier per kind in ``CROSS_DOC_AGREE`` (two values ->
whether they agree). Pins are grouped by pinned file, not by tool, so a shared
file such as ``.tool-versions`` can hold two different true pins: a pin finding
whose every version the file contains is dropped (``_reconciled``). Two pins agree when one version's dotted components are a
prefix of the other's (``3.11`` and ``3.11.9``, matching the lenient pin check;
``1.2`` and ``11.2`` do not). Two counts agree within the per-file tolerance
(``count_within_tolerance``: the larger of 10% or 2). An enforcement claim
carries no value, so it has no cross-document form.

Paths in a skill's ``SKILL.md`` or ``references/`` are skill-relative, so
their claims resolve against ``skills/<name>/`` (``rebase_skill_claims``),
falling back to the repo root only when just that reading exists; claims are
grouped by that resolved target.

Scope: the Markdown under ``docs/``, ``skills/*/references/`` and every
``skills/*/SKILL.md`` (from the ``--scope`` directory when one is set), minus
``HISTORICAL_DOC_TREES`` (design records, ADRs and RFCs: they describe past
intent and may legitimately disagree with current code), the built-in excluded
trees and the user's excludes; only files the repo tracks
(any file when it is not a git repo). The graded instruction files join the
cross-document comparison, since ``CLAUDE.md`` against a guide is the common
case, but are not re-verified here: ``instruction_claims`` reports those.
Rules that conflict in meaning ("always rebase" against "never rebase") are not
checkable claims and stay out of this deterministic core.
"""
from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import replace
from pathlib import Path
from typing import Any, NamedTuple, TypedDict

from lib.assess_config import is_user_excluded
from lib.doc_graph import is_excluded_path, is_repo_file
from lib.git_churn import tracked_files
from lib.instruction_claims import (
    Claim,
    count_within_tolerance,
    empty_block,
    extract_claims,
    has_ci_config,
    tally_claims,
)

# A claim value two files can disagree on: a pinned version or a claimed count.
ClaimValue = str | int


class DocClaimSite(TypedDict):
    """One file's statement of a claim: where, and the value it gives."""

    file: str
    line: int
    value: ClaimValue


class DocContradiction(TypedDict):
    """A claim two or more files make with values that do not agree."""

    kind: str
    path: str
    sites: list[DocClaimSite]


class DocClaimsBlock(TypedDict):
    """``run-context.json`` ``doc_claims``."""

    files_scanned: int
    excluded_trees: list[str]
    total: int
    verified: int
    failed: int
    failures: list[dict[str, Any]]
    contradictions_total: int
    contradictions: list[DocContradiction]


# Globs, from the repository root, of the documents whose claims are checked.
DOC_CLAIM_GLOBS = ("docs/**/*.md", "skills/*/references/**/*.md", "skills/*/SKILL.md")
# Records of past intent: they describe what was planned, not what is.
HISTORICAL_DOC_TREES = (
    "docs/design", "docs/superpowers", "docs/adr", "docs/adrs", "docs/decisions",
    "docs/rfcs", "docs/rfc",
)
# List caps; the counts beside each list carry the full total.
MAX_FAILURES = 50
MAX_CONTRADICTIONS = 25


def _version_parts(version: ClaimValue) -> tuple[str, ...]:
    return tuple(str(version).split("."))


def _versions_agree(a: ClaimValue, b: ClaimValue) -> bool:
    left, right = _version_parts(a), _version_parts(b)
    shorter = min(len(left), len(right))
    return left[:shorter] == right[:shorter]


def _counts_agree(a: ClaimValue, b: ClaimValue) -> bool:
    return count_within_tolerance(int(a), int(b))


CROSS_DOC_VALUE: dict[str, Callable[[Claim], ClaimValue]] = {
    "pin": lambda claim: claim.fields["version"],
    "count": lambda claim: claim.fields["claimed"],
}
CROSS_DOC_AGREE: dict[str, Callable[[ClaimValue, ClaimValue], bool]] = {
    "pin": _versions_agree,
    "count": _counts_agree,
}


class _Site(NamedTuple):
    file: str
    line: int
    value: ClaimValue
    real: Path


def _historical(rel: Path) -> bool:
    return any(rel.parts[:len(Path(tree).parts)] == Path(tree).parts
               for tree in HISTORICAL_DOC_TREES)


def doc_claim_files(
    repo_root: Path, excludes: tuple[set[str], list[str]] | None = None,
    scope: Path | None = None,
) -> list[str]:
    """Repo-relative paths of the documents in scope, sorted.

    The globs and the historical trees are matched from ``scope`` (the run's
    ``--scope`` directory, default the repo root); paths are reported, and the
    excludes matched, relative to the repo root.
    """
    root = repo_root.resolve()
    base = (scope or root).resolve()
    tracked = tracked_files(root)
    dirs, patterns = excludes or (set(), [])
    found: set[str] = set()
    for pattern in DOC_CLAIM_GLOBS:
        for path in base.glob(pattern):
            rel = path.relative_to(root)
            if (not path.is_file() or _historical(path.relative_to(base))
                    or is_excluded_path(rel)
                    or is_user_excluded(rel, dirs, patterns)
                    or not is_repo_file(path, root, tracked)):
                continue
            found.add(rel.as_posix())
    return sorted(found)


def _skill_dir(rel: str) -> str | None:
    """``skills/<name>`` (under any scope prefix) for a skill's ``SKILL.md`` or a
    file under its ``references/``; None for any other document."""
    parts = Path(rel).parts
    for i in range(len(parts) - 3, -1, -1):
        if parts[i] != "skills":
            continue
        rest = parts[i + 2:]
        if rest == ("SKILL.md",) or (len(rest) > 1 and rest[0] == "references"):
            return Path(*parts[:i + 2]).as_posix()
    return None


def _target_exists(root: Path, path: str) -> bool:
    """Whether the file a claim names, or a glob's wildcard-free directory, exists."""
    fixed: list[str] = []
    for part in Path(path).parts:
        if re.search(r"[*?]", part):
            break
        fixed.append(part)
    return bool(fixed) and root.joinpath(*fixed).exists()


def rebase_skill_claims(root: Path, rel: str, claims: list[Claim]) -> list[Claim]:
    """Resolve a skill document's claim paths against its skill directory.

    A ``SKILL.md`` or skill reference names ``references/*.md`` or
    ``scripts/x.sh`` relative to ``skills/<name>/``. Each claim path is rewritten
    to ``skills/<name>/<path>`` unless only the repo-root reading exists, so two
    skills' ``references/*.md`` are two different targets, verified and grouped
    apart. Other documents' claims are returned unchanged.
    """
    skill = _skill_dir(rel)
    if skill is None:
        return claims
    out: list[Claim] = []
    for claim in claims:
        local = f"{skill}/{claim.path}"
        if _target_exists(root, claim.path) and not _target_exists(root, local):
            out.append(claim)
        else:
            out.append(replace(claim, path=local))
    return out


def _read(root: Path, rel: str) -> tuple[Path, str] | None:
    candidate = root / rel
    try:
        return candidate.resolve(), candidate.read_text(encoding="utf-8")
    except (OSError, RuntimeError, UnicodeDecodeError):
        return None


def _contradiction(kind: str, path: str, sites: list[_Site]) -> DocContradiction | None:
    agree = CROSS_DOC_AGREE[kind]
    clash = any(a.real != b.real and not agree(a.value, b.value)
                for i, a in enumerate(sites) for b in sites[i + 1:])
    if not clash:
        return None
    ordered = sorted(sites, key=lambda s: (s.file, s.line))
    return {
        "kind": kind, "path": path,
        "sites": [DocClaimSite(file=s.file, line=s.line, value=s.value) for s in ordered],
    }


def find_contradictions(claims_by_file: Iterable[tuple[str, Path, list[Claim]]]) -> list[DocContradiction]:
    """One finding per (kind, path) whose files disagree, listing every site.

    ``claims_by_file`` holds (repo-relative path, resolved path, claims); two
    entries with the same resolved path are one file, and a file that
    disagrees only with itself is not a cross-document contradiction.
    """
    groups: dict[tuple[str, str], list[_Site]] = {}
    for rel, real, claims in claims_by_file:
        for claim in claims:
            value_of = CROSS_DOC_VALUE.get(claim.kind)
            if value_of is not None:
                groups.setdefault((claim.kind, claim.path), []).append(
                    _Site(rel, claim.line, value_of(claim), real))
    found = (_contradiction(kind, path, sites) for (kind, path), sites in sorted(groups.items()))
    return [c for c in found if c is not None]


def _reconciled(root: Path, contradiction: DocContradiction) -> bool:
    """True when the repo shows every value of a pin finding is true.

    A shared pin file (``.tool-versions``) holds one version per tool, so
    "Node 20.11.0" and "Python 3.12.1" pinned in it can both hold: a group of
    pins the file contains every version of is not a disagreement.
    """
    if contradiction["kind"] != "pin":
        return False
    pinned = root / contradiction["path"]
    try:
        if not pinned.resolve().is_relative_to(root.resolve()):
            return False
        text = pinned.read_text(encoding="utf-8")
    except (OSError, RuntimeError, UnicodeDecodeError):
        return False
    return all(_holds_version(text, str(site["value"])) for site in contradiction["sites"])


def _holds_version(text: str, version: str) -> bool:
    """True when ``version`` occurs as a whole version or its leading components:
    ``3.11`` is held by ``3.11.9``, but ``1.2`` is not held by ``11.2.0`` and
    ``20.1`` is not held by ``20.11.0``."""
    return re.search(rf"(?<![\d.]){re.escape(version)}(?!\d)", text) is not None


def scan_doc_claims(
    repo_root: Path | str,
    instruction_files: Iterable[str],
    scope: Path | None = None,
    excludes: tuple[set[str], list[str]] | None = None,
) -> DocClaimsBlock:
    """Verify the doc set's claims and find claims two files disagree on.

    Returns ``files_scanned`` (documents verified), the ``instruction_claims``
    counters and ``failures`` for those documents (``failures`` capped at
    ``MAX_FAILURES``; ``failed`` is the full count), and ``contradictions``
    (capped at ``MAX_CONTRADICTIONS``; ``contradictions_total`` is the full
    count), each ``{kind, path, sites[{file, line, value}]}``.
    """
    root = Path(repo_root)
    docs = doc_claim_files(root, excludes, scope)
    block = empty_block()
    ci_configured = has_ci_config(root)
    pool: list[tuple[str, Path, list[Claim]]] = []
    seen: set[Path] = set()
    scanned = 0
    # Instruction files first: a document that is also one (``docs/CLAUDE.md``,
    # or a doc symlinked to ``CLAUDE.md``) joins the comparison once and is left
    # to ``instruction_claims`` for verification.
    sources = [(rel, False) for rel in instruction_files] + [(rel, True) for rel in docs]
    for rel, verify in sources:
        read = _read(root, rel)
        if read is None or read[0] in seen:
            continue
        real, text = read
        seen.add(real)
        claims = extract_claims(text)
        if verify:
            claims = rebase_skill_claims(root, rel, claims)
        pool.append((rel, real, claims))
        if verify:
            scanned += 1
            tally_claims(block, root, rel, claims, ci_configured)
    contradictions = [c for c in find_contradictions(pool) if not _reconciled(root, c)]
    return {
        "files_scanned": scanned,
        "excluded_trees": list(HISTORICAL_DOC_TREES),
        "total": block["total"],
        "verified": block["verified"],
        "failed": block["failed"],
        "failures": block["failures"][:MAX_FAILURES],
        "contradictions_total": len(contradictions),
        "contradictions": contradictions[:MAX_CONTRADICTIONS],
    }
