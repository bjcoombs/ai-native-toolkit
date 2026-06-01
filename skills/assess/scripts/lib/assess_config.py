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
```

The same two lists feed every scan. There is no per-scan override knob -
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
"""
from __future__ import annotations

import fnmatch
import sys
import tomllib
from pathlib import Path


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


def load_config(repo_root: Path) -> dict:
    """Read `<repo_root>/.assess/config.toml` and return the parsed dict.

    Returns `{}` when the file does not exist, isn't readable, or fails to
    parse. Malformed files print a one-line warning to stderr; missing files
    are silent (the common case).
    """
    config_path = (repo_root / ".assess" / CONFIG_FILE).resolve()
    if not config_path.is_file():
        return {}
    try:
        return tomllib.loads(config_path.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, OSError) as e:
        print(
            f"warning: could not read {config_path} ({e}); "
            "continuing with defaults",
            file=sys.stderr,
        )
        return {}


def _string_list(config: dict, key: str) -> list[str]:
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


def load_structure_config(repo_root: Path) -> dict:
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


# The findings that represent a concern (everything except the one positive
# finding, ``refactor_boundary``). The canonical order + the positive finding
# live in ``keyhole_signals.FINDING_ORDER``; the gate only needs the concern set
# and must not import back up into a sibling lib module, so the list is repeated
# here deliberately. ``test_gate_concerns_match_keyhole_signals`` pins the two in
# sync so a new finding can't silently escape the default warn set.
GATE_CONCERN_FINDINGS = [
    "hidden_coupling",
    "lying_map",
    "unexplained_complexity",
    "untrusted_hotspot",
    "self_referential_tests",
    "orphaned_understanding",
    "candidate_dead_weight",
]


def _positive_number(section: dict, key: str) -> float | None:
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


def load_gate_config(repo_root: Path) -> dict:
    """Return the ``[gate]`` settings from ``.assess/config.toml``.

    The gate is the CI regression check (``assess_gate.py``). Its defaults are
    deliberately **warn-only**: a repo that adopts the emitted workflow without
    writing any config never has a pipeline blocked by surprise. Failing is
    strictly opt-in via ``fail_on``.

    Returned keys:

    - ``enabled`` (bool, default ``True``): master switch. ``false`` makes the
      gate always pass while still reporting, so a repo can mute it without
      deleting the workflow.
    - ``fail_on`` (list[str], default ``[]``): finding names whose presence (a
      non-empty ``paths`` list) fails the gate. Empty means warn-only.
    - ``warn_on`` (list[str], default = every concern finding): finding names
      reported but non-blocking. An explicit empty list silences warnings.
    - ``ccn_p95_max`` (float | None): fail when the p95 file CCN exceeds this.
    - ``containment_min`` (float | None): fail when the safe-zone containment
      ratio drops below this (0-1).

    Honours the same "degrade silently" contract as the other loaders: a missing
    file, missing section, or malformed value falls back to the default rather
    than blocking the assessment.
    """
    cfg = load_config(repo_root)
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
    return {
        "enabled": enabled if isinstance(enabled, bool) else True,
        "fail_on": _string_list(gate, "fail_on"),
        "warn_on": warn_on,
        "ccn_p95_max": _positive_number(gate, "ccn_p95_max"),
        "containment_min": _positive_number(gate, "containment_min"),
    }


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
