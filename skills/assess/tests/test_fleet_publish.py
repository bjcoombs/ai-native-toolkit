"""``lib.fleet_publish``: the fleet artifact database projection (issue #947)."""
from __future__ import annotations

import copy
import re
import json
from collections.abc import Mapping
from typing import Any, cast

import pytest

from fleet_data_fixture import SALT, build
from lib import fleet_data as fd
from lib import fleet_publish as fp

RUN_1, RUN_2 = "2026-11-20", "2026-11-27"


def _bundle(privacy: fd.Privacy = fd.Privacy(), scan_date: str = RUN_1) -> fd.FleetData:
    data: fd.FleetData = build(privacy)
    data["run"]["scan_date"] = scan_date
    return data


class FakeDb:
    """The database's write semantics: ``set`` replaces, ``update`` merges nested objects
    recursively (arrays and scalars replace) and requires the document to exist."""

    def __init__(self) -> None:
        self.docs: dict[str, dict[str, Any]] = {}

    def apply(self, plan: fp.PublishPlan) -> None:
        for batch in plan["batches"]:
            assert len(batch) <= fp.LIMITS.batch_writes
            names = [f"{w['collection']}/{w['doc_id']}" for w in batch]
            assert len(names) == len(set(names))  # each document addressed once per batch
            for write, name in zip(batch, names):
                data = copy.deepcopy(write["data"])
                if write["op"] == "set":
                    self.docs[name] = data
                else:
                    assert write["op"] == "update" and name in self.docs, name
                    self._merge(self.docs[name], data)

    def _merge(self, into: dict[str, Any], data: Mapping[str, Any]) -> None:
        for key, value in data.items():
            if isinstance(value, dict) and isinstance(into.get(key), dict):
                self._merge(into[key], value)
            else:
                into[key] = value

    def stored(self) -> dict[str, int]:
        return {name: len(json.dumps(doc, sort_keys=True, separators=(",", ":")).encode()) for name, doc in self.docs.items()}


def _publish(db: FakeDb, bundle: fd.FleetData, run_id: str) -> fp.PublishPlan:
    plan = fp.project(bundle, run_id, stored=db.stored())
    db.apply(plan)
    return plan


def _writes(plan: fp.PublishPlan) -> list[fp.Write]:
    return [w for batch in plan["batches"] for w in batch]


def test_the_same_bundle_gives_byte_identical_batches() -> None:
    first = fp.dumps_plan(fp.project(_bundle(), RUN_1))
    assert first == fp.dumps_plan(fp.project(_bundle(), RUN_1))
    assert first.endswith("\n") and json.loads(first)["run_id"] == RUN_1


def test_runs_one_two_and_two_again_leave_one_document_per_run_and_no_duplicates() -> None:
    db = FakeDb()
    _publish(db, _bundle(), RUN_1)
    _publish(db, _bundle(scan_date=RUN_2), RUN_2)
    after_two = copy.deepcopy(db.docs)
    again = _publish(db, _bundle(scan_date=RUN_2), RUN_2)
    assert db.docs == after_two  # republishing a run changes nothing
    assert sorted(n for n in db.docs if n.startswith("runs/")) == ["runs/" + RUN_1, "runs/" + RUN_2]
    bundle = _bundle()
    units = {fp.doc_id(u["key"]) for v in bundle["views"] for u in v["units"]}
    repos = {fp.doc_id(r["key"]) for r in bundle["repos"]}
    assert {n for n in db.docs if n.startswith("units/")} == {f"units/{u}" for u in units}
    assert {n for n in db.docs if n.startswith("repos/")} == {f"repos/{r}" for r in repos}
    for name, doc in db.docs.items():
        if not name.startswith("runs/"):
            assert list(doc) == ["history"] and sorted(doc["history"]) == [RUN_1, RUN_2]
    assert {w["op"] for w in _writes(again) if w["collection"] != "runs"} == {"update"}


def test_units_and_repos_write_only_their_history_entry_and_never_targets() -> None:
    plan = fp.project(_bundle(), RUN_1)
    writes = _writes(plan)
    assert writes[0]["collection"] == "runs" and writes[0]["op"] == "set"
    assert [w["collection"] for w in writes[1:]] == sorted((w["collection"] for w in writes[1:]),
                                                            key=lambda c: c != "units")
    assert "targets" not in {w["collection"] for w in writes}
    repo = next(r for r in _bundle()["repos"] if r["key"] == "grp/sub/proj1")
    write = next(w for w in writes if w["doc_id"] == fp.doc_id("grp/sub/proj1"))
    assert write["data"] == {"history": {RUN_1: {k: v for k, v in repo.items() if k != "key"}}}
    run: Any = writes[0]["data"]
    assert run["counts"] == {"repos": 4, "units": 4}
    assert run["views"] == [{"view": "ownership", "units": 3}, {"view": "system", "units": 1}]
    assert "repos" not in run and run["run"]["scan_date"] == RUN_1


def test_fields_the_bundle_gains_are_projected_generically() -> None:
    bundle = _bundle()
    extra: dict[str, Any] = {"future_count": 1, "future_list": [{"n": 2}]}
    bundle["repos"][0].update(extra)  # type: ignore[typeddict-item]
    key = fp.doc_id(bundle["repos"][0]["key"])
    write = next(w for w in _writes(fp.project(bundle, RUN_1)) if w["doc_id"] == key)
    entry: Any = write["data"]["history"]
    assert entry[RUN_1]["future_count"] == 1 and entry[RUN_1]["future_list"] == [{"n": 2}]
    bundle["repos"][0].update(cast(Any, {"future_text": "token"}))
    with pytest.raises(fp.PrivacyError, match="repos.future_text: no counts-only rule"):
        fp.project(bundle, RUN_1)  # a new string is refused until its counts-only rule lands


def test_a_full_bundle_is_refused() -> None:
    with pytest.raises(fp.FullRunRefused, match="--full"):
        fp.project(_bundle(fd.Privacy(full=True)), RUN_1)


def test_a_string_breaking_the_counts_only_rules_is_refused_by_pattern() -> None:
    bundle = _bundle()
    bundle["repos"][0]["reason"] = "a free-text sentence"
    bundle["views"][0]["units"][0]["name"] = "Platform"
    with pytest.raises(fp.PrivacyError) as err:
        fp.project(bundle, RUN_1)
    assert "repos.reason: not a token" in str(err.value)
    assert "views.units.name: dropped in counts-only output" in str(err.value)


def test_a_hashed_batch_holds_no_repository_name_url_or_path() -> None:
    plan = fp.project(_bundle(fd.Privacy(salt=SALT)), RUN_1)
    text = json.dumps(plan["batches"], ensure_ascii=False)
    for name in ("grp", "sub", "other", "proj1", "proj2", "proj3", "proj4", "checkout", "Checkout",
                 "gitlab", "example.com", "https://", "@grp", "bob", "maintainers", "/", "~2F"):
        assert name not in text, name


def test_an_unhashed_key_or_a_web_url_in_a_hashed_bundle_is_refused() -> None:
    bundle = _bundle(fd.Privacy(salt=SALT))
    bundle["repos"][0]["key"] = "grp/sub/proj1"
    bundle["repos"][1]["web_url"] = "https://gitlab.example.com/grp/sub/proj2"
    bundle["repos"][2]["units"][0]["unit"] = "ownership/grp"
    with pytest.raises(fp.PrivacyError) as err:
        fp.project(bundle, RUN_1)
    for problem in ("repos document id: not hashed", "repos.web_url: not written under --hash-paths",
                    "repos.units.unit: not hashed under --hash-paths"):
        assert problem in str(err.value)


def test_doc_ids_are_safe_segments_and_decode_back() -> None:
    for key in ("ownership/grp/sub", "h:409f5a9502b685ea", "a~b.c", "ünï/код", "2026-11-20.2", "..", "."):
        encoded = fp.doc_id(key)
        assert all(ch.isascii() and (ch.isalnum() or ch in "_-~:@+") for ch in encoded)
        assert fp.decode_doc_id(encoded) == key
    assert fp.doc_id("ownership/grp/sub") == "ownership~2Fgrp~2Fsub"
    assert fp.doc_id("a/b") != fp.doc_id("a~2Fb")  # injective: the escape character is escaped too
    with pytest.raises(fp.PublishError, match="1 to 200"):
        fp.doc_id("x" * 201)
    with pytest.raises(fp.PublishError):
        fp.doc_id("")


def test_a_same_day_run_id_keys_history_without_a_dot() -> None:
    writes = _writes(fp.project(_bundle(), "2026-11-20.2"))
    assert writes[0]["doc_id"] == "2026-11-20~2E2"
    assert all(list(w["data"]["history"]) == ["2026-11-20~2E2"] for w in writes[1:])  # type: ignore[call-overload]


def test_the_manifest_matches_the_access_rules_exactly() -> None:
    assert fp.access_rules() == {"db": {"rules": [
        {"path": "", "read": "view", "write": "owner"},
        {"path": "runs", "read": "view", "write": "owner"},
        {"path": "units", "read": "view", "write": "owner"},
        {"path": "repos", "read": "view", "write": "owner"},
        {"path": "targets", "read": "view", "write": "interact"},
    ]}}


def test_limits_carry_their_stated_values() -> None:
    assert fp.LIMITS == fp.Limits(document_bytes=262_144, documents=5_000, depth=32,
                                  batch_writes=50, batch_bytes=1_048_576)
    assert fp.WARN_SHARE == 0.8


def _synthetic(repos: int) -> fd.FleetData:
    """A hashed bundle of ``repos`` repositories under one team, from the fixture's shapes."""
    bundle = _bundle(fd.Privacy(salt=SALT))
    template = bundle["repos"][0]
    unit = template["units"][0]["unit"]
    bundle["repos"] = [{**copy.deepcopy(template), "key": f"h:{i:016x}", "units": [{"view": "ownership", "unit": unit}]}
                       for i in range(repos)]
    lift = bundle["opportunities"]["biggest_lift"]  # type: ignore[index]
    assert lift is not None
    lift["repos"] = [r["key"] for r in bundle["repos"]]
    lift["repo_count"] = repos
    return bundle


def test_a_ten_thousand_repository_bundle_reports_its_count_and_peak() -> None:
    plan = fp.project(_synthetic(10_000), RUN_1)
    size = plan["size"]
    assert size["documents"] == 1 + 4 + 10_000 and not size["fits"]  # over the 5,000 cap: #950 shards
    larger = fp.LIMITS._replace(documents=25_000)  # a contract stating the larger cap
    assert fp.project(_synthetic(10_000), RUN_1, limits=larger)["size"]["fits"]
    # every repository key in the opportunity list lands in the runs document: the peak
    assert size["peak"]["document"] == f"runs/{RUN_1}"
    assert 200_000 < size["peak"]["bytes"] < fp.LIMITS.document_bytes
    assert size["warnings"] == [size["peak"]]  # over 80% of the per-document limit
    writes = _writes(plan)
    assert len(writes) == 10_005
    for batch in plan["batches"]:
        assert len(batch) <= 50 and len(json.dumps({"writes": batch}).encode()) <= fp.LIMITS.batch_bytes


def test_the_warning_fires_at_eighty_percent_and_the_guard_refuses_above_the_limit() -> None:
    bundle = _bundle()
    writes = _writes(fp.project(bundle, RUN_1))
    sizes = {f"{w['collection']}/{w['doc_id']}": len(json.dumps(w["data"], sort_keys=True, separators=(",", ":"),
                                                                ensure_ascii=False).encode()) for w in writes}
    largest = max(sizes.values())
    warn = fp.LIMITS._replace(document_bytes=int(largest / 0.85))
    report = fp.project(bundle, RUN_1, limits=warn)["size"]
    assert report["warnings"] and all(w["bytes"] >= 0.8 * warn.document_bytes for w in report["warnings"])
    assert report["peak"]["bytes"] == largest
    with pytest.raises(fp.DocumentTooLarge, match=r"(runs|units|repos)/\S+ is about \d+ bytes"):
        fp.project(bundle, RUN_1, limits=fp.LIMITS._replace(document_bytes=largest - 1))


def test_an_update_counts_the_stored_document_and_a_new_one_is_a_set() -> None:
    bundle = _bundle()
    name = f"repos/{fp.doc_id('grp/sub/proj1')}"
    plan = fp.project(bundle, RUN_2, stored={name: 1000})
    write = next(w for w in _writes(plan) if f"{w['collection']}/{w['doc_id']}" == name)
    assert write["op"] == "update"
    assert {w["op"] for w in _writes(plan) if w is not write and w["collection"] != "runs"} == {"set"}
    with pytest.raises(fp.DocumentTooLarge, match=name):
        fp.project(bundle, RUN_2, stored={name: fp.LIMITS.document_bytes})


def test_the_document_count_is_reported_against_the_artifact_limit() -> None:
    small = fp.LIMITS._replace(documents=5)
    report = fp.project(_bundle(), RUN_1, limits=small)["size"]
    assert report["documents"] == 9 and not report["fits"] and report["document_limit"] == 5
    stored = {f"runs/2026-01-0{i}": 100 for i in range(1, 4)}
    assert fp.project(_bundle(), RUN_1, stored=stored)["size"]["documents"] == 12


def test_a_document_nested_too_deep_is_refused() -> None:
    with pytest.raises(fp.DocumentTooLarge, match="deeper than 3 levels"):
        fp.project(_bundle(), RUN_1, limits=fp.LIMITS._replace(depth=3))


def test_batches_cut_on_count_and_on_body_size() -> None:
    writes = _writes(fp.project(_synthetic(120), RUN_1))
    assert [len(b) for b in fp.batches(writes)] == [50, 50, 25]
    one = len(json.dumps(writes[-1]).encode())
    tight = fp.LIMITS._replace(batch_bytes=one * 3)
    cut = fp.batches(writes[1:], tight)
    assert all(len(b) <= 3 for b in cut) and [w for b in cut for w in b] == writes[1:]


def _previous(db: FakeDb, run_key: str) -> dict[str, object]:
    return {name: (doc if name.startswith("runs/") else doc["history"][run_key])
            for name, doc in db.docs.items() if name.startswith("runs/" + run_key) or run_key in doc.get("history", {})}


def test_a_republish_with_the_same_content_passes_and_a_changed_one_is_refused() -> None:
    db = FakeDb()
    _publish(db, _bundle(), RUN_1)
    previous = _previous(db, RUN_1)
    assert len(previous) == 1 + 4 + 4
    fp.project(_bundle(), RUN_1, stored=db.stored(), previous=previous)  # the same run again
    changed = _bundle()
    changed["repos"][0]["reason"] = "scan_error"
    with pytest.raises(fp.RepublishConflict, match="repos/.* a published run id is immutable"):
        fp.project(changed, RUN_1, stored=db.stored(), previous=previous)
    fp.project(changed, RUN_2, stored=db.stored(), previous=_previous(db, RUN_2))  # a new run id is fine
    dropped = _bundle()
    dropped["repos"] = dropped["repos"][1:]
    gone = f"repos/{fp.doc_id(_bundle()['repos'][0]['key'])}"
    entries = {name: entry for name, entry in previous.items() if not name.startswith("runs/")}
    with pytest.raises(fp.RepublishConflict, match=f"in {re.escape(gone)};"):
        fp.project(dropped, RUN_1, stored=db.stored(), previous=entries)  # only the dropped entry differs
    many = _bundle()
    for repo in many["repos"]:
        repo["active"] = not repo["active"]
    many["run"]["window"] = "3mo"
    many["views"][0]["units"][0]["kind"] = "division"
    with pytest.raises(fp.RepublishConflict, match="and 1 more"):
        fp.project(many, RUN_1, stored=db.stored(), previous=previous)
