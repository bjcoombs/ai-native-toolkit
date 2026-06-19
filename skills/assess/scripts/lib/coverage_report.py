"""Parse an *existing* coverage report into the shape ``scan_test_pressure``
already consumes - no coverage run, no third-party library, read-only.

`/assess` never runs the test suite (it stays read-only and fast), so the only
honest source of line-coverage truth is a report the project already generated
in CI or locally. This module reads two ubiquitous formats - Cobertura
``coverage.xml`` and ``lcov.info`` - and reduces each to the documented shape the
test-pressure scan's ``coverage_data=`` param expects:

    {"_overall": float, "per_file": {relpath: line_rate}}

``_overall`` feeds the Layer 1 coverage-vs-mutation gap signal; ``per_file`` is
the per-file line ratio. Both are *informational* truth pulled from the
project's own tooling, never a gate.

Honest degradation is the hard contract here: a missing report, a malformed one,
or an unreadable file degrades to ``None`` - never an exception, never a block to
the assessment. The provenance distinction (a real read vs. "none found") is
recorded by the orchestrator from ``detect_coverage_report`` so the report can
state which it was.

A bare ``.coverage`` SQLite *file* is deliberately out of scope: reading it needs
the ``coverage.py`` library (a runtime dependency the deterministic core does not
take), so it degrades as if absent.

Inward-only imports: this module imports stdlib only and is imported by the
orchestrator (`assess_core.py`); it never imports an orchestrator itself.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

# Cobertura first, then lcov; repo root, then the common report sub-directories.
# A ``.coverage`` directory is searched for nested reports; a ``.coverage`` SQLite
# *file* never matches (``is_file`` on a path inside it fails) - out of scope by
# construction, no special-case needed.
_CANDIDATES: tuple[tuple[str, str], ...] = (
    ("coverage.xml", "cobertura"),
    ("coverage/coverage.xml", "cobertura"),
    (".coverage/coverage.xml", "cobertura"),
    ("lcov.info", "lcov"),
    ("coverage/lcov.info", "lcov"),
    (".coverage/lcov.info", "lcov"),
)


def _parse_cobertura(path: Path) -> dict[str, Any] | None:
    """Parse a Cobertura ``coverage.xml`` into ``{_overall, per_file}``.

    ``_overall`` is the root ``line-rate``; ``per_file`` maps each ``<class>``
    element's ``filename`` to its ``line-rate``. ``root.iter("class")`` walks the
    whole tree, so both the flat (``<coverage><classes><class>``) and nested
    (``<coverage><packages><package><classes><class>``) schemas are handled by
    the same pass. Returns ``None`` on any parse/read error or empty report.
    """
    try:
        root = ET.parse(str(path)).getroot()
    except (ET.ParseError, OSError, ValueError):
        return None
    try:
        rate = root.get("line-rate")
        overall: float | None = float(rate) if rate is not None else None

        per_file: dict[str, float] = {}
        for cls in root.iter("class"):
            filename = cls.get("filename")
            cls_rate = cls.get("line-rate")
            if filename is None or cls_rate is None:
                continue
            try:
                per_file[filename] = float(cls_rate)
            except (TypeError, ValueError):
                continue

        if overall is None and not per_file:
            return None
        return {"_overall": overall, "per_file": per_file}
    except (TypeError, ValueError):  # pragma: no cover - defensive
        return None


def _parse_lcov(path: Path) -> dict[str, Any] | None:
    """Parse an ``lcov.info`` tracefile into ``{_overall, per_file}``.

    Line-by-line: ``SF:`` opens a record (the source file), ``LF:`` is lines
    found, ``LH:`` is lines hit. Per-file ``line_rate = LH / LF``; overall is
    ``sum(LH) / sum(LF)`` across all records. A record is flushed on
    ``end_of_record``, on the next ``SF:``, or at end-of-file, so a tracefile
    that omits the terminator still parses. Returns ``None`` on read error or an
    empty / zero-line report.
    """
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except (OSError, ValueError):
        return None

    per_file: dict[str, float] = {}
    total_lf = 0
    total_lh = 0
    current: str | None = None
    lf = 0
    lh = 0

    def flush() -> None:
        nonlocal total_lf, total_lh
        if current is not None and lf > 0:
            per_file[current] = lh / lf
            total_lf += lf
            total_lh += lh

    try:
        for raw in text.splitlines():
            line = raw.strip()
            if line.startswith("SF:"):
                flush()
                current = line[3:].strip()
                lf = lh = 0
            elif line.startswith("LF:"):
                lf = int(line[3:].strip())
            elif line.startswith("LH:"):
                lh = int(line[3:].strip())
            elif line == "end_of_record":
                flush()
                current = None
                lf = lh = 0
        flush()
    except (TypeError, ValueError):
        return None

    if not per_file or total_lf == 0:
        return None
    return {"_overall": total_lh / total_lf, "per_file": per_file}


def detect_coverage_report(repo_root: Path | str) -> dict[str, str] | None:
    """Locate a coverage report at the repo root or a common sub-directory.

    Searches ``coverage.xml`` (Cobertura) and ``lcov.info`` at the repo root,
    ``./coverage/``, and ``./.coverage/`` (as a directory). Returns
    ``{"source": <relpath>, "format": "cobertura"|"lcov"}`` for the first match,
    or ``None`` if none is found. A ``.coverage`` SQLite file is never matched -
    it is out of scope (see module docstring).
    """
    root = Path(repo_root)
    for rel, fmt in _CANDIDATES:
        if (root / rel).is_file():
            return {"source": rel, "format": fmt}
    return None


def load_coverage_data(repo_root: Path | str) -> dict[str, Any] | None:
    """Detect, then parse, a coverage report under ``repo_root``.

    Returns the documented ``{_overall, per_file}`` shape, or ``None`` when no
    report is found or the report cannot be parsed. Never raises - this is the
    read-only entry point the orchestrator wires before ``scan_test_pressure``.
    """
    try:
        detected = detect_coverage_report(repo_root)
        if detected is None:
            return None
        path = Path(repo_root) / detected["source"]
        if detected["format"] == "cobertura":
            return _parse_cobertura(path)
        if detected["format"] == "lcov":
            return _parse_lcov(path)
        return None
    except Exception:  # noqa: BLE001 - never raise into the assessment
        return None
