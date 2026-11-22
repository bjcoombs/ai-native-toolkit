"""Tests for fold_changes.py: the per-PR version notes and the release fold (issue #1011)."""
from __future__ import annotations

import datetime
import re
import shutil
from pathlib import Path

import pytest

import fold_changes as fc

FIXTURES = Path(__file__).parent / "fixtures" / "fold_changes"
RELEASE_DATE = datetime.date(2026, 12, 1)


def _tree(root: Path) -> dict[str, str]:
    return {p.relative_to(root).as_posix(): p.read_text(encoding="utf-8")
            for p in sorted(root.rglob("*")) if p.is_file()}


def _write(root: Path, name: str, text: str) -> Path:
    path = root / fc.CHANGES_DIR / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture
def before(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    shutil.copytree(FIXTURES / "before", root)
    return root


# The repository itself -----------------------------------------------------

def test_repo_notes_name_a_known_counter_and_are_bullets() -> None:
    # read_notes raises FoldError naming every bad note.
    fc.read_notes(fc.REPO_ROOT)


def test_counter_files_hold_no_unreleased_block() -> None:
    pattern = re.compile(r"^\s*#.*\bUnreleased\b")
    for name, counter in fc.COUNTERS.items():
        text = (fc.REPO_ROOT / counter.path).read_text(encoding="utf-8")
        hits = [line for line in text.splitlines() if pattern.match(line)]
        assert not hits, f"{counter.path} ({name}): move the note to {fc.CHANGES_DIR}/<issue>.{name}.md: {hits}"


def test_every_counter_has_one_assignment_line_in_the_repo() -> None:
    for name, counter in fc.COUNTERS.items():
        lines = (fc.REPO_ROOT / counter.path).read_text(encoding="utf-8").splitlines()
        assert sum(1 for line in lines if counter.assignment.match(line)) == 1, name


# The fold --------------------------------------------------------------------

def test_fold_matches_the_hand_fold_of_the_notes_moved_from_main(before: Path) -> None:
    folded = fc.fold(before, "3.0.0", RELEASE_DATE, {})
    assert folded == ["artifact_schema", "assess_method", "level_result", "levels_default", "signals_schema"]
    assert _tree(before) == _tree(FIXTURES / "after")


def test_fold_writes_lf_line_endings(before: Path) -> None:
    fc.fold(before, "3.0.0", RELEASE_DATE, {})
    for path in {c.path for c in fc.COUNTERS.values() if (before / c.path).exists()}:
        assert b"\r\n" not in (before / path).read_bytes(), path


def test_fold_deletes_the_notes_and_keeps_the_readme(before: Path) -> None:
    _write(before, "README.md", "not a note\n")
    fc.fold(before, "3.0.0", RELEASE_DATE, {})
    assert [p.name for p in (before / fc.CHANGES_DIR).iterdir()] == ["README.md"]


def test_fold_with_no_notes_changes_nothing(tmp_path: Path) -> None:
    (tmp_path / fc.CHANGES_DIR).mkdir(parents=True)
    assert fc.fold(tmp_path, "3.0.0", RELEASE_DATE, {}) == []


def test_set_overrides_the_next_value(before: Path) -> None:
    fc.fold(before, "3.0.0", RELEASE_DATE, {"artifact_schema": "2.0.0"})
    text = (before / fc.COUNTERS["artifact_schema"].path).read_text(encoding="utf-8")
    assert '# 2.0.0 (3.0.0 release): three changes since 1.42.0.' in text
    assert 'ARTIFACT_SCHEMA_VERSION = "2.0.0"' in text


def test_set_rejects_an_unknown_counter(before: Path) -> None:
    with pytest.raises(fc.FoldError, match="unknown counters: nope"):
        fc.fold(before, "3.0.0", RELEASE_DATE, {"nope": "1"})


def test_fold_refuses_a_file_without_the_assignment_line(before: Path) -> None:
    (before / fc.COUNTERS["level_result"].path).write_text("# no counter here\n", encoding="utf-8")
    with pytest.raises(fc.FoldError, match="expected one level_result assignment line, found 0"):
        fc.fold(before, "3.0.0", RELEASE_DATE, {})


FLEET_DATA_BEFORE = """\
from lib.levels import evaluate_row

# 1 (#703): first version.
# 3 (#746): ``opportunities``.
# Bump on any change to the shapes below; #704 and #705 read it.
FLEET_DATA_VERSION = 3
"""


def test_an_entry_goes_above_the_closing_bump_rule(tmp_path: Path) -> None:
    path = tmp_path / fc.COUNTERS["fleet_data"].path
    path.parent.mkdir(parents=True)
    path.write_text(FLEET_DATA_BEFORE, encoding="utf-8")
    _write(tmp_path, "1001.fleet_data.md", "- ``units``.\n")
    fc.fold(tmp_path, "3.0.0", RELEASE_DATE, {})
    assert path.read_text(encoding="utf-8") == FLEET_DATA_BEFORE.replace(
        "# Bump on any change",
        "# 4 (3.0.0 release): one change since 3.\n# - ``units``.\n# Bump on any change",
    ).replace("FLEET_DATA_VERSION = 3", "FLEET_DATA_VERSION = 4")


def test_a_missing_closing_bump_rule_is_named(tmp_path: Path) -> None:
    path = tmp_path / fc.COUNTERS["achievements"].path
    path.parent.mkdir(parents=True)
    path.write_text("# 4 (#772): retired.\nACHIEVEMENTS_VERSION = 4\n", encoding="utf-8")
    _write(tmp_path, "1001.achievements.md", "- x\n")
    with pytest.raises(fc.FoldError, match="found 0"):
        fc.fold(tmp_path, "3.0.0", RELEASE_DATE, {})


def _fleet_row(tmp_path: Path) -> Path:
    path = tmp_path / fc.COUNTERS["fleet_row"].path
    path.parent.mkdir(parents=True)
    path.write_text("# 2 (#569): ``active``.\nFLEET_ROW_VERSION = 2\n", encoding="utf-8")
    _write(tmp_path, "932.fleet_row.md", "- ``dirty_reason`` (#932).\n")
    return path


def test_an_explicit_counter_refuses_to_pick_its_own_value(tmp_path: Path) -> None:
    path = _fleet_row(tmp_path)
    with pytest.raises(fc.FoldError, match=re.escape("fleet_row needs --set fleet_row=<value>")):
        fc.fold(tmp_path, "3.0.0", RELEASE_DATE, {})
    assert path.read_text(encoding="utf-8").endswith("FLEET_ROW_VERSION = 2\n")


def test_setting_the_current_value_records_the_notes_without_a_bump(tmp_path: Path) -> None:
    path = _fleet_row(tmp_path)
    fc.fold(tmp_path, "3.0.0", RELEASE_DATE, {"fleet_row": "2"})
    assert path.read_text(encoding="utf-8") == (
        "# 2 (#569): ``active``.\n"
        "# 2 (3.0.0 release): one change, without a bump.\n"
        "# - ``dirty_reason`` (#932).\n"
        "FLEET_ROW_VERSION = 2\n"
    )


def test_next_value_per_kind() -> None:
    assert fc.next_value(fc.COUNTERS["artifact_schema"], "1.42.0", RELEASE_DATE) == "1.43.0"
    assert fc.next_value(fc.COUNTERS["assess_method"], "37", RELEASE_DATE) == "38"
    assert fc.next_value(fc.COUNTERS["levels_default"], "default-2026.11.22", RELEASE_DATE) == "default-2026.12.01"


def test_count_wording() -> None:
    assert fc._count(1, "rule changes") == "one rule change"
    assert fc._count(11, "changes") == "11 changes"


# Note validation ---------------------------------------------------------------

@pytest.mark.parametrize(("name", "text", "message"), [
    ("927.nope.md", "- a note\n", "unknown counter 'nope'"),
    ("artifact_schema.md", "- a note\n", "name is not <issue>.<counter>.md"),
    ("0927.artifact_schema.md", "- a note\n", "name is not <issue>.<counter>.md"),
    ("927.artifact_schema.txt", "- a note\n", "name is not <issue>.<counter>.md"),
    ("927.artifact_schema.md", "a note\n", "not a bullet or its continuation"),
    ("927.artifact_schema.md", "- a note\n\n- another\n", "not a bullet or its continuation"),
    ("927.artifact_schema.md", "-\n", "not a bullet or its continuation"),
    ("927.artifact_schema.md", "", "not a bullet or its continuation"),
])
def test_a_bad_note_is_named(tmp_path: Path, name: str, text: str, message: str) -> None:
    _write(tmp_path, name, text)
    with pytest.raises(fc.FoldError, match=re.escape(f"{name}: {message}")):
        fc.read_notes(tmp_path)


def test_a_note_may_hold_several_bullets(tmp_path: Path) -> None:
    _write(tmp_path, "927.artifact_schema.md", "- one\n  more\n- two\n")
    (note,) = fc.read_notes(tmp_path)
    assert note.bullets == (("- one", "  more"), ("- two",))


def test_notes_sort_by_issue_number(tmp_path: Path) -> None:
    _write(tmp_path, "1001.assess_method.md", "- later\n")
    _write(tmp_path, "999.assess_method.md", "- earlier\n")
    assert [n.issue for n in fc.read_notes(tmp_path)] == [999, 1001]


def test_no_changes_directory_reads_no_notes(tmp_path: Path) -> None:
    assert fc.read_notes(tmp_path) == []


# CLI ---------------------------------------------------------------------------

def test_main_check_validates_without_writing(before: Path, capsys: pytest.CaptureFixture[str]) -> None:
    snapshot = _tree(before)
    assert fc.main(["--check", "--root", str(before)]) == 0
    assert "10 notes valid" in capsys.readouterr().out
    assert _tree(before) == snapshot


def test_main_folds(before: Path, capsys: pytest.CaptureFixture[str]) -> None:
    argv = ["--release", "3.0.0", "--date", "2026-12-01", "--root", str(before)]
    assert fc.main(argv) == 0
    assert "folded: artifact_schema, assess_method" in capsys.readouterr().out
    assert _tree(before) == _tree(FIXTURES / "after")


def test_main_reports_a_bad_note(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _write(tmp_path, "1.nope.md", "- x\n")
    assert fc.main(["--check", "--root", str(tmp_path)]) == 1
    assert "unknown counter 'nope'" in capsys.readouterr().err


def test_main_rejects_a_malformed_set(before: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert fc.main(["--release", "3.0.0", "--set", "artifact_schema", "--root", str(before)]) == 1
    assert "--set wants counter=value" in capsys.readouterr().err


def test_main_needs_a_release_to_fold(before: Path) -> None:
    with pytest.raises(SystemExit):
        fc.main(["--root", str(before)])
