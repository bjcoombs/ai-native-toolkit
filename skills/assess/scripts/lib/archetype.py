"""Repository archetype detection for /assess.

The 0-8 layered model assumes a software repo: read-side foundation (L0-L1),
write-side enforcement (L2-L7), and a meta capstone (L8). For a **knowledge /
document base** - markdown sources, an LLM-maintained wiki, a ``CLAUDE.md``
schema, and no application code or runtime - the write-side layers have no code
surface to enforce. They are *not applicable*, not *failing*. Leaving them in
the denominator makes a well-run KB score ~2.5/8 and read as "Not Ready", which
is a lying score: it penalises the repo for not having tests on code it doesn't
contain.

This module turns "what kind of repo is this?" into a deterministic signal so
the scorer can mark the inapplicable layers **N/A** (excluded from the
denominator) rather than **Missing** (a real gap). The headline then
renormalises over the layers that actually apply.

Detection is **dispatch-friendly**: ``classify_archetype`` evaluates one
archetype today (knowledge-base) and falls through to ``software``. Adding a
further archetype later means adding a branch, not rebuilding the scaffolding -
but we deliberately ship one archetype now (YAGNI) rather than a general
framework.

Two override paths keep the heuristic honest:

- an explicit ``<!-- assess-archetype: knowledge-base -->`` (or ``software``)
  marker in any instruction file **forces or suppresses** detection, so a
  maintainer is never trapped by a misfire.
- the heuristic itself gates on the code-file ratio **and** the absence of a
  runtime surface (``package.json``, ``pyproject.toml``, ``go.mod``,
  ``Dockerfile``, ...), so a documentation-heavy *application* (lots of
  markdown but a real build) is never mistaken for a KB.

A documented **AI maintenance workflow** - the Karpathy LLM-wiki pattern
(immutable raw sources, the schema file as the product, an ingest workflow,
query-as-filing, periodic lint/consolidation) - is both a detection signal and
a scored read-side (Layer 0) quality signal. Best-practice pointer:
https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

# Imported from doc_graph (a lib module - the inward-only layering allows it).
from lib.doc_graph import CODE_EXTENSIONS, DOC_EXTENSIONS, EXCLUDE_DIRS
from lib.git_churn import tracked_files

# The Karpathy LLM-wiki best-practice pointer. Surfaced in the report so a KB
# maintainer has the canonical reference for the pattern being scored.
KARPATHY_GIST_URL = (
    "https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f"
)

# Layer bands of the 0-8 model. The write-side band has no code surface in a
# knowledge base, so those layers go N/A there.
ALL_LAYERS: list[int] = list(range(0, 9))  # 0..8
WRITE_SIDE_LAYERS: list[int] = [2, 3, 4, 5, 6, 7]
KB_APPLICABLE_LAYERS: list[int] = [0, 1, 8]  # read-side foundation + meta

# The software display denominator caps at 8 (nine layers, ceiling 8 - see
# assess-findings score derivation). A knowledge base renormalises over only
# its applicable layers.
SOFTWARE_DENOMINATOR = 8

# Heuristic thresholds. Conservative by design: a repo is only called a
# knowledge base when code is a *tiny* fraction AND there is no runtime surface.
# A typical software repo with a docs/ tree stays well above the ratio; a
# documentation-heavy app is caught by the runtime-surface gate.
KB_CODE_RATIO_MAX = 0.10  # code / (code + docs) must be at or below this
KB_MIN_DOC_FILES = 3  # need a real doc base, not an empty repo

# Marker filenames whose presence at the repo root signals an application
# runtime / build surface (so the repo is software even if doc-heavy). Globs
# are matched against the basename; plain names are matched exactly.
RUNTIME_SURFACE_MARKERS: tuple[str, ...] = (
    "package.json", "pyproject.toml", "setup.py", "setup.cfg", "go.mod",
    "Cargo.toml", "pom.xml", "build.gradle", "build.gradle.kts", "Gemfile",
    "composer.json", "Dockerfile", "requirements.txt", "tsconfig.json",
    "pubspec.yaml", "*.csproj", "*.sln", "*.cabal", "mix.exs",
)

# Instruction files an override marker / maintenance text may live in.
_INSTRUCTION_FILES: tuple[str, ...] = (
    "CLAUDE.md", "AGENTS.md", "GEMINI.md", ".cursorrules",
    ".github/copilot-instructions.md",
)

# An override marker in any instruction file. Tolerant of surrounding HTML
# comment syntax and whitespace: `<!-- assess-archetype: knowledge-base -->`,
# `assess-archetype: software`, etc.
_OVERRIDE_RE = re.compile(
    r"assess-archetype\s*[:=]\s*([a-z][a-z0-9-]*)", re.IGNORECASE
)

# Normalisation of override values to the two archetypes we support.
_OVERRIDE_ALIASES = {
    "knowledge-base": "knowledge-base",
    "knowledgebase": "knowledge-base",
    "kb": "knowledge-base",
    "wiki": "knowledge-base",
    "software": "software",
    "code": "software",
    "app": "software",
}

# Karpathy LLM-wiki maintenance signals. Each key is a named facet of the
# pattern; the value is a list of regexes any of which marks the facet present.
# Kept reasonably specific so a stray word doesn't trip a facet.
_KB_MAINTENANCE_SIGNALS: dict[str, list[str]] = {
    "immutable-sources": [
        r"immutable\s+(raw\s+)?sources?",
        r"raw\s+sources?\s+are\s+(immutable|append-only|never\s+edit)",
        r"append-only",
        r"never\s+(edit|modify)\s+(the\s+)?(raw\s+)?sources?",
    ],
    "schema-as-product": [
        r"schema\s+(file\s+)?is\s+the\s+product",
        r"the\s+product\s+is\s+the\s+schema",
        r"schema\s+file\s+as\s+the\s+product",
        r"schema-as-product",
    ],
    "ingest-workflow": [
        r"ingest(ion)?\s+(workflow|pipeline|process|step)",
        r"intake\s+(workflow|process)",
        r"how\s+(the\s+)?(ai|agent|llm)\s+(ingests|maintains|updates)",
    ],
    "query-as-filing": [
        r"query[- ]as[- ]filing",
        r"file\s+(it|the\s+answer)\s+(back\s+)?into",
        r"queries?\s+(get|are)\s+filed",
    ],
    "periodic-consolidation": [
        r"periodic\s+(lint|review|consolidat|compaction)",
        r"consolidat(e|ion)\s+(pass|step|workflow)",
        r"lint\s+(pass|the\s+wiki|the\s+knowledge)",
        r"compaction|garbage[- ]collect",
    ],
}


def _scan_override(repo_root: Path) -> tuple[str | None, str | None]:
    """Return ``(normalised_archetype, source_rel_path)`` for the first marker.

    Scans the known instruction files for ``assess-archetype: <value>`` and
    normalises the value to ``knowledge-base`` or ``software``, also carrying the
    repo-relative path of the file the marker was found in so a contradiction
    finding can point at it. Both are ``None`` when no recognised marker exists.
    """
    for rel in _INSTRUCTION_FILES:
        path = repo_root / rel
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        m = _OVERRIDE_RE.search(text)
        if not m:
            continue
        normalised = _OVERRIDE_ALIASES.get(m.group(1).strip().lower())
        if normalised:
            return normalised, rel
    return None, None


def read_archetype_override(repo_root: Path) -> str | None:
    """Return the forced archetype from an instruction-file marker, or None.

    Scans the known instruction files for ``assess-archetype: <value>`` and
    normalises the value to ``knowledge-base`` or ``software``. A
    ``knowledge-base`` marker *forces* the KB archetype; a ``software`` marker
    *suppresses* it. An unrecognised value is ignored (returns None) so a typo
    falls back to the heuristic rather than silently mis-scoring.
    """
    return _scan_override(repo_root)[0]


def detect_kb_maintenance(text: str) -> dict[str, Any]:
    """Detect a documented AI KB-maintenance workflow (Karpathy LLM-wiki).

    Scans combined instruction / doc text for the named facets of the pattern.
    A facet counts once. ``documented`` is True when the gist is cited directly
    or at least two distinct facets appear - two independent facets are a real
    description of a maintenance loop, a single keyword is not.

    Returns the gist pointer unconditionally so the report can cite the
    best-practice reference whether or not the repo documents the pattern.
    """
    lowered = text.lower()
    signals_found: list[str] = []
    for facet, patterns in _KB_MAINTENANCE_SIGNALS.items():
        if any(re.search(p, lowered) for p in patterns):
            signals_found.append(facet)

    gist_cited = (
        "karpathy/442a6bf555914893e9891c11519de94f" in lowered
        or bool(re.search(r"karpathy.{0,40}(llm\s+wiki|wiki)", lowered))
    )
    documented = gist_cited or len(signals_found) >= 2
    return {
        "documented": documented,
        "signals_found": signals_found,
        "gist_cited": gist_cited,
        "gist": KARPATHY_GIST_URL,
    }


def _matches_marker(basename: str, marker: str) -> bool:
    if "*" in marker or "?" in marker:
        from fnmatch import fnmatch

        return fnmatch(basename, marker)
    return basename == marker


def _has_runtime_surface(repo_root: Path) -> bool:
    """True when a root-level marker indicates an application build/runtime.

    Only the repo root is inspected: a build manifest nested deep in a docs
    example is not the repo's own runtime surface.
    """
    try:
        entries = list(repo_root.iterdir())
    except OSError:
        return False
    for entry in entries:
        if not entry.is_file():
            continue
        if any(_matches_marker(entry.name, m) for m in RUNTIME_SURFACE_MARKERS):
            return True
    return False


def _classify_files(repo_root: Path) -> tuple[int, int, int]:
    """Count (code, doc, other) files, honouring git tracking + excludes.

    Prefers git-tracked files (the precise "files in the repo"); falls back to
    a filesystem walk when the root isn't a git repo. Excluded directories
    (``.git``, ``node_modules``, ``.assess``, ...) never count.
    """
    tracked = tracked_files(repo_root)
    if tracked is not None:
        paths = [p for p in tracked if _under_repo(p, repo_root)]
    else:
        paths = [p for p in repo_root.rglob("*") if p.is_file()]

    code = doc = other = 0
    for path in paths:
        if _is_excluded(path, repo_root):
            continue
        suffix = path.suffix.lower()
        if suffix in CODE_EXTENSIONS:
            code += 1
        elif suffix in DOC_EXTENSIONS:
            doc += 1
        else:
            other += 1
    return code, doc, other


def _under_repo(path: Path, repo_root: Path) -> bool:
    try:
        path.resolve().relative_to(repo_root.resolve())
        return True
    except ValueError:
        return False


def _is_excluded(path: Path, repo_root: Path) -> bool:
    try:
        rel = path.resolve().relative_to(repo_root.resolve())
    except ValueError:
        return True
    return any(part in EXCLUDE_DIRS for part in rel.parts)


def classify_archetype(
    *,
    code_file_count: int,
    doc_file_count: int,
    other_file_count: int,
    has_runtime_surface: bool,
    override: str | None,
    kb_maintenance: dict[str, Any],
) -> dict[str, Any]:
    """Pure archetype classification from already-gathered signals.

    The IO-free core: ``analyze_archetype`` gathers the inputs and delegates
    here, so this is the unit the tests pin. Returns the ``archetype`` block
    shape written to ``run-context.json``.
    """
    content_total = code_file_count + doc_file_count
    code_ratio = (code_file_count / content_total) if content_total else 0.0

    # What the deterministic heuristic would have concluded on its own signals -
    # computed unconditionally so an override can be checked against it and any
    # contradiction surfaced (the override still wins, but never silently).
    heuristic_is_kb = (
        doc_file_count >= KB_MIN_DOC_FILES
        and code_ratio <= KB_CODE_RATIO_MAX
        and not has_runtime_surface
    )
    heuristic_archetype = "knowledge-base" if heuristic_is_kb else "software"

    if override == "knowledge-base":
        archetype, detected_via = "knowledge-base", "override"
        reason = "forced by an `assess-archetype: knowledge-base` marker"
    elif override == "software":
        archetype, detected_via = "software", "override"
        reason = "forced by an `assess-archetype: software` marker"
    else:
        detected_via = "heuristic"
        is_kb = heuristic_is_kb
        archetype = heuristic_archetype
        if is_kb:
            reason = (
                f"code-file ratio {code_ratio:.2f} "
                f"({code_file_count} code / {doc_file_count} docs) at or below "
                f"{KB_CODE_RATIO_MAX:.2f} and no runtime surface detected"
            )
        elif has_runtime_surface:
            reason = (
                f"a runtime surface is present (code-file ratio {code_ratio:.2f}); "
                "scored as a software repo"
            )
        else:
            reason = (
                f"code-file ratio {code_ratio:.2f} "
                f"({code_file_count} code / {doc_file_count} docs) exceeds the "
                f"knowledge-base threshold {KB_CODE_RATIO_MAX:.2f}"
            )

    # An override wins the classification (denominator, na_layers, reason all
    # follow the forced archetype above), but when the deterministic signals
    # would have concluded differently the disagreement is emitted as a visible
    # finding. This is the guardrail-erosion tendency turned into a signal: a
    # marker that quietly overrides what the code actually looks like.
    override_contradicts_signals = (
        detected_via == "override" and archetype != heuristic_archetype
    )
    contradiction_details: str | None = None
    if override_contradicts_signals:
        contradiction_details = (
            f"Override forces {archetype}, but signals suggest "
            f"{heuristic_archetype}: code_ratio={code_ratio:.2f}, "
            f"has_runtime={has_runtime_surface}, doc_files={doc_file_count}, "
            f"code_files={code_file_count}"
        )

    if archetype == "knowledge-base":
        applicable = list(KB_APPLICABLE_LAYERS)
        na_layers = list(WRITE_SIDE_LAYERS)
        denominator = len(applicable)
    else:
        applicable = list(ALL_LAYERS)
        na_layers = []
        denominator = SOFTWARE_DENOMINATOR

    return {
        "available": True,
        "archetype": archetype,
        "detected_via": detected_via,
        "override": override,
        "override_contradicts_signals": override_contradicts_signals,
        "contradiction_details": contradiction_details,
        "reason": reason,
        "signals": {
            "code_file_count": code_file_count,
            "doc_file_count": doc_file_count,
            "other_file_count": other_file_count,
            "code_file_ratio": round(code_ratio, 3),
            "has_runtime_surface": has_runtime_surface,
        },
        "applicable_layers": applicable,
        "na_layers": na_layers,
        "denominator": denominator,
        "kb_maintenance": kb_maintenance,
    }


def _gather_instruction_text(repo_root: Path) -> str:
    parts: list[str] = []
    for rel in _INSTRUCTION_FILES:
        path = repo_root / rel
        try:
            parts.append(path.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
    return "\n".join(parts)


def analyze_archetype(repo_root: Path) -> dict[str, Any]:
    """Detect the repository archetype and assemble the run-context block.

    Side-effect-free with respect to the repo (read-only). Gathers the file
    counts, runtime-surface signal, override marker, and KB-maintenance signal,
    then delegates the verdict to ``classify_archetype``.
    """
    code, doc, other = _classify_files(repo_root)
    override, override_source = _scan_override(repo_root)
    kb_maintenance = detect_kb_maintenance(_gather_instruction_text(repo_root))
    has_runtime = _has_runtime_surface(repo_root)
    block = classify_archetype(
        code_file_count=code,
        doc_file_count=doc,
        other_file_count=other,
        has_runtime_surface=has_runtime,
        override=override,
        kb_maintenance=kb_maintenance,
    )
    # The marker source lets a contradiction finding point at the file the
    # override lives in; recorded whenever a recognised marker was found.
    if override_source is not None:
        block["override_source"] = override_source
    return block
