"""Unit tests for floor_anchor.py pure helpers (remediation + step summary + path match).

The API-driven checks are exercised in CI against live settings; these cover the
deterministic, network-free surface: the fail-closed remediation message, the
job-summary writer, and the path-glob matcher.
"""
import pytest

import floor_anchor
from floor_anchor import (
    _path_matches,
    _write_step_summary,
    check_required_check,
    main,
    remediation,
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


# ── remediation: crisp, complete, actionable ─────────────────────────────────

def test_remediation_names_all_three_settings_actions():
    text = remediation(REPO)
    # Command 1: required status check registration.
    assert "required_status_checks" in text
    assert "checks[][context]=floor enforcement" in text
    # Command 2: path-restriction push ruleset.
    assert "file_path_restriction" in text
    assert floor_anchor.FLOOR_PATH in text
    # Command 3: the anchor read token secret.
    assert "gh secret set FLOOR_ANCHOR_TOKEN" in text
    assert "Administration: read" in text


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


# ── _path_matches: glob-aware path restriction matching ──────────────────────

def test_path_matches_exact():
    assert _path_matches(floor_anchor.FLOOR_PATH, [floor_anchor.FLOOR_PATH])


def test_path_matches_glob():
    assert _path_matches(floor_anchor.FLOOR_PATH, [".github/workflows/*.yml"])


def test_path_matches_none_when_unrelated():
    assert not _path_matches(floor_anchor.FLOOR_PATH, ["docs/**"])


def test_path_matches_empty_is_false():
    assert not _path_matches(floor_anchor.FLOOR_PATH, [])


# ── _write_step_summary: best-effort job-summary output ──────────────────────

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


# ── main: fails closed when no token is available ────────────────────────────

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
