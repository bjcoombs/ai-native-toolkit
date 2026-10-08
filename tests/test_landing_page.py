"""Keep the GitHub Pages landing page's skill and command index true.

`docs/index.html` lists every user-invocable skill and command by hand. Without
this check, a new skill or command ships while the public page goes on
describing the old set, the lying-map failure `/assess` flags in other repos.
Names are read from disk, so a skill or command moving between `skills/` and
`commands/` under the same name still passes.
"""
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
PAGE = REPO / "docs" / "index.html"

# Composed by other skills or commands, never invoked by a user, so the page
# leaves them out. A new entry here needs the same justification.
NOT_USER_INVOCABLE = {
    "ab-equivalence",
    "assess-findings",
    "assess-pr",
    "marathon",
    "pr-review-merge",
    "tm-marathon-config-example",
}

DT_RE = re.compile(r"<dt>/([a-z0-9][a-z0-9-]*)</dt>")


def _invocable_names() -> list[str]:
    skills = {p.parent.name for p in (REPO / "skills").glob("*/SKILL.md")}
    commands = {p.stem for p in (REPO / "commands").glob("*.md") if p.name != "README.md"}
    return sorted((skills | commands) - NOT_USER_INVOCABLE)


@pytest.mark.parametrize("name", _invocable_names())
def test_landing_page_lists_every_invocable_name(name: str) -> None:
    listed = set(DT_RE.findall(PAGE.read_text(encoding="utf-8")))
    assert name in listed, (
        f"/{name} exists on disk but docs/index.html has no <dt>/{name}</dt>; "
        "add it to the skills or commands index on the landing page"
    )


def test_landing_page_lists_nothing_that_does_not_exist() -> None:
    listed = set(DT_RE.findall(PAGE.read_text(encoding="utf-8")))
    stale = sorted(listed - set(_invocable_names()))
    assert not stale, f"docs/index.html lists names with no skill or command on disk: {stale}"
