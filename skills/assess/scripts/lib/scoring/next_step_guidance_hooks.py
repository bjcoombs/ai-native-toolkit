"""Repository-native wording for the agent hooks criterion (#846), read by ``next_step_guidance``.

``agent_ops.summary.hooks_present`` (``lib.agents.harness_settings.hooks_present``) reads the hook
files of every harness the scan knows, so its wording names each of them. It is an ``eq true``
flag: ``HOOK_FLAGS`` holds its ``(change, done_when)`` pair and ``lib.scoring.next_step_guidance``
wraps it in its flag wording. It sits apart from ``next_step_guidance.py`` to keep that file under
its size ceiling, and imports nothing. Tests: ``tests/test_fleet_next_steps.py`` (every default
signal has wording).
"""
from __future__ import annotations

HOOK_FLAGS: dict[str, tuple[str, str]] = {
    "agent_ops.summary.hooks_present": (
        "Commit an agent hook: a `hooks` entry in `.claude/settings.json` or `.gemini/settings.json`, "
        "a script in `.claude/hooks/`, a `.cursor/hooks.json`, or a Copilot hook file in "
        "`.github/hooks/`.",
        "`git ls-files` lists one of those files with a hook that runs a command, or a script under "
        "`.claude/hooks/`."),
}
