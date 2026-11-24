"""Shared fixture for the behavioural contract of ghreport's GitLab provider (`--provider gitlab`).

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

The tests are split over several files so that `--dist loadfile` can run
them on separate workers and Windows CI shards:
- `test_ghreport_gitlab.py`
- `test_ghreport_gitlab_settings.py`
"""
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from gitbash import BASH

REPO = Path(__file__).resolve().parent.parent
GHREPORT = REPO / "skills" / "ghreport" / "scripts" / "ghreport.sh"
HOST = "gl.test"

GROUPS = {"grp": 1, "grp/sub": 2, "grp/sub/deep": 3, "grp/empty": 4}

# A page is a JSON array, one object for a single-resource endpoint, or a
# str served as a raw body.
Pages = list[list[dict[str, object]] | dict[str, object] | str]
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
# status to fail with; <key>.fail-after holds a call count after which the
# endpoint answers 500.
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
    if [ -f "$GLAB_DATA/$key.fail-after" ]; then
      calls=$(( $(cat "$GLAB_DATA/$key.count" 2>/dev/null || echo 0) + 1 ))
      echo "$calls" > "$GLAB_DATA/$key.count"
      if [ "$calls" -gt "$(cat "$GLAB_DATA/$key.fail-after")" ]; then
        echo "glab: 500 (HTTP 500)" >&2
        exit 1
      fi
    fi
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
            stub.write_text(body, newline="\n")
            stub.chmod(0o755)
        data = tmp_path / "glab-data"
        (data / "ids").mkdir(parents=True)
        for path, gid in GROUPS.items():
            (data / "ids" / str(gid)).write_text(path, newline="\n")
        endpoints = _endpoints()
        endpoints.update(overrides or {})
        for endpoint, value in endpoints.items():
            key = endpoint.replace("/", "__")
            if isinstance(value, int):
                (data / f"{key}.err").write_text(str(value), newline="\n")
                continue
            for n, page in enumerate(value, start=1):
                (data / f"{key}.{n}.json").write_text(page if isinstance(page, str) else json.dumps(page), newline="\n")
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

    def path_first(self, directory: Path) -> None:
        """Put a directory ahead of the stubs on PATH."""
        self.env["PATH"] = f"{directory}{os.pathsep}{self.env['PATH']}"

    def run(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [BASH, str(GHREPORT), "--provider", "gitlab", "--root", str(self.root), *args],
            capture_output=True,
            check=False,
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


ALPHA = "projects/grp/alpha"


# Paths with `__` at every position. Flattening `/` to `__` maps each pair
# onto one fragment name. The stub keys its data the same way, so each pair
# shares one metadata file and their rows carry the same cells; the point is
# that both rows appear.
COLLIDING = ["grp/a__b", "grp__a/b", "grp/__c", "grp__/c", "grp/d__/e", "grp/d/__e"]


def _collision_overrides() -> Endpoints:
    overrides: Endpoints = {"groups/grp/projects": [[_project(p) for p in ["grp/alpha", *COLLIDING]]]}
    for p in COLLIDING:
        overrides[f"projects/{p}"] = [_project(p)]
    return overrides


def _listed_projects(endpoints: Endpoints) -> int:
    """Distinct projects across the group listings discovery reads."""
    paths = {
        str(proj["path_with_namespace"])
        for key, pages in endpoints.items()
        if key.startswith("groups/") and key.endswith(("/projects", "/projects/shared"))
        and not isinstance(pages, int)
        for page in pages
        if isinstance(page, list)
        for proj in page
    }
    return len(paths)


def _empty_project(path: str) -> dict[str, object]:
    # GitLab's shape for a repository with no commits.
    return {**_project(path), "default_branch": None, "empty_repo": True}


# The listing ghsync reads still shows a branch (ghsync skips empty
# projects), but the project's own metadata says it is empty: a repository
# emptied between the two calls.
EMPTY = {ALPHA: [_empty_project("grp/alpha")]}


# ---- group settings ----------------------------------------------------------
# The base fixture serves neither `groups/grp` nor `groups/grp/push_rule`, so
# by default grp is a group the token cannot read: every setting is unknown.

SETTINGS = ("two_factor", "push_rules", "default_branch_protection", "rulesets")


def _settings(out: str, scope: str) -> dict[str, str]:
    """The `  - field: value` lines under `- <scope>` in the settings section."""
    lines = out.split("Group settings")[1].splitlines()
    start = lines.index(f"- {scope}")
    found: dict[str, str] = {}
    for ln in lines[start + 1:]:
        if not ln.startswith("  - "):
            break
        key, value = ln[4:].split(": ", 1)
        found[key] = value
    return found


def _group_json(**fields: object) -> list[dict[str, object]]:
    return [{"id": 1, "full_path": "grp", **fields}]
