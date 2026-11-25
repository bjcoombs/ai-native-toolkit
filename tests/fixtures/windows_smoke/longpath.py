"""The long-path smoke run in .github/workflows/windows.yml (#1405).

A GitLab layout from ghsync nests the full project path and then
``<repo>/<repo>-main``, so a deep source tree passes Windows' 260-character
path limit. ghsync clones with ``core.longpaths=true`` (#1269); this checks
that the scanners then read a file at such a path.

Usage:
  python longpath.py build CHECKOUT
  python longpath.py check CHECKOUT SIGNALS_JSON RUN_CONTEXT_JSON

``build`` makes a git repository at CHECKOUT, with ``core.longpaths=true`` in
its own config as a ghsync clone has it, holding one Python module whose
absolute path is longer than 260 characters (``LONG_MODULE`` under a chain of
packages). The module's file-aggregate cyclomatic complexity is
``FILE_CCN``, which no other file in the repository reaches; it holds a
function nothing calls and one bare TODO marker. On Windows the files are written
through the ``\\\\?\\`` prefix, so the fixture exists whatever the machine's
LongPathsEnabled setting; whether each scanner then reads it is the test.

``check`` reads ``assess_signals.py``'s output and ``assess_core.py``'s
run-context.json and reports one line per scanner:

- complexity (lizard): ``stats_summary.ccn.max`` is ``FILE_CCN``;
- structure (grimp): ``structure.footprints`` lists the long module;
- liveness (vulture): ``dead_code.candidates`` lists the long module's path;
- markers (rg): ``promissory_markers.todo_bare`` counts the long module's TODO.

Each scanner reads as ``read``, ``not measured`` (its block is unavailable
and says why, which is an honest reading), ``known gap`` (listed in
``KNOWN_GAPS`` with its issue) or ``MISSED``: the block reports a reading
that leaves the long file out, the silent under-read #1405 guards against.
Only ``MISSED`` fails the step.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

LIMIT = 260
# The absolute path of the long module is built past this length, so it stays
# over LIMIT on any runner temp directory.
TARGET = 300
SEGMENT = "deeply_nested_package_for_windows_long_paths"
LONG_MODULE = "long_module.py"
LONG_FUNCTION = "classify"
LONG_CCN = 9
# lizard's file-aggregate ccn: classify plus tidy (ccn 1).
FILE_CCN = LONG_CCN + 1
# Scanner name -> issue that tracks a fix it needs. A scanner listed here that
# misses the long file reads as "known gap" rather than failing the step.
KNOWN_GAPS: dict[str, str] = {}

APP = 'from pkg import short\n\n\ndef main():\n    return short.helper()\n'
SHORT = "def helper():\n    return 1\n"
LONG_SOURCE = (
    "def classify(x):\n"
    + "".join(f"    {'if' if i == 1 else 'elif'} x == {i}:\n        return {i}\n" for i in range(1, LONG_CCN))
    + "    return 0\n"
    + "\n\n"
    + "def tidy():\n"
    + "    # TODO tidy this module\n"
    + "    return None\n"
)


def _os_path(path: Path) -> str:
    """``path`` in a form the OS accepts past 260 characters."""
    text = os.path.abspath(path)
    if os.name == "nt" and not text.startswith("\\\\?\\"):
        return "\\\\?\\" + text
    return text


def _write(path: Path, text: str) -> None:
    os.makedirs(_os_path(path.parent), exist_ok=True)
    with open(_os_path(path), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def _git(checkout: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(checkout), "-c", "core.longpaths=true", *args],
        check=True, capture_output=True, encoding="utf-8", errors="replace",
    ).stdout


def long_rel(checkout: Path) -> str:
    """The repo-relative path of the long module, its depth fixed by CHECKOUT's length."""
    parts = ["pkg"]
    while len(os.path.abspath(checkout / Path(*parts) / LONG_MODULE)) <= TARGET:
        parts.append(f"{SEGMENT}_{len(parts)}")
    return "/".join([*parts, LONG_MODULE])


def build(checkout: Path) -> str:
    rel = long_rel(checkout)
    os.makedirs(_os_path(checkout), exist_ok=True)
    _git(checkout, "init", "-q", "-b", "main")
    _git(checkout, "config", "core.longpaths", "true")
    _write(checkout / "README.md", "# deep\n\nSee [the app](app.py).\n")
    _write(checkout / "app.py", APP)
    _write(checkout / "pkg" / "short.py", SHORT)
    dirs = rel.split("/")[:-1]
    for depth in range(1, len(dirs) + 1):
        _write(checkout.joinpath(*dirs[:depth]) / "__init__.py", "")
    _write(checkout / rel, LONG_SOURCE)
    _git(checkout, "add", "-A")
    _git(checkout, "-c", "user.name=ci", "-c", "user.email=ci@example.invalid",
         "commit", "-q", "-m", "long-path fixture")
    tracked = _git(checkout, "ls-files").splitlines()
    assert rel in tracked, f"{rel} is not tracked: {tracked}"
    return rel


def _block(doc: dict[str, Any], name: str) -> dict[str, Any] | None:
    block = doc.get(name)
    if not isinstance(block, dict):
        return None
    return block


def _unavailable(block: dict[str, Any] | None, name: str) -> str | None:
    if block is None:
        return f"no {name} block"
    if block.get("available") is False:
        return f"{name} unavailable: {block.get('reason') or 'no reason given'}"
    return None


def judge(rel: str, signals: dict[str, Any], context: dict[str, Any]) -> dict[str, tuple[str, str]]:
    """Scanner -> (outcome, detail) for the long module at ``rel``."""
    scans = signals.get("scans", {})
    module = rel.removesuffix(".py").replace("/", ".")
    out: dict[str, tuple[str, str]] = {}

    stats = _block(scans, "stats_summary")
    why = _unavailable(stats, "stats_summary")
    if why is None and stats is not None:
        ccn_max = (stats.get("ccn") or {}).get("max")
        hit = ccn_max == FILE_CCN
        out["lizard"] = ("read" if hit else "MISSED", f"stats_summary.ccn.max={ccn_max}, long module ccn {FILE_CCN}")
    else:
        out["lizard"] = ("not measured", str(why))

    structure = _block(scans, "structure")
    why = _unavailable(structure, "structure")
    if why is None and structure is not None:
        names = {f.get("module") for f in structure.get("footprints") or [] if isinstance(f, dict)}
        out["grimp"] = ("read" if module in names else "MISSED", f"{len(names)} modules in structure.footprints")
    else:
        out["grimp"] = ("not measured", str(why))

    dead = _block(context, "dead_code")
    why = _unavailable(dead, "dead_code")
    if why is None and dead is not None:
        hits = [c for c in dead.get("candidates") or []
                if isinstance(c, dict) and c.get("path") == rel and c.get("symbol") == LONG_FUNCTION]
        tools = ", ".join(f"{t.get('tool')}={t.get('status')}" for t in dead.get("tools") or [] if isinstance(t, dict))
        out["vulture"] = ("read" if hits else "MISSED", f"dead_code tools: {tools or 'none'}")
    else:
        out["vulture"] = ("not measured", str(why))

    markers = _block(scans, "promissory_markers")
    why = _unavailable(markers, "promissory_markers")
    if why is None and markers is not None:
        bare = markers.get("todo_bare")
        out["rg"] = ("read" if bare == 1 else "MISSED", f"promissory_markers.todo_bare={bare}, long module has 1")
    else:
        out["rg"] = ("not measured", str(why))

    for scanner, issue in KNOWN_GAPS.items():
        if out.get(scanner, ("", ""))[0] == "MISSED":
            out[scanner] = ("known gap", f"{out[scanner][1]}; tracked in {issue}")
    return out


def _long_paths_enabled() -> str:
    if os.name != "nt":
        return "not Windows"
    import winreg  # noqa: PLC0415 - Windows only

    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\FileSystem") as key:
            return str(winreg.QueryValueEx(key, "LongPathsEnabled")[0])
    except OSError:
        return "unset"


def check(checkout: Path, signals_path: Path, context_path: Path) -> int:
    rel = long_rel(checkout)
    absolute = os.path.abspath(checkout / rel)
    print(f"long module: {len(absolute)} characters (limit {LIMIT}); LongPathsEnabled: {_long_paths_enabled()}")
    assert len(absolute) > LIMIT, f"{absolute} is not past {LIMIT} characters"
    signals = json.loads(signals_path.read_text(encoding="utf-8"))
    context = json.loads(context_path.read_text(encoding="utf-8"))
    results = judge(rel, signals, context)
    for scanner, (outcome, detail) in results.items():
        print(f"{scanner}: {outcome} ({detail})")
    missed = sorted(s for s, (outcome, _) in results.items() if outcome == "MISSED")
    if missed:
        print(f"scanners that left out the long file: {', '.join(missed)}")
    return 1 if missed else 0


def main(argv: list[str]) -> int:
    if len(argv) == 2 and argv[0] == "build":
        print(build(Path(argv[1])))
        return 0
    if len(argv) == 4 and argv[0] == "check":
        return check(Path(argv[1]), Path(argv[2]), Path(argv[3]))
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
