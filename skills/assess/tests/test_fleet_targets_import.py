"""Page-written Step targets read back into the targets file, lib/fleet/fleet_targets_import.py and its CLI (issue #948)."""
from __future__ import annotations

import importlib.util
import json
import tomllib
from pathlib import Path
from types import ModuleType
from typing import Any, cast

import pytest

import lib.fleet.fleet_targets_import as ti
from lib.fleet.fleet_data import FleetData
from lib.fleet.fleet_targets import TargetSpec, parse_targets
from lib.levels import MaturityModel
from test_fleet_achievements_cli import STEP_LEVELS, _root, _seed_lower
from test_fleet_cli import fleet_cli

SCRIPT = Path(__file__).resolve().parents[2] / "fleet" / "scripts" / "fleet_targets_import.py"
MODEL = cast(MaturityModel, {"steps": [{"step": 1}, {"step": 2}, {"step": 3}, {"step": 4}]})


def _load_cli() -> ModuleType:
    spec = importlib.util.spec_from_file_location("fleet_targets_import_cli", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


cli = _load_cli()


def _bundle(*, hashed: bool = False) -> FleetData:
    unit = "h:0123456789abcdef" if hashed else "ownership/acme/payments"
    repo = "h:fedcba9876543210" if hashed else "acme/svc"
    return cast(FleetData, {
        "views": [{"view": "ownership", "units": [{"key": unit}, {"key": "ownership/acme"}]},
                  {"view": "system", "units": [{"key": "system/billing"}]}],
        "repos": [{"key": repo}],
    })


def _row(view: str, key: str, data: dict[str, Any]) -> dict[str, Any]:
    return {"doc_id": ti.target_doc_id(view, key), "data": data, "version": 3}


def test_target_doc_id_encodes_the_unit_key():
    assert ti.target_doc_id("ownership", "ownership/acme/payments") == "ownership~ownership~2Facme~2Fpayments"
    assert ti.target_doc_id("repo", "acme/svc") == "repo~acme~2Fsvc"


FIXTURE_ROWS = [
    _row("ownership", "ownership/acme/payments", {"step": 3, "note": "Q1 goal"}),
    _row("ownership", "ownership/nope", {"step": 2}),
    _row("ownership", "ownership/acme", {"step": 5}),
    _row("system", "system/billing", {"step": 2, "owner": "someone"}),
    _row("repo", "acme/svc", {"step": 2, "note": "x" * (ti.NOTE_MAX + 1)}),
]
FIXTURE_REFUSALS = [ti.Refusal(2, ti.UNKNOWN_UNIT), ti.Refusal(3, ti.STEP_OUT_OF_RANGE),
                    ti.Refusal(4, ti.UNKNOWN_FIELD), ti.Refusal(5, ti.STRING_TOO_LONG)]


def test_one_valid_and_four_invalid_rows_give_one_target_and_four_named_refusals():
    result = ti.import_rows(FIXTURE_ROWS, _bundle(), MODEL)
    assert result.targets == [TargetSpec("ownership", "acme/payments", 3)]
    assert result.refusals == FIXTURE_REFUSALS
    unknown_view = ti.import_rows([_row("team", "ownership/acme", {"step": 2})], _bundle(), MODEL)
    assert unknown_view.refusals == [ti.Refusal(1, ti.UNKNOWN_VIEW)]


@pytest.mark.parametrize(("row", "reason"), [
    ("not a row", ti.MALFORMED_ROW),
    ({"doc_id": 7, "data": {"step": 2}}, ti.MALFORMED_ROW),
    ({"doc_id": "repo~acme~2Fsvc", "data": [2]}, ti.MALFORMED_ROW),
    ({"doc_id": "repo~acme~2Fsvc"}, ti.MALFORMED_ROW),
    ({"doc_id": "repo", "data": {"step": 2}}, ti.BAD_DOC_ID),
    ({"doc_id": "repo~", "data": {"step": 2}}, ti.BAD_DOC_ID),
    ({"doc_id": "repo~acme~FF", "data": {"step": 2}}, ti.BAD_DOC_ID),  # not UTF-8
    ({"doc_id": "repo~acme~41svc", "data": {"step": 2}}, ti.BAD_DOC_ID),  # not canonical ("A" is safe)
    ({"doc_id": "repo~acme/svc", "data": {"step": 2}}, ti.BAD_DOC_ID),
    (_row("repo", "acme/svc", {"step": 2, "note": "x" * (ti.NOTE_MAX + 1)}), ti.STRING_TOO_LONG),
    (_row("repo", "acme/svc", {"step": 2, "note": 4}), ti.STRING_TOO_LONG),
    (_row("repo", "acme/svc", {"step": True}), ti.STEP_OUT_OF_RANGE),
    (_row("repo", "acme/svc", {"step": 3.0}), ti.STEP_OUT_OF_RANGE),
    (_row("repo", "acme/svc", {"step": 1}), ti.STEP_OUT_OF_RANGE),
    (_row("repo", "acme/svc", {"step": "3"}), ti.STEP_OUT_OF_RANGE),
    (_row("repo", "acme/svc", {}), ti.STEP_OUT_OF_RANGE),
    (_row("repo", "acme/other", {"step": 2}), ti.UNKNOWN_UNIT),
    (_row("system", "ownership/acme", {"step": 2}), ti.UNKNOWN_UNIT),  # a unit of another view
])
def test_each_invalid_row_is_refused_with_its_reason(row, reason):
    assert ti.import_rows([row], _bundle(), MODEL) == ti.ImportResult([], [ti.Refusal(1, reason)])


def test_a_note_at_the_limit_is_accepted_and_never_copied():
    note = 'x" \n[[target]]\nunit = "evil"' + "y" * 10
    result = ti.import_rows([_row("repo", "acme/svc", {"step": 2, "note": note})], _bundle(), MODEL)
    assert result.targets == [TargetSpec("repo", "acme/svc", 2)]
    text = ti.dumps_targets(result.targets, MODEL)
    assert "evil" not in text and "yyyy" not in text and "note" not in text


def test_hashed_keys_are_refused_by_name():
    rows = [_row("ownership", "h:0123456789abcdef", {"step": 2}), _row("repo", "h:fedcba9876543210", {"step": 2})]
    result = ti.import_rows(rows, _bundle(hashed=True), MODEL)
    assert result == ti.ImportResult([], [ti.Refusal(1, ti.HASHED_KEY), ti.Refusal(2, ti.HASHED_KEY)])


def test_two_rows_for_one_unit_are_both_refused():
    rows = [_row("repo", "acme/svc", {"step": 2}), _row("system", "system/billing", {"step": 4}),
            _row("repo", "acme/svc", {"step": 3})]
    result = ti.import_rows(rows, _bundle(), MODEL)
    assert result.targets == [TargetSpec("system", "billing", 4)]
    assert result.refusals == [ti.Refusal(1, ti.DUPLICATE), ti.Refusal(3, ti.DUPLICATE)]


def test_output_is_sorted_and_reads_back_through_parse_targets():
    rows = [_row("system", "system/billing", {"step": 4}), _row("repo", "acme/svc", {"step": 2}),
            _row("ownership", "ownership/acme/payments", {"step": 3}), _row("ownership", "ownership/acme", {"step": 2})]
    result = ti.import_rows(rows, _bundle(), MODEL)
    text = ti.dumps_targets(result.targets, MODEL)
    assert text == ti.dumps_targets(list(reversed(result.targets)), MODEL)
    assert parse_targets(tomllib.loads(text), MODEL) == [
        TargetSpec("ownership", "acme", 2), TargetSpec("ownership", "acme/payments", 3),
        TargetSpec("repo", "acme/svc", 2), TargetSpec("system", "billing", 4)]
    assert "\r" not in text and text.endswith("\n")


def test_no_valid_rows_still_writes_an_empty_targets_file():
    text = ti.dumps_targets([], MODEL)
    assert parse_targets(tomllib.loads(text), MODEL) == []


def test_a_unit_id_with_quotes_is_escaped():
    target = TargetSpec("repo", 'a"b\\c\x01', 2)
    assert parse_targets(tomllib.loads(ti.dumps_targets([target], MODEL)), MODEL) == [target]


def test_the_export_must_be_a_list_and_the_model_needs_steps():
    with pytest.raises(ti.TargetsImportError, match="JSON list"):
        ti.import_rows({"rows": []}, _bundle(), MODEL)
    with pytest.raises(ti.TargetsImportError, match=r"\[\[steps\]\]"):
        ti.import_rows([], _bundle(), cast(MaturityModel, {"steps": [{"step": 1}]}))


def test_known_units_skips_a_key_outside_its_view_and_a_blank_id():
    bundle = cast(FleetData, {"views": [{"view": "system", "units": [{"key": "ownership/x"}, {"key": "system/ "}]}],
                              "repos": [{"key": ""}]})
    assert ti.known_units(bundle) == {}


# --- CLI -------------------------------------------------------------------------------------


def _files(tmp_path: Path, rows: object) -> list[str]:
    (tmp_path / "export.json").write_text(json.dumps(rows), encoding="utf-8")
    (tmp_path / "fleet-data.json").write_text(json.dumps(_bundle()), encoding="utf-8")
    (tmp_path / "levels.toml").write_text(STEP_LEVELS, encoding="utf-8")
    return [str(tmp_path / "export.json"), "--fleet-data", str(tmp_path / "fleet-data.json"),
            "--levels", str(tmp_path / "levels.toml"), "--out", str(tmp_path / "targets.toml")]


def test_cli_writes_the_one_valid_fixture_row_and_names_the_four_refusals(tmp_path, capsys):
    args = _files(tmp_path, FIXTURE_ROWS)
    gate = '\ngates = [{ id = "instructions", row = "instructions", level = 1 }]\n'
    four = STEP_LEVELS + '\n[[steps]]\nstep = 3\nname = "Unattended agent PRs"\nthreshold = 0' + gate \
        + '\n[[steps]]\nstep = 4\nname = "Autonomous loops"\nthreshold = 0' + gate
    (tmp_path / "levels.toml").write_text(four, encoding="utf-8")
    assert cli.main(args) == 0
    targets = parse_targets(tomllib.loads((tmp_path / "targets.toml").read_text(encoding="utf-8")), MODEL)
    assert targets == [TargetSpec("ownership", "acme/payments", 3)]
    err = capsys.readouterr().err
    assert [line.split(": ", 1)[1] for line in err.splitlines() if ": row " in line] == [
        f"row {r.row}: {r.reason}" for r in FIXTURE_REFUSALS]
    assert "1 target(s) written, 4 row(s) refused" in err


def test_cli_writes_the_file_byte_identically_and_reports_refusals_without_echoing_rows(tmp_path, capsys):
    rows = [_row("repo", "acme/svc", {"step": 2, "note": "secret-note"}), _row("repo", "acme/svc-evil", {"step": 2})]
    args = _files(tmp_path, rows)
    assert cli.main(args) == 0
    first = (tmp_path / "targets.toml").read_bytes()
    err = capsys.readouterr().err
    assert "row 2: unknown_unit" in err and "1 target(s) written, 1 row(s) refused" in err
    assert "evil" not in err and "secret-note" not in err
    assert cli.main(args) == 0
    assert (tmp_path / "targets.toml").read_bytes() == first
    assert b"\r\n" not in first and b'unit = "acme/svc"' in first


@pytest.mark.parametrize(("export", "bundle", "message"), [
    ("{not json", None, "cannot read the export"),
    (json.dumps({"rows": []}), None, "JSON list"),
    ("[]", "[]", "is not a fleet-data.json bundle"),
    ("[]", json.dumps({"views": [{"view": "repo"}], "repos": []}), "is not a fleet-data.json bundle"),
])
def test_cli_usage_errors_exit_2(tmp_path, capsys, export, bundle, message):
    args = _files(tmp_path, [])
    (tmp_path / "export.json").write_text(export, encoding="utf-8")
    if bundle is not None:
        (tmp_path / "fleet-data.json").write_text(bundle, encoding="utf-8")
    assert cli.main(args) == 2
    assert message in capsys.readouterr().err
    assert not (tmp_path / "targets.toml").exists()


def test_cli_refuses_a_model_without_steps(tmp_path, capsys):
    args = _files(tmp_path, [])
    (tmp_path / "levels.toml").write_text(STEP_LEVELS.split("[[steps]]")[0], encoding="utf-8")
    assert cli.main(args) == 2
    assert "[[steps]]" in capsys.readouterr().err


def test_the_imported_file_drives_fleet_targets_to_a_target_hit(tmp_path, monkeypatch, capsys):
    """The #945 fixture: a page-written row for grpa/svc1 reaches the next run as a target_hit."""
    root = _root(tmp_path)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "levels.toml").write_text(STEP_LEVELS, encoding="utf-8")
    base = [str(root), "--levels", "levels.toml", "--history-dir", "hist"]
    assert fleet_cli.main([*base, "--out", "o1"]) == 0
    bundle = json.loads((tmp_path / "o1" / "fleet-data.json").read_text(encoding="utf-8"))
    unit_key = next(u["key"] for v in bundle["views"] if v["view"] == "ownership" for u in v["units"])
    rows = [_row("repo", "grpa/svc1", {"step": 2, "note": "reach Step 2"}), _row("repo", "grpa/gone", {"step": 2}),
            _row("ownership", unit_key, {"step": 2})]
    (tmp_path / "export.json").write_text(json.dumps(rows), encoding="utf-8")
    assert cli.main(["export.json", "--fleet-data", "o1/fleet-data.json", "--levels", "levels.toml",
                     "--out", "targets.toml"]) == 0
    (today,) = (tmp_path / "hist" / "grpa+grpb").iterdir()
    _seed_lower(today, "2000-01-01", "grpa/svc1")
    capsys.readouterr()
    assert fleet_cli.main([*base, "--targets", "targets.toml", "--out", "o2"]) == 0
    doc = json.loads((tmp_path / "o2" / "achievements.json").read_text(encoding="utf-8"))
    assert ("target_hit", "grpa/svc1") in {(a["kind"], a["unit"]) for a in doc["achievements"]}
    assert {("repo", "grpa/svc1"), ("ownership", unit_key)} <= {(t["view"], t["unit"]) for t in doc["targets"]}
