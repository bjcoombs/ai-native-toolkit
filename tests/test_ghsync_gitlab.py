"""Behavioural contract for ghsync's GitLab provider (`--provider gitlab`).

Runs the real `ghsync.sh` offline. `glab` is stubbed on PATH and serves
GitLab REST JSON from files this module writes; `git clone` of
`https://gl.test/<path>.git` (or the ssh forms) is rewritten by a throwaway
global git config (`GIT_CONFIG_GLOBAL`) with `url.<base>.insteadOf` onto
local bare repos. A `gh` stub that always fails proves the GitLab path never
calls the GitHub CLI. No network, no auth.

Fixture tree on the host `gl.test`:

- `grp` (id 1), the only group the user is a member of, with descendants
  `grp/sub` (2), `grp/sub/deep` (3) and the empty `grp/empty` (4).
- Projects: `grp/alpha`, `grp/omega` (served on a second page, so it is
  found only when discovery passes `--paginate`), `grp/sub/beta`,
  `grp/sub/alpha` (shares a basename with `grp/alpha`),
  `grp/sub/deep/gamma`, and the archived `grp/old`.
- `other/ns/shared` lives outside `grp` and is shared into both `grp` and
  `grp/sub`; `grp/sub/beta` is also shared into `grp`. Each is listed once.
"""
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
GHSYNC = REPO / "skills" / "ghsync" / "scripts" / "ghsync.sh"
HOST = "gl.test"

SYNCED = [
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
    return {"id": GROUPS[full_path], "full_path": full_path,
            "path": full_path.rsplit("/", 1)[-1]}


# Endpoint (decoded, no query string) -> list of pages. A second page is
# served only when the caller passes --paginate.
ENDPOINTS: dict[str, list[list[dict[str, object]]]] = {
    "groups": [[_group("grp")]],
    "groups/grp/descendant_groups": [[_group("grp/sub"), _group("grp/sub/deep"),
                                      _group("grp/empty")]],
    "groups/grp/sub/descendant_groups": [[_group("grp/sub/deep")]],
    "groups/grp/sub/deep/descendant_groups": [[]],
    "groups/grp/empty/descendant_groups": [[]],
    "groups/grp/projects": [[_project("grp/alpha"), _project("grp/old", True)],
                            [_project("grp/omega")]],
    "groups/grp/projects/shared": [[_project("other/ns/shared"),
                                    _project("grp/sub/beta")]],
    "groups/grp/sub/projects": [[_project("grp/sub/beta"), _project("grp/sub/alpha")]],
    "groups/grp/sub/projects/shared": [[_project("other/ns/shared")]],
    "groups/grp/sub/deep/projects": [[_project("grp/sub/deep/gamma")]],
    "groups/grp/sub/deep/projects/shared": [[]],
    "groups/grp/empty/projects": [[]],
    "groups/grp/empty/projects/shared": [[]],
}

# `glab` stub. Endpoints are looked up by numeric id or by (URL-encoded)
# path after the query string is dropped. `auth status` succeeds only for
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
    endpoint=${endpoint%%\?*}
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
            stub.write_text(body)
            stub.chmod(0o755)
        data = tmp_path / "glab-data"
        (data / "ids").mkdir(parents=True)
        for path, gid in GROUPS.items():
            (data / "ids" / str(gid)).write_text(path)
        for endpoint, pages in ENDPOINTS.items():
            key = endpoint.replace("/", "__")
            for n, page in enumerate(pages, start=1):
                (data / f"{key}.{n}.json").write_text(json.dumps(page))
        self.glab_log = tmp_path / "glab.log"
        self.gh_log = tmp_path / "gh.log"
        gitconfig = tmp_path / "gitconfig"
        base = f"file://{self.bare}/"
        gitconfig.write_text(
            "[user]\n\tname = Test\n\temail = test@example.com\n"
            "[init]\n\tdefaultBranch = main\n"
            "[commit]\n\tgpgsign = false\n"
            f'[url "{base}"]\n'
            f"\tinsteadOf = https://{HOST}/\n"
            f"\tinsteadOf = http://{HOST}/\n"
            f"\tinsteadOf = ssh://git@{HOST}/\n"
            f"\tinsteadOf = git@{HOST}:\n"
        )
        self.env = {
            "PATH": f"{self.stub_dir}:{os.environ['PATH']}",
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
            ["git", *args], cwd=cwd, env=self.env,
            capture_output=True, text=True, check=True,
        )
        return res.stdout.strip()

    def _make_bare(self, path: str) -> None:
        bare = self.bare / f"{path}.git"
        bare.parent.mkdir(parents=True, exist_ok=True)
        self.git("init", "-q", "--bare", "-b", "main", str(bare), cwd=self.tmp)
        work = self.tmp / "seed" / path
        self.git("clone", "-q", str(bare), str(work), cwd=self.tmp)
        (work / "README").write_text(f"{path}\n")
        self.git("add", "README", cwd=work)
        self.git("commit", "-q", "-m", "init", cwd=work)
        self.git("push", "-q", "origin", "main", cwd=work)

    def advance(self, path: str) -> str:
        work = self.tmp / "seed" / path
        with (work / "CHANGES").open("a") as fh:
            fh.write("more\n")
        self.git("add", "CHANGES", cwd=work)
        self.git("commit", "-q", "-m", "advance", cwd=work)
        self.git("push", "-q", "origin", "main", cwd=work)
        return self.git("rev-parse", "HEAD", cwd=work)

    def checkout(self, path: str, root: Path | None = None) -> Path:
        return (root or self.root) / path / f"{path.rsplit('/', 1)[-1]}-main"

    def head(self, path: str) -> str:
        return self.git("rev-parse", "HEAD", cwd=self.checkout(path))

    def run(self, *args: str, env: dict[str, str] | None = None,
            root: Path | None = None) -> subprocess.CompletedProcess[str]:
        cmd = ["bash", str(GHSYNC), "--provider", "gitlab", *args]
        if root is not None:
            cmd += ["--root", str(root)]
        return subprocess.run(
            cmd, capture_output=True, text=True, env={**self.env, **(env or {})},
            cwd=self.tmp, timeout=120,
        )

    def sync(self, *args: str) -> subprocess.CompletedProcess[str]:
        return self.run("--host", HOST, "--quiet", *args, root=self.root)

    def api_calls(self) -> list[str]:
        if not self.glab_log.exists():
            return []
        return [ln for ln in self.glab_log.read_text().splitlines()
                if ln.startswith("glab api")]


def _section(output: str, heading: str) -> list[str]:
    lines = output.splitlines()
    for i, line in enumerate(lines):
        if line.startswith(f"{heading} ("):
            body = []
            for entry in lines[i + 1:]:
                if not entry.strip():
                    break
                body.append(entry)
            return body
    return []


def _checkouts(root: Path) -> set[str]:
    return {str(p.parent.parent.relative_to(root))
            for p in root.rglob("*-main/.git")}


@pytest.fixture
def env(tmp_path: Path) -> Env:
    return Env(tmp_path)


# ---- discovery --------------------------------------------------------------

def test_porcelain_lists_every_project_once(env: Env) -> None:
    res = env.run("--host", HOST, "--porcelain")
    assert res.returncode == 0, res.stderr
    assert res.stdout.splitlines() == SYNCED
    # Progress goes to stderr, and the paginated call carries --paginate.
    assert "Host:" in res.stderr
    assert all("--paginate" in call for call in env.api_calls())
    assert not env.gh_log.exists(), env.gh_log.read_text()


def test_default_root_is_dev_host(env: Env) -> None:
    res = env.run("--host", HOST, "--porcelain")
    assert res.returncode == 0, res.stderr
    cache = env.tmp / "dev" / HOST / ".gh_archived_repos"
    assert cache.read_text().split() == ARCHIVED


def test_list_repos_names_each_project(env: Env) -> None:
    res = env.run("--host", HOST, "--list-repos")
    assert res.returncode == 0, res.stderr
    for path in SYNCED:
        assert res.stdout.count(f"  - {path}\n") == 1, path
    assert "grp/old" not in res.stdout


def test_host_with_scheme_is_normalised(env: Env) -> None:
    res = env.run("--host", f"https://{HOST}/", "--porcelain")
    assert res.returncode == 0, res.stderr
    assert res.stdout.splitlines() == SYNCED
    assert (env.tmp / "dev" / HOST).is_dir()


def test_group_narrows_to_subtree(env: Env) -> None:
    res = env.run("--host", HOST, "--group", "grp/sub", "--porcelain")
    assert res.returncode == 0, res.stderr
    assert res.stdout.splitlines() == SUB_TREE


def test_group_naming_a_leaf_subgroup(env: Env) -> None:
    res = env.run("--host", HOST, "--group=grp/sub/deep", "--porcelain")
    assert res.returncode == 0, res.stderr
    assert res.stdout.splitlines() == ["grp/sub/deep/gamma"]


def test_empty_group_finds_nothing(env: Env) -> None:
    res = env.run("--host", HOST, "--group", "grp/empty", "--list-repos")
    assert res.returncode == 0, res.stderr
    assert "No repositories found" in res.stdout


def test_group_outside_membership_is_looked_up_directly(env: Env) -> None:
    """A group the user is not a member of is still reachable by path."""
    groups = Path(env.env["GLAB_DATA"]) / "groups.1.json"
    groups.write_text("[]")
    res = env.run("--host", HOST, "--group", "grp/sub", "--porcelain")
    assert res.returncode == 0, res.stderr
    assert res.stdout.splitlines() == SUB_TREE


def test_unknown_group_fails(env: Env) -> None:
    res = env.run("--host", HOST, "--group", "nope", "--list-repos")
    assert res.returncode != 0
    assert "nope" in res.stdout + res.stderr


# ---- sync -------------------------------------------------------------------

def test_dry_run_creates_no_checkout(env: Env) -> None:
    res = env.sync("--dry-run")
    assert res.returncode == 0, res.stdout + res.stderr
    created = {p.name for p in env.root.iterdir()} - {".gh_archived_repos"}
    assert created == set(), created


def test_first_run_clones_nested_layout_then_up_to_date(env: Env) -> None:
    res = env.sync()
    assert res.returncode == 0, res.stdout + res.stderr
    for path in SYNCED:
        checkout = env.checkout(path)
        assert (checkout / ".git").is_dir(), f"{path} not cloned at {checkout}"
        assert (env.root / path / "worktree").is_dir()
        assert env.git("branch", "--show-current", cwd=checkout) == "main"
        assert (checkout / "README").read_text() == f"{path}\n"
    assert not (env.root / "grp" / "old").exists()
    assert not (env.root / "grp" / "other").exists()
    assert "Up-to-date: 0 | Updated: 6 | Failed: 0" in res.stdout
    assert "GitLab Sync Summary" in res.stdout

    before = {p: env.head(p) for p in SYNCED}
    res = env.sync()
    assert res.returncode == 0, res.stdout + res.stderr
    assert {p: env.head(p) for p in SYNCED} == before
    assert "Up-to-date: 6 | Updated: 0 | Failed: 0" in res.stdout
    assert not env.gh_log.exists()


def test_uncommitted_change_is_fetched_not_pulled(env: Env) -> None:
    assert env.sync().returncode == 0
    checkout = env.checkout("grp/alpha")
    head = env.head("grp/alpha")
    new = env.advance("grp/alpha")
    (checkout / "README").write_text("local edit\n")
    res = env.sync()
    assert res.returncode == 0, res.stdout + res.stderr
    assert env.head("grp/alpha") == head
    assert env.git("rev-parse", "origin/main", cwd=checkout) == new
    assert (checkout / "README").read_text() == "local edit\n"
    assert _section(res.stdout, "Uncommitted changes") == ["grp/alpha"]


def test_behind_checkout_fast_forwards(env: Env) -> None:
    assert env.sync().returncode == 0
    new = env.advance("grp/sub/deep/gamma")
    res = env.sync()
    assert res.returncode == 0, res.stdout + res.stderr
    assert env.head("grp/sub/deep/gamma") == new
    assert _section(res.stdout, "Updated") == ["grp/sub/deep/gamma"]


# ---- --from-list ------------------------------------------------------------

def test_from_list_reproduces_layout_without_api(env: Env) -> None:
    listing = env.run("--host", HOST, "--porcelain")
    assert listing.returncode == 0, listing.stderr
    assert env.sync().returncode == 0

    saved = env.tmp / "list.txt"
    lines = listing.stdout.splitlines()
    saved.write_text(
        "# saved from another network\n\n"
        + "\n".join(f"  {ln}\r" if i == 0 else ln for i, ln in enumerate(lines))
        + "\n\n# trailing comment\n" + lines[0] + "\n"
    )
    env.glab_log.unlink()
    root2 = env.tmp / "root2"
    res = env.run("--host", HOST, "--from-list", str(saved), "--quiet",
                  env={"GLAB_API_FAIL": "1"}, root=root2)
    assert res.returncode == 0, res.stdout + res.stderr
    assert _checkouts(root2) == _checkouts(env.root) == set(SYNCED)
    assert env.api_calls() == []


def test_from_list_works_without_glab(env: Env, tmp_path: Path) -> None:
    saved = tmp_path / "list.txt"
    saved.write_text("grp/alpha\nother/ns/shared\n")
    res = env.run("--host", HOST, "--from-list", str(saved), "--quiet",
                  env={"PATH": _path_without_glab(tmp_path)}, root=env.root)
    assert res.returncode == 0, res.stdout + res.stderr
    assert _checkouts(env.root) == {"grp/alpha", "other/ns/shared"}


def test_from_list_respects_limit(env: Env, tmp_path: Path) -> None:
    saved = tmp_path / "list.txt"
    saved.write_text("\n".join(SYNCED) + "\n")
    res = env.run("--host", HOST, "--from-list", str(saved), "--limit", "2",
                  "--quiet", root=env.root)
    assert res.returncode == 0, res.stdout + res.stderr
    assert _checkouts(env.root) == set(SYNCED[:2])


def test_from_list_with_scheme_host(env: Env, tmp_path: Path) -> None:
    saved = tmp_path / "list.txt"
    saved.write_text("grp/sub/beta\n")
    res = env.run("--host", f"https://{HOST}", "--from-list", str(saved),
                  "--quiet", root=env.root)
    assert res.returncode == 0, res.stdout + res.stderr
    assert _checkouts(env.root) == {"grp/sub/beta"}


def test_from_list_missing_file_fails(env: Env) -> None:
    res = env.run("--host", HOST, "--from-list", str(env.tmp / "nope.txt"),
                  root=env.root)
    assert res.returncode != 0
    assert "nope.txt" in res.stdout + res.stderr


# ---- fail fast --------------------------------------------------------------

def _path_without_glab(tmp_path: Path) -> str:
    """A PATH holding only the tools the script needs, and no glab."""
    bin_dir = tmp_path / "noglab"
    bin_dir.mkdir(exist_ok=True)
    for tool in ("bash", "git", "jq", "mktemp", "sort", "wc", "tr", "basename",
                 "dirname", "cat", "rm", "rmdir", "mkdir", "sed", "grep", "awk",
                 "cut", "head", "env", "timeout", "gtimeout", "ssh"):
        found = shutil.which(tool)
        if found and not (bin_dir / tool).exists():
            (bin_dir / tool).symlink_to(found)
    gh = bin_dir / "gh"
    if not gh.exists():
        gh.write_text(GH_STUB)
        gh.chmod(0o755)
    return str(bin_dir)


def test_missing_glab_fails_fast(env: Env, tmp_path: Path) -> None:
    res = env.run("--host", HOST, "--list-repos",
                  env={"PATH": _path_without_glab(tmp_path)})
    assert res.returncode != 0
    out = res.stdout + res.stderr
    assert HOST in out and "glab" in out


@pytest.mark.parametrize("host,reason", [
    ("gl.unreachable", "not authenticated"),
    ("gl.down", "unreachable"),
])
def test_failed_host_check_fails_fast(env: Env, host: str, reason: str) -> None:
    res = env.run("--host", host, "--list-repos")
    assert res.returncode != 0
    out = res.stdout + res.stderr
    assert host in out and reason in out, out
    assert len([ln for ln in out.splitlines() if ln.strip()]) == 1, out
    assert env.api_calls() == []
    auth_calls = [ln for ln in env.glab_log.read_text().splitlines()
                  if ln.startswith("glab auth")]
    assert len(auth_calls) == 1
    assert not (env.tmp / "dev" / host).exists()


def test_failed_membership_call_fails(env: Env) -> None:
    res = env.run("--host", HOST, "--list-repos", env={"GLAB_API_FAIL": "1"})
    assert res.returncode != 0
    assert HOST in res.stdout + res.stderr
    assert len(env.api_calls()) == 1


# ---- flag validation --------------------------------------------------------

@pytest.mark.parametrize("args,needle", [
    (["--provider", "gitlab", "--org", "x"], "--org"),
    (["--provider", "gitlab", "--list-teams"], "--list-teams"),
    (["--provider", "github", "--from-list", "f"], "--from-list"),
    (["--provider", "github", "--group", "g"], "--group"),
    (["--provider", "github", "--host", "h"], "--host"),
    (["--provider", "gitlab", "--group", "g", "--from-list", "f"], "--group"),
    (["--provider", "bitbucket"], "bitbucket"),
])
def test_rejected_flag_combinations(env: Env, args: list[str], needle: str) -> None:
    res = subprocess.run(
        ["bash", str(GHSYNC), *args, "--list-repos"],
        capture_output=True, text=True, env=env.env, cwd=env.tmp, timeout=60,
    )
    assert res.returncode != 0
    assert needle in res.stdout + res.stderr
    assert env.api_calls() == []
    assert not env.gh_log.exists()


def test_help_documents_gitlab_flags(env: Env) -> None:
    res = subprocess.run(["bash", str(GHSYNC), "--help"], capture_output=True,
                         text=True, env=env.env, timeout=60)
    assert res.returncode == 0
    for flag in ("--provider", "--host", "--group", "--from-list", "--porcelain"):
        assert flag in res.stdout, flag
    # The whole header block prints, ending at its closing rule.
    assert res.stdout.rstrip().splitlines()[-1].startswith("# ====")
    assert "#\n# GitHub Enterprise" in res.stdout
