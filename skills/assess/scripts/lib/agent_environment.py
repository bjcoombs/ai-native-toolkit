"""Can an agent set up this repository and check its own work? (issue #514)

Cloud agents provision their own environment from repository files, and every
agent vendor names the same first properties: a committed setup, one command
that runs the checks, and an instruction file that names it. GitHub calls a
Copilot agent without ``copilot-setup-steps.yml`` "slow and unreliable", since it
discovers dependencies by trial and error; Codex reads AGENTS.md "to find
project-specific lint and test commands". Layer 5 scored only the CI server, so
a repository with complete CI and no way to bootstrap or check locally read as
Present. This block is the evidence for the Layer 5 cap that closes that gap.

Everything but ``ci_duration`` is static and deterministic, read from the files
at HEAD (``lib.command_index.repo_files``):

- ``setup``: committed setup an agent runs. ``copilot_setup_steps``
  (``.github/workflows/copilot-setup-steps.yml``, credited only when it passes
  the checks below), ``devcontainer`` (``.devcontainer.json``,
  ``.devcontainer/devcontainer.json`` or ``.devcontainer/<name>/devcontainer.json``),
  ``cursor_environment`` (``.cursor/environment.json``), ``session_start_hook``
  (a ``SessionStart`` hook in a tracked ``.claude/settings.json``, read from the
  ``agent_harness`` block), ``setup_script`` (``script/setup``,
  ``script/bootstrap``, ``bin/setup``, ``init.sh``) and ``setup_target`` (a
  Makefile or justfile ``setup`` / ``bootstrap`` target).
- ``check``: the test and check entry points the repository defines (package
  scripts, Makefile and justfile targets, tox, nox, pytest, go, cargo, maven,
  gradle), and the check commands each graded instruction file names, resolved
  with ``lib.command_resolver``; only the files an agent loads at session start
  count (``AGENT_LOADED``), not a review-bot prompt. ``named`` lists the ones that resolve. A check
  is classified by the target it runs (``make test``, ``npm run ci``, the
  program after ``uv run``), so an install command such as ``npm ci`` or
  ``uv sync --extra test`` is never a check.
- ``pinning``: one row per dependency manifest (a ``package.json`` or
  ``pyproject.toml`` that declares dependencies, ``Pipfile``,
  ``requirements*.txt``, ``go.mod``, ``Cargo.toml``, ``Gemfile``,
  ``composer.json``) outside the built-in and ``.assess/config.toml`` excludes,
  and the lockfile beside it or in a parent directory. A
  ``requirements*.txt`` counts as its own lock when every requirement is
  pinned with ``==``.
- ``findings``: a ``copilot-setup-steps.yml`` that breaks GitHub's documented
  constraints: one job named ``copilot-setup-steps``, only ``steps``,
  ``permissions``, ``runs-on``, ``services``, ``snapshot`` and
  ``timeout-minutes`` set on it (others are ignored), ``timeout-minutes`` at most
  59; and a file saved as ``.yaml``, which Copilot never reads.
- ``layer5_cap``: Present needs a check command an instruction file names, plus
  committed setup or a lockfile-backed bootstrap (every manifest locked, or no
  manifest to install). When either is missing the ceiling is Partial, with the
  unmet requirements and their evidence.

``ci_duration`` is the one network read: the median wall time of recent
successful push runs on the default branch, per workflow, through ``lib.gh_cli``
with a short per-call timeout. No remote, no ``gh``, no auth, offline or a
refused read gives ``{"available": False, "reason"}`` and never fails the run.
It is report evidence only: the 10-minute figure is published guidance with no
outcome evidence, so it caps nothing.

The workflow file is read line by line (the core ships no YAML parser): flow
style (``jobs: {...}``) and anchors are not followed and are reported as a
finding rather than guessed.
"""
from __future__ import annotations

import json
import re
import statistics
import tomllib
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any, Literal, NotRequired, TypedDict

from lib.assess_config import is_user_excluded
from lib.command_index import RepoIndex, build_index
from lib.command_resolver import RUNNERS, command_words, extract_commands, resolve
from lib.doc_graph import is_excluded_path
from lib.gh_cli import GhUnavailable, gh_json, open_github

SetupKind = Literal[
    "copilot_setup_steps", "devcontainer", "cursor_environment",
    "session_start_hook", "setup_script", "setup_target",
]
FindingKind = Literal[
    "copilot_wrong_extension", "copilot_unparsed", "copilot_no_jobs", "copilot_missing_job",
    "copilot_extra_job", "copilot_unsupported_key", "copilot_missing_steps",
    "copilot_timeout_over_limit", "copilot_timeout_not_integer",
]

COPILOT_PATH = ".github/workflows/copilot-setup-steps.yml"
COPILOT_JOB = "copilot-setup-steps"
COPILOT_ALLOWED_KEYS = frozenset({
    "steps", "permissions", "runs-on", "services", "snapshot", "timeout-minutes",
})
COPILOT_MAX_TIMEOUT = 59
SETUP_SCRIPTS = ("script/setup", "script/bootstrap", "bin/setup", "init.sh")
SETUP_TARGETS = frozenset({"setup", "bootstrap"})
CHECK_WORDS = frozenset({
    "test", "tests", "check", "checks", "verify", "ci", "validate", "pytest", "tox", "nox",
})
_ALWAYS_CHECK = frozenset({"pytest", "py.test", "tox", "nox"})
_PM_TEST = frozenset({"test", "t", "tst"})
_VALUED_FLAGS = frozenset({
    "-C", "-f", "--file", "--makefile", "--directory", "--dir", "--cwd", "--prefix", "--project",
    "--with", "--extra", "--group", "--only-group", "--python", "-p", "--package", "--from",
    "--filter", "-F", "--workspace", "-w", "-e", "-s", "--manifest-path",
})
# Instruction files a coding agent loads at session start. A bot prompt
# (`.github/claude-review-instructions.md`) or `docs/CLAUDE.md` is graded, but
# naming a check there does not put it in front of the agent doing the work.
AGENT_LOADED = frozenset({
    "CLAUDE.md", "AGENTS.md", "GEMINI.md", ".cursorrules", ".github/copilot-instructions.md",
})
CI_TARGET_SECONDS = 600
CI_RUN_LIMIT = 50
CI_TIMEOUT_SECONDS = 10
MAX_NAMED = 10

_NODE_LOCKS = ("package-lock.json", "npm-shrinkwrap.json", "yarn.lock", "pnpm-lock.yaml",
               "bun.lock", "bun.lockb")
_PY_LOCKS = ("uv.lock", "poetry.lock", "pdm.lock", "pylock.toml")
_LOCKS: dict[str, tuple[str, tuple[str, ...]]] = {
    "package.json": ("node", _NODE_LOCKS),
    "pyproject.toml": ("python", _PY_LOCKS),
    "Pipfile": ("python", ("Pipfile.lock",)),
    "go.mod": ("go", ("go.sum",)),
    "Cargo.toml": ("rust", ("Cargo.lock",)),
    "Gemfile": ("ruby", ("Gemfile.lock",)),
    "composer.json": ("php", ("composer.lock",)),
}
_REQUIREMENTS = re.compile(r"^requirements[\w.-]*\.txt$")
_KEY = re.compile(r"""^(?P<indent> *)(?P<key>[A-Za-z0-9_.-]+|"[^"]+"|'[^']+')\s*:(?:\s+(?P<value>.*))?$""")
_COMMENT = re.compile(r"(?:^|\s+)#.*$")


class SetupSource(TypedDict):
    kind: SetupKind
    path: str
    detail: str


class CheckEntry(TypedDict):
    command: str
    source: str


class NamedCheck(TypedDict):
    file: str
    line: int
    command: str
    verdict: str
    reason: str


class PinRow(TypedDict):
    ecosystem: str
    manifest: str
    locked: bool
    lockfile: str | None


class WorkflowDuration(TypedDict):
    workflow: str
    runs: int
    median_seconds: int


class EnvFinding(TypedDict):
    kind: FindingKind
    file: str
    line: int | None
    detail: str
    fix: str


class Layer5Cap(TypedDict):
    applies: bool
    ceiling: Literal["Partial"] | None
    unmet: list[str]
    reason: str


class SetupBlock(TypedDict):
    present: bool
    sources: list[SetupSource]


class CheckBlock(TypedDict):
    entry_points: list[CheckEntry]
    named: list[NamedCheck]
    named_total: int
    unresolved: list[NamedCheck]


class PinningBlock(TypedDict):
    manifests: list[PinRow]
    all_locked: bool | None


class CiDuration(TypedDict):
    """``available: false`` carries only ``reason``; a measurement carries the rest."""

    available: bool
    reason: NotRequired[str]
    branch: NotRequired[str]
    runs_sampled: NotRequired[int]
    slowest_median_seconds: NotRequired[int]
    target_seconds: NotRequired[int]
    over_target: NotRequired[bool]
    workflows: NotRequired[list[WorkflowDuration]]


class AgentEnvironmentBlock(TypedDict):
    """``run-context.json`` ``agent_environment``."""

    available: bool
    setup: SetupBlock
    check: CheckBlock
    pinning: PinningBlock
    ci_duration: CiDuration
    findings: list[EnvFinding]
    layer5_cap: Layer5Cap


def _read(root: Path, rel: str) -> str:
    try:
        return (root / rel).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


# --- copilot-setup-steps.yml ---------------------------------------------------


def _finding(kind: FindingKind, line: int | None, detail: str, fix: str,
             file: str = COPILOT_PATH) -> EnvFinding:
    return {"kind": kind, "file": file, "line": line, "detail": detail, "fix": fix}


def _keys(text: str) -> list[tuple[int, int, str, str]]:
    """``(line, indent, key, value)`` for every mapping-key line, comments dropped.

    A sequence item (``- run: x``) reads as its key two columns in. Lines inside
    a block scalar (``key: |`` or ``- run: |``) are skipped, so a script body
    never reads as keys.
    """
    out: list[tuple[int, int, str, str]] = []
    scalar_indent: int | None = None
    for lineno, raw in enumerate(text.splitlines(), 1):
        if not raw.strip():
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        if scalar_indent is not None:
            if indent > scalar_indent:
                continue
            scalar_indent = None
        line = _COMMENT.sub("", raw).rstrip()
        if line.lstrip(" ").startswith("- "):
            line = " " * (indent + 2) + line.lstrip(" ")[2:].lstrip(" ")
            indent += 2
        m = _KEY.match(line)
        if not m:
            continue
        value = (m.group("value") or "").strip()
        out.append((lineno, indent, m.group("key").strip("'\""), value))
        if value[:1] in ("|", ">"):
            scalar_indent = indent
    return out


def _children(keys: list[tuple[int, int, str, str]], start: int) -> list[tuple[int, int, str, str]]:
    """The direct child keys of ``keys[start]``: the next deeper indent, until a dedent."""
    parent_indent = keys[start][1]
    kids: list[tuple[int, int, str, str]] = []
    child_indent: int | None = None
    for row in keys[start + 1:]:
        if row[1] <= parent_indent:
            break
        if child_indent is None:
            child_indent = row[1]
        if row[1] == child_indent:
            kids.append(row)
    return kids


def check_copilot_setup(text: str) -> tuple[bool, list[EnvFinding]]:
    """Validate a ``copilot-setup-steps.yml`` body: ``(usable, findings)``.

    ``usable`` is True when the job exists, has steps and a timeout within the
    limit; an ignored key or an extra job is still a finding.
    """
    keys = _keys(text)
    jobs_at = next((i for i, k in enumerate(keys) if k[1] == 0 and k[2] == "jobs"), None)
    if jobs_at is None:
        return False, [_finding("copilot_no_jobs", None, "no top-level `jobs:` key",
                                f"Add a `jobs:` map with one `{COPILOT_JOB}` job.")]
    if keys[jobs_at][3] and keys[jobs_at][3][0] in "{&*":
        return False, [_finding(
            "copilot_unparsed", keys[jobs_at][0],
            "`jobs:` is written in flow style or through an anchor, which this check does not read",
            "Write `jobs:` as a block mapping so the job and its keys can be checked.")]
    findings: list[EnvFinding] = []
    jobs = _children(keys, jobs_at)
    for line, _, name, _ in jobs:
        if name != COPILOT_JOB:
            findings.append(_finding(
                "copilot_extra_job", line, f"job `{name}` beside `{COPILOT_JOB}`",
                f"Keep only the `{COPILOT_JOB}` job; GitHub documents the file as a single job."))
    job_at = next((i for i, k in enumerate(keys) if k in jobs and k[2] == COPILOT_JOB), None)
    if job_at is None:
        findings.append(_finding(
            "copilot_missing_job", keys[jobs_at][0], f"no job named `{COPILOT_JOB}`",
            f"Name the job `{COPILOT_JOB}`; Copilot ignores a job with any other name."))
        return False, findings
    usable = True
    job_keys = _children(keys, job_at)
    names = {k[2] for k in job_keys}
    for line, _, key, value in job_keys:
        if key not in COPILOT_ALLOWED_KEYS:
            findings.append(_finding(
                "copilot_unsupported_key", line, f"`{key}` is set on the job; Copilot ignores it",
                "Remove it; only steps, permissions, runs-on, services, snapshot and "
                "timeout-minutes apply."))
        if key == "timeout-minutes":
            if not re.fullmatch(r"\d+", value):
                usable = False
                findings.append(_finding(
                    "copilot_timeout_not_integer", line, f"`timeout-minutes: {value}` is not a whole number",
                    f"Set a literal number of minutes, at most {COPILOT_MAX_TIMEOUT}."))
            elif int(value) > COPILOT_MAX_TIMEOUT:
                usable = False
                findings.append(_finding(
                    "copilot_timeout_over_limit", line,
                    f"`timeout-minutes: {value}` exceeds GitHub's limit of {COPILOT_MAX_TIMEOUT}",
                    f"Lower it to {COPILOT_MAX_TIMEOUT} or less."))
    if "steps" not in names:
        usable = False
        findings.append(_finding(
            "copilot_missing_steps", keys[job_at][0], f"the `{COPILOT_JOB}` job has no `steps`",
            "Add the steps that install the project's dependencies."))
    return usable, findings


# --- setup -------------------------------------------------------------------


def _session_start(harness: Any, index: RepoIndex) -> list[SetupSource]:
    """SessionStart hooks in the tracked ``.claude/settings.json``, from ``agent_harness``."""
    if ".claude/settings.json" not in index.files:
        return []
    rows = harness.get("executes") if isinstance(harness, dict) else None
    if not isinstance(rows, list):
        data = _settings_hooks(index.root)
        return [{"kind": "session_start_hook", "path": ".claude/settings.json",
                 "detail": "SessionStart hook"}] if data else []
    return [
        {"kind": "session_start_hook", "path": ".claude/settings.json",
         "detail": str(r.get("detail") or "SessionStart hook")}
        for r in rows
        if isinstance(r, dict) and r.get("kind") == "settings_hook"
        and r.get("file") == ".claude/settings.json"
        and str(r.get("name", "")).split(" [")[0] == "SessionStart"
    ]


def _settings_hooks(root: Path) -> bool:
    """Fallback when ``agent_harness`` did not run: does settings carry a SessionStart hook?"""
    try:
        data = json.loads(_read(root, ".claude/settings.json") or "{}")
    except json.JSONDecodeError:
        return False
    hooks = data.get("hooks") if isinstance(data, dict) else None
    return isinstance(hooks, dict) and bool(hooks.get("SessionStart"))


def _setup(index: RepoIndex, harness: Any) -> tuple[list[SetupSource], list[EnvFinding]]:
    sources: list[SetupSource] = []
    findings: list[EnvFinding] = []
    if COPILOT_PATH in index.files:
        usable, problems = check_copilot_setup(_read(index.root, COPILOT_PATH))
        findings.extend(problems)
        if usable:
            sources.append({"kind": "copilot_setup_steps", "path": COPILOT_PATH,
                            "detail": f"`{COPILOT_JOB}` job"})
    yaml_variant = COPILOT_PATH.removesuffix(".yml") + ".yaml"
    if yaml_variant in index.files:
        findings.append(_finding(
            "copilot_wrong_extension", None, "saved as `.yaml`; Copilot reads only the `.yml` path",
            f"Rename it to `{COPILOT_PATH}`.", file=yaml_variant))
    for rel in sorted(index.files):
        p = PurePosixPath(rel)
        if rel == ".devcontainer.json" or (
            p.name == "devcontainer.json" and p.parts[0] == ".devcontainer" and len(p.parts) <= 3
        ):
            sources.append({"kind": "devcontainer", "path": rel, "detail": "dev container"})
    if ".cursor/environment.json" in index.files:
        sources.append({"kind": "cursor_environment", "path": ".cursor/environment.json",
                        "detail": "Cursor cloud agent environment"})
    sources.extend(_session_start(harness, index))
    for rel in SETUP_SCRIPTS:
        if rel in index.files:
            sources.append({"kind": "setup_script", "path": rel, "detail": "setup script"})
    for family, runner, config in ((index.make_targets, "make", "Makefile"),
                                   (index.just_recipes, "just", "justfile")):
        for name in sorted(SETUP_TARGETS & (family.names if family else frozenset())):
            sources.append({"kind": "setup_target", "path": config, "detail": f"`{runner} {name}`"})
    return sources, findings


# --- check entry points --------------------------------------------------------


def _is_check_name(name: str) -> bool:
    return any(part in CHECK_WORDS for part in re.split(r"[:_./-]", name.lower()))


def _positionals(args: list[str]) -> list[str]:
    """Non-flag words, with the value of a known valued flag (``--with pytest``) dropped."""
    out: list[str] = []
    skip = False
    for word in args:
        if skip:
            skip = False
        elif word.startswith("-"):
            skip = word in _VALUED_FLAGS
        else:
            out.append(word)
    return out


def _drop_leading_flags(args: list[str]) -> list[str]:
    """``args`` from the first positional on: the program ``uv run --with x`` runs."""
    i = 0
    while i < len(args) and args[i].startswith("-"):
        i += 2 if args[i] in _VALUED_FLAGS else 1
    return args[i:]


def _check_target(words: list[str]) -> bool:
    """Whether the program, subcommand or target a command runs is a check."""
    head, pos = words[0], _positionals(words[1:])
    if head in _ALWAYS_CHECK:
        return True
    if head in ("uv", "poetry", "uvx"):
        rest = words[1:]
        if head != "uvx":
            if not pos or pos[0] != "run":
                return False  # sync, install, add: setup, not a check
            rest = rest[rest.index("run") + 1:]
        program = _drop_leading_flags(rest)
        return bool(program) and _check_target(program)
    if head in ("npm", "pnpm", "yarn", "bun"):
        if pos and pos[0] in ("run", "run-script"):
            return len(pos) > 1 and _is_check_name(pos[1])
        # Built-in subcommands: only the test aliases check; `npm ci` installs.
        return bool(pos) and pos[0] in _PM_TEST
    if head in ("python", "python3"):
        if "-m" in words:
            module = words[words.index("-m") + 1:words.index("-m") + 2]
            return bool(module) and module[0] in _ALWAYS_CHECK
        return bool(pos) and _is_check_name(PurePosixPath(pos[0]).name)
    if head in ("bash", "sh", "zsh", "source"):
        return bool(pos) and _is_check_name(PurePosixPath(pos[0]).name)
    if head.startswith("./") and head not in RUNNERS:
        return _is_check_name(PurePosixPath(head).name)
    # make, just, go, cargo, mvn, gradle: the first target or goal names the work.
    return bool(pos) and _is_check_name(pos[0])


def is_check_command(text: str) -> bool:
    """A command an agent runs to check its work: a check target of a modelled runner or script."""
    words = command_words(text)
    if not words:
        return False
    head = words[0]
    if head not in RUNNERS and not head.startswith("./"):
        return False
    return _check_target(words)


def _entry_points(index: RepoIndex) -> list[CheckEntry]:
    out: list[CheckEntry] = []
    families = ((index.npm_scripts, "npm run", "package.json"),
                (index.make_targets, "make", "Makefile"),
                (index.just_recipes, "just", "justfile"),
                (index.tox_envs, "tox -e", "tox"),
                (index.nox_sessions, "nox -s", "noxfile.py"))
    for family, runner, source in families:
        for name in sorted(n for n in (family.names if family else frozenset()) if _is_check_name(n)):
            out.append({"command": f"{runner} {name}", "source": source})
    if index.tox_envs is not None:
        out.append({"command": "tox", "source": "tox"})
    if index.pytest_present:
        out.append({"command": "pytest", "source": "pytest configuration"})
    for basename, command in (("go.mod", "go test ./..."), ("Cargo.toml", "cargo test"),
                              ("pom.xml", "mvn verify")):
        if index.has_basename(basename):
            out.append({"command": command, "source": basename})
    if index.has_basename("build.gradle") or index.has_basename("build.gradle.kts"):
        out.append({"command": "./gradlew check" if index.has_basename("gradlew") else "gradle check",
                    "source": "Gradle build"})
    return out


def _named(index: RepoIndex, instruction_files: Any) -> list[NamedCheck]:
    rows: list[NamedCheck] = []
    files = sorted(instruction_files) if isinstance(instruction_files, dict) else []
    for rel in (f for f in files if f in AGENT_LOADED):
        for cmd in extract_commands(_read(index.root, rel)):
            if not is_check_command(cmd.text):
                continue
            res = resolve(cmd, index=index)
            rows.append({"file": rel, "line": cmd.line, "command": cmd.text,
                         "verdict": res.verdict, "reason": res.reason})
    return rows


# --- pinning -------------------------------------------------------------------


def _declares_deps(root: Path, rel: str) -> bool:
    """A ``package.json`` or ``pyproject.toml`` that declares installable dependencies.

    A manifest that only holds scripts or tool configuration has nothing to lock;
    one that does not parse is skipped. Other manifests always count.
    """
    name = PurePosixPath(rel).name
    if name == "package.json":
        try:
            pkg = json.loads(_read(root, rel) or "{}")
        except json.JSONDecodeError:
            return False
        return isinstance(pkg, dict) and any(
            pkg.get(k) for k in ("dependencies", "devDependencies", "optionalDependencies",
                                 "peerDependencies"))
    if name == "go.mod":
        # Go writes no go.sum for a module that requires nothing.
        return re.search(r"^\s*require\b", _read(root, rel), re.MULTILINE) is not None
    if name != "pyproject.toml":
        return True
    try:
        data = tomllib.loads(_read(root, rel))
    except tomllib.TOMLDecodeError:
        return False
    project = data.get("project")
    project = project if isinstance(project, dict) else {}
    tool = data.get("tool")
    poetry = tool.get("poetry") if isinstance(tool, dict) else None
    return bool(project.get("dependencies") or project.get("optional-dependencies")
                or data.get("dependency-groups")
                or (isinstance(poetry, dict) and poetry.get("dependencies")))


def _requirement_lines(text: str) -> list[str]:
    """Direct requirements, without comments, options and ``-r`` / ``-c`` includes."""
    reqs = [ln.split("#")[0].strip() for ln in text.splitlines()]
    return [r for r in reqs if r and not r.startswith("-")]


def _requirements_pinned(text: str) -> bool:
    reqs = _requirement_lines(text)
    return bool(reqs) and all("==" in r for r in reqs)


def _lock_near(index: RepoIndex, rel: str, locks: tuple[str, ...]) -> str | None:
    """The first lockfile in the manifest's directory or a parent (a workspace root)."""
    parent = PurePosixPath(rel).parent
    while True:
        for name in locks:
            cand = str(parent / name) if str(parent) != "." else name
            if cand in index.files:
                return cand
        if str(parent) in (".", ""):
            return None
        parent = parent.parent


def _pinning(index: RepoIndex, excludes: tuple[set[str], list[str]] | None) -> list[PinRow]:
    dirs, pats = excludes if excludes else (set(), [])
    rows: list[PinRow] = []
    for rel in sorted(index.files):
        p = PurePosixPath(rel)
        if is_excluded_path(Path(rel)) or is_user_excluded(Path(rel), dirs, pats):
            continue
        if p.name in _LOCKS:
            if not _declares_deps(index.root, rel):
                continue
            ecosystem, locks = _LOCKS[p.name]
            lock = _lock_near(index, rel, locks)
            rows.append({"ecosystem": ecosystem, "manifest": rel, "locked": lock is not None,
                         "lockfile": lock})
        elif _REQUIREMENTS.match(p.name):
            text = _read(index.root, rel)
            if not _requirement_lines(text):
                continue  # only `-r` / `-c` includes or options: nothing to pin here
            pinned = _requirements_pinned(text)
            rows.append({"ecosystem": "python", "manifest": rel, "locked": pinned,
                         "lockfile": rel if pinned else None})
    return rows


# --- CI duration (network, optional) -------------------------------------------


def _seconds(run: dict[str, Any]) -> float | None:
    try:
        start = datetime.fromisoformat(str(run["startedAt"]).replace("Z", "+00:00"))
        end = datetime.fromisoformat(str(run["updatedAt"]).replace("Z", "+00:00"))
    except (KeyError, ValueError):
        return None
    secs = (end - start).total_seconds()
    return secs if secs >= 0 else None


def ci_duration(repo_root: Path) -> CiDuration:
    """Median wall time per workflow over recent successful push runs on the default branch."""
    try:
        repo = open_github(repo_root, CI_TIMEOUT_SECONDS)
        info = gh_json(["repo", "view", repo.slug, "--json", "defaultBranchRef"], CI_TIMEOUT_SECONDS)
        branch = (info.get("defaultBranchRef") or {}).get("name") if isinstance(info, dict) else None
        if not isinstance(branch, str) or not branch:
            return {"available": False, "reason": "gh_bad_json: no default branch in `gh repo view`"}
        runs = gh_json([
            "run", "list", "--repo", repo.slug, "--branch", branch, "--status", "success",
            "--event", "push", "--limit", str(CI_RUN_LIMIT),
            "--json", "workflowName,startedAt,updatedAt",
        ], CI_TIMEOUT_SECONDS)
    except GhUnavailable as e:
        return {"available": False, "reason": e.reason}
    by_workflow: dict[str, list[float]] = {}
    for run in runs if isinstance(runs, list) else []:
        secs = _seconds(run) if isinstance(run, dict) else None
        if secs is not None:
            by_workflow.setdefault(str(run.get("workflowName") or "?"), []).append(secs)
    if not by_workflow:
        return {"available": False,
                "reason": f"no_runs: no successful push runs on `{branch}` in the last {CI_RUN_LIMIT}"}
    workflows: list[WorkflowDuration] = sorted(
        ({"workflow": name, "runs": len(v), "median_seconds": round(statistics.median(v))}
         for name, v in by_workflow.items()),
        key=lambda w: (-w["median_seconds"], w["workflow"]),
    )
    slowest = workflows[0]["median_seconds"]
    return {
        "available": True, "branch": branch, "runs_sampled": sum(len(v) for v in by_workflow.values()),
        "slowest_median_seconds": slowest, "target_seconds": CI_TARGET_SECONDS,
        "over_target": slowest > CI_TARGET_SECONDS, "workflows": workflows,
    }


# --- assembly ------------------------------------------------------------------


def layer5_cap(named: list[NamedCheck], setup: list[SetupSource], pins: list[PinRow]) -> Layer5Cap:
    """Partial ceiling unless a named check resolves and setup or a locked bootstrap exists."""
    unmet: list[str] = []
    resolved = [n for n in named if n["verdict"] == "resolved"]
    if not resolved:
        unmet.append("check_named")
    locked_bootstrap = all(p["locked"] for p in pins)
    if not setup and not locked_bootstrap:
        unmet.append("setup_or_lockfile")
    parts: list[str] = []
    if "check_named" in unmet:
        parts.append("no instruction file names a check command that resolves"
                     + (f" ({len(named)} named, none resolved)" if named else ""))
    if "setup_or_lockfile" in unmet:
        unlocked = [p["manifest"] for p in pins if not p["locked"]]
        parts.append("no committed setup and no lockfile for " + ", ".join(f"`{m}`" for m in unlocked))
    if not unmet:
        how = (f"setup: {', '.join(s['path'] for s in setup)}" if setup
               else "every manifest locked" if pins else "no dependency manifest to install")
        reason = f"`{resolved[0]['command']}` named in {resolved[0]['file']}:{resolved[0]['line']}; {how}"
    else:
        reason = "; ".join(parts)
    return {"applies": bool(unmet), "ceiling": "Partial" if unmet else None,
            "unmet": unmet, "reason": reason}


def scan_agent_environment(
    repo_root: Path, instruction_files: Any = None, agent_harness: Any = None,
    excludes: tuple[set[str], list[str]] | None = None, *, probe_ci: bool = True,
) -> AgentEnvironmentBlock:
    """Build the ``agent_environment`` block for the repository at ``repo_root``."""
    index = build_index(repo_root)
    setup, findings = _setup(index, agent_harness)
    named = _named(index, instruction_files)
    pins = _pinning(index, excludes)
    resolved = [n for n in named if n["verdict"] == "resolved"]
    return {
        "available": True,
        "setup": {"present": bool(setup), "sources": setup},
        "check": {"entry_points": _entry_points(index), "named": resolved[:MAX_NAMED],
                  "named_total": len(resolved),
                  "unresolved": [n for n in named if n["verdict"] != "resolved"][:MAX_NAMED]},
        "pinning": {"manifests": pins, "all_locked": all(p["locked"] for p in pins) if pins else None},
        "ci_duration": ci_duration(repo_root) if probe_ci else {
            "available": False, "reason": "not_probed: the CI-duration probe was turned off"},
        "findings": findings,
        "layer5_cap": layer5_cap(named, setup, pins),
    }
