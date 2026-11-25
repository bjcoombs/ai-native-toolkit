"""Bounded thread pool that runs one stage of the scan table concurrently (#1381, part 2 of #798).

``scan_registry.run_scans`` ran its table one entry after another although each ``ScanSpec``
declares what it ``reads``. ``run_ordered`` runs entry ``i`` once every entry it depends on
has finished, at most ``jobs`` at a time, and ``dependencies`` builds those edges from the
declared reads: an entry waits only for the earlier entries of its own stage that produce a
name it reads (its key, or a spread key), because earlier stages have already finished.

The work of one entry is split in three, so only the scan call leaves the calling thread:
``prepare(i)`` runs in the calling thread and resolves the entry's reads into a call;
the call runs on a pool thread; ``finish(i, result)`` runs in the calling thread again and
writes the value. Every write to the run context and the inputs mapping is therefore made by
one thread. ``finish`` runs in completion order, so ``scan_registry`` puts the keys a pooled
stage added back into table order afterwards with ``restore_order``: ``run-context.json`` is
byte-identical to a ``--jobs 1`` run, which ``tests/test_scan_registry.py`` checks on fixture
repositories. ``jobs`` 1 runs no pool: prepare, call and finish in table order, as before.

A failure (an exception from ``prepare``, the call or ``finish``) does not stop the entries
already running. Entries later in the table than the earliest failure are not started, entries
before it still run, and the earliest failure is raised once the pool is idle: the exception a
``--jobs 1`` run raises. Only the gate entries that opt out of degrading can fail this way.

Threads suit the scans: most of their time is spent in subprocesses (git, lizard, rg) or
reading files. A process pool would need every value and callable to pickle, and no
timing so far shows a pure-Python scan worth it. ``default_jobs`` is the CPU count capped at
``JOBS_CAP``; ``add_jobs_argument`` gives ``assess_core.py`` and ``assess_signals.py`` the same
``--jobs`` flag, and ``share_of_cpus`` is the per-repository default ``/fleet`` passes its children
(``fleet.py --scan-jobs``), so N repositories at a time do not each start a full pool. Tests: ``tests/test_scan_pool.py``, ``tests/test_scan_registry.py``.
"""
from __future__ import annotations

import argparse
import heapq
import os
from collections.abc import Callable, Sequence
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from typing import Any, TypeVar

R = TypeVar("R")

JOBS_CAP = 8


def default_jobs() -> int:
    """The CPU count, capped at ``JOBS_CAP``; at least 1."""
    return max(1, min(os.cpu_count() or 1, JOBS_CAP))


def share_of_cpus(parallel: int) -> int:
    """Scans per process when ``parallel`` processes scan at once (``fleet.py --jobs``): the CPU
    count split between them, capped at ``JOBS_CAP``, at least 1. At fleet's default of one
    repository per CPU that is 1, each repository's scans running one after another."""
    return max(1, min((os.cpu_count() or 1) // max(1, parallel), JOBS_CAP))


def jobs_value(text: str) -> int:
    """``--jobs`` as a whole number of 1 or more; argparse reports anything else as a usage error."""
    try:
        jobs = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{text!r} is not a whole number") from None
    if jobs < 1:
        raise argparse.ArgumentTypeError(f"{jobs} is less than 1")
    return jobs


def add_jobs_argument(parser: argparse.ArgumentParser) -> None:
    """The ``--jobs N`` flag: scans run at most ``N`` at a time."""
    parser.add_argument(
        "--jobs", type=jobs_value, default=default_jobs(), metavar="N",
        help=f"Scans run at most N at a time, each after the scans it reads (default: the CPU "
             f"count, capped at {JOBS_CAP}; here %(default)s). The output is the same for every N; "
             "1 runs them one after another in table order.",
    )


def dependencies(reads: Sequence[Sequence[str]],
                 produces: Sequence[Sequence[str]]) -> list[frozenset[int]]:
    """For each entry, the indices of the earlier entries that produce a name it reads.

    ``reads[i]`` are entry ``i``'s declared reads and ``produces[i]`` the names it writes (its
    key and any spread keys). A name no entry here produces is read from what is already there.
    """
    producer: dict[str, int] = {}
    out: list[frozenset[int]] = []
    for index, (wanted, made) in enumerate(zip(reads, produces, strict=True)):
        out.append(frozenset(producer[name] for name in wanted if name in producer))
        for name in made:
            producer.setdefault(name, index)
    return out


def restore_order(target: dict[str, Any], groups: Sequence[Sequence[str]]) -> None:
    """Move each key of ``groups`` to the end of ``target``, group by group, keys in order.

    Keys a pooled stage added in completion order end up in the order ``--jobs 1`` adds them;
    keys that were there before the stage keep their place.
    """
    for keys in groups:
        for key in keys:
            target[key] = target.pop(key)


def run_ordered(count: int, deps: Sequence[frozenset[int]],
                prepare: Callable[[int], Callable[[], R]],
                finish: Callable[[int, R], None], jobs: int) -> None:
    """Run entries ``0..count-1``: ``prepare(i)``, its call, then ``finish(i, result)``.

    Entry ``i`` is prepared once every index in ``deps[i]`` has finished. With ``jobs`` 1 the
    entries run in index order in this thread and the first failure propagates at once.
    Otherwise the calls run on at most ``jobs`` pool threads, and the earliest failure by index
    is raised after the entries before it have run (module docstring).
    """
    if jobs <= 1:
        for index in range(count):
            finish(index, prepare(index)())
        return
    waiting = [set(each) for each in deps]
    dependents: list[list[int]] = [[] for _ in range(count)]
    for index, each in enumerate(deps):
        for dep in each:
            dependents[dep].append(index)
    ready = [index for index in range(count) if not waiting[index]]
    heapq.heapify(ready)
    failed: dict[int, Exception] = {}
    running: dict[Future[R], int] = {}
    pool = ThreadPoolExecutor(max_workers=jobs, thread_name_prefix="scan")
    try:
        while ready or running:
            limit = min(failed, default=count)
            while ready:
                index = heapq.heappop(ready)
                if index > limit:
                    continue
                try:
                    running[pool.submit(prepare(index))] = index
                except Exception as e:  # noqa: BLE001 - raised in table order below
                    failed[index] = e
                    limit = min(limit, index)
            if not running:
                break
            done, _ = wait(running, return_when=FIRST_COMPLETED)
            for future in sorted(done, key=running.__getitem__):
                index = running.pop(future)
                try:
                    finish(index, future.result())
                except Exception as e:  # noqa: BLE001 - raised in table order below
                    failed[index] = e
                    continue
                for later in dependents[index]:
                    waiting[later].discard(index)
                    if not waiting[later]:
                        heapq.heappush(ready, later)
    finally:
        pool.shutdown(wait=True, cancel_futures=True)
    if failed:
        raise failed[min(failed)]
