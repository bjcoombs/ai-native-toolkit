"""History windows and day counts are counted from the assessed commit (issue #773).

Every test here runs on ``dated_git_repo``: absolute dates, HEAD committed at
2020-01-15T12:00:00Z. Measured from today every window in it is empty and every
age is years long, so each expectation below fails if a scan reads the clock.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from lib.ai_usage import scan_ai_usage
from lib.doc_staleness import analyze_doc_staleness, content_clock
from lib.commit_anchor import head_commit_epoch
from lib.git_churn import commit_epochs_by_file, git_churn_scores
from lib.history_window import anchored_since
from lib.instruction_files import _file_freshness_days
from lib.understanding_analysis import _days_since_last_human_commit

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
HEAD_AT = datetime(2020, 1, 15, 12, tzinfo=timezone.utc)


def _epoch(*parts: int) -> int:
    return int(datetime(*parts, tzinfo=timezone.utc).timestamp())  # type: ignore[misc]


@pytest.mark.parametrize(("window", "anchor", "expected"), [
    ("12mo", _epoch(2020, 1, 15, 12), _epoch(2019, 1, 15, 12)),
    ("6mo", _epoch(2020, 8, 31, 9), _epoch(2020, 2, 29, 9)),   # clamped to the month end
    ("1y", _epoch(2020, 2, 29), _epoch(2019, 2, 28)),
    ("90d", _epoch(2020, 1, 15, 12), _epoch(2020, 1, 15, 12) - 90 * 86400),
    ("2w", _epoch(2020, 1, 15), _epoch(2020, 1, 1)),
])
def test_anchored_since_counts_back_by_calendar(window: str, anchor: int, expected: int) -> None:
    assert anchored_since(window, anchor) == f"@{expected}"


def test_anchored_since_all_history_and_out_of_range() -> None:
    assert anchored_since(None, _epoch(2020, 1, 1)) is None
    # git reads a number as an epoch only from nine digits (1973-03-03), so a
    # start before that, or before year 1, is all history.
    assert anchored_since("5y", _epoch(1975, 1, 1)) is None
    assert anchored_since("3000y", _epoch(2020, 1, 1)) is None
    with pytest.raises(ValueError):
        anchored_since("6 months", _epoch(2020, 1, 1))


def test_head_commit_epoch(dated_git_repo: Path, tmp_path: Path) -> None:
    assert head_commit_epoch(dated_git_repo) == int(HEAD_AT.timestamp())
    assert head_commit_epoch(tmp_path / "missing") is None


def test_churn_window_is_anchored_to_head_not_today(dated_git_repo: Path) -> None:
    app = (dated_git_repo / "pkg" / "app.py").resolve()
    # 12mo from HEAD starts 2019-01-15: the five edits and HEAD, not the 2019-01-10 init.
    assert git_churn_scores(dated_git_repo, window="12mo")[app] == 6
    epochs = commit_epochs_by_file(dated_git_repo, "12mo")
    assert epochs is not None and len(epochs[app]) == 6
    assert git_churn_scores(dated_git_repo)[app] == 7


def test_windows_degrade_without_an_anchor(tmp_path: Path) -> None:
    """No HEAD means no anchor: an empty result, never a silent all-history read."""
    empty = tmp_path / "empty"
    empty.mkdir()
    subprocess.run(["git", "-C", str(empty), "init", "-q"], check=True)
    assert git_churn_scores(empty, window="12mo") == {}
    assert commit_epochs_by_file(empty, "12mo") is None


def test_last_commit_days_counts_from_head(dated_git_repo: Path) -> None:
    block = analyze_doc_staleness(dated_git_repo)
    days = {d["path"]: d["last_commit_days"] for d in block["docs"]}
    assert days["README.md"] == 370  # 2019-01-10 to 2020-01-15
    assert block["summary"]["stale_pct"] is not None


def test_instruction_freshness_counts_from_head(dated_git_repo: Path) -> None:
    clock = content_clock(dated_git_repo)
    assert clock.anchor_epoch == int(HEAD_AT.timestamp())
    assert _file_freshness_days(dated_git_repo / "CLAUDE.md", clock) == 320


def test_ai_usage_window_is_anchored(dated_git_repo: Path) -> None:
    block = scan_ai_usage(dated_git_repo, None, "6mo")
    assert block["available"] is True
    assert block["total_commits"] == 6  # from 2019-07-15: five edits and HEAD
    assert block["agent_trailer_commits"] == 1
    assert block["since"] == anchored_since("6mo", int(HEAD_AT.timestamp()))


def test_days_since_human_commit_counts_from_head(dated_git_repo: Path) -> None:
    anchor = int(HEAD_AT.timestamp())
    assert _days_since_last_human_commit(str(dated_git_repo), "README.md", anchor) == 370
    assert _days_since_last_human_commit(str(dated_git_repo), "README.md", None) is None


# Replaces the clocks module-wide, so a read in any module moves, not only the
# allowlisted run-metadata sites.
_SITECUSTOMIZE = '''
import datetime as _d, os as _os, time as _t
_EPOCH = float(_os.environ["FAKE_CLOCK_EPOCH"])
class _DateTime(_d.datetime):
    @classmethod
    def now(cls, tz=None):
        return cls.fromtimestamp(_EPOCH, tz)
    @classmethod
    def utcnow(cls):
        return cls.fromtimestamp(_EPOCH, _d.timezone.utc).replace(tzinfo=None)
    @classmethod
    def today(cls):
        return cls.fromtimestamp(_EPOCH)
class _Date(_d.date):
    @classmethod
    def today(cls):
        return cls.fromtimestamp(_EPOCH)
_d.datetime = _DateTime
_d.date = _Date
_t.time = lambda: _EPOCH
_t.time_ns = lambda: int(_EPOCH * 1e9)
'''


def _run_at(repo: Path, out: Path, fake: Path, epoch: int) -> dict[str, object]:
    env = {**os.environ, "PYTHONPATH": str(fake), "FAKE_CLOCK_EPOCH": str(epoch),
           "GIT_TEST_DATE_NOW": str(epoch)}
    year = subprocess.run(
        [sys.executable, "-c", "import datetime; print(datetime.datetime.now().year)"],
        env=env, capture_output=True, text=True, check=True).stdout.strip()
    assert year == str(datetime.fromtimestamp(epoch, timezone.utc).year), "fake clock not applied"
    subprocess.run([sys.executable, str(SCRIPTS / "assess_signals.py"), str(repo), "--out", str(out)],
                   env=env, capture_output=True, text=True, check=True)
    doc: dict[str, object] = json.loads(out.read_text(encoding="utf-8"))
    return doc


def test_same_commit_same_signals_on_two_dates(dated_git_repo: Path, tmp_path: Path) -> None:
    fake = tmp_path / "fakeclock"
    fake.mkdir()
    (fake / "sitecustomize.py").write_text(_SITECUSTOMIZE, encoding="utf-8")
    first = _run_at(dated_git_repo, tmp_path / "a.json", fake, _epoch(2027, 3, 1))
    second = _run_at(dated_git_repo, tmp_path / "b.json", fake, _epoch(2031, 9, 15))

    assert json.dumps(first["scans"], sort_keys=True) == json.dumps(second["scans"], sort_keys=True)
    # Not vacuous: two runs that read the clock agree too, with every window empty.
    scans = first["scans"]
    assert isinstance(scans, dict)
    assert scans["ai_usage"]["total_commits"] == 6
    assert scans["ai_usage"]["agent_trailer_commits"] == 1
    assert scans["doc_staleness"]["summary"]["stale_pct"] is not None
    days = {d["path"]: d["last_commit_days"] for d in scans["doc_staleness"]["docs"]}
    assert days["README.md"] == 370
