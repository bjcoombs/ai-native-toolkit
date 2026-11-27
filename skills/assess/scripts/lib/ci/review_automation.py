"""AI review committed in CI, read statically (issue #696): the ``review_automation`` and ``review_evidence`` blocks.

Layer 7 used to read only ``review_reality``, which needs the platform API, so a
headless or fleet run read it as unknown on every repository, including those
where an agent reviews every change. The evidence that one does is often
committed to the repository:

- **CI step or component** (``ci_action``, ``ci_component``). A GitHub workflow
  step whose ``uses:`` action (``shared_tooling.step_uses``) is in
  ``REVIEW_ACTIONS``, or a GitLab ``include: component:`` / ``include: project:``
  entry (``shared_tooling.gitlab_includes``, the same parser the shared-tooling
  map uses) whose last path segment matches ``REVIEW_COMPONENT``. Either also
  matches when its source is listed in the fleet config's ``[review]
  ai_review_components`` (``lib/fleet_config.py``): an entry is a GitLab project
  or component path or a GitHub ``owner/repo`` action, compared lowercased with
  the host and version dropped; a project path covers the components under it.
- **Review app configuration** (``config_file``): ``REVIEW_CONFIG_FILES``, the
  configuration files of AI review apps. Each names the app's login in
  ``delivery.AI_REVIEWERS``, so the two lists name the same apps. Copilot code
  review has no review-specific file (``.github/copilot-instructions.md`` also
  steers chat and the coding agent, so it is Layer 0 evidence, not review) and
  is not read.

``REVIEW_ACTIONS`` and ``REVIEW_COMPONENT`` are the pinned matcher; ``is_review_action``
and ``is_review_component`` expose it for other CI readers (#689).

Only git-tracked files are credited, as in ``shared_tooling``: an untracked
match is listed with ``tracked: false`` and does not set ``present``. A
``ci_action`` is credited only when its workflow's ``on:`` names a
``REVIEW_EVENTS`` trigger (``pull_request``, ``pull_request_target``,
``merge_group``); one wired only to ``issue_comment`` or ``issues`` is listed
with ``on_changes: false``. A workflow whose ``on:`` the shared ``on`` reader
(``ci_triggers.workflow_events``, #1036) cannot read is credited, except an unclosed
flow collection (``ci_triggers.unclosed_on``), which GitHub rejects. A GitLab include is credited as committed: the jobs it brings live
in the included file, so whether they run in merge-request pipelines is not read
(a known limit). Includes inside a GitLab template the run resolved offline
(``lib/ci/ci_includes.py``, #766: a ``local:`` file, or a ``project:`` /
``component:`` clone in the ``--ghsync-root``) are matched the same way: such a
match keeps the path and line of the repository's own include that led to it,
tracked as that file is, with ``depth`` the include level it was found at (0
for the repository's own file). Job scripts in a template are not read for
review steps; a review is seen through a review component or project name, or a
``[review] ai_review_components`` entry (a known limit). Under ``--platform``
(and in a full ``/assess`` run), where an include did not resolve offline, the
sources GitLab's CI lint API lists as merged in (``lib/hosting/gitlab_ci_lint.py``,
#796: a component location, a project file's project) are matched the same way;
a source an offline match already names is not listed twice. Such a match has
``line`` null and ``depth`` 1 (the API does not say the level). When git
tracking is unknown the block is unavailable. Counts-only output keeps
``present``, the ``kinds`` and each match's ``kind``, ``rule`` (``pinned`` or
``config``) and ``reviewer`` slug (``configured`` for a config match), never a
source or a config entry.

``review_evidence`` is the derived Layer 7 level 1 signal: AI review in CI, or a
review on a sampled merged change (``review_reality.reviewed_share > 0``, or, where
``reviewed_share`` is null because only approvals are read, ``approved_share > 0``, or
``bot_review_share > 0``: on GitLab an AI reviewer's note, not the author's, on a sampled
merged change, #1568).
``present`` is true when either source is true, false when the automation scan
found none and ``reviewed_share`` is 0 (a review config file alone does not
count against a measured 0: only a CI step, or a measured ``bot_review_share >
0``, does), and otherwise the block is unavailable. A review app's config file
with no CI step reads unavailable too while ``reviewed_share`` is unread (#922):
the app may not be installed, so the file waits for ``fleet --platform``
with the reason of the block that could not say (``review_reality``'s first,
so a headless run's ``network`` reason reaches the ``fleet --platform`` hint).
It is listed in ``signals.PLATFORM_SCANS`` so the platform pass rebuilds it from
the measured ``review_reality``.

AI review committed in CI and the Layer 7 level 1 signal (issue #696).
`scan_review_automation(repo_root, fleet_config, ghsync_root, merged_ci)` writes `{available, present, kinds[],
matches[]}`; a match is `{kind, rule, reviewer, path, line, tracked, on_changes, depth}`.
The include entries of GitLab files `ci_includes.resolve_includes` read are matched as the
repository's own are (#766), keeping the path and line of the repository's own include that
led there, with `depth` the include level (0 for the repository's own file). Its fourth
argument is the `gitlab_merged_ci` reading (#796): the sources GitLab's CI lint API lists as
merged in are matched the same way, once each (a source an offline entry names is skipped),
with `line` null and `depth` 1. Kinds:
`ci_action` (a `shared_tooling.step_uses` action in the pinned `REVIEW_ACTIONS`: any
`anthropics/*`, `coderabbitai/*`, `qodo-ai`/`codium-ai` `pr-agent`; a name heuristic, so whether an included job runs in merge-request
pipelines is not read), `ci_component` (a
`shared_tooling.gitlab_includes` component or project whose last path segment matches
`REVIEW_COMPONENT`, a name heuristic: `ai-code-review`, `claude_review`, `llm-review`,
`code-review-ai`, `pr-agent`), and `config_file` (`REVIEW_CONFIG_FILES`: `.coderabbit.yaml`/`.yml`,
`.gemini/config.yaml`, `.pr_agent.toml`, each mapped to its `delivery.AI_REVIEWERS`
login). A CI action or include also matches (`rule: config`, `reviewer: configured`)
when its source equals, or sits under, a `[review] ai_review_components` entry
(lowercased, host, a leading CI variable such as `$CI_SERVER_FQDN`, and `@version` dropped). Copilot code review has no review-specific
file and is not read. A `ci_action` is credited only when its workflow's `on:` (`shared_tooling.workflow_events`)
names `pull_request`, `pull_request_target` or `merge_group` (`REVIEW_EVENTS`): a step wired
only to `issue_comment` or `issues` (an @claude responder) is listed with `on_changes:
false`; an `on:` the line reader cannot read is credited. `present` and `kinds` credit
git-tracked matches only; outside a
git checkout the block is `available: false`. Counts-only keeps the booleans, kinds,
rules and reviewer slugs, never a source or a config entry. `is_review_action` and
`is_review_component` expose the pinned matcher to other CI readers (#689).
`review_evidence(review_automation, review_reality)` is the derived block the default
maturity model's Layer 7 level 1 reads: `{available, present, ai_review_in_ci,
reviewed_changes}`; `present` is true when an AI review step is credited or
`review_reality.reviewed_share > 0` (`approved_share > 0` or `bot_review_share > 0`
where `reviewed_share` is null, #1568), false when neither block shows review (a review config file alone does not count
against a measured `reviewed_share` of 0; a CI step, or a measured `bot_review_share > 0`,
does; with no CI step it reads unavailable while `reviewed_share` is unread, #922), and
otherwise the block is unavailable
with the reason of the block that could not say, `review_reality`'s first (so the
offline `network` code gives the `fleet --platform` hint). It is in
`signals.PLATFORM_SCANS`, so the platform pass rebuilds it from the measured
`review_reality`. Tests: `tests/test_review_automation.py`.
"""
from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Literal, NotRequired, TypeGuard, TypedDict

from lib.ci.ci_includes import resolve_includes
from lib.ci.ci_triggers import unclosed_on
from lib.ci.merged_ci import MergedCi, as_merged
from lib.fleet_config import FleetConfig
from lib.history.git_churn import is_tracked, tracked_files
from lib.ci.shared_tooling import (
    action_repo,
    component_source,
    gitlab_includes,
    step_uses,
    workflow_events,
)

ReviewKind = Literal["ci_action", "ci_component", "config_file"]
ReviewRule = Literal["pinned", "config"]
KINDS: tuple[ReviewKind, ...] = ("ci_action", "ci_component", "config_file")
CONFIGURED = "configured"

_GITLAB_CI = ".gitlab-ci.yml"
_WORKFLOWS_DIR = ".github/workflows"

# GitHub actions that run an AI review, by ``owner/repo`` (lowercased): (slug, pattern).
# Any ``anthropics/*`` action counts (claude-code-action, claude-code-base-action).
REVIEW_ACTIONS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("claude-code", re.compile(r"^anthropics/[a-z0-9_.-]+$")),
    ("coderabbit", re.compile(r"^coderabbitai/[a-z0-9_.-]+$")),
    ("qodo", re.compile(r"^(?:qodo-ai|codium-ai)/pr-agent$")),
)

# A GitLab CI component or included project whose last path segment names AI
# review: ``ai-code-review``, ``claude_review``, ``llm-review``, ``code-review-ai``,
# ``pr-agent``. A heuristic on names; ``review-bot`` (no AI word) does not match, and an
# in-house name goes in ``[review] ai_review_components``.
REVIEW_COMPONENT = re.compile(
    r"(?:^|[-_.])(?:ai|llm|claude|gpt|gemini|codex|coderabbit|duo)[-_]?(?:code[-_]?)?review"
    r"|(?:^|[-_.])(?:code[-_]?)?review[-_]?(?:ai|llm|agent)(?:$|[-_.])"
    r"|(?:^|[-_.])(?:pr|mr)[-_]agent(?:$|[-_.])"
)

# The workflow events that run on a proposed change: a review action wired only to
# ``issue_comment`` or ``issues`` (an @claude responder, issue triage) reviews nothing.
REVIEW_EVENTS = frozenset({"pull_request", "pull_request_target", "merge_group"})

# Configuration files of AI review apps: path -> the app's ``delivery.AI_REVIEWERS`` login.
REVIEW_CONFIG_FILES: Mapping[str, str] = {
    ".coderabbit.yaml": "coderabbitai",
    ".coderabbit.yml": "coderabbitai",
    ".gemini/config.yaml": "gemini-code-assist",
    ".pr_agent.toml": "qodo-merge-pro",
}


class ReviewMatch(TypedDict):
    """One committed piece of AI review evidence."""

    kind: ReviewKind
    rule: ReviewRule
    reviewer: str  # a pinned slug or AI_REVIEWERS login; "configured" for a config match
    path: str
    line: int | None  # None for a config file
    tracked: bool
    on_changes: bool  # runs on proposed changes (a ci_action's workflow triggers on pull requests)
    depth: int  # include level the match was found at: 0 for the repository's own file (#766)


class ReviewAutomationBlock(TypedDict):
    """``run-context.json`` ``review_automation``."""

    available: bool
    present: bool  # a tracked match: "AI review in CI"
    kinds: list[ReviewKind]  # distinct kinds of the tracked matches, in KINDS order
    matches: list[ReviewMatch]
    reason: NotRequired[str]


class ReviewEvidenceBlock(TypedDict):
    """``run-context.json`` ``review_evidence``: Layer 7 level 1."""

    available: bool
    present: bool | None  # AI review in CI, or a reviewed merged change
    ai_review_in_ci: bool | None  # review_automation.present; None when unavailable
    reviewed_changes: bool | None  # review_reality.reviewed_share > 0; None when unread
    reason: NotRequired[str]


def is_review_action(repo: str) -> str | None:
    """The pinned slug of a review action (``owner/repo``), or None."""
    repo = repo.lower()
    return next((slug for slug, pattern in REVIEW_ACTIONS if pattern.match(repo)), None)


def is_review_component(source: str) -> bool:
    """A GitLab component or project path whose last segment names AI review."""
    name = source.rstrip("/").rsplit("/", 1)[-1].lower()
    return REVIEW_COMPONENT.search(name) is not None


def _normalise(entry: str) -> str:
    """A config entry as a source: lowercased, version and host dropped, as
    ``shared_tooling`` drops them from an include (a host, or a CI variable such as
    ``$CI_SERVER_FQDN``)."""
    path = entry.strip().split("@", 1)[0].strip("/").lower()
    first, _, rest = path.partition("/")
    if rest and ("." in first or ":" in first or first.startswith("$")):
        path = rest
    return path


def configured_components(config: FleetConfig | None) -> list[str]:
    """``[review] ai_review_components``, normalised."""
    entries = config["review"].get("ai_review_components", []) if config else []
    return [n for n in (_normalise(e) for e in entries if isinstance(e, str)) if n]


def _configured(source: str, configured: Sequence[str]) -> bool:
    source = source.lower()
    return any(source == entry or source.startswith(entry + "/") for entry in configured)


def _match(kind: ReviewKind, source: str, pinned: str | None, configured: Sequence[str],
           path: str, line: int | None, tracked: bool, on_changes: bool = True,
           depth: int = 0) -> ReviewMatch | None:
    if pinned is not None:
        return {"kind": kind, "rule": "pinned", "reviewer": pinned, "path": path, "line": line,
                "tracked": tracked, "on_changes": on_changes, "depth": depth}
    if _configured(source, configured):
        return {"kind": kind, "rule": "config", "reviewer": CONFIGURED, "path": path, "line": line,
                "tracked": tracked, "on_changes": on_changes, "depth": depth}
    return None


def runs_on_changes(text: str) -> bool:
    """A workflow that triggers on proposed changes (``REVIEW_EVENTS``); one whose ``on:``
    cannot be read is credited, as a GitLab include is, unless it is an unclosed flow
    collection GitHub rejects (#1036)."""
    if unclosed_on(text):
        return False
    events = workflow_events(text)
    return events is None or bool(events & REVIEW_EVENTS)


def _in_repo_file(repo_root: Path, path: Path) -> bool:
    try:
        if path.is_symlink() or not path.is_file():
            return False
        path.resolve().relative_to(repo_root)
    except (OSError, ValueError):
        return False
    return True


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _gitlab_matches(repo_root: Path, tracked: frozenset[Path] | None,
                    configured: Sequence[str], ghsync_root: Path | None,
                    merged: MergedCi) -> list[ReviewMatch]:
    path = repo_root / _GITLAB_CI
    if not _in_repo_file(repo_root, path) or (text := _read(path)) is None:
        return []
    own_tracked = is_tracked(path, tracked)
    sources = [(text, None, 0)] + [(f.text, f.line, f.depth)
                                   for f in resolve_includes(repo_root, ghsync_root).files]
    out: list[ReviewMatch] = []
    seen: set[str] = set()
    for body, top_line, depth in sources:
        for entry in gitlab_includes(body, _GITLAB_CI, own_tracked):
            seen.add(component_source(entry["source"]).lower())
            pinned = "ai-review-component" if is_review_component(entry["source"]) else None
            match = _match("ci_component", entry["source"], pinned, configured, _GITLAB_CI,
                           entry["line"] if top_line is None else top_line, entry["tracked"],
                           depth=depth)
            if match is not None:
                out.append(match)
    for source in merged.include_sources if merged.read else ():
        if component_source(source).lower() in seen:
            continue
        seen.add(component_source(source).lower())
        pinned = "ai-review-component" if is_review_component(source) else None
        match = _match("ci_component", source, pinned, configured, _GITLAB_CI, None,
                       own_tracked, depth=1)
        if match is not None:
            out.append(match)
    return out


def _workflow_matches(repo_root: Path, tracked: frozenset[Path] | None,
                      configured: Sequence[str]) -> list[ReviewMatch]:
    root = repo_root / _WORKFLOWS_DIR
    try:
        files = sorted(p for p in root.iterdir() if p.suffix in (".yml", ".yaml")) if root.is_dir() else []
    except OSError:
        return []
    out: list[ReviewMatch] = []
    for workflow in files:
        if not _in_repo_file(repo_root, workflow) or (text := _read(workflow)) is None:
            continue
        rel = workflow.relative_to(repo_root).as_posix()
        for line, value in step_uses(text):
            repo = action_repo(value)
            if repo is None:
                continue
            match = _match("ci_action", repo, is_review_action(repo), configured, rel, line,
                           is_tracked(workflow, tracked), runs_on_changes(text))
            if match is not None:
                out.append(match)
    return out


def _config_file_matches(repo_root: Path, tracked: frozenset[Path] | None) -> list[ReviewMatch]:
    out: list[ReviewMatch] = []
    for rel, reviewer in sorted(REVIEW_CONFIG_FILES.items()):
        path = repo_root / rel
        if _in_repo_file(repo_root, path):
            out.append({"kind": "config_file", "rule": "pinned", "reviewer": reviewer, "path": rel,
                        "line": None, "tracked": is_tracked(path, tracked), "on_changes": True,
                        "depth": 0})
    return out


def scan_review_automation(repo_root: Path, fleet_config: FleetConfig | None = None,
                           ghsync_root: Path | None = None,
                           merged_ci: object = None) -> ReviewAutomationBlock:
    """Build the ``review_automation`` block for one repository. ``merged_ci`` is the
    ``gitlab_merged_ci`` reading (``lib/hosting/gitlab_ci_lint.py``); anything else is not read."""
    repo_root = repo_root.resolve()
    tracked = tracked_files(repo_root)
    configured = configured_components(fleet_config)
    matches = (_gitlab_matches(repo_root, tracked, configured, ghsync_root, as_merged(merged_ci))
               + _workflow_matches(repo_root, tracked, configured)
               + _config_file_matches(repo_root, tracked))
    credited = {m["kind"] for m in matches if m["tracked"] and m["on_changes"]}
    block: ReviewAutomationBlock = {
        "available": tracked is not None,
        "present": bool(credited),
        "kinds": [kind for kind in KINDS if kind in credited],
        "matches": matches,
    }
    if tracked is None:
        block["reason"] = "git_tracking_unknown: not a git checkout, or git failed"
    return block


def _reason(block: Mapping[str, Any] | None, name: str) -> str:
    reason = block.get("reason") if isinstance(block, Mapping) else None
    return reason if isinstance(reason, str) and reason else f"not_measured: {name} was not built"


def _number(value: object) -> TypeGuard[int | float]:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def review_evidence(review_automation: Mapping[str, Any] | None,
                    review_reality: Mapping[str, Any] | None) -> ReviewEvidenceBlock:
    """AI review in CI or a reviewed merged change (see the module docstring)."""
    automation: bool | None = None
    if isinstance(review_automation, Mapping) and review_automation.get("available") is True:
        automation = review_automation.get("present") is True
    reviewed: bool | None = None
    share = review_reality.get("reviewed_share") if isinstance(review_reality, Mapping) else None
    approved = review_reality.get("approved_share") if isinstance(review_reality, Mapping) else None
    bot = review_reality.get("bot_review_share") if isinstance(review_reality, Mapping) else None
    if isinstance(review_reality, Mapping) and review_reality.get("available") is True:
        if _number(share):
            reviewed = share > 0
        elif _number(approved) and approved > 0:
            reviewed = True  # GitLab reads approvals only: an approval by another account is review
        elif _number(bot) and bot > 0:
            # #1568: GitLab notes left reviewed_share undecided, but an AI reviewer's note
            # (not the author's) on a sampled merged change is review of that change.
            reviewed = True
    block: ReviewEvidenceBlock = {
        "available": True,
        "present": None,
        "ai_review_in_ci": automation,
        "reviewed_changes": reviewed,
    }
    credited = automation
    kinds = review_automation.get("kinds") if isinstance(review_automation, Mapping) else None
    ci_step = bool(set(kinds or ()) & {"ci_action", "ci_component"})
    if automation and reviewed is None and not ci_step:
        # A review app's config file alone (#922): the app may not be installed, so it
        # proves nothing until review_reality is read (fleet --platform).
        credited = None
    if automation and reviewed is False:
        # Review measured as absent: a config file alone (a stale .coderabbit.yaml)
        # contradicts it. A CI step still counts (claude-code-action posts comments,
        # which reviewed_share does not read), and so does a measured bot review.
        credited = ci_step or (_number(bot) and bot > 0)
    if credited or reviewed:
        block["present"] = True
    elif automation is not None and reviewed is False:
        block["present"] = False
    else:
        block["available"] = False
        if reviewed is None:
            if isinstance(review_reality, Mapping) and review_reality.get("available") is True:
                block["reason"] = "not_measured: review_reality.reviewed_share is null"
            else:
                block["reason"] = _reason(review_reality, "review_reality")
        else:
            block["reason"] = _reason(review_automation, "review_automation")
    return block
