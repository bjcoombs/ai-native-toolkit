"""Tests for the per-team next-steps pages (issue #571): lib/fleet_next_steps.py,
lib/next_step_guidance.py, the readers in lib/fleet_view.py and skills/fleet/scripts/fleet_next_steps.py.

The golden pages live in ``tests/fixtures/fleet_next_steps/expected/``. Regenerate
them after an intended change, then review the diff:

    cd skills/assess && FLEET_NEXT_STEPS_REGEN=1 uv run --locked pytest tests/test_fleet_next_steps.py
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import tomllib
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from lib import fleet_next_steps as ns
from lib.fleet import FLEET_ROW_VERSION, FleetRow, write_rows
from lib.fleet_groups import load_settings, roll_up, write_groups
from lib.fleet_view import FleetViewError, load_groups, read_jsonl, read_repos
from lib.next_step_guidance import guidance, measure_hint
from lib.levels import load_levels
from lib.level_signals import DEFAULT_LEVELS, load_default_levels, signal_path_validator
from test_fleet_cli import fleet_cli, froot  # noqa: F401 - froot is a fixture

FIXTURES = Path(__file__).parent / "fixtures" / "fleet_next_steps"
EXPECTED = FIXTURES / "expected"
LEVELS_PATH = FIXTURES / "levels.toml"
SCRIPT = Path(__file__).resolve().parents[2] / "fleet" / "scripts" / "fleet_next_steps.py"
LEVELS = load_levels(LEVELS_PATH, signal_path_validator())


def _scans(*, bytes_: int = 900, lines: int = 40, named: int = 1, enforced: bool = True,
           mutation: bool = False, permissions: bool = True, tooling: bool = True,
           cap: tuple[str, ...] = ()) -> dict[str, Any]:
    return {
        "nested_instructions": {"available": True, "budget": {"claude_code": {"bytes": bytes_, "lines": lines}}},
        "agent_environment": {"available": True, "check": {"named_total": named},
                              "layer5_cap": {"applies": bool(cap), "unmet": list(cap)}},
        "coverage_gate": {"available": True, "configured": enforced},
        "test_pressure": {"available": True, "mutation_run": mutation},
        "agent_ops": {"available": True, "summary": {"permissions_encoded": permissions}},
        "shared_tooling": {"available": tooling},
    }


def _row(repo: str, scans: dict[str, Any] | None, reason: str | None = None) -> FleetRow:
    return {"fleet_row_version": FLEET_ROW_VERSION, "repo": repo, "group": repo.rsplit("/", 1)[0],
            "status": "ok" if scans is not None else "failed", "reason": reason,
            "signals_schema_version": 1 if scans is not None else None, "scans": scans,
            "active": True if scans is not None else None}


def _setting(value: str) -> dict[str, Any]:
    return {"value": value, "reason": None}


SETTINGS = [
    {"scope": "grpa", "provider": "gitlab", "two_factor": _setting("on"),
     "push_rules": _setting("deny_delete_tag, member_check"),
     "default_branch_protection": _setting("full"),
     "rulesets": {"value": "unknown", "reason": "endpoint missing"}},
    {"scope": "grpc", "provider": "gitlab", "two_factor": _setting("off"),
     "push_rules": _setting("on"), "default_branch_protection": _setting("full"),
     "rulesets": _setting("off")},
]

REPOS = [
    _row("grpa/svc1", _scans()),
    _row("grpa/svc2", _scans(named=0, lines=340, permissions=False, cap=("check_named",))),
    _row("grpa/svc3", None, "scan_timeout"),
    _row("grpb/lib1", _scans(named=0, enforced=False, tooling=False,
                             cap=("check_named", "setup_or_lockfile"))),
    _row("grpc/sub/app", _scans(mutation=True)),
]


def fleet_run(out: Path) -> Path:
    """Write the fixture fleet run (repos.jsonl, groups.jsonl, settings.json) to ``out``."""
    settings = out / "settings.json"
    out.mkdir(parents=True, exist_ok=True)
    settings.write_text(json.dumps(SETTINGS), encoding="utf-8")
    write_rows(out, REPOS)
    write_groups(out, roll_up(REPOS, LEVELS, load_settings(settings)))
    return settings


def _load_cli() -> ModuleType:
    spec = importlib.util.spec_from_file_location("fleet_next_steps_cli", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


cli = _load_cli()


def _pages(tmp_path: Path) -> dict[str, str]:
    settings = fleet_run(tmp_path)
    assert cli.main([str(tmp_path), "--levels", str(LEVELS_PATH), "--settings", str(settings)]) == 0
    return {p.relative_to(tmp_path).as_posix(): p.read_text(encoding="utf-8")
            for p in sorted(tmp_path.rglob("*.md"))}


# --- golden ------------------------------------------------------------------


def test_pages_match_the_golden_files(tmp_path):
    pages = _pages(tmp_path)
    if os.environ.get("FLEET_NEXT_STEPS_REGEN"):
        for stale in EXPECTED.rglob("*.md"):
            stale.unlink()
        for relative, body in pages.items():
            (EXPECTED / relative).parent.mkdir(parents=True, exist_ok=True)
            (EXPECTED / relative).write_text(body, encoding="utf-8")
    expected = {p.relative_to(EXPECTED).as_posix(): p.read_text(encoding="utf-8")
                for p in sorted(EXPECTED.rglob("*.md"))}
    assert pages == expected


def test_rerendering_the_same_run_is_byte_identical(tmp_path):
    first = _pages(tmp_path / "one")
    second = _pages(tmp_path / "two")
    assert first == second
    settings = tmp_path / "one" / "settings.json"
    assert cli.main([str(tmp_path / "one"), "--levels", str(LEVELS_PATH), "--settings", str(settings)]) == 0
    again = {p.relative_to(tmp_path / "one").as_posix(): p.read_text(encoding="utf-8")
             for p in sorted((tmp_path / "one").rglob("*.md"))}
    assert again == first


def test_page_levels_agree_with_groups_jsonl(tmp_path):
    pages = _pages(tmp_path)
    titles = {row["id"]: row["title"] for row in LEVELS["rows"]}
    for group in load_groups(tmp_path):
        page = pages[ns.page_path(group["group"]).as_posix()]
        for entry in group["levels"]:
            cell = "unknown" if entry["status"] == "unknown" else f"{entry['level']} of "
            assert f"| {ns.text(titles[entry['id']])} | {cell}" in page


def test_every_row_below_top_names_an_unmet_criterion_and_its_repositories(tmp_path):
    pages = _pages(tmp_path)
    tops = {row["id"]: len(row["levels"]) for row in LEVELS["rows"]}
    titles = {row["id"]: ns.text(row["title"]) for row in LEVELS["rows"]}
    checked = 0
    for group in load_groups(tmp_path):
        page = pages[ns.page_path(group["group"]).as_posix()]
        for entry in group["levels"]:
            if entry["status"] != "level" or entry["level"] == tops[entry["id"]]:
                continue
            section = page.split(f"## {titles[entry['id']]}\n", 1)[1].split("\n## ", 1)[0]
            assert "\n1. **" in section
            assert ("Holding the group back" in section) == (entry["scope"] == "team")
            assert ("Applies to: group settings" in section) == (entry["scope"] == "org")
            checked += 1
    assert checked >= 5


def test_unknown_names_unmeasured_signals_and_reads_as_a_measurement_gap(tmp_path):
    page = _pages(tmp_path)["teams/grpa.md"]
    section = page.split("## Runtime legibility\n", 1)[1].split("\n## ", 1)[0]
    assert section.startswith("\nIn place: ") and "This is a measurement gap:" in section
    assert "`observability.rung`: not measured in 3 of 3 repositories" in section
    assert "`grpa/svc3` (scan did not complete: `scan_timeout`)" in section
    assert "Holding the group back" not in section
    instructions = page.split("## Agent instructions\n", 1)[1].split("\n## ", 1)[0]
    assert "In place: nothing confirmed on this row yet; the level is not measured (between 0 and 1 of 2 once measured)" in instructions
    assert "The repository scan did not complete in each of them" in instructions
    assert "cap the group at level 1" in instructions and "`grpa/svc2` (now 340)" in instructions
    grpb = _pages(tmp_path / "b")["teams/grpb.md"]
    assert "No ghreport settings matched this group" in grpb


def test_steps_are_single_change_with_repository_native_done_when(tmp_path):
    page = _pages(tmp_path)["teams/grpa.md"]
    steps = re.findall(r"^\d+\. \*\*(.+)$", page, flags=re.M)
    assert steps
    done = re.findall(r"^   - Done when: (.+)$", page, flags=re.M)
    assert any("." not in "".join(re.findall(r"`([^`]+)`", d)) or "CLAUDE.md" in d for d in done)
    signal_paths = {c["signal"] for row in LEVELS["rows"] for lv in row["levels"] for c in lv["criteria"]}
    native = [d for d in done if not any(s in d for s in signal_paths)]
    assert native, "at least one done-when names no signal path"


def test_non_token_values_print_as_set_never_in_clear(tmp_path):
    pages = _pages(tmp_path)
    blob = "".join(pages.values())
    assert "deny_delete_tag" not in blob and "member_check" not in blob
    assert "   - Now: set." in pages["teams/grpa.md"]
    assert "   - Now: `off`." in pages["teams/grpc/sub.md"]


def test_failed_repository_below_target_is_listed_as_not_measured(tmp_path):
    page = _pages(tmp_path)["teams/grpa.md"]
    section = page.split("## Coverage \\| mutation\n", 1)[1].split("\n## ", 1)[0]
    assert "0 of 3 repositories already at level" in section and "the rule needs 2" in section
    assert "`grpa/svc3` (scan did not complete: `scan_timeout`)" in section


def test_unmapped_signal_falls_back_to_the_expression(tmp_path):
    page = _pages(tmp_path)["teams/grpb.md"]
    assert "**Meet `shared_tooling.available` = true.** No repository-native wording" in page


# --- errors ------------------------------------------------------------------


def test_a_different_levels_version_exits_2(tmp_path, capsys):
    settings = fleet_run(tmp_path)
    assert cli.main([str(tmp_path), "--levels", str(DEFAULT_LEVELS), "--settings", str(settings)]) == 2
    assert "rolled up with maturity model 'next-steps-fixture-1'" in capsys.readouterr().err


def test_missing_settings_disagree_with_the_run_and_exit_2(tmp_path, capsys):
    fleet_run(tmp_path)
    assert cli.main([str(tmp_path), "--levels", str(LEVELS_PATH)]) == 2
    assert "pass the --levels and --settings the fleet run used" in capsys.readouterr().err


def test_missing_output_exits_2(tmp_path, capsys):
    assert cli.main([str(tmp_path), "--levels", str(LEVELS_PATH)]) == 2
    assert "repos.jsonl: cannot read" in capsys.readouterr().err


def test_mismatched_group_sets_raise(tmp_path):
    fleet_run(tmp_path)
    groups = load_groups(tmp_path)
    with pytest.raises(ns.NextStepsError, match="different groups"):
        ns.build_pages(LEVELS, read_repos(tmp_path), groups[:1], load_settings(tmp_path / "settings.json"))


def test_a_changed_row_set_raises(tmp_path):
    fleet_run(tmp_path)
    groups = load_groups(tmp_path)
    groups[0]["levels"].append({**groups[0]["levels"][0], "id": "extra"})
    with pytest.raises(ns.NextStepsError, match="different set of maturity model rows"):
        ns.build_pages(LEVELS, read_repos(tmp_path), groups, load_settings(tmp_path / "settings.json"))


def test_stale_pages_are_removed_and_other_files_kept(tmp_path):
    stale = tmp_path / "teams" / "gone" / "old.md"
    stale.parent.mkdir(parents=True)
    stale.write_text("x", encoding="utf-8")
    keep = tmp_path / "teams" / "notes.txt"
    keep.write_text("keep", encoding="utf-8")
    _pages(tmp_path)
    assert not stale.exists() and not stale.parent.exists()
    assert keep.read_text(encoding="utf-8") == "keep"


@pytest.mark.parametrize("group", ["../x", "a//b", ".hidden", "a/..", "a\\b"])
def test_page_path_refuses_escaping_ids(group):
    with pytest.raises(ns.NextStepsError):
        ns.page_path(group)


def test_write_pages_refuses_a_path_outside(tmp_path):
    with pytest.raises(ns.NextStepsError, match="outside"):
        ns.write_pages(tmp_path / "out", {"../escape.md": "x"})


# --- inputs ------------------------------------------------------------------


@pytest.mark.parametrize(("line", "fault"), [
    ("not json", "not JSON"), ("[1]", "not a JSON object"),
    ('{"fleet_row_version": 99}', f"expected {FLEET_ROW_VERSION}"),
])
def test_read_jsonl_names_file_and_line(tmp_path, line, fault):
    path = tmp_path / "repos.jsonl"
    path.write_text("\n" + line + "\n", encoding="utf-8")
    with pytest.raises(FleetViewError, match=rf"repos.jsonl:2: .*{fault}"):
        read_jsonl(path, "fleet_row_version", FLEET_ROW_VERSION)


def test_an_empty_run_writes_an_empty_index(tmp_path):
    for name in ("repos.jsonl", "groups.jsonl"):
        (tmp_path / name).write_text("", encoding="utf-8")
    assert cli.main([str(tmp_path), "--levels", str(LEVELS_PATH)]) == 0
    assert (tmp_path / "teams.md").is_file()
    assert list(tmp_path.glob("teams/*.md")) == []


def test_readers_require_ids_and_sort(tmp_path):
    (tmp_path / "repos.jsonl").write_text(f'{{"fleet_row_version": {FLEET_ROW_VERSION}}}\n', encoding="utf-8")
    with pytest.raises(FleetViewError, match="no repo or group id"):
        read_repos(tmp_path)
    (tmp_path / "groups.jsonl").write_text('{"fleet_group_version": 1, "group": "g"}\n', encoding="utf-8")
    with pytest.raises(FleetViewError, match="needs a string 'group' and a 'levels' list"):
        load_groups(tmp_path)
    fleet_run(tmp_path)
    assert [r["repo"] for r in read_repos(tmp_path)] == sorted(r["repo"] for r in REPOS)


# --- formatting and guidance -------------------------------------------------


def test_formatting_is_counts_only_and_inert():
    assert ns.scalar(0.8) == "0.8" and ns.scalar(1 / 3) == "0.333" and ns.scalar(2.0) == "2"
    assert ns.scalar(float("nan")) == "not measured" and ns.scalar(None) == "not measured"
    assert ns.scalar(True) == "true" and ns.scalar("on") == "`on`" and ns.scalar("two words") == "set"
    assert ns.operand(["A", "B"]) == "[`A`, `B`]"
    assert ns.text("a|b *c*\n[d]") == "a\\|b \\*c\\* \\[d\\]"
    assert ns.code("a`b") == "`` a`b ``" and ns.code("a|b", in_table=True) == "`a\\|b`"


def test_every_default_levels_signal_has_repository_native_wording():
    maturity_model = load_default_levels()
    for row in maturity_model["rows"]:
        for level in row["levels"]:
            for c in level["criteria"]:
                words = guidance(c["signal"], c["op"], c["value"])
                assert words is not None, c
                assert c["signal"] not in words["done_when"]


@pytest.mark.parametrize(("signal", "op", "value", "needle"), [
    ("nested_instructions.budget.claude_code.lines", "lt", 150, "fewer than 150 lines"),
    ("observability.rung", "gt", 1, "OBSERVABILITY.md"),
    ("review_reality.reviewed_share", "gt", 0, "Have some merged"),
    ("review_reality.reviewed_share", "gte", 0.8, "at least 80% of the"),
    ("review_reality.reviewed_share", "gt", 0.5, "Have more than 50% of the"),
    ("review_reality.approved_share", "gte", 0.5, "(at least 50% of merged"),
    ("review_reality.approved_share", "gt", 0.5, "(more than 50% of merged"),
    ("two_factor.value", "in", ["on", "full"], "one of `on`, `full`"),
    ("rulesets.value", "ne", "off", "from `off`"),
    ("structure.modularity_q", "gte", 0.3, "at least 0.3"),
    ("structure.front_door_ratio", "gte", 0.9, "At least 90% of imports"),
    ("promissory_markers.families.suppression.stale", "lte", 10, "10 or fewer suppressions"),
    ("stats_summary.loc.p95", "lte", 500, "500 lines or fewer"),
    ("structure_drift.tier_0.matched_patterns", "gt", 0, "CODEOWNERS"),
    ("coverage_gate.threshold_meets_minimum", "eq", True, "below 50 does not count"),
    ("structure_drift.tier_0.boundary_patterns", "gt", 0, "tracked, non-excluded source file"),
    ("structure_drift.tier_0.boundary_patterns", "gte", 1, "a `CODEOWNERS` line per module path"),
])
def test_guidance_templates_on_the_criterion(signal, op, value, needle):
    words = guidance(signal, op, value)
    assert words is not None and needle in words["change"] + words["done_when"]


@pytest.mark.parametrize("signal", ["review_reality.reviewed_share", "review_reality.approved_share"])
def test_a_strict_bound_is_never_worded_at_least(signal):
    words = guidance(signal, "gt", 0.5)
    assert words is not None and "at least 50%" not in words["change"]


@pytest.mark.parametrize(("signal", "op", "value"), [
    ("coverage_gate.configured", "eq", False), ("observability.rung", "gte", 9),
    ("coverage_gate.threshold_meets_minimum", "eq", False),
    ("observability.rung", "lt", 2), ("nested_instructions.budget.claude_code.lines", "gt", 5),
    ("agent_environment.check.named_total", "gt", 3), ("instruction_claims.failed", "gt", 0),
    ("agent_environment.layer5_cap.applies", "eq", True), ("two_factor.value", "lt", 1),
    ("nested_instructions.budget.claude_code.bytes", "lt", 5), ("unknown.signal", "eq", 1),
    ("review_reality.approved_share", "lt", 0.5), ("structure.modularity_q", "lt", 0.3),
    ("promissory_markers.families.suppression.total", "eq", 0), ("stats_summary.loc.p95", "gt", 5),
    ("structure.front_door_ratio", "lt", 0.9), ("structure_drift.tier_0.boundary_patterns", "eq", 0),
    ("structure_drift.tier_0.matched_patterns", "eq", 0),
    ("promissory_markers.families.suppression.stale", "gt", 10),
])
def test_guidance_declines_shapes_it_does_not_fit(signal, op, value):
    assert guidance(signal, op, value) is None


def test_measure_hints():
    assert "`scan_failed`" in measure_hint("observability.rung")   # headless since #699
    assert "`not_git_root`" in measure_hint("doc_staleness.summary.stale_pct")
    assert "shallow clone" in measure_hint("doc_staleness.summary.stale_pct")
    assert "ghreport" in measure_hint("two_factor.value")
    assert "`coverage_gate` scan" in measure_hint("coverage_gate.configured")
    assert "${{ env.COV_MIN }}" in measure_hint("coverage_gate.threshold_meets_minimum")
    assert "`no_grimp`" in measure_hint("structure.modularity_q")
    assert "no_import_edges" in measure_hint("structure.modularity_q")
    hint = measure_hint("ci_checks.lint_runs")   # issue #782
    assert "check_target" in hint and "`pre-commit run`" in hint
    hint = measure_hint("ci_checks.enforced_check_runs")   # issue #775
    assert "included_jobs" in hint and "unknown_trigger" in hint
    assert "`push` or `pull_request`" in hint and "re-run the scan" in hint
    assert "unread_ci_system" in hint and "Jenkins" in hint and "CircleCI" in hint   # #787
    assert "cannot read those systems" in hint and "nothing in the repository needs to change" in hint
    assert "`enforced_basis: external_ci`" in hint and "`OWNERS_ALIASES`" in hint   # #928
    assert "`no_lizard`" in measure_hint("stats_summary.loc.p95")
    assert "full `/assess` run" not in measure_hint("structure_drift.tier_0.boundary_patterns")
    assert "no lint suppressions" in measure_hint("promissory_markers.families.suppression.total")
    assert "`DESIGN.md`" in measure_hint("structure_drift.tier_0.boundary_patterns")


def test_default_levels_toml_parses():
    assert tomllib.loads(DEFAULT_LEVELS.read_text(encoding="utf-8"))["version"]


# --- end to end --------------------------------------------------------------


def test_real_fleet_run_with_the_default_levels_renders_pages(froot, tmp_path):  # noqa: F811
    out = tmp_path / "out"
    assert fleet_cli.main([str(froot), "--levels", str(DEFAULT_LEVELS), "--out", str(out)]) == 0
    assert cli.main([str(out)]) == 0
    tops = {row["id"]: len(row["levels"]) for row in load_default_levels()["rows"]}
    titles = {row["id"]: ns.text(row["title"]) for row in load_default_levels()["rows"]}
    for group in load_groups(out):
        page = (out / ns.page_path(group["group"])).read_text(encoding="utf-8")
        for entry in group["levels"]:
            section = page.split(f"## {titles[entry['id']]}\n", 1)[1].split("\n## ", 1)[0]
            if entry["status"] == "unknown":
                assert "This is a measurement gap:" in section
            elif entry["level"] < tops[entry["id"]]:
                assert "\n1. **" in section and "Holding the group back" in section
    assert "No repository-native wording" not in "".join(
        p.read_text(encoding="utf-8") for p in out.rglob("*.md"))


def test_a_run_without_a_maturity_model_says_so(froot, tmp_path, capsys):  # noqa: F811
    out = tmp_path / "out"
    assert fleet_cli.main([str(froot), "--out", str(out)]) == 0
    assert cli.main([str(out)]) == 2
    assert "the fleet run had no --levels" in capsys.readouterr().err


def test_index_links_are_percent_encoded():
    group = {"group": "my team", "repos": 1, "levels": []}
    page = ns.index_page(LEVELS, [group])  # type: ignore[list-item]
    assert "(teams/my%20team.md)" in page


def test_layer5_step_names_only_the_unmet_part(tmp_path):
    pages = _pages(tmp_path)
    grpa = pages["teams/grpa.md"].split("## Agent environment\n", 1)[1]
    assert "**Name a check command that resolves" in grpa and "lockfile" not in grpa
    assert "`grpa/svc2` (now true; unmet: `check_named`)" in grpa
    grpb = pages["teams/grpb.md"].split("## Agent environment\n", 1)[1]
    assert "check command that resolves" in grpb and "lockfile" in grpb


@pytest.mark.parametrize(("unmet", "has_check", "has_lock"), [
    (["check_named"], True, False), (["setup_or_lockfile"], False, True),
    (["check_named", "setup_or_lockfile"], True, True), ([], True, True),
])
def test_layer5_wording_follows_the_unmet_parts(unmet, has_check, has_lock):
    words = guidance("agent_environment.layer5_cap.applies", "eq", False, unmet)
    assert words is not None
    assert ("check command" in words["change"]) == has_check
    assert ("lockfile" in words["change"]) == has_lock
    assert ("check command" in words["done_when"]) == has_check


def test_parts_reads_the_cap_tokens():
    from lib.next_step_guidance import parts
    scans = {"agent_environment": {"layer5_cap": {"unmet": ["setup_or_lockfile", "check_named"]}}}
    assert parts("agent_environment.layer5_cap.applies", scans) == ["check_named", "setup_or_lockfile"]
    assert parts("agent_environment.layer5_cap.applies",
                 {"agent_environment": {"layer5_cap": {"unmet": "check_named"}}}) == ["check_named"]
    assert parts("agent_environment.layer5_cap.applies", {}) == []
    assert parts("coverage_gate.configured", scans) == []


def test_lint_runs_guidance_names_a_linter_command_in_ci():
    """Issue #782 requirement 6: the done-when names a linter command in a CI file."""
    words = guidance("ci_checks.lint_runs", "eq", True)
    assert words is not None
    assert "`.gitlab-ci.yml`" in words["done_when"] and "`ruff check .`" in words["done_when"]


def test_a_level_0_row_opens_with_nothing_confirmed_in_team_and_org_branches(tmp_path):
    """Level 0 confirms nothing, so its opening line says so instead of reading as a level held (#743)."""
    pages = _pages(tmp_path)
    org = pages["teams/grpc/sub.md"].split("## Group settings\n", 1)[1].split("\n## ", 1)[0]
    assert org.startswith("\nIn place: nothing confirmed on this row yet (level 0 of ")
    team = pages["teams/grpb.md"].split("## Shared tooling\n", 1)[1].split("\n## ", 1)[0]
    assert team.startswith("\nIn place: nothing confirmed on this row yet (level 0 of ")
    assert not any(re.search(r"^In place: level 0 ", page, flags=re.M) for page in pages.values())
