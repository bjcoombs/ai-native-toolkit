"""Owner exclusions of conditions (lib/owner_exclusions.py, lib/level_tally.py, #910).

An excluded condition reads not_applicable with disposition excluded_by_owner,
leaves the denominator, never earns, and never holds a Step gate. An entry with
no reason, an unknown kind or an unknown id is skipped with a warning, so the
condition stays in force. A run without [checks] is unchanged.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

import lib.level_tally as lt
from lib import core_scans, headline as hl
from lib.counts_only.content_free import counts_only
from lib.owner_exclusions import KINDS, PROBLEMS, owner_exclusions_block, parse_exclusions
from lib.scan_registry import SCANS
from test_level_tally import (  # noqa: F401 - estate is a fixture
    CI, DOC, INSTR, SCRIPTS, STEPS, _eq, _fleet, _fresh, _model, _row, _state, _stepped, estate,
)

KNOWN = frozenset({"fresh.1", "fresh.2", "ci.1", "instr.1"})


def _exclude(cid: str, kind: str = "measured_elsewhere", **extra: Any) -> dict[str, Any]:
    return {"id": cid, "kind": kind, "reason": "Measured in the release repository.", "source": "repo", **extra}


def _config(*entries: Any) -> dict[str, Any]:
    return {"checks": {"exclude": list(entries)}}


# --- parsing ------------------------------------------------------------------

def test_an_entry_with_id_and_reason_is_in_force_with_its_provenance():
    entry = {"id": "ci.1", "reason": " CI runs elsewhere. ", "kind": "measured_elsewhere",
             "excluded_by": "ben", "excluded_at": "2026-11-22", "plugin_version": "2.2.0"}
    block = parse_exclusions(_config(entry), KNOWN)
    assert block["exclusions"] == [{"id": "ci.1", "kind": "measured_elsewhere", "reason": "CI runs elsewhere.",
                                    "source": "repo", "excluded_by": "ben", "excluded_at": "2026-11-22",
                                    "version": "2.2.0"}]
    assert block["skipped"] == []
    assert block["disclosures"] == [
        "`ci.1` excluded by owner: CI runs elsewhere. (measured elsewhere, by ben on 2026-11-22, plugin v2.2.0)"]


def test_an_unquoted_toml_date_is_kept_as_iso_text():
    import tomllib

    config = tomllib.loads('[checks]\nexclude = [{ id = "ci.1", reason = "r", excluded_at = 2026-11-22 }]\n')
    block = parse_exclusions(config, KNOWN)
    assert block["exclusions"][0]["excluded_at"] == "2026-11-22"
    assert block["disclosures"] == ["`ci.1` excluded by owner: r (other on 2026-11-22)"]


def test_kind_defaults_to_other_and_provenance_is_optional():
    block = parse_exclusions(_config({"id": "ci.1", "reason": "r"}), KNOWN)
    assert block["exclusions"][0]["kind"] == "other"
    assert block["exclusions"][0]["excluded_by"] is None
    assert block["disclosures"] == ["`ci.1` excluded by owner: r (other)"]


@pytest.mark.parametrize("entry, problem", [
    ("ci.1", "not_a_table"),
    ({"reason": "r"}, "no_id"),
    ({"id": "ci.1"}, "no_reason"),
    ({"id": "ci.1", "reason": "   "}, "no_reason"),
    ({"id": "ci.1", "reason": "r", "kind": "waived"}, "unknown_kind"),
    ({"id": "ci.1", "reason": "r", "kind": ""}, "unknown_kind"),
    ({"id": "ci.1", "reason": "r", "kind": 5}, "unknown_kind"),
    ({"id": "nope.1", "reason": "r"}, "unknown_condition"),
])
def test_a_bad_entry_is_skipped_and_the_condition_stays_in_force(entry, problem):
    block = parse_exclusions(_config(entry), KNOWN)
    assert block["exclusions"] == [] and [s["problem"] for s in block["skipped"]] == [problem]
    assert problem in PROBLEMS


def test_a_repeated_id_keeps_its_first_entry():
    block = parse_exclusions(_config({"id": "ci.1", "reason": "a", "kind": "false_positive"},
                                     {"id": "ci.1", "reason": "b"}), KNOWN)
    assert [e["kind"] for e in block["exclusions"]] == ["false_positive"]
    assert block["skipped"] == [{"index": 1, "id": "ci.1", "problem": "duplicate"}]


@pytest.mark.parametrize("config", [{}, {"checks": {}}, {"checks": {"other": 1}}])
def test_no_exclude_list_excludes_nothing_silently(config):
    assert parse_exclusions(config, KNOWN) == {"exclusions": [], "skipped": [], "disclosures": []}


@pytest.mark.parametrize("config, named", [
    ({"checks": "x"}, None),
    ({"checks": {"exclude": "ci.1"}}, None),
    ({"checks": {"exclude": {"id": "ci.1", "reason": "r"}}}, "ci.1"),  # one inline table, not a list
])
def test_a_malformed_section_is_skipped_visibly_and_excludes_nothing(config, named):
    block = parse_exclusions(config, KNOWN)
    assert block["exclusions"] == []
    assert block["skipped"] == [{"index": 0, "id": named, "problem": "not_a_list"}]


def test_every_kind_is_accepted():
    block = parse_exclusions(_config(*({"id": f"fresh.{n}", "reason": "r", "kind": k}
                                       for n, k in enumerate(KINDS[:2], 1))), KNOWN)
    assert [e["kind"] for e in block["exclusions"]] == list(KINDS[:2])
    for kind in KINDS:
        assert parse_exclusions(_config({"id": "ci.1", "reason": "r", "kind": kind}), KNOWN)["skipped"] == []


def test_the_block_reads_the_repo_config_and_warns_on_skipped(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    (tmp_path / ".assess").mkdir()
    (tmp_path / ".assess" / "config.toml").write_text(
        '[checks]\nexclude = [\n'
        '  { id = "layer-6-coverage.1", kind = "measured_elsewhere", reason = "Release repo gates coverage." },\n'
        '  { id = "layer-5-ci.1" },\n]\n', encoding="utf-8")
    block = core_scans.owner_exclusions(tmp_path)
    assert [e["id"] for e in block["exclusions"]] == ["layer-6-coverage.1"]
    assert block["skipped"] == [{"index": 1, "id": "layer-5-ci.1", "problem": "no_reason"}]
    assert "exclude[1] (layer-5-ci.1) skipped: no reason; the condition stays in force" in capsys.readouterr().err


def test_a_repo_without_config_has_an_empty_block(tmp_path: Path):
    assert owner_exclusions_block(tmp_path) == {"exclusions": [], "skipped": [], "disclosures": []}


# --- the tally ----------------------------------------------------------------

def test_an_excluded_met_condition_neither_earns_nor_counts():
    model = _model(_row("fresh", [{"signal": DOC, "op": "lte", "value": 50}]))
    result = lt.level_result(model, _fresh(), [_exclude("fresh.1")])
    cond = result["conditions"][0]
    assert (cond["state"], cond["counted"], cond["disposition"], cond["exclusion_kind"]) == (
        "not_applicable", False, "excluded_by_owner", "measured_elsewhere")
    assert result["tally"] == {"met": 0, "measured": 0, "not_measured": 0}
    assert result["counted"] == [] and result["listed"] == []
    assert result["excluded"] == [{"id": "fresh.1", "kind": "measured_elsewhere", "source": "repo"}]


def test_an_excluded_unmet_condition_leaves_the_denominator():
    model = _model(_row("fresh", [{"signal": DOC, "op": "lte", "value": 50}]),
                   _row("other", [{"signal": DOC, "op": "lte", "value": 50}]))
    result = lt.level_result(model, _fresh(80), [_exclude("fresh.1")])
    assert result["tally"] == {"met": 0, "measured": 1, "not_measured": 0}


def test_an_excluded_unmeasured_condition_leaves_not_measured():
    model = _model(_row("fresh", [{"signal": DOC, "op": "lte", "value": 50}]))
    result = lt.level_result(model, {}, [_exclude("fresh.1")])
    assert result["tally"]["not_measured"] == 0 and result["not_measured"] == []


def test_a_dependent_of_an_excluded_condition_is_not_applicable_too():
    model = _model(_row("ci", [_eq(CI)]),
                   _row("fresh", [{"signal": DOC, "op": "lte", "value": 50}], l1={"requires": ["ci.1"]}))
    signals = {**_fresh(), "ci_checks": {"test_or_lint_configured": True}}
    result = lt.level_result(model, signals, [_exclude("ci.1")])
    assert _state(result, "fresh.1") == ("not_applicable", "requires:ci.1")
    assert "disposition" not in next(c for c in result["conditions"] if c["id"] == "fresh.1")
    assert result["tally"]["met"] == 0
    # the headline counts the dependent too: both left the denominator
    assert "· 2 not applicable (excluded by owner) ·" in hl._tally_line(result)


def test_an_excluded_gate_condition_keeps_its_step_unreachable():
    signals = {**_fresh(), "layer_grades": {"instruction_file_present": True},
               "ci_checks": {"test_or_lint_configured": True}}
    assert lt.level_result(_stepped(), signals)["step"] == 3
    result = lt.level_result(_stepped(), signals, [_exclude("ci.1", "tool_unsupported")])
    assert result["step"] == 2
    assert result["next_step"] == {"step": 3, "blockers": [
        {"kind": "gate", "gate": "ci_runs_tests", "condition": "ci.1", "state": "not_applicable",
         "disposition": "excluded_by_owner", "exclusion_kind": "tool_unsupported"}]}
    assert result["tally"]["met"] == 2  # the threshold holds: only the excluded gate blocks


def test_no_exclusions_give_the_same_result_as_before():
    model = _stepped()
    signals = {**_fresh(), "layer_grades": {"instruction_file_present": True}}
    assert lt.level_result(model, signals, []) == lt.level_result(model, signals)
    assert "excluded" not in lt.level_result(model, signals)


def test_an_id_the_model_does_not_define_is_ignored_and_the_first_entry_wins():
    model = _model(_row("fresh", [{"signal": DOC, "op": "lte", "value": 50}]))
    result = lt.level_result(model, _fresh(), [_exclude("nope.1"), _exclude("fresh.1", "other"),
                                               _exclude("fresh.1", "false_positive")])
    assert result["excluded"] == [{"id": "fresh.1", "kind": "other", "source": "repo"}]


def test_the_run_context_level_reads_the_owner_exclusions_block():
    ctx = {**_fresh(), "owner_exclusions": {"exclusions": [_exclude("doc-freshness.1")]}}
    assert _state(lt.run_context_level(ctx), "doc-freshness.1") == ("not_applicable", None)
    for degraded in ({"available": False, "reason": "x"}, None, {"exclusions": "x"}):
        assert "excluded" not in lt.run_context_level({**_fresh(), "owner_exclusions": degraded})


def test_the_level_entry_reads_the_exclusions_block_last():
    spec = next(s for s in SCANS if s.key == "level")
    # the run's maturity-model choice follows the blocks (#1150)
    assert spec.reads == (*lt.LEVEL_READS, "maturity_model") and spec.reads[-2] == "owner_exclusions"
    blocks = [None] * len(lt.DEFAULT_BLOCKS)
    result = core_scans.level(*blocks, {"exclusions": [_exclude("doc-freshness.1")]}, None)
    assert result["excluded"][0]["id"] == "doc-freshness.1"


# --- display and privacy ------------------------------------------------------

def test_the_headline_names_an_excluded_gate_and_counts_exclusions():
    model = _stepped()
    signals = {**_fresh(), "layer_grades": {"instruction_file_present": True},
               "ci_checks": {"test_or_lint_configured": True}}
    lines = hl.headline(lt.level_result(model, signals, [_exclude("ci.1")]), model)["lines"]
    assert "CI turns red on a broken check (excluded by owner: measured elsewhere; the Step waits while the exclusion stands)" \
        in lines[1]
    assert "· 1 not applicable (excluded by owner) ·" in lines[2]
    plain = hl.headline(lt.level_result(model, signals), model)["lines"]
    assert "excluded" not in " ".join(plain)


def test_counts_only_keeps_ids_and_kinds_and_drops_the_reason_and_owner():
    block = parse_exclusions(_config({"id": "ci.1", "reason": "secret words", "excluded_by": "ben"},
                                     {"id": "who-knows", "reason": "r"}), KNOWN)
    model = _model(_row("ci", [_eq(CI)]), _row("instr", [_eq(INSTR)]), steps=STEPS)
    level = lt.level_result(model, {}, block["exclusions"])
    out = counts_only({"owner_exclusions": block, "level": level})
    kept = out["owner_exclusions"]
    assert kept["exclusions"][0]["id"] == "ci.1" and kept["exclusions"][0]["kind"] == "other"
    assert "secret words" not in str(out) and "ben" not in str(out) and "who-knows" not in str(out)
    assert out["level"]["excluded"] == [{"id": "ci.1", "kind": "other", "source": "repo"}]
    assert out["level"]["conditions"][0]["disposition"] == "excluded_by_owner"


# --- end to end ---------------------------------------------------------------

def test_assess_applies_a_committed_exclusion_and_the_recount_agrees(estate: Path, tmp_path: Path):  # noqa: F811 - estate is a pytest fixture imported by name
    fleet = _fleet(estate, "e4")["repos"]["ci2/r"]
    target = fleet["counted"][0]
    repo = tmp_path / "ci2"
    subprocess.run(["git", "clone", "-q", str(estate / "ci2" / "r"), str(repo)], check=True, capture_output=True)
    assess = repo / ".assess"
    assess.mkdir()
    (assess / "config.toml").write_text(
        f'[checks]\nexclude = [{{ id = "{target}", kind = "measured_elsewhere", reason = "Owner reason." }}]\n',
        encoding="utf-8")
    treemap = subprocess.run(["uv", "run", "--script", str(SCRIPTS / "complexity-treemap.py"), str(repo), "-o",
                              str(assess / "complexity-heatmap.svg"), "--stats", str(assess / "complexity-stats.json")],
                             capture_output=True, encoding="utf-8", errors="replace")
    assert treemap.returncode == 0, treemap.stderr
    from assess_core import build_and_publish_run

    ctx = build_and_publish_run(repo_root=repo, run_date="2026-11-21", non_interactive=True)
    level = ctx["level"]
    assert level["tally"]["met"] == fleet["tally"]["met"] - 1
    assert level["tally"]["measured"] == fleet["tally"]["measured"] - 1
    assert _state(level, target) == ("not_applicable", None)
    assert level["excluded"] == [{"id": target, "kind": "measured_elsewhere", "source": "repo"}]
    assert ctx["owner_exclusions"]["disclosures"] == [f"`{target}` excluded by owner: Owner reason. (measured elsewhere)"]
    on_disk = json.loads((assess / "run-context.json").read_text(encoding="utf-8"))
    assert lt.run_context_level(on_disk) == on_disk["level"] == level
