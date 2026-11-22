"""The fleet settings file, ``--fleet-config FILE`` (issue #706).

One TOML file of fleet-wide lists and settings that ``fleet.py`` hands to every
repository scan (``assess_signals.py --fleet-config``). It has one section per
consumer; each consumer issue adds its keys to ``SECTION_KEYS``:

```toml
[ai_usage]   # agent identities (#695)
[review]     # AI review components (#696)
[platform]   # --platform settings (#714)
[identities] # confirmed bot and agent accounts (#765)
```

API:

- ``load_fleet_config(path)`` / ``parse_fleet_config(text)``: parse and validate.
  A TOML error, a top-level key that is not a known section, a section that is
  not a table, a key a section does not define, or a value of the wrong kind
  raises ``FleetConfigError``, whose message names the key (``review.foo``) and
  the expected kind, never the value. The CLIs exit 2 on it.
- ``ai_usage_section`` / ``review_section`` / ``platform_section`` /
  ``identities_section``: the typed
  view of one section. A section the file leaves out is empty.
- ``merge_repo_sections(fleet, repo_config)`` and ``repo_fleet_config(repo_root,
  fleet)``: the config one repository's scans read (the merge rule below).
- ``config_sha256`` and ``section_entries``: the run stamp's comparability key
  and per-section entry counts. Neither carries a pattern or a value.

Adding a key (a consumer PR): add one line to the section's table in
``SECTION_KEYS`` naming a validator (``string_list``, ``table_list``,
``string``, ``boolean``, ``positive_int``, or your own with the ``Validator``
signature; ``regex`` and ``one_of`` are here too), add the key to the section's TypedDict (``total=False``), and
document it in ``skills/fleet/references/fleet-config.md``. Scans read the merged config through
the registry input ``fleet_config`` (add it to the spec's ``reads``).

Merge rule. A repository's ``.assess/config.toml`` may carry the same sections.
They extend the fleet file for that repository and never replace it:

- a list key is the fleet's entries in their order, then the repository's
  entries that are not already present, in their order. A repository can add
  entries, never remove one, and a fleet entry keeps its index in every
  repository (per-rule counts are positional);
- a scalar key the fleet file sets keeps the fleet value; a repository value
  that differs is ignored with a warning naming the key. A scalar the fleet
  file leaves unset takes the repository's value.

The repository side is lenient, as ``assess_config`` is: a key or section that
does not validate there is skipped with a one-line warning, so a target
repository can never fail a fleet run. Without ``--fleet-config`` (and in
interactive ``/assess``) the fleet side is empty and the repository's sections
apply alone.

Canonical form. ``config_sha256`` hashes the validated config as sorted-key
JSON with empty sections left out: comments, whitespace, key order and a newly
defined empty section do not change it; list order does (it is meaningful).
``UNHASHED_KEYS`` (how a run goes, not what it measures: ``[platform] jobs`` and
``timeout``) are left out of it too.

The fleet settings file, `--fleet-config FILE` (issue #706), with the known sections
`[ai_usage]`, `[review]`, `[platform]` and `[identities]` (empty allowed; their keys are added by #695,
#696 and #714; `[review]` has `ai_review_components`, a string list read by
`review_automation`; `[platform]` has `jobs` and `timeout`, the defaults of `fleet.py
--platform-jobs` and `--platform-timeout`). API:
- `load_fleet_config(path)` / `parse_fleet_config(text)` validate against `SECTION_KEYS`
  and raise `FleetConfigError` naming the key (`review.foo`) and expected kind, never the
  value; both CLIs exit 2 on it.
- `ai_usage_section` / `review_section` / `platform_section` return the typed section
  (`AiUsageSection`, `ReviewSection`, `PlatformSection`; `total=False` TypedDicts).
- `merge_repo_sections(fleet, repo_config)` and `repo_fleet_config(repo_root, fleet)`: the
  merge rule. A repository's `.assess/config.toml` sections extend the fleet file: list
  keys are the fleet entries in order then the repository's new ones (fleet indices are
  stable); a scalar the fleet sets keeps the fleet value (a differing repository value is
  ignored with a warning); an unset scalar takes the repository value. Repository input
  that does not validate is skipped with a stderr warning, never raised.
- `config_sha256` (sorted-key JSON of the validated config: comments and key order do not
  count, list order does; `UNHASHED_KEYS`, settings of how a run goes such as `[platform]
  jobs` and `timeout`, are left out so they never break comparability) and `section_entries` (a list counts its items, else 1) feed
  the run stamp.

Keys so far: `[ai_usage]` `agent_identities` (`table_list` of `pattern` (`regex`,
required) and `field` (`one_of("email", "username", "name")`)) and
`token_users_as_agents` (`boolean`), read by `ai_usage` (#695) and the GitLab note
classifier (`gitlab_notes`, #716); `[review]` `ai_review_markers` (`string_list`), read by
`gitlab_notes` (#716); `[identities]` `agents`, `bots` and `people` (`string_list`
of literal accounts, #765), read through `identities_section` by `ai_usage.matcher_from_config`
(so also `delivery` and `gitlab_notes`) and counted in `section_entries`.
Adding a key: one line in the section's `SECTION_KEYS` table naming a validator
(`string_list`, `table_list(fields, required)`, `string`, `boolean`, `positive_int`,
`regex` (compiles; the error never quotes the pattern), `one_of(*choices)`, or
any `Validator`), the key in the section's TypedDict, and a row in `skills/fleet/references/fleet-config.md`.
A scan reads the merged config through the registry input `fleet_config` (a private setup
spec, `run_setup.fleet_config`, in `/assess`; `signals.build_inputs` supplies it merged with
the fleet file in the headless run). The value is private, never a run-context block, so it
needs no counts-only rule; a consumer reports counts only. Comparability: `fleet_compare`,
`fleet_history._MATCH_KEYS` and `fleet_trends.MATCH_KEYS` all key on
`fleet_config_sha256`. Tests: `tests/test_fleet_config.py`.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
import tomllib
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, NamedTuple, TypedDict, cast

from lib.agent_identity import RULE_FIELDS
from lib.assess_config import TomlTable, load_config


class FleetConfigError(ValueError):
    """The fleet settings file cannot be used; the message names the key."""


# (value, "section.key") -> the normalised value; raises FleetConfigError naming the key.
Validator = Callable[[object, str], object]


def string_list(value: object, key: str) -> object:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise FleetConfigError(f"{key}: expected a list of strings")
    return list(value)


def string(value: object, key: str) -> object:
    if not isinstance(value, str):
        raise FleetConfigError(f"{key}: expected a string")
    return value


def boolean(value: object, key: str) -> object:
    if not isinstance(value, bool):
        raise FleetConfigError(f"{key}: expected true or false")
    return value


def positive_int(value: object, key: str) -> object:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise FleetConfigError(f"{key}: expected a whole number of at least 1")
    return value


def regex(value: object, key: str) -> object:
    """A string that compiles as a Python regular expression. The error names the key,
    never the pattern or the ``re.error`` text (which quotes it)."""
    if not isinstance(value, str):
        raise FleetConfigError(f"{key}: expected a string")
    try:
        re.compile(value)
    except re.error:
        raise FleetConfigError(f"{key}: expected a valid regular expression") from None
    return value


def one_of(*choices: str) -> Validator:
    """A validator for a string that is one of ``choices``."""
    def validate(value: object, key: str) -> object:
        if not isinstance(value, str) or value not in choices:
            raise FleetConfigError(f"{key}: expected one of {', '.join(choices)}")
        return value
    return validate


def table_list(fields: Mapping[str, Validator], required: frozenset[str] = frozenset()) -> Validator:
    """A validator for an array of tables (``[[section.key]]``) with these fields."""
    def validate(value: object, key: str) -> object:
        if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
            raise FleetConfigError(f"{key}: expected a list of tables")
        tables: list[dict[str, object]] = []
        for i, item in enumerate(value):
            where = f"{key}[{i}]"
            unknown = sorted(set(item) - set(fields))
            if unknown:
                raise FleetConfigError(f"{where}.{unknown[0]}: unknown field "
                                       f"(known: {', '.join(sorted(fields)) or 'none'})")
            missing = sorted(required - set(item))
            if missing:
                raise FleetConfigError(f"{where}.{missing[0]}: required field missing")
            tables.append({name: fields[name](field, f"{where}.{name}") for name, field in item.items()})
        return tables
    return validate


# The sections and the keys each defines; each consumer issue adds its own.
SectionKeys = Mapping[str, Validator]
SECTION_KEYS: Mapping[str, SectionKeys] = {
    "ai_usage": {  # #695
        "agent_identities": table_list({"pattern": regex, "field": one_of(*RULE_FIELDS)},
                                       frozenset({"pattern"})),
        "token_users_as_agents": boolean,
    },
    "review": {  # #696, #716
        "ai_review_markers": string_list,  # #716
        "ai_review_components": string_list,  # #696
    },
    "platform": {"jobs": positive_int, "timeout": positive_int},  # #714
    # #765: literal accounts (an e-mail, a login such as ``codecov[bot]``), never a pattern.
    "identities": {"agents": string_list, "bots": string_list, "people": string_list},
}

# Keys that set how a run goes, never what it measures: left out of
# ``config_sha256`` so a change to one never makes two runs incomparable.
# ``[platform] jobs`` and ``timeout`` are the defaults of ``fleet.py
# --platform-jobs`` and ``--platform-timeout`` (the flags win).
UNHASHED_KEYS: Mapping[str, frozenset[str]] = {"platform": frozenset({"jobs", "timeout"})}


class AgentIdentityEntry(TypedDict, total=False):
    """One ``[[ai_usage.agent_identities]]`` rule: a regular expression and the field it
    reads (``email``, ``username`` or ``name``; left out, the e-mail and the username)."""

    pattern: str
    field: str


class AiUsageSection(TypedDict, total=False):
    """``[ai_usage]`` (#695): in-house agent identities, matched by ``ai_usage`` through
    ``agent_identity.AuthorMatcher``. Per-rule counts are reported by list position."""

    agent_identities: list[AgentIdentityEntry]
    token_users_as_agents: bool


class ReviewSection(TypedDict, total=False):
    """``[review]``: AI review settings (#696, #716). ``ai_review_markers``: strings an
    AI reviewer prints in its GitLab merge-request notes, matched as case-insensitive
    substrings on top of ``gitlab_notes.PINNED_MARKERS``. ``ai_review_components``: GitLab
    project or component paths and GitHub ``owner/repo`` actions that run an AI review in
    CI, read by ``review_automation``."""

    ai_review_markers: list[str]
    ai_review_components: list[str]


class PlatformSection(TypedDict, total=False):
    """``[platform]`` (#714): defaults for ``fleet.py --platform-jobs`` and ``--platform-timeout``."""

    jobs: int
    timeout: int


class IdentitiesSection(TypedDict, total=False):
    """``[identities]`` (#765): accounts the human confirmed, each a literal e-mail or
    login matched case-insensitively (``lib.agent_identity.ConfirmedIdentities``).
    ``agents`` count as agent authors; ``bots`` (automation) and ``people`` never do."""

    agents: list[str]
    bots: list[str]
    people: list[str]


class FleetConfig(TypedDict):
    """A validated config: every known section, empty when the file leaves it out."""

    ai_usage: AiUsageSection
    review: ReviewSection
    platform: PlatformSection
    identities: IdentitiesSection


class FleetConfigEntries(TypedDict):
    """Entries per section: a list key counts its items, any other key counts 1.
    A stamp written before #765 has no ``identities`` count."""

    ai_usage: int
    review: int
    platform: int
    identities: int


def empty_config() -> FleetConfig:
    return {"ai_usage": {}, "review": {}, "platform": {}, "identities": {}}


def ai_usage_section(config: FleetConfig) -> AiUsageSection:
    return config["ai_usage"]


def review_section(config: FleetConfig) -> ReviewSection:
    return config["review"]


def platform_section(config: FleetConfig) -> PlatformSection:
    return config["platform"]


def identities_section(config: FleetConfig) -> IdentitiesSection:
    """Empty for a config validated against a schema without the section."""
    return config.get("identities", {})


def _validate_section(name: str, table: object, keys: SectionKeys) -> dict[str, object]:
    if not isinstance(table, dict):
        raise FleetConfigError(f"{name}: expected a table ([{name}])")
    unknown = sorted(set(table) - set(keys))
    if unknown:
        raise FleetConfigError(f"{name}.{unknown[0]}: unknown key "
                               f"(known: {', '.join(sorted(keys)) or 'none yet'})")
    return {key: keys[key](value, f"{name}.{key}") for key, value in table.items()}


def validate_config(data: Mapping[str, object],
                    schema: Mapping[str, SectionKeys] | None = None) -> FleetConfig:
    """``data`` (parsed TOML) validated against ``schema`` (default ``SECTION_KEYS``);
    raises ``FleetConfigError``."""
    schema = SECTION_KEYS if schema is None else schema
    unknown = sorted(set(data) - set(schema))
    if unknown:
        raise FleetConfigError(f"{unknown[0]}: unknown section (known: {', '.join(sorted(schema))})")
    config: dict[str, object] = {name: {} for name in schema}
    for name, table in data.items():
        config[name] = _validate_section(name, table, schema[name])
    return cast(FleetConfig, config)


def parse_fleet_config(text: str, schema: Mapping[str, SectionKeys] | None = None) -> FleetConfig:
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise FleetConfigError(f"not valid TOML ({exc})") from exc
    return validate_config(data, schema)


def read_fleet_config(path: Path) -> str:
    """The file's text; raises ``FleetConfigError`` when it cannot be read."""
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise FleetConfigError(f"cannot read ({getattr(exc, 'strerror', None) or exc})") from exc


def load_fleet_config(path: Path, schema: Mapping[str, SectionKeys] | None = None) -> FleetConfig:
    """Read and validate ``path``; raises ``FleetConfigError``."""
    return parse_fleet_config(read_fleet_config(path), schema)


def canonical_json(config: FleetConfig) -> str:
    """Sorted-key JSON with empty sections and ``UNHASHED_KEYS`` left out, so adding a
    section later leaves every stored hash unchanged."""
    hashed = {name: {key: value for key, value in cast(Mapping[str, object], section).items()
                     if key not in UNHASHED_KEYS.get(name, frozenset())}
              for name, section in config.items()}
    present = {name: section for name, section in hashed.items() if section}
    return json.dumps(present, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def config_sha256(config: FleetConfig) -> str:
    """The comparability key: sha256 of the canonical form (no path, no file bytes)."""
    return hashlib.sha256(canonical_json(config).encode("utf-8")).hexdigest()


def _entries(section: Mapping[str, object]) -> int:
    return sum(len(value) if isinstance(value, list) else 1 for value in section.values())


def section_entries(config: FleetConfig) -> FleetConfigEntries:
    return {
        "ai_usage": _entries(config["ai_usage"]),
        "review": _entries(config["review"]),
        "platform": _entries(config["platform"]),
        "identities": _entries(identities_section(config)),
    }


class MergedConfig(NamedTuple):
    config: FleetConfig
    warnings: list[str]  # one line per repository key skipped or overridden


def _repo_section(name: str, table: object, keys: SectionKeys, warnings: list[str]) -> dict[str, object]:
    """The repository's keys for one section that validate; the rest become warnings."""
    if not isinstance(table, dict):
        warnings.append(f"{name}: expected a table; ignored")
        return {}
    valid: dict[str, object] = {}
    for key, value in table.items():
        if key not in keys:
            warnings.append(f"{name}.{key}: unknown key; ignored")
            continue
        try:
            valid[key] = keys[key](value, f"{name}.{key}")
        except FleetConfigError as exc:
            warnings.append(f"{exc}; ignored")
    return valid


def _extend(fleet: list[Any], repo: list[Any]) -> list[Any]:
    merged = list(fleet)
    for item in repo:
        if item not in merged:
            merged.append(item)
    return merged


def merge_repo_sections(fleet: FleetConfig, repo_config: Mapping[str, object],
                        schema: Mapping[str, SectionKeys] | None = None) -> MergedConfig:
    """``fleet`` extended by the known sections of one repository's parsed ``.assess/config.toml``.

    Other keys of ``repo_config`` (``exclude_dirs``, ``[gate]``, ...) belong to
    ``assess_config`` and are not read here.
    """
    schema = SECTION_KEYS if schema is None else schema
    warnings: list[str] = []
    merged: dict[str, object] = {}
    for name, keys in schema.items():
        base: dict[str, object] = dict(cast(Mapping[str, object], fleet.get(name, {})))
        if name in repo_config:
            for key, value in _repo_section(name, repo_config[name], keys, warnings).items():
                if key not in base:
                    base[key] = value
                elif isinstance(base[key], list) and isinstance(value, list):
                    base[key] = _extend(cast(list[Any], base[key]), value)
                elif base[key] != value:
                    warnings.append(f"{name}.{key}: set in the fleet config; the repository value is ignored")
        merged[name] = base
    return MergedConfig(cast(FleetConfig, merged), warnings)


def repo_fleet_config(repo_root: Path, fleet: FleetConfig | None = None) -> FleetConfig:
    """The config ``repo_root``'s scans read: ``fleet`` (empty without one) merged with its
    ``.assess/config.toml`` sections. Warnings go to stderr; never raises on repository input."""
    repo_config: TomlTable = load_config(repo_root)
    merged = merge_repo_sections(fleet if fleet is not None else empty_config(), repo_config)
    for warning in merged.warnings:
        print(f"warning: .assess/config.toml {warning}", file=sys.stderr)
    return merged.config
