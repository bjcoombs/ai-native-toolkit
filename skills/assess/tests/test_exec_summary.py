"""The headline block with the report's top card filled in (lib/exec_summary.py, #1102, #1252).

The card's own states are pinned in ``tests/test_top_card.py``. Here: an unavailable headline
keeps the fields empty, counts-only drops the card text, the catalog has the Step gates entry
the fallback call to action links, the landing page shows the card the code prints, and the
end-to-end case runs the core on the contract's ci2 shape and reads the card at the very top
of the deterministic report.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path
from typing import Any

from lib.exec_summary import GATE_TASK, STEP_GATES_URL, summarised
from lib.headline import as_headline, headline
from lib.level_tally import default_model
from lib.top_card import card_lines
from test_headline import SCRIPTS, _level, _run, ci2  # noqa: F401 - ci2 is a fixture
from test_top_card import _cond
from test_top_card import _level as _card_level

ROOT = Path(__file__).resolve().parents[3]
KNOWN = {"available": True, "first_run": False, "closed_count": 1, "closed": [], "closed_reason": None,
         "gates_gained": [], "gates_lost": [], "next_blocker": "ci_runs_tests"}


def test_an_unavailable_headline_keeps_the_fields_empty() -> None:
    down = headline({"available": False, "reason": "x"}, default_model())
    assert summarised(down, {"available": False}, KNOWN, default_model())["summary_markdown"] == ""


def test_as_headline_backfills_the_summary_on_an_older_block() -> None:
    old = {k: v for k, v in headline(_level(), default_model()).items()
           if k not in ("summary_lines", "call_to_action", "summary_markdown")}
    assert (as_headline(old)["summary_lines"], as_headline(old)["summary_markdown"]) == ([], "")


def test_counts_only_drops_the_card_text() -> None:
    from lib.counts_only.content_free import counts_only

    level = _level()
    block = summarised(headline(level, default_model()), level, KNOWN, default_model())  # type: ignore[arg-type]
    kept = counts_only({"headline": block})["headline"]
    assert "call_to_action" not in kept and "summary_markdown" not in kept
    assert kept["summary_lines"] == len(block["summary_lines"])


def test_the_catalog_has_the_step_gates_entry_the_link_names() -> None:
    catalog = (ROOT / "docs" / "what-we-measure.md").read_text(encoding="utf-8")
    assert STEP_GATES_URL.endswith("/docs/what-we-measure.md#step-gates")
    assert "\n### Step gates\n" in catalog


def test_every_default_gate_has_a_task() -> None:
    gates = {g["id"] for s in default_model().get("steps", []) for g in s.get("gates", [])}
    assert gates <= set(GATE_TASK)


def test_the_landing_page_shows_the_card_the_code_prints() -> None:
    """The site's "What the report says first" example is the code's own output, word for word."""
    page = (ROOT / "docs" / "index.html").read_text(encoding="utf-8")
    conds = [_cond("layer-0-instructions.1"), _cond("layer-5-ci.1", "unmet"), _cond("layer-6-coverage.1", "unmet")]
    level = _card_level(2, {"step": 3, "blockers": [{"kind": "gate", "gate": "ci_runs_tests",
                                                     "condition": "layer-5-ci.1", "state": "unmet"}]}, conds)
    action: Any = {"start": "add `pytest` to the `test` job in `.github/workflows/ci.yml`",
                   "done_when": "a failing test turns the `test` job red"}
    for line in card_lines(level, default_model(), KNOWN, None, None, action):
        html = re.sub(r"`([^`]+)`", r"<code>\1</code>", line)
        assert f"<div>{html}</div>" in page, line


def test_ci2_report_opens_with_the_card(ci2: Path) -> None:  # noqa: F811 - the fixture
    ctx = _run(ci2, "2026-11-22")
    head = ctx["headline"]
    assert head["summary_lines"][0].startswith(f"Step {ctx['level']['step']} of 4: ")
    assert "This run is your baseline; the next `/assess` run reports what moved." in head["summary_lines"]
    assert sum(line.startswith("Start here") for line in head["summary_lines"]) == 1
    report = subprocess.run([sys.executable, str(SCRIPTS / "assess_report.py"), str(ci2), "--stdout"],
                            capture_output=True, encoding="utf-8", errors="replace", check=True).stdout
    assert report.index(head["summary_markdown"]) < report.index("\n## ")
    assert "not known" not in report.split("\n## ", 1)[0]
