"""Emit the frozen-harness GitHub Action for /assess.

The third end-of-run offer turns ``/assess`` from a thing-you-run into a
thing-that-runs: it writes a GitHub Action that runs the deterministic core on
every pull request and gates on AI-readiness floors (and, opt-in, cross-run
regressions) via ``assess_gate.py``. The AI writes the workflow once, baking in
the toolchain this run discovered; from then on it is a contract, not a norm.

This module renders the workflow from ``templates/assess-gate.yml.template`` (a
stdlib ``string.Template`` - no new dependency, honouring the deterministic-core
contract). It is pure-render plus a thin file-writer, so the rendering is unit
testable without touching disk.
"""
from __future__ import annotations

import re
from pathlib import Path
from string import Template

# Where the template lives relative to this module: scripts/lib/ -> skills/assess/.
_TEMPLATE_PATH = Path(__file__).resolve().parent.parent.parent / "templates" / "assess-gate.yml.template"

# Discovered binaries we know how to install in CI. lizard / squarify / grimp /
# networkx are Python deps the scripts pull via uv, so they need no OS step; only
# external binaries (scc and the per-language dead-code tools) get a step here.
# A discovered tool with no recipe is surfaced as a comment so the maintainer
# wires it in rather than the gate silently dropping it.
#
# Two invariants, both enforced by tests:
# - Every install is pinned to an exact release. A floating @latest can shift
#   complexity-stats.json between runs and move the regression baseline with no
#   change in the assessed tree (a future scc release did exactly this risk).
# - Every install carries ``continue-on-error: true``. The gate's contract is
#   warn-only until the repo opts in via .assess/config.toml; an install failure
#   is infrastructure, not a finding, so it degrades to reduced coverage (the
#   core skips tools missing from PATH) instead of a red check.
_CONTINUE = "        continue-on-error: true  # missing tool degrades coverage, not the check\n"
_TOOL_STEPS: dict[str, str] = {
    "scc": (
        "      - name: Install scc\n"
        + _CONTINUE
        + "        run: go install github.com/boyter/scc/v3@v3.7.0\n"
    ),
    "staticcheck": (
        "      - name: Install staticcheck\n"
        + _CONTINUE
        + "        run: go install honnef.co/go/tools/cmd/staticcheck@2026.1\n"
    ),
    "ts-prune": (
        "      - name: Install ts-prune\n"
        + _CONTINUE
        + "        run: npm install -g ts-prune@0.10.3\n"
    ),
    "knip": (
        "      - name: Install knip\n"
        + _CONTINUE
        + "        run: npm install -g knip@6.16.1\n"
    ),
}


def _render_tool_steps(discovered_tools: list[str]) -> str:
    """Render the install steps for discovered external tools.

    Returns a block beginning with a leading newline so it slots cleanly between
    two existing steps in the template, or an empty string when nothing external
    needs installing (the deterministic core's Python deps come via uv).
    """
    steps: list[str] = []
    unknown: list[str] = []
    seen: set[str] = set()
    for tool in discovered_tools:
        if tool in seen:
            continue
        seen.add(tool)
        recipe = _TOOL_STEPS.get(tool)
        if recipe is not None:
            steps.append(recipe)
        elif tool not in {"lizard", "squarify", "grimp", "networkx", "matplotlib", "numpy"}:
            unknown.append(tool)
    if unknown:
        listed = ", ".join(sorted(unknown))
        steps.append(
            f"      # Discovered tools without an install recipe: {listed}.\n"
            "      # Add a step above if the gate should depend on them.\n"
        )
    if not steps:
        return ""
    return "\n" + "".join(steps)


# The ignore list written when neither --paths nor --paths-ignore is given and an
# existing workflow already filters pull requests by path: docs-only PRs skip
# the gate the way they skip the repo's other path-filtered checks, and a PR that
# only refreshes the committed .assess/ snapshot does not gate against itself.
# The cost: doc-truth findings (lying_map, orphaned_understanding) no longer gate
# docs-only PRs, so the CLI's notice says so.
DEFAULT_PATHS_IGNORE = ["**/*.md", ".assess/**"]

# A line scan, not a YAML parse: the deterministic core carries no YAML dependency.
# Only a pull-request trigger counts as evidence that PR checks are path-scoped; a
# ``paths:`` on ``push`` (a publish trigger, say) says nothing about PR checks.
_PR_TRIGGER_RE = re.compile(r"^(\s*)pull_request(?:_target)?\s*:(.*)$")
_PATHS_KEY_RE = re.compile(r"^\s*paths(?:-ignore)?\s*:")
_FLOW_PATHS_RE = re.compile(r"[{,]\s*paths(?:-ignore)?\s*:")


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip())


def _filters_pull_requests_by_path(text: str) -> bool:
    """True when a ``pull_request`` / ``pull_request_target`` trigger carries a
    ``paths:`` or ``paths-ignore:`` key, or a step uses ``dorny/paths-filter``
    (which only has a diff to filter on pull-request-shaped events)."""
    if "dorny/paths-filter" in text:
        return True
    lines = text.splitlines()
    for i, line in enumerate(lines):
        m = _PR_TRIGGER_RE.match(line)
        if m is None:
            continue
        if _FLOW_PATHS_RE.search(m[2]):  # pull_request: {paths: [...]}
            return True
        depth = len(m[1])
        for child in lines[i + 1:]:
            if not child.strip() or child.lstrip().startswith("#"):
                continue
            if _indent(child) <= depth:
                break
            if _PATHS_KEY_RE.match(child):
                return True
    return False


def _yaml_quote(value: str) -> str:
    """Single-quoted YAML scalar: globs start with ``*`` (an alias) otherwise."""
    return "'" + value.replace("'", "''") + "'"


def _render_path_filters(paths: list[str] | None, paths_ignore: list[str] | None) -> str:
    """Render ``paths:`` / ``paths-ignore:`` lists for the ``on.pull_request`` block.

    Each list keeps its given order. Returns lines ending in a newline, or an empty
    string when neither list has entries.
    """
    lines: list[str] = []
    for key, globs in (("paths", paths), ("paths-ignore", paths_ignore)):
        if globs:
            lines.append(f"    {key}:\n")
            lines.extend(f"      - {_yaml_quote(g)}\n" for g in globs)
    return "".join(lines)


def find_path_filtered_workflow(repo_root: Path) -> Path | None:
    """The first existing workflow that filters pull requests by path, else None.

    Scans ``.github/workflows/*.yml`` and ``*.yaml`` in name order, skipping the
    gate's own ``assess-gate.yml`` so a regenerated gate never detects its own
    default. A file counts when its ``pull_request`` or ``pull_request_target``
    trigger has a ``paths:`` or ``paths-ignore:`` key, or it uses
    ``dorny/paths-filter``.
    """
    workflows = repo_root / ".github" / "workflows"
    if not workflows.is_dir():
        return None
    candidates = sorted(p for p in workflows.iterdir() if p.suffix in {".yml", ".yaml"} and p.is_file())
    for path in candidates:
        if path.name == "assess-gate.yml":
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if _filters_pull_requests_by_path(text):
            return path
    return None


def render_ci_workflow(
    plugin_version: str,
    default_branch: str = "main",
    discovered_tools: list[str] | None = None,
    generated_date: str = "an /assess run",
    paths: list[str] | None = None,
    paths_ignore: list[str] | None = None,
) -> str:
    """Render the assess-gate workflow YAML as a string.

    Pure: no disk writes. ``discovered_tools`` are the binaries this run found
    (e.g. ``["lizard", "scc"]``); only the external ones get an install step.
    ``paths`` / ``paths-ignore`` become lists under ``on.pull_request``. Passing
    both raises ``ValueError``: GitHub rejects the two on one event, and the gate
    would then never run.
    """
    if paths and paths_ignore:
        raise ValueError("paths and paths_ignore cannot both be set on one pull_request trigger")
    template = Template(_TEMPLATE_PATH.read_text(encoding="utf-8"))
    return template.substitute(
        plugin_version=plugin_version,
        default_branch=default_branch,
        generated_date=generated_date,
        tool_steps=_render_tool_steps(discovered_tools or []),
        path_filters=_render_path_filters(paths, paths_ignore),
    )


def emit_ci_workflow(
    repo_root: Path,
    discovered_tools: list[str],
    plugin_version: str,
    default_branch: str = "main",
    generated_date: str = "an /assess run",
    paths: list[str] | None = None,
    paths_ignore: list[str] | None = None,
) -> Path:
    """Write ``.github/workflows/assess-gate.yml`` with the discovered tools baked in.

    Returns the path written. Creates ``.github/workflows/`` if absent.
    """
    workflow = render_ci_workflow(
        plugin_version=plugin_version,
        default_branch=default_branch,
        discovered_tools=discovered_tools,
        generated_date=generated_date,
        paths=paths,
        paths_ignore=paths_ignore,
    )
    workflow_path = repo_root / ".github" / "workflows" / "assess-gate.yml"
    workflow_path.parent.mkdir(parents=True, exist_ok=True)
    workflow_path.write_text(workflow, encoding="utf-8")
    return workflow_path
