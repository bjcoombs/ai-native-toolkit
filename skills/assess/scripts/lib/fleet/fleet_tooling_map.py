"""Shared-tooling map SVG from one fleet run (issue #674, parent #672).

Repositories on the left, the shared CI sources and agent marketplaces they
reference on the right (``shared_tooling`` ids, git-tracked files only), one
curve per reference. Each shared item carries its dependent count: what breaks
if that template changes.

Drift: a CI include whose pinned ``ref`` differs from the most common ref the
fleet pins for that source is drawn dashed in the warning colour (ties go to
the lexically greatest ref). An unpinned include (``ref`` null) follows the
source's default branch: it is not drift, and the item counts it as unpinned.
An include whose ``ref`` key is absent (counts-only dropped it), or whose ref
holds a CI variable (``ref_variable``, #684: pinned, to a version the file does
not say), is "pin unknown", drawn dotted, never "in step", unpinned or drift. A
run whose ``shared_tooling`` blocks carry no ``ref`` or ``ref_variable`` at all
(before #674) says so in the subtitle and draws no dashes.

A repository whose ``shared_tooling`` evidence is missing (a failed scan, an
unavailable or absent block) is named on a hatched strip, never shown as
sharing nothing; repositories that share nothing are named on their own line.
Deterministic: repositories and items sorted by fixed keys.

The shared-tooling map (#674). `tooling_view(repos)` reads each scanned repository's
`shared_tooling` block: links to its tracked CI source and marketplace ids with the refs it
pins (`ci_includes[].ref`), the repositories that share nothing, and those with no evidence
(failed, absent or unavailable block). `common_refs` is each item's most pinned ref (ties:
lexically greatest); `drifted` marks a link pinned off it, drawn dashed. An unpinned include
is not drift (counted as unpinned); an include whose `ref` key is absent (dropped by
counts-only) or whose ref is a CI variable (`ref_variable`, #684) is "pin unknown", drawn
dotted, never unpinned or drift. A run whose blocks carry neither `ref` nor `ref_variable`
(before #674) says "drift not shown" instead. `render_tooling_map` draws
repositories left, items right with their dependent counts, and the unknown repositories on
a hatched strip.
"""
from __future__ import annotations

import textwrap
from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any, TypedDict

import lib.fleet.fleet_svg as svg
from lib.fleet.runner import STATUS_OK, FleetRow
from lib.fleet.fleet_svg import THEMED_HATCH

TITLE = "Fleet shared tooling"
FILE = "shared-tooling.svg"
PAD = 16.0
TOP = 112.0
ROW_H = 26.0
CURVE_W = 300.0
MAX_REPO_LABEL = 48
MAX_ITEM_LABEL = 44
WRAP = 104
LINE_H = 16.0
MIN_WIDTH = 760.0

KINDS = {
    "gitlab_project": "CI: GitLab project include",
    "gitlab_component": "CI: GitLab component",
    "github_reusable_workflow": "CI: GitHub reusable workflow",
    "marketplace": "Agent marketplace",
}


class Link(TypedDict):
    repo: str
    item: str  # shared_tooling id, e.g. "gitlab_project:platform/ci"
    refs: list[str]  # pinned refs seen for this item in this repository, sorted
    unpinned: bool  # an include of this item with no pin (ref null): follows the default branch
    pin_unknown: bool  # ref absent (not collected, dropped by counts-only) or a CI variable


class ToolingView(TypedDict):
    links: list[Link]  # sorted by (repo, item)
    none: list[str]  # repositories with evidence and no shared item
    unknown: list[str]  # repositories with no shared_tooling evidence
    refs_collected: bool  # any CI include carries a ref or ref_variable key (absent before #674)


def _block(row: FleetRow) -> Mapping[str, Any] | None:
    block = (row["scans"] or {}).get("shared_tooling") if row["status"] == STATUS_OK else None
    return block if isinstance(block, dict) and block.get("available") is True else None


UNPINNED = "(unpinned)"
UNKNOWN = "(pin unknown)"


def _refs(block: Mapping[str, Any]) -> tuple[dict[str, set[str]], bool]:
    """Item -> the pins its tracked includes carry, and whether any include carries a pin
    field at all. ``UNPINNED`` for a null ref; ``UNKNOWN`` for a ref that holds a CI
    variable (``ref_variable``) or is absent (a run before #674, or a ref counts-only dropped)."""
    refs: dict[str, set[str]] = {}
    collected = False
    for entry in block.get("ci_includes") or []:
        if isinstance(entry, dict) and entry.get("tracked") is True and isinstance(entry.get("id"), str):
            collected = collected or "ref" in entry or "ref_variable" in entry
            ref = UNKNOWN if entry.get("ref_variable") is True else entry.get("ref", UNKNOWN)
            pin = ref if isinstance(ref, str) and ref and ref != UNKNOWN else (
                UNPINNED if ref is None else UNKNOWN)
            refs.setdefault(entry["id"], set()).add(pin)
    return refs, collected


def _link(repo: str, item: str, pins: set[str]) -> Link:
    return {"repo": repo, "item": item, "refs": sorted(pins - {UNPINNED, UNKNOWN}),
            "unpinned": UNPINNED in pins, "pin_unknown": UNKNOWN in pins}


def tooling_view(repos: Sequence[FleetRow]) -> ToolingView:
    """Every repository's shared items and pinned refs, from its ``shared_tooling`` block."""
    links: list[Link] = []
    none: list[str] = []
    unknown: list[str] = []
    collected = False
    for row in sorted(repos, key=lambda r: r["repo"]):
        block = _block(row)
        if block is None:
            unknown.append(row["repo"])
            continue
        ids = sorted({i for key in ("ci_source_ids", "marketplace_ids")
                      for i in block.get(key) or [] if isinstance(i, str)})
        if not ids:
            none.append(row["repo"])
        refs, has_pins = _refs(block)
        collected = collected or has_pins
        links.extend(_link(row["repo"], i, refs.get(i, set())) for i in ids)
    return {"links": links, "none": none, "unknown": unknown,
            "refs_collected": collected}


def common_refs(links: Sequence[Link]) -> dict[str, str]:
    """Item -> the ref most repositories pin (ties: the lexically greatest)."""
    counts: dict[str, Counter[str]] = {}
    for link in links:
        counts.setdefault(link["item"], Counter()).update(link["refs"])
    return {item: max(c.items(), key=lambda kv: (kv[1], kv[0]))[0]
            for item, c in sorted(counts.items()) if c}


def drifted(link: Link, common: Mapping[str, str]) -> bool:
    """A pinned ref other than the fleet's most common one for the item."""
    target = common.get(link["item"])
    return target is not None and any(ref != target for ref in link["refs"])


def item_parts(item: str) -> tuple[str, str]:
    """(display name, kind label) for a shared_tooling id."""
    kind, _, name = item.partition(":")
    return (name or item), KINDS.get(kind, kind)


def _wrapped(prefix: str, names: Sequence[str]) -> list[str]:
    return textwrap.wrap(prefix + ", ".join(names), WRAP)


def render_tooling_map(view: ToolingView) -> str:
    """The shared-tooling map as an SVG document; the same view gives the same bytes."""
    links = view["links"]
    repos = sorted({link["repo"] for link in links})
    dependents = Counter(link["item"] for link in links)
    items = sorted(dependents, key=lambda i: (-dependents[i], i))
    common = common_refs(links)
    drift = {(link["repo"], link["item"]) for link in links if drifted(link, common)}
    repo_labels = [svg.truncate(r, MAX_REPO_LABEL, keep_tail=True) for r in repos]
    lx = PAD + max([svg.text_width(label) for label in repo_labels], default=80.0) + 14
    rx = lx + CURVE_W
    rows = max(len(repos), len(items), 1)
    span = (rows - 1) * ROW_H
    ry = {repo: TOP + i * ROW_H for i, repo in enumerate(repos)}
    iy = {item: TOP + (i * span / (len(items) - 1) if len(items) > 1 else 0.0)
          for i, item in enumerate(items)}
    bar = {item: min(max(26.0, dependents[item] * 14.0), 140.0) for item in items}

    unknown_pins = sum(1 for link in links if link["pin_unknown"])
    drift_note = (f"{len(drift)} pinned off the fleet's most common ref (dashed)"
                  + (f", {unknown_pins} pins unknown (dotted)" if unknown_pins else "")
                  if view["refs_collected"] else "pinned refs not collected in this run: drift not shown")
    body = [svg.text(PAD, PAD + 18, TITLE, size=18, weight="bold", cls="fg"),
            svg.text(PAD, PAD + 40, f"{len(repos)} repositories → {len(items)} shared CI sources "
                     f"and marketplaces · {drift_note}", cls="muted"),
            svg.text(PAD, PAD + 58, "Drift: a CI pin that differs from the most common ref the fleet "
                     "pins for that source (ties: the lexically greatest ref); unpinned is not drift.",
                     size=11.5, cls="muted"),
            svg.text(lx - 12, TOP - 22, "REPOSITORY", size=11, anchor="end", cls="muted"),
            svg.text(rx, TOP - 22, "SHARED ITEM · DEPENDENTS", size=11, cls="muted")]
    for link in links:
        y0, y1 = ry[link["repo"]], iy[link["item"]]
        late = (link["repo"], link["item"]) in drift
        if late:
            style = 'class="s-warn" stroke-width="2" stroke-dasharray="5 4"'
        elif link["pin_unknown"] and view["refs_collected"]:
            style = 'class="s-muted" stroke-width="1.4" stroke-dasharray="1 3"'
        else:
            style = 'class="s-accent" stroke-width="1.4" opacity="0.55"'
        pins = [*link["refs"], *([UNPINNED] if link["unpinned"] else []),
                *([UNKNOWN] if link["pin_unknown"] else [])]
        refs = f" @ {', '.join(pins)}" if pins else ""
        tip = f"{link['repo']} → {link['item']}{refs}"
        body.append(f'<path d="M{lx:.1f} {y0:.1f} C {lx + 140:.1f} {y0:.1f}, {rx - 140:.1f} {y1:.1f}, '
                    f'{rx:.1f} {y1:.1f}" fill="none" {style}><title>{svg.esc(tip)}</title></path>')
    for repo, label in zip(repos, repo_labels):
        body.append(svg.text(lx - 12, ry[repo] + 4, label, anchor="end", cls="fg",
                             tooltip=repo if label != repo else None))
        body.append(f'<circle cx="{lx:.1f}" cy="{ry[repo]:.1f}" r="3.5" class="f-fg"/>')
    widest = rx
    for item in items:
        name, kind = item_parts(item)
        label = svg.truncate(name, MAX_ITEM_LABEL)
        off = sum(1 for link in links if link["item"] == item and (link["repo"], item) in drift)
        loose = sum(1 for link in links if link["item"] == item and link["unpinned"])
        sub = kind + (f" · {off} off {common[item]}" if off else
                      (f" · pinned {common[item]}" if item in common else ""))
        sub += f" · {loose} unpinned" if loose and item in common else ""
        y = iy[item]
        body.append(f'<rect x="{rx:.1f}" y="{y - 12:.1f}" width="{bar[item]:.1f}" height="24.0" rx="3" '
                    f'class="f-{"warn" if off else "accent"}"/>')
        body.append(svg.text(rx + 8, y + 4, str(dependents[item]), size=11.5, weight="600", cls="bg"))
        body.append(svg.text(rx + bar[item] + 10, y, label, size=12.5, weight="500", cls="fg",
                             tooltip=name if label != name else None))
        body.append(svg.text(rx + bar[item] + 10, y + 14, sub, size=10.5, cls="muted"))
        widest = max(widest, rx + bar[item] + 10 + max(svg.text_width(label, 12.5),
                                                        svg.text_width(sub, 10.5)))
    cursor = TOP + span + 36
    if not links:
        body.append(svg.text(PAD, TOP, "No repository references a shared CI source or marketplace.",
                             cls="muted"))
    for line in _wrapped("No shared tooling: ", view["none"]) if view["none"] else []:
        body.append(svg.text(PAD, cursor, line, size=11.5, cls="muted"))
        cursor += LINE_H
    if view["unknown"]:
        lines = _wrapped("No shared_tooling evidence, unknown is not none: ", view["unknown"])
        cursor += 6
        parts, height = svg.hatched_note(PAD, cursor, lines)
        body.extend(parts)
        cursor += height
    width = float(int(max(widest + PAD, MIN_WIDTH, PAD * 2 + WRAP * svg.text_width("x", 11.5))) + 1)
    desc = ("Map of fleet repositories (left) to the shared CI sources and agent marketplaces they "
            "reference (right), with each shared item's dependent count; a dashed line is a pin "
            "that differs from the fleet's most common ref; repositories without evidence are "
            "listed on a hatched strip.")
    return svg.document(width, float(int(cursor + PAD)), TITLE, desc, body, defs=THEMED_HATCH)
