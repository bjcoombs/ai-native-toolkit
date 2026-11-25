"""Counts-only output: a content-free copy of a run-context (issue #546).

A run-context carries text a third party should not redistribute: finding
lines, commands, commit subjects, program names, author identities. When a
scan of someone else's repository is meant to leave the machine it ran on,
``counts_only`` keeps the derived values and drops the rest:

- every number, boolean and null is kept;
- a string is kept only when ``lib.counts_only.counts_only_rules`` classifies its field
  as a path, a short token (kind, grade, band, date, version) or a reason
  code (the code of an unavailable block's reason, never its sentence);
- every other string is dropped, and a dropped list of strings becomes its
  length, so "6 signals" survives where the six sentences do not.

Paths are kept as written by default. With ``hash_paths`` every path value and
every key of a path-keyed map becomes ``h:`` plus 16 hex digits of an
HMAC-SHA256 under a salt. The salt is random per call unless one is given, so
two runs' hashes join only when the caller fixes the salt (``--hash-salt``).
The salt itself is never written to the output.

A map listed in ``IDENTIFIER_KEYED_MAPS`` keeps its identifier-like keys; an entry whose
last segment is ``*`` covers every map one level below the one named, so ``pack.*`` keeps
the check ids of each practice pack under ``pack`` (#1273).

Pure: the input is never mutated, and nothing here reads the filesystem. The
CLI helpers at the bottom give ``assess_core`` (and the headless entry point
of #545) one set of flags.

``render_doc`` writes ``references/counts-only.md`` with one section per block, sorted by
block name, so a new collector's section lands at a fixed place and two PRs that add
different collectors merge cleanly (#1239).

The content-free run-context mode for scans of repositories you do not own (#546).
`counts_only(ctx, hash_paths=, salt=)` returns a copy that keeps every number, boolean
and null and only the strings whose field `counts_only_rules` classifies as a path or a
short token (kind, grade, band, date, version); free text, commands, commit subjects,
program names and identities are dropped, and a dropped list of strings becomes its
length. A string field with no rule is dropped too (allow-list, not deny-list). The
`timings` diagnostic block (#1380) is left out whole: it differs on every run. Path
hashing is opt-in: HMAC-SHA256 under a random per-call salt, or a fixed `--hash-salt`
so hashes join across runs. `unclassified_strings` lists the string fields a
run-context holds with no rule. A block that degraded to the scan table's unavailable marker is read as a record even where its key is path-keyed or dropped, so it survives as `{"available": false}` (#623). `add_cli_arguments` / `validate_cli_args` /
`apply_cli` give `assess_core.py --counts-only` (which writes
`run-context.counts-only.json` beside `run-context.json`) and the headless entry point
one set of flags. `counts_only_scans` applies the filter to the `scans` map of an
`assess_signals.py --counts-only` document and keeps each unavailable block listed with
its `reason_code` (`no_remote`, `not_registered`, or `unavailable`). `reason_token`
reduces a `REASON` field (`structure.reason`) to its code: the leading code when the fixed
allow-list `import_graph.STRUCTURE_REASON_CODES` or `reason_codes.BLOCK_REASON_CODES` (`no_ownership_map`, the `structure_drift.reason` marker, #908) names it (so a path or a name never leaks), the code of
a known sentence (`REASON_SENTENCE_CODES`), or `unavailable`, and drops an empty reason,
so a full run keeps `no_import_graph_rust` or `no_grimp` as a signals run does (#764).
Tests: `tests/test_counts_only_reason.py`. `render_doc`
generates `references/counts-only.md`, the per-block field list (plus the
`fleet-data.json` table). `hash_value` hashes one key the way a kept path is hashed, for
the fleet's unit and repository keys (#703). Tests:
`tests/test_counts_only.py`, `tests/test_signals_counts_only.py`.
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import re
import secrets
from collections.abc import Mapping
from functools import cache
from typing import Any

from lib.scan_timings import DIAGNOSTIC_KEYS
from lib.counts_only.counts_only_rules import (
    DROP,
    EXTRA_FIELD_NAMES,
    FLEET_DATA_RULES,
    HASHED_PATH,
    ID,
    FLAT_RULES,
    IDENTIFIER_KEYED_MAPS,
    KEY_RE,
    PATH,
    PATH_KEYED_MAPS,
    REASON,
    RULES,
    TOKEN,
    TOKEN_RE,
    FieldClass,
    classify,
)
from lib.counts_only.counts_only_schema import typed_field_names
from lib.counts_only.reason_codes import reason_code as reason_code
from lib.counts_only.reason_codes import reason_token as reason_token
from lib.run_context_types import JsonDict

# The longest path kept; a longer "path" is more likely prose than a file.
_MAX_PATH = 400
HASH_PREFIX = "h:"
# Written beside run-context.json by ``--counts-only``.
COUNTS_ONLY_FILE = "run-context.counts-only.json"


def _child(pattern: str, key: str) -> str:
    return f"{pattern}.{key}" if pattern else key


# A URL or other ``scheme:`` value, and an e-mail address: never a repo path.
_SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
_EMAIL_RE = re.compile(r"[^\s/@]+@[^\s/@]+\.[A-Za-z]{2,}")


@cache
def field_names() -> frozenset[str]:
    """The record keys kept outside the declared maps.

    The fields of the typed block shapes, every segment a rule names, and
    ``EXTRA_FIELD_NAMES`` for the blocks built as dict literals. Any other key
    of a record is treated as data and dropped.
    """
    rule_segments = {seg for pattern in FLAT_RULES for seg in pattern.split(".")}
    return typed_field_names() | rule_segments | EXTRA_FIELD_NAMES


def _is_marker(value: Mapping[str, Any]) -> bool:
    """The scan table's unavailable marker: ``{"available": False, "reason": ...}``."""
    return value.get("available") is False and "reason" in value and len(value) == 2


def _key_kind(pattern: str, key: object, marker: bool = False) -> str | None:
    """``"path"``, ``"identifier"``, ``"field"``, or None for a data key to drop.

    A path-keyed block that degraded to the unavailable ``marker`` is a record:
    its ``available`` and ``reason`` keys are fields, not paths (#623).
    """
    if pattern in PATH_KEYED_MAPS and not marker:
        return "path"
    if not isinstance(key, str):
        return None
    if pattern in IDENTIFIER_KEYED_MAPS or f"{pattern.rpartition('.')[0]}.*" in IDENTIFIER_KEYED_MAPS:
        return "identifier" if KEY_RE.match(key) else None
    return "field" if key in field_names() else None


def _is_pathlike(value: str, *, allow_space: bool = False) -> bool:
    """A repo-relative path, glob or module name.

    Not absolute, not a URL or ``scheme:`` value, no e-mail, and no whitespace
    unless ``allow_space``: a path field can be fed a sentence read from a doc,
    so a value there with a space is dropped. A path-keyed map's keys come
    from the file tree, so ``docs/My Notes.md`` is accepted there.
    """
    return (
        0 < len(value) <= _MAX_PATH
        and value.isprintable()
        and (allow_space or not any(c.isspace() for c in value))
        and not value.startswith(("/", "~", "\\"))
        and not _SCHEME_RE.match(value)
        and not _EMAIL_RE.search(value)
    )


class _Filter:
    def __init__(self, hash_paths: bool, salt: str | None) -> None:
        self.hash_paths = hash_paths
        key = salt if salt is not None else secrets.token_hex(16)
        self._key = key.encode("utf-8")

    def path(self, value: str, *, allow_space: bool = False) -> str | None:
        return self._hashed(value) if _is_pathlike(value, allow_space=allow_space) else None

    def identifier(self, value: str) -> str | None:
        """An external id: no whitespace or e-mail, hashed like a path."""
        ok = (0 < len(value) <= _MAX_PATH and value.isprintable()
              and not any(c.isspace() for c in value) and not _EMAIL_RE.search(value))
        return self._hashed(value) if ok else None

    def _hashed(self, value: str) -> str:
        if not self.hash_paths:
            return value
        digest = hmac.new(self._key, value.encode("utf-8"), hashlib.sha256)
        return HASH_PREFIX + digest.hexdigest()[:16]

    def string(self, cls: FieldClass | None, value: str) -> str | None:
        if cls == PATH:
            return self.path(value)
        if cls == ID:
            return self.identifier(value)
        if cls == HASHED_PATH:
            # a folder from the file tree, only ever written hashed: no plaintext-path test,
            # so a folder with a space or an "@" keeps its join key
            return self._hashed(value) if self.hash_paths and value else None
        if cls == TOKEN and TOKEN_RE.match(value):
            return value
        if cls == REASON:
            return reason_token(value)
        return None

    def value(self, pattern: str, value: Any) -> tuple[bool, Any]:
        """``(keep, filtered)`` for one value at ``pattern``."""
        if value is None or isinstance(value, (bool, int, float)):
            return True, value
        if isinstance(value, str):
            out = self.string(classify(pattern), value)
            return out is not None, out
        if isinstance(value, Mapping):
            # A dropped field that degraded keeps its marker's flag (#623).
            if classify(pattern) == DROP and not _is_marker(value):
                return False, None
            return True, self.mapping(pattern, value)
        if isinstance(value, list):
            return self.sequence(pattern, value)
        return False, None

    def mapping(self, pattern: str, value: Mapping[str, Any]) -> JsonDict:
        out: JsonDict = {}
        marker = _is_marker(value)
        for key, item in value.items():
            kind = _key_kind(pattern, key, marker)
            new_key: str | None
            if kind == "path":
                new_key = self.path(str(key), allow_space=True)
                child = _child(pattern, "{}")
            else:
                new_key = str(key) if kind is not None else None
                child = _child(pattern, str(key))
            if new_key is None:
                continue
            keep, filtered = self.value(child, item)
            if keep:
                out[new_key] = filtered
        return out

    def sequence(self, pattern: str, value: list[Any]) -> tuple[bool, Any]:
        cls = classify(pattern)
        if cls == DROP and not any(isinstance(v, (Mapping, list)) for v in value):
            return True, len(value)
        if value and all(isinstance(v, str) for v in value) and cls not in (PATH, TOKEN, ID, HASHED_PATH):
            return True, len(value)
        out: list[Any] = []
        for item in value:
            keep, filtered = self.value(pattern, item)
            if keep:
                out.append(filtered)
        return True, out


def counts_only(
    ctx: Mapping[str, Any], *, hash_paths: bool = False, salt: str | None = None,
) -> JsonDict:
    """A content-free copy of ``ctx``: counts, flags, tokens and paths only.

    ``hash_paths`` replaces every kept path (and every path-keyed map key) with
    a salted hash; ``salt`` fixes the hash key so hashes join across runs, and
    None draws a random one for this call. A diagnostic block (``scan_timings.DIAGNOSTIC_KEYS``:
    the run's ``timings``, #1380) is left out whole.
    """
    measured = {key: value for key, value in ctx.items() if key not in DIAGNOSTIC_KEYS}
    return _Filter(hash_paths, salt).mapping("", measured)


def hash_path(value: str, salt: str) -> str | None:
    """``value`` hashed as ``counts_only(..., hash_paths=True, salt=salt)`` hashes
    a path-keyed map key (spaces allowed: the value is a path from config or the
    file tree, never a sentence), so a fleet file written beside the scans joins
    with them; None when ``value`` is not path-like."""
    return _Filter(True, salt).path(value, allow_space=True)


HASHED_RE = re.compile(r"h:[0-9a-f]{16}")


def is_hashed(value: str) -> bool:
    """``value`` is already a ``hash_value`` output: a stored id hashed at the output boundary (#909)."""
    return HASHED_RE.fullmatch(value) is not None


def hash_value(value: str, salt: str) -> str:
    """``value`` as ``h:`` plus 16 hex digits under ``salt``, the hash every kept path gets,
    for a key that must always hash (a fleet unit or repository key, #703)."""
    return _Filter(True, salt)._hashed(value)


def unclassified_strings(ctx: Mapping[str, Any]) -> list[str]:
    """Field patterns in ``ctx`` that hold a string but have no rule.

    Run over an unfiltered run-context. The filter drops these fields, so the
    output is safe either way; an entry here means a field is being dropped by
    default rather than by decision, and the tests fail until it is classified.
    A record key that is not a known field name (and is not a key of a
    declared path- or identifier-keyed map) is reported as ``<pattern>.<key>?``.
    """
    found: set[str] = set()

    def walk(pattern: str, value: Any) -> None:
        if isinstance(value, str):
            if classify(pattern) is None:
                found.add(pattern)
        elif isinstance(value, Mapping):
            if classify(pattern) == DROP and not _is_marker(value):
                return
            marker = _is_marker(value)
            for key, item in value.items():
                kind = _key_kind(pattern, key, marker)
                if kind == "path":
                    walk(_child(pattern, "{}"), item)
                elif kind is None:
                    found.add(_child(pattern, f"{key}?"))
                else:
                    walk(_child(pattern, str(key)), item)
        elif isinstance(value, list):
            for item in value:
                walk(pattern, item)

    walk("", ctx)
    return sorted(found)


_CLASS_LABEL = {PATH: "path (hashed when hashing is on)", TOKEN: "token",
                REASON: "reason code",
                ID: "external id (hashed when hashing is on)", DROP: "dropped",
                HASHED_PATH: "path, dropped unless hashing is on (then hashed)"}


def render_doc() -> str:
    """The per-block field list in ``references/counts-only.md``."""
    lines = [
        "# Counts-only output: fields kept per block",
        "",
        "Generated from each collector's `privacy` table in `checks/core.toml` and",
        "the global rules in `scripts/lib/counts_only/counts_only_rules.py` (in the assess skill)",
        "by `lib.counts_only.content_free.render_doc`; `test_counts_only.py` fails when this file",
        "and the table disagree. Each section below is one collector, sorted by block name. Regenerate with:",
        "",
        "```bash",
        "# from the assess skill directory",
        "uv run python -c \"import sys; sys.path.insert(0, 'scripts');"
        " from lib.counts_only.content_free import render_doc; print(render_doc(), end='')\""
        " > references/counts-only.md",
        "```",
        "",
        "`assess_core.py --counts-only` writes `run-context.counts-only.json` beside",
        "`run-context.json`. `assess_signals.py --counts-only` filters each block",
        "under `scans` in its `--out` document the same way, keeps only the code",
        "of an unavailable block's `reason` (`no_remote`, `not_registered`, or",
        "`unavailable`), and writes `stamp.target_head` as null.",
        "",
        "Every number, boolean and null in every block is kept.",
        "A string is kept only when its field is listed below as a path or a",
        "token; every other string, listed or not, is dropped. A dropped list of",
        "strings is replaced by its length. A token is kept only while it is a",
        "slug of at most 40 characters (no spaces, `/` or `@`).",
        "",
        "Paths are kept as written unless `--hash-paths` (or `--hash-salt VALUE`)",
        "is given; then each path value and each key of a path-keyed map becomes",
        "`h:` plus 16 hex digits of an HMAC-SHA256. The salt is random per run",
        "unless `--hash-salt` fixes it, which makes hashes join across runs. The",
        "salt is never written to the output.",
        "",
        "A field pattern is the dotted run of keys from the top of",
        "`run-context.json`, list levels left out, `{}` for the key of a",
        "path-keyed map and `*` for any key. A path value with whitespace is",
        "dropped (a path field can be fed a sentence), but a path-keyed map's",
        "keys come from the file tree, so a key like `docs/My Notes.md` is kept",
        "(hashed when hashing is on). A field named `path`, `file`,",
        "`file_a`, `file_b`, `test_file` or `source_file` is a path in every block,",
        "and a field named `reason` is dropped in every block without its own rule.",
        "A field kept as a reason code keeps only the code of a `code: sentence`",
        "reason when it is a known structure code (`no_import_graph_rust`) or `no_ownership_map`,",
        "or the code a known sentence maps to (`no_grimp`), else `unavailable`,",
        "so a path or name before the colon is never kept; an empty reason is dropped.",
        "",
        "Not filtered: the wiki pages, hotspot pages, badge and report the run",
        "also writes stay in the target repository's `.assess/` directory and",
        "carry full text. Only `run-context.counts-only.json` (and an",
        "`assess_signals.py --counts-only` document) is content-free, with the",
        "`package-map.counts-only.svg`, `doc-drift.counts-only.svg` and",
        "`review-strip.counts-only.svg` drawn from it. Like the JSON, those copies keep",
        "path-class values (package ids, doc paths) in clear under `--counts-only` alone;",
        "only `--hash-paths` or `--hash-salt` hashes them, so share them from a hashed run.",
        "The Step ladder and the evidence waffle have no copy: they name only the model's Steps,",
        "gates and rows, and counts.",
        "",
        "## Path-keyed maps",
        "",
    ]
    lines += [f"- `{m}`" for m in sorted(PATH_KEYED_MAPS)]
    lines += [
        "",
        "## Identifier-keyed maps",
        "",
        "Keys are kinds, tools or subscore names, kept while they are identifiers.",
        "Every other object is a record: a key survives only when it is a known",
        "field name, so a map keyed by a login, a function or a directory is dropped.",
        "",
    ]
    lines += [f"- `{m}`" for m in sorted(IDENTIFIER_KEYED_MAPS)]
    for block, rules in sorted(RULES.items()):  # by block name: a new block lands at a fixed place (#1239)
        lines += ["", f"## {block}", "", "| Field | Kept as |", "|---|---|"]
        lines += [f"| `{p}` | {_CLASS_LABEL[c]} |" for p, c in rules.items()]
    lines += [
        "", "## fleet-data.json (the fleet skill)", "",
        "Patterns from the top of `fleet-data.json`, not of a run-context. A",
        "unit or repository key is always kept (hashed, spaces allowed, under",
        "path hashing); under hashing no name, owner or web URL is written.",
        "The run stamp (`run.*`) and maturity-model fields (`model.*`, the row ids,",
        "`repos.rows.signal`) come from the run and the `--levels` file and are",
        "kept as written, as is a criterion's `why` (`repos.rows.why`,",
        "`opportunities.biggest_lift.why`), the check manifest's own text. A string",
        "`repos.rows.value` that is not a token is written `set`.",
        "", "| Field | Kept as |", "|---|---|",
    ]
    lines += [f"| `{p}` | {_CLASS_LABEL[c]} |" for p, c in FLEET_DATA_RULES.items()]
    return "\n".join(lines) + "\n"


# --- CLI helpers shared by assess_core and the headless entry point -------

def add_cli_arguments(parser: argparse.ArgumentParser) -> None:
    """Add ``--counts-only``, ``--hash-paths`` and ``--hash-salt``."""
    parser.add_argument(
        "--counts-only", action="store_true",
        help=(
            "Content-free output: counts, grades, scores, booleans, kinds and "
            "repo-relative paths only, with free text, "
            "commands, commit subjects and identities dropped. For sharing a "
            "scan of a repository you do not own. assess_core writes "
            "run-context.counts-only.json beside run-context.json; "
            "assess_signals filters its --out document. "
            "Fields kept: references/counts-only.md."
        ),
    )
    parser.add_argument(
        "--hash-paths", action="store_true",
        help=(
            "With --counts-only, replace every repo-relative path with a salted "
            "hash. The salt is random per run unless --hash-salt fixes it."
        ),
    )
    parser.add_argument(
        "--hash-salt", metavar="VALUE",
        help=(
            "With --counts-only, hash paths under this salt (implies "
            "--hash-paths), so hashes from different runs join."
        ),
    )


def validate_cli_args(parser: argparse.ArgumentParser, parsed: argparse.Namespace) -> None:
    """Reject a hashing flag without ``--counts-only`` (exits via ``parser.error``)."""
    if (parsed.hash_paths or parsed.hash_salt is not None) and not parsed.counts_only:
        parser.error("--hash-paths and --hash-salt need --counts-only")
    if parsed.hash_salt == "":
        parser.error("--hash-salt needs a non-empty value")
    if parsed.counts_only and getattr(parsed, "opt_in_mutation", False):
        parser.error("--counts-only applies to a full run, not --opt-in-mutation")


def hash_options(parsed: argparse.Namespace) -> tuple[bool, str | None]:
    """``(hash_paths, salt)`` from the parsed flags; a salt implies hashing."""
    salt: str | None = parsed.hash_salt
    return bool(parsed.hash_paths) or salt is not None, salt


def apply_cli(ctx: Mapping[str, Any], parsed: argparse.Namespace) -> JsonDict | None:
    """The counts-only copy the parsed flags ask for, or None without ``--counts-only``."""
    if not parsed.counts_only:
        return None
    hash_paths, salt = hash_options(parsed)
    return counts_only(ctx, hash_paths=hash_paths, salt=salt)


def counts_only_scans(
    scans: Mapping[str, Any], *, hash_paths: bool = False, salt: str | None = None,
) -> JsonDict:
    """``counts_only`` over the ``scans`` map of an ``assess_signals`` document.

    The scan names are run-context keys, so the same rules apply. A block that
    reports ``available: false`` is always listed and keeps a ``reason`` (the
    signals schema requires one), reduced to its code by ``reason_code``.
    """
    out = counts_only(scans, hash_paths=hash_paths, salt=salt)
    for name, block in scans.items():
        if not (isinstance(block, Mapping) and block.get("available") is False):
            continue
        filtered = out.get(name)
        if not isinstance(filtered, dict):
            # A block whose ready shape is dropped text (a list of strings)
            # still reports that it was not run.
            filtered = out[name] = {"available": False}
        filtered["reason"] = reason_code(block.get("reason"))
    return out
