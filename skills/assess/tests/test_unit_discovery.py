"""Monorepo unit discovery (lib/unit_discovery.py, issue #862).

Units are the shallowest build-manifest folders; a workspace root is an
aggregator whose declared members are the units; manifest-only folders are
skipped; ``[[units]]`` in .assess/config.toml overrides. Counts-only output
carries counts and tokens, never a unit path.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from lib.counts_only import counts_only
from lib.scan_registry import SCANS
from lib.unit_discovery import PROBLEMS, SOURCES, scan_units


def _write(root: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


def _paths(block: dict[str, object]) -> list[str]:
    return [u["path"] for u in block["units"]]  # type: ignore[attr-defined,index]


def _skipped(block: dict[str, object]) -> dict[str | None, str]:
    return {s["path"]: s["problem"] for s in block["skipped"]}  # type: ignore[attr-defined,index]


# --- the acceptance fixtures --------------------------------------------------

THREE_UNITS = {
    "services/billing/go.mod": "module billing\n",
    "services/billing/main.go": "package main\n",
    "services/billing/CLAUDE.md": "# Billing\n",
    "services/search/pom.xml": "<project/>\n",
    "services/search/src/main/java/App.java": "class App {}\n",
    "web/package.json": '{"name": "web"}\n',
    "web/src/index.ts": "export {}\n",
    # manifest-only aggregator: a BOM with no sources
    "bom/pom.xml": "<project><packaging>pom</packaging></project>\n",
    # a manifest inside a unit is part of that unit
    "web/tools/package.json": '{"name": "tools"}\n',
    "web/tools/run.js": "1\n",
    "README.md": "# Repo\n",
}


def test_a_three_unit_repository_has_three_units_and_skips_the_bom(tmp_path: Path):
    block = scan_units(_write(tmp_path, THREE_UNITS))
    assert block["source"] == "discovered"
    assert _paths(block) == ["services/billing", "services/search", "web"]
    assert block["unit_count"] == 3
    assert [u["ecosystem"] for u in block["units"]] == ["go", "maven", "npm"]
    assert {u["origin"] for u in block["units"]} == {"manifest"}
    assert _skipped(block) == {"bom": "manifest_only"}
    assert block["aggregators"] == [] and block["aggregator_count"] == 0


def _codex_shaped(root: Path) -> Path:
    """openai/codex: a root pnpm workspace plus a Cargo workspace in codex-rs."""
    crates = ["core", "tui", "exec-server", "code-mode-host", "rollout-trace"]
    files = {
        "package.json": '{"name": "codex-monorepo", "private": true}\n',
        "pnpm-workspace.yaml": "packages:\n  - codex-cli\n  - 'sdk/*'\n  - '!sdk/old'\n",
        "codex-cli/package.json": '{"name": "@openai/codex"}\n',
        "codex-cli/bin/codex.js": "1\n",
        "sdk/typescript/package.json": "{}\n",
        "sdk/typescript/src/index.ts": "1\n",
        "sdk/old/package.json": "{}\n",
        "sdk/old/index.js": "1\n",
        "codex-rs/Cargo.toml": (
            '[workspace]\nmembers = ["core", "tui", "exec-server", "code-mode-host", "rollout-trace",'
            ' "utils/*", "gone"]\nexclude = ["utils/skip"]\nresolver = "2"\n'
        ),
        "codex-rs/utils/pty/Cargo.toml": "[package]\nname='pty'\n",
        "codex-rs/utils/pty/src/lib.rs": "\n",
        "codex-rs/utils/skip/Cargo.toml": "[package]\nname='skip'\n",
        "codex-rs/utils/skip/src/lib.rs": "\n",
        "codex-rs/exec-server/src/sandbox_integrity/AGENTS.md": "# nested\n",
        "AGENTS.md": "# root\n",
    }
    for crate in crates:
        files[f"codex-rs/{crate}/Cargo.toml"] = f"[package]\nname = '{crate}'\n"
        files[f"codex-rs/{crate}/src/lib.rs"] = "\n"
    files["codex-rs/code-mode-host/AGENTS.md"] = "# crate\n"
    return _write(root, files)


def test_a_codex_shaped_repository_makes_each_workspace_member_a_unit(tmp_path: Path):
    block = scan_units(_codex_shaped(tmp_path))
    rust = [u for u in block["units"] if u["workspace"] == "codex-rs"]
    assert [u["path"] for u in rust] == [
        "codex-rs/code-mode-host", "codex-rs/core", "codex-rs/exec-server",
        "codex-rs/rollout-trace", "codex-rs/tui", "codex-rs/utils/pty",
    ]
    assert {u["workspace"] for u in rust} == {"codex-rs"}
    assert {u["origin"] for u in rust} == {"workspace_member"}
    node = [u for u in block["units"] if u["workspace"] == "."]
    assert [(u["path"], u["ecosystem"]) for u in node] == [
        ("codex-cli", "pnpm"), ("sdk/typescript", "pnpm"),
    ]
    # a folder a workspace excludes is a package of its own: a plain unit
    plain = {u["path"]: u["ecosystem"] for u in block["units"] if u["origin"] == "manifest"}
    assert plain == {"codex-rs/utils/skip": "cargo", "sdk/old": "npm"}
    assert block["unit_count"] == 10
    assert block["aggregators"] == [
        {"path": ".", "ecosystem": "pnpm", "member_count": 2},
        {"path": "codex-rs", "ecosystem": "cargo", "member_count": 6},
    ]
    assert _skipped(block) == {"codex-rs/gone": "missing"}
    # nested instruction files are scopes inside a unit, not units
    assert "codex-rs/exec-server/src/sandbox_integrity" not in _paths(block)


# --- each workspace declaration ----------------------------------------------

@pytest.mark.parametrize("files, members", [
    ({"package.json": '{"workspaces": ["packages/*"]}', "packages/a/package.json": "{}",
      "packages/a/i.js": "1"}, ["packages/a"]),
    ({"package.json": '{"workspaces": {"packages": ["apps/web"]}}', "apps/web/package.json": "{}",
      "apps/web/i.ts": "1"}, ["apps/web"]),
    ({"go.work": "go 1.22\n\nuse (\n\t./api // the api\n\t./worker\n)\nuse ./cli\n",
      "api/go.mod": "m", "api/a.go": "p", "worker/go.mod": "m", "worker/w.go": "p",
      "cli/go.mod": "m", "cli/c.go": "p"}, ["api", "cli", "worker"]),
    ({"pyproject.toml": '[tool.uv.workspace]\nmembers = ["packages/*"]\nexclude = ["packages/x"]\n',
      "packages/a/pyproject.toml": "", "packages/a/a.py": "", "packages/x/pyproject.toml": "",
      "packages/x/x.py": ""}, ["packages/a", "packages/x"]),
    ({"settings.gradle.kts": 'rootProject.name = "r"\ninclude(":app", ":lib:core")\nincludeBuild("b")\n',
      "app/build.gradle.kts": "", "app/src/A.kt": "", "lib/core/build.gradle.kts": "",
      "lib/core/src/C.kt": "", "b/build.gradle": "", "b/B.java": ""}, ["app", "b", "lib/core"]),
    ({"pom.xml": "<project><modules><module>web</module><!-- <module>old</module> -->"
      "<module>bom</module></modules></project>",
      "web/pom.xml": "<project/>", "web/src/W.java": "", "bom/pom.xml": "<project/>"}, ["web"]),
    ({"Cargo.toml": '[workspace]\nmembers = ["crates/**"]\n',
      "crates/a/Cargo.toml": "", "crates/a/src/lib.rs": "", "crates/group/b/Cargo.toml": "",
      "crates/group/b/src/lib.rs": ""}, ["crates/a", "crates/group/b"]),
], ids=["npm", "npm-packages", "go-work", "uv", "gradle", "maven", "cargo-globstar"])
def test_each_workspace_kind_makes_its_members_units(tmp_path: Path, files: dict[str, str],
                                                     members: list[str]):
    block = scan_units(_write(tmp_path, files))
    assert _paths(block) == members
    assert [a["path"] for a in block["aggregators"]] == ["."]


def test_gradle_include_build_is_a_separate_shallowest_unit_not_a_member(tmp_path: Path):
    block = scan_units(_write(tmp_path, {
        "settings.gradle": "include 'app'\nincludeBuild 'b'\n",
        "app/build.gradle": "", "app/A.java": "", "b/build.gradle": "", "b/B.java": "",
    }))
    origins = {u["path"]: u["origin"] for u in block["units"]}
    assert origins == {"app": "workspace_member", "b": "manifest"}


def test_a_settings_only_pnpm_workspace_file_is_no_workspace(tmp_path: Path):
    block = scan_units(_write(tmp_path, {
        "pnpm-workspace.yaml": "onlyBuiltDependencies:\n  - esbuild\n",
        "package.json": "{}", "src/index.ts": "",
    }))
    assert _paths(block) == ["."] and block["aggregators"] == []


def test_a_pnpm_flow_list_is_read_and_an_unparseable_value_is_named(tmp_path: Path):
    flow = scan_units(_write(tmp_path / "flow", {
        "pnpm-workspace.yaml": "packages: ['apps/*', \"!apps/old\"]\n",
        "package.json": "{}", "apps/web/package.json": "{}", "apps/web/i.ts": "",
        "apps/old/package.json": "{}", "apps/old/i.ts": "",
    }))
    assert [(u["path"], u["workspace"]) for u in flow["units"]] == [
        ("apps/old", None), ("apps/web", "."),
    ]
    odd = scan_units(_write(tmp_path / "odd", {
        "pnpm-workspace.yaml": "packages: *anchor\n", "package.json": "{}", "i.ts": "",
    }))
    assert odd["unreadable_manifests"] == ["pnpm-workspace.yaml"] and _paths(odd) == []
    assert odd["unit_count"] is None and odd["unit_count_reason"] == "unreadable_manifest"


@pytest.mark.parametrize("settings", [
    'include(\n    ":app", // the app\n    ":lib"\n)\n',
    "include ':app',\n        ':lib'\n",
], ids=["kotlin-multiline", "groovy-continued"])
def test_a_gradle_include_over_several_lines_lists_every_member(tmp_path: Path, settings: str):
    block = scan_units(_write(tmp_path, {
        "settings.gradle.kts": settings, "app/build.gradle.kts": "", "app/A.kt": "",
        "lib/build.gradle.kts": "", "lib/L.kt": "",
    }))
    assert _paths(block) == ["app", "lib"]


@pytest.mark.parametrize("files", [
    {"go.work": "use (\n\t.\n\t./tools\n)\n", "go.mod": "m", "main.go": "",
     "tools/go.mod": "m", "tools/t.go": ""},
    {"Cargo.toml": '[package]\nname = "r"\n[workspace]\nmembers = ["tools"]\n', "src/main.rs": "",
     "tools/Cargo.toml": "", "tools/src/lib.rs": ""},
    {"pyproject.toml": '[project]\nname = "r"\n[tool.uv.workspace]\nmembers = ["tools"]\n',
     "r/__init__.py": "", "tools/pyproject.toml": "", "tools/t.py": ""},
], ids=["go-use-dot", "cargo-root-package", "uv-root-project"])
def test_a_root_that_is_also_a_member_is_a_unit(tmp_path: Path, files: dict[str, str]):
    block = scan_units(_write(tmp_path, files))
    assert _paths(block) == [".", "tools"]
    assert block["aggregators"][0]["member_count"] == 2
    assert block["units"][0]["workspace"] == "."


def test_a_root_member_with_no_source_of_its_own_is_manifest_only(tmp_path: Path):
    block = scan_units(_write(tmp_path, {
        "Cargo.toml": '[package]\nname = "r"\n[workspace]\nmembers = ["tools"]\n',
        "tools/Cargo.toml": "", "tools/src/lib.rs": "",
    }))
    assert _paths(block) == ["tools"] and _skipped(block) == {".": "manifest_only"}


@pytest.mark.parametrize("files, expected", [
    ({"package.json": '{"workspaces": ["packages/*"]}', "packages/package.json": "{}",
      "packages/a/package.json": "{}", "packages/a/i.js": "",
      "packages/b/package.json": "{}", "packages/b/i.js": ""},
     ["packages/a", "packages/b"]),
    ({"Cargo.toml": '[workspace]\nmembers = ["crates/*"]\n',
      "crates/Cargo.toml": "[package]\n", "crates/src/lib.rs": "",
      "crates/a/Cargo.toml": "", "crates/a/src/lib.rs": ""},
     ["crates", "crates/a"]),
], ids=["no-own-source", "own-source"])
def test_a_manifest_above_claimed_members_never_nests_units(tmp_path: Path, files: dict[str, str],
                                                            expected: list[str]):
    block = scan_units(_write(tmp_path, files))
    assert _paths(block) == expected
    if "crates" in expected:  # it owns src/, so it is a unit of those files beside crates/a
        assert block["units"][0]["origin"] == "manifest"
    else:
        assert _skipped(block) == {"packages": "manifest_only"}


def test_a_member_inside_an_earlier_unit_is_skipped_as_claimed(tmp_path: Path):
    block = scan_units(_write(tmp_path, {
        "lib/go.mod": "m", "lib/l.go": "",
        "tools/go.work": "use (\n\t.\n\t../lib\n)\n", "tools/go.mod": "m", "tools/t.go": "",
    }))
    assert _paths(block) == ["lib", "tools"]
    assert _skipped(block) == {"lib": "claimed"}


def test_a_maven_member_that_aggregates_is_one_unit(tmp_path: Path):
    block = scan_units(_write(tmp_path, {
        "pom.xml": "<project><modules><module>webserver</module></modules></project>",
        "webserver/pom.xml": "<project><modules><module>http</module></modules></project>",
        "webserver/http/pom.xml": "<project/>", "webserver/http/src/H.java": "",
    }))
    assert _paths(block) == ["webserver"]


def test_a_single_package_repository_is_one_root_unit(tmp_path: Path):
    block = scan_units(_write(tmp_path, {
        "pyproject.toml": "[project]\nname='x'\n", "src/x/__init__.py": "",
        "tests/fixtures/demo/package.json": "{}", "tests/fixtures/demo/i.js": "",
    }))
    assert _paths(block) == ["."]
    assert block["units"][0]["ecosystem"] == "python"


def test_a_repository_without_a_manifest_has_no_units(tmp_path: Path):
    block = scan_units(_write(tmp_path, {"README.md": "# docs\n", "notes/a.md": "x"}))
    assert block["unit_count"] == 0 and block["units"] == [] and block["skipped"] == []


def test_a_member_outside_the_repository_is_skipped(tmp_path: Path):
    block = scan_units(_write(tmp_path, {
        "Cargo.toml": '[workspace]\nmembers = ["../elsewhere", "a"]\n',
        "a/Cargo.toml": "", "a/src/lib.rs": "",
    }))
    assert _paths(block) == ["a"]
    assert _skipped(block) == {"../elsewhere": "outside_repo"}


def test_an_unparseable_workspace_manifest_makes_the_count_unknown(tmp_path: Path):
    block = scan_units(_write(tmp_path, {
        "Cargo.toml": '[workspace\nmembers = ["crates/*"]', "crates/a/Cargo.toml": "",
        "crates/a/src/lib.rs": "", "crates/b/Cargo.toml": "", "crates/b/src/lib.rs": "",
        "web/package.json": "{not json", "web/i.js": "",
    }))
    assert block["unit_count"] is None and block["unit_count_reason"] == "unreadable_manifest"
    assert block["unreadable_manifests"] == ["Cargo.toml"]
    assert block["aggregators"] == [{"path": ".", "ecosystem": "cargo", "member_count": None}]
    assert _paths(block) == []


def test_a_plain_unparseable_leaf_manifest_is_skipped_and_the_count_stays_known(tmp_path: Path):
    block = scan_units(_write(tmp_path, {"web/package.json": "{not json", "web/i.js": ""}))
    assert block["unit_count"] == 1 and block["unreadable_manifests"] == []
    assert _paths(block) == ["web"]
    assert _skipped(block) == {"web/package.json": "unparseable_leaf"}


PNPM_WITH_BROKEN_FIXTURE = {
    "package.json": '{"name": "mono", "private": true}',
    "pnpm-workspace.yaml": "packages:\n  - 'packages/*'\n",
    "packages/a/package.json": '{"name": "a"}', "packages/a/i.ts": "",
    "packages/b/package.json": '{"name": "b"}', "packages/b/i.ts": "",
    # a deliberately malformed fixture outside the member globs
    "tools/fx/package.json": "{not json", "tools/fx/case.js": "",
}


def test_a_broken_fixture_outside_the_member_globs_leaves_the_count_known(tmp_path: Path):
    block = scan_units(_write(tmp_path, PNPM_WITH_BROKEN_FIXTURE))
    assert block["unit_count"] == 3 and block["unit_count_reason"] is None
    assert block["unreadable_manifests"] == []
    assert _paths(block) == ["packages/a", "packages/b", "tools/fx"]
    assert _skipped(block)["tools/fx/package.json"] == "unparseable_leaf"


@pytest.mark.parametrize("files, unreadable", [
    # a broken root Cargo.toml workspace
    ({"Cargo.toml": '[workspace\nmembers = ["a"]', "a/Cargo.toml": "", "a/src/lib.rs": ""},
     ["Cargo.toml"]),
    # a broken package.json at a workspace member
    ({"pnpm-workspace.yaml": "packages: ['packages/*']\n",
      "packages/a/package.json": "{not json", "packages/a/i.ts": ""}, ["packages/a/package.json"]),
    # a broken package.json beside a readable workspace declaration
    ({"sub/Cargo.toml": '[workspace]\nmembers = ["c"]', "sub/package.json": "{not json",
      "sub/c/Cargo.toml": "", "sub/c/src/lib.rs": ""}, ["sub/package.json"]),
    # a file named as a workspace manifest, away from the root
    ({"sub/pnpm-workspace.yaml": "packages: nope\n", "sub/i.ts": ""}, ["sub/pnpm-workspace.yaml"]),
    # a member shallower than its workspace, visited first as a plain candidate
    ({"deep/ws/package.json": '{"workspaces": ["../../lib"]}', "deep/ws/i.js": "",
      "lib/package.json": "{not json", "lib/i.js": ""}, ["lib/package.json"]),
])
def test_an_unparseable_manifest_that_could_change_the_units_nulls_the_count(
        tmp_path: Path, files: dict[str, str], unreadable: list[str]):
    block = scan_units(_write(tmp_path, files))
    assert block["unit_count"] is None and block["unit_count_reason"] == "unreadable_manifest"
    assert block["unreadable_manifests"] == unreadable
    assert "unparseable_leaf" not in _skipped(block).values()


def test_a_glob_matching_nothing_is_skipped_and_an_empty_workspace_is_a_plain_unit(tmp_path: Path):
    block = scan_units(_write(tmp_path, {
        "package.json": "{}", "pnpm-workspace.yaml": "packages: ['packages/*']\n", "src/i.ts": "",
    }))
    assert _paths(block) == ["."] and block["unit_count"] == 1
    assert _skipped(block) == {"packages/*": "no_match"}
    assert block["aggregators"] == []


@pytest.mark.parametrize("workspaces", ['{"nohoist": ["x"]}', "[]"])
def test_a_yarn_workspace_without_members_is_a_plain_unit(tmp_path: Path, workspaces: str):
    block = scan_units(_write(tmp_path, {
        "package.json": f'{{"name": "r", "workspaces": {workspaces}}}', "src/i.ts": "",
    }))
    assert _paths(block) == ["."] and block["units"][0]["origin"] == "manifest"


def test_an_npm_root_with_its_own_sources_is_a_unit_too(tmp_path: Path):
    block = scan_units(_write(tmp_path, {
        "package.json": '{"name": "r", "workspaces": ["packages/*"]}', "src/i.ts": "",
        "packages/a/package.json": "{}", "packages/a/i.ts": "",
    }))
    assert [(u["path"], u["workspace"]) for u in block["units"]] == [(".", "."), ("packages/a", ".")]


def test_a_member_that_is_a_workspace_is_an_aggregator_in_turn(tmp_path: Path):
    block = scan_units(_write(tmp_path, {
        "package.json": "{}", "pnpm-workspace.yaml": "packages:\n  - 'pkgs/*'\n",
        "pkgs/rs/package.json": '{"name": "rs"}',
        "pkgs/rs/Cargo.toml": '[workspace]\nmembers = ["a", "b"]\n',
        "pkgs/rs/a/Cargo.toml": "", "pkgs/rs/a/src/lib.rs": "",
        "pkgs/rs/b/Cargo.toml": "", "pkgs/rs/b/src/lib.rs": "",
        "pkgs/ui/package.json": "{}", "pkgs/ui/i.ts": "",
    }))
    assert [(u["path"], u["ecosystem"], u["workspace"]) for u in block["units"]] == [
        ("pkgs/rs/a", "cargo", "pkgs/rs"), ("pkgs/rs/b", "cargo", "pkgs/rs"), ("pkgs/ui", "pnpm", "."),
    ]
    assert [(a["path"], a["ecosystem"]) for a in block["aggregators"]] == [
        (".", "pnpm"), ("pkgs/rs", "cargo"),
    ]


def test_every_workspace_a_folder_declares_is_read(tmp_path: Path):
    block = scan_units(_write(tmp_path, {
        "Cargo.toml": '[workspace]\nmembers = ["crates/*"]\n',
        "package.json": '{"workspaces": ["npm/*"]}',
        "crates/core/Cargo.toml": "", "crates/core/src/lib.rs": "",
        "npm/cli/package.json": "{}", "npm/cli/i.js": "",
        "npm/wasm/package.json": "{}", "npm/wasm/i.js": "",
    }))
    assert [(u["path"], u["ecosystem"]) for u in block["units"]] == [
        ("crates/core", "cargo"), ("npm/cli", "npm"), ("npm/wasm", "npm"),
    ]
    assert [(a["ecosystem"], a["member_count"]) for a in block["aggregators"]] == [("cargo", 1), ("npm", 2)]


@pytest.mark.parametrize("root", [
    {"pyproject.toml": "[tool.ruff]\nline-length = 100\n"},
    {"package.json": '{"devDependencies": {"prettier": "3"}}'},
    {"package.json": '{"name": "acme-monorepo", "private": true, "devDependencies": {"husky": "9"}}'},
], ids=["ruff-only-pyproject", "dev-only-package-json", "named-private-root"])
def test_a_tooling_root_manifest_does_not_fold_the_repository(tmp_path: Path, root: dict[str, str]):
    block = scan_units(_write(tmp_path, root | {
        "svc/a/pyproject.toml": "[project]\nname = 'a'\n", "svc/a/a.py": "",
        "svc/b/go.mod": "m", "svc/b/b.go": "",
    }))
    assert _paths(block) == ["svc/a", "svc/b"]
    assert _skipped(block) == {".": "tooling_only"}


def test_a_package_json_with_an_entry_point_is_a_package(tmp_path: Path):
    block = scan_units(_write(tmp_path, {
        "package.json": '{"name": "lib", "main": "index.js"}', "index.js": "",
        "tools/go.mod": "m", "tools/t.go": "",
    }))
    assert _paths(block) == ["."]


def test_a_gradle_project_dir_moves_its_member(tmp_path: Path):
    block = scan_units(_write(tmp_path, {
        "settings.gradle": "include ':core'\nproject(':core').projectDir = file('modules/core')\n",
        "modules/core/build.gradle": "", "modules/core/C.java": "",
    }))
    assert _paths(block) == ["modules/core"] and block["skipped"] == []


def test_a_byte_order_mark_does_not_make_a_manifest_unreadable(tmp_path: Path):
    block = scan_units(_write(tmp_path, {
        "Cargo.toml": '\ufeff[workspace]\nmembers = ["a"]\n', "a/Cargo.toml": "\ufeff[package]\n",
        "a/src/lib.rs": "", "web/package.json": '\ufeff{"name": "web"}', "web/i.js": "",
    }))
    assert block["unreadable_manifests"] == [] and block["unit_count"] == 2
    assert _paths(block) == ["a", "web"]


def test_workspaces_that_list_each_other_do_not_recurse(tmp_path: Path):
    block = scan_units(_write(tmp_path, {
        "package.json": '{"workspaces": ["packages/*"]}',
        "packages/x/package.json": '{"workspaces": ["../*"]}', "packages/x/i.js": "",
        "packages/y/package.json": '{"workspaces": ["../*"]}', "packages/y/i.js": "",
    }))
    assert block["unit_count"] == 2 and _paths(block) == ["packages/x", "packages/y"]
    assert all(s["problem"] == "claimed" for s in block["skipped"])


def test_excluded_folders_hold_no_units(tmp_path: Path):
    root = _write(tmp_path, {
        "node_modules/dep/package.json": "{}", "node_modules/dep/i.js": "",
        "vendored/lib/go.mod": "m", "vendored/lib/l.go": "",
        "app/go.mod": "m", "app/a.go": "",
    })
    assert _paths(scan_units(root, ({"vendored"}, []))) == ["app"]


# --- [[units]] override -------------------------------------------------------

def test_declared_units_replace_discovery(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    root = _write(tmp_path, dict(THREE_UNITS) | {
        ".assess/config.toml": (
            '[[units]]\npath = "./services/"\nname = "Services"\n'
            '[[units]]\npath = "web"\n'
            '[[units]]\npath = "web"\n'
            '[[units]]\npath = "gone"\n'
            '[[units]]\nname = "no path"\n'
            '[[units]]\npath = "../up"\n'
        ),
    })
    block = scan_units(root)
    assert block["source"] == "config"
    assert [(u["path"], u["name"], u["origin"]) for u in block["units"]] == [
        ("services", "Services", "config"), ("web", None, "config"),
    ]
    assert [s["problem"] for s in block["skipped"]] == ["duplicate", "missing", "no_path", "no_path"]
    assert block["aggregators"] == []
    assert capsys.readouterr().err.count("[[units]] entry skipped") == 4


def test_no_valid_declared_unit_falls_back_to_discovery(tmp_path: Path):
    root = _write(tmp_path, dict(THREE_UNITS) | {".assess/config.toml": "units = 'web'\n"})
    block = scan_units(root)
    assert block["source"] == "discovered" and block["unit_count"] == 3
    assert block["skipped"][0] == {"path": None, "problem": "not_a_list"}


# --- registry and counts-only -------------------------------------------------

def test_the_units_block_is_a_core_scan_reading_the_excludes():
    spec = next(s for s in SCANS if s.key == "units")
    assert spec.reads == ("repo_root", "excludes") and not spec.network


def test_counts_only_never_carries_a_unit_path_or_name(tmp_path: Path):
    root = _codex_shaped(tmp_path)
    (root / ".assess").mkdir()
    block = scan_units(root)
    for salted in (False, True):
        out = counts_only({"units": block}, hash_paths=salted, salt="s")["units"]
        text = json.dumps(out)
        assert "codex" not in text and "sdk/" not in text and "gone" not in text
        assert out["unit_count"] == 10 and out["aggregator_count"] == 2
        assert out["unit_count_reason"] is None
        assert out["source"] == "discovered"
        assert [u["ecosystem"] for u in out["units"]].count("cargo") == 7
        assert {u["origin"] for u in out["units"]} == {"workspace_member", "manifest"}
        assert out["aggregators"][1] == {"ecosystem": "cargo", "member_count": 6}
        assert out["skipped"] == [{"problem": "missing"}]
        assert out["unreadable_manifests"] == 0


def test_counts_only_keeps_the_unknown_count_reason(tmp_path: Path):
    block = scan_units(_write(tmp_path, {"package.json": "{not json", "i.js": ""}))
    out = counts_only({"units": block})["units"]
    assert out["unit_count"] is None and out["unit_count_reason"] == "unreadable_manifest"
    assert out["aggregators"] == [{"ecosystem": "npm", "member_count": None}]
    assert out["unreadable_manifests"] == 1


def test_counts_only_carries_the_leaf_skip_code_and_no_path(tmp_path: Path):
    block = scan_units(_write(tmp_path, PNPM_WITH_BROKEN_FIXTURE))
    for salted in (False, True):
        out = counts_only({"units": block}, hash_paths=salted, salt="s")["units"]
        assert {"problem": "unparseable_leaf"} in out["skipped"]
        assert out["unit_count"] == 3 and out["unreadable_manifests"] == 0
        assert "tools" not in json.dumps(out) and "fx" not in json.dumps(out)


def test_codes_are_tokens():
    assert set(SOURCES) == {"discovered", "config"}
    assert all(code.isidentifier() for code in PROBLEMS)
