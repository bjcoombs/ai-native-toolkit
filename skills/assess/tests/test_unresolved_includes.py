"""CI content behind an unread GitLab include reads not measured, never 0 (issue #1350).

The CI gate already read not measured when a repository's CI is an include the scan could not
read; the secrets guard's level 1 and the coverage gate read 0. Each now reads not measured
with a reason the fleet views split by cause.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

import pytest

import lib.scoring.level_tally as lt
from lib.counts_only.content_free import counts_only_scans
from lib.run.signals import build_document
from lib.secrets_guard import scan_secrets_guard
from lib.testing.coverage_gate import detect_coverage_gate

PROJECT_ONLY = "include:\n  - project: grp/templates\n    file: ci.yml\n"
HOOK = "repos:\n  - repo: https://github.com/gitleaks/gitleaks\n    rev: v8.18.0\n    hooks:\n      - id: gitleaks\n"


def _repo(root: Path, files: dict[str, str]) -> Path:
    env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull}
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    for args in (["init", "-q"], ["remote", "add", "origin", "https://gitlab.example.test/grp/app.git"],
                 ["add", "-A"], ["-c", "user.email=a@example.com", "-c", "user.name=A", "commit", "-qm", "c"]):
        subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, env=env)
    return root


def _conditions(scans: dict[str, Any]) -> dict[str, tuple[str, str | None]]:
    result = lt.level_result(lt.default_model(), scans)
    return {c["id"]: (c["state"], c.get("reason")) for c in result["conditions"]}


# --- success criterion 1: a repository whose CI is only an unresolved project include ------


def test_ci_only_in_an_unread_project_include_reads_not_measured_everywhere(tmp_path: Path) -> None:
    scans = build_document(_repo(tmp_path, {".gitlab-ci.yml": PROJECT_ONLY, "README.md": "x\n"}))["scans"]
    conds = _conditions(dict(scans))
    assert conds["layer-5-ci.1"] == ("not_measured", "included_jobs")
    assert conds["layer-6-coverage.1"] == ("not_measured", "ci_includes_unresolved")
    assert conds["secrets-guard.1"] == ("not_measured", "ci_includes_unresolved")
    assert conds["secrets-guard.2"] == ("not_measured", "ci_includes_unresolved")


# --- the secrets guard's level 1 (reverses the #1173 asymmetry) ----------------------------


def test_level_1_reads_not_measured_when_only_an_unread_include_could_configure_it(tmp_path: Path) -> None:
    block = scan_secrets_guard(_repo(tmp_path, {".gitlab-ci.yml": PROJECT_ONLY}))
    assert block["configured"] is None
    assert block["configured_reason"] == "ci_includes_unresolved"
    assert _conditions({"secrets_guard": block})["secrets-guard.1"] == ("not_measured", "ci_includes_unresolved")


def test_a_pre_commit_hook_settles_level_1_beside_an_unread_include(tmp_path: Path) -> None:
    block = scan_secrets_guard(_repo(tmp_path, {".gitlab-ci.yml": PROJECT_ONLY, ".pre-commit-config.yaml": HOOK}))
    assert block["configured"] is True and block["configured_reason"] is None
    conds = _conditions({"secrets_guard": block})
    assert conds["secrets-guard.1"] == ("met", None)
    assert conds["secrets-guard.2"] == ("not_measured", "ci_includes_unresolved")


@pytest.mark.parametrize("ci", ["build:\n  script:\n    - make\n",
                                "include:\n  - template: Security/SAST.gitlab-ci.yml\nbuild:\n  script:\n    - make\n"])
def test_with_every_include_read_level_1_stays_unmet(tmp_path: Path, ci: str) -> None:
    block = scan_secrets_guard(_repo(tmp_path, {".gitlab-ci.yml": ci}))
    assert block["configured"] is False and block["configured_reason"] is None
    assert _conditions({"secrets_guard": block})["secrets-guard.1"] == ("unmet", None)


def test_counts_only_keeps_both_levels_reason_codes(tmp_path: Path) -> None:
    """A fleet row is counts-only: the codes survive so the fleet views split by cause."""
    block = scan_secrets_guard(_repo(tmp_path, {".gitlab-ci.yml": PROJECT_ONLY}))
    kept = counts_only_scans({"secrets_guard": block})["secrets_guard"]
    assert kept["configured"] is None and kept["configured_reason"] == "ci_includes_unresolved"
    assert kept["enforcement"]["reason"] == "ci_includes_unresolved"
    assert _conditions({"secrets_guard": kept})["secrets-guard.2"] == ("not_measured", "ci_includes_unresolved")


# --- the coverage gate -------------------------------------------------------------------


def test_no_gate_beside_an_unread_include_reads_not_measured(tmp_path: Path) -> None:
    block = detect_coverage_gate(_repo(tmp_path, {".gitlab-ci.yml": PROJECT_ONLY}))
    assert block["threshold_meets_minimum"] is False
    assert (block["enforced_meets_minimum"], block["enforced_basis"]) == (None, "ci_includes_unresolved")


@pytest.mark.parametrize("ci", ["test:\n  script:\n    - pytest\n",
                                "include:\n  - template: Security/SAST.gitlab-ci.yml\ntest:\n  script:\n    - pytest\n"])
def test_no_gate_with_every_include_read_stays_unmet(tmp_path: Path, ci: str) -> None:
    block = detect_coverage_gate(_repo(tmp_path, {".gitlab-ci.yml": ci}))
    assert (block["enforced_meets_minimum"], block["enforced_basis"]) == (False, "no_gate")


def test_a_low_gate_read_in_the_checkout_stays_below_the_minimum(tmp_path: Path) -> None:
    files = {".gitlab-ci.yml": PROJECT_ONLY, "pyproject.toml": "[tool.coverage.report]\nfail_under = 20\n"}
    block = detect_coverage_gate(_repo(tmp_path, files))
    assert (block["enforced_meets_minimum"], block["enforced_basis"]) == (False, "threshold_below_minimum")


@pytest.mark.parametrize("manifest", ["go.mod", "Cargo.toml"])
def test_a_go_or_rust_module_with_no_gate_beside_an_unread_include_names_the_include(tmp_path: Path, manifest: str) -> None:
    body = "module example.test/app\n" if manifest == "go.mod" else "[package]\nname = \"app\"\n"
    block = detect_coverage_gate(_repo(tmp_path, {".gitlab-ci.yml": PROJECT_ONLY, manifest: body}))
    assert (block["enforced_meets_minimum"], block["enforced_basis"]) == (None, "ci_includes_unresolved")


def test_a_gate_whose_threshold_is_unread_keeps_its_reason_beside_an_unread_include(tmp_path: Path) -> None:
    ci = PROJECT_ONLY + "test:\n  script:\n    - pytest --cov --cov-fail-under=$COV_MIN\n"
    block = detect_coverage_gate(_repo(tmp_path, {".gitlab-ci.yml": ci}))
    assert block["gates"] and block["enforced_basis"] == "threshold_not_read"


def test_a_resolved_local_include_still_holds_the_reading(tmp_path: Path) -> None:
    """The coverage scan reads only the root CI file, so a gate in a local include it resolved is unseen."""
    files = {".gitlab-ci.yml": "include:\n  - local: .gitlab/ci/test.yml\n",
             ".gitlab/ci/test.yml": "test:\n  script:\n    - pytest --cov --cov-fail-under=80\n"}
    block = detect_coverage_gate(_repo(tmp_path, files))
    assert (block["enforced_meets_minimum"], block["enforced_basis"]) == (None, "ci_includes_unresolved")
