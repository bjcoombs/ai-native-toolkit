"""How ``gh`` and ``glab`` are run: no prompt, rate-limit backoff, a per-run response cache (#714).

``gh_cli._run_gh`` and ``gitlab_cli._run_glab`` run their binary through
:func:`run_cli`. It adds, for every call:

- **No prompt.** stdin is ``/dev/null`` and the environment sets
  ``GH_PROMPT_DISABLED``, ``NO_PROMPT`` (glab) and ``GIT_TERMINAL_PROMPT=0``,
  so a credential or confirmation prompt fails the call instead of waiting for
  a person who is not there. The rest of the environment is inherited.
- **429 backoff.** A call whose output reports HTTP 429 is retried up to
  ``MAX_RETRIES`` times. Each wait is the ``Retry-After`` seconds when the CLI
  printed that header (``gh api --include`` does; a plain call does not), else
  ``BASE_WAIT * 2**attempt``; never more than ``MAX_WAIT`` per wait. The last
  429 is returned as the call's result, and the caller maps it to a
  ``rate_limited`` reason. The sleep is ``SLEEP`` (a test replaces it).
- **Response cache.** When ``CACHE_ENV`` names a directory, a successful call's
  stdout is stored there under the sha256 of its argv, and the same argv later
  in the run (from any process) is answered from the file without running the
  CLI. Only successes are stored. ``fleet --platform`` sets it for its platform
  children only, to a temporary directory removed when the pass ends: cached
  responses hold titles and account names, so they never land in ``--out``, the
  run history or a target. Without it nothing is cached.

Callers keep their own ``FileNotFoundError`` and ``subprocess.TimeoutExpired``
mapping; this module raises both unchanged. Pure subprocess + stdlib.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import NamedTuple

CACHE_ENV = "ASSESS_PLATFORM_CACHE"
NO_PROMPT_ENV: Mapping[str, str] = {
    "GH_PROMPT_DISABLED": "1",
    "NO_PROMPT": "1",  # glab
    "GIT_TERMINAL_PROMPT": "0",
}
MAX_RETRIES = 3
BASE_WAIT = 2.0
MAX_WAIT = 60.0

_RATE_LIMITED_RE = re.compile(r"\(HTTP 429\)|\b429 Too Many Requests\b", re.IGNORECASE)
_RETRY_AFTER_RE = re.compile(r"^\s*Retry-After:\s*(\d+)\s*$", re.IGNORECASE | re.MULTILINE)

SLEEP: Callable[[float], None] = time.sleep


class CliResult(NamedTuple):
    returncode: int
    stdout: str
    stderr: str


def rate_limited(text: str) -> bool:
    """True when a CLI's output reports HTTP 429."""
    return bool(_RATE_LIMITED_RE.search(text))


def retry_wait(output: str, attempt: int) -> float:
    """Seconds to wait before retry ``attempt`` (0-based): ``Retry-After`` when printed, else
    exponential; at most ``MAX_WAIT``."""
    m = _RETRY_AFTER_RE.search(output)
    wait = float(m.group(1)) if m else BASE_WAIT * 2 ** attempt
    return min(wait, MAX_WAIT)


def cli_env() -> dict[str, str]:
    return {**os.environ, **NO_PROMPT_ENV}


def _cache_path(cmd: list[str]) -> Path | None:
    root = os.environ.get(CACHE_ENV)
    if not root:
        return None
    key = hashlib.sha256(json.dumps(cmd).encode("utf-8")).hexdigest()
    return Path(root) / key


def _cached(path: Path | None) -> str | None:
    if path is None:
        return None
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _store(path: Path | None, stdout: str) -> None:
    """Write through a temp file and ``os.replace``, so a concurrent reader never sees half a
    response. A write that fails is skipped: the cache is a saving, never the record."""
    if path is None:
        return
    try:
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(stdout)
        os.replace(tmp, path)
    except OSError:
        return


def _once(cmd: list[str], timeout: int) -> CliResult:
    proc = subprocess.run(
        cmd, capture_output=True, text=True, stdin=subprocess.DEVNULL, env=cli_env(),
        encoding="utf-8", errors="replace", timeout=timeout,
    )
    return CliResult(proc.returncode, proc.stdout or "", proc.stderr or "")


def run_cli(cmd: list[str], timeout: int) -> CliResult:
    """Run ``cmd`` (``gh ...`` or ``glab ...``) with the rules above."""
    path = _cache_path(cmd)
    hit = _cached(path)
    if hit is not None:
        return CliResult(0, hit, "")
    result = _once(cmd, timeout)
    for attempt in range(MAX_RETRIES):
        output = result.stderr + "\n" + result.stdout
        if result.returncode == 0 or not rate_limited(output):
            break
        SLEEP(retry_wait(output, attempt))
        result = _once(cmd, timeout)
    if result.returncode == 0:
        _store(path, result.stdout)
    return result
