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


OWNER = "bjcoombs"


def _environment_payload(reviewers=(OWNER,)):
    """A GitHub environment body whose required reviewers are ``reviewers``."""
    return {
        "name": floor_anchor.SIGNOFF_ENVIRONMENT,
        "protection_rules": [
            {
                "type": "required_reviewers",
                "reviewers": [
                    {"type": "User", "reviewer": {"login": login}}
                    for login in reviewers
                ],
            }
        ],
    }


def _stub_get_full(
    required_contexts, default_branch="main", environment=None, env_status=200
):
    """Like ``_stub_get`` but also answers the repo-metadata and environment
    calls, so ``main`` can resolve the default branch, the owner and clause
    iii's sign-off environment and run end-to-end offline."""
    env_body = _environment_payload() if environment is None else environment

    def _get(path, token):
        if path == f"/repos/{REPO}":
            return 200, {"default_branch": default_branch, "owner": {"login": OWNER}}
        if "/environments/" in path:
            return env_status, env_body
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
    # Descoping the path lock must NOT weaken the three hard requirements: with a
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


# ── check_contexts_have_workflows: a required context no job emits (R19 g3) ───

WORKFLOW_WITH_TWO_JOBS = """\
name: Tests

on:
  pull_request:

jobs:
  pytest:
    name: scripts/ pytest
    runs-on: ubuntu-latest
    steps:
      - name: Run pytest
        run: pytest

  lint:
    name: 'ruff + mypy gates'
    runs-on: ubuntu-latest
    steps:
      - name: Ruff
        run: ruff check .
"""


def _workflows(tmp_path, body=WORKFLOW_WITH_TWO_JOBS, filename="tests.yml"):
    """Materialize a repo root whose .github/workflows holds one workflow."""
    wf_dir = tmp_path / ".github" / "workflows"
    wf_dir.mkdir(parents=True, exist_ok=True)
    (wf_dir / filename).write_text(body, encoding="utf-8")
    return tmp_path


WORKFLOW_WITH_AN_UNNAMED_JOB = """\
name: Review

on:
  pull_request:

jobs:
  claude-review:
    runs-on: ubuntu-latest
    steps:
      - name: Review
        run: review
"""


def test_workflow_job_names_reads_job_names_not_step_names(tmp_path):
    names = floor_anchor._workflow_job_names(_workflows(tmp_path))
    # Job names come from the four-space `name:` lines; step names sit deeper
    # behind a `- `, and the workflow's own top-level name is at column zero.
    # Both jobs carry a `name:`, so GitHub uses those literals and never the
    # ids - `pytest` and `lint` are not check-run names and must not appear,
    # and no step name ("Run pytest", "Ruff") leaks in either.
    assert names == {"scripts/ pytest", "ruff + mypy gates"}


def test_workflow_job_names_drops_the_id_of_a_named_job(tmp_path):
    # The id/name pair the caller exists to disambiguate: require the id
    # `contract-tests` instead of the name `plugin contract pytest` - the
    # natural confusion when editing branch protection by hand - and a
    # collector that kept both would print ok while the context never arrives.
    body = """\
jobs:
  contract-tests:
    name: plugin contract pytest
    runs-on: ubuntu-latest
    steps:
      - run: pytest
"""
    names = floor_anchor._workflow_job_names(_workflows(tmp_path, body=body))
    assert names == {"plugin contract pytest"}
    assert "contract-tests" not in names


def test_named_job_id_is_not_a_valid_required_context(tmp_path):
    # The end-to-end consequence: requiring the id of a named job fails closed.
    body = """\
jobs:
  contract-tests:
    name: plugin contract pytest
    runs-on: ubuntu-latest
    steps:
      - run: pytest
"""
    with pytest.raises(floor_anchor.AnchorError) as exc:
        floor_anchor.check_contexts_have_workflows(
            {"contract-tests"}, _workflows(tmp_path, body=body)
        )
    assert "contract-tests" in str(exc.value)


def test_workflow_job_names_collects_the_id_of_an_unnamed_job(tmp_path):
    # A job with no `name:` still produces a check context - GitHub falls back
    # to the job id. Missing it makes the fail-closed caller a false red the
    # moment such a context is required.
    root = _workflows(tmp_path, body=WORKFLOW_WITH_AN_UNNAMED_JOB, filename="review.yml")
    assert floor_anchor._workflow_job_names(root) == {"claude-review"}


def test_workflow_job_names_separates_a_named_job_from_an_unnamed_neighbour(tmp_path):
    # One pass, two outcomes: the named job resolves to its `name:` and drops
    # its id; the unnamed job that follows keeps its id. Both shapes coexist in
    # this repo's own workflows.
    body = """\
jobs:
  contract-tests:
    name: plugin contract pytest
    runs-on: ubuntu-latest
    steps:
      - run: pytest

  claude-review:
    runs-on: ubuntu-latest
    steps:
      - run: review
"""
    names = floor_anchor._workflow_job_names(_workflows(tmp_path, body=body))
    assert names == {"plugin contract pytest", "claude-review"}


def test_workflow_job_names_collects_a_trailing_unnamed_job(tmp_path):
    # The pending id must still be flushed when the file ends on an unnamed
    # job - there is no following two-space key to close it.
    body = """\
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - run: build
"""
    assert floor_anchor._workflow_job_names(_workflows(tmp_path, body=body)) == {"build"}


def test_workflow_job_names_reads_yaml_as_well_as_yml(tmp_path):
    root = _workflows(
        tmp_path,
        body="jobs:\n  build:\n    name: standalone build\n    runs-on: ubuntu-latest\n",
        filename="build.yaml",
    )
    assert "standalone build" in floor_anchor._workflow_job_names(root)


def test_workflow_job_names_does_not_collect_keys_outside_the_jobs_block(tmp_path):
    # Two-space keys under `on:` or `permissions:` are not job ids. Only keys
    # inside the `jobs:` block are.
    body = """\
name: Tests

on:
  pull_request:
    branches: [main]

permissions:
  contents: read

jobs:
  pytest:
    name: scripts/ pytest
    runs-on: ubuntu-latest
    steps:
      - run: pytest
"""
    names = floor_anchor._workflow_job_names(_workflows(tmp_path, body=body))
    assert names == {"scripts/ pytest"}


def test_collected_job_ids_cannot_mask_a_renamed_context(tmp_path):
    # A renamed `name:` orphans the context it used to produce: the job is
    # named, so its id never enters the set and cannot stand in for the old
    # literal. This is FC13's probe in miniature.
    body = """\
jobs:
  pytest:
    name: scripts pytest renamed
    runs-on: ubuntu-latest
    steps:
      - run: pytest
"""
    with pytest.raises(floor_anchor.AnchorError) as exc:
        floor_anchor.check_contexts_have_workflows(
            {"scripts/ pytest"}, _workflows(tmp_path, body=body)
        )
    assert "scripts/ pytest" in str(exc.value)


def test_workflow_job_names_reads_every_workflow_file(tmp_path):
    root = _workflows(tmp_path)
    _workflows(
        root,
        body="jobs:\n  floor:\n    name: floor enforcement\n    runs-on: ubuntu-latest\n",
        filename="floor.yml",
    )
    assert "floor enforcement" in floor_anchor._workflow_job_names(root)


def test_contexts_have_workflows_passes_when_every_context_is_a_job(tmp_path, capsys):
    floor_anchor.check_contexts_have_workflows({"scripts/ pytest"}, _workflows(tmp_path))
    assert "ok   " in capsys.readouterr().out


def test_contexts_have_workflows_fails_closed_on_an_orphaned_context(tmp_path):
    # A job rename orphans the context branch protection still requires: the
    # check never arrives, so the PR blocks on a check that cannot go green.
    with pytest.raises(floor_anchor.AnchorError) as exc:
        floor_anchor.check_contexts_have_workflows(
            {"scripts/ pytest", "plugin contract pytest"}, _workflows(tmp_path)
        )
    msg = str(exc.value)
    assert "plugin contract pytest" in msg
    assert "scripts/ pytest" in msg  # named among the job names it did see


def test_contexts_have_workflows_fails_closed_without_a_workflow_dir(tmp_path):
    with pytest.raises(floor_anchor.AnchorError):
        floor_anchor.check_contexts_have_workflows({"floor enforcement"}, tmp_path)


def test_contexts_have_workflows_holds_against_this_repo(capsys):
    # The live subset check, run against the checked-out workflows: every
    # context this repo requires today is produced by a job name literal.
    floor_anchor.check_contexts_have_workflows({
        "skills/assess pytest",
        "scripts/ pytest",
        "plugin contract pytest",
        "Validate PR title",
        floor_anchor.FLOOR_CONTEXT,
        floor_anchor.ANCHOR_CONTEXT,
    })
    assert "ok   " in capsys.readouterr().out


def test_required_check_returns_the_contexts_it_saw(monkeypatch):
    # main() feeds this set to the workflow check, so it has to carry every
    # required context, not just the two floor ones.
    monkeypatch.setattr(
        floor_anchor,
        "_get",
        _stub_get(
            {floor_anchor.FLOOR_CONTEXT, floor_anchor.ANCHOR_CONTEXT, "Validate PR title"}
        ),
    )
    contexts = check_required_check(REPO, "main", "tok")
    assert contexts == {
        floor_anchor.FLOOR_CONTEXT,
        floor_anchor.ANCHOR_CONTEXT,
        "Validate PR title",
    }


def test_main_fails_closed_when_a_required_context_has_no_job(monkeypatch, capsys):
    monkeypatch.setenv("GITHUB_REPOSITORY", REPO)
    monkeypatch.setenv("FLOOR_ANCHOR_TOKEN", "tok")
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    monkeypatch.setattr(
        floor_anchor,
        "_get",
        _stub_get_full(
            {floor_anchor.FLOOR_CONTEXT, floor_anchor.ANCHOR_CONTEXT, "no such job xyz"}
        ),
    )
    rc = main()
    assert rc == 1
    assert "no such job xyz" in capsys.readouterr().err


# ── clause iii's sign-off artefact: environment + workflow wiring (fail-closed) ─
#
# Disarm paths, each of which leaves the `floor sign-off` job LOOKING present
# while approving nothing. The first three: the environment is deleted, its
# required reviewer is dropped (an environment with none auto-approves its own
# deployment), or floor.yml stops wiring the environment into the job the
# required context needs. The rest (numbered in the tests below) edit the
# guards around that wiring: the refusal step, the enforcement job's `if:`,
# the sign-off trigger, the filter job's place in `needs`, and the
# never-requested step. Each must fail closed rather than pass quietly.

FLOOR_YML_WIRED = """\
name: Floor

on:
  pull_request:

jobs:
  floor:
    name: floor enforcement
    needs: [signoff, canary-changes]
    if: ${{ !cancelled() }}
    runs-on: ubuntu-latest
    steps:
      - name: Fail on a refused sign-off
        if: needs.signoff.result == 'failure'
        run: exit 1
      - name: Fail on an unclassified path filter
        if: needs.canary-changes.result != 'success'
        run: |
          echo "::error::the filter did not run"
          exit 1
      - name: Fail on a floor-core change with no approved sign-off
        if: needs.canary-changes.outputs.floor_core_changed == 'true' && needs.signoff.result != 'success'
        run: exit 1

  signoff:
    name: floor sign-off
    needs: [canary-changes]
    if: needs.canary-changes.outputs.floor_core_changed == 'true'
    environment: floor-signoff
    runs-on: ubuntu-latest
    steps:
      - name: Record the approval
        run: echo ok

  canary-changes:
    name: canary path filter
    runs-on: ubuntu-latest
    outputs:
      floor_core_changed: ${{ steps.filter.outputs.floor_core_changed }}
    steps:
      - name: Detect changes to protected paths
        id: filter
        run: |
          FLOOR_CORE="$(python scripts/floor_check.py protected \\
            --base "${BASE_SHA}" --changed --role floor-core < "${CHANGED}")"
          echo "floor_core_changed=true" >> "$GITHUB_OUTPUT"
"""


def _floor_yml(tmp_path, body=FLOOR_YML_WIRED):
    """Materialize a repo root whose .github/workflows holds one floor.yml."""
    wf_dir = tmp_path / ".github" / "workflows"
    wf_dir.mkdir(parents=True, exist_ok=True)
    (wf_dir / "floor.yml").write_text(body, encoding="utf-8")
    return tmp_path


def test_signoff_environment_passes_when_the_owner_is_a_required_reviewer(
    monkeypatch, capsys
):
    monkeypatch.setattr(floor_anchor, "_get", _stub_get_full(set()))
    floor_anchor.check_signoff_environment(REPO, "tok")
    out = capsys.readouterr().out
    assert "ok   " in out
    assert floor_anchor.SIGNOFF_ENVIRONMENT in out
    assert OWNER in out


def test_signoff_environment_fails_closed_when_the_environment_is_absent(monkeypatch):
    # Disarm path 1: the environment is deleted in repo settings. The job still
    # names it, GitHub requests no review, and every floor change self-approves.
    monkeypatch.setattr(
        floor_anchor, "_get", _stub_get_full(set(), env_status=404, environment={})
    )
    with pytest.raises(floor_anchor.AnchorError) as exc:
        floor_anchor.check_signoff_environment(REPO, "tok")
    msg = str(exc.value)
    assert floor_anchor.SIGNOFF_ENVIRONMENT in msg
    assert "does not exist" in msg


def test_signoff_environment_fails_closed_when_the_owner_is_not_a_reviewer(monkeypatch):
    # Disarm path 2: the environment survives but its required-reviewer rule no
    # longer lists the owner, so the deployment approves itself.
    monkeypatch.setattr(
        floor_anchor,
        "_get",
        _stub_get_full(set(), environment=_environment_payload(reviewers=())),
    )
    with pytest.raises(floor_anchor.AnchorError) as exc:
        floor_anchor.check_signoff_environment(REPO, "tok")
    msg = str(exc.value)
    assert OWNER in msg
    assert floor_anchor.SIGNOFF_ENVIRONMENT in msg


def test_signoff_environment_fails_closed_when_only_a_team_reviews(monkeypatch):
    # A team is not the maintainer clause iii names, and membership can change
    # without any floor edit, so a team-only rule is not the artefact.
    team_rule = {
        "protection_rules": [
            {
                "type": "required_reviewers",
                "reviewers": [{"type": "Team", "reviewer": {"slug": "maintainers"}}],
            }
        ]
    }
    monkeypatch.setattr(
        floor_anchor, "_get", _stub_get_full(set(), environment=team_rule)
    )
    with pytest.raises(floor_anchor.AnchorError):
        floor_anchor.check_signoff_environment(REPO, "tok")


def test_signoff_environment_fails_closed_on_an_unreadable_response(monkeypatch):
    # A 403 (token without the scope) is an inability to confirm, never a pass.
    monkeypatch.setattr(
        floor_anchor,
        "_get",
        _stub_get_full(set(), env_status=403, environment={"message": "Forbidden"}),
    )
    with pytest.raises(floor_anchor.AnchorError) as exc:
        floor_anchor.check_signoff_environment(REPO, "tok")
    assert "403" in str(exc.value)


def test_signoff_workflow_wiring_passes_on_a_wired_floor_yml(tmp_path, capsys):
    floor_anchor.check_workflow_wiring(_floor_yml(tmp_path))
    out = capsys.readouterr().out
    assert "ok   " in out
    assert "signoff" in out


def test_signoff_workflow_wiring_fails_closed_without_the_environment_line(tmp_path):
    # Disarm path 3a: the environment survives in settings but no job names it,
    # so no deployment review is ever requested.
    body = FLOOR_YML_WIRED.replace("    environment: floor-signoff\n", "")
    with pytest.raises(floor_anchor.AnchorError) as exc:
        floor_anchor.check_workflow_wiring(_floor_yml(tmp_path, body))
    msg = str(exc.value)
    assert floor_anchor.SIGNOFF_ENVIRONMENT in msg
    assert floor_anchor.FLOOR_PATH in msg


def test_signoff_wiring_fails_closed_when_enforcement_drops_the_filter_job(tmp_path):
    # Disarm path 10: `floor enforcement` keeps the sign-off edge but drops
    # the filter job from `needs`. Its never-requested step then reads an
    # empty output, compares it to 'true', and never fires.
    body = FLOOR_YML_WIRED.replace(
        "    needs: [signoff, canary-changes]\n", "    needs: [signoff]\n"
    )
    with pytest.raises(floor_anchor.AnchorError) as exc:
        floor_anchor.check_workflow_wiring(_floor_yml(tmp_path, body))
    msg = str(exc.value)
    assert floor_anchor.FLOOR_CONTEXT in msg
    assert "canary-changes" in msg
    assert "`needs:`" in msg


def test_signoff_wiring_fails_closed_when_the_filter_job_does_not_exist(tmp_path):
    # Disarm path 11: both guards still name `canary-changes`, but the job
    # itself is gone (renamed, say). GitHub reads its output as empty rather
    # than erroring, so the review is never requested and the step never
    # fires.
    body = FLOOR_YML_WIRED.replace("  canary-changes:\n", "  path-filter:\n")
    with pytest.raises(floor_anchor.AnchorError) as exc:
        floor_anchor.check_workflow_wiring(_floor_yml(tmp_path, body))
    msg = str(exc.value)
    assert "no job 'canary-changes'" in msg


def test_signoff_workflow_wiring_fails_closed_when_enforcement_drops_needs(tmp_path):
    # Disarm path 3b: the job still requests the review, but the required
    # context no longer depends on it, so a refusal cannot turn it red.
    body = FLOOR_YML_WIRED.replace("    needs: [signoff, canary-changes]\n", "")
    with pytest.raises(floor_anchor.AnchorError) as exc:
        floor_anchor.check_workflow_wiring(_floor_yml(tmp_path, body))
    msg = str(exc.value)
    assert "needs" in msg
    assert floor_anchor.FLOOR_CONTEXT in msg


def test_signoff_workflow_wiring_reads_a_block_list_needs(tmp_path, capsys):
    # `needs:` takes three YAML shapes; a block list must not read as unwired.
    body = FLOOR_YML_WIRED.replace(
        "    needs: [signoff, canary-changes]\n",
        "    needs:\n      - signoff\n      - canary-changes\n",
    )
    floor_anchor.check_workflow_wiring(_floor_yml(tmp_path, body))
    assert "ok   " in capsys.readouterr().out


def test_signoff_workflow_wiring_fails_closed_without_a_floor_yml(tmp_path):
    with pytest.raises(floor_anchor.AnchorError):
        floor_anchor.check_workflow_wiring(tmp_path)


def test_signoff_wiring_holds_against_this_repo(capsys):
    # The live assertion, run against the checked-out floor.yml: this branch
    # still wires the environment into the job the required context needs.
    floor_anchor.check_workflow_wiring()
    assert "ok   " in capsys.readouterr().out


def test_signoff_environment_name_is_read_from_the_environment(monkeypatch):
    # The name is an env var (like FLOOR_CONTEXT/ANCHOR_CONTEXT) so the
    # fail-closed branch can be driven live against a name that cannot exist.
    monkeypatch.setattr(floor_anchor, "SIGNOFF_ENVIRONMENT", "no-such-environment-probe")
    monkeypatch.setattr(
        floor_anchor, "_get", _stub_get_full(set(), env_status=404, environment={})
    )
    with pytest.raises(floor_anchor.AnchorError) as exc:
        floor_anchor.check_signoff_environment(REPO, "tok")
    assert "no-such-environment-probe" in str(exc.value)


def test_signoff_environment_fails_closed_when_a_second_user_is_listed(monkeypatch):
    # An environment review is satisfied by ANY ONE of its reviewers, so
    # {owner, someone-else} is a disarm, not a safe superset: the owner's click
    # is no longer required. A membership test would pass this.
    monkeypatch.setattr(
        floor_anchor,
        "_get",
        _stub_get_full(
            set(), environment=_environment_payload(reviewers=(OWNER, "someone-else"))
        ),
    )
    with pytest.raises(floor_anchor.AnchorError) as exc:
        floor_anchor.check_signoff_environment(REPO, "tok")
    assert "someone-else" in str(exc.value)


def test_signoff_environment_fails_closed_when_a_team_reviews_beside_the_owner(
    monkeypatch,
):
    payload = _environment_payload()
    payload["protection_rules"][0]["reviewers"].append(
        {"type": "Team", "reviewer": {"slug": "maintainers"}}
    )
    monkeypatch.setattr(
        floor_anchor, "_get", _stub_get_full(set(), environment=payload)
    )
    with pytest.raises(floor_anchor.AnchorError) as exc:
        floor_anchor.check_signoff_environment(REPO, "tok")
    assert "maintainers" in str(exc.value)


def test_signoff_wiring_fails_closed_when_the_refusal_step_is_deleted(tmp_path):
    # Disarm path 4: environment, needs edge and anchor all intact, but the
    # step that converts a refusal into a red job is gone, so refusal is a
    # no-op -- the sign-off job fails alone and the required context is green.
    body = FLOOR_YML_WIRED.replace(
        "      - name: Fail on a refused sign-off\n"
        "        if: needs.signoff.result == 'failure'\n"
        "        run: exit 1\n",
        "      - name: Check out\n        run: true\n",
    )
    with pytest.raises(floor_anchor.AnchorError) as exc:
        floor_anchor.check_workflow_wiring(_floor_yml(tmp_path, body))
    assert "'failure'" in str(exc.value)


def test_signoff_wiring_fails_closed_on_a_signoff_result_conjunct(tmp_path):
    # Disarm path 5: the guard gains `&& needs.signoff.result != 'failure'`, so
    # a refused review SKIPS the required context -- which branch protection
    # reads as satisfied. The guard must stay `${{ !cancelled() }}` alone.
    body = FLOOR_YML_WIRED.replace(
        "    if: ${{ !cancelled() }}\n",
        "    if: ${{ !cancelled() && needs.signoff.result != 'failure' }}\n",
    )
    with pytest.raises(floor_anchor.AnchorError) as exc:
        floor_anchor.check_workflow_wiring(_floor_yml(tmp_path, body))
    msg = str(exc.value)
    assert "cancelled" in msg
    assert floor_anchor.FLOOR_CONTEXT in msg


def test_signoff_wiring_fails_closed_on_any_needs_result_conjunct(tmp_path):
    # Disarm path 5b: the conjunct names the path filter, not the sign-off.
    # `needs.canary-changes.result == 'success'` skips the required context
    # when the filter fails -- and a filter that never ran means the sign-off
    # was never requested. Any `needs.<job>.result` on the guard is rejected.
    body = FLOOR_YML_WIRED.replace(
        "    if: ${{ !cancelled() }}\n",
        "    if: ${{ !cancelled() && needs.canary-changes.result == 'success' }}\n",
    )
    with pytest.raises(floor_anchor.AnchorError) as exc:
        floor_anchor.check_workflow_wiring(_floor_yml(tmp_path, body))
    msg = str(exc.value)
    assert "cancelled" in msg
    assert "needs.canary-changes.result" in msg


def test_signoff_wiring_fails_closed_when_the_signoff_trigger_is_absent(tmp_path):
    # Disarm path 6: the sign-off job loses its `if:`. The review is then
    # requested on every PR, which drowns the maintainer's click in noise
    # rather than reserving it for a floor-core change.
    body = FLOOR_YML_WIRED.replace(
        "    if: needs.canary-changes.outputs.floor_core_changed == 'true'\n", ""
    )
    with pytest.raises(floor_anchor.AnchorError) as exc:
        floor_anchor.check_workflow_wiring(_floor_yml(tmp_path, body))
    msg = str(exc.value)
    assert "no job-level `if:`" in msg
    assert floor_anchor.FLOOR_CORE_OUTPUT in msg


@pytest.mark.parametrize(
    "guard",
    [
        # re-keyed to a different output: the review is never requested
        "needs.canary-changes.outputs.run_canaries == 'true'",
        # an extra conjunct: the PR under review decides who needs the review
        "needs.canary-changes.outputs.floor_core_changed == 'true' && github.actor != 'bjcoombs'",
        # compared to the wrong literal: skipped on every PR
        "needs.canary-changes.outputs.floor_core_changed == 'yes'",
        # a constant: skipped on every PR while the environment line survives
        "false",
    ],
)
def test_signoff_wiring_fails_closed_when_the_signoff_trigger_is_rekeyed(
    tmp_path, guard
):
    # Disarm path 7: the `if:` survives but no longer says what the path
    # filter said. The environment, the needs edge and the refusal step are
    # all untouched, so only a pinned trigger catches it.
    body = FLOOR_YML_WIRED.replace(
        "    if: needs.canary-changes.outputs.floor_core_changed == 'true'\n",
        f"    if: {guard}\n",
    )
    with pytest.raises(floor_anchor.AnchorError) as exc:
        floor_anchor.check_workflow_wiring(_floor_yml(tmp_path, body))
    msg = str(exc.value)
    assert "alone" in msg
    assert floor_anchor.FLOOR_CORE_OUTPUT in msg


@pytest.mark.parametrize(
    "guard",
    [
        "${{ needs.canary-changes.outputs.floor_core_changed == 'true' }}",
        'needs.canary-changes.outputs.floor_core_changed == "true"',
        "needs.canary-changes.outputs.floor_core_changed=='true'",
    ],
)
def test_signoff_wiring_accepts_equivalent_spellings_of_the_trigger(
    tmp_path, guard, capsys
):
    # The `${{ }}` wrapper, double quotes and spacing are the same guard to
    # GitHub; the anchor pins the expression, not its whitespace.
    body = FLOOR_YML_WIRED.replace(
        "    if: needs.canary-changes.outputs.floor_core_changed == 'true'\n",
        f"    if: {guard}\n",
    )
    floor_anchor.check_workflow_wiring(_floor_yml(tmp_path, body))
    assert floor_anchor.FLOOR_CORE_OUTPUT in capsys.readouterr().out


def test_signoff_wiring_fails_closed_when_the_filter_job_leaves_needs(tmp_path):
    # Disarm path 8: the trigger still reads the filter's output, but the
    # filter job is no longer in the sign-off job's `needs`. GitHub evaluates
    # an output from a job outside `needs` as empty, so the guard is false on
    # every PR and the review is never requested.
    body = FLOOR_YML_WIRED.replace(
        "    needs: [canary-changes]\n    if: needs.canary-changes",
        "    if: needs.canary-changes",
    )
    with pytest.raises(floor_anchor.AnchorError) as exc:
        floor_anchor.check_workflow_wiring(_floor_yml(tmp_path, body))
    msg = str(exc.value)
    assert "canary-changes" in msg
    assert "`needs:`" in msg


def test_signoff_wiring_fails_closed_when_the_never_requested_step_is_deleted(
    tmp_path,
):
    # Disarm path 9: the runtime mirror of the pinned trigger. With this step
    # gone, a sign-off job that skips instead of refusing (its `if:` edited
    # in a PR the anchor did not run against, or the filter output renamed)
    # leaves the required context green.
    body = FLOOR_YML_WIRED.replace(
        "      - name: Fail on a floor-core change with no approved sign-off\n"
        "        if: needs.canary-changes.outputs.floor_core_changed == 'true'"
        " && needs.signoff.result != 'success'\n"
        "        run: exit 1\n",
        "",
    )
    with pytest.raises(floor_anchor.AnchorError) as exc:
        floor_anchor.check_workflow_wiring(_floor_yml(tmp_path, body))
    msg = str(exc.value)
    assert "never requested" in msg
    assert floor_anchor.FLOOR_CORE_OUTPUT in msg


def test_signoff_wiring_fails_closed_when_the_unclassified_step_is_deleted(tmp_path):
    # Disarm path 12: the step that reds a filter that never ran is gone. A
    # failed filter then leaves the sign-off SKIPPED (not refused) and the
    # required context green with no classification behind it.
    body = FLOOR_YML_WIRED.replace(
        "      - name: Fail on an unclassified path filter\n"
        "        if: needs.canary-changes.result != 'success'\n"
        "        run: |\n"
        '          echo "::error::the filter did not run"\n'
        "          exit 1\n",
        "",
    )
    with pytest.raises(floor_anchor.AnchorError) as exc:
        floor_anchor.check_workflow_wiring(_floor_yml(tmp_path, body))
    msg = str(exc.value)
    assert "needs.canary-changes.result != 'success'" in msg
    assert "classification" in msg


def test_signoff_wiring_fails_closed_when_the_unclassified_step_names_another_job(
    tmp_path,
):
    # Disarm path 12b: the guard survives but reads a job other than the one
    # the never-requested step classifies from.
    body = FLOOR_YML_WIRED.replace(
        "        if: needs.canary-changes.result != 'success'\n",
        "        if: needs.signoff.result != 'success'\n",
    )
    with pytest.raises(floor_anchor.AnchorError) as exc:
        floor_anchor.check_workflow_wiring(_floor_yml(tmp_path, body))
    assert "needs.canary-changes.result != 'success'" in str(exc.value)


@pytest.mark.parametrize(
    "script",
    [
        "        run: exit 1\n",
        "        run: |\n"
        '          echo "::error::the filter did not run"\n'
        "          exit 1\n",
        "        if: needs.canary-changes.outputs.floor_core_changed == 'true'"
        " && needs.signoff.result != 'success'\n"
        "        run: exit 1\n",
    ],
)
def test_signoff_wiring_fails_closed_when_a_conversion_step_runs_true(
    tmp_path, script
):
    # Disarm path 13: every guard is byte-identical, but the script behind it
    # no longer exits non-zero. `run: true` fires the step into a pass, so a
    # refusal, a never-requested review or an unclassified filter converts to
    # nothing. Applied in turn to the refusal step (inline run), the
    # unclassified step (block run) and the never-requested step.
    hollow = script.rsplit("        run:", 1)[0] + "        run: true\n"
    assert script in FLOOR_YML_WIRED
    body = FLOOR_YML_WIRED.replace(script, hollow, 1)
    with pytest.raises(floor_anchor.AnchorError) as exc:
        floor_anchor.check_workflow_wiring(_floor_yml(tmp_path, body))
    msg = str(exc.value)
    assert "never exits non-zero" in msg
    assert "'true'" in msg


def test_signoff_wiring_accepts_a_block_run_that_exits_non_zero(tmp_path, capsys):
    # A multi-line `run: |` whose last line is `exit 1` is the real floor.yml
    # shape; the block reader must find the exit inside it.
    body = FLOOR_YML_WIRED.replace(
        "        if: needs.signoff.result == 'failure'\n        run: exit 1\n",
        "        if: needs.signoff.result == 'failure'\n"
        "        run: |\n"
        '          echo "::error::refused"\n'
        "          exit 1\n",
    )
    floor_anchor.check_workflow_wiring(_floor_yml(tmp_path, body))
    assert "ok   " in capsys.readouterr().out


def test_signoff_wiring_fails_closed_when_enforcement_has_no_guard(tmp_path):
    # Disarm path 14: the `if: ${{ !cancelled() }}` line is simply removed.
    # GitHub's default guard is `success()`, so a refused sign-off SKIPS the
    # required context -- absent, which branch protection reads as satisfied.
    body = FLOOR_YML_WIRED.replace("    if: ${{ !cancelled() }}\n", "", 1)
    with pytest.raises(floor_anchor.AnchorError) as exc:
        floor_anchor.check_workflow_wiring(_floor_yml(tmp_path, body))
    msg = str(exc.value)
    assert "no `if:` at all" in msg
    assert "cancelled" in msg


def test_signoff_wiring_fails_closed_when_the_filter_declares_no_output(tmp_path):
    # Disarm path 15: every consumer still reads floor_core_changed, but the
    # producer no longer declares it under `outputs:`, so it is empty on
    # every run and the review is never requested.
    body = FLOOR_YML_WIRED.replace(
        "      floor_core_changed: ${{ steps.filter.outputs.floor_core_changed }}\n",
        "",
    )
    with pytest.raises(floor_anchor.AnchorError) as exc:
        floor_anchor.check_workflow_wiring(_floor_yml(tmp_path, body))
    msg = str(exc.value)
    assert "declares no" in msg
    assert "canary-changes" in msg


def test_signoff_wiring_rejects_an_exit_that_is_only_mentioned(tmp_path):
    # `echo "exit 1"` mentions a non-zero exit without performing one; the
    # script check wants a line that IS `exit <n>`.
    body = FLOOR_YML_WIRED.replace(
        "        if: needs.signoff.result == 'failure'\n        run: exit 1\n",
        "        if: needs.signoff.result == 'failure'\n"
        "        run: echo \"would exit 1\"\n",
    )
    with pytest.raises(floor_anchor.AnchorError) as exc:
        floor_anchor.check_workflow_wiring(_floor_yml(tmp_path, body))
    assert "never exits non-zero" in str(exc.value)


@pytest.mark.parametrize(
    "where, anchor, line",
    [
        # the enforcement job: a conversion step's exit 1 stops failing it
        ("step", "        if: needs.signoff.result == 'failure'\n",
         "        continue-on-error: true\n"),
        ("floor", "    if: ${{ !cancelled() }}\n", "    continue-on-error: true\n"),
        # the jobs it reads: their result arrives as 'success' after a failure
        ("signoff", "    environment: floor-signoff\n", "    continue-on-error: true\n"),
        ("canary-changes", "    name: canary path filter\n",
         "    continue-on-error: true\n"),
    ],
)
def test_signoff_wiring_fails_closed_on_continue_on_error(
    tmp_path, where, anchor, line
):
    # Disarm path 16: every guard and script is byte-identical, but a
    # `continue-on-error: true` -- on a conversion step, on the enforcement
    # job, or on a job whose result it reads -- turns a failure into a pass.
    body = FLOOR_YML_WIRED.replace(anchor, anchor + line, 1)
    with pytest.raises(floor_anchor.AnchorError) as exc:
        floor_anchor.check_workflow_wiring(_floor_yml(tmp_path, body))
    msg = str(exc.value)
    assert "continue-on-error" in msg
    if where not in ("step", "floor"):
        assert f"job '{where}'" in msg


def test_signoff_wiring_fails_closed_on_a_decoy_filter_job(tmp_path):
    # Disarm path 17: the sign-off stays keyed on the real filter, but the
    # never-requested step reads a second job that always answers false, so
    # the step never fires. Both consumers must read the SAME producer.
    body = FLOOR_YML_WIRED.replace(
        "    needs: [signoff, canary-changes]\n",
        "    needs: [signoff, canary-changes, decoy]\n",
    ).replace(
        "        if: needs.canary-changes.outputs.floor_core_changed == 'true'"
        " && needs.signoff.result != 'success'\n",
        "        if: needs.decoy.outputs.floor_core_changed == 'true'"
        " && needs.signoff.result != 'success'\n",
    ) + (
        "\n  decoy:\n    runs-on: ubuntu-latest\n    outputs:\n"
        "      floor_core_changed: ${{ steps.f.outputs.floor_core_changed }}\n"
        "    steps:\n      - id: f\n"
        '        run: echo "floor_core_changed=false" >> "$GITHUB_OUTPUT"\n'
    )
    with pytest.raises(floor_anchor.AnchorError) as exc:
        floor_anchor.check_workflow_wiring(_floor_yml(tmp_path, body))
    msg = str(exc.value)
    assert "needs.canary-changes.outputs.floor_core_changed" in msg


def test_signoff_wiring_reads_the_output_only_under_outputs(tmp_path):
    # A six-space `floor_core_changed:` line elsewhere in the filter job (an
    # `env:` entry, say) is not a declared output.
    body = FLOOR_YML_WIRED.replace(
        "    outputs:\n"
        "      floor_core_changed: ${{ steps.filter.outputs.floor_core_changed }}\n",
        "    env:\n      floor_core_changed: true\n",
    )
    with pytest.raises(floor_anchor.AnchorError) as exc:
        floor_anchor.check_workflow_wiring(_floor_yml(tmp_path, body))
    assert "declares no" in str(exc.value)


def test_signoff_wiring_fails_closed_on_a_literal_output(tmp_path):
    # Disarm path 18: the filter job keeps declaring floor_core_changed, but
    # as a literal rather than a forwarded step output, so every PR is
    # answered 'false' without the script ever being asked.
    body = FLOOR_YML_WIRED.replace(
        "      floor_core_changed: ${{ steps.filter.outputs.floor_core_changed }}\n",
        "      floor_core_changed: 'false'\n",
    )
    with pytest.raises(floor_anchor.AnchorError) as exc:
        floor_anchor.check_workflow_wiring(_floor_yml(tmp_path, body))
    assert "steps.<id>.outputs" in str(exc.value)


@pytest.mark.parametrize(
    "old, new",
    [
        # the producing step no longer runs the classifier
        (
            '          FLOOR_CORE="$(python scripts/floor_check.py protected \\\n'
            '            --base "${BASE_SHA}" --changed --role floor-core < "${CHANGED}")"\n',
            "",
        ),
        # the classifier is asked for a different role
        ("--role floor-core", "--role canary"),
        # the output forwards a step that does not exist
        ("steps.filter.outputs.floor_core_changed", "steps.other.outputs.floor_core_changed"),
    ],
)
def test_signoff_wiring_fails_closed_when_the_producer_skips_the_classifier(
    tmp_path, old, new
):
    # Disarm path 19: the output is forwarded from a step, but that step does
    # not run `floor_check.py protected --role floor-core`, so the answer is
    # the step's own, not the script's classification.
    assert old in FLOOR_YML_WIRED
    body = FLOOR_YML_WIRED.replace(old, new, 1)
    with pytest.raises(floor_anchor.AnchorError) as exc:
        floor_anchor.check_workflow_wiring(_floor_yml(tmp_path, body))
    assert "floor_check.py protected" in str(exc.value)


def test_signoff_wiring_tolerates_a_trailing_comment_on_a_guard(tmp_path, capsys):
    body = FLOOR_YML_WIRED.replace(
        "    if: ${{ !cancelled() }}\n",
        "    if: ${{ !cancelled() }}  # never skip: a skipped required context reads as satisfied\n",
    ).replace(
        "    if: needs.canary-changes.outputs.floor_core_changed == 'true'\n",
        "    if: needs.canary-changes.outputs.floor_core_changed == 'true' # clause iii\n",
    )
    floor_anchor.check_workflow_wiring(_floor_yml(tmp_path, body))
    assert "ok   " in capsys.readouterr().out


def test_signoff_wiring_reads_a_four_space_step_list(tmp_path, capsys):
    # `steps:` items may sit at four spaces (flush with the key) or six; both
    # are the same list to YAML, so the step reader must accept both.
    start = FLOOR_YML_WIRED.index("    steps:\n") + len("    steps:\n")
    end = FLOOR_YML_WIRED.index("\n  signoff:")
    block = FLOOR_YML_WIRED[start:end]
    reindented = "\n".join(line[2:] if line.startswith("  ") else line for line in block.split("\n"))
    body = FLOOR_YML_WIRED[:start] + reindented + FLOOR_YML_WIRED[end:]
    floor_anchor.check_workflow_wiring(_floor_yml(tmp_path, body))
    assert "ok   " in capsys.readouterr().out


def test_signoff_wiring_accepts_an_explicit_continue_on_error_false(tmp_path, capsys):
    # `continue-on-error: false` is the default spelled out; only a value
    # that could make a failure non-fatal is rejected.
    anchor = "    if: ${{ !cancelled() }}\n"
    body = FLOOR_YML_WIRED.replace(anchor, anchor + "    continue-on-error: false\n", 1)
    floor_anchor.check_workflow_wiring(_floor_yml(tmp_path, body))
    assert "ok   " in capsys.readouterr().out
