"""Behavioural contract for ghsync's clone and sync path.

Runs the real `ghsync.sh` offline. `gh` is stubbed on PATH, and
`gh repo clone` runs a real `git clone https://github.com/<org>/<path>.git`,
which a throwaway global git config (`GIT_CONFIG_GLOBAL`) rewrites with
`url.<base>.insteadOf` onto local bare repos. No network, no auth.

Repository paths cover every shape the layout code handles:

- `alpha`: a flat name.
- `dotted.name-x`: a name with dots and dashes.
- `ns/nested`: a nested path. GitHub never returns one, but the stub's repo
  list can, so the provider-agnostic layout is exercised through the GitHub
  provider with no test-only hook.
- `ns/alpha`: shares a basename with `alpha`. Dedupe keyed on the bare name
  would collapse the two; keyed on the path they stay distinct.

Plus three repos that must never be cloned: `arch-one` (archived),
`blocked` and `ns/blocked` (blacklisted by path via GHSYNC_BLACKLIST).
"""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
GHSYNC = REPO / "skills" / "ghsync" / "scripts" / "ghsync.sh"
ORG = "testorg"

SYNCED = ["alpha", "dotted.name-x", "ns/alpha", "ns/nested"]
NEVER_CLONED = ["arch-one", "blocked", "ns/blocked"]
BLACKLIST = "blocked ns/blocked"

# Stub covering the calls the discovery and clone paths make. `gh` ignores
# --jq here, so `api users/*` prints the already-filtered value.
GH_STUB = """\
#!/usr/bin/env bash
echo "gh $*" >> "$GH_LOG"
case "$1" in
  auth) exit 0 ;;
  api)
    case "$2" in
      users/*) echo "Organization" ;;
      *) echo "" ;;
    esac ;;
  repo)
    case "$2" in
      list)
        cat <<'JSON'
[{"name":"alpha","isArchived":false,"defaultBranchRef":{"name":"main"}},
 {"name":"dotted.name-x","isArchived":false,"defaultBranchRef":{"name":"main"}},
 {"name":"ns/nested","isArchived":false,"defaultBranchRef":{"name":"main"}},
 {"name":"ns/alpha","isArchived":false,"defaultBranchRef":{"name":"main"}},
 {"name":"alpha","isArchived":false,"defaultBranchRef":{"name":"main"}},
 {"name":"blocked","isArchived":false,"defaultBranchRef":{"name":"main"}},
 {"name":"ns/blocked","isArchived":false,"defaultBranchRef":{"name":"main"}},
 {"name":"arch-one","isArchived":true,"defaultBranchRef":{"name":"main"}}]
JSON
        ;;
      clone) exec git clone -q "https://github.com/$3.git" "$4" ;;
      view) echo "main" ;;
    esac ;;
esac
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
        stub_dir = tmp_path / "bin"
        stub_dir.mkdir()
        gh = stub_dir / "gh"
        gh.write_text(GH_STUB)
        gh.chmod(0o755)
        self.gh_log = tmp_path / "gh.log"
        gitconfig = tmp_path / "gitconfig"
        gitconfig.write_text(
            "[user]\n\tname = Test\n\temail = test@example.com\n"
            "[init]\n\tdefaultBranch = main\n"
            "[commit]\n\tgpgsign = false\n"
            f'[url "file://{self.bare}/"]\n\tinsteadOf = https://github.com/\n'
        )
        self.env = {
            "PATH": f"{stub_dir}:{os.environ['PATH']}",
            "HOME": str(tmp_path),
            "GIT_CONFIG_GLOBAL": str(gitconfig),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GH_LOG": str(self.gh_log),
            "GHSYNC_BLACKLIST": BLACKLIST,
        }
        for path in SYNCED + NEVER_CLONED:
            self._make_bare(path)

    def git(self, *args: str, cwd: Path) -> str:
        res = subprocess.run(
            ["git", *args], cwd=cwd, env=self.env,
            capture_output=True, text=True, check=True,
        )
        return res.stdout.strip()

    def _make_bare(self, path: str) -> None:
        bare = self.bare / ORG / f"{path}.git"
        bare.parent.mkdir(parents=True, exist_ok=True)
        self.git("init", "-q", "--bare", "-b", "main", str(bare), cwd=self.tmp)
        work = self.tmp / "seed" / path
        self.git("clone", "-q", str(bare), str(work), cwd=self.tmp)
        (work / "README").write_text(f"{path}\n")
        self.git("add", "README", cwd=work)
        self.git("commit", "-q", "-m", "init", cwd=work)
        self.git("push", "-q", "origin", "main", cwd=work)

    def advance(self, path: str) -> str:
        """Push a new commit to the bare repo's main; return its SHA."""
        work = self.tmp / "seed" / path
        with (work / "CHANGES").open("a") as fh:
            fh.write("more\n")
        self.git("add", "CHANGES", cwd=work)
        self.git("commit", "-q", "-m", "advance", cwd=work)
        self.git("push", "-q", "origin", "main", cwd=work)
        return self.git("rev-parse", "HEAD", cwd=work)

    def checkout(self, path: str) -> Path:
        return self.root / path / f"{path.rsplit('/', 1)[-1]}-main"

    def head(self, path: str) -> str:
        return self.git("rev-parse", "HEAD", cwd=self.checkout(path))

    def run(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(GHSYNC), "--org", ORG, "--root", str(self.root),
             "--quiet", *args],
            capture_output=True, text=True, env=self.env, timeout=120,
        )


def _section(output: str, heading: str) -> list[str]:
    """Lines under a summary heading such as 'Uncommitted changes (1):'."""
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


@pytest.fixture
def env(tmp_path: Path) -> Env:
    return Env(tmp_path)


def test_porcelain_lists_paths_deduped_and_filtered(env: Env) -> None:
    res = env.run("--porcelain")
    assert res.returncode == 0, res.stderr
    assert res.stdout.splitlines() == SYNCED
    assert (env.root / ".gh_archived_repos").read_text().split() == ["arch-one"]


def test_dry_run_creates_no_checkout(env: Env) -> None:
    res = env.run("--dry-run")
    assert res.returncode == 0, res.stderr
    created = {p.name for p in env.root.iterdir()} - {".gh_archived_repos"}
    assert created == set(), created
    assert "gh repo clone" not in env.gh_log.read_text()


def test_first_run_clones_into_path_layout(env: Env) -> None:
    res = env.run()
    assert res.returncode == 0, res.stdout + res.stderr
    for path in SYNCED:
        checkout = env.checkout(path)
        assert (checkout / ".git").is_dir(), f"{path} not cloned at {checkout}"
        assert (env.root / path / "worktree").is_dir()
        assert env.git("branch", "--show-current", cwd=checkout) == "main"
        assert (checkout / "README").read_text() == f"{path}\n"
    for path in NEVER_CLONED:
        assert not (env.root / path).exists(), f"{path} should not be cloned"
    assert "Up-to-date: 0 | Updated: 4 | Failed: 0" in res.stdout
    assert sorted(_section(res.stdout, "Updated")) == SYNCED
    clones = [ln for ln in env.gh_log.read_text().splitlines()
              if ln.startswith("gh repo clone")]
    assert sorted(ln.split()[3] for ln in clones) == [f"{ORG}/{p}" for p in SYNCED]


def test_second_run_is_up_to_date(env: Env) -> None:
    assert env.run().returncode == 0
    before = {p: env.head(p) for p in SYNCED}
    res = env.run()
    assert res.returncode == 0, res.stdout + res.stderr
    assert {p: env.head(p) for p in SYNCED} == before
    assert "Up-to-date: 4 | Updated: 0 | Failed: 0" in res.stdout
    assert _section(res.stdout, "Updated") == []


def test_behind_uncommitted_and_branch_states(env: Env) -> None:
    """One run, three states: behind, uncommitted edit, non-default branch."""
    assert env.run().returncode == 0

    # Behind: the flat repo fast-forwards.
    alpha_new = env.advance("alpha")

    # Uncommitted: an unstaged edit to a tracked file in the nested checkout.
    # The checkout is fetched (origin/main advances) but not pulled.
    nested = env.checkout("ns/nested")
    nested_head = env.head("ns/nested")
    nested_new = env.advance("ns/nested")
    (nested / "README").write_text("local edit\n")

    # Non-default branch: the dotted name is on a feature branch.
    dotted = env.checkout("dotted.name-x")
    env.git("checkout", "-q", "-b", "feature", cwd=dotted)

    res = env.run()
    assert res.returncode == 0, res.stdout + res.stderr

    assert env.head("alpha") == alpha_new
    assert _section(res.stdout, "Updated") == ["alpha"]

    assert env.head("ns/nested") == nested_head
    assert env.git("rev-parse", "origin/main", cwd=nested) == nested_new
    assert (nested / "README").read_text() == "local edit\n"
    assert _section(res.stdout, "Uncommitted changes") == ["ns/nested"]

    assert env.git("branch", "--show-current", cwd=dotted) == "feature"
    assert _section(res.stdout, "Not on default branch") == ["dotted.name-x (feature)"]

    # The basename twin of alpha is untouched and still current.
    assert "Up-to-date: 1 | Updated: 1 | Failed: 0 | Uncommitted: 1 | Branch issues: 1" in res.stdout


def test_staged_change_is_also_uncommitted(env: Env) -> None:
    assert env.run().returncode == 0
    checkout = env.checkout("ns/alpha")
    head = env.head("ns/alpha")
    env.advance("ns/alpha")
    (checkout / "README").write_text("staged edit\n")
    env.git("add", "README", cwd=checkout)
    res = env.run()
    assert res.returncode == 0, res.stdout + res.stderr
    assert env.head("ns/alpha") == head
    assert _section(res.stdout, "Uncommitted changes") == ["ns/alpha"]


def test_failed_clone_removes_only_its_own_directories(env: Env) -> None:
    """A failed nested clone leaves its sibling under the shared parent."""
    shutil.rmtree(env.bare / ORG / "ns" / "nested.git")
    res = env.run()
    assert res.returncode == 0, res.stdout + res.stderr
    assert _section(res.stdout, "Failed") == ["ns/nested"]
    assert not (env.root / "ns" / "nested").exists()
    assert (env.checkout("ns/alpha") / ".git").is_dir()
