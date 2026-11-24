"""Doc-graph reader false positives (#1227, #1228, #1229), end to end through
``build_doc_graph`` plus the unit rules in ``lib.docs.doc_links`` and
``lib.complexity.raw_source`` that decide them.

- #1227: a towncrier-style release-note fragment tree beside its README is a
  working-notes tree, not a pile of orphans.
- #1228: a TOML array-of-tables header written in prose next to the TOML file
  it belongs to is not a broken wikilink.
- #1229: a filename several docs share (every skill's ``SKILL.md``) is no
  missing cross-reference to whichever of them the index met last.
"""
from __future__ import annotations

from pathlib import Path

from lib.assess_config import load_working_notes_config
from lib.complexity.raw_source import classify_working_notes_trees
from lib.docs.doc_graph import build_doc_graph
from lib.docs.doc_links import is_toml_table_header


def _write(root: Path, rel: str, text: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _graph(root: Path) -> dict:
    cfg = load_working_notes_config(root)
    return build_doc_graph(
        root, working_notes_dirs=cfg.dirs, working_notes_ignore=cfg.ignore,
    ).as_dict()


# --- #1227: release-note fragment trees -------------------------------------

def _fragments(root: Path, directory: str = "changes") -> None:
    _write(root, "README.md", f"# Home\n[Pending notes]({directory}/README.md)\n")
    _write(root, f"{directory}/README.md", "# Pending notes\nOne `<issue>.<kind>.md` per change.\n")
    for name in ("927.artifact_schema.md", "1254.assess_method.md",
                 "1256.feature.md", "+cleanup.misc.md", "1300.bugfix.2.md"):
        _write(root, f"{directory}/{name}", "- a note\n")


def test_fragment_tree_is_working_notes_and_leaves_the_orphan_rate(tmp_path: Path) -> None:
    _fragments(tmp_path)
    d = _graph(tmp_path)
    assert d["excluded_working_notes_trees"] == [{"path": "changes", "file_count": 5}]
    assert d["orphans"] == []
    assert d["orphan_rate"] == 0.0
    assert d["doc_count"] == 2  # README.md and changes/README.md stay counted


def test_fragment_tree_without_a_readme_stays_counted(tmp_path: Path) -> None:
    _fragments(tmp_path)
    (tmp_path / "changes" / "README.md").unlink()
    d = _graph(tmp_path)
    assert d["excluded_working_notes_trees"] == []
    assert len(d["orphans"]) == 5


def test_linked_fragment_and_other_docs_stay_counted(tmp_path: Path) -> None:
    _fragments(tmp_path)
    _write(tmp_path, "changes/README.md", "# Notes\n[the first](927.artifact_schema.md)\n")
    _write(tmp_path, "changes/guide.md", "# A curated page\n")
    d = _graph(tmp_path)
    assert d["excluded_working_notes_trees"] == [{"path": "changes", "file_count": 4}]
    assert d["orphans"] == ["changes/guide.md"]


def test_numbered_names_outside_the_fragment_shape_are_not_fragments() -> None:
    signals = {
        "book/README.md": {"in_degree": 1, "inbound_sources": ["README.md"]},
        "book/chapter-1.md": {"in_degree": 0, "inbound_sources": []},
        "book/v1.2.md": {"in_degree": 0, "inbound_sources": []},
        "book/12.md": {"in_degree": 0, "inbound_sources": []},
    }
    assert classify_working_notes_trees(signals) == []


def test_working_notes_ignore_keeps_a_fragment_tree_counted() -> None:
    signals = {
        "changes/README.md": {"in_degree": 1, "inbound_sources": ["README.md"]},
        "changes/1.feature.md": {"in_degree": 0, "inbound_sources": []},
    }
    assert classify_working_notes_trees(signals) == [
        {"path": "changes", "file_count": 1, "docs": ["changes/1.feature.md"]},
    ]
    assert classify_working_notes_trees(signals, ignore=["changes"]) == []


# --- #1228: TOML table headers in prose -------------------------------------

def test_toml_table_header_in_prose_is_not_a_broken_link(tmp_path: Path) -> None:
    _write(tmp_path, "README.md", (
        "Folders come from the [[units]] of .assess/config.toml.\n"
        "Steps read [[rows.levels]] of `levels/default.toml`.\n"
    ))
    d = _graph(tmp_path)
    assert d["broken_links"] == []
    assert d["dangling_links"] == 0


def test_unresolved_wikilink_without_a_toml_file_is_still_broken(tmp_path: Path) -> None:
    _write(tmp_path, "README.md", "See [[units]] for the list.\n")
    d = _graph(tmp_path)
    assert d["broken_links"] == [{"from": "README.md", "target": "units", "kind": "wikilink"}]


def test_resolving_wikilink_on_a_toml_line_still_links(tmp_path: Path) -> None:
    _write(tmp_path, "README.md", "See [[units]] and pyproject.toml.\n")
    _write(tmp_path, "units.md", "# Units\n")
    d = _graph(tmp_path)
    assert d["broken_links"] == []
    assert d["orphans"] == []


def test_is_toml_table_header_rules() -> None:
    assert is_toml_table_header("units", "the [[units]] of .assess/config.toml")
    assert is_toml_table_header("rows.levels", "[[rows.levels]] in `default.toml`")
    assert not is_toml_table_header("units", "the [[units]] page")
    assert not is_toml_table_header("my note", "[[my note]] beside config.toml")
    assert not is_toml_table_header("units", "a .toml file")  # no file name


# --- #1229: shared filenames in missing_xrefs -------------------------------

def test_shared_filename_is_not_a_missing_xref_to_the_last_one(tmp_path: Path) -> None:
    for skill in ("assess", "huddle", "understand"):
        _write(tmp_path, f"skills/{skill}/SKILL.md", f"# {skill}\n")
    _write(tmp_path, "CLAUDE.md", "Every skill has a SKILL.md. Use /understand first.\n")
    d = _graph(tmp_path)
    assert all(x["to"] != "skills/understand/SKILL.md" for x in d["missing_xrefs"])
    assert d["missing_xrefs"] == []


def test_shared_filename_names_the_sibling_in_its_own_directory(tmp_path: Path) -> None:
    for skill in ("assess", "understand"):
        _write(tmp_path, f"skills/{skill}/SKILL.md", f"# {skill}\n")
    _write(tmp_path, "skills/assess/notes-on-use.md", "Read SKILL.md first.\n")
    d = _graph(tmp_path)
    assert d["missing_xrefs"] == [
        {"from": "skills/assess/notes-on-use.md", "to": "skills/assess/SKILL.md"},
    ]


def test_unique_filename_is_still_a_missing_xref(tmp_path: Path) -> None:
    _write(tmp_path, "overview.md", "The payments.md flow.\n")
    _write(tmp_path, "docs/payments.md", "payments\n")
    d = _graph(tmp_path)
    assert d["missing_xrefs"] == [{"from": "overview.md", "to": "docs/payments.md"}]
