"""lib/contributor_files.py and the contributor-files condition (#1045)."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import lib.level_tally as lt
import lib.manifest.check_manifest as cm
from lib.contributor_files import scan_contributor_files

CID = "contributor-files.1"
# The maintainer's approved why on #1045, byte for byte.
WHY = ("A contributing guide tells a newcomer, human or agent, how to build, test and shape a change the "
       "way the project merges it, and a security policy gives them a private route for a vulnerability "
       "instead of a public issue.")


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
    block = scan_contributor_files(repo)
    assert (block["ready"], block["guide"], block["policy"], block["policy_kind"]) == (
        True, "CONTRIBUTING.md", "SECURITY.md", "file")
    assert _state(block) == ("met", None)
    (repo / "SECURITY.md").unlink()
    block = scan_contributor_files(repo)
    assert block["ready"] is False and block["policy"] is None
    assert _state(block) == ("unmet", None)


@pytest.mark.parametrize(("files", "guide", "policy"), [
    ({".github/CONTRIBUTING.md": "x", ".github/SECURITY.md": "x"}, ".github/CONTRIBUTING.md", ".github/SECURITY.md"),
    ({"docs/contributing.rst": "x", "docs/SECURITY": "x"}, "docs/contributing.rst", "docs/SECURITY"),
    ({"Contributing.adoc": "x", "security.txt": "x"}, "Contributing.adoc", "security.txt"),
])
def test_the_files_are_found_where_the_forges_read_them(tmp_path: Path, files: dict[str, str], guide: str,
                                                        policy: str) -> None:
    block = scan_contributor_files(_repo(tmp_path, files))
    assert (block["guide"], block["policy"], block["ready"]) == (guide, policy, True)


def test_a_folder_named_contributing_is_not_a_guide(tmp_path: Path) -> None:
    block = scan_contributor_files(_repo(tmp_path, {"docs/contributing/goal.md": "x", "SECURITY.md": "x"}))
    assert block["guide"] is None and block["ready"] is False


@pytest.mark.parametrize("where", ["CONTRIBUTING.md", "README.md"])
def test_a_private_reporting_link_is_a_policy(tmp_path: Path, where: str) -> None:
    files = {"CONTRIBUTING.md": "# Contributing\n",
             where: "Report a vulnerability at https://github.com/o/r/security/advisories/new.\n"}
    block = scan_contributor_files(_repo(tmp_path, files))
    assert (block["policy"], block["policy_kind"], block["ready"]) == (where, "private_reporting", True)


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


# --- the manifest ----------------------------------------------------------------------


def test_the_condition_carries_the_approved_why_and_gates_no_step() -> None:
    manifest = cm.core_manifest()
    cond = next(e for e in manifest["entries"] if e.get("key") == f"{CID}:contributor_files.ready")
    assert cond["why"] == WHY  # type: ignore[typeddict-item]
    assert not [c for c in lt.gate_conditions(lt.default_model()) if c.startswith("contributor-files.")]
    row = next(r for r in lt.default_model()["rows"] if r["id"] == "contributor-files")
    assert row["levels"][0]["truth"] == ["docs"]
