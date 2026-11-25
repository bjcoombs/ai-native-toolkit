"""CI the scanner cannot read: unread CI paths, off-repository CI and the Prow marker (#928)."""
from __future__ import annotations

import json
import os
import stat
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from assess_test_support import skip_without_posix_shell
from lib import external_ci as ext
from lib.ci.ci_checks import scan_ci_checks
from lib.testing.coverage_gate import detect_coverage_gate
from lib.external_ci_rules import NOT_READ, ExternalCi, as_external, known_app, known_context
from lib.hosting.gh_cli import GhUnavailable, GithubRepo
from lib.hosting.merged_sample import MergedSample
from lib.hosting.platform_cli import CACHE_ENV
from lib.signals import collect
from lib.unread_ci import UNREAD_CI_FILES, is_unread_ci_path, unread_ci_paths

AZP = "trigger:\n  - main\nsteps:\n  - script: make test\n"
LUCI = ("enabled_branches:\n  - main\ntargets:\n  - name: Linux analyze\n"
        "    recipe: packages/packages\n")
CIWF = "on: [pull_request]\njobs:\n  t:\n    runs-on: x\n    steps:\n      - run: pytest\n"
TOOLS = "on: [pull_request]\njobs:\n  t:\n    runs-on: x\n    steps:\n      - run: echo tools\n"
OWNERS = {"OWNERS": "approvers:\n  - a\n", "OWNERS_ALIASES": "aliases:\n  team: [a]\n"}

# The accepted static list (#928 decision 3), one file per path or pattern.
STATIC = [".prow.yaml", ".zuul.yaml", "zuul.d/jobs.yaml", ".drone.yml", ".woodpecker.yml",
          ".woodpecker/ci.yml", ".cirrus.yml", "appveyor.yml", ".semaphore/semaphore.yml",
          "cloudbuild.yaml", "bitrise.yml", "codemagic.yaml", "azure-pipelines.yml",
          "azure-pipelines.yaml", ".azure/pipelines/ci-public.yml", "eng/pipelines/ci-public.yml",
          ".azure-pipelines.yml", ".azure-pipelines.yaml", ".azure-pipelines/ci.yml",
          "azure-pipelines/ci.yml", "infra/config/main.star", "infra/config/generated/luci.cfg",
          ".ci.yaml", ".azure/pipelines/ci.yml", "eng/pipelines/ci.yml"]


def _write(root: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


def _states(block: Any) -> tuple[Any, ...]:
    return (block["enforced_check_runs"], block["enforced_basis"], block["lint_runs"],
            block["lint_basis"], block["basis"])


@pytest.mark.parametrize("rel", STATIC)
def test_every_static_path_reads_not_measured(tmp_path: Path, rel: str) -> None:
    block = scan_ci_checks(_write(tmp_path, {rel: LUCI if rel == ".ci.yaml" else AZP}))
    assert _states(block) == (None, "unread_ci_system", None, "unread_ci_system",
                              "unread_ci_system")
    assert block["unread_ci_files"] == 1
    assert block["test_or_lint_configured"] is False


@pytest.mark.parametrize("rel", [
    "tools/.ci.yaml", "infra/config/README.md", "infra/configs/main.star", "eng/pipelines/notes.md",
    ".woodpecker/README.md", "docs/azure-pipelines.yml", "pkg/zuul.d/jobs.yaml", "infra/config/",
])
def test_a_near_miss_path_is_not_an_unread_ci_file(tmp_path: Path, rel: str) -> None:
    assert not is_unread_ci_path(rel)
    if not rel.endswith("/"):
        assert _states(scan_ci_checks(_write(tmp_path, {rel: AZP})))[:2] == (False, "no_ci")


def test_the_exact_list_keeps_the_v787_files() -> None:
    assert {"Jenkinsfile", ".circleci/config.yml", "bitbucket-pipelines.yml", ".travis.yml",
            ".buildkite/pipeline.yml", "buildkite.yaml"} <= UNREAD_CI_FILES
    assert unread_ci_paths(["b.py", "eng/pipelines/a.yml", "Jenkinsfile"]) == [
        "Jenkinsfile", "eng/pipelines/a.yml"]


def test_a_read_workflow_wins_over_an_unread_file(tmp_path: Path) -> None:
    block = scan_ci_checks(_write(tmp_path, {".ci.yaml": LUCI, ".github/workflows/ci.yml": CIWF}))
    assert (block["enforced_check_runs"], block["enforced_basis"]) == (True, "enforced_check")


def test_the_coverage_gate_shares_the_unread_paths(tmp_path: Path) -> None:
    files = {".azure/pipelines/ci.yml": AZP,
             "pyproject.toml": "[tool.coverage.report]\nfail_under = 80\n"}
    block = detect_coverage_gate(_write(tmp_path, files))
    assert (block["enforced_meets_minimum"], block["enforced_basis"]) == (None, "unread_ci_system")


# ---------- the offline Prow marker ----------

@pytest.mark.parametrize(("files", "expected"), [
    (OWNERS, (None, "external_ci_marker", None, "external_ci_marker", "external_ci_marker")),
    ({**OWNERS, ".github/workflows/ci.yml": CIWF},
     (True, "enforced_check", False, "no_lint_command", "check_command")),
    ({**OWNERS, ".github/workflows/tools.yml": TOOLS},
     (False, "no_check_command", False, "no_lint_command", "no_check_command")),
    ({**OWNERS, ".ci.yaml": LUCI},
     (None, "unread_ci_system", None, "unread_ci_system", "unread_ci_system")),
    ({**OWNERS, ".gitlab-ci.yml": "deploy:\n  script:\n    - make deploy\n"},
     (False, "no_check_command", False, "no_lint_command", "no_check_command")),
    ({"OWNERS": OWNERS["OWNERS"]}, (False, "no_ci", False, "no_ci", "no_ci")),
    ({"test/env_test.rb": 'skip unless ENV["BUILDKITE"]\n'}, (False, "no_ci", False, "no_ci", "no_ci")),
    ({"pkg/OWNERS_ALIASES": OWNERS["OWNERS_ALIASES"]}, (False, "no_ci", False, "no_ci", "no_ci")),
], ids=["prow", "prow-wf", "prow-tools", "prow-unread", "prow-gitlab", "owners-only", "env-mark",
        "nested-aliases"])
def test_the_prow_marker_applies_only_with_no_ci_file(
        tmp_path: Path, files: dict[str, str], expected: tuple[Any, ...]) -> None:
    assert _states(scan_ci_checks(_write(tmp_path, files))) == expected


def test_a_plain_repository_with_no_ci_still_reads_false(tmp_path: Path) -> None:
    """No CI is not unknown (I928-2)."""
    block = scan_ci_checks(_write(tmp_path, {"README.md": "x\n"}))
    assert _states(block) == (False, "no_ci", False, "no_ci", "no_ci")


# ---------- external CI from platform evidence ----------

def test_the_majority_rule() -> None:
    assert ExternalCi(3, 2).applies and ExternalCi(3, 3).applies
    assert not ExternalCi(3, 1).applies and not ExternalCi(2, 1).applies
    assert not ExternalCi(0, 0).applies and not NOT_READ.applies
    assert as_external({"available": False, "reason": "x"}) is NOT_READ
    assert as_external(None) is NOT_READ


@pytest.mark.parametrize(("slug", "known"), [
    ("buildkite", True), ("Azure-Pipelines", True), ("travis-ci", True), ("cirrus-ci", True),
    ("github-actions", False), ("codecov", False), ("coderabbitai", False), (None, False),
])
def test_the_app_allowlist(slug: object, known: bool) -> None:
    assert known_app(slug) is known


@pytest.mark.parametrize(("context", "known"), [
    ("buildkite/rails", True), ("buildkite", True), ("tide", True), ("ci/circleci: test", True),
    ("continuous-integration/travis-ci/pr", True), ("Azure Pipelines", True),
    ("luci-flutter", True), ("codecov/patch", False), ("EasyCLA", False), ("cla/google", False),
    ("tidelift", False), ("Tidelift Alignment", False), ("lucidchart", False),
    ("buildkitex", False), ("jenkinsfile-lint", False), ("jenkins/pr-head", True),
    ("", False), (3, False),
])
def test_the_context_allowlist(context: object, known: bool) -> None:
    assert known_context(context) is known


def test_external_ci_replaces_only_the_false_cases(tmp_path: Path) -> None:
    yes, no = ExternalCi(3, 3), ExternalCi(3, 1)
    tools = _write(tmp_path / "tools", {".github/workflows/tools.yml": TOOLS})
    assert _states(scan_ci_checks(tools, external_ci=yes)) == (
        None, "external_ci", None, "external_ci", "external_ci")
    assert _states(scan_ci_checks(tools, external_ci=no))[:2] == (False, "no_check_command")
    assert _states(scan_ci_checks(tools))[:2] == (False, "no_check_command")
    # Never credit, never over a read check: an enforced check still reads true.
    wf = _write(tmp_path / "wf", {".github/workflows/ci.yml": CIWF})
    assert _states(scan_ci_checks(wf, external_ci=yes))[:2] == (True, "enforced_check")
    # A masked check reads external_ci in place of not_enforced.
    masked = CIWF.replace("pytest", "pytest || true")
    m = _write(tmp_path / "masked", {".github/workflows/ci.yml": masked})
    assert _states(scan_ci_checks(m, external_ci=yes))[:2] == (None, "external_ci")
    # Platform evidence comes before the offline marker.
    prow = _write(tmp_path / "prow", OWNERS)
    assert _states(scan_ci_checks(prow, external_ci=yes))[:2] == (None, "external_ci")
    none = _write(tmp_path / "none", {"README.md": "x\n"})
    assert _states(scan_ci_checks(none, external_ci=yes))[:2] == (None, "external_ci")


REPO = GithubRepo("o", "ext")


def _sample(heads: list[str | None], provider: str = "github") -> MergedSample:
    changes = tuple({"mergedAt": "2026-11-21T00:00:00Z", **({"headRefOid": h} if h else {})}
                    for h in heads)
    if provider == "gitlab":
        return MergedSample("gitlab", changes, None)
    return MergedSample("github", changes, None, github=REPO)


def _fake(monkeypatch: pytest.MonkeyPatch, answers: dict[str, Any]) -> list[tuple[list[str], bool]]:
    calls: list[tuple[list[str], bool]] = []

    def fake(args: list[str], timeout: int = 20, cache: bool = True) -> Any:
        calls.append((args, cache))
        key = args[-1].rsplit("/", 2)[-2] + "/" + args[-1].rsplit("/", 1)[-1]
        got = answers.get(key)
        if isinstance(got, Exception):
            raise got
        return got if got is not None else {}

    monkeypatch.setattr(ext, "gh_json", fake)
    return calls


BK_RUNS = {"check_runs": [{"app": {"slug": "buildkite"}, "name": "ext"}]}
GHA_RUNS = {"check_runs": [{"app": {"slug": "github-actions"}, "name": "t"}]}
BK_STATUS = {"statuses": [{"context": "buildkite/ext"}]}


def test_read_external_ci_counts_known_systems(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _fake(monkeypatch, {"H1/check-runs": BK_RUNS, "H2/check-runs": GHA_RUNS,
                                "H2/status": BK_STATUS, "H3/check-runs": GHA_RUNS,
                                "H3/status": {"statuses": []}})
    got = ext.read_external_ci(_sample(["H1", "H2", "H3", None]))
    assert got == ExternalCi(4, 2) and not got.applies
    assert [c[0][1] for c in calls] == ["repos/o/ext/commits/H1/check-runs",
                                        "repos/o/ext/commits/H2/check-runs",
                                        "repos/o/ext/commits/H2/status",
                                        "repos/o/ext/commits/H3/check-runs",
                                        "repos/o/ext/commits/H3/status"]
    assert all(args[0] == "api" and not cache for args, cache in calls)
    assert len(calls) <= ext.CALLS_PER_CHANGE * 4


def test_a_missing_head_counts_as_not_external(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake(monkeypatch, {"H1/check-runs": GhUnavailable("not_found: gone"),
                        "H2/check-runs": BK_RUNS, "H3/check-runs": BK_RUNS})
    assert ext.read_external_ci(_sample(["H1", "H2", "H3"])) == ExternalCi(3, 2)


def test_a_refused_read_or_no_github_sample_reads_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _fake(monkeypatch, {"H1/check-runs": GhUnavailable("no_access: refused")})
    assert ext.read_external_ci(_sample(["H1", "H2"])) is NOT_READ
    assert ext.read_external_ci(_sample(["H1"], provider="gitlab")) is NOT_READ
    assert ext.read_external_ci({"available": False, "reason": "network"}) is NOT_READ
    assert ext.no_external_ci(_sample(["H1"])) is NOT_READ
    assert len(calls) == 1


# ---------- end to end: the headless runner with a stub gh ----------

FAKE_GH = """#!/bin/sh
echo "$*" >> "$FAKE_GH/log"
serve() { [ -f "$FAKE_GH/$1" ] && cat "$FAKE_GH/$1" && exit 0; }
case "$1:$*" in
  auth:*) exit 0 ;;
  pr:*) serve prs.json ;;
  api:*/check-runs*) serve "runs-$(echo "$2" | sed 's|.*/commits/||; s|/.*||')" ;;
  api:*/status*) serve "status-$(echo "$2" | sed 's|.*/commits/||; s|/.*||')" ;;
esac
echo "gh: Not Found (HTTP 404)" >&2
exit 1
"""


def _git(repo: Path, *args: str) -> None:
    env = {**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null"}
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, env=env)


@pytest.fixture
def stub(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    skip_without_posix_shell()
    bindir, ghdir = tmp_path / "bin", tmp_path / "gh"
    bindir.mkdir()
    ghdir.mkdir()
    gh = bindir / "gh"
    gh.write_text(FAKE_GH, encoding="utf-8")
    gh.chmod(gh.stat().st_mode | stat.S_IEXEC)
    (ghdir / "log").write_text("", encoding="utf-8")
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_GH", str(ghdir))
    monkeypatch.delenv(CACHE_ENV, raising=False)
    merged = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%dT00:00:00Z")
    prs = [{"number": n, "mergedAt": merged, "createdAt": merged, "headRefOid": f"H{n}",
            "author": {"login": "a"}, "mergedBy": {"login": "a"}, "reviews": [],
            "comments": [], "reviewDecision": ""} for n in (1, 2, 3)]
    (ghdir / "prs.json").write_text(json.dumps(prs), encoding="utf-8")
    return ghdir


def _repo(root: Path, files: dict[str, str]) -> Path:
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _git(root, "remote", "add", "origin", "https://github.com/o/ext.git")
    _write(root, files)
    _git(root, "add", "-A")
    _git(root, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-qm", "x")
    return root


def _serve(ghdir: Path, slug: str, status: list[str]) -> None:
    for n in (1, 2, 3):
        runs = {"check_runs": [{"app": {"slug": slug}}]}
        (ghdir / f"runs-H{n}").write_text(json.dumps(runs), encoding="utf-8")
        statuses = {"statuses": [{"context": c} for c in status]}
        (ghdir / f"status-H{n}").write_text(json.dumps(statuses), encoding="utf-8")


def _platform_ci(repo: Path, platform: bool) -> Any:
    return collect(repo, ["ci_checks"], window="6mo", platform=platform)["ci_checks"]


def test_ext_reads_external_ci_under_platform_only(stub: Path, tmp_path: Path) -> None:
    _serve(stub, "buildkite", ["buildkite/ext"])
    repo = _repo(tmp_path / "ext", {".github/workflows/tools.yml": TOOLS, "CLAUDE.md": "x\n"})
    block = _platform_ci(repo, platform=True)
    assert (block["enforced_check_runs"], block["enforced_basis"]) == (None, "external_ci")
    log = (stub / "log").read_text(encoding="utf-8").splitlines()
    assert not [line for line in log if "-X" in line.split() or "--method" in line.split()]
    for n in (1, 2, 3):
        assert sum(f"/commits/H{n}/" in line for line in log) <= ext.CALLS_PER_CHANGE
    offline = _platform_ci(repo, platform=False)
    assert (offline["enforced_check_runs"], offline["enforced_basis"]) == (False, "no_check_command")


def test_ext2_with_github_actions_only_reads_false(stub: Path, tmp_path: Path) -> None:
    _serve(stub, "github-actions", [])
    repo = _repo(tmp_path / "ext2", {".github/workflows/tools.yml": TOOLS})
    block = _platform_ci(repo, platform=True)
    assert (block["enforced_check_runs"], block["enforced_basis"]) == (False, "no_check_command")


def test_non_ci_apps_read_false(stub: Path, tmp_path: Path) -> None:
    _serve(stub, "codecov", ["coderabbitai"])
    repo = _repo(tmp_path / "nonci", {".github/workflows/tools.yml": TOOLS})
    assert _platform_ci(repo, platform=True)["enforced_basis"] == "no_check_command"


def test_prow_under_platform_reads_external_ci(stub: Path, tmp_path: Path) -> None:
    _serve(stub, "buildkite", [])
    repo = _repo(tmp_path / "prow", OWNERS)
    assert _platform_ci(repo, platform=True)["enforced_basis"] == "external_ci"
    assert _platform_ci(repo, platform=False)["enforced_basis"] == "external_ci_marker"
