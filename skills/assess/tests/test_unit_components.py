"""Skill and plugin-component units (lib/unit_components.py, issue #1098).

Each folder holding a ``SKILL.md`` is a unit of kind ``skill``, shallowest first; a plugin
root's ``agents/`` and ``commands/`` are units of their own kind. Manifest discovery runs
around them: a manifest inside a component is part of it, a manifest folder above them is a
unit of its own files only. Every unit then gets its own readings, Step and roll-up through
the #862-#865 chain unchanged.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from lib.counts_only import counts_only
from lib.unit_components import KINDS, discover_components
from lib.unit_discovery import scan_units
from test_unit_discovery import _paths, _skipped, _write

REPO = Path(__file__).resolve().parents[3]

PLUGIN = {
    ".claude-plugin/plugin.json": '{"name": "p"}\n',
    "README.md": "# Plugin\n",
    "pyproject.toml": "[tool.ruff]\nline-length = 100\n",  # tooling only: claims nothing
    "agents/reviewer.md": "---\nname: reviewer\n---\n",
    "commands/go.md": "# go\n",
    "skills/plain/SKILL.md": "---\nname: plain\n---\n",
    "skills/plain/references/guide.md": "# Guide\n",
    "skills/tooled/SKILL.md": "---\nname: tooled\n---\n",
    "skills/tooled/pyproject.toml": '[project]\nname = "tooled"\n',
    "skills/tooled/scripts/run.py": "print(1)\n",
    "skills/tooled/tests/test_run.py": "def test_x():\n    pass\n",
    # a skill inside a skill and a plugin inside a skill are part of that skill
    "skills/tooled/inner/SKILL.md": "---\nname: inner\n---\n",
    "skills/tooled/bundle/.claude-plugin/plugin.json": "{}\n",
    "skills/tooled/bundle/agents/a.md": "# a\n",
    # test data is no unit, inside a skill or not
    "skills/tooled/tests/fixtures/sample/SKILL.md": "---\nname: sample\n---\n",
    "skills/tooled/examples/demo/SKILL.md": "---\nname: demo\n---\n",
}


def _kinds(block: dict[str, Any]) -> dict[str, tuple[str, str | None, str]]:
    return {u["path"]: (u["kind"], u["ecosystem"], u["origin"]) for u in block["units"]}


def test_each_skill_and_plugin_component_is_a_unit(tmp_path: Path) -> None:
    block = scan_units(_write(tmp_path, PLUGIN))
    assert _kinds(block) == {  # type: ignore[arg-type]
        "agents": ("agents", None, "component"),
        "commands": ("commands", None, "component"),
        "skills/plain": ("skill", None, "component"),
        # a skill with a build manifest is one unit, of kind skill, with the manifest's ecosystem
        "skills/tooled": ("skill", "python", "component"),
    }
    assert block["unit_count"] == 4
    assert _skipped(block) == {  # type: ignore[arg-type]
        ".": "tooling_only",
        "skills/tooled/bundle/agents": "claimed",
        "skills/tooled/inner": "claimed",
    }


def test_a_package_above_skills_is_a_unit_of_its_own_files(tmp_path: Path) -> None:
    files = {"pyproject.toml": '[project]\nname = "pkg"\n', "src/pkg.py": "x = 1\n",
             "skills/a/SKILL.md": "# a\n", "skills/a/scripts/a.py": "y = 2\n"}
    block = scan_units(_write(tmp_path, files))
    assert _kinds(block) == {".": ("package", "python", "manifest"),  # type: ignore[arg-type]
                             "skills/a": ("skill", None, "component")}


def test_a_package_whose_only_sources_sit_in_skills_is_no_unit(tmp_path: Path) -> None:
    files = {"pyproject.toml": '[project]\nname = "pkg"\n',
             "skills/a/SKILL.md": "# a\n", "skills/a/scripts/a.py": "y = 2\n"}
    block = scan_units(_write(tmp_path, files))
    assert _paths(block) == ["skills/a"]  # type: ignore[arg-type]
    assert _skipped(block) == {".": "manifest_only"}  # type: ignore[arg-type]


def test_a_repository_that_is_only_a_skill_is_one_unit(tmp_path: Path) -> None:
    files = {"SKILL.md": "# s\n", "scripts/run.py": "1\n", "references/guide.md": "# g\n",
             "examples/inner/SKILL.md": "# inner\n"}  # test data, no unit
    block = scan_units(_write(tmp_path, files))
    assert _kinds(block) == {".": ("skill", None, "component")}  # type: ignore[arg-type]
    assert block["unit_count"] == 1 and _skipped(block) == {}  # type: ignore[arg-type]


def test_a_root_skill_over_packages_keeps_every_package_a_unit(tmp_path: Path) -> None:
    """Maintainer decision on #1098: a root usage skill must not collapse a monorepo."""
    files = {"SKILL.md": "# s\n", "svc-a/go.mod": "module a\n", "svc-a/main.go": "package main\n",
             "svc-b/go.mod": "module b\n", "svc-b/main.go": "package main\n",
             "web/package.json": '{"name": "web"}\n', "web/index.js": "1\n",
             "skills/usage/SKILL.md": "# usage\n"}
    block = scan_units(_write(tmp_path, files))
    assert _kinds(block) == {  # type: ignore[arg-type]
        ".": ("skill", None, "component"),
        "skills/usage": ("skill", None, "component"),
        "svc-a": ("package", "go", "manifest"),
        "svc-b": ("package", "go", "manifest"),
        "web": ("package", "npm", "manifest"),
    }
    assert block["unit_count"] == 5 and _skipped(block) == {}  # type: ignore[arg-type]


def test_a_root_skill_beside_a_root_package_is_one_root_unit(tmp_path: Path) -> None:
    files = {"SKILL.md": "# s\n", "pyproject.toml": '[project]\nname = "r"\n', "src/r.py": "1\n",
             "svc/go.mod": "module s\n", "svc/main.go": "package main\n"}
    block = scan_units(_write(tmp_path, files))
    # a root package already holds the manifests below it (#862); the root skill only names its kind
    assert _kinds(block) == {".": ("skill", "python", "manifest")}  # type: ignore[arg-type]


def test_a_root_skill_over_a_tooling_root_is_the_root_unit_not_a_skip(tmp_path: Path) -> None:
    files = {"SKILL.md": "# s\n", "pyproject.toml": "[tool.ruff]\n",
             "svc/go.mod": "module s\n", "svc/main.go": "package main\n"}
    block = scan_units(_write(tmp_path, files))
    assert _kinds(block) == {".": ("skill", "python", "component"),  # type: ignore[arg-type]
                             "svc": ("package", "go", "manifest")}
    assert _skipped(block) == {}  # type: ignore[arg-type]


def test_a_skill_folder_named_like_test_data_is_still_a_skill(tmp_path: Path) -> None:
    names = ["spec", "demo", "e2e", "examples", "test"]
    block = scan_units(_write(tmp_path, {f"skills/{n}/SKILL.md": f"# {n}\n" for n in names}))
    assert _paths(block) == sorted(f"skills/{n}" for n in names)  # type: ignore[arg-type]


def test_hidden_agent_configuration_skills_are_no_units(tmp_path: Path) -> None:
    files = {"package.json": '{"name": "app", "workspaces": ["apps/*"]}\n',
             "apps/web/package.json": '{"name": "web"}\n', "apps/web/index.ts": "1\n",
             ".github/skills/release/SKILL.md": "# release\n",
             ".agents/skills/lint/SKILL.md": "# lint\n",
             ".agents/.claude-plugin/plugin.json": "{}\n", ".agents/agents/a.md": "# a\n"}
    block = scan_units(_write(tmp_path, files))
    assert _kinds(block) == {"apps/web": ("package", "npm", "workspace_member")}  # type: ignore[arg-type]


def test_a_claim_is_listed_once(tmp_path: Path) -> None:
    files = {"skills/a/SKILL.md": "# a\n", "skills/a/sub/SKILL.md": "# sub\n",
             "skills/a/sub/pyproject.toml": '[project]\nname = "sub"\n', "skills/a/sub/m.py": "1\n"}
    block = scan_units(_write(tmp_path, files))
    assert block["skipped"] == [{"path": "skills/a/sub", "problem": "claimed"}]


def test_a_workspace_member_that_is_a_skill_is_a_unit_not_a_skip(tmp_path: Path) -> None:
    files = {"package.json": '{"name": "root"}\n', "pnpm-workspace.yaml": "packages:\n  - skills/*\n",
             "skills/a/SKILL.md": "# a\n", "skills/a/package.json": '{"name": "a"}\n',
             "skills/a/index.js": "1\n", "skills/b/package.json": '{"name": "b"}\n', "skills/b/index.js": "1\n"}
    block = scan_units(_write(tmp_path, files))
    assert _kinds(block) == {"skills/a": ("skill", "npm", "component"),  # type: ignore[arg-type]
                             "skills/b": ("package", "pnpm", "workspace_member")}
    assert "skills/a" not in _skipped(block)  # type: ignore[arg-type]


def test_a_workspace_member_inside_a_skill_is_claimed_once(tmp_path: Path) -> None:
    files = {"package.json": '{"name": "root", "workspaces": ["skills/foo/pkg"]}\n',
             "skills/foo/SKILL.md": "# foo\n", "skills/foo/pkg/package.json": '{"name": "pkg"}\n',
             "skills/foo/pkg/index.js": "1\n"}
    block = scan_units(_write(tmp_path, files))
    assert _paths(block) == ["skills/foo"]  # type: ignore[arg-type]
    assert [s for s in block["skipped"] if s["path"] == "skills/foo/pkg"] == [
        {"path": "skills/foo/pkg", "problem": "claimed"}]


def test_a_skill_fixture_inside_a_package_is_no_unit(tmp_path: Path) -> None:
    files = {"pkg/pyproject.toml": '[project]\nname = "pkg"\n', "pkg/src/m.py": "x = 1\n",
             "pkg/tests/fixtures/sample/SKILL.md": "# sample\n",
             "pkg/tests/fixtures/plug/.claude-plugin/plugin.json": "{}\n",
             "pkg/tests/fixtures/plug/agents/a.md": "# a\n"}
    block = scan_units(_write(tmp_path, files))
    assert _kinds(block) == {"pkg": ("package", "python", "manifest")}  # type: ignore[arg-type]
    assert _skipped(block) == {}  # type: ignore[arg-type]


def test_agents_without_a_plugin_manifest_are_not_a_unit(tmp_path: Path) -> None:
    block = scan_units(_write(tmp_path, {"agents/a.md": "# a\n", "commands/c.md": "# c\n"}))
    assert block["units"] == [] and block["unit_count"] == 0


def test_a_nested_plugin_root_contributes_its_components(tmp_path: Path) -> None:
    files = {"plugins/p/.claude-plugin/plugin.json": "{}\n", "plugins/p/agents/a.md": "# a\n",
             "plugins/p/skills/s/SKILL.md": "# s\n", "plugins/q/.claude-plugin/plugin.json": "{}\n",
             "plugins/q/commands/c.md": "# c\n"}
    block = scan_units(_write(tmp_path, files))
    assert _kinds(block) == {  # type: ignore[arg-type]
        "plugins/p/agents": ("agents", None, "component"),
        "plugins/p/skills/s": ("skill", None, "component"),
        "plugins/q/commands": ("commands", None, "component"),
    }


def test_a_repository_without_components_reads_package_units_only(tmp_path: Path) -> None:
    files = {"a/go.mod": "module a\n", "a/main.go": "package main\n",
             "b/package.json": '{"name": "b"}\n', "b/index.js": "1\n"}
    block = scan_units(_write(tmp_path, files))
    assert [(u["path"], u["kind"], u["origin"]) for u in block["units"]] == [
        ("a", "package", "manifest"), ("b", "package", "manifest")]


def test_declared_units_replace_discovery_and_name_their_kind(tmp_path: Path) -> None:
    files = {**PLUGIN, ".assess/config.toml":
             '[[units]]\npath = "skills/plain"\n[[units]]\npath = "agents"\n'}
    block = scan_units(_write(tmp_path, files))
    assert block["source"] == "config"
    assert [(u["path"], u["kind"]) for u in block["units"]] == [
        ("skills/plain", "skill"), ("agents", "package")]


def test_discover_components_is_pure_over_the_file_list() -> None:
    files = ["s/SKILL.md", "s/t/SKILL.md", ".claude-plugin/plugin.json", "agents/a.md"]
    dirs = frozenset({".", "s", "s/t", ".claude-plugin", "agents"})
    found = discover_components(files, dirs)
    assert [(c.path, c.kind) for c in found.units] == [("agents", "agents"), ("s", "skill")]
    assert found.claimed == ["s/t"]
    assert set(KINDS) == {"package", "skill", "agents", "commands"}


@pytest.mark.parametrize("salted", [False, True])
def test_counts_only_keeps_the_kind_and_never_a_skill_name(tmp_path: Path, salted: bool) -> None:
    block = scan_units(_write(tmp_path, PLUGIN))
    out = counts_only({"units": block}, hash_paths=salted, salt="s")["units"]
    assert [u["kind"] for u in out["units"]] == ["agents", "commands", "skill", "skill"]
    assert "plain" not in str(out) and "tooled" not in str(out)


def test_this_repository_lists_each_skill_as_a_unit() -> None:
    """The issue's fixture: this repository at main, one unit per ``skills/<name>/``."""
    if not (REPO / ".claude-plugin" / "plugin.json").is_file():
        pytest.skip("not run from a checkout of the toolkit")
    block = scan_units(REPO)
    skills = {p.parent.relative_to(REPO).as_posix() for p in (REPO / "skills").glob("*/SKILL.md")}
    kinds = {u["path"]: u["kind"] for u in block["units"]}
    assert {p for p, k in kinds.items() if k == "skill"} == skills
    assert kinds["agents"] == "agents" and kinds["scripts"] == "package"


# --- the units chain: readings, Steps and the roll-up per skill -----------------------

@pytest.fixture(scope="module")
def run(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    from assess_core import build_run_context
    files = {**PLUGIN, "skills/tooled/CLAUDE.md": "# Tooled\n\nRun `uv run pytest`.\n"}
    repo = _write(tmp_path_factory.mktemp("plugin") / "repo", files)
    ctx: dict[str, Any] = build_run_context(repo_root=repo, run_date="2026-11-23",
                                            non_interactive=True)
    return ctx


def test_each_skill_gets_its_own_readings_step_and_roll_up(run: dict[str, Any]) -> None:
    paths = ["agents", "commands", "skills/plain", "skills/tooled"]
    readings = run["unit_readings"]
    assert [u["path"] for u in readings["units"]] == paths
    origins = {u["path"]: {c["id"]: c["origin"] for c in u["conditions"]} for u in readings["units"]}
    # the skill's own instruction file is its own evidence; root CI is inherited
    assert origins["skills/tooled"]["layer-0-instructions.1"] == "unit"
    assert origins["skills/plain"]["layer-5-ci.1"] == "inherited"
    levels = run["unit_levels"]
    assert [u["path"] for u in levels["units"]] == paths
    assert all(u["step"] is not None for u in levels["units"])
    rollup = run["unit_rollup"]
    assert rollup["units"] == 4 and rollup["unlocks"]
