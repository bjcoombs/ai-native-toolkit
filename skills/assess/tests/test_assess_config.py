"""Tests for the working-notes keys in `.assess/config.toml` (issue #367).

`working_notes_dirs` forces a directory to be classified as a working-notes
tree whatever its size or fingerprint; `working_notes_ignore` keeps a directory
counted in the headline even when the fingerprint matches. Each test runs the
key end to end: config file -> `load_working_notes_config` -> `build_doc_graph`.
"""
from __future__ import annotations

from pathlib import Path

from lib.assess_config import load_working_notes_config
from lib.doc_graph import build_doc_graph


def _write(root: Path, rel: str, text: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _config(root: Path, body: str) -> None:
    _write(root, ".assess/config.toml", body)


def _plan_notes(root: Path, directory: str = "notes") -> None:
    """50 plan notes under one backlog index: the fingerprint matches."""
    for i in range(1, 51):
        _write(root, f"{directory}/plan_{i:02d}.md", f"# plan {i:02d}\n")
    _write(root, f"{directory}/backlog.md", "".join(
        f"- [plan {i:02d}](plan_{i:02d}.md)\n" for i in range(1, 51)
    ))


_WORDS = "harbour ledger compass anchor beacon current driftwood estuary".split()


def _journal(root: Path) -> None:
    """Eight cross-linked pages with varied names: no fingerprint matches."""
    n = len(_WORDS)
    for i, w in enumerate(_WORDS):
        links = "".join(f"- [{_WORDS[(i + k) % n]}]({_WORDS[(i + k) % n]}.md)\n" for k in (1, 3))
        _write(root, f"journal/{w}.md", f"# {w}\n\n{links}")


def _graph(root: Path) -> dict:
    return build_doc_graph(root, **load_working_notes_config(root)).as_dict()


def test_working_notes_dirs_forces_classification(tmp_path: Path) -> None:
    _journal(tmp_path)
    _write(tmp_path, "README.md", "# Home\n[harbour](journal/harbour.md)\n")
    assert _graph(tmp_path)["excluded_working_notes_trees"] == []

    _config(tmp_path, 'working_notes_dirs = ["./journal/"]\n')
    d = _graph(tmp_path)
    assert d["excluded_working_notes_trees"] == [{"path": "journal", "file_count": 8}]
    assert d["working_notes_doc_count"] == 8
    assert d["doc_count"] == 1


def test_working_notes_dirs_nested_path_and_absent_dir(tmp_path: Path) -> None:
    for i in range(3):
        _write(tmp_path, f"docs/plans/p{i}.md", f"# p{i}\n")
    _write(tmp_path, "docs/guide.md", "# Guide\n")
    _config(tmp_path, 'working_notes_dirs = ["docs/plans", "does-not-exist"]\n')
    d = _graph(tmp_path)
    assert d["excluded_working_notes_trees"] == [{"path": "docs/plans", "file_count": 3}]
    assert d["doc_count"] == 1


def test_working_notes_dirs_absorbs_a_detected_tree_below_it(tmp_path: Path) -> None:
    _plan_notes(tmp_path, "work/notes")
    _write(tmp_path, "work/summary.md", "# Summary\n")
    _config(tmp_path, 'working_notes_dirs = ["work"]\n')
    d = _graph(tmp_path)
    assert d["excluded_working_notes_trees"] == [{"path": "work", "file_count": 52}]


def test_working_notes_ignore_suppresses_classification(tmp_path: Path) -> None:
    _plan_notes(tmp_path)
    _write(tmp_path, "README.md", "# Home\n[backlog](notes/backlog.md)\n")
    assert _graph(tmp_path)["excluded_working_notes_trees"] == [
        {"path": "notes", "file_count": 51}
    ]

    _config(tmp_path, 'working_notes_ignore = ["notes"]\n')
    d = _graph(tmp_path)
    assert d["excluded_working_notes_trees"] == []
    assert d["working_notes_doc_count"] == 0
    assert d["doc_count"] == 52


def test_working_notes_ignore_covers_subdirectories_and_wins_over_dirs(tmp_path: Path) -> None:
    _plan_notes(tmp_path, "notes/2026")
    _journal(tmp_path)
    _config(tmp_path, 'working_notes_dirs = ["journal"]\nworking_notes_ignore = ["notes", "journal"]\n')
    d = _graph(tmp_path)
    assert d["excluded_working_notes_trees"] == []
    assert d["doc_count"] == 51 + len(_WORDS)


def test_working_notes_config_degrades_silently(tmp_path: Path) -> None:
    assert load_working_notes_config(tmp_path) == {
        "working_notes_dirs": [], "working_notes_ignore": [],
    }
    _config(tmp_path, 'working_notes_dirs = "journal"\nworking_notes_ignore = ["", "/", 7, "a/b/"]\n')
    assert load_working_notes_config(tmp_path) == {
        "working_notes_dirs": [], "working_notes_ignore": ["a/b"],
    }
    _config(tmp_path, "working_notes_dirs = [\n")  # malformed TOML
    assert load_working_notes_config(tmp_path) == {
        "working_notes_dirs": [], "working_notes_ignore": [],
    }
