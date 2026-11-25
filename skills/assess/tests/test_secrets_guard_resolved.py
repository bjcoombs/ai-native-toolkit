"""The secrets guard over GitLab's merged configuration, manual hooks, shell quoting and the assessed
commit (#1204). Each case either credits a scan the line reader missed, or reads not met or not
measured; none reads a false met."""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from test_secrets_guard import _repo, _states

from lib.secrets_guard import scan_secrets_guard
from lib.secrets_guard_lines import blank_quoted, shell_text

GITLAB = "https://gitlab.com/g/p.git"
GITHUB = "https://github.com/acme/widget.git"
TEMPLATE = "include:\n  - template: Jobs/Secret-Detection.gitlab-ci.yml\n"
SCAN_JOB = "secrets:\n  script:\n    - gitleaks detect\n"
PRE_COMMIT = "repos:\n  - repo: https://github.com/gitleaks/gitleaks\n    hooks:\n      - id: gitleaks\n"
RUN_ALL = "steps:\n  - run: pre-commit run --all-files\n"


def _gitlab(tmp_path: Path, files: dict[str, str]) -> dict[str, Any]:
    block: dict[str, Any] = dict(scan_secrets_guard(_repo(tmp_path, files, GITLAB)))
    return block


# --- 1. GitLab rules: never evaluated, never met -----------------------------------------


def test_a_scan_job_under_rules_is_not_measured_and_still_configured(tmp_path: Path) -> None:
    block = _gitlab(tmp_path, {".gitlab-ci.yml": "secrets:\n  rules:\n    - if: $CI_COMMIT_TAG\n"
                                                 "  script:\n    - gitleaks detect\n"})
    assert block["ci"]["present"] is False and block["ci"]["rules_unevaluated"] == ["gitleaks"]
    # Level 1 lists the configured scan; level 2 cannot say whether it runs on each change.
    assert _states(block) == (("met", None), ("not_measured", "gitlab_rules_unevaluated"))


def test_rules_an_include_adds_to_a_root_job_are_not_measured(tmp_path: Path) -> None:
    block = _gitlab(tmp_path, {".gitlab-ci.yml": "include:\n  - local: ci/rules.yml\n" + SCAN_JOB,
                               "ci/rules.yml": "secrets:\n  rules:\n    - if: $CI_COMMIT_TAG\n"})
    assert block["ci"]["present"] is False and block["ci"]["rules_unevaluated"] == ["gitleaks"]


def test_rules_an_extends_parent_brings_are_not_measured(tmp_path: Path) -> None:
    text = ".tags:\n  rules:\n    - if: $CI_COMMIT_TAG\nsecrets:\n  extends: .tags\n  script:\n    - gitleaks detect\n"
    block = _gitlab(tmp_path, {".gitlab-ci.yml": text})
    assert block["ci"]["present"] is False and block["ci"]["rules_unevaluated"] == ["gitleaks"]


def test_a_job_from_a_conditional_include_is_not_measured(tmp_path: Path) -> None:
    root = "include:\n  - local: ci/scan.yml\n    rules:\n      - if: $CI_COMMIT_TAG\n"
    block = _gitlab(tmp_path, {".gitlab-ci.yml": root, "ci/scan.yml": SCAN_JOB})
    assert block["ci"]["present"] is False and block["ci"]["rules_unevaluated"] == ["gitleaks"]


# --- 2. include overrides, settled by the merged configuration ----------------------------


@pytest.mark.parametrize("override", ["secrets:\n  when: never\n", "secrets:\n  when: manual\n",
                                      "secrets:\n  script:\n    - echo skipped\n",
                                      "secrets:\n  allow_failure: true\n"])
def test_a_root_override_of_an_included_scan_job_removes_it(tmp_path: Path, override: str) -> None:
    block = _gitlab(tmp_path, {".gitlab-ci.yml": "include:\n  - local: ci/scan.yml\n" + override,
                               "ci/scan.yml": SCAN_JOB})
    assert block["ci"]["present"] is False and block["ci"]["tools"] == []
    assert _states(block)[1] == ("unmet", None)


def test_a_settled_root_override_keeps_the_included_scan(tmp_path: Path) -> None:
    block = _gitlab(tmp_path, {".gitlab-ci.yml": "include:\n  - local: ci/scan.yml\nsecrets:\n  stage: test\n",
                               "ci/scan.yml": SCAN_JOB})
    assert block["ci"]["tools"] == ["gitleaks"] and _states(block)[1] == ("met", None)


def test_a_root_when_always_wins_over_an_included_never(tmp_path: Path) -> None:
    root = TEMPLATE + "  - local: ci/off.yml\nsecret_detection:\n  when: always\n"
    block = _gitlab(tmp_path, {".gitlab-ci.yml": root, "ci/off.yml": "secret_detection:\n  when: never\n"})
    assert block["ci"]["tools"] == ["secret_detection"]


def test_a_visible_job_extending_a_hidden_scan_counts(tmp_path: Path) -> None:
    text = ".scan:\n  script:\n    - gitleaks detect\nsecrets:\n  extends: .scan\n"
    assert _gitlab(tmp_path, {".gitlab-ci.yml": text})["ci"]["tools"] == ["gitleaks"]


def test_a_merged_job_beside_an_unread_include_is_not_measured(tmp_path: Path) -> None:
    root = "include:\n  - local: ci/scan.yml\n  - project: group/ci\n    file: x.yml\nsecrets:\n  stage: test\n"
    block = _gitlab(tmp_path, {".gitlab-ci.yml": root, "ci/scan.yml": SCAN_JOB})
    assert block["ci"]["present"] is False and block["enforcement"]["available"] is False


@pytest.mark.parametrize("extra", [
    "unit:\n  variables:\n    SECRET_DETECTION_DISABLED: \"true\"\n  script:\n    - make test\n",
    "unit:\n  rules:\n    - if: $CI_COMMIT_TAG\n      variables:\n        SECRET_DETECTION_DISABLED: \"true\"\n"
    "  script:\n    - make test\n",
    "variables:\n  SECRET_DETECTION_DISABLED: \"false\"\n",
])
def test_the_switch_outside_the_pipeline_or_the_job_does_not_turn_the_template_off(tmp_path: Path, extra: str) -> None:
    assert _gitlab(tmp_path, {".gitlab-ci.yml": TEMPLATE + extra})["ci"]["tools"] == ["secret_detection"]


@pytest.mark.parametrize("extra", [
    "variables:\n  SECRET_DETECTION_DISABLED: \"true\"\n",
    "variables:\n  SECRET_DETECTION_DISABLED:\n    value: \"true\"\n    description: off\n",
    "secret_detection:\n  variables:\n    SECRET_DETECTION_DISABLED: \"1\"\n",
    "variables:\n  SECRET_DETECTION_DISABLED: {value: \"true\", description: off}\n",
])
def test_the_switch_in_the_pipeline_or_the_job_turns_the_template_off(tmp_path: Path, extra: str) -> None:
    block = _gitlab(tmp_path, {".gitlab-ci.yml": TEMPLATE + extra})
    assert block["ci"]["present"] is False and _states(block)[1] == ("unmet", None)


def test_the_switch_in_an_included_file_turns_the_template_off(tmp_path: Path) -> None:
    block = _gitlab(tmp_path, {".gitlab-ci.yml": TEMPLATE + "  - local: ci/vars.yml\n",
                               "ci/vars.yml": "variables:\n  SECRET_DETECTION_DISABLED: \"true\"\n"})
    assert block["ci"]["present"] is False


def test_the_job_variable_wins_over_the_pipeline_variable(tmp_path: Path) -> None:
    text = (TEMPLATE + "variables:\n  SECRET_DETECTION_DISABLED: \"true\"\n"
            "secret_detection:\n  variables:\n    SECRET_DETECTION_DISABLED: \"false\"\n")
    assert _gitlab(tmp_path, {".gitlab-ci.yml": text})["ci"]["tools"] == ["secret_detection"]


def test_the_switch_under_workflow_rules_is_not_measured(tmp_path: Path) -> None:
    text = TEMPLATE + ("workflow:\n  rules:\n    - if: $CI_COMMIT_TAG\n      variables:\n"
                       "        SECRET_DETECTION_DISABLED: \"true\"\n    - when: always\n")
    block = _gitlab(tmp_path, {".gitlab-ci.yml": text})
    assert block["ci"]["present"] is False and block["ci"]["rules_unevaluated"] == ["secret_detection"]
    assert _states(block)[1] == ("not_measured", "gitlab_rules_unevaluated")


# --- 3. pre-commit manual hooks ------------------------------------------------------------


@pytest.mark.parametrize("cfg", [PRE_COMMIT + "        stages: [manual]\n",
                                 "default_stages: [manual]\n" + PRE_COMMIT,
                                 PRE_COMMIT.replace("      - id: gitleaks\n",
                                                    "      - stages: [manual]\n        id: gitleaks\n")])
def test_a_manual_only_hook_does_not_meet_level_1(tmp_path: Path, cfg: str) -> None:
    block = scan_secrets_guard(_repo(tmp_path, {".pre-commit-config.yaml": cfg}))
    assert block["pre_commit"]["present"] is False and block["configured"] is False
    assert _states(block)[0] == ("unmet", None)


def test_a_repository_with_one_commit_hook_still_meets_level_1(tmp_path: Path) -> None:
    cfg = PRE_COMMIT + "        stages: [manual]\n      - id: gitleaks-docker\n        stages: [pre-push]\n"
    block = scan_secrets_guard(_repo(tmp_path, {".pre-commit-config.yaml": cfg}))
    assert block["pre_commit"]["tools"] == ["gitleaks"] and _states(block)[0] == ("met", None)


@pytest.mark.parametrize("run", ["pre-commit run --hook-stage manual --all-files",
                                 "pre-commit run gitleaks --hook-stage=manual --all-files"])
def test_ci_running_the_manual_stage_runs_a_manual_hook(tmp_path: Path, run: str) -> None:
    root = _repo(tmp_path, {".pre-commit-config.yaml": PRE_COMMIT + "        stages: [manual]\n",
                            ".github/workflows/x.yml": f"steps:\n  - run: {run}\n"})
    block = scan_secrets_guard(root)
    assert block["ci"]["tools"] == ["gitleaks"] and block["configured"] is True


def test_ci_running_another_stage_skips_a_commit_hook(tmp_path: Path) -> None:
    root = _repo(tmp_path, {".pre-commit-config.yaml": PRE_COMMIT + "        stages: [pre-commit]\n",
                            ".github/workflows/x.yml": "steps:\n  - run: pre-commit run --hook-stage manual -a\n"})
    assert scan_secrets_guard(root)["ci"]["present"] is False


@pytest.mark.parametrize("hook_id", ['"gitleaks"', "'gitleaks'"])
def test_a_quoted_hook_id_is_run_by_ci(tmp_path: Path, hook_id: str) -> None:
    cfg = PRE_COMMIT.replace("id: gitleaks", f"id: {hook_id}")
    root = _repo(tmp_path, {".pre-commit-config.yaml": cfg,
                            ".github/workflows/x.yml": "steps:\n  - run: pre-commit run gitleaks --all-files\n"})
    assert scan_secrets_guard(root)["ci"]["tools"] == ["gitleaks"]


# --- 4. shell quoting and argument parsing ----------------------------------------------------


@pytest.mark.parametrize("run", [
    "gitleaks detect --log-opts=\"main|dev\"",       # a pipe inside quotes is an argument
    "gitleaks detect --log-opts='a||b'",
    "echo \"a || b\" && gitleaks detect",
    "git diff | gitleaks stdin",                      # last in the pipe: its status is the pipe's
    "git secrets --scan",                              # the git-subcommand form
    "'gitleaks detect --source .'",                    # a YAML-quoted value
])
def test_a_quoted_or_piped_scan_is_credited(tmp_path: Path, run: str) -> None:
    block = scan_secrets_guard(_repo(tmp_path, {".github/workflows/x.yml": f"steps:\n  - run: {run}\n"}, GITHUB))
    assert block["ci"]["present"] is True


@pytest.mark.parametrize("run", [
    "echo 'a; gitleaks detect'",                      # an argument, not a command
    "echo \"run gitleaks detect && done\"",
    "'gitleaks detect | tee out.txt'",                 # a YAML-quoted value: its pipe is the shell's
    "\"gitleaks detect || true\"",
    "gitleaks detect | tee out.txt",
    "git secrets --install",
    "\"gitleaks detect\" | tee out.txt",
])
def test_a_quoted_argument_or_masked_scan_is_not_credited(tmp_path: Path, run: str) -> None:
    block = scan_secrets_guard(_repo(tmp_path, {".github/workflows/x.yml": f"steps:\n  - run: {run}\n"}))
    assert block["ci"]["present"] is False


def test_a_yaml_quoted_gitlab_script_item_counts(tmp_path: Path) -> None:
    text = "secrets:\n  script:\n    - \"gitleaks detect --redact\"\n"
    assert _gitlab(tmp_path, {".gitlab-ci.yml": text})["ci"]["tools"] == ["gitleaks"]


def test_a_flow_sequence_keeps_its_operators(tmp_path: Path) -> None:
    assert blank_quoted('script: ["gitleaks detect | tee x"]') == 'script: ["gitleaks detect | tee x"]'
    assert blank_quoted("echo 'a|b' | x") == "echo '___' | x"
    assert shell_text("    - 'it''s gitleaks'") == "    - it's gitleaks"
    assert shell_text('    - "say \\"hi\\""') == '    - say "hi"'
    assert blank_quoted("echo 'open | x") == "echo 'open | x"  # unclosed: read as written
    assert blank_quoted('a \\| b "c\\"|d"') == 'a \\_ b "_____"'


def test_a_hook_stage_flag_without_a_value_reads_the_commit_stage() -> None:
    from lib.secrets_guard_hooks import hook_stage
    assert hook_stage(["--hook-stage"]) == "pre-commit" and hook_stage(["--hook-stage", "'manual'"]) == "manual"


def test_a_step_that_opens_with_env_still_runs_its_scan(tmp_path: Path) -> None:
    text = "steps:\n  - env:\n      GITLEAKS_CONFIG: .gitleaks.toml\n    run: gitleaks detect\n"
    assert scan_secrets_guard(_repo(tmp_path, {".github/workflows/x.yml": text}))["ci"]["tools"] == ["gitleaks"]


@pytest.mark.parametrize(("uses", "tools"), [("GitGuardian/ggshield/actions/secret@v1.33.0", ["ggshield"]),
                                             ("GitGuardian/ggshield/actions/iac@v1.33.0", [])])
def test_gitguardians_maintained_secret_action_counts(tmp_path: Path, uses: str, tools: list[str]) -> None:
    text = f"steps:\n  - uses: {uses}\n"
    assert scan_secrets_guard(_repo(tmp_path, {".github/workflows/x.yml": text}))["ci"]["tools"] == tools


# --- 5. CI at the assessed commit, read as the other CI readers read it ----------------------


def test_an_untracked_workflow_or_hook_config_is_not_read(tmp_path: Path) -> None:
    root = _repo(tmp_path, {"README.md": "x\n"}, GITLAB)
    _repo(root, {".github/workflows/x.yml": "steps:\n  - run: gitleaks detect\n",
                 ".pre-commit-config.yaml": PRE_COMMIT, ".gitlab-ci.yml": SCAN_JOB})
    block = scan_secrets_guard(root)
    assert block["ci"]["present"] is False and block["pre_commit"]["present"] is False
    assert _states(block) == (("unmet", None), ("unmet", None))
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True, capture_output=True)
    tracked = scan_secrets_guard(root)
    assert tracked["pre_commit"]["present"] is True
    assert tracked["ci"]["files"] == [".github/workflows/x.yml", ".gitlab-ci.yml"]


def test_a_local_composite_action_runs_its_scan(tmp_path: Path) -> None:
    action = "runs:\n  using: composite\n  steps:\n    - shell: bash\n      run: gitleaks detect --source .\n"
    root = _repo(tmp_path, {".github/actions/scan/action.yml": action,
                            ".github/workflows/x.yml": "jobs:\n  s:\n    steps:\n      - uses: ./.github/actions/scan\n"},
                 GITHUB)
    assert scan_secrets_guard(root)["ci"]["tools"] == ["gitleaks"]


def test_a_masked_composite_call_stays_masked(tmp_path: Path) -> None:
    action = "runs:\n  using: composite\n  steps:\n    - shell: bash\n      run: gitleaks detect --source .\n"
    wf = "jobs:\n  s:\n    steps:\n      - uses: ./.github/actions/scan\n        continue-on-error: true\n"
    root = _repo(tmp_path, {".github/actions/scan/action.yml": action, ".github/workflows/x.yml": wf}, GITHUB)
    assert scan_secrets_guard(root)["ci"]["present"] is False


def test_an_env_command_is_read_through(tmp_path: Path) -> None:
    wf = "jobs:\n  s:\n    env:\n      SCANNER: gitleaks\n    steps:\n      - run: $SCANNER detect --source .\n"
    assert scan_secrets_guard(_repo(tmp_path, {".github/workflows/x.yml": wf}, GITHUB))["ci"]["tools"] == ["gitleaks"]
