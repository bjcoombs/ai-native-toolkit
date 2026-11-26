"""GitLab's merged CI configuration, read through the CI lint API (issue #796, #766 part 2).

``lib/ci/ci_includes.py`` resolves a ``.gitlab-ci.yml`` include offline when the
included file is cloned in the ghsync root. An include it cannot read (a
template project that is not cloned, a ``template:`` or ``remote:`` file)
leaves ``ci_checks.test_or_lint_configured`` null. This module asks GitLab for the
merged configuration of exactly those repositories, so ``ci_checks`` and
``review_automation`` see the included jobs.

It is the private ``network`` registry spec ``gitlab_merged_ci``. The headless
runner (``lib/run/signals.py``) calls it only under ``--platform``, because
``ci_checks`` and ``review_automation`` are in ``signals.PLATFORM_SCANS``;
offline it calls :func:`no_merged_ci`, which reads nothing. ``fleet --platform``
runs it in its separate platform pass. ``assess_core`` ignores the ``network``
flag (``lib/run/scan_registry.py``), so a full ``/assess`` run reads it too, as it
reads ``review_reality``.

Rules, in order:

- **A template already resolved offline wins.** The API is asked only when
  ``resolve_includes`` left at least one include unresolved
  (``not_needed`` otherwise; ``not_read`` when there is no GitLab CI file or it
  has no ``include:``). ``ci_checks`` adds what the merged configuration
  says to its offline reading; it never takes away what the offline reading found.
- **Only a GitLab remote.** ``gitlab_cli.open_gitlab`` decides; a repository
  without one is ``unavailable`` with reason ``not_gitlab``.
- **The argv names its target.** One call:
  ``glab api --hostname <host> projects/<encoded path>/ci/lint?content_ref=<HEAD sha>``.
  It is a GET: the repository's files are never uploaded. ``content_ref`` pins
  the commit the clone holds, so the merged result describes the same commit the
  offline scans read. A commit the remote does not hold is refused, and the block
  stays unknown.
- **The cache never holds free text** (#778): the call is made with
  ``cache=False``. The merged YAML, the ``errors`` and the ``warnings`` stay
  in memory and are never written anywhere. ``ci_checks`` publishes a count and
  a status token, and ``review_automation`` publishes a match per review
  component.

A response with ``valid`` true and a ``merged_yaml`` string is ``read``. A
response that GitLab marks invalid, or that is not that shape, is ``invalid``.
A refused call is ``unavailable`` with the ``GhUnavailable`` reason code
(``no_access``, ``not_found``, ``not_authenticated``, ``rate_limited``, ...).
The response's ``includes`` list, where the host sends one, gives the sources
the merge pulled in: a ``component`` item's ``location``, or a ``file`` item's
``extra.project``. ``review_automation`` matches these the way it matches an
``include:`` entry.

GitLab's merged CI configuration through the CI lint API (#796, #766 part 2): the private
`network` spec `gitlab_merged_ci` (`read_merged_ci(repo_root, ghsync_root)`; offline the
runner calls `no_merged_ci`, which reads nothing). Asked only when
`ci_includes.resolve_includes` left an include unresolved (`not_needed` otherwise: a
template resolved offline wins) and `gitlab_cli.open_gitlab` finds a GitLab remote
(`not_gitlab` otherwise). One call, naming its target:
`glab api --hostname <host> projects/<encoded>/ci/lint?content_ref=<HEAD sha>`, a GET (no
file content uploaded), made with `cache=False` so the merged YAML never reaches the
platform response cache (#778). `valid` true with a `merged_yaml` string is `read`; any
other shape is `invalid`; a refusal is `unavailable` with the `GhUnavailable` reason code.
`include_sources(includes)` reads the response's `includes` list (a `component` item's
`location`, a `file` item's `extra.project`) for `review_automation`. Errors and warnings
are never kept. Tests: `tests/test_gitlab_ci_lint.py`, `tests/test_platform_argv_contract.py`.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from lib.ci.ci_includes import resolve_includes
from lib.counts_only.content_free import reason_code
from lib.hosting.gh_cli import GhUnavailable
from lib.hosting.gitlab_cli import glab_api, open_gitlab
from lib.ci.merged_ci import NOT_NEEDED, NOT_READ, MergedCi


def no_merged_ci(_repo_root: Path, _ghsync_root: Path | None = None) -> MergedCi:
    """The offline reading: nothing asked."""
    return NOT_READ


def _head(repo_root: Path) -> str | None:
    try:
        out = subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "--verify", "--quiet", "HEAD^{commit}"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", check=True,
            timeout=30)
    except (subprocess.CalledProcessError, OSError, subprocess.TimeoutExpired):
        return None
    sha = out.stdout.strip()
    return sha if len(sha) in (40, 64) and all(c in "0123456789abcdef" for c in sha) else None


def include_sources(includes: object) -> tuple[str, ...]:
    """The sources in a lint response's ``includes`` list: a component's ``location``, a
    project file's ``extra.project``; stripped, first appearance kept (#958)."""
    out: list[str] = []
    for item in includes if isinstance(includes, list) else ():
        if not isinstance(item, dict):
            continue
        source: Any = None
        if item.get("type") == "component":
            source = item.get("location")
        elif item.get("type") == "file":
            extra = item.get("extra")
            source = extra.get("project") if isinstance(extra, dict) else None
        if isinstance(source, str) and source.strip() and source.strip() not in out:
            out.append(source.strip())
    return tuple(out)


def read_merged_ci(repo_root: Path, ghsync_root: Path | None = None) -> MergedCi:
    """The merged configuration of ``repo_root`` when an include did not resolve offline."""
    root = repo_root.resolve()
    resolution = resolve_includes(root, ghsync_root)
    if not resolution.includes:
        return NOT_READ  # no GitLab CI file, or one with no include: nothing to merge
    if resolution.unresolved == 0:
        return NOT_NEEDED
    try:
        project = open_gitlab(root)
        if project is None:
            return MergedCi("unavailable", "not_gitlab")
        sha = _head(root)
        if sha is None:
            return MergedCi("unavailable", "no_head")
        data = glab_api(project, f"projects/{project.encoded}/ci/lint?content_ref={sha}",
                        cache=False)
    except GhUnavailable as e:
        return MergedCi("unavailable", reason_code(e.reason))
    if not isinstance(data, dict) or data.get("valid") is not True \
            or not isinstance(data.get("merged_yaml"), str):
        return MergedCi("invalid")
    return MergedCi("read", None, data["merged_yaml"], include_sources(data.get("includes")))
