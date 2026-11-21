"""GitLab CI includes resolved offline, from the ghsync root (issue #766).

``ci_checks`` (#698) and ``review_automation`` (#696) read a repository's own
``.gitlab-ci.yml``. On an estate where the jobs come from shared templates, the
test job or the AI review step lives in another project. This module reads the
top-level ``include:`` of ``.gitlab-ci.yml`` and, where the included file is on
disk, returns its text so those scans read the included jobs too:

- ``local:`` (or a bare path item): a file of the same checkout. In the
  repository itself it is read from the work tree when git tracks it (an
  uncommitted fragment is ``file_missing``), as ``.gitlab-ci.yml`` is; in
  a template it is read at the template's resolved commit. A glob is not
  expanded (``glob``).
- ``project: <path>`` with ``file:`` (a scalar or a list) and ``ref:``: the
  clone ``<root>/<path>/<last segment>-main`` of the ghsync root passed as
  ``--ghsync-root`` (``fleet.py`` passes its ROOT). The file is read with
  ``git show`` at ``ref`` when the clone holds it (a branch, tag or
  ``origin/<ref>``; ``ref_basis: ref``). A pin the clone does not hold, or one
  that is not a plain ref name (a CI variable, an option-like value), is
  ``ref_missing``: reading HEAD instead could credit, or deny, the wrong
  version. No ``ref:`` reads HEAD (``ref_basis: head``), the project's default
  branch in a ghsync clone.
- ``component: <host>/<project path>/<name>@<version>``: the project
  ``<project path>`` resolved as above, file ``templates/<name>.yml`` or
  ``templates/<name>/template.yml``, at ``<version>`` when the clone holds it.
- ``template:`` and ``remote:`` name files offline reading cannot hold
  (``not_offline``).

Included files are read for their own ``include:`` in turn, to ``MAX_DEPTH``
levels (the repository's own include is depth 1) and at most ``MAX_FILES``
files; past either bound an include is ``depth_limit`` / ``file_limit``. A file
already read (a cycle, or the same file included twice) is not read again.
Reads never leave a clone: a project path or file with a ``..`` or empty
segment is ``bad_path``, a ref that could pass as a git option is not tried,
and a clone that resolves outside the root (a symlink) is ``not_cloned``.

Each include item gets one ``IncludeRecord``: ``kind``, ``id`` (the
``shared_tooling`` id: ``gitlab_project:<path>`` for a project,
``gitlab_component:<path>`` for a component; null for local, template and remote), ``status`` (``resolved`` or the token
saying why not), ``ref_basis`` (``ref``, ``head``, ``worktree``; null when not
resolved) and ``depth``. An item is resolved only when every file it names was
read. ``ci_checks`` publishes the records as ``includes`` with the counts
``resolved_includes`` / ``unresolved_includes``; ``review_automation`` reads
the include entries of the resolved files. Template text is never published.

Deterministic for fixed checkouts: ``git show`` of committed content, records in
walk order (file order, each nested include after its parent), no network. Read only (``GIT_OPTIONAL_LOCKS=0``).
"""
from __future__ import annotations

import functools
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, TypedDict

from lib.command_index import MAX_CONFIG_BYTES
from lib.git_churn import tracked_files
from lib.shared_tooling import component_source, include_lines, pin

GITLAB_CI = ".gitlab-ci.yml"
MAX_DEPTH = 5
MAX_FILES = 50

IncludeKind = Literal["local", "project", "component", "template", "remote"]
RefBasis = Literal["ref", "head", "worktree"]
IncludeStatus = Literal[
    "resolved", "no_root", "not_cloned", "file_missing", "not_offline", "glob", "bad_path",
    "depth_limit", "file_limit", "unparsed", "ref_missing",
]
STATUSES: tuple[IncludeStatus, ...] = (
    "resolved", "no_root", "not_cloned", "file_missing", "not_offline", "glob", "bad_path",
    "depth_limit", "file_limit", "unparsed", "ref_missing",
)


class IncludeRecord(TypedDict):
    """One include item of the repository's CI or of a file it includes."""

    kind: IncludeKind
    id: str | None  # gitlab_project:<path> / gitlab_component:<path>; None for local, template, remote
    status: IncludeStatus
    ref_basis: RefBasis | None  # how the file was read; None when not resolved
    depth: int  # 1: the repository's own .gitlab-ci.yml names it


@dataclass(frozen=True)
class IncludeItem:
    """One parsed ``include:`` item."""

    kind: IncludeKind
    source: str  # project or component path, local path, template name or URL
    files: tuple[str, ...]  # project files; empty for the other kinds
    ref: str | None
    line: int


@dataclass(frozen=True)
class IncludedFile:
    """A file read through an include: its text for the scans, never published."""

    text: str
    depth: int
    line: int  # the line of the repository's own include that led here


@dataclass(frozen=True)
class Resolution:
    files: tuple[IncludedFile, ...]
    includes: tuple[IncludeRecord, ...]

    @property
    def unresolved(self) -> int:
        return sum(r["status"] != "resolved" for r in self.includes)


EMPTY = Resolution((), ())

_KEY = re.compile(r"""^["']?(project|component|local|template|remote|file|ref)["']?\s*:(.*)$""")
_SAFE_REF = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_./+~-]*$")
_GLOB = re.compile(r"[*?\[]")


# --- parsing -----------------------------------------------------------------


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1].strip()
    return value


def _split_flow(text: str) -> list[str]:
    """``[a, {b: c, d: [e]}]`` or ``{a: b, c: [d]}`` -> its top-level items, outer brackets dropped."""
    text = text.strip()
    if text[:1] in "[{" and text[-1:] in "]}":
        text = text[1:-1]
    items: list[str] = []
    depth = 0
    quote = ""
    current = ""
    for ch in text:
        if quote:
            quote = "" if ch == quote else quote
        elif ch in "\"'":
            quote = ch
        elif ch in "[{":
            depth += 1
        elif ch in "]}":
            depth -= 1
        elif ch == "," and depth == 0:
            items.append(current.strip())
            current = ""
            continue
        current += ch
    if current.strip():
        items.append(current.strip())
    return items


def _values(raw: str) -> list[str]:
    """A scalar or a flow list as a list of plain strings."""
    raw = raw.strip()
    if raw.startswith("["):
        return [v for v in (_unquote(x) for x in _split_flow(raw)) if v]
    value = _unquote(raw)
    return [value] if value else []


_ITEM_KINDS: tuple[IncludeKind, ...] = ("project", "component", "local", "template", "remote")


def _item(keys: dict[str, list[str]], line: int) -> IncludeItem | None:
    for kind in _ITEM_KINDS:
        if keys.get(kind):
            refs = keys.get("ref") or []
            return IncludeItem(kind, keys[kind][0], tuple(keys.get("file") or ()),
                               refs[0] if refs else None, line)
    return None


def _scalar_item(value: str, line: int) -> IncludeItem | None:
    raw, value = value.strip(), _unquote(value)
    if not value or (raw == value and re.match(r"^[\w.-]+\s*:(?:\s|$)", value)):
        return None  # empty, or an unquoted mapping with a key this reader does not know
    kind: IncludeKind = "remote" if re.match(r"^[a-z]+://", value, re.IGNORECASE) else "local"
    return IncludeItem(kind, value, (), None, line)


def _flow_item(text: str, line: int) -> IncludeItem | None:
    text = text.strip()
    if not text.startswith("{"):
        return _scalar_item(text, line)
    keys: dict[str, list[str]] = {}
    for pair in _split_flow(text):
        match = _KEY.match(pair)
        if match:
            keys[match.group(1)] = _values(match.group(2))
    return _item(keys, line)


def include_items(text: str) -> tuple[list[IncludeItem], bool, int]:
    """The top-level ``include:`` items of a GitLab CI file, whether it has an ``include:``
    section at all, and how many entries named no item this reader parses (an empty
    ``project:``, an unknown key), so neither a section nor an entry it cannot read is lost."""
    rows = include_lines(text)
    if not rows:
        return [], False, 0
    first, head, _inline = rows[0]
    if not head:
        reader = _BlockReader()
        items = reader.items(rows[1:])
        return items, True, reader.dropped + (not items and not reader.dropped)
    entries = _split_flow(head) if head.startswith("[") else [head]
    items = [i for v in entries if (i := _flow_item(v, first)) is not None]
    return items, True, max(len(entries) - len(items), 0) + (not entries)


class _BlockReader:
    """Block-style include items: a list of mappings, scalars or flow items, or one mapping."""

    def __init__(self) -> None:
        self.out: list[IncludeItem] = []
        self.keys: dict[str, list[str]] = {}
        self.line = 0
        self.list_col: int | None = None
        self.key_col: int | None = None
        self.pending_file = False
        self.dropped = 0  # entries that named no parsable item
        self.started = False  # an entry is open (a "- " item, or the single mapping)

    def flush(self) -> None:
        if (done := _item(self.keys, self.line)) is not None:
            self.out.append(done)
        elif self.started:
            self.dropped += 1
        self.keys, self.pending_file, self.started = {}, False, False

    def start_item(self, content: str, col: int, number: int) -> tuple[str, int] | None:
        """A new ``- `` item: its first key's text and column, or None when the item is
        a scalar or flow item read whole."""
        self.flush()
        self.line = number
        rest = content[1:].lstrip()
        self.key_col = col + len(content) - len(rest)
        if _KEY.match(rest):
            self.started = True
            return rest, self.key_col
        whole = _flow_item(rest, number) if rest.startswith("{") else _scalar_item(rest, number)
        if whole is not None:
            self.out.append(whole)
        else:
            self.dropped += 1
        self.key_col = None
        return None

    def items(self, rows: list[tuple[int, str, bool]]) -> list[IncludeItem]:
        for number, line, _ in rows:
            content = line.lstrip()
            col = len(line) - len(content)
            if self.list_col is None and self.key_col is None and content.startswith("-"):
                self.list_col = col
            if content.startswith("-") and col == self.list_col:
                started = self.start_item(content, col, number)
                if started is None:
                    continue
                content, col = started
            elif self.key_col is None and self.list_col is None:
                self.key_col, self.line = col, number  # include: as a single mapping
                self.started = True
            self.key(content, col)
        self.flush()
        return self.out

    def key(self, content: str, col: int) -> None:
        # ">=": an indentless sequence (`- a.yml` at the key column) is valid YAML.
        if self.pending_file and self.key_col is not None and col >= self.key_col \
                and content.startswith("-"):
            self.keys.setdefault("file", []).extend(_values(content[1:]))
            return
        if self.key_col is not None and col <= self.key_col and not content.startswith("-"):
            self.pending_file = False  # any mapping key (rules:, inputs:) ends the file list
        match = _KEY.match(content)
        if match and col == self.key_col:
            name, value = match.group(1), match.group(2).strip()
            self.pending_file = name == "file" and not value
            self.keys[name] = _values(value) if value else []


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
        size = _git(self.checkout, "cat-file", "-s", f"{self.commit}:{rel}")
        if size is None or not size.strip().isdigit() or int(size) > MAX_CONFIG_BYTES:
            return None
        return _git(self.checkout, "show", f"{self.commit}:{rel}")


def _clone(root: Path, project: str) -> Path | None:
    clone = root / project / f"{project.rsplit('/', 1)[-1]}-main"
    try:
        if not clone.is_dir():
            return None
        resolved = clone.resolve()
        resolved.relative_to(root)  # a symlinked clone pointing outside the root is not read
    except (OSError, ValueError):
        return None
    return resolved


def _project_source(root: Path, project: str, ref: str | None) -> _Source | IncludeStatus:
    """The clone and commit an item reads, or why it cannot: ``not_cloned``, or
    ``ref_missing`` for a named ref the clone does not hold or that is no plain ref name."""
    clone = _clone(root, project)
    if clone is None:
        return "not_cloned"
    if ref is not None and ref.strip():
        pinned = pin(ref)  # None for a CI variable: a version the file does not say
        for candidate in ((pinned, f"refs/remotes/origin/{pinned}") if pinned else ()):
            if (commit := _commit(clone, candidate)) is not None:
                return _Source(clone, commit, "ref")
        return "ref_missing"
    head = _commit(clone, "HEAD")
    return _Source(clone, head, "head") if head is not None else "not_cloned"


class _Walk:
    def __init__(self, repo_root: Path, root: Path | None) -> None:
        self.repo_root = repo_root
        self.root = root
        self.files: list[IncludedFile] = []
        self.records: list[IncludeRecord] = []
        self.seen: set[tuple[str, str | None, str]] = set()

    def record(self, item: IncludeItem, ident: str | None, status: IncludeStatus,
               basis: RefBasis | None, depth: int) -> None:
        self.records.append({"kind": item.kind, "id": ident, "status": status,
                             "ref_basis": basis if status == "resolved" else None, "depth": depth})

    def read_all(self, source: _Source, paths: list[str], depth: int,
                 line: int) -> IncludeStatus:
        """Read every file of one item: ``resolved``, or the reason it could not."""
        texts: list[str] = []
        keys: list[tuple[str, str | None, str]] = []
        for rel in paths:
            key = (str(source.checkout), source.commit, rel)
            if key in self.seen or key in keys:
                continue
            if len(self.seen) + len(keys) >= MAX_FILES:
                return "file_limit"
            text = source.read(rel)
            if text is None:
                return "file_missing"  # nothing of this item is marked read: a later item reads it afresh
            keys.append(key)
            texts.append(text)
        self.seen.update(keys)  # only once the whole item has read
        for text in texts:
            self.files.append(IncludedFile(text, depth, line))
            self.walk(text, source, depth + 1, line)
        return "resolved"

    def walk(self, text: str, here: _Source, depth: int, top_line: int | None) -> None:
        items, _has_section, dropped = include_items(text)
        for _ in range(dropped):
            self.records.append({"kind": "local", "id": None, "status": "unparsed",
                                 "ref_basis": None, "depth": depth})
        for item in items:
            self.one(item, here, depth, item.line if top_line is None else top_line)

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
        source = _project_source(self.root, project, ref)
        if isinstance(source, str):
            self.record(item, ident, source, None, depth)
            return
        if item.kind == "component":
            found = next((p for p in (f"templates/{name}.yml", f"templates/{name}/template.yml")
                          if source.read(p) is not None), None)
            if found is None:
                self.record(item, ident, "file_missing", None, depth)
                return
            paths = [found]
        self.finish(item, ident, source, paths, depth, line)

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


@functools.lru_cache(maxsize=8)
def resolve_includes(repo_root: Path, ghsync_root: Path | None = None) -> Resolution:
    """Every include of ``repo_root``'s ``.gitlab-ci.yml``, resolved where the file is on
    disk (see the module docstring). Cached: ``ci_checks`` and ``review_automation`` share it."""
    repo_root = repo_root.resolve()
    own = _Source(repo_root, None, "worktree", tracked_files(repo_root))
    text = own.read(GITLAB_CI)
    if text is None:
        return EMPTY
    walk = _Walk(repo_root, ghsync_root.resolve() if ghsync_root is not None else None)
    walk.seen.add((str(repo_root), None, GITLAB_CI))
    walk.walk(text, own, 1, None)
    return Resolution(tuple(walk.files), tuple(walk.records))
