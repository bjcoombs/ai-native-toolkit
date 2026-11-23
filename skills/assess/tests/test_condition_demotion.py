"""The fleet exit rule (#1086): lib/fleet/condition_demotion.py and the tally's ``demoted`` argument."""
from __future__ import annotations

import ast
import datetime as dt
import json
from pathlib import Path
from typing import Any

import pytest

import lib.fleet.condition_demotion as cd
import lib.fleet.condition_effect as ce
import lib.level_tally as lt
from lib.fleet.fleet_history import OPTIONAL_HISTORY_FILES
from lib.fleet.fleet_level import fleet_levels
from lib.fleet.runner import FLEET_ROW_VERSION
from lib.levels import parse_levels
from lib.levels_fields import PREDICTIONS
from fleet_trends_fixture import stamp

SIGNAL = "doc_staleness.summary.stale_pct"
SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
PAST, FUTURE = dt.date(2026, 11, 1), dt.date(2027, 5, 23)


def _level(predicted: str | None = None, review: dt.date | None = None) -> dict[str, Any]:
    extra = {"predicted": predicted, "review": review} if predicted else {}
    return {"level": 1, "truth": ["docs"], **extra, "criteria": [{"signal": SIGNAL, "op": "eq", "value": 0}]}


def _model(practice: dict[str, Any] | None = None, gate: dict[str, Any] | None = None) -> Any:
    def row(rid: str, level: dict[str, Any]) -> dict[str, Any]:
        return {"id": rid, "title": rid, "scope": "team", "aggregation": "min", "levels": [level]}
    return parse_levels({"version": "v1",
                         "rows": [row("g", gate or _level()), row("p", practice or _level())],
                         "steps": [{"step": 1, "name": "One"},
                                   {"step": 2, "name": "Two", "gates": [{"id": "gate", "row": "g", "level": 1}]}]})


DATA = {"doc_staleness": {"summary": {"stale_pct": 0}}}


# --- the tally -------------------------------------------------------------------------


def test_a_demoted_condition_is_listed_not_counted_and_the_step_holds() -> None:
    plain = lt.level_result(_model(), DATA)
    demoted = lt.level_result(_model(), DATA, demoted=["p.1"])
    cond = next(c for c in demoted["conditions"] if c["id"] == "p.1")
    assert cond["state"] == "met" and cond["counted"] is False and cond["disposition"] == lt.DEMOTED
    assert demoted["listed"] == ["p.1"] and demoted["counted"] == ["g.1"]
    assert (plain["tally"]["met"], demoted["tally"]["met"]) == (2, 1)
    assert demoted["step"] == plain["step"] == 2 and demoted["next_step"] == plain["next_step"]


def test_a_gate_condition_or_unknown_id_is_never_demoted() -> None:
    plain = lt.level_result(_model(), DATA)
    assert lt.level_result(_model(), DATA, demoted=["g.1", "nope.1"]) == plain
    assert lt.gate_conditions(_model()) == frozenset({"g.1"})


def test_an_owner_exclusion_wins_over_a_demotion() -> None:
    result = lt.level_result(_model(), DATA, [{"id": "p.1", "kind": "other"}], demoted=["p.1"])
    cond = next(c for c in result["conditions"] if c["id"] == "p.1")
    assert cond["disposition"] == lt.EXCLUDED_BY_OWNER


def test_fleet_levels_apply_the_demotions() -> None:
    row = {"fleet_row_version": FLEET_ROW_VERSION, "repo": "a", "group": "g", "status": "ok", "scans": DATA}
    levels = fleet_levels([row], _model(), toolkit_commit=None, plugin_version="3.0.0",  # type: ignore[list-item]
                          demoted=["p.1"])
    assert levels["repos"]["a"]["listed"] == ["p.1"]


# --- a repository can never demote -----------------------------------------------------


def test_only_the_fleet_passes_demotions_to_the_tally() -> None:
    callers = []
    for path in sorted(SCRIPTS.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "level_result"
                    and (len(node.args) > 3 or any(k.arg == "demoted" for k in node.keywords))):
                callers.append(path.relative_to(SCRIPTS).as_posix())
    assert callers == ["lib/fleet/fleet_level.py"]


@pytest.mark.parametrize("config", [
    '[exit_rule]\ndemoted = ["layer-1-liveness.1"]\n',
    '[checks]\ndemoted = ["layer-1-liveness.1"]\ndemote = ["layer-1-liveness.1"]\n',
    'demoted = ["layer-1-liveness.1"]\n',
    '[ai_usage]\ndemoted = ["layer-1-liveness.1"]\n',
])
def test_a_repository_config_cannot_introduce_a_demotion(tmp_path: Path, config: str) -> None:
    from lib.owner_exclusions import owner_exclusions_block
    from lib.run_setup import fleet_config

    def level(root: Path) -> Any:
        return lt.run_context_level({"fleet_config": fleet_config(root), "owner_exclusions": owner_exclusions_block(root),
                                     "observability": {"rung": 3}})

    bare = tmp_path / "bare"
    bare.mkdir()
    (tmp_path / ".assess").mkdir()
    (tmp_path / ".assess" / "config.toml").write_text(config, encoding="utf-8")
    result = level(tmp_path)
    assert not [c for c in result["conditions"] if c.get("disposition") == lt.DEMOTED]
    assert next(c for c in result["conditions"] if c["id"] == "layer-1-liveness.1")["state"] == "met"
    assert result == level(bare)


# --- the decision ----------------------------------------------------------------------


def _effects(model: Any, met: list[float], unmet: list[float]) -> ce.ConditionEffectDoc:
    def row(repo: str, stale: int, share: float) -> dict[str, Any]:
        return {"fleet_row_version": FLEET_ROW_VERSION, "repo": repo, "group": "g", "status": "ok",
                "scans": {"doc_staleness": {"summary": {"stale_pct": stale}},
                          "agent_outcome": {"available": True, "revert_share": share, "rework_share": share}}}
    rows = [row(f"m{i}", 0, s) for i, s in enumerate(met)] + [row(f"u{i}", 5, s) for i, s in enumerate(unmet)]
    return ce.build_condition_effect(model, [ce.HistoryRun("2026-11-23", rows)])  # type: ignore[list-item]


NO_EFFECT = ([0.1, 0.3] * 5, [0.15, 0.25] * 5)
EFFECT = ([0.0, 0.02] * 5, [0.4, 0.42] * 5)


def _decide(practice: dict[str, Any], samples: tuple[list[float], list[float]] = NO_EFFECT,
            gate: dict[str, Any] | None = None) -> cd.DemotionsDoc:
    model = _model(practice, gate)
    return cd.decide(model, _effects(model, *samples), "2026-11-23")


@pytest.mark.parametrize(("practice", "samples", "status"), [
    (_level("lower_revert", FUTURE), NO_EFFECT, "not_due"),
    (_level("lower_revert", PAST), NO_EFFECT, "demoted"),
    (_level("lower_revert", PAST), EFFECT, "effect_detected"),
    (_level("lower_rework", PAST), NO_EFFECT, "demoted"),
    (_level("none", PAST), NO_EFFECT, "demoted"),
    (_level("lower_revert", PAST), ([0.1, 0.3] * 4, [0.15, 0.25] * 5), "insufficient_evidence"),
    (_level("lower_revert", PAST), ([0.1], [0.2]), "insufficient_evidence"),
    (_level("lower_revert", PAST), ([0.0] * 10, [0.0] * 10), "insufficient_evidence"),  # no spread
])
def test_each_status(practice: dict[str, Any], samples: tuple[list[float], list[float]], status: str) -> None:
    doc = _decide(practice, samples)
    (entry,) = doc["conditions"]
    assert entry["id"] == "p.1" and entry["status"] == status
    assert doc["demoted"] == (["p.1"] if status == "demoted" else [])


def test_the_review_day_itself_is_due() -> None:
    model = _model(_level("lower_revert", dt.date(2026, 11, 23)))
    assert cd.decide(model, _effects(model, *NO_EFFECT), "2026-11-23")["demoted"] == ["p.1"]


def test_a_gate_condition_is_exempt_and_recorded_with_its_evidence() -> None:
    doc = _decide(_level(), gate=_level("lower_revert", PAST))
    (entry,) = doc["conditions"]
    assert (entry["id"], entry["status"], doc["demoted"]) == ("g.1", "gate_exempt", [])
    assert [e["measure"] for e in entry["evidence"]] == ["revert_share"]


def test_a_condition_without_a_review_date_is_not_listed() -> None:
    assert _decide(_level())["conditions"] == []


def test_the_evidence_is_the_measures_the_prediction_names() -> None:
    (entry,) = _decide(_level("none", PAST))["conditions"]
    assert [e["measure"] for e in entry["evidence"]] == ["revert_share", "rework_share"]
    assert set(cd.PREDICTION_MEASURES) == set(PREDICTIONS)


def test_the_record_is_kept_in_the_history_and_has_no_repository_id(tmp_path: Path) -> None:
    assert cd.DEMOTIONS_FILE in OPTIONAL_HISTORY_FILES
    text = cd.write_demotions(tmp_path, _decide(_level("lower_revert", PAST))).read_text(encoding="utf-8")
    assert '"m0"' not in text and json.loads(text)["demoted"] == ["p.1"]


# --- fleet.py --------------------------------------------------------------------------


def test_fleet_writes_and_applies_the_demotions(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from test_fleet_cli import fleet_cli

    model = _model(_level("lower_revert", PAST))
    rows = [{"fleet_row_version": FLEET_ROW_VERSION, "repo": f"{side}{i}", "group": "g", "status": "ok",
             "scans": {"doc_staleness": {"summary": {"stale_pct": stale}},
                       "agent_outcome": {"available": True, "revert_share": share, "rework_share": share}}}
            for side, stale, shares in (("m", 0, NO_EFFECT[0]), ("u", 5, NO_EFFECT[1]))
            for i, share in enumerate(shares)]
    demoted = fleet_cli._condition_effect(tmp_path, model, stamp("2026-11-23", "v1"), rows, None, None)
    assert demoted == ["p.1"]
    assert json.loads((tmp_path / cd.DEMOTIONS_FILE).read_text(encoding="utf-8"))["demoted"] == ["p.1"]
    assert "fleet: exit rule: 1 condition(s) demoted to informational: p.1" in capsys.readouterr().err
    assert fleet_cli._condition_effect(tmp_path, None, stamp("2026-11-23", None), rows, None, None) == []
    assert not (tmp_path / cd.DEMOTIONS_FILE).exists()
