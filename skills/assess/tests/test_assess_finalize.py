"""End-to-end test for assess_finalize - the LLM write-back script."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from assess_finalize import FinalizeValidationError, finalize_run


def _seed_run_context(
    assess_dir: Path,
    *,
    denominator: int = 8,
    hotspots: tuple[str, ...] = ("src/foo.go",),
    mutation_run: bool = True,
    run_id: str | None = None,
) -> None:
    """Write a minimal valid run-context.json for finalize to reconcile against.

    finalize now reads run-context.json first and enforces invariants against it,
    so every finalize test seeds a matching context. Defaults produce a
    software-repo (denominator 8) context whose top hotspots and mutation state
    satisfy the invariants; individual tests override to exercise a violation.
    """
    ctx: dict = {
        "archetype": {"available": True, "denominator": denominator},
        "stats_summary": {
            "top_hotspots": [{"path": p} for p in hotspots],
        },
        "mutation_not_run_cap": {
            "applies": not mutation_run,
            "mutation_run": mutation_run,
            "max_layer6_band": "Present" if mutation_run else "Partial",
            "annotation": (
                None if mutation_run
                else "truth-pressure unproven (mutation not run)"
            ),
        },
    }
    if run_id is not None:
        ctx["run_id"] = run_id
    (assess_dir / "run-context.json").write_text(json.dumps(ctx), encoding="utf-8")


def _seed_log_md(assess_dir: Path) -> None:
    """Seed a log.md with one entry that has placeholders awaiting LLM fill."""
    (assess_dir / "log.md").write_text(
        "# Assess Log\n\n"
        "## 2026-05-22\n\n"
        "- **Files scored:** 100\n"
        "- **AI Readiness:** 0.0 / 8 ((LLM fills in))\n"
        "- **Instructions grade:** B+\n"
        "- **Hotspot transitions:** 1 graduated, 0 regressed, 0 new, 2 persistent\n"
        "- **Top action:** Deterministic ranker not yet wired (LLM picks Top 3)\n\n"
        "[Full report](./assess-report.md)\n\n"
        "---\n",
        encoding="utf-8",
    )


def _seed_hotspot_page(assess_dir: Path, slug: str) -> None:
    (assess_dir / "hotspots").mkdir(exist_ok=True)
    (assess_dir / "hotspots" / f"{slug}.md").write_text(
        "# Hotspot: `src/foo.go`\n\n"
        "## Suggested actions\n\n"
        "- Pending LLM-generated suggestions\n",
        encoding="utf-8",
    )


def test_finalize_updates_log_last_entry(tmp_assess_dir: Path) -> None:
    """The latest log.md entry gets its placeholders replaced with LLM-provided values."""
    _seed_log_md(tmp_assess_dir)
    _seed_run_context(tmp_assess_dir)
    finalize_input = {
        "score": 6.0,
        "maturity_label": "Solid",
        "top_action": "Add cyclop rule to .golangci.yml (threshold 15)",
        "hotspot_actions": {},
    }
    (tmp_assess_dir / "finalize-input.json").write_text(
        json.dumps(finalize_input), encoding="utf-8"
    )

    finalize_run(assess_dir=tmp_assess_dir)
    content = (tmp_assess_dir / "log.md").read_text(encoding="utf-8")
    assert "AI Readiness:** 6.0 / 8 (Solid)" in content
    assert "Top action:** Add cyclop rule to .golangci.yml (threshold 15)" in content
    # Placeholders must be gone
    assert "((LLM fills in))" not in content
    assert "Deterministic ranker not yet wired" not in content


def test_finalize_knowledge_base_denominator(tmp_assess_dir: Path) -> None:
    """A KB run finalises the log + badge over its applicable-layer denominator (#224)."""
    _seed_log_md(tmp_assess_dir)
    _seed_run_context(tmp_assess_dir, denominator=3)
    finalize_input = {
        "score": 2.5,
        "maturity_label": "Knowledge Base · Solid (3 applicable layers)",
        "denominator": 3,
        "top_action": "Document the KB maintenance workflow in CLAUDE.md",
        "hotspot_actions": {},
    }
    (tmp_assess_dir / "finalize-input.json").write_text(
        json.dumps(finalize_input), encoding="utf-8"
    )

    finalize_run(assess_dir=tmp_assess_dir)
    content = (tmp_assess_dir / "log.md").read_text(encoding="utf-8")
    assert "AI Readiness:** 2.5 / 3 (Knowledge Base · Solid (3 applicable layers))" in content
    assert "/ 8" not in content  # the misleading software denominator is gone
    badge = json.loads((tmp_assess_dir / "badge.json").read_text(encoding="utf-8"))
    assert badge["message"].startswith("2.5/3 · Knowledge Base")


def test_finalize_updates_hotspot_actions(tmp_assess_dir: Path) -> None:
    """Hotspot pages get their 'Suggested actions' section rewritten with LLM input."""
    # The slug for "src/foo.go" includes a sha256[:8] hash - use the same function the script uses
    from lib.wiki_writer import slug_for_path
    slug = slug_for_path("src/foo.go")
    _seed_hotspot_page(tmp_assess_dir, slug=slug)
    finalize_input = {
        "score": 6.0,
        "maturity_label": "Solid",
        "top_action": "x",
        "hotspot_actions": {
            "src/foo.go": [
                "Split parseLine into smaller functions",
                "Add a test file at src/foo_test.go",
            ],
        },
    }
    (tmp_assess_dir / "finalize-input.json").write_text(
        json.dumps(finalize_input), encoding="utf-8"
    )
    # Need a log.md too since finalize_run does both
    _seed_log_md(tmp_assess_dir)
    _seed_run_context(tmp_assess_dir)

    finalize_run(assess_dir=tmp_assess_dir)
    page = (tmp_assess_dir / "hotspots" / f"{slug}.md").read_text(encoding="utf-8")
    assert "Split parseLine" in page
    assert "Add a test file at src/foo_test.go" in page
    assert "Pending LLM-generated suggestions" not in page


def test_finalize_missing_input_raises(tmp_assess_dir: Path) -> None:
    """If finalize-input.json doesn't exist, raise a clear error."""
    with pytest.raises(FileNotFoundError):
        finalize_run(assess_dir=tmp_assess_dir)


def test_finalize_hotspot_without_match_is_skipped(tmp_assess_dir: Path) -> None:
    """An entry in hotspot_actions whose page doesn't exist is silently skipped.

    This is forward-compatible with the path lifecycle: a hotspot might graduate
    between when the LLM read the data and when finalize runs.
    """
    _seed_log_md(tmp_assess_dir)
    # The referenced path IS a real top hotspot in run-context (so it clears the
    # fabrication invariant); its wiki page simply doesn't exist, which is the
    # lifecycle case this test covers (graduated between LLM read and finalize).
    _seed_run_context(tmp_assess_dir, hotspots=("src/nonexistent.go",))
    finalize_input = {
        "score": 6.0,
        "maturity_label": "Solid",
        "top_action": "x",
        "hotspot_actions": {"src/nonexistent.go": ["something"]},
    }
    (tmp_assess_dir / "finalize-input.json").write_text(
        json.dumps(finalize_input), encoding="utf-8"
    )

    # Should not raise
    finalize_run(assess_dir=tmp_assess_dir)


def test_finalize_log_handles_backslash_in_top_action(tmp_assess_dir: Path) -> None:
    """Top action text containing \\1 must be inserted literally, not as a regex backreference."""
    _seed_log_md(tmp_assess_dir)
    _seed_run_context(tmp_assess_dir)
    finalize_input = {
        "score": 6.0,
        "maturity_label": "Solid",
        # Adversarial: contains \1 which would be a re.sub backreference if not handled
        "top_action": r"Replace `\1` capture group references in regexes",
        "hotspot_actions": {},
    }
    (tmp_assess_dir / "finalize-input.json").write_text(
        json.dumps(finalize_input), encoding="utf-8"
    )

    finalize_run(assess_dir=tmp_assess_dir)
    content = (tmp_assess_dir / "log.md").read_text(encoding="utf-8")
    assert r"Replace `\1` capture group references" in content


def test_finalize_hotspot_action_handles_backslash(tmp_assess_dir: Path) -> None:
    """Hotspot action text containing \\1 must be inserted literally."""
    from lib.wiki_writer import slug_for_path
    slug = slug_for_path("src/foo.go")
    _seed_hotspot_page(tmp_assess_dir, slug=slug)
    _seed_log_md(tmp_assess_dir)
    _seed_run_context(tmp_assess_dir)
    finalize_input = {
        "score": 6.0,
        "maturity_label": "Solid",
        "top_action": "x",
        "hotspot_actions": {
            "src/foo.go": [
                r"Use \1 to denote first capture group",
                "Add tests",
            ],
        },
    }
    (tmp_assess_dir / "finalize-input.json").write_text(
        json.dumps(finalize_input), encoding="utf-8"
    )

    finalize_run(assess_dir=tmp_assess_dir)
    page = (tmp_assess_dir / "hotspots" / f"{slug}.md").read_text(encoding="utf-8")
    assert r"Use \1 to denote first capture group" in page


def test_finalize_reads_from_cache_path(tmp_assess_dir: Path) -> None:
    """The preferred input location is `.assess/.cache/finalize-input.json`."""
    _seed_log_md(tmp_assess_dir)
    _seed_run_context(tmp_assess_dir)
    cache_dir = tmp_assess_dir / ".cache"
    cache_dir.mkdir()
    (cache_dir / "finalize-input.json").write_text(
        json.dumps({
            "score": 7.0, "maturity_label": "AI-Native",
            "top_action": "x", "hotspot_actions": {},
        }),
        encoding="utf-8",
    )

    finalize_run(assess_dir=tmp_assess_dir)
    content = (tmp_assess_dir / "log.md").read_text(encoding="utf-8")
    assert "AI Readiness:** 7.0 / 8 (AI-Native)" in content


def test_finalize_deletes_input_after_success(tmp_assess_dir: Path) -> None:
    """The input file is one-off: delete it on success so it can't leak into commits."""
    _seed_log_md(tmp_assess_dir)
    _seed_run_context(tmp_assess_dir)
    legacy = tmp_assess_dir / "finalize-input.json"
    legacy.write_text(
        json.dumps({
            "score": 6.0, "maturity_label": "Solid",
            "top_action": "x", "hotspot_actions": {},
        }),
        encoding="utf-8",
    )

    finalize_run(assess_dir=tmp_assess_dir)
    assert not legacy.exists()


def test_finalize_cleans_up_legacy_when_cache_present(tmp_assess_dir: Path) -> None:
    """If both cache and legacy paths exist (e.g. an older run left a stale
    legacy file), finalize prefers cache and cleans up both.
    """
    _seed_log_md(tmp_assess_dir)
    _seed_run_context(tmp_assess_dir)
    cache_dir = tmp_assess_dir / ".cache"
    cache_dir.mkdir()
    cache_path = cache_dir / "finalize-input.json"
    cache_path.write_text(
        json.dumps({
            "score": 7.0, "maturity_label": "AI-Native",
            "top_action": "current", "hotspot_actions": {},
        }),
        encoding="utf-8",
    )
    legacy = tmp_assess_dir / "finalize-input.json"
    legacy.write_text(
        json.dumps({
            "score": 3.0, "maturity_label": "Basic",
            "top_action": "stale", "hotspot_actions": {},
        }),
        encoding="utf-8",
    )

    finalize_run(assess_dir=tmp_assess_dir)
    # Cache wins (the value the report just published).
    content = (tmp_assess_dir / "log.md").read_text(encoding="utf-8")
    assert "AI-Native" in content
    # Both files cleaned up.
    assert not cache_path.exists()
    assert not legacy.exists()


def test_finalize_updates_last_entry_when_older_entry_unfinalized(tmp_assess_dir: Path) -> None:
    """When an older log entry still has placeholders, finalize updates the LATEST.

    Log entries are appended (newest at bottom). If we finalized the first match,
    we'd overwrite stale historical data and leave the new run unfilled.
    The older unfinalized entry stays as evidence of "this run wasn't finalized."
    """
    _seed_run_context(tmp_assess_dir)
    # Seed two entries: older (unfinalized, placeholders intact) then newer (also placeholders)
    (tmp_assess_dir / "log.md").write_text(
        "# Assess Log\n\n"
        "## 2026-05-01\n\n"
        "- **Files scored:** 80\n"
        "- **AI Readiness:** 0.0 / 8 ((LLM fills in))\n"
        "- **Top action:** Deterministic ranker not yet wired (LLM picks Top 3)\n\n"
        "---\n\n"
        "## 2026-05-22\n\n"
        "- **Files scored:** 100\n"
        "- **AI Readiness:** 0.0 / 8 ((LLM fills in))\n"
        "- **Top action:** Deterministic ranker not yet wired (LLM picks Top 3)\n\n"
        "---\n",
        encoding="utf-8",
    )
    finalize_input = {
        "score": 6.5,
        "maturity_label": "Solid",
        "top_action": "Add cyclop rule (threshold 15) to .golangci.yml",
        "hotspot_actions": {},
    }
    (tmp_assess_dir / "finalize-input.json").write_text(
        json.dumps(finalize_input), encoding="utf-8"
    )

    finalize_run(assess_dir=tmp_assess_dir)
    content = (tmp_assess_dir / "log.md").read_text(encoding="utf-8")

    # The 2026-05-22 (latest) entry got filled
    latest_section = content.split("## 2026-05-22")[1]
    assert "AI Readiness:** 6.5 / 8 (Solid)" in latest_section
    assert "Top action:** Add cyclop rule (threshold 15) to .golangci.yml" in latest_section

    # The 2026-05-01 (older) entry's placeholders are PRESERVED as historical evidence
    older_section = content.split("## 2026-05-22")[0]
    assert "AI Readiness:** 0.0 / 8 ((LLM fills in))" in older_section
    assert "Deterministic ranker not yet wired" in older_section


def _base_input() -> dict:
    return {
        "score": 6.0,
        "maturity_label": "Solid",
        "top_action": "Add cyclop rule",
        "hotspot_actions": {},
    }


def _good_action(rank: int = 1) -> dict:
    return {
        "rank": rank,
        "action": "Add cyclop rule (threshold 15) to .golangci.yml",
        "layer": 3,
        "effort": "small",
        "files": [".golangci.yml"],
        "first_step": "Add cyclop under linters",
        "done_when": "golangci-lint run passes with the rule active",
        "scope_fence": "Only .golangci.yml; no source edits",
    }


def _distinct_action(rank: int, *, finding: str | None = None) -> dict:
    """A good action with a rank-unique directive (the carry-forward identity)."""
    a = {**_good_action(rank=rank), "action": f"Action number {rank}"}
    if finding is not None:
        a["finding"] = finding
    return a


def test_finalize_writes_actions_contract(tmp_assess_dir: Path) -> None:
    """An `actions` array in the input becomes the durable .assess/actions.json,
    sorted by rank, with the executor-critical fields intact (v2 schema)."""
    _seed_log_md(tmp_assess_dir)
    _seed_run_context(tmp_assess_dir, run_id="run-abc")
    finalize_input = {
        **_base_input(),
        "actions": [_distinct_action(rank=2), _distinct_action(rank=1)],
    }
    (tmp_assess_dir / "finalize-input.json").write_text(
        json.dumps(finalize_input), encoding="utf-8"
    )

    finalize_run(assess_dir=tmp_assess_dir)

    contract = json.loads(
        (tmp_assess_dir / "actions.json").read_text(encoding="utf-8")
    )
    assert contract["schema"] == 2
    assert contract["run_id"] == "run-abc"
    assert [a["rank"] for a in contract["actions"]] == [1, 2]
    for a in contract["actions"]:
        assert a["done_when"]
        assert a["scope_fence"]
        # v2 lifecycle fields present and initialised for a first run.
        assert a["status"] == "pending"
        assert a["claimed_by"] is None
        assert a["completed_sha"] is None
        assert a["mode"] in {
            "characterize_first", "verify_then_retire", "refactor_safe",
        }


def test_finalize_derives_mode_from_finding(tmp_assess_dir: Path) -> None:
    """`mode` is derived deterministically from each action's finding type; an
    action with no finding falls back to the conservative characterize_first."""
    _seed_log_md(tmp_assess_dir)
    _seed_run_context(tmp_assess_dir)
    finalize_input = {
        **_base_input(),
        "actions": [
            _distinct_action(rank=1, finding="lying_map"),
            _distinct_action(rank=2, finding="refactor_boundary"),
            _distinct_action(rank=3),  # no finding -> default
        ],
    }
    (tmp_assess_dir / "finalize-input.json").write_text(
        json.dumps(finalize_input), encoding="utf-8"
    )

    finalize_run(assess_dir=tmp_assess_dir)

    by_rank = {
        a["rank"]: a
        for a in json.loads(
            (tmp_assess_dir / "actions.json").read_text(encoding="utf-8")
        )["actions"]
    }
    assert by_rank[1]["mode"] == "verify_then_retire"
    assert by_rank[2]["mode"] == "refactor_safe"
    assert by_rank[3]["mode"] == "characterize_first"


def test_finalize_carries_status_across_runs(tmp_assess_dir: Path) -> None:
    """A done action stays done with its completed_sha on re-run - status,
    claimed_by, and completed_sha are carried forward by the action directive."""
    _seed_log_md(tmp_assess_dir)
    _seed_run_context(tmp_assess_dir)
    # Run 1: writes a pending contract.
    (tmp_assess_dir / "finalize-input.json").write_text(
        json.dumps({**_base_input(), "actions": [_distinct_action(rank=1)]}),
        encoding="utf-8",
    )
    finalize_run(assess_dir=tmp_assess_dir)

    # An executor marks the action done out of band.
    contract_path = tmp_assess_dir / "actions.json"
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    contract["actions"][0].update(
        status="done", claimed_by="agent-7", completed_sha="deadbeef",
    )
    contract_path.write_text(json.dumps(contract), encoding="utf-8")

    # Run 2: the same action re-appears (rank changed) - it must stay done.
    # Same directive text (the carry-forward key), new rank.
    _seed_log_md(tmp_assess_dir)
    reranked = {**_distinct_action(rank=1), "rank": 3}
    (tmp_assess_dir / "finalize-input.json").write_text(
        json.dumps({**_base_input(), "actions": [reranked]}),
        encoding="utf-8",
    )
    finalize_run(assess_dir=tmp_assess_dir)

    after = json.loads(contract_path.read_text(encoding="utf-8"))["actions"][0]
    assert after["status"] == "done"
    assert after["claimed_by"] == "agent-7"
    assert after["completed_sha"] == "deadbeef"
    assert after["rank"] == 3  # rank is recomputed, not carried


def test_finalize_reads_v1_actions_for_carry_forward(tmp_assess_dir: Path) -> None:
    """A pre-existing v1 actions.json (no lifecycle fields) is read without
    error; the re-run upgrades it to v2 with every action initialised pending."""
    _seed_log_md(tmp_assess_dir)
    _seed_run_context(tmp_assess_dir)
    # A v1-shaped contract on disk from before this schema existed.
    (tmp_assess_dir / "actions.json").write_text(
        json.dumps({
            "schema": 1,
            "actions": [{
                "rank": 1,
                "action": "Action number 1",
                "done_when": "x",
                "scope_fence": "y",
            }],
        }),
        encoding="utf-8",
    )
    (tmp_assess_dir / "finalize-input.json").write_text(
        json.dumps({**_base_input(), "actions": [_distinct_action(rank=1)]}),
        encoding="utf-8",
    )

    finalize_run(assess_dir=tmp_assess_dir)

    contract = json.loads(
        (tmp_assess_dir / "actions.json").read_text(encoding="utf-8")
    )
    assert contract["schema"] == 2
    entry = contract["actions"][0]
    assert entry["status"] == "pending"
    assert entry["claimed_by"] is None
    assert entry["completed_sha"] is None


def test_finalize_without_actions_writes_no_contract(tmp_assess_dir: Path) -> None:
    """Backwards compatibility: an input with no `actions` key (the pre-1.41
    shape) finalizes the wiki exactly as before and writes no actions.json."""
    _seed_log_md(tmp_assess_dir)
    _seed_run_context(tmp_assess_dir)
    (tmp_assess_dir / "finalize-input.json").write_text(
        json.dumps(_base_input()), encoding="utf-8"
    )

    finalize_run(assess_dir=tmp_assess_dir)

    assert not (tmp_assess_dir / "actions.json").exists()
    content = (tmp_assess_dir / "log.md").read_text(encoding="utf-8")
    assert "AI Readiness:** 6.0 / 8 (Solid)" in content


def test_finalize_drops_malformed_action_entries(tmp_assess_dir: Path) -> None:
    """An entry missing done_when/scope_fence is dropped (a malformed contract
    must not reach an executor as if complete); valid siblings still land."""
    _seed_log_md(tmp_assess_dir)
    _seed_run_context(tmp_assess_dir)
    incomplete = {"rank": 2, "action": "vague intention"}  # no done_when/fence
    finalize_input = {
        **_base_input(),
        "actions": [_good_action(rank=1), incomplete],
    }
    (tmp_assess_dir / "finalize-input.json").write_text(
        json.dumps(finalize_input), encoding="utf-8"
    )

    finalize_run(assess_dir=tmp_assess_dir)

    contract = json.loads(
        (tmp_assess_dir / "actions.json").read_text(encoding="utf-8")
    )
    assert len(contract["actions"]) == 1
    assert contract["actions"][0]["rank"] == 1


def test_finalize_all_actions_malformed_writes_no_contract(tmp_assess_dir: Path) -> None:
    _seed_log_md(tmp_assess_dir)
    _seed_run_context(tmp_assess_dir)
    finalize_input = {**_base_input(), "actions": [{"rank": 1}]}
    (tmp_assess_dir / "finalize-input.json").write_text(
        json.dumps(finalize_input), encoding="utf-8"
    )

    finalize_run(assess_dir=tmp_assess_dir)

    assert not (tmp_assess_dir / "actions.json").exists()


def test_finalize_writes_score_badge(tmp_assess_dir: Path) -> None:
    """Finalize always (over)writes badge.json with the LLM-scored form."""
    _seed_log_md(tmp_assess_dir)
    _seed_run_context(tmp_assess_dir)
    (tmp_assess_dir / "badge.json").write_text(
        '{"schemaVersion": 1, "label": "AI-readiness", '
        '"message": "9 findings · 9 stale markers", "color": "orange"}',
        encoding="utf-8",
    )
    (tmp_assess_dir / "finalize-input.json").write_text(
        json.dumps(_base_input()), encoding="utf-8"
    )

    finalize_run(assess_dir=tmp_assess_dir)

    badge = json.loads((tmp_assess_dir / "badge.json").read_text(encoding="utf-8"))
    assert badge["message"] == "6.0/8 · Solid"
    assert badge["color"] == "green"


# --- Task 1: finalize reconciles run-context invariants (fail-closed) --------


def _write_input(assess_dir: Path, data: dict) -> None:
    (assess_dir / "finalize-input.json").write_text(json.dumps(data), encoding="utf-8")


def test_finalize_missing_run_context_refuses(tmp_assess_dir: Path) -> None:
    """A present input but no run-context.json is a hard, named failure - there
    is nothing to reconcile against, so finalize refuses and writes nothing."""
    _seed_log_md(tmp_assess_dir)  # note: no _seed_run_context
    _write_input(tmp_assess_dir, _base_input())

    with pytest.raises(FinalizeValidationError, match="run-context.json missing"):
        finalize_run(assess_dir=tmp_assess_dir)

    # Nothing written: the log placeholder survives untouched.
    content = (tmp_assess_dir / "log.md").read_text(encoding="utf-8")
    assert "((LLM fills in))" in content
    assert not (tmp_assess_dir / "actions.json").exists()


def test_finalize_denominator_mismatch_refuses(tmp_assess_dir: Path) -> None:
    """The input denominator must match run-context archetype.denominator."""
    _seed_log_md(tmp_assess_dir)
    _seed_run_context(tmp_assess_dir, denominator=3)  # KB
    _write_input(tmp_assess_dir, {**_base_input(), "denominator": 8})

    with pytest.raises(FinalizeValidationError, match="denominator mismatch"):
        finalize_run(assess_dir=tmp_assess_dir)


def test_finalize_score_exceeds_denominator_refuses(tmp_assess_dir: Path) -> None:
    """A score above its denominator is impossible - refuse."""
    _seed_log_md(tmp_assess_dir)
    _seed_run_context(tmp_assess_dir)
    _write_input(
        tmp_assess_dir,
        {**_base_input(), "score": 9.0, "maturity_label": "AI-Native"},
    )

    with pytest.raises(FinalizeValidationError, match="exceeds denominator"):
        finalize_run(assess_dir=tmp_assess_dir)


def test_finalize_maturity_inconsistent_with_score_refuses(tmp_assess_dir: Path) -> None:
    """A label that overstates the score band (2.0/8 called 'AI-Native') is a
    lying self-description - refuse, naming the expected tier."""
    _seed_log_md(tmp_assess_dir)
    _seed_run_context(tmp_assess_dir)
    _write_input(
        tmp_assess_dir,
        {**_base_input(), "score": 2.0, "maturity_label": "AI-Native"},
    )

    with pytest.raises(FinalizeValidationError, match="claims tier 'AI-Native'"):
        finalize_run(assess_dir=tmp_assess_dir)


def test_finalize_valid_maturity_bands_pass(tmp_assess_dir: Path) -> None:
    """The documented ladder is accepted verbatim: each score earns its label."""
    for score, label in [
        (7.0, "AI-Native"),   # 0.875
        (6.0, "Solid"),       # 0.75
        (3.0, "Basic"),       # 0.375
        (1.0, "Not Ready"),   # 0.125
    ]:
        _seed_log_md(tmp_assess_dir)
        _seed_run_context(tmp_assess_dir)
        _write_input(
            tmp_assess_dir,
            {**_base_input(), "score": score, "maturity_label": label},
        )
        finalize_run(assess_dir=tmp_assess_dir)  # must not raise


def test_finalize_fabricated_hotspot_path_refuses(tmp_assess_dir: Path) -> None:
    """A hotspot_actions key absent from run-context top_hotspots is fabricated;
    the error names the offending path."""
    _seed_log_md(tmp_assess_dir)
    _seed_run_context(tmp_assess_dir, hotspots=("src/foo.go",))
    _write_input(
        tmp_assess_dir,
        {**_base_input(), "hotspot_actions": {"src/invented.go": ["do a thing"]}},
    )

    with pytest.raises(FinalizeValidationError, match=r"src/invented\.go"):
        finalize_run(assess_dir=tmp_assess_dir)


def test_finalize_writes_nothing_on_violation(tmp_assess_dir: Path) -> None:
    """A violation short-circuits before any write: no badge, no actions.json,
    log placeholders intact, and the input file is NOT consumed (so a fixed
    rerun can still find it)."""
    _seed_log_md(tmp_assess_dir)
    _seed_run_context(tmp_assess_dir)
    bad = {**_base_input(), "score": 2.0, "maturity_label": "AI-Native",
           "actions": [_good_action(rank=1)]}
    _write_input(tmp_assess_dir, bad)

    with pytest.raises(FinalizeValidationError):
        finalize_run(assess_dir=tmp_assess_dir)

    assert "((LLM fills in))" in (tmp_assess_dir / "log.md").read_text(encoding="utf-8")
    assert not (tmp_assess_dir / "actions.json").exists()
    assert not (tmp_assess_dir / "badge.json").exists()
    assert (tmp_assess_dir / "finalize-input.json").exists()  # not consumed


# --- Task 2: run_id torn-write detection -------------------------------------


def test_finalize_run_id_mismatch_refuses(tmp_assess_dir: Path) -> None:
    """finalize-input and run-context from different runs (mismatched run_id) is
    a torn write - refuse."""
    _seed_log_md(tmp_assess_dir)
    _seed_run_context(tmp_assess_dir, run_id="20260707120000-aaaaaaaa")
    _write_input(tmp_assess_dir, {**_base_input(), "run_id": "20260707130000-bbbbbbbb"})

    with pytest.raises(FinalizeValidationError, match="torn write"):
        finalize_run(assess_dir=tmp_assess_dir)


def test_finalize_run_id_match_stamps_badge(tmp_assess_dir: Path) -> None:
    """Matching run_id passes, and the badge is stamped with it."""
    _seed_log_md(tmp_assess_dir)
    run_id = "20260707120000-abcdef01"
    _seed_run_context(tmp_assess_dir, run_id=run_id)
    _write_input(tmp_assess_dir, {**_base_input(), "run_id": run_id})

    finalize_run(assess_dir=tmp_assess_dir)

    badge = json.loads((tmp_assess_dir / "badge.json").read_text(encoding="utf-8"))
    assert badge["run_id"] == run_id


def test_finalize_legacy_input_without_run_id_still_works(tmp_assess_dir: Path) -> None:
    """A legacy input carrying no run_id finalises even when run-context has one
    (backward compat: the torn-write check needs both stamps to fire)."""
    _seed_log_md(tmp_assess_dir)
    _seed_run_context(tmp_assess_dir, run_id="20260707120000-abcdef01")
    _write_input(tmp_assess_dir, _base_input())  # no run_id

    finalize_run(assess_dir=tmp_assess_dir)  # must not raise
    assert "AI Readiness:** 6.0 / 8 (Solid)" in (
        tmp_assess_dir / "log.md"
    ).read_text(encoding="utf-8")


# --- Task 8: Layer 6 capped at Partial when mutation never ran ---------------


def test_finalize_layer6_present_without_mutation_refuses(tmp_assess_dir: Path) -> None:
    """Mutation never ran + LLM scores Layer 6 Present (1.0) -> finalize rejects,
    naming the required annotation."""
    _seed_log_md(tmp_assess_dir)
    _seed_run_context(tmp_assess_dir, mutation_run=False)
    _write_input(tmp_assess_dir, {**_base_input(), "layer_scores": {"6": 1.0}})

    with pytest.raises(
        FinalizeValidationError,
        match="truth-pressure unproven",
    ):
        finalize_run(assess_dir=tmp_assess_dir)


def test_finalize_layer6_present_with_mutation_allowed(tmp_assess_dir: Path) -> None:
    """When mutation actually ran, a Present Layer 6 is allowed."""
    _seed_log_md(tmp_assess_dir)
    _seed_run_context(tmp_assess_dir, mutation_run=True)
    _write_input(tmp_assess_dir, {**_base_input(), "layer_scores": {"6": 1.0}})

    finalize_run(assess_dir=tmp_assess_dir)  # must not raise


def test_finalize_layer6_partial_without_mutation_allowed(tmp_assess_dir: Path) -> None:
    """Partial (0.5) is the ceiling when mutation didn't run - allowed."""
    _seed_log_md(tmp_assess_dir)
    _seed_run_context(tmp_assess_dir, mutation_run=False)
    _write_input(tmp_assess_dir, {**_base_input(), "layer_scores": {"6": 0.5}})

    finalize_run(assess_dir=tmp_assess_dir)  # must not raise
