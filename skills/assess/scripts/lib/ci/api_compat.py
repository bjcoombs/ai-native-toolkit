"""API and contract compatibility checks that CI runs (issue #853, theme 8 of #826).

``scan_api_compat(repo_root, ci_checks)`` builds the ``api_compat`` block from two arms. Nothing is
executed: CI files, the scripts, recipes and build files a step leads to, and test files are read
as text, and Python test files are parsed with ``ast``, never imported.

- **Named tool (``runs``).** A committed CI file (a GitHub Actions workflow, local composite
  actions inlined, or ``.gitlab-ci.yml``) invokes a compatibility tool, on its own line or through
  the shared CI-step follower (``lib.ci.ci_step_follower``: a committed script, a ``make`` recipe,
  a Maven or Gradle build, up to ``_MAX_FOLLOW`` levels) or one root ``package.json`` script
  (``npm run <t>``, ``pnpm``, ``yarn``, ``bun``). The tools (``TOOLS``): ``apidiff`` (or ``go run
  .../apidiff``), ``api-extractor run`` (not ``--local``, which rewrites the report), ``griffe
  check``, ``cargo semver-checks``, ``buf breaking``, ``japicmp`` and ``revapi`` (their Maven goals
  and Gradle plugins, read from the follower's ``tasks`` and ``plugins``). An install (``go install
  .../apidiff``, ``npm i -D @microsoft/api-extractor``), a ``--help`` or ``--version`` call and an
  ``echo`` invoke nothing.
- **Pinning test (``pins``).** A tracked test file pins the public exports: a Python test whose
  ``ast`` holds an ``==`` comparison with ``__all__`` on one side (``assert set(__all__) ==
  set(EXPECTED_ALL)``), or a JavaScript or TypeScript test that compares ``Object.keys`` of a
  module to a snapshot or a literal (``expect(Object.keys(api)).toMatchSnapshot()``). It counts when
  CI runs tests: ``ci_checks.enforced_check_runs`` is true (the layer-5 reading).
- **Declared only (``declared``, listed, never counted).** A tool named in a build file
  (``pom.xml``, a Gradle build, ``package.json``, a Makefile, ``pyproject.toml``, ``Cargo.toml``,
  ``buf.yaml``, ``api-extractor.json``) or a file under ``hack/``, ``scripts/``, ``build/`` or
  ``.github/`` that no CI run reaches. Docs never declare.

Masking follows the #775 rule (``lib.ci.ci_masking.mask_lines``): ``|| true``, ``continue-on-error``,
``allow_failure`` and a never-true ``if:`` do not count, and a form whose failure the step may hide
(``ci_unsure``) is unsettled, never credit. A followed script or recipe line with a ``||`` fallback
that does not re-raise does not count and is not followed (``lib.ci.docs_kept_true``'s rule). Only
shell bodies are read: GitHub ``run:``, GitLab ``script:`` and ``before_script:`` (GitLab ignores an
``after_script:`` failure). On GitLab a job whose ``when:`` or every ``rules:`` entry is ``never``
or ``manual`` runs nothing, and a hidden job (``.compat:``) runs only as far as a visible job that
runs ``extends:`` it; a tool in a hidden job nothing extends, in a file that uses anchors or
``!reference``, is unsettled.

``present`` is true when a tool runs, or a pinning test is tracked and CI runs tests. Otherwise
null when it cannot be settled: ``tests_not_read`` (a pinning test, and ``enforced_check_runs``
null or the ``ci_checks`` block absent) or ``ci_not_read`` (a tool is declared or found in an
unsettled form, and a tracked CI file of a system this reader does not parse, a GitLab top-level
``include:`` or a follower reading it could not settle may run it). Else false.

Full reference: lib/docs/api_compat.md. Inward-only imports; never an orchestrator. Tests:
``tests/test_api_compat_condition.py``.
"""
from __future__ import annotations

import ast
import json
import posixpath
import re
from collections.abc import Mapping
from pathlib import Path

from lib.ci.agent_evals import _idle
from lib.ci.check_command import is_info_invocation
from lib.ci.ci_action_inputs import ReadLocal
from lib.ci.ci_build_follower import Followed
from lib.ci.ci_masking import IndentTree, mask_lines, parse_lines
from lib.ci.ci_step_follower import StepFollower, follow_workflow
from lib.ci.ci_step_paths import local_reader
from lib.ci.docs_kept_true import _commands
from lib.instructions.command_index import CI_CONFIG_FILES, ci_config_files, ci_line_text, read_config, repo_files
from lib.run_context_types_api_compat import ApiCompatBlock, ApiCompatRun
from lib.unread_ci import unread_ci_paths

TOOLS = ("apidiff", "api-extractor", "griffe", "cargo-semver-checks", "buf-breaking", "japicmp", "revapi")
REASON_CI_NOT_READ = "ci_not_read"
REASON_TESTS_NOT_READ = "tests_not_read"
_MAX_FOLLOW = 3
_GITHUB_BODY = frozenset({"run"})
_GITLAB_BODY = frozenset({"script", "before_script"})
_GLOBALS = frozenset({"variables", "workflow", "include", "stages", "image", "services", "cache"})
_INCLUDE = re.compile(r"^include\s*:", re.M)
_ALIASING = re.compile(r"!reference|<<\s*:|(?:^|[\s\[,])\*[\w.-]+", re.M)
# Words a launcher puts before the program it runs (``uv run``, ``npx --yes``, ``pnpm exec``).
_LAUNCHERS = frozenset({"uv", "uvx", "pipx", "poetry", "pdm", "hatch", "rye", "pipenv", "npx", "bunx",
                        "pnpx", "pnpm", "yarn", "bun", "run", "exec", "dlx", "x", "python", "python3", "-m"})
_VALUED = frozenset({"--with", "--from", "--package", "-p", "--python", "--project", "-C", "--directory"})
_PM = frozenset({"npm", "pnpm", "yarn", "bun"})
_DOC_SUFFIXES = (".md", ".rst", ".txt", ".adoc", ".html")
_BUILD_FILES = frozenset({"pom.xml", "build.gradle", "build.gradle.kts", "settings.gradle", "settings.gradle.kts",
                          "package.json", "Makefile", "makefile", "GNUmakefile", "justfile", "pyproject.toml",
                          "Cargo.toml", "buf.yaml", "buf.work.yaml", "api-extractor.json", "noxfile.py", "tox.ini"})
_BUILD_DIRS = ("hack/", "scripts/", "build/", ".github/")
_DECLARED: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("apidiff", re.compile(r"\bapidiff\b")),
    ("api-extractor", re.compile(r"api-extractor")),
    ("griffe", re.compile(r"\bgriffe\b")),
    ("cargo-semver-checks", re.compile(r"semver-checks")),
    ("buf-breaking", re.compile(r"\bbuf\s+breaking\b")),
    ("japicmp", re.compile(r"japicmp")),
    ("revapi", re.compile(r"\brevapi\b")),
)
_BUF_BREAKING = re.compile(r"^breaking\s*:", re.M)   # the breaking-rules section of a buf.yaml
_SET_ASIDE = ("fixtures/", "testdata/", "vendor/", "node_modules/", "third_party/")
_JS_TEST = re.compile(r"\.(?:test|spec)\.[cm]?[jt]sx?$")
_JS_PIN = re.compile(r"expect\(\s*Object\.keys\(\s*[\w$.]+\s*\)(?:\.sort\(\))?\s*\)\s*"
                     r"\.(?:toMatchSnapshot|toMatchInlineSnapshot|toEqual|toStrictEqual)\(")


def _name(word: str) -> str:
    """A program word as a bare name: ``@microsoft/api-extractor@7`` and ``node_modules/.bin/x`` read
    ``api-extractor`` and ``x``."""
    base = posixpath.basename(word.lstrip("@").split("@", 1)[0]) if word.startswith("@") else posixpath.basename(word)
    return base.split("@", 1)[0]


def _program(words: list[str]) -> list[str]:
    """``words`` from the program a launcher runs, the launcher's own options skipped."""
    i = 0
    while i < len(words) - 1:
        word = words[i]
        if word in _LAUNCHERS:
            i += 1
        elif i and word.startswith("-"):
            i += 2 if word in _VALUED else 1
        else:
            break
    return words[i:]


def tool_of(words: list[str]) -> str | None:
    """The ``TOOLS`` token one command invokes, or None (module docstring)."""
    if not words or is_info_invocation(" ".join(words)):
        return None
    if words[:2] == ["go", "run"]:
        return "apidiff" if any(_name(w) == "apidiff" for w in words[2:] if not w.startswith("-")) else None
    prog = _program(words)
    head, rest = (_name(prog[0]), prog[1:]) if prog else ("", [])
    if head == "apidiff" or head == "japicmp" or head == "revapi":
        return head
    if head == "java" and any(w.endswith(".jar") and "japicmp" in _name(w) for w in rest):
        return "japicmp"
    if head == "api-extractor":
        return "api-extractor" if rest[:1] == ["run"] and not {"--local", "-l"} & set(rest) else None
    if head == "griffe" and rest[:1] == ["check"]:
        return "griffe"
    if (head == "cargo" and rest[:1] == ["semver-checks"]) or head == "cargo-semver-checks":
        return "cargo-semver-checks"
    if head == "buf" and rest[:1] == ["breaking"]:
        return "buf-breaking"
    return None


def build_tools(followed: Followed) -> tuple[set[str], bool]:
    """The tools a Maven or Gradle step reaches through its bound plugins and tasks, and whether
    the reading left one of them unsettled (a ``-Djapicmp.skip``, an unread ``apply from:``)."""
    names = [n.lower() for n in (*followed.plugins, *followed.tasks)]
    found = {tool for tool in ("japicmp", "revapi") if any(tool in n for n in names)}
    if any(t in u.lower() for u in followed.unknown for t in found):
        return set(), True   # a property may skip the goal: not credit
    return found, bool(followed.unknown)


def _package_scripts(read_local: ReadLocal, cwd: str) -> Mapping[str, object]:
    text = read_local(posixpath.join(cwd, "package.json") if cwd else "package.json")
    try:
        scripts = json.loads(text).get("scripts", {}) if text else {}
    except (ValueError, AttributeError):
        return {}
    return scripts if isinstance(scripts, dict) else {}


class _Reader:
    """Follows one repository's CI lines to the tools they run (module docstring)."""

    def __init__(self, read_local: ReadLocal, tracked: frozenset[str]) -> None:
        self.read_local = read_local
        self.follower = StepFollower(read_local, tracked)
        self.unsettled = False

    def lines(self, lines: list[str], depth: int = 0, path: tuple[str, ...] = ()) -> set[str]:
        """The tools the shell ``lines`` run, followed into the repository."""
        found: set[str] = set()
        for line in lines:
            commands = list(_commands(ci_line_text(line)))
            for cmd in commands:
                tool = tool_of(cmd["words"]) if not cmd["masked"] else None
                if tool is not None:
                    found.add(tool)
                if not cmd["masked"] and depth < _MAX_FOLLOW:
                    found |= self._npm(cmd["words"], depth, path)
            if depth >= _MAX_FOLLOW or any(c["masked"] for c in commands):
                continue   # a fallback that can pass: what the line runs cannot fail the job
            for followed in self.follower.step(line):
                if any(p in path for p in followed.paths):
                    continue
                tools, unsettled = build_tools(followed)
                found |= tools
                self.unsettled |= unsettled or (followed.kind == "workflow" and bool(followed.unknown))
                found |= self.lines(list(followed.commands), depth + 1, (*path, *followed.paths))
        return found

    def _npm(self, words: list[str], depth: int, path: tuple[str, ...]) -> set[str]:
        """The tools a root ``package.json`` script runs, for ``npm run <t>`` and its kin."""
        if not words or _name(words[0]) not in _PM:
            return set()
        rest = [w for w in words[1:] if not w.startswith("-")]
        target = rest[1] if rest[:1] == ["run"] and len(rest) > 1 else (
            rest[0] if words[0] in ("yarn", "pnpm", "bun") and rest and rest[0] not in _LAUNCHERS else None)
        script = _package_scripts(self.read_local, "").get(target or "")
        key = f"package.json#{target}"
        if not isinstance(script, str) or key in path:
            return set()
        return self.lines([script], depth + 1, (*path, key))


def _extends(tree: IndentTree, top: int) -> list[str]:
    """The names a GitLab job's ``extends:`` holds (a value, a flow list or a block list)."""
    out: list[str] = []
    for k in tree.children(top):
        if tree.lines[k].key != "extends":
            continue
        value = tree.lines[k].value.strip()
        if value:
            out += [v.strip(" '\"") for v in value.strip("[]").split(",") if v.strip(" '\"")]
        out += [tree.lines[j].value.strip(" '\"") for j in tree.subtree(k)
                if j != k and tree.lines[j].dash and tree.lines[j].value]
    return out


def _gitlab_jobs(tree: IndentTree) -> tuple[set[int], set[int]]:
    """``(running, untaken)``: the top-level GitLab jobs that run on a pipeline (visible, not a
    global keyword, not idle, or hidden and extended by one that runs), and the hidden jobs
    nothing that runs extends."""
    tops = {i: line.key for i, line in enumerate(tree.lines) if line.structural and line.level == 0 and line.key
            and not tree.ancestors(i)}
    idle = _idle(tree)
    running = {i for i, key in tops.items() if not key.startswith(".") and key not in _GLOBALS and i not in idle}
    by_name = {key: i for i, key in tops.items()}
    queue = list(running)
    while queue:
        for name in _extends(tree, queue.pop()):
            j = by_name.get(name)
            if j is not None and j not in running and j not in idle:
                running.add(j)
                queue.append(j)
    untaken = {i for i, key in tops.items() if key.startswith(".") and i not in running}
    return running, untaken


def _job_lines(text: str, gitlab: bool) -> tuple[list[list[str]], list[str], list[str]]:
    """``(jobs, untaken, unsure)``: each running job's unmasked shell lines, the lines of GitLab
    hidden jobs nothing extends, and the lines whose failure the step may hide (``ci_unsure``)."""
    keys = _GITLAB_BODY if gitlab else _GITHUB_BODY
    lines = text.splitlines()
    unmasked, unsure = mask_lines(text)
    tree = IndentTree(parse_lines(lines))
    running, untaken = _gitlab_jobs(tree) if gitlab else (set(), set())
    spans: dict[int, list[str]] = {}
    hidden: list[str] = []
    doubt: list[str] = []
    for i, line in enumerate(unmasked):
        if tree.lines[tree.body(i)].key not in keys:
            continue
        start = tree.job(i).start
        if gitlab and start in untaken:
            hidden.append(line)
        elif not gitlab or start in running:
            if line.strip():
                spans.setdefault(start, []).append(line)
            if unsure[i].strip():
                doubt.append(unsure[i])
    return list(spans.values()), hidden, doubt


def _declared(tracked: frozenset[str], read_local: ReadLocal) -> dict[str, list[str]]:
    """Tool token to the build files that name it (module docstring), sorted."""
    out: dict[str, list[str]] = {}
    for rel in sorted(tracked):
        base = posixpath.basename(rel)
        if rel.endswith(_DOC_SUFFIXES) or any(s in f"/{rel}" for s in ("/" + d for d in _SET_ASIDE)):
            continue
        if base not in _BUILD_FILES and not rel.startswith(_BUILD_DIRS) and not base.startswith("build.gradle"):
            continue
        text = read_local(rel) or ""
        for tool, pattern in _DECLARED:
            if pattern.search(text) or (tool == "buf-breaking" and base.startswith("buf") and _BUF_BREAKING.search(text)):
                out.setdefault(tool, []).append(rel)
    return out


def _python_pins(text: str) -> bool:
    """An ``==`` comparison in a Python test with ``__all__`` on one side and not the other."""
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return False

    def names_all(node: ast.AST) -> bool:
        return any((isinstance(n, ast.Name) and n.id == "__all__")
                   or (isinstance(n, ast.Attribute) and n.attr == "__all__") for n in ast.walk(node))

    for node in ast.walk(tree):
        if isinstance(node, ast.Compare) and len(node.ops) == 1 and isinstance(node.ops[0], ast.Eq):
            left, right = node.left, node.comparators[0]
            if names_all(left) != names_all(right):
                return True
    return False


def _pins(tracked: frozenset[str], read_local: ReadLocal) -> list[str]:
    """The tracked test files that pin public exports (module docstring), sorted."""
    out: list[str] = []
    for rel in sorted(tracked):
        base = posixpath.basename(rel)
        if any(s in f"/{rel}" for s in ("/" + d for d in _SET_ASIDE)):
            continue
        if base.endswith(".py") and (base.startswith("test_") or base.endswith("_test.py")):
            if _python_pins(read_local(rel) or ""):
                out.append(rel)
        elif _JS_TEST.search(base):
            text = "\n".join(line for line in (read_local(rel) or "").splitlines()
                             if not line.lstrip().startswith("//"))
            if _JS_PIN.search(text):
                out.append(rel)
    return out


def scan_api_compat(repo_root: Path, ci_checks: Mapping[str, object] | None = None) -> ApiCompatBlock:
    """Build the ``api_compat`` block (module docstring)."""
    repo = repo_root.resolve()
    tracked = repo_files(repo)
    read_local: ReadLocal = local_reader(repo, tracked)
    files = ci_config_files(tracked)
    reader = _Reader(read_local, tracked)
    runs: dict[tuple[str, str], None] = {}
    unsure_hit = False
    for rel in files:
        gitlab = rel in CI_CONFIG_FILES
        raw = read_config(repo, rel)
        text = raw if gitlab else follow_workflow(raw, read_local).text
        jobs, hidden, doubt = _job_lines(text, gitlab)
        for lines in jobs:
            for tool in sorted(reader.lines(lines)):
                runs[(rel, tool)] = None
        unsure_hit |= bool(_Reader(read_local, tracked).lines(doubt))
        unsure_hit |= bool(_ALIASING.search(raw)) and bool(_Reader(read_local, tracked).lines(hidden))
    run_rows = [ApiCompatRun(path=rel, tool=tool) for rel, tool in runs]
    reached = {tool for _, tool in runs}
    declared = {t: paths for t, paths in _declared(tracked, read_local).items() if t not in reached}
    pins = _pins(tracked, read_local)
    tests_run = ci_checks.get("enforced_check_runs") if isinstance(ci_checks, Mapping) else None
    block = ApiCompatBlock(
        files_read=len(files), tools_run=len(reached), pinning_tests=len(pins), declared_only=len(declared),
        present=bool(run_rows) or (bool(pins) and tests_run is True),
        runs=run_rows, pins=pins,
        declared=[ApiCompatRun(path=p, tool=t) for t, paths in sorted(declared.items()) for p in paths])
    if block["present"]:
        return block
    gitlab_includes = any(_INCLUDE.search(read_config(repo, rel)) for rel in files if rel in CI_CONFIG_FILES)
    if pins and tests_run is None:
        block["present"], block["present_reason"] = None, REASON_TESTS_NOT_READ
    elif (declared or unsure_hit) and (unsure_hit or reader.unsettled or unread_ci_paths(tracked) or gitlab_includes):
        block["present"], block["present_reason"] = None, REASON_CI_NOT_READ
    return block
