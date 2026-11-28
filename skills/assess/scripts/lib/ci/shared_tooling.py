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
the version and the GitLab host are dropped); the pinned version is kept apart
as ``ref`` (a GitLab include's ``ref:``, a component's or reusable workflow's
``@ref``; null when unpinned), so a fleet map can show a pin that drifted (#674). A ref
that holds a CI variable (``$TPL_REF``) names no version the file states: ``ref`` is null
and ``ref_variable`` true, so the map draws it as "pin unknown", never as unpinned or as
drift (#684). A marketplace is identified by
its name, the key Claude Code itself uses to tell marketplaces apart, so a
manifest and a settings entry naming the same marketplace collapse to one.
``distinct_ids`` is that roll-up over several blocks.

Only git-tracked files are credited, as in ``agent_ops``: an uncommitted file
reaches no clone. Untracked entries are listed with ``tracked: false`` and left
out of ``ci_source_ids`` and ``marketplace_ids``. Deterministic and offline:
file reads plus ``git ls-files``. YAML is read line by line (stdlib only); a
file it cannot read yields no entries rather than an error.

Shared CI sources and agent marketplaces, per repository (issue #566), the
file-content half of the organisation-wide evidence in #550.
`scan_shared_tooling(repo_root)` writes `{available, ci_includes[], marketplaces[],
ci_source_ids[], marketplace_ids[]}`. `ci_includes` rows (`kind`, `source`, `id`,
`ref`, `ref_variable`, `path`, `line`, `tracked`) come from the top-level `include:` section of
`.gitlab-ci.yml` (`project:` -> `gitlab_project`, `component:` ->
`gitlab_component` with the host and `@version` dropped) and from `uses:
<owner>/<repo>/.github/workflows/...` in `.github/workflows/*.yml`
(`github_reusable_workflow`, keyed on the repository). `ref` is the pin kept apart
from the id (#674): a GitLab include item's own `ref:` (joined by include item, so a
`ref:` under `inputs:` is not it), a component's or workflow's `@ref`, `null` when
unpinned. A ref holding a CI variable (`$TPL_REF`, `@${VERSION}`) is recorded as `ref`
`null` with `ref_variable` true (#684): pinned, to a value the file does not say, so `--full`
and counts-only agree and the map draws it as "pin unknown". Counts-only keeps a `ref` that is a token (`v4`, `1.2.0`, a SHA) and drops it otherwise (`release/1.2`). Local includes, templates,
`./.github/workflows` references, a job's `trigger:`, keys in nested lists
(`inputs:`, `rules:`), step-level `uses:` and `run:` script text are skipped.
Symlinks and paths resolving outside the repository are not read. `marketplaces`
rows (`kind`, `name`, `source`, `id`, `path`, `tracked`) are the
`.claude-plugin/marketplace.json` manifest (`manifest`) and each
`extraKnownMarketplaces` entry in `.claude/settings.json` (`settings`, `source` as
`<kind>:<repo|url|path>`). A marketplace's `id` is its lowercased name, the key
Claude Code tells marketplaces apart by, so a manifest and a settings entry naming
it count once. The two id lists credit git-tracked files only, as `agent_ops` does. Outside a git checkout (or when git fails) the block is `available: false` with a `reason`, so a roll-up never reads unknown tracking as "shares nothing"; entries are still listed. `is_tracked` lives in `git_churn` beside `tracked_files`, shared with `agent_ops`.
`distinct_ids(blocks)` is the fleet roll-up: the union of the id lists, skipping
`available: false` blocks. YAML is read line by line (stdlib); offline, no `gh`.
`step_uses(text)` (#696) is the step-level reader the block itself does not use: (line,
value) for each `uses:` that is a step's own key (on its `- ` line or at its key column)
inside a job's `steps:` list, skipping job-level `uses:`, a `uses:` nested under `with:`, and the
body of a block scalar (`run: |`, `prompt: |`); `action_repo(value)` turns one into
`owner/repo` (lowercased; None for `./` and `docker://`). Actions are not shared CI
sources, so they never enter `ci_includes` or `ci_source_ids`. `workflow_events(text)`
reads the top-level `on:` through `ci_triggers.workflow_events`, the one shared `on:`
reader (#1036), keeping event-shaped names; None when there is none it can read.
`ON_KEY` (the `on:` key, YAML 1.1 `true:` included) is shared with `agent_harness`.
`include_lines` and `component_source` expose the include-section reader to `ci_includes`;
`strip_comment` exposes the comment stripper to `ci_include_inputs` (#1339). A ref holding
`$[[ inputs.<name> ]]` reads here as `ref_variable` (its value is set by whoever includes the
file); `ci_includes` resolves it from the include's inputs before reading the clone.
"""
from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any, Literal, NotRequired, TypedDict

from lib.ci.ci_triggers import workflow_events as _on_events
from lib.history.git_churn import is_tracked, tracked_files

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
    ref: str | None  # the pinned version: GitLab ``ref:``, component or workflow ``@ref``
    ref_variable: bool  # the ref holds a CI variable: pinned, to a version the file does not say
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
_INCLUDE_KEY = re.compile(r"(?:^|[\s{,])(project|component|ref)\s*:\s*" + _INCLUDE_VALUE)
_BLOCK_KEY = re.compile(r"^(project|component|ref)\s*:\s*" + _INCLUDE_VALUE)
_REUSABLE_REF = re.compile(
    r"""^([A-Za-z0-9_.\-]+/[A-Za-z0-9_.\-]+)/\.github/workflows/[^\s"'#@]+(?:@([^\s"'#]+))?"""
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


def pin(ref: str | None) -> str | None:
    """A pinned ref as recorded: None when absent or when it holds a CI variable (``$TPL_REF``),
    whose value the file does not say, so ``--full`` and counts-only agree."""
    ref = (ref or "").strip()
    return None if not ref or is_variable(ref) else ref


def is_variable(ref: str | None) -> bool:
    """The ref holds a CI variable (``$TPL_REF``, ``${VERSION}``): pinned, value unknown."""
    return "$" in (ref or "")


def _component_ref(raw: str) -> str:
    """``host/group/project/name@1.0`` -> ``1.0``; empty without a version."""
    return raw.partition("@")[2]


def _component_source(raw: str) -> str:
    """``host/group/project/name@1.0`` -> ``group/project/name``."""
    path = raw.split("@", 1)[0].strip("/")
    first, _, rest = path.partition("/")
    if rest and (first in _HOST_VARS or "." in first or ":" in first):
        path = rest
    return path


def include_lines(text: str) -> list[tuple[int, str, bool]]:
    """The top-level ``include:`` section rows (``_include_lines``), for ``lib/ci/ci_includes.py``."""
    return _include_lines(text)


def component_source(raw: str) -> str:
    """A component reference's ``group/project/name`` (``_component_source``)."""
    return _component_source(raw)


def strip_comment(line: str) -> str:
    """``line`` without a trailing unquoted ``# comment`` (``_strip_comment``), for
    ``lib/ci/ci_include_inputs.py`` (#1339)."""
    return _strip_comment(line)


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
    return _join_flow(out)


def _flow_depth(line: str) -> int:
    """Net ``{``/``[`` opened by ``line``, ignoring brackets inside quoted scalars."""
    depth = 0
    quote = ""
    for ch in line:
        if quote:
            quote = "" if ch == quote else quote
        elif ch in "\"'":
            quote = ch
        elif ch in "{[":
            depth += 1
        elif ch in "}]":
            depth -= 1
    return depth


def _join_flow(rows: list[tuple[int, str, bool]]) -> list[tuple[int, str, bool]]:
    """Join a flow collection that spans lines (``- {project: a,`` then ``ref: v1}``)
    into its first row, so its keys stay one item.

    Only a row that opens a flow collection (its value, after an optional ``- ``,
    starts with ``{`` or ``[``) starts a join, and quoted brackets are not counted,
    so a bracket in a ``rules: if:`` regex never swallows the items after it.
    """
    joined: list[tuple[int, str, bool]] = []
    depth = 0
    for number, line, inline in rows:
        if depth > 0:
            first, text, flag = joined[-1]
            joined[-1] = (first, f"{text} {line.strip()}", flag)
            depth = max(depth + _flow_depth(line), 0)
            continue
        joined.append((number, line, inline))
        value = line.strip()
        value = value[1:].lstrip() if value.startswith("-") else value
        if value.startswith(("{", "[")):
            depth = max(_flow_depth(value), 0)
    return joined


def _flow_keys(text: str) -> list[tuple[str, str, int]]:
    """``project`` / ``component`` / ``ref`` keys of flow-style include items, with each
    key's item number on the line (``[{project: a, ref: v1}, {project: b}]`` is two items).

    Only keys one brace deep count - the item's own keys - so a nested
    ``inputs: {project: x}`` is not read as a source.
    """
    keys: list[tuple[str, str, int]] = []
    for match in _INCLUDE_KEY.finditer(text):
        prefix = text[: match.start(1)]
        if prefix.count("{") - prefix.count("}") == 1:
            keys.append((match.group(1), match.group(2), _objects_opened(prefix)))
    return keys


def _objects_opened(prefix: str) -> int:
    """How many top-level ``{`` objects ``prefix`` has opened."""
    depth = opened = 0
    for ch in prefix:
        if ch == "{":
            opened += depth == 0
            depth += 1
        elif ch == "}":
            depth = max(depth - 1, 0)
    return opened


def _include_keys(text: str) -> list[tuple[int, str, str, int]]:
    """(line, key, raw value, item) for each include item's own source and ``ref`` key.

    A block-style key counts only at the include item's own indentation, so
    ``project:`` under a component's ``inputs:`` is an input, not a source.
    ``item`` numbers the include items, so a ``ref:`` joins its own item's project.
    """
    keys: list[tuple[int, str, str, int]] = []
    item_col: int | None = None
    list_col: int | None = None
    item = 0
    for number, line, inline in _include_lines(text):
        if inline:
            item_col = list_col = None
            flow = _flow_keys(line)
            keys += [(number, k, v, item + 1 + sub) for k, v, sub in flow]
            item += 1 + max((sub for _, _, sub in flow), default=0)
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
            item += 1
        if content.startswith(("{", "[")):
            flow = _flow_keys(content)
            keys += [(number, k, v, item + sub) for k, v, sub in flow]
            item += max((sub for _, _, sub in flow), default=0)
            continue
        if item_col is None:
            item_col = col
        match = _BLOCK_KEY.match(content)
        if match and col == item_col:
            keys.append((number, match.group(1), match.group(2), item))
    return keys


def gitlab_includes(text: str, path: str, tracked: bool) -> list[CiInclude]:
    """``include: project:`` / ``include: component:`` entries in a GitLab CI file."""
    entries: list[CiInclude] = []
    keys = _include_keys(text)
    refs = {item: _unquote(v) for _, k, v, item in keys if k == "ref"}
    for number, key, raw_value, item in keys:
        value = _unquote(raw_value)
        if not value or key == "ref":
            continue
        if key == "project":
            source = value.strip("/")
            kind: CiKind = "gitlab_project"
            raw_ref = refs.get(item)
        else:
            source = _component_source(value)
            kind = "gitlab_component"
            raw_ref = _component_ref(value)
        entries.append({
            "kind": kind,
            "source": source,
            "id": f"{kind}:{source.lower()}",
            "ref": pin(raw_ref),
            "ref_variable": is_variable(raw_ref),
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


_STEP_USES = re.compile(r"""^(?:-\s+)?(?:uses|"uses"|'uses')\s*:(.*)$""")
_STEPS_KEY = re.compile(r"""^(?:steps|"steps"|'steps')\s*:\s*$""")
_BLOCK_SCALAR = re.compile(r""":\s*[|>][-+0-9]*\s*$""")
_ACTION_REF = re.compile(r"""^([A-Za-z0-9_.\-]+/[A-Za-z0-9_.\-]+)(?:/[^\s"'#@]*)?(?:@[^\s"'#]+)?$""")


def step_uses(text: str) -> list[tuple[int, str]]:
    """(line, value) for each step-level ``uses:`` in a GitHub workflow (#696).

    A step's ``- uses: owner/repo@ref`` (or ``uses:`` under a step's ``- name:``)
    inside a job's ``steps:`` list runs an action. Job-level ``uses:`` (a
    reusable workflow, ``_job_uses``) is not a step, and the body of a block
    scalar (``run: |``) is skipped, so a script that prints ``uses:`` runs nothing.
    Only a step's own key counts: ``uses:`` on the step's ``- `` line or at its key
    column, never one nested deeper (under ``with:``).
    """
    rows: list[tuple[int, str]] = []
    steps_col: int | None = None
    scalar_col: int | None = None
    dash_col: int | None = None  # the column of the steps list's "- "
    step_key_col: int | None = None  # the current step mapping's key column
    for number, raw in enumerate(text.splitlines(), start=1):
        line = _strip_comment(raw).rstrip()
        if not line.strip():
            continue
        content = line.lstrip()
        col = len(line) - len(content)
        if scalar_col is not None:
            if col > scalar_col:
                continue
            scalar_col = None
        if steps_col is not None and col <= steps_col and not content.startswith("-"):
            steps_col = None
        if steps_col is not None and col < steps_col:
            steps_col = None
        key = content[1:].lstrip() if content.startswith("-") else content
        key_col = col + len(content) - len(key)
        if _BLOCK_SCALAR.search(key):
            scalar_col = key_col
        if _STEPS_KEY.match(key):
            steps_col, dash_col, step_key_col = col, None, None
            continue
        if steps_col is None or col < steps_col:
            continue
        own_key = False
        if content.startswith("-") and (dash_col is None or col == dash_col):
            dash_col, step_key_col, own_key = col, key_col, True
        elif not content.startswith("-") and col == step_key_col:
            own_key = True
        match = _STEP_USES.match(key) if own_key else None
        if match and (value := _unquote(match.group(1))):
            rows.append((number, value))
    return rows


# A workflow's top-level ``on:`` key. ``true:`` is how a YAML 1.1 loader (PyYAML)
# reads and re-serialises the bare ``on`` key, so a workflow round-tripped through
# one carries it. Shared with ``lib/agents/agent_harness.py``.
ON_KEY = re.compile(r"""^(?:on|"on"|'on'|true)\s*:(?P<rest>.*)$""")
_EVENT_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def workflow_events(text: str) -> frozenset[str] | None:
    """The events a GitHub workflow's top-level ``on:`` names, or None when it has none
    the shared reader (``ci_triggers.workflow_events``, #1036) can read."""
    names = frozenset(e for e in _on_events(text) or () if _EVENT_NAME.match(e))
    return names or None


def action_repo(value: str) -> str | None:
    """``owner/repo[/path]@ref`` -> ``owner/repo`` lowercased; None for a local
    (``./``) or ``docker://`` action, or a value that names no repository."""
    value = _unquote(value)
    if value.startswith(("./", "docker://")):
        return None
    match = _ACTION_REF.match(value)
    return match.group(1).lower() if match else None


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
            "ref": pin(match.group(2)),
            "ref_variable": is_variable(match.group(2)),
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


def pulls_jobs_from_elsewhere(text: str, gitlab: bool) -> bool:
    """A CI file whose jobs can come from a file this checkout does not hold.

    GitLab: any top-level ``include:`` (local, template, remote, project or
    component). GitHub: a job-level ``uses:`` that calls a reusable workflow in
    another repository (``./`` references point at an indexed local workflow).
    """
    if gitlab:
        return bool(_include_lines(text))
    return any(not _unquote(value).startswith("./") for _, value in _job_uses(text))


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
