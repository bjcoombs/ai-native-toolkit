"""Shared setup for the refiner write-script tests (issue #1321).

Puts `skills/refine/scripts/` on `sys.path` and replays recorded `gh` output
from `tests/fixtures/refine_write/` with no network. `FakeGh` maps each read
the `Writer` makes (a `gh api` GET) to a fixture file and records every write
(`gh issue edit`, `gh issue comment`, `gh api -X ...`) without running it.
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Sequence

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "skills" / "refine" / "scripts"
FIXTURES = REPO / "tests" / "fixtures" / "refine_write"
FIXTURE_REPO = "acme/widgets"
NOW = datetime(2026, 11, 25, 12, 0, 0, tzinfo=timezone.utc)

if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import refine_write  # noqa: E402


def is_write(args: Sequence[str]) -> bool:
    if args[:2] in (["issue", "edit"], ["issue", "comment"]):
        return True
    return bool(args) and args[0] == "api" and "-X" in args


class FakeGh:
    """A `Runner` serving fixtures by API path; records reads and writes."""

    def __init__(self, routes: Dict[str, str]) -> None:
        self.routes = {f"repos/{FIXTURE_REPO}/{k}": v for k, v in routes.items()}
        self.reads: List[List[str]] = []
        self.writes: List[List[str]] = []

    def __call__(self, args: Sequence[str]) -> str:
        if is_write(args):
            self.writes.append(list(args))
            return "{}"
        assert args[0] == "api", f"unexpected read: gh {' '.join(args)}"
        path = [a for a in args[1:] if not a.startswith("-")][-1]
        self.reads.append(list(args))
        for key in sorted(self.routes, key=len, reverse=True):
            if path == key or path.startswith(key + "?"):
                value = self.routes[key]
                if value.endswith(".json"):
                    return (FIXTURES / value).read_text(encoding="utf-8")
                return value
        raise AssertionError(f"no fixture for gh {' '.join(args)}")


def settings(tmp: Path, live: bool = True, caps: Optional[Dict[str, int]] = None,
             **extra: str) -> "refine_write.Settings":
    env = {
        "GH_REPO": FIXTURE_REPO,
        "REFINE_MODE": "live" if live else "dry-run",
        "REFINE_ALLOWED_LABELS": "agent-ready,needs-triage,bug,enhancement",
        "REFINE_STATE_DIR": str(tmp / "state"),
    }
    if caps is not None:
        env["CLAUDE_CODE_SCRIPT_CAPS"] = json.dumps(caps)
    env.update(extra)
    return refine_write.Settings(env)


def writer(tmp: Path, routes: Dict[str, str], **kw: object) -> "tuple[refine_write.Writer, FakeGh]":
    gh = FakeGh(routes)
    return refine_write.Writer(settings(tmp, **kw), gh, NOW), gh  # type: ignore[arg-type]


def verdict() -> Dict[str, object]:
    data: Dict[str, object] = json.loads((FIXTURES / "verdict.json").read_text(encoding="utf-8"))
    return data


def actions(tmp: Path) -> List[Dict[str, object]]:
    log = tmp / "state" / "actions.jsonl"
    if not log.is_file():
        return []
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines() if line.strip()]
