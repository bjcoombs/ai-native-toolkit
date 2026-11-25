"""GitLab CI includes resolved offline (issue #766), and the configuration GitLab merges (#1180).

``ci_checks`` (#698) and ``review_automation`` (#696) read a repository's own
``.gitlab-ci.yml``. Where the jobs come from shared templates, this module reads the
top-level ``include:`` and, where the included file is on disk, returns its text:
``local:`` from the checkout, ``project:`` and ``component:`` from the clone
``<ghsync_root>/<path>/<last>-main`` at ``ref:`` (a pin the clone does not hold is
``ref_missing``), ``template:`` / ``remote:`` are ``not_offline``. The toolkit's own regression
gate (#1152) is ``toolkit_gate`` and counts as read: its exact ``remote:`` URL, or a ``component:``
whose file, read from the clone, runs what ``GATE_TEMPLATE`` runs; a lookalike name alone is read
like any component (#1179). Nested includes are walked to
``MAX_DEPTH`` (5) and ``MAX_FILES`` (50). Each include item gets one ``IncludeRecord`` (kind,
id, status, ref_basis, depth), published by ``ci_checks`` as ``includes``; template text is
never published. ``resolve_includes(repo_root, ghsync_root)`` returns a ``Resolution``:
``files`` (``IncludedFile(text, depth, line)``), ``includes``, ``root`` (the repository's own
file text) and ``slots``, GitLab's merge order (#1180).

**Renamed projects** (#1357): ``resolve_includes(..., renames)`` takes the ``IncludeRenames``
the ``--platform`` lookup made (``lib/hosting/gitlab_include_renames.py``). A project with no
clone at its written path that the API answered for under a new path reads from the clone at
that new path, never a checkout at the old one. Each ``project`` / ``component`` record with no
clone at its written path carries ``rename``: ``followed`` (read through the new path),
``checked`` (looked up, no clone at a new path) or ``not_checked`` (offline, or not answered);
every other record has ``rename`` null.

**The merged configuration** (#1180): ``merged_config(repo_root, ghsync_root)`` applies
GitLab's precedence across the root file and every included file. Included files merge in
order, a nested include before the file that includes it, and the root file last, so a later
definition of a job key wins; two maps deep-merge and anything else is replaced. Then
``extends:`` (parents in order, the job's own keys last) and ``default:`` (a key the job does
not set, unless ``inherit: default:`` says not). Each ``MergedJob`` holds its ``defs`` and,
per key, the contributing ``KeyDef`` chain (lowest precedence first). Anything it cannot
settle is ``unresolved`` with a reason code, never read optimistically: an include under
``rules:`` (``conditional_include``), an anchor merge, alias, ``$[[ ]]`` interpolation or
duplicate key (``unreadable``), an ``extends:`` parent not found (``extends_missing``), a cycle
or more than 11 levels, an unreadable ``inherit:``. ``open`` says an include this run could
not read may also define the job. Line readers use ``lib.ci.ci_merged_view``.

Deterministic for fixed checkouts and renames; read only (``GIT_OPTIONAL_LOCKS=0``); no network.
Full reference: lib/docs/ci_includes.md. Tests: ``tests/test_ci_includes.py``,
``tests/test_ci_merged_config.py``, ``tests/test_ci_include_renames.py``.
"""
from __future__ import annotations

import functools
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, TypedDict

from lib.instructions.command_index import MAX_CONFIG_BYTES
from lib.git_churn import tracked_files
from lib.ci.ci_include_items import (
    KINDS as KINDS, IncludeItem as IncludeItem, IncludeKind as IncludeKind, _split_flow, _unquote,
    include_items as include_items)
from lib.ci.shared_tooling import component_source, pin

GITLAB_CI = ".gitlab-ci.yml"
MAX_DEPTH = 5
MAX_FILES = 50

RefBasis = Literal["ref", "head", "worktree"]
# A project include with no clone at its written path (#1357): read through the path the API
# redirected it to, looked up with no clone found there, or not looked up (offline).
RenameBasis = Literal["followed", "checked", "not_checked"]
RENAME_BASES: tuple[RenameBasis, ...] = ("followed", "checked", "not_checked")
IncludeStatus = Literal[
    "resolved", "toolkit_gate", "no_root", "not_cloned", "file_missing", "not_offline", "glob", "bad_path",
    "depth_limit", "file_limit", "unparsed", "ref_missing",
]
STATUSES: tuple[IncludeStatus, ...] = (
    "resolved", "toolkit_gate", "no_root", "not_cloned", "file_missing", "not_offline", "glob", "bad_path",
    "depth_limit", "file_limit", "unparsed", "ref_missing",
)


class IncludeRecord(TypedDict):
    """One include item of the repository's CI or of a file it includes."""

    kind: IncludeKind
    id: str | None  # gitlab_project:<path> / gitlab_component:<path>; None for the other kinds
    status: IncludeStatus
    ref_basis: RefBasis | None  # how the file was read; None when not resolved
    depth: int  # 1: the repository's own .gitlab-ci.yml names it
    rename: RenameBasis | None  # #1357: set only on a project or component with no clone at its path


@dataclass(frozen=True)
class IncludeRenames:
    """The ``--platform`` project lookup (#1357): ``checked`` holds every project path the API
    answered for (lower-cased), and ``moved`` each one it answered under another path, as
    ``(old, new)`` pairs. Hashable, so ``resolve_includes`` stays cached."""

    checked: frozenset[str] = frozenset()
    moved: tuple[tuple[str, str], ...] = ()

    def basis(self, project: str) -> RenameBasis:
        return "checked" if project.lower() in self.checked else "not_checked"

    def target(self, project: str) -> str | None:
        return next((new for old, new in self.moved if old == project.lower()), None)


@dataclass(frozen=True)
class IncludedFile:
    """A file read through an include: its text for the scans, never published."""

    text: str
    depth: int
    line: int  # the line of the repository's own include that led here


@dataclass(frozen=True)
class Slot:
    """One place in GitLab's merge order (#1180): an included file read (``file`` indexes
    ``Resolution.files``), or an include this run could not read (``file`` None) that may define
    the jobs ``jobs`` names (None: any job). ``conditional``: under an include with ``rules:``.
    ``include``: an unread include item as written, ``<kind>: <source>``, so a reader can tell
    which include it is (#1204); None for a read file, the gate and a repeated file."""

    file: int | None
    conditional: bool = False
    jobs: frozenset[str] | None = None
    include: str | None = None


@dataclass(frozen=True)
class Resolution:
    files: tuple[IncludedFile, ...]
    includes: tuple[IncludeRecord, ...]
    root: str | None = None  # the repository's own .gitlab-ci.yml text, never published
    slots: tuple[Slot, ...] = ()  # merge order, lowest precedence first; the root file follows

    @property
    def unresolved(self) -> int:
        return sum(r["status"] not in SETTLED for r in self.includes)


# The statuses that leave no job unread: a file read, or the toolkit's gate template (#1152).
SETTLED: frozenset[str] = frozenset({"resolved", "toolkit_gate"})
# The toolkit's own regression gate template (#1152), matched exactly; no other remote.
_TOOLKIT_GATE_REMOTE = re.compile(
    r"^https://raw\.githubusercontent\.com/bjcoombs/ai-native-toolkit/[^/\s]+"
    r"/templates/assess-gate\.yml$")
# The jobs that template defines (templates/assess-gate.yml): a same-named job is not settled (#1180).
TOOLKIT_GATE_JOBS = frozenset({"assess-gate"})
# That template as this toolkit ships it, the reference a component's file is compared with (#1179).
# A copy outside the repository layout (a standalone build) has none, and recognises no component.
_UP = Path(__file__).resolve().parents
GATE_TEMPLATE = (_UP[5] if len(_UP) > 5 else _UP[0]) / "templates" / "assess-gate.yml"


def is_toolkit_gate(kind: str, source: str) -> bool:
    """True for an include that names the toolkit's own gate template by its one known source,
    the ``remote:`` raw URL of this repository (#1152). A ``component:`` is never the gate by its
    name: any host or group can hold an ``ai-native-toolkit/assess-gate`` (#1179). It is the gate
    only when the file read from its clone is the template (``is_toolkit_gate_text``)."""
    source = source.strip().strip("\"'")
    return kind == "remote" and bool(_TOOLKIT_GATE_REMOTE.match(source)) and ".." not in source


def _gate_body(text: str) -> tuple[str, ...] | None:
    """The lines a gate template runs: those after its ``spec:`` header's ``---``, blank and
    comment lines dropped. None when the text does not open with a ``spec:`` header alone."""
    lines = text.splitlines()
    cut = next((i for i, line in enumerate(lines) if line.rstrip() == "---"), None)
    if cut is None:
        return None
    tops = [line.split(":", 1)[0] for line in lines[:cut] if _content(line) and line[:1] not in " \t"]
    if tops != ["spec"]:
        return None
    return tuple(line.rstrip() for line in lines[cut + 1:] if _content(line))


@functools.lru_cache(maxsize=1)
def _shipped_gate() -> tuple[str, ...] | None:
    try:
        return _gate_body(GATE_TEMPLATE.read_text(encoding="utf-8"))
    except OSError:
        return None  # a build without the template: no component is recognised as the gate


def is_toolkit_gate_text(text: str) -> bool:
    """True when ``text`` (a component's template file) runs exactly what the toolkit's own
    gate template runs: the lines after the ``spec:`` header match ``GATE_TEMPLATE``. Input
    defaults may differ (a mirror pins its own version); a job, key or command that differs
    does not match, and the file is then read like any other (#1179)."""
    shipped = _shipped_gate()
    return shipped is not None and _gate_body(text) == shipped


EMPTY = Resolution((), ())

_SAFE_REF = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_./+~-]*$")
_GLOB = re.compile(r"[*?\[]")
_REGULAR_MODES = frozenset({"100644", "100755"})


# --- reading -----------------------------------------------------------------


def _clean(path: str) -> str | None:
    """A repo-relative POSIX path, or None when it climbs out or is empty."""
    parts = path.strip().strip("/").split("/")
    if not parts or any(p in ("", ".", "..") for p in parts):
        return None
    return "/".join(parts)


def _git(checkout: Path, *args: str) -> str | None:
    env = {**os.environ, "GIT_OPTIONAL_LOCKS": "0"}
    try:
        out = subprocess.run(
            ["git", "-C", str(checkout), *args], capture_output=True, text=True,
            encoding="utf-8", errors="replace", check=True, timeout=30, env=env,
        )
    except (subprocess.CalledProcessError, OSError, subprocess.TimeoutExpired):
        return None
    return out.stdout


def _commit(checkout: Path, ref: str) -> str | None:
    if not _SAFE_REF.match(ref) or ".." in ref:
        return None
    out = _git(checkout, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")
    return out.strip() if out and out.strip() else None


@dataclass(frozen=True)
class _Source:
    """Where an include's files are read: a work tree, or a clone at a commit."""

    checkout: Path
    commit: str | None  # None: the repository's own work tree
    basis: RefBasis
    tracked: frozenset[Path] | None = None  # work tree: only git-tracked files are read

    def read(self, rel: str) -> str | None:
        if self.commit is None:
            path = self.checkout / rel
            try:
                if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_CONFIG_BYTES:
                    return None
                resolved = path.resolve()
                resolved.relative_to(self.checkout)
                if self.tracked is not None and resolved not in self.tracked:
                    return None  # an uncommitted fragment reaches no clone
                return path.read_text(encoding="utf-8", errors="replace")
            except (OSError, ValueError):
                return None
        # A regular file only, as the work tree reads: a directory (a tree listing) or a
        # committed symlink (its target path) is no CI file.
        entry = _git(self.checkout, "--literal-pathspecs", "ls-tree", "-z", "--long",
                     self.commit, "--", rel)
        meta, _, name = (entry or "").rstrip("\0").partition("\t")
        fields = meta.split()
        if (name != rel or len(fields) != 4 or fields[0] not in _REGULAR_MODES
                or fields[1] != "blob" or not fields[3].isdigit()
                or int(fields[3]) > MAX_CONFIG_BYTES):
            return None
        return _git(self.checkout, "cat-file", "blob", fields[2])


def _entry(parent: Path, name: str) -> Path | None:
    """``parent/name`` under its on-disk name: an exact match, else the one entry equal to
    ``name`` ignoring case (GitLab paths are case-insensitive, #859). None when there is no
    entry, or more than one differing only in case."""
    try:
        names = os.listdir(parent)
    except OSError:
        return None
    if name in names:
        return parent / name
    folded = [n for n in names if n.casefold() == name.casefold()]
    return parent / folded[0] if len(folded) == 1 else None


def _clone(root: Path, project: str) -> Path | None:
    clone: Path | None = root
    for name in (*project.split("/"), f"{project.rsplit('/', 1)[-1]}-main"):
        clone = _entry(clone, name) if clone is not None else None
    try:
        if clone is None or not clone.is_dir():
            return None
        resolved = clone.resolve()
        resolved.relative_to(root)  # a symlinked clone pointing outside the root is not read
    except (OSError, ValueError):
        return None
    return resolved


def _project_source(root: Path, project: str, ref: str | None,
                    renames: IncludeRenames | None = None
                    ) -> tuple[_Source | IncludeStatus, RenameBasis | None]:
    """The clone and commit an item reads, or why it cannot: ``not_cloned``, or
    ``ref_missing`` for a named ref the clone does not hold or that is no plain ref name. With
    no clone at ``project``, a clone at the path the API moved it to (#1357), and the rename
    basis; None when the written path is cloned."""
    clone, rename = _clone(root, project), None
    if clone is None:
        rename = renames.basis(project) if renames is not None else "not_checked"
        moved = renames.target(project) if renames is not None else None
        clone = _clone(root, moved) if moved is not None else None
        if clone is None:
            return "not_cloned", rename
        rename = "followed"
    return _read_at(clone, ref), rename


def _read_at(clone: Path, ref: str | None) -> _Source | IncludeStatus:
    if ref is not None and ref.strip():
        pinned = pin(ref)  # None for a CI variable: a version the file does not say
        for candidate in ((pinned, f"refs/remotes/origin/{pinned}") if pinned else ()):
            if (commit := _commit(clone, candidate)) is not None:
                return _Source(clone, commit, "ref")
        return "ref_missing"
    head = _commit(clone, "HEAD")
    return _Source(clone, head, "head") if head is not None else "not_cloned"


def _written(item: IncludeItem) -> str:
    """An include item as written, ``<kind>: <source>`` (``Slot.include``)."""
    return f"{item.kind}: {item.source}"


class _Walk:
    def __init__(self, repo_root: Path, root: Path | None,
                 renames: IncludeRenames | None = None) -> None:
        self.repo_root = repo_root
        self.root = root
        self.renames = renames
        self.rename: RenameBasis | None = None  # of the project item being recorded (#1357)
        self.files: list[IncludedFile] = []
        self.records: list[IncludeRecord] = []
        self.seen: set[tuple[str, str | None, str]] = set()
        self.slots: list[Slot] = []
        self.texts: dict[tuple[str, str | None, str], str] = {}
        self.conditional = False  # the item being read sits under an include with rules:

    def record(self, item: IncludeItem, ident: str | None, status: IncludeStatus,
               basis: RefBasis | None, depth: int) -> None:
        self.records.append({"kind": item.kind, "id": ident, "status": status,
                             "ref_basis": basis if status == "resolved" else None, "depth": depth,
                             "rename": self.rename})
        if status not in SETTLED:   # an include not read may define any job (#1180)
            self.slots.append(Slot(None, self.conditional, include=_written(item)))

    def gate(self, item: IncludeItem, ident: str | None, depth: int, conditional: bool) -> None:
        """The toolkit's gate template: settled, except for the jobs it defines (#1152, #1180)."""
        self.record(item, ident, "toolkit_gate", None, depth)
        self.slots.append(Slot(None, conditional, TOOLKIT_GATE_JOBS))

    def read_all(self, source: _Source, paths: list[str], depth: int,
                 line: int) -> IncludeStatus:
        """Read every file of one item: ``resolved``, or the reason it could not."""
        texts: list[str] = []
        keys: list[tuple[str, str | None, str]] = []
        for rel in paths:
            key = (str(source.checkout), source.commit, rel)
            if key in self.seen or key in keys:
                # Read once; where GitLab places a repeated file is not settled here (#1180).
                again = self.texts.get(key)
                self.slots.append(Slot(None, self.conditional, frozenset(
                    name for name, _ in _entries(again, 0)[0]) if again is not None else None))
                continue
            if len(self.seen) + len(keys) >= MAX_FILES:
                return "file_limit"
            text = source.read(rel)
            if text is None:
                return "file_missing"  # nothing of this item is marked read: a later item reads it afresh
            keys.append(key)
            texts.append(text)
        self.seen.update(keys)  # only once the whole item has read
        self.texts.update(zip(keys, texts, strict=True))
        for text in texts:
            index = len(self.files)
            self.files.append(IncludedFile(text, depth, line))
            self.walk(text, source, depth + 1, line)
            self.slots.append(Slot(index, self.conditional))  # after its own includes
        return "resolved"

    def walk(self, text: str, here: _Source, depth: int, top_line: int | None) -> None:
        items, _has_section, dropped = include_items(text)
        for _ in range(dropped):
            self.records.append({"kind": "unknown", "id": None, "status": "unparsed",
                                 "ref_basis": None, "depth": depth, "rename": None})
        for item in items:
            self.rename = None  # before the gate: a remote gate never carries a project's rename
            if is_toolkit_gate(item.kind, item.source) and depth <= MAX_DEPTH:
                self.gate(item, None, depth, self.conditional or item.conditional)
                continue
            outer, self.conditional = self.conditional, self.conditional or item.conditional
            self.one(item, here, depth, item.line if top_line is None else top_line)
            self.conditional = outer

    def one(self, item: IncludeItem, here: _Source, depth: int, line: int) -> None:
        ident: str | None = None
        if item.kind == "project":
            ident = f"gitlab_project:{item.source.strip('/').lower()}"
        elif item.kind == "component":
            ident = f"gitlab_component:{component_source(item.source).lower()}"
        if depth > MAX_DEPTH:
            self.record(item, ident, "depth_limit", None, depth)
            return
        if item.kind in ("template", "remote"):
            self.record(item, ident, "not_offline", None, depth)
            return
        if item.kind == "local":
            if _GLOB.search(item.source):
                self.record(item, ident, "glob", None, depth)
                return
            rel = _clean(item.source)
            if rel is None:
                self.record(item, ident, "bad_path", None, depth)
                return
            self.finish(item, ident, here, [rel], depth, line)
            return
        if self.root is None:
            self.record(item, ident, "no_root", None, depth)
            return
        if item.kind == "project":
            project, ref = _clean(item.source), item.ref
            names = [_clean(f) for f in item.files]
            paths = [n for n in names if n is not None]
            if project is None or not item.files or len(paths) != len(names):
                self.record(item, ident, "bad_path", None, depth)
                return
        else:
            full = component_source(item.source)
            project = _clean(full.rsplit("/", 1)[0]) if "/" in full else None
            name = full.rsplit("/", 1)[-1]
            ref = item.source.partition("@")[2] or None
            if project is None or _clean(name) is None:
                self.record(item, ident, "bad_path", None, depth)
                return
            paths = []
        source, self.rename = _project_source(self.root, project, ref, self.renames)
        if isinstance(source, str):
            self.record(item, ident, source, None, depth)
            return
        if item.kind == "component":
            found = self.component(item, ident, source, name, depth)
            if found is None:
                return
            paths = [found]
        self.finish(item, ident, source, paths, depth, line)

    def component(self, item: IncludeItem, ident: str | None, source: _Source, name: str,
                  depth: int) -> str | None:
        """The file a component reads, or None once it is recorded: ``file_missing``, or
        ``toolkit_gate`` for a mirror of the gate, its file read from the clone (#1179)."""
        found = next(((p, text) for p in (f"templates/{name}.yml", f"templates/{name}/template.yml")
                      if (text := source.read(p)) is not None), None)
        if found is None:
            self.record(item, ident, "file_missing", None, depth)
            return None
        if is_toolkit_gate_text(found[1]):
            self.gate(item, ident, depth, self.conditional)
            return None
        return found[0]

    def finish(self, item: IncludeItem, ident: str | None, source: _Source, paths: list[str],
               depth: int, line: int) -> None:
        if len(self.seen) >= MAX_FILES:
            self.record(item, ident, "file_limit", None, depth)
            return
        at = len(self.records)
        self.record(item, ident, "resolved", source.basis, depth)  # before its nested includes
        status = self.read_all(source, paths, depth, line)
        if status != "resolved":
            self.records[at] = {**self.records[at], "status": status, "ref_basis": None}
            self.slots.append(Slot(None, self.conditional, include=_written(item)))


@functools.lru_cache(maxsize=8)
def resolve_includes(repo_root: Path, ghsync_root: Path | None = None,
                     renames: IncludeRenames | None = None) -> Resolution:
    """Every include of ``repo_root``'s ``.gitlab-ci.yml``, resolved where the file is on
    disk (see the module docstring); ``renames`` maps a moved project to its clone (#1357).
    Cached: ``ci_checks`` and ``review_automation`` share it."""
    repo_root = repo_root.resolve()
    own = _Source(repo_root, None, "worktree", tracked_files(repo_root))
    text = own.read(GITLAB_CI)
    if text is None:
        return EMPTY
    walk = _Walk(repo_root, ghsync_root.resolve() if ghsync_root is not None else None, renames)
    walk.seen.add((str(repo_root), None, GITLAB_CI))
    walk.walk(text, own, 1, None)
    return Resolution(tuple(walk.files), tuple(walk.records), text, tuple(walk.slots))


# --- the merged configuration (#1180) ------------------------------------------

# Top-level keys that are not jobs: GitLab's global keywords and a component's spec: header.
GLOBAL_KEYS = frozenset({"default", "include", "stages", "variables", "workflow", "image",
                         "services", "cache", "before_script", "after_script", "spec"})
# The keywords a job takes from default: when it does not set them (GitLab's default: docs).
DEFAULT_KEYS = frozenset({"after_script", "artifacts", "before_script", "cache", "hooks",
                          "id_tokens", "image", "interruptible", "retry", "services", "tags",
                          "timeout"})
# Deprecated top-level forms of default: keys.
_LEGACY_DEFAULTS = frozenset({"image", "services", "cache", "before_script", "after_script"})
MAX_EXTENDS = 11  # GitLab's nesting limit for extends:
_TOP = re.compile(r"""^(?P<key>"[^"]*"|'[^']*'|[^\s#"'\-?&*!|>%@`{\[][^#]*?)\s*:(?:\s+(?P<value>.*))?$""")
_SUB = re.compile(r"""^(?P<key>"[^"]*"|'[^']*'|[\w.-]+)\s*:(?:\s+(?P<value>.*))?$""")
_ANCHOR = re.compile(r"&[\w.-]+")
# An anchor merge (<<: *a) or an alias value (key: *a, - *a): content defined elsewhere.
_ALIAS = re.compile(r"^\s*(?:-\s+)?(?:<<\s*:|(?:[\w.-]+\s*:\s+)?\*[\w.-]+\s*(?:#.*)?$)")
Unresolved = Literal["conditional_include", "include_order", "unreadable", "extends_missing",
                     "extends_cycle", "extends_depth", "inherit", "default_unresolved"]


def _bare(value: str | None) -> str:
    """A key's inline value with its comment dropped (``key: x # note`` -> ``x``)."""
    return re.sub(r"(?:^|\s+)#.*$", "", value or "").strip()


@dataclass(frozen=True)
class KeyDef:
    """One definition of a key: lines ``start`` (the key line) to ``end`` (exclusive) of
    ``MergedConfig.sources[source]``; ``col`` is the key's column."""

    source: int
    start: int
    end: int
    col: int


@dataclass(frozen=True)
class JobDef:
    """One top-level entry of one file: its lines and its keys, or ``unreadable``."""

    source: int
    start: int
    end: int
    keys: tuple[tuple[str, KeyDef], ...]
    unreadable: bool


def _content(line: str) -> bool:
    stripped = line.strip()
    return bool(stripped) and not stripped.startswith("#")


def _job_def(lines: list[str], source: int, start: int, end: int, value: str) -> JobDef:
    body = lines[start:end]
    unreadable = (bool(value) and _ANCHOR.fullmatch(value) is None) or any(
        _ALIAS.match(line) or "$[[" in line for line in body)
    keys: list[tuple[str, KeyDef]] = []
    col: int | None = None
    open_block = False  # the last key has no inline value, so a sequence may follow at its indent
    for j in range(start + 1, end):
        raw = lines[j]
        if not _content(raw):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        col = indent if col is None else col
        if indent > col or (indent == col and raw.strip()[:2] in ("- ", "-") and open_block):
            continue   # a key's value; a block sequence may sit at the key's own indent
        match = _SUB.match(raw.strip())
        if indent < col or match is None:
            unreadable = True
            continue
        if keys:
            name, last = keys[-1]
            keys[-1] = (name, KeyDef(source, last.start, j, col))
        keys.append((_unquote(match.group("key")), KeyDef(source, j, end, col)))
        open_block = not _bare(match.group("value")) or _ANCHOR.fullmatch(_bare(match.group("value"))) is not None
    if len({name for name, _ in keys}) < len(keys):
        unreadable = True  # a duplicate key: which one YAML keeps is not read here
    return JobDef(source, start, end, tuple(keys), unreadable)


def _entries(text: str, source: int) -> tuple[list[tuple[str, JobDef]], bool]:
    """The top-level entries of one GitLab CI file, and whether one could not be named (a key
    this reader cannot parse, or an interpolated ``$[[ ]]`` name, which may name any job)."""
    lines = text.splitlines()
    tops = [i for i, line in enumerate(lines) if line[:1] not in ("", " ", "\t", "#")]
    out: list[tuple[str, JobDef]] = []
    wild = False
    for n, i in enumerate(tops):
        end = tops[n + 1] if n + 1 < len(tops) else len(lines)
        if lines[i].rstrip() in ("---", "..."):
            continue
        match = _TOP.match(lines[i].rstrip())
        if match is None or "$[[" in lines[i]:
            wild = True
            continue
        out.append((_unquote(match.group("key")),
                    _job_def(lines, source, i, end, _bare(match.group("value")))))
    return out, wild


@dataclass(frozen=True)
class MergedJob:
    """One job (or ``default:`` / ``workflow:``) after GitLab's merge: ``keys`` maps each key to
    its definitions, lowest precedence first (the last wins; consecutive maps deep-merge)."""

    name: str
    defs: tuple[JobDef, ...]
    keys: dict[str, tuple[KeyDef, ...]]
    unresolved: Unresolved | None  # set: read as not measured
    open: bool  # an include this run could not read may also define this job
    extended: tuple[str, ...] = ()  # the jobs extends: reaches, nearest first
    inherited: frozenset[str] = frozenset()  # keys taken from default:

    @property
    def hidden(self) -> bool:
        return self.name.startswith(".")


@dataclass(frozen=True)
class MergedConfig:
    """``sources``: the root file (0) then ``Resolution.files``. Never published."""

    sources: tuple[str, ...]
    jobs: dict[str, MergedJob]  # every job, hidden templates included
    default: MergedJob | None
    workflow: MergedJob | None
    open: bool


def _chain(defs: list[JobDef]) -> dict[str, list[KeyDef]]:
    out: dict[str, list[KeyDef]] = {}
    for d in defs:
        for name, key in d.keys:
            out.setdefault(name, []).append(key)
    return out


def _names(lines: list[str], key: KeyDef) -> list[str] | None:
    """The names an ``extends:`` value lists (a scalar, a flow list or a block list), or None."""
    value = _bare(lines[key.start].split(":", 1)[1])
    if value:
        names = [_unquote(v) for v in (_split_flow(value) if value.startswith("[") else [value])]
    else:
        items = [line.strip() for line in lines[key.start + 1:key.end] if _content(line)]
        if not items or not all(i.startswith("- ") for i in items):
            return None
        names = [_unquote(_bare(i[2:])) for i in items]
    return names if names and all(n and n[0] not in "&*!{[|>" for n in names) else None


def _inherit(lines: list[str], key: KeyDef) -> frozenset[str] | None:
    """The default: keys ``inherit:`` lets a job take, or None when not read."""
    if _bare(lines[key.start].split(":", 1)[1]):
        return None
    rows = [line for line in lines[key.start + 1:key.end] if _content(line)]
    col = len(rows[0]) - len(rows[0].lstrip(" ")) if rows else 0
    own = [i for i, r in enumerate(rows) if len(r) - len(r.lstrip(" ")) == col
           and not r.strip().startswith("- ")]
    for n, i in enumerate(own):
        match = _SUB.match(rows[i].strip())
        if match is None or _unquote(match.group("key")) != "default":
            continue
        value = _bare(match.group("value"))
        if value in ("true", "false"):
            return DEFAULT_KEYS if value == "true" else frozenset()
        if value.startswith("["):
            return frozenset(_unquote(v) for v in _split_flow(value))
        items = rows[i + 1:own[n + 1] if n + 1 < len(own) else len(rows)]
        if value or not items or not all(r.strip().startswith("- ") for r in items):
            return None
        return frozenset(_unquote(_bare(r.strip()[2:])) for r in items)
    return DEFAULT_KEYS


class _Merge:
    def __init__(self, resolution: Resolution) -> None:
        self.sources = (resolution.root or "", *(f.text for f in resolution.files))
        self.lines = [text.splitlines() for text in self.sources]
        self.defs: dict[str, list[JobDef]] = {}
        self.unsure: dict[str, Unresolved] = {}
        self.open = False
        for slot in (*resolution.slots, None):   # None: the root file, merged last
            if slot is not None and slot.file is None:
                self.open = self.open or slot.jobs is None
                for name in slot.jobs or ():
                    self.unsure.setdefault(name, "include_order")
                continue
            source = 0 if slot is None or slot.file is None else slot.file + 1
            entries, wild = _entries(self.sources[source], source)
            self.open = self.open or wild
            for name, job in entries:
                self.defs.setdefault(name, []).append(job)
                if slot is not None and slot.conditional:
                    self.unsure.setdefault(name, "conditional_include")
        self.done: dict[str, MergedJob] = {}

    def base(self, name: str) -> MergedJob:
        defs = self.defs.get(name, [])
        reason = self.unsure.get(name) or ("unreadable" if any(d.unreadable for d in defs) else None)
        keys = {k: tuple(v) for k, v in _chain(defs).items()}
        return MergedJob(name, tuple(defs), keys, reason, self.open)

    def job(self, name: str, trail: tuple[str, ...] = ()) -> MergedJob:
        if name in self.done:
            return self.done[name]
        own = self.base(name)
        keys: dict[str, list[KeyDef]] = {}
        reason: Unresolved | None = own.unresolved
        extended: tuple[str, ...] = ()
        if "extends" in own.keys:
            last = own.keys["extends"][-1]
            parents = _names(self.lines[last.source], last)
            if parents is None:
                reason = reason or "unreadable"
            for parent in parents or ():
                if parent in trail or parent == name:
                    reason = reason or "extends_cycle"
                elif len(trail) >= MAX_EXTENDS:
                    reason = reason or "extends_depth"
                elif parent not in self.defs:
                    reason = reason or "extends_missing"
                else:
                    up = self.job(parent, (*trail, name))
                    reason = reason or up.unresolved
                    extended = (*extended, parent, *up.extended)
                    for key, chain in up.keys.items():
                        keys.setdefault(key, []).extend(chain)
        for key, chain in own.keys.items():
            if key != "extends":
                keys.setdefault(key, []).extend(chain)
        merged = MergedJob(name, own.defs, {k: tuple(v) for k, v in keys.items()}, reason,
                           own.open, extended)
        if not trail:
            self.done[name] = merged
        return merged

    def defaults(self, job: MergedJob, default: MergedJob | None) -> MergedJob:
        """``job`` with the ``default:`` keys it does not set (``inherit: default:`` read)."""
        if default is None or job.hidden:
            return job
        allowed: frozenset[str] | None = DEFAULT_KEYS
        if "inherit" in job.keys:
            chain = job.keys["inherit"]
            allowed = _inherit(self.lines[chain[-1].source], chain[-1]) if len(chain) == 1 else None
        keys, reason, taken = dict(job.keys), job.unresolved, set()
        for key, chain in default.keys.items():
            if key in DEFAULT_KEYS and key not in keys and (allowed is None or key in allowed):
                keys[key] = chain
                taken.add(key)
                reason = reason or ("inherit" if allowed is None else default.unresolved)
        return MergedJob(job.name, job.defs, keys, reason, job.open, job.extended, frozenset(taken))

    def default(self) -> MergedJob | None:
        """``default:`` merged, with the deprecated top-level forms of its keys."""
        legacy = {name: self.defs[name] for name in _LEGACY_DEFAULTS if name in self.defs}
        if "default" not in self.defs and not legacy:
            return None
        base = self.base("default")
        keys, reason = dict(base.keys), base.unresolved
        for name, defs in legacy.items():
            if name in keys:
                reason = reason or "default_unresolved"   # both forms: which wins is not read
            keys[name] = tuple(KeyDef(d.source, d.start, d.end, 0) for d in defs)
            reason = reason or self.unsure.get(name)
        return MergedJob("default", base.defs + tuple(d for v in legacy.values() for d in v), keys,
                         reason, self.open)


@functools.lru_cache(maxsize=8)
def merged_config(repo_root: Path, ghsync_root: Path | None = None,
                  renames: IncludeRenames | None = None) -> MergedConfig | None:
    """GitLab's merged configuration for ``repo_root`` (#1180; module docstring), or None
    without a readable ``.gitlab-ci.yml``. Built from ``resolve_includes``."""
    resolution = resolve_includes(repo_root, ghsync_root, renames)
    if resolution.root is None:
        return None
    merge = _Merge(resolution)
    default = merge.default()
    jobs = {name: merge.defaults(merge.job(name), default)
            for name in merge.defs if name not in GLOBAL_KEYS}
    workflow = merge.base("workflow") if "workflow" in merge.defs else None
    return MergedConfig(merge.sources, jobs, default, workflow, merge.open)
