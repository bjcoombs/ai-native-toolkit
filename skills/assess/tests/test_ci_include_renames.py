"""Tests for lib/hosting/gitlab_include_renames.py: CI include projects looked up across a rename
under ``--platform`` (issue #1357, part 3 of #1285), and the headless runner wiring."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from assess_test_support import skip_without_posix_shell

import lib.hosting.gitlab_include_renames as renames_mod
from lib.ci.ci_includes import IncludeRenames, resolve_includes
from lib.hosting.gh_cli import GhUnavailable
from lib.hosting.gitlab_cli import GitlabProject
from lib.signals import collect
from test_fleet import make_repo

TPL = "unit:\n  script:\n    - pytest -q\n"
OLD = "include:\n  - project: grp/old-templates\n    file: /templates/test.yml\n"
HOST = GitlabProject("gitlab.example.com", "grp/app")


def _root(tmp_path: Path, ci: str = OLD) -> tuple[Path, Path]:
    root = tmp_path / "gl"
    app = make_repo(root, "grp/app", files={".gitlab-ci.yml": ci},
                    remote="https://gitlab.example.com/grp/app.git")
    make_repo(root, "grp/ci-templates", files={"templates/test.yml": TPL})
    return root, app


class Api:
    """A fake ``glab_api``: path -> answer (a new path, or an exception reason)."""

    def __init__(self, answers: dict[str, str]) -> None:
        self.answers = answers
        self.calls: list[tuple[str, str, bool]] = []

    def __call__(self, project: GitlabProject, path: str, cache: bool = True) -> object:
        self.calls.append((project.host, path, cache))
        answer = self.answers.get(path, "not_found: 404")
        if ":" in answer:
            raise GhUnavailable(answer)
        return {"path_with_namespace": answer, "description": "free text"}


@pytest.fixture
def api(monkeypatch: pytest.MonkeyPatch) -> Api:
    fake = Api({})
    monkeypatch.setattr(renames_mod, "glab_api", fake)
    monkeypatch.setattr(renames_mod, "open_gitlab", lambda _root: HOST)
    resolve_includes.cache_clear()
    return fake


def test_an_include_with_no_clone_is_looked_up_by_path_and_never_cached(tmp_path, api):
    root, app = _root(tmp_path)
    api.answers["projects/grp%2Fold-templates"] = "Grp/CI-Templates"
    got = renames_mod.read_include_renames(app, root)
    assert got == IncludeRenames(frozenset({"grp/old-templates"}),
                                 (("grp/old-templates", "grp/ci-templates"),))
    assert api.calls == [("gitlab.example.com", "projects/grp%2Fold-templates", False)]
    assert [r["rename"] for r in resolve_includes(app, root, got).includes] == ["followed"]


def test_a_cloned_include_asks_nothing(tmp_path, api):
    root, app = _root(tmp_path, OLD.replace("old-templates", "ci-templates"))
    assert renames_mod.read_include_renames(app, root) == IncludeRenames()
    assert api.calls == []


def test_not_found_is_checked_and_a_refusal_is_not(tmp_path, api):
    ci = OLD + "  - component: gitlab.example.com/grp/comps/unit@1\n"
    root, app = _root(tmp_path, ci)
    api.answers["projects/grp%2Fcomps"] = "no_access: 403"
    got = renames_mod.read_include_renames(app, root)
    assert got == IncludeRenames(frozenset({"grp/old-templates"}))
    assert [r["rename"] for r in resolve_includes(app, root, got).includes] == ["checked", "not_checked"]


def test_a_host_wide_failure_stops_the_asking(tmp_path, api):
    ci = OLD + "  - project: grp/second\n    file: /t.yml\n"
    root, app = _root(tmp_path, ci)
    for reason in ("glab_timeout: no answer", "rate_limited: still limited", "glab_not_installed: x"):
        api.calls.clear()
        api.answers["projects/grp%2Fold-templates"] = reason
        assert renames_mod.read_include_renames(app, root) == IncludeRenames()
        assert len(api.calls) == 1


def test_a_timeout_after_one_rename_keeps_the_rename_already_found(tmp_path, api):
    ci = OLD + "  - project: grp/second\n    file: /t.yml\n"
    root, app = _root(tmp_path, ci)
    api.answers.update({"projects/grp%2Fold-templates": "grp/ci-templates",
                        "projects/grp%2Fsecond": "glab_timeout: no answer"})
    got = renames_mod.read_include_renames(app, root)
    assert got == IncludeRenames(frozenset({"grp/old-templates"}),
                                 (("grp/old-templates", "grp/ci-templates"),))
    assert [c[1] for c in api.calls] == ["projects/grp%2Fold-templates", "projects/grp%2Fsecond"]
    assert [r["rename"] for r in resolve_includes(app, root, got).includes] == ["followed", "not_checked"]


def test_an_answer_of_another_shape_is_not_checked(tmp_path, api, monkeypatch):
    root, app = _root(tmp_path)
    monkeypatch.setattr(renames_mod, "glab_api", lambda *_a, **_k: [])
    assert renames_mod.read_include_renames(app, root) == IncludeRenames()


def test_a_followed_project_has_its_own_includes_looked_up(tmp_path, api):
    root, app = _root(tmp_path)
    make_repo(root, "grp/base", files={"base.yml": TPL})
    make_repo(root, "grp/ci-templates", files={
        "templates/test.yml": "include:\n  - project: grp/old-base\n    file: /base.yml\n"})
    api.answers.update({"projects/grp%2Fold-templates": "grp/ci-templates",
                        "projects/grp%2Fold-base": "grp/base"})
    got = renames_mod.read_include_renames(app, root)
    assert got is not None and dict(got.moved) == {"grp/old-templates": "grp/ci-templates",
                                                   "grp/old-base": "grp/base"}
    records = resolve_includes(app, root, got).includes
    assert [(r["status"], r["rename"]) for r in records] == [("resolved", "followed")] * 2


def test_nothing_is_checked_without_a_root_or_a_gitlab_remote(tmp_path, api, monkeypatch):
    root, app = _root(tmp_path)
    assert renames_mod.read_include_renames(app, None) is None
    monkeypatch.setattr(renames_mod, "open_gitlab", lambda _root: None)
    assert renames_mod.read_include_renames(app, root) is None

    def refuse(_root: Path) -> None:
        raise GhUnavailable("not_authenticated: no login")
    monkeypatch.setattr(renames_mod, "open_gitlab", refuse)
    assert renames_mod.read_include_renames(app, root) is None
    assert renames_mod.no_include_renames(app, root) is None
    assert api.calls == []


# --- the headless runner ----------------------------------------------------------

GLAB = """#!/bin/sh
echo "$*" >> "$GLAB_DIR/log"
case "$*" in
  "auth status"*) exit 0 ;;
  *"projects/grp%2Fold-templates") echo '{"path_with_namespace":"grp/ci-templates"}'; exit 0 ;;
esac
echo "glab: 404 Not Found (HTTP 404)" >&2; exit 1
"""


@pytest.fixture
def glab(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    skip_without_posix_shell()
    bindir, data = tmp_path / "bin", tmp_path / "glab"
    for d in (bindir, data):
        d.mkdir()
    (bindir / "glab").write_text(GLAB, encoding="utf-8")
    (bindir / "glab").chmod(0o755)
    (bindir / "gh").write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    (bindir / "gh").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("GLAB_DIR", str(data))
    monkeypatch.setenv("ASSESS_PLATFORM_CACHE", str(tmp_path / "cache"))
    monkeypatch.chdir(tmp_path)
    resolve_includes.cache_clear()
    return data


def test_platform_ci_checks_follows_the_rename_and_offline_does_not(tmp_path, glab):
    root, app = _root(tmp_path)
    offline = collect(app, ["ci_checks"], ghsync_root=root)["ci_checks"]
    assert [(r["status"], r["rename"]) for r in offline["includes"]] == [("not_cloned", "not_checked")]
    assert not (glab / "log").exists() or "old-templates" not in (glab / "log").read_text(encoding="utf-8")
    online = collect(app, ["ci_checks"], ghsync_root=root, platform=True)["ci_checks"]
    assert [(r["status"], r["rename"]) for r in online["includes"]] == [("resolved", "followed")]
    assert online["test_or_lint_configured"] is True
    assert "pytest" not in json.dumps(online["includes"])
