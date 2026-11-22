"""Keep the Windows setup guide reachable and its make-free commands true (#755).

`docs/windows.md` is where a Windows user without `make` learns the setup and
the commands each Makefile target runs. It drifts in two ways: a SKILL.md with
shell commands stops pointing at it, or a Makefile recipe changes and the page
goes on showing the old command. Both are checked here.
"""
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
PAGE = REPO / "docs" / "windows.md"
COMMAND_FENCE = re.compile(r"^\s*```(?:bash|sh|shell|python)\s*$", re.MULTILINE)
# The make-free targets the page documents, and `check`'s composite parts.
DOCUMENTED_TARGETS = ("setup", "lock-check", "test-assess", "test-scripts", "test-contract", "lint")


def _links_page(path: Path) -> bool:
    """True when a relative markdown link in `path` resolves to docs/windows.md."""
    text = path.read_text(encoding="utf-8")
    return any(
        (path.parent / target.split("#", 1)[0]).resolve() == PAGE.resolve()
        for target in re.findall(r"\]\(([^)\s]+)\)", text)
        if not target.startswith(("http://", "https://", "#"))
    )


def _skills_with_commands() -> list[Path]:
    return [
        p for p in sorted((REPO / "skills").glob("*/SKILL.md"))
        if COMMAND_FENCE.search(p.read_text(encoding="utf-8"))
    ]


def test_readme_and_index_link_the_page() -> None:
    assert PAGE.exists()
    assert _links_page(REPO / "README.md"), "README.md does not link docs/windows.md"
    assert _links_page(REPO / "docs" / "index.md"), "docs/index.md does not link docs/windows.md"


def test_there_are_skills_with_commands() -> None:
    assert len(_skills_with_commands()) >= 10


@pytest.mark.parametrize("p", _skills_with_commands(), ids=lambda p: p.parent.name)
def test_skill_with_commands_links_the_page(p: Path) -> None:
    assert _links_page(p), (
        f"{p.relative_to(REPO).as_posix()} has a shell or python command block but "
        "no Windows note linking ../../docs/windows.md"
    )


def _recipe(target: str) -> list[str]:
    lines = (REPO / "Makefile").read_text(encoding="utf-8").splitlines()
    start = lines.index(next(line for line in lines if line.startswith(f"{target}:")))
    body: list[str] = []
    for line in lines[start + 1:]:
        if not line.startswith("\t"):
            break
        body.append(line.strip())
    return body


def make_free(command: str) -> str | None:
    """The page's form of one recipe line; None for a line the page words differently."""
    if command.startswith("@"):
        return None  # echo/warning lines; the page checks rg with `rg --version`
    m = re.fullmatch(r"cd (\S+) && uv (.+)", command)
    if m:
        return f"uv --directory {m.group(1)} {m.group(2)}"
    return command


def test_make_free_translation() -> None:
    assert make_free("cd scripts && uv run --locked mypy") == "uv --directory scripts run --locked mypy"
    assert make_free("uv run --with pytest pytest -v tests/") == "uv run --with pytest pytest -v tests/"
    assert make_free("@command -v rg") is None


@pytest.mark.parametrize("target", DOCUMENTED_TARGETS)
def test_page_shows_each_recipe_line(target: str) -> None:
    page = PAGE.read_text(encoding="utf-8").splitlines()
    recipe = _recipe(target)
    assert recipe, f"Makefile target {target} has no recipe"
    for command in recipe:
        expected = make_free(command)
        if expected is not None:
            assert expected in page, f"docs/windows.md has no line `{expected}` (make {target})"


def test_check_is_the_documented_targets() -> None:
    line = next(
        line for line in (REPO / "Makefile").read_text(encoding="utf-8").splitlines()
        if line.startswith("check:")
    )
    parts = set(line.split(":", 1)[1].split())
    assert parts <= set(DOCUMENTED_TARGETS), f"make check gained a target the page does not show: {parts}"


def test_page_notes_git_bash_path_conversion() -> None:
    assert "MSYS_NO_PATHCONV=1 git show" in PAGE.read_text(encoding="utf-8")


def test_fleet_missing_graph_example_has_a_builder_free_language() -> None:
    # Go gained an import-graph builder, so it is no example of a missing graph.
    assert "no_import_graph_go" not in (REPO / "skills" / "fleet" / "SKILL.md").read_text(encoding="utf-8")
