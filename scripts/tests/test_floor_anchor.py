"""Unit tests for floor_anchor.py pure helpers (remediation + step summary +
both-contexts requirement + the E2 path-lock descope warning).

The API-driven checks are exercised in CI against live settings; these cover the
deterministic, network-free surface: the fail-closed remediation message, the
job-summary writers, the both-contexts requirement, and the descope warning that
replaced the former (org-only, HTTP 422) path-restriction hard check.
"""
import pytest

import floor_anchor
from floor_anchor import (
    _descope_warning,
    _write_step_summary,
    check_required_check,
    main,
    remediation,
    warn_path_lock_descoped,
)

REPO = "bjcoombs/ai-native-toolkit"


def _stub_get(required_contexts):
    """Return a fake ``_get`` that reports ``required_contexts`` on the branch and
    no rulesets, so ``check_required_check`` runs offline against a known set."""

    def _get(path, token):
        if "required_status_checks" in path:
            return 200, {"contexts": sorted(required_contexts)}
        if path.endswith("/rulesets"):
            return 200, []
        return 404, {}

    return _get


def _stub_get_full(required_contexts, default_branch="main"):
    """Like ``_stub_get`` but also answers the repo-metadata call so ``main`` can
    resolve the default branch and run end-to-end offline."""

    def _get(path, token):
        if path == f"/repos/{REPO}":
            return 200, {"default_branch": default_branch}
        if "required_status_checks" in path:
            return 200, {"contexts": sorted(required_contexts)}
        if path.endswith("/rulesets"):
            return 200, []
        return 404, {}

    return _get


# ── remediation: crisp, complete, actionable, path-lock DESCOPED ─────────────

def test_remediation_names_both_settings_actions():
    text = remediation(REPO)
    # Command 1: required status check registration.
    assert "required_status_checks" in text
    assert "checks[][context]=floor enforcement" in text
    # Command 2: the anchor read token secret.
    assert "gh secret set FLOOR_ANCHOR_TOKEN" in text
    assert "Administration: read" in text


def test_remediation_omits_the_descoped_path_lock_command():
    # The path lock is descoped (org-only, HTTP 422). Remediation must NOT tell a
    # maintainer to create the ruleset -- that command fails 422 on this repo.
    text = remediation(REPO)
    assert "rulesets" not in text.lower() or "--method POST" not in text
    assert "bypass_actors" not in text


def test_remediation_documents_the_descope_decision():
    text = remediation(REPO)
    assert "DESCOPED" in text
    assert floor_anchor.PATH_LOCK_DESCOPE_DATE in text
    assert "422" in text


def test_remediation_requires_both_floor_contexts():
    # Both floor job contexts must be registered as required checks. Omitting the
    # anchor context leaves the self-anchor job non-required, so a later PR could
    # drop 'floor enforcement' from protection and the floor silently disarms (E2).
    text = remediation(REPO)
    assert "checks[][context]=floor enforcement" in text
    assert "checks[][context]=floor self-anchor" in text


def test_remediation_preserves_existing_required_contexts():
    text = remediation(REPO)
    for ctx in (
        "skills/assess pytest",
        "scripts/ pytest",
        "plugin contract pytest",
        "Validate PR title",
    ):
        assert f"checks[][context]={ctx}" in text


def test_remediation_interpolates_repo():
    assert f"repos/{REPO}/branches/main/protection" in remediation(REPO)


# ── warn_path_lock_descoped: loud, non-failing capability gap (E2) ────────────

def test_descope_warning_states_decision_evidence_and_residual():
    msg = _descope_warning()
    # The named decision + its date.
    assert "DESCOPED" in msg
    assert floor_anchor.PATH_LOCK_DESCOPE_DATE in msg
    # The 422 evidence, quoted.
    assert "422" in msg
    assert "org-owned repos can have push rules" in msg
    # The honest residual: process signals, named as such.
    assert "code review" in msg
    assert "retro" in msg
    assert "until a gutted workflow merges" in msg


def test_warn_path_lock_descoped_does_not_raise_and_annotates_stderr(capsys):
    # It must WARN, not fail: returns None, emits a ::warning:: annotation.
    assert warn_path_lock_descoped() is None
    err = capsys.readouterr().err
    assert "::warning::" in err
    assert "DESCOPED" in err


def test_warn_path_lock_descoped_writes_job_summary(tmp_path, monkeypatch):
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    warn_path_lock_descoped()
    body = summary.read_text(encoding="utf-8")
    assert "DESCOPED" in body
    assert "warning, not a failure" in body
    assert "422" in body


def test_warn_path_lock_descoped_noop_summary_without_env(monkeypatch):
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    # Must not raise when the env var is absent.
    warn_path_lock_descoped()


# ── _write_step_summary: best-effort FAIL job-summary output ─────────────────

def test_step_summary_written_when_env_set(tmp_path, monkeypatch):
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    _write_step_summary("no token available", REPO)
    body = summary.read_text(encoding="utf-8")
    assert "floor self-anchor" in body.lower()
    assert "FAIL" in body
    assert "no token available" in body
    assert "gh secret set FLOOR_ANCHOR_TOKEN" in body


def test_step_summary_noop_without_env(monkeypatch):
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    # Must not raise when the env var is absent.
    _write_step_summary("some reason", REPO)


# ── check_required_check: BOTH floor contexts must gate (PRD E2) ─────────────

def test_required_check_passes_when_both_contexts_present(monkeypatch, capsys):
    monkeypatch.setattr(
        floor_anchor,
        "_get",
        _stub_get({floor_anchor.FLOOR_CONTEXT, floor_anchor.ANCHOR_CONTEXT}),
    )
    # Both required -> no raise, and the ok line names both contexts.
    check_required_check(REPO, "main", "tok")
    out = capsys.readouterr().out
    assert floor_anchor.FLOOR_CONTEXT in out
    assert floor_anchor.ANCHOR_CONTEXT in out


def test_required_check_fails_when_anchor_context_missing(monkeypatch):
    # Deterministic layer required, self-anchor NOT: the disarm path this fix
    # closes (a later PR could then drop 'floor enforcement' silently).
    monkeypatch.setattr(
        floor_anchor, "_get", _stub_get({floor_anchor.FLOOR_CONTEXT})
    )
    with pytest.raises(floor_anchor.AnchorError) as exc:
        check_required_check(REPO, "main", "tok")
    # The specific missing context is named; the present one is not flagged missing.
    assert floor_anchor.ANCHOR_CONTEXT in str(exc.value)


def test_required_check_fails_when_floor_context_missing(monkeypatch):
    monkeypatch.setattr(
        floor_anchor, "_get", _stub_get({floor_anchor.ANCHOR_CONTEXT})
    )
    with pytest.raises(floor_anchor.AnchorError) as exc:
        check_required_check(REPO, "main", "tok")
    assert floor_anchor.FLOOR_CONTEXT in str(exc.value)


def test_required_check_fails_when_neither_context_present(monkeypatch):
    monkeypatch.setattr(floor_anchor, "_get", _stub_get(set()))
    with pytest.raises(floor_anchor.AnchorError) as exc:
        check_required_check(REPO, "main", "tok")
    msg = str(exc.value)
    assert floor_anchor.FLOOR_CONTEXT in msg
    assert floor_anchor.ANCHOR_CONTEXT in msg


# ── main: hard requirements stay fail-closed even with the path lock descoped ─

def test_main_fails_closed_without_token(monkeypatch, capsys):
    monkeypatch.setenv("GITHUB_REPOSITORY", REPO)
    monkeypatch.delenv("FLOOR_ANCHOR_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    rc = main()
    assert rc == 1
    err = capsys.readouterr().err
    assert "FAIL floor self-anchor" in err
    assert "gh secret set FLOOR_ANCHOR_TOKEN" in err


def test_main_fails_closed_when_required_contexts_missing(monkeypatch, capsys):
    # Descoping the path lock must NOT weaken the two hard requirements: with a
    # token present but the floor contexts absent from protection, main still
    # fails closed rather than passing on the descope warning alone.
    monkeypatch.setenv("GITHUB_REPOSITORY", REPO)
    monkeypatch.setenv("FLOOR_ANCHOR_TOKEN", "tok")
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    monkeypatch.setattr(floor_anchor, "_get", _stub_get_full(set()))
    rc = main()
    assert rc == 1
    err = capsys.readouterr().err
    assert "FAIL floor self-anchor" in err


def test_main_passes_and_warns_when_both_contexts_present(monkeypatch, capsys):
    # The descope working: both hard requirements hold, so the job goes GREEN
    # while loudly warning that the path lock is a documented capability gap.
    monkeypatch.setenv("GITHUB_REPOSITORY", REPO)
    monkeypatch.setenv("FLOOR_ANCHOR_TOKEN", "tok")
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    monkeypatch.setattr(
        floor_anchor,
        "_get",
        _stub_get_full({floor_anchor.FLOOR_CONTEXT, floor_anchor.ANCHOR_CONTEXT}),
    )
    rc = main()
    assert rc == 0
    captured = capsys.readouterr()
    assert "::warning::" in captured.err
    assert "DESCOPED" in captured.err
    assert "path lock is descoped" in captured.out
