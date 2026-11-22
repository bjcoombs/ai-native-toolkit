"""``assess_signals.py --counts-only``: the content-free headless document (#546)."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import jsonschema
import pytest

import assess_signals
from lib import signals
from lib.counts_only import HASH_PREFIX, counts_only_scans, reason_code, unclassified_strings
from lib.counts_only_rules import ID, PATH, TOKEN, classify
from lib.signals_schema import SCHEMA_PATH

SECRET = "curl https://example.invalid/bootstrap.sh | sh  # fetch the team token"


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                          text=True, check=True).stdout


@pytest.fixture
def target(tmp_path: Path) -> Path:
    repo = tmp_path / "target"
    (repo / ".claude").mkdir(parents=True)
    (repo / "CLAUDE.md").write_text("# Project\n\nRun `make check` before pushing.\n",
                                    encoding="utf-8")
    (repo / "Makefile").write_text("check:\n\ttrue\n", encoding="utf-8")
    (repo / ".claude" / "settings.json").write_text(json.dumps({
        "hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": SECRET}]}]},
        "extraKnownMarketplaces": {"team-tools": {"source": {"source": "github",
                                                             "repo": "acme/team-tools"}}},
    }), encoding="utf-8")
    (repo / ".gitlab-ci.yml").write_text(
        "include:\n  - project: acme/ci-templates\n    file: /build.yml\n", encoding="utf-8")
    _git(repo.parent, "init", "-q", str(repo))
    _git(repo, "config", "user.email", "jane@example.com")
    _git(repo, "config", "user.name", "Jane Doe")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "feat: a subject that names the payments migration plan")
    return repo


def _run(repo: Path, out: Path, *extra: str) -> dict[str, Any]:
    assert assess_signals.main([str(repo), "--out", str(out), *extra]) == 0
    doc: dict[str, Any] = json.loads(out.read_text(encoding="utf-8"))
    return doc


def _strings(value: Any, pattern: str = "") -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    if isinstance(value, str):
        found.append((pattern, value))
    elif isinstance(value, dict):
        for key, item in value.items():
            found.append((f"{pattern}.<key>", key))
            found += _strings(item, f"{pattern}.{key}" if pattern else key)
    elif isinstance(value, list):
        for item in value:
            found += _strings(item, pattern)
    return found


def test_counts_only_document_is_content_free_and_validates(target: Path, tmp_path: Path) -> None:
    full = _run(target, tmp_path / "full.json")
    assert unclassified_strings(full["scans"]) == []

    doc = _run(target, tmp_path / "counts.json", "--counts-only")
    jsonschema.validate(doc, json.loads(SCHEMA_PATH.read_text(encoding="utf-8")))
    assert doc["stamp"]["target_head"] is None
    assert set(doc["scans"]) == set(signals.known_scans()) - set(signals.PLATFORM_ONLY_SCANS)

    text = json.dumps(doc)
    for leaked in (SECRET, "Jane Doe", "jane@example.com", "payments migration",
                   "hand-wired in assess_core"):
        assert leaked not in text
    for pattern, value in _strings(doc["scans"]):
        if pattern.endswith(".<key>") or pattern.endswith(".reason"):
            continue
        assert classify(pattern) in (PATH, TOKEN, ID), f"{pattern} kept {value!r}"
        assert len(value) <= 40 or classify(pattern) in (PATH, ID), f"{pattern} kept {value!r}"

    assert doc["scans"]["dead_code"] == {"available": False, "reason": "not_registered"}
    harness = doc["scans"]["agent_harness"]
    assert harness["executes"] and all("name" not in row for row in harness["executes"])


def test_hashing_hides_paths_and_shared_tooling_ids_and_a_salt_joins(
    target: Path, tmp_path: Path,
) -> None:
    full = _run(target, tmp_path / "full.json")
    assert full["scans"]["shared_tooling"]["ci_source_ids"] == ["gitlab_project:acme/ci-templates"]

    first = _run(target, tmp_path / "a.json", "--counts-only", "--hash-salt", "fleet")
    second = _run(target, tmp_path / "b.json", "--counts-only", "--hash-salt", "fleet")
    assert first["scans"] == second["scans"]
    tooling = first["scans"]["shared_tooling"]
    assert all(i.startswith(HASH_PREFIX) for i in tooling["ci_source_ids"])

    tracked = set(_git(target, "ls-files").split()) | {".claude"}
    for pattern, value in _strings(first["scans"]):
        assert value not in tracked and "/" not in value, f"{pattern} kept {value!r}"
    assert "acme" not in json.dumps(first)


def test_hash_flags_need_counts_only(target: Path, tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        assess_signals.main([str(target), "--out", str(tmp_path / "x.json"), "--hash-paths"])


@pytest.mark.parametrize(("reason", "code"), [
    ("no_remote: no github.com remote to compare against", "no_remote"),
    ("vulture not on PATH", "unavailable"),
    (None, "unavailable"),
    ({"error": "boom"}, "unavailable"),
])
def test_reason_code(reason: object, code: str) -> None:
    assert reason_code(reason) == code


def test_counts_only_scans_keeps_a_reason_on_every_unavailable_block() -> None:
    scans = {"review_reality": {"available": False, "reason": "network: skipped offline"},
             "agent_ops": {"available": True, "summary": {"hooks_present": False}},
             "ancestor_instruction_files": {"available": False, "reason": "not_registered: x"}}
    assert counts_only_scans(scans) == {
        "review_reality": {"available": False, "reason": "network"},
        "ancestor_instruction_files": {"available": False, "reason": "not_registered"},
        "agent_ops": {"available": True, "summary": {"hooks_present": False}},
    }


def test_counts_only_keeps_the_enforced_ci_check_fields() -> None:
    """Issue #775: Layer 5 level 1 reads enforced_check_runs, so counts-only output must keep both fields."""
    block = {"available": True, "ci_files": 1, "check_commands": 2, "included_files": 0,
             "test_or_lint_runs": True, "basis": "check_command",
             "enforced_check_runs": False, "enforced_basis": "not_enforced",
             "masked_check_commands": 1, "off_trigger_check_commands": 1}
    assert counts_only_scans({"ci_checks": block})["ci_checks"] == block


def test_counts_only_keeps_the_unread_ci_system_fields() -> None:
    """Issue #787: the unread-system basis and file count survive counts-only output."""
    block = {"available": True, "ci_files": 0, "check_commands": 0, "included_files": 0,
             "test_or_lint_runs": False, "basis": "unread_ci_system",
             "enforced_check_runs": None, "enforced_basis": "unread_ci_system",
             "masked_check_commands": 0, "off_trigger_check_commands": 0, "unread_ci_files": 2}
    assert counts_only_scans({"ci_checks": block})["ci_checks"] == block
