"""lib/contributor_files.py, lib/hosting/org_security_policy.py and the contributor-files condition (#1045)."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import lib.hosting.org_security_policy as osp
import lib.level_tally as lt
import lib.manifest.check_manifest as cm
from lib.contributor_files import scan_contributor_files
from lib.hosting.gh_cli import GhUnavailable, GithubRepo

CID = "contributor-files.1"
# The maintainer's approved why on #1045, byte for byte.
WHY = ("A contributing guide tells a newcomer, human or agent, how to build, test and shape a change the "
       "way the project merges it, and a security policy gives them a private route for a vulnerability "
       "instead of a public issue.")
ORG_FOUND = {"available": True, "found": True, "path": "SECURITY.md"}
ORG_NONE = {"available": True, "found": False, "path": None}
OFFLINE = {"available": False, "reason": "network: reads GitHub or GitLab through gh or glab"}
GITLAB = {"available": False, "reason": "no_remote: no github.com remote to compare against"}


def _repo(root: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


def _state(block: Any) -> tuple[str, str | None]:
    cond = next(c for c in lt.level_result(lt.default_model(), {"contributor_files": block})["conditions"]
                if c["id"] == CID)
    return cond["state"], cond.get("reason")


# --- the issue's success criteria ------------------------------------------------------


def test_a_guide_and_a_policy_read_met_and_removing_the_policy_reads_unmet(tmp_path: Path) -> None:
    repo = _repo(tmp_path, {"CONTRIBUTING.md": "# How to contribute\n", "SECURITY.md": "# Report privately\n"})
    block = scan_contributor_files(repo, None, OFFLINE)  # a policy in the tree needs no network
    assert block["ready"] is True and block["guide"] == {"present": True, "path": "CONTRIBUTING.md"}
    assert block["policy"] == {"available": True, "present": True, "path": "SECURITY.md", "kind": "file",
                               "source": "repo"}
    assert _state(block) == ("met", None)
    (repo / "SECURITY.md").unlink()
    block = scan_contributor_files(repo, None, ORG_NONE)  # the owner shares none either
    assert block["ready"] is False and block["policy"]["present"] is False
    assert _state(block) == ("unmet", None)


@pytest.mark.parametrize(("files", "guide", "policy"), [
    ({".github/CONTRIBUTING.md": "x", ".github/SECURITY.md": "x"}, ".github/CONTRIBUTING.md", ".github/SECURITY.md"),
    ({"docs/contributing.rst": "x", "docs/SECURITY": "x"}, "docs/contributing.rst", "docs/SECURITY"),
    ({"Contributing.adoc": "x", "security.txt": "x"}, "Contributing.adoc", "security.txt"),
])
def test_the_files_are_found_where_the_forges_read_them(tmp_path: Path, files: dict[str, str], guide: str,
                                                        policy: str) -> None:
    block = scan_contributor_files(_repo(tmp_path, files))
    assert (block["guide"]["path"], block["policy"]["path"], block["ready"]) == (guide, policy, True)


def test_a_folder_named_contributing_is_not_a_guide(tmp_path: Path) -> None:
    block = scan_contributor_files(_repo(tmp_path, {"docs/contributing/goal.md": "x", "SECURITY.md": "x"}))
    assert block["guide"]["present"] is False and block["ready"] is False
    assert _state(block) == ("unmet", None)


@pytest.mark.parametrize("where", ["CONTRIBUTING.md", "README.md"])
def test_a_private_reporting_link_is_a_policy(tmp_path: Path, where: str) -> None:
    files = {"CONTRIBUTING.md": "# Contributing\n",
             where: "Report a vulnerability at https://github.com/o/r/security/advisories/new.\n"}
    policy = scan_contributor_files(_repo(tmp_path, files))["policy"]
    assert (policy["path"], policy["kind"], policy["source"]) == (where, "private_reporting", "repo")


def test_templates_are_recorded_and_not_required(tmp_path: Path) -> None:
    bare = scan_contributor_files(_repo(tmp_path / "a", {"CONTRIBUTING.md": "x", "SECURITY.md": "x"}))
    assert (bare["issue_template"], bare["change_template"], bare["ready"]) == (False, False, True)
    full = scan_contributor_files(_repo(tmp_path / "b", {
        ".github/ISSUE_TEMPLATE/bug.yml": "x", ".github/pull_request_template.md": "x"}))
    assert (full["issue_template"], full["change_template"], full["ready"]) == (True, True, False)
    gitlab = scan_contributor_files(_repo(tmp_path / "c", {
        ".gitlab/issue_templates/Bug.md": "x", ".gitlab/merge_request_templates/Default.md": "x"}))
    assert (gitlab["issue_template"], gitlab["change_template"]) == (True, True)


def test_a_knowledge_base_reads_not_measured(tmp_path: Path) -> None:
    block = scan_contributor_files(tmp_path, {"archetype": "knowledge-base"})
    assert block["available"] is False and block["reason"].startswith("knowledge_base:")
    assert _state(block) == ("not_measured", "knowledge_base")
    assert scan_contributor_files(tmp_path, {"archetype": "software"})["available"] is True


# --- the owner's shared policy ---------------------------------------------------------


def test_the_owners_shared_policy_counts_when_it_is_read(tmp_path: Path) -> None:
    block = scan_contributor_files(_repo(tmp_path, {"CONTRIBUTING.md": "x"}), None, ORG_FOUND)
    assert block["policy"] == {"available": True, "present": True, "path": "SECURITY.md", "kind": "org_file",
                               "source": "org"}
    assert block["ready"] is True and _state(block) == ("met", None)


@pytest.mark.parametrize("org", [OFFLINE, GITLAB, None])
def test_an_unread_owner_policy_is_not_measured_never_missing(tmp_path: Path, org: Any) -> None:
    block = scan_contributor_files(_repo(tmp_path, {"CONTRIBUTING.md": "x"}), None, org)
    assert block["policy"]["available"] is False
    assert block["policy"]["reason"].startswith("org_policy_not_read:")
    assert block["ready"] is None
    assert _state(block) == ("not_measured", "org_policy_not_read")


def test_no_guide_is_unmet_whatever_the_owner_policy(tmp_path: Path) -> None:
    block = scan_contributor_files(tmp_path, None, OFFLINE)
    assert block["ready"] is False and _state(block) == ("unmet", None)


def _gh(monkeypatch: pytest.MonkeyPatch, listings: dict[str, Any]) -> None:
    monkeypatch.setattr(osp, "open_github", lambda root: GithubRepo("acme", "widget"))

    def api(path: str) -> Any:
        if path not in listings:
            raise GhUnavailable(f"not_found: `gh api {path}` (HTTP 404)")
        return listings[path]
    monkeypatch.setattr(osp, "gh_api", api)


@pytest.mark.parametrize(("listings", "expected"), [
    ({"repos/acme/.github/contents": [{"name": "SECURITY.md", "type": "file"}]},
     {"available": True, "found": True, "path": "SECURITY.md"}),
    ({"repos/acme/.github/contents": [{"name": "README.md", "type": "file"}],
      "repos/acme/.github/contents/.github": [{"name": "security.md", "type": "file"}]},
     {"available": True, "found": True, "path": ".github/security.md"}),
    ({"repos/acme/.github/contents": [{"name": "README.md", "type": "file"}]},
     {"available": True, "found": False, "path": None}),
    ({}, {"available": True, "found": False, "path": None}),  # no .github repository
])
def test_the_owners_dot_github_repository_is_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                  listings: dict[str, Any], expected: dict[str, Any]) -> None:
    _gh(monkeypatch, listings)
    assert osp.scan_org_security_policy(tmp_path) == expected


def test_no_github_remote_or_sign_in_is_unavailable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(root: Path) -> GithubRepo:
        raise GhUnavailable("no_remote: no github.com remote to compare against")
    monkeypatch.setattr(osp, "open_github", refuse)
    block = osp.scan_org_security_policy(tmp_path)
    assert block == {"available": False, "reason": "no_remote: no github.com remote to compare against"}
    assert _state(scan_contributor_files(_repo(tmp_path, {"CONTRIBUTING.md": "x"}), None, block)) == (
        "not_measured", "org_policy_not_read")


def test_an_api_error_past_the_root_is_unavailable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(osp, "open_github", lambda root: GithubRepo("acme", "widget"))

    def api(path: str) -> Any:
        raise GhUnavailable("rate_limited: still rate limited")
    monkeypatch.setattr(osp, "gh_api", api)
    assert osp.scan_org_security_policy(tmp_path)["available"] is False


def test_the_org_scan_runs_on_the_platform_pass_only() -> None:
    import lib.signals as signals

    assert "org_security_policy" in signals.PLATFORM_SCANS
    collector = next(c for c in cm.collectors(cm.core_manifest()) if c["id"] == "core.org_security_policy")
    assert collector.get("network") is True and collector.get("platform_pass") is True


# --- the manifest ----------------------------------------------------------------------


def test_the_condition_carries_the_approved_why_and_gates_no_step() -> None:
    manifest = cm.core_manifest()
    for signal in ("guide.present", "policy.present"):
        cond = next(e for e in manifest["entries"] if e.get("key") == f"{CID}:contributor_files.{signal}")
        assert cond["why"] == WHY  # type: ignore[typeddict-item]
    assert not [c for c in lt.gate_conditions(lt.default_model()) if c.startswith("contributor-files.")]
    row = next(r for r in lt.default_model()["rows"] if r["id"] == "contributor-files")
    assert row["levels"][0]["truth"] == ["docs"]
