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
