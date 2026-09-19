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
  tag ships the action. ``gh`` checks the running tag first; only when it is
  absent or ships no ``action.yml`` are the tags listed and the newest published
  tag that does is pinned instead, with the choice printed. With neither ``gh`` nor ``git`` reaching GitHub, the running
  version is emitted with an "unverified" warning. An explicit ``--version`` is
  emitted as given, unchecked. When the running version is unknown or known
  unpublished and no published tag qualifies, nothing is written and the exit
  code is 1.
- ``--branch`` defaults to the repo's detected default branch (``main`` if it
  can't be detected).
- ``--tools`` defaults to auto-detecting ``scc`` on PATH plus ``lizard`` (the
  always-present complexity backend); pass a comma list to override.
- ``--paths <glob>`` / ``--paths-ignore <glob>`` (each repeatable, order kept)
  become ``paths:`` / ``paths-ignore:`` under ``on.pull_request``. GitHub rejects
  both on one event, so passing both is a usage error. With neither, when an
  existing workflow under ``.github/workflows/`` filters pull requests by path (a
  ``paths:`` or ``paths-ignore:`` key on a ``pull_request`` trigger, or
  ``dorny/paths-filter``), the gate gets
  ``paths-ignore: ['**/*.md', '.assess/**']`` and a line saying so is printed.

Run:
    uv run assess_emit_workflow.py <repo_root> [--version V] [--branch B] [--tools a,b]
        [--paths GLOB ... | --paths-ignore GLOB ...]
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

from lib.ci_workflow import DEFAULT_PATHS_IGNORE, emit_ci_workflow, find_path_filtered_workflow


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


def _resolve_version(running: str | None) -> tuple[str | None, str]:
    """Pick the version to pin and a one-line note saying which and why.

    ``None`` means nothing safe can be pinned; the note says why."""
    status = _action_status(f"v{running}") if running else "absent"
    if running and status == "ok":
        return running, f"Pinned v{running}: the running version's tag ships action.yml."
    tags = _published_tags()
    if running and status == "unknown":
        if tags is None:
            return _unverified(running, "neither gh nor git ls-remote reached GitHub")
        if f"v{running}" in tags and _released_with_action(f"v{running}"):
            return running, f"Pinned v{running}: the tag is published (gh could not confirm action.yml)."
    why = f"v{running} is not published or ships no action.yml" if running else "the running version is unknown"
    for tag in [t for t in tags or [] if _released_with_action(t)][:_MAX_TAG_PROBES]:
        # gh unavailable before or during the walk: a release at or after the
        # first action.yml release stands in. Only a definite 404 rules a tag out.
        probe = status if status == "unknown" else _action_status(tag)
        if probe == "ok":
            return tag[1:], f"Pinned {tag}, the newest published tag that ships action.yml: {why}."
        if probe == "unknown":
            return tag[1:], f"Pinned {tag}, the newest published release after action.yml shipped (gh could not confirm it): {why}."
    # Every path here has a definite negative (gh's 404, or a tag list without
    # the running tag), so pinning the running version would write a dead ref.
    return None, (
        f"ERROR: no workflow written - {why}, and no published tag shipping action.yml was "
        "found. Pass --version <X.Y.Z> naming a published release."
    )


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


def _opt_all(args: list[str], name: str) -> list[str]:
    """Every value of a repeatable flag, in the order given."""
    return [args[i + 1] for i, arg in enumerate(args[:-1]) if arg == name]


def _path_filters(repo_root: Path, paths: list[str], paths_ignore: list[str]) -> tuple[list[str], list[str]]:
    """The explicit filters, or the docs-only default when the repo already filters by path."""
    if paths or paths_ignore:
        return paths, paths_ignore
    source = find_path_filtered_workflow(repo_root)
    if source is None:
        return [], []
    globs = ", ".join(DEFAULT_PATHS_IGNORE)
    print(
        f"Applied the default paths-ignore ({globs}): {source.relative_to(repo_root)} "
        "already filters pull requests by path, so docs-only and .assess/-only PRs skip "
        "the gate, and doc-truth findings (lying_map, orphaned_understanding) no longer "
        "gate them. A skipped PR reports no gate check at all, so a required status check "
        "on the gate would stay pending on it. Pass --paths or --paths-ignore to override.",
        file=sys.stderr,
    )
    return [], list(DEFAULT_PATHS_IGNORE)


_USAGE = (
    "Usage: assess_emit_workflow.py <repo_root> [--version V] [--branch B] [--tools a,b] "
    "[--paths GLOB ... | --paths-ignore GLOB ...]"
)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    flags = {"--version", "--branch", "--tools", "--paths", "--paths-ignore"}
    positional: list[str] = []
    i = 0
    while i < len(args):
        if args[i] in flags:
            if i + 1 >= len(args) or args[i + 1] in flags:
                print(f"{args[i]} needs a value.", file=sys.stderr)
                print(_USAGE, file=sys.stderr)
                return 2
            if args[i] in {"--paths", "--paths-ignore"} and not args[i + 1].strip():
                print(f"{args[i]} needs a non-empty value.", file=sys.stderr)
                print(_USAGE, file=sys.stderr)
                return 2
            i += 2
            continue
        if args[i].startswith("-"):
            print(f"Unknown option {args[i]}: pass a flag and its value as two arguments.", file=sys.stderr)
            print(_USAGE, file=sys.stderr)
            return 2
        positional.append(args[i])
        i += 1
    if len(positional) != 1:
        if len(positional) > 1:
            print(
                f"Unexpected arguments {positional[1:]}: quote globs so the shell does not expand them.",
                file=sys.stderr,
            )
        print(_USAGE, file=sys.stderr)
        return 2
    paths, paths_ignore = _opt_all(args, "--paths"), _opt_all(args, "--paths-ignore")
    if paths and paths_ignore:
        print("GitHub rejects --paths and --paths-ignore on the same event; pass one.", file=sys.stderr)
        print(_USAGE, file=sys.stderr)
        return 2
    repo_root = Path(positional[0]).resolve()
    version = _opt(args, "--version")
    if version is None:
        version, note = _resolve_version(_running_version())
        print(note, file=sys.stderr)
        if version is None:
            return 1
    branch = _opt(args, "--branch") or _default_branch(repo_root)
    tools_arg = _opt(args, "--tools")
    tools = (
        [t.strip() for t in tools_arg.split(",") if t.strip()]
        if tools_arg is not None
        else _detect_tools()
    )
    paths, paths_ignore = _path_filters(repo_root, paths, paths_ignore)
    path = emit_ci_workflow(
        repo_root, tools, version, default_branch=branch, paths=paths, paths_ignore=paths_ignore,
    )
    print(str(path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
