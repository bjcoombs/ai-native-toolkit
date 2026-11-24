"""The Step diagrams fixture (#941): a small fleet, an earlier run and their golden renders.

The model (``tests/fixtures/fleet_step_diagrams/levels.toml``) gives Step 2 two gates, so a
repository can be held by more than one. Regenerate the goldens with
``uv run --locked python tests/fleet_step_diagrams_fixture.py`` from ``skills/assess``, then
review the diff.
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import lib.fleet.fleet_step_funnel as funnel  # noqa: E402
import lib.fleet.fleet_step_lifts as lifts  # noqa: E402
import lib.fleet.fleet_step_movement as movement  # noqa: E402
from lib.fleet.fleet_compare import FleetRun, comparability  # noqa: E402
from lib.fleet.fleet_data import level_results, step_fields  # noqa: E402
from lib.fleet.fleet_stamp import build_stamp  # noqa: E402
from lib.fleet.runner import FLEET_ROW_VERSION, FleetRow  # noqa: E402
from lib.levels import MaturityModel, load_levels  # noqa: E402
from lib.run_context_types import JsonDict  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "fleet_step_diagrams"
MODEL: MaturityModel = load_levels(FIXTURES / "levels.toml")


def scans(lines: int, agent: int | None, ci: bool) -> JsonDict:
    usage: JsonDict = ({"available": True, "window": "6mo", "total_commits": 10, "agent_trailer_commits": agent}
                       if agent is not None else {"available": False, "reason": "network"})
    return {"nested_instructions": {"budget": {"claude_code": {"lines": lines}}}, "ai_usage": usage,
            "ci_checks": {"available": True, "test_or_lint_configured": ci}}


def row(repo: str, s: JsonDict | None) -> FleetRow:
    return {"fleet_row_version": FLEET_ROW_VERSION, "repo": repo, "group": repo.split("/")[0],
            "status": "ok" if s is not None else "failed", "reason": None if s is not None else "scan_timeout",
            "signals_schema_version": 1 if s is not None else None, "scans": s, "active": s is not None or None}


# top; held by ci; held by instructions alone (twice); held by both Step 2 gates; a not-measured
# gate; a failed scan.
ROWS: tuple[FleetRow, ...] = (
    row("grp/alpha", scans(10, 3, True)),
    row("grp/bravo", scans(10, 3, False)),
    row("grp/charlie", scans(0, 3, True)),
    row("grp/delta", scans(0, 0, False)),
    row("grp/echo", scans(10, None, True)),
    row("grp/foxtrot", None),
    row("grp/golf", scans(0, 2, False)),
)
# The earlier run: alpha has since gained CI (a real move), golf is new, hotel has gone.
EARLIER_ROWS: tuple[FleetRow, ...] = (
    row("grp/alpha", scans(10, 3, False)),
    row("grp/bravo", scans(10, 3, False)),
    row("grp/charlie", scans(0, 3, True)),
    row("grp/delta", scans(0, 0, False)),
    row("grp/echo", scans(10, None, True)),
    row("grp/foxtrot", None),
    row("grp/hotel", scans(10, 3, True)),
)
# Under the earlier run's own rules charlie stood at Step 2; scored again under this model it
# is at Step 1, as now: a move the rule change alone explains.
OLD_RULES_STEPS = {"grp/alpha": 2, "grp/bravo": 2, "grp/charlie": 2, "grp/delta": 1, "grp/echo": 1,
                   "grp/foxtrot": None, "grp/hotel": 3}


def data(rows: tuple[FleetRow, ...] = ROWS, model: MaturityModel = MODEL) -> dict[str, Any]:
    """The ``fleet-data.json`` fields the Step diagrams read, as ``build_fleet_data`` writes them."""
    results = level_results(model, rows)
    fields = step_fields(model, rows, results)
    return {"repos": [{"key": r["repo"], "status": r["status"], "reason": r["reason"], **fields[r["repo"]]}
                      for r in rows],
            "opportunities": {"step_lifts": lifts.step_lifts(model, results.values())}}


def earlier_run(rows: tuple[FleetRow, ...] = EARLIER_ROWS, *, levels_version: str | None = None,
                steps: dict[str, int | None] | None = None, stored: bool = True) -> FleetRun:
    stamp = build_stamp(list(rows), toolkit_commit=None, plugin_version="3.0.0", today=date(2026, 10, 20),
                        window="6mo", levels_version=levels_version or MODEL["version"], counts_only=True,
                        paths_hashed=False, scans=None)
    if steps is None and stored:
        results = level_results(MODEL, rows)
        steps = {k: (v or {}).get("step") for k, v in results.items()}
    return FleetRun(stamp, list(rows), [], None, steps)


RULE_CHANGE = dict(levels_version="step-diagrams-fixture-0", steps=OLD_RULES_STEPS)


def reasons(run: FleetRun) -> list[str]:
    """The comparison's reasons against this run, which differs from ``run`` only in its model version."""
    return comparability(run.stamp, {**run.stamp, "levels_version": MODEL["version"]})[0]  # type: ignore[typeddict-item]
NOT_COMPARABLE = ["window changed '6mo'->'3mo'"]


def goldens() -> dict[str, str]:
    """``expected-<name>.svg`` -> the render."""
    one = data(ROWS[:1])
    return {
        "expected-step-funnel.svg": funnel.render(MODEL, data()),
        "expected-one-change-away.svg": lifts.render(data()),
        "expected-step-movement.svg": movement.render(movement.step_moves(MODEL, earlier_run(), data())),
        "expected-step-movement-rules.svg": movement.render(movement.step_moves(
            MODEL, earlier_run(**RULE_CHANGE), data(), reasons(earlier_run(**RULE_CHANGE)))),
        "expected-step-movement-not-comparable.svg": movement.render(movement.step_moves(
            MODEL, earlier_run(), data(), NOT_COMPARABLE)),
        "expected-step-funnel-one.svg": funnel.render(MODEL, one),
        "expected-one-change-away-one.svg": lifts.render(one),
        "expected-step-movement-one.svg": movement.render(movement.step_moves(
            MODEL, earlier_run(EARLIER_ROWS[:1]), one)),
    }


if __name__ == "__main__":
    for name, content in goldens().items():
        (FIXTURES / name).write_text(content, encoding="utf-8", newline="\n")
