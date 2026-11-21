"""TypeScript/JavaScript import-graph builder (#709).

``fixtures/ts_workspace`` is a two-package workspace: ``@acme/core`` and
``@acme/app``, a JSONC ``tsconfig`` that ``extends`` a base for ``baseUrl`` and
declares a ``paths`` alias, CommonJS ``require`` in a ``.js`` / ``.jsx`` pair, and
the files the scan must skip (a test, a declaration file, a tool config).
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import assess_signals
from lib.headless_layers import structure
from lib.import_graph_builders import BUILDERS
from lib.import_graph_typescript import (
    TYPESCRIPT_BUILDER,
    export_surface,
    import_specifiers,
    read_jsonc,
    strip_comments,
)
from lib.import_graph import ModuleGraph
from lib.structure_graph import analyze_structure

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "ts_workspace"
APP = "packages/app/src"
CORE = "packages/core/src"


def _write(root: Path, rel: str, text: str = "") -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _build(root: Path, scope: Path | None = None,
           exclude: set[str] | None = None) -> ModuleGraph:
    graph = TYPESCRIPT_BUILDER.build(root.resolve(), exclude or set(), scope)
    assert graph is not None
    return graph


# --------------------------------------------------------------------------
# The workspace fixture
# --------------------------------------------------------------------------

def test_the_builder_is_registered() -> None:
    assert TYPESCRIPT_BUILDER in BUILDERS


def test_the_workspace_graph_resolves_every_import_form() -> None:
    g = _build(FIXTURE)
    assert g.language == "typescript"
    assert g.modules == sorted([
        f"{APP}/config.ts", f"{APP}/index.ts", f"{APP}/main.ts",
        f"{APP}/widgets/button.jsx", f"{APP}/widgets/index.js",
        f"{CORE}/index.ts", f"{CORE}/math.ts", f"{CORE}/strings.ts",
    ])  # no math.test.ts, types.d.ts or vite.config.ts
    assert g.edges[f"{APP}/index.ts"] == {
        f"{CORE}/index.ts",    # workspace name, main dist/index.js -> src/index.ts
        f"{CORE}/strings.ts",  # workspace subpath
        f"{APP}/config.ts",    # tsconfig paths alias, baseUrl from the extended base
    }  # node:fs and lodash are external
    assert g.edges[f"{APP}/main.ts"] == {
        f"{APP}/widgets/index.js",  # multi-line import of a directory
        f"{CORE}/math.ts",          # relative across packages
        f"{APP}/config.ts",         # dynamic import()
    }
    assert g.edges[f"{CORE}/index.ts"] == {f"{CORE}/math.ts", f"{CORE}/strings.ts"}  # .js -> .ts
    assert g.edges[f"{APP}/widgets/index.js"] == {f"{APP}/widgets/button.jsx"}  # require
    assert g.edges[f"{APP}/config.ts"] == set()  # commented-out imports are not edges


def test_packages_front_doors_and_surfaces() -> None:
    g = _build(FIXTURE)
    assert g.package_of[f"{CORE}/math.ts"] == CORE
    assert g.package_of[f"{APP}/main.ts"] == APP
    assert g.package_of[f"{APP}/widgets/button.jsx"] == f"{APP}/widgets"
    assert g.front_doors == {f"{APP}/index.ts", f"{APP}/widgets/index.js", f"{CORE}/index.ts"}
    assert g.surfaces[f"{CORE}/math.ts"] == 2
    assert g.surfaces[f"{APP}/config.ts"] == 1
    assert g.surfaces[f"{APP}/widgets/button.jsx"] == 1
    assert g.surfaces[f"{CORE}/index.ts"] == 0  # re-exports are not its own definitions
    assert g.module_file[f"{CORE}/math.ts"] == (FIXTURE / CORE / "math.ts").resolve()


def test_a_two_package_workspace_gets_a_modularity_score() -> None:
    block = analyze_structure(FIXTURE).as_dict()
    assert block["available"] is True and block["language"] == "typescript"
    assert block["modularity_q"] is not None and block["modularity_reason"] is None
    assert block["cross_package_edges"] == 4
    assert block["front_door_ratio"] == 0.5
    assert {(e["importer"], e["imported"]) for e in block["internal_burrow_edges"]} == {
        (f"{APP}/index.ts", f"{CORE}/strings.ts"), (f"{APP}/main.ts", f"{CORE}/math.ts"),
    }
    assert any(fp["exposed_surface"] for fp in block["footprints"])
    assert structure(block) is block


def test_a_headless_counts_only_run_keeps_the_score(tmp_path: Path) -> None:
    repo = tmp_path / "ts"
    shutil.copytree(FIXTURE, repo)
    subprocess.run(["git", "init", "-q", str(repo)], check=True, capture_output=True)
    out = tmp_path / "out.json"
    assert assess_signals.main([str(repo), "--out", str(out), "--scans", "structure",
                                "--counts-only"]) == 0
    doc: dict[str, Any] = json.loads(out.read_text(encoding="utf-8"))
    block = doc["scans"]["structure"]
    assert block["available"] is True and block["language"] == "typescript"
    assert isinstance(block["modularity_q"], float)
    assert block["modularity_reason"] is None


def test_a_scope_keeps_its_modules_but_resolves_across_the_repo() -> None:
    g = _build(FIXTURE, scope=(FIXTURE / "packages" / "app").resolve())
    assert all(m.startswith("packages/app/") for m in g.modules)
    # A module outside the scope is not graphed, so an import of it is external.
    assert g.edges[f"{APP}/index.ts"] == {f"{APP}/config.ts"}


# --------------------------------------------------------------------------
# Small trees
# --------------------------------------------------------------------------

def test_no_source_means_no_graph(tmp_path: Path) -> None:
    _write(tmp_path, "README.md", "x")
    _write(tmp_path, "node_modules/dep/index.js", "")
    _write(tmp_path, "src/a.test.ts", "")
    assert TYPESCRIPT_BUILDER.build(tmp_path, set(), None) is None


def test_plain_javascript_reports_javascript(tmp_path: Path) -> None:
    _write(tmp_path, "lib/a.js", "const b = require('./b');\n")
    _write(tmp_path, "lib/b.cjs", "module.exports = 1;\n")
    g = _build(tmp_path)
    assert g.language == "javascript"
    assert g.edges["lib/a.js"] == {"lib/b.cjs"}
    assert g.package_of == {"lib/a.js": "lib", "lib/b.cjs": "lib"}  # no index: top dir
    assert analyze_structure(tmp_path).language == "javascript"


def test_configured_excludes_are_honoured(tmp_path: Path) -> None:
    _write(tmp_path, "gen/a.ts", "import './b';\n")
    _write(tmp_path, "src/b.ts", "")
    assert _build(tmp_path, exclude={"gen"}).modules == ["src/b.ts"]


def test_jsconfig_base_url_and_root_files(tmp_path: Path) -> None:
    _write(tmp_path, "jsconfig.json", '{"compilerOptions": {"baseUrl": "src"}}')
    _write(tmp_path, "src/util/index.js", "")
    _write(tmp_path, "main.js", "import u from 'util';\nimport x from 'missing';\n")
    g = _build(tmp_path)
    assert g.edges["main.js"] == {"src/util/index.js"}
    assert g.package_of["main.js"] == "."


def test_a_paths_exact_alias_and_longest_prefix(tmp_path: Path) -> None:
    _write(tmp_path, "tsconfig.json", json.dumps({"compilerOptions": {"paths": {
        "config": ["./cfg/main.ts"], "@/*": ["./src/*"], "@/deep/*": ["./deep/*"]}}}))
    _write(tmp_path, "cfg/main.ts")
    _write(tmp_path, "src/x.ts")
    _write(tmp_path, "deep/y.ts")
    _write(tmp_path, "a.ts", "import 'config';\nimport '@/x';\nimport '@/deep/y';\n")
    assert _build(tmp_path).edges["a.ts"] == {"cfg/main.ts", "src/x.ts", "deep/y.ts"}


def test_a_broken_config_or_manifest_degrades_to_relative_only(tmp_path: Path) -> None:
    _write(tmp_path, "tsconfig.json", "{ not json")
    _write(tmp_path, "package.json", "[]")
    _write(tmp_path, "a.ts", "import './b';\nimport 'b';\n")
    _write(tmp_path, "b.ts")
    assert _build(tmp_path).edges["a.ts"] == {"b.ts"}


def test_an_extends_cycle_terminates(tmp_path: Path) -> None:
    _write(tmp_path, "tsconfig.json", '{"extends": "./other"}')
    _write(tmp_path, "other.json", '{"extends": "./tsconfig.json"}')
    _write(tmp_path, "a.ts", "import 'x';\n")
    assert _build(tmp_path).edges["a.ts"] == set()


# --------------------------------------------------------------------------
# The text scan
# --------------------------------------------------------------------------

def test_strip_comments_keeps_strings() -> None:
    text = "const u = 'http://x'; // gone\n/* gone\n too */ const s = \"/* kept */\";\n`a${'//'}`"
    stripped = strip_comments(text)
    assert "gone" not in stripped
    assert "'http://x'" in stripped and '"/* kept */"' in stripped
    assert strip_comments("'unterminated\n// gone") == "'unterminated\n"
    assert strip_comments("'a\\'b' // c") == "'a\\'b' "


def test_import_specifiers_cover_each_form_once() -> None:
    text = (
        "import a from './a';\nimport type { B } from \"./b\";\nimport './side';\n"
        "export * from './c';\nexport { d } from './d';\nimport e = require('./e');\n"
        "const f = require('./f');\nconst g = await import('./g');\n"
        "import a2 from './a';\nobj.require('./not');\nimport q from './q?raw';\n"
    )
    found = import_specifiers(text)
    assert len(found) == len(set(found))
    assert set(found) == {"./a", "./b", "./c", "./d", "./side", "./e", "./f", "./g", "./q"}


def test_export_surface_counts_declarations_and_lists() -> None:
    text = ("export default class A {}\nexport async function b() {}\n"
            "export interface C {}\nexport { d, e as f, };\nexport { g } from './g';\n"
            "exports.h = 1;\nmodule.exports.i = 2;\n")
    assert export_surface(text) == 7


def test_read_jsonc_rejects_a_non_object(tmp_path: Path) -> None:
    _write(tmp_path, "a.json", "[1, 2,]")
    assert read_jsonc(tmp_path / "a.json") is None
    assert read_jsonc(tmp_path / "missing.json") is None



def test_a_regex_escape_does_not_open_a_block_comment() -> None:
    text = "const re = /a\\/*/;\nimport b from './b';\n"
    assert "import b from './b'" in strip_comments(text)
    assert import_specifiers(text) == ["./b"]


def test_only_tool_config_files_are_skipped(tmp_path: Path) -> None:
    for rel in ("src/app.config.ts", "src/database.config.ts", "vite.config.ts",
                "jest.config.js", "src/main.ts"):
        _write(tmp_path, rel)
    assert _build(tmp_path).modules == [
        "src/app.config.ts", "src/database.config.ts", "src/main.ts"]
