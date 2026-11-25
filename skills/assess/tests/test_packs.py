"""lib/packs.py: the *.pack.toml format and its strict, code-free loader (#1272)."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from lib.packs import CODE_KEYS, PackError, load_pack, parse_pack, signal_path

LIB_DIR = Path(__file__).resolve().parents[1] / "scripts" / "lib"

VALID = '''\
[pack]
name = "payments"
version = "2026.11"

[[checks]]
id = "glossary"
title = "Domain glossary"
why = "Agents need the domain's words."
exists = "docs/glossary.md"

[[checks]]
id = "adrs"
title = "Decision records"
why = "Decisions outlive their authors."
glob_count = "docs/adr/*.md"

[[checks]]
id = "money_type"
title = "Money type in use"
why = "Floats lose pennies."
contains = { glob = "src/**/*.py", text = "Money(" }

[[checks]]
id = "owner_set"
title = "Service owner declared"
why = "An agent needs to know who to ask."
key_exists = { file = "service.yaml", key = "metadata.owner" }
'''

HEADER = '[pack]\nname = "p"\nversion = "1"\n\n'


def _write(tmp_path: Path, text: str, name: str = "team.pack.toml") -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def _check(body: str) -> str:
    return HEADER + f'[[checks]]\nid = "c"\ntitle = "T"\nwhy = "W"\n{body}\n'


def _error(tmp_path: Path, text: str) -> str:
    with pytest.raises(PackError) as info:
        load_pack(_write(tmp_path, text))
    return str(info.value)


def test_valid_pack_loads_every_rule(tmp_path: Path) -> None:
    pack = load_pack(_write(tmp_path, VALID))
    assert pack["name"] == "payments"
    assert pack["version"] == "2026.11"
    assert [c["rule"] for c in pack["checks"]] == [
        {"kind": "exists", "path": "docs/glossary.md"},
        {"kind": "glob_count", "glob": "docs/adr/*.md"},
        {"kind": "contains", "glob": "src/**/*.py", "text": "Money("},
        {"kind": "key_exists", "file": "service.yaml", "key": "metadata.owner"},
    ]
    assert [c["signal"] for c in pack["checks"]] == [
        "pack.payments.glossary", "pack.payments.adrs",
        "pack.payments.money_type", "pack.payments.owner_set",
    ]
    assert pack["checks"][0]["title"] == "Domain glossary"
    assert pack["checks"][0]["why"] == "Agents need the domain's words."
    assert re.fullmatch(r"[0-9a-f]{64}", pack["sha256"])
    # Pinned: the canonical JSON of this pack. A change here is a change to the hash recipe.
    assert pack["sha256"] == "4f409dcd6b06d165f54f5d2bec07190f3df16edde7d377cca3f92b2eb243b693"
    assert signal_path("payments", "adrs") == "pack.payments.adrs"


def test_sha256_is_stable_and_moves_with_one_character(tmp_path: Path) -> None:
    first = load_pack(_write(tmp_path, VALID))["sha256"]
    assert load_pack(_write(tmp_path, VALID))["sha256"] == first
    # Formatting and comments are not content.
    reformatted = "# a comment\n" + VALID.replace('name = "payments"', 'name    =    "payments"')
    assert load_pack(_write(tmp_path, reformatted))["sha256"] == first
    changed = VALID.replace('text = "Money("', 'text = "Money["')
    assert load_pack(_write(tmp_path, changed))["sha256"] != first


@pytest.mark.parametrize("key", sorted(CODE_KEYS))
def test_code_running_key_in_a_check_names_the_fix(tmp_path: Path, key: str) -> None:
    message = _error(tmp_path, _check(f'exists = "a"\n{key} = "make lint"'))
    assert "rule" in message
    assert "#1266" in message
    assert key in message


@pytest.mark.parametrize("where", ["root", "pack", "rule"])
def test_code_running_key_is_refused_at_every_level(tmp_path: Path, where: str) -> None:
    text = {
        "root": 'command = "x"\n' + _check('exists = "a"'),
        "pack": '[pack]\nname = "p"\nversion = "1"\nscript = "x"\n\n[[checks]]\nid = "c"\n'
                'title = "T"\nwhy = "W"\nexists = "a"\n',
        "rule": _check('contains = { glob = "*.md", text = "x", python = "1" }'),
    }[where]
    message = _error(tmp_path, text)
    assert "rule" in message and "#1266" in message


def test_code_keys_cover_the_issue_list() -> None:
    assert CODE_KEYS == {"command", "run", "exec", "script", "shell", "callable", "entry_point", "python"}


def test_unknown_key_is_named(tmp_path: Path) -> None:
    message = _error(tmp_path, _check('exists = "a"\nseverity = "high"'))
    assert "severity" in message and "checks[0]" in message


def test_unknown_key_inside_a_rule_is_named(tmp_path: Path) -> None:
    message = _error(tmp_path, _check('contains = { glob = "*.md", text = "x", regex = true }'))
    assert "regex" in message and "contains" in message


def test_duplicate_check_id_is_named(tmp_path: Path) -> None:
    text = _check('exists = "a"') + '\n[[checks]]\nid = "c"\ntitle = "T"\nwhy = "W"\nexists = "b"\n'
    message = _error(tmp_path, text)
    assert "id" in message and "duplicate" in message and "'c'" in message


@pytest.mark.parametrize("rule, field", [
    ('exists = "/etc/passwd"', "exists"),
    ('glob_count = "C:/x/*.md"', "glob_count"),
    ('contains = { glob = "/src/*.py", text = "x" }', "contains.glob"),
    ('key_exists = { file = "/srv/a.json", key = "a" }', "key_exists.file"),
])
def test_absolute_path_is_named(tmp_path: Path, rule: str, field: str) -> None:
    message = _error(tmp_path, _check(rule))
    assert "absolute" in message and field in message


@pytest.mark.parametrize("rule, field", [
    ('exists = "../secrets"', "exists"),
    ('glob_count = "docs/../../*"', "glob_count"),
    ('key_exists = { file = "a/../../b.toml", key = "a" }', "key_exists.file"),
])
def test_dotdot_path_is_named(tmp_path: Path, rule: str, field: str) -> None:
    message = _error(tmp_path, _check(rule))
    assert "'..'" in message and field in message


def test_home_and_backslash_paths_are_refused(tmp_path: Path) -> None:
    assert "home-directory" in _error(tmp_path, _check('exists = "~/.ssh/id_rsa"'))
    assert "'/'" in _error(tmp_path, _check('exists = "docs\\\\a.md"'))


@pytest.mark.parametrize("path, expect", [
    ("a\\u0000b", "control character"),
    ("docs/\\tx.md", "control character"),
    (".", "'.' segment"),
    ("./docs/a.md", "'.' segment"),
    ("docs//a.md", "empty"),
    ("docs/", "empty"),
])
def test_non_canonical_path_is_refused(tmp_path: Path, path: str, expect: str) -> None:
    message = _error(tmp_path, _check(f'exists = "{path}"'))
    assert expect in message and "exists" in message


def test_key_file_suffix_ignores_case(tmp_path: Path) -> None:
    pack = load_pack(_write(tmp_path, _check('key_exists = { file = "Service.YAML", key = "a" }')))
    assert pack["checks"][0]["rule"] == {"kind": "key_exists", "file": "Service.YAML", "key": "a"}


def test_dotted_names_are_not_dotdot(tmp_path: Path) -> None:
    pack = load_pack(_write(tmp_path, _check('glob_count = "..github/x..md"')))
    assert pack["checks"][0]["rule"] == {"kind": "glob_count", "glob": "..github/x..md"}


ROW = '''
[[rows]]
id = "p-glossary"
title = "Glossary"
scope = "team"
aggregation = "min"

  [[rows.levels]]
  level = 1
  truth = ["docs"]
  criteria = [{ signal = "pack.p.c", op = "eq", value = true }]
'''


def test_rows_load_through_the_levels_parser(tmp_path: Path) -> None:
    """#1274 lifts the #1272 refusal: rows parse with the lib/levels.py grammar."""
    pack = load_pack(_write(tmp_path, _check('exists = "a"') + ROW))
    assert [r["id"] for r in pack["rows"]] == ["p-glossary"]
    assert pack["rows"][0]["levels"][0]["truth"] == ["docs"]
    without = load_pack(_write(tmp_path, _check('exists = "a"')))
    assert without["rows"] == [] and without["sha256"] != pack["sha256"]


def test_a_pack_of_rows_alone_loads(tmp_path: Path) -> None:
    assert load_pack(_write(tmp_path, HEADER + ROW))["checks"] == []


def test_a_pack_with_neither_checks_nor_rows_is_refused(tmp_path: Path) -> None:
    assert "[[rows]]" in _error(tmp_path, HEADER)


def test_a_requires_outside_the_pack_is_left_to_layering(tmp_path: Path) -> None:
    text = HEADER + ROW.replace('truth = ["docs"]', 'truth = ["docs"]\n  requires = ["layer-0-instructions.1"]')
    assert load_pack(_write(tmp_path, text))["rows"][0]["levels"][0]["requires"] == ["layer-0-instructions.1"]


@pytest.mark.parametrize("old, new, expect", [
    ('truth = ["docs"]\n', "", "truth"),
    ('scope = "team"', 'scope = "galaxy"', "scope"),
    ('op = "eq"', 'op = "like"', "rows:"),
    ('level = 1', 'level = 2', "rows:"),
])
def test_a_malformed_row_is_a_pack_error(tmp_path: Path, old: str, new: str, expect: str) -> None:
    assert expect in _error(tmp_path, HEADER + ROW.replace(old, new))


def test_a_duplicate_row_id_in_one_pack_is_refused(tmp_path: Path) -> None:
    assert "duplicate" in _error(tmp_path, HEADER + ROW + ROW)


@pytest.mark.parametrize("body", ['glob_count = "docs/[z-a].md"',
                                  'contains = { glob = "src/[z-a]/*.py", text = "x" }'])
def test_a_glob_that_does_not_compile_is_a_load_error_naming_the_check(tmp_path: Path, body: str) -> None:
    """Carried from the #1284 review: never a collector failure that blanks both blocks."""
    message = _error(tmp_path, _check(body))
    assert "checks[0] ('c')" in message and "[z-a]" in message and "not a valid glob" in message


@pytest.mark.parametrize("body, expect", [
    ("", "exactly one rule"),
    ('exists = "a"\nglob_count = "b"', "exactly one rule"),
    ('exists = 3', "exists"),
    ('contains = "x"', "inline table"),
    ('contains = { glob = "*.md" }', "contains.text"),
    ('key_exists = { file = "a.ini", key = "x" }', "key_exists.file"),
    ('key_exists = { file = "a.toml", key = "a..b" }', "key_exists.key"),
])
def test_malformed_rule_is_named(tmp_path: Path, body: str, expect: str) -> None:
    assert expect in _error(tmp_path, _check(body))


@pytest.mark.parametrize("text, expect", [
    ('[[checks]]\nid = "c"\ntitle = "T"\nwhy = "W"\nexists = "a"\n', "[pack]"),
    ('[pack]\nname = "Bad-Name"\nversion = "1"\n', "pack.name"),
    ('[pack]\nname = "p"\n', "pack.version"),
    ('[pack]\nname = "p"\nversion = "1"\n', "checks"),
    (HEADER + '[[checks]]\nid = "9c"\ntitle = "T"\nwhy = "W"\nexists = "a"\n', "checks[0].id"),
    (HEADER + '[[checks]]\nid = "c"\nwhy = "W"\nexists = "a"\n', "title"),
    (HEADER + '[[checks]]\nid = "c"\ntitle = "T"\nexists = "a"\n', "why"),
])
def test_malformed_pack_is_named(tmp_path: Path, text: str, expect: str) -> None:
    assert expect in _error(tmp_path, text)


@pytest.mark.parametrize("text, expect", [
    ('[pack]\nname = "p\\n"\nversion = "1"\n\n[[checks]]\nid = "c"\ntitle = "T"\nwhy = "W"\nexists = "a"\n',
     "pack.name"),
    (HEADER + '[[checks]]\nid = "c\\n"\ntitle = "T"\nwhy = "W"\nexists = "a"\n', "checks[0].id"),
    (_check('key_exists = { file = "a.json", key = "a.b\\n" }'), "key_exists.key"),
])
def test_trailing_newline_never_reaches_a_name_or_key(tmp_path: Path, text: str, expect: str) -> None:
    assert expect in _error(tmp_path, text)


def test_file_must_be_named_pack_toml(tmp_path: Path) -> None:
    with pytest.raises(PackError, match=r"\.pack\.toml"):
        load_pack(_write(tmp_path, VALID, name="team.toml"))


def test_unreadable_or_invalid_toml_is_a_pack_error(tmp_path: Path) -> None:
    with pytest.raises(PackError, match="cannot read pack"):
        load_pack(tmp_path / "missing.pack.toml")
    with pytest.raises(PackError, match="cannot read pack"):
        load_pack(_write(tmp_path, "[pack\n"))


def test_non_table_check_is_refused() -> None:
    with pytest.raises(PackError, match="must be a table"):
        parse_pack({"pack": {"name": "p", "version": "1"}, "checks": ["exists"]})


def test_loader_runs_nothing() -> None:
    """The loader holds no way to run code or import a plugin (issue success criterion)."""
    banned = re.compile(r"subprocess|os\.system|importlib|entry_points|eval\(|exec\(")
    for path in sorted(LIB_DIR.glob("packs*")):
        assert not banned.search(path.read_text(encoding="utf-8")), path.name
