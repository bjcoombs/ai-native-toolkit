"""Each condition's ``why`` in the fleet outputs (issue #1128).

A fleet run under the default maturity model carries the check manifest's why for each
next-step criterion: in ``fleet-data.json`` (``repos.rows.why``, ``opportunities.biggest_lift.why``),
on the team next-steps pages ("- Why:" above "Done when") and in ``level.json`` (each
condition's ``why``, already set by ``lib.level_tally``). A criterion with no manifest entry
gets null and no Why line. The why is the toolkit's own text, so counts-only and hashed
bundles keep it word for word (``FLEET_DATA_KEPT``).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

import lib.fleet.fleet_next_steps as ns
from lib.counts_only.counts_only_rules import FLEET_DATA_KEPT
from lib.fleet.fleet_publish import check_strings
from lib.level_signals import DEFAULT_LEVELS
from lib.level_tally import criterion_why
from lib.manifest.check_manifest import condition_key, core_manifest
from lib.manifest.manifest_whys import condition_whys
from test_fleet_cli import fleet_cli, froot  # noqa: F401 - froot is a fixture
from test_fleet_next_steps import EXPECTED, cli

WHYS = condition_whys(core_manifest())
MODES = {"full": ["--full"], "counts-only": [], "hashed": ["--hash-salt", "s4lt"]}


@pytest.fixture(scope="module")
def runs(froot: Path, tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:  # noqa: F811
    out: dict[str, Path] = {}
    for mode, flags in MODES.items():
        target = tmp_path_factory.mktemp(f"why-{mode}")
        assert fleet_cli.main([str(froot), "--levels", str(DEFAULT_LEVELS), "--out", str(target), *flags]) == 0
        out[mode] = target
    return out


def _data(out: Path) -> Any:
    return json.loads((out / "fleet-data.json").read_text(encoding="utf-8"))


def _expected_why(cell: dict[str, Any]) -> str | None:
    """The manifest why behind a cell, read straight from the manifest (not via criterion_why)."""
    if cell["signal"] is None or cell["status"] != "level":
        return None
    return WHYS.get(condition_key(cell["row"], cell["level"] + 1, cell["signal"]))


@pytest.mark.parametrize("mode", sorted(MODES))
def test_fleet_data_cells_carry_the_manifest_why_in_every_privacy_mode(runs: dict[str, Path], mode: str) -> None:
    data = _data(runs[mode])
    cells = [cell for repo in data["repos"] for cell in repo["rows"]]
    below = [c for c in cells if c["status"] == "level" and c["signal"] is not None]
    assert below, "the fixture fleet has a repository below a row's top level"
    for cell in below:
        assert cell["why"] == _expected_why(cell)
    assert any(cell["why"] for cell in below)
    assert all(cell["why"] is None for cell in cells if cell["signal"] is None)
    lift = data["opportunities"]["biggest_lift"]
    if lift is not None:
        assert lift["why"] == WHYS.get(condition_key(lift["row"], lift["level"], lift["signal"]))


@pytest.mark.parametrize("mode", ["counts-only", "hashed"])
def test_counts_only_bundles_keep_the_why_and_pass_the_publish_check(runs: dict[str, Path], mode: str) -> None:
    """#770's lesson: a test that fails if counts-only drops the field."""
    assert {"repos.rows.why", "opportunities.biggest_lift.why"} <= FLEET_DATA_KEPT
    full = {(r["status"], tuple(c["why"] for c in r["rows"])) for r in _data(runs["full"])["repos"]}
    kept = {(r["status"], tuple(c["why"] for c in r["rows"])) for r in _data(runs[mode])["repos"]}
    assert kept == full
    assert check_strings(_data(runs[mode]), "", mode == "hashed") == []


def test_team_pages_show_why_above_done_when(runs: dict[str, Path]) -> None:
    out = runs["full"]
    assert cli.main([str(out)]) == 0
    lines = [line for page in sorted((out / "teams").rglob("*.md"))
             for line in page.read_text(encoding="utf-8").splitlines()]
    whys = [i for i, line in enumerate(lines) if line.startswith("   - Why: ")]
    assert whys, "a default-model step with a manifest why shows it"
    for i in whys:
        assert lines[i + 1].startswith("   - Done when: ")
        assert lines[i - 1].split(". ", 1)[0].isdigit()  # straight under the numbered step
        assert lines[i].removeprefix("   - Why: ") in {ns.text(w) for w in WHYS.values()}
    assert "   - Why: \n" not in "\n".join(lines) + "\n"


def test_level_json_conditions_carry_their_why(runs: dict[str, Path]) -> None:
    level = json.loads((runs["counts-only"] / "level.json").read_text(encoding="utf-8"))
    conditions = [c for result in level["repos"].values() for c in result["conditions"]]
    assert conditions and all(c.get("why") for c in conditions if c["state"] == "unmet")


def test_a_criterion_with_no_manifest_entry_gets_no_why_line() -> None:
    criterion: Any = {"signal": "own.signal", "op": "eq", "value": True}
    assert criterion_why("own-row", 1, "own.signal") is None
    bare = ns._step(1, criterion, [])
    assert not any("Why:" in line for line in bare)
    worded = ns._step(1, criterion, [], why="Because.")
    assert worded[1] == "   - Why: Because." and worded[2].startswith("   - Done when: ")


def test_the_golden_pages_of_a_callers_own_model_have_no_why_line() -> None:
    pages = [p.read_text(encoding="utf-8") for p in EXPECTED.rglob("*.md")]
    assert pages and not any("Why:" in page for page in pages)


def test_criterion_why_reads_the_core_manifest() -> None:
    key, why = next(iter(WHYS.items()))
    head, _, signal = key.partition(":")
    row, _, level = head.rpartition(".")
    assert criterion_why(row, int(level), signal) == why
