"""Practice packs: the ``*.pack.toml`` format and its strict loader (#1272, part A of #1266).

A practice pack lets a team add its own checks to /assess without running code
from the assessed repository. A pack holds declarative rules only; the loader
validates them and returns a typed ``Pack``. Evaluating the rules against a
repository is the pack collector's job (#1273), not this module's.

Pack file (TOML, read with the stdlib ``tomllib``; the name ends ``.pack.toml``):

```toml
[pack]
name = "payments"          # ^[a-z][a-z0-9_]*$
version = "2026.11"        # stamped with the pack's identity

[[checks]]
id = "glossary"            # ^[a-z][a-z0-9_]*$, unique in the pack
title = "Domain glossary"
why = "Agents need the domain's words before they touch the code."
exists = "docs/glossary.md"                                   # true when the path exists

[[checks]]
id = "adrs"
title = "Architecture decision records"
why = "Decisions written down outlive the people who made them."
glob_count = "docs/adr/*.md"                                  # number of matching files

[[checks]]
id = "money_type"
title = "Money type in use"
why = "Floats lose pennies."
contains = { glob = "src/**/*.py", text = "Money(" }          # files holding the literal text

[[checks]]
id = "owner_set"
title = "Service owner declared"
why = "An agent needs to know who to ask."
key_exists = { file = "service.yaml", key = "metadata.owner" }  # TOML, YAML or JSON key
```

Rules:

- Each check has ``id``, ``title``, ``why`` and exactly one of the four rules.
  ``contains`` matches literal text only, never a regular expression.
  ``key_exists`` names a ``.toml``, ``.yaml``, ``.yml`` or ``.json`` file and a
  dotted key with no empty segment.
- Every path and glob is relative to the assessed repository root, written with
  ``/``. An absolute path, a ``..`` segment, a ``~`` path or a backslash is a
  load error.
- Key sets are closed, as in ``lib/levels.py``: an unknown key anywhere is a load
  error naming its location. A key that would run code (``CODE_KEYS``) gets its
  own error that names the fix: express the check as one of the four rules;
  #1266 says why installed-package collectors are out of scope.
- ``[[rows]]`` is refused: rows in a pack arrive with layering (#1274).
- Each check publishes its result under ``pack.<pack>.<check>`` (``signal``), a
  namespace no core signal uses.
- ``Pack.sha256`` is the sha256 of the canonical JSON (sorted keys, no spaces,
  UTF-8) of the loaded pack: ``[pack]`` and every check's ``id``, ``title``,
  ``why`` and rule, in file order, as ``lib/fleet/fleet_method.py`` hashes a
  model. Any change to the file's content, the prose included, changes it;
  formatting and comments do not.

Loading is a pure function of the file: it reads that one file, touches no
repository and runs nothing. Every fault raises ``PackError``.
"""
from __future__ import annotations

import hashlib
import json
import re
import tomllib
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Literal, Mapping, TypedDict

PACK_SUFFIX = ".pack.toml"
SIGNAL_PREFIX = "pack"
NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")
_KEY_PATTERN = re.compile(r"^[^.\s]+(\.[^.\s]+)*$")
KEY_FILE_SUFFIXES = (".toml", ".yaml", ".yml", ".json")

RULES = ("exists", "glob_count", "contains", "key_exists")
# Keys that would make a pack run code. Each is refused by name, with the fix.
CODE_KEYS = frozenset({"command", "run", "exec", "script", "shell", "callable", "entry_point", "python"})

_ROOT_KEYS = frozenset({"pack", "checks"})
_PACK_KEYS = frozenset({"name", "version"})
_CHECK_KEYS = frozenset({"id", "title", "why"}) | frozenset(RULES)
_CONTAINS_KEYS = frozenset({"glob", "text"})
_KEY_EXISTS_KEYS = frozenset({"file", "key"})


class PackError(ValueError):
    """A pack file that cannot be loaded; the message names the field at fault."""


class ExistsRule(TypedDict):
    kind: Literal["exists"]
    path: str


class GlobCountRule(TypedDict):
    kind: Literal["glob_count"]
    glob: str


class ContainsRule(TypedDict):
    kind: Literal["contains"]
    glob: str
    text: str


class KeyExistsRule(TypedDict):
    kind: Literal["key_exists"]
    file: str
    key: str


Rule = ExistsRule | GlobCountRule | ContainsRule | KeyExistsRule


class PackCheck(TypedDict):
    id: str
    title: str
    why: str
    rule: Rule
    signal: str  # pack.<pack>.<check>


class Pack(TypedDict):
    name: str
    version: str
    checks: list[PackCheck]
    sha256: str


def signal_path(pack: str, check: str) -> str:
    """The signal path a check publishes: ``pack.<pack>.<check>``."""
    return f"{SIGNAL_PREFIX}.{pack}.{check}"


def load_pack(path: Path) -> Pack:
    """Read and validate one ``*.pack.toml`` file; raise ``PackError`` on any fault."""
    if not path.name.endswith(PACK_SUFFIX) or path.name == PACK_SUFFIX:
        raise PackError(f"{path.name}: a pack file is named '<name>{PACK_SUFFIX}'")
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise PackError(f"cannot read pack {path.name}: {exc}") from exc
    return parse_pack(data)


def parse_pack(data: Mapping[str, Any]) -> Pack:
    """Validate an already-parsed pack table; raise ``PackError`` on any fault."""
    if "rows" in data:
        raise PackError("rows: [[rows]] in a pack arrive with layering (#1274); "
                        "this format takes [[checks]] only")
    _check_keys(data, _ROOT_KEYS, "pack file")
    header = data.get("pack")
    if not isinstance(header, Mapping):
        raise PackError("pack: a [pack] table with 'name' and 'version' is required")
    _check_keys(header, _PACK_KEYS, "pack")
    name = _name(header.get("name"), "pack.name")
    version = _text(header.get("version"), "pack.version")
    raw_checks = data.get("checks")
    if not isinstance(raw_checks, list) or not raw_checks:
        raise PackError("checks: a pack needs one or more [[checks]] tables")
    checks: list[PackCheck] = []
    seen: set[str] = set()
    for index, raw in enumerate(raw_checks):
        check = _parse_check(raw, f"checks[{index}]", name)
        if check["id"] in seen:
            raise PackError(f"checks[{index}].id: duplicate check id {check['id']!r}")
        seen.add(check["id"])
        checks.append(check)
    return {"name": name, "version": version, "checks": checks,
            "sha256": pack_sha256(name, version, checks)}


def pack_sha256(name: str, version: str, checks: list[PackCheck]) -> str:
    """The sha256 of the canonical JSON of a loaded pack (the derived ``signal`` left out)."""
    body = {"pack": {"name": name, "version": version},
            "checks": [{"id": c["id"], "title": c["title"], "why": c["why"], "rule": c["rule"]}
                       for c in checks]}
    text = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _parse_check(raw: object, where: str, pack: str) -> PackCheck:
    if not isinstance(raw, Mapping):
        raise PackError(f"{where}: each check must be a table")
    _check_keys(raw, _CHECK_KEYS, where)
    check_id = _name(raw.get("id"), f"{where}.id")
    where = f"{where} ({check_id!r})"
    title = _text(raw.get("title"), f"{where}.title")
    why = _text(raw.get("why"), f"{where}.why")
    present = [key for key in RULES if key in raw]
    if len(present) != 1:
        found = ", ".join(present) if present else "none"
        raise PackError(f"{where}: a check needs exactly one rule of {', '.join(RULES)} (found: {found})")
    return {"id": check_id, "title": title, "why": why,
            "rule": _parse_rule(present[0], raw[present[0]], f"{where}.{present[0]}"),
            "signal": signal_path(pack, check_id)}


def _parse_rule(kind: str, value: object, where: str) -> Rule:
    if kind == "exists":
        return {"kind": "exists", "path": _relative(value, where)}
    if kind == "glob_count":
        return {"kind": "glob_count", "glob": _relative(value, where)}
    if not isinstance(value, Mapping):
        raise PackError(f"{where}: must be an inline table")
    if kind == "contains":
        _check_keys(value, _CONTAINS_KEYS, where)
        return {"kind": "contains", "glob": _relative(value.get("glob"), f"{where}.glob"),
                "text": _text(value.get("text"), f"{where}.text")}
    _check_keys(value, _KEY_EXISTS_KEYS, where)
    file = _relative(value.get("file"), f"{where}.file")
    if not file.lower().endswith(KEY_FILE_SUFFIXES):
        raise PackError(f"{where}.file: {file!r} must be a {', '.join(KEY_FILE_SUFFIXES)} file")
    key = _text(value.get("key"), f"{where}.key")
    if not _KEY_PATTERN.match(key):
        raise PackError(f"{where}.key: {key!r} must be a dotted key with no empty segment")
    return {"kind": "key_exists", "file": file, "key": key}


def _check_keys(table: Mapping[str, Any], allowed: frozenset[str], where: str) -> None:
    for key in table:
        if key in CODE_KEYS:
            raise PackError(
                f"{where}.{key}: a pack never runs code. Express the check as one rule: "
                f"{', '.join(RULES)}. Installed-package collectors are out of scope; see #1266.")
    unknown = sorted(set(table) - allowed)
    if unknown:
        raise PackError(f"{where}: unknown key {unknown[0]!r} (allowed: {', '.join(sorted(allowed))})")


def _text(value: object, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PackError(f"{where}: must be a non-empty string")
    return value


def _name(value: object, where: str) -> str:
    text = _text(value, where)
    if not NAME_PATTERN.match(text):
        raise PackError(f"{where}: {text!r} must match {NAME_PATTERN.pattern}")
    return text


def _relative(value: object, where: str) -> str:
    """A path or glob relative to the assessed repository root, or ``PackError``."""
    text = _text(value, where)
    if "\\" in text:
        raise PackError(f"{where}: {text!r} must use '/' as the separator")
    if text.startswith("~"):
        raise PackError(f"{where}: {text!r} is a home-directory path; use a path relative to the repository root")
    if PurePosixPath(text).is_absolute() or PureWindowsPath(text).drive:
        raise PackError(f"{where}: {text!r} is absolute; use a path relative to the repository root")
    if ".." in text.split("/"):
        raise PackError(f"{where}: {text!r} has a '..' segment; paths stay inside the repository root")
    return text
