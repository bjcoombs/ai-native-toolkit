"""The fleet platform pass, ``fleet.py --platform`` (issue #714).

The offline scan (``lib/fleet/runner.py`` ``run_fleet``) never reaches the network.
With ``--platform`` a second, separate pass runs after it: for each repository
whose offline row is ``ok``, one more ``assess_signals.py`` child with
``--platform --scans <PLATFORM_SCANS>`` (``lib/run/signals.py``), the same output
mode and salt, its own worker pool (``--platform-jobs``) and its own per-repo
timeout (``--platform-timeout``). Its blocks are joined into the row's
``scans`` by repository, replacing the offline ``network`` placeholders and adding
the ``PLATFORM_ONLY_SCANS`` blocks (``delivery``) the offline row leaves out, the way
``--settings`` is joined after the scan. The pass never changes a row's
``status`` or ``reason``: a slow or refusing host cannot turn an offline row into
``scan_timeout``. A child that times out, exits non-zero or writes no valid
document gives each platform block ``{"available": false, "reason":
"platform_<token>: ..."}`` (``platform_timeout``, ``platform_exit_<code>``,
``platform_invalid_output``, ``platform_error``; counts-only keeps the code).
A row whose offline scan failed gets no platform read.

The scans run are ``PLATFORM_SCANS`` restricted to ``--scans`` when that is
given. Every child shares one response cache directory for the pass
(``lib/hosting/platform_cli.py``), created here and removed when the pass ends, so an
auth probe or a ``users/<login>[bot]`` lookup is answered once per run.

``after`` (``--delete-after``) runs on each row once its platform read is done,
so a clone is never deleted before the pass has read its remote.

Extension point (#715, #716): a new platform block is a registry scan whose
name is appended to ``PLATFORM_SCANS``; a sample of merged changes shared by
several blocks is a private ``network`` registry spec they ``read``. This module
needs no change for either.

The fleet platform pass, `fleet.py --platform` (issue #714). `run_platform(rows, targets,
options, jobs=, timeout=, names=, after=)` runs after the offline scan: one more
`assess_signals.py --platform --scans <names>` child per `ok` row (`fleet.run_signals`,
the offline scan's output mode and salt, `--platform-jobs` workers, `--platform-timeout`
each, stderr tails in `<logs>/<repo>.platform.stderr`) and `join`s its blocks into the
row's `scans` by repository. It never changes `status` or `reason`, so a slow host cannot
make an offline row `scan_timeout`: a child that times out, exits non-zero, raises or
omits a block gives that block `available: false` with `platform_timeout`,
`platform_exit_<code>`, `platform_error` or `platform_invalid_output` (the code only in
counts-only output). Such a failed block never replaces an offline block that is
available (`join`, #696): `review_evidence` credits an AI review step offline, and a slow
host keeps that credit. Failed rows are not read. `names` is `platform_scans(--scans)`:
`signals.PLATFORM_SCANS` narrowed by `--scans` (`review_automation` and `ci_checks` are
listed because they read GitLab's merged CI configuration, #796). All children share one response cache
directory (`platform_cli.CACHE_ENV`) that the pass creates and removes. `after` (the
`--delete-after` hook) runs on each row after its platform read, so a clone outlives the
read of its remote. A new platform block (#715, #716) needs no change here: see
`signals.py`. Tests: `tests/test_fleet_platform.py`.
"""
from __future__ import annotations

import tempfile
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

from lib.counts_only.content_free import reason_code
from lib.fleet.runner import (
    INVALID_OUTPUT,
    SCAN_ERROR,
    SCAN_TIMEOUT,
    STATUS_OK,
    AfterFn,
    FleetRow,
    ScanOptions,
    Target,
    run_signals,
)
from lib.run_context_types import JsonDict
from lib.run.signals import PLATFORM_SCANS, with_companions

DEFAULT_PLATFORM_JOBS = 4
DEFAULT_PLATFORM_TIMEOUT = 300
PLATFORM_LOG_SUFFIX = ".platform.stderr"

RunFn = Callable[[Target, Path, ScanOptions, str], JsonDict | str]

_TOKENS = {SCAN_TIMEOUT: "platform_timeout", SCAN_ERROR: "platform_error",
           INVALID_OUTPUT: "platform_invalid_output"}


def platform_scans(scans: Sequence[str] | None) -> list[str]:
    """``PLATFORM_SCANS``, restricted to ``scans`` (the ``--scans`` selection, with its
    ``signals.COMPANION_SCANS``) when given."""
    wanted = None if scans is None else set(with_companions(scans))
    return [name for name in PLATFORM_SCANS if wanted is None or name in wanted]


def failure_reason(token: str) -> str:
    """A platform block's reason for a child that gave no document."""
    code = _TOKENS.get(token) or token.replace("scan_", "platform_", 1)
    return f"{code}: the platform pass gave no result for this repository"


def failed_blocks(names: Sequence[str], token: str, counts_only: bool) -> dict[str, JsonDict]:
    reason = failure_reason(token)
    shown = reason_code(reason) if counts_only else reason
    return {name: {"available": False, "reason": shown} for name in names}


def join(row: FleetRow, blocks: dict[str, JsonDict]) -> FleetRow:
    """``row`` with ``blocks`` in its ``scans``; ``status`` and ``reason`` are never touched.

    A failed platform block (``platform_<token>``) never replaces an offline block that is
    available: ``review_evidence`` credits an AI review step offline (#696), and a slow
    host must not take that credit away.
    """
    if row["status"] != STATUS_OK or row["scans"] is None:
        return row
    offline = row["scans"]
    kept = {name: block for name, block in blocks.items()
            if not (_platform_failure(block) and offline.get(name, {}).get("available") is True)}
    return {**row, "scans": {**offline, **kept}}


def _platform_failure(block: JsonDict) -> bool:
    return block.get("available") is False and reason_code(block.get("reason")).startswith("platform_")


def _blocks(document: JsonDict | str, names: Sequence[str], counts_only: bool) -> dict[str, JsonDict]:
    if isinstance(document, str):
        return failed_blocks(names, document, counts_only)
    scans = document["scans"]
    missing = [name for name in names if not isinstance(scans.get(name), dict)]
    blocks = failed_blocks(missing, INVALID_OUTPUT, counts_only)
    blocks.update({name: scans[name] for name in names if name not in missing})
    return blocks


def _one(row: FleetRow, target: Target | None, scratch: Path, options: ScanOptions,
         names: Sequence[str], run: RunFn) -> FleetRow:
    if target is None or row["status"] != STATUS_OK:
        return row
    try:
        document = run(target, scratch, options, PLATFORM_LOG_SUFFIX)
    except Exception:  # noqa: BLE001 - one repository's platform read never stops the run
        document = SCAN_ERROR
    return join(row, _blocks(document, names, options.counts_only))


def run_platform(
    rows: Sequence[FleetRow], targets: Sequence[Target], options: ScanOptions, *,
    jobs: int, timeout: int, names: Sequence[str], after: AfterFn | None = None,
    run: RunFn = run_signals,
) -> list[FleetRow]:
    """Every ok row with its platform blocks joined, sorted by ``repo``.

    ``options`` are the offline scan's; the children run with ``--platform``,
    ``--scans`` set to ``names`` and ``timeout`` per repository.
    """
    by_repo = {target.repo: target for target in targets}

    def one(row: FleetRow, scratch: Path, child: ScanOptions) -> FleetRow:
        target = by_repo.get(row["repo"])
        joined = _one(row, target, scratch, child, names, run) if names else row
        if after is not None and target is not None:
            after(target, joined)
        return joined

    with tempfile.TemporaryDirectory(prefix="fleet-platform-") as scratch, \
            tempfile.TemporaryDirectory(prefix="fleet-platform-cache-") as cache, \
            ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
        child = replace(options, platform=True, scans=",".join(names), timeout=timeout,
                        cache_dir=Path(cache).resolve())
        joined = list(pool.map(lambda r: one(r, Path(scratch), child), rows))
    return sorted(joined, key=lambda r: r["repo"])
