"""Tests for lib.complexity.scc_coverage: the implausibly-low scc complexity check (#590).

The calibration rows are real scc 3.5.0 output for the shell scripts of the
repository the issue came from: ``scripts/doctor.sh`` scored ccn 5 across 2,150
NLOC, because an escaped quote inside ``"${id//\\"/}"`` left scc's keyword
counter inside a string for the rest of the file.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from test_complexity_treemap import stubbed_treemap

from assess_report import render_hotspots_table
from lib.anomaly_detector import detect_anomalies
from lib.complexity.scc_coverage import (
    MIN_UNRELIABLE_NLOC,
    backend_by_language,
    complexity_unreliable,
)

ROOT = Path("/repo")

# (relative path, NLOC, scc ccn), all scc language "Shell".
CALIBRATION = [
    ("build.sh", 239, 52), ("tools/chromium-switch-smoke.sh", 77, 11),
    ("tools/patch-necessity-audit.sh", 368, 29), ("scripts/doctor.sh", 2150, 5),
    ("scripts/_common.sh", 112, 22), ("scripts/packaging/rpm.sh", 253, 49),
    ("scripts/packaging/deb.sh", 291, 77), ("scripts/launcher-common.sh", 1229, 208),
    ("scripts/patches/tray-icon-selection.sh", 163, 5),
    ("scripts/patches/virtiofsd-probe.sh", 81, 2),
    ("scripts/patches/cowork-bwrap.sh", 240, 11),
    ("scripts/patches/config.sh", 112, 7),
    ("scripts/cowork-fallback/cowork.sh", 769, 70),
    ("tests/test-artifact-common.sh", 581, 103),
]


@pytest.fixture(scope="module")
def treemap():
    with stubbed_treemap() as mod:
        yield mod


def _files(rows, lang="Shell", src="scc"):
    files = [(ROOT / p, loc, float(ccn), src) for p, loc, ccn in rows]
    return files, {f[0]: lang for f in files}


def test_calibration_flags_only_the_undercounted_script():
    files, langs = _files(CALIBRATION)
    rows = complexity_unreliable(files, langs, {}, ROOT)
    assert [r["path"] for r in rows] == ["scripts/doctor.sh"]
    row = rows[0]
    assert row["language"] == "Shell"
    assert row["loc"] == 2150 and row["ccn"] == 5.0
    assert row["density"] == round(5 / 2150, 4)
    assert row["language_median_density"] > 10 * row["density"]


def test_flat_but_small_script_is_not_flagged():
    """A short low-density script can be genuinely flat (a list of commands)."""
    files, langs = _files([("a.sh", 300, 60), ("b.sh", 300, 60),
                           ("flat.sh", MIN_UNRELIABLE_NLOC - 1, 0)])
    assert complexity_unreliable(files, langs, {}, ROOT) == []
    files, langs = _files([("a.sh", 300, 60), ("b.sh", 300, 60),
                           ("flat.sh", MIN_UNRELIABLE_NLOC, 0)])
    assert [r["path"] for r in complexity_unreliable(files, langs, {}, ROOT)] == ["flat.sh"]


def test_two_files_are_enough_peers():
    """The issue's own pair: 2,150 NLOC at ccn 5 beside 1,229 NLOC at ccn 208."""
    files, langs = _files([("doctor.sh", 2150, 5), ("launcher-common.sh", 1229, 208)])
    assert [r["path"] for r in complexity_unreliable(files, langs, {}, ROOT)] == ["doctor.sh"]


def test_single_file_language_and_zero_median_are_never_flagged():
    files, langs = _files([("doctor.sh", 2150, 5)])
    assert complexity_unreliable(files, langs, {}, ROOT) == []
    files, langs = _files([("a.sh", 2150, 0), ("b.sh", 400, 0)])
    assert complexity_unreliable(files, langs, {}, ROOT) == []


def test_only_scc_only_code_files_are_checked():
    """lizard rows, files with a per-function breakdown and data are out of scope."""
    rows = [("a.x", 300, 60), ("b.x", 300, 60), ("low.x", 2000, 1)]
    files, langs = _files(rows, src="lizard")
    assert complexity_unreliable(files, langs, {}, ROOT) == []
    files, langs = _files(rows)
    breakdown = {ROOT / "low.x": [1.0]}
    assert complexity_unreliable(files, langs, breakdown, ROOT) == []
    files, langs = _files(rows, lang="YAML")
    assert complexity_unreliable(files, langs, {}, ROOT) == []


def test_languages_are_judged_against_their_own_median():
    shell, shell_langs = _files([("a.sh", 300, 60), ("b.sh", 300, 60)])
    make, make_langs = _files([("Makefile", 300, 3), ("b.mk", 300, 3)], lang="Makefile")
    rows = complexity_unreliable(shell + make, {**shell_langs, **make_langs}, {}, ROOT)
    assert rows == []


def test_backend_by_language_null_for_partial_coverage():
    a, b = ROOT / "a.py", ROOT / "b.sh"
    files = [(a, 10, 2.0, "lizard"), (b, 10, 2.0, "scc")]
    assert backend_by_language(files, {a: "Python", b: "Shell"}, {a: "lizard"}) == {
        "Python": "lizard", "Shell": None,
    }


# --- end to end: sidecar -> run-context anomaly -> report --------------------

def test_write_stats_carries_complexity_unreliable(treemap, tmp_path):
    files = [(tmp_path / p, loc, float(ccn), "scc") for p, loc, ccn in CALIBRATION]
    langs = {f[0]: "Shell" for f in files}
    out = tmp_path / "stats.json"
    treemap.write_stats(files, None, None, tmp_path, out, fn_ccn_by_path={},
                        languages_by_path=langs)
    stats = json.loads(out.read_text(encoding="utf-8"))
    assert [r["path"] for r in stats["complexity_unreliable"]] == ["scripts/doctor.sh"]
    assert treemap.STATS_SCHEMA_VERSION >= 7


def test_lizard_only_sidecar_has_empty_list(treemap, tmp_path):
    out = tmp_path / "stats.json"
    treemap.write_stats([(tmp_path / "a.py", 100, 5.0, "lizard")], None, None,
                        tmp_path, out, fn_ccn_by_path={tmp_path / "a.py": [5.0]})
    assert json.loads(out.read_text(encoding="utf-8"))["complexity_unreliable"] == []


def _unreliable_ctx():
    files, langs = _files(CALIBRATION)
    return {"stats_summary": {
        "files_scored": len(files),
        "top_hotspots": [
            {"path": "scripts/launcher-common.sh", "loc": 1229, "ccn": 208.0,
             "max_fn_ccn": None, "commits": 9, "source": "scc"},
            {"path": "scripts/doctor.sh", "loc": 2150, "ccn": 5.0,
             "max_fn_ccn": None, "commits": 4, "source": "scc"},
            {"path": "src/app.py", "loc": 300, "ccn": 40.0,
             "max_fn_ccn": 12.0, "commits": 3, "source": "lizard"},
        ],
        "complexity_unreliable": complexity_unreliable(files, langs, {}, ROOT),
    }}


def test_anomaly_reports_counts_only():
    found = [a for a in detect_anomalies(_unreliable_ctx()) if a.code == "COMPLEXITY_UNRELIABLE"]
    assert len(found) == 1
    assert found[0].detail == "files=1, languages=1, max_loc=2150"
    assert "doctor" not in found[0].detail and "doctor" not in found[0].description


def test_no_anomaly_without_flagged_files():
    ctx = {"stats_summary": {"files_scored": 1, "complexity_unreliable": []}}
    assert all(a.code != "COMPLEXITY_UNRELIABLE" for a in detect_anomalies(ctx))
    # An older run context without the key degrades the same way.
    assert all(a.code != "COMPLEXITY_UNRELIABLE" for a in detect_anomalies({}))


def test_report_labels_scc_ccn_approximate():
    out = render_hotspots_table(_unreliable_ctx())
    assert "| `scripts/launcher-common.sh` | 1229 | ~208 (approximate) | 9 |" in out
    assert "| `scripts/doctor.sh` | 2150 | ~5 (unreliable) | 4 |" in out
    assert "| `src/app.py` | 300 | 40 | 3 |" in out
    assert "`~N (approximate)` is scc's file-level keyword count" in out


# Every place a ccn caveat is spelled out (issue #630): one spelling per case.
CCN_CAVEAT_DOCS = ["skills/assess-findings/SKILL.md", "skills/assess-pr/SKILL.md"]
REPO_ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.parametrize("doc", CCN_CAVEAT_DOCS)
def test_ccn_caveats_use_one_spelling_per_case(doc):
    text = (REPO_ROOT / doc).read_text(encoding="utf-8")
    assert "ccn ~N (approximate)" in text or "ccn ~<N> (approximate)" in text
    assert "ccn ~N (unreliable)" in text or "ccn ~<N> (unreliable)" in text
    for variant in ("file-level scc)", "aggregate ccn ~", "ccn unreliable", "scc under-count"):
        assert variant not in text, f"{doc} spells a ccn caveat as {variant!r}"


def test_report_without_scc_rows_has_no_footnote():
    ctx = {"stats_summary": {"top_hotspots": [
        {"path": "src/app.py", "loc": 300, "ccn": 40.0, "commits": 3}]}}
    assert render_hotspots_table(ctx) == (
        "| Path | LOC | CCN | Commits |\n|------|-----|-----|---------|\n"
        "| `src/app.py` | 300 | 40 | 3 |"
    )


# --- the real scc on a generated fixture --------------------------------------

def _undercounted_script(n_blocks: int) -> str:
    """A shell script whose early quoting puts scc's counter inside a string.

    Each block after the trap holds three real branches, so the script has
    hundreds of decision points that scc does not see.
    """
    lines = ["#!/usr/bin/env bash", "distro_id() {", "  local id='x'",
             '  id="${id//\\"/}"', "  printf '%s' \"$id\"", "}"]
    for i in range(n_blocks):
        lines += [f"check_{i}() {{", f'  if [[ -f /tmp/f{i} ]]; then',
                  f"    echo found {i}", "  elif [[ -d /tmp ]]; then",
                  f"    echo dir {i}", "  fi", f"  for x in a b; do echo $x {i}; done", "}"]
    return "\n".join(lines) + "\n"


def _plain_script(n_blocks: int) -> str:
    lines = ["#!/usr/bin/env bash"]
    for i in range(n_blocks):
        lines += [f"run_{i}() {{", f'  if [[ -f /tmp/g{i} ]]; then',
                  f"    echo yes {i}", "  fi", "}"]
    return "\n".join(lines) + "\n"


@pytest.mark.skipif(shutil.which("scc") is None, reason="scc not installed")
def test_real_scc_undercount_is_flagged(treemap, tmp_path):
    (tmp_path / "doctor.sh").write_text(_undercounted_script(280), encoding="utf-8")
    (tmp_path / "plain.sh").write_text(_plain_script(60), encoding="utf-8")
    (tmp_path / "other.sh").write_text(_plain_script(40), encoding="utf-8")
    langs: dict[Path, str] = {}
    scores = treemap.scc_scores(tmp_path, languages=langs)
    doctor = (tmp_path / "doctor.sh").resolve()
    assert scores[doctor][0] > 2000
    files = [(p, loc, cx, "scc") for p, (loc, cx) in scores.items()]
    rows = complexity_unreliable(files, langs, {}, tmp_path.resolve())
    assert [r["path"] for r in rows] == ["doctor.sh"]
