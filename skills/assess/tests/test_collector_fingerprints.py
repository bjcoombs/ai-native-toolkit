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


def test_the_committed_fingerprints_match_the_fleet_golden_and_the_manifest():
    hashes, revisions = cf.current()
    stored = cf.read_stored()
    assert cf.violations(stored, hashes, revisions) == []
    assert stored == cf.fingerprints(hashes, revisions), (
        "collector_fingerprints.json is stale: run `cd skills/assess && uv run --locked python "
        "tests/collector_fingerprints.py` (make regen does)")
