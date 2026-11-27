"""Condition marker and dedup by condition id on filed backlog items (#810), and the WIP limit (#812)."""
from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

import pytest

import assess_actions_render as render
from lib.report import backlog_items as bi
from lib.report import backlog_wip as bw

MODEL = "default-2026.11.24"
ENTRY = {"rank": 1, "action": "Run the tests in CI", "condition": "layer-5-ci.1",
         "done_when": "`make check` runs in CI", "scope_fence": "CI only", "layer": 5}


class FixtureTracker:
    """A tracker that files an item only when the plan says create, and writes back a marked body."""

    def __init__(self, items: list[dict[str, object]] | None = None) -> None:
        self.items: list[dict[str, object]] = list(items or [])

    def file(self, rendered: list[render.TrackerItem], label: str = bi.DEFAULT_LABEL) -> list[int]:
        numbers = []
        for item, step in zip(rendered, bi.plan_filing(rendered, self.items, label=label), strict=True):
            if step["action"] == "create":
                number = len(self.items) + 1
                self.items.append({"number": number, "title": item["title"], "body": item["body"],
                                   "state": "OPEN", "labels": [{"name": bi.DEFAULT_LABEL}]})
            else:
                number = step["number"]
                if step["action"] == "reuse_and_mark":
                    self._get(number)["body"] = step["marked_body"]
            numbers.append(number)
        return numbers

    def _get(self, number: int) -> dict[str, object]:
        return next(i for i in self.items if i["number"] == number)


def _items(*entries: Mapping[str, object]) -> list[render.TrackerItem]:
    return render.render_items(list(entries), model=MODEL)


def test_filing_twice_for_one_condition_creates_one_item() -> None:
    tracker = FixtureTracker()
    first = tracker.file(_items(ENTRY))
    second = tracker.file(_items(ENTRY))
    assert first == second == [1]
    assert len(tracker.items) == 1


def test_a_retitled_item_is_matched_by_its_marker() -> None:
    tracker = FixtureTracker()
    tracker.file(_items(ENTRY))
    tracker.items[0]["title"] = "CI: run the suite on every push"  # a maintainer renamed it
    reworded = {**ENTRY, "action": "Add a CI job that runs make check"}
    assert tracker.file(_items(reworded)) == [1]
    assert len(tracker.items) == 1


def test_an_unmarked_item_is_matched_by_label_and_title_and_gains_the_marker() -> None:
    old_body = "Filed before markers existed.\n"
    tracker = FixtureTracker([{"number": 7, "title": "Run the tests in CI", "body": old_body,
                               "state": "CLOSED", "labels": ["assess-finding"]}])
    assert tracker.file(_items(ENTRY)) == [7]
    body = str(tracker.items[0]["body"])
    assert body.startswith(old_body.rstrip())
    assert body.rstrip().endswith(f"<!-- assess:condition=layer-5-ci@1 model={MODEL} -->")
    assert tracker.file(_items({**ENTRY, "action": "Renamed"})) == [7]  # now matched by its marker


def test_an_unmarked_item_without_the_label_is_not_matched() -> None:
    tracker = FixtureTracker([{"number": 3, "title": "Run the tests in CI", "body": "", "labels": []}])
    assert tracker.file(_items(ENTRY)) == [2]  # a new item beside #3
    upstream = FixtureTracker([{"number": 3, "title": "Run the tests in CI", "body": "", "labels": []}])
    assert upstream.file(_items(ENTRY), label="") == [3]  # filing upstream: no label to check


def test_a_marked_item_for_another_condition_is_not_taken_by_title() -> None:
    other = {**ENTRY, "condition": "layer-5-ci.2"}
    tracker = FixtureTracker()
    tracker.file(_items(other))
    assert tracker.file(_items(ENTRY)) == [2]


def test_sub_items_carry_their_own_marker_and_survive_a_rank_change() -> None:
    entry = {**ENTRY, "sub_items": [{"label": "a", "action": "Pin ruff", "done_when": "`ruff --version`"}]}
    items = _items(entry)
    assert items[1]["marker"] == f"<!-- assess:condition=layer-5-ci@1 sub=a model={MODEL} -->"
    tracker = FixtureTracker()
    assert tracker.file(items) == [1, 2]
    moved = _items({**entry, "rank": 3, "sub_items": [{"label": "3a", "action": "Pin ruff",
                                                       "done_when": "x"}]})
    assert moved[1]["title"] == "3a. Pin ruff"
    assert tracker.file(moved) == [1, 2]


def test_a_sub_item_marker_needs_its_title_too() -> None:
    # Letters are positional: the model reassigns them, so sub=a alone is not the same work.
    first = {**ENTRY, "sub_items": [{"label": "a", "action": "Pin ruff", "done_when": "d"}]}
    tracker = FixtureTracker()
    assert tracker.file(_items(first)) == [1, 2]
    other = {**ENTRY, "sub_items": [{"label": "a", "action": "Add a lockfile", "done_when": "d"}]}
    assert tracker.file(_items(other)) == [1, 3]


def test_a_sub_item_whose_letter_moved_is_found_and_re_marked() -> None:
    first = {**ENTRY, "sub_items": [{"label": "a", "action": "Pin ruff", "done_when": "d"}]}
    tracker = FixtureTracker()
    tracker.file(_items(first))
    moved = {**ENTRY, "sub_items": [{"label": "a", "action": "Add a lockfile", "done_when": "d"},
                                    {"label": "b", "action": "Pin ruff", "done_when": "d"}]}
    assert tracker.file(_items(moved)) == [1, 3, 2]
    assert bi.marker_key(str(tracker.items[1]["body"])) == ("layer-5-ci", 1, "b", 0)
    assert str(tracker.items[1]["body"]).count("assess:condition=") == 1


def test_an_unmarked_sub_item_is_matched_after_its_prefix() -> None:
    entry = {**ENTRY, "sub_items": [{"label": "a", "action": "Pin ruff", "done_when": "d"}]}
    tracker = FixtureTracker([
        {"number": 1, "title": "Run the tests in CI", "body": "", "labels": ["assess-finding"]},
        {"number": 2, "title": "2a. Pin ruff", "body": "", "labels": ["assess-finding"]},
    ])
    assert tracker.file(_items(entry)) == [1, 2]
    assert bi.marker_key(str(tracker.items[1]["body"])) == ("layer-5-ci", 1, "a", 0)


def test_an_action_without_a_condition_has_no_marker_and_dedups_by_title() -> None:
    plain = {k: v for k, v in ENTRY.items() if k != "condition"}
    item = _items(plain)[0]
    assert "marker" not in item and "assess:" not in item["body"]
    tracker = FixtureTracker()
    assert tracker.file([item]) == tracker.file([item]) == [1]


def test_one_unmarked_item_is_claimed_once_per_run() -> None:
    tracker = FixtureTracker([{"number": 1, "title": "Same", "body": "", "labels": ["assess-finding"]}])
    plain = {"rank": 1, "action": "Same", "done_when": "d", "scope_fence": "s"}
    assert tracker.file(_items(plain, {**plain, "rank": 2})) == [1, 2]


def test_open_items_are_preferred_over_closed_ones() -> None:
    marker = bi.condition_marker("layer-5-ci.1", MODEL)
    existing = [{"number": 2, "body": marker, "state": "CLOSED"}, {"number": 9, "body": marker, "state": "OPEN"}]
    assert bi.match_item("t", marker, existing)["number"] == 9


@pytest.mark.parametrize(("condition", "model", "sub", "marker"), [
    ("layer-5-ci.1", MODEL, "", f"<!-- assess:condition=layer-5-ci@1 model={MODEL} -->"),
    ("layer-5-ci.1", None, "2b", "<!-- assess:condition=layer-5-ci@1 sub=b model=unknown -->"),
    ("my.row.2", "v 1", "", "<!-- assess:condition=my.row@2 model=v_1 -->"),
    ("no-level", MODEL, "", ""),
    ("row.x", MODEL, "", ""),
])
def test_condition_marker_format(condition: str, model: str | None, sub: str, marker: str) -> None:
    assert bi.condition_marker(condition, model, sub) == marker


def test_parse_marker_ignores_unknown_fields_and_other_comments() -> None:
    body = "text\n<!-- other -->\n<!-- assess:condition=r@2 sub=c model=m since=x -->\n"
    assert bi.parse_marker(body) == {"condition": "r@2", "sub": "c", "model": "m", "since": "x"}
    assert bi.marker_key(body) == ("r", 2, "c", 0)
    assert bi.marker_key("<!-- assess:condition=r@2 part=3 model=m -->") == ("r", 2, "", 3)
    assert bi.marker_key("<!-- assess:condition=bad model=m -->") is None
    assert bi.parse_marker("") is None


def test_plan_subcommand_reads_the_tracker_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assess = tmp_path / ".assess"
    assess.mkdir()
    (assess / "actions.json").write_text(json.dumps({"schema": 2, "actions": [ENTRY]}), encoding="utf-8")
    (assess / "run-context.json").write_text(json.dumps({"level": {"levels_version": MODEL}}), encoding="utf-8")
    existing = tmp_path / "existing.json"
    existing.write_text(json.dumps([{"number": 5, "title": "Run the tests in CI", "body": "old",
                                     "state": "OPEN", "labels": [{"name": "assess-finding"}]}]),
                        encoding="utf-8")
    assert render.main([str(tmp_path), "plan", "--existing", str(existing)]) == 0
    (item,) = json.loads(capsys.readouterr().out)
    assert item["filing"]["action"] == "reuse_and_mark" and item["filing"]["number"] == 5
    assert item["filing"]["marked_body"] == f"old\n\n{item['marker']}\n"
    existing.write_text("{}", encoding="utf-8")
    assert render.main([str(tmp_path), "plan", "--existing", str(existing)]) == 1
    assert "JSON array" in capsys.readouterr().err


def test_items_split_from_one_action_keep_separate_items() -> None:
    # "One concern per item": the parts of one action share a condition, so each carries part=<n>.
    items = _items(ENTRY)
    base, marker = items[0]["body"], items[0]["marker"]
    parts = []
    for n in (1, 2):
        split = bi.condition_marker("layer-5-ci.1", MODEL, part=n)
        assert split == f"<!-- assess:condition=layer-5-ci@1 part={n} model={MODEL} -->"
        parts.append(render.TrackerItem(rank=1, title=f"Part {n}", body=base.replace(marker, split)))
    tracker = FixtureTracker()
    assert tracker.file(parts) == [1, 2]
    assert tracker.file(parts) == [1, 2]
    assert tracker.file(items) == [3]  # the unsplit action is a third key


# The work-in-progress limit and the next-unlock ranking (#812).

def _entry(rank: int, condition: str) -> dict[str, object]:
    return {**ENTRY, "rank": rank, "action": f"Action {rank}", "condition": condition}


def _marked(number: int, condition: str, state: str = "OPEN") -> dict[str, object]:
    return {"number": number, "title": f"Filed {number}", "body": bi.condition_marker(condition, MODEL),
            "state": state, "labels": [{"name": bi.DEFAULT_LABEL}]}


def _propose(items: list[render.TrackerItem], existing: list[dict[str, object]],
             level: object = None, wip: int = 3) -> list[render.TrackerItem]:
    return render.plan_items(items, existing, level=level, wip=wip)


def _filed(planned: list[render.TrackerItem]) -> list[str]:
    return [p["title"] for p in planned if p["wip"]["decision"] == bw.FILE]


NEW = [_entry(1, "row-a.1"), _entry(2, "row-b.1"), _entry(3, "row-c.1")]


def test_three_open_marked_items_hold_every_new_item_as_next_up() -> None:
    existing = [_marked(n, f"old-{n}.1") for n in (1, 2, 3)]
    planned = _propose(_items(*NEW), existing)
    assert _filed(planned) == []
    assert [p["wip"]["decision"] for p in planned] == [bw.NEXT_UP] * 3
    block = bw.next_up_markdown(planned, [p["wip"] for p in planned])
    assert block.startswith("**Next up**")
    assert [line for line in block.splitlines() if line.startswith("- ")] == [
        "- Action 1", "- Action 2", "- Action 3"]


def test_closing_one_lets_the_next_ranked_item_be_proposed() -> None:
    existing = [_marked(n, f"old-{n}.1") for n in (1, 2, 3)]
    existing[1]["state"] = "CLOSED"
    level = {"next_step": {"step": 2, "blockers": [{"kind": "gate", "condition": "row-c.1"}]}}
    planned = _propose(_items(*NEW), existing, level)
    assert _filed(planned) == ["Action 3"]  # row-c.1 unlocks the next Step, so it outranks rank 1
    assert planned[2]["wip"]["tier"] == "next_unlock"
    assert [line for line in bw.next_up_markdown(planned, [p["wip"] for p in planned]).splitlines()
            if line.startswith("- ")] == ["- Action 1", "- Action 2"]


def test_an_item_already_open_takes_no_new_slot() -> None:
    existing = [_marked(1, "row-a.1"), _marked(2, "old-2.1")]
    planned = _propose(_items(*NEW), existing)
    assert [p["wip"]["decision"] for p in planned] == [bw.OPEN, bw.FILE, bw.NEXT_UP]
    assert bw.next_up_markdown(planned, [p["wip"] for p in planned], wip=3).count("\n- ") == 1


def test_a_lapse_recovery_outranks_a_new_gain() -> None:
    existing = [_marked(1, "old-1.1"), _marked(2, "old-2.1"), _marked(9, "row-b.1", state="CLOSED")]
    planned = _propose(_items(*NEW), existing)
    assert [p["wip"]["tier"] for p in planned] == ["gain", "recovery", "gain"]
    assert _filed(planned) == ["Action 2"]  # reopening #9 takes the one free slot


def test_unmarked_labelled_open_items_count_and_the_limit_is_configurable() -> None:
    legacy = [{"number": 4, "title": "Old", "body": "", "state": "OPEN", "labels": ["assess-finding"]}]
    assert bw.open_backlog(legacy) == {4} and bw.open_backlog(legacy, label="") == set()
    assert _filed(_propose(_items(*NEW), legacy, wip=1)) == []
    assert _filed(_propose(_items(*NEW), [], wip=0)) == []
    assert _filed(_propose(_items(*NEW), [], wip=5)) == ["Action 1", "Action 2", "Action 3"]
    assert bw.next_up_markdown([], []) == ""


def test_next_up_subcommand_reads_the_wip_from_config(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assess = tmp_path / ".assess"
    assess.mkdir()
    (assess / "actions.json").write_text(json.dumps({"schema": 2, "actions": NEW}), encoding="utf-8")
    (assess / "run-context.json").write_text(json.dumps({"level": {"levels_version": MODEL}}), encoding="utf-8")
    (assess / "config.toml").write_text("[backlog]\nwip = 1\n", encoding="utf-8")
    existing = tmp_path / "existing.json"
    existing.write_text("[]", encoding="utf-8")
    assert render.main([str(tmp_path), "plan", "--existing", str(existing)]) == 0
    assert [i["wip"]["decision"] for i in json.loads(capsys.readouterr().out)] == ["file", "next_up", "next_up"]
    assert render.main([str(tmp_path), "next-up", "--existing", str(existing)]) == 0
    assert "- Action 2\n- Action 3\n" in capsys.readouterr().out
    assert render.main([str(tmp_path), "next-up", "--existing", str(existing), "--wip", "3"]) == 0
    assert capsys.readouterr().out == ""


def test_two_actions_matching_one_closed_item_take_one_slot() -> None:
    # A marker without sub or part claims nothing, so both actions reuse closed #9 (#811 note).
    existing = [_marked(1, "old-1.1"), _marked(2, "old-2.1"), _marked(9, "row-a.1", state="CLOSED")]
    twins = _items(_entry(1, "row-a.1"), _entry(2, "row-a.1"), _entry(3, "row-c.1"))
    planned = _propose(twins, existing)
    assert [p["filing"].get("number") for p in planned[:2]] == [9, 9]
    assert [p["wip"]["decision"] for p in planned] == [bw.FILE, bw.FILE, bw.NEXT_UP]
