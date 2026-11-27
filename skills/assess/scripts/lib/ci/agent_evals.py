"""Agent eval suites that CI runs against a committed baseline (#850, part of #826).

An agent eval suite scores a model-backed feature on fixed cases. Run in CI against a committed
baseline (a snapshot, recorded model responses or a tool-budget file), it turns a prompt or
model change that makes the agent worse into a red check instead of a user report. The tendency
it compensates for is guardrail erosion: with no baseline in the tree, "the evals still pass"
means whatever the last person to run them decided it means. Rules only, no model; read by the
``ci_model_jobs`` collector (``lib.ci.ci_model_jobs``), which already reads the CI files.

**A suite** is the outermost tracked folder named in ``EVAL_DIRS`` (``evals``, ``eval``,
``evaluations``, ``llm_evals``, ``agent-evals`` ...), or a test tier in ``TEST_TIERS``
(``agent``, ``agents``, ``llm``) directly under a test root (``tests``, ``test``, ``spec`` ...).
A folder under ``SET_ASIDE_DIRS`` (vendored, fixture or docs trees) is not one.

**Model reference.** A suite counts only when a file in it names a model provider, client or
harness (``MODEL_REF``: anthropic, openai, claude, gpt-4, gemini, llm, litellm, langchain,
promptfoo, deepeval ...) or it holds a recorded-response fixture (a cassette or recording). An
``eval`` folder holding an expression evaluator names none, so it is set aside
(``set_aside``), never counted.

**CI reference.** A committed CI file (a GitHub workflow, a repo-local action, GitLab CI) runs
the suite: a ``run:`` or ``script:`` command, or a ``working-directory:``, ``with:`` or ``args:``
value, names its path (``pytest evals/``, ``working-directory: tests/agent``; a top-level
``eval`` folder only as ``eval/`` or ``./eval``, since ``eval`` is also a command), its dotted module
(``python -m evals.run``) or its folder name as a pytest marker (``pytest -m eval``; ``eval`` and
``evals``, ``agent`` and ``agents`` read as one; never ``-m evaluation_slow``). Commands are read
as ``lib.ci.ci_shell_text.command_text`` reads them, so a quoted argument
(``git ls-files 'evals/**'``, ``-m "not eval"``), an ``echo`` and a comment run nothing. A job id,
a step ``name:``, a ``paths:`` or ``changes:`` trigger filter and a step or job whose ``if:`` is
never true run nothing, nor does a command whose first word only lints, formats, moves or
installs (``NOT_RUNNERS``: ``ruff check evals``, ``rm -rf evals``, ``pip install evals``) or an
input of a GitHub ``actions/`` utility (``upload-artifact`` with ``path: evals/results.json``). A
path a runner is told to skip (``--ignore=evals``, ``-k ...``) is the opposite of a run. One level of task runner is
followed: a CI command running ``make <t>``, ``just <t>`` or ``npm run <t>`` (``pnpm``, ``yarn``,
``bun``) names the suite when the root ``Makefile``, ``justfile`` or ``package.json`` recipe for
``<t>`` does.

**Baseline** (``BASELINE_KINDS``), a tracked data file inside the suite: ``snapshot``
(``__snapshots__``, ``*.snap``, ``*.ambr``, a name holding ``snapshot``), ``cached_responses``
(``cassettes``, ``recordings``, ``cached_responses``, a name holding ``cassette``, ``recorded`` or
``cached``), ``tool_budget`` (a name holding ``budget``) or ``baseline`` (a name holding
``baseline`` or ``golden``). A file name alone counts only for a data file, never source code
(``test_snapshot.py``); a folder name counts for every file under it.

Block (``ci_model_jobs.agent_evals`` in run-context.json)::

    {"suites": int, "set_aside": int, "ci_run": int, "baselined": int,
     "present": bool | None, "present_reason": str (only when present is None),
     "dirs": [{"path", "ci": bool, "baseline": [kind, ...]}]}

``present`` (the ``agent-evals.1`` condition) is true when one suite is both named by CI and
holds a baseline. It is null (``eval_ci_unread``) when none is, a baselined suite is not named
by any CI file read, and a CI file or include was not read (``ci_model_jobs.unread`` above 0).
Otherwise false: no suite, or none both run and baselined. Tests:
``tests/test_agent_evals_condition.py``.
"""
from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterable, Mapping
from typing import NotRequired, TypedDict

from lib.ci.ci_masking import IndentTree, parse_lines
from lib.ci.ci_shell_text import command_text

REASON_CI_UNREAD = "eval_ci_unread"
EVAL_DIRS = frozenset({
    "evals", "eval", "evaluations", "evaluation", "llm_evals", "llm-evals", "agent_evals",
    "agent-evals", "ai_evals", "ai-evals", "model_evals", "model-evals", "prompt_evals",
    "prompt-evals", "llm_tests", "llm-tests", "agent_tests", "agent-tests",
})
TEST_TIERS = frozenset({"agent", "agents", "llm"})
TEST_ROOTS = frozenset({"tests", "test", "__tests__", "spec", "specs", "testing"})
SET_ASIDE_DIRS = frozenset({
    "node_modules", "vendor", "vendored", "third_party", "third-party", "external", ".venv", "venv",
    "site-packages", "dist", "build", "fixtures", "__fixtures__", "testdata", "test-data",
    "test_data", "docs", "doc",
})
BASELINE_KINDS = ("baseline", "cached_responses", "snapshot", "tool_budget")
MODEL_REF = re.compile(
    r"(?<![a-z])(?:anthropic|openai|claude|gpt-?(?:3|4|5|o)\w*|gemini|llms?|litellm|langchain|"
    r"llama_?index|ollama|bedrock|vertexai|mistralai|groq|openrouter|chat\.?completions?|"
    r"promptfoo|deepeval|inspect_ai|ragas)(?![a-z])", re.IGNORECASE)
MAX_SUITE_FILES = 200
MAX_FILE_BYTES = 256_000

_CODE = (".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".go", ".rb", ".java", ".kt", ".rs",
         ".sh", ".cs", ".php", ".swift")
_RECORDED_DIRS = frozenset({"cassettes", "recordings", "cached_responses", "recorded"})
_SNAPSHOT_DIRS = frozenset({"__snapshots__", "snapshots", "snapshot"})
_BASELINE_DIRS = frozenset({"baseline", "baselines", "golden", "goldens"})
# The keys whose value runs a shell command, and those whose value names a path a step runs in or
# hands to an action; any other key (a job id, a step name:, a paths: filter) names, never runs.
_SHELL_KEYS = frozenset({"run", "script", "before_script", "after_script", "command", "entrypoint"})
_PATH_KEYS = frozenset({"working-directory", "with", "args"})
_EXCLUDED = re.compile(r"--(?:ignore|ignore-glob|deselect|exclude)(?:=|\s+)\S+|\s-k\s*\S+")
_MARKER = re.compile(r"(?:^|\s)-m\s*([\w-]+)")
_NEVER = re.compile(r"""^(["']?)(?:false|\$\{\{\s*false\s*\}\})\1$""", re.IGNORECASE)
_TIER_ALIASES = ({"eval", "evals"}, {"agent", "agents"})
# A command whose first word only lints, formats, lists, moves or installs: naming the suite there
# runs no eval (``ruff check evals``, ``rm -rf evals``, ``pip install evals``).
NOT_RUNNERS = frozenset({
    "ruff", "black", "isort", "flake8", "pylint", "mypy", "pyright", "eslint", "prettier", "biome",
    "codespell", "yamllint", "markdownlint", "shellcheck", "actionlint", "pre-commit", "rm", "ls",
    "cp", "mv", "mkdir", "touch", "cat", "find", "tar", "zip", "unzip", "chmod", "du", "wc", "git",
    "pip", "pip3", "pipx", "test", "[",
})
_SEGMENT = re.compile(r"&&|\|\||[;|&\n]")
# Launcher words read past before the first word is checked (``python -m pip``, ``uv run ruff``).
_LAUNCHERS = frozenset({"python", "python3", "-m", "uv", "run", "poetry", "pipenv", "pdm", "hatch", "rye", "--"})
_LEAD = re.compile(r"^(?:\s*(?:[A-Za-z_]\w*=\S*|sudo|time|exec|env)\s+)*")
_RUNNER = re.compile(r"""(?:^|[\s;&|(])(?:(make|just)(?:\s+-\S+)*|(?:npm|pnpm|yarn|bun)\s+run|pnpm|yarn)\s+([\w:.-]+)""")
_MAKE_RULE = re.compile(r"^([^\s:#=][^:#=]*?)\s*::?(?!=)")


class EvalSuite(TypedDict):
    path: str
    ci: bool  # named by a committed CI file
    baseline: list[str]  # BASELINE_KINDS found inside the suite


class AgentEvalsBlock(TypedDict):
    suites: int  # suites with a model reference
    set_aside: int  # eval-named folders with no model reference
    ci_run: int
    baselined: int
    present: bool | None
    present_reason: NotRequired[str]
    dirs: list[EvalSuite]


def _same_tier(marker: str, name: str) -> bool:
    """A ``pytest -m`` marker names the suite folder ``name``: the same word, or ``eval`` / ``evals``
    and ``agent`` / ``agents`` read as one; never a longer word holding it (``evaluation_slow``)."""
    marker, name = marker.lower(), name.lower()
    return marker == name or any(marker in tier and name in tier for tier in _TIER_ALIASES)


def suite_of(rel: str) -> str | None:
    """The suite folder ``rel`` sits in (see the module doc), or None."""
    parts = rel.split("/")[:-1]
    for i, part in enumerate(parts):
        if part.lower() in SET_ASIDE_DIRS:
            return None
        if part.lower() in EVAL_DIRS or (part.lower() in TEST_TIERS and i and parts[i - 1].lower() in TEST_ROOTS):
            return "/".join(parts[:i + 1])
    return None


def baseline_kinds(inner: str) -> set[str]:
    """The baseline kinds a file at ``inner`` (relative to its suite) shows."""
    parts = [p.lower() for p in inner.split("/")]
    dirs, name = set(parts[:-1]), parts[-1]
    kinds: set[str] = set()
    data = not name.endswith(_CODE)
    if dirs & _SNAPSHOT_DIRS or name.endswith((".snap", ".ambr")) or (data and "snapshot" in name):
        kinds.add("snapshot")
    if dirs & _RECORDED_DIRS or (data and any(w in name for w in ("cassette", "recorded", "cached"))):
        kinds.add("cached_responses")
    if data and "budget" in name:
        kinds.add("tool_budget")
    if dirs & _BASELINE_DIRS or (data and ("baseline" in name or "golden" in name)):
        kinds.add("baseline")
    return kinds


def _dead(tree: IndentTree) -> set[int]:
    """The lines of every mapping (a step or a job) whose ``if:`` is never true."""
    dead: set[int] = set()
    for i, line in enumerate(tree.lines):
        if line.structural and line.key == "if" and _NEVER.match(line.value) and i in tree.mapping_of:
            keys = tree.siblings[tree.mapping_of[i]]
            dead.update(range(keys[0], max(tree.subtree(k).stop for k in keys)))
    return dead


def _value(tree: IndentTree, i: int) -> str:
    """A key's or list item's value, with a block scalar's lines (``run: |``) joined."""
    value = tree.lines[i].value
    if value[:1] in ("|", ">"):
        return "\n".join(tree.lines[j].value for j in range(i + 1, len(tree.lines)) if tree.owner[j] == i)
    return value


def runnable(command: str) -> str:
    """``command`` without the segments whose first word, past a launcher (``python -m``, ``uv``,
    ``uv run``, ``poetry run``), is in ``NOT_RUNNERS``."""
    keep = []
    for segment in _SEGMENT.split(command):
        words = _LEAD.sub("", segment.strip(" \t(")).split()
        while len(words) > 1 and (words[0] in _LAUNCHERS or words[0].startswith("-")):
            words = words[1:]
        if words and words[0].rsplit("/", 1)[-1] not in NOT_RUNNERS:
            keep.append(segment)
    return "\n".join(keep)


def _utility_with(tree: IndentTree, with_line: int) -> bool:
    """Whether a ``with:`` block belongs to a step using a GitHub ``actions/`` utility (checkout,
    cache, upload or download an artifact, set up a toolchain), whose inputs run no eval."""
    keys = tree.siblings[tree.mapping_of[with_line]] if with_line in tree.mapping_of else []
    uses = next((tree.lines[k].value.strip("'\"") for k in keys if tree.lines[k].key == "uses"), "")
    return uses.lower().startswith("actions/")


def run_texts(text: str) -> list[str]:
    """What a CI file runs: each shell value (``run:``, ``script:`` and its items) as
    ``command_text`` reads it, so a quoted argument, an ``echo`` and a comment name nothing, and
    each ``working-directory:``, ``with:`` or ``args:`` value as written. A job id, a step
    ``name:``, a ``paths:`` filter, a step or job whose ``if:`` is never true, a command segment
    in ``NOT_RUNNERS`` and the ``with:`` inputs of a GitHub ``actions/`` utility run nothing;
    skip flags (``--ignore=evals``, ``-k ...``) are blanked."""
    tree = IndentTree(parse_lines(text.splitlines()))
    dead = _dead(tree)
    out: list[str] = []
    for i, line in enumerate(tree.lines):
        if not line.structural or i in dead:
            continue
        parent = tree.ancestors(i)[:1]
        owner = tree.lines[parent[0]].key if parent else ""
        shell = line.key in _SHELL_KEYS or (line.dash and not line.key and owner in _SHELL_KEYS)
        if shell:
            out.append(_EXCLUDED.sub(" ", runnable(command_text(_value(tree, i)))))
        elif owner == "with" and _utility_with(tree, parent[0]):
            continue
        elif line.key in _PATH_KEYS or owner in _PATH_KEYS:
            out.append(_value(tree, i))
    return [t for t in out if t.strip()]


# A top-level suite folder whose bare name is also a command or subcommand (the shell builtin
# ``eval``, ``promptfoo eval``): only ``eval/``, ``./eval`` or a whole path value names it.
_COMMAND_NAMES = frozenset({"eval"})


def _names(lines: Iterable[str], suite: str) -> bool:
    if suite in _COMMAND_NAMES:
        path = re.compile(r"(?<![\w./-])(?:\./" + re.escape(suite) + r"(?![\w-])|" + re.escape(suite) + r"/)")
    else:
        path = re.compile(r"(?<![\w./-])(?:\./)?" + re.escape(suite) + r"(?![\w-])")
    module = re.compile(r"(?:^|\s)-m\s+" + re.escape(suite.replace("/", ".")) + r"(?![\w-])")
    name = suite.rsplit("/", 1)[-1]
    for line in lines:
        if line.strip() == suite or path.search(line) or module.search(line):
            return True
        if "pytest" in line and any(_same_tier(word, name) for word in _MARKER.findall(line)):
            return True
    return False


def _recipes(read: Callable[[str], str], tracked: frozenset[str], targets: set[str]) -> list[str]:
    """The root ``Makefile`` / ``justfile`` recipe lines and ``package.json`` scripts of ``targets``."""
    out: list[str] = []
    for name in ("Makefile", "makefile", "GNUmakefile", "justfile", "Justfile"):
        if name not in tracked:
            continue
        current = False
        for line in read(name).splitlines():
            if line[:1] in ("\t", " "):
                if current:
                    out.append(line.strip())
                continue
            rule = _MAKE_RULE.match(line)
            if rule:
                current = bool(set(rule.group(1).lstrip("@").split()) & targets)
                rest = line[rule.end():]
                if current and not name.lower().endswith("makefile"):
                    out.append(rest)  # a just recipe's dependencies sit on its line
            elif line.strip():
                current = False
    if "package.json" in tracked:
        try:
            scripts = json.loads(read("package.json")).get("scripts", {})
        except (ValueError, AttributeError):
            scripts = {}
        if isinstance(scripts, dict):
            out += [str(scripts[t]) for t in sorted(targets) if t in scripts]
    return out


def scan_agent_evals(tracked: frozenset[str], read: Callable[[str], str], ci_texts: Mapping[str, str],
                     ci_unread: int) -> AgentEvalsBlock:
    """Build the ``agent_evals`` block from the tracked paths, a reader and the CI texts."""
    members: dict[str, list[str]] = {}
    for rel in sorted(tracked):
        suite = suite_of(rel)
        if suite is not None:
            members.setdefault(suite, []).append(rel)
    lines = [line for text in ci_texts.values() for line in run_texts(text)]
    targets = {m.group(2) for line in lines for m in _RUNNER.finditer(line)}
    lines += [_EXCLUDED.sub(" ", runnable(command_text(r))) for r in _recipes(read, tracked, targets)] if targets else []
    dirs: list[EvalSuite] = []
    set_aside = 0
    for suite, files in members.items():
        inner = [rel[len(suite) + 1:] for rel in files]
        kinds: set[str] = set()
        for name in inner:
            kinds |= baseline_kinds(name)
        recorded = "cached_responses" in kinds
        if not recorded and not any(
                MODEL_REF.search(read(rel)[:MAX_FILE_BYTES]) for rel in files[:MAX_SUITE_FILES]):
            set_aside += 1
            continue
        dirs.append(EvalSuite(path=suite, ci=_names(lines, suite), baseline=sorted(kinds)))
    met = any(d["ci"] and d["baseline"] for d in dirs)
    block = AgentEvalsBlock(suites=len(dirs), set_aside=set_aside, ci_run=sum(d["ci"] for d in dirs),
                            baselined=sum(bool(d["baseline"]) for d in dirs), present=met, dirs=dirs)
    if not met and ci_unread and any(d["baseline"] and not d["ci"] for d in dirs):
        block["present"] = None
        block["present_reason"] = REASON_CI_UNREAD
    return block
