"""Tests for raw-source subtree detection (issue #225).

The pure classifier in ``lib.raw_source`` decides, from three per-doc graph
signals, whether a directory subtree is a dump of raw, machine-extracted source
documents that should be excluded from the headline read-side metrics. These
tests pin the threshold contract so the detector behaves identically regardless
of which LLM is driving the surrounding assessment.
"""
from __future__ import annotations

import pytest

from lib.raw_source import (
    RAW_TREE_ISOLATION_DENSITY,
    RAW_TREE_MACHINE_DENSITY,
    RAW_TREE_MIN_FILES,
    WORKING_NOTES_INDEX_SHARE,
    WORKING_NOTES_LOW_INDEGREE_DENSITY,
    WORKING_NOTES_MIN_FILES,
    WORKING_NOTES_NAME_DENSITY,
    _name_key,
    classify_raw_trees,
    classify_working_notes_trees,
)


def _signal(in_degree: int = 0, out_degree: int = 0, machine_links: int = 0) -> dict:
    return {
        "in_degree": in_degree,
        "out_degree": out_degree,
        "machine_links": machine_links,
    }


def _raw_tree(prefix: str, n: int, machine_share: float = 1.0) -> dict[str, dict]:
    """A subtree of ``n`` link-isolated docs; ``machine_share`` of them carry a
    machine-extracted (non-navigational) link."""
    signals: dict[str, dict] = {}
    machine_count = round(n * machine_share)
    for i in range(n):
        signals[f"{prefix}/doc-{i:03d}.md"] = _signal(
            machine_links=1 if i < machine_count else 0,
        )
    return signals


def test_no_docs_returns_empty() -> None:
    assert classify_raw_trees({}) == []


def test_large_isolated_machine_tree_detected() -> None:
    signals = _raw_tree("sar-export", RAW_TREE_MIN_FILES + 2)
    trees = classify_raw_trees(signals)
    assert len(trees) == 1
    assert trees[0]["path"] == "sar-export"
    assert trees[0]["file_count"] == RAW_TREE_MIN_FILES + 2
    assert len(trees[0]["docs"]) == RAW_TREE_MIN_FILES + 2


def test_below_min_files_not_detected() -> None:
    signals = _raw_tree("sar-export", RAW_TREE_MIN_FILES - 1)
    assert classify_raw_trees(signals) == []


def test_isolated_but_no_machine_links_not_detected() -> None:
    # A folder of genuinely standalone-but-curated notes: link-isolated, but
    # none carry the machine-extraction fingerprint. Must NOT be excluded.
    signals = _raw_tree("notes", RAW_TREE_MIN_FILES + 5, machine_share=0.0)
    assert classify_raw_trees(signals) == []


def test_well_linked_machine_tree_not_detected() -> None:
    # Files carry machine links but are also internally navigable (in/out edges).
    signals: dict[str, dict] = {}
    for i in range(RAW_TREE_MIN_FILES + 4):
        signals[f"corpus/doc-{i:03d}.md"] = _signal(
            in_degree=2, out_degree=2, machine_links=1,
        )
    assert classify_raw_trees(signals) == []


def test_entry_point_excluded_from_isolation_numerator() -> None:
    # An entry doc in the subtree is not counted as isolated; with enough
    # isolated machine docs around it the tree still qualifies.
    signals = _raw_tree("dump", RAW_TREE_MIN_FILES + 4)
    entry = "dump/index.md"
    signals[entry] = _signal(out_degree=5, machine_links=0)
    trees = classify_raw_trees(signals, entries={entry})
    assert len(trees) == 1
    assert trees[0]["path"] == "dump"


def test_outermost_subtree_is_kept() -> None:
    # Two qualifying batch subtrees nested under a qualifying parent: only the
    # outermost ("export") is reported, not its children.
    signals: dict[str, dict] = {}
    signals.update(_raw_tree("export/batch-1", RAW_TREE_MIN_FILES + 1))
    signals.update(_raw_tree("export/batch-2", RAW_TREE_MIN_FILES + 1))
    trees = classify_raw_trees(signals)
    assert [t["path"] for t in trees] == ["export"]
    assert trees[0]["file_count"] == 2 * (RAW_TREE_MIN_FILES + 1)


def test_two_independent_raw_trees_both_reported() -> None:
    signals: dict[str, dict] = {}
    signals.update(_raw_tree("sar-export", RAW_TREE_MIN_FILES + 1))
    signals.update(_raw_tree("disclosure-dump", RAW_TREE_MIN_FILES + 1))
    trees = classify_raw_trees(signals)
    assert sorted(t["path"] for t in trees) == ["disclosure-dump", "sar-export"]


def test_root_level_docs_never_excluded() -> None:
    # Root-level isolated machine docs (no enclosing subtree) are never excluded
    # so a whole-repo false positive can't zero out the metrics.
    signals = {
        f"doc-{i:03d}.md": _signal(machine_links=1)
        for i in range(RAW_TREE_MIN_FILES + 5)
    }
    assert classify_raw_trees(signals) == []


def test_thresholds_are_tunable() -> None:
    # A smaller tree is detected once the min-files threshold is lowered.
    signals = _raw_tree("small-dump", 4)
    assert classify_raw_trees(signals) == []
    trees = classify_raw_trees(signals, min_files=3)
    assert [t["path"] for t in trees] == ["small-dump"]


def test_machine_density_threshold_boundary() -> None:
    # Exactly at the machine-density threshold qualifies; just below does not.
    n = 20
    at = _raw_tree("a", n, machine_share=RAW_TREE_MACHINE_DENSITY)
    assert [t["path"] for t in classify_raw_trees(at)] == ["a"]
    below = _raw_tree("b", n, machine_share=RAW_TREE_MACHINE_DENSITY - 0.1)
    assert classify_raw_trees(below) == []


def test_isolation_density_threshold() -> None:
    # A subtree where too many docs are linked (below the isolation density)
    # does not qualify even with machine links everywhere.
    n = 20
    linked = round(n * (1 - RAW_TREE_ISOLATION_DENSITY) + 1)
    signals: dict[str, dict] = {}
    for i in range(n):
        is_linked = i < linked
        signals[f"mix/doc-{i:03d}.md"] = _signal(
            in_degree=1 if is_linked else 0,
            out_degree=1 if is_linked else 0,
            machine_links=1,
        )
    assert classify_raw_trees(signals) == []


# --- Working-notes trees (issue #366) ---------------------------------------


def _wn_signal(sources: list[str]) -> dict:
    return {"in_degree": len(sources), "inbound_sources": sources}


def _notes_tree(prefix: str, n: int, stem: str = "plan_{i:02d}") -> dict[str, dict]:
    """``n`` pattern-named notes each linked once from ``<prefix>/backlog.md``,
    and the backlog index linked once from the README."""
    signals = {
        f"{prefix}/{stem.format(i=i)}.md": _wn_signal([f"{prefix}/backlog.md"])
        for i in range(1, n + 1)
    }
    signals[f"{prefix}/backlog.md"] = _wn_signal(["README.md"])
    return signals


def test_working_notes_tree_counts_the_index_too() -> None:
    signals = _notes_tree("notes", 50)
    signals["README.md"] = _wn_signal([])
    trees = classify_working_notes_trees(signals)
    assert [(t["path"], t["file_count"]) for t in trees] == [("notes", 51)]
    assert "notes/backlog.md" in trees[0]["docs"]


def test_working_notes_date_and_ticket_names_match() -> None:
    dated = {
        f"journal/2026-01-{d:02d}-standup.md": _wn_signal(["journal/log.md"])
        for d in range(1, 29)
    }
    tickets = {
        f"tickets/PROJ-{i}.md": _wn_signal(["tickets/board.md"]) for i in range(100, 130)
    }
    trees = classify_working_notes_trees({**dated, **tickets})
    assert [t["path"] for t in trees] == ["journal", "tickets"]


def test_varied_cross_linked_wiki_is_not_working_notes() -> None:
    names = [f"topic{chr(97 + i % 26)}{chr(97 + i // 26)}" for i in range(50)]
    words = ["auth", "billing", "cache", "deploy", "events"] * 10
    stems = [f"{w}{n}" for w, n in zip(words, names)]
    signals = {
        f"wiki/{s}.md": _wn_signal([f"wiki/{stems[(i + k) % 50]}.md" for k in (1, 7, 13)])
        for i, s in enumerate(stems)
    }
    assert classify_working_notes_trees(signals) == []


def test_small_curated_wiki_does_not_match() -> None:
    names = "architecture billing caching deploy events glossary logging metrics onboarding"
    stems = names.split()
    signals = {
        f"docs/{s}.md": _wn_signal([f"docs/{stems[(i + 1) % 9]}.md", f"docs/{stems[(i + 4) % 9]}.md"])
        for i, s in enumerate(stems)
    }
    assert classify_working_notes_trees(signals) == []


def test_working_notes_below_size_threshold_not_classified() -> None:
    signals = _notes_tree("notes", WORKING_NOTES_MIN_FILES - 2)
    assert classify_working_notes_trees(signals) == []


def test_pattern_named_notes_without_an_index_hub_not_classified() -> None:
    # Same names, but every note is linked from a different source: no index
    # holds the inbound links, so the index leg fails.
    signals = {
        f"notes/plan_{i:02d}.md": _wn_signal([f"docs/page{i}.md"]) for i in range(40)
    }
    assert classify_working_notes_trees(signals) == []


def test_heavily_linked_pattern_names_not_classified() -> None:
    # Pattern names under one index, but each page has several inbound links:
    # a curated series (release notes cross-linked), not working notes.
    signals = {
        f"notes/plan_{i:02d}.md": _wn_signal(
            ["notes/index.md", f"notes/plan_{(i + 1) % 40:02d}.md", f"notes/plan_{(i + 2) % 40:02d}.md"]
        )
        for i in range(40)
    }
    assert classify_working_notes_trees(signals) == []


def test_working_notes_nested_tree_keeps_curated_siblings() -> None:
    # A notes tree inside docs/ must not pull its curated siblings with it.
    signals = _notes_tree("docs/notes", 50)
    signals["docs/guide.md"] = _wn_signal(["README.md"])
    trees = classify_working_notes_trees(signals)
    assert [t["path"] for t in trees] == ["docs/notes"]


def test_prefix_named_indexed_section_is_not_working_notes() -> None:
    # A curated how-to section: same-prefixed pages each linked once from the
    # section README. Passes in-degree and index; the name leg must fail it,
    # because a shared word is not a sequence.
    topics = [f"how-to-{w}" for w in (
        "deploy rotate-keys restore scale debug profile migrate upgrade rollback "
        "tag release audit onboard offboard backup patch seed index cache trace"
    ).split()]
    signals = {f"docs/how-to/{t}.md": _wn_signal(["docs/how-to/README.md"]) for t in topics}
    signals["docs/how-to/README.md"] = _wn_signal(["README.md"])
    assert classify_working_notes_trees(signals) == []


def _indexed_section(prefix: str, stems: list[str]) -> dict[str, dict]:
    """Pages each linked once from ``<prefix>/README.md``, linked from the root."""
    signals = {f"{prefix}/{s}.md": _wn_signal([f"{prefix}/README.md"]) for s in stems}
    signals[f"{prefix}/README.md"] = _wn_signal(["README.md"])
    return signals


def test_dotted_release_pages_are_not_working_notes() -> None:
    signals = _indexed_section("releases", [f"release-2.{i}.1" for i in range(30)])
    assert classify_working_notes_trees(signals) == []


def test_v_prefixed_version_pages_are_not_working_notes() -> None:
    signals = _indexed_section("releases", [f"v1.{i}.0" for i in range(30)])
    assert classify_working_notes_trees(signals) == []


def test_titled_decision_records_are_not_working_notes() -> None:
    adrs = _indexed_section("docs/adr", [f"adr-{i:04d}-decision-{i}x" for i in range(1, 25)])
    rfcs = _indexed_section("docs/rfc", [f"rfc-{i:03d}-proposal" for i in range(1, 25)])
    assert classify_working_notes_trees(adrs) == []
    assert classify_working_notes_trees(rfcs) == []


@pytest.mark.parametrize(
    ("rel", "key"),
    [
        ("notes/plan_07.md", "plan"),
        ("notes/plan-07.md", "plan"),
        ("tickets/PROJ-123.md", "proj"),
        ("tickets/gh-42.md", "gh"),
        ("journal/2026-01-31-standup.md", "<date>"),
        ("releases/release-2.1.0.md", None),
        ("releases/v1.2.3.md", None),
        ("docs/adr/adr-0001-use-postgres.md", None),
        ("docs/rfc/rfc-042-streaming.md", None),
        ("docs/how-to/how-to-deploy.md", None),
        ("docs/adr/0001-use-postgres.md", None),
    ],
)
def test_name_key_families(rel: str, key: str | None) -> None:
    assert _name_key(rel) == key


def test_mixed_hyphen_series_count_as_separate_families() -> None:
    # plan-/spike-/retro-/audit- are four families: over the three-prefix
    # ceiling, so no three of them reach the name density.
    stems = [f"{w}-{i:02d}" for w in ("plan", "spike", "retro", "audit") for i in range(1, 7)]
    signals = _indexed_section("notes", stems)
    assert classify_working_notes_trees(signals) == []


def test_curated_sibling_citing_one_note_stays_in_headline() -> None:
    signals = _notes_tree("docs/notes", 50)
    signals["docs/notes/plan_01.md"] = _wn_signal(["docs/notes/backlog.md", "docs/guide.md"])
    signals["docs/guide.md"] = _wn_signal(["README.md"])
    trees = classify_working_notes_trees(signals)
    assert [t["path"] for t in trees] == ["docs/notes"]


def test_notes_split_by_period_keep_their_index_in_the_tree() -> None:
    signals = {
        f"notes/{y}/plan_{i:02d}.md": _wn_signal(["notes/backlog.md"])
        for y in (2025, 2026) for i in range(25)
    }
    signals["notes/backlog.md"] = _wn_signal(["README.md"])
    trees = classify_working_notes_trees(signals)
    assert [(t["path"], t["file_count"]) for t in trees] == [("notes", 51)]


def test_sparsely_linked_notes_pile_is_not_working_notes() -> None:
    # One stray link into an otherwise unlinked pile: the few edges that exist
    # are concentrated, but no index covers the tree, so it stays counted.
    signals = {f"notes/topic-{i}.md": _wn_signal([]) for i in range(1, 26)}
    signals["notes/topic-1.md"] = _wn_signal(["README.md"])
    assert classify_working_notes_trees(signals) == []
    signals["notes/topic-2.md"] = _wn_signal(["docs/guide.md"])
    assert classify_working_notes_trees(signals) == []


def test_non_absorbing_subdirectory_shields_its_curated_page() -> None:
    # docs/team refuses to absorb docs/team/notes because of onboarding.md;
    # docs must not then treat onboarding.md as nested and absorb it anyway.
    signals = {
        f"docs/team/notes/plan_{i:02d}.md": _wn_signal(["docs/index.md"]) for i in range(1, 51)
    }
    signals["docs/index.md"] = _wn_signal(["README.md"])
    signals["docs/team/onboarding.md"] = _wn_signal(["README.md"])
    trees = classify_working_notes_trees(signals)
    assert [(t["path"], t["file_count"]) for t in trees] == [("docs/team/notes", 50)]


def test_curated_subdirectory_below_the_floor_stays_counted() -> None:
    # docs/ holds 50 notes directly and docs/guides/ (9 pages and a README,
    # under the size floor, so it never qualifies). docs clears every leg over
    # all 61 docs, but the guides carry no name family and are no index into
    # the notes: only the notes and their index leave the headline.
    signals = {f"docs/plan_{i:02d}.md": _wn_signal(["docs/index.md"]) for i in range(1, 51)}
    signals["docs/index.md"] = _wn_signal(["README.md"])
    guides = "architecture billing caching deploy events glossary logging metrics onboarding".split()
    for g in guides:
        signals[f"docs/guides/{g}.md"] = _wn_signal(["docs/guides/README.md"])
    signals["docs/guides/README.md"] = _wn_signal(["docs/index.md"])
    trees = classify_working_notes_trees(signals)
    assert [(t["path"], t["file_count"]) for t in trees] == [("docs", 51)]
    assert not any(r.startswith("docs/guides/") for r in trees[0]["docs"])


def test_working_notes_thresholds_are_precision_first() -> None:
    assert WORKING_NOTES_MIN_FILES >= 10
    assert 0.5 < WORKING_NOTES_NAME_DENSITY <= 1.0
    assert 0.5 < WORKING_NOTES_LOW_INDEGREE_DENSITY <= 1.0
    assert 0.5 < WORKING_NOTES_INDEX_SHARE <= 1.0
