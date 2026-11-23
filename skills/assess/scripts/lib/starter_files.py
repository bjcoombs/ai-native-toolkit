"""Plan and write the starter files for the next blocking Step gate (``/assess --fix``, #1154).

The deterministic half of ``assess_fix.py``. Three moves:

- ``detect_facts`` reads what the repository already has through the existing detectors: the
  dominant language (``lib/graph/import_graph.dominant_language``), the check command (the
  ``agent_environment.check.entry_points`` the scan found: a named test or check target first,
  then the build's own test command), the CI system (a tracked ``.github/workflows`` file or
  ``.gitlab-ci.yml``, else the ``origin`` remote's host, else GitHub) and the default branch.
  An override (``--language``, ``--check-command``, ``--ci``) wins and is recorded as one.
- ``plan_files`` turns the gate ids to fix into files (``lib/starter_templates.py``):
  ``instructions`` a ``CLAUDE.md``; ``ci_runs_tests`` a CI job running the check command;
  ``coverage_gate`` a CI job running it under a coverage threshold; the ``guardrail`` a
  ``.claude/settings.json`` deny rule (#1153). Where the CI system keeps one file
  (``.gitlab-ci.yml``) and it exists, the job goes in ``.gitlab/ci/<name>.yml`` and the manual
  step is the ``include:`` line. A gate with no form that fits (no check command, a language with
  no coverage form the gate scan reads) gets a file-less manual step instead.
- ``write_files`` writes each planned file that does not exist and reports each that does as a
  conflict, with the text to add by hand. It never overwrites a file.
"""
from __future__ import annotations

import subprocess
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from lib.graph.import_graph import dominant_language
from lib.run_context_types_fix import CiSystem, FileOutcome, PlannedFile, RepoFacts
from lib.starter_templates import (
    COVERAGE_THRESHOLD,
    coverage_command,
    deny_settings,
    github_workflow,
    gitlab_job,
    instruction_file,
)

GUARDRAIL = "guardrail"
INSTRUCTION_PATH = "CLAUDE.md"
SETTINGS_PATH = ".claude/settings.json"
GITLAB_CI = ".gitlab-ci.yml"
# Files a template reads to pick its setup (lockfiles, manifests, wrappers).
MARKERS = ("uv.lock", "pyproject.toml", "setup.py", "requirements.txt", "package-lock.json",
           "pnpm-lock.yaml", "yarn.lock", "go.mod", "Cargo.toml", "pom.xml", "gradlew",
           "build.gradle", "build.gradle.kts")
# A language's own test command when the scan found none (a starter repository with no build yet).
DEFAULT_CHECK = {"python": "pytest", "go": "go test ./...", "rust": "cargo test",
                 "javascript": "npm test", "typescript": "npm test"}
_CHECK_WORDS = ("test", "check")


def _git(root: Path, *args: str) -> str | None:
    try:
        out = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True,
                             encoding="utf-8", errors="replace", check=True, timeout=30)
    except (subprocess.CalledProcessError, OSError, subprocess.TimeoutExpired):
        return None
    return out.stdout.strip() or None


def pick_check(entry_points: Iterable[Mapping[str, Any]]) -> tuple[str, str] | None:
    """The entry point to run in CI: a test target first, then a check target, then the first."""
    rows = [(str(e.get("command", "")), str(e.get("source", ""))) for e in entry_points if e.get("command")]
    for word in _CHECK_WORDS:
        for command, source in rows:
            if word in command.split()[-1]:
                return command, source
    return rows[0] if rows else None


def _host_system(url: str | None) -> CiSystem | None:
    if not url:
        return None
    if "gitlab" in url.lower():
        return "gitlab"
    if "github" in url.lower():
        return "github"
    return None


def ci_system(root: Path) -> tuple[CiSystem, str]:
    """The CI system and why: a tracked CI file, the remote's host, else GitHub."""
    tracked = _git(root, "ls-files", ".github/workflows", GITLAB_CI) or ""
    paths = tracked.splitlines()
    if any(p.startswith(".github/workflows/") and p.endswith((".yml", ".yaml")) for p in paths):
        return "github", "tracked"
    if GITLAB_CI in paths:
        return "gitlab", "tracked"
    host = _host_system(_git(root, "remote", "get-url", "origin"))
    return (host, "remote") if host else ("github", "default")


def default_branch(root: Path) -> str:
    """``origin``'s HEAD branch, else the checked-out branch, else ``main``."""
    head = _git(root, "symbolic-ref", "--short", "refs/remotes/origin/HEAD")
    if head and "/" in head:
        return head.split("/", 1)[1]
    return _git(root, "branch", "--show-current") or "main"


def detect_facts(root: Path, scans: Mapping[str, Any], *, language: str | None = None,
                 check_command: str | None = None, ci: CiSystem | None = None) -> RepoFacts:
    """What the repository already has, with any override winning."""
    lang = language or dominant_language(root)
    env = scans.get("agent_environment")
    check = env.get("check") if isinstance(env, dict) else None
    picked = pick_check(check.get("entry_points") or [] if isinstance(check, dict) else [])
    if check_command:
        picked = (check_command, "override")
    elif picked is None and lang in DEFAULT_CHECK:
        picked = (DEFAULT_CHECK[lang], f"{lang} default")
    system, basis = (ci, "override") if ci else ci_system(root)
    return {"language": lang, "check_command": picked[0] if picked else None,
            "check_source": picked[1] if picked else None, "ci_system": system, "ci_basis": basis,
            "default_branch": default_branch(root),
            "files": [m for m in MARKERS if (root / m).is_file()]}


def _ci_file(root: Path, facts: RepoFacts, name: str, command: str,
             installs: list[str] | None, gate: str) -> PlannedFile:
    if facts["ci_system"] == "github":
        return {"path": f".github/workflows/{name}.yml", "gate": gate, "manual": None,
                "content": github_workflow(facts, name, command, installs)}
    job = gitlab_job(facts, name, command, installs)
    if not (root / GITLAB_CI).exists():
        return {"path": GITLAB_CI, "gate": gate, "manual": None, "content": job}
    path = f".gitlab/ci/{name}.yml"
    return {"path": path, "gate": gate, "content": job,
            "manual": f"add `include: [{{local: {path}}}]` to `{GITLAB_CI}`"}


def _manual(gate: str, text: str) -> PlannedFile:
    return {"path": "", "gate": gate, "content": "", "manual": text}


def plan_files(root: Path, gates: Iterable[str], facts: RepoFacts, guardrail: bool) -> list[PlannedFile]:
    """The files for ``gates`` (Step gate ids, in Step order) and, when asked, the guardrail.

    An entry with an empty ``path`` writes nothing: its ``manual`` is the step left."""
    wanted = list(dict.fromkeys(gates))
    command = facts["check_command"]
    planned: list[PlannedFile] = []
    gitlab_new = facts["ci_system"] == "gitlab" and not (root / GITLAB_CI).exists()
    jobs: list[str] = []
    for gate in wanted:
        if gate == "instructions":
            planned.append({"path": INSTRUCTION_PATH, "gate": gate, "manual": None,
                            "content": instruction_file(root.resolve().name, facts, guardrail)})
            if command is None:
                planned[-1]["manual"] = f"name the command that runs the tests in `{INSTRUCTION_PATH}`"
        elif gate in ("ci_runs_tests", "coverage_gate") and command is None:
            planned.append(_manual(gate, "no test command was found; rerun with `--check-command \"<command>\"`"))
        elif gate == "ci_runs_tests" and command is not None:
            planned.append(_ci_file(root, facts, "tests", command, None, gate))
        elif gate == "coverage_gate":
            form = coverage_command(facts)
            if form is None:
                planned.append(_manual(gate, f"add a coverage threshold of at least {COVERAGE_THRESHOLD} percent "
                                             f"for {facts['language'] or 'this project'} and run it in CI"))
            else:
                planned.append(_ci_file(root, facts, "coverage", form[0], form[1], gate))
        else:
            planned.append(_manual(gate, f"no starter file is known for the `{gate}` gate"))
        if gitlab_new and planned[-1]["path"] == GITLAB_CI:
            jobs.append(planned[-1]["content"])
    if len(jobs) > 1:  # a new .gitlab-ci.yml holds every job: merge them into the first entry
        merged = [p for p in planned if p["path"] == GITLAB_CI]
        merged[0]["content"] = "\n".join(jobs)
        planned = [p for p in planned if p["path"] != GITLAB_CI or p is merged[0]]
    if guardrail:
        planned.append({"path": SETTINGS_PATH, "gate": GUARDRAIL, "content": deny_settings(facts), "manual": None})
    return planned


def write_files(root: Path, planned: Iterable[PlannedFile]) -> list[FileOutcome]:
    """Write each planned file that does not exist; report each that does, unchanged."""
    out: list[FileOutcome] = []
    for item in planned:
        if not item["path"]:
            out.append({"path": "", "gate": item["gate"], "state": "manual", "manual": item["manual"],
                        "suggested": None})
            continue
        target = root / item["path"]
        if target.exists() or target.is_symlink():
            out.append({"path": item["path"], "gate": item["gate"], "state": "conflict",
                        "manual": f"`{item['path']}` exists and was left as it is; add the suggested text by hand",
                        "suggested": item["content"]})
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("x", encoding="utf-8", newline="\n") as fh:  # "x": never replace a file
            fh.write(item["content"])
        out.append({"path": item["path"], "gate": item["gate"], "state": "written", "manual": item["manual"],
                    "suggested": None})
    return out
