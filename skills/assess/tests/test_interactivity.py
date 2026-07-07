"""Tests for the non-interactive consent contract (Task 13)."""
from __future__ import annotations

import io

from lib.interactivity import (
    OFFER_TYPES,
    build_offers_block,
    is_interactive,
    non_interactive_offers,
)


class _Tty(io.StringIO):
    def __init__(self, tty: bool) -> None:
        super().__init__()
        self._tty = tty

    def isatty(self) -> bool:  # noqa: D401
        return self._tty


# ── detection ───────────────────────────────────────────────────────────────

def test_tty_no_ci_is_interactive() -> None:
    assert is_interactive(stdin=_Tty(True), env={}) is True


def test_non_tty_is_non_interactive() -> None:
    assert is_interactive(stdin=_Tty(False), env={}) is False


def test_ci_env_forces_non_interactive_even_with_tty() -> None:
    # A CI runner may allocate a pseudo-tty; CI=true still wins.
    assert is_interactive(stdin=_Tty(True), env={"CI": "true"}) is False


def test_empty_ci_env_is_not_ci() -> None:
    assert is_interactive(stdin=_Tty(True), env={"CI": ""}) is True


def test_closed_stream_degrades_to_non_interactive() -> None:
    class _Broken:
        def isatty(self) -> bool:
            raise ValueError("closed")

    assert is_interactive(stdin=_Broken(), env={}) is False


# ── offer recording ─────────────────────────────────────────────────────────

def test_non_interactive_offers_all_skipped() -> None:
    offers = non_interactive_offers()
    assert {o["type"] for o in offers} == set(OFFER_TYPES)
    assert all(o["status"] == "skipped" for o in offers)
    assert all(o["reason"] == "non-interactive" for o in offers)


def test_block_interactive_leaves_offers_empty() -> None:
    block = build_offers_block(stdin=_Tty(True), env={})
    assert block["interactive"] is True
    assert block["offers"] == []


def test_block_non_interactive_records_every_offer_skipped() -> None:
    block = build_offers_block(stdin=_Tty(False), env={})
    assert block["interactive"] is False
    assert len(block["offers"]) == len(OFFER_TYPES)
    # Mutation (code modification) is recorded as skipped like every other offer.
    assert any(o["type"] == "mutation" and o["status"] == "skipped"
               for o in block["offers"])


def test_offer_types_cover_all_three_phases_plus_uninstall() -> None:
    assert "tool_install" in OFFER_TYPES      # Phase 1
    assert "mutation" in OFFER_TYPES          # Phase 3
    assert {"pr", "issue_tracking", "ci_gate", "feedback"} <= set(OFFER_TYPES)  # Phase 2
    assert "uninstall" in OFFER_TYPES         # end-of-run
