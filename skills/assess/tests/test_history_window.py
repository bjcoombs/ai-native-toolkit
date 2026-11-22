"""The one history-window grammar (issue #601) and the shallow-boundary
reachability check (issue #633)."""
from __future__ import annotations

import re
import subprocess
from datetime import date
from pathlib import Path

import pytest

from lib import ai_usage, fleet_activity, git_churn, history_window, signals
from lib.history_window import WINDOW_PATTERN, split_window

LIB = Path(__file__).resolve().parents[1] / "scripts" / "lib"
VALID = ["1d", "90d", "12w", "6mo", "24mo", "1y", "5y"]
INVALID = ["", "0d", "06mo", "6m", "6 mo", "mo", "6mos", "-1y", "1.5y", "six months"]


@pytest.mark.parametrize(("window", "parts"), [
    ("90d", (90, "d")), ("12w", (12, "w")), ("6mo", (6, "mo")), ("1y", (1, "y")),
])
def test_split_window(window: str, parts: tuple[int, str]) -> None:
    assert split_window(window) == parts


@pytest.mark.parametrize("window", INVALID)
def test_split_window_rejects(window: str) -> None:
    with pytest.raises(ValueError):
        split_window(window)


@pytest.mark.parametrize("window", ["6mo\n", "6mo\r\n", " 6mo", "6mo "])
def test_split_window_rejects_surrounding_whitespace(window: str) -> None:
    """``$`` matches before a trailing newline; the whole string must match, as
    the ECMA-262 ``run.schema.json`` pattern requires (issue #648)."""
    with pytest.raises(ValueError):
        split_window(window)


@pytest.mark.parametrize("window", VALID + INVALID)
def test_every_reader_agrees_with_the_grammar(window: str) -> None:
    """signals, ai_usage, fleet_activity and the churn-label normaliser accept
    exactly the windows the shared grammar accepts."""
    valid = window in VALID
    for read in (signals.parse_window,
                 ai_usage.resolve_window,
                 lambda w: fleet_activity.window_start(w, date(2026, 1, 31))):
        if valid:
            read(window)
        else:
            with pytest.raises(ValueError):
                read(window)
    found = git_churn.window_from_churn_label(f"commits (last {window})")
    assert found == (window if valid else None)
    assert (re.fullmatch(WINDOW_PATTERN, window) is not None) == valid


def test_the_grammar_is_defined_once() -> None:
    """No lib module other than history_window spells out the unit alternation
    as a regex group; fleet_schema builds its run.json pattern from it."""
    units = re.compile(r"\((?:\?:)?d\|w\|mo\|y\)")
    holders = sorted(p.name for p in LIB.glob("*.py") if units.search(p.read_text(encoding="utf-8")))
    assert holders == [Path(history_window.__file__).name]


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True, encoding="utf-8", errors="surrogateescape", check=True).stdout.strip()


def test_reaches_from_head(tmp_path: Path) -> None:
    repo = tmp_path / "r"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "-c", "user.name=T", "-c", "user.email=t@example.com",
         "commit", "-q", "--allow-empty", "-m", "base")
    base = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", "-b", "side")
    _git(repo, "-c", "user.name=T", "-c", "user.email=t@example.com",
         "commit", "-q", "--allow-empty", "-m", "side")
    side = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", "main")

    assert git_churn._reaches_from_head(str(repo), base) is True
    assert git_churn._reaches_from_head(str(repo), side) is False
    # git cannot say (an unknown object): the boundary is kept, as before #633.
    assert git_churn._reaches_from_head(str(repo), "0" * 40) is True
