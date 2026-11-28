"""The ``/fleet`` publish step for a sharded fleet (#950): per-shard plans, the registry and the
targets read-back. Nothing here publishes or calls a network."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

import fleet_page_fixture as fx
import lib.fleet.fleet_publish_shards as pss
from lib.fleet.fleet_data import dumps_fleet_data
from lib.fleet.fleet_publish import PublishError
from lib.fleet.fleet_shard import UNASSIGNED, shard_folder
from lib.fleet.fleet_targets_import import target_doc_id

FLEET = Path(__file__).resolve().parents[2] / "fleet" / "scripts"
SUB, OTHER = "ownership/grp/sub", "ownership/grp/other"
URL = "https://claude.ai/artifact/"


def _script(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"{name}_cli_950", FLEET / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _data() -> dict[str, Any]:
    return dict(fx.run(fx.DATES[-1], fx.VARIANTS["counts-only"]))


def _out(tmp_path: Path) -> Path:
    out = tmp_path / "out"
    out.mkdir()
    (out / "fleet-data.json").write_text(dumps_fleet_data(_data()), encoding="utf-8")  # type: ignore[arg-type]
    (out / "fleet-view.html").write_text(fx.goldens()["expected-counts-only.html"], encoding="utf-8")
    return out


def test_a_two_shard_plan_has_publish_steps_for_both_shards_and_the_index_in_plan_order(tmp_path: Path) -> None:
    org = tmp_path / "history" / "grp"
    pss.record_shard(org, URL + "sub", SUB)
    pss.record_shard(org, URL + "other", OTHER)
    exports = {shard_folder(SUB): {}, shard_folder(OTHER): {}}
    sharded = pss.plan_sharded(_data(), exports, run_id=fx.DATES[-1], registry=pss.read_shards(org), allow_names=True)
    assert sharded is not None
    steps = sharded["summary"]["steps"]
    assert [(s["kind"], s["key"], s["url"]) for s in steps] == [
        ("index", None, None), ("shard", OTHER, URL + "other"), ("shard", SUB, URL + "sub"), ("shard", UNASSIGNED, None)]
    assert sharded["summary"]["added"] == [UNASSIGNED] and not sharded["summary"]["links_complete"]
    out = _out(tmp_path)
    folder = pss.write_sharded(out, sharded, (out / "fleet-view.html").read_text(encoding="utf-8"), _data())
    for step in steps:
        assert (folder / step["page"]).is_file()
        if step["folder"]:
            own = json.loads((folder / "shards" / step["folder"] / "plan.json").read_text(encoding="utf-8"))
            assert own["batches"] and all((folder / "shards" / step["folder"] / b).is_file() for b in own["batches"])
    assert json.loads((folder / "plan.json").read_text(encoding="utf-8"))["steps"] == steps


def test_a_recorded_shard_without_its_export_is_refused(tmp_path: Path) -> None:
    org = tmp_path / "grp"
    pss.record_shard(org, URL + "sub", SUB)
    with pytest.raises(PublishError, match="recorded but its collections were not exported"):
        pss.plan_sharded(_data(), {}, run_id=fx.DATES[-1], registry=pss.read_shards(org), allow_names=True)


def test_a_fleet_that_fits_and_has_no_shards_is_not_sharded() -> None:
    assert pss.plan_sharded(_data(), {}, run_id=fx.DATES[-1], registry=None, allow_names=True) is None


def test_the_registry_keeps_every_url_and_refuses_a_second_one_or_a_bad_link(tmp_path: Path) -> None:
    assert pss.read_shards(tmp_path) is None
    pss.record_shard(tmp_path, URL + "i", None)
    pss.record_shard(tmp_path, URL + "a", SUB)
    pss.record_shard(tmp_path, URL + "a", SUB)  # the same URL again is a no-op
    assert pss.read_shards(tmp_path) == {"index": URL + "i", "shards": {SUB: URL + "a"}}
    with pytest.raises(PublishError, match="another artifact for this shard"):
        pss.record_shard(tmp_path, URL + "b", SUB)
    with pytest.raises(PublishError, match="another artifact for the index"):
        pss.record_shard(tmp_path, URL + "j", None)
    with pytest.raises(PublishError, match="https://claude.ai/"):
        pss.record_shard(tmp_path, "https://example.com/x", OTHER)
    (tmp_path / pss.SHARDS_FILE).write_text("[]", encoding="utf-8")
    with pytest.raises(PublishError, match="is not {index, shards}"):
        pss.record_shard(tmp_path, URL + "c", OTHER)
    assert (tmp_path / pss.SHARDS_FILE).read_text(encoding="utf-8") == "[]"  # left as it was


def test_the_cli_plans_shards_with_org_dir_and_prints_them(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    cli = _script("fleet_publish_plan")
    out, org, exports = _out(tmp_path), tmp_path / "grp", tmp_path / "exports"
    exports.mkdir()
    assert cli.main(["shards", str(out), str(org)]) == 1  # fits one artifact, nothing recorded
    assert cli.main(["record-shard", str(org), "--url", URL + "s", "--shard", SUB]) == 0
    assert cli.main(["shards", str(out), str(org)]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines == ["index new", f"{shard_folder(OTHER)} new", f"{shard_folder(SUB)} {URL}s", "unassigned new"]
    assert cli.main(["plan", str(out), "--exports", str(exports), "--org-dir", str(org), "--allow-names"]) == 2
    (exports / shard_folder(SUB)).mkdir()
    assert cli.main(["plan", str(out), "--exports", str(exports), "--org-dir", str(org), "--allow-names"]) == 0
    err = capsys.readouterr().err
    assert "sharded into 3 artifact(s) plus an index" in err and "plan again" in err
    assert (out / "publish" / "index.html").is_file()
    assert cli.main(["record-shard", str(org), "--url", URL + "i", "--index"]) == 0
    assert pss.read_shards(org) == {"index": URL + "i", "shards": {SUB: URL + "s"}}


def test_targets_read_back_from_two_shards_merge_into_one_targets_file(tmp_path: Path) -> None:
    cli = _script("fleet_targets_import")
    data = _data()
    (tmp_path / "fleet-data.json").write_text(json.dumps(data), encoding="utf-8")
    (tmp_path / "levels.toml").write_text(fx.fdf.LEVELS, encoding="utf-8")
    (tmp_path / "a.json").write_text(json.dumps([{"doc_id": target_doc_id("ownership", SUB), "data": {"step": 3}}]),
                                     encoding="utf-8")
    (tmp_path / "b.json").write_text(json.dumps([{"doc_id": target_doc_id("ownership", OTHER), "data": {"step": 2}}]),
                                     encoding="utf-8")
    args = ["--fleet-data", str(tmp_path / "fleet-data.json"), "--levels", str(tmp_path / "levels.toml")]
    assert cli.main([str(tmp_path / "a.json"), str(tmp_path / "b.json"), *args, "--out", str(tmp_path / "t.toml")]) == 0
    text = (tmp_path / "t.toml").read_text(encoding="utf-8")
    assert text.count("[[target]]") == 2
    assert 'unit = "grp/other"\nstep = 2' in text and 'unit = "grp/sub"\nstep = 3' in text
