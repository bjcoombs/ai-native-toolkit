"""A starter maturity model: a commented copy of the default model or a profile (#1151).

``scripts/levels_new.py OUT [--profile NAME] [--version V]`` calls ``write_starter``. It
copies ``levels/default.toml``, or ``levels/profiles/<NAME>.toml``, to ``OUT`` with two
changes:

- ``FIELD_GUIDE`` goes first: one comment line or two for each field of the format, so a
  newcomer can edit the copy without opening ``lib/scoring/levels.py``;
- the ``version`` line becomes ``V``, by default ``<OUT stem>-1`` (``custom-<OUT stem>-1``
  when that would start with a reserved prefix, as for ``default.toml``). The shipped
  versions start ``default-`` or ``profile-``, and ``lib.profiles.resolve`` refuses those in
  a custom file, so a copy that kept them would not load with ``--levels``.

The copy must load with ``--levels`` before the command reports success: ``write_starter``
writes ``.<OUT stem>.levels-new.toml`` beside ``OUT`` (with the usual file mode), loads it through ``lib.profiles.single_repo_choice``
and moves it onto ``OUT`` only when it loads. On any error the temporary file is removed and
``OUT`` is left as it was. It never replaces an existing file unless ``force`` is set. A file
system error (a folder that does not exist, no permission) is a ``StarterError`` too. The guide that uses it is
``docs/build-your-own-practice.md`` at the repository root.

Tests: ``tests/test_levels_starter.py``.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

from lib.scoring.level_signals import signal_path_validator
from lib.profiles import RESERVED_PREFIXES, ProfileError, profile_path, single_repo_choice
from lib.scoring.signal_grades import DEFAULT_LEVELS

__all__ = ["FIELD_GUIDE", "StarterError", "default_version", "source_path", "starter_text", "write_starter"]

FIELD_GUIDE = """\
# Your own maturity model, written by scripts/levels_new.py.
# Run it with: /assess --levels {name}   (or /fleet --levels {name})
# The guide: docs/build-your-own-practice.md in the ai-native-toolkit repository.
#
# The fields:
# - version: a name for this edition of the model. Change it after every edit.
#   It may not start with "default-" or "profile-".
# - [[rows]]: one practice. Each row has these fields:
#   - id: a short name, unique in the file. A Step gate names it.
#   - title: the words the report shows for the practice.
#   - scope: "team" reads each repository; "org" reads the GitHub or GitLab
#     group settings (/fleet only).
#   - aggregation: for a team row, how /fleet joins its repositories. "min" is
#     the level every repository reaches. "share" with share = 0.75 is the
#     level three quarters of them reach.
# - [[rows.levels]]: the levels of a row, numbered 1, 2, 3 with no gap. A
#   repository reaches a level when every criterion of it and of each lower
#   level holds.
#   - truth: the tags of what the level keeps true, such as "behaviour" or
#     "docs". lib/scoring/levels_fields.py lists them all.
#   - criteria: a list of { signal, op, value } tests. All of them must hold.
#     - signal: a dotted signal path. docs/what-we-measure.md lists them.
#     - op: eq, ne, lt, lte, gt, gte, in or not_in.
#     - value: a number, true or false, a string, or a list for in and not_in.
#   - requires (optional): ids such as "layer-5-ci.1" that must be met too.
#   - polarity = "hazard" (optional): the level marks a harm, not a practice; blocks names a gate it holds.
# - [[steps]]: the autonomy Steps, numbered 1, 2, 3. Step 1 has no gates. Each
#   later Step keeps the gates below it and adds one. A gate is
#   { id, row, level }: it holds when that row reaches that level.
#
# The notes below come from {source}.

"""

_VERSION_LINE = re.compile(r'^version\s*=\s*"[^"]*"', re.MULTILINE)


class StarterError(ValueError):
    """The starter cannot be written: an unknown profile, a file in the way, or a bad version."""


def source_path(profile: str | None) -> Path:
    """The file a starter copies: the default model, or the profile ``profile``."""
    if profile is None:
        return DEFAULT_LEVELS
    try:
        return profile_path(profile)
    except ProfileError as exc:
        raise StarterError(str(exc)) from exc


def starter_text(source: Path, name: str, version: str) -> str:
    """``source``'s text after ``FIELD_GUIDE``, with its ``version`` line set to ``version``."""
    text = source.read_text(encoding="utf-8")
    if not _VERSION_LINE.search(text):
        raise StarterError(f"{source.name} has no version line to replace")
    body = _VERSION_LINE.sub(f'version = "{version}"', text, count=1)
    origin = source.relative_to(DEFAULT_LEVELS.parent.parent).as_posix()
    # replace, not str.format: the guide's own { signal, op, value } braces are literal
    return FIELD_GUIDE.replace("{name}", name).replace("{source}", origin) + body


def write_starter(out: Path, profile: str | None = None, version: str | None = None,
                  force: bool = False) -> Path:
    """Write the starter model to ``out`` and check that it loads with ``--levels``."""
    if out.exists() and not force:
        raise StarterError(f"{out} already exists; pass --force to replace it")
    if version is not None and (not version.strip() or '"' in version or "\\" in version):
        raise StarterError(f"version {version!r} must be a non-empty string without quotes or backslashes")
    text = starter_text(source_path(profile), out.name, version or default_version(out))
    # write_text, not mkstemp: the copy keeps the usual umask mode, never owner-only 0600
    temporary = out.with_name(f".{out.stem}.levels-new.toml")
    try:
        temporary.write_text(text, encoding="utf-8")
        single_repo_choice(None, temporary, signal_path_validator())
        os.replace(temporary, out)
    except ProfileError as exc:  # name the user's file, never the temporary one
        raise StarterError(f"the copy does not load: {str(exc).replace(temporary.name, out.name)}") from exc
    except OSError as exc:
        raise StarterError(f"cannot write {out}: {exc.strerror or exc}") from exc  # never the temporary name
    finally:
        temporary.unlink(missing_ok=True)
    return out


def default_version(out: Path) -> str:
    """``<stem>-1``, or ``custom-<stem>-1`` when the stem would give a reserved version."""
    version = f"{out.stem}-1"
    return f"custom-{version}" if version.startswith(RESERVED_PREFIXES) else version
