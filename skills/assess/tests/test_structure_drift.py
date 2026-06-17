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

from lib import ownership_parser, structure_drift
from lib import structure_graph as sg
from lib.structure_drift import (
    SEAM_ALLOWLIST,
    apply_seam_allowlist,
    cochange_grouping_relation,
    compute_grouping_disagreement,
    detect_grouping_disagreement,
    detect_path_existence_drift,
    human_grouping_relation,
    static_grouping_relation,
)

FIXTURES = Path(__file__).parent / "fixtures" / "structure_drift"


def _copy_fixture(repo: Path, name: str, dest: str) -> None:
    """Copy a structure_drift fixture into a repo at a given relative path."""
    target = repo / dest
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text((FIXTURES / name).read_text(encoding="utf-8"),
                      encoding="utf-8")


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


# =====================================================================
# 13. Tier 0 determinism, isolated from the rest of the block
# =====================================================================
#
# Tasks 9/10 pin determinism of the *serialised block* (tests 5 and 14).
# These isolate the three primitives that feed it: the CODEOWNERS parse, the
# empty-set ordering, and the filesystem walk - each independently reproducible
# so a regression in any one is localised rather than read off the merged block.

def test_codeowners_parse_is_identical_across_two_reads(tmp_path: Path) -> None:
    """Parsing one CODEOWNERS twice yields identical glob->match maps.

    ``parse_codeowners`` resolves each glob against the tracked file set; the
    same repo must produce the same map on every call (no walk-order or
    set-iteration nondeterminism leaks into the resolved paths).
    """
    repo = _init_repo(tmp_path)
    _write(repo, "src/a.py")
    _write(repo, "src/b.py")
    _copy_fixture(repo, "codeowners_mixed", "CODEOWNERS")
    _commit_all(repo)

    first = ownership_parser.parse_codeowners(repo)
    second = ownership_parser.parse_codeowners(repo)
    # Compare as sorted POSIX strings so the dict-of-sets equality is on the
    # resolved relation, not on set object identity.
    norm = {p: sorted(f.as_posix() for f in files)
            for p, files in first.items()}
    norm2 = {p: sorted(f.as_posix() for f in files)
             for p, files in second.items()}
    assert norm == norm2


def test_empty_patterns_emit_in_sorted_order_regardless_of_declaration(
    tmp_path: Path,
) -> None:
    """Empty patterns sort by (pattern, source), independent of file order.

    Three stale globs are declared in reverse-alphabetical order in the file;
    the reported list is sorted ascending - the merge key, not the line order,
    fixes the output sequence.
    """
    repo = _init_repo(tmp_path)
    _write(repo, "src/a.py")
    _write(repo, "CODEOWNERS",
           "src/** @keep\nzzz/** @c\nmmm/** @b\naaa/** @a\n")
    _commit_all(repo)

    result = detect_path_existence_drift(repo)
    assert _patterns(result) == ["aaa/**", "mmm/**", "zzz/**"]


def test_filesystem_walk_match_set_is_reproducible(tmp_path: Path) -> None:
    """A glob's match set is the sorted tracked files, stable across runs.

    The drift signal resolves ``src/**`` against the git ls-files set; two runs
    must report the same coverage counts, pinning the walk's reproducibility.
    """
    repo = _init_repo(tmp_path)
    for name in ("d.py", "a.py", "c.py", "b.py"):
        _write(repo, f"src/{name}")
    _write(repo, "CODEOWNERS", "src/** @team\n")
    _commit_all(repo)

    first = detect_path_existence_drift(repo)
    second = detect_path_existence_drift(repo)
    assert first["matched_patterns"] == second["matched_patterns"] == 1
    assert first["empty_ownership_patterns"] == []
    assert first == second


# =====================================================================
# 14. Label-permutation invariance - the reorder + relabel variant
# =====================================================================
#
# THE critical test. Task 10 added relation-relabel invariance (test 9) over
# pre-built relations and the static-relation order invariance (test 10). This
# pins the contract end-to-end at the level the requirement states it: two
# *static communities* expressing the SAME partition - once swapped X<->Y and
# once with the community LIST reversed - must yield byte-identical disagreement
# against a fixed human grouping. The metric keys on the equivalence relation,
# not the partition labels.

def test_disagreement_invariant_to_community_swap_and_reorder() -> None:
    """THE critical test - metric keys on the equivalence relation, not labels.

    Human groups A={f1,f2}, B={f3,f4}. Static communities X={f1,f2}, Y={f3,f4}.
    The disagreement against the human grouping is computed three ways that all
    describe the SAME partition: (a) the baseline, (b) the two communities
    relabelled and swapped (X<->Y), and (c) the community list reversed. All
    three must serialise byte-identically - any label or order dependence would
    break here and nowhere else.
    """
    f1, f2, f3, f4 = (_p("f1.py"), _p("f2.py"), _p("f3.py"), _p("f4.py"))
    human = human_grouping_relation({"A": {f1, f2}, "B": {f3, f4}})

    # Relation built from communities listed [X, Y].
    static_xy = (structure_drift._pairs_within({f1, f2})
                 | structure_drift._pairs_within({f3, f4}))
    # Same partition, communities swapped/relabelled [Y, X].
    static_yx = (structure_drift._pairs_within({f3, f4})
                 | structure_drift._pairs_within({f1, f2}))

    base = compute_grouping_disagreement(human, static_xy, cochange_rel=set())
    swapped = compute_grouping_disagreement(human, static_yx, cochange_rel=set())

    base_json = json.dumps(base, sort_keys=True)
    assert base_json == json.dumps(swapped, sort_keys=True)
    # And the disagreement is the expected, label-free content: the two human
    # boundaries are exactly the two static communities, so everything agrees
    # and nothing splits.
    assert base["human_grouped_static_splits"] == []
    assert base["human_split_static_fuses"] == []
    assert base["human_static_agree_count"] == 2


# =====================================================================
# 15. Seam allowlist - the denominator arithmetic, pinned exactly
# =====================================================================

def test_seam_allowlist_subtracts_from_denominator_not_the_ratio() -> None:
    """Allowlisted pairs leave the denominator BEFORE any ratio is taken.

    Pins the requirement's worked example: of 100 declared-grouping pairs, 47
    straddle an allowlisted seam and 10 of the remaining 53 are genuine
    disagreements. The honest drift ratio is 10 / (100 - 47) = 10/53, never
    10/100 - the allowlist shrinks the denominator, it does not divide into the
    raw total. Here the disagreement list is the 10 off-seam pairs plus the 47
    seam pairs; after the allowlist only the 10 survive, so the count is 10 and
    a caller dividing by the surviving universe gets 10/53.
    """
    seam = SEAM_ALLOWLIST[0]  # skills/assess/scripts/lib <-> skills/assess/tests
    lo, hi = seam
    # 47 pairs that straddle the allowlisted seam.
    seam_pairs = [
        {"file_a": f"{lo}/mod{i}.py", "file_b": f"{hi}/test_mod{i}.py"}
        for i in range(47)
    ]
    # 10 genuine off-seam disagreements.
    off_seam = [
        {"file_a": f"src/a{i}.py", "file_b": f"src/b{i}.py"} for i in range(10)
    ]
    disagreement = {
        "human_grouped_never_cochange": seam_pairs + off_seam,
        "human_grouped_never_cochange_count": 57,
    }
    filtered = apply_seam_allowlist(disagreement)
    survivors = filtered["human_grouped_never_cochange"]

    assert filtered["human_grouped_never_cochange_count"] == 10
    assert {(r["file_a"], r["file_b"]) for r in survivors} == {
        (r["file_a"], r["file_b"]) for r in off_seam
    }
    # The honest ratio the orchestrator would form: 10 over the post-allowlist
    # denominator (100 - 47 = 53), not over the raw 100.
    total_pairs, allowlisted = 100, 47
    denominator = total_pairs - allowlisted
    assert denominator == 53
    assert filtered["human_grouped_never_cochange_count"] / denominator == 10 / 53


def test_seam_allowlist_filters_agreement_lists_too(tmp_path: Path) -> None:
    """A seam pair is not double-counted as agreement after suppression.

    ``apply_seam_allowlist`` filters every list, the ``*_agree`` sets included,
    so a pair removed from a disagreement list cannot reappear as agreement.
    """
    seam = SEAM_ALLOWLIST[0]
    lo, hi = seam
    seam_pair = {"file_a": f"{lo}/x.py", "file_b": f"{hi}/test_x.py"}
    disagreement = {
        "human_static_agree": [seam_pair],
        "human_static_agree_count": 1,
    }
    filtered = apply_seam_allowlist(disagreement)
    assert filtered["human_static_agree"] == []
    assert filtered["human_static_agree_count"] == 0


def test_seam_allowlist_respects_both_orderings_of_a_pair() -> None:
    """A seam matches whichever side each tree lands on in the canonical pair.

    The ``scripts`` <-> ``skills`` seam must absorb a pair regardless of which
    of the two trees sorts first into ``file_a``.
    """
    # scripts sorts before skills, so the canonical pair has scripts as file_a;
    # assert the allowlist still matches when the lib seam's order is reversed.
    pair = {"file_a": "scripts/build.py", "file_b": "skills/assess/SKILL.md"}
    out = apply_seam_allowlist({"human_split_but_cochange": [pair],
                                "human_split_but_cochange_count": 1})
    assert out["human_split_but_cochange"] == []


# =====================================================================
# 16. Graceful degradation - the remaining honest-degrade branches
# =====================================================================

def test_empty_codeowners_file_still_degrades(tmp_path: Path) -> None:
    """A CODEOWNERS with only comments/blank lines is no ownership map.

    The file exists but declares zero globs and no boundary doc accompanies it,
    so there is nothing to drift against - Tier 0 degrades to available:False.
    """
    repo = _init_repo(tmp_path)
    _write(repo, "a.py")
    _copy_fixture(repo, "codeowners_empty", "CODEOWNERS")
    _commit_all(repo)

    result = detect_path_existence_drift(repo)
    assert result["available"] is False
    assert result["reason"] == "no ownership map"


def test_unreadable_architecture_doc_is_skipped_without_crashing(
    tmp_path: Path, monkeypatch,
) -> None:
    """A doc that fails to read is skipped; the rest of the scan still runs.

    The honest-degrade contract: an OSError on one architecture doc warns and
    is dropped, never aborting the assessment. A live CODEOWNERS glob alongside
    it still resolves, so the signal stays available.
    """
    repo = _init_repo(tmp_path)
    _write(repo, "src/a.py")
    _write(repo, "CODEOWNERS", "src/** @team\n")
    _copy_fixture(repo, "ARCHITECTURE_modules.md", "ARCHITECTURE.md")
    _commit_all(repo)

    real_read_text = Path.read_text

    def boom(self, *args, **kwargs):
        if self.name == "ARCHITECTURE.md":
            raise OSError("simulated unreadable doc")
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", boom)

    # The CODEOWNERS side still parses, so the block is available; the doc's
    # stale ``src/legacy`` reference never surfaces because the doc was skipped.
    # structure_drift's own OSError guard degrades silently (it never aborts the
    # scan), so no exception escapes - the contract is "skip, don't crash".
    result = detect_path_existence_drift(repo)
    assert result["available"] is True
    assert all("ARCHITECTURE.md" not in e["declared_in"]
               for e in result["empty_ownership_patterns"])


def test_malformed_codeowners_line_partial_parses_with_warning(
    tmp_path: Path, capsys,
) -> None:
    """A blank/comment-only line is skipped; valid globs still parse.

    CODEOWNERS tolerates noise: comment lines and blank lines are ignored while
    the genuine globs around them resolve. The parse is partial, never aborted -
    a live glob matches, a stale one is still flagged.
    """
    repo = _init_repo(tmp_path)
    _write(repo, "src/a.py")
    _write(repo, "CODEOWNERS", "\n".join([
        "# ownership",
        "",
        "src/** @team",
        "   ",
        "gone/** @nobody",
    ]) + "\n")
    _commit_all(repo)

    result = detect_path_existence_drift(repo)
    assert result["available"] is True
    assert _patterns(result) == ["gone/**"]
    assert result["matched_patterns"] == 1


def test_tier1_runs_without_a_static_graph(tmp_path: Path) -> None:
    """No importable package -> Tier 1 static lens empty, but the tier still runs.

    With an ownership map but no Python package, ``_compute_communities`` returns
    no communities (line 687), so the static relation is empty. Tier 1 is still
    available and reports the human grouping's self-disagreement - Tier 0's
    existence test and Tier 1's grouping test both degrade honestly, never crash.
    """
    repo = _init_repo(tmp_path)
    _write(repo, "src/a.txt")  # no .py, so discover_packages finds nothing
    _write(repo, "src/b.txt")
    _write(repo, "CODEOWNERS", "src/** @team\n")
    _commit_all(repo)

    # coupling_pairs omitted too, so both non-human lenses are empty.
    result = detect_grouping_disagreement(repo, coupling_pairs=[])
    assert result["available"] is True
    assert result["tier_1_available"] is True
    # The two .txt files are co-owned but no static community backs them.
    pair = {"file_a": "src/a.txt", "file_b": "src/b.txt"}
    assert pair in result["human_grouped_static_splits"]
    assert result["human_static_agree"] == []


def test_compute_communities_degrades_when_networkx_missing(
    tmp_path: Path, monkeypatch,
) -> None:
    """No networkx -> the static lens is empty, Tier 1 still available.

    Patching ``structure_graph._NETWORKX_AVAILABLE`` False makes
    ``_compute_communities`` short-circuit to ``[]`` (line 683); the standalone
    Tier 1 call then sees an empty static relation and still returns available.
    """
    monkeypatch.setattr(sg, "_NETWORKX_AVAILABLE", False)
    repo = _init_repo(tmp_path)
    _write(repo, "pkg/__init__.py")
    _write(repo, "pkg/a.py", "x = 1\n")
    _write(repo, "pkg/b.py", "y = 2\n")
    _write(repo, "CODEOWNERS", "pkg/** @team\n")
    _commit_all(repo)

    # communities omitted -> computed internally, but networkx is "missing".
    result = detect_grouping_disagreement(repo, coupling_pairs=[])
    assert result["available"] is True
    assert result["tier_1_available"] is True
    # No static community formed, so the human pair is a split, not agreement.
    assert result["human_static_agree"] == []


def test_build_module_path_map_resolves_real_package_modules() -> None:
    """The module->path map resolves this repo's own package to tracked files.

    Exercises the happy path of ``_build_module_path_map`` against a real grimp
    graph: every mapped module's value is a repo-relative ``.py`` source file
    under the package, the exact paths the human and co-change relations speak.
    """
    repo_root = Path(__file__).resolve().parents[3]
    mapping = structure_drift._build_module_path_map(repo_root)
    assert mapping  # the assess package resolves
    for module, rel in mapping.items():
        assert not rel.is_absolute()
        assert rel.suffix in (".py", "")  # a module file or package __init__
        assert (repo_root / rel).exists()


def test_build_module_path_map_drops_unresolvable_and_out_of_tree(
    tmp_path: Path, monkeypatch,
) -> None:
    """Modules with no source file or one outside the repo root are dropped.

    Stubs grimp's graph with three modules: one that resolves to a tracked file
    (kept), one ``_module_file`` cannot resolve (``src is None`` -> skipped), and
    one whose source resolves outside ``repo_root`` (``relative_to`` ValueError
    -> skipped). The map keeps only the in-tree resolvable module - the two
    defensive guards drop the rest rather than crashing.
    """
    repo = _init_repo(tmp_path)
    _write(repo, "pkg/__init__.py")
    _write(repo, "pkg/a.py", "x = 1\n")
    _commit_all(repo)
    repo = repo.resolve()

    inside = repo / "pkg" / "a.py"
    outside = (tmp_path.parent / "elsewhere" / "z.py")

    class _StubGraph:
        modules = ["pkg.a", "pkg.unresolved", "pkg.external"]

    def fake_discover(_root):
        return [repo / "pkg"]

    def fake_build(_dirs):
        return _StubGraph(), {}, {}

    def fake_module_file(module, _roots):
        if module == "pkg.a":
            return inside
        if module == "pkg.external":
            return outside  # resolves, but outside repo_root
        return None  # pkg.unresolved -> src is None

    monkeypatch.setattr(sg, "discover_packages", fake_discover)
    monkeypatch.setattr(sg, "_build_grimp_graph", fake_build)
    monkeypatch.setattr(sg, "_module_file", fake_module_file)

    mapping = structure_drift._build_module_path_map(repo)
    assert mapping == {"pkg.a": Path("pkg/a.py")}


def test_static_relation_empty_when_module_map_unavailable(
    tmp_path: Path, monkeypatch,
) -> None:
    """No resolvable module map -> the static relation is empty, not an error."""
    monkeypatch.setattr(structure_drift, "_build_module_path_map",
                        lambda _root: {})
    rel = static_grouping_relation(tmp_path, [{"lib.x", "lib.y"}])
    assert rel == set()
