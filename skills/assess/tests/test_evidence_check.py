"""Tests for lib/evidence_check.py - deterministic re-check of scorer evidence.

Each evidence entry is re-checked with exists() or a literal substring search.
A false entry lands in ``evidence_rejected`` with its kind and arguments intact
(that is what "naming the entry" means); a true one lands in ``evidence``.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from lib.evidence_check import check_evidence, is_referenced_in

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"

WORKFLOW = (
    "on: push\n"
    "jobs:\n"
    "  lint:\n"
    "    runs-on: ubuntu-latest\n"
    "    steps:\n"
    "      - run: bash scripts/check-x.sh\n"
)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    (tmp_path / "docs").mkdir()
    (tmp_path / "scripts").mkdir()
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    (tmp_path / "docs" / "guide.md").write_text("# guide\n")
    (tmp_path / "scripts" / "check-x.sh").write_text("echo ok\n")
    (tmp_path / ".github" / "workflows" / "ci.yml").write_text(WORKFLOW)
    return tmp_path


def test_path_absent_for_existing_file_is_rejected_and_named(repo: Path) -> None:
    entry = {"layer": 0, "kind": "path_absent", "path": "docs/guide.md"}
    result = check_evidence(repo, [entry])
    assert result["evidence"] == []
    assert len(result["evidence_rejected"]) == 1
    rejected = result["evidence_rejected"][0]
    assert rejected["kind"] == "path_absent"
    assert rejected["path"] == "docs/guide.md"
    assert rejected["layer"] == 0
    assert rejected["reason"]


def test_not_referenced_in_for_script_a_workflow_calls_is_rejected(repo: Path) -> None:
    entry = {
        "layer": 7,
        "kind": "not_referenced_in",
        "needle": "scripts/check-x.sh",
        "path": ".github/workflows",
    }
    result = check_evidence(repo, [entry])
    assert result["evidence"] == []
    rejected = result["evidence_rejected"]
    assert [(e["kind"], e["path"], e["needle"]) for e in rejected] == [
        ("not_referenced_in", ".github/workflows", "scripts/check-x.sh")
    ]


def test_all_true_input_passes_with_no_rejected_entries(repo: Path) -> None:
    entries = [
        {"layer": 0, "kind": "path_exists", "path": "docs/guide.md"},
        {"layer": 0, "kind": "path_absent", "path": "docs/missing.md"},
        {"layer": 7, "kind": "referenced_in", "needle": "scripts/check-x.sh",
         "path": ".github/workflows"},
        {"layer": 7, "kind": "not_referenced_in", "needle": "scripts/other.sh",
         "path": ".github/workflows"},
        {"layer": 0, "kind": "file_contains", "path": "docs/guide.md",
         "needle": "# guide"},
    ]
    result = check_evidence(repo, entries)
    assert result["evidence_rejected"] == []
    assert result["evidence"] == entries


def test_one_false_entry_of_each_kind_is_rejected(repo: Path) -> None:
    entries = [
        {"layer": 0, "kind": "path_exists", "path": "docs/missing.md"},
        {"layer": 0, "kind": "path_absent", "path": "docs/guide.md"},
        {"layer": 7, "kind": "referenced_in", "needle": "scripts/other.sh",
         "path": ".github/workflows"},
        {"layer": 7, "kind": "not_referenced_in", "needle": "scripts/check-x.sh",
         "path": ".github/workflows"},
        {"layer": 0, "kind": "file_contains", "path": "docs/guide.md",
         "needle": "no such text"},
    ]
    result = check_evidence(repo, entries)
    assert result["evidence"] == []
    assert [e["kind"] for e in result["evidence_rejected"]] == [
        e["kind"] for e in entries
    ]


def test_unknown_keys_pass_through_and_input_is_not_mutated(repo: Path) -> None:
    entry = {"layer": 0, "kind": "path_exists", "path": "docs/guide.md", "note": "x"}
    result = check_evidence(repo, [entry])
    assert result["evidence"] == [entry]
    bad = {"layer": 0, "kind": "path_exists", "path": "nope", "note": "y"}
    result = check_evidence(repo, [bad])
    assert result["evidence_rejected"][0]["note"] == "y"
    assert "reason" not in bad


@pytest.mark.parametrize(
    "entry",
    [
        {"layer": 0, "kind": "no_such_kind", "path": "docs/guide.md"},
        {"layer": 0, "kind": "path_exists"},
        {"layer": 7, "kind": "referenced_in", "path": ".github/workflows"},
        {"layer": 0, "kind": "file_contains", "path": "docs/guide.md", "needle": ""},
        {"layer": 0, "kind": "path_exists", "path": "../outside.md"},
        {"layer": 0, "kind": "path_absent", "path": "/etc/passwd"},
        {"layer": 0, "kind": "file_contains", "path": "docs", "needle": "guide"},
        {"layer": 7, "kind": "not_referenced_in", "needle": "x.sh",
         "path": ".github/nowhere"},
        "not an object",
    ],
)
def test_malformed_or_unverifiable_entries_are_rejected(repo: Path, entry) -> None:
    result = check_evidence(repo, [entry])
    assert result["evidence"] == []
    assert len(result["evidence_rejected"]) == 1


def test_is_referenced_in_searches_a_directory_or_a_single_file(repo: Path) -> None:
    assert is_referenced_in(repo, "scripts/check-x.sh", ".github/workflows") is True
    assert is_referenced_in(repo, "scripts/other.sh", ".github/workflows") is False
    assert is_referenced_in(repo, "scripts/check-x.sh", ".github/workflows/ci.yml") is True
    assert is_referenced_in(repo, "scripts/check-x.sh", ".github/missing") is False
    nested = repo / ".github" / "workflows" / "sub"
    nested.mkdir()
    (nested / "deep.yml").write_text("run: scripts/deep.sh\n")
    assert is_referenced_in(repo, "scripts/deep.sh", ".github/workflows") is True


def test_cli_writes_both_lists_to_the_json_out_file(repo: Path, tmp_path_factory) -> None:
    out_dir = tmp_path_factory.mktemp("out")
    ev = out_dir / "ev.json"
    out = out_dir / "out.json"
    ev.write_text(json.dumps([
        {"layer": 0, "kind": "path_exists", "path": "docs/guide.md"},
        {"layer": 0, "kind": "path_absent", "path": "docs/guide.md"},
    ]))
    proc = subprocess.run(
        [sys.executable, "-m", "lib.evidence_check", str(repo), str(ev),
         "--json", str(out)],
        cwd=SCRIPTS_DIR, capture_output=True, text=True,
    )
    assert proc.returncode == 1
    assert "path_absent" in proc.stdout
    data = json.loads(out.read_text())
    assert [e["kind"] for e in data["evidence"]] == ["path_exists"]
    assert [e["kind"] for e in data["evidence_rejected"]] == ["path_absent"]


def test_cli_refuses_input_that_is_not_a_flat_array(repo: Path, tmp_path_factory) -> None:
    out_dir = tmp_path_factory.mktemp("out")
    ev = out_dir / "ev.json"
    ev.write_text(json.dumps({"evidence": []}))
    proc = subprocess.run(
        [sys.executable, "-m", "lib.evidence_check", str(repo), str(ev),
         "--json", str(out_dir / "out.json")],
        cwd=SCRIPTS_DIR, capture_output=True, text=True,
    )
    assert proc.returncode == 2
    assert not (out_dir / "out.json").exists()
