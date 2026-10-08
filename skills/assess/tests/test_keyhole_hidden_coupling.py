"""Keyhole-signal unit tests for one finding family.

Covers containment_by_dir, the static-modularity projection, the behaviour
block, the coupled pairs on each hidden_coupling finding, and the structure
drift (Tier 1) seams folded into hidden_coupling.

The pure, git-free derivation core; end-to-end wiring (build_run_context)
is covered in test_assess_core.py. Split from the former
test_keyhole_signals.py by finding family; shared integrate() fixtures live
in keyhole_helpers.py.
"""
from __future__ import annotations

from pathlib import Path

from keyhole_helpers import (
    _BLEEDING_COMMIT_SETS,
    _COMPLEXITY_STATS,
    _MODULAR_STRUCTURE,
    _stale_doc_staleness,
)
from lib import keyhole_signals as ks


# --- containment_by_dir ------------------------------------------------------

def test_containment_by_dir_flags_island_and_bleeder() -> None:
    """A directory whose commits stay inside it scores high; one whose commits
    keep dragging in outside files scores low."""
    commit_sets = [
        # island/ changes alone, repeatedly (self-contained)
        {Path("island/a.py")},
        {Path("island/a.py"), Path("island/b.py")},
        {Path("island/b.py")},
        {Path("island/a.py")},
        {Path("island/c.py")},
        # bleeder/ always drags in core/
        {Path("bleeder/x.py"), Path("core/util.py")},
        {Path("bleeder/y.py"), Path("core/util.py")},
        {Path("bleeder/x.py"), Path("core/other.py")},
        {Path("bleeder/z.py"), Path("core/util.py")},
        {Path("bleeder/x.py"), Path("shared/s.py")},
    ]
    cont = ks.containment_by_dir(Path("/nonexistent"), commit_sets, min_commits=5)
    assert cont["island"] == 1.0
    assert cont["bleeder"] == 0.0
    # The repo root "." is never a candidate directory (vacuously contained).
    assert "." not in cont


def test_containment_by_dir_respects_min_commits() -> None:
    """Directories touched fewer than min_commits times are omitted."""
    commit_sets = [{Path("rare/a.py")}, {Path("rare/a.py")}]
    cont = ks.containment_by_dir(Path("/nonexistent"), commit_sets, min_commits=5)
    assert cont == {}


# --- static-modularity projection -------------------------------------------

def test_project_static_modularity_repo_level_onto_dirs() -> None:
    structure = {"available": True, "modularity_q": 0.5, "front_door_ratio": 0.9}
    proj = ks.project_static_modularity(structure, ["a", "b"])
    assert proj == {
        "a": {"modularity_q": 0.5, "front_door_ratio": 0.9},
        "b": {"modularity_q": 0.5, "front_door_ratio": 0.9},
    }


def test_project_static_modularity_none_when_unavailable() -> None:
    assert ks.project_static_modularity({"available": False}, ["a"]) is None
    assert ks.project_static_modularity(None, ["a"]) is None


# --- behaviour block ---------------------------------------------------------

def test_behaviour_block_hidden_coupling_when_modular_but_bleeds() -> None:
    """A bleeding dir that looks modular statically becomes hidden_coupling."""
    commit_sets = [
        {Path("looksmodular/x.py"), Path("core/util.py")},
        {Path("looksmodular/y.py"), Path("core/util.py")},
        {Path("looksmodular/x.py"), Path("core/other.py")},
        {Path("looksmodular/z.py"), Path("core/util.py")},
        {Path("looksmodular/x.py"), Path("shared/s.py")},
    ]
    structure = {"available": True, "modularity_q": 0.6, "front_door_ratio": 0.95}
    block = ks.build_behaviour_block(Path("/nonexistent"), commit_sets, structure)
    assert block["available"] is True
    hc_paths = [h["path"] for h in block["hidden_coupling_findings"]]
    assert "looksmodular" in hc_paths
    # B1 change-coupling pairs are wired in (a list; exact contents depend on
    # min_support, exercised in test_change_coupling.py).
    assert isinstance(block["change_coupling_pairs"], list)


def test_behaviour_block_bleeding_module_without_static_graph() -> None:
    """No static graph -> a bleeding dir degrades to bleeding_module."""
    commit_sets = [
        {Path("bleeder/x.py"), Path("core/util.py")},
        {Path("bleeder/y.py"), Path("core/util.py")},
        {Path("bleeder/x.py"), Path("core/other.py")},
        {Path("bleeder/z.py"), Path("core/util.py")},
        {Path("bleeder/x.py"), Path("shared/s.py")},
    ]
    block = ks.build_behaviour_block(
        Path("/nonexistent"), commit_sets, {"available": False}
    )
    findings = {f["finding"] for f in block["static_history_disagreement"]}
    assert "bleeding_module" in findings
    assert block["hidden_coupling_findings"] == []


def test_behaviour_block_non_python_dir_never_hidden_coupling() -> None:
    """The static import graph is silent on doc/config trees, so a bleeding
    non-Python dir degrades to bleeding_module, never a false hidden_coupling -
    even when a Python static graph is available."""
    commit_sets = [
        {Path("docs/a.md"), Path("core/util.py")},
        {Path("docs/b.md"), Path("core/util.py")},
        {Path("docs/a.md"), Path("core/other.py")},
        {Path("docs/c.md"), Path("core/util.py")},
        {Path("docs/a.md"), Path("src/s.py")},
    ]
    structure = {"available": True, "modularity_q": 0.6, "front_door_ratio": 0.95}
    block = ks.build_behaviour_block(Path("/nonexistent"), commit_sets, structure)
    hc_paths = [h["path"] for h in block["hidden_coupling_findings"]]
    assert "docs" not in hc_paths
    findings = {f["path"]: f["finding"] for f in block["static_history_disagreement"]}
    assert findings.get("docs") == "bleeding_module"


def test_behaviour_block_refactor_boundary_is_positive() -> None:
    commit_sets = [
        {Path("island/a.py")},
        {Path("island/b.py")},
        {Path("island/a.py")},
        {Path("island/c.py")},
        {Path("island/b.py")},
    ]
    block = ks.build_behaviour_block(Path("/nonexistent"), commit_sets, None)
    assert any(b["path"] == "island" for b in block["refactor_boundaries"])


# --- coupled pairs on each hidden-coupling finding ---------------------------

# The structure dict every fixture below passes: `project_static_modularity`
# reads exactly these three keys, and one metric at or above its threshold is
# what makes a bleeding directory read as hidden_coupling rather than a plain
# bleeding_module. The free variable in each fixture is the history, not this.
_MODULAR = {"available": True, "modularity_q": 0.9, "front_door_ratio": 0.9}


def _history(spec: list[tuple[list[str], int]]) -> list[set[Path]]:
    """Expand ``[(files, repeats), ...]`` into a commit file-set list."""
    return [{Path(f) for f in files} for files, repeats in spec for _ in range(repeats)]


def _pair_ids(finding: dict) -> list[str]:
    return [f"{p['file_a']}>{p['file_b']}" for p in finding["coupled_pairs"]]


def _by_path(block: dict) -> dict[str, dict]:
    return {f["path"]: f for f in block["hidden_coupling_findings"]}


def test_coupled_pairs_export_matches_on_path_components_not_prefix() -> None:
    """A file is inside D when it begins with ``D + "/"``.

    `src/app2` begins with the characters of `src/app`, so a `startswith(D)`
    test without the separator puts `src/app2/x.py` on the `src/app` finding.
    The parent `src` takes both, which is what a component test must do.
    """
    commit_sets = _history(
        [(["src/app/a.py", "ext/e.py"], 6), (["src/app2/x.py", "ext/e.py"], 5)]
    )
    block = ks.build_behaviour_block(Path("/nonexistent"), commit_sets, _MODULAR)
    by_path = _by_path(block)
    assert sorted(by_path) == ["ext", "src", "src/app", "src/app2"]
    assert _pair_ids(by_path["src/app"]) == ["ext/e.py>src/app/a.py"]
    assert by_path["src/app"]["coupled_pairs_total"] == 1
    assert _pair_ids(by_path["src/app2"]) == ["ext/e.py>src/app2/x.py"]
    assert by_path["src/app2"]["coupled_pairs_total"] == 1
    assert _pair_ids(by_path["src"]) == [
        "ext/e.py>src/app/a.py",
        "ext/e.py>src/app2/x.py",
    ]
    assert by_path["src"]["coupled_pairs_total"] == 2


def test_coupled_pairs_export_excludes_pairs_wholly_inside_the_directory() -> None:
    """Only a pair that crosses D's boundary counts: exactly one file inside D.

    A hidden-coupling finding reports commits bleeding out of D, so a pair with
    both files inside D is cohesion, not evidence, and a pair with neither file
    inside D is about somewhere else. Both are left off D's list.
    """
    commit_sets = _history(
        [
            (["alpha/a.py", "beta/p.py"], 6),
            (["alpha/b.py", "beta/q.py"], 4),
            (["beta/p.py", "beta/q.py"], 3),
        ]
    )
    block = ks.build_behaviour_block(Path("/nonexistent"), commit_sets, _MODULAR)
    by_path = _by_path(block)
    # beta/p.py>beta/q.py sits wholly inside beta: excluded from beta's list.
    assert _pair_ids(by_path["beta"]) == [
        "alpha/a.py>beta/p.py",
        "alpha/b.py>beta/q.py",
    ]
    assert by_path["beta"]["coupled_pairs_total"] == 2
    # ...and wholly outside alpha: excluded from alpha's list too.
    assert _pair_ids(by_path["alpha"]) == [
        "alpha/a.py>beta/p.py",
        "alpha/b.py>beta/q.py",
    ]
    assert by_path["alpha"]["coupled_pairs_total"] == 2


def test_coupled_pairs_export_ancestor_keeps_only_its_own_crossings() -> None:
    """A pair can cross a child directory and still sit wholly inside its
    parent. It counts for the child and not the parent, so an ancestor's five
    slots are not filled by the internal seams of its own subtree, which carry
    the highest counts in most repositories."""
    # Four commits stay inside src; twelve reach out to ext, so src bleeds
    # (containment 0.25) while its internal pair still has the top count.
    commit_sets = _history(
        [(["src/app/a.py", "src/b.py"], 4)]
        + [(["src/app/a.py", f"ext/e{i}.py"], 3) for i in range(1, 5)]
    )
    block = ks.build_behaviour_block(Path("/nonexistent"), commit_sets, _MODULAR)
    by_path = _by_path(block)
    assert _pair_ids(by_path["src/app"]) == [
        "src/app/a.py>src/b.py",
        "ext/e1.py>src/app/a.py",
        "ext/e2.py>src/app/a.py",
        "ext/e3.py>src/app/a.py",
        "ext/e4.py>src/app/a.py",
    ]
    assert by_path["src/app"]["coupled_pairs_total"] == 5
    # src/app/a.py>src/b.py is wholly inside src, so despite leading on count
    # it takes none of src's slots.
    assert _pair_ids(by_path["src"]) == [
        "ext/e1.py>src/app/a.py",
        "ext/e2.py>src/app/a.py",
        "ext/e3.py>src/app/a.py",
        "ext/e4.py>src/app/a.py",
    ]
    assert by_path["src"]["coupled_pairs_total"] == 4


def test_coupled_pairs_export_reads_windows_separated_pair_paths(monkeypatch) -> None:
    """Pair paths come from ``str(Path)``, backslash-separated on Windows,
    while finding paths are posix. Ancestors are derived the way
    `_candidate_dirs` derives flagged directories, so the two still meet.
    CI runs on POSIX, so the module's ``Path`` is pointed at the Windows
    flavour to parse the pair paths as Windows would."""
    from pathlib import PureWindowsPath

    monkeypatch.setattr(ks, "Path", PureWindowsPath)
    findings = [{"path": "src/app"}, {"path": "src"}]
    pairs = [
        {"file_a": "ext\\e.py", "file_b": "src\\app\\a.py",
         "co_change_count": 6, "support_pct": 50.0},
        {"file_a": "src\\app\\a.py", "file_b": "src\\b.py",
         "co_change_count": 4, "support_pct": 33.33},
    ]
    ks._attach_coupled_pairs(findings, pairs)
    by_path = {f["path"]: f for f in findings}
    # Both pairs cross src/app; only the ext pair crosses src.
    assert by_path["src/app"]["coupled_pairs_total"] == 2
    assert by_path["src"]["coupled_pairs"] == [pairs[0]]
    assert by_path["src"]["coupled_pairs_total"] == 1


def test_coupled_pairs_export_cuts_to_five_and_reports_the_total() -> None:
    """At most 5 pairs per finding, in the repository-wide list's own order,
    with the pre-cut count beside them so a cut list never reads as complete."""
    commit_sets = _history(
        [(["hub/h.py", f"out/f{i}.py"], 9 if i == 3 else 3) for i in range(1, 13)]
    )
    block = ks.build_behaviour_block(Path("/nonexistent"), commit_sets, _MODULAR)
    hub = _by_path(block)["hub"]
    # f3 leads on co_change_count; the eleven that tie at 3 order by file_b
    # under a byte comparison, so unpadded names run f1, f10, f11, f12, f2.
    assert _pair_ids(hub) == [
        "hub/h.py>out/f3.py",
        "hub/h.py>out/f1.py",
        "hub/h.py>out/f10.py",
        "hub/h.py>out/f11.py",
        "hub/h.py>out/f12.py",
    ]
    assert hub["coupled_pairs_total"] == 12
    # The existing repository-wide list is untouched: same twelve, same order.
    assert len(block["change_coupling_pairs"]) == 12
    assert block["change_coupling_pairs_total"] == 12


def test_coupled_pairs_export_selects_before_the_repository_wide_cap() -> None:
    """A pair the 100-pair cap discards still reaches its own finding.

    Sixteen `noise/` files co-changing ten times is 120 pairs at count 10;
    the single `edge`-to-`far` pair at count 5 sorts last of 121 and is cut
    from `change_coupling_pairs`. Selecting from that capped list is the
    defect this export exists to avoid.
    """
    noise = [f"noise/n{i:02d}.py" for i in range(1, 17)]
    commit_sets = _history([(noise, 10), (["edge/e.py", "far/g.py"], 5)])
    block = ks.build_behaviour_block(Path("/nonexistent"), commit_sets, _MODULAR)
    assert len(block["change_coupling_pairs"]) == ks.MAX_COUPLING_PAIRS == 100
    assert block["change_coupling_pairs_total"] == 121
    capped = {(p["file_a"], p["file_b"]) for p in block["change_coupling_pairs"]}
    assert ("edge/e.py", "far/g.py") not in capped
    edge = _by_path(block)["edge"]
    assert _pair_ids(edge) == ["edge/e.py>far/g.py"]
    assert edge["coupled_pairs_total"] == 1


def test_coupled_pairs_export_keys_are_present_and_empty_when_nothing_matches() -> None:
    """`coupled_pairs: []` with `coupled_pairs_total: 0`, never an absent key:
    the report has to tell "no pairs recorded" from "field absent"."""
    commit_sets = _history(
        [(["lone/l.py", f"sink/s{i}.py"], 1) for i in range(1, 6)]
        + [(["mod/m.py", "app/z.py"], 6)]
    )
    block = ks.build_behaviour_block(Path("/nonexistent"), commit_sets, _MODULAR)
    lone = _by_path(block)["lone"]
    assert lone["coupled_pairs"] == []
    assert lone["coupled_pairs_total"] == 0


def test_coupled_pairs_export_keys_stay_off_non_hidden_coupling_entries() -> None:
    """`hidden_coupling_findings` and `static_history_disagreement` share their
    record objects, so a write onto every disagreement entry would leak the two
    keys onto `bleeding_module` records. It must not."""
    commit_sets = _history(
        [(["mod/m.py", "app/z.py"], 6), (["cfg/c.yaml", "app/z.py"], 5)]
    )
    block = ks.build_behaviour_block(Path("/nonexistent"), commit_sets, _MODULAR)
    by_finding = {f["path"]: f for f in block["static_history_disagreement"]}
    # cfg carries no Python file, so the static graph is silent on it and it
    # degrades to bleeding_module.
    assert by_finding["cfg"]["finding"] == "bleeding_module"
    assert set(by_finding["cfg"]) == {
        "path",
        "containment_ratio",
        "finding",
        "recommendation",
    }
    assert set(by_finding["mod"]) == {
        "path",
        "containment_ratio",
        "finding",
        "recommendation",
        "coupled_pairs",
        "coupled_pairs_total",
    }


def test_coupled_pairs_export_total_is_present_on_the_unavailable_path() -> None:
    """No commit file-sets: the block-level total is 0, not absent, so a
    consumer never meets the key on one path and not the other."""
    block = ks.build_behaviour_block(Path("/nonexistent"), [], _MODULAR)
    assert block["available"] is False
    assert block["change_coupling_pairs_total"] == 0


def test_coupled_pairs_export_total_is_present_when_the_builder_fails(
    tmp_path: Path, monkeypatch,
) -> None:
    """The other unavailable path: `integrate` degrades a builder that raises
    to a fallback block. That block carries the total too, with the same data
    keys as the no-history block, so the two paths cannot drift apart."""
    def boom(*_args, **_kwargs):
        raise RuntimeError("simulated signal failure")

    monkeypatch.setattr(ks, "build_behaviour_block", boom)
    out = ks.integrate(
        repo_root=tmp_path, complexity_stats={}, doc_staleness={},
        dead_code={}, observability={}, structure={},
        commit_sets=[{Path("a/x.py")}],
    )
    degraded = out["behaviour"]
    assert degraded["available"] is False
    assert "simulated signal failure" in degraded["reason"]
    assert degraded["change_coupling_pairs_total"] == 0
    monkeypatch.undo()
    no_history = ks.build_behaviour_block(Path("/nonexistent"), [], _MODULAR)
    assert set(degraded) == set(no_history)


# --- structure drift (Tier 1) folded into hidden_coupling --------------------

def _drift_tier1(pairs: list[tuple[str, str]]) -> dict:
    """A minimal available Tier 1 result carrying only the hidden-seam list."""
    return {
        "available": True,
        "human_split_but_cochange": [
            {"file_a": a, "file_b": b} for a, b in pairs
        ],
    }


def test_drift_hidden_coupling_unavailable_is_empty() -> None:
    """An unavailable Tier 1 result yields no hidden-coupling dirs."""
    assert ks.structure_drift_hidden_coupling_dirs({"available": False}) == []
    assert ks.structure_drift_hidden_coupling_dirs({}) == []


def test_drift_hidden_coupling_recurring_dir_pair_surfaces() -> None:
    """Two trees straddled by >= min_pairs distinct file pairs both surface.

    Two distinct file pairs link src/ and lib/; the pair recurs, so both
    directories read as a genuine hidden seam.
    """
    tier1 = _drift_tier1([
        ("src/a.py", "lib/x.py"),
        ("src/b.py", "lib/y.py"),
    ])
    assert ks.structure_drift_hidden_coupling_dirs(tier1) == ["lib", "src"]


def test_drift_hidden_coupling_hub_file_is_not_a_seam() -> None:
    """A single hub file coupling with every tree manufactures no seam.

    The version hot-file shape: one file in cfg/ co-changes with a different
    directory on each pair. Each directory *pair* occurs exactly once, so none
    recurs and no directory surfaces - the version-bump ritual is not drift.
    """
    tier1 = _drift_tier1([
        ("cfg/v.json", "src/a.py"),
        ("cfg/v.json", "lib/b.py"),
        ("cfg/v.json", "docs/c.md"),
    ])
    assert ks.structure_drift_hidden_coupling_dirs(tier1) == []


def test_drift_hidden_coupling_ignores_root_and_intra_dir() -> None:
    """Pairs touching the repo root, or within one directory, are ignored.

    A root-level file (dir ``.``) has vacuous containment; an intra-directory
    pair is cohesion, not a cross-tree seam. Neither contributes a finding even
    when repeated.
    """
    tier1 = _drift_tier1([
        ("README.md", "src/a.py"),   # root side -> ignored
        ("README.md", "src/b.py"),   # root side -> ignored
        ("src/c.py", "src/d.py"),    # intra-dir -> ignored
        ("src/e.py", "src/f.py"),    # intra-dir -> ignored
    ])
    assert ks.structure_drift_hidden_coupling_dirs(tier1) == []


def test_drift_hidden_coupling_single_pair_below_threshold() -> None:
    """One file pair straddling two trees is below the recurrence threshold."""
    tier1 = _drift_tier1([("src/a.py", "lib/x.py")])
    assert ks.structure_drift_hidden_coupling_dirs(tier1) == []


def test_drift_tier1_silent_without_static_graph() -> None:
    """_structure_drift_tier1 returns unavailable when the static graph is out.

    With no import graph there is nothing to disagree with, so Tier 1 is not
    even attempted - the detector is never called.
    """
    out = ks._structure_drift_tier1(
        Path("/nonexistent"),
        {"available": False},
        {"available": True, "change_coupling_pairs": []},
    )
    assert out == {"available": False}


def test_integrate_returns_structure_drift_tier1() -> None:
    """integrate() exposes the Tier 1 result for the orchestrator to serialise.

    With a /nonexistent repo there is no ownership map, so the detector degrades
    to available:False - but the key is present, proving the wiring exists.
    """
    result = ks.integrate(
        repo_root=Path("/nonexistent"),
        complexity_stats=_COMPLEXITY_STATS,
        doc_staleness=_stale_doc_staleness(churn_degenerate=False),
        dead_code={"available": False, "candidate_count": 0,
                   "candidates": [], "tools": []},
        observability={"rung": None, "reachable": {"present": False}},
        structure=_MODULAR_STRUCTURE,
        commit_sets=_BLEEDING_COMMIT_SETS,
    )
    assert "structure_drift_tier1" in result
    assert result["structure_drift_tier1"].get("available") in (True, False)
