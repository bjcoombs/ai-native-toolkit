"""Tests for the ghreport skill.

The assembler (headline counts + table + needs-attention rendering) is the
testable core, and it is exercised offline through the script's own
`--render-dir` mode: feed a directory of hand-written per-repo JSON fragments
and assert the rendered output. No network, no `gh`, deterministic. Requires
`bash` and `jq` (jq does the rendering; it ships on the CI runner).
"""
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
GHREPORT = REPO / "skills" / "ghreport" / "scripts" / "ghreport.sh"

# One fragment per signal the assembler must distinguish.
FRAGMENTS = {
    "alpha": {  # clean
        "repo": "alpha", "unreachable": False, "default_branch": "main",
        "open_prs": 2, "draft_prs": 1, "ci": "pass", "failing_workflows": "",
        "dependabot": 0, "code_scanning": 0, "secret_scanning": 0,
        "protection": "protected",
    },
    "bravo": {  # failing CI
        "repo": "bravo", "unreachable": False, "default_branch": "main",
        "open_prs": 0, "draft_prs": 0, "ci": "fail", "failing_workflows": "Tests, Lint",
        "dependabot": 0, "code_scanning": 0, "secret_scanning": 0,
        "protection": "protected",
    },
    "charlie": {  # open alerts + unprotected
        "repo": "charlie", "unreachable": False, "default_branch": "main",
        "open_prs": 1, "draft_prs": 0, "ci": "pass", "failing_workflows": "",
        "dependabot": 5, "code_scanning": 2, "secret_scanning": 0,
        "protection": "unprotected",
    },
    "delta": {  # no access everywhere - must NOT count as alerts
        "repo": "delta", "unreachable": False, "default_branch": "main",
        "open_prs": 0, "draft_prs": 0, "ci": "none", "failing_workflows": "",
        "dependabot": "no-access", "code_scanning": "no-access",
        "secret_scanning": "no-access", "protection": "unknown",
    },
    "echo": {  # unreachable
        "repo": "echo", "unreachable": True, "default_branch": "?",
        "open_prs": 0, "draft_prs": 0, "ci": "none", "failing_workflows": "",
        "dependabot": "no-access", "code_scanning": "no-access",
        "secret_scanning": "no-access", "protection": "unknown",
    },
}

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None or shutil.which("jq") is None,
    reason="requires bash and jq on PATH",
)


@pytest.fixture
def fragments_dir(tmp_path):
    d = tmp_path / "frags"
    d.mkdir()
    for name, frag in FRAGMENTS.items():
        (d / f"{name}.json").write_text(json.dumps(frag))
    return d


def _render(fragments_dir, root, *extra):
    return subprocess.run(
        ["bash", str(GHREPORT), "--render-dir", str(fragments_dir),
         "--org", "demo", "--root", str(root), *extra],
        capture_output=True, text=True, timeout=60,
    )


def test_headline_counts(fragments_dir, tmp_path):
    res = _render(fragments_dir, tmp_path, "--no-file")
    assert res.returncode == 0, res.stderr
    out = res.stdout
    assert "1 with failing CI" in out
    assert "1 with open security alerts" in out          # charlie only
    assert "1 with an unprotected default branch" in out  # charlie
    assert "1 could not be assessed" in out               # echo
    assert "3 open PR(s) total" in out                    # alpha 2 + charlie 1


def test_no_access_not_counted_as_alert(fragments_dir, tmp_path):
    """delta is no-access on every endpoint; it must not appear as an alert."""
    res = _render(fragments_dir, tmp_path, "--no-file")
    # delta is clean of real signals, so it must not be in Needs attention.
    attention = res.stdout.split("Needs attention")[-1]
    assert "delta" not in attention
    # And its alert cells render as n/a, never as 0.
    assert "| delta | 0 | none | n/a | n/a | n/a | unknown |" in res.stdout


def test_failing_and_alert_repos_in_attention(fragments_dir, tmp_path):
    res = _render(fragments_dir, tmp_path, "--no-file")
    attention = res.stdout.split("Needs attention")[-1]
    assert "bravo" in attention and "Tests, Lint" in attention
    assert "charlie" in attention and "5 Dependabot" in attention
    assert "echo" in attention and "could not assess" in attention


def test_table_rows_have_six_columns(fragments_dir, tmp_path):
    res = _render(fragments_dir, tmp_path, "--no-file")
    data_rows = [ln for ln in res.stdout.splitlines()
                 if ln.startswith("| ") and "Repo" not in ln and "---" not in ln]
    assert len(data_rows) == 5
    for row in data_rows:
        # 7 cells (Repo + 6 signal columns) bounded by pipes => 8 pipes.
        assert row.count("|") == 8, row


def test_markdown_file_written(fragments_dir, tmp_path):
    res = _render(fragments_dir, tmp_path)  # no --no-file
    assert res.returncode == 0, res.stderr
    files = list(tmp_path.glob("org-state-demo-*.md"))
    assert len(files) == 1, f"expected one report file, got {files}"
    body = files[0].read_text()
    assert "# Org state: demo" in body
    assert "## Needs attention" in body
    assert "## Legend" in body
    assert "charlie" in body


def test_no_file_writes_nothing(fragments_dir, tmp_path):
    _render(fragments_dir, tmp_path, "--no-file")
    assert list(tmp_path.glob("org-state-*.md")) == []


def test_help_exits_zero():
    res = subprocess.run(["bash", str(GHREPORT), "--help"],
                         capture_output=True, text=True, timeout=30)
    assert res.returncode == 0
    assert "--render-dir" in res.stdout
    assert "ghreport.sh" in res.stdout


def test_unknown_flag_exits_nonzero():
    res = subprocess.run(["bash", str(GHREPORT), "--bogus"],
                         capture_output=True, text=True, timeout=30)
    assert res.returncode != 0


def test_missing_ghsync_exits_nonzero(tmp_path):
    """Discovery mode with no resolvable ghsync.sh must fail clearly, not hang."""
    isolated = tmp_path / "isolated"
    isolated.mkdir()
    shutil.copy(GHREPORT, isolated / "ghreport.sh")
    env = dict(os.environ)
    env["HOME"] = str(tmp_path / "fakehome")  # no ~/.claude/skills/ghsync there
    env.pop("CLAUDE_PLUGIN_ROOT", None)
    res = subprocess.run(
        ["bash", str(isolated / "ghreport.sh"), "--org", "demo", "--root", str(tmp_path)],
        capture_output=True, text=True, timeout=30, env=env,
    )
    assert res.returncode != 0
    assert "ghsync.sh not found" in res.stderr
