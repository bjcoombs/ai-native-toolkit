"""Condition marker and dedup by condition id on filed backlog items (#810)."""
from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

import pytest

import assess_actions_render as render
from lib.report import backlog_items as bi

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
    moved = _items({**entry, "rank": 3, "sub_items": [{"label": "3a", "action": "Pin ruff 0.9",
                                                       "done_when": "x"}]})
    assert moved[1]["title"] == "3a. Pin ruff 0.9"
    assert tracker.file(moved) == [1, 2]


def test_an_unmarked_sub_item_is_matched_after_its_prefix() -> None:
    entry = {**ENTRY, "sub_items": [{"label": "a", "action": "Pin ruff", "done_when": "d"}]}
    tracker = FixtureTracker([
        {"number": 1, "title": "Run the tests in CI", "body": "", "labels": ["assess-finding"]},
        {"number": 2, "title": "2a. Pin ruff", "body": "", "labels": ["assess-finding"]},
    ])
    assert tracker.file(_items(entry)) == [1, 2]
    assert bi.marker_key(str(tracker.items[1]["body"])) == ("layer-5-ci", 1, "a")


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
    assert bi.marker_key(body) == ("r", 2, "c")
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
