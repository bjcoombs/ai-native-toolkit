"""Toolkit-root tests skip with a reason outside the toolkit repository (#1649, part of #1487).

A test that reads the ai-native-toolkit repository itself (its workflows, its
``action.yml``, its root docs, its plugin manifest) carries the ``toolkit_repo``
marker and reaches the repository through ``assess_test_support.TOOLKIT_ROOT``.
In a vendored copy of the skill tree that root is some other repository, so the
marked tests skip with a reason. In this repository's own CI a missing root fails
the session instead, so a broken probe can never skip them silently.
"""
from __future__ import annotations

import ast
import json
import os
from pathlib import Path

import pytest
from assess_test_support import (
    TOOLKIT_REPO_MARKER,
    TOOLKIT_REPOSITORY,
    TOOLKIT_ROOT,
    TOOLKIT_ROOT_PRESENT,
    gate_toolkit_repo_tests,
    is_toolkit_root,
    toolkit_root_verdict,
)

TESTS = Path(__file__).resolve().parent
SUPPORT = TESTS / "assess_test_support.py"


def _plugin_root(tmp_path: Path, manifest: str) -> Path:
    (tmp_path / ".claude-plugin").mkdir()
    (tmp_path / ".claude-plugin" / "plugin.json").write_text(manifest, encoding="utf-8")
    return tmp_path


# --- the root probe ------------------------------------------------------------

def test_the_probe_accepts_a_manifest_naming_this_plugin(tmp_path: Path) -> None:
    root = _plugin_root(tmp_path, json.dumps({"name": "ai-native-toolkit", "version": "1.0.0"}))
    assert is_toolkit_root(root)


@pytest.mark.parametrize("manifest", [
    json.dumps({"name": "downstream-marketplace"}),
    json.dumps(["ai-native-toolkit"]),
    "{not json",
])
def test_the_probe_rejects_another_plugin_or_a_broken_manifest(tmp_path: Path, manifest: str) -> None:
    assert not is_toolkit_root(_plugin_root(tmp_path, manifest))


def test_the_probe_rejects_a_root_without_a_manifest(tmp_path: Path) -> None:
    assert not is_toolkit_root(tmp_path)


def test_the_root_is_three_levels_above_the_tests_directory() -> None:
    assert TOOLKIT_ROOT == TESTS.parents[2]
    assert TOOLKIT_ROOT_PRESENT == is_toolkit_root(TOOLKIT_ROOT)


# --- the verdict -----------------------------------------------------------------

@pytest.mark.parametrize(("present", "github_repository", "verdict"), [
    (True, None, "run"),
    (True, TOOLKIT_REPOSITORY, "run"),
    (False, None, "skip"),
    (False, "", "skip"),
    (False, "someone/downstream-marketplace", "skip"),
    (False, TOOLKIT_REPOSITORY, "fail"),
    (False, TOOLKIT_REPOSITORY.upper(), "fail"),
])
def test_the_verdict_skips_outside_the_root_and_fails_in_this_repositorys_ci(
        present: bool, github_repository: str | None, verdict: str) -> None:
    assert toolkit_root_verdict(present, github_repository) == verdict


class _Item:
    """The two ``pytest.Item`` methods the gate calls."""

    def __init__(self, marked: bool) -> None:
        self.marked = marked
        self.added: list[pytest.MarkDecorator] = []

    def get_closest_marker(self, name: str) -> object | None:
        return object() if self.marked and name == TOOLKIT_REPO_MARKER else None

    def add_marker(self, marker: pytest.MarkDecorator) -> None:
        self.added.append(marker)


def test_the_gate_skips_only_marked_tests_with_the_root_in_the_reason() -> None:
    marked, plain = _Item(True), _Item(False)
    gate_toolkit_repo_tests([marked, plain], root=Path("/elsewhere"), present=False, github_repository=None)
    assert plain.added == []
    [skip] = marked.added
    assert skip.name == "skip"
    reason = skip.kwargs["reason"]
    assert "ai-native-toolkit" in reason and Path("/elsewhere").as_posix() in reason


def test_the_gate_leaves_marked_tests_alone_at_the_root() -> None:
    marked = _Item(True)
    gate_toolkit_repo_tests([marked], root=TOOLKIT_ROOT, present=True, github_repository=TOOLKIT_REPOSITORY)
    assert marked.added == []


def test_the_gate_fails_the_session_in_this_repositorys_ci_without_the_root() -> None:
    marked = _Item(True)
    with pytest.raises(pytest.UsageError, match="ai-native-toolkit"):
        gate_toolkit_repo_tests([marked], root=Path("/elsewhere"), present=False,
                                github_repository=TOOLKIT_REPOSITORY)
    assert marked.added == []


def test_the_gate_does_not_fail_a_session_with_no_marked_test() -> None:
    """mutmut runs unmarked tests from ``skills/assess/mutants/``, where the probe misses."""
    plain = _Item(False)
    gate_toolkit_repo_tests([plain], root=Path("/elsewhere"), present=False,
                            github_repository=TOOLKIT_REPOSITORY)
    assert plain.added == []


def test_this_repositorys_ci_finds_the_root() -> None:
    """The live probe: in this repository's CI the marked tests run, never skip."""
    if os.environ.get("GITHUB_REPOSITORY", "").lower() != TOOLKIT_REPOSITORY:
        pytest.skip("not this repository's CI")
    assert TOOLKIT_ROOT_PRESENT


# --- the lint: every test reaches the root through the helper --------------------

def _parents_3_uses(tree: ast.Module) -> list[int]:
    """Lines of ``<expr>.parents[3]``: the ad-hoc root lookup the helper replaces."""
    return sorted(
        node.lineno for node in ast.walk(tree)
        if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Attribute)
        and node.value.attr == "parents" and isinstance(node.slice, ast.Constant) and node.slice.value == 3
    )


def test_the_lint_sees_a_parents_3_lookup() -> None:
    source = "from pathlib import Path\nROOT = Path(__file__).resolve().parents[3]\nX = Path('a').parents[2]\n"
    assert _parents_3_uses(ast.parse(source)) == [2]


def test_no_assess_test_finds_the_repository_by_parents_3() -> None:
    uses = [
        f"{path.relative_to(TESTS).as_posix()}:{line}"
        for path in sorted(TESTS.rglob("*.py"))
        if "fixtures" not in path.relative_to(TESTS).parts and path != SUPPORT
        for line in _parents_3_uses(ast.parse(path.read_text(encoding="utf-8")))
    ]
    assert not uses, ("use assess_test_support.TOOLKIT_ROOT and the toolkit_repo marker (#1649), "
                      "not parents[3]: " + ", ".join(uses))


# --- the lint: a test that reads outside skills/ carries the marker ---------------

def _root_aliases(tree: ast.Module) -> set[str]:
    """``TOOLKIT_ROOT`` and the module-level names bound to it (``REPO = TOOLKIT_ROOT``)."""
    aliases = {"TOOLKIT_ROOT"}
    for node in tree.body:
        if (isinstance(node, ast.Assign) and isinstance(node.value, ast.Name)
                and node.value.id in aliases):
            aliases |= {t.id for t in node.targets if isinstance(t, ast.Name)}
    return aliases


# Segments a vendored copy has too: the skill tree itself, and the checkout's own .git.
IN_ANY_CHECKOUT = {"skills", ".git"}
# A line whose read degrades by itself in a vendored copy (a missing tree globs empty)
# says so with this comment instead of skipping the whole test.
OPTIONAL_COMMENT = "# toolkit-root optional:"


def _outside_skills_uses(tree: ast.Module, source: str = "") -> list[int]:
    """Lines of ``<root> / "<segment>"`` whose first segment a vendored copy lacks."""
    aliases = _root_aliases(tree)
    text = source.splitlines()
    lines = set()
    for node in ast.walk(tree):
        if (isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div)
                and isinstance(node.left, ast.Name) and node.left.id in aliases
                and isinstance(node.right, ast.Constant) and isinstance(node.right.value, str)
                and node.right.value.strip("/").split("/", 1)[0] not in IN_ANY_CHECKOUT
                and not (node.lineno <= len(text) and OPTIONAL_COMMENT in text[node.lineno - 1])):
            lines.add(node.lineno)
    return sorted(lines)


def _has_toolkit_repo_mark(tree: ast.Module) -> bool:
    """Whether the module names ``mark.toolkit_repo``, as ``pytestmark`` or a decorator."""
    return any(
        isinstance(node, ast.Attribute) and node.attr == TOOLKIT_REPO_MARKER
        and ((isinstance(node.value, ast.Attribute) and node.value.attr == "mark")
             or (isinstance(node.value, ast.Name) and node.value.id == "mark"))
        for node in ast.walk(tree)
    )


def _unmarked_outside_skills(source: str) -> list[int]:
    tree = ast.parse(source)
    return [] if _has_toolkit_repo_mark(tree) else _outside_skills_uses(tree, source)


@pytest.mark.parametrize(("source", "lines"), [
    ('A = TOOLKIT_ROOT / "action.yml"\n', [1]),
    ('REPO = TOOLKIT_ROOT\ndef test_x():\n    (REPO / ".github" / "workflows").read_text()\n', [3]),
    ('A = TOOLKIT_ROOT / "skills" / "fleet"\nB = TOOLKIT_ROOT / "skills/assess"\n', []),
    ('import pytest\npytestmark = pytest.mark.toolkit_repo\nA = TOOLKIT_ROOT / "docs"\n', []),
    ('import pytest\n@pytest.mark.toolkit_repo\ndef test_x():\n    TOOLKIT_ROOT / "agents"\n', []),
    ('from pytest import mark\n@mark.toolkit_repo\ndef test_x():\n    TOOLKIT_ROOT / "README.md"\n', []),
    ('import pytest\n@pytest.mark.needs_symlink\ndef test_x():\n    TOOLKIT_ROOT / "templates"\n', [4]),
    ('A = TOOLKIT_ROOT / ".git"\n', []),
    ('A = (TOOLKIT_ROOT / "tests", TOOLKIT_ROOT / "scripts")  # toolkit-root optional: globs empty\n', []),
])
def test_the_marker_lint_sees_an_unmarked_read_outside_skills(source: str, lines: list[int]) -> None:
    assert _unmarked_outside_skills(source) == lines


def test_every_assess_test_that_reads_outside_skills_carries_the_marker() -> None:
    """A test that reads ``action.yml``, ``docs/``, ``.github/`` or ``agents/`` fails in a
    vendored copy unless it skips there; the marker is what makes it skip (#1649)."""
    uses = [
        f"{path.relative_to(TESTS).as_posix()}:{line}"
        for path in sorted(TESTS.rglob("*.py"))
        if "fixtures" not in path.relative_to(TESTS).parts and path != SUPPORT
        for line in _unmarked_outside_skills(path.read_text(encoding="utf-8"))
    ]
    assert not uses, ("a module that reads the toolkit root outside skills/ needs "
                      "@pytest.mark.toolkit_repo (#1649): " + ", ".join(uses))
