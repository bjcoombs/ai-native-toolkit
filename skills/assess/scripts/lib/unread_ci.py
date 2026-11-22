"""CI files of systems the scan does not parse (issues #787, #928).

``ci_checks`` and ``coverage_ci`` read GitHub Actions workflows and
``.gitlab-ci.yml`` only. A tracked file of another CI system means the
repository's checks may run where the scanner cannot see them, so a signal
that finds no readable check reads not measured (``unread_ci_system``), never
false. :func:`unread_ci_paths` is the one predicate both scans use.

Every path is relative to the repository root; a file of the same name in a
subdirectory is not the repository's pipeline.

Exact paths (``UNREAD_CI_FILES``):

- Jenkins ``Jenkinsfile``; CircleCI ``.circleci/config.yml``; Bitbucket
  ``bitbucket-pipelines.yml``; Travis CI ``.travis.yml``.
- Azure Pipelines ``azure-pipelines.yml``, ``azure-pipelines.yaml``,
  ``.azure-pipelines.yml`` and ``.azure-pipelines.yaml``.
- Buildkite ``.buildkite/pipeline.yml``, ``.buildkite/pipeline.yaml``,
  ``buildkite.yml`` and ``buildkite.yaml``.
- Flutter Cocoon / LUCI ``.ci.yaml``; Prow ``.prow.yaml``; Zuul ``.zuul.yaml``;
  Drone ``.drone.yml``; Woodpecker ``.woodpecker.yml``; Cirrus CI
  ``.cirrus.yml``; AppVeyor ``appveyor.yml``; Semaphore
  ``.semaphore/semaphore.yml``; Google Cloud Build ``cloudbuild.yaml``;
  Bitrise ``bitrise.yml``; Codemagic ``codemagic.yaml``.

Patterns (``UNREAD_CI_PATTERNS``: a directory, at any depth below it, and the
file suffixes that count there):

- LUCI project configuration: any ``*.star`` or ``*.cfg`` file under
  ``infra/config/``.
- Azure Pipelines: any ``*.yml`` or ``*.yaml`` file under ``.azure/pipelines/``,
  ``eng/pipelines/`` (the .NET Arcade layout), ``.azure-pipelines/`` or
  ``azure-pipelines/``.
- Woodpecker: any ``*.yml`` or ``*.yaml`` file under ``.woodpecker/``.
- Zuul: any file under ``zuul.d/``.

The files are counted, never parsed: ``ci_checks.unread_ci_files`` is the
number of tracked paths that match, and no path is published.

The unread-CI paths (issues #787, #928): CI files of systems `ci_checks` and `coverage_ci`
do not parse, shared by both through `unread_ci_paths(tracked)` / `is_unread_ci_path(rel)`.
Every path is relative to the repository root; the same name in a subdirectory is not
the repository's pipeline. The unread-CI path list:

- Exact paths (`UNREAD_CI_FILES`): Jenkins `Jenkinsfile`; CircleCI `.circleci/config.yml`;
  Bitbucket `bitbucket-pipelines.yml`; Travis CI `.travis.yml`; Azure Pipelines
  `azure-pipelines.yml`, `azure-pipelines.yaml`, `.azure-pipelines.yml` and
  `.azure-pipelines.yaml`; Buildkite `.buildkite/pipeline.yml`, `.buildkite/pipeline.yaml`,
  `buildkite.yml` and `buildkite.yaml`; Flutter Cocoon / LUCI `.ci.yaml`; Prow
  `.prow.yaml`; Zuul `.zuul.yaml`; Drone `.drone.yml`; Woodpecker `.woodpecker.yml`;
  Cirrus CI `.cirrus.yml`; AppVeyor `appveyor.yml`; Semaphore `.semaphore/semaphore.yml`;
  Google Cloud Build `cloudbuild.yaml`; Bitrise `bitrise.yml`; Codemagic `codemagic.yaml`.
- Patterns (`UNREAD_CI_PATTERNS`, any depth below the directory): LUCI project
  configuration `infra/config/**/*.star` and `infra/config/**/*.cfg`; Azure Pipelines
  `.azure/pipelines/**/*.yml|yaml`, `eng/pipelines/**/*.yml|yaml` (the .NET Arcade
  layout), `.azure-pipelines/**/*.yml|yaml` and `azure-pipelines/**/*.yml|yaml`;
  Woodpecker `.woodpecker/**/*.yml|yaml`; Zuul any file under `zuul.d/`.

The files are counted, never parsed or published. Tests: `tests/test_external_ci.py`.
"""
from __future__ import annotations

from collections.abc import Iterable

UNREAD_CI_FILES = frozenset({
    "Jenkinsfile", ".circleci/config.yml", "bitbucket-pipelines.yml", ".travis.yml",
    "azure-pipelines.yml", "azure-pipelines.yaml", ".azure-pipelines.yml", ".azure-pipelines.yaml",
    ".buildkite/pipeline.yml", ".buildkite/pipeline.yaml", "buildkite.yml", "buildkite.yaml",
    ".ci.yaml", ".prow.yaml", ".zuul.yaml", ".drone.yml", ".woodpecker.yml", ".cirrus.yml",
    "appveyor.yml", ".semaphore/semaphore.yml", "cloudbuild.yaml", "bitrise.yml", "codemagic.yaml",
})

_YAML = (".yml", ".yaml")

# (directory prefix, suffixes that count under it; empty means any file).
UNREAD_CI_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("infra/config/", (".star", ".cfg")),
    (".azure/pipelines/", _YAML),
    ("eng/pipelines/", _YAML),
    (".azure-pipelines/", _YAML),
    ("azure-pipelines/", _YAML),
    (".woodpecker/", _YAML),
    ("zuul.d/", ()),
)


def is_unread_ci_path(rel: str) -> bool:
    """True when a root-relative POSIX path is a CI file of a system the scan does not parse."""
    if rel in UNREAD_CI_FILES:
        return True
    return any(rel.startswith(prefix) and len(rel) > len(prefix)
               and (not suffixes or rel.endswith(suffixes))
               for prefix, suffixes in UNREAD_CI_PATTERNS)


def unread_ci_paths(tracked: Iterable[str]) -> list[str]:
    """The tracked paths that are unread CI files, sorted."""
    return sorted(rel for rel in tracked if is_unread_ci_path(rel))
