"""Behavioural contract for `ghsync.sh --porcelain`.

The `--porcelain` flag exists so downstream tooling (e.g. /ghreport) can parse
the discovered repo list off stdout. The contract that matters: with
`--porcelain`, stdout carries ONLY repo names (one per line, no prefix, no
progress chatter); every informational line goes to stderr. Without it, the
existing `--list-repos` output (header + `  - name` lines) is unchanged.

This runs the real script offline: `gh` is stubbed on PATH to return canned
discovery data, and the script's own `jq` filtering does the rest (jq ships on
the CI runner). No network, no auth, deterministic.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
GHSYNC = REPO / "skills" / "ghsync" / "scripts" / "ghsync.sh"

# Stub `gh` covering exactly the calls the discovery path makes: auth check,
# account-type probe (Organization), no team memberships, and an org repo
# list with an archived repo and an empty (no default branch) repo that must
# both be filtered out.
GH_STUB = """\
#!/usr/bin/env bash
case "$1" in
  auth) exit 0 ;;
  api)
    case "$2" in
      users/*) echo "Organization" ;;
      user/teams) echo "" ;;
      *) echo "" ;;
    esac ;;
  repo)
    cat <<'JSON'
[{"name":"zebra","isArchived":false,"defaultBranchRef":{"name":"main"}},
 {"name":"alpha","isArchived":false,"defaultBranchRef":{"name":"main"}},
 {"name":"arch-one","isArchived":true,"defaultBranchRef":{"name":"main"}},
 {"name":"empty-one","isArchived":false,"defaultBranchRef":null}]
JSON
    ;;
  *) exit 0 ;;
esac
"""

# After dedupe/sort and dropping the archived + empty repos.
EXPECTED_REPOS = ["alpha", "zebra"]

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None or shutil.which("jq") is None,
    reason="requires bash and jq on PATH",
)


def _run(tmp_path, *args):
    stub_dir = tmp_path / "bin"
    stub_dir.mkdir(exist_ok=True)
    gh = stub_dir / "gh"
    gh.write_text(GH_STUB)
    gh.chmod(0o755)
    root = tmp_path / "root"
    root.mkdir(exist_ok=True)
    env = {"PATH": f"{stub_dir}:{shutil.os.environ['PATH']}"}
    return subprocess.run(
        ["bash", str(GHSYNC), "--org", "testorg", "--root", str(root), *args],
        capture_output=True, text=True, env=env, timeout=60,
    )


def test_porcelain_stdout_is_only_repo_names(tmp_path):
    res = _run(tmp_path, "--porcelain")
    assert res.returncode == 0, res.stderr
    lines = res.stdout.splitlines()
    assert lines == EXPECTED_REPOS, f"stdout not clean repo list: {res.stdout!r}"


def test_porcelain_routes_chatter_to_stderr(tmp_path):
    res = _run(tmp_path, "--porcelain")
    # Progress/status lines must not contaminate stdout...
    assert "Org:" not in res.stdout
    assert "Fetching" not in res.stdout
    assert "  - " not in res.stdout
    # ...they belong on stderr.
    assert "Org:" in res.stderr


def test_list_repos_without_porcelain_unchanged(tmp_path):
    res = _run(tmp_path, "--list-repos")
    assert res.returncode == 0, res.stderr
    # Human format: a header plus `  - name` bullet lines, chatter on stdout.
    assert "Repositories accessible to you:" in res.stdout
    assert "  - alpha" in res.stdout
    assert "  - zebra" in res.stdout


def test_porcelain_documented_in_help(tmp_path):
    res = _run(tmp_path, "--help")
    assert res.returncode == 0
    assert "--porcelain" in res.stdout
