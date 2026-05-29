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
