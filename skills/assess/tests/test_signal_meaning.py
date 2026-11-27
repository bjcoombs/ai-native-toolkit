"""Tests for the signals whose meaning changed at a schema bump, lib/signal_meaning.py (issue #867)."""
from __future__ import annotations

import lib.signal_meaning as sm
from lib.run.signals import SIGNALS_SCHEMA_VERSION


def test_every_schema_version_says_which_signals_changed_meaning():
    # a SIGNALS_SCHEMA_VERSION bump without an entry would compare a changed signal as a move
    assert sorted(sm.MEANING_CHANGES) == list(range(2, SIGNALS_SCHEMA_VERSION + 1))


def test_stale_paths_collect_every_bump_between_two_versions_in_either_order():
    assert sm.stale_paths(4, 4) == frozenset()
    assert sm.stale_paths(2, 4) == frozenset(sm.MEANING_CHANGES[3] + sm.MEANING_CHANGES[4])
    assert sm.stale_paths(4, 2) == sm.stale_paths(2, 4)
    assert "observability.rung" in (sm.stale_paths(3, 4) or ())


def test_a_version_this_toolkit_does_not_know_gives_none():
    assert sm.stale_paths(1, SIGNALS_SCHEMA_VERSION + 1) is None
    assert sm.stale_paths(None, 1) is None
    assert sm.stale_paths(True, 1) is None
    assert sm.stale_paths(0, 1) is None
    assert sm.stale_paths(99, 99) == frozenset()  # one version, even an unknown one
    assert sm.stale_paths(99, 98) is None


def test_without_drops_leaves_and_blocks_and_leaves_the_input_alone():
    scans = {"observability": {"rung": 2, "instrumented": {"present": True}}, "ci": {"n": 1}}
    out = sm.without(scans, ["observability.rung", "ci", "absent.path", "ci.n"])
    assert out == {"observability": {"instrumented": {"present": True}}}
    assert scans["observability"]["rung"] == 2 and "ci" in scans
    assert sm.without(None, ["a"]) is None


def test_held_and_reads():
    assert sm.held({"a": {"b": 0}}, "a.b") and not sm.held({"a": {"b": 0}}, "a.c")
    assert not sm.held({"a": 1}, "a.b")
    assert sm.reads("observability.rung", {"observability.rung"})
    assert sm.reads("observability", {"observability.rung"})
    assert sm.reads("observability.instrumented", {"observability.instrumented.present"})
    assert not sm.reads("observability.run", {"observability.rung"})
