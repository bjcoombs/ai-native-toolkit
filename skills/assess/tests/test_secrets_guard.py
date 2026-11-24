"""lib/secrets_guard.py and the secrets-guard practice (#1173): level 1 any guard, level 2 one an agent can't skip."""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

import lib.level_tally as lt
import lib.manifest.check_manifest as cm
from test_ci_includes import INCLUDE_TEST, _root_with_template

from lib.secrets_guard import SECRET_TOOLS, scan_secrets_guard

HOOK = "repos:\n  - repo: https://github.com/gitleaks/gitleaks\n    rev: v8.18.0\n    hooks:\n      - id: gitleaks\n"
ACTION = "jobs:\n  scan:\n    runs-on: ubuntu-latest\n    steps:\n      - uses: gitleaks/gitleaks-action@v2\n"


ON_CHANGE = "on: [push, pull_request]\n"


def _repo(root: Path, files: dict[str, str], remote: str | None = None) -> Path:
    """Write ``files``; a GitHub workflow with no ``on:`` line gets ``ON_CHANGE``, so a case states
    only the step it tests (the trigger rule has its own tests)."""
    for rel, text in files.items():
        if "/workflows/" in rel and not any(ln.startswith(("on:", "true:", "'on':", '"on":')) for ln in text.splitlines()):
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
    (".gitlab-ci.yml", "include:\n  - template: Jobs/Secret-Detection.gitlab-ci.yml\n"
                       "secret_detection:\n  stage: test\n", ["secret_detection"]),  # an override
    (".github/workflows/scan.yml", "steps:\n  - uses: GitGuardian/ggshield-action@v1\n", ["ggshield"]),
    (".github/workflows/scan.yml", "steps:\n  - run: |\n      docker run zricethezav/gitleaks detect\n", ["gitleaks"]),
    (".github/workflows/scan.yml",
     "steps:\n  - run: detect-secrets-hook --baseline .secrets.baseline $(git ls-files)\n", ["detect-secrets"]),
    (".github/workflows/scan.yml", "steps:\n  - run: echo \"build #1\" && gitleaks detect\n", ["gitleaks"]),
    (".github/workflows/scan.yml", "steps:\n  - run: gitleaks detect || exit 1\n", ["gitleaks"]),
    (".gitlab-ci.yml", "scan:\n  script:\n    - |\n      trufflehog git file://. \\\n        --fail\n", ["trufflehog"]),
    (".github/workflows/scan.yml", "steps:\n  - name: scan\n    run: |\n      gitleaks detect --source .\n",
     ["gitleaks"]),
    (".github/workflows/scan.yml", "steps:\n  - run: >\n      trufflehog git file://. --fail\n", ["trufflehog"]),
    (".github/workflows/scan.yml", "steps:\n  - run: docker run -v $PWD:/path zricethezav/gitleaks:latest detect\n",
     ["gitleaks"]),
    (".github/workflows/scan.yml", "steps:\n  - run: docker run --rm ghcr.io/gitleaks/gitleaks:v8.18.0 detect\n",
     ["gitleaks"]),
    (".github/workflows/scan.yml", "steps:\n  - uses: docker://zricethezav/gitleaks:latest\n", ["gitleaks"]),
    (".github/workflows/scan.yml", "steps:\n  - run: npx secretlint '**/*'\n", ["secretlint"]),
    (".github/workflows/scan.yml", "steps:\n  - run: pipx run detect-secrets-hook --baseline b\n", ["detect-secrets"]),
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
    (".github/workflows/x.yml", "steps:\n  - run: gitleaks detect || :\n"),          # masked with the no-op
    (".github/workflows/x.yml", "steps:\n  - run: gitleaks detect || echo leaks found\n"),
    (".gitlab-ci.yml", "scan:\n  script:\n    - gitleaks detect | tee out.txt\n"),  # no pipefail
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
    (".gitlab-ci.yml", "scan:\n  allow_failure: true\n  script:\n    - gitleaks detect\n"),  # masked job
    (".github/workflows/x.yml", "steps:\n  - continue-on-error: true\n    run: gitleaks detect\n"),  # step-first mask
    (".github/workflows/x.yml", "steps:\n  - run: gitleaks detect\n    continue-on-error: ${{ true }}\n"),
    (".gitlab-ci.yml", "deploy:\n  needs:\n    - gitleaks\n  script:\n    - make deploy\n"),  # a job name
    (".github/workflows/x.yml", "steps:\n  - name: Secrets - gitleaks\n    run: echo skipped\n"),  # a step label
    (".github/workflows/x.yml", "steps:\n  - run: detect-secrets scan --baseline .secrets.baseline\n"),  # exits 0
    (".github/workflows/x.yml", "steps:\n  - run: trufflehog git file://.\n"),  # no --fail: exits 0
    (".github/workflows/x.yml", "steps:\n  - run: gitleaks detect --exit-code 0\n"),  # cannot fail
    (".github/workflows/x.yml", "steps:\n  - run: gitleaks protect --staged\n"),  # nothing staged in CI
    (".gitlab-ci.yml", "scan:\n  script:\n    - |\n      gitleaks detect \\\n        --exit-code 0\n"),  # continued
    (".github/workflows/x.yml", "steps:\n  - run: gitleaks version\n"),
    (".github/workflows/x.yml", "steps:\n  - run: gitleaks --help\n"),
    (".github/workflows/x.yml", "steps:\n  - run: echo Secret-Detection.gitlab-ci.yml\n"),  # a GitHub file
    (".gitlab-ci.yml", "build:\n  tags:\n    - gitleaks\n  script:\n    - make\n"),  # a runner tag
    (".github/workflows/x.yml", "on:\n  push:\n    paths:\n      - trufflehog\n"),  # a path filter                    # a hidden job never runs
    (".gitlab-ci.yml", "include:\n  # - template: Security/Secret-Detection.gitlab-ci.yml\n"),  # commented out
    (".gitlab-ci.yml", "include:\n  - template: Jobs/Secret-Detection.gitlab-ci.yml\n"
                       "variables:\n  SECRET_DETECTION_DISABLED: \"true\"\n"),  # switched off
    (".gitlab-ci.yml", "scan:\n  script:\n    - echo hi\n  after_script:\n    - gitleaks detect --source .\n"),
    (".gitlab-ci.yml", "scan:\n  when: manual\n  script:\n    - gitleaks detect\n"),  # a click, not a guard
    (".gitlab-ci.yml", "scan:\n  rules:\n    - when: manual\n  script:\n    - gitleaks detect\n"),
    (".gitlab-ci.yml", "scan:\n  script:\n    - echo hi\n  after_script:\n  - gitleaks detect\n"),  # indentless list
    (".gitlab-ci.yml", "secret_detection:\n  script: echo ready\n"),  # a job name, not a scan
    (".gitlab-ci.yml", "include:\n  - template: Jobs/Secret-Detection.gitlab-ci.yml\n"
                       "secret_detection:\n  when: manual\n"),  # a click, not a guard
    (".github/workflows/x.yml", "on:\n  workflow_dispatch:\n    inputs:\n      pull_request:\n        type: string\n"
                                "jobs:\n  s:\n    steps:\n      - run: gitleaks detect\n"),  # an input name
    (".github/workflows/x.yml", "steps:\n  - run: trufflehog git file://. --fail-on-scan-errors\n"),
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


def test_a_job_level_manual_in_a_four_space_file_masks_the_job(tmp_path: Path) -> None:
    text = ("scan:\n    rules:\n        - if: $CI_COMMIT_TAG\n    when: manual\n"
            "    script:\n        - gitleaks detect\n")
    assert scan_secrets_guard(_repo(tmp_path, {".gitlab-ci.yml": text}))["ci"]["present"] is False


def test_a_github_input_named_when_masks_nothing(tmp_path: Path) -> None:
    text = ("jobs:\n  a:\n    steps:\n      - uses: some/action@v1\n        with:\n          when: manual\n"
            "  b:\n    steps:\n      - run: gitleaks detect\n")
    assert scan_secrets_guard(_repo(tmp_path, {".github/workflows/x.yml": text}))["ci"]["tools"] == ["gitleaks"]


@pytest.mark.parametrize(("rel", "text"), [
    (".github/workflows/x.yml", "jobs:\n  s:\n    if: false\n    steps:\n      - run: gitleaks detect\n"),
    (".github/workflows/x.yml", "jobs:\n  s:\n    if: ${{ false }}\n    steps:\n      - run: gitleaks detect\n"),
    (".gitlab-ci.yml", "include:\n  - template: Security/Secret-Detection.gitlab-ci.yml\n"
                       "secret_detection:\n  rules:\n    - when: never\n"),
])
def test_a_job_that_never_runs_is_not_a_scan(tmp_path: Path, rel: str, text: str) -> None:
    assert scan_secrets_guard(_repo(tmp_path, {rel: text}))["ci"]["present"] is False


@pytest.mark.parametrize("text", [
    "include:\n  - component: $CI_SERVER_FQDN/components/secret-detection/secret-detection@1.1\n",
    "include:\n  - template: Auto-DevOps.gitlab-ci.yml\n",
])
def test_gitlab_component_or_auto_devops_is_a_scan(tmp_path: Path, text: str) -> None:
    assert scan_secrets_guard(_repo(tmp_path, {".gitlab-ci.yml": text}))["ci"]["tools"] == ["secret_detection"]


def test_a_scan_in_a_local_include_counts(tmp_path: Path) -> None:
    root = _repo(tmp_path, {".gitlab-ci.yml": "include:\n  - local: ci/scan.yml\n",
                            "ci/scan.yml": "scan:\n  script:\n    - gitleaks detect\n"})
    subprocess.run(["git", "init", "-q", str(root)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True, capture_output=True)
    ci = scan_secrets_guard(root)["ci"]
    assert ci["tools"] == ["gitleaks"] and ci["includes_unresolved"] == 0


def test_an_unread_include_leaves_level_2_not_measured(tmp_path: Path) -> None:
    root = _repo(tmp_path, {".gitlab-ci.yml": "include:\n  - project: group/ci\n    file: scan.yml\n"},
                 remote="https://gitlab.com/g/p.git")
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True, capture_output=True)
    block = scan_secrets_guard(root)
    assert block["ci"]["includes_unresolved"] == 1
    assert block["enforcement"]["available"] is False
    assert block["enforcement"]["reason"].startswith("ci_includes_unresolved")


def test_a_project_include_resolves_from_a_ghsync_root(tmp_path: Path) -> None:
    ci = INCLUDE_TEST.replace("test.yml", "scan.yml")
    root, app = _root_with_template(tmp_path, {"templates/scan.yml": "scan:\n  script:\n    - gitleaks detect\n"}, ci=ci)
    assert scan_secrets_guard(app)["ci"]["includes_unresolved"] == 1  # without the root, unread
    ci_block = scan_secrets_guard(app, ghsync_root=root)["ci"]
    assert ci_block["tools"] == ["gitleaks"] and ci_block["includes_unresolved"] == 0


PRE_COMMIT = "repos:\n  - repo: https://github.com/gitleaks/gitleaks\n    hooks:\n      - id: gitleaks\n"


@pytest.mark.parametrize(("step", "tools"), [
    ("  - uses: pre-commit/action@v3.0.1\n", ["gitleaks"]),
    ("  - run: pre-commit run --all-files\n", ["gitleaks"]),
    ("  - run: pre-commit run gitleaks --all-files\n", ["gitleaks"]),
    ("  - run: pre-commit run eslint --all-files\n", []),  # another hook by id
    ("  - run: pre-commit run gitleaks\n", []),  # staged files only: none in CI
    ("  - run: pre-commit run --all-files ruff\n", []),  # a hook id after the flag
    ("  - run: pre-commit run --all-files gitleaks\n", ["gitleaks"]),
    ("  - run: pre-commit run -a\n", ["gitleaks"]),
    ("  - name: pre-commit run --all-files\n    run: echo hi\n", []),  # a step label
])
def test_ci_that_runs_the_pre_commit_secrets_hooks_is_a_scan(tmp_path: Path, step: str, tools: list[str]) -> None:
    root = _repo(tmp_path, {".pre-commit-config.yaml": PRE_COMMIT, ".github/workflows/x.yml": "steps:\n" + step})
    assert scan_secrets_guard(root)["ci"]["tools"] == tools


def test_a_pre_commit_comment_naming_a_tool_is_not_a_hook(tmp_path: Path) -> None:
    text = "repos:\n  - repo: https://github.com/astral-sh/ruff-pre-commit\n    hooks:\n      - id: ruff  # gitleaks later\n"
    assert scan_secrets_guard(_repo(tmp_path, {".pre-commit-config.yaml": text}))["pre_commit"]["present"] is False


def test_a_hook_id_variant_maps_to_its_tool(tmp_path: Path) -> None:
    cfg = "repos:\n  - repo: https://github.com/gitleaks/gitleaks\n    hooks:\n      - id: gitleaks-docker\n"
    root = _repo(tmp_path, {".pre-commit-config.yaml": cfg,
                            ".github/workflows/x.yml": "steps:\n  - run: pre-commit run gitleaks-docker --all-files\n"})
    assert scan_secrets_guard(root)["ci"]["tools"] == ["gitleaks"]


@pytest.mark.parametrize(("stages", "runs"), [
    ("        stages:\n          - pre-commit\n", True),
    ("        stages:\n        - pre-commit\n        - pre-push\n", True),
    ("        stages:\n          - manual\n", False),
])
def test_a_block_style_stages_list_is_read(tmp_path: Path, stages: str, runs: bool) -> None:
    root = _repo(tmp_path, {".pre-commit-config.yaml": PRE_COMMIT + stages,
                            ".github/workflows/x.yml": "steps:\n  - run: pre-commit run --all-files\n"})
    assert scan_secrets_guard(root)["ci"]["present"] is runs


def test_a_manual_stage_hook_is_not_run_by_ci(tmp_path: Path) -> None:
    cfg = PRE_COMMIT + "        stages: [manual]\n"
    root = _repo(tmp_path, {".pre-commit-config.yaml": cfg,
                            ".github/workflows/x.yml": "steps:\n  - run: pre-commit run --all-files\n"})
    block = scan_secrets_guard(root)
    assert block["ci"]["present"] is False and block["configured"] is True  # level 1 still lists it


def test_pre_commit_run_without_a_secrets_hook_is_not_a_scan(tmp_path: Path) -> None:
    root = _repo(tmp_path, {".github/workflows/x.yml": "steps:\n  - run: pre-commit run --all-files\n"})
    assert scan_secrets_guard(root)["ci"]["present"] is False


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


def test_stages_before_the_id_is_read(tmp_path: Path) -> None:
    cfg = ("repos:\n  - repo: https://github.com/gitleaks/gitleaks\n    hooks:\n"
           "      - stages: [manual]\n        id: gitleaks\n")
    root = _repo(tmp_path, {".pre-commit-config.yaml": cfg,
                            ".github/workflows/x.yml": "steps:\n  - run: pre-commit run --all-files\n"})
    assert scan_secrets_guard(root)["ci"]["present"] is False


@pytest.mark.parametrize(("default", "runs"), [("default_stages: [pre-push]\n", False),
                                                ("default_stages: [pre-commit, pre-push]\n", True)])
def test_default_stages_apply_to_a_hook_with_none(tmp_path: Path, default: str, runs: bool) -> None:
    root = _repo(tmp_path, {".pre-commit-config.yaml": default + PRE_COMMIT,
                            ".github/workflows/x.yml": "steps:\n  - run: pre-commit run --all-files\n"})
    assert scan_secrets_guard(root)["ci"]["present"] is runs


@pytest.mark.parametrize("url", ["https://github.com/o/r.git", "git@github.com:o/r.git", "ssh://git@github.com/o/r",
                                 "ssh://git@github.com:22/o/r", "org-123@github.com:o/r", "github.com:o/r",
                                 "https://gitlab.com/o/r.git", "git@example.com:o/r.git"])
def test_the_remote_match_agrees_with_gh_cli(url: str) -> None:
    from lib.hosting.gh_cli import parse_github_remote
    from lib.secrets_guard import _GITHUB_REMOTE
    assert bool(_GITHUB_REMOTE.match(url)) is (parse_github_remote(url) is not None)


def test_a_manual_variant_does_not_drop_a_selected_hook(tmp_path: Path) -> None:
    cfg = PRE_COMMIT + "      - id: gitleaks-docker\n        stages: [manual]\n"
    root = _repo(tmp_path, {".pre-commit-config.yaml": cfg,
                            ".github/workflows/x.yml": "steps:\n  - run: pre-commit run --all-files\n"})
    assert scan_secrets_guard(root)["ci"]["tools"] == ["gitleaks"]


def test_never_rules_all_the_way_down_switch_the_job_off(tmp_path: Path) -> None:
    text = ("include:\n  - template: Jobs/Secret-Detection.gitlab-ci.yml\n"
            "secret_detection:\n  rules:\n    - if: $CI_COMMIT_TAG\n      when: never\n    - when: never\n")
    assert scan_secrets_guard(_repo(tmp_path, {".gitlab-ci.yml": text}))["ci"]["present"] is False


def test_a_bare_gitleaks_call_prints_usage(tmp_path: Path) -> None:
    text = "steps:\n  - run: |\n      gitleaks\n"
    assert scan_secrets_guard(_repo(tmp_path, {".github/workflows/x.yml": text}))["ci"]["present"] is False


def test_an_untagged_docker_uses_step_still_counts(tmp_path: Path) -> None:
    text = "steps:\n  - uses: docker://zricethezav/gitleaks\n    with:\n      args: detect --source .\n"
    assert scan_secrets_guard(_repo(tmp_path, {".github/workflows/x.yml": text}))["ci"]["tools"] == ["gitleaks"]


@pytest.mark.parametrize(("root_ci", "included"), [
    ("include:\n  - local: ci/sec.yml\nvariables:\n  SECRET_DETECTION_DISABLED: \"true\"\n",
     "include:\n  - template: Jobs/Secret-Detection.gitlab-ci.yml\n"),
    ("include:\n  - template: Jobs/Secret-Detection.gitlab-ci.yml\n  - local: ci/sec.yml\n",
     "secret_detection:\n  rules:\n    - when: never\n"),
])
def test_a_switch_off_in_one_file_cancels_the_template_in_another(tmp_path: Path, root_ci: str, included: str) -> None:
    root = _repo(tmp_path, {".gitlab-ci.yml": root_ci, "ci/sec.yml": included}, remote="https://gitlab.com/g/p.git")
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True, capture_output=True)
    assert scan_secrets_guard(root)["ci"]["present"] is False


def test_a_template_in_an_include_still_counts(tmp_path: Path) -> None:
    root = _repo(tmp_path, {".gitlab-ci.yml": "include:\n  - local: ci/sec.yml\n",
                            "ci/sec.yml": "include:\n  - template: Jobs/Secret-Detection.gitlab-ci.yml\n"},
                 remote="https://gitlab.com/g/p.git")
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True, capture_output=True)
    assert scan_secrets_guard(root)["ci"]["tools"] == ["secret_detection"]


def test_a_partial_root_override_keeps_an_included_when_never(tmp_path: Path) -> None:
    root_ci = ("include:\n  - template: Jobs/Secret-Detection.gitlab-ci.yml\n  - local: ci/sec.yml\n"
               "secret_detection:\n  variables:\n    FOO: bar\n")
    root = _repo(tmp_path, {".gitlab-ci.yml": root_ci, "ci/sec.yml": "secret_detection:\n  rules:\n    - when: never\n"},
                 remote="https://gitlab.com/g/p.git")
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True, capture_output=True)
    assert scan_secrets_guard(root)["ci"]["present"] is False


def test_a_nested_when_does_not_decide_for_the_job(tmp_path: Path) -> None:
    root_ci = ("include:\n  - template: Jobs/Secret-Detection.gitlab-ci.yml\n  - local: ci/sec.yml\n"
               "secret_detection:\n  artifacts:\n    when: always\n")
    root = _repo(tmp_path, {".gitlab-ci.yml": root_ci, "ci/sec.yml": "secret_detection:\n  rules:\n    - when: never\n"},
                 remote="https://gitlab.com/g/p.git")
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True, capture_output=True)
    assert scan_secrets_guard(root)["ci"]["present"] is False


def test_pre_commit_action_extra_args_select_one_hook(tmp_path: Path) -> None:
    cfg = PRE_COMMIT + "  - repo: https://github.com/astral-sh/ruff-pre-commit\n    hooks:\n      - id: ruff\n"
    wf = "steps:\n  - uses: pre-commit/action@v3.0.1\n    with:\n      extra_args: ruff --all-files\n"
    root = _repo(tmp_path, {".pre-commit-config.yaml": cfg, ".github/workflows/x.yml": wf})
    assert scan_secrets_guard(root)["ci"]["present"] is False


def test_a_named_hook_must_match_a_configured_id(tmp_path: Path) -> None:
    cfg = "repos:\n  - repo: https://github.com/gitleaks/gitleaks\n    hooks:\n      - id: gitleaks-docker\n"
    root = _repo(tmp_path, {".pre-commit-config.yaml": cfg,
                            ".github/workflows/x.yml": "steps:\n  - run: pre-commit run gitleaks --all-files\n"})
    assert scan_secrets_guard(root)["ci"]["present"] is False


def test_a_hidden_gitlab_job_is_not_a_scan(tmp_path: Path) -> None:
    text = ".secret_scan:\n  script:\n    - gitleaks detect\nbuild:\n  script:\n    - make\n"
    assert scan_secrets_guard(_repo(tmp_path, {".gitlab-ci.yml": text}))["ci"]["present"] is False


TEMPLATE = "include:\n  - template: Jobs/Secret-Detection.gitlab-ci.yml\n"


@pytest.mark.parametrize("job", [
    "secret_detection:\n  rules:\n    - if: $CI_COMMIT_TAG\n      when: never\n    - when: on_success\n",
    "secret_detection:\n  rules:\n    - if: $CI_PIPELINE_SOURCE == \"merge_request_event\"\n    - when: never\n",
    "secret_detection:\n  rules:\n    - changes:\n        - docs/**\n      when: never\n",
    "secret_detection:\n  only:\n    - main\n",
    "secret_detection:\n  when: delayed\n  start_in: 5 minutes\n",
])
def test_a_template_job_with_rules_is_not_measured(tmp_path: Path, job: str) -> None:
    block = scan_secrets_guard(_repo(tmp_path, {".gitlab-ci.yml": TEMPLATE + job}))
    assert block["ci"]["present"] is False and block["ci"]["rules_unevaluated"] == ["secret_detection"]
    assert block["enforcement"]["available"] is False
    assert block["enforcement"]["reason"].startswith("gitlab_rules_unevaluated:")


def test_a_scan_job_with_rules_is_not_measured(tmp_path: Path) -> None:
    text = "secrets:\n  rules:\n    - if: $CI_COMMIT_TAG\n      when: manual\n    - when: on_success\n  script:\n    - gitleaks detect\n"
    block = scan_secrets_guard(_repo(tmp_path, {".gitlab-ci.yml": text}))
    assert block["ci"]["rules_unevaluated"] == ["gitleaks"]
    assert block["enforcement"]["reason"].startswith("gitlab_rules_unevaluated:")


@pytest.mark.parametrize("job", ["secret_detection:\n  when: never\n", "secret_detection:\n  when: manual\n"])
def test_a_job_level_never_or_manual_is_certainly_off(tmp_path: Path, job: str) -> None:
    block = scan_secrets_guard(_repo(tmp_path, {".gitlab-ci.yml": TEMPLATE + job}))
    assert block["ci"]["present"] is False and block["ci"]["rules_unevaluated"] == []
    assert block["enforcement"] == {"available": True, "present": False, "source": None}


@pytest.mark.parametrize("job", ["", "secret_detection:\n  variables:\n    FOO: bar\n",
                                 "secret_detection:\n  when: always\n"])
def test_a_plain_template_job_counts(tmp_path: Path, job: str) -> None:
    assert scan_secrets_guard(_repo(tmp_path, {".gitlab-ci.yml": TEMPLATE + job}))["ci"]["tools"] == ["secret_detection"]
