"""Tests for the method stamp: the rules hash and collector revisions, and comparing on them (issue #952)."""
from __future__ import annotations

import json
import tomllib
from datetime import date
from pathlib import Path
from typing import Any, cast

import lib.fleet.fleet_achievements as fa
import lib.fleet.fleet_method as fm
from fleet_data_fixture import LEVELS
from lib.fleet.fleet_achievements_runs import earlier_runs
from lib.fleet.fleet_compare import FleetRun, compare, comparability, comparison_lines
from lib.fleet.condition_demotion import with_demotions
from lib.fleet.fleet_stamp import FleetStamp, build_stamp
from lib.manifest.check_manifest import collectors, core_manifest
from lib.scoring.levels import MaturityModel, parse_levels
from lib.packs import PackIdentity
from test_fleet_rescore import BASE, DATES, KEY, NOW, V1, _data, _entry, _rows, _stamp


def _model(text: str) -> MaturityModel:
    return parse_levels(tomllib.loads(text))


# The same maturity-model version as V1, with docs 2 needing grade A (was A or B).
SAME_VERSION_NEW_RULES = _model(LEVELS.replace('value = ["A", "B"]', 'value = ["A"]'))


def _method(model: MaturityModel | None, **extra: object) -> FleetStamp:
    stamp = build_stamp([], toolkit_commit="abc", plugin_version="3.0.0", today=date(2026, 11, 22),
                        window="6mo", levels_version=model["version"] if model else None, counts_only=True,
                        paths_hashed=False, scans=None, model=model)
    return cast(FleetStamp, {**stamp, **extra})


def _legacy(stamp: FleetStamp) -> FleetStamp:
    """``stamp`` as a toolkit before #952 wrote it: neither method key."""
    return cast(FleetStamp, {k: v for k, v in stamp.items() if k not in ("rules_sha256", "collectors")})


# --- the hash and the revisions ---------------------------------------------------


def test_rules_hash_is_none_without_a_model_and_stable_for_one() -> None:
    assert fm.rules_sha256(None) is None
    digest = fm.rules_sha256(V1)
    assert digest is not None and len(digest) == 64 and digest == fm.rules_sha256(_model(LEVELS))


def test_rules_hash_moves_with_a_criterion_and_not_with_names_the_version_or_demotions() -> None:
    renamed = _model(LEVELS.replace('version = "fleet-data-1"', 'version = "other"')
                     .replace('title = "CI gate"', 'title = "Continuous integration"'))
    assert fm.rules_sha256(renamed) == fm.rules_sha256(V1)
    assert fm.rules_sha256(with_demotions(V1, ["docs.1"])) == fm.rules_sha256(V1)
    assert fm.rules_sha256(SAME_VERSION_NEW_RULES) != fm.rules_sha256(V1)


def test_rules_hash_reads_a_new_model_key_by_default() -> None:
    extra = cast(MaturityModel, {**V1, "future_key": [1]})
    assert fm.rules_sha256(extra) != fm.rules_sha256(V1)


def test_collector_revisions_list_every_core_collector_by_id() -> None:
    """Every core collector but the pack collector, whose identity is each pack's sha256 (#1273)."""
    revisions = fm.collector_revisions()
    manifest = {c["id"]: c["revision"] for c in collectors(core_manifest()) if c["id"] != fm.PACK_COLLECTOR}
    assert [c["id"] for c in revisions] == sorted(manifest)
    assert {c["id"]: c["revision"] for c in revisions} == manifest


def test_build_stamp_records_the_rules_and_collectors() -> None:
    stamp = _method(V1)
    assert stamp["rules_sha256"] == fm.rules_sha256(V1)
    assert stamp["collectors"] == fm.collector_revisions()
    assert _method(None)["rules_sha256"] is None


# --- comparability ----------------------------------------------------------------


def test_the_same_version_with_other_rules_is_refused_with_a_rules_reason() -> None:
    reasons, _notes = comparability(_method(V1), _method(SAME_VERSION_NEW_RULES))
    assert len(reasons) == 1 and reasons[0].startswith("maturity-model rules changed ")


def test_the_same_rules_compare_with_no_method_note() -> None:
    assert comparability(_method(V1), _method(V1)) == ([], [])


def test_an_earlier_stamp_without_the_rules_compares_with_a_note_never_as_a_refusal() -> None:
    reasons, notes = comparability(_legacy(_method(V1)), _method(SAME_VERSION_NEW_RULES))
    assert reasons == []
    assert notes == [fm.RULES_NOTE, fm.COLLECTORS_NOTE]


def test_two_stamps_without_the_method_keys_compare_as_before() -> None:
    assert comparability(_legacy(_method(V1)), _legacy(_method(V1))) == ([], [])


def test_runs_without_levels_take_no_rules_note() -> None:
    reasons, notes = comparability(_legacy(_method(None)), _method(None))
    assert reasons == [] and notes == [fm.COLLECTORS_NOTE]


def test_a_collector_revision_change_is_a_note_not_a_refusal() -> None:
    later = _method(V1)
    bumped = [{**c, "revision": c["revision"] + 1} if c["id"] == "core.ci_checks" else c
              for c in later["collectors"] or []]
    reasons, notes = comparability(_method(V1), cast(FleetStamp, {**later, "collectors": bumped}))
    revision = {c["id"]: c["revision"] for c in fm.collector_revisions()}["core.ci_checks"]
    assert reasons == []
    assert notes == [f"collector revisions changed: core.ci_checks {revision}->{revision + 1}"]


def test_compare_keeps_the_note_and_the_summary_prints_it() -> None:
    rows = _rows(BASE)
    earlier = FleetRun(_legacy(_method(V1)), rows, [])
    comparison = compare(earlier, FleetRun(_method(V1), rows, []))
    assert comparison["comparable"] and fm.RULES_NOTE in comparison["notes"]
    assert fm.RULES_NOTE in comparison_lines(comparison)


def test_a_rules_change_is_a_refusal_in_compare() -> None:
    rows = _rows(BASE)
    comparison = compare(FleetRun(_method(V1), rows, []), FleetRun(_method(SAME_VERSION_NEW_RULES), rows, []))
    assert not comparison["comparable"]
    assert comparison_lines(comparison)[0].startswith("not comparable: maturity-model rules changed")


# --- the achievements replay --------------------------------------------------------


def _with_rules(model: MaturityModel, date_: str) -> FleetStamp:
    return _stamp(date_, model, rules_sha256=fm.rules_sha256(model))


def test_an_entry_under_the_same_version_with_other_rules_is_rescored(tmp_path: Path) -> None:
    org = tmp_path / "org"
    for day in DATES:
        _entry(org, day, BASE, V1, rules_sha256=fm.rules_sha256(V1))
    new = SAME_VERSION_NEW_RULES
    runs = earlier_runs(org, _with_rules(new, NOW), frozenset(BASE), model=new, key=KEY)
    assert [r.run_id for r in runs] == list(DATES)
    assert all(r.stored is not None and r.rescored_rules == fm.rules_sha256(V1) for r in runs)
    assert all(r.data["run"]["rules_sha256"] == fm.rules_sha256(new) for r in runs)
    doc = fa.build_achievements(fa.HistoryRun(NOW, _data(NOW, BASE, new)), runs, new)
    assert doc["rescored_runs"] == list(DATES)
    lines = doc["recount"]["lines"] if doc["recount"] else []
    assert lines and all("(was " not in line["text"] for line in lines)  # one version: no "(was ...)"


def test_an_entry_without_the_rules_hash_is_read_as_stored_under_the_same_version(tmp_path: Path) -> None:
    org = tmp_path / "org"
    _entry(org, DATES[0], BASE, V1)  # the fixture stamp predates #952: no rules_sha256
    runs = earlier_runs(org, _with_rules(V1, NOW), frozenset(BASE), model=V1, key=KEY)
    assert [(r.run_id, r.stored) for r in runs] == [(DATES[0], None)]


def test_the_stamp_schema_accepts_old_and_new_stamps() -> None:
    import jsonschema

    schema: Any = json.loads((Path(__file__).resolve().parents[2] / "fleet" / "references" / "run.schema.json")
                             .read_text(encoding="utf-8"))
    jsonschema.validate(_method(V1), schema)
    jsonschema.validate(_legacy(_method(V1)), schema)


# --- same-day history and the trend chart ---------------------------------------------


def _kept(tmp_path: Path, stamp: FleetStamp) -> Path:
    from lib.fleet.fleet_stamp import RUN_FILE

    entry = tmp_path / "org" / "2026-11-22"
    entry.mkdir(parents=True)
    (entry / RUN_FILE).write_text(json.dumps(stamp), encoding="utf-8")
    return entry


def test_a_same_day_rerun_under_edited_rules_keeps_the_earlier_entry(tmp_path: Path) -> None:
    from lib.fleet.fleet_history import entry_conflict

    entry = _kept(tmp_path, _method(V1))
    assert entry_conflict(entry, _method(V1)) is None
    conflict = entry_conflict(entry, _method(SAME_VERSION_NEW_RULES))
    assert conflict is not None and "rules_sha256" in conflict


def test_a_same_day_entry_without_the_rules_hash_is_kept_apart_but_is_a_compare_baseline(tmp_path: Path) -> None:
    from lib.fleet.fleet_history import entry_conflict, latest_matching

    entry = _kept(tmp_path, _legacy(_method(V1)))
    assert entry_conflict(entry, _method(V1)) is not None
    assert latest_matching(tmp_path / "org", _method(V1)) == entry
    assert latest_matching(tmp_path / "org", _method(SAME_VERSION_NEW_RULES)) == entry


def test_a_recorded_rules_hash_must_match_for_a_compare_baseline(tmp_path: Path) -> None:
    from lib.fleet.fleet_history import latest_matching

    _kept(tmp_path, _method(V1))
    assert latest_matching(tmp_path / "org", _method(SAME_VERSION_NEW_RULES)) is None


def test_the_trend_breaks_at_a_rules_change_both_runs_recorded() -> None:
    import lib.fleet.fleet_trends as ft

    def run(label: str, rules: str | None) -> ft.TrendRun:
        point: ft.TrendRun = {"label": label, "levels_version": "v1", "means": {"g": 1.0}, "scaled": True}
        if rules is not None:
            point["rules_sha256"] = rules
        return point

    drawn = [run("a", None), run("b", "r1"), run("c", "r1"), run("d", "r2")]
    assert ft._segments(drawn, "g") == [[0, 1, 2], [3]]  # unrecorded -> r1 is no break
    assert ft.layout(drawn).breaks == [3]


def test_an_unrecorded_rules_hash_is_not_native_credit_across_two_rescored_entries(
        tmp_path: Path, monkeypatch: Any) -> None:
    from test_fleet_rescore import V2

    org = tmp_path / "org"
    _entry(org, DATES[0], BASE, V1)  # before #952: no hash
    _entry(org, DATES[1], BASE, V1, rules_sha256=fm.rules_sha256(V1))
    _entry(org, DATES[2], BASE, V1, rules_sha256=fm.rules_sha256(V1))
    runs = earlier_runs(org, _with_rules(V2, NOW), frozenset(BASE), model=V2, key=KEY)
    assert [r.rescored_rules for r in runs] == [None, fm.rules_sha256(V1), fm.rules_sha256(V1)]
    seen: list[object] = []
    step = fa._Replay.step

    def spy(self: Any, run_id: str, prev_id: str, *args: Any) -> None:
        seen.append((run_id, args[-1] is not None))
        step(self, run_id, prev_id, *args)

    monkeypatch.setattr(fa._Replay, "step", spy)
    fa.build_achievements(fa.HistoryRun(NOW, _data(NOW, BASE, V2)), runs, V2)
    # rescored cells are passed (not native) only on the step whose two entries' rules differ
    assert seen[:2] == [(DATES[1], True), (DATES[2], False)]


def test_a_profile_resolved_model_changes_the_hash() -> None:
    # fleet.py turns --profile into its --levels file (lib/profiles.py, #1170) and hashes the loaded model
    from lib.scoring.levels import load_levels
    from lib.profiles import profile_path

    default = load_levels(Path(__file__).resolve().parents[1] / "levels" / "default.toml")
    handover = load_levels(profile_path("handover"))
    citizen = load_levels(profile_path("citizen"))
    hashes = {fm.rules_sha256(default), fm.rules_sha256(handover), fm.rules_sha256(citizen)}
    assert len(hashes) == 3 and None not in hashes
    assert _method(handover)["rules_sha256"] == fm.rules_sha256(handover)


# --- practice packs (#1273) ----------------------------------------------------------

TEAM: PackIdentity = {"name": "team", "version": "1", "sha256": "c" * 64}


def test_the_pack_collector_is_named_by_sha256_not_a_collector_revision() -> None:
    assert fm.PACK_COLLECTOR in {c["id"] for c in collectors(core_manifest())}
    assert fm.PACK_COLLECTOR not in {c["id"] for c in fm.collector_revisions()}


def test_a_stamp_without_packs_is_the_stamp_before_packs() -> None:
    stamp = _method(V1)
    assert "packs" not in stamp
    with_packs = build_stamp([], toolkit_commit="abc", plugin_version="3.0.0", today=date(2026, 11, 22),
                             window="6mo", levels_version=V1["version"], counts_only=True,
                             paths_hashed=False, scans=None, model=V1, packs=[TEAM])
    assert with_packs["packs"] == [TEAM]
    assert {k: v for k, v in with_packs.items() if k != "packs"} == stamp


def test_pack_reasons_compare_name_and_sha_in_order() -> None:
    other = {**TEAM, "name": "other"}  # a plain mapping, as a stamp read from disk holds
    assert fm.pack_reasons({"packs": [TEAM, other]}, {"packs": [TEAM, other]}) == []
    assert fm.pack_reasons({"packs": [TEAM, other]}, {"packs": [other, TEAM]}) != []
    assert fm.pack_reasons({}, {"packs": []}) == []
    assert fm.pack_reasons({}, {"packs": [TEAM]}) == ["practice packs changed none->team@cccccccccccc"]
    assert fm.rule_reasons({"packs": [TEAM]}, {}) == ["practice packs changed team@cccccccccccc->none"]


# --- pack rows layered onto the model (#1274) ------------------------------------------


def _pack_with_rows(name: str, row_ids: tuple[str, ...]) -> Any:
    from lib.packs import parse_pack
    return parse_pack({
        "pack": {"name": name, "version": "1"},
        "rows": [{"id": rid, "title": rid, "scope": "team", "aggregation": "min",
                  "levels": [{"level": 1, "truth": ["docs"],
                              "criteria": [{"signal": f"pack.{name}.c", "op": "eq", "value": True}]}]}
                 for rid in row_ids]})


def test_adding_one_pack_row_changes_the_rules_hash() -> None:
    from lib.pack_layering import layer_packs
    base = fm.rules_sha256(V1)
    no_rows = {**_pack_with_rows("team", ("glossary",)), "rows": []}
    assert fm.rules_sha256(layer_packs(V1, "levels.toml", [no_rows])) == base
    one = layer_packs(V1, "levels.toml", [_pack_with_rows("team", ("glossary",))])
    two = layer_packs(V1, "levels.toml", [_pack_with_rows("team", ("glossary", "owner"))])
    assert len({base, fm.rules_sha256(one), fm.rules_sha256(two)}) == 3


def test_moving_a_pack_row_to_another_pack_changes_the_rules_hash() -> None:
    from lib.pack_layering import layer_packs
    company = _pack_with_rows("company", ("glossary",))
    team = _pack_with_rows("team", ("owner",))
    moved = {**_pack_with_rows("team", ("owner",)), "rows": [*company["rows"], *team["rows"]]}
    first = layer_packs(V1, "levels.toml", [company, team])
    second = layer_packs(V1, "levels.toml", [{**company, "rows": []}, moved])
    assert [r["id"] for r in first["rows"]] == [r["id"] for r in second["rows"]]
    assert fm.rules_sha256(first) != fm.rules_sha256(second)
