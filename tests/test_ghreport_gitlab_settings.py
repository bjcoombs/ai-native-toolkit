"""ghreport's GitLab provider: the group settings section.

The fixture and stubs live in `ghreport_gitlab_env.py`.
"""

import json
import subprocess
from pathlib import Path

import pytest
from ghreport_gitlab_env import (
    GHREPORT,
    HOST,
    SETTINGS,
    Env,
    _group,
    _group_json,
    _settings,
)
from gitbash import BASH


@pytest.fixture
def env(tmp_path: Path) -> Env:
    return Env(tmp_path)


@pytest.fixture
def full_run(env: Env) -> subprocess.CompletedProcess[str]:
    return env.run("--host", HOST)


def test_unreadable_group_reports_every_setting_unknown(env: Env, full_run: subprocess.CompletedProcess[str]) -> None:
    assert full_run.returncode == 0, full_run.stderr
    settings = _settings(full_run.stdout, "grp")
    assert set(settings) == set(SETTINGS)
    for name, value in settings.items():
        assert value.startswith("unknown ("), (name, value)
        assert value != "unknown ()", name
    assert "HTTP 404" in settings["two_factor"]
    assert "HTTP 404" in settings["push_rules"]
    assert "1 group(s): 0 setting(s) off, 4 unknown" in full_run.stdout
    report = next(env.root.glob(f"org-state-{HOST}-*.md")).read_text()
    assert "## Group settings" in report
    assert "1 group(s): 0 setting(s) off, 4 unknown" in report


def test_settings_section_leaves_the_project_rows_alone(full_run: subprocess.CompletedProcess[str]) -> None:
    rows = [ln for ln in full_run.stdout.splitlines()
            if ln.startswith("| ") and "Project" not in ln and "---" not in ln]
    assert len(rows) == 5
    assert full_run.stdout.startswith("\n====== Org State:")


def test_readable_group_settings(tmp_path: Path) -> None:
    env = Env(tmp_path, {
        "groups/grp": _group_json(require_two_factor_authentication=True, default_branch_protection=2),
        "groups/grp/push_rule": [{
            "id": 9, "member_check": True, "prevent_secrets": True, "deny_delete_tag": False,
            "commit_message_regex": "^(feat|fix):", "branch_name_regex": "", "max_file_size": 0,
        }],
    })
    res = env.run("--host", HOST, "--no-file")
    assert res.returncode == 0, res.stderr
    assert _settings(res.stdout, "grp") == {
        "two_factor": "on",
        "push_rules": "member_check, prevent_secrets, commit_message_regex",
        "default_branch_protection": "full",
        "rulesets": "unknown (endpoint missing: GitLab has no group rulesets)",
    }
    assert "1 group(s): 0 setting(s) off, 1 unknown" in res.stdout
    assert "groups/grp/push_rule" in " ".join(env.api_calls())


def test_group_settings_off(tmp_path: Path) -> None:
    env = Env(tmp_path, {
        "groups/grp": _group_json(require_two_factor_authentication=False, default_branch_protection=0),
        "groups/grp/push_rule": [{"id": 9, "member_check": False, "commit_message_regex": None}],
    })
    res = env.run("--host", HOST, "--no-file")
    settings = _settings(res.stdout, "grp")
    assert settings["two_factor"] == "off"
    assert settings["push_rules"] == "off"
    assert settings["default_branch_protection"] == "off"
    assert "1 group(s): 3 setting(s) off, 1 unknown" in res.stdout


@pytest.mark.parametrize(
    ("defaults", "expected"),
    [
        ({"allowed_to_push": [{"access_level": 40}], "allowed_to_merge": [{"access_level": 30}],
          "allow_force_push": False}, "push: maintainers, merge: developers"),
        ({"allowed_to_push": [{"access_level": 30}], "allow_force_push": True}, "off"),
    ],
)
def test_default_branch_protection_defaults(tmp_path: Path, defaults: dict[str, object], expected: str) -> None:
    env = Env(tmp_path, {"groups/grp": _group_json(default_branch_protection_defaults=defaults)})
    res = env.run("--host", HOST, "--no-file")
    assert _settings(res.stdout, "grp")["default_branch_protection"] == expected


@pytest.mark.parametrize("code", [401, 403, 404, 500])
def test_push_rule_failure_is_never_off(tmp_path: Path, code: int) -> None:
    env = Env(tmp_path, {
        "groups/grp": _group_json(require_two_factor_authentication=True),
        "groups/grp/push_rule": code,
    })
    res = env.run("--host", HOST, "--no-file")
    value = _settings(res.stdout, "grp")["push_rules"]
    assert value.startswith("unknown (") and f"HTTP {code}" in value


def test_group_settings_error_body_is_unknown(tmp_path: Path) -> None:
    env = Env(tmp_path, {"groups/grp": [{"message": "403 Forbidden"}],
                         "groups/grp/push_rule": [{"message": "404 Not Found"}]})
    res = env.run("--host", HOST, "--no-file")
    settings = _settings(res.stdout, "grp")
    assert settings["two_factor"] == "unknown (unreadable response)"
    assert settings["push_rules"] == "unknown (unreadable response)"


@pytest.mark.parametrize("jq16", [False, True], ids=["installed-jq", "jq16-empty-input"])
def test_empty_group_settings_body_is_unknown(tmp_path: Path, request: pytest.FixtureRequest, jq16: bool) -> None:
    """A group or push-rule endpoint that exits 0 with an empty body reads
    unknown on jq 1.6's empty-input `-e` behaviour too, and the project table
    still renders (issue #603)."""
    env = Env(tmp_path, {"groups/grp": [""], "groups/grp/push_rule": [""]})
    if jq16:
        env.path_first(request.getfixturevalue("jq16_bin"))
    res = env.run("--host", HOST, "--no-file")
    assert res.returncode == 0, res.stderr
    settings = _settings(res.stdout, "grp")
    assert settings["two_factor"] == "unknown (unreadable response)"
    assert settings["push_rules"] == "unknown (unreadable response)"
    rows = [ln for ln in res.stdout.splitlines()
            if ln.startswith("| ") and "Project" not in ln and "---" not in ln]
    assert len(rows) == 5


def test_group_flag_scopes_the_settings(env: Env) -> None:
    res = env.run("--host", HOST, "--group", "grp/sub", "--no-file")
    assert res.returncode == 0, res.stderr
    assert set(_settings(res.stdout, "grp/sub")) == set(SETTINGS)
    assert "- grp\n" not in res.stdout.split("Group settings")[1]
    assert any("groups/grp%2Fsub/push_rule" in c for c in env.api_calls())


def test_render_dir_replays_settings_from_their_subdirectory(tmp_path: Path) -> None:
    frags = tmp_path / "frags"
    (frags / "settings").mkdir(parents=True)
    (frags / "0.json").write_text(json.dumps({
        "repo": "grp/real", "unreachable": False, "default_branch": "main", "empty_repo": False,
        "open_mrs": 0, "draft_mrs": 0, "pipeline": "success", "vulnerabilities": "no-access",
        "protection": "protected",
    }), newline="\n")
    unknown = {"value": "unknown", "reason": "denied: no access or tier (HTTP 403)"}
    (frags / "settings" / "0.json").write_text(json.dumps({
        "scope": "grp", "provider": "gitlab", "two_factor": unknown, "push_rules": unknown,
        "default_branch_protection": unknown, "rulesets": unknown,
    }), newline="\n")
    res = subprocess.run(
        [BASH, str(GHREPORT), "--provider", "gitlab", "--render-dir", str(frags), "--no-file"],
        capture_output=True, check=False, text=True, timeout=60,
    )
    assert res.returncode == 0, res.stderr
    assert "1 project(s)" in res.stdout
    assert "1 group(s): 0 setting(s) off, 4 unknown" in res.stdout
    assert _settings(res.stdout, "grp")["two_factor"] == "unknown (denied: no access or tier (HTTP 403))"


def test_settings_cover_only_top_level_member_groups(tmp_path: Path) -> None:
    """Membership in a group and its subgroup yields one settings entry, for
    the top-level group: the subgroup is not reported separately."""
    env = Env(tmp_path, {"groups": [[_group("grp"), _group("grp/sub")]]})
    res = env.run("--host", HOST, "--no-file")
    assert res.returncode == 0, res.stderr
    section = res.stdout.split("Group settings")[1]
    assert "1 group(s):" in section
    assert "- grp\n" in section
    assert "- grp/sub" not in section
    assert not any("groups/grp%2Fsub/push_rule" in c for c in env.api_calls())


@pytest.mark.parametrize(
    ("push", "force", "expected"),
    [
        (40, True, "push: maintainers, merge: developers, force push: allowed"),
        (30, True, "off"),
        (30, False, "push: developers, merge: developers"),
    ],
)
def test_force_push_alone_is_not_off(tmp_path: Path, push: int, force: bool, expected: str) -> None:
    """GitLab's unprotected preset is developers push plus force push; force
    push with maintainers-only push is reported, not called off."""
    defaults = {"allowed_to_push": [{"access_level": push}],
                "allowed_to_merge": [{"access_level": 30}], "allow_force_push": force}
    env = Env(tmp_path, {"groups/grp": _group_json(default_branch_protection_defaults=defaults)})
    res = env.run("--host", HOST, "--no-file")
    assert _settings(res.stdout, "grp")["default_branch_protection"] == expected


def test_unreadable_group_list_keeps_the_section(tmp_path: Path) -> None:
    """Discovery reads the group list once; the settings read fails. The
    section stays, every setting unknown, rather than disappearing."""
    env = Env(tmp_path)
    (tmp_path / "glab-data" / "groups.fail-after").write_text("1", newline="\n")
    res = env.run("--host", HOST, "--no-file")
    assert res.returncode == 0, res.stderr
    assert "5 project(s)" in res.stdout
    settings = _settings(res.stdout, HOST)
    assert set(settings) == set(SETTINGS)
    assert set(settings.values()) == {"unknown (group list unreadable)"}
    assert "1 group(s): 0 setting(s) off, 4 unknown" in res.stdout


def test_keep_fragments_capture_then_replay_is_identical(env: Env) -> None:
    """#694: a live GitLab run captured with --keep-fragments replays through
    --render-dir to the same terminal report and markdown file, with the group
    settings read from settings/."""
    keep = env.tmp / "capture"
    live = env.run("--host", HOST, "--quiet", "--keep-fragments", str(keep))
    assert live.returncode == 0, live.stderr
    assert any(keep.glob("*.json")), "no per-project fragments kept"
    assert any((keep / "settings").glob("*.json")), "no settings fragments kept"
    live_file = next(env.root.glob(f"org-state-{HOST}-*.md"))
    live_body = live_file.read_text()
    live_file.unlink()
    calls_before = len(env.api_calls())

    replay = env.run("--host", HOST, "--quiet", "--render-dir", str(keep))
    assert replay.returncode == 0, replay.stderr
    assert replay.stdout == live.stdout
    assert next(env.root.glob(f"org-state-{HOST}-*.md")).read_text() == live_body
    assert "Group settings" in replay.stdout
    assert len(env.api_calls()) == calls_before, "replay must not call the API"
