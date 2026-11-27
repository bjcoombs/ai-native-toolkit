"""A not-planned close counts as a decline, and declined 6b file and reopen offers are recorded (#1543)."""
from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

import assess_actions_render as render
from lib.report import backlog_items as bi
from lib.run.decline_markers import PROPOSAL_DECLINES, read_not_planned, read_proposal_declines

MODEL = "default-2026.11.24"
NEW_MODEL = "default-2026.12.01"
HEAD = "a" * 40
LINT = "layer-3-lint.1"
CI = "layer-5-ci.1"
DOCS = "layer-1-docs.1"
CLOSED_AT = "2026-11-20T10:00:00Z"


def _item(number: int, condition: str, state: str, reason: str | None = None,
          closed_at: str | None = None, title: str = "") -> dict[str, object]:
    body = f"**Condition:** `{condition}`\n\n{bi.condition_marker(condition, MODEL)}\n"
    item: dict[str, object] = {"number": number, "title": title or f"Item {number}", "body": body, "state": state,
                               "labels": [{"name": bi.DEFAULT_LABEL}]}
    if reason is not None:
        item["stateReason"] = reason
    if closed_at is not None:
        item["closedAt"] = closed_at
    return item


def _condition(cid: str, state: str) -> dict[str, object]:
    row, _, level = cid.rpartition(".")
    return {"id": cid, "row": row, "level": int(level), "grade": "verified", "counted": True, "state": state}


def _setup(tmp_path: Path, items: list[dict[str, object]], *conditions: Mapping[str, object],
           model: str = MODEL, actions: list[dict[str, object]] | None = None) -> Path:
    assess = tmp_path / ".assess"
    assess.mkdir(exist_ok=True)
    ctx = {"interactive": True, "level": {"levels_version": model, "conditions": list(conditions)},
           "measured_commit": {"head_sha": HEAD, "dirty": False},
           "since_last_run": {"previous_commit": "b" * 40},
           "prior_level": {"prior_level_version": 3, "levels_version": model, "tags": []}}
    (assess / "run-context.json").write_text(json.dumps(ctx), encoding="utf-8")
    if actions is not None:
        (assess / "actions.json").write_text(json.dumps({"schema": 2, "actions": actions}), encoding="utf-8")
    existing = tmp_path / "existing.json"
    existing.write_text(json.dumps(items), encoding="utf-8")
    return existing


def _reconcile(tmp_path: Path, existing: Path, capsys: pytest.CaptureFixture[str]) -> dict[str, Any]:
    assert render.main([str(tmp_path), "reconcile", "--existing", str(existing)]) == 0
    out: dict[str, Any] = json.loads(capsys.readouterr().out)
    return out


def _plan(tmp_path: Path, existing: Path, capsys: pytest.CaptureFixture[str], *extra: str) -> list[dict[str, Any]]:
    assert render.main([str(tmp_path), "plan", "--existing", str(existing), *extra]) == 0
    out: list[dict[str, Any]] = json.loads(capsys.readouterr().out)
    return out


def _numbers(out: dict[str, Any]) -> list[int]:
    return [p["number"] for p in out["proposals"]]


# ── a not-planned close counts as a decline ─────────────────────────────────

def test_a_not_planned_close_gets_no_reopen_until_the_model_changes(
        tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    existing = _setup(tmp_path, [_item(5, LINT, "CLOSED", "NOT_PLANNED", CLOSED_AT, title="Pin the linter")],
                      _condition(LINT, "unmet"))
    out = _reconcile(tmp_path, existing, capsys)
    assert out["proposals"] == [] and out["next_up"] == []
    [line] = out["declined"]
    assert line.startswith(f"reopen #5 (`{LINT}`) declined by an unknown user on 2026-11-20 under model {MODEL}")
    assert "reason: closed as not planned" in line
    assert "Declined before" in out["summary"]
    assert _numbers(_reconcile(tmp_path, existing, capsys)) == []  # the next run still holds it
    _setup(tmp_path, json.loads(existing.read_text(encoding="utf-8")), _condition(LINT, "unmet"), model=NEW_MODEL)
    out = _reconcile(tmp_path, existing, capsys)
    assert [(p["kind"], p["number"]) for p in out["proposals"]] == [("reopen", 5)]
    assert out["declined"] == []
    assert _numbers(_reconcile(tmp_path, existing, capsys)) == [5]  # the close is not read as a decline again


def test_a_state_change_brings_the_reopen_back(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    items = [_item(5, LINT, "CLOSED", "NOT_PLANNED", CLOSED_AT)]
    existing = _setup(tmp_path, items, _condition(LINT, "unmet"))
    assert _numbers(_reconcile(tmp_path, existing, capsys)) == []
    _setup(tmp_path, items, _condition(LINT, "met"))  # held: nothing to reopen, and the decline lapses
    assert _numbers(_reconcile(tmp_path, existing, capsys)) == []
    _setup(tmp_path, items, _condition(LINT, "unmet"))
    assert _numbers(_reconcile(tmp_path, existing, capsys)) == [5]


def test_a_second_not_planned_close_is_a_new_decline(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    existing = _setup(tmp_path, [_item(5, LINT, "CLOSED", "NOT_PLANNED", CLOSED_AT)], _condition(LINT, "unmet"))
    _reconcile(tmp_path, existing, capsys)
    _setup(tmp_path, [_item(5, LINT, "CLOSED", "NOT_PLANNED", "2026-11-26T09:00:00Z")], _condition(LINT, "unmet"),
           model=NEW_MODEL)
    out = _reconcile(tmp_path, existing, capsys)
    assert out["proposals"] == [] and "on 2026-11-26" in out["declined"][0]
    assert [c["closed_at"] for c in read_not_planned(tmp_path / ".assess")] == ["2026-11-26T09:00:00Z"]


@pytest.mark.parametrize("item", [
    _item(5, LINT, "CLOSED", "COMPLETED", CLOSED_AT),
    _item(5, LINT, "closed"),  # GitLab: no stateReason, so no decline is inferred
])
def test_a_completed_or_gitlab_close_is_reopened(
        tmp_path: Path, capsys: pytest.CaptureFixture[str], item: dict[str, object]) -> None:
    existing = _setup(tmp_path, [item], _condition(LINT, "unmet"))
    assert _numbers(_reconcile(tmp_path, existing, capsys)) == [5]
    assert not (tmp_path / ".assess" / PROPOSAL_DECLINES).exists()


def test_a_not_planned_reopen_keeps_no_wip_slot(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    items = [_item(1, DOCS, "OPEN"), _item(2, "layer-2-open.1", "OPEN"),
             _item(5, LINT, "CLOSED", "NOT_PLANNED", CLOSED_AT), _item(6, CI, "CLOSED", "COMPLETED", CLOSED_AT)]
    existing = _setup(tmp_path, items, _condition(DOCS, "unmet"), _condition("layer-2-open.1", "unmet"),
                      _condition(LINT, "unmet"), _condition(CI, "unmet"))
    out = _reconcile(tmp_path, existing, capsys)  # wip 3, two open: one slot, and #5 does not take it
    assert _numbers(out) == [6] and out["next_up"] == [] and len(out["declined"]) == 1


# ── declined 6b file and reopen offers are recorded ─────────────────────────

ACTIONS = [{"rank": n, "action": f"Action {n}", "condition": cid, "done_when": "done", "scope_fence": "fence"}
           for n, cid in ((1, LINT), (2, CI), (3, DOCS))]
CONDITIONS = (_condition(LINT, "unmet"), _condition(CI, "unmet"), _condition(DOCS, "unmet"))


def _decisions(planned: list[dict[str, Any]]) -> list[str]:
    return [p["wip"]["decision"] for p in planned]


def test_a_declined_file_offer_is_not_offered_again_while_the_evidence_holds(
        tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / ".assess").mkdir()
    (tmp_path / ".assess" / "config.toml").write_text("[backlog]\nwip = 2\n", encoding="utf-8")
    existing = _setup(tmp_path, [], *CONDITIONS, actions=ACTIONS)
    assert _decisions(_plan(tmp_path, existing, capsys)) == ["file", "file", "next_up"]
    planned = _plan(tmp_path, existing, capsys, "--decline", "1", "--declined-by", "ben",
                    "--declined-at", "2026-11-27", "--decline-reason", "1=not this quarter")
    assert _decisions(planned) == ["declined", "file", "file"]  # the declined offer frees its slot
    assert planned[0]["declined"].startswith(f"file (`{LINT}`) declined by ben on 2026-11-27 under model {MODEL}")
    assert "reason: not this quarter" in planned[0]["declined"]
    [record] = read_proposal_declines(tmp_path / ".assess")
    assert (record["kind"], record["number"], record["condition"], record["state"]) == ("file", 0, LINT, "unmet")
    assert _decisions(_plan(tmp_path, existing, capsys)) == ["declined", "file", "file"]
    assert render.main([str(tmp_path), "next-up", "--existing", str(existing)]) == 0
    assert capsys.readouterr().out == ""
    _setup(tmp_path, [], *CONDITIONS, model=NEW_MODEL)
    assert _decisions(_plan(tmp_path, existing, capsys)) == ["file", "file", "next_up"]


def test_a_declined_reopen_offer_also_holds_the_reconcile_reopen(
        tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    existing = _setup(tmp_path, [_item(9, LINT, "CLOSED", "COMPLETED", CLOSED_AT, title="Action 1")], *CONDITIONS,
                      actions=ACTIONS[:1])
    [item] = _plan(tmp_path, existing, capsys)
    assert item["filing"] == {"action": "reuse", "number": 9, "state": "CLOSED"} and item["wip"]["decision"] == "file"
    [item] = _plan(tmp_path, existing, capsys, "--decline", "1", "--declined-by", "ben", "--declined-at", "2026-11-27")
    assert item["wip"]["decision"] == "declined" and item["declined"].startswith(f"reopen #9 (`{LINT}`) declined by ben")
    out = _reconcile(tmp_path, existing, capsys)
    assert out["proposals"] == [] and len(out["declined"]) == 1


def test_a_not_planned_closed_match_is_not_offered_for_reopening(
        tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    existing = _setup(tmp_path, [_item(9, LINT, "CLOSED", "NOT_PLANNED", CLOSED_AT, title="Action 1")], *CONDITIONS,
                      actions=ACTIONS)
    planned = _plan(tmp_path, existing, capsys)
    assert _decisions(planned) == ["declined", "file", "file"]
    assert "reason: closed as not planned" in planned[0]["declined"]


def test_decline_names_only_an_offered_item(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    existing = _setup(tmp_path, [_item(9, LINT, "OPEN", title="Action 1")], *CONDITIONS, actions=ACTIONS)
    assert render.main([str(tmp_path), "plan", "--existing", str(existing), "--decline", "1"]) == 1
    assert "1 is not offered" in capsys.readouterr().err
    assert render.main([str(tmp_path), "plan", "--existing", str(existing), "--decline", "7"]) == 1
    assert "names no item 7" in capsys.readouterr().err
    assert not (tmp_path / ".assess" / PROPOSAL_DECLINES).exists()


@pytest.mark.parametrize("flags", [["--declined-by", "ben"], ["--declined-at", "2026-11-27"],
                                   ["--decline-reason", "1=not yet"]])
def test_plan_decline_flags_without_decline_fail_at_parsing(
        tmp_path: Path, capsys: pytest.CaptureFixture[str], flags: list[str]) -> None:
    existing = _setup(tmp_path, [], *CONDITIONS, actions=ACTIONS)
    with pytest.raises(SystemExit) as exc:
        render.main([str(tmp_path), "plan", "--existing", str(existing), *flags])
    assert exc.value.code == 2
    assert "needs --decline" in capsys.readouterr().err
