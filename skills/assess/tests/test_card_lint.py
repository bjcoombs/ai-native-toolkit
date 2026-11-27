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


def test_done_when_warnings_lints_each_sub_item() -> None:
    """A sub-item's done_when gets the entry's rule, named by its parent rank and label (#1518)."""
    entry = {"rank": 2, "action": "Split the module", "done_when": COMMAND, "sub_items": [
        {"label": "a", "action": "Move the parser", "done_when": PROSE},
        {"label": "b", "action": "Move the writer", "done_when": COMMAND},
        "not a sub-item", {"label": "c", "action": "No exit"},
    ]}
    [warning] = done_when_warnings([entry])
    assert "rank 2 sub-item 'a'" in warning and "Move the parser" in warning and PROSE in warning


def _finalize_sub_items(assess_dir: Path, sub_items: list[dict[str, str]]) -> None:
    _seed_log_md(assess_dir)
    _seed_run_context(assess_dir)
    action = {**_good_action(rank=1), "sub_items": sub_items}
    (assess_dir / "finalize-input.json").write_text(
        json.dumps({**_base_input(), "actions": [action]}), encoding="utf-8")
    finalize_run(assess_dir=assess_dir)


def test_finalize_warns_on_a_prose_only_sub_item_done_when(
    tmp_assess_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _finalize_sub_items(tmp_assess_dir, [{"label": "a", "action": "Move the parser", "done_when": PROSE}])
    err = capsys.readouterr().err
    assert "finalize: warning: action rank 1 sub-item 'a'" in err
    assert "Move the parser" in err and PROSE in err
    contract = json.loads((tmp_assess_dir / "actions.json").read_text(encoding="utf-8"))
    assert contract["actions"][0]["sub_items"][0]["done_when"] == PROSE


def test_finalize_skips_a_sub_item_with_a_blank_action(
    tmp_assess_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A whitespace-only sub-item action is dropped with a warning naming rank and label, never linted."""
    _finalize_sub_items(tmp_assess_dir, [
        {"label": "a", "action": "  \n\t", "done_when": PROSE},
        {"label": "b", "action": "Move the writer", "done_when": COMMAND},
    ])
    err = capsys.readouterr().err
    assert "rank 1 dropping sub-item 'a' with an empty action" in err
    assert PROSE not in err
    contract = json.loads((tmp_assess_dir / "actions.json").read_text(encoding="utf-8"))
    assert [s["label"] for s in contract["actions"][0]["sub_items"]] == ["b"]
