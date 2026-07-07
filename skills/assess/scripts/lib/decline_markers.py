"""Read decline markers (``.assess/.no-<tool>``) with provenance.

A decline marker records that a user permanently declined an optional tool for
this repo - ``scc`` (coverage-extension for the treemap), a per-language
dead-code linter (``vulture`` / ``ts-prune`` / ``staticcheck``), or the bounded
mutation pass (``mutmut`` / ``stryker``). Historically each marker was an empty
``touch``-ed file: present-or-absent was the entire signal. That made the
decline a silent, provenance-free fact - the report could not disclose *who*
declined *what* and *when*, and a decline made against an old major version
outlived the tool changes that might have made it worth re-offering.

Markers are now JSON with provenance::

    {"declined_by": "ben", "declined_at": "2026-07-07",
     "plugin_version": "1.54.4", "reason": "pure-docs repo"}

``reason`` is optional. Legacy empty / non-JSON markers are still honoured as a
decline - they just carry no provenance (``declined_by`` / ``declined_at`` /
``version`` are ``None``), so they read as "declined by an unknown user on an
unknown date" rather than crashing the scan.

**Re-offer on a major bump.** A marker written under an older *major* plugin
version is stale enough that the tool's behaviour may have changed materially
since the user declined. Such a marker carries ``reoffer: True`` so the
orchestrator can re-ask once. When the user declines again permanently, the new
marker is stamped with the *current* version - its major now matches, so
``reoffer`` drops back to ``False`` and the re-offer does not repeat every run
within the same major (re-offer once per major). Legacy markers carry no
version, so they are never auto-re-offered (there is no major to compare).

The ``reoffer`` field is a pure staleness signal, set for *any* tool declined
under an older major - but only mutation tools are actually re-offered (SKILL.md
Step 2d); Step 2b never re-asks a linter decline. So the report's
"re-offer eligible" disclosure suffix is gated on ``MUTATION_TOOLS`` (see
:func:`_disclosure`), and ``reoffer_mutation`` keys off the same set - a stale
linter marker keeps ``reoffer: True`` for its own record without claiming a
re-offer that never comes.

Pure stdlib. Never raises out of :func:`read_decline_markers`.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

MARKER_PREFIX = ".no-"

# Tools whose decline gates the consent-heavy bounded mutation pass. A stale
# decline for one of these is what ``reoffer_mutation`` keys off.
MUTATION_TOOLS = frozenset({"mutmut", "stryker"})


@dataclass
class DeclineMarker:
    """One parsed ``.no-<tool>`` marker."""

    path: str  # repo-root-relative, e.g. ".assess/.no-mutmut"
    tool: str  # e.g. "mutmut"
    declined_by: str | None
    declined_at: str | None
    version: str | None  # plugin_version recorded in the marker
    reason: str | None
    reoffer: bool  # marker major < current major (never True for legacy markers)

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "tool": self.tool,
            "declined_by": self.declined_by,
            "declined_at": self.declined_at,
            "version": self.version,
            "reason": self.reason,
            "reoffer": self.reoffer,
        }


def _major(version: str | None) -> int | None:
    """Extract the integer major component of a semver string, else None."""
    if not version:
        return None
    head = str(version).strip().lstrip("vV").split(".", 1)[0]
    try:
        return int(head)
    except ValueError:
        return None


def _parse_marker(path: Path, current_version: str) -> DeclineMarker:
    tool = path.name[len(MARKER_PREFIX):]
    declined_by: str | None = None
    declined_at: str | None = None
    version: str | None = None
    reason: str | None = None
    try:
        raw = path.read_text(encoding="utf-8").strip()
    except OSError:
        raw = ""
    if raw:
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            data = None
        if isinstance(data, dict):
            declined_by = data.get("declined_by") or None
            declined_at = data.get("declined_at") or None
            version = data.get("plugin_version") or None
            reason = data.get("reason") or None

    marker_major = _major(version)
    current_major = _major(current_version)
    # Re-offer only when both majors are known and the marker's is older. A
    # legacy marker (no version) has no major, so it is never auto-re-offered.
    reoffer = (
        marker_major is not None
        and current_major is not None
        and marker_major < current_major
    )
    return DeclineMarker(
        path=path.name,
        tool=tool,
        declined_by=declined_by,
        declined_at=declined_at,
        version=version,
        reason=reason,
        reoffer=reoffer,
    )


def read_decline_markers(
    assess_dir: Path, current_version: str
) -> list[DeclineMarker]:
    """Scan ``assess_dir`` for ``.no-<tool>`` markers and parse each.

    Returns markers sorted by tool for a stable, byte-reproducible run-context.
    A missing or unreadable ``.assess`` directory yields an empty list; never
    raises.
    """
    try:
        if not assess_dir.is_dir():
            return []
        candidates = sorted(
            p
            for p in assess_dir.iterdir()
            if p.is_file()
            and p.name.startswith(MARKER_PREFIX)
            and len(p.name) > len(MARKER_PREFIX)
        )
    except OSError:
        return []
    markers = [_parse_marker(p, current_version) for p in candidates]
    markers.sort(key=lambda m: m.tool)
    return markers


def build_decline_block(
    assess_dir: Path, current_version: str
) -> dict[str, Any]:
    """Build the run-context ``decline_markers`` block plus derived flags.

    Returns a dict with:

    - ``markers``: list of per-marker provenance dicts.
    - ``reoffer_mutation``: True when a mutation-tool decline (``mutmut`` /
      ``stryker``) was written under an older major - the SKILL.md Step 2d
      re-offer flag.
    - ``disclosures``: human-readable one-liners the report surfaces so an
      active permanent decline is never invisible.
    """
    markers = read_decline_markers(assess_dir, current_version)
    reoffer_mutation = any(
        m.tool in MUTATION_TOOLS and m.reoffer for m in markers
    )
    disclosures = [_disclosure(m) for m in markers]
    return {
        "markers": [m.to_dict() for m in markers],
        "reoffer_mutation": reoffer_mutation,
        "disclosures": disclosures,
    }


def _disclosure(m: DeclineMarker) -> str:
    """One-line report disclosure of an active decline marker."""
    who = m.declined_by or "an unknown user"
    when = m.declined_at or "an unknown date"
    label = _tool_label(m.tool)
    line = f"{label} permanently declined by {who} on {when}"
    if m.version:
        line += f" (plugin v{m.version})"
    # Only mutation tools are actually re-offered (SKILL.md Step 2d). A linter
    # decline (scc, vulture, ...) carries reoffer=True too when it predates a
    # major bump, but Step 2b never consults it - so gate the suffix on
    # MUTATION_TOOLS, else a stale linter marker claims a re-offer that never
    # comes.
    if m.reoffer and m.tool in MUTATION_TOOLS:
        line += " - re-offer eligible (declined under an older major)"
    return line


def _tool_label(tool: str) -> str:
    if tool in MUTATION_TOOLS:
        return "Mutation testing"
    if tool == "scc":
        return "scc coverage extension"
    return f"Dead-code analysis (`{tool}`)"
