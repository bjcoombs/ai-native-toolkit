"""docs/build-your-own-practice.md: the guide's worked example, run verbatim (#1151).

Each fenced block after a ``<!-- guide-example: NAME -->`` marker is read from the guide as it
stands. The test runs the ``copy`` command through ``levels_new.main``, appends the
``practice`` table, loads the file the way ``/assess --levels`` does and checks the summary
lines against ``result-60`` and ``result-85``. It also swaps in the ``typo`` line and checks
the ``typo-error`` message. A change to the guide or the code that makes them disagree
turns this test red. The guide's prose is held to the plain-English lint and the banned words.

Section 7, a pack check end to end (#1275): the ``pack`` file, which holds the check and the
practice row (#1274), is written into a fixture repository with and without
``docs/glossary.md``, and the ``pack-run`` command is run through ``assess_core.main``. The
practice reads met, then unmet, and the summary holds the ``pack-result-met`` or
``pack-result-unmet`` line. The ``pack-command`` line, swapped in for the rule, gives the loader
message of ``pack-command-error``.
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
from lib.run import core_scans
from lib.packs import PackError, load_pack
from lib.scoring.level_signals import signal_path_validator
from lib.scoring.level_tally import LEVEL_READS
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
                          "pack", "pack-run", "pack-result-met", "pack-result-unmet",
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


# --- section 7: a pack check end to end (#1275) ----------------------------------------


def _pack_run() -> list[str]:
    """The ``pack-run`` command's arguments after ``/assess``, checked against the file name."""
    argv = shlex.split(examples()["pack-run"].strip())
    assert argv[0] == "/assess"
    assert argv[argv.index("--pack") + 1] == "team.pack.toml"
    return argv[1:]


@pytest.fixture
def team_repo(tmp_path: Path) -> Path:
    """The reader's repository: the guide's ``team.pack.toml`` (its check and its row) and a
    ``docs/glossary.md``."""
    root = tmp_path / "your-repo"
    (root / "docs").mkdir(parents=True)
    (root / "docs" / "glossary.md").write_text("# Glossary\n", encoding="utf-8")
    (root / "team.pack.toml").write_text(examples()["pack"], encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(root)], check=True, capture_output=True,
                   encoding="utf-8", errors="replace")
    return root


def _run(repo: Path, args: list[str], monkeypatch: pytest.MonkeyPatch,
         capsys: pytest.CaptureFixture[str]) -> dict[str, Any]:
    monkeypatch.chdir(repo)
    assert assess_core.main([str(repo), *args]) == 0
    capsys.readouterr()
    ctx: dict[str, Any] = json.loads((repo / ".assess" / "run-context.json").read_text(encoding="utf-8"))
    return ctx


@pytest.mark.parametrize("glossary", [True, False])
def test_the_pack_run_reads_as_the_guide_says(team_repo: Path, glossary: bool,
                                              monkeypatch: pytest.MonkeyPatch,
                                              capsys: pytest.CaptureFixture[str]) -> None:
    if not glossary:
        (team_repo / "docs" / "glossary.md").unlink()
    ctx = _run(team_repo, _pack_run(), monkeypatch, capsys)
    assert ctx["pack"] == {"team": {"glossary": glossary}}
    assert [p["name"] for p in ctx["packs"]] == ["team"]
    condition = next(c for c in ctx["level"]["conditions"] if c["id"] == "glossary.1")
    assert condition["state"] == ("met" if glossary else "unmet")
    assert condition["source"] == "team"
    expected = examples()["pack-result-met" if glossary else "pack-result-unmet"].strip()
    assert expected in ctx["headline"]["summary_lines"]


def test_the_pack_adds_its_practice_to_a_levels_copy(model: Path, team_repo: Path,
                                                     monkeypatch: pytest.MonkeyPatch,
                                                     capsys: pytest.CaptureFixture[str]) -> None:
    """The guide's "pass ``--levels my-team.toml`` as well": the section 2 copy plus the pack."""
    (team_repo / "my-team.toml").write_text(model.read_text(encoding="utf-8"), encoding="utf-8")
    ctx = _run(team_repo, ["--levels", "my-team.toml", *_pack_run()], monkeypatch, capsys)
    ids = [c["id"] for c in ctx["level"]["conditions"]]
    assert "coverage-80.1" in ids and ids[-1] == "glossary.1"
    summary = ctx["headline"]["summary_lines"]
    assert examples()["pack-result-met"].strip() in summary
    assert "Not in the default model: Coverage gate of at least 80; Domain glossary [pack team]." in summary


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
