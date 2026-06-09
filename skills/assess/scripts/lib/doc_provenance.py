"""Provenance-aware staleness for *generated* docs.

For a hand-written doc, staleness is "the code moved while the doc sat still" -
the churn-ratio signal in ``lib.doc_staleness``. For a **generated** doc (a Jira
note dump, an API reference, codegen output) that measure is the wrong one:

  - A generated doc is *fresh* when it matches the data it was derived from and
    *stale* when its **source** has moved on - regardless of the doc file's
    wall-clock age.
  - A freshly regenerated dump of ~1,200 notes shares one recent mtime (so it
    looks fresh) even when its source changed afterwards.
  - An old-but-still-accurate generated doc reads as a "lying map" under the age
    model when it is not one.

The meaningful signal is simply: **a generated doc is stale iff its source is
newer than the doc.**

A doc declares provenance two ways; frontmatter wins over config when both name
a source for the same doc:

1. **YAML frontmatter** ``source:`` (a string or a list) - the file(s) the doc
   derives from, resolved relative to the repo root first, then to the doc's own
   directory. An optional ``generated_by:`` names the generator command/script
   for humans; it is recorded but does not affect staleness.

       ---
       source: data/jira.tsv
       generated_by: scripts/dump-jira-notes.py
       ---

2. **`.assess/config.toml`** ``[[generated]]`` array-of-tables mapping a folder
   to its source(s), for bulk-generated trees that cannot each carry frontmatter:

       [[generated]]
       path = "notes"
       source = "data/jira.tsv"

   Every doc whose path is under ``notes/`` inherits that source. ``source`` may
   be a string or a list of strings, resolved relative to the repo root.

When provenance resolves, ``lib.doc_staleness`` computes ``source_newer`` (is any
source's last change more recent than the doc's?) and ``lib.doc_complexity_join``
reads that flag to sign freshness directly (+1 when the source has not moved,
-1 when it has) instead of the churn ratio - so a generated doc whose source is
quiet is never classified as a ``lying_map``.

This module is **standalone**: it reads frontmatter and last-change timestamps,
and never imports an orchestrator (the inward-only contract). Timestamps come
from ``lib.git_churn`` (git commit time when tracked) and fall back to the
filesystem mtime, so both the doc and its source are compared on the same axis
(epoch seconds).
"""
from __future__ import annotations

from pathlib import Path

from lib.git_churn import file_last_commit_epoch

# A YAML frontmatter block is fenced by `---` lines at the very top of the file.
_FENCE = "---"
# Keys we read from frontmatter. Everything else is ignored.
_SOURCE_KEY = "source"
_GENERATED_BY_KEY = "generated_by"


def _read_head(doc: Path, max_bytes: int = 8192) -> str:
    """Read the first chunk of a doc - enough to hold any frontmatter block."""
    try:
        with doc.open("r", encoding="utf-8", errors="ignore") as fh:
            return fh.read(max_bytes)
    except OSError:
        return ""


def _strip_inline_comment(value: str) -> str:
    """Drop a trailing `# comment` and surrounding quotes from a scalar value."""
    # Only strip a comment that is clearly spaced off the value, to avoid eating
    # a `#fragment` that is part of a path/URL.
    if " #" in value:
        value = value.split(" #", 1)[0]
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1]
    return value.strip()


def _parse_scalar_or_flow_list(value: str) -> list[str]:
    """Parse a frontmatter scalar or inline `[a, b]` flow list into a string list."""
    value = value.strip()
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1]
        return [s for raw in inner.split(",") if (s := _strip_inline_comment(raw))]
    scalar = _strip_inline_comment(value)
    return [scalar] if scalar else []


def parse_frontmatter_provenance(doc: Path) -> tuple[list[str], str | None]:
    """Return ``(source_values, generated_by)`` declared in the doc's frontmatter.

    Supports the three YAML shapes a generator is likely to emit without needing
    a YAML dependency (the deterministic core ships none)::

        source: data/jira.tsv          # scalar
        source: [a.tsv, b.tsv]         # inline flow list
        source:                        # block list
          - a.tsv
          - b.tsv

    ``source_values`` are the raw declared strings (not yet resolved to paths);
    an empty list means "no provenance declared". A malformed or absent block
    degrades to ``([], None)`` - provenance is optional, never an error.
    """
    head = _read_head(doc)
    lines = head.splitlines()
    if not lines or lines[0].strip() != _FENCE:
        return [], None
    # Find the closing fence.
    end = None
    for i in range(1, len(lines)):
        if lines[i].strip() == _FENCE:
            end = i
            break
    if end is None:
        return [], None

    block = lines[1:end]
    sources: list[str] = []
    generated_by: str | None = None
    i = 0
    while i < len(block):
        line = block[i]
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or ":" not in stripped:
            i += 1
            continue
        key, _, rest = stripped.partition(":")
        key = key.strip().lower()
        rest = rest.strip()
        if key == _SOURCE_KEY:
            if rest:
                sources.extend(_parse_scalar_or_flow_list(rest))
                i += 1
            else:
                # Block list: consume following `- item` lines (any indentation).
                i += 1
                while i < len(block):
                    item = block[i].strip()
                    if item.startswith("- "):
                        val = _strip_inline_comment(item[2:])
                        if val:
                            sources.append(val)
                        i += 1
                    else:
                        break
        elif key == _GENERATED_BY_KEY:
            generated_by = _strip_inline_comment(rest) or None
            i += 1
        else:
            i += 1
    return sources, generated_by


def _resolve_source(raw: str, doc: Path, repo_root: Path) -> Path | None:
    """Resolve a declared source string to an existing file path.

    Tries repo-root-relative first (the documented primary rule), then relative
    to the doc's own directory. Returns ``None`` when neither exists, so a typo'd
    or moved source simply yields no provenance signal rather than a false one.
    """
    raw = raw.strip().lstrip("/")
    if not raw:
        return None
    for base in (repo_root, doc.parent):
        candidate = (base / raw).resolve()
        if candidate.is_file():
            return candidate
    return None


def resolve_doc_sources(
    doc: Path,
    repo_root: Path,
    config_map: list[tuple[str, list[str]]] | None = None,
) -> tuple[list[Path], str | None, str]:
    """Resolve a doc's provenance sources.

    Returns ``(resolved_source_paths, generated_by, method)`` where ``method`` is
    ``"frontmatter"``, ``"config"``, or ``""`` (no provenance). Frontmatter wins
    over the config mapping. ``config_map`` is a list of ``(path_prefix, sources)``
    from ``lib.assess_config.load_generated_sources``.
    """
    fm_sources, generated_by = parse_frontmatter_provenance(doc)
    if fm_sources:
        resolved = [p for raw in fm_sources if (p := _resolve_source(raw, doc, repo_root))]
        if resolved:
            return resolved, generated_by, "frontmatter"

    if config_map:
        try:
            rel = doc.resolve().relative_to(repo_root.resolve())
        except ValueError:
            rel = None
        if rel is not None:
            rel_posix = rel.as_posix()
            for prefix, sources in config_map:
                norm = prefix.strip("/")
                if rel_posix == norm or rel_posix.startswith(norm + "/"):
                    resolved = [
                        p for raw in sources
                        if (p := _resolve_source(raw, doc, repo_root))
                    ]
                    if resolved:
                        return resolved, generated_by, "config"
    return [], generated_by, ""


def _change_ts(path: Path) -> float | None:
    """Last-change time in epoch seconds: git commit time if tracked, else mtime.

    Both a doc and its source resolve through this one function, so they are
    always compared on the same axis even when one is committed and the other is
    a working-tree-only data file.
    """
    epoch = file_last_commit_epoch(path)
    if epoch is not None:
        return float(epoch)
    try:
        return path.stat().st_mtime
    except OSError:
        return None


def source_is_newer(doc: Path, sources: list[Path]) -> bool | None:
    """True if any source last changed more recently than the doc.

    ``None`` when the comparison cannot be made (the doc's or every source's
    timestamp is unavailable) - the caller then keeps the age/churn signal rather
    than inventing a verdict.
    """
    if not sources:
        return None
    doc_ts = _change_ts(doc)
    if doc_ts is None:
        return None
    source_times = [t for s in sources if (t := _change_ts(s)) is not None]
    if not source_times:
        return None
    return max(source_times) > doc_ts
