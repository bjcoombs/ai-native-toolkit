"""The pack collector, --pack and pack identity (#1273, part B of #1266).

The fixture repository holds a file for each of the four rule kinds, an unparsable YAML
file, and a symlink to a file outside the root that holds the text a ``contains`` rule
looks for. Every assertion reads the run context the scan table writes, or the collector
directly; none runs anything from the repository.
"""
from __future__ import annotations

import json
import os
import subprocess
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

import assess_core
import lib.run.scan_registry as reg
from lib.scoring.level_signals import signal_path_validator
from lib.scoring.level_tally import LEVEL_READS
from lib.pack_checks import collect, glob_regex
from lib.pack_yaml import LEAF, OPAQUE, UNPARSABLE, UNSUPPORTED, YamlError, lookup, parse
from lib.packs import load_pack
from lib.profiles import ProfileError, single_repo_choice

SECRET = "s3cr3t-owner-value"
NEEDLE = "Money("

FILES = {
    "README.md": "# Repo\n",
    "docs/glossary.md": "# Glossary\n",
    "docs/adr/0001-first.md": "# One\n",
    "docs/adr/0002-second.md": "# Two\n",
    "docs/adr/notes.txt": "not an adr\n",
    "src/pay/money.py": f"x = {NEEDLE}1)\n",
    "src/pay/deep/more.py": f"y = {NEEDLE}2)\n",
    "src/other.py": "z = 3\n",
    "service.yaml": f"# owner below\nmetadata:\n  owner: {SECRET}\n  tags:\n    - a\n    - b\nspec: {{x: 1}}\n",
    "Service.YML": "metadata:\n  team: core\n",
    "settings.toml": "[tool.lint]\nstrict = true\n",
    "package.json": '{"scripts": {"test": "jest"}}\n',
    "broken.yaml": "metadata:\n    owner: a\n  team: b\n",
    "broken.json": "{not json",
    "vendor/lib/money.py": f"v = {NEEDLE}9)\n",
}

PACK = f'''[pack]
name = "team"
version = "2026.11"

[[checks]]
id = "glossary"
title = "Domain glossary"
why = "Agents need the domain's words."
exists = "docs/glossary.md"

[[checks]]
id = "adr_dir"
title = "ADR folder"
why = "Decisions written down."
exists = "docs/adr"

[[checks]]
id = "missing"
title = "Missing file"
why = "Absent."
exists = "docs/none.md"

[[checks]]
id = "adrs"
title = "ADRs"
why = "Decisions written down."
glob_count = "docs/adr/*.md"

[[checks]]
id = "python_files"
title = "Python files"
why = "Every depth."
glob_count = "src/**/*.py"

[[checks]]
id = "money_type"
title = "Money type"
why = "Floats lose pennies."
contains = {{ glob = "**/*.py", text = "{NEEDLE}" }}

[[checks]]
id = "owner_set"
title = "Owner"
why = "Who to ask."
key_exists = {{ file = "service.yaml", key = "metadata.owner" }}

[[checks]]
id = "owner_missing"
title = "Owner email"
why = "Who to ask."
key_exists = {{ file = "service.yaml", key = "metadata.email" }}

[[checks]]
id = "list_step"
title = "Into a list"
why = "A list holds no key."
key_exists = {{ file = "service.yaml", key = "metadata.tags.a" }}

[[checks]]
id = "flow_step"
title = "Into a flow mapping"
why = "The reader does not open it."
key_exists = {{ file = "service.yaml", key = "spec.x" }}

[[checks]]
id = "upper_suffix"
title = "Upper-case suffix"
why = "Suffix case is ignored."
key_exists = {{ file = "Service.YML", key = "metadata.team" }}

[[checks]]
id = "toml_key"
title = "TOML key"
why = "Lint is strict."
key_exists = {{ file = "settings.toml", key = "tool.lint.strict" }}

[[checks]]
id = "json_key"
title = "JSON key"
why = "A test script."
key_exists = {{ file = "package.json", key = "scripts.test" }}

[[checks]]
id = "bad_yaml"
title = "Broken YAML"
why = "Unparsable."
key_exists = {{ file = "broken.yaml", key = "metadata.owner" }}

[[checks]]
id = "bad_json"
title = "Broken JSON"
why = "Unparsable."
key_exists = {{ file = "broken.json", key = "a" }}

[[checks]]
id = "no_file"
title = "No file"
why = "Absent."
key_exists = {{ file = "absent.yaml", key = "a" }}

[[checks]]
id = "outside_link"
title = "Link outside"
why = "Never followed."
exists = "linked.py"
'''

LEVELS = """version = "team-1"

[[rows]]
id = "glossary"
title = "Domain glossary"
scope = "team"
aggregation = "min"

  [[rows.levels]]
  level = 1
  truth = ["docs"]
  criteria = [{ signal = "pack.team.glossary", op = "eq", value = true }]

[[rows]]
id = "adrs"
title = "Decision records"
scope = "team"
aggregation = "min"

  [[rows.levels]]
  level = 1
  truth = ["docs"]
  criteria = [{ signal = "pack.team.adrs", op = "gte", value = 5 }]

[[steps]]
step = 1
name = "Assisted"
"""


def _write(root: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    _write(root, FILES)
    _write(root, {".assess/config.toml": 'exclude_dirs = ["vendor"]\n',
                  ".assess/hotspots/old.py": f"o = {NEEDLE}0)\n"})
    outside = tmp_path / "outside" / "secret.py"
    _write(outside.parent, {"secret.py": f"s = {NEEDLE}7)\n"})
    try:
        (root / "linked.py").symlink_to(outside)
        (root / "src" / "linked_dir").symlink_to(outside.parent, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are not available here")
    subprocess.run(["git", "init", "-q", str(root)], check=True, capture_output=True,
                   encoding="utf-8", errors="replace")
    return root


@pytest.fixture
def pack_file(tmp_path: Path) -> Path:
    path = tmp_path / "team.pack.toml"
    path.write_text(PACK, encoding="utf-8")
    return path


def _excludes(root: Path) -> tuple[set[str], list[str]]:
    from lib.assess_config import load_excludes
    return load_excludes(root)


def test_each_rule_kind_yields_its_boolean_or_count(repo: Path, pack_file: Path) -> None:
    blocks = collect(repo, _excludes(repo), [load_pack(pack_file)])
    team = blocks["pack"]["team"]
    assert team["glossary"] is True and team["adr_dir"] is True and team["missing"] is False
    assert team["adrs"] == 2  # notes.txt does not match *.md
    assert team["python_files"] == 3  # src/**/*.py at every depth, the symlinked folder not walked
    # two files under src; vendor/ is excluded, .assess/ skipped and the outside file never read
    assert team["money_type"] == 2
    assert team["owner_set"] is True and team["owner_missing"] is False
    assert team["list_step"] is False  # a list holds no key
    assert team["upper_suffix"] is True and team["toml_key"] is True and team["json_key"] is True
    assert team["no_file"] is False


def test_an_unparsable_file_reads_unavailable_never_false(repo: Path, pack_file: Path) -> None:
    team = collect(repo, _excludes(repo), [load_pack(pack_file)])["pack"]["team"]
    assert team["bad_yaml"] == {"available": False, "reason": UNPARSABLE}
    assert team["bad_json"] == {"available": False, "reason": UNPARSABLE}
    assert team["flow_step"] == {"available": False, "reason": UNSUPPORTED}


def test_a_symlink_outside_the_root_is_not_followed(repo: Path, pack_file: Path) -> None:
    team = collect(repo, _excludes(repo), [load_pack(pack_file)])["pack"]["team"]
    assert team["outside_link"] is False
    assert team["money_type"] == 2  # secret.py outside the root holds the text too


def test_dependency_and_cache_folders_are_not_walked(repo: Path, pack_file: Path) -> None:
    """A check reads the same before and after an install: node_modules and .venv are skipped."""
    before = collect(repo, _excludes(repo), [load_pack(pack_file)])
    _write(repo, {"node_modules/pkg/money.py": f"m = {NEEDLE}1)\n",
                  "src/.venv/lib/site.py": f"n = {NEEDLE}2)\n", "src/pay/__pycache__/x.py": ""})
    assert collect(repo, _excludes(repo), [load_pack(pack_file)]) == before


def test_a_symlink_inside_the_root_is_read(repo: Path, tmp_path: Path) -> None:
    (repo / "docs" / "alias.md").symlink_to(repo / "docs" / "glossary.md")
    path = tmp_path / "in.pack.toml"
    path.write_text('[pack]\nname = "t"\nversion = "1"\n\n[[checks]]\nid = "alias"\ntitle = "A"\n'
                    'why = "W"\nexists = "docs/alias.md"\n', encoding="utf-8")
    assert collect(repo, _excludes(repo), [load_pack(path)])["pack"]["t"]["alias"] is True


def test_no_pack_writes_no_block(repo: Path) -> None:
    assert collect(repo, _excludes(repo), []) == {}
    assert collect(repo, _excludes(repo), None) == {}


@pytest.mark.parametrize(("glob", "path", "hit"), [
    ("docs/*.md", "docs/a.md", True), ("docs/*.md", "docs/x/a.md", False),
    ("**/*.py", "a.py", True), ("**/*.py", "a/b/c.py", True), ("src/**", "src/a/b", True),
    ("a/?.md", "a/b.md", True), ("a/?.md", "a/bc.md", False), ("a/[bc].md", "a/c.md", True),
    ("a/[!bc].md", "a/c.md", False), ("a+b.md", "a+b.md", True), ("a+b.md", "aab.md", False),
])
def test_glob_semantics(glob: str, path: str, hit: bool) -> None:
    assert bool(glob_regex(glob).fullmatch(path)) is hit


@pytest.mark.parametrize(("text", "key", "found"), [
    ("a:\n  b:\n    c: 1\n", "a.b.c", True),
    ("a:\n  b: 1\n", "a.c", False),
    ("a:\n  b: 1\n", "a.b.c", False),
    ("---\na: |\n  text: here\n  more\nb: 2\n", "b", True),
    ("---\na: |\n  text: here\n", "a.text", False),
    ("a: x # comment: no\n'q k': 1\n\"d\": 2\n", "q k", True),
    ("a:\nb: 2\n", "a", True),
    ("a: long\n  continued\nb: 1\n", "b", True),
    ("a: &anchor\n  b: 1\n", "a.b", True),
    ("- a\n- b\n", "a", False),
    ("", "a", False),
    ("a:\n- x\n- y\nb: 1\n", "b", True),
    ("description:\n  Long text\n  more text\nname: x\n", "name", True),
    ("description:\n  Long text\nname: x\n", "description.Long", False),
])
def test_yaml_reader_finds_keys(text: str, key: str, found: bool) -> None:
    assert lookup(parse(text), key) is found


@pytest.mark.parametrize(("text", "code"), [
    ("a:\n    b: 1\n  c: 2\n", UNPARSABLE),
    ("a:\n  b: 1\n c: 2\n", UNPARSABLE),
    ("\ta: 1\n", UNPARSABLE),
    ("just a line\n", UNPARSABLE),
    ("a: 1\n---\nb: 2\n", UNSUPPORTED),
    ("%YAML 1.2\n---\na: 1\n", UNSUPPORTED),
    ("<<: *base\n", UNSUPPORTED),
    ("? complex\n: value\n", UNSUPPORTED),
    ("a: [1,\n  2]\n", UNSUPPORTED),
    ("{a: 1}\n", UNSUPPORTED),
])
def test_yaml_reader_refuses_what_it_cannot_follow(text: str, code: str) -> None:
    with pytest.raises(YamlError) as caught:
        parse(text)
    assert caught.value.code == code


def test_yaml_lookup_into_a_flow_or_alias_is_unsupported() -> None:
    assert parse("a: *ref\n") == {"a": OPAQUE}
    with pytest.raises(YamlError):
        lookup(parse("a: *ref\n"), "a.b")
    assert parse("a: 1\n") == {"a": LEAF}


# --- the scan table, the run context and the level ------------------------------------


def _model(tmp_path: Path, packs: tuple[Any, ...]) -> Any:
    path = tmp_path / "team-levels.toml"
    path.write_text(LEVELS, encoding="utf-8")
    return single_repo_choice(None, path, signal_path_validator(packs), packs)


def test_the_run_context_records_signals_and_identity_and_no_path_or_text(
        repo: Path, pack_file: Path, tmp_path: Path) -> None:
    packs = (load_pack(pack_file),)
    ctx = assess_core.build_run_context(repo_root=repo, run_date="2026-11-24", non_interactive=True,
                                        model=_model(tmp_path, packs), packs=packs)
    assert ctx["packs"] == [{"name": "team", "version": "2026.11", "sha256": packs[0]["sha256"]}]
    assert ctx["pack"]["team"]["glossary"] is True
    keys = list(ctx)
    assert keys.index("guardrails") < keys.index("pack") < keys.index("packs") < keys.index("level")
    pack_text = json.dumps({"pack": ctx["pack"], "packs": ctx["packs"], "level": ctx["level"]})
    for check in packs[0]["checks"]:
        rule = check["rule"]
        # the paths, globs, file names and text a rule names (a dotted key is a check's own words)
        for value in (v for k, v in rule.items() if k in ("path", "glob", "file", "text")):
            assert value not in pack_text, value
    whole = json.dumps(ctx)
    assert SECRET not in whole and NEEDLE not in whole
    conditions = {c["id"]: c for c in ctx["level"]["conditions"]}
    assert conditions["glossary.1"]["state"] == "met"
    assert conditions["adrs.1"]["state"] == "unmet"
    assert conditions["glossary.1"]["grade"] is None and not conditions["glossary.1"]["counted"]
    assert "glossary.1" in ctx["level"]["listed"]


def test_counts_only_keeps_pack_and_check_names_and_values(repo: Path, pack_file: Path) -> None:
    from lib.counts_only.content_free import counts_only
    packs = (load_pack(pack_file),)
    blocks = collect(repo, _excludes(repo), packs)
    out = counts_only({"pack": blocks["pack"], "packs": blocks["packs"]})
    assert out["pack"]["team"]["glossary"] is True and out["pack"]["team"]["adrs"] == 2
    assert out["pack"]["team"]["bad_yaml"] == {"available": False}
    assert out["packs"][0]["name"] == "team" and out["packs"][0]["version"] == "2026.11"
    assert SECRET not in json.dumps(out)


def test_a_pack_path_without_its_pack_is_refused_naming_pack(tmp_path: Path, pack_file: Path) -> None:
    with pytest.raises(ProfileError, match="--pack"):
        _model(tmp_path, ())
    other = load_pack(pack_file)
    other = {**other, "name": "other",
             "checks": [{**c, "signal": c["signal"].replace("pack.team.", "pack.other.")} for c in other["checks"]]}
    with pytest.raises(ProfileError, match="--pack"):
        _model(tmp_path, (other,))


def test_a_pack_path_for_a_missing_check_is_refused(tmp_path: Path, pack_file: Path) -> None:
    pack = load_pack(pack_file)
    pack = {**pack, "checks": [c for c in pack["checks"] if c["id"] != "adrs"]}
    with pytest.raises(ProfileError, match="pack.team.adrs"):
        _model(tmp_path, (pack,))


def test_the_level_scan_reads_the_pack_block() -> None:
    level = next(s for s in reg.SCANS if s.key == "level")
    assert level.reads[-1] == "pack" and level.reads[:len(LEVEL_READS)] == LEVEL_READS


def test_a_run_without_pack_writes_the_same_run_context_as_the_table_without_packs(repo: Path) -> None:
    """Requirement 8: the table as it was before #1273 (no pack_checks entry, the level entry
    without its pack read) and today's table give byte-identical run contexts with no --pack."""
    before = tuple(replace(s, reads=s.reads[:-1]) if s.key == "level" else s
                   for s in reg.SCANS if s.key != "pack_checks")
    reg.validate(before, reg.PROVIDED_INPUTS - {"packs"})
    outputs = []
    for table in (reg.SCANS, before):
        ctx: dict[str, Any] = {}
        inputs = {"repo_root": repo, "requested_scope": None, "run_date": "2026-11-24",
                  "non_interactive": True, "maturity_model": None, "managed_settings": False, "packs": ()}
        reg.run_scans(ctx, inputs, specs=table)
        ctx.pop("run_id")
        outputs.append(json.dumps(ctx, sort_keys=False, default=str))
    assert outputs[0] == outputs[1]
    assert "pack" not in json.loads(outputs[0]) and "packs" not in json.loads(outputs[0])


# --- the CLI -------------------------------------------------------------------------


def test_a_pack_load_error_exits_2_with_the_loaders_message(repo: Path, tmp_path: Path,
                                                             capsys: pytest.CaptureFixture[str]) -> None:
    bad = tmp_path / "bad.pack.toml"
    bad.write_text('[pack]\nname = "t"\nversion = "1"\n\n[[checks]]\nid = "x"\ntitle = "T"\nwhy = "W"\n'
                   'command = "rm -rf /"\n', encoding="utf-8")
    assert assess_core.main([str(repo), "--pack", str(bad)]) == 2
    assert "never runs code" in capsys.readouterr().err


def test_two_packs_of_one_name_exit_2(repo: Path, pack_file: Path, tmp_path: Path,
                                      capsys: pytest.CaptureFixture[str]) -> None:
    copy = tmp_path / "copy.pack.toml"
    copy.write_text(PACK, encoding="utf-8")
    assert assess_core.main([str(repo), "--pack", str(pack_file), "--pack", str(copy)]) == 2
    assert "already passed" in capsys.readouterr().err


def test_a_levels_row_reading_a_pack_needs_its_pack_on_the_cli(repo: Path, tmp_path: Path,
                                                               capsys: pytest.CaptureFixture[str]) -> None:
    levels = tmp_path / "team-levels.toml"
    levels.write_text(LEVELS, encoding="utf-8")
    assert assess_core.main([str(repo), "--levels", str(levels)]) == 2
    assert "--pack" in capsys.readouterr().err


def test_pack_flag_is_repeatable_in_pass_order(pack_file: Path, tmp_path: Path) -> None:
    second = tmp_path / "second.pack.toml"
    second.write_text(PACK.replace('name = "team"', 'name = "second"'), encoding="utf-8")
    packs = assess_core.load_packs([str(second), str(pack_file)])
    assert [p["name"] for p in packs] == ["second", "team"]



def test_a_narrowed_run_plans_the_pack_checks(repo: Path, pack_file: Path, tmp_path: Path) -> None:
    """#1268's scan plan (a --levels run) always runs pack_checks: with --pack it evaluates the
    checks, and without it writes no pack or packs key, never a not-run marker."""
    from lib.run.scan_plan import run_plan
    packs = (load_pack(pack_file),)
    choice = _model(tmp_path, packs)
    plan = run_plan(choice)
    assert plan is not None and plan.runs("pack_checks")
    ctx = assess_core.build_run_context(repo_root=repo, run_date="2026-11-24", non_interactive=True,
                                        model=choice, plan=plan, packs=packs)
    assert ctx["pack"]["team"]["glossary"] is True
    assert next(c for c in ctx["level"]["conditions"] if c["id"] == "glossary.1")["state"] == "met"
    bare = assess_core.build_run_context(repo_root=repo, run_date="2026-11-24", non_interactive=True,
                                         model=single_repo_choice("citizen", None), plan=run_plan(
                                             single_repo_choice("citizen", None)))
    assert "pack" not in bare and "packs" not in bare and "scan_plan" in bare


@pytest.mark.skipif(not hasattr(os, "geteuid") or os.geteuid() == 0, reason="needs a non-root POSIX user")
def test_an_unlistable_folder_makes_absences_unknown(repo: Path, pack_file: Path) -> None:
    """A folder the walk cannot list: counts and absences read unreadable_file, never 0 or false."""
    locked = repo / "docs" / "adr"
    locked.chmod(0)
    try:
        team = collect(repo, _excludes(repo), [load_pack(pack_file)])["pack"]["team"]
    finally:
        locked.chmod(0o755)
    marker = {"available": False, "reason": "unreadable_file"}
    assert team["adrs"] == marker and team["money_type"] == marker
    assert team["missing"] == marker and team["no_file"] == marker
    assert team["glossary"] is True  # found, so still known
