"""The top card re-rendered after finalize writes this run's Top 3 (lib/card_refresh.py, #1252).

The core words the card before the model has chosen this run's Top 3, so its Start here names
the action the previous run left. Finalize writes ``actions.json`` and then re-renders the two
lines, in the run context and in the report, so the finished card names this run's rank 1.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from lib.card_refresh import refresh_card, refreshed, refreshed_report
from lib.exec_summary import run_headline, summarised
from lib.headline import headline
from lib.level_tally import default_model
from lib.top_card import card_lines, card_markdown

MODEL = default_model()
OLD = {"start": "`old step`", "done_when": "the old thing is done"}
NEW_ACTIONS = {"schema": 2, "actions": [
    {"rank": 1, "status": "pending", "first_step": "new step", "done_when": "the new thing passes",
     "action": "a", "scope_fence": "f"},
    {"rank": 2, "status": "pending", "first_step": "second", "done_when": "two", "action": "b",
     "scope_fence": "f"}]}


def _cond(cid: str, state: str = "met") -> dict[str, Any]:
    return {"id": cid, "row": cid.rsplit(".", 1)[0], "level": 1, "grade": "verified", "counted": True,
            "state": state}


def _level() -> dict[str, Any]:
    conds = [_cond("layer-0-instructions.1"), _cond("layer-5-ci.1"), _cond("layer-6-coverage.1")]
    return {"level_result_version": 1, "levels_version": "default-x",
            "tally": {"met": 3, "measured": 3, "not_measured": 0}, "counted": [], "listed": [],
            "not_measured": [], "conditions": conds, "step": 4, "next_step": None}


def _block(action: Any = OLD) -> Any:
    level = _level()
    return summarised(headline(level, MODEL), level, None, MODEL, None, None, "abc1234", action)  # type: ignore[arg-type]


def _run(tmp_path: Path, report_card: str | None = None) -> tuple[Path, Any]:
    block = _block()
    ctx = {"run_id": "r1", "level": _level(), "headline": block}
    (tmp_path / "run-context.json").write_text(json.dumps(ctx, indent=2), encoding="utf-8")
    card = block["summary_markdown"] if report_card is None else report_card
    (tmp_path / "assess-report.md").write_text(
        f"# Codebase Assessment: demo\n\n_Generated today._\n\n{card}\n\n## Top 3 Actions\n\n| 1 | a |\n",
        encoding="utf-8")
    (tmp_path / "actions.json").write_text(json.dumps(NEW_ACTIONS), encoding="utf-8")
    return tmp_path, block


def test_finalize_refresh_names_this_runs_rank_one_in_context_and_report(tmp_path: Path) -> None:
    assess_dir, old = _run(tmp_path)
    assert refresh_card(assess_dir) is None
    ctx = json.loads((assess_dir / "run-context.json").read_text(encoding="utf-8"))
    card = ctx["headline"]
    assert card["call_to_action"] == "Start here: `new step`"
    assert "Done when: the new thing passes." in card["summary_lines"]
    # every other line is the core's, and the markdown is the card renderer's own output
    assert card["summary_lines"][:-2] == old["summary_lines"][:-2]
    assert card["summary_markdown"] == card_markdown(card["summary_lines"])
    report = (assess_dir / "assess-report.md").read_text(encoding="utf-8")
    assert card["summary_markdown"] in report and "old step" not in report
    assert report.count("Start here") == 1 and report.count("Done when") == 1
    # a second finalize changes nothing
    before = (assess_dir / "run-context.json").read_text(encoding="utf-8"), report
    assert refresh_card(assess_dir) is None
    assert ((assess_dir / "run-context.json").read_text(encoding="utf-8"),
            (assess_dir / "assess-report.md").read_text(encoding="utf-8")) == before


def test_a_report_that_moved_the_card_still_gets_its_two_lines(tmp_path: Path) -> None:
    lines = _block()["summary_lines"]
    loose = "\n\n".join(["**Moved lead**", *lines[1:]])  # the lead reworded, the rest as pasted
    assess_dir, _ = _run(tmp_path, loose)
    assert refresh_card(assess_dir) is None
    report = (assess_dir / "assess-report.md").read_text(encoding="utf-8")
    assert "Start here: `new step`" in report and "Done when: the new thing passes." in report
    assert "**Moved lead**" in report


def test_a_report_without_the_card_is_named_in_a_warning(tmp_path: Path) -> None:
    assess_dir, _ = _run(tmp_path, "no card here")
    warning = refresh_card(assess_dir)
    assert warning is not None and "headline.summary_markdown" in warning
    # the run context still carries the refreshed card for the deterministic report
    ctx = json.loads((assess_dir / "run-context.json").read_text(encoding="utf-8"))
    assert ctx["headline"]["call_to_action"] == "Start here: `new step`"


def test_without_an_open_action_the_card_falls_back_and_without_a_card_nothing_is_written(tmp_path: Path) -> None:
    block = _block()
    fallback = refreshed(block, _level(), None)
    assert fallback is not None
    assert fallback["call_to_action"] == "Start here: run `/assess` again after your next change"
    assert refreshed(_block(None), _level(), None) is None  # nothing changes
    # counts-only drops the card fields; an unavailable headline has none
    assert refreshed({k: v for k, v in block.items() if not k.startswith("summary_")}, _level(), None) is None
    assert refreshed(block, {"available": False}, None) is None
    assert refresh_card(tmp_path) is None  # no run context at all
    assert refreshed_report("text", {"summary_lines": ["x"]}, {"summary_lines": ["y"]}) is None


def test_a_scoped_run_never_reads_the_whole_repo_actions(tmp_path: Path) -> None:
    """#1280 review: ``.assess/<slug>/`` reads its own actions.json, never ``.assess/actions.json``."""
    whole = tmp_path / ".assess"
    scoped = whole / "services-api"
    scoped.mkdir(parents=True)
    (whole / "actions.json").write_text(json.dumps(NEW_ACTIONS), encoding="utf-8")
    level = _level()
    block = run_headline(level, None, None, None, None, scoped)
    assert "new step" not in json.dumps(block)
    assert block["call_to_action"] == "Start here: run `/assess` again after your next change"
    (scoped / "run-context.json").write_text(json.dumps({"level": level, "headline": _block()}), encoding="utf-8")
    refresh_card(scoped)
    ctx = json.loads((scoped / "run-context.json").read_text(encoding="utf-8"))
    assert "new step" not in json.dumps(ctx)


def test_a_step_name_that_opens_with_an_acronym_keeps_it() -> None:
    """#1280 review: a ``--levels`` Step named ``CI-gated merges`` must not read ``cI-gated merges``."""
    steps = [{"step": 1, "name": "CI-gated merges", "gates": []},
             {"step": 2, "name": "Unattended", "gates": [{"id": "g", "row": "r", "level": 1}]}]
    model = {**MODEL, "steps": steps}
    level = {**_level(), "step": 1, "next_step": {"step": 2, "blockers": []}}
    assert card_lines(level, model, None)[0] == "Step 1 of 2: CI-gated merges."  # type: ignore[arg-type]
    named = {**model, "steps": [{**steps[0], "name": "Agents in CI"}, steps[1]]}
    assert card_lines(level, named, None)[0] == "Step 1 of 2: agents in CI."  # type: ignore[arg-type]


def test_finalize_rewrites_the_card_from_the_top_3_it_writes(tmp_path: Path) -> None:
    from assess_finalize import finalize_run
    block = _block()
    ctx = {"run_id": "r1", "level": _level(), "headline": block,
           "stats_summary": {"top_hotspots": []},
           "mutation_not_run_cap": {"applies": False, "mutation_run": True, "max_layer6_band": "Present"}}
    (tmp_path / "run-context.json").write_text(json.dumps(ctx), encoding="utf-8")
    (tmp_path / "assess-report.md").write_text(f"# R\n\n{block['summary_markdown']}\n\n## Top 3 Actions\n",
                                               encoding="utf-8")
    (tmp_path / "finalize-input.json").write_text(json.dumps(
        {"run_id": "r1", "top_action": "a", "hotspot_actions": {}, "actions": NEW_ACTIONS["actions"]}),
        encoding="utf-8")
    finalize_run(assess_dir=tmp_path)
    report = (tmp_path / "assess-report.md").read_text(encoding="utf-8")
    assert "Start here: `new step`" in report and "old step" not in report
    refreshed_ctx = json.loads((tmp_path / "run-context.json").read_text(encoding="utf-8"))
    assert refreshed_ctx["headline"]["call_to_action"] == "Start here: `new step`"
