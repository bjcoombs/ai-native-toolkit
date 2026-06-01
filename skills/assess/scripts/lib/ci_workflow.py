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

from pathlib import Path
from string import Template

# Where the template lives relative to this module: scripts/lib/ -> skills/assess/.
_TEMPLATE_PATH = Path(__file__).resolve().parent.parent.parent / "templates" / "assess-gate.yml.template"

# Discovered binaries we know how to install in CI. lizard / squarify / grimp /
# networkx are Python deps the scripts pull via uv, so they need no OS step; only
# external binaries (scc and the per-language dead-code tools) get a step here.
# A discovered tool with no recipe is surfaced as a comment so the maintainer
# wires it in rather than the gate silently dropping it.
_TOOL_STEPS: dict[str, str] = {
    "scc": (
        "      - name: Install scc\n"
        "        run: go install github.com/boyter/scc/v3@latest\n"
    ),
    "staticcheck": (
        "      - name: Install staticcheck\n"
        "        run: go install honnef.co/go/tools/cmd/staticcheck@latest\n"
    ),
    "ts-prune": (
        "      - name: Install ts-prune\n"
        "        run: npm install -g ts-prune\n"
    ),
    "knip": (
        "      - name: Install knip\n"
        "        run: npm install -g knip\n"
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


def render_ci_workflow(
    plugin_version: str,
    default_branch: str = "main",
    discovered_tools: list[str] | None = None,
    generated_date: str = "an /assess run",
) -> str:
    """Render the assess-gate workflow YAML as a string.

    Pure: no disk writes. ``discovered_tools`` are the binaries this run found
    (e.g. ``["lizard", "scc"]``); only the external ones get an install step.
    """
    template = Template(_TEMPLATE_PATH.read_text(encoding="utf-8"))
    return template.substitute(
        plugin_version=plugin_version,
        default_branch=default_branch,
        generated_date=generated_date,
        tool_steps=_render_tool_steps(discovered_tools or []),
    )


def emit_ci_workflow(
    repo_root: Path,
    discovered_tools: list[str],
    plugin_version: str,
    default_branch: str = "main",
    generated_date: str = "an /assess run",
) -> Path:
    """Write ``.github/workflows/assess-gate.yml`` with the discovered tools baked in.

    Returns the path written. Creates ``.github/workflows/`` if absent.
    """
    workflow = render_ci_workflow(
        plugin_version=plugin_version,
        default_branch=default_branch,
        discovered_tools=discovered_tools,
        generated_date=generated_date,
    )
    workflow_path = repo_root / ".github" / "workflows" / "assess-gate.yml"
    workflow_path.parent.mkdir(parents=True, exist_ok=True)
    workflow_path.write_text(workflow, encoding="utf-8")
    return workflow_path
