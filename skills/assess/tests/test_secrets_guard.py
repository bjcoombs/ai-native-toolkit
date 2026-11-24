"""lib/secrets_guard.py and the secrets-guard practice (#1173): level 1 any guard, level 2 one an agent can't skip."""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

import lib.level_tally as lt
import lib.manifest.check_manifest as cm
from lib.secrets_guard import SECRET_TOOLS, scan_secrets_guard

HOOK = "repos:\n  - repo: https://github.com/gitleaks/gitleaks\n    rev: v8.18.0\n    hooks:\n      - id: gitleaks\n"
ACTION = "jobs:\n  scan:\n    runs-on: ubuntu-latest\n    steps:\n      - uses: gitleaks/gitleaks-action@v2\n"


ON_CHANGE = "on: [push, pull_request]\n"


def _repo(root: Path, files: dict[str, str], remote: str | None = None) -> Path:
    """Write ``files``; a GitHub workflow with no ``on:`` line gets ``ON_CHANGE``, so a case states
    only the step it tests (the trigger rule has its own tests)."""
    for rel, text in files.items():
        if "/workflows/" in rel and not any(ln.startswith(("on:", "true:")) for ln in text.splitlines()):
            text = ON_CHANGE + text
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    if remote is not None:
        subprocess.run(["git", "init", "-q", str(root)], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(root), "remote", "add", "origin", remote], check=True, capture_output=True)
    return root


def _states(block: Any) -> tuple[tuple[str, str | None], tuple[str, str | None]]:
    conds = {c["id"]: c for c in lt.level_result(lt.default_model(), {"secrets_guard": block})["conditions"]}
    one, two = conds["secrets-guard.1"], conds["secrets-guard.2"]
    return (one["state"], one.get("reason")), (two["state"], two.get("reason"))


# --- the issue's success criteria ------------------------------------------------------


def test_a_gitleaks_hook_meets_level_1_listed_and_not_level_2(tmp_path: Path) -> None:
    block = scan_secrets_guard(_repo(tmp_path, {".pre-commit-config.yaml": HOOK}))
    assert block["pre_commit"] == {"present": True, "file": ".pre-commit-config.yaml", "tools": ["gitleaks"]}
    assert block["configured"] is True and block["sources"] == [{"source": "pre_commit", "grade": "config_only"}]
    one, two = _states(block)
    assert one == ("met", None) and two == ("unmet", None)  # no remote: no platform setting to read
    cond = next(c for c in lt.level_result(lt.default_model(), {"secrets_guard": block})["conditions"]
                if c["id"] == "secrets-guard.1")
    assert cond["grade"] == "config_only" and cond["counted"] is False  # listed, not counted


def test_nothing_configured_is_unmet(tmp_path: Path) -> None:
    block = scan_secrets_guard(_repo(tmp_path, {"README.md": "x"}))
    assert block["configured"] is False and block["sources"] == []
    assert _states(block) == (("unmet", None), ("unmet", None))


@pytest.mark.parametrize("remote", ["https://github.com/acme/widget.git", "git@github.com:acme/widget.git"])
def test_a_github_repository_without_a_ci_scan_reads_level_2_not_measured(tmp_path: Path, remote: str) -> None:
    block = scan_secrets_guard(_repo(tmp_path, {".pre-commit-config.yaml": HOOK}, remote))
    assert block["enforcement"]["available"] is False
    assert block["enforcement"]["reason"].startswith("secret_scanning_not_read:")
    one, two = _states(block)
    assert one == ("met", None) and two == ("not_measured", "secret_scanning_not_read")


def test_a_gitlab_repository_without_a_ci_scan_reads_level_2_unmet(tmp_path: Path) -> None:
    block = scan_secrets_guard(_repo(tmp_path, {"README.md": "x"}, "https://gitlab.com/grp/app.git"))
    assert _states(block)[1] == ("unmet", None)


# --- CI --------------------------------------------------------------------------------


@pytest.mark.parametrize(("rel", "text", "tools"), [
    (".github/workflows/scan.yml", ACTION, ["gitleaks"]),
    (".github/workflows/scan.yaml", "steps:\n  - uses: trufflesecurity/trufflehog@main\n", ["trufflehog"]),
    (".github/workflows/scan.yml", "steps:\n  - run: ggshield secret scan ci\n", ["ggshield"]),
    (".gitlab-ci.yml", "include:\n  - template: Security/Secret-Detection.gitlab-ci.yml\n", ["secret_detection"]),
    (".gitlab-ci.yml", "secret_detection:\n  stage: test\n", ["secret_detection"]),
    (".github/workflows/scan.yml", "steps:\n  - uses: GitGuardian/ggshield-action@v1\n", ["ggshield"]),
    (".github/workflows/scan.yml", "steps:\n  - run: |\n      docker run zricethezav/gitleaks detect\n", ["gitleaks"]),
    (".github/workflows/scan.yml", "steps:\n  - run: detect-secrets scan --baseline .secrets.baseline\n",
     ["detect-secrets"]),
    (".github/workflows/scan.yml", "steps:\n  - name: scan\n    run: |\n      gitleaks detect --source .\n",
     ["gitleaks"]),
    (".github/workflows/scan.yml", "steps:\n  - run: >\n      trufflehog git file://. --fail\n", ["trufflehog"]),
    (".gitlab-ci.yml", "scan:\n  script:\n    - echo start\n    - gitleaks detect --source .\n", ["gitleaks"]),
    (".gitlab-ci.yml", "include:\n  - template: Security/Secret-Detection.gitlab-ci.yml\n"
                       "secret_detection:\n  allow_failure: true\n", ["secret_detection"]),  # reports by artifact
])
def test_a_ci_scan_meets_level_2_measured(tmp_path: Path, rel: str, text: str, tools: list[str]) -> None:
    block = scan_secrets_guard(_repo(tmp_path, {rel: text}, "https://github.com/acme/widget.git"))
    assert block["ci"]["tools"] == tools and block["enforcement"] == {"available": True, "present": True,
                                                                     "source": "ci"}
    assert {"source": "ci", "grade": "measured"} in block["sources"]
    assert _states(block) == (("met", None), ("met", None))


@pytest.mark.parametrize(("rel", "text"), [
    (".github/workflows/x.yml", "steps:\n  - run: gitleaks detect || true\n"),       # masked: guards nothing
    (".github/workflows/x.yml", "steps:\n  # - uses: gitleaks/gitleaks-action@v2\n"),  # commented out
    (".github/workflows/x.yml", "steps:\n  - run: echo gitleaks-report.json\n"),       # a file name, not the tool
    (".github/workflows/x.yml", "env:\n  TOKEN: abc # gitleaks:allow\n"),              # an allowlist note
    (".github/workflows/x.yml", "on:\n  push:\n    paths-ignore:\n      - '.gitleaks.toml'\n"),  # a path
    (".github/workflows/x.yml",                                                        # a masked step
     "jobs:\n  s:\n    steps:\n      - uses: gitleaks/gitleaks-action@v2\n        continue-on-error: true\n"),
    (".github/workflows/x.yml",                                                        # a masked job
     "jobs:\n  s:\n    continue-on-error: true\n    steps:\n      - run: gitleaks detect\n"),
    (".github/workflows/x.yml", "steps:\n  - uses: someone/gitleaks-wrapper@v1\n"),   # not a known action
    (".gitlab-ci.yml", ".secret_detection:\n  script: echo hi\n"),
    (".gitlab-ci.yml", "scan:\n  allow_failure: true\n  script:\n    - gitleaks detect\n"),  # masked job                    # a hidden job never runs
    (".gitlab-ci.yml", "include:\n  # - template: Security/Secret-Detection.gitlab-ci.yml\n"),  # commented out
])
def test_a_masked_commented_or_lookalike_line_is_not_a_scan(tmp_path: Path, rel: str, text: str) -> None:
    block = scan_secrets_guard(_repo(tmp_path, {rel: text}))
    assert block["ci"]["present"] is False


@pytest.mark.parametrize(("trigger", "counts"), [
    ("on: schedule\n", False),
    ("on:\n  schedule:\n    - cron: '0 0 * * *'\n  workflow_dispatch:\n", False),
    ("on: pull_request\n", True),
    ("on:\n  merge_group:\n  schedule:\n    - cron: '0 0 * * *'\n", True),
    ("'on':\n  push:\n    branches: [main]\n", True),
])
def test_a_github_workflow_counts_only_when_it_runs_on_a_change(tmp_path: Path, trigger: str, counts: bool) -> None:
    block = scan_secrets_guard(_repo(tmp_path, {".github/workflows/x.yml": trigger + ACTION}))
    assert block["ci"]["present"] is counts


@pytest.mark.parametrize(("rel", "text"), [
    (".github/workflows/x.yml", "jobs:\n  s:\n    if: false\n    steps:\n      - run: gitleaks detect\n"),
    (".github/workflows/x.yml", "jobs:\n  s:\n    if: ${{ false }}\n    steps:\n      - run: gitleaks detect\n"),
    (".gitlab-ci.yml", "include:\n  - template: Security/Secret-Detection.gitlab-ci.yml\n"
                       "secret_detection:\n  rules:\n    - when: never\n"),
])
def test_a_job_that_never_runs_is_not_a_scan(tmp_path: Path, rel: str, text: str) -> None:
    assert scan_secrets_guard(_repo(tmp_path, {rel: text}))["ci"]["present"] is False


def test_a_root_gitlab_file_counts_in_a_directory_named_workflows(tmp_path: Path) -> None:
    root = tmp_path / "workflows"
    root.mkdir()
    text = "include:\n  - template: Security/Secret-Detection.gitlab-ci.yml\n"
    assert scan_secrets_guard(_repo(root, {".gitlab-ci.yml": text}))["ci"]["present"] is True


def test_an_unmasked_step_beside_a_masked_one_still_counts(tmp_path: Path) -> None:
    text = ("jobs:\n  s:\n    steps:\n      - uses: gitleaks/gitleaks-action@v2\n        continue-on-error: true\n"
            "      - uses: trufflesecurity/trufflehog@main\n")
    assert scan_secrets_guard(_repo(tmp_path, {".github/workflows/x.yml": text}))["ci"]["tools"] == ["trufflehog"]


def test_a_workflow_in_a_subfolder_is_not_read(tmp_path: Path) -> None:
    # GitHub runs only the files directly under .github/workflows/.
    assert scan_secrets_guard(_repo(tmp_path, {".github/workflows/old/scan.yml": ACTION}))["ci"]["present"] is False


@pytest.mark.parametrize("tool", SECRET_TOOLS)
def test_every_listed_tool_is_found_as_a_hook(tmp_path: Path, tool: str) -> None:
    hook = f"repos:\n  - repo: https://example.org/{tool}\n    hooks:\n      - id: {tool}\n"
    assert scan_secrets_guard(_repo(tmp_path, {".pre-commit-config.yaml": hook}))["pre_commit"]["tools"] == [tool]


def test_a_pre_commit_config_without_a_secrets_hook_is_not_a_guard(tmp_path: Path) -> None:
    hook = "repos:\n  - repo: https://github.com/astral-sh/ruff-pre-commit\n    hooks:\n      - id: ruff\n"
    block = scan_secrets_guard(_repo(tmp_path, {".pre-commit-config.yaml": hook}))
    assert block["pre_commit"]["present"] is False and block["configured"] is False


# --- the manifest ----------------------------------------------------------------------


def test_the_levels_carry_their_grades_and_gate_no_step() -> None:
    manifest = cm.core_manifest()
    signals = {e["path"]: e for e in manifest["entries"] if e.get("kind") == "signal"
               and str(e.get("path", "")).startswith("secrets_guard.")}
    assert signals["secrets_guard.configured"]["evidence"] == "config_text"  # type: ignore[typeddict-item]
    assert signals["secrets_guard.enforcement.present"]["evidence"] == "head_structure"  # type: ignore[typeddict-item]
    assert not [c for c in lt.gate_conditions(lt.default_model()) if c.startswith("secrets-guard.")]
