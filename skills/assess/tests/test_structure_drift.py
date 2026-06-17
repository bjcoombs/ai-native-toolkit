"""Contract suite for the Tier 0 path-existence structure-drift signal.

Tier 0 is the zero-threshold cut: a declared ownership pattern (a CODEOWNERS glob
or an ARCHITECTURE.md path reference) that matches *zero* tracked files on disk -
a boundary the filesystem has left behind. These tests pin the enumerate-both-
sides behaviour, the two contracts the signal inherits from the parser
(deterministic byte-identical output, honest degradation when no map exists), and
the excluded-only-matches-as-empty rule.

Fixtures build small repos in ``tmp_path``; resolution is against the *tracked*
file set, so they commit their files. Ambient git config is neutralised
process-wide by the package ``conftest.py``.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from lib.structure_drift import detect_path_existence_drift


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args],
                   check=True, capture_output=True, text=True,
                   env={**os.environ})


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "dev@example.com")
    _git(repo, "config", "user.name", "Dev")
    return repo


def _write(repo: Path, rel: str, text: str = "x\n") -> None:
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _commit_all(repo: Path, message: str = "c") -> None:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)


def _patterns(result: dict) -> list[str]:
    return [e["pattern"] for e in result["empty_ownership_patterns"]]


# --- 1. CODEOWNERS empty-glob detection --------------------------------------

def test_empty_codeowners_glob_is_flagged(tmp_path: Path) -> None:
    """A CODEOWNERS glob matching no tracked file surfaces as drift.

    ``*.py`` matches the committed file (no drift); ``legacy/**`` matches nothing
    (the directory is gone) and is the sole empty pattern, attributed to
    ``CODEOWNERS``.
    """
    repo = _init_repo(tmp_path)
    _write(repo, "a.py")
    _write(repo, "CODEOWNERS", "*.py @a\nlegacy/** @b\n")
    _commit_all(repo)

    result = detect_path_existence_drift(repo)
    assert result["available"] is True
    assert result["tier_0_available"] is True
    assert result["empty_ownership_patterns"] == [
        {"pattern": "legacy/**", "declared_in": "CODEOWNERS", "owners": []}
    ]


def test_all_valid_codeowners_yields_no_findings(tmp_path: Path) -> None:
    """When every glob matches at least one file, there is no drift.

    The map is still available; the empty list is empty and coverage is full.
    """
    repo = _init_repo(tmp_path)
    _write(repo, "src/a.py")
    _write(repo, "docs/guide.md")
    _write(repo, "CODEOWNERS", "src/** @a\ndocs/ @b\n")
    _commit_all(repo)

    result = detect_path_existence_drift(repo)
    assert result["available"] is True
    assert result["empty_ownership_patterns"] == []
    assert result["total_patterns"] == 2
    assert result["matched_patterns"] == 2
    assert result["coverage_ratio"] == 1.0


def test_mixed_state_reports_only_the_empty_pattern(tmp_path: Path) -> None:
    """A repo with both live and stale globs reports only the stale one.

    Coverage reflects the split: two of three declared patterns match.
    """
    repo = _init_repo(tmp_path)
    _write(repo, "src/a.py")
    _write(repo, "docs/x.md")
    _write(repo, "CODEOWNERS", "src/** @a\ndocs/ @b\nghost/** @c\n")
    _commit_all(repo)

    result = detect_path_existence_drift(repo)
    assert _patterns(result) == ["ghost/**"]
    assert result["total_patterns"] == 3
    assert result["matched_patterns"] == 2
    assert result["coverage_ratio"] == 0.667


# --- 2. ARCHITECTURE.md stale-reference detection ----------------------------

def test_architecture_stale_module_ref_to_deleted_dir(tmp_path: Path) -> None:
    """A stale ARCHITECTURE.md path reference to a missing dir surfaces as drift.

    The doc declares two boundaries: a live one owning ``src/api/`` (matches) and
    a stale one owning ``src/legacy/`` (deleted, matches nothing). Only the stale
    reference is flagged, attributed to its ``<doc>::<header>`` boundary.
    """
    repo = _init_repo(tmp_path)
    _write(repo, "src/api/server.py")
    _write(repo, "ARCHITECTURE.md", "\n".join([
        "## API layer",
        "The API module owns `src/api/`.",
        "",
        "## Legacy",
        "The legacy module owns `src/legacy/`.",
    ]) + "\n")
    _commit_all(repo)

    # The parser normalises a reference's trailing punctuation, so the prose
    # ``src/legacy/`` is captured as ``src/legacy`` - that normalised form is the
    # reported pattern.
    result = detect_path_existence_drift(repo)
    assert result["empty_ownership_patterns"] == [
        {"pattern": "src/legacy", "declared_in": "ARCHITECTURE.md::Legacy",
         "owners": []}
    ]


def test_codeowners_and_architecture_empties_merge_and_sort(tmp_path: Path) -> None:
    """Empties from both sources merge into one list sorted by (pattern, source).

    A stale CODEOWNERS glob and a stale architecture reference both appear,
    ordered by pattern then declaring source - the deterministic merge key.
    """
    repo = _init_repo(tmp_path)
    _write(repo, "src/a.py")
    _write(repo, "CODEOWNERS", "src/** @a\nzzz/** @b\n")
    _write(repo, "ARCHITECTURE.md", "\n".join([
        "## Core",
        "owns `src/`",
        "## Ghost",
        "owns `aaa/gone/`",
    ]) + "\n")
    _commit_all(repo)

    # Architecture prose ``aaa/gone/`` normalises to ``aaa/gone``; the merge then
    # sorts the two empties by (pattern, source).
    result = detect_path_existence_drift(repo)
    assert _patterns(result) == ["aaa/gone", "zzz/**"]


# --- 3. Excluded-only patterns count as empty --------------------------------

def test_pattern_matching_only_excluded_files_is_empty(tmp_path: Path) -> None:
    """A glob whose only matches sit under an excluded dir reports as drift.

    ``node_modules`` is a built-in exclude, so a ``node_modules/**`` glob resolves
    to an empty set even though files exist there - the excluded tree is not part
    of the navigable repo a contributor reasons over.
    """
    repo = _init_repo(tmp_path)
    _write(repo, "src/a.py")
    _write(repo, "node_modules/dep/index.js")
    _write(repo, "CODEOWNERS", "src/** @a\nnode_modules/** @vendor\n")
    _commit_all(repo)

    result = detect_path_existence_drift(repo)
    assert _patterns(result) == ["node_modules/**"]


# --- 4. Graceful degradation -------------------------------------------------

def test_no_ownership_map_degrades(tmp_path: Path) -> None:
    """A repo with no CODEOWNERS and no boundary doc reports no ownership map."""
    repo = _init_repo(tmp_path)
    _write(repo, "a.py")
    _commit_all(repo)

    result = detect_path_existence_drift(repo)
    assert result["available"] is False
    assert result["reason"] == "no ownership map"
    assert result["tier_0_available"] is False
    assert result["empty_ownership_patterns"] == []
    assert result["total_patterns"] == 0
    assert result["coverage_ratio"] == 0.0


# --- 5. Determinism ----------------------------------------------------------

def test_output_is_byte_identical_across_runs(tmp_path: Path) -> None:
    """Two runs over one repo serialize to identical output.

    Sets are sorted at the boundary, so no iteration order leaks. Serialising the
    full block twice and asserting equality pins the determinism contract.
    """
    repo = _init_repo(tmp_path)
    _write(repo, "src/a.py")
    _write(repo, "src/b.py")
    _write(repo, "CODEOWNERS", "src/** @a\nghost/** @b\nz/** @c\n")
    _write(repo, "ARCHITECTURE.md", "## Core\nowns `src/` and `dead/`\n")
    _commit_all(repo)

    first = json.dumps(detect_path_existence_drift(repo), sort_keys=True)
    second = json.dumps(detect_path_existence_drift(repo), sort_keys=True)
    assert first == second


# --- 6. Integration: this repo's own seam map --------------------------------

def test_integration_lib_readme_seam_paths_are_not_false_positives() -> None:
    """This repo's lib README seam declaration does not false-positive.

    ``skills/assess/scripts/lib/README.md`` names ``doc_graph.py`` and the
    ``skills/assess/...`` seam directories as load-bearing boundaries; they all
    resolve to real tracked paths, so none of them appears in
    ``empty_ownership_patterns``. This is the dogfood guard that Tier 0 reads a
    genuine, human-written ownership map on real data without manufacturing drift.
    """
    repo_root = Path(__file__).resolve().parents[3]  # repo top
    result = detect_path_existence_drift(repo_root)

    assert result["available"] is True
    seam_doc = "skills/assess/scripts/lib/README.md"
    offenders = [
        e for e in result["empty_ownership_patterns"]
        if e["declared_in"].startswith(seam_doc + "::")
        and "doc_graph.py" in e["pattern"]
    ]
    assert offenders == [], offenders
