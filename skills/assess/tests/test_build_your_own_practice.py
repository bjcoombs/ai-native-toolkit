"""docs/build-your-own-practice.md: the guide's worked example, run verbatim (#1151).

Each fenced block after a ``<!-- guide-example: NAME -->`` marker is read from the guide as it
stands. The test runs the ``copy`` command through ``levels_new.main``, appends the
``practice`` table, loads the file the way ``/assess --levels`` does and checks the summary
lines against ``result-60`` and ``result-85``. It also swaps in the ``typo`` line and checks
the ``typo-error`` message. A change to the guide or the code that makes them disagree
turns this test red. The guide's prose is held to the plain-English lint and the banned words.

Section 7, a pack check end to end (#1275): the ``pack`` file and the ``pack-row`` table are
written into a fixture repository with and without ``docs/glossary.md``, and the ``pack-run``
command is run through ``assess_core.main``; the practice reads met, then unmet. The summary
lines are checked against ``pack-result-met`` and ``pack-result-unmet`` with the pack block the
real collector writes, and the ``pack-command`` line, swapped in for the rule, gives the
loader message of ``pack-command-error``.
"""
from __future__ import annotations

import json
import re
import shlex
import subprocess
from pathlib import Path
from typing import Any

import pytest

import assess_core
import levels_new
from lib import core_scans
from lib.pack_checks import collect
from lib.packs import PackError, load_pack
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
    assert set(found) == {"copy", "practice", "typo", "typo-error", "result-60", "result-85",
                          "pack", "pack-row", "pack-run", "pack-result-met", "pack-result-unmet",
                          "pack-command", "pack-command-error"}
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


def _summary(path: Path, threshold: float, packs: tuple[Any, ...] = (),
             pack: dict[str, Any] | None = None) -> list[str]:
    choice = single_repo_choice(None, path, signal_path_validator(packs), packs)
    ctx = {**HANDOVER_MET, "coverage_gate": {"enforced_meets_minimum": True, "max_threshold_percent": threshold}}
    level = core_scans.level(*(ctx.get(k) for k in LEVEL_READS), choice, pack)
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


# --- section 7: a pack check end to end (#1275) ----------------------------------------


def _pack_run() -> list[str]:
    """The ``pack-run`` command's arguments after ``/assess``, checked against the file names."""
    argv = shlex.split(examples()["pack-run"].strip())
    assert argv[0] == "/assess"
    assert argv[argv.index("--levels") + 1] == "my-team.toml"
    assert argv[argv.index("--pack") + 1] == "team.pack.toml"
    return argv[1:]


@pytest.fixture
def team_repo(model: Path, tmp_path: Path) -> Path:
    """The reader's repository: ``my-team.toml`` with the ``pack-row`` table added, the
    ``team.pack.toml`` of the guide and a ``docs/glossary.md``."""
    root = tmp_path / "your-repo"
    (root / "docs").mkdir(parents=True)
    (root / "docs" / "glossary.md").write_text("# Glossary\n", encoding="utf-8")
    (root / "my-team.toml").write_text(
        model.read_text(encoding="utf-8") + "\n" + examples()["pack-row"], encoding="utf-8")
    (root / "team.pack.toml").write_text(examples()["pack"], encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(root)], check=True, capture_output=True,
                   encoding="utf-8", errors="replace")
    return root


@pytest.mark.parametrize("glossary", [True, False])
def test_the_pack_run_scores_the_glossary_practice(team_repo: Path, glossary: bool,
                                                   monkeypatch: pytest.MonkeyPatch,
                                                   capsys: pytest.CaptureFixture[str]) -> None:
    if not glossary:
        (team_repo / "docs" / "glossary.md").unlink()
    monkeypatch.chdir(team_repo)
    assert assess_core.main([str(team_repo), *_pack_run()]) == 0
    capsys.readouterr()
    ctx = json.loads((team_repo / ".assess" / "run-context.json").read_text(encoding="utf-8"))
    assert ctx["pack"] == {"team": {"glossary": glossary}}
    assert [p["name"] for p in ctx["packs"]] == ["team"]
    condition = next(c for c in ctx["level"]["conditions"] if c["id"] == "glossary.1")
    assert condition["state"] == ("met" if glossary else "unmet")
    summary = ctx["headline"]["summary_lines"]
    assert "Not in the default model: Coverage gate of at least 80; Domain glossary." in summary
    assert ("Domain glossary (not met)" in summary[1]) is not glossary


@pytest.mark.parametrize("glossary", [True, False])
def test_the_pack_result_reads_as_the_guide_says(team_repo: Path, glossary: bool) -> None:
    if not glossary:
        (team_repo / "docs" / "glossary.md").unlink()
    packs = assess_core.load_packs([str(team_repo / "team.pack.toml")])
    pack = collect(team_repo, (set(), []), packs)["pack"]
    expected = examples()["pack-result-met" if glossary else "pack-result-unmet"].strip().splitlines()
    assert _summary(team_repo / "my-team.toml", 85, packs, pack)[:len(expected)] == expected


def test_a_pack_row_without_the_pack_stops_naming_pack(team_repo: Path) -> None:
    with pytest.raises(ProfileError, match="--pack"):
        single_repo_choice(None, team_repo / "my-team.toml", signal_path_validator())


def test_a_pack_that_runs_a_command_stops_with_the_error_the_guide_shows(team_repo: Path) -> None:
    path = team_repo / "team.pack.toml"
    rule = 'exists = "docs/glossary.md"\n'
    text = path.read_text(encoding="utf-8")
    assert rule in text
    path.write_text(text.replace(rule, examples()["pack-command"]), encoding="utf-8")
    with pytest.raises(PackError) as caught:
        load_pack(path)
    # assess_core.py prints a refused pack as "error: <message>"
    assert f"error: {caught.value}" == examples()["pack-command-error"].strip()


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
    assert any("practice pack" in b for b in blocks)  # section 7 is read too
    assert [issue for block in blocks for issue in lint(block)] == []


def test_the_guide_uses_no_banned_word() -> None:
    assert banned_hits(GUIDE.read_text(encoding="utf-8")) == []
