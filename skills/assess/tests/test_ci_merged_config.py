"""GitLab's merged configuration (issue #1180): include overrides, extends: and default:.

The resolver (``lib.ci_includes.merged_config``) and its line view (``lib.ci_merged_view``),
read through ``ci_checks``. Each false-credit case has a mirror that still reads as before, and
every case the resolver cannot settle reads as not measured, never as met.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from lib.ci_checks import scan_ci_checks
from lib.ci_includes import TOOLKIT_GATE_JOBS, include_items, merged_config, resolve_includes
from lib.ci_merged_view import merged_view

REPO = Path(__file__).resolve().parents[3]
LINT = "lint:\n  script:\n    - ruff check .\n"


def _repo(root: Path, files: dict[str, str]) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, check=True)
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-qm", "c"],
                   cwd=root, check=True)
    return root


def _lint(tmp_path: Path, files: dict[str, str], ghsync_root: Path | None = None) -> tuple[object, object]:
    block = scan_ci_checks(_repo(tmp_path / "repo", files), ghsync_root)
    return block["lint_runs"], block["lint_basis"]


# --- include overrides (the root file and a later include win) -----------------------------


@pytest.mark.parametrize(("root", "reading"), [
    pytest.param("lint:\n  when: manual\n", (False, "not_enforced"), id="root-when-manual"),
    pytest.param("lint:\n  allow_failure: true\n", (False, "not_enforced"), id="root-allow-failure"),
    pytest.param("lint:\n  rules:\n    - if: $CI_PIPELINE_SOURCE == \"schedule\"\n",
                 (False, "not_enforced"), id="root-schedule-rules"),
    pytest.param("lint:\n  script:\n    - echo replaced\n", (False, "no_lint_command"), id="root-script"),
    pytest.param("lint:\n  rules:\n    - if: $CUSTOM\n", (None, "unknown_trigger"), id="root-custom-rule"),
    # Mirrors: an override that leaves the job enforced, and a job of another name.
    pytest.param("lint:\n  stage: test\n", (True, "lint_command"), id="root-stage-only"),
    pytest.param("lint:\n  allow_failure: false\n", (True, "lint_command"), id="root-allow-failure-false"),
    pytest.param("other:\n  when: manual\n  script:\n    - echo x\n", (True, "lint_command"), id="other-job"),
])
def test_a_root_override_of_an_included_job_is_read_merged(tmp_path: Path, root: str,
                                                           reading: tuple[object, object]) -> None:
    files = {".gitlab-ci.yml": "include:\n  - local: ci/lint.yml\n" + root, "ci/lint.yml": LINT}
    assert _lint(tmp_path, files) == reading


def test_a_root_script_over_an_included_manual_job_runs_the_root_script_manually(tmp_path: Path) -> None:
    files = {".gitlab-ci.yml": "include:\n  - local: ci/lint.yml\n" + LINT,
             "ci/lint.yml": "lint:\n  when: manual\n  script:\n    - echo tpl\n"}
    assert _lint(tmp_path, files) == (False, "not_enforced")


def test_a_later_include_overrides_an_earlier_one(tmp_path: Path) -> None:
    files = {".gitlab-ci.yml": "include:\n  - local: a.yml\n  - local: b.yml\n",
             "a.yml": LINT, "b.yml": "lint:\n  when: manual\n"}
    assert _lint(tmp_path, files) == (False, "not_enforced")
    # Mirror: the manual file first, the job file last: the later include's keys win only
    # where it sets them, so the job stays manual.
    files[".gitlab-ci.yml"] = "include:\n  - local: b.yml\n  - local: a.yml\n"
    assert _lint(tmp_path / "mirror", files) == (False, "not_enforced")


def test_a_file_overrides_what_it_includes(tmp_path: Path) -> None:
    files = {".gitlab-ci.yml": "include:\n  - local: a.yml\n",
             "a.yml": "include:\n  - local: b.yml\nlint:\n  allow_failure: false\n",
             "b.yml": "lint:\n  allow_failure: true\n  script:\n    - ruff check .\n"}
    assert _lint(tmp_path, files) == (True, "lint_command")
    config = merged_config(tmp_path / "repo")
    assert config is not None
    chain = config.jobs["lint"].keys["allow_failure"]
    assert [config.sources[k.source].splitlines()[k.start].strip() for k in chain] == [
        "allow_failure: true", "allow_failure: false"]   # b.yml first: a.yml includes it


def test_a_project_include_override_is_read_from_the_clone(tmp_path: Path) -> None:
    ghsync = tmp_path / "ghsync"
    _repo(ghsync / "grp" / "tpl" / "tpl-main", {"lint.yml": LINT})
    files = {".gitlab-ci.yml": "include:\n  - project: grp/tpl\n    file: /lint.yml\nlint:\n  when: manual\n"}
    assert _lint(tmp_path, files, ghsync) == (False, "not_enforced")


def test_maps_deep_merge_and_lists_replace(tmp_path: Path) -> None:
    files = {".gitlab-ci.yml": "include:\n  - local: ci/lint.yml\nlint:\n  variables:\n    B: '2'\n",
             "ci/lint.yml": "lint:\n  variables:\n    A: '1'\n  script:\n    - ruff check .\n"}
    repo = _repo(tmp_path / "repo", files)
    config = merged_config(repo)
    assert config is not None
    view = merged_view(config)
    assert "    A: '1'" in view.rendered and "    B: '2'" in view.rendered
    assert view.unsettled == frozenset()
    assert scan_ci_checks(repo)["lint_runs"] is True


# --- cases the resolver cannot settle read as not measured ------------------------------


def test_an_override_from_a_conditional_include_is_not_settled(tmp_path: Path) -> None:
    files = {".gitlab-ci.yml": "include:\n  - local: ci/lint.yml\n"
                               "  - local: ci/manual.yml\n    rules:\n      - if: $NIGHTLY\n",
             "ci/lint.yml": LINT, "ci/manual.yml": "lint:\n  when: manual\n"}
    assert _lint(tmp_path, files) == (None, "unknown_trigger")
    config = merged_config(tmp_path / "repo")
    assert config is not None and config.jobs["lint"].unresolved == "conditional_include"
    assert include_items(files[".gitlab-ci.yml"])[0][1].conditional is True


@pytest.mark.parametrize(("extra", "reason"), [
    pytest.param({"ci/lint.yml": ".t: &t\n  allow_failure: true\nlint:\n  <<: *t\n  script:\n    - ruff check .\n"},
                 "unreadable", id="anchor-merge"),
    pytest.param({"ci/lint.yml": "lint:\n  when: $[[ inputs.when ]]\n  script:\n    - ruff check .\n"},
                 "unreadable", id="interpolated"),
    pytest.param({"ci/lint.yml": "lint:\n  extends: .missing\n  script:\n    - ruff check .\n"},
                 "extends_missing", id="extends-missing"),
    pytest.param({"ci/lint.yml": "lint:\n  extends: lint\n  script:\n    - ruff check .\n"},
                 "extends_cycle", id="extends-cycle"),
])
def test_an_unreadable_merged_job_reads_not_measured(tmp_path: Path, extra: dict[str, str],
                                                     reason: str) -> None:
    files = {".gitlab-ci.yml": "include:\n  - local: ci/lint.yml\nlint:\n  stage: test\n", **extra}
    assert _lint(tmp_path, files) == (None, "unknown_trigger")
    config = merged_config(tmp_path / "repo")
    assert config is not None and config.jobs["lint"].unresolved == reason


def test_a_repeated_include_leaves_its_jobs_unsettled(tmp_path: Path) -> None:
    files = {".gitlab-ci.yml": "include:\n  - local: a.yml\n  - local: b.yml\n  - local: a.yml\n",
             "a.yml": LINT, "b.yml": "lint:\n  when: manual\n"}
    assert _lint(tmp_path, files) == (None, "unknown_trigger")


def test_an_unread_include_marks_every_job_open(tmp_path: Path) -> None:
    files = {".gitlab-ci.yml": "include:\n  - template: Security/SAST.gitlab-ci.yml\n" + LINT}
    repo = _repo(tmp_path / "repo", files)
    config = merged_config(repo)
    assert config is not None and config.open and config.jobs["lint"].open
    assert config.jobs["lint"].unresolved is None
    # ci_checks keeps #766's reading of a job not merged from several read files.
    assert scan_ci_checks(repo)["lint_runs"] is True


def test_the_toolkit_gate_template_settles_every_job_but_its_own() -> None:
    template = (REPO / "templates" / "assess-gate.yml").read_text(encoding="utf-8")
    jobs = {m.group(1) for m in re.finditer(r"^([\w.-]+):", template.split("\n---\n", 1)[-1], re.M)}
    assert jobs - {"spec", "include", "variables", "stages", "workflow", "default"} == TOOLKIT_GATE_JOBS


def test_a_gate_include_with_a_same_named_job_is_not_settled(tmp_path: Path) -> None:
    gate = "https://raw.githubusercontent.com/bjcoombs/ai-native-toolkit/v1.0.0/templates/assess-gate.yml"
    files = {".gitlab-ci.yml": f"include:\n  - remote: {gate}\nassess-gate:\n  script:\n    - ruff check .\n"
                               + LINT}
    config = merged_config(_repo(tmp_path / "repo", files))
    assert config is not None and not config.open
    assert config.jobs["assess-gate"].unresolved == "include_order"
    assert config.jobs["lint"].unresolved is None


# --- extends: and default: --------------------------------------------------------------


@pytest.mark.parametrize(("text", "reading"), [
    pytest.param(".t:\n  allow_failure: true\nlint:\n  extends: .t\n  script:\n    - ruff check .\n",
                 (False, "not_enforced"), id="template-allow-failure"),
    pytest.param(".t:\n  script:\n    - ruff check .\nlint:\n  extends: .t\n  when: manual\n",
                 (False, "not_enforced"), id="template-script-manual-job"),
    pytest.param(".t:\n  script:\n    - ruff check .\nlint:\n  extends: .t\n", (True, "lint_command"),
                 id="template-script"),
    pytest.param(".a:\n  allow_failure: true\n.b:\n  allow_failure: false\nlint:\n  extends: [.a, .b]\n"
                 "  script:\n    - ruff check .\n", (True, "lint_command"), id="last-parent-wins"),
    pytest.param(".t:\n  allow_failure: true\nlint:\n  extends: .t\n  allow_failure: false\n"
                 "  script:\n    - ruff check .\n", (True, "lint_command"), id="own-key-wins"),
])
def test_extends_is_read_with_every_key(tmp_path: Path, text: str, reading: tuple[object, object]) -> None:
    assert _lint(tmp_path, {".gitlab-ci.yml": text}) == reading


def test_extends_across_an_include(tmp_path: Path) -> None:
    files = {".gitlab-ci.yml": "include:\n  - local: t.yml\nlint:\n  extends: .t\n  script:\n    - ruff check .\n",
             "t.yml": ".t:\n  when: manual\n"}
    assert _lint(tmp_path, files) == (False, "not_enforced")
    files["t.yml"] = ".t:\n  stage: test\n"
    assert _lint(tmp_path / "mirror", files) == (True, "lint_command")


@pytest.mark.parametrize(("text", "reading"), [
    pytest.param("default:\n  before_script:\n    - ruff check .\njob:\n  before_script:\n    - echo own\n"
                 "  script:\n    - echo x\n", (False, "no_lint_command"), id="every-job-overrides"),
    pytest.param("default:\n  before_script:\n    - ruff check .\njob:\n  when: manual\n  script:\n    - echo x\n",
                 (False, "not_enforced"), id="inherited-by-manual-job"),
    pytest.param("default:\n  before_script:\n    - ruff check .\njob:\n  script:\n    - echo x\n",
                 (True, "lint_command"), id="inherited"),
    pytest.param("default:\n  before_script:\n    - ruff check .\njob:\n  inherit:\n    default: false\n"
                 "  script:\n    - echo x\n", (False, "no_lint_command"), id="inherit-false"),
    pytest.param("default:\n  before_script:\n    - ruff check .\njob:\n  inherit:\n    default: [image]\n"
                 "  script:\n    - echo x\n", (False, "no_lint_command"), id="inherit-list"),
    pytest.param("default:\n  before_script:\n    - ruff check .\njob:\n  inherit:\n    default: sometimes\n"
                 "  script:\n    - echo x\n", (None, "unknown_trigger"), id="inherit-unread"),
])
def test_default_script_keys_run_only_in_the_jobs_that_take_them(
        tmp_path: Path, text: str, reading: tuple[object, object]) -> None:
    assert _lint(tmp_path, {".gitlab-ci.yml": text}) == reading


def test_a_single_file_without_merges_is_unchanged(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "repo", {".gitlab-ci.yml": "stages: [test]\n" + LINT})
    config = merged_config(repo)
    assert config is not None
    view = merged_view(config)
    assert view.rendered == "" and view.texts == config.sources
    assert resolve_includes(repo).slots == ()


def test_the_root_workflow_applies_to_a_merged_job(tmp_path: Path) -> None:
    files = {".gitlab-ci.yml": "workflow:\n  rules:\n    - if: $CI_PIPELINE_SOURCE == \"schedule\"\n"
                               "include:\n  - local: ci/lint.yml\nlint:\n  stage: test\n",
             "ci/lint.yml": LINT}
    assert _lint(tmp_path, files) == (False, "not_enforced")


def test_a_flow_map_beside_a_block_map_is_not_merged(tmp_path: Path) -> None:
    files = {".gitlab-ci.yml": "include:\n  - local: ci/lint.yml\nlint:\n  variables: {B: '2'}\n",
             "ci/lint.yml": "lint:\n  variables:\n    A: '1'\n  script:\n    - ruff check .\n"}
    assert _lint(tmp_path, files) == (None, "unknown_trigger")


def test_keys_written_at_another_indent_are_rendered_at_one(tmp_path: Path) -> None:
    files = {".gitlab-ci.yml": "include:\n  - local: ci/lint.yml\nlint:\n  allow_failure: true\n",
             "ci/lint.yml": "lint:\n      script:\n          - ruff check .\n"}
    repo = _repo(tmp_path / "repo", files)
    config = merged_config(repo)
    assert config is not None
    assert merged_view(config).rendered.splitlines() == [
        "job0:", "  script:", "      - ruff check .", "  allow_failure: true"]
    assert scan_ci_checks(repo)["lint_runs"] is False


_DEEP = "".join(f".t{i}:\n  extends: .t{i + 1}\n" for i in range(12)) + ".t12:\n  stage: test\n"


@pytest.mark.parametrize(("text", "job", "reason", "inherited"), [
    pytest.param("lint:\n  script: [a]\n  script: [b]\n", "lint", "unreadable", frozenset(), id="duplicate-key"),
    pytest.param("lint:\n  extends: {a: b}\n", "lint", "unreadable", frozenset(), id="extends-flow-map"),
    pytest.param(".a:\n  stage: x\nlint:\n  extends:\n    - .a\n", "lint", None, frozenset(), id="extends-block-list"),
    pytest.param(_DEEP + "lint:\n  extends: .t0\n", "lint", "extends_depth", frozenset(), id="extends-depth"),
    pytest.param("default:\n  before_script: [a]\nbefore_script: [b]\nlint:\n  script: [c]\n", "lint",
                 "default_unresolved", frozenset({"before_script"}), id="default-and-legacy"),
    pytest.param("before_script: [b]\nlint:\n  script: [c]\n", "lint", None, frozenset({"before_script"}),
                 id="legacy-before-script"),
    pytest.param("default:\n  image: x\n  tags: [t]\nlint:\n  inherit:\n    variables: false\n"
                 "    default:\n      - image\n  script: [c]\n", "lint", None, frozenset({"image"}),
                 id="inherit-block-list"),
    pytest.param("default:\n  image: x\nlint:\n  inherit:\n    variables: false\n  script: [c]\n", "lint", None,
                 frozenset({"image"}), id="inherit-without-default"),
    pytest.param("default:\n  image: x\nlint:\n  inherit: {default: false}\n  script: [c]\n", "lint", "inherit",
                 frozenset({"image"}), id="inherit-flow"),
])
def test_merged_config_reasons(tmp_path: Path, text: str, job: str, reason: str | None,
                               inherited: frozenset[str]) -> None:
    config = merged_config(_repo(tmp_path / "repo", {".gitlab-ci.yml": text}))
    assert config is not None
    assert (config.jobs[job].unresolved, config.jobs[job].inherited) == (reason, inherited)


def test_an_unnamed_top_level_entry_opens_the_config(tmp_path: Path) -> None:
    text = "spec:\n  inputs:\n    x: {}\n---\n$[[ inputs.x ]]-lint:\n  script: [c]\nlint:\n  script: [d]\n"
    config = merged_config(_repo(tmp_path / "repo", {".gitlab-ci.yml": text}))
    assert config is not None and config.open and set(config.jobs) == {"lint"}


def test_no_gitlab_file_has_no_merged_config(tmp_path: Path) -> None:
    assert merged_config(_repo(tmp_path / "repo", {"README.md": "x\n"})) is None


@pytest.mark.parametrize("default", [
    pytest.param("before_script:\n  - pip install ruff\n", id="legacy"),
    pytest.param("default:\n  before_script:\n    - pip install ruff\n", id="default"),
])
def test_a_job_merged_only_by_default_keeps_its_reading_when_anchored(tmp_path: Path, default: str) -> None:
    text = default + ".common: &common\n  tags: [docker]\nlint:\n  <<: *common\n  script:\n    - ruff check .\n"
    repo = _repo(tmp_path / "repo", {".gitlab-ci.yml": text})
    block = scan_ci_checks(repo)
    assert (block["lint_runs"], block["enforced_check_runs"]) == (True, True)   # as on main
    config = merged_config(repo)
    assert config is not None
    assert merged_view(config).rendered == ""


@pytest.mark.parametrize(("text", "reading"), [
    pytest.param(".base:\n  image: python:3.12\nlint:\n  extends: .base\n  script:\n  - ruff check .\n",
                 (True, "lint_command"), id="script"),
    pytest.param(".base:\n  allow_failure: true\nlint:\n  extends:\n  - .base\n  script:\n  - ruff check .\n",
                 (False, "not_enforced"), id="extends-list"),
    pytest.param("default:\n  before_script:\n  - ruff check .\njob:\n  inherit:\n    default:\n    - image\n"
                 "  script:\n  - echo x\n", (False, "no_lint_command"), id="inherit-list"),
])
def test_a_sequence_at_its_key_indent_is_read(tmp_path: Path, text: str, reading: tuple[object, object]) -> None:
    assert _lint(tmp_path, {".gitlab-ci.yml": text}) == reading
    config = merged_config(tmp_path / "repo")
    assert config is not None and all(j.unresolved is None for j in config.jobs.values())


def test_a_nested_sequence_at_its_key_indent_merges(tmp_path: Path) -> None:
    files = {".gitlab-ci.yml": "include:\n  - local: ci/lint.yml\nlint:\n  cache:\n    key: k\n",
             "ci/lint.yml": "lint:\n  cache:\n    paths:\n    - .cache\n  script:\n    - ruff check .\n"}
    repo = _repo(tmp_path / "repo", files)
    config = merged_config(repo)
    assert config is not None
    view = merged_view(config)
    assert view.unsettled == frozenset()
    assert "    - .cache" in view.rendered and "    key: k" in view.rendered
