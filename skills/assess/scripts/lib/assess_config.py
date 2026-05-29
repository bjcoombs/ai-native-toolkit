"""Per-repo `.assess/config.toml` reader.

A single, optional config file lets a repo persist `/assess` preferences that
would otherwise have to be re-supplied as CLI flags on every run. Today this
covers the treemap's exclude lists (issue #50: repos that intentionally track
vetted-context / reference data need a durable escape hatch from the
dominance warning, not a one-off `--exclude` invocation each time).

Schema (only the keys this repo's scripts read are documented; extra keys are
preserved and ignored so the file stays forward-compatible):

```toml
[treemap]
exclude_dirs = ["regulatory-raw", "vetted-context"]
exclude_patterns = ["*.csv", "*.parquet"]
```

Design choices:

- **Optional and additive.** A missing config is the default state, not an
  error. The defaults baked into each script always apply; the config only
  *extends* them. CLI flags layer on top of both.
- **Degrade silently on malformed input.** A broken TOML file should never
  block an assessment - the loader returns an empty config and prints a
  warning to stderr. The script keeps running on defaults.
- **No new dependencies.** `tomllib` is in the stdlib since Python 3.11
  (which the existing scripts already require).
"""
from __future__ import annotations

import sys
import tomllib
from pathlib import Path


CONFIG_FILE = "config.toml"


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


def treemap_excludes(config: dict) -> tuple[list[str], list[str]]:
    """Return `(extra_exclude_dirs, extra_exclude_patterns)` from `[treemap]`.

    Always returns two lists (possibly empty). Non-string entries are dropped
    silently so a malformed entry doesn't poison the rest of the config -
    e.g. `exclude_dirs = ["regulatory-raw", 42]` keeps `regulatory-raw` and
    drops the integer.
    """
    section = config.get("treemap") or {}
    dirs = [str(x) for x in section.get("exclude_dirs", []) if isinstance(x, str)]
    pats = [str(x) for x in section.get("exclude_patterns", []) if isinstance(x, str)]
    return dirs, pats
