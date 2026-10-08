"""Keep the GitHub Pages landing page's skill and command index true.

`docs/index.html` lists every user-invocable skill and command by hand. Without
this check, a new skill or command ships while the public page goes on
describing the old set, the lying-map failure `/assess` flags in other repos.

Entries are classified by frontmatter, not by directory: a file with
`disable-model-invocation: true` is a manual-only workflow and belongs in a
`data-kind="manual"` list on the page; anything else is a skill and belongs in
a `data-kind="skill"` list. `commands/` is read when present, so the check
holds before and after commands move to `skills/<name>/SKILL.md`.
"""

import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PAGE = REPO / "docs" / "index.html"

# Composed by other skills or commands, or reference material, never invoked
# by a user, so the page leaves them out. A new entry needs the same reason.
NOT_USER_INVOCABLE = {
    "ab-equivalence",
    "assess-findings",
    "assess-pr",
    "marathon",
    "pr-review-merge",
    "tm-marathon-config-example",
}

FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)
MANUAL_RE = re.compile(r"^disable-model-invocation:\s*true\s*$", re.MULTILINE)
LIST_RE = re.compile(
    r'<dl class="legend" data-kind="(skill|manual)">(.*?)</dl>', re.DOTALL
)
DT_RE = re.compile(r"<dt>/([a-z0-9][a-z0-9-]*)</dt>")


def _is_manual(path: Path) -> bool:
    match = FRONTMATTER_RE.match(path.read_text(encoding="utf-8"))
    return bool(match and MANUAL_RE.search(match.group(1)))


def _on_disk() -> dict[str, set[str]]:
    files = {p.parent.name: p for p in (REPO / "skills").glob("*/SKILL.md")}
    commands = REPO / "commands"
    if commands.is_dir():
        files.update(
            {p.stem: p for p in commands.glob("*.md") if p.name != "README.md"}
        )
    kinds: dict[str, set[str]] = {"skill": set(), "manual": set()}
    for name, path in files.items():
        if name not in NOT_USER_INVOCABLE:
            kinds["manual" if _is_manual(path) else "skill"].add(name)
    return kinds


def _on_page() -> dict[str, set[str]]:
    kinds: dict[str, set[str]] = {"skill": set(), "manual": set()}
    for kind, body in LIST_RE.findall(PAGE.read_text(encoding="utf-8")):
        kinds[kind].update(DT_RE.findall(body))
    return kinds


def test_page_has_both_lists() -> None:
    page = _on_page()
    assert page["skill"] and page["manual"], (
        'docs/index.html needs <dl class="legend" data-kind="skill"> and data-kind="manual" lists'
    )


def test_page_skill_lists_match_disk() -> None:
    disk, page = _on_disk()["skill"], _on_page()["skill"]
    assert page == disk, (
        f"docs/index.html skill lists are missing {sorted(disk - page)} "
        f"and list {sorted(page - disk)}, which are not model-invocable skills on disk"
    )


def test_page_manual_lists_match_disk() -> None:
    disk, page = _on_disk()["manual"], _on_page()["manual"]
    assert page == disk, (
        f"docs/index.html command lists are missing {sorted(disk - page)} "
        f"and list {sorted(page - disk)}, which have no disable-model-invocation: true file on disk"
    )
