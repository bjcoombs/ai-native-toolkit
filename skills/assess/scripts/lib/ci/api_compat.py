"""API and contract compatibility checks that CI runs (issue #853, theme 8 of #826).

``scan_api_compat(repo_root, ci_checks)`` builds the ``api_compat`` block from two arms. Nothing is
executed: CI files, the scripts, recipes and build files a step leads to, and test files are read
as text, and Python test files are parsed with ``ast``, never imported.

- **Named tool (``runs``).** A committed CI file (a GitHub Actions workflow, local composite
  actions inlined, or ``.gitlab-ci.yml``) invokes a compatibility tool, on its own line or through
  the shared CI-step follower (``lib.ci.ci_step_follower``: a committed script, a ``make`` recipe,
  a Maven or Gradle build, up to ``_MAX_FOLLOW`` levels) or one root ``package.json`` script
  (``npm run <t>``, ``npm test`` / ``t`` / ``tst``, ``pnpm``, ``yarn``, ``bun`` but not bun's own
  ``bun test``), or a GitHub step ``uses:`` one of ``ACTIONS`` (``obi1kenobi/cargo-semver-checks-action``,
  ``bufbuild/buf-breaking-action``, ``joelanford/go-apidiff``). The tools
  (``TOOLS``): ``apidiff`` (or ``go run .../apidiff``), ``api-extractor run`` (not ``--local``, which
  rewrites the report), ``griffe check``, ``cargo semver-checks``, ``buf breaking``, ``japicmp`` and
  ``revapi`` (their Maven goals and Gradle plugins, read from the follower's ``tasks`` and
  ``plugins``). An install (``go install .../apidiff``, ``npm i -D @microsoft/api-extractor``), a
  ``--help`` or ``--version`` call and an ``echo`` invoke nothing. A run counts only when it can
  turn the job red (``can_stop``): ``apidiff`` exits 0 on an incompatible change, so it needs a
  re-raise (``exit 1``) that a read of its output decides (``_decides``: ``test ! -s out.txt || exit 1``)
  in the job, script or recipe that runs it, on the same line or a later one, or a test of its ``$(...)`` output that decides one
  (``if [ -n "$(apidiff a b)" ]; then exit 1; fi``, ``then`` on the next line too, an ``&&`` chain or
  ``||`` fallback, ``inline_apidiff``); a CI step's pipe is read as written (#1617); the japicmp CLI needs an
  ``--error-on-*`` flag, and its Maven or Gradle plugin a ``breakBuild*`` or ``failOn*`` setting
  true. A run that cannot stop the job is listed under ``declared``, never counted.
- **Pinning test (``pins``).** A tracked test file pins the public exports: a Python test whose
  ``ast`` holds an ``==`` comparison with ``__all__`` on one side and a literal list of names, or a
  name bound to one, on the other (``assert set(__all__) == set(EXPECTED_ALL)``; never ``dir(mod)``), or a JavaScript or TypeScript test that compares ``Object.keys`` of a
  module to a snapshot or a literal array or object (``expect(Object.keys(api)).toMatchSnapshot()``;
  never ``.toEqual(Object.keys(other))``). It counts when
  CI runs tests: ``ci_checks.enforced_check_runs`` is true (the layer-5 reading).
- **Declared only (``declared``, listed, never counted).** A tool named in a build file
  (``pom.xml``, a Gradle build, ``package.json``, a Makefile, ``pyproject.toml``, ``Cargo.toml``,
  ``buf.yaml``, ``api-extractor.json``) or a file under ``hack/``, ``scripts/``, ``build/`` or
  ``.github/`` (not a workflow, which is read as CI) that no CI run reaches (griffe only as
  ``griffe check`` or a ``[tool.griffe]`` table, never a griffe dependency), and a CI run that
  cannot stop its job (the CI file is the path). Docs never declare.

Masking follows the #775 rule (``lib.ci.ci_masking.mask_lines``): ``|| true``, ``continue-on-error``,
``allow_failure`` and a never-true ``if:`` do not count, and a form whose failure the step may hide
(``ci_unsure``) is unsettled, never credit. A followed script or recipe line with a ``||`` fallback
that does not re-raise does not count and is not followed (``lib.ci.ci_job_commands.commands``). Only
shell bodies are read: GitHub ``run:``, GitLab ``script:`` and ``before_script:`` (GitLab ignores an
``after_script:`` failure). GitLab is read as GitLab merges it (``lib.ci.ci_merged_view``:
local includes, and an ``extends:`` job's effective ``script:``). On GitLab a job whose ``when:`` or every ``rules:`` entry is ``never``
or ``manual`` runs nothing, and a hidden job (``.compat:``) runs only as far as a visible job that
runs ``extends:`` it without setting that body itself; a tool in a hidden job nothing extends, in a file that uses
a YAML alias as a whole value or a flow-sequence item (never a shell glob), a merge key or
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
from itertools import dropwhile, takewhile
from pathlib import Path

from lib.ci.agent_evals import _idle
from lib.ci.check_command import is_info_invocation
from lib.ci.ci_action_inputs import ReadLocal
from lib.ci.ci_build_follower import Followed
from lib.ci.ci_includes import merged_config
from lib.ci.ci_masking import IndentTree, mask_lines, parse_lines
from lib.ci.ci_merged_view import merged_view
from lib.ci.ci_step_follower import StepFollower, follow_workflow
from lib.ci.ci_step_paths import local_reader
from lib.ci.ci_job_commands import OPERATOR as _OPERATOR
from lib.ci.ci_job_commands import commands as _commands
from lib.ci.ci_job_commands import fallback
from lib.instructions.command_resolver import command_words
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
_OUTPUT_FILE = re.compile(r"(?:\d?>>?|\btee(?:\s+-a)?)\s*([\w./-]+)")
_SUBSHELL = re.compile(r"\s*(?:(?:then|else|do|\{|!)\s+)*\(")   # a command that opens ``( ... )``, not a quoted ``(``
_STATUS_VAR = re.compile(r"(?<![\w$])(\w+)=\$\?")
_CAPTURE = re.compile(r"^\s*(?:local\s+|export\s+)?(\w+)=\$\((.*)\)\s*$")   # ``out=$(apidiff a b)``
# The japicmp settings that make a breaking change stop the build: the Maven plugin's
# ``breakBuildOn*`` / ``breakBuildBasedOnSemanticVersioning`` and the Gradle task's ``failOn*``.
_JAPICMP_SETTING = r"(?:breakBuild(?:On|Based)\w*|failOn(?:Modification|SourceIncompatibility|BinaryIncompatibility))"
_REVAPI_NO_FAIL = re.compile(r"^-Drevapi\.failBuildOnProblemsFound=false$")
_REVAPI_QUIET = re.compile(r"failBuildOnProblemsFound\s*(?:>|=|\(|\.set\()\s*false")
_JAPICMP_FLAG = re.compile(r"^-Djapicmp\." + _JAPICMP_SETTING + r"=true$")
_JAPICMP_BREAK = re.compile(_JAPICMP_SETTING + r"\s*(?:>|=|\(|\.set\()\s*true")
# Remote GitHub actions that run a compatibility check and stop the job on a breaking change.
ACTIONS = {"obi1kenobi/cargo-semver-checks-action": "cargo-semver-checks",
           "bufbuild/buf-breaking-action": "buf-breaking",
           "joelanford/go-apidiff": "apidiff"}   # exits with go-apidiff's code: non-zero on a break
# A YAML alias only where YAML reads one: a whole value (``key: *name``, ``"key": !!seq *name``,
# ``- *name``) or a flow-sequence item (``key: [*name, make]``), never a shell glob (``ls *.txt``).
_ALIASING = re.compile(r"!reference|<<\s*:|^\s*(?:-\s+)?(?:[\"']?[\w.-]+[\"']?\s*:\s+|-\s+)(?:!!?\w+\s+)?"
                       r"(?:\*[\w.-]+\s*(?:#.*)?$|\[(?:[^\]\n]*,)?\s*\*[\w.-]+)", re.M)
# Words a launcher puts before the program it runs (``uv run``, ``npx --yes``, ``pnpm exec``).
_LAUNCHERS = frozenset({"uv", "uvx", "pipx", "poetry", "pdm", "hatch", "rye", "pipenv", "npx", "bunx",
                        "pnpx", "pnpm", "yarn", "bun", "run", "exec", "dlx", "x", "python", "python3", "-m"})
_VALUED = frozenset({"--with", "--from", "--package", "-p", "--python", "--project", "-C", "--directory"})
_PM = frozenset({"npm", "pnpm", "yarn", "bun"})
_NPM_TEST = frozenset({"test", "t", "tst"})   # npm's built-in aliases for ``npm run test``
# ``bun <word>`` runs a script only when the word is not one of bun's own commands (``bun test`` is
# bun's test runner, never the ``test`` script).
_BUN_OWN = frozenset({"test", "install", "i", "add", "a", "remove", "rm", "update", "build", "init", "create",
                      "upgrade", "link", "unlink", "pm", "x", "exec", "outdated", "publish", "patch"})
_DOC_SUFFIXES = (".md", ".rst", ".txt", ".adoc", ".html")
_BUILD_FILES = frozenset({"pom.xml", "build.gradle", "build.gradle.kts", "settings.gradle", "settings.gradle.kts",
                          "package.json", "Makefile", "makefile", "GNUmakefile", "justfile", "pyproject.toml",
                          "Cargo.toml", "buf.yaml", "buf.work.yaml", "api-extractor.json", "noxfile.py", "tox.ini"})
_BUILD_DIRS = ("hack/", "scripts/", "build/", ".github/")
_DECLARED: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("apidiff", re.compile(r"\bapidiff\b")),
    ("api-extractor", re.compile(r"api-extractor")),
    ("griffe", re.compile(r"\bgriffe\s+check\b|^\s*\[tool\.griffe\b", re.M)),   # not a griffe dependency
    ("cargo-semver-checks", re.compile(r"semver-checks")),
    ("buf-breaking", re.compile(r"\bbuf\s+breaking\b")),
    ("japicmp", re.compile(r"japicmp")),
    ("revapi", re.compile(r"\brevapi\b")),
)
_BUF_BREAKING = re.compile(r"^breaking\s*:", re.M)   # the breaking-rules section of a buf.yaml
_SET_ASIDE = ("fixtures/", "testdata/", "vendor/", "node_modules/", "third_party/")
_JS_TEST = re.compile(r"\.(?:test|spec)\.[cm]?[jt]sx?$")
# ``expect(Object.keys(mod))`` against a snapshot, or ``toEqual`` / ``toStrictEqual`` with a literal
# array or object as the argument; ``.toEqual(Object.keys(other))`` compares two live surfaces.
_JS_PIN = re.compile(r"expect\(\s*Object\.keys\(\s*[\w$.]+\s*\)(?:\.sort\(\))?\s*\)\s*"
                     r"\.(?:(?:toMatchSnapshot|toMatchInlineSnapshot)\(|(?:toEqual|toStrictEqual)\(\s*[\[{])")


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


def _shell(line: str) -> str:
    """A CI or script line as shell text, a whole-line capture (``out=$(apidiff a b)``) read as
    the command it runs."""
    text = ci_line_text(line)
    capture = _CAPTURE.match(text)
    return capture.group(2) if capture else text


def _rest(text: str, words: list[str]) -> list[str]:
    """The operator-split parts of one shell line after the command whose words are ``words``,
    less that command's own ``||`` fallback (``apidiff a b || exit 1`` re-raises nothing: apidiff
    exits 0). Operators sit at the even indexes."""
    parts = _OPERATOR.split(text)
    for i in range(0, len(parts), 2):
        if command_words(parts[i].strip().strip("(){} ")) == words:
            rest = parts[i + 1:]
            if rest[:1] == ["||"]:   # skip the whole fallback: up to the next ``;`` or ``&&`` at depth 0
                depth, j = 0, 1
                while j < len(rest) and not (rest[j] in (";", "&&") and depth <= 0):
                    depth += rest[j].count("{") + rest[j].count("(") - rest[j].count("}") - rest[j].count(")")
                    j += 1
                rest = rest[j:]
            return rest
    return []


def _reraises(words: list[str], held: frozenset[str] = frozenset()) -> bool:
    """A command that ends its script red: ``exit`` or ``return`` with a non-zero literal or a
    variable other than ``$?`` or one ``held`` (assigned ``$?``), or ``false``, as the command itself
    (after a ``then``, ``else``, ``do`` or ``{``), never an argument (``echo false``, ``echo exit 1``)."""
    head = list(dropwhile(lambda w: w in ("then", "else", "do", "{"), words))
    code = head[1].strip('"') if len(head) > 1 else ""
    zero = code in ("", "0", "$?") or code.strip("${}") in held   # ``$?`` after apidiff is its 0
    return (head[:1] in (["exit"], ["return"]) and not zero) or head[:1] == ["false"]


def _output_refs(line: str) -> list[str]:
    """How later lines can name what apidiff wrote: the variable a whole-line capture assigns
    (``out=$(apidiff a b)`` gives ``$out``) and the files it is redirected or tee'd to."""
    text = ci_line_text(line)
    capture = _CAPTURE.match(text)
    refs = [f"${capture.group(1)}", f"${{{capture.group(1)}}}"] if capture else []
    # apidiff's own command and its pipe only: ``apidiff a b; echo x > other.txt`` writes other.txt
    tail = re.split(r"&&|\|\||;", text[text.find("apidiff"):], maxsplit=1)[0]
    return refs + [m.group(1) for m in _OUTPUT_FILE.finditer(tail) if not m.group(1).startswith("/dev/")]


def _names(ref: str, text: str) -> bool:
    """Whether ``text`` names ``ref`` as a whole word: ``$out`` is not ``$output``."""
    return re.search(r"(?<![\w./-])" + re.escape(ref) + r"(?![\w./-])", text) is not None


def _test_words(words: list[str]) -> list[str] | None:
    """The operands of a ``[ ... ]``, ``[[ ... ]]`` or ``test ...`` command (an ``if`` before it
    dropped), or None for any other command."""
    words = words[1:] if words[:1] == ["if"] else words
    if words[:1] in (["["], ["[["]) and words[-1:] == ["]" if words[0] == "[" else "]]"]:
        return words[1:-1]
    return words[1:] if words[:1] == ["test"] else None


_COUNT_TEST = {("-ne", "0"): "pos", ("-eq", "0"): "neg", ("-lt", "1"): "neg", ("-le", "0"): "neg"}


def _count_test(op: str, limit: str) -> str | None:
    """The polarity of a line or byte count compared with a literal: ``-gt N`` and ``-ge N``
    (N at least 1) succeed only on a non-empty report, even when a threshold lets a small one
    pass (``-gt 5`` still goes red on a large break)."""
    if limit.isdigit() and (op == "-gt" or (op == "-ge" and int(limit) >= 1)):
        return "pos"
    return _COUNT_TEST.get((op, limit))


def _polarity(words: list[str]) -> str | None:
    """``"pos"`` when a command that reads apidiff's output succeeds on a non-empty report
    (``[ -s out.txt ]``, ``[ -n "$out" ]``, ``[ $(wc -l < out.txt) -gt 0 ]``, ``grep -q . out.txt``),
    ``"neg"`` when it fails on one (``[ ! -s out.txt ]``, ``[ -z "$out" ]``, ``diff``, ``cmp``), else
    None (``cat``: decides nothing). A leading ``if`` is dropped, and each ``!`` (before the command
    or inside the test) turns the polarity over: ``if ! [ -z "$(cat out.txt)" ]`` is ``"pos"``."""
    words = words[1:] if words[:1] == ["if"] else words
    negated = False
    while words[:1] == ["!"]:
        negated, words = not negated, words[1:]
    operands = _test_words(words)
    if operands is None:
        head = _name(words[0]) if words else ""
        flags = "".join(w[1:] for w in words[1:] if w.startswith("-") and not w.startswith("--"))
        if head == "grep" and "v" not in flags and "--invert-match" not in words:
            found: str | None = "pos"
        else:
            found = "neg" if head in ("diff", "cmp") else None
    else:
        while operands[:1] == ["!"]:
            negated, operands = not negated, operands[1:]
        if len(operands) == 2 and operands[0] in ("-s", "-n", "-z"):
            found = "pos" if operands[0] != "-z" else "neg"
        elif len(operands) >= 3:   # ``$(wc -l < f) -gt 0``: the redirect is not a word
            found = _count_test(operands[-2], operands[-1])
        else:
            found = None
    if found is None or not negated:
        return found
    return "neg" if found == "pos" else "pos"


def _words(part: str) -> list[str]:
    return command_words(part.strip().strip("(){} "))


def _and_chain(rest: list[str]) -> list[str]:
    """The commands of the ``&&`` chain that opens ``rest`` (``rest[0]`` is ``&&``), braces kept
    together, up to the next ``||``, ``;`` or ``|`` outside them: in ``[ -s f ] && echo x || exit 1``
    the ``exit 1`` runs only when the test fails, so it is not in the chain."""
    out: list[str] = []
    depth = 0
    for k, part in enumerate(rest[1:]):
        if k % 2:
            if depth <= 0 and part != "&&":
                break
            continue
        depth += part.count("{") + part.count("(") - part.count("}") - part.count(")")
        out.append(part)
    return out


def _branch_reraises(rest: list[str], held: frozenset[str] = frozenset()) -> bool:
    """Whether the ``&&`` chain or ``||`` fallback opening ``rest`` re-raises past any later passing ``||`` (lib/docs)."""
    def ends(m: int) -> bool:   # ``exit N`` the test reaches unconditionally: chained, or in its ``{ }``
        ops, group = rest[0:m:2], "".join(rest[1:m + 1])
        return _exits(_words(rest[m])) and not any(map(_SUBSHELL.match, rest[1:m + 1:2])) and (
            all(o == "&&" for o in ops) and all(_words(p)[:1] in (["echo"], ["printf"]) for p in rest[1:m:2])
            or ops == ["||"] or (rest[m - 1] == ";" and command_words(rest[1])[:1] == ["{"]
                                                            and group.count("{") == 1 and "}" not in group))
    branch = _and_chain(rest) if rest[0] == "&&" else _OPERATOR.split(fallback(rest[1:]))[::2]
    tails = [branch] + [_OPERATOR.split(fallback(rest[k + 1:]))[::2] for k in range(2, len(rest), 2) if rest[k] == "||"]
    raised = [any(_reraises(_words(p), held) for p in parts) for parts in tails]   # the branch, then each later fallback
    return raised[0] and (all(raised) or any(ends(m) for m in range(1, 2 * len(branch), 2)))


def _exits(words: list[str]) -> bool:   # ``exit N``, N non-zero: ends the shell before a later ``|| true``
    return words[:1] == ["exit"] and len(words) > 1 and words[1].strip('"').isdigit() and int(words[1].strip('"')) > 0


def _decides(parts: list[str], refs: list[str], later: list[str], held: frozenset[str], piped: bool = False) -> bool:
    """Whether a pipeline in the operator-split ``parts`` (commands at the even indexes) that reads
    apidiff's output (names one of ``refs``, or, with ``piped``, the first pipeline, which apidiff
    feeds) decides a re-raise: its last command succeeds on a non-empty report and its ``&&`` chain
    holds the re-raise (``_and_chain``), or fails on one and ``||`` does, or it is an ``if`` test that succeeds on
    one with a re-raise in its ``then`` branch (``then`` on that line or the next, read on across
    ``later`` lines). An ``exit 1``
    after a command that only prints the report (``cat out.txt; go build || exit 1``) is not."""
    i = 0
    while i < len(parts):
        j = i
        while j + 1 < len(parts) and parts[j + 1] == "|":
            j += 2
        reads = (piped and i == 0) or any(_names(r, p) for p in parts[i:j + 1:2] for r in refs)
        words, rest = _words(parts[j]), parts[j + 1:]
        polarity = _polarity(words) if reads else None
        if polarity is not None and words[:1] == ["if"]:
            if polarity == "pos" and rest[:1] in ([";"], []) and any(_reraises(c, held) for c in _then_body(rest, later)):
                return True
        elif polarity is not None and len(rest) > 1 and (polarity, rest[0]) in (("pos", "&&"), ("neg", "||")):
            if _branch_reraises(rest, held):
                return True
        i = j + 2
    return False


def _checks_output(words: list[str], unit: list[str]) -> bool:
    """Whether apidiff's output decides a re-raise (``_decides``): the pipeline it feeds on its own
    line (``apidiff a b | grep -q . && exit 1``), or a later command of its line or of a later line
    of the same shell that reads its captured variable or output file
    (``apidiff a b > out.txt; test ! -s out.txt || exit 1``, ``if [ -s out.txt ]; then exit 1; fi``).
    An ``exit 1`` that reads nothing apidiff wrote, or that its read does not decide, is not its check."""
    if not unit:
        return False
    held = frozenset(_STATUS_VAR.findall("\n".join(unit)))   # ``rc=$?`` holds apidiff's 0
    first = _shell(unit[0])
    refs = _output_refs(unit[0])
    rest = _rest(first, words)
    piped = rest[:1] == ["|"]
    if not _rescued("".join(rest[1:])) and _decides(rest[1:], refs, unit[1:], held, piped):
        return True
    return any(not _rescued(ci_line_text(line)) and _decides(_OPERATOR.split(ci_line_text(line)), refs, unit[k + 1:], held)
               for k, line in enumerate(unit[1:], start=1))


def _rescued(text: str) -> bool:
    """Whether a passing ``||`` fallback follows a command of the line as written
    (``if [ -s o ]; then false; fi || true``): its re-raise cannot turn the job red. Read here
    because a CI line's masking drops the ``if`` test and with it this ``fi || true``."""
    return any(c["masked"] for c in _commands(_shell(text)))


_SUBST = re.compile(r"^\$\((.*)\)$", re.S)


def _tested_substitution(words: list[str]) -> tuple[str, list[str]] | None:
    """``(flag, inner words)`` for a test of one command substitution's output:
    ``[ -n "$(apidiff a b)" ]``, ``[[ -z ... ]]``, ``test -n ...``, an ``if`` before any of them."""
    operands = _test_words(words)
    if operands is None:
        return None
    words = operands
    sub = _SUBST.match(words[1]) if len(words) == 2 and words[0] in ("-n", "-z") else None
    return (words[0], command_words(sub.group(1))) if sub else None


def _then_body(parts: list[str], later: list[str]) -> list[list[str]]:
    """The commands of an ``if``'s ``then`` branch, up to ``else``, ``elif`` or ``fi``: from the
    operator-split ``parts`` after the condition, then from the ``later`` lines of the shell.
    Empty when a later ``fi || true`` rescues a branch with no bare ``exit N`` line (lib/docs)."""
    cmds = [command_words(p.strip().strip("(){} ")) for p in parts[1::2]]
    cmds += [c["words"] for line in later for c in _commands(_shell(line)) if not c["masked"]]
    if not cmds or cmds[0][:1] != ["then"]:
        return []
    out: list[list[str]] = []
    for cmd in [cmds[0][1:], *cmds[1:]]:
        if cmd[:1] in (["else"], ["elif"], ["fi"]):
            break
        out.append(cmd)
    fi = _fi_line(cmds[:len(parts[1::2])], later)
    if fi is None or not _rescued(later[fi]):
        return out
    span = list(takewhile(lambda t: not re.match(r"\s*(?:else|elif)\b", t), [*parts[1::2], *map(ci_line_text, later[:fi])]))
    nested = any(c[:1] in (["if"], ["case"], ["while"], ["for"], ["until"]) for c in out) or any(map(_SUBSHELL.match, span))
    return out if not nested and any(_exits(command_words(re.sub(r"^\s*then\s+", "", t))) for t in span) else []


def _fi_line(same: list[list[str]], later: list[str]) -> int | None:
    """The index in ``later`` of the ``fi`` closing the tested ``if``, nested blocks counted."""
    depth = 1
    for k, cmds in enumerate([same, *([c["words"] for c in _commands(_shell(line))] for line in later)]):
        for words in cmds:
            head = next((w for w in words if w not in ("then", "else", "do")), "")
            depth += (head == "if") - (head == "fi")
            if depth <= 0:
                return k - 1 if k > 0 else None
    return None   # never closed, or on the test's own line (the caller's ``_rescued``)


def inline_apidiff(text: str, later: list[str]) -> bool | None:
    """Whether a shell line runs apidiff inside a test of its output (``$(...)``): None when it
    does not, True when the test decides a re-raise (``if [ -n "$(apidiff a b)" ]; then exit 1;
    fi``, ``test -n ... && exit 1``, ``[ -z ... ] || exit 1``, the ``then`` branch read on to
    ``fi`` across ``later`` lines), else False (``-z`` with ``then exit 1`` goes red when nothing
    changed, never on a break)."""
    parts = _OPERATOR.split(text)
    found: bool | None = None
    for i in range(0, len(parts), 2):
        words = command_words(parts[i].strip().strip("(){} "))
        tested = _tested_substitution(words)
        if tested is None or tool_of(tested[1]) != "apidiff":
            continue
        flag, rest = tested[0], parts[i + 1:]
        op = rest[0] if len(rest) > 1 else ""
        if words[0] == "if":   # ``then`` after ``;`` or on the next line
            found = found or (flag == "-n" and op in (";", "") and any(_reraises(c) for c in _then_body(rest, later)))
        else:   # the chain or fallback ``_decides`` reads
            found = found or ((flag, op) in (("-n", "&&"), ("-z", "||")) and _branch_reraises(rest))
    return found


def can_stop(tool: str, words: list[str], unit: list[str]) -> bool:
    """Whether a run of ``tool`` can turn its job red. ``apidiff`` exits 0 on an incompatible
    change, so it counts only when its output decides a re-raise later in ``unit``, the lines
    from the call to the end of its shell (a GitHub step, a GitLab job's script, a followed
    script or recipe): ``_checks_output``. The japicmp CLI exits 0 unless an ``--error-on-*``
    flag is passed. Every other tool exits non-zero on a breaking change by default."""
    if tool == "apidiff":
        return _checks_output(words, unit)
    if tool == "japicmp":
        return any(w.startswith("--error-on-") for w in words)
    return True


def build_tools(followed: Followed, read_local: ReadLocal, words: list[str]) -> tuple[set[str], set[str], bool]:
    """``(run, report_only, unsettled)`` for a Maven or Gradle step: the tools its bound plugins
    and tasks reach, those that cannot stop the build (japicmp with no ``breakBuild*`` or
    ``failOn*`` setting true in a build file read, nor a ``-Djapicmp.breakBuild*=true``; a Maven
    revapi run that reaches no ``revapi-maven-plugin:check`` goal, or revapi with
    ``failBuildOnProblemsFound`` set false), and
    whether the reading left a part unsettled (a ``-Djapicmp.skip``, an unread ``apply from:``)."""
    names = [n.lower() for n in (*followed.plugins, *followed.tasks)]
    found = {tool for tool in ("japicmp", "revapi") if any(tool in n for n in names)}
    if any(t in u.lower() for u in followed.unknown for t in found):
        return set(), set(), True   # a property may skip the goal: not credit
    quiet: set[str] = set()
    if "revapi" in found and (
            (followed.kind == "maven" and not any(t.lower().startswith("revapi-maven-plugin:check") for t in followed.tasks))
            or any(_REVAPI_NO_FAIL.match(w) for w in words)
            or any(_REVAPI_QUIET.search(read_local(p) or "") for p in followed.paths)):
        quiet.add("revapi")   # only the ``check`` goal stops the build, and only with failing left on
    if "japicmp" in found and not (any(_JAPICMP_FLAG.match(w) for w in words)
                                   or any(_JAPICMP_BREAK.search(read_local(p) or "") for p in followed.paths)):
        quiet.add("japicmp")
    return found - quiet, quiet, bool(followed.unknown)


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
        self.report_only: set[str] = set()   # tools run in a form that cannot stop the job

    def lines(self, lines: list[str], depth: int = 0, path: tuple[str, ...] = (),
              raw: list[str] | None = None) -> set[str]:
        """The tools the shell ``lines`` run, followed into the repository. ``raw`` holds the same
        lines before masking (a CI file's), which apidiff's output check reads: masking drops an
        ``if`` condition such as ``[ -s out.txt ]`` that names the output."""
        found: set[str] = set()
        as_written, raw = raw is not None, raw if raw is not None else lines   # as written: a CI file's shell
        for index, line in enumerate(lines):
            commands = list(_commands(_shell(line)))
            inline = inline_apidiff(ci_line_text(raw[index]), raw[index + 1:])   # masking drops an ``if`` test
            if inline and not any(c["masked"] for c in commands) and not _rescued(ci_line_text(raw[index])):
                found.add("apidiff")
            elif inline is not None:
                self.report_only.add("apidiff")
            text = ci_line_text(raw[index])   # a piped apidiff masking dropped (pipefail off), read as written
            piped = [c["words"] for c in _commands(_shell(text)) if as_written and tool_of(c["words"]) == "apidiff"]
            if piped and not any(c["masked"] for c in commands) and not _rescued(text) and any(
                    _checks_output(words, raw[index:]) for words in piped):
                found.add("apidiff")
            elif piped:
                self.report_only.add("apidiff")
            for cmd in commands:
                tool = tool_of(cmd["words"]) if not cmd["masked"] else None
                if tool is not None and can_stop(tool, cmd["words"], raw[index:]):
                    found.add(tool)
                elif tool is not None:
                    self.report_only.add(tool)
                if not cmd["masked"] and depth < _MAX_FOLLOW:
                    found |= self._npm(cmd["words"], depth, path)
            if depth >= _MAX_FOLLOW or any(c["masked"] for c in commands):
                continue   # a fallback that can pass: what the line runs cannot fail the job
            words = [w for c in commands for w in c["words"]]
            for followed in self.follower.step(line):
                if any(p in path for p in followed.paths):
                    continue
                tools, quiet, unsettled = build_tools(followed, self.read_local, words)
                found |= tools
                self.report_only |= quiet
                self.unsettled |= unsettled or (followed.kind == "workflow" and bool(followed.unknown))
                found |= self.lines(list(followed.commands), depth + 1, (*path, *followed.paths))
        return found

    def _npm(self, words: list[str], depth: int, path: tuple[str, ...]) -> set[str]:
        """The tools a root ``package.json`` script runs, for ``npm run <t>`` and its kin."""
        if not words or _name(words[0]) not in _PM:
            return set()
        pm = _name(words[0])
        rest = [w for w in words[1:] if not w.startswith("-")]
        if rest[:1] in (["run"], ["run-script"]) and len(rest) > 1:
            target: str | None = rest[1]
        elif pm == "npm":
            target = "test" if rest[:1] and rest[0] in _NPM_TEST else None
        elif rest and rest[0] not in _LAUNCHERS and not (pm == "bun" and rest[0] in _BUN_OWN):
            target = rest[0]
        else:
            target = None
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


def _gitlab_jobs(tree: IndentTree) -> tuple[dict[int, frozenset[str]], set[int]]:
    """``(running, untaken)``: each top-level GitLab job that runs on a pipeline with the shell
    bodies of it that run, and the hidden jobs nothing that runs extends. A visible job that is not
    a global keyword and not idle runs all of ``_GITLAB_BODY``. A hidden job runs a body only
    through a job that runs, ``extends:`` it and does not set that body itself: GitLab's
    ``extends`` replaces an array, so ``test: {extends: .compat, script: [...]}`` runs its own
    ``script:``, never the template's."""
    tops = {i: line.key for i, line in enumerate(tree.lines) if line.structural and line.level == 0 and line.key
            and not tree.ancestors(i)}
    idle = _idle(tree)
    running = {i: _GITLAB_BODY for i, key in tops.items()
               if not key.startswith(".") and key not in _GLOBALS and i not in idle}
    by_name = {key: i for i, key in tops.items()}
    queue = list(running)
    while queue:
        e = queue.pop()
        own = frozenset(tree.lines[k].key for k in tree.children(e)) & _GITLAB_BODY
        for name in _extends(tree, e):
            j = by_name.get(name)
            if j is None or j in idle or not tops[j].startswith("."):
                continue
            kept = running.get(j, frozenset()) | (running[e] - own)
            if kept != running.get(j):
                running[j] = kept
                queue.append(j)
    untaken = {i for i, key in tops.items() if key.startswith(".") and not running.get(i)}
    return running, untaken


def _dropped_if(lines: list[str], unmasked: list[str], tree: IndentTree, i: int) -> bool:
    """Whether a blanked line is kept as written: ``fi``/``elif``/``else``, or a lone ``if`` test whose next line survives (lib/docs)."""
    text, nxt = ci_line_text(lines[i]).lstrip(), i + 1
    kept = any(ci_line_text(u).strip("|>-+ ") and tree.body(j) == tree.body(i) for j, u in enumerate(unmasked))   # not masked
    return kept and re.match(r"(?:fi|elif|else)\b", text) is not None or (text.startswith("if ") and nxt < len(unmasked)
                                                   and bool(unmasked[nxt].strip()) and tree.body(nxt) == tree.body(i))


def _job_lines(text: str, gitlab: bool) -> tuple[list[tuple[list[str], list[str]]], list[str], list[str], set[str]]:
    """``(shells, untaken, unsure, actions)``: the unmasked lines of each shell that runs (a GitHub
    ``run:`` step, a running GitLab job's ``before_script:`` and ``script:``) beside the same
    lines as written, the lines of
    GitLab hidden jobs nothing extends, the lines whose exit status the step may hide
    (``ci_unsure``), and the tools of the ``ACTIONS`` an unmasked GitHub step ``uses:``."""
    keys = _GITLAB_BODY if gitlab else _GITHUB_BODY
    lines = text.splitlines()
    unmasked, unsure = mask_lines(text)
    tree = IndentTree(parse_lines(lines))
    running, untaken = _gitlab_jobs(tree) if gitlab else ({}, set())
    spans: dict[int, list[str]] = {}
    raws: dict[int, list[str]] = {}
    hidden: list[str] = []
    doubt: list[str] = []
    actions: set[str] = set()
    for i, line in enumerate(unmasked):
        ref = tree.lines[i].value.strip("'\" ").split("@", 1)[0].lower()
        if not gitlab and tree.lines[i].key == "uses" and line.strip() and ref in ACTIONS:
            actions.add(ACTIONS[ref])
        if tree.lines[tree.body(i)].key not in keys:
            continue
        start = tree.job(i).start
        shell = start if gitlab else tree.body(i)   # a GitHub step is its own shell; a GitLab job one
        if gitlab and start in untaken:
            hidden.append(line)
        elif not gitlab or tree.lines[tree.body(i)].key in running.get(start, frozenset()):
            if line.strip() or _dropped_if(lines, unmasked, tree, i):
                spans.setdefault(shell, []).append(line)
                raws.setdefault(shell, []).append(lines[i])
            if unsure[i].strip():
                doubt.append(unsure[i])
    return [(spans[k], raws[k]) for k in spans], hidden, doubt, actions


def _declared(tracked: frozenset[str], read_local: ReadLocal, ci_files: frozenset[str]) -> dict[str, list[str]]:
    """Tool token to the build files that name it (module docstring), sorted."""
    out: dict[str, list[str]] = {}
    for rel in sorted(tracked):
        base = posixpath.basename(rel)
        if rel.endswith(_DOC_SUFFIXES) or any(s in f"/{rel}" for s in ("/" + d for d in _SET_ASIDE)):
            continue
        if rel in ci_files or (base not in _BUILD_FILES and not rel.startswith(_BUILD_DIRS)
                               and not base.startswith("build.gradle")):
            continue   # a CI file is read as CI, never as a declaration
        text = read_local(rel) or ""
        for tool, pattern in _DECLARED:
            if pattern.search(text) or (tool == "buf-breaking" and base.startswith("buf") and _BUF_BREAKING.search(text)):
                out.setdefault(tool, []).append(rel)
    return out


_WRAPS = frozenset({"set", "sorted", "list", "tuple", "frozenset"})


def _literal_strs(node: ast.AST) -> bool:
    """A list, tuple or set literal of one or more string constants."""
    return (isinstance(node, (ast.List, ast.Tuple, ast.Set)) and bool(node.elts)
            and all(isinstance(e, ast.Constant) and isinstance(e.value, str) for e in node.elts))


def _python_pins(text: str) -> bool:
    """An ``==`` comparison in a Python test with ``__all__`` on one side and, on the other, a
    literal of names or a name bound to one (``EXPECTED_ALL``), optionally wrapped in ``set``,
    ``sorted``, ``list``, ``tuple`` or ``frozenset``, and nothing else. A comparison with ``dir(mod)`` or
    ``inspect.getmembers`` checks ``__all__`` against the module, not against a fixed list."""
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return False
    fixed = {t.id for n in ast.walk(tree) if isinstance(n, (ast.Assign, ast.AnnAssign)) and n.value is not None
             and _literal_strs(n.value) for t in (n.targets if isinstance(n, ast.Assign) else [n.target])
             if isinstance(t, ast.Name)}

    def names_all(node: ast.AST) -> bool:
        return any((isinstance(n, ast.Name) and n.id == "__all__")
                   or (isinstance(n, ast.Attribute) and n.attr == "__all__") for n in ast.walk(node))

    def pinned(node: ast.AST) -> bool:
        """The side is the fixed list itself: a literal, a name bound to one, or one wrapped in
        ``set``, ``sorted``, ``list``, ``tuple`` or ``frozenset``; ``set(dir(m)) - {"x"}`` is not."""
        if _literal_strs(node) or (isinstance(node, ast.Name) and node.id in fixed):
            return True
        return (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _WRAPS
                and len(node.args) == 1 and not node.keywords and pinned(node.args[0]))

    for node in ast.walk(tree):
        if isinstance(node, ast.Compare) and len(node.ops) == 1 and isinstance(node.ops[0], ast.Eq):
            left, right = node.left, node.comparators[0]
            if (names_all(left) and not names_all(right) and pinned(right)) or (
                    names_all(right) and not names_all(left) and pinned(left)):
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


def gitlab_texts(repo: Path, raw: str, reader: _Reader) -> list[str]:
    """The GitLab texts to read, as GitLab merges them (``lib.ci.ci_merged_view``, #1180): each
    source with its merged jobs blanked, then the rendered merged jobs, so an ``extends:`` job runs
    its effective ``script:`` (an array the template's is replaced by) and a local include is
    read. A rendered line the view could not settle is left out and marks the reading unsettled.
    With no view (no readable merged configuration), the root file alone, read with
    ``_gitlab_jobs``'s own ``extends:`` rule."""
    config = merged_config(repo)
    view = merged_view(config) if config is not None and config.sources[0] == raw else None
    if view is None:
        return [raw]
    reader.unsettled |= bool(view.unsettled) or bool(config is not None and config.open)
    rendered = "\n".join("" if i in view.unsettled else line for i, line in enumerate(view.rendered.splitlines()))
    return [*view.texts, rendered]


def scan_api_compat(repo_root: Path, ci_checks: Mapping[str, object] | None = None) -> ApiCompatBlock:
    """Build the ``api_compat`` block (module docstring)."""
    repo = repo_root.resolve()
    tracked = repo_files(repo)
    read_local: ReadLocal = local_reader(repo, tracked)
    files = ci_config_files(tracked)
    reader = _Reader(read_local, tracked)
    runs: dict[tuple[str, str], None] = {}
    quiet: dict[tuple[str, str], None] = {}   # run in a form that cannot stop the job: declared
    unsure_hit = False
    for rel in files:
        gitlab = rel in CI_CONFIG_FILES
        raw = read_config(repo, rel)
        texts = gitlab_texts(repo, raw, reader) if gitlab else [follow_workflow(raw, read_local).text]
        reader.report_only = set()
        tools: set[str] = set()
        for text in texts:
            jobs, hidden, doubt, actions = _job_lines(text, gitlab)
            tools |= actions
            for lines, raw_lines in jobs:
                tools |= reader.lines(lines, raw=raw_lines)
            unsure_hit |= bool(_Reader(read_local, tracked).lines(doubt))
            unsure_hit |= bool(_ALIASING.search(text)) and bool(_Reader(read_local, tracked).lines(hidden))
        for tool in sorted(tools):
            runs[(rel, tool)] = None
        for tool in sorted(reader.report_only - tools):
            quiet[(rel, tool)] = None
    run_rows = [ApiCompatRun(path=rel, tool=tool) for rel, tool in runs]
    reached = {tool for _, tool in runs}
    declared = {t: paths for t, paths in _declared(tracked, read_local, frozenset(files)).items() if t not in reached}
    for rel, tool in quiet:
        if tool not in reached:
            declared.setdefault(tool, []).append(rel)
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
