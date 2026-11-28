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

GitLab. ``projects/<id>/environments`` gives the names and deployment tiers (the same paging).
With no production environment no other read is made. Otherwise
``projects/<id>/protected_environments`` (a Premium feature): a production environment is
reviewed when the protected entry of its exact name (GitLab protects by exact name, so only
the selection of production environments ignores case) has an approval rule with
``required_approvals`` of 1 or more, or the older ``required_approval_count`` of 1 or more.
An ancestor group's protected environments apply by deployment tier, so a production
environment no project entry approves is read against them: the project read
(``projects/<id>``) gives the namespace; a personal namespace has no group rules; for a group
namespace every ancestor's ``groups/<path>/protected_environments`` is read, and an entry
(named for a tier) with an approval rule covers every environment of that ``tier``. A namespace
the project read does not report, a refused group read, or a tier an environment does not
report (with some group rule to match) makes ``unreviewed_count`` null, never unreviewed.
Self-review is the project's ``allow_pipeline_trigger_approve_deployment`` (the person who ran
the pipeline may approve its deployment): true counts every reviewed production environment;
false, or none reviewed, is 0; absent from the project read, or that read refused, is null
(``not_reported``, or the refusal's code). A refused protected-environment read (``no_access``
or ``not_found``: a tier or token that cannot read it, HTTP 403 or 404) leaves
``production_environments`` measured and makes both other counts null with that code as their
reason. Any other failure of a GitLab read (rate limit, timeout, bad JSON, truncation) makes
the whole block unavailable, as on the environment list. An entry of any list that is not an
object with a name is bad JSON on both providers, never a dropped entry.

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
from urllib.parse import quote

from lib.assess_config import load_config
from lib.hosting.gh_cli import GhUnavailable, gh_api, open_github, unavailable
from lib.hosting.gitlab_cli import GitlabProject, glab_api, open_gitlab

DEFAULT_NAMES = frozenset({"production", "prod"})
PAGE = 100
MAX_PAGES = 10
NOT_REPORTED = "not_reported"
# The refusals a tier or token gives (HTTP 403, 404); any other failure is the whole block's.
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


def _paged(read: Callable[[int], list[Any]], what: str, bad: str) -> list[dict[str, Any]]:
    """Every entry of a paged list. ``bad`` (``gh_bad_json`` or ``glab_bad_json``) when an
    entry is not an object with a name, never a dropped entry; ``truncated`` when still full
    after ``MAX_PAGES``."""
    out: list[dict[str, Any]] = []
    for page in range(1, MAX_PAGES + 1):
        got = read(page)
        if not all(isinstance(e, dict) and isinstance(e.get("name"), str) for e in got):
            raise GhUnavailable(f"{bad}: an entry of the {what} list has no name")
        out += got
        if len(got) < PAGE:
            return out
    raise GhUnavailable(f"truncated: the {what} list is still full after {len(out)} entries "
                        f"({MAX_PAGES} pages)")


def _named(entries: Iterable[dict[str, Any]], names: frozenset[str]) -> list[dict[str, Any]]:
    return [e for e in entries if e["name"].casefold() in names]


def _code(error: GhUnavailable) -> str:
    return error.reason.partition(":")[0].strip()


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
    production = _named(_paged(read, "environment", "gh_bad_json"), names)
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


def _glab_list(project: GitlabProject, path: str, what: str) -> list[dict[str, Any]]:
    def read(page: int) -> list[Any]:
        got = glab_api(project, f"{path}?per_page={PAGE}&page={page}")
        if not isinstance(got, list):
            raise GhUnavailable(f"glab_bad_json: the {what} list was not a list")
        return got
    return _paged(read, what, "glab_bad_json")


def _group_tiers(project: GitlabProject, full_path: str) -> set[str]:
    """The deployment tiers an ancestor group's protected environments require approval for.
    Raises GhUnavailable on any failed read."""
    parts = full_path.split("/")
    tiers: set[str] = set()
    for depth in range(1, len(parts) + 1):
        group = quote("/".join(parts[:depth]), safe="")
        tiers |= {g["name"] for g in _glab_list(project, f"groups/{group}/protected_environments",
                                                "group protected environment") if _approvals(g)}
    return tiers


def _gitlab_unreviewed(project: GitlabProject, meta: dict[str, Any], pending: list[dict[str, Any]],
                       meta_reason: str) -> tuple[int | None, str]:
    """How many of ``pending`` (no approving project rule) no ancestor group's rule covers
    either, with the reason when that cannot be told."""
    if not pending:
        return 0, ""
    ns = meta.get("namespace")
    kind = ns.get("kind") if isinstance(ns, dict) else None
    full_path = ns.get("full_path") if isinstance(ns, dict) else None
    if kind == "user":
        return len(pending), ""
    if kind != "group" or not isinstance(full_path, str):
        return None, meta_reason  # an inherited group rule cannot be ruled out
    try:
        tiers = _group_tiers(project, full_path)
    except GhUnavailable as e:
        if _code(e) not in REFUSED:
            raise
        return None, _code(e)
    if not tiers:
        return len(pending), ""
    if any(not isinstance(e.get("tier"), str) for e in pending):
        return None, NOT_REPORTED
    return sum(1 for e in pending if e["tier"] not in tiers), ""


def _gitlab(project: GitlabProject, names: frozenset[str], configured: bool) -> DeployControlsBlock:
    base = f"projects/{project.encoded}"
    production = _named(_glab_list(project, f"{base}/environments", "environment"), names)
    if not production:
        return _block("gitlab", configured, 0, 0, 0)
    try:
        protected = _glab_list(project, f"{base}/protected_environments", "protected environment")
    except GhUnavailable as e:
        if _code(e) not in REFUSED:
            raise
        return _block("gitlab", configured, len(production), None, None, _code(e), _code(e))
    approved = {p["name"] for p in protected if _approvals(p)}  # GitLab protects by exact name
    meta_reason = NOT_REPORTED
    try:
        got = glab_api(project, base)
    except GhUnavailable as e:
        if _code(e) not in REFUSED:
            raise
        got, meta_reason = None, _code(e)
    meta: dict[str, Any] = got if isinstance(got, dict) else {}
    pending = [e for e in production if e["name"] not in approved]
    unreviewed, unreviewed_reason = _gitlab_unreviewed(project, meta, pending, meta_reason)
    reviewed = None if unreviewed is None else len(production) - unreviewed
    flag = meta.get("allow_pipeline_trigger_approve_deployment")
    self_review: int | None = None
    self_review_reason = meta_reason
    if reviewed == 0 or flag is False:  # the count covers reviewed environments only
        self_review = 0
    elif flag is True:
        self_review, self_review_reason = reviewed, unreviewed_reason
    return _block("gitlab", configured, len(production), unreviewed, self_review,
                  unreviewed_reason, self_review_reason)


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
