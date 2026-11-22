"""The external-CI reading and its allowlist, with no network client (issue #928).

``lib/external_ci.py`` reads the check runs and commit statuses of sampled merged
pull requests and returns an :class:`ExternalCi`; ``lib/ci_checks.py`` reads that
value through :func:`as_external`. The two live apart so the offline ``ci_checks``
scan never imports a platform client (``lib.gh_cli``).

The allowlist: a check run's app slug in ``EXTERNAL_CI_APPS``, or a commit status
context equal to an entry of ``EXTERNAL_CI_CONTEXTS`` or the entry followed by a
separator (``/``, ``:``, a space or ``-``), compared case-insensitively. Anything else (``github-actions``, ``codecov``,
``coderabbitai``, a CLA bot) is not CI evidence. :attr:`ExternalCi.applies` is the
majority rule: more than half of the sampled changes carry a known system.
"""
from __future__ import annotations

from dataclasses import dataclass

# GitHub App slugs of check runs posted by a known CI system.
EXTERNAL_CI_APPS = frozenset({
    "buildkite", "circleci-checks", "circleci", "travis-ci", "appveyor", "cirrus-ci",
    "azure-pipelines", "google-cloud-build", "semaphoreci", "semaphore-ci", "drone",
    "woodpecker-ci", "bitrise", "codemagic", "jenkins", "zuul",
    "flutter-dashboard",  # Flutter's Cocoon app, which reports its LUCI builds
})

# Commit status contexts of a known CI system: an exact name or a prefix.
EXTERNAL_CI_CONTEXTS: tuple[str, ...] = (
    "buildkite/", "ci/circleci", "continuous-integration/travis-ci", "travis-ci/",
    "continuous-integration/appveyor", "appveyor", "cirrus ci", "cirrus-ci",
    "azure pipelines", "continuous-integration/jenkins", "jenkins", "continuous-integration/drone",
    "ci/woodpecker", "ci/semaphoreci", "semaphore", "google cloud build", "ci/bitrise",
    "codemagic", "luci", "tide", "zuul",
)


@dataclass(frozen=True)
class ExternalCi:
    """``sampled`` merged changes read; ``external`` of them carry a known external CI system."""

    sampled: int = 0
    external: int = 0

    @property
    def applies(self) -> bool:
        return self.sampled > 0 and 2 * self.external > self.sampled


NOT_READ = ExternalCi()


def as_external(value: object) -> ExternalCi:
    """``value`` when it is an :class:`ExternalCi`, else ``NOT_READ`` (a degraded spec's dict)."""
    return value if isinstance(value, ExternalCi) else NOT_READ


def known_app(slug: object) -> bool:
    return isinstance(slug, str) and slug.lower() in EXTERNAL_CI_APPS


# What may follow an entry in a longer context (``buildkite/rails``, ``ci/circleci: test``,
# ``luci-flutter``): never a letter, so ``tide`` does not match ``tidelift``.
_SEPARATORS = "/: -"


def known_context(context: object) -> bool:
    """A status context equal to an entry, or an entry followed by a separator."""
    if not isinstance(context, str):
        return False
    name = context.strip().lower()
    for entry in EXTERNAL_CI_CONTEXTS:
        base = entry.rstrip("/")
        if name == base or (name.startswith(base) and name[len(base)] in _SEPARATORS):
            return True
    return False
