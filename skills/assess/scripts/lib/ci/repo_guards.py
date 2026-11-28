"""Ratchets and repo-owned guards that CI runs, and whether each guard is itself tested (issue #854).

Theme 9 of #826. ``scan_repo_guards(repo_root)`` reads the committed CI files (GitHub Actions
workflows directly under ``.github/workflows/`` and ``.gitlab-ci.yml``) job by job through their
unmasked shell lines (``lib.ci.ci_job_commands.job_lines``, the #775 masking rule), and follows
each step into the repository through the shared CI-step follower (``lib.ci.ci_step_follower``:
a committed script, a ``make`` recipe, a local action or reusable workflow, up to
``_MAX_FOLLOW`` levels). A leading ``cd <dir>`` on a line sets the folder its later commands'
scripts and paths are read from, as the follower reads it. Nothing is executed. It returns the ``repo_guards`` block, one
``guards`` entry per CI file, kind and guard:

- **lint_baseline.** A lint or type-check command (``lib.ci.check_command.is_lint_command``), a
  known baseline tool (``mypy-baseline``, ``betterer``, ``phpstan``, ``psalm``, ``detekt``, ...)
  or a committed check script names a tracked baseline file (a name holding ``baseline`` or
  ``ratchet``, or a known one such as ``.rubocop_todo.yml``), as an argument or, for a committed
  script, in its own text. It counts only with a prune or update command: a tracked command
  line (a make recipe, ``justfile``, ``package.json``, ``pyproject.toml``, ``tox.ini``, a shell
  script, a CI file, or the check script itself) that names the baseline or the check script
  beside an update word (``--update``, ``--generate-baseline``, ``prune``, ``sync`` ...) in the
  same command, a whole-line comment never counting. One
  with no such command is counted in ``baselines_without_update`` and is not a guard.
- **boundary_checker.** A step runs a known boundary tool (``import-boss``, ``lint-imports``,
  ``tach check``, ``depcruise``, ``go-arch-lint``, ``arch-go``, ``deptrac``, ``hassfest``), or a
  committed script whose name pairs a check word (``check``, ``verify``, ``lint``, ``guard``,
  ``enforce``, ``validate``) with a boundary word (``imports``, ``layers``, ``boundaries``,
  ``architecture``, ``cycles``): ``hack/verify-imports.sh``, ``scripts/check_layers.py``. An
  architecture test (a tracked test file with ``arch`` in its name that imports ArchUnit,
  ArchUnitNET, pytestarch, pytest-archon or ts-arch) counts when an ordinary CI job runs a test
  command.
- **size_cap.** A committed check script whose name names a file-size cap (``file_size``,
  ``max_lines``, ``line_count``, ``size_limit`` ...), or a baseline or ratchet file whose name
  does, read by a check: ``.file-size-ratchet.toml`` read by ``scripts/check_sizes.py``.

What never counts: a command behind a passing ``||`` fallback (``|| true``) and what it runs, a
GitLab ``after_script:``, an install (``pip install import-linter``), a ``*baseline*`` file no CI
step reads, and a baseline or script under a benchmark or performance path
(``bench``, ``benchmark``, ``perf``). A workflow that runs only off the ordinary triggers
(``workflow_dispatch``, ``schedule``; ``lib.ci.ci_triggers``) guards no change and is not
read for guards. A guard found only in a workflow or GitLab job whose trigger cannot be
settled is ``unknown_trigger``: it is listed but does not make ``present`` true.

``present`` is true when any guard on an ordinary trigger is found. With none: a guard on an
unsettled trigger reads null (``trigger_unknown``); a tracked CI file of a system this reader
does not parse (``lib.unread_ci``), a ``.gitlab-ci.yml`` with a top-level ``include:``, or a
step the follower could not settle (a remote reusable workflow, an unread Gradle script, a
``run:`` expression ``lib.ci.ci_expressions`` leaves unsettled) reads
null (``ci_not_read``); otherwise false.

**Guard is itself tested** (#1391 scope). A guard that is a committed script (``script``) has
its own tests when a tracked test file named for it (``test_<stem>.py``, ``<stem>_test.py``,
``<stem>_test.go``, ``test_<stem>.sh``, ``<stem>.bats``, ``<stem>.test.ts`` ...; ``-`` and ``_``
read alike) names the stem in its text, or, for a guard that is a folder (``go run
./hack/tools/importcheck``, ``python -m script.hassfest``), when a test file sits in that folder
or in ``tests/<name>/``. ``tested_by`` lists them. ``guard_tested`` is true when any guard script
has a test, false when guards are scripts and none has one or when ``present`` is false, and
null when ``present`` is null (its reason) or when every guard found is a third-party tool or an
architecture test with no script of its own (``no_guard_script``). The default maturity model's
``repo-guards.1`` names ``repo-guard-tested.1`` as its ``guarded_by`` guard.

``guards_or_unknown`` is the collector: a reader fault reads null (``scan_failed``) for both.
Full reference: lib/docs/repo_guards.md (known limits).

Inward-only imports; never an orchestrator. Tests: ``tests/test_repo_guards.py``.
"""
from __future__ import annotations

import posixpath
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Literal, NotRequired, TypedDict

from lib.ci.check_command import is_check_command, is_lint_command
from lib.ci.ci_action_inputs import ReadLocal
from lib.ci.ci_job_commands import OPERATOR, commands, installs, job_lines, program
from lib.ci.ci_step_follower import StepFollower, follow_workflow
from lib.ci.ci_step_paths import local_reader
from lib.ci.ci_triggers import Trigger, gitlab_line_triggers, resolve_workflows, workflow_trigger
from lib.instructions.command_index import CI_CONFIG_FILES, ci_config_files, ci_line_text, read_config, repo_files
from lib.unread_ci import unread_ci_paths

GuardKind = Literal["lint_baseline", "boundary_checker", "size_cap"]
KINDS: tuple[GuardKind, ...] = ("lint_baseline", "boundary_checker", "size_cap")

REASON_CI_NOT_READ = "ci_not_read"
REASON_TRIGGER_UNKNOWN = "trigger_unknown"
REASON_NO_GUARD_SCRIPT = "no_guard_script"
REASON_SCAN_FAILED = "scan_failed"

_MAX_FOLLOW = 3
_INCLUDE = re.compile(r"^include\s*:", re.M)
# A job-level ``uses:`` of a reusable workflow in another repository: its steps are not read.
_REMOTE_JOB = re.compile(r"^\s*uses:\s*['\"]?(?!\./)[^\s'\"]+/\.github/workflows/", re.M)
_BOUNDARY_TOOLS = frozenset({"import-boss", "lint-imports", "depcruise", "dependency-cruiser", "go-arch-lint",
                             "arch-go", "deptrac", "hassfest"})
_BASELINE_TOOLS = frozenset({"mypy-baseline", "betterer", "phpstan", "psalm", "detekt", "detekt-cli", "ktlint",
                             "swiftlint", "basedpyright", "pyright", "rubocop", "pylint", "eslint"})
_KNOWN_BASELINES = frozenset({".rubocop_todo.yml", ".betterer.results"})
_INTERPRETERS = frozenset({"node", "ruby", "bash", "sh", "zsh", "dash", "perl", "php", "bun", "deno", "tsx",
                           "ts-node", "python", "python3"})
# Matched inside a name token too (``importcheck``); ``arch`` only as a whole token (not ``search``).
_CHECK_PARTS = ("check", "verify", "lint", "guard", "enforce", "validate", "ratchet")
_BOUNDARY_PARTS = ("import", "boundar", "architecture", "circular")
_BOUNDARY_STARTS = ("layer", "cycle")   # at a token's start only: not ``player``, ``lifecycle``
_SIZE = re.compile(r"(?:^|_)(?:file_?sizes?|file_?lengths?|line_?counts?|max_?lines|long_?files|"
                   r"sizes?_(?:cap|limit|ratchet)s?|loc_(?:cap|limit)s?)(?:_|$)")
_BENCH = re.compile(r"(?:^|[/_.-])(?:bench|benchmarks?|perf|performance)(?:[/_.-]|$)", re.I)
# ``(?<!no-)``: a negated flag (``--no-sync``, ``--no-update``) is not an update command
_UPDATE = re.compile(r"(?:^|[\s\"'=:/-])-{0,2}(?<!no-)(?<!no_)(?:update|prune|regen|regenerate|refresh|writebaseline|write|"
                     r"generate|sync|set-baseline|create-baseline)(?![\w])", re.I)
_HOLDERS = frozenset({"Makefile", "GNUmakefile", "makefile", "justfile", "Justfile", "package.json",
                      "pyproject.toml", "tox.ini", "noxfile.py", "Taskfile.yml", "Taskfile.yaml"})
_ARCH_MARKERS = ("com.tngtech.archunit", "ArchUnitNET", "pytestarch", "pytest_archon", "archrule", "tsarch")
# ``ArchitectureTest``, ``arch_test``, ``LayeredArchTest``; not ``SearchTest``
_ARCH_NAME = re.compile(r"(?:^|[^A-Za-z])[Aa]rch|(?<=[a-z])Arch")
_TEST_PARTS = frozenset({"test", "tests", "spec", "specs", "__tests__"})
_CODE_EXT = (".java", ".kt", ".cs", ".py", ".ts", ".js")


class RepoGuard(TypedDict):
    """One ratchet or repo-owned guard CI runs."""

    path: str              # the CI file whose job runs it
    kind: GuardKind
    via: str               # a known tool's name, ``lint`` (another lint command), ``script`` or ``archunit``
    target: str            # the baseline file, script, folder or architecture test; "" for a bare tool
    script: str | None     # the committed guard script or folder, None for a tool or a test
    trigger: Trigger       # the trigger of the job that runs it
    tested_by: list[str]   # tracked test files of ``script``


class RepoGuardsBlock(TypedDict):
    """``repo_guards`` (module docstring)."""

    files_read: int
    lint_baseline: int
    boundary_checker: int
    size_cap: int
    baselines_without_update: int
    present: bool | None
    present_reason: NotRequired[str]
    guard_scripts: int
    guard_scripts_tested: int
    guard_tested: bool | None
    guard_tested_reason: NotRequired[str]
    guards: list[RepoGuard]


class _Found(TypedDict):
    kind: GuardKind
    via: str
    target: str
    script: str | None


def _stem(rel: str) -> str:
    """The file or folder name without its extensions (``.file-size-ratchet.toml`` reads
    ``file-size-ratchet``)."""
    return posixpath.basename(rel.rstrip("/")).lstrip(".").split(".", 1)[0]


def _tokens(rel: str) -> list[str]:
    return [t for t in re.split(r"[^a-z0-9]+", _stem(rel).lower()) if t]


def _check_name_kind(rel: str) -> GuardKind | None:
    """The guard kind a committed script's name says (module docstring), or None."""
    tokens = _tokens(rel)
    joined = "_".join(tokens)
    if "hassfest" in tokens or joined.startswith("import_boss"):
        return "boundary_checker"
    checks = _checks(tokens)
    if checks and _SIZE.search(joined):
        return "size_cap"
    if checks and ("arch" in tokens or any(p in t for t in tokens for p in _BOUNDARY_PARTS)
                   or any(t.startswith(_BOUNDARY_STARTS) for t in tokens)):
        return "boundary_checker"
    return None


def _checks(tokens: list[str]) -> bool:
    """A name token holds a check word (``check``, ``verify``, ``importcheck``)."""
    return any(p in t for t in tokens for p in _CHECK_PARTS)


def _is_baseline(rel: str) -> bool:
    name = posixpath.basename(rel).lower()
    return (name in _KNOWN_BASELINES or "baseline" in name or "ratchet" in name) and not _BENCH.search(rel)


def _baseline_kind(rel: str) -> GuardKind:
    return "size_cap" if _SIZE.search("_".join(_tokens(rel))) else "lint_baseline"


def _arg_paths(args: Iterable[str]) -> list[str]:
    """Each literal path an argument names (``--baseline=x.json`` reads ``x.json``)."""
    out: list[str] = []
    for arg in args:
        value = arg.split("=", 1)[1] if arg.startswith("-") and "=" in arg else arg
        value = value.strip("'\"")
        if not value or value.startswith(("-", "/", "~")) or "$" in value:
            continue
        rel = posixpath.normpath(value.removeprefix("./"))
        if rel not in (".", "") and not rel.startswith(".."):
            out.append(rel)
    return out


class _Reader:
    def __init__(self, repo: Path, tracked: frozenset[str]) -> None:
        self.tracked = tracked
        self.read_local: ReadLocal = local_reader(repo, tracked)
        self.follower = StepFollower(self.read_local, tracked)
        self.unknown = False      # a followed step left something unread
        self.runs_tests = False   # an ordinary job runs a test command
        self.no_update: set[str] = set()
        self._texts: dict[str, str] = {}
        self._holder_cache: list[str] | None = None
        self._sorted = sorted(tracked)
        self._baselines = [r for r in self._sorted if _is_baseline(r)]

    def text(self, rel: str) -> str:
        """A tracked file's text, read once."""
        if rel not in self._texts:
            self._texts[rel] = self.read_local(rel) or ""
        return self._texts[rel]

    # --- what one command runs -------------------------------------------------------------

    def _folder(self, rel: str) -> bool:
        return any(t.startswith(rel + "/") for t in self.tracked)

    def _module(self, base: str) -> str | None:
        """The file (``x/y.py``) or package folder (``x/y``) ``python -m x.y`` runs, from its
        path form ``x/y``."""
        if f"{base}.py" in self.tracked:
            return f"{base}.py"
        return base if f"{base}/__main__.py" in self.tracked else None

    def script_of(self, words: list[str], cwd: str | None = "") -> str | None:
        """The committed script or folder a command runs from ``cwd``, or None (a ``cd`` that
        left what the reader can settle passes None)."""
        prog = program(words)
        if not prog or cwd is None:
            return None
        first = _at(cwd, prog[0])
        if first is not None and first in self.tracked:
            return first
        flag = words.index("-m") if "-m" in words else -1
        if 0 <= flag < len(words) - 1 and words[flag + 1] == prog[0]:
            base = _at(cwd, prog[0].replace(".", "/"))
            return self._module(base) if base is not None else None
        head = posixpath.basename(prog[0])
        if head == "go" and prog[1:2] == ["run"]:
            target = next((w for w in prog[2:] if not w.startswith("-")), "")
            rel = _at(cwd, target) if target.startswith("./") else None
            return rel if rel and self._folder(rel) else None
        if head in _INTERPRETERS:
            target = next((w for w in prog[1:] if not w.startswith("-")), "")
            rel = _at(cwd, target) if target else None
            return rel if rel is not None and rel in self.tracked else None
        return None

    def classify(self, words: list[str], cwd: str | None = "") -> list[_Found]:
        """The guards one unmasked command runs from ``cwd`` (module docstring)."""
        if installs(words):
            return []
        prog = program(words)
        if not prog:
            return []
        head = posixpath.basename(prog[0])
        found: list[_Found] = []
        if head in _BOUNDARY_TOOLS or (head == "tach" and "check" in prog[1:]):
            found.append(_Found(kind="boundary_checker", via=head, target="", script=None))
        script = self.script_of(words, cwd)
        if script is not None and _BENCH.search(script):
            return found
        text = " ".join(words)
        reads_baseline = (is_lint_command(text) or head in _BASELINE_TOOLS
                          or (script is not None and _checks(_tokens(script))))
        args = [a for a in (_at(cwd, p) for p in _arg_paths(prog[1:])) if a is not None] if cwd is not None else []
        named = [r for r in args if r in self.tracked and _is_baseline(r) and r != script]
        if script is not None and script in self.tracked and reads_baseline:
            body = self.text(script)
            named += [r for r in self._baselines if r != script and r not in named
                      and posixpath.basename(r) in body]
        if reads_baseline:
            for rel in named:
                kind = _baseline_kind(rel)
                if kind == "lint_baseline" and not self._updated(rel, script, head):
                    self.no_update.add(rel)
                    continue
                via = "script" if script else head if head in _BASELINE_TOOLS else "lint"
                found.append(_Found(kind=kind, via=via, target=rel, script=script))
        # a check script named for a boundary or size cap keeps that credit when no baseline it
        # reads counted (each lacked an update command)
        counted = any(f["target"] in named for f in found)
        named_kind = _check_name_kind(script) if script is not None and not counted else None
        if script is not None and named_kind is not None:
            found.append(_Found(kind=named_kind, via="script", target=script, script=script))
        return found

    def _updated(self, baseline: str, script: str | None, tool: str) -> bool:
        """A tracked command line names the baseline (or its check script) beside an update word."""
        names = {posixpath.basename(baseline), _stem(baseline)}
        if script is not None:
            names.add(posixpath.basename(script))
            # the check script has its own update mode for the file it reads: one code line
            # names both, never a comment (``# never update x.json by hand``)
            if script in self.tracked and any(posixpath.basename(baseline) in part and _UPDATE.search(part)
                                              for part in _segments(self.text(script))):
                return True
        # one command names both: the CI line that reads the baseline after ``uv sync &&`` does not
        for rel in self._holders():
            for line in _segments(self.text(rel)):
                if any(n and n in line for n in names) and _UPDATE.search(line):
                    return True
                if tool in _BASELINE_TOOLS and tool in line.split() and _UPDATE.search(line) and "baseline" in line:
                    return True
        return False

    def _holders(self) -> list[str]:
        if self._holder_cache is None:
            self._holder_cache = sorted(
                r for r in self.tracked
                if posixpath.basename(r) in _HOLDERS or r.endswith((".mk", ".sh")) or r in CI_CONFIG_FILES
                or (r.startswith(".github/workflows/") and r.endswith((".yml", ".yaml"))))
        return self._holder_cache

    # --- one job -----------------------------------------------------------------------------

    def job(self, lines: list[str], ordinary: bool) -> list[_Found]:
        found: list[_Found] = []
        self._sequence(lines, found, (), 0, ordinary)
        return found

    def _sequence(self, lines: list[str], found: list[_Found], path: tuple[str, ...], depth: int,
                  ordinary: bool) -> None:
        for line in lines:
            cmds = list(commands(ci_line_text(line)))
            cwd: str | None = ""   # a leading ``cd`` holds for the rest of the line, as the follower reads it
            for cmd in cmds:
                if cmd["words"][:1] == ["cd"]:
                    cwd = StepFollower._cd(cwd, cmd["words"])
                    continue
                if cmd["masked"]:
                    continue
                found.extend(self.classify(cmd["words"], cwd))
                if ordinary and is_check_command(" ".join(cmd["words"])):
                    self.runs_tests = True
            if depth >= _MAX_FOLLOW or any(c["masked"] for c in cmds):
                continue   # a line whose fallback can pass: what it runs cannot fail the job
            for followed in self.follower.step(line):
                if followed.unknown:
                    self.unknown = True
                if any(p in path for p in followed.paths):
                    continue
                self._sequence(list(followed.commands), found, (*path, *followed.paths), depth + 1, ordinary)

    # --- architecture tests and a guard's own tests -----------------------------------------

    def arch_tests(self) -> list[str]:
        out: list[str] = []
        for rel in sorted(self.tracked):
            parts = rel.lower().split("/")
            if (rel.endswith(_CODE_EXT) and _ARCH_NAME.search(posixpath.basename(rel))
                    and set(parts[:-1]) & (_TEST_PARTS | {"src"})
                    and any(m in self.text(rel) for m in _ARCH_MARKERS)):
                out.append(rel)
        return out

    def tests_of(self, script: str) -> list[str]:
        """The tracked test files of a guard script or folder (module docstring)."""
        stem = _stem(script)
        under, dash = stem.replace("-", "_"), stem.replace("_", "-")
        if self._folder(script) and script not in self.tracked:
            inside = [t for t in self._sorted
                      if (t.startswith(script + "/") or any(t.startswith(f"{d}/{posixpath.basename(script)}/")
                                                           for d in ("tests", "test")))
                      and _is_test_file(t)]
            return inside
        names = {f"test_{under}.py", f"{under}_test.py", f"{under}_test.go", f"test_{under}.sh",
                 f"test_{dash}.sh", f"test-{dash}.sh", f"{dash}_test.sh", f"{under}_test.sh", f"{dash}.bats",
                 f"{under}.bats", f"test_{under}.bats",
                 *(f"{v}.{k}.{e}" for v in (stem, under, dash) for k in ("test", "spec")
                   for e in ("js", "ts", "mjs", "cjs"))}
        out: list[str] = []
        for rel in self._sorted:
            if rel != script and posixpath.basename(rel) in names:
                body = self.text(rel)
                if stem in body or under in body or dash in body:
                    out.append(rel)
        return out


def _segments(text: str) -> list[str]:
    """Each command of ``text``'s lines that are not a whole-line comment (``#``, ``//``),
    split on ``&&``, ``||``, ``;`` and ``|``."""
    return [part for line in text.splitlines() if not line.lstrip().startswith(("#", "//"))
            for part in OPERATOR.split(line) if part not in ("&&", "||", ";", "|")]


def _at(cwd: str, path: str) -> str | None:
    """``path`` read from ``cwd``, normalised; None when it is not literal or leaves the checkout."""
    if not path or path.startswith(("/", "~")) or "$" in path:
        return None
    rel = posixpath.normpath(posixpath.join(cwd, path.removeprefix("./")))
    return None if rel in (".", "") or rel == ".." or rel.startswith("../") else rel


def _is_test_file(rel: str) -> bool:
    name = posixpath.basename(rel)
    return (name.startswith("test_") and name.endswith(".py")) or name.endswith(("_test.py", "_test.go"))


def _gitlab_split(text: str) -> dict[Trigger, str]:
    """``.gitlab-ci.yml`` with the lines of each trigger kept apart (others blanked)."""
    lines = text.splitlines()
    triggers = gitlab_line_triggers(text)
    return {want: "\n".join(line if t == want else "" for line, t in zip(lines, triggers, strict=True))
            for want in ("ordinary", "unknown")}


def scan_repo_guards(repo_root: Path) -> RepoGuardsBlock:
    """Build the ``repo_guards`` block (module docstring)."""
    repo = repo_root.resolve()
    tracked = repo_files(repo)
    files = ci_config_files(tracked)
    reader = _Reader(repo, tracked)
    raw = {rel: read_config(repo, rel) for rel in files}
    github = {rel: text for rel, text in raw.items() if rel not in CI_CONFIG_FILES}
    settled = resolve_workflows({rel: workflow_trigger(text) for rel, text in github.items()}, github)
    seen: dict[tuple[str, GuardKind, str, str], RepoGuard] = {}
    first_test_ci = ""
    for rel in files:
        if rel in CI_CONFIG_FILES:
            parts = list(_gitlab_split(raw[rel]).items())
        elif settled[rel] == "not_ordinary":
            continue
        else:
            followed = follow_workflow(raw[rel], reader.read_local)
            parts = [(settled[rel], followed.text)]
            reader.unknown = reader.unknown or bool(followed.unknown) or bool(_REMOTE_JOB.search(raw[rel]))
        for trigger, text in parts:
            for lines in job_lines(text, rel in CI_CONFIG_FILES):
                before = reader.runs_tests
                for f in reader.job(lines, trigger == "ordinary"):
                    key = (rel, f["kind"], f["via"], f["target"])
                    if key not in seen or (trigger == "ordinary" and seen[key]["trigger"] != "ordinary"):
                        seen[key] = RepoGuard(path=rel, kind=f["kind"], via=f["via"], target=f["target"],
                                              script=f["script"], trigger=trigger, tested_by=[])
                if reader.runs_tests and not before and not first_test_ci:
                    first_test_ci = rel
    if first_test_ci:
        for test in reader.arch_tests():
            seen[(first_test_ci, "boundary_checker", "archunit", test)] = RepoGuard(
                path=first_test_ci, kind="boundary_checker", via="archunit", target=test, script=None,
                trigger="ordinary", tested_by=[])
    guards = list(seen.values())
    for guard in guards:
        if guard["script"] is not None:
            guard["tested_by"] = reader.tests_of(guard["script"])
    return _block(guards, len(files), reader, tracked, raw)


def _block(guards: list[RepoGuard], files_read: int, reader: _Reader, tracked: frozenset[str],
           raw: dict[str, str]) -> RepoGuardsBlock:
    live = [g for g in guards if g["trigger"] == "ordinary"]
    scripts = {g["script"]: bool(g["tested_by"]) for g in live if g["script"] is not None}
    count = {k: sum(g["kind"] == k for g in live) for k in KINDS}
    block = RepoGuardsBlock(
        files_read=files_read, lint_baseline=count["lint_baseline"], boundary_checker=count["boundary_checker"],
        size_cap=count["size_cap"], baselines_without_update=len(reader.no_update), present=bool(live),
        guard_scripts=len(scripts), guard_scripts_tested=sum(scripts.values()),
        guard_tested=any(scripts.values()) if live else False, guards=guards)
    if not live:
        gitlab_includes = any(_INCLUDE.search(text) for rel, text in raw.items() if rel in CI_CONFIG_FILES)
        reason = (REASON_TRIGGER_UNKNOWN if guards
                  else REASON_CI_NOT_READ if (unread_ci_paths(tracked) or gitlab_includes or reader.unknown)
                  else None)
        if reason is not None:
            block["present"], block["present_reason"] = None, reason
            block["guard_tested"], block["guard_tested_reason"] = None, reason
    elif not scripts:
        block["guard_tested"], block["guard_tested_reason"] = None, REASON_NO_GUARD_SCRIPT
    return block


def guards_or_unknown(repo_root: Path) -> RepoGuardsBlock:
    """``scan_repo_guards``, or a not-measured block (``scan_failed``) when the reader raises."""
    try:
        return scan_repo_guards(repo_root)
    except Exception:   # noqa: BLE001 - any reader fault reads not measured, never false
        return RepoGuardsBlock(files_read=0, lint_baseline=0, boundary_checker=0, size_cap=0,
                               baselines_without_update=0, present=None, present_reason=REASON_SCAN_FAILED,
                               guard_scripts=0, guard_scripts_tested=0, guard_tested=None,
                               guard_tested_reason=REASON_SCAN_FAILED, guards=[])
