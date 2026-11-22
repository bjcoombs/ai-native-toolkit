"""Tests for the doc-drift map and the review strip (issue #943): lib/assess_diagrams_review.py.

The goldens under ``tests/fixtures/assess_diagrams/`` lock the whole render; the semantic
tests read the bars and ticks back out of the SVG and hold them to the run-context blocks.
Regenerate the goldens with ``uv run --locked python tests/assess_diagrams_review_fixture.py``
from ``skills/assess``.
"""
from __future__ import annotations

import copy
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import pytest

from assess_core import build_and_publish_run
from assess_diagrams_review_fixture import CTX, DOC_STALENESS, DOCS, FIXTURES, REVIEW, goldens
from assess_report import render_report
from lib import assess_diagrams as ad
from lib import assess_diagrams_review as rd
from lib.counts_only import counts_only
from lib.review_reality import summarize
from lib.vocabulary import IN_PLACE, banned_hits
from svg_cascade import overridden_text_attributes
from test_decomposition_parity import _build_parity_fixture

SVG_NS = "{http://www.w3.org/2000/svg}"
_TEXT = re.compile(r"<(text|title|desc)\b[^>]*>(.*?)</\1>", re.S)


def _svg(ctx: dict[str, Any], file: str) -> str:
    [diagram] = [d for d in rd.review_diagrams(ctx) if d["file"] == file]
    assert diagram["svg"] is not None, diagram["skipped"]
    return diagram["svg"]


def _rects(content: str, attr: str) -> list[ET.Element]:
    root = ET.fromstring(content.encode("utf-8"))
    return [r for r in root.iter(f"{SVG_NS}rect") if attr in r.attrib]


def _bots(content: str) -> int:
    root = ET.fromstring(content.encode("utf-8"))
    return sum(1 for c in root.iter(f"{SVG_NS}circle") if c.attrib.get("data-bot") == "true")


def _prose(content: str) -> str:
    return "\n".join(re.sub(r"<[^>]+>", " ", body) for _, body in _TEXT.findall(content))


# --- goldens and determinism ----------------------------------------------------


@pytest.mark.parametrize("name", sorted(goldens()))
def test_render_matches_golden(name: str) -> None:
    assert goldens()[name] == (FIXTURES / name).read_text(encoding="utf-8")


def test_two_renders_are_byte_identical() -> None:
    assert rd.review_diagrams(copy.deepcopy(CTX)) == rd.review_diagrams(copy.deepcopy(CTX))


@pytest.mark.parametrize("file", rd.REVIEW_FILES)
def test_stylesheet_never_overrides_a_text_attribute(file: str) -> None:
    assert overridden_text_attributes(_svg(CTX, file)) == []


# --- doc drift ------------------------------------------------------------------


def test_drift_order_is_the_doc_staleness_order_top_15() -> None:
    bars = _rects(_svg(CTX, rd.DRIFT_FILE), "data-ratio")
    mapped = [d for d in DOCS if d["subject_method"] != "repo-baseline"]
    assert len(mapped) > rd.DRIFT_TOP and len(bars) == rd.DRIFT_TOP
    assert [float(b.attrib["data-ratio"]) for b in bars] == [d["ratio"] for d in mapped[:rd.DRIFT_TOP]]
    titles = [b.find(f"{SVG_NS}title").text or "" for b in bars]  # type: ignore[union-attr]
    assert [t.rsplit(":", 1)[0] for t in titles] == [d["path"] for d in mapped[:rd.DRIFT_TOP]]


def test_drift_keeps_the_block_order_even_when_it_is_not_sorted() -> None:
    docs = [DOCS[3], DOCS[0], DOCS[2]]
    bars = rd.drift_bars({"docs": docs})
    assert [b["path"] for b in bars] == [d["path"] for d in docs]


def test_drift_states_the_remainder_and_counts_baseline_docs_in_the_caption() -> None:
    content = _svg(CTX, rd.DRIFT_FILE)
    assert "And 3 more mapped docs, not drawn" in content
    assert "baseline/OVERVIEW.md" not in content and "CONTRIBUTING.md" not in content
    assert "2 docs measured against the whole repository are counted here, not drawn." in content
    [entry, _] = rd.review_diagrams(CTX)
    assert "2 docs measured against the whole repository" in entry["caption"]


def test_drift_behind_matches_the_staleness_test() -> None:
    bars = {b.attrib["data-ratio"]: b.attrib["data-behind"] for b in _rects(_svg(CTX, rd.DRIFT_FILE), "data-ratio")}
    assert bars["14"] == "true" and bars["2"] == "false" and bars["2.5"] == "true"
    generated = rd.drift_bars({"docs": [DOCS[-3]]})
    assert generated[0]["behind"] is True and generated[0]["ratio"] == 0.0  # a newer declared source


def test_drift_labels_name_the_subject_root_and_the_rest() -> None:
    shared = rd.drift_bars({"docs": [DOCS[-2]]})[0]
    assert rd._subject_label(shared) == "describes lib/a and 2 more"
    assert rd._subject_label({**shared, "subject": "", "more_roots": 0}) == "describes its linked code"


@pytest.mark.parametrize("block, reason", [
    (None, "the run has no doc-freshness data."),
    ({"available": False, "reason": "scan_failed: boom"}, "not measured here: scan did not complete."),
    ({**DOC_STALENESS, "history_truncated": True}, "the clone is shallow"),
    ({**DOC_STALENESS, "churn_degenerate": True}, "about one commit per file"),
    ({**DOC_STALENESS, "docs": [DOCS[1], DOCS[-1]]}, "no doc maps to code of its own; 2 docs"),
    ({**DOC_STALENESS, "docs": []}, "the repository has no docs to measure."),
    ({**DOC_STALENESS, "docs": [{"path": "a.md", "ratio": "odd", "subject_method": "base-doc"}]},
     "the renderer raised ValueError."),
])
def test_drift_not_drawn_says_why(block: object, reason: str) -> None:
    ctx = {} if block is None else {"doc_staleness": block}
    drift = rd.review_diagrams(ctx)[0]
    assert drift["svg"] is None and reason in (drift["skipped"] or "")
    section = ad.diagrams_section([drift])
    assert f"Not drawn: {drift['skipped']}" in section and section.splitlines()[2].startswith(IN_PLACE)


def test_drift_labels_are_hashed_under_counts_only() -> None:
    hashed = counts_only(CTX, hash_paths=True, salt="fixture-salt")
    content = _svg(hashed, rd.DRIFT_FILE)
    for doc in DOCS:
        assert doc["path"] not in content
        for root in doc["subject_roots"]:
            assert f"describes {root}" not in content
    assert "describes h:" in content
    full = _svg(CTX, rd.DRIFT_FILE)
    for attr in ("data-ratio", "data-behind"):
        assert [b.attrib[attr] for b in _rects(content, attr)] == [b.attrib[attr] for b in _rects(full, attr)]


# --- review strip ---------------------------------------------------------------


def _ticks(content: str) -> list[str]:
    return [r.attrib["data-state"] for r in _rects(content, "data-state")]


def test_strip_ticks_are_the_sequence_in_merge_order() -> None:
    content = _svg(CTX, rd.STRIP_FILE)
    assert _ticks(content) == REVIEW["sequence"]
    assert _bots(content) == REVIEW["bot_sequence"].count("bot")
    unknown = [r for r in _rects(content, "data-state") if r.attrib["data-state"] == "unknown"]
    assert [r.attrib["fill"] for r in unknown] == ["url(#fleet-hatch)"]


def _pr(n: int, reviews: list[tuple[str, str]], bot: bool = False) -> dict[str, Any]:
    return {"number": n, "title": f"SECRET-TITLE-{n}", "author": {"login": "alice-login"},
            "mergedBy": {"login": "alice-login"}, "mergedAt": f"2026-09-{n + 1:02d}T00:00:00Z",
            "reviews": [{"author": {"login": who}, "state": state} for who, state in reviews],
            "comments": [{"author": {"login": "review-bot-login", "is_bot": True}}] if bot else []}


class _Bots:
    def is_bot(self, actor: object) -> bool:
        return isinstance(actor, dict) and bool(actor.get("is_bot"))


def test_strip_counts_equal_the_github_shares_and_bot_only_is_unreviewed() -> None:
    prs = [_pr(4, [("bob-login", "APPROVED")]), _pr(3, [("bob-login", "COMMENTED")]),
           _pr(2, [], bot=True), _pr(1, []), _pr(0, [("alice-login", "APPROVED")], bot=True)]
    block = summarize(prs, True, bots=_Bots())  # type: ignore[arg-type]
    content = _svg({"review_reality": block}, rd.STRIP_FILE)
    ticks = _ticks(content)
    total = block["merged_count"]
    assert round(ticks.count("approved") / total, 3) == block["approved_share"]
    assert round((ticks.count("approved") + ticks.count("reviewed")) / total, 3) == block["reviewed_share"]
    # Oldest first: the author's own approval and the bot-only change read unreviewed, marked.
    assert ticks == ["unreviewed", "unreviewed", "unreviewed", "reviewed", "approved"]
    assert block["bot_sequence"] == ["bot", "none", "bot", "none", "none"] and _bots(content) == 2
    assert block["bot_review_share"] == 0.4


def test_strip_unknown_tick_keeps_reviewed_share_none() -> None:
    content = _svg(CTX, rd.STRIP_FILE)
    assert REVIEW["reviewed_share"] is None
    assert _ticks(content).count("approved") / REVIEW["merged_count"] == REVIEW["approved_share"]
    assert "Not known: the platform left it undecided (1)" in content


def test_no_identity_reaches_the_run_context_or_any_svg() -> None:
    prs = [_pr(0, [("bob-login", "APPROVED")], bot=True), _pr(1, [])]
    block = summarize(prs, True, bots=_Bots())  # type: ignore[arg-type]
    texts = [json.dumps(block), *(d["svg"] or "" for d in rd.review_diagrams({"review_reality": block}))]
    for text in texts:
        for secret in ("alice-login", "bob-login", "review-bot-login", "SECRET-TITLE", '"number"'):
            assert secret not in text


@pytest.mark.parametrize("block, reason", [
    (None, "the run has no review data."),
    ({"available": False, "reason": "needs_platform: no gh"}, "not measured here: needs GitHub or GitLab access."),
    ({"available": False}, "not measured here: unavailable."),
    ({**REVIEW, "sequence": None}, "no per-change sequence"),
    ({**REVIEW, "sequence": [], "merged_count": 0}, "no change was merged"),
])
def test_strip_not_drawn_says_why(block: object, reason: str, tmp_path: Path) -> None:
    ctx = {} if block is None else {"review_reality": block}
    strip = rd.review_diagrams(ctx)[1]
    assert strip["svg"] is None and reason in (strip["skipped"] or "")
    assert f"Not drawn: {strip['skipped']}" in ad.diagrams_section([strip])
    (tmp_path / rd.STRIP_FILE).write_text("stale", encoding="utf-8")
    assert rd.write_all(tmp_path, rd.review_diagrams(ctx), rd.REVIEW_FILES) == []
    assert not (tmp_path / rd.STRIP_FILE).exists()


def test_strip_reads_an_unexpected_token_as_unknown() -> None:
    content = _svg({"review_reality": {**REVIEW, "sequence": ["approved", "<odd>", 3]}}, rd.STRIP_FILE)
    assert _ticks(content) == ["approved", "unknown", "unknown"]
    assert "odd" not in content and "f-rv-unknown" not in content


def test_strip_ignores_a_bot_list_of_another_length() -> None:
    content = _svg({"review_reality": {**REVIEW, "bot_sequence": ["bot"]}}, rd.STRIP_FILE)
    assert _bots(content) == 0


# --- captions, report and wiring -------------------------------------------------


def test_text_names_no_layer_number_and_no_banned_word() -> None:
    texts = {name: _prose(c) if name.endswith(".svg") else c for name, c in goldens().items()}
    for name, content in texts.items():
        assert not re.search(r"\blayer[- ]\d", content, re.I), name
        assert not banned_hits(content), (name, banned_hits(content))


def test_section_opens_each_diagram_with_in_place() -> None:
    lines = goldens()["expected-review-section.md"].splitlines()
    assert lines[0] == "## Docs behind their code" and lines[2].startswith(IN_PLACE)
    strip = lines.index("## Review on recent merges")
    assert lines[strip + 2] == (f"{IN_PLACE} 3 of 10 sampled merged changes approved and 2 more "
                                "reviewed, and 1 not known.")


def test_build_all_puts_the_waffle_first() -> None:
    assert [d["file"] for d in rd.build_all(CTX)] == list(rd.ALL_FILES)


def test_real_run_writes_and_links_every_diagram(tmp_path: Path) -> None:
    repo = _build_parity_fixture(tmp_path / "repo")
    ctx = build_and_publish_run(repo_root=repo, run_date="2026-01-01")
    report = render_report(ctx, "r")
    for diagram in rd.build_all(ctx):
        path = repo / ".assess" / diagram["file"]
        if diagram["svg"] is None:
            assert f"Not drawn: {diagram['skipped']}" in report and not path.exists()
        else:
            assert path.read_text(encoding="utf-8") == diagram["svg"]
            assert f"[![{diagram['title']}](./{diagram['file']})](./{diagram['file']})" in report
        for text in (diagram["in_place"], diagram["caption"], diagram["skipped"] or ""):
            assert not banned_hits(text)


def test_counts_only_run_redraws_the_review_diagrams_from_the_hashed_copy(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import assess_core
    repo = _build_parity_fixture(tmp_path / "repo")
    drawn: list[dict[str, Any]] = []

    def spy(ctx: Any) -> Any:
        drawn.append(ctx)
        return rd.counts_only_diagrams(ctx)

    monkeypatch.setattr(assess_core, "counts_only_diagrams", spy)
    assert assess_core.main([str(repo), "--non-interactive", "--counts-only", "--hash-salt", "s"]) == 0
    [copy_ctx] = drawn
    hashed = [d["path"] for d in copy_ctx["doc_staleness"]["docs"]]
    assert hashed and all(p.startswith("h:") for p in hashed)
    full = json.loads((repo / ".assess" / "run-context.json").read_text(encoding="utf-8"))
    real_paths = [d["path"] for d in full["doc_staleness"]["docs"]]
    drift = repo / ".assess" / rd.COUNTS_ONLY_FILES[0]
    if drift.exists():
        assert not any(path in drift.read_text(encoding="utf-8") for path in real_paths)
    # The full-text pictures beside the report are left as the run drew them.
    assert [(repo / ".assess" / d["file"]).exists() for d in rd.review_diagrams(full)] == \
        [d["svg"] is not None for d in rd.review_diagrams(full)]


def test_counts_only_diagrams_are_named_apart() -> None:
    hashed = counts_only(CTX, hash_paths=True, salt="s")
    drawn = rd.counts_only_diagrams(hashed)
    assert [d["file"] for d in drawn] == ["doc-drift.counts-only.svg", "review-strip.counts-only.svg"]
    assert all(d["svg"] is not None for d in drawn)
