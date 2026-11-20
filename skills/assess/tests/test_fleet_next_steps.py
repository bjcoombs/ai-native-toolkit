"""Tests for the per-team next-steps pages (issue #571): lib/fleet_next_steps.py,
lib/next_step_guidance.py, lib/fleet_inputs.py and skills/fleet/scripts/fleet_next_steps.py.

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
from lib.fleet_inputs import FleetInputError, read_groups, read_jsonl, read_repos
from lib.next_step_guidance import guidance, measure_hint
from lib.rubric import load_rubric
from lib.rubric_signals import DEFAULT_RUBRIC, load_default_rubric, signal_path_validator
from test_fleet_cli import fleet_cli, froot  # noqa: F401 - froot is a fixture

FIXTURES = Path(__file__).parent / "fixtures" / "fleet_next_steps"
EXPECTED = FIXTURES / "expected"
RUBRIC_PATH = FIXTURES / "rubric.toml"
SCRIPT = Path(__file__).resolve().parents[2] / "fleet" / "scripts" / "fleet_next_steps.py"
RUBRIC = load_rubric(RUBRIC_PATH, signal_path_validator())


def _scans(*, bytes_: int = 900, lines: int = 40, named: int = 1, enforced: bool = True,
           mutation: bool = False, permissions: bool = True, tooling: bool = True) -> dict[str, Any]:
    return {
        "nested_instructions": {"available": True, "budget": {"claude_code": {"bytes": bytes_, "lines": lines}}},
        "agent_environment": {"available": True, "check": {"named_total": named}},
        "coverage_gate": {"available": True, "enforced": enforced},
        "test_pressure": {"available": True, "mutation_run": mutation},
        "agent_ops": {"available": True, "summary": {"permissions_encoded": permissions}},
        "shared_tooling": {"available": tooling},
    }


def _row(repo: str, scans: dict[str, Any] | None, reason: str | None = None) -> FleetRow:
    return {"fleet_row_version": FLEET_ROW_VERSION, "repo": repo, "group": repo.rsplit("/", 1)[0],
            "status": "ok" if scans is not None else "failed", "reason": reason,
            "signals_schema_version": 1 if scans is not None else None, "scans": scans}


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
    _row("grpa/svc2", _scans(named=0, lines=340, permissions=False)),
    _row("grpa/svc3", None, "scan_timeout"),
    _row("grpb/lib1", _scans(named=0, enforced=False, tooling=False)),
    _row("grpc/sub/app", _scans(mutation=True)),
]


def fleet_run(out: Path) -> Path:
    """Write the fixture fleet run (repos.jsonl, groups.jsonl, settings.json) to ``out``."""
    settings = out / "settings.json"
    out.mkdir(parents=True, exist_ok=True)
    settings.write_text(json.dumps(SETTINGS), encoding="utf-8")
    write_rows(out, REPOS)
    write_groups(out, roll_up(REPOS, RUBRIC, load_settings(settings)))
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
    assert cli.main([str(tmp_path), "--rubric", str(RUBRIC_PATH), "--settings", str(settings)]) == 0
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
    assert cli.main([str(tmp_path / "one"), "--rubric", str(RUBRIC_PATH), "--settings", str(settings)]) == 0
    again = {p.relative_to(tmp_path / "one").as_posix(): p.read_text(encoding="utf-8")
             for p in sorted((tmp_path / "one").rglob("*.md"))}
    assert again == first


def test_page_levels_agree_with_groups_jsonl(tmp_path):
    pages = _pages(tmp_path)
    titles = {row["id"]: row["title"] for row in RUBRIC["rows"]}
    for group in read_groups(tmp_path):
        page = pages[ns.page_path(group["group"]).as_posix()]
        for entry in group["rubric"]:
            cell = "unknown" if entry["status"] == "unknown" else f"{entry['level']} of "
            assert f"| {ns.text(titles[entry['id']])} | {cell}" in page


def test_every_row_below_top_names_an_unmet_criterion_and_its_repositories(tmp_path):
    pages = _pages(tmp_path)
    tops = {row["id"]: len(row["levels"]) for row in RUBRIC["rows"]}
    titles = {row["id"]: ns.text(row["title"]) for row in RUBRIC["rows"]}
    checked = 0
    for group in read_groups(tmp_path):
        page = pages[ns.page_path(group["group"]).as_posix()]
        for entry in group["rubric"]:
            if entry["status"] != "level" or entry["level"] == tops[entry["id"]]:
                continue
            section = page.split(f"## {titles[entry['id']]}\n", 1)[1].split("\n## ", 1)[0]
            assert "\n1. **" in section
            assert ("Holding the group back" in section) == (entry["scope"] == "team")
            assert ("Applies to: group settings" in section) == (entry["scope"] == "org")
            checked += 1
    assert checked >= 5


def test_unknown_names_missing_signals_and_never_reads_as_failure(tmp_path):
    page = _pages(tmp_path)["teams/grpa.md"]
    section = page.split("## Runtime legibility\n", 1)[1].split("\n## ", 1)[0]
    assert "not a failure" in section
    assert "`observability.rung`: not measured in 3 of 3 repositories" in section
    assert "`grpa/svc3` (scan failed: `scan_timeout`)" in section
    assert "Holding the group back" not in section
    instructions = page.split("## Agent instructions\n", 1)[1].split("\n## ", 1)[0]
    assert "Level unknown (between 0 and 1 of 2)" in instructions
    assert "The repository scan failed in each of them" in instructions
    assert "cap the group at level 1" in instructions and "`grpa/svc2` (now 340)" in instructions
    grpb = _pages(tmp_path / "b")["teams/grpb.md"]
    assert "No ghreport settings matched this group" in grpb


def test_steps_are_single_change_with_repository_native_done_when(tmp_path):
    page = _pages(tmp_path)["teams/grpa.md"]
    steps = re.findall(r"^\d+\. \*\*(.+)$", page, flags=re.M)
    assert steps
    done = re.findall(r"^   - Done when: (.+)$", page, flags=re.M)
    assert any("." not in "".join(re.findall(r"`([^`]+)`", d)) or "CLAUDE.md" in d for d in done)
    signal_paths = {c["signal"] for row in RUBRIC["rows"] for lv in row["levels"] for c in lv["criteria"]}
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
    assert "0 of 3 repositories reach it; the rule needs 2" in section
    assert "`grpa/svc3` (scan failed: `scan_timeout`)" in section


def test_unmapped_signal_falls_back_to_the_expression(tmp_path):
    page = _pages(tmp_path)["teams/grpb.md"]
    assert "**Meet `shared_tooling.available` = true.** No repository-native wording" in page


# --- errors ------------------------------------------------------------------


def test_a_different_rubric_version_exits_2(tmp_path, capsys):
    settings = fleet_run(tmp_path)
    assert cli.main([str(tmp_path), "--rubric", str(DEFAULT_RUBRIC), "--settings", str(settings)]) == 2
    assert "rolled up with rubric 'next-steps-fixture-1'" in capsys.readouterr().err


def test_missing_settings_disagree_with_the_run_and_exit_2(tmp_path, capsys):
    fleet_run(tmp_path)
    assert cli.main([str(tmp_path), "--rubric", str(RUBRIC_PATH)]) == 2
    assert "pass the --rubric and --settings the fleet run used" in capsys.readouterr().err


def test_missing_output_exits_2(tmp_path, capsys):
    assert cli.main([str(tmp_path), "--rubric", str(RUBRIC_PATH)]) == 2
    assert "repos.jsonl: cannot read" in capsys.readouterr().err


def test_mismatched_group_sets_raise(tmp_path):
    fleet_run(tmp_path)
    groups = read_groups(tmp_path)
    with pytest.raises(ns.NextStepsError, match="different groups"):
        ns.build_pages(RUBRIC, read_repos(tmp_path), groups[:1], load_settings(tmp_path / "settings.json"))


def test_a_changed_row_set_raises(tmp_path):
    fleet_run(tmp_path)
    groups = read_groups(tmp_path)
    groups[0]["rubric"].append({**groups[0]["rubric"][0], "id": "extra"})
    with pytest.raises(ns.NextStepsError, match="different set of rubric rows"):
        ns.build_pages(RUBRIC, read_repos(tmp_path), groups, load_settings(tmp_path / "settings.json"))


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
    ('{"fleet_row_version": 99}', "expected 1"),
])
def test_read_jsonl_names_file_and_line(tmp_path, line, fault):
    path = tmp_path / "repos.jsonl"
    path.write_text("\n" + line + "\n", encoding="utf-8")
    with pytest.raises(FleetInputError, match=rf"repos.jsonl:2: .*{fault}"):
        read_jsonl(path, "fleet_row_version", FLEET_ROW_VERSION)


def test_readers_require_ids_and_sort(tmp_path):
    (tmp_path / "repos.jsonl").write_text('{"fleet_row_version": 1}\n', encoding="utf-8")
    with pytest.raises(FleetInputError, match="no repo or group id"):
        read_repos(tmp_path)
    (tmp_path / "groups.jsonl").write_text('{"fleet_group_version": 1, "group": "g"}\n', encoding="utf-8")
    with pytest.raises(FleetInputError, match="no group id or rubric list"):
        read_groups(tmp_path)
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


def test_every_default_rubric_signal_has_repository_native_wording():
    rubric = load_default_rubric()
    for row in rubric["rows"]:
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
    ("two_factor.value", "in", ["on", "full"], "one of `on`, `full`"),
    ("rulesets.value", "ne", "off", "from `off`"),
])
def test_guidance_templates_on_the_criterion(signal, op, value, needle):
    words = guidance(signal, op, value)
    assert words is not None and needle in words["change"] + words["done_when"]


@pytest.mark.parametrize(("signal", "op", "value"), [
    ("coverage_gate.enforced", "eq", False), ("observability.rung", "gte", 9),
    ("observability.rung", "lt", 2), ("nested_instructions.budget.claude_code.lines", "gt", 5),
    ("agent_environment.check.named_total", "gt", 3), ("instruction_claims.failed", "gt", 0),
    ("agent_environment.layer5_cap.applies", "eq", True), ("two_factor.value", "lt", 1),
    ("nested_instructions.budget.claude_code.bytes", "lt", 5), ("unknown.signal", "eq", 1),
    ("review_reality.approved_share", "lt", 0.5),
])
def test_guidance_declines_shapes_it_does_not_fit(signal, op, value):
    assert guidance(signal, op, value) is None


def test_measure_hints():
    assert "full `/assess` run" in measure_hint("observability.rung")
    assert "ghreport" in measure_hint("two_factor.value")
    assert "`coverage_gate` scan" in measure_hint("coverage_gate.enforced")


def test_default_rubric_toml_parses():
    assert tomllib.loads(DEFAULT_RUBRIC.read_text(encoding="utf-8"))["version"]


# --- end to end --------------------------------------------------------------


def test_real_fleet_run_with_the_default_rubric_renders_pages(froot, tmp_path):  # noqa: F811
    out = tmp_path / "out"
    assert fleet_cli.main([str(froot), "--rubric", str(DEFAULT_RUBRIC), "--out", str(out)]) == 0
    assert cli.main([str(out)]) == 0
    tops = {row["id"]: len(row["levels"]) for row in load_default_rubric()["rows"]}
    titles = {row["id"]: ns.text(row["title"]) for row in load_default_rubric()["rows"]}
    for group in read_groups(out):
        page = (out / ns.page_path(group["group"])).read_text(encoding="utf-8")
        for entry in group["rubric"]:
            section = page.split(f"## {titles[entry['id']]}\n", 1)[1].split("\n## ", 1)[0]
            if entry["status"] == "unknown":
                assert "not a failure" in section
            elif entry["level"] < tops[entry["id"]]:
                assert "\n1. **" in section and "Holding the group back" in section
    assert "No repository-native wording" not in "".join(
        p.read_text(encoding="utf-8") for p in out.rglob("*.md"))


def test_a_run_without_a_rubric_says_so(froot, tmp_path, capsys):  # noqa: F811
    out = tmp_path / "out"
    assert fleet_cli.main([str(froot), "--out", str(out)]) == 0
    assert cli.main([str(out)]) == 2
    assert "the fleet run had no --rubric" in capsys.readouterr().err


def test_index_links_are_percent_encoded():
    group = {"group": "my team", "repos": 1, "rubric": []}
    page = ns.index_page(RUBRIC, [group])  # type: ignore[list-item]
    assert "(teams/my%20team.md)" in page
