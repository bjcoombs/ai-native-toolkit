"""Emit the frozen-harness CI workflow for /assess (the third end-of-run offer).

Thin CLI over ``lib.ci_workflow.emit_ci_workflow``. The orchestrator (SKILL.md
Step 6.5) runs this after the user accepts the "freeze this into a repeatable
check?" offer. It writes ``.github/workflows/assess-gate.yml`` into the target
repo, pinning a published toolkit release and baking in the toolchain it found.

Defaults are derived so the common case is a single argument:
- ``--version`` defaults to the running plugin's version (``plugin.json`` beside
  this script), never the target's ``.assess/run-context.json``, which can be
  months old. The pin is checked upstream: ``git ls-remote --tags`` lists the
  published tags and ``gh api .../contents/action.yml?ref=<tag>`` confirms the
  tag ships the action. When the running tag is absent or ships no
  ``action.yml``, the newest published tag that does is pinned instead and the
  choice is printed. With neither ``gh`` nor ``git`` reaching GitHub, the running
  version is emitted with an "unverified" warning. An explicit ``--version`` is
  emitted as given, unchecked.
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
import re
import shutil
import subprocess
import sys
from pathlib import Path

from lib.ci_workflow import emit_ci_workflow


_REPO = "bjcoombs/ai-native-toolkit"
_REMOTE = f"https://github.com/{_REPO}.git"
# action.yml first shipped in v1.42.0; no earlier tag can back a ``uses:`` line.
_FIRST_ACTION_RELEASE = (1, 42, 0)
# Upper bound on per-tag action.yml lookups while walking down the tag list.
_MAX_TAG_PROBES = 10
_SEMVER_TAG = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")


def _running_version() -> str | None:
    """The running plugin's version from ``.claude-plugin/plugin.json``.

    scripts/assess_emit_workflow.py -> scripts/ -> skills/assess/ -> skills/ -> repo root.
    Same lookup as ``assess_core._read_plugin_version``; not imported from there
    because that module pulls the whole analysis stack and its dependencies.
    """
    plugin_json = Path(__file__).resolve().parents[3] / ".claude-plugin" / "plugin.json"
    try:
        version = json.loads(plugin_json.read_text(encoding="utf-8")).get("version")
    except (OSError, json.JSONDecodeError):
        return None
    return str(version) if version else None


def _run(cmd: list[str]) -> tuple[int, str, str]:
    """Run ``cmd``; ``(returncode, stdout, stderr)``, with 127 when it can't start.

    The only door to the network (tests stub it), via ``gh`` / ``git`` on PATH.
    """
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=20, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        return 127, "", str(exc)
    return out.returncode, out.stdout, out.stderr


def _semver(tag: str) -> tuple[int, int, int] | None:
    m = _SEMVER_TAG.match(tag)
    return (int(m[1]), int(m[2]), int(m[3])) if m else None


def _published_tags() -> list[str] | None:
    """Release tags (``vX.Y.Z``) on the upstream, newest first; None if unreachable."""
    rc, out, _ = _run(["git", "ls-remote", "--tags", "--refs", _REMOTE])
    if rc != 0:
        return None
    tags = [line.rsplit("refs/tags/", 1)[-1] for line in out.splitlines() if "refs/tags/" in line]
    return sorted((t for t in tags if _semver(t)), key=lambda t: _semver(t) or (0, 0, 0), reverse=True)


def _action_status(tag: str) -> str:
    """``ok`` if ``tag`` ships action.yml, ``absent`` on a definite HTTP 404,
    ``unknown`` when gh can't answer (missing, unauthenticated, offline)."""
    rc, out, err = _run(["gh", "api", f"repos/{_REPO}/contents/action.yml?ref={tag}"])
    if rc == 0:
        return "ok"
    return "absent" if "HTTP 404" in f"{out}\n{err}" else "unknown"


def _released_with_action(tag: str) -> bool:
    return (_semver(tag) or (0, 0, 0)) >= _FIRST_ACTION_RELEASE


def _unverified(version: str, why: str) -> tuple[str, str]:
    return version, (
        f"WARNING: pin v{version} is unverified - {why}. "
        "Check the tag exists and ships action.yml before committing the workflow."
    )


def _resolve_version(running: str | None) -> tuple[str, str]:
    """Pick the version to pin and a one-line note saying which and why."""
    tags = _published_tags()
    status = _action_status(f"v{running}") if running else "absent"
    if running and status == "ok":
        return running, f"Pinned v{running}: the running version's tag ships action.yml."
    if running and status == "unknown":
        if tags is None:
            return _unverified(running, "neither gh nor git ls-remote reached GitHub")
        if f"v{running}" in tags and _released_with_action(f"v{running}"):
            return running, f"Pinned v{running}: the tag is published (gh could not confirm action.yml)."
    why = f"v{running} is not published or ships no action.yml" if running else "the running version is unknown"
    for tag in [t for t in tags or [] if _released_with_action(t)][:_MAX_TAG_PROBES]:
        # With gh unavailable, a release at or after the first action.yml release stands in.
        if status == "unknown" or _action_status(tag) == "ok":
            return tag[1:], f"Pinned {tag}, the newest published tag that ships action.yml: {why}."
    return _unverified(running or "latest", f"{why}, and no published tag shipping action.yml was found")


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
    version = _opt(args, "--version")
    if version is None:
        version, note = _resolve_version(_running_version())
        print(note, file=sys.stderr)
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
