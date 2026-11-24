"""The banned-vocabulary guard (issue #743, parent #740): ``lib/vocabulary.py``.

Renders the user-facing outputs from source, never from a committed golden, so an edit to
any string that reaches them is caught here: a real fleet run with the shipped maturity
model (``report.md``, every diagram, the heat map, the team next-steps pages), the
in-repo diagram, trend, heat map and next-steps fixtures, and the /assess deterministic
report with every cross-practice finding rendered. It fails when a banned word or a retired
band label appears in the prose, naming the word, the output and the line, and when a
section of ``report.md`` or a next-steps page does not open with ``In place:``.

    cd skills/assess && uv run --locked pytest tests/test_vocabulary_guard.py
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

import assess_diagrams_fixture
import assess_diagrams_packages_fixture
import assess_diagrams_review_fixture
import fleet_diagrams_fixture
import fleet_trends_fixture
from assess_report import render_report
import lib.keyhole.keyhole_signals as ks
from lib.level_signals import DEFAULT_LEVELS
from lib.run_context_types import KeyholeConcern
from lib.vocabulary import (BANNED_WORDS, IN_PLACE, RETIRED_LABELS, ROW_PHRASES, TEMPLATE_WORDS, banned_hits,
                            opening_lines, prose, row_hits, template_prose)
from test_decomposition_parity import _build_parity_fixture
from test_fleet_cli import fleet_cli, froot  # noqa: F401 - froot is a fixture
from test_fleet_heatmap import FIXTURES as HEATMAP_FIXTURES
from test_fleet_heatmap import cli as heatmap_cli
from test_fleet_next_steps import _pages as next_steps_pages
from test_fleet_next_steps import cli as next_steps_cli

_SVG_TEXT = re.compile(r"<(text|title)\b[^>]*>(.*?)</\1>", re.S)


def _svg_prose(svg: str) -> str:
    return "\n".join(re.sub(r"<[^>]+>", " ", body) for _, body in _SVG_TEXT.findall(svg))


@pytest.fixture(scope="module")
def fleet_out(froot: Path, tmp_path_factory: pytest.TempPathFactory) -> Path:  # noqa: F811
    """A real fleet run with diagrams, then the heat map and the next-steps pages on it."""
    base = tmp_path_factory.mktemp("vocabulary")
    out = base / "out"
    assert fleet_cli.main([str(froot), "--levels", str(DEFAULT_LEVELS), "--history-dir",
                           str(base / "history"), "--out", str(out)]) == 0
    assert heatmap_cli.main([str(out), "--levels", str(DEFAULT_LEVELS)]) == 0
    assert next_steps_cli.main([str(out), "--levels", str(DEFAULT_LEVELS)]) == 0
    return out


@pytest.fixture(scope="module")
def outputs(fleet_out: Path, tmp_path_factory: pytest.TempPathFactory) -> dict[str, str]:
    """Every rendered output as ``{name: prose-bearing text}``."""
    rendered: dict[str, str] = {}
    for path in sorted(fleet_out.rglob("*.md")):
        rendered[f"fleet run: {path.relative_to(fleet_out).as_posix()}"] = path.read_text(encoding="utf-8")
    for path in sorted(fleet_out.glob("*.svg")):
        rendered[f"fleet run: {path.name}"] = _svg_prose(path.read_text(encoding="utf-8"))
    for name, content in fleet_diagrams_fixture.goldens(fleet_diagrams_fixture.FIXTURES).items():
        rendered[f"diagrams fixture: {name}"] = _svg_prose(content) if name.endswith(".svg") else content
    for name, content in assess_diagrams_packages_fixture.goldens().items():
        rendered[f"package map fixture: {name}"] = _svg_prose(content) if name.endswith(".svg") else content
    for name, content in assess_diagrams_fixture.goldens().items():
        rendered[f"assess diagrams fixture: {name}"] = _svg_prose(content) if name.endswith(".svg") else content
    for name, content in assess_diagrams_review_fixture.goldens().items():
        rendered[f"assess review diagrams fixture: {name}"] = _svg_prose(content) if name.endswith(".svg") else content
    for name, content in fleet_trends_fixture.goldens(fleet_trends_fixture.FIXTURES).items():
        rendered[f"trends fixture: {name}"] = _svg_prose(content) if name.endswith(".svg") else content
    base = tmp_path_factory.mktemp("vocabulary-fixtures")
    for name, extra in (("heatmap.svg", []), ("heatmap-compare.svg", ["--compare", str(HEATMAP_FIXTURES / "earlier")])):
        target = base / name
        assert heatmap_cli.main([str(HEATMAP_FIXTURES), "--levels", str(HEATMAP_FIXTURES / "levels.toml"),
                                 "--output", str(target), *extra]) == 0
        rendered[f"heat map fixture: {name}"] = _svg_prose(target.read_text(encoding="utf-8"))
    for name, page in next_steps_pages(base / "next-steps").items():
        rendered[f"next-steps fixture: {name}"] = page
    from assess_core import build_and_publish_run
    repo = _build_parity_fixture(base / "repo")
    rendered["assess report"] = render_report(build_and_publish_run(repo_root=repo, run_date="2026-01-01"), "r")
    every = ks.assemble_findings({name: ["src/a.py"] for name in ks.FINDING_ORDER})
    rendered["assess findings"] = ks.render_findings_markdown(every, [])
    concerns: list[KeyholeConcern] = [{"name": name, "count": 1} for name in ks.FINDING_ORDER]
    rendered["assess keyhole summary"] = ks._format_summary(concerns, 2)
    return rendered


def test_no_banned_word_or_retired_label_in_rendered_prose(outputs: dict[str, str]) -> None:
    hits = [f"{term!r} in {name}, line {line}: {text}"
            for name, content in outputs.items() for term, line, text in banned_hits(content)]
    assert not hits, "banned vocabulary (lib/vocabulary.py) in rendered output:\n" + "\n".join(hits)


def _fleet_pages(outputs: dict[str, str]) -> dict[str, str]:
    return {name: content for name, content in outputs.items()
            if name.startswith(("fleet run", "diagrams fixture", "trends fixture", "heat map fixture",
                                "next-steps fixture"))}


def test_fleet_pages_name_steps_and_gates_not_rows(outputs: dict[str, str], fleet_out: Path) -> None:
    """#1254: a maturity-model entry is a practice, Step or gate on every fleet page, never a row."""
    pages = _fleet_pages(outputs)
    assert any(name.endswith("report.md") for name in pages)
    assert any("teams" in name for name in pages)
    view = fleet_out / "fleet-view.html"
    assert view.is_file()
    pages["fleet run: fleet-view.html"] = re.sub(r"<[^>]+>", " ", view.read_text(encoding="utf-8"))
    hits = [f"{phrase!r} in {name}, line {line}: {text}"
            for name, content in pages.items() for phrase, line, text in row_hits(content)]
    assert not hits, "'row' for a maturity-model entry on a fleet page:\n" + "\n".join(hits)


_TEMPLATE = Path(__file__).resolve().parents[2] / "assess-findings" / "SKILL.md"


def test_the_model_written_template_uses_the_shared_vocabulary() -> None:
    """#1254: the report template the model fills, fenced blocks included, keeps out the
    words the shared vocabulary replaced."""
    text = template_prose(_TEMPLATE.read_text(encoding="utf-8"))
    hits = [f"{term!r}, line {line}: {body}" for term, line, body in banned_hits(text)
            if term in TEMPLATE_WORDS]
    assert not hits, f"banned vocabulary in {_TEMPLATE.name}:\n" + "\n".join(hits)


def test_every_fleet_report_and_next_steps_section_opens_with_what_is_in_place(
        outputs: dict[str, str]) -> None:
    pairs = []
    for name, content in outputs.items():
        if name.endswith("report.md"):
            pairs += [(name, h, first) for h, first in opening_lines(content, "##")]
        elif "teams" in name or name.startswith("next-steps fixture"):
            pairs += [(name, h, first) for h, first in opening_lines(content)]
    assert len(pairs) > 20
    missing = [f"{name}: {heading!r} opens with {first!r}" for name, heading, first in pairs
               if not first.startswith(f"{IN_PLACE} ")]
    assert not missing, f"sections that do not open with {IN_PLACE!r}:\n" + "\n".join(missing)


def test_the_guard_reads_the_shipped_fleet_report_sections(fleet_out: Path) -> None:
    """The real run's report has the credit section and every diagram section."""
    headings = [h for h, _ in opening_lines((fleet_out / "report.md").read_text(encoding="utf-8"), "##")]
    assert "## What changed and who earned it" in headings and len(headings) >= 6


def test_the_readme_documents_exactly_the_banned_list() -> None:
    import lib.vocabulary
    section = lib.vocabulary.__doc__ or ""
    listed = re.findall(r"^\| `([^`]+)` \|", section, flags=re.M)
    assert listed == [*BANNED_WORDS, *RETIRED_LABELS]
    assert "tests/test_vocabulary_guard.py" in section and IN_PLACE in section


def test_every_next_step_and_hint_the_shipped_model_can_render_is_supportive() -> None:
    """Every criterion's guidance and every signal's hint, not only the levels a fixture reaches."""
    from lib.fleet.fleet_next_steps import FULL_RUN_HINT, PLATFORM_HINT
    from lib.headline import reason_word
    from lib.level_signals import load_default_levels
    from lib.next_step_guidance import _LAYER5_PARTS, guidance, measure_hint
    texts = {"FULL_RUN_HINT": FULL_RUN_HINT, "PLATFORM_HINT": PLATFORM_HINT}
    for row in load_default_levels()["rows"]:
        texts[f"{row['id']} title"] = row["title"]
        for number, level in enumerate(row["levels"], start=1):
            for criterion in level["criteria"]:
                signal = criterion["signal"]
                texts[f"{signal} hint"] = measure_hint(signal)
                for unmet in ((), *((part,) for part in _LAYER5_PARTS)):
                    worded = guidance(signal, criterion["op"], criterion["value"], unmet)
                    if worded is not None:
                        texts[f"{row['id']}.{number} {signal}"] = f"{worded['change']}\n{worded['done_when']}"
    for code in ("scan_failed", "missing_checkout", "no_python_packages", "build_failed"):
        texts[f"reason {code}"] = reason_word(code)
    assert len(texts) > 40
    hits = [f"{term!r} in {name}: {text}" for name, content in texts.items()
            for term, _, text in banned_hits(content)]
    assert not hits, "banned vocabulary in next-step guidance:\n" + "\n".join(hits)


@pytest.mark.parametrize(("text", "found"), [
    ("Structure reads tangled.", ["tangled"]),
    ("A Lying map.", ["lying"]),
    ("capped at Partly in place, and remains unproven: 68 structural concerns", ["capped", "unproven", "concerns"]),
    ("Rows say Missing.", ["missing"]),
    ("Score 7 / 8 - AI-Native", ["AI-Native"]),
    ("a failure", ["failure"]),
    ("`instruction_claims.failed` and `fail_under` stay", []),
    ("coverage_gate.fail_under stays", []),
    ("```\nfailed\n```", []),
    ("at least 50 percent", []),
    # The product's own name is not the retired band label (#794); prose "AI-Native" still is.
    ("See [docs/what-we-measure.md](https://github.com/bjcoombs/ai-native-toolkit/blob/main/docs/x.md).", []),
    ("Read https://github.com/bjcoombs/ai-native-toolkit/issues/1 first.", []),
    ("Install AI-Native-toolkit, then the ai-native-toolkit plugin.", []),
    ("This repository is AI-Native.", ["AI-Native"]),
    ("This repo is AI-native.", ["AI-Native"]),
    ("an ai-native team", ["AI-Native"]),
    ("an AI-Native-ready repository", ["AI-Native"]),
    ("the AI-Native-Toolkit band", ["AI-Native"]),
    ("an ai-native-toolkits list", ["AI-Native"]),
    ("AI-Native readiness, see [the guide](https://example.test/ai-native-toolkit).", ["AI-Native"]),
])
def test_banned_hits_reads_prose_only(text: str, found: list[str]) -> None:
    assert [term for term, _, _ in banned_hits(text)] == found


@pytest.mark.parametrize(("text", "found"), [
    ("What moved, and which row moved it?", ["which row"]),
    ("no team rows to measure", ["team row"]),
    ("nothing confirmed on this row yet", ["this row"]),
    ("one table row per repository", []),
    ("`team_rows` stays", []),
])
def test_row_hits(text: str, found: list[str]) -> None:
    assert [phrase for phrase, _, _ in row_hits(text)] == found
    assert set(found) <= set(ROW_PHRASES)


def test_template_prose_reads_fenced_template_text() -> None:
    assert "lying" in prose(template_prose("```markdown\nlying signals\n```\n"))
    assert set(TEMPLATE_WORDS) <= set(BANNED_WORDS)


def test_prose_blanks_code_and_identifiers() -> None:
    assert "lying" not in prose("`lying_map` and lying_map")
    assert "toolkit" not in prose("[a](https://github.com/bjcoombs/ai-native-toolkit/x) <https://h.test/ai-native-toolkit>")


def test_opening_lines_pairs_each_heading_with_its_first_line() -> None:
    page = "# T\n\nIn place: a.\n\n## A\n\n### B\n\nx\n## C\n"
    assert opening_lines(page) == [("# T", "In place: a."), ("## A", ""), ("### B", "x"), ("## C", "")]
    assert opening_lines(page, "##") == [("## A", ""), ("## C", "")]


def test_every_manifest_why_is_supportive_and_names_no_layer_number() -> None:
    """#939: every why in the check manifest, as measures.json carries it, reads supportively."""
    import json

    from lib.manifest.manifest_whys import PLUMBING
    measures = json.loads((Path(__file__).resolve().parents[1] / "references" / "measures.json")
                          .read_text(encoding="utf-8"))
    whys = [c["why"] for r in measures["rows"] for lv in r["levels"] for c in lv["criteria"]]
    whys += [e["why"] for key in ("scans", "findings", "tags") for e in measures[key]]
    whys = [why for why in whys if why != PLUMBING]
    assert len(whys) >= 83 and all(whys)
    for why in whys:
        assert not banned_hits(why), why
        assert not re.search(r"layer[ -]?[0-9]", prose(why), re.I), why
