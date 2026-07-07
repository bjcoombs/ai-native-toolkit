"""Detect whether an /assess run can prompt a human, and record the offer
lifecycle when it cannot.

/assess makes several consent offers across its run - installing analysis tools,
running the code-modifying mutation pass, opening a PR, tracking findings,
freezing a CI gate, filing feedback, uninstalling. Every one needs a human to
answer. In a **headless or CI** run no human is present, so an offer that blocks
on input would hang the pipeline forever. The contract is therefore explicit:
**when no human can answer, every offer is treated as declined** and recorded,
so a non-interactive run completes with zero interactive prompts and an audit
trail of what was skipped and why.

Detection is `sys.stdin.isatty() and not os.getenv("CI")`: a non-tty stdin
(piped, redirected, no controlling terminal) *or* a truthy `CI` env var (the
convention every major CI provider sets) means non-interactive. The `CI` check
matters because some CI runners allocate a pseudo-tty, which would otherwise
read as interactive.

Pure stdlib; no side effects. The orchestrator reads `interactive` /`offers`
from run-context.json and honours the same contract for the offers it makes by
hand (AskUserQuestion), which a script cannot make for it.
"""
from __future__ import annotations

import os
import sys
from typing import Any, TextIO

# Canonical offer types spanning the skill's whole consent lifecycle. The three
# interactive phases plus the end-of-run uninstall offer:
#   Phase 1 (tool installs):      tool_install
#   Phase 3 (code modification):  mutation
#   Phase 2 (write-back):         pr, issue_tracking, ci_gate, feedback
#   End-of-run:                   uninstall
OFFER_TYPES: tuple[str, ...] = (
    "tool_install",
    "mutation",
    "pr",
    "issue_tracking",
    "ci_gate",
    "feedback",
    "uninstall",
)


def is_interactive(
    *, stdin: TextIO | None = None, env: dict[str, str] | None = None
) -> bool:
    """True only when a human could answer a prompt.

    Non-interactive when ``CI`` is set (truthy) or stdin is not a tty. A stdin
    object without a usable ``isatty`` (closed, replaced) is treated as
    non-interactive - the safe default is to never block.
    """
    environ = env if env is not None else os.environ
    if environ.get("CI"):
        return False
    stream = stdin if stdin is not None else sys.stdin
    try:
        return bool(stream.isatty())
    except (ValueError, AttributeError):
        return False


def non_interactive_offers(
    offer_types: tuple[str, ...] = OFFER_TYPES,
    *,
    reason: str = "non-interactive",
) -> list[dict[str, str]]:
    """Every offer recorded as skipped - the headless/CI decline contract."""
    return [
        {"type": t, "status": "skipped", "reason": reason} for t in offer_types
    ]


def build_offers_block(
    *, stdin: TextIO | None = None, env: dict[str, str] | None = None
) -> dict[str, Any]:
    """Run-context ``offers`` block plus the ``interactive`` flag.

    Interactive: ``offers`` is empty - the orchestrator makes the offers live
    via AskUserQuestion. Non-interactive: every offer is pre-recorded as
    skipped, and the orchestrator must make **no** prompts.
    """
    interactive = is_interactive(stdin=stdin, env=env)
    return {
        "interactive": interactive,
        "offers": [] if interactive else non_interactive_offers(),
    }
