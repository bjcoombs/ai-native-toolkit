#!/usr/bin/env python3
"""The refiner's settings, read from the environment the workflow sets (issue #1321).

Moved out of `refine_write.py` (#1345), which re-exports `Settings` and the
defaults below, so `refine_write.Settings` stays the name callers use. The
variables are described in `refine_write.py`'s docstring and in the reusable
workflows' inputs.

Python 3.8+, stdlib only.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from refine_definition import parse_override, size_limit

Json = Dict[str, Any]

DEFAULT_READY = "agent-ready"
DEFAULT_TRIAGE = "needs-triage"
DEFAULT_BOT = "github-actions[bot]"
DEFAULT_CLOSE_CAP = 10
DEFAULT_MAX_POINTS = 8


def _csv(value: str) -> List[str]:
    return [v.strip() for v in value.split(",") if v.strip()]


class Settings:
    """The refiner's settings, read from the environment the workflow sets."""

    def __init__(self, env: Optional[Dict[str, str]] = None) -> None:
        e = dict(os.environ if env is None else env)
        self.platform = e.get("REFINE_PLATFORM") or "github"  # "gitlab": refine_write_gitlab.GitLabWriter
        self.repo = e.get("GH_REPO") or e.get("GITHUB_REPOSITORY") or e.get("CI_PROJECT_PATH") or ""
        self.live = e.get("REFINE_MODE", "") == "live"
        self.ready_label = e.get("REFINE_READY_LABEL") or DEFAULT_READY
        self.triage_label = e.get("REFINE_TRIAGE_LABEL") or DEFAULT_TRIAGE
        self.allowed_labels: Set[str] = set(_csv(e.get("REFINE_ALLOWED_LABELS", "")))
        self.allowed_labels |= {self.ready_label, self.triage_label}
        self.bot_login = e.get("REFINE_BOT_LOGIN") or DEFAULT_BOT
        self.bot_login_set = bool(e.get("REFINE_BOT_LOGIN"))
        self.close_cap = int(e.get("REFINE_CLOSE_CAP") or DEFAULT_CLOSE_CAP)
        self.max_points_override = parse_override(e.get("REFINE_MAX_POINTS", ""))  # fails before any write
        q = e.get("REFINE_QUESTIONS_ISSUE", "").strip().lstrip("#")
        self.questions_issue: Optional[int] = int(q) if q.isdigit() else None
        caps = e.get("CLAUDE_CODE_SCRIPT_CAPS", "").strip()
        self.caps: Optional[Dict[str, int]] = None
        if caps:
            parsed = json.loads(caps)
            if not isinstance(parsed, dict):
                raise ValueError("CLAUDE_CODE_SCRIPT_CAPS must be a JSON object")
            self.caps = {str(k): int(v) for k, v in parsed.items()}
        base = e.get("REFINE_STATE_DIR") or str(Path(e.get("RUNNER_TEMP") or tempfile.gettempdir()) / "refine")
        self.state_dir = Path(base)
        self.actions_log = Path(e.get("REFINE_ACTIONS_LOG") or str(self.state_dir / "actions.jsonl"))

    def max_points(self, verdict: Optional[Json]) -> int:  # the limit the verdict's refine.py run used
        return size_limit(self.max_points_override, DEFAULT_MAX_POINTS, (verdict or {}).get("definition"))
