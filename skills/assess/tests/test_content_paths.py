"""``content_paths``: a pure rename is not a content change (contract I425-2)."""
from __future__ import annotations

from lib.history.content_paths import content_paths

A, B = "a" * 40, "b" * 40
Z = "0" * 40


def _line(status: str, old: str, new: str, path: str) -> str:
    modes = {"A": "000000 100644", "D": "100644 000000"}.get(status, "100644 100644")
    return f":{modes} {old} {new} {status}\t{path}"


def test_a_pure_rename_folded_by_the_map_is_dropped() -> None:
    lines = [_line("D", A, Z, "docs/old.md"), _line("A", Z, A, "docs/new.md")]
    assert content_paths(lines, {"docs/old.md": "docs/new.md"}) == set()


def test_a_rename_with_an_edit_counts_under_the_current_name() -> None:
    lines = [_line("D", A, Z, "docs/old.md"), _line("A", Z, B, "docs/new.md")]
    assert content_paths(lines, {"docs/old.md": "docs/new.md"}) == {"docs/new.md"}


def test_without_the_map_both_names_count() -> None:
    lines = [_line("D", A, Z, "docs/old.md"), _line("A", Z, A, "docs/new.md")]
    assert content_paths(lines) == {"docs/old.md", "docs/new.md"}


def test_edits_count_and_non_raw_lines_are_ignored() -> None:
    lines = ["", "commit header", _line("M", A, B, "src/x.py"), "no-tab line"]
    assert content_paths(lines) == {"src/x.py"}


def test_within_pairs_a_rename_from_outside_but_counts_only_inside() -> None:
    inside = lambda rel: rel.startswith("pkg/")  # noqa: E731
    moved = [_line("D", A, Z, "other/a.py"), _line("A", Z, A, "pkg/a.py")]
    assert content_paths(moved, {"other/a.py": "pkg/a.py"}, inside) == set()
    outside_edit = [_line("M", A, B, "other/a.py"), _line("M", A, B, "pkg/z.py")]
    assert content_paths(outside_edit, {"other/a.py": "pkg/a.py"}, inside) == {"pkg/z.py"}
