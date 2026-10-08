"""Decide whether the cross-run stats diff can be trusted.

Compares the plugin version, the stats schema version and each complexity
backend's version stamped on the prior and current ``complexity-stats.json``.
Stdlib only.
"""

from __future__ import annotations

import re
from typing import Any


def _parse_semver(value: str | None) -> tuple[int, int, int] | None:
    """Parse ``MAJOR.MINOR.PATCH`` (leading ``v`` and a pre-release/build
    suffix tolerated) into an int triple, or ``None`` when it isn't a semver.

    A small local parse rather than a ``packaging`` dependency: the deterministic
    core is stdlib-only by convention (its only deps are the graph libs), and the
    only comparison the diff needs is the major component and equality.
    """
    if not isinstance(value, str):
        return None
    m = re.match(r"^\s*v?(\d+)\.(\d+)\.(\d+)", value)
    if not m:
        return None
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


def _diff_is_reliable(
    prior_version: str | None,
    current_version: str | None,
    prior_schema: object | None,
    current_schema: object | None,
) -> tuple[bool, str | None]:
    """Decide whether a cross-run diff can be trusted, and why not if not.

    A diff is only trustworthy when both snapshots came from a comparable
    toolchain. This gates the plugin-version and stats-schema halves of that
    (tool-backend version changes are handled by the caller, which owns the
    per-tool notes). Ordering matches the failure severity:

    - prior snapshot never stamped a version -> unreliable (can't establish
      comparability at all);
    - either version unparseable -> unreliable (can't reason about the delta);
    - stats schema changed -> unreliable (the sidecar shape the diff reads moved);
    - MAJOR version changed -> unreliable AND a trend reset (breaking change to
      the deterministic core; prior history is not comparable);
    - only MINOR/PATCH moved -> reliable, trend and gate stay armed.

    Returns ``(reliable, note)``; ``note`` is ``None`` exactly when reliable.
    """
    if not prior_version:
        return False, "version not stamped in prior snapshot"
    pv = _parse_semver(prior_version)
    cv = _parse_semver(current_version)
    if pv is None or cv is None:
        return False, (
            f"unparseable plugin version (prior {prior_version!r}, "
            f"current {current_version!r})"
        )
    if prior_schema != current_schema:
        return False, f"schema version changed {prior_schema}->{current_schema}"
    if pv[0] != cv[0]:
        return False, f"major version changed {prior_version}->{current_version}"
    return True, None


_NON_TOOL_VERSION_KEYS = frozenset(
    {"schema_version", "artifact_schema_version", "plugin_version"})


def stats_tool_versions(stats: dict[str, Any] | None) -> dict[str, str]:
    """Extract the ``{tool: version}`` map a stats sidecar stamped.

    Reads every flat ``<tool>_version`` key the treemap writes (``lizard_version``,
    ``scc_version``, and any per-function backend added later), skipping the
    layout and plugin stamps. A tool absent from a pre-stamping snapshot is
    simply omitted - it can't be compared, so it never forces a false reset."""
    if not isinstance(stats, dict):
        return {}
    out: dict[str, str] = {}
    for key, v in stats.items():
        if (key.endswith("_version") and key not in _NON_TOOL_VERSION_KEYS
                and isinstance(v, str) and v):
            out[key[: -len("_version")]] = v
    return out


def compute_diff_reliability(
    prior_exists: bool, prior: dict[str, Any] | None, current: dict[str, Any],
) -> tuple[bool, str | None, bool]:
    """Decide whether the cross-run diff is trustworthy, returning
    ``(diff_reliable, diff_version_note, diff_trend_reset)``.

    Layers the plugin/schema check (``_diff_is_reliable``) over the tool-backend
    check (``_tool_version_change_note``): a MINOR/PATCH plugin bump keeps the
    diff armed unless a complexity backend also moved. A first run (no prior) is
    trivially reliable - there is nothing to compare, so nothing to distrust.
    """
    if not prior_exists:
        return True, None, False
    reliable, note = _diff_is_reliable(
        (prior or {}).get("plugin_version"),
        current.get("plugin_version"),
        (prior or {}).get("schema_version"),
        current.get("schema_version"),
    )
    if not reliable:
        # A MAJOR plugin bump is a breaking change to the core: the prior trend
        # history is not comparable, so the report discloses a reset.
        trend_reset = bool(note and note.startswith("major version changed"))
        return False, note, trend_reset
    # Plugin/schema are comparable; a backend version change still voids the diff
    # (and names which tool moved) so the numbers aren't read as a regression.
    tool_note = _tool_version_change_note(
        stats_tool_versions(prior), stats_tool_versions(current),
    )
    if tool_note is not None:
        return False, tool_note, False
    return True, None, False


def _tool_version_change_note(
    prior_tools: dict[str, str], current_tools: dict[str, str]
) -> str | None:
    """Return a note naming the first complexity backend whose version changed
    between the two snapshots, or ``None`` when every shared tool matches.

    Only a tool recorded in BOTH snapshots can be compared; a tool missing from
    the prior snapshot (older, pre-stamping) is skipped rather than treated as a
    change - that case is already caught by the plugin/schema reliability check.
    A backend version change (e.g. a lizard release) can shift cyclomatic scores
    with no change in the tree, so the diff against the prior snapshot is voided.
    """
    for tool in sorted(current_tools):
        pv = prior_tools.get(tool)
        cv = current_tools.get(tool)
        if pv is not None and cv is not None and pv != cv:
            return (
                f"{tool} version changed {pv}->{cv}; complexity scores may shift, "
                "so the diff against the prior snapshot is not comparable"
            )
    return None


def prior_stamps(prior: dict[str, Any] | None) -> tuple[Any, Any]:
    """The prior snapshot's (plugin_version, schema_version), None when absent."""
    prior_version = prior.get("plugin_version") if prior else None
    prior_schema = prior.get("schema_version") if prior else None
    return prior_version, prior_schema
