"""Propose-only reconcile of filed backlog items (#811): close, reopen, rules changed, demote, superseded."""
from __future__ import annotations

import copy
import json
from collections.abc import Mapping
from pathlib import Path

import pytest

import assess_actions_render as render
from lib.report import backlog_items as bi
from lib.report import backlog_reconcile as br
from lib.report import backlog_schedule as bs

MODEL = "default-2026.11.24"
OLD_MODEL = "default-2026.11.01"
HEAD = "a" * 40
LABELS = [{"name": bi.DEFAULT_LABEL}]


def _marked(number: int, condition: str, state: str, model: str = MODEL, part: int = 0,
            title: str = "") -> dict[str, object]:
    body = f"**Condition:** `{condition}`\n\n{bi.condition_marker(condition, model, part=part)}\n"
    return {"number": number, "title": title or f"Item {number}", "body": body, "state": state, "labels": LABELS}


def _condition(cid: str, state: str, **extra: object) -> dict[str, object]:
    row, _, level = cid.rpartition(".")
    return {"id": cid, "row": row, "level": int(level), "grade": "verified", "counted": True,
            "state": state, **extra}


def _context(*conditions: Mapping[str, object], model: str = MODEL, previous_model: str = MODEL,
             previous_commit: str = "b" * 40, dirty: bool = False, interactive: bool = True) -> dict[str, object]:
    return {
        "interactive": interactive,
        "level": {"levels_version": model, "conditions": list(conditions)},
        "measured_commit": {"head_sha": HEAD, "dirty": dirty},
        "since_last_run": {"previous_commit": previous_commit},
        "prior_level": {"prior_level_version": 3, "levels_version": previous_model, "tags": []},
    }


# The fixture tracker: one item per case.
TRACKER: list[dict[str, object]] = [
    _marked(1, "layer-5-ci.1", "OPEN", title="Run the tests in CI"),  # condition now holds
    _marked(2, "layer-3-lint.1", "CLOSED", title="Pin the linter"),  # condition lapsed
    _marked(3, "layer-4-docs.2", "OPEN", title="Write the docs index"),  # still unmet: nothing
    {"number": 4, "title": "Review the co-committed tests", "state": "OPEN", "labels": LABELS,
     "body": "**Finding:** `self_referential_tests` in `src/a.py`\n"},
]
CONDITIONS = (
    _condition("layer-5-ci.1", "met", why="CI runs the suite on every push"),
    _condition("layer-3-lint.1", "unmet", reason="requires_unmet"),
    _condition("layer-4-docs.2", "unmet"),
)


class FixtureTracker:
    """A tracker that applies reconcile writes, so a test can see what a confirmation changed."""

    def __init__(self, items: list[dict[str, object]]) -> None:
        self.items = copy.deepcopy(items)

    def apply(self, writes: list[br.ReconcileWrite]) -> None:
        for w in writes:
            item = next(i for i in self.items if i["number"] == w["number"])
            if w["op"] == "close":
                item["state"] = "CLOSED"
            elif w["op"] == "reopen":
                item["state"] = "OPEN"
            else:
                item["labels"] = [*LABELS, {"name": w["add_label"]}]


def _run(items: list[dict[str, object]], ctx: Mapping[str, object], **kw: object) -> br.ReconcileResult:
    return br.reconcile(items, br.run_facts(ctx), **kw)  # type: ignore[arg-type]


def _by_number(result: br.ReconcileResult) -> dict[int, br.Proposal]:
    return {p["number"]: p for p in result["proposals"]}


def test_close_and_reopen_proposals_cite_evidence_commit_and_cause() -> None:
    result = _run(TRACKER, _context(*CONDITIONS))
    props = _by_number(result)
    close, reopen = props[1], props[2]
    assert close["kind"] == "close" and close["condition"] == "layer-5-ci.1"
    assert close["reason"] == "`layer-5-ci.1` reads met (verified) at aaaaaaaaaaaa: CI runs the suite on every push"
    assert close["commit"] == HEAD and close["write"]["op"] == "close"
    assert HEAD in close["write"]["comment"]
    assert reopen["kind"] == "reopen" and reopen["commit"] == HEAD
    assert reopen["reason"] == "`layer-3-lint.1` reads unmet (verified) at aaaaaaaaaaaa (requires_unmet)"
    assert 3 not in props
    assert "Close #1 (Run the tests in CI)" in result["summary"]
    assert "Reopen #2 (Pin the linter)" in result["summary"]


def test_nothing_is_written_until_confirmation_and_only_confirmed_writes_apply() -> None:
    before = copy.deepcopy(TRACKER)
    tracker = FixtureTracker(TRACKER)
    result = _run(tracker.items, _context(*CONDITIONS))
    assert tracker.items == before  # proposing changed nothing
    assert len(result["proposals"]) == 3
    tracker.apply(br.confirmed_writes(result["proposals"], {1}))
    states = {i["number"]: i["state"] for i in tracker.items}
    assert states == {1: "CLOSED", 2: "CLOSED", 3: "OPEN", 4: "OPEN"}
    assert tracker.items[1:] == before[1:]
    assert br.confirmed_writes(result["proposals"], set()) == []


def test_a_recount_only_change_reads_rules_changed_and_proposes_nothing() -> None:
    old = [_marked(1, "layer-5-ci.1", "OPEN", model=OLD_MODEL), _marked(2, "layer-3-lint.1", "CLOSED", model=OLD_MODEL)]
    ctx = _context(*CONDITIONS, previous_model=OLD_MODEL, previous_commit=HEAD)
    result = _run(old, ctx)
    assert result["proposals"] == []
    lines = [r["line"] for r in result["rules_changed"]]
    assert lines == [f"rules changed: your repo did not ({OLD_MODEL} -> {MODEL})"] * 2
    assert "rules changed: your repo did not" in result["summary"]
    assert "Close" not in result["summary"] and "Reopen" not in result["summary"]


def test_a_model_change_on_a_changed_repo_is_proposed_with_the_model_named() -> None:
    old = [_marked(1, "layer-5-ci.1", "OPEN", model=OLD_MODEL)]
    result = _run(old, _context(*CONDITIONS, previous_model=OLD_MODEL))  # the commit moved too
    (close,) = result["proposals"]
    assert close["kind"] == "close" and close["reason"].endswith(f"the model also changed ({OLD_MODEL} -> {MODEL})")
    assert result["rules_changed"] == []


def test_demote_proposal_for_self_referential_tests() -> None:
    result = _run(TRACKER, _context(*CONDITIONS))
    demote = _by_number(result)[4]
    assert demote["kind"] == "demote" and demote["finding"] == "self_referential_tests"
    assert demote["write"] == {"op": "label", "number": 4, "add_label": br.INFORMATIONAL_LABEL,
                               "comment": demote["write"]["comment"]}
    assert "Demote to informational #4" in result["summary"]
    tracker = FixtureTracker(TRACKER)
    tracker.apply(br.confirmed_writes(result["proposals"], {4}))
    assert br.INFORMATIONAL_LABEL in bi.item_labels(tracker.items[3])
    assert 4 not in _by_number(_run(tracker.items, _context(*CONDITIONS)))  # demoted once


def test_a_finding_item_filed_this_run_is_not_demoted() -> None:
    props = _by_number(_run(TRACKER, _context(*CONDITIONS), filed={4}))
    assert 4 not in props and props[1]["kind"] == "close"


def test_a_why_ending_in_a_full_stop_reads_one_stop() -> None:
    cond = _condition("layer-5-ci.1", "met", why="pytest runs in CI.")
    (close,) = _run([_marked(1, "layer-5-ci.1", "OPEN")], _context(cond))["proposals"]
    assert close["reason"].endswith("pytest runs in CI") and "CI.." not in close["write"]["comment"]
    assert "CI.." not in br.summary([close], [], [])


def test_a_not_applicable_condition_is_demoted() -> None:
    items = [_marked(2, "layer-4-docs.2", "OPEN"), _marked(3, "layer-4-docs.2", "CLOSED")]
    props = _by_number(_run(items, _context(_condition("layer-4-docs.2", "not_applicable"))))
    assert props[2]["kind"] == "demote" and "reads not applicable" in props[2]["reason"]
    assert 3 not in props  # a closed item is left as it is


def test_a_condition_a_profile_run_does_not_list_is_left_alone() -> None:
    # A profile cuts ci_gates to level 1, so ci_gates.3 is missing from the run, not from the model.
    items = [_marked(1, "ci_gates.3", "OPEN"), _marked(2, "ci_gates.3", "CLOSED")]
    result = _run(items, _context(_condition("ci_gates.1", "met"), model="profile-citizen-2026.11.24"))
    assert result["proposals"] == []
    assert "2 item(s) left as they are" in result["notes"][0]


def test_hazard_polarity_reads_unmet_as_holding() -> None:
    items = [_marked(1, "hazard-row.1", "OPEN")]
    assert _run(items, _context(_condition("hazard-row.1", "unmet", polarity="hazard")))["proposals"][0]["kind"] == "close"
    assert _run(items, _context(_condition("hazard-row.1", "met", polarity="hazard")))["proposals"] == []


@pytest.mark.parametrize("state", ["not_measured", "declined"])
def test_unknown_or_declined_readings_propose_nothing(state: str) -> None:
    items = [_marked(1, "layer-5-ci.1", "OPEN"), _marked(2, "layer-5-ci.1", "CLOSED")]
    assert _run(items, _context(_condition("layer-5-ci.1", state)))["proposals"] == []


def test_a_dirty_tree_holds_close_and_reopen() -> None:
    result = _run(TRACKER, _context(*CONDITIONS, dirty=True))
    assert [p["kind"] for p in result["proposals"]] == ["demote"]
    assert "2 close or reopen proposal(s) held" in result["notes"][0]


def test_a_non_interactive_run_proposes_nothing() -> None:
    result = _run(TRACKER, _context(*CONDITIONS, interactive=False))
    assert result["proposals"] == [] and "non-interactive" in result["notes"][0]


def test_items_without_the_label_or_a_number_are_passed_over() -> None:
    unlabelled = {**_marked(1, "layer-5-ci.1", "OPEN"), "labels": []}
    assert _run([unlabelled], _context(*CONDITIONS))["proposals"] == []
    assert len(_run([unlabelled], _context(*CONDITIONS), label="")["proposals"]) == 1
    assert _run([{"title": "x", "body": ""}], _context(*CONDITIONS))["proposals"] == []


def test_a_run_without_a_level_result_proposes_only_finding_demotes() -> None:
    result = br.reconcile(TRACKER, br.run_facts({"interactive": True}))
    assert [p["number"] for p in result["proposals"]] == [4]
    assert "no level result" in result["notes"][0]


def test_parts_of_an_action_no_longer_split_are_superseded() -> None:
    # #1524 edge case: run 1 split the action into parts 1 and 2; run 2 files it whole.
    parts = [_marked(1, "layer-4-docs.2", "OPEN", part=1, title="Part one"),
             _marked(2, "layer-4-docs.2", "OPEN", part=2, title="Part two")]
    whole = render.render_items([{"rank": 1, "action": "Write the docs index", "condition": "layer-4-docs.2",
                                  "done_when": "d", "scope_fence": "s"}], model=MODEL)
    (step,) = bi.plan_filing(whole, parts)
    assert step["action"] == "reuse_and_mark" and step["number"] == 1
    assert bi.marker_key(step["marked_body"]) == ("layer-4-docs", 2, "", 0)
    tracker = [{**parts[0], "body": step["marked_body"]}, parts[1]]
    ctx = _context(_condition("layer-4-docs.2", "unmet"))
    (sup,) = _run(tracker, ctx)["proposals"]
    assert sup["kind"] == "supersede" and sup["number"] == 2 and sup["superseded_by"] == 1
    assert sup["write"]["op"] == "close"
    assert _run(tracker, ctx, filed={2})["proposals"] == []  # split again this run: parts kept


def test_a_closed_part_beside_the_whole_item_is_not_reopened() -> None:
    tracker = [_marked(1, "layer-4-docs.2", "OPEN"), _marked(2, "layer-4-docs.2", "CLOSED", part=2),
               _marked(3, "layer-4-docs.2", "CLOSED", part=3)]
    assert _run(tracker, _context(_condition("layer-4-docs.2", "unmet")))["proposals"] == []
    alone = [_marked(2, "layer-4-docs.2", "CLOSED", part=2)]  # no whole item: the part reopens
    assert _run(alone, _context(_condition("layer-4-docs.2", "unmet")))["proposals"][0]["kind"] == "reopen"


def test_a_closed_whole_item_reopens_alone_not_its_closed_parts() -> None:
    # #1530: the whole item and its parts are all closed and the condition lapsed.
    tracker = [_marked(1, "layer-4-docs.2", "CLOSED"), _marked(2, "layer-4-docs.2", "CLOSED", part=2),
               _marked(3, "layer-4-docs.2", "CLOSED", part=3)]
    props = _run(tracker, _context(_condition("layer-4-docs.2", "unmet")))["proposals"]
    assert [(p["kind"], p["number"]) for p in props] == [("reopen", 1)]


def test_an_open_part_beside_a_closed_whole_item_is_not_superseded() -> None:
    tracker = [_marked(1, "layer-4-docs.2", "CLOSED"), _marked(2, "layer-4-docs.2", "OPEN", part=2)]
    props = _run(tracker, _context(_condition("layer-4-docs.2", "unmet")))["proposals"]
    assert [(p["kind"], p["number"]) for p in props] == [("reopen", 1)]


@pytest.mark.parametrize("order", [(0, 1, 2), (1, 0, 2)])
def test_supersession_takes_an_open_whole_item_whatever_the_tracker_order(order: tuple[int, int, int]) -> None:
    # Two whole items can share a key (two Top 3 actions naming one condition); the open one carries the action.
    items = [_marked(5, "layer-4-docs.2", "CLOSED"), _marked(6, "layer-4-docs.2", "OPEN"),
             _marked(7, "layer-4-docs.2", "OPEN", part=2)]
    props = _run([items[i] for i in order], _context(_condition("layer-4-docs.2", "unmet")))["proposals"]
    sup = [p for p in props if p["kind"] == "supersede"]
    assert [(p["number"], p.get("superseded_by")) for p in sup] == [(7, 6)]

def test_an_empty_label_skips_demotes_with_a_note() -> None:
    # #1530: with --label "" the informational label cannot be written, so no demote is proposed.
    items = [TRACKER[3], _marked(2, "layer-4-docs.2", "OPEN")]
    result = _run(items, _context(_condition("layer-4-docs.2", "not_applicable")), label="")
    assert result["proposals"] == []
    assert any("2 demote proposal(s) skipped" in n for n in result["notes"])
    assert "2 demote proposal(s) skipped" in result["summary"]
    again = _run(items, _context(_condition("layer-4-docs.2", "not_applicable")), label="")
    assert again["proposals"] == []  # never proposed, so never proposed again


def test_a_close_wins_over_superseded() -> None:
    tracker = [_marked(1, "layer-5-ci.1", "OPEN"), _marked(2, "layer-5-ci.1", "OPEN", part=2)]
    kinds = {p["number"]: p["kind"] for p in _run(tracker, _context(*CONDITIONS))["proposals"]}
    assert kinds == {1: "close", 2: "close"}


def test_run_facts_reads_a_profile_record_and_missing_blocks() -> None:
    facts = br.run_facts({"level": {"levels_version": "profile-handover-2026.11.24", "conditions": "bad"}})
    assert facts["conditions"] is None and facts["previous_model"] is None and facts["head_sha"] is None
    assert facts["interactive"] is True and facts["dirty"] is None


def _cli_dir(tmp_path: Path, ctx: Mapping[str, object] | None) -> tuple[Path, Path]:
    assess = tmp_path / ".assess"
    assess.mkdir(exist_ok=True)
    if ctx is not None:
        (assess / "run-context.json").write_text(json.dumps(ctx), encoding="utf-8")
    existing = tmp_path / "existing.json"
    existing.write_text(json.dumps(TRACKER), encoding="utf-8")
    return assess, existing


def test_reconcile_subcommand_proposes_then_prints_confirmed_writes(
        tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assess, existing = _cli_dir(tmp_path, _context(*CONDITIONS))
    (assess / "config.toml").write_text("[backlog]\nwip = 4\n", encoding="utf-8")  # room for the reopen (#1542)
    before = existing.read_text(encoding="utf-8")
    assert render.main([str(tmp_path), "reconcile", "--existing", str(existing)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert [p["number"] for p in out["proposals"]] == [1, 2, 4] and out["summary"].startswith("**Reconcile: 3")
    assert render.main([str(tmp_path), "reconcile", "--existing", str(existing), "--confirm", "#2,4"]) == 0
    assert [w["number"] for w in json.loads(capsys.readouterr().out)] == [2, 4]
    assert render.main([str(tmp_path), "reconcile", "--existing", str(existing), "--confirm", "all"]) == 0
    assert len(json.loads(capsys.readouterr().out)) == 3
    assert existing.read_text(encoding="utf-8") == before


def test_reconcile_subcommand_errors(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _, existing = _cli_dir(tmp_path, None)
    assert render.main([str(tmp_path), "reconcile", "--existing", str(existing)]) == 1
    assert "run-context.json" in capsys.readouterr().err
    (tmp_path / ".assess" / "run-context.json").write_text(json.dumps(_context(*CONDITIONS)), encoding="utf-8")
    assert render.main([str(tmp_path), "reconcile", "--existing", str(existing), "--confirm", "x"]) == 1
    assert "item numbers" in capsys.readouterr().err
    existing.write_text("{}", encoding="utf-8")
    assert render.main([str(tmp_path), "reconcile", "--existing", str(existing)]) == 1
    assert "JSON array" in capsys.readouterr().err


# ── the scheduled reconcile (#814) ─────────────────────────────────────────

def _status(cid: int, text: str = "Filed by /assess.") -> dict[str, object]:
    body = bs.new_status_comment(bs.status_line(text, "2026-11-01", None))
    return {"url": f"https://github.com/o/r/issues/1#issuecomment-{cid}", "body": body,
            "authorAssociation": "OWNER", "author": {"login": "owner"}}


def _human(text: str) -> dict[str, object]:
    return {"url": "https://github.com/o/r/issues/1#issuecomment-999", "body": text,
            "authorAssociation": "OWNER", "author": {"login": "owner"}}


def _scheduled_tracker() -> list[dict[str, object]]:
    """The fixture tracker with a status comment and a human comment on each marked item."""
    items = copy.deepcopy(TRACKER)
    for n, item in enumerate(items[:3], start=1):
        item["comments"] = [_human("I will take this."), _status(100 + n)]
        item["assignees"] = [{"login": "dev"}]
    items.append({"number": 5, "title": "Unmarked item", "state": "OPEN", "labels": LABELS,
                  "body": "No marker here.\n", "comments": [_status(105)]})
    return items


class ScheduledTracker:
    """A tracker that applies scheduled writes, so a test can diff what a run changed."""

    def __init__(self, items: list[dict[str, object]]) -> None:
        self.items = items

    def apply(self, writes: list[dict[str, object]]) -> None:
        for w in writes:
            if w["op"] == "create":
                self.items.append({"number": 100 + len(self.items), "title": w["title"], "body": w["body"],
                                   "state": "OPEN", "labels": [{"name": x} for x in w["labels"]],  # type: ignore[attr-defined]
                                   "comments": [{**_status(900), "body": w["status"]}]})
                continue
            item = next(i for i in self.items if i["number"] == w["number"])
            comments: list[dict[str, object]] = item.setdefault("comments", [])  # type: ignore[assignment]
            if w["op"] == "edit_comment":
                c = next(c for c in comments if bs.comment_id(c) == w["comment_id"])
                c["body"] = w["body"]
            elif w["op"] == "add_comment":
                comments.append({**_status(800 + int(w["number"])), "body": w["body"]})  # type: ignore[call-overload]
            elif w["op"] == "close":
                item["state"] = "CLOSED"


def _scheduled(tmp_path: Path, capsys: pytest.CaptureFixture[str], tracker: ScheduledTracker, today: str,
               mode: str | None = None, ctx: Mapping[str, object] | None = None) -> dict[str, object]:
    _cli_dir(tmp_path, ctx or _context(*CONDITIONS, interactive=False))
    if mode:
        (tmp_path / ".assess" / "config.toml").write_text(f'[backlog]\nmode = "{mode}"\n', encoding="utf-8")
    existing = tmp_path / "existing.json"
    existing.write_text(json.dumps(tracker.items), encoding="utf-8")
    assert render.main([str(tmp_path), "reconcile", "--existing", str(existing), "--scheduled",
                        "--today", today]) == 0
    plan: dict[str, object] = json.loads(capsys.readouterr().out)
    tracker.apply(plan["writes"])  # type: ignore[arg-type]
    return plan


def _without_status_lines(items: list[dict[str, object]]) -> list[dict[str, object]]:
    out = copy.deepcopy(items)
    for item in out:
        for c in item.get("comments", []):  # type: ignore[attr-defined]
            c["body"] = "\n".join(x for x in c["body"].split("\n") if not x.startswith(bs.STATUS_PREFIX))
    return out


def test_two_propose_runs_change_only_the_status_line_of_one_comment_per_item(
        tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    tracker = ScheduledTracker(_scheduled_tracker())
    before = copy.deepcopy(tracker.items)
    for today in ("2026-11-27", "2026-11-28"):
        prior = copy.deepcopy(tracker.items)
        plan = _scheduled(tmp_path, capsys, tracker, today)
        assert plan["mode"] == "propose"
        assert {w["op"] for w in plan["writes"]} == {"edit_comment"}  # type: ignore[attr-defined]
        assert sorted(w["number"] for w in plan["writes"]) == [1, 2, 3]  # type: ignore[attr-defined]
        assert _without_status_lines(tracker.items) == _without_status_lines(prior)
        for old, new in zip(prior, tracker.items, strict=True):
            changed = [(a["body"], b["body"]) for a, b in zip(old.get("comments", []), new.get("comments", []),  # type: ignore[call-overload]
                                                               strict=True) if a["body"] != b["body"]]
            assert len(changed) == (1 if old["number"] in (1, 2, 3) else 0)
            for a, b in changed:
                diff = [(x, y) for x, y in zip(a.split("\n"), b.split("\n"), strict=True) if x != y]
                assert len(diff) == 1 and diff[0][1].startswith(bs.STATUS_PREFIX) and today in diff[0][1]
    assert _without_status_lines(tracker.items) == _without_status_lines(before)
    one = next(i for i in tracker.items if i["number"] == 1)
    assert "Proposed close" in one["comments"][1]["body"] and one["state"] == "OPEN"  # type: ignore[index]


@pytest.mark.parametrize(("mode", "state"), [("file", "OPEN"), ("file-and-close", "CLOSED"), ("propose", "OPEN")])
def test_file_and_close_closes_an_item_whose_condition_holds_and_file_does_not(
        tmp_path: Path, capsys: pytest.CaptureFixture[str], mode: str, state: str) -> None:
    tracker = ScheduledTracker(_scheduled_tracker())
    _scheduled(tmp_path, capsys, tracker, "2026-11-27", mode)
    states = {i["number"]: i["state"] for i in tracker.items}
    assert states[1] == state
    assert states[2] == "CLOSED" and states[3] == "OPEN"  # a reopen stays a proposal in every mode


def test_an_unmarked_item_is_untouched_and_human_edits_are_kept(
        tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    items = _scheduled_tracker()
    items[0]["title"], items[0]["labels"] = "Renamed by a person", [*LABELS, {"name": "p1"}]
    tracker = ScheduledTracker(items)
    before = copy.deepcopy(tracker.items)
    plan = _scheduled(tmp_path, capsys, tracker, "2026-11-27", "file-and-close")
    assert all(w.get("number") not in (4, 5) for w in plan["writes"])  # type: ignore[attr-defined]
    for old, new in zip(before, tracker.items, strict=True):
        assert {k: new[k] for k in ("title", "body", "labels")} == {k: old[k] for k in ("title", "body", "labels")}
        assert new.get("assignees") == old.get("assignees")
    assert tracker.items[3] == before[3] and tracker.items[4] == before[4]
    assert {w["op"] for w in plan["writes"]} <= {"edit_comment", "close"}  # type: ignore[attr-defined]


def test_an_unknown_mode_fails_the_scheduled_run(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _, existing = _cli_dir(tmp_path, _context(*CONDITIONS))
    (tmp_path / ".assess" / "config.toml").write_text('[backlog]\nmode = "close-all"\n', encoding="utf-8")
    assert render.main([str(tmp_path), "reconcile", "--existing", str(existing), "--scheduled",
                        "--today", "2026-11-27"]) == 1
    assert "propose, file, file-and-close" in capsys.readouterr().err
    assert render.main([str(tmp_path), "reconcile", "--existing", str(existing), "--scheduled"]) == 1
    assert "needs --today" in capsys.readouterr().err
    assert render.main([str(tmp_path), "status-comment", "--today", "2026-11-27"]) == 0
    assert bs.STATUS_MARKER in capsys.readouterr().out


# ── the open-item limit on reopens (#1542) ─────────────────────────────────

# Three open items whose conditions still read unmet, and one closed item whose condition lapsed.
FULL = [_marked(n, f"layer-{n}-open.1", "OPEN") for n in (1, 2, 3)] + [
    _marked(9, "layer-3-lint.1", "CLOSED", title="Pin the linter")]
FULL_CONDITIONS = (*(_condition(f"layer-{n}-open.1", "unmet") for n in (1, 2, 3)),
                   _condition("layer-3-lint.1", "unmet", reason="requires_unmet"))


def test_a_reopen_over_the_wip_limit_is_listed_next_up_not_proposed() -> None:
    result = _run(FULL, _context(*FULL_CONDITIONS), wip=3)
    assert result["proposals"] == []
    assert [(n["number"], n["condition"]) for n in result["next_up"]] == [(9, "layer-3-lint.1")]
    assert "requires_unmet" in result["next_up"][0]["reason"]
    assert "**Next up** (not reopened: the backlog holds at most 3 open items" in result["summary"]
    assert "- #9 (Pin the linter):" in result["summary"]
    assert [p["number"] for p in _run(FULL, _context(*FULL_CONDITIONS), wip=4)["proposals"]] == [9]
    assert _run(FULL, _context(*FULL_CONDITIONS))["next_up"] == []  # no limit given: the #811 behaviour


def test_free_slots_go_to_the_next_unlock_reopen_first() -> None:
    items = [_marked(1, "layer-1-open.1", "OPEN"), _marked(5, "layer-3-lint.1", "CLOSED"),
             _marked(6, "layer-6-gate.1", "CLOSED")]
    ctx = _context(_condition("layer-1-open.1", "unmet"), _condition("layer-3-lint.1", "unmet"),
                   _condition("layer-6-gate.1", "unmet"))
    ctx["level"]["next_step"] = {"step": 4, "blockers": [{"condition": "layer-6-gate.1"}]}  # type: ignore[index]
    result = _run(items, ctx, wip=2)
    assert [p["number"] for p in result["proposals"]] == [6]
    assert [n["number"] for n in result["next_up"]] == [5]
    assert [n["number"] for n in _run(items, ctx, wip=1)["next_up"]] == [6, 5]


def test_a_close_in_the_same_batch_does_not_free_a_slot_until_written() -> None:
    items = [*FULL[:2], _marked(3, "layer-5-ci.1", "OPEN"), FULL[3]]
    result = _run(items, _context(*FULL_CONDITIONS[:2], _condition("layer-5-ci.1", "met"), FULL_CONDITIONS[3]),
                  wip=3)
    assert [(p["kind"], p["number"]) for p in result["proposals"]] == [("close", 3)]
    assert [n["number"] for n in result["next_up"]] == [9]


def test_reconcile_subcommand_holds_a_reopen_at_the_configured_limit(
        tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _, existing = _cli_dir(tmp_path, _context(*FULL_CONDITIONS))
    existing.write_text(json.dumps(FULL), encoding="utf-8")
    assert render.main([str(tmp_path), "reconcile", "--existing", str(existing)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["proposals"] == [] and [n["number"] for n in out["next_up"]] == [9]
    assert "**Next up**" in out["summary"]
    (tmp_path / ".assess" / "config.toml").write_text("[backlog]\nwip = 4\n", encoding="utf-8")
    assert render.main([str(tmp_path), "reconcile", "--existing", str(existing)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert [p["number"] for p in out["proposals"]] == [9] and out["next_up"] == []


def test_a_declined_reopen_does_not_keep_the_free_slot(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    items = [*FULL[:2], _marked(5, "layer-3-lint.1", "CLOSED"), _marked(6, "layer-6-gate.1", "CLOSED")]
    ctx = _context(*FULL_CONDITIONS[:2], FULL_CONDITIONS[3], _condition("layer-6-gate.1", "unmet"))
    _, existing = _cli_dir(tmp_path, ctx)
    existing.write_text(json.dumps(items), encoding="utf-8")
    assert render.main([str(tmp_path), "reconcile", "--existing", str(existing)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert [p["number"] for p in out["proposals"]] == [5] and [n["number"] for n in out["next_up"]] == [6]
    assert render.main([str(tmp_path), "reconcile", "--existing", str(existing), "--confirm", "none",
                        "--record-declines", "--declined-by", "ben", "--declined-at", "2026-11-27"]) == 0
    capsys.readouterr()
    assert render.main([str(tmp_path), "reconcile", "--existing", str(existing)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert [p["number"] for p in out["proposals"]] == [6] and out["next_up"] == []
    assert len(out["declined"]) == 1


def test_a_scheduled_status_line_names_a_reopen_held_as_next_up(
        tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    items = copy.deepcopy(FULL)
    for n, item in enumerate(items, start=1):
        item["comments"] = [_status(200 + n)]
    tracker = ScheduledTracker(items)
    _scheduled(tmp_path, capsys, tracker, "2026-11-27", ctx=_context(*FULL_CONDITIONS, interactive=False))
    held = next(i for i in tracker.items if i["number"] == 9)
    assert held["state"] == "CLOSED"
    assert "Next up: reopened on confirmation once the `[backlog] wip` limit" in held["comments"][0]["body"]  # type: ignore[index]
