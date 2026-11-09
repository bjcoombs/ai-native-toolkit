"""Behavioural contract for ghreport's GitLab provider (`--provider gitlab`).

Runs the real `ghreport.sh`, which runs the real `ghsync.sh` for discovery,
offline. `glab` is stubbed on PATH and serves GitLab REST JSON from files
this module writes: an endpoint maps to a list of pages (a later page is
served only under `--paginate`) or to an HTTP status code, which the stub
prints as `glab: <code> (HTTP <code>)` before exiting 1. A `gh` stub that
always fails proves the GitLab path never calls the GitHub CLI.

Fixture on host `gl.test`: the user belongs to `grp` only, which has the
subgroups `grp/sub`, `grp/sub/deep` and the empty `grp/empty`.

| Project | MRs | Pipeline on main | Vulnerabilities | Protection |
|---------|-----|------------------|-----------------|------------|
| `grp/alpha` | 2, across two pages | success | 403 | `main` listed |
| `grp/sub/beta` | none | no pipeline | 404 | only `release/*` |
| `grp/sub/deep/gamma` | 500 | failed | 2 open, 1 resolved | list 403, single 200 |
| `grp/sub/deep/ghost` | project metadata 404 | - | - | - |
| `other/ns/shared` (shared into `grp`) | 1 draft, 1 closed | 403 | 500 | list 404, single 404 |
"""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
GHREPORT = REPO / "skills" / "ghreport" / "scripts" / "ghreport.sh"
HOST = "gl.test"

GROUPS = {"grp": 1, "grp/sub": 2, "grp/sub/deep": 3, "grp/empty": 4}

# A page is a JSON array, or one object for a single-resource endpoint.
Pages = list[list[dict[str, object]] | dict[str, object]]
Endpoints = dict[str, "Pages | int"]


def _project(path: str) -> dict[str, object]:
    return {
        "id": abs(hash(path)) % 100000,
        "path_with_namespace": path,
        "path": path.rsplit("/", 1)[-1],
        "default_branch": "main",
        "archived": False,
        "empty_repo": False,
        "http_url_to_repo": f"https://{HOST}/{path}.git",
        "ssh_url_to_repo": f"git@{HOST}:{path}.git",
    }


def _group(full_path: str) -> dict[str, object]:
    return {"id": GROUPS[full_path], "full_path": full_path, "path": full_path.rsplit("/", 1)[-1]}


def _mr(iid: int, state: str = "opened", draft: bool = False) -> dict[str, object]:
    return {"iid": iid, "state": state, "draft": draft}


def _pipeline(pid: int, status: str, ref: str = "main") -> dict[str, object]:
    return {"id": pid, "status": status, "ref": ref}


def _endpoints() -> Endpoints:
    alpha, beta, gamma, ghost, shared = (
        "projects/grp/alpha",
        "projects/grp/sub/beta",
        "projects/grp/sub/deep/gamma",
        "projects/grp/sub/deep/ghost",
        "projects/other/ns/shared",
    )
    return {
        # Discovery, as ghsync reads it.
        "groups": [[_group("grp")]],
        "groups/grp/descendant_groups": [
            [_group("grp/sub"), _group("grp/sub/deep"), _group("grp/empty")]
        ],
        "groups/grp/sub/descendant_groups": [[_group("grp/sub/deep")]],
        "groups/grp/sub/deep/descendant_groups": [[]],
        "groups/grp/empty/descendant_groups": [[]],
        "groups/grp/projects": [[_project("grp/alpha")]],
        "groups/grp/projects/shared": [[_project("other/ns/shared")]],
        "groups/grp/sub/projects": [[_project("grp/sub/beta")]],
        "groups/grp/sub/projects/shared": [[]],
        "groups/grp/sub/deep/projects": [
            [_project("grp/sub/deep/gamma"), _project("grp/sub/deep/ghost")]
        ],
        "groups/grp/sub/deep/projects/shared": [[]],
        "groups/grp/empty/projects": [[]],
        "groups/grp/empty/projects/shared": [[]],
        # grp/alpha: the GL10 shape.
        alpha: [_project("grp/alpha")],
        f"{alpha}/merge_requests": [[_mr(1)], [_mr(2)]],
        f"{alpha}/pipelines": [[_pipeline(10, "success")]],
        f"{alpha}/vulnerabilities": 403,
        f"{alpha}/protected_branches": [[{"name": "main"}]],
        # grp/sub/beta: empty everywhere; protection covers another branch.
        beta: [_project("grp/sub/beta")],
        f"{beta}/merge_requests": [[]],
        f"{beta}/pipelines": [[]],
        f"{beta}/vulnerabilities": 404,
        f"{beta}/protected_branches": [[{"name": "release/*"}]],
        # grp/sub/deep/gamma: failures and real findings.
        gamma: [_project("grp/sub/deep/gamma")],
        f"{gamma}/merge_requests": 500,
        f"{gamma}/pipelines": [[_pipeline(30, "failed")]],
        f"{gamma}/vulnerabilities": [
            [{"id": 1, "state": "detected"}, {"id": 2, "state": "confirmed"}],
            [{"id": 3, "state": "resolved"}],
        ],
        f"{gamma}/protected_branches": 403,
        f"{gamma}/protected_branches/main": [{"name": "main"}],
        # grp/sub/deep/ghost: listed, but its metadata is unreadable.
        ghost: 404,
        # other/ns/shared: shared into grp; everything denied but MRs.
        shared: [_project("other/ns/shared")],
        f"{shared}/merge_requests": [[_mr(5, draft=True), _mr(6, state="closed")]],
        f"{shared}/pipelines": 403,
        f"{shared}/vulnerabilities": 500,
        f"{shared}/protected_branches": 404,
        f"{shared}/protected_branches/main": 404,
    }


# Endpoints are looked up after the query string is dropped and %2F decoded;
# numeric group ids resolve through ids/<id>. A file <key>.err holds an HTTP
# status to fail with.
GLAB_STUB = r"""#!/usr/bin/env bash
echo "glab $*" >> "$GLAB_LOG"
case "$1" in
  auth)
    host=""
    while [ $# -gt 0 ]; do
      case "$1" in
        --hostname|-h) host=$2; shift 2 ;;
        --hostname=*) host=${1#*=}; shift ;;
        *) shift ;;
      esac
    done
    [ "$host" = "$GLAB_OK_HOST" ] && exit 0
    echo "$host: not logged in" >&2
    exit 1 ;;
  api)
    shift
    paginate=false
    endpoint=""
    while [ $# -gt 0 ]; do
      case "$1" in
        --paginate) paginate=true; shift ;;
        --hostname|-h) shift 2 ;;
        --hostname=*) shift ;;
        *) endpoint=$1; shift ;;
      esac
    done
    endpoint=${endpoint%%\?*}
    endpoint=${endpoint//%2F//}
    endpoint=${endpoint//%2f//}
    if [[ "$endpoint" =~ ^groups/([0-9]+)(/.*)?$ ]]; then
      gpath=$(cat "$GLAB_DATA/ids/${BASH_REMATCH[1]}" 2>/dev/null) || {
        echo "glab: 404 Not Found (HTTP 404)" >&2; exit 1; }
      endpoint="groups/$gpath${BASH_REMATCH[2]}"
    fi
    key=${endpoint//\//__}
    if [ -f "$GLAB_DATA/$key.err" ]; then
      code=$(cat "$GLAB_DATA/$key.err")
      echo "glab: $code (HTTP $code)" >&2
      exit 1
    fi
    [ -f "$GLAB_DATA/$key.1.json" ] || { echo "glab: 404 Not Found (HTTP 404)" >&2; exit 1; }
    cat "$GLAB_DATA/$key.1.json"
    if [ "$paginate" = true ]; then
      n=2
      while [ -f "$GLAB_DATA/$key.$n.json" ]; do
        cat "$GLAB_DATA/$key.$n.json"
        n=$((n + 1))
      done
    fi ;;
  *) exit 1 ;;
esac
"""

GH_STUB = """#!/usr/bin/env bash
echo "gh $*" >> "$GH_LOG"
exit 1
"""

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None or shutil.which("jq") is None,
    reason="requires bash and jq on PATH",
)


class Env:
    def __init__(self, tmp_path: Path, overrides: Endpoints | None = None) -> None:
        self.tmp = tmp_path
        self.root = tmp_path / "root"
        self.root.mkdir()
        stub_dir = tmp_path / "bin"
        stub_dir.mkdir()
        for name, body in (("glab", GLAB_STUB), ("gh", GH_STUB)):
            stub = stub_dir / name
            stub.write_text(body)
            stub.chmod(0o755)
        data = tmp_path / "glab-data"
        (data / "ids").mkdir(parents=True)
        for path, gid in GROUPS.items():
            (data / "ids" / str(gid)).write_text(path)
        endpoints = _endpoints()
        endpoints.update(overrides or {})
        for endpoint, value in endpoints.items():
            key = endpoint.replace("/", "__")
            if isinstance(value, int):
                (data / f"{key}.err").write_text(str(value))
                continue
            for n, page in enumerate(value, start=1):
                (data / f"{key}.{n}.json").write_text(json.dumps(page))
        self.glab_log = tmp_path / "glab.log"
        self.gh_log = tmp_path / "gh.log"
        home = tmp_path / "home"
        home.mkdir()
        self.env = {
            **os.environ,
            "PATH": f"{stub_dir}{os.pathsep}{os.environ['PATH']}",
            "HOME": str(home),
            "GLAB_LOG": str(self.glab_log),
            "GH_LOG": str(self.gh_log),
            "GLAB_DATA": str(data),
            "GLAB_OK_HOST": HOST,
            "GIT_CONFIG_GLOBAL": os.devnull,
        }

    def run(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(GHREPORT), "--provider", "gitlab", "--root", str(self.root), *args],
            capture_output=True,
            text=True,
            timeout=120,
            env=self.env,
            cwd=self.root,
        )

    def api_calls(self) -> list[str]:
        if not self.glab_log.exists():
            return []
        return [ln for ln in self.glab_log.read_text().splitlines() if ln.startswith("glab api")]


def _row(out: str, project: str) -> str:
    rows = [ln for ln in out.splitlines() if ln.startswith(f"| {project} |")]
    assert len(rows) == 1, f"expected one row for {project}, got {rows}\n{out}"
    return rows[0]


def _cells(row: str) -> list[str]:
    return [c.strip() for c in row.strip("|").split("|")]


def _attention(out: str) -> str:
    return out.split("Needs attention")[-1]


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
        ["bash", str(GHREPORT), "--host", HOST, "--render-dir", str(tmp_path)],
        capture_output=True, text=True, timeout=60,
    )
    assert res.returncode == 1
    assert "--host applies to --provider gitlab only" in res.stderr


ALPHA = "projects/grp/alpha"


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
        # Pipeline statuses pass through as GitLab reports them.
        ({f"{ALPHA}/pipelines": [[_pipeline(11, "running")]]}, 3, "running"),
        ({f"{ALPHA}/pipelines": [[_pipeline(12, "canceled")]]}, 3, "canceled"),
        ({f"{ALPHA}/pipelines": [[]]}, 3, "none"),
        ({f"{ALPHA}/pipelines": [[_pipeline(13, "failed", ref="feature")]]}, 3, "none"),
        # Protection: none, wildcard, and the single-branch fallback.
        ({f"{ALPHA}/protected_branches": [[]]}, 5, "unprotected"),
        ({f"{ALPHA}/protected_branches": [[{"name": "ma*"}]]}, 5, "protected"),
        ({f"{ALPHA}/protected_branches": [[{"name": "m.in"}]]}, 5, "unprotected"),
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
