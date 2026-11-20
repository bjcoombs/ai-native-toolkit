"""Tests for the fleet org units tree, lib/fleet_units.py, and the checkout facts it reads,
lib/fleet_repo_facts.py (issue #703)."""
from __future__ import annotations

import subprocess
import tomllib
from pathlib import Path

import pytest

from lib import fleet_units as fu
from lib.fleet import Target
from lib.fleet_repo_facts import collect_facts, default_owners, repo_facts, web_url

GITLAB = ["grp/sub/proj1", "grp/sub/proj2", "grp/other/deep/proj3", "grp/proj4"]


def _units(tree: fu.Tree) -> dict[str, tuple[str, str | None, str]]:
    return {u["id"]: (u["kind"], u["parent"], u["source"]) for u in tree["units"]}


def _map(text: str) -> fu.UnitsMap:
    return fu.parse_units(tomllib.loads(text))


# --- inference ---------------------------------------------------------------


def test_gitlab_nested_groups_build_a_three_level_tree() -> None:
    ownership, system = fu.build_trees(GITLAB, fu.empty_map())
    assert ownership["view"] == "ownership"
    assert _units(ownership) == {
        "grp": ("org", None, "path"),
        "grp/other": ("division", "grp", "path"),
        "grp/other/deep": ("team", "grp/other", "path"),
        "grp/sub": ("team", "grp", "path"),
    }
    assert ownership["placement"] == {"grp/other/deep/proj3": "grp/other/deep", "grp/proj4": "grp",
                                      "grp/sub/proj1": "grp/sub", "grp/sub/proj2": "grp/sub"}
    assert system == {"view": "system", "units": [], "placement": {}}


def test_deeper_intermediate_units_are_departments() -> None:
    (ownership, _) = fu.build_trees(["a/b/c/d/repo"], fu.empty_map())
    assert [u["kind"] for u in ownership["units"]] == ["org", "division", "department", "team"]


def test_github_teams_place_a_repo_in_its_deepest_team() -> None:
    fragment = {"scope": "acme", "provider": "github", "teams": [
        {"slug": "eng", "parent": None, "repos": ["svc1", "svc2"]},
        {"slug": "payments", "parent": "eng", "repos": ["svc1"]},
        {"slug": "alpha", "parent": None, "repos": ["svc1"]},
        {"slug": "loop", "parent": "loop", "repos": []},
        "junk", {"slug": ""},
    ]}
    ownership, _ = fu.build_trees(["acme/svc1", "acme/svc2", "acme/svc3"], fu.empty_map(), {"acme": fragment})
    assert ownership["placement"] == {"acme/svc1": "acme/payments", "acme/svc2": "acme/eng", "acme/svc3": "acme"}
    units = {u["id"]: u for u in ownership["units"]}
    assert units["acme/payments"]["parent"] == "acme/eng"
    assert units["acme/payments"]["owners"] == ["@acme/payments"]
    assert units["acme/eng"]["kind"] == "team" and units["acme/payments"]["source"] == "github_teams"
    assert "acme/alpha" not in units  # the shallower team lost the tie-break


def test_teams_only_apply_to_github_fragments() -> None:
    fragment = {"scope": "grp", "provider": "gitlab", "teams": [{"slug": "x", "parent": None, "repos": ["p"]}]}
    ownership, _ = fu.build_trees(["grp/p"], fu.empty_map(), {"grp": fragment})
    assert ownership["placement"] == {"grp/p": "grp"}


def test_codeowners_team_handle_is_the_placement_fallback() -> None:
    owners = {"acme/svc1": ["@alice", "@acme/platform"], "acme/svc2": ["@other/x"]}
    ownership, _ = fu.build_trees(["acme/svc1", "acme/svc2"], fu.empty_map(), {}, owners)
    assert ownership["placement"] == {"acme/svc1": "acme/platform", "acme/svc2": "acme"}
    units = {u["id"]: u for u in ownership["units"]}
    assert units["acme/platform"]["source"] == "codeowners"
    assert units["acme/platform"]["owners"] == ["@acme/platform"]


def test_codeowners_never_move_a_repo_out_of_a_nested_group() -> None:
    ownership, _ = fu.build_trees(["grp/sub/p"], fu.empty_map(), {}, {"grp/sub/p": ["@grp/sub/team"]})
    assert ownership["placement"] == {"grp/sub/p": "grp/sub"}


def test_a_team_placement_beats_codeowners() -> None:
    fragment = {"scope": "acme", "provider": "github", "teams": [{"slug": "eng", "parent": None, "repos": ["svc"]}]}
    ownership, _ = fu.build_trees(["acme/svc"], fu.empty_map(), {"acme": fragment}, {"acme/svc": ["@acme/ops"]})
    assert ownership["placement"] == {"acme/svc": "acme/eng"}


# --- mapping file ------------------------------------------------------------

MAPPING = """\
[[unit]]
id = "grp/platform"
kind = "division"
parent = "grp"
name = "Platform"
owners = ["@grp/leads"]

[[unit]]
id = "grp/sub"
kind = "team"
parent = "grp/platform"

[[unit]]
view = "system"
id = "checkout"
kind = "system"

[[repo]]
repo = "grp/proj4"
ownership = "grp/sub"
system = "checkout"
owners = ["@grp/payments"]

[[repo]]
repo = "grp/sub/proj1"
system = "checkout"

[[repo]]
repo = "gone/repo"
ownership = "grp/sub"
"""


def test_a_mapping_file_overrides_the_inferred_tree() -> None:
    mapping = _map(MAPPING)
    ownership, system = fu.build_trees(GITLAB, mapping)
    units = _units(ownership)
    assert units["grp/sub"] == ("team", "grp/platform", "mapping")
    assert units["grp/platform"] == ("division", "grp", "mapping")
    assert ownership["placement"]["grp/proj4"] == "grp/sub"
    assert system["placement"] == {"grp/proj4": "checkout", "grp/sub/proj1": "checkout"}
    assert _units(system) == {"checkout": ("system", None, "mapping")}
    assert fu.unmatched_repos(mapping, GITLAB) == ["gone/repo"]


def test_a_repo_is_placed_once_per_view() -> None:
    trees = fu.build_trees(GITLAB, _map(MAPPING))
    for tree in trees:
        assert len(tree["placement"]) == len(set(tree["placement"]))
    with pytest.raises(fu.UnitsError, match="listed twice"):
        _map('[[repo]]\nrepo = "a/b"\n[[repo]]\nrepo = "a/b"\n')


def test_inferred_units_emptied_by_the_mapping_are_dropped() -> None:
    mapping = _map('[[unit]]\nid = "elsewhere"\nkind = "org"\n[[repo]]\nrepo = "grp/sub/p"\nownership = "elsewhere"\n')
    ownership, _ = fu.build_trees(["grp/sub/p"], mapping)
    assert _units(ownership) == {"elsewhere": ("org", None, "mapping")}


def test_a_declared_unit_keeps_its_emptied_inferred_parent() -> None:
    mapping = _map('[[unit]]\nid = "grp/sub/new"\nkind = "team"\nparent = "grp/sub"\n'
                   '[[unit]]\nid = "x"\nkind = "org"\n[[repo]]\nrepo = "grp/sub/p"\nownership = "x"\n')
    ownership, _ = fu.build_trees(["grp/sub/p"], mapping)
    ids = {u["id"] for u in ownership["units"]}
    assert ids == {"grp", "grp/sub", "grp/sub/new", "x"}
    assert all(u["parent"] is None or u["parent"] in ids for u in ownership["units"])


def test_repo_owners_prefer_the_mapping() -> None:
    mapping = _map(MAPPING)
    owners = fu.repo_owners(mapping, {"grp/proj4": ["@x"], "grp/sub/proj1": ["@y"]}, ["grp/proj4", "grp/sub/proj1"])
    assert owners == {"grp/proj4": ["@grp/payments"], "grp/sub/proj1": ["@y"]}


@pytest.mark.parametrize(("text", "message"), [
    ('[[unit]]\nid = "a"\nkind = "squad"\n', "unknown kind 'squad'"),
    ('[[unit]]\nid = "a"\nkind = "org"\ncolour = "red"\n', "unknown key 'colour'"),
    ('[[unit]]\nview = "money"\nid = "a"\nkind = "org"\n', "unknown view 'money'"),
    ('[[unit]]\nid = "/abs"\nkind = "org"\n', "path-like id"),
    ('[[unit]]\nkind = "org"\n', "'id' must be a non-empty string"),
    ('[[unit]]\nid = "a"\nkind = "org"\n[[unit]]\nid = "a"\nkind = "team"\n', "declared twice"),
    ('[[unit]]\nid = "a"\nkind = "org"\nowners = "x"\n', "'owners' must be a list"),
    ('[[repo]]\nrepo = "a/b"\nteam = "x"\n', "unknown key 'team'"),
    ('flavour = 1\n', "unknown key 'flavour'"),
])
def test_mapping_shape_errors_name_the_entry(text: str, message: str) -> None:
    with pytest.raises(fu.UnitsError, match=message):
        _map(text)


@pytest.mark.parametrize(("text", "message"), [
    ('[[unit]]\nid = "x"\nkind = "team"\nparent = "nope"\n', "parent 'nope' is not a unit"),
    ('[[unit]]\nid = "x"\nkind = "team"\nparent = "y"\n[[unit]]\nid = "y"\nkind = "team"\nparent = "x"\n',
     "cycle"),
    ('[[repo]]\nrepo = "grp/proj4"\nsystem = "nope"\n', "system unit 'nope' is not a unit"),
])
def test_mapping_reference_errors_name_the_entry(text: str, message: str) -> None:
    with pytest.raises(fu.UnitsError, match=message):
        fu.build_trees(GITLAB, _map(text))


def test_load_units_reads_a_file_and_reports_faults(tmp_path: Path) -> None:
    good = tmp_path / "units.toml"
    good.write_text(MAPPING, encoding="utf-8")
    assert len(fu.load_units(good)["units"]) == 3
    bad = tmp_path / "bad.toml"
    bad.write_text("[[unit]\n", encoding="utf-8")
    with pytest.raises(fu.UnitsError, match="cannot read units file bad.toml"):
        fu.load_units(bad)
    with pytest.raises(fu.UnitsError, match="cannot read"):
        fu.load_units(tmp_path / "missing.toml")
    table = tmp_path / "table.toml"
    table.write_text("[unit]\nid = 'a'\n", encoding="utf-8")
    with pytest.raises(fu.UnitsError, match=r"array of tables"):
        fu.load_units(table)


# --- checkout facts ----------------------------------------------------------


@pytest.mark.parametrize(("remote", "url"), [
    ("git@github.com:acme/svc1.git", "https://github.com/acme/svc1"),
    ("https://github.com/acme/svc1.git", "https://github.com/acme/svc1"),
    ("https://user:t0ken@GitLab.Example.com/grp/sub/proj.git/", "https://gitlab.example.com/grp/sub/proj"),
    ("ssh://git@gitlab.example.com:2222/grp/sub/proj.git", "https://gitlab.example.com/grp/sub/proj"),
    ("https://gitlab.example.com:8443/grp/proj.git", "https://gitlab.example.com:8443/grp/proj"),
    ("gitlab.example.com:grp/proj", "https://gitlab.example.com/grp/proj"),
    ("file:///srv/repos/x.git", None),
    ("/srv/repos/x.git", None),
    ("https://host/.git", None),
    ("", None),
    (None, None),
])
def test_web_url_normalises_remotes(remote: str | None, url: str | None) -> None:
    assert web_url(remote) == url


def test_default_owners_take_the_last_catch_all_rule() -> None:
    text = ("# comment\n* @acme/old\n/docs/ @acme/docs\n[Section]\n^[Other][2] @x\n"
            "*   @acme/platform  alice@example.com notanowner # trailing\n")
    assert default_owners(text) == ("@acme/platform", "alice@example.com")
    assert default_owners("/src/ @a\n") == ()


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def test_repo_facts_read_origin_and_codeowners(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    checkout = tmp_path / "svc"
    (checkout / ".github").mkdir(parents=True)
    _git(checkout, "init", "-q")
    _git(checkout, "remote", "add", "origin", "git@github.com:acme/svc.git")
    (checkout / ".github" / "CODEOWNERS").write_text("* @acme/eng\n", encoding="utf-8")
    (checkout / "CODEOWNERS").write_text("* @acme/ignored\n", encoding="utf-8")
    facts = collect_facts([Target("acme/svc", checkout), Target("acme/gone", tmp_path / "gone")], jobs=2)
    assert facts["acme/svc"].web_url == "https://github.com/acme/svc"
    assert facts["acme/svc"].owners == ("@acme/eng",)
    assert facts["acme/gone"] == (None, ())
    def denied(self: Path) -> bool:
        raise PermissionError("denied")
    monkeypatch.setattr(Path, "is_dir", denied)
    assert repo_facts(Target("acme/svc", checkout)) == (None, ())
    monkeypatch.undo()
    bare = tmp_path / "bare"
    bare.mkdir()
    assert repo_facts(Target("x/bare", bare)) == (None, ())
