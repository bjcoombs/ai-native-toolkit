"""Per-repo `.assess/config.toml` reader.

A single, optional config file lets a repo persist `/assess` preferences that
would otherwise have to be re-supplied as CLI flags on every run. Today this
covers the user-supplied exclude lists (issue #50: repos that intentionally
track vetted-context / reference data need a durable escape hatch that
applies *across every scan* - the heatmap, the doc-navigability graph, the
doc-staleness association, and the dead-code/liveness scan).

Schema (top-level, no section - the file is already namespaced by living
under `.assess/`):

```toml
exclude_dirs = ["regulatory-raw", "vetted-context"]
exclude_patterns = ["*.csv", "*.parquet"]

# Working-notes overrides (issue #367): force or suppress the doc graph's
# working-notes classification for a directory, relative to the repo root.
working_notes_dirs = ["journal"]
working_notes_ignore = ["docs/chapters"]

# Provenance for generated-doc trees (issue #178): doc-staleness for docs
# under `path` is measured against `source` (newer source = stale) instead of
# the doc's own file age. `source` may be a string or a list. Paths are
# relative to the repo root.
[[generated]]
path = "notes"
source = "data/jira.tsv"

# Fleet settings sections (issue #706), read by `lib/fleet_config.py`, not here:
# they extend a `--fleet-config` file for this repository (lists append, the
# fleet's scalars win). Keys are defined by the consumers (#695, #696, #714).
[ai_usage]
token_users_as_agents = false   # GitLab access-token users count as agents
[[ai_usage.agent_identities]]   # an in-house agent (regex; field: email|username|name)
pattern = "^inhouse-agent"
[review]
[platform]

# Owner exclusions of maturity-model conditions (issue #910), read by
# `lib/owner_exclusions.py`, not here: a condition id, a required reason and a kind.
[checks]
exclude = [{ id = "layer-6-coverage.1", kind = "measured_elsewhere", reason = "Gated in the release repo." }]

# Monorepo units (issue #862), read by `lib/unit_discovery.py`, not here: each entry
# is one unit folder, replacing the discovered units.
[[units]]
path = "services/billing"
name = "Billing"

# Cross-repo doc freshness (issue #664), measured by /fleet only: `path` (a doc
# or folder here) describes `scope` (required, paths inside fleet repo `repo`).
[[describes]]
path = "services/billing.md"
repo = "grp/billing"
scope = ["api/"]
```

The exclude lists feed every scan. There is no per-scan override knob -
if the user excludes `regulatory-raw/`, they mean "this is reference data,
not source," and that statement applies to every layer's view of the
codebase. Consistency is the point.

Design choices:

- **Optional and additive.** A missing config is the default state, not an
  error. The built-in defaults baked into each scan always apply; the config
  only *extends* them. CLI flags layer on top of both.
- **Degrade silently on malformed input.** A broken TOML file should never
  block an assessment - the loader returns empty excludes and prints a
  one-line warning to stderr. Scans keep running on defaults.
- **No new dependencies.** `tomllib` is in the stdlib since Python 3.11
  (which the existing scripts already require).

Reads the optional per-repo `.assess/config.toml`: `exclude_dirs` / `exclude_patterns`
(the same two lists feed every scan - heatmap, doc graph, staleness, liveness - so
exclusion is consistent), the `[gate]` and `[structure]` sections, and the `[[generated]]`
folder->source provenance map (issue #178) consumed by `doc_provenance.py`, and the
`working_notes_dirs` / `working_notes_ignore` directory lists (issue #367), which
`load_working_notes_config` returns as a typed `WorkingNotesConfig` pair for both
`assess_core.py` and `doc-graph-svg.py`. `resolve_excludes`
is the single shared path that combines config excludes with CLI `--exclude`; both the treemap
CLI and `doc-graph-svg.py` call it, so every artifact computes over the identical doc/code set.
Degrades silently on missing or malformed config rather than blocking the run. The
`[ai_usage]`, `[review]` and `[platform]` sections are read by `fleet_config.py`, not here
(they extend a `--fleet-config` file, #706).
"""
from __future__ import annotations

import fnmatch
import sys
import tomllib
from pathlib import Path
from typing import Any, NamedTuple, TypedDict

from lib.check_manifest import core_manifest, finding_tables

# A parsed ``.assess/config.toml`` (or one of its sections): whatever TOML the
# user wrote, validated key by key by the loaders below.
TomlTable = dict[str, Any]


class StructureConfig(TypedDict):
    """``load_structure_config``: the ``[structure]`` settings."""

    keyhole_budget: int


class GateConfig(TypedDict):
    """``load_gate_config``: the normalised ``[gate]`` settings."""

    enabled: bool
    fail_on: list[str]
    warn_on: list[str]
    ccn_p95_max: float | None
    containment_min: float | None
    fail_on_regression: bool


# The tool's own directory under the scanned repo root: this config file lives
# there beside the wiki a run writes. `git_churn` imports both constants so the
# pathspecs its `dirty` check runs under are built from the same path this
# module reads, and a rename here cannot leave that check pointing at a path
# that no longer exists (issue #414).
ASSESS_DIR = ".assess"

CONFIG_FILE = "config.toml"


def is_user_excluded(rel: Path, extra_dirs: set[str],
                     extra_patterns: list[str]) -> bool:
    """True if `rel` matches a user-supplied exclude.

    `extra_dirs` is matched exactly against any component of the relative
    path. `extra_patterns` is matched as a basename glob via `fnmatch`.
    Either match is sufficient - the two lists are independent. Empty
    inputs always return False so callers can call unconditionally.

    Reused by every scan so the semantics of `--exclude` / `config.toml`
    are identical across the heatmap, the doc-navigability graph, the
    doc-staleness pass, and the liveness scan.
    """
    if extra_dirs and any(part in extra_dirs for part in rel.parts):
        return True
    if extra_patterns and any(
        fnmatch.fnmatch(rel.name, pat) for pat in extra_patterns
    ):
        return True
    return False


def load_config(repo_root: Path) -> TomlTable:
    """Read `<repo_root>/.assess/config.toml` and return the parsed dict.

    Returns `{}` when the file does not exist, isn't readable, or fails to
    parse. Malformed files print a one-line warning to stderr; missing files
    are silent (the common case).
    """
    config_path = (repo_root / ASSESS_DIR / CONFIG_FILE).resolve()
    if not config_path.is_file():
        return {}
    try:
        return tomllib.loads(config_path.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError, OSError) as e:
        print(
            f"warning: could not read {config_path} ({e}); "
            "continuing with defaults",
            file=sys.stderr,
        )
        return {}


def _string_list(config: TomlTable, key: str) -> list[str]:
    """Return `config[key]` filtered to strings only.

    Honours the "degrade silently" contract on three failure modes:

    - **Key missing**: returns `[]` (the common case).
    - **Value is not a list** (e.g. `exclude_dirs = "regulatory-raw"` or
      `exclude_dirs = 5`): returns `[]`. Iterating a string would produce
      single-character "dir names" that match unexpectedly; iterating an
      int would raise `TypeError` and propagate up through `load_excludes`
      into the orchestrator, blocking the assessment.
    - **List with non-string entries** (e.g. `exclude_dirs = ["foo", 42]`):
      drops the bad entry, keeps the rest. One malformed value doesn't
      poison the rest of the config.
    """
    value = config.get(key, [])
    if not isinstance(value, list):
        return []
    return [str(x) for x in value if isinstance(x, str)]


# Default comprehension-footprint budget (A1). A unit whose footprint --
# size + the public surface of its direct deps + its own exposed surface --
# exceeds this is one no agent can change completely from inside a single
# context window. The number is a tunable proxy for "a fraction of a reference
# window"; repos calibrate it via `.assess/config.toml` `[structure]`.
DEFAULT_KEYHOLE_BUDGET = 2000


def load_structure_config(repo_root: Path) -> StructureConfig:
    """Return the `[structure]` settings from `.assess/config.toml`.

    Currently a single key, `keyhole_budget` (the A1 comprehension-footprint
    budget), defaulting to `DEFAULT_KEYHOLE_BUDGET`. Honours the same
    "degrade silently" contract as the exclude loaders: a missing file,
    missing section, or malformed value falls back to the default rather
    than blocking the assessment.
    """
    cfg = load_config(repo_root)
    section = cfg.get("structure", {})
    budget = DEFAULT_KEYHOLE_BUDGET
    if isinstance(section, dict):
        value = section.get("keyhole_budget")
        # bool is an int subclass; reject it so `keyhole_budget = true` doesn't
        # silently become a budget of 1. Only a positive int is a valid budget.
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            budget = value
    return {"keyhole_budget": budget}


# The findings that represent a concern (the manifest's ``concern = true``: every
# finding except the one positive, ``refactor_boundary``), in the canonical finding
# order. Loaded from ``checks/core.toml`` (#795), the same source as
# ``keyhole_signals.FINDING_ORDER``, so a new finding cannot escape the default
# warn set.
GATE_CONCERN_FINDINGS = finding_tables(core_manifest())["concern"]


def _positive_number(section: TomlTable, key: str) -> float | None:
    """Return ``section[key]`` as a float when it is a positive real, else None.

    A threshold of zero or below is meaningless (every run would trip it), and a
    non-numeric value is malformed config - both degrade to "no threshold" rather
    than blocking the gate. ``bool`` is rejected (it is an ``int`` subclass) so
    ``ccn_p95_max = true`` doesn't silently become a threshold of 1.
    """
    value = section.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if value > 0 else None


def _parse_gate_section(cfg: TomlTable) -> GateConfig:
    """Build the gate settings from an already-parsed config dict.

    Split from ``load_gate_config`` so the same normalization serves both the
    convention path (``.assess/config.toml``) and an explicit ``--config`` file.
    """
    gate = cfg.get("gate", {})
    if not isinstance(gate, dict):
        gate = {}
    # Distinguish "missing" (use the default warn set) from an explicit empty
    # list (warn on nothing) - ``_string_list`` alone can't tell them apart.
    warn_on = (
        _string_list(gate, "warn_on")
        if "warn_on" in gate
        else list(GATE_CONCERN_FINDINGS)
    )
    enabled = gate.get("enabled", True)
    fail_on_regression = gate.get("fail_on_regression", False)
    return {
        "enabled": enabled if isinstance(enabled, bool) else True,
        "fail_on": _string_list(gate, "fail_on"),
        "warn_on": warn_on,
        "ccn_p95_max": _positive_number(gate, "ccn_p95_max"),
        "containment_min": _positive_number(gate, "containment_min"),
        "fail_on_regression": (
            fail_on_regression if isinstance(fail_on_regression, bool) else False
        ),
    }


def load_gate_config(repo_root: Path) -> GateConfig:
    """Return the ``[gate]`` settings from ``.assess/config.toml``.

    The gate is the CI check run by ``assess_gate.py``. Its defaults are
    deliberately **warn-only**: a repo that adopts the emitted workflow without
    writing any config never has a pipeline blocked by surprise. Every way to
    fail is strictly opt-in.

    Returned keys:

    - ``enabled`` (bool, default ``True``): master switch. ``false`` makes the
      gate always pass while still reporting, so a repo can mute it without
      deleting the workflow.
    - ``fail_on`` (list[str], default ``[]``): finding names whose presence (a
      non-empty ``paths`` list) fails the gate - an AI-readiness *floor*. Empty
      means warn-only.
    - ``warn_on`` (list[str], default = every concern finding): finding names
      reported but non-blocking. An explicit empty list silences warnings.
    - ``ccn_p95_max`` (float | None): floor - fail when the p95 file CCN exceeds.
    - ``containment_min`` (float | None): floor - fail when the safe-zone
      containment ratio drops below this (0-1).
    - ``fail_on_regression`` (bool, default ``False``): true *regression* check -
      fail when the cross-run diff (computed by ``assess_core`` against the prior
      committed snapshot) reports hotspots whose complexity/churn increased.

    Honours the same "degrade silently" contract as the other loaders: a missing
    file, missing section, or malformed value falls back to the default rather
    than blocking the assessment.
    """
    return _parse_gate_section(load_config(repo_root))


def load_gate_config_file(config_path: Path) -> GateConfig:
    """Return the ``[gate]`` settings from an explicit TOML file path.

    Used to honour ``assess_gate.py --config <path>`` when the gate config lives
    somewhere other than the conventional ``.assess/config.toml``. Degrades the
    same way as ``load_config``: a missing or malformed file yields defaults.
    """
    config_path = config_path.resolve()
    if not config_path.is_file():
        return _parse_gate_section({})
    try:
        cfg = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError, OSError) as e:
        print(
            f"warning: could not read {config_path} ({e}); continuing with defaults",
            file=sys.stderr,
        )
        cfg = {}
    return _parse_gate_section(cfg)


def load_generated_sources(repo_root: Path) -> list[tuple[str, list[str]]]:
    """Return the ``[[generated]]`` folder->source provenance mappings.

    Lets a repo declare that a generated-doc tree derives from a source file or
    command, so doc-staleness for those docs is measured against the source
    rather than the doc's own age (issue #178). Schema::

        [[generated]]
        path = "notes"
        source = "data/jira.tsv"

        [[generated]]
        path = "docs/api"
        source = ["openapi.yaml", "schema.proto"]

    ``path`` is a folder relative to the repo root; every doc under it inherits
    the mapping. ``source`` is a string or list of strings relative to the repo
    root. Returns a list of ``(path, [sources])`` tuples, in declaration order so
    the first matching prefix wins deterministically. Honours the same "degrade
    silently" contract as the other loaders: a missing file, missing/!list
    section, or malformed entry is skipped rather than blocking the assessment.
    """
    cfg = load_config(repo_root)
    raw = cfg.get("generated")
    if not isinstance(raw, list):
        return []
    out: list[tuple[str, list[str]]] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        path = entry.get("path")
        if not isinstance(path, str) or not path.strip():
            continue
        source = entry.get("source")
        if isinstance(source, str):
            sources = [source]
        elif isinstance(source, list):
            sources = [s for s in source if isinstance(s, str)]
        else:
            sources = []
        if not sources:
            continue
        out.append((path.strip(), sources))
    return out


class DescribesMapping(TypedDict):
    """One valid ``[[describes]]`` entry: repo-relative POSIX paths."""

    path: str
    repo: str
    scope: list[str]


class DescribesConfig(NamedTuple):
    """``load_describes``: the valid mappings and one message per rejected entry."""

    mappings: list[DescribesMapping]
    errors: list[str]


SCOPE_REMEDIATION = (
    "name the documented surface inside the service, for example "
    'scope = ["api/"], ["openapi.yaml"] or ["proto/"]; '
    'scope = ["."] maps the whole repository and is reported as low confidence'
)


def _clean_rel(raw: object, *, allow_root: bool) -> str | None:
    """``raw`` as a repo-relative POSIX path, or None when it is not one.

    These become git pathspecs, so an absolute path, a ``..`` segment and a
    leading ``:`` (pathspec magic) are rejected rather than resolved.
    """
    if not isinstance(raw, str):
        return None
    path = raw.strip().replace("\\", "/")
    if path.startswith(("/", ":", "~")) or not path.isprintable():
        return None
    while path.startswith("./"):
        path = path[2:]
    path = path.rstrip("/")
    if path == ".":
        return path if allow_root else None
    if not path or ".." in path.split("/"):
        return None
    return path


def _describes_entry(i: int, entry: object) -> DescribesMapping | str:
    """A valid mapping, or the message saying why entry ``i`` is rejected."""
    where = f"[[describes]] entry {i}"
    if not isinstance(entry, dict):
        return f"{where} is not a table"
    path = _clean_rel(entry.get("path"), allow_root=False)
    if path is None:
        return f"{where} needs path = a doc or folder relative to this repository's root"
    where = f"{where} (path = {path!r})"
    repo = entry.get("repo")
    if not isinstance(repo, str) or not repo.strip() or any(c.isspace() for c in repo.strip()):
        return f"{where} needs repo = a fleet repository id such as \"grp/billing\""
    if "scope" not in entry:
        return f"{where} has no scope: {SCOPE_REMEDIATION}"
    raw = entry["scope"]
    raws = [raw] if isinstance(raw, str) else raw if isinstance(raw, list) else []
    scope = [_clean_rel(s, allow_root=True) for s in raws]
    if not scope or any(s is None for s in scope):
        return f"{where} has an empty or invalid scope {raw!r}: {SCOPE_REMEDIATION}"
    return {"path": path, "repo": repo.strip().strip("/"),
            "scope": sorted({s for s in scope if s is not None})}


def load_describes(repo_root: Path) -> DescribesConfig:
    """Return the ``[[describes]]`` cross-repo doc mappings (issue #664).

    A docs repository declares which service surface each of its docs describes,
    so ``/fleet`` can count the service commits since the doc last changed::

        [[describes]]
        path = "services/billing.md"   # a doc or folder in this repository
        repo = "grp/billing"           # a repository id in the fleet
        scope = ["api/"]               # required: the documented surface

    Unlike the other loaders this one does not skip a bad entry silently: a
    mapping nobody can measure must be visible, so each rejected entry yields a
    message (a missing ``scope`` names the remediation). Only declared mappings
    are ever used; nothing is inferred from links in the docs.
    """
    raw = load_config(repo_root).get("describes")
    if raw is None:
        return DescribesConfig([], [])
    if not isinstance(raw, list):
        return DescribesConfig([], ["describes must be an array of tables: [[describes]]"])
    mappings: list[DescribesMapping] = []
    errors: list[str] = []
    for i, entry in enumerate(raw, 1):
        parsed = _describes_entry(i, entry)
        if isinstance(parsed, str):
            errors.append(parsed)
        else:
            mappings.append(parsed)
    return DescribesConfig(mappings, errors)


class DeclaredMapping(TypedDict):
    """``run-context.json`` ``doc_staleness.declared_cross_repo`` entry."""

    path: str | None
    repo: str | None
    scope: list[str]
    status: str  # "declared_not_measured" | "invalid"
    reason: str | None  # "invalid_mapping" for a rejected entry


def declared_block(repo_root: Path) -> list[DeclaredMapping]:
    """Standalone ``/assess``: the ``[[describes]]`` mappings, listed and not measured.

    Only ``/fleet`` holds the service checkouts (``lib/fleet_doc_freshness.py``);
    a single-repo run says the mappings exist and guesses nothing.
    """
    cfg = load_describes(repo_root)
    out: list[DeclaredMapping] = [
        {"path": m["path"], "repo": m["repo"], "scope": m["scope"],
         "status": "declared_not_measured", "reason": None}
        for m in cfg.mappings
    ]
    out += [{"path": None, "repo": None, "scope": [], "status": "invalid",
             "reason": "invalid_mapping"} for _ in cfg.errors]
    return out


def load_excludes(repo_root: Path) -> tuple[set[str], list[str]]:
    """Return `(extra_exclude_dirs, extra_exclude_patterns)` from the config.

    The same two lists feed every `/assess` scan. Returns `(set(), [])`
    when the config is missing or doesn't define the keys - callers should
    union with their built-in defaults rather than replace.
    """
    cfg = load_config(repo_root)
    dirs = set(_string_list(cfg, "exclude_dirs"))
    pats = _string_list(cfg, "exclude_patterns")
    return dirs, pats


def _dir_list(config: TomlTable, key: str) -> list[str]:
    """``_string_list`` normalised to repo-relative posix directory paths:
    ``./journal/`` -> ``journal``; an entry that names no directory is dropped."""
    out = []
    for raw in _string_list(config, key):
        path = raw.strip().replace("\\", "/").strip("/")
        while path.startswith("./"):
            path = path[2:]
        if path and path != ".":
            out.append(path)
    return out


class WorkingNotesConfig(NamedTuple):
    """The working-notes overrides, as repo-relative directory paths."""

    dirs: list[str]
    ignore: list[str]


def load_working_notes_config(repo_root: Path) -> WorkingNotesConfig:
    """Return the working-notes overrides for ``build_doc_graph``.

    ``working_notes_dirs`` forces each listed directory to be reported as a
    working-notes tree whatever its size or fingerprint (issue #367);
    ``working_notes_ignore`` keeps each listed directory, and everything under
    it, out of every working-notes tree even when the fingerprint matches.
    Both are lists of repo-relative directory paths. Honours the same "degrade
    silently" contract as the other loaders.
    """
    cfg = load_config(repo_root)
    return WorkingNotesConfig(
        dirs=_dir_list(cfg, "working_notes_dirs"),
        ignore=_dir_list(cfg, "working_notes_ignore"),
    )


def split_cli_excludes(cli_excludes: list[str]) -> tuple[set[str], list[str]]:
    """Split repeatable `--exclude PATTERN` values into `(dirs, glob_patterns)`.

    A pattern containing a glob metacharacter (`*?[`) is a basename glob; any
    other value is treated as a directory name. This is the same shape the
    treemap CLI accepts, so `--exclude regulatory-raw` works as a dir match
    without the user picking the right list.
    """
    dirs: set[str] = set()
    patterns: list[str] = []
    for pat in cli_excludes:
        if any(c in pat for c in "*?["):
            patterns.append(pat)
        else:
            dirs.add(pat)
    return dirs, patterns


def resolve_excludes(
    repo_root: Path, cli_excludes: list[str] | None = None,
) -> tuple[set[str], list[str]]:
    """Config excludes (`.assess/config.toml`) + CLI `--exclude`, combined.

    The single resolution path shared by the treemap CLI and the doc-graph SVG
    so every artifact computes over the *identical* doc/code set. Both extend
    the built-in defaults; callers union the result with their own defaults
    rather than replacing them (issue #177).
    """
    cfg_dirs, cfg_patterns = load_excludes(repo_root)
    cli_dirs, cli_patterns = split_cli_excludes(cli_excludes or [])
    return cfg_dirs | cli_dirs, cfg_patterns + cli_patterns
