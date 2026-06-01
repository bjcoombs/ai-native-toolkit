"""Emit the frozen-harness CI workflow for /assess (the third end-of-run offer).

Thin CLI over ``lib.ci_workflow.emit_ci_workflow``. The orchestrator (SKILL.md
Step 6.5) runs this after the user accepts the "freeze this into a repeatable
check?" offer. It writes ``.github/workflows/assess-gate.yml`` into the target
repo, baking in the plugin version this run used and the toolchain it found.

Defaults are derived so the common case is a single argument:
- ``--version`` defaults to the ``plugin_version`` recorded in run-context.json
  (the exact version that produced this snapshot), so the emitted workflow pins
  the matching deterministic core.
- ``--branch`` defaults to the repo's detected default branch (``main`` if it
  can't be detected).
- ``--tools`` defaults to auto-detecting ``scc`` on PATH plus ``lizard`` (the
  always-present complexity backend); pass a comma list to override.

Run:
    uv run assess_emit_workflow.py <repo_root> [--version V] [--branch B] [--tools a,b]
"""
# /// script
# requires-python = ">=3.11"
# ///
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

from lib.ci_workflow import emit_ci_workflow


def _plugin_version(repo_root: Path) -> str:
    """Read ``plugin_version`` from the repo's run-context.json, else 'latest'."""
    ctx_path = repo_root / ".assess" / "run-context.json"
    try:
        ctx = json.loads(ctx_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "latest"
    version = ctx.get("plugin_version")
    return str(version) if version else "latest"


def _default_branch(repo_root: Path) -> str:
    """Best-effort detect the repo's default branch; fall back to 'main'."""
    try:
        out = subprocess.run(
            ["git", "-C", str(repo_root), "symbolic-ref", "--short", "refs/remotes/origin/HEAD"],
            capture_output=True, text=True, timeout=5, check=False,
        )
        ref = out.stdout.strip()
        if ref:
            return ref.rsplit("/", 1)[-1]
    except (OSError, subprocess.SubprocessError):
        pass
    return "main"


def _detect_tools() -> list[str]:
    """Discovered external tools we can install in CI; lizard is always present."""
    tools = ["lizard"]
    if shutil.which("scc"):
        tools.append("scc")
    return tools


def _opt(args: list[str], name: str) -> str | None:
    if name in args:
        idx = args.index(name)
        if idx + 1 < len(args):
            return args[idx + 1]
    return None


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    flags = {"--version", "--branch", "--tools"}
    positional: list[str] = []
    i = 0
    while i < len(args):
        if args[i] in flags:
            i += 2
            continue
        if args[i].startswith("-"):
            i += 1
            continue
        positional.append(args[i])
        i += 1
    if not positional:
        print(
            "Usage: assess_emit_workflow.py <repo_root> "
            "[--version V] [--branch B] [--tools a,b]",
            file=sys.stderr,
        )
        return 2
    repo_root = Path(positional[0]).resolve()
    version = _opt(args, "--version") or _plugin_version(repo_root)
    branch = _opt(args, "--branch") or _default_branch(repo_root)
    tools_arg = _opt(args, "--tools")
    tools = (
        [t.strip() for t in tools_arg.split(",") if t.strip()]
        if tools_arg is not None
        else _detect_tools()
    )
    path = emit_ci_workflow(repo_root, tools, version, default_branch=branch)
    print(str(path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
