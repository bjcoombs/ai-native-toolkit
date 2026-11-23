"""Named maturity-model profiles and the model choice of a single-repository run (lib/profiles.py, #1150).

Pins that every shipped profile reuses the default model's practices unchanged, that a run
meeting every handover practice reads "Ready for handover" and that the same run without CI
running the tests names that one gap, that a custom model's own practices are marked, that a
single-repository run gives the Step /fleet gives for the same model and blocks, and the
command-line refusals.
"""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import pytest

from lib import core_scans
from lib.fleet.fleet_level import repo_level
from lib.level_signals import signal_path_validator
from lib.level_tally import LEVEL_READS, default_model
from lib.profiles import (PROFILES_DIR, ModelChoice, ProfileError, add_model_arguments, model_lines,
                          non_default_ids, profile_names, profile_path, resolve, single_repo_choice)
from test_headline import ci2  # noqa: F401 - ci2 is a fixture

# Run-context blocks that meet every handover practice.
HANDOVER_MET: dict[str, Any] = {
    "layer_grades": {"instruction_file_present": True},
    "ci_checks": {"lint_runs": True, "enforced_check_runs": True},
    "coverage_gate": {"enforced_meets_minimum": True},
    "review_evidence": {"present": True},
    "doc_staleness": {"summary": {"stale_pct": 10}},
}

CUSTOM = """version = "my-1"

[[rows]]
id = "layer-0-instructions"
title = "Agent instructions"
scope = "team"
aggregation = "min"

  [[rows.levels]]
  level = 1
  truth = ["agent_context"]
  criteria = [{ signal = "layer_grades.instruction_file_present", op = "eq", value = true }]

[[rows]]
id = "fresh-docs"
title = "Very fresh docs"
scope = "team"
aggregation = "min"

  [[rows.levels]]
  level = 1
  truth = ["docs"]
  criteria = [{ signal = "doc_staleness.summary.stale_pct", op = "lte", value = 5 }]

[[steps]]
step = 1
name = "Assisted"

[[steps]]
step = 2
name = "Supervised agent PRs"
gates = [{ id = "instructions", row = "layer-0-instructions", level = 1 }]
"""


def _blocks(**over: Any) -> dict[str, Any]:
    ctx = {**HANDOVER_MET, **over}
    return {k: v for k, v in ctx.items() if v is not None}


def _summary(ctx: dict[str, Any], choice: ModelChoice | None) -> list[str]:
    level = core_scans.level(*(ctx.get(k) for k in LEVEL_READS), choice)
    return list(core_scans.headline(level, None, choice)["summary_lines"])


def _custom(tmp_path: Path) -> Path:
    path = tmp_path / "my.toml"
    path.write_text(CUSTOM, encoding="utf-8")
    return path


def test_the_shipped_profiles_are_handover_and_citizen() -> None:
    assert profile_names() == ("citizen", "handover")


@pytest.mark.parametrize("name", ["handover", "citizen"])
def test_a_profile_reuses_the_default_practices_and_gates_unchanged(name: str) -> None:
    choice = resolve(name, None, signal_path_validator())
    assert choice.kind == "profile" and choice.label == f"profile: {name}"
    assert non_default_ids(choice.model) == []
    default_steps = {s["step"]: s for s in default_model()["steps"]}
    for step in choice.model["steps"]:
        assert step == default_steps[step["step"]]
    assert choice.model["version"].startswith(f"profile-{name}-")


@pytest.mark.parametrize("name", ["handover", "citizen"])
def test_a_profile_justifies_each_practice_in_one_line(name: str) -> None:
    text = profile_path(name).read_text(encoding="utf-8")
    for cid in (f"{r['id']}.{lv['level']}" for r in resolve(name, None).model["rows"] for lv in r["levels"]):
        assert f"# - {cid}: " in text, cid


def test_handover_met_reads_ready_for_handover() -> None:
    lines = _summary(_blocks(), resolve("handover", None))
    assert lines[:2] == ["Maturity model: profile handover, not the default.",
                         "Ready for handover (profile: handover)."]
    assert lines[2].startswith("Step 4 of 4: ")


def test_without_ci_running_the_tests_the_one_gap_is_named() -> None:
    lines = _summary(_blocks(ci_checks={"lint_runs": True, "enforced_check_runs": False}),
                     resolve("handover", None))
    assert lines[1] == ("Not ready for handover (profile: handover). "
                        "Missing: CI and the agent's local check (not met).")
    assert lines[2].startswith("Step 2 of 4: ")


def test_a_practice_not_measured_is_missing_with_its_reason() -> None:
    lines = _summary(_blocks(review_evidence=None), resolve("handover", None))
    assert lines[1] == ("Not ready for handover (profile: handover). "
                        "Missing: Review on merged pull requests (not measured: scan did not run).")


def test_an_owner_exclusion_does_not_read_ready() -> None:
    excluded = {"exclusions": [{"id": "layer-7-review.1", "kind": "not_applicable", "source": "config"}]}
    lines = _summary(_blocks(owner_exclusions=excluded), resolve("handover", None))
    assert lines[1].startswith("Not ready for handover (profile: handover). Missing: "
                               "Review on merged pull requests (excluded by owner: ")


def test_citizen_has_its_own_words() -> None:
    choice = resolve("citizen", None)
    assert _summary(_blocks(), choice)[1] == "Meets the citizen bar (profile: citizen)."
    lines = _summary(_blocks(layer_grades={"instruction_file_present": False}), choice)
    assert lines[1] == "Below the citizen bar (profile: citizen). Missing: Agent instructions (not met)."


def test_a_custom_model_marks_the_practices_the_default_does_not_define(tmp_path: Path) -> None:
    choice = single_repo_choice(None, _custom(tmp_path), signal_path_validator())
    assert choice.kind == "file" and choice.label == "model: my.toml"
    lines = _summary(_blocks(), choice)
    assert lines[0] == "Maturity model: my.toml, a custom file, not the default."
    assert lines[1] == ("Not every practice met (model: my.toml). "
                        "Missing: Very fresh docs (not met) [not in the default model].")
    assert lines[2] == "Not in the default model: Very fresh docs."


def test_a_level_above_one_is_named() -> None:
    model = default_model()
    choice = ModelChoice(model, "file", "copy.toml")
    lines = model_lines(core_scans.level(*(_blocks().get(k) for k in LEVEL_READS), choice), choice)
    assert "Agent instructions, level 2 (" in lines[1]


def test_the_default_model_adds_no_lines() -> None:
    choice = resolve(None, None)
    assert choice.kind == "default" and choice.label == "model: default"
    level = core_scans.level(*(_blocks().get(k) for k in LEVEL_READS), None)
    assert model_lines(level, choice) == [] and model_lines(level, None) == []
    assert not any("Maturity model" in line for line in _summary(_blocks(), None))


@pytest.mark.parametrize("name", ["handover", "citizen"])
def test_the_single_repository_step_is_the_fleet_step(name: str, tmp_path: Path) -> None:
    for choice in (resolve(name, None), single_repo_choice(None, _custom(tmp_path))):
        for ctx in (_blocks(), _blocks(ci_checks={"enforced_check_runs": False}), {}):
            mine = core_scans.level(*(ctx.get(k) for k in LEVEL_READS), choice)
            fleet = repo_level(choice.model, {"scans": ctx})  # type: ignore[typeddict-item]
            assert mine == fleet


def test_a_model_reading_a_block_the_level_does_not_read_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "wide.toml"
    path.write_text(CUSTOM.replace("doc_staleness.summary.stale_pct", "ai_usage.agent_share"), encoding="utf-8")
    assert resolve(None, path).kind == "file"
    with pytest.raises(ProfileError, match="model: wide.toml reads ai_usage, which the single-repository"):
        single_repo_choice(None, path)


def test_resolve_refusals(tmp_path: Path) -> None:
    with pytest.raises(ProfileError, match="unknown profile 'nope'; the profiles are: citizen, handover"):
        resolve("nope", None)
    with pytest.raises(ProfileError, match="pass one"):
        resolve("handover", tmp_path / "x.toml")
    with pytest.raises(ProfileError):
        resolve(None, tmp_path / "missing.toml")
    bad = tmp_path / "bad.toml"
    bad.write_text('version = "x"\nunknown = 1\n', encoding="utf-8")
    with pytest.raises(ProfileError):
        resolve(None, bad)


def test_the_flags_are_mutually_exclusive() -> None:
    parser = argparse.ArgumentParser()
    add_model_arguments(parser)
    assert vars(parser.parse_args(["--profile", "handover"])) == {"profile": "handover", "levels": None}
    with pytest.raises(SystemExit):
        parser.parse_args(["--profile", "handover", "--levels", "x.toml"])
    only = argparse.ArgumentParser()
    add_model_arguments(only, levels=False)
    assert vars(only.parse_args([])) == {"profile": None}


def test_the_profiles_directory_holds_only_profiles() -> None:
    assert sorted(p.name for p in PROFILES_DIR.iterdir()) == ["citizen.toml", "handover.toml"]


def test_assess_core_refuses_an_unknown_profile(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    from assess_core import main

    assert main([str(tmp_path), "--profile", "nope"]) == 2
    assert "unknown profile 'nope'" in capsys.readouterr().err


def test_a_mutation_refresh_needs_the_runs_model() -> None:
    from assess_core import _model_differs

    handover = resolve("handover", None)
    profile_level = {"levels_version": handover.model["version"]}
    assert _model_differs(profile_level, None)
    assert not _model_differs(profile_level, handover)
    assert not _model_differs({"levels_version": default_model()["version"]}, None)
    assert not _model_differs({"levels_version": "default-2026.11.10"}, None)  # an older default
    assert not _model_differs({"available": False}, handover)


def test_a_profile_run_names_the_profile_at_the_top_of_the_report(ci2: Path) -> None:  # noqa: F811
    from assess_core import build_and_publish_run

    choice = single_repo_choice("handover", None, signal_path_validator())
    ctx = build_and_publish_run(repo_root=ci2, run_date="2026-11-23", non_interactive=True, model=choice)
    assert ctx["level"]["levels_version"] == choice.model["version"]
    lines = ctx["headline"]["summary_lines"]
    assert lines[0] == "Maturity model: profile handover, not the default."
    assert lines[1].startswith("Not ready for handover (profile: handover). Missing: ")
    assert ctx["headline"]["summary_markdown"].startswith(lines[0])


def _fleet_cli() -> Any:
    from test_fleet_cli import fleet_cli

    return fleet_cli


def test_fleet_profile_is_the_profiles_levels_file() -> None:
    fleet_cli = _fleet_cli()
    args = fleet_cli._parse_args(["root", "--out", "o", "--profile", "handover"])
    assert args.levels == str(profile_path("handover"))
    for argv in (["--profile", "nope"], ["--profile", "handover", "--levels", "x.toml"]):
        with pytest.raises(SystemExit) as exc:
            fleet_cli._parse_args(["root", "--out", "o", *argv])
        assert exc.value.code == 2


@pytest.mark.parametrize("flags", [["--profile", "handover"], ["--levels", "my.toml"]])
def test_assess_and_fleet_give_one_step_for_one_model(flags: list[str], ci2: Path,  # noqa: F811
                                                      tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import json

    from assess_core import build_and_publish_run

    _custom(tmp_path)
    monkeypatch.chdir(tmp_path)
    listing = tmp_path / "list.txt"
    listing.write_text(f"{ci2}\n", encoding="utf-8")
    assert _fleet_cli().main(["--from-list", str(listing), *flags, "--out", "out", "--no-history"]) == 0
    fleet = json.loads((tmp_path / "out" / "level.json").read_text(encoding="utf-8"))
    (repo_result,) = fleet["repos"].values()
    choice = single_repo_choice(*((flags[1], None) if flags[0] == "--profile" else (None, flags[1])))
    ctx = build_and_publish_run(repo_root=ci2, run_date="2026-11-23", non_interactive=True, model=choice)
    assert fleet["levels_version"] == ctx["level"]["levels_version"] == choice.model["version"]
    assert ctx["level"]["step"] == repo_result["step"]
    assert {c["id"]: c["state"] for c in ctx["level"]["conditions"]} == \
        {c["id"]: c["state"] for c in repo_result["conditions"]}


def test_a_mutation_refresh_under_another_model_stops_before_mutating(tmp_path: Path,
                                                                       capsys: pytest.CaptureFixture[str]) -> None:
    import json

    from assess_core import run_opt_in_mutation

    (tmp_path / ".assess").mkdir()
    ctx = {"level": {"levels_version": resolve("handover", None).model["version"]},
           "test_focus": {"targets": [{"path": "a.py", "tests": ["test_a.py"]}]}}
    (tmp_path / ".assess" / "run-context.json").write_text(json.dumps(ctx), encoding="utf-8")
    assert run_opt_in_mutation(tmp_path) == 2
    assert "needs the --profile or --levels the run used" in capsys.readouterr().err


def test_the_step_line_keeps_its_glossary_link_under_a_profile() -> None:
    choice = resolve("handover", None)
    level = core_scans.level(*(_blocks().get(k) for k in LEVEL_READS), choice)
    paragraphs = core_scans.headline(level, None, choice)["summary_markdown"].split("\n\n")
    assert paragraphs[0] == "Maturity model: profile handover, not the default."
    assert paragraphs[2].startswith("[Step](") and "#step) 4 of 4: " in paragraphs[2]


def test_a_custom_model_may_not_take_a_shipped_version(tmp_path: Path) -> None:
    for version in (default_model()["version"], "profile-handover-2026.11.23"):
        path = tmp_path / "copy.toml"
        path.write_text(CUSTOM.replace('version = "my-1"', f'version = "{version}"'), encoding="utf-8")
        with pytest.raises(ProfileError, match="name the shipped models"):
            resolve(None, path)
