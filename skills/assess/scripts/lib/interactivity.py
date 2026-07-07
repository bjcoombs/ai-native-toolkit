"""Decide whether an /assess run may prompt a human, and record the offer
lifecycle when it may not.

/assess makes several consent offers across its run - installing analysis tools,
running the code-modifying mutation pass, opening a PR, tracking findings,
freezing a CI gate, filing feedback, uninstalling. Every one needs a human to
answer. In a **headless or CI** run no human is present, so an offer that blocks
on input would hang the pipeline forever. The contract is therefore explicit:
**when no human can answer, every offer is treated as declined** and recorded,
so a non-interactive run completes with zero interactive prompts and an audit
trail of what was skipped and why.

**Why not `isatty()`.** The core (`assess_core.py`) is launched via
`uv run "$SKILL_DIR/scripts/assess_core.py"` from a Bash tool, so it runs as a
tool-invoked subprocess with **no controlling terminal** - `sys.stdin.isatty()`
is False even in a perfectly normal interactive `/assess`. A subprocess stdin
TTY says nothing about whether the orchestrator (the agent running SKILL.md) can
prompt the human via AskUserQuestion, which is a model-level tool that works
regardless of subprocess stdin. So the decider is an **explicit signal the
orchestrator passes in**, not a stdin probe: the run is interactive by default,
and non-interactive only when the orchestrator marks it so (the `--non-interactive`
flag / ``ASSESS_NON_INTERACTIVE`` env var it sets on its own headless/CI path) or
when the ``CI`` env var is set. The orchestrator self-determines interactivity in
Phase 1 (before the core runs) from its own runtime context; passing the same
signal into the core keeps Phases 2/3 consistent with Phase 1.

Pure stdlib; no side effects. The orchestrator reads `interactive` /`offers`
from run-context.json and honours the same contract for the offers it makes by
hand (AskUserQuestion), which a script cannot make for it.
"""
from __future__ import annotations

import os
from typing import Any

# Env var the orchestrator may set (as an alternative to the --non-interactive
# CLI flag) to mark a headless/CI run. Any truthy value counts.
NON_INTERACTIVE_ENV: str = "ASSESS_NON_INTERACTIVE"

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
    *, non_interactive: bool = False, env: dict[str, str] | None = None
) -> bool:
    """True unless an explicit non-interactive signal is present.

    Interactive is the **default** - a normal /assess invocation. Non-interactive
    only when the orchestrator says so: ``non_interactive=True`` (the caller's
    headless/CI path, set from its own runtime context), or ``CI`` /
    ``ASSESS_NON_INTERACTIVE`` set truthy in the environment. ``isatty()`` is
    deliberately not consulted - the core always runs as a subprocess with no
    controlling terminal, so it is never a valid interactivity signal here.
    """
    if non_interactive:
        return False
    environ = env if env is not None else os.environ
    if environ.get("CI"):
        return False
    if environ.get(NON_INTERACTIVE_ENV):
        return False
    return True


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
    *, non_interactive: bool = False, env: dict[str, str] | None = None
) -> dict[str, Any]:
    """Run-context ``offers`` block plus the ``interactive`` flag.

    Interactive: ``offers`` is empty - the orchestrator makes the offers live
    via AskUserQuestion. Non-interactive: every offer is pre-recorded as
    skipped, and the orchestrator must make **no** prompts.
    """
    interactive = is_interactive(non_interactive=non_interactive, env=env)
    return {
        "interactive": interactive,
        "offers": [] if interactive else non_interactive_offers(),
    }
