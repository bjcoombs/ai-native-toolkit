"""Docs and generated files that CI keeps true (issue #852, theme 7 of #826).

``scan_docs_kept_true(repo_root)`` reads the committed CI files (GitHub Actions workflows
directly under ``.github/workflows/`` and ``.gitlab-ci.yml``) and the repository files a step
runs, followed through the shared CI-step follower (``lib.ci.ci_step_follower``: a committed
script, a ``make`` recipe, up to ``_MAX_FOLLOW`` levels). Nothing is executed. It returns the
``doc_staleness.kept_true`` block, one ``checks`` entry per CI file and kind:

- **regen_diff.** A ``git diff`` with ``--exit-code`` or ``--quiet`` after an earlier command
  of the same job (or of the same followed script or recipe) that is not a ``git``, shell
  built-in or install command: the job regenerates files, then fails when they differ from
  the commit. ``covers`` is the diff's literal path arguments (``git diff --exit-code --
  docs/``); ``.`` and no path name no subtree.
- **verify_pair.** A step runs a committed ``verify-<x>`` script (``hack/verify-codegen.sh``)
  and the tree tracks its ``update-<x>`` sibling in the same folder.
- **doc_tests.** A step runs doc examples as tests: ``--doctest-modules`` /
  ``--doctest-glob``, ``python -m doctest``, a Sphinx ``doctest`` builder (``-b doctest``,
  ``-M doctest``), ``cargo test --doc``, ``pytest --markdown-docs``, or an ``mdtest``,
  ``phmdoctest`` or ``byexample`` run; or a CI step runs ``pytest`` and the root
  ``pyproject.toml``, ``pytest.ini``, ``setup.cfg`` or ``tox.ini`` (read by name) adds a
  ``--doctest-`` flag. A doctest flag in configuration that no CI step runs does not count.
  ``covers`` is the command's literal path arguments that name a tracked file or folder, for
  a run that executes doc files only (``--doctest-glob``, ``--markdown-docs``, ``python -m
  doctest``, Sphinx, the mdtest-style runners): ``--doctest-modules``, ``cargo test --doc`` and
  the configuration-only case test code docstrings, so they meet the condition with no subtree.
  With ``--doctest-glob`` the covered paths are the tracked files under those arguments whose
  name matches a glob, since pytest collects no other doc file.

Masking (#775, ``lib.ci.ci_masking.mask_lines``): a CI file is read through its unmasked
lines, so ``continue-on-error``, ``|| true`` and the not-measured forms (``git diff
--exit-code || echo changed``) never count. A followed script or recipe line is not
YAML: there a command followed by ``||`` counts only when the fallback re-raises
(``exit 1``, ``exit $rc``, ``false``) before the next ``;`` or ``&&``.

``present`` is true when any check is found. With none found and a tracked CI file of a
system this reader does not parse (``lib.unread_ci``: Jenkins, Prow, Buildkite ...), it is
null with ``present_reason`` ``ci_not_read``, never false. ``kept_true_or_unknown`` is the
scan the staleness collector calls: a reader fault reads null (``scan_failed``) there.

``apply_kept_true(block, kept)`` joins the reading to doc staleness (#1389): a doc whose
path lies in a subtree a check ``covers`` gets ``kept_true_by`` (the check's ``kind`` and CI
``path``) and reads fresh (``lib.docs.doc_complexity_join.is_stale``), and the summary is
recomputed.

Full reference: lib/docs/docs_kept_true.md.

Inward-only imports; never an orchestrator. Tests: ``tests/test_docs_kept_true.py``.
"""
from __future__ import annotations

import posixpath
import re
from fnmatch import fnmatch
from collections.abc import Iterator
from pathlib import Path
from typing import TypedDict

from lib.ci.ci_action_inputs import ReadLocal
from lib.ci.ci_kept_types import DocsKeptTrueBlock, KeptCheck, KeptKind, KeptTrueBy
from lib.ci.ci_masking import IndentTree, mask_lines, parse_lines
from lib.ci.ci_step_follower import StepFollower, follow_workflow
from lib.ci.ci_step_paths import local_reader, script_path
from lib.docs.doc_staleness import DocStalenessBlock, staleness_summary
from lib.instructions.command_index import CI_CONFIG_FILES, ci_config_files, ci_line_text, read_config, repo_files
from lib.instructions.command_resolver import command_words
from lib.unread_ci import unread_ci_paths

REASON_CI_NOT_READ = "ci_not_read"
REASON_SCAN_FAILED = "scan_failed"
PYTEST_CONFIGS = ("pyproject.toml", "pytest.ini", "setup.cfg", "tox.ini")
_MAX_FOLLOW = 3
_OPERATOR = re.compile(r"(&&|\|\||;|\|)")
_RERAISE = re.compile(r"\bexit\s+(?:[1-9]\d*|\"?\$)|(?:^|[\s;{(])false\b")
_VERIFY = re.compile(r"^(?P<dir>(?:.*/)?)verify-(?P<name>[\w.-]+)$")
_NOT_REGEN = frozenset({"git", "echo", "printf", "cd", "pushd", "popd", "ls", "cat", "pwd", "mkdir",
                        "set", "export", "true", ":", "test", "[", "exit", "apt-get", "apt", "brew"})
_BODY_KEYS = frozenset({"run", "script", "before_script", "after_script"})
_DOCTEST_RUNNERS = frozenset({"mdtest", "phmdoctest", "byexample"})
_WRAPPERS = frozenset({"uv", "poetry", "pdm", "hatch", "pipenv", "rye", "python", "python3", "run", "exec"})


class _Cmd(TypedDict):
    words: list[str]
    masked: bool  # a ``||`` fallback that does not re-raise follows it


def _commands(text: str) -> Iterator[_Cmd]:
    """Each command of one shell line, with whether a passing ``||`` fallback follows it."""
    parts = _OPERATOR.split(text)
    for i in range(0, len(parts), 2):
        words = command_words(parts[i].strip().strip("(){} "))
        if not words:
            continue
        rest = parts[i + 1:]
        masked = bool(rest) and rest[0] == "||" and not _RERAISE.search(_fallback(rest[1:]) + " ")
        yield _Cmd(words=words, masked=masked)


def _fallback(rest: list[str]) -> str:
    """The ``||`` fallback's text: up to the next ``;`` or ``&&``, braces kept together."""
    out: list[str] = []
    depth = 0
    for part in rest:
        if part in (";", "&&") and depth <= 0:
            break
        depth += part.count("{") + part.count("(") - part.count("}") - part.count(")")
        out.append(part)
    return " ".join(out)


def _program(words: list[str]) -> list[str]:
    """``words`` from the program a wrapper runs (``uv run pytest`` reads ``pytest``)."""
    i = 0
    while i < len(words) - 1 and (words[i] in _WRAPPERS or words[i] == "-m"):
        i += 1
    return words[i:]


def _named_paths(args: list[str], tracked: frozenset[str]) -> list[str]:
    """The literal arguments that name a tracked file or folder (never ``.``)."""
    out: list[str] = []
    for arg in args:
        if arg.startswith("-") or any(c in arg for c in "=$*?") or arg.startswith(("/", "~")):
            continue
        rel = posixpath.normpath(arg.removeprefix("./"))
        if rel in (".", "") or rel.startswith(".."):
            continue
        if rel in tracked or any(t.startswith(rel + "/") for t in tracked):
            out.append(rel)
    return list(dict.fromkeys(out))


def _diff_check(words: list[str]) -> bool:
    return words[:2] == ["git", "diff"] and any(w in ("--exit-code", "--quiet") for w in words[2:])


def _doc_test(words: list[str]) -> bool:
    prog = _program(words)
    head = posixpath.basename(prog[0]) if prog else ""
    pairs = set(zip(prog, prog[1:]))
    return (any(w.startswith(("--doctest-modules", "--doctest-glob", "--markdown-docs")) for w in prog)
            or _python_doctest(words)
            or ("-b", "doctest") in pairs or ("-M", "doctest") in pairs
            or (head == "cargo" and "test" in prog and "--doc" in prog)
            or head in _DOCTEST_RUNNERS)


def _python_doctest(words: list[str]) -> bool:
    """``python -m doctest`` (``python3``, through a wrapper), not a ``pytest -m doctest`` marker."""
    return any(posixpath.basename(a) in ("python", "python3") and b == "-m" and c == "doctest"
               for a, b, c in zip(words, words[1:], words[2:]))


def _doc_globs(words: list[str]) -> list[str]:
    """The ``--doctest-glob`` patterns of a pytest run (``--doctest-glob=*.md`` or two words)."""
    out: list[str] = []
    for i, w in enumerate(words):
        if w.startswith("--doctest-glob="):
            out.append(w.split("=", 1)[1].strip("'\""))
        elif w == "--doctest-glob" and i + 1 < len(words):
            out.append(words[i + 1].strip("'\""))
    return out


def _runs_doc_files(words: list[str]) -> bool:
    """Whether the doc-test run executes doc files (markdown, reST), not code docstrings only:
    only such a run gives its paths to ``covers``."""
    prog = _program(words)
    head = posixpath.basename(prog[0]) if prog else ""
    pairs = set(zip(prog, prog[1:]))
    return (any(w.startswith(("--doctest-glob", "--markdown-docs")) for w in prog)
            or _python_doctest(words)
            or ("-b", "doctest") in pairs or ("-M", "doctest") in pairs or head in _DOCTEST_RUNNERS)


def _runs_pytest(words: list[str]) -> bool:
    prog = _program(words)
    return bool(prog) and posixpath.basename(prog[0]) in ("pytest", "py.test")


def _regenerates(words: list[str]) -> bool:
    head = posixpath.basename(words[0])
    return head not in _NOT_REGEN and "install" not in words[:3] and words[:2] != ["npm", "ci"]


class _Reader:
    def __init__(self, repo: Path, tracked: frozenset[str]) -> None:
        self.tracked = tracked
        self.read_local: ReadLocal = local_reader(repo, tracked)
        self.follower = StepFollower(self.read_local, tracked)
        self.doctest_config = any(_config_doctest(self.read_local(name) or "") for name in PYTEST_CONFIGS)

    def job(self, lines: list[str]) -> list[tuple[KeptKind, list[str]]]:
        """The checks found in one job's unmasked lines, in order."""
        found: list[tuple[KeptKind, list[str]]] = []
        self._sequence(lines, found, (), 0)
        return found

    def _sequence(self, lines: list[str], found: list[tuple[KeptKind, list[str]]],
                  path: tuple[str, ...], depth: int) -> None:
        regenerated = False
        for line in lines:
            for cmd in _commands(ci_line_text(line)):
                words = cmd["words"]
                if not cmd["masked"]:
                    self._classify(words, regenerated, found)
                if _regenerates(words) and not _diff_check(words):
                    regenerated = True
            if depth >= _MAX_FOLLOW:
                continue
            for followed in self.follower.step(line):
                if any(p in path for p in followed.paths):
                    continue
                inner: list[tuple[KeptKind, list[str]]] = []
                self._sequence(list(followed.commands), inner, (*path, *followed.paths), depth + 1)
                found.extend(inner)

    def _classify(self, words: list[str], regenerated: bool, found: list[tuple[KeptKind, list[str]]]) -> None:
        if _diff_check(words) and regenerated:
            args = words[words.index("--") + 1:] if "--" in words else words[2:]
            found.append(("regen_diff", _named_paths(args, self.tracked)))
        elif _doc_test(words) or (self.doctest_config and _runs_pytest(words)):
            covers = _named_paths(_program(words)[1:], self.tracked) if _runs_doc_files(words) else []
            globs = _doc_globs(words)
            if globs and not any(w.startswith("--markdown-docs") for w in words):
                covers = [t for t in sorted(self.tracked) for c in covers  # only the files pytest collects
                          if (t == c or t.startswith(c + "/")) and any(fnmatch(posixpath.basename(t), g) for g in globs)]
            found.append(("doc_tests", list(dict.fromkeys(covers))))
        script = script_path(" ".join(words))
        match = _VERIFY.match(script) if script else None
        if match and script in self.tracked:
            stem = posixpath.basename(script)[len("verify-"):]
            if f"{match['dir']}update-{stem}" in self.tracked:
                found.append(("verify_pair", []))


def _config_doctest(text: str) -> bool:
    """A pytest configuration that adds a ``--doctest-`` flag (comments skipped)."""
    return any("--doctest-" in line.split("#", 1)[0] for line in text.splitlines())


def _job_lines(text: str) -> list[list[str]]:
    """Each job's unmasked shell lines in order (GitHub ``jobs.<id>``, GitLab top-level keys):
    the lines of a ``run:``, ``script:``, ``before_script:`` or ``after_script:`` body."""
    lines = text.splitlines()
    unmasked, _unsure = mask_lines(text)
    tree = IndentTree(parse_lines(lines))
    spans: dict[int, list[str]] = {}
    for i, line in enumerate(unmasked):
        if line.strip() and tree.lines[tree.body(i)].key in _BODY_KEYS:
            spans.setdefault(tree.job(i).start, []).append(line)
    return list(spans.values())


def scan_docs_kept_true(repo_root: Path) -> DocsKeptTrueBlock:
    """Build the ``doc_staleness.kept_true`` block (module docstring)."""
    repo = repo_root.resolve()
    tracked = repo_files(repo)
    files = ci_config_files(tracked)
    reader = _Reader(repo, tracked)
    seen: dict[tuple[str, KeptKind], list[str]] = {}
    for rel in files:
        text = read_config(repo, rel)
        if rel not in CI_CONFIG_FILES:
            text = follow_workflow(text, reader.read_local).text
        for lines in _job_lines(text):
            for kind, covers in reader.job(lines):
                merged = seen.setdefault((rel, kind), [])
                merged.extend(c for c in covers if c not in merged)
    checks = [KeptCheck(path=rel, kind=kind, covers=covers) for (rel, kind), covers in seen.items()]
    count = {kind: sum(c["kind"] == kind for c in checks) for kind in ("regen_diff", "verify_pair", "doc_tests")}
    block = DocsKeptTrueBlock(files_read=len(files), regen_diff=count["regen_diff"],
                              verify_pair=count["verify_pair"], doc_tests=count["doc_tests"],
                              present=bool(checks), checks=checks)
    if not checks and unread_ci_paths(tracked):
        block["present"] = None
        block["present_reason"] = REASON_CI_NOT_READ
    return block


def kept_true_or_unknown(repo_root: Path) -> DocsKeptTrueBlock:
    """``scan_docs_kept_true``, or a not-measured block (``scan_failed``) when the reader
    raises, so a failure here never drops the doc staleness block it joins."""
    try:
        return scan_docs_kept_true(repo_root)
    except Exception:   # noqa: BLE001 - any reader fault reads not measured, never false
        return DocsKeptTrueBlock(files_read=0, regen_diff=0, verify_pair=0, doc_tests=0,
                                 present=None, present_reason=REASON_SCAN_FAILED, checks=[])


def covering_check(doc: str, kept: DocsKeptTrueBlock) -> KeptTrueBy | None:
    """The first check whose ``covers`` holds ``doc`` (the path or a folder above it)."""
    for check in kept["checks"]:
        if any(doc == c or doc.startswith(c + "/") for c in check["covers"]):
            return KeptTrueBy(kind=check["kind"], path=check["path"])
    return None


def apply_kept_true(block: DocStalenessBlock, kept: DocsKeptTrueBlock) -> DocStalenessBlock:
    """``block`` with ``kept_true`` set, each covered doc row marked ``kept_true_by`` and the
    summary recomputed over the marked rows (module docstring)."""
    block["kept_true"] = kept
    marked = False
    for row in block["docs"]:
        by = covering_check(row["path"], kept)
        if by is not None:
            row["kept_true_by"] = by
            marked = True
    if marked and "summary" in block:
        block["summary"] = staleness_summary(
            block["docs"], reliable=not (block["history_truncated"] or block["churn_degenerate"]))
    return block
