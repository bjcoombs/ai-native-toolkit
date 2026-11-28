"""Whether production deployment environments require a reviewer, read through gh or glab (#1666).

A change can be reviewed and merged and still reach production through a deployment no one
approved. This block counts the production environments and how many of them let a deployment
run without a second person: none of the evidence is in the commit, so it is a network scan
(``network = true``, ``platform_pass = true``). A headless run without ``--platform`` reports
the ``network`` reason.

Production environments. ``[change_controls] environments`` in ``.assess/config.toml`` names
them (a list of names; an empty, blank or malformed list keeps the default); with none named,
an environment named ``production`` or ``prod`` is production. Names match without regard to
case on both providers: GitHub's environment names are case-insensitive, and on GitLab, where
they are not, ``Production`` and ``production`` both count, so a renamed copy is never missed.
``configured`` says which rule applied.

GitHub. ``repos/<slug>/environments`` (read access suffices), ``PAGE`` (100) environments a page
until a short page, at most ``MAX_PAGES`` (10) pages; a list still full after them is the
``truncated`` unavailable block, never a partial count. A production environment is unreviewed
when it has no ``required_reviewers`` protection rule, and allows self-review when that rule's
``prevent_self_review`` is false; a rule that does not report the flag makes
``self_review_allowed_count`` null with ``not_reported``.

GitLab. ``projects/<id>/environments`` gives the names (the same paging). With no production
environment no other read is made. Otherwise ``projects/<id>/protected_environments`` (a
Premium feature): a production environment is reviewed when its protected entry has an
approval rule with ``required_approvals`` of 1 or more, or the older
``required_approval_count`` of 1 or more. Self-review is the project's
``allow_pipeline_trigger_approve_deployment`` (the person who ran the pipeline may approve
its deployment): true counts every reviewed production environment; with none reviewed the
count is 0; absent from the project read, or that read refused, is null (``not_reported``, or
the refusal's code). A refused protected-environment read (``no_access`` or ``not_found``: a
tier or token that cannot read it, HTTP 403 or 404) leaves ``production_environments`` measured
and makes both other counts null with that code as their reason; it is never read as
unreviewed. Any other failure of that read (rate limit, timeout, bad JSON, truncation) makes
the whole block unavailable, as on the environment list.
Group-level protected environments are not read.

Block (``deploy_controls`` in run-context.json)::

    {"available": True, "provider": "github" | "gitlab", "configured": bool,
     "production_environments": int,
     "unreviewed_count": int | None, "unreviewed_count_reason": str,        # reason only when null
     "self_review_allowed_count": int | None, "self_review_allowed_count_reason": str}

No production environment reads ``production_environments: 0`` with both counts 0, which a
reader takes as not applicable, never unmet. No remote, no sign-in, a refused or malformed
environment list and a truncated list are the ``gh_cli.unavailable`` block. Counts, flags and
reason codes only: no environment name or reviewer reaches the block. No maturity-model row
reads it yet. Tests: ``tests/test_deploy_controls.py``.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any, Literal, TypedDict

from lib.assess_config import load_config
from lib.hosting.gh_cli import GhUnavailable, gh_api, open_github, unavailable
from lib.hosting.gitlab_cli import GitlabProject, glab_api, open_gitlab

DEFAULT_NAMES = frozenset({"production", "prod"})
PAGE = 100
MAX_PAGES = 10
NOT_REPORTED = "not_reported"
# The protected-environment refusals a tier or token gives; any other failure is the whole block's.
REFUSED = frozenset({"no_access", "not_found"})


class DeployControlsBlock(TypedDict, total=False):
    """``run-context.json`` ``deploy_controls`` (see the module doc)."""

    available: bool
    reason: str
    provider: Literal["github", "gitlab"]
    configured: bool
    production_environments: int
    unreviewed_count: int | None
    unreviewed_count_reason: str
    self_review_allowed_count: int | None
    self_review_allowed_count_reason: str


def production_names(repo_root: Path) -> tuple[frozenset[str], bool]:
    """``[change_controls] environments`` casefolded, and True, when it lists a non-blank
    name; else the default names and False."""
    section = load_config(repo_root).get("change_controls")
    raw = section.get("environments") if isinstance(section, dict) else None
    names = frozenset(n.strip().casefold() for n in raw if isinstance(n, str) and n.strip()) \
        if isinstance(raw, list) else frozenset()
    return (names, True) if names else (DEFAULT_NAMES, False)


def _paged(read: Callable[[int], list[Any]], what: str) -> list[dict[str, Any]]:
    """Every entry of a paged list; ``truncated`` when still full after ``MAX_PAGES``."""
    out: list[dict[str, Any]] = []
    for page in range(1, MAX_PAGES + 1):
        got = read(page)
        out += [e for e in got if isinstance(e, dict)]
        if len(got) < PAGE:
            return out
    raise GhUnavailable(f"truncated: the {what} list is still full after {len(out)} entries "
                        f"({MAX_PAGES} pages)")


def _named(entries: Iterable[dict[str, Any]], names: frozenset[str]) -> list[dict[str, Any]]:
    return [e for e in entries if isinstance(e.get("name"), str) and e["name"].casefold() in names]


def _block(provider: Literal["github", "gitlab"], configured: bool, production: int,
           unreviewed: int | None, self_review: int | None,
           unreviewed_reason: str = "", self_review_reason: str = "") -> DeployControlsBlock:
    block: DeployControlsBlock = {
        "available": True, "provider": provider, "configured": configured,
        "production_environments": production,
        "unreviewed_count": unreviewed, "self_review_allowed_count": self_review}
    if unreviewed is None:
        block["unreviewed_count_reason"] = unreviewed_reason
    if self_review is None:
        block["self_review_allowed_count_reason"] = self_review_reason
    return block


def _github(repo_root: Path, names: frozenset[str], configured: bool) -> DeployControlsBlock:
    repo = open_github(repo_root)

    def read(page: int) -> list[Any]:
        got = gh_api(f"repos/{repo.slug}/environments?per_page={PAGE}&page={page}")
        envs = got.get("environments") if isinstance(got, dict) else None
        if not isinstance(envs, list):
            raise GhUnavailable("gh_bad_json: the environment list was not a list")
        return envs
    production = _named(_paged(read, "environment"), names)
    rules = [next((r for r in (e.get("protection_rules") or []) if isinstance(r, dict)
                   and r.get("type") == "required_reviewers"), None) for e in production]
    reviewed = [r for r in rules if r is not None]
    flags = [r.get("prevent_self_review") for r in reviewed]
    self_review = (None if any(not isinstance(f, bool) for f in flags)
                   else sum(1 for f in flags if f is False))
    return _block("github", configured, len(production), len(production) - len(reviewed),
                  self_review, self_review_reason=NOT_REPORTED)


def _approvals(entry: dict[str, Any]) -> bool:
    rules = entry.get("approval_rules")
    by_rule = any(isinstance(r, dict) and isinstance(r.get("required_approvals"), int)
                  and r["required_approvals"] >= 1 for r in (rules if isinstance(rules, list) else []))
    legacy = entry.get("required_approval_count")
    return by_rule or (isinstance(legacy, int) and legacy >= 1)


def _gitlab(project: GitlabProject, names: frozenset[str], configured: bool) -> DeployControlsBlock:
    def listing(path: str) -> Callable[[int], list[Any]]:
        def read(page: int) -> list[Any]:
            got = glab_api(project, f"projects/{project.encoded}/{path}?per_page={PAGE}&page={page}")
            if not isinstance(got, list):
                raise GhUnavailable(f"glab_bad_json: the {path} list was not a list")
            return got
        return read
    production = _named(_paged(listing("environments"), "environment"), names)
    if not production:
        return _block("gitlab", configured, 0, 0, 0)
    try:
        protected = _named(_paged(listing("protected_environments"), "protected environment"), names)
    except GhUnavailable as e:
        code = e.reason.partition(":")[0].strip()
        if code not in REFUSED:
            raise
        return _block("gitlab", configured, len(production), None, None, code, code)
    approved = {p["name"].casefold() for p in protected if _approvals(p)}
    reviewed = sum(1 for e in production if e["name"].casefold() in approved)
    reason = NOT_REPORTED
    try:
        meta = glab_api(project, f"projects/{project.encoded}")
    except GhUnavailable as e:
        meta, reason = None, e.reason.partition(":")[0].strip()
    flag = meta.get("allow_pipeline_trigger_approve_deployment") if isinstance(meta, dict) else None
    self_review: int | None = None
    if reviewed == 0:  # the count covers reviewed environments only
        self_review = 0
    elif isinstance(flag, bool):
        self_review = reviewed if flag else 0
    return _block("gitlab", configured, len(production), len(production) - reviewed,
                  self_review, self_review_reason=reason)


def scan_deploy_controls(repo_root: Path) -> DeployControlsBlock | dict[str, Any]:
    """Build the ``deploy_controls`` block (see the module doc)."""
    names, configured = production_names(repo_root)
    try:
        project = open_gitlab(repo_root)
        if project is not None:
            return _gitlab(project, names, configured)
        return _github(repo_root, names, configured)
    except GhUnavailable as e:
        return unavailable(e.reason)
