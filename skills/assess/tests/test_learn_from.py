"""The sibling units that already hold a unit's next gate (lib/fleet/fleet_learn_from.py, #1155).

In a monorepo where package A holds ``ci_runs_tests`` and package B does not, B's row reads
"Learn from: A" in the ``/assess`` unit roll-up and in the fleet unit rows. The names are
written in full output only: counts-only drops them and ``--hash-paths`` hashes them.

Per-unit CI is not read by a real scan yet (every unit inherits the root CI, ``unit_levels``),
so the fixtures hand-write the readings, as ``tests/fleet_data_fixture.py`` does.
"""
from __future__ import annotations

import json
from typing import Any

import lib.fleet.fleet_data as fd
from fleet_data_fixture import SALT, build
from lib import unit_rollup as ur
from lib.counts_only.content_free import counts_only, hash_value
from lib.fleet.fleet_data_units import unit_rows
from lib.fleet.fleet_learn_from import MAX_NAMES, NextGate, Sibling, holds, learn_from, line, next_gate
from lib.fleet.fleet_opportunities_section import opportunities_section
from lib.fleet.fleet_publish import check_strings
from lib.level_tally import default_model

INSTR = "layer-0-instructions.1"
CI = "layer-5-ci.1"
A, B, C = "packages/alpha-pkg", "packages/bravo-pkg", "services/charlie-svc"
NAMES = (A, B, C)
CI_GATE = NextGate("ci_runs_tests", (CI,))


def _sib(name: str, state: str = "met", kind: str | None = "package", eco: str | None = "python") -> Sibling:
    return Sibling(name, kind, eco, {CI: state})


# --- choosing the siblings ------------------------------------------------------

def test_only_siblings_whose_gate_conditions_all_read_met_hold_it() -> None:
    me = _sib("me", "unmet")
    sibs = [me, _sib("a"), _sib("b", "unmet"), _sib("c", "not_measured"), Sibling("d", None, None, {})]
    assert learn_from(me, CI_GATE, sibs) == (["a"], 1)
    assert not holds({CI: "met"}, NextGate("x", ()))  # a gate with no condition never holds


def test_same_ecosystem_and_kind_first_then_by_name_capped_at_three() -> None:
    me = _sib("me", "unmet", kind="skill", eco="python")
    sibs = [me, _sib("z-both", kind="skill", eco="python"), _sib("y-eco", kind="package", eco="python"),
            _sib("x-kind", kind="skill", eco="node"), _sib("w-none", kind=None, eco=None),
            _sib("v-both", kind="skill", eco="python")]
    names, holders = learn_from(me, CI_GATE, sibs)
    assert names == ["v-both", "z-both", "y-eco"] and len(names) == MAX_NAMES and holders == 5
    # the order is the same whatever order the siblings come in
    assert learn_from(me, CI_GATE, list(reversed(sibs))) == (names, holders)
    # an unknown ecosystem or kind matches nothing: plain name order
    assert learn_from(Sibling("me", None, None, {}), CI_GATE, sibs)[0] == ["v-both", "w-none", "x-kind"]


def test_only_an_unmet_gate_the_owner_did_not_exclude_is_one_to_learn() -> None:
    model = default_model()
    unmet = {"step": 3, "blockers": [{"kind": "gate", "gate": "ci_runs_tests", "condition": CI, "state": "unmet"}]}
    assert next_gate(unmet, model) == CI_GATE
    excluded = {"step": 3, "blockers": [{**unmet["blockers"][0], "disposition": "excluded_by_owner"}]}  # type: ignore[index]
    not_measured = {"step": 3, "blockers": [{**unmet["blockers"][0], "state": "not_measured"}]}  # type: ignore[index]
    assert next_gate(excluded, model) is None and next_gate(not_measured, model) is None
    assert next_gate(None, model) is None and next_gate({"step": 3, "blockers": []}, model) is None
    assert line([A, B]) == f"Learn from: {A}, {B}"


# --- the /assess unit roll-up -----------------------------------------------------

def _unit(path: str, ci: str) -> dict[str, Any]:
    """A Step 2 unit: the instruction file held; CI met (holds Step 3) or unmet."""
    blockers = [] if ci == "met" else [{"kind": "gate", "gate": "ci_runs_tests", "condition": CI, "state": ci}]
    return {"path": path, "step": 3 if ci == "met" else 2,
            "next_step": {"step": 4 if ci == "met" else 3, "blockers": blockers or [
                {"kind": "gate", "gate": "coverage_gate", "condition": "layer-6-coverage.1", "state": "unmet"}]},
            "tally": {"met": 0, "measured": 0, "not_measured": 0, "verified": 0},
            "conditions": [{"id": INSTR, "state": "met", "counted": True, "origin": "inherited"},
                           {"id": CI, "state": ci, "counted": True, "origin": "unit"}],
            "inherited": []}


def _levels() -> dict[str, Any]:
    units = [_unit(A, "met"), _unit(B, "unmet"), _unit(C, "unmet")]
    return {"available": True, "unit_count": 3, "unit_count_reason": None, "levels_version": "x", "units": units}


UNITS = {"available": True, "units": [{"path": A, "kind": "package", "ecosystem": "python"},
                                      {"path": B, "kind": "package", "ecosystem": "python"},
                                      {"path": C, "kind": "package", "ecosystem": "go"}]}


def test_the_rollup_names_the_sibling_that_holds_the_next_gate() -> None:
    block = ur.unit_rollup(_levels(), units_block=UNITS)
    assert block["learn_from"] == [{"unit": B, "gate": "ci_runs_tests", "holders": 1, "names": [A]},
                                   {"unit": C, "gate": "ci_runs_tests", "holders": 1, "names": [A]}]
    assert f"{B}: CI turns red when a check breaks. Learn from: {A}" in block["lines"]
    assert f"Learn from: {A}" in block["markdown"]
    # A's next gate (coverage) is held by no sibling: no line for A
    assert not any(line_.startswith(f"{A}:") for line_ in block["lines"])


def test_the_rollup_without_a_units_block_still_names_the_holder() -> None:
    assert ur.unit_rollup(_levels())["learn_from"][0]["names"] == [A]


def test_counts_only_rollup_names_no_unit_and_hashed_keeps_the_join_key() -> None:
    ctx = {"unit_rollup": ur.unit_rollup(_levels(), units_block=UNITS)}
    plain = counts_only(ctx)["unit_rollup"]
    text = json.dumps(plain)
    assert not any(name in text for name in NAMES) and "Learn from" not in text
    assert [e["holders"] for e in plain["learn_from"]] == [1, 1]  # the count survives
    hashed = counts_only(ctx, hash_paths=True, salt=SALT)["unit_rollup"]
    assert not any(name in json.dumps(hashed) for name in NAMES)
    entry = hashed["learn_from"][0]
    assert entry["unit"] == hash_value(B, SALT) and entry["names"] == [hash_value(A, SALT)]


# --- the fleet unit rows ------------------------------------------------------------

def _scans() -> dict[str, Any]:
    """A repository holding its instruction file but not CI; package A runs its own CI."""
    ci = {"available": True, "enforced_check_runs": True}
    units = [{"path": p, "kind": "package", "blocks": {"ci_checks": ci} if p == A else {},
              "conditions": [{"id": CI, "origin": "unit"}]} for p in NAMES]
    return {"layer_grades": {"available": True, "instruction_file_present": True},
            "ci_checks": {"available": True, "enforced_check_runs": False},
            "units": UNITS,
            "unit_readings": {"available": True, "unit_count": 3, "unit_count_reason": None,
                              "unit_blocks": ["ci_checks"], "units": units}}


def test_fleet_row_b_reads_learn_from_a() -> None:
    rows = {r["key"]: r for r in unit_rows(default_model(), _scans()) or []}
    assert rows[B]["next_blocker"] == {"gate": "ci_runs_tests", "state": "unmet", "disposition": None}
    assert rows[B]["learn_from"] == [A] and rows[C]["learn_from"] == [A]
    assert rows[A]["learn_from"] == []


def test_fleet_rows_name_the_siblings_only_in_full_or_hashed() -> None:
    rows = unit_rows(default_model(), _scans())
    full = fd.public_unit_rows(rows, fd.Privacy(full=True)) or []
    assert [r["learn_from"] for r in full] == [[], [A], [A]]
    plain = fd.public_unit_rows(rows, fd.Privacy()) or []
    assert [r["learn_from"] for r in plain] == [None, None, None]
    assert not any(name in json.dumps(plain) for name in NAMES)
    assert check_strings([{"unit_rows": plain}], "repos", hashed=False) == []
    hashed = fd.public_unit_rows(rows, fd.Privacy(salt=SALT)) or []
    assert hashed[1]["learn_from"] == [hash_value(A, SALT)] == [hashed[0]["key"]]
    assert not any(name in json.dumps(hashed) for name in NAMES)
    assert check_strings([{"unit_rows": hashed}], "repos", hashed=True) == []
    # a clear sibling name in a published bundle is refused
    assert check_strings([{"unit_rows": full}], "repos", hashed=True) == [
        "repos.unit_rows.key: written only hashed, under --hash-paths",
        "repos.unit_rows.learn_from: written only hashed, under --hash-paths"]


def test_the_rollup_shows_five_lines_and_counts_the_rest() -> None:
    units = [_unit("holder", "met")] + [_unit(f"pkg-{n}", "unmet") for n in range(7)]
    block = ur.unit_rollup({**_levels(), "unit_count": 8, "units": units})
    assert len(block["learn_from"]) == 7  # the block lists every entry
    learn = [x for x in block["lines"] if "Learn from:" in x]
    assert len(learn) == ur.LEARN_LINES == 5
    assert block["lines"][-1] == "2 more units have a sibling unit that holds their next gate"


# --- fleet repositories and report.md ---------------------------------------------------

def test_a_repository_learns_from_the_fleet_repositories_that_hold_its_gate() -> None:
    full = {r["key"]: r for r in build(fd.Privacy(full=True))["repos"]}
    # proj2 is held back by ci; proj3 runs ci (its own next gate, docs, is not measured)
    assert full["grp/sub/proj2"]["learn_from"] == ["grp/other/proj3"]
    assert full["grp/other/proj3"]["learn_from"] == []  # not measured: nothing to learn
    assert full["grp/sub/proj1"]["learn_from"] == []  # excluded by the owner
    assert full["grp/proj4"]["learn_from"] is None  # failed: not measured
    plain = build(fd.Privacy())
    assert all(r["learn_from"] is None for r in plain["repos"])
    hashed = {r["key"]: r for r in build(fd.Privacy(salt=SALT))["repos"]}
    assert hashed[hash_value("grp/sub/proj2", SALT)]["learn_from"] == [hash_value("grp/other/proj3", SALT)]
    assert check_strings(build(fd.Privacy(salt=SALT))["repos"], "repos", hashed=True) == []
    assert check_strings(plain["repos"], "repos", hashed=False) == []


def test_report_lists_who_to_learn_from_only_where_names_are_written() -> None:
    section = opportunities_section(build(fd.Privacy(full=True)), None) or ""
    assert "**Learn from.**" in section
    assert "- `grp/sub/proj2`: the ci gate holds. Learn from: `grp/other/proj3`." in section
    assert "- `grp/sub/proj2` unit `apps/web`: the docs gate holds. Learn from: `apps/api`." in section
    plain = opportunities_section(build(fd.Privacy()), None) or ""
    assert "Not shown: counts-only output names no repository or unit to learn from." in plain
    assert "apps/" not in plain
    hashed = opportunities_section(build(fd.Privacy(salt=SALT)), None) or ""
    assert f"Learn from: `{hash_value('apps/api', SALT)}`" in hashed and "apps/" not in hashed
