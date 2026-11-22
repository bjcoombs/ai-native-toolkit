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
import signal
import subprocess
import time
from pathlib import Path

import pytest
from gitbash import BASH, IS_WINDOWS

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
        (d / f"{name}.json").write_text(json.dumps(frag), newline="\n")
    return d


def _render(fragments_dir, root, *extra):
    return subprocess.run(
        [BASH, str(GHREPORT), "--render-dir", str(fragments_dir),
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
    res = subprocess.run([BASH, str(GHREPORT), "--help"],
                         capture_output=True, text=True, timeout=30)
    assert res.returncode == 0
    assert "--render-dir" in res.stdout
    assert "ghreport.sh" in res.stdout


def test_unknown_flag_exits_nonzero():
    res = subprocess.run([BASH, str(GHREPORT), "--bogus"],
                         capture_output=True, text=True, timeout=30)
    assert res.returncode != 0


# A `gh` stub covering every call the real ghsync discovery AND ghreport's
# per-repo queries make, so the full pipeline runs offline. openrepo has no
# branch protection (gh emits the real "(HTTP 404)" string); lockedrepo is
# protected. This exercises the query path the hand-written fixtures bypass -
# in particular the 404 grep that classifies "unprotected".
GH_STUB_INTEGRATION = """\
#!/usr/bin/env bash
sub=$1; shift || true
case "$sub" in
  auth) exit 0 ;;
  repo)
    cat <<'JSON'
[{"name":"openrepo","isArchived":false,"defaultBranchRef":{"name":"main"}},
 {"name":"lockedrepo","isArchived":false,"defaultBranchRef":{"name":"main"}}]
JSON
    exit 0 ;;
esac
path=$1
case "$path" in
  users/*) echo "Organization" ;;
  user/teams) echo "" ;;
  *pulls*) echo '[]' ;;
  *actions/runs*) echo '{"workflow_runs":[]}' ;;
  *dependabot/alerts*) echo 0 ;;
  *code-scanning/alerts*) echo 0 ;;
  *secret-scanning/alerts*) echo 0 ;;
  */openrepo/branches/*/protection) echo "gh: Branch not protected (HTTP 404)" >&2; exit 1 ;;
  */lockedrepo/branches/*/protection) echo '{"required_pull_request_reviews":{}}' ;;
  repos/testorg/*) echo '{"default_branch":"main"}' ;;
  *) echo "" ;;
esac
"""


def test_protection_detection_end_to_end(tmp_path):
    """Full pipeline (real ghsync + stubbed gh): the unprotected signal must fire.

    Guards the MUST-FIX regression where the 404 grep matched `(404)` instead
    of the `(HTTP 404)` gh actually emits, so `unprotected` was dead code.
    """
    stub_dir = tmp_path / "bin"
    stub_dir.mkdir()
    gh = stub_dir / "gh"
    gh.write_text(GH_STUB_INTEGRATION, newline="\n")
    gh.chmod(0o755)
    root = tmp_path / "root"
    root.mkdir()
    env = dict(os.environ)
    env["PATH"] = f"{stub_dir}{os.pathsep}{env['PATH']}"
    res = subprocess.run(
        [BASH, str(GHREPORT), "--org", "testorg", "--root", str(root),
         "--quiet", "--no-file"],
        capture_output=True, text=True, timeout=90, env=env,
    )
    assert res.returncode == 0, res.stderr
    assert "1 with an unprotected default branch" in res.stdout
    assert "| openrepo | 0 | none | 0 | 0 | 0 | unprotected |" in res.stdout
    assert "| lockedrepo | 0 | none | 0 | 0 | 0 | protected |" in res.stdout
    attention = res.stdout.split("Needs attention")[-1]
    assert "openrepo" in attention and "unprotected" in attention
    assert "lockedrepo" not in attention


def test_limit_truncates_before_querying(tmp_path):
    """--limit must actually cap the repos queried, not silently no-op."""
    stub_dir = tmp_path / "bin"
    stub_dir.mkdir()
    gh = stub_dir / "gh"
    gh.write_text(GH_STUB_INTEGRATION, newline="\n")
    gh.chmod(0o755)
    root = tmp_path / "root"
    root.mkdir()
    env = dict(os.environ)
    env["PATH"] = f"{stub_dir}{os.pathsep}{env['PATH']}"
    res = subprocess.run(
        [BASH, str(GHREPORT), "--org", "testorg", "--root", str(root),
         "--quiet", "--no-file", "--limit", "1"],
        capture_output=True, text=True, timeout=90, env=env,
    )
    assert res.returncode == 0, res.stderr
    assert "Limiting to the first 1 of 2" in res.stderr
    data_rows = [ln for ln in res.stdout.splitlines()
                 if ln.startswith("| ") and "Repo" not in ln and "---" not in ln]
    assert len(data_rows) == 1, data_rows


def test_missing_ghsync_exits_nonzero(tmp_path):
    """Discovery mode with no resolvable ghsync.sh must fail clearly, not hang."""
    isolated = tmp_path / "isolated"
    isolated.mkdir()
    shutil.copy(GHREPORT, isolated / "ghreport.sh")
    env = dict(os.environ)
    env["HOME"] = str(tmp_path / "fakehome")  # no ~/.claude/skills/ghsync there
    env.pop("CLAUDE_PLUGIN_ROOT", None)
    res = subprocess.run(
        [BASH, str(isolated / "ghreport.sh"), "--org", "demo", "--root", str(tmp_path)],
        capture_output=True, text=True, timeout=30, env=env,
    )
    assert res.returncode != 0
    assert "ghsync.sh not found" in res.stderr


# ---- org settings ------------------------------------------------------------
# A `gh` stub for the full pipeline whose `orgs/...` answers come from files:
# $GH_DATA/<path with / as __>.json is served, <key>.err holds an HTTP status
# to fail with (printed the way gh prints it), and a missing file is a 404.
GH_STUB_SETTINGS = r"""#!/usr/bin/env bash
echo "gh $*" >> "$GH_LOG"
sub=$1; shift || true
case "$sub" in
  auth) exit 0 ;;
  repo)
    echo '[{"name":"openrepo","isArchived":false,"defaultBranchRef":{"name":"main"}}]'
    exit 0 ;;
esac
path=$1
case "$path" in
  users/*) echo "${GH_USER_TYPE:-Organization}" ;;
  user/teams) echo "" ;;
  orgs/*)
    key=${path%%\?*}
    key=${key//\//__}
    if [ -f "$GH_DATA/$key.err" ]; then
      code=$(cat "$GH_DATA/$key.err")
      echo "gh: Request failed (HTTP $code)" >&2
      exit 1
    fi
    [ -f "$GH_DATA/$key.json" ] || { echo "gh: Not Found (HTTP 404)" >&2; exit 1; }
    cat "$GH_DATA/$key.json" ;;
  *pulls*) echo '[]' ;;
  *actions/runs*) echo '{"workflow_runs":[]}' ;;
  *alerts*) echo 0 ;;
  */branches/*/protection) echo "gh: Branch not protected (HTTP 404)" >&2; exit 1 ;;
  repos/testorg/*) echo '{"default_branch":"main"}' ;;
  *) echo "" ;;
esac
"""

ORG_SETTINGS = ("two_factor", "push_rules", "default_branch_protection", "rulesets")


def _run_with_org(tmp_path, responses, *extra, path_first=None):
    stub_dir = tmp_path / "bin"
    stub_dir.mkdir()
    gh = stub_dir / "gh"
    gh.write_text(GH_STUB_SETTINGS, newline="\n")
    gh.chmod(0o755)
    data = tmp_path / "gh-data"
    data.mkdir()
    for path, body in responses.items():
        key = path.replace("/", "__")
        if isinstance(body, int):
            (data / f"{key}.err").write_text(str(body), newline="\n")
        elif isinstance(body, str):  # a raw body, served as-is
            (data / f"{key}.json").write_text(body, newline="\n")
        else:
            (data / f"{key}.json").write_text(json.dumps(body), newline="\n")
    root = tmp_path / "root"
    root.mkdir()
    env = dict(os.environ)
    path = f"{stub_dir}{os.pathsep}{env['PATH']}"
    if path_first is not None:
        path = f"{path_first}{os.pathsep}{path}"
    env.update(PATH=path, GH_DATA=str(data),
               GH_LOG=str(tmp_path / "gh.log"))
    return subprocess.run(
        [BASH, str(GHREPORT), "--org", "testorg", "--root", str(root), "--quiet", *extra],
        capture_output=True, text=True, timeout=90, env=env,
    ), root


def _org_settings(out, scope="testorg"):
    lines = out.split("Org settings")[1].splitlines()
    start = lines.index(f"- {scope}")
    found = {}
    for ln in lines[start + 1:]:
        if not ln.startswith("  - "):
            break
        key, value = ln[4:].split(": ", 1)
        found[key] = value
    return found


def test_org_settings_non_owner_reads_unknown(tmp_path):
    """A non-owner sees the org without its 2FA field and cannot list
    rulesets: every setting is unknown with a reason, none is off."""
    res, root = _run_with_org(tmp_path, {
        "orgs/testorg": {"login": "testorg"},
        "orgs/testorg/rulesets": 403,
    })
    assert res.returncode == 0, res.stderr
    settings = _org_settings(res.stdout)
    assert set(settings) == set(ORG_SETTINGS)
    assert settings["two_factor"] == "unknown (no access: readable by org owners only)"
    for name in ("push_rules", "default_branch_protection", "rulesets"):
        assert settings[name].startswith("unknown (") and "HTTP 403" in settings[name], name
    assert "1 org(s): 0 setting(s) off, 4 unknown" in res.stdout
    # The per-repo output is unchanged.
    assert "1 repo(s): 0 with failing CI" in res.stdout
    assert "| openrepo | 0 | none | 0 | 0 | 0 | unprotected |" in res.stdout
    body = next(root.glob("org-state-testorg-*.md")).read_text()
    assert "## Org settings" in body
    assert "1 org(s): 0 setting(s) off, 4 unknown" in body


def test_org_settings_unreadable_org(tmp_path):
    res, _ = _run_with_org(tmp_path, {}, "--no-file")
    settings = _org_settings(res.stdout)
    assert all(v.startswith("unknown (") and "HTTP 404" in v for v in settings.values()), settings
    assert "0 setting(s) off" in res.stdout


def test_org_settings_owner(tmp_path):
    rulesets = [
        {"id": 1, "name": "main-guard", "target": "branch", "enforcement": "active"},
        {"id": 2, "name": "no-big-files", "target": "push", "enforcement": "active"},
        {"id": 3, "name": "trial", "target": "branch", "enforcement": "evaluate"},
        {"id": 4, "name": "some-repos", "target": "branch", "enforcement": "active"},
    ]
    res, _ = _run_with_org(tmp_path, {
        "orgs/testorg": {"login": "testorg", "two_factor_requirement_enabled": True},
        "orgs/testorg/rulesets": rulesets,
        "orgs/testorg/rulesets/1": {**rulesets[0], "conditions": {
            "ref_name": {"include": ["~DEFAULT_BRANCH"], "exclude": []},
            "repository_name": {"include": ["~ALL"], "exclude": []}}},
        "orgs/testorg/rulesets/4": {**rulesets[3], "conditions": {
            "ref_name": {"include": ["~DEFAULT_BRANCH"], "exclude": []},
            "repository_name": {"include": ["api-*"], "exclude": []}}},
    }, "--no-file")
    assert res.returncode == 0, res.stderr
    assert _org_settings(res.stdout) == {
        "two_factor": "on",
        "push_rules": "no-big-files (active)",
        "default_branch_protection": "ruleset: main-guard",
        "rulesets": "4: main-guard (active), no-big-files (active), trial (evaluate), some-repos (active)",
    }
    assert "1 org(s): 0 setting(s) off, 0 unknown" in res.stdout


def test_org_settings_off(tmp_path):
    res, _ = _run_with_org(tmp_path, {
        "orgs/testorg": {"login": "testorg", "two_factor_requirement_enabled": False},
        "orgs/testorg/rulesets": [],
    }, "--no-file")
    assert set(_org_settings(res.stdout).values()) == {"off"}
    assert "1 org(s): 4 setting(s) off, 0 unknown" in res.stdout


def test_unreadable_ruleset_detail_is_unknown_not_off(tmp_path):
    res, _ = _run_with_org(tmp_path, {
        "orgs/testorg": {"login": "testorg", "two_factor_requirement_enabled": True},
        "orgs/testorg/rulesets": [{"id": 7, "name": "guard", "target": "branch", "enforcement": "active"}],
        "orgs/testorg/rulesets/7": 403,
    }, "--no-file")
    value = _org_settings(res.stdout)["default_branch_protection"]
    assert value.startswith("unknown (ruleset 7 unreadable") and "HTTP 403" in value
    assert _org_settings(res.stdout)["push_rules"] == "off"


@pytest.mark.parametrize("jq16", [False, True], ids=["installed-jq", "jq16-empty-input"])
@pytest.mark.parametrize("state", ["disabled", "evaluate"])
def test_non_active_push_ruleset_reads_off_and_names_it(tmp_path, request, jq16, state):
    """A push ruleset that is not active enforces nothing: push_rules reads
    off, counts as off in the headline, and still names the ruleset (#594)."""
    path_first = request.getfixturevalue("jq16_bin") if jq16 else None
    res, _ = _run_with_org(tmp_path, {
        "orgs/testorg": {"login": "testorg", "two_factor_requirement_enabled": True},
        "orgs/testorg/rulesets": [
            {"id": 2, "name": "no-big-files", "target": "push", "enforcement": state}],
    }, "--no-file", path_first=path_first)
    assert res.returncode == 0, res.stderr
    settings = _org_settings(res.stdout)
    assert settings["push_rules"] == f"off (not active: no-big-files ({state}))"
    assert settings["rulesets"] == f"1: no-big-files ({state})"
    # push_rules and default_branch_protection are off; two_factor is on.
    assert "1 org(s): 2 setting(s) off, 0 unknown" in res.stdout


def test_active_and_disabled_push_rulesets_are_not_off(tmp_path):
    res, _ = _run_with_org(tmp_path, {
        "orgs/testorg": {"login": "testorg", "two_factor_requirement_enabled": True},
        "orgs/testorg/rulesets": [
            {"id": 2, "name": "no-big-files", "target": "push", "enforcement": "active"},
            {"id": 3, "name": "old-push", "target": "push", "enforcement": "disabled"}],
    }, "--no-file")
    assert res.returncode == 0, res.stderr
    assert _org_settings(res.stdout)["push_rules"] == "no-big-files (active), old-push (disabled)"
    assert "1 org(s): 1 setting(s) off, 0 unknown" in res.stdout


def test_render_dir_settings_subdir_is_not_a_repo_row(fragments_dir, tmp_path):
    (fragments_dir / "settings").mkdir()
    (fragments_dir / "settings" / "org.json").write_text(json.dumps({
        "scope": "demo", "provider": "github",
        "two_factor": {"value": "unknown", "reason": "no access: readable by org owners only"},
        "push_rules": {"value": "off", "reason": None},
        "default_branch_protection": {"value": "ruleset: guard", "reason": None},
        "rulesets": {"value": "1: guard (active)", "reason": None},
    }), newline="\n")
    res = _render(fragments_dir, tmp_path, "--no-file")
    assert res.returncode == 0, res.stderr
    assert "5 repo(s)" in res.stdout
    data_rows = [ln for ln in res.stdout.splitlines()
                 if ln.startswith("| ") and "Repo" not in ln and "---" not in ln]
    assert len(data_rows) == 5
    assert "1 org(s): 1 setting(s) off, 1 unknown" in res.stdout
    assert _org_settings(res.stdout, "demo")["default_branch_protection"] == "ruleset: guard"


def test_render_dir_without_settings_has_no_settings_section(fragments_dir, tmp_path):
    res = _render(fragments_dir, tmp_path)
    assert "Org settings" not in res.stdout
    body = next(tmp_path.glob("org-state-demo-*.md")).read_text()
    assert "## Org settings" not in body


def test_personal_account_names_the_reason(tmp_path, monkeypatch):
    """A personal account has no org settings: the 404 is explained as that,
    not as missing access, and no ruleset call is made."""
    monkeypatch.setenv("GH_USER_TYPE", "User")
    res, _ = _run_with_org(tmp_path, {}, "--no-file")
    assert res.returncode == 0, res.stderr
    settings = _org_settings(res.stdout)
    assert set(settings) == set(ORG_SETTINGS)
    assert set(settings.values()) == {"unknown (personal account: no org settings)"}
    assert "rulesets" not in (tmp_path / "gh.log").read_text()


@pytest.mark.parametrize("jq16", [False, True], ids=["installed-jq", "jq16-empty-input"])
@pytest.mark.parametrize("body", ["", "\n", "not json"], ids=["empty", "blank", "non-json"])
def test_empty_settings_body_is_unknown_and_table_renders(tmp_path, request, jq16, body):
    """An org or ruleset endpoint that exits 0 with an empty or non-JSON body
    reads unknown, on jq 1.6's empty-input `-e` behaviour as well as 1.7's,
    and the repository table still renders (issue #603)."""
    path_first = request.getfixturevalue("jq16_bin") if jq16 else None
    res, _ = _run_with_org(tmp_path, {
        "orgs/testorg": body,
        "orgs/testorg/rulesets": [{"id": 7, "name": "guard", "target": "branch", "enforcement": "active"}],
        "orgs/testorg/rulesets/7": body,
    }, "--no-file", path_first=path_first)
    assert res.returncode == 0, res.stderr
    settings = _org_settings(res.stdout)
    assert settings["two_factor"] == "unknown (unreadable response)"
    assert settings["default_branch_protection"] == "unknown (ruleset 7 unreadable: unreadable response)"
    assert settings["rulesets"] == "1: guard (active)"
    assert "| openrepo | 0 | none | 0 | 0 | 0 | unprotected |" in res.stdout


def test_jq16_shim_reproduces_empty_input_exit(jq16_bin):
    """Guards the shim itself: `jq -e` over empty input exits 0 through it,
    while `jq -en input` still fails, which is what the fix relies on."""
    # Windows cannot exec a shebang script directly, so bash runs the shim there.
    jq = [BASH, str(jq16_bin / "jq")] if IS_WINDOWS else [str(jq16_bin / "jq")]
    assert subprocess.run([*jq, "-e", "type==\"object\""], input="", text=True).returncode == 0
    assert subprocess.run([*jq, "-en", "input"], input="", text=True,
                          capture_output=True).returncode != 0


# ---- capture then replay (--keep-fragments, #694) ---------------------------

def _without_report_path(out):
    """Drop the line naming the report file: capture and replay write to
    different roots, and everything else must match."""
    return "\n".join(ln for ln in out.splitlines() if not ln.startswith("Report written to:"))


def test_keep_fragments_capture_then_replay_is_identical(tmp_path):
    """A live run with --keep-fragments, replayed with --render-dir, gives the
    same terminal report and the same markdown file, settings included."""
    keep = tmp_path / "capture"
    res, root = _run_with_org(tmp_path, {
        "orgs/testorg": {"login": "testorg", "two_factor_requirement_enabled": True},
        "orgs/testorg/rulesets": [],
    }, "--keep-fragments", str(keep))
    assert res.returncode == 0, res.stderr
    assert f"Fragments kept in: {keep}" in res.stderr
    assert sorted(p.relative_to(keep).as_posix() for p in keep.rglob("*.json")) == [
        "openrepo.json", "settings/org.json"]
    assert (keep / "CAPTURE_COMPLETE").is_file()
    assert "Org settings" in res.stdout

    replay_root = tmp_path / "replay"
    replay_root.mkdir()
    rep = subprocess.run(
        [BASH, str(GHREPORT), "--render-dir", str(keep), "--org", "testorg",
         "--root", str(replay_root), "--quiet"],
        capture_output=True, check=False, text=True, timeout=60,
    )
    assert rep.returncode == 0, rep.stderr
    assert "CAPTURE_COMPLETE" not in rep.stderr, "a finished capture must not warn"
    assert _without_report_path(rep.stdout) == _without_report_path(res.stdout)
    live_file = next(root.glob("org-state-testorg-*.md")).read_text()
    replay_file = next(replay_root.glob("org-state-testorg-*.md")).read_text()
    assert replay_file == live_file


def test_keep_fragments_refuses_a_non_empty_dir(tmp_path):
    """A stale fragment in the capture dir would become a replay row."""
    keep = tmp_path / "capture"
    keep.mkdir()
    (keep / "stale.json").write_text("{}", newline="\n")
    res, _ = _run_with_org(tmp_path, {}, "--no-file", "--keep-fragments", str(keep))
    assert res.returncode != 0
    assert "not empty" in res.stderr
    assert not (tmp_path / "gh.log").exists(), "must fail before any gh call"


def test_keep_fragments_with_render_dir_is_rejected(fragments_dir, tmp_path):
    res = _render(fragments_dir, tmp_path, "--keep-fragments", str(tmp_path / "k"))
    assert res.returncode != 0
    assert "cannot be combined with --render-dir" in res.stderr


def test_help_names_the_capture_route():
    res = subprocess.run([BASH, str(GHREPORT), "--help"],
                         capture_output=True, check=False, text=True, timeout=30)
    assert "--keep-fragments DIR" in res.stdout
    assert "--render-dir DIR" in res.stdout


@pytest.mark.skipif(IS_WINDOWS, reason="stops the run with os.killpg, which needs POSIX process groups")
def test_interrupted_capture_has_no_marker_and_replay_warns(tmp_path):
    """A capture stopped mid-query keeps a partial fragment set and no
    CAPTURE_COMPLETE marker; replaying it warns that it may be partial."""
    stub_dir = tmp_path / "bin"
    stub_dir.mkdir()
    gh = stub_dir / "gh"
    # The per-repo query hangs, so the run is stopped after settings are kept.
    gh.write_text(GH_STUB_SETTINGS.replace("*pulls*) echo '[]' ;;", "*pulls*) sleep 60 ;;"), newline="\n")
    gh.chmod(0o755)
    data = tmp_path / "gh-data"
    data.mkdir()
    (data / "orgs__testorg__rulesets.json").write_text("[]", newline="\n")
    log = tmp_path / "gh.log"
    keep = tmp_path / "capture"
    env = dict(os.environ)
    env.update(PATH=f"{stub_dir}{os.pathsep}{env['PATH']}", GH_DATA=str(data), GH_LOG=str(log))
    proc = subprocess.Popen(
        [BASH, str(GHREPORT), "--org", "testorg", "--root", str(tmp_path), "--quiet",
         "--no-file", "--keep-fragments", str(keep)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env, start_new_session=True,
    )
    try:
        deadline = time.monotonic() + 60
        while not (log.exists() and "pulls" in log.read_text()):
            assert proc.poll() is None, "run ended before the per-repo query"
            assert time.monotonic() < deadline, "per-repo query never started"
            time.sleep(0.1)
    finally:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.wait(timeout=30)
    assert (keep / "settings" / "org.json").is_file(), "settings are kept before repo queries"
    assert not (keep / "CAPTURE_COMPLETE").exists()

    rep = _render(keep, tmp_path, "--no-file")
    assert rep.returncode == 0, rep.stderr
    assert "no CAPTURE_COMPLETE marker" in rep.stderr
    assert "may be partial" in rep.stderr


# ---- GitHub teams on the org fragment (#733) --------------------------------
TEAMS = [{"slug": "platform", "parent": None},
         {"slug": "payments", "parent": {"slug": "platform"}}]


def _repo(name, role="write"):
    return {"name": name, "role_name": role}


def _captured_org_fragment(tmp_path, responses):
    keep = tmp_path / "capture"
    res, _ = _run_with_org(tmp_path, {"orgs/testorg": {"login": "testorg"}, **responses},
                           "--no-file", "--keep-fragments", str(keep))
    assert res.returncode == 0, res.stderr
    return res, json.loads((keep / "settings" / "org.json").read_text())


def test_org_fragment_carries_teams_with_parents_and_repos(tmp_path):
    res, fragment = _captured_org_fragment(tmp_path, {
        "orgs/testorg/teams": TEAMS,
        "orgs/testorg/teams/platform/repos": [_repo("svc2")],
        "orgs/testorg/teams/payments/repos": [_repo("svc1")],
    })
    assert fragment["teams"] == [
        {"slug": "payments", "parent": "platform", "repos": ["svc1"]},
        {"slug": "platform", "parent": None, "repos": ["svc2"]},
    ]
    assert set(ORG_SETTINGS) <= set(fragment)
    # Not a setting: the report's settings section is unchanged.
    assert "teams" not in _org_settings(res.stdout)
    log = (tmp_path / "gh.log").read_text()
    assert "orgs/testorg/teams/payments/repos" in log and "orgs/testorg/teams/platform/repos" in log


def test_repos_a_child_team_inherits_stay_on_the_parent(tmp_path):
    """A child team listing its parent's repositories with the parent's role
    holds them by inheritance: each repository stays with the most specific
    team that holds it directly, so parent repos do not all land in a child."""
    _, fragment = _captured_org_fragment(tmp_path, {
        "orgs/testorg/teams": TEAMS,
        "orgs/testorg/teams/platform/repos": [_repo("svc2"), _repo("svc3")],
        "orgs/testorg/teams/payments/repos": [
            _repo("svc1"), _repo("svc2"), _repo("svc3", role="admin")],
    })
    by_slug = {t["slug"]: t for t in fragment["teams"]}
    assert by_slug["platform"]["repos"] == ["svc2", "svc3"]
    # svc2 is inherited (same role); svc3 is a direct grant (a different role).
    assert by_slug["payments"]["repos"] == ["svc1", "svc3"]


@pytest.mark.parametrize("responses", [
    {"orgs/testorg/teams": 403},
    {"orgs/testorg/teams": TEAMS, "orgs/testorg/teams/platform/repos": [_repo("svc2")],
     "orgs/testorg/teams/payments/repos": 403},
    {"orgs/testorg/teams": '{"message": "not a list"}'},
], ids=["team-list-denied", "team-repos-denied", "team-list-malformed"])
def test_unreadable_teams_leave_the_key_out(tmp_path, responses):
    """An unread team list is never an empty one: the key is left out."""
    res, fragment = _captured_org_fragment(tmp_path, responses)
    assert "teams" not in fragment
    assert set(ORG_SETTINGS) <= set(fragment)
    assert "Org settings" in res.stdout
    assert "GitHub teams not read for testorg" in res.stderr


def test_an_org_with_no_teams_has_an_empty_team_list(tmp_path):
    _, fragment = _captured_org_fragment(tmp_path, {"orgs/testorg/teams": []})
    assert fragment["teams"] == []
