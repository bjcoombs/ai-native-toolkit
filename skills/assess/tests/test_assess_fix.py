"""End-to-end tests for ``assess_fix.py`` (``/assess --fix`` and ``--starter``, issue #1154).

Each case builds a small git repository, runs the fix with no pull request, and reads the Step
again from fresh scans, as a re-run of ``/assess`` would. The issue's success criteria:

- a Step 1 fixture with tests and no instruction file reads Step 2 after ``--fix``;
- a fixture whose CI does not run the tests gains a job for its CI system and the gate holds;
- no existing file is overwritten.

Delivery is tested with a recorded runner, so no case pushes or calls a host CLI.
"""
from __future__ import annotations

import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import assess_fix as cli  # noqa: E402
from lib.level_tally import default_model, level_result  # noqa: E402
from lib.starter_delivery import open_change, tree_clean  # noqa: E402

FLEET_SCRIPTS = SCRIPTS.parents[1] / "fleet" / "scripts"


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True,
                   text=True, encoding="utf-8", errors="replace")


def _repo(tmp_path: Path, files: dict[str, str]) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "T")
    for rel, text in files.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(text, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")
    return repo


PYTHON = {
    "pyproject.toml": '[project]\nname = "pkg"\nversion = "0.1"\n[tool.pytest.ini_options]\ntestpaths = ["tests"]\n',
    "src/pkg/__init__.py": "def add(a, b):\n    return a + b\n",
    "tests/test_add.py": "from pkg import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n",
}
WITH_INSTRUCTIONS = {**PYTHON, "CLAUDE.md": "# pkg\n\nRun `pytest`.\n",
                     ".claude/settings.json": '{"permissions": {"deny": ["Bash(git push origin main)"]}}\n'}


def _step(repo: Path) -> int | None:
    return level_result(default_model(), cli.read_scans(repo))["step"]


def test_step_1_with_tests_reads_step_2_after_fix(tmp_path: Path) -> None:
    repo = _repo(tmp_path, PYTHON)
    assert _step(repo) == 1
    report = cli.fix(repo, starter=False, open_pr=False)
    assert [f["path"] for f in report["files"]] == ["CLAUDE.md", ".claude/settings.json"]
    assert all(p["held_after"] for p in report["gates"])
    assert (report["step_before"], report["step_after"]) == (1, 2)
    assert _step(repo) == 2  # the re-run reads the gate held
    assert "pytest" in (repo / "CLAUDE.md").read_text(encoding="utf-8")
    assert report["delivery"]["reason"] == "no_pr_requested"


def test_ci_that_does_not_run_tests_gains_a_test_job(tmp_path: Path) -> None:
    lint = "name: lint\non: [pull_request]\njobs:\n  l:\n    runs-on: ubuntu-latest\n    steps:\n      - run: echo hi\n"
    repo = _repo(tmp_path, {**WITH_INSTRUCTIONS, ".github/workflows/lint.yml": lint})
    assert _step(repo) == 2
    report = cli.fix(repo, starter=False, open_pr=False)
    assert report["facts"]["ci_system"] == "github" and report["facts"]["ci_basis"] == "tracked"
    assert [f["path"] for f in report["files"]] == [".github/workflows/tests.yml"]
    assert report["gates"][0]["held_after"] and _step(repo) == 3
    assert (repo / ".github/workflows/lint.yml").read_text(encoding="utf-8") == lint


def test_gitlab_remote_gets_a_gitlab_job(tmp_path: Path) -> None:
    repo = _repo(tmp_path, WITH_INSTRUCTIONS)
    _git(repo, "remote", "add", "origin", "git@gitlab.example.com:team/pkg.git")
    report = cli.fix(repo, starter=False, open_pr=False)
    assert report["facts"]["ci_basis"] == "remote"
    assert [f["path"] for f in report["files"]] == [".gitlab-ci.yml"]
    assert report["gates"][0]["held_after"] and _step(repo) == 3


def test_existing_gitlab_ci_is_kept_and_the_include_is_the_manual_step(tmp_path: Path) -> None:
    gitlab = "lint:\n  script:\n    - echo hi\n"
    repo = _repo(tmp_path, {**WITH_INSTRUCTIONS, ".gitlab-ci.yml": gitlab})
    report = cli.fix(repo, starter=False, open_pr=False)
    assert (repo / ".gitlab-ci.yml").read_text(encoding="utf-8") == gitlab
    assert [f["path"] for f in report["files"]] == [".gitlab/ci/tests.yml"]
    proof = report["gates"][0]
    assert not proof["held_after"] and "include" in (proof["manual"] or "")
    assert "manual step" in cli.render(report)


def test_an_existing_file_is_a_conflict_never_overwritten(tmp_path: Path) -> None:
    repo = _repo(tmp_path, PYTHON)
    (repo / "CLAUDE.md").write_text("draft, not committed\n", encoding="utf-8")
    report = cli.fix(repo, starter=False, open_pr=False)
    assert (repo / "CLAUDE.md").read_text(encoding="utf-8") == "draft, not committed\n"
    conflict = report["files"][0]
    assert conflict["state"] == "conflict" and conflict["suggested"]
    assert "conflict: CLAUDE.md exists" in cli.render(report)


def test_starter_scaffolds_every_gate_on_an_empty_directory(tmp_path: Path) -> None:
    report = cli.fix(tmp_path, starter=True, language="python", open_pr=False)
    paths = {f["path"] for f in report["files"] if f["state"] == "written"}
    assert paths == {"CLAUDE.md", ".github/workflows/tests.yml", ".github/workflows/coverage.yml",
                     ".claude/settings.json"}
    assert report["delivery"]["reason"] == "not_a_git_repo"


def test_starter_in_a_new_git_repository_reaches_the_top_step_and_then_has_nothing_to_do(tmp_path: Path) -> None:
    repo = tmp_path / "new"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    report = cli.fix(repo, starter=True, language="python", check_command="pytest", open_pr=False)
    assert report["step_after"] == 4 and all(p["held_after"] for p in report["gates"])
    again = cli.fix(repo, starter=True, open_pr=False)
    assert again["nothing_to_do"] and again["files"] == []
    assert "Nothing to do" in cli.render(again)


class _Recorder:
    def __init__(self, fail: Sequence[str] = ()) -> None:
        self.calls: list[list[str]] = []
        self.fail = fail

    def __call__(self, cmd: Sequence[str], cwd: Path) -> tuple[int, str]:
        self.calls.append(list(cmd))
        joined = " ".join(cmd)
        if any(joined.startswith(f) for f in self.fail):
            return 1, ""
        if joined.startswith("git remote get-url"):
            return 0, "git@github.com:org/pkg.git"
        if joined.startswith("gh pr create"):
            return 0, "https://github.com/org/pkg/pull/7"
        return 0, ""


def test_open_change_makes_a_branch_and_pull_request(tmp_path: Path) -> None:
    runner = _Recorder()
    delivery = open_change(tmp_path, "assess/fix-instructions", "chore: x", "body", ["CLAUDE.md"],
                           was_clean=True, runner=runner)
    assert delivery == {"mode": "pull_request", "branch": "assess/fix-instructions",
                        "url": "https://github.com/org/pkg/pull/7", "reason": "opened"}
    assert [c[:2] for c in runner.calls] == [["git", "remote"], ["gh", "auth"], ["git", "switch"],
                                             ["git", "commit"], ["git", "push"], ["gh", "pr"]]
    assert runner.calls[3][-2:] == ["--", "CLAUDE.md"]  # only the starter files are committed


@pytest.mark.parametrize(("clean", "fail", "reason"), [
    (False, (), "dirty_tree"),
    (True, ("gh auth",), "gh_not_authenticated"),
    (True, ("git push",), "push_failed"),
])
def test_open_change_leaves_the_files_staged_when_a_precondition_fails(
        tmp_path: Path, clean: bool, fail: tuple[str, ...], reason: str) -> None:
    delivery = open_change(tmp_path, "b", "t", "body", ["CLAUDE.md"], was_clean=clean, runner=_Recorder(fail))
    assert delivery["mode"] == "working_tree" and delivery["reason"] == reason


def test_fix_opens_the_pull_request_through_the_runner(tmp_path: Path) -> None:
    repo = _repo(tmp_path, PYTHON)
    runner = _Recorder(fail=())

    def real_git_then_record(cmd: Sequence[str], cwd: Path) -> tuple[int, str]:
        if list(cmd[:2]) in (["git", "rev-parse"], ["git", "status"], ["git", "add"]):
            out = subprocess.run(list(cmd), cwd=cwd, capture_output=True, text=True, encoding="utf-8",
                                 errors="replace", check=False)
            return out.returncode, out.stdout.strip()
        return runner(cmd, cwd)

    report = cli.fix(repo, starter=False, runner=real_git_then_record)
    assert report["delivery"]["mode"] == "pull_request"
    create = next(c for c in runner.calls if c[:3] == ["gh", "pr", "create"])
    assert "CLAUDE.md" in create[create.index("--body") + 1]
    assert "the checkout is now on assess/fix-instructions" in cli.render(report)


@pytest.mark.parametrize(("reason", "words"), [
    ("push_failed", "committed, not pushed,"),
    ("pull_request_failed", "committed and pushed, with no pull request opened,"),
])
def test_render_says_the_files_are_committed_after_a_late_failure(tmp_path: Path, reason: str, words: str) -> None:
    report = cli.fix(_repo(tmp_path, PYTHON), starter=False, open_pr=False)
    report["delivery"] = {"mode": "working_tree", "branch": "assess/fix-instructions", "url": None, "reason": reason}
    text = cli.render(report)
    assert f"The files are {words} on branch assess/fix-instructions" in text and "staged in the working" not in text


def test_github_repository_named_gitlab_asks_gh(tmp_path: Path) -> None:
    runner = _Recorder(fail=("gh auth",))
    runner_url = "git@github.com:acme/gitlab-migration.git"

    def recorded(cmd: Sequence[str], cwd: Path) -> tuple[int, str]:
        return (0, runner_url) if list(cmd[:3]) == ["git", "remote", "get-url"] else runner(cmd, cwd)

    assert open_change(tmp_path, "b", "t", "body", ["CLAUDE.md"], was_clean=True,
                       runner=recorded)["reason"] == "gh_not_authenticated"


def test_assess_output_does_not_make_the_tree_dirty(tmp_path: Path) -> None:
    repo = _repo(tmp_path, {**PYTHON, ".assess/log.md": "old\n"})
    (repo / ".assess" / "log.md").write_text("new run\n", encoding="utf-8")
    (repo / ".assess" / "assess-report.md").write_text("report\n", encoding="utf-8")
    assert tree_clean(repo)
    (repo / "notes.txt").write_text("mine\n", encoding="utf-8")
    assert not tree_clean(repo)


def test_main_needs_a_mode_and_writes_nothing_without_one(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as exc:
        cli.main([str(tmp_path)])
    assert exc.value.code == 2
    assert list(tmp_path.iterdir()) == []


def test_fleet_has_no_fix_path() -> None:
    scripts = sorted(FLEET_SCRIPTS.rglob("*.py"))
    assert scripts, FLEET_SCRIPTS
    for path in scripts:
        text = path.read_text(encoding="utf-8")
        assert "assess_fix" not in text and "starter_files" not in text, path
        assert '"--fix"' not in text and '"--starter"' not in text, path
