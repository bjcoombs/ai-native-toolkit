"""Split one pytest run into N deterministic shards by test file (stdlib only).

The Windows workflow runs its two slowest suites, `skills/assess` and the
ghsync/ghreport suites, as several matrix legs; each leg loads this plugin and
keeps one shard of the collected tests:

    PYTHONPATH=<repo>/tests pytest -p pytest_shard --shard 2/4 ...

`--shard K/N` is 1-based (K in 1..N). The unit of assignment is the test file,
so every test of a file lands in the same shard and `--dist loadfile` still
keeps that file on one worker (#831). Each file goes to exactly one shard:

1. Files are weighted by their size in bytes (identical on every OS, because
   the workflow checks out with `core.autocrlf` off), or by seconds from an
   optional `--shard-weights` JSON file mapping a file's node path to its
   measured run time. A file missing from that map gets the median weight.
2. Heaviest file first, ties broken by node path, each file joins the shard
   with the least weight so far (ties go to the lowest shard number).

The plan places every collected file exactly once by construction, and it
depends only on the set of collected files and their weights. So N legs that
collect the same files with the same arguments, differing only in K, hold
disjoint shards whose union is the whole collection. No single leg can see
the other legs, so that precondition is what the guards check:
tests/test_windows_workflow.py pins one suite step per sharded suite whose
only per-leg argument is `--shard`, with every K in 1..N exactly once, and
tests/test_pytest_shard.py runs the plugin over real collections and checks
that the shards' union is the full collection with no overlap.
Without `--shard` the plugin does nothing.

`--shard-record PATH` writes the run's seconds per test file (setup, call and
teardown summed, in the xdist controller too) as the JSON `--shard-weights`
reads. The committed `skills/assess/tests/shard-weights.json` holds Windows
seconds: rebuild it from a green Windows run by merging its
`shard-seconds-assess-<K>` artifacts with `jq -S -s add` (averaging a few runs
smooths their noise), not from a Linux or local run, whose per-file times differ. A stale weights file still yields a
valid partition; only the balance drifts.
"""
from __future__ import annotations

import json
import statistics
from pathlib import Path

import pytest


def parse_shard(value: str) -> tuple[int, int]:
    """Parse `K/N` into a 1-based shard number and a shard count."""
    try:
        k_text, n_text = value.split("/")
        k, n = int(k_text), int(n_text)
    except ValueError:
        raise pytest.UsageError(f"--shard expects K/N, such as 1/4: {value!r}") from None
    if n < 1 or not 1 <= k <= n:
        raise pytest.UsageError(f"--shard {value}: need 1 <= K <= N")
    return k, n


def plan_shards(weights: dict[str, float], n: int) -> list[list[str]]:
    """Assign every file to one of `n` shards, heaviest first, to the lightest shard.

    Deterministic for a given mapping: the order is (-weight, path) and a tie in
    shard load goes to the lowest shard index.
    """
    shards: list[list[str]] = [[] for _ in range(n)]
    loads = [0.0] * n
    for path in sorted(weights, key=lambda p: (-weights[p], p)):
        i = min(range(n), key=lambda j: (loads[j], j))
        shards[i].append(path)
        loads[i] += weights[path]
    return shards


def file_weights(files: dict[str, Path], measured: dict[str, float] | None) -> dict[str, float]:
    """Weight each node path by measured seconds when given, else by its size in bytes."""
    if measured is None:
        return {key: float(path.stat().st_size) for key, path in files.items()}
    known = [measured[key] for key in files if key in measured]
    fallback = statistics.median(known) if known else 1.0
    return {key: float(measured.get(key, fallback)) for key in files}


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("shard", "deterministic split by test file")
    group.addoption("--shard", default=None, metavar="K/N", help="run only shard K of N (1-based)")
    group.addoption(
        "--shard-weights",
        default=None,
        metavar="PATH",
        help="JSON map of test file node path to seconds; file size in bytes when absent",
    )
    group.addoption(
        "--shard-record",
        default=None,
        metavar="PATH",
        help="write this run's seconds per test file as a --shard-weights JSON map",
    )


def _node_path(item: pytest.Item) -> str:
    return item.nodeid.split("::", 1)[0]


@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    value = config.getoption("--shard")
    if value is None:
        return
    k, n = parse_shard(value)
    files: dict[str, Path] = {}
    for item in items:
        files.setdefault(_node_path(item), Path(str(item.path)))
    weights_path = config.getoption("--shard-weights")
    measured = None
    if weights_path:
        measured = {str(key): float(val) for key, val in json.loads(Path(weights_path).read_text(encoding="utf-8")).items()}
    shards = plan_shards(file_weights(files, measured), n)
    keep = set(shards[k - 1])
    selected = [item for item in items if _node_path(item) in keep]
    deselected = [item for item in items if _node_path(item) not in keep]
    if deselected:
        config.hook.pytest_deselected(items=deselected)
    items[:] = selected


def pytest_report_header(config: pytest.Config) -> str | None:
    value = config.getoption("--shard")
    if value is None:
        return None
    k, n = parse_shard(value)
    return f"shard: {k} of {n}, by test file"


_RECORDED: dict[str, float] = {}


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    path = report.nodeid.split("::", 1)[0]
    _RECORDED[path] = _RECORDED.get(path, 0.0) + report.duration


def pytest_sessionfinish(session: pytest.Session) -> None:
    out = session.config.getoption("--shard-record")
    if out is None or hasattr(session.config, "workerinput"):
        return
    data = {key: round(_RECORDED[key], 2) for key in sorted(_RECORDED)}
    Path(out).write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")
