"""The check manifest: every collector, signal, condition and finding, declared once (#789).

``skills/assess/checks/core.toml`` (owner ``core``) declares each check with its
metadata: what a collector reads, its stage and degrade behaviour, whether it needs
the network, how the headless runner reports its blocks, its counts-only privacy
classes and its typed shapes; each maturity-model signal's path, type and evidence
kind; display metadata (a supportive ``fix``) for each default criterion; and each
derived finding's order, action, mode and rules. The published contract for one
manifest is ``skills/assess/references/check-manifest.schema.json``.

This module is the strict runtime loader: stdlib ``tomllib`` plus allowed-key sets and
typed errors, in the style of ``levels.parse_levels``, because ``jsonschema`` is a dev
dependency only. Every problem is a structured ``check_validation.ValidationError``
(Meridian's shape, with a "did you mean" suggestion for a misspelt key or reference),
and ``load_manifest`` raises ``CheckManifestError`` carrying all of them. The loaded
value is the TOML document itself, cast to ``CheckManifest``: no defaults are filled
and nothing is re-keyed, so ``json.dumps`` of it validates against the schema.

Generated from it at import (#795, steps 3 and 4 of the #789 migration), through
:func:`core_manifest` (loaded once per process): the finding tables
(``keyhole_signals.FINDING_ORDER``, ``FINDING_ACTIONS``, ``FINDING_MODES``,
``GIT_HISTORY_FINDINGS``, ``_CHURN_DERIVED_FINDINGS``,
``assess_config.GATE_CONCERN_FINDINGS``) from :func:`finding_tables`, the
``hidden_coupling`` threshold defaults (``coupling_analysis``) from
:func:`finding_thresholds`, the counts-only ``counts_only_rules.RULES`` from
:func:`privacy_rules`, and the per-block ``reason`` descriptions of the published
signals and fleet-row schemas from :func:`reason_codes`. The scan registry, the
headless tables, the typed roots and the default maturity model are still code, checked
against the manifest in both directions by ``tests/test_check_manifest.py``. This
module imports none of those modules (they import it), so the agreement checks live
in the tests.

Ids: a collector is ``<owner>.<scan key>`` (``core.ci_checks``), a signal
``<owner>:<run-context path>`` (``core:ci_checks.enforced_check_runs``), a condition
``<owner>.cond.<name>`` (core uses ``<row>.<level>.<leaf>``), keyed by a unique
``<row>.<level>:<signal path>``, and a
finding ``<owner>.finding.<name>``. Grades are never authored: a signal carries its
producer ``evidence`` class (``level_signals.SIGNAL_INPUTS``, #783), and its grade
follows from that class.
"""
from __future__ import annotations

import functools
import re
import tomllib
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, Literal, NotRequired, TypedDict, cast

from lib.check_validation import ValidationError, closest, errors_only, finding, format_error

CORE_MANIFEST = Path(__file__).resolve().parents[2] / "checks" / "core.toml"
MANIFEST_VERSION = 1

Kind = Literal["collector", "signal", "condition", "finding"]
KINDS: tuple[Kind, ...] = ("collector", "signal", "condition", "finding")
# The registry's stages, in run order (``scan_registry.STAGES``; a test pins the copy).
STAGES = ("setup", "read_side", "post_offers", "final")
# The names the core passes to ``run_scans`` (``scan_registry.PROVIDED_INPUTS``).
PROVIDED_INPUTS = frozenset({"repo_root", "requested_scope", "run_date", "non_interactive"})
# Tokens for ``scan_registry.GATE_REASONS``, the reasons a scan may opt out of degrading.
GATE_REASONS = ("setup", "bad_scope", "self_degrading", "serialiser")
PRIVACY_CLASSES = ("path", "token", "reason", "id", "drop")
# Producer input classes (``level_signals.InputClass``, #783 and #789 3.1).
EVIDENCE = ("config_text", "head_text", "head_structure", "ci_executed",
            "git_history", "execution_artifact", "platform")
HEADLESS = ("registered", "headless_variant", "unregistered")
SIGNAL_TYPES = ("bool", "number", "string", "enum")
FINDING_MODES = ("characterize_first", "verify_then_retire", "refactor_safe")
CARRIED_IN = ("reason", "basis", "unmeasured_reason")
# The one ``suppressed_when`` expression the finding tables evaluate until the rule
# evaluator (#789 step 5): a finding that declares it is emptied on degenerate churn.
CHURN_SUPPRESSION = "doc_staleness.churn_degenerate == true"

_OWNER_RE = re.compile(r"^[a-z][a-z0-9_-]{0,39}$")
_ID_RE = re.compile(r"^[a-z][a-z0-9_.:-]{0,127}$")
_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_CALLABLE_RE = re.compile(r"^[a-z_][a-z0-9_.]*:[A-Za-z_][A-Za-z0-9_]*$")
# A reason code; a trailing ``<name>`` stands for a family (``no_import_graph_<language>``).
_CODE_RE = re.compile(r"^[a-z][a-z0-9_]{0,39}(<[a-z]+>)?$")
_KEY_RE = re.compile(r"^([a-z0-9-]+)\.([0-9]+):([a-z0-9_.]+)$")


class UnknownReason(TypedDict):
    code: str
    means: str
    next_step: str
    carried_in: NotRequired[Literal["reason", "basis", "unmeasured_reason"]]
    block: NotRequired[str]


class HeadlessVariant(TypedDict):
    """The headless runner's own spec for a block (``lib.headless_layers``, ``lib.headless_static``)."""

    block: str
    callable: str
    reads: list[str]
    stage: str


class ShapeRef(TypedDict):
    """A typed root (``counts_only_schema.ROOTS``): a run-context path and its TypedDict."""

    path: str
    type: str


class _EntryBase(TypedDict):
    id: str
    title: str
    summary: NotRequired[str]
    since: NotRequired[str]
    issues: NotRequired[list[int]]


class CollectorEntry(_EntryBase):
    kind: Literal["collector"]
    callable: str
    publishes: list[str]
    reads: list[str]
    stage: str
    revision: int
    offline_callable: NotRequired[str]
    private: NotRequired[bool]
    spread: NotRequired[bool]
    degrade: NotRequired[bool]
    gate_reason: NotRequired[str]
    label: NotRequired[str]
    network: NotRequired[bool]
    platform_pass: NotRequired[bool]
    platform_only: NotRequired[bool]
    headless: NotRequired[dict[str, str]]
    headless_variants: NotRequired[list[HeadlessVariant]]
    evidence: NotRequired[str]
    shapes: NotRequired[list[ShapeRef]]
    unknown_reasons: NotRequired[list[UnknownReason]]
    privacy: NotRequired[dict[str, str]]


class SignalEntry(_EntryBase):
    kind: Literal["signal"]
    path: str
    type: str
    collector: str
    evidence: str
    values: NotRequired[list[str]]
    null_means: NotRequired[str]


class ConditionEntry(_EntryBase):
    kind: Literal["condition"]
    key: str
    fix: str


class FindingEntry(_EntryBase):
    kind: Literal["finding"]
    name: str
    order: int
    action: str
    mode: str
    concern: bool
    inputs: list[str]
    derive: NotRequired[str]
    thresholds: NotRequired[dict[str, float]]
    suppressed_when: NotRequired[str]
    prune_missing_paths: NotRequired[bool]
    headless: NotRequired[str]


Entry = CollectorEntry | SignalEntry | ConditionEntry | FindingEntry


class CheckManifest(TypedDict):
    manifest_version: int
    owner: str
    entries: list[Entry]
    owner_version: NotRequired[str]


class CheckManifestError(ValueError):
    """The manifest is malformed; ``errors`` holds every structured finding."""

    def __init__(self, source: str, errors: Sequence[ValidationError]) -> None:
        self.errors = list(errors)
        lines = "\n".join(f"  {format_error(e)}" for e in self.errors)
        super().__init__(f"{source}: {len(self.errors)} check manifest error(s)\n{lines}")


# --- field grammar ---------------------------------------------------------------

Check = Callable[[Any], str]  # returns "" when the value fits, else what was expected


def _str(pattern: re.Pattern[str] | None = None, max_len: int = 0) -> Check:
    def check(v: Any) -> str:
        if not isinstance(v, str):
            return "a string"
        if pattern is not None and not pattern.match(v):
            return f"a string matching {pattern.pattern}"
        if max_len and len(v) > max_len:
            return f"a string of at most {max_len} characters"
        return ""
    return check


def _enum(values: Sequence[str]) -> Check:
    return lambda v: "" if isinstance(v, str) and v in values else f"one of {', '.join(values)}"


def _bool(v: Any) -> str:
    return "" if isinstance(v, bool) else "true or false"


def _int(minimum: int) -> Check:
    def check(v: Any) -> str:
        ok = isinstance(v, int) and not isinstance(v, bool) and v >= minimum
        return "" if ok else f"an integer of at least {minimum}"
    return check


def _number(v: Any) -> str:
    return "" if isinstance(v, (int, float)) and not isinstance(v, bool) else "a number"


def _list(item: Check, min_items: int = 0) -> Check:
    def check(v: Any) -> str:
        if not isinstance(v, list) or len(v) < min_items:
            return f"a list of at least {min_items} item(s)" if min_items else "a list"
        bad = [item(x) for x in v if item(x)]
        return f"a list whose items are {bad[0]}" if bad else ""
    return check


def _table(value: Check) -> Check:
    def check(v: Any) -> str:
        if not isinstance(v, dict):
            return "a table"
        bad = [value(x) for x in v.values() if value(x)]
        return f"a table whose values are {bad[0]}" if bad else ""
    return check


def _records(fields: Mapping[str, Check], required: frozenset[str]) -> Check:
    def check(v: Any) -> str:
        if not isinstance(v, list):
            return "a list of tables"
        for rec in v:
            if not isinstance(rec, dict) or set(rec) - set(fields) or required - set(rec):
                return f"a list of tables with keys {sorted(fields)} (required {sorted(required)})"
            bad = [f"{k}: {fields[k](x)}" for k, x in rec.items() if fields[k](x)]
            if bad:
                return f"a list of tables ({bad[0]})"
        return ""
    return check


_STRS = _list(_str())
_COMMON: dict[str, Check] = {
    "id": _str(_ID_RE), "kind": _enum(KINDS), "title": _str(max_len=120),
    "summary": _str(max_len=600), "since": _str(), "issues": _list(_int(1)),
}
_FIELDS: dict[str, dict[str, Check]] = {
    "collector": {
        "callable": _str(_CALLABLE_RE), "offline_callable": _str(_CALLABLE_RE),
        "publishes": _STRS, "reads": _STRS, "stage": _enum(STAGES), "revision": _int(1),
        "private": _bool, "spread": _bool, "degrade": _bool, "gate_reason": _enum(GATE_REASONS),
        "label": _str(_NAME_RE), "network": _bool, "platform_pass": _bool,
        "platform_only": _bool, "headless": _table(_enum(HEADLESS)),
        "headless_variants": _records(
            {"block": _str(_NAME_RE), "callable": _str(_CALLABLE_RE), "reads": _STRS,
             "stage": _enum(STAGES)}, frozenset({"block", "callable", "reads", "stage"})),
        "evidence": _enum(EVIDENCE),
        "shapes": _records({"path": _str(), "type": _str(_CALLABLE_RE)},
                           frozenset({"path", "type"})),
        "unknown_reasons": _records(
            {"code": _str(_CODE_RE), "carried_in": _enum(CARRIED_IN), "block": _str(_NAME_RE),
             "means": _str(max_len=300), "next_step": _str(max_len=300)},
            frozenset({"code", "means", "next_step"})),
        "privacy": _table(_enum(PRIVACY_CLASSES)),
    },
    "signal": {
        "path": _str(), "type": _enum(SIGNAL_TYPES), "collector": _str(_ID_RE),
        "evidence": _enum(EVIDENCE), "values": _STRS, "null_means": _str(max_len=300),
    },
    "condition": {"key": _str(_KEY_RE), "fix": _str(max_len=300)},
    "finding": {
        "name": _str(_NAME_RE), "order": _int(0), "action": _str(max_len=200),
        "mode": _enum(FINDING_MODES), "concern": _bool, "inputs": _STRS,
        "derive": _str(_CALLABLE_RE), "thresholds": _table(_number),
        "suppressed_when": _str(max_len=1024), "prune_missing_paths": _bool,
        "headless": _enum(("registered", "unregistered")),
    },
}
_REQUIRED: dict[str, frozenset[str]] = {
    "collector": frozenset({"callable", "publishes", "reads", "stage", "revision"}),
    "signal": frozenset({"path", "type", "collector", "evidence"}),
    "condition": frozenset({"key", "fix"}),
    "finding": frozenset({"name", "order", "action", "mode", "concern", "inputs"}),
}
_ROOT: dict[str, Check] = {
    "manifest_version": lambda v: "" if v == MANIFEST_VERSION and not isinstance(v, bool)
    else f"the integer {MANIFEST_VERSION}",
    "owner": _str(_OWNER_RE), "owner_version": _str(), "entries": lambda v: (
        "" if isinstance(v, list) else "a list of tables"),
}


def _keys(where: str, data: Mapping[str, Any], allowed: Mapping[str, Check],
          required: frozenset[str], out: list[ValidationError]) -> None:
    """Unknown, missing and mistyped keys of one table."""
    for key in data:
        if key not in allowed:
            out.append(finding(f"{where}.{key}", "UNKNOWN_KEY", f"unknown key {key!r}",
                               suggestion=closest(key, allowed), available=allowed))
        elif (expected := allowed[key](data[key])):
            out.append(finding(f"{where}.{key}", "WRONG_TYPE", f"expected {expected}"))
    for key in sorted(required - set(data)):
        out.append(finding(f"{where}.{key}", "MISSING_KEY", f"required key {key!r} is missing"))


def _entry_shape(i: int, raw: Any, owner: str, out: list[ValidationError]) -> bool:
    """Check one entry's keys and id form; True when it is well-formed enough to cross-check."""
    if not isinstance(raw, dict):
        out.append(finding(f"entries[{i}]", "WRONG_TYPE", "expected a table"))
        return False
    kind = raw.get("kind")
    where = f"entries[{i}] ({raw.get('id', '?')})"
    if kind not in KINDS:
        out.append(finding(f"{where}.kind", "UNKNOWN_KIND", f"kind {kind!r} is not one of {KINDS}",
                           suggestion=closest(str(kind), KINDS), available=KINDS))
        return False
    before = len(out)
    _keys(where, raw, {**_COMMON, **_FIELDS[kind]},
          frozenset({"id", "kind", "title"}) | _REQUIRED[kind], out)
    if len(out) > before:
        return False
    expected = _expected_id(raw, owner)
    if kind == "condition":
        fits = raw["id"].startswith(expected) and len(raw["id"]) > len(expected)
    else:
        fits = raw["id"] == expected
    if not fits:
        out.append(finding(f"{where}.id", "BAD_ID", f"a {kind} id must be {expected!r}"))
        return False
    return True


def _expected_id(raw: Mapping[str, Any], owner: str) -> str:
    """The id an entry must have (for a condition, the prefix its id must start with)."""
    kind = raw["kind"]
    if kind == "signal":
        return f"{owner}:{raw['path']}"
    if kind == "finding":
        return f"{owner}.finding.{raw['name']}"
    if kind == "condition":
        return f"{owner}.cond."  # a prefix: at least one character must follow
    name = raw["id"].removeprefix(f"{owner}.")
    return raw["id"] if raw["id"].startswith(f"{owner}.") and _NAME_RE.match(name) \
        else f"{owner}.<scan key>"


def scan_key(entry: CollectorEntry) -> str:
    """The scan registry key a collector entry declares (its id after the owner)."""
    return entry["id"].split(".", 1)[1]


def collectors(manifest: CheckManifest) -> list[CollectorEntry]:
    return [e for e in manifest["entries"] if e["kind"] == "collector"]


def signals(manifest: CheckManifest) -> list[SignalEntry]:
    return [e for e in manifest["entries"] if e["kind"] == "signal"]


def conditions(manifest: CheckManifest) -> list[ConditionEntry]:
    return [e for e in manifest["entries"] if e["kind"] == "condition"]


def findings(manifest: CheckManifest) -> list[FindingEntry]:
    return sorted((e for e in manifest["entries"] if e["kind"] == "finding"),
                  key=lambda f: f["order"])


def block_collectors(manifest: CheckManifest) -> dict[str, str]:
    """Run-context block -> the id of the collector that publishes it (private ones publish none)."""
    return {block: c["id"] for c in collectors(manifest) for block in c["publishes"]}


def condition_key(row: str, level: int, signal: str) -> str:
    """``<row>.<level>:<signal>``, the key a condition entry carries for one criterion."""
    return f"{row}.{level}:{signal}"


# --- cross-references --------------------------------------------------------------

def _collector_rules(c: CollectorEntry, where: str, out: list[ValidationError]) -> None:
    """The registry's per-spec rules (``scan_registry.validate``), restated for the manifest."""
    name, pub = scan_key(c), c["publishes"]
    if c.get("private") and c.get("spread"):
        out.append(finding(where, "PRIVATE_AND_SPREAD", "a collector is private or spread, not both"))
    elif c.get("private") and pub:
        out.append(finding(f"{where}.publishes", "PRIVATE_PUBLISHES", "a private collector publishes nothing"))
    elif c.get("spread") and not pub:
        out.append(finding(f"{where}.publishes", "SPREAD_EMPTY", "a spread collector names its keys"))
    elif not c.get("private") and not c.get("spread") and pub != [name]:
        out.append(finding(f"{where}.publishes", "PUBLISHES_KEY",
                           f"a plain collector publishes its own key, [{name!r}]"))
    if c.get("degrade", True) is False and "gate_reason" not in c:
        out.append(finding(f"{where}.gate_reason", "NO_GATE_REASON",
                           "a collector that opts out of degrading names a gate_reason"))
    if "offline_callable" in c and not c.get("network"):
        out.append(finding(f"{where}.offline_callable", "OFFLINE_NOT_NETWORK",
                           "only a network collector declares an offline_callable"))
    if c.get("platform_only") and not c.get("platform_pass"):
        out.append(finding(f"{where}.platform_only", "PLATFORM_ONLY_NOT_PASS",
                           "a platform-only collector runs in the platform pass"))
    variants = {v["block"] for v in c.get("headless_variants", [])}
    for block, status in c.get("headless", {}).items():
        if block not in pub:
            out.append(finding(f"{where}.headless.{block}", "UNPUBLISHED_BLOCK",
                               f"{name} does not publish {block!r}",
                               suggestion=closest(block, pub), available=pub))
        if (status == "headless_variant") != (block in variants):
            out.append(finding(f"{where}.headless.{block}", "VARIANT_MISMATCH",
                               "a block is headless_variant exactly when a headless_variants entry names it"))
    for block in sorted(variants - set(c.get("headless", {}))):
        out.append(finding(f"{where}.headless_variants", "VARIANT_MISMATCH",
                           f"headless_variants names {block!r}, which headless does not list"))
    _published_refs(c, where, out)


def _published_refs(c: CollectorEntry, where: str, out: list[ValidationError]) -> None:
    """A shape and a reason code's ``block`` sit under a block the collector publishes."""
    name, pub = scan_key(c), c["publishes"]
    for shape in c.get("shapes", []):
        if shape["path"].split(".")[0] not in pub:
            out.append(finding(f"{where}.shapes", "UNPUBLISHED_BLOCK",
                               f"shape {shape['path']!r} is not under a block {name} publishes"))
    for reason in c.get("unknown_reasons", []):
        if "block" in reason and reason["block"] not in pub:
            out.append(finding(f"{where}.unknown_reasons", "UNPUBLISHED_BLOCK",
                               f"reason {reason['code']!r} names {reason['block']!r}, which "
                               f"{name} does not publish", suggestion=closest(reason["block"], pub),
                               available=pub))


def _reads_graph(cols: Sequence[tuple[str, CollectorEntry]], out: list[ValidationError]) -> None:
    """Every read is provided by the core or by an earlier collector at the same or an earlier stage."""
    produced: dict[str, int] = {n: -1 for n in PROVIDED_INPUTS}
    written: dict[str, str] = {}
    for where, c in cols:
        name, stage = scan_key(c), STAGES.index(c["stage"])
        for read in c["reads"]:
            if read not in produced:
                out.append(finding(f"{where}.reads", "UNKNOWN_READ",
                                   f"{name} reads {read!r}, which no earlier collector produces",
                                   suggestion=closest(read, produced)))
            elif produced[read] > stage:
                out.append(finding(f"{where}.stage", "LATE_READ",
                                   f"{name} runs at {c['stage']!r} but reads {read!r}, produced later"))
        if name in PROVIDED_INPUTS:
            out.append(finding(f"{where}.id", "SHADOWS_INPUT", f"scan key {name!r} shadows a core input"))
        for block in c["publishes"]:
            if block in written:
                out.append(finding(f"{where}.publishes", "DUPLICATE_BLOCK",
                                   f"{block!r} is also published by {written[block]}"))
            written[block] = name
        produced[name] = stage
        # as scan_registry.validate: a spread entry's keys are readable from its stage on
        if c.get("spread"):
            produced.update({block: stage for block in c["publishes"]})


def _references(m: CheckManifest, where_of: Mapping[str, str], out: list[ValidationError]) -> None:
    by_id = {e["id"]: e for e in m["entries"]}
    collector_ids = [c["id"] for c in collectors(m)]
    blocks = block_collectors(m)
    for s in signals(m):
        where = where_of[s["id"]]
        owner = by_id.get(s["collector"])
        if owner is None or owner["kind"] != "collector":
            out.append(finding(f"{where}.collector", "UNKNOWN_COLLECTOR",
                               f"collector {s['collector']!r} is not declared",
                               suggestion=closest(s["collector"], collector_ids)))
        elif s["path"].split(".")[0] not in owner["publishes"]:
            out.append(finding(f"{where}.path", "UNPUBLISHED_BLOCK",
                               f"{s['collector']} does not publish {s['path'].split('.')[0]!r}"
                               f" (published by {blocks.get(s['path'].split('.')[0], 'nothing')})"))
    signal_paths = [s["path"] for s in signals(m)]
    keys: set[str] = set()
    for c in conditions(m):
        if c["key"] in keys:
            out.append(finding(f"{where_of[c['id']]}.key", "DUPLICATE_KEY",
                               f"condition key {c['key']!r} is declared twice"))
        keys.add(c["key"])
        sig = c["key"].split(":", 1)[1]
        if sig not in signal_paths:
            out.append(finding(f"{where_of[c['id']]}.key", "UNKNOWN_SIGNAL",
                               f"condition reads {sig!r}, which no signal entry declares",
                               suggestion=closest(sig, signal_paths)))
    classified: dict[str, str] = {}
    for col in collectors(m):
        for pattern in col.get("privacy", {}):
            if pattern in classified:
                out.append(finding(f"{where_of[col['id']]}.privacy", "DUPLICATE_PRIVACY",
                                   f"{pattern!r} is also classified by {classified[pattern]}"))
            classified[pattern] = col["id"]
    names = set(blocks) | {scan_key(c) for c in collectors(m)}
    orders: dict[int, str] = {}
    for f in findings(m):
        where = where_of[f["id"]]
        if f["order"] in orders:
            out.append(finding(f"{where}.order", "DUPLICATE_ORDER",
                               f"order {f['order']} is also {orders[f['order']]}"))
        orders[f["order"]] = f["name"]
        if f.get("suppressed_when", CHURN_SUPPRESSION) != CHURN_SUPPRESSION:
            out.append(finding(f"{where}.suppressed_when", "UNSUPPORTED_EXPRESSION",
                               f"the only supported expression is {CHURN_SUPPRESSION!r}"))
        for read in f["inputs"]:
            if read.split(".")[0] not in names:
                out.append(finding(f"{where}.inputs", "UNKNOWN_INPUT",
                                   f"input {read!r} names no block or collector",
                                   suggestion=closest(read.split(".")[0], names)))


def validate_manifest(data: Mapping[str, Any]) -> list[ValidationError]:
    """Every structural and cross-reference finding for a parsed manifest document."""
    out: list[ValidationError] = []
    _keys("manifest", data, _ROOT, frozenset({"manifest_version", "owner", "entries"}), out)
    if out:
        return out
    owner = cast(str, data["owner"])
    where_of: dict[str, str] = {}
    cols: list[tuple[str, CollectorEntry]] = []
    for i, raw in enumerate(data["entries"]):
        if not _entry_shape(i, raw, owner, out):
            continue
        where = f"entries[{i}] ({raw['id']})"
        if raw["id"] in where_of:
            out.append(finding(f"{where}.id", "DUPLICATE_ID", f"id {raw['id']!r} is declared twice"))
        where_of[raw["id"]] = where
        if raw["kind"] == "collector":
            cols.append((where, cast(CollectorEntry, raw)))
            _collector_rules(cast(CollectorEntry, raw), where, out)
    if out:
        return out
    _reads_graph(cols, out)
    _references(cast(CheckManifest, data), where_of, out)
    return out


def parse_manifest(data: Mapping[str, Any], source: str = "check manifest") -> CheckManifest:
    """``data`` as a ``CheckManifest``; raises ``CheckManifestError`` on any error finding."""
    errors = errors_only(validate_manifest(data))
    if errors:
        raise CheckManifestError(source, errors)
    return cast(CheckManifest, data)


def load_manifest(path: Path = CORE_MANIFEST) -> CheckManifest:
    """Read and validate a manifest file (the core manifest by default)."""
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as e:
        raise CheckManifestError(path.as_posix(), [finding("manifest", "TOML_ERROR", str(e))]) from e
    return parse_manifest(data, path.as_posix())


@functools.cache
def core_manifest() -> CheckManifest:
    """The validated core manifest, loaded once per process (the tables below read it at import)."""
    return load_manifest()


# --- tables generated from a manifest (#795) ------------------------------------------

class FindingTables(TypedDict):
    """The finding tables ``keyhole_signals`` and ``assess_config`` expose, from the manifest."""

    order: list[str]
    actions: dict[str, str]
    modes: dict[str, str]
    concern: list[str]
    prune_missing_paths: frozenset[str]
    churn_suppressed: frozenset[str]


def finding_tables(manifest: CheckManifest) -> FindingTables:
    """Every table keyed by finding name, in ``order`` (the report and severity order)."""
    entries = findings(manifest)
    return {
        "order": [f["name"] for f in entries],
        "actions": {f["name"]: f["action"] for f in entries},
        "modes": {f["name"]: f["mode"] for f in entries},
        "concern": [f["name"] for f in entries if f["concern"]],
        "prune_missing_paths": frozenset(f["name"] for f in entries if f.get("prune_missing_paths")),
        "churn_suppressed": frozenset(f["name"] for f in entries
                                      if f.get("suppressed_when") == CHURN_SUPPRESSION),
    }


def finding_thresholds(manifest: CheckManifest, name: str) -> dict[str, float]:
    """The ``thresholds`` table of one finding (empty when it declares none)."""
    return next((dict(f.get("thresholds", {})) for f in findings(manifest) if f["name"] == name), {})


def privacy_rules(manifest: CheckManifest) -> dict[str, dict[str, str]]:
    """Scan key -> its collector's privacy table, in manifest (scan-table) order."""
    return {scan_key(c): dict(c["privacy"]) for c in collectors(manifest) if c.get("privacy")}


def reason_codes(manifest: CheckManifest, block: str) -> list[UnknownReason]:
    """The codes a block's ``reason`` may carry: its collector's ``unknown_reasons`` carried in
    ``reason`` (the default) that name this block or no block."""
    declared = [r for c in collectors(manifest) if block in c["publishes"]
                for r in c.get("unknown_reasons", [])]
    return [r for r in declared
            if r.get("carried_in", "reason") == "reason" and r.get("block", block) == block]
