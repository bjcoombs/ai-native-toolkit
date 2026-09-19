"""Tests for lib/evidence_check.py - deterministic re-check of scorer evidence.

Each evidence entry is re-checked with exists() or a literal substring search.
A false entry lands in ``evidence_rejected`` with its kind and arguments intact
(that is what "naming the entry" means); a true one lands in ``evidence``.
"""
from __future__ import annotations

import json
import os
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
    ("entry", "reason"),
    [
        ({"layer": 0, "kind": "no_such_kind", "path": "docs/guide.md"}, "unknown kind"),
        ({"layer": 0, "kind": "path_exists"}, "missing path"),
        ({"layer": 7, "kind": "referenced_in", "path": ".github/workflows"}, "missing needle"),
        ({"layer": 0, "kind": "file_contains", "path": "docs/guide.md", "needle": ""},
         "missing needle"),
        ({"layer": 0, "kind": "path_exists", "path": "../outside.md"},
         "outside the repository root"),
        ({"layer": 0, "kind": "path_absent", "path": "/etc/passwd"},
         "outside the repository root"),
        ({"layer": 0, "kind": "file_contains", "path": "docs", "needle": "guide"},
         "not a file"),
        ({"layer": 7, "kind": "not_referenced_in", "needle": "x.sh",
          "path": ".github/nowhere"}, "does not exist"),
        ("not an object", "not an object"),
    ],
)
def test_malformed_or_unverifiable_entries_are_rejected(repo: Path, entry, reason: str) -> None:
    result = check_evidence(repo, [entry])
    assert result["evidence"] == []
    assert len(result["evidence_rejected"]) == 1
    assert reason in result["evidence_rejected"][0]["reason"]


def test_named_path_through_a_symlinked_directory_out_of_the_repo_is_rejected(
    repo: Path, tmp_path_factory
) -> None:
    outside = tmp_path_factory.mktemp("outside")
    (outside / "notes.md").write_text("scripts/secret.sh\n")
    (repo / "docs" / "ext").symlink_to(outside, target_is_directory=True)
    entries = [
        {"layer": 0, "kind": "path_exists", "path": "docs/ext/notes.md"},
        {"layer": 0, "kind": "file_contains", "path": "docs/ext/notes.md",
         "needle": "scripts/secret.sh"},
        {"layer": 7, "kind": "referenced_in", "needle": "scripts/secret.sh",
         "path": "docs/ext"},
    ]
    result = check_evidence(repo, entries)
    assert result["evidence"] == []
    assert all("outside the repository root" in e["reason"]
               for e in result["evidence_rejected"])
    assert is_referenced_in(repo, "scripts/secret.sh", "docs/ext") is False


def test_lone_surrogate_needle_is_rejected_not_raised(repo: Path) -> None:
    [needle] = json.loads('["\\ud800"]')
    entries = [
        {"layer": 7, "kind": kind, "needle": needle, "path": path}
        for kind, path in (("referenced_in", ".github/workflows"),
                           ("not_referenced_in", ".github/workflows"),
                           ("file_contains", "docs/guide.md"))
    ]
    result = check_evidence(repo, entries)
    assert result["evidence"] == []
    assert all("not valid text" in e["reason"] for e in result["evidence_rejected"])
    assert is_referenced_in(repo, needle, ".github/workflows") is False


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


def test_path_with_nul_is_rejected_not_raised(repo: Path) -> None:
    entries = [
        {"layer": 0, "kind": "path_exists", "path": "docs/\0guide.md"},
        {"layer": 7, "kind": "referenced_in", "needle": "x", "path": ".github\0"},
    ]
    result = check_evidence(repo, entries)
    assert result["evidence"] == []
    assert len(result["evidence_rejected"]) == 2
    assert is_referenced_in(repo, "x", ".github\0") is False


def test_reference_search_does_not_enter_git_metadata(repo: Path) -> None:
    (repo / ".git").mkdir()
    (repo / ".git" / "config").write_text("scripts/check-x.sh\n")
    assert is_referenced_in(repo, "scripts/check-x.sh", ".git") is False
    assert is_referenced_in(repo, "scripts/check-x.sh", ".git/config") is False
    for kind in ("referenced_in", "not_referenced_in"):
        result = check_evidence(
            repo, [{"layer": 7, "kind": kind, "needle": "x", "path": ".git/config"}]
        )
        assert result["evidence"] == [], kind


@pytest.mark.skipif(sys.platform == "win32" or not hasattr(os, "geteuid") or os.geteuid() == 0,
                    reason="permission bits do not restrict root or Windows")
def test_not_referenced_in_is_rejected_when_the_search_is_incomplete(repo: Path) -> None:
    hidden = repo / ".github" / "workflows" / "locked"
    hidden.mkdir()
    (hidden / "ci.yml").write_text("run: scripts/other.sh\n")
    unreadable = repo / ".github" / "workflows" / "unreadable.yml"
    unreadable.write_text("run: scripts/third.sh\n")
    hidden.chmod(0)
    unreadable.chmod(0)
    try:
        for needle in ("scripts/other.sh", "scripts/third.sh"):
            entry = {"layer": 7, "kind": "not_referenced_in", "needle": needle,
                     "path": ".github/workflows"}
            result = check_evidence(repo, [entry])
            assert result["evidence"] == [], needle
            assert "could not be searched" in result["evidence_rejected"][0]["reason"]
        # A match found elsewhere still verifies a positive claim.
        ok = {"layer": 7, "kind": "referenced_in", "needle": "scripts/check-x.sh",
              "path": ".github/workflows"}
        assert check_evidence(repo, [ok])["evidence"] == [ok]
    finally:
        hidden.chmod(0o755)
        unreadable.chmod(0o644)


def _not_referenced(needle: str, path: str = ".github/workflows") -> dict:
    return {"layer": 7, "kind": "not_referenced_in", "needle": needle, "path": path}


def test_walk_skips_symlinks_out_of_the_repo(repo: Path, tmp_path_factory) -> None:
    outside = tmp_path_factory.mktemp("outside")
    (outside / "hosts").write_text("scripts/secret.sh\n")
    (repo / ".github" / "workflows" / "link.yml").symlink_to(outside / "hosts")
    (repo / ".github" / "workflows" / "linkdir").symlink_to(outside, target_is_directory=True)
    (repo / ".github" / "workflows" / "dangling.yml").symlink_to(repo / "no-such-file")
    assert is_referenced_in(repo, "scripts/secret.sh", ".github/workflows") is False
    entry = _not_referenced("scripts/secret.sh")
    # Content outside the root is not repository content: the claim holds.
    assert check_evidence(repo, [entry])["evidence"] == [entry]


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="no FIFOs on this platform")
def test_fifo_under_the_path_makes_a_negative_claim_incomplete(repo: Path) -> None:
    os.mkfifo(repo / ".github" / "workflows" / "pipe")
    result = check_evidence(repo, [_not_referenced("scripts/other.sh")])
    assert result["evidence"] == []
    assert "could not be searched" in result["evidence_rejected"][0]["reason"]
    # Named directly as the path, the FIFO is incomplete too, never an empty search.
    named = check_evidence(repo, [
        _not_referenced("scripts/other.sh", ".github/workflows/pipe"),
        {"layer": 7, "kind": "referenced_in", "needle": "scripts/other.sh",
         "path": ".github/workflows/pipe"},
    ])
    assert named["evidence"] == []
    assert all("could not be searched" in e["reason"] for e in named["evidence_rejected"])
    assert is_referenced_in(repo, "scripts/other.sh", ".github/workflows/pipe") is False
    # The FIFO is never opened, and a match elsewhere still verifies.
    ok = {"layer": 7, "kind": "referenced_in", "needle": "scripts/check-x.sh",
          "path": ".github/workflows"}
    assert check_evidence(repo, [ok])["evidence"] == [ok]


def test_symlinked_file_inside_the_repo_is_searched_at_its_target(repo: Path) -> None:
    (repo / "ci-shared").mkdir()
    (repo / "ci-shared" / "deploy.yml").write_text("run: scripts/deploy.sh\n")
    (repo / ".github" / "workflows" / "shared.yml").symlink_to(
        repo / "ci-shared" / "deploy.yml")
    assert is_referenced_in(repo, "scripts/deploy.sh", ".github/workflows") is True
    result = check_evidence(repo, [_not_referenced("scripts/deploy.sh")])
    assert "needle found" in result["evidence_rejected"][0]["reason"]


def test_symlinked_directory_inside_the_repo_makes_a_negative_claim_incomplete(
    repo: Path,
) -> None:
    (repo / "ci-shared").mkdir()
    (repo / "ci-shared" / "deploy.yml").write_text("run: scripts/deploy.sh\n")
    (repo / ".github" / "workflows" / "shared").symlink_to(
        repo / "ci-shared", target_is_directory=True)
    result = check_evidence(repo, [_not_referenced("scripts/deploy.sh")])
    assert result["evidence"] == []
    assert "could not be searched" in result["evidence_rejected"][0]["reason"]
    # A link back into the directory already being searched adds nothing unread.
    (repo / ".github" / "workflows" / "shared").unlink()
    (repo / ".github" / "workflows" / "self").symlink_to(
        repo / ".github" / "workflows", target_is_directory=True)
    entry = _not_referenced("scripts/deploy.sh")
    assert check_evidence(repo, [entry])["evidence"] == [entry]


def test_walk_does_not_read_previous_assess_output(repo: Path) -> None:
    (repo / ".assess").mkdir()
    (repo / ".assess" / "assess-report.md").write_text("scripts/stale.sh is not wired\n")
    entry = _not_referenced("scripts/stale.sh", ".")
    assert check_evidence(repo, [entry])["evidence"] == [entry]
    # Naming .assess directly still searches it.
    assert is_referenced_in(repo, "scripts/stale.sh", ".assess") is True


def test_needle_spanning_a_read_chunk_boundary_is_found(repo: Path) -> None:
    from lib import evidence_check

    needle = "scripts/boundary.sh"
    pad = "x" * (evidence_check._CHUNK - 5)
    (repo / "docs" / "big.txt").write_text(pad + needle + "\n")
    assert is_referenced_in(repo, needle, "docs") is True
    assert is_referenced_in(repo, needle, "docs/big.txt") is True


def test_root_that_is_not_a_directory_verifies_nothing(tmp_path: Path) -> None:
    missing = tmp_path / "no-such-root"
    entry = {"layer": 0, "kind": "path_absent", "path": "docs/guide.md"}
    result = check_evidence(missing, [entry])
    assert result["evidence"] == []
    assert "not a directory" in result["evidence_rejected"][0]["reason"]


def test_cli_refuses_a_root_that_is_not_a_directory(tmp_path: Path) -> None:
    ev = tmp_path / "ev.json"
    ev.write_text(json.dumps([{"layer": 0, "kind": "path_absent", "path": "x"}]))
    proc = subprocess.run(
        [sys.executable, "-m", "lib.evidence_check", str(tmp_path / "nope"), str(ev),
         "--json", str(tmp_path / "out.json")],
        cwd=SCRIPTS_DIR, capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 2
    assert not (tmp_path / "out.json").exists()


def test_cli_refuses_evidence_that_is_not_utf8(repo: Path, tmp_path_factory) -> None:
    out_dir = tmp_path_factory.mktemp("out")
    ev = out_dir / "ev.json"
    ev.write_bytes(b'[{"layer": 0, "kind": "path_exists", "path": "docs/\xff.md"}]')
    proc = subprocess.run(
        [sys.executable, "-m", "lib.evidence_check", str(repo), str(ev),
         "--json", str(out_dir / "out.json")],
        cwd=SCRIPTS_DIR, capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 2, proc.stderr
    assert "Traceback" not in proc.stderr
    assert not (out_dir / "out.json").exists()


def test_symlinks_into_git_metadata_are_not_searched(repo: Path) -> None:
    (repo / ".git").mkdir()
    (repo / ".git" / "config").write_text("scripts/hidden.sh\n")
    (repo / "docs" / "gitlink").symlink_to(repo / ".git", target_is_directory=True)
    (repo / "config-link").symlink_to(repo / ".git" / "config")
    (repo / ".github" / "workflows" / "cfg.yml").symlink_to(repo / ".git" / "config")
    for path in ("docs/gitlink", "config-link", ".github/workflows"):
        assert is_referenced_in(repo, "scripts/hidden.sh", path) is False, path
    named = check_evidence(repo, [
        {"layer": 7, "kind": "referenced_in", "needle": "x", "path": "docs/gitlink"},
        {"layer": 7, "kind": "not_referenced_in", "needle": "x", "path": "config-link"},
    ])
    assert named["evidence"] == []
    assert all(".git/" in e["reason"] for e in named["evidence_rejected"])
    walked = _not_referenced("scripts/hidden.sh")
    assert check_evidence(repo, [walked])["evidence"] == [walked]


def test_cli_exits_2_when_the_json_output_cannot_be_written(repo: Path, tmp_path_factory) -> None:
    out_dir = tmp_path_factory.mktemp("out")
    ev = out_dir / "ev.json"
    ev.write_text(json.dumps([{"layer": 0, "kind": "path_absent", "path": "docs/guide.md"}]))
    proc = subprocess.run(
        [sys.executable, "-m", "lib.evidence_check", str(repo), str(ev),
         "--json", str(out_dir / "missing-dir" / "out.json")],
        cwd=SCRIPTS_DIR, capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 2, proc.stderr
    assert "Traceback" not in proc.stderr
