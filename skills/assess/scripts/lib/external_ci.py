"""Does CI run off-repository, on a known external CI system? (issue #928)

Some repositories run their checks on a CI system configured outside the
repository: LUCI, Prow (configured in another repository), Buildkite, Azure
Pipelines under a project setting. The files at HEAD then show no readable
check, and ``ci_checks`` would read false. Under ``--platform`` this module
reads the checks GitHub recorded on recently merged pull requests instead.

It is the private ``network`` registry spec ``external_ci``. It reads the
shared ``merged_sample`` (``lib/merged_sample.py``, up to ``SAMPLE_CAP``
merged pull requests in the run window). The headless runner calls it only
under ``--platform``, because ``ci_checks`` reads it and is in
``signals.PLATFORM_SCANS``; offline it calls :func:`no_external_ci`, which
reads nothing. A full ``/assess`` run reads it too.

Calls. For each sampled pull request with a head commit (``headRefOid``), at
most ``CALLS_PER_CHANGE`` (2) GitHub REST reads, both GET:

1. ``repos/<owner>/<repo>/commits/<head sha>/check-runs``: the ``app.slug`` of
   each check run on the first page (GitHub's default page of 30).
2. ``repos/<owner>/<repo>/commits/<head sha>/status``: the ``context`` of each
   commit status. Skipped when the check runs already name a known system.

Both are read with ``cache=False``: check-run output and status descriptions
are free text, and they stay in memory. Only two counts leave this module.

The allowlist (``lib/external_ci_rules.py``). A change carries a known external CI
system when a check run's app slug is in ``EXTERNAL_CI_APPS``, or a status context equals or starts with
an entry of ``EXTERNAL_CI_CONTEXTS`` (case-insensitive). An app or context that
is on neither list (``github-actions``, ``codecov``, ``coderabbitai``, a CLA
bot) is not CI evidence and changes nothing.

The rule. :attr:`ExternalCi.applies` is true when more than half of the sampled
changes carry a known system. ``ci_checks`` then reads ``enforced_check_runs``
and ``lint_runs`` null with basis ``external_ci`` in place of a false case
(``not_enforced``, ``no_check_command``, ``no_lint_command``, ``no_ci``). It
never gives credit: an external context shows that a check ran, not that it
ran the tests or could block the merge.

A GitLab sample, an empty sample, a sample that could not be read, or any
refused call (other than ``not_found``) returns a reading that does not apply, so
the offline result stands. A ``not_found`` head (a merged fork pull request whose
commit the remote no longer holds) counts that change as not external. GitLab external pipelines are not read.
"""
from __future__ import annotations

from typing import Any

from lib.external_ci_rules import NOT_READ, ExternalCi, known_app, known_context
from lib.gh_cli import GhUnavailable, gh_json
from lib.merged_sample import MergedSample

# Reads per sampled pull request: check runs, then commit statuses.
CALLS_PER_CHANGE = 2


def _items(got: Any, key: str) -> list[dict[str, Any]]:
    items = got.get(key) if isinstance(got, dict) else None
    return [i for i in items if isinstance(i, dict)] if isinstance(items, list) else []


def _carries_external(slug: str, sha: str) -> bool:
    runs = gh_json(["api", f"repos/{slug}/commits/{sha}/check-runs"], cache=False)
    apps = [run.get("app") for run in _items(runs, "check_runs")]
    if any(isinstance(app, dict) and known_app(app.get("slug")) for app in apps):
        return True
    status = gh_json(["api", f"repos/{slug}/commits/{sha}/status"], cache=False)
    return any(known_context(s.get("context")) for s in _items(status, "statuses"))


def read_external_ci(merged_sample: object) -> ExternalCi:
    """The reading for the sampled merged pull requests (see the module docstring)."""
    if not isinstance(merged_sample, MergedSample) or merged_sample.github is None:
        return NOT_READ
    slug = merged_sample.github.slug
    shas = [str(c["headRefOid"]) for c in merged_sample.changes
            if isinstance(c.get("headRefOid"), str) and c["headRefOid"]]
    external = 0
    for sha in shas:
        try:
            external += _carries_external(slug, sha)
        except GhUnavailable as e:
            if e.reason.startswith("not_found"):
                continue  # a head the remote no longer holds: that change is not external
            return NOT_READ  # a refused or rate-limited read leaves the offline result
    return ExternalCi(len(merged_sample.changes), external)


def no_external_ci(_merged_sample: object = None) -> ExternalCi:
    """The offline reading: nothing asked."""
    return NOT_READ
