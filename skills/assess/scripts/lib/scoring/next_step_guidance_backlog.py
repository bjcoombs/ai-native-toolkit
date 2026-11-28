"""Repository-native wording for the backlog-ready criteria (#1324), read by ``next_step_guidance``.

The backlog-ready row (``levels/default.toml``) reads seven boolean signals of the
``backlog_ready`` block (``lib/backlog_ready.py``, ``lib/backlog_ready_outcomes.py``). Each is a
``eq true`` flag, so ``BACKLOG_FLAGS`` holds one ``(change, done_when)`` pair per signal and
``lib.scoring.next_step_guidance`` wraps each in its flag wording. ``BACKLOG_HINT`` is the
``backlog_ready`` block's measure hint: why level 2 can read not measured. It sits apart from
``next_step_guidance.py`` to keep that file under its size ceiling, and imports nothing.
Tests: ``tests/test_fleet_next_steps.py`` (every default signal has wording).
"""
from __future__ import annotations

BACKLOG_FLAGS: dict[str, tuple[str, str]] = {
    "backlog_ready.required_form_field": (
        "Commit an issue form under `.github/ISSUE_TEMPLATE/` with at least one `body` field whose "
        "`validations` sets `required: true`, or a GitLab issue template under "
        "`.gitlab/issue_templates/` with the six definition-of-ready headings.",
        "Opening a new issue shows a form that will not submit with a required field empty, or the "
        "GitLab template holds the six headings."),
    "backlog_ready.untriaged_on_open": (
        "Add a workflow on `issues: types: [opened]` that adds an untriaged label "
        "(`gh issue edit --add-label`, or a labeler action); on GitLab, include the refinement "
        "template or schedule a job that adds one.",
        "A new test issue gets the untriaged label within a minute of opening, with no person "
        "adding it."),
    "backlog_ready.ready_label_documented": (
        "Document the ready label in a Markdown file such as `CONTRIBUTING.md`, the label in "
        "backticks, with a list of the criteria an issue must meet under it.",
        "A committed Markdown file names the ready label in backticks with a list of its criteria "
        "beneath."),
    "backlog_ready.stale_job_safe": (
        "Set `exempt-issue-labels` on each `actions/stale` step to the ready label, or "
        "`days-before-issue-close: -1`, so a ready issue is never closed for age.",
        "No `actions/stale` step in `.github/workflows/` can close an issue carrying the ready "
        "label."),
    "backlog_ready.first_response_met": (
        "Answer, label or close new issues within `first_response_max_days` (default 2), counting "
        "only people other than the author and not bots; the threshold is set in `[backlog_ready]` "
        "in `.assess/config.toml`.",
        "Over the last `window_days` (default 90), the median wait for a first response from "
        "someone other than the author, unanswered issues included, is within the threshold."),
    "backlog_ready.untriaged_met": (
        "Triage open issues older than 7 days: give each a label other than the untriaged one, or "
        "close it.",
        "At most `untriaged_max_share` (default 10%) of the open issues older than 7 days carry no "
        "label or an untriaged label."),
    "backlog_ready.close_open_met": (
        "Close, merge into another or decline issues at least as fast as they open, so the backlog "
        "an agent draws from does not grow without bound.",
        "Over the last `window_days` (default 90), issues closed divided by issues opened is at "
        "least `close_open_min_ratio` (default 0.9)."),
}

BACKLOG_HINT = (
    "Level 1 is read from committed files by every run. Level 2 reads the issue tracker: a "
    "headless fleet scan makes no network calls, so it is not measured (`no_network`) without "
    "`--platform`; `no_platform_auth` means gh or glab is signed out or refused the issues, and "
    "`unsupported_host` a remote that is not GitHub or a GitLab glab reaches. A read that stopped "
    "at its page cap leaves the ratio not measured (`window_truncated`; set a shorter "
    "`window_days` in `[backlog_ready]`) or the untriaged share (`open_truncated`).")
