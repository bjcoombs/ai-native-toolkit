"""Shared setup for the GitLab refiner tests (issue #1327).

Puts `skills/refine/scripts/` on `sys.path` and replays recorded `glab api`
output from `tests/fixtures/refine_gitlab/` with no network. `FakeGlab` maps
each GET to a fixture by its API path (the query string is ignored, and the
project path is written `P`), and records every write (`glab api -X ...`)
without running it.
"""
import sys
from pathlib import Path
from typing import Dict, List, Sequence

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "skills" / "refine" / "scripts"
FIXTURES = REPO / "tests" / "fixtures" / "refine_gitlab"
PROJECT = "acme/widgets"
P = "projects/acme%2Fwidgets"

if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import refine_platform_gitlab  # noqa: E402


class FakeGlab:
    """A `Runner` serving fixtures by API path; reads pass the read-only check."""

    def __init__(self, routes: Dict[str, str]) -> None:
        self.routes = {k.replace("P/", P + "/") if k.startswith("P/") else (P if k == "P" else k): v
                       for k, v in routes.items()}
        self.reads: List[List[str]] = []
        self.writes: List[List[str]] = []

    def __call__(self, args: Sequence[str]) -> str:
        if "-X" in args:
            self.writes.append(list(args))
            return "{}"
        refine_platform_gitlab.check_glab_read_only(args)
        self.reads.append(list(args))
        full = [a for a in args[1:] if not a.startswith("-")][-1]
        path, _, query = full.partition("?")
        # A key may carry `?<text>`: it then matches only a call whose query holds that text.
        for key in sorted(self.routes, key=len, reverse=True):
            base, _, needle = key.partition("?")
            if base == path and needle in query:
                value = self.routes[key]
                break
        else:
            raise AssertionError(f"no fixture for glab {' '.join(args)}")
        if value.endswith(".json"):
            return (FIXTURES / value).read_text(encoding="utf-8")
        return value

    def paths(self) -> List[str]:
        return [[a for a in r[1:] if not a.startswith("-")][-1] for r in self.reads]


BACKLOG_ROUTES = {
    "P/issues": "issues_open.json",
    "P/issues/1/notes": "notes_1.json",
    "P/issues/2/notes": "notes_2.json",
    "P/issues/1/links": "links_1.json",
    "P/issues/2/links": "links_2.json",
    "P/members/all": "members.json",
    "P/merge_requests": "mrs_open.json",
    "P/merge_requests/7/closes_issues": "mr_7_closes.json",
    "P/merge_requests/7/diffs": "mr_7_diffs.json",
}
