"""Tests for the non-interactive consent contract (Task 13).

The decider is an **explicit** signal the orchestrator passes in, never a
subprocess stdin probe: the core always runs under `uv run ...` from a Bash
tool with no controlling terminal, so `isatty()` is False even in a normal
interactive /assess. A run is interactive by default and non-interactive only
when marked so (the --non-interactive flag / ASSESS_NON_INTERACTIVE env var) or
when CI is set.
"""
from __future__ import annotations

from lib.interactivity import (
    NON_INTERACTIVE_ENV,
    OFFER_TYPES,
    build_offers_block,
    is_interactive,
    non_interactive_offers,
)


# ── detection ───────────────────────────────────────────────────────────────

def test_default_run_is_interactive() -> None:
    # No flag, no CI env: a normal /assess invocation is interactive.
    assert is_interactive(env={}) is True


def test_explicit_flag_forces_non_interactive() -> None:
    assert is_interactive(non_interactive=True, env={}) is False


def test_ci_env_forces_non_interactive() -> None:
    assert is_interactive(env={"CI": "true"}) is False


def test_assess_non_interactive_env_forces_non_interactive() -> None:
    assert is_interactive(env={NON_INTERACTIVE_ENV: "1"}) is False


def test_empty_ci_env_is_not_ci() -> None:
    assert is_interactive(env={"CI": ""}) is True


def test_empty_non_interactive_env_is_interactive() -> None:
    assert is_interactive(env={NON_INTERACTIVE_ENV: ""}) is True


def test_flag_wins_even_without_env() -> None:
    # The explicit flag alone is sufficient; no env var needed.
    assert is_interactive(non_interactive=True, env={"CI": ""}) is False


# ── offer recording ─────────────────────────────────────────────────────────

def test_non_interactive_offers_all_skipped() -> None:
    offers = non_interactive_offers()
    assert {o["type"] for o in offers} == set(OFFER_TYPES)
    assert all(o["status"] == "skipped" for o in offers)
    assert all(o["reason"] == "non-interactive" for o in offers)


def test_block_default_run_presents_offers() -> None:
    # A normal (no-flag, no-CI) run is interactive: offers stay empty for the
    # orchestrator to present live, NOT pre-recorded as skipped.
    block = build_offers_block(env={})
    assert block["interactive"] is True
    assert block["offers"] == []


def test_block_non_interactive_records_every_offer_skipped() -> None:
    block = build_offers_block(non_interactive=True, env={})
    assert block["interactive"] is False
    assert len(block["offers"]) == len(OFFER_TYPES)
    # Mutation (code modification) is recorded as skipped like every other offer.
    assert any(o["type"] == "mutation" and o["status"] == "skipped"
               for o in block["offers"])


def test_block_ci_env_records_every_offer_skipped() -> None:
    block = build_offers_block(env={"CI": "true"})
    assert block["interactive"] is False
    assert len(block["offers"]) == len(OFFER_TYPES)


def test_offer_types_cover_all_three_phases_plus_uninstall() -> None:
    assert "tool_install" in OFFER_TYPES      # Phase 1
    assert "mutation" in OFFER_TYPES          # Phase 3
    assert {"pr", "issue_tracking", "ci_gate", "feedback"} <= set(OFFER_TYPES)  # Phase 2
    assert "uninstall" in OFFER_TYPES         # end-of-run
