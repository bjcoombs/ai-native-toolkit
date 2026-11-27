"""Shared fixture for the behavioural contract of ghsync's GitLab provider (`--provider gitlab`).

Runs the real `ghsync.sh` offline. `glab` is stubbed on PATH and serves
GitLab REST JSON from files this module writes; `git clone` of
`https://gl.test/<path>.git` (or the ssh forms) is rewritten by a throwaway
global git config (`GIT_CONFIG_GLOBAL`) with `url.<base>.insteadOf` onto
local bare repos. A `gh` stub that always fails proves the GitLab path never
calls the GitHub CLI. No network, no auth.

Fixture tree on the host `gl.test`:

- `grp` (id 1), the only group the user is a member of, with descendants
  `grp/sub` (2), `grp/sub/deep` (3) and the empty `grp/empty` (4).
- Projects: `grp/.dotfiles` (a leading dot, which GitLab allows),
  `grp/alpha`, `grp/omega` (served on a second page, so it is
  found only when discovery passes `--paginate`), `grp/sub/beta`,
  `grp/sub/alpha` (shares a basename with `grp/alpha`),
  `grp/sub/deep/gamma`, and the archived `grp/old`.
- `other/ns/shared` lives outside `grp` and is shared into both `grp` and
  `grp/sub`; `grp/sub/beta` is also shared into `grp`. Each is listed once.
- The user's direct project memberships and starred projects are empty by
  default; a test fills them with `_serve`.

The tests are split over several files so that `--dist loadfile` can run
them on separate workers and Windows CI shards:
- `test_ghsync_gitlab.py`
- `test_ghsync_gitlab_sync.py`
- `test_ghsync_gitlab_from_list.py`
"""
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from gitbash import BASH, IS_WINDOWS, base_env, write_script

REPO = Path(__file__).resolve().parent.parent
GHSYNC = REPO / "skills" / "ghsync" / "scripts" / "ghsync.sh"
HOST = "gl.test"

SYNCED = [
    "grp/.dotfiles",
    "grp/alpha",
    "grp/omega",
    "grp/sub/alpha",
    "grp/sub/beta",
    "grp/sub/deep/gamma",
    "other/ns/shared",
]
ARCHIVED = ["grp/old"]
SUB_TREE = ["grp/sub/alpha", "grp/sub/beta", "grp/sub/deep/gamma", "other/ns/shared"]

GROUPS = {
    "grp": 1,
    "grp/sub": 2,
    "grp/sub/deep": 3,
    "grp/empty": 4,
}


def _project(path: str, archived: bool = False) -> dict[str, object]:
    return {
        "id": abs(hash(path)) % 100000,
        "path_with_namespace": path,
        "path": path.rsplit("/", 1)[-1],
        "default_branch": "main",
        "archived": archived,
        "empty_repo": False,
        "http_url_to_repo": f"https://{HOST}/{path}.git",
        "ssh_url_to_repo": f"git@{HOST}:{path}.git",
    }


def _group(full_path: str) -> dict[str, object]:
    return {
        "id": GROUPS[full_path],
        "full_path": full_path,
        "path": full_path.rsplit("/", 1)[-1],
    }


# Endpoint (decoded, no query string) -> list of pages. A second page is
# served only when the caller passes --paginate.
ENDPOINTS: dict[str, list[list[dict[str, object]]]] = {
    "groups": [[_group("grp")]],
    "groups/grp/descendant_groups": [
        [_group("grp/sub"), _group("grp/sub/deep"), _group("grp/empty")]
    ],
    "groups/grp/sub/descendant_groups": [[_group("grp/sub/deep")]],
    "groups/grp/sub/deep/descendant_groups": [[]],
    "groups/grp/empty/descendant_groups": [[]],
    "groups/grp/projects": [
        [_project("grp/.dotfiles"), _project("grp/alpha"), _project("grp/old", True)],
        [_project("grp/omega")],
    ],
    "groups/grp/projects/shared": [
        [_project("other/ns/shared"), _project("grp/sub/beta")]
    ],
    "groups/grp/sub/projects": [[_project("grp/sub/beta"), _project("grp/sub/alpha")]],
    "groups/grp/sub/projects/shared": [[_project("other/ns/shared")]],
    "groups/grp/sub/deep/projects": [[_project("grp/sub/deep/gamma")]],
    "groups/grp/sub/deep/projects/shared": [[]],
    "groups/grp/empty/projects": [[]],
    "groups/grp/empty/projects/shared": [[]],
    # projects?membership=true and projects?starred=true (see the stub).
    "projects/membership": [[]],
    "projects/starred": [[]],
}

# `glab` stub. Endpoints are looked up by numeric id or by (URL-encoded)
# path after the query string is dropped, except `projects?membership=true`
# and `projects?starred=true`, served from `projects/membership` and
# `projects/starred`. `auth status` succeeds only for
# hosts in GLAB_OK_HOSTS and reports a DNS failure for GLAB_DOWN_HOSTS.
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
    for ok in $GLAB_OK_HOSTS; do [ "$ok" = "$host" ] && exit 0; done
    for down in $GLAB_DOWN_HOSTS; do
      if [ "$down" = "$host" ]; then
        echo "error: dial tcp: lookup $host: no such host" >&2
        exit 1
      fi
    done
    echo "$host: not logged in" >&2
    exit 1 ;;
  api)
    shift
    [ -n "${GLAB_API_FAIL:-}" ] && { echo "glab: api unavailable" >&2; exit 1; }
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
    query=""
    case "$endpoint" in *\?*) query=${endpoint#*\?} ;; esac
    endpoint=${endpoint%%\?*}
    # The top-level project listing is one endpoint whose filter is in the
    # query string: serve membership=true and starred=true from their own
    # fixture keys.
    if [ "$endpoint" = projects ]; then
      case "&$query&" in
        *"&starred=true&"*) endpoint=projects/starred ;;
        *"&membership=true&"*) endpoint=projects/membership ;;
      esac
    fi
    endpoint=${endpoint//%2F//}
    endpoint=${endpoint//%2f//}
    # Numeric group id -> path.
    if [[ "$endpoint" =~ ^groups/([0-9]+)(/.*)?$ ]]; then
      gpath=$(cat "$GLAB_DATA/ids/${BASH_REMATCH[1]}" 2>/dev/null) || {
        echo "404 Not Found" >&2; exit 1; }
      endpoint="groups/$gpath${BASH_REMATCH[2]}"
    fi
    key=${endpoint//\//__}
    [ -f "$GLAB_DATA/$key.1.json" ] || { echo "404 Not Found: $endpoint" >&2; exit 1; }
    cat "$GLAB_DATA/$key.1.json"
    if [ "$paginate" = true ]; then
      n=2
      while [ -f "$GLAB_DATA/$key.$n.json" ]; do
        cat "$GLAB_DATA/$key.$n.json"
        n=$((n + 1))
      done
    fi ;;
  repo)
    [ "$2" = clone ] && exec git clone -q "https://$GLAB_CLONE_HOST/$3.git" "$4"
    exit 1 ;;
  *) exit 1 ;;
esac
"""

GH_STUB = """#!/usr/bin/env bash
echo "gh $*" >> "$GH_LOG"
exit 1
"""

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None
    or shutil.which("jq") is None
    or shutil.which("git") is None,
    reason="requires bash, jq and git on PATH",
)


class Env:
    def __init__(self, tmp_path: Path) -> None:
        self.tmp = tmp_path
        self.bare = tmp_path / "bare"
        self.root = tmp_path / "root"
        self.root.mkdir()
        self.stub_dir = tmp_path / "bin"
        self.stub_dir.mkdir()
        for name, body in (("glab", GLAB_STUB), ("gh", GH_STUB)):
            stub = self.stub_dir / name
            stub.write_text(body, newline="\n")
            stub.chmod(0o755)
        data = tmp_path / "glab-data"
        (data / "ids").mkdir(parents=True)
        for path, gid in GROUPS.items():
            (data / "ids" / str(gid)).write_text(path, newline="\n")
        for endpoint, pages in ENDPOINTS.items():
            key = endpoint.replace("/", "__")
            for n, page in enumerate(pages, start=1):
                (data / f"{key}.{n}.json").write_text(json.dumps(page), newline="\n")
        self.data = data
        self.glab_log = tmp_path / "glab.log"
        self.gh_log = tmp_path / "gh.log"
        gitconfig = tmp_path / "gitconfig"
        base = f"{self.bare.as_uri()}/"
        gitconfig.write_text(
            "[user]\n\tname = Test\n\temail = test@example.com\n"
            "[init]\n\tdefaultBranch = main\n"
            "[commit]\n\tgpgsign = false\n"
            "[uploadpack]\n\tallowFilter = true\n"
            f'[url "{base}"]\n'
            f"\tinsteadOf = https://{HOST}/\n"
            f"\tinsteadOf = https://{HOST}:8443/\n"
            f"\tinsteadOf = http://{HOST}/\n"
            f"\tinsteadOf = ssh://git@{HOST}/\n"
            f"\tinsteadOf = git@{HOST}:\n",
            newline="\n",
        )
        self.env = {
            **base_env(),
            "PATH": f"{self.stub_dir}{os.pathsep}{os.environ['PATH']}",
            "HOME": str(tmp_path),
            "XDG_CONFIG_HOME": str(tmp_path / ".config"),
            "GLAB_CONFIG_DIR": str(tmp_path / ".glab"),
            "GH_CONFIG_DIR": str(tmp_path / ".gh"),
            "GIT_CONFIG_GLOBAL": str(gitconfig),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GLAB_LOG": str(self.glab_log),
            "GH_LOG": str(self.gh_log),
            "GLAB_DATA": str(data),
            "GLAB_OK_HOSTS": HOST,
            "GLAB_DOWN_HOSTS": "gl.down",
            "GLAB_CLONE_HOST": HOST,
        }
        for path in SYNCED + ARCHIVED:
            self._make_bare(path)

    def git(self, *args: str, cwd: Path) -> str:
        res = subprocess.run(
            ["git", *args],
            cwd=cwd,
            env=self.env,
            capture_output=True,
            text=True,
            check=True,
        )
        return res.stdout.strip()

    def _make_bare(self, path: str) -> None:
        bare = self.bare / f"{path}.git"
        bare.parent.mkdir(parents=True, exist_ok=True)
        self.git("init", "-q", "--bare", "-b", "main", str(bare), cwd=self.tmp)
        work = self.tmp / "seed" / path
        self.git("clone", "-q", str(bare), str(work), cwd=self.tmp)
        (work / "README").write_text(f"{path}\n", newline="\n")
        self.git("add", "README", cwd=work)
        self.git("commit", "-q", "-m", "init", cwd=work)
        self.git("push", "-q", "origin", "main", cwd=work)

    def advance(self, path: str, date: str | None = None) -> str:
        work = self.tmp / "seed" / path
        with (work / "CHANGES").open("a") as fh:
            fh.write("more\n")
        self.git("add", "CHANGES", cwd=work)
        dated = {"GIT_AUTHOR_DATE": date, "GIT_COMMITTER_DATE": date} if date else {}
        subprocess.run(
            ["git", "commit", "-q", "-m", "advance"],
            cwd=work,
            check=True,
            env={**self.env, **dated},
        )
        self.git("push", "-q", "origin", "main", cwd=work)
        return self.git("rev-parse", "HEAD", cwd=work)

    def checkout(self, path: str, root: Path | None = None) -> Path:
        return (root or self.root) / path / f"{path.rsplit('/', 1)[-1]}-main"

    def head(self, path: str) -> str:
        return self.git("rev-parse", "HEAD", cwd=self.checkout(path))

    def run(
        self, *args: str, env: dict[str, str] | None = None, root: Path | None = None
    ) -> subprocess.CompletedProcess[str]:
        cmd = [BASH, str(GHSYNC), "--provider", "gitlab", *args]
        if root is not None:
            cmd += ["--root", str(root)]
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            env={**self.env, **(env or {})},
            cwd=self.tmp,
            timeout=120,
        )

    def sync_at(self, root: Path) -> subprocess.CompletedProcess[str]:
        return self.run("--host", HOST, "--quiet", root=root)

    def sync(self, *args: str) -> subprocess.CompletedProcess[str]:
        return self.run("--host", HOST, "--quiet", *args, root=self.root)

    def api_calls(self) -> list[str]:
        if not self.glab_log.exists():
            return []
        return [
            ln
            for ln in self.glab_log.read_text().splitlines()
            if ln.startswith("glab api")
        ]


def _section(output: str, heading: str) -> list[str]:
    lines = output.splitlines()
    for i, line in enumerate(lines):
        if line.startswith(f"{heading} ("):
            body = []
            for entry in lines[i + 1 :]:
                if not entry.strip():
                    break
                body.append(entry)
            return body
    return []


def _checkouts(root: Path) -> set[str]:
    return {p.parent.parent.relative_to(root).as_posix() for p in root.rglob("*-main/.git")}


# ---- analysis-only mirrors ----------------------------------------------------


def _config(env: Env, cwd: Path, key: str) -> str | None:
    res = subprocess.run(
        ["git", "config", "--get", key],
        cwd=cwd,
        env=env.env,
        capture_output=True,
        text=True,
    )
    return res.stdout.strip() if res.returncode == 0 else None


def _missing_objects(env: Env, cwd: Path) -> list[str]:
    out = env.git("rev-list", "--objects", "--missing=print", "--all", cwd=cwd)
    return [ln for ln in out.splitlines() if ln.startswith("?")]


# ---- fail fast --------------------------------------------------------------


def _path_without_glab(tmp_path: Path) -> str:
    """A PATH holding only the tools the script needs, and no glab."""
    bin_dir = tmp_path / "noglab"
    bin_dir.mkdir(exist_ok=True)
    extra_dirs: list[str] = []
    for tool in (
        "bash",
        "git",
        "jq",
        "mktemp",
        "sort",
        "wc",
        "tr",
        "basename",
        "dirname",
        "cat",
        "rm",
        "rmdir",
        "mkdir",
        "sed",
        "grep",
        "awk",
        "cut",
        "mv",
        "cp",
        "head",
        "env",
        "timeout",
        "gtimeout",
        "ssh",
    ):
        found = shutil.which(tool)
        if found is None:
            continue
        if IS_WINDOWS:
            # A Git Bash tool cannot be symlinked out of its directory (it
            # loads msys-2.0.dll from beside it), so its directory goes on
            # PATH instead, unless that directory also holds a glab.
            tool_dir = str(Path(found).parent)
            if tool_dir not in extra_dirs:
                extra_dirs.append(tool_dir)
        elif not (bin_dir / tool).exists():
            (bin_dir / tool).symlink_to(found)
    gh = bin_dir / "gh"
    if not gh.exists():
        write_script(gh, GH_STUB)
    glab = shutil.which("glab")
    if glab is not None and str(Path(glab).parent) in extra_dirs:
        pytest.skip("glab shares a directory with a tool the script needs")
    return os.pathsep.join([str(bin_dir), *extra_dirs])


# ---- --group with partial membership -----------------------------------------


def _member_of_sub_only(env: Env) -> None:
    groups = Path(env.env["GLAB_DATA"]) / "groups.1.json"
    groups.write_text(json.dumps([_group("grp/sub")]), newline="\n")


# ---- direct memberships and starred projects ----------------------------------


def _serve(env: Env, endpoint: str, *pages: list[dict[str, object]]) -> None:
    """Replace the pages the glab stub serves for one fixture endpoint."""
    data = Path(env.env["GLAB_DATA"])
    key = endpoint.replace("/", "__")
    for old in data.glob(f"{key}.*.json"):
        old.unlink()
    for n, page in enumerate(pages, start=1):
        (data / f"{key}.{n}.json").write_text(json.dumps(page), newline="\n")
