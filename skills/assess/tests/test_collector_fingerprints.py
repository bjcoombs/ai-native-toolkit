"""A collector whose fleet-golden output changes must bump its revision (#952, tests/collector_fingerprints.py)."""
from __future__ import annotations

import collector_fingerprints as cf

ROWS = [{"repo": "a/x", "scans": {"blk": {"available": True, "n": 1}, "other": {"n": 2}}},
        {"repo": "a/y", "scans": {"blk": {"available": False, "reason": "not_registered"}}}]
PUBLISHES = {"core.blk": ["blk"], "core.other": ["other"], "core.absent": ["missing"]}


def test_hashes_cover_collectors_with_a_block_in_the_golden_only():
    hashes = cf.output_hashes(ROWS, PUBLISHES)
    assert set(hashes) == {"core.blk", "core.other"}


def test_changed_output_under_the_same_revision_fails():
    before = cf.fingerprints(cf.output_hashes(ROWS, PUBLISHES), {"core.blk": 1, "core.other": 1})
    changed = [{**ROWS[0], "scans": {**ROWS[0]["scans"], "blk": {"available": True, "n": 9}}}, ROWS[1]]
    hashes = cf.output_hashes(changed, PUBLISHES)
    found = cf.violations(before, hashes, {"core.blk": 1, "core.other": 1})
    assert len(found) == 1 and found[0].startswith("core.blk:") and "bump `revision`" in found[0]


def test_changed_output_with_a_bumped_revision_passes():
    before = cf.fingerprints(cf.output_hashes(ROWS, PUBLISHES), {"core.blk": 1, "core.other": 1})
    changed = [{**ROWS[0], "scans": {**ROWS[0]["scans"], "blk": {"available": True, "n": 9}}}, ROWS[1]]
    assert cf.violations(before, cf.output_hashes(changed, PUBLISHES), {"core.blk": 2, "core.other": 1}) == []


def test_a_collector_new_to_the_golden_is_not_a_violation():
    assert cf.violations({}, cf.output_hashes(ROWS, PUBLISHES), {"core.blk": 1, "core.other": 1}) == []


REVISIONS = {"core.blk": 1, "core.other": 1}
NEW_REPO = {"repo": "a/z", "scans": {"blk": {"available": True, "n": 3}, "other": {"n": 4}}}


def test_a_repository_added_to_the_golden_is_not_a_violation():
    before = cf.fingerprints(cf.output_hashes(ROWS, PUBLISHES), REVISIONS)
    assert cf.violations(before, cf.output_hashes([*ROWS, NEW_REPO], PUBLISHES), REVISIONS) == []


def test_a_repository_dropped_from_the_golden_is_not_a_violation():
    before = cf.fingerprints(cf.output_hashes([*ROWS, NEW_REPO], PUBLISHES), REVISIONS)
    assert cf.violations(before, cf.output_hashes(ROWS, PUBLISHES), REVISIONS) == []


def test_one_mutated_block_in_the_real_golden_is_exactly_one_violation():
    hashes, revisions = cf.current()
    stored = cf.read_stored()
    rows = cf.golden_rows()
    rows[0] = {**rows[0], "scans": {**rows[0]["scans"], "stats_summary": {"available": True, "mutated": 1}}}
    from lib.manifest.check_manifest import collectors, core_manifest
    publishes = {c["id"]: c["publishes"] for c in collectors(core_manifest())}
    found = cf.violations(stored, cf.output_hashes(rows, publishes), revisions)
    assert len(found) == 1 and found[0].startswith("core.run_header:") and rows[0]["repo"] in found[0]
    assert cf.violations(stored, hashes, revisions) == []


def test_a_missing_fingerprints_file_is_refused_without_init(tmp_path, monkeypatch):
    monkeypatch.setattr(cf, "FINGERPRINTS", tmp_path / "collector_fingerprints.json")
    assert cf.main([]) == 1
    assert not cf.FINGERPRINTS.exists()
    assert cf.main(["--init"]) == 0
    assert cf.read_stored() == cf.fingerprints(*cf.current())


def test_the_committed_fingerprints_match_the_fleet_golden_and_the_manifest():
    hashes, revisions = cf.current()
    stored = cf.read_stored()
    assert cf.violations(stored, hashes, revisions) == []
    assert stored == cf.fingerprints(hashes, revisions), (
        "collector_fingerprints.json is stale: run `cd skills/assess && uv run --locked python "
        "tests/collector_fingerprints.py` (make regen does)")
