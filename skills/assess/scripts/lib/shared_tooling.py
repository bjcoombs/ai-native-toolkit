"""Scan a repository for the shared CI sources and agent marketplaces it uses.

Some maturity signals are only visible across a fleet: whether teams pull their
pipelines from one shared CI source, and whether they share one skill or plugin
marketplace or each set up their own (issue #550). This scan reports, per
repository, the file-content half of that evidence so a fleet roll-up can
count distinct sources and marketplaces per group:

- Shared CI sources: ``include: project:`` and ``include: component:`` entries
  in ``.gitlab-ci.yml``, and ``uses: <owner>/<repo>/.github/workflows/...``
  job-level reusable-workflow references in ``.github/workflows/*.yml``. Local includes
  and ``./.github/workflows`` references are not shared, so they are skipped.
- Agent marketplaces: a ``.claude-plugin/marketplace.json`` manifest (the
  repository hosts a marketplace) and each ``extraKnownMarketplaces`` entry in
  ``.claude/settings.json`` (the repository points its sessions at one).

Every entry carries a stable ``id`` so a roll-up dedupes on it. A CI source is
identified by the project, component or repository it comes from (lowercased;
the version and the GitLab host are dropped). A marketplace is identified by
its name, the key Claude Code itself uses to tell marketplaces apart, so a
manifest and a settings entry naming the same marketplace collapse to one.
``distinct_ids`` is that roll-up over several blocks.

Only git-tracked files are credited, as in ``agent_ops``: an uncommitted file
reaches no clone. Untracked entries are listed with ``tracked: false`` and left
out of ``ci_source_ids`` and ``marketplace_ids``. Deterministic and offline:
file reads plus ``git ls-files``. YAML is read line by line (stdlib only); a
file it cannot read yields no entries rather than an error.
"""
from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any, Literal, NotRequired, TypedDict

from lib.git_churn import is_tracked, tracked_files

_GITLAB_CI = ".gitlab-ci.yml"
_WORKFLOWS_DIR = ".github/workflows"
_MARKETPLACE_MANIFEST = ".claude-plugin/marketplace.json"
_SETTINGS = ".claude/settings.json"

CiKind = Literal["gitlab_project", "gitlab_component", "github_reusable_workflow"]
MarketplaceKind = Literal["manifest", "settings"]


class CiInclude(TypedDict):
    """One shared CI source a pipeline file pulls in."""

    kind: CiKind
    source: str
    id: str
    path: str
    line: int
    tracked: bool


class MarketplaceRef(TypedDict):
    """One marketplace the repository hosts (manifest) or names (settings)."""

    kind: MarketplaceKind
    name: str
    source: str
    id: str
    path: str
    tracked: bool


class SharedToolingBlock(TypedDict):
    """``run-context.json`` ``shared_tooling``."""

    available: bool
    ci_includes: list[CiInclude]
    marketplaces: list[MarketplaceRef]
    ci_source_ids: list[str]
    marketplace_ids: list[str]
    reason: NotRequired[str]


class DistinctIds(TypedDict):
    """Distinct shared CI sources and marketplaces across several blocks."""

    ci_sources: list[str]
    marketplaces: list[str]


_TOP_LEVEL_INCLUDE = re.compile(r"""^(?:include|"include"|'include')\s*:(.*)$""")
_TOP_LEVEL_JOBS = re.compile(r"""^(?:jobs|"jobs"|'jobs')\s*:""")
_JOB_USES = re.compile(r"""^(?:uses|"uses"|'uses')\s*:(.*)$""")
_INCLUDE_VALUE = r"""("[^"]*"|'[^']*'|(?:\$\{[^}]*\}|[^\s,}#])+)"""
_INCLUDE_KEY = re.compile(r"(?:^|[\s{,])(project|component)\s*:\s*" + _INCLUDE_VALUE)
_BLOCK_KEY = re.compile(r"^(project|component)\s*:\s*" + _INCLUDE_VALUE)
_REUSABLE_REF = re.compile(
    r"""^([A-Za-z0-9_.\-]+/[A-Za-z0-9_.\-]+)/\.github/workflows/[^\s"'#]+"""
)
_HOST_VARS = ("$CI_SERVER_FQDN", "${CI_SERVER_FQDN}", "$CI_SERVER_HOST", "${CI_SERVER_HOST}")


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1].strip()
    return value


def _strip_comment(line: str) -> str:
    """Drop a trailing ``# comment`` that is not inside quotes."""
    quote = ""
    for i, ch in enumerate(line):
        if quote:
            if ch == quote:
                quote = ""
        elif ch in "\"'":
            quote = ch
        elif ch == "#" and (i == 0 or line[i - 1].isspace()):
            return line[:i]
    return line


def _component_source(raw: str) -> str:
    """``host/group/project/name@1.0`` -> ``group/project/name``."""
    path = raw.split("@", 1)[0].strip("/")
    first, _, rest = path.partition("/")
    if rest and (first in _HOST_VARS or "." in first or ":" in first):
        path = rest
    return path


def _include_lines(text: str) -> list[tuple[int, str, bool]]:
    """The top-level ``include:`` section as (line number, text, inline) rows.

    The section is the ``include:`` line itself (its inline value, ``inline``
    true) and every following line until the next top-level key. ``include:``
    nested under a job's ``trigger:`` is a child pipeline, not a shared source,
    and is indented, so it never starts a section.
    """
    out: list[tuple[int, str, bool]] = []
    inside = False
    for number, raw in enumerate(text.splitlines(), start=1):
        line = _strip_comment(raw).rstrip()
        if not line.strip():
            continue
        top_level = not line[0].isspace() and not line.startswith("-")
        if top_level:
            match = _TOP_LEVEL_INCLUDE.match(line)
            inside = match is not None
            if match:
                out.append((number, match.group(1).strip(), True))
            continue
        if inside:
            out.append((number, line, False))
    return out


def _flow_keys(text: str) -> list[tuple[str, str]]:
    """``project`` / ``component`` keys of a flow-style include item.

    Only keys one brace deep count - the item's own keys - so a nested
    ``inputs: {project: x}`` is not read as a source.
    """
    keys: list[tuple[str, str]] = []
    for match in _INCLUDE_KEY.finditer(text):
        prefix = text[: match.start(1)]
        if prefix.count("{") - prefix.count("}") == 1:
            keys.append((match.group(1), match.group(2)))
    return keys


def _include_keys(text: str) -> list[tuple[int, str, str]]:
    """(line, key, raw value) for each include item's own source key.

    A block-style key counts only at the include item's own indentation, so
    ``project:`` under a component's ``inputs:`` is an input, not a source.
    """
    keys: list[tuple[int, str, str]] = []
    item_col: int | None = None
    list_col: int | None = None
    for number, line, inline in _include_lines(text):
        if inline:
            item_col = list_col = None
            keys += [(number, k, v) for k, v in _flow_keys(line)]
            continue
        content = line.lstrip()
        col = len(line) - len(content)
        if content.startswith("-"):
            if list_col is None and item_col is None:
                list_col = col
            if col != list_col:
                continue  # an item of a nested list (rules:, inputs:), never a source
            rest = content[1:].lstrip()
            col += len(content) - len(rest)
            content = rest
            item_col = col
        if content.startswith(("{", "[")):
            keys += [(number, k, v) for k, v in _flow_keys(content)]
            continue
        if item_col is None:
            item_col = col
        match = _BLOCK_KEY.match(content)
        if match and col == item_col:
            keys.append((number, match.group(1), match.group(2)))
    return keys


def gitlab_includes(text: str, path: str, tracked: bool) -> list[CiInclude]:
    """``include: project:`` / ``include: component:`` entries in a GitLab CI file."""
    entries: list[CiInclude] = []
    for number, key, raw_value in _include_keys(text):
        value = _unquote(raw_value)
        if not value:
            continue
        if key == "project":
            source = value.strip("/")
            kind: CiKind = "gitlab_project"
        else:
            source = _component_source(value)
            kind = "gitlab_component"
        entries.append({
            "kind": kind,
            "source": source,
            "id": f"{kind}:{source.lower()}",
            "path": path,
            "line": number,
            "tracked": tracked,
        })
    return entries


def _job_uses(text: str) -> list[tuple[int, str]]:
    """(line, value) for each ``uses:`` key directly under a ``jobs.<job_id>``.

    Only a job's own key calls a reusable workflow: a step's ``- uses:`` is an
    action and a ``run:`` script that prints ``uses:`` calls nothing, and both
    sit deeper than the job's keys. A value on the following line is read too.
    """
    rows: list[tuple[int, str]] = []
    in_jobs = False
    job_col: int | None = None
    body_col: int | None = None
    pending: int | None = None
    for number, raw in enumerate(text.splitlines(), start=1):
        line = _strip_comment(raw).rstrip()
        if not line.strip():
            continue
        content = line.lstrip()
        col = len(line) - len(content)
        if pending is not None:
            if body_col is not None and col > body_col:
                rows.append((pending, content))
                pending = None
                continue
            pending = None
        if col == 0:
            in_jobs = _TOP_LEVEL_JOBS.match(line) is not None
            job_col = body_col = None
            continue
        if not in_jobs or (job_col is not None and col < job_col):
            continue
        if job_col is None or col == job_col:
            job_col, body_col = col, None
            continue
        if body_col is None:
            body_col = col
        match = _JOB_USES.match(content) if col == body_col else None
        if match:
            value = match.group(1).strip()
            if value:
                rows.append((number, value))
            else:
                pending = number
    return rows


def github_reusable_workflows(text: str, path: str, tracked: bool) -> list[CiInclude]:
    """Job-level ``uses: owner/repo/.github/workflows/<file>@ref`` references."""
    entries: list[CiInclude] = []
    for number, value in _job_uses(text):
        match = _REUSABLE_REF.match(_unquote(value))
        if match is None:
            continue
        repo = match.group(1)
        entries.append({
            "kind": "github_reusable_workflow",
            "source": repo,
            "id": f"github_reusable_workflow:{repo.lower()}",
            "path": path,
            "line": number,
            "tracked": tracked,
        })
    return entries


def marketplace_id(name: str) -> str:
    """The roll-up identity of a marketplace: its name, as Claude Code keys it."""
    return f"marketplace:{name.strip().lower()}"


def _settings_source(value: Any) -> str:
    """A stable text form of an ``extraKnownMarketplaces`` entry's ``source``."""
    source = value.get("source") if isinstance(value, dict) else None
    if not isinstance(source, dict):
        return ""
    kind = source.get("source")
    for field in ("repo", "url", "path", "package"):
        target = source.get(field)
        if isinstance(target, str) and target:
            return f"{kind}:{target}" if isinstance(kind, str) else target
    return json.dumps(source, sort_keys=True)


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def manifest_marketplace(data: Any, path: str, tracked: bool) -> list[MarketplaceRef]:
    """The marketplace a ``marketplace.json`` declares, or nothing without a name."""
    if not isinstance(data, dict):
        return []
    name = data.get("name")
    if not isinstance(name, str) or not name.strip():
        return []
    return [{
        "kind": "manifest",
        "name": name,
        "source": path,
        "id": marketplace_id(name),
        "path": path,
        "tracked": tracked,
    }]


def settings_marketplaces(data: Any, path: str, tracked: bool) -> list[MarketplaceRef]:
    """Each ``extraKnownMarketplaces`` entry in a settings file, in name order."""
    if not isinstance(data, dict):
        return []
    extra = data.get("extraKnownMarketplaces")
    if not isinstance(extra, dict):
        return []
    return [
        {
            "kind": "settings",
            "name": name,
            "source": _settings_source(extra[name]),
            "id": marketplace_id(name),
            "path": path,
            "tracked": tracked,
        }
        for name in sorted(extra)
        if isinstance(name, str) and name.strip()
    ]


def _in_repo_file(repo_root: Path, path: Path) -> bool:
    """A regular file inside the repository: no symlink, no read outside it.

    A symlink is skipped outright, so a link never borrows its target's
    tracked status or reads a sibling checkout's file under this repo's path.
    """
    try:
        if path.is_symlink() or not path.is_file():
            return False
        path.resolve().relative_to(repo_root)
    except (OSError, ValueError):
        return False
    return True


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _workflow_files(repo_root: Path) -> list[Path]:
    root = repo_root / _WORKFLOWS_DIR
    try:
        children = list(root.iterdir()) if root.is_dir() else []
    except OSError:
        return []
    return sorted(
        p for p in children
        if p.suffix in (".yml", ".yaml") and _in_repo_file(repo_root, p)
    )


def _ci_includes(repo_root: Path, tracked: frozenset[Path] | None) -> list[CiInclude]:
    entries: list[CiInclude] = []
    gitlab = repo_root / _GITLAB_CI
    if _in_repo_file(repo_root, gitlab) and (text := _read_text(gitlab)) is not None:
        entries += gitlab_includes(text, _GITLAB_CI, is_tracked(gitlab, tracked))
    for workflow in _workflow_files(repo_root):
        if (text := _read_text(workflow)) is not None:
            rel = workflow.relative_to(repo_root).as_posix()
            entries += github_reusable_workflows(text, rel, is_tracked(workflow, tracked))
    return entries


def _marketplaces(repo_root: Path, tracked: frozenset[Path] | None) -> list[MarketplaceRef]:
    entries: list[MarketplaceRef] = []
    manifest = repo_root / _MARKETPLACE_MANIFEST
    if _in_repo_file(repo_root, manifest):
        entries += manifest_marketplace(
            _read_json(manifest), _MARKETPLACE_MANIFEST, is_tracked(manifest, tracked)
        )
    settings = repo_root / _SETTINGS
    if _in_repo_file(repo_root, settings):
        entries += settings_marketplaces(
            _read_json(settings), _SETTINGS, is_tracked(settings, tracked)
        )
    return entries


def scan_shared_tooling(repo_root: Path) -> SharedToolingBlock:
    """Build the run-context ``shared_tooling`` block for one repository."""
    repo_root = repo_root.resolve()
    tracked = tracked_files(repo_root)
    ci_includes = _ci_includes(repo_root, tracked)
    marketplaces = _marketplaces(repo_root, tracked)
    block: SharedToolingBlock = {
        "available": tracked is not None,
        "ci_includes": ci_includes,
        "marketplaces": marketplaces,
        "ci_source_ids": sorted({e["id"] for e in ci_includes if e["tracked"]}),
        "marketplace_ids": sorted({e["id"] for e in marketplaces if e["tracked"]}),
    }
    if tracked is None:
        # Empty id lists here mean "tracking unknown", not "shares nothing":
        # a roll-up must skip this repository rather than count it as zero.
        block["reason"] = "git tracking unknown (not a git checkout, or git failed)"
    return block


def distinct_ids(blocks: Iterable[Mapping[str, Any]]) -> DistinctIds:
    """Distinct shared CI sources and marketplaces across repositories' blocks.

    The roll-up a fleet report runs per group: union the per-repository id
    lists. A degraded block (``available: false``) contributes nothing.
    """
    ci: set[str] = set()
    markets: set[str] = set()
    for block in blocks:
        if not block.get("available"):
            continue
        ci.update(block.get("ci_source_ids") or [])
        markets.update(block.get("marketplace_ids") or [])
    return {"ci_sources": sorted(ci), "marketplaces": sorted(markets)}
