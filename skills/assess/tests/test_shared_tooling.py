"""Tests for the shared CI source and agent marketplace scan (issue #566)."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from lib.scan_registry import SCANS
from lib.shared_tooling import (
    distinct_ids,
    github_reusable_workflows,
    gitlab_includes,
    scan_shared_tooling,
)


def _write(repo: Path, rel: str, content: object) -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    text = content if isinstance(content, str) else json.dumps(content)
    path.write_text(text, encoding="utf-8")


# ── GitLab includes ─────────────────────────────────────────────────────────

GITLAB_CI = """\
stages: [build]
include:
  - local: '/ci/local.yml'            # not shared
  - project: 'Platform/CI-Templates'
    ref: main
    file: '/templates/build.yml'
  - component: $CI_SERVER_FQDN/platform/components/sast@1.2.0
    inputs:
      project: my-app                 # a component input, not a source
  - template: Auto-DevOps.gitlab-ci.yml
  - { project: "security/scanners", file: "scan.yml", inputs: {project: nested} }
  # - project: commented/out
build:
  script: make
deploy:
  trigger:
    project: downstream/project   # multi-project trigger, not an include
    include:
      - project: child/pipeline
"""


def test_gitlab_includes_report_project_and_component_sources() -> None:
    entries = gitlab_includes(GITLAB_CI, ".gitlab-ci.yml", tracked=True)
    assert [(e["kind"], e["source"], e["id"]) for e in entries] == [
        ("gitlab_project", "Platform/CI-Templates", "gitlab_project:platform/ci-templates"),
        ("gitlab_component", "platform/components/sast",
         "gitlab_component:platform/components/sast"),
        ("gitlab_project", "security/scanners", "gitlab_project:security/scanners"),
    ]
    assert [e["line"] for e in entries] == [4, 7, 11]


def test_gitlab_component_host_and_version_are_dropped() -> None:
    text = (
        "include:\n"
        "- component: gitlab.example.com/g/p/lint@~latest\n"
        "- component: ${CI_SERVER_FQDN}/g/p/lint@2\n"
    )
    ids = {e["id"] for e in gitlab_includes(text, ".gitlab-ci.yml", tracked=True)}
    assert ids == {"gitlab_component:g/p/lint"}


def test_gitlab_mapping_form_reads_keys_at_its_own_indentation() -> None:
    text = (
        "include:\n"
        "  project: g/p\n"
        "  file: x.yml\n"
        "  inputs:\n"
        "    component: not/a/source\n"
    )
    assert [e["source"] for e in gitlab_includes(text, "f", True)] == ["g/p"]


def test_gitlab_inline_include_and_no_include() -> None:
    inline = "include: [{project: 'a/b', file: x.yml}, {local: y.yml}]\n"
    assert [e["source"] for e in gitlab_includes(inline, "f", True)] == ["a/b"]
    assert gitlab_includes("include: '/local.yml'\n", "f", True) == []
    assert gitlab_includes("job:\n  script: echo project: x\n", "f", True) == []


# ── GitHub reusable workflows ───────────────────────────────────────────────

WORKFLOW = """\
on: push
jobs:
  shared:
    uses: Acme/ci-workflows/.github/workflows/build.yml@v3
  quoted:
    uses: "acme/ci-workflows/.github/workflows/test.yaml@main"
  local:
    uses: ./.github/workflows/local.yml
  step:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
  # uses: other/repo/.github/workflows/old.yml@v1
"""


def test_reusable_workflow_references_dedupe_on_repository() -> None:
    entries = github_reusable_workflows(WORKFLOW, ".github/workflows/ci.yml", tracked=True)
    assert [(e["source"], e["line"]) for e in entries] == [
        ("Acme/ci-workflows", 4), ("acme/ci-workflows", 6),
    ]
    assert {e["id"] for e in entries} == {"github_reusable_workflow:acme/ci-workflows"}


# ── whole-repository scan ───────────────────────────────────────────────────

def test_empty_repository(git_repo) -> None:
    repo, commit = git_repo
    _write(repo, "README.md", "hi")
    commit("init")
    assert scan_shared_tooling(repo) == {
        "available": True,
        "ci_includes": [],
        "marketplaces": [],
        "ci_source_ids": [],
        "marketplace_ids": [],
    }


def test_scan_reads_ci_files_manifest_and_settings(git_repo) -> None:
    repo, commit = git_repo
    _write(repo, ".gitlab-ci.yml", GITLAB_CI)
    _write(repo, ".github/workflows/ci.yml", WORKFLOW)
    _write(repo, ".github/workflows/notes.txt", "uses: x/y/.github/workflows/z.yml@1")
    _write(repo, ".claude-plugin/marketplace.json", {"name": "Team-Tools", "plugins": []})
    _write(repo, ".claude/settings.json", {"extraKnownMarketplaces": {
        "vendor": {"source": {"source": "git", "url": "https://git.example.com/v.git"}},
        "acme": {"source": {"source": "github", "repo": "acme/agent-marketplace"}},
    }})
    commit("add tooling")
    block = scan_shared_tooling(repo)
    assert block["ci_source_ids"] == [
        "github_reusable_workflow:acme/ci-workflows",
        "gitlab_component:platform/components/sast",
        "gitlab_project:platform/ci-templates",
        "gitlab_project:security/scanners",
    ]
    assert [(m["kind"], m["name"], m["source"]) for m in block["marketplaces"]] == [
        ("manifest", "Team-Tools", ".claude-plugin/marketplace.json"),
        ("settings", "acme", "github:acme/agent-marketplace"),
        ("settings", "vendor", "git:https://git.example.com/v.git"),
    ]
    assert block["marketplace_ids"] == [
        "marketplace:acme", "marketplace:team-tools", "marketplace:vendor",
    ]


def test_untracked_files_are_listed_but_not_credited(git_repo) -> None:
    repo, commit = git_repo
    _write(repo, "README.md", "hi")
    commit("init")
    _write(repo, ".claude/settings.json",
           {"extraKnownMarketplaces": {"local": {"source": {"source": "directory", "path": "../m"}}}})
    _write(repo, ".gitlab-ci.yml", "include:\n  - project: g/p\n")
    block = scan_shared_tooling(repo)
    assert [m["tracked"] for m in block["marketplaces"]] == [False]
    assert block["marketplaces"][0]["source"] == "directory:../m"
    assert [c["tracked"] for c in block["ci_includes"]] == [False]
    assert block["marketplace_ids"] == []
    assert block["ci_source_ids"] == []


def test_malformed_or_nameless_files_yield_no_entries(git_repo) -> None:
    repo, commit = git_repo
    _write(repo, ".claude-plugin/marketplace.json", {"plugins": []})
    _write(repo, ".claude/settings.json", "{not json")
    commit("broken")
    block = scan_shared_tooling(repo)
    assert block["marketplaces"] == []
    _write(repo, ".claude/settings.json", ["a list"])
    _write(repo, ".claude-plugin/marketplace.json", "[]")
    commit("lists")
    assert scan_shared_tooling(repo)["marketplaces"] == []


def test_settings_source_without_known_target_is_canonical_json(git_repo) -> None:
    repo, commit = git_repo
    _write(repo, ".claude/settings.json", {"extraKnownMarketplaces": {
        "odd": {"source": {"source": "npm", "registry": "r"}},
        "bare": "not-a-dict",
    }})
    commit("odd")
    sources = {m["name"]: m["source"] for m in scan_shared_tooling(repo)["marketplaces"]}
    assert sources == {"bare": "", "odd": '{"registry": "r", "source": "npm"}'}


# ── roll-up: #550 success criterion 2 ───────────────────────────────────────

def test_two_manifests_and_a_settings_entry_count_two_marketplaces(tmp_path: Path) -> None:
    """Two repositories host a marketplace; a third points at one of them."""
    blocks = []
    for name, files in (
        ("alpha-repo", {".claude-plugin/marketplace.json": {"name": "alpha", "plugins": []}}),
        ("beta-repo", {".claude-plugin/marketplace.json": {"name": "beta", "plugins": []}}),
        ("app-repo", {".claude/settings.json": {"extraKnownMarketplaces": {
            "alpha": {"source": {"source": "github", "repo": "group/alpha-repo"}},
        }}}),
    ):
        repo = tmp_path / name
        repo.mkdir()
        for rel, content in files.items():
            _write(repo, rel, content)
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
        blocks.append(scan_shared_tooling(repo))

    rollup = distinct_ids([*blocks, {"available": False, "reason": "scan failed"}])
    assert rollup["marketplaces"] == ["marketplace:alpha", "marketplace:beta"]
    assert len(rollup["marketplaces"]) == 2
    assert rollup["ci_sources"] == []


def test_scan_is_registered() -> None:
    spec = next(s for s in SCANS if s.key == "shared_tooling")
    assert spec.fn is scan_shared_tooling
    assert spec.reads == ("repo_root",)


# ── review follow-ups ───────────────────────────────────────────────────────

def test_gitlab_nested_lists_neither_add_nor_drop_sources() -> None:
    text = (
        "include:\n"
        "  - component: g/p/c@1\n"
        "    inputs:\n"
        "      targets:\n"
        "        - project: not/a/source\n"
        "  - file: x.yml\n"
        "    rules:\n"
        "      - if: $CI\n"
        "    project: after/rules\n"
        "\"include\":\n"
        "  project: quoted/key\n"
    )
    assert [e["source"] for e in gitlab_includes(text, "f", True)] == [
        "g/p/c", "after/rules", "quoted/key",
    ]


def test_gitlab_mapping_form_with_nested_list_keeps_later_keys() -> None:
    text = "include:\n  rules:\n    - if: $X\n  project: g/p\n"
    assert [e["source"] for e in gitlab_includes(text, "f", True)] == ["g/p"]


def test_reusable_workflow_only_at_job_level_and_on_next_line() -> None:
    text = (
        "jobs:\n"
        "  echo:\n"
        "    runs-on: x\n"
        "    steps:\n"
        "      - run: |\n"
        "          echo 'uses: acme/ci/.github/workflows/build.yml@v1'\n"
        "  split:\n"
        "    uses:\n"
        "      'org/shared/.github/workflows/deploy.yml@main'\n"
        "\"jobs\": {}\n"
        "other:\n"
        "  uses: not/jobs/.github/workflows/x.yml@1\n"
    )
    entries = github_reusable_workflows(text, "w.yml", True)
    assert [(e["source"], e["line"]) for e in entries] == [("org/shared", 8)]


def test_symlinks_and_paths_outside_the_repo_are_not_read(tmp_path: Path) -> None:
    outside = tmp_path / "sibling"
    _write(outside, "settings.json", {"extraKnownMarketplaces": {"x": {}}})
    _write(outside, "wf.yml", "jobs:\n  a:\n    uses: o/r/.github/workflows/w.yml@1\n")
    repo = tmp_path / "repo"
    (repo / ".claude").mkdir(parents=True)
    (repo / ".github" / "workflows").mkdir(parents=True)
    (repo / ".claude" / "settings.json").symlink_to(outside / "settings.json")
    (repo / ".github" / "workflows" / "wf.yml").symlink_to(outside / "wf.yml")
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    block = scan_shared_tooling(repo)
    assert block["marketplaces"] == [] and block["ci_includes"] == []


def test_unlistable_workflow_dir_keeps_other_evidence(git_repo, monkeypatch) -> None:
    repo, commit = git_repo
    _write(repo, ".gitlab-ci.yml", "include:\n  - project: g/p\n")
    _write(repo, ".github/workflows/ci.yml", "jobs: {}\n")
    _write(repo, ".claude-plugin/marketplace.json", {"name": "m"})
    commit("init")
    real_iterdir = Path.iterdir

    def broken(self: Path):  # type: ignore[no-untyped-def]
        if self.name == "workflows":
            raise PermissionError("denied")
        return real_iterdir(self)

    monkeypatch.setattr(Path, "iterdir", broken)
    block = scan_shared_tooling(repo)
    assert block["ci_source_ids"] == ["gitlab_project:g/p"]
    assert block["marketplace_ids"] == ["marketplace:m"]


def test_unknown_git_tracking_is_unavailable_not_empty(tmp_path: Path) -> None:
    _write(tmp_path, ".claude-plugin/marketplace.json", {"name": "m"})
    block = scan_shared_tooling(tmp_path)
    assert block["available"] is False
    assert "tracking unknown" in block["reason"]
    assert [m["tracked"] for m in block["marketplaces"]] == [False]
    assert distinct_ids([block])["marketplaces"] == []
