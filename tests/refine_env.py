"""Shared setup for the refinement script tests (issue #1320).

Puts `skills/refine/scripts/` on `sys.path` so the tests import the script's
modules as the script does, and replays the recorded `gh` output in
`tests/fixtures/refine/` with no network: `recorded_runner` maps each `gh`
call the GitHub adapter makes to a fixture file, after the same read-only
check the real runner applies.
"""
import sys
from pathlib import Path
from typing import List, Sequence

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "skills" / "refine" / "scripts"
ENTRY = REPO / "skills" / "refine" / "scripts" / "refine.py"
FIXTURES = REPO / "tests" / "fixtures" / "refine"
DEFINITION = REPO / "docs" / "contributing" / "definition-of-ready.md"
SCHEMA = SCRIPTS / "refine-output.schema.json"
NOW = "2026-11-25T12:00:00Z"
FIXTURE_REPO = "acme/widgets"

if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import refine_platform  # noqa: E402


class RecordedRunner:
    """A `Runner` that serves fixture files and records every call."""

    def __init__(self, fixtures: Path = FIXTURES) -> None:
        self.fixtures = fixtures
        self.calls: List[List[str]] = []

    def __call__(self, args: Sequence[str]) -> str:
        refine_platform.check_read_only(args)
        self.calls.append(list(args))
        if args[:2] == ["pr", "list"]:
            name = "open_prs.json"
        elif args[:2] == ["issue", "list"] and "closed" in args:
            name = "closed_issues.json"
        elif args[:2] == ["issue", "list"]:
            name = "open_issues.json"
        else:
            raise AssertionError(f"no fixture for gh {' '.join(args)}")
        return (self.fixtures / name).read_text(encoding="utf-8")


def recorded_backlog(fixtures: Path = FIXTURES) -> "refine_platform.GitHubBacklog":
    return refine_platform.GitHubBacklog(FIXTURE_REPO, runner=RecordedRunner(fixtures))
