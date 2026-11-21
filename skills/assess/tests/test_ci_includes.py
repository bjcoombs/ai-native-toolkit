"""Tests for lib/ci_includes.py: GitLab CI includes resolved offline from the ghsync root (#766)."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

import assess_signals
from lib import ci_includes
from lib.ci_checks import scan_ci_checks
from lib.ci_includes import IncludeItem, include_items, resolve_includes
from lib.counts_only import counts_only_scans
from lib.review_automation import scan_review_automation


def _commit(repo: Path, files: dict[str, str], *, branch: str = "main", tag: str | None = None) -> None:
    if not (repo / ".git").exists():
        repo.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "init", "-q", "-b", branch], cwd=repo, check=True)
    for rel, text in files.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-qm", "c"],
                   cwd=repo, check=True)
    if tag:
        subprocess.run(["git", "tag", tag], cwd=repo, check=True)


def _clone(root: Path, path: str) -> Path:
    return root / path / f"{path.rsplit('/', 1)[-1]}-main"


TEST_TPL = "unit:\n  script:\n    - pytest -q\n"
INCLUDE_TEST = "include:\n  - project: grp/ci-templates\n    file: /templates/test.yml\n    ref: main\n"


# --- the parser ---------------------------------------------------------------


@pytest.mark.parametrize(("text", "items"), [
    (INCLUDE_TEST, [IncludeItem("project", "grp/ci-templates", ("/templates/test.yml",), "main", 2)]),
    ("include:\n  - project: a/b\n    ref: v1\n    file:\n      - /x.yml\n      - 'y.yml'\n",
     [IncludeItem("project", "a/b", ("/x.yml", "y.yml"), "v1", 2)]),
    ("include:\n  - project: a/b\n    file: [/x.yml, /y.yml]\n",
     [IncludeItem("project", "a/b", ("/x.yml", "/y.yml"), None, 2)]),
    ("include:\n  project: a/b\n  file: /x.yml\n", [IncludeItem("project", "a/b", ("/x.yml",), None, 2)]),
    ("include: '/ci/jobs.yml'\n", [IncludeItem("local", "/ci/jobs.yml", (), None, 1)]),
    ("include: [{project: a/b, file: [/x.yml], ref: v2}, 'l.yml']\n",
     [IncludeItem("project", "a/b", ("/x.yml",), "v2", 1), IncludeItem("local", "l.yml", (), None, 1)]),
    ("include:\n  - local: /a.yml\n  - template: Security/SAST.gitlab-ci.yml\n"
     "  - remote: https://x.test/a.yml\n  - https://x.test/b.yml\n  - component: gl.test/g/c@1\n"
     "  - {project: p/q, file: /f.yml}\n",
     [IncludeItem("local", "/a.yml", (), None, 2),
      IncludeItem("template", "Security/SAST.gitlab-ci.yml", (), None, 3),
      IncludeItem("remote", "https://x.test/a.yml", (), None, 4),
      IncludeItem("remote", "https://x.test/b.yml", (), None, 5),
      IncludeItem("component", "gl.test/g/c@1", (), None, 6),
      IncludeItem("project", "p/q", ("/f.yml",), None, 7)]),
    ("include:\n  - component: gl.test/g/c@1\n    inputs:\n      project: not/a-source\n",
     [IncludeItem("component", "gl.test/g/c@1", (), None, 2)]),
])
def test_include_items(text: str, items: list[IncludeItem]) -> None:
    assert include_items(text) == (items, True, 0)


def test_a_file_without_an_include_section_has_none() -> None:
    assert include_items("build:\n  script:\n    - make\n") == ([], False, 0)


@pytest.mark.parametrize("text", [
    "include:\n  - project: ''\n    file: /x.yml\n  - local: /a.yml\n",
    "include:\n  - unknown_key: x\n  - local: /a.yml\n",
    "include: [{project: ''}, /a.yml]\n",
])
def test_an_entry_naming_no_item_is_counted_not_lost(text: str) -> None:
    items, has_section, dropped = include_items(text)
    assert (len(items), has_section, dropped) == (1, True, 1)


# --- resolution ---------------------------------------------------------------


def _root_with_template(tmp_path: Path, tpl: dict[str, str], ci: str = INCLUDE_TEST) -> tuple[Path, Path]:
    root = tmp_path / "root"
    _commit(_clone(root, "grp/ci-templates"), tpl)
    app = _clone(root, "grp/app")
    _commit(app, {".gitlab-ci.yml": ci, "src/a.py": "x = 1\n"})
    return root, app


def test_a_project_include_resolves_from_the_root_and_runs_the_check(tmp_path: Path) -> None:
    root, app = _root_with_template(tmp_path, {"templates/test.yml": TEST_TPL})
    block = scan_ci_checks(app, root)
    assert (block["test_or_lint_runs"], block["basis"]) == (True, "check_command")
    assert (block["check_commands"], block["included_check_commands"]) == (0, 1)
    assert block["includes"] == [{"kind": "project", "id": "gitlab_project:grp/ci-templates",
                                  "status": "resolved", "ref_basis": "ref", "depth": 1}]
    assert (block["resolved_includes"], block["unresolved_includes"]) == (1, 0)


def test_without_a_root_a_project_include_stays_unknown(tmp_path: Path) -> None:
    _root, app = _root_with_template(tmp_path, {"templates/test.yml": TEST_TPL})
    block = scan_ci_checks(app)
    assert block["test_or_lint_runs"] is None and block["includes"][0]["status"] == "no_root"


def test_an_uncloned_template_stays_unknown(tmp_path: Path) -> None:
    root, app = _root_with_template(tmp_path, {"templates/test.yml": TEST_TPL},
                                    ci="include:\n  - project: grp/not-cloned\n    file: /t.yml\n")
    block = scan_ci_checks(app, root)
    assert (block["test_or_lint_runs"], block["basis"]) == (None, "included_jobs")
    assert block["includes"][0]["status"] == "not_cloned"


def test_every_include_resolved_with_no_check_is_false(tmp_path: Path) -> None:
    root, app = _root_with_template(tmp_path, {"templates/test.yml": "docs:\n  script:\n    - make docs\n"})
    block = scan_ci_checks(app, root)
    assert (block["test_or_lint_runs"], block["basis"]) == (False, "no_check_command")


def test_a_missing_file_stays_unknown(tmp_path: Path) -> None:
    root, app = _root_with_template(tmp_path, {"templates/other.yml": TEST_TPL})
    block = scan_ci_checks(app, root)
    assert block["test_or_lint_runs"] is None and block["includes"][0]["status"] == "file_missing"


@pytest.mark.parametrize("ref", ["v9", "$TPL_REF", "--output=/x"])
def test_a_pin_the_clone_cannot_read_is_unresolved_never_false(tmp_path: Path, ref: str) -> None:
    """HEAD is a different version than the pin: neither true nor false is read from it."""
    ci = f"include:\n  - project: grp/ci-templates\n    file: /templates/test.yml\n    ref: '{ref}'\n"
    root, app = _root_with_template(tmp_path, {"templates/test.yml": "docs:\n  script:\n    - make docs\n"}, ci=ci)
    block = scan_ci_checks(app, root)
    assert [(r["status"], r["ref_basis"]) for r in block["includes"]] == [("ref_missing", None)]
    assert (block["test_or_lint_runs"], block["basis"]) == (None, "included_jobs")


def test_no_ref_reads_head_and_records_it(tmp_path: Path) -> None:
    ci = "include:\n  - project: grp/ci-templates\n    file: /templates/test.yml\n"
    root, app = _root_with_template(tmp_path, {"templates/test.yml": TEST_TPL}, ci=ci)
    record = scan_ci_checks(app, root)["includes"][0]
    assert (record["status"], record["ref_basis"]) == ("resolved", "head")


def test_a_pinned_ref_reads_that_commit_not_head(tmp_path: Path) -> None:
    root = tmp_path / "root"
    tpl = _clone(root, "grp/ci-templates")
    _commit(tpl, {"templates/test.yml": TEST_TPL}, tag="v1")
    _commit(tpl, {"templates/test.yml": "docs:\n  script:\n    - make docs\n"})
    app = _clone(root, "grp/app")
    _commit(app, {".gitlab-ci.yml": INCLUDE_TEST.replace("ref: main", "ref: v1")})
    block = scan_ci_checks(app, root)
    assert block["test_or_lint_runs"] is True and block["includes"][0]["ref_basis"] == "ref"


def test_local_includes_resolve_in_the_checkout_and_in_a_template(tmp_path: Path) -> None:
    root, app = _root_with_template(
        tmp_path, {"templates/test.yml": "include:\n  - local: /jobs/unit.yml\n", "jobs/unit.yml": TEST_TPL},
        ci="include:\n  - local: ci/own.yml\n" + INCLUDE_TEST.removeprefix("include:\n"))
    _commit(app, {"ci/own.yml": "build:\n  script:\n    - make build\n"})
    block = scan_ci_checks(app, root)
    assert block["test_or_lint_runs"] is True
    assert [(r["kind"], r["status"], r["ref_basis"], r["depth"]) for r in block["includes"]] == [
        ("local", "resolved", "worktree", 1), ("project", "resolved", "ref", 1),
        ("local", "resolved", "ref", 2)]


@pytest.mark.parametrize(("ci", "status"), [
    ("include:\n  - template: Security/SAST.gitlab-ci.yml\n", "not_offline"),
    ("include:\n  - remote: https://x.test/a.yml\n", "not_offline"),
    ("include:\n  - local: /ci/*.yml\n", "glob"),
    ("include:\n  - local: ../outside.yml\n", "bad_path"),
    ("include:\n  - project: grp/../etc\n    file: /t.yml\n", "bad_path"),
    ("include:\n  - project: grp/ci-templates\n    file: /../t.yml\n", "bad_path"),
    ("include:\n  - project: grp/ci-templates\n", "bad_path"),
])
def test_unresolvable_includes_are_named(tmp_path: Path, ci: str, status: str) -> None:
    root, app = _root_with_template(tmp_path, {"templates/test.yml": TEST_TPL}, ci=ci)
    record = scan_ci_checks(app, root)["includes"][0]
    assert record["status"] == status


def test_a_component_resolves_from_its_project_clone(tmp_path: Path) -> None:
    root, app = _root_with_template(tmp_path, {"README.md": "x\n"},
                                    ci="include:\n  - component: gl.test/grp/comps/unit@1.0\n")
    _commit(_clone(root, "grp/comps"), {"templates/unit/template.yml": TEST_TPL}, tag="1.0")
    block = scan_ci_checks(app, root)
    assert block["test_or_lint_runs"] is True
    assert block["includes"][0] == {"kind": "component", "id": "gitlab_component:grp/comps/unit",
                                    "status": "resolved", "ref_basis": "ref", "depth": 1}


def test_a_cycle_reads_each_file_once_and_depth_is_bounded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    loop = "include:\n  - project: grp/ci-templates\n    file: /a.yml\n"
    root, app = _root_with_template(tmp_path, {"a.yml": loop + TEST_TPL}, ci=loop)
    block = scan_ci_checks(app, root)
    assert block["test_or_lint_runs"] is True and block["unresolved_includes"] == 0
    monkeypatch.setattr(ci_includes, "MAX_DEPTH", 1)
    chain = "include:\n  - project: grp/ci-templates\n    file: /b.yml\n"
    root2, app2 = _root_with_template(tmp_path / "two", {"a.yml": chain, "b.yml": TEST_TPL}, ci=loop)
    deep = scan_ci_checks(app2, root2)
    assert [r["status"] for r in deep["includes"]] == ["resolved", "depth_limit"]
    assert deep["test_or_lint_runs"] is None


def test_a_symlinked_clone_outside_the_root_is_not_read(tmp_path: Path) -> None:
    outside = tmp_path / "elsewhere"
    _commit(outside, {"templates/test.yml": TEST_TPL})
    root = tmp_path / "root"
    (root / "grp" / "ci-templates").mkdir(parents=True)
    (root / "grp" / "ci-templates" / "ci-templates-main").symlink_to(outside)
    app = _clone(root, "grp/app")
    _commit(app, {".gitlab-ci.yml": INCLUDE_TEST})
    assert scan_ci_checks(app, root)["includes"][0]["status"] == "not_cloned"


def test_an_unparsed_include_section_stays_unknown(tmp_path: Path) -> None:
    app = tmp_path / "app"
    _commit(app, {".gitlab-ci.yml": "include:\n  - !reference [x]\n"})
    resolution = resolve_includes(app)
    assert resolution.unresolved >= 1


# --- review automation through a template -------------------------------------


def test_a_review_component_inside_a_template_credits_review(tmp_path: Path) -> None:
    ci = INCLUDE_TEST.replace("test.yml", "review.yml")
    root, app = _root_with_template(
        tmp_path, {"templates/review.yml": "include:\n  - component: gl.test/tools/ai-review@1.2.0\n"}, ci=ci)
    block = scan_review_automation(app, ghsync_root=root)
    assert block["present"] is True and block["kinds"] == ["ci_component"]
    assert [(m["path"], m["line"], m["depth"], m["tracked"]) for m in block["matches"]] == [
        (".gitlab-ci.yml", 2, 1, True)]
    assert scan_review_automation(app)["present"] is False  # without the root the template is unread


# --- counts-only and the CLI ----------------------------------------------------


def test_counts_only_keeps_every_include_field_and_no_file_name(tmp_path: Path) -> None:
    root, app = _root_with_template(tmp_path, {"templates/test.yml": TEST_TPL})
    scans = {"ci_checks": scan_ci_checks(app, root),
             "review_automation": scan_review_automation(app, ghsync_root=root)}
    out = counts_only_scans(scans)
    ci = out["ci_checks"]
    for field in ("included_check_commands", "resolved_includes", "unresolved_includes"):
        assert ci[field] == scans["ci_checks"][field], field  # a dropped field fails here
    assert ci["includes"] == scans["ci_checks"]["includes"]
    assert ci["test_or_lint_runs"] is True
    assert "templates/test.yml" not in json.dumps(out)


def test_assess_signals_reads_the_ghsync_root_flag(tmp_path: Path) -> None:
    root, app = _root_with_template(tmp_path, {"templates/test.yml": TEST_TPL})
    out = tmp_path / "out.json"
    assert assess_signals.main([str(app), "--out", str(out), "--scans", "ci_checks",
                                "--ghsync-root", str(root), "--counts-only"]) == 0
    assert json.loads(out.read_text(encoding="utf-8"))["scans"]["ci_checks"]["test_or_lint_runs"] is True
    assert assess_signals.main([str(app), "--out", str(out), "--ghsync-root", str(tmp_path / "nope")]) == 2


def test_fleet_passes_its_root_unless_delete_after(tmp_path: Path) -> None:
    from lib.fleet import ScanOptions, signals_command
    command = signals_command(tmp_path / "c", tmp_path / "o.json", ScanOptions(ghsync_root=tmp_path))
    assert command[command.index("--ghsync-root") + 1] == str(tmp_path)
    assert "--ghsync-root" not in signals_command(tmp_path / "c", tmp_path / "o.json", ScanOptions())


def test_an_untracked_local_fragment_is_not_read(tmp_path: Path) -> None:
    app = tmp_path / "app"
    _commit(app, {".gitlab-ci.yml": "include:\n  - local: ci/unit.yml\n"})
    (app / "ci").mkdir()
    (app / "ci" / "unit.yml").write_text(TEST_TPL, encoding="utf-8")
    block = scan_ci_checks(app)
    assert block["test_or_lint_runs"] is None and block["includes"][0]["status"] == "file_missing"


def test_a_symlinked_gitlab_ci_file_is_never_fully_read(tmp_path: Path) -> None:
    app = tmp_path / "app"
    _commit(app, {"real.yml": "include:\n  - local: /none.yml\nbuild:\n  script:\n    - make\n"})
    (app / ".gitlab-ci.yml").symlink_to(app / "real.yml")
    _commit(app, {})
    block = scan_ci_checks(app)
    assert block["included_files"] == 1 and block["includes"] == []
    assert (block["test_or_lint_runs"], block["basis"]) == (None, "included_jobs")


def test_an_unreadable_entry_keeps_the_signal_unknown(tmp_path: Path) -> None:
    root, app = _root_with_template(
        tmp_path, {"templates/test.yml": "docs:\n  script:\n    - make docs\n"},
        ci=INCLUDE_TEST + "  - project: ''\n")
    block = scan_ci_checks(app, root)
    assert [r["status"] for r in block["includes"]] == ["unparsed", "resolved"]
    assert block["test_or_lint_runs"] is None


def test_counts_only_keeps_review_depth_and_null_ids(tmp_path: Path) -> None:
    ci = INCLUDE_TEST.replace("test.yml", "review.yml") + "  - local: ci/build.yml\n"
    root, app = _root_with_template(
        tmp_path, {"templates/review.yml": "include:\n  - component: gl.test/tools/ai-review@1.2.0\n"}, ci=ci)
    _commit(app, {"ci/build.yml": "build:\n  script:\n    - make build\n"})
    scans = {"ci_checks": scan_ci_checks(app, root),
             "review_automation": scan_review_automation(app, ghsync_root=root)}
    out = counts_only_scans(scans)
    assert out["ci_checks"]["includes"] == scans["ci_checks"]["includes"]
    assert any(r["id"] is None for r in out["ci_checks"]["includes"])
    assert [m["depth"] for m in out["review_automation"]["matches"]] == [1]
    assert out["review_automation"]["present"] is True
    assert "ci/build.yml" not in json.dumps(out)


@pytest.mark.parametrize("text", [
    "include:\n  - project: a/b\n    file:\n    - /x.yml\n    - /y.yml\n",
    "include:\n  project: a/b\n  file:\n  - /x.yml\n  - /y.yml\n",
])
def test_an_indentless_file_sequence_is_read(text: str) -> None:
    items, _, dropped = include_items(text)
    assert (items[0].files, dropped) == (("/x.yml", "/y.yml"), 0)


def test_a_file_from_a_failed_item_is_read_again_by_a_later_item(tmp_path: Path) -> None:
    ci = ("include:\n  - project: grp/ci-templates\n    file: [/templates/test.yml, /missing.yml]\n"
          "  - project: grp/ci-templates\n    file: /templates/test.yml\n")
    root, app = _root_with_template(tmp_path, {"templates/test.yml": TEST_TPL}, ci=ci)
    block = scan_ci_checks(app, root)
    assert [r["status"] for r in block["includes"]] == ["file_missing", "resolved"]
    assert block["included_check_commands"] == 1 and block["test_or_lint_runs"] is True


def test_an_item_stopped_by_the_file_limit_says_so(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ci_includes, "MAX_FILES", 2)  # .gitlab-ci.yml is the first file read
    ci = "include:\n  - project: grp/ci-templates\n    file: [/a.yml, /b.yml]\n"
    root, app = _root_with_template(tmp_path, {"a.yml": TEST_TPL, "b.yml": TEST_TPL}, ci=ci)
    block = scan_ci_checks(app, root)
    assert [r["status"] for r in block["includes"]] == ["file_limit"]
    assert block["test_or_lint_runs"] is None


@pytest.mark.parametrize("text", [
    "include:\n  - project: a/b\n    file:\n      - /x.yml\n    rules:\n      - if: $X\n",
    "include:\n  - project: a/b\n    file:\n    - /x.yml\n    rules:\n    - if: $X\n",
    "include:\n  - project: a/b\n    file:\n      - /x.yml\n    inputs:\n      - stage: test\n",
])
def test_a_key_after_a_file_list_ends_it(text: str) -> None:
    items, _, dropped = include_items(text)
    assert (items[0].files, dropped) == (("/x.yml",), 0)
