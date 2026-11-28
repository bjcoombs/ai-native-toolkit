"""lib/hosting/deploy_controls.py: the deploy_controls block (#1666).

Every read is stubbed: ``open_github``, ``open_gitlab``, ``gh_api`` and ``glab_api`` are
replaced on the module, so no test reaches the network. Fixtures are invented.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import lib.hosting.deploy_controls as dc
import lib.manifest.check_manifest as cm
import lib.scoring.level_tally as lt
from lib.counts_only.content_free import counts_only
from lib.hosting.gh_cli import GhUnavailable, GithubRepo
from lib.hosting.gitlab_cli import GitlabProject

GH_ENVS = "repos/acme/widget/environments?per_page=100&page=1"
GL = GitlabProject(host="gitlab.example.com", path="grp/app")
GL_ENVS = "projects/grp%2Fapp/environments?per_page=100&page=1"
GL_PROTECTED = "projects/grp%2Fapp/protected_environments?per_page=100&page=1"
GL_PROJECT = "projects/grp%2Fapp"
COUNTS = ("production_environments", "unreviewed_count", "self_review_allowed_count")


def _reviewers(prevent_self_review: bool) -> dict[str, Any]:
    return {"type": "required_reviewers", "prevent_self_review": prevent_self_review,
            "reviewers": [{"type": "Team", "reviewer": {"id": 1}}]}


def _env(name: str, *rules: dict[str, Any]) -> dict[str, Any]:
    return {"name": name, "protection_rules": list(rules)}


def _github(monkeypatch: pytest.MonkeyPatch, pages: dict[str, Any]) -> list[str]:
    calls: list[str] = []
    monkeypatch.setattr(dc, "open_gitlab", lambda root: None)
    monkeypatch.setattr(dc, "open_github", lambda root: GithubRepo("acme", "widget"))

    def api(path: str) -> Any:
        calls.append(path)
        got = pages.get(path)
        if isinstance(got, GhUnavailable):
            raise got
        if got is None:
            raise GhUnavailable(f"not_found: `gh api {path}` (HTTP 404)")
        return got
    monkeypatch.setattr(dc, "gh_api", api)
    return calls


def _gitlab(monkeypatch: pytest.MonkeyPatch, pages: dict[str, Any]) -> None:
    monkeypatch.setattr(dc, "open_gitlab", lambda root: GL)

    def no_github(root: Path) -> GithubRepo:
        raise AssertionError("a GitLab remote never reads GitHub")
    monkeypatch.setattr(dc, "open_github", no_github)

    def api(project: GitlabProject, path: str, cache: bool = True) -> Any:
        got = pages.get(path)
        if isinstance(got, GhUnavailable):
            raise got
        if got is None:
            raise GhUnavailable(f"not_found: `glab api {path}` (HTTP 404)")
        return got
    monkeypatch.setattr(dc, "glab_api", api)


def _config(root: Path, text: str) -> Path:
    (root / ".assess").mkdir(exist_ok=True)
    (root / ".assess" / "config.toml").write_text(text, encoding="utf-8")
    return root


def _counts(block: Any) -> tuple[Any, ...]:
    return tuple(block[k] for k in COUNTS)


# --- GitHub ------------------------------------------------------------------------------


def test_required_reviewers_with_self_review_prevented(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _github(monkeypatch, {GH_ENVS: {"total_count": 1, "environments": [_env("production", _reviewers(True))]}})
    block = dc.scan_deploy_controls(tmp_path)
    assert block["available"] is True and block["provider"] == "github"
    assert _counts(block) == (1, 0, 0)


def test_required_reviewers_with_self_review_allowed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _github(monkeypatch, {GH_ENVS: {"total_count": 1, "environments": [_env("production", _reviewers(False))]}})
    assert _counts(dc.scan_deploy_controls(tmp_path)) == (1, 0, 1)


def test_a_production_environment_without_reviewers_is_unreviewed(tmp_path: Path,
                                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    envs = [_env("production", {"type": "wait_timer", "wait_timer": 5}), _env("prod"),
            _env("Production-EU", _reviewers(True))]
    _github(monkeypatch, {GH_ENVS: {"total_count": 3, "environments": envs}})
    # "Production-EU" is not named production or prod, so only the first two count.
    assert _counts(dc.scan_deploy_controls(tmp_path)) == (2, 2, 0)


def test_no_environments_reads_zero(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _github(monkeypatch, {GH_ENVS: {"total_count": 0, "environments": []}})
    block = dc.scan_deploy_controls(tmp_path)
    assert block["available"] is True and _counts(block) == (0, 0, 0)


def test_a_refused_read_is_unavailable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _github(monkeypatch, {GH_ENVS: GhUnavailable("no_access: `gh api ...` was refused (HTTP 403)")})
    block = dc.scan_deploy_controls(tmp_path)
    assert block == {"available": False, "reason": "no_access: `gh api ...` was refused (HTTP 403)"}


def test_a_missing_repository_is_unavailable_never_zero(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _github(monkeypatch, {})
    block = dc.scan_deploy_controls(tmp_path)
    assert block["available"] is False and block["reason"].startswith("not_found:")


def test_no_remote_is_unavailable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dc, "open_gitlab", lambda root: None)

    def refuse(root: Path) -> GithubRepo:
        raise GhUnavailable("no_remote: no github.com remote to compare against")
    monkeypatch.setattr(dc, "open_github", refuse)
    assert dc.scan_deploy_controls(tmp_path)["reason"].startswith("no_remote:")


def test_default_names_count_prod_and_not_staging(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    envs = [_env("prod"), _env("staging")]
    _github(monkeypatch, {GH_ENVS: {"total_count": 2, "environments": envs}})
    block = dc.scan_deploy_controls(tmp_path)
    assert block["configured"] is False and _counts(block) == (1, 1, 0)


def test_configured_names_replace_the_defaults(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    envs = [_env("prod"), _env("live", _reviewers(False)), _env("staging")]
    _github(monkeypatch, {GH_ENVS: {"total_count": 3, "environments": envs}})
    block = dc.scan_deploy_controls(_config(tmp_path, '[change_controls]\nenvironments = ["live"]\n'))
    assert block["configured"] is True and _counts(block) == (1, 0, 1)


@pytest.mark.parametrize("text", ["[change_controls]\nenvironments = []\n",
                                  '[change_controls]\nenvironments = ["  "]\n',
                                  '[change_controls]\nenvironments = "live"\n',
                                  "change_controls = 1\n"])
def test_an_empty_or_malformed_list_keeps_the_defaults(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                       text: str) -> None:
    _github(monkeypatch, {GH_ENVS: {"total_count": 1, "environments": [_env("production")]}})
    block = dc.scan_deploy_controls(_config(tmp_path, text))
    assert block["configured"] is False and _counts(block) == (1, 1, 0)


def test_every_page_is_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    first = [_env(f"review-{i}") for i in range(dc.PAGE)]
    page2 = GH_ENVS.replace("&page=1", "&page=2")
    calls = _github(monkeypatch, {GH_ENVS: {"total_count": dc.PAGE + 1, "environments": first},
                                  page2: {"total_count": dc.PAGE + 1, "environments": [_env("prod")]}})
    assert _counts(dc.scan_deploy_controls(tmp_path)) == (1, 1, 0)
    assert calls == [GH_ENVS, page2]


def test_a_list_past_the_page_ceiling_is_truncated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    full = {"total_count": 10_000, "environments": [_env(f"e{i}") for i in range(dc.PAGE)]}
    pages = {GH_ENVS.replace("&page=1", f"&page={n}"): full for n in range(1, dc.MAX_PAGES + 2)}
    _github(monkeypatch, pages)
    block = dc.scan_deploy_controls(tmp_path)
    assert block["available"] is False and block["reason"].startswith("truncated:")


def test_a_rule_without_the_self_review_flag_reads_null(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rule = {"type": "required_reviewers", "reviewers": [{"type": "User"}]}
    _github(monkeypatch, {GH_ENVS: {"total_count": 1, "environments": [_env("production", rule)]}})
    block = dc.scan_deploy_controls(tmp_path)
    assert _counts(block) == (1, 0, None)
    assert block["self_review_allowed_count_reason"] == "not_reported"


def test_a_malformed_list_is_unavailable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _github(monkeypatch, {GH_ENVS: {"environments": "x"}})
    block = dc.scan_deploy_controls(tmp_path)
    assert block["available"] is False and block["reason"].startswith("gh_bad_json:")


# --- GitLab ------------------------------------------------------------------------------


def _approval(required: int) -> dict[str, Any]:
    return {"name": "production", "deploy_access_levels": [{"access_level": 40}],
            "approval_rules": [{"access_level": 30, "required_approvals": required}]}


def test_gitlab_protected_environment_with_approvals(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _gitlab(monkeypatch, {GL_ENVS: [{"name": "production"}, {"name": "staging"}],
                          GL_PROTECTED: [_approval(1)],
                          GL_PROJECT: {"allow_pipeline_trigger_approve_deployment": False}})
    block = dc.scan_deploy_controls(tmp_path)
    assert block["provider"] == "gitlab" and _counts(block) == (1, 0, 0)


def test_gitlab_triggerer_may_approve(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _gitlab(monkeypatch, {GL_ENVS: [{"name": "prod"}], GL_PROTECTED: [{**_approval(2), "name": "prod"}],
                          GL_PROJECT: {"allow_pipeline_trigger_approve_deployment": True}})
    assert _counts(dc.scan_deploy_controls(tmp_path)) == (1, 0, 1)


def test_gitlab_protected_without_approvals_is_unreviewed(tmp_path: Path,
                                                          monkeypatch: pytest.MonkeyPatch) -> None:
    _gitlab(monkeypatch, {GL_ENVS: [{"name": "production"}],
                          GL_PROTECTED: [{**_approval(0), "approval_rules": []}],
                          GL_PROJECT: {"allow_pipeline_trigger_approve_deployment": False}})
    assert _counts(dc.scan_deploy_controls(tmp_path)) == (1, 1, 0)


def test_gitlab_legacy_approval_count_counts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    legacy = {"name": "production", "required_approval_count": 1, "approval_rules": []}
    _gitlab(monkeypatch, {GL_ENVS: [{"name": "production"}], GL_PROTECTED: [legacy],
                          GL_PROJECT: {"allow_pipeline_trigger_approve_deployment": False}})
    assert _counts(dc.scan_deploy_controls(tmp_path)) == (1, 0, 0)


def test_gitlab_tier_refusal_is_null_with_reason(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _gitlab(monkeypatch, {GL_ENVS: [{"name": "production"}],
                          GL_PROTECTED: GhUnavailable("no_access: `glab api ...` was refused (HTTP 403)"),
                          GL_PROJECT: {}})
    block = dc.scan_deploy_controls(tmp_path)
    assert block["available"] is True and _counts(block) == (1, None, None)
    assert block["unreviewed_count_reason"] == "no_access"
    assert block["self_review_allowed_count_reason"] == "no_access"


def test_gitlab_unreported_triggerer_setting_is_null(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _gitlab(monkeypatch, {GL_ENVS: [{"name": "production"}], GL_PROTECTED: [_approval(1)], GL_PROJECT: {}})
    block = dc.scan_deploy_controls(tmp_path)
    assert _counts(block) == (1, 0, None) and block["self_review_allowed_count_reason"] == "not_reported"


def test_gitlab_no_reviewed_environment_allows_no_self_review(tmp_path: Path,
                                                             monkeypatch: pytest.MonkeyPatch) -> None:
    _gitlab(monkeypatch, {GL_ENVS: [{"name": "production"}], GL_PROTECTED: [], GL_PROJECT: {}})
    block = dc.scan_deploy_controls(tmp_path)
    assert _counts(block) == (1, 1, 0) and "self_review_allowed_count_reason" not in block


def test_gitlab_refused_project_read_is_null_with_its_code(tmp_path: Path,
                                                           monkeypatch: pytest.MonkeyPatch) -> None:
    _gitlab(monkeypatch, {GL_ENVS: [{"name": "production"}], GL_PROTECTED: [_approval(1)],
                          GL_PROJECT: GhUnavailable("no_access: refused (HTTP 403)")})
    block = dc.scan_deploy_controls(tmp_path)
    assert _counts(block) == (1, 0, None) and block["self_review_allowed_count_reason"] == "no_access"


def test_gitlab_other_protected_read_failure_is_unavailable(tmp_path: Path,
                                                            monkeypatch: pytest.MonkeyPatch) -> None:
    _gitlab(monkeypatch, {GL_ENVS: [{"name": "production"}],
                          GL_PROTECTED: GhUnavailable("rate_limited: still rate limited")})
    block = dc.scan_deploy_controls(tmp_path)
    assert block == {"available": False, "reason": "rate_limited: still rate limited"}


def test_gitlab_no_production_environment_makes_no_further_read(tmp_path: Path,
                                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    _gitlab(monkeypatch, {GL_ENVS: [{"name": "review/feature"}]})
    assert _counts(dc.scan_deploy_controls(tmp_path)) == (0, 0, 0)


def test_gitlab_refused_environment_list_is_unavailable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _gitlab(monkeypatch, {GL_ENVS: GhUnavailable("no_access: `glab api ...` was refused (HTTP 403)")})
    assert dc.scan_deploy_controls(tmp_path) == {
        "available": False, "reason": "no_access: `glab api ...` was refused (HTTP 403)"}


# --- registration, privacy and the level -------------------------------------------------


def test_the_collector_runs_on_the_platform_pass() -> None:
    import lib.run.signals as signals

    assert "deploy_controls" in signals.PLATFORM_SCANS
    collector = next(c for c in cm.collectors(cm.core_manifest()) if c["id"] == "core.deploy_controls")
    assert collector.get("network") is True and collector.get("platform_pass") is True


def test_counts_only_keeps_every_count(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _gitlab(monkeypatch, {GL_ENVS: [{"name": "production"}],
                          GL_PROTECTED: GhUnavailable("no_access: refused (HTTP 403)"), GL_PROJECT: {}})
    block = dc.scan_deploy_controls(tmp_path)
    out = counts_only({"deploy_controls": block})["deploy_controls"]
    for field in ("available", "provider", "configured", *COUNTS, "unreviewed_count_reason",
                  "self_review_allowed_count_reason"):
        assert out[field] == block[field], field  # type: ignore[literal-required]


def test_the_block_leaves_the_default_level_unchanged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _github(monkeypatch, {GH_ENVS: {"total_count": 1, "environments": [_env("production")]}})
    model = lt.default_model()
    assert lt.level_result(model, {"deploy_controls": dc.scan_deploy_controls(tmp_path)}) == \
        lt.level_result(model, {})
