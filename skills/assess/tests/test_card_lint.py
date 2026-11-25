"""The card's evidence rule, shared by the construction test and finalize (#1300)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from assess_finalize import finalize_run
from lib.card_lint import carries_evidence, done_when_warnings, lint_errors
from test_assess_finalize import _base_input, _good_action, _seed_log_md, _seed_run_context

PROSE = "The coupling is documented and the shared state is extracted"
COMMAND = "`golangci-lint run` passes with the rule active"


@pytest.mark.parametrize("text, ok", [
    (PROSE, False), (COMMAND, True), ("Coverage reaches 80 percent", True),
    ("The [guide](docs/guide.md) names the check", True), ("", False),
])
def test_carries_evidence_needs_a_digit_link_or_backticked_span(text: str, ok: bool) -> None:
    assert carries_evidence(text) is ok


def test_lint_errors_keeps_the_failing_paragraphs_in_order() -> None:
    assert lint_errors(["a", "Step 2", "b"]) == ["a", "b"]


def test_done_when_warnings_names_each_prose_only_action() -> None:
    actions: list[object] = [
        {"rank": 1, "action": "Document the coupling", "done_when": PROSE},
        {"rank": 2, "action": "Add the lint", "done_when": COMMAND},
        "not an action", {"rank": 3, "action": "No exit"},
    ]
    [warning] = done_when_warnings(actions)
    assert "rank 1" in warning and "Document the coupling" in warning and PROSE in warning


def _finalize(assess_dir: Path, done_when: str) -> None:
    _seed_log_md(assess_dir)
    _seed_run_context(assess_dir)
    action = {**_good_action(rank=1), "done_when": done_when}
    (assess_dir / "finalize-input.json").write_text(
        json.dumps({**_base_input(), "actions": [action]}), encoding="utf-8")
    finalize_run(assess_dir=assess_dir)


def test_finalize_warns_on_a_prose_only_done_when(
    tmp_assess_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _finalize(tmp_assess_dir, PROSE)
    err = capsys.readouterr().err
    assert "finalize: warning: action rank 1" in err
    assert "Add cyclop rule" in err and PROSE in err
    # A warning, not a refusal: the contract is still written.
    contract = json.loads((tmp_assess_dir / "actions.json").read_text(encoding="utf-8"))
    assert contract["actions"][0]["done_when"] == PROSE


def test_finalize_is_quiet_on_a_backticked_command(
    tmp_assess_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _finalize(tmp_assess_dir, COMMAND)
    assert "done_when" not in capsys.readouterr().err


def test_finalize_skips_an_entry_it_drops(
    tmp_assess_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A malformed entry is dropped with its own warning and never linted."""
    _seed_log_md(tmp_assess_dir)
    _seed_run_context(tmp_assess_dir)
    dropped = {k: v for k, v in {**_good_action(rank=2), "done_when": PROSE}.items()
               if k != "scope_fence"}
    (tmp_assess_dir / "finalize-input.json").write_text(
        json.dumps({**_base_input(), "actions": [_good_action(rank=1), dropped]}),
        encoding="utf-8")
    finalize_run(assess_dir=tmp_assess_dir)
    err = capsys.readouterr().err
    assert "dropping malformed entry" in err and PROSE not in err
