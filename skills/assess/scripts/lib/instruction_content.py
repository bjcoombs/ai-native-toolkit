"""Repository-checked content signals for the instruction-file grader.

``lib.agent_instructions_grader`` scores an instruction file on what helps an
agent. Three of its signals need the repository, not just the text, and live
here:

- **References that exist.** A backticked path (``src/auth/login.py``,
  ``scripts/``, ``CLAUDE.md``) counts only when it exists at HEAD; a backticked
  code symbol (``grade_instructions``, ``parseConfig()``) only when some other
  tracked file names it. A reference that resolves to nothing is reported as a
  stale reference. Treude and Baltes (2026, https://arxiv.org/html/2606.09090v1)
  found stale code-element references in 23.0% of repositories' instruction
  files and 24% of flagged references false positives on manual review, so a
  stale reference is a finding to read, never a score penalty. False
  positives are kept down by what is not checked: URLs, globs, placeholders
  (``<name>``, ``{id}``, ``$VAR``), absolute and home paths, paths under the
  trees every scan excludes (``lib.doc_graph.is_excluded_path``), gitignored
  paths (generated artefacts such as ``coverage.xml``), a slash path with no
  extension whose first segment is not in the repo (``owner/repo``,
  ``feat/branch``), ALL_CAPS names (environment variables are often external),
  and fenced code. A path also resolves relative to the instruction file's
  directory or as the tail of a tracked path (``lib/foo.py`` for
  ``skills/x/lib/foo.py``).
- **Directory-tree blocks** and **repository-overview sections**: Gloaguen et
  al. (2026, https://arxiv.org/html/2602.11988v1) found repository overviews did
  not help agents while context files raised cost 19-23%.
- **Overlap with the root README**: lines an agent could read in ``README.md``
  are paid for twice when repeated in an always-loaded file.

``build_repo_context`` reads the repository once; pass the context to every
call. Without git, the symbol and gitignore checks are skipped (no evidence
either way) and paths are checked against a filesystem walk.
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal, NamedTuple, TypedDict

from lib.command_index import RepoIndex, build_index
from lib.doc_graph import is_excluded_path

GIT_TIMEOUT_SECONDS = 30
README_NAMES = ("README.md", "README.rst", "README.txt", "README", "readme.md")

# A README line shorter than this is a heading or a list stub ("## Testing"),
# whose repetition says nothing about duplicated content.
README_OVERLAP_MIN_CHARS = 30
# Fewer comparable lines than this and the ratio is noise.
README_OVERLAP_MIN_LINES = 5

_KNOWN_EXTENSIONS = frozenset("""
md mdx rst txt adoc py pyi ts tsx js jsx mjs cjs json jsonc json5 toml yml yaml sh
bash zsh fish ps1 go mod sum rs java kt kts scala rb php cs fs swift c h cc cpp hpp
m mm sql css scss sass less html htm svg xml gradle properties ini cfg conf lock
env mk just tf tfvars hcl proto graphql gql vue svelte astro dart ex exs erl hs ml
clj lua r jl ipynb csv tsv png jpg jpeg gif pdf dockerfile tpl j2 nix bzl
""".split())

_FENCE = re.compile(r"^\s*(`{3,}|~{3,})")
_INLINE = re.compile(r"(?<!`)`([^`\n]+)`(?!`)")
_NOT_A_PATH = re.compile(r"[\s*?\[\]{}<>$|=,;\"'()!^&@%]")
_LOCATION_SUFFIX = re.compile(r"(?:::.*|:\d+(?::\d+)?|#L\d+.*)$")
_EXTENSION = re.compile(r"\.([A-Za-z0-9]{1,10})$")
_DOTFILE = re.compile(r"^\.[A-Za-z][\w.-]*$")
_SYMBOL = re.compile(r"^(_*[A-Za-z][A-Za-z0-9_]{3,})(?:\(\))?$")
_SNAKE = re.compile(r"[a-z0-9]_[a-z0-9]")
_CAMEL = re.compile(r"^_*[a-z][a-z0-9]*[A-Z]")
_PASCAL = re.compile(r"^[A-Z][a-z0-9]+[A-Z]")
_TREE_LINE = re.compile(r"(?:├──|└──|│\s|\|--|`--|\+--|\\--)")
TREE_MIN_LINES = 3
_OVERVIEW_HEADING = re.compile(
    r"^#{1,6}\s+(?:"
    r"(?:project|repository|repo|codebase|code\s?base|directory|folder|file|source)\s+"
    r"(?:overview|structure|layout|organi[sz]ation|tree|map)"
    r"|overview|introduction"
    r"|about(?:\s+this\s+(?:project|repo(?:sitory)?|codebase))?"
    r"|what\s+is\s+this(?:\s+(?:project|repo(?:sitory)?))?"
    r")\s*:?\s*$",
    re.IGNORECASE,
)
_README_MARKUP = re.compile(r"^[\s#>*+\-\d.)|]*")


class Reference(NamedTuple):
    line: int
    text: str
    kind: Literal["path", "symbol"]


class StaleReference(TypedDict):
    """A backticked path or symbol that does not exist at HEAD."""

    kind: Literal["stale_path", "stale_symbol"]
    line: int
    reference: str
    reason: str


@dataclass(frozen=True)
class RepoContext:
    """What the content checks read from a repository, built once per run."""

    root: Path
    index: RepoIndex
    readme_text: str
    git: bool


def _is_git(root: Path) -> bool:
    try:
        subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--is-inside-work-tree"],
            capture_output=True, check=True, timeout=GIT_TIMEOUT_SECONDS,
        )
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        return False
    return True


def build_repo_context(repo_root: Path) -> RepoContext:
    root = repo_root.resolve()
    index = build_index(root)
    readme = ""
    for name in README_NAMES:
        if index.has_file(name):
            try:
                readme = (root / name).read_text(encoding="utf-8", errors="replace")
            except OSError:
                readme = ""
            break
    return RepoContext(root=root, index=index, readme_text=readme, git=_is_git(root))


def _prose_spans(text: str) -> list[tuple[int, str]]:
    """Inline code spans outside fenced blocks, with their 1-based line."""
    spans: list[tuple[int, str]] = []
    in_fence = False
    for lineno, line in enumerate(text.splitlines(), 1):
        if _FENCE.match(line):
            in_fence = not in_fence
            continue
        if not in_fence:
            spans.extend((lineno, m.group(1).strip()) for m in _INLINE.finditer(line))
    return spans


def _path_candidate(span: str) -> str | None:
    """The path a backtick span names, or None when it is not a checkable path."""
    if "://" in span or span.startswith(("/", "~", "-", "..", "www.")):
        return None
    path = _LOCATION_SUFFIX.sub("", span)
    if not path or _NOT_A_PATH.search(path) or is_excluded_path(Path(path)):
        return None
    if "/" in path:
        return path if all(path.rstrip("/").split("/")) else None
    ext = _EXTENSION.search(path)
    if (ext and ext.group(1).lower() in _KNOWN_EXTENSIONS) or _DOTFILE.match(path):
        return path
    return None


def path_references(text: str) -> list[Reference]:
    """Backticked spans in prose that name a file or directory."""
    out: list[Reference] = []
    for lineno, span in _prose_spans(text):
        path = _path_candidate(span)
        if path is not None:
            out.append(Reference(lineno, path, "path"))
    return out


def symbol_references(text: str) -> list[Reference]:
    """Backticked spans in prose that name a code symbol (snake_case, camelCase, PascalCase)."""
    out: list[Reference] = []
    for lineno, span in _prose_spans(text):
        match = _SYMBOL.match(span)
        if not match:
            continue
        name = match.group(1)
        if name.isupper() or not (_SNAKE.search(name) or _CAMEL.match(name) or _PASCAL.match(name)):
            continue
        out.append(Reference(lineno, name, "symbol"))
    return out


def _tail_match(candidates: frozenset[str], ref: str) -> bool:
    suffix = "/" + ref
    return any(c.endswith(suffix) for c in candidates)


def path_exists(index: RepoIndex, ref: str, self_dir: str = "") -> bool:
    """True when ``ref`` names a tracked file or directory (see the module doc)."""
    ref = ref[2:] if ref.startswith("./") else ref
    bare = ref.rstrip("/")
    if index.has_path(bare) or (self_dir and index.has_path(f"{self_dir}/{bare}")):
        return True
    if "/" not in bare:
        return bare in index.basenames or any(PurePosixPath(d).name == bare for d in index.dirs)
    return _tail_match(index.files, bare) or _tail_match(index.dirs, bare)


def _uncheckable_slash_path(index: RepoIndex, ref: str) -> bool:
    """``owner/repo`` or ``feat/x``: no extension and a first segment the repo lacks."""
    bare = ref.rstrip("/")
    if "/" not in bare or ref.endswith("/") or _EXTENSION.search(bare):
        return False
    return not index.has_path(bare.split("/", 1)[0])


def _gitignored(root: Path, paths: list[str]) -> set[str]:
    if not paths:
        return set()
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "check-ignore", "--stdin"],
            input="\n".join(paths), capture_output=True, text=True,
            timeout=GIT_TIMEOUT_SECONDS, check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return set()
    return set(proc.stdout.splitlines())


def _symbols_present(root: Path, names: list[str], self_rel: str) -> set[str] | None:
    """Which ``names`` occur as whole words in a tracked file other than ``self_rel``."""
    if not names:
        return set()
    cmd = ["git", "-C", str(root), "grep", "-I", "-F", "-w", "-o", "-h", "--no-color"]
    for name in names:
        cmd += ["-e", name]
    cmd += ["--", ".", f":(exclude){self_rel}"] if self_rel else []
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=GIT_TIMEOUT_SECONDS, check=False)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if proc.returncode not in (0, 1):
        return None
    return set(proc.stdout.split())


def check_paths(text: str, ctx: RepoContext, self_rel: str) -> tuple[list[Reference], list[StaleReference]]:
    """(paths that exist, stale paths) among the file's backticked path references."""
    parent = str(PurePosixPath(self_rel).parent)
    self_dir = "" if parent == "." else parent
    existing: list[Reference] = []
    missing: list[Reference] = []
    for ref in path_references(text):
        if path_exists(ctx.index, ref.text, self_dir):
            existing.append(ref)
        elif not _uncheckable_slash_path(ctx.index, ref.text):
            missing.append(ref)
    ignored = _gitignored(ctx.root, sorted({r.text.rstrip("/") for r in missing})) if ctx.git else set()
    stale: list[StaleReference] = [
        {"kind": "stale_path", "line": r.line, "reference": r.text,
         "reason": "no tracked file or directory at this path"}
        for r in missing if r.text.rstrip("/") not in ignored
    ]
    return existing, stale


def check_symbols(text: str, ctx: RepoContext, self_rel: str) -> list[StaleReference]:
    """Backticked code symbols that no other tracked file names. Empty without git."""
    refs = symbol_references(text)
    if not ctx.git or not refs:
        return []
    present = _symbols_present(ctx.root, sorted({r.text for r in refs}), self_rel)
    if present is None:
        return []
    return [
        {"kind": "stale_symbol", "line": r.line, "reference": r.text,
         "reason": "no other tracked file names this symbol"}
        for r in refs if r.text not in present
    ]


def count_directory_trees(text: str) -> int:
    """Runs of at least ``TREE_MIN_LINES`` consecutive tree-drawing lines (``├──``, ``|--``)."""
    trees = 0
    run = 0
    for line in text.splitlines():
        if _TREE_LINE.search(line):
            run += 1
            if run == TREE_MIN_LINES:
                trees += 1
        else:
            run = 0
    return trees


def overview_headings(text: str) -> list[str]:
    """Headings that open a repository-overview section ("## Project structure")."""
    return [line.strip() for line in text.splitlines() if _OVERVIEW_HEADING.match(line.strip())]


def _comparable_lines(text: str) -> set[str]:
    out: set[str] = set()
    for line in text.splitlines():
        norm = " ".join(_README_MARKUP.sub("", line).lower().split())
        if len(norm) >= README_OVERLAP_MIN_CHARS:
            out.add(norm)
    return out


def readme_overlap_pct(text: str, readme_text: str) -> int:
    """Percent of the file's substantive lines that also appear in the README."""
    if not readme_text:
        return 0
    lines = _comparable_lines(text)
    if len(lines) < README_OVERLAP_MIN_LINES:
        return 0
    shared = lines & _comparable_lines(readme_text)
    return round(100 * len(shared) / len(lines))
