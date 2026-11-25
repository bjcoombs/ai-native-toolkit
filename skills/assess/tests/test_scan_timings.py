"""Tests for lib/scan_timings.py: the timings diagnostic and its one-line summary (#1380)."""
from __future__ import annotations

import pytest

from lib.scan_timings import TIMINGS_KEY, summary_line, timed, timings_block


def test_timed_records_even_when_the_body_raises():
    inputs: dict = {}
    with pytest.raises(RuntimeError), timed(inputs, "scans", "boom"):
        raise RuntimeError("kaput")
    assert inputs[TIMINGS_KEY]["scans"]["boom"] >= 0
    assert inputs[TIMINGS_KEY]["total_seconds"] == 0.0  # only a stage moves the total


def test_a_stage_refreshes_the_total_as_the_sum_of_stages():
    inputs: dict = {}
    block = timings_block(inputs)
    block["stages"]["setup"] = 1.25
    with timed(inputs, "stages", "final"):
        pass
    assert block["total_seconds"] == round(1.25 + block["stages"]["final"], 3)
    assert timings_block(inputs) is block


def test_summary_names_the_total_and_the_slowest_scans_slowest_first():
    block = {"total_seconds": 12.04, "stages": {"setup": 12.04},
             "scans": {"a": 1.0, "b": 5.0, "c": 5.0, "d": 0.2}}
    assert summary_line(block) == "timings: total 12.0s; slowest scans: b 5.0s, c 5.0s, a 1.0s"


def test_summary_without_a_block_or_scans_says_so():
    assert summary_line(None) == "timings: not recorded"
    empty = {"total_seconds": 0.0, "stages": {}, "scans": {}}
    assert summary_line(empty) == "timings: total 0.0s; slowest scans: none"


def test_counts_only_checks_leave_the_timings_block_out():
    from lib.counts_only.content_free import counts_only, unclassified_strings

    ctx = {"run_date": "2026-11-25", TIMINGS_KEY: {"total_seconds": 1.0, "stages": {"setup": 1.0},
                                                   "scans": {"run_header": 1.0}}}
    assert TIMINGS_KEY not in counts_only(ctx)
    assert unclassified_strings(ctx) == []
