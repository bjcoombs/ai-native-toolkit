"""Contract suite for the coverage-report parser.

The parser reads an *existing* coverage report (Cobertura ``coverage.xml`` or
``lcov.info``) into the shape ``scan_test_pressure`` consumes:
``{"_overall": float, "per_file": {relpath: line_rate}}``. These tests pin the
two parse formats, the detection search order, and the hard contract that any
absent or malformed input degrades to ``None`` rather than raising.

Expected ratios are hand-computed in the fixtures so the contract is auditable:
- ``fixtures/coverage.xml`` - root line-rate 0.75; src/a.py 0.8, src/b.py 0.5.
- ``fixtures/lcov.info`` - a: LH 8/LF 10 = 0.8; b: 2/10 = 0.2;
  overall (8+2)/(10+10) = 0.5.
"""
from __future__ import annotations

from pathlib import Path

from lib.coverage_report import (
    _parse_cobertura,
    _parse_lcov,
    detect_coverage_report,
    load_coverage_data,
)


# --- Cobertura ------------------------------------------------------------

def test_parse_cobertura_nested_schema(fixtures_dir: Path) -> None:
    result = _parse_cobertura(fixtures_dir / "coverage.xml")
    assert result is not None
    assert result["_overall"] == 0.75
    assert result["per_file"] == {"src/a.py": 0.8, "src/b.py": 0.5}


def test_parse_cobertura_flat_schema(tmp_path: Path) -> None:
    """A flat ``<coverage><classes><class>`` report (no ``<packages>``) parses
    via the same tree walk."""
    xml = (
        '<?xml version="1.0" ?>\n'
        '<coverage line-rate="0.6">\n'
        '  <classes>\n'
        '    <class filename="x.py" line-rate="0.6"/>\n'
        '  </classes>\n'
        '</coverage>\n'
    )
    path = tmp_path / "coverage.xml"
    path.write_text(xml)
    result = _parse_cobertura(path)
    assert result == {"_overall": 0.6, "per_file": {"x.py": 0.6}}


def test_parse_cobertura_malformed_returns_none(tmp_path: Path) -> None:
    path = tmp_path / "coverage.xml"
    path.write_text("<coverage line-rate=\"0.5\"><classes><not-closed")
    assert _parse_cobertura(path) is None


def test_parse_cobertura_absent_returns_none(tmp_path: Path) -> None:
    assert _parse_cobertura(tmp_path / "nope.xml") is None


# --- lcov -----------------------------------------------------------------

def test_parse_lcov(fixtures_dir: Path) -> None:
    result = _parse_lcov(fixtures_dir / "lcov.info")
    assert result is not None
    assert result["per_file"] == {"src/a.py": 0.8, "src/b.py": 0.2}
    assert result["_overall"] == 0.5


def test_parse_lcov_no_terminator_still_flushes(tmp_path: Path) -> None:
    """A record without ``end_of_record`` is flushed at EOF."""
    path = tmp_path / "lcov.info"
    path.write_text("SF:a.py\nLF:4\nLH:2\n")
    result = _parse_lcov(path)
    assert result == {"_overall": 0.5, "per_file": {"a.py": 0.5}}


def test_parse_lcov_zero_lines_returns_none(tmp_path: Path) -> None:
    """A record with LF:0 contributes nothing; an all-zero report degrades."""
    path = tmp_path / "lcov.info"
    path.write_text("SF:a.py\nLF:0\nLH:0\nend_of_record\n")
    assert _parse_lcov(path) is None


def test_parse_lcov_absent_returns_none(tmp_path: Path) -> None:
    assert _parse_lcov(tmp_path / "nope.info") is None


# --- detection ------------------------------------------------------------

def test_detect_prefers_cobertura_at_root(tmp_path: Path) -> None:
    (tmp_path / "coverage.xml").write_text("<coverage line-rate=\"0.5\"/>")
    (tmp_path / "lcov.info").write_text("SF:a\nLF:1\nLH:1\nend_of_record\n")
    assert detect_coverage_report(tmp_path) == {
        "source": "coverage.xml", "format": "cobertura"}


def test_detect_lcov_in_coverage_subdir(tmp_path: Path) -> None:
    (tmp_path / "coverage").mkdir()
    (tmp_path / "coverage" / "lcov.info").write_text(
        "SF:a\nLF:1\nLH:1\nend_of_record\n")
    assert detect_coverage_report(tmp_path) == {
        "source": "coverage/lcov.info", "format": "lcov"}


def test_detect_dot_coverage_directory(tmp_path: Path) -> None:
    (tmp_path / ".coverage").mkdir()
    (tmp_path / ".coverage" / "coverage.xml").write_text(
        "<coverage line-rate=\"0.5\"/>")
    assert detect_coverage_report(tmp_path) == {
        "source": ".coverage/coverage.xml", "format": "cobertura"}


def test_detect_dot_coverage_sqlite_file_out_of_scope(tmp_path: Path) -> None:
    """A bare ``.coverage`` SQLite *file* (not a directory) is never matched -
    reading it needs the coverage.py library, which is out of scope."""
    (tmp_path / ".coverage").write_text("SQLite format 3\x00binary junk")
    assert detect_coverage_report(tmp_path) is None


def test_detect_none_when_absent(tmp_path: Path) -> None:
    assert detect_coverage_report(tmp_path) is None


# --- load (end to end) ----------------------------------------------------

def test_load_cobertura_end_to_end(tmp_path: Path) -> None:
    (tmp_path / "coverage.xml").write_text(
        '<coverage line-rate="0.9"><classes>'
        '<class filename="m.py" line-rate="0.9"/></classes></coverage>')
    assert load_coverage_data(tmp_path) == {
        "_overall": 0.9, "per_file": {"m.py": 0.9}}


def test_load_returns_none_when_no_report(tmp_path: Path) -> None:
    assert load_coverage_data(tmp_path) is None


def test_load_malformed_report_degrades_to_none(tmp_path: Path) -> None:
    """Detected but unparseable -> None, never an exception."""
    (tmp_path / "coverage.xml").write_text("not xml at all <<<")
    assert load_coverage_data(tmp_path) is None
