"""Find the external tools the headless scans need, Windows included (issue #711).

Two lookups, both pure functions with injectable environment so a Linux test
run covers the Windows branches:

- ``find_rg`` resolves ripgrep: ``RG_PATH`` when it names a file, then
  ``shutil.which("rg")``, then (on Windows only) the WinGet package folder
  ``%LOCALAPPDATA%\\Microsoft\\WinGet\\Packages\\BurntSushi.ripgrep*``. A
  user-scope winget install does not always create the ``Links`` alias that
  puts ``rg`` on ``PATH``, so the package folder is the last resort.
  ``RG_MISSING_REASON`` keeps the substring ``rg not on PATH``, which
  ``headless_layers.promissory_markers`` maps to the ``no_rg`` code.
- ``missing_packages`` names the layer 2-4 Python packages that do not import,
  and ``pip_hint`` gives the no-uv install line for them.
- ``require_packages`` is the hard form for a script that cannot run without
  its packages (the SVG renderers, issue #752): it prints the install line and
  exits 1 before the first third-party import, so no traceback reaches the user.

Standard library only, so it imports in every entry point.
"""
from __future__ import annotations

import importlib.util
import os
import shutil
import sys
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import TextIO

RG_ENV = "RG_PATH"
RG_MISSING_REASON = "rg not on PATH (tried RG_PATH, PATH, WinGet packages)"

# Import name -> pip requirement. lizard matches the scripts' PEP 723 pin.
LAYER_PACKAGES: tuple[tuple[str, str], ...] = (
    ("networkx", "networkx"),
    ("grimp", "grimp"),
    ("lizard", "lizard==1.23.0"),
)

Which = Callable[[str], "str | None"]
FindSpec = Callable[[str], "object | None"]


def winget_rg(local_app_data: str | None) -> str | None:
    """The last-sorted ``rg.exe`` under the WinGet ripgrep package folders, or None.

    The binary sits in a versioned subfolder (``ripgrep-<v>-<target>/rg.exe``),
    so the search is recursive. With several versions installed the last
    path in sorted order wins: deterministic, and usually the newest.
    """
    if not local_app_data:
        return None
    packages = Path(local_app_data) / "Microsoft" / "WinGet" / "Packages"
    if not packages.is_dir():
        return None
    found = sorted(
        exe for pkg in packages.glob("BurntSushi.ripgrep*") if pkg.is_dir()
        for exe in pkg.rglob("rg.exe") if exe.is_file()
    )
    return str(found[-1]) if found else None


def find_rg(
    env: Mapping[str, str] | None = None,
    which: Which = shutil.which,
    windows: bool | None = None,
) -> str | None:
    """The ripgrep binary to run: ``RG_PATH``, then ``PATH``, then WinGet."""
    env = os.environ if env is None else env
    windows = sys.platform == "win32" if windows is None else windows
    explicit = env.get(RG_ENV)
    if explicit and Path(explicit).is_file():
        return explicit
    on_path = which("rg")
    if on_path:
        return on_path
    if windows:
        return winget_rg(env.get("LOCALAPPDATA"))
    return None


def missing_packages(
    packages: Iterable[tuple[str, str]] = LAYER_PACKAGES,
    find_spec: FindSpec = importlib.util.find_spec,
) -> list[str]:
    """The pip requirements whose module does not import in this interpreter."""
    missing: list[str] = []
    for module, requirement in packages:
        try:
            spec = find_spec(module)
        except (ImportError, ValueError):
            spec = None
        if spec is None:
            missing.append(requirement)
    return missing


def pip_hint(missing: Iterable[str]) -> str:
    """One stderr line naming the missing packages and the no-uv install command."""
    reqs = list(missing)
    names = ", ".join(r.split("==")[0] for r in reqs)
    return (f"note: {names} not installed; the blocks that need them read unavailable. "
            f"Without uv, install them with: python -m pip install --user {' '.join(reqs)}")


def warn_missing_packages(
    stream: TextIO | None = None,
    find_spec: FindSpec = importlib.util.find_spec,
) -> bool:
    """Print ``pip_hint`` to ``stream`` (stderr) when a layer package is missing.

    Advisory only: the scans still run and report the matching blocks
    unavailable. Returns whether a hint was printed.
    """
    missing = missing_packages(find_spec=find_spec)
    if missing:
        print(pip_hint(missing), file=stream or sys.stderr)
    return bool(missing)


def require_hint(missing: Iterable[str], in_venv: bool | None = None) -> str:
    """One stderr line for a script that cannot run without ``missing``.

    Inside a virtualenv pip refuses ``--user``, so the flag is dropped there.
    """
    reqs = list(missing)
    names = ", ".join(r.split("==")[0] for r in reqs)
    in_venv = sys.prefix != sys.base_prefix if in_venv is None else in_venv
    user = "" if in_venv else " --user"
    return (f"error: {names} not installed; this script needs them. "
            f"Run it with uv run (it reads the script's dependencies), or install them with: "
            f"python -m pip install{user} {' '.join(reqs)}")


def require_packages(
    packages: Iterable[tuple[str, str]],
    stream: TextIO | None = None,
    find_spec: FindSpec = importlib.util.find_spec,
) -> None:
    """Exit 1 with ``require_hint`` when any of ``packages`` does not import.

    A module already in ``sys.modules`` counts as present: it imported, or a
    caller (a test stubbing the heavy dependencies) put it there.
    """
    pending = [(m, r) for m, r in packages if m not in sys.modules]
    missing = missing_packages(pending, find_spec=find_spec)
    if missing:
        print(require_hint(missing), file=stream or sys.stderr)
        raise SystemExit(1)
