"""lib/fleet/condition_effect.py: each condition's outcome effect over the fleet history (#1086)."""
from __future__ import annotations

import datetime as dt
import json
import math
from pathlib import Path
from typing import Any

import pytest

import lib.fleet.condition_effect as ce
from lib.fleet.runner import FLEET_ROW_VERSION
from lib.levels import parse_levels
from fleet_trends_fixture import stamp

SIGNAL = "doc_staleness.summary.stale_pct"


def _model() -> Any:
    return parse_levels({"version": "v1", "rows": [
        {"id": "r", "title": "R", "scope": "team", "aggregation": "min", "levels": [
            {"level": 1, "truth": ["docs"], "predicted": "lower_revert", "review": dt.date(2027, 5, 23),
             "criteria": [{"signal": SIGNAL, "op": "eq", "value": 0}]}]}]})


def _row(repo: str, stale: Any, revert: Any, rework: Any = None, *, status: str = "ok",
         available: bool = True) -> dict[str, Any]:
    outcome: dict[str, Any] = ({"available": True, "revert_share": revert, "rework_share": rework}
                               if available else {"available": False, "reason": "no_git: git missing"})
    return {"fleet_row_version": FLEET_ROW_VERSION, "repo": repo, "group": "g", "status": status,
            "scans": {"doc_staleness": {"summary": {"stale_pct": stale}}, "agent_outcome": outcome}}


# math.isclose, not pytest.approx: another test leaves a numpy stub in sys.modules on the
# same xdist worker, and approx calls numpy when it is imported.

# --- the Student t quantile ------------------------------------------------------------


@pytest.mark.parametrize(("df", "expected"), [
    (1, 12.706205), (9, 2.262157), (18, 2.100922), (30, 2.042272), (1e7, 1.959964),
])
def test_t_quantile_matches_the_published_table(df: float, expected: float) -> None:
    assert math.isclose(ce.t_quantile(0.975, df), expected, abs_tol=1e-5)


def test_t_cdf_is_symmetric() -> None:
    assert math.isclose(ce.t_cdf(0.0, 5), 0.5, abs_tol=1e-12)
    assert math.isclose(ce.t_cdf(-1.3, 7), 1 - ce.t_cdf(1.3, 7), abs_tol=1e-12)


# --- the estimate ----------------------------------------------------------------------


def test_welch_with_equal_spread_uses_the_pooled_degrees_of_freedom() -> None:
    met, unmet = [0.0, 0.2] * 5, [0.3, 0.5] * 5  # equal n and variance: df = 2(n - 1) = 18
    effect = ce.welch("revert_share", met, unmet)
    var = sum((v - 0.1) ** 2 for v in met) / 9
    half = 2.100922 * (2 * var / 10) ** 0.5
    assert effect["effect"] is not None and math.isclose(effect["effect"], -0.3, abs_tol=1e-9)
    assert effect["interval"] is not None
    low, high = effect["interval"]
    assert math.isclose(low, -0.3 - half, abs_tol=1e-5) and math.isclose(high, -0.3 + half, abs_tol=1e-5)
    assert effect["includes_zero"] is False and effect["low_confidence"] is False
    assert (effect["met"], effect["unmet"], effect["reason"]) == (10, 10, None)


def test_overlapping_groups_include_zero() -> None:
    effect = ce.welch("revert_share", [0.1, 0.3] * 5, [0.15, 0.25] * 5)
    assert effect["effect"] is not None and math.isclose(effect["effect"], 0.0, abs_tol=1e-9)
    assert effect["includes_zero"] is True


def test_nine_a_side_is_low_confidence_and_ten_is_not() -> None:
    assert ce.welch("m", [0.1, 0.2] * 5, [0.1, 0.2, 0.3] * 3)["low_confidence"] is True
    assert ce.welch("m", [0.1, 0.2] * 5, [0.1, 0.2] * 5)["low_confidence"] is False
    assert ce.MIN_PER_SIDE == 10 and ce.CONFIDENCE == 0.95


@pytest.mark.parametrize(("met", "unmet"), [([], []), ([0.1], [0.1, 0.2]), ([0.1, 0.2], [])])
def test_under_two_a_side_has_no_interval(met: list[float], unmet: list[float]) -> None:
    effect = ce.welch("m", met, unmet)
    assert effect["interval"] is None and effect["effect"] is None and effect["includes_zero"] is None
    assert effect["reason"] == ce.TOO_FEW and effect["low_confidence"] is True


def test_no_spread_gives_a_point_interval() -> None:
    same = ce.welch("m", [0.1, 0.1], [0.1, 0.1])
    apart = ce.welch("m", [0.0, 0.0], [0.2, 0.2])
    assert same["interval"] == [0.0, 0.0] and same["includes_zero"] is True
    assert apart["interval"] == [-0.2, -0.2] and apart["includes_zero"] is False


# --- the observations ------------------------------------------------------------------


def test_each_repository_gives_its_newest_observation() -> None:
    runs = [ce.HistoryRun("2026-11-01", [_row("a", 0, 0.5), _row("b", 5, 0.4)]),
            ce.HistoryRun("2026-11-08", [_row("a", 5, 0.2)])]  # a: newest is unmet, 0.2
    seen, repos = ce.observations(runs, _model())
    assert seen[("r.1", "revert_share")] == {"a": (False, 0.2), "b": (False, 0.4)}
    assert repos == 2


@pytest.mark.parametrize("row", [
    _row("a", 0, 0.5, status="failed"),
    _row("a", 0, 0.5, available=False),
    _row("a", 0, None),
    _row("a", None, 0.5),  # the condition is not measured
    _row("a", 0, True),  # a bool is not a share
])
def test_an_unknown_gives_no_observation(row: dict[str, Any]) -> None:
    seen, repos = ce.observations([ce.HistoryRun("2026-11-01", [row])], _model())  # type: ignore[list-item]
    assert ("r.1", "revert_share") not in seen and repos == 0


def test_a_null_share_keeps_the_earlier_observation_of_that_measure() -> None:
    runs = [ce.HistoryRun("2026-11-01", [_row("a", 0, 0.5, 0.1)]),
            ce.HistoryRun("2026-11-08", [_row("a", 0, None, 0.3)])]
    seen, _ = ce.observations(runs, _model())
    assert seen[("r.1", "revert_share")] == {"a": (True, 0.5)}
    assert seen[("r.1", "rework_share")] == {"a": (True, 0.3)}


# --- the document ----------------------------------------------------------------------


def _fleet(n_met: int, n_unmet: int) -> list[dict[str, Any]]:
    return ([_row(f"m{i}", 0, 0.1 + i / 100, 0.2) for i in range(n_met)]
            + [_row(f"u{i}", 5, 0.1 + i / 100, 0.2) for i in range(n_unmet)])


def test_the_document_carries_counts_and_no_repository_id(tmp_path: Path) -> None:
    doc = ce.build_condition_effect(_model(), [ce.HistoryRun("2026-11-23", _fleet(10, 10))])  # type: ignore[arg-type]
    assert doc["runs"] == ["2026-11-23"] and doc["repositories"] == 20
    assert (doc["min_per_side"], doc["confidence"], doc["measures"]) == (10, 0.95, ["revert_share", "rework_share"])
    (cond,) = doc["conditions"]
    assert (cond["id"], cond["predicted"], cond["review"]) == ("r.1", "lower_revert", "2027-05-23")
    revert, rework = cond["effects"]
    assert revert["includes_zero"] is True and revert["low_confidence"] is False
    assert rework["interval"] == [0.0, 0.0]
    path = ce.write_condition_effect(tmp_path, doc)
    text = path.read_text(encoding="utf-8")
    assert '"m0"' not in text and '"u0"' not in text
    assert json.loads(text) == doc


def test_the_same_history_gives_the_same_bytes(tmp_path: Path) -> None:
    runs = [ce.HistoryRun("2026-11-23", _fleet(3, 12))]  # type: ignore[arg-type]
    a = ce.write_condition_effect(tmp_path, ce.build_condition_effect(_model(), runs)).read_bytes()
    b = ce.write_condition_effect(tmp_path, ce.build_condition_effect(_model(), runs)).read_bytes()
    assert a == b


def test_today_one_thin_run_is_low_confidence_everywhere() -> None:
    doc = ce.build_condition_effect(_model(), [ce.HistoryRun("2026-11-23", _fleet(2, 1))])  # type: ignore[arg-type]
    assert all(e["low_confidence"] for c in doc["conditions"] for e in c["effects"])


# --- the history -----------------------------------------------------------------------


def _entry(org: Path, name: str, rows: list[dict[str, Any]], window: str = "6mo") -> None:
    entry = org / name
    entry.mkdir(parents=True)
    (entry / "run.json").write_text(json.dumps(stamp(name.split(".")[0], "v1", window)), encoding="utf-8")
    (entry / "repos.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def test_history_reads_comparable_earlier_entries_oldest_first(tmp_path: Path) -> None:
    org = tmp_path / "org"
    _entry(org, "2026-11-01", [_row("a", 0, 0.5)])
    _entry(org, "2026-11-08", [_row("a", 0, 0.4)], window="3mo")  # another window: not comparable
    _entry(org, "2026-11-15", [_row("a", 5, 0.3)])
    _entry(org, "2026-11-23", [_row("a", 5, 0.2)])  # this run's date: this run comes from memory
    (org / "2026-11-20").mkdir()  # no run.json: passed over
    runs = ce.history_runs(org, stamp("2026-11-23", "v1"), frozenset({"a"}))
    assert [r.scan_date for r in runs] == ["2026-11-01", "2026-11-15"]
    assert [r.rows[0]["repo"] for r in runs] == ["a", "a"]


def test_a_hashed_entry_under_another_salt_is_passed_over(tmp_path: Path) -> None:
    from lib.counts_only.content_free import hash_value

    org = tmp_path / "org"
    _entry(org, "2026-11-01", [_row(hash_value("grp/a", "salt-1"), 0, 0.5)])  # another salt
    _entry(org, "2026-11-08", [_row(hash_value("grp/a", "salt-2"), 0, 0.4)])  # this run's salt
    this = frozenset({hash_value("grp/a", "salt-2")})
    runs = ce.history_runs(org, stamp("2026-11-23", "v1"), this)
    assert [r.scan_date for r in runs] == ["2026-11-08"]


def test_a_missing_history_is_no_runs(tmp_path: Path) -> None:
    assert ce.history_runs(tmp_path / "none", stamp("2026-11-23", "v1"), frozenset()) == []


# --- fleet.py --------------------------------------------------------------------------


def test_fleet_writes_the_document_with_levels_and_reads_the_history(tmp_path: Path, monkeypatch: Any) -> None:
    from test_fleet import make_repo
    from test_fleet_cli import TEAM_LEVELS, fleet_cli

    root = tmp_path / "root"
    make_repo(root, "grpa/svc1", files={"CLAUDE.md": "rules\n"})
    make_repo(root, "grpb/lib1")
    monkeypatch.chdir(tmp_path)
    (tmp_path / "levels.toml").write_text(TEAM_LEVELS, encoding="utf-8")
    args = [str(root), "--levels", "levels.toml", "--history-dir", "hist"]
    assert fleet_cli.main([*args, "--out", "o1"]) == 0
    first = json.loads((tmp_path / "o1" / ce.CONDITION_EFFECT_FILE).read_text(encoding="utf-8"))
    assert len(first["runs"]) == 1 and first["conditions"]
    assert all(e["low_confidence"] for c in first["conditions"] for e in c["effects"])
    (today,) = (tmp_path / "hist" / "grpa+grpb").iterdir()
    earlier = today.parent / "2000-01-01"
    earlier.mkdir()
    for name in ("run.json", "repos.jsonl"):
        (earlier / name).write_bytes((today / name).read_bytes())
    stamp_doc = json.loads((earlier / "run.json").read_text(encoding="utf-8"))
    (earlier / "run.json").write_text(json.dumps({**stamp_doc, "scan_date": "2000-01-01"}), encoding="utf-8")
    assert fleet_cli.main([*args, "--out", "o2"]) == 0
    second = json.loads((tmp_path / "o2" / ce.CONDITION_EFFECT_FILE).read_text(encoding="utf-8"))
    assert second["runs"] == ["2000-01-01", first["runs"][0]]


def test_fleet_without_levels_removes_a_stale_document(tmp_path: Path) -> None:
    from test_fleet_cli import fleet_cli

    (tmp_path / ce.CONDITION_EFFECT_FILE).write_text("{}", encoding="utf-8")
    fleet_cli._condition_effect(tmp_path, None, stamp("2026-11-23", None), [], None, None)
    assert not (tmp_path / ce.CONDITION_EFFECT_FILE).exists()


def test_a_failure_never_fails_the_run(tmp_path: Path, monkeypatch: Any, capsys: Any) -> None:
    from test_fleet_cli import fleet_cli

    def boom(*_args: object) -> None:
        raise ValueError("bug")
    monkeypatch.setattr(fleet_cli, "build_condition_effect", boom)
    (tmp_path / ce.CONDITION_EFFECT_FILE).write_text("{}", encoding="utf-8")  # an earlier run's
    fleet_cli._condition_effect(tmp_path, _model(), stamp("2026-11-23", "v1"), [], None, None)
    assert "fleet: condition_effect.json not written (ValueError)" in capsys.readouterr().err
    assert not (tmp_path / ce.CONDITION_EFFECT_FILE).exists()
