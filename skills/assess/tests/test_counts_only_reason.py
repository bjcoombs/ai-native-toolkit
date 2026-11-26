"""A full counts-only run keeps ``structure.reason`` as its code (issue #764)."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

import assess_core
from lib.counts_only.content_free import COUNTS_ONLY_FILE, counts_only, reason_token
from lib.counts_only.counts_only_rules import REASON, classify
from lib.graph.import_graph import (
    INVALID_IMPORT_GRAPH,
    LANGUAGE_BY_EXTENSION,
    NO_PYTHON_PACKAGES,
    NO_SOURCE_FILES,
    STRUCTURE_REASON_CODES,
    no_graph_reason,
)
from lib.graph.import_graph_python import GRIMP_MISSING


def test_structure_reason_is_a_reason_code_rule() -> None:
    assert classify("structure.reason") == REASON


@pytest.mark.parametrize(("reason", "code"), [
    ("no_import_graph_rust: no import graph was built for rust, ...", "no_import_graph_rust"),
    ("no_source_files: nothing to graph", "no_source_files"),
    ("grimp not installed; static structure not assessed", "no_grimp"),
    ("networkx not installed; static structure not assessed", "no_networkx"),
    ("grimp failed to build import graph (boom)", "grimp_failed"),
    ("no importable Python packages found", "no_python_packages"),
    ("something else went wrong", "unavailable"),
    # privacy: a path-like or name-like prefix never survives as a code
    ("/home/user/repo: permission denied", "unavailable"),
    ("src/app.py: syntax error", "unavailable"),
    ("alice: could not read the module", "unavailable"),
    ("alice@example.com: denied", "unavailable"),
    ("Alice_Smith: denied", "unavailable"),
    ("alice_smith: denied", "unavailable"),
    ("no_import_graph_alice: denied", "unavailable"),
    # #825: no_builder keeps its language, and only a known one
    ("no_builder:java: no import graph builder covers java, ...; jdeps would provide one",
     "no_builder:java"),
    ("no_builder:alice: denied", "unavailable"),
    ("no_builder: denied", "unavailable"),
])
def test_reason_token_keeps_the_code_only(reason: str, code: str) -> None:
    assert reason_token(reason) == code


@pytest.mark.parametrize("ext", sorted(LANGUAGE_BY_EXTENSION))
def test_every_no_graph_reason_keeps_a_listed_code(tmp_path: Path, ext: str) -> None:
    """The allow-list and the reasons the selector writes cannot drift (#764)."""
    (tmp_path / f"a{ext}").write_text("x\n", encoding="utf-8")
    code = reason_token(no_graph_reason(tmp_path))
    assert code in STRUCTURE_REASON_CODES
    assert code != "unavailable"


@pytest.mark.parametrize("reason", [
    NO_SOURCE_FILES, NO_PYTHON_PACKAGES, GRIMP_MISSING,
    f"{INVALID_IMPORT_GRAPH}: the rust builder returned 1 module(s) with no package",
    "grimp failed to build import graph (boom)",
    "networkx not installed; static structure not assessed",
])
def test_every_builder_reason_keeps_a_listed_code(reason: str) -> None:
    code = reason_token(reason)
    assert code in STRUCTURE_REASON_CODES


def test_allow_list_holds_only_structure_codes() -> None:
    assert all(c.startswith(("no_", "grimp_", "invalid_")) for c in STRUCTURE_REASON_CODES)


def test_counts_only_keeps_structure_reason_code_and_drops_empty() -> None:
    out = counts_only({"structure": {"available": False,
                                     "reason": "no_import_graph_rust: a sentence here"}})
    assert out["structure"] == {"available": False, "reason": "no_import_graph_rust"}
    out = counts_only({"structure": {"available": True, "reason": ""}})
    assert "reason" not in out["structure"]


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True,
                   encoding="utf-8", errors="replace",
                   env={**os.environ, "GIT_CONFIG_GLOBAL": os.devnull})


def test_full_counts_only_run_on_rust_repo_names_the_language(tmp_path: Path) -> None:
    repo = tmp_path / "rs"
    (repo / "src").mkdir(parents=True)
    (repo / "Cargo.toml").write_text(
        '[package]\nname = "x"\nversion = "0.1.0"\nedition = "2021"\n', encoding="utf-8")
    _git(repo, "init", "-q")
    ident = ("-c", "user.email=a@example.com", "-c", "user.name=A")
    _git(repo, *ident, "add", "-A")
    _git(repo, *ident, "commit", "-qm", "chore: manifest")
    (repo / "src" / "main.rs").write_text(
        'fn main() {\n    println!("hi");\n}\n', encoding="utf-8")
    _git(repo, *ident, "add", "-A")
    _git(repo, *ident, "commit", "-qm", "feat: main")

    assert assess_core.main([str(repo), "--non-interactive", "--counts-only"]) == 0
    out = json.loads((repo / ".assess" / COUNTS_ONLY_FILE).read_text(encoding="utf-8"))
    assert out["structure"]["available"] is False
    assert out["structure"]["reason"] == "no_builder:rust"
