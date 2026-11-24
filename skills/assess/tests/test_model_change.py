"""One report, one maturity model, when a run uses a profile or a --levels file (lib/model_change.py, #1150).

Pins that a gate is never reported gained or lost across models, that the Step ladder and
the evidence waffle are drawn from the run's own model (or not drawn for a custom file),
and that the unit roll-up says it reads the default model under a profile.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from lib import core_scans
from lib.assess_diagrams import build_diagrams
from lib.top_card import since_line
from lib.level_tally import LEVEL_READS, default_model
from lib.model_change import (CUSTOM_NOT_DRAWN, across_models, custom_run, model_changed_line, model_for_level,
                              model_name, previous_levels_version, units_label)
from lib.prior_level import write_prior_level
from lib.profiles import ModelChoice, resolve
from test_headline import ci2  # noqa: F401 - ci2 is a fixture

DEFAULT_VERSION = default_model()["version"]
HANDOVER = resolve("handover", None)
CITIZEN = resolve("citizen", None)
BLOCKS: dict[str, Any] = {
    "layer_grades": {"instruction_file_present": True},
    "ci_checks": {"lint_runs": True, "enforced_check_runs": True},
}


def _level(choice: ModelChoice | None) -> Any:
    return core_scans.level(*(BLOCKS.get(k) for k in LEVEL_READS), choice)


def _since(**over: Any) -> Any:
    block = {"available": True, "first_run": False, "previous_run_id": "r1", "previous_commit": "abc",
             "closed": [], "closed_reason": None, "closed_count": 0, "unresolved": 0,
             "gates_gained": ["ci_runs_tests"], "gates_lost": [], "next_blocker": "coverage_gate",
             "lines": ["Closed since last run: 0", "Gates gained: CI runs tests", "Gates lost: none",
                       "Next blocker: coverage gate"], "markdown": ""}
    return {**block, **over}


def test_model_names() -> None:
    assert model_name("default-2026.11.10") == model_name(DEFAULT_VERSION) == "default"
    assert model_name(HANDOVER.model["version"]) == "handover"
    assert model_name("profile-citizen-2026.11.23") == "citizen"
    assert model_name("my-1") == "my-1"
    assert model_name("profile-") == "profile-"


def test_the_model_of_a_level_block() -> None:
    assert model_for_level({"levels_version": "default-2026.11.10"}) == default_model()
    assert model_for_level({"levels_version": HANDOVER.model["version"]}) == HANDOVER.model
    assert model_for_level({"levels_version": "my-1"}) is None
    assert model_for_level({"levels_version": "profile-nope-2026.11.23"}) is None
    assert model_for_level({"available": False}) is None and model_for_level(None) is None


def test_a_gate_is_never_compared_across_models() -> None:
    block = across_models(_since(), _level(HANDOVER), DEFAULT_VERSION)
    assert block["gates_gained"] is None and block["gates_lost"] is None
    assert block["lines"] == ["Closed since last run: 0",
                              "Model changed since last run (default → handover); gates not compared",
                              "Next blocker: coverage gate"]
    assert block["markdown"].splitlines()[1] == "- Model changed since last run (default → handover); gates not compared"
    assert model_changed_line(block) == block["lines"][1]
    assert since_line(block) == ("Since last run: "
                                 "model changed (default → handover); gates not compared.")


def test_one_model_keeps_the_comparison() -> None:
    since = _since()
    assert across_models(since, _level(None), "default-2026.11.10") is since
    assert across_models(since, _level(HANDOVER), HANDOVER.model["version"]) is since
    assert across_models(since, _level(HANDOVER), None) is since
    assert across_models(since, {"available": False}, DEFAULT_VERSION) is since
    first = _since(first_run=True)
    assert across_models(first, _level(HANDOVER), DEFAULT_VERSION) is first
    assert model_changed_line(since) is None and model_changed_line(None) is None


def test_the_previous_version_follows_the_baseline_order(tmp_path: Path) -> None:
    assert previous_levels_version(tmp_path, None) is None
    (tmp_path / "run-context.json").write_text(json.dumps({"level": {"levels_version": "ctx-1"}}), encoding="utf-8")
    assert previous_levels_version(tmp_path, None) == "ctx-1"
    write_prior_level(tmp_path, _level(None))  # no baseline: run-context.json still decides
    assert previous_levels_version(tmp_path, None) == "ctx-1"
    write_prior_level(tmp_path, _level(None), {"run_id": "r", "head_sha": "abc", "held_gates": []})
    assert previous_levels_version(tmp_path, None) == DEFAULT_VERSION
    # a profile run reads past the committed default baseline, to the run-context.json
    assert previous_levels_version(tmp_path, None, _level(HANDOVER)) == "ctx-1"
    superseded = {"since_last_run": {"available": True}, "level": {"levels_version": "sup-1"}}
    assert previous_levels_version(tmp_path, superseded) == "sup-1"
    (tmp_path / "prior-level.json").unlink()
    (tmp_path / "run-context.json").write_text("{", encoding="utf-8")
    assert previous_levels_version(tmp_path, None) is None


def test_the_diagrams_draw_the_runs_own_model() -> None:
    ladder, waffle = build_diagrams({"level": _level(CITIZEN)})
    assert ladder["svg"] is not None and waffle["svg"] is not None
    assert "Autonomous loops" not in ladder["svg"]  # citizen stops at Step 3
    default_ladder, _ = build_diagrams({"level": _level(None)})
    assert default_ladder["svg"] is not None and "Autonomous loops" in default_ladder["svg"]


def test_a_custom_model_draws_neither_diagram() -> None:
    custom = ModelChoice(CITIZEN.model | {"version": "my-1"}, "file", "my.toml")
    level = _level(custom)
    ctx = {"level": level, "headline": core_scans.headline(level, None, custom)}
    assert custom_run(ctx) and not custom_run({"level": level})
    diagrams = build_diagrams(ctx)
    assert [d["svg"] for d in diagrams] == [None, None]
    assert [d["skipped"] for d in diagrams] == [CUSTOM_NOT_DRAWN, CUSTOM_NOT_DRAWN]
    assert [d["svg"] is None for d in build_diagrams({})] == [True, True]  # no level: the old reasons
    # a version the report does not know, with no custom-model summary, draws the default model
    assert build_diagrams({"level": {**level, "levels_version": "fixture"}})[0]["svg"] is not None


def test_the_unit_rollup_names_the_default_model_under_a_profile() -> None:
    block: Any = {"lines": ["Units: Step 2 (lowest unit) · 1 of 2 measured units at Step 3", "Next unlock"],
                  "markdown": ""}
    labelled = units_label(block, HANDOVER)
    assert labelled["lines"][0].startswith("Units (default model): Step 2")
    assert labelled["markdown"] == "\n\n".join(labelled["lines"])
    assert units_label(block, None) is block
    assert units_label(block, resolve(None, None)) is block
    empty: Any = {"lines": [], "markdown": ""}
    assert units_label(empty, HANDOVER) is empty


def test_a_profile_run_after_a_default_run_compares_no_gate(ci2: Path) -> None:  # noqa: F811
    """A profile's first run after a default run has no committed record of its own, so it is
    that profile's baseline (#1256): no gate is compared with the default run's."""
    from assess_core import build_and_publish_run

    build_and_publish_run(repo_root=ci2, run_date="2026-11-22", non_interactive=True)
    ctx = build_and_publish_run(repo_root=ci2, run_date="2026-11-23", non_interactive=True, model=HANDOVER)
    since = ctx["since_last_run"]
    assert since["first_run"] is True
    assert since["gates_gained"] is None and since["gates_lost"] is None
    again = build_and_publish_run(repo_root=ci2, run_date="2026-11-24", non_interactive=True, model=HANDOVER)
    assert again["since_last_run"]["first_run"] is False
    assert model_changed_line(again["since_last_run"]) is None


def test_a_custom_run_after_a_default_run_compares_no_gate(tmp_path: Path) -> None:
    """A custom --levels run reads the local run-context.json, so a default run before it names
    another model and the gates are not compared (#1150)."""
    level = {"levels_version": "my-1", "conditions": []}
    previous = previous_levels_version_from(tmp_path, {"levels_version": DEFAULT_VERSION})
    block = across_models(_since(), level, previous)
    assert (block["gates_gained"], block["gates_lost"]) == (None, None)
    assert "Model changed since last run (default → my-1); gates not compared" in block["lines"]


def previous_levels_version_from(assess_dir: Path, level: Any) -> str | None:
    (assess_dir / "run-context.json").write_text(json.dumps({"level": level}), encoding="utf-8")
    return previous_levels_version(assess_dir, None, {"levels_version": "my-1"})


def test_a_degraded_headline_still_keeps_a_custom_level_off_the_default_diagrams() -> None:
    level = {"levels_version": "my-1", "step": 1, "next_step": None,
             "conditions": [{"id": "fresh-docs.1", "row": "fresh-docs", "level": 1, "grade": "measured",
                             "counted": True, "state": "met"}]}
    ctx = {"level": level, "headline": {"available": False}}
    assert custom_run(ctx)
    assert [d["skipped"] for d in build_diagrams(ctx)] == [CUSTOM_NOT_DRAWN, CUSTOM_NOT_DRAWN]
    default_ids = {**level, "conditions": [{**level["conditions"][0], "id": "layer-0-instructions.1"}]}
    assert not custom_run({"level": default_ids, "headline": {"available": False}})
    assert not custom_run({"level": level})  # no headline block: not a run context


@pytest.mark.parametrize("name", ["citizen", "handover"])
def test_two_runs_of_one_profile_compare_their_gates(name: str, ci2: Path) -> None:  # noqa: F811
    from assess_core import build_and_publish_run
    from test_headline import _commit

    choice = resolve(name, None)
    workflow = ci2 / ".github" / "workflows" / "ci.yml"
    text = workflow.read_text(encoding="utf-8")
    workflow.unlink()
    _commit(ci2, "2026-11-03T10:00:00Z", "no CI")
    first = build_and_publish_run(repo_root=ci2, run_date="2026-11-22", non_interactive=True, model=choice)
    assert first["level"]["step"] == 2
    workflow.write_text(text, encoding="utf-8")
    _commit(ci2, "2026-11-04T10:00:00Z", "CI")
    second = build_and_publish_run(repo_root=ci2, run_date="2026-11-23", non_interactive=True, model=choice)
    since = second["since_last_run"]
    assert second["level"]["step"] == 3
    assert since["gates_gained"] == ["ci_runs_tests"] and since["gates_lost"] == []
    assert model_changed_line(since) is None
    assert "Gates lost: none" in since["lines"]
    assert any(line.startswith("Gates gained: ") and "none" not in line for line in since["lines"])


def test_a_profile_run_writes_only_its_own_record(tmp_path: Path) -> None:
    import json

    from lib.prior_level import load_baseline

    default = {"run_id": "d", "head_sha": "a" * 40, "held_gates": ["instructions"]}
    write_prior_level(tmp_path, _level(None), default,
                      [{"path": "CLAUDE.md", "completed_sha": "b" * 40, "cost_before": 3, "runs_held": 0,
                        "credited": False, "saved": None, "gates": None, "credited_at": None}])
    before = json.loads((tmp_path / "prior-level.json").read_text(encoding="utf-8"))
    citizen = {"run_id": "c", "head_sha": "c" * 40, "held_gates": ["instructions", "ci_runs_tests"]}
    write_prior_level(tmp_path, _level(CITIZEN), citizen, [])
    after = json.loads((tmp_path / "prior-level.json").read_text(encoding="utf-8"))
    assert {k: v for k, v in after.items() if k != "models"} == before  # the default record and ledger kept
    assert after["models"]["citizen"]["baseline"] == citizen and "pruning" not in after["models"]["citizen"]
    assert load_baseline(tmp_path, _level(CITIZEN)) == citizen
    assert load_baseline(tmp_path, _level(None)) == load_baseline(tmp_path) == default
    assert load_baseline(tmp_path, _level(HANDOVER)) is None  # handover has no record yet
    write_prior_level(tmp_path, _level(None), default)  # a default run keeps every profile record
    assert load_baseline(tmp_path, _level(CITIZEN)) == citizen
    custom = {**_level(None), "levels_version": "team-1"}
    assert write_prior_level(tmp_path, custom, default) is None  # a custom model keeps no record
    degraded = {"available": False, "levels_version": CITIZEN.model["version"]}
    assert write_prior_level(tmp_path, degraded, citizen) is None  # #1158: every record kept
    assert load_baseline(tmp_path, _level(CITIZEN)) == citizen


def test_a_profile_run_leaves_an_unreadable_file_as_it_is(tmp_path: Path) -> None:
    """The default record in it is what the CI gate reads; only a default run replaces it."""
    path = tmp_path / "prior-level.json"
    path.write_text('{"prior_level_version": 4}', encoding="utf-8")
    citizen = {"run_id": "c", "head_sha": "c" * 40, "held_gates": []}
    assert write_prior_level(tmp_path, _level(CITIZEN), citizen) is None
    assert path.read_text(encoding="utf-8") == '{"prior_level_version": 4}'
    assert write_prior_level(tmp_path, _level(None), citizen) == path  # a default run rewrites it, as before


def test_a_profile_only_file_reads(tmp_path: Path) -> None:
    from lib.prior_level import load_baseline, load_prior_level

    citizen = {"run_id": "c", "head_sha": "c" * 40, "held_gates": ["instructions"]}
    write_prior_level(tmp_path, _level(CITIZEN), citizen)
    prior = load_prior_level(tmp_path)
    assert prior is not None and "levels_version" not in prior and prior["prior_level_version"] == 3
    assert load_baseline(tmp_path, _level(CITIZEN)) == citizen and load_baseline(tmp_path) is None


@pytest.mark.parametrize("removed", ["CLAUDE.md", ".github/workflows/ci.yml"])
def test_a_profile_run_keeps_the_default_baseline_so_ci_still_catches_a_lost_gate(removed: str, ci2: Path) -> None:  # noqa: F811
    """Default baseline, then a --profile run, then a default run that lost a gate: the gate fails."""
    import json

    from assess_core import build_and_publish_run
    from assess_gate import evaluate, format_verdict
    from lib.assess_config import load_gate_config
    from test_headline import _commit

    assess = ci2 / ".assess"
    build_and_publish_run(repo_root=ci2, run_date="2026-11-22", non_interactive=True)
    default = {k: v for k, v in json.loads((assess / "prior-level.json").read_text(encoding="utf-8")).items()
               if k != "models"}
    build_and_publish_run(repo_root=ci2, run_date="2026-11-23", non_interactive=True, model=HANDOVER)
    after = json.loads((assess / "prior-level.json").read_text(encoding="utf-8"))
    assert {k: v for k, v in after.items() if k != "models"} == default and "handover" in after["models"]
    (ci2 / removed).unlink()
    _commit(ci2, "2026-11-05T10:00:00Z", f"drop {removed}")
    (assess / "run-context.json").unlink()  # a CI checkout has the committed baseline only
    ctx = build_and_publish_run(repo_root=ci2, run_date="2026-11-24", non_interactive=True)
    lost = ctx["since_last_run"]["gates_lost"]
    assert lost and model_changed_line(ctx["since_last_run"]) is None
    (assess / "config.toml").write_text("[gate]\nfail_on_gate_loss = true\n", encoding="utf-8")
    verdict = evaluate(ctx, load_gate_config(ci2))
    assert verdict["failed"] is True
    assert f"FAIL gate lost: {lost[0]}" in format_verdict(verdict)


@pytest.mark.parametrize("name", ["citizen", "handover"])
def test_two_profile_runs_compare_on_the_committed_record_alone(name: str, ci2: Path) -> None:  # noqa: F811
    """#1184: with run-context.json deleted between them, as in CI or a fresh clone, the second
    run reads the first run's committed profile record and reports the gate gained."""
    from assess_core import build_and_publish_run
    from test_headline import _commit

    choice = resolve(name, None)
    workflow = ci2 / ".github" / "workflows" / "ci.yml"
    text = workflow.read_text(encoding="utf-8")
    workflow.unlink()
    _commit(ci2, "2026-11-03T10:00:00Z", "no CI")
    first = build_and_publish_run(repo_root=ci2, run_date="2026-11-22", non_interactive=True, model=choice)
    assert first["level"]["step"] == 2
    workflow.write_text(text, encoding="utf-8")
    _commit(ci2, "2026-11-04T10:00:00Z", "CI")
    (ci2 / ".assess" / "run-context.json").unlink()
    second = build_and_publish_run(repo_root=ci2, run_date="2026-11-23", non_interactive=True, model=choice)
    since = second["since_last_run"]
    assert since["gates_gained"] == ["ci_runs_tests"] and since["gates_lost"] == []
    assert since["previous_run_id"] == first["run_id"] and model_changed_line(since) is None


def test_a_profile_run_under_the_gate_loss_opt_in_reads_its_own_record(ci2: Path) -> None:  # noqa: F811
    """No profile record yet: "no baseline yet", as a default first run. With one committed, the
    profile run compares on its own gates and fails on a real loss."""
    from assess_core import build_and_publish_run
    from assess_gate import evaluate, format_verdict
    from lib.assess_config import load_gate_config
    from test_headline import _commit

    assess = ci2 / ".assess"
    build_and_publish_run(repo_root=ci2, run_date="2026-11-21", non_interactive=True)
    (assess / "run-context.json").unlink()  # a CI checkout: the committed default record only
    (assess / "config.toml").write_text("[gate]\nfail_on_gate_loss = true\n", encoding="utf-8")
    first = build_and_publish_run(repo_root=ci2, run_date="2026-11-22", non_interactive=True, model=CITIZEN)
    verdict = evaluate(first, load_gate_config(ci2))
    assert verdict["failed"] is False and verdict["no_baseline"] is True
    assert "no baseline yet" in format_verdict(verdict)
    (ci2 / "CLAUDE.md").unlink()
    _commit(ci2, "2026-11-05T10:00:00Z", "drop CLAUDE.md")
    (assess / "run-context.json").unlink()
    second = build_and_publish_run(repo_root=ci2, run_date="2026-11-23", non_interactive=True, model=CITIZEN)
    assert "instructions" in second["since_last_run"]["gates_lost"]
    verdict = evaluate(second, load_gate_config(ci2))
    assert verdict["failed"] is True
    assert "FAIL gate lost: instructions" in format_verdict(verdict)
