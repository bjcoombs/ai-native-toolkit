"""Tests for fold_changes.py: the per-PR version notes and the release fold (issues #1011, #1262)."""
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
    comment = re.compile(r"^\s*#.*\bUnreleased\b")
    for name, counter in fc.COUNTERS.items():
        text = (fc.REPO_ROOT / counter.path).read_text(encoding="utf-8")
        hits = [line for line in text.splitlines() if comment.match(line)]
        assert not hits, f"{counter.path} ({name}): move the note to {fc.CHANGES_DIR}/<issue>.{name}.md: {hits}"
        history = (fc.REPO_ROOT / fc.history_path(name)).read_text(encoding="utf-8")
        hits = [line for line in history.splitlines() if re.search(r"\bUnreleased\b", line)]
        assert not hits, f"{fc.history_path(name)}: move the note to {fc.CHANGES_DIR}/<issue>.{name}.md: {hits}"


def test_repo_history_layout_is_valid() -> None:
    # Every counter: the pointer line directly above it, no history comment left
    # above it, and a history file holding one marker line.
    assert [e for name in fc.COUNTERS for e in fc.layout_errors(fc.REPO_ROOT, name)] == []


def test_repo_history_files_are_the_only_files_in_the_history_directory() -> None:
    directory = fc.REPO_ROOT / fc.HISTORY_DIR
    assert sorted(p.name for p in directory.iterdir()) == sorted(f"{name}.md" for name in fc.COUNTERS)


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
    for name, counter in fc.COUNTERS.items():
        for path in (counter.path, fc.history_path(name)):
            if (before / path).exists():
                assert b"\r\n" not in (before / path).read_bytes(), path


def test_fold_changes_only_the_value_line_of_a_source_file(before: Path) -> None:
    sources = {c.path: (before / c.path).read_text(encoding="utf-8").split("\n")
               for c in fc.COUNTERS.values() if (before / c.path).exists()}
    fc.fold(before, "3.0.0", RELEASE_DATE, {})
    for path, old in sources.items():
        new = (before / path).read_text(encoding="utf-8").split("\n")
        assert len(new) == len(old), path
        assert sum(1 for a, b in zip(old, new) if a != b) == 1, path


def test_fold_puts_the_new_entry_above_the_older_ones(before: Path) -> None:
    fc.fold(before, "3.0.0", RELEASE_DATE, {})
    text = (before / fc.history_path("assess_method")).read_text(encoding="utf-8")
    newest = text.index("38 (3.0.0 release): two rule changes since 37.")
    assert text.index(fc.MARKER) < newest < text.index("- ci_checks recognises common test runners")


def test_fold_deletes_the_notes_and_keeps_the_readme_and_histories(before: Path) -> None:
    _write(before, "README.md", "not a note\n")
    fc.fold(before, "3.0.0", RELEASE_DATE, {})
    assert sorted(p.name for p in (before / fc.CHANGES_DIR).iterdir()) == ["README.md", "history"]


def test_fold_with_no_notes_changes_nothing(tmp_path: Path) -> None:
    (tmp_path / fc.CHANGES_DIR).mkdir(parents=True)
    assert fc.fold(tmp_path, "3.0.0", RELEASE_DATE, {}) == []


def test_set_overrides_the_next_value(before: Path) -> None:
    fc.fold(before, "3.0.0", RELEASE_DATE, {"artifact_schema": "2.0.0"})
    history = (before / fc.history_path("artifact_schema")).read_text(encoding="utf-8")
    assert "\n2.0.0 (3.0.0 release): three changes since 1.42.0.\n" in history
    text = (before / fc.COUNTERS["artifact_schema"].path).read_text(encoding="utf-8")
    assert 'ARTIFACT_SCHEMA_VERSION = "2.0.0"' in text


def test_set_rejects_an_unknown_counter(before: Path) -> None:
    with pytest.raises(fc.FoldError, match="unknown counters: nope"):
        fc.fold(before, "3.0.0", RELEASE_DATE, {"nope": "1"})


def test_set_rejects_a_counter_with_no_notes(before: Path) -> None:
    snapshot = _tree(before)
    with pytest.raises(fc.FoldError, match="no notes to fold: fleet_row"):
        fc.fold(before, "3.0.0", RELEASE_DATE, {"fleet_row": "3"})
    assert _tree(before) == snapshot


@pytest.mark.parametrize(("name", "value"), [
    ("assess_method", "abc"),
    ("artifact_schema", "2.0"),
    ("levels_default", "2026.12.01"),
])
def test_set_rejects_a_value_the_counter_cannot_take(before: Path, name: str, value: str) -> None:
    snapshot = _tree(before)
    with pytest.raises(fc.FoldError, match=re.escape(f"--set {name}={value} is not a value {name} takes")):
        fc.fold(before, "3.0.0", RELEASE_DATE, {name: value})
    assert _tree(before) == snapshot


def test_fold_refuses_a_file_without_the_assignment_line(before: Path) -> None:
    (before / fc.COUNTERS["level_result"].path).write_text("# no counter here\n", encoding="utf-8")
    with pytest.raises(fc.FoldError, match="expected one level_result assignment line, found 0"):
        fc.fold(before, "3.0.0", RELEASE_DATE, {})


def _counter(root: Path, name: str, source: str, history: str | None = None) -> tuple[Path, Path]:
    """Write counter ``name``'s source and history file under ``root``."""
    path = root / fc.COUNTERS[name].path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")
    hist = root / fc.history_path(name)
    hist.parent.mkdir(parents=True, exist_ok=True)
    hist.write_text(f"# history\n\n{fc.MARKER}\n" if history is None else history, encoding="utf-8")
    return path, hist


def test_a_history_comment_left_in_a_source_file_stops_the_fold(tmp_path: Path) -> None:
    source = f"# 1 (#703): first version.\n# Bump on any change.\n{fc.pointer('fleet_data')}\nFLEET_DATA_VERSION = 3\n"
    path, _ = _counter(tmp_path, "fleet_data", source)
    _write(tmp_path, "1001.fleet_data.md", "- ``units``.\n")
    with pytest.raises(fc.FoldError, match=r"history comment above fleet_data; move it to .*fleet_data\.md"):
        fc.fold(tmp_path, "3.0.0", RELEASE_DATE, {})
    assert path.read_text(encoding="utf-8") == source


@pytest.mark.parametrize("line", [
    "# 1.4.0 (also): run-context.json gains x.",
    "# 39 (4.0.0 release): two rule changes, without a bump.",
    "# default-2026.11.24 (4.0.0 release): ten changes since default-2026.11.23.",
    "# 2: churn counts follow renames.",
])
def test_each_history_comment_shape_is_caught(tmp_path: Path, line: str) -> None:
    _counter(tmp_path, "assess_method", f"{line}\n#\n{fc.pointer('assess_method')}\nASSESS_METHOD_VERSION = 3\n")
    assert any("history comment above assess_method" in e for e in fc.layout_errors(tmp_path, "assess_method"))


def test_a_missing_pointer_line_stops_the_fold(tmp_path: Path) -> None:
    _counter(tmp_path, "achievements", "# Bump on any change.\nACHIEVEMENTS_VERSION = 4\n")
    _write(tmp_path, "1001.achievements.md", "- x\n")
    with pytest.raises(fc.FoldError, match=re.escape(f"must read {fc.pointer('achievements')!r}")):
        fc.fold(tmp_path, "3.0.0", RELEASE_DATE, {})


@pytest.mark.parametrize(("history", "message"), [
    (None, "achievements.md: missing"),
    ("# history\n", "expected one marker line"),
    (f"{fc.MARKER}\n{fc.MARKER}\n", "found 2"),
])
def test_a_bad_history_file_stops_the_fold(tmp_path: Path, history: str | None, message: str) -> None:
    path, hist = _counter(tmp_path, "achievements", f"{fc.pointer('achievements')}\nACHIEVEMENTS_VERSION = 4\n",
                          history or "")
    if history is None:
        hist.unlink()
    _write(tmp_path, "1001.achievements.md", "- x\n")
    with pytest.raises(fc.FoldError, match=re.escape(message)):
        fc.fold(tmp_path, "3.0.0", RELEASE_DATE, {})
    assert path.read_text(encoding="utf-8").endswith("ACHIEVEMENTS_VERSION = 4\n")


def test_an_entry_goes_on_top_of_an_empty_history(tmp_path: Path) -> None:
    path, hist = _counter(tmp_path, "fleet_data", f"# Bump on any change.\n{fc.pointer('fleet_data')}\nFLEET_DATA_VERSION = 3\n")
    _write(tmp_path, "1001.fleet_data.md", "- ``units``.\n")
    fc.fold(tmp_path, "3.0.0", RELEASE_DATE, {})
    assert path.read_text(encoding="utf-8").endswith(f"{fc.pointer('fleet_data')}\nFLEET_DATA_VERSION = 4\n")
    assert hist.read_text(encoding="utf-8") == (
        f"# history\n\n{fc.MARKER}\n\n4 (3.0.0 release): one change since 3.\n- ``units``.\n"
    )


FLEET_ROW_HISTORY = f"# history\n\n{fc.MARKER}\n\n2 (#569): ``active``.\n"


def _fleet_row(tmp_path: Path) -> tuple[Path, Path]:
    paths = _counter(tmp_path, "fleet_row", f"{fc.pointer('fleet_row')}\nFLEET_ROW_VERSION = 2\n", FLEET_ROW_HISTORY)
    _write(tmp_path, "932.fleet_row.md", "- ``dirty_reason`` (#932).\n")
    return paths


def test_an_explicit_counter_refuses_to_pick_its_own_value(tmp_path: Path) -> None:
    path, hist = _fleet_row(tmp_path)
    with pytest.raises(fc.FoldError, match=re.escape("fleet_row needs --set fleet_row=<value>")):
        fc.fold(tmp_path, "3.0.0", RELEASE_DATE, {})
    assert path.read_text(encoding="utf-8").endswith("FLEET_ROW_VERSION = 2\n")
    assert hist.read_text(encoding="utf-8") == FLEET_ROW_HISTORY


def test_setting_the_current_value_records_the_notes_without_a_bump(tmp_path: Path) -> None:
    path, hist = _fleet_row(tmp_path)
    fc.fold(tmp_path, "3.0.0", RELEASE_DATE, {"fleet_row": "2"})
    assert path.read_text(encoding="utf-8") == f"{fc.pointer('fleet_row')}\nFLEET_ROW_VERSION = 2\n"
    assert hist.read_text(encoding="utf-8") == FLEET_ROW_HISTORY.replace(
        f"{fc.MARKER}\n", f"{fc.MARKER}\n\n2 (3.0.0 release): one change, without a bump.\n- ``dirty_reason`` (#932).\n")


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
    # The fixture holds five of the eight counters; give the other three a valid layout.
    for name in ("fleet_data", "achievements", "fleet_row"):
        constant = fc.COUNTERS[name].assignment.pattern.lstrip("^").split(" ")[0]
        _counter(before, name, f"{fc.pointer(name)}\n{constant} = 1\n")
    snapshot = _tree(before)
    assert fc.main(["--check", "--root", str(before)]) == 0
    assert "10 notes valid; history layout valid" in capsys.readouterr().out
    assert _tree(before) == snapshot


def test_main_check_reports_a_bad_layout(before: Path, capsys: pytest.CaptureFixture[str]) -> None:
    # fleet_data, achievements and fleet_row have no files in the fixture.
    assert fc.main(["--check", "--root", str(before)]) == 1
    err = capsys.readouterr().err
    assert "skills/assess/scripts/lib/fleet/fleet_data.py: missing" in err
    assert "skills/assess/changes/history/fleet_row.md: missing" in err


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
