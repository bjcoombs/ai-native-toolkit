"""Tests for the bounded scan pool (lib/run/scan_pool.py, #1381)."""
from __future__ import annotations

import argparse
import threading
import time

import pytest

from lib.run import scan_pool as pool


def test_default_jobs_is_the_cpu_count_capped(monkeypatch):
    monkeypatch.setattr(pool.os, "cpu_count", lambda: 64)
    assert pool.default_jobs() == pool.JOBS_CAP
    monkeypatch.setattr(pool.os, "cpu_count", lambda: 3)
    assert pool.default_jobs() == 3
    monkeypatch.setattr(pool.os, "cpu_count", lambda: None)
    assert pool.default_jobs() == 1


def test_share_of_cpus_splits_the_cpus_between_processes(monkeypatch):
    monkeypatch.setattr(pool.os, "cpu_count", lambda: 16)
    assert [pool.share_of_cpus(n) for n in (1, 2, 4, 16, 64, 0)] == [8, 8, 4, 1, 1, 8]


@pytest.mark.parametrize("text", ["0", "-2", "two", "1.5"])
def test_jobs_below_one_or_not_a_number_is_a_usage_error(text):
    parser = argparse.ArgumentParser()
    pool.add_jobs_argument(parser)
    with pytest.raises(SystemExit) as raised:
        parser.parse_args(["--jobs", text])
    assert raised.value.code == 2
    assert parser.parse_args(["--jobs", "3"]).jobs == 3
    assert parser.parse_args([]).jobs == pool.default_jobs()


def test_dependencies_follow_the_earlier_producer_of_each_read():
    deps = pool.dependencies(
        [(), ("repo_root",), ("a",), ("b1", "a"), ("x",)],
        [("a",), ("b", "b1", "b2"), ("c",), ("d",), ("e",)],
    )
    assert deps == [frozenset(), frozenset(), frozenset({0}), frozenset({0, 1}), frozenset()]


def test_restore_order_moves_added_keys_into_group_order():
    d = {"before": 0, "c": 3, "a": 1, "b2": 2, "b1": 2}
    pool.restore_order(d, [["a"], ["b1", "b2"], ["c"]])
    assert list(d) == ["before", "a", "b1", "b2", "c"]


def _run(count, deps, jobs, work, fail=frozenset()):
    """``run_ordered`` over ``work[i]()`` calls; returns the finish order and the threads used."""
    finished: list[int] = []
    threads: set[str] = set()

    def prepare(i):
        if i in fail:
            raise ValueError(f"prepare {i}")

        def call():
            threads.add(threading.current_thread().name)
            return work[i]()
        return call

    pool.run_ordered(count, deps, prepare, lambda i, r: finished.append(i), jobs)
    return finished, threads


def test_jobs_one_runs_in_index_order_on_the_calling_thread():
    finished, threads = _run(3, [frozenset()] * 3, 1, [lambda: None] * 3)
    assert finished == [0, 1, 2]
    assert threads == {threading.current_thread().name}


def test_independent_entries_overlap_and_a_dependent_waits():
    """Entry 0 cannot finish until entry 1 has run, so they must overlap; entry 2 reads entry 0
    and starts only after it has finished. Ordered by events, not sleep margins."""
    one_ran = threading.Event()
    zero_done = threading.Event()
    seen: dict[str, bool] = {}

    def zero():
        seen["overlapped"] = one_ran.wait(timeout=10)
        zero_done.set()

    def two():
        seen["after_zero"] = zero_done.is_set()

    finished, threads = _run(3, [frozenset(), frozenset(), frozenset({0})], 4,
                             [zero, one_ran.set, two])
    assert seen == {"overlapped": True, "after_zero": True}
    # 1 and 0 may report in either order: 1 sets the event and 0 returns straight after.
    assert finished.index(0) < finished.index(2)  # the dependent reports after its input
    assert all(name.startswith("scan") for name in threads)


def test_the_earliest_failure_is_raised_after_the_entries_before_it_ran():
    """Entry 3 fails in prepare, at submission, before any call ends; entry 1's call fails later
    but is earlier in the table, so its error is raised, as a jobs-1 run would raise it. Entry 4
    becomes ready only when entry 2 finishes, after the failure at 3, so it never starts."""
    ran: list[int] = []

    def ok(i):
        def run():
            ran.append(i)
        return run

    def boom(i):
        def run():
            time.sleep(0.05)
            raise RuntimeError(f"entry {i}")
        return run

    work = [ok(0), boom(1), ok(2), ok(3), ok(4)]
    deps = [frozenset(), frozenset(), frozenset(), frozenset(), frozenset({2})]
    with pytest.raises(RuntimeError, match="entry 1"):
        _run(5, deps, 4, work, fail=frozenset({3}))
    assert sorted(ran) == [0, 2]


def test_a_prepare_failure_is_raised_in_index_order():
    work = [lambda: None] * 3
    with pytest.raises(ValueError, match="prepare 1"):
        _run(3, [frozenset()] * 3, 2, work, fail=frozenset({1, 2}))


def test_fleet_passes_scan_jobs_to_each_repository_scan(tmp_path, monkeypatch):
    """#1381: /fleet --scan-jobs reaches assess_signals --jobs; unset, the child's default."""
    from lib.fleet.fleet_cli_args import parse_args
    from lib.fleet.runner import ScanOptions, signals_command

    monkeypatch.setattr(pool.os, "cpu_count", lambda: 16)
    command = signals_command(tmp_path / "c", tmp_path / "o.json", ScanOptions(scan_jobs=3))
    assert command[-2:] == ["--jobs", "3"]
    assert "--jobs" not in signals_command(tmp_path / "c", tmp_path / "o.json", ScanOptions())
    out = str(tmp_path / "out")
    assert parse_args([str(tmp_path), "--out", out, "--scan-jobs", "2"]).scan_jobs == 2
    # by default the CPUs are split between the repositories scanned at once
    assert parse_args([str(tmp_path), "--out", out, "--jobs", "16"]).scan_jobs == 1
    assert parse_args([str(tmp_path), "--out", out, "--jobs", "4"]).scan_jobs == 4
    assert parse_args([str(tmp_path), "--out", out, "--jobs", "1"]).scan_jobs == pool.JOBS_CAP
    with pytest.raises(SystemExit):
        parse_args([str(tmp_path), "--out", out, "--scan-jobs", "0"])


# --- fan-out inside a scan (#1382) -------------------------------------------------------


def test_fan_out_yields_in_input_order_when_items_finish_out_of_order():
    """The first item finishes last; its result still comes first. No completion order is
    asserted: only the order of the yielded results, which is the input order."""
    release = threading.Event()

    def work(i):
        if i == 0:
            release.wait(timeout=10)
        elif i == 3:
            release.set()
        return i * 10

    with pool.widened(4):
        assert list(pool.fan_out(work, range(4))) == [0, 10, 20, 30]


def test_fan_out_matches_the_serial_map_at_widths_1_and_4():
    items = list(range(25))
    serial = list(pool.fan_out(lambda i: (i, i * i), items))
    with pool.widened(4):
        wide = list(pool.fan_out(lambda i: (i, i * i), items))
    assert wide == serial == [(i, i * i) for i in items]


def test_width_1_is_a_lazy_map_in_the_calling_thread():
    """--jobs 1 keeps the serial path: no pool thread, and a loop that stops early calls no more."""
    calls: list[tuple[int, str]] = []

    def work(i):
        calls.append((i, threading.current_thread().name))
        return i

    for got in pool.fan_out(work, range(5)):
        if got == 1:
            break
    assert calls == [(0, threading.current_thread().name), (1, threading.current_thread().name)]


def test_widened_sets_the_width_for_its_body_and_jobs_1_leaves_it():
    assert pool.fan_out_width() == 1
    with pool.widened(4):
        assert pool.fan_out_width() == 4
        with pool.widened(1):  # a nested jobs-1 run_scans (the unit pass) inherits the width
            assert pool.fan_out_width() == 4
        with pool.widened(2):
            assert pool.fan_out_width() == 2
        assert pool.fan_out_width() == 4
    assert pool.fan_out_width() == 1


def test_an_item_failure_is_raised_at_its_place_in_the_order():
    def work(i):
        if i == 2:
            raise RuntimeError("item 2")
        return i

    for width in (1, 4):
        got: list[int] = []
        with pool.widened(width), pytest.raises(RuntimeError, match="item 2"):
            for value in pool.fan_out(work, range(5)):
                got.append(value)
        assert got == [0, 1], width
    assert pool.fan_out_width() == 1


def test_run_scans_widens_fan_out_to_jobs_for_the_run():
    """run_scans raises the fan-out width to --jobs for every stage, and restores it after."""
    from lib.run.scan_registry import STAGE_READ_SIDE, ScanSpec, run_scans

    seen: list[int] = []
    spec = ScanSpec("probe", lambda: seen.append(pool.fan_out_width()), (), STAGE_READ_SIDE)
    for jobs in (1, 3):
        run_scans({}, {}, None, [spec], jobs=jobs)
    assert seen == [1, 3]
    assert pool.fan_out_width() == 1


def test_process_fan_out_yields_in_input_order():
    """processes=True spawns workers for pure-Python work; results still come in input order."""
    import math

    items = [12, 3, 9, 0, 7]
    with pool.widened(3):
        assert list(pool.fan_out(math.factorial, items, processes=True)) == [math.factorial(i) for i in items]
        assert list(pool.fan_out(math.factorial, [], processes=True)) == []
    assert list(pool.fan_out(math.factorial, items, processes=True)) == [math.factorial(i) for i in items]
