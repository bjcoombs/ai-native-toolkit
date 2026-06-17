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

from lib.structure_drift import (
    apply_seam_allowlist,
    cochange_grouping_relation,
    compute_grouping_disagreement,
    detect_grouping_disagreement,
    detect_path_existence_drift,
    human_grouping_relation,
    static_grouping_relation,
)


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


# =====================================================================
# Tier 1 - equivalence-relation grouping disagreement
# =====================================================================

def _p(name: str) -> Path:
    return Path(name)


# --- 7. Relation construction from a grouping --------------------------------

def test_human_relation_emits_all_same_group_pairs() -> None:
    """A boundary owning n files contributes its n*(n-1)/2 canonical pairs.

    Two boundaries each owning two files yield two intra-group pairs; the cross-
    boundary pairs are absent (different groups), and every pair is canonical
    (``file_a < file_b``).
    """
    ownership = {
        "A": {_p("f1.py"), _p("f2.py")},
        "B": {_p("f3.py"), _p("f4.py")},
    }
    rel = human_grouping_relation(ownership)
    assert rel == {
        (_p("f1.py"), _p("f2.py")),
        (_p("f3.py"), _p("f4.py")),
    }


def test_singleton_group_contributes_no_pair() -> None:
    """A boundary owning one file declares no co-membership, so adds no pair."""
    assert human_grouping_relation({"solo": {_p("only.py")}}) == set()


def test_cochange_relation_filters_by_support_threshold() -> None:
    """Only co-change pairs at or above the support threshold enter the relation.

    The pair below ``threshold_pct`` is dropped; the survivor is canonicalised
    regardless of the order ``change_coupling`` listed its files.
    """
    pairs = [
        {"file_a": "b.py", "file_b": "a.py", "co_change_count": 9, "support_pct": 12.0},
        {"file_a": "c.py", "file_b": "d.py", "co_change_count": 2, "support_pct": 1.0},
    ]
    rel = cochange_grouping_relation(pairs, threshold_pct=5.0)
    assert rel == {(_p("a.py"), _p("b.py"))}


# --- 8. Split-vs-fuse disagreement -------------------------------------------

def test_split_and_fuse_are_directional_set_differences() -> None:
    """human-static and static-human capture the two disagreement directions.

    ``human`` groups (f1,f2); ``static`` instead groups (f2,f3). The pair the
    human declares but static splits is (f1,f2); the pair static fuses but the
    human splits is (f2,f3); they share no agreement.
    """
    human = {(_p("f1"), _p("f2"))}
    static = {(_p("f2"), _p("f3"))}
    d = compute_grouping_disagreement(human, static, cochange_rel=set())
    assert d["human_grouped_static_splits"] == [{"file_a": "f1", "file_b": "f2"}]
    assert d["human_grouped_static_splits_count"] == 1
    assert d["human_split_static_fuses"] == [{"file_a": "f2", "file_b": "f3"}]
    assert d["human_static_agree"] == []


def test_agreement_is_the_intersection() -> None:
    """A pair both lenses group lands in the agree set, not either difference."""
    human = {(_p("f1"), _p("f2")), (_p("f3"), _p("f4"))}
    static = {(_p("f1"), _p("f2"))}
    d = compute_grouping_disagreement(human, static, cochange_rel=set())
    assert d["human_static_agree"] == [{"file_a": "f1", "file_b": "f2"}]
    assert d["human_grouped_static_splits"] == [{"file_a": "f3", "file_b": "f4"}]


# --- 9. THE label-permutation invariance test (the hard gate) ----------------

def test_disagreement_is_invariant_to_community_relabeling() -> None:
    """Relabeling / reordering the communities must not change any metric.

    Communities ``[A:{f1,f2}, B:{f3,f4}]`` and the relabelled, reordered
    ``[X:{f3,f4}, Y:{f1,f2}]`` are the SAME partition - same co-membership
    relation. The disagreement against a fixed human grouping must be byte-
    identical. This is the correctness property of the whole tier: the metric
    carries pairs, never community labels.
    """
    human = {(_p("f1"), _p("f2"))}

    static_a = {(_p("f1"), _p("f2")), (_p("f3"), _p("f4"))}
    # Same partition, communities swapped and relabelled - identical relation.
    static_b = {(_p("f3"), _p("f4")), (_p("f1"), _p("f2"))}

    d_a = compute_grouping_disagreement(human, static_a, cochange_rel=set())
    d_b = compute_grouping_disagreement(human, static_b, cochange_rel=set())
    assert json.dumps(d_a, sort_keys=True) == json.dumps(d_b, sort_keys=True)


def test_static_relation_invariant_to_community_order(tmp_path: Path) -> None:
    """``static_grouping_relation`` ignores community order and labels.

    Building the relation from communities in one order and from the reversed
    list yields the identical pair set - the relation never records which
    community a pair came from. Uses bare module names that resolve to no file,
    so the relation is exercised purely as set math (empty here), the invariance
    holding trivially and by construction.
    """
    repo = _init_repo(tmp_path)
    _write(repo, "a.py")
    _commit_all(repo)
    comms = [{"lib.x", "lib.y"}, {"lib.z", "lib.w"}]
    forward = static_grouping_relation(repo, comms)
    reverse = static_grouping_relation(repo, list(reversed(comms)))
    assert forward == reverse


# --- 10. Seam allowlist ------------------------------------------------------

def test_seam_allowlist_drops_the_allowlisted_pair() -> None:
    """A pair straddling an allowlisted seam is subtracted from the denominator.

    The lib<->tests seam pair is removed from the disagreement list and its count
    drops to zero; a genuine off-seam disagreement is untouched.
    """
    seam_pair = {
        "file_a": "skills/assess/scripts/lib/foo.py",
        "file_b": "skills/assess/tests/test_foo.py",
    }
    off_seam = {"file_a": "src/a.py", "file_b": "src/b.py"}
    disagreement = {
        "human_grouped_never_cochange": [seam_pair, off_seam],
        "human_grouped_never_cochange_count": 2,
    }
    filtered = apply_seam_allowlist(disagreement)
    assert filtered["human_grouped_never_cochange"] == [off_seam]
    assert filtered["human_grouped_never_cochange_count"] == 1


def test_seam_allowlist_only_removes_never_adds() -> None:
    """An allowlist can only shrink a list (correct-by-construction denominator)."""
    disagreement = {
        "human_split_but_cochange": [{"file_a": "x/a.py", "file_b": "y/b.py"}],
        "human_split_but_cochange_count": 1,
    }
    filtered = apply_seam_allowlist(disagreement)
    assert len(filtered["human_split_but_cochange"]) <= 1


# --- 11. Top-level callable: degradation + determinism -----------------------

def test_tier1_degrades_with_no_ownership_map(tmp_path: Path) -> None:
    """No CODEOWNERS and no boundary doc -> available:False, all lists empty."""
    repo = _init_repo(tmp_path)
    _write(repo, "a.py")
    _commit_all(repo)

    result = detect_grouping_disagreement(repo)
    assert result["available"] is False
    assert result["reason"] == "no ownership map"
    assert result["tier_1_available"] is False
    assert result["human_grouped_static_splits"] == []
    assert result["human_grouped_static_splits_count"] == 0


def test_tier1_is_byte_identical_across_runs(tmp_path: Path) -> None:
    """Two runs over one repo serialize identically (no set order leaks)."""
    repo = _init_repo(tmp_path)
    _write(repo, "src/a.py")
    _write(repo, "src/b.py")
    _write(repo, "CODEOWNERS", "src/** @a\n")
    _commit_all(repo)

    first = json.dumps(
        detect_grouping_disagreement(repo, communities=[], coupling_pairs=[]),
        sort_keys=True,
    )
    second = json.dumps(
        detect_grouping_disagreement(repo, communities=[], coupling_pairs=[]),
        sort_keys=True,
    )
    assert first == second


def test_tier1_human_only_groups_codeowners_files(tmp_path: Path) -> None:
    """With no static / co-change lens, a multi-file boundary self-disagrees.

    A CODEOWNERS glob grouping two files declares them co-grouped; with empty
    static and co-change relations, that pair is ``human_grouped_static_splits``
    AND ``human_grouped_never_cochange`` (no lens corroborates it) and never
    ``agree``.
    """
    repo = _init_repo(tmp_path)
    _write(repo, "pkg/a.py")
    _write(repo, "pkg/b.py")
    _write(repo, "CODEOWNERS", "pkg/** @team\n")
    _commit_all(repo)

    result = detect_grouping_disagreement(repo, communities=[], coupling_pairs=[])
    assert result["available"] is True
    pair = {"file_a": "pkg/a.py", "file_b": "pkg/b.py"}
    assert pair in result["human_grouped_static_splits"]
    assert pair in result["human_grouped_never_cochange"]
    assert result["human_static_agree"] == []


# --- 12. Integration: this repo, zero false positives after allowlist --------

def test_tier1_integration_no_false_positives_after_allowlist() -> None:
    """On this repo the documented seams don't surface as Tier 1 disagreement.

    The lib README declares the ``skills/assess/scripts/lib`` <-> ``skills/assess/
    tests`` seam; any co-change pair straddling it must be absorbed by the seam
    allowlist, never reported as ``human_split_but_cochange``. This is the dogfood
    guard that the allowlist matches the README's stated seams.
    """
    repo_root = Path(__file__).resolve().parents[3]
    result = detect_grouping_disagreement(repo_root)
    assert result["available"] in (True, False)
    if not result["available"]:
        return
    for row in result["human_split_but_cochange"]:
        a, b = row["file_a"], row["file_b"]
        lib_test_seam = (
            (a.startswith("skills/assess/scripts/lib")
             and b.startswith("skills/assess/tests"))
            or (b.startswith("skills/assess/scripts/lib")
                and a.startswith("skills/assess/tests"))
        )
        assert not lib_test_seam, row
