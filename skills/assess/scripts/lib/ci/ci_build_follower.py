"""What a ``make``, Gradle or Maven CI step runs, read from the committed build files (issue #845).

The build-tool half of the shared CI-step follower (``lib.ci.ci_step_follower``). Each reader
takes the step's words and a ``ReadLocal`` (``lib.ci.ci_step_paths.local_reader``: tracked
files inside the checkout only, never a symlink out of it) and returns a ``Followed``. Nothing
is executed and no model or build tool is run: the build files are read as text.

- **make** (``make`` / ``gmake``). ``-C dir`` and ``-f file`` pick the Makefile (else
  ``GNUmakefile``, ``makefile``, ``Makefile`` in that directory, make's own order). Each named
  target (else ``.DEFAULT_GOAL``, else the first rule) resolves to its recipe commands, its
  prerequisite targets' recipes first (depth first, each once), and a recipe line
  ``$(MAKE) <target>`` with no ``-C`` / ``-f`` follows that target in the same Makefile. A
  recipe line's ``@``, ``-`` and ``+`` prefixes are dropped. A named target with no rule, and
  an ``include`` the reader does not follow, land in ``unknown``.
- **Gradle** (``gradle``, ``./gradlew``). The build file of each project ``settings.gradle[.kts]``
  includes (at ``-p dir`` or the working directory; with no settings file, the root project
  alone) is read, or only the asked project's for ``:app:check``, with the ``subprojects {}`` /
  ``allprojects {}`` blocks of the projects above it. A build file the settings do not include
  (a test fixture) is never read; an ``includeBuild``, a ``projectDir`` or ``buildFileName``
  override, and an asked project the settings do not include land in ``unknown``. A task
  ``-x`` excludes is left out of the closure; ``-x :app:test`` (one project) lands in ``unknown``. With no
  tracked-file list (``files`` empty), a settings or build file is found through the reader
  instead. ``tasks`` is the closure of the asked tasks over the edges the files declare (``check.dependsOn japicmp``,
  ``tasks.named("check") { dependsOn(...) }``, ``check { finalizedBy ... }``) and the
  lifecycle edges of the plugins they apply (``_GRADLE_PLUGIN_TASKS``: ``java`` binds ``test``
  to ``check``, ``checkstyle`` binds ``checkstyleMain``, ``build`` runs ``check``). ``plugins``
  is each applied plugin bound to a task in that closure: through the table, or because the
  plugin id's last segment names a reached task (``me.champeau.gradle.japicmp`` and a
  ``japicmp`` task). An ``apply from:`` script is not read and lands in ``unknown``.
- **Maven** (``mvn``, ``./mvnw``). The root ``pom.xml`` (or the one ``-f`` names) and the
  closure of its ``<modules>`` are read, so a pom no module names (a test fixture) never is;
  ``-pl`` keeps the modules it names (by path or ``:artifactId``) and the root pom, and an entry
  it cannot settle, ``-rf``, ``-am`` / ``-amd`` and a ``-D`` property that skips a goal
  (``-DskipTests``, ``-Dcheckstyle.skip``) land in ``unknown``; ``-N`` reads the root pom alone. A phase or goal given as a
  property (``${goals}``, ``$GOALS``) and a phase outside the default and clean lifecycles
  (``site``) land in ``unknown`` as ``phase: <word>``. A plugin declared with no
  executions takes those a ``<pluginManagement>`` in the poms read gives it.
  A phase resolves to every plugin execution bound to that phase or an earlier one of its
  lifecycle: an execution's ``<phase>``, else the goal's default phase from
  ``_MAVEN_GOAL_PHASES`` (``japicmp-maven-plugin:cmp`` is ``verify``), plus the packaging's
  default bindings (``maven-surefire-plugin:test`` at ``test`` for a jar). ``tasks`` are
  ``artifactId:goal``, ``plugins`` the artifactIds. A goal written as ``prefix:goal``
  (``spotless:check``) resolves to the declared plugin with that prefix. An execution whose
  phase cannot be read, a profile's plugins, a ``<!DOCTYPE`` and a file that does not parse
  land in ``unknown``, never as no plugin.

Reasons, not a model: ``unknown`` holds what the reading could not settle, so a caller reads
it as null, never false. Inward-only imports: ``lib.ci.ci_action_inputs`` (``ReadLocal``),
``lib.command_flags``, ``lib.runner_args`` and ``lib.instructions.command_index``. Tests:
``tests/test_ci_step_follower.py``.
"""
from __future__ import annotations

import posixpath
import re
import xml.etree.ElementTree as ET
from collections.abc import Iterable
from pathlib import PurePosixPath
from typing import Literal, NamedTuple

from lib.ci.ci_action_inputs import ReadLocal
from lib.command_flags import make_args
from lib.runner_args import gradle_task_paths, gradle_tasks

FollowKind = Literal["script", "composite", "workflow", "make", "gradle", "maven"]


class Followed(NamedTuple):
    """What one CI step runs, as read from the repository (module docstring)."""

    kind: FollowKind
    paths: tuple[str, ...]          # the files read
    commands: tuple[str, ...] = ()  # shell commands (a script, an action, a workflow, a recipe)
    plugins: tuple[str, ...] = ()   # build plugins bound to what the step asks for
    tasks: tuple[str, ...] = ()     # Gradle tasks or Maven ``artifactId:goal`` it runs
    unknown: tuple[str, ...] = ()   # what the reading could not settle


def _uniq(items: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(items))


def _relative(directory: str, name: str) -> str | None:
    """``directory/name`` normalised, or None when it leaves the checkout or is not literal."""
    if any(p.startswith(("/", "~")) or "$" in p for p in (directory, name)):
        return None
    joined = posixpath.normpath(posixpath.join(directory, name))
    return None if joined == ".." or joined.startswith("../") else joined


# --- make -----------------------------------------------------------------------------------

_MAKEFILES = ("GNUmakefile", "makefile", "Makefile")
_RULE = re.compile(r"^([^\s:#=][^:#=]*?)\s*::?(?!=)")
_CONDITIONALS = frozenset({"ifeq", "ifneq", "ifdef", "ifndef", "else", "endif"})
_DEFAULT_GOAL = re.compile(r"^\.DEFAULT_GOAL\s*[:?+]?=\s*(\S+)")
_SUBMAKE = frozenset({"$(MAKE)", "${MAKE}", "make"})
_MAX_MAKE_DEPTH = 32


class _Makefile(NamedTuple):
    rules: dict[str, list[tuple[list[str], list[str]]]]   # target -> [(prerequisites, recipe)]
    first: str | None
    includes: bool


def _logical_lines(text: str) -> list[str]:
    """Lines with backslash continuations joined."""
    out: list[str] = []
    pending = ""
    for line in text.splitlines():
        if line.endswith("\\"):
            pending += line[:-1] + " "
            continue
        out.append(pending + line)
        pending = ""
    return out + ([pending] if pending else [])


def _parse_rule(line: str) -> tuple[list[str], list[str], str] | None:
    """``(targets, prerequisites, inline recipe)`` of a rule line, or None for anything else."""
    match = _RULE.match(line)
    if not match:
        return None
    rest = line[match.end():]
    prereqs, _, inline = rest.partition(";")
    prereqs = prereqs.split("#", 1)[0]
    words = [w for w in prereqs.split() if w != "|"]
    if any("=" in w for w in words):
        return None   # a target-specific variable (``test: CFLAGS=-O``), not a rule
    return match.group(1).split(), words, inline.strip()


def _parse_makefile(text: str) -> _Makefile:
    rules: dict[str, list[tuple[list[str], list[str]]]] = {}
    first: str | None = None
    goal: str | None = None
    includes = False
    recipe: list[str] | None = None
    for line in _logical_lines(text):
        if line.startswith("\t"):
            command = line.strip().lstrip("@-+").strip()
            if recipe is not None and command and not command.startswith("#"):
                recipe.append(command)
            continue
        if not line.strip() or line.lstrip().startswith("#") or line.split()[0] in _CONDITIONALS:
            continue
        includes = includes or line.split()[0] in ("include", "-include", "sinclude")
        default = _DEFAULT_GOAL.match(line)
        goal = default.group(1) if default else goal
        rule = _parse_rule(line)
        if rule is None:
            recipe = None
            continue
        targets, prereqs, inline = rule
        recipe = [inline] if inline else []
        for target in targets:
            if target.startswith(".") or "%" in target or "$" in target:
                continue
            rules.setdefault(target, []).append((prereqs, recipe))
            first = first or target
    return _Makefile(rules, goal or first, includes)


def _make_options(args: list[str]) -> tuple[str, str | None, list[str]]:
    """``(directory, makefile, rest)``: ``-C`` / ``-f`` read, the other words left."""
    directory, makefile, rest = "", None, []
    i = 0
    while i < len(args):
        word = args[i]
        nxt = args[i + 1] if i + 1 < len(args) else ""
        if word in ("-C", "--directory", "-f", "--file", "--makefile"):
            value, i = nxt, i + 2
        elif word.startswith(("-C", "-f")) and len(word) > 2:
            value, i = word[2:], i + 1
            word = word[:2]
        elif word.startswith(("--directory=", "--file=", "--makefile=")):
            word, _, value = word.partition("=")
            i += 1
        else:
            rest.append(word)
            i += 1
            continue
        if word in ("-C", "--directory"):
            directory = posixpath.join(directory, value)
        else:
            makefile = value
    return directory, makefile, rest


def _make_targets(args: list[str]) -> list[str]:
    return [w for w in make_args(args) if not w.startswith("-") and "=" not in w]


def _make_commands(make: _Makefile, target: str, seen: set[str], depth: int = 0) -> list[str]:
    """``target``'s recipe commands, prerequisites first, each target once."""
    if target in seen or depth > _MAX_MAKE_DEPTH:
        return []
    seen.add(target)
    out: list[str] = []
    for prereqs, recipe in make.rules.get(target, []):
        for prereq in prereqs:
            out += _make_commands(make, prereq, seen, depth + 1)
        for command in recipe:
            out.append(command)
            words = command.split()
            if words and words[0] in _SUBMAKE and not ({"-C", "-f"} & set(words)):
                for inner in _make_targets(words[1:]):
                    out += _make_commands(make, inner, seen, depth + 1)
    return out


def follow_make(words: list[str], read_local: ReadLocal, cwd: str = "") -> Followed | None:
    """The recipe commands ``make <target>`` runs (module docstring), or None when no Makefile
    is read."""
    if not words or PurePosixPath(words[0]).name not in ("make", "gmake"):
        return None
    directory, named, rest = _make_options(words[1:])
    base = _relative(cwd, directory)
    if base is None:
        return None
    for name in ([named] if named else _MAKEFILES):
        rel = _relative(base, name)
        text = read_local(rel) if rel is not None else None
        if rel is not None and text is not None:
            break
    else:
        return None
    make = _parse_makefile(text)
    targets = _make_targets(rest) or ([make.first] if make.first else [])
    seen: set[str] = set()
    commands = [c for t in targets for c in _make_commands(make, t, seen)]
    unknown = [t for t in targets if t not in make.rules] + (["include"] if make.includes else [])
    return Followed("make", (rel,), tuple(commands), unknown=_uniq(unknown))


# --- Gradle ---------------------------------------------------------------------------------

_GRADLE_COMMENT = re.compile(r"/\*.*?\*/|(?<![:\"'])//[^\n]*", re.S)
_JVM_TEST = ("test",)
# Plugins whose tasks join a lifecycle task, by plugin id: (lifecycle task, its tasks).
_GRADLE_PLUGIN_TASKS: dict[str, tuple[tuple[str, tuple[str, ...]], ...]] = {
    **{p: (("check", _JVM_TEST), ("assemble", ("jar",))) for p in (
        "java", "java-library", "application", "groovy", "scala", "java-gradle-plugin",
        "org.jetbrains.kotlin.jvm", "kotlin")},
    **{p: (("check", ("test", "lint")),) for p in (
        "com.android.application", "com.android.library", "org.jetbrains.kotlin.android")},
    "checkstyle": (("check", ("checkstyleMain", "checkstyleTest")),),
    "pmd": (("check", ("pmdMain", "pmdTest")),),
    "codenarc": (("check", ("codenarcMain", "codenarcTest")),),
    "com.github.spotbugs": (("check", ("spotbugsMain", "spotbugsTest")),),
    "com.diffplug.spotless": (("check", ("spotlessCheck",)),),
    "io.gitlab.arturbosch.detekt": (("check", ("detekt",)),),
    "org.jlleitschuh.gradle.ktlint": (("check", ("ktlintCheck",)),),
    "org.jetbrains.kotlinx.binary-compatibility-validator": (("check", ("apiCheck",)),),
}
_LIFECYCLE = {"build": ("assemble", "check")}
_PLUGIN_ID = re.compile(
    r"""\bid\s*\(?\s*["']([\w.-]+)["']\s*\)?(?![^\n]*\bapply\s*\(?\s*false)"""
    r"""|\bapply\s*\(?\s*plugin\s*[:=]\s*["']([\w.-]+)["']"""
    r"""|\bkotlin\s*\(\s*["'](\w+)["']\s*\)(?![^\n]*\bapply\s*\(?\s*false)""")
_PLUGINS_BLOCK = re.compile(r"\bplugins\s*\{")
_BARE_PLUGIN = re.compile(r"^\s*`?([a-z][\w-]*)`?\s*$", re.M)
_EDGE_LINE = re.compile(r"""(?:\btasks\.)?\b(\w+)\.(?:dependsOn|finalizedBy)\b\s*\(?([^\n]*)""")
_TASK_BLOCK = re.compile(
    r"""(?:\btasks\.(?:named|getByName)(?:<[\w.]+>)?\s*\(\s*["'](\w+)["'][^)]*\)"""
    r"""|\btasks\.(\w+)|^\s*(\w+))\s*\{""", re.M)
_EDGE_IN_BLOCK = re.compile(r"""\b(?:dependsOn|finalizedBy)\b\s*\(?([^\n]*)""")
_TASK_ARG = re.compile(r"""["']([\w:.-]+)["']|\b([A-Za-z_]\w*)\b""")
_NOT_TASK = frozenset({"tasks", "named", "getByName", "project", "provider", "it", "listOf",
                       "setOf", "arrayOf", "file", "files", "true", "false", "null"})
_SHARED_BLOCK = re.compile(r"\b(?:subprojects|allprojects)\s*\{")
_APPLY_FROM = re.compile(r"\bapply\s*\(?\s*from\s*[:=]")


def _block(text: str, start: int) -> str:
    """The text of the ``{ ... }`` block whose ``{`` is at ``start - 1``."""
    depth, i = 1, start
    while i < len(text) and depth:
        depth += {"{": 1, "}": -1}.get(text[i], 0)
        i += 1
    return text[start:i - 1]


def _task_names(args: str) -> list[str]:
    names: list[str] = []
    for quoted, bare in _TASK_ARG.findall(args.split("//", 1)[0]):
        name = (quoted or bare).rsplit(":", 1)[-1]
        if name and name not in _NOT_TASK:
            names.append(name)
    return names


def _gradle_plugins(text: str) -> list[str]:
    found = [a or b or ("org.jetbrains.kotlin." + c if c else "") for a, b, c in _PLUGIN_ID.findall(text)]
    for match in _PLUGINS_BLOCK.finditer(text):
        found += _BARE_PLUGIN.findall(_block(text, match.end()))
    return [p for p in found if p]


def _gradle_edges(text: str) -> dict[str, list[str]]:
    edges: dict[str, list[str]] = {}
    for task, args in _EDGE_LINE.findall(text):
        edges.setdefault(task, []).extend(_task_names(args))
    for match in _TASK_BLOCK.finditer(text):
        task = match.group(1) or match.group(2) or match.group(3)
        for args in _EDGE_IN_BLOCK.findall(_block(text, match.end())):
            edges.setdefault(task, []).extend(_task_names(args))
    return edges


_INCLUDE = re.compile(r"\binclude\b(?!Build)\s*\(?([^\n]*)")
_QUOTED = re.compile(r"""["']([\w:.-]+)["']""")


def _gradle_base(words: list[str], cwd: str) -> str | None:
    """The Gradle root directory: ``-p`` / ``--project-dir`` against ``cwd``, else ``cwd``."""
    for flag in ("-p", "--project-dir"):
        if flag in words[:-1]:
            joined = _relative(cwd, words[words.index(flag) + 1])
            return None if joined is None else "" if joined == "." else joined
    return cwd


def _present(files: frozenset[str], read_local: ReadLocal, rel: str) -> bool:
    """Whether ``rel`` is a file of the build: in ``files``, or, with no list, one the reader reads."""
    return rel in files if files else read_local(rel) is not None


def _gradle_projects(files: frozenset[str], read_local: ReadLocal, base: str) -> tuple[list[str], list[str]]:
    """``(project directories relative to base, unknown)`` from ``settings.gradle[.kts]``: the root
    (``""``) and each ``include``; with no settings file the build is the root project alone."""
    settings = next((r for r in (posixpath.join(base, n) for n in ("settings.gradle", "settings.gradle.kts"))
                     if _present(files, read_local, r)), None)
    text = read_local(settings) if settings is not None else None
    if text is None:
        return [""], ([f"unread: {settings}"] if settings is not None else [])
    text = _GRADLE_COMMENT.sub("", text)
    projects = [""] + [name.strip(":").replace(":", "/") for args in _INCLUDE.findall(text)
                       for name in _QUOTED.findall(args)]
    unknown = [f"{word}: {settings}" for word in ("includeBuild", "projectDir", "buildFileName")
               if word in text]
    return list(_uniq(projects)), unknown


def _gradle_files(files: frozenset[str], read_local: ReadLocal, words: list[str],
                  cwd: str) -> tuple[list[str], list[str], list[str]] | None:
    """``(build files, ancestor build files, unknown)`` a Gradle command reads: the build file of
    each project ``settings.gradle[.kts]`` includes (or of the asked projects only, for
    ``:app:check``), and the build files of the projects above an asked one, whose
    ``subprojects {}`` / ``allprojects {}`` blocks also configure it. A build file of a
    directory the settings do not include (a test fixture) is never read."""
    base = _gradle_base(words, cwd)
    if base is None:
        return None
    projects, unknown = _gradle_projects(files, read_local, base)
    asked = {"/".join(p.strip(":").split(":")[:-1]) if p.startswith(":") else None
             for p in gradle_task_paths(words[1:])}
    unknown += [f"project: {d}" for d in sorted(d for d in asked if d and d not in projects)]
    scoped = [d for d in projects if None in asked or not asked or d in asked]

    def build(d: str) -> list[str]:
        return [r for r in (posixpath.join(base, d, n) for n in ("build.gradle", "build.gradle.kts"))
                if _present(files, read_local, r)]
    above = {"" if str(a) == "." else str(a) for d in scoped if d for a in PurePosixPath(d).parents}
    own = [f for d in scoped for f in build(d)]
    ancestors = [f for d in projects if d in above for f in build(d) if f not in own]
    return own, ancestors, unknown


def _gradle_closure(asked: list[str], edges: dict[str, list[str]], excluded: set[str]) -> list[str]:
    """The asked tasks and every task they reach, an excluded (``-x``) task and what only it
    reaches left out, as Gradle leaves them out."""
    reached: list[str] = []
    stack = list(reversed(asked))
    while stack:
        task = stack.pop()
        if task in reached or task in excluded:
            continue
        reached.append(task)
        stack += reversed(edges.get(task, []))
    return reached


def follow_gradle(words: list[str], read_local: ReadLocal, files: frozenset[str],
                  cwd: str = "") -> Followed | None:
    """The plugins and tasks a Gradle command's tasks are bound to (module docstring)."""
    if not words or PurePosixPath(words[0]).name not in ("gradle", "gradlew"):
        return None
    found = _gradle_files(files, read_local, words, cwd)
    if not found or not (found[0] or found[2]):
        return None   # no build file and nothing unsettled: not a Gradle build here
    own, ancestors, unknown = found
    plugins: list[str] = []
    edges: dict[str, list[str]] = {name: list(on) for name, on in _LIFECYCLE.items()}
    read: list[str] = []
    for rel in ancestors + own:
        text = read_local(rel)
        if text is None:
            continue
        text = _GRADLE_COMMENT.sub("", text)
        if rel in ancestors:
            text = "\n".join(_block(text, m.end()) for m in _SHARED_BLOCK.finditer(text))
            if not text.strip():
                continue
        read.append(rel)
        unknown += ["apply from: " + rel] if _APPLY_FROM.search(text) else []
        plugins += _gradle_plugins(text)
        for task, on in _gradle_edges(text).items():
            edges.setdefault(task, []).extend(on)
    for plugin in plugins:
        for lifecycle, tasks in _GRADLE_PLUGIN_TASKS.get(plugin, ()):
            edges.setdefault(lifecycle, []).extend(tasks)
    asked = [p.rsplit(":", 1)[-1] for p in gradle_task_paths(words[1:])]
    _, excluded = gradle_tasks(words[1:])
    unknown += [f"-x {e}" for e in sorted(excluded) if ":" in e]   # one project's only (-x :app:test)
    reached = _gradle_closure(asked, edges, {e for e in excluded if ":" not in e})
    bound = [p for p in _uniq(plugins)
             if any(t in reached for _, tasks in _GRADLE_PLUGIN_TASKS.get(p, ()) for t in tasks)
             or p.rsplit(".", 1)[-1] in reached]
    run = tuple(t for t in reached if t not in asked)
    return Followed("gradle", tuple(read), plugins=tuple(bound), tasks=run,
                    unknown=_uniq(unknown))


# --- Maven ----------------------------------------------------------------------------------

_DEFAULT_PHASES = (
    "validate", "initialize", "generate-sources", "process-sources", "generate-resources",
    "process-resources", "compile", "process-classes", "generate-test-sources",
    "process-test-sources", "generate-test-resources", "process-test-resources",
    "test-compile", "process-test-classes", "test", "prepare-package", "package",
    "pre-integration-test", "integration-test", "post-integration-test", "verify", "install",
    "deploy")
_CLEAN_PHASES = ("pre-clean", "clean", "post-clean")
# The default bindings of a jar-like packaging, phase -> artifactId:goal (the Maven docs'
# "Built-in Lifecycle Bindings"); ``{packaging}`` is the packaging's own plugin goal.
_PACKAGING_BINDINGS = (
    ("process-resources", "maven-resources-plugin:resources"),
    ("compile", "maven-compiler-plugin:compile"),
    ("process-test-resources", "maven-resources-plugin:testResources"),
    ("test-compile", "maven-compiler-plugin:testCompile"),
    ("test", "maven-surefire-plugin:test"),
    ("package", "maven-{packaging}-plugin:{packaging}"),
    ("install", "maven-install-plugin:install"),
    ("deploy", "maven-deploy-plugin:deploy"),
)
_JAR_LIKE = frozenset({"jar", "war", "ear", "ejb", "rar"})
_CLEAN_BINDINGS = (("clean", "maven-clean-plugin:clean"),)
# The default phase of a plugin goal, by plugin prefix and goal, where the plugin declares one.
_MAVEN_GOAL_PHASES = {
    ("japicmp", "cmp"): "verify", ("checkstyle", "check"): "verify",
    ("spotbugs", "check"): "verify", ("pmd", "check"): "verify", ("pmd", "cpd-check"): "verify",
    ("jacoco", "check"): "verify", ("jacoco", "report"): "verify",
    ("jacoco", "prepare-agent"): "initialize", ("spotless", "check"): "verify",
    ("enforcer", "enforce"): "validate", ("revapi", "check"): "verify",
    ("failsafe", "integration-test"): "integration-test", ("failsafe", "verify"): "verify",
    ("surefire", "test"): "test", ("animal-sniffer", "check"): "process-classes",
    ("dependency", "analyze"): "verify", ("dependency", "analyze-only"): "verify",
    ("apache-rat", "check"): "validate", ("license", "check"): "validate",
    ("fmt", "check"): "verify", ("errorprone", "compile"): "compile",
}
_MAVEN_VALUED = frozenset({
    "-pl", "--projects", "-rf", "--resume-from", "-f", "--file", "-s", "--settings", "-gs",
    "--global-settings", "-t", "--toolchains", "-T", "--threads", "-P", "--activate-profiles",
    "-D", "--define", "-l", "--log-file", "-b", "--builder"})


def _prefix(artifact: str) -> str:
    """A plugin's goal prefix: ``maven-x-plugin`` and ``x-maven-plugin`` are ``x``."""
    for pattern in (r"^maven-(.+)-plugin$", r"^(.+)-maven-plugin$", r"^(.+)-plugin$"):
        match = re.match(pattern, artifact)
        if match:
            return match.group(1)
    return artifact


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _child(node: ET.Element, name: str) -> ET.Element | None:
    return next((c for c in node if _local(c.tag) == name), None)


def _text(node: ET.Element | None, name: str, default: str = "") -> str:
    found = _child(node, name) if node is not None else None
    return (found.text or "").strip() if found is not None else default


def _plugin_nodes(node: ET.Element | None) -> list[ET.Element]:
    plugins = _child(node, "plugins") if node is not None else None
    return [p for p in plugins if _local(p.tag) == "plugin"] if plugins is not None else []


_Bindings = tuple[list[tuple[str, str]], list[str]]   # ((phase, artifactId:goal), unknown)


class _Pom(NamedTuple):
    bindings: list[tuple[str, str]]   # (phase, artifactId:goal)
    unknown: list[str]
    artifacts: list[str]
    artifact_id: str = ""
    managed: dict[str, _Bindings] = {}   # <pluginManagement> executions, by artifactId
    bare: tuple[str, ...] = ()           # plugins declared with no executions of their own
    modules: tuple[str, ...] = ()        # <modules><module> values


def _execution_bindings(plugin: ET.Element) -> _Bindings:
    artifact = _text(plugin, "artifactId")
    bindings: list[tuple[str, str]] = []
    unknown: list[str] = []
    executions = _child(plugin, "executions")
    for execution in [e for e in executions if _local(e.tag) == "execution"] if executions is not None else []:
        goals = _child(execution, "goals")
        for goal in [(g.text or "").strip() for g in goals] if goals is not None else []:
            phase = _text(execution, "phase") or _MAVEN_GOAL_PHASES.get((_prefix(artifact), goal))
            if phase:
                bindings.append((phase, f"{artifact}:{goal}"))
            else:
                unknown.append(f"{artifact}:{goal}")
    return bindings, unknown


def _read_pom(text: str, rel: str) -> _Pom:
    if "<!DOCTYPE" in text or "<!ENTITY" in text:
        return _Pom([], [f"doctype: {rel}"], [])
    try:
        project = ET.fromstring(text)
    except ET.ParseError:
        return _Pom([], [f"unparsed: {rel}"], [])
    packaging = _text(project, "packaging", "jar")
    bindings = [(phase, goal.format(packaging=packaging)) for phase, goal in _PACKAGING_BINDINGS
                if packaging in _JAR_LIKE or phase in ("install", "deploy")]
    bindings += list(_CLEAN_BINDINGS)
    unknown: list[str] = [] if packaging in _JAR_LIKE or packaging == "pom" else [f"packaging: {packaging}"]
    artifacts: list[str] = []
    bare: list[str] = []
    build = _child(project, "build")
    for plugin in _plugin_nodes(build):
        artifacts.append(_text(plugin, "artifactId"))
        found, unread = _execution_bindings(plugin)
        bindings += found
        unknown += unread
        bare += [] if found or unread else [artifacts[-1]]
    management = _child(build, "pluginManagement") if build is not None else None
    managed = {_text(p, "artifactId"): _execution_bindings(p) for p in _plugin_nodes(management)}
    profiles = _child(project, "profiles")
    for profile in [p for p in profiles if _local(p.tag) == "profile"] if profiles is not None else []:
        if _plugin_nodes(_child(profile, "build")) or _child(profile, "modules") is not None:
            unknown.append("profile: " + (_text(profile, "id") or rel))
    modules = _child(project, "modules")
    names = tuple((m.text or "").strip() for m in modules if _local(m.tag) == "module") if modules is not None else ()
    return _Pom(bindings, unknown, artifacts, _text(project, "artifactId"), managed, tuple(bare), names)


class _MavenArgs(NamedTuple):
    pom: str | None          # -f
    projects: list[str]      # -pl entries
    unread: list[str]        # options whose module selection is not read (-rf, -am)
    asked: list[str]         # goals and phases


def _split_long(args: list[str]) -> list[str]:
    """``--projects=a`` as ``--projects a``, so the ``=`` form selects modules too."""
    out: list[str] = []
    for word in args:
        flag, eq, value = word.partition("=")
        out += [flag, value] if word.startswith("--") and eq and flag in _MAVEN_VALUED else [word]
    return out


def _skip_property(define: str) -> list[str]:
    """``["-Dx.skip"]`` for a property that turns a bound goal off (``skipTests``,
    ``checkstyle.skip``, ``maven.test.skip``) unless set false; the goals it skips still read
    as bound, so the run is unknown, never credited."""
    name, _, value = define.partition("=")
    return [f"-D{name}"] if "skip" in name.lower() and value.lower() not in ("false",) else []


def _maven_words(args: list[str]) -> _MavenArgs:
    pom, projects, unread, out, skip = None, [], [], [], ""
    for word in _split_long(args):
        if skip:
            pom = word if skip in ("-f", "--file") else pom
            unread += _skip_property(word) if skip in ("-D", "--define") else []
            projects += [p for p in word.split(",") if p] if skip in ("-pl", "--projects") else []
            unread += [skip] if skip in ("-rf", "--resume-from") else []
            skip = ""
        elif word.startswith("-"):
            skip = word if word in _MAVEN_VALUED else ""
            unread += [word] if word in ("-am", "--also-make", "-amd", "--also-make-dependents") else []
            unread += _skip_property(word[2:]) if word.startswith("-D") and len(word) > 2 else []
            unread += [word] if word in ("-N", "--non-recursive") else []
        else:
            out.append(word)
    return _MavenArgs(pom, projects, unread, out)


def _select(poms: dict[str, _Pom], projects: list[str], root: str) -> tuple[list[str], list[str]]:
    """``(pom paths, unknown)`` a ``-pl`` list builds: each module named by path or by
    ``[group]:artifactId``, and the root pom its modules inherit from. An entry the reader
    cannot settle (``!x`` exclusion, ``?x`` optional, no matching pom) is unknown."""
    if not projects:
        return list(poms), []
    base = posixpath.dirname(root)
    chosen = [root]
    unknown: list[str] = []
    for entry in projects:
        if entry[:1] in ("!", "?", "$"):
            unknown.append(f"-pl: {entry}")
            continue
        if ":" in entry:
            match = [rel for rel, pom in poms.items() if pom.artifact_id == entry.rsplit(":", 1)[-1]]
        else:
            path = _relative(base, entry)
            match = [rel for rel in poms if path is not None and rel == posixpath.join(path, "pom.xml")]
        chosen += [rel for rel in match if rel not in chosen]
        unknown += [] if match else [f"-pl: {entry}"]
    return chosen, unknown


def _bindings(poms: list[_Pom]) -> tuple[list[tuple[str, str]], list[str]]:
    """Each pom's bindings, with a plugin it declares bare taking the executions a
    ``<pluginManagement>`` in the poms read gives it (a parent's, usually)."""
    managed: dict[str, _Bindings] = {}
    for pom in poms:
        managed.update(pom.managed)
    bindings: list[tuple[str, str]] = []
    unknown: list[str] = []
    for pom in poms:
        bindings += pom.bindings
        unknown += pom.unknown
        for artifact in pom.bare:
            found, unread = managed.get(artifact, ([], []))
            bindings += found
            unknown += unread
    return bindings, unknown


def _phases_upto(phase: str) -> tuple[str, ...] | None:
    for lifecycle in (_DEFAULT_PHASES, _CLEAN_PHASES):
        if phase in lifecycle:
            return lifecycle[:lifecycle.index(phase) + 1]
    return None


def _root_pom(pom: str | None, cwd: str) -> str | None:
    """The pom a Maven command starts from: ``-f`` (a file or a directory) or ``pom.xml``."""
    rel = _relative(cwd, pom or "pom.xml")
    return rel if rel is None or rel.endswith(".xml") else posixpath.join(rel, "pom.xml")


def _reactor(read_local: ReadLocal, root: str) -> tuple[dict[str, _Pom], list[str]]:
    """The root pom and the closure of its ``<modules>`` (a pom no module names, such as a test
    fixture, is never read), and the modules that cannot be read."""
    poms: dict[str, _Pom] = {}
    unknown: list[str] = []
    queue = [root]
    while queue:
        rel = queue.pop(0)
        text = read_local(rel) if rel not in poms else None
        if text is None:
            unknown += [] if rel in poms or rel == root else [f"module: {rel}"]
            continue
        poms[rel] = pom = _read_pom(text, rel)
        for module in pom.modules:
            child = _relative(posixpath.dirname(rel), module)
            if child is None:
                unknown.append(f"module: {module}")
            else:
                queue.append(child if child.endswith(".xml") else posixpath.join(child, "pom.xml"))
    return poms, unknown


def _goal_task(word: str, artifacts: list[str]) -> tuple[str, str]:
    """``(plugin, artifactId:goal)`` for a ``prefix:goal`` or ``group:artifact[:version]:goal``."""
    parts = word.split(":")
    goal = parts[-1]
    if len(parts) == 2:
        artifact = next((a for a in artifacts if _prefix(a) == parts[0]), parts[0])
    else:
        artifact = parts[1]
    return artifact, f"{artifact}:{goal}"


def follow_maven(words: list[str], read_local: ReadLocal, cwd: str = "") -> Followed | None:
    """The plugins and goals a Maven command's phases and goals are bound to (module docstring)."""
    if not words or PurePosixPath(words[0]).name not in ("mvn", "mvnw", "mvnw.cmd"):
        return None
    args = _maven_words(words[1:])
    root = _root_pom(args.pom, cwd)
    parsed, missing = _reactor(read_local, root) if root is not None else ({}, [])
    if root is not None and root in parsed and {"-N", "--non-recursive"} & set(args.unread):
        parsed, missing = {root: parsed[root]}, []   # -N builds the root pom alone
    if root is None or not parsed:
        return None
    read, unknown = _select(parsed, args.projects, root)
    unknown += missing
    poms = [parsed[rel] for rel in read]
    bindings, unread = _bindings(poms)
    unknown += unread + [u for u in args.unread if u not in ("-N", "--non-recursive")]   # -N is read above
    artifacts = [a for p in poms for a in p.artifacts]
    tasks: list[str] = []
    for word in args.asked:
        if "$" in word:
            unknown.append(f"phase: {word}")   # a property (``${goals}``): its value is not read
            continue
        if ":" in word:
            tasks.append(_goal_task(word, artifacts)[1])
            continue
        phases = _phases_upto(word)
        if phases is None:
            unknown.append(f"phase: {word}")
            continue
        bound = [(phases.index(ph), goal) for ph, goal in bindings if ph in phases]
        tasks += [goal for _, goal in sorted(bound, key=lambda b: b[0])]
    tasks = list(_uniq(tasks))
    plugins = _uniq(t.rsplit(":", 1)[0] for t in tasks)
    return Followed("maven", tuple(read), plugins=plugins, tasks=tuple(tasks), unknown=_uniq(unknown))
