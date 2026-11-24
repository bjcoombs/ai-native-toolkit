"""ghreport's GitLab provider: discovery, project rows, flags and empty repositories.

The fixture and stubs live in `ghreport_gitlab_env.py`.
"""

import json
import subprocess
from pathlib import Path

import pytest
from ghreport_gitlab_env import (
    ALPHA,
    COLLIDING,
    EMPTY,
    GHREPORT,
    HOST,
    Endpoints,
    Env,
    _attention,
    _cells,
    _collision_overrides,
    _endpoints,
    _listed_projects,
    _pipeline,
    _project,
    _row,
)
from gitbash import BASH


@pytest.fixture
def env(tmp_path: Path) -> Env:
    return Env(tmp_path)


@pytest.fixture
def full_run(env: Env) -> subprocess.CompletedProcess[str]:
    return env.run("--host", HOST)


def test_gl10_alpha_row(env: Env, full_run: subprocess.CompletedProcess[str]) -> None:
    """GL10: 2 open MRs (across two pages), success on main, main protected,
    vulnerabilities no-access rather than 0."""
    assert full_run.returncode == 0, full_run.stderr
    assert _cells(_row(full_run.stdout, "grp/alpha")) == [
        "grp/alpha", "main", "2", "success", "no-access", "protected",
    ]
    report = next(env.root.glob(f"org-state-{HOST}-*.md")).read_text()
    assert _row(report, "grp/alpha") == _row(full_run.stdout, "grp/alpha")
    assert not env.gh_log.exists(), "the GitLab path must not call gh"


def test_discovery_covers_subgroups_and_shared(full_run: subprocess.CompletedProcess[str]) -> None:
    rows = [ln for ln in full_run.stdout.splitlines()
            if ln.startswith("| ") and "Project" not in ln and "---" not in ln]
    assert sorted(_cells(r)[0] for r in rows) == [
        "grp/alpha", "grp/sub/beta", "grp/sub/deep/gamma", "grp/sub/deep/ghost", "other/ns/shared",
    ]
    assert full_run.stdout.count("| grp/alpha |") == 1


@pytest.mark.parametrize(
    ("project", "cells"),
    [
        ("grp/sub/beta", ["main", "0", "none", "no-access", "unprotected"]),
        ("grp/sub/deep/gamma", ["main", "no-access", "failed", "2", "protected"]),
        ("other/ns/shared", ["main", "1 (1 draft)", "unknown", "no-access", "unknown"]),
        ("grp/sub/deep/ghost", ["-", "-", "-", "-", "could not assess"]),
    ],
)
def test_row_per_case(full_run: subprocess.CompletedProcess[str], project: str, cells: list[str]) -> None:
    assert _cells(_row(full_run.stdout, project))[1:] == cells


def test_headline_names_unreadable_counts(full_run: subprocess.CompletedProcess[str]) -> None:
    out = full_run.stdout
    assert (
        "5 project(s): 1 with a failed pipeline, 1 with open vulnerabilities, "
        "1 with an unprotected default branch, 3 open MR(s) total, "
        "MRs unreadable on 1, vulnerabilities no-access on 3, 1 could not be assessed"
    ) in out


def test_needs_attention(full_run: subprocess.CompletedProcess[str]) -> None:
    attention = _attention(full_run.stdout)
    assert "**grp/sub/deep/gamma**: pipeline failed on main; 2 open vulnerabilities" in attention
    assert "**grp/sub/beta**: default branch unprotected" in attention
    assert "**grp/sub/deep/ghost**: could not assess" in attention
    # No-access is not a finding: alpha and shared have no real signal.
    assert "grp/alpha" not in attention
    assert "other/ns/shared" not in attention


def test_report_file_shape(env: Env, full_run: subprocess.CompletedProcess[str]) -> None:
    report = next(env.root.glob(f"org-state-{HOST}-*.md")).read_text()
    for heading in ("# Org state: gl.test", "## Projects", "## Needs attention", "## Legend"):
        assert heading in report
    assert "| Project | Branch | MRs | Pipeline | Vulnerabilities | Protection |" in report


def test_group_narrows_discovery(env: Env) -> None:
    res = env.run("--host", HOST, "--group", "grp/sub")
    assert res.returncode == 0, res.stderr
    assert "| grp/sub/beta |" in res.stdout
    assert "| grp/sub/deep/gamma |" in res.stdout
    assert "grp/alpha" not in res.stdout
    assert list(env.root.glob(f"org-state-{HOST}-grp-sub-*.md"))


def test_limit_caps_queries(env: Env) -> None:
    res = env.run("--host", HOST, "--limit", "1", "--no-file")
    assert res.returncode == 0, res.stderr
    assert "1 project(s)" in res.stdout
    assert "| grp/alpha |" in res.stdout
    assert not any("projects/grp%2Fsub" in c for c in env.api_calls())


def test_empty_group(env: Env) -> None:
    res = env.run("--host", HOST, "--group", "grp/empty")
    assert res.returncode == 0, res.stderr
    assert "No repositories discovered" in res.stderr
    assert not list(env.root.glob("org-state-*.md"))


def test_auth_failure_fails_fast(env: Env) -> None:
    res = env.run("--host", "gl.unreachable")
    assert res.returncode != 0
    errors = [ln for ln in res.stderr.splitlines() if ln.startswith("Error")]
    assert len(errors) == 1 and "gl.unreachable" in errors[0], res.stderr
    assert env.api_calls() == []


def test_host_given_as_url(env: Env) -> None:
    res = env.run("--host", f"https://{HOST}/", "--limit", "1", "--no-file")
    assert res.returncode == 0, res.stderr
    assert _cells(_row(res.stdout, "grp/alpha"))[2:4] == ["2", "success"]


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["--org", "acme"], "--org applies to --provider github only"),
    ],
)
def test_rejects_github_flags(env: Env, args: list[str], message: str) -> None:
    res = env.run("--host", HOST, *args)
    assert res.returncode == 1
    assert message in res.stderr


def test_rejects_gitlab_flags_on_github(tmp_path: Path) -> None:
    res = subprocess.run(
        [BASH, str(GHREPORT), "--host", HOST, "--render-dir", str(tmp_path)],
        capture_output=True, check=False, text=True, timeout=60,
    )
    assert res.returncode == 1
    assert "--host applies to --provider gitlab only" in res.stderr


@pytest.mark.parametrize(
    ("overrides", "column", "expected"),
    [
        # Each endpoint x 403 / 404 / 500: unreadable, never 0.
        *[({f"{ALPHA}/merge_requests": code}, 2, "no-access") for code in (403, 404, 500)],
        *[({f"{ALPHA}/pipelines": code}, 3, "unknown") for code in (403, 404, 500)],
        *[({f"{ALPHA}/vulnerabilities": code}, 4, "no-access") for code in (403, 404, 500)],
        *[({f"{ALPHA}/protected_branches": code, f"{ALPHA}/protected_branches/main": code},
           5, "unknown") for code in (403, 404, 500)],
        # A 200 with data or an empty list is a real count.
        ({f"{ALPHA}/vulnerabilities": [[]]}, 4, "0"),
        ({f"{ALPHA}/vulnerabilities": [[{"id": 1, "state": "detected"}]]}, 4, "1"),
        ({f"{ALPHA}/merge_requests": [[]]}, 2, "0"),
        # An error body served with exit 0 is unreadable too.
        ({f"{ALPHA}/merge_requests": [{"message": "403 Forbidden"}]}, 2, "no-access"),
        # Pipeline: the newest finished (success or failed) one, as on GitHub.
        # running, canceled, skipped and manual never mask the last result.
        ({f"{ALPHA}/pipelines": [[_pipeline(12, "running"), _pipeline(11, "failed")]]}, 3, "failed"),
        ({f"{ALPHA}/pipelines": [[_pipeline(12, "running"), _pipeline(11, "success")]]}, 3, "success"),
        ({f"{ALPHA}/pipelines": [[_pipeline(14, "canceled"), _pipeline(13, "skipped"),
                                  _pipeline(12, "manual"), _pipeline(11, "failed")]]}, 3, "failed"),
        ({f"{ALPHA}/pipelines": [[_pipeline(12, "failed"), _pipeline(11, "success")]]}, 3, "failed"),
        ({f"{ALPHA}/pipelines": [[_pipeline(11, "running")]]}, 3, "none"),
        ({f"{ALPHA}/pipelines": [[_pipeline(12, "canceled")]]}, 3, "none"),
        ({f"{ALPHA}/pipelines": [[]]}, 3, "none"),
        ({f"{ALPHA}/pipelines": [{"message": "403 Forbidden"}]}, 3, "unknown"),
        ({f"{ALPHA}/pipelines": [[_pipeline(13, "failed", ref="feature")]]}, 3, "none"),
        # Protection: none, wildcard, and the single-branch fallback.
        ({f"{ALPHA}/protected_branches": [[]]}, 5, "unprotected"),
        ({f"{ALPHA}/protected_branches": [[{"name": "ma*"}]]}, 5, "protected"),
        ({f"{ALPHA}/protected_branches": [[{"name": "m.in"}]]}, 5, "unprotected"),
        # Issue #677: the covering wildcard only on a later page is still read.
        ({f"{ALPHA}/protected_branches": [[{"name": f"feature/{i}"} for i in range(100)],
                                          [{"name": "ma*"}]]}, 5, "protected"),
        ({f"{ALPHA}/protected_branches": 403, f"{ALPHA}/protected_branches/main": [{"name": "main"}]},
         5, "protected"),
    ],
)
def test_endpoint_matrix(tmp_path: Path, overrides: Endpoints, column: int, expected: str) -> None:
    env = Env(tmp_path, overrides)
    res = env.run("--host", HOST, "--limit", "1", "--no-file")
    assert res.returncode == 0, res.stderr
    assert _cells(_row(res.stdout, "grp/alpha"))[column] == expected


def test_failed_pipeline_needs_attention_other_statuses_do_not(tmp_path: Path) -> None:
    env = Env(tmp_path, {f"{ALPHA}/pipelines": [[_pipeline(14, "canceled")]]})
    res = env.run("--host", HOST, "--limit", "1", "--no-file")
    assert "Needs attention: none" in res.stdout


def test_running_pipeline_does_not_hide_an_earlier_failure(tmp_path: Path) -> None:
    env = Env(tmp_path, {f"{ALPHA}/pipelines": [[_pipeline(15, "running"), _pipeline(14, "failed")]]})
    res = env.run("--host", HOST, "--limit", "1", "--no-file")
    assert res.returncode == 0, res.stderr
    assert "Needs attention: none" not in res.stdout
    assert "pipeline failed on main" in res.stdout


def test_pipelines_request_asks_for_more_than_one(tmp_path: Path) -> None:
    env = Env(tmp_path, {})
    env.run("--host", HOST, "--limit", "1", "--no-file")
    calls = [ln for ln in env.glab_log.read_text().splitlines() if "/pipelines" in ln]
    assert calls, "no pipelines call made"
    assert all("per_page=20" in ln for ln in calls), calls


def test_help_prints_only_the_header() -> None:
    res = subprocess.run(
        [BASH, str(GHREPORT), "--help"], capture_output=True, check=False, text=True, timeout=60,
    )
    assert res.returncode == 0
    lines = res.stdout.splitlines()
    assert lines, "help printed nothing"
    assert not any('=""' in ln for ln in lines), res.stdout
    assert all(ln.startswith("#") for ln in lines), res.stdout
    assert any("--provider P" in ln for ln in lines)
    assert any("--render-dir DIR" in ln for ln in lines)


def test_help_ends_at_the_closing_header_line() -> None:
    res = subprocess.run(
        [BASH, str(GHREPORT), "--help"], capture_output=True, check=False, text=True, timeout=60,
    )
    header = GHREPORT.read_text().splitlines()
    closing = next(i for i, ln in enumerate(header) if i > 3 and ln.startswith("# ===="))
    assert res.stdout.splitlines() == header[3 : closing + 1]
    assert "(skips discovery + querying; replay / testing)" in res.stdout


@pytest.mark.parametrize("edit", ["add", "remove"])
def test_help_range_follows_header_edits(tmp_path: Path, edit: str) -> None:
    """The help range is not a fixed line count: a header line added or
    removed still leaves --help ending at the closing rule."""
    lines = GHREPORT.read_text().splitlines()
    closing = next(i for i, ln in enumerate(lines) if i > 3 and ln.startswith("# ===="))
    if edit == "add":
        lines[closing:closing] = ["#   --extra X        An added header line"]
    else:
        del lines[closing - 2]
    copy = tmp_path / "ghreport.sh"
    copy.write_text("\n".join(lines) + "\n", newline="\n")
    res = subprocess.run(
        [BASH, str(copy), "--help"], capture_output=True, check=False, text=True, timeout=60,
    )
    assert res.returncode == 0
    out = res.stdout.splitlines()
    new_closing = next(i for i, ln in enumerate(lines) if i > 3 and ln.startswith("# ===="))
    assert out == lines[3 : new_closing + 1]
    assert out[-1].startswith("# ====")
    assert all(ln.startswith("#") for ln in out)


def test_fragment_names_do_not_collide(tmp_path: Path) -> None:
    overrides = _collision_overrides()
    expected = _listed_projects({**_endpoints(), **overrides})
    env = Env(tmp_path, overrides)
    res = env.run("--host", HOST, "--group", "grp", "--no-file")
    assert res.returncode == 0, res.stderr
    for p in COLLIDING:
        assert _cells(_row(res.stdout, p))[0] == p
    assert f"{expected} project(s)" in res.stdout
    assert _cells(_row(res.stdout, "grp/alpha")) == [
        "grp/alpha", "main", "2", "success", "no-access", "protected",
    ]


def test_empty_repository_reports_empty(tmp_path: Path) -> None:
    env = Env(tmp_path, EMPTY)
    res = env.run("--host", HOST)
    assert res.returncode == 0, res.stderr
    cells = _cells(_row(res.stdout, "grp/alpha"))
    assert cells[1] == "empty" and cells[3] == "empty" and cells[5] == "empty", cells
    calls = env.api_calls()
    assert not any("grp%2Falpha/pipelines" in c for c in calls), calls
    assert not any("grp%2Falpha/protected_branches" in c for c in calls), calls
    assert "grp/alpha" not in _attention(res.stdout)
    assert "1 with an unprotected default branch" in res.stdout
    assert ", 1 empty" in res.stdout
    # The neighbours are unchanged.
    assert _cells(_row(res.stdout, "grp/sub/beta"))[1:] == ["main", "0", "none", "no-access", "unprotected"]
    assert _cells(_row(res.stdout, "grp/sub/deep/gamma"))[1:] == ["main", "no-access", "failed", "2", "protected"]
    report = next(env.root.glob(f"org-state-{HOST}-*.md")).read_text()
    assert "`empty`" in report.split("## Legend")[-1]


def test_empty_repository_with_limit(tmp_path: Path) -> None:
    env = Env(tmp_path, EMPTY)
    res = env.run("--host", HOST, "--limit", "1", "--no-file")
    assert res.returncode == 0, res.stderr
    assert "1 project(s)" in res.stdout
    assert _cells(_row(res.stdout, "grp/alpha"))[1:] == ["empty", "2", "empty", "no-access", "empty"]
    assert "Needs attention: none" in res.stdout


def test_empty_repo_flag_with_a_branch_name_reports_empty(tmp_path: Path) -> None:
    """GitLab can report empty_repo: true while still naming a default
    branch. ghsync discovery treats that as empty, so ghreport does too:
    no pipeline or protection call, and no false `unprotected` verdict."""
    project = {**_project("grp/alpha"), "default_branch": "main", "empty_repo": True}
    env = Env(tmp_path, {ALPHA: [project]})
    res = env.run("--host", HOST, "--limit", "1", "--no-file")
    assert res.returncode == 0, res.stderr
    assert _cells(_row(res.stdout, "grp/alpha"))[1:] == ["empty", "2", "empty", "no-access", "empty"]
    calls = env.api_calls()
    assert not any("grp%2Falpha/pipelines" in c for c in calls), calls
    assert not any("grp%2Falpha/protected_branches" in c for c in calls), calls
    assert "Needs attention: none" in res.stdout


def test_empty_repository_with_open_vulnerabilities_needs_attention(tmp_path: Path) -> None:
    """GitLab keeps vulnerability records from earlier scans. Emptiness does
    not hide them: the project is listed under Needs attention for them, and
    for nothing about its pipeline or protection."""
    vulns: Endpoints = {f"{ALPHA}/vulnerabilities": [[{"id": 7, "state": "detected"}]]}
    env = Env(tmp_path, {**EMPTY, **vulns})
    res = env.run("--host", HOST, "--limit", "1", "--no-file")
    assert res.returncode == 0, res.stderr
    assert _cells(_row(res.stdout, "grp/alpha"))[1:] == ["empty", "2", "empty", "1", "empty"]
    assert "Needs attention: none" not in res.stdout
    lines = [ln for ln in _attention(res.stdout).splitlines() if "**grp/alpha**" in ln]
    assert lines == ["- **grp/alpha**: 1 open vulnerability"], res.stdout


def test_render_dir_tells_an_empty_repo_from_a_branch_named_empty(tmp_path: Path) -> None:
    """The fragment marks emptiness with empty_repo and a null branch, so a
    replay keeps a real branch named `empty` apart from an empty project."""
    frags = tmp_path / "frags"
    frags.mkdir()
    base = {"unreachable": False, "open_mrs": 0, "draft_mrs": 0, "vulnerabilities": "no-access"}
    (frags / "0.json").write_text(json.dumps({
        **base, "repo": "grp/real", "default_branch": "empty", "empty_repo": False,
        "pipeline": "success", "protection": "protected",
    }), newline="\n")
    (frags / "1.json").write_text(json.dumps({
        **base, "repo": "grp/blank", "default_branch": None, "empty_repo": True,
        "pipeline": "empty", "protection": "empty",
    }), newline="\n")
    res = subprocess.run(
        [BASH, str(GHREPORT), "--provider", "gitlab", "--render-dir", str(frags), "--no-file"],
        capture_output=True, check=False, text=True, timeout=60,
    )
    assert res.returncode == 0, res.stderr
    assert _cells(_row(res.stdout, "grp/real"))[1:] == ["empty", "0", "success", "no-access", "protected"]
    assert _cells(_row(res.stdout, "grp/blank"))[1:] == ["empty", "0", "empty", "no-access", "empty"]
    assert ", 1 empty" in res.stdout
    assert "Needs attention: none" in res.stdout
