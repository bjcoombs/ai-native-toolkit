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
from lib.fleet.fleet_compare import FleetRun, compare, comparability, summary_lines
from lib.fleet.condition_demotion import with_demotions
from lib.fleet.fleet_stamp import FleetStamp, build_stamp
from lib.manifest.check_manifest import collectors, core_manifest
from lib.levels import MaturityModel, parse_levels
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
    revisions = fm.collector_revisions()
    manifest = {c["id"]: c["revision"] for c in collectors(core_manifest())}
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
    assert fm.RULES_NOTE in summary_lines(comparison)


def test_a_rules_change_is_a_refusal_in_compare() -> None:
    rows = _rows(BASE)
    comparison = compare(FleetRun(_method(V1), rows, []), FleetRun(_method(SAME_VERSION_NEW_RULES), rows, []))
    assert not comparison["comparable"]
    assert summary_lines(comparison)[0].startswith("not comparable: maturity-model rules changed")


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
