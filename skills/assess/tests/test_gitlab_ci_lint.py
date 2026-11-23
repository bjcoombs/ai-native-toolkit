"""Tests for lib/hosting/gitlab_ci_lint.py: GitLab's merged CI configuration through the CI lint API
under ``--platform`` (issue #796, #766 part 2), and how ci_checks and review_automation read it."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest

from assess_test_support import skip_without_posix_shell

import lib.hosting.gitlab_ci_lint as gitlab_ci_lint
from lib.ci_checks import scan_ci_checks
from lib.counts_only.content_free import counts_only_scans
from lib.merged_ci import NOT_NEEDED, NOT_READ, MergedCi, as_merged
from lib.review_automation import scan_review_automation
from lib.signals import collect
from test_fleet import make_repo
from test_fleet_cli import fleet_cli

INCLUDE = "include:\n  - project: {project}\n    file: /templates/test.yml\n"
MERGED = "unit:\n  script:\n    - pytest -q\n"

# glab: auth passes; any ci/lint endpoint answers LINT_FILE (or exits as told); everything else
# 404s. Before answering it records every file under $LEAK_DIRS that holds the merged text.
GLAB = """#!/bin/sh
grep -rl 'pytest -q' $LEAK_DIRS >> "$GLAB_DIR/leak.log" 2>/dev/null
echo "$*" >> "$GLAB_DIR/log"
case "$*" in
  "auth status"*) exit 0 ;;
  *"/ci/lint"*)
    if [ -f "$GLAB_DIR/refuse" ]; then echo "glab: 403 Forbidden (HTTP 403)" >&2; exit 1; fi
    cat "$GLAB_DIR/lint.json"; exit 0 ;;
esac
echo "glab: 404 Not Found (HTTP 404)" >&2; exit 1
"""


@pytest.fixture
def glab(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    skip_without_posix_shell()
    bindir, data, scratch = tmp_path / "bin", tmp_path / "glab", tmp_path / "tmp"
    for d in (bindir, data, scratch):
        d.mkdir()
    (bindir / "glab").write_text(GLAB, encoding="utf-8")
    (bindir / "glab").chmod(0o755)
    (bindir / "gh").write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    (bindir / "gh").chmod(0o755)
    lint(data, {"valid": True, "merged_yaml": MERGED, "errors": [], "warnings": []})
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("GLAB_DIR", str(data))
    monkeypatch.setenv("LEAK_DIRS", str(scratch))
    monkeypatch.setenv("TMPDIR", str(scratch))
    monkeypatch.setattr(tempfile, "tempdir", str(scratch))
    monkeypatch.chdir(tmp_path)
    gitlab_ci_lint.resolve_includes.cache_clear()
    return data


def lint(data: Path, answer: object) -> None:
    (data / "lint.json").write_text(json.dumps(answer), encoding="utf-8")


def calls(data: Path, name: str = "log") -> list[str]:
    path = data / name
    return path.read_text(encoding="utf-8").splitlines() if path.exists() else []


def _root(tmp_path: Path) -> Path:
    """app includes an uncloned template project; both includes a cloned one (no check)."""
    root = tmp_path / "gl"
    make_repo(root, "grp/app", files={"src/a.py": "x = 1\n",
                                      ".gitlab-ci.yml": INCLUDE.format(project="grp/ci-templates")},
              remote="https://gitlab.com/grp/app.git")
    make_repo(root, "grp/both", files={"src/a.py": "x = 1\n",
                                       ".gitlab-ci.yml": INCLUDE.format(project="grp/tpl2")},
              remote="https://gitlab.com/grp/both.git")
    make_repo(root, "grp/tpl2", files={"templates/test.yml": "docs:\n  script:\n    - make docs\n"})
    return root


def _app(root: Path) -> Path:
    return root / "grp/app/app-main"


# --- the read ------------------------------------------------------------------


def test_the_lint_api_is_asked_by_project_and_commit_and_never_cached(glab, tmp_path, monkeypatch):
    root = _root(tmp_path)
    cache = tmp_path / "tmp" / "cache"
    cache.mkdir()
    monkeypatch.setenv("ASSESS_PLATFORM_CACHE", str(cache))
    merged = gitlab_ci_lint.read_merged_ci(_app(root), root)
    assert merged.status == "read" and merged.text == MERGED and merged.read
    lint_calls = [c for c in calls(glab) if "/ci/lint" in c]
    assert len(lint_calls) == 1
    argv = lint_calls[0].split()
    assert argv[:3] == ["api", "--hostname", "gitlab.com"]
    assert argv[3].startswith("projects/grp%2Fapp/ci/lint?content_ref=")
    assert len(argv[3].rsplit("=", 1)[1]) == 40  # the HEAD commit the clone holds
    assert not [p for p in cache.iterdir() if b"pytest -q" in p.read_bytes()]


def test_a_template_resolved_offline_is_not_asked_for(glab, tmp_path):
    root = _root(tmp_path)
    assert gitlab_ci_lint.read_merged_ci(root / "grp/both/both-main", root) == NOT_NEEDED
    assert calls(glab) == []


@pytest.mark.parametrize(("answer", "status"), [
    ({"valid": False, "merged_yaml": None, "errors": ["jobs config should contain a job"]}, "invalid"),
    ({"valid": True}, "invalid"),
    ([], "invalid"),
])
def test_an_invalid_answer_is_not_read(glab, tmp_path, answer, status):
    lint(glab, answer)
    root = _root(tmp_path)
    merged = gitlab_ci_lint.read_merged_ci(_app(root), root)
    assert (merged.status, merged.text, merged.read) == (status, None, False)


def test_a_refused_read_keeps_the_reason_code_only(glab, tmp_path):
    (glab / "refuse").write_text("", encoding="utf-8")
    root = _root(tmp_path)
    assert gitlab_ci_lint.read_merged_ci(_app(root), root) == MergedCi("unavailable", "no_access")


def test_a_repository_without_a_gitlab_remote_asks_nothing(glab, tmp_path):
    root = tmp_path / "gh"
    app = make_repo(root, "org/app", files={".gitlab-ci.yml": INCLUDE.format(project="grp/x")},
                    remote="https://github.com/org/app.git")
    assert gitlab_ci_lint.read_merged_ci(app, root) == MergedCi("unavailable", "not_gitlab")
    assert calls(glab) == []


def test_include_sources_reads_components_and_project_files():
    includes = [
        {"type": "component", "location": "gitlab.com/tools/ai-review/review@1.0"},
        {"type": "file", "location": "/t.yml", "extra": {"project": "grp/ci-templates", "ref": "main"}},
        {"type": "file", "location": "/u.yml", "extra": {"project": "grp/ci-templates"}},
        {"type": "local", "location": "/ci/a.yml"},
        {"type": "template", "location": "Auto-DevOps.gitlab-ci.yml"},
        "junk", {"type": "file", "extra": None},
    ]
    assert gitlab_ci_lint.include_sources(includes) == (
        "gitlab.com/tools/ai-review/review@1.0", "grp/ci-templates")
    assert gitlab_ci_lint.include_sources(None) == ()


def test_as_merged_treats_anything_else_as_not_read():
    assert as_merged({"available": False, "reason": "network"}) == NOT_READ
    assert as_merged(None) == NOT_READ
    assert gitlab_ci_lint.no_merged_ci(Path("."), None) == NOT_READ


# --- ci_checks and review_automation --------------------------------------------


def _offline_app(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "gl"
    app = make_repo(root, "grp/app", files={".gitlab-ci.yml": INCLUDE.format(project="grp/missing")})
    return root, app


def test_ci_checks_reads_the_merged_check_where_offline_was_unknown(tmp_path):
    root, app = _offline_app(tmp_path)
    assert scan_ci_checks(app, root)["test_or_lint_configured"] is None
    block = scan_ci_checks(app, root, MergedCi("read", text=MERGED))
    assert (block["test_or_lint_configured"], block["basis"]) == (True, "merged_config")
    assert (block["merged_config"], block["merged_check_commands"]) == ("read", 1)
    assert (block["check_commands"], block["included_check_commands"]) == (0, 0)


def test_ci_checks_reads_gitlabs_yaml_dump_shape(tmp_path):
    """GitLab dumps merged_yaml with a document marker and indentless lists."""
    root, app = _offline_app(tmp_path)
    block = scan_ci_checks(app, root, MergedCi("read", text="---\nunit:\n  script:\n  - pytest\n"))
    assert (block["test_or_lint_configured"], block["merged_check_commands"]) == (True, 1)


def test_ci_checks_reads_false_when_the_merged_config_holds_no_check(tmp_path):
    root, app = _offline_app(tmp_path)
    block = scan_ci_checks(app, root, MergedCi("read", text="build:\n  script:\n    - make\n"))
    assert (block["test_or_lint_configured"], block["basis"]) == (False, "no_check_command")


@pytest.mark.parametrize("ci", [
    "include:\n  - project: grp/missing\n    file: /templates/test.yml\n    rules:\n      - if: $X\n",
    "include:\n  - {project: grp/missing, file: /t.yml, rules: [{if: $X}]}\n",
])
def test_a_conditional_include_keeps_a_check_free_merge_unknown(tmp_path, ci):
    """The lint API may leave a ``rules:`` include out of the merge: no check is not false."""
    root = tmp_path / "gl"
    app = make_repo(root, "grp/app", files={".gitlab-ci.yml": ci})
    block = scan_ci_checks(app, root, MergedCi("read", text="build:\n  script:\n    - make\n"))
    assert (block["test_or_lint_configured"], block["basis"]) == (None, "merged_config_partial")
    block = scan_ci_checks(app, root, MergedCi("read", text=MERGED))
    assert (block["test_or_lint_configured"], block["basis"]) == (True, "merged_config")


def test_a_rules_include_inside_a_template_read_offline_keeps_the_merge_unknown(tmp_path):
    root = tmp_path / "gl"
    make_repo(root, "grp/tpl", files={"templates/test.yml": (
        "include:\n  - project: grp/missing\n    file: /x.yml\n    rules:\n      - if: $X\n")})
    app = make_repo(root, "grp/app", files={".gitlab-ci.yml": INCLUDE.format(project="grp/tpl")})
    block = scan_ci_checks(app, root, MergedCi("read", text="build:\n  script:\n    - make\n"))
    assert (block["test_or_lint_configured"], block["basis"]) == (None, "merged_config_partial")


@pytest.mark.parametrize(("own", "merged"), [
    # a trigger: job in the repository's own file: a child pipeline
    (INCLUDE.format(project="grp/missing") + "child:\n  trigger:\n    include: ci/child.yml\n",
     "build:\n  script:\n    - make\n"),
    # a trigger: job in the repository's own file: a multi-project pipeline, scalar form
    (INCLUDE.format(project="grp/missing") + "deploy:\n  trigger: grp/deploy\n",
     "build:\n  script:\n    - make\n"),
    # a trigger: job only the merge shows (it came from the uncloned template)
    (INCLUDE.format(project="grp/missing"),
     "build:\n  script:\n    - make\ndeploy:\n  trigger:\n    project: grp/deploy\n"),
])
def test_a_child_pipeline_keeps_a_check_free_merge_unknown(tmp_path, own, merged):
    """A child or downstream pipeline is not expanded in the merge: no check is not false."""
    root = tmp_path / "gl"
    app = make_repo(root, "grp/app", files={".gitlab-ci.yml": own})
    block = scan_ci_checks(app, root, MergedCi("read", text=merged))
    assert (block["test_or_lint_configured"], block["basis"]) == (None, "merged_config_partial")
    assert block["merged_config"] == "read"


def test_child_pipeline_reads_a_job_key_only():
    from lib.ci_checks import child_pipeline
    assert child_pipeline("child:\n  trigger:\n    include: a.yml\n")
    assert not child_pipeline("trigger: x\nbuild:\n  script:\n    - echo trigger: no\n")


def test_a_top_level_trigger_job_after_a_blank_line_is_not_a_child_pipeline():
    # `^\s+` crossed the blank line's newline and read the job name as a `trigger:` key (#958).
    from lib.ci_checks import child_pipeline
    assert not child_pipeline("build:\n  script: make\n\ntrigger:\n  script: x\n")
    assert child_pipeline("build:\n  script: make\n\nchild:\n\n  trigger:\n    include: a.yml\n")


def test_include_sources_records_a_padded_duplicate_once():
    includes = [{"type": "file", "extra": {"project": "grp/x"}},
                {"type": "file", "extra": {"project": " grp/x "}}]
    assert gitlab_ci_lint.include_sources(includes) == ("grp/x",)


def test_conditional_include_reads_only_the_include_section():
    from lib.ci_checks import conditional_include
    assert not conditional_include(INCLUDE.format(project="a/b") + "t:\n  rules:\n    - if: $X\n")
    assert not conditional_include("include:\n  - local: /a.yml  # rules: none\n")


def test_no_gitlab_ci_file_or_no_include_is_not_read(glab, tmp_path):
    root = tmp_path / "gl"
    plain = make_repo(root, "grp/plain", files={"src/a.py": "x = 1\n"},
                      remote="https://gitlab.com/grp/plain.git")
    bare = make_repo(root, "grp/bare", files={".gitlab-ci.yml": "t:\n  script:\n    - make\n"},
                     remote="https://gitlab.com/grp/bare.git")
    assert gitlab_ci_lint.read_merged_ci(plain, root) == NOT_READ
    assert gitlab_ci_lint.read_merged_ci(bare, root) == NOT_READ
    assert calls(glab) == []


@pytest.mark.parametrize("merged", [MergedCi("unavailable", "no_access"), MergedCi("invalid"),
                                    NOT_READ, {"available": False, "reason": "x"}])
def test_ci_checks_stays_unknown_without_a_merged_read(tmp_path, merged):
    root, app = _offline_app(tmp_path)
    block = scan_ci_checks(app, root, merged)
    assert (block["test_or_lint_configured"], block["basis"]) == (None, "included_jobs")
    assert block["merged_check_commands"] == 0


def test_an_offline_check_wins_over_the_merged_config(tmp_path):
    root = tmp_path / "gl"
    app = make_repo(root, "grp/app", files={
        ".gitlab-ci.yml": INCLUDE.format(project="grp/missing") + "t:\n  script:\n    - make test\n"})
    block = scan_ci_checks(app, root, MergedCi("read", text="build:\n  script:\n    - make\n"))
    assert (block["test_or_lint_configured"], block["basis"]) == (True, "check_command")


def test_review_automation_matches_a_merged_review_component_once(tmp_path):
    root, app = _offline_app(tmp_path)
    assert scan_review_automation(app, ghsync_root=root)["present"] is False
    merged = MergedCi("read", text=MERGED, include_sources=(
        "gitlab.com/tools/ai-review@1.2.0", "grp/missing", "gitlab.com/tools/ai-review@1.2.0x"))
    block = scan_review_automation(app, ghsync_root=root, merged_ci=merged)
    assert block["present"] is True and block["kinds"] == ["ci_component"]
    assert [(m["reviewer"], m["line"], m["depth"]) for m in block["matches"]] == [
        ("ai-review-component", None, 1)]


def test_counts_only_keeps_the_merged_fields_and_no_text(tmp_path):
    root, app = _offline_app(tmp_path)
    scans = {"ci_checks": scan_ci_checks(app, root, MergedCi("read", text=MERGED))}
    out = counts_only_scans(scans)["ci_checks"]
    for field in ("merged_config", "merged_config_reason", "merged_check_commands",
                  "test_or_lint_configured", "basis"):
        assert out[field] == scans["ci_checks"][field], field  # a dropped field fails here
    unavailable = {"ci_checks": scan_ci_checks(app, root, MergedCi("unavailable", "not_found"))}
    assert counts_only_scans(unavailable)["ci_checks"]["merged_config_reason"] == "not_found"
    assert "pytest" not in json.dumps(out)


# --- the headless runner and fleet --platform ------------------------------------


def test_offline_collect_never_asks(glab, tmp_path):
    root = _root(tmp_path)
    block = collect(_app(root), ["ci_checks"], ghsync_root=root)["ci_checks"]
    assert (block["merged_config"], block["test_or_lint_configured"]) == ("not_read", None)
    assert calls(glab) == []


def _rows(out: Path) -> dict[str, dict[str, object]]:
    rows = [json.loads(line) for line in (out / "repos.jsonl").read_text(encoding="utf-8").splitlines()]
    return {r["repo"]: r["scans"] for r in rows}


def test_fleet_platform_reads_the_merged_config_where_a_template_is_not_cloned(glab, tmp_path):
    root = _root(tmp_path)
    base = [str(root), "--no-history", "--no-diagrams"]
    assert fleet_cli.main([*base, "--out", str(tmp_path / "n0")]) == 0
    assert calls(glab) == []
    assert _rows(tmp_path / "n0")["grp/app"]["ci_checks"]["test_or_lint_configured"] is None

    assert fleet_cli.main([*base, "--out", str(tmp_path / "n1"), "--platform",
                           "--platform-jobs", "1"]) == 0
    lint_calls = [c for c in calls(glab) if "/ci/lint" in c]
    assert len(lint_calls) == 1 and "projects/grp%2Fapp/ci/lint" in lint_calls[0]
    scans = _rows(tmp_path / "n1")
    app, both = scans["grp/app"]["ci_checks"], scans["grp/both"]["ci_checks"]
    assert (app["test_or_lint_configured"], app["basis"], app["merged_config"]) == (
        True, "merged_config", "read")
    assert (both["test_or_lint_configured"], both["merged_config"]) == (False, "not_needed")
    assert calls(glab, "leak.log") == []
    for where in (tmp_path / "tmp", tmp_path / "n1"):
        assert not [p for p in where.rglob("*") if p.is_file() and b"pytest -q" in p.read_bytes()]
