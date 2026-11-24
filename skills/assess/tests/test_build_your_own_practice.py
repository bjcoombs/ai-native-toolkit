"""docs/build-your-own-practice.md: the guide's worked example, run verbatim (#1151).

Each fenced block after a ``<!-- guide-example: NAME -->`` marker is read from the guide as it
stands. The test runs the ``copy`` command through ``levels_new.main``, appends the
``practice`` table, loads the file the way ``/assess --levels`` does and checks the summary
lines against ``result-60`` and ``result-85``. It also swaps in the ``typo`` line and checks
the ``typo-error`` message. A change to the guide or the code that makes them disagree
turns this test red. The guide's prose is held to the plain-English lint and the banned words.
"""
from __future__ import annotations

import re
import shlex
from pathlib import Path
from typing import Any

import pytest

import levels_new
from lib import core_scans
from lib.level_signals import signal_path_validator
from lib.level_tally import LEVEL_READS
from lib.plain_english import lint
from lib.profiles import ProfileError, single_repo_choice
from lib.vocabulary import banned_hits

GUIDE = Path(__file__).resolve().parents[3] / "docs" / "build-your-own-practice.md"
_EXAMPLE = re.compile(r"<!-- guide-example: ([\w-]+) -->\n```\w*\n(.*?)```", re.S)

# Run-context blocks that meet every handover practice, with the coverage threshold left to the test.
HANDOVER_MET: dict[str, Any] = {
    "layer_grades": {"instruction_file_present": True},
    "ci_checks": {"lint_runs": True, "enforced_check_runs": True},
    "review_evidence": {"present": True},
    "doc_staleness": {"summary": {"stale_pct": 10}},
}


def examples() -> dict[str, str]:
    found = {name: body for name, body in _EXAMPLE.findall(GUIDE.read_text(encoding="utf-8"))}
    assert set(found) == {"copy", "practice", "typo", "typo-error", "result-60", "result-85"}
    return found


@pytest.fixture
def model(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """``my-team.toml`` made by the guide's copy command and practice table."""
    monkeypatch.chdir(tmp_path)
    argv = shlex.split(examples()["copy"].strip())
    assert argv[:3] == ["uv", "run", "scripts/levels_new.py"]
    assert argv[3].startswith("/path/to/your-repo/")  # the reader's repository: here, tmp_path
    path = tmp_path / Path(argv[3]).name
    assert levels_new.main([str(path), *argv[4:]]) == 0
    path.write_text(path.read_text(encoding="utf-8") + "\n" + examples()["practice"], encoding="utf-8")
    return path


def _summary(path: Path, threshold: float) -> list[str]:
    choice = single_repo_choice(None, path, signal_path_validator())
    ctx = {**HANDOVER_MET, "coverage_gate": {"enforced_meets_minimum": True, "max_threshold_percent": threshold}}
    level = core_scans.level(*(ctx.get(k) for k in LEVEL_READS), choice)
    return list(core_scans.headline(level, None, choice)["summary_lines"])


@pytest.mark.parametrize("threshold", [60, 85])
def test_the_worked_example_reads_as_the_guide_says(model: Path, threshold: int) -> None:
    expected = examples()[f"result-{threshold}"].strip().splitlines()
    assert _summary(model, threshold)[:len(expected)] == expected


def test_the_typo_stops_with_the_error_the_guide_shows(model: Path) -> None:
    good = '    { signal = "coverage_gate.max_threshold_percent", op = "gte", value = 80 },\n'
    text = model.read_text(encoding="utf-8")
    assert good in text
    model.write_text(text.replace(good, examples()["typo"]), encoding="utf-8")
    with pytest.raises(ProfileError) as caught:
        single_repo_choice(None, model, signal_path_validator())
    # assess_core.py prints a refused model as "error: <message>"
    assert f"error: {caught.value}" == examples()["typo-error"].strip()


def _prose_blocks() -> list[str]:
    """The guide's paragraphs, list items and table cells, without code, markers or headings."""
    text = re.sub(r"```.*?```", "", GUIDE.read_text(encoding="utf-8"), flags=re.S)
    blocks: list[str] = []
    for block in re.split(r"\n\s*\n", text):
        if block.startswith(("#", "<!--")):
            continue
        if block.startswith("|"):
            blocks += [cell for row in block.splitlines()[2:] for cell in row.split("|")[3:4]]
        else:
            blocks += re.split(r"\n- ", block)
    return [b for b in blocks if b.strip()]


def test_the_guide_is_plain_english() -> None:
    blocks = _prose_blocks()
    assert len(blocks) > 20 and any("handover" in b for b in blocks)  # the table cells are read too
    assert [issue for block in blocks for issue in lint(block)] == []


def test_the_guide_uses_no_banned_word() -> None:
    assert banned_hits(GUIDE.read_text(encoding="utf-8")) == []
