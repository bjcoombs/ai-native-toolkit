"""The ghsync --from-list helper for CI include projects, lib/fleet/fleet_include_list.py and its CLI (issue #1356)."""
from __future__ import annotations

import base64
import importlib.util
import json
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

import lib.fleet.fleet_include_list as fil
from lib.ci.ci_include_items import IncludeItem
from lib.ci.ci_includes import MAX_DEPTH, MAX_FILES
from lib.command_index import MAX_CONFIG_BYTES
from lib.hosting.gh_cli import GhUnavailable

SCRIPT = Path(__file__).resolve().parents[2] / "fleet" / "scripts" / "fleet_include_list.py"


def _load_cli() -> ModuleType:
    spec = importlib.util.spec_from_file_location("fleet_include_list_cli", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


cli = _load_cli()


class FakeApi:
    """A GitLab API: ``projects`` maps a path to its CI files (path -> text); a path absent
    from it answers ``missing`` (default 404)."""

    def __init__(self, projects: dict[str, dict[str, str | None]],
                 missing: dict[str, fil.Access] | None = None) -> None:
        self.projects = projects
        self.missing = missing or {}
        self.read_from: list[str] = []

    def access(self, project: str) -> fil.ProjectAccess:
        if project in self.projects:
            return fil.ProjectAccess("reachable", project, "main")
        return fil.ProjectAccess(self.missing.get(project, "not_found"))

    def ci_files(self, project: str, ref: str) -> tuple[list[str], bool]:
        self.read_from.append(project)
        return list(self.projects[project]), True

    def read(self, project: str, file: str, ref: str) -> str | None:
        return self.projects[project].get(file)


def _include(*projects: str) -> str:
    return "include:\n" + "".join(f"  - project: {p}\n    file: /ci.yml\n" for p in projects)


def _item(kind: Any, source: str) -> IncludeItem:
    return IncludeItem(kind, source, (), None, 1)


@pytest.mark.parametrize(("kind", "source", "want"), [
    ("project", "Platform/CI-Templates", "platform/ci-templates"),
    ("project", "/grp/sub/tpl/", "grp/sub/tpl"),
    ("component", "gitlab.example.com/grp/components/lint@1.2", "grp/components"),
    ("component", "$CI_SERVER_FQDN/grp/sub/components/build@~latest", "grp/sub/components"),
    ("project", "$TEMPLATE_PROJECT", None),
    ("project", "single", None),
    ("component", "lint@1.0", None),
    ("local", "/ci/build.yml", None),
    ("template", "Auto-DevOps.gitlab-ci.yml", None),
])
def test_include_project_maps_each_kind(kind: Any, source: str, want: str | None) -> None:
    assert fil.include_project(_item(kind, source)) == want


def test_reachable_projects_are_listed_and_a_404_is_reported(tmp_path: Path) -> None:
    run = {"not_cloned_includes": [
        {"project": "grp/templates", "kind": "project", "repositories": 3},
        {"project": "grp/components", "kind": "component", "repositories": 2},
        {"project": "grp/gone", "kind": "project", "repositories": 1}]}
    (tmp_path / "run.json").write_text(json.dumps(run), encoding="utf-8")
    api = FakeApi({"grp/templates": {}, "grp/components": {}})
    out = tmp_path / "list.txt"
    assert cli.main([str(tmp_path), "--host", "https://gitlab.example.com/", "--output", str(out)], api) == 0
    text = out.read_text(encoding="utf-8")
    assert text == fil.HEADER + "grp/components\ngrp/templates\n"
    assert [line for line in text.splitlines() if not line.startswith("#")] == ["grp/components", "grp/templates"]


def test_the_report_names_each_unreachable_project_with_its_status(capsys: pytest.CaptureFixture[str],
                                                                  tmp_path: Path) -> None:
    run = {"not_cloned_includes": [{"project": p, "kind": "project", "repositories": 1}
                                   for p in ("grp/a", "grp/b", "grp/gone", "grp/secret")]}
    (tmp_path / "run.json").write_text(json.dumps(run), encoding="utf-8")
    api = FakeApi({"grp/a": {}, "grp/b": {}}, {"grp/secret": "no_access"})
    assert cli.main([str(tmp_path), "--host", "gitlab.example.com"], api) == 0
    shown = capsys.readouterr().out
    assert "4 CI include projects checked, 2 reachable" in shown
    assert "| `grp/gone` | not_found | the fleet run |" in shown
    assert "| `grp/secret` | no_access | the fleet run |" in shown
    assert "--host gitlab.example.com --from-list" in shown
    assert (tmp_path / "include-projects.txt").read_text(encoding="utf-8").endswith("grp/a\ngrp/b\n")


def test_a_nested_include_is_followed_to_the_depth_limit_and_no_further() -> None:
    chain = [f"grp/level{n}" for n in range(1, 9)]  # each includes the next
    api = FakeApi({p: {"ci.yml": _include(nxt)} for p, nxt in zip(chain, chain[1:], strict=False)})
    checked = fil.walk([chain[0]], api, max_depth=3)
    assert [(c.project, c.depth, c.via) for c in checked] == [
        ("grp/level1", 1, None), ("grp/level2", 2, "grp/level1"), ("grp/level3", 3, "grp/level2")]
    assert api.read_from == ["grp/level1", "grp/level2"]  # depth 3 is checked, not read


def test_the_default_depth_is_fleets_nested_include_depth() -> None:
    chain = [f"grp/level{n}" for n in range(1, 12)]
    api = FakeApi({p: {"ci.yml": _include(nxt)} for p, nxt in zip(chain, chain[1:], strict=False)})
    assert max(c.depth for c in fil.walk([chain[0]], api)) == MAX_DEPTH


def test_a_nested_project_is_checked_once_and_a_nested_404_names_its_includer() -> None:
    api = FakeApi({"grp/a": {"ci.yml": _include("grp/b", "grp/gone")},
                   "grp/b": {"x.yml": _include("grp/a", "grp/gone")}})
    checked = fil.walk(["grp/a"], api)
    assert [(c.project, c.status, c.via) for c in checked] == [
        ("grp/a", "reachable", None), ("grp/b", "reachable", "grp/a"), ("grp/gone", "not_found", "grp/a")]
    assert "| `grp/gone` | not_found | `grp/a` |" in fil.report(checked, "l.txt", "h")


def test_the_api_spelling_is_listed_when_it_differs_only_in_case() -> None:
    class Cased(FakeApi):
        def access(self, project: str) -> fil.ProjectAccess:
            return fil.ProjectAccess("reachable", {"grp/tpl": "Grp/Tpl", "grp/old": "grp/new"}[project], None)

    checked = fil.walk(["grp/tpl", "grp/old"], Cased({}))
    assert fil.list_file(checked) == fil.HEADER + "Grp/Tpl\ngrp/old\n"


@pytest.mark.parametrize("entry", [{"project": None, "kind": "project", "repositories": 1},
                                   {"project": "h:0123456789abcdef", "kind": "project", "repositories": 1}])
def test_a_counts_only_or_hashed_run_is_refused(entry: dict[str, Any], tmp_path: Path,
                                                capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / "run.json").write_text(json.dumps({"not_cloned_includes": [entry]}), encoding="utf-8")
    assert cli.main([str(tmp_path), "--host", "h"], FakeApi({})) == 2
    assert "--full" in capsys.readouterr().err
    assert not (tmp_path / "include-projects.txt").exists()


def test_seeds_dedupe_and_set_aside_a_path_that_is_no_project() -> None:
    assert fil.seeds({}) == ([], [])
    assert fil.seeds({"not_cloned_includes": [{"project": "G/A"}, {"project": "g/a"}]}) == (["g/a"], [])
    odd = [{"project": "$ci_project_namespace/ci-templates"}, {"project": "g/a"}, {"project": "solo"},
           {"project": "solo"}]
    assert fil.seeds({"not_cloned_includes": odd}) == (["g/a"], ["$ci_project_namespace/ci-templates", "solo"])
    with pytest.raises(fil.IncludeListError, match="not a list"):
        fil.seeds({"not_cloned_includes": {"project": "g/a"}})


def test_a_variable_path_in_the_run_is_reported_and_the_rest_checked(
        tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    run = {"not_cloned_includes": [{"project": "$ci_project_namespace/ci-templates"}, {"project": "grp/a"}]}
    (tmp_path / "run.json").write_text(json.dumps(run), encoding="utf-8")
    assert cli.main([str(tmp_path), "--host", "h"], FakeApi({"grp/a": {}})) == 0
    shown = capsys.readouterr().out
    assert "1 not looked up" in shown and "`$ci_project_namespace/ci-templates`" in shown
    assert (tmp_path / "include-projects.txt").read_text(encoding="utf-8").endswith("grp/a\n")


def test_missing_run_json_exits_2(tmp_path: Path) -> None:
    assert cli.main([str(tmp_path), "--host", "h"], FakeApi({})) == 2
    (tmp_path / "run.json").write_text("[]", encoding="utf-8")
    assert cli.main([str(tmp_path), "--host", "h"], FakeApi({})) == 2


def test_a_host_failure_exits_3_and_writes_nothing(tmp_path: Path) -> None:
    class Down(FakeApi):
        def access(self, project: str) -> fil.ProjectAccess:
            raise fil.IncludeListError("not_authenticated: glab is not logged in")

    (tmp_path / "run.json").write_text(json.dumps({"not_cloned_includes": [{"project": "g/a"}]}),
                                       encoding="utf-8")
    assert cli.main([str(tmp_path), "--host", "h"], Down({})) == 3
    assert not (tmp_path / "include-projects.txt").exists()


# --- GlabIncludeApi over a stubbed glab_api ------------------------------------------


def _b64(text: str) -> str:
    return base64.b64encode(text.encode()).decode()


def _stub(monkeypatch: pytest.MonkeyPatch, answers: dict[str, Any]) -> list[str]:
    calls: list[str] = []

    def fake(project: Any, path: str, cache: bool = True) -> Any:
        assert cache is False
        calls.append(path)
        got = answers.get(path)
        if isinstance(got, GhUnavailable):
            raise got
        if got is None:
            raise GhUnavailable(f"not_found: `glab api {path}` (404 Not Found)")
        return got

    monkeypatch.setattr(fil, "glab_api", fake)
    return calls


def test_glab_access_maps_each_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub(monkeypatch, {
        "projects/g%2Fok": {"path_with_namespace": "G/ok", "default_branch": "main"},
        "projects/g%2Fempty": {"path_with_namespace": "g/empty", "default_branch": None},
        "projects/g%2Fdenied": GhUnavailable("no_access: refused (403 Forbidden)"),
        "projects/g%2Fodd": GhUnavailable("glab_error: failed", "502"),
        "projects/g%2Flist": [],
        "projects/g%2Fslow": GhUnavailable("glab_timeout: `glab api` gave no answer within 20s"),
    })
    api = fil.GlabIncludeApi("gitlab.example.com")
    assert api.access("g/ok") == fil.ProjectAccess("reachable", "G/ok", "main")
    assert api.access("g/empty") == fil.ProjectAccess("reachable", "g/empty", None)
    assert api.access("g/gone").status == "not_found"
    assert api.access("g/denied").status == "no_access"
    assert api.access("g/odd").status == "error"
    assert api.access("g/list").status == "error"
    assert api.access("g/slow").status == "error"  # one slow project, not the whole host


def test_open_checks_the_login_first(monkeypatch: pytest.MonkeyPatch) -> None:
    hosts: list[str] = []
    monkeypatch.setattr(fil, "glab_auth", hosts.append)
    assert fil.GlabIncludeApi.open("gitlab.example.com").host == "gitlab.example.com"

    def refused(host: str) -> None:
        raise GhUnavailable("not_authenticated: glab is not logged in to h")

    monkeypatch.setattr(fil, "glab_auth", refused)
    with pytest.raises(fil.IncludeListError, match="not_authenticated"):
        fil.GlabIncludeApi.open("h")
    assert hosts == ["gitlab.example.com"]


def test_the_cli_exits_3_when_glab_is_not_logged_in(monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
                                                     capsys: pytest.CaptureFixture[str]) -> None:
    def refused(host: str) -> None:
        raise GhUnavailable("not_authenticated: glab is not logged in to h")

    monkeypatch.setattr(fil, "glab_auth", refused)
    (tmp_path / "run.json").write_text(json.dumps({"not_cloned_includes": [{"project": "g/a"}]}),
                                       encoding="utf-8")
    assert cli.main([str(tmp_path), "--host", "h"]) == 3
    assert "not_authenticated" in capsys.readouterr().err
    assert not (tmp_path / "include-projects.txt").exists()


def test_a_project_not_fully_read_is_reported() -> None:
    class Gaps(FakeApi):
        def ci_files(self, project: str, ref: str) -> tuple[list[str], bool]:
            files, _ = super().ci_files(project, ref)
            return files, project != "grp/trunc"

    api = Gaps({"grp/a": {"ci.yml": _include("grp/b", "grp/trunc"), "gone.yml": None},
                "grp/b": {f"f{n}.yml": "" for n in range(MAX_FILES + 1)}, "grp/trunc": {}})
    checked = fil.walk(["grp/a"], api)
    assert [(c.project, c.partial) for c in checked] == [("grp/a", True), ("grp/b", True), ("grp/trunc", True)]
    shown = fil.report(checked, "l.txt", "h")
    assert "3 reachable but not fully read" in shown and "`grp/trunc`" in shown
    whole = fil.walk(["grp/x"], FakeApi({"grp/x": {"ci.yml": ""}}))
    assert not whole[0].partial and "not fully read" not in fil.report(whole, "l.txt", "h")


def test_glab_host_failure_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub(monkeypatch, {"projects/g%2Fa": GhUnavailable("not_authenticated: glab is not logged in")})
    with pytest.raises(fil.IncludeListError, match="not_authenticated"):
        fil.GlabIncludeApi("h").access("g/a")


def test_glab_ci_files_pages_the_tree_and_keeps_yaml(monkeypatch: pytest.MonkeyPatch) -> None:
    base = "projects/g%2Fa/repository/tree?recursive=true&per_page=100"
    first = [{"type": "blob", "path": f"t/{n}.yml"} for n in range(99)] + [{"type": "tree", "path": "t"}]
    _stub(monkeypatch, {f"{base}&page=1&ref=main": first,
                        f"{base}&page=2&ref=main": [{"type": "blob", "path": "b.yaml"},
                                                    {"type": "blob", "path": "README.md"}, "junk"]})
    files, complete = fil.GlabIncludeApi("h").ci_files("g/a", "main")
    assert complete and len(files) == 100 and files[-1] == "b.yaml" and "README.md" not in files


def test_glab_ci_files_still_full_after_the_page_cap_is_incomplete(monkeypatch: pytest.MonkeyPatch) -> None:
    base = "projects/g%2Fa/repository/tree?recursive=true&per_page=100"
    full = [{"type": "blob", "path": "x.yml"}] * 100
    _stub(monkeypatch, {f"{base}&page={n}&ref=main": full for n in range(1, fil.TREE_MAX_PAGES + 1)})
    files, complete = fil.GlabIncludeApi("h").ci_files("g/a", "main")
    assert not complete and len(files) == 100 * fil.TREE_MAX_PAGES


def test_glab_ci_files_stops_on_a_refusal_or_a_bad_page(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub(monkeypatch, {})  # every page refused
    assert fil.GlabIncludeApi("h").ci_files("g/a", "main") == ([], False)
    _stub(monkeypatch, {"projects/g%2Fa/repository/tree?recursive=true&per_page=100&page=1&ref=main": {}})
    assert fil.GlabIncludeApi("h").ci_files("g/a", "main") == ([], False)


def test_glab_read_decodes_base64_and_refuses_the_rest(monkeypatch: pytest.MonkeyPatch) -> None:
    files = "projects/g%2Fa/repository/files/"
    _stub(monkeypatch, {
        f"{files}templates%2Flint.yml?ref=v1%2Fx": {"content": _b64("include: []\n"), "encoding": "base64"},
        f"{files}plain.yml?ref=main": {"content": "x", "encoding": "text"},
        f"{files}bad.yml?ref=main": {"content": "!!!", "encoding": "base64"},
        f"{files}big.yml?ref=main": {"content": _b64("x" * (MAX_CONFIG_BYTES + 1)), "encoding": "base64"},
    })
    api = fil.GlabIncludeApi("h")
    assert api.read("g/a", "templates/lint.yml", "v1/x") == "include: []\n"
    assert api.read("g/a", "plain.yml", "main") is None
    assert api.read("g/a", "bad.yml", "main") is None
    assert api.read("g/a", "big.yml", "main") is None
    assert api.read("g/a", "gone.yml", "main") is None
